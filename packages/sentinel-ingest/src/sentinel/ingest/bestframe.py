"""Choosing which frame of a track to keep.

Section 5.1 step 4: largest, sharpest, furthest from the frame edge, and not
the first or last frame. Every downstream pipeline sees only this crop, so
this function quietly sets the ceiling on description, embedding and plate
quality. It is worth more than it looks.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from sentinel.core.config import settings
from sentinel.ingest.track import Track

#: Classes whose detector box excludes the person riding it.
TWO_WHEELER_CLASSES = {"motorcycle", "bicycle"}


@dataclass
class BestFrame:
    pts_s: float
    bbox: tuple[int, int, int, int]          # the vehicle, in frame pixels
    crop_bbox: tuple[int, int, int, int]     # where `crop` was cut, same space
    score: float
    crop: np.ndarray


def pad_bbox(
    bbox: tuple[int, int, int, int], cls: str, frame_w: int, frame_h: int
) -> tuple[int, int, int, int]:
    """Grow the detector box into the crop the pipelines need, clipped to frame.

    COCO annotates a motorcycle without its rider, so a tight box puts the
    head outside the only image `violate` ever opens.
    """
    x1, y1, x2, y2 = bbox
    w, h = max(x2 - x1, 1), max(y2 - y1, 1)

    side = settings.crop_pad_frac
    top = side
    if cls in TWO_WHEELER_CLASSES:
        top += settings.crop_pad_top_two_wheeler_frac

    return (
        max(0, int(round(x1 - w * side))),
        max(0, int(round(y1 - h * top))),
        min(frame_w, int(round(x2 + w * side))),
        min(frame_h, int(round(y2 + h * side))),
    )


def sharpness(image: np.ndarray) -> float:
    """Variance of the Laplacian. Higher is sharper.

    Cheap and adequate: we are ranking crops of the same vehicle from the
    same camera against each other, not measuring absolute quality.
    """
    if image.size == 0:
        return 0.0
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def edge_margin(bbox: tuple[int, int, int, int], frame_w: int, frame_h: int) -> float:
    """0 when the box touches an edge, 1 when it is dead centre.

    A vehicle half out of frame is half a vehicle, and a model asked to name
    its make will invent one.
    """
    x1, y1, x2, y2 = bbox
    margins = [x1, y1, frame_w - x2, frame_h - y2]
    return max(0.0, min(margins) / max(min(frame_w, frame_h) / 2.0, 1.0))


def choose(
    track: Track,
    frames: dict[float, np.ndarray],
    frame_w: int,
    frame_h: int,
) -> BestFrame | None:
    """Pick the best frame of a finished track.

    Args:
        track: the finished track, carrying its per-frame history.
        frames: cached images keyed by the pts_s they were decoded at. Only
            the frames the caller kept are available; the rest are gone, and
            that is fine.
    """
    candidates = [
        (pts, bbox, det_score)
        for pts, bbox, det_score in track.history
        if pts in frames
    ]
    if not candidates:
        return None

    # Drop the first and last observation: a vehicle entering or leaving is
    # clipped by the frame edge almost by definition.
    if len(candidates) > 2:
        candidates = candidates[1:-1]

    areas = [max(1, (b[2] - b[0]) * (b[3] - b[1])) for _, b, _ in candidates]
    max_area = max(areas)

    best: BestFrame | None = None
    best_score = -1.0

    for (pts, bbox, det_score), area in zip(candidates, areas, strict=True):
        x1, y1, x2, y2 = bbox
        image = frames[pts]
        h, w = image.shape[:2]

        # Score the tight box, store the padded one. Padding is mostly road,
        # and the padded box is clipped by construction, so scoring it would
        # blunt sharpness and report every vehicle as edge-clipped.
        tight = image[max(0, y1) : min(h, y2), max(0, x1) : min(w, x2)]
        if tight.size == 0:
            continue

        crop_bbox = pad_bbox(bbox, track.cls, w, h)
        cx1, cy1, cx2, cy2 = crop_bbox
        crop = image[cy1:cy2, cx1:cx2]
        if crop.size == 0:
            continue

        size_term = area / max_area
        sharp_term = min(sharpness(tight) / 300.0, 1.0)
        edge_term = edge_margin(bbox, frame_w, frame_h)

        # Size dominates, because a 40-pixel vehicle is unusable however
        # sharp it is. Detector score is a weak tiebreak only.
        score = 0.45 * size_term + 0.25 * sharp_term + 0.20 * edge_term + 0.10 * det_score

        if score > best_score:
            best_score = score
            best = BestFrame(
                pts_s=pts, bbox=bbox, crop_bbox=crop_bbox,
                score=score, crop=crop.copy(),
            )

    return best
