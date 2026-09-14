"""SENTINEL_INGEST_MAX_CAMERAS: stream only the first N cameras.

The grid meters watch time per account; every camera at once spent it.
"""

from __future__ import annotations

from sentinel.ingest.supervisor import limit_cameras


def test_no_limit_keeps_every_camera():
    cameras = {"cam02": "a", "cam01": "b"}
    assert limit_cameras(cameras, None) == cameras
    assert limit_cameras(cameras, 0) == cameras


def test_the_first_cameras_by_id_are_kept_whatever_the_catalogue_order():
    cameras = {f"cam{n:02d}": n for n in (12, 3, 29, 1, 8, 5)}
    assert list(limit_cameras(cameras, 3)) == ["cam01", "cam03", "cam05"]


def test_a_limit_above_the_camera_count_changes_nothing():
    cameras = {"cam01": "a", "cam02": "b"}
    assert limit_cameras(cameras, 8) == cameras
