"""Headless-Blender tests for .3dm, STEP, AI, PDF, and SVG export.

Outputs are reopened to verify analytic solids, STEP surfaces, and 1:1 vector geometry.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import zipfile

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import rhino3dm as r3  # noqa: E402

from narwhal3d import cdr_reader, filedialog, kernel, print2d, print_commands, tracedraw  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402
from narwhal3d.state import app_state  # noqa: E402

results = []


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))


TMP = tempfile.mkdtemp(prefix="narwhal_export_")
TOP = print2d.View((1, 0, 0), (0, 1, 0), (0, 0, 0), 200.0, 150.0)


def last():
    return app_state.history[-1] if app_state.history else ""


def new_names(before):
    return sorted({o.name for o in bpy.data.objects} - before)


def select(names):
    for o in bpy.data.objects:
        o.select_set(o.name in names)


def export(names, path, hairline=False):
    app_state.active_command = None
    select(names)
    app_state.feed_line("Export")
    if hairline:
        app_state.feed_line("Hairline")
    app_state.feed_line(path)
    return last()


def run():
    filedialog.set_backend(None)            # headless -> typed names
    print_commands.view_provider = lambda: TOP
    app_state.set_model(NarwhalModel())
    app_state.active_command = None
    b = {o.name for o in bpy.data.objects}
    for t in ("Cylinder", "w0,0,0", "5", "10"):
        app_state.feed_line(t)
    cyl = new_names(b)
    b = {o.name for o in bpy.data.objects}
    for t in ("Circle", "w30,0,0", "10"):
        app_state.feed_line(t)
    circ = new_names(b)
    n_model = len(app_state.model.file.Objects)
    check(cyl and circ and n_model == 2, "setup: a cylinder and a circle",
          f"{cyl} {circ} {n_model}")

    # 1. .3dm: only the selection, and the solid reopens ANALYTIC
    p = os.path.join(TMP, "sel.3dm")
    msg = export(cyl, p)
    f = r3.File3dm.Read(p) if os.path.exists(p) else None
    check(f is not None and len(f.Objects) == 1, "Export .3dm writes ONLY the "
          "selected object", f"{msg!r}")
    reopened = NarwhalModel.load(p) if f is not None else None
    geom = reopened.file.Objects[0].Geometry if reopened else None
    # a primitive carries a RECIPE (not a master -- MEASURED); after the round trip
    # through the exported file it must still rebuild as the exact OCCT solid
    from narwhal3d import bridge
    shape = bridge._occt_operand(geom) if geom is not None else None
    n_faces = None
    if shape is not None:
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        ex, n_faces = TopExp_Explorer(shape, TopAbs_FACE), 0
        while ex.More():
            n_faces += 1
            ex.Next()
    check(kernel.recipe_of(geom) if geom is not None else False,
          "the exported solid keeps its RECIPE through the .3dm", f"{geom}")
    check(shape is not None and n_faces == 3,
          "and reopens as the exact 3-face cylinder (rule 0 survives export)",
          f"faces={n_faces}")
    check(len(app_state.model.file.Objects) == n_model and app_state.doc_path is None,
          "the model itself is unchanged and does not become the exported file",
          f"{app_state.doc_path}")

    # 2. .step: analytic surfaces in the file
    p = os.path.join(TMP, "cyl.step")
    msg = export(cyl, p)
    text = open(p).read() if os.path.exists(p) else ""
    check("CYLINDRICAL_SURFACE" in text and text.count("PLANE(") <= 2,
          "Export .step carries a CYLINDRICAL_SURFACE, not facets",
          f"{msg!r} planes={text.count('PLANE(')}")

    # 3. a curve to STEP: refused by name, nothing written
    p = os.path.join(TMP, "circle.step")
    msg = export(circ, p)
    check(not os.path.exists(p) and "REFUSED" in msg,
          "a curve to STEP is refused by name and nothing is written", f"{msg!r}")

    # 4. .ai at 1:1: the page IS the drawing (20 x 20 mm = 56.69 pt)
    p = os.path.join(TMP, "circle.ai")
    msg = export(circ, p)
    info = ""
    if os.path.exists(p) and shutil.which("pdfinfo"):
        info = subprocess.run(["pdfinfo", p], capture_output=True, text=True).stdout
    size = re.search(r"Page size:\s+([\d.]+) x ([\d.]+) pts", info)
    want = 20.0 * 72 / 25.4
    check(os.path.exists(p) and (not shutil.which("pdfinfo") or (
        size and abs(float(size.group(1)) - want) < 0.05
        and abs(float(size.group(2)) - want) < 0.05)),
          "Export .ai is the drawing at 1:1: a r=10 circle -> a 20 x 20 mm page",
          f"{msg!r} {size.groups() if size else None}")

    # 5. .svg / .pdf remain independent 2-D vector outputs.
    for ext in ("svg", "pdf"):
        p = os.path.join(TMP, f"circle.{ext}")
        msg = export(circ, p)
        check(os.path.exists(p), f"Export .{ext} writes the file", f"{msg!r}")

    cdr_calls = []
    original_cdr = tracedraw.svg_to_cdr

    def forbidden_cdr(*args, **kwargs):
        cdr_calls.append((args, kwargs))
        raise AssertionError("native v15 export called TraceDraw")

    tracedraw.svg_to_cdr = forbidden_cdr
    try:
        p = os.path.join(TMP, "circle.cdr")
        msg = export(circ, p)
    finally:
        tracedraw.svg_to_cdr = original_cdr
    version = 0
    if os.path.exists(p):
        with zipfile.ZipFile(p) as archive:
            riff = archive.read("content/riffData.cdr")
            version = int.from_bytes(riff[20:22], "little")
    check(os.path.exists(p) and version == 1500 and not cdr_calls
          and "native CorelDRAW v15" in msg,
          "Export .cdr writes native 1:1 CorelDRAW v15 without TraceDraw", f"{msg!r}")
    hairline_path = os.path.join(TMP, "circle-hairline.cdr")
    hairline_msg = export(circ, hairline_path, hairline=True)
    hairline_drawing = cdr_reader.read_cdr(hairline_path) \
        if os.path.exists(hairline_path) else None
    check(hairline_drawing is not None
          and all(obj.hairline and abs(obj.width_mm - 0.0762) < 1e-12
                  for obj in hairline_drawing.objects),
          "Export Hairline command option writes genuine 0.003-inch CorelDRAW outlines",
          f"{hairline_msg!r}")

    # 6. perspective: a 2-D export cannot preserve scale -> refused
    print_commands.view_provider = lambda: print2d.View(
        (1, 0, 0), (0, 1, 0), (0, 0, 0), 1, 1, perspective=True)
    p = os.path.join(TMP, "persp.ai")
    msg = export(circ, p)
    check(not os.path.exists(p) and "PERSPECTIVE" in msg and "scale" in msg,
          "a 2-D export from a perspective view is refused -- it cannot preserve "
          "scale", f"{msg!r}")
    print_commands.view_provider = lambda: TOP

    # 7. an unsupported format is refused by name
    p = os.path.join(TMP, "x.iges")
    msg = export(cyl, p)
    check(not os.path.exists(p) and "not implemented" in msg,
          "Export .iges is refused by name", f"{msg!r}")

    # 8. an inch model exports to STEP in inches (rather than being refused while
    #    OCCT wrote millimetres); spike/test_export_formats.py holds the full gate
    app_state.model.file.Settings.ModelUnitSystem = r3.UnitSystem.Inches
    p = os.path.join(TMP, "inch.step")
    msg = export(cyl, p)
    text = open(p).read() if os.path.exists(p) else ""
    check("CONVERSION_BASED_UNIT('INCH'" in text and "Inches" in msg,
          "STEP export of an inch model declares INCH (not a mislabelled mm part)",
          f"{msg!r}")
    app_state.model.file.Settings.ModelUnitSystem = r3.UnitSystem.Millimeters

    # 9. nothing selected: Export ASKS for the objects
    app_state.active_command = None
    select([])
    app_state.feed_line("Export")
    asking = (app_state.active_command is not None
              and app_state.active_command.step_index == 0)
    app_state.cancel()
    check(asking, "with nothing selected, Export asks for the objects first")

    # 10. two solids -> ONE STEP file holding both (a compound)
    b = {o.name for o in bpy.data.objects}
    for t in ("Box", "w20,20,0", "w30,30,0", "10"):
        app_state.feed_line(t)
    box = new_names(b)
    p = os.path.join(TMP, "two.step")
    msg = export(cyl + box, p)
    text = open(p).read() if os.path.exists(p) else ""
    check("Exported 2 solid" in msg and "CYLINDRICAL_SURFACE" in text
          and text.count("MANIFOLD_SOLID_BREP") == 2,
          "two solids export to one STEP file as two solids", f"{msg!r} "
          f"solids={text.count('MANIFOLD_SOLID_BREP')}")

    from narwhal3d import commands
    check(commands.COMMANDS.get("export").__class__.__module__.endswith(
        "export_commands"), "the registry's 'export' uses export_commands")


try:
    run()
except Exception:  # noqa: BLE001
    results.append((False, "suite raised", traceback.format_exc()))
finally:
    filedialog.set_backend(None)
    print_commands.view_provider = None
    shutil.rmtree(TMP, ignore_errors=True)

failed = [r for r in results if not r[0]]
for ok, label, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok else ""))
print(f"\n{len(results) - len(failed)}/{len(results)} export checks passed")
if failed:
    raise SystemExit(1)
