"""Frame reads get a thread per camera.

Detection runs on the shared executor. Reads block -- an RTSP open waits out
its timeout, a dead feed sleeps through reconnect backoff -- and when every
camera read from the shared pool too, cameras stuck in backoff took every
thread and detection had nowhere to run: ingest sat at 0% CPU with a dozen
streams open.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
from sentinel.ingest import worker as worker_mod
from sentinel.ingest.track import ByteTrack
from test_worker_ids import gating


def test_a_stuck_camera_cannot_starve_the_others(monkeypatch):
    release = threading.Event()

    class FakeStream:
        def __init__(self, handle, **_):
            self.handle = handle

        def frames(self):
            if self.handle == "stuck":
                release.wait()  # a camera sleeping through reconnect backoff
                return
            for i in range(3):
                yield SimpleNamespace(
                    image=np.zeros((4, 4, 3), np.uint8),
                    pts_s=i * 0.1,
                    dt_s=0.1,
                    seen_at=datetime.now(UTC),
                    discontinuity=False,
                )

        def close(self):
            pass

    async def ready(self, *args, **kwargs):
        return True

    async def nothing(self, *args, **kwargs):
        return None

    monkeypatch.setattr(worker_mod, "CameraStream", FakeStream)
    monkeypatch.setattr(worker_mod.CameraWorker, "prepare", ready)
    monkeypatch.setattr(worker_mod.CameraWorker, "_post_health", nothing)
    monkeypatch.setattr(worker_mod.CameraWorker, "_flush_tracks", nothing)
    monkeypatch.setattr(worker_mod.CameraWorker, "_should_archive", lambda self, frame: False)

    detected: list[str] = []

    def make(camera_id: str) -> worker_mod.CameraWorker:
        w = worker_mod.CameraWorker(
            camera_id,
            adapter=SimpleNamespace(open=lambda cid: cid),
            detector=lambda image: detected.append(camera_id) or [],
            once=True,
        )
        w.gating = gating()
        w.tracker = ByteTrack()
        return w

    async def scenario():
        # One shared thread: as if every other camera's read already held the rest.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        stuck = asyncio.create_task(make("stuck").run())
        await asyncio.sleep(0.2)  # let the stuck read claim its thread
        try:
            await asyncio.wait_for(make("live").run(), timeout=5)
        finally:
            release.set()  # otherwise a failure hangs in executor shutdown
        await stuck

    asyncio.run(scenario())
    assert detected == ["live"] * 3
