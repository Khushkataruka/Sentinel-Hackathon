"""Pipeline CLI.

    sentinel-pipeline run --pipeline describe
    sentinel-pipeline run --pipeline embed --concurrency 4
    sentinel-pipeline run --all              one task per pipeline, one process
    sentinel-pipeline run --all --drain      empty the queues, then exit

--all is for the evaluation, where three process groups is the target
topology. In production each pipeline scales on its own, which is the reason
they are separate queues in the first place.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sentinel.core.db import close_pool
from sentinel.core.logging import configure_logging
from sentinel.core.types import Pipeline
from sentinel.pipelines.describe import DescribeWorker
from sentinel.pipelines.embed import EmbedWorker
from sentinel.pipelines.plate import PlateWorker
from sentinel.pipelines.violate import ViolateWorker

WORKERS = {
    Pipeline.DESCRIBE: DescribeWorker,
    Pipeline.EMBED: EmbedWorker,
    Pipeline.PLATE: PlateWorker,
    Pipeline.VIOLATE: ViolateWorker,
}


async def _run(pipelines: list[Pipeline], concurrency: int, drain: bool) -> None:
    workers = [WORKERS[p](concurrency=concurrency, drain=drain) for p in pipelines]
    for worker in workers:
        worker.install_signal_handlers()
    try:
        await asyncio.gather(*(w.run() for w in workers))
    finally:
        await close_pool()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel-pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="consume a crop queue")
    group = run.add_mutually_exclusive_group(required=True)
    group.add_argument("--pipeline", choices=[p.value for p in Pipeline])
    group.add_argument("--all", action="store_true", help="run all four in one process")
    run.add_argument("--concurrency", type=int, default=2)
    run.add_argument(
        "--drain", action="store_true",
        help="process what is queued and exit, rather than waiting for more",
    )

    args = parser.parse_args(argv)
    configure_logging("pipelines")

    pipelines = list(Pipeline) if args.all else [Pipeline(args.pipeline)]
    asyncio.run(_run(pipelines, args.concurrency, args.drain))
    return 0


if __name__ == "__main__":
    sys.exit(main())
