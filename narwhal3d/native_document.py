"""Resolver and supervised process adapter for the built-in Narwhal document core."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from . import converter_process

PROTOCOL = "narwhal-document-core 1"
ENV_VAR = "NARWHAL_DOCUMENT_CORE"


class DocumentCoreError(RuntimeError):
    """The built-in container tool was absent, failed, or violated its protocol."""


def _name() -> str:
    return "narwhal-document-core.exe" if sys.platform == "win32" else "narwhal-document-core"


def _candidates() -> tuple[Path, ...]:
    explicit = os.environ.get(ENV_VAR)
    root = Path(__file__).resolve().parents[1]
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    platform_key = "windows-x86_64" if sys.platform == "win32" else "linux-x86_64"
    candidates.extend((
        Path(__file__).resolve().parent / "native" / _name(),
        root / "runtime" / platform_key / "bin" / _name(),
        root / "artifacts" / "document-core" / platform_key / _name(),
        root / "native" / "document_core" / "target" / "release" / _name(),
    ))
    return tuple(candidates)


_probe_cache: dict[tuple[str, int], bool] = {}


def executable() -> Path | None:
    """Return the first executable that identifies the exact v1 protocol."""
    for path in _candidates():
        try:
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            stamp = path.stat().st_mtime_ns
        except OSError:
            continue
        key = (str(path), stamp)
        valid = _probe_cache.get(key)
        if valid is None:
            result = converter_process.run(
                (str(path), "--version"), timeout=10, max_output=4096)
            valid = (result.returncode == 0 and not result.timed_out
                     and result.stdout.strip() == PROTOCOL)
            _probe_cache[key] = valid
        if valid:
            return path
    return None


def require() -> Path:
    path = executable()
    if path is None:
        raise DocumentCoreError(
            "the built-in Narwhal document core is unavailable; reinstall the application "
            f"or set {ENV_VAR} to a compatible {PROTOCOL!r} executable")
    return path


def _run(arguments: tuple[str, ...], timeout: float) -> str:
    path = require()
    result = converter_process.run(
        (str(path), *arguments), timeout=timeout, max_output=65536)
    if result.returncode != 0 or result.timed_out or result.error:
        detail = result.error or result.stderr.strip() or f"exit status {result.returncode}"
        raise DocumentCoreError(detail)
    return result.stdout


def pack(staging: str | os.PathLike, target: str | os.PathLike) -> None:
    """Atomically pack one validated staging directory into ``target``."""
    _run(("pack", os.fspath(staging), os.fspath(target)), timeout=300)


def unpack(source: str | os.PathLike, output: str | os.PathLike) -> None:
    """Validate and atomically create the previously absent output directory."""
    _run(("unpack", os.fspath(source), os.fspath(output)), timeout=300)


def inspect(source: str | os.PathLike) -> tuple[tuple[str, int, str], ...]:
    """Return every validated ``(sha256, size, member)`` row from a container."""
    path = require()
    try:
        result = subprocess.run(
            (str(path), "inspect", os.fspath(source)), check=False,
            capture_output=True, text=True, errors="replace", timeout=300,
            env=converter_process._environment(None))
    except (OSError, subprocess.SubprocessError) as exc:
        raise DocumentCoreError(f"could not inspect native document: {exc}") from exc
    if result.returncode != 0:
        raise DocumentCoreError(result.stderr.strip() or f"exit status {result.returncode}")
    text = result.stdout
    if len(text.encode("utf-8")) > 16 * 1024 * 1024:
        raise DocumentCoreError("document inventory exceeds 16 MiB")
    rows = []
    for line in text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or len(parts[0]) != 64:
            raise DocumentCoreError("document core returned malformed inspect output")
        try:
            size = int(parts[1])
        except ValueError as exc:
            raise DocumentCoreError("document core returned a non-numeric member size") from exc
        if size < 0:
            raise DocumentCoreError("document core returned a negative member size")
        rows.append((parts[0], size, parts[2]))
    return tuple(rows)
