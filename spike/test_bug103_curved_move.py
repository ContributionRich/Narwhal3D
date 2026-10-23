"""Bug #103: exact local movement after nested box edits and an analytic fillet."""
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


load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = load("narwhal3d.bridge", "narwhal3d/bridge.py")
from narwhal3d import kernel, native_geometry  # noqa: E402
from narwhal3d.kernel import occt_backend as occt, onbrep  # noqa: E402

if not occt.available():
    print(f"SKIP: bug #103 needs OCCT ({occt.unavailable_reason()})")
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
        return [position for position, candidate in enumerate(candidates, 1)
                if occt._local_distance(target, occt._local_point(candidate)) <= 1e-8]
    return [position for position, candidate in enumerate(candidates, 1)
            if occt._local_translated_match(old_part, candidate, kind, vector, 1e-8)]


# Public-safe deterministic reconstruction of private report file 0103.3dm.
x0, y0 = -6.477947513076515, -33.190338963562695
dx, dy, dz = 34.1133890286797, 36.843464326216235, 37.15776896423896
box = bridge.make_box((x0, y0, 0.0), (x0 + dx, y0 + dy, 0.0), dz)
edge_moved = bridge.move_subobject(
    box, "edge", 6, (25.787460065679063, 0.0, 14.664984059400204))
vertex_moved = bridge.move_subobject(
    edge_moved, "vertex", 6, (-11.01420535459281, -35.323344422237696, 0.0))
filleted = bridge.bevel_edges_occt(vertex_moved, 5.0, edge_indices=[8])
source_shape = shape_of(filleted)
source_recipe = kernel.recipe_of(filleted)
assert source_shape is not None and source_recipe["kind"] == "fillet"
assert occt.face_types(source_shape) == {"BSPLINE": 4, "PLANE": 3}
assert occt.is_airtight(source_shape) and occt.is_valid(source_shape)
assert bridge._modifier_base(source_recipe)["kind"] == "box"
writer_available = onbrep.available()
if writer_available:
    assert onbrep.exact_brep(source_shape, occt) is not None, onbrep.last_error()
assert len(source_recipe.get("edge_targets", ())) == 1

saved_native_move = native_geometry.move_component
native_geometry.move_component = lambda *_args, **_kwargs: native_geometry.MoveResult("unavailable")
try:
    compatibility_parent = occt.from_recipe(source_recipe["a"])
    compatibility_index = occt._local_resolve_target(
        compatibility_parent, "edge", source_recipe["edges"][0],
        source_recipe["edge_targets"][0], occt._local_tolerances(compatibility_parent)) + 1
    assert compatibility_index != source_recipe["edges"][0]
    compatibility_fillet = occt.from_recipe(source_recipe)
    assert occt.is_valid(compatibility_fillet) and occt.is_airtight(compatibility_fillet)
    assert occt.face_types(compatibility_fillet) == occt.face_types(source_shape)
finally:
    native_geometry.move_component = saved_native_move

cases = (
    ("vertex", 3, {"BSPLINE": 4, "PLANE": 3}),
    ("face", 5, {"BSPLINE": 4, "PLANE": 3}),
    ("vertex", 10, {"BSPLINE": 5, "PLANE": 2}),
    ("face", 7, {"BSPLINE": 4, "PLANE": 3}),
)
vector = (0.25, 0.0, 0.0)
for kind, index, expected_types in cases:
    moved = bridge.move_subobject(filleted, kind, index, vector)
    moved_shape = kernel.master_of(moved)
    recipe = kernel.recipe_of(moved)
    assert moved_shape is not None and recipe["kind"] == "local_move"
    assert recipe["a"] == source_recipe
    assert occt.face_types(moved_shape) == expected_types
    assert occt.is_airtight(moved_shape) and occt.is_valid(moved_shape)
    assert kernel.honesty_problem(moved) is None
    if writer_available:
        assert onbrep.exact_brep(moved_shape, occt) is not None, onbrep.last_error()
    assert len(translated_matches(filleted, moved, kind, index, vector)) == 1
    rebuilt = occt.from_recipe(recipe)
    assert occt.is_airtight(rebuilt) and occt.is_valid(rebuilt)
    assert occt.face_types(rebuilt) == expected_types
    assert abs(occt.volume(rebuilt) - occt.volume(moved_shape)) <= 1e-8

print("BUG 103 CURVED MOVE PASSED")
