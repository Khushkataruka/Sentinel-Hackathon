"""The real violation detector's association and gating logic.

No weights are loaded here. The rider association is a pure function over
detection boxes, and the violation detector constructed with no phone model
never imports ultralytics -- which is the point: the parts that decide what
gets asserted about a person are testable without a GPU.
"""

from __future__ import annotations

import numpy as np
from sentinel.pipelines.models.violations import (
    SUPPORTED,
    RiderBox,
    YoloViolationDetector,
    _Box,
    _riders_from_boxes,
)

CROP = np.zeros((240, 320, 3), dtype=np.uint8)

ALL_TYPES = [
    "no_helmet", "triple_riding", "wrong_way", "red_light",
    "illegal_parking", "no_seatbelt", "phone_use",
]


def rider(x1, x2, score=0.9):
    return _Box("rider", score, (x1, 0, x2, 200))


def detector():
    """No phone weights, so no ultralytics import and no model on disk."""
    return YoloViolationDetector(None, device="cpu")


# -- rider association ------------------------------------------------------


def test_a_rider_with_no_helmet_box_is_unassessable_not_bareheaded():
    """The distinction the whole tri-state exists for. A camera that cannot
    see helmets must not start reporting that nobody is wearing one."""
    riders = _riders_from_boxes([rider(0, 100)])
    assert riders[0].helmet is None
    assert riders[0].helmet is not False


def test_a_helmet_box_over_a_rider_is_that_rider_s_verdict():
    riders = _riders_from_boxes([
        rider(0, 100),
        _Box("helmet", 0.8, (30, 10, 70, 50)),      # centre (50, 30), inside
    ])
    assert riders[0].helmet is True
    assert riders[0].helmet_conf == 0.8


def test_a_helmet_box_outside_every_rider_attaches_to_nobody():
    riders = _riders_from_boxes([
        rider(0, 100),
        _Box("no_helmet", 0.9, (200, 10, 240, 50)),  # centre (220, 30), outside
    ])
    assert riders[0].helmet is None


def test_verdicts_follow_their_own_rider():
    """Two riders, one helmeted and one not -- the boxes must not swap."""
    riders = _riders_from_boxes([
        rider(0, 100),
        rider(150, 250),
        _Box("helmet", 0.8, (30, 10, 70, 50)),        # over the left rider
        _Box("no_helmet", 0.7, (180, 10, 220, 50)),   # over the right rider
    ])
    assert [r.slot for r in riders] == [1, 2]
    assert riders[0].helmet is True
    assert riders[1].helmet is False


def test_slots_are_assigned_left_to_right_regardless_of_detection_order():
    riders = _riders_from_boxes([rider(150, 250), rider(0, 100)])
    assert [r.bbox[0] for r in riders] == [0, 150]
    assert [r.slot for r in riders] == [1, 2]


# -- what gets asserted -----------------------------------------------------


def test_one_no_helmet_finding_per_bareheaded_rider_each_naming_its_slot():
    riders = [
        RiderBox(1, (0, 0, 100, 200), helmet=False, helmet_conf=0.7, score=0.9),
        RiderBox(2, (100, 0, 200, 200), helmet=True, helmet_conf=0.8, score=0.9),
        RiderBox(3, (200, 0, 300, 200), helmet=False, helmet_conf=0.6, score=0.9),
    ]
    findings = detector()(
        CROP, vehicle_class="motorcycle", permitted=["no_helmet"],
        track_context={"rider_boxes": riders},
    )
    assert [f.rider_slot for f in findings] == [1, 3]
    assert all(f.violation_type == "no_helmet" for f in findings)


def test_an_unassessable_rider_is_never_reported_as_no_helmet():
    riders = [RiderBox(1, (0, 0, 100, 200), helmet=None, helmet_conf=None, score=0.9)]
    findings = detector()(
        CROP, vehicle_class="motorcycle", permitted=["no_helmet"],
        track_context={"rider_boxes": riders},
    )
    assert findings == []


def test_triple_riding_needs_three_occupants():
    def occupants(n):
        riders = [
            RiderBox(i, (i * 60, 0, i * 60 + 50, 200), helmet=True,
                     helmet_conf=0.8, score=0.9)
            for i in range(1, n + 1)
        ]
        return detector()(
            CROP, vehicle_class="motorcycle", permitted=["triple_riding"],
            track_context={"rider_boxes": riders},
        )

    assert occupants(2) == []
    assert [f.violation_type for f in occupants(3)] == ["triple_riding"]


def test_triple_riding_is_only_as_confident_as_its_weakest_box():
    """Drop the flimsiest rider and there is no third occupant, so the
    weakest box -- not the average -- sets the confidence."""
    riders = [
        RiderBox(1, (0, 0, 50, 200), helmet=True, helmet_conf=0.9, score=0.95),
        RiderBox(2, (60, 0, 110, 200), helmet=True, helmet_conf=0.9, score=0.90),
        RiderBox(3, (120, 0, 170, 200), helmet=True, helmet_conf=0.9, score=0.51),
    ]
    findings = detector()(
        CROP, vehicle_class="motorcycle", permitted=["triple_riding"],
        track_context={"rider_boxes": riders},
    )
    assert findings[0].confidence == 0.51
    assert findings[0].bbox == (0, 0, 170, 200)   # union of the rider boxes


def test_nothing_outside_the_permitted_list_is_ever_asserted():
    """The database trigger would reject it, but a pipeline that relies on
    being caught produces exceptions instead of rows."""
    riders = [
        RiderBox(i, (i * 60, 0, i * 60 + 50, 200), helmet=False,
                 helmet_conf=0.7, score=0.9)
        for i in range(1, 4)
    ]
    findings = detector()(
        CROP, vehicle_class="motorcycle", permitted=["triple_riding"],
        track_context={"rider_boxes": riders},
    )
    assert {f.violation_type for f in findings} == {"triple_riding"}


def test_the_types_a_crop_cannot_support_produce_nothing():
    """Temporal types and no_seatbelt are permitted here and still silent --
    this is the behaviour change from the stub, pinned deliberately."""
    riders = [
        RiderBox(i, (i * 60, 0, i * 60 + 50, 200), helmet=False,
                 helmet_conf=0.7, score=0.9)
        for i in range(1, 4)
    ]
    findings = detector()(
        CROP, vehicle_class="motorcycle", permitted=ALL_TYPES,
        track_context={"rider_boxes": riders},
    )
    asserted = {f.violation_type for f in findings}
    assert asserted <= SUPPORTED
    assert not asserted & {"wrong_way", "red_light", "illegal_parking", "no_seatbelt"}


def test_phone_use_is_silent_without_phone_weights():
    findings = detector()(
        CROP, vehicle_class="car", permitted=["phone_use"], track_context={},
    )
    assert findings == []
