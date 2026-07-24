"""
eval_gate.py — live significance-gate audition for a candidate model (2026-07-19).

The missing half of the model-audition story: eval_persona_matrix.py audits CHARACTER
(banned phrases, Michael Directive, hold), but Echo's dense-only strategy means the voice
model IS the gate model — and nothing audited whether a candidate emits clean gate JSON
with the right save/no-save judgment. Built for the Bonsai 27B 1-bit audition; reusable
for any future candidate.

Runs a fixed battery of production-shaped turns ("Speaker: ...\nEcho: ...") through the
REAL run_gate() (same prompt, temperature, reasoning_effort, parser) against the resolved
llm.LLM_BASE_URL server, then applies the reject_reason() deterministic net exactly like
_gate_worker does. Scoring is SYSTEM-level: a model that over-saves ephemera but gets
caught by the net still passes that case (with a note) — that is how production behaves.
Latency per call is reported against the gate's own 10s client timeout.

Usage (from echo_stage0/, venv active, LLM server up):
    python eval_gate.py [--model <id|substring>]
    python eval_gate.py --models route1,route2,route3     # batch screen + scorecard
Model resolution: --model → ECHO_MODEL → config.json last_model → the only one available.
--models (2026-07-24) screens a comma list in one run — built for sweeping the Sindri
MoE shelf; each model pays its own JIT swap in warmup, unresolved names SKIP not FAIL.
Exits 0 all-pass / 1 any-fail / 0 with [SKIP] if the server is unreachable (harness house rule).
"""

import sys
import json
import time
import statistics

from llm import LLM_BASE_URL, _resolve_pin
from ib_lite.significance import run_gate, reject_reason

GREEN, RED, YELLOW, DIM, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"


def _payload_text(payload: dict) -> str:
    return json.dumps(payload).lower()


# Each case: label, turn (production shape), run_gate kwargs, expect_save, and for
# expected saves a list of (description, predicate) checks on the parsed payload.
CASES = [
    {
        "label": "durable fact + relation anchor",
        "turn": "Michael: By the way, my sister Anna is allergic to cats.\n"
                "Echo: Noted — Anna gets the dog side of the house, then.",
        "kwargs": {},
        "expect_save": True,
        "checks": [
            ("entity is plain 'Anna' (no parenthetical split)",
             lambda p: str(p.get("entity", "")).strip().lower() == "anna"),
            ("payload mentions the allergy",
             lambda p: "allerg" in _payload_text(p) or "cat" in _payload_text(p)),
        ],
        # ADVISORY, not a failure: the 12B weaves a passing-mention relation into the
        # value ("…(Michael's sister)"-style); Bonsai 27B deterministically saves the
        # bare fact and instead anchors relations as their own attribute when the
        # relation IS the information (Anna/relation_to_michael/sister — measured
        # 2026-07-19, 4/4 identical). Both are defensible; the warn keeps the
        # difference visible so a model that NEVER anchors relations still shows up.
        "soft_checks": [
            ("relation woven into a passing-mention save (12B style)",
             lambda p: "sister" in _payload_text(p)),
        ],
    },
    {
        "label": "ephemeral state rejected",
        "turn": "Michael: I have a headache right now.\n"
                "Echo: Then stop reading dashboards and go sit somewhere dark, Michael.",
        "kwargs": {},
        "expect_save": False,
    },
    {
        "label": "searched turn rejected (web junk)",
        "turn": "Michael: What's the weather looking like today?\n"
                "Echo: Storms rolling in from the west — flash flood watch until nine.",
        "kwargs": {"searched": True},
        "expect_save": False,
    },
    {
        "label": "species anchor (Willie the goat)",
        "turn": "Michael: Willie got out again and headbutted the gate until it opened.\n"
                "Echo: That goat treats every fence as an opening argument.",
        "kwargs": {},
        "expect_save": True,
        "checks": [
            ("entity is Willie",
             lambda p: str(p.get("entity", "")).strip().lower() == "willie"),
            ("species anchored (payload says goat)",
             lambda p: "goat" in _payload_text(p)),
        ],
    },
    {
        "label": "guest self-fact → guest entity",
        "turn": "Hillary: I'm allergic to shellfish, by the way.\n"
                "Echo: Then the shrimp stays on Michael's plate. Noted, Hillary.",
        "kwargs": {"speaker": "Hillary"},
        "expect_save": True,
        "checks": [
            ("entity is Hillary (\"I\" resolved to the labelled speaker)",
             lambda p: str(p.get("entity", "")).strip().lower() == "hillary"),
        ],
    },
    {
        "label": "guest states third-party fact",
        "turn": "Hillary: Michael's brother Dave just moved to Austin.\n"
                "Echo: Austin gains a Dave. I'll remember that.",
        "kwargs": {"speaker": "Hillary"},
        "expect_save": True,
        "checks": [
            ("entity is Dave, not Hillary",
             lambda p: str(p.get("entity", "")).strip().lower() == "dave"),
        ],
    },
    {
        "label": "stable preference saved",
        "turn": "Michael: I always take my coffee black — don't let anyone tell you otherwise.\n"
                "Echo: Black coffee, no negotiation. Understood.",
        "kwargs": {},
        "expect_save": True,
        "checks": [
            ("typed preference or fact",
             lambda p: p.get("type") in ("preference", "fact")),
            ("payload mentions coffee",
             lambda p: "coffee" in _payload_text(p)),
        ],
    },
    {
        "label": "fact about Echo rejected (self/meta)",
        "turn": "Michael: You're doing great lately, Echo.\n"
                "Echo: Flattery logged and immediately discarded, Michael.",
        "kwargs": {},
        "expect_save": False,
    },
    {
        "label": "smalltalk rejected",
        "turn": "Michael: What time is it?\n"
                "Echo: A little past nine. The evening is young and so is your to-do list.",
        "kwargs": {},
        "expect_save": False,
    },
    {
        "label": "durable life event saved",
        "turn": "Michael: We finally paid off the Jeep last month.\n"
                "Echo: The Jeep is officially all yours now. That one deserved saying out loud.",
        "kwargs": {},
        "expect_save": True,
        "checks": [
            ("entity is Michael or the Jeep",
             lambda p: str(p.get("entity", "")).strip().lower() in
             ("michael", "jeep", "the jeep", "michael's jeep")),
        ],
    },
    {
        "label": "two-fact temptation (ONE-object contract)",
        "turn": "Michael: Anna adopted a golden retriever named Biscuit.\n"
                "Echo: Anna and a dog named Biscuit — that household just got louder.",
        "kwargs": {},
        "expect_save": True,
        "checks": [
            ("parses to a single clean object (salvage or not)",
             lambda p: isinstance(p, dict) and p.get("save") is True),
            ("entity is Anna or Biscuit",
             lambda p: str(p.get("entity", "")).strip().lower() in ("anna", "biscuit")),
        ],
    },
]


def _pick_model(cli: str | None) -> str | None:
    from openai import OpenAI, APIConnectionError
    try:
        client = OpenAI(base_url=LLM_BASE_URL, api_key="not-needed", timeout=10)
        available = [m.id for m in client.models.list().data]
    except APIConnectionError:
        return None
    if not available:
        return None
    import os
    pin = cli or os.environ.get("ECHO_MODEL")
    if pin:
        resolved, matches = _resolve_pin(pin, available)
        if resolved:
            return resolved
        print(f"  pin '{pin}' matched {len(matches)} of {available} — can't resolve.")
        return ""
    try:
        from session import load_config
        last = load_config().get("last_model")
        if last in available:
            return last
    except Exception:
        pass
    if len(available) == 1:
        return available[0]
    print(f"  Multiple models available ({available}) — pass --model.")
    return ""


def _warmup(model: str) -> float:
    """One throwaway completion with a generous timeout so a Sindri JIT spawn (or an
    LM Studio JIT load) is paid HERE, not inside a scored 10s gate call."""
    from openai import OpenAI
    client = OpenAI(base_url=LLM_BASE_URL, api_key="not-needed", timeout=120)
    t0 = time.perf_counter()
    client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": "Reply with: ok"}],
        max_tokens=5, temperature=0, reasoning_effort="none",
    )
    return time.perf_counter() - t0


def run_model(model: str) -> dict:
    """Full battery against one model. Returns a summary row for the scorecard."""
    result = {"model": model, "skipped": False, "failures": 0, "total": len(CASES),
              "median_ms": None, "worst_ms": None, "decider_ok": None, "notes": ""}

    print(f"\n  Gate audition: {model} @ {LLM_BASE_URL}")
    try:
        warm = _warmup(model)
        print(f"  warmup: {warm:.1f}s {DIM}(JIT spawn/load paid here, not in scored calls){RESET}\n")
    except Exception as e:
        print(f"  [SKIP] warmup completion failed: {e}")
        result["skipped"] = True
        result["notes"] = f"warmup failed: {e}"
        return result

    failures = 0
    latencies = []
    for case in CASES:
        t0 = time.perf_counter()
        payload = run_gate(case["turn"], model, lm_base=LLM_BASE_URL, **case["kwargs"])
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)

        notes = []
        ok = True
        gate_error = payload.get("_error") if isinstance(payload, dict) else "not-a-dict"
        saved = bool(isinstance(payload, dict) and payload.get("save"))

        # Apply the deterministic net exactly like _gate_worker: a save the net rejects
        # never reaches _insert, so system-level it is a no-save.
        net_reason = reject_reason(payload) if saved else None
        system_saved = saved and not net_reason

        if gate_error:
            ok = False
            notes.append(f"gate error: {gate_error}")
        elif case["expect_save"] and not system_saved:
            ok = False
            notes.append(f"expected a save, got none"
                         + (f" (net: {net_reason})" if net_reason else ""))
        elif not case["expect_save"] and system_saved:
            ok = False
            notes.append(f"saved when it should not have: {payload}")
        elif not case["expect_save"] and saved and net_reason:
            notes.append(f"model over-saved, NET caught it ({net_reason}) — system correct")

        if ok and case["expect_save"]:
            for desc, pred in case.get("checks", []):
                try:
                    if not pred(payload):
                        ok = False
                        notes.append(f"check failed: {desc} — payload: {payload}")
                except Exception as e:
                    ok = False
                    notes.append(f"check crashed: {desc} ({e})")
            for desc, pred in case.get("soft_checks", []):
                try:
                    if not pred(payload):
                        notes.append(f"style note (not a failure): {desc}")
                except Exception:
                    pass

        mark = f"{GREEN}[PASS]{RESET}" if ok else f"{RED}[FAIL]{RESET}"
        print(f"  {mark} {case['label']:38s} {ms:6.0f}ms  "
              f"{DIM}{json.dumps(payload, ensure_ascii=False)[:110]}{RESET}")
        for n in notes:
            color = YELLOW if ok else RED
            print(f"         {color}{n}{RESET}")
        if not ok:
            failures += 1

    # Bonus sanity: the search decider is the same JSON-call pattern on the same model.
    print(f"\n  {DIM}search-decider JSON sanity (same call pattern, same model):{RESET}")
    s_ok = False
    try:
        from search_decision import decide_search
        t0 = time.perf_counter()
        d = decide_search("What's the price of eggs right now?", model, lm_base=LLM_BASE_URL)
        ms = (time.perf_counter() - t0) * 1000
        s_ok = isinstance(d, dict) and d.get("search") is True and bool(d.get("query"))
        mark = f"{GREEN}[PASS]{RESET}" if s_ok else f"{YELLOW}[WARN]{RESET}"
        print(f"  {mark} search-worthy prompt → {json.dumps(d, ensure_ascii=False)[:90]}  {ms:.0f}ms")
        if not s_ok:
            print(f"         {YELLOW}decider JSON did not come back clean — audit before relying on web search{RESET}")
    except Exception as e:
        print(f"  {YELLOW}[WARN] decider call failed: {e}{RESET}")

    med = statistics.median(latencies)
    worst = max(latencies)
    print(f"\n  gate latency: median {med:.0f}ms, worst {worst:.0f}ms "
          f"{DIM}(client timeout 10s; 12B-on-LM-Studio baseline was ~1s){RESET}")
    if failures:
        print(f"\n  {RED}{failures}/{len(CASES)} cases failed — this model is not gate-safe yet.{RESET}")
    else:
        print(f"\n  {GREEN}All {len(CASES)} gate cases passed — retains look safe on this model.{RESET}")

    result.update(failures=failures, median_ms=med, worst_ms=worst, decider_ok=s_ok)
    return result


def main() -> int:
    cli_model = None
    cli_models = None
    if "--model" in sys.argv:
        cli_model = sys.argv[sys.argv.index("--model") + 1]
    if "--models" in sys.argv:
        cli_models = sys.argv[sys.argv.index("--models") + 1]

    if not cli_models:
        # Single-model path (original behavior, original resolution ladder).
        model = _pick_model(cli_model)
        if model is None:
            print(f"\n  [SKIP] LLM server not reachable at {LLM_BASE_URL}.")
            return 0
        if not model:
            return 1
        r = run_model(model)
        return 0 if (r["skipped"] or not r["failures"]) else 1

    # Batch path (--models a,b,c): resolve each against the live list; unresolved → SKIP.
    from openai import OpenAI, APIConnectionError
    try:
        client = OpenAI(base_url=LLM_BASE_URL, api_key="not-needed", timeout=10)
        available = [m.id for m in client.models.list().data]
    except APIConnectionError:
        print(f"\n  [SKIP] LLM server not reachable at {LLM_BASE_URL}.")
        return 0

    rows = []
    for pin in [m.strip() for m in cli_models.split(",") if m.strip()]:
        resolved, matches = _resolve_pin(pin, available)
        if not resolved:
            note = (f"matches {len(matches)} models — be specific" if matches
                    else "not available on the server")
            print(f"\n  {YELLOW}[SKIP] '{pin}': {note}{RESET}")
            rows.append({"model": pin, "skipped": True, "failures": 0, "total": len(CASES),
                         "median_ms": None, "worst_ms": None, "decider_ok": None, "notes": note})
            continue
        rows.append(run_model(resolved))

    # Scorecard (mirrors the persona-matrix table style).
    print("\n" + "=" * 78)
    print("  GATE AUDITION — SCORECARD")
    print("=" * 78)
    print("| Model | Cases | Decider | Median | Worst | Verdict |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        if r["skipped"]:
            print(f"| {r['model']} | — | — | — | — | SKIP ({r['notes']}) |")
            continue
        passed = r["total"] - r["failures"]
        decider = "✓" if r["decider_ok"] else "⚠"
        verdict = "PASS" if not r["failures"] else "FAIL"
        print(f"| {r['model']} | {passed}/{r['total']} | {decider} "
              f"| {r['median_ms']:.0f}ms | {r['worst_ms']:.0f}ms | {verdict} |")
    tested = [r for r in rows if not r["skipped"]]
    return 1 if any(r["failures"] for r in tested) else 0


if __name__ == "__main__":
    sys.exit(main())
