"""Import and non-.3dm Open tests for STEP, DXF, SVG, OBJ, STL, PDF, and AI.

Fixtures pin format parsing, document units, layer mapping, exact-solid adoption, and
Blender display conversion for imported meshes.
"""
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import types
import zipfile

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("narwhal3d.cplane", "narwhal3d/cplane.py")
_load("narwhal3d.runtime_tools", "narwhal3d/runtime_tools.py")
td = _load("narwhal3d.tracedraw", "narwhal3d/tracedraw.py")
vi = _load("narwhal3d.vector_import", "narwhal3d/vector_import.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
analysis = _load("narwhal3d.analysis", "narwhal3d/analysis.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
nearest = _load("narwhal3d.nearest", "narwhal3d/nearest.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
ex = _load("narwhal3d.export_commands", "narwhal3d/export_commands.py")
dxf_writer = _load("narwhal3d.dxf_writer", "narwhal3d/dxf_writer.py")
obj_writer = _load("narwhal3d.obj_writer", "narwhal3d/obj_writer.py")
stl_writer = _load("narwhal3d.stl_writer", "narwhal3d/stl_writer.py")
vector_export = _load("narwhal3d.vector_export", "narwhal3d/vector_export.py")
_load("narwhal3d.native_cdr", "narwhal3d/native_cdr.py")
_load("narwhal3d.cdr_reader", "narwhal3d/cdr_reader.py")
cdr_writer = _load("narwhal3d.cdr_writer", "narwhal3d/cdr_writer.py")
from narwhal3d import kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as ob, onbrep  # noqa: E402

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


def done():
    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        for f in failures:
            print("FAIL:", f)
        sys.exit(1)
    print("IMPORT PASSED")
    sys.exit(0)


ic = sys.modules.get("narwhal3d.import_commands")
check("import" in commands.COMMANDS and ic is not None, "the Import command is registered")
if ic is None:
    done()
TMP = tempfile.mkdtemp(prefix="g89_")


def doc(unit="Millimeters"):
    m = model.NarwhalModel()
    m.set_unit_system(unit)
    return m


class Ctx:
    def __init__(self, m):
        self.model = m
        self.added = []
        self.selected = []

    def add_geometry(self, geom, base_name, layer_index=None):
        nm = f"{base_name}_{len(self.added) + 1}"
        object_id, _attributes = self.model.add_object(geom, nm, layer_index)
        self.added.append((nm, geom, layer_index))
        return object_id, nm

    def note_change(self, label=None):
        pass

    def select_by_names(self, names):
        self.selected = list(names)
        return list(names)


def rel(a, b):
    return abs(a - b) / abs(b)


print("-- native 3DM merges into the open model --")
source_3dm = doc("Inches")
cut_layer = source_3dm.add_layer("Cut", (17, 83, 149))
source_3dm.add_object(bridge.make_circle((1, 2, 0), 2), "ImportedRing", cut_layer)
source_3dm.add_object(bridge.make_line((0, 0, 0), (3, 0, 0)), "ImportedAxis", 0)
native_path = os.path.join(TMP, "native.3dm")
source_3dm.save(native_path)
target_3dm = doc("Millimeters")
target_3dm.add_object(bridge.make_line((-1, 0, 0), (-2, 0, 0)), "Existing", 0)
native_ctx = Ctx(target_3dm)
native_ok, native_msg = ic.import_into(native_ctx, native_path)
native_objects = list(target_3dm.file.Objects)
native_ring = next((o for o in native_objects
                    if (o.Attributes.Name or "").startswith("ImportedRing")), None)
native_circle = native_ring.Geometry.TryGetCircle() if native_ring is not None else None
native_layer = (target_3dm.file.Layers[int(native_ring.Attributes.LayerIndex)]
                if native_ring is not None else None)
check(native_ok and len(native_objects) == 3 and native_circle is not None
      and abs(native_circle.Radius - 50.8) < 1e-9,
      f"3DM Import keeps the open document and scales 2 inches to 50.8 mm "
      f"({len(native_objects)} objects; {native_msg!r})")
check(native_layer is not None and native_layer.Name == "Cut"
      and tuple(native_layer.Color)[:3] == (17, 83, 149),
      f"3DM Import creates the source layer with its colour "
      f"({native_layer and native_layer.Name!r}; {native_layer and tuple(native_layer.Color)[:3]})")
check(native_ctx.selected == [name for name, _geom, _layer in native_ctx.added]
      and len(native_ctx.selected) == 2 and "Existing" not in native_ctx.selected,
      f"Import selects exactly every newly added object ({native_ctx.selected})")
check(".3dm" in ic.FORMATS
      and any("*.3dm" in patterns for _label, patterns in ic.IMPORT_FILTERS),
      "Import's command and file-dialog formats expose .3dm")

V_BU = 1000 + 45 * math.pi
if ob.available():
    print("-- STEP: exported box union cylinder --")
    m = doc()
    m.add_object(bridge.boolean_brep(bridge.make_box((0, 0, 0), (10, 10, 0), 10),
                                     bridge.make_cylinder((10, 5, 0), 3, 10), "UNION"), "BU", 0)
    step = os.path.join(TMP, "bu.step")
    ok, msg = ex.export_objects(m, list(m.file.Objects), step)
    got = ic.read_foreign(step, doc())
    g = got.items[0][0] if got.items else None
    mst = kernel.master_of(g) if g is not None else None
    mp = analysis.exact_mass(g) if g is not None else None
    check(len(got.items) == 1 and mst is not None and ob.face_types(mst) == {"PLANE": 7,
                                                                            "CYLINDER": 1}
          and ob.is_airtight(mst),
          f"imports as ONE exact solid {{PLANE: 7, CYLINDER: 1}}, airtight "
          f"({mst is not None and ob.face_types(mst)})")
    check(mp is not None and rel(mp["volume"], V_BU) < 1e-9,
          f"measuring 1000 + 45pi ({mp and mp['volume']!r})")
    form = bridge.last_document_form()
    check(g is not None and mst is not None and kernel.honesty_problem(g) is None
          and form[0] in {"exact", "triangulated"},
          f"the document object has an honest analytic master and declared display form "
          f"({form}, {g is not None and len(g.Faces)} proxy faces)")
    drilled = bridge.boolean_brep(g, bridge.make_cylinder((5, 5, -1), 2, 12), "DIFFERENCE")
    check(kernel.master_of(drilled) is not None and bridge.last_occt_decline() is None,
          "and it booleans again exactly")

    print("\n-- STEP units --")
    im = doc("Inches")
    im.add_object(bridge.make_box((0, 0, 0), (1, 2, 0), 3), "B", 0)
    istep = os.path.join(TMP, "inch.step")
    ex.export_objects(im, list(im.file.Objects), istep)
    got = ic.read_foreign(istep, doc("Millimeters"))
    lo, hi = ob.tight_bbox(kernel.master_of(got.items[0][0]))
    check(all(abs(a - b) < 1e-9 for a, b in zip(hi, (25.4, 50.8, 76.2)))
          and all(abs(a) < 1e-9 for a in lo) and any("INCH" in n for n in got.notes),
          f"an INCH 1x2x3 box lands 25.4 x 50.8 x 76.2 in a mm model, and says so "
          f"({hi}; {got.notes})")
    got = ic.read_foreign(istep, doc("Inches"))
    lo, hi = ob.tight_bbox(kernel.master_of(got.items[0][0]))
    check(all(abs(a - b) < 1e-9 for a, b in zip(hi, (1, 2, 3))),
          f"and 1 x 2 x 3 in an inch model ({hi})")
    from OCP.Interface import Interface_Static  # noqa: E402
    check(Interface_Static.CVal_s("xstep.cascade.unit") == "MM",
          "OCCT's cascade unit is restored after the read")

    print("\n-- an imported solid survives save and reopen --")
    c = Ctx(doc())
    ok, msg = ic.import_into(c, step)
    p3 = os.path.join(TMP, "imp.3dm")
    c.model.save(p3)
    kernel.forget_masters()
    m2 = model.NarwhalModel.load(p3)
    reopened = m2.file.Objects[0].Geometry
    reopened_master = kernel.master_of(reopened)
    reopened_volume = ob.volume(reopened_master) if reopened_master is not None else None
    if onbrep.available():
        check(ok and reopened_volume is not None and rel(reopened_volume, V_BU) < 1e-9,
              f"with the optional writer, reopen retains 1000 + 45pi "
              f"(master={reopened_master is not None}, message={msg!r})")
    else:
        check(ok and reopened_master is None
              and kernel.representation_of(reopened) == kernel.MESH_DERIVED
              and "WARNING" in msg and "SAVED faceted" in msg,
              f"without the optional writer, reopen is demoted and the import/save "
              f"limitation is explicit (representation={kernel.representation_of(reopened)!r}, "
              f"message={msg!r})")
else:
    print("  skip  STEP gates need OCCT")

print("\n-- DXF --")
dxf = os.path.join(TMP, "a.dxf")
dxf_writer.write_dxf(dxf, [
    {"type": "CIRCLE", "center": (1, 2, 0), "radius": 7.5, "layer": "Cut"},
    {"type": "ARC", "center": (0, 0, 0), "radius": 5, "start_deg": 0, "end_deg": 90},
    {"type": "LINE", "p0": (0, 0, 0), "p1": (10, 5, 0)},
    {"type": "POLYLINE", "points": [(0, 0, 0), (4, 0, 0), (4, 3, 0)], "closed": True}],
    layers={"Cut": 1})
got = ic.read_foreign(dxf, doc())
geoms = {b: g for g, b, _l in got.items}
circ = geoms.get("Circle")
cc = circ.TryGetCircle() if circ is not None and circ.IsCircle() else None
nc = circ.ToNurbsCurve() if circ is not None else None
check(cc is not None and nc.Degree == 2 and nc.IsRational and cc.Radius == 7.5
      and math.dist((cc.Center.X, cc.Center.Y), (1.0, 2.0)) < 1e-12,
      f"a DXF CIRCLE is a RATIONAL degree-2 circle, radius exactly 7.5 at (1, 2) "
      f"({cc and cc.Radius})")
arc = geoms.get("Arc")
a = arc.TryGetArc() if arc is not None else None
check(a is not None and abs(a.Radius - 5) < 1e-12 and abs(a.AngleDegrees - 90) < 1e-9
      and math.dist((arc.PointAtStart.X, arc.PointAtStart.Y), (5, 0)) < 1e-12,
      f"an ARC is an exact 90-degree arc from (5, 0) ({a and a.AngleDegrees})")
pl = geoms.get("Polyline")
check(pl is not None and pl.IsClosed and pl.PointCount == 4 and "Line" in geoms,
      "a closed POLYLINE closes; the LINE is a line")
check([l for g, b, l in got.items if b == "Circle"] == ["Cut"] and got.layers.get("Cut")
      == (255, 0, 0), f"layers come across by name and colour ({got.layers})")
inch_dxf = os.path.join(TMP, "inch.dxf")
text = open(dxf).read().replace("$INSUNITS\n70\n4", "$INSUNITS\n70\n1")
open(inch_dxf, "w").write(text)
got = ic.read_foreign(inch_dxf, doc())
r = [g.TryGetCircle().Radius for g, b, _ in got.items if b == "Circle"]
check(r and abs(r[0] - 7.5 * 25.4) < 1e-9 and any("Inches" in n for n in got.notes),
      f"$INSUNITS = 1 (inches) scales into a mm model: r = 190.5 ({r}, {got.notes})")
lw = os.path.join(TMP, "lw.dxf")
open(lw, "w").write("0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n0\nENDSEC\n0\nSECTION\n2\n"
                    "ENTITIES\n0\nLWPOLYLINE\n8\n0\n90\n3\n70\n0\n10\n0\n20\n0\n42\n1\n10\n10\n"
                    "20\n0\n10\n10\n20\n10\n0\nTEXT\n8\n0\n1\nhi\n0\nENDSEC\n0\nEOF\n")
got = ic.read_foreign(lw, doc())
check(len(got.items) == 1 and any("bulge" in n and "TEXT" in n and "STRAIGHT" in n
                                  for n in got.notes),
      f"what the reader skipped (a bulge, a TEXT) is REPORTED, bulge named ({got.notes})")

print("\n-- SVG (the file Inkscape wrote) --")
INKSCAPE = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<svg
   width="210mm"
   height="297mm"
   viewBox="0 0 210 297"
   version="1.1"
   id="svg1"
   xmlns="http://www.w3.org/2000/svg"
   xmlns:svg="http://www.w3.org/2000/svg">
  <g
     id="layer1" />
  <g
     id="g1"
     transform="translate(10,5)">
    <path
       id="c1"
       style="fill:none;stroke:#000000"
       d="m 125,100 a 25,25 0 0 1 -25,25 25,25 0 0 1 -25,-25 25,25 0 0 1 25,-25 25,25 0 0 1 25,25 z" />
    <path
       id="r1"
       style="fill:none;stroke:#000000"
       d="M 20,30 H 60 V 50 H 20 Z" />
  </g>
</svg>
"""
svg = os.path.join(TMP, "ink.svg")
open(svg, "w").write(INKSCAPE)
got = ic.read_foreign(svg, doc())
crv, rect = got.items[0][0], got.items[1][0] if len(got.items) > 1 else None
C, R = (110.0, 297.0 - 105.0), 25.0
d = crv.Domain
dev = max(abs(math.dist((crv.PointAt(d.T0 + (d.T1 - d.T0) * i / 400).X,
                         crv.PointAt(d.T0 + (d.T1 - d.T0) * i / 400).Y), C) - R)
          for i in range(401))
check(crv.IsClosed and dev <= 3e-4 * R,
      f"the circle path imports closed, centre (110, 192), within 3e-4 r "
      f"(max {dev:.2e} vs {3e-4 * R:.1e})")
check(isinstance(crv, r3.NurbsCurve) and crv.Degree == 3 and len(crv.Points) == 13,
      f"as ONE degree-3 curve whose 4 spans are the 4 cubics (13 control points)")
check(any("APPROXIMATE" in n for n in got.notes) and any("1 mm" in n for n in got.notes),
      f"the arc approximation and the unit rule are stated ({got.notes})")
pts = [rect.Point(i) for i in range(rect.PointCount)] if isinstance(rect, r3.PolylineCurve) \
    else []
check(len(pts) == 5 and rect.IsClosed and {(p.X, p.Y) for p in pts}
      == {(30.0, 297 - 35.0), (70.0, 262.0), (70.0, 242.0), (30.0, 242.0)},
      f"the rect path is an EXACT closed polyline, transformed and flipped "
      f"({[(p.X, p.Y) for p in pts]})")
got = ic.read_foreign(svg, doc("Inches"))
c2 = got.items[0][0]
xs = [c2.PointAt(c2.Domain.T0 + (c2.Domain.T1 - c2.Domain.T0) * i / 400).X
      for i in range(401)]
check(abs((max(xs) - min(xs)) - 50 / 25.4) < 1e-3,
      f"into an INCH model the 50 mm circle is 1.9685 in across ({max(xs) - min(xs):.4f})")
el = os.path.join(TMP, "el.svg")
open(el, "w").write('<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96">'
                    '<circle cx="48" cy="48" r="24" transform="scale(2)"/></svg>')
got = ic.read_foreign(el, doc())
e = got.items[0][0]
ec = e.TryGetCircle() if e.IsCircle() else None
check(ec is not None and e.IsRational and abs(ec.Radius - 48 * 25.4 / 96) < 1e-12
      and not any("APPROXIMATE" in n for n in got.notes),
      f"a <circle> element (no viewBox: px, 1/96 in), under a transform, is an EXACT "
      f"rational circle r = 48 px = 12.7 mm ({ec and ec.Radius})")

layered_svg = os.path.join(TMP, "layers-and-use.svg")
open(layered_svg, "w").write('''<svg xmlns="http://www.w3.org/2000/svg"
 xmlns:xlink="http://www.w3.org/1999/xlink"
 xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
 width="100mm" height="100mm" viewBox="0 0 100 100">
 <defs><path id="unit" d="M0,0 L10,0" stroke="#0000ff" fill="none"/></defs>
 <g inkscape:groupmode="layer" inkscape:label="Cut" style="stroke:#ff0000;fill:none">
  <path d="M10,10 L20,10"/>
 </g>
 <g inkscape:groupmode="layer" inkscape:label="Score" stroke="rgb(0%,100%,0%)">
  <path d="M10,20 L20,20" fill="none"/>
 </g>
 <use xlink:href="#unit" x="20" y="30"/>
 <rect class="BoundingBox" x="0" y="0" width="100" height="100"
       stroke="none" fill="none"/>
</svg>''')
got_layers = ic.read_foreign(layered_svg, doc())
item_layers = [layer for _geom, _base, layer in got_layers.items]
used = got_layers.items[2][0] if len(got_layers.items) == 3 else None
check(item_layers == ["Cut", "Score", "Vector #0000FF"]
      and got_layers.layers == {"Cut": (255, 0, 0), "Score": (0, 255, 0),
                                "Vector #0000FF": (0, 0, 255)},
      f"SVG/Inkscape layer names and stroke colours become document layers, while "
      f"an explicitly unpainted BoundingBox helper is ignored "
      f"({item_layers}, {got_layers.layers})")
check(used is not None
      and math.dist((used.PointAtStart.X, used.PointAtStart.Y), (20.0, 70.0)) < 1e-12
      and math.dist((used.PointAtEnd.X, used.PointAtEnd.Y), (30.0, 70.0)) < 1e-12,
      f"SVG <use> instances import referenced vector geometry with placement "
      f"({used and (used.PointAtStart.X, used.PointAtStart.Y)} -> "
      f"{used and (used.PointAtEnd.X, used.PointAtEnd.Y)})")

print("\n-- optional PDF / Adobe Illustrator converters --")
try:
    converters = {tool: vi._need(tool, "optional import test")
                  for tool in ("inkscape", "pdfinfo", "gs")}
except vi.VectorImportError as exc:
    converters = None
    print(f"  skip  optional converter pack is not installed ({exc})")
    check(True, "PDF/AI converter tests skip explicitly when the optional pack is absent")
else:
    check(all(converters.values()),
          "production resolver finds every installed optional vector converter")
    beziers = (((10.0, 20.0), (20.0, 20.0), (30.0, 40.0), (40.0, 40.0)),)
    pdf = os.path.join(TMP, "drawing.pdf")
    vector_export.write_pdf(pdf, [vector_export.Stroke(beziers)], 100.0, 80.0)
    got = ic.read_foreign(pdf, doc())
    pdf_curve = got.items[0][0] if got.items else None
    check(len(got.items) == 1 and isinstance(pdf_curve, r3.NurbsCurve)
          and pdf_curve.Degree == 3 and len(pdf_curve.Points) == 4,
          f"a PDF cubic painting path imports as one degree-3 NURBS curve "
          f"({len(got.items)} item(s))")
    check(pdf_curve is not None
          and math.dist((pdf_curve.PointAtStart.X, pdf_curve.PointAtStart.Y),
                        (10.0, 20.0)) < 2e-3
          and math.dist((pdf_curve.PointAtEnd.X, pdf_curve.PointAtEnd.Y),
                        (40.0, 40.0)) < 2e-3,
          "PDF points preserve physical millimetre scale and orientation")

    modern_ai = os.path.join(TMP, "modern.ai")
    vector_export.write_ai(modern_ai, [vector_export.Stroke(beziers)], 100.0, 80.0)
    got_ai = ic.read_foreign(modern_ai, doc("Inches"))
    ai_curve = got_ai.items[0][0] if got_ai.items else None
    check(ai_curve is not None and ai_curve.Degree == 3
          and math.dist((ai_curve.PointAtStart.X, ai_curve.PointAtStart.Y),
                        (10.0 / 25.4, 20.0 / 25.4)) < 1e-4
          and any("PDF-compatible AI" in note for note in got_ai.notes),
          "a PDF-compatible AI follows the vector route and model units")

# Keep layer insertion and live-panel synchronization covered with an independent SVG.
layered_model, _layered_message = ic.open_foreign(layered_svg)
layer_names = [layered_model.file.Layers[int(obj.Attributes.LayerIndex)].Name
               for obj in layered_model.file.Objects]
check(layer_names == ["Cut", "Score", "Vector #0000FF"]
      and tuple(layered_model.file.Layers[1].Color)[:3] == (255, 0, 0)
      and tuple(layered_model.file.Layers[2].Color)[:3] == (0, 255, 0),
      f"SVG Open inserts curves on their source layers ({layer_names})")

native_cdr_source = os.path.join(TMP, "native-v15.cdr")
native_strokes = [
    vector_export.Stroke(
        beziers=(((10.0, 20.0), (20.0, 20.0), (30.0, 20.0), (40.0, 20.0)),),
        rgb=(1.0, 0.0, 0.0), width_mm=0.0762, layer="Cut"),
    vector_export.Stroke(
        beziers=(((50.0, 10.0), (55.0, 10.0), (60.0, 10.0), (65.0, 10.0)),),
        rgb=(0.0, 0.0, 1.0), width_mm=0.25, layer="Outline"),
]
cdr_writer.write_cdr(native_cdr_source, native_strokes, 100.0, 80.0, "Native import")
cdr_calls = []
original_tdraw = td.cdr_to_tdraw


def forbidden_cdr_tdraw(*args, **kwargs):
    cdr_calls.append((args, kwargs))
    raise AssertionError("native v15 import called TraceDraw")


td.cdr_to_tdraw = forbidden_cdr_tdraw
try:
    native_cdr_got = ic.read_foreign(native_cdr_source, doc("Inches"))
finally:
    td.cdr_to_tdraw = original_tdraw
native_layers = [layer for _geom, _base, layer in native_cdr_got.items]
native_first = native_cdr_got.items[0][0]
check(not cdr_calls and native_layers == ["Cut", "Outline"]
      and native_cdr_got.layers == {"Cut": (255, 0, 0), "Outline": (0, 0, 255)}
      and abs(native_first.PointAtStart.X - 10.0 / 25.4) < 1e-12
      and sorted(round(value, 4) for value in native_cdr_got.plot_widths.values())
          == [0.0762, 0.25]
      and any("without an external converter" in note for note in native_cdr_got.notes),
      "CDR v15 Import uses the native reader without TraceDraw and preserves layers/colours/widths")
native_opened, native_open_message = ic.open_foreign(native_cdr_source)
native_open_widths = sorted(round(float(obj.Attributes.PlotWeight), 4)
                            for obj in native_opened.file.Objects)
check(native_open_widths == [0.0762, 0.25]
      and "CorelDRAW v15 decoded directly" in native_open_message,
      "CDR v15 Open applies ordinary widths and exact Hairline to document attributes")
native_import_model = doc()
native_import_ctx = Ctx(native_import_model)
native_import_ok, native_import_message = ic.import_into(native_import_ctx, native_cdr_source)
native_import_widths = sorted(round(float(obj.Attributes.PlotWeight), 4)
                              for obj in native_import_model.file.Objects)
check(native_import_ok and native_import_widths == [0.0762, 0.25],
      "CDR v15 Import applies ordinary widths and exact Hairline through add_geometry UUIDs")

malformed_cdr = os.path.join(TMP, "malformed.cdr")
open(malformed_cdr, "wb").write(b"not a CDR package")
td.cdr_to_tdraw = forbidden_cdr_tdraw
try:
    try:
        ic.read_foreign(malformed_cdr, doc())
    except ValueError as exc:
        malformed_message = str(exc)
    else:
        malformed_message = ""
finally:
    td.cdr_to_tdraw = original_tdraw
check("malformed CorelDRAW file refused" in malformed_message and not cdr_calls,
      "malformed CDR is refused without silently switching to TraceDraw")
check(ic._cdr_unsupported_reason("the CDR contains an unsupported non-curve object")
      and ic._cdr_unsupported_reason("invalid CDR RIFF size") is None,
      "unsupported-v15 structure is classified for explicit fallback without admitting malformed RIFF")

cdr_source = os.path.join(TMP, "unsupported-v16.cdr")
with zipfile.ZipFile(native_cdr_source) as source_archive, zipfile.ZipFile(cdr_source, "w") as out:
    for info in source_archive.infolist():
        data = source_archive.read(info.filename)
        if info.filename == "content/riffData.cdr":
            data = bytearray(data)
            data[20:22] = (1600).to_bytes(2, "little")
            data = bytes(data)
        out.writestr(info, data)


def fake_cdr_tdraw(path, directory):
    cdr_calls.append(path)
    k = 0.5522847498307936

    def circle(cx, cy):
        return [{"MoveTo": {"x": cx + 1.0, "y": cy}},
                {"CurveTo": [{"x": cx + 1.0, "y": cy + k},
                             {"x": cx + k, "y": cy + 1.0},
                             {"x": cx, "y": cy + 1.0}]},
                {"CurveTo": [{"x": cx - k, "y": cy + 1.0},
                             {"x": cx - 1.0, "y": cy + k},
                             {"x": cx - 1.0, "y": cy}]},
                {"CurveTo": [{"x": cx - 1.0, "y": cy - k},
                             {"x": cx - k, "y": cy - 1.0},
                             {"x": cx, "y": cy - 1.0}]},
                {"CurveTo": [{"x": cx + k, "y": cy - 1.0},
                             {"x": cx + 1.0, "y": cy - k},
                             {"x": cx + 1.0, "y": cy}]}, "ClosePath"]

    black_shapes = [{"id": 20 + i, "kind": {"type": "path", "path": circle(5 + i * 5, 5),
                     "closed": True}, "transform": [1, 0, 0, 1, 0, 0],
                     "fill": {"fill": "none"}, "stroke": None, "visible": True,
                     "opacity": 1.0} for i in range(5)]
    compound = {"id": 10, "kind": {"type": "path", "path": [
        {"MoveTo": {"x": 0, "y": 0}}, {"LineTo": {"x": 25.4, "y": 0}},
        {"MoveTo": {"x": 0, "y": 10}},
        {"CurveTo": [{"x": 2, "y": 12}, {"x": 4, "y": 12}, {"x": 6, "y": 10}]}
    ], "closed": False}, "transform": [1, 0, 0, 1, 10, 20],
        "fill": {"fill": "none"},
        "stroke": {"color": {"model": "rgb", "r": 1, "g": 0, "b": 0},
                   "width": 0.1}, "visible": True, "opacity": 1.0}
    color_conflict = {"id": 30, "kind": {"type": "path", "path": [
        {"MoveTo": {"x": 2, "y": 15}}, {"LineTo": {"x": 8, "y": 15}}
    ], "closed": False}, "transform": [1, 0, 0, 1, 0, 0],
        "fill": {"fill": "none"},
        "stroke": {"color": {"model": "rgb", "r": 1, "g": 0, "b": 0},
                   "width": 0.2}, "visible": True, "opacity": 1.0}
    fixture = {"title": "Structured CDR fixture", "pages": [{"id": 1,
        "name": "Page 1", "size": {"width": 100.0, "height": 80.0}, "layers": [
            {"id": 2, "name": "Layer 2", "visible": True, "printable": True,
             "locked": False, "shapes": black_shapes + [color_conflict]},
            {"id": 3, "name": "Layer 1", "visible": True, "printable": True,
             "locked": False, "shapes": [compound]}
        ]}]}
    target = os.path.join(directory, "fixture.tdraw")
    with open(target, "w", encoding="utf-8") as stream:
        json.dump(fixture, stream)
    return target, ["warning: unsupported vector fill retained as boundary"]


td.cdr_to_tdraw = fake_cdr_tdraw
try:
    cdr_got = ic.read_foreign(cdr_source, doc("Inches"))
finally:
    td.cdr_to_tdraw = original_tdraw
cdr_layers = [layer for _geom, _base, layer in cdr_got.items]
first_curve = next((geom for geom, _base, layer in cdr_got.items if layer == "Layer 1"), None)
check(cdr_calls == [cdr_source] and len(cdr_got.items) == 8
      and cdr_layers.count("Layer 2") == 5
      and cdr_layers.count("Layer 2 [#FF0000]") == 1
      and cdr_layers.count("Layer 1") == 2
      and cdr_got.layers == {"Layer 2": (0, 0, 0),
                             "Layer 2 [#FF0000]": (255, 0, 0),
                             "Layer 1": (255, 0, 0)}
      and sorted(round(value, 4) for value in cdr_got.plot_widths.values())
          == [0.1, 0.1, 0.2]
      and any("vector fill" in note for note in cdr_got.notes)
      and any("used local TraceDraw" in note for note in cdr_got.notes),
      "unsupported CDR uses explicit structured TraceDraw fallback, retaining five secondary "
      "paths, both compound subpaths, source layers, colours, and warnings")
check(first_curve is not None
      and math.dist((first_curve.PointAtStart.X, first_curve.PointAtStart.Y),
                    (10.0 / 25.4, 20.0 / 25.4)) < 1e-12
      and abs(math.dist((first_curve.PointAtStart.X, first_curve.PointAtStart.Y),
                        (first_curve.PointAtEnd.X, first_curve.PointAtEnd.Y)) - 1.0) < 1e-12,
      "structured CDR transforms and millimetres map exactly into model units")
check(".cdr" in ic.FORMATS
      and any("*.cdr" in patterns for _label, patterns in ic.IMPORT_FILTERS),
      "Import/Open filters expose CorelDRAW files")

# Import mutates the existing model rather than calling AppState.set_model. The
# right Layers panel reads AppState.layers, so import must explicitly rebuild
# that live mirror after creating the SVG layers.
live_model = doc()
live_ctx = Ctx(live_model)
rebuild_calls = []
fake_state = types.ModuleType("narwhal3d.state")
fake_state.app_state = types.SimpleNamespace(
    ctx=live_ctx, model=live_model,
    _rebuild_layers=lambda: rebuild_calls.append(
        [info["name"] for info in live_model.layers()]))
prior_state = sys.modules.get("narwhal3d.state")
sys.modules["narwhal3d.state"] = fake_state
try:
    imported_ok, _imported_msg = ic.import_into(live_ctx, layered_svg)
finally:
    if prior_state is None:
        del sys.modules["narwhal3d.state"]
    else:
        sys.modules["narwhal3d.state"] = prior_state
check(imported_ok and rebuild_calls == [["Default", "Cut", "Score", "Vector #0000FF"]],
      f"SVG Import refreshes the live Layers panel mirror once ({rebuild_calls})")
check(".pdf" in ic.FORMATS and ".ai" in ic.FORMATS
      and any("*.pdf *.ai" in patterns for _label, patterns in ic.IMPORT_FILTERS),
      "Import/Open filters expose PDF and AI")

print("\n-- OBJ / STL meshes --")
V = [(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)]
T = [(0, 2, 3), (0, 3, 1), (4, 5, 7), (4, 7, 6), (0, 1, 5), (0, 5, 4),
     (2, 6, 7), (2, 7, 3), (0, 4, 6), (0, 6, 2), (1, 3, 7), (1, 7, 5)]
objp = os.path.join(TMP, "cube.obj")
obj_writer.write_obj(objp, [("Cube", V, T)])
got = ic.read_foreign(objp, doc())
mesh = got.items[0][0] if got.items else None
check(isinstance(mesh, r3.Mesh) and len(mesh.Faces) == 12 and len(mesh.Vertices) == 8
      and got.items[0][1] == "Mesh",
      f"an OBJ cube imports as a 12-triangle MESH object, named as one "
      f"({mesh and len(mesh.Faces)} faces, {mesh and len(mesh.Vertices)} vertices)")
stlp = os.path.join(TMP, "cube.stl")
stl_writer.write_stl(stlp, [(V[a], V[b], V[c]) for a, b, c in T])
got = ic.read_foreign(stlp, doc())
sm = got.items[0][0] if got.items else None
check(isinstance(sm, r3.Mesh) and len(sm.Faces) == 12 and len(sm.Vertices) == 8,
      f"an STL cube's 36 corners WELD to 8 vertices ({sm and len(sm.Vertices)})")

print("\n-- layer-index cache --")
class _CountLayer:
    def __init__(self, name, color):
        self.Name = name
        self.Color = tuple(color) + (255,)


class _CountLayers:
    def __init__(self, records):
        self.records = [_CountLayer(name, color) for name, color in records]
        self.length_reads = 0
        self.item_reads = 0

    def __len__(self):
        self.length_reads += 1
        return len(self.records)

    def __getitem__(self, index):
        self.item_reads += 1
        return self.records[index]


class _CountFile:
    def __init__(self, records):
        self.Layers = _CountLayers(records)


class _CountModel:
    def __init__(self, records=(('Default', (255, 255, 255)),)):
        self.file = _CountFile(records)
        self.added_layers = []
        self.objects = []

    def add_layer(self, name, color):
        index = len(self.file.Layers.records)
        self.file.Layers.records.append(_CountLayer(name, color))
        self.added_layers.append((name, tuple(color)))
        return index

    def add_object(self, geom, name, layer_index):
        self.objects.append((geom, name, layer_index))


class _CountCtx:
    def __init__(self, model):
        self.model = model
        self.added = []

    def note_change(self, _label=None):
        pass

    def add_geometry(self, geom, base_name, layer_index=None):
        self.added.append((geom, base_name, layer_index))


cache_model = _CountModel((('Default', (1, 2, 3)), ('Existing', (4, 5, 6))))
cache_items = [(object(), "Curve", "New") for _ in range(1200)]
cache_items += [(object(), "Curve", "Existing"), (object(), "Curve", "New")]
cache_got = ic.Imported()
cache_got.items = cache_items
cache_got.layers = {"New": (9, 8, 7), "Existing": (4, 5, 6)}
old_read_foreign = ic.read_foreign
ic.read_foreign = lambda _path, _model: cache_got
try:
    cache_ctx = _CountCtx(cache_model)
    cache_ok, _cache_message = ic.import_into(cache_ctx, "layers.svg")
finally:
    ic.read_foreign = old_read_foreign
check(cache_ok and len(cache_model.file.Layers.records) == 3
      and [layer.Name for layer in cache_model.file.Layers.records]
      == ["Default", "Existing", "New"]
      and cache_model.added_layers == [("New", (9, 8, 7))]
      and cache_model.file.Layers.length_reads == 1
      and cache_model.file.Layers.item_reads == 2
      and {index for _g, _b, index in cache_ctx.added} == {1, 2},
      f"Import caches existing layers and first-seen additions (len={cache_model.file.Layers.length_reads}, "
      f"items={cache_model.file.Layers.item_reads}, layers={[l.Name for l in cache_model.file.Layers.records]})")

# The Open route uses the same cache and must have the same bounded table access.
open_model_module = types.ModuleType("narwhal3d.model")
open_model_module.NarwhalModel = lambda: _CountModel((('Default', (1, 2, 3)),))
prior_model_module = sys.modules.get("narwhal3d.model")
sys.modules["narwhal3d.model"] = open_model_module
ic.read_foreign = lambda _path, _model: cache_got
try:
    opened, _open_message = ic.open_foreign("layers.svg")
finally:
    ic.read_foreign = old_read_foreign
    if prior_model_module is None:
        del sys.modules["narwhal3d.model"]
    else:
        sys.modules["narwhal3d.model"] = prior_model_module
check(opened is not None and [layer.Name for layer in opened.file.Layers.records]
      == ["Default", "New", "Existing"]
      and opened.file.Layers.length_reads == 1
      and opened.file.Layers.item_reads == 1
      and [obj[2] for obj in opened.objects[:2]] == [1, 1],
      f"Open uses the layer cache without changing insertion or assignments "
      f"(len={opened.file.Layers.length_reads}, items={opened.file.Layers.item_reads}, "
      f"layers={[l.Name for l in opened.file.Layers.records]}, "
      f"objects={[obj[2] for obj in opened.objects[:2]]})")

print("\n-- the command, and Open's hook --")
c = Ctx(doc())
cmd = type(commands.COMMANDS["import"])()
cmd.args = [dxf]
msg, _d = cmd.execute(c)
names = [c.model.file.Layers[i].Name for i in range(len(c.model.file.Layers))]
check(len(c.added) == 4 and "Cut" in names and msg.startswith("Imported 4 object(s)"),
      f"Import adds the 4 DXF objects, creating layer Cut ({msg!r})")
c = Ctx(doc())
cmd = type(commands.COMMANDS["import"])()
cmd.args = [os.path.join(TMP, "x.iges")]
open(cmd.args[0], "w").write("x")
msg, _d = cmd.execute(c)
check(not c.added and "not implemented yet" in msg, f"an unknown format is refused ({msg!r})")
m3, msg = ic.open_foreign(objp)
check(m3 is not None and m3.object_count() == 1
      and isinstance(m3.file.Objects[0].Geometry, r3.Mesh),
      f"open_foreign(path) returns a NEW model holding the file's objects ({msg!r})")

try:
    import bmesh  # noqa: F401
    bm = bridge.geometry_to_bmesh(mesh)
    check(len(bm.faces) == 12 and len(bm.verts) == 8,
          f"a MESH object is drawn as itself ({len(bm.faces)} faces)")
    bm.free()
except ImportError:
    print("  (the mesh display gate runs under blender -b)")

done()
