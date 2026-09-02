from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sentinel.api.deps import DbConn, DbTxn, User
from sentinel.core.models import VehicleDescription
from sentinel.correlation import search as search_lib
from sentinel.correlation import watchlist

router = APIRouter(tags=["search"])


class RegistrationSearch(BaseModel):
    registration_no: str
    since: datetime | None = None
    until: datetime | None = None


class DescriptionSearch(BaseModel):
    colour: str | None = None
    vtype: str | None = None
    make: str | None = None
    model: str | None = None
    caption_query: str | None = None
    district: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    camera_ids: list[str] | None = None


@router.post("/search/registration")
async def by_registration(body: RegistrationSearch, conn: DbTxn, user: User):
    """Search by registration number.

    VAHAN turns the number into a description, and tells us how many vehicles
    match it. That count travels with every result: it is the difference
    between a match meaning something and a match meaning nothing.
    """
    result = await search_lib.by_registration(
        conn, body.registration_no, since=body.since, until=body.until,
        requested_by=uuid.UUID(user.id),
    )
    return result.as_dict()


@router.post("/search/description")
async def by_description(body: DescriptionSearch, conn: DbTxn, user: User):
    """Search by description, or by free text over the generated captions.

    caption_query goes to the caption indexes, which can express things the
    four attribute columns cannot -- "white hatchback with a roof carrier"
    has no column.
    """
    description = VehicleDescription(
        colour=body.colour, vtype=body.vtype, make=body.make, model=body.model,
        caption_query=body.caption_query, district=body.district,
    )
    result = await search_lib.run(
        conn, description, since=body.since, until=body.until,
        camera_ids=body.camera_ids, requested_by=uuid.UUID(user.id),
        district=body.district,
    )
    return result.as_dict()


@router.get("/searches/{search_id}/routes")
async def routes_for_search(search_id: uuid.UUID, conn: DbConn, user: User):
    """Ranked routes, with the competing count always visible.

    If forty routes are physically consistent, forty come back. Picking one
    and calling it the answer would invent certainty the evidence does not
    contain.
    """
    rows = await conn.fetch(
        """
        SELECT id, rank, score, competing_count, rarity_count,
               plate_anchored, min_trust, created_at
          FROM routes WHERE search_id = $1 ORDER BY rank
        """,
        search_id,
    )
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no routes for that search")
    return [dict(r) for r in rows]


@router.get("/routes/{route_id}")
async def route_detail(route_id: uuid.UUID, conn: DbConn, user: User):
    """One route: every leg, every sighting, and the gaps.

    Legs with seq < 0 are the ones we REJECTED, kept with their reason.
    'Why was this link dropped' is an audit answer, so they are returned
    rather than hidden.
    """
    route = await conn.fetchrow("SELECT * FROM routes WHERE id = $1", route_id)
    if route is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such route")

    legs = await conn.fetch(
        """
        SELECT l.*,
               fs.camera_id AS from_camera, fs.seen_at AS from_seen_at,
               ts.camera_id AS to_camera,   ts.seen_at AS to_seen_at,
               ST_Y(fc.location::geometry) AS from_lat,
               ST_X(fc.location::geometry) AS from_lon,
               ST_Y(tc.location::geometry) AS to_lat,
               ST_X(tc.location::geometry) AS to_lon
          FROM route_legs l
          JOIN sightings fs ON fs.read_id = l.from_read_id
          JOIN sightings ts ON ts.read_id = l.to_read_id
          JOIN cameras fc ON fc.camera_id = fs.camera_id
          JOIN cameras tc ON tc.camera_id = ts.camera_id
         WHERE l.route_id = $1
         ORDER BY l.seq
        """,
        route_id,
    )

    return {
        "route": dict(route),
        "legs": [dict(r) for r in legs if r["seq"] > 0],
        "rejected_legs": [dict(r) for r in legs if r["seq"] < 0],
        "gaps": [
            {"seq": r["seq"], "gap_s": r["gap_s"],
             "from_camera": r["from_camera"], "to_camera": r["to_camera"]}
            for r in legs if r["seq"] > 0 and r["gap_s"]
        ],
    }


class WatchlistEntryIn(BaseModel):
    label: str
    reason: str
    registration_no: str | None = None
    colour: str | None = None
    vtype: str | None = None
    make: str | None = None
    model: str | None = None
    priority: str = "review"
    backfill_days: int = 15


@router.post("/watchlist", status_code=status.HTTP_201_CREATED)
async def add_watchlist_entry(body: WatchlistEntryIn, conn: DbTxn, user: User):
    """Add an entry, then immediately search everything already recorded.

    The watchlist works both ways: new detections check against it, and a new
    entry checks against history. Adding an entry without the backfill would
    silently discard everywhere the vehicle has already been.
    """
    entry_id = await conn.fetchval(
        """
        INSERT INTO watchlist_entries
            (label, registration_no, colour, vtype, make, model, priority,
             reason, created_by)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id
        """,
        body.label, body.registration_no, body.colour, body.vtype, body.make,
        body.model, body.priority, body.reason, uuid.UUID(user.id),
    )
    report = await watchlist.backfill(
        conn, entry_id, lookback_days=body.backfill_days,
        requested_by=uuid.UUID(user.id),
    )
    return {"entry_id": str(entry_id), "backfill": report}


@router.get("/watchlist")
async def list_watchlist(conn: DbConn, user: User):
    rows = await conn.fetch(
        """
        SELECT w.id, w.label, w.registration_no, w.colour, w.vtype, w.make,
               w.model, w.priority, w.reason, w.active, w.created_at,
               u.display_name AS created_by_name
          FROM watchlist_entries w JOIN users u ON u.id = w.created_by
         WHERE w.active ORDER BY w.created_at DESC
        """
    )
    return [dict(r) for r in rows]
