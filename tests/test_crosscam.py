"""Cross-camera correlation: what counts as a match, and what counts as one match.

The matching itself is `correlation.search`, tested elsewhere. What is new
here is the framing -- which routes are correlations at all, and how many
distinct ones a pile of independent searches actually describes.
"""

from __future__ import annotations

from sentinel.correlation.crosscam import dedupe, render_html


def route(read_ids, cameras, score, **extra):
    return {"read_ids": list(read_ids), "cameras": list(cameras), "score": score, **extra}


def test_single_camera_routes_are_not_correlations():
    """A route inside one camera is that camera seeing the vehicle twice.
    Real, and not the question a batch of videos asks."""
    kept = dedupe([
        route(["a", "b"], ["cam1", "cam1"], 0.9),
        route(["a", "c"], ["cam1", "cam2"], 0.4),
    ])

    assert [r["read_ids"] for r in kept] == [["a", "c"]]


def test_the_same_vehicle_found_twice_is_one_match():
    """Every seed searches on its own, so a vehicle in two videos is
    discovered from both ends. That is one match, and the better-scoring
    discovery of it is the one to keep."""
    kept = dedupe([
        route(["a", "b"], ["cam1", "cam2"], 0.55, seed_read_id="a"),
        route(["b", "a"], ["cam2", "cam1"], 0.71, seed_read_id="b"),
    ])

    assert len(kept) == 1
    assert kept[0]["score"] == 0.71
    assert kept[0]["seed_read_id"] == "b"


def test_distinct_vehicles_stay_distinct():
    kept = dedupe([
        route(["a", "b"], ["cam1", "cam2"], 0.5),
        route(["c", "d"], ["cam1", "cam2"], 0.8),
        route(["a", "b", "e"], ["cam1", "cam2", "cam3"], 0.6),
    ])

    assert len(kept) == 3
    assert [r["score"] for r in kept] == [0.8, 0.6, 0.5], "not ranked best-first"


def test_empty_report_says_why_rather_than_looking_broken():
    """A run with no matches is a normal outcome -- most often because the
    appearance model is a stub. A blank page reads as a failure."""
    page = render_html({"generated_at": "2026-09-07T09:00:00Z", "cameras": ["VID-a"],
                        "seeds": 3, "matches": []})

    assert "stub" in page.lower()
    assert "<html" in page


def test_report_shows_the_competing_count_and_marks_a_capped_one():
    """The design commits to never presenting a single answer: the count
    travels with the score, and a capped count is a floor, not a total."""
    page = render_html({
        "generated_at": "2026-09-07T09:00:00Z", "cameras": ["VID-a", "VID-b"],
        "seeds": 1,
        "matches": [{
            "match_id": "MATCH-1", "score": 0.63, "competing_count": 12,
            "competing_count_capped": True, "plate_anchored": False,
            "min_trust": 0.5, "rarity_count": 900, "cameras": ["VID-a", "VID-b"],
            "route_id": "r", "search_id": "s", "seed_read_id": "x", "explain": {},
            "legs": [{"seq": 1, "distance_km": 2.0, "elapsed_s": 180.0,
                      "required_speed_kmh": 40.0, "plausible": True,
                      "drop_reason": None, "gap_s": None}],
            "sightings": [
                {"read_id": "x", "camera_id": "VID-a", "seen_at": "2026-09-07T09:00:00",
                 "class": "car", "colour": "white", "crop_ref": None},
                {"read_id": "y", "camera_id": "VID-b", "seen_at": "2026-09-07T09:03:00",
                 "class": "car", "colour": "white", "crop_ref": None},
            ],
        }],
    })

    assert "MATCH-1" in page
    assert "12 competing" in page
    assert "capped" in page
    assert "40.0 km/h" in page


def test_report_escapes_model_output():
    """Captions and plate reads come out of a model and land in HTML."""
    page = render_html({
        "generated_at": "x", "cameras": ["VID-a"], "seeds": 1,
        "matches": [{
            "match_id": "MATCH-1", "score": 0.5, "competing_count": 1,
            "competing_count_capped": False, "plate_anchored": False,
            "min_trust": None, "rarity_count": None, "cameras": ["VID-a", "VID-b"],
            "route_id": "r", "search_id": "s", "seed_read_id": "x", "explain": {},
            "legs": [],
            "sightings": [{"read_id": "x", "camera_id": "VID-a", "seen_at": None,
                           "class": "car", "plate_text": "<script>alert(1)</script>",
                           "crop_ref": None}],
        }],
    })

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
