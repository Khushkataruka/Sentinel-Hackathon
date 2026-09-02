"""Turning presentation timestamps into wall-clock time.

The sandbox integration guide is blunt about this, and it is worth repeating
because getting it wrong produces bugs that look like model failures:

  * The declared frame rate does not match the delivery rate. Anything
    time-derived that is computed from a frame count is wrong.
  * On connect, the gateway replays its buffered group-of-pictures so the
    decoder can start at a keyframe. The first second or two therefore
    arrives FASTER than real time. A tracker that stamps frames by arrival
    computes impossible velocities immediately after every connection.
  * Frame intervals are not uniform. A gap is not a disconnect.
  * Each feed is a continuous recording that LOOPS. At the loop point the
    scene cuts and the PTS jumps.

So: PTS is the only clock. This class anchors the stream's PTS to one
wall-clock reading taken at the first frame, and every subsequent timestamp
is anchor + (pts - pts0). Between anchors the mapping is exact and monotonic,
which is what the Kalman filter and the dwell-time arithmetic need.

Re-anchoring happens on reconnect and at a detected discontinuity. It means
timestamps across a cut are not comparable to the frame before it, which is
correct: they are different moments in the recording.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sentinel.core.config import settings


@dataclass
class PtsClock:
    """Maps stream presentation timestamps to wall-clock instants.

    Args:
        discontinuity_s: a PTS step outside [0, this] is treated as a cut.
            Backwards steps always are.
    """

    discontinuity_s: float = field(default_factory=lambda: settings.pts_discontinuity_s)

    _anchor_wall: datetime | None = field(default=None, init=False)
    _anchor_pts_s: float | None = field(default=None, init=False)
    _last_pts_s: float | None = field(default=None, init=False)
    discontinuities: int = field(default=0, init=False)
    frames: int = field(default=0, init=False)

    @property
    def anchored(self) -> bool:
        return self._anchor_wall is not None

    @property
    def last_pts_s(self) -> float | None:
        return self._last_pts_s

    def anchor(self, pts_s: float, wall: datetime | None = None) -> None:
        """Pin this PTS to a wall-clock instant. Called on the first frame of
        a connection and again after any cut."""
        self._anchor_wall = wall or datetime.now(UTC)
        self._anchor_pts_s = pts_s
        self._last_pts_s = pts_s

    def reset(self) -> None:
        """Drop the anchor. The next frame re-anchors."""
        self._anchor_wall = None
        self._anchor_pts_s = None
        self._last_pts_s = None

    def observe(self, pts_s: float) -> tuple[datetime, float, bool]:
        """Take one frame's PTS.

        Returns (wall_clock_time, seconds_since_previous_frame, was_discontinuity).

        The delta is what motion models must integrate over -- never a fixed
        cadence, and never the interval between arrivals.
        """
        self.frames += 1

        if not self.anchored:
            self.anchor(pts_s)
            return self._anchor_wall, 0.0, False   # type: ignore[return-value]

        previous = self._last_pts_s
        assert previous is not None
        delta = pts_s - previous

        if delta < 0 or delta > self.discontinuity_s:
            # The recording looped, or the camera rebooted. Anchor afresh and
            # tell the caller, which flushes tracks and resets the bucket.
            self.discontinuities += 1
            self.anchor(pts_s)
            return self._anchor_wall, 0.0, True   # type: ignore[return-value]

        self._last_pts_s = pts_s
        wall = self._anchor_wall + timedelta(  # type: ignore[operator]
            seconds=pts_s - self._anchor_pts_s  # type: ignore[operator]
        )
        return wall, delta, False

    def measured_fps(self) -> float | None:
        """The real delivery rate, from PTS span and frame count.

        This is the number to report as decode_fps in a health check. The
        declared rate is not trustworthy and is never read.
        """
        if self._anchor_pts_s is None or self._last_pts_s is None or self.frames < 2:
            return None
        span = self._last_pts_s - self._anchor_pts_s
        return (self.frames - 1) / span if span > 0 else None
