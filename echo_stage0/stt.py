"""
STT wrapper for Echo Stage 0.

Auto-detects faster-whisper (preferred) or openai-whisper.
CUDA-accelerated on RTX 5080.
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


class STTEngine:
    """Speech-to-text engine with auto-detection of available backends."""

    def __init__(self, model_size: str = "base"):
        if _BACKEND is None:
            print(
                "\n ERROR: No STT engine found.\n"
                "  Install one of the following:\n"
                "    pip install faster-whisper   (recommended — fastest on CUDA)\n"
                "    pip install openai-whisper\n"
            )
            sys.exit(1)

        self._backend = _BACKEND
        self._model_size = model_size
        self._model = None
        self._load_model()

    def _load_model(self):
        """Load the STT model with CUDA if available."""
        if self._backend == "faster-whisper":
            try:
                self._model = _FasterWhisperModel(
                    self._model_size,
                    device="cuda",
                    compute_type="float16",
                )
                self._device = "cuda"
            except Exception:
                # Fall back to CPU if CUDA fails
                self._model = _FasterWhisperModel(
                    self._model_size,
                    device="cpu",
                    compute_type="int8",
                )
                self._device = "cpu"

        elif self._backend == "openai-whisper":
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = _openai_whisper.load_model(self._model_size, device=device)
            self._device = device

        print(f"  STT: {self._backend} ({self._model_size}) on {self._device}")

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
