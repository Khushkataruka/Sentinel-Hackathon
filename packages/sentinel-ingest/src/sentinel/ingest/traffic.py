"""Flow and density, accumulated inside ingest.

This runs here rather than as a queue consumer because it needs per-frame
state that never reaches a crop: how much of the carriageway was covered,
how many vehicles were visible at once, and how many frames we actually
decoded.

The two quantities are kept apart on purpose.

  Flow    -- vehicles crossing per interval. One fixed camera measures this
             directly, by counting distinct tracks. Available everywhere.
  Density -- vehicles per kilometre of road. Has to be inferred from
             carriageway occupancy, and only where the survey measured the
             lane polygon and lane count.

Calling flow "density" is the standard mistake and a transport engineer on
the panel would spot it immediately.

frames_expected and frames_decoded are both recorded. A camera that was down
for four minutes of a five-minute bucket reports a low count, and a low count
is indistinguishable from a quiet road unless you can see how much of the
interval was observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.core.types import LosBand
from sentinel.ingest.detect import VEHICLE_CLASSES, Detection

log = get_logger(__name__)

#: Level of service, banded on vehicles per kilometre per lane. Five coarse
#: bands on purpose: a control room reads them at a glance and nothing finer
#: has been calibrated.
LOS_THRESHOLDS_VPKPL = [
    (11.0, LosBand.FREE),
    (18.0, LosBand.LIGHT),
    (26.0, LosBand.MODERATE),
    (45.0, LosBand.HEAVY),
]

#: Mean projected length a vehicle occupies along the carriageway, metres.
#: Used only to turn occupancy into a vehicles-per-km estimate, and only on
#: cameras where the lane geometry was actually measured.
MEAN_VEHICLE_LENGTH_M = 4.5


def band(density_vpkm: float | None, lane_count: int | None) -> LosBand | None:
    if density_vpkm is None or not lane_count:
        return None
    per_lane = density_vpkm / max(lane_count, 1)
    for limit, los in LOS_THRESHOLDS_VPKPL:
        if per_lane < limit:
            return los
    return LosBand.JAM


@dataclass
class Bucket:
    """One five-minute accumulation window for one camera."""

    camera_id: str
    bucket_start: datetime
    bucket_seconds: int
    expected_fps: float

    frames_decoded: int = 0
    occupancy_sum: float = 0.0
    occupancy_peak: float = 0.0
    concurrent_sum: int = 0
    class_counts: dict[str, int] = field(default_factory=dict)
    dwell_sums: dict[str, float] = field(default_factory=dict)
    counted_tracks: set[int] = field(default_factory=set)

    @property
    def frames_expected(self) -> int:
        # From the configured decode rate, not the delivered one: the point is
        # to compare what we should have seen with what we did.
        return max(1, int(round(self.expected_fps * self.bucket_seconds)))

    @property
    def ends_at(self) -> datetime:
        return self.bucket_start + timedelta(seconds=self.bucket_seconds)

    @property
    def coverage(self) -> float:
        return min(self.frames_decoded / self.frames_expected, 1.0)

    def observe_frame(self, detections: list[Detection], lane_area_px: float | None) -> None:
        self.frames_decoded += 1
        vehicles = [d for d in detections if d.cls in VEHICLE_CLASSES]
        self.concurrent_sum += len(vehicles)

        if lane_area_px and lane_area_px > 0:
            covered = sum(d.area for d in vehicles)
            occupancy = min(covered / lane_area_px, 1.0)
            self.occupancy_sum += occupancy
            self.occupancy_peak = max(self.occupancy_peak, occupancy)

    def observe_track_end(self, track_id: int, cls: str, dwell_s: float) -> None:
        """Count a distinct track once. Flow is a count of vehicles that
        crossed, not of detections."""
        if track_id in self.counted_tracks:
            return
        self.counted_tracks.add(track_id)
        self.class_counts[cls] = self.class_counts.get(cls, 0) + 1
        self.dwell_sums[cls] = self.dwell_sums.get(cls, 0.0) + dwell_s

    def summary(
        self, *, density_viable: bool, lane_count: int | None, lane_length_m: float | None
    ) -> dict:
        decoded = max(self.frames_decoded, 1)
        mean_occupancy = self.occupancy_sum / decoded if self.occupancy_sum else None
        mean_concurrent = self.concurrent_sum / decoded

        density_vpkm = None
        if density_viable and mean_occupancy is not None and lane_length_m:
            # Occupancy is the fraction of the carriageway covered. Over a
            # known stretch that converts to vehicles per kilometre. It is an
            # estimate and it is labelled as one.
            vehicles_in_view = mean_occupancy * (lane_length_m / MEAN_VEHICLE_LENGTH_M)
            density_vpkm = vehicles_in_view * (1000.0 / lane_length_m)

        return {
            "camera_id": self.camera_id,
            "bucket_start": self.bucket_start,
            "bucket_seconds": self.bucket_seconds,
            "frames_expected": self.frames_expected,
            "frames_decoded": min(self.frames_decoded, self.frames_expected),
            "mean_occupancy": mean_occupancy,
            "peak_occupancy": self.occupancy_peak or None,
            "mean_concurrent": mean_concurrent,
            "density_vpkm": density_vpkm,
            "los": band(density_vpkm, lane_count),
            "counts": [
                {
                    "class": cls,
                    "vehicle_count": count,
                    # Dwell is time in frame. Without calibration it is a
                    # slowness proxy, comparable to itself at one camera and
                    # not across cameras. Labelled that way on the dashboard.
                    "mean_dwell_s": self.dwell_sums[cls] / count if count else None,
                }
                for cls, count in sorted(self.class_counts.items())
            ],
        }


class TrafficAccumulator:
    """Rolls buckets over for one camera and hands finished ones back."""

    def __init__(
        self,
        camera_id: str,
        *,
        expected_fps: float,
        density_viable: bool = False,
        lane_polygon_px: np.ndarray | None = None,
        lane_count: int | None = None,
        lane_length_m: float | None = None,
        bucket_seconds: int | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.expected_fps = expected_fps
        self.density_viable = density_viable
        self.lane_polygon_px = lane_polygon_px
        self.lane_count = lane_count
        self.lane_length_m = lane_length_m
        self.bucket_seconds = bucket_seconds or settings.traffic_bucket_seconds
        self._bucket: Bucket | None = None

    @property
    def lane_area_px(self) -> float | None:
        if self.lane_polygon_px is None or len(self.lane_polygon_px) < 3:
            return None
        import cv2

        return float(cv2.contourArea(self.lane_polygon_px.astype(np.int32)))

    def _bucket_start_for(self, when: datetime) -> datetime:
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        elapsed = int((when - epoch).total_seconds())
        return epoch + timedelta(seconds=elapsed - elapsed % self.bucket_seconds)

    def observe_frame(self, when: datetime, detections: list[Detection]) -> dict | None:
        """Take one frame. Returns a finished bucket summary when one closes."""
        start = self._bucket_start_for(when)
        finished = None

        if self._bucket is None:
            self._bucket = Bucket(self.camera_id, start, self.bucket_seconds, self.expected_fps)
        elif start != self._bucket.bucket_start:
            finished = self.close()
            self._bucket = Bucket(self.camera_id, start, self.bucket_seconds, self.expected_fps)

        self._bucket.observe_frame(detections, self.lane_area_px)
        return finished

    def observe_track_end(self, track_id: int, cls: str, dwell_s: float) -> None:
        if self._bucket is not None:
            self._bucket.observe_track_end(track_id, cls, dwell_s)

    def close(self) -> dict | None:
        """Close the open bucket and return its summary."""
        if self._bucket is None:
            return None
        summary = self._bucket.summary(
            density_viable=self.density_viable,
            lane_count=self.lane_count,
            lane_length_m=self.lane_length_m,
        )
        self._bucket = None
        return summary

    def discard(self) -> None:
        """Throw the open bucket away.

        Called at a scene discontinuity: the frames before and after a loop
        point are different moments in the recording, and averaging across
        the cut would produce a number describing nothing.
        """
        self._bucket = None
