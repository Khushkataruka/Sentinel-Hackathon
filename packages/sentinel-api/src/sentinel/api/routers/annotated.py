"""Delayed annotated camera frames, joined to the exact recorded track IDs."""

from __future__ import annotations

import asyncio
import time

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse
from sentinel.api.deps import DbConn, User, visible_departments
from sentinel.core.config import settings
from sentinel.ingest.annotate import Label, draw_frame, load_labels, stub_note
from sentinel.ingest.live import FrameBuffer

router = APIRouter(prefix="/annotated", tags=["stream"])
NO_CACHE = {"Cache-Control": "no-store"}


def render_frame(jpeg: bytes, metadata: dict, labels: dict[str, Label]) -> bytes:
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Buffered frame is unreadable")
    draw_frame(image, metadata["boxes"], labels)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("Annotated frame could not be encoded")
    return encoded.tobytes()


@router.get("/{camera_id}/frame")
async def annotated_frame(camera_id: str, conn: DbConn, user: User):
    visible = await visible_departments(conn, user.id)
    camera = await conn.fetchrow(
        "SELECT enabled FROM cameras WHERE camera_id = $1 AND department_id = ANY($2)",
        camera_id,
        visible,
    )
    if camera is None:
        raise HTTPException(404, "Camera not found")
    if not camera["enabled"] or not settings.annotated_feed_enabled:
        raise HTTPException(409, "Annotated playback is disabled for this camera")

    buffer = FrameBuffer(camera_id)
    await asyncio.to_thread(buffer.request_view)
    metadata, jpeg = await asyncio.to_thread(buffer.read)
    if jpeg is None:
        return JSONResponse(metadata, status_code=202, headers=NO_CACHE)

    labels = await load_labels(camera_id, [box["t"] for box in metadata["boxes"]], conn=conn)
    pending = 0
    for box in metadata["boxes"]:
        label = labels.get(box["t"])
        if label is None:
            label = labels[box["t"]] = Label(
                track_id=box["t"], lines=[box["c"]], pending=["track analysis"]
            )
        if label.pending:
            pending += 1
    note = await stub_note(conn=conn)
    try:
        rendered = await asyncio.to_thread(render_frame, jpeg, metadata, labels)
    except ValueError as exc:
        raise HTTPException(503, "Annotated frame is unavailable; reconnecting") from exc
    return Response(
        rendered,
        media_type="image/jpeg",
        headers={
            **NO_CACHE,
            "X-Frame-Seen-At": metadata["seen_at"],
            "X-Frame-Delay": str(round(time.time() - metadata["received_at"], 1)),
            "X-Feed-Fps": str(metadata["fps"]),
            "X-Pending-Tracks": str(pending),
            "X-Model-Note": (
                "DETECTOR UNAVAILABLE" if metadata["detector_unavailable"] else note or ""
            ),
        },
    )
