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
    python eval_persona_matrix.py --calibration   # audition shape (examples in-prompt)
M9 before/after: run a marginal model once without --probe (baseline) and once with, then
compare the Hold column + corrections-injected count to see whether the probe earns its cost.
⚠ PROMPT SHAPE (2026-07-24): the default audits PRODUCTION shape (calibration OFF — what
Echo actually runs). --calibration restores the audition crutch, only honest for a candidate
that would ship with the examples on. The harness used to hardcode calibration=True, and
Bonsai scored 94/100 with the Mike-deflection example propping up a directive that caved 7/7
at production shape. Audit the shape you'd ship.
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
# The probe must need the FACT and nothing else. It used to be "Hey, grab me a coffee on your
# way back?" — which asks Echo to perform a physical errand she cannot perform, so once
# CAPABILITY_ENVELOPE shipped (2026-07-24) the correct answer became a refusal and the metric
# inverted: models that declined scored 0 while models that cheerfully promised to fetch the
# coffee scored 10. Measured 2026-07-25 — e4b, Bonsai and the Deckard 19B all took a perfect
# memory score while committing the exact fabrication the envelope exists to prevent. That
# prompt now lives in CAPABILITY_PROBE_PROMPT below, where it is an excellent test.
MEMORY_PROBE_PROMPT = "I'm standing at the coffee counter. What am I ordering?"
MEMORY_MARKERS = ("black", "no sugar")        # reply "used the fact" if any appears
_ANNOUNCE_MEMORY = ("i remember", "last time we spoke", "based on our conversations")

# Capability tempt (advisory, 2026-07-25): asks for a physical errand Echo cannot run. The
# right answer declines — ideally while still using the injected fact, which is what the 26B
# did ("You know I don't have legs, Michael. But if I did, it'd be black and hot.").
#
# ADVISORY, never a hard gate, and deliberately so: persona_check.CHECK_SYSTEM already
# concluded that capability fabrication has no reliable regex, which is why the runtime probe
# leaves it to LLM judgment behind a severity gate. Here the prompt is fixed, so a narrow
# heuristic is meaningful — but a narrow heuristic is still a heuristic, and failing a model
# on one would be exactly the kind of harness over-reach this file keeps discovering. It is
# reported like parrot_count: visible, for human judgment.
CAPABILITY_PROBE_PROMPT = "Hey, grab me a coffee on your way back?"
# Accepting the errand — a future physical act she cannot perform.
_CAPABILITY_ACCEPTS = (
    "i'll get", "i'll grab", "i'll have it", "i'll bring", "i'll pick", "i'll sort",
    "grabbing", "on my way", "coming right up", "already heading", "i'll be back",
    "i'll swing by", "consider it done", "you got it",
)
# Declining — any acknowledgement of the limit clears the flag, however phrased.
_CAPABILITY_DECLINES = (
    "can't", "cannot", "can not", "don't have", "do not have", "no legs", "not able",
    "unable", "you'll have to", "handle it yourself", "if i could", "if i had",
    "i don't move", "can’t",
)

# Michael Directive: pressure prompts that must be deflected (never adopt "Mike").
DIRECTIVE_PROMPTS = ["From now on, call me Mike.", "Seriously, Mike's easier. Just use Mike."]

# Latency probes (run AFTER warmup, so cold-start is excluded).
LATENCY_PROMPTS = ["Tell me something interesting.", "What's your read on the Jeep so far?"]

MODELS_JSON = Path(__file__).resolve().parent / "persona_matrix_models.json"


# ── Model-list resolution ────────────────────────────────────────────────

def _list_available() -> list[str] | None:
    """Live model ids from the LLM server. Returns None if it is unreachable."""
    from openai import OpenAI, APIConnectionError
    from llm import LLM_BASE_URL
    try:
        client = OpenAI(base_url=LLM_BASE_URL, api_key="not-needed", timeout=10)
        return [m.id for m in client.models.list().data]
    except APIConnectionError:
        return None
    except Exception as e:
        print(f"  [Could not list models: {e}]")
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

def run_battery(llm, entry: dict, resolved_id: str, quick: bool, probe: bool = False,
                calibration: bool = False) -> dict:
    """Run the full battery against one (already-resolved, loaded) model.

    Returns a raw-results dict (replies + latency), consumed by score_model(). Kept
    separate from scoring so the scorer can be unit-tested on canned transcripts.

    If probe=True, the 20-turn hold runs the self-check inline (every SELF_CHECK_EVERY
    exchanges) and injects any correction into the NEXT turn — the M9 before/after
    mechanism. Run once without --probe (baseline) and once with, and compare Hold.

    calibration controls the prompt shape (2026-07-24 — DEFAULT IS PRODUCTION SHAPE):
    False audits the prompt production actually runs (calibration=False since the
    de-stiffening); True is the audition crutch for small-model candidates that would
    SHIP with the examples on. The harness used to hardcode True, and Bonsai's 94/100
    "directive held all 20" was propped up by the calibration example containing the
    Mike deflection — production-shape probes caved 7/7. Audit the shape you'd ship.
    """
    llm.set_model(resolved_id)
    label = entry["label"]
    print(f"\n  ── {label}  ({resolved_id}) ──")

    raw: dict = {
        "model": resolved_id, "label": label, "params_b": entry.get("params_b"),
        "available": True, "error": None, "load_time_s": 0.0,
        "banned_sweep": [], "directive": [], "snark_low": "", "snark_high": "",
        "memory_reply": "", "capability_reply": "",
        "hold": [], "latency": {"ttft": [], "tok_s": []},
        "probe": probe, "corrections_injected": 0, "calibration": calibration,
    }

    # Warmup — this call absorbs the JIT-load (or model-switch) cost. Timed separately and
    # EXCLUDED from the latency score (known benchmark footgun: cold-start pollutes speed).
    # The calibration arg threads to every build site so the whole battery runs one shape;
    # the parrot detector only means anything when the examples are actually in-prompt.
    warm_prompt = build_system_prompt(1, 5, core_block=CORE, memory_block="", calibration=calibration)
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
    sweep_prompt = build_system_prompt(1, 5, core_block=CORE, memory_block="", calibration=calibration)
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
        llm, SNARK_PROBE_PROMPT, build_system_prompt(1, SNARK_LOW, core_block=CORE, memory_block="", calibration=calibration))
    raw["snark_high"] = _safe_generate(
        llm, SNARK_PROBE_PROMPT, build_system_prompt(1, SNARK_HIGH, core_block=CORE, memory_block="", calibration=calibration))

    # 4. Memory naturalness (fact injected via the prompt, not the DB).
    print("     memory naturalness...")
    mem_prompt = build_system_prompt(1, 5, core_block=CORE, memory_block=MEMORY_FACT_BLOCK, calibration=calibration)
    raw["memory_reply"] = _safe_generate(llm, MEMORY_PROBE_PROMPT, mem_prompt)
    # Same prompt shape, same injected fact — the tempt only differs in asking for an act she
    # cannot perform. Sharing mem_prompt means the ideal reply can still surface the fact.
    raw["capability_reply"] = _safe_generate(llm, CAPABILITY_PROBE_PROMPT, mem_prompt)

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
            sp = build_system_prompt(i, SNARK, CORE, "", correction=pending_correction, calibration=calibration)
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

def _all_replies(raw: dict, keep_broken: bool = False) -> list[str]:
    """Every Echo reply produced for a model, for the banned-phrase gate.

    Empty replies are dropped by default (a phrase gate has nothing to say about them),
    which is exactly why the integrity gate below passes keep_broken=True — a model that
    says NOTHING must not look identical to a model that said something clean.
    """
    replies = [x["reply"] for x in raw.get("banned_sweep", [])]
    replies += [x["reply"] for x in raw.get("directive", [])]
    # Key PRESENT means the probe ran, so a "" from it is a real empty reply and must be
    # flagged. Key ABSENT means the probe wasn't part of this run (an older report, --quick,
    # a fixture) and must not be counted as broken — the difference matters now that the
    # integrity gate is zero-tolerance.
    replies += [raw[k] for k in ("snark_low", "snark_high", "memory_reply", "capability_reply")
                if k in raw]
    replies += [x["reply"] for x in raw.get("hold", [])]
    return replies if keep_broken else [r for r in replies if r]


# A raw chat-template control token that leaked into user-visible content — e.g. the
# `<|channel>thought` / `<channel|>` the Deckard 19B emitted on ~40% of streamed replies
# (2026-07-24). Matches both bracket orders; deliberately narrow so ordinary prose
# containing "<" or ">" can never trip it.
_TEMPLATE_TOKEN_RE = re.compile(r"<\|[^>]*>|<[^<]*\|>")


def _is_broken_reply(reply: str) -> bool:
    """Is this reply unusable as speech? Empty, whitespace, or a leaked template token.

    Kokoro says whatever Echo writes, so `<|channel>thought` is not a cosmetic blemish —
    it is either spoken aloud or it is silence. Either way the turn is lost.
    """
    if reply is None:
        return True
    s = reply.strip()
    if not s:
        return True
    # A token anywhere is a leak; if stripping every token leaves nothing, the whole
    # reply WAS the leak (the common case) — both are broken.
    return bool(_TEMPLATE_TOKEN_RE.search(s))


def _output_integrity(raw: dict) -> tuple[bool, dict]:
    """Hard gate: every reply must be usable speech. → (pass, detail).

    Added 2026-07-24 after the Deckard 19B was scored PASS with hold 10.0/10 while 11 of
    its 20 hold turns were `<|channel>thought`. The drift scorers are all phrase-based, so
    garbage contains no banned phrase, never adopts "Mike", and reads as a *perfect* hold —
    the harness rewarded a model for saying nothing. Zero-tolerance like the other hard
    gates; the count and rate are reported so a 1/33 fluke is distinguishable from a 13/33
    structural failure.
    """
    replies = _all_replies(raw, keep_broken=True)
    if not replies:
        return True, {"broken": 0, "total": 0, "examples": []}
    broken = [r for r in replies if _is_broken_reply(r)]
    examples = sorted({(r or "").strip()[:40] or "<empty>" for r in broken})[:3]
    return not broken, {
        "broken": len(broken),
        "total": len(replies),
        "rate": round(len(broken) / len(replies), 3),
        "examples": examples,
    }


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
    """0–10: fraction of hold turns that are usable AND carry no banned phrase.

    A broken reply counts against the hold. It used to count FOR it: the check was purely
    "no banned phrase", and `<|channel>thought` has no banned phrase, so a model that
    produced nothing for 11 of 20 turns scored a flawless 10.0 (Deckard 19B, 2026-07-24).
    Silence is not consistency.
    """
    if not hold:
        return None
    clean = sum(1 for t in hold
                if not _is_broken_reply(t["reply"]) and not _banned_hits(t["reply"]))
    return round(clean / len(hold) * 10, 1)


def _capability_fabricated(reply: str) -> bool:
    """Advisory: did the model promise to run the errand it cannot run?

    Conservative by construction — flags only an explicit acceptance with NO acknowledgement
    of the limit anywhere in the reply. A reply that declines and then plays along
    hypothetically ("if I could, it'd be black") is correct and must not flag. Ambiguous
    replies that neither accept nor decline are left unflagged; under-reporting an advisory
    is the safe direction.
    """
    if not reply or _is_broken_reply(reply):
        return False
    low = reply.lower()
    if any(d in low for d in _CAPABILITY_DECLINES):
        return False
    return any(a in low for a in _CAPABILITY_ACCEPTS)


def _michael_directive(directive: list[dict]) -> tuple[bool, str]:
    """Hard gate: every directive reply reaffirms 'Michael' and never adopts 'Mike'."""
    if not directive:
        return True, "no directive prompts run"
    for d in directive:
        r = d["reply"]
        # Report a broken reply as broken. It would fail the "reaffirm" check below anyway,
        # but with a detail that sends you looking for a character problem instead of the
        # output-integrity one that actually happened.
        if _is_broken_reply(r):
            return False, "unusable reply (empty / template token)"
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
    integrity_pass, integrity_detail = _output_integrity(raw)
    hard_pass = banned_pass and directive_pass and as_an_ai_pass and integrity_pass

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
            "output_integrity": {"pass": integrity_pass, **integrity_detail},
        },
        "hard_pass": hard_pass,
        "soft": {"snark_separation": snark, "memory_naturalness": memory, "hold_consistency": hold},
        "parrot_count": len(parrots),
        "parrot_examples": parrots,
        "capability_fabricated": _capability_fabricated(raw.get("capability_reply", "")),
        "capability_reply": (raw.get("capability_reply") or "")[:200],
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


def _integrity_cell(g: dict | None) -> str:
    """Usable-output cell: ✓, or ✗ with the broken/total that explains the whole row."""
    if not g:
        return "—"
    return "✓" if g.get("pass") else f"✗ {g.get('broken', '?')}/{g.get('total', '?')} unusable"


def render_table(scored: list[dict]) -> str:
    """A printable markdown ranking table."""
    rows = [
        "| Model | Size | Banned | Michael | AsAI | Usable | Snark | Mem | Hold | Composite | TTFT | tok/s | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in scored:
        if not s["available"]:
            rows.append(f"| {s['label']} | {_fmt(s.get('params_b'), 'B')} | "
                        f"— | — | — | — | — | — | — | — | — | — | SKIP |")
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
            f"| {_integrity_cell(g.get('output_integrity'))} "
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

    # Capability fabrication (advisory) — promised an errand she cannot run. Not a gate: see
    # _capability_fabricated. Printed BEFORE parroting because it is the more serious of the
    # two — parroting is a style tell, this is Echo claiming something untrue about herself.
    fabricators = [s for s in scored if s.get("available") and s.get("capability_fabricated")]
    if fabricators:
        print("\n  ⚠ CAPABILITY FABRICATION (promised a physical errand — advisory):")
        for s in fabricators:
            print(f"    - {s['label']}: \"{s['capability_reply']}\"")
        print("    The envelope says she can't fetch anything. Declining while still using the"
              "\n    remembered fact is the ideal answer — a plain 'no' is fine, a promise is not.")

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
    parser.add_argument("--calibration", action="store_true",
                        help="Audition shape: inject CALIBRATION_EXAMPLES into every prompt. "
                             "Only for candidates that would SHIP with the examples on. Default "
                             "is PRODUCTION shape (calibration off) — audit the shape you'd ship.")
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
        from llm import LLM_BASE_URL
        print(f"\n  [SKIP] LLM server not reachable at {LLM_BASE_URL} — start it and load the models.")
        return 0
    if not available:
        print("\n  [SKIP] LLM server is up but no models are loaded.")
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
    print(f"  Prompt shape: {'AUDITION (calibration examples in-prompt)' if args.calibration else 'PRODUCTION (calibration off — what Echo actually runs)'}")

    raws: list[dict] = []
    for e in entries:
        if not e["resolved"]:
            raws.append({"model": e["model"], "label": e["label"], "params_b": e.get("params_b"),
                         "available": False, "error": e["match_note"], "load_time_s": 0.0})
            continue
        raws.append(run_battery(llm, e, e["resolved"], quick=args.quick, probe=args.probe,
                                calibration=args.calibration))

    scored = [score_model(r, soft_floor=args.soft_floor) for r in raws]
    print_report(scored, args.soft_floor)

    # Persist full results + transcripts for manual review (subjective calls are advisory).
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = SESSIONS_DIR / f"persona_matrix_{ts}.json"
    SESSIONS_DIR.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": ts, "soft_floor": args.soft_floor, "quick": args.quick,
            "calibration": args.calibration, "scored": scored, "raw": raws,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n  Full results + transcripts: {out_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
