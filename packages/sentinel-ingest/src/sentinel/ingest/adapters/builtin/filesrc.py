"""Local video file adapter.

Exists so the pipeline can be developed and tested with no sandbox, no
network and no cameras. Points at video files on disk and loops them, which
also makes it the honest way to exercise the loop-point discontinuity
handling that the live grid will trigger.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sentinel.ingest.adapters.base import (
    AdapterError,
    BaseAdapter,
    CameraRef,
    HealthReport,
    StreamHandle,
)


class FileAdapter(BaseAdapter):
    name = "file"
    supports_seek = True   # a local file is the one case where seeking is free

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        root = Path(str(config.get("root", "./var/samples"))).expanduser()
        pattern = str(config.get("pattern", "*.mp4"))
        self._cameras: dict[str, CameraRef] = {}

        for entry in config.get("cameras", []):
            path = Path(str(entry["path"])).expanduser()
            cam_id = str(entry.get("camera_id", path.stem))
            self._cameras[cam_id] = CameraRef(
                camera_id=cam_id, name=str(entry.get("name", cam_id)), url=str(path),
                lat=entry.get("lat"), lon=entry.get("lon"),
            )

        if not self._cameras and root.exists():
            for path in sorted(root.glob(pattern)):
                self._cameras[path.stem] = CameraRef(
                    camera_id=path.stem, name=path.stem, url=str(path)
                )

        if not self._cameras:
            raise AdapterError(f"no video files found under {root} matching {pattern!r}")

    def enumerate(self) -> list[CameraRef]:
        return list(self._cameras.values())

    def open(self, camera_id: str) -> StreamHandle:
        ref = self._cameras.get(camera_id)
        if ref is None:
            raise AdapterError(f"unknown camera {camera_id!r}")
        if not Path(ref.url).exists():
            raise AdapterError(f"file has gone: {ref.url}")
        return StreamHandle(camera_id=camera_id, url=ref.url, transport="file")

    def health(self, camera_id: str) -> HealthReport:
        ref = self._cameras.get(camera_id)
        exists = ref is not None and Path(ref.url).exists()
        return HealthReport(
            reachable=exists,
            last_frame_at=datetime.now(UTC) if exists else None,
            detail={"path": ref.url if ref else None},
        )

    def seek(self, camera_id: str, ts: datetime) -> StreamHandle:
        handle = self.open(camera_id)
        handle.options["seek_to"] = ts.isoformat()
        return handle


def build(config: dict[str, Any]) -> FileAdapter:
    return FileAdapter(config)
