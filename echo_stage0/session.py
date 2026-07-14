"""
Session management for Echo Stage 2.

Tracks conversation turns, detects sign-off phrase, computes metrics,
and writes session + summary files to ./sessions/.
"""

import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path


SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"

# Sign-off pattern: "echo" must appear, then "that's/thats all for now"
_SIGNOFF_PATTERN = re.compile(
    r"\becho\b.*\bthat'?s\s+all\s+for\s+now\b",
    re.IGNORECASE,
)

# Forget pattern: a clear imperative to drop the last thing remembered.
# Kept narrow to avoid firing on ordinary conversation.
_FORGET_PATTERNS = [
    re.compile(r"\bforget\s+(that|what\s+i\s+just\s+(said|told\s+you)|what\s+you\s+just\s+(saved|stored))\b", re.IGNORECASE),
    re.compile(r"\bscratch\s+that\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+remember\s+that\b", re.IGNORECASE),
]

# Maximum Snark Mode trigger (PRD §2b). "Echo, maximum snark mode" and close variants.
# Requires both "max(imum)" and "snark" so it can't fire on ordinary talk about snark.
_MAX_SNARK_PATTERN = re.compile(r"\bmax(?:imum)?\s+snark\b", re.IGNORECASE)

# Web-search off/on switch (Stage 5 Part 3 §6 NTH, session-scoped). Requires the explicit
# "offline"/"online" keyword so it can't fire on ordinary conversation.
_STAY_OFFLINE_PATTERN = re.compile(r"\b(?:stay|go|get)\s+offline\b", re.IGNORECASE)
_GO_ONLINE_PATTERN = re.compile(r"\b(?:go\s+(?:back\s+)?online|back\s+online)\b", re.IGNORECASE)

# Location voice override (Stage 5 Part 5 §6, session-scoped). Deliberately specific
# phrases so they can't fire on ordinary talk about the jeep or home ("I drove the jeep
# home" matches neither). Jeep checked first.
_LOC_JEEP_PATTERN = re.compile(
    r"\b(?:we'?re\s+in\s+the\s+jeep|(?:get|hop)\s+in\s+the\s+jeep|jeep\s+mode|in\s+the\s+jeep\s+now)\b",
    re.IGNORECASE,
)
_LOC_HOME_PATTERN = re.compile(
    r"\b(?:we'?re\s+(?:at\s+)?home|we'?re\s+in\s+the\s+house|back\s+home|home\s+mode)\b",
    re.IGNORECASE,
)


def is_signoff(transcript: str) -> bool:
    """Check if the transcript contains the sign-off phrase."""
    return bool(_SIGNOFF_PATTERN.search(transcript))


def is_forget(transcript: str) -> bool:
    """Check if the transcript is a 'forget that' correction command."""
    return any(p.search(transcript) for p in _FORGET_PATTERNS)


def is_max_snark(transcript: str) -> bool:
    """Check if the transcript triggers Maximum Snark Mode."""
    return bool(_MAX_SNARK_PATTERN.search(transcript))


def is_stay_offline(transcript: str) -> bool:
    """Check if the transcript asks Echo to stop searching the web this session."""
    return bool(_STAY_OFFLINE_PATTERN.search(transcript))


def is_go_online(transcript: str) -> bool:
    """Check if the transcript asks Echo to resume web search this session."""
    return bool(_GO_ONLINE_PATTERN.search(transcript))


def is_location_override(transcript: str) -> str | None:
    """Return 'jeep'/'home' if the transcript forces a location, else None."""
    if _LOC_JEEP_PATTERN.search(transcript):
        return "jeep"
    if _LOC_HOME_PATTERN.search(transcript):
        return "home"
    return None


class Session:
    """Manages a single conversation session."""

    def __init__(self, model: str, stt_backend: str, tts_backend: str, user_name: str | None = None):
        now = datetime.now()
        self._session_id = now.strftime("%Y-%m-%d_%H-%M-%S")
        self._started_at = now.isoformat(timespec="seconds")
        self._model = model
        self._stt_backend = stt_backend
        self._tts_backend = tts_backend
        self._user_name = user_name or ""
        self._turns: list[dict] = []
        self._turn_counter = 0

        # ── Personality state (Stage 5 Part 2) ──
        # exchange_count is distinct from _turn_counter: it counts full user→Echo
        # exchanges (1 per round trip), used for anti-drift anchor timing. _turn_counter
        # counts speaker-turns (2 per exchange) and is used for logging/metrics.
        # Per-process, so both reset at session end automatically.
        self._exchange_count = 0
        # daily_snark is set once at session start (by main from daily_state); max_snark
        # is the in-session Maximum Snark Mode lock. Effective level is computed per turn.
        self.daily_snark = 5
        self.max_snark = False
        # mood_opener: a resolved opening-tone nudge (from the prior session's mood),
        # set once at session start and applied only on the first exchange.
        self.mood_opener = ""
        # web_search_off: session-scoped kill switch for web search (voice "stay offline").
        # The global enable lives in echo_search.json; this is the in-session override.
        self.web_search_off = False
        # location: 'home'/'jeep'/'unknown', resolved once at session start (like snark)
        # from the local network fingerprint; the voice override can flip it mid-session.
        self.location = "unknown"
        # persona_correction: an on-demand self-check nudge (persona_check.py) injected into
        # the NEXT turn's system prompt, then consumed (one-turn decay). Set on the probe's
        # background thread, read+cleared on the main thread. A plain string swap is atomic
        # under the GIL, so no lock — a lost/stale nudge is harmless (re-detected next probe).
        self.persona_correction = ""

        SESSIONS_DIR.mkdir(exist_ok=True)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def user_name(self) -> str:
        return self._user_name

    @property
    def turn_count(self) -> int:
        return self._turn_counter

    @property
    def has_turns(self) -> bool:
        return self._turn_counter > 0

    @property
    def exchange_count(self) -> int:
        """Number of full user→Echo exchanges so far this session (anti-drift timing)."""
        return self._exchange_count

    def increment_exchange(self) -> int:
        """Advance the exchange counter for a real turn. Returns the new value.

        Call this once per genuine exchange, AFTER the too-short/no-speech/sign-off/
        forget/max-snark guards (those return before a real exchange and must not
        advance the counter). The returned 1-based value is what build_system_prompt
        checks for the anchor (8, 16, 24, ...).
        """
        self._exchange_count += 1
        return self._exchange_count

    @property
    def effective_snark(self) -> int:
        """Current snark level: locked to 10 in Maximum Snark Mode, else the daily roll."""
        return 10 if self.max_snark else self.daily_snark

    def set_persona_correction(self, nudge: str) -> None:
        """Queue a self-check nudge for the next turn (called from the probe thread)."""
        self.persona_correction = (nudge or "").strip()

    def consume_persona_correction(self) -> str:
        """Return the queued nudge and clear it (one-turn decay). Called on the main thread."""
        nudge = self.persona_correction
        self.persona_correction = ""
        return nudge

    @property
    def turns(self) -> list[dict]:
        return self._turns

    def add_user_turn(self, content: str, stt_latency_s: float) -> dict:
        """Record a user turn."""
        self._turn_counter += 1
        turn = {
            "turn_id": self._turn_counter,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "speaker": "user",
            "content": content,
            "stt_latency_s": round(stt_latency_s, 4),
            "first_audio_s": None,
        }
        self._turns.append(turn)
        return turn

    def add_echo_turn(self, content: str, first_audio_s: float) -> dict:
        """Record an Echo turn."""
        self._turn_counter += 1
        turn = {
            "turn_id": self._turn_counter,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "speaker": "echo",
            "content": content,
            "stt_latency_s": None,
            "first_audio_s": round(first_audio_s, 4),
        }
        self._turns.append(turn)
        return turn

    def compute_metrics(self) -> dict:
        """Compute avg STT, first-audio, and TTFT across the session."""
        stt_vals = [t["stt_latency_s"] for t in self._turns if t["stt_latency_s"] is not None]
        fa_vals = [t["first_audio_s"] for t in self._turns if t["first_audio_s"] is not None]

        return {
            "stt_avg_s": round(statistics.mean(stt_vals), 4) if stt_vals else 0.0,
            "first_audio_avg_s": round(statistics.mean(fa_vals), 4) if fa_vals else 0.0,
        }

    def get_conversation_text(self) -> str:
        """Format all turns as readable text for the summary LLM pass."""
        lines = []
        for turn in self._turns:
            speaker = "User" if turn["speaker"] == "user" else "Echo"
            lines.append(f"{speaker}: {turn['content']}")
        return "\n".join(lines)

    def save_session_file(self) -> Path:
        """Write the full session log to disk. Returns the file path."""
        data = {
            "session_id": self._session_id,
            "started_at": self._started_at,
            "ended_at": datetime.now().isoformat(timespec="seconds"),
            "user_name": self._user_name,
            "model": self._model,
            "stt_backend": self._stt_backend,
            "tts_backend": self._tts_backend,
            "turn_count": self._turn_counter,
            "metrics": self.compute_metrics(),
            "turns": self._turns,
        }

        filepath = SESSIONS_DIR / f"session_{self._session_id}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return filepath

    def save_summary_file(self, summary: dict) -> Path:
        """Write the session summary to disk. Returns the file path."""
        data = {
            "session_id": self._session_id,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "user_name": self._user_name,
            **summary,
        }

        filepath = SESSIONS_DIR / f"summary_{self._session_id}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return filepath


def load_config() -> dict:
    """Load user config from config.json. Creates default if missing."""
    config_path = Path(__file__).resolve().parent / "config.json"

    default = {
        "user_name": "Michael",
        "user_pronouns": "he/him",
    }

    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults for any missing keys
            for key, val in default.items():
                data.setdefault(key, val)
            return data
        except (json.JSONDecodeError, OSError):
            pass

    # Write default config
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(default, f, indent=2)
    return default


def save_config(config: dict) -> None:
    """Persist the config dict back to config.json (used to remember the last-picked model)."""
    config_path = Path(__file__).resolve().parent / "config.json"
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except OSError as e:
        # Non-fatal: a failed save just means we won't remember the choice next time.
        print(f"  [Could not save config.json: {e}]")
