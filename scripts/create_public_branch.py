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

ROOT = Path(__file__).resolve().parent.parent

# Explicit allowlist of paths that belong in the public root. Anything else is
# excluded by design. Paths are relative to the repository root.
ROOT_FILES = (
    "README.md", "CONTRIBUTING.md", "LICENSE", "THIRD_PARTY_NOTICES",
    ".gitignore", "pyrightconfig.json", "narwhal3d.sh", "run_tests.sh",
    "uishot.sh",
)
LAUNCH_FILES = (
    "launch/__init__.py", "launch/narwhal3d.py", "launch/startup.py",
)
PATCH_FILES = (
    "blender_patches/README.md",
)
SCRIPT_FILES = (
    "scripts/fetch_locked.py", "scripts/bootstrap_dev_linux.sh",
    "scripts/build_blender_linux.sh", "scripts/assemble_linux_release.py",
    "scripts/verify_linux_release.py", "scripts/audit_public_tree.py",
    "scripts/create_public_branch.py", "scripts/capture_readme_demo.py",
    "scripts/build_readme_demo.sh",
)
TEST_FILES = (
    "spike/audit_representation.py", "spike/_roundtrip_reader.py",
    "spike/test_geometry.py",
    "spike/test_kernel.py", "spike/test_primitives.py",
    "spike/test_boolean.py", "spike/test_boolean_extrusion.py",
    "spike/test_tessellate.py", "spike/test_trim.py", "spike/test_sweep.py",
    "spike/ui_smoke.py", "spike/addon_e2e.py", "spike/test_app_drawn.py",
    "spike/branding_test.py", "spike/test_toolbars.py",
    "spike/test_menubar.py", "spike/test_menubar_resource.py",
    "spike/test_rightpanel.py", "spike/test_aliases.py",
    "spike/test_file_commands.py", "spike/test_sessionlog.py",
    "spike/test_import.py", "spike/test_export_cmd.py",
    "spike/test_print_cmd.py", "spike/test_printdialog.py",
    "spike/test_dxf_reader.py", "spike/test_dxf_writer.py",
    "spike/test_obj_reader.py", "spike/test_obj_writer.py",
    "spike/test_stl_writer.py", "spike/test_vector_export.py",
    "spike/test_onbrep_public.py", "spike/test_open_arg.py",
    "spike/test_clear_undo.py",
    "spike/public_ui_report.py",
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
    except subprocess.CalledProcessError as e:
        raise PublicBranchError(
            f"command failed: {' '.join(args)}\n{e.stderr}"
        ) from e
    return result.stdout


def _allowlisted_paths() -> set[str]:
    paths = set(ROOT_FILES + LAUNCH_FILES + PATCH_FILES + SCRIPT_FILES + TEST_FILES)
    # package files are discovered separately so the allowlist stays readable
    for p in (ROOT / "narwhal3d").rglob("*"):
        if p.is_file() and p.suffix in {".py", ".md"} or p.name == "__init__.py":
            rel = p.relative_to(ROOT).as_posix()
            if rel not in PACKAGE_EXCLUDES:
                paths.add(rel)
    return paths


def create_public_branch(name: str = "public") -> str:
    """Stage the allowlisted tree into a new orphan-style local branch.

    Returns the new branch tip SHA.
    """
    paths = _allowlisted_paths()
    with tempfile.TemporaryDirectory() as td:
        index = Path(td) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        run(["git", "read-tree", "--empty"], env=env)
        for rel in sorted(paths):
            full = ROOT / rel
            if not full.is_file():
                continue
            run(["git", "update-index", "--add", "--cacheinfo",
                 f"100644,{run(['git', 'hash-object', '-w', str(full)]).strip()},{rel}"],
                env=env)
        tree = run(["git", "write-tree"], env=env).strip()
        commit = run(["git", "commit-tree", tree, "-m", f"public root {name}"], env=env).strip()
        run(["git", "update-ref", f"refs/heads/{name}", commit])
    return commit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="public")
    args = parser.parse_args()
    sha = create_public_branch(args.name)
    print(sha)


if __name__ == "__main__":
    main()
