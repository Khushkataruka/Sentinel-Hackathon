"""Correlation CLI.

    sentinel-correlation run
    sentinel-correlation search --registration GJ01AB1234
    sentinel-correlation backfill --entry <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from sentinel.core.db import close_pool, transaction
from sentinel.core.logging import configure_logging


async def _run() -> None:
    from sentinel.correlation.worker import CorrelationWorker

    worker = CorrelationWorker()
    worker.install_signal_handlers()
    try:
        await worker.run()
    finally:
        await close_pool()


async def _search(registration: str) -> None:
    from sentinel.correlation import search

    async with transaction() as conn:
        result = await search.by_registration(conn, registration)
    print(json.dumps(result.as_dict(), indent=2, default=str))
    await close_pool()


async def _backfill(entry_id: str, days: int) -> None:
    from sentinel.correlation import watchlist

    async with transaction() as conn:
        report = await watchlist.backfill(conn, uuid.UUID(entry_id), lookback_days=days)
    print(json.dumps(report, indent=2, default=str))
    await close_pool()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel-correlation")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="consume the outbox and raise alerts")

    search_cmd = sub.add_parser("search", help="run one search from the shell")
    search_cmd.add_argument("--registration", required=True)

    backfill = sub.add_parser("backfill", help="search history for a watchlist entry")
    backfill.add_argument("--entry", required=True)
    backfill.add_argument("--days", type=int, default=15)

    args = parser.parse_args(argv)
    configure_logging("correlation")

    if args.command == "run":
        asyncio.run(_run())
    elif args.command == "search":
        asyncio.run(_search(args.registration))
    else:
        asyncio.run(_backfill(args.entry, args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
