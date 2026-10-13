#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BLENDER="$ROOT/tools/blender-patched/blender"
PYTHON="$ROOT/tools/blender-patched/5.2/python/bin/python3.13"

[[ -x "$BLENDER" ]] || { echo "error: missing executable $BLENDER" >&2; exit 2; }
[[ -x "$PYTHON" ]] || { echo "error: missing executable $PYTHON" >&2; exit 2; }

cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1

find narwhal3d spike scripts -type d -name __pycache__ -prune -exec rm -rf {} +

run() {
    local label=$1
    shift
    printf '\n==> %s\n' "$label"
    if "$@"; then
        printf 'PASS: %s\n' "$label"
    else
        local status=$?
        printf 'FAIL: %s (exit %d)\n' "$label" "$status" >&2
        return "$status"
    fi
}

run "packaging: fetch lock" "$PYTHON" scripts/fetch_locked.py --self-check
run "packaging: assemble release" "$PYTHON" scripts/assemble_linux_release.py --self-check
run "packaging: verify release" "$PYTHON" scripts/verify_linux_release.py --self-check
run "packaging: public-tree audit" "$PYTHON" scripts/audit_public_tree.py --self-check
run "public project organization" "$PYTHON" spike/test_public_project.py
run "packaging: bootstrap" scripts/bootstrap_dev_linux.sh --self-check
run "packaging: Blender build" scripts/build_blender_linux.sh --self-check

PURE_SUITES=(
    spike/test_geometry.py
    spike/test_kernel.py
    spike/test_primitives.py
    spike/test_picture.py
    spike/test_cmdbar_hits.py
    spike/test_extrude_distance.py
    spike/test_extrude_options.py
    spike/test_boolean_extrusion.py
    spike/branding_test.py
    spike/test_toolbars.py
    spike/test_menubar.py
    spike/test_menubar_resource.py
    spike/test_rightpanel.py
    spike/test_aliases.py
    spike/test_document_clipboard.py
    spike/test_sessionlog.py
    spike/test_bugreport.py
    spike/test_open_arg.py
    spike/test_printdialog.py
    spike/test_dxf_reader.py
    spike/test_dxf_writer.py
    spike/test_obj_reader.py
    spike/test_obj_writer.py
    spike/test_stl_writer.py
    spike/test_vector_export.py
    spike/test_tracedraw.py
    spike/test_windows_collector.py
    spike/test_windows_release.py
    spike/test_release_policy.py
    spike/test_onbrep_public.py
)

BLENDER_SUITES=(
    spike/audit_representation.py
    spike/test_boolean.py
    spike/test_tessellate.py
    spike/test_trim.py
    spike/test_picture.py
    spike/test_picture_display.py
    spike/test_gumball_region_extrude.py
    spike/test_control_point_osnap.py
    spike/test_sweep.py
    spike/ui_smoke.py
    spike/addon_e2e.py
    spike/test_app_drawn.py
    spike/test_file_commands.py
    spike/test_import.py
    spike/test_export_cmd.py
    spike/test_print_cmd.py
)

for suite in "${PURE_SUITES[@]}"; do
    run "$suite" "$PYTHON" "$suite"
done

for suite in "${BLENDER_SUITES[@]}"; do
    run "$suite" "$BLENDER" -b --factory-startup --python-exit-code 1 --python "$suite"
done

if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
    run "UI screenshot suite" ./uishot.sh
else
    printf '\nSKIP: UI screenshot suite (no display)\n'
fi

printf '\nAll public tests passed.\n'
