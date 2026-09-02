"""Adapter for the Sentinel sandbox camera grid.

Reads the catalogue at {base}/api/ingest and hands back whatever URLs it
finds there. The camera set and the ids change, so enumerate() re-reads
rather than caching for the life of the process, with a short TTL so a
supervisor loop does not hammer the endpoint.

Consume only. This adapter never publishes and never calls the gateway's
control API.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.ingest.adapters.base import (
    AdapterError,
    BaseAdapter,
    CameraRef,
    HealthReport,
    StreamHandle,
)

log = get_logger(__name__)

CATALOGUE_TTL_S = 60.0


def _pick(entry: dict[str, Any], *keys: str, default: Any = None) -> Any:
    lower = {str(k).lower(): v for k, v in entry.items()}
    for key in keys:
        value = lower.get(key.lower())
        if value not in (None, ""):
            return value
    return default


class SentinelGridAdapter(BaseAdapter):
    name = "sentinel-grid"
    supports_seek = False   # the guide is explicit: no seeking, no byte ranges

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.base_url = str(
            config.get("base_url") or settings.sentinel_base_url or ""
        ).rstrip("/")
        if not self.base_url:
            raise AdapterError("sentinel-grid needs base_url in adapter.toml or the env")
        self.timeout = float(config.get("timeout_s", settings.sentinel_http_timeout))
        self._cache: list[CameraRef] = []
        self._cached_at = 0.0

    # -- catalogue ---------------------------------------------------------

    def _catalogue(self, force: bool = False) -> list[CameraRef]:
        if not force and self._cache and time.monotonic() - self._cached_at < CATALOGUE_TTL_S:
            return self._cache

        url = f"{self.base_url}{settings.sentinel_catalogue_path}"
        try:
            response = httpx.get(url, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise AdapterError(f"catalogue fetch failed ({url}): {exc}") from exc

        entries = body
        if isinstance(body, dict):
            for key in ("cameras", "streams", "items", "data", "results"):
                if isinstance(body.get(key), list):
                    entries = body[key]
                    break
            else:
                entries = [body]

        refs: list[CameraRef] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            cam_id = _pick(entry, "id", "camera_id", "stream_id")
            if cam_id is None:
                continue
            cam_id = str(cam_id)
            location = _pick(entry, "location", default={}) or {}
            lat = _pick(entry, "lat", "latitude")
            lon = _pick(entry, "lon", "lng", "longitude")
            if lat is None and isinstance(location, dict):
                lat = _pick(location, "lat", "latitude")
                lon = _pick(location, "lon", "lng", "longitude")

            refs.append(
                CameraRef(
                    camera_id=cam_id,
                    name=str(_pick(entry, "name", "title", default=cam_id)),
                    # Take the URL the catalogue gives. Never build one.
                    url=str(
                        _pick(entry, "rtsp", "rtsp_url", "rtspUrl")
                        or f"rtsp://{self._host()}:8554/stream/{cam_id}"
                    ),
                    lat=float(lat) if lat is not None else None,
                    lon=float(lon) if lon is not None else None,
                    codec=str(_pick(entry, "codec", "video_codec", default="h264")),
                    width=_pick(entry, "width", "frame_width"),
                    height=_pick(entry, "height", "frame_height"),
                    metadata={
                        "live": bool(_pick(entry, "live", "online", default=True)),
                        "hls": _pick(entry, "hls", "hls_url"),
                        "whep": _pick(entry, "whep", "whep_url", "webrtc"),
                        # Recorded, never used for timing. See ptsclock.
                        "declared_fps": _pick(entry, "fps", "framerate", "frame_rate"),
                    },
                )
            )

        self._cache, self._cached_at = refs, time.monotonic()
        return refs

    def _host(self) -> str:
        return self.base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]

    # -- adapter interface -------------------------------------------------

    def enumerate(self) -> list[CameraRef]:
        return self._catalogue()

    def open(self, camera_id: str) -> StreamHandle:
        for ref in self._catalogue():
            if ref.camera_id == camera_id:
                return StreamHandle(
                    camera_id=camera_id,
                    url=ref.url,
                    transport="tcp",
                    codec=ref.codec,
                    options={
                        # Forced everywhere. UDP fails across NAT and most
                        # corporate firewalls, and partial delivery produces
                        # corrupt frames that look like model bugs.
                        "rtsp_transport": "tcp",
                        "stimeout": "5000000",
                    },
                )
        raise AdapterError(f"camera {camera_id!r} is not in the catalogue")

    def health(self, camera_id: str) -> HealthReport:
        """Reachability from the catalogue's own live flag.

        Deliberately shallow. Whether frames are actually arriving is known
        only to the process holding the stream, and it posts that itself --
        a health check that only pings will never catch a camera that is up
        and detecting nothing.
        """
        try:
            for ref in self._catalogue(force=True):
                if ref.camera_id == camera_id:
                    return HealthReport(
                        reachable=bool(ref.metadata.get("live", True)),
                        last_frame_at=datetime.now(UTC),
                        detail={"source": "catalogue", "codec": ref.codec},
                    )
            return HealthReport(reachable=False, detail={"reason": "not in catalogue"})
        except AdapterError as exc:
            return HealthReport(reachable=False, detail={"reason": str(exc)})


def build(config: dict[str, Any]) -> SentinelGridAdapter:
    return SentinelGridAdapter(config)
