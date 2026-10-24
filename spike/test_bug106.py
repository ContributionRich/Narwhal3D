"""Bug #106: move a six-edge extrusion-cap vertex along the extrusion axis exactly."""
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
    print(f"SKIP: bug #106 needs OCCT ({occt.unavailable_reason()})")
    raise SystemExit(0)


def shape_of(geometry):
    return kernel.master_of(geometry) or bridge._occt_operand(geometry)


def topology(shape):
    return (len(occt.unique_faces(shape)), len(occt._unique_edges(shape)),
            len(occt._unique_vertices(shape)))


P = r3.Point3d
profile = r3.PolyCurve()
assert profile.Append(r3.LineCurve(P(0, 0, 0), P(4, 0, 0)))
assert profile.Append(r3.Arc(P(4, 0, 0), P(6, 2, 0), P(4, 4, 0)).ToNurbsCurve())
assert profile.Append(r3.LineCurve(P(4, 4, 0), P(2, 4, 0)))
assert profile.Append(r3.LineCurve(P(2, 4, 0), P(0, 4, 0)))
assert profile.Append(r3.LineCurve(P(0, 4, 0), P(-1, 2, 0)))
assert profile.Append(r3.LineCurve(P(-1, 2, 0), P(0, 0, 0)))
assert profile.IsClosed and profile.SegmentCount == 6

extrusion = bridge.make_extrusion(profile, 5.0)
source = shape_of(extrusion)
assert topology(source) == (8, 18, 12)
vertices = occt._unique_vertices(source)
points = [occt._local_point(vertex) for vertex in vertices]
top = max(point[2] for point in points)
selected_index = min(
    (index for index, point in enumerate(points, 1) if abs(point[2] - top) <= 1e-9),
    key=lambda index: (-points[index - 1][0], points[index - 1][1]))
old_point = points[selected_index - 1]
vector = (0.0, 0.0, 0.25)

moved = bridge.move_subobject(extrusion, "vertex", selected_index, vector)
moved_shape = shape_of(moved)
recipe = kernel.recipe_of(moved)
assert topology(moved_shape) == topology(source)
assert occt.is_valid(moved_shape) and occt.is_airtight(moved_shape)
assert kernel.honesty_problem(moved) is None
assert recipe["kind"] == "local_move" and recipe["a"] == kernel.recipe_of(extrusion)
new_points = [occt._local_point(vertex) for vertex in occt._unique_vertices(moved_shape)]
target = tuple(old_point[index] + vector[index] for index in range(3))
assert sum(occt._local_distance(target, point) <= 1e-8 for point in new_points) == 1
assert occt.face_types(moved_shape) == {"PLANE": 6, "BSPLINE": 2}

replay = occt.from_recipe(recipe)
assert topology(replay) == topology(source)
assert occt.is_valid(replay) and occt.is_airtight(replay)
assert occt.face_types(replay) == occt.face_types(moved_shape)
assert abs(occt.volume(replay) - occt.volume(moved_shape)) <= 1e-8
print("BUG 106 SIX-EDGE CAP VERTEX MOVEMENT PASSED")
