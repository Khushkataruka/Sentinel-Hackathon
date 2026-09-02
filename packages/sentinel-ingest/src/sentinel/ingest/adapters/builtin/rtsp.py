"""Generic RTSP adapter.

For a department that will simply give us URLs. Cameras are listed in
adapter.toml; nothing is discovered.

    name = "district-rtsp"
    driver = "sentinel.ingest.adapters.builtin.rtsp"

    [[cameras]]
    camera_id = "VAL-014"
    name = "Valsad Toll North"
    url = "rtsp://10.4.2.11:554/Streaming/Channels/101"
    lat = 20.5992
    lon = 72.9342
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sentinel.ingest.adapters.base import (
    AdapterError,
    BaseAdapter,
    CameraRef,
    HealthReport,
    StreamHandle,
)


class RtspAdapter(BaseAdapter):
    name = "rtsp"
    supports_seek = False

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.timeout = float(config.get("timeout_s", 5.0))
        self._cameras: dict[str, CameraRef] = {}
        for entry in config.get("cameras", []):
            if not entry.get("camera_id") or not entry.get("url"):
                raise AdapterError("every [[cameras]] entry needs camera_id and url")
            ref = CameraRef(
                camera_id=str(entry["camera_id"]),
                name=str(entry.get("name", entry["camera_id"])),
                url=str(entry["url"]),
                lat=entry.get("lat"),
                lon=entry.get("lon"),
                codec=entry.get("codec"),
                metadata={k: v for k, v in entry.items() if k not in
                          {"camera_id", "name", "url", "lat", "lon", "codec"}},
            )
            self._cameras[ref.camera_id] = ref
        if not self._cameras:
            raise AdapterError("no [[cameras]] entries in adapter.toml")

    def enumerate(self) -> list[CameraRef]:
        return list(self._cameras.values())

    def open(self, camera_id: str) -> StreamHandle:
        ref = self._cameras.get(camera_id)
        if ref is None:
            raise AdapterError(f"unknown camera {camera_id!r}")
        return StreamHandle(
            camera_id=camera_id, url=ref.url, transport="tcp", codec=ref.codec,
            options={"rtsp_transport": "tcp", "stimeout": "5000000"},
        )

    def health(self, camera_id: str) -> HealthReport:
        """A TCP connect to the RTSP port.

        This proves the port answers and nothing else. It is the check the
        design document calls insufficient on its own, which is why ingest
        posts frame rate and detection count separately.
        """
        ref = self._cameras.get(camera_id)
        if ref is None:
            return HealthReport(reachable=False, detail={"reason": "unknown camera"})
        parsed = urlparse(ref.url)
        host, port = parsed.hostname, parsed.port or 554
        if not host:
            return HealthReport(reachable=False, detail={"reason": "no host in url"})
        try:
            with socket.create_connection((host, port), timeout=self.timeout):
                return HealthReport(
                    reachable=True, last_frame_at=datetime.now(UTC),
                    detail={"check": "tcp_connect", "port": port},
                )
        except OSError as exc:
            return HealthReport(reachable=False, detail={"reason": str(exc)})


def build(config: dict[str, Any]) -> RtspAdapter:
    return RtspAdapter(config)
