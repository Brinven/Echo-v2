"""
Audio recording and playback for Echo Stage 0.

Uses sounddevice for both capture and playback.
Records at 16kHz mono (Whisper's native format).
"""

import sys
import threading

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    print(
        "\n ERROR: sounddevice not installed.\n"
        "  pip install sounddevice\n"
    )
    sys.exit(1)


SAMPLE_RATE = 16000
CHANNELS = 1


class AudioRecorder:
    """Callback-based audio recorder using sounddevice."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self._sample_rate = sample_rate
        self._buffer: list[np.ndarray] = []
        self._stream = None
        self._recording = False

    def start(self):
        """Start recording audio from the default input device."""
        self._buffer = []
        self._recording = True
        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=CHANNELS,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        """
        Stop recording and return the captured audio.

        Returns:
            float32 numpy array, 16kHz mono
        """
        self._recording = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._buffer:
            return np.array([], dtype="float32")

        audio = np.concatenate(self._buffer)
        # Flatten to 1D mono
        if audio.ndim > 1:
            audio = audio[:, 0]
        return audio

    def _callback(self, indata, frames, time_info, status):
        """sounddevice stream callback — appends audio chunks to buffer."""
        if status:
            print(f"  [audio warning] {status}")
        if self._recording:
            self._buffer.append(indata.copy())

    @property
    def sample_rate(self) -> int:
        return self._sample_rate


def play_audio(audio: np.ndarray, sample_rate: int):
    """
    Play audio through the default output device. Blocks until complete.

    Args:
        audio: float32 numpy array
        sample_rate: Sample rate in Hz
    """
    done = threading.Event()

    def _finished_callback():
        done.set()

    sd.play(audio, samplerate=sample_rate)
    sd.wait()


def list_devices():
    """Print available audio devices for troubleshooting."""
    print("\n  Available audio devices:")
    print(sd.query_devices())
