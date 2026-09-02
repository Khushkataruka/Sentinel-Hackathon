"""The append-only audit log.

A database trigger raises on any UPDATE or DELETE, so this module only ever
inserts. Call it inside the transaction that made the change: an audit entry
that commits when the change rolled back is worse than no entry at all.
"""

from __future__ import annotations

from typing import Any

import asyncpg
from sentinel.core.types import ActorKind


async def write(
    conn: asyncpg.Connection,
    *,
    actor_kind: ActorKind,
    actor_id: str,
    action: str,
    object_type: str,
    object_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record one action.

    action is dotted and past-tense-neutral: 'camera.create',
    'alert.approve', 'access_grant.revoke', 'search.run'.
    """
    await conn.execute(
        """
        INSERT INTO audit_log (actor_kind, actor_id, action, object_type, object_id, details)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        actor_kind.value,
        actor_id,
        action,
        object_type,
        str(object_id),
        details or {},
    )


async def service(
    conn: asyncpg.Connection,
    name: str,
    action: str,
    object_type: str,
    object_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Shorthand for a service acting on its own, with no user behind it."""
    await write(
        conn,
        actor_kind=ActorKind.SERVICE,
        actor_id=name,
        action=action,
        object_type=object_type,
        object_id=object_id,
        details=details,
    )
