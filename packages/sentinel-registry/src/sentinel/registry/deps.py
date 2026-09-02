"""Request-scoped dependencies.

Authentication is a stub: it reads a username header and looks the user up.
Real authentication is an HLD concern and slotting a proper provider in here
changes one function. What is NOT a stub is that every write handler takes
the resolved user and passes it to the audit log -- so the audit trail is
correct the moment authentication becomes real.
"""

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


def require_role(*roles: str):
    async def _check(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"role {user.role!r} may not do this"
            )
        return user

    return _check


DbConn = Annotated[asyncpg.Connection, Depends(db_conn)]
DbTxn = Annotated[asyncpg.Connection, Depends(db_txn)]
User = Annotated[CurrentUser, Depends(current_user)]
Admin = Annotated[CurrentUser, Depends(require_role("admin"))]
