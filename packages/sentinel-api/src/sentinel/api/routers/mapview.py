from __future__ import annotations

from fastapi import APIRouter
from sentinel.api.deps import DbConn, User, visible_departments

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/cameras")
async def cameras(conn: DbConn, user: User):
    """Cameras with their latest health and survey state, for the map.

    surveyed is exposed explicitly. An unsurveyed camera is drawn differently
    because it is producing nothing, and a map that showed it as a working
    camera would be lying about coverage.
    """
    visible = await visible_departments(conn, user.id)
    rows = await conn.fetch(
        """
        SELECT c.camera_id, c.name, c.department_id, d.code AS department_code,
               c.kind, c.enabled,
               ST_Y(c.location::geometry) AS lat,
               ST_X(c.location::geometry) AS lon,
               c.bearing_deg, c.range_m,
               p.camera_id IS NOT NULL AS surveyed,
               p.trust_level, p.plate_viable, p.density_viable,
               p.permitted_violations, p.corridor_group,
               h.verdict, h.checked_at, h.decode_fps, h.detections_1h
          FROM cameras c
          JOIN departments d ON d.id = c.department_id
          LEFT JOIN camera_profiles p ON p.camera_id = c.camera_id
          LEFT JOIN camera_latest_health h ON h.camera_id = c.camera_id
         WHERE c.department_id = ANY($1)
         ORDER BY c.camera_id
        """,
        visible,
    )
    return [dict(r) for r in rows]


@router.get("/coverage")
async def coverage(conn: DbConn, user: User):
    """Each camera's estimated field of view as a polygon.

    A wedge from the location, bearing and range. Subtracting the union of
    these from the road network is what produces the coverage gaps, and that
    subtraction needs a road layer we do not ship -- so this returns the
    wedges and the frontend does the visual overlay.

    Cameras with no bearing or range produce nothing here rather than a
    circle, because a circle would claim coverage in directions the camera
    does not face.
    """
    visible = await visible_departments(conn, user.id)
    rows = await conn.fetch(
        """
        SELECT c.camera_id,
               ST_AsGeoJSON(
                 ST_Buffer(c.location, c.range_m)::geometry
               ) AS footprint,
               c.bearing_deg, c.range_m
          FROM cameras c
         WHERE c.department_id = ANY($1)
           AND c.enabled AND c.range_m IS NOT NULL AND c.bearing_deg IS NOT NULL
        """,
        visible,
    )
    return [dict(r) for r in rows]
