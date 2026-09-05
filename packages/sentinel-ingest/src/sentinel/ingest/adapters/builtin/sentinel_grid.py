"""Adapter for the Sentinel sandbox camera grid.

Reads the catalogue at {base}/cameras.json and hands back the URLs it finds
there. The camera set and the ids change, so enumerate() re-reads rather
than caching for the life of the process, with a short TTL so a supervisor
loop does not hammer the endpoint.

Two facts about this grid shape the code below.

  1. THE MEDIA IS NOT ON THE CATALOGUE HOST. The catalogue is served through
     a CDN; RTSP and WebRTC cannot be, so they come off the gateway's own
     address on 8554/8889. Catalogue entries written for a browser can still
     name the CDN host, which resolves and then carries nothing, so RTSP and
     WHEP URLs are retargeted onto grid_media_host.

  2. EVERY RTSP CONNECTION AUTHENTICATES. The registered email and the
     access password go in the URL, percent-encoded. That makes the URL a
     secret, so it is assembled in open() and never in enumerate(): a
     CameraRef is persisted to the adapters table and served over the API,
     and must stay credential-free.

Consume only. This adapter never publishes and never calls the gateway's
control API.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sentinel.core import gridauth, streamurl
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

        # Credentials come from the environment. adapter.toml may name the
        # media host and ports -- deployment topology, not a secret -- but
        # deliberately cannot carry the password: adapter folders get copied
        # between machines and committed by accident, sessions do not.
        self.media_host = str(config.get("media_host", settings.grid_media_host) or "")
        self.rtsp_port = int(config.get("rtsp_port", settings.grid_rtsp_port))
        self.whep_port = int(config.get("whep_port", settings.grid_whep_port))
        self.prefer_hls = bool(config.get("prefer_hls", settings.grid_prefer_hls))

        self._cache: list[CameraRef] = []
        self._cached_at = 0.0

    # -- catalogue ---------------------------------------------------------

    def _fetch_catalogue(self) -> Any:
        """GET the catalogue, trying each known path, with the session.

        Without a session the CDN answers a redirect to the sign-in page and
        an HTML body. Parsing that as JSON fails with something unhelpful, so
        the content type is checked and the missing credential is named.
        """
        errors: list[str] = []
        for url in settings.sentinel_catalogue_urls(self.base_url):
            try:
                response = httpx.get(
                    url,
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers=gridauth.session_headers(),
                    cookies=gridauth.session_cookies(),
                )
                if response.status_code == 404:
                    errors.append(f"{url}: 404")
                    continue
                response.raise_for_status()
                if "json" not in response.headers.get("content-type", "").lower():
                    raise AdapterError(
                        f"{url} answered HTML, not JSON -- the catalogue needs a "
                        "session. Set SENTINEL_SENTINEL_COOKIE or "
                        "SENTINEL_SENTINEL_TOKEN."
                    )
                return response.json()
            except AdapterError:
                raise
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        raise AdapterError("catalogue fetch failed -- " + "; ".join(errors))

    def _catalogue(self, force: bool = False) -> list[CameraRef]:
        if not force and self._cache and time.monotonic() - self._cached_at < CATALOGUE_TTL_S:
            return self._cache

        body = self._fetch_catalogue()

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

            # Prefer the catalogue's own URLs, fall back to the documented
            # pattern, then move the media ones onto the host that can
            # actually serve them. All three stay credential-free: open()
            # attaches the password, and only in the handle it returns.
            rtsp = streamurl.retarget(
                streamurl.strip_credentials(str(
                    _pick(entry, "rtsp", "rtsp_url", "rtspUrl")
                    or streamurl.rtsp_url(cam_id, self._media_host(), self.rtsp_port)
                )),
                self.media_host or None, self.rtsp_port,
            )
            whep_raw = _pick(entry, "whep", "whep_url", "webrtc", "webrtc_url")
            whep = streamurl.retarget(
                streamurl.strip_credentials(str(
                    whep_raw or streamurl.whep_url(cam_id, self._media_host(), self.whep_port)
                )),
                self.media_host or None, self.whep_port,
            )
            # HLS is the one endpoint the CDN can carry, so it keeps the
            # catalogue's host and is gated by the session, not by userinfo.
            hls = str(
                _pick(entry, "hls", "hls_url", "hls_live_url")
                or streamurl.hls_url(cam_id, self.base_url)
            )

            refs.append(
                CameraRef(
                    camera_id=cam_id,
                    name=str(_pick(entry, "name", "title", default=cam_id)),
                    url=hls if self.prefer_hls else rtsp,
                    lat=float(lat) if lat is not None else None,
                    lon=float(lon) if lon is not None else None,
                    codec=str(_pick(entry, "codec", "video_codec", default="h264")),
                    width=_pick(entry, "width", "frame_width"),
                    height=_pick(entry, "height", "frame_height"),
                    metadata={
                        "live": bool(_pick(entry, "live", "online", default=True)),
                        "rtsp": rtsp,
                        "hls": hls,
                        "whep": whep,
                        # Recorded, never used for timing. See ptsclock.
                        "declared_fps": _pick(entry, "fps", "framerate", "frame_rate"),
                    },
                )
            )

        self._cache, self._cached_at = refs, time.monotonic()
        return refs

    def _catalogue_host(self) -> str:
        return self.base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]

    def _media_host(self) -> str:
        """Where RTSP and WHEP are served. The catalogue host only if no
        media host is configured, which is the single-host deployment."""
        return self.media_host or self._catalogue_host()

    # -- adapter interface -------------------------------------------------

    def enumerate(self) -> list[CameraRef]:
        return self._catalogue()

    def open(self, camera_id: str) -> StreamHandle:
        """The only place a credentialed URL is built.

        Everything upstream of here -- CameraRef, the adapters table, the
        API, the logs -- carries the bare URL. What comes back from this
        method is a secret, and decode.py redacts it before logging.
        """
        for ref in self._catalogue():
            if ref.camera_id != camera_id:
                continue

            if self.prefer_hls:
                # No userinfo: the CDN authenticates with the session, and
                # FFmpeg has to be handed it as a header.
                url = str(ref.metadata.get("hls") or ref.url)
                options = {}
                cookie = gridauth.cookie_header()
                if cookie:
                    options["headers"] = f"Cookie: {cookie}"
                return StreamHandle(
                    camera_id=camera_id, url=url,
                    # Not RTSP, so the transport field says how the fetch is
                    # done rather than which RTSP interleaving to force. HLS
                    # is TCP either way, and the loader's TCP check only
                    # looks at rtsp:// URLs.
                    transport="tcp", codec=ref.codec, options=options,
                )

            url = streamurl.with_credentials(
                str(ref.metadata.get("rtsp") or ref.url),
                settings.grid_email, settings.grid_password,
            )
            if not settings.grid_email or not settings.grid_password:
                # Not fatal: a grid with an open gateway still works, and a
                # deployment mid-rollout should get a stream rather than an
                # exception. But this is the shape of an auth failure that
                # otherwise surfaces as a silent 401 at connect.
                log.warning(
                    "grid_credentials_missing", camera=camera_id,
                    detail="RTSP authenticates per connection; set "
                           "SENTINEL_GRID_EMAIL and SENTINEL_GRID_PASSWORD",
                )
            return StreamHandle(
                camera_id=camera_id,
                url=url,
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
