from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from sentinel.api.deps import DbConn, User

router = APIRouter(prefix="/traffic", tags=["traffic"])


@router.get("/state")
async def state(
    conn: DbConn,
    user: User,
    camera_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    usable_only: bool = Query(
        True, description="hide buckets below 60 percent observation coverage"
    ),
    limit: int = Query(500, le=5000),
):
    """Occupancy, density and observation coverage per bucket.

    usable_only reads the traffic_state_usable view, which hides buckets
    below sixty percent coverage. Below that the numbers are not comparable
    to anything, and the dashboard should show a gap rather than draw a
    reassuring line through a dead camera.

    Turn it off to see the dead buckets themselves, which is what you want
    when diagnosing a camera rather than reading traffic.
    """
    table = "traffic_state_usable" if usable_only else "traffic_state"
    rows = await conn.fetch(
        f"""
        SELECT *, frames_decoded::real / frames_expected AS coverage
          FROM {table}
         WHERE ($1::text IS NULL OR camera_id = $1)
           AND ($2::timestamptz IS NULL OR bucket_start >= $2)
           AND ($3::timestamptz IS NULL OR bucket_start <= $3)
         ORDER BY bucket_start DESC
         LIMIT $4
        """,  # noqa: S608 - table name is from a fixed two-value branch
        camera_id, since, until, limit,
    )
    return [dict(r) for r in rows]


@router.get("/counts")
async def counts(
    conn: DbConn,
    user: User,
    camera_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(2000, le=20000),
):
    """Vehicle counts per class per bucket, joined to their coverage.

    Every count comes back with the coverage of its bucket attached. A count
    read without its coverage is not interpretable: a camera that was down
    for four of five minutes reports a low number, and a low number is
    indistinguishable from a quiet road.

    mean_dwell_s is time in frame. Without camera calibration that is a
    slowness proxy -- comparable to itself over time at one camera, not
    across cameras -- and it is labelled that way rather than called speed.
    """
    rows = await conn.fetch(
        """
        SELECT c.*, s.frames_expected, s.frames_decoded, s.los,
               s.frames_decoded::real / s.frames_expected AS coverage
          FROM traffic_counts c
          JOIN traffic_state s
            ON s.camera_id = c.camera_id AND s.bucket_start = c.bucket_start
         WHERE ($1::text IS NULL OR c.camera_id = $1)
           AND ($2::timestamptz IS NULL OR c.bucket_start >= $2)
           AND ($3::timestamptz IS NULL OR c.bucket_start <= $3)
         ORDER BY c.bucket_start DESC, c.class
         LIMIT $4
        """,
        camera_id, since, until, limit,
    )
    return [dict(r) for r in rows]


@router.get("/congestion")
async def congestion(conn: DbConn, user: User, limit: int = Query(100, le=500)):
    """Recent heavy and jam bands, from the partial index."""
    rows = await conn.fetch(
        """
        SELECT s.camera_id, c.name, s.bucket_start, s.los, s.density_vpkm,
               s.mean_occupancy, s.peak_occupancy,
               s.frames_decoded::real / s.frames_expected AS coverage,
               ST_Y(c.location::geometry) AS lat, ST_X(c.location::geometry) AS lon
          FROM traffic_state s JOIN cameras c ON c.camera_id = s.camera_id
         WHERE s.los IN ('heavy','jam')
           AND s.frames_decoded::real / s.frames_expected >= 0.6
         ORDER BY s.bucket_start DESC LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]
