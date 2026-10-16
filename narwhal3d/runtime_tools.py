"""Locate bundled converter executables without making the app platform-bound.

The runtime layout is intentionally platform-neutral::

    runtime/<platform>-<arch>/bin/<tool>
    tools/runtime/<platform>-<arch>/bin/<tool>   # development checkout

``NARWHAL3D_RUNTIME_DIR`` may point at another runtime root.  It is checked first,
then the app-local bundle, then the development bundle, and finally PATH unless
``strict_bundled`` is requested.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import shutil
import sys
from pathlib import Path
from typing import Iterable


class RuntimeToolError(FileNotFoundError):
    """A requested converter is not available in an acceptable runtime."""


_ALIASES: dict[str, tuple[str, ...]] = {
    "inkscape": ("inkscape",),
    "pdfinfo": ("pdfinfo",),
    "gs": ("gs", "ghostscript"),
    "ghostscript": ("gs", "ghostscript"),
    "soffice": ("soffice", "libreoffice"),
    "libreoffice": ("soffice", "libreoffice"),
    "tracedraw-cli": ("tracedraw-cli",),
}


def platform_name(value: str | None = None) -> str:
    """Return a stable runtime platform name, independent of Python spelling."""
    value = (value or sys.platform).lower()
    if value.startswith("linux"):
        return "linux"
    if value in {"darwin", "mac", "macos"}:
        return "macos"
    if value.startswith(("win", "msys", "cygwin")):
        return "windows"
    return value.replace(" ", "-").replace("/", "-")


def arch_name(value: str | None = None) -> str:
    """Return a portable architecture directory component."""
    value = (value or _platform.machine() or "unknown").lower()
    aliases = {
        "amd64": "x86_64", "x64": "x86_64", "x86-64": "x86_64",
        "aarch64": "arm64", "armv8": "arm64", "arm64": "arm64",
        "i386": "x86", "i686": "x86", "x86": "x86",
    }
    return aliases.get(value, value.replace(" ", "-").replace("/", "-"))


def runtime_key(*, platform: str | None = None, arch: str | None = None) -> str:
    return f"{platform_name(platform)}-{arch_name(arch)}"


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(tool: str) -> str:
    key = tool.lower()
    if key in ("libreoffice", "soffice"):
        return "soffice"
    if key in ("ghostscript", "gs"):
        return "gs"
    return key


def _strict_verified(base: Path, key: str, tool: str, candidate: Path) -> bool:
    """True only when generated inventory still matches immutable source metadata."""
    inventory_path = base / key / "inventory.json"
    manifest_path = base / "manifest.json"
    canonical = _canonical(tool)
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = inventory["tools"][canonical]
        details = manifest["tools"][canonical]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(record, dict) or not isinstance(details, dict):
        return False
    expected_executable = details.get("executable_sha256")
    if not isinstance(expected_executable, str) or len(expected_executable) != 64:
        return False
    try:
        executable_digest = _sha256(candidate)
    except OSError:
        return False
    valid = (inventory.get("schema") == 1
             and manifest.get("schema") == 1
             and inventory.get("platform") == key
             and record.get("verified") is True
             and record.get("executable") == candidate.name
             and record.get("executable_sha256") == expected_executable
             and executable_digest == expected_executable)
    expected_archive = details.get("sha256")
    if expected_archive:
        artifact = record.get("artifact")
        if not isinstance(artifact, str) or Path(artifact).name != artifact:
            return False
        archive = candidate.parent / artifact
        try:
            archive_digest = _sha256(archive)
        except OSError:
            return False
        valid = (valid and record.get("sha256") == expected_archive
                 and archive_digest == expected_archive
                 and record.get("revision") == details.get("revision")
                 and record.get("version") == details.get("version"))
    return valid


def _roots(app_root: Path, env: dict[str, str], development_root: Path | None) -> Iterable[Path]:
    override = env.get("NARWHAL3D_RUNTIME_DIR")
    if override:
        yield Path(override).expanduser()
    yield app_root / "runtime"
    yield development_root if development_root is not None else app_root / "tools" / "runtime"


def _names(tool: str) -> tuple[str, ...]:
    key = tool.lower()
    return _ALIASES.get(key, (tool,))


def resolve_tool(
    tool: str,
    *,
    app_root: str | os.PathLike[str] | None = None,
    development_root: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    platform: str | None = None,
    arch: str | None = None,
    strict_bundled: bool = False,
) -> str:
    """Resolve *tool* to a validated executable path.

    The optional ``env`` argument makes lookup deterministic in tests.  Strict
    mode refuses PATH, which is required for reproducible/import-safe packaging.
    """
    environ = os.environ if env is None else env
    root = Path(app_root) if app_root is not None else Path(__file__).resolve().parents[1]
    dev = Path(development_root) if development_root is not None else None
    key = runtime_key(platform=platform, arch=arch)
    attempted: list[str] = []
    for base in _roots(root, environ, dev):
        bin_dir = base / key / "bin"
        for name in _names(tool):
            candidate = bin_dir / name
            attempted.append(str(candidate))
            if _is_executable_file(candidate):
                if not strict_bundled or _strict_verified(base, key, tool, candidate):
                    return str(candidate)
            # Windows bundles commonly retain .exe while the logical name does not.
            if platform_name(platform).startswith("windows"):
                exe = candidate.with_suffix(".exe")
                attempted.append(str(exe))
                if _is_executable_file(exe):
                    if not strict_bundled or _strict_verified(base, key, tool, exe):
                        return str(exe)
    if not strict_bundled:
        path_value = environ.get("PATH")
        for name in _names(tool):
            candidate = shutil.which(name, path=path_value)
            if candidate and _is_executable_file(Path(candidate)):
                return candidate
    mode = "bundled runtime" if strict_bundled else "bundled runtime or PATH"
    raise RuntimeToolError(
        f"converter {tool!r} was not found in the {mode}; tried "
        + ", ".join(attempted)
        + (" (PATH disabled by strict mode)" if strict_bundled else "")
    )


def bundled_bin_dir(*, app_root: str | os.PathLike[str] | None = None,
                    platform: str | None = None, arch: str | None = None) -> Path:
    """Return the app-local bundle bin directory (whether or not it exists)."""
    root = Path(app_root) if app_root is not None else Path(__file__).resolve().parents[1]
    return root / "runtime" / runtime_key(platform=platform, arch=arch) / "bin"
