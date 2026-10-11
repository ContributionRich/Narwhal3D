"""`Import` -- foreign CAD, vector, and mesh files.

MEASURED (commands/import.htm): Import "imports objects from a file into the current
model"; (fileio/*): "When you open a non-3dm file and save the model, its filename will be
the default 3dm filename." `open_foreign(path)` provides that Open route for
`file_commands`.

Formats, and how exact each is:

* **.step / .stp** -- OCCT's STEP reader -> analytic MASTERS -> the exact polysurface in
  the document. UNIT-AWARE: the reader converts the file's length unit
  into the MODEL's (MEASURED: an inch box reads 1 x 2 x 3 into an inch model, 25.4 x 50.8 x
  76.2 into a mm one). Free edges come in as curves.
* **.dxf** -- `dxf_reader`. LINE / CIRCLE / ARC become EXACT rhino3dm curves
  (a circle is the rational degree-2 circle), POLYLINE / LWPOLYLINE polylines.
  `$INSUNITS` scales into the model units (unitless = model units). What the reader
  skipped (bulges, blocks, text) is REPORTED by count. Layers come across by name.
* **.svg** -- `svgpath` for path data, plus `circle` / `ellipse` / `rect` /
  `line` / `polyline` / `polygon` elements and `transform`s. Each subpath is ONE
  degree-3 NURBS curve whose Bezier spans ARE the cubics (exact); a subpath of straight
  segments is an exact polyline; a `circle`/`ellipse` element is the exact rational
  curve. Elliptical-arc path commands (`A`) are cubic approximations within a
  bound of 3e-4 r, and the message SAYS so. UNITS, MEASURED with an Inkscape 1.4 file:
  the root is ``width="210mm" viewBox="0 0 210 297"`` -- one user unit is one mm -- so
  user units are ``width / viewBox width`` when both are present, else CSS px (1/96 in).
  OURS: SVG's y runs DOWN; it is flipped about the page so the drawing reads the right
  way up in Top, with the page's lower-left corner at the origin.
* **.pdf / .ai** -- converted to SVG painting paths, then through the same exact
  cubic-to-NURBS route above. Modern PDF-compatible AI uses the PDF route; legacy
  PostScript AI is normalized by Ghostscript with `-dSAFER`. Text is omitted unless
  it was converted to outlines, and fills import as boundary paths; every omission is
  reported. PDF points preserve physical scale (1 pt = 1/72 in). Multiple pages are placed
  vertically; the page-selection dialog is not built yet.
* **.obj / .stl** -- `obj_reader` / `stl_writer.read_stl` -> mesh objects, named
  as meshes because these formats contain mesh geometry. STL triangles are welded on exactly
  equal corners. Neither format carries units; the numbers are taken as model units.
"""
from __future__ import annotations

import math
import os
import re
import xml.etree.ElementTree as ET

import rhino3dm as r3

from . import commands
from . import filedialog

TEXT = commands.TEXT

IMPORT_FILTERS = (("All supported", "*.step *.stp *.dxf *.svg *.pdf *.ai *.obj *.stl"),
                  ("STEP", "*.step *.stp"), ("AutoCAD DXF", "*.dxf"), ("SVG", "*.svg"),
                  ("PDF / Adobe Illustrator", "*.pdf *.ai"),
                  ("Wavefront OBJ", "*.obj"), ("STL", "*.stl"))
FORMATS = (".step", ".stp", ".dxf", ".svg", ".pdf", ".ai", ".obj", ".stl")

#: DXF `$INSUNITS` codes -> document unit names (the AutoCAD table; 0 = unitless).
DXF_UNITS = {1: "Inches", 2: "Feet", 3: "Miles", 4: "Millimeters", 5: "Centimeters",
             6: "Meters", 7: "Kilometers", 8: "Microinches", 9: "Mils", 10: "Yards",
             11: "Angstroms", 12: "Nanometers", 13: "Microns", 14: "Decimeters",
             15: "Dekameters", 16: "Hectometers", 17: "Gigameters"}

#: OURS: AutoCAD Color Index 1..7 -> RGB for the layers a DXF brings in; others grey.
ACI_RGB = {1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 0), 4: (0, 255, 255),
           5: (0, 0, 255), 6: (255, 0, 255), 7: (0, 0, 0)}

#: CSS absolute units in mm (SVG `width`/`height`)
SVG_UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72.0, "pc": 25.4 / 6.0,
               "px": 25.4 / 96.0, "": 25.4 / 96.0, "q": 0.25}


class Imported:
    """What a file brought in: ``items`` = ``[(geometry, base_name, layer_name|None)]``,
    ``notes`` = lines for the command line (approximations, skips, units)."""

    def __init__(self):
        self.items: list[tuple] = []
        self.notes: list[str] = []
        self.layers: dict[str, tuple[int, int, int]] = {}  # name -> (r, g, b)
        # (width, height) in model units, used only for PDF page stacking.
        self.page_size: tuple[float, float] | None = None


def _units():
    from . import units
    return units


def _model_unit(model):
    try:
        return model.unit_system()
    except Exception:  # noqa: BLE001
        return "Millimeters"


def _scale(from_unit, model):
    """Model units per one `from_unit`."""
    to = _model_unit(model)
    mm = _units().UNIT_SCALE_MM
    if from_unit not in mm or to not in mm:
        return 1.0
    return mm[from_unit] / mm[to]


# --------------------------------------------------------------------------- STEP

def _read_step(path, model):
    from . import bridge
    from .kernel import occt_backend as ob

    if not ob.available():
        raise ValueError("STEP import needs the OCCT kernel (vendor/occt), which is not "
                         "available")
    unit = _model_unit(model)
    step_unit = ob.STEP_UNITS.get(unit)
    if step_unit is None:
        raise ValueError(f"STEP import into a {unit} model is not implemented yet")
    got = ob.import_step(path, step_unit)
    out = Imported()
    for key, base in (("solids", "Polysurface"), ("shells", "Polysurface"),
                      ("faces", "Surface")):
        for shape in got[key]:
            out.items.append((bridge.document_object_from_shape(shape), base, None))
    for d in got["edges"]:
        out.items.append((bridge.curve_from_recipe(d), "Curve", None))
    files = sorted(set(got["file_units"]))
    if files:
        out.notes.append(f"file units {', '.join(files)} -> {unit}")
    form = bridge.last_document_form()
    if got["solids"] or got["shells"] or got["faces"]:
        if form[0] != "exact":
            out.notes.append("WARNING: no openNURBS brep writer -- the solids will be SAVED "
                             "faceted (they measure and export exactly)")
    return out


# --------------------------------------------------------------------------- DXF

def _arc_from(center, radius, a0, a1):
    sweep = (a1 - a0) % 360.0
    if sweep <= 0.0:
        sweep = 360.0
    pts = []
    for f in (0.0, 0.5, 1.0):
        t = math.radians(a0 + sweep * f)
        pts.append((center[0] + radius * math.cos(t), center[1] + radius * math.sin(t),
                    center[2]))
    return pts


def _read_dxf(path, model):
    from . import bridge, dxf_reader

    data = dxf_reader.read_dxf(path)
    file_unit = DXF_UNITS.get(data.get("insunits") or 0)
    k = _scale(file_unit, model) if file_unit else 1.0
    out = Imported()
    for name, aci in (data.get("layers") or {}).items():
        out.layers[name] = ACI_RGB.get(int(aci) if aci is not None else 7, (128, 128, 128))

    def P(p):
        return (p[0] * k, p[1] * k, (p[2] if len(p) > 2 else 0.0) * k)

    for e in data["entities"]:
        t = e["type"]
        layer = e.get("layer")
        if t == "LINE":
            out.items.append((bridge.make_line(P(e["p0"]), P(e["p1"])), "Line", layer))
        elif t == "CIRCLE":
            out.items.append((bridge.make_circle(P(e["center"]), e["radius"] * k),
                              "Circle", layer))
        elif t == "ARC":
            s, m, en = _arc_from(P(e["center"]), e["radius"] * k, e["start_deg"],
                                 e["end_deg"])
            out.items.append((bridge.make_arc(s, m, en), "Arc", layer))
        elif t in ("POLYLINE", "LWPOLYLINE"):
            pts = [P(p) for p in e["points"]]
            if len(pts) < 2:
                continue
            if e.get("closed") and pts[0] != pts[-1]:
                pts.append(pts[0])
            out.items.append((r3.PolylineCurve([r3.Point3d(*q) for q in pts]),
                              "Polyline", layer))
    if file_unit and k != 1.0:
        out.notes.append(f"file units {file_unit} -> {_model_unit(model)} (x{k:g})")
    elif not file_unit:
        out.notes.append("the file declares no units; taken as model units")
    skipped = data.get("skipped") or {}
    if skipped:
        parts = ", ".join(f"{n} {kind}" for kind, n in sorted(skipped.items()))
        out.notes.append(f"NOT imported: {parts}" + (
            " (polyline bulges were dropped: those segments came in STRAIGHT)"
            if skipped.get("bulge") else ""))
    return out


# --------------------------------------------------------------------------- SVG

def _num_unit(text):
    m = re.match(r"\s*([-+]?[\d.]+(?:[eE][-+]?\d+)?)\s*([a-zA-Z%]*)", text or "")
    if not m:
        return None, None
    return float(m.group(1)), m.group(2).lower()


def _mat_mul(a, b):
    """2x3 affine matrices (a, b, c, d, e, f) as in SVG: x' = a x + c y + e."""
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
            a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _parse_transform(text):
    m = IDENTITY
    for name, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", text or ""):
        v = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", args)]
        name = name.lower()
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate":
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale":
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate":
            a = math.radians(v[0])
            t = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            if len(v) == 3:
                t = _mat_mul(_mat_mul((1, 0, 0, 1, v[1], v[2]), t),
                             (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewx":
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewy":
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = _mat_mul(m, t)
    return m


def _apply(m, p):
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def _bezier_curve(beziers, m, closed):
    """One subpath's cubics as ONE curve, exactly: a polyline when every span is straight
    (svgpath emits a line as the cubic with handles at 1/3 and 2/3), else a degree-3 NURBS
    whose spans ARE the cubics (knots of multiplicity 3)."""
    def straight(b):
        p0, c1, c2, p1 = b
        e1 = (p0[0] + (p1[0] - p0[0]) / 3, p0[1] + (p1[1] - p0[1]) / 3)
        e2 = (p0[0] + 2 * (p1[0] - p0[0]) / 3, p0[1] + 2 * (p1[1] - p0[1]) / 3)
        L = max(1e-300, math.dist(p0, p1))
        return math.dist(c1, e1) <= 1e-12 * L and math.dist(c2, e2) <= 1e-12 * L
    if all(straight(b) for b in beziers):
        pts = [_apply(m, beziers[0][0])] + [_apply(m, b[3]) for b in beziers]
        return r3.PolylineCurve([r3.Point3d(x, y, 0.0) for x, y in pts])
    ctrl = [beziers[0][0]]
    for b in beziers:
        ctrl += [b[1], b[2], b[3]]
    n = len(beziers)
    c = r3.NurbsCurve(3, False, 4, len(ctrl))
    for i, q in enumerate(ctrl):
        x, y = _apply(m, q)
        c.Points[i] = r3.Point4d(x, y, 0.0, 1.0)
    knots = [float(i) for i in range(n + 1) for _ in range(3)]
    for i, kv in enumerate(knots):
        c.Knots[i] = kv
    return c


def _conic(cx, cy, rx, ry, m):
    """The EXACT rational ellipse (circle when rx == ry), mapped through the affine `m` --
    an affine map of a rational curve is the curve of the mapped control points (weights
    unchanged), so this stays exact under any SVG transform."""
    nc = r3.Circle(r3.Point3d(0, 0, 0), 1.0).ToNurbsCurve()
    for i in range(len(nc.Points)):
        q = nc.Points[i]
        w = q.W
        x, y = _apply(m, (cx + rx * q.X / w, cy + ry * q.Y / w))
        nc.Points[i] = r3.Point4d(x * w, y * w, 0.0, w)
    return nc


def _svg_style(el, inherited):
    """Computed subset of SVG style needed for import (paint + visibility)."""
    style = dict(inherited)
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            key, value = part.split(":", 1)
            style[key.strip().lower()] = value.strip()
    for key in ("stroke", "fill", "display", "visibility"):
        if el.get(key) is not None:
            style[key] = el.get(key).strip()
    return style


def _svg_rgb(value):
    """Common SVG colour spellings -> 8-bit RGB, or None for no paint/unknown."""
    text = (value or "").strip().lower()
    if not text or text in ("none", "transparent", "currentcolor") or text.startswith("url("):
        return None
    named = {"black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
             "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
             "cyan": (0, 255, 255), "aqua": (0, 255, 255),
             "magenta": (255, 0, 255), "fuchsia": (255, 0, 255),
             "gray": (128, 128, 128), "grey": (128, 128, 128)}
    if text in named:
        return named[text]
    if re.fullmatch(r"#[0-9a-f]{3}", text):
        return tuple(int(ch * 2, 16) for ch in text[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", text):
        return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))
    match = re.fullmatch(r"rgb\s*\(([^)]*)\)", text)
    if match:
        bits = [x.strip() for x in match.group(1).split(",")]
        if len(bits) == 3:
            try:
                if all(x.endswith("%") for x in bits):
                    return tuple(max(0, min(255, round(float(x[:-1]) * 2.55))) for x in bits)
                return tuple(max(0, min(255, round(float(x)))) for x in bits)
            except ValueError:
                return None
    return None


def _read_svg(path, model, offset=(0.0, 0.0)):
    from . import svgpath

    root = ET.parse(path).getroot()
    vb = [float(v) for v in re.split(r"[\s,]+", (root.get("viewBox") or "").strip()) if v]
    w, wu = _num_unit(root.get("width"))
    h, hu = _num_unit(root.get("height"))
    if len(vb) == 4 and w and wu in SVG_UNIT_MM and wu != "%":
        s = w * SVG_UNIT_MM[wu] / vb[2]          # mm per user unit
        unit_note = f"user unit = {s:g} mm (width {w:g}{wu} / viewBox {vb[2]:g})"
    else:
        s = SVG_UNIT_MM["px"]
        unit_note = "user unit = 1 px = 1/96 in"
    if len(vb) == 4:
        top = vb[1] + vb[3]
        left = vb[0]
    else:
        left = 0.0
        top = (h * SVG_UNIT_MM.get(hu, SVG_UNIT_MM["px"]) / s) if h else 0.0
    k = s / _units().UNIT_SCALE_MM[_model_unit(model)]   # model units per user unit
    # user -> model: x' = k (x - left), y' = k (top - y), plus an optional
    # model-space page offset used by PDF/AI multi-page import.
    base = (k, 0.0, 0.0, -k, -k * left + offset[0], k * top + offset[1])
    out = Imported()
    if len(vb) == 4:
        out.page_size = (abs(vb[2] * k), abs(vb[3] * k))
    elif w and h:
        out.page_size = (abs(w * SVG_UNIT_MM.get(wu, SVG_UNIT_MM["px"]) /
                             _units().UNIT_SCALE_MM[_model_unit(model)]),
                         abs(h * SVG_UNIT_MM.get(hu, SVG_UNIT_MM["px"]) /
                             _units().UNIT_SCALE_MM[_model_unit(model)]))
    arcs = skipped = clipped = 0
    ids = {el.get("id"): el for el in root.iter() if el.get("id")}
    layer_colors = {}

    def item_layer(label, style):
        # A stroked path uses its stroke colour; a fill-only path uses its fill.
        # Unknown/default paint stays black, matching SVG's default fill.
        rgb = _svg_rgb(style.get("stroke")) or _svg_rgb(style.get("fill")) or (0, 0, 0)
        if label:
            name = label
            prior = layer_colors.get(name)
            if prior is not None and prior != rgb:
                name = f"{label} [#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}]"
            layer_colors[name] = rgb
            out.layers[name] = rgb
            return name
        if rgb != (0, 0, 0):
            name = f"Vector #{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
            layer_colors[name] = rgb
            out.layers[name] = rgb
            return name
        return None

    def add(geom, base_name, label, style):
        out.items.append((geom, base_name, item_layer(label, style)))

    def walk(el, m, layer=None, inherited_style=None, resolving=()):
        nonlocal arcs, skipped, clipped
        style = _svg_style(el, inherited_style or {})
        if style.get("display", "").lower() == "none" or \
                style.get("visibility", "").lower() in ("hidden", "collapse"):
            return
        m = _mat_mul(m, _parse_transform(el.get("transform")))
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "g":
            groupmode = next((v for k, v in el.attrib.items() if k.endswith("}groupmode")), None)
            label = next((v for k, v in el.attrib.items() if k.endswith("}label")), None)
            if groupmode == "layer" and label:
                layer = label
        if el.get("clip-path"):
            clipped += 1
        f = lambda n, d=0.0: float(el.get(n, d) or d)  # noqa: E731
        geometry_tags = {"path", "circle", "ellipse", "rect", "line", "polyline", "polygon"}
        # LibreOffice writes non-drawing helpers such as object BoundingBox
        # rectangles with both paints explicitly disabled. They must never become
        # imported document curves.
        if tag in geometry_tags and style.get("fill", "").lower() == "none" \
                and style.get("stroke", "").lower() == "none":
            return

        if tag == "path":
            d = el.get("d") or ""
            if re.search(r"[Aa]", d):
                arcs += 1
            for sp in svgpath.parse_path(d):
                if sp["beziers"]:
                    add(_bezier_curve(sp["beziers"], m, sp["closed"]),
                        "Curve", layer, style)
        elif tag in ("circle", "ellipse"):
            rx = f("r") if tag == "circle" else f("rx")
            ry = f("r") if tag == "circle" else f("ry")
            if rx > 0 and ry > 0:
                add(_conic(f("cx"), f("cy"), rx, ry, m),
                    "Circle" if tag == "circle" else "Ellipse", layer, style)
        elif tag == "rect":
            x, y, ww, hh = f("x"), f("y"), f("width"), f("height")
            if f("rx") or f("ry"):
                rx = f("rx") or f("ry")
                ry = f("ry") or rx
                d = (f"M{x + rx},{y} H{x + ww - rx} A{rx},{ry} 0 0 1 {x + ww},{y + ry} "
                     f"V{y + hh - ry} A{rx},{ry} 0 0 1 {x + ww - rx},{y + hh} H{x + rx} "
                     f"A{rx},{ry} 0 0 1 {x},{y + hh - ry} V{y + ry} "
                     f"A{rx},{ry} 0 0 1 {x + rx},{y} Z")
                arcs += 1
                for sp in svgpath.parse_path(d):
                    add(_bezier_curve(sp["beziers"], m, True), "Curve", layer, style)
            elif ww > 0 and hh > 0:
                pts = [(x, y), (x + ww, y), (x + ww, y + hh), (x, y + hh), (x, y)]
                add(r3.PolylineCurve(
                    [r3.Point3d(*_apply(m, p), 0.0) for p in pts]),
                    "Rectangle", layer, style)
        elif tag == "line":
            pts = [(f("x1"), f("y1")), (f("x2"), f("y2"))]
            add(r3.LineCurve(r3.Point3d(*_apply(m, pts[0]), 0.0),
                             r3.Point3d(*_apply(m, pts[1]), 0.0)),
                "Line", layer, style)
        elif tag in ("polyline", "polygon"):
            v = [float(t) for t in re.findall(
                r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", el.get("points") or "")]
            pts = list(zip(v[0::2], v[1::2]))
            if tag == "polygon" and pts:
                pts.append(pts[0])
            if len(pts) >= 2:
                add(r3.PolylineCurve(
                    [r3.Point3d(*_apply(m, p), 0.0) for p in pts]),
                    "Polyline", layer, style)
        elif tag == "use":
            href = el.get("href") or el.get("{http://www.w3.org/1999/xlink}href") or ""
            target_id = href[1:] if href.startswith("#") else ""
            target = ids.get(target_id)
            if target is None or target_id in resolving:
                skipped += 1
                return
            use_m = _mat_mul(m, (1, 0, 0, 1, f("x"), f("y")))
            walk(target, use_m, layer, style, resolving + (target_id,))
            return
        elif tag in ("text", "image", "tspan"):
            skipped += 1
            return
        if tag in ("svg", "g", "a", "switch", "symbol") or \
                tag == root.tag.rsplit("}", 1)[-1]:
            for child in el:
                walk(child, m, layer, style, resolving)

    for child in root:
        walk(child, base)
    out.notes.append(unit_note)
    if arcs:
        out.notes.append(f"APPROXIMATE: {arcs} path(s) with elliptical arcs came in as "
                         f"cubic Beziers within 3e-4 of the radius (SVG arcs are not "
                         f"NURBS-exact through svgpath)")
    if skipped:
        out.notes.append(f"NOT imported: {skipped} text/image/unresolved-use element(s)")
    if clipped:
        out.notes.append(f"WARNING: {clipped} clipped group/path(s); clip boundaries are not "
                         "applied, so geometry outside a complex clip may remain")
    return out


def _read_pdf_ai(path, model):
    """Import PDF/AI painting paths as real curves, never as a raster image."""
    from . import vector_import

    out = Imported()
    y_offset = 0.0
    with vector_import.converted_svg_pages(path) as (pages, conversion_notes):
        for page_number, svg_path in enumerate(pages, start=1):
            got = _read_svg(svg_path, model, offset=(0.0, y_offset))
            out.items.extend(got.items)
            out.layers.update(got.layers)
            # The SVG unit note is useful once; repeated Poppler pages have the same
            # point-based unit. Keep page-specific approximation/skip reports.
            for note in got.notes:
                if note not in out.notes:
                    out.notes.append(note)
            if got.page_size:
                # OURS: pages are adjacent, bottom-to-top; this keeps placement
                # deterministic without inventing an inter-page gap.
                y_offset += got.page_size[1]
            elif page_number < len(pages):
                out.notes.append(
                    f"page {page_number} has no measurable page height; later pages overlap")
        out.notes.extend(conversion_notes)
    return out



# --------------------------------------------------------------------------- meshes

def _read_obj(path, model):
    from . import bridge, obj_reader

    out = Imported()
    for name, verts, faces in obj_reader.read_obj(path):
        if faces:
            out.items.append((bridge.mesh_from_triangles(verts, faces), "Mesh", None))
    out.notes.append("imported as MESH objects (OBJ carries no units: taken as model units)")
    return out


def _read_stl(path, model):
    from . import bridge, stl_writer

    tris = stl_writer.read_stl(path)
    index, verts, faces = {}, [], []
    for t in tris:
        f = []
        for p in t:
            key = (float(p[0]), float(p[1]), float(p[2]))
            j = index.get(key)
            if j is None:
                j = index[key] = len(verts)
                verts.append(key)
            f.append(j)
        if len(set(f)) == 3:
            faces.append(tuple(f))
    out = Imported()
    if faces:
        out.items.append((bridge.mesh_from_triangles(verts, faces), "Mesh", None))
    out.notes.append("imported as a MESH object (STL carries no units: taken as model units)")
    return out


READERS = {".step": _read_step, ".stp": _read_step, ".dxf": _read_dxf, ".svg": _read_svg,
           ".pdf": _read_pdf_ai, ".ai": _read_pdf_ai, ".obj": _read_obj,
           ".stl": _read_stl}


def read_foreign(path, model):
    """Read `path` (by extension) into an :class:`Imported`, scaled to `model`'s units.
    Raises ValueError, worded for the command line."""
    path = os.path.abspath(os.path.expanduser((path or "").strip().strip('"')))
    ext = os.path.splitext(path)[1].lower()
    if ext not in READERS:
        raise ValueError(f"importing {ext or 'a file with no extension'} is not implemented "
                         f"yet -- use " + ", ".join(FORMATS))
    if not os.path.isfile(path):
        raise ValueError(f"no such file: {path}")
    try:
        return READERS[ext](path, model)
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"could not import {os.path.basename(path)}: {e}") from e


class _LayerIndexCache:
    """Resolve imported layer names without rescanning the document table per item.

    The cache is deliberately per operation: document layer edits made elsewhere cannot
    leave a long-lived importer cache stale.  Existing duplicate names retain the first
    table entry, matching the old linear lookup; newly created layers are inserted in
    exactly the order their names are first encountered.
    """

    def __init__(self, model):
        self.model = model
        self.by_name = {}
        layers = model.file.Layers
        for index in range(len(layers)):
            name = layers[index].Name
            self.by_name.setdefault(name, index)

    def index(self, name, rgb):
        index = self.by_name.get(name)
        if index is not None:
            return index
        index = self.model.add_layer(name, rgb)
        self.by_name[name] = index
        return index


def _layer_index(model, name, rgb):
    """Compatibility helper for callers that resolve one layer outside an import batch."""
    return _LayerIndexCache(model).index(name, rgb)


def _summary(path, got, n):
    kinds = {}
    for _g, base, _l in got.items:
        kinds[base] = kinds.get(base, 0) + 1
    what = ", ".join(f"{c} {b.lower()}{'s' if c != 1 else ''}" for b, c in sorted(kinds.items()))
    msg = f"Imported {n} object(s) from {os.path.basename(path)}" + (f": {what}" if what else "")
    if got.notes:
        msg += ". " + "; ".join(got.notes)
    return msg


def import_into(ctx, path):
    """Add a file's objects to `ctx`'s model through `ctx.add_geometry`. ``(ok, msg)``."""
    try:
        got = read_foreign(path, ctx.model)
    except ValueError as e:
        return False, f"Import: {e}"
    if not got.items:
        return False, f"Import: nothing in {os.path.basename(path)} could be imported" + (
            ". " + "; ".join(got.notes) if got.notes else "")
    ctx.note_change("Import")
    layer_cache = _LayerIndexCache(ctx.model)
    for geom, base, layer in got.items:
        li = layer_cache.index(layer, got.layers.get(layer, (0, 0, 0))) \
            if layer else None
        ctx.add_geometry(geom, base, li)
    # The Layers panel reads AppState.layers, a live mirror of model.file.Layers.
    # Open replaces the model and rebuilds that mirror in set_model(); Import
    # mutates the current model in place, so it must synchronize once after all
    # new layers exist. Restrict this to the real app context so headless/fake
    # contexts keep no global side effect.
    try:
        from .state import app_state
        if app_state.ctx is ctx and app_state.model is ctx.model:
            app_state._rebuild_layers()
    except Exception:  # noqa: BLE001 -- import is also used without Blender/state
        pass
    return True, _summary(path, got, len(got.items))


def open_foreign(path):
    """The Open route for a non-3dm file: ``(model, message)``, or ``(None, message)`` when
    it cannot be read. The new model has the default units; the file's own units are
    converted into them. `file_commands` uses this for the extensions in `FORMATS`
    and then rebuilds the display."""
    from .model import NarwhalModel

    model = NarwhalModel()
    try:
        got = read_foreign(path, model)
    except ValueError as e:
        return None, f"Open: {e}"
    layer_cache = _LayerIndexCache(model)
    for i, (geom, base, layer) in enumerate(got.items, start=1):
        li = layer_cache.index(layer, got.layers.get(layer, (0, 0, 0))) if layer else 0
        model.add_object(geom, f"{base}{i:02d}", li)
    return model, _summary(path, got, len(got.items))


class ImportCmd(commands.Command):
    name = "Import"
    description = ("Import objects from a .step, .dxf, .svg, .pdf, .ai, .obj or .stl "
                   "file into the current model.")
    steps = [("Press Enter to choose the file, or type a file name:", TEXT)]

    def _enter_default(self, ctx):
        return ""

    def execute(self, ctx):
        target = self.args[0] if self.args else ""
        if not target:
            try:
                from .state import app_state
                start = filedialog.start_dir(app_state.doc_path)
            except Exception:  # noqa: BLE001
                start = os.path.expanduser("~")
            status, target = filedialog.ask_open_path("Import", start, IMPORT_FILTERS)
            if status == filedialog.CANCEL:
                return "Import cancelled", True
            if status == filedialog.UNAVAILABLE:
                self.args = []
                self.step_index = 0
                return ("No file dialog is available (kdialog / zenity) -- type the file "
                        "name (" + " ".join(FORMATS) + ")."), False
        ok, msg = import_into(ctx, target)
        return msg, True


PROVIDED = (ImportCmd,)
for _cls in PROVIDED:
    _inst = _cls()
    commands.COMMANDS[_inst.name.lower()] = _inst
