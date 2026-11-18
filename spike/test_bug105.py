"""Bug #105: general exact extrusion-part movement reaches the certified native core."""
import importlib.util
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relative))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kernel = load("narwhal3d.kernel", "narwhal3d/kernel/__init__.py")
bridge = load("narwhal3d.bridge", "narwhal3d/bridge.py")
from narwhal3d.kernel import occt_backend as occt  # noqa: E402

if not occt.available():
    print(f"SKIP: bug #105 needs OCCT ({occt.unavailable_reason()})")
    raise SystemExit(0)


def shape_of(geometry):
    return kernel.master_of(geometry) or bridge._occt_operand(geometry)


def topology(shape):
    return (len(occt.unique_faces(shape)), len(occt._unique_edges(shape)),
            len(occt._unique_vertices(shape)))


def translated_matches(before, after, kind, index, vector):
    tables = {"face": occt.unique_faces, "edge": occt._unique_edges,
              "vertex": occt._unique_vertices}
    old_part = tables[kind](shape_of(before))[index - 1]
    candidates = tables[kind](shape_of(after))
    if kind == "vertex":
        target = occt._local_add(occt._local_point(old_part), vector)
        return [candidate for candidate in candidates
                if occt._local_distance(target, occt._local_point(candidate)) <= 1e-8]
    return [candidate for candidate in candidates
            if occt._local_translated_match(old_part, candidate, kind, vector, 1e-8)]


P = r3.Point3d
profile = r3.PolyCurve()
assert profile.Append(r3.LineCurve(P(1, 0, 0), P(5, 0, 0)))
assert profile.Append(r3.Arc(P(5, 0, 0), P(6, 1, 0), P(5, 2, 0)).ToNurbsCurve())
assert profile.Append(r3.LineCurve(P(5, 2, 0), P(1, 2, 0)))
assert profile.Append(r3.Arc(P(1, 2, 0), P(0, 1, 0), P(1, 0, 0)).ToNurbsCurve())
assert profile.IsClosed
extrusion = bridge.make_extrusion(profile, 4.0)
source_shape = shape_of(extrusion)
source_recipe = kernel.recipe_of(extrusion)
source_topology = topology(source_shape)
curved_face = next(index for index, face in enumerate(occt.unique_faces(source_shape), 1)
                   if occt.face_plane(face) is None)

for kind, index, vector in (
        ("face", curved_face, (0.0, 0.0, 0.2)),
        ("edge", 1, (0.2, 0.0, 0.0)),
        ("vertex", 1, (0.2, 0.0, 0.0))):
    moved = bridge.move_subobject(extrusion, kind, index, vector)
    moved_shape = shape_of(moved)
    recipe = kernel.recipe_of(moved)
    assert topology(moved_shape) == source_topology
    assert occt.is_valid(moved_shape) and occt.is_airtight(moved_shape)
    assert kernel.honesty_problem(moved) is None
    assert recipe["kind"] == "local_move" and recipe["a"] == source_recipe
    assert len(translated_matches(extrusion, moved, kind, index, vector)) == 1
    replay = occt.from_recipe(recipe)
    assert topology(replay) == source_topology
    assert occt.is_valid(replay) and occt.is_airtight(replay)
    assert abs(occt.volume(replay) - occt.volume(moved_shape)) <= 1e-8
    print(f"PASS: exact rounded-extrusion {kind} movement and recipe replay")

cylinder = bridge.make_cylinder((0, 0, 0), 3.0, 5.0)
wall = next(index for index, face in enumerate(occt.unique_faces(shape_of(cylinder)), 1)
            if occt.face_plane(face) is None)
# The wall carries both rims and so every vertex: moving it moves the whole cylinder
# rigidly (bug #110 matrix; this was refused before), exactly and keeping its type.
moved_cylinder = shape_of(bridge.move_subobject(cylinder, "face", wall, (0.2, 0.0, 0.0)))
assert occt.is_valid(moved_cylinder) and occt.is_airtight(moved_cylinder)
assert occt.face_types(moved_cylinder) == occt.face_types(shape_of(cylinder))
assert abs(occt.volume(moved_cylinder) - occt.volume(shape_of(cylinder))) <= 1e-9
before = sorted(occt._local_point(v) for v in occt._unique_vertices(shape_of(cylinder)))
after = sorted(occt._local_point(v) for v in occt._unique_vertices(moved_cylinder))
assert all(occt._local_distance((a[0] + 0.2, a[1], a[2]), b) <= 1e-9 for a, b in zip(before, after))
print("PASS: a cylinder wall move translates the whole cylinder exactly")
# Real SDK/master boundary: imported solids can have no construction recipe.
# A native-master parent preserves exact replay without editing the source.
from OCP.ShapeFix import ShapeFix_ShapeTolerance
high_master = occt.from_recipe(kernel.recipe_of(cylinder))
ShapeFix_ShapeTolerance().SetTolerance(high_master, 0.02)
without_history = bridge._proxy_from_occt(high_master)
kernel.register_master(without_history, high_master)
assert kernel.recipe_of(without_history) is None
moved = bridge.move_subobject(without_history, "face", wall, (0.2, 0.0, 0.0))
recipe = kernel.recipe_of(moved)
assert kernel.recipe_of(without_history) is None
assert occt.is_valid(shape_of(moved)) and occt.is_airtight(shape_of(moved))
assert topology(shape_of(moved)) == topology(shape_of(without_history))
assert len(translated_matches(without_history, moved, "face", wall, (0.2, 0.0, 0.0))) == 1
replay = occt.from_recipe(recipe)
assert recipe["kind"] == "native_master"
assert occt.is_valid(replay) and occt.is_airtight(replay)
assert abs(occt.volume(replay) - occt.volume(shape_of(moved))) <= 1e-8
# Actual host persistence: the high-tolerance parent must not block later Save.
import tempfile
from pathlib import Path
from narwhal3d.model import NarwhalModel
from narwhal3d import narwhal_document
with tempfile.TemporaryDirectory(prefix="master-move-roundtrip-") as directory:
    path = Path(directory) / "moved.narwhal"
    model = NarwhalModel()
    object_id, _ = model.add_object(moved, "Imported edit")
    assert narwhal_document.save_model(model, path)
    reopened = narwhal_document.load_model(path)
    narwhal_document.commit_masters(reopened)
    item = reopened.file.Objects.FindId(object_id)
    assert kernel.recipe_of(item.Geometry)["kind"] == "native_master"
    second = bridge.move_subobject(item.Geometry, "face", wall, (0.1, 0.0, 0.0))
    reopened.replace_geometry(object_id, second)
    assert narwhal_document.save_model(reopened, path)
    final = narwhal_document.load_model(path)
    narwhal_document.commit_masters(final)
    exact = shape_of(final.file.Objects.FindId(object_id).Geometry)
    assert occt.is_valid(exact) and occt.is_airtight(exact)
    assert topology(exact) == topology(shape_of(without_history))
assert kernel.recipe_of(without_history) is None
# A recipe-less planar master must also use native editing, not the Python
# planar-polyhedron compatibility implementation.
box_master = occt.make_box(2, 3, 4)
box_proxy = bridge._proxy_from_occt(box_master)
kernel.register_master(box_proxy, box_master)
assert kernel.recipe_of(box_proxy) is None
edited_box = bridge.move_subobject(box_proxy, "face", 1, (-0.1, 0.0, 0.0))
assert kernel.recipe_of(edited_box)["kind"] == "native_master"
assert kernel.recipe_of(box_proxy) is None
assert occt.is_valid(shape_of(edited_box)) and occt.is_airtight(shape_of(edited_box))
print("PASS: imported snapshot edits save, reload, move again and save exactly")
print("BUG 105 EXACT SUBOBJECT MOVEMENT PASSED")
