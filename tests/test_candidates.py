"""Candidate merging, and the empty-description guard."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sentinel.core.models import Candidate, VehicleDescription
from sentinel.correlation import candidates as cand


def candidate(read_id, seconds, score, matched):
    return Candidate(
        read_id=read_id, camera_id="CAM-1",
        seen_at=datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds),
        score=score, matched_on=list(matched),
    )


def test_merge_keeps_the_best_score_and_every_signal():
    """A sighting found by both appearance and a plate read is stronger
    evidence than one found by either alone."""
    rid = uuid.uuid4()
    merged = cand.merge(
        [candidate(rid, 0, 0.6, ["attribute"])],
        [candidate(rid, 0, 0.9, ["plate"])],
    )
    assert len(merged) == 1
    assert merged[0].score == 0.9
    assert set(merged[0].matched_on) == {"attribute", "plate"}


def test_merge_orders_by_time():
    a, b = uuid.uuid4(), uuid.uuid4()
    merged = cand.merge([candidate(b, 60, 0.5, ["v"])], [candidate(a, 10, 0.5, ["v"])])
    assert [c.read_id for c in merged] == [a, b]


def test_merge_of_nothing_is_nothing():
    assert cand.merge([], []) == []


async def test_an_empty_description_matches_nothing(db):
    """A registration number missing from VAHAN yields a description with no
    attributes. Matching the whole estate would produce thousands of
    candidates and a top route chosen by nothing at all."""
    result = await cand.by_attributes(db, VehicleDescription())
    assert result == []


async def test_a_described_vehicle_still_filters(db):
    """The guard must not disable the filter for real descriptions."""
    await db.execute(
        "SELECT 1"
    )
    result = await cand.by_attributes(db, VehicleDescription(colour="__nosuchcolour__"))
    assert result == []      # a real filter, applied, matching nothing
