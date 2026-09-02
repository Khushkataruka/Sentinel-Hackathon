from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from sentinel.api.deps import DbConn, User
from sentinel.core import media

router = APIRouter(tags=["evidence"])


@router.get("/evidence/{read_id}")
async def evidence(read_id: uuid.UUID, conn: DbConn, user: User):
    """Everything behind one sighting.

    Every claim points at the camera, frame, timestamp and record it came
    from. The model ids are included: a claim has to be traceable to the
    weights that produced it, or the scoring is not replayable.
    """
    sighting = await conn.fetchrow(
        """
        SELECT s.*, c.name AS camera_name,
               ST_Y(c.location::geometry) AS lat, ST_X(c.location::geometry) AS lon,
               c.bearing_deg, p.trust_level, p.resolution_class,
               p.permitted_attributes, p.permitted_violations, p.measured_at
          FROM sightings s
          JOIN cameras c ON c.camera_id = s.camera_id
          LEFT JOIN camera_profiles p ON p.camera_id = s.camera_id
         WHERE s.read_id = $1
        """,
        read_id,
    )
    if sighting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such sighting")

    row = dict(sighting)
    row.pop("embedding", None)          # 2 KB of floats nobody is rendering
    row.pop("caption_embedding", None)

    models = await conn.fetch(
        """
        SELECT id, role, name, version, weights_sha256, runtime
          FROM model_versions
         WHERE id = ANY($1::smallint[])
        """,
        [
            i for i in (
                sighting["detect_model_id"], sighting["describe_model_id"],
                sighting["embed_model_id"],
            ) if i is not None
        ],
    )

    return {
        "sighting": row,
        "riders": [
            dict(r) for r in await conn.fetch(
                "SELECT slot, bbox, helmet, helmet_conf FROM sighting_riders "
                "WHERE read_id = $1 ORDER BY slot", read_id
            )
        ],
        "plate_hypotheses": [
            dict(r) for r in await conn.fetch(
                "SELECT rank, plate, confidence FROM plate_hypotheses "
                "WHERE read_id = $1 ORDER BY rank", read_id
            )
        ],
        "violations": [
            dict(r) for r in await conn.fetch(
                "SELECT id, violation_type, rider_slot, confidence, review_status, "
                "evidence_ref FROM violations WHERE read_id = $1", read_id
            )
        ],
        "models": [dict(r) for r in models],
    }


@router.get("/frames")
async def nearby_frames(
    conn: DbConn,
    user: User,
    camera_id: str,
    around: datetime,
    before_s: int = Query(30, le=600),
    after_s: int = Query(30, le=600),
):
    """The archived frames either side of a moment.

    We hold one frame per second for every camera, so an operator steps
    through what happened before and after a detection without going back to
    the department, and without depending on whether that vendor's adapter
    supports seeking.
    """
    rows = await conn.fetch(
        """
        SELECT id, captured_at, path FROM frames
         WHERE camera_id = $1 AND captured_at BETWEEN $2 AND $3
         ORDER BY captured_at
        """,
        camera_id,
        around - timedelta(seconds=before_s),
        around + timedelta(seconds=after_s),
    )
    return [dict(r) for r in rows]


@router.get("/media/{kind}/{ref:path}")
async def media_file(kind: str, ref: str, user: User):
    """Serve a crop, archived frame or evidence image.

    Path-traversal guarded by resolving against media_root and refusing
    anything that escapes it.
    """
    if kind not in {"crops", "frames", "evidence"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown media kind")

    path = media.absolute(f"{kind}/{ref}").resolve()
    from sentinel.core.config import settings

    try:
        path.relative_to(settings.media_root.resolve())
    except ValueError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "outside the media root") from None
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/audit")
async def audit_trail(
    conn: DbConn,
    user: User,
    object_type: str | None = None,
    object_id: str | None = None,
    since: datetime | None = None,
    limit: int = Query(200, le=2000),
):
    """The append-only log.

    A trigger raises on any update or delete, so no application bug can
    silently rewrite history. Scoring is deterministic, so any alert in here
    can be rebuilt later exactly as it was.
    """
    rows = await conn.fetch(
        """
        SELECT * FROM audit_log
         WHERE ($1::text IS NULL OR object_type = $1)
           AND ($2::text IS NULL OR object_id = $2)
           AND ($3::timestamptz IS NULL OR occurred_at >= $3)
         ORDER BY occurred_at DESC LIMIT $4
        """,
        object_type, object_id, since, limit,
    )
    return [dict(r) for r in rows]
