"""
Ordered audio playback queue for Echo.

TTS chunks are enqueued as they're synthesized. A background thread
plays them sequentially so audio output is seamless while LLM
streaming and TTS continue in parallel.
"""

import threading
import queue

import numpy as np
import sounddevice as sd


# Sentinel value to signal the player thread to stop
_STOP = None


class AudioQueue:
    """Thread-safe ordered audio playback queue."""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._playing = threading.Event()
        self._done = threading.Event()
        self._sample_rate: int | None = None

    def start(self):
        """Start the playback thread. Call before enqueuing audio."""
        self._done.clear()
        self._playing.set()
        self._thread = threading.Thread(target=self._player_loop, daemon=True)
        self._thread.start()

    def enqueue(self, audio: np.ndarray, sample_rate: int):
        """Add an audio chunk to the playback queue."""
        self._sample_rate = sample_rate
        self._queue.put((audio, sample_rate))

    def finish(self):
        """Signal that no more audio will be enqueued. Blocks until all audio plays."""
        self._queue.put(_STOP)
        if self._thread is not None:
            self._thread.join()
        self._done.set()

    def stop(self):
        """Immediately stop playback and clear the queue."""
        self._playing.clear()
        # Drain the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._done.set()

    def wait_done(self):
        """Block until all queued audio has finished playing."""
        self._done.wait()

    @property
    def is_playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _player_loop(self):
        """Background thread that plays audio chunks in order."""
        while self._playing.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if item is _STOP:
                break

            audio, sample_rate = item
            try:
                sd.play(audio, samplerate=sample_rate)
                sd.wait()
            except Exception as e:
                print(f"  [audio playback error: {e}]")
