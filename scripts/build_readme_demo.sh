#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${ROOT}/tools/blender-patched/blender"
DEMO="${ROOT}/artifacts/readme-demo"
OUTPUT="${ROOT}/docs/readme-command-loft.gif"
APP_LOG="${TMPDIR:-/tmp}/narwhal3d_readme_demo.$$.log"
RECORDER_LOG="${TMPDIR:-/tmp}/narwhal3d_readme_recorder.$$.log"
READY="${DEMO}/ready"
RECORDING="${DEMO}/recording"
CAPTURE="${DEMO}/capture"
DONE="${DEMO}/done"
APP_PID=""
RECORDER_PID=""

cleanup() {
  if [[ -n "$RECORDER_PID" ]]; then kill -TERM "$RECORDER_PID" 2>/dev/null || true; fi
  if [[ -n "$APP_PID" ]]; then kill -TERM "$APP_PID" 2>/dev/null || true; fi
  rm -f "$APP_LOG" "$RECORDER_LOG"
}
trap cleanup EXIT

[[ -x "$RUNTIME" ]] || { echo "error: missing native runtime: $RUNTIME" >&2; exit 2; }
command -v spectacle >/dev/null || { echo "error: Spectacle is required for Wayland pointer capture" >&2; exit 2; }
command -v ffmpeg >/dev/null || { echo "error: ffmpeg is required for GIF conversion" >&2; exit 2; }
[[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] || { echo "error: a display is required" >&2; exit 2; }

rm -rf "$DEMO"
mkdir -p "$DEMO" "$(dirname "$OUTPUT")"
export NARWHAL3D_NO_DIALOGS=1

"$RUNTIME" --enable-event-simulate --factory-startup \
  --python "${ROOT}/launch/bootstrap.py" \
  --python "${ROOT}/scripts/capture_readme_demo.py" \
  >"$APP_LOG" 2>&1 &
APP_PID=$!

for _attempt in $(seq 1 300); do
  [[ -f "$READY" ]] && break
  kill -0 "$APP_PID" 2>/dev/null || { tail -n 40 "$APP_LOG" >&2; exit 1; }
  sleep 0.1
done
if [[ ! -f "$READY" ]]; then
  echo "error: demo UI did not become ready" >&2
  tail -n 40 "$APP_LOG" >&2
  exit 1
fi

(
  frame=0
  while [[ ! -f "$CAPTURE" ]]; do
    kill -0 "$APP_PID" 2>/dev/null || exit 1
    sleep 0.05
  done
  while kill -0 "$APP_PID" 2>/dev/null && [[ ! -f "$DONE" ]]; do
    printf -v path '%s/frame-%05d.png' "$DEMO" "$frame"
    spectacle --activewindow --background --nonotify --pointer --output "$path" \
      >>"$RECORDER_LOG" 2>&1 || true
    frame=$((frame + 1))
    sleep 0.08
  done
) &
RECORDER_PID=$!
sleep 0.4
touch "$RECORDING"

set +e
wait "$APP_PID"
APP_STATUS=$?
set -e
APP_PID=""
wait "$RECORDER_PID" || true
RECORDER_PID=""

if [[ $APP_STATUS -ne 0 ]] || ! grep -q '^README DEMO CAPTURE DONE$' "$APP_LOG"; then
  echo "error: demo interaction did not complete" >&2
  tail -n 40 "$APP_LOG" >&2
  exit 1
fi
FRAME_COUNT=$(find "$DEMO" -maxdepth 1 -type f -name 'frame-*.png' | wc -l)
if [[ $FRAME_COUNT -lt 20 ]]; then
  echo "error: only $FRAME_COUNT pointer frames were captured" >&2
  tail -n 40 "$RECORDER_LOG" >&2
  exit 1
fi

ffmpeg -y -hide_banner -loglevel warning -framerate 2 \
  -pattern_type glob -i "$DEMO/frame-*.png" \
  -filter_complex "scale=1200:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=3" \
  -loop 0 "$OUTPUT"

printf 'captured %s pointer frames\n' "$FRAME_COUNT"
printf 'wrote %s\n' "$OUTPUT"
