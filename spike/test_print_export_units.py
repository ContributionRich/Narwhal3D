"""Print and Export follow the DOCUMENT's units, not a hard-coded millimetre.

User report: "Print command always shows mm. It should mirror the working document
units, and print to those units. Similarly Export should export to those units."

MEASURED before this suite (inch document):
* the Print dialog showed "Letter  (215.9 x 279.4 mm)", "  Width (mm)", and took a
  typed custom size as millimetres;
* a printed / exported SVG wrote width="279.4000" with NO unit -- which SVG reads as
  CSS px (1/96 in), so the sheet opened at a quarter of its size -- and mm coordinates;
* a printed DXF wrote mm with $INSUNITS 4; Export .dxf rescaled an inch model by 25.4;
* -Print said "(= X mm per unit)" and "DXF SPLINE output in millimetres".

Reference-application documentation (the command help corpus):
* fileio/autocad_dwg_dxf_import_export: the reference application "sets [$INSUNITS] based
  on the document unit system to 1 for inches, 4 for millimeters, etc." -- DXF is written in model units;
* commands/print: Default line width "Ranges from Hairline to normal drafting widths
  (in millimeters)" -- so line widths stay mm in every document;
* commands/print: paper Size is "Paper sizes supported by the selected printer" -- no
  default page per unit system is documented, so the Letter default is kept.

Every check is chosen so the old code fails it: an inch document, where mm and the
document unit differ by 25.4, and a mm document, which must not change in meaning.

Run: scripts/run_suite.sh spike/test_print_export_units.py
"""
import importlib
import math
import os
import re
import sys
import tempfile
import types
import xml.etree.ElementTree as ET

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")
_pkg = types.ModuleType("narwhal3d")
_pkg.__path__ = [ADDON]
_pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = _pkg
pd = importlib.import_module("narwhal3d.printdialog")
p2 = importlib.import_module("narwhal3d.print2d")
dxf_reader = importlib.import_module("narwhal3d.dxf_reader")
print_commands = importlib.import_module("narwhal3d.print_commands")
ex = importlib.import_module("narwhal3d.export_commands")

TMP = tempfile.mkdtemp(prefix="narwhal_units_")
SVG_NS = "{http://www.w3.org/2000/svg}"
CSS_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "": 25.4 / 96.0, "px": 25.4 / 96.0}
results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((True, name, ""))
        except AssertionError as e:
            results.append((False, name, str(e)))
        except Exception as e:  # noqa: BLE001
            results.append((False, name, f"{type(e).__name__}: {e}"))
        return fn
    return deco


class Model:
    def __init__(self, unit):
        self.file = r3.File3dm()
        self.file.Settings.ModelUnitSystem = getattr(r3.UnitSystem, unit)


def two_unit_line(unit):
    """A document holding one line exactly 2 model units long, along X."""
    m = Model(unit)
    m.file.Objects.AddCurve(r3.LineCurve(r3.Point3d(-1, 0, 0), r3.Point3d(1, 0, 0)))
    return m


TOP = p2.View((1, 0, 0), (0, 1, 0), (0, 0, 0), 20.0, 20.0, name="Top")


def page_1to1(unit, size="Letter"):
    return p2.build_page(two_unit_line(unit), TOP,
                         p2.PageSetup(size=size, orientation="Landscape", scale=1.0))


def svg_facts(path):
    """``(width_attr, height_attr, viewbox, mm per user unit, stroke length in user
    units)`` -- the length from the first path's start to its end point."""
    root = ET.parse(path).getroot()
    w, h, vb = root.get("width"), root.get("height"), root.get("viewBox")
    num, unit = re.fullmatch(r"([0-9.]+)([a-z]*)", w).groups()
    vbw = float(vb.split()[2])
    mm_per_user = float(num) * CSS_MM[unit] / vbw
    d = root.find(f".//{SVG_NS}path").get("d")
    nums = [float(t) for t in re.findall(r"-?[0-9.]+", d)]
    length = math.dist(nums[:2], nums[-2:])
    return w, h, vb, mm_per_user, length


def dxf_header(path):
    lines = open(path).read().splitlines()
    return {lines[i + 1]: lines[i + 3] for i in range(len(lines) - 3)
            if lines[i].strip() == "9"}


# ---------------------------------------------------------------------------
# 1. the dialog shows page lengths in the document's paper unit
# ---------------------------------------------------------------------------

@check("inch document: page sizes, custom labels and Custom read in inches")
def _():
    st = pd.DialogState(unit="Inches")
    assert st.label_of("size", "Letter") == "Letter  (8.5 x 11 in)", st.label_of("size", "Letter")
    assert st.label_of("size", "A4") == "A4  (8.27 x 11.69 in)", st.label_of("size", "A4")
    assert st.row_label("custom_w") == "  Width (in)", st.row_label("custom_w")
    assert st.row_label("custom_h") == "  Height (in)", st.row_label("custom_h")
    st.size, st.custom_w, st.custom_h = "Custom", 215.9, 279.4
    assert st.shown("size") == "Custom  (8.5 x 11 in)", st.shown("size")
    assert st.shown("custom_w") == "8.5", st.shown("custom_w")


@check("mm document: the same rows still read in millimetres")
def _():
    st = pd.DialogState(unit="Millimeters")
    assert st.label_of("size", "A4") == "A4  (210 x 297 mm)", st.label_of("size", "A4")
    assert st.label_of("size", "Letter") == "Letter  (215.9 x 279.4 mm)"
    assert st.row_label("custom_w") == "  Width (mm)", st.row_label("custom_w")
    assert pd.DialogState().row_label("custom_h") == "  Height (mm)"


@check("line widths stay mm in an inch document (reference help: drafting widths in millimeters)")
def _():
    st = pd.DialogState(unit="Inches")
    assert st.label_of("width", "0.25") == "0.25 mm", st.label_of("width", "0.25")
    assert st.width_mm() == p2.DEFAULT_WIDTH_MM


@check("a custom size TYPED in inches is stored as the right millimetres")
def _():
    st = pd.DialogState(unit="Inches", size="Custom", custom_w=210.0, custom_h=297.0)
    pd._focus_field(st, "custom_w")
    assert st.edit_text == "8.2677", st.edit_text           # 210 mm, shown in inches
    st.edit_text = "8.5"
    assert abs(st.live_value("custom_w") - 215.9) < 1e-9, st.live_value("custom_w")
    assert pd.commit_field(st) is True
    assert abs(st.custom_w - 215.9) < 1e-9, st.custom_w
    st.focus, st.edit_text = "custom_h", "11"
    pd.commit_field(st)
    w, h = p2.page_size(st.setup())
    assert abs(w - 215.9) < 1e-9 and abs(h - 279.4) < 1e-9, (w, h)


@check("the default Custom sheet is Letter (8.5 x 11 in) on an inch document, A4 on mm")
def _():
    st = pd.DialogState(unit="Inches", size="Custom")
    assert (st.custom_w, st.custom_h) == (215.9, 279.4), (st.custom_w, st.custom_h)
    assert st.shown("size") == "Custom  (8.5 x 11 in)", st.shown("size")
    assert p2.page_size(st.setup()) == (215.9, 279.4)
    for unit in ("Millimeters", "Centimeters"):
        mm = pd.DialogState(unit=unit, size="Custom")
        assert (mm.custom_w, mm.custom_h) == (210.0, 297.0), (unit, mm.custom_w)
    assert (pd.DialogState().custom_w, pd.DialogState().custom_h) == (210.0, 297.0)


@check("remembered settings: a saved DEFAULT sheet yields to the document's; a typed one stays")
def _():
    # the dialog saves custom_w/h on every close, so a mm session leaves 210 x 297 behind
    st = pd.restore_settings(pd.DialogState(unit="Inches", view="Top"),
                             {"custom_w": 210.0, "custom_h": 297.0, "size": "A4",
                              "view": "Front"})
    assert (st.custom_w, st.custom_h) == (215.9, 279.4), (st.custom_w, st.custom_h)
    assert st.size == "A4" and st.view == "Top", (st.size, st.view)
    st = pd.restore_settings(pd.DialogState(unit="Millimeters"),
                             {"custom_w": 215.9, "custom_h": 279.4})
    assert (st.custom_w, st.custom_h) == (210.0, 297.0), (st.custom_w, st.custom_h)
    st = pd.restore_settings(pd.DialogState(unit="Inches"),
                             {"custom_w": 300.0, "custom_h": 400.0})
    assert (st.custom_w, st.custom_h) == (300.0, 400.0), (st.custom_w, st.custom_h)


@check("the custom-size limit is checked in mm and reported in inches")
def _():
    st = pd.DialogState(unit="Inches", size="Custom", custom_w=215.9)
    st.focus, st.edit_text = "custom_w", "190"               # 4826 mm: allowed
    assert pd.commit_field(st) and abs(st.custom_w - 4826.0) < 1e-9, st.custom_w
    st.focus, st.edit_text = "custom_w", "200"               # 5080 mm: too big
    assert pd.commit_field(st) is False and abs(st.custom_w - 4826.0) < 1e-9
    assert "196.8504 in" in st.message, st.message


@check("an inch dialog still lays out: every label and value fits its box")
def _():
    st = pd.DialogState(unit="Inches", size="Custom", views=("Top",), view="Top")
    for scale in (1.0, 1.5):
        lay = pd.layout(1200 * scale, 800 * scale, scale, st)
        fs = 13.0 * scale
        for rid, lr, vr in lay.rows:
            need = pd.text_width(st.row_label(rid), fs)
            assert lr.w >= need, (rid, lr.w, need)
            if pd.ROW_KINDS[rid] != "check":
                txt = pd.text_width(st.shown(rid), fs) + pd.FIELD_TEXT_PAD * scale
                assert vr.w >= txt, (rid, vr.w, txt)


@check("the dialog's status text gives the page in inches")
def _():
    st = pd.DialogState(unit="Inches")
    assert st.page_text(page_1to1("Inches")) == "11 x 8.5 in", st.page_text(page_1to1("Inches"))


# ---------------------------------------------------------------------------
# 2. Print OUTPUT: declared in the paper unit, physically exact
# ---------------------------------------------------------------------------

@check("inch print SVG: width/height/viewBox in inches; a 2 in line at 1:1 is 2 in")
def _():
    page = page_1to1("Inches")
    assert page.unit == "Inches" and abs(page.width_mm - 279.4) < 1e-9, page.unit
    p = os.path.join(TMP, "inch.svg")
    p2.write(p, page)
    w, h, vb, mm_per, length = svg_facts(p)
    assert (w, h) == ("11.000000in", "8.500000in"), (w, h)
    assert vb == "0 0 11.000000 8.500000", vb
    assert abs(length - 2.0) < 1e-6, length                 # in inches
    assert abs(length * mm_per - 50.8) < 1e-5, length * mm_per


@check("mm print SVG: declared mm (was unitless = px); a 2 mm line is 2 mm")
def _():
    p = os.path.join(TMP, "mm.svg")
    p2.write(p, page_1to1("Millimeters"))
    w, h, vb, mm_per, length = svg_facts(p)
    assert (w, h) == ("279.4000mm", "215.9000mm"), (w, h)
    assert abs(length - 2.0) < 1e-6 and abs(mm_per - 1.0) < 1e-12, (length, mm_per)


@check("print DXF: $INSUNITS / $MEASUREMENT and coordinates in the paper unit")
def _():
    for unit, insunits, measurement in (("Inches", 1, 0), ("Millimeters", 4, 1)):
        p = os.path.join(TMP, f"print-{unit}.dxf")
        p2.write(p, page_1to1(unit))
        hdr = dxf_header(p)
        assert hdr.get("$INSUNITS") == str(insunits), (unit, hdr)
        assert hdr.get("$MEASUREMENT") == str(measurement), (unit, hdr)
        data = dxf_reader.read_dxf(p)
        pts = data["entities"][0]["points"]
        assert data["insunits"] == insunits
        assert abs(math.dist(pts[0][:2], pts[-1][:2]) - 2.0) < 1e-6, (unit, pts)


@check("print PDF stays physical: an inch Letter landscape is 792 x 612 pt")
def _():
    p = os.path.join(TMP, "inch.pdf")
    p2.write(p, page_1to1("Inches"))
    box = re.search(rb"/MediaBox \[0 0 ([0-9.]+) ([0-9.]+)\]", open(p, "rb").read())
    assert box and abs(float(box[1]) - 792) < 1e-3 and abs(float(box[2]) - 612) < 1e-3, box


@check("-Print reports the page and the Fit scale in inches for an inch document")
def _():
    class Ctx:
        model = two_unit_line("Inches")

    print_commands.view_provider = lambda: TOP
    try:
        cmd = type(print_commands.commands.COMMANDS["print"])()
        cmd.args = [os.path.join(TMP, "fit.dxf")]
        msg, _done = cmd.execute(Ctx())
    finally:
        print_commands.view_provider = None
    # Fit: 215.9 mm / 20 units = 10.795 mm = 0.425 in of paper per model unit
    assert "(11 x 8.5 in)" in msg, msg
    assert "(= 0.425 in on paper per model unit)" in msg, msg
    assert "DXF SPLINE output in inches" in msg and " mm" not in msg, msg


# ---------------------------------------------------------------------------
# 3. Export: the document's units, declared where the format records them
# ---------------------------------------------------------------------------

@check("Export .dxf: model coordinates, $INSUNITS of the document (mm 4, cm 5, in 1)")
def _():
    for unit, code in (("Millimeters", 4), ("Centimeters", 5), ("Inches", 1)):
        m = Model(unit)
        m.file.Objects.AddCurve(r3.Circle(r3.Point3d(0, 0, 0), 1.5).ToNurbsCurve())
        p = os.path.join(TMP, f"export-{unit}.dxf")
        ok, msg = ex.export_objects(m, list(m.file.Objects), p)
        assert ok, msg
        data = dxf_reader.read_dxf(p)
        assert data["insunits"] == code, (unit, data["insunits"])
        assert data["entities"][0]["radius"] == 1.5, (unit, data["entities"][0])
        assert f"in {unit.lower()}" in msg, msg


@check("Export .svg of an inch drawing: declared in inches, a 2 in line is 2 in")
def _():
    m = two_unit_line("Inches")
    print_commands.view_provider = lambda: TOP
    try:
        p = os.path.join(TMP, "export-inch.svg")
        ok, msg = ex.export_objects(m, list(m.file.Objects), p)
    finally:
        print_commands.view_provider = None
    assert ok, msg
    w, _h, _vb, mm_per, length = svg_facts(p)
    assert w == "2.000000in", w
    assert abs(length - 2.0) < 1e-6 and abs(length * mm_per - 50.8) < 1e-5, (length, mm_per)
    assert "(2.0000 x " in msg and msg.rstrip(")").endswith(" in"), msg


@check("Export .stl/.obj of an inch box writes model numbers as-is (no unit in format)")
def _():
    from narwhal3d.kernel import occt_backend as ob

    if not ob.available():
        print("  skip  STL/OBJ check needs OCCT")
        return
    bridge = importlib.import_module("narwhal3d.bridge")
    stl_writer = importlib.import_module("narwhal3d.stl_writer")
    m = Model("Inches")
    m.file.Objects.AddBrep(bridge.make_box((0, 0, 0), (1, 1, 0), 1))
    p = os.path.join(TMP, "inch.stl")
    ok, msg = ex.export_objects(m, list(m.file.Objects), p)
    assert ok, msg
    coords = {round(c, 6) for t in stl_writer.read_stl(p) for v in t for c in v}
    assert coords == {0.0, 1.0}, sorted(coords)


for ok, name, why in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {why}" if why else ""))
passed = sum(ok for ok, _n, _w in results)
print(f"{passed}/{len(results)} print/export unit checks passed")
sys.exit(0 if passed == len(results) else 1)
