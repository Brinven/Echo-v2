"""
Offline tests for the significance-gate hardening (2026-07-16).

The tightened GATE_SYSTEM prompt and the `searched` hint change the MODEL's behavior (verified
live), but `reject_reason` is a deterministic backstop that must catch the noise classes even if
the model ignores the prompt — so it's the part worth pinning here, model-free.

Run:  python test_significance.py     (exit 0 = all assertions passed)
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from ib_lite.significance import GATE_SYSTEM, reject_reason, _build_user_content


def _fact(entity, attribute, value="x"):
    return {"save": True, "type": "fact", "entity": entity, "attribute": attribute, "value": value}


def run_user_content() -> None:
    """The gate's user message is assembled by a pure function (Stage 6 Phase 2 seam)."""
    print("\n── Significance gate: _build_user_content (offline) ──")

    turn = "Michael: my sister Anna is allergic to cats\nEcho: Noted."

    # Michael (default / explicit / any casing) → NO speaker line: the solo path's gate
    # prompt is byte-identical to pre-Phase-2.
    base = _build_user_content(turn)
    assert base == f"Turn transcript:\n{turn}", "baseline prompt changed"
    assert _build_user_content(turn, speaker="Michael") == base
    assert _build_user_content(turn, speaker="michael") == base
    assert _build_user_content(turn, speaker="") == base, "empty speaker must fall back to baseline"
    print("  [PASS] Michael/empty speaker → prompt byte-identical to pre-Phase-2")

    # A guest → the speaker line names them and pins the pronoun resolution.
    guest = _build_user_content("Hillary: I have a headache\nEcho: Rest.", speaker="Hillary")
    assert "The person speaking in this turn is Hillary" in guest
    assert 'resolve "I", "my", "me" to Hillary' in guest
    print("  [PASS] guest speaker line present, pronouns pinned to the speaker")

    # Composes with searched and correction — all three stack in order.
    full = _build_user_content(turn, searched=True, correction="Missing fields", speaker="Jon")
    assert full.index("Turn transcript:") < full.index("person speaking in this turn is Jon")
    assert full.index("is Jon") < full.index("web lookup") < full.index("Missing fields")
    print("  [PASS] speaker + searched + correction compose in order")

    # The searched hint is speaker-neutral now (a guest's search turn must not re-anchor
    # the gate onto Michael).
    assert "the speaker stated" in _build_user_content(turn, searched=True)
    print("  [PASS] searched hint is speaker-neutral")


def run() -> None:
    print("\n── Significance gate: reject_reason (offline) ──")

    # Durable facts pass (reject_reason returns None).
    for e, a, v in [("Michael", "favorite_bird", "crows"),
                    ("Michael", "location", "Magnolia, Texas"),
                    ("Jeep", "needs", "new shocks"),
                    ("Hillary", "profession", "RN"),
                    ("Jon", "hobby", "hiking")]:
        assert reject_reason(_fact(e, a, v)) is None, f"durable fact wrongly rejected: {e}/{a}"
    print("  [PASS] durable facts pass the net")

    # Self / meta entities are dropped — identity lives in persona.py, never in memory.
    for e in ["Echo", "echo", "memory_system", "the memory system", "the system", "ib-lite", "assistant"]:
        assert reject_reason(_fact(e, "trait", "whatever")) is not None, f"self/meta entity allowed: {e}"
    print("  [PASS] self/meta entities dropped (Echo, memory_system, the system, …)")

    # Ephemeral attributes are dropped — anything current_*, or a bare status/state/mood.
    for a in ["current_task", "current_action", "current_city", "current_project", "current_state",
              "status", "state", "mood", "activity"]:
        assert reject_reason(_fact("Michael", a, "whatever")) is not None, f"ephemeral attr allowed: {a}"
    print("  [PASS] ephemeral attributes dropped (current_*, status, state, mood, …)")

    # The exact noise the live DB had accumulated — every one must be caught.
    live_noise = [
        _fact("Echo", "personality_trait", "values being useful over merely present"),
        _fact("Echo", "goal", "provide clarity without unnecessary noise"),
        _fact("memory_system", "status", "upgraded with better long-term memory"),
        _fact("Michael", "current_task", "testing different image models"),
        _fact("Michael", "current_action", "testing system clarity"),
        _fact("Michael's location", "current_city", "Magnolia, Texas"),
        _fact("homestead", "current_state", "quiet"),
    ]
    for p in live_noise:
        assert reject_reason(p) is not None, f"live noise slipped through: {p['entity']}/{p['attribute']}"
    print("  [PASS] all 7 real accumulated-noise facts are caught")

    # Preferences and policies pass through untouched (keyed + intentional, not gate-invented facts).
    assert reject_reason({"save": True, "type": "preference", "key": "coffee", "value": "black"}) is None
    assert reject_reason({"save": True, "type": "policy", "key": "x", "rule": "y", "priority": 5}) is None
    assert reject_reason({"save": False}) is None
    assert reject_reason("not a dict") is None
    print("  [PASS] preferences / policies / non-saves pass through")

    print("  OFFLINE: all significance-gate checks passed.")


def run_anchor_guidance() -> None:
    """The entity-anchoring guidance (2026-07-17) must stay in GATE_SYSTEM.

    The Willie case: photo-turn facts saved a goat's personality with no record that he IS a
    goat — a bare name that gets ambiguous as the cast of people/pets/things grows. The
    guidance itself is prompt-only (behavior verified live); this pins its presence so a
    future prompt edit can't silently drop it.
    """
    print("\n── Significance gate: entity-anchoring guidance present (offline) ──")

    assert "say WHAT they are" in GATE_SYSTEM, "anchoring guidance dropped from GATE_SYSTEM"
    assert "species" in GATE_SYSTEM and "relation to Michael" in GATE_SYSTEM
    assert 'value="a goat; likes to tip things over"' in GATE_SYSTEM, "worked example dropped"
    print("  [PASS] anchoring guidance + worked example present in GATE_SYSTEM")


if __name__ == "__main__":
    run()
    run_user_content()
    run_anchor_guidance()
    print()
