"""The watchlist payload is a trust boundary. An entry naming no vehicle would
never alert live, and its backfill would search every sighting on record."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sentinel.api.routers.search import WatchlistEntryIn


def test_an_entry_must_name_a_vehicle():
    with pytest.raises(ValidationError):
        WatchlistEntryIn(label="stolen", reason="FIR")
    assert WatchlistEntryIn(label="stolen", reason="FIR", colour="white").colour == "white"


def test_priority_must_be_an_alert_tier():
    with pytest.raises(ValidationError):
        WatchlistEntryIn(label="stolen", reason="FIR", colour="white", priority="urgent")
