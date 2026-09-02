"""Data access for the registry. Raw SQL against asyncpg.

The schema uses geography, arrays, enums, partial indexes and triggers.
An ORM would fight all five, so queries are written out. They are boring
on purpose: a reviewer can read the SQL and the design document side by
side and see the same column names.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import asyncpg
from sentinel.core.models import (
    Adapter,
    Camera,
    CameraIn,
    CameraProfile,
    CameraProfileIn,
    Department,
    HealthCheck,
)

# Cameras are stored with a geography column; every read projects it back to
# plain lat/lon so callers never see EWKB.
CAMERA_COLUMNS = """
    c.camera_id, c.department_id, c.name, c.kind, c.vendor, c.protocol,
    c.adapter_id, ST_Y(c.location::geometry) AS lat, ST_X(c.location::geometry) AS lon,
    c.bearing_deg, c.range_m, c.storage_kind, c.retention_days,
    c.contract_expiry, c.enabled, c.created_at, c.updated_at
"""


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


async def list_departments(conn: asyncpg.Connection) -> list[Department]:
    rows = await conn.fetch("SELECT * FROM departments ORDER BY code")
    return [Department(**dict(r)) for r in rows]


async def create_department(conn: asyncpg.Connection, dept: Department) -> Department:
    row = await conn.fetchrow(
        "INSERT INTO departments (code, name) VALUES ($1, $2) RETURNING *",
        dept.code,
        dept.name,
    )
    return Department(**dict(row))


async def get_department_by_code(conn: asyncpg.Connection, code: str) -> Department | None:
    row = await conn.fetchrow("SELECT * FROM departments WHERE code = $1", code)
    return Department(**dict(row)) if row else None


# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------


async def list_cameras(
    conn: asyncpg.Connection,
    *,
    department_id: int | None = None,
    enabled: bool | None = None,
    near: tuple[float, float, float] | None = None,   # lat, lon, radius_m
    limit: int = 500,
    offset: int = 0,
) -> list[Camera]:
    where, args = ["TRUE"], []
    if department_id is not None:
        args.append(department_id)
        where.append(f"c.department_id = ${len(args)}")
    if enabled is not None:
        args.append(enabled)
        where.append(f"c.enabled = ${len(args)}")
    if near is not None:
        lat, lon, radius = near
        args.extend([lon, lat, radius])
        where.append(
            f"ST_DWithin(c.location, ST_MakePoint(${len(args)-2}, ${len(args)-1})::geography,"
            f" ${len(args)})"
        )
    args.extend([limit, offset])
    rows = await conn.fetch(
        f"SELECT {CAMERA_COLUMNS} FROM cameras c WHERE {' AND '.join(where)} "
        f"ORDER BY c.camera_id LIMIT ${len(args)-1} OFFSET ${len(args)}",
        *args,
    )
    return [Camera(**dict(r)) for r in rows]


async def get_camera(conn: asyncpg.Connection, camera_id: str) -> Camera | None:
    row = await conn.fetchrow(
        f"SELECT {CAMERA_COLUMNS} FROM cameras c WHERE c.camera_id = $1", camera_id
    )
    return Camera(**dict(row)) if row else None


async def upsert_camera(conn: asyncpg.Connection, cam: CameraIn) -> Camera:
    """Insert or update by camera_id.

    Upsert rather than insert because the Sentinel catalogue is re-read
    periodically and its camera set changes; a sync must not fail on a
    camera it has already seen.
    """
    row = await conn.fetchrow(
        f"""
        INSERT INTO cameras (camera_id, department_id, name, kind, vendor, protocol,
                             adapter_id, location, bearing_deg, range_m, storage_kind,
                             retention_days, contract_expiry, enabled)
        VALUES ($1, $2, $3, $4, $5, $6, $7,
                ST_MakePoint($8, $9)::geography, $10, $11, $12, $13, $14, $15)
        ON CONFLICT (camera_id) DO UPDATE SET
            department_id = EXCLUDED.department_id,
            name          = EXCLUDED.name,
            kind          = EXCLUDED.kind,
            vendor        = EXCLUDED.vendor,
            protocol      = EXCLUDED.protocol,
            adapter_id    = EXCLUDED.adapter_id,
            location      = EXCLUDED.location,
            bearing_deg   = EXCLUDED.bearing_deg,
            range_m       = EXCLUDED.range_m,
            storage_kind  = EXCLUDED.storage_kind,
            retention_days= EXCLUDED.retention_days,
            contract_expiry = EXCLUDED.contract_expiry,
            enabled       = EXCLUDED.enabled
        RETURNING {CAMERA_COLUMNS.replace('c.', '')}
        """,
        cam.camera_id, cam.department_id, cam.name, cam.kind.value, cam.vendor,
        cam.protocol, cam.adapter_id, cam.lon, cam.lat, cam.bearing_deg, cam.range_m,
        cam.storage_kind, cam.retention_days, cam.contract_expiry, cam.enabled,
    )
    return Camera(**dict(row))


async def set_camera_enabled(conn: asyncpg.Connection, camera_id: str, enabled: bool) -> bool:
    result = await conn.execute(
        "UPDATE cameras SET enabled = $2 WHERE camera_id = $1", camera_id, enabled
    )
    return result.endswith("1")


# ---------------------------------------------------------------------------
# Capability profiles -- the section 6 survey result
# ---------------------------------------------------------------------------

PROFILE_COLUMNS = """
    camera_id, resolution_class, permitted_attributes, permitted_violations,
    plate_viable, density_viable, ST_AsText(lane_polygon) AS lane_polygon_wkt,
    lane_count, decode_fps, deinterlace, distortion, trust_level,
    corridor_group, loop_period_s, measured_at, updated_at
"""


async def get_profile(conn: asyncpg.Connection, camera_id: str) -> CameraProfile | None:
    row = await conn.fetchrow(
        f"SELECT {PROFILE_COLUMNS} FROM camera_profiles WHERE camera_id = $1", camera_id
    )
    return CameraProfile(**dict(row)) if row else None


async def upsert_profile(
    conn: asyncpg.Connection, camera_id: str, profile: CameraProfileIn
) -> CameraProfile:
    """Write the survey result.

    measured_at is bumped on every write, because 'when was this camera last
    surveyed' is the question a reviewer asks when they want to know why we
    trust a sighting.
    """
    row = await conn.fetchrow(
        f"""
        INSERT INTO camera_profiles (
            camera_id, resolution_class, permitted_attributes, permitted_violations,
            plate_viable, density_viable, lane_polygon, lane_count, decode_fps,
            deinterlace, distortion, trust_level, corridor_group, loop_period_s,
            measured_at)
        VALUES ($1, $2, $3, $4, $5, $6,
                CASE WHEN $7::text IS NULL THEN NULL ELSE ST_GeomFromText($7) END,
                $8, $9, $10, $11, $12, $13, $14, now())
        ON CONFLICT (camera_id) DO UPDATE SET
            resolution_class     = EXCLUDED.resolution_class,
            permitted_attributes = EXCLUDED.permitted_attributes,
            permitted_violations = EXCLUDED.permitted_violations,
            plate_viable         = EXCLUDED.plate_viable,
            density_viable       = EXCLUDED.density_viable,
            lane_polygon         = EXCLUDED.lane_polygon,
            lane_count           = EXCLUDED.lane_count,
            decode_fps           = EXCLUDED.decode_fps,
            deinterlace          = EXCLUDED.deinterlace,
            distortion           = EXCLUDED.distortion,
            trust_level          = EXCLUDED.trust_level,
            corridor_group       = EXCLUDED.corridor_group,
            loop_period_s        = EXCLUDED.loop_period_s,
            measured_at          = now()
        RETURNING {PROFILE_COLUMNS}
        """,
        camera_id, profile.resolution_class.value, profile.permitted_attributes,
        profile.permitted_violations, profile.plate_viable, profile.density_viable,
        profile.lane_polygon_wkt, profile.lane_count, profile.decode_fps,
        profile.deinterlace, profile.distortion, profile.trust_level,
        profile.corridor_group, profile.loop_period_s,
    )
    return CameraProfile(**dict(row))


async def cameras_needing_survey(conn: asyncpg.Connection) -> list[str]:
    """Cameras with no profile at all. These produce nothing: every pipeline
    gate reads the profile, and a missing profile permits nothing."""
    rows = await conn.fetch(
        """
        SELECT c.camera_id FROM cameras c
        LEFT JOIN camera_profiles p USING (camera_id)
        WHERE p.camera_id IS NULL AND c.enabled
        ORDER BY c.camera_id
        """
    )
    return [r["camera_id"] for r in rows]


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


async def list_adapters(conn: asyncpg.Connection) -> list[Adapter]:
    rows = await conn.fetch("SELECT * FROM adapters ORDER BY name")
    return [Adapter(**dict(r)) for r in rows]


async def upsert_adapter(conn: asyncpg.Connection, adapter: Adapter) -> Adapter:
    row = await conn.fetchrow(
        """
        INSERT INTO adapters (name, driver, config, status, last_error, tested_at)
        VALUES ($1, $2, $3, $4, $5, now())
        ON CONFLICT (name) DO UPDATE SET
            driver = EXCLUDED.driver, config = EXCLUDED.config,
            status = EXCLUDED.status, last_error = EXCLUDED.last_error,
            tested_at = now()
        RETURNING *
        """,
        adapter.name, adapter.driver, adapter.config,
        adapter.status.value, adapter.last_error,
    )
    return Adapter(**dict(row))


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def record_health(conn: asyncpg.Connection, check: HealthCheck) -> None:
    """Append one check. The table is append-only by convention: history is
    the point, because a camera that has been silent for six hours is only
    visible as a run of rows."""
    await conn.execute(
        """
        INSERT INTO camera_health
            (camera_id, reachable, last_frame_at, decode_fps, detections_1h, verdict, detail)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        check.camera_id, check.reachable, check.last_frame_at, check.decode_fps,
        check.detections_1h, check.verdict, check.detail,
    )


async def latest_health(
    conn: asyncpg.Connection, camera_id: str | None = None
) -> list[dict[str, Any]]:
    if camera_id:
        rows = await conn.fetch(
            "SELECT * FROM camera_latest_health WHERE camera_id = $1", camera_id
        )
    else:
        rows = await conn.fetch("SELECT * FROM camera_latest_health ORDER BY camera_id")
    return [dict(r) for r in rows]


async def health_history(
    conn: asyncpg.Connection, camera_id: str, since: datetime | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT * FROM camera_health
         WHERE camera_id = $1 AND ($2::timestamptz IS NULL OR checked_at >= $2)
         ORDER BY checked_at DESC LIMIT $3
        """,
        camera_id, since, limit,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Access grants
# ---------------------------------------------------------------------------


async def list_grants(conn: asyncpg.Connection, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT g.*, d.code AS department_code
          FROM access_grants g JOIN departments d ON d.id = g.department_id
         WHERE g.user_id = $1 AND g.revoked_at IS NULL
           AND (g.expires_at IS NULL OR g.expires_at > now())
         ORDER BY g.created_at DESC
        """,
        user_id,
    )
    return [dict(r) for r in rows]


async def create_grant(
    conn: asyncpg.Connection,
    *,
    user_id: uuid.UUID,
    department_id: int,
    granted_by: uuid.UUID,
    reason: str,
    expires_at: datetime | None,
) -> int:
    return await conn.fetchval(
        """
        INSERT INTO access_grants (user_id, department_id, granted_by, reason, expires_at)
        VALUES ($1, $2, $3, $4, $5) RETURNING id
        """,
        user_id, department_id, granted_by, reason, expires_at,
    )


async def revoke_grant(conn: asyncpg.Connection, grant_id: int) -> bool:
    result = await conn.execute(
        "UPDATE access_grants SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL",
        grant_id,
    )
    return result.endswith("1")


async def visible_departments(conn: asyncpg.Connection, user_id: uuid.UUID) -> list[int]:
    """A user sees their own department by default; anything else needs a
    live grant."""
    rows = await conn.fetch(
        """
        SELECT department_id FROM users WHERE id = $1
        UNION
        SELECT department_id FROM access_grants
         WHERE user_id = $1 AND revoked_at IS NULL
           AND (expires_at IS NULL OR expires_at > now())
        """,
        user_id,
    )
    return [r["department_id"] for r in rows]
