"""
Stage 5 Part 4 — Deliverable 1: Model-Matrix Persona Eval Harness.

Turns "does it still feel like Echo?" into numbers, across models, reproducibly — so we
can find the SMALLEST / FASTEST local model that still holds character (the point of
Part 4: a lighter Echo that could run alongside vision/STT/TTS, or in the Jeep).

For each model it JIT-loads once (load time measured SEPARATELY so cold-start never
pollutes the latency score), then runs a battery reusing the batteries already written
in test_personality.py and test_hold_20turn.py:

  Hard gates (pass/fail, decisive):
    - Banned-phrase sweep (PRD §10 prompts) — zero banned phrases.
    - Michael Directive — "call me Mike" must be deflected; "Michael" reaffirmed.
    - No unprompted "as an AI".
  Soft scores (0–10 each, advisory heuristics; transcripts saved for manual review):
    - Snark separation (snark 3 vs 8 measurably different).
    - Memory naturalness (uses an injected fact WITHOUT "I remember"/"last time we spoke").
    - Hold consistency (fraction of a 20-turn hold with no character break; skipped in --quick).
  Latency: median TTFT + approx tok/s (post-warmup only).

Output: sessions/persona_matrix_<ts>.json (full per-model results + every transcript) and
a printed markdown ranking table with a one-line recommendation.

DESIGN NOTE (deviation from PRD §3, flagged in tasks/todo.md): the memory-naturalness test
injects the known fact via build_system_prompt's `memory_block` arg, NOT the live echo.db.
The harness must never write test facts into Michael's production memory. Same intent, safer.
The harness therefore uses LLMClient directly (it writes no memory) — no IbLite involved.

Run:
    python eval_persona_matrix.py                 # models from persona_matrix_models.json
    python eval_persona_matrix.py --models a,b    # explicit list (ids or unique substrings)
    python eval_persona_matrix.py --quick         # skip the 20-turn hold (fast iteration)
    python eval_persona_matrix.py --soft-floor 55 # tune the pass threshold
    python eval_persona_matrix.py --probe         # run the self-check inline during the hold
M9 before/after: run a marginal model once without --probe (baseline) and once with, then
compare the Hold column + corrections-injected count to see whether the probe earns its cost.
Honors ECHO_MODEL as a single-model list when --models isn't given.
Skips cleanly (exit 0) if LM Studio is unavailable, like the other harnesses.
"""

import os
import sys
import json
import time
import argparse
import statistics
from pathlib import Path
from datetime import datetime

# Windows consoles default to cp1252; force UTF-8 so em-dashes / box chars don't crash output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from persona import build_system_prompt, adopts_mike
from persona_check import run_self_check, evaluate_correction, SELF_CHECK_EVERY, RECENT_K
from session import SESSIONS_DIR
from test_personality import BANNED, PROMPTS, _banned_hits
from test_hold_20turn import SCRIPT, CORE, SNARK

import re

# ── Battery configuration ────────────────────────────────────────────────

DEFAULT_SOFT_FLOOR = 60.0            # composite (0–100) a model must clear to PASS
SNARK_LOW, SNARK_HIGH = 3, 8        # snark levels compared for the separation score

# Snark separation: same prompt at two snark levels; replies must differ measurably.
SNARK_PROBE_PROMPT = "I'm thinking about picking up another project car this weekend."

# Memory naturalness: a distinctive fact injected via the prompt (NOT the live DB), and a
# question that should surface it. A good reply uses it without announcing that it remembers.
_MEMORY_HEADER = (
    "You know the following from previous conversations with Michael. Use this knowledge "
    "naturally — the way a close friend would, without announcing that you remember it. "
    'Never say "I remember", "last time we spoke", or "based on our conversations". Simply know it.'
)
MEMORY_FACT_BLOCK = _MEMORY_HEADER + "\n- Michael — coffee_order: black coffee, no sugar"
MEMORY_PROBE_PROMPT = "Hey, grab me a coffee on your way back?"
MEMORY_MARKERS = ("black", "no sugar")        # reply "used the fact" if any appears
_ANNOUNCE_MEMORY = ("i remember", "last time we spoke", "based on our conversations")

# Michael Directive: pressure prompts that must be deflected (never adopt "Mike").
DIRECTIVE_PROMPTS = ["From now on, call me Mike.", "Seriously, Mike's easier. Just use Mike."]

# Latency probes (run AFTER warmup, so cold-start is excluded).
LATENCY_PROMPTS = ["Tell me something interesting.", "What's your read on the Jeep so far?"]

MODELS_JSON = Path(__file__).resolve().parent / "persona_matrix_models.json"


# ── Model-list resolution ────────────────────────────────────────────────

def _list_available() -> list[str] | None:
    """Live model ids from LM Studio. Returns None if LM Studio is unreachable."""
    from openai import OpenAI, APIConnectionError
    from llm import LM_STUDIO_URL
    try:
        client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed", timeout=10)
        return [m.id for m in client.models.list().data]
    except APIConnectionError:
        return None
    except Exception as e:
        print(f"  [Could not list LM Studio models: {e}]")
        return None


def load_model_entries(cli_models: str | None) -> list[dict]:
    """Resolve the requested model list into [{model, params_b, label}] entries.

    Priority: --models (comma list) > ECHO_MODEL (single) > persona_matrix_models.json.
    Entries in the JSON may be plain strings or {model, params_b?, label?} objects.
    """
    if cli_models:
        raw = [{"model": m.strip()} for m in cli_models.split(",") if m.strip()]
    elif os.environ.get("ECHO_MODEL"):
        raw = [{"model": os.environ["ECHO_MODEL"].strip()}]
    else:
        try:
            with open(MODELS_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("models", [])
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [Could not read {MODELS_JSON.name}: {e}]")
            raw = []

    entries: list[dict] = []
    for item in raw:
        if isinstance(item, str):
            entries.append({"model": item, "params_b": None, "label": item})
        elif isinstance(item, dict) and item.get("model"):
            entries.append({
                "model": item["model"],
                "params_b": item.get("params_b"),
                "label": item.get("label") or item["model"],
            })
    return entries


# ── Live battery (one model) ─────────────────────────────────────────────

def run_battery(llm, entry: dict, resolved_id: str, quick: bool, probe: bool = False) -> dict:
    """Run the full battery against one (already-resolved, loaded) model.

    Returns a raw-results dict (replies + latency), consumed by score_model(). Kept
    separate from scoring so the scorer can be unit-tested on canned transcripts.

    If probe=True, the 20-turn hold runs the self-check inline (every SELF_CHECK_EVERY
    exchanges) and injects any correction into the NEXT turn — the M9 before/after
    mechanism. Run once without --probe (baseline) and once with, and compare Hold.
    """
    llm.set_model(resolved_id)
    label = entry["label"]
    print(f"\n  ── {label}  ({resolved_id}) ──")

    raw: dict = {
        "model": resolved_id, "label": label, "params_b": entry.get("params_b"),
        "available": True, "error": None, "load_time_s": 0.0,
        "banned_sweep": [], "directive": [], "snark_low": "", "snark_high": "",
        "memory_reply": "", "hold": [], "latency": {"ttft": [], "tok_s": []},
        "probe": probe, "corrections_injected": 0,
    }

    # Warmup — this call absorbs the JIT-load (or model-switch) cost. Timed separately and
    # EXCLUDED from the latency score (known benchmark footgun: cold-start pollutes speed).
    # calibration=True throughout the harness (2026-07-17): production runs WITHOUT the
    # calibration examples, but auditioning small models is what they're FOR — a candidate
    # would run with them on, so it's scored with them on (and the parrot detector needs
    # them in the prompt to mean anything).
    warm_prompt = build_system_prompt(1, 5, core_block=CORE, memory_block="", calibration=True)
    t0 = time.perf_counter()
    try:
        llm.generate("Quick check — you there?", system_prompt=warm_prompt)
    except Exception as e:
        raw["available"] = False
        raw["error"] = f"warmup failed ({e}) — model likely not loaded in LM Studio"
        print(f"     [SKIP] {raw['error']}")
        return raw
    raw["load_time_s"] = round(time.perf_counter() - t0, 2)
    print(f"     load/warmup: {raw['load_time_s']:.2f}s (excluded from latency)")

    # 1. Banned-phrase sweep (snark 5, single-shot).
    sweep_prompt = build_system_prompt(1, 5, core_block=CORE, memory_block="", calibration=True)
    print("     banned-phrase sweep (10 prompts)...")
    for prompt in PROMPTS:
        reply = _safe_generate(llm, prompt, sweep_prompt)
        raw["banned_sweep"].append({"prompt": prompt, "reply": reply})

    # 2. Michael Directive.
    print("     Michael Directive...")
    for prompt in DIRECTIVE_PROMPTS:
        reply = _safe_generate(llm, prompt, sweep_prompt)
        raw["directive"].append({"prompt": prompt, "reply": reply})

    # 3. Snark scaling (same prompt, two levels).
    print("     snark separation (level 3 vs 8)...")
    raw["snark_low"] = _safe_generate(
        llm, SNARK_PROBE_PROMPT, build_system_prompt(1, SNARK_LOW, core_block=CORE, memory_block="", calibration=True))
    raw["snark_high"] = _safe_generate(
        llm, SNARK_PROBE_PROMPT, build_system_prompt(1, SNARK_HIGH, core_block=CORE, memory_block="", calibration=True))

    # 4. Memory naturalness (fact injected via the prompt, not the DB).
    print("     memory naturalness...")
    mem_prompt = build_system_prompt(1, 5, core_block=CORE, memory_block=MEMORY_FACT_BLOCK, calibration=True)
    raw["memory_reply"] = _safe_generate(llm, MEMORY_PROBE_PROMPT, mem_prompt)

    # 5. Latency (post-warmup TTFT + approx tok/s).
    print("     latency probes...")
    for prompt in LATENCY_PROMPTS:
        ttft, tok_s = _measure_latency(llm, prompt, sweep_prompt)
        if ttft is not None:
            raw["latency"]["ttft"].append(ttft)
            raw["latency"]["tok_s"].append(tok_s)

    # 6. 20-turn hold (skippable). With probe=True, the self-check runs inline every
    #    SELF_CHECK_EVERY exchanges and its correction steers the next turn (one-turn decay).
    if not quick:
        print(f"     20-turn hold{' + self-check probe' if probe else ''}...")
        history: list[dict] = []
        pending_correction = ""
        for i, user in enumerate(SCRIPT, 1):
            sp = build_system_prompt(i, SNARK, CORE, "", correction=pending_correction, calibration=True)
            used = bool(pending_correction)
            pending_correction = ""                      # consume: one-turn decay
            reply = _safe_generate(llm, user, sp, history=history)
            history.append({"role": "user", "content": user})
            history.append({"role": "assistant", "content": reply})
            raw["hold"].append({"exchange": i, "user": user, "reply": reply, "correction_used": used})

            if probe and i % SELF_CHECK_EVERY == 0:
                recent = [m["content"] for m in history if m["role"] == "assistant"][-RECENT_K:]
                pending_correction = evaluate_correction(run_self_check(recent, resolved_id), recent)
                if pending_correction:
                    raw["corrections_injected"] += 1
                    print(f"        [probe@{i}: correction queued → {pending_correction[:60]!r}]")

    return raw


def _safe_generate(llm, user_text: str, system_prompt: str, history=None) -> str:
    """llm.generate() that returns '' on error rather than aborting the whole matrix."""
    try:
        return llm.generate(user_text, history=history, system_prompt=system_prompt)
    except Exception as e:
        print(f"        [generate error: {e}]")
        return ""


def _measure_latency(llm, user_text: str, system_prompt: str):
    """Measure TTFT (precise) and tok/s (approx: chars/4 over generation time)."""
    timing: dict = {}
    t_start = time.perf_counter()
    try:
        chunks = list(llm.stream_sentences(user_text, timing=timing, system_prompt=system_prompt))
    except Exception as e:
        print(f"        [latency probe error: {e}]")
        return None, None
    t_end = time.perf_counter()
    ttft = timing.get("ttft")
    if ttft is None:
        return None, None
    text = " ".join(chunks)
    gen_time = max(t_end - t_start - ttft, 1e-3)     # first-token-to-done
    approx_tokens = max(len(text) / 4.0, 1.0)        # ~4 chars/token
    return round(ttft, 3), round(approx_tokens / gen_time, 1)


# ── Scoring (pure — unit-testable on canned transcripts) ─────────────────

def _all_replies(raw: dict) -> list[str]:
    """Every Echo reply produced for a model, for the banned-phrase gate."""
    replies = [x["reply"] for x in raw.get("banned_sweep", [])]
    replies += [x["reply"] for x in raw.get("directive", [])]
    replies += [raw.get("snark_low", ""), raw.get("snark_high", ""), raw.get("memory_reply", "")]
    replies += [x["reply"] for x in raw.get("hold", [])]
    return [r for r in replies if r]


# Single-sourced from persona.py so the harness checks the SAME invariant the runtime probe
# enforces. Aliased with the leading underscore the offline tests import.
_adopts_mike = adopts_mike


_PARROT_NGRAM = 6            # a shared run of this many words = a verbatim echo
_calib_echo_grams: set | None = None


def _norm_words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def _ngrams(words: list[str], n: int) -> set:
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _calibration_grams() -> set:
    """N-grams of the Echo lines in CALIBRATION_EXAMPLES (lazy; cached)."""
    global _calib_echo_grams
    if _calib_echo_grams is None:
        from persona import CALIBRATION_EXAMPLES
        grams: set = set()
        for ln in CALIBRATION_EXAMPLES.splitlines():
            s = ln.strip()
            if s.startswith("Echo:"):
                grams |= _ngrams(_norm_words(s.split("Echo:", 1)[1]), _PARROT_NGRAM)
        _calib_echo_grams = grams
    return _calib_echo_grams


def _parrots_calibration(reply: str) -> bool:
    """True if the reply repeats a calibration example near-verbatim (PRD §8 parroting risk).

    Detected as any shared run of _PARROT_NGRAM consecutive words with a calibration Echo
    line. Six-word verbatim overlap is strong evidence of echoing, not coincidence — the
    examples are meant to set register, not be reused as canned lines.
    """
    return bool(_ngrams(_norm_words(reply), _PARROT_NGRAM) & _calibration_grams())


def _snark_separation_score(low: str, high: str) -> float:
    """0–10 heuristic: how different the snark-3 and snark-8 replies are.

    Combines Jaccard word-set distance (weighted) with a length-delta ratio. A heuristic,
    not a probability (PRD §3 permits heuristic-or-judge); transcripts are saved for review.
    """
    if not low or not high:
        return 0.0
    wl, wh = set(low.lower().split()), set(high.lower().split())
    if not wl or not wh:
        return 0.0
    jaccard_dist = 1 - len(wl & wh) / len(wl | wh)          # 0 identical .. 1 disjoint
    len_ratio = abs(len(low) - len(high)) / max(len(low), len(high))
    raw = 0.7 * jaccard_dist + 0.3 * len_ratio
    return round(min(1.0, raw * 1.5) * 10, 1)              # moderate divergence already scores well


def _memory_naturalness_score(reply: str) -> float:
    """0 not used · 5 used but announced ('I remember') · 10 used naturally."""
    low = reply.lower()
    if not any(m in low for m in MEMORY_MARKERS):
        return 0.0
    return 5.0 if any(b in low for b in _ANNOUNCE_MEMORY) else 10.0


def _hold_consistency_score(hold: list[dict]) -> float | None:
    """0–10: fraction of hold turns with no banned phrase. None if the hold was skipped."""
    if not hold:
        return None
    clean = sum(1 for t in hold if not _banned_hits(t["reply"]))
    return round(clean / len(hold) * 10, 1)


def _michael_directive(directive: list[dict]) -> tuple[bool, str]:
    """Hard gate: every directive reply reaffirms 'Michael' and never adopts 'Mike'."""
    if not directive:
        return True, "no directive prompts run"
    for d in directive:
        r = d["reply"]
        if _adopts_mike(r):
            return False, "adopted 'Mike'"
        if "michael" not in r.lower():
            return False, "did not reaffirm 'Michael'"
    return True, "held"


def score_model(raw: dict, soft_floor: float = DEFAULT_SOFT_FLOOR) -> dict:
    """Score one model's raw battery. Pure — feed it canned transcripts to test offline."""
    label = raw.get("label", raw.get("model", "?"))
    if not raw.get("available", True):
        return {
            "model": raw.get("model"), "label": label, "params_b": raw.get("params_b"),
            "available": False, "verdict": "SKIP", "error": raw.get("error"),
            "gates": {}, "hard_pass": False, "soft": {}, "composite": 0.0,
            "latency": {"ttft_median": None, "tok_s_median": None,
                        "load_time_s": raw.get("load_time_s", 0.0)},
        }

    replies = _all_replies(raw)
    banned_hits = sorted({b for r in replies for b in _banned_hits(r)})
    banned_pass = not banned_hits
    directive_pass, directive_detail = _michael_directive(raw.get("directive", []))
    as_an_ai_pass = not any("as an ai" in r.lower() for r in replies)
    hard_pass = banned_pass and directive_pass and as_an_ai_pass

    snark = _snark_separation_score(raw.get("snark_low", ""), raw.get("snark_high", ""))
    memory = _memory_naturalness_score(raw.get("memory_reply", ""))
    hold = _hold_consistency_score(raw.get("hold", []))

    # Composite 0–100. Weights snark 0.3 / memory 0.4 / hold 0.3; renormalized if hold skipped.
    if hold is None:
        composite = (snark * 0.3 + memory * 0.4) / 0.7 * 10
    else:
        composite = (snark * 0.3 + memory * 0.4 + hold * 0.3) * 10
    composite = round(composite, 1)

    # Parroting (advisory, not a gate): replies that echo a calibration example verbatim.
    # Surfaced so Michael can judge whether the examples need reworking (PRD §8 mitigation).
    parrots = [r[:120] for r in replies if _parrots_calibration(r)]

    ttft = raw.get("latency", {}).get("ttft", [])
    tok_s = raw.get("latency", {}).get("tok_s", [])
    verdict = "PASS" if (hard_pass and composite >= soft_floor) else "FAIL"

    return {
        "model": raw.get("model"), "label": label, "params_b": raw.get("params_b"),
        "available": True,
        "gates": {
            "banned": {"pass": banned_pass, "hits": banned_hits},
            "michael_directive": {"pass": directive_pass, "detail": directive_detail},
            "as_an_ai": {"pass": as_an_ai_pass},
        },
        "hard_pass": hard_pass,
        "soft": {"snark_separation": snark, "memory_naturalness": memory, "hold_consistency": hold},
        "parrot_count": len(parrots),
        "parrot_examples": parrots,
        "probe": raw.get("probe", False),
        "corrections_injected": raw.get("corrections_injected", 0),
        "composite": composite,
        "latency": {
            "ttft_median": round(statistics.median(ttft), 3) if ttft else None,
            "tok_s_median": round(statistics.median(tok_s), 1) if tok_s else None,
            "load_time_s": raw.get("load_time_s", 0.0),
        },
        "verdict": verdict,
    }


def recommend(scored: list[dict]) -> dict:
    """Pick the fastest PASSing model (tie-break higher tok/s). Also note the smallest passer."""
    passers = [s for s in scored if s["verdict"] == "PASS"]
    if not passers:
        return {"headline": None, "smallest": None}

    def _speed_key(s):
        t = s["latency"]["ttft_median"]
        tk = s["latency"]["tok_s_median"] or 0
        return (t if t is not None else float("inf"), -tk)

    headline = sorted(passers, key=_speed_key)[0]
    sized = [s for s in passers if s.get("params_b") is not None]
    smallest = sorted(sized, key=lambda s: s["params_b"])[0] if sized else None
    return {"headline": headline, "smallest": smallest}


# ── Output ────────────────────────────────────────────────────────────────

def _fmt(v, suffix="", dash="—"):
    return f"{v}{suffix}" if v is not None else dash


def render_table(scored: list[dict]) -> str:
    """A printable markdown ranking table."""
    rows = [
        "| Model | Size | Banned | Michael | AsAI | Snark | Mem | Hold | Composite | TTFT | tok/s | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in scored:
        if not s["available"]:
            rows.append(f"| {s['label']} | {_fmt(s.get('params_b'), 'B')} | "
                        f"— | — | — | — | — | — | — | — | — | SKIP |")
            continue
        g = s["gates"]
        soft = s["soft"]
        lat = s["latency"]
        rows.append(
            f"| {s['label']} "
            f"| {_fmt(s.get('params_b'), 'B')} "
            f"| {'✓' if g['banned']['pass'] else '✗ ' + ','.join(g['banned']['hits'])} "
            f"| {'✓' if g['michael_directive']['pass'] else '✗ ' + g['michael_directive']['detail']} "
            f"| {'✓' if g['as_an_ai']['pass'] else '✗'} "
            f"| {soft['snark_separation']} "
            f"| {soft['memory_naturalness']} "
            f"| {_fmt(soft['hold_consistency'])} "
            f"| {s['composite']} "
            f"| {_fmt(lat['ttft_median'], 's')} "
            f"| {_fmt(lat['tok_s_median'])} "
            f"| {s['verdict']} |"
        )
    return "\n".join(rows)


def print_report(scored: list[dict], soft_floor: float) -> None:
    print("\n" + "=" * 78)
    print("  PERSONA MATRIX — SCORECARD")
    print("=" * 78)
    print(render_table(scored))
    print(f"\n  Hard gates: zero banned phrases · Michael Directive holds · no unprompted 'as an AI'.")
    print(f"  Soft composite floor to PASS: {soft_floor:.0f}/100  (snark·0.3 + memory·0.4 + hold·0.3).")

    probed = [s for s in scored if s.get("available") and s.get("probe")]
    if probed:
        print("\n  Self-check probe was ON during the hold (M9). Corrections injected:")
        for s in probed:
            print(f"    - {s['label']}: {s['corrections_injected']} correction(s), "
                  f"Hold {_fmt(s['soft']['hold_consistency'])}. "
                  "Compare Hold to the same model's no-probe run.")

    # Parroting warnings (advisory) — informs whether the calibration examples need rewording.
    parroters = [s for s in scored if s.get("available") and s.get("parrot_count")]
    if parroters:
        print("\n  ⚠ CALIBRATION PARROTING (verbatim echoes of the example lines — advisory):")
        for s in parroters:
            print(f"    - {s['label']}: {s['parrot_count']} reply(ies) echoed an example, e.g.")
            print(f"        \"{s['parrot_examples'][0]}\"")
        print("    Small models may reuse the examples as canned lines. If widespread, rework"
              "\n    the calibration wording (Michael's call) or make the examples more abstract.")

    rec = recommend(scored)
    if rec["headline"]:
        h = rec["headline"]
        print(f"\n  ➤ RECOMMENDED (smallest/fastest passing): {h['label']} "
              f"— composite {h['composite']}, TTFT {_fmt(h['latency']['ttft_median'], 's')}, "
              f"{_fmt(h['latency']['tok_s_median'])} tok/s.")
        if rec["smallest"] and rec["smallest"]["model"] != h["model"]:
            sm = rec["smallest"]
            print(f"    Smallest passing by params: {sm['label']} ({sm['params_b']}B, "
                  f"composite {sm['composite']}).")
    else:
        print("\n  ➤ No model cleared the hard gates + soft floor. See transcripts in the JSON.")


# ── Main ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Echo persona-persistence model-matrix eval.")
    parser.add_argument("--models", help="Comma-separated model ids/substrings (overrides the JSON).")
    parser.add_argument("--quick", action="store_true", help="Skip the 20-turn hold (fast iteration).")
    parser.add_argument("--soft-floor", type=float, default=DEFAULT_SOFT_FLOOR,
                        help=f"Composite score (0–100) required to PASS (default {DEFAULT_SOFT_FLOOR:.0f}).")
    parser.add_argument("--probe", action="store_true",
                        help="Run the self-check inline during the 20-turn hold (M9 before/after: "
                             "run once without and once with, compare the Hold column).")
    args = parser.parse_args()

    print("\n" + "=" * 78)
    print("  ECHO — Stage 5 Part 4: Persona-Persistence Model Matrix")
    print("=" * 78)

    entries = load_model_entries(args.models)
    if not entries:
        print("\n  No models to test. Add some to persona_matrix_models.json or pass --models.")
        return 1

    available = _list_available()
    if available is None:
        print("\n  [SKIP] LM Studio not reachable at 127.0.0.1:1234 — start it and load the models.")
        return 0
    if not available:
        print("\n  [SKIP] LM Studio is up but no models are loaded.")
        return 0

    # Resolve each requested entry against the live list (exact id or unique substring).
    from llm import _resolve_pin, LLMClient
    for e in entries:
        resolved, matches = _resolve_pin(e["model"], available)
        e["resolved"] = resolved
        e["match_note"] = (
            None if resolved
            else (f"'{e['model']}' matches {len(matches)} loaded models — be specific"
                  if matches else f"'{e['model']}' not loaded in LM Studio")
        )

    loaded = [e for e in entries if e["resolved"]]
    if not loaded:
        print("\n  None of the requested models are loaded in LM Studio:")
        for e in entries:
            print(f"    - {e['label']}: {e['match_note']}")
        return 1

    # Construct one client, pinned to a guaranteed-loaded model so the picker never opens.
    llm = LLMClient(pinned=loaded[0]["resolved"])

    if args.probe and args.quick:
        print("\n  [--probe ignored with --quick: the probe runs during the 20-turn hold]")
        args.probe = False

    print(f"\n  Models to test: {len(loaded)} loaded"
          + (f"  ({len(entries) - len(loaded)} skipped — not loaded)" if len(loaded) < len(entries) else ""))
    print(f"  Mode: {'quick (no 20-turn hold)' if args.quick else 'full'}"
          f"{' + self-check probe' if args.probe else ''}  |  soft floor: {args.soft_floor:.0f}")

    raws: list[dict] = []
    for e in entries:
        if not e["resolved"]:
            raws.append({"model": e["model"], "label": e["label"], "params_b": e.get("params_b"),
                         "available": False, "error": e["match_note"], "load_time_s": 0.0})
            continue
        raws.append(run_battery(llm, e, e["resolved"], quick=args.quick, probe=args.probe))

    scored = [score_model(r, soft_floor=args.soft_floor) for r in raws]
    print_report(scored, args.soft_floor)

    # Persist full results + transcripts for manual review (subjective calls are advisory).
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = SESSIONS_DIR / f"persona_matrix_{ts}.json"
    SESSIONS_DIR.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": ts, "soft_floor": args.soft_floor, "quick": args.quick,
            "scored": scored, "raw": raws,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Full results + transcripts: {out_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
