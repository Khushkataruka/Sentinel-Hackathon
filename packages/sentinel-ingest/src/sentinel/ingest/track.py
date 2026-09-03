"""ByteTrack, integrating over presentation timestamps.

Standard ByteTrack with one deliberate change: the Kalman prediction step
takes a real dt in seconds rather than assuming one frame of constant
cadence. The sandbox delivers non-uniform intervals and replays a buffered
group-of-pictures faster than real time on every connect, so a fixed-dt
motion model produces impossible velocities right after each reconnect --
which then look like detector failures.

The association is the ByteTrack contribution: match high-confidence
detections first, then give the leftovers a second chance against the
low-confidence ones. Low-scoring boxes are usually occluded vehicles, not
noise, and throwing them away is what fragments tracks.

State is (x, y, a, h, vx, vy, va, vh): centre, aspect ratio, height, and
their velocities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

import numpy as np
from sentinel.core.config import settings
from sentinel.ingest.detect import Detection


class TrackState(Enum):
    TENTATIVE = auto()   # seen once; not yet a real object
    CONFIRMED = auto()
    LOST = auto()
    REMOVED = auto()


def xyxy_to_xyah(box: tuple[float, float, float, float]) -> np.ndarray:
    x1, y1, x2, y2 = box
    w, h = max(x2 - x1, 1e-3), max(y2 - y1, 1e-3)
    return np.array([x1 + w / 2, y1 + h / 2, w / h, h], dtype=np.float64)


def xyah_to_xyxy(mean: np.ndarray) -> tuple[int, int, int, int]:
    x, y, a, h = mean[:4]
    w = a * h
    return int(x - w / 2), int(y - h / 2), int(x + w / 2), int(y + h / 2)


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU between two sets of xyxy boxes."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    area_a = ((a[:, 2] - a[:, 0]).clip(0) * (a[:, 3] - a[:, 1]).clip(0))[:, None]
    area_b = ((b[:, 2] - b[:, 0]).clip(0) * (b[:, 3] - b[:, 1]).clip(0))[None, :]
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = (x2 - x1).clip(0) * (y2 - y1).clip(0)
    return inter / (area_a + area_b - inter + 1e-9)


def class_mask(track_classes: list[str], det_classes: list[str]) -> np.ndarray:
    """1.0 where the classes agree, 0.0 where they do not.

    Multiplied into the IoU before association. Without it a rider's person
    box can win the greedy assignment against the motorcycle's own track,
    which then keeps cls='motorcycle' while being fed person observations.
    """
    if not track_classes or not det_classes:
        return np.zeros((len(track_classes), len(det_classes)))
    return (
        np.array(track_classes)[:, None] == np.array(det_classes)[None, :]
    ).astype(float)


def greedy_match(
    cost: np.ndarray, threshold: float
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Greedy assignment on a similarity matrix.

    Hungarian would be optimal; greedy is within noise at these track counts
    and keeps scipy off the dependency list for a service that has to run on
    whatever the evaluation machine is.
    """
    matches: list[tuple[int, int]] = []
    if cost.size:
        work = cost.copy()
        while True:
            i, j = np.unravel_index(work.argmax(), work.shape)
            if work[i, j] < threshold:
                break
            matches.append((int(i), int(j)))
            work[i, :] = -1
            work[:, j] = -1
    matched_rows = {i for i, _ in matches}
    matched_cols = {j for _, j in matches}
    return (
        matches,
        [i for i in range(cost.shape[0]) if i not in matched_rows],
        [j for j in range(cost.shape[1]) if j not in matched_cols],
    )


class KalmanBox:
    """Constant-velocity Kalman filter over (x, y, a, h).

    dt is passed in per step, in seconds, from PTS deltas.
    """

    def __init__(self, measurement: np.ndarray) -> None:
        self.mean = np.concatenate([measurement, np.zeros(4)])
        std = np.array([2.0, 2.0, 0.01, 2.0, 10.0, 10.0, 0.05, 10.0])
        self.covariance = np.diag(std**2)
        self._pos_weight = 1.0 / 20
        self._vel_weight = 1.0 / 160

    def predict(self, dt_s: float) -> None:
        dt = float(np.clip(dt_s, 0.0, 1.0))   # a long gap must not fling the box
        F = np.eye(8)
        for i in range(4):
            F[i, i + 4] = dt

        h = max(self.mean[3], 1.0)
        q_pos = np.array([self._pos_weight * h, self._pos_weight * h, 1e-2,
                          self._pos_weight * h])
        q_vel = np.array([self._vel_weight * h, self._vel_weight * h, 1e-5,
                          self._vel_weight * h])
        Q = np.diag(np.concatenate([q_pos, q_vel]) ** 2) * max(dt, 1e-3)

        self.mean = F @ self.mean
        self.covariance = F @ self.covariance @ F.T + Q

    def update(self, measurement: np.ndarray) -> None:
        H = np.zeros((4, 8))
        H[:4, :4] = np.eye(4)
        h = max(self.mean[3], 1.0)
        R = np.diag(
            np.array([self._pos_weight * h, self._pos_weight * h, 1e-1,
                      self._pos_weight * h]) ** 2
        )
        S = H @ self.covariance @ H.T + R
        K = self.covariance @ H.T @ np.linalg.inv(S)
        self.mean = self.mean + K @ (measurement - H @ self.mean)
        self.covariance = (np.eye(8) - K @ H) @ self.covariance


@dataclass
class Track:
    track_id: int
    cls: str
    kalman: KalmanBox
    state: TrackState = TrackState.TENTATIVE
    score: float = 0.0
    hits: int = 1
    age_s: float = 0.0
    time_since_update_s: float = 0.0
    start_pts_s: float = 0.0
    last_pts_s: float = 0.0
    #: Per-frame history the best-frame chooser and the violation pipeline
    #: read: (pts_s, bbox, score). Bounded, because a vehicle stuck at a
    #: signal for four minutes must not grow this without limit.
    history: list[tuple[float, tuple[int, int, int, int], float]] = field(
        default_factory=list
    )

    MAX_HISTORY = 300

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return xyah_to_xyxy(self.kalman.mean)

    @property
    def duration_s(self) -> float:
        return max(self.last_pts_s - self.start_pts_s, 0.0)

    def observe(self, detection: Detection, pts_s: float) -> None:
        self.kalman.update(xyxy_to_xyah(detection.bbox))
        self.score = detection.score
        self.hits += 1
        self.time_since_update_s = 0.0
        self.last_pts_s = pts_s
        self.history.append((pts_s, detection.bbox, detection.score))
        if len(self.history) > self.MAX_HISTORY:
            del self.history[: len(self.history) - self.MAX_HISTORY]
        if self.state is TrackState.TENTATIVE and self.hits >= settings.min_track_frames:
            self.state = TrackState.CONFIRMED
        elif self.state is TrackState.LOST:
            self.state = TrackState.CONFIRMED


@dataclass
class UpdateStats:
    """How one frame's association went.

    dissociation_ratio is the load-bearing field. At a feed loop point every
    vehicle on screen is replaced at once, so every confirmed track loses its
    match in the SAME frame. Ordinary traffic never does that: vehicles leave
    one or two at a time. A ratio near 1.0 across several tracks is therefore
    a scene cut, and it is the only signal that works when the gateway
    rewrites presentation timestamps to run continuously across the loop --
    which mediamtx, the software this grid is built on, does.
    """

    confirmed_before: int = 0
    matched: int = 0
    dissociated: int = 0
    spawned: int = 0

    @property
    def dissociation_ratio(self) -> float:
        return self.dissociated / self.confirmed_before if self.confirmed_before else 0.0


class ByteTrack:
    """Multi-object tracker, one instance per camera.

    reset() exists because the feed loops: at the discontinuity every track
    is finished and the ids start again. A tracker that carried ids across
    the cut would link two unrelated vehicles into one.
    """

    #: A cut is declared when at least this many confirmed tracks all lose
    #: association in one frame, and at least this fraction of them do.
    #: Two thresholds, because one vehicle leaving is not evidence of
    #: anything and the ratio alone is meaningless at low track counts.
    CUT_MIN_TRACKS = 2
    CUT_MIN_RATIO = 0.7

    def __init__(
        self,
        *,
        high_threshold: float = 0.5,
        low_threshold: float = 0.1,
        match_threshold: float = 0.2,
        max_lost_s: float = 2.0,
    ) -> None:
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        self.match_threshold = match_threshold
        self.max_lost_s = max_lost_s

        self.tracks: list[Track] = []
        self._next_id = 1
        self.last_stats = UpdateStats()

    def suspects_scene_cut(self) -> bool:
        """Did the last frame look like every vehicle was replaced at once?"""
        stats = self.last_stats
        return (
            stats.confirmed_before >= self.CUT_MIN_TRACKS
            and stats.dissociation_ratio >= self.CUT_MIN_RATIO
        )

    def reset(self) -> list[Track]:
        """End every live track and clear state. Returns the tracks that were
        cut off, so the caller can still write sightings for them."""
        finished = [t for t in self.tracks if t.state is not TrackState.TENTATIVE]
        self.tracks = []
        return finished

    def _spawn(self, detection: Detection, pts_s: float) -> Track:
        track = Track(
            track_id=self._next_id,
            cls=detection.cls,
            kalman=KalmanBox(xyxy_to_xyah(detection.bbox)),
            score=detection.score,
            start_pts_s=pts_s,
            last_pts_s=pts_s,
            history=[(pts_s, detection.bbox, detection.score)],
        )
        self._next_id += 1
        return track

    def update(
        self, detections: list[Detection], dt_s: float, pts_s: float
    ) -> tuple[list[Track], list[Track]]:
        """Advance one frame.

        Returns (active_tracks, finished_tracks). A finished track is one that
        has been unmatched for longer than max_lost_s -- that is when the
        caller writes its sighting, because only then is the best frame known.
        """
        stats = UpdateStats(
            confirmed_before=sum(1 for t in self.tracks if t.state is TrackState.CONFIRMED)
        )

        for track in self.tracks:
            track.kalman.predict(dt_s)
            track.age_s += dt_s
            track.time_since_update_s += dt_s

        high = [d for d in detections if d.score >= self.high_threshold]
        low = [
            d for d in detections
            if self.low_threshold <= d.score < self.high_threshold
        ]

        candidates = [t for t in self.tracks if t.state is not TrackState.REMOVED]
        track_boxes = np.array([t.bbox for t in candidates], dtype=float).reshape(-1, 4)

        # Stage one: high-confidence detections against everything.
        high_boxes = np.array([d.bbox for d in high], dtype=float).reshape(-1, 4)
        matches, unmatched_tracks, unmatched_high = greedy_match(
            iou_matrix(track_boxes, high_boxes)
            * class_mask([t.cls for t in candidates], [d.cls for d in high]),
            self.match_threshold,
        )
        for ti, di in matches:
            candidates[ti].observe(high[di], pts_s)

        # Stage two: the ByteTrack idea. Leftover tracks get a second pass
        # against the low-confidence boxes, which are usually occluded
        # vehicles rather than noise.
        remaining = [candidates[i] for i in unmatched_tracks]
        if remaining and low:
            low_boxes = np.array([d.bbox for d in low], dtype=float).reshape(-1, 4)
            rem_boxes = np.array([t.bbox for t in remaining], dtype=float).reshape(-1, 4)
            second, still_unmatched, _ = greedy_match(
                iou_matrix(rem_boxes, low_boxes)
                * class_mask([t.cls for t in remaining], [d.cls for d in low]),
                self.match_threshold,
            )
            for ri, di in second:
                remaining[ri].observe(low[di], pts_s)
            lost = [remaining[i] for i in still_unmatched]
        else:
            lost = remaining

        for track in lost:
            if track.state is TrackState.CONFIRMED:
                stats.dissociated += 1
                track.state = TrackState.LOST
        stats.matched = stats.confirmed_before - stats.dissociated

        # Unmatched high-confidence detections start new tracks.
        for di in unmatched_high:
            stats.spawned += 1
            self.tracks.append(self._spawn(high[di], pts_s))

        self.last_stats = stats

        # Retire what has been gone too long.
        finished: list[Track] = []
        live: list[Track] = []
        for track in self.tracks:
            if track.time_since_update_s > self.max_lost_s:
                track.state = TrackState.REMOVED
                if track.hits >= settings.min_track_frames:
                    finished.append(track)
                # A one-frame blip is dropped silently: one detection is not
                # a vehicle passing a camera, and writing it as a sighting
                # would put noise into the route graph.
            else:
                live.append(track)
        self.tracks = live

        active = [t for t in live if t.state is TrackState.CONFIRMED]
        return active, finished
