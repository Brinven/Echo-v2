"""
Validation gate for Ib-Lite writes.

The significance gate (a 12B LLM call) is imprecise — it sometimes emits the
wrong shape. This module is the deterministic Python check that catches missing
or malformed fields before anything hits SQLite. On failure the caller re-prompts
the model once with the error message; a second failure is logged and discarded.
"""

REQUIRED_FIELDS = {
    "fact":       {"entity", "attribute", "value"},
    "preference": {"key", "value"},
    "policy":     {"key", "rule", "priority"},
}


def validate_write(payload: dict) -> tuple[bool, str]:
    """Returns (valid, error_message). On failure, caller retries once."""
    if not isinstance(payload, dict):
        return False, "payload is not an object"

    ptype = payload.get("type")
    if ptype not in REQUIRED_FIELDS:
        return False, f"Unknown type: {ptype!r} (expected one of {sorted(REQUIRED_FIELDS)})"

    missing = REQUIRED_FIELDS[ptype] - set(payload.keys())
    if missing:
        return False, f"Missing fields for {ptype}: {sorted(missing)}"

    # Every required field must be a non-empty string (priority excepted).
    for field in REQUIRED_FIELDS[ptype]:
        if field == "priority":
            continue
        val = payload.get(field)
        if not isinstance(val, str) or not val.strip():
            return False, f"Field {field!r} must be a non-empty string"

    if ptype == "policy":
        p = payload.get("priority")
        if not isinstance(p, int) or isinstance(p, bool) or not (1 <= p <= 10):
            return False, f"priority must be an int 1-10, got: {p!r}"

    return True, ""
