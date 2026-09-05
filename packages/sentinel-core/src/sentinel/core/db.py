"""Database access: one asyncpg pool per process, with the codecs the
schema needs.

Two types need help.

``vector`` (pgvector) has no binary protocol asyncpg knows about, so it is
registered as a text codec: a Python list of floats goes out as '[1,2,3]'
and comes back the same way. This is the documented approach and costs a
parse per row, which is why sightings_poll exists and why nothing SELECTs
embedding columns unless it is about to score them.

``geography(Point, 4326)`` is not decoded at all. Queries cast it with
ST_X/ST_Y or ST_AsText on the way out and ST_MakePoint/ST_GeogFromText on
the way in. Decoding EWKB in the client to hand it straight back to
PostGIS would buy nothing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import asyncpg
from sentinel.core.config import settings
from sentinel.core.logging import get_logger

log = get_logger(__name__)

_pool: asyncpg.Pool | None = None


def encode_vector(values: Sequence[float] | None) -> str | None:
    """Python floats -> the pgvector text form."""
    if values is None:
        return None
    return "[" + ",".join(f"{float(v):.7g}" for v in values) + "]"


def decode_vector(text: str | None) -> list[float] | None:
    if text is None:
        return None
    return [float(x) for x in text.strip()[1:-1].split(",")] if text.strip() != "[]" else []


async def _init_connection(conn: asyncpg.Connection) -> None:
    # jsonb as dicts, not strings.
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(
            typename, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
    # pgvector, over the text protocol.
    try:
        await conn.set_type_codec(
            "vector",
            encoder=lambda v: v if isinstance(v, str) else encode_vector(v),
            decoder=decode_vector,
            format="text",
        )
    except (asyncpg.exceptions.UndefinedObjectError, ValueError) as exc:
        # Extension not installed yet -- migrations have not run. Let the
        # caller fail on the query rather than here, where the message is worse.
        #
        # Two spellings of the same condition: the server raises
        # UndefinedObject when it can resolve nothing, and asyncpg raises a
        # plain ValueError("unknown type: public.vector") when introspection
        # comes back empty. Catching only the first meant a fresh database
        # failed to open a pool at all, with an error that named neither
        # pgvector nor the migrations.
        if isinstance(exc, ValueError) and "unknown type" not in str(exc):
            raise
        log.warning("pgvector_codec_unavailable", error=str(exc),
                    hint="run migrations first: make migrate")


def _check_dsn(dsn: str) -> None:
    """Fail on an unparseable DSN with a message that names the cause.

    A DSN is a URL, so a password containing any of []@:/?#% has to be
    percent-encoded. Unencoded, a '[' surfaces from urllib as "Invalid IPv6
    URL" -- which names neither the password nor the setting, and sends you
    looking at the network for an hour.

    Checked here rather than around create_pool: that call raises ValueError
    for unrelated reasons too, and a wrapper there blamed the password for a
    missing pgvector extension.
    """
    if not dsn.startswith(("postgres://", "postgresql://")):
        return          # a bare name or a unix socket; asyncpg's problem, not ours
    try:
        urlsplit(dsn)
    except ValueError as exc:
        raise ValueError(
            f"SENTINEL_DATABASE_URL could not be parsed ({exc}). A password "
            "containing any of []@:/?#% must be percent-encoded -- '[' is %5B, "
            "'@' is %40. Nothing else about the URL is wrong."
        ) from exc


async def get_pool() -> asyncpg.Pool:
    """The process-wide pool. Created on first use."""
    global _pool
    if _pool is None:
        _check_dsn(settings.database_url)
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=settings.db_min_pool,
            max_size=settings.db_max_pool,
            command_timeout=settings.db_command_timeout,
            init=_init_connection,
        )
        log.info("db_pool_open", min=settings.db_min_pool, max=settings.db_max_pool)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("db_pool_closed")


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    """A connection inside a transaction.

    Used wherever the design says "in one transaction" -- the sighting stub
    and its outbox event, a violation and its rider rows, any write plus its
    audit_log entry.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        yield conn


async def healthcheck() -> bool:
    try:
        async with acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception as exc:  # pragma: no cover
        log.error("db_healthcheck_failed", error=str(exc))
        return False
