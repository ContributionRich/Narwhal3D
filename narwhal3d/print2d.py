"""2-D vector output: document curves -> cubic Beziers on a page. rhino3dm, **no bpy**.

PDF, AI, SVG, and CDR are vector formats whose shared curve representation is the CUBIC
Bezier, so every document curve is turned into cubics here, projected through the view,
and laid out on the page. `vector_export` writes the result.

Accuracy -- this is a drawing that may go to a laser cutter, so it is held to a number:

* a non-rational curve of degree <= 3 (lines, polylines, `Curve`, `InterpCrv`) is
  converted EXACTLY -- its Bezier spans ARE cubics (degree elevation is exact);
* a RATIONAL curve (circle, arc, ellipse, conic) cannot be a cubic -- no cubic is a
  circle, in any file format. Each span is fitted with a Hermite cubic that matches
  the curve's end points and end tangents exactly, and split until it is within
  `PAGE_TOL_MM` everywhere. **This is an approximation, named as one**: the output
  tessellation is never treated as document geometry, and the
  document keeps the exact circle);
* parallel projection is affine, and an affine map of a Bezier is the Bezier of the
  mapped control points -- so projecting adds no error.

`PAGE_TOL_MM` is 0.001 mm (one micron on paper).

SILHOUETTES: surfaces and solids with an exact OCCT shape (master or recipe) print
what the view SHOWS -- visible edges, tangent edges and silhouette OUTLINES, hidden lines
removed, every object hiding the others -- via OCCT's exact hidden-line removal
(`occt_backend.visible_outlines`). MEASURED: a sphere r=10 in Top prints ONE closed
circle (it used to print as its seam only); a box in Top prints 4 lines, not 12.

Not built, and reported by `build_page` rather than skipped silently:
* a brep with NO exact shape still prints its EDGES only, and the page says so;
* PERSPECTIVE views (a perspective projection of a Bezier is rational -- refused).
* points, meshes, text, hatches, dimensions, linetypes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import rhino3dm as r3

from . import vector_export as vx

#: Standard paper sheets offered by the file-output dialog.
PAGE_SIZES_MM = {
    "Letter": (215.9, 279.4), "Legal": (215.9, 355.6), "Tabloid": (279.4, 431.8),
    "A4": (210.0, 297.0), "A3": (297.0, 420.0), "A2": (420.0, 594.0),
    "A1": (594.0, 841.0), "A0": (841.0, 1189.0),
}
#: Letter landscape is the default file-output page.
DEFAULT_PAGE = "Letter"
#: a page exactly the size of the drawing (2-D export), not a sheet of paper
DRAWING = "Drawing"
#: a sheet the user types the size of (PageSetup.custom_mm = (width, height) in mm)
CUSTOM = "Custom"
DEFAULT_ORIENTATION = "Landscape"

#: Standard drafting widths offered by the file-output dialog.
PRINT_WIDTHS_MM = (0.13, 0.18, 0.25, 0.30, 0.35, 0.50, 0.60, 0.70, 0.80, 0.90,
                   1.00, 1.20, 1.40, 2.00)
#: The dialog uses the first standard drafting width by default.
DEFAULT_WIDTH_MM = PRINT_WIDTHS_MM[0]
#: CorelDRAW's native Hairline is exactly 0.003 inch.
HAIRLINE_MM = 0.0762

PAGE_TOL_MM = 0.001

UNIT_MM = {
    "Millimeters": 1.0, "Centimeters": 10.0, "Meters": 1000.0, "Inches": 25.4,
    "Feet": 304.8, "Microns": 0.001, "Kilometers": 1.0e6, "Yards": 914.4,
}


@dataclass
class View:
    """A PARALLEL view: where it looks from, in WORLD coordinates.

    `xaxis`/`yaxis` are the screen's right and up directions (unit vectors); `center`
    the world point at the middle of the viewport; `width`/`height` the visible extent
    in model units. `perspective` True is refused by `build_page`.
    """
    xaxis: tuple
    yaxis: tuple
    center: tuple
    width: float
    height: float
    perspective: bool = False
    name: str = ""


@dataclass
class PageSetup:
    size: str = DEFAULT_PAGE
    orientation: str = DEFAULT_ORIENTATION
    area: str = "Viewport"          # Viewport | Extents
    scale: object = "Fit"           # "Fit" or a float: page units per model unit
    color: str = "PrintColor"       # PrintColor | DisplayColor | BlackWhite
    margin_mm: float = 0.0          # vector files use the full page
    default_width_mm: float = DEFAULT_WIDTH_MM   # the dialog's "Default line width"
    custom_mm: tuple = (210.0, 297.0)            # used when size == CUSTOM
    cdr_version: int = 15                         # v15 default; v17 is explicit opt-in


@dataclass
class Page:
    width_mm: float
    height_mm: float
    strokes: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    scale: float = 1.0               # page mm per model unit actually used
    cdr_version: int = 15


# ---------------------------------------------------------------------------
# curves -> cubic Bezier spans (3-D, world)
# ---------------------------------------------------------------------------

def _spans(curve):
    """``[(degree, [(x, y, z, w), ...]), ...]`` -- the curve's Bezier spans.

    Euclidean points with their weights. rhino3dm's Point4d coordinates are premultiplied
    by W, so they are divided by the weight here.
    """
    nc = curve.ToNurbsCurve()
    if nc is None:
        return []
    nc = nc.Duplicate()
    if not nc.MakePiecewiseBezier(True):
        return []
    d = nc.Degree
    pts = []
    for i in range(len(nc.Points)):
        q = nc.Points[i]
        w = q.W if q.W else 1.0
        pts.append((q.X / w, q.Y / w, q.Z / w, w))
    out = []
    for s in range(0, len(pts) - 1, d):
        span = pts[s:s + d + 1]
        if len(span) == d + 1:
            out.append((d, span))
    return out


def _lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _elevate(d, p):
    """A non-rational degree-1/2/3 Bezier as an exact cubic."""
    if d == 3:
        return tuple(p)
    if d == 2:
        p0, p1, p2 = p
        return (p0, _lerp(p0, p1, 2.0 / 3.0), _lerp(p2, p1, 2.0 / 3.0), p2)
    if d == 1:
        p0, p1 = p
        return (p0, _lerp(p0, p1, 1.0 / 3.0), _lerp(p0, p1, 2.0 / 3.0), p1)
    raise ValueError(d)


def _homog(span):
    return [(x * w, y * w, z * w, w) for x, y, z, w in span]


def _casteljau(h, t):
    """Split homogeneous control points at t -> (left, right, point)."""
    left, right = [h[0]], [h[-1]]
    pts = list(h)
    while len(pts) > 1:
        pts = [_lerp(a, b, t) for a, b in zip(pts, pts[1:])]
        left.append(pts[0])
        right.append(pts[-1])
    return left, right[::-1], pts[0]


def _eval(h, t):
    _l, _r, p = _casteljau(h, t)
    return (p[0] / p[3], p[1] / p[3], p[2] / p[3])


def _euclid(q):
    return (q[0] / q[3], q[1] / q[3], q[2] / q[3])


def _hermite(h):
    """The cubic matching a rational Bezier's end points AND end tangents."""
    n = len(h) - 1
    p0, p1 = _euclid(h[0]), _euclid(h[1])
    pn, pm = _euclid(h[-1]), _euclid(h[-2])
    d0 = tuple(n * h[1][3] / h[0][3] * (b - a) for a, b in zip(p0, p1))
    d1 = tuple(n * h[-2][3] / h[-1][3] * (b - a) for a, b in zip(pm, pn))
    return (p0, tuple(a + v / 3.0 for a, v in zip(p0, d0)),
            tuple(a - v / 3.0 for a, v in zip(pn, d1)), pn)


def _cubic_at(c, t):
    s = 1.0 - t
    return tuple(s * s * s * a + 3 * s * s * t * b + 3 * s * t * t * cc + t * t * t * d
                 for a, b, cc, d in zip(*c))


def _fit(h, tol, depth=0):
    """Cubics within `tol` of the rational/high-degree span `h` (homogeneous)."""
    c = _hermite(h)
    worst = 0.0
    for k in range(1, 16):
        t = k / 16.0
        worst = max(worst, math.dist(_cubic_at(c, t), _eval(h, t)))
    if worst <= tol or depth >= 18:
        return [c]
    left, right, _p = _casteljau(h, 0.5)
    return _fit(left, tol, depth + 1) + _fit(right, tol, depth + 1)


def curve_cubics(curve, tol):
    """A curve as a list of 3-D cubic Beziers, each within `tol` (EXACT when it can be)."""
    out = []
    for d, span in _spans(curve):
        ws = [w for *_p, w in span]
        rational = max(ws) - min(ws) > 1e-12 * max(1.0, max(ws))
        pts = [p[:3] for p in span]
        if not rational and d <= 3:
            out.append(_elevate(d, pts))
        else:
            out.extend(_fit(_homog(span), tol))
    return out


# ---------------------------------------------------------------------------
# document -> page
# ---------------------------------------------------------------------------

def _wire_curves(geom):
    """The curves that draw `geom`, plus a note when something is left out."""
    if isinstance(geom, r3.Curve):
        return [geom], None
    g = geom
    if isinstance(g, r3.Extrusion):
        g = g.ToBrep(True)
    if isinstance(g, r3.Brep):
        from . import bridge

        curves, _src = bridge.extract_wireframe(g)
        return curves, "silhouettes"
    if isinstance(geom, r3.Point):
        return [], "points"
    if isinstance(geom, r3.Mesh):
        return [], "meshes"
    return [], type(geom).__name__


def _outlines(objs, view):
    """Per object: its visible outline curves, or None to fall back to its edges.
    Breps and extrusions are hidden TOGETHER, so one hides another."""
    out = [None] * len(objs)
    idx = [i for i, o in enumerate(objs) if isinstance(o.Geometry, (r3.Brep, r3.Extrusion))]
    if not idx:
        return out
    ux, uy = view.xaxis, view.yaxis
    normal = (ux[1] * uy[2] - ux[2] * uy[1], ux[2] * uy[0] - ux[0] * uy[2],
              ux[0] * uy[1] - ux[1] * uy[0])        # toward the eye: right x up
    try:
        from . import bridge

        got = bridge.visible_outline_curves([objs[i].Geometry for i in idx], normal, ux)
    except Exception:  # noqa: BLE001
        return out          # the edges print instead, and the page says so
    for i, curves in zip(idx, got):
        out[i] = curves
    return out


_NOTE_TEXT = {
    "silhouettes": ("some surfaces or solids have no exact shape and print their EDGES "
                    "only -- their silhouettes (outlines) are not drawn"),
    "points": "point objects are not printed yet",
    "meshes": "meshes are not printed yet",
}


def _rgb(model, obj, mode):
    if mode == "BlackWhite":
        return (0.0, 0.0, 0.0)
    a = obj.Attributes
    layer = None
    try:
        layer = model.file.Layers[int(a.LayerIndex)]
    except Exception:  # noqa: BLE001
        pass
    if mode == "DisplayColor":
        col = a.ObjectColor if int(a.ColorSource) == 1 else (
            layer.Color if layer is not None else (0, 0, 0, 255))
    else:
        # PlotColorSource: 0 layer, 1 object, 2 display (rhino3dm enum order)
        src = int(a.PlotColorSource)
        if src == 1:
            col = a.PlotColor
        elif src == 2:
            col = a.ObjectColor if int(a.ColorSource) == 1 else (
                layer.Color if layer is not None else (0, 0, 0, 255))
        else:
            col = layer.PlotColor if layer is not None else (0, 0, 0, 255)
    return (col[0] / 255.0, col[1] / 255.0, col[2] / 255.0)


def _width(model, obj, default=DEFAULT_WIDTH_MM):
    """Print width in mm, or None for "No Print". 0 = Default (rhino3dm PlotWeight)."""
    a = obj.Attributes
    w = None
    try:
        if int(a.PlotWeightSource) == 1:
            w = float(a.PlotWeight)
        else:
            w = float(model.file.Layers[int(a.LayerIndex)].PlotWeight)
    except Exception:  # noqa: BLE001
        w = 0.0
    if w < 0:
        return None
    return w if w > 0 else default


def _layer_name(model, obj):
    try:
        return model.file.Layers[int(obj.Attributes.LayerIndex)].Name or ""
    except Exception:  # noqa: BLE001
        return ""


def _visible(model, obj):
    try:
        if not obj.Attributes.Visible:
            return False
        return bool(model.file.Layers[int(obj.Attributes.LayerIndex)].Visible)
    except Exception:  # noqa: BLE001
        return True


def model_unit_mm(model):
    """Millimetres per model unit (the 1:1 scale needs it)."""
    try:
        getter = getattr(model, "unit_system", None)
        name = getter() if callable(getter) else str(
            model.file.Settings.ModelUnitSystem).split(".")[-1]
    except Exception:  # noqa: BLE001
        name = "Millimeters"
    return UNIT_MM.get(name, 1.0), name


def page_size(setup):
    if setup.size == CUSTOM:
        # typed by the user as width x height: used exactly as given, so the
        # orientation choice does not re-interpret it
        w, h = setup.custom_mm
        if not (w > 0 and h > 0):
            raise ValueError("a custom paper size needs a positive width and height")
        return float(w), float(h)
    w, h = PAGE_SIZES_MM[setup.size]
    if setup.orientation == "Landscape":
        w, h = max(w, h), min(w, h)
    else:
        w, h = min(w, h), max(w, h)
    return w, h


def build_page(model, view, setup, objects=None):
    """Lay the (visible, printable) objects out on a page. Returns a :class:`Page`.

    `objects` defaults to every object in the model. Raises ValueError, worded for the
    command line, for what is refused (a perspective view, an unknown paper size).
    """
    if view.perspective:
        raise ValueError("printing a PERSPECTIVE view is not implemented yet -- "
                         "print Top, Front, Right or another parallel view")
    drawing = setup.size == DRAWING
    if not drawing and setup.size not in PAGE_SIZES_MM and setup.size != CUSTOM:
        raise ValueError(f"unknown paper size {setup.size!r}")
    pw, ph = page_size(setup) if not drawing else (0.0, 0.0)
    objs = list(model.file.Objects) if objects is None else list(objects)
    ux, uy = view.xaxis, view.yaxis
    c0 = view.center

    def to_view(p):
        d = (p[0] - c0[0], p[1] - c0[1], p[2] - c0[2])
        return (d[0] * ux[0] + d[1] * ux[1] + d[2] * ux[2],
                d[0] * uy[0] + d[1] * uy[1] + d[2] * uy[2])

    items, notes = [], set()
    printable = [obj for obj in objs if _visible(model, obj)
                 and _width(model, obj, setup.default_width_mm) is not None]
    outlines = _outlines(printable, view)
    for i, obj in enumerate(printable):
        width = _width(model, obj, setup.default_width_mm)
        if outlines[i] is not None:
            curves, note = outlines[i], None
        else:
            curves, note = _wire_curves(obj.Geometry)
        if note:
            notes.add(note)
        rgb = _rgb(model, obj, setup.color)
        lname = _layer_name(model, obj)
        for crv in curves:
            items.append((crv, rgb, width, lname))

    unit_mm, _unit = model_unit_mm(model)
    avail_w = pw - 2 * setup.margin_mm
    avail_h = ph - 2 * setup.margin_mm
    # the region of the VIEW plane that goes on the page, and the scale
    if setup.area == "Extents" or drawing:
        # The extents of what will actually be DRAWN, in the view plane: sampled from
        # a coarse cubic pass. A curve's bounding box is not usable here -- for a
        # NURBS it may be the control hull, and it is a WORLD box, which projects to
        # a larger rectangle than the drawing for anything not axis-aligned.
        xs, ys = [], []
        coarse = 1e-4 * max(view.width, view.height, 1e-9)
        for crv, _rgb_, _w, _ln in items:
            for c in curve_cubics(crv, coarse):
                for i in range(17):
                    u, v = to_view(_cubic_at(c, i / 16.0))
                    xs.append(u)
                    ys.append(v)
        if xs:
            cu, cv = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            ext_w, ext_h = max(xs) - min(xs), max(ys) - min(ys)
        else:
            cu = cv = 0.0
            ext_w, ext_h = view.width, view.height
    else:
        cu = cv = 0.0
        ext_w, ext_h = view.width, view.height
    if drawing:
        # A 2-D export page is the drawing itself at the requested scale;
        # Fit has no separate paper sheet to fit into.
        k = 1.0 if setup.scale == "Fit" else float(setup.scale)
        s = k * unit_mm
        pw = max(1.0, ext_w * s + 2 * setup.margin_mm)
        ph = max(1.0, ext_h * s + 2 * setup.margin_mm)
    elif setup.scale == "Fit":
        sx = avail_w / ext_w if ext_w > 0 else math.inf
        sy = avail_h / ext_h if ext_h > 0 else math.inf
        s = min(sx, sy)
        if not math.isfinite(s):
            s = 1.0
    else:
        s = float(setup.scale) * unit_mm       # page mm per model unit
    page = Page(pw, ph)
    page.scale = s
    page.cdr_version = setup.cdr_version
    ox, oy = pw / 2.0, ph / 2.0                # centred on the output page

    def to_page(p):
        u, v = to_view(p)
        return (ox + (u - cu) * s, oy + (v - cv) * s)

    world_tol = PAGE_TOL_MM / s if s > 0 else PAGE_TOL_MM
    for crv, rgb, width, lname in items:
        cubics = curve_cubics(crv, world_tol)
        if not cubics:
            continue
        bez = tuple(tuple(to_page(p) for p in c) for c in cubics)
        # Each stroke carries its layer so the writer can preserve PDF/SVG layers.
        page.strokes.append(vx.Stroke(beziers=bez, closed=bool(crv.IsClosed),
                                      rgb=rgb, width_mm=width, layer=lname))
    page.notes = [_NOTE_TEXT.get(n, f"{n} objects are not printed yet")
                  for n in sorted(notes)]
    return page


def write(path, page, title="", hairline=False, cdr_version=None):
    """Write `page` by extension (.pdf/.ai/.svg/.dxf/native CDR)."""
    low = path.lower()
    if low.endswith(".pdf"):
        return vx.write_pdf(path, page.strokes, page.width_mm, page.height_mm, title)
    if low.endswith(".ai"):
        return vx.write_ai(path, page.strokes, page.width_mm, page.height_mm, title)
    if low.endswith(".svg"):
        return vx.write_svg(path, page.strokes, page.width_mm, page.height_mm)
    if low.endswith(".dxf"):
        from . import dxf_writer

        entities = []
        layers = {}
        for stroke in page.strokes:
            if not stroke.beziers:
                continue
            layer = stroke.layer or "0"
            rgb = tuple(max(0, min(255, round(value * 255))) for value in stroke.rgb)
            true_color = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
            layers.setdefault(layer, 0x1000000 + true_color)
            controls = [(*stroke.beziers[0][0], 0.0)]
            for _p0, p1, p2, p3 in stroke.beziers:
                controls.extend(((*p1, 0.0), (*p2, 0.0), (*p3, 0.0)))
            entities.append({"type": "SPLINE", "control_points": controls,
                             "closed": stroke.closed, "layer": layer,
                             "true_color": true_color})
        if not entities:
            raise ValueError("nothing to write to DXF")
        return dxf_writer.write_dxf(path, entities, layers=layers)
    if low.endswith(".cdr"):
        version = int(cdr_version if cdr_version is not None else page.cdr_version)
        if version == 15:
            from . import cdr_writer

            return cdr_writer.write_cdr(
                path, page.strokes, page.width_mm, page.height_mm, title, hairline=hairline)
        if version == 17:
            from . import embedded_cdr

            return embedded_cdr.write_v17(
                path, page.strokes, page.width_mm, page.height_mm, title, hairline=hairline)
        raise ValueError(f"CDR output version must be 15 or 17, not {version}")
    raise ValueError(f"{path!r}: print/export writes .pdf, .ai, .svg, .dxf or .cdr")
