from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sentinel.core import audit
from sentinel.core.types import ActorKind
from sentinel.registry import repo
from sentinel.registry.deps import Admin, DbConn, DbTxn, User

router = APIRouter(prefix="/access-grants", tags=["access"])


class GrantIn(BaseModel):
    user_id: uuid.UUID
    department_id: int
    reason: str
    expires_at: datetime | None = None


@router.get("")
async def my_grants(conn: DbConn, user: User):
    return await repo.list_grants(conn, uuid.UUID(user.id))


@router.post("", status_code=status.HTTP_201_CREATED)
async def grant(body: GrantIn, conn: DbTxn, user: Admin):
    """Cross-department viewing rights.

    Both the grant and its revocation go to the audit log. The reason is
    mandatory at the schema level, so 'why does this operator see RTO
    cameras' always has a recorded answer.
    """
    grant_id = await repo.create_grant(
        conn, user_id=body.user_id, department_id=body.department_id,
        granted_by=uuid.UUID(user.id), reason=body.reason, expires_at=body.expires_at,
    )
    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id, action="access_grant.create",
        object_type="access_grant", object_id=str(grant_id),
        details={"user_id": str(body.user_id), "department_id": body.department_id,
                 "reason": body.reason},
    )
    return {"id": grant_id}


@router.delete("/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(grant_id: int, conn: DbTxn, user: Admin):
    if not await repo.revoke_grant(conn, grant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no live grant with that id")
    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id, action="access_grant.revoke",
        object_type="access_grant", object_id=str(grant_id),
    )
