"""Flow and density, and the coverage columns that stop them being noise."""

from __future__ import annotations

from datetime import UTC, datetime

from sentinel.core.types import LosBand
from sentinel.ingest.detect import Detection
from sentinel.ingest.traffic import TrafficAccumulator, band


def det(x1, y1, x2, y2, cls="car"):
    return Detection((x1, y1, x2, y2), 0.9, cls)


def test_a_dead_camera_is_distinguishable_from_a_quiet_road():
    """The whole reason both columns exist. Two buckets with the same low
    count, one observed fully and one barely observed at all."""
    quiet = TrafficAccumulator("CAM-1", expected_fps=10.0, bucket_seconds=10)
    dead = TrafficAccumulator("CAM-2", expected_fps=10.0, bucket_seconds=10)

    start = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
    for _ in range(100):
        quiet.observe_frame(start, [])
    for _ in range(8):
        dead.observe_frame(start, [])

    q, d = quiet.close(), dead.close()
    assert q["frames_decoded"] == 100 and q["frames_expected"] == 100
    assert d["frames_decoded"] == 8 and d["frames_expected"] == 100
    # Same story from the counts alone; different once you read the coverage.
    assert q["frames_decoded"] / q["frames_expected"] >= 0.6
    assert d["frames_decoded"] / d["frames_expected"] < 0.6


def test_flow_counts_distinct_tracks_not_detections():
    acc = TrafficAccumulator("CAM-1", expected_fps=10.0, bucket_seconds=300)
    acc.observe_frame(datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC), [])
    acc.observe_track_end(1, "car", 4.0)
    acc.observe_track_end(1, "car", 4.0)     # the same vehicle again
    acc.observe_track_end(2, "car", 6.0)
    summary = acc.close()
    counts = {c["class"]: c["vehicle_count"] for c in summary["counts"]}
    assert counts["car"] == 2


def test_density_is_absent_without_measured_lane_geometry():
    """Flow is available on every camera. Density is not, and calling one the
    other is the standard mistake."""
    acc = TrafficAccumulator("CAM-1", expected_fps=10.0, density_viable=False)
    acc.observe_frame(datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC), [det(0, 0, 50, 50)])
    summary = acc.close()
    assert summary["density_vpkm"] is None
    assert summary["los"] is None


def test_los_bands_read_per_lane():
    assert band(20.0, 4) is LosBand.FREE       # 5 per lane
    assert band(200.0, 2) is LosBand.JAM       # 100 per lane
    assert band(None, 3) is None
    assert band(30.0, None) is None


def test_a_discontinuity_discards_the_open_bucket():
    """Averaging across a scene cut produces a number describing nothing."""
    acc = TrafficAccumulator("CAM-1", expected_fps=10.0, bucket_seconds=300)
    acc.observe_frame(datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC), [det(0, 0, 40, 40)])
    acc.discard()
    assert acc.close() is None


def test_buckets_roll_over_on_the_wall_clock_boundary():
    acc = TrafficAccumulator("CAM-1", expected_fps=10.0, bucket_seconds=300)
    acc.observe_frame(datetime(2026, 9, 2, 10, 2, 0, tzinfo=UTC), [])
    finished = acc.observe_frame(datetime(2026, 9, 2, 10, 6, 0, tzinfo=UTC), [])
    assert finished is not None
    assert finished["bucket_start"].minute == 0
