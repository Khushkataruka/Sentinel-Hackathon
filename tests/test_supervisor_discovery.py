"""Ingest waits out a refused catalogue instead of exiting.

The grid refuses its catalogue for minutes at a time. Ingest used to run
discovery once, find no cameras, log no_cameras and exit -- leaving nothing
ingesting until someone restarted it by hand.
"""

from __future__ import annotations

import asyncio

from sentinel.ingest import supervisor as sup


def test_ingest_retries_discovery_until_cameras_appear(monkeypatch):
    attempts: list[int] = []

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(sup, "discover", lambda: attempts.append(1) or [])
    monkeypatch.setattr(sup, "sync_to_registry", nothing)
    monkeypatch.setattr(sup, "_detect_model_id", nothing)
    monkeypatch.setattr(sup, "load_detector", lambda model_id: None)
    monkeypatch.setattr(sup.settings, "reconnect_backoff_initial_s", 0.01)

    supervisor = sup.IngestSupervisor()
    # Refused twice, then the catalogue answers.
    maps = iter([{}, {}, {"cam01": object()}])

    async def camera_map():
        return next(maps)

    started: list[str] = []

    async def supervise(camera_id, adapter):
        started.append(camera_id)
        supervisor.stop()

    monkeypatch.setattr(supervisor, "_camera_map", camera_map)
    monkeypatch.setattr(supervisor, "_supervise", supervise)

    asyncio.run(asyncio.wait_for(supervisor.run(), timeout=5))

    assert len(attempts) == 3
    assert started == ["cam01"]


def test_a_stop_during_the_retry_wait_ends_discovery(monkeypatch):
    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(sup, "discover", lambda: [])
    monkeypatch.setattr(sup, "sync_to_registry", nothing)
    monkeypatch.setattr(sup, "_detect_model_id", nothing)
    monkeypatch.setattr(sup, "load_detector", lambda model_id: None)
    monkeypatch.setattr(sup.settings, "reconnect_backoff_initial_s", 60.0)

    supervisor = sup.IngestSupervisor()

    async def camera_map():
        return {}

    monkeypatch.setattr(supervisor, "_camera_map", camera_map)

    async def scenario():
        running = asyncio.create_task(supervisor.run())
        await asyncio.sleep(0.1)  # now in the 60s backoff
        supervisor.stop()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())
    assert supervisor.tasks == {}
