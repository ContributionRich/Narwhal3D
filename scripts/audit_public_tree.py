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
    "THIRD_PARTY_NOTICES",
    "narwhal3d.sh",
    "launch/narwhal3d.desktop.in",
    "launch/narwhal3d-3dm.xml",
    "launch/narwhal3d.png",
    "packaging/linux-x86_64.lock.json",
    "licenses/GPL-3.0-or-later.txt",
    "licenses/LGPL-2.1-or-later.txt",
    "licenses/OCCT-exception-1.0.txt",
    "licenses/OCP-Apache-2.0.txt",
    "licenses/AppImage-MIT.txt",
    "licenses/rhino3dm-MIT.txt",
}
FORBIDDEN_EXACT = {
    "AGENTS" + ".md",
    "coco" + "cad.sh",
    "rhino" + "alt.sh",
    "narwhal3d/cdr_reader.py",
    "narwhal3d/cdr_writer.py",
    "narwhal3d/kernel/brep_writer.cpp",
    "launch/build_brep_writer.sh",
    "spike/test_cdr.py",
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
    "spike/rhino_*.py",
    "spike/gen_*.py",
    "spike/probe*.py",
    "spike/ui_probe*.py",
    "spike/measure_rhino_*.py",
    "spike/docs_dump.txt",
)
ALLOWED_IMAGES = (
    "launch/narwhal3d.png",
    "narwhal3d/icons/narwhal-*.png",
    "blender_patches/narwhal3d_app_icon.svg",
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ico", ".svg"}
# Assemble signatures from fragments so this policy file is subject to its own scan.
FORBIDDEN_TEXT = (
    b"/home/" + b"nathaniel",
    b"coco " + b"cad",
    b"coco" + b"cad",
    b"rhino " + b"alternative",
    b"rhino" + b"alternative",
    b"rhinocore" + b".dll",
    b"vendor/rhino" + b"-help",
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
    b"rhino" + b"_addon",
    b"rhino" + b"alt_",
    b"rhino" + b"alt-",
    b"whale orn cut" + b"test fix.cdr",
    b"complex setting" + b".3dm",
    b"flow ring" + b".3dm",
)
FORBIDDEN_PATTERNS = (
    re.compile(rb"\b(?:t|g|f|q)-\d+\b"),
    re.compile(rb"\bbug report\s*#\d+\b"),
    re.compile(rb"\bround\s+\d+\b"),
    re.compile(rb"\b(?:front" + rb"ier|lower tier|geometry agent|hand" + rb"off)\b"),
    re.compile(rb"spike/(?:probe|rhino_|gen_|measure_)"),
)
SECRET_MARKERS = (
    b"-----BEGIN PRIVATE " + b"KEY-----",
    b"gh" + b"p_",
    b"github_" + b"pat_",
    b"AK" + b"IA",
)
# These policy files necessarily name exclusions/signatures. They remain subject to path,
# symlink, Python-compilation, binary and secret checks.
PROVENANCE_SCAN_EXEMPT = {".gitignore", "scripts/audit_public_tree.py"}


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
    safe["narwhal3d/icons/narwhal-box.png"] = ("100644", b"\x89PNG\r\n\x1a\n\0")
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
        ("old namespace", {"notes.py": ("100644", b"import rhino" + b"_addon\n")}),
        ("invalid Python", {"broken.py": ("100644", b"def broken(:\n")}),
        ("unexpected image", {"shot.png": ("100644", b"png")}),
        ("commercial miner", {"spike/rhino_" + "help.py": ("100644", b"x")}),
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
