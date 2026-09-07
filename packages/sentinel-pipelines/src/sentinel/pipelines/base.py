"""The shared pipeline worker loop.

Every pipeline is the same shape:

    claim jobs -> load the crop -> run the model -> write columns
      -> set the status -> post the completion event -> ack

The write, the status, the event and the ack all happen in ONE transaction.
If the process dies mid-job, the whole thing rolls back and the job's lock
ages out and is reclaimed. There is no state in which a sighting says 'done'
but the outbox has no event, or vice versa.

Subclasses implement process(); nothing else.
"""

from __future__ import annotations

import abc
import asyncio
import signal
from typing import Any

import numpy as np
from sentinel.core import media, outbox, queue
from sentinel.core.config import settings
from sentinel.core.db import acquire, transaction
from sentinel.core.logging import get_logger
from sentinel.core.types import DONE_EVENT, STATUS_COLUMN, Pipeline, PipelineStatus

log = get_logger(__name__)


class PipelineWorker(abc.ABC):
    """One pipeline, one process. Run several for more throughput -- the
    queue's SKIP LOCKED claim means they never collide."""

    pipeline: Pipeline
    model_role: str | None = None

    def __init__(self, concurrency: int = 1, *, drain: bool = False) -> None:
        self.concurrency = concurrency
        #: Exit when the queue comes back empty instead of waiting for more.
        #: A batch run over a fixed set of videos has a last job; a live
        #: estate does not.
        self.drain = drain
        self.model_id: int | None = None
        self._stopping = asyncio.Event()
        self.processed = 0
        self.failed = 0

    # -- to implement ------------------------------------------------------

    @abc.abstractmethod
    async def process(
        self, conn, job: queue.Job, crop: np.ndarray
    ) -> dict[str, Any] | None:
        """Do the work and write this pipeline's columns.

        Called inside the job's transaction. Return a dict for the log, or
        None. Raise to fail the job.
        """

    async def setup(self) -> None:
        """Load models. Called once before the loop starts.

        Optional, not abstract: a pipeline with no model to load does not
        need to declare an empty override.
        """
        return None

    # -- machinery ---------------------------------------------------------

    async def _resolve_model_id(self) -> None:
        if not self.model_role:
            return
        async with acquire() as conn:
            self.model_id = await conn.fetchval(
                "SELECT id FROM model_versions WHERE role = $1 "
                "ORDER BY registered_at DESC LIMIT 1",
                self.model_role,
            )
        if self.model_id is None:
            log.warning("no_model_version", role=self.model_role,
                        hint="register one so claims are traceable to weights")

    def _load_crop(self, crop_ref: str) -> np.ndarray | None:
        import cv2

        path = media.absolute(crop_ref)
        if not path.exists():
            return None
        image = cv2.imread(str(path))
        return image if image is not None and image.size else None

    async def _set_status(self, conn, read_id, status: PipelineStatus) -> None:
        column = STATUS_COLUMN[self.pipeline]
        await conn.execute(
            f"UPDATE sightings SET {column} = $2 WHERE read_id = $1",  # noqa: S608
            read_id, status.value,
        )

    async def _handle(self, job: queue.Job) -> None:
        crop = self._load_crop(job.crop_ref)

        if crop is None:
            # The crop is gone -- shed by a retention sweep, or never written.
            # Mark the pipeline skipped rather than failed: there is nothing
            # to retry, and correlation must not keep waiting.
            async with transaction() as conn:
                await self._set_status(conn, job.read_id, PipelineStatus.SKIPPED)
                await outbox.post(conn, job.read_id, DONE_EVENT[self.pipeline])
                await queue.ack(conn, job.id)
            log.warning("crop_missing", pipeline=self.pipeline.value,
                        read_id=str(job.read_id), crop_ref=job.crop_ref)
            return

        try:
            async with transaction() as conn:
                detail = await self.process(conn, job, crop)
                await self._set_status(conn, job.read_id, PipelineStatus.DONE)
                await outbox.post(conn, job.read_id, DONE_EVENT[self.pipeline])
                await queue.ack(conn, job.id)
            self.processed += 1
            if detail:
                log.debug("job_done", pipeline=self.pipeline.value,
                          read_id=str(job.read_id), **detail)

        except Exception as exc:
            self.failed += 1
            message = f"{type(exc).__name__}: {exc}"
            async with transaction() as conn:
                terminal = await queue.fail(conn, job.id, message)
                if terminal:
                    # Out of retries. Set 'failed' and post the event anyway:
                    # correlation is waiting on this pipeline and a permanent
                    # failure has to unblock it, not hang it.
                    await self._set_status(conn, job.read_id, PipelineStatus.FAILED)
                    await outbox.post(conn, job.read_id, DONE_EVENT[self.pipeline])
            log.error("job_failed", pipeline=self.pipeline.value,
                      read_id=str(job.read_id), attempts=job.attempts, error=message)

    async def run(self) -> None:
        await self._resolve_model_id()
        await self.setup()
        log.info("pipeline_started", pipeline=self.pipeline.value,
                 concurrency=self.concurrency, model_id=self.model_id)

        semaphore = asyncio.Semaphore(self.concurrency)

        async def guarded(job: queue.Job) -> None:
            async with semaphore:
                await self._handle(job)

        while not self._stopping.is_set():
            async with acquire() as conn:
                jobs = await queue.claim(conn, self.pipeline)

            if not jobs:
                if self.drain:
                    log.info("pipeline_drained", pipeline=self.pipeline.value,
                             processed=self.processed, failed=self.failed)
                    break
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=settings.queue_poll_interval_s
                    )
                except TimeoutError:
                    pass
                continue

            await asyncio.gather(*(guarded(job) for job in jobs))

        log.info("pipeline_stopped", pipeline=self.pipeline.value,
                 processed=self.processed, failed=self.failed)

    def stop(self) -> None:
        self._stopping.set()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except NotImplementedError:      # pragma: no cover
                pass
