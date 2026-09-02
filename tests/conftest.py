"""Test fixtures.

The unit tests here need no database. The integration tests do, and they skip
themselves cleanly when SENTINEL_DATABASE_URL points at nothing -- so
`pytest` works on a laptop with no Postgres and covers more when there is one.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Same .env the services read, so a developer who can run the stack can also
# run the integration tests without a second piece of setup.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
os.environ.setdefault("SENTINEL_MEDIA_ROOT", "./var/test-media")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    """A connection to a migrated database, or a skip."""
    import asyncpg

    url = os.environ.get("SENTINEL_DATABASE_URL")
    if not url:
        pytest.skip("SENTINEL_DATABASE_URL not set")
    try:
        conn = await asyncpg.connect(url, timeout=3)
    except Exception as exc:
        pytest.skip(f"database unreachable: {exc}")

    if not await conn.fetchval("SELECT to_regclass('public.sightings') IS NOT NULL"):
        await conn.close()
        pytest.skip("migrations have not been applied")

    try:
        yield conn
    finally:
        await conn.close()
