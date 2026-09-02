from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sentinel.api.deps import DbConn, DbTxn, User
from sentinel.core import audit
from sentinel.core.types import ActorKind, AlertStatus, AlertTier

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
async def inbox(
    conn: DbConn,
    user: User,
    tier: AlertTier | None = None,
    alert_status: AlertStatus = AlertStatus.NEW,
    limit: int = Query(100, le=500),
):
    """The tiered alert queue.

    competing_count comes back with every route-backed alert, because a good
    score that is one of forty equally good scores is a shortlist, not a
    match, and the operator has to be able to see that at a glance.
    """
    rows = await conn.fetch(
        """
        SELECT a.id, a.tier, a.score, a.status, a.created_at,
               a.read_id, a.route_id, a.violation_id,
               a.congestion_camera, a.congestion_bucket,
               w.label AS watchlist_label, w.reason AS watchlist_reason,
               r.competing_count, r.plate_anchored, r.min_trust, r.rarity_count,
               s.camera_id, s.seen_at, s.colour, s.vtype, s.make, s.model,
               s.plate_text, s.crop_ref,
               v.violation_type, v.review_status
          FROM alerts a
          LEFT JOIN watchlist_entries w ON w.id = a.watchlist_entry_id
          LEFT JOIN routes r            ON r.id = a.route_id
          LEFT JOIN sightings s         ON s.read_id = a.read_id
          LEFT JOIN violations v        ON v.id = a.violation_id
         WHERE a.status = $1 AND ($2::alert_tier IS NULL OR a.tier = $2)
         ORDER BY
           CASE a.tier WHEN 'priority' THEN 0 WHEN 'review' THEN 1 ELSE 2 END,
           a.created_at DESC
         LIMIT $3
        """,
        alert_status.value, tier.value if tier else None, limit,
    )
    return [dict(r) for r in rows]


class Decision(BaseModel):
    status: AlertStatus
    note: str | None = None


@router.post("/{alert_id}/decide")
async def decide(alert_id: uuid.UUID, body: Decision, conn: DbTxn, user: User):
    """Approve, reject or escalate.

    The decision goes to the audit log and is kept as labelled data for
    tuning the scoring, which is the only training signal this platform gets
    from its own operation.
    """
    if body.status is AlertStatus.NEW:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot set an alert back to new")

    updated = await conn.fetchval(
        """
        UPDATE alerts SET status = $2, decided_by = $3, decided_at = now()
         WHERE id = $1 AND status = 'new' RETURNING id
        """,
        alert_id, body.status.value, uuid.UUID(user.id),
    )
    if updated is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "already decided, or no such alert")

    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id,
        action=f"alert.{body.status.value}", object_type="alert",
        object_id=str(alert_id), details={"note": body.note} if body.note else {},
    )
    return {"id": str(alert_id), "status": body.status.value}


@router.get("/counts")
async def counts(conn: DbConn, user: User):
    rows = await conn.fetch(
        "SELECT tier, status, count(*) AS n FROM alerts GROUP BY tier, status"
    )
    return [dict(r) for r in rows]
