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
try:
    bridge.move_subobject(cylinder, "face", wall, (0.2, 0.0, 0.0))
except ValueError as error:
    message = str(error)
    assert "native local move refused" in message and "not implemented" not in message
else:
    raise AssertionError("the unsupported cylinder-wall deformation unexpectedly succeeded")
print("PASS: unsupported curved deformation reaches exact certification, not a blanket refusal")
print("BUG 105 EXACT SUBOBJECT MOVEMENT PASSED")
