"""Best-frame selection quietly sets the ceiling on every pipeline
downstream, because they only ever see this crop."""

from __future__ import annotations

import cv2
import numpy as np
from sentinel.ingest import bestframe
from sentinel.ingest.track import KalmanBox, Track, TrackState, xyxy_to_xyah


def sharp_image(w=640, h=480):
    image = np.zeros((h, w, 3), dtype=np.uint8)
    for x in range(0, w, 8):
        image[:, x : x + 4] = 255      # high-frequency detail
    return image


def blurry_image(w=640, h=480):
    return cv2.GaussianBlur(sharp_image(w, h), (31, 31), 0)


def make_track(history):
    track = Track(
        track_id=1, cls="car", kalman=KalmanBox(xyxy_to_xyah(history[0][1])),
        state=TrackState.CONFIRMED, hits=len(history), history=list(history),
    )
    return track


def test_sharper_crop_wins_at_equal_size():
    boxes = [(200, 150, 360, 270)] * 4
    history = [(float(i), boxes[i], 0.9) for i in range(4)]
    frames = {
        0.0: sharp_image(), 1.0: blurry_image(),
        2.0: sharp_image(), 3.0: blurry_image(),
    }
    best = bestframe.choose(make_track(history), frames, 640, 480)
    assert best is not None
    assert bestframe.sharpness(best.crop) > bestframe.sharpness(
        blurry_image()[150:270, 200:360]
    )


def test_larger_crop_beats_smaller():
    history = [
        (0.0, (300, 220, 340, 260), 0.9),
        (1.0, (250, 180, 390, 300), 0.9),
        (2.0, (300, 220, 340, 260), 0.9),
        (3.0, (300, 220, 340, 260), 0.9),
    ]
    frames = {float(i): sharp_image() for i in range(4)}
    best = bestframe.choose(make_track(history), frames, 640, 480)
    assert best.bbox == (250, 180, 390, 300)


def test_first_and_last_observations_are_dropped():
    """A vehicle entering or leaving is clipped by the frame edge almost by
    definition."""
    history = [
        (0.0, (0, 200, 200, 320), 0.9),        # huge, but on the edge
        (1.0, (250, 200, 380, 320), 0.9),
        (2.0, (260, 200, 390, 320), 0.9),
        (3.0, (440, 200, 640, 320), 0.9),      # huge, other edge
    ]
    frames = {float(i): sharp_image() for i in range(4)}
    best = bestframe.choose(make_track(history), frames, 640, 480)
    assert best.bbox[0] not in (0, 440)


def test_edge_margin_is_zero_at_the_border():
    assert bestframe.edge_margin((0, 100, 80, 200), 640, 480) == 0.0
    assert bestframe.edge_margin((280, 200, 360, 280), 640, 480) > 0.5


def test_no_cached_frames_means_no_best_frame():
    history = [(0.0, (100, 100, 200, 200), 0.9)]
    assert bestframe.choose(make_track(history), {}, 640, 480) is None
