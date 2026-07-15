"""
TTS wrapper for Echo.

Connects to Kokoro-FastAPI at localhost:8880 via OpenAI-compatible API.
Returns audio as a numpy array ready for playback.

Keep-warm: a background thread pings Kokoro every few seconds with a
tiny synthesis to prevent the model from going cold (~2.4s cold vs ~0.1s warm).
"""

import io
import sys
import time
import threading

import numpy as np
import soundfile as sf
import requests
from openai import OpenAI, APIConnectionError


KOKORO_URL = "http://127.0.0.1:8880/v1"
VOICE = "af_heart"          # default only — the live voice is per-instance (self._voice)
KEEPALIVE_INTERVAL_S = 8  # ping every 8 seconds


class TTSEngine:
    """Text-to-speech engine using Kokoro-FastAPI with keep-warm.

    The voice is per-instance and swappable at runtime (Stage 8.1): Michael picks it from the
    dashboard so he can audition Kokoro's ~50 voices without editing code. It is passed in from
    config.json (`voice`) so the choice survives a restart; VOICE above is just the fallback.
    """

    def __init__(self, voice: str | None = None):
        self._client = OpenAI(
            base_url=KOKORO_URL,
            api_key="not-needed",
        )
        self._voice = voice or VOICE
        self._last_call = 0.0
        self._lock = threading.Lock()
        self._keepalive_thread = None
        self._running = False
        self._verify_and_warm()
        self._start_keepalive()

    @property
    def voice(self) -> str:
        return self._voice

    def set_voice(self, name: str) -> None:
        """Swap the voice in place. Called ONLY from the main loop, between turns — a swap
        mid-reply would change voice mid-sentence (synthesis is chunk-by-chunk)."""
        self._voice = name

    def list_voices(self) -> list[str]:
        """Voice ids Kokoro can serve (empty list on any error — never raises)."""
        try:
            resp = requests.get("http://127.0.0.1:8880/v1/audio/voices", timeout=3)
            resp.raise_for_status()
            data = resp.json()
            return sorted(data.get("voices", []))
        except (requests.RequestException, ValueError):
            return []

    def _verify_and_warm(self):
        """Check server is reachable and warm up the model."""
        try:
            resp = requests.get("http://127.0.0.1:8880/health", timeout=5)
            resp.raise_for_status()
        except (requests.ConnectionError, requests.Timeout):
            print(
                "\n ERROR: Kokoro-FastAPI not detected at localhost:8880.\n"
                "  Please start Kokoro-FastAPI (run start-kokoro.bat).\n"
            )
            sys.exit(1)

        print(f"  TTS: kokoro-fastapi (voice: {self._voice}) -- warming up...", end="", flush=True)
        self._do_warmup()
        print(" done")

    def _do_warmup(self):
        """Fire a tiny synthesis to keep the model warm."""
        try:
            self._client.audio.speech.create(
                model="kokoro", voice=self._voice, input=".", response_format="wav",
            )
            self._last_call = time.monotonic()
        except Exception:
            pass  # non-fatal, will retry on next keepalive

    def _start_keepalive(self):
        """Start background thread that keeps Kokoro warm."""
        self._running = True
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()

    def _keepalive_loop(self):
        """Background loop: ping Kokoro if no real call happened recently."""
        while self._running:
            time.sleep(2)
            elapsed = time.monotonic() - self._last_call
            if elapsed >= KEEPALIVE_INTERVAL_S:
                with self._lock:
                    self._do_warmup()

    def synthesize(self, text: str, voice: str | None = None) -> tuple[np.ndarray, int]:
        """
        Synthesize text to audio.

        Args:
            text: Text to speak
            voice: One-off voice override (the dashboard's Preview button). Does NOT change the
                active voice — a preview must not become a commitment.

        Returns:
            Tuple of (audio_array as float32, sample_rate)
        """
        with self._lock:
            try:
                response = self._client.audio.speech.create(
                    model="kokoro",
                    voice=voice or self._voice,
                    input=text,
                    response_format="wav",
                    speed=1.15,
                )
            except APIConnectionError:
                raise ConnectionError(
                    "Lost connection to Kokoro-FastAPI during TTS. "
                    "Check that the server is still running."
                )
            self._last_call = time.monotonic()

        # Parse WAV bytes into numpy array
        audio_bytes = response.content
        audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")

        # Ensure mono
        if audio_data.ndim > 1:
            audio_data = audio_data.mean(axis=1)

        return audio_data, sample_rate

    def shutdown(self):
        """Stop the keepalive thread."""
        self._running = False

    @property
    def backend(self) -> str:
        return "kokoro-fastapi"
