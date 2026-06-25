"""
Embedding wrapper for Ib-Lite.

all-MiniLM-L6-v2: 22MB, 384-dim, CPU-only. It must never touch the GPU —
that VRAM belongs to the 12B inference model. Embeddings are stored as raw
little-endian float32 bytes so sqlite-vec's vec_distance_cosine reads them
directly.

The model is a module-level singleton, loaded once on first encode() (~1-2s),
then reused for the life of the process.
"""

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"
DIM = 384

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME, device="cpu")
    return _model


def encode(text: str) -> bytes:
    """Encode text to normalized float32 bytes (384 dims)."""
    vec = _get_model().encode(text, normalize_embeddings=True)
    return np.asarray(vec, dtype=np.float32).tobytes()


def decode(blob: bytes) -> np.ndarray:
    """Inverse of encode(): bytes -> float32 ndarray."""
    return np.frombuffer(blob, dtype=np.float32)
