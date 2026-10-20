"""Vector file writer (PDF / SVG / AI). Pure Python, standard library only.

This module does not turn 3-D curves into 2-D Beziers. Its input is ready-made
cubic Beziers, and its responsibility is only to write them as PDF, SVG, or AI.

A ``.ai`` file is written as a PDF. Illustrator, Inkscape and laser-cut
software all open a PDF-format ``.ai`` (Illustrator's own files are, since
CS6, PDF 1.4 with an ``.ai`` container). So ``write_ai`` is an alias of
``write_pdf``. This is a deliberate, documented choice, not a default.

Coordinate contract (the caller's problem is to get it right):

* every point is in **MILLIMETRES**,
* page coordinates, origin **BOTTOM-left**, **y UP** (like PDF user space),
* a stroke is a run of cubic Beziers ``((x0,y0),(x1,y1),(x2,y2),(x3,y3))``
  where each segment starts where the previous one ended.

PDF and SVG share that contract and both honour it; the only difference is
that SVG's coordinate system has y DOWN, so SVG flips y (``H - y``).


"""

from dataclasses import dataclass

# 1 inch = 72 points (PDF) and 25.4 millimetres. So 1 mm = 72/25.4 points.
MM_TO_PT = 72.0 / 25.4


def _native_pdf_module():
    """Lazily resolve the native PDF adapter (Q117-07), or ``None``.

    Returns the ``native_pure`` module when it can be imported (production,
    where this file is ``narwhal3d.vector_export``) or loaded from this
    directory (spike tests load this module by file path, so ``narwhal3d`` is
    not an importable package there). ``None`` means "use the unchanged
    Python writer" -- the native library is absent, it predates the PDF
    export, the route is disabled by the measured crossover, or the adapter
    cannot be imported. Same dual-resolution pattern as ``obj_writer``.
    """
    try:
        from . import native_pure as _mod  # production package context
        return _mod
    except ImportError:
        pass
    try:
        import importlib.util
        import os
        import sys
        _canon = "narwhal3d_native_pure"
        if _canon in sys.modules:
            return sys.modules[_canon]
        _path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "native_pure.py")
        if not os.path.isfile(_path):
            return None
        _spec = importlib.util.spec_from_file_location(_canon, _path)
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_canon] = _mod
        _spec.loader.exec_module(_mod)
        return _mod
    except Exception:  # noqa: BLE001 -- any failure degrades to the Python path
        return None


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


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------

def _pt(mm: float) -> float:
    """Millimetres -> points."""
    return mm * MM_TO_PT


def _f4(mm: float) -> str:
    """A millimetre value, converted to points and printed with 4 decimals."""
    return f"{_pt(mm):.4f}"


def _f4_svg(mm: float) -> str:
    """A millimetre value printed with 4 decimals (SVG is already in mm)."""
    return f"{mm:.4f}"


def _pdf_escape(text: str) -> str:
    """Escape a PDF literal string (``(...)``): backslash and parens."""
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "(":
            out.append("\\(")
        elif ch == ")":
            out.append("\\)")
        else:
            out.append(ch)
    return "".join(out)


def _xml_escape(text: str) -> str:
    """Escape a value for use inside an XML attribute value."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# content stream (shared by PDF)
# ---------------------------------------------------------------------------

def _stroke_ops(s) -> list:
    """The operator lines for ONE stroke (no layer markers)."""
    r, g, b = s.rgb
    ops = [
        f"{r:.4f} {g:.4f} {b:.4f} RG",          # stroke colour
        f"{s.width_mm * MM_TO_PT:.4f} w",        # line width
    ]
    if not s.beziers:
        return ops
    x0, y0 = s.beziers[0][0]
    ops.append(f"{_f4(x0)} {_f4(y0)} m")          # move to start
    for bz in s.beziers:
        _p0, p1, p2, p3 = bz
        ops.append(
            f"{_f4(p1[0])} {_f4(p1[1])} "
            f"{_f4(p2[0])} {_f4(p2[1])} "
            f"{_f4(p3[0])} {_f4(p3[1])} c")
    if s.closed:
        ops.append("h")
    ops.append("S")
    return ops


def _content_ops(strokes, layer_of=None) -> list:
    """Return the content-stream operator lines for every stroke.

    Coordinates are already in points (bottom-left origin, y up) -- exactly
    PDF user space, so no flip is needed here.

    ``layer_of`` maps a stroke to its OCG resource name (e.g. ``L0``)
    or ``None``. When it returns a name the stroke is wrapped in a marked
    content sequence (``/OC name BDC ... EMC``); when it returns ``None`` the
    stroke is emitted bare. If ``layer_of`` is ``None`` there are no layers at
    all and the output is byte-identical to the no-layer case.
    """
    ops = []
    for s in strokes:
        body = _stroke_ops(s)
        if layer_of is None:
            ops.extend(body)
            continue
        key = layer_of(s)
        if key is None:
            ops.extend(body)          # layer="" -> outside any group
            continue
        # A property-list resource name is a PDF Name object and therefore must
        # keep its leading slash. `/OC L0 BDC` is invalid (L0 is parsed as an
        # operator); Poppler then drops every layered stroke as a blank page.
        ops.append(f"/OC /{key} BDC")
        ops.extend(body)
        ops.append("EMC")
    return ops


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _pdf_header() -> bytes:
    header = b"%PDF-1.4\n"
    # The spec wants a comment line whose four bytes are each >= 128, so that
    # binary viewers do not mistake the file for text.
    header += b"%\xe2\xe3\xcf\xd3\n"
    return header


def _finalize(header, objects, info_num) -> bytes:
    """Lay the objects down and emit the xref table + trailer.

    Every xref entry holds the TRUE byte offset of its object and is exactly
    20 bytes including its line terminator (space + \n, which the spec allows
    alongside \r\n). *info_num* is the object number of the Info dict.
    """
    parts = [header]
    pos = len(header)
    offsets = {0: 0}
    for num, body in objects:
        offsets[num] = pos
        parts.append(body)
        pos += len(body)

    xref_pos = pos
    n = max(offsets) + 1  # number of objects (0..n-1)
    xref = ["xref\n", "0 %d\n" % n]
    for i in range(n):
        if i == 0:
            xref.append("0000000000 65535 f \n")
        else:
            xref.append("%010d 00000 n \n" % offsets[i])
    xref.append("trailer\n")
    xref.append("<< /Size %d /Root 1 0 R /Info %d 0 R >>\n" % (n, info_num))
    xref.append("startxref\n")
    xref.append("%d\n" % xref_pos)
    xref.append("%%EOF\n")
    return b"".join(parts) + "".join(xref).encode("latin-1")


def _base_objects(strokes, page_w_mm, page_h_mm, title, layers) -> list:
    """Objects 1..5 plus one OCG object per layer (numbered 6..).

    When *layers* is empty this is byte-for-byte the no-layer layout. When it
    is not, the catalog carries ``/OCProperties`` (the OCG list and the order),
    the page ``/Resources`` carries ``/Properties`` (the ``L0``..``Lk`` names),
    and each layer's strokes are wrapped in ``/OC /Lk BDC ... EMC``; strokes
    with an empty layer stay outside any group.
    """
    W = _pt(page_w_mm)
    H = _pt(page_h_mm)

    if not layers:
        content = "\n".join(_content_ops(strokes, None)).encode("latin-1")
        catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
        page_res = b"/Resources << /ProcSet [/PDF] >>"
    else:
        names = {name: "L%d" % i for i, name in enumerate(layers)}

        def layer_of(s):
            return names[s.layer] if s.layer else None

        content = "\n".join(_content_ops(strokes, layer_of)).encode("latin-1")
        refs = b" ".join(b"%d 0 R" % (6 + i) for i in range(len(layers)))
        props = b" ".join(b"/L%d %d 0 R" % (i, 6 + i) for i in range(len(layers)))
        catalog = (
            b"<< /Type /Catalog /Pages 2 0 R /OCProperties << "
            b"/OCGs [" + refs + b"] /D << /Order [" + refs + b"] >> >> >>"
        )
        page_res = b"/Resources << /ProcSet [/PDF] /Properties << " + props + b" >> >>"

    objects = []
    objects.append((1, b"1 0 obj\n" + catalog + b"\nendobj\n"))
    objects.append((
        2,
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
    ))
    objects.append((3, (
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R "
        + ("/MediaBox [0 0 %.4f %.4f]" % (W, H)).encode("latin-1")
        + b" /Contents 4 0 R " + page_res + b" >>\n"
        b"endobj\n"
    )))
    objects.append((4, (
        b"4 0 obj\n"
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\n"
        b"stream\n" + content + b"\nendstream\nendobj\n"
    )))
    objects.append((5, (
        b"5 0 obj\n"
        b"<< /Title (" + _pdf_escape(title).encode("latin-1") + b") "
        b"/Producer (Narwhal 3D) >>\nendobj\n"
    )))
    for i, name in enumerate(layers):
        objects.append((6 + i, (
            b"%d 0 obj\n<< /Type /OCG /Name (%s) >>\nendobj\n"
            % (6 + i, _pdf_escape(name).encode("latin-1"))
        )))
    return objects


def _build_pdf(strokes, page_w_mm, page_h_mm, title) -> bytes:
    # Distinct layer names in first-seen order (non-empty only).
    layers = []
    for s in strokes:
        if s.layer and s.layer not in layers:
            layers.append(s.layer)
    objects = _base_objects(strokes, page_w_mm, page_h_mm, title, layers)
    return _finalize(_pdf_header(), objects, info_num=5)


def write_pdf(path, strokes, page_w_mm, page_h_mm, title="") -> int:
    """Write *strokes* to *path* as a single-page PDF 1.4.

    Returns the number of bytes written. An empty *strokes* list is valid and
    produces a blank page.

    The one-pass native serializer (Q117-07) is used when it is active for this
    input; otherwise (and on any native failure) the unchanged Python
    ``_build_pdf`` runs. Both paths are byte-for-byte identical, so a caller
    can never tell which one produced the file.
    """
    strokes = list(strokes)
    native = _native_pdf_module()
    if native is not None and getattr(native, "_native_pdf_encode_into", None) \
            is not None and native._pdf_native_active(strokes):
        result = native._native_pdf_encode_into(
            strokes, page_w_mm, page_h_mm, title)
        if result is not None:
            out, written = result
            with open(path, "wb") as fh:
                fh.write(memoryview(out)[:written])
            return written
    data = _build_pdf(strokes, page_w_mm, page_h_mm, title)
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)


def write_ai(path, strokes, page_w_mm, page_h_mm, title="") -> int:
    """Write *strokes* to a ``.ai`` file.

    # OURS: the ``.ai`` is written as a PDF (see the module docstring).
    """
    return write_pdf(path, strokes, page_w_mm, page_h_mm, title)


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

def _svg_path(stroke, page_h_mm) -> str:
    """Build an SVG path ``d`` for one stroke.

    SVG's y axis points DOWN, so we flip: ``y_svg = H - y``. A point at
    ``y = 0`` mm therefore lands at ``y = H``.
    """
    if not stroke.beziers:
        return ""
    H = page_h_mm

    def xy(p):
        return _f4_svg(p[0]) + " " + _f4_svg(H - p[1])

    x0, y0 = stroke.beziers[0][0]
    parts = ["M " + xy((x0, y0))]
    for bz in stroke.beziers:
        _p0, p1, p2, p3 = bz
        parts.append("C " + xy(p1) + " " + xy(p2) + " " + xy(p3))
    if stroke.closed:
        parts.append("Z")
    return " ".join(parts)


def _svg_path_element(s, page_h_mm) -> str:
    """One ``<path .../>`` line for *s* (two-space indent, as before)."""
    r, g, b = s.rgb
    d = _svg_path(s, page_h_mm)
    return (
        '  <path d="%s" stroke="rgb(%d,%d,%d)" '
        'stroke-width="%s" fill="none"/>\n'
        % (d, int(round(r * 255)), int(round(g * 255)), int(round(b * 255)),
           _f4_svg(s.width_mm))
    )


def write_svg(path, strokes, page_w_mm, page_h_mm) -> int:
    """Write *strokes* to *path* as an SVG document.

    Width/height and viewBox are in millimetres; y is flipped (``H - y``).
    Returns the number of bytes written.

    When any stroke has a non-empty ``layer``, each distinct layer
    becomes ``<g id="layer-N" inkscape:groupmode="layer" inkscape:label="name">``
    (one per layer, first-seen order) with ``xmlns:inkscape`` on the root; a
    stroke with ``layer=""`` stays outside any group. With no layers the output
    is byte-identical to the no-layer case.
    """
    strokes = list(strokes)
    native = _native_pdf_module()
    if native is not None and getattr(native, "_native_svg_encode_into", None) \
            is not None and native._svg_vector_native_active(strokes):
        result = native._native_svg_encode_into(strokes, page_w_mm, page_h_mm)
        if result is not None:
            data, written = result
            with open(path, "wb") as fh:
                fh.write(memoryview(data)[:written])
            return written

    W = page_w_mm
    H = page_h_mm

    layers = []
    for s in strokes:
        if s.layer and s.layer not in layers:
            layers.append(s.layer)

    out = ['<?xml version="1.0" encoding="UTF-8"?>\n']
    if layers:
        out.append(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            'width="%s" height="%s" viewBox="0 0 %s %s">\n'
            % (_f4_svg(W), _f4_svg(H), _f4_svg(W), _f4_svg(H))
        )
        for i, name in enumerate(layers):
            out.append('  <g id="layer-%d" inkscape:groupmode="layer" '
                       'inkscape:label="%s">\n' % (i, _xml_escape(name)))
            for s in strokes:
                if s.layer == name:
                    out.append(_svg_path_element(s, H))
            out.append('  </g>\n')
        # strokes with an empty layer stay outside any group
        for s in strokes:
            if not s.layer:
                out.append(_svg_path_element(s, H))
    else:
        out.append(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'width="%s" height="%s" viewBox="0 0 %s %s">\n'
            % (_f4_svg(W), _f4_svg(H), _f4_svg(W), _f4_svg(H))
        )
        for s in strokes:
            out.append(_svg_path_element(s, H))
    out.append("</svg>\n")
    data = "".join(out).encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)
