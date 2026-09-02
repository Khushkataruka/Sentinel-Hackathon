
BEGIN;

CREATE EXTENSION IF NOT EXISTS postgis;    -- spatial types and functions
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector: embedding storage and KNN
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- trigram indexes for fuzzy plate search

-- ----------------------------------------------------------------------------
-- Enumerated types
-- ----------------------------------------------------------------------------

CREATE TYPE camera_kind     AS ENUM ('ip', 'analog');
CREATE TYPE adapter_status  AS ENUM ('registered', 'failed', 'disabled');
CREATE TYPE pipeline_status AS ENUM ('pending', 'done', 'failed', 'skipped', 'timeout');
CREATE TYPE sighting_state  AS ENUM ('open', 'complete', 'correlated');
CREATE TYPE search_kind     AS ENUM ('registration', 'description', 'watchlist_backfill');
CREATE TYPE alert_tier      AS ENUM ('dismiss', 'review', 'priority');
CREATE TYPE alert_status    AS ENUM ('new', 'approved', 'rejected', 'escalated');
CREATE TYPE actor_kind      AS ENUM ('user', 'service');

-- A violation is an observation. An alert is something an operator is asked
-- to act on. They get separate lifecycles because most violations never
-- become alerts.
CREATE TYPE review_status   AS ENUM ('auto_confirmed', 'pending_review',
                                     'confirmed', 'rejected');

-- Level of service. Five coarse bands on purpose: a control room reads them
-- at a glance, and we have not calibrated anything finer.
CREATE TYPE los_band        AS ENUM ('free', 'light', 'moderate', 'heavy', 'jam');

-- ----------------------------------------------------------------------------
-- updated_at maintenance.
-- Every table with an updated_at column gets this trigger.
-- ----------------------------------------------------------------------------

CREATE FUNCTION touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- ============================================================================
-- REGISTRY: departments, users, adapters, cameras, capability profiles, health
-- ============================================================================

CREATE TABLE departments (
  id          smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code        text NOT NULL UNIQUE,          -- e.g. 'HOME', 'FCS', 'RTO'
  name        text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username       text NOT NULL UNIQUE,
  display_name   text NOT NULL,
  department_id  smallint NOT NULL REFERENCES departments(id),
  role           text NOT NULL CHECK (role IN ('operator', 'admin', 'auditor')),
  active         boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER users_touch BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Cross-department viewing rights. A user sees their own department by
-- default; anything else needs a row here. Grants and revocations also go
-- to audit_log.
CREATE TABLE access_grants (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id        uuid NOT NULL REFERENCES users(id),
  department_id  smallint NOT NULL REFERENCES departments(id),
  granted_by     uuid NOT NULL REFERENCES users(id),
  reason         text NOT NULL,
  expires_at     timestamptz,
  revoked_at     timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX access_grants_user_idx ON access_grants (user_id)
  WHERE revoked_at IS NULL;

-- One row per adapter loaded from the adapter folder.
CREATE TABLE adapters (
  id          smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name        text NOT NULL UNIQUE,
  driver      text NOT NULL,                 -- module/entrypoint of the driver
  config      jsonb NOT NULL DEFAULT '{}',
  status      adapter_status NOT NULL DEFAULT 'registered',
  last_error  text,
  tested_at   timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER adapters_touch BEFORE UPDATE ON adapters
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE cameras (
  camera_id       text PRIMARY KEY,          -- stable external id, dept-scoped
  department_id   smallint NOT NULL REFERENCES departments(id),
  name            text NOT NULL,
  kind            camera_kind NOT NULL,
  vendor          text,
  protocol        text,                      -- 'rtsp', 'onvif', 'vms-api', ...
  adapter_id      smallint REFERENCES adapters(id),
  location        geography(Point, 4326) NOT NULL,
  bearing_deg     real CHECK (bearing_deg >= 0 AND bearing_deg < 360),
  range_m         real CHECK (range_m > 0),
  storage_kind    text,                      -- 'cloud', 'local', 'nvr', ...
  retention_days  smallint,
  contract_expiry date,
  enabled         boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER cameras_touch BEFORE UPDATE ON cameras
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE INDEX cameras_dept_idx     ON cameras (department_id);
CREATE INDEX cameras_location_idx ON cameras USING gist (location);

-- Output of the §6 camera survey. One row per camera, re-measurable.
-- This row decides which pipelines a sighting from this camera enters.
CREATE TABLE camera_profiles (
  camera_id            text PRIMARY KEY REFERENCES cameras(camera_id),
  resolution_class     text NOT NULL,        -- 'full', 'reduced', 'thumbnail'
  permitted_attributes text[] NOT NULL DEFAULT '{}',  -- e.g. {colour,type,make}
  permitted_violations text[] NOT NULL DEFAULT '{}',  -- violation_types.code values
  plate_viable         boolean NOT NULL DEFAULT false,
  density_viable       boolean NOT NULL DEFAULT false, -- lane geometry measured
  lane_polygon         geometry(Polygon),    -- image-space carriageway mask
  lane_count           smallint CHECK (lane_count > 0),
  decode_fps           real,
  deinterlace          boolean NOT NULL DEFAULT false,
  distortion           jsonb,                -- undistortion params, null = none
  trust_level          real NOT NULL DEFAULT 1.0
                         CHECK (trust_level > 0 AND trust_level <= 1),
  corridor_group       text,
  measured_at          timestamptz NOT NULL DEFAULT now(),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER camera_profiles_touch BEFORE UPDATE ON camera_profiles
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE INDEX camera_profiles_violations_idx ON camera_profiles
  USING gin (permitted_violations);

-- Health history, one row per check. Append-only by convention.
-- "Reachable but zero detections in six hours" is visible here and
-- nowhere else.
CREATE TABLE camera_health (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  camera_id        text NOT NULL REFERENCES cameras(camera_id),
  checked_at       timestamptz NOT NULL DEFAULT now(),
  reachable        boolean NOT NULL,
  last_frame_at    timestamptz,
  decode_fps       real,
  detections_1h    integer,
  verdict          text NOT NULL,            -- 'ok', 'degraded', 'silent', 'down'
  detail           jsonb
);
CREATE INDEX camera_health_cam_idx ON camera_health (camera_id, checked_at DESC);

-- Latest health per camera, for the map.
CREATE VIEW camera_latest_health AS
  SELECT DISTINCT ON (camera_id) *
  FROM camera_health
  ORDER BY camera_id, checked_at DESC;

-- ============================================================================
-- MODEL REGISTRY
-- ============================================================================

-- Description and embedding are now separate models, and the violation
-- pipeline adds several more.
CREATE TABLE model_versions (
  id             smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  role           text NOT NULL CHECK (role IN
                   ('detect', 'track', 'describe', 'embed', 'caption_embed',
                    'plate_detect', 'plate_ocr', 'violation')),
  name           text NOT NULL,              -- 'florence-2-base-ft', 'reid-r50'
  version        text NOT NULL,
  weights_sha256 text,                       -- what ran, not what we meant to run
  output_dim     smallint,                   -- embeddings only
  runtime        text,                       -- 'onnx-cpu', 'onnx-cuda', ...
  notes          text,
  registered_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (role, name, version)
);

-- ============================================================================
-- SIGHTINGS: the stub record ingestion writes and the pipelines fill in
-- ============================================================================

-- One row per tracked vehicle passing one camera. Ingestion inserts the
-- stub; the description, embedding, plate and violation pipelines UPDATE
-- their own columns. Filterable attributes are real columns, not JSON,
-- because the candidate search filters on them with an index (§5.7).
CREATE TABLE sightings (
  read_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  camera_id       text NOT NULL REFERENCES cameras(camera_id),
  track_id        text NOT NULL,
  seen_at         timestamptz NOT NULL,      -- best-frame timestamp
  track_start_at  timestamptz,               -- first frame of the track
  track_end_at    timestamptz,               -- last frame; both feed dwell time
  bbox            integer[] NOT NULL CHECK (cardinality(bbox) = 4),
  class           text NOT NULL,             -- 'car','motorcycle','bus','truck','auto'
  crop_ref        text NOT NULL,             -- path of the best-frame crop on disk
  detect_model_id smallint REFERENCES model_versions(id),

  -- Description pipeline output (captioning model, structured fields parsed
  -- out of constrained generation)
  colour            text,
  vtype             text,                    -- 'hatchback', 'sedan', 'suv', ...
  make              text,
  model             text,
  features          jsonb NOT NULL DEFAULT '[]',
  caption           text,                    -- free-text description as generated
  caption_embedding vector(384),             -- text encoder over `caption`
  describe_model_id smallint REFERENCES model_versions(id),

  -- Appearance embedding: separate identity-trained model, separate pass
  embedding       vector(512),               -- dim MUST match model_versions.output_dim
  embed_model_id  smallint REFERENCES model_versions(id),

  -- Plate pipeline output (best hypothesis; the rest in plate_hypotheses)
  plate_text      text,
  plate_conf      real,                      -- kept: sets the fuzzy-match cutoff

  -- Pipeline bookkeeping. Ingestion sets 'skipped' from the camera profile
  -- at insert; a pipeline that never runs never blocks correlation.
  describe_status  pipeline_status NOT NULL DEFAULT 'pending',
  embed_status     pipeline_status NOT NULL DEFAULT 'pending',
  plate_status     pipeline_status NOT NULL DEFAULT 'pending',
  violation_status pipeline_status NOT NULL DEFAULT 'pending',
  state            sighting_state  NOT NULL DEFAULT 'open',

  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),

  UNIQUE (camera_id, track_id)
);
CREATE TRIGGER sightings_touch BEFORE UPDATE ON sightings
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- Time-window scans per camera (route building, operator review)
CREATE INDEX sightings_cam_time_idx ON sightings (camera_id, seen_at);
CREATE INDEX sightings_time_idx     ON sightings (seen_at);

-- The cheap attribute filter
CREATE INDEX sightings_attr_idx ON sightings (colour, vtype, make, model)
  WHERE colour IS NOT NULL;

-- Polling cursor for anything watching this table
CREATE INDEX sightings_updated_idx ON sightings (updated_at);

-- KNN over what survives the attribute filter. Partial: rows without an
-- embedding are invisible to it. cosine to match the re-id literature.
CREATE INDEX sightings_embedding_idx ON sightings
  USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

-- Natural-language search over the generated caption. "White hatchback with
-- a roof carrier" lands here; it cannot be expressed in the attribute filter.
CREATE INDEX sightings_caption_vec_idx ON sightings
  USING hnsw (caption_embedding vector_cosine_ops)
  WHERE caption_embedding IS NOT NULL;
CREATE INDEX sightings_caption_trgm_idx ON sightings
  USING gin (caption gin_trgm_ops);

-- Exact plate lookups
CREATE INDEX sightings_plate_idx ON sightings (plate_text)
  WHERE plate_text IS NOT NULL;

-- Top-k OCR guesses per sighting, one row each, so they are indexable.
-- A JSON array inside sightings cannot carry a trigram index; this can.
CREATE TABLE plate_hypotheses (
  read_id     uuid NOT NULL REFERENCES sightings(read_id) ON DELETE CASCADE,
  rank        smallint NOT NULL CHECK (rank >= 1),
  plate       text NOT NULL,
  confidence  real NOT NULL,
  PRIMARY KEY (read_id, rank)
);
CREATE INDEX plate_hypotheses_trgm_idx ON plate_hypotheses
  USING gin (plate gin_trgm_ops);

-- ----------------------------------------------------------------------------
-- Riders.
--
-- It exists so that
-- "no helmet" can name WHICH rider, and so that "triple riding" is a row
-- count instead of a number buried in a JSON blob.
-- ----------------------------------------------------------------------------
CREATE TABLE sighting_riders (
  read_id      uuid NOT NULL REFERENCES sightings(read_id) ON DELETE CASCADE,
  slot         smallint NOT NULL CHECK (slot >= 1),   -- 1 = rider, 2+ = pillion
  bbox         integer[] NOT NULL CHECK (cardinality(bbox) = 4),
  helmet       boolean,                    -- NULL = not assessable on this camera
  helmet_conf  real,                       -- detector box score; gates review
  detector_id  smallint REFERENCES model_versions(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (read_id, slot)
);
CREATE INDEX sighting_riders_nohelmet_idx ON sighting_riders (read_id)
  WHERE helmet IS FALSE;

-- What pollers should actually read. No embedding column: a 512-float
-- vector is ~2 KB per row and the notification service recomputes nothing
-- from it during a poll — it fetches vectors only for the rows it scores.
CREATE VIEW sightings_poll AS
  SELECT read_id, camera_id, track_id, seen_at, class,
         colour, vtype, make, model, caption,
         plate_text, plate_conf,
         describe_status, embed_status, plate_status, violation_status, state,
         created_at, updated_at
  FROM sightings;

-- ----------------------------------------------------------------------------
-- Outbox for the notification/correlation service.
-- Written in the SAME transaction as the sighting insert/update, so an
-- event exists if and only if the change it describes is committed.
-- The consumer claims rows with FOR UPDATE SKIP LOCKED and marks them
-- consumed. That is immune to the commit-order race that raw
-- "WHERE created_at > $last" polling has.
-- ----------------------------------------------------------------------------
CREATE TABLE sighting_events (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  read_id      uuid NOT NULL,
  event        text NOT NULL,   -- 'created','describe_done','embed_done',
                                -- 'plate_done','violation_done','complete','timeout'
  created_at   timestamptz NOT NULL DEFAULT now(),
  consumed_at  timestamptz
);
CREATE INDEX sighting_events_pending_idx ON sighting_events (id)
  WHERE consumed_at IS NULL;

-- ============================================================================
-- TRAFFIC VIOLATIONS
-- ============================================================================

-- The catalogue. violations.violation_type is a foreign key to this, so the
-- set of things this platform is willing to assert is data an admin can
-- inspect, not strings scattered through pipeline code.
--
-- needs_glass_penetration is the honest column. Seatbelt and phone use both
-- require resolving a small, low-contrast object through a windscreen at
-- pole distance — a harder optical problem than the face detection this
-- platform dropped as infeasible. Both types stay in the catalogue, but a
-- camera only produces them if the survey put them in permitted_violations,
-- and neither has an auto_confirm_threshold.
CREATE TABLE violation_types (
  code                    text PRIMARY KEY,      -- 'no_helmet', 'phone_use', ...
  label                   text NOT NULL,         -- what the operator sees
  mv_act_section          text,                  -- indicative, for the report only
  subject                 text NOT NULL
                            CHECK (subject IN ('vehicle', 'rider')),
  applies_to              text[] NOT NULL DEFAULT '{}',  -- vehicle classes; {} = any
  is_temporal             boolean NOT NULL DEFAULT false, -- needs a track, not a frame
  needs_glass_penetration boolean NOT NULL DEFAULT false,
  needs_scene_context     boolean NOT NULL DEFAULT false, -- signal head, stop line
  auto_confirm_threshold  real,                  -- NULL = always goes to a human
  review_threshold        real NOT NULL,         -- below this, discard silently
  enabled                 boolean NOT NULL DEFAULT true,
  CHECK (auto_confirm_threshold IS NULL
         OR auto_confirm_threshold >= review_threshold)
);

INSERT INTO violation_types
  (code, label, mv_act_section, subject, applies_to, is_temporal,
   needs_glass_penetration, needs_scene_context,
   auto_confirm_threshold, review_threshold) VALUES
  ('no_helmet',       'Riding without helmet',              '194D', 'rider',
     '{motorcycle}', false, false, false, 0.85, 0.45),
  ('triple_riding',   'More than two on a two-wheeler',     '194C', 'vehicle',
     '{motorcycle}', false, false, false, 0.80, 0.45),
  ('wrong_way',       'Driving against traffic',            '184',  'vehicle',
     '{}',           true,  false, true,  0.85, 0.50),
  ('red_light',       'Signal violation',                   '184',  'vehicle',
     '{}',           true,  false, true,  NULL, 0.60),
  ('illegal_parking', 'Parked in a no-parking zone',        '201',  'vehicle',
     '{}',           true,  false, true,  NULL, 0.55),
  ('no_seatbelt',     'Driving without seatbelt',           '194B', 'vehicle',
     '{car}',        false, true,  false, NULL, 0.60),
  ('phone_use',       'Mobile phone use while driving',     '184',  'vehicle',
     '{}',           false, true,  false, NULL, 0.60);

-- Zero or more rows per sighting. A sighting with no violations has no rows
-- here.
--
-- confidence survives on this table, unlike on the description, because it
-- does a different job: it decides whether a human looks at the case. It is
-- a threshold on a detector box score, not a claim about who the vehicle is.
CREATE TABLE violations (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  read_id        uuid NOT NULL REFERENCES sightings(read_id) ON DELETE CASCADE,
  camera_id      text NOT NULL REFERENCES cameras(camera_id),
  violation_type text NOT NULL REFERENCES violation_types(code),
  rider_slot     smallint,                  -- set when the type's subject is 'rider'
  confidence     real NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  seen_at        timestamptz NOT NULL,
  window_start   timestamptz,               -- temporal types: the span observed
  window_end     timestamptz,
  evidence_ref   text,                      -- annotated frame on disk, if produced
  evidence_bbox  integer[] CHECK (evidence_bbox IS NULL
                                  OR cardinality(evidence_bbox) = 4),
  detector_id    smallint REFERENCES model_versions(id),
  review_status  review_status NOT NULL DEFAULT 'pending_review',
  reviewed_by    uuid REFERENCES users(id),
  reviewed_at    timestamptz,
  details        jsonb NOT NULL DEFAULT '{}',
  created_at     timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (read_id, rider_slot)
    REFERENCES sighting_riders(read_id, slot),  -- skipped when rider_slot IS NULL
  CHECK (window_end IS NULL OR window_start IS NULL OR window_end >= window_start),
  CHECK ((review_status IN ('confirmed', 'rejected')) = (reviewed_at IS NOT NULL))
);
CREATE INDEX violations_cam_time_idx ON violations (camera_id, seen_at);
CREATE INDEX violations_read_idx     ON violations (read_id);
CREATE INDEX violations_created_idx  ON violations (created_at);   -- polling cursor
CREATE INDEX violations_queue_idx    ON violations (violation_type, seen_at DESC)
  WHERE review_status = 'pending_review';

-- A violation may only be asserted on a camera whose survey permits that
-- type. Enforced here rather than trusted to the pipeline, because the
-- pipeline is the part most likely to be wrong at three in the morning.
CREATE FUNCTION violation_permitted() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE allowed text[];
BEGIN
  SELECT permitted_violations INTO allowed
    FROM camera_profiles WHERE camera_id = NEW.camera_id;
  IF allowed IS NULL OR NOT (NEW.violation_type = ANY (allowed)) THEN
    RAISE EXCEPTION 'camera % is not surveyed for violation type %',
      NEW.camera_id, NEW.violation_type;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER violations_permitted_check BEFORE INSERT ON violations
  FOR EACH ROW EXECUTE FUNCTION violation_permitted();

-- ============================================================================
-- TRAFFIC FLOW AND DENSITY
-- ============================================================================

-- Two different quantities, two tables, because conflating them is the
-- standard mistake. One fixed camera measures FLOW: vehicles crossing per
-- interval. DENSITY is vehicles per unit length of road, which we estimate
-- from carriageway occupancy, and only on cameras whose lane geometry was
-- measured (camera_profiles.density_viable).

-- Flow, per class, per bucket.
CREATE TABLE traffic_counts (
  camera_id       text NOT NULL REFERENCES cameras(camera_id),
  bucket_start    timestamptz NOT NULL,
  bucket_seconds  integer NOT NULL DEFAULT 300 CHECK (bucket_seconds > 0),
  class           text NOT NULL,
  vehicle_count   integer NOT NULL CHECK (vehicle_count >= 0),  -- distinct tracks
  mean_dwell_s    real,                    -- time in frame; a slowness proxy, not speed
  computed_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (camera_id, bucket_start, class)
);
CREATE INDEX traffic_counts_time_idx ON traffic_counts (bucket_start);

-- Density and observation coverage, per bucket, all classes together.
--
-- frames_expected / frames_decoded is the pair that stops this analytic
-- being noise. A camera that was down for four of five minutes reports a
-- low count, and a low count is indistinguishable from a quiet road unless
-- you can see how much of the interval was actually observed. Every row in
-- traffic_counts must be read against this one.
CREATE TABLE traffic_state (
  camera_id       text NOT NULL REFERENCES cameras(camera_id),
  bucket_start    timestamptz NOT NULL,
  bucket_seconds  integer NOT NULL DEFAULT 300 CHECK (bucket_seconds > 0),
  frames_expected integer NOT NULL CHECK (frames_expected > 0),
  frames_decoded  integer NOT NULL CHECK (frames_decoded >= 0),
  mean_occupancy  real CHECK (mean_occupancy BETWEEN 0 AND 1),  -- lane area covered
  peak_occupancy  real CHECK (peak_occupancy BETWEEN 0 AND 1),
  mean_concurrent real,                    -- mean vehicles visible per frame
  density_vpkm    real,                    -- NULL unless density_viable
  los             los_band,
  computed_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (camera_id, bucket_start),
  CHECK (frames_decoded <= frames_expected)
);
CREATE INDEX traffic_state_time_idx ON traffic_state (bucket_start);
CREATE INDEX traffic_state_los_idx  ON traffic_state (los, bucket_start DESC)
  WHERE los IN ('heavy', 'jam');

-- Buckets worth plotting. Below 60 percent observed, the numbers are not
-- comparable to anything, and the dashboard should say so rather than draw
-- a reassuring line through them.
CREATE VIEW traffic_state_usable AS
  SELECT *, frames_decoded::real / frames_expected AS coverage
  FROM traffic_state
  WHERE frames_decoded::real / frames_expected >= 0.6;

-- ============================================================================
-- FRAME ARCHIVE: index only; images live on disk
-- ============================================================================

CREATE TABLE frames (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  camera_id    text NOT NULL REFERENCES cameras(camera_id),
  captured_at  timestamptz NOT NULL,
  path         text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (camera_id, captured_at)             -- 1 fps, so this holds
);
-- Operator stepping backwards/forwards from a sighting:
--   WHERE camera_id = $1 AND captured_at BETWEEN $2 AND $3 ORDER BY captured_at
-- is one index range scan.

-- ============================================================================
-- REFERENCE DATA: VAHAN stand-in and rarity counts
-- ============================================================================

-- Stand-in for the VAHAN lookup during the evaluation (§1.2).
CREATE TABLE vahan_vehicles (
  registration_no text PRIMARY KEY,
  colour          text NOT NULL,
  make            text NOT NULL,
  model           text NOT NULL,
  vtype           text NOT NULL,
  vehicle_class   text,
  fuel            text,
  owner_name      text,
  district        text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX vahan_desc_idx ON vahan_vehicles (district, colour, vtype, make, model);

-- "How many vehicles match this description here". Cached from VAHAN.
-- 'any' is the wildcard value: NULL cannot sit in a primary key, and a
-- three-way NULL match in a join is a bug factory anyway.
--
-- With attribute confidences gone, this table carries more weight than it
-- did: it is now the only thing separating a strong description match from
-- a weak one. See §3.2.
CREATE TABLE rarity (
  district    text NOT NULL DEFAULT 'any',
  colour      text NOT NULL DEFAULT 'any',
  vtype       text NOT NULL DEFAULT 'any',
  make        text NOT NULL DEFAULT 'any',
  model       text NOT NULL DEFAULT 'any',
  match_count integer NOT NULL CHECK (match_count >= 0),
  source      text NOT NULL DEFAULT 'vahan_stub',
  fetched_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (district, colour, vtype, make, model)
);

-- ============================================================================
-- WATCHLIST
-- ============================================================================

CREATE TABLE watchlist_entries (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  label           text NOT NULL,             -- what the operator sees
  registration_no text,                      -- if known
  colour          text,
  vtype           text,
  make            text,
  model           text,
  embedding       vector(512),               -- reference appearance, if any
  priority        alert_tier NOT NULL DEFAULT 'review',
  reason          text NOT NULL,
  active          boolean NOT NULL DEFAULT true,
  created_by      uuid NOT NULL REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER watchlist_touch BEFORE UPDATE ON watchlist_entries
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE INDEX watchlist_active_idx  ON watchlist_entries (created_at) WHERE active;
CREATE INDEX watchlist_updated_idx ON watchlist_entries (updated_at);

-- ============================================================================
-- SEARCHES, ROUTES
-- ============================================================================

-- Every search (live query, or the backfill a new watchlist entry triggers)
-- gets a row, so routes have a parent and the audit log has an anchor.
CREATE TABLE searches (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind               search_kind NOT NULL,
  params             jsonb NOT NULL,          -- reg no / description / entry id
  watchlist_entry_id uuid REFERENCES watchlist_entries(id),
  requested_by       uuid REFERENCES users(id),  -- NULL when a service triggers it
  status             text NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'done', 'failed')),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER searches_touch BEFORE UPDATE ON searches
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE routes (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  search_id        uuid NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
  rank             integer NOT NULL,
  score            real NOT NULL,
  competing_count  integer NOT NULL,          -- travels with the score, always shown
  rarity_count     integer,                   -- population behind the description
  plate_anchored   boolean NOT NULL DEFAULT false,  -- any leg backed by an OCR read
  min_trust        real,                      -- weakest camera on the route
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (search_id, rank)
);
CREATE TRIGGER routes_touch BEFORE UPDATE ON routes
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- One row per leg, including the legs we rejected (plausible = false,
-- with the reason), because "why was this link dropped" is an audit answer.
CREATE TABLE route_legs (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  route_id           uuid NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  seq                integer NOT NULL,
  from_read_id       uuid NOT NULL REFERENCES sightings(read_id),
  to_read_id         uuid NOT NULL REFERENCES sightings(read_id),
  distance_km        real NOT NULL,
  elapsed_s          real NOT NULL,
  required_speed_kmh real NOT NULL,
  plausible          boolean NOT NULL,
  drop_reason        text,                    -- NULL when plausible
  gap_s              real,                    -- uncovered stretch, if any
  UNIQUE (route_id, seq)
);

-- ============================================================================
-- ALERTS
-- ============================================================================

CREATE TABLE alerts (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  watchlist_entry_id uuid REFERENCES watchlist_entries(id),  -- NULL for violations
  read_id            uuid REFERENCES sightings(read_id),
  route_id           uuid REFERENCES routes(id),
  violation_id       bigint REFERENCES violations(id),
  congestion_camera  text,                                   -- jam alerts
  congestion_bucket  timestamptz,
  tier               alert_tier NOT NULL,
  score              real NOT NULL,
  status             alert_status NOT NULL DEFAULT 'new',
  decided_by         uuid REFERENCES users(id),
  decided_at         timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CHECK (read_id IS NOT NULL OR violation_id IS NOT NULL
         OR congestion_camera IS NOT NULL),
  FOREIGN KEY (congestion_camera, congestion_bucket)
    REFERENCES traffic_state(camera_id, bucket_start)
);
CREATE TRIGGER alerts_touch BEFORE UPDATE ON alerts
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE INDEX alerts_inbox_idx   ON alerts (tier, created_at DESC) WHERE status = 'new';
CREATE INDEX alerts_updated_idx ON alerts (updated_at);   -- dashboard polling cursor

-- ============================================================================
-- AUDIT LOG: append-only
-- ============================================================================

CREATE TABLE audit_log (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at  timestamptz NOT NULL DEFAULT now(),
  actor_kind   actor_kind NOT NULL,
  actor_id     text NOT NULL,                -- user uuid or service name
  action       text NOT NULL,                -- 'sighting.create', 'alert.approve', ...
  object_type  text NOT NULL,
  object_id    text NOT NULL,
  details      jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_time_idx   ON audit_log (occurred_at);
CREATE INDEX audit_object_idx ON audit_log (object_type, object_id);



CREATE FUNCTION audit_log_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only';
END $$;

CREATE TRIGGER audit_log_no_update
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();

COMMIT;
