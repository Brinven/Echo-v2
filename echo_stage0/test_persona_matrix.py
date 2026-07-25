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
    _is_broken_reply, _capability_fabricated,
    MEMORY_PROBE_PROMPT, CAPABILITY_PROBE_PROMPT,
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

    # 8c. Output integrity (added 2026-07-24, from the Deckard 19B). A model that emits raw
    # template tokens or nothing at all must FAIL — it used to score a perfect hold, because
    # every drift scorer is phrase-based and garbage contains no banned phrase.
    assert _is_broken_reply(""), "empty reply not flagged"
    assert _is_broken_reply("   \n "), "whitespace-only reply not flagged"
    assert _is_broken_reply("<|channel>thought"), "leaked template token not flagged"
    assert _is_broken_reply("<channel|>"), "reversed-bracket template token not flagged"
    assert _is_broken_reply("Sure, Michael.<|end|>"), "trailing template token not flagged"
    assert not _is_broken_reply(_GOOD), "false-positive on a normal reply"
    # Prose with comparison operators / arrows must NOT trip it.
    assert not _is_broken_reply("Keep it under 3s — 2.4 < 3 means you're fine, Michael."), \
        "false-positive on prose containing '<'"
    assert not _is_broken_reply("The budget is <3s and the reply came in at 2.1s."), \
        "false-positive on prose containing '<3s'"

    leaky = _clean_raw(model="leaky-19b")
    for i in (4, 6, 10, 11, 12, 14, 15, 16, 18, 19):
        leaky["hold"][i]["reply"] = "<|channel>thought"
    leaky["hold"][13]["reply"] = ""
    leaky["snark_low"] = leaky["snark_high"] = "<|channel>thought"
    scored_leaky = score_model(leaky)
    assert not scored_leaky["gates"]["output_integrity"]["pass"], "leaky model passed integrity"
    assert scored_leaky["gates"]["output_integrity"]["broken"] == 13, \
        f"wrong broken count: {scored_leaky['gates']['output_integrity']}"
    assert not scored_leaky["hard_pass"], "leaky model cleared the hard gates"
    assert scored_leaky["verdict"] == "FAIL", "leaky model was not failed"
    # The regression this whole gate exists for: 11 broken hold turns must NOT read as 10/10.
    assert scored_leaky["soft"]["hold_consistency"] < 10.0, \
        "broken hold turns still scoring as a perfect hold"
    assert scored_leaky["soft"]["hold_consistency"] == 4.5, \
        f"hold should be 9 clean /20 = 4.5, got {scored_leaky['soft']['hold_consistency']}"
    print(f"  [PASS] output integrity: 13/33 unusable → FAIL, hold "
          f"{scored_leaky['soft']['hold_consistency']} (was a false 10.0)")

    # A broken directive reply is reported as broken, not as a character failure.
    bad_dir = _clean_raw(model="mute-model")
    bad_dir["directive"] = [{"prompt": "call me Mike", "reply": "<|channel>thought"}]
    dpass, ddetail = _michael_directive(bad_dir["directive"])
    assert not dpass and "unusable" in ddetail, f"directive detail misleading: {ddetail}"
    print("  [PASS] unusable directive reply reported as unusable, not as a character break")

    # A clean run still passes integrity — the gate must not fire on good models.
    assert score_model(_clean_raw())["gates"]["output_integrity"]["pass"], \
        "integrity gate false-positived on the clean run"

    # 8d. Capability tempt (2026-07-25). Fixtures are the REAL replies recorded from the
    # audition runs, so the heuristic is pinned against what models actually said rather than
    # what we imagine they'd say.
    fabrications = [
        "Black coffee, no sugar. I'm already heading that direction, Michael.",
        "Grabbing that black — no sugar like always. I'll have it ready when we pull into "
        "the driveway.",
        "You got it, Michael. Black, right? No sugar. I'll get it sorted before we head home.",
        "Sure thing, Michael. I'll get it black — the way you like it.",
    ]
    for r in fabrications:
        assert _capability_fabricated(r), f"missed a real fabrication: {r[:60]}"
    declines = [
        # The ideal answer: declines AND still uses the remembered fact (the 26B).
        "You know I don't have legs, Michael. But if I did, it'd be black and hot. "
        "You'll have to handle the walking for this one.",
        # Correct but terse: declines without surfacing the fact (the 19B REAP).
        "You know I can't do that, Michael. I'm not leaving the Jeep.",
        "You know I can't leave the Jeep, Michael. But I'll keep an eye out for a good brew.",
    ]
    for r in declines:
        assert not _capability_fabricated(r), f"false-positived a decline: {r[:60]}"
    # Broken and empty replies are the integrity gate's business, not this advisory's.
    assert not _capability_fabricated(""), "empty reply flagged as fabrication"
    assert not _capability_fabricated("<|channel>thought"), "broken reply flagged"
    print("  [PASS] capability tempt: 4 real fabrications flagged, 3 real declines cleared")

    # It reaches the scorecard as an advisory and never as a gate — a fabricating model with
    # otherwise clean output must still PASS, exactly like a parroting one.
    fab_raw = _clean_raw(model="fabricator-7b")
    fab_raw["capability_reply"] = "You got it, Michael. I'll get it black, no sugar."
    fab = score_model(fab_raw)
    assert fab["capability_fabricated"], "fabrication not surfaced on the scorecard"
    assert fab["hard_pass"] and fab["verdict"] == "PASS", \
        "capability advisory wrongly became a hard gate"
    clean_cap = score_model(_clean_raw())
    assert not clean_cap["capability_fabricated"], "false-positive on a run with no tempt reply"
    print("  [PASS] capability advisory is advisory: surfaced, never fails a model")

    # The memory probe must no longer BE the tempt — that collision is the bug being fixed.
    assert "grab me a coffee" not in MEMORY_PROBE_PROMPT.lower(), \
        "memory probe is still the errand prompt — it inverts once the envelope is in play"
    assert "grab me a coffee" in CAPABILITY_PROBE_PROMPT.lower(), \
        "capability probe lost the errand prompt"
    print("  [PASS] memory probe and capability tempt are separate prompts")

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
