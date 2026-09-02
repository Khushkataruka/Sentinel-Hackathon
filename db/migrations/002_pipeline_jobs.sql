-- ============================================================================
-- 002: Crop queues, held in Postgres.
--
-- The design doc (SS 5.2) names four crop queues -- q.describe, q.embed,
-- q.plate, q.violate -- and one durable queue, q.correlate. sighting_events
-- already IS q.correlate: it is an outbox, written in the same transaction as
-- the sighting, claimed with FOR UPDATE SKIP LOCKED. The four crop queues had
-- no home, so they get one here.
--
-- This is the whole of the "Postgres instead of a broker" decision. When a
-- real broker arrives, delete this file and the sentinel.core.queue module.
-- Nothing else references pipeline_jobs.
--
-- Semantics required by SS 5.2:
--   crop queues  -> best effort, drop the OLDEST when they back up
--   q.correlate  -> durable, replayable, block the sender
-- Dropping the oldest is claim_pipeline_jobs' problem, not the table's:
-- see trim_pipeline_queue() below.
-- ============================================================================

BEGIN;

CREATE TYPE pipeline_kind AS ENUM ('describe', 'embed', 'plate', 'violate');

CREATE TABLE pipeline_jobs (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  pipeline    pipeline_kind NOT NULL,
  read_id     uuid NOT NULL REFERENCES sightings(read_id) ON DELETE CASCADE,
  camera_id   text NOT NULL REFERENCES cameras(camera_id),
  crop_ref    text NOT NULL,
  payload     jsonb NOT NULL DEFAULT '{}',   -- e.g. permitted violation types

  attempts    smallint NOT NULL DEFAULT 0,
  max_attempts smallint NOT NULL DEFAULT 3,
  locked_at   timestamptz,
  locked_by   text,
  done_at     timestamptz,
  failed_at   timestamptz,
  dropped_at  timestamptz,                   -- shed under backpressure, not an error
  last_error  text,
  created_at  timestamptz NOT NULL DEFAULT now(),

  -- One job per pipeline per sighting. A pipeline that has already been
  -- enqueued for a sighting is not enqueued twice by a retrying ingest worker.
  UNIQUE (pipeline, read_id)
);

-- The claim scan. Partial, so the index only ever holds live work: a finished
-- queue costs nothing to poll.
CREATE INDEX pipeline_jobs_claim_idx ON pipeline_jobs (pipeline, id)
  WHERE done_at IS NULL AND failed_at IS NULL AND dropped_at IS NULL;

-- Recovering jobs whose worker died holding the lock.
CREATE INDEX pipeline_jobs_locked_idx ON pipeline_jobs (locked_at)
  WHERE locked_at IS NOT NULL AND done_at IS NULL;

CREATE INDEX pipeline_jobs_read_idx ON pipeline_jobs (read_id);

-- ----------------------------------------------------------------------------
-- Backpressure: drop the oldest.
--
-- Called by ingest after enqueueing. Keeps at most `keep_depth` unclaimed jobs
-- per pipeline and marks the surplus dropped, oldest first. Marking rather
-- than deleting, because "we shed 4,000 describe jobs at 18:40" is an
-- operational fact worth being able to see afterwards.
--
-- The sighting's own status column is set to 'skipped' by the caller so
-- correlation is never left waiting on a job that will not run.
-- ----------------------------------------------------------------------------
CREATE FUNCTION trim_pipeline_queue(p_pipeline pipeline_kind, keep_depth integer)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE dropped integer;
BEGIN
  WITH live AS (
    SELECT id, row_number() OVER (ORDER BY id DESC) AS rn
    FROM pipeline_jobs
    WHERE pipeline = p_pipeline
      AND done_at IS NULL AND failed_at IS NULL AND dropped_at IS NULL
      AND locked_at IS NULL
  )
  UPDATE pipeline_jobs j
     SET dropped_at = now(),
         last_error = 'shed under backpressure'
    FROM live
   WHERE j.id = live.id AND live.rn > keep_depth;
  GET DIAGNOSTICS dropped = ROW_COUNT;
  RETURN dropped;
END $$;

-- Queue depth per pipeline, for the admin screen and for deciding when to trim.
CREATE VIEW pipeline_queue_depth AS
  SELECT pipeline,
         count(*) FILTER (WHERE locked_at IS NULL)     AS waiting,
         count(*) FILTER (WHERE locked_at IS NOT NULL) AS in_flight,
         min(created_at)                               AS oldest_waiting_at
  FROM pipeline_jobs
  WHERE done_at IS NULL AND failed_at IS NULL AND dropped_at IS NULL
  GROUP BY pipeline;

COMMIT;
