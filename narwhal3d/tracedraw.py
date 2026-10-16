"""Pinned local TraceDraw fallback for unsupported CorelDRAW generations/features.

TraceDraw is an independent clean-room project (MIT or Apache-2.0). Narwhal uses only
its command-line converter: no service, upload, GUI, or raster fallback. Native v15
Import/Open and native v15 Export/Print do not require it. Unsupported source generations
or structures can become structured ``.tdraw`` JSON without the lossy SVG importer;
``svg_to_cdr`` remains available for explicit non-default version conversion.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence


DEFAULT_CDR_VERSION = 17
SUPPORTED_WRITE_VERSIONS = range(7, 23)


class TraceDrawError(ValueError):
    """TraceDraw is unavailable or refused a conversion."""


def executable() -> str:
    """Resolve ``tracedraw-cli`` through Narwhal's bundled-runtime/PATH policy."""
    from .runtime_tools import RuntimeToolError, resolve_tool

    try:
        return resolve_tool(
            "tracedraw-cli",
            strict_bundled=os.environ.get("NARWHAL3D_STRICT_RUNTIME") == "1",
        )
    except RuntimeToolError as exc:
        raise TraceDrawError(
            "this unsupported CorelDRAW generation/feature needs tracedraw-cli; "
            "install the bundled converter runtime or add tracedraw-cli to PATH "
            f"({exc})"
        ) from exc


def _run(argv: Sequence[str], purpose: str,
         extra_env: Mapping[str, str] | None = None):
    from . import converter_process

    done = converter_process.run(
        list(argv), timeout=180, extra_env=extra_env, max_output=8192)
    if done.timed_out:
        raise TraceDrawError(f"{purpose} timed out after 180 seconds")
    if done.error:
        raise TraceDrawError(f"{purpose} could not start: {done.error}")
    if done.returncode:
        detail = (done.stderr or done.stdout or "unknown TraceDraw error").strip()
        if len(detail) > 800:
            detail = detail[-800:]
        raise TraceDrawError(f"{purpose} failed: {detail}")
    return done


def _warnings(done) -> list[str]:
    lines = (done.stdout + "\n" + done.stderr).splitlines()
    return [line.strip() for line in lines if "warning:" in line.lower()]


def cdr_to_tdraw(source: str, directory: str) -> tuple[str, list[str]]:
    """Convert ``source`` to validated TraceDraw JSON in ``directory``.

    The structured route is intentional: TraceDraw's SVG exporter has been measured to
    omit objects and flatten layer identity from otherwise readable CDR documents.
    """
    source = os.path.abspath(source)
    cli = executable()
    info = _run([cli, "info", source], "reading CorelDRAW information")
    target = os.path.join(directory, "drawing.tdraw")
    done = _run([cli, "convert", source, target],
                "decoding CorelDRAW objects and layers")
    if not os.path.isfile(target) or os.path.getsize(target) == 0:
        raise TraceDrawError("TraceDraw produced no structured drawing data")
    try:
        with open(target, encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TraceDrawError(f"TraceDraw produced invalid structured drawing data: {exc}") from exc
    pages = document.get("pages") if isinstance(document, dict) else None
    if not isinstance(pages, list) or not pages:
        raise TraceDrawError("TraceDraw reported no readable CorelDRAW pages")
    notes = _warnings(info)
    notes.extend(note for note in _warnings(done) if note not in notes)
    notes.append("CorelDRAW decoded locally by TraceDraw with object and layer structure")
    if len(pages) > 1:
        notes.append(f"all {len(pages)} CorelDRAW pages imported vertically")
    return target, notes


def svg_to_cdr(source: str, target: str, version: int = DEFAULT_CDR_VERSION) -> int:
    """Convert SVG to a genuine CDR of ``version`` and atomically replace ``target``."""
    version = int(version)
    if version not in SUPPORTED_WRITE_VERSIONS:
        raise TraceDrawError("CorelDRAW output version must be from 7 through 22")
    cli = executable()
    target = os.path.abspath(target)
    temporary = f"{target}.saving-{os.getpid()}.tmp.cdr"
    try:
        _run(
            [cli, "convert", os.path.abspath(source), temporary],
            f"writing CorelDRAW v{version}",
            {"TRACEDRAW_CDR_VERSION": str(version)},
        )
        if not os.path.isfile(temporary) or os.path.getsize(temporary) == 0:
            raise TraceDrawError("TraceDraw produced no CDR data")
        info = _run([cli, "info", temporary], "verifying CorelDRAW output")
        match = re.search(r"^version:\s+CDR version\s+(\d+)\s*$", info.stdout,
                          re.MULTILINE | re.IGNORECASE)
        if not match or int(match.group(1)) != version:
            got = match.group(1) if match else "unknown"
            raise TraceDrawError(
                f"TraceDraw wrote CDR version {got}, expected {version}; output was refused")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return os.path.getsize(target)
