#!/usr/bin/env python3
"""Shared fail-closed policy for very-early Narwhal 3D prerelease packages."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

PREVIEW_SERIES = (0, 4)
MAX_HOST_GLIBC = (2, 35)  # Ubuntu 22.04, the oldest standard-support LTS in 2026.
PREVIEW_VERSION_RE = re.compile(
    r"0\.4\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z][0-9A-Za-z._-]*)?\Z")
GLIBC_RE = re.compile(r"\bGLIBC_([0-9]+)\.([0-9]+)\b")


def validate_preview_version(version: str) -> str:
    """Return a valid 0.4.x prerelease-package version; reject every later series."""
    if not isinstance(version, str) or not PREVIEW_VERSION_RE.fullmatch(version):
        raise ValueError(
            "release versions are capped at 0.4.x while Narwhal 3D is an early prerelease")
    return version


def glibc_versions(text: str) -> set[tuple[int, int]]:
    """Extract required/defined GLIBC symbol versions from ``readelf`` text."""
    return {(int(major), int(minor)) for major, minor in GLIBC_RE.findall(text or "")}


def format_glibc(version: tuple[int, int]) -> str:
    return f"{version[0]}.{version[1]}"


def scan_runtime_glibc(root: Path) -> tuple[tuple[int, int], Path]:
    """Return the newest GLIBC symbol referenced by regular ELF files below ``root``.

    Symlinks are skipped because their real in-tree targets are scanned independently.
    A malformed/unreadable ELF is a release error rather than an uninspected payload.
    """
    readelf = shutil.which("readelf")
    if not readelf:
        raise RuntimeError("readelf is required to certify the AppImage host glibc baseline")
    newest = (0, 0)
    newest_path: Path | None = None
    elf_count = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    continue
        except OSError as exc:
            raise RuntimeError(f"cannot inspect runtime file {path}: {exc}") from exc
        elf_count += 1
        try:
            completed = subprocess.run(
                [readelf, "--version-info", str(path)], check=False,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"readelf failed for {path}: {exc}") from exc
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"readelf refused runtime ELF {path}: {detail}")
        versions = glibc_versions(completed.stdout)
        if versions and max(versions) > newest:
            newest = max(versions)
            newest_path = path
    if not elf_count or newest_path is None:
        raise RuntimeError("runtime contains no inspectable ELF with a GLIBC requirement")
    if newest > MAX_HOST_GLIBC:
        relative = newest_path.relative_to(root) if newest_path.is_relative_to(root) else newest_path
        raise RuntimeError(
            f"runtime requires GLIBC_{format_glibc(newest)} in {relative}; "
            f"AppImage host baseline is capped at GLIBC_{format_glibc(MAX_HOST_GLIBC)}")
    return newest, newest_path
