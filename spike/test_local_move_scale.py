"""Exact non-uniform scaling of a locally edited NURBS solid."""
import importlib.util
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.environ.get("NARWHAL_GEOMETRY_CORE"):
    for library in ("libnarwhal_geometry_core.so", "libnarwhal_geometry_core.dylib",
                    "narwhal_geometry_core.dll"):
        candidate = os.path.join(
            ROOT, "native", "geometry_core", "target", "release", library)
        if os.path.isfile(candidate):
            os.environ["NARWHAL_GEOMETRY_CORE"] = candidate
            break
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
from narwhal3d import kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as occt  # noqa: E402

if not occt.available():
    print(f"SKIP: exact scale needs OCCT ({occt.unavailable_reason()})")
    raise SystemExit(0)

box = bridge.make_box((0, 0, 0), (4, 4, 0), 4)
edited = bridge.move_subobject(box, "vertex", 1, (0.5, 0.2, 1.0))
before = kernel.master_of(edited)
assert before is not None and occt.is_airtight(before)
before_volume = occt.volume(before)

calls = []
original = occt.transform


def guarded(shape, matrix, **kwargs):
    if shape.IsSame(before):
        calls.append(shape)
        raise RuntimeError("finished locally edited BRep entered GTransform")
    return original(shape, matrix, **kwargs)


occt.transform = guarded
try:
    plane = r3.Plane.WorldXY()
    scaled = bridge.transform_geometry(
        edited, r3.Transform.Scale(plane, 2.0, 1.0, 1.0))
finally:
    occt.transform = original

after = kernel.master_of(scaled)
assert not calls, "locally edited solids must replay instead of transforming the finished BRep"
assert after is not None and occt.is_airtight(after)
assert abs(occt.volume(after) - 2.0 * before_volume) < 1e-8
assert occt.face_types(after) == {"PLANE": 3, "BSPLINE": 3}
assert kernel.honesty_problem(scaled) is None
print("LOCAL MOVE SCALE PASSED")
