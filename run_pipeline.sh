#!/usr/bin/env bash
#
# One command, from video files to annotated video and cross-camera correlations.
#
#   ./run_pipeline.sh a.mp4 b.mp4 c.mp4
#
# The platform is built for cameras, which never end. This drives the same code
# over files, which do: ingest one pass, drain the crop pipelines, drain
# correlation, correlate the cameras against each other, then draw the result
# back onto the video. Nothing is left running afterwards.
#
# See PIPELINE.md for what each stage does and where your own weights go.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# -- defaults ----------------------------------------------------------------
OUT_ROOT="out"
MANIFEST=""
FPS=""
MAX_SEEDS=50
FETCH_MODELS=1
KEEP_SIGHTINGS=0
VIDEOS=()

usage() {
  cat <<'USAGE'
usage: ./run_pipeline.sh [options] VIDEO [VIDEO...]

  --out DIR           where runs are written              (default: ./out)
  --manifest FILE     JSON giving each video a camera_id, name, lat, lon, start_at
  --fps N             analysis frame rate                 (default: SENTINEL_TARGET_DECODE_FPS)
  --max-seeds N       sightings to correlate from         (default: 50)
  --no-fetch-models   fail rather than download a detector when none is present
  --keep-sightings    add to previous runs instead of replacing them
  -h, --help          this

Manifest format:
  {"videos": [{"path": "a.mp4", "camera_id": "cam01", "name": "Paldi Circle",
               "lat": 23.0121, "lon": 72.5583, "start_at": "2026-09-07T09:00:00Z"}]}
Every field is optional; anything absent falls back to the synthetic default.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)             OUT_ROOT="$2"; shift 2 ;;
    --manifest)        MANIFEST="$2"; shift 2 ;;
    --fps)             FPS="$2"; shift 2 ;;
    --max-seeds)       MAX_SEEDS="$2"; shift 2 ;;
    --no-fetch-models) FETCH_MODELS=0; shift ;;
    --keep-sightings)  KEEP_SIGHTINGS=1; shift ;;
    -h|--help)         usage; exit 0 ;;
    -*)                echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *)                 VIDEOS+=("$1"); shift ;;
  esac
done

[[ ${#VIDEOS[@]} -gt 0 ]] || { echo "no input videos given" >&2; usage >&2; exit 2; }

# -- output ------------------------------------------------------------------
RUN_ID="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$OUT_ROOT/$RUN_ID"
mkdir -p "$RUN_DIR/videos" "$RUN_DIR/tracks"
LOG="$RUN_DIR/run.log"

# Colour only when a human is looking.
if [[ -t 1 ]]; then B=$'\033[1m'; RED=$'\033[31m'; YEL=$'\033[33m'; N=$'\033[0m'
else B=""; RED=""; YEL=""; N=""; fi

step()  { echo "${B}==> $*${N}"; echo "==> $*" >>"$LOG"; }
info()  { echo "    $*"; echo "    $*" >>"$LOG"; }
warn()  { echo "${YEL}    ! $*${N}"; echo "    ! $*" >>"$LOG"; }
die()   { echo "${RED}    x $*${N}" >&2; echo "    x $*" >>"$LOG"; exit 1; }

# Resolve the interpreter once. A .venv is the normal case; uv covers a
# checkout that has not been synced, and `python3 -m uv` covers a machine
# where uv is installed as a module rather than on PATH (the Makefile makes
# the same allowance).
if [[ -x .venv/bin/python ]]; then
  PY=(.venv/bin/python)
  RUNNER=()
elif command -v uv >/dev/null 2>&1; then
  PY=(uv run python)
  RUNNER=(uv run)
elif python3 -m uv --version >/dev/null 2>&1; then
  PY=(python3 -m uv run python)
  RUNNER=(python3 -m uv run)
else
  echo "no .venv and no uv; run 'make install' first" >&2; exit 1
fi
bin() {  # bin sentinel-ingest ... -> the console script, venv or uv
  local name="$1"; shift
  if [[ -x ".venv/bin/$name" ]]; then ".venv/bin/$name" "$@"
  else "${RUNNER[@]}" "$name" "$@"; fi
}

# ============================================================================
step "0/7  preflight"
# ============================================================================
[[ -f .env ]] || die ".env is missing. Copy .env.example and set SENTINEL_DATABASE_URL."
command -v ffmpeg >/dev/null 2>&1 \
  || warn "ffmpeg not on PATH: annotated video stays mp4v and may not play in a browser"

for v in "${VIDEOS[@]}"; do
  [[ -f "$v" ]] || die "no such file: $v"
done
# Two files with the same stem become one camera id, and sightings is
# UNIQUE (camera_id, track_id) -- the second file's tracks would be swallowed
# by ON CONFLICT DO NOTHING. Checked here for a fast, clear failure; `resolve`
# checks it again for anything that calls it directly. Skipped with a
# manifest, which may well map two same-named files to different cameras.
if [[ -z "$MANIFEST" ]] && \
   [[ $(printf '%s\n' "${VIDEOS[@]}" | xargs -n1 basename | sed 's/\.[^.]*$//' | sort -u | wc -l) \
      -ne ${#VIDEOS[@]} ]]; then
  die "two inputs share a filename stem; rename one, or map them in --manifest"
fi

"${PY[@]}" - <<'PYEOF' >>"$LOG" 2>&1 || die "cannot reach the database (see $LOG). Is SENTINEL_DATABASE_URL right?"
import asyncio, os, sys
from dotenv import load_dotenv
load_dotenv(".env")
import asyncpg

async def main():
    url = os.environ.get("SENTINEL_DATABASE_URL")
    if not url:
        print("SENTINEL_DATABASE_URL is not set"); sys.exit(1)
    conn = await asyncio.wait_for(asyncpg.connect(url), 30)
    try:
        await conn.fetchval("SELECT count(*) FROM schema_migrations")
    finally:
        await conn.close()
asyncio.run(main())
PYEOF
info "database reachable and migrated"
info "run directory: $RUN_DIR"

# ============================================================================
step "1/7  models"
# ============================================================================
DETECT_PATH="${SENTINEL_DETECT_MODEL_PATH:-./var/models/yolo.onnx}"

if command -v uv >/dev/null 2>&1; then UVX=(uv)
elif python3 -m uv --version >/dev/null 2>&1; then UVX=(python3 -m uv)
else UVX=(); fi

# uv sync makes the environment match EXACTLY what is asked for, so the extras
# have to be named together -- syncing 'models' alone silently uninstalls
# pytest and ruff.
sync_extras() {
  [[ ${#UVX[@]} -gt 0 ]] || return 1
  local args=(sync --extra dev --extra models)
  for extra in "$@"; do args+=(--extra "$extra"); done
  # torch is well over a gigabyte and the default timeout does not survive a
  # slow connection. One retry, because a timeout is usually transient.
  UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}" "${UVX[@]}" "${args[@]}" >>"$LOG" 2>&1 \
    || UV_HTTP_TIMEOUT=600 "${UVX[@]}" "${args[@]}" >>"$LOG" 2>&1
}

if ! "${PY[@]}" -c "import onnxruntime" >/dev/null 2>&1; then
  info "installing onnxruntime (the inference runtime)"
  sync_extras || die "could not install the 'models' extra (see $LOG)"
fi

if [[ ! -f "$DETECT_PATH" ]]; then
  if [[ $FETCH_MODELS -eq 1 ]]; then
    warn "no detector at $DETECT_PATH"
    warn "fetching YOLOv8n and exporting it — first run only, and it pulls torch (~2 GB)"
    mkdir -p "$(dirname "$DETECT_PATH")"
    sync_extras export || die "could not install the 'export' extra (see $LOG).
    If the download timed out, re-run. To skip it entirely, export a detector
    yourself and put it at $DETECT_PATH -- see PIPELINE.md §3."
    # The same export as the Dockerfile's models stage, so a container and a
    # laptop run identical weights.
    ( cd "$(dirname "$DETECT_PATH")" \
      && "$HERE/.venv/bin/yolo" export model=yolov8n.pt format=onnx opset=12 imgsz=640 \
    ) >>"$LOG" 2>&1 || die "yolo export failed (see $LOG)"
    mv "$(dirname "$DETECT_PATH")/yolov8n.onnx" "$DETECT_PATH"
    info "detector exported to $DETECT_PATH"
    REGISTER_DETECTOR=1
  else
    die "no detector at $DETECT_PATH and --no-fetch-models was given.
    Without it every video produces zero sightings. See PIPELINE.md §3."
  fi
else
  info "detector: $DETECT_PATH"
fi

MODELS_DIR="$(dirname "$DETECT_PATH")"

# model_versions records which weights produced each row, and the annotated
# video reads it to decide whether to print the STUB banner. Leaving a real
# detector registered as '0.0.0-stub' banners a run that is not stubbed; the
# opposite mistake puts a placeholder in front of a room unmarked. Keep it
# truthful both ways. Idempotent on the weights' sha256, so this runs every
# time and inserts only when the file has actually changed.
"${PY[@]}" - "$DETECT_PATH" >>"$LOG" 2>&1 <<'PYEOF' || warn "could not register the detector in model_versions"
import asyncio, hashlib, os, sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env")
import asyncpg


async def main():
    path = Path(sys.argv[1])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    conn = await asyncpg.connect(os.environ["SENTINEL_DATABASE_URL"])
    try:
        await conn.execute(
            """
            INSERT INTO model_versions
                (role, name, version, weights_sha256, runtime, notes)
            SELECT 'detect', $1, '1.0.0', $2, 'onnx-cpu', $3
             WHERE NOT EXISTS (
               SELECT 1 FROM model_versions
                WHERE role = 'detect' AND weights_sha256 = $2)
            """,
            path.stem, digest, f"registered by run_pipeline.sh from {path}",
        )
    finally:
        await conn.close()


asyncio.run(main())
PYEOF

if compgen -G "$MODELS_DIR/*plate*.pt" >/dev/null 2>&1; then
  info "plate weights present — real ANPR (needs the 'anpr' extra: uv sync --extra anpr)"
else
  warn "no plate weights in $MODELS_DIR: plates stay stubbed (1 crop in 8, marked)."
  warn "drop license-plate-finetune-v1m.pt there and re-run. See PIPELINE.md §3."
fi

# ============================================================================
step "2/7  migrations"
# ============================================================================
"${PY[@]}" scripts/migrate.py >>"$LOG" 2>&1 || die "migrations failed (see $LOG)"
info "schema up to date"

# ============================================================================
step "3/7  ingest — decode, detect, track, write sightings"
# ============================================================================
OFFLINE_ARGS=(offline "${VIDEOS[@]}" --out "$RUN_DIR")
if [[ -n "$MANIFEST" ]]; then OFFLINE_ARGS+=(--manifest "$MANIFEST"); fi
if [[ $KEEP_SIGHTINGS -eq 1 ]]; then OFFLINE_ARGS+=(--keep-sightings); fi
# Exported rather than passed as a flag: both passes must agree on the rate or
# the sidecar join goes sparse, and the annotate pass reads the same setting.
if [[ -n "$FPS" ]]; then export SENTINEL_TARGET_DECODE_FPS="$FPS"; fi

# Stage output goes to the log, not to a .json: configure_logging writes its
# lines to stdout, so capturing stdout as JSON captures the log with it. Every
# command below writes its own machine-readable file instead.
bin sentinel-ingest "${OFFLINE_ARGS[@]}" >>"$LOG" 2>&1 \
  || die "ingest failed (see $LOG)"

# One id per line, because a manifest may name a camera with a space in it.
# `mapfile` would be the obvious tool and is bash 4+; macOS ships bash 3.2, so
# this reads the lines the portable way instead.
CAMERAS=()
while IFS= read -r cam; do
  [[ -n "$cam" ]] && CAMERAS+=("$cam")
done < <("${PY[@]}" -c "
import json
for v in json.load(open('$RUN_DIR/run.json'))['videos']:
    print(v['camera_id'])
")
TOTAL_SIGHTINGS=$("${PY[@]}" -c "
import json
print(sum(v['sightings'] for v in json.load(open('$RUN_DIR/run.json'))['videos']))
")
info "cameras: ${CAMERAS[*]}"
info "sightings written: $TOTAL_SIGHTINGS"
if [[ "$TOTAL_SIGHTINGS" -eq 0 ]]; then
  warn "no sightings — nothing to describe, read or correlate."
  warn "the detector found no vehicles. Check the footage, or SENTINEL_DETECT_CONF."
fi

# ============================================================================
step "4/7  pipelines — describe, embed, plate, violate"
# ============================================================================
bin sentinel-pipeline run --all --drain >>"$LOG" 2>&1 || die "pipelines failed (see $LOG)"
info "crop queues drained"

# ============================================================================
step "5/7  correlation"
# ============================================================================
bin sentinel-correlation run --drain >>"$LOG" 2>&1 || die "correlation failed (see $LOG)"
bin sentinel-correlation crosscam \
  --cameras "${CAMERAS[@]}" \
  --out "$RUN_DIR/correlations.json" \
  --html "$RUN_DIR/report.html" \
  --max-seeds "$MAX_SEEDS" >>"$LOG" 2>&1 \
  || die "cross-camera correlation failed (see $LOG)"
MATCHES=$("${PY[@]}" -c "
import json; print(len(json.load(open('$RUN_DIR/correlations.json'))['matches']))
")
info "cross-camera matches: $MATCHES"

# ============================================================================
step "6/7  annotate"
# ============================================================================
bin sentinel-ingest annotate "${VIDEOS[@]}" \
  --tracks "$RUN_DIR/tracks" \
  --out "$RUN_DIR/videos" \
  --correlations "$RUN_DIR/correlations.json" \
  >>"$LOG" 2>&1 \
  || die "annotation failed (see $LOG)"

# ============================================================================
step "7/7  summary"
# ============================================================================
"${PY[@]}" - "$RUN_DIR" <<'PYEOF'
import json, sys
from pathlib import Path

run = Path(sys.argv[1])
ingest = json.loads((run / "run.json").read_text())
corr = json.loads((run / "correlations.json").read_text())
ann = {a["camera_id"]: a for a in json.loads((run / "videos" / "annotate.json").read_text())}

note = next((a["stub_note"] for a in ann.values() if a.get("stub_note")), None)
per_match = {}
for match in corr["matches"]:
    for s in match["sightings"]:
        per_match.setdefault(s["camera_id"], set()).add(match["match_id"])

print()
print(f"  {'camera':<24} {'sightings':>9} {'matches':>8}  video")
print(f"  {'-'*24} {'-'*9} {'-'*8}  {'-'*40}")
for video in ingest["videos"]:
    cam = video["camera_id"]
    out = ann.get(cam, {}).get("output", "-")
    print(f"  {cam:<24} {video['sightings']:>9} "
          f"{len(per_match.get(cam, ())):>8}  {out}")
print()
print(f"  matches   {run / 'correlations.json'}")
print(f"  report    {run / 'report.html'}")
print(f"  log       {run / 'run.log'}")
if note:
    print()
    print(f"  ! {note}")
    print("    Those outputs are placeholders, not measurements. PIPELINE.md §3 "
          "says where the real weights go.")
if any(v["synthetic_position"] for v in ingest["videos"]):
    print()
    print("  ! Camera positions are synthetic. Every distance, elapsed time and")
    print("    required speed in the report is therefore made up. Pass --manifest")
    print("    with real lat/lon and start_at to make them mean something.")
print()
PYEOF
