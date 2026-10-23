"""Bug #104: accept certified creases and move parts of one-face Sweep1 surfaces."""
import importlib.util
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.environ.get("NARWHAL_GEOMETRY_CORE"):
    candidate = os.path.join(
        ROOT, "native", "geometry_core", "target", "release",
        "libnarwhal_geometry_core.so")
    if os.path.isfile(candidate):
        os.environ["NARWHAL_GEOMETRY_CORE"] = candidate
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

from narwhal3d import native_geometry  # noqa: E402
from narwhal3d.kernel import occt_backend as occt  # noqa: E402

if not occt.available():
    print(f"SKIP: bug #104 needs OCCT ({occt.unavailable_reason()})")
    raise SystemExit(0)


def shape_of(geom):
    return kernel.master_of(geom) or bridge._occt_operand(geom)


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


def topology(shape):
    return (len(occt.unique_faces(shape)), len(occt._unique_edges(shape)),
            len(occt._unique_vertices(shape)))


# Public-safe reconstruction of the report's nested box/edit/fillet history.
x0, y0 = -26.19090950681972, -30.689229845613454
dx, dy, dz = 45.30876687336976, 33.99993656031093, 34.738523310599035
filleted = bridge.make_box((x0, y0, 0.0), (x0 + dx, y0 + dy, 0.0), dz)
filleted = bridge.move_subobject(
    filleted, "edge", 12, (0.0, 29.09533029819947, 16.614288344859673))
filleted = bridge.move_subobject(
    filleted, "vertex", 8, (-26.684831330963732, -13.510004384950058, 0.0))
filleted = bridge.bevel_edges_occt(filleted, 5.0, edge_indices=[12])
assert filleted is not None
filleted = bridge.move_subobject(
    filleted, "face", 7, (-17.86489128555846, -4.744562996416791,
                           -4.643615783896259))
filleted = bridge.move_subobject(
    filleted, "vertex", 7, (0.0, 0.0, -26.515402016394024))
filleted_shape = shape_of(filleted)
assert topology(filleted_shape) == (7, 15, 10)
assert occt.face_types(filleted_shape) == {"BSPLINE": 5, "PLANE": 2}
assert occt.is_valid(filleted_shape) and occt.is_airtight(filleted_shape)

crease_vector = (0.0, 0.0, 0.25)
creased = bridge.move_subobject(filleted, "vertex", 8, crease_vector)
creased_shape = shape_of(creased)
assert topology(creased_shape) == topology(filleted_shape)
assert occt.face_types(creased_shape) == occt.face_types(filleted_shape)
assert occt.is_valid(creased_shape) and occt.is_airtight(creased_shape)
assert len(translated_matches(filleted, creased, "vertex", 8, crease_vector)) == 1
assert kernel.recipe_of(creased)["kind"] == "local_move"
creased_replay = occt.from_recipe(kernel.recipe_of(creased))
assert occt.is_valid(creased_replay) and occt.is_airtight(creased_replay)
assert abs(occt.volume(creased_replay) - occt.volume(creased_shape)) <= 1e-8

# Exact one-face ruled strip produced by the report's straight-rail Sweep1 family. Build the
# NURBS face directly so this regression does not depend on the optional linked `.3dm` writer
# used to materialize an OCCT display proxy in development/release installations.
section = r3.NurbsCurve.Create(False, 3, [
    r3.Point3d(-4.0, 0.0, 0.0), r3.Point3d(-1.5, 0.0, 2.0),
    r3.Point3d(1.5, 0.0, -2.0), r3.Point3d(4.0, 0.0, 0.0)])
section_end = r3.NurbsCurve.Create(False, 3, [
    r3.Point3d(-4.0, 12.0, 0.0), r3.Point3d(-1.5, 12.0, 2.0),
    r3.Point3d(1.5, 12.0, -2.0), r3.Point3d(4.0, 12.0, 0.0)])
surface = r3.NurbsSurface.CreateRuledSurface(section, section_end)
swept = r3.Brep.CreateFromSurface(surface)
assert swept is not None and bridge.record_exact_recipe(swept)
assert kernel.recipe_of(swept)["kind"] == "brep_data"
swept_shape = shape_of(swept)
assert topology(swept_shape) == (1, 4, 4)
assert occt.face_types(swept_shape) == {"BSPLINE": 1}
assert occt.is_valid(swept_shape) and not occt.is_airtight(swept_shape)

vector = (0.25, 0.125, 0.375)
for kind, index in (("vertex", 3), ("edge", 1), ("face", 1)):
    moved = bridge.move_subobject(swept, kind, index, vector)
    moved_shape = shape_of(moved)
    recipe = kernel.recipe_of(moved)
    assert topology(moved_shape) == (1, 4, 4)
    assert occt.face_types(moved_shape) == {"BSPLINE": 1}
    assert occt.is_valid(moved_shape) and not occt.is_airtight(moved_shape)
    assert kernel.honesty_problem(moved) is None
    assert recipe["kind"] == "local_move" and recipe["a"] == kernel.recipe_of(swept)
    assert len(translated_matches(swept, moved, kind, index, vector)) == 1
    replay = occt.from_recipe(recipe)
    assert topology(replay) == (1, 4, 4)
    assert occt.is_valid(replay) and not occt.is_airtight(replay)
    old_area, _ = occt._local_part_measure(moved_shape, "face")
    replay_area, _ = occt._local_part_measure(replay, "face")
    assert abs(old_area - replay_area) <= 1e-8

# Complete compatibility fallback: unavailable native code still moves all Sweep1 parts.
saved_native_move = native_geometry.move_component
native_geometry.move_component = lambda *_args, **_kwargs: native_geometry.MoveResult("unavailable")
try:
    for kind, index in (("vertex", 3), ("edge", 1), ("face", 1)):
        moved = bridge.move_subobject(swept, kind, index, vector)
        moved_shape = shape_of(moved)
        assert topology(moved_shape) == (1, 4, 4)
        assert occt.is_valid(moved_shape) and not occt.is_airtight(moved_shape)
        assert len(translated_matches(swept, moved, kind, index, vector)) == 1
finally:
    native_geometry.move_component = saved_native_move

print("BUG 104 SUBOBJECT MOVE PASSED")
