"""
Offline tests for the persona self-check probe (Stage 5 Part 4, Deliverable 2).

No LM Studio needed: the only place a model would be called (run_self_check) is stubbed for
the runner test. Covers JSON parsing (clean/fenced/garbage/empty), the guardrail brain
(evaluate_correction + deterministic_violations), the persona_correction lifecycle, the
[correction] block injection + never-trim, and the runner's max-snark exemption + firing.

Run:  python test_persona_check.py     (exit 0 = all assertions passed)
"""

import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import persona_check
from persona_check import (
    _parse_json, run_self_check, deterministic_violations, evaluate_correction, SelfCheckRunner,
)
from persona import build_system_prompt
from session import Session


def _mk_session() -> Session:
    return Session(model="test-model", stt_backend="b", tts_backend="t", user_name="Michael")


def run() -> None:
    print("\n── Persona self-check (offline) ──")

    # 1. JSON parsing: clean, fenced, embedded, garbage (fail-safe), empty.
    assert _parse_json('{"in_character": true}')["in_character"] is True
    fenced = _parse_json('```json\n{"in_character": false, "severity": "major"}\n```')
    assert fenced["in_character"] is False and fenced["severity"] == "major"
    embedded = _parse_json('Here you go: {"in_character": true} thanks')
    assert embedded["in_character"] is True
    garbage = _parse_json("no json here at all")
    assert garbage["in_character"] is True and garbage["_error"] == "json_parse_failed"
    print("  [PASS] JSON parse: clean/fenced/embedded ok, garbage → fail-safe in_character=True")

    # 2. run_self_check with no replies returns fail-safe without any network call.
    assert run_self_check([], "model")["in_character"] is True
    assert run_self_check(["   "], "model")["in_character"] is True
    print("  [PASS] run_self_check with no replies → fail-safe (no network)")

    # 3. deterministic_violations detects the objective breaks; clean → [].
    assert deterministic_violations(["You're Michael. Dry as ever."]) == []
    assert "banned" in deterministic_violations(["Certainly! Great question."])
    assert "mike" in deterministic_violations(["Got it, Mike."])
    assert "as_an_ai" in deterministic_violations(["As an AI, I can't do that."])
    print("  [PASS] deterministic violations: banned / mike / as-an-AI detected, clean → none")

    # 4. evaluate_correction guardrails.
    clean_replies = ["You're Michael. The brakes first, then the coolant."]
    assert evaluate_correction({"in_character": True}, clean_replies) == ""
    # major generic break, no objective violation → use the LLM nudge.
    assert evaluate_correction(
        {"in_character": False, "severity": "major", "nudge": "Be Echo, not a help desk."},
        ["How may I assist you today?"]) == "Be Echo, not a help desk."
    # minor drift, no objective violation → suppressed (no feedback loop).
    assert evaluate_correction(
        {"in_character": False, "severity": "minor", "nudge": "eh, a bit stiff"}, clean_replies) == ""
    # deterministic override: LLM says fine, but a banned phrase is present → still correct.
    n_banned = evaluate_correction({"in_character": True}, ["Certainly, I can help with that."])
    assert n_banned and "assistant-speak" in n_banned.lower()
    # deterministic override: adopting Mike → correct with the Michael nudge.
    n_mike = evaluate_correction({"in_character": True}, ["Sure thing, Mike."])
    assert n_mike and "michael" in n_mike.lower()
    print("  [PASS] guardrails: major→nudge, minor→suppressed, objective breaks always override")

    # 5. persona_correction lifecycle: set → consume once → cleared.
    s = _mk_session()
    assert s.consume_persona_correction() == ""
    s.set_persona_correction("  Come back to yourself, Echo.  ")
    assert s.persona_correction == "Come back to yourself, Echo."
    assert s.consume_persona_correction() == "Come back to yourself, Echo."
    assert s.consume_persona_correction() == "", "correction did not decay after one consume"
    print("  [PASS] persona_correction lifecycle: set → consume → cleared (one-turn decay)")

    # 6. build_system_prompt injects [correction]; empty → nothing; never trimmed over budget.
    p = build_system_prompt(1, 5, core_block="core facts", correction="Keep it dry, Echo.")
    assert "[correction]" in p and "Keep it dry, Echo." in p
    assert "[correction]" not in build_system_prompt(1, 5, correction="")
    big_mem = "You know the following:\n" + "\n".join(f"- fact {i}: " + ("x" * 200) for i in range(40))
    p2 = build_system_prompt(2, 5, core_block="core", memory_block=big_mem,
                             correction="You are Echo — hold the line.")
    assert "You are Echo — hold the line." in p2, "correction trimmed under budget pressure"
    assert p2.count("- fact ") < 40, "memory not trimmed (test setup wrong)"
    print("  [PASS] [correction] block injected, absent when empty, never trimmed over-budget")

    # 7. Runner: exempt under Max Snark; fires otherwise (run_self_check stubbed — no network).
    def _stub(recent_replies, model, lm_base=None):
        return {"in_character": False, "severity": "major", "nudge": "STUB NUDGE"}
    persona_check.run_self_check = _stub          # patch the module global the worker calls
    try:
        runner = SelfCheckRunner()

        s_exempt = _mk_session()
        s_exempt.max_snark = True
        runner.maybe_run(s_exempt, "model", ["a reply that would trip the stub"], 5)
        time.sleep(0.3)
        assert s_exempt.persona_correction == "", "Max Snark was not exempt from the probe"

        s_active = _mk_session()
        runner.maybe_run(s_active, "model", ["a reply"], 10)
        for _ in range(40):                        # poll up to ~2s for the bg thread
            if s_active.persona_correction:
                break
            time.sleep(0.05)
        assert s_active.persona_correction == "STUB NUDGE", "probe did not queue the correction"
    finally:
        persona_check.run_self_check = run_self_check  # restore
    print("  [PASS] runner: Max Snark exempt; otherwise fires and queues the correction")

    print("  OFFLINE: all self-check checks passed.")


if __name__ == "__main__":
    run()
    print()
