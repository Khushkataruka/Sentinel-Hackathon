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

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import cv2
import httpx
import numpy as np
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.pipelines.models.caption_prompt import FEW_SHOT_EXAMPLES, caption_messages
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


def parse_description_xml(content: str) -> Description:
    """Validate the model's XML and extract only persisted description fields."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("vLLM returned no XML description")
    content = content.strip()
    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        if lines[0].lower() in {"```", "```xml"}:
            content = "\n".join(lines[1:-1]).strip()
    if "<!DOCTYPE" in content or "<!ENTITY" in content:
        raise ValueError("vLLM returned unsupported XML")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError("vLLM returned malformed vehicle XML") from exc

    expected = {"thinking", "type", "colour", "make", "model", "features", "caption"}
    if root.tag != "image" or len(root) != len(expected) or {c.tag for c in root} != expected:
        raise ValueError("vLLM XML must contain one image with all description fields")
    if any(node.attrib or (node.tail or "").strip() for node in root.iter()):
        raise ValueError("vLLM XML contains unexpected attributes or text")
    if (root.text or "").strip():
        raise ValueError("vLLM XML contains text outside its description fields")

    def text_value(element: ET.Element) -> str | None:
        if len(element):
            raise ValueError(f"vLLM XML field {element.tag} must contain plain text")
        value = (element.text or "").strip()
        return None if value.lower() in {"", "unknown", "none", "null", "n/a"} else value

    fields = {child.tag: text_value(child) for child in root if child.tag != "features"}
    features = root.find("features")
    if (features.text or "").strip() or any(c.tag != "feature" for c in features):
        raise ValueError("vLLM XML features must contain feature elements")
    return Description(
        colour=fields["colour"],
        vtype=fields["type"],
        make=fields["make"],
        model=fields["model"],
        features=[value for child in features if (value := text_value(child))],
        caption=fields["caption"] or "",
    )


class VllmCaptioner:
    """Describe a JPEG crop through vLLM using three-shot XML prompting."""

    is_stub = False

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.model = model
        api_base = self.base_url if self.base_url.endswith("/v1") else f"{self.base_url}/v1"
        self.endpoint = f"{api_base}/chat/completions"
        self.client = httpx.Client(timeout=settings.vllm_timeout_s)
        log.info("vllm_captioner_enabled", model=model, examples=len(FEW_SHOT_EXAMPLES))

    def __call__(self, crop: np.ndarray, vehicle_class: str) -> Description:
        if crop is None or crop.size == 0:
            raise ValueError("cannot caption an empty crop")
        encoded, buffer = cv2.imencode(".jpg", crop)
        if not encoded:
            raise ValueError("could not encode crop as JPEG")
        b64_img = base64.b64encode(buffer).decode("ascii")
        image_data_url = f"data:image/jpeg;base64,{b64_img}"

        payload = {
            "model": self.model,
            "messages": caption_messages(image_data_url, vehicle_class),
            "temperature": 0,
            "max_tokens": settings.vllm_max_tokens,
            "stream": False,
        }

        # Let transport and parsing failures reach the queue's retry handling;
        # returning an empty Description would mark a failed inference as done.
        response = self.client.post(self.endpoint, json=payload)
        response.raise_for_status()
        try:
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("vLLM returned no chat completion") from exc
        if choice.get("finish_reason") not in {None, "stop"}:
            raise ValueError("vLLM did not finish the XML description")
        return parse_description_xml(content)


def load_captioner() -> Captioner:
    if settings.vllm_url:
        return VllmCaptioner(settings.vllm_url, settings.vllm_model)

    path = settings.caption_model_path
    if Path(path).exists():
        try:
            return OnnxCaptioner(Path(path))
        except NotImplementedError:
            log.error("captioner_not_implemented", path=str(path))
    return StubCaptioner()


def load_caption_embedder() -> CaptionEmbedder:
    return StubCaptionEmbedder()
