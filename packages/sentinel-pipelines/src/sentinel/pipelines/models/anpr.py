"""Plate detection and reading.

Only runs on cameras whose survey says plates are readable. Section 3.1 is
blunt: plates are not readable on most of this estate, and identification
runs on appearance with ANPR wherever it happens to work.

The top few guesses are kept, not just the best one. That is what makes the
confusion-weighted fuzzy matching in correlation work at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.pipelines.models.stubbase import rng_for

log = get_logger(__name__)

#: Indian plate formats. Validating against these rejects a large amount of
#: OCR garbage before it ever reaches the database.
PLATE_PATTERNS = [
    re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$"),   # GJ01AB1234, modern
    re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,2}\d{1,4}$"),  # older short forms
    re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$"),            # Bharat series
]

STATE_CODES = {
    "GJ", "MH", "RJ", "MP", "DL", "UP", "KA", "TN", "AP", "TS", "HR", "PB",
    "WB", "KL", "OD", "BR", "JH", "CG", "UK", "HP", "GA", "AS", "CH", "DD", "DN",
}


@dataclass
class PlateRead:
    text: str
    confidence: float
    rank: int
    valid_format: bool


def validate(text: str) -> bool:
    cleaned = re.sub(r"[^A-Z0-9]", "", text.upper())
    if len(cleaned) < 6 or cleaned[:2] not in STATE_CODES:
        return False
    return any(pattern.match(cleaned) for pattern in PLATE_PATTERNS)


class PlateReader(Protocol):
    is_stub: bool

    def __call__(self, crop: np.ndarray, top_k: int = 3) -> list[PlateRead]: ...


class StubPlateReader:
    """Reads nothing, most of the time -- which is the honest stub.

    Section 3.1 says plates are not legible on most of this estate. A stub
    that returned a plate for every vehicle would make the platform look like
    it solves the problem it explicitly cannot solve, and every route would
    come out plate-anchored and wrongly confident.

    So this returns hypotheses for roughly one crop in eight, deterministically,
    and marks them.
    """

    is_stub = True
    HIT_RATE = 0.125

    def __init__(self) -> None:
        log.warning(
            "stub_plate_reader",
            reason="no ANPR weights; plate reads are placeholders",
            expected_at=str(settings.plate_ocr_model_path),
        )

    def __call__(self, crop: np.ndarray, top_k: int = 3) -> list[PlateRead]:
        rng = rng_for(crop)
        if rng.random() > self.HIT_RATE:
            return []

        letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
        state = sorted(STATE_CODES)[int(rng.integers(len(STATE_CODES)))]
        series = "".join(letters[int(rng.integers(len(letters)))] for _ in range(2))
        digits = f"{int(rng.integers(0, 10000)):04d}"
        base = f"{state}{int(rng.integers(1, 39)):02d}{series}{digits}"

        reads = [PlateRead(base, round(0.55 + float(rng.random()) * 0.35, 3), 1,
                           validate(base))]
        # Alternatives that differ by exactly the confusions OCR really makes,
        # so the fuzzy matcher in correlation has something realistic to chew on.
        confusions = {"0": "D", "1": "I", "8": "B", "5": "S", "2": "Z"}
        for rank in range(2, top_k + 1):
            variant = list(base)
            for i, ch in enumerate(variant):
                if ch in confusions and rng.random() < 0.4:
                    variant[i] = confusions[ch]
                    break
            text = "".join(variant)
            reads.append(
                PlateRead(text, round(reads[0].confidence * (0.8 ** (rank - 1)), 3),
                          rank, validate(text))
            )
        return reads


class OnnxPlateReader:
    """Detector plus OCR, both ONNX, both CPU. Not wired in the scaffold."""

    is_stub = False

    def __init__(self, detect_path: Path, ocr_path: Path) -> None:   # pragma: no cover
        raise NotImplementedError(
            "ANPR not wired; export the plate detector and reader to ONNX"
        )

    def __call__(self, crop: np.ndarray, top_k: int = 3) -> list[PlateRead]:  # pragma: no cover
        raise NotImplementedError


def load_plate_reader() -> PlateReader:
    detect, ocr = settings.plate_detect_model_path, settings.plate_ocr_model_path
    if Path(detect).exists() and Path(ocr).exists():
        try:
            return OnnxPlateReader(Path(detect), Path(ocr))
        except NotImplementedError:
            log.error("anpr_not_implemented")
    return StubPlateReader()
