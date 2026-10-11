"""Headless-Blender command, geometry, model, and .3dm round-trip tests."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bpy  # noqa: E402


def run_command(st, line):
    st.feed_line(line)


failures = []

# --- register the add-on package as a module (bypasses Blender's addon loader)
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "narwhal3d", os.path.join(ROOT, "narwhal3d", "__init__.py"),
    submodule_search_locations=[os.path.join(ROOT, "narwhal3d")])
narwhal3d = importlib.util.module_from_spec(spec)
sys.modules["narwhal3d"] = narwhal3d
spec.loader.exec_module(narwhal3d)

from narwhal3d.state import app_state as st  # noqa: E402
from narwhal3d import analysis  # noqa: E402

# --- run a battery of commands through the state machine
run_command(st, "Line")
run_command(st, "1,2,3")
run_command(st, "5,6,7")

run_command(st, "Circle")
run_command(st, "0,0,0")
run_command(st, "4")

run_command(st, "Box")
run_command(st, "0,0,0")
run_command(st, "3,2,0")
run_command(st, "1.5")

run_command(st, "Sphere")
run_command(st, "10,0,0")
run_command(st, "2")

run_command(st, "Cylinder")
run_command(st, "-5,0,0")
run_command(st, "1.5")
run_command(st, "3")

run_command(st, "Polyline")
run_command(st, "0,0,0")
run_command(st, "2,2,0")
run_command(st, "4,1,0")
run_command(st, "")  # empty Enter finishes variable-point command

# --- verify Blender objects were created
names = {o.name for o in bpy.context.scene.objects}
expected_prefixes = ("Line", "Circle", "Box", "Sphere", "Cylinder", "Polyline")
created = [n for n in names if any(n.startswith(p) for p in expected_prefixes)]
print("created objects:", sorted(created))
if len(created) < 6:
    failures.append(f"expected >=6 created objects, got {len(created)}")

# --- verify the model file has matching geometry
model = st.model
print("model object count:", model.object_count())
if model.object_count() < 6:
    failures.append(f"model has only {model.object_count()} objects")

# --- extrude the circle we made
#
# ExtrudeCrv now takes its curve from the SELECTION when there is one
# (Command.preselect_names), because the reference CAD application's documented steps are "Select a curve.
# Specify a distance." -- there is no curve prompt.  Deselect first so this drives
# the NAMED path, which must keep working: the selection is a shortcut, not a
# replacement.
run_command(st, "SelNone")
run_command(st, "ExtrudeCrv")
run_command(st, "Circle1")
run_command(st, "5")
names = {o.name for o in bpy.context.scene.objects}
extrusions = [n for n in names if n.startswith("Extrusion")]
print("extrusions:", extrusions)
if not extrusions:
    failures.append("ExtrudeCrv did not create an object")

# --- offset the circle (same reason: drive the named path with nothing selected)
run_command(st, "SelNone")
run_command(st, "Offset")
run_command(st, "Circle1")
run_command(st, "2")
names = {o.name for o in bpy.context.scene.objects}
offsets = [n for n in names if n.startswith("Offset")]
print("offsets:", offsets)
if not offsets:
    failures.append("Offset did not create an object")

# Exercise the selection-driven path: select the curve and provide only a distance.
run_command(st, "Select")
run_command(st, "Circle1")
before = {o.name for o in bpy.context.scene.objects}
run_command(st, "ExtrudeCrv")
run_command(st, "6")
made = {o.name for o in bpy.context.scene.objects} - before
print("extrude from selection made:", sorted(made))
if not any(n.startswith("Extrusion") for n in made):
    failures.append("ExtrudeCrv did not extrude the selected curve")
run_command(st, "SelNone")

# --- move the box (select it first via Select command)
run_command(st, "Select")
run_command(st, "Box1")
run_command(st, "Move")
# Move takes two points; use the origin as the base point.
run_command(st, "0,0,0")
run_command(st, "1,0,0")
box = bpy.data.objects.get("Box1")
if box is None:
    failures.append("Box1 missing after Select/Move")
else:
    t = tuple(round(v, 6) for v in box.matrix_world.translation)
    print("Box1 matrix_world translation:", t)
    if abs(t[0] - 1.0) > 1e-6:
        failures.append(f"Move did not translate Box1: {t}")

# --- delete the sphere
run_command(st, "Select")
run_command(st, "Sphere1")
count_before_delete = model.object_count()
run_command(st, "Delete")
if bpy.data.objects.get("Sphere1") is not None:
    failures.append("Sphere1 still exists after Delete")
# Delete must also remove the object from the File3dm (not just the scene)
if model.object_count() != count_before_delete - 1:
    failures.append(f"Delete did not remove Sphere1 from the model file: "
                    f"{count_before_delete} -> {model.object_count()}")

# --- analysis commands report correct numbers
run_command(st, "Length")
run_command(st, "Line1")
last = st.history[-1]
print("length:", last)
if "6.928" not in last:  # |p1-p2| of (1,2,3)->(5,6,7) is sqrt(48)=6.9282
    failures.append(f"Length wrong: {last}")

run_command(st, "Area")
run_command(st, "Circle1")
last = st.history[-1]
print("area:", last)
# 16*pi = 50.2655; the message prints 6 significant digits
if "50.264" not in last and "50.265" not in last:
    failures.append(f"Area wrong: {last}")

run_command(st, "Volume")
run_command(st, "Box1")
last = st.history[-1]
print("volume:", last)
if "9" not in last:  # box is 3 x 2 x 1.5
    failures.append(f"Volume wrong: {last}")

run_command(st, "Distance")
run_command(st, "0,0,0")
run_command(st, "3,4,0")
last = st.history[-1]
print("distance:", last)
if "5" not in last:
    failures.append(f"Distance wrong: {last}")

run_command(st, "BoundingBox")
run_command(st, "Box1")
last = st.history[-1]
print("bbox:", last)
if "min (" not in last or "size (" not in last:
    failures.append(f"BoundingBox wrong: {last}")

# --- copy / array / mirror create correctly-placed duplicates
# (Box1 was moved to x=1 earlier, so it now spans x in [1,4])
# NOTE: add_geometry bakes world coords into mesh data and leaves the object
# origin at 0, so we verify positions via mesh vertex bounds, not ob.location.
def _bounds(ob):
    xs = [v.co.x for v in ob.data.vertices]
    ys = [v.co.y for v in ob.data.vertices]
    return min(xs), max(xs), min(ys), max(ys)

run_command(st, "Select")
run_command(st, "Box1")
run_command(st, "Copy")
# Copy takes two points and remains open for repeated destinations until Enter.
run_command(st, "0,0,0")
run_command(st, "10,0,0")
run_command(st, "")                   # Enter -> build the copy + finish
copies = [o for o in bpy.data.objects if o.name.startswith("Copy")]
print("copies:", sorted(o.name for o in copies))
if len(copies) != 1:
    failures.append(f"expected 1 copy, got {len(copies)}")
else:
    x0, x1 = _bounds(copies[0])[0], _bounds(copies[0])[1]
    # original spans [1,4]; +10 delta -> [11,14]
    if abs(x0 - 11.0) > 1e-6 or abs(x1 - 14.0) > 1e-6:
        failures.append(f"Copy x-range wrong: [{x0:.3f}, {x1:.3f}], expected [11,14]")

run_command(st, "ArrayPolar")
run_command(st, "5,0,0")   # center
run_command(st, "4")       # count -- the reference CAD application counts the ORIGINAL, so 3 copies
run_command(st, "z")
polars = sorted([o for o in bpy.data.objects if o.name.startswith("Array")], key=lambda o: o.name)
print("polar array:", len(polars), "elements")
if len(polars) != 3:
    failures.append(f"expected 3 polar elements (count=4 includes the "
                    f"original), got {len(polars)}")
else:
    import math as _m
    # Box1 center is at (2.5, 1); distance to array center (5,0) is r
    cx, cy = 2.5, 1.0
    axc, ayc = 5.0, 0.0
    r = _m.dist((cx, cy), (axc, ayc))
    base_ang = _m.atan2(cy - ayc, cx - axc)
    for i in range(3):
        ang = base_ang + _m.radians(90 * (i + 1))
        ex = axc + r * _m.cos(ang)
        ey = ayc + r * _m.sin(ang)
        # element center must be at the rotated position
        x0, x1, y0, y1 = _bounds(polars[i])
        ecx, ecy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if abs(ecx - ex) > 1e-3 or abs(ecy - ey) > 1e-3:
            failures.append(f"polar element {i} center ({ecx:.3f},{ecy:.3f}), "
                            f"expected ({ex:.3f},{ey:.3f})")
    # no element may land back on the original (the old off-by-one bug put the
    # last copy at a full 360 deg turn, exactly on top of Box1)
    for i, ob in enumerate(polars):
        x0, x1, y0, y1 = _bounds(ob)
        ecx, ecy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if abs(ecx - cx) < 1e-6 and abs(ecy - cy) < 1e-6:
            failures.append(f"polar element {i} is coincident with the original")

run_command(st, "Mirror")
run_command(st, "0,0,0")   # mirror line along Y axis -> vertical plane x=0
run_command(st, "0,5,0")
mirrors = [o for o in bpy.data.objects if o.name.startswith("Mirror")]
print("mirrors:", sorted(o.name for o in mirrors))
if len(mirrors) != 1:
    failures.append(f"expected 1 mirror image, got {len(mirrors)}")
else:
    # Box1 spans x [1,4]; mirrored across x=0 it must span [-4,-1]
    x0, x1 = _bounds(mirrors[0])[0], _bounds(mirrors[0])[1]
    if abs(x0 - (-4.0)) > 1e-6 or abs(x1 - (-1.0)) > 1e-6:
        failures.append(f"mirror x-range wrong: [{x0:.3f}, {x1:.3f}], expected [-4,-1]")

# --- linear array: the reference CAD application's count INCLUDES the original, so count=3 -> 2 copies
run_command(st, "Select")
run_command(st, "Box1")
before_arr = {o.name for o in bpy.data.objects if o.name.startswith("Array")}
run_command(st, "Array")
run_command(st, "3")         # count (includes the original)
run_command(st, "20,0,0")    # direction
new_arr = sorted({o.name for o in bpy.data.objects
                  if o.name.startswith("Array")} - before_arr)
print("linear array:", new_arr)
if len(new_arr) != 2:
    failures.append(f"expected 2 linear array copies (count=3 includes the "
                    f"original), got {len(new_arr)}")
else:
    # Box1 spans x [1,4]; copies must sit at +20 and +40
    for i, name in enumerate(new_arr):
        ob = bpy.data.objects[name]
        x0, x1 = _bounds(ob)[0], _bounds(ob)[1]
        want = 1.0 + 20.0 * (i + 1)
        if abs(x0 - want) > 1e-6 or abs(x1 - (want + 3.0)) > 1e-6:
            failures.append(f"linear array element {i} x-range "
                            f"[{x0:.3f}, {x1:.3f}], expected "
                            f"[{want:.3f}, {want + 3.0:.3f}]")

# a count below 2 is meaningless and must be rejected, not silently do nothing
before_arr = {o.name for o in bpy.data.objects if o.name.startswith("Array")}
run_command(st, "Array")
run_command(st, "1")
run_command(st, "5,0,0")
if {o.name for o in bpy.data.objects if o.name.startswith("Array")} != before_arr:
    failures.append("Array with count=1 created objects")
if "Count must be 2 or more" not in st.history[-1]:
    failures.append(f"Array count=1 did not report an error: {st.history[-1]!r}")

# --- union of two overlapping boxes ---------------------------------------
# A [0..3]x[0..2]x[0..2], B [1.5..4.5]x[0..2]x[0..2]; overlap x in [1.5,3].
run_command(st, "Box")
run_command(st, "0,0,0")
run_command(st, "3,2,0")
run_command(st, "2")          # -> Box2
run_command(st, "Box")
run_command(st, "1.5,0,0")
run_command(st, "4.5,2,0")
run_command(st, "2")          # -> Box3
run_command(st, "Select")
run_command(st, "all")
# Deselect because this block exercises the typed-name path. BooleanUnion opts into
# selection fill, so leaving everything selected would make it run on the whole scene
# -- which includes curves from earlier blocks and is correctly refused as "not a
# solid". `Select all` is kept above because it is the Select command's own coverage;
# only its side effect is undone here. Deselect through the view layer because app-drawn
# curves are absent from `bpy.context.selected_objects`.
for _ob in [o for o in bpy.context.view_layer.objects.selected if o is not None]:
    _ob.select_set(False)
run_command(st, "BooleanUnion")   # ONE objects_multi step: name the operands, Enter
# Typed names accumulate until an empty Enter confirms the BooleanUnion set.
run_command(st, "Box2")
run_command(st, "Box3")
run_command(st, "")              # <- the Enter
booleans = [o for o in bpy.data.objects if o.name.startswith("Boolean")]
print("boolean results:", sorted(o.name for o in booleans))
if len(booleans) != 1:
    failures.append(f"expected exactly 1 boolean result, got {len(booleans)}")
else:
    res = booleans[0]
    # inputs must be consumed (removed from the scene)
    if bpy.data.objects.get("Box2") is not None or bpy.data.objects.get("Box3") is not None:
        failures.append("BooleanUnion did not remove its input objects")
    geom = st.ctx.geom_by_name(res.name)
    if geom is None:
        failures.append(f"boolean result {res.name} has no geometry in the model")
    else:
        bb = geom.GetBoundingBox()
        # union extent: x [0..4.5], y [0..2], z [0..2]
        if not (abs(bb.Min.X) < 1e-3 and abs(bb.Max.X - 4.5) < 1e-3
                and abs(bb.Min.Y) < 1e-3 and abs(bb.Max.Y - 2.0) < 1e-3):
            failures.append(f"union bbox wrong: X[{bb.Min.X:.3f},{bb.Max.X:.3f}] "
                            f"Y[{bb.Min.Y:.3f},{bb.Max.Y:.3f}]")
        # volume of the union = 4.5 * 2 * 2 = 18 (mesh-derived, small tolerance)
        vol = analysis.solid_volume(geom, 2, 2)
        print("union volume:", round(vol, 4), "(expected ~18)")
        if abs(vol - 18.0) > 0.3:
            failures.append(f"union volume {vol:.4f} not within tolerance of 18")

# difference: Box2 - Box3 keeps only A's non-overlap -> x [0..1.5], vol = 6
run_command(st, "Box")
run_command(st, "0,0,0")
run_command(st, "3,2,0")
run_command(st, "2")          # -> Box4
run_command(st, "Box")
run_command(st, "1.5,0,0")
run_command(st, "4.5,2,0")
run_command(st, "2")          # -> Box5
# BooleanDifference collects and confirms two independent operand sets. Feeding both
# names without an Enter between them
# would put them in the same set, which is a different operation. Deselect through the
# view layer so preselection does not answer the first set.
for _ob in [o for o in bpy.context.view_layer.objects.selected if o is not None]:
    _ob.select_set(False)
run_command(st, "BooleanDifference")
run_command(st, "Box4")
run_command(st, "")                   # <- confirms set 1 (subtract FROM)
run_command(st, "Box5")
run_command(st, "")                   # <- confirms set 2 (subtract WITH)
diffs = [o for o in bpy.data.objects if o.name.startswith("Boolean")]
if len(diffs) != 2:
    failures.append(f"expected 2 boolean results after difference, got {len(diffs)}")
else:
    diff_geom = st.ctx.geom_by_name(diffs[-1].name)
    if diff_geom is None:
        failures.append("difference result has no geometry in the model")
    else:
        dvol = analysis.solid_volume(diff_geom, 2, 2)
        print("difference volume:", round(dvol, 4), "(expected ~6)")
        if abs(dvol - 6.0) > 0.3:
            failures.append(f"difference volume {dvol:.4f} not within tolerance of 6")

# --- Volume on a boolean result must be fast (under 5 seconds) -------------
# The union result from above is mesh-derived (~16k faces). With the old fixed
# nu=64 it took ~33 min; with adaptive density (nu=1 for planar faces) it's exact.
import time as _time
bool_obj = next((o for o in bpy.data.objects if o.name.startswith("Boolean")), None)
if bool_obj is not None:
    geom = st.ctx.geom_by_name(bool_obj.name)
    n = analysis.analysis_density(geom)
    t0 = _time.monotonic()
    vol_fast = analysis.solid_volume(geom, n, n)
    elapsed = _time.monotonic() - t0
    print(f"Volume on boolean ({len(geom.Faces)} faces) in {elapsed:.3f}s "
          f"density={n} vol={vol_fast:.4f}")
    if n != 1:
        failures.append(f"expected density 1 for mesh-derived brep, got {n}")
    if elapsed > 5.0:
        failures.append(f"Volume took {elapsed:.1f}s (>5s limit)")
    if abs(vol_fast - 18.0) > 0.3:
        failures.append(f"boolean volume {vol_fast:.4f} not ~18")
else:
    failures.append("no boolean object found for Volume speed test")

# --- BooleanDifference direction follows operand-set order -----------------
#
# This used to pin "direction follows the ACTIVE object": with exactly two objects
# selected, `_BooleanCmd.begin` executed immediately, taking the active object as the
# one subtracted FROM. That rule was OURS -- `booleanunion.htm` describes
# BooleanDifference as TWO prompts ("Select surfaces or polysurfaces to subtract
# from, confirm, then select the subtracting set and confirm it independently.
#
# So OURS is replaced by MEASURED, and the INVARIANT this test exists for is kept
# exactly: the DIRECTION must follow which operand is nominated first, and the wrong
# direction must fail. A [0..3] and B [2..5] overlap in x[2..3]; subtracting A FROM B
# keeps x[3..5], and the reversed answer x[0..2] still fails.
run_command(st, "Box")
run_command(st, "0,0,0")
run_command(st, "3,1,0")
run_command(st, "1")          # -> BoxA (left)
run_command(st, "Box")
run_command(st, "2,0,0")
run_command(st, "5,1,0")
run_command(st, "1")          # -> BoxB (right, overlaps A in x[2..3])
all_boxes = sorted([o.name for o in bpy.data.objects if o.name.startswith("Box")],
                   key=lambda n: int(n.replace("Box", "")) if n[3:].isdigit() else 0)
t26_a, t26_b = all_boxes[-2], all_boxes[-1]
print(f"difference operands: A={t26_a} B={t26_b}")
# Deselect through the view layer because app-drawn curves are absent from
# `bpy.context.selected_objects`.
for ob in [o for o in bpy.context.view_layer.objects.selected if o is not None]:
    ob.select_set(False)
run_command(st, "BooleanDifference")
run_command(st, t26_b)        # subtract FROM the RIGHT box [2..5]
run_command(st, "")
run_command(st, t26_a)        # subtract WITH the LEFT box [0..3]
run_command(st, "")
diffs_t26 = [o for o in bpy.data.objects if o.name.startswith("Boolean")]
if not diffs_t26:
    failures.append("BooleanDifference produced no result")
else:
    res = diffs_t26[-1]
    geom = st.ctx.geom_by_name(res.name)
    if geom is None:
        failures.append(f"{res.name} has no geometry in model")
    else:
        bb = geom.GetBoundingBox()
        # A (right box [2..5]) - B (left box [0..3]) keeps x [3..5].
        # The wrong direction (B - A) would be x [0..2].
        print(f"difference result bbox X[{bb.Min.X:.3f},{bb.Max.X:.3f}] "
              f"(expected ~[3,5] for right-left)")
        if not (abs(bb.Min.X - 3.0) < 1e-3 and abs(bb.Max.X - 5.0) < 1e-3):
            failures.append(f"direction wrong — bbox X[{bb.Min.X:.3f},{bb.Max.X:.3f}] "
                            f"expected [3,5] (active-right minus left); got reversed")

# --- save/reopen preserves geometry and keeps commands functional ----------
t25_path = "/tmp/narwhal3d_roundtrip.3dm"

# Park a box at a known, deliberately non-default place, then save.
run_command(st, "Box")
run_command(st, "0,0,0")
run_command(st, "2,1,0")
run_command(st, "1")
roundtrip_name = sorted(
    (o.Attributes.Name or "") for o in st.model.file.Objects
    if (o.Attributes.Name or "").startswith("Box"))[-1]
run_command(st, "Select")
run_command(st, roundtrip_name)
run_command(st, "Move")
run_command(st, "0,0,0")       # base point
run_command(st, "30,0,0")      # -> x should now be [30,32]

pre = st.ctx.geom_by_name(roundtrip_name).GetBoundingBox()
print(f"{roundtrip_name} before save X[{pre.Min.X:.2f},{pre.Max.X:.2f}] "
      f"(expected [30,32] - the move must be baked into the document)")
if not (abs(pre.Min.X - 30.0) < 1e-6 and abs(pre.Max.X - 32.0) < 1e-6):
    failures.append(f"Move was not baked into stored geometry: "
                    f"X[{pre.Min.X:.3f},{pre.Max.X:.3f}], expected [30,32]")

if os.path.exists(t25_path):       # a leftover must not satisfy the check below
    os.remove(t25_path)
run_command(st, "Save")
run_command(st, t25_path)
if not os.path.exists(t25_path):
    failures.append("Save did not write the round-trip file")
else:
    saved_count = st.model.object_count()
    err_mark = len(st.history)
    run_command(st, "Open")
    run_command(st, t25_path)
    print("Open:", st.history[-1])

    # Every named object in the file must now be drawn in the viewport.
    model_names = {(o.Attributes.Name or "") for o in st.model.file.Objects} - {""}
    scene_names = {o.name for o in bpy.data.objects}
    undrawn = sorted(model_names - scene_names)
    if undrawn:
        failures.append(f"Open left {len(undrawn)} object(s) undrawn: {undrawn[:5]}")
    stale = sorted(scene_names - model_names)
    if stale:
        failures.append(f"Open left stale viewport objects: {stale[:5]}")
    print(f"round trip: {len(model_names)} in file, {len(scene_names)} drawn, "
          f"{len(undrawn)} undrawn, {len(stale)} stale")

    # The moved box must reload in its moved position.
    reloaded = st.ctx.geom_by_name(roundtrip_name)
    if reloaded is None:
        failures.append(f"{roundtrip_name} missing from reopened file")
    else:
        bb = reloaded.GetBoundingBox()
        print(f"{roundtrip_name} after reopen X[{bb.Min.X:.2f},{bb.Max.X:.2f}]")
        if not (abs(bb.Min.X - 30.0) < 1e-6 and abs(bb.Max.X - 32.0) < 1e-6):
            failures.append(f"placement lost on reload — "
                            f"X[{bb.Min.X:.3f},{bb.Max.X:.3f}], expected [30,32]")

    # Transforms, deletes, and booleans must work on a freshly loaded model.
    # Assert BEHAVIOURALLY (scan the command bar for errors) rather than poking
    # at private attributes: the old `hasattr(_transforms)` check passed even
    # when the command had already failed, because the viewport is updated
    # before the document call that used to raise.
    run_command(st, "Select")
    run_command(st, roundtrip_name)
    run_command(st, "Move")
    run_command(st, "0,0,0")       # base point
    run_command(st, "5,0,0")
    moved = st.ctx.geom_by_name(roundtrip_name)
    if moved is not None:
        bb = moved.GetBoundingBox()
        if not (abs(bb.Min.X - 35.0) < 1e-6):
            failures.append(f"Move after Open did not reach x=35: "
                            f"X[{bb.Min.X:.3f},{bb.Max.X:.3f}]")

    count_before = st.model.object_count()
    run_command(st, "Select")
    run_command(st, roundtrip_name)
    run_command(st, "Delete")
    if st.model.object_count() != count_before - 1:
        failures.append(f"Delete after Open did not remove from the file "
                        f"({count_before} -> {st.model.object_count()})")

    # a boolean after Open exercises _world_geom on loaded geometry
    run_command(st, "Box")
    run_command(st, "40,0,0")
    run_command(st, "43,1,0")
    run_command(st, "1")
    run_command(st, "Box")
    run_command(st, "42,0,0")
    run_command(st, "45,1,0")
    run_command(st, "1")
    post_boxes = sorted(
        ((o.Attributes.Name or "") for o in st.model.file.Objects
         if (o.Attributes.Name or "").startswith("Box")),
        key=lambda n: int(n[3:]) if n[3:].isdigit() else 0)
    bools_before = len([o for o in bpy.data.objects if o.name.startswith("Boolean")])
    # Deselect so preselection does not answer the operand set before typed names.
    for _ob in [o for o in bpy.context.view_layer.objects.selected if o is not None]:
        _ob.select_set(False)
    run_command(st, "BooleanUnion")
    run_command(st, post_boxes[-2])
    run_command(st, post_boxes[-1])
    run_command(st, "")          # confirm the set
    bools_after = len([o for o in bpy.data.objects if o.name.startswith("Boolean")])
    if bools_after != bools_before + 1:
        failures.append("BooleanUnion after Open produced no result")

    # nothing in the whole post-Open sequence may have logged an error
    errs = [ln for ln in st.history[err_mark:]
            if ln.startswith(("Error:", "Bad input:")) or "AttributeError" in ln]
    if errs:
        failures.append(f"post-Open sequence logged {len(errs)} error(s): {errs[:3]}")
    print(f"post-Open sequence logged {len(errs)} error(s)")

# --- save .3dm and re-read with rhino3dm directly
import rhino3dm as r3  # noqa: E402

path = "/tmp/narwhal3d_e2e.3dm"
# The model already has a file, so Save writes to it without prompting; a new path
# belongs to SaveAs.
# old sequence fails, with a leftover copy present it passes. The file is removed first
# so a leftover can never pass this again.
if os.path.exists(path):
    os.remove(path)
run_command(st, "SaveAs")
run_command(st, path)
if not os.path.exists(path):
    failures.append(f"{path} was not written")
else:
    loaded = r3.File3dm.Read(path)
    print("re-read .3dm objects:", len(loaded.Objects))
    if loaded is None or len(loaded.Objects) < 5:
        failures.append(".3dm re-read has too few objects")
    # the mesh-derived boolean result must survive a save/reload round-trip
    bool_geom = next((o.Geometry for o in loaded.Objects
                      if (o.Attributes.Name or "").startswith("Boolean")), None)
    if bool_geom is None:
        failures.append("boolean result missing from re-read .3dm")
    elif not isinstance(bool_geom, r3.Brep) or len(bool_geom.Faces) == 0:
        failures.append(f"re-read boolean geometry is empty: {type(bool_geom).__name__}")

# --- new geometry lands on the current layer -------------------------------
_layer_idx = st.ctx.model.add_layer("E2ELayer", (10, 20, 30))
st.ctx.model.set_current_layer(_layer_idx)
_names_before = {(o.Attributes.Name or "") for o in st.ctx.model.file.Objects}
run_command(st, "Point")
run_command(st, "7,7,7")
_new = [o for o in st.ctx.model.file.Objects
        if (o.Attributes.Name or "") not in _names_before]
if not _new:
    failures.append("the current-layer Point was not created")
elif int(_new[0].Attributes.LayerIndex) != _layer_idx:
    failures.append(f"new object landed on layer "
                    f"{_new[0].Attributes.LayerIndex}, expected current layer "
                    f"{_layer_idx} (add_geometry ignored the current layer)")
else:
    print(f"a new object follows the current layer ({_layer_idx})")
st.ctx.model.set_current_layer(0)   # restore so later blocks are unaffected

# --- typed points use the active viewport's CPlane -------------------------
# The pure rules are covered by spike/test_coords.py; this proves the WIRING --
# that Command._parse actually consults the context's active_cplane() instead of
# treating typed text as world coordinates. Uses a stub context so it does not
# need four real viewports.
from narwhal3d import cplane as _cplane  # noqa: E402
from narwhal3d.commands import COMMANDS as _CMDS  # noqa: E402


class _StubCtx:
    """Only what Command._parse touches."""

    def __init__(self, plane):
        self._plane = plane

    def active_cplane(self):
        return self._plane


import copy as _cp  # noqa: E402

for _view, _plane, _typed, _want in (
        ("Top", _cplane.WORLD_TOP, "1,2", (1.0, 2.0, 0.0)),
        ("Front", _cplane.WORLD_FRONT, "1,2", (1.0, 0.0, 2.0)),
        ("Right", _cplane.WORLD_RIGHT, "1,2", (0.0, 1.0, 2.0))):
    _cmd = _cp.deepcopy(_CMDS["line"])
    _cmd.feed(_typed, _StubCtx(_plane))
    _got = _cmd.args[0] if _cmd.args else None
    if _got is None or any(abs(a - b) > 1e-9 for a, b in zip(_got, _want)):
        failures.append(f"with {_view} active, typing {_typed!r} gave "
                        f"{_got}, expected {_want} (CPlane coords ignored?)")
    else:
        print(f"{_view:<6} typed {_typed} -> world {_got}")

# a bad coordinate must RE-PROMPT, not cancel the command (the reference CAD application's behaviour)
_cmd = _cp.deepcopy(_CMDS["line"])
_msg, _done = _cmd.feed("not-a-point", _StubCtx(_cplane.WORLD_TOP))
if _done:
    failures.append("a bad coordinate cancelled the command instead of re-prompting")
else:
    print(f"bad input re-prompts: {_msg!r}")

# --- report
print("\n" + "=" * 50)
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("ADDON E2E PASSED")
