from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sentinel.core import audit
from sentinel.core.models import Camera, CameraIn
from sentinel.core.types import ActorKind
from sentinel.registry import repo
from sentinel.registry.deps import Admin, DbConn, DbTxn, User

router = APIRouter(prefix="/cameras", tags=["registry"])


@router.get("", response_model=list[Camera])
async def list_cameras(
    conn: DbConn,
    user: User,
    department_id: int | None = None,
    enabled: bool | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_m: float = Query(5000, gt=0),
    limit: int = Query(500, le=2000),
    offset: int = 0,
):
    """Cameras the caller may see.

    Departments see their own. Cross-department viewing needs a live grant,
    which is resolved here rather than trusted to the frontend.
    """
    visible = await repo.visible_departments(conn, user.id)
    if department_id is not None and department_id not in visible:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no grant for that department")

    near = (lat, lon, radius_m) if lat is not None and lon is not None else None
    cameras = await repo.list_cameras(
        conn, department_id=department_id, enabled=enabled, near=near,
        limit=limit, offset=offset,
    )
    return [c for c in cameras if c.department_id in visible]


@router.get("/needing-survey", response_model=list[str])
async def needing_survey(conn: DbConn, user: Admin):
    """Cameras with no capability profile. They produce nothing until
    surveyed, so this list is the onboarding backlog."""
    return await repo.cameras_needing_survey(conn)


@router.get("/{camera_id}", response_model=Camera)
async def get_camera(camera_id: str, conn: DbConn, user: User):
    cam = await repo.get_camera(conn, camera_id)
    if cam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such camera")
    if cam.department_id not in await repo.visible_departments(conn, user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no grant for that department")
    return cam


@router.put("/{camera_id}", response_model=Camera)
async def upsert_camera(camera_id: str, body: CameraIn, conn: DbTxn, user: Admin):
    if body.camera_id != camera_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "camera_id mismatch")
    cam = await repo.upsert_camera(conn, body)
    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id, action="camera.upsert",
        object_type="camera", object_id=camera_id,
        details={"department_id": body.department_id, "kind": body.kind.value},
    )
    return cam


@router.post("/{camera_id}/enable", status_code=status.HTTP_204_NO_CONTENT)
async def enable_camera(camera_id: str, conn: DbTxn, user: Admin, enabled: bool = True):
    if not await repo.set_camera_enabled(conn, camera_id, enabled):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such camera")
    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id,
        action="camera.enable" if enabled else "camera.disable",
        object_type="camera", object_id=camera_id,
    )
