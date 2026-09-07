# Running the pipeline over a videos
This document is about running a fixed set of video files in, annotated video and cross-camera
correlations out.

```bash
./run_pipeline.sh a.mp4 b.mp4 c.mp4
```

Everything below is either something you have to supply, or something you need
to know to put your own model in the right place.

---

## 1. Quick start

```bash
cp .env.example .env                    # then set SENTINEL_DATABASE_URL
uv sync --extra dev --extra models      # or: make install
./run_pipeline.sh clip1.mp4 clip2.mp4   # or: make pipeline V="clip1.mp4 clip2.mp4"
```

The first run with no detector downloads YOLOv8n and exports it to ONNX. That
pulls torch, which is around two gigabytes, and it happens once. `--no-fetch-models`
refuses instead, which is what you want in CI.

Output lands in `out/<timestamp>/`:

```
videos/<name>.annotated.mp4   boxes, description, plate, violations, MATCH tags
tracks/<name>.jsonl           per-frame box geometry (the join between the two passes)
correlations.json             every vehicle seen by more than one camera
report.html                   the same thing for a human, crops embedded, one file
run.json                      what was ingested: cameras, coordinates, times, counts
videos/annotate.json          per video: frames written, boxes drawn, matches tagged
run.log                       everything every stage printed
```

The stages log to stdout, so `run.log` is where their output goes and each
one writes its own machine-readable file alongside it. Nothing in the runner
parses a command's stdout.

Useful flags:

| | |
|---|---|
| `--manifest videos.json` | give videos real camera ids, coordinates and start times |
| `--out DIR` | where runs are written (default `./out`) |
| `--fps N` | analysis frame rate (default `SENTINEL_TARGET_DECODE_FPS`, 10) |
| `--max-seeds N` | how many sightings to correlate from (default 50) |
| `--no-fetch-models` | fail rather than download a detector |
| `--keep-sightings` | add to previous runs instead of replacing them |

By default a re-run **replaces** the previous run's sightings for those
cameras, along with the searches and routes built from them. Track ids are
scoped per run, so without that a second run would sit alongside the first and
every vehicle would correlate with its own earlier self. `--keep-sightings`
turns the replacement off when you want to accumulate on purpose. The audit
log is never touched.

### The manifest

A video file carries no camera identity, no location and no real timestamp,
and correlation needs all three — every route leg is a distance over an
elapsed time. Without a manifest the runner invents them: cameras strung out
2 km apart with start times 180 s apart, which is about 40 km/h and therefore
inside the plausibility window, so cross-video routes can form at all.
**Those distances are fiction.** The run summary says so, and a manifest is
how you replace them:

```json
{"videos": [
  {"path": "clip1.mp4", "camera_id": "cam04", "name": "Paldi Circle",
   "lat": 23.0121, "lon": 72.5583, "start_at": "2026-09-07T09:00:00Z"},
  {"path": "clip2.mp4", "camera_id": "cam05", "name": "Visat teen Rasta",
   "lat": 23.0789, "lon": 72.5810, "start_at": "2026-09-07T09:04:30Z"}
]}
```

Every field is optional and applies on its own: give a `lat`/`lon` and you keep
the staggered start times. Point `camera_id` at a camera the sandbox sync
already created and you get its real coordinates and trust level.

---

## 2. What you must supply

All settings are `SENTINEL_`-prefixed and read from `.env`
(`packages/sentinel-core/src/sentinel/core/config.py`).

**Required for this flow — nothing runs without it:**

| Variable | Default | Notes |
|---|---|---|
| `SENTINEL_DATABASE_URL` | `postgresql://sentinel:sentinel@localhost:5432/sentinel` | Postgres with **postgis**, **pgvector** and **pg_trgm**. Any host: a Supabase URL works, so does `make db-up`. |

**Worth setting:**

| Variable | Default | Notes |
|---|---|---|
| `SENTINEL_MEDIA_ROOT` | `./var/media` | crops, archived frames and violation evidence. Grows; it is not the database. |
| `SENTINEL_TARGET_DECODE_FPS` | `10.0` | the analysis rate. Also the frame rate of the annotated video. |
| `SENTINEL_DETECT_CONF` | `0.35` | raise it if the video is full of false boxes, lower it if vehicles are missed. |
| `SENTINEL_DETECT_MODEL_PATH` | `./var/models/yolo.onnx` | see §3. |
| `SENTINEL_ONNX_PROVIDERS` | `["CPUExecutionProvider"]` | `["CUDAExecutionProvider","CPUExecutionProvider"]` on a GPU box. |
| `SENTINEL_LOG_LEVEL` | `INFO` | `DEBUG` puts per-frame decisions in `run.log`. |

**Only for the live sandbox grid — this flow ignores all of them:**
`SENTINEL_SENTINEL_BASE_URL`, `SENTINEL_SENTINEL_COOKIE` / `_TOKEN`,
`SENTINEL_GRID_EMAIL`, `SENTINEL_GRID_PASSWORD`, `SENTINEL_GRID_MEDIA_HOST`,
`SENTINEL_GRID_RTSP_PORT`, `SENTINEL_GRID_WHEP_PORT`, `SENTINEL_GRID_PREFER_HLS`.

You do **not** need to run `scripts/survey.py` or start the registry. The
offline ingest writes each video's `cameras` and `camera_profiles` rows
itself, permitting colour/type/make/model, plates, density, and the two
violation types that need neither glass penetration nor scene context. That
profile is marked provisional in `distortion` and in the audit log; it is not
the section 6 survey.

---

## 3. Where your models go

**This is the section to read before dropping anything in.** Every model sits
behind a loader that degrades to a stub rather than crashing, so a missing
file is a quiet log line and a plausible-looking wrong answer — not an error.

Default directory: `var/models/`.

| Role | Put the file at | Loader | What happens without it |
|---|---|---|---|
| **Vehicle detect** | `var/models/yolo.onnx` | `ingest/detect.py:242` `load_detector` | `NullDetector` → **zero sightings, and therefore nothing downstream at all** |
| **Plate detect + OCR** | `var/models/license-plate-finetune-v1m.pt` (or `-v1n.pt`, or `plate_detect.pt`) | `pipelines/models/anpr.py:410` `load_plate_reader` | `StubPlateReader` — returns a plate for one crop in eight, deliberately |
| **Re-ID / appearance** | `var/models/reid.onnx` | `pipelines/models/reid.py:120` `load_reid` | `StubReID` — a random projection of a thumbnail. Cross-camera matching is then noise. |
| **Caption** | `var/models/caption.onnx` | `pipelines/models/captioner.py:170` `load_captioner` | `StubCaptioner`, output prefixed `[stub]` |
| **Caption embed** | `var/models/caption_embed.onnx` | `pipelines/models/captioner.py:180` | **always the stub** — the ONNX branch is not wired up (see §5) |
| **Rider / violation** | — | `pipelines/models/violations.py:154,158` | **stub only; there is no real path yet** (see §5) |

### The contract each one has to satisfy

Every loader returns something matching a `Protocol`. Implement the protocol,
make the loader return your class, and nothing else in the platform changes.

```python
# ingest/detect.py
class Detector(Protocol):
    model_id: int | None
    def __call__(self, image: np.ndarray) -> list[Detection]: ...
    # Detection(bbox=(x1,y1,x2,y2) in FRAME pixels, score: float, cls: str)
    # cls must be one of car, motorcycle, bus, truck, bicycle, person

# pipelines/models/anpr.py
class PlateReader(Protocol):
    is_stub: bool
    def __call__(self, crop: np.ndarray, top_k: int = 3) -> list[PlateRead]: ...

# pipelines/models/reid.py       -> vector of settings.embedding_dim (512)
# pipelines/models/captioner.py  -> caption text + parsed attribute fields
```

`is_stub` is not decoration: it is what puts the `STUB MODELS` banner on the
annotated video. Set it to `False` only when the thing really is a model.

### Two things to know before you drop weights in

**The ingest detector takes ONNX only.** `load_detector` builds an
`onnxruntime` session and nothing else. If you have a `.pt` vehicle model, the
ANPR pipeline will find and use it (it looks for
`var/models/vehicle_detection_master_v1.pt`) while ingest silently ignores it
and finds no vehicles at all. Export it:

```bash
uv sync --extra export
uv run yolo export model=your_model.pt format=onnx opset=12 imgsz=640
mv your_model.onnx var/models/yolo.onnx
```

**Register what you dropped in.** `model_versions` records which weights
produced each row, and the annotated video's stub banner reads from it. After
replacing a model:

```sql
INSERT INTO model_versions (role, name, version, runtime, output_dim, notes)
VALUES ('detect', 'yolov8m-vehicles', '1.0.0', 'onnx-cpu', NULL, 'fine-tuned on GJ footage');
```

Roles are `detect`, `track`, `describe`, `caption_embed`, `embed`,
`plate_detect`, `plate_ocr`, `violation`. A version ending `-stub` is what
triggers the banner. For `embed`, `output_dim` **must** be 512 — it is the
declared width of `sightings.embedding`, and a mismatch fails at insert.

### Extras

```bash
uv sync --extra dev --extra models    # onnxruntime: run ONNX models
uv sync --extra dev --extra export    # + ultralytics: make an ONNX model (pulls torch)
uv sync --extra dev --extra anpr      # + easyocr: the PyTorch plate reader
```

Name `dev` alongside whatever else you want: `uv sync` makes the environment
match *exactly* what is asked for, so syncing `models` on its own quietly
uninstalls pytest and ruff.

`uv sync --all-extras` resolves *root* extras only — a workspace member's
extra is never picked up by it. That is why a freshly synced `.venv` had no
`onnxruntime` despite `sentinel-ingest` declaring it.

---

## 4. What each component does

### The stages `run_pipeline.sh` drives

| # | Stage | Command | What it does |
|---|---|---|---|
| 0 | preflight | — | `.env`, ffmpeg, database reachable and migrated, no two inputs sharing a filename stem |
| 1 | models | `uv sync --extra models` | ensures onnxruntime, exports YOLOv8n if there is no detector, and registers the weights' sha256 in `model_versions` so the STUB banner tells the truth |
| 2 | migrations | `scripts/migrate.py` | applies `db/migrations/*.sql` once each, ledgered in `schema_migrations` |
| 3 | ingest | `sentinel-ingest offline` | decode → detect → track → best frame → `sightings`; writes the track sidecars |
| 4 | pipelines | `sentinel-pipeline run --all --drain` | describe, embed, plate, violate over each crop |
| 5 | correlate | `sentinel-correlation run --drain` then `crosscam` | consumes the outbox; then finds vehicles seen by two cameras |
| 6 | annotate | `sentinel-ingest annotate` | second decode pass, draws everything back onto the video |
| 7 | summary | — | per-camera counts, output paths, and every caveat that applies to this run |

`--drain` on stages 4 and 5 is what makes them terminate: both are daemons by
default, because a live estate has no last job.

### The packages

**`sentinel-core`** — everything shared. `config.py` settings, `db.py` pool and
transactions, `models.py` transport types, `queue.py` the crop queues,
`outbox.py` the sighting event log, `audit.py` the append-only trail,
`media.py` where images go on disk, `streamurl.py` credentialed RTSP URLs.

**`sentinel-registry`** (`:8000`) — departments, cameras, capability profiles,
adapters, health. Not used by this flow; the offline ingest writes those rows
directly so no service has to be listening.

**`sentinel-ingest`** — the camera side.

| Module | Does |
|---|---|
| `adapters/` | where a camera source comes from. `filesrc` is the one this flow uses. |
| `decode.py` | `CameraStream`: OpenCV/FFmpeg, RTSP over TCP, reconnect, rate limiting by PTS. `once=True` stops at end of file. |
| `ptsclock.py` | presentation timestamps → wall clock, and the loop-cut detection. `fixed_epoch` pins a file to a chosen instant. |
| `detect.py` | ONNX YOLO, letterboxing, class-wise NMS. Degrades to `NullDetector`. |
| `track.py` | ByteTrack over Kalman boxes, integrating real PTS deltas rather than frame counts. |
| `bestframe.py` | which frame of a track becomes the crop: size, sharpness, edge margin, detector score. |
| `traffic.py` | occupancy and density per 5-minute bucket, with the coverage fraction. |
| `writer.py` | the one transaction: sighting + outbox event + pipeline jobs, with capability gating applied at insert. |
| `worker.py` | one camera's loop. Holds the tracker, the frame cache and the traffic bucket. |
| **`offline.py`** | **one pass over files: camera rows, profiles, fixed timestamps, the track sidecar.** |
| **`annotate.py`** | **the second pass: draws boxes, description, plate, violations and MATCH tags back onto the video.** |

**`sentinel-pipelines`** — one worker per crop queue. `describe` writes colour,
type, make, model, features and caption; `embed` writes the 512-d appearance
vector; `plate` writes the best read plus every hypothesis; `violate` writes
rider boxes and violations. Each claims with `FOR UPDATE SKIP LOCKED` and
writes its columns, its status and its outbox event in one transaction.

**`sentinel-correlation`** — `candidates.py` five search paths (attributes,
appearance KNN, caption, fuzzy plate, merged), `routes.py` physically possible
routes with speed plausibility, `scoring.py` deterministic ranking,
`watchlist.py` alerts, `worker.py` the outbox consumer, and **`crosscam.py`**,
which is the batch entry point this flow uses.

**`sentinel-api`** (`:8001`) and **`frontend`** (`:8080`) — the control room.
Not started by this flow; `make up` runs the whole stack if you want it.

### How the annotated video is built

Two decode passes, joined on presentation timestamp.

The first pass writes `sightings`, and one box per track — the best frame.
Per-frame geometry lives only in ByteTrack's memory and is discarded, so
`offline.py` hooks an observer onto the tracker and writes every confirmed
box to `tracks/<name>.jsonl` as it goes past.

The labels do not exist yet at that point: colour, plate, violations and
matches are all produced later, by the pipelines and by correlation. So the
second pass re-decodes the same file with the same rate limiter, looks each
frame up by its PTS, and draws.

The output runs at the analysis rate, not the source rate. Every box on it was
measured on the frame it is drawn on. Rendering at 25 fps would mean holding
each box across frames it was never computed for — smoother, and a small lie.

What gets drawn, per vehicle, bottom to top: the structured attributes
(`white hatchback · Maruti Swift`), the caption, any notable feature the
caption did not already mention, the plate with its confidence, any pipeline
still pending, the `MATCH-n` tag, and any violation. Box colour is by class,
except that a match turns it warm and a violation turns it red.

---

## 5. Known ceilings

Things that are true right now, deliberately or otherwise.

- **The annotated video is at the analysis frame rate** (10 fps by default),
  not the source rate. Raise `SENTINEL_TARGET_DECODE_FPS` for smoother output
  at proportionally more compute.
- **A remote database dominates the runtime.** Every sighting is written in
  its own transaction — that atomicity with the outbox is the point — and
  correlation reads one row per outbox event. Against a hosted Postgres each
  of those is a round trip, so a two-minute clip can take several minutes to
  push through, almost all of it waiting on the network rather than on any
  model. `make db-up` (Postgres in Docker, on localhost) makes the same run
  an order of magnitude faster.
- **Low-resolution footage silently loses appearance matching.** The offline
  profile declares `resolution_class = full`, which requires a crop of at
  least 64x64 px before the embed pipeline will run (`MIN_CROP_PIXELS` in
  `pipelines/models/reid.py`). On a 480x360 clip most vehicles are smaller
  than that, so their sightings get no embedding and correlation falls back to
  attributes and plates alone. The skip is logged per sighting as
  `crop below usable size` — grep `run.log` for it before concluding that
  cross-camera matching is broken.
- **`--max-seeds` is a real cap, not a display limit.** Each seed is a full
  search — candidates, route enumeration, scoring, persist — so the number
  decides how long stage 5 takes. Seeds are ordered plate first, then
  embedding, then attributes, so the cap falls on the least informative
  sightings; and a vehicle that is not itself seeded can still turn up as a
  *candidate* inside another seed's search. But past the cap a match can be
  missed. Raise it when a run matters more than the clock.
- **`vahan_vehicles` is empty.** Every rarity count is therefore a fallback,
  which scores matches *down*. Load real registrations and the scoring gets
  sharper. Nothing crashes without it.
- **Synthetic camera positions.** Without a manifest, every distance, elapsed
  time and required speed in the report is derived from a made-up line of
  cameras. The summary says so on every run that uses them.
- **`load_caption_embedder` always returns the stub.** The
  `SENTINEL_CAPTION_EMBED_MODEL_PATH` setting is currently dead — the ONNX
  branch was never wired up. Free-text caption search runs on hashed
  bag-of-words until it is.
- **There is no real violation detector.** `no_helmet` and `triple_riding` are
  permitted and gated correctly all the way to the database trigger, but the
  only implementation is a stub. This is the largest genuinely missing piece.
- **A mid-file decode error ends the pass.** A file's failed read is treated
  as end-of-file, because for a local file it almost always is. `run.json`
  reports `frames_decoded`, so a truncated pass is visible rather than silent.
- **Autorickshaws land as `car` or `truck`.** COCO has no class for them; a
  fine-tuned detector fixes it and nothing else needs to change.
- **No challans, no person tracking, no attribute confidences, no single
  answer.** Those four are design decisions, enforced rather than missing.
  `README.md` explains each.
