"""
STT wrapper for Echo Stage 0.

Auto-detects faster-whisper (preferred) or openai-whisper.
CUDA-accelerated on RTX 5080.

Production default: large-v3-turbo (CTranslate2 via faster-whisper). Override with
STTEngine(model_size=...) or, from main, config.json `stt_model` / ECHO_STT_MODEL.
"""

import sys
import numpy as np

# Auto-detect STT backend
_BACKEND = None

try:
    from faster_whisper import WhisperModel as _FasterWhisperModel
    _BACKEND = "faster-whisper"
except ImportError:
    pass

if _BACKEND is None:
    try:
        import whisper as _openai_whisper
        _BACKEND = "openai-whisper"
    except ImportError:
        pass

# Default for production Echo. base was too weak on proper nouns / casual speech
# (Maat 2026-07 STT brief). Turbo keeps CTranslate2 / torch-independent CUDA.
DEFAULT_MODEL_SIZE = "large-v3-turbo"

# Production compute type (2026-07-19): int8_float16 halves the CUDA weight footprint
# vs float16 (~1.7GB → ~0.9GB for turbo) with negligible accuracy cost — chosen to give
# the 27B headroom on the shared 16GB card, not for speed (STT was already 0.15-0.35s).
# CT2 converts at load from the same cached weights — no new download. Rollback:
# config.json "stt_compute": "float16" (or ECHO_STT_COMPUTE=float16) and restart.
DEFAULT_COMPUTE_TYPE = "int8_float16"

# openai-whisper does not ship large-v3-turbo; map to the closest stock model.
_OPENAI_SIZE_MAP = {
    "large-v3-turbo": "large-v3",
    "turbo": "large-v3",
    "distil-large-v3": "large-v3",
}


class STTEngine:
    """Speech-to-text engine with auto-detection of available backends."""

    def __init__(self, model_size: str = DEFAULT_MODEL_SIZE,
                 compute_type: str = DEFAULT_COMPUTE_TYPE):
        if _BACKEND is None:
            print(
                "\n ERROR: No STT engine found.\n"
                "  Install one of the following:\n"
                "    pip install faster-whisper   (recommended — fastest on CUDA)\n"
                "    pip install openai-whisper\n"
            )
            sys.exit(1)

        self._backend = _BACKEND
        self._model_size = (model_size or DEFAULT_MODEL_SIZE).strip() or DEFAULT_MODEL_SIZE
        self._compute = (compute_type or DEFAULT_COMPUTE_TYPE).strip() or DEFAULT_COMPUTE_TYPE
        self._model = None
        self._device = None
        self._load_model()

    def _load_model(self):
        """Load the STT model with CUDA if available.

        Device fallback (CUDA → CPU) is intentional. Model-id fallback is NOT —
        silently loading base after a turbo request would hide an accuracy regression.
        """
        if self._backend == "faster-whisper":
            try:
                self._model = _FasterWhisperModel(
                    self._model_size,
                    device="cuda",
                    compute_type=self._compute,
                )
                self._device = "cuda"
            except Exception as cuda_err:
                # Fall back to CPU if CUDA fails (OOM, driver, etc.)
                print(
                    f"  [STT] CUDA load failed for '{self._model_size}' ({cuda_err}); "
                    f"trying CPU int8 — latency will be much worse"
                )
                try:
                    self._model = _FasterWhisperModel(
                        self._model_size,
                        device="cpu",
                        compute_type="int8",
                    )
                    self._device = "cpu"
                    self._compute = "int8"  # report what actually loaded
                except Exception as cpu_err:
                    print(
                        f"\n ERROR: STT failed to load model '{self._model_size}'.\n"
                        f"  CUDA error: {cuda_err}\n"
                        f"  CPU error:  {cpu_err}\n"
                        f"  Check the model id (faster-whisper sizes include "
                        f"base, small, medium, large-v3, large-v3-turbo) or free VRAM.\n"
                    )
                    sys.exit(1)

        elif self._backend == "openai-whisper":
            import torch
            load_size = _OPENAI_SIZE_MAP.get(self._model_size, self._model_size)
            if load_size != self._model_size:
                print(
                    f"  [STT] openai-whisper has no '{self._model_size}'; "
                    f"loading '{load_size}' instead"
                )
            device = "cuda" if torch.cuda.is_available() else "cpu"
            try:
                self._model = _openai_whisper.load_model(load_size, device=device)
            except Exception as e:
                print(
                    f"\n ERROR: STT failed to load openai-whisper model "
                    f"'{load_size}' (requested '{self._model_size}'): {e}\n"
                )
                sys.exit(1)
            self._device = device

        # compute type only applies to CTranslate2; openai-whisper keeps the plain line.
        detail = (f"{self._model_size}, {self._compute}"
                  if self._backend == "faster-whisper" else self._model_size)
        print(f"  STT: {self._backend} ({detail}) on {self._device}")

    def transcribe(self, audio: np.ndarray) -> str:
        """
        Transcribe audio to text.

        Args:
            audio: float32 numpy array, 16kHz mono

        Returns:
            Transcribed text string
        """
        if self._backend == "faster-whisper":
            segments, _info = self._model.transcribe(
                audio,
                language="en",
                vad_filter=True,
            )
            text = " ".join(seg.text.strip() for seg in segments)

        elif self._backend == "openai-whisper":
            result = self._model.transcribe(
                audio,
                language="en",
                fp16=(self._device == "cuda"),
            )
            text = result["text"].strip()

        return text

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def model_size(self) -> str:
        return self._model_size

    @property
    def compute_type(self) -> str:
        return self._compute
