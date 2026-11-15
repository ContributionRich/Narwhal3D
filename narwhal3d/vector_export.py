"""Exact PDF / PDF-format AI / SVG serialization, owned by Rust.

Input is ready-made 2D cubic Beziers in millimetres, bottom-left origin/y up.
SVG flips y and optionally declares/converts its paper unit. Closed paths are
stroked, not filled. AI deliberately remains a PDF alias (OURS).

Python retains Stroke, host encoding, ctypes marshalling and ordinary file I/O.
Required-native errors never replay a Python codec. Nonfinite/overflowed values,
non-2D control shapes and nonpositive unit scales are explicitly refused before
opening the destination (intentional safety corrections). Precision is bounded
at 4096 decimals; Rust enforces named memory/input limits.
"""
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys

MM_TO_PT = 72.0 / 25.4


def _native():
    if __package__:
        try:
            from . import native_vector_export
            return native_vector_export
        except ImportError:
            pass  # qualified direct-file loading need not import the Blender app
    key = "narwhal3d_native_vector_export"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name("native_vector_export.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[key] = module
    return sys.modules[key]


@dataclass(frozen=True)
class Stroke:
    """One continuous path of cubic Beziers, ready to be stroked.

    ``beziers``: ``((x0,y0),(x1,y1),(x2,y2),(x3,y3)), ...`` in millimetres,
    page coordinates, origin bottom-left, y up. Each segment starts where the
    previous one ended.

    ``closed``: cap the path (PDF ``h`` / SVG ``Z``). A closed stroke is still
    **stroked**, never filled -- this is a vector *writer*, not a renderer.

    ``rgb``: stroke colour, each component 0..1.
    ``width_mm``: line width in millimetres.
    ``layer``: an optional layer name. When any stroke has a non-empty
    layer, each distinct layer becomes an Optional Content Group (PDF) / a
    labelled ``<g>`` (SVG); strokes with ``layer=""`` stay outside any group.
    Added as the LAST field so every existing caller keeps working.
    """

    beziers: tuple
    closed: bool = False
    rgb: tuple = (0.0, 0.0, 0.0)
    width_mm: float = 0.25
    layer: str = ""


def write_pdf(path, strokes, page_w_mm, page_h_mm, title="") -> int:
    """Encode/validate one PDF 1.4 page, then use ordinary host file I/O."""
    data = _native().encode(strokes, page_w_mm, page_h_mm, title=title, pdf=True)
    # The complete native result is validated before ordinary open can truncate.
    # Keep host I/O here to preserve Python file/fd semantics and actual OS errors.
    with open(path, "wb") as stream:
        stream.write(data)
    return len(data)


def write_ai(path, strokes, page_w_mm, page_h_mm, title="") -> int:
    """OURS: Illustrator-compatible PDF-format .ai, exactly the PDF writer."""
    return write_pdf(path, strokes, page_w_mm, page_h_mm, title)


def write_svg(path, strokes, page_w_mm, page_h_mm, unit=None) -> int:
    """Write SVG; unit is (suffix, millimetres-per-unit, decimals).

    Without unit the legacy unitless/fixed-4 document is preserved byte-for-byte.
    Unicode/arbitrary-length suffixes and precision beyond 12 are supported.
    """
    data = _native().encode(strokes, page_w_mm, page_h_mm, unit=unit)
    # The complete native result is validated before ordinary open can truncate.
    # Keep host I/O here to preserve Python file/fd semantics and actual OS errors.
    with open(path, "wb") as stream:
        stream.write(data)
    return len(data)
