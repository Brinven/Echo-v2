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
  Streaming (2026-08-27): given a queue, the sink ALSO pushes each chunk the moment it is
  enqueued, so the phone hears the filler while the search runs and the first sentence
  before the rest is generated — instead of one WAV after the whole turn.
"""

import io
import time
import base64
import queue
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 16000  # what the pipeline (Whisper/ECAPA) expects; matches audio.SAMPLE_RATE

# ── Visual input (Level 1): photo sniff + save ───────────────────────────
# The client already downscaled to a ~1600px JPEG (a few hundred KB); 10 MB is a runaway
# guard against an un-downscaled original, not a target. Module const so tests can patch it.
IMAGE_MAX_BYTES = 10 * 1024 * 1024

# logs/photos/ at the repo root (logs/ is already gitignored wholesale).
_PHOTO_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "photos"

_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def sniff_image_mime(data: bytes) -> str | None:
    """Identify an image by magic bytes alone — JPEG/PNG/WebP, no PIL, no new dep.

    None = not an image we accept. Deliberately narrow: these are the three formats a
    phone's canvas/library hand over; anything else is a mis-upload, not a use case.
    """
    if not data:
        return None
    for magic, mime in _IMAGE_MAGIC:
        if data.startswith(magic):
            return mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def save_photo(data: bytes, mime: str, session_id: str,
               photo_dir: Path | None = None) -> str | None:
    """Write an uploaded photo to logs/photos/ and return its repo-relative pointer.

    Fail-soft: any OSError → None — a failed save must never block the turn (the photo
    still rides to the model from memory). session_id already carries the date, so the
    filename only adds a time-of-day; a same-second collision gets a numeric suffix.
    photo_dir=None → _PHOTO_DIR, resolved at CALL time so tests can patch the module
    attribute and never write into the real logs/photos/.
    """
    try:
        photo_dir = photo_dir or _PHOTO_DIR
        photo_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{session_id}_{time.strftime('%H-%M-%S')}"
        ext = _EXT.get(mime, ".bin")
        path = photo_dir / f"{stem}{ext}"
        n = 1
        while path.exists():
            path = photo_dir / f"{stem}_{n}{ext}"
            n += 1
        path.write_bytes(data)
        return f"logs/photos/{path.name}"
    except OSError:
        return None


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


def _pcm16_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    """Encode float32 mono samples as a 16-bit PCM WAV — the shape the phone decodes."""
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class RemoteAudioSink:
    """Drop-in for AudioQueue that collects chunks instead of playing them.

    The pipeline calls start() → enqueue()× → finish() (and run_signoff adds wait_done());
    all are cheap and none block. is_playing mirrors AudioQueue's property shape.

    Streaming (2026-08-27): with a `stream_q`, every enqueue() ALSO pushes
    ("audio", {"wav_b64", "sample_rate"}) — that one chunk as its own small WAV — so the
    request thread can hand it to the phone while the pipeline is still generating. The
    chunks are still collected, so wav_bytes()/sink_to_b64() keep working (the non-streaming
    shape, the sign-off goodbye). Without a queue the sink is byte-identical to before.
    The ("done", result) sentinel is control.finish_remote_turn's job, never the sink's.
    """

    def __init__(self, stream_q: "queue.Queue | None" = None):
        self._chunks: list[tuple[np.ndarray, int]] = []
        self._stream_q = stream_q

    def start(self) -> None:
        pass

    def enqueue(self, audio: np.ndarray, sample_rate: int) -> None:
        a = np.asarray(audio, dtype=np.float32)
        if a.ndim > 1:
            a = a.mean(axis=1)
        sr = int(sample_rate)
        self._chunks.append((a, sr))
        if self._stream_q is not None:
            self._stream_q.put(("audio", {
                "wav_b64": base64.b64encode(_pcm16_wav(a, sr)).decode("ascii"),
                "sample_rate": sr,
            }))

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
        return _pcm16_wav(np.concatenate(parts), target_sr), target_sr


def sink_to_b64(sink: RemoteAudioSink) -> tuple[str, int] | None:
    """The sink's WAV as (base64 string, sample_rate) for the JSON response. None if empty."""
    wav = sink.wav_bytes()
    if wav is None:
        return None
    return base64.b64encode(wav[0]).decode("ascii"), wav[1]
