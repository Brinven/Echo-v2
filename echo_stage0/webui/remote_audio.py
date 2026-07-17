"""Remote Voice (Level 2): phone-audio decode + the collecting audio sink.

Two pieces, both pure and offline-testable:

- decode_to_pcm16k() turns whatever the phone's MediaRecorder produced (iOS records
  mp4/AAC, Android webm/Opus, tests feed wav) into the 16 kHz mono float32 ndarray the
  pipeline expects. Decoding rides on PyAV, which faster-whisper already ships — no new
  dependency. Fail-soft: any undecodable input returns None, never raises.

- RemoteAudioSink quacks like audio_queue.AudioQueue but COLLECTS synthesized chunks
  instead of playing them. Passing it as `audio_q` IS the remote mode: the pipeline runs
  unchanged (search filler included — it simply rides at the front of the reply), the PC
  speakers stay silent, and sink_to_b64() hands the reply back for the phone to play.
  finish()/wait_done() are non-blocking no-ops — nothing plays, so nothing is waited on.
"""

import io
import base64

import numpy as np
import soundfile as sf

SAMPLE_RATE = 16000  # what the pipeline (Whisper/ECAPA) expects; matches audio.SAMPLE_RATE


def decode_to_pcm16k(data: bytes) -> np.ndarray | None:
    """Decode arbitrary compressed audio bytes to 16 kHz mono float32 in [-1, 1].

    Container/codec-agnostic (wav, mp4/AAC, webm/Opus, ogg...) via PyAV's bundled ffmpeg.
    Returns None on empty/undecodable input or if PyAV is unavailable — the route turns
    that into a 400, it must never take the server down.
    """
    if not data:
        return None
    try:
        import av
        from av.audio.resampler import AudioResampler
    except ImportError:
        return None
    try:
        chunks: list[np.ndarray] = []
        with av.open(io.BytesIO(data)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                return None
            resampler = AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
            # Flush the resampler's tail — without this the last few hundred samples
            # (the end of the final word) are silently dropped.
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray().reshape(-1))
    except Exception:
        return None
    if not chunks:
        return None
    return np.concatenate(chunks).astype(np.float32) / 32768.0


class RemoteAudioSink:
    """Drop-in for AudioQueue that collects chunks instead of playing them.

    The pipeline calls start() → enqueue()× → finish() (and run_signoff adds wait_done());
    all are cheap and none block. is_playing mirrors AudioQueue's property shape.
    """

    def __init__(self):
        self._chunks: list[tuple[np.ndarray, int]] = []

    def start(self) -> None:
        pass

    def enqueue(self, audio: np.ndarray, sample_rate: int) -> None:
        a = np.asarray(audio, dtype=np.float32)
        if a.ndim > 1:
            a = a.mean(axis=1)
        self._chunks.append((a, int(sample_rate)))

    def finish(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def wait_done(self) -> None:
        pass

    @property
    def is_playing(self) -> bool:
        return False

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def wav_bytes(self) -> tuple[bytes, int] | None:
        """All collected chunks as one 16-bit PCM WAV. None if nothing was synthesized."""
        if not self._chunks:
            return None
        target_sr = self._chunks[0][1]
        parts: list[np.ndarray] = []
        for a, sr in self._chunks:
            if sr != target_sr and len(a) > 1:
                # Kokoro serves one rate per process, so this path is insurance, not routine.
                n = int(round(len(a) * target_sr / sr))
                a = np.interp(np.linspace(0.0, len(a) - 1.0, n),
                              np.arange(len(a)), a).astype(np.float32)
            parts.append(a)
        buf = io.BytesIO()
        sf.write(buf, np.concatenate(parts), target_sr, format="WAV", subtype="PCM_16")
        return buf.getvalue(), target_sr


def sink_to_b64(sink: RemoteAudioSink) -> tuple[str, int] | None:
    """The sink's WAV as (base64 string, sample_rate) for the JSON response. None if empty."""
    wav = sink.wav_bytes()
    if wav is None:
        return None
    return base64.b64encode(wav[0]).decode("ascii"), wav[1]
