from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from sentinel.core import audit
from sentinel.core.types import ActorKind
from sentinel.registry import catalogue, importer
from sentinel.registry.deps import Admin, DbTxn

router = APIRouter(tags=["onboarding"])


@router.post("/import/cameras", status_code=status.HTTP_200_OK)
async def import_cameras(
    conn: DbTxn,
    user: Admin,
    file: UploadFile = File(...),
    default_department_id: int | None = Query(
        None, description="used for rows with no department column"
    ),
):
    """Bulk onboard from CSV or XLSX.

    Partial by design: valid rows are written, invalid rows come back with
    the row number and the reason. Nothing is rolled back for a bad row.
    """
    data = await file.read()
    name = (file.filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        rows = importer.parse_xlsx(data)
    elif name.endswith((".csv", ".txt")):
        rows = importer.parse_csv(data)
    else:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "send a .csv or .xlsx")

    result = await importer.import_rows(conn, rows, default_department_id)
    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id, action="camera.bulk_import",
        object_type="import", object_id=file.filename or "upload",
        details={"accepted": result.accepted, "rejected": len(result.rejected)},
    )
    return result.as_dict()


@router.post("/sync/sentinel")
async def sync_sentinel(
    conn: DbTxn,
    user: Admin,
    department_id: int = Query(..., description="department the grid cameras belong to"),
    base_url: str | None = Query(None, description="overrides SENTINEL_SENTINEL_BASE_URL"),
    adapter_id: int | None = Query(None, description="adapter row to record stream URLs on"),
    coordinate_file: str | None = Query(
        None, description="overrides SENTINEL_COORDINATE_FILE"
    ),
):
    """Pull the sandbox catalogue and upsert its cameras.

    Two things to expect in the response.

    `skipped` lists cameras the catalogue offers that we have no position
    for. The catalogue carries no coordinates at all -- `location` is a place
    name -- so positions come from a separate file, and a camera without one
    cannot be mapped, covered or route-validated.

    `position_quality` counts how many of the positions we do have were
    actually surveyed rather than eyeballed. Guessed positions reduce the
    camera's provisional trust_level, because a route leg is only as good as
    the two positions it runs between.

    Cameras arrive with a provisional profile that permits nothing, and will
    produce sightings with every pipeline 'skipped' until a survey is
    recorded. That is the intended order.
    """
    try:
        report = await catalogue.sync(
            conn, department_id=department_id, base_url=base_url,
            adapter_id=adapter_id, coordinate_file=coordinate_file,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"catalogue unreachable: {exc}") from exc

    await audit.write(
        conn, actor_kind=ActorKind.USER, actor_id=user.id, action="catalogue.sync",
        object_type="department", object_id=str(department_id),
        details={"synced": report["count"], "skipped": len(report["skipped"]),
                 "position_quality": report["position_quality"]},
    )
    return report
