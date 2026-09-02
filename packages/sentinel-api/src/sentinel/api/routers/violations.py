from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sentinel.api.deps import DbConn, DbTxn, User
from sentinel.core import audit
from sentinel.core.types import ActorKind, ReviewStatus

router = APIRouter(prefix="/violations", tags=["violations"])


@router.get("/types")
async def types(conn: DbConn, user: User):
    """The catalogue of what this platform is willing to assert.

    A table, not strings scattered through pipeline code, so an administrator
    can inspect it. needs_glass_penetration marks the two types -- seatbelt
    and phone use -- that require resolving a small object through a
    windscreen at pole distance. Neither has an auto-confirm threshold.
    """
    rows = await conn.fetch("SELECT * FROM violation_types ORDER BY code")
    return [dict(r) for r in rows]


@router.get("")
async def queue(
    conn: DbConn,
    user: User,
    violation_type: str | None = None,
    review_status: ReviewStatus = ReviewStatus.PENDING_REVIEW,
    camera_id: str | None = None,
    limit: int = Query(100, le=500),
):
    rows = await conn.fetch(
        """
        SELECT v.id, v.read_id, v.camera_id, v.violation_type, v.rider_slot,
               v.confidence, v.seen_at, v.window_start, v.window_end,
               v.evidence_ref, v.evidence_bbox, v.review_status, v.details,
               t.label, t.mv_act_section, t.subject, t.needs_glass_penetration,
               s.class, s.colour, s.make, s.model, s.plate_text, s.crop_ref,
               p.trust_level
          FROM violations v
          JOIN violation_types t ON t.code = v.violation_type
          JOIN sightings s ON s.read_id = v.read_id
          LEFT JOIN camera_profiles p ON p.camera_id = v.camera_id
         WHERE v.review_status = $1
           AND ($2::text IS NULL OR v.violation_type = $2)
           AND ($3::text IS NULL OR v.camera_id = $3)
         ORDER BY v.seen_at DESC
         LIMIT $4
        """,
        review_status.value, violation_type, camera_id, limit,
    )
    return [dict(r) for r in rows]


@router.get("/{violation_id}/riders")
async def riders(violation_id: int, conn: DbConn, user: User):
    """The occupant boxes behind a rider-subject violation.

    A rider row is a box on a vehicle and nothing more: no embedding, no
    identity, no watchlist, and it is deleted with the sighting. This
    platform cannot answer 'where has this person been'.
    """
    rows = await conn.fetch(
        """
        SELECT r.slot, r.bbox, r.helmet, r.helmet_conf
          FROM sighting_riders r
          JOIN violations v ON v.read_id = r.read_id
         WHERE v.id = $1 ORDER BY r.slot
        """,
        violation_id,
    )
    return [dict(r) for r in rows]


class ReviewIn(BaseModel):
    review_status: ReviewStatus
    note: str | None = None


@router.post("/{violation_id}/review")
async def review(violation_id: int, body: ReviewIn, conn: DbTxn, user: User):
    """Confirm or reject a violation.

    We report violations; we do not issue challans. Automatic fining needs an
    identified owner, and plates are not readable on most of this estate.
    A confirmed violation is a record, not a penalty.
    """
    if body.review_status not in (ReviewStatus.CONFIRMED, ReviewStatus.REJECTED):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "review sets confirmed or rejected only"
        )

    updated = await conn.fetchval(
        """
        UPDATE violations
           SET review_status = $2, reviewed_by = $3, reviewed_at = now()
         WHERE id = $1 AND review_status = 'pending_review'
        RETURNING id
        """,
        violation_id, body.review_status.value, uuid.UUID(user.id),
    )
    if updated is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "already reviewed, or no such row")

    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id,
        action=f"violation.{body.review_status.value}", object_type="violation",
        object_id=str(violation_id), details={"note": body.note} if body.note else {},
    )
    return {"id": violation_id, "review_status": body.review_status.value}
