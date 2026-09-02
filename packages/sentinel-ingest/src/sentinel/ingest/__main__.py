"""Ingest CLI.

    sentinel-ingest run                 every camera every adapter claims
    sentinel-ingest run --only CAM-1    just one, for debugging
    sentinel-ingest adapters            load and test adapters, print results
    sentinel-ingest probe CAM-1         open one stream and report what arrives
    sentinel-ingest preflight CAM-1     measure a feed against the guide's checklist
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sentinel.core.db import close_pool
from sentinel.core.logging import configure_logging, get_logger

log = get_logger(__name__)


async def _run(only: list[str] | None) -> None:
    from sentinel.ingest.supervisor import IngestSupervisor

    supervisor = IngestSupervisor(only=only)
    supervisor.install_signal_handlers()
    try:
        await supervisor.run()
    finally:
        await close_pool()


async def _adapters() -> None:
    from sentinel.ingest.adapters.loader import discover, sync_to_registry

    loaded = discover()
    for item in loaded:
        mark = "ok  " if item.adapter else "FAIL"
        print(f"[{mark}] {item.name:<24} {item.driver}")
        if item.adapter:
            print(f"         {item.camera_count} camera(s)")
        else:
            print(f"         {item.last_error}")
    try:
        await sync_to_registry(loaded)
    finally:
        await close_pool()


async def _probe(camera_id: str, seconds: float) -> None:
    """Open one stream and report what actually arrives.

    Prints the measured frame rate, not the declared one, plus discontinuity
    and reconnect counts. This is the tool for the pre-submission checklist:
    restart a feed while it runs and watch it recover.
    """
    import time

    from sentinel.ingest.adapters.loader import discover
    from sentinel.ingest.decode import CameraStream

    for loaded in discover():
        if loaded.adapter is None:
            continue
        try:
            refs = {r.camera_id for r in loaded.adapter.enumerate()}
        except Exception:
            continue
        if camera_id not in refs:
            continue

        stream = CameraStream(loaded.adapter.open(camera_id))
        started = time.monotonic()
        first_shape = None
        try:
            for frame in stream.frames():
                first_shape = first_shape or frame.shape
                if frame.discontinuity:
                    print(f"  -- scene discontinuity at pts {frame.pts_s:.2f}s")
                if time.monotonic() - started > seconds:
                    break
        finally:
            stream.close()

        stats = stream.stats()
        print(f"camera        {camera_id}")
        print(f"adapter       {loaded.name}")
        print(f"frame size    {first_shape}")
        print(f"decoded       {stats['frames_decoded']}")
        print(f"emitted       {stats['frames_emitted']}")
        fps = stats["measured_fps"]
        print(f"measured fps  {fps:.2f}" if fps else "measured fps  n/a")
        print(f"reconnects    {stats['reconnects']}")
        print(f"decode errors {stats['decode_errors']}")
        print(f"cuts          {stats['discontinuities']}")
        await close_pool()
        return

    print(f"no adapter claims camera {camera_id!r}", file=sys.stderr)
    await close_pool()


def _preflight(camera_id: str, seconds: float, url: str | None, as_json: bool) -> None:
    """Measure one camera against the integration guide's checklist.

    Run this from a machine that can reach the grid. It prints the loop
    period, which is the one number ingest cannot work out for itself.
    """
    import json as _json

    from sentinel.ingest.adapters.base import StreamHandle
    from sentinel.ingest.preflight import render, run

    if url:
        handle = StreamHandle(camera_id, url, "tcp", options={"rtsp_transport": "tcp"})
    else:
        from sentinel.ingest.adapters.loader import discover

        handle = None
        for loaded in discover():
            if loaded.adapter is None:
                continue
            try:
                if camera_id in {r.camera_id for r in loaded.adapter.enumerate()}:
                    handle = loaded.adapter.open(camera_id)
                    break
            except Exception:
                continue
        if handle is None:
            print(f"no adapter claims camera {camera_id!r}; pass --url", file=sys.stderr)
            return

    result = run(handle, seconds=seconds)
    print(_json.dumps(result.as_dict(), indent=2) if as_json else render(result))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel-ingest")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="ingest every claimed camera")
    run.add_argument("--only", nargs="*", default=None, help="limit to these camera ids")

    sub.add_parser("adapters", help="load and test adapters")

    probe = sub.add_parser("probe", help="open one stream and report what arrives")
    probe.add_argument("camera_id")
    probe.add_argument("--seconds", type=float, default=15.0)

    pre = sub.add_parser(
        "preflight", help="measure a feed: real fps, PTS behaviour, loop period"
    )
    pre.add_argument("camera_id")
    pre.add_argument("--seconds", type=float, default=180.0)
    pre.add_argument("--url", default=None, help="bypass adapters, measure this URL")
    pre.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    configure_logging("ingest")

    if args.command == "run":
        asyncio.run(_run(args.only))
    elif args.command == "adapters":
        asyncio.run(_adapters())
    elif args.command == "preflight":
        _preflight(args.camera_id, args.seconds, args.url, args.json)
    else:
        asyncio.run(_probe(args.camera_id, args.seconds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
