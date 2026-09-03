"""ByteTrack, and the PTS-driven motion model."""

from __future__ import annotations

import numpy as np
from sentinel.ingest.detect import Detection
from sentinel.ingest.track import (
    ByteTrack,
    KalmanBox,
    class_mask,
    iou_matrix,
    xyxy_to_xyah,
)


def det(x1, y1, x2, y2, score=0.9, cls="car"):
    return Detection((x1, y1, x2, y2), score, cls)


def test_iou_of_identical_boxes_is_one():
    boxes = np.array([[0, 0, 10, 10]], dtype=float)
    assert abs(iou_matrix(boxes, boxes)[0, 0] - 1.0) < 1e-6


def test_disjoint_boxes_have_no_overlap():
    a = np.array([[0, 0, 10, 10]], dtype=float)
    b = np.array([[50, 50, 60, 60]], dtype=float)
    assert iou_matrix(a, b)[0, 0] == 0.0


def test_a_vehicle_crossing_the_frame_keeps_one_id():
    tracker = ByteTrack()
    ids = set()
    for step in range(12):
        x = 100 + step * 18
        active, _ = tracker.update([det(x, 200, x + 80, 260)], 0.1, step * 0.1)
        ids.update(t.track_id for t in active)
    assert len(ids) == 1, "one vehicle must produce one record, not one per frame"


def test_two_vehicles_get_two_ids():
    tracker = ByteTrack()
    for step in range(10):
        tracker.update(
            [det(100 + step * 10, 200, 180 + step * 10, 260),
             det(400 - step * 10, 300, 480 - step * 10, 360)],
            0.1, step * 0.1,
        )
    assert tracker._next_id - 1 >= 2


def test_low_confidence_detections_keep_an_occluded_track_alive():
    """The ByteTrack contribution: a low-scoring box is usually an occluded
    vehicle, not noise, and dropping it fragments the track."""
    tracker = ByteTrack()
    for step in range(6):
        x = 100 + step * 15
        tracker.update([det(x, 200, x + 80, 260, score=0.9)], 0.1, step * 0.1)
    before = {t.track_id for t in tracker.tracks}

    for step in range(6, 10):
        x = 100 + step * 15
        tracker.update([det(x, 200, x + 80, 260, score=0.25)], 0.1, step * 0.1)
    after = {t.track_id for t in tracker.tracks}
    assert before == after


def test_a_finished_track_is_returned_once():
    tracker = ByteTrack()
    for step in range(8):
        x = 100 + step * 15
        tracker.update([det(x, 200, x + 80, 260)], 0.1, step * 0.1)

    finished_total = []
    for step in range(8, 50):
        _, finished = tracker.update([], 0.1, step * 0.1)
        finished_total.extend(finished)
    assert len(finished_total) == 1


def test_a_one_frame_blip_is_not_a_sighting():
    """One detection is not a vehicle passing a camera. Writing it would put
    noise into the route graph."""
    tracker = ByteTrack()
    tracker.update([det(100, 200, 180, 260)], 0.1, 0.0)
    finished = []
    for step in range(1, 60):
        _, done = tracker.update([], 0.1, step * 0.1)
        finished.extend(done)
    assert finished == []


def test_reset_ends_every_track():
    """The feed loops. Carrying ids across the cut would link two unrelated
    vehicles, and that link would become a route leg."""
    tracker = ByteTrack()
    for step in range(8):
        x = 100 + step * 15
        tracker.update([det(x, 200, x + 80, 260)], 0.1, step * 0.1)

    cut = tracker.reset()
    assert len(cut) == 1
    assert tracker.tracks == []


def test_kalman_integrates_over_real_time_not_frame_count():
    """Two seconds of motion must advance the box twice as far as one,
    whatever the frame cadence was."""
    def advance(dt, steps):
        box = KalmanBox(xyxy_to_xyah((100.0, 100.0, 180.0, 160.0)))
        for i in range(1, steps + 1):
            box.predict(dt)
            box.update(xyxy_to_xyah((100.0 + i * 20 * dt, 100.0,
                                     180.0 + i * 20 * dt, 160.0)))
        return box.mean[4]     # x velocity

    slow = advance(0.1, 20)
    fast = advance(0.2, 10)
    # Same real-world speed, different cadence: velocities should agree.
    assert abs(slow - fast) < abs(slow) * 0.5


def test_a_long_gap_does_not_fling_the_box():
    box = KalmanBox(xyxy_to_xyah((100.0, 100.0, 180.0, 160.0)))
    box.mean[4] = 500.0        # a large velocity
    before = box.mean[0]
    box.predict(30.0)          # a thirty second gap
    assert box.mean[0] - before <= 500.0 * 1.0 + 1e-6


def test_ordinary_traffic_is_not_mistaken_for_a_scene_cut():
    """Vehicles leave one or two at a time. If that read as a cut, every
    camera would flush its tracks constantly."""
    tracker = ByteTrack()
    for step in range(8):
        tracker.update(
            [det(100 + step * 12, 200, 180 + step * 12, 260),
             det(300 + step * 12, 300, 380 + step * 12, 360),
             det(500 - step * 12, 100, 580 - step * 12, 160)],
            0.1, step * 0.1,
        )
    assert not tracker.suspects_scene_cut()

    # One of the three leaves the frame.
    for step in range(8, 12):
        tracker.update(
            [det(100 + step * 12, 200, 180 + step * 12, 260),
             det(300 + step * 12, 300, 380 + step * 12, 360)],
            0.1, step * 0.1,
        )
        assert not tracker.suspects_scene_cut(), "one vehicle leaving is not a cut"


def test_every_vehicle_replaced_at_once_is_a_scene_cut():
    """The loop point. Every vehicle on screen is replaced in one frame,
    which ordinary traffic never does.

    This is the detector that matters: mediamtx rewrites PTS to run
    continuously across the loop, so the clock never sees the cut.
    """
    tracker = ByteTrack()
    for step in range(8):
        tracker.update(
            [det(100 + step * 12, 200, 180 + step * 12, 260),
             det(300 + step * 12, 300, 380 + step * 12, 360),
             det(500 - step * 12, 100, 580 - step * 12, 160)],
            0.1, step * 0.1,
        )
    assert not tracker.suspects_scene_cut()

    # The recording loops: three different vehicles, elsewhere in the frame.
    tracker.update(
        [det(20, 400, 100, 450), det(600, 30, 640, 80)], 0.067, 0.8
    )
    assert tracker.suspects_scene_cut()


def test_a_single_track_never_triggers_a_cut():
    """With one vehicle on screen a teleport is indistinguishable from one
    leaving and another arriving, so we do not guess."""
    tracker = ByteTrack()
    for step in range(8):
        x = 100 + step * 12
        tracker.update([det(x, 200, x + 80, 260)], 0.1, step * 0.1)
    tracker.update([det(500, 400, 580, 450)], 0.1, 0.8)
    assert not tracker.suspects_scene_cut()


def test_an_empty_frame_does_not_read_as_a_cut_on_its_own():
    """A gap in detections is not a scene change; it is a gap."""
    tracker = ByteTrack()
    assert not tracker.suspects_scene_cut()
    tracker.update([], 0.1, 0.0)
    assert not tracker.suspects_scene_cut()


def test_track_ids_keep_climbing_across_a_reset():
    """Within one process, ids are not reused after a scene cut.

    The collision that lost sightings on a real feed came from worker
    RESTARTS, not from resets: a fresh ByteTrack numbers from 1 again, and
    sightings has UNIQUE (camera_id, track_id). See test_worker_ids.py --
    CameraWorker scopes the stored id with a per-run token for exactly that
    reason.
    """
    tracker = ByteTrack()
    for step in range(8):
        x = 100 + step * 15
        tracker.update([det(x, 200, x + 80, 260)], 0.1, step * 0.1)
    before = {t.track_id for t in tracker.tracks}

    tracker.reset()
    tracker.update([det(100, 200, 180, 260)], 0.1, 1.0)
    after = {t.track_id for t in tracker.tracks}

    assert not (before & after), "a reset must not reissue live ids"


def test_a_fresh_tracker_reuses_ids_from_one():
    """The actual collision source: a restarted worker starts again at 1."""
    a, b = ByteTrack(), ByteTrack()
    a.update([det(100, 200, 180, 260)], 0.1, 0.0)
    b.update([det(400, 300, 480, 360)], 0.1, 0.0)
    assert {t.track_id for t in a.tracks} == {t.track_id for t in b.tracks} == {1}


def test_person_box_cannot_capture_a_vehicle_track():
    """A rider's person box overlaps its motorcycle heavily. Without a class
    constraint the greedy match can hand it the bike's track, which then
    keeps cls='motorcycle' while being fed person observations."""
    tracker = ByteTrack()
    bike = (300, 200, 380, 300)

    for i in range(4):
        tracker.update([Detection(bike, 0.9, "motorcycle")], 0.1, i * 0.1)

    track = tracker.tracks[0]
    assert track.cls == "motorcycle"

    # A person box sitting right on top of the bike, plus the bike itself.
    person = (305, 190, 375, 295)
    tracker.update(
        [Detection(person, 0.95, "person"), Detection(bike, 0.9, "motorcycle")],
        0.1, 0.4,
    )

    bike_tracks = [t for t in tracker.tracks if t.cls == "motorcycle"]
    person_tracks = [t for t in tracker.tracks if t.cls == "person"]
    assert len(bike_tracks) == 1
    assert len(person_tracks) == 1

    # The bike's track took the bike box, not the higher-scoring person box.
    assert bike_tracks[0].history[-1][1] == bike
    assert person_tracks[0].history[-1][1] == person


def test_class_mask_shape_and_values():
    m = class_mask(["car", "person"], ["person", "car", "car"])
    assert m.shape == (2, 3)
    assert m.tolist() == [[0.0, 1.0, 1.0], [1.0, 0.0, 0.0]]
    assert class_mask([], ["car"]).shape == (0, 1)
