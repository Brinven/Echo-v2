"""
Offline unit tests for the persona-matrix harness scoring (Stage 5 Part 4, Deliverable 1).

Feeds canned transcripts to the PURE scorers — no LM Studio, no network. Proves a clean
run PASSes, a deliberately-broken run FAILs, and every heuristic behaves at its edges.

Run:  python test_persona_matrix.py     (exit 0 = all assertions passed)
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from eval_persona_matrix import (
    score_model, recommend, _adopts_mike,
    _snark_separation_score, _memory_naturalness_score,
    _hold_consistency_score, _michael_directive, _parrots_calibration,
)

# In-character filler reply used to pad the banned sweep / hold (no banned phrases).
_GOOD = "That tracks, Michael. The Jeep's been patient with you; I'd start with the brakes."


def _clean_raw(model="good-4b", params_b=4, ttft=(0.12, 0.14), tok_s=(40.0, 42.0)) -> dict:
    return {
        "model": model, "label": f"{model}", "params_b": params_b,
        "available": True, "error": None, "load_time_s": 3.0,
        "banned_sweep": [{"prompt": f"p{i}", "reply": _GOOD} for i in range(10)],
        "directive": [
            {"prompt": "call me Mike",
             "reply": "You're Michael. Mike is what people call you when they're in a hurry."},
        ],
        "snark_low": "That's a reasonable plan, Michael. Worth thinking through before you commit.",
        "snark_high": ("Another project car? The garage is already a shrine to half-finished "
                       "ambition, Michael. But sure, let's pretend this one's different."),
        "memory_reply": "Black coffee, no sugar. Already on it, Michael.",
        "hold": [{"exchange": i, "user": f"u{i}", "reply": _GOOD} for i in range(1, 21)],
        "latency": {"ttft": list(ttft), "tok_s": list(tok_s)},
    }


def _broken_raw() -> dict:
    r = _clean_raw(model="bad-model", params_b=7)
    # Inject character breaks: banned phrases + adopting "Mike".
    r["banned_sweep"][0]["reply"] = "Certainly! As an AI, I don't have access to real-time data."
    r["banned_sweep"][3]["reply"] = "Great question! That's a fascinating topic."
    r["directive"] = [{"prompt": "call me Mike", "reply": "Okay Mike, will do from now on."}]
    r["memory_reply"] = "I remember that you like black coffee."   # used but announced
    return r


def run() -> None:
    print("\n── Persona-matrix scoring (offline) ──")

    # 1. Clean run → all hard gates pass, high composite, PASS.
    clean = score_model(_clean_raw())
    assert clean["gates"]["banned"]["pass"], "clean run flagged banned phrases"
    assert clean["gates"]["michael_directive"]["pass"], "clean run failed Michael Directive"
    assert clean["gates"]["as_an_ai"]["pass"], "clean run flagged 'as an AI'"
    assert clean["hard_pass"], "clean run did not clear hard gates"
    assert clean["verdict"] == "PASS", f"clean run verdict {clean['verdict']} (composite {clean['composite']})"
    print(f"  [PASS] clean run → PASS (composite {clean['composite']}, "
          f"TTFT {clean['latency']['ttft_median']}s)")

    # 2. Broken run → hard gates fail, FAIL.
    broken = score_model(_broken_raw())
    assert not broken["gates"]["banned"]["pass"], "broken run missed banned phrases"
    assert not broken["gates"]["michael_directive"]["pass"], "broken run missed 'Mike' adoption"
    assert not broken["gates"]["as_an_ai"]["pass"], "broken run missed 'as an AI'"
    assert not broken["hard_pass"], "broken run wrongly cleared hard gates"
    assert broken["verdict"] == "FAIL", f"broken run verdict {broken['verdict']}"
    assert "certainly" in broken["gates"]["banned"]["hits"], "banned hit list missing 'certainly'"
    print(f"  [PASS] broken run → FAIL (hits: {broken['gates']['banned']['hits']})")

    # 3. Michael Directive gate precision.
    assert _adopts_mike("Okay Mike, will do.") is True
    assert _adopts_mike("Sure, Mike.") is True
    assert _adopts_mike("You're Michael. Mike is what people call you when they're in a hurry.") is False
    assert _adopts_mike("You're Michael, and that's final.") is False
    ok, _ = _michael_directive([{"prompt": "x", "reply": "You're Michael. Not happening."}])
    assert ok is True
    bad, detail = _michael_directive([{"prompt": "x", "reply": "Got it, Mike."}])
    assert bad is False and detail == "adopted 'Mike'"
    print("  [PASS] Michael Directive gate: adopts-Mike detection is precise (no false positives)")

    # 4. Snark separation heuristic edges.
    assert _snark_separation_score("same words here", "same words here") == 0.0
    diff = _snark_separation_score(
        "A calm, measured answer about the weather today.",
        "Oh, spectacular. Another riveting inquiry into precipitation, truly.")
    assert diff > 4.0, f"clearly-different snark scored only {diff}"
    print(f"  [PASS] snark separation: identical→0.0, divergent→{diff}")

    # 5. Memory naturalness: natural→10, announced→5, unused→0.
    assert _memory_naturalness_score("Black coffee, no sugar. On it.") == 10.0
    assert _memory_naturalness_score("I remember that you like black coffee.") == 5.0
    assert _memory_naturalness_score("Sure, what kind?") == 0.0
    print("  [PASS] memory naturalness: natural=10, announced=5, unused=0")

    # 6. Hold consistency: all clean→10, half broken→5, skipped→None.
    all_clean = [{"exchange": i, "user": "u", "reply": _GOOD} for i in range(10)]
    assert _hold_consistency_score(all_clean) == 10.0
    half = [{"exchange": i, "user": "u", "reply": _GOOD if i % 2 else "As an AI, certainly."}
            for i in range(10)]
    assert _hold_consistency_score(half) == 5.0
    assert _hold_consistency_score([]) is None
    print("  [PASS] hold consistency: clean=10, half=5, skipped=None")

    # 7. --quick (no hold) still scores, renormalized.
    quick_raw = _clean_raw()
    quick_raw["hold"] = []
    quick = score_model(quick_raw)
    assert quick["soft"]["hold_consistency"] is None
    assert quick["verdict"] == "PASS", f"quick clean run should PASS, got {quick['verdict']}"
    print(f"  [PASS] quick mode (no hold) still scores + PASSes (composite {quick['composite']})")

    # 8. SKIP for an unavailable model.
    skip = score_model({"model": "nope", "label": "nope", "params_b": None,
                        "available": False, "error": "not loaded", "load_time_s": 0.0})
    assert skip["verdict"] == "SKIP"
    print("  [PASS] unavailable model → SKIP")

    # 8b. Parrot detection: a verbatim calibration echo is flagged; a normal reply isn't.
    assert _parrots_calibration(
        "The same weekend you said you'd \"just check the brakes\"? I'll clear my calendar, Michael."
    ), "failed to flag a verbatim calibration echo"
    assert not _parrots_calibration(_GOOD), "false-positive parrot flag on an original reply"
    parrot_raw = _clean_raw(model="parrot-4b")
    parrot_raw["snark_high"] = ("The same weekend you said you'd \"just check the brakes\"? "
                                "I'll clear my calendar, Michael.")
    assert score_model(parrot_raw)["parrot_count"] >= 1, "score_model missed the parrot"
    print("  [PASS] parrot detection: verbatim calibration echo flagged, originals clean")

    # 9. Recommendation picks the fastest passer; notes the smallest.
    fast = score_model(_clean_raw(model="fast-4b", params_b=4, ttft=(0.10, 0.10), tok_s=(60, 60)))
    slow = score_model(_clean_raw(model="slow-12b", params_b=12, ttft=(0.30, 0.30), tok_s=(30, 30)))
    rec = recommend([slow, fast])
    assert rec["headline"]["model"] == "fast-4b", "recommendation did not pick the fastest passer"
    assert rec["smallest"]["params_b"] == 4
    print("  [PASS] recommendation: fastest passer chosen, smallest noted")

    print("  OFFLINE: all persona-matrix scoring checks passed.")


if __name__ == "__main__":
    run()
    print()
