"""Vehicle description: structured attributes plus a free-text caption.

Real implementation: a fine-tuned captioning model under constrained
generation, so the output parses into known fields instead of free prose we
then have to interpret.

No confidence output, deliberately. A captioning model can tell you how
likely the word "white" was. It cannot tell you the probability that the car
is white. Those are different quantities and only one of them belongs in a
police record. What carries uncertainty instead is the population count from
VAHAN, the camera's measured trust level, and the competing route count --
all measured, none of them a model's opinion of its own output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.pipelines.models.stubbase import dominant_colour_name, rng_for

log = get_logger(__name__)

VTYPES = ["hatchback", "sedan", "suv", "muv", "pickup", "van"]
MAKES = ["maruti", "hyundai", "tata", "mahindra", "toyota", "honda", "kia"]
MODELS = {
    "maruti": ["swift", "alto", "baleno", "dzire", "wagonr"],
    "hyundai": ["i20", "creta", "venue", "grand i10"],
    "tata": ["nexon", "punch", "tiago", "altroz"],
    "mahindra": ["scorpio", "bolero", "xuv700", "thar"],
    "toyota": ["innova", "fortuner", "glanza"],
    "honda": ["city", "amaze", "jazz"],
    "kia": ["seltos", "sonet", "carens"],
}
FEATURES = ["roof carrier", "bull bar", "tinted windows", "taxi livery",
            "damaged bumper", "roof rack", "commercial signage"]


@dataclass
class Description:
    colour: str | None = None
    vtype: str | None = None
    make: str | None = None
    model: str | None = None
    features: list[str] = field(default_factory=list)
    caption: str = ""

    def restrict_to(self, permitted: list[str]) -> Description:
        """Drop any field the camera's survey does not permit.

        A thumbnail-resolution camera does not get to claim a model name. The
        gate is applied here rather than trusted to the prompt, because the
        prompt is not enforceable and camera_profiles is.
        """
        allowed = set(permitted)
        return Description(
            colour=self.colour if "colour" in allowed else None,
            vtype=self.vtype if "type" in allowed or "vtype" in allowed else None,
            make=self.make if "make" in allowed else None,
            model=self.model if "model" in allowed else None,
            features=self.features if "features" in allowed else [],
            caption=self.caption,
        )


class Captioner(Protocol):
    is_stub: bool

    def __call__(self, crop: np.ndarray, vehicle_class: str) -> Description: ...


class StubCaptioner:
    """Deterministic placeholder.

    Colour is derived from the actual pixels so it varies sensibly; make and
    model are drawn from a seeded generator and are NOT a claim about the
    vehicle. Every caption is prefixed so no stub output can be mistaken for
    a real one in the database.
    """

    is_stub = True
    STUB_PREFIX = "[stub] "

    def __init__(self) -> None:
        log.warning(
            "stub_captioner",
            reason="no captioning weights; descriptions are placeholders",
            expected_at=str(settings.caption_model_path),
        )

    def __call__(self, crop: np.ndarray, vehicle_class: str) -> Description:
        rng = rng_for(crop)
        colour = dominant_colour_name(crop)

        if vehicle_class in {"motorcycle", "bicycle"}:
            vtype, make, model = vehicle_class, None, None
        elif vehicle_class in {"bus", "truck"}:
            vtype, make, model = vehicle_class, None, None
        else:
            vtype = VTYPES[int(rng.integers(len(VTYPES)))]
            make = MAKES[int(rng.integers(len(MAKES)))]
            model = MODELS[make][int(rng.integers(len(MODELS[make])))]

        features = (
            [FEATURES[int(rng.integers(len(FEATURES)))]] if rng.random() < 0.25 else []
        )
        parts = [p for p in (colour, make, model, vtype) if p]
        caption = self.STUB_PREFIX + " ".join(parts)
        if features:
            caption += " with " + ", ".join(features)

        return Description(colour, vtype, make, model, features, caption)


class OnnxCaptioner:
    """The real thing. Not implemented in the scaffold.

    Wiring note for whoever fills this in: constrained generation against a
    fixed prompt, parse into the fields above, and do NOT add a confidence
    number. See section 3.2 -- a missing number is safer than an invented
    one, because a missing number is visibly missing.
    """

    is_stub = False

    def __init__(self, model_path: Path) -> None:      # pragma: no cover
        raise NotImplementedError(
            "captioning model not wired; export to ONNX and implement __call__"
        )

    def __call__(self, crop: np.ndarray, vehicle_class: str) -> Description:  # pragma: no cover
        raise NotImplementedError


class CaptionEmbedder(Protocol):
    is_stub: bool
    dim: int

    def __call__(self, text: str) -> list[float]: ...


class StubCaptionEmbedder:
    """Hashed bag-of-words into a unit vector.

    Not semantic -- two paraphrases land far apart, which a real sentence
    encoder would not do. It is here so the vector column, its HNSW index and
    the query path are all exercised end to end before the real encoder
    arrives.
    """

    is_stub = True

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings.caption_embedding_dim
        log.warning("stub_caption_embedder", dim=self.dim,
                    reason="hashed bag-of-words, not semantic")

    def __call__(self, text: str) -> list[float]:
        vector = np.zeros(self.dim, dtype=np.float32)
        for token in text.lower().replace(",", " ").split():
            vector[hash(token) % self.dim] += 1.0
        norm = float(np.linalg.norm(vector))
        return (vector / norm).tolist() if norm else vector.tolist()


def load_captioner() -> Captioner:
    path = settings.caption_model_path
    if Path(path).exists():
        try:
            return OnnxCaptioner(Path(path))
        except NotImplementedError:
            log.error("captioner_not_implemented", path=str(path))
    return StubCaptioner()


def load_caption_embedder() -> CaptionEmbedder:
    return StubCaptionEmbedder()
