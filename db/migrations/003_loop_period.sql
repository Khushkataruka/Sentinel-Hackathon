-- ============================================================================
-- 003: the loop period, measured per camera.
--
-- Why this column exists.
--
-- The sandbox feeds are continuous recordings that loop, and the integration
-- guide says the scene cuts abruptly at the loop point. Long-lived state --
-- tracker ids above all -- has to recover from that cut.
--
-- Three ways of detecting it were tried against a real RTSP feed served by
-- mediamtx, which is the software family the grid runs on:
--
--   1. a presentation-timestamp jump. mediamtx rewrites PTS to run
--      continuously across the loop, so the clock never sees it. Measured:
--      zero detections over 50s of a looping 20s clip.
--   2. mass track dissociation. Works when traffic is sparse and every
--      vehicle really is replaced at once. In dense traffic the boxes are
--      close enough that greedy IoU finds a partner for every track, so
--      nothing dissociates -- the tracks silently re-bind to different
--      vehicles. Measured: zero dissociations at the loop point.
--   3. Kalman prediction residual. Loop frames sit inside the normal range
--      (mean predicted-vs-actual IoU 0.60 at the loop, 0.57 worst case
--      during ordinary traffic). No separation.
--
-- So it is not detectable from the pixels. It IS knowable: the recording has
-- a fixed length, so the cut lands at a fixed PTS interval. Measure it once
-- during the section 6 survey, store it here, and cut on schedule.
--
-- NULL means unknown, which is the safe default: no scheduled cut, and the
-- two opportunistic detectors still run. Measure it with:
--
--     sentinel-ingest preflight <camera_id> --seconds 180
-- ============================================================================

BEGIN;

ALTER TABLE camera_profiles
  ADD COLUMN loop_period_s real
    CHECK (loop_period_s IS NULL OR loop_period_s > 0);

COMMENT ON COLUMN camera_profiles.loop_period_s IS
  'Length of the looping recording behind this feed, in seconds of PTS. '
  'NULL = unknown or not a looping feed. Ingest flushes tracker state at '
  'each multiple, because the loop point is not detectable from the video.';

COMMIT;
