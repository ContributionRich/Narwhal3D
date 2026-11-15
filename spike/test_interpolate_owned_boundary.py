"""Interpolation retirement host contracts; all computation belongs to Rust.

No Python solver/oracle. Small exact dyadic fixtures below are host framing
checks; the 2465 independently captured helper answers live in Rust tests.
"""
import ctypes
import importlib.util
import os
from pathlib import Path
import sys
import types
import tempfile

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg

def load(name):
    spec = importlib.util.spec_from_file_location("narwhal3d." + name, ROOT / "narwhal3d" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    setattr(pkg, name, mod)
    return mod

native = load("native_interpolate")
live = load("interpolate")
checks = 0

def check(value):
    global checks
    assert value
    checks += 1

def refusal(fn, args, message, cls=live.InterpolationError):
    global checks
    try:
        fn(*args)
    except cls as error:
        assert str(error) == message, (str(error), message)
        checks += 1
    else:
        raise AssertionError((fn.__name__, "accepted refused input"))

pts = [(0., 0., 0.), (1., 0., 0.), (3., 0., 0.)]
check(live.parameters(pts) == [0., 1/3, 1.])
check(live.parameters(pts, "Uniform") == [0., .5, 1.])
check(live.closed_parameters(pts, "Chord") == [0., 1/6, .5, 1.])
check(live.averaged_knots([0., .5, 1.], 2) == [0., 0., 0., 1., 1., 1.])
knots = [0., 0., 0., 1., 1., 1.]
check(live.find_span(3, 2, .5, knots) == 2)
check(live.basis_values(2, .5, 2, knots) == [.25, .5, .25])
check(live.collocation([0., .5, 1.], knots, 3, 2) == [[1., 0., 0.], [.25, .5, .25], [0., 0., 1.]])
check(live.periodic_knots([0., .5, 1.], 1) == [-.5, 0., .5, 1., 1.5])
a = [[1., 0.], [0., 2.]]
lu, perm = live.lu_factor(a)
check((lu, perm) == (a, [0, 1]) and all(isinstance(i, int) for i in perm))
check(a == [[1., 0.], [0., 2.]] and lu is not a and all(x is not y for x, y in zip(lu, a)))
solved = live.lu_solve(lu, perm, [[2., 4., 6., 8.], [4., 8., 12., 16.]])
check(solved == [[2., 4., 6., 8.], [2., 4., 6., 8.]] and all(isinstance(row, list) for row in solved))
solved = live.cyclic_tridiagonal_solve([0.]*3, [2.]*3, [0.]*3, [(2., 4.), (6., 8.), (10., 12.)])
check(solved == [(1., 2.), (3., 4.), (5., 6.)] and all(isinstance(row, tuple) for row in solved))
check(live.lu_factor([]) == ([], []))
check(live.averaged_knots([0.], 0) == [0., 1.])
check(live.skin_control_net([[], [], []]) == ([], [0., 0., 0., 1., 1., 1.], 2))
check(live.skin_control_net([[p] for p in pts], 3, "not-a-style") == live.skin_control_net([[p] for p in pts], 3, "Uniform"))

# Exact messages captured from the archived public implementation.
for fn, args, message in (
    (live.parameters, (pts, "Unknown"), "knot style 'Unknown' not implemented; the reference CAD application's are Uniform, Chord, SqrtChrd"),
    (live.parameters, (pts[:1],), "interpolation needs at least 2 points (got 1)"),
    (live.parameters, ([pts[0], pts[1], pts[1]],), "points 1 and 2 are coincident, so a Chord parameterisation is undefined; remove the duplicate or use Uniform"),
    (live.closed_parameters, ([pts[0], pts[1], pts[0]],), "points 2 and 0 are coincident, so a closed curve through them is undefined; remove the duplicate"),
    (live.averaged_knots, ([0., 1.], 3), "a degree-3 interpolation needs at least 4 points (got 2)"),
    (live.lu_factor, ([[1., 2.], [2., 4.]],), live._SINGULAR),
    (live.interpolate_points, (pts, 0), "degree must be >= 1 (got 0)"),
    (live.interpolate_points, (pts, 2), "interpolation degree must be ODD (got 2); the reference CAD application's own CreateInterpolatedCurve documents 'Degree must be odd' and works best at 3"),
    (live.interpolate_points_periodic, (pts[:2],), "a closed interpolation needs at least 3 points (got 2)"),
    (live.skin_control_net, ([[pts[0]], []],), "section 1 has 0 control points but section 0 has 1; make the sections compatible before skinning"),
    (live.interpolate_points, ([pts[0], pts[1], pts[1]],), "points 1 and 2 are coincident, so a Chord parameterisation is undefined; remove the duplicate or use Uniform"),
    (live.interpolate_points_periodic, ([pts[0], pts[1], pts[0]],), "points 2 and 0 are coincident, so a closed curve through them is undefined; remove the duplicate"),
):
    refusal(fn, args, message)
refusal(live.averaged_knots, ([0., .5, 1.], 0), "float division by zero", ZeroDivisionError)
refusal(live.lu_solve, ([[1., 0.], [0., 0.]], [0, 1], [[1.], [1.]]), "float division by zero", ZeroDivisionError)
refusal(live.cyclic_tridiagonal_solve, ([0.]*3, [0.]*3, [0.]*3, [(1.,)]*3), "float division by zero", ZeroDivisionError)
refusal(live.cyclic_tridiagonal_solve, ([0]*3, [0]*3, [0]*3, [(1,)]*3), "division by zero", ZeroDivisionError)
refusal(live.cyclic_tridiagonal_solve, ([0.]*3, [1., 0., 1.], [0.]*3, [(1.,)]*3), live._CLOSED_SINGULAR)

# Route the formerly Python-only smallest inputs through the actual C library.
for fn, args, counter in (
    (live.interpolate_points, (pts[:2],), "_native_calls"),
    (live.interpolate_points, (pts,), "_native_calls"),
    (live.skin_control_net, ([[p] for p in pts],), "_native_skin_calls"),
    (live.interpolate_points_periodic, (pts,), "_native_periodic_calls"),
):
    before = getattr(native, counter)
    fn(*args)
    check(getattr(native, counter) == before + 1)

# Missing/stale ABI may not revive a Python solve, even if old solve symbols exist.
original = native._helper_binding
try:
    native._helper_binding = None
    for fn, args in ((live.parameters, (pts,)), (live.interpolate_points, (pts,)), (live.interpolate_points_periodic, (pts,)), (live.skin_control_net, ([[p] for p in pts],))):
        refusal(fn, args, "exact interpolation helper ABI v1 is unavailable; install the matching native core (no Python fallback)", RuntimeError)
finally:
    native._helper_binding = original

# Corrupt success counts and explicit native refusals cannot become public results.
original = native._helper_binding
before = native._native_helper_calls
try:
    for code in (0, -2, -4):
        native._helper_binding = lambda *args, code=code: code
        try:
            live.parameters(pts)
        except (RuntimeError if code == 0 else live.InterpolationError):
            check(native._native_helper_calls == before)
        else:
            raise AssertionError("accepted malformed helper framing")
finally:
    native._helper_binding = original

# Host arrays remain caller-owned and failure payloads remain untouched.
data = (ctypes.c_double * 4)(1., 2., 2., 4.)
out = (ctypes.c_double * 8)(*[123.] * 8)
req = native.HelperRequest(6, 0, 2, 0, 0, 0, 0., data, 4)
used, detail = ctypes.c_uint64(99), ctypes.c_uint64(99)
check(native._helper_fn()(ctypes.byref(req), out, 8, ctypes.byref(used), ctypes.byref(detail)) == -4)
check(used.value == 0 and list(out) == [123.] * 8)
# Public direct-file loading remains usable by edgepick outside Blender.
spec = importlib.util.spec_from_file_location("_standalone_interpolation", ROOT / "narwhal3d/interpolate.py")
standalone = importlib.util.module_from_spec(spec)
spec.loader.exec_module(standalone)
check(standalone.basis_values(2, .5, 2, knots) == [.25, .5, .25])
edgepick = load("edgepick")
curve = {"ctype": "nurbs", "degree": 2, "poles": pts,
         "weights": [1., 1., 1.], "knots": knots}
check(edgepick.evaluate(curve, 0.) == pts[0])
check(edgepick.evaluate(curve, 1.) == pts[-1])
# Huge requested degrees still clamp before crossing the fixed-width ABI.
check(live.interpolate_points(pts, 10**100 + 1) == live.interpolate_points(pts, 3))
check(live.skin_control_net([[p] for p in pts], 10**100) == live.skin_control_net([[p] for p in pts], 3))
# Preserve measured pre-retirement threshold-dependent skinning edge contracts.
small = [[(float(i), 0., 0., 7.)] for i in range(3)]
big = [[(float(i), float(j), 0., 7.) for j in range(8)] for i in range(8)]
refusal(live.skin_control_net, (small, 0), "float division by zero", ZeroDivisionError)
refusal(live.skin_control_net, (big, 0, "Unknown"), "float division by zero", ZeroDivisionError)
check(live.skin_control_net(big, 0)[2] == 1)
check(len(live.skin_control_net(small, 1)[0][0][0]) == 4)
check(len(live.skin_control_net(big, 1)[0][0][0]) == 3)
# Real loader failure and environment-key invalidation, not just monkeypatching.
saved = os.environ.get("NARWHAL_PURE_CORE")
try:
    with tempfile.TemporaryDirectory(prefix="interpolation-abi-") as directory:
        os.environ["NARWHAL_PURE_CORE"] = str(Path(directory) / "missing-native-core")
        refusal(live.interpolate_points, (pts,), "exact interpolation helper ABI v1 is unavailable; install the matching native core (no Python fallback)", RuntimeError)
        check(native.interpolate(pts, 3, "Chord") is None)
finally:
    if saved is None:
        os.environ.pop("NARWHAL_PURE_CORE", None)
    else:
        os.environ["NARWHAL_PURE_CORE"] = saved
check(live.parameters(pts) == [0., 1/3, 1.])
# A required adapter may not quietly return the old optional None sentinel.
for adapter, fn, args in (
    ("interpolate", live.interpolate_points, (pts,)),
    ("interpolate_periodic", live.interpolate_points_periodic, (pts,)),
    ("skin_control_net", live.skin_control_net, ([[p] for p in pts],)),
):
    original = getattr(native, adapter)
    try:
        setattr(native, adapter, lambda *args, **kwargs: None)
        refusal(fn, args, "required native interpolation returned no result; install the matching native core (protocol error; no Python fallback)", RuntimeError)
    finally:
        setattr(native, adapter, original)

# Real adapter framing failures are infrastructure errors, not geometry declines.
# The same C boundary's singular status must retain InterpolationError by name.
for binding, fn, args, counter, singular in (
    ("_binding", live.interpolate_points, (pts,), "_native_calls", live._SINGULAR),
    ("_periodic_binding", live.interpolate_points_periodic, (pts,), "_native_periodic_calls", live._CLOSED_SINGULAR),
    ("_skin_binding", live.skin_control_net, ([[p] for p in pts],), "_native_skin_calls", live._SINGULAR),
):
    fn(*args)  # Resolve the actual binding/signature before the injected replies.
    original = getattr(native, binding)
    before = getattr(native, counter)
    try:
        setattr(native, binding, lambda *args: 0)  # malformed success: zero counts
        refusal(fn, args, "native interpolation returned invalid output framing; install the matching native core (protocol error; no Python fallback)", RuntimeError)
        setattr(native, binding, lambda *args: -4)
        refusal(fn, args, singular)
        check(getattr(native, counter) == before)
    finally:
        setattr(native, binding, original)
print(f"{checks} interpolation-owned boundary checks passed")
