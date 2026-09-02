"""q.correlate: the sighting_events outbox.

Durable and replayable, unlike the crop queues. An event exists if and only
if the change it describes committed, because the insert shares a
transaction with the change. That is what makes it immune to the
commit-order race a 'WHERE updated_at > $last' poller has: a row committed
at T1 but visible at T2 is invisible to a cursor that has already passed T1.

Consumers claim with FOR UPDATE SKIP LOCKED and mark consumed.
"""

from __future__ import annotations

import uuid

import asyncpg
from sentinel.core.logging import get_logger

log = get_logger(__name__)

CREATED = "created"
COMPLETE = "complete"
TIMEOUT = "timeout"


async def post(conn: asyncpg.Connection, read_id: uuid.UUID, event: str) -> None:
    """Write an outbox event. Must share a transaction with the change."""
    await conn.execute(
        "INSERT INTO sighting_events (read_id, event) VALUES ($1, $2)", read_id, event
    )


async def claim(conn: asyncpg.Connection, limit: int = 32) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        WITH claimed AS (
          SELECT id FROM sighting_events
           WHERE consumed_at IS NULL
           ORDER BY id
           FOR UPDATE SKIP LOCKED
           LIMIT $1
        )
        UPDATE sighting_events e
           SET consumed_at = now()
          FROM claimed
         WHERE e.id = claimed.id
        RETURNING e.id, e.read_id, e.event, e.created_at
        """,
        limit,
    )


async def replay(conn: asyncpg.Connection, since_id: int, limit: int = 1000):
    """Re-read consumed events. The audit log has to be rebuildable, so the
    consumer must be able to go back over ground it has covered."""
    return await conn.fetch(
        """
        SELECT id, read_id, event, created_at, consumed_at
          FROM sighting_events
         WHERE id > $1
         ORDER BY id
         LIMIT $2
        """,
        since_id,
        limit,
    )
