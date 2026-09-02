from __future__ import annotations

from fastapi import APIRouter, status
from sentinel.core import audit
from sentinel.core.models import Adapter
from sentinel.core.types import ActorKind
from sentinel.registry import repo
from sentinel.registry.deps import Admin, DbConn, DbTxn

router = APIRouter(prefix="/adapters", tags=["adapters"])


@router.get("", response_model=list[Adapter])
async def list_adapters(conn: DbConn):
    """Every adapter the loader has seen, including the ones that failed.

    A failed adapter is a row with status='failed' and last_error set, not a
    crashed process -- that is the whole promise of the plug-in model.
    """
    return await repo.list_adapters(conn)


@router.put("/{name}", response_model=Adapter)
async def upsert_adapter(name: str, body: Adapter, conn: DbTxn, user: Admin):
    body.name = name
    adapter = await repo.upsert_adapter(conn, body)
    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id, action="adapter.upsert",
        object_type="adapter", object_id=name,
        details={"driver": body.driver, "status": body.status.value},
    )
    return adapter


@router.post("/{name}/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_adapter(name: str, conn: DbTxn, user: Admin):
    await conn.execute("UPDATE adapters SET status = 'disabled' WHERE name = $1", name)
    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id, action="adapter.disable",
        object_type="adapter", object_id=name,
    )
