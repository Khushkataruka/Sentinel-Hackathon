"""The tracker's dt is the time between the frames it is actually given.

The clock observes every decoded frame; the rate limit hands on only some of
them. dt used to be the decoded-frame delta, so the tracker was told 0.04s
had passed when 0.12s had.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import cv2
import numpy as np
from sentinel.ingest import decode


class FakeCapture:
    """A 25 fps source: a frame every 40 ms of presentation time."""

    def __init__(self, url, backend):
        self.ms = 0.0

    def isOpened(self):
        return True

    def set(self, prop, value):
        return True

    def read(self):
        self.ms += 40.0
        return True, np.zeros((4, 4, 3), np.uint8)

    def get(self, prop):
        return self.ms if prop == cv2.CAP_PROP_POS_MSEC else 0.0

    def release(self):
        pass


def test_dt_spans_the_frames_the_rate_limit_skipped(monkeypatch):
    monkeypatch.setattr(decode.cv2, "VideoCapture", FakeCapture)
    # _open rewrites this for the capture options; restore it afterwards.
    monkeypatch.setenv(
        "OPENCV_FFMPEG_CAPTURE_OPTIONS", os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
    )
    handle = SimpleNamespace(
        camera_id="cam", url="rtsp://grid.example/stream/cam", transport="tcp", options={}
    )
    stream = decode.CameraStream(handle, target_fps=10.0)
    frames = stream.frames()
    emitted = [next(frames) for _ in range(5)]
    stream.close()

    dts = [round(frame.dt_s, 3) for frame in emitted[1:]]
    pts_gaps = [round(b.pts_s - a.pts_s, 3) for a, b in zip(emitted, emitted[1:], strict=False)]
    assert dts == pts_gaps
    assert all(dt >= 0.1 for dt in dts), dts
