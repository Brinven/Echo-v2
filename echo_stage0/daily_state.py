"""
Daily snark state for Echo (Stage 5 Part 2).

Echo's snark level (0-10) varies by calendar day. A random level is rolled once
per day on the first session after midnight and persists across all sessions that
day (stored in echo_daily_state.json). Maximum Snark Mode (level 10 lock) is a
SESSION-level override handled in session.py — it does NOT touch this file.

The roll uses Python's runtime datetime/random (fine here — unlike workflow scripts,
the Echo runtime has full date/random access).
"""

import os
import json
import random
import logging
from pathlib import Path
from datetime import date

logger = logging.getLogger(__name__)

STATE_PATH = Path(__file__).resolve().parent / "echo_daily_state.json"

# Default level when the file is missing or corrupt (PRD §9 risk row).
DEFAULT_SNARK_LEVEL = 5
SNARK_MIN = 0
SNARK_MAX = 10


def _today_str(today: date | None = None) -> str:
    return (today or date.today()).isoformat()


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically (temp file + os.replace) so a crash can't corrupt it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def get_daily_snark_level(today: date | None = None, force_level: int | None = None) -> int:
    """Return today's snark level, rolling (and persisting) a new one on day change.

    Args:
        today: override "today" — test seam so harnesses are reproducible.
        force_level: pin the level (test seam); persisted like a normal roll.

    Behavior:
        - If echo_daily_state.json's stored date == today, return the stored level.
        - Otherwise roll a fresh random level (or force_level), write it, return it.
        - Missing/corrupt file → default to DEFAULT_SNARK_LEVEL, regenerate, log.
    """
    today_str = _today_str(today)

    if force_level is not None:
        level = max(SNARK_MIN, min(SNARK_MAX, int(force_level)))
        _atomic_write(STATE_PATH, {"date": today_str, "snark_level": level})
        return level

    # Try to read an existing, valid state for today.
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            stored_level = int(data["snark_level"])
            if data.get("date") == today_str and SNARK_MIN <= stored_level <= SNARK_MAX:
                return stored_level
        except (json.JSONDecodeError, OSError, KeyError, ValueError, TypeError) as e:
            logger.warning(f"echo_daily_state.json unreadable ({e}); defaulting to {DEFAULT_SNARK_LEVEL}")
            try:
                _atomic_write(STATE_PATH, {"date": today_str, "snark_level": DEFAULT_SNARK_LEVEL})
            except OSError:
                pass
            return DEFAULT_SNARK_LEVEL

    # New day (or no file yet): roll a fresh level and persist it.
    level = random.randint(SNARK_MIN, SNARK_MAX)
    try:
        _atomic_write(STATE_PATH, {"date": today_str, "snark_level": level})
    except OSError as e:
        logger.warning(f"could not persist daily snark state ({e}); using {level} for this session")
    logger.info(f"rolled new daily snark level: {level}")
    return level
