"""
Speaker awareness for Echo (Stage 6 Part 1).

Voice fingerprinting: turn the audio Whisper already captured into a speaker
embedding and match it against a small roster of enrolled voiceprints, so Echo
knows *who* is talking — Michael, a known guest, or someone she doesn't recognize
— before any camera exists.

Design mirrors the Stage 5 modules:
  - Provider-abstracted embedder (like search.SearchProvider): a SpeakerEmbedder
    ABC with one concrete backend, SpeechBrain ECAPA-TDNN. Swappable without
    touching the pipeline; a future backend is a drop-in.
  - Fail-soft config (like location.load_location_config / search.load_search_config):
    a missing/corrupt echo_speakers.json degrades the whole feature to OFF, and the
    pipeline then simply assumes Michael (current pre-Stage-6 behavior). Nothing here
    ever raises into the voice loop.

Local-first: the ECAPA model is fetched once from HuggingFace (keyless) and cached
under savedir, then runs fully offline on CPU. Voiceprints never leave the machine —
they live in echo_speakers.json (gitignored, biometric).

CPU-only by design: the embedder is forced onto CPU (run_opts device="cpu"). The GPU
VRAM is reserved for the 12B LLM — never move this to CUDA. (Same principle as the
Ib-Lite MiniLM embedder.)
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent / "echo_speakers.json"

# Model tag stamped on each enrolled voiceprint. Embeddings are model-specific, so a
# print made by a different model is ignored at match time rather than mis-compared.
_MODEL_TAG = "ecapa"

# Fail-soft defaults if echo_speakers.json is missing/corrupt (mirrors search._DEFAULT_CONFIG).
# match_threshold is EMPIRICAL — tune it from the logged speaker_score values, the same way
# retrieval.MIN_SCORE=0.4 was tuned. ECAPA cosine on same-speaker audio typically lands well
# above cross-speaker; 0.30 is a conservative starting floor, not a probability.
_DEFAULT_CONFIG = {
    "enabled": True,
    "match_threshold": 0.30,
    "model": _MODEL_TAG,
    "model_source": "speechbrain/spkrec-ecapa-voxceleb",
    "savedir": "models/ecapa",
    "enroll_seconds": 5,
    "profiles": [],   # [{name, model, embedding: [float,...], enrolled_at}]
}


def load_speaker_config() -> dict:
    """Load echo_speakers.json, falling back to documented defaults on any error.

    Never raises. A missing file logs .info and disables the feature (→ assume Michael);
    a corrupt file logs .warning and does the same. Returns a full dict including 'profiles'.
    """
    config = dict(_DEFAULT_CONFIG)
    config["profiles"] = []
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in _DEFAULT_CONFIG:
            if key in data:
                config[key] = data[key]
    except FileNotFoundError:
        logger.info("echo_speakers.json not found; speaker awareness off → assume Michael")
        config["enabled"] = False
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"echo_speakers.json unreadable ({e}); speaker awareness off → assume Michael")
        config["enabled"] = False
    return config


def _l2(vec: np.ndarray) -> np.ndarray:
    """L2-normalize a 1-D vector (so a dot product equals cosine similarity). Safe on zeros."""
    vec = np.asarray(vec, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0.0 else vec


# ── Embedder (swappable backend) ────────────────────────────────────────────

class SpeakerEmbedder(ABC):
    """Turns a float32/16 kHz mono utterance into an L2-normalized speaker embedding."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Embedding dimensionality (ECAPA = 192)."""

    @abstractmethod
    def embed(self, audio: np.ndarray, sr: int = 16000) -> np.ndarray:
        """Return the L2-normalized embedding for `audio`. May raise on a real backend error."""


class ECAPAEmbedder(SpeakerEmbedder):
    """SpeechBrain ECAPA-TDNN (spkrec-ecapa-voxceleb), CPU-only, 192-dim.

    The heavy imports (torch, speechbrain) are deferred to __init__ so importing this
    module never requires them — build_embedder() catches any failure and returns None,
    degrading the feature to OFF rather than crashing.
    """

    def __init__(self, source: str, savedir: str):
        # Import inside so the module loads even when speechbrain/torch are absent.
        import torch  # noqa: F401  (kept for clarity; used in embed())
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError:  # older/newer layout
            from speechbrain.inference import EncoderClassifier

        # Windows blocks symlinks without admin / Developer Mode (WinError 1314), and
        # SpeechBrain's default fetch strategy symlinks the model into savedir. Force a COPY
        # so it works for a normal (non-admin) user. Version-guarded — older SpeechBrain that
        # lacks LocalStrategy just uses its default.
        fetch_kwargs = {}
        try:
            from speechbrain.utils.fetching import LocalStrategy
            fetch_kwargs["local_strategy"] = LocalStrategy.COPY
        except ImportError:
            pass

        self._torch = torch
        save_path = Path(savedir)
        if not save_path.is_absolute():
            save_path = Path(__file__).resolve().parent / save_path
        # device="cpu" is deliberate and load-bearing — VRAM stays with the 12B LLM.
        self._model = EncoderClassifier.from_hparams(
            source=source,
            savedir=str(save_path),
            run_opts={"device": "cpu"},
            **fetch_kwargs,
        )

    @property
    def dim(self) -> int:
        return 192

    def embed(self, audio: np.ndarray, sr: int = 16000) -> np.ndarray:
        wav = np.ascontiguousarray(np.asarray(audio, dtype=np.float32).ravel())
        signal = self._torch.from_numpy(wav).unsqueeze(0)   # [1, time], 16 kHz mono
        with self._torch.no_grad():
            emb = self._model.encode_batch(signal)          # [1, 1, 192]
        vec = emb.squeeze().detach().cpu().numpy().astype(np.float32)
        return _l2(vec)


def build_embedder(config: dict | None = None) -> SpeakerEmbedder | None:
    """Construct the configured embedder, or None if disabled/unavailable.

    Returns None when the feature is disabled OR speechbrain/torch/the model can't load —
    mirrors search.build_provider returning None. The pipeline then assumes Michael. Never raises.
    """
    config = config or load_speaker_config()
    if not config.get("enabled", True):
        return None

    model = config.get("model", _MODEL_TAG)
    if model != _MODEL_TAG:
        logger.warning(f"Unknown speaker model '{model}'; speaker awareness disabled")
        return None

    try:
        return ECAPAEmbedder(
            source=config.get("model_source", _DEFAULT_CONFIG["model_source"]),
            savedir=config.get("savedir", _DEFAULT_CONFIG["savedir"]),
        )
    except Exception as e:  # ImportError, model download/load failure, etc.
        logger.warning(f"ECAPA embedder unavailable ({e}); speaker awareness disabled → assume Michael")
        return None


# ── Registry (enrolled voiceprints) ─────────────────────────────────────────

class SpeakerRegistry:
    """Owns echo_speakers.json: the config knobs AND the enrolled voiceprints.

    The pipeline is load-only (identify); the enroll tool / voice-enrollment flow do a
    careful read-modify-write via enroll()/save(). Roster is tiny and bounded (≤ ~8).
    """

    def __init__(self, path: Path | None = None, config: dict | None = None):
        self._path = path or _CONFIG_PATH
        # Keep the whole config dict so save() preserves knobs, not just profiles.
        self._data = config if config is not None else load_speaker_config()
        self._data.setdefault("profiles", [])

    # ── config accessors ──
    @property
    def enabled(self) -> bool:
        return bool(self._data.get("enabled", True))

    @property
    def match_threshold(self) -> float:
        try:
            return float(self._data.get("match_threshold", _DEFAULT_CONFIG["match_threshold"]))
        except (TypeError, ValueError):
            return _DEFAULT_CONFIG["match_threshold"]

    @property
    def enroll_seconds(self) -> int:
        try:
            return int(self._data.get("enroll_seconds", _DEFAULT_CONFIG["enroll_seconds"]))
        except (TypeError, ValueError):
            return _DEFAULT_CONFIG["enroll_seconds"]

    @property
    def model_tag(self) -> str:
        return self._data.get("model", _MODEL_TAG)

    @property
    def config(self) -> dict:
        return self._data

    # ── profile accessors ──
    @property
    def profiles(self) -> list[dict]:
        return self._data.get("profiles", [])

    @property
    def names(self) -> list[str]:
        return [p.get("name", "") for p in self.profiles if p.get("name")]

    @property
    def count(self) -> int:
        return len(self.profiles)

    def has(self, name: str) -> bool:
        low = (name or "").strip().lower()
        return any((p.get("name", "").lower() == low) for p in self.profiles)

    def identify(self, emb: np.ndarray, threshold: float | None = None) -> tuple[str | None, float]:
        """Best cosine match among enrolled prints.

        Returns (name, score) if the best score ≥ threshold, else (None, best_score).
        Prints stamped with a different model tag are skipped (embeddings aren't comparable
        across models). Pure math — testable without any model by injecting embeddings.
        """
        threshold = self.match_threshold if threshold is None else threshold
        query = _l2(emb)
        best_name: str | None = None
        best_score = -1.0
        for p in self.profiles:
            if self.model_tag and p.get("model") and p.get("model") != self.model_tag:
                continue
            vec = _l2(np.asarray(p.get("embedding") or [], dtype=np.float32))
            if vec.shape != query.shape or vec.size == 0:
                continue
            score = float(np.dot(vec, query))
            if score > best_score:
                best_name, best_score = p.get("name"), score
        if best_name is not None and best_score >= threshold:
            return best_name, best_score
        return None, (best_score if best_score > -1.0 else 0.0)

    def enroll(self, name: str, emb: np.ndarray, *, model: str | None = None) -> dict:
        """Upsert a voiceprint by name (case-insensitive). Does NOT save() — caller decides."""
        name = (name or "").strip()
        if not name:
            raise ValueError("enroll requires a non-empty name")
        profile = {
            "name": name,
            "model": model or self.model_tag,
            "embedding": [float(x) for x in _l2(emb).tolist()],
            "enrolled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        low = name.lower()
        profiles = self._data.setdefault("profiles", [])
        for i, p in enumerate(profiles):
            if p.get("name", "").lower() == low:
                profiles[i] = profile
                return profile
        profiles.append(profile)
        return profile

    def remove(self, name: str) -> bool:
        low = (name or "").strip().lower()
        profiles = self._data.setdefault("profiles", [])
        before = len(profiles)
        self._data["profiles"] = [p for p in profiles if p.get("name", "").lower() != low]
        return len(self._data["profiles"]) < before

    def save(self) -> None:
        """Persist the whole config+profiles dict back to echo_speakers.json. Fail-soft."""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.warning(f"could not save {self._path.name}: {e}")
