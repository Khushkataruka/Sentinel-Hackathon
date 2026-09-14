from __future__ import annotations

from fastapi import APIRouter, Query
from sentinel.api.deps import DbConn, User

router = APIRouter(tags=["live_sightings"])

@router.get("/live_sightings")
async def get_sightings(
    conn: DbConn,
    user: User,
    limit: int = Query(50, le=200),
):
    """Fetch recent vehicle sightings."""
    rows = await conn.fetch(
        """
        SELECT s.read_id, s.camera_id, s.seen_at, s.colour, s.vtype AS type, s.make, s.model,
               s.bbox, s.crop_bbox, c.name AS camera_name,
               (SELECT plate FROM plate_hypotheses p WHERE p.read_id = s.read_id AND p.rank = 1 LIMIT 1) as best_plate
          FROM sightings s
          JOIN cameras c ON c.camera_id = s.camera_id
         ORDER BY s.seen_at DESC
         LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]
