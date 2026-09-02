"""CLI for the registry: serve it, or drive the catalogue sync from a shell."""

from __future__ import annotations

import argparse
import asyncio
import sys

from sentinel.core.db import close_pool, transaction
from sentinel.core.logging import configure_logging, get_logger

log = get_logger(__name__)


async def _sync(department_id: int, base_url: str | None, adapter_id: int | None) -> None:
    from sentinel.registry import catalogue

    async with transaction() as conn:
        report = await catalogue.sync(
            conn, department_id=department_id, base_url=base_url, adapter_id=adapter_id
        )
    print(f"synced {report['count']} cameras")
    for skip in report["skipped"]:
        print(f"  skipped {skip.get('camera_id', '?')}: {skip['reason']}")
    await close_pool()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel-registry")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the HTTP service")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    sync = sub.add_parser("sync-sentinel", help="pull the sandbox catalogue")
    sync.add_argument("--department-id", type=int, required=True)
    sync.add_argument("--base-url", default=None)
    sync.add_argument("--adapter-id", type=int, default=None)

    args = parser.parse_args(argv)
    configure_logging("registry")

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "sentinel.registry.app:app", host=args.host, port=args.port, reload=args.reload
        )
        return 0

    asyncio.run(_sync(args.department_id, args.base_url, args.adapter_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
