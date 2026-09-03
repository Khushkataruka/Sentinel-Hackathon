"""Writing what ingest produces.

The single most important function here is write_sighting. The sighting
stub, its outbox event and its pipeline jobs go in ONE transaction, so an
event exists if and only if the change it describes committed. That is what
makes the downstream consumer immune to the commit-order race a
"WHERE updated_at > $last" poller has: a row committed at T1 but made
visible at T2 is invisible forever to a cursor that has already passed T1.

Pipeline gating also happens here, at insert. A pipeline the camera profile
does not permit gets status 'skipped' immediately and no job, so correlation
is never left waiting on work that will not run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg
import cv2
import numpy as np
from sentinel.core import media, outbox, queue
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.core.types import Pipeline, PipelineStatus
from sentinel.ingest.detect import Detection

log = get_logger(__name__)

JPEG_QUALITY = 92


@dataclass
class Gating:
    """What this camera's survey permits. Read once per camera at start-up
    and refreshed when the profile changes."""

    camera_id: str
    permitted_attributes: list[str]
    permitted_violations: list[str]
    plate_viable: bool
    density_viable: bool
    decode_fps: float | None
    deinterlace: bool
    distortion: dict[str, Any] | None
    trust_level: float
    lane_count: int | None
    lane_polygon_wkt: str | None
    #: Seconds of PTS between loop points, or None if never measured.
    loop_period_s: float | None

    @property
    def describes(self) -> bool:
        # Runs when the camera has any permitted attribute at all.
        return bool(self.permitted_attributes)

    @property
    def violates(self) -> bool:
        return bool(self.permitted_violations)

    @property
    def embeds(self) -> bool:
        # The embedding is what cross-camera matching runs on, so it is
        # attempted wherever a crop exists. Crop size is checked in the
        # pipeline against resolution_class, not here.
        return True

    def status_for(self, pipeline: Pipeline) -> PipelineStatus:
        permitted = {
            Pipeline.DESCRIBE: self.describes,
            Pipeline.EMBED: self.embeds,
            Pipeline.PLATE: self.plate_viable,
            Pipeline.VIOLATE: self.violates,
        }[pipeline]
        return PipelineStatus.PENDING if permitted else PipelineStatus.SKIPPED


async def load_gating(conn: asyncpg.Connection, camera_id: str) -> Gating | None:
    """Read the survey result. No profile means the camera claims nothing."""
    row = await conn.fetchrow(
        """
        SELECT camera_id, permitted_attributes, permitted_violations, plate_viable,
               density_viable, decode_fps, deinterlace, distortion, trust_level,
               lane_count, ST_AsText(lane_polygon) AS lane_polygon_wkt,
               loop_period_s
          FROM camera_profiles WHERE camera_id = $1
        """,
        camera_id,
    )
    return Gating(**dict(row)) if row else None


def write_crop(camera_id: str, when: datetime, read_id: uuid.UUID, crop: np.ndarray) -> str:
    """Put the best-frame crop on disk and return its stored reference.

    Named by read_id. Naming it by the tracker id let a looping feed
    overwrite one vehicle's crop with another's in the same hour partition,
    since tracker ids restart at 1 at every scene cut.
    """
    path = media.crop_path(camera_id, when, read_id)
    cv2.imwrite(str(path), crop, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return media.relative(path)


def discard_crop(crop_ref: str) -> None:
    """Drop a crop whose sighting did not commit."""
    try:
        media.absolute(crop_ref).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("orphan_crop_unlink_failed", crop_ref=crop_ref, error=str(exc))


async def write_sighting(
    conn: asyncpg.Connection,
    *,
    read_id: uuid.UUID,
    camera_id: str,
    track_id: str,
    seen_at: datetime,
    track_start_at: datetime | None,
    track_end_at: datetime | None,
    bbox: tuple[int, int, int, int],
    crop_bbox: tuple[int, int, int, int] | None,
    cls: str,
    crop_ref: str,
    detect_model_id: int | None,
    gating: Gating,
) -> uuid.UUID | None:
    """Insert the stub, its outbox event and its pipeline jobs, atomically.

    Returns the read_id, or None if this (camera_id, track_id) was already
    written -- which happens when a worker restarts mid-flush and is not an
    error.

    read_id is supplied by the caller because the crop is written under that
    name before this transaction opens.

    The caller MUST pass a connection already inside a transaction.
    """
    statuses = {p: gating.status_for(p) for p in Pipeline}

    row = await conn.fetchrow(
        """
        INSERT INTO sightings (
            read_id, camera_id, track_id, seen_at, track_start_at, track_end_at,
            bbox, crop_bbox, class, crop_ref, detect_model_id,
            describe_status, embed_status, plate_status, violation_status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        ON CONFLICT (camera_id, track_id) DO NOTHING
        RETURNING read_id
        """,
        read_id, camera_id, track_id, seen_at, track_start_at, track_end_at,
        list(bbox), list(crop_bbox) if crop_bbox else None,
        cls, crop_ref, detect_model_id,
        statuses[Pipeline.DESCRIBE].value, statuses[Pipeline.EMBED].value,
        statuses[Pipeline.PLATE].value, statuses[Pipeline.VIOLATE].value,
    )
    if row is None:
        log.debug("sighting_duplicate", camera=camera_id, track=track_id)
        return None

    # Same transaction. This is the whole point.
    await outbox.post(conn, read_id, outbox.CREATED)

    for pipeline, status in statuses.items():
        if status is not PipelineStatus.PENDING:
            continue
        payload: dict[str, Any] = {"class": cls}
        if pipeline is Pipeline.DESCRIBE:
            payload["permitted_attributes"] = gating.permitted_attributes
        elif pipeline is Pipeline.VIOLATE:
            payload["permitted_violations"] = gating.permitted_violations
        await queue.enqueue(
            conn, pipeline=pipeline, read_id=read_id, camera_id=camera_id,
            crop_ref=crop_ref, payload=payload,
        )

    return read_id


async def archive_frame(
    conn: asyncpg.Connection,
    camera_id: str,
    when: datetime,
    image: np.ndarray,
    detections: list[Detection],
) -> bool:
    """Keep one frame per second, but only where something was happening.

    Empty road produces nothing. The archive is what an operator steps
    through around a sighting, so it needs to cover the moments that have a
    sighting near them and no others.
    """
    if not detections:
        return False

    path = media.frame_path(camera_id, when)
    cv2.imwrite(str(path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    result = await conn.execute(
        """
        INSERT INTO frames (camera_id, captured_at, path) VALUES ($1, $2, $3)
        ON CONFLICT (camera_id, captured_at) DO NOTHING
        """,
        camera_id, when, media.relative(path),
    )
    return result.endswith("1")


async def write_traffic(conn: asyncpg.Connection, summary: dict[str, Any]) -> None:
    """Write one closed bucket: the state row, then a count row per class."""
    await conn.execute(
        """
        INSERT INTO traffic_state (
            camera_id, bucket_start, bucket_seconds, frames_expected, frames_decoded,
            mean_occupancy, peak_occupancy, mean_concurrent, density_vpkm, los)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (camera_id, bucket_start) DO UPDATE SET
            frames_decoded = EXCLUDED.frames_decoded,
            mean_occupancy = EXCLUDED.mean_occupancy,
            peak_occupancy = EXCLUDED.peak_occupancy,
            mean_concurrent = EXCLUDED.mean_concurrent,
            density_vpkm = EXCLUDED.density_vpkm,
            los = EXCLUDED.los,
            computed_at = now()
        """,
        summary["camera_id"], summary["bucket_start"], summary["bucket_seconds"],
        summary["frames_expected"], summary["frames_decoded"],
        summary["mean_occupancy"], summary["peak_occupancy"], summary["mean_concurrent"],
        summary["density_vpkm"],
        summary["los"].value if summary["los"] else None,
    )

    for count in summary["counts"]:
        await conn.execute(
            """
            INSERT INTO traffic_counts (
                camera_id, bucket_start, bucket_seconds, class, vehicle_count, mean_dwell_s)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (camera_id, bucket_start, class) DO UPDATE SET
                vehicle_count = EXCLUDED.vehicle_count,
                mean_dwell_s = EXCLUDED.mean_dwell_s,
                computed_at = now()
            """,
            summary["camera_id"], summary["bucket_start"], summary["bucket_seconds"],
            count["class"], count["vehicle_count"], count["mean_dwell_s"],
        )


async def write_health(
    conn: asyncpg.Connection,
    camera_id: str,
    *,
    reachable: bool,
    last_frame_at: datetime | None,
    measured_fps: float | None,
    detections_1h: int | None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Post a health check from the process that actually holds the stream.

    The verdict distinguishes 'silent' -- reachable, decoding, detecting
    nothing -- from 'down'. A ping will never tell you the difference, and
    'reachable but zero detections in six hours' is the failure that matters.
    """
    if not reachable:
        verdict = "down"
    elif detections_1h == 0:
        verdict = "silent"
    elif measured_fps is not None and measured_fps < settings.target_decode_fps * 0.5:
        verdict = "degraded"
    else:
        verdict = "ok"

    await conn.execute(
        """
        INSERT INTO camera_health
            (camera_id, reachable, last_frame_at, decode_fps, detections_1h, verdict, detail)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        camera_id, reachable, last_frame_at, measured_fps, detections_1h, verdict,
        detail or {},
    )
