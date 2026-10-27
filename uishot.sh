#!/usr/bin/env bash
# Launch the public Narwhal 3D workspace in a real window and record its UI contract.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="${ROOT}/tools/blender-patched/blender"
[ -x "$RUNTIME" ] || RUNTIME="${ROOT}/tools/blender-5.2.2-linux-x64/blender"
BOOTSTRAP="${ROOT}/launch/bootstrap.py"
REPORTER="${ROOT}/spike/public_ui_report.py"
TIMEOUT_SECONDS=90

export NARWHAL3D_NO_DIALOGS=1
export NARWHAL3D_DEFER_FIRST_PRESENT=1
export NARWHAL3D_STATUSBAR_HEIGHT=37
unset NARWHAL3D_UI_READY NARWHAL3D_PRESENT_WATCHDOG
PURE_CORE="${ROOT}/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so"
if [[ -z "${NARWHAL_PURE_CORE:-}" && -f "$PURE_CORE" ]]; then
  export NARWHAL_PURE_CORE="$PURE_CORE"
fi

if [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  echo "SKIP: no display is available; the public UI report requires a real window."
  exit 0
fi

if [[ ! -x "$RUNTIME" ]]; then
  echo "error: Narwhal runtime not found at $RUNTIME" >&2
  exit 1
fi

mkdir -p "${ROOT}/artifacts"
LOG="${TMPDIR:-/tmp}/narwhal3d_uishot.$$.log"
LOCK="${TMPDIR:-/tmp}/narwhal3d_gui.lock"
PREFS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/narwhal3d-shot-prefs.XXXXXX")"
export NARWHAL3D_SETTINGS_FILE="$PREFS_DIR/preferences.json"
trap 'rm -f "$LOG"; rm -rf "$PREFS_DIR"' EXIT

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock 9
fi

timeout --signal=TERM "$TIMEOUT_SECONDS" \
  "$RUNTIME" --factory-startup \
  --python "$BOOTSTRAP" \
  --python "$REPORTER" \
  >"$LOG" 2>&1
status=$?

if [[ $status -ne 0 ]]; then
  echo "✘ Narwhal UI run exited with status $status. Log tail:"
  tail -n 25 "$LOG"
  exit 1
fi

if ! grep -q '^PUBLIC UI REPORT DONE$' "$LOG"; then
  echo "✘ Narwhal UI run did not print its completion marker. Log tail:"
  tail -n 25 "$LOG"
  exit 1
fi

tally="$(grep -E '^[0-9]+/[0-9]+ checks passed$' "$LOG" | tail -n 1)"
if [[ -z "$tally" ]]; then
  echo "✘ Narwhal UI report did not print an N/N tally."
  tail -n 25 "$LOG"
  exit 1
fi

passed="${tally%%/*}"
rest="${tally#*/}"
total="${rest%% *}"
if [[ "$passed" != "$total" ]]; then
  echo "✘ $tally"
  grep '^FAIL ' "$LOG" || true
  exit 1
fi

cat "${ROOT}/artifacts/ui_report.txt"
echo "PUBLIC UI REPORT DONE"
echo "✔ $tally"
echo "wrote artifacts/ui_report.txt and artifacts/ui.png"
