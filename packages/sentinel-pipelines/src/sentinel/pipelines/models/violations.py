"""Violation detectors, and the rider detector they depend on.

Two things this module takes seriously.

Riders exist as rows because "more than two on a two-wheeler" is a count of
occupants and "no helmet" is a statement about one of them. Both become
unusable as free text in a JSON blob. A rider row is a box on a vehicle and
nothing more: no embedding, no identity, no watchlist, and it cascade-deletes
with the sighting.

Seatbelt and phone use need to resolve a small, low-contrast object through a
windscreen at pole distance. That is a harder optical problem than the face
detection this platform dropped as infeasible. They stay in the catalogue,
they only fire where the survey cleared them, and they have no auto-confirm
threshold -- every one goes to a human. The database enforces the first two;
this module does not get a vote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from sentinel.core.logging import get_logger
from sentinel.pipelines.models.stubbase import rng_for

log = get_logger(__name__)


@dataclass
class RiderBox:
    slot: int                       # 1 = rider, 2+ = pillion
    bbox: tuple[int, int, int, int]
    helmet: bool | None = None      # None = not assessable on this camera
    helmet_conf: float | None = None


@dataclass
class ViolationFinding:
    violation_type: str
    confidence: float
    rider_slot: int | None = None
    bbox: tuple[int, int, int, int] | None = None
    details: dict[str, Any] = field(default_factory=dict)
    window_start_pts: float | None = None
    window_end_pts: float | None = None


class RiderDetector(Protocol):
    is_stub: bool

    def __call__(self, crop: np.ndarray) -> list[RiderBox]: ...


class ViolationDetector(Protocol):
    is_stub: bool

    def __call__(
        self,
        crop: np.ndarray,
        *,
        vehicle_class: str,
        permitted: list[str],
        track_context: dict[str, Any] | None = None,
    ) -> list[ViolationFinding]: ...


class StubRiderDetector:
    """Places one or two rider boxes on a two-wheeler crop, deterministically."""

    is_stub = True

    def __init__(self) -> None:
        log.warning("stub_rider_detector", reason="no rider weights; boxes are placeholders")

    def __call__(self, crop: np.ndarray) -> list[RiderBox]:
        h, w = crop.shape[:2]
        rng = rng_for(crop)
        occupants = 1 + int(rng.random() < 0.22) + int(rng.random() < 0.06)

        riders: list[RiderBox] = []
        for slot in range(1, occupants + 1):
            top = int(h * 0.05)
            bottom = int(h * 0.55)
            left = int(w * (0.15 + 0.18 * (slot - 1)))
            right = min(w, left + int(w * 0.45))
            confidence = round(0.35 + float(rng.random()) * 0.6, 3)
            # A helmet verdict of None means the camera cannot assess it,
            # which is different from "no helmet" and must stay different.
            helmet = None if confidence < 0.45 else bool(rng.random() > 0.35)
            riders.append(
                RiderBox(slot, (left, top, right, bottom), helmet,
                         confidence if helmet is not None else None)
            )
        return riders


class StubViolationDetector:
    """Deterministic findings for the permitted types only.

    Never invents a type the camera was not surveyed for. The database would
    reject it anyway -- a trigger checks violation_type against
    camera_profiles.permitted_violations on insert -- but a pipeline that
    relied on being caught would be a pipeline that produced exceptions
    instead of rows every night.
    """

    is_stub = True

    #: Types that need a track rather than a single frame.
    TEMPORAL = {"wrong_way", "red_light", "illegal_parking"}

    def __init__(self) -> None:
        log.warning(
            "stub_violation_detector",
            reason="no violation weights; findings are placeholders",
        )

    def __call__(
        self,
        crop: np.ndarray,
        *,
        vehicle_class: str,
        permitted: list[str],
        track_context: dict[str, Any] | None = None,
    ) -> list[ViolationFinding]:
        rng = rng_for(crop)
        findings: list[ViolationFinding] = []
        context = track_context or {}

        for code in permitted:
            # Roughly one in six permitted types fires per vehicle. The real
            # detectors decide this properly; the rate here just keeps the
            # review queue populated without drowning it.
            if rng.random() > 0.16:
                continue

            confidence = round(0.40 + float(rng.random()) * 0.55, 3)
            finding = ViolationFinding(
                violation_type=code,
                confidence=confidence,
                details={"stub": True, "vehicle_class": vehicle_class},
            )
            if code in self.TEMPORAL:
                finding.window_start_pts = context.get("track_start_pts")
                finding.window_end_pts = context.get("track_end_pts")
                finding.details["evaluated_against"] = "track"
            findings.append(finding)

        return findings


def load_rider_detector() -> RiderDetector:
    return StubRiderDetector()


def load_violation_detector() -> ViolationDetector:
    return StubViolationDetector()
