#!/usr/bin/env python3
"""Apply SQL migrations in filename order, once each.

Deliberately dumb: no down-migrations, no autogeneration. The schema is a
hand-written artefact that the design document refers to by column name, and
a tool that rewrites it would make the document wrong.

    python scripts/migrate.py           apply pending migrations
    python scripts/migrate.py --seed    apply, then load db/seed/dev_seed.sql
    python scripts/migrate.py --status  list applied and pending
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db" / "migrations"
SEED = ROOT / "db" / "seed" / "dev_seed.sql"

LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename    text PRIMARY KEY,
  sha256      text NOT NULL,
  applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


def _database_url() -> str:
    import os

    load_dotenv(ROOT / ".env")
    url = os.environ.get("SENTINEL_DATABASE_URL")
    if not url:
        sys.exit("SENTINEL_DATABASE_URL is not set (copy .env.example to .env)")
    return url


async def _run(seed: bool, status_only: bool) -> None:
    conn = await asyncpg.connect(_database_url())
    try:
        await conn.execute(LEDGER)
        applied = {
            r["filename"]: r["sha256"]
            for r in await conn.fetch("SELECT filename, sha256 FROM schema_migrations")
        }
        files = sorted(MIGRATIONS.glob("*.sql"))
        if not files:
            sys.exit(f"no migrations found in {MIGRATIONS}")

        for path in files:
            body = path.read_text()
            digest = hashlib.sha256(body.encode()).hexdigest()
            if path.name in applied:
                if applied[path.name] != digest:
                    print(f"  ! {path.name} CHANGED SINCE IT WAS APPLIED", file=sys.stderr)
                else:
                    print(f"  = {path.name}")
                continue
            if status_only:
                print(f"  + {path.name} (pending)")
                continue
            print(f"  + {path.name}")
            # Each file carries its own BEGIN/COMMIT.
            await conn.execute(body)
            await conn.execute(
                "INSERT INTO schema_migrations (filename, sha256) VALUES ($1, $2)",
                path.name,
                digest,
            )

        if seed and not status_only:
            if not SEED.exists():
                sys.exit(f"no seed file at {SEED}")
            print(f"  ~ {SEED.name}")
            await conn.execute(SEED.read_text())
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", action="store_true", help="load the development seed data")
    ap.add_argument("--status", action="store_true", help="show state without applying")
    args = ap.parse_args()
    asyncio.run(_run(args.seed, args.status))


if __name__ == "__main__":
    main()
