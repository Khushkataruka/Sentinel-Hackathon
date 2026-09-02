from __future__ import annotations

from fastapi import APIRouter
from sentinel.core import queue
from sentinel.registry.deps import DbConn, User

router = APIRouter(prefix="/queues", tags=["admin"])


@router.get("/depth")
async def depth(conn: DbConn, user: User):
    """Crop queue depth per pipeline, for the admin screen.

    oldest_waiting_at is the number that matters: a deep queue that is
    draining is fine, a shallow one that has not moved in an hour is not.
    """
    return await queue.depth(conn)
