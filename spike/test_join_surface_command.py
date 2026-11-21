#!/usr/bin/env python3
"""`Join` / `JoinCopy` -- the SURFACE (Brep) family, at the command level.

Run (bundled Python; explicit native core and brep writer):
  NARWHAL_GEOMETRY_CORE=<...>/libnarwhal_geometry_core.so \
  NARWHAL3D_BREP_WRITER=<root>/narwhal3d/kernel/bin/brep_writer-linux-x86_64 \
  <patched-python> spike/test_join_surface_command.py

Actual Blender acceptance (same explicit environment, no bpy/display stubs):
  <blender-patched>/blender -b --factory-startup --python-exit-code 1 \
    --python spike/test_join_surface_command.py -- --host
The debug candidate requires its matching bundled cadquery_ocp_novtk.libs on
LD_LIBRARY_PATH; do not mix it with vendor/occt's differently hashed wheel.

The curve family is also covered by spike/test_join.py (unchanged). This suite
runs a REAL NarwhalContext + Rust undo stack. --host additionally uses the real
Blender scene, display allocations, selection and Undo/Redo display refresh;
the plain SDK mode stubs only that Blender surface. It and drives the EXACT native surface join through
`bridge.join_breps` -- the geometry worker's contract, using the SAME
worker-qualified fixtures spike/test_join_surfaces.py uses:

  * all 6 faces of a 2x3x4 box  -> one closed solid (6 PLANE, airtight, vol 24);
  * the cylinder's first 2 faces -> one OPEN polysurface ({PLANE:1, CYLINDER:1});
  * the documented join threshold is **2x the document absolute tolerance**
    (commands/join.htm: "edges are within 2x tolerance") -- pinned here at the
    command level with a coplanar pair whose gap is between 1x and 2x.

Guarantees under test:
  * results are added BEFORE their consumed inputs are removed;
  * Join removes ONLY the consumed keys; JoinCopy keeps ALL inputs;
  * a refusal leaves EVERY input in place -- no partial success, no deletion;
  * the result inherits the FIRST selected member's layer;
  * a closed join is named Solid, an open one Polysurface;
  * curves still join (1x), surfaces join (2x), and points/other geometry are
    refused honestly in a mixed selection (never faked into a result).

No surface behavior is mocked: the faces come from worker-qualified OCCT
fixtures and Join is the native exact path. This Python is unavoidable host/API
coverage (bpy, rhino3dm, callback faults), not a second geometry implementation.
All exact geometry and history computation remain in their native backends.
"""
import importlib.util
import math
import os
import sys
import types
import tempfile
import subprocess

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, relpath):
    path = os.path.join(ROOT, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


HOST = "--host" in sys.argv or __name__ == "__main__" and "bpy" in sys.modules
if HOST:
    import bpy

# bpy stub: no active object (selection order stands), no display objects, no scene.
_bpy = types.ModuleType("bpy")
_bpy.context = types.SimpleNamespace(
    view_layer=types.SimpleNamespace(objects=types.SimpleNamespace(active=None)))
_bpy.data = types.SimpleNamespace(objects=types.SimpleNamespace(get=lambda name: None))
sys.modules.setdefault("bpy", _bpy)

bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
kernel = _load("narwhal3d.kernel", "narwhal3d/kernel/__init__.py")
occt = _load("narwhal3d.kernel.occt_backend", "narwhal3d/kernel/occt_backend.py")
native = _load("narwhal3d.native_geometry", "narwhal3d/native_geometry.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
if not HOST:
    bridge.refresh_display_objects = lambda ctx, changes: True  # no display to refresh

checks = 0
failures = []


def check(cond, label, detail=""):
    global checks
    checks += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f"  ({detail})" if detail else ""))
        failures.append(label)


class _Sel:
    def __init__(self, name):
        self.name = name


def box_faces(prefix="f"):
    """The 6 faces of a 2x3x4 box as separate exact breps (worker-qualified)."""
    recipe = {"kind": "box", "dx": 2, "dy": 3, "dz": 4}
    parent = occt.from_recipe(recipe)
    return [(f"{prefix}{i}",
             bridge.occt_backed(face, {"kind": "face", "index": i, "a": recipe}))
            for i, face in enumerate(occt.unique_faces(parent), 1)]


def cyl_faces(prefix="c"):
    """The 3 faces of an r=2, h=3 cylinder (worker-qualified)."""
    recipe = {"kind": "cylinder", "radius": 2, "height": 3}
    parent = occt.from_recipe(recipe)
    return [(f"{prefix}{i}",
             bridge.occt_backed(face, {"kind": "face", "index": i, "a": recipe}))
            for i, face in enumerate(occt.unique_faces(parent), 1)]


def line(a, b):
    return r3.LineCurve(r3.Point3d(*a), r3.Point3d(*b))


def touching_curves():
    return [("A", line((0, 0, 0), (5, 0, 0))),
            ("B", line((5, 0, 0), (5, 5, 0)))]


def coplanar_pair(gap):
    """Two coplanar unit squares in the z=0 plane, `gap` apart in u (worker-style)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Pln, gp_Ax3
    first = BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3()), 0., 1., 0., 1.).Face()
    second = BRepBuilderAPI_MakeFace(gp_Pln(gp_Ax3()), 1.0 + gap, 2.0, 0., 1.).Face()
    return [("left", bridge.occt_backed(first, occt.native_master_recipe(first))),
            ("right", bridge.occt_backed(second, occt.native_master_recipe(second)))]


def make_ctx(objects, tol=None):
    """A document holding `objects` [(name, geom, layer_index)], all selected in
    that order. `tol` sets the document absolute tolerance."""
    if HOST:
        bpy.data.batch_remove(list(bpy.data.objects))
    ctx = commands.NarwhalContext(model.NarwhalModel())
    if tol is not None:
        ctx.model.set_absolute_tolerance(tol)
    # The document must actually hold these layers: rhino3dm's
    # File3dmObjectTable.Add clamps ObjectAttributes.LayerIndex to 0 when the
    # layer is absent from file.Layers (MEASURED: an out-of-range index is
    # silently discarded, no error). Same guard as spike/test_join.py's
    # `join_ctx` -- the model starts with exactly one layer.
    top = max(l for _, _, l in objects)
    for index in range(1, 1 + top):
        layer = r3.Layer()
        layer.Name = f"L{index}"
        ctx.model.file.Layers.Add(layer)
    for name, geom, layer in objects:
        ctx.model.add_object(geom, name, layer)
    names = [name for name, _, _ in objects]
    if HOST:
        ctx.rebuild_display()
        ctx.select_by_names(names)
        bpy.context.view_layer.objects.active = bpy.data.objects.get(names[0])
        return ctx
    ctx.selected_objects = lambda: [_Sel(n) for n in names]
    ctx.object_by_name = lambda name: None
    ctx.rebuild_display = lambda: None
    ctx.select_by_names = lambda names: None
    ctx._make_display_object = lambda *a, **k: object()
    return ctx


def doc(ctx):
    out = {}
    for f in ctx.model.file.Objects:
        a = f.Attributes
        if isinstance(f.Geometry, r3.Brep):
            _ = f.Geometry.IsSolid  # Populate the SDK topology cache before encoding.
        out[str(a.Id)] = (a.Name, int(a.LayerIndex), int(a.Mode),
                          str(f.Geometry.Encode()))
    return out


def exact_payload(geom):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    master = kernel.master_of(geom)
    if master is None:
        return geom.Encode()
    # Use the existing kernel policy on a PRIVATE copy, not live source faces.
    copy = BRepBuilderAPI_Copy(master, True, False).Shape()
    occt.document_clear_cache_flags(copy)
    return occt.shape_to_bytes(copy, triangles=False)


def history_doc(ctx):
    # File3dm Add/Duplicate is not a byte-preserving archive operation. History
    # must preserve UUIDs, attributes, recipe trees AND exact master payloads.
    # Raw Encode equality remains required for refusal/publication rollback.
    return {str(o.Attributes.Id): (o.Attributes.Encode(), kernel.recipe_of(o.Geometry),
            tuple(o.Geometry.GetUserStrings()),
            exact_payload(o.Geometry))
            for o in ctx.model.file.Objects}


def names_in(ctx):
    return [f.Attributes.Name for f in ctx.model.file.Objects]


def run(key, ctx):
    return type(commands.COMMANDS[key])().begin(ctx)


def the_brep(ctx):
    """The FIRST Brep object in the document (inputs kept or all consumed)."""
    for f in ctx.model.file.Objects:
        if isinstance(f.Geometry, r3.Brep):
            return f
    return None


def the_result(ctx, input_names):
    """The NEW Brep result: a Brep whose name is not one of the input names.

    JoinCopy keeps its inputs, so 'the first Brep' would be an input; the
    result is always the Brep that was not selected.
    """
    for f in ctx.model.file.Objects:
        if isinstance(f.Geometry, r3.Brep) and f.Attributes.Name not in set(input_names):
            return f
    return None


def face_types(geom):
    return occt.face_types(kernel.master_of(geom))


def is_airtight(geom):
    return occt.is_airtight(kernel.master_of(geom))


def volume(geom):
    return occt.volume(kernel.master_of(geom))


if not native.join_exact_available():
    raise RuntimeError("qualified native Join candidate unavailable; pin NARWHAL_GEOMETRY_CORE and its OCCT dependencies")

# ===========================================================================
print()
print("=" * 64)
print("1. SURFACE JOIN: all 6 box faces -> one closed solid, inputs consumed")
print("=" * 64)

faces = box_faces()
in_names = {n for n, _ in faces}
ctx = make_ctx([(n, g, 0) for n, g in faces])  # Actual default 0.01 -> 0.02
before = doc(ctx)
msg, done = run("join", ctx)
check(done, f"Join runs ({msg!r})")
res = the_result(ctx, in_names)
check(res is not None, "exactly one Brep result was created", names_in(ctx))
if res is not None:
    g = res.Geometry
    check(g.IsSolid, "the joined shell is a SOLID", f"IsSolid={g.IsSolid}")
    check(face_types(g) == {"PLANE": 6}, "six analytic planar faces", f"{face_types(g)}")
    check(is_airtight(g), "airtight (manufacturable)")
    check(abs(volume(g) - 24.0) < 1e-9, "volume 24 (2x3x4 box)", f"{volume(g)}")
    check(kernel.honesty_problem(g) is None, "no honesty problem")
    check(not in_names & set(names_in(ctx)), "all 6 consumed inputs removed",
          f"{sorted(names_in(ctx))}")
    check(len(names_in(ctx)) == 1, "only the result remains", f"{sorted(names_in(ctx))}")

print()
print("=" * 64)
print("2. OPEN JOIN: cylinder 2 faces -> Polysurface (not Solid), inputs consumed")
print("=" * 64)

cf = cyl_faces()
in_names = {n for n, _ in cf[:2]}
ctx = make_ctx([(n, g, 0) for n, g in cf[:2]], tol=0.004)
msg, done = run("join", ctx)
check(done, f"Join runs ({msg!r})")
res = the_result(ctx, in_names)
check(res is not None, "one Brep result created", names_in(ctx))
if res is not None:
    g = res.Geometry
    check(not g.IsSolid, "the 2-face join is OPEN (not a solid)", f"IsSolid={g.IsSolid}")
    check(face_types(g) == {"PLANE": 1, "CYLINDER": 1}, f"1 plane + 1 curved face",
          f"{face_types(g)}")
    check(not in_names & set(names_in(ctx)), "both consumed inputs removed")
    check(len(names_in(ctx)) == 1, "only the result remains")
    check("open" in msg.lower() or "polysurface" in msg.lower() or res.Attributes.Name
          .startswith("Polysurface"), "reported as open/Polysurface",
          f"name={res.Attributes.Name!r} msg={msg!r}")

print()
print("=" * 64)
print("3. LAYER: the result inherits the FIRST selected member's layer")
print("=" * 64)

faces = box_faces()
layers = [3, 1, 1, 1, 1, 1]
ctx = make_ctx([(n, g, layers[i]) for i, (n, g) in enumerate(faces)], tol=0.004)
in_names = {n for n, _ in faces}
msg, done = run("join", ctx)
res = the_result(ctx, in_names)
check(res is not None and int(res.Attributes.LayerIndex) == 3,
      "result on layer 3 (the first selected face's layer)",
      f"layer={res.Attributes.LayerIndex if res is not None else None}")

print()
print("=" * 64)
print("4. JOINCOPY: all inputs kept, one result, one Undo step")
print("=" * 64)

faces = box_faces()
in_names = {n for n, _ in faces}
ctx = make_ctx([(n, g, 0) for n, g in faces], tol=0.004)
before = doc(ctx)
depth = len(ctx.undo._undo)
msg, done = run("joincopy", ctx)
after = doc(ctx)
check(done, f"JoinCopy runs ({msg!r})")
check(len(after) == len(before) + 1, "exactly one object added",
      f"{len(before)} -> {len(after)}")
check(all(after.get(k) == v for k, v in before.items()),
      "every input keeps its id, name, layer, mode and exact geometry")
res = the_result(ctx, in_names)
check(res is not None and res.Geometry.IsSolid, "the copy's result is a solid")
check(res is not None and res.Attributes.Name not in in_names,
      "the result is a NEW object (its name is not an input's)")
check(len(ctx.undo._undo) == depth + 1, "one Undo record", f"{depth} -> {len(ctx.undo._undo)}")
ctx.undo_last()
check(doc(ctx) == before, "Undo removes only the result; inputs untouched")
ctx.redo_last()
check(len(doc(ctx)) == len(before) + 1, "Redo restores the result")

print()
print("=" * 64)
print("5. MIXED: curves join (1x), box faces join (2x), point refused and kept")
print("=" * 64)

pt = r3.Point(r3.Point3d(9, 9, 9))
faces = box_faces()
objs = [("A", line((0, 0, 0), (5, 0, 0)), 0),
        ("B", line((5, 0, 0), (5, 5, 0)), 0),
        (faces[0][0], faces[0][1], 2)]
# add the remaining 5 box faces so the surface family has >= 2 joinable inputs
objs += [(n, g, 1) for n, g in faces[1:]]
objs.append(("Pt", pt, 0))
surf_names = [n for n, _, _ in objs if n not in ("A", "B", "Pt")]
ctx = make_ctx(objs, tol=0.004)
msg, done = run("join", ctx)
check(done, f"mixed Join runs ({msg!r})")
names = set(names_in(ctx))
check("Pt" in names, "the point is KEPT (refused, not faked)")
check(not ({n for n, _, _ in objs} - {"Pt"}) & names,
      "the eight joinable inputs (2 curves + 6 faces) are all consumed",
      f"{sorted(names)}")
check(len(names) == 3, "exactly three names remain: the point + curve + surface",
      f"{sorted(names)}")
check("Not joined: points" in msg, "the point refusal is named", msg)
# the curve result is a single joined curve; the surface result is the solid
curve_res = [f for f in ctx.model.file.Objects
             if isinstance(f.Geometry, r3.Curve) and f.Attributes.Name not in ("A", "B")][0]
check(isinstance(curve_res.Geometry, r3.Curve), "the curve result is a joined curve")
surf_res = the_result(ctx, surf_names)
check(surf_res is not None and surf_res.Geometry.IsSolid, "the surface result is the solid")
check(int(surf_res.Attributes.LayerIndex) == 2,
      "the surface result takes the first selected surface's layer (2)",
      f"layer={surf_res.Attributes.LayerIndex}")

print()
print("=" * 64)
print("6. REFUSAL: one face + one point -> refused by name, nothing changes")
print("=" * 64)

pt = r3.Point(r3.Point3d(0, 0, 0))
face = box_faces()[0]
ctx = make_ctx([(face[0], face[1], 0), ("Pt", pt, 0)])
before = doc(ctx)
msg, done = run("join", ctx)
check(("not joined" in msg) or ("joins nothing" in msg),
      f"refused by name ({msg!r})")
check(doc(ctx) == before, "nothing added or removed")
check(len(names_in(ctx)) == 2, "both inputs still present")

# and a single face with no point: still nothing to join, still kept
ctx = make_ctx([(face[0], face[1], 0)], tol=0.004)
before = doc(ctx)
msg, done = run("join", ctx)
check("select at least two" in msg or "joins nothing" in msg or "at least two" in msg,
      f"a lone surface is refused by name ({msg!r})")
check(doc(ctx) == before, "the lone surface is kept")

print()
print("=" * 64)
print("7. 2x THRESHOLD: gap between 1x and 2x the doc tolerance")
print("=" * 64)

D = 1e-4
gap = 1.5e-4   # 1x (1e-4) < gap < 2x (2e-4)
pair = coplanar_pair(gap)
# GREEN: the command uses 2x, so 2x (2e-4) > gap -> the pair JOINS.
ctx = make_ctx([(n, g, 0) for n, g in pair], tol=D)
msg, done = run("join", ctx)
res = the_result(ctx, {n for n, _ in pair})
check(res is not None and not res.Geometry.IsSolid,
      f"gap {gap} < 2x {2*D} joins (1x {D} < gap would refuse)",
      f"res={res is not None} msg={msg!r}")
# RED at 1x: a command that used 1x would refuse this gap. Prove the bridge
# would refuse at 1x (so the join above is specifically the 2x doing the work).
refuse1x = bridge.join_breps([(n, g) for n, g in pair], D)
check(refuse1x == [], "the same pair is REFUSED at 1x (bridge-level), confirming 2x",
      f"{refuse1x}")

print()
print("=" * 64)
print("8. DEFAULT SUCCESS: document 0.01 -> qualified distance 0.02")
print("=" * 64)
for key in ("join", "joincopy"):
    faces = cyl_faces()
    in_names = {n for n, _ in faces}
    ctx = make_ctx([(n, g, 0) for n, g in faces])
    before = history_doc(ctx)
    depth = len(ctx.undo._undo)
    msg, done = run(key, ctx)
    res = the_result(ctx, in_names)
    check(res is not None, f"{key} succeeds at actual document default", msg)
    if res is None:
        continue
    g = res.Geometry
    check(face_types(g) == {"PLANE": 2, "CYLINDER": 1}, "default result preserves cylinder faces")
    check(is_airtight(g) and abs(volume(g) - 12 * math.pi) < 1e-9, "default result exact volume and closure")
    check(kernel.recipe_of(g)["tolerance"] == 0.02, "recipe pins default qualified tolerance once")
    check(len(ctx.undo._undo) == depth + 1, f"{key} exactly one Undo")
    after = history_doc(ctx)
    ctx.undo_last()
    check(history_doc(ctx) == before, f"{key} Undo restores IDs, recipes and exact masters")
    ctx.redo_last()
    check(history_doc(ctx) == after, f"{key} Redo restores IDs, recipes and exact masters")
    restored = the_result(ctx, in_names).Geometry
    check(kernel.master_of(restored) is not None and kernel.honesty_problem(restored) is None,
          f"{key} Redo preserves master/document binding")
    if HOST:
        check(set(bpy.data.objects.keys()) == set(names_in(ctx)), f"{key} actual Blender display matches Redo document")
    with tempfile.TemporaryDirectory(prefix="join-command-save-") as folder:
        path = os.path.join(folder, "joined.3dm")
        check(ctx.model.save(path), f"{key} saves real 3dm")
        reopened = model.NarwhalModel.load(path)
        saved = next(o.Geometry for o in reopened.file.Objects if o.Attributes.Name not in in_names)
        master = kernel.master_of(saved)
        check(master is not None and occt.face_types(master) == {"PLANE": 2, "CYLINDER": 1},
              f"{key} reload preserves analytic cylinder")
        step = os.path.join(folder, "joined.step")
        occt.export_step(master, step)
        check("CYLINDRICAL_SURFACE" in open(step).read(), f"{key} STEP is analytic")
        python = (os.path.join(os.path.dirname(bpy.app.binary_path),
                  f"{bpy.app.version[0]}.{bpy.app.version[1]}", "python", "bin",
                  f"python{sys.version_info.major}.{sys.version_info.minor}") if HOST else sys.executable)
        reload_code = """
import os, sys, types, math
root, path, step = sys.argv[1:]
pkg = types.ModuleType('narwhal3d')
pkg.__path__ = [os.path.join(root, 'narwhal3d')]
sys.modules['narwhal3d'] = pkg
from narwhal3d import model, kernel, native_geometry
from narwhal3d.kernel import occt_backend as occt
assert native_geometry.join_exact_available()
if path.endswith('.narwhal'):
    from narwhal3d import narwhal_document
    document = narwhal_document.load_model(path)
    assert all(kernel.master_of(o.Geometry) is None for o in document.file.Objects)
    narwhal_document.commit_masters(document)
else:
    document = model.NarwhalModel.load(path)
results = [o.Geometry for o in document.file.Objects if kernel.recipe_of(o.Geometry).get('kind') == 'join_exact']
assert len(results) == 1
geom = results[0]
assert kernel.recipe_of(geom)['tolerance'] == 0.02
master = kernel.master_of(geom)
assert master is not None and occt.is_airtight(master)
assert occt.face_types(master) == {'CYLINDER': 1, 'PLANE': 2}
assert abs(occt.volume(master) - 12 * math.pi) < 1e-9
occt.export_step(master, step)
assert 'CYLINDRICAL_SURFACE' in open(step).read()
print('FRESH_DEFAULT_JOIN_RELOAD_STEP_OK')
"""
        child = subprocess.run([python, "-B", "-c", reload_code, ROOT, path, step],
                               capture_output=True, text=True, timeout=60)
        check(child.returncode == 0 and "FRESH_DEFAULT_JOIN_RELOAD_STEP_OK" in child.stdout,
              f"{key} fresh-process default recipe rebuild + analytic STEP", child.stdout + child.stderr)
        if HOST:
            from narwhal3d import narwhal_document
            project = os.path.join(folder, "joined.narwhal")
            check(narwhal_document.save_model(ctx.model, project), f"{key} saves exact native project")
            check(not ctx.model.last_save_warnings, f"{key} native save needs no recipe rebase or approximation", ctx.model.last_save_warnings)
            native_child = subprocess.run([python, "-B", "-c", reload_code, ROOT, project, step],
                                          capture_output=True, text=True, timeout=60)
            check(native_child.returncode == 0 and "FRESH_DEFAULT_JOIN_RELOAD_STEP_OK" in native_child.stdout,
                  f"{key} native worker roundtrip preserves Join tree and analytic STEP",
                  native_child.stdout + native_child.stderr)


print("8b. DISCONNECTED MULTIPLE RESULTS + SINGLETON: ONE HISTORY STEP")
for key in ("join", "joincopy"):
    boxes, cylinders = box_faces(), cyl_faces()
    for _, g in cylinders:
        bridge.transform_geometry(g, r3.Transform.Translation(20, 0, 0))
    lone = box_faces("lone")[0]
    bridge.transform_geometry(lone[1], r3.Transform.Translation(40, 0, 0))
    objects = [(n, g, 2) for n, g in boxes] + [(n, g, 3) for n, g in cylinders] + [(lone[0], lone[1], 1)]
    ctx = make_ctx(objects)
    source_names = {n for n, _, _ in objects}
    before = history_doc(ctx)
    depth = len(ctx.undo._undo)
    msg, done = run(key, ctx)
    results = [o for o in ctx.model.file.Objects if o.Attributes.Name not in source_names]
    check(len(results) == 2 and {o.Attributes.LayerIndex for o in results} == {2, 3}, f"{key} publishes both exact groups on their source layers", msg)
    check(lone[0] in names_in(ctx) and lone[0] in msg, f"{key} preserves and names standalone source")
    check(len(ctx.undo._undo) == depth + 1, f"{key} multiple groups have one history record")
    after = history_doc(ctx)
    ctx.undo_last()
    check(history_doc(ctx) == before, f"{key} multiple group Undo preserves originals and masters")
    ctx.redo_last()
    check(history_doc(ctx) == after, f"{key} multiple group Redo preserves exact results")

print("8c. SCOPED KernelError: UNRELATED SAME-NAMED EXCEPTION IS NOT A REFUSAL")
ctx = make_ctx([(n, g, 0) for n, g in box_faces()])
before = doc(ctx)
original_join = bridge.join_breps
unrelated_error = type("KernelError", (Exception,), {})
def unrelated_failure(*args, **kwargs):
    raise unrelated_error("unrelated implementation bug")
bridge.join_breps = unrelated_failure
try:
    try:
        run("join", ctx)
    except unrelated_error:
        check(True, "unrelated same-named exception propagates")
    else:
        check(False, "unrelated same-named exception propagates")
finally:
    bridge.join_breps = original_join
check(doc(ctx) == before, "unexpected preflight exception preserves all inputs")

print("9. REFUSAL IS ATOMIC ACROSS FAMILIES")
faces = box_faces()
# An identical source face is an actual native unsupported-topology refusal.
objects = [(n, g, 0) for n, g in faces] + [("duplicate", faces[0][1].Duplicate(), 0)]
objects += [(n, g, 0) for n, g in touching_curves()]
ctx = make_ctx(objects)
before = doc(ctx)
depth = len(ctx.undo._undo)
msg, done = run("join", ctx)
check("could not be joined exactly" in msg and "all inputs kept" in msg, "actual kernel refusal named", msg)
check(doc(ctx) == before and len(ctx.undo._undo) == depth, "kernel refusal preserves both families and Undo")

print("10. STAGED ADD FAILURE: MULTIRESULT AND MIXED FAMILIES")
for mixed in (False, True):
    faces = box_faces()
    others = touching_curves() if mixed else cyl_faces()
    if not mixed:
        for _, g in others:
            bridge.transform_geometry(g, r3.Transform.Translation(20, 0, 0))
    ctx = make_ctx([(n, g, 0) for n, g in faces + others])
    before = doc(ctx)
    exact_before = history_doc(ctx)
    depth = len(ctx.undo._undo)
    original_add = ctx._add_geometry
    counters_before = dict(ctx.counters)
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("injected second result failure")
        return original_add(*args, **kwargs)
    ctx._add_geometry = fail_second
    msg, done = run("join", ctx)
    check("publication failed" in msg and len(calls) == 2, f"second result failure reported mixed={mixed}", msg)
    check(doc(ctx) == before and history_doc(ctx) == exact_before, f"all original sources retained and staged output removed mixed={mixed}")
    check(len(ctx.undo._undo) == depth, f"failed staging leaves no Undo mixed={mixed}")
    check(ctx.counters == counters_before, f"failed staging restores output name counters mixed={mixed}", str((counters_before, ctx.counters)))
    check(not commands.selection_state.last_created(ctx.model), f"failed staging clears only its SelLast allocations mixed={mixed}")
    if HOST:
        check(set(bpy.data.objects.keys()) == set(names_in(ctx)), f"no stray/deleted Blender displays mixed={mixed}")

print("10a. FALSE ADD SUCCESS MUST NOT CONSUME INPUTS")
ctx = make_ctx([(n, g, 0) for n, g in cyl_faces()])
before = doc(ctx)
ctx._add_geometry = lambda *args, **kwargs: ("00000000-0000-0000-0000-000000000000", "Ghost")
msg, done = run("join", ctx)
check("did not publish its result" in msg, "non-publishing addition is refused", msg)
check(doc(ctx) == before, "false add success retains every original")

print("10b. BEFORE-DELETE CALLBACK FAILS BEFORE ANY SOURCE/DISPLAY CONSUMPTION")
ctx = make_ctx([(n, g, 0) for n, g in cyl_faces()])
before = doc(ctx)
depth = len(ctx.undo._undo)
hooks = []
def fail_consumption_hook():
    hooks.append(1)
    # First invocation is output insertion, second is input consumption.
    if len(hooks) == 2:
        raise RuntimeError("injected input-deletion hook failure")
ctx.model.before_change = fail_consumption_hook
msg, done = run("join", ctx)
ctx.model.before_change = None
check("publication failed" in msg and len(hooks) == 2, "deletion callback failure reported before consuming sources", msg)
check(doc(ctx) == before and len(ctx.undo._undo) == depth, "deletion hook refusal retains all originals without history")
if HOST:
    check(set(bpy.data.objects.keys()) == set(names_in(ctx)), "deletion hook refusal retains all original displays")

if HOST:
    print("11. DISPLAY CALLBACK FAILURE AND EXTERNAL EDIT PRESERVATION")
    for family, source_edit, raises in (("surface", False, True), ("surface", True, True),
                                        ("surface", True, False), ("curve", False, True),
                                        ("curve", True, False)):
        faces = cyl_faces() if family == "surface" else touching_curves()
        source_name = faces[0][0]
        ctx = make_ctx([(n, g, 0) for n, g in faces])
        before = doc(ctx)
        source_objects = {o.name: o.as_pointer() for o in ctx.selected_objects()}
        original_display = ctx._make_display_object
        def fail_display(*args, **kwargs):
            ob = original_display(*args, **kwargs)
            # A legitimate external copy shares the result's analytic master.
            # Rollback must target our UUID, not every object with that key.
            ctx.model.add_object(args[0].Duplicate(), "external", 0)
            if source_edit:
                ctx.geom_by_name(source_name).SetUserString("callback", "keep me")
            if raises:
                raise RuntimeError("injected post-allocation callback failure")
            return ob
        ctx._make_display_object = fail_display
        msg, done = run("join", ctx)
        check("publication failed" in msg, f"callback failure/edit detected {family} raises={raises}", msg)
        check(set(names_in(ctx)) == {n for n, _ in faces} | {"external"}, "only own output rolled back; external addition retained")
        check(all(bpy.data.objects.get(n).as_pointer() == ptr for n, ptr in source_objects.items()),
              "source display handles untouched on add failure")
        if source_edit:
            check(ctx.geom_by_name(source_name).GetUserString("callback") == "keep me", "callback source edit retained")
        else:
            check(all(doc(ctx).get(k) == v for k,v in before.items()), "source encodings unchanged on failed display add")
        ctx._make_display_object = original_display
        check(ctx.undo_last() == "External change during Join", "surviving callback edit has one ordinary Undo")
        check(doc(ctx).keys() == before.keys(), "external edit Undo restores original IDs")

    print("12. DISPLAY IDENTITY GUARD PRESERVES CALLBACK RENAMES")
    ctx = make_ctx([(n, g, 0) for n, g in cyl_faces()])
    before = doc(ctx)
    original = ctx.object_by_name("c1")
    pointer = original.as_pointer()
    original_display = ctx._make_display_object
    def rename_source_display(*args, **kwargs):
        result = original_display(*args, **kwargs)
        original.name = "external-renamed-source"
        return result
    ctx._make_display_object = rename_source_display
    msg, done = run("join", ctx)
    check("input display changed" in msg and "publication failed" in msg, "source display replacement/rename stops publication", msg)
    check(doc(ctx) == before, "display-only callback edit retains all exact sources")
    try:
        preserved = original.as_pointer() == pointer and original.name == "external-renamed-source"
    except ReferenceError:
        preserved = False
    check(preserved, "original display handle and external rename preserved")
    check(set(bpy.data.objects.keys()) == {"external-renamed-source", "c2", "c3"}, "no output or unrelated display deleted on display guard refusal")


print()
print("=" * 64)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"JOIN SURFACE COMMAND TESTS PASSED ({checks} checks)")
