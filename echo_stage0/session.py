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


def is_signoff(transcript: str) -> bool:
    """Check if the transcript contains the sign-off phrase."""
    return bool(_SIGNOFF_PATTERN.search(transcript))


def is_forget(transcript: str) -> bool:
    """Check if the transcript is a 'forget that' correction command."""
    return any(p.search(transcript) for p in _FORGET_PATTERNS)


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
