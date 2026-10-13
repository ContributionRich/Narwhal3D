"""Convert PDF and Adobe Illustrator drawings to SVG without rasterizing them.

PDF-based AI files use the PDF import path; legacy PostScript AI is normalized
before conversion. Multiple PDF pages are preserved, and text must be converted
to curves before import when it is needed as geometry.

This module is deliberately only a conversion boundary. Inkscape/Poppler emits
SVG path commands for PDF painting paths; ``import_commands._read_svg`` turns
those cubic paths into real degree-3 NURBS curves. Nothing here rasterizes a
page or turns a curve into line segments.
"""
from __future__ import annotations

import os
import re

import subprocess
import tempfile
from collections.abc import Generator, Sequence
from contextlib import contextmanager



class VectorImportError(ValueError):
    """A vector file cannot be converted safely."""


def _need(program: str, purpose: str) -> str:
    # Runtime selection is centralized so imports use the same bundled tools as
    # the launcher and can be made reproducible with NARWHAL3D_STRICT_RUNTIME.
    from .runtime_tools import RuntimeToolError, resolve_tool

    try:
        return resolve_tool(
            program,
            strict_bundled=os.environ.get("NARWHAL3D_STRICT_RUNTIME") == "1",
        )
    except RuntimeToolError as exc:
        raise VectorImportError(
            f"{purpose} needs {program}; install the bundled converter runtime "
            f"or add it to PATH ({exc})"
        ) from exc


def _run(argv: Sequence[str], purpose: str) -> str:
    try:
        done = subprocess.run(
            argv, check=False, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise VectorImportError(f"{purpose} timed out after 120 seconds") from exc
    except OSError as exc:
        raise VectorImportError(f"{purpose} could not start: {exc}") from exc
    if done.returncode:
        detail = (done.stderr or done.stdout or "unknown converter error").strip()
        if len(detail) > 600:
            detail = detail[-600:]
        raise VectorImportError(f"{purpose} failed: {detail}")
    return done.stdout


def _is_pdf(path: str) -> bool:
    with open(path, "rb") as stream:
        return stream.read(1024).lstrip().startswith(b"%PDF-")


def _page_count(pdf_path: str) -> int:
    info = _run([_need("pdfinfo", "PDF import"), pdf_path], "reading PDF information")
    encrypted = re.search(r"^Encrypted:\s+yes", info, re.MULTILINE | re.IGNORECASE)
    if encrypted:
        raise VectorImportError("encrypted PDF files are not supported")
    match = re.search(r"^Pages:\s+(\d+)\s*$", info, re.MULTILINE)
    if not match or int(match.group(1)) < 1:
        raise VectorImportError("the PDF has no readable pages")
    return int(match.group(1))


def _legacy_ai_to_pdf(source: str, target: str) -> None:
    # -dSAFER is mandatory: PostScript is a programming language, and imported
    # drawings are untrusted input. -dEPSCrop honours Illustrator's artboard /
    # EPS BoundingBox instead of putting every drawing on a default Letter page.
    _ = _run([
        _need("gs", "legacy Illustrator import"), "-dSAFER", "-dBATCH",
        "-dNOPAUSE", "-sDEVICE=pdfwrite", "-dEPSCrop",
        f"-sOutputFile={target}", source,
    ], "converting the legacy Illustrator file to PDF")



def _pdf_page_to_svg(pdf_path: str, page: int, target: str) -> None:
    _ = _run([
        _need("inkscape", "PDF/Illustrator vector import"),
        f"--pages={page}", "--pdf-poppler", "--pdf-font-strategy=delete-all",
        "--export-plain-svg", f"--export-filename={target}", pdf_path,
    ], f"converting PDF page {page} to vector paths")
    if not os.path.isfile(target) or os.path.getsize(target) == 0:
        raise VectorImportError(f"converting PDF page {page} produced no SVG data")


@contextmanager
def converted_svg_pages(path: str) -> Generator[tuple[list[str], list[str]], None, None]:
    """Yield ``(svg_paths, notes)`` for a PDF or AI file.

    CorelDRAW uses the separate structured TraceDraw route in ``import_commands`` so
    SVG conversion cannot discard CDR objects or layer identity. Modern AI files beginning
    with ``%PDF-`` use the PDF route directly. Legacy
    PostScript AI is normalized by Ghostscript first. Every PDF page is emitted
    independently so the caller can place pages vertically. Text objects are
    deliberately omitted; text converted to outlines remains a normal path.
    """
    source = os.path.abspath(path)
    ext = os.path.splitext(source)[1].lower()
    if ext not in (".pdf", ".ai"):
        shown = ext or "a file with no extension"
        raise VectorImportError(
            f"{shown} vector import is not supported -- use .pdf or .ai")
    with tempfile.TemporaryDirectory(prefix="narwhal3d_vector_import_") as tmp:
        pdf_path = source
        notes: list[str] = []
        if not _is_pdf(source):
            if ext != ".ai":
                raise VectorImportError("the file does not contain a PDF document")
            pdf_path = os.path.join(tmp, "legacy-ai.pdf")
            _legacy_ai_to_pdf(source, pdf_path)
            notes.append("legacy PostScript AI converted as vector paths")
        elif ext == ".ai":
            notes.append("PDF-compatible AI imported with PDF options")

        count = _page_count(pdf_path)
        pages: list[str] = []
        for page in range(1, count + 1):
            target = os.path.join(tmp, f"page-{page}.svg")
            _pdf_page_to_svg(pdf_path, page, target)
            pages.append(target)
        notes.append(
            "text objects were not imported; text converted to curves/outlines is imported")
        notes.append("PDF fills import as their boundary paths; hatch fills are not implemented")
        notes.append("PDF layer hierarchy and bitmap Pictures are not implemented")
        if count > 1:
            notes.append(
                f"all {count} pages imported vertically (page-selection dialog not implemented)")
        yield pages, notes
