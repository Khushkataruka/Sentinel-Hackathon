"""The watchlist, which works both ways.

A new detection is checked against the watchlist. A new watchlist entry is
checked against everything already recorded. The second is a separate piece
of code and the more expensive one, because there is no starting sighting to
work from -- it runs the whole search across the sightings table.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import asyncpg
from sentinel.core import audit
from sentinel.core.logging import get_logger
from sentinel.core.models import Candidate, VehicleDescription
from sentinel.core.types import AlertTier, SearchKind
from sentinel.correlation import plates, scoring, search

log = get_logger(__name__)


async def active_entries(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, label, registration_no, colour, vtype, make, model,
               priority, reason
          FROM watchlist_entries WHERE active ORDER BY created_at DESC
        """
    )
    return [dict(r) for r in rows]


def _attribute_match(entry: dict[str, Any], sighting: dict[str, Any]) -> float:
    """How well a sighting matches a watchlist entry on attributes alone.

    Returns the fraction of the entry's SPECIFIED attributes that agree. An
    entry that names only a colour is easy to match and will score low on
    rarity later, which is the correct outcome rather than something to
    compensate for here.
    """
    fields = ("colour", "vtype", "make", "model")
    specified = [f for f in fields if entry.get(f)]
    if not specified:
        return 0.0
    hits = sum(
        1 for f in specified
        if sighting.get(f) and str(sighting[f]).lower() == str(entry[f]).lower()
    )
    return hits / len(specified)


async def check_sighting(
    conn: asyncpg.Connection, read_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Check one completed sighting against every active watchlist entry.

    The live path. Cheap by design: it runs on every sighting the platform
    produces, so it does attribute comparison and plate matching in memory
    and does not build routes. Route building happens when an operator opens
    the alert.
    """
    sighting = await conn.fetchrow(
        """
        SELECT s.read_id, s.camera_id, s.seen_at, s.colour, s.vtype, s.make,
               s.model, s.plate_text, s.plate_conf,
               COALESCE(p.trust_level, 0.5) AS trust_level
          FROM sightings s
          LEFT JOIN camera_profiles p ON p.camera_id = s.camera_id
         WHERE s.read_id = $1
        """,
        read_id,
    )
    if sighting is None:
        return []

    row = dict(sighting)
    hypotheses = [
        r["plate"]
        for r in await conn.fetch(
            "SELECT plate FROM plate_hypotheses WHERE read_id = $1", read_id
        )
    ]

    raised: list[dict[str, Any]] = []

    for entry in await active_entries(conn):
        attribute_score = _attribute_match(entry, row)

        plate_score = 0.0
        if entry["registration_no"]:
            for candidate_plate in filter(None, [row.get("plate_text"), *hypotheses]):
                plate_score = max(
                    plate_score,
                    plates.similarity(entry["registration_no"], candidate_plate),
                )

        if plate_score < 0.82 and attribute_score < 1.0:
            continue

        # A plate match is an identity; an attribute match is a category.
        # They are weighted accordingly, and the camera's measured trust
        # multiplies both.
        score = max(plate_score, attribute_score * 0.55) * float(row["trust_level"])
        tier = scoring.tier(score, competing_count=1, plate_anchored=plate_score >= 0.82)

        # The entry's own priority can only raise the tier, never lower it:
        # an operator marking something priority is a decision, and the
        # scorer should not overrule it.
        if entry["priority"] == AlertTier.PRIORITY and tier is not AlertTier.DISMISS:
            tier = AlertTier.PRIORITY

        if tier is AlertTier.DISMISS:
            continue

        alert_id = await conn.fetchval(
            """
            INSERT INTO alerts (watchlist_entry_id, read_id, tier, score)
            VALUES ($1,$2,$3,$4) RETURNING id
            """,
            entry["id"], read_id, tier.value, score,
        )
        await audit.service(
            conn, "correlation", "alert.raise", "alert", str(alert_id),
            {
                "watchlist_entry": str(entry["id"]),
                "read_id": str(read_id),
                "tier": tier.value,
                "plate_score": round(plate_score, 4),
                "attribute_score": round(attribute_score, 4),
            },
        )
        raised.append(
            {"alert_id": str(alert_id), "entry": entry["label"], "tier": tier.value,
             "score": score}
        )

    return raised


async def backfill(
    conn: asyncpg.Connection,
    entry_id: uuid.UUID,
    *,
    lookback_days: int = 15,
    requested_by: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Search everything already recorded for a new watchlist entry.

    Add a vehicle to the watchlist now, and this shows everywhere a matching
    vehicle has been in what we have recorded. Retention is per camera, so
    the effective lookback varies -- the result says how far back it actually
    reached rather than implying a uniform window.
    """
    entry = await conn.fetchrow(
        "SELECT * FROM watchlist_entries WHERE id = $1 AND active", entry_id
    )
    if entry is None:
        raise ValueError(f"no active watchlist entry {entry_id}")

    description = VehicleDescription(
        registration_no=entry["registration_no"],
        colour=entry["colour"],
        vtype=entry["vtype"],
        make=entry["make"],
        model=entry["model"],
        embedding=entry["embedding"],
    )

    since = datetime.now(tz=None).astimezone() - timedelta(days=lookback_days)
    result = await search.run(
        conn, description, kind=SearchKind.WATCHLIST_BACKFILL,
        since=since, requested_by=requested_by, watchlist_entry_id=entry_id,
    )

    # One alert per route, tiered on the route's own score and its competing
    # count -- not one alert per sighting, which would bury the operator.
    raised = 0
    for route in result.routes:
        tier = scoring.tier(
            route["score"],
            competing_count=route["competing_count"],
            plate_anchored=route["plate_anchored"],
        )
        if tier is AlertTier.DISMISS:
            continue
        alert_id = await conn.fetchval(
            """
            INSERT INTO alerts (watchlist_entry_id, route_id, read_id, tier, score)
            VALUES ($1,$2,$3,$4,$5) RETURNING id
            """,
            entry_id, uuid.UUID(route["route_id"]),
            uuid.UUID(route["read_ids"][0]) if route["read_ids"] else None,
            tier.value, route["score"],
        )
        await audit.service(
            conn, "correlation", "alert.raise_backfill", "alert", str(alert_id),
            {"watchlist_entry": str(entry_id), "route_id": route["route_id"],
             "tier": tier.value},
        )
        raised += 1

    log.info("watchlist_backfill", entry=str(entry_id),
             routes=len(result.routes), alerts=raised)
    return {**result.as_dict(), "alerts_raised": raised,
            "lookback_days": lookback_days}


def candidates_from_alert(rows: list[dict[str, Any]]) -> list[Candidate]:
    """Rehydrate stored candidates for an evidence panel."""
    return [
        Candidate(
            read_id=r["read_id"], camera_id=r["camera_id"], seen_at=r["seen_at"],
            score=r.get("score", 0.0), matched_on=r.get("matched_on", []),
            plate_text=r.get("plate_text"), trust_level=r.get("trust_level", 0.5),
        )
        for r in rows
    ]
