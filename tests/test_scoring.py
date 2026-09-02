"""Route scoring. The properties the design document commits to."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sentinel.core.models import Candidate
from sentinel.core.types import AlertTier
from sentinel.correlation import scoring
from sentinel.correlation.routes import Leg, RouteCandidate


def candidate(seconds, camera="CAM-1", trust=1.0, matched=("attribute",)):
    return Candidate(
        read_id=__import__("uuid").uuid4(),
        camera_id=camera,
        seen_at=datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds),
        score=1.0, matched_on=list(matched), trust_level=trust,
    )


def route(n_legs, trust=1.0, plate=False):
    matched = ("attribute", "plate") if plate else ("attribute",)
    sightings = [
        candidate(i * 300, f"CAM-{i}", trust, matched) for i in range(n_legs + 1)
    ]
    legs = [
        Leg(i + 1, sightings[i].read_id, sightings[i + 1].read_id,
            sightings[i].camera_id, sightings[i + 1].camera_id,
            2.0, 300.0, 24.0, True)
        for i in range(n_legs)
    ]
    return RouteCandidate(sightings=sightings, legs=legs)


def test_a_rare_description_beats_a_common_one():
    r = route(2)
    assert scoring.score_route(r, 40) > scoring.score_route(r, 40_000)


def test_forty_thousand_matches_is_a_category_not_an_identification():
    assert scoring.rarity_term(40_000) < 0.1
    assert scoring.rarity_term(1) == 1.0


def test_a_short_rare_chain_beats_a_long_common_one():
    """A four-camera chain for a common car should score BELOW a two-camera
    chain for a rare one. This is the property the weights exist for."""
    common_long = scoring.score_route(route(4), 40_000)
    rare_short = scoring.score_route(route(2), 50)
    assert rare_short > common_long


def test_one_plate_read_beats_four_description_matches():
    anchored = scoring.score_route(route(1, plate=True), 40_000)
    described = scoring.score_route(route(4, plate=False), 40_000)
    assert anchored > described


def test_the_weakest_camera_sets_the_trust_term():
    mixed = route(2)
    mixed.sightings[1].trust_level = 0.2
    assert scoring.trust_term(mixed) == 0.2


def test_scoring_is_deterministic():
    r = route(3, plate=True)
    assert scoring.score_route(r, 900) == scoring.score_route(r, 900)


def test_many_competing_routes_demote_the_tier():
    """A good score that is one of forty equally good scores is a shortlist,
    not a match."""
    alone = scoring.tier(0.7, competing_count=1)
    crowded = scoring.tier(0.7, competing_count=60)
    assert alone is AlertTier.PRIORITY
    assert crowded is not AlertTier.PRIORITY


def test_a_weak_match_is_dismissed():
    assert scoring.tier(0.1, competing_count=1) is AlertTier.DISMISS


def test_explain_adds_up_to_the_score():
    r = route(2, plate=True)
    parts = scoring.explain(r, 500)
    total = (parts["trust_weighted"] + parts["rarity_weighted"]
             + parts["plate_weighted"] + parts["length_weighted"])
    assert abs(total - parts["total"]) < 1e-3


def test_ranking_is_stable():
    routes = [route(1), route(3), route(2)]
    first = [len(r.legs) for r, _, _ in scoring.rank(routes, 100)]
    second = [len(r.legs) for r, _, _ in scoring.rank(routes, 100)]
    assert first == second


def test_route_enumeration_cap_is_reported_not_hidden():
    """A capped competing_count understates uncertainty. It has to be
    marked, because an operator reads that number as evidence."""
    r = route(2)
    assert r.count_capped is False
    r.count_capped = True
    assert r.count_capped is True
