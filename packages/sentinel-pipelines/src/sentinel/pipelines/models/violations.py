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

What the real detectors cover
-----------------------------
The YOLO path runs over the vehicle *crop* the pipeline already cut, not the
frame, because that is what ``ViolateWorker`` has. That bounds what is
assertable here:

``no_helmet``      helmet weights, classes rider / helmet / no_helmet.
``triple_riding``  the same pass -- the crop is the two-wheeler, so the
                   occupant count is just the number of rider boxes.
``phone_use``      phone weights, one class, ``cell_phone``.

``no_seatbelt`` is NOT implemented and will not be. The available weights
know exactly one class, ``seat_belt``. Absence of a seatbelt box is not
evidence of an unbelted driver -- through a windscreen at pole distance it is
overwhelmingly evidence that the belt did not resolve, which is what
``violation_types.needs_glass_penetration`` already says about this type.

``wrong_way``, ``red_light`` and ``illegal_parking`` are not implemented
either. All three need a track across frames, and two of them need a signal
head or a stop line in view. A single crop carries none of that. The stub
fabricates them; the real detector returns nothing, which is why swapping the
weights in makes those rows stop appearing in the review queue.

Every unimplemented-but-permitted type is named in a log line at load, once
per worker. A permitted type that silently never fires is a thing someone
debugs at three in the morning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.pipelines.models.stubbase import rng_for

log = get_logger(__name__)

#: What the YOLO path can actually assert. Everything else in the catalogue
#: is either temporal or optically out of reach -- see the module docstring.
SUPPORTED = frozenset({"no_helmet", "triple_riding", "phone_use"})


@dataclass
class RiderBox:
    slot: int                       # 1 = rider, 2+ = pillion
    bbox: tuple[int, int, int, int]
    helmet: bool | None = None      # None = not assessable on this camera
    helmet_conf: float | None = None
    score: float = 1.0              # the rider box's own detection score


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
                         confidence if helmet is not None else None, confidence)
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


# ---------------------------------------------------------------------------
# The real path: ultralytics over the vehicle crop
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Box:
    name: str
    score: float
    xyxy: tuple[int, int, int, int]

    @property
    def centre(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.xyxy
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _detect(model: Any, crop: np.ndarray, conf: float, device: str) -> list[_Box]:
    """One ultralytics forward pass, flattened to plain boxes.

    No letterboxing or NMS here, unlike ingest/detect.py: ultralytics does
    both internally, and duplicating them would be two implementations of the
    same thing that can drift apart.
    """
    result = model.predict(crop, conf=conf, verbose=False, device=device)[0]
    if result.boxes is None:
        return []

    boxes: list[_Box] = []
    for b in result.boxes:
        x1, y1, x2, y2 = (int(round(v)) for v in b.xyxy[0].tolist())
        if x2 <= x1 or y2 <= y1:
            continue
        boxes.append(_Box(result.names[int(b.cls)], float(b.conf), (x1, y1, x2, y2)))
    return boxes


def _contains_centre(outer: tuple[int, int, int, int], inner: _Box) -> bool:
    cx, cy = inner.centre
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _riders_from_boxes(boxes: list[_Box]) -> list[RiderBox]:
    """Rider boxes plus a per-rider helmet verdict, from one helmet-model pass.

    A helmet box belongs to the rider whose box contains its centre; where
    several riders overlap, the highest-scoring box wins. A rider with no
    helmet-class box over them gets None -- not assessable -- never False.
    """
    riders = sorted((b for b in boxes if b.name == "rider"), key=lambda b: b.xyxy[0])
    helmets = [b for b in boxes if b.name in ("helmet", "no_helmet")]

    out: list[RiderBox] = []
    for slot, rider in enumerate(riders, start=1):
        mine = [h for h in helmets if _contains_centre(rider.xyxy, h)]
        best = max(mine, key=lambda h: h.score) if mine else None
        out.append(
            RiderBox(
                slot=slot,
                bbox=rider.xyxy,
                helmet=(best.name == "helmet") if best else None,
                helmet_conf=best.score if best else None,
                score=rider.score,
            )
        )
    return out


class YoloRiderDetector:
    """Rider boxes and per-rider helmet verdicts from the helmet weights.

    Slot order is left-to-right across the crop. That is deterministic and it
    is NOT a claim about who is driving: a single crop carries no direction of
    travel, so nothing here can tell the rider from the pillion. Slot 1 means
    "leftmost in this image". It matters because s.194D is a rider-specific
    offence, and a reviewer reading "slot 2" should not take it as "pillion".
    Fixing it needs the track's direction of travel, which is the same thing
    the temporal violation types are waiting on.

    The helmet verdict stays tri-state. A rider with no helmet-class box over
    them gets None -- not assessable -- rather than False. Collapsing those
    two is how a camera that cannot see helmets starts reporting that nobody
    is wearing one.
    """

    is_stub = False

    def __init__(self, model_path: Path, *, conf: float, device: str) -> None:
        from ultralytics import YOLO

        self.model = YOLO(str(model_path))
        self.conf = conf
        self.device = device
        log.info("rider_detector_loaded", path=str(model_path), device=device)

    def __call__(self, crop: np.ndarray) -> list[RiderBox]:
        return _riders_from_boxes(_detect(self.model, crop, self.conf, self.device))


class YoloViolationDetector:
    """The three types a single vehicle crop can support.

    no_helmet and triple_riding are read off the rider boxes the rider
    detector already produced -- they arrive through track_context, so the
    helmet weights run once per crop rather than twice.

    Throughput ceiling: yolo11m on CPU is roughly 0.3 s per crop, and a
    two-wheeler runs two models. That is fine for a queue a human reviews and
    not fine for a live estate. The upgrade path is SENTINEL_VIOLATION_DEVICE
    (mps, or a CUDA index), or an ONNX export behind the same protocol, the
    way ingest/detect.py already runs.
    """

    is_stub = False

    def __init__(self, phone_model_path: Path | None, *, device: str) -> None:
        self.device = device
        self.phone_model = None
        if phone_model_path is not None:
            from ultralytics import YOLO

            self.phone_model = YOLO(str(phone_model_path))
            log.info("phone_detector_loaded", path=str(phone_model_path), device=device)

        self._reported: set[str] = set()

    def _report_unsupported(self, permitted: list[str]) -> None:
        """Name the permitted types this detector will never fire, once each.

        A camera surveyed for a type that silently never produces a row is
        indistinguishable from a camera where nothing happens.
        """
        for code in permitted:
            if code in SUPPORTED or code in self._reported:
                continue
            self._reported.add(code)
            reason = (
                "needs a track across frames; this detector sees one crop"
                if code in StubViolationDetector.TEMPORAL
                else "no detector: the weights assert presence, not absence"
                if code == "no_seatbelt"
                else "not implemented"
            )
            log.warning("violation_type_unsupported", type=code, reason=reason)

    def __call__(
        self,
        crop: np.ndarray,
        *,
        vehicle_class: str,
        permitted: list[str],
        track_context: dict[str, Any] | None = None,
    ) -> list[ViolationFinding]:
        self._report_unsupported(permitted)

        context = track_context or {}
        riders: list[RiderBox] = context.get("rider_boxes") or []
        findings: list[ViolationFinding] = []

        if "no_helmet" in permitted:
            # One finding per bare-headed rider: two of them is two offences,
            # and each names its own slot. helmet is None -- not assessable --
            # never reaches here, which is the whole point of the tri-state.
            for rider in riders:
                if rider.helmet is False:
                    findings.append(
                        ViolationFinding(
                            violation_type="no_helmet",
                            confidence=rider.helmet_conf or rider.score,
                            rider_slot=rider.slot,
                            bbox=rider.bbox,
                            details={"occupants": len(riders)},
                        )
                    )

        if "triple_riding" in permitted and len(riders) >= 3:
            # The claim is only as strong as the weakest box that makes the
            # count: drop the flimsiest rider and there is no third occupant.
            findings.append(
                ViolationFinding(
                    violation_type="triple_riding",
                    confidence=min(r.score for r in riders),
                    bbox=_union(r.bbox for r in riders),
                    details={"occupants": len(riders)},
                )
            )

        if "phone_use" in permitted and self.phone_model is not None:
            phones = _detect(self.phone_model, crop, settings.phone_conf, self.device)
            if phones:
                best = max(phones, key=lambda b: b.score)
                findings.append(
                    ViolationFinding(
                        violation_type="phone_use",
                        confidence=best.score,
                        bbox=best.xyxy,
                        details={"vehicle_class": vehicle_class,
                                 "through_glass": vehicle_class not in ("motorcycle", "bicycle")},
                    )
                )

        return findings


def _union(boxes) -> tuple[int, int, int, int]:
    xs1, ys1, xs2, ys2 = zip(*boxes, strict=True)
    return min(xs1), min(ys1), max(xs2), max(ys2)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_rider_detector() -> RiderDetector:
    path = settings.helmet_model_path
    if not Path(path).exists():
        return StubRiderDetector()
    try:
        return YoloRiderDetector(
            Path(path), conf=settings.helmet_conf, device=settings.violation_device
        )
    except Exception as exc:
        log.error("rider_detector_load_failed", path=str(path), error=str(exc))
        return StubRiderDetector()


def load_violation_detector() -> ViolationDetector:
    """Real detector only when the helmet weights are present.

    Gated on the helmet weights rather than the phone weights because the
    rider boxes are this detector's main input. With the helmet weights
    missing the rider detector is a stub, and feeding stub rider boxes to a
    real violation detector would turn placeholders into confident no_helmet
    rows. Stub in, stub out.
    """
    if not Path(settings.helmet_model_path).exists():
        return StubViolationDetector()

    phone_path = Path(settings.phone_model_path)
    if not phone_path.exists():
        log.warning("no_phone_weights", expected_at=str(phone_path),
                    effect="phone_use will not fire even where it is permitted")
        phone_path = None

    try:
        return YoloViolationDetector(phone_path, device=settings.violation_device)
    except Exception as exc:
        log.error("violation_detector_load_failed", error=str(exc))
        return StubViolationDetector()
