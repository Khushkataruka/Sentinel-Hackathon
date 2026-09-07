"""Drawing what the platform saw back onto the video.

A second decode pass, joined to the first through the sidecar the offline run
wrote. Two passes rather than one because the labels do not exist yet during
the first: colour, plate, violations and cross-camera matches are all produced
downstream of ingest, by pipelines and correlation, minutes later.

The join key is `pts_s`. Both passes rate-limit identically off the same
presentation timestamps, so the same frames are selected in the same order --
and unlike a frame index, PTS is a property of the file rather than of how it
was read. A frame the sidecar does not mention is written through unannotated
rather than dropped, so the output never silently loses time.

The output runs at the analysis rate (`target_decode_fps`, 10 by default), not
the source rate. That is deliberate: every box on it was measured on the frame
it is drawn on. Rendering at 25 fps would mean holding each box across two or
three frames it was never computed for, which looks smoother and is a small
lie about what the detector did.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from sentinel.core.config import settings
from sentinel.core.db import acquire
from sentinel.core.logging import get_logger
from sentinel.ingest.adapters.base import StreamHandle
from sentinel.ingest.decode import CameraStream

log = get_logger(__name__)

#: BGR, because OpenCV. Chosen to stay apart on a grey road and to survive
#: the yuv420p round trip, which mangles saturated reds and blues.
CLASS_COLOURS: dict[str, tuple[int, int, int]] = {
    "car":        (255, 196,  64),
    "motorcycle": ( 92, 220, 255),
    "bus":        (140, 255, 160),
    "truck":      (200, 160, 255),
    "bicycle":    (180, 255, 255),
    "auto":       ( 96, 208, 255),
    "person":     (190, 190, 190),
}
DEFAULT_COLOUR = (220, 220, 220)
MATCH_COLOUR = (80, 120, 255)      # the one warm colour; a match is the point
VIOLATION_COLOUR = (70, 70, 255)

FONT = cv2.FONT_HERSHEY_SIMPLEX

#: A caption runs to a sentence. Past this it covers the vehicle it describes.
CAPTION_CHARS = 46

PENDING_COLOUR = (150, 150, 150)

#: PTS values are floats from a decoder, so an exact dict lookup between two
#: passes is a coin flip. Quantise to a millisecond, which is finer than any
#: frame interval and coarse enough to be stable.
PTS_QUANTUM = 1000.0


def _key(pts_s: float) -> int:
    return int(round(pts_s * PTS_QUANTUM))


@dataclass
class Label:
    """Everything known about one track, flattened for drawing."""

    track_id: str
    read_id: str | None = None
    cls: str | None = None
    lines: list[str] = field(default_factory=list)
    match: str | None = None
    violations: list[str] = field(default_factory=list)
    #: Pipelines that never reported. Drawn, because a vehicle with no colour
    #: because the describe pipeline is still queued looks identical to one
    #: the model had nothing to say about.
    pending: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


#: Words a caption may repeat without earning its line back.
_FILLER = {"a", "an", "the", "with", "and", "of", "in", "on", "vehicle", "stub"}


def _adds_something(caption: str, attributes: str) -> bool:
    """Does the caption say anything the attribute line did not?

    The stub captioner writes the attributes back out as a sentence, so
    without this check every vehicle carries the same words twice and the
    label is half wasted. A real captioner names the roof carrier, the dented
    panel, the missing bumper -- and passes.
    """
    def words(text: str) -> set[str]:
        return {
            "".join(ch for ch in token if ch.isalnum())
            for token in text.lower().split()
        } - _FILLER - {""}

    return bool(words(caption) - words(attributes))


def describe_lines(row: Mapping[str, Any]) -> list[str]:
    """The description pipeline's output, as it should appear on the frame.

    Four separate things live in those columns and they read differently:

      * the structured attributes -- colour, type, make, model -- which are
        what search actually filters on, so they go first and in full;
      * the free-text caption, which is the only place a roof carrier or a
        broken tail light ever appears -- truncated, because a label stack
        that covers the vehicle it describes is worse than no label, and
        dropped entirely when it only restates the attributes, which is
        exactly what the stub captioner does;
      * the notable features list, which the caption usually repeats, so it
        is shown only where it says something the caption did not;
      * the plate, kept last and with its confidence, because a plate with no
        confidence next to it invites more trust than it has earned.

    A stub caption arrives prefixed `[stub]` and is drawn that way on purpose.
    """
    lines: list[str] = []

    attributes = " ".join(
        part for part in (row.get("colour"), row.get("vtype") or row.get("class")) if part
    )
    badge = " ".join(str(part) for part in (row.get("make"), row.get("model")) if part)
    if attributes and badge:
        lines.append(f"{attributes} · {badge}")
    elif attributes or badge:
        lines.append(attributes or badge)
    elif row.get("class"):
        lines.append(str(row["class"]))

    caption = (row.get("caption") or "").strip()
    if caption and _adds_something(caption, lines[0] if lines else ""):
        lines.append(caption if len(caption) <= CAPTION_CHARS
                     else caption[: CAPTION_CHARS - 1].rstrip() + "…")

    features = [str(f) for f in (row.get("features") or []) if f]
    unclaimed = [f for f in features if f.lower() not in caption.lower()]
    if unclaimed:
        lines.append("+ " + ", ".join(unclaimed[:3]))

    if row.get("plate_text"):
        conf = row.get("plate_conf")
        lines.append(
            str(row["plate_text"]) + (f"  {conf:.2f}" if conf is not None else "")
        )

    return lines


async def load_labels(camera_id: str) -> dict[str, Label]:
    """One query per camera for every sighting it produced, plus violations.

    Keyed by `sightings.track_id`, which is what the sidecar recorded.
    """
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.read_id, s.track_id, s.class, s.colour, s.vtype, s.make,
                   s.model, s.features, s.caption, s.plate_text, s.plate_conf,
                   s.describe_status, s.plate_status, s.violation_status,
                   coalesce(
                     array_agg(v.violation_type ORDER BY v.violation_type)
                       FILTER (WHERE v.violation_type IS NOT NULL),
                     '{}'
                   ) AS violations
              FROM sightings s
              LEFT JOIN violations v ON v.read_id = s.read_id
             WHERE s.camera_id = $1
             GROUP BY s.read_id
            """,
            camera_id,
        )

    labels: dict[str, Label] = {}
    for row in rows:
        record = dict(row)
        pending = [
            name for name, status in (
                ("description", record["describe_status"]),
                ("plate", record["plate_status"]),
                ("violations", record["violation_status"]),
            ) if status == "pending"
        ]

        labels[row["track_id"]] = Label(
            track_id=row["track_id"],
            read_id=str(row["read_id"]),
            cls=row["class"],
            lines=describe_lines(record),
            violations=list(row["violations"]),
            pending=pending,
        )
    return labels


def apply_matches(labels: dict[str, Label], correlations: dict[str, Any] | None) -> None:
    """Tag the tracks that correlation linked to another camera."""
    if not correlations:
        return
    by_read: dict[str, str] = {}
    for match in correlations.get("matches", []):
        for sighting in match.get("sightings", []):
            by_read[str(sighting["read_id"])] = match["match_id"]
    for label in labels.values():
        if label.read_id and label.read_id in by_read:
            label.match = by_read[label.read_id]


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def font_scale(width: int) -> float:
    """Text sized to the frame, not to a constant.

    The estate runs 320x240 through 2560x1440. A fixed scale is either
    unreadable on the big ones or covers the road on the small ones -- and
    the small ones are exactly where the label has least room to begin with.
    """
    return max(0.34, min(0.80, width / 2400.0))


def _block_size(
    lines: list[tuple[str, tuple[int, int, int]]], scale: float, thickness: int
) -> tuple[int, int]:
    """(width, height) of the label stack, before anything is drawn."""
    widest = height = 0
    for text, _ in lines:
        (tw, th), base = cv2.getTextSize(text, FONT, scale, thickness)
        widest = max(widest, tw + 6)
        height += th + base + 5
    return widest, height


def _overlaps(a: tuple[int, int, int, int], boxes: list[tuple[int, int, int, int]]) -> bool:
    ax1, ay1, ax2, ay2 = a
    return any(
        ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2
        for bx1, by1, bx2, by2 in boxes
    )


def _text_block(
    image: np.ndarray, origin: tuple[int, int], lines: list[tuple[str, tuple[int, int, int]]],
    *, scale: float = 0.42, thickness: int = 1,
) -> None:
    """A readable label over arbitrary footage, growing upward from `origin`.

    Every line gets a filled plate behind it. Outlined text alone disappears
    over a busy road, and a road is the only thing this ever draws over.

    Both edges are handled here rather than by the caller, because a label
    that runs off the frame is not an error anywhere -- OpenCV clips it in
    silence and the caption simply is not there.
    """
    x, y = origin
    pad = 3
    height, width = image.shape[:2]
    y = min(y, height - 1)

    for text, colour in lines:
        (tw, th), base = cv2.getTextSize(text, FONT, scale, thickness)
        # A caption is long and a vehicle can be at the right-hand edge.
        # Slide the plate left rather than let the text run off the frame.
        left = max(0, min(x, width - tw - 2 * pad - 1))
        top = y - th - base - pad
        if top < 0:
            break                   # out of sky; the caller already tried below
        cv2.rectangle(image, (left, top), (left + tw + 2 * pad, y), (24, 24, 24), -1)
        cv2.putText(image, text, (left + pad, y - base - 1), FONT, scale, colour,
                    thickness, cv2.LINE_AA)
        y = top - 2


def label_lines(
    box: dict[str, Any], label: Label | None, colour: tuple[int, int, int]
) -> list[tuple[str, tuple[int, int, int]]]:
    """The label stack for one vehicle, most important line LAST.

    Last, because `_text_block` grows upward from the box: the final entry
    ends up nearest the vehicle, and the list can be trimmed from the front
    when there is no room. So the order here is also the priority order,
    reversed -- a violation is the last thing to be dropped, a caption the
    first.
    """
    if label is None:
        # No sighting row: the track is still open, or the pipelines have not
        # reported. Say the class and nothing else rather than draw a blank.
        return [(f"{box.get('c', '?')} {box.get('s', '')}".strip(), colour)]

    lines: list[tuple[str, tuple[int, int, int]]] = []
    if label.pending:
        lines.append((f"…{', '.join(label.pending)}", PENDING_COLOUR))
    # label.lines is [attributes, caption, features, plate]; reversed here so
    # the attributes end up closest to the vehicle and the caption is the
    # first thing sacrificed.
    lines.extend((text, colour) for text in reversed(label.lines))
    if label.match:
        lines.append((label.match, MATCH_COLOUR))
    for violation in label.violations:
        lines.append((violation.replace("_", " ").upper(), VIOLATION_COLOUR))
    return lines


def draw_frame(
    image: np.ndarray, boxes: list[dict[str, Any]], labels: dict[str, Label],
) -> int:
    """Draw one frame's boxes in place. Returns how many were drawn.

    Dense traffic is the normal case, and a naive label per box turns into a
    wall of overlapping text that hides both the road and itself. So labels
    are placed largest-vehicle-first -- the nearest vehicle is the one an
    operator is looking at -- and a stack that would land on an existing one
    is trimmed line by line until it fits, or dropped.
    """
    height, width = image.shape[:2]
    scale = font_scale(width)
    drawn = 0
    occupied: list[tuple[int, int, int, int]] = []

    def area(box: dict[str, Any]) -> int:
        x1, y1, x2, y2 = box["b"]
        return max(0, x2 - x1) * max(0, y2 - y1)

    for box in sorted(boxes, key=area, reverse=True):
        x1, y1, x2, y2 = (int(v) for v in box["b"])
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(width - 1, x2), min(height - 1, y2)
        if x2 <= x1 or y2 <= y1:
            continue

        label = labels.get(box["t"])
        colour = CLASS_COLOURS.get(box.get("c") or "", DEFAULT_COLOUR)
        if label and label.violations:
            colour = VIOLATION_COLOUR
        elif label and label.match:
            colour = MATCH_COLOUR

        cv2.rectangle(image, (x1, y1), (x2, y2), colour, 2)
        drawn += 1

        lines = label_lines(box, label, colour)
        while lines:
            block_w, block_h = _block_size(lines, scale, 1)
            left = max(0, min(x1, width - block_w - 1))
            # Above the box by default. Traffic cameras look down, so the
            # vehicles worth labelling are often near the top edge with no
            # sky to put text in; those get the label underneath instead.
            if y1 - block_h >= 0:
                rect = (left, y1 - block_h, left + block_w, y1)
                anchor = (x1, y1 - 2)
            else:
                bottom = min(y2 + 2 + block_h, height - 1)
                rect = (left, bottom - block_h, left + block_w, bottom)
                anchor = (x1, bottom)

            if not _overlaps(rect, occupied):
                _text_block(image, anchor, lines, scale=scale)
                occupied.append(rect)
                break
            # Drop the least important line and try again. Empty means every
            # line collided: leave the box unlabelled rather than draw a
            # legend nobody can read.
            lines = lines[1:]

    return drawn


def draw_hud(
    image: np.ndarray, *, camera_id: str, when: str, index: int, total: int,
    stub_note: str | None,
) -> None:
    height = image.shape[0]
    lines: list[tuple[str, tuple[int, int, int]]] = []
    if stub_note:
        lines.append((stub_note, (60, 60, 255)))
    lines.append((f"{camera_id}  {when}  frame {index}/{total}", (235, 235, 235)))
    _text_block(image, (8, height - 8), lines, scale=font_scale(image.shape[1]) + 0.04)


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def read_sidecar(path: Path) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """Load the track sidecar into a PTS-keyed index."""
    header: dict[str, Any] = {}
    frames: dict[int, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if "header" in row:
                # More than one: the spec is written up front, the rate the
                # pass actually ran at only once it has finished.
                header.update(row["header"])
                continue
            frames[_key(row["pts"])] = row
    return header, frames


async def stub_note() -> str | None:
    """A banner whenever what produced these labels was not a real model.

    An annotated video is the most quotable artefact this repo produces, and a
    stub caption reads exactly like a real one at a glance. `model_versions`
    already records which weights ran; this only surfaces it.
    """
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, version FROM model_versions ORDER BY registered_at DESC"
        )
    latest: dict[str, str] = {}
    for row in rows:
        latest.setdefault(row["role"], row["version"])
    stubbed = sorted(r for r, v in latest.items() if str(v).endswith("-stub"))
    if not stubbed:
        return None
    return "STUB MODELS: " + ", ".join(stubbed)


def _remux(src: Path, dst: Path) -> bool:
    """mp4v out of OpenCV plays in almost nothing. H.264 yuv420p plays in
    almost everything, including the browser this ends up in."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("ffmpeg_missing", action="leaving mp4v output as-is",
                    hint="install ffmpeg for a browser-playable file")
        return False
    result = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.warning("remux_failed", error=result.stderr.strip()[:300])
        return False
    return True


async def render(
    video: Path, sidecar: Path, out_path: Path,
    *, correlations: dict[str, Any] | None = None, fps: float | None = None,
) -> dict[str, Any]:
    """Second pass: re-decode `video`, draw the sidecar, write `out_path`."""
    header, frames = read_sidecar(sidecar)
    camera_id = str(header.get("camera_id") or video.stem)
    start_at = header.get("start_at")

    labels = await load_labels(camera_id)
    apply_matches(labels, correlations)
    note = await stub_note()

    # The rate the first pass ran at, not the current setting. A camera
    # profile carries its own decode_fps, so the two can differ -- and if they
    # do, the two passes select different frames and the sidecar join falls
    # apart. An explicit --fps still wins, for the case where you know better.
    fps = fps or header.get("target_fps") or settings.target_decode_fps
    stream = CameraStream(
        StreamHandle(camera_id=camera_id, url=str(video), transport="file"),
        target_fps=fps, once=True,
        fixed_epoch=None if start_at is None else _parse_dt(start_at),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_path.with_suffix(".raw.mp4")
    writer: cv2.VideoWriter | None = None
    written = boxes_drawn = matched_frames = 0
    total = len(frames)

    try:
        for frame in stream.frames():
            if writer is None:
                height, width = frame.image.shape[:2]
                writer = cv2.VideoWriter(
                    str(raw_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
                )
                if not writer.isOpened():
                    raise RuntimeError(f"could not open {raw_path} for writing")

            record = frames.get(_key(frame.pts_s))
            image = frame.image.copy()
            if record is not None:
                matched_frames += 1
                boxes_drawn += draw_frame(image, record["boxes"], labels)
            draw_hud(
                image, camera_id=camera_id,
                when=frame.seen_at.strftime("%Y-%m-%d %H:%M:%S"),
                index=written + 1, total=total, stub_note=note,
            )
            writer.write(image)
            written += 1
    finally:
        stream.close()
        if writer is not None:
            writer.release()

    if written == 0:
        raise RuntimeError(f"decoded no frames from {video}")

    if _remux(raw_path, out_path):
        raw_path.unlink(missing_ok=True)
    else:
        raw_path.replace(out_path)

    summary = {
        "camera_id": camera_id,
        "video": str(video),
        "output": str(out_path),
        "frames_written": written,
        "frames_with_tracks": matched_frames,
        "sidecar_frames": total,
        "boxes_drawn": boxes_drawn,
        "labelled_tracks": len(labels),
        "matched_tracks": sum(1 for v in labels.values() if v.match),
        "stub_note": note,
    }
    if total and matched_frames < total * 0.9:
        # The two passes disagreed about which frames to emit. Not fatal --
        # the video is still correct, just sparser than it should be -- but it
        # means something changed between the passes, most likely decode fps.
        log.warning("sidecar_join_sparse", camera=camera_id,
                    matched=matched_frames, expected=total,
                    hint="was --fps the same for both passes?")
    log.info("annotated", **summary)
    return summary


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def main(
    videos: list[str], sidecar_dir: str, out_dir: str,
    correlations_path: str | None, fps: float | None,
) -> int:
    """Entry point for `sentinel-ingest annotate`.

    The summary goes to a file rather than only to stdout, because
    `configure_logging` writes log lines to stdout too -- anything capturing
    this command's output as JSON would be parsing them as well.
    """
    from sentinel.core.db import close_pool

    correlations = (
        json.loads(Path(correlations_path).read_text(encoding="utf-8"))
        if correlations_path and Path(correlations_path).exists() else None
    )
    if correlations_path and correlations is None:
        log.warning("correlations_missing", path=correlations_path,
                    action="rendering without match tags")

    async def _go() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            for name in videos:
                video = Path(name).expanduser().resolve()
                sidecar = Path(sidecar_dir) / f"{video.stem}.jsonl"
                if not sidecar.exists():
                    raise SystemExit(
                        f"no track sidecar at {sidecar}; run `sentinel-ingest offline` first"
                    )
                out.append(await render(
                    video, sidecar,
                    Path(out_dir) / f"{video.stem}.annotated.mp4",
                    correlations=correlations, fps=fps,
                ))
        finally:
            await close_pool()
        return out

    summary = asyncio.run(_go())
    summary_path = Path(out_dir) / "annotate.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


__all__ = [
    "Label", "describe_lines", "font_scale", "label_lines", "load_labels",
    "apply_matches", "draw_frame", "draw_hud", "read_sidecar", "render", "main",
]
