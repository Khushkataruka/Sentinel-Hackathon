"""Measure a real feed against the integration guide's checklist.

Run this on a machine that can actually reach the grid, before trusting
anything else. It answers the questions the guide says will bite you, with
numbers rather than assumptions:

  * what codec and frame size is this camera really sending
  * what is the DELIVERED frame rate, as opposed to the declared one
  * are inter-frame intervals uniform (they are not)
  * does presentation time reset at the loop point, or run straight through
  * how long is the loop, so ingest can flush tracker state on schedule
  * does the stream recover from an interruption

The loop period is the output that matters most. The loop point is not
detectable from the video -- same camera, same background, similar traffic
density -- so if PTS runs continuously across it, the only way to cut
tracker state at the right moment is to know how long the recording is.
Whatever this prints goes into camera_profiles.loop_period_s.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sentinel.core import streamurl
from sentinel.core.logging import get_logger
from sentinel.ingest.adapters.base import StreamHandle
from sentinel.ingest.decode import CameraStream

log = get_logger(__name__)


@dataclass
class Preflight:
    camera_id: str
    url: str
    frames_decoded: int = 0
    frames_emitted: int = 0
    width: int | None = None
    height: int | None = None
    measured_fps: float | None = None
    pts_span_s: float = 0.0
    dt_min: float | None = None
    dt_max: float | None = None
    dt_median: float | None = None
    pts_resets: int = 0
    pts_reset_at: list[float] = field(default_factory=list)
    loop_period_s: float | None = None
    decode_errors: int = 0
    reconnects: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def run(handle: StreamHandle, seconds: float = 180.0,
        target_fps: float | None = None) -> Preflight:
    """Watch one camera and report what it actually does.

    Three minutes is the useful default: long enough to see a loop point on
    a recording of a few minutes, short enough to run over fifty cameras in
    an afternoon.
    """
    # Redacted at construction: a preflight report is pasted into
    # tickets and support mail, and the RTSP URL carries the password.
    result = Preflight(camera_id=handle.camera_id, url=streamurl.redact(handle.url))
    stream = CameraStream(handle, target_fps=target_fps or 0.0)  # 0 = no throttle

    pts_values: list[float] = []
    deltas: list[float] = []
    started = time.monotonic()

    try:
        for frame in stream.frames():
            if result.width is None:
                result.width, result.height = frame.shape

            if frame.discontinuity:
                result.pts_resets += 1
                result.pts_reset_at.append(round(frame.pts_s, 3))
            else:
                pts_values.append(frame.pts_s)
                if frame.dt_s > 0:
                    deltas.append(frame.dt_s)

            if time.monotonic() - started > seconds:
                break
    finally:
        stream.close()

    stats = stream.stats()
    result.frames_decoded = int(stats["frames_decoded"] or 0)
    result.frames_emitted = int(stats["frames_emitted"] or 0)
    result.decode_errors = int(stats["decode_errors"] or 0)
    result.reconnects = int(stats["reconnects"] or 0)
    result.measured_fps = stats["measured_fps"]

    if pts_values:
        result.pts_span_s = round(pts_values[-1] - pts_values[0], 3)
    if deltas:
        arr = np.array(deltas)
        result.dt_min = round(float(arr.min()), 4)
        result.dt_max = round(float(arr.max()), 4)
        result.dt_median = round(float(np.median(arr)), 4)

    # --- the loop period -------------------------------------------------
    if result.pts_resets >= 2:
        # The gateway resets PTS at the loop, so the period is the gap
        # between resets and ingest could detect it from the clock alone.
        gaps = np.diff(result.pts_reset_at)
        result.loop_period_s = round(float(np.median(gaps)), 2)
        result.notes.append(
            f"PTS resets at the loop point ({result.pts_resets} seen); period "
            f"{result.loop_period_s}s. The clock can detect this on its own."
        )
    elif result.pts_resets == 1:
        result.notes.append(
            "one PTS reset seen -- that may be the loop, or a reconnect. "
            "Run longer to separate them."
        )
    else:
        result.notes.append(
            f"PTS ran continuously for {result.pts_span_s}s with no reset. If "
            "the recording loops in that window, the loop point is INVISIBLE "
            "to the clock -- and it is also invisible in the pixels, because "
            "the camera and background do not change. Measure the recording's "
            "length another way and set camera_profiles.loop_period_s, or "
            "tracker state will be carried across the cut."
        )

    if result.measured_fps and result.dt_median:
        implied = 1.0 / result.dt_median
        if abs(implied - result.measured_fps) > 0.5:
            result.notes.append(
                f"delivery is uneven: median interval implies {implied:.1f} fps "
                f"but the overall rate is {result.measured_fps:.1f} fps"
            )
    if result.dt_max and result.dt_min and result.dt_max > result.dt_min * 3:
        result.notes.append(
            f"inter-frame intervals vary {result.dt_min:.3f}s to {result.dt_max:.3f}s "
            "-- motion models must integrate real elapsed PTS, never a fixed cadence"
        )
    if result.decode_errors:
        result.notes.append(
            f"{result.decode_errors} decode hiccups, survived without aborting "
            "(expected on H.265 until the first IDR arrives)"
        )
    if result.reconnects:
        result.notes.append(f"{result.reconnects} reconnect(s) during the run")

    return result


def render(result: Preflight) -> str:
    lines = [
        f"camera            {result.camera_id}",
        f"url               {result.url}",
        f"frame size        {result.width}x{result.height}",
        f"frames decoded    {result.frames_decoded}",
        "measured fps      "
        + (f"{result.measured_fps:.2f}" if result.measured_fps else "n/a")
        + "   <- delivered, not declared",
        f"pts span          {result.pts_span_s}s",
        f"inter-frame dt    min {result.dt_min}  median {result.dt_median}  max {result.dt_max}",
        f"pts resets        {result.pts_resets} {result.pts_reset_at or ''}",
        "loop period       "
        + (f"{result.loop_period_s}s  <- put this in camera_profiles.loop_period_s"
           if result.loop_period_s else "UNKNOWN -- see notes"),
        f"decode errors     {result.decode_errors}",
        f"reconnects        {result.reconnects}",
        "",
        "notes:",
    ]
    lines += [f"  - {n}" for n in result.notes]
    return "\n".join(lines)
