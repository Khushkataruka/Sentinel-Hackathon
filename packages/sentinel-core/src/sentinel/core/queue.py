"""The four crop queues, held in Postgres.

Section 5.2 of the design gives these queues two properties: best effort,
and drop the oldest when they back up. Both are implemented here.

Claiming uses FOR UPDATE SKIP LOCKED, so N workers on one pipeline never
hand each other the same job and a slow worker never blocks a fast one.
A crashed worker's job is reclaimed once its lock ages past
queue_lock_timeout_s, which is why locked_at is a timestamp and not a flag.

Nothing else in the codebase touches pipeline_jobs. Replacing this module
and 002_pipeline_jobs.sql with a broker client is the whole migration.
"""

from __future__ import annotations

import os
import socket
import uuid
from typing import Any

import asyncpg
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.core.types import Pipeline

log = get_logger(__name__)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class Job:
    """A claimed unit of work. Ack it or fail it; never drop it silently."""

    __slots__ = ("id", "pipeline", "read_id", "camera_id", "crop_ref", "payload", "attempts")

    def __init__(self, record: asyncpg.Record) -> None:
        self.id: int = record["id"]
        self.pipeline = Pipeline(record["pipeline"])
        self.read_id: uuid.UUID = record["read_id"]
        self.camera_id: str = record["camera_id"]
        self.crop_ref: str = record["crop_ref"]
        self.payload: dict[str, Any] = record["payload"] or {}
        self.attempts: int = record["attempts"]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job {self.pipeline} id={self.id} read={self.read_id}>"


async def enqueue(
    conn: asyncpg.Connection,
    *,
    pipeline: Pipeline,
    read_id: uuid.UUID,
    camera_id: str,
    crop_ref: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Add one job. Call inside the same transaction as the sighting insert.

    ON CONFLICT DO NOTHING because (pipeline, read_id) is unique: an ingest
    worker retrying after a partial failure must not double-enqueue.
    """
    await conn.execute(
        """
        INSERT INTO pipeline_jobs (pipeline, read_id, camera_id, crop_ref, payload)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (pipeline, read_id) DO NOTHING
        """,
        pipeline.value,
        read_id,
        camera_id,
        crop_ref,
        payload or {},
    )


async def claim(
    conn: asyncpg.Connection, pipeline: Pipeline, limit: int | None = None
) -> list[Job]:
    """Take up to `limit` jobs for this worker.

    Reclaims jobs whose lock has aged out, so a worker that was SIGKILLed
    mid-job does not strand it.
    """
    limit = limit or settings.queue_claim_batch
    rows = await conn.fetch(
        """
        WITH claimed AS (
          SELECT id
          FROM pipeline_jobs
          WHERE pipeline = $1
            AND done_at IS NULL AND failed_at IS NULL AND dropped_at IS NULL
            AND (locked_at IS NULL OR locked_at < now() - ($2 || ' seconds')::interval)
          ORDER BY id
          FOR UPDATE SKIP LOCKED
          LIMIT $3
        )
        UPDATE pipeline_jobs j
           SET locked_at = now(), locked_by = $4, attempts = j.attempts + 1
          FROM claimed
         WHERE j.id = claimed.id
        RETURNING j.id, j.pipeline, j.read_id, j.camera_id, j.crop_ref,
                  j.payload, j.attempts
        """,
        pipeline.value,
        str(int(settings.queue_lock_timeout_s)),
        limit,
        WORKER_ID,
    )
    return [Job(r) for r in rows]


async def ack(conn: asyncpg.Connection, job_id: int) -> None:
    await conn.execute(
        "UPDATE pipeline_jobs SET done_at = now(), locked_at = NULL WHERE id = $1", job_id
    )


async def fail(conn: asyncpg.Connection, job_id: int, error: str) -> bool:
    """Record a failure. Returns True if the job is now permanently failed.

    Below max_attempts the lock is simply released and the job comes back
    round. At max_attempts it is marked failed and the caller is expected to
    set the sighting's status column to 'failed' so correlation stops waiting.
    """
    row = await conn.fetchrow(
        """
        UPDATE pipeline_jobs
           SET locked_at = NULL,
               last_error = $2,
               failed_at = CASE WHEN attempts >= max_attempts THEN now() END
         WHERE id = $1
        RETURNING failed_at IS NOT NULL AS terminal
        """,
        job_id,
        error[:2000],
    )
    return bool(row and row["terminal"])


async def trim(conn: asyncpg.Connection, pipeline: Pipeline) -> int:
    """Shed the oldest waiting jobs past the depth cap. Section 5.2's
    'drop the oldest'.

    Missing one sighting is acceptable; stalling ingestion is not.
    """
    dropped = await conn.fetchval(
        "SELECT trim_pipeline_queue($1, $2)", pipeline.value, settings.queue_max_depth
    )
    if dropped:
        log.warning("queue_shed", pipeline=pipeline.value, dropped=dropped)
    return int(dropped or 0)


async def depth(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch("SELECT * FROM pipeline_queue_depth ORDER BY pipeline")
    return [dict(r) for r in rows]
