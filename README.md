# Sentinel

Integrated video management and analytics platform.
Gujarat Police Innovation Challenge 2026.

Six services over one Postgres database. This repository is the scaffolding:
everything except the model forward passes is written properly — SQL,
transactions, queue semantics, the outbox, capability gating, route
enumeration, scoring. Where a neural network belongs there is a Protocol and
a deterministic stub behind it. Swapping in real weights changes one class.

---

## Layout

```
db/migrations/       001_schema.sql        the schema, verbatim
                     002_pipeline_jobs.sql the crop queues, held in Postgres
                     003_loop_period.sql   measured loop length, per camera
                     004_crop_bbox.sql     where the crop was cut from
db/seed/             departments, users, model_versions, rarity rows
                     camera_coordinates.json  positions the catalogue lacks
scripts/migrate.py   applies migrations in order, once each
scripts/survey.py    provisional camera profiles, so pipelines are not all skipped
docker/              postgres image, nginx config, container adapter set
adapters/            drop-in adapter folders
frontend/            React control room
packages/
  sentinel-core         config, db, types, queue, outbox, audit, media
  sentinel-registry     FastAPI :8000 — cameras, profiles, adapters, health
  sentinel-ingest       decode, detect, track, sighting stubs, traffic buckets
  sentinel-pipelines    describe, embed, plate, violate
  sentinel-correlation  candidates, routes, scoring, watchlist, alerts
  sentinel-api          FastAPI :8001 — map, search, alerts, traffic, evidence
```

All six install into the `sentinel.*` namespace, so imports do not care which
package a module came from.

---

## Getting it running

```bash
cp .env.example .env          # then set SENTINEL_SENTINEL_BASE_URL
make build                    # images; the first one exports YOLOv8n and is slow
make up                       # the whole stack
```

Frontend on **:8080**, registry on :8000, API on :8001. `make ps` for status,
`make logs S=ingest` to follow one service, `make down` to stop.

Eight containers: postgres (postgis + pgvector), a one-shot `migrate`,
registry, ingest, pipelines, correlation, api, and an nginx that serves the
frontend build. nginx is not decoration, the frontend talks to *two*
services, and the API mounts its static files at `/` last, so letting it
serve the build would swallow every registry route as a 404.

For the evaluation the design calls for three process groups; setting
`SENTINEL_EMBED_CORRELATION=1` runs correlation inside the API process, which
gets you there and lets you drop the `correlation` container.

Without docker:

```bash
make install                  # uv sync --all-extras
make db-up && make seed
uv run sentinel-registry serve
uv run sentinel-ingest run
uv run sentinel-pipeline run --all
uv run sentinel-correlation run
uv run sentinel-api           # :8001
```

### Onboarding the sandbox cameras

```bash
make sync                     # or: uv run sentinel-registry sync-sentinel --department-id 1
```

This reads `{base}/cameras.json` — falling back to the older `/api/ingest`
— and upserts every camera it lists. Camera ids are theirs; the catalogue is
the contract, so a URL is only constructed when an entry omits one.

Synced cameras arrive with a **provisional profile that permits nothing**.
They will produce sightings with every pipeline marked `skipped` until a
survey is recorded against them. That is the intended order, not a bug —
capability comes from measurement, and the measurement involves photographing
a windscreen.

```bash
curl -X PUT localhost:8000/cameras/CAM-1/profile \
  -H 'X-Sentinel-User: admin' -H 'Content-Type: application/json' \
  -d '{"resolution_class":"full","permitted_attributes":["colour","type","make"],
       "permitted_violations":["no_helmet","triple_riding"],
       "plate_viable":false,"trust_level":0.8}'
```

Cameras still waiting: `scripts/survey.py --list`. Note that
`GET /cameras/needing-survey` will look empty after a sync — it finds cameras
with no profile *row*, and the sync writes a permit-nothing row for every
camera it imports. The backlog is a profile that permits nothing, not a
missing one.

To get the stack producing something visible before a real survey exists:

```bash
make survey ARGS=--all        # provisional profiles: colour/type/make only
```

That deliberately leaves `plate_viable` false and `permitted_violations`
empty. It is not the section 6 survey and says so in the audit trail.

---

## What is real and what is a stub

| | Real | Stubbed |
|---|---|---|
| core | all of it | — |
| registry | all of it | authentication is a username header |
| ingest | adapters, PTS clock, decode, ByteTrack, best-frame, traffic, writes | YOLO forward pass |
| pipelines | worker loop, claim/ack, gating, writes, events | caption, re-id, OCR, violation detectors |
| correlation | candidate SQL, fuzzy plates, routes, scoring, tiering, alerts | VAHAN reads the stand-in tables |
| api | all endpoints | — |
| frontend | routing, API client, page shells | visual design |

Every stub logs loudly at load and marks its output. Stub captions are
prefixed `[stub]`. The plate stub reads nothing about seven times in eight,
because §3.1 says plates are not legible on most of this estate and a stub
that returned one every time would make every route look plate-anchored and
falsely confident.

**Missing weights degrade, they do not crash.** The whole stack runs with an
empty `var/models/`.

---

## The crop is not the bounding box

Every pipeline sees one image: the best-frame crop. Not the frame, not the
neighbouring boxes.

It used to cut tight to the detector box. COCO annotates a `motorcycle` as the
machine without its rider, so the rider's head is above the box — and
`no_helmet` is about the head. The pipeline was being asked a question the
crop could not answer.

The crop is now padded: 12% on every side, plus 70% of the box height above a
two-wheeler, clipped to the frame. `sightings.bbox` is still the vehicle;
`sightings.crop_bbox` is where the crop was taken from. Both are frame pixels.
`sighting_riders.bbox` is crop pixels — 004 adds column comments saying which
is which, because nothing did before.

Scoring still uses the tight box: sharpness over a padded crop averages in
road, and edge margin measures how much of the vehicle the frame cuts off.

COCO has no autorickshaw class, so autos land as `car` or `truck`.

## The schema additions

`002_pipeline_jobs.sql` adds `pipeline_jobs`, `trim_pipeline_queue()` and
`pipeline_queue_depth`. Nothing else was touched.

The design names five queues. `sighting_events` already *is* `q.correlate` —
an outbox, written in the same transaction as the sighting, claimed with
`FOR UPDATE SKIP LOCKED`. The four crop queues had no home, so they get one
here: best-effort, drop-the-oldest, exactly as §5.2 specifies.

To move to Redis or NATS, delete that migration and `sentinel/core/queue.py`.
Nothing else references the table.

`003_loop_period.sql` adds one nullable column, `camera_profiles.loop_period_s`.

`004_crop_bbox.sql` adds one nullable column, `sightings.crop_bbox`, and
comments the coordinate space of the three `bbox` columns, which disagreed
with each other and said nothing about it.

---

## What was measured against a live feed

The decode path was tested against a real RTSP server (mediamtx, the same
software family the grid runs on) serving looping H.264 and H.265 clips, not
against mocks. Five findings, all of which changed the code.

**1. The loop point is not detectable from the video.** Three detectors were
built and measured:

| Detector | Result |
|---|---|
| PTS discontinuity | 0 fires over 50s of a looping 20s clip — mediamtx rewrites PTS to run straight through the loop |
| Mass track dissociation | 0 fires. In dense traffic the boxes are close enough that greedy IoU finds a partner for every track, so nothing dissociates — the tracks silently re-bind to *different* vehicles |
| Kalman prediction residual | No separation. Loop frames (mean predicted-vs-actual IoU 0.60) sit inside the ordinary-traffic range (worst 0.57) |

Same camera, same background, similar density. There is no signal. So the
loop period is **measured once per camera and stored** —
`camera_profiles.loop_period_s`, added in `003_loop_period.sql` — and ingest
cuts on schedule. `sentinel-ingest preflight` measures it. The two
opportunistic detectors are kept because they cost nothing and catch the
sparse case, but they are not the mechanism.

**2. Track ids collided across worker restarts.** `sightings` has
`UNIQUE (camera_id, track_id)` and ByteTrack numbers from 1, so a restarted
worker's inserts were swallowed by `ON CONFLICT DO NOTHING`. Measured: 23 of
73 sightings vanished silently. Stored ids are now scoped
`<run-token>:<epoch>:<n>`. Re-measured after the fix: 89 written, 89 stored.

**3. A spurious scene cut on every connect.** Some backends report no PTS for
the first frames after a join, so the fallback path jumped when real
timestamps arrived. Harmless in itself, but it corrupted loop-period
measurement. Discontinuities in the first 10 frames after a connect are now
treated as the clock settling.

**4. An empty description matched the whole estate.** A registration number
missing from VAHAN produced a description with no attributes, and the
attribute filter had no WHERE clause left to apply. Fixed; the plate search
still runs alongside.

**5. A capped route count looked like a complete one.** Now marked
`competing_count_capped`.

Reproduce any of it with `sentinel-ingest preflight <camera_id>`.

## The catalogue is not what the code first assumed

`GET /cameras.json` returns, per camera:

```json
{"id": "13", "location": "13 CN Vidhyalaya", "live": true,
 "codec": "h264", "width": 1920, "height": 1080, "fps": 25,
 "rtsp_url": "...", "webrtc_url": "...", "hls_live_url": "..."}
```

Three consequences:

- **There are no coordinates.** `location` is a place name, not a position.
  Since `cameras.location` is `NOT NULL`, and every distance, speed check
  and coverage calculation needs it, positions come from a separate file —
  `db/seed/camera_coordinates.json`, extracted from the camera-grid page.
  Of the 30 cameras, **6 positions are marked `ok`, 13 `approx`, 10 `guess`,
  1 missing.** A guessed position makes its route distances and speed checks
  unreliable, so it reduces the camera's provisional `trust_level`. Fixing
  those coordinates is worth more than any model work.
- **The endpoint needs a session.** It 302s to `/auth/login`. Set
  `SENTINEL_SENTINEL_COOKIE` or `SENTINEL_SENTINEL_TOKEN`. A sync that gets
  HTML now fails loudly rather than reporting zero cameras.
- **Mixed everything**: h264 and hevc, 1280x720 through 2560x1440, and 19 of
  30 declare no codec at all. No fixed-shape inference batch will work.

## Two hosts, two credentials

The grid does not serve everything from one place, and confusing the two is
the fastest way to a stream that never opens.

| | Endpoint | Authenticates with |
|---|---|---|
| HLS | `https://cctv.corp8.cloud/<id>/index.m3u8` | the session (cookie or token) |
| RTSP | `rtsp://<email>:<password>@103.250.160.189:8554/stream/<id>` | credentials in the URL |
| WHEP | `http://<email>:<password>@103.250.160.189:8889/stream/<id>/whep` | credentials in the URL |

A CDN terminates HTTP, so it can carry HLS and cannot carry RTSP's
interleaved TCP or WebRTC's UDP. Those come off the gateway's own address,
and every connection authenticates with the registered email and access
password embedded in the URL:

```bash
SENTINEL_GRID_EMAIL=you@example.com     # unencoded; the '@' is encoded for you
SENTINEL_GRID_PASSWORD=...
SENTINEL_GRID_MEDIA_HOST=103.250.160.189
```

Three things follow, all in `sentinel/core/streamurl.py`:

- **The `@` in the email is percent-encoded** — `you%40example.com`. Unencoded
  it does not error; it ends the userinfo early and points the connection at
  a hostname of `example.com`.
- **The credentialed URL is built in `open()` and nowhere else.** A `CameraRef`
  is persisted to the `adapters` table and served over the API, so it carries
  a bare URL. `decode.py` and `preflight` redact before logging or printing —
  a preflight report gets pasted into support mail.
- **Catalogue URLs are retargeted onto the media host.** The grid's own entries
  are written for a browser and may still name the CDN, which resolves and
  then carries nothing.

Where 8554/TCP is blocked, `SENTINEL_GRID_PREFER_HLS=1` decodes the CDN's HLS
instead — the guide's own fallback, at the cost of latency and a segment of
buffering. The session cookie goes to FFmpeg as a `Cookie` header.

## Notes on the sandbox feeds

The integration guide's checklist is implemented in `ingest/decode.py` and
`ingest/ptsclock.py`. The parts worth knowing:

- **RTSP over TCP is forced** everywhere, and the adapter loader *rejects* an
  RTSP adapter that does not force it.
- **The declared frame rate is never read.** `CAP_PROP_FPS` appears once, as a
  last-resort divisor when a backend reports no PTS at all for the first few
  frames after a join. Every timestamp comes from presentation timestamps.
- **`PtsClock`** anchors PTS to one wall-clock reading and derives every
  `seen_at` from the delta. The GOP replayed faster than real time on connect
  therefore produces correct intervals, not impossible velocities.
- **The loop point is a first-class event.** A backwards or large forward PTS
  jump flushes every track, discards the open traffic bucket and re-anchors
  the clock. Track ids do not survive a cut — carrying them across would link
  two unrelated vehicles, and that link would become a route leg.
- **Reconnect backs off** 2s → 30s with jitter, so an estate does not retry in
  lockstep after a gateway restart.
- **Decoder errors at join are counted, not fatal.** `Error constructing the
  frame RPS` until the first IDR is normal on H.265.
- **ByteTrack integrates over PTS deltas**, not frame counts.

Check a feed:

```bash
uv run sentinel-ingest probe CAM-1 --seconds 20
```

Prints the *measured* frame rate, reconnects, decode errors and scene cuts.
Restart a feed while it runs — that is the checklist item.

---

## Things the platform will not do

These are deliberate and enforced, not missing.

- **No challans.** Violations are reported, never fined. Automatic fining
  needs an identified owner; §3.1 says plates are not readable on most of
  this estate. Fining by appearance means fining the wrong person at scale,
  with an audit trail proving it.
- **No person tracking.** A rider row is a box on a vehicle: no embedding, no
  identity, no watchlist, and it cascade-deletes with the sighting. There is
  no path by which a person box becomes a persistent record.
- **No attribute confidences.** A captioning model can say how likely the word
  "white" was; it cannot say the probability that the car is white. Those are
  different quantities and only one belongs in a police record. Uncertainty is
  carried by the VAHAN population count, the camera's measured trust level and
  the competing-route count — all measured, none of them a model's opinion of
  its own output.
- **No single answer.** Searches return every physically consistent route,
  ranked, with the competing count visible. If enumeration hits the cap the
  count is marked as a floor rather than passed off as a total.
- **No claiming a capability that was not measured.** A database trigger
  refuses a violation type the camera's survey does not permit. The pipeline
  does not pre-check it — a rejected insert is the design working.

---

## Development

```bash
make test      # 83 tests; DB-backed ones skip cleanly with no database
make lint      # ruff
```

The tests cover the parts that are easy to get subtly wrong: the PTS clock
across a loop cut, ByteTrack association and time integration, best-frame
scoring, confusion-weighted plate distance, the scoring properties the design
commits to (a short rare chain must beat a long common one), traffic coverage
arithmetic, and adapter loading including a deliberately broken adapter.

### Working without the sandbox

Drop `.mp4` files into `var/samples/` and the `sample-files` adapter picks
them up. A looping file produces the same hard scene cut the live grid does,
which makes it the honest way to test discontinuity handling.
