from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, status
from sentinel.core.models import HealthCheck
from sentinel.registry import repo
from sentinel.registry.deps import DbConn, DbTxn, User

router = APIRouter(tags=["health"])


@router.post("/cameras/{camera_id}/health", status_code=status.HTTP_201_CREATED)
async def post_health(camera_id: str, body: HealthCheck, conn: DbTxn):
    """Record one health check.

    Posted by ingest, not polled by the registry: the process holding the
    stream is the only one that knows the real decode rate and detection
    count. 'Reachable' on its own is the check that misses every failure
    worth catching.
    """
    body.camera_id = camera_id
    await repo.record_health(conn, body)
    return {"recorded": True}


@router.get("/cameras/{camera_id}/health")
async def get_history(
    camera_id: str,
    conn: DbConn,
    user: User,
    since: datetime | None = None,
    limit: int = Query(200, le=2000),
):
    return await repo.health_history(conn, camera_id, since, limit)


@router.get("/health/latest")
async def get_latest(conn: DbConn, user: User, camera_id: str | None = None):
    """Latest verdict per camera. This is what the map colours itself with."""
    return await repo.latest_health(conn, camera_id)
