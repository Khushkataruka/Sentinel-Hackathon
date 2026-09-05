"""Camera ids survive a rename; positions do not get re-surveyed.

The grid renumbered "1".."30" to "cam01".."cam30" mid-project. A renamed id
is indistinguishable from an unknown camera, so without canonical matching
every camera is skipped for "no coordinate" and the sync reports success
having onboarded nothing -- the quietest possible failure.
"""

from __future__ import annotations

import json

from sentinel.registry.catalogue import _canonical_id, load_coordinates


def test_the_grids_two_id_forms_canonicalise_together():
    assert _canonical_id("cam01") == _canonical_id("1") == "1"
    assert _canonical_id("CAM-30") == _canonical_id("30") == "30"
    assert _canonical_id("01") == "1"


def test_distinct_cameras_stay_distinct():
    assert _canonical_id("cam01") != _canonical_id("cam10")
    assert _canonical_id("cam2") != _canonical_id("cam20")


def test_an_id_with_no_digits_matches_itself():
    assert _canonical_id("junction-west") == "junction-west"
    assert _canonical_id(" Junction-West ") == "junction-west"


def test_positions_are_found_under_both_forms(tmp_path):
    path = tmp_path / "coords.json"
    path.write_text(json.dumps({"cameras": [
        {"id": "1", "lat": 23.0125, "lon": 72.568, "quality": "approx"},
        {"id": "2", "lat": 23.02, "lon": 72.57, "quality": "ok"},
    ]}))
    coords = load_coordinates(path)
    assert coords["1"]["lat"] == 23.0125                    # the literal id
    assert coords[_canonical_id("cam01")]["lat"] == 23.0125  # after the rename
    assert coords[_canonical_id("cam02")]["quality"] == "ok"


def test_a_literal_id_is_never_shadowed_by_another_cameras_alias(tmp_path):
    """'01' and '1' both canonicalise to '1'. The real row wins."""
    path = tmp_path / "coords.json"
    path.write_text(json.dumps({"cameras": [
        {"id": "01", "lat": 1.0, "lon": 1.0},
        {"id": "1", "lat": 2.0, "lon": 2.0},
    ]}))
    coords = load_coordinates(path)
    assert coords["01"]["lat"] == 1.0
    assert coords["1"]["lat"] == 2.0


def test_entries_without_a_position_are_not_indexed(tmp_path):
    path = tmp_path / "coords.json"
    path.write_text(json.dumps({"cameras": [
        {"id": "7", "lat": None, "lon": None},
        {"id": "8", "lat": 23.0, "lon": 72.0},
    ]}))
    coords = load_coordinates(path)
    assert "7" not in coords
    assert "8" in coords


def test_the_shipped_coordinate_file_covers_the_current_grid_ids():
    """The estate as shipped: 29 of the 30 cameras have a position."""
    coords = load_coordinates("db/seed/camera_coordinates.json")
    found = [f"cam{n:02d}" for n in range(1, 31)
             if _canonical_id(f"cam{n:02d}") in coords]
    assert len(found) == 29
    assert "cam30" not in found      # known gap, recorded in the README
