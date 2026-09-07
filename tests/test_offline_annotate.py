"""The two-pass join, which is the part that can be subtly wrong.

Pass one records per-frame boxes against PTS; pass two re-decodes and draws
them. If the join key drifts -- a float compared exactly, a frame index that
depends on how the file was read -- the video still renders, still looks
plausible, and has every box on the wrong frame. So the tests here are about
the key and the geometry, not about whether OpenCV can write a file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from sentinel.ingest.annotate import (
    CAPTION_CHARS,
    Label,
    _key,
    _overlaps,
    apply_matches,
    describe_lines,
    draw_frame,
    draw_hud,
    font_scale,
    label_lines,
    read_sidecar,
)
from sentinel.ingest.offline import _slug, resolve


def test_pts_key_is_stable_across_float_noise():
    """Two decoders reporting the same millisecond must land in the same bucket.

    CAP_PROP_POS_MSEC comes back as a double; comparing it exactly between
    passes is a coin flip, and a missed key silently drops a frame's boxes.
    """
    assert _key(1.234) == _key(1.2340000001)
    assert _key(1.234) != _key(1.235)


def test_sidecar_round_trips(tmp_path: Path):
    """Two header rows, because the rate the pass ran at is only known once it
    has finished -- and the annotate pass has to decode at the same rate or it
    selects different frames and the join silently goes sparse."""
    path = tmp_path / "clip.jsonl"
    path.write_text(
        json.dumps({"header": {"camera_id": "VID-clip", "start_at": "2026-09-07T09:00:00+00:00"}})
        + "\n"
        + json.dumps({"pts": 0.1, "i": 1, "at": "x", "boxes": [
            {"t": "run:0:1", "b": [10, 20, 40, 60], "c": "car", "s": 0.9}]})
        + "\n"
        + json.dumps({"header": {"target_fps": 12.5}})
        + "\n",
        encoding="utf-8",
    )
    header, frames = read_sidecar(path)
    assert header["camera_id"] == "VID-clip"
    assert header["target_fps"] == 12.5
    assert list(frames) == [_key(0.1)]
    assert frames[_key(0.1)]["boxes"][0]["c"] == "car"


def test_draw_frame_marks_the_box_it_was_given():
    """The drawn rectangle has to land on the coordinates the sidecar named."""
    image = np.zeros((120, 200, 3), dtype=np.uint8)
    boxes = [{"t": "run:0:1", "b": [40, 50, 90, 100], "c": "car", "s": 0.8}]

    drawn = draw_frame(image, boxes, {})

    assert drawn == 1
    # The border is painted; the middle of the box and the far corner are not.
    assert image[50, 60].any(), "top edge of the box was not drawn"
    assert not image[75, 65].any(), "the box was filled instead of outlined"
    assert not image[110, 190].any(), "something was drawn outside the box"


def test_draw_frame_clips_to_the_frame():
    """A Kalman-predicted box can sit partly outside the image. Drawing it
    must not raise, and a box entirely outside must not be drawn at all."""
    image = np.zeros((60, 60, 3), dtype=np.uint8)

    assert draw_frame(image, [{"t": "a", "b": [-30, -30, 20, 20], "c": "car"}], {}) == 1
    assert draw_frame(image, [{"t": "b", "b": [900, 900, 950, 950], "c": "car"}], {}) == 0


def test_match_tags_attach_by_read_id():
    labels = {"run:0:1": Label(track_id="run:0:1", read_id="abc"),
              "run:0:2": Label(track_id="run:0:2", read_id="def")}

    apply_matches(labels, {"matches": [
        {"match_id": "MATCH-1", "sightings": [{"read_id": "abc"}, {"read_id": "zzz"}]}
    ]})

    assert labels["run:0:1"].match == "MATCH-1"
    assert labels["run:0:2"].match is None


def test_hud_does_not_overflow_a_short_frame():
    """The HUD is anchored to the bottom edge; a tiny frame must not index
    negatively into the array or raise."""
    image = np.zeros((48, 160, 3), dtype=np.uint8)
    draw_hud(image, camera_id="VID-x", when="09:00:00", index=1, total=1,
             stub_note="STUB MODELS: detect")
    assert image.any()


# -- resolving files to cameras ---------------------------------------------


def test_slug_survives_awkward_filenames():
    assert _slug("cctv052x2004080516x01638") == "VID-cctv052x2004080516x01638"
    assert _slug("cam 01 - north (raw)") == "VID-cam-01-north-raw"


def test_default_spacing_is_a_plausible_speed():
    """Synthetic cameras must be far enough apart in space and time that a
    route between them survives the plausibility check in correlation.routes.
    2 km in 180 s is 40 km/h; the window is [2, 140]."""
    specs = resolve([Path("/tmp/a.mp4"), Path("/tmp/b.mp4")],
                    epoch=datetime(2026, 9, 7, 9, 0, tzinfo=UTC))

    gap_s = (specs[1].start_at - specs[0].start_at).total_seconds()
    km = (specs[1].lat - specs[0].lat) * 111.32
    speed = km / (gap_s / 3600.0)

    assert 2.0 < speed < 140.0, f"{speed:.1f} km/h is outside the plausibility window"
    assert all(s.synthetic_position for s in specs)


def test_manifest_overrides_field_by_field():
    """Giving a position must not cost you the staggered start times."""
    specs = resolve(
        [Path("/tmp/a.mp4"), Path("/tmp/b.mp4")],
        {"videos": [{"path": "a.mp4", "camera_id": "cam01", "lat": 23.01, "lon": 72.55}]},
        epoch=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
    )

    assert specs[0].camera_id == "cam01"
    assert (specs[0].lat, specs[0].lon) == (23.01, 72.55)
    assert specs[0].synthetic_position is False
    assert specs[1].synthetic_position is True
    assert specs[0].start_at != specs[1].start_at


def test_colliding_camera_ids_are_refused():
    """sightings is UNIQUE (camera_id, track_id). Two files on one camera
    means the second one's tracks disappear through ON CONFLICT DO NOTHING --
    silently, which is the worst way to lose half a run."""
    with pytest.raises(ValueError, match="same camera id"):
        resolve([Path("/a/clip.mp4"), Path("/b/clip.mp4")])


# -- what the description pipeline produces, on the frame --------------------


def test_description_is_drawn_in_full():
    """Attributes, caption and plate are four different claims and all of them
    belong on the video -- the attributes are what search filters on, and the
    caption is the only place a roof carrier ever appears."""
    lines = describe_lines({
        "colour": "white", "vtype": "hatchback", "class": "car",
        "make": "Maruti", "model": "Swift",
        "caption": "a white hatchback with a roof carrier",
        "features": [], "plate_text": "GJ01AB1234", "plate_conf": 0.71,
    })

    assert lines[0] == "white hatchback · Maruti Swift"
    assert "roof carrier" in lines[1]
    assert lines[-1].startswith("GJ01AB1234")
    assert "0.71" in lines[-1], "a plate without its confidence invites false trust"


def test_a_long_caption_is_truncated_not_wrapped():
    """A label stack that covers the vehicle it describes is worse than none."""
    lines = describe_lines({"class": "car", "caption": "x" * 400})

    assert max(len(line) for line in lines) <= CAPTION_CHARS


def test_stub_captions_are_left_marked():
    """The stub prefixes its output; erasing that on the way to the screen
    would make a placeholder look like a measurement."""
    lines = describe_lines({"class": "car", "caption": "[stub] a grey car"})

    assert any("[stub]" in line for line in lines)


def test_a_sighting_with_no_description_still_names_its_class():
    """Nothing has run yet, or the camera permits no attributes. Say the one
    thing that is known rather than drawing a blank plate."""
    assert describe_lines({"class": "motorcycle"}) == ["motorcycle"]


def test_features_the_caption_already_covers_are_not_repeated():
    lines = describe_lines({
        "class": "car", "caption": "a white car with a roof carrier",
        "features": ["roof carrier", "towbar"],
    })

    assert not any(line.startswith("+ roof carrier") for line in lines)
    assert any("towbar" in line for line in lines)


def test_labels_stay_inside_the_frame():
    """A vehicle at the right-hand edge with a long caption must not push text
    off the image -- OpenCV clips silently and the caption just vanishes."""
    image = np.zeros((120, 200, 3), dtype=np.uint8)
    text = "a very long caption indeed, about a car"
    labels = {"t1": Label(track_id="t1", lines=[text])}
    box_x1 = 180

    draw_frame(image, [{"t": "t1", "b": [box_x1, 60, 198, 90], "c": "car"}], labels)

    painted = np.where(image.any(axis=(0, 2)))[0]
    # The label was slid left of the box it belongs to, and the whole of it
    # is on the image rather than clipped off the right edge.
    assert painted.min() < box_x1
    assert painted.max() <= image.shape[1] - 1


def test_overlap_test_is_not_off_by_one():
    """The whole crowding fix rests on this. Touching edges do not overlap;
    a single shared pixel does."""
    assert _overlaps((0, 0, 10, 10), [(10, 0, 20, 10)]) is False, "edge-to-edge is not overlap"
    assert _overlaps((0, 0, 10, 10), [(9, 9, 20, 20)]) is True
    assert _overlaps((0, 0, 10, 10), []) is False
    assert _overlaps((5, 5, 6, 6), [(0, 0, 100, 100)]) is True, "contained is overlap"


def test_the_caption_is_sacrificed_before_the_violation():
    """A label stack that will not fit is trimmed from the least important
    end. `_text_block` grows upward, so the list runs least-to-most important
    and trimming takes from the front."""
    label = Label(
        track_id="t1",
        lines=["silver sedan", "a silver sedan with a carrier", "+ towbar", "GJ01AB1234"],
        match="MATCH-2", violations=["no_helmet"], pending=["plate"],
    )

    texts = [text for text, _ in label_lines({"c": "car"}, label, (1, 1, 1))]

    assert texts[-1] == "NO HELMET", "a violation must be the last thing dropped"
    assert texts[-2] == "MATCH-2"
    assert texts.index("silver sedan") > texts.index("a silver sedan with a carrier")
    assert texts[0].startswith("…"), "the pending note should go first"


def test_a_crowded_frame_still_draws_every_box():
    """Dense traffic is the normal case. Labels may be trimmed or dropped,
    but a vehicle never loses its box because its neighbour got there first."""
    image = np.zeros((300, 400, 3), dtype=np.uint8)
    full = ["silver sedan · toyota glanza", "a silver sedan with a roof carrier",
            "+ towbar", "GJ01AB1234  0.71"]
    boxes = [{"t": f"t{i}", "b": [40, 150 + i * 4, 200, 170 + i * 4], "c": "car"}
             for i in range(6)]
    labels = {f"t{i}": Label(track_id=f"t{i}", lines=list(full)) for i in range(6)}

    assert draw_frame(image, boxes, labels) == 6


def test_font_scales_with_the_frame():
    """320x240 to 2560x1440 is the real spread. One fixed size is either
    unreadable on the big frames or covers the road on the small ones."""
    assert font_scale(320) < font_scale(1280) < font_scale(1920)
    assert 0.34 <= font_scale(160)
    assert font_scale(4000) <= 0.80


def test_a_label_that_does_not_fit_above_is_drawn_below():
    """Traffic cameras look down, so the interesting vehicles are near the top
    of the frame -- exactly where a six-line description would be drawn off
    the image. OpenCV clips that in silence."""
    image = np.zeros((200, 240, 3), dtype=np.uint8)
    labels = {"t1": Label(
        track_id="t1",
        lines=["white hatchback · Maruti Swift", "a white hatchback with a carrier",
               "+ towbar", "GJ01AB1234  0.71"],
    )}

    # Box hard against the top edge: nothing fits above it.
    draw_frame(image, [{"t": "t1", "b": [20, 2, 90, 40], "c": "car"}], labels)

    assert image[60:, :].any(), "the label was not moved below the box"


def test_a_caption_that_only_restates_the_attributes_is_dropped():
    """The stub captioner writes the attribute fields back out as a sentence.
    Drawn as well as the attributes it is the same words twice, on a label
    that is already competing for room."""
    lines = describe_lines({
        "colour": "silver", "vtype": "suv", "make": "maruti", "model": "wagonr",
        "caption": "[stub] silver maruti wagonr suv",
    })

    assert lines == ["silver suv · maruti wagonr"]


def test_a_caption_that_says_something_new_is_kept():
    """The whole reason free text exists: a roof rack is in no column."""
    lines = describe_lines({
        "colour": "silver", "vtype": "bus", "caption": "[stub] silver bus with roof rack",
    })

    assert len(lines) == 2
    assert "roof rack" in lines[1]
