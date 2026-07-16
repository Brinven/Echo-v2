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

import gpu
from session import vad_default_for_location

# logs/stage0_log.jsonl lives at the repo root (see logger.LOG_FILE).
_LOG_FILE = Path(__file__).resolve().parent.parent.parent / "logs" / "stage0_log.jsonl"

# Health-probe targets (Windows: 127.0.0.1, never localhost — see tasks/lessons.md).
_DEFAULT_LM_URL = "http://127.0.0.1:1234/v1/models"
_DEFAULT_KOKORO_URL = "http://127.0.0.1:8880/health"
_HEALTH_CACHE_S = 5.0
_MODELS_CACHE_S = 10.0


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
        vad_available: bool = False,
        list_models=None,
        list_voices=None,
        model_state=None,
        voice_name: str = "",
        memory_db_path=None,
        lm_studio_url: str = _DEFAULT_LM_URL,
        kokoro_url: str = _DEFAULT_KOKORO_URL,
    ):
        # Memory-page DB. None → the real echo.db (db.DB_PATH); tests inject a temp path so a
        # memory edit/delete route never mutates Michael's production memory. The web thread
        # opens its OWN connection to this per request — it never touches IbLite._conn.
        self.memory_db_path = memory_db_path
        self.session = session
        self.sm = sm
        self.registry = registry                 # SpeakerRegistry or None
        self._space_pressed = space_pressed
        self._space_released = space_released
        self._mute_toggle = mute_toggle_event
        self._quit = quit_event
        self._model_name = model_name
        self.speaker_active = speaker_active      # True only if the ECAPA embedder loaded
        self.vad_available = vad_available        # True only if webrtcvad imported
        # Read-only listers, NOT the LLMClient/TTSEngine: the dashboard must never mutate the
        # pipeline. A chosen model/voice is parked in pending_* and the MAIN LOOP applies it
        # between turns (main.do_model_swap / do_voice_swap), so a swap can never land
        # mid-generation — which for the voice would mean changing voice mid-sentence.
        self._list_models = list_models
        self._list_voices = list_voices
        self._model_state = model_state      # read-only probe, like the listers
        self.pending_model: str | None = None
        self.pending_voice: str | None = None
        self.pending_preview: str | None = None
        self._voice_name = voice_name
        self._models_cache: list[str] = []
        self._models_ts = 0.0
        self._voices_cache: list[str] = []
        self._voices_ts = 0.0
        self._lm_url = lm_studio_url
        self._kokoro_url = kokoro_url

        # Mute lives here so every control surface shares one source of truth.
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
            # Hands-free follows the room: re-apply the location default on every change, so
            # "we're in the Jeep" also stops VAD listening to road noise. Michael can still
            # toggle it back explicitly afterwards.
            self.session.vad_enabled = vad_default_for_location(loc)
            return loc
        return None

    def set_vad(self, enabled: bool) -> bool:
        """Explicit hands-free override. No-op unless the engine actually loaded."""
        if not self.vad_available:
            return False
        self.session.vad_enabled = bool(enabled)
        return self.session.vad_enabled

    def toggle_vad(self) -> bool:
        if not self.vad_available:
            return False
        return self.set_vad(not self.session.vad_enabled)

    # ── model swap (parked for the main loop; never applied here) ──
    def models(self) -> list[str]:
        """Live model ids from LM Studio, cached ~10s (the dropdown may be opened repeatedly)."""
        if self._list_models is None:
            return []
        now = time.monotonic()
        if self._models_cache and (now - self._models_ts) < _MODELS_CACHE_S:
            return self._models_cache
        try:
            self._models_cache = list(self._list_models() or [])
            self._models_ts = now
        except Exception:
            return self._models_cache
        return self._models_cache

    def request_model(self, name: str) -> bool:
        """Park a model swap for the main loop. Rejects anything LM Studio doesn't list."""
        name = (name or "").strip()
        if not name or name not in self.models():
            return False
        self.pending_model = name
        return True

    def take_pending_model(self) -> str | None:
        """Main loop only: claim the pending swap (read + clear)."""
        name = self.pending_model
        self.pending_model = None
        return name

    def set_model_name(self, name: str) -> None:
        """Main loop only: report the model actually in use, after a completed swap."""
        self._model_name = name

    # ── Kokoro voice (same park-for-the-main-loop pattern as the model) ──
    def voices(self) -> list[str]:
        """Voice ids Kokoro can serve, cached ~10s. Empty if TTS/Kokoro is unreachable."""
        if self._list_voices is None:
            return []
        now = time.monotonic()
        if self._voices_cache and (now - self._voices_ts) < _MODELS_CACHE_S:
            return self._voices_cache
        try:
            self._voices_cache = list(self._list_voices() or [])
            self._voices_ts = now
        except Exception:
            return self._voices_cache
        return self._voices_cache

    def request_voice(self, name: str) -> bool:
        """Park a voice change for the main loop. Rejects anything Kokoro doesn't serve."""
        name = (name or "").strip()
        if not name or name not in self.voices():
            return False
        self.pending_voice = name
        return True

    def take_pending_voice(self) -> str | None:
        """Main loop only: claim the pending voice change (read + clear)."""
        name = self.pending_voice
        self.pending_voice = None
        return name

    def set_voice_name(self, name: str) -> None:
        """Main loop only: report the voice actually in use, after a completed change."""
        self._voice_name = name

    def request_preview(self, name: str) -> bool:
        """Park a voice PREVIEW for the main loop (speaks a fixed sample line in `name`).

        Parked rather than synthesized here for the same reason as everything else: the web thread
        must not touch the pipeline, and playing audio from it could collide with a live reply or
        be heard by the open mic. The main loop plays it while idle, with the mic paused.
        """
        name = (name or "").strip()
        if not name or name not in self.voices():
            return False
        self.pending_preview = name
        return True

    def take_pending_preview(self) -> str | None:
        """Main loop only: claim the pending preview (read + clear)."""
        name = self.pending_preview
        self.pending_preview = None
        return name

    def set_web_search(self, off: bool) -> bool:
        self.session.web_search_off = bool(off)
        return self.session.web_search_off

    def request_quit(self) -> None:
        self._quit.set()

    # ── speaker controls ──
    def start_enroll(self, name: str, ignore: bool = False) -> bool:
        """Arm enrollment: the next spoken utterance becomes this voice's print.

        `ignore=True` enrolls it as furniture — a voice Echo recognizes and never answers
        (Michael's Kokoro clock, a TV). Arm-and-wait is the right shape for those: a clock
        only speaks on the half hour, so a CLI that records on demand can't catch it.

        No-op (returns False) if speaker awareness isn't active — otherwise session.enrolling
        would get stuck (the capture guard needs the embedder).
        """
        name = (name or "").strip()
        if not name or not self.speaker_active:
            return False
        # Order matters: the capture guard reads `enrolling` to fire, so set the flag it will
        # read FIRST. Two plain attribute writes, GIL-atomic, no lock — the house pattern.
        self.session.enrolling_ignore = bool(ignore)
        self.session.enrolling = name[:1].upper() + name[1:]
        return True

    def cancel_enroll(self) -> None:
        self.session.enrolling = None
        self.session.enrolling_ignore = False

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
        # speaker_name is per-turn (who actually spoke); `speaker` is the role. The UI needs both:
        # it used to label every user turn with the LIVE current_speaker, so the moment a guest
        # spoke, every earlier line silently re-labelled to them — the readout lied about history.
        turns = [{"speaker": t.get("speaker"), "name": t.get("speaker_name"),
                  "content": t.get("content", "")}
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
            "vad_available": self.vad_available,
            "vad_enabled": bool(self.vad_available and s.vad_enabled),
            "pending_model": self.pending_model,
            "voice": self._voice_name,
            "pending_voice": self.pending_voice,
            "enrolling": s.enrolling,
            "enrolling_ignore": bool(s.enrolling_ignore),
            "turn_count": s.turn_count,
            "exchange_count": s.exchange_count,
            "transcript": turns,
            "speaker_active": self.speaker_active,
            # active_names, not names: the chips list PEOPLE. Ignored voices are reported
            # separately so the panel can show them muted rather than as guests.
            "enrolled": self.registry.active_names if self.registry else [],
            "ignored": self.registry.ignored_names if self.registry else [],
            "match_threshold": self.registry.match_threshold if self.registry else None,
        }

    def health(self) -> dict:
        """LM Studio + Kokoro reachability + VRAM (cached ~5s so polling doesn't hammer them).

        VRAM is here because this machine usually has something else on the GPU (Invoke, or a model
        left loaded). Echo's model JIT-loads on the first request, so a full card fails at the first
        thing Michael says — showing the numbers lets him SEE it instead of debugging a lie.
        """
        now = time.monotonic()
        if self._health_cache is not None and (now - self._health_ts) < _HEALTH_CACHE_S:
            return self._health_cache
        vram = gpu.vram_usage()
        result = {
            "lm_studio": self._reachable(self._lm_url),
            "kokoro": self._reachable(self._kokoro_url),
            "model": self._model_name,
            "model_state": self._model_state() if self._model_state else "unknown",
            "vram_used_mb": vram[0] if vram else None,
            "vram_total_mb": vram[1] if vram else None,
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

    def history(self, *, q=None, speaker=None, limit=None) -> dict:
        """Grouped conversation history for the /history page, read from the same JSONL log.

        Read-only. Imported lazily so this module stays importable without the history helper
        (and keeps the 'importing webui is cheap' property the package docstring promises).
        """
        from . import history as _history
        return _history.read_history(_LOG_FILE, q=q, speaker=speaker, limit=limit)
