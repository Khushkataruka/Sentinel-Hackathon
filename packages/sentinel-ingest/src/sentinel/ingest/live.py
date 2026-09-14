"""A bounded, atomic frame ring shared by ingest and the playback API.

Each slot contains its geometry and JPEG together. Readers can never pair a
new image with old boxes, including across a loop cut or worker restart.
The ring uses arrival time for retention and delay; the original PTS-derived
timestamp is preserved separately and displayed to the operator.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
from sentinel.core.config import settings
from sentinel.ingest.decode import Frame
from sentinel.ingest.detect import VEHICLE_CLASSES, NullDetector
from sentinel.ingest.track import Track, TrackState

if TYPE_CHECKING:
    from sentinel.ingest.worker import CameraWorker


class FrameBuffer:
    VIEWER_TTL_S = 15

    def __init__(self, camera_id: str) -> None:
        # Camera IDs come from external catalogues, never from filesystem paths.
        key = hashlib.sha256(camera_id.encode()).hexdigest()
        self.directory = settings.media_root / "annotated" / key
        self.fps = settings.annotated_feed_fps
        self.delay = settings.annotated_feed_delay_s
        self.slots = math.ceil((self.delay + 15) * self.fps) + 1

    def _path(self, tick: int) -> Path:
        return self.directory / f"{tick % self.slots:05d}.frame"

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            try:
                file.write(data)
                file.flush()
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)

    def requested(self, now: float) -> bool:
        try:
            return float((self.directory / "viewer-until").read_text()) > now
        except (OSError, ValueError):
            return False

    def request_view(self) -> None:
        """A renewable lease shared by viewers and independent API processes."""
        self.directory.mkdir(parents=True, exist_ok=True)
        now = time.time()
        if not self.requested(now + self.VIEWER_TTL_S - 5):
            self._atomic_write(
                self.directory / "viewer-until", str(now + self.VIEWER_TTL_S).encode()
            )

    def clear(self) -> None:
        for path in self.directory.glob("*.frame"):
            path.unlink(missing_ok=True)
        (self.directory / "latest.json").unlink(missing_ok=True)

    def write(self, metadata: dict, jpeg: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        tick = math.floor(metadata["received_at"] * self.fps)
        self._atomic_write(self._path(tick), json.dumps(metadata).encode() + b"\n" + jpeg)
        self._atomic_write(self.directory / "latest.json", json.dumps(metadata).encode())

    def read(self, now: float | None = None) -> tuple[dict, bytes | None]:
        now = time.time() if now is None else now
        base = {"delay_s": self.delay, "fps": self.fps}
        try:
            latest = json.loads((self.directory / "latest.json").read_bytes())
        except (OSError, ValueError):
            return {**base, "state": "waiting", "wait_s": self.delay}, None
        if now - latest["received_at"] > 10:
            return {**base, "state": "stalled"}, None

        target = now - self.delay
        tick = math.floor(target * self.fps)
        # A modest gap is normal with variable source FPS or slow inference.
        # Never silently replay a frozen frame indefinitely.
        for candidate in range(tick, tick - math.ceil(3 * self.fps) - 1, -1):
            try:
                header, jpeg = self._path(candidate).read_bytes().split(b"\n", 1)
                metadata = json.loads(header)
            except (OSError, ValueError):
                continue
            if (
                metadata["session"] == latest["session"]
                and target - 3 <= metadata["received_at"] <= target
            ):
                return {**base, "state": "playing", **metadata}, jpeg

        wait = max(0, latest["started_at"] + self.delay - now)
        return {
            **base,
            "state": "buffering" if wait > 0 else "gap",
            "wait_s": math.ceil(wait),
        }, None


class LivePublisher:
    def __init__(self, camera_id: str) -> None:
        self.buffer = FrameBuffer(camera_id)
        self.started_at: float | None = None
        self.last_tick: int | None = None
        self.session = uuid.uuid4().hex
        self._active = False
        self._next_viewer_check = 0.0

    def publish(self, frame: Frame, tracks: list[Track], worker: CameraWorker) -> None:
        now = time.time()
        if now >= self._next_viewer_check:
            requested = self.buffer.requested(now)
            if self._active and not requested:
                self.buffer.clear()
                self.started_at = None
                self.last_tick = None
                self.session = uuid.uuid4().hex
            self._active = requested
            self._next_viewer_check = now + 1
        if not self._active:
            return
        tick = math.floor(now * self.buffer.fps)
        if tick == self.last_tick:
            return
        height, width = frame.image.shape[:2]
        output_width = min(width, settings.annotated_feed_width)
        output_height = max(1, round(height * output_width / width))
        image = cv2.resize(frame.image, (output_width, output_height))
        sx, sy = output_width / width, output_height / height
        boxes = [
            {
                "t": worker.scoped_track_id(track),
                "b": [round(v * s) for v, s in zip(track.bbox, (sx, sy, sx, sy), strict=True)],
                "c": track.cls,
                "s": round(float(track.score), 3),
            }
            for track in tracks
            if track.state is TrackState.CONFIRMED and track.cls in VEHICLE_CLASSES
        ]
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise RuntimeError("Could not encode buffered camera frame")
        if self.started_at is None:
            self.started_at = now
        self.buffer.write(
            {
                "session": self.session,
                "received_at": now,
                "started_at": self.started_at,
                "seen_at": frame.seen_at.isoformat(),
                "boxes": boxes,
                "detector_unavailable": isinstance(worker.detector, NullDetector),
            },
            encoded.tobytes(),
        )
        self.last_tick = tick
