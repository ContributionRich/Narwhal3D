"""Patched-Blender tests for app-drawn objects outside the dependency graph.

``Collection.narwhal_app_drawn`` objects retain selection, active-object, and hide
state while remaining outside evaluated dependency-graph contents.

Checked structurally:
  1. objects in the app-drawn collection are NOT in the evaluated depsgraph; solids are;
  2. they stay selectable: select_set/select_get, active, view_layer.objects.selected;
  3. Zoom's `bridge.blender_sees_curves()` puts them back for its duration, then out;
  4. `bridge.local_box_corners` gives a real box (their evaluated `bound_box` is zeros);
  5. every writer of their mesh / matrix notifies `bridge.DISPLAY_CHANGE_LISTENERS`
     (they raise no depsgraph update any more);
  6. the graph rebuild after an add/remove no longer scales with the number of curves
     (the curves contribute no ids).

An unpatched Blender reports a skip because the required collection property is absent.
"""
import importlib.util
import os
import sys
import types

import bmesh
import bpy
from mathutils import Matrix

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vendor", "occt"))
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
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")

checks, failures = 0, []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


if not bridge.app_drawn_ok():
    print("  skip  this Blender has no `Collection.narwhal_app_drawn` (unpatched) -- "
          "build it: blender_patches/README.md")
    sys.exit(0)

heard = []
bridge.DISPLAY_CHANGE_LISTENERS.append(lambda names, removed=False: heard.append(
    (tuple(names), removed)))


def curve_bm(x):
    bm = bmesh.new()
    a = bm.verts.new((x, 0.0, 0.0))
    b = bm.verts.new((x + 1.0, 2.0, 3.0))
    bm.edges.new((a, b))
    return bm


def solid_bm():
    bm = bmesh.new()
    vs = [bm.verts.new(p) for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0))]
    bm.faces.new(vs)
    return bm


print("-- app-drawn objects are not in the dependency graph --")
heard.clear()
curves = [bridge.bmesh_to_object(f"crv{i}", curve_bm(float(i))) for i in range(300)]
solid = bridge.bmesh_to_object("solid0", solid_bm())
coll = bridge.curve_collection(create=False)
vl = bpy.context.view_layer
vl.update()
dg = bpy.context.evaluated_depsgraph_get()
ids = {i.name for i in dg.ids}
check(coll is not None and coll.narwhal_app_drawn,
      "the curve collection carries the patch's flag (narwhal_app_drawn)")
check(not any(c.name in ids for c in curves) and solid.name in ids,
      f"no app-drawn object is in the evaluated depsgraph ({sum(c.name in ids for c in curves)}"
      f" of {len(curves)}); a solid is")
check(len(heard) == len(curves) and all(not r for _n, r in heard),
      f"creating each app-drawn object notified the app ({len(heard)} notices)")

print("\n-- they stay selectable --")
for o in bridge.view_layer_selected():          # (factory startup selects its Cube)
    o.select_set(False)
c0 = curves[0]
c0.select_set(True)
vl.objects.active = c0
sel = [o.name for o in vl.objects.selected if o is not None]
check(c0.select_get() and c0.name in sel and vl.objects.active == c0,
      "select_set / select_get / active / view_layer.objects.selected all hold an app-drawn "
      "object")
check([o.name for o in bridge.selected_display_objects()] == [c0.name],
      "bridge.selected_display_objects lists it")
c0.select_set(False)

print("\n-- Zoom lets Blender see them, then puts them back out --")
with bridge.blender_sees_curves():
    inside = {i.name for i in bpy.context.evaluated_depsgraph_get().ids}
after = {i.name for i in bpy.context.evaluated_depsgraph_get().ids}
check(c0.name in inside and c0.name not in after and coll.narwhal_app_drawn,
      "inside blender_sees_curves() they are evaluated (framing operators need it); "
      "after it they are out again")

print("\n-- their box comes from the mesh --")
corners = bridge.local_box_corners(c0)
xs, ys, zs = zip(*corners)
check(min(xs) == 0.0 and max(xs) == 1.0 and max(ys) == 2.0 and max(zs) == 3.0,
      f"local_box_corners is the mesh's box ({min(xs)}..{max(xs)}, y {max(ys)}, z {max(zs)})")

print("\n-- every writer tells the app --")
heard.clear()
import rhino3dm as r3  # noqa: E402

line = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(5, 0, 0))
ok1 = bridge.rebuild_object_mesh(c0.name, line)
n1 = [n for n, _r in heard]
heard.clear()
ok2 = bridge.retessellate_curve_for_view(curves[1].name,
                                         r3.Circle(r3.Point3d(0, 0, 0), 2.0).ToNurbsCurve(), 100.0)
n2 = [n for n, _r in heard]
heard.clear()
bridge.remove_display_objects([curves[2]])
n3 = list(heard)
check(ok1 and n1 == [(c0.name,)], f"rebuild_object_mesh notifies ({n1})")
check(ok2 and n2 == [(curves[1].name,)], f"retessellate_curve_for_view notifies ({n2})")
check(n3 == [(("crv2",), True)], f"remove_display_objects notifies, as a removal ({n3})")

print("\n-- an add/remove no longer rebuilds a graph the size of the curves --")
heard.clear()
n_ids_before = len(bpy.context.evaluated_depsgraph_get().ids)
more = [bridge.bmesh_to_object(f"more{i}", curve_bm(float(i))) for i in range(200)]
vl.update()
n_ids_after = len(bpy.context.evaluated_depsgraph_get().ids)
check(n_ids_after == n_ids_before,
      f"200 more app-drawn objects add no ids to the depsgraph ({n_ids_before} -> "
      f"{n_ids_after})")
c0.matrix_world = Matrix.Translation((4.0, 0.0, 0.0))
vl.update()
check(tuple(c0.matrix_world.translation) == (4.0, 0.0, 0.0),
      "an app-drawn object's matrix_world still reads back what was written (it is not "
      "evaluated, so the original IS the value)")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("APP-DRAWN (patched Blender) PASSED")
