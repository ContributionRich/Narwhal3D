#!/usr/bin/env bash
# Launch the workspace the way the native welcome window does (forced onto X11, deferred
# first presentation) and assert the startup layout: four viewports, command bar, docks.
#
# The welcome route is what the desktop icon runs. On a HiDPI Wayland desktop it opens a
# physical-pixel window at a much larger interface scale than a direct launch, which is
# where the "viewplanes everywhere" startup defect lived.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${ROOT}/tools/blender-patched/blender"
[ -x "$RUNTIME" ] || RUNTIME="${ROOT}/tools/blender-5.2.2-linux-x64/blender"

if [[ -z "${DISPLAY:-}" ]]; then
  echo "SKIP: no X11 display; the welcome's X11 launch route cannot be exercised."
  exit 0
fi

export NARWHAL3D_NO_DIALOGS=1
export NARWHAL3D_DEFER_FIRST_PRESENT=1
unset NARWHAL3D_UI_READY NARWHAL3D_PRESENT_WATCHDOG || true
# The same deliberately missing socket the welcome window sets (native_stacking.rs).
export WAYLAND_DISPLAY="narwhal3d-force-x11-$$"
OCCT_LIBS="${ROOT}/tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs"
[ -d "$OCCT_LIBS" ] && export LD_LIBRARY_PATH="$OCCT_LIBS"
PURE_CORE="${ROOT}/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so"
if [[ -z "${NARWHAL_PURE_CORE:-}" && -f "$PURE_CORE" ]]; then
  export NARWHAL_PURE_CORE="$PURE_CORE"
fi

LOG="${TMPDIR:-/tmp}/narwhal3d_startup_layout.$$.log"
LOCK="${TMPDIR:-/tmp}/narwhal3d_gui.lock"
trap 'rm -f "$LOG"' EXIT
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock 9
fi

timeout --signal=TERM 90 "$RUNTIME" --factory-startup \
  --python "${ROOT}/launch/bootstrap.py" \
  --python "${ROOT}/spike/test_startup_layout.py" >"$LOG" 2>&1
status=$?

grep -E '^(--|  (ok|FAIL|NOTE)|     |[0-9]+/[0-9]+ checks|FAILED|\[narwhal3d\] (layout|viewport setup|top strip|left band|right band|interface scale))' "$LOG" || true
if [[ $status -ne 0 ]] || ! grep -q '^STARTUP LAYOUT DONE$' "$LOG"; then
  echo "✘ startup layout run failed (status $status). Log tail:"
  tail -n 25 "$LOG"
  exit 1
fi
echo "✔ startup layout through the welcome's X11 route"
