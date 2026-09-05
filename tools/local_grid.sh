#!/usr/bin/env bash
# A local RTSP grid, for testing the decode path without the sandbox.
#
# Serves looping H.264 and H.265 clips over RTSP on :8554, which is how every
# finding in the README's "What was measured against a live feed" section was
# produced. A looping clip reproduces the scene cut the real grid has.
#
#   ./tools/local_grid.sh start
#   uv run sentinel-ingest preflight CAM-A --url rtsp://127.0.0.1:8554/stream/CAM-A
#   ./tools/local_grid.sh stop
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${MEDIAMTX:-mediamtx}"
CONF="$HERE/var/mediamtx.yml"
LOCAL_VIDEO="${LOCAL_GRID_VIDEO:-$HERE/cctv052x2004080516x01638.mp4}"

case "${1:-start}" in
start)
  mkdir -p "$HERE/var/samples"
  if [ -f "$LOCAL_VIDEO" ]; then
    CAM_A_VIDEO="$LOCAL_VIDEO"
  else
    CAM_A_VIDEO="$HERE/var/samples/CAM-A.mp4"
    [ -f "$CAM_A_VIDEO" ] || \
      python3 "$HERE/tools/make_test_video.py" "$CAM_A_VIDEO" 7
  fi
  [ -f "$HERE/var/samples/CAM-B.mp4" ] || \
    python3 "$HERE/tools/make_test_video.py" "$HERE/var/samples/CAM-B.mp4" 11

  command -v "$BIN" >/dev/null || {
    echo "mediamtx not found. Get it from" \
         "https://github.com/bluenviron/mediamtx/releases and put it on PATH," \
         "or set MEDIAMTX=/path/to/mediamtx" >&2; exit 1; }

  cat > "$CONF" <<EOF
logLevel: warn
rtspAddress: :8554
rtpAddress: :18000
rtcpAddress: :18001
multicastRTPPort: 18002
multicastRTCPPort: 18003
rtmp: no
hls: no
webrtc: no
srt: no
api: no
metrics: no
pathDefaults:
  runOnInitRestart: yes
paths:
  stream/CAM-A:
    runOnInit: >
      ffmpeg -hide_banner -loglevel error -re -stream_loop -1
      -i $CAM_A_VIDEO
      -c:v libx264 -preset ultrafast -tune zerolatency -g 15 -pix_fmt yuv420p -an
      -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/stream/CAM-A
  stream/CAM-B:
    runOnInit: >
      ffmpeg -hide_banner -loglevel error -re -stream_loop -1
      -i $HERE/var/samples/CAM-B.mp4
      -c:v libx265 -preset ultrafast -x265-params log-level=none -g 15 -pix_fmt yuv420p -an
      -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/stream/CAM-B
EOF
  # Refuse to start on top of an existing grid. Without this the checks below
  # pass on the OTHER instance's port while this one dies -- which is exactly
  # how this script once reported "grid up" with nothing running.
  if nc -z 127.0.0.1 8554 2>/dev/null; then
    echo "something is already listening on :8554 -- run '$0 stop' first" >&2
    exit 1
  fi

  # nohup alone, no setsid: setsid does not exist on macOS.
  nohup "$BIN" "$CONF" > "$HERE/var/mediamtx.log" 2>&1 < /dev/null &
  MTX_PID=$!

  # Wait for the port. Liveness is checked too, not just the port.
  ready=""
  for _ in $(seq 1 40); do
    if ! kill -0 "$MTX_PID" 2>/dev/null; then break; fi
    if nc -z 127.0.0.1 8554 2>/dev/null; then ready=1; break; fi
    sleep 0.5
  done

  if ! kill -0 "$MTX_PID" 2>/dev/null; then
    echo "mediamtx exited. Log follows:" >&2
    sed 's/^/  /' "$HERE/var/mediamtx.log" >&2
    exit 1
  fi
  if [ -z "$ready" ]; then
    echo "mediamtx is running but never opened :8554. Log follows:" >&2
    sed 's/^/  /' "$HERE/var/mediamtx.log" >&2
    exit 1
  fi

  # ffmpeg needs a moment to publish into the paths. Re-check afterwards: a
  # launch doomed by a port conflict has not necessarily died yet above.
  sleep 3
  if ! kill -0 "$MTX_PID" 2>/dev/null; then
    echo "mediamtx exited during start-up. Log follows:" >&2
    sed 's/^/  /' "$HERE/var/mediamtx.log" >&2
    exit 1
  fi

  echo "grid up:  rtsp://127.0.0.1:8554/stream/CAM-A   (h264)"
  echo "          rtsp://127.0.0.1:8554/stream/CAM-B   (h265)"
  echo
  echo "check it:  ffprobe -rtsp_transport tcp rtsp://127.0.0.1:8554/stream/CAM-A"
  ;;
stop)
  pkill -f "mediamtx.*mediamtx.yml" || true
  pkill -f "ffmpeg.*stream_loop" || true
  echo "grid down"
  ;;
*) echo "usage: $0 {start|stop}" >&2; exit 2 ;;
esac
