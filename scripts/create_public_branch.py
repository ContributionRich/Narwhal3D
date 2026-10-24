#!/usr/bin/env python3
"""Create the single-root Narwhal 3D public branch from an explicit allowlist.

The private development history is never made a parent. Files are staged through a
throwaway Git index, audited by blob, and only then published as a local branch ref.
Nothing is pushed by this script.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "narwhal3d-public"
ROOT_FILES = (
    ".gitignore", "LICENSE", "README.md", "CONTRIBUTING.md", "STATUS.md",
    "COMPATIBILITY.md", "ROADMAP.md", "CHANGELOG.md", "SECURITY.md", "SUPPORT.md", "GOVERNANCE.md",
    "CODE_OF_CONDUCT.md", "THIRD_PARTY_NOTICES",
    "docs/readme-command-loft.gif", "docs/images/bugreport.png",
    "docs/gaps.md", "docs/github-project.md", "docs/windows-build.md", "docs/macos-build.md",
    "narwhal3d.sh", "run_tests.sh", "uishot.sh", "Build-Narwhal3D-macOS.command",
    "pyrightconfig.json",
)
LAUNCH_FILES = (
    "launch/bootstrap.py", "launch/make_icon.py", "launch/open_arg.py",
    "launch/narwhal3d.desktop.in", "launch/narwhal3d-3dm.xml",
    "launch/narwhal3d-project.xml", "launch/narwhal3d.png",
)
WELCOME_FILES = (
    "native/welcome/.gitignore", "native/welcome/Cargo.lock", "native/welcome/Cargo.toml",
    "native/welcome/src/main.rs", "native/welcome/src/native_stacking.rs",
)
PATCH_FILES = (
    "blender_patches/README.md", "blender_patches/app_drawn.patch",
    "blender_patches/apply_app_drawn.py",
    "blender_patches/cad_boundary_wires.patch",
    "blender_patches/apply_cad_boundary_wires.py",
    "blender_patches/apply_deferred_first_present.py",
    "blender_patches/apply_narwhal3d_branding.py",
    "blender_patches/narwhal3d_app_icon.svg",
)
SCRIPT_FILES = (
    "scripts/fetch_locked.py", "scripts/bootstrap_dev_linux.sh",
    "scripts/build_blender_linux.sh", "scripts/release_policy.py", "scripts/assemble_linux_release.py",
    "scripts/assemble_windows_release.py", "scripts/verify_linux_release.py", "scripts/verify_windows_release.py", "scripts/audit_public_tree.py",
    "scripts/create_public_branch.py", "scripts/capture_readme_demo.py",
    "scripts/build_readme_demo.sh", "scripts/build_toolbar_icons.py",
    "scripts/bootstrap_linux_runtime.py", "scripts/stage_fedora_inkscape.py",
    "scripts/collect_windows_build_info.ps1", "scripts/build_windows_preview.ps1",
    "scripts/build_brep_writer_source.sh", "scripts/verify_brep_writer.py",
    "scripts/build_document_core.py", "scripts/verify_document_core.py",
    "scripts/check_pure_core.py", "scripts/build_macos_source.sh",
    "scripts/build_selection_core.py", "scripts/verify_selection_core.py",
)
TEST_FILES = (
    "spike/audit_representation.py", "spike/_roundtrip_reader.py",
    "spike/test_geometry.py", "spike/test_local_brep_move.py",
    "spike/test_kernel.py", "spike/test_primitives.py",
    "spike/test_picture.py", "spike/test_cmdbar_hits.py",
    "spike/test_extrude_distance.py", "spike/test_extrude_options.py",
    "spike/test_curve_options.py", "spike/test_helix.py",
    "spike/test_spiral.py", "spike/test_coil_preview.py",
    "spike/test_boolean.py", "spike/test_boolean_extrusion.py",
    "spike/test_tessellate.py", "spike/test_trim.py", "spike/test_trim_drag.py",
    "spike/test_sweep.py",
    "spike/test_picture_display.py", "spike/test_gumball_region_extrude.py",
    "spike/test_bug102_live.py", "spike/test_local_move_scale.py",
    "spike/test_bug103_curved_move.py", "spike/test_bug104_subobject_move.py",
    "spike/test_bug105.py", "spike/test_bug106.py",
    "spike/test_document_clipboard.py", "spike/test_control_point_osnap.py",
    "spike/ui_smoke.py", "spike/addon_e2e.py", "spike/test_app_drawn.py",
    "spike/branding_test.py", "spike/test_deferred_present_patch.py",
    "spike/test_welcome.py", "spike/test_toolbars.py",
    "spike/test_menubar.py", "spike/test_menubar_resource.py",
    "spike/test_rightpanel.py", "spike/test_aliases.py",
    "spike/test_file_commands.py", "spike/test_sessionlog.py",
    "spike/test_bugreport.py",
    "spike/test_import.py", "spike/test_export_cmd.py",
    "spike/test_print_cmd.py", "spike/test_printdialog.py",
    "spike/test_dxf_reader.py", "spike/test_dxf_writer.py", "spike/test_native_dxf.py",
    "spike/test_obj_reader.py", "spike/test_obj_writer.py", "spike/test_native_obj.py",
    "spike/test_stl_writer.py", "spike/test_native_stl.py",
    "spike/test_numparse.py", "spike/test_native_numparse.py",
    "spike/test_calc.py", "spike/test_native_calc.py",
    "spike/test_bearing.py", "spike/test_native_bearing.py",
    "spike/test_coords.py", "spike/test_native_coords.py",
    "spike/test_macro.py", "spike/test_native_macro.py",
    "spike/test_macro_plan.py", "spike/test_native_macro_plan.py",
    "spike/test_svgpath.py", "spike/test_native_svgpath.py",
    "spike/test_curve_through_polyline.py", "spike/test_conic.py",
    "spike/test_parabola.py", "spike/test_parabola3pt.py",
    "spike/test_hyperbola.py", "spike/test_catenary.py",
    "spike/test_curve_through_pt.py", "spike/test_tween_curves.py",
    "spike/test_handlecurve.py", "spike/test_curve_through_srf_control_pt.py",
    "spike/test_sequential_object_steps.py",
    "spike/test_pure_core_gate.py", "spike/test_vector_export.py",
    "spike/test_native_joinchain.py", "spike/test_native_vector_export.py",
    "spike/test_native_command_search.py", "spike/test_native_coil.py",
    "spike/test_native_coil_preview.py",
    "spike/test_native_interpolate.py", "spike/test_native_tween.py",
    "spike/test_tracedraw.py", "spike/test_cdr.py", "spike/test_native_cdr.py",
    "spike/test_embedded_cdr.py",
    "spike/test_converter_supervisor.py", "spike/test_runtime_tools.py",
    "spike/test_onbrep_public.py", "spike/test_open_arg.py",
    "spike/test_windows_collector.py", "spike/test_windows_release.py",
    "spike/test_linux_release.py", "spike/test_package_completeness.py", "spike/test_release_policy.py", "spike/test_macos_source.py",
    "spike/test_rust_selection_core.py", "spike/test_native_selection.py",
    "spike/test_selection_core_packaging.py",
    "spike/test_native_geometry.py", "spike/test_native_platform.py",
    "spike/test_native_gridsnap.py",
    "spike/test_public_project.py", "spike/public_ui_report.py",
    "spike/test_ai_assistant.py", "spike/test_ai_transport.py",
    "spike/test_narwhal_document.py", "spike/test_document_core_artifacts.py",
)
NATIVE_FILES = (
    "native/cdr_core/.gitignore", "native/cdr_core/Cargo.lock",
    "native/cdr_core/Cargo.toml", "native/cdr_core/src/lib.rs",
    "native/cdr_core/src/reader.rs",
    "native/converter_supervisor/.gitignore", "native/converter_supervisor/Cargo.lock",
    "native/converter_supervisor/Cargo.toml", "native/converter_supervisor/src/main.rs",
    "native/cdr_multiversion/.gitignore", "native/cdr_multiversion/Cargo.lock",
    "native/cdr_multiversion/Cargo.toml",
    "native/cdr_multiversion/src/main.rs",
    "native/brep_writer/main.cpp", "native/brep_writer/README.md",
    "native/document_core/.cargo/config.toml", "native/document_core/.gitignore",
    "native/document_core/Cargo.lock", "native/document_core/Cargo.toml",
    "native/document_core/rust-toolchain.toml", "native/document_core/README.md",
    "native/document_core/src/container.rs", "native/document_core/src/lib.rs",
    "native/document_core/src/main.rs", "native/document_core/src/sha256.rs",
    "native/document_core/tests/document_core.rs",
    "native/pure_core/.cargo/config.toml", "native/pure_core/.gitignore",
    "native/pure_core/Cargo.lock", "native/pure_core/Cargo.toml",
    "native/pure_core/rust-toolchain.toml", "native/pure_core/README.md",
    "native/pure_core/src/lib.rs", "native/pure_core/src/stl.rs",
    "native/pure_core/src/obj.rs", "native/pure_core/src/dxf.rs",
    "native/pure_core/src/numparse.rs", "native/pure_core/src/calc.rs",
    "native/pure_core/src/bearing.rs", "native/pure_core/src/coords.rs",
    "native/pure_core/src/macro_parse.rs", "native/pure_core/src/macro_plan.rs",
    "native/pure_core/src/svgpath.rs", "native/pure_core/src/joinchain.rs",
    "native/pure_core/src/vector_export.rs", "native/pure_core/src/command_search.rs",
    "native/pure_core/src/coil.rs", "native/pure_core/src/interpolate.rs",
    "native/pure_core/src/tween.rs", "native/pure_core/src/gridsnap.rs",
    "native/geometry_core/.gitignore", "native/geometry_core/Cargo.lock",
    "native/geometry_core/Cargo.toml", "native/geometry_core/README.md",
    "native/geometry_core/build.rs", "native/geometry_core/cpp/occt_adapter.cpp",
    "native/geometry_core/include/narwhal_geometry_core.h",
    "native/geometry_core/src/lib.rs",
    "native/platform_core/.gitignore", "native/platform_core/Cargo.lock",
    "native/platform_core/Cargo.toml", "native/platform_core/README.md",
    "native/platform_core/include/narwhal_platform_core.h",
    "native/platform_core/src/lib.rs",
    "native/selection_core/.cargo/config.toml", "native/selection_core/.gitignore",
    "native/selection_core/Cargo.lock", "native/selection_core/Cargo.toml",
    "native/selection_core/rust-toolchain.toml", "native/selection_core/README.md",
    "native/selection_core/src/lib.rs",
)
RUNTIME_FILES = ("runtime/README.md", "runtime/manifest.json")
PACKAGE_EXCLUDES = {"narwhal3d/kernel/brep_writer.cpp"}


class PublicBranchError(RuntimeError):
    """The public root cannot be created safely."""


def run(args: list[str], *, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            args, cwd=ROOT, env=env, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "").strip()
        raise PublicBranchError(f"command failed: {' '.join(args)}{': ' + detail if detail else ''}") from exc
    return result.stdout.strip()


def recursive_files(directory: str) -> list[str]:
    root = ROOT / directory
    if not root.is_dir() or root.is_symlink():
        raise PublicBranchError(f"missing safe directory: {directory}")
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PublicBranchError(f"symlink is not allowed in public source: {path.relative_to(ROOT)}")
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        if "__pycache__" in path.parts or "target" in path.parts \
                or path.suffix in {".pyc", ".pyo"}:
            continue
        if relative in PACKAGE_EXCLUDES:
            continue
        if relative.startswith("narwhal3d/kernel/bin/"):
            continue
        files.append(relative)
    return files


def public_files() -> list[str]:
    paths = set(ROOT_FILES + LAUNCH_FILES + WELCOME_FILES + NATIVE_FILES + RUNTIME_FILES
                + PATCH_FILES + SCRIPT_FILES + TEST_FILES)
    for directory in ("narwhal3d", "packaging", "licenses", ".github", "artwork"):
        paths.update(recursive_files(directory))
    for directory in ("native/vendor/tracedraw",):
        paths.update(recursive_files(directory))
    missing = sorted(path for path in paths if not (ROOT / path).is_file())
    if missing:
        raise PublicBranchError("allowlisted files are missing: " + ", ".join(missing))
    return sorted(paths)


def commit_root(paths: list[str], message: str, timestamp: int) -> str:
    if timestamp < 0:
        raise PublicBranchError("timestamp must be non-negative")
    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{BRANCH}"],
        cwd=ROOT,
    ).returncode == 0
    if exists:
        raise PublicBranchError(f"branch already exists: {BRANCH}")
    with tempfile.TemporaryDirectory(prefix="narwhal3d-public-index-") as temporary:
        index = str(Path(temporary) / "index")
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = index
        run(["git", "read-tree", "--empty"], env=env)
        # Explicit paths only. `-f` allows committed generated icons while the source
        # checkout keeps downloaded runtimes ignored.
        run(["git", "add", "-f", "--", *paths], env=env)
        tree = run(["git", "write-tree"], env=env)
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "Narwhal 3D Contributors",
        "GIT_AUTHOR_EMAIL": "dev@localhost",
        "GIT_COMMITTER_NAME": "Narwhal 3D Contributors",
        "GIT_COMMITTER_EMAIL": "dev@localhost",
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
    })
    commit = run(["git", "commit-tree", tree, "-m", message], env=env)
    # Audit the unattached commit before creating the public ref.
    run([
        sys.executable, "scripts/audit_public_tree.py", "--root", str(ROOT),
        "--git-ref", commit, "--require-root-commit",
    ])
    run(["git", "update-ref", f"refs/heads/{BRANCH}", commit, "0" * 40])
    return commit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timestamp", type=int, required=True, help="root commit Unix timestamp")
    parser.add_argument("--message", default="Initial public release of Narwhal 3D")
    parser.add_argument("--list", action="store_true", help="print the allowlist without committing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = public_files()
    if args.list:
        print("\n".join(paths))
        return 0
    commit = commit_root(paths, args.message, args.timestamp)
    print(f"created {BRANCH} at {commit} with {len(paths)} files")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublicBranchError as exc:
        raise SystemExit(f"PUBLIC BRANCH ERROR: {exc}") from exc
