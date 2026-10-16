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
    "docs/gaps.md", "docs/github-project.md", "docs/windows-build.md",
    "narwhal3d.sh", "run_tests.sh", "uishot.sh", "pyrightconfig.json",
)
LAUNCH_FILES = (
    "launch/bootstrap.py", "launch/make_icon.py", "launch/open_arg.py",
    "launch/narwhal3d.desktop.in", "launch/narwhal3d-3dm.xml",
    "launch/narwhal3d.png",
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
    "scripts/verify_linux_release.py", "scripts/verify_windows_release.py", "scripts/audit_public_tree.py",
    "scripts/create_public_branch.py", "scripts/capture_readme_demo.py",
    "scripts/build_readme_demo.sh", "scripts/build_toolbar_icons.py",
    "scripts/collect_windows_build_info.ps1", "scripts/build_windows_preview.ps1",
)
TEST_FILES = (
    "spike/audit_representation.py", "spike/_roundtrip_reader.py",
    "spike/test_geometry.py",
    "spike/test_kernel.py", "spike/test_primitives.py",
    "spike/test_picture.py", "spike/test_cmdbar_hits.py",
    "spike/test_extrude_distance.py", "spike/test_extrude_options.py",
    "spike/test_boolean.py", "spike/test_boolean_extrusion.py",
    "spike/test_tessellate.py", "spike/test_trim.py", "spike/test_trim_drag.py",
    "spike/test_sweep.py",
    "spike/test_picture_display.py", "spike/test_gumball_region_extrude.py",
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
    "spike/test_dxf_reader.py", "spike/test_dxf_writer.py",
    "spike/test_obj_reader.py", "spike/test_obj_writer.py",
    "spike/test_stl_writer.py", "spike/test_vector_export.py",
    "spike/test_tracedraw.py", "spike/test_onbrep_public.py", "spike/test_open_arg.py",
    "spike/test_windows_collector.py", "spike/test_windows_release.py",
    "spike/test_release_policy.py", "spike/test_public_project.py", "spike/public_ui_report.py",
)
PACKAGE_EXCLUDES = {
    "narwhal3d/cdr_reader.py",
    "narwhal3d/cdr_writer.py",
    "narwhal3d/kernel/brep_writer.cpp",
}


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
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if relative in PACKAGE_EXCLUDES:
            continue
        if relative.startswith("narwhal3d/kernel/bin/"):
            continue
        files.append(relative)
    return files


def public_files() -> list[str]:
    paths = set(ROOT_FILES + LAUNCH_FILES + WELCOME_FILES + PATCH_FILES + SCRIPT_FILES + TEST_FILES)
    for directory in ("narwhal3d", "packaging", "licenses", ".github", "artwork"):
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
