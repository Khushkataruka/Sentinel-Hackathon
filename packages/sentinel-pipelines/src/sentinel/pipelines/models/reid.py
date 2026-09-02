"""Appearance embedding.

A separate model with a separate training objective from the description
model, and that separation is the point. One model producing both is trained
to give two white Swifts the same description, which pushes their embeddings
together -- the exact opposite of what re-identification needs. So: two
models, two passes, two objectives.

The output dimension recorded in model_versions and the vector(512) column
declaration must agree. A mismatch is a silent corruption rather than an
error, which is why embed_model_id sits on every row: a bad vector is at
least traceable to the run that produced it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.pipelines.models.stubbase import rng_for

log = get_logger(__name__)

#: Below this, a crop carries too few pixels for an appearance vector to mean
#: anything. Per resolution class, from the survey.
MIN_CROP_PIXELS = {"full": 64 * 64, "reduced": 40 * 40, "thumbnail": 24 * 24}


class ReIDEncoder(Protocol):
    is_stub: bool
    dim: int

    def __call__(self, crop: np.ndarray) -> list[float]: ...


def usable(crop: np.ndarray, resolution_class: str) -> bool:
    h, w = crop.shape[:2]
    return h * w >= MIN_CROP_PIXELS.get(resolution_class, MIN_CROP_PIXELS["thumbnail"])


class StubReID:
    """Deterministic pseudo-embedding, L2-normalised.

    It is seeded from a downsampled copy of the crop, so visually similar
    crops of the SAME track land close together and anything else does not.
    That is enough to exercise the HNSW index, the cosine metric and the
    scorer. It is not re-identification and will not match one vehicle across
    two cameras.
    """

    is_stub = True

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.embedding_dim
        log.warning(
            "stub_reid",
            dim=self.dim,
            reason="no re-id weights; cross-camera matching will not work",
            expected_at=str(settings.reid_model_path),
        )

    def __call__(self, crop: np.ndarray) -> list[float]:
        import cv2

        thumb = cv2.resize(crop, (16, 16), interpolation=cv2.INTER_AREA)
        base = thumb.astype(np.float32).reshape(-1) / 255.0
        rng = rng_for(crop)
        # Project the thumbnail through a fixed random matrix, then jitter
        # deterministically. Same crop -> same vector, always.
        projection = np.random.default_rng(12345).standard_normal((base.size, self.dim))
        vector = base @ projection + rng.standard_normal(self.dim) * 0.01
        norm = float(np.linalg.norm(vector))
        # Normalised, so cosine distance is the comparison and the index
        # agrees with the scorer.
        return (vector / norm).astype(np.float32).tolist() if norm else vector.tolist()


class OnnxReID:
    """Real encoder over ONNX Runtime."""

    is_stub = False

    def __init__(self, model_path: Path, dim: int | None = None) -> None:
        import onnxruntime as ort

        self.session = ort.InferenceSession(
            str(model_path), providers=settings.onnx_providers
        )
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        self.height = int(shape[2]) if isinstance(shape[2], int) else 256
        self.width = int(shape[3]) if isinstance(shape[3], int) else 128
        self.dim = dim or settings.embedding_dim
        log.info("reid_loaded", path=str(model_path), input=(self.height, self.width))

    def __call__(self, crop: np.ndarray) -> list[float]:
        import cv2

        resized = cv2.resize(crop, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
        blob = resized[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 3, 1, 1)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 3, 1, 1)
        blob = (blob - mean) / std

        features = np.squeeze(self.session.run(None, {self.input_name: blob})[0])
        if features.ndim > 1:
            features = features.reshape(-1)
        if features.size != self.dim:
            raise ValueError(
                f"model returned {features.size} dims, expected {self.dim}; "
                "model_versions.output_dim and the vector column must agree"
            )
        norm = float(np.linalg.norm(features))
        return (features / norm).tolist() if norm else features.tolist()


def load_reid() -> ReIDEncoder:
    path = settings.reid_model_path
    if Path(path).exists():
        try:
            return OnnxReID(Path(path))
        except Exception as exc:
            log.error("reid_load_failed", path=str(path), error=str(exc))
    return StubReID()
