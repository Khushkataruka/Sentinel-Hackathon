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
                    log.warning("camera_not_registered", camera=ref.camera_id,
                                adapter=loaded.name,
                                action="onboard it first, or run the catalogue sync")
                    continue
                mapping[ref.camera_id] = loaded.adapter
        return mapping

    async def _supervise(self, camera_id: str, adapter: Adapter) -> None:
        backoff = settings.reconnect_backoff_initial_s
        while not self._stopping.is_set():
            worker = CameraWorker(
                camera_id, adapter,
                detector=self._detector, detect_model_id=self._detect_model_id,
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

            delay = min(backoff, settings.reconnect_backoff_max_s)
            delay *= 0.75 + random.random() * 0.5
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                return
            except TimeoutError:
                backoff = min(backoff * 2, settings.reconnect_backoff_max_s)

    async def run(self) -> None:
        self.adapters = discover()
        await sync_to_registry(self.adapters)

        self._detect_model_id = await _detect_model_id()
        # One detector shared across cameras: the ONNX session is thread-safe
        # for inference and loading N copies of the weights is the fastest way
        # to run out of memory at eighty thousand cameras.
        self._detector = load_detector(self._detect_model_id)

        cameras = await self._camera_map()
        if not cameras:
            log.error("no_cameras", hint="check adapters, the registry and --only")
            return

        log.info("ingest_starting", cameras=len(cameras),
                 adapters=sum(1 for a in self.adapters if a.adapter))

        for camera_id, adapter in cameras.items():
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
            except NotImplementedError:      # pragma: no cover - Windows
                pass

    def stats(self) -> dict[str, Any]:
        from sentinel.ingest.worker import stats_snapshot

        return stats_snapshot(self.workers)
