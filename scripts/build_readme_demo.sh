#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${ROOT}/tools/blender-patched/blender"
FRAMES="${ROOT}/artifacts/readme-demo"
OUTPUT="${ROOT}/docs/readme-command-loft.gif"
LOG="${TMPDIR:-/tmp}/narwhal3d_readme_demo.$$.log"

[[ -x "$RUNTIME" ]] || { echo "error: missing native runtime: $RUNTIME" >&2; exit 2; }
command -v convert >/dev/null || { echo "error: ImageMagick 'convert' is required" >&2; exit 2; }
[[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] || { echo "error: a display is required" >&2; exit 2; }

rm -rf "$FRAMES"
mkdir -p "$FRAMES" "$(dirname "$OUTPUT")"
trap 'rm -f "$LOG"' EXIT
export NARWHAL3D_NO_DIALOGS=1

timeout --signal=TERM 90 \
  "$RUNTIME" --factory-startup \
  --python "${ROOT}/launch/bootstrap.py" \
  --python "${ROOT}/scripts/capture_readme_demo.py" \
  >"$LOG" 2>&1

grep -q '^README DEMO CAPTURE DONE$' "$LOG" || {
  echo "error: demo capture did not complete" >&2
  tail -n 30 "$LOG" >&2
  exit 1
}

convert \
  -delay 90 "$FRAMES/00-two-curves.png" \
  -delay 70 "$FRAMES/01-type-loft.png" \
  -delay 90 "$FRAMES/02-loft-first-prompt.png" \
  -delay 90 "$FRAMES/03-first-curve-picked.png" \
  -delay 120 "$FRAMES/04-loft-created.png" \
  -delay 180 "$FRAMES/05-loft-result-hold.png" \
  -resize '1200x>' -layers Optimize -loop 0 "$OUTPUT"

printf 'wrote %s\n' "$OUTPUT"
