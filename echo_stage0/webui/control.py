"""
EchoControl — the bridge between the voice loop and the web dashboard (Stage 7).

The dashboard is a second input/output surface over the state the voice loop already
exposes. This object holds references to the live session/state and the SAME
threading.Events the keyboard handler (main.on_key) sets, and exposes:
  - reads  (snapshot / health / recent_scores) for the dashboard to display, and
  - writes (talk / mute / snark / location / websearch / enroll / threshold / quit) that
    do exactly what a keypress does — set a flag or an Event the main loop already polls.

It deliberately touches NOTHING in the STT/LLM/TTS pipeline. Every mutation is a single
attribute assignment or an Event.set() — GIL-atomic, the same pattern the project already
relies on for cross-thread flags (e.g. session.persona_correction). No new locks.
"""

import json
import time
from pathlib import Path

import httpx

# logs/stage0_log.jsonl lives at the repo root (see logger.LOG_FILE).
_LOG_FILE = Path(__file__).resolve().parent.parent.parent / "logs" / "stage0_log.jsonl"

# Health-probe targets (Windows: 127.0.0.1, never localhost — see tasks/lessons.md).
_DEFAULT_LM_URL = "http://127.0.0.1:1234/v1/models"
_DEFAULT_KOKORO_URL = "http://127.0.0.1:8880/health"
_HEALTH_CACHE_S = 5.0


class EchoControl:
    """Read live Echo state and drive it through the same flags the keyboard uses."""

    def __init__(
        self,
        session,
        sm,
        registry,
        *,
        space_pressed,
        space_released,
        mute_toggle_event,
        quit_event,
        model_name: str = "",
        speaker_active: bool = False,
        lm_studio_url: str = _DEFAULT_LM_URL,
        kokoro_url: str = _DEFAULT_KOKORO_URL,
    ):
        self.session = session
        self.sm = sm
        self.registry = registry                 # SpeakerRegistry or None
        self._space_pressed = space_pressed
        self._space_released = space_released
        self._mute_toggle = mute_toggle_event
        self._quit = quit_event
        self._model_name = model_name
        self.speaker_active = speaker_active      # True only if the ECAPA embedder loaded
        self._lm_url = lm_studio_url
        self._kokoro_url = kokoro_url

        # Mute lives here now so keyboard AND web share one source of truth.
        self._muted = False
        self._health_cache: dict | None = None
        self._health_ts = 0.0

    # ── mute (shared source of truth) ──
    @property
    def muted(self) -> bool:
        return self._muted

    def toggle_mute(self) -> bool:
        self._muted = not self._muted
        self._mute_toggle.set()
        return self._muted

    # ── PTT (press-and-hold Talk button) ──
    def talk_press(self) -> None:
        self._space_pressed.set()

    def talk_release(self) -> None:
        self._space_released.set()

    # ── session toggles (mirror on_key / the voice overrides) ──
    def set_snark(self, level: int) -> int:
        level = max(0, min(10, int(level)))
        self.session.daily_snark = level
        self.session.max_snark = False           # choosing a level exits Max Snark
        return level

    def toggle_max_snark(self) -> bool:
        self.session.max_snark = not self.session.max_snark
        return self.session.max_snark

    def set_location(self, loc: str) -> str | None:
        if loc in ("home", "jeep"):
            self.session.location = loc
            return loc
        return None

    def set_web_search(self, off: bool) -> bool:
        self.session.web_search_off = bool(off)
        return self.session.web_search_off

    def request_quit(self) -> None:
        self._quit.set()

    # ── speaker controls ──
    def start_enroll(self, name: str) -> bool:
        """Arm enrollment: the next spoken utterance becomes this person's voiceprint.

        No-op (returns False) if speaker awareness isn't active — otherwise session.enrolling
        would get stuck (the capture guard needs the embedder).
        """
        name = (name or "").strip()
        if not name or not self.speaker_active:
            return False
        self.session.enrolling = name[:1].upper() + name[1:]
        return True

    def cancel_enroll(self) -> None:
        self.session.enrolling = None

    def set_threshold(self, value: float) -> float | None:
        """Update the speaker match threshold LIVE (identify() reads it per call) and persist."""
        if self.registry is None:
            return None
        try:
            v = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return None
        self.registry.config["match_threshold"] = v
        self.registry.save()
        return v

    # ── reads for the dashboard ──
    def snapshot(self) -> dict:
        s = self.session
        turns = [{"speaker": t.get("speaker"), "content": t.get("content", "")}
                 for t in s.turns[-12:]]
        return {
            "state": self.sm.label,
            "muted": self._muted,
            "model": self._model_name,
            "user_name": s.user_name,
            "current_speaker": s.current_speaker,
            "last_speaker_score": round(getattr(s, "last_speaker_score", 0.0), 3),
            "snark": s.effective_snark,
            "max_snark": s.max_snark,
            "location": s.location,
            "web_search_off": s.web_search_off,
            "enrolling": s.enrolling,
            "turn_count": s.turn_count,
            "exchange_count": s.exchange_count,
            "transcript": turns,
            "speaker_active": self.speaker_active,
            "enrolled": self.registry.names if self.registry else [],
            "match_threshold": self.registry.match_threshold if self.registry else None,
        }

    def health(self) -> dict:
        """LM Studio + Kokoro reachability (cached ~5s so polling doesn't hammer them)."""
        now = time.monotonic()
        if self._health_cache is not None and (now - self._health_ts) < _HEALTH_CACHE_S:
            return self._health_cache
        result = {
            "lm_studio": self._reachable(self._lm_url),
            "kokoro": self._reachable(self._kokoro_url),
            "model": self._model_name,
        }
        self._health_cache = result
        self._health_ts = now
        return result

    @staticmethod
    def _reachable(url: str) -> bool:
        """Any HTTP response means the service is up (a 404 still proves it's listening)."""
        try:
            httpx.get(url, timeout=1.0)
            return True
        except (httpx.HTTPError, OSError):
            return False

    def recent_scores(self, n: int = 15) -> list[dict]:
        """Last n speaker_score rows from the JSONL log — feeds threshold tuning."""
        try:
            lines = _LOG_FILE.read_text(encoding="utf-8").splitlines()[-300:]
        except OSError:
            return []
        out: list[dict] = []
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "speaker_score" in rec:
                out.append({
                    "speaker": rec.get("speaker"),
                    "score": rec.get("speaker_score"),
                    "known": rec.get("speaker_known"),
                })
            if len(out) >= n:
                break
        return list(reversed(out))
