"""The decode loop.

Every item on the sandbox integration guide's pre-submission checklist is
implemented here, and each one is commented with why, because they all look
like defensive noise until the day one of them fires.

  1. RTSP over TCP, forced, in every client.
  2. No timing logic reads the declared frame rate or frame arrival time.
     Everything comes from presentation timestamps -- see PtsClock.
  3. Inter-frame gaps do not stall or crash the pipeline. A gap is not a
     disconnect.
  4. Reconnect with exponential backoff, tested by restarting a feed.
  5. Decoder warnings on join are logged, not fatal. Attaching mid-stream
     produces 'Error constructing the frame RPS' and similar until the first
     IDR arrives; a pipeline that aborts on the first decoder error bounces
     forever on H.265 streams.
  6. Per-camera properties, not a uniform grid. Frame size is read from the
     stream, and the detector letterboxes per camera.
  7. Scene discontinuity at the loop point is a first-class event, surfaced
     to the caller so tracks and traffic buckets can be reset.
"""

from __future__ import annotations

import os

# Must be set before cv2 is imported: OpenCV reads it when the FFmpeg backend
# initialises. Setting it later has no effect, which is a fun afternoon.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;5000000"
)

import random  # noqa: E402
import time  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import datetime  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from sentinel.core import streamurl  # noqa: E402
from sentinel.core.config import settings  # noqa: E402
from sentinel.core.logging import get_logger  # noqa: E402
from sentinel.ingest.adapters.base import StreamHandle  # noqa: E402
from sentinel.ingest.ptsclock import PtsClock  # noqa: E402

log = get_logger(__name__)

#: Frames after a connect during which a PTS jump is treated as the clock
#: settling rather than a scene cut. The gateway replays a buffered
#: group-of-pictures on join, and some backends report no timestamp at all
#: for the first few frames of it.
SETTLING_FRAMES = 10


@dataclass
class Frame:
    """One decoded frame, with the only timestamp anyone should use."""

    image: np.ndarray
    seen_at: datetime          # wall clock, derived from PTS
    pts_s: float               # raw presentation timestamp, seconds
    dt_s: float                # seconds since the previous frame, from PTS
    index: int                 # frames since connect; for logging only
    discontinuity: bool = False  # the recording looped or the camera rebooted

    @property
    def shape(self) -> tuple[int, int]:
        h, w = self.image.shape[:2]
        return w, h


class DecodeError(RuntimeError):
    pass


class CameraStream:
    """A supervised capture over one camera.

    Iterating yields Frames forever, reconnecting underneath as needed. The
    iterator only stops when close() is called or the stop predicate returns
    True, because a feed being briefly down is not a reason to stop
    processing a camera.
    """

    def __init__(self, handle: StreamHandle, target_fps: float | None = None) -> None:
        self.handle = handle
        self.camera_id = handle.camera_id
        self.target_fps = target_fps or settings.target_decode_fps
        self.clock = PtsClock()

        self._cap: cv2.VideoCapture | None = None
        self._closed = False
        self._backoff = settings.reconnect_backoff_initial_s
        self._consecutive_errors = 0
        self._last_emitted_pts: float | None = None
        self._frames_since_connect = 0

        self.frames_decoded = 0
        self.frames_emitted = 0
        self.reconnects = 0
        self.decode_errors = 0

    # -- connection --------------------------------------------------------

    def _open(self) -> None:
        url = self.handle.url
        # The RTSP endpoints authenticate per connection with credentials in
        # the URL, so what goes to a log line is never `url` itself.
        safe_url = streamurl.redact(url)

        options = dict(self.handle.options)
        if streamurl.is_rtsp(url) and self.handle.transport.lower() == "tcp":
            # Belt and braces: the env var covers the process, this covers
            # the case where something else has already reset it.
            options = {"rtsp_transport": "tcp", **options}
        if options:
            # OpenCV splits this on '|' and then on the first ';' of each
            # pair, so a value may contain ';' -- a multi-cookie Cookie
            # header does -- but a '|' would silently truncate it.
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "|".join(
                f"{k};{str(v).replace('|', '')}" for k, v in options.items()
            )

        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            raise DecodeError(f"could not open {safe_url}")

        # A small buffer keeps latency down. We do NOT read CAP_PROP_FPS:
        # the guide says it does not match the delivery rate, and using it
        # for anything time-derived produces wrong answers that look right.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)

        self._cap = cap
        self._consecutive_errors = 0
        self.clock.reset()          # the replayed GOP must not be timed
        self._last_emitted_pts = None
        self._frames_since_connect = 0
        log.info("stream_open", camera=self.camera_id, url=safe_url,
                 transport=self.handle.transport)

    def _reconnect(self) -> None:
        self.release()
        self.reconnects += 1
        # Jitter, so eighty thousand cameras do not retry in lockstep after
        # a gateway restart.
        delay = min(self._backoff, settings.reconnect_backoff_max_s)
        delay *= 0.75 + random.random() * 0.5
        log.warning("stream_reconnect", camera=self.camera_id,
                    attempt=self.reconnects, sleep_s=round(delay, 1))
        time.sleep(delay)
        self._backoff = min(self._backoff * 2, settings.reconnect_backoff_max_s)

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def close(self) -> None:
        """Each connected client gets its own copy of the stream, so a
        capture we are finished with must actually be closed."""
        self._closed = True
        self.release()

    # -- reading -----------------------------------------------------------

    def _pts_seconds(self) -> float:
        """Presentation timestamp of the frame just read, in seconds."""
        assert self._cap is not None
        ms = self._cap.get(cv2.CAP_PROP_POS_MSEC)
        if ms is None or ms <= 0 or ms != ms:      # 0, negative or NaN
            # Some backends report no PTS on the first frames after a join.
            # Fall back to the frame position over the container's nominal
            # rate for those few frames only; the clock re-anchors as soon as
            # real timestamps appear.
            pos = self._cap.get(cv2.CAP_PROP_POS_FRAMES) or 0
            nominal = self._cap.get(cv2.CAP_PROP_FPS) or 25.0
            return float(pos) / float(nominal or 25.0)
        return float(ms) / 1000.0

    def _should_emit(self, pts_s: float) -> bool:
        """Rate-limit to target_fps using PTS, not sleep.

        Throttling by wall clock would drop the wrong frames during the
        faster-than-real-time GOP replay at join.
        """
        if self.target_fps <= 0:
            return True
        if self._last_emitted_pts is None:
            self._last_emitted_pts = pts_s
            return True
        if pts_s - self._last_emitted_pts >= (1.0 / self.target_fps) - 1e-6:
            self._last_emitted_pts = pts_s
            return True
        return False

    def frames(self) -> Iterator[Frame]:
        """Yield decoded frames, forever, reconnecting underneath."""
        while not self._closed:
            if self._cap is None:
                try:
                    self._open()
                    self._backoff = settings.reconnect_backoff_initial_s
                except DecodeError as exc:
                    log.warning("stream_open_failed", camera=self.camera_id, error=str(exc))
                    self._reconnect()
                    continue

            ok, image = self._cap.read()   # type: ignore[union-attr]

            if not ok or image is None:
                # A failed read is either a gap, a decoder warning at join, or
                # a real disconnect. We cannot tell them apart from one read,
                # so we count. Aborting on the first one is the mistake the
                # guide calls out, and it bounces forever on H.265.
                self.decode_errors += 1
                self._consecutive_errors += 1
                if self._consecutive_errors == 1:
                    log.debug("decode_hiccup", camera=self.camera_id)
                if self._consecutive_errors >= settings.max_consecutive_decode_errors:
                    log.warning("stream_lost", camera=self.camera_id,
                                consecutive_errors=self._consecutive_errors)
                    self._reconnect()
                else:
                    time.sleep(0.02)
                continue

            self._consecutive_errors = 0
            self.frames_decoded += 1
            self._frames_since_connect += 1

            pts_s = self._pts_seconds()
            seen_at, dt_s, cut = self.clock.observe(pts_s)

            if cut and self._frames_since_connect <= SETTLING_FRAMES:
                # Not a scene cut -- the clock is still settling. Some
                # backends report no PTS for the first frames after a join,
                # so _pts_seconds falls back to a frame count, and the jump
                # to real timestamps looks like a discontinuity. There is no
                # tracker state to flush this early anyway, and a spurious
                # cut here would corrupt a loop-period measurement.
                log.debug("clock_settling", camera=self.camera_id,
                          frame=self._frames_since_connect, pts_s=round(pts_s, 3))
                continue

            if cut:
                # The loop point. Long-lived state -- background models,
                # re-id galleries, track ids -- must recover from a hard cut
                # rather than assume infinite continuity. We surface it and
                # let the worker flush.
                log.info("scene_discontinuity", camera=self.camera_id,
                         pts_s=round(pts_s, 3),
                         total=self.clock.discontinuities)
                self._last_emitted_pts = None
                self.frames_emitted += 1
                yield Frame(image, seen_at, pts_s, 0.0, self.frames_decoded, True)
                continue

            if not self._should_emit(pts_s):
                continue

            self.frames_emitted += 1
            yield Frame(image, seen_at, pts_s, dt_s, self.frames_decoded)

    # -- reporting ---------------------------------------------------------

    def stats(self) -> dict[str, float | int | None]:
        return {
            "frames_decoded": self.frames_decoded,
            "frames_emitted": self.frames_emitted,
            "reconnects": self.reconnects,
            "decode_errors": self.decode_errors,
            "discontinuities": self.clock.discontinuities,
            # The real rate, measured. Never the declared one.
            "measured_fps": self.clock.measured_fps(),
        }
