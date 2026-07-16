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

from ib_lite.significance import reject_reason


def _fact(entity, attribute, value="x"):
    return {"save": True, "type": "fact", "entity": entity, "attribute": attribute, "value": value}


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


if __name__ == "__main__":
    run()
    print()
