#!/usr/bin/env python3
"""Audit a Narwhal 3D source tree or Git ref before public release.

This is a technical provenance gate, not legal advice. It prevents known private paths,
legacy product assets, commercial extraction tools, unexpected images, and personal
identifiers from entering the public root commit.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    ".gitignore",
    "LICENSE",
    "README.md",
    "CONTRIBUTING.md",
    "STATUS.md",
    "COMPATIBILITY.md",
    "ROADMAP.md",
    "CHANGELOG.md",
    "SECURITY.md",
    "SUPPORT.md",
    "GOVERNANCE.md",
    "CODE_OF_CONDUCT.md",
    "THIRD_PARTY_NOTICES",
    "docs/readme-command-loft.gif",
    "docs/images/bugreport.png",
    "docs/gaps.md",
    "docs/github-project.md",
    "docs/windows-build.md",
    "docs/macos-build.md",
    "artwork/icons/README.md",
    "narwhal3d.sh",
    "run_tests.sh",
    "uishot.sh",
    "narwhal3d/commands.py",
    "scripts/create_public_branch.py",
    "scripts/collect_windows_build_info.ps1",
    "scripts/build_windows_preview.ps1",
    "scripts/build_brep_writer_source.sh",
    "scripts/verify_brep_writer.py",
    "scripts/build_document_core.py",
    "scripts/verify_document_core.py",
    "scripts/check_pure_core.py",
    "scripts/build_selection_core.py",
    "scripts/verify_selection_core.py",
    "scripts/build_macos_source.sh",
    "scripts/release_policy.py",
    "scripts/verify_windows_release.py",
    "spike/test_windows_collector.py",
    "spike/test_windows_release.py",
    "spike/test_release_policy.py",
    "spike/test_macos_source.py",
    "launch/narwhal3d.desktop.in",
    "launch/narwhal3d-3dm.xml",
    "launch/narwhal3d-project.xml",
    "launch/narwhal3d.png",
    "packaging/linux-x86_64.lock.json",
    "licenses/GPL-3.0-or-later.txt",
    "licenses/LGPL-2.1-or-later.txt",
    "licenses/OCCT-exception-1.0.txt",
    "licenses/OCP-Apache-2.0.txt",
    "licenses/AppImage-MIT.txt",
    "licenses/rhino3dm-MIT.txt",
    "native/brep_writer/main.cpp",
    "native/brep_writer/README.md",
    "native/document_core/Cargo.toml",
    "native/document_core/README.md",
    "native/document_core/src/main.rs",
    "native/document_core/tests/document_core.rs",
    "narwhal3d/native_document.py",
    "narwhal3d/narwhal_document.py",
    "narwhal3d/document_worker.py",
    "spike/test_narwhal_document.py",
    "spike/test_document_core_artifacts.py",
    "native/pure_core/Cargo.toml",
    "native/pure_core/README.md",
    "native/pure_core/src/lib.rs",
    "native/pure_core/src/stl.rs",
    "native/pure_core/src/obj.rs",
    "native/pure_core/src/dxf.rs",
    "native/pure_core/src/numparse.rs",
    "native/pure_core/src/calc.rs",
    "native/pure_core/src/bearing.rs",
    "native/pure_core/src/coords.rs",
    "native/pure_core/src/macro_parse.rs",
    "native/pure_core/src/macro_plan.rs",
    "native/pure_core/src/svgpath.rs",
    "native/pure_core/src/joinchain.rs",
    "native/pure_core/src/vector_export.rs",
    "native/pure_core/src/command_search.rs",
    "native/pure_core/src/coil.rs",
    "native/pure_core/src/interpolate.rs",
    "native/pure_core/src/tween.rs",
    "native/selection_core/Cargo.toml",
    "native/selection_core/src/lib.rs",
    "spike/test_pure_core_gate.py",
    "spike/test_native_stl.py",
    "spike/test_native_obj.py",
    "spike/test_native_dxf.py",
    "spike/test_native_joinchain.py",
    "spike/test_native_vector_export.py",
    "spike/test_native_command_search.py",
    "spike/test_native_coil.py",
    "spike/test_native_interpolate.py",
    "spike/test_native_tween.py",
    "spike/test_curve_options.py",
    "spike/test_helix.py",
    "spike/test_spiral.py",
    "spike/test_coil_preview.py",
    "spike/test_curve_through_polyline.py",
    "spike/test_conic.py",
    "spike/test_parabola.py",
    "spike/test_parabola3pt.py",
    "spike/test_hyperbola.py",
    "spike/test_catenary.py",
    "spike/test_curve_through_pt.py",
    "spike/test_tween_curves.py",
    "spike/test_rust_selection_core.py",
    "spike/test_native_selection.py",
    "spike/test_selection_core_packaging.py",
}
FORBIDDEN_EXACT = {
    "AGENTS" + ".md",
    "POLICY.md",
    "coco" + "cad.sh",
    "rhi" + "no" + "alt.sh",
    "narwhal3d/kernel/brep_writer.cpp",
    "launch/build_brep_writer.sh",
    "spike/ui_report.py",
}
FORBIDDEN_PREFIXES = (
    "bug_reports/",
    "docs/measured/",
    "ExamplePhotos/",
    "More" + "_Data/",
    "vendor/",
    "tools/",
    "artifacts/",
)
FORBIDDEN_GLOBS = (
    "spike/" + "rhi" + "no_*.py",
    "spike/gen_*.py",
    "spike/probe*.py",
    "spike/ui_probe*.py",
    "spike/measure_" + "rhi" + "no_*.py",
    "spike/docs_dump.txt",
)
ALLOWED_IMAGES = (
    "launch/narwhal3d.png",
    "docs/readme-command-loft.gif",
    "docs/images/bugreport.png",
    "artwork/icons/*.png",
    "narwhal3d/icons/narwhal-*.png",
    "blender_patches/narwhal3d_app_icon.svg",
    "packaging/windows/*.ico",
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ico", ".svg"}
LEGACY_PATH_PATTERN = re.compile(r"rhi" + r"no(?!3dm)", re.IGNORECASE)
LEGACY_TEXT_PATTERN = re.compile(rb"\brhi" + rb"no(?!3dm)")
LEGACY_TEXT_ALLOWED = {"COMPATIBILITY.md"}
# Assemble signatures from fragments so this policy file is subject to its own scan.
FORBIDDEN_TEXT = (
    b"/home/" + b"nathaniel",
    b"coco " + b"cad",
    b"coco" + b"cad",
    b"rhi" + b"no alternative",
    b"rhi" + b"noalternative",
    b"rhi" + b"nocore.dll",
    b"vendor/rhi" + b"no-help",
    b"owner" + b":",
    b"owner" + b"'s",
    b"owner-" + b"reported",
    b"owner bug" + b" log",
    b"agents" + b".md",
    b"docs/current" + b"_round.md",
    b"docs/kernel" + b".md",
    b"docs/geometry",
    b"coco" + b"ville",
    b"example" + b"photos/",
    b"more_" + b"data/",
    b"bug_" + b"reports/",
    b"rhi" + b"no_addon",
    b"rhi" + b"noalt_",
    b"rhi" + b"noalt-",
    b"whale orn cut" + b"test fix.cdr",
    b"complex setting" + b".3dm",
    b"flow ring" + b".3dm",
)
FORBIDDEN_PATTERNS = (
    re.compile(rb"\b(?:t|g|f|q)-\d+\b"),
    re.compile(rb"\bbug report\s*#\d+\b"),
    re.compile(rb"\bround\s+\d+\b"),
    re.compile(rb"\b(?:front" + rb"ier|lower tier|geometry agent|hand" + rb"off)\b"),
    re.compile(rb"spike/(?:probe|rhi" + rb"no_|gen_|measure_)"),
)
SECRET_MARKERS = (
    b"-----BEGIN PRIVATE " + b"KEY-----",
    b"gh" + b"p_",
    b"github_" + b"pat_",
    b"AK" + b"IA",
)
# These policy files necessarily name exclusions/signatures. They remain subject to path,
# symlink, Python-compilation, binary and secret checks.
PROVENANCE_SCAN_EXEMPT = {
    ".gitignore",
    "scripts/audit_public_tree.py",
    # Exact upstream source has a local Rust variable named `owner`; its bytes are
    # independently pinned by SOURCE_MANIFEST.json and spike/test_public_project.py.
    "native/vendor/tracedraw/core/src/document.rs",
}
REQUIRED_EXECUTABLES = {
    "narwhal3d.sh",
    "run_tests.sh",
    "uishot.sh",
    "scripts/bootstrap_dev_linux.sh",
    "scripts/build_blender_linux.sh",
    "scripts/build_macos_source.sh",
    "scripts/build_readme_demo.sh",
}
REQUIRED_CONTENT = {
    "narwhal3d/commands.py": (
        b"class Command:",
        b"def _build_registry",
        b"COMMANDS: dict[str, Command] = _build_registry()",
    ),
    "scripts/create_public_branch.py": (
        b"BRANCH = \"narwhal3d-public\"",
        b"for directory in (\"narwhal3d\", \"packaging\", \"licenses\", \".github\", \"artwork\")",
        b"--require-root-commit",
    ),
}


class AuditError(RuntimeError):
    """The requested tree could not be enumerated safely."""


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(args, cwd=root, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"").decode("utf-8", "replace").strip()
        raise AuditError(f"command failed: {' '.join(args)}{': ' + detail if detail else ''}") from exc


def git_entries(root: Path, ref: str) -> dict[str, tuple[str, bytes]]:
    """Return ``path -> (mode, blob)`` for every file in *ref*."""
    listing = _run(root, "git", "ls-tree", "-rz", ref).stdout
    entries: dict[str, tuple[str, bytes]] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, raw_path = record.split(b"\t", 1)
        mode, kind, _object = meta.decode("ascii").split()
        if kind != "blob":
            continue
        path = raw_path.decode("utf-8", "surrogateescape")
        blob = _run(root, "git", "show", f"{ref}:{path}").stdout
        entries[path] = (mode, blob)
    return entries


def filesystem_entries(root: Path) -> dict[str, tuple[str, bytes]]:
    """Return regular files and symlinks below *root*, excluding Git metadata."""
    entries: dict[str, tuple[str, bytes]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        if path.is_symlink():
            entries[rel] = ("120000", os.readlink(path).encode("utf-8", "surrogateescape"))
        elif path.is_file():
            entries[rel] = ("100755" if os.access(path, os.X_OK) else "100644", path.read_bytes())
    return entries


def path_problem(path: str) -> str | None:
    posix = PurePosixPath(path)
    if path.startswith("/") or ".." in posix.parts or "\\" in path:
        return "unsafe path spelling"
    if LEGACY_PATH_PATTERN.search(path):
        return "legacy product name in public path"
    if path in FORBIDDEN_EXACT:
        return "forbidden private or excluded component"
    if any(path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return "forbidden private directory"
    if any(fnmatch.fnmatchcase(path, pattern) for pattern in FORBIDDEN_GLOBS):
        return "commercial research/mining tool"
    if posix.suffix.lower() in IMAGE_SUFFIXES and not any(
        fnmatch.fnmatchcase(path, pattern) for pattern in ALLOWED_IMAGES
    ):
        return "unexpected image or artwork"
    return None


def audit_entries(entries: dict[str, tuple[str, bytes]], require_manifest: bool = True) -> list[str]:
    problems: list[str] = []
    paths = set(entries)
    if require_manifest:
        for missing in sorted(REQUIRED - paths):
            problems.append(f"missing required public file: {missing}")
    for path, (mode, blob) in sorted(entries.items()):
        problem = path_problem(path)
        if problem:
            problems.append(f"{path}: {problem}")
        if mode == "120000":
            problems.append(f"{path}: symlinks are not permitted in the public source root")
            continue
        if path in REQUIRED_EXECUTABLES and mode != "100755":
            problems.append(f"{path}: required launcher or test harness is not executable")
        for marker in REQUIRED_CONTENT.get(path, ()):
            if marker not in blob:
                problems.append(f"{path}: missing required structural marker {marker!r}")
        if PurePosixPath(path).suffix == ".py":
            try:
                compile(blob, path, "exec")
            except (SyntaxError, UnicodeError, ValueError) as exc:
                problems.append(f"{path}: Python source does not compile: {exc}")
        if b"\0" in blob:
            continue
        folded = blob.lower()
        if path not in PROVENANCE_SCAN_EXEMPT:
            for marker in FORBIDDEN_TEXT:
                if marker in folded:
                    problems.append(
                        f"{path}: contains forbidden private/legacy marker {marker!r}")
            for pattern in FORBIDDEN_PATTERNS:
                match = pattern.search(folded)
                if match:
                    problems.append(
                        f"{path}: contains forbidden development marker {match.group(0)!r}")
            if path not in LEGACY_TEXT_ALLOWED:
                match = LEGACY_TEXT_PATTERN.search(folded)
                if match:
                    problems.append(
                        f"{path}: contains forbidden legacy product marker {match.group(0)!r}")
        for marker in SECRET_MARKERS:
            if marker.lower() in folded:
                problems.append(f"{path}: contains possible secret marker {marker!r}")
    return problems


def require_root_commit(root: Path, ref: str) -> str | None:
    line = _run(root, "git", "rev-list", "--parents", "-n", "1", ref).stdout.decode().strip()
    fields = line.split()
    if len(fields) != 1:
        return f"{ref} is not a root commit: {line}"
    return None


def self_check() -> None:
    safe = {path: ("100644", b"safe\n") for path in REQUIRED}
    for path in REQUIRED_EXECUTABLES:
        safe[path] = ("100755", b"#!/bin/sh\nexit 0\n")
    safe["narwhal3d/commands.py"] = (
        "100644",
        b"class Command:\n    pass\n\ndef _build_registry():\n    return {}\n\n"
        b"COMMANDS: dict[str, Command] = _build_registry()\n",
    )
    safe["scripts/create_public_branch.py"] = (
        "100644",
        b"BRANCH = \"narwhal3d-public\"\n"
        b"for directory in (\"narwhal3d\", \"packaging\", \"licenses\", \".github\", \"artwork\"):\n"
        b"    pass\nROOT_GATE = \"--require-root-commit\"\n",
    )
    safe["narwhal3d/icons/narwhal-box.png"] = ("100644", b"\x89PNG\r\n\x1a\n\0")
    safe["narwhal3d/cdr_reader.py"] = ("100644", b"def read_cdr(path):\n    return path\n")
    safe["narwhal3d/cdr_writer.py"] = ("100644", b"def write_cdr(path):\n    return path\n")
    safe["spike/test_cdr.py"] = ("100644", b"print('public CDR test')\n")
    safe["dependency-name.txt"] = ("100644", b"rhi" + b"no3dm is required\n")
    safe["COMPATIBILITY.md"] = (
        "100644", b"Target: Rhi" + b"noceros 3D\nhttps://www.rhi" + b"no3d.com/\n")
    if audit_entries(safe):
        raise AuditError("self-check rejected a safe synthetic manifest")
    cases = (
        ("private path", {"bug_" + "reports/report.md": ("100644", b"x")}),
        ("legacy product", {"notes.txt": ("100644", b"Coco " + b"CAD")}),
        ("personal path", {"notes.txt": ("100644", b"/home/" + b"nathaniel/model.3dm")}),
        ("private attribution", {"notes.py": ("100644", b"# " + b"Owner's words\n")}),
        ("private document", {"notes.py": ("100644", b"# See docs/" + b"KERNEL.md\n")}),
        ("task identifier", {"notes.py": ("100644", b"# " + b"G-112 implementation\n")}),
        ("numbered report", {"notes.py": ("100644", b"# bug report " + b"#74\n")}),
        ("handoff lore", {"notes.py": ("100644", b"# frontier hand" + b"off\n")}),
        ("old namespace", {"notes.py": ("100644", b"import rhi" + b"no_addon\n")}),
        ("legacy product word", {"notes.py": ("100644", b"# rhi" + b"no behavior\n")}),
        ("truncated command registry",
         {"narwhal3d/commands.py": ("100644", b"PLACEHOLDER_FOR_SIZE\n")}),
        ("non-executable test harness", {"run_tests.sh": ("100644", b"safe\n")}),
        ("invalid Python", {"broken.py": ("100644", b"def broken(:\n")}),
        ("unexpected image", {"shot.png": ("100644", b"png")}),
        ("commercial miner", {"spike/rhi" + "no_help.py": ("100644", b"x")}),
        ("symlink", {"escape": ("120000", b"../private")}),
    )
    for label, addition in cases:
        candidate = dict(safe)
        candidate.update(addition)
        if not audit_entries(candidate):
            raise AuditError(f"self-check failed to reject {label}")
    print("audit_public_tree.py self-check: OK")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository/tree root")
    parser.add_argument("--git-ref", help="audit blobs stored in this Git ref")
    parser.add_argument("--require-root-commit", action="store_true", help="require --git-ref to have no parent")
    parser.add_argument("--self-check", action="store_true", help="exercise built-in allow/reject cases")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_check:
        self_check()
        return 0
    root = args.root.resolve()
    if args.require_root_commit and not args.git_ref:
        raise AuditError("--require-root-commit requires --git-ref")
    entries = git_entries(root, args.git_ref) if args.git_ref else filesystem_entries(root)
    problems = audit_entries(entries)
    if args.git_ref and args.require_root_commit:
        root_problem = require_root_commit(root, args.git_ref)
        if root_problem:
            problems.insert(0, root_problem)
    if problems:
        print(f"PUBLIC TREE AUDIT FAILED: {len(problems)} problem(s)", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print(f"PUBLIC TREE AUDIT PASSED: {len(entries)} file(s)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        raise SystemExit(f"PUBLIC TREE AUDIT ERROR: {exc}") from exc
