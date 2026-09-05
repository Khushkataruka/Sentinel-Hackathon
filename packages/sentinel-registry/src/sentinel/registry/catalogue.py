"""Sync from the Sentinel sandbox catalogue.

Written against the real payload, not a guess at it. What GET /cameras.json
actually returns, per camera:

    {"id": "13", "location": "13 CN Vidhyalaya", "live": true,
     "codec": "h264", "width": 1920, "height": 1080, "fps": 25,
     "bitrate_kbps": 0, "rtsp_url": "...", "webrtc_url": "...",
     "hls_live_url": "..."}

Three things about that shape drive everything below.

1. THERE ARE NO COORDINATES. `location` is a human place name -- "13 CN
   Vidhyalaya" -- not a position. Nothing in the catalogue can be put on a
   map. Since cameras.location is NOT NULL, and since every distance, speed
   check and coverage calculation depends on it, a camera with no known
   position cannot be onboarded at all. Positions come from a separate
   coordinate file; see load_coordinates().

2. THE ENDPOINT REQUIRES A SESSION. It 302s to /auth/login for an
   unauthenticated request. Credentials come from the environment, never
   from adapter.toml, and are sent as either a bearer token or a cookie --
   see sentinel.core.gridauth.

   The RTSP and WebRTC URLs authenticate separately, per connection, with
   the registered email and access password in the URL. Those credentials
   are attached at open() time by the adapter and are NOT stored here: what
   this module writes to the adapters table is deliberately bare, because a
   row in a database is not a place to keep a password.

3. CAMERA IDS ARE THE GRID'S, AND THEY HAVE ALREADY CHANGED ONCE -- bare
   integers "1".."30" became "cam01".."cam30". They are unique only within
   this grid, so department scoping matters: a Home Department camera "1"
   and an RTO camera "1" are different cameras.

   The coordinate file was keyed by the old form, and a renamed id is
   indistinguishable from an unknown camera: every one would have been
   skipped for "no coordinate", and the sync would have reported success
   having onboarded nothing. Positions are therefore looked up by a
   canonical id -- see _canonical_id -- so a survey outlives a rename.

The catalogue reports resolution and codec. It does NOT decide capability:
plate_viable, density_viable and permitted_violations all stay false and
empty until the section 6 survey says otherwise, because those are
measurements involving a photograph of a windscreen, not fields in a JSON
document.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from sentinel.core import gridauth, streamurl
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.core.models import CameraIn, CameraProfileIn
from sentinel.core.types import CameraKind, ResolutionClass
from sentinel.registry import repo

log = get_logger(__name__)

DEFAULT_COORDINATE_FILE = Path("db/seed/camera_coordinates.json")

#: How much to trust a camera whose position was eyeballed rather than
#: surveyed. A route leg's distance -- and therefore its speed check -- is
#: only as good as the two positions it runs between, so a guessed position
#: has to weaken every route that passes through it. This multiplies the
#: provisional trust_level; the section 6 survey overwrites it later.
POSITION_QUALITY_TRUST = {
    "ok": 1.00,
    "approx": 0.70,
    "guess": 0.40,
}

UNSURVEYED_TRUST = 0.5


def _canonical_id(value: str) -> str:
    """A camera id reduced to what survives a renaming.

    "cam01", "CAM-1", "01" and "1" are all the same camera on this grid, and
    the estate has already been renumbered once mid-project. Positions are
    surveyed by hand and are expensive to redo, so they are matched on this
    rather than on the literal string the catalogue happens to use today.

    Anything without digits falls back to the lowercased original, so an id
    like "junction-west" still matches itself.
    """
    digits = "".join(c for c in value if c.isdigit())
    if not digits:
        return value.strip().lower()
    return digits.lstrip("0") or "0"


def _pick(entry: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Field names are not contractual either. Try a few spellings."""
    lower = {str(k).lower(): v for k, v in entry.items()}
    for key in keys:
        value = lower.get(key.lower())
        if value not in (None, ""):
            return value
    return default


def _resolution_class(width: int | None, height: int | None) -> ResolutionClass:
    """Coarse banding from frame size, for triage only.

    The real resolution_class comes from measured vehicle width in pixels at
    typical distance (section 6.3), which cannot be derived from frame size
    without knowing how far away the road is. A 1920x1080 camera pointed down
    a long approach can be worse than a 1280x720 one over a junction.
    """
    if not width or not height:
        return ResolutionClass.THUMBNAIL
    if width >= 1600 or height >= 900:
        return ResolutionClass.FULL
    if width >= 800 or height >= 480:
        return ResolutionClass.REDUCED
    return ResolutionClass.THUMBNAIL


def load_coordinates(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Positions for the grid's cameras, keyed by catalogue id.

    The catalogue has none, so this file is the only source. Each entry
    carries a quality of 'ok', 'approx' or 'guess' -- and on the estate as
    shipped, most of them are not 'ok'. That is recorded rather than
    smoothed over, because a route built on guessed positions produces
    speed checks that mean nothing, and the operator deserves to know.
    """
    p = Path(path or os.environ.get("SENTINEL_COORDINATE_FILE") or DEFAULT_COORDINATE_FILE)
    if not p.exists():
        log.warning("no_coordinate_file", path=str(p),
                    consequence="cameras without a position cannot be onboarded")
        return {}

    data = json.loads(p.read_text())
    entries = data.get("cameras", data) if isinstance(data, dict) else data
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        cid = str(entry.get("id") or entry.get("camera_id") or "")
        if cid and entry.get("lat") is not None and entry.get("lon") is not None:
            out[cid] = entry
    # Aliases, added after every literal id so a real id is never shadowed
    # by another camera's canonical form.
    for cid in list(out):
        out.setdefault(_canonical_id(cid), out[cid])

    qualities: dict[str, int] = {}
    for entry in out.values():
        q = entry.get("quality", "unstated")
        qualities[q] = qualities.get(q, 0) + 1
    log.info("coordinates_loaded", path=str(p), cameras=len(out), quality=qualities)
    return out


async def fetch_catalogue(base_url: str | None = None) -> list[dict[str, Any]]:
    """The catalogue, from the first path that answers JSON.

    The grid renamed the endpoint from /api/ingest to /cameras.json. Both
    are tried, in configured order, so a sync does not fail on an estate
    that has not moved yet -- and only a 404 advances to the next one, since
    a 401 or a sign-in redirect means the path was right and the session
    was not.
    """
    urls = settings.sentinel_catalogue_urls(base_url)
    if not urls or urls[0].startswith("/"):
        raise RuntimeError("SENTINEL_SENTINEL_BASE_URL is not configured")

    body: Any = None
    tried: list[str] = []
    async with httpx.AsyncClient(
        timeout=settings.sentinel_http_timeout,
        follow_redirects=True,
        headers=gridauth.session_headers(),
        cookies=gridauth.session_cookies(),
    ) as client:
        for url in urls:
            response = await client.get(url)
            if response.status_code == 404 and url != urls[-1]:
                tried.append(f"{url}: 404")
                continue
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "json" not in content_type.lower():
                # We were handed the sign-in page. Say so plainly: a sync
                # that reported "0 cameras" here would look like an empty grid.
                raise RuntimeError(
                    f"{url} returned {content_type or 'no content-type'}, not JSON "
                    "-- the catalogue needs a session. Set SENTINEL_SENTINEL_COOKIE "
                    "or SENTINEL_SENTINEL_TOKEN."
                )
            body = response.json()
            if tried:
                log.info("catalogue_path_fallback", used=url, skipped=tried)
            break
        else:
            raise RuntimeError("no catalogue path answered -- " + "; ".join(tried))

    if isinstance(body, dict):
        for key in ("cameras", "streams", "items", "data", "results"):
            if isinstance(body.get(key), list):
                return body[key]
        return [body]
    return list(body)


async def sync(
    conn: asyncpg.Connection,
    *,
    department_id: int,
    base_url: str | None = None,
    adapter_id: int | None = None,
    coordinate_file: Path | str | None = None,
) -> dict[str, Any]:
    """Upsert every camera the catalogue lists that we have a position for."""
    entries = await fetch_catalogue(base_url)
    coordinates = load_coordinates(coordinate_file)

    synced: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for entry in entries:
        camera_id = _pick(entry, "id", "camera_id", "stream_id")
        if camera_id is None:
            skipped.append({"reason": "no id field", "entry": str(entry)[:160]})
            continue
        camera_id = str(camera_id)

        position = coordinates.get(camera_id) or coordinates.get(
            _canonical_id(camera_id)
        )
        if not position:
            # A camera with no position cannot go on the map, cannot take
            # part in coverage analysis, and cannot have a route leg
            # validated against it. Reported, not invented.
            skipped.append({
                "camera_id": camera_id,
                "location": str(_pick(entry, "location", default="")),
                "reason": "no coordinate for this id; add one to the coordinate file",
            })
            continue

        quality = str(position.get("quality", "guess"))
        width = _pick(entry, "width", "frame_width")
        height = _pick(entry, "height", "frame_height")
        codec = str(_pick(entry, "codec", "video_codec", default="") or "")
        declared_fps = _pick(entry, "fps", "framerate", "frame_rate")
        # Bare, and pointed at the host that can actually serve it. The
        # catalogue's RTSP entry may name the CDN, which cannot carry RTSP,
        # and may arrive with credentials already on it, which must not be
        # written to a database row. The adapter re-attaches them at open().
        rtsp = streamurl.retarget(
            streamurl.strip_credentials(str(
                _pick(entry, "rtsp_url", "rtsp", "rtspUrl") or ""
            )),
            settings.grid_media_host or None, settings.grid_rtsp_port,
        ) or None
        hls = _pick(entry, "hls_live_url", "hls_url", "hls")

        # `location` is a place name, which is exactly what an operator wants
        # to read on an alert. It is the camera's name, not its position.
        name = str(_pick(entry, "location", "name", "title", default=camera_id))

        await repo.upsert_camera(conn, CameraIn(
            camera_id=camera_id,
            department_id=department_id,
            name=name,
            kind=CameraKind.IP,
            vendor="sentinel-grid",
            # How ingest will actually decode this camera, which is the
            # deployment's choice: RTSP off the gateway, or HLS off the CDN
            # where 8554/TCP is closed.
            protocol="hls" if settings.grid_prefer_hls else "rtsp",
            adapter_id=adapter_id,
            lat=float(position["lat"]),
            lon=float(position["lon"]),
            storage_kind="grid",
            enabled=bool(_pick(entry, "live", "online", "enabled", default=True)),
        ))

        # Provisional profile, only where none exists. Never overwrite a survey.
        if await repo.get_profile(conn, camera_id) is None:
            await repo.upsert_profile(conn, camera_id, CameraProfileIn(
                resolution_class=_resolution_class(
                    int(width) if width else None, int(height) if height else None
                ),
                permitted_attributes=[],     # nothing until surveyed
                permitted_violations=[],     # nothing until surveyed
                plate_viable=False,
                density_viable=False,
                # A decoder hint only. The declared rate does not match the
                # delivery rate, and nothing time-derived may read it.
                decode_fps=min(float(declared_fps), settings.target_decode_fps)
                if declared_fps else settings.target_decode_fps,
                trust_level=round(
                    UNSURVEYED_TRUST * POSITION_QUALITY_TRUST.get(quality, 0.4), 3
                ),
                loop_period_s=None,          # measure with `sentinel-ingest preflight`
            ))

        if adapter_id is not None:
            await conn.execute(
                """
                UPDATE adapters SET config = jsonb_set(
                    COALESCE(config, '{}'), ARRAY['cameras', $2], $3::jsonb, true)
                 WHERE id = $1
                """,
                adapter_id, camera_id,
                {"rtsp_url": rtsp, "hls_url": hls, "codec": codec,
                 "width": width, "height": height,
                 "declared_fps": declared_fps, "position_quality": quality},
            )

        synced.append({"camera_id": camera_id, "name": name,
                       "position_quality": quality, "codec": codec or "unstated"})

    quality_counts: dict[str, int] = {}
    for item in synced:
        q = item["position_quality"]
        quality_counts[q] = quality_counts.get(q, 0) + 1

    log.info("catalogue_sync", synced=len(synced), skipped=len(skipped),
             position_quality=quality_counts)

    return {
        "synced": [s["camera_id"] for s in synced],
        "cameras": synced,
        "skipped": skipped,
        "count": len(synced),
        "position_quality": quality_counts,
        # Surfaced, not buried: routes through eyeballed positions have
        # distances that were eyeballed too.
        "warning": (
            f"{quality_counts.get('guess', 0)} of {len(synced)} cameras have "
            "guessed positions; their route distances and speed checks are "
            "unreliable and their trust_level is reduced accordingly"
        ) if quality_counts.get("guess") else None,
    }
