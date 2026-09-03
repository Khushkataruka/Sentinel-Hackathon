"""One camera, one worker.

Pulls frames, detects, tracks, and writes sightings when tracks end. Holds
the per-camera state that nothing else can hold: the tracker, the frame
cache the best-frame chooser reads from, and the open traffic bucket.

Runs in a thread executor for the CV work, because OpenCV and ONNX Runtime
both release the GIL during their heavy calls and blocking the event loop
with them would starve every other camera.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sentinel.core.config import settings
from sentinel.core.db import transaction
from sentinel.core.logging import get_logger
from sentinel.ingest import bestframe, writer
from sentinel.ingest.adapters.base import Adapter, AdapterError
from sentinel.ingest.decode import CameraStream, Frame
from sentinel.ingest.detect import VEHICLE_CLASSES, Detector, load_detector
from sentinel.ingest.track import ByteTrack, Track
from sentinel.ingest.traffic import TrafficAccumulator

log = get_logger(__name__)

#: How many recent frames to keep for best-frame selection. At 10 fps this is
#: about twelve seconds, comfortably longer than a vehicle crossing a frame,
#: and bounded so a wedged track cannot eat the heap.
FRAME_CACHE_SIZE = 120

HEALTH_INTERVAL_S = 60.0


def _parse_polygon_wkt(wkt: str | None) -> np.ndarray | None:
    """POLYGON((x y, ...)) in image space -> an (n, 2) array."""
    if not wkt or "(" not in wkt:
        return None
    try:
        inner = wkt[wkt.index("((") + 2 : wkt.rindex("))")]
        points = [
            tuple(float(v) for v in pair.strip().split())
            for pair in inner.split(",")
            if pair.strip()
        ]
        return np.array(points, dtype=np.float32) if len(points) >= 3 else None
    except Exception:
        log.warning("lane_polygon_unparseable", wkt=wkt[:80])
        return None


class CameraWorker:
    """The ingest loop for one camera."""

    def __init__(
        self,
        camera_id: str,
        adapter: Adapter,
        detector: Detector | None = None,
        detect_model_id: int | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.adapter = adapter
        self.detector = detector or load_detector(detect_model_id)
        self.detect_model_id = detect_model_id

        self.gating: writer.Gating | None = None
        self.tracker: ByteTrack | None = None
        self.traffic: TrafficAccumulator | None = None
        self.stream: CameraStream | None = None

        self._frames: OrderedDict[float, np.ndarray] = OrderedDict()
        self._last_archive_pts: float | None = None
        #: Which loop iteration we are in, by PTS. The loop point cannot be
        #: seen in the video, so when the survey measured the period we cut
        #: on schedule instead of hoping to notice.
        self._loop_index: int | None = None
        self._detections_since_health = 0
        self._last_health = 0.0
        self._stopping = False

        # sightings has UNIQUE (camera_id, track_id), and the tracker numbers
        # its tracks from 1 and starts again at every reset -- at each loop
        # cut, and on every worker restart. Writing the bare tracker id would
        # therefore collide with earlier ones and the insert would be silently
        # swallowed by ON CONFLICT DO NOTHING. Measured on a looping feed:
        # roughly a third of all sightings after the first cut disappeared.
        #
        # So the stored id is scoped by a per-run token and an epoch that
        # increments at every reset. Unique per camera, stable within a
        # track's life, and still readable in a log line.
        self._run_token = uuid.uuid4().hex[:8]
        self._epoch = 0

        self.sightings_written = 0

    # -- setup -------------------------------------------------------------

    async def prepare(self) -> bool:
        """Read the survey result. A camera with no profile is skipped.

        Not a failure: it means the camera was onboarded but not yet
        surveyed, and a camera that claims nothing should produce nothing.
        """
        async with transaction() as conn:
            self.gating = await writer.load_gating(conn, self.camera_id)

        if self.gating is None:
            log.warning("camera_unsurveyed", camera=self.camera_id,
                        action="skipping; record a capability profile first")
            return False

        self.tracker = ByteTrack()
        self.traffic = TrafficAccumulator(
            self.camera_id,
            expected_fps=self.gating.decode_fps or settings.target_decode_fps,
            density_viable=self.gating.density_viable,
            lane_polygon_px=_parse_polygon_wkt(self.gating.lane_polygon_wkt),
            lane_count=self.gating.lane_count,
            lane_length_m=float((self.gating.distortion or {}).get("lane_length_m", 0))
            or None,
        )
        return True

    # -- frame handling ----------------------------------------------------

    def _scoped_track_id(self, track: Track) -> str:
        """A track id unique to this camera for all time.

        `<run>:<epoch>:<n>` -- the run token distinguishes worker restarts,
        the epoch distinguishes the segments between scene cuts, and n is the
        tracker's own id within a segment.
        """
        return f"{self._run_token}:{self._epoch}:{track.track_id}"

    def _cache_frame(self, frame: Frame) -> None:
        self._frames[frame.pts_s] = frame.image
        while len(self._frames) > FRAME_CACHE_SIZE:
            self._frames.popitem(last=False)

    def _crossed_loop_point(self, frame: Frame) -> bool:
        """Has PTS passed a multiple of the measured loop period?

        This is the reliable detector, and the only one, when the gateway
        keeps PTS continuous across the loop. It needs camera_profiles
        .loop_period_s, which the survey measures with:

            sentinel-ingest preflight <camera_id>

        With no measurement it returns False and we fall back to the two
        opportunistic detectors, which catch some cases and not others.
        """
        period = self.gating.loop_period_s if self.gating else None
        if not period or period <= 0:
            return False
        index = int(frame.pts_s // period)
        if self._loop_index is None:
            self._loop_index = index
            return False
        if index != self._loop_index:
            self._loop_index = index
            return True
        return False

    def _should_archive(self, frame: Frame) -> bool:
        """One frame per second, by PTS.

        The archive rate and the processing rate are different things and
        stay different: tracking needs 8-12 fps to follow a vehicle across a
        frame, the archive needs 1 because a human is looking at it.
        """
        interval = 1.0 / max(settings.archive_fps, 0.001)
        if self._last_archive_pts is None or frame.pts_s - self._last_archive_pts >= interval:
            self._last_archive_pts = frame.pts_s
            return True
        return False

    async def _flush_tracks(self, tracks: list[Track], frame: Frame) -> None:
        """Write a sighting per finished track."""
        if not tracks or self.gating is None:
            return
        w, h = frame.shape

        def at_pts(pts_s: float) -> datetime:
            """Wall clock for a PTS, anchored on the frame in hand.

            Clamped: at a scene cut this is called with the post-cut frame
            while the tracks still carry pre-cut PTS.
            """
            return frame.seen_at - timedelta(seconds=max(0.0, frame.pts_s - pts_s))

        for track in tracks:
            if track.cls not in VEHICLE_CLASSES:
                # People are tracked so a person box competes for the
                # association rather than being absorbed into a vehicle
                # track, but a pedestrian is not a sighting. This platform
                # cannot answer "where has this person been", and writing
                # person sightings is how it would start to.
                continue

            best = bestframe.choose(track, self._frames, w, h)
            if best is None:
                continue

            seen_at = at_pts(best.pts_s)
            read_id = uuid.uuid4()

            # Outside the transaction: a file write cannot be rolled back.
            # The name is unique, so failure means cleanup, not collision.
            try:
                crop_ref = writer.write_crop(
                    self.camera_id, seen_at, read_id, best.crop
                )
            except Exception as exc:
                log.error("crop_write_failed", camera=self.camera_id,
                          track=track.track_id, error=str(exc))
                continue

            written = False
            try:
                async with transaction() as conn:
                    written = await writer.write_sighting(
                        conn,
                        read_id=read_id,
                        camera_id=self.camera_id,
                        track_id=self._scoped_track_id(track),
                        seen_at=seen_at,
                        track_start_at=at_pts(track.start_pts_s),
                        track_end_at=at_pts(track.last_pts_s),
                        bbox=best.bbox,
                        crop_bbox=best.crop_bbox,
                        cls=track.cls,
                        crop_ref=crop_ref,
                        detect_model_id=self.detect_model_id,
                        gating=self.gating,
                    ) is not None
            except Exception as exc:
                log.error("sighting_write_failed", camera=self.camera_id,
                          track=track.track_id, error=str(exc))

            if written:
                self.sightings_written += 1
            else:
                writer.discard_crop(crop_ref)

            if self.traffic is not None:
                self.traffic.observe_track_end(track.track_id, track.cls, track.duration_s)

    async def _on_discontinuity(self, frame: Frame) -> None:
        """The feed looped, or the camera rebooted.

        Every live track is finished here rather than carried across: two
        vehicles either side of a cut are not one vehicle, and a track that
        spanned the cut would become a route leg between two unrelated
        sightings. The open bucket is discarded for the same reason -- an
        average across a scene change describes nothing.
        """
        if self.tracker is None:
            return
        cut_tracks = self.tracker.reset()
        # Flush the old segment's tracks under the OLD epoch, then move on:
        # ids are about to start from 1 again.
        await self._flush_tracks(cut_tracks, frame)
        self._epoch += 1
        self._frames.clear()
        self._last_archive_pts = None
        self._loop_index = None
        if self.traffic is not None:
            self.traffic.discard()
        log.info("state_reset_after_cut", camera=self.camera_id,
                 tracks_flushed=len(cut_tracks), epoch=self._epoch)

    async def _post_health(self, reachable: bool) -> None:
        stats = self.stream.stats() if self.stream else {}
        try:
            async with transaction() as conn:
                await writer.write_health(
                    conn, self.camera_id,
                    reachable=reachable,
                    last_frame_at=datetime.now(UTC) if reachable else None,
                    measured_fps=stats.get("measured_fps"),
                    detections_1h=self._detections_since_health,
                    detail={**stats, "sightings": self.sightings_written},
                )
        except Exception as exc:
            log.error("health_write_failed", camera=self.camera_id, error=str(exc))
        self._detections_since_health = 0
        self._last_health = time.monotonic()

    # -- main loop ---------------------------------------------------------

    async def run(self) -> None:
        if not await self.prepare():
            return

        try:
            handle = self.adapter.open(self.camera_id)
        except AdapterError as exc:
            log.error("camera_open_failed", camera=self.camera_id, error=str(exc))
            await self._post_health(reachable=False)
            return

        assert self.gating is not None and self.tracker is not None
        self.stream = CameraStream(
            handle, target_fps=self.gating.decode_fps or settings.target_decode_fps
        )
        loop = asyncio.get_running_loop()
        frame_iter = self.stream.frames()
        log.info("camera_worker_started", camera=self.camera_id)

        try:
            while not self._stopping:
                # next() on a blocking iterator, off the event loop.
                frame = await loop.run_in_executor(None, lambda: next(frame_iter, None))
                if frame is None:
                    break

                if frame.discontinuity:
                    await self._on_discontinuity(frame)
                    continue

                # Scheduled cut, from the measured loop period. Checked
                # before detection so no frame is tracked across the join.
                if self._crossed_loop_point(frame):
                    log.info("scene_cut_scheduled", camera=self.camera_id,
                             pts_s=round(frame.pts_s, 2),
                             period_s=self.gating.loop_period_s)
                    await self._on_discontinuity(frame)
                    continue

                detections = await loop.run_in_executor(
                    None, self.detector, frame.image
                )
                self._detections_since_health += len(detections)
                self._cache_frame(frame)

                _, finished = self.tracker.update(detections, frame.dt_s, frame.pts_s)
                if finished:
                    await self._flush_tracks(finished, frame)

                # Opportunistic cut detector, and a weak one. It fires
                # when every vehicle on screen is replaced at once, which
                # happens at a loop point only if traffic is SPARSE. Measured
                # against a real looping feed: in dense traffic the boxes are
                # close enough that greedy IoU finds a partner for every
                # track, nothing dissociates, and the tracks silently re-bind
                # to different vehicles. So this is a bonus, not the
                # mechanism -- camera_profiles.loop_period_s is.
                if self.tracker.suspects_scene_cut():
                    log.info(
                        "scene_cut_from_track_dissociation",
                        camera=self.camera_id,
                        tracks=self.tracker.last_stats.confirmed_before,
                        dissociated=self.tracker.last_stats.dissociated,
                        pts_s=round(frame.pts_s, 2),
                    )
                    await self._on_discontinuity(frame)
                    continue

                if self.traffic is not None:
                    closed = self.traffic.observe_frame(frame.seen_at, detections)
                    if closed:
                        async with transaction() as conn:
                            await writer.write_traffic(conn, closed)

                if self._should_archive(frame):
                    try:
                        async with transaction() as conn:
                            await writer.archive_frame(
                                conn, self.camera_id, frame.seen_at.replace(microsecond=0),
                                frame.image, detections,
                            )
                    except Exception as exc:
                        log.warning("frame_archive_failed",
                                    camera=self.camera_id, error=str(exc))

                if time.monotonic() - self._last_health > HEALTH_INTERVAL_S:
                    await self._post_health(reachable=True)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("camera_worker_crashed", camera=self.camera_id,
                      error=f"{type(exc).__name__}: {exc}")
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Close the capture and flush what is still open.

        Each connected client gets its own copy of the stream, so releasing
        the capture matters -- a worker that exits without closing leaves the
        gateway sending frames to nobody.
        """
        self._stopping = True
        if self.stream is not None:
            self.stream.close()
        if self.traffic is not None:
            summary = self.traffic.close()
            if summary:
                try:
                    async with transaction() as conn:
                        await writer.write_traffic(conn, summary)
                except Exception as exc:
                    log.warning("final_bucket_failed", camera=self.camera_id, error=str(exc))
        log.info("camera_worker_stopped", camera=self.camera_id,
                 sightings=self.sightings_written)

    def stop(self) -> None:
        self._stopping = True


def stats_snapshot(workers: dict[str, CameraWorker]) -> dict[str, Any]:
    return {
        cam: {"sightings": w.sightings_written, **(w.stream.stats() if w.stream else {})}
        for cam, w in workers.items()
    }
