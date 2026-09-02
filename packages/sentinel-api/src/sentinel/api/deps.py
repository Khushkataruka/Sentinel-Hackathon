"""Shared dependencies. Same stub authentication as the registry, so the
audit trail is correct the moment real authentication lands."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import Depends, Header, HTTPException, status
from sentinel.core.db import acquire, transaction
from sentinel.core.models import Base


class CurrentUser(Base):
    id: str
    username: str
    display_name: str
    department_id: int
    role: str


async def db_conn() -> AsyncIterator[asyncpg.Connection]:
    async with acquire() as conn:
        yield conn


async def db_txn() -> AsyncIterator[asyncpg.Connection]:
    async with transaction() as conn:
        yield conn


async def current_user(
    conn: Annotated[asyncpg.Connection, Depends(db_conn)],
    x_sentinel_user: Annotated[str, Header()] = "admin",
) -> CurrentUser:
    row = await conn.fetchrow(
        "SELECT id, username, display_name, department_id, role FROM users "
        "WHERE username = $1 AND active",
        x_sentinel_user,
    )
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"unknown user {x_sentinel_user!r}")
    return CurrentUser(id=str(row["id"]), **{k: row[k] for k in
                       ("username", "display_name", "department_id", "role")})


async def visible_departments(conn: asyncpg.Connection, user_id: str) -> list[int]:
    rows = await conn.fetch(
        """
        SELECT department_id FROM users WHERE id = $1
        UNION
        SELECT department_id FROM access_grants
         WHERE user_id = $1 AND revoked_at IS NULL
           AND (expires_at IS NULL OR expires_at > now())
        """,
        user_id,
    )
    return [r["department_id"] for r in rows]


DbConn = Annotated[asyncpg.Connection, Depends(db_conn)]
DbTxn = Annotated[asyncpg.Connection, Depends(db_txn)]
User = Annotated[CurrentUser, Depends(current_user)]
