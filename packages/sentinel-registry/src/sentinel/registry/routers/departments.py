from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sentinel.core import audit
from sentinel.core.models import Department
from sentinel.core.types import ActorKind
from sentinel.registry import repo
from sentinel.registry.deps import Admin, DbConn, DbTxn

router = APIRouter(prefix="/departments", tags=["registry"])


@router.get("", response_model=list[Department])
async def list_departments(conn: DbConn):
    return await repo.list_departments(conn)


@router.post("", response_model=Department, status_code=status.HTTP_201_CREATED)
async def create_department(dept: Department, conn: DbTxn, user: Admin):
    if await repo.get_department_by_code(conn, dept.code):
        raise HTTPException(status.HTTP_409_CONFLICT, f"department {dept.code} exists")
    created = await repo.create_department(conn, dept)
    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id, action="department.create",
        object_type="department", object_id=str(created.id), details={"code": created.code},
    )
    return created
