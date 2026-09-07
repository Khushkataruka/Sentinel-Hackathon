"""Scoped track ids, and the scheduled loop cut.

Both exist because of things measured against a real looping RTSP feed
rather than reasoned about: ids collided across resets, and the loop point
turned out to be undetectable from the video.
"""

from __future__ import annotations

from types import SimpleNamespace

from sentinel.ingest.track import ByteTrack, KalmanBox, Track, xyxy_to_xyah
from sentinel.ingest.worker import CameraWorker
from sentinel.ingest.writer import Gating


def gating(loop_period_s=None):
    return Gating(
        camera_id="CAM-1", permitted_attributes=[], permitted_violations=[],
        plate_viable=False, density_viable=False, decode_fps=10.0,
        deinterlace=False, distortion=None, trust_level=0.8,
        lane_count=None, lane_polygon_wkt=None, loop_period_s=loop_period_s,
    )


def worker(loop_period_s=None):
    w = CameraWorker("CAM-1", adapter=None, detector=lambda img: [])
    w.gating = gating(loop_period_s)
    w.tracker = ByteTrack()
    return w


def track(n):
    return Track(track_id=n, cls="car", kalman=KalmanBox(xyxy_to_xyah((0, 0, 10, 10))))


def test_scoped_ids_survive_a_reset():
    w = worker()
    before = w.scoped_track_id(track(1))
    w._epoch += 1
    after = w.scoped_track_id(track(1))
    assert before != after, "the same tracker id in two epochs must differ"


def test_scoped_ids_are_stable_within_an_epoch():
    w = worker()
    assert w.scoped_track_id(track(4)) == w.scoped_track_id(track(4))


def test_two_workers_on_one_camera_do_not_collide():
    """A restarted worker begins numbering at 1 again."""
    assert worker().scoped_track_id(track(1)) != worker().scoped_track_id(track(1))


def test_no_scheduled_cut_without_a_measured_period():
    """Unknown means do nothing, not guess."""
    w = worker(loop_period_s=None)
    assert not any(
        w._crossed_loop_point(SimpleNamespace(pts_s=p)) for p in (0, 10, 20, 40, 100)
    )


def test_scheduled_cut_fires_once_per_loop():
    w = worker(loop_period_s=20.0)
    fired = [
        p for p in (0.0, 5.0, 19.9, 20.1, 25.0, 39.9, 40.1, 45.0, 60.5)
        if w._crossed_loop_point(SimpleNamespace(pts_s=p))
    ]
    assert fired == [20.1, 40.1, 60.5]


def test_the_first_frame_is_never_a_scheduled_cut():
    """Joining mid-recording must not immediately flush state that does not
    exist yet."""
    w = worker(loop_period_s=20.0)
    assert not w._crossed_loop_point(SimpleNamespace(pts_s=137.4))
    assert w._crossed_loop_point(SimpleNamespace(pts_s=140.1))
