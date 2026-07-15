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


# In-conversation voice enrollment (Stage 6 Part 1): "Echo, this is Jon" starts capture;
# the NEXT utterance's audio becomes Jon's voiceprint. Deliberately narrow — requires
# "echo" + an introduction verb + a name token. A word/length guard (below) keeps it from
# firing on ordinary sentences like "Echo, this is important because...".
_ENROLL_PATTERNS = [
    re.compile(r"\becho\b,?\s+this\s+is\s+([a-z][a-z'\-]{1,20})\b", re.IGNORECASE),
    # Possessive form: keep the apostrophe OUT of the name class so "Jon's" captures "Jon".
    re.compile(r"\becho\b,?\s+remember\s+([a-z][a-z\-]{1,20})'?s\s+voice\b", re.IGNORECASE),
]
# Tokens that match the name slot but are never a person being introduced.
_ENROLL_STOPWORDS = {
    "me", "here", "back", "home", "there", "him", "her", "them", "us", "it",
    "that", "this", "important", "done", "everything", "all", "my", "our", "a", "the",
}
# Cancel an in-progress enrollment capture.
_ENROLL_CANCEL_PATTERN = re.compile(r"\b(?:cancel|never\s*mind|stop|forget\s+it)\b", re.IGNORECASE)


def is_enroll_command(transcript: str) -> str | None:
    """Return the name to enroll if the transcript is an 'Echo, this is <name>' command.

    Guarded against false positives: the utterance must be short (an introduction, not a
    sentence that happens to contain 'this is'), and the captured token must not be a
    common non-name word. Returns the name Title-Cased, or None.
    """
    if len((transcript or "").split()) > 8:
        return None
    for pat in _ENROLL_PATTERNS:
        m = pat.search(transcript)
        if m:
            name = m.group(1).strip()
            if name.lower() in _ENROLL_STOPWORDS:
                return None
            return name[:1].upper() + name[1:]
    return None


def is_enroll_cancel(transcript: str) -> bool:
    """True if the transcript cancels an in-progress enrollment capture."""
    return bool(_ENROLL_CANCEL_PATTERN.search(transcript))


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
        # vad_enabled: hands-free listening for THIS session. Defaults from location
        # (see vad_default_for_location) and is re-applied whenever location changes; the
        # dashboard toggle overrides it at any time. The engine's availability is separate —
        # vad.available is False without webrtcvad, and no flag can turn that on.
        self.vad_enabled = False
        # current_speaker: who is talking THIS turn (speaker_id voice fingerprint). Defaults
        # to Michael (the pre-Stage-6 assumption, preserved when speaker awareness is off);
        # recomputed each real turn when active. "unknown" when a voice matches no profile.
        self.current_speaker = user_name or "Michael"
        # enrolling: a pending in-conversation enrollment. "Echo, this is Jon" sets the name;
        # the NEXT utterance's audio is captured as Jon's voiceprint. None when not enrolling.
        self.enrolling: str | None = None
        # last_speaker_score: cosine score of the most recent speaker identification, surfaced
        # in the GUI for threshold tuning. Set each real turn alongside current_speaker.
        self.last_speaker_score = 0.0
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

    @property
    def current_speaker_is_michael(self) -> bool:
        """True if the current speaker is Michael (the owner) — gates the memory write.

        When speaker awareness is off, current_speaker stays Michael, so this is True and
        memory behaves exactly as before Stage 6.
        """
        owner = (self._user_name or "Michael").strip().lower()
        return (self.current_speaker or "").strip().lower() == owner

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


def vad_default_for_location(location: str) -> bool:
    """Should hands-free listening be ON for this location? (Stage 8)

    home    -> True   : desk/downtime, quiet, hands often busy — this is the point of VAD.
    jeep    -> False  : road noise, radio and passengers would trigger it constantly; the
                        Talk button stays deliberate while driving.
    unknown -> True   : follows the Stage 5 Part 5 rule that `unknown` fails to NEUTRAL/home
                        behavior, never jeep. Being hands-free when uncertain is the neutral
                        default; it is also recoverable in one tap, unlike a wrong jeep guess.

    Michael can override any of this from the dashboard at any time.
    """
    return location != "jeep"
