#!/usr/bin/env bash
# Run ONE test suite with the environment ./run_tests.sh gives it.
#
#   scripts/run_suite.sh spike/test_visibility.py [suite arguments...]
#
# Suites that build a document need the application core, history core and the
# exact-geometry runtime on their paths; run with a bare interpreter they fail with
# "application-core library not installed". This script exports the same variables
# as run_tests.sh, from the components scripts/setup_dev_native.sh staged, and runs
# the suite the way run_tests.sh does: headless Blender for the suites run_tests.sh
# lists in BLENDER_SUITES, the runtime's Python for everything else.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ $# -ge 1 ]] || { echo "usage: scripts/run_suite.sh spike/<suite>.py [args...]" >&2; exit 2; }
SUITE=$1
shift
[[ -f "$ROOT/$SUITE" ]] || { echo "error: no such suite: $SUITE" >&2; exit 2; }

# The same runtime choice as run_tests.sh: the patched runtime, else the official one.
RUNTIME_ROOT="$ROOT/tools/blender-patched"
if [[ ! -x "$RUNTIME_ROOT/blender" || ! -x "$RUNTIME_ROOT/5.2/python/bin/python3.13" ]]; then
    RUNTIME_ROOT="$ROOT/tools/blender-5.2.2-linux-x64"
fi
BLENDER="$RUNTIME_ROOT/blender"
PYTHON="$RUNTIME_ROOT/5.2/python/bin/python3.13"
[[ -x "$BLENDER" && -x "$PYTHON" ]] || {
    echo "error: no runtime; run scripts/bootstrap_dev_linux.sh and scripts/setup_dev_native.sh" >&2
    exit 2
}

DEV_BREP_WRITER="$ROOT/artifacts/brep-writer/linux-x86_64/brep_writer-linux-x86_64"
if [[ -z "${NARWHAL3D_BREP_WRITER:-}" && -x "$DEV_BREP_WRITER" ]]; then
    export NARWHAL3D_BREP_WRITER="$DEV_BREP_WRITER"
fi
ICON_TEST_PYTHON="${XDG_CACHE_HOME:-$HOME/.cache}/narwhal3d/icon-test-venv/bin/python"
if [[ -z "${NARWHAL3D_TEST_PYTHON:-}" && -x "$ICON_TEST_PYTHON" ]]; then
    export NARWHAL3D_TEST_PYTHON="$ICON_TEST_PYTHON"
fi
export NARWHAL_RUNTIME_ROOT="$RUNTIME_ROOT"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT/vendor/occt${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="$RUNTIME_ROOT/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export NARWHAL_PURE_CORE="${NARWHAL_PURE_CORE:-$ROOT/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so}"
export NARWHAL_GEOMETRY_CORE="${NARWHAL_GEOMETRY_CORE:-$ROOT/native/geometry_core/target/release/libnarwhal_geometry_core.so}"
export NARWHAL_APPLICATION_CORE="${NARWHAL_APPLICATION_CORE:-$ROOT/artifacts/application-core/linux-x86_64/libnarwhal_application_core.so}"
export NARWHAL_HISTORY_CORE="${NARWHAL_HISTORY_CORE:-$NARWHAL_APPLICATION_CORE}"
# The CDR import helpers, when this checkout has built them (run_tests.sh builds them).
for pair in "NARWHAL_CDR_CORE=native/cdr_core/target/release/libnarwhal_cdr_core.so" \
            "NARWHAL_CDR_MULTIVERSION=native/cdr_multiversion/target/release/narwhal-cdr-multiversion" \
            "NARWHAL_CONVERTER_SUPERVISOR=native/converter_supervisor/target/release/narwhal-converter-supervisor"; do
    name=${pair%%=*}
    if [[ -z "${!name:-}" && -e "$ROOT/${pair#*=}" ]]; then
        export "$name=$ROOT/${pair#*=}"
    fi
done
UI_CORE_LIBRARY="$ROOT/artifacts/ui-core/linux-x86_64/libnarwhal_ui_core.so"
if [[ -z "${NARWHAL_UI_CORE_LIBRARY:-}" && -f "$UI_CORE_LIBRARY" ]]; then
    export NARWHAL_UI_CORE_LIBRARY="$UI_CORE_LIBRARY"
fi

cd "$ROOT"
in_blender_suites() {
    sed -n '/^BLENDER_SUITES=(/,/^)/p' run_tests.sh | grep -qx "    $1"
}
if in_blender_suites "$SUITE"; then
    exec "$BLENDER" -b --factory-startup --python-exit-code 1 --python "$SUITE" -- "$@"
fi
exec "$PYTHON" "$SUITE" "$@"
