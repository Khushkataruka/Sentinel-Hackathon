"""One pass over video files on disk, into the same tables a live camera writes.

The platform is built for cameras: `sentinel-ingest run` supervises a feed that
never ends, and every timestamp comes from the moment the decoder happened to
connect. Neither holds for a file. This module supplies the three things a
finite source needs and nothing else:

  * a stop condition          -- CameraStream(once=True)
  * a place and a time        -- ensure_camera + PtsClock's fixed epoch
  * the per-frame geometry    -- an observer, written to a sidecar

Everything in between -- decode, the PTS clock, ByteTrack, best-frame
selection, the crop write, `write_sighting` and its outbox event and its
pipeline jobs -- is the live path, unchanged. If this file starts to look
like a second ingest implementation, it has gone wrong.

A file has no location, so route building has nothing to measure. Rather than
refuse, a video with no manifest entry is given a synthetic position: cameras
strung out along a line, LEG_KM apart, with start times LEG_SECONDS apart.
That is roughly 40 km/h, which sits inside the plausibility window in
`correlation.routes`, so the same vehicle appearing in two files can actually
form a route. Synthetic coordinates are recorded as such in the manifest the
run writes, and they are the first thing to replace with real ones.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from sentinel.core import audit
from sentinel.core.config import settings
from sentinel.core.db import acquire, transaction
from sentinel.core.logging import get_logger
from sentinel.core.types import ALL_ATTRIBUTES
from sentinel.ingest.adapters.builtin.filesrc import FileAdapter
from sentinel.ingest.decode import Frame
from sentinel.ingest.detect import load_detector
from sentinel.ingest.track import Track, TrackState
from sentinel.ingest.worker import CameraWorker

log = get_logger(__name__)

#: Default spacing between consecutive videos when no manifest gives real
#: coordinates. 2 km in 180 s is ~40 km/h.
LEG_KM = 2.0
LEG_SECONDS = 180.0

#: Degrees of latitude per kilometre. Good to a fraction of a percent, and the
#: positions are synthetic anyway -- a proper projection here would be false
#: precision on a made-up number.
DEG_PER_KM = 1.0 / 111.32

#: Where the synthetic line starts. Ahmedabad, so a synthetic run lands on the
#: same map as the real estate rather than in the Gulf of Guinea.
ORIGIN_LAT = 23.0225
ORIGIN_LON = 72.5714

#: What an offline camera's profile permits. Deliberately not the section 6
#: survey -- see `distortion` below, which says so in the row itself.
#:
#: The full grant, same as every other camera: see ALL_ATTRIBUTES in
#: sentinel.core.types. `plate_viable` is true because the point of an offline
#: run is to exercise ANPR on footage chosen for it.
PERMITTED_ATTRIBUTES = list(ALL_ATTRIBUTES)


@dataclass
class VideoSpec:
    """One input video, resolved to a camera."""

    path: Path
    camera_id: str
    name: str
    lat: float
    lon: float
    start_at: datetime
    synthetic_position: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "camera_id": self.camera_id,
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "start_at": self.start_at.isoformat(),
            "synthetic_position": self.synthetic_position,
        }


@dataclass
class VideoResult:
    spec: VideoSpec
    sightings: int = 0
    frames: int = 0
    tracks_seen: int = 0
    sidecar: Path | None = None
    stats: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Resolving videos to cameras
# ---------------------------------------------------------------------------


def _slug(stem: str) -> str:
    """A camera id from a filename. Kept short and printable -- it appears in
    log lines, on the annotated video and in the report."""
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in stem).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return f"VID-{cleaned[:40] or 'unnamed'}"


def resolve(
    paths: list[Path],
    manifest: dict[str, Any] | None = None,
    *,
    epoch: datetime | None = None,
    leg_km: float = LEG_KM,
    leg_seconds: float = LEG_SECONDS,
) -> list[VideoSpec]:
    """Turn a list of files into camera specs.

    A manifest entry wins over every default, field by field: give a lat/lon
    and nothing else and you keep the staggered start times.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for entry in (manifest or {}).get("videos", []):
        key = str(entry.get("path") or entry.get("file") or "")
        by_name[Path(key).name] = entry

    epoch = epoch or datetime.now(UTC).replace(microsecond=0)
    specs: list[VideoSpec] = []

    for index, path in enumerate(paths):
        entry = by_name.get(path.name, {})
        has_position = entry.get("lat") is not None and entry.get("lon") is not None

        start_raw = entry.get("start_at")
        start_at = (
            datetime.fromisoformat(str(start_raw))
            if start_raw
            else epoch + timedelta(seconds=leg_seconds * index)
        )
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=UTC)

        specs.append(
            VideoSpec(
                path=path,
                camera_id=str(entry.get("camera_id") or _slug(path.stem)),
                name=str(entry.get("name") or path.stem),
                lat=float(entry["lat"]) if has_position
                else ORIGIN_LAT + DEG_PER_KM * leg_km * index,
                lon=float(entry["lon"]) if has_position else ORIGIN_LON,
                start_at=start_at,
                synthetic_position=not has_position,
            )
        )

    duplicates = {s.camera_id for s in specs if
                  sum(1 for o in specs if o.camera_id == s.camera_id) > 1}
    if duplicates:
        # Two files with the same stem would share a camera, and `sightings`
        # is UNIQUE (camera_id, track_id) -- the second file's tracks would
        # collide with the first's and vanish through ON CONFLICT DO NOTHING.
        raise ValueError(
            f"two inputs resolve to the same camera id {sorted(duplicates)}; "
            "rename a file or give them distinct camera_ids in the manifest"
        )
    return specs


# ---------------------------------------------------------------------------
# Registry rows
# ---------------------------------------------------------------------------


async def ensure_camera(conn: asyncpg.Connection, spec: VideoSpec) -> None:
    """Upsert the `cameras` row ingest checks for.

    Written straight to the database rather than through the registry's HTTP
    API, because a batch run is not supposed to leave a service listening on
    :8000, and `scripts/survey.py` -- which does go through it -- cannot set
    `plate_viable` or `permitted_violations`, which an offline run wants on.
    The audit row says which path wrote it.
    """
    department_id = await conn.fetchval("SELECT id FROM departments ORDER BY id LIMIT 1")
    if department_id is None:
        raise RuntimeError(
            "no departments row; run `python scripts/migrate.py --seed` first"
        )

    await conn.execute(
        """
        INSERT INTO cameras (camera_id, department_id, name, kind, protocol,
                             location, enabled)
        VALUES ($1, $2, $3, 'ip', 'file',
                ST_SetSRID(ST_MakePoint($5, $4), 4326)::geography, true)
        ON CONFLICT (camera_id) DO UPDATE SET
            name     = EXCLUDED.name,
            location = EXCLUDED.location,
            enabled  = true
        """,
        spec.camera_id, department_id, spec.name, spec.lat, spec.lon,
    )
    await audit.service(
        conn, "ingest-offline", "camera.upsert", "camera", spec.camera_id,
        {"source": str(spec.path), "synthetic_position": spec.synthetic_position},
    )


async def ensure_profile(conn: asyncpg.Connection, spec: VideoSpec) -> None:
    """Write a profile that permits the pipelines an offline run wants.

    Not the section 6 survey, and the row says so: `distortion.survey` carries
    the disclaimer, the same way `scripts/survey.py` marks its provisional
    rows.

    Every enabled violation type is permitted, including the ones needing
    glass penetration or scene context. An arbitrary clip has no stop line or
    signal head to reason about, so `red_light` and `wrong_way` findings here
    rest on nothing measured; they are enabled because the grant is uniform,
    not because the footage supports them.
    """
    violations = [
        r["code"] for r in await conn.fetch(
            "SELECT code FROM violation_types WHERE enabled ORDER BY code"
        )
    ]

    await conn.execute(
        """
        INSERT INTO camera_profiles (
            camera_id, resolution_class, permitted_attributes, permitted_violations,
            plate_viable, density_viable, decode_fps, trust_level, distortion)
        VALUES ($1, 'full', $2, $3, true, true, $4, 0.5, $5)
        ON CONFLICT (camera_id) DO UPDATE SET
            resolution_class     = EXCLUDED.resolution_class,
            permitted_attributes = EXCLUDED.permitted_attributes,
            permitted_violations = EXCLUDED.permitted_violations,
            plate_viable         = EXCLUDED.plate_viable,
            density_viable       = EXCLUDED.density_viable,
            decode_fps           = EXCLUDED.decode_fps,
            distortion           = EXCLUDED.distortion,
            measured_at          = now()
        """,
        spec.camera_id, PERMITTED_ATTRIBUTES, violations,
        settings.target_decode_fps,
        # A dict, not json.dumps of one: the pool registers a jsonb codec, so
        # a pre-encoded string is stored as a JSON *string* and comes back as
        # str -- which is how camera_profiles.distortion reached
        # CameraWorker.prepare() as something with no .get().
        {"survey": "provisional; offline file run, not a section 6 survey"},
    )
    await audit.service(
        conn, "ingest-offline", "camera_profile.upsert", "camera", spec.camera_id,
        {"permitted_violations": violations, "provisional": True},
    )


async def clear_sightings(conn: asyncpg.Connection, camera_ids: list[str]) -> int:
    """Drop a previous run's sightings for these cameras, and what hangs off them.

    Track ids are scoped by a per-run token, so a re-run does not collide with
    the last one -- it accumulates alongside it, and then the same vehicle in
    the same video appears twice in every correlation. Deleting first is what
    makes a re-run mean "re-run" rather than "run again as well".

    Order matters, and it is dictated by which foreign keys cascade and which
    do not. `route_legs.from_read_id` references `sightings` with no cascade,
    so the sightings cannot go until the legs do; and a route stripped of its
    legs is not a preserved audit record, it is a broken one. So the searches
    that produced them go too, which cascades routes and legs cleanly.
    Riders, plate hypotheses and violations cascade from the sighting itself.
    `audit_log` is untouched -- it is append-only and says what happened.
    """
    if not camera_ids:
        return 0

    doomed = ("SELECT read_id FROM sightings WHERE camera_id = ANY($1::text[])")

    # Alerts first: they reference both sightings and routes, and neither
    # reference cascades.
    await conn.execute(
        f"DELETE FROM alerts WHERE read_id IN ({doomed}) OR route_id IN ("
        f"  SELECT r.id FROM routes r JOIN route_legs l ON l.route_id = r.id"
        f"   WHERE l.from_read_id IN ({doomed}) OR l.to_read_id IN ({doomed}))",
        camera_ids,
    )
    # Cascades to routes, which cascades to route_legs.
    await conn.execute(
        f"DELETE FROM searches WHERE id IN ("
        f"  SELECT DISTINCT r.search_id FROM routes r JOIN route_legs l ON l.route_id = r.id"
        f"   WHERE l.from_read_id IN ({doomed}) OR l.to_read_id IN ({doomed}))",
        camera_ids,
    )
    await conn.execute(f"DELETE FROM sighting_events WHERE read_id IN ({doomed})", camera_ids)
    await conn.execute(
        "DELETE FROM pipeline_jobs WHERE camera_id = ANY($1::text[])", camera_ids
    )
    result = await conn.execute(
        "DELETE FROM sightings WHERE camera_id = ANY($1::text[])", camera_ids
    )
    return int(result.rsplit(" ", 1)[-1] or 0)


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


class _SidecarWriter:
    """Per-frame track geometry, one JSON object per line.

    This exists because nothing else keeps it. `sightings` holds one box per
    track -- the best frame -- and ByteTrack's `Track.history` is discarded
    when the track ends. Drawing a box on every frame of the output therefore
    needs the boxes recorded as they go past.

    Keyed on `pts_s`, not a frame index: the annotate pass re-decodes the same
    file with the same rate limiter, and PTS is the one identifier that is a
    property of the video rather than of how it was read.
    """

    def __init__(self, path: Path, spec: VideoSpec) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._fh = path.open("w", encoding="utf-8")
        self.frames = 0
        self.track_ids: set[str] = set()
        self._fh.write(json.dumps({"header": spec.as_dict()}) + "\n")

    def __call__(self, frame: Frame, tracks: list[Track], worker: CameraWorker) -> None:
        boxes = []
        for track in tracks:
            if track.state is not TrackState.CONFIRMED:
                # ByteTrack already hands back only confirmed tracks, so this
                # never fires today. It stays because the observer takes
                # whatever the tracker passes it, and a tentative box is a
                # detection that has not yet earned a line on the video.
                continue
            scoped = worker.scoped_track_id(track)
            self.track_ids.add(scoped)
            x1, y1, x2, y2 = track.bbox
            boxes.append({
                "t": scoped,
                "b": [int(x1), int(y1), int(x2), int(y2)],
                "c": track.cls,
                "s": round(float(track.score), 3),
            })
        self.frames += 1
        self._fh.write(json.dumps({
            "pts": round(frame.pts_s, 4),
            "i": frame.index,
            "at": frame.seen_at.isoformat(),
            "boxes": boxes,
        }) + "\n")

    def close(self, target_fps: float | None = None) -> None:
        """Record the rate the pass actually ran at, then close.

        Written at the end because it is not known at the start: the rate
        comes from the camera's profile, which is read after the worker
        starts. The annotate pass reads it back and decodes at the same rate,
        which is what keeps the two passes selecting the same frames -- a
        camera profile with its own decode_fps would otherwise silently
        desynchronise them.
        """
        if target_fps:
            self._fh.write(json.dumps({"header": {"target_fps": target_fps}}) + "\n")
        self._fh.close()


async def ingest_one(
    spec: VideoSpec, sidecar_dir: Path, detector=None, detect_model_id: int | None = None
) -> VideoResult:
    """Decode one file once, writing sightings and a track sidecar."""
    adapter = FileAdapter({
        "name": "offline",
        "cameras": [{
            "path": str(spec.path), "camera_id": spec.camera_id,
            "name": spec.name, "lat": spec.lat, "lon": spec.lon,
        }],
    })

    sidecar = _SidecarWriter(sidecar_dir / f"{spec.path.stem}.jsonl", spec)
    worker = CameraWorker(
        spec.camera_id, adapter,
        detector=detector, detect_model_id=detect_model_id,
        once=True, fixed_epoch=spec.start_at, observer=sidecar,
    )
    try:
        await worker.run()
    finally:
        sidecar.close(worker.stream.target_fps if worker.stream else None)

    return VideoResult(
        spec=spec,
        sightings=worker.sightings_written,
        frames=sidecar.frames,
        tracks_seen=len(sidecar.track_ids),
        sidecar=sidecar.path,
        stats=worker.stream.stats() if worker.stream else {},
    )


async def run(
    paths: list[Path],
    out_dir: Path,
    *,
    manifest: dict[str, Any] | None = None,
    reset: bool = True,
    leg_km: float = LEG_KM,
    leg_seconds: float = LEG_SECONDS,
) -> dict[str, Any]:
    """Ingest every video, in order, into the same database a camera writes to."""
    specs = resolve(paths, manifest, leg_km=leg_km, leg_seconds=leg_seconds)
    sidecar_dir = out_dir / "tracks"
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    async with transaction() as conn:
        for spec in specs:
            await ensure_camera(conn, spec)
            await ensure_profile(conn, spec)
        removed = (
            await clear_sightings(conn, [s.camera_id for s in specs]) if reset else 0
        )
    if removed:
        log.info("previous_sightings_cleared", count=removed)

    async with acquire() as conn:
        detect_model_id = await conn.fetchval(
            "SELECT id FROM model_versions WHERE role = 'detect' "
            "ORDER BY registered_at DESC LIMIT 1"
        )

    # One detector, shared: loading the weights once per video is the fastest
    # way to spend a minute per file doing nothing.
    detector = load_detector(detect_model_id)

    results: list[VideoResult] = []
    for spec in specs:
        log.info("offline_video_start", camera=spec.camera_id, path=str(spec.path),
                 start_at=spec.start_at.isoformat())
        result = await ingest_one(spec, sidecar_dir, detector, detect_model_id)
        results.append(result)
        log.info("offline_video_done", camera=spec.camera_id,
                 sightings=result.sightings, frames=result.frames,
                 tracks=result.tracks_seen)

    report = {
        "run_at": datetime.now(UTC).isoformat(),
        "detector_stub": getattr(detector, "model_id", None) is None,
        "videos": [
            {
                **r.spec.as_dict(),
                "sightings": r.sightings,
                "frames_annotatable": r.frames,
                "tracks": r.tracks_seen,
                "sidecar": str(r.sidecar),
                "decode": r.stats,
            }
            for r in results
        ],
    }
    manifest_path = out_dir / "run.json"
    manifest_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def main(
    paths: list[str], out: str, manifest_path: str | None, *,
    reset: bool = True, leg_km: float = LEG_KM, leg_seconds: float = LEG_SECONDS,
) -> int:
    """Entry point for `sentinel-ingest offline`."""
    from sentinel.core.db import close_pool

    files = [Path(p).expanduser().resolve() for p in paths]
    missing = [p for p in files if not p.exists()]
    if missing:
        raise SystemExit(f"no such file: {', '.join(str(m) for m in missing)}")

    manifest = (
        json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        if manifest_path else None
    )

    async def _go() -> dict[str, Any]:
        try:
            return await run(
                files, Path(out).expanduser().resolve(), manifest=manifest,
                reset=reset, leg_km=leg_km, leg_seconds=leg_seconds,
            )
        finally:
            await close_pool()

    report = asyncio.run(_go())
    total = sum(v["sightings"] for v in report["videos"])
    if total == 0:
        log.warning(
            "no_sightings",
            reason="the detector found nothing in any input",
            hint=f"is there a model at {settings.detect_model_path}?",
        )
    print(json.dumps(report, indent=2, default=str))
    return 0


__all__ = [
    "VideoSpec", "VideoResult", "resolve", "ensure_camera", "ensure_profile",
    "clear_sightings", "ingest_one", "run", "main",
]
