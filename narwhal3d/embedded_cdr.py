"""Bundled, supervised Rust reader for CorelDRAW versions 16 through 18.

The helper is part of Narwhal's package, not an external application. Running the
best-effort format parser in a child process keeps malformed files, allocation failure,
and parser aborts outside the application process; `converter_process` bounds time and
captured output and tears down the complete process tree.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import zipfile


class EmbeddedCDRError(ValueError):
    """The bundled reader refused malformed or incomplete CDR data."""


class EmbeddedCDRUnsupported(EmbeddedCDRError):
    """The file is a recognized CDR generation outside the embedded range."""


class EmbeddedCDRUnavailable(EmbeddedCDRError):
    """The bundled helper is absent from this development/source checkout."""


def _executable() -> str | None:
    explicit = os.environ.get("NARWHAL_CDR_MULTIVERSION")
    if explicit:
        return explicit if os.path.isfile(explicit) and os.access(explicit, os.X_OK) else None
    try:
        from .runtime_tools import arch_name, platform_name

        key = f"{platform_name()}-{arch_name()}"
    except Exception:  # noqa: BLE001 -- optional native component resolution
        return None
    root = Path(__file__).resolve().parents[1]
    name = "narwhal-cdr-multiversion.exe" if key.startswith("windows-") \
        else "narwhal-cdr-multiversion"
    candidates = (
        root / "runtime" / key / "bin" / name,
        root / "artifacts" / "cdr-multiversion" / key / name,
    )
    return next((str(path) for path in candidates
                 if path.is_file() and os.access(path, os.X_OK)), None)


def _valid(value) -> bool:
    if (not isinstance(value, dict) or value.get("version") not in (16, 17, 18)
            or not isinstance(value.get("document"), dict)
            or not isinstance(value["document"].get("pages"), list)
            or not value["document"]["pages"] or not isinstance(value.get("report"), dict)):
        return False
    report = value["report"]
    for key in ("pages", "layers", "shapes", "skipped_objects"):
        if not isinstance(report.get(key), int) or isinstance(report[key], bool) or report[key] < 0:
            return False
    warnings = report.get("warnings")
    return (isinstance(warnings, list) and len(warnings) <= 200
            and all(isinstance(warning, str) for warning in warnings))


def version_hint(path: str) -> int | None:
    """Return a bounded ZIP metadata version hint; never treats it as proof of validity."""
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("META-INF/metadata.xml")
            if info.file_size > 1024 * 1024:
                return None
            text = archive.read(info).decode("utf-8")
    except (OSError, KeyError, UnicodeError, zipfile.BadZipFile):
        return None
    match = re.search(r"<[^>]*CoreVersion>\s*(\d+)\s*</[^>]*CoreVersion>", text)
    if not match:
        return None
    value = int(match.group(1))
    return value // 100 if value % 100 == 0 else None


def decode(path: str) -> dict:
    """Return an embedded v16-v18 TraceDraw document or raise a classified error."""
    executable = _executable()
    if executable is None:
        raise EmbeddedCDRUnavailable("bundled multi-version CDR reader is unavailable")
    source = os.path.abspath(path)
    try:
        if not os.path.isfile(source) or os.path.getsize(source) > 64 * 1024 * 1024:
            raise EmbeddedCDRError("CDR input must be a regular file no larger than 64 MiB")
    except OSError as exc:
        raise EmbeddedCDRError(f"cannot inspect CDR input: {exc}") from exc
    from . import converter_process

    with tempfile.TemporaryDirectory(prefix="narwhal_embedded_cdr_") as directory:
        output = os.path.join(directory, "decoded.json")
        done = converter_process.run(
            [executable, source, output], timeout=120, max_output=8192)
        if done.timed_out:
            raise EmbeddedCDRError("bundled multi-version CDR reader timed out")
        if done.error:
            raise EmbeddedCDRError(f"bundled multi-version CDR reader could not start: {done.error}")
        detail = (done.stderr or done.stdout or "reader refused the file").strip()
        if done.returncode == 4:
            raise EmbeddedCDRUnsupported(detail)
        if done.returncode:
            raise EmbeddedCDRError(detail)
        try:
            if not os.path.isfile(output) or os.path.getsize(output) > 128 * 1024 * 1024:
                raise EmbeddedCDRError("bundled CDR reader produced no bounded result")
            with open(output, encoding="utf-8") as stream:
                value = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EmbeddedCDRError(f"bundled CDR reader produced invalid JSON: {exc}") from exc
    if not _valid(value):
        raise EmbeddedCDRError("bundled CDR reader produced an incomplete result")
    return value
