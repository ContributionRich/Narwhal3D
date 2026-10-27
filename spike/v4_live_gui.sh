#!/usr/bin/env bash
# Current shell interaction and rendering checks, serialized with other GUI runs.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  echo 'SKIP: v4 live UI requires a display'
  exit 0
fi
cd "$ROOT"
export NARWHAL3D_DEFER_FIRST_PRESENT=1 NARWHAL3D_STATUSBAR_HEIGHT=37
export NARWHAL3D_NO_DIALOGS=1 PYTHONDONTWRITEBYTECODE=1
unset NARWHAL3D_UI_READY NARWHAL3D_PRESENT_WATCHDOG NARWHAL3D_THEME NARWHAL3D_WELCOME_REQUEST || true
export PYTHONPATH="$ROOT/vendor/occt${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="$ROOT/tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export NARWHAL_PURE_CORE="${NARWHAL_PURE_CORE:-$ROOT/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so}"
export NARWHAL_GEOMETRY_CORE="${NARWHAL_GEOMETRY_CORE:-$ROOT/native/geometry_core/target/release/libnarwhal_geometry_core.so}"
export NARWHAL_SELECTION_CORE="${NARWHAL_SELECTION_CORE:-$ROOT/artifacts/selection-core/linux-x86_64/libnarwhal_selection_core.so}"
export NARWHAL_PLATFORM_CORE="${NARWHAL_PLATFORM_CORE:-$ROOT/native/platform_core/target/release/libnarwhal_platform_core.so}"
export NARWHAL_DOCUMENT_CORE="${NARWHAL_DOCUMENT_CORE:-$ROOT/native/document_core/target/release/narwhal-document-core}"
export NARWHAL_CDR_CORE="${NARWHAL_CDR_CORE:-$ROOT/native/cdr_core/target/release/libnarwhal_cdr_core.so}"
export NARWHAL_CDR_MULTIVERSION="${NARWHAL_CDR_MULTIVERSION:-$ROOT/native/cdr_multiversion/target/release/narwhal-cdr-multiversion}"
export NARWHAL_CONVERTER_SUPERVISOR="${NARWHAL_CONVERTER_SUPERVISOR:-$ROOT/native/converter_supervisor/target/release/narwhal-converter-supervisor}"
LOG="${TMPDIR:-/tmp}/narwhal3d_v4_live.$$.log"
PREFS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/narwhal3d-gui-prefs.XXXXXX")"
export NARWHAL3D_SETTINGS_FILE="$PREFS_DIR/preferences.json"
trap 'rm -f "$LOG"; rm -rf "$PREFS_DIR"' EXIT
exec 9>"${TMPDIR:-/tmp}/narwhal3d_gui.lock"
flock 9

run_gui() {
  local suite=$1 marker=$2 order=$3 status=0
  local bootstrap="$ROOT/launch/bootstrap.py"
  local scripts=(--python "$bootstrap" --python "$ROOT/$suite")
  if [[ "$order" == before-bootstrap ]]; then
    scripts=(--python "$ROOT/$suite" --python "$bootstrap")
  fi
  timeout 200 "$ROOT/tools/blender-patched/blender" --enable-event-simulate --factory-startup \
    --python-exit-code 1 "${scripts[@]}" >"$LOG" 2>&1 || status=$?
  # Preferences includes phase before status in its JSON; do not require status to be first.
  if [[ $status -ne 0 ]] || ! grep -q "^${marker} {.*\"status\": \"passed\"" "$LOG" \
      || grep -qE 'Traceback|draw failed|panel failed|viewport setup .*failed' "$LOG"; then
    cat "$LOG"
    echo "FAIL: $suite${NARWHAL_PREF_TEST_PHASE:+ ($NARWHAL_PREF_TEST_PHASE)} (exit $status)" >&2
    return 1
  fi
  grep -E '^(V4|SHELL_FIXES|RENDER_QUALITY|COMPONENT_FILLET) PASS' "$LOG" || true
  echo "PASS: $suite${NARWHAL_PREF_TEST_PHASE:+ ($NARWHAL_PREF_TEST_PHASE)}"
}

unset NARWHAL_PREF_TEST_PHASE || true
run_gui spike/test_v4_live.py V4_LIVE_RESULT after-bootstrap
rm -f "$NARWHAL3D_SETTINGS_FILE"
run_gui spike/test_shell_fixes_gui.py SHELL_FIXES_RESULT after-bootstrap
rm -f "$NARWHAL3D_SETTINGS_FILE"
run_gui spike/test_render_quality_gui.py RENDER_QUALITY_RESULT after-bootstrap
rm -f "$NARWHAL3D_SETTINGS_FILE"
run_gui spike/test_component_fillet_gui.py COMPONENT_FILLET_RESULT before-bootstrap
rm -f "$NARWHAL3D_SETTINGS_FILE"
# These two launches intentionally share the write phase's disposable preferences.
export NARWHAL_PREF_TEST_PHASE=write
run_gui spike/test_preferences_gui.py PREFERENCES_GUI_RESULT before-bootstrap
export NARWHAL_PREF_TEST_PHASE=read
run_gui spike/test_preferences_gui.py PREFERENCES_GUI_RESULT before-bootstrap
unset NARWHAL_PREF_TEST_PHASE
rm -f "$NARWHAL3D_SETTINGS_FILE"
run_gui spike/test_window_overlay_gui.py WINDOW_OVERLAY_RESULT before-bootstrap
