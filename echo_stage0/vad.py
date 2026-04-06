"""
Voice Activity Detection for Echo.

Uses webrtcvad (Google's WebRTC VAD) for lightweight, fast speech detection.
Falls back to PTT-only mode if webrtcvad is not installed.

webrtcvad requires 16-bit signed integer audio frames at 16kHz.
Frame sizes must be 10, 20, or 30ms (we use 30ms = 480 samples).
"""

import struct
import numpy as np

_HAS_VAD = False
try:
    import webrtcvad
    _HAS_VAD = True
except ImportError:
    pass


FRAME_DURATION_MS = 30
SAMPLE_RATE = 16000
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # 480 samples

# Speech onset: consecutive voiced frames needed to trigger
ONSET_FRAMES = 3
# Silence duration (ms) after speech to trigger end
SILENCE_DURATION_MS = 700
SILENCE_FRAMES = SILENCE_DURATION_MS // FRAME_DURATION_MS  # ~23 frames


class VADDetector:
    """
    Voice activity detector with webrtcvad.

    Processes audio frames and reports speech onset and offset.
    Falls back gracefully if webrtcvad is unavailable.
    """

    def __init__(self, aggressiveness: int = 2):
        self._available = _HAS_VAD
        self._vad = None
        self._aggressiveness = aggressiveness

        # State tracking
        self._voiced_count = 0
        self._silence_count = 0
        self._speech_active = False

        if self._available:
            self._vad = webrtcvad.Vad(aggressiveness)
            print(f"  VAD: webrtcvad (mode {aggressiveness})")
        else:
            print(
                "  VAD: unavailable -- running PTT-only mode.\n"
                "       Install webrtcvad for hands-free operation."
            )

    @property
    def available(self) -> bool:
        return self._available

    @property
    def speech_active(self) -> bool:
        return self._speech_active

    def process_frame(self, audio_frame: np.ndarray) -> str | None:
        """
        Process a single audio frame and return state transition if any.

        Args:
            audio_frame: float32 numpy array, exactly FRAME_SIZE samples (480)

        Returns:
            "speech_start" if speech onset detected,
            "speech_end" if silence after speech detected,
            None if no transition
        """
        if not self._available or self._vad is None:
            return None

        # Convert float32 [-1, 1] to int16 bytes for webrtcvad
        int16_data = (audio_frame * 32767).astype(np.int16)
        raw_bytes = int16_data.tobytes()

        is_speech = self._vad.is_speech(raw_bytes, SAMPLE_RATE)

        if is_speech:
            self._voiced_count += 1
            self._silence_count = 0

            if not self._speech_active and self._voiced_count >= ONSET_FRAMES:
                self._speech_active = True
                return "speech_start"
        else:
            self._silence_count += 1

            if self._speech_active and self._silence_count >= SILENCE_FRAMES:
                self._speech_active = False
                self._voiced_count = 0
                return "speech_end"

            # Decay voiced count during silence (but don't reset immediately)
            if not self._speech_active and self._silence_count > ONSET_FRAMES:
                self._voiced_count = 0

        return None

    def reset(self):
        """Reset detector state for a new listening period."""
        self._voiced_count = 0
        self._silence_count = 0
        self._speech_active = False
