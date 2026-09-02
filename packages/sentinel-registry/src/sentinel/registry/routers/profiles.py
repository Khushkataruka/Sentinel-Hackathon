from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sentinel.core import audit
from sentinel.core.models import CameraProfile, CameraProfileIn
from sentinel.core.types import ActorKind
from sentinel.registry import repo
from sentinel.registry.deps import Admin, DbConn, DbTxn, User

router = APIRouter(prefix="/cameras/{camera_id}/profile", tags=["survey"])


@router.get("", response_model=CameraProfile)
async def get_profile(camera_id: str, conn: DbConn, user: User):
    profile = await repo.get_profile(conn, camera_id)
    if profile is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "camera has not been surveyed; it is permitted to claim nothing",
        )
    return profile


@router.put("", response_model=CameraProfile)
async def put_profile(camera_id: str, body: CameraProfileIn, conn: DbTxn, user: Admin):
    """Record a survey result.

    This is the most consequential write in the registry. permitted_violations
    is enforced by a database trigger on insert into violations, so a camera
    that loses a permission here stops being able to assert it immediately,
    with no pipeline redeploy.

    density_viable without lane geometry is rejected: the density estimate is
    computed from the lane polygon, and claiming density without one would
    produce a number with nothing behind it.
    """
    if await repo.get_camera(conn, camera_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such camera")
    if body.density_viable and not (body.lane_polygon_wkt and body.lane_count):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "density_viable requires lane_polygon_wkt and lane_count",
        )

    before = await repo.get_profile(conn, camera_id)
    profile = await repo.upsert_profile(conn, camera_id, body)
    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id, action="camera_profile.upsert",
        object_type="camera", object_id=camera_id,
        details={
            "permitted_violations_before": before.permitted_violations if before else None,
            "permitted_violations_after": profile.permitted_violations,
            "plate_viable": profile.plate_viable,
            "trust_level": profile.trust_level,
        },
    )
    return profile
