"""Supervising one worker per camera.

A camera that crashes is restarted with backoff; the others are untouched.
That isolation is the reason ingest is described as running in its own
process group -- a broken adapter cannot take down anything else.
"""

from __future__ import annotations

import asyncio
import random
import signal
from typing import Any

from sentinel.core.config import settings
from sentinel.core.db import acquire
from sentinel.core.logging import get_logger
from sentinel.ingest.adapters.base import Adapter
from sentinel.ingest.adapters.loader import LoadedAdapter, discover, sync_to_registry
from sentinel.ingest.detect import load_detector
from sentinel.ingest.worker import CameraWorker

log = get_logger(__name__)

#: Seconds between starting one camera's worker and the next. Started all at
#: once, 29 cameras opening RTSP together drew dozens of 401s and refused
#: connections from the grid's gateway while the opens that did succeed
#: trickled in one at a time -- a rate limit, as far as the logs show.
STARTUP_STAGGER_S = 2.0


def limit_cameras(cameras: dict[str, Adapter], limit: int | None) -> dict[str, Adapter]:
    """The first `limit` cameras by id, or all of them when no limit is set.

    The grid meters watch time per account, and all 29 cameras at once spent
    it and locked the account out. Sorted, so the same cameras come back on
    every restart rather than whichever the catalogue listed first.
    """
    if not limit or len(cameras) <= limit:
        return cameras
    kept = sorted(cameras)[:limit]
    log.info("cameras_limited", limit=limit, kept=kept, skipped=len(cameras) - limit)
    return {camera_id: cameras[camera_id] for camera_id in kept}


async def _detect_model_id() -> int | None:
    """The model_versions row for the running detector.

    Recorded on every sighting, so a claim can be traced back to the weights
    that produced it. Null when nothing is registered, which is honest --
    the column records what ran, not what we meant to run.
    """
    async with acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM model_versions WHERE role = 'detect' "
            "ORDER BY registered_at DESC LIMIT 1"
        )


class IngestSupervisor:
    def __init__(self, only: list[str] | None = None) -> None:
        self.only = set(only) if only else None
        self.adapters: list[LoadedAdapter] = []
        self.workers: dict[str, CameraWorker] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self._stopping = asyncio.Event()
        self._detector = None
        self._detect_model_id: int | None = None

    async def _stopped_within(self, seconds: float) -> bool:
        """Wait up to `seconds`; True if a stop arrived in that time."""
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False

    async def _backoff(self, backoff: float) -> bool:
        """A jittered backoff wait. True if a stop arrived meanwhile."""
        delay = min(backoff, settings.reconnect_backoff_max_s)
        return await self._stopped_within(delay * (0.75 + random.random() * 0.5))

    async def _camera_map(self) -> dict[str, Adapter]:
        """Every camera an adapter claims, mapped to the adapter that claims it.

        Cross-checked against the registry: a camera an adapter offers but
        that has never been onboarded is not ingested, because there is
        nowhere to put its sightings and no profile to gate them.
        """
        async with acquire() as conn:
            known = {
                r["camera_id"]
                for r in await conn.fetch("SELECT camera_id FROM cameras WHERE enabled")
            }

        mapping: dict[str, Adapter] = {}
        for loaded in self.adapters:
            if loaded.adapter is None:
                continue
            try:
                refs = loaded.adapter.enumerate()
            except Exception as exc:
                log.error("enumerate_failed", adapter=loaded.name, error=str(exc))
                continue
            for ref in refs:
                if self.only and ref.camera_id not in self.only:
                    continue
                if ref.camera_id not in known:
                    log.warning(
                        "camera_not_registered",
                        camera=ref.camera_id,
                        adapter=loaded.name,
                        action="onboard it first, or run the catalogue sync",
                    )
                    continue
                mapping[ref.camera_id] = loaded.adapter
        return mapping

    async def _discover_cameras(self) -> dict[str, Adapter]:
        """Load the adapters and map their cameras, retrying until some appear.

        The grid refuses its catalogue for minutes at a time -- a session
        refused straight after login, a throttle after a burst of stream
        retries. Exiting on the first refusal left ingest dead until someone
        restarted it by hand. Each attempt still lands in the adapters table.
        Returns empty only when a stop arrives first.
        """
        backoff = settings.reconnect_backoff_initial_s
        while not self._stopping.is_set():
            self.adapters = discover()
            await sync_to_registry(self.adapters)
            cameras = await self._camera_map()
            if cameras:
                return cameras
            log.error(
                "no_cameras",
                hint="check adapters, the registry and --only",
                retry_backoff_s=round(min(backoff, settings.reconnect_backoff_max_s)),
            )
            if await self._backoff(backoff):
                break
            backoff = min(backoff * 2, settings.reconnect_backoff_max_s)
        return {}

    async def _supervise(self, camera_id: str, adapter: Adapter) -> None:
        backoff = settings.reconnect_backoff_initial_s
        while not self._stopping.is_set():
            worker = CameraWorker(
                camera_id,
                adapter,
                detector=self._detector,
                detect_model_id=self._detect_model_id,
            )
            self.workers[camera_id] = worker
            try:
                await worker.run()
                if self._stopping.is_set():
                    return
                # A clean return means the camera was unsurveyed or the
                # adapter refused it. Retrying immediately would spin.
                log.info("worker_returned", camera=camera_id)
            except asyncio.CancelledError:
                worker.stop()
                await worker.shutdown()
                raise
            except Exception as exc:
                log.error("worker_failed", camera=camera_id, error=str(exc))

            if await self._backoff(backoff):
                return
            backoff = min(backoff * 2, settings.reconnect_backoff_max_s)

    async def run(self) -> None:
        self._detect_model_id = await _detect_model_id()
        # One detector shared across cameras: the ONNX session is thread-safe
        # for inference and loading N copies of the weights is the fastest way
        # to run out of memory at eighty thousand cameras.
        self._detector = load_detector(self._detect_model_id)

        cameras = limit_cameras(await self._discover_cameras(), settings.ingest_max_cameras)
        if not cameras:
            return

        log.info(
            "ingest_starting",
            cameras=len(cameras),
            adapters=sum(1 for a in self.adapters if a.adapter),
        )

        for index, (camera_id, adapter) in enumerate(cameras.items()):
            if index and await self._stopped_within(STARTUP_STAGGER_S):
                break
            self.tasks[camera_id] = asyncio.create_task(
                self._supervise(camera_id, adapter), name=f"camera:{camera_id}"
            )

        await self._stopping.wait()

        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        log.info("ingest_stopped")

    def stop(self) -> None:
        self._stopping.set()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except NotImplementedError:  # pragma: no cover - Windows
                pass

    def stats(self) -> dict[str, Any]:
        from sentinel.ingest.worker import stats_snapshot

        return stats_snapshot(self.workers)
