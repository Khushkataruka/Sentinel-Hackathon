"""The correlation worker.

Consumes the sighting_events outbox. A sighting is picked up once every
pipeline that its camera expects has reported, or a timeout fires.

The timeout matters and is not a nicety: a stuck violation pipeline must not
hold up vehicle matching. A sighting that times out has its outstanding
statuses set to 'timeout' and is correlated on what it has.

Consumption uses FOR UPDATE SKIP LOCKED against the outbox, so several
correlation workers can run without coordinating and without processing the
same event twice.
"""

from __future__ import annotations

import asyncio
import signal
import time
import uuid

from sentinel.core import audit, outbox
from sentinel.core.config import settings
from sentinel.core.db import acquire, transaction
from sentinel.core.logging import get_logger
from sentinel.core.types import PipelineStatus, SightingState
from sentinel.correlation import watchlist

log = get_logger(__name__)

TERMINAL = (
    PipelineStatus.DONE.value,
    PipelineStatus.SKIPPED.value,
    PipelineStatus.FAILED.value,
    PipelineStatus.TIMEOUT.value,
)

SWEEP_INTERVAL_S = 30.0


class CorrelationWorker:
    def __init__(self, *, drain: bool = False) -> None:
        #: Exit once the outbox is empty and nothing is still open. A batch
        #: run has a last sighting; a live estate does not.
        self.drain = drain
        self._stopping = asyncio.Event()
        self.correlated = 0
        self.timed_out = 0

    # -- readiness ---------------------------------------------------------

    async def _is_ready(self, conn, read_id: uuid.UUID) -> bool:
        """Has every pipeline this camera expects reported?"""
        row = await conn.fetchrow(
            """
            SELECT describe_status, embed_status, plate_status, violation_status, state
              FROM sightings WHERE read_id = $1
            """,
            read_id,
        )
        if row is None or row["state"] == SightingState.CORRELATED.value:
            return False
        return all(
            row[column] in TERMINAL
            for column in (
                "describe_status", "embed_status", "plate_status", "violation_status"
            )
        )

    async def _correlate(self, read_id: uuid.UUID) -> None:
        async with transaction() as conn:
            if not await self._is_ready(conn, read_id):
                return

            raised = await watchlist.check_sighting(conn, read_id)
            await conn.execute(
                "UPDATE sightings SET state = $2 WHERE read_id = $1",
                read_id, SightingState.CORRELATED.value,
            )
            await outbox.post(conn, read_id, outbox.COMPLETE)
            await audit.service(
                conn, "correlation", "sighting.correlate", "sighting", str(read_id),
                {"alerts": len(raised)},
            )

        self.correlated += 1
        if raised:
            log.info("watchlist_hit", read_id=str(read_id), alerts=raised)

    # -- timeout sweep -----------------------------------------------------

    async def _sweep_timeouts(self) -> None:
        """Force stuck sightings forward.

        Any sighting still 'pending' on some pipeline past the timeout has
        that pipeline marked 'timeout' and is then correlated on what it
        has. Better a sighting matched on three signals than one that never
        gets matched at all.
        """
        async with transaction() as conn:
            rows = await conn.fetch(
                f"""
                UPDATE sightings SET
                    describe_status  = CASE WHEN describe_status  = 'pending'
                                            THEN 'timeout' ELSE describe_status END,
                    embed_status     = CASE WHEN embed_status     = 'pending'
                                            THEN 'timeout' ELSE embed_status END,
                    plate_status     = CASE WHEN plate_status     = 'pending'
                                            THEN 'timeout' ELSE plate_status END,
                    violation_status = CASE WHEN violation_status = 'pending'
                                            THEN 'timeout' ELSE violation_status END
                 WHERE state = 'open'
                   AND created_at < now() - interval '{int(settings.correlation_timeout_s)} seconds'
                   AND (describe_status = 'pending' OR embed_status = 'pending'
                        OR plate_status = 'pending' OR violation_status = 'pending')
                RETURNING read_id
                """  # noqa: S608 - interval is an int from config, not user input
            )
            for row in rows:
                await outbox.post(conn, row["read_id"], outbox.TIMEOUT)

        if rows:
            self.timed_out += len(rows)
            log.warning("sightings_timed_out", count=len(rows),
                        after_s=settings.correlation_timeout_s)

    # -- loop --------------------------------------------------------------

    async def _quiet(self, waiting_since: float | None) -> bool:
        """Nothing left to consume, and nothing still waiting to become
        consumable.

        An empty outbox alone is not enough: a sighting whose pipelines have
        not all reported has no event yet and would be abandoned half-done.
        Those are what the timeout sweep resolves, so a drain waits for them
        too -- run the crop pipelines to completion first and this returns
        true on the first check.

        Bounded, though. A sighting can be left `open` with every pipeline
        status already terminal and its outbox event consumed; the sweep only
        moves rows that are still `pending`, so nothing would ever make it
        ready and an unbounded wait would hang the batch forever on one bad
        row. After correlation_timeout_s of an empty outbox this gives up and
        says how many it left behind.
        """
        async with acquire() as conn:
            open_rows = await conn.fetchval(
                "SELECT count(*) FROM sightings WHERE state = $1",
                SightingState.OPEN.value,
            )
        if not open_rows:
            return True

        waited = time.monotonic() - (waiting_since or time.monotonic())
        if waited > settings.correlation_timeout_s:
            log.warning(
                "drain_gave_up_on_open_sightings",
                open=open_rows, waited_s=round(waited, 1),
                reason="the outbox is empty but these will never become ready",
            )
            return True
        return False

    async def run(self) -> None:
        log.info("correlation_started", timeout_s=settings.correlation_timeout_s)
        sweep_task = asyncio.create_task(self._sweep_loop())

        idle_since: float | None = None
        try:
            while not self._stopping.is_set():
                async with acquire() as conn:
                    events = await outbox.claim(conn, limit=32)

                if not events:
                    idle_since = idle_since or time.monotonic()
                    if self.drain and await self._quiet(idle_since):
                        log.info("correlation_drained", correlated=self.correlated,
                                 timed_out=self.timed_out)
                        break
                    try:
                        await asyncio.wait_for(
                            self._stopping.wait(), timeout=settings.queue_poll_interval_s
                        )
                    except TimeoutError:
                        pass
                    continue

                idle_since = None
                for event in events:
                    if event["event"] == outbox.COMPLETE:
                        continue    # our own output; do not loop on it
                    try:
                        await self._correlate(event["read_id"])
                    except Exception as exc:
                        log.error("correlate_failed", read_id=str(event["read_id"]),
                                  error=f"{type(exc).__name__}: {exc}")
        finally:
            sweep_task.cancel()
            log.info("correlation_stopped", correlated=self.correlated,
                     timed_out=self.timed_out)

    async def _sweep_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=SWEEP_INTERVAL_S)
                return
            except TimeoutError:
                try:
                    await self._sweep_timeouts()
                except Exception as exc:
                    log.error("timeout_sweep_failed", error=str(exc))

    def stop(self) -> None:
        self._stopping.set()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except NotImplementedError:      # pragma: no cover
                pass
