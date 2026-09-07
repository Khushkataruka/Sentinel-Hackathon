"""Correlation CLI.

    sentinel-correlation run
    sentinel-correlation run --drain
    sentinel-correlation search --registration GJ01AB1234
    sentinel-correlation backfill --entry <uuid>
    sentinel-correlation crosscam --cameras VID-a VID-b --out correlations.json

crosscam is the batch counterpart: no operator, no query, just "did anything
in these cameras appear twice". It seeds the ordinary search from each
sighting rather than inventing a second way to match.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from sentinel.core.db import close_pool, transaction
from sentinel.core.logging import configure_logging


async def _run(drain: bool = False) -> None:
    from sentinel.correlation.worker import CorrelationWorker

    worker = CorrelationWorker(drain=drain)
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

    run_cmd = sub.add_parser("run", help="consume the outbox and raise alerts")
    run_cmd.add_argument(
        "--drain", action="store_true",
        help="correlate what is outstanding and exit, rather than waiting for more",
    )

    search_cmd = sub.add_parser("search", help="run one search from the shell")
    search_cmd.add_argument("--registration", required=True)

    backfill = sub.add_parser("backfill", help="search history for a watchlist entry")
    backfill.add_argument("--entry", required=True)
    backfill.add_argument("--days", type=int, default=15)

    cross = sub.add_parser(
        "crosscam", help="find vehicles seen by more than one of these cameras"
    )
    cross.add_argument("--cameras", nargs="+", required=True)
    cross.add_argument("--out", required=True, help="where to write correlations.json")
    cross.add_argument("--html", default=None, help="also write a standalone report here")
    cross.add_argument(
        "--max-seeds", type=int, default=None,
        help="how many sightings to search from; the cap falls on the weakest first",
    )

    args = parser.parse_args(argv)
    configure_logging("correlation")

    if args.command == "run":
        asyncio.run(_run(args.drain))
    elif args.command == "search":
        asyncio.run(_search(args.registration))
    elif args.command == "crosscam":
        from sentinel.correlation import crosscam

        return crosscam.main(
            args.cameras, args.out, html_out=args.html,
            max_seeds=args.max_seeds or crosscam.DEFAULT_MAX_SEEDS,
        )
    else:
        asyncio.run(_backfill(args.entry, args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
