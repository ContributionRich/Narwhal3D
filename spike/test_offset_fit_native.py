"""Offset deviation on the Rust nearest search, the legacy fallback, and the Offset preview memo.

The retained compatibility fitter (`offset._offset_control_polygon_approx`) keeps its
48, 96, 192 ... sample schedule and 201 targets per native/legacy nearest check. Those
INNER validation contracts are checked separately from public compact output. Public
outputs must instead have fewer controls AND pass a dense independent true-offset oracle.
Missing/declining nearest support still runs the legacy scan for the whole inner check.

The legacy algorithm is frozen below (`legacy_*`), copied from `offset.py` before the change,
so parity and fallback are checked against the old code and not against the new code itself.

Run: scripts/run_suite.sh spike/test_offset_fit_native.py
Optional additional personal fixture: NARWHAL_TEST_OFFSET_PRIVATE_3DM=/path/to/report.3dm
The default suite never reads a personal document.
"""
import importlib.util
import math
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = pkg


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("narwhal3d.cplane", "narwhal3d/cplane.py")
off = _load("narwhal3d.offset", "narwhal3d/offset.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
from narwhal3d import nearest, native_nearest  # noqa: E402

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


# ---------------------------------------------------------------------------
# the frozen legacy oracle (copied from offset.py before the native change)
# ---------------------------------------------------------------------------

def legacy_dist_point_to_curve(curve, p, samples=200):
    d = curve.Domain
    best_t, best = d.T0, math.inf
    for i in range(samples + 1):
        t = d.T0 + (d.T1 - d.T0) * i / samples
        q = curve.PointAt(t)
        dd = math.dist(p, (q.X, q.Y, q.Z))
        if dd < best:
            best, best_t = dd, t
    step = (d.T1 - d.T0) / samples
    lo, hi = max(d.T0, best_t - step), min(d.T1, best_t + step)

    def at(t):
        q = curve.PointAt(t)
        return math.dist(p, (q.X, q.Y, q.Z))

    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c1, c2 = hi - gr * (hi - lo), lo + gr * (hi - lo)
    f1, f2 = at(c1), at(c2)
    for _ in range(50):
        if hi - lo < 1e-15:
            break
        if f1 < f2:
            hi, c2, f2 = c2, c1, f1
            c1 = hi - gr * (hi - lo)
            f1 = at(c1)
        else:
            lo, c1, f1 = c1, c2, f2
            c2 = lo + gr * (hi - lo)
            f2 = at(c2)
    return at(0.5 * (lo + hi))


def legacy_deviation(curve, fit, distance, n, samples=200):
    d = curve.Domain
    worst = 0.0
    for i in range(samples + 1):
        target = off._offset_point(curve, d.T0 + (d.T1 - d.T0) * i / samples, distance, n)
        worst = max(worst, legacy_dist_point_to_curve(fit, target))
    return worst


def legacy_fit(curve, distance, n, tolerance=1e-3, max_samples=512):
    """(fit, max_deviation, samples) of the pre-change free-form approximation."""
    d = curve.Domain
    count = 48
    best = None
    while count <= max_samples:
        pts = [off._offset_point(curve, d.T0 + (d.T1 - d.T0) * i / count, distance, n)
               for i in range(count + 1)]
        fit = r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(*p) for p in pts], 3)
        dev = legacy_deviation(curve, fit, distance, n)
        best = (fit, dev, count)
        if dev <= tolerance:
            return best
        count *= 2
    return best


def cv_diff(a, b):
    if len(a.Points) != len(b.Points):
        return math.inf
    worst = 0.0
    for i in range(len(a.Points)):
        p, q = a.Points[i], b.Points[i]
        worst = max(worst, abs(p.X - q.X), abs(p.Y - q.Y), abs(p.Z - q.Z), abs(p.W - q.W))
    return worst


def samples_for(count):
    """The number of deviation checks the schedule runs before it accepts `count`."""
    return {48: 1, 96: 2, 192: 3, 384: 4, 512: 5}[count]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

# OURS: independent data, not sampled/copied from an owner document or artwork.
# The asymmetric harmonics produce uneven curvature and different +/- refinement
# counts; a periodic cubic seam exercises preparation, fallback and edited-data keys.
PROFILE = r3.NurbsCurve.Create(True, 3, [
    r3.Point3d((4.0 + 0.3 * math.cos(3*a)) * math.cos(a),
               (2.0 + 0.15 * math.sin(2*a)) * math.sin(a), 0.0)
    for a in (2*math.pi*i/14 for i in range(14))])
PROFILE.Domain = r3.Interval(-7.0, 29.0)
check(PROFILE.IsValid and PROFILE.IsClosed and PROFILE.IsPeriodic
      and PROFILE.Degree == 3 and len(PROFILE.Points) == 17,
      "mandatory independent adversarial profile: valid closed periodic cubic, 17 controls")
check(PROFILE.TryGetCircle() is None and PROFILE.TryGetArc() is None
      and not PROFILE.IsLinear(), "profile exercises freeform fitting, not an analytic shortcut")
PROFILE_NORMAL = (0.0, 0.0, 1.0)

# Explicit opt-in ONLY: absence of personal data never removes mandatory coverage.
# A requested missing/bad file is an error, not a silent skip or synthetic substitution.
PRIVATE_PROFILE = None
_private_path = os.environ.get("NARWHAL_TEST_OFFSET_PRIVATE_3DM")
if _private_path:
    _doc = r3.File3dm.Read(os.path.expanduser(_private_path))
    if _doc is None:
        raise RuntimeError("cannot read explicitly requested NARWHAL_TEST_OFFSET_PRIVATE_3DM")
    PRIVATE_PROFILE = next((o.Geometry for o in _doc.Objects
                            if o.Attributes.Name == "Projection3"), None)
    if not isinstance(PRIVATE_PROFILE, r3.NurbsCurve):
        raise RuntimeError("explicit private fixture has no Projection3 NURBS curve")


TILTED = PROFILE.Duplicate()
TILTED.Transform(r3.Transform.Rotation(math.radians(30), r3.Vector3d(1, 0, 0), r3.Point3d(0, 0, 0)))
TILTED_NORMAL = (0.0, -0.5, 0.8660254037844386)

CUSP = r3.NurbsCurve.CreateControlPointCurve(
    [r3.Point3d(*p) for p in [(0, 0, 0), (4, 0, 0), (0, 0.3, 0), (4, 0.6, 0), (0, 0.9, 0)]], 3)
CUSP_NORMAL = (0.0, 0.0, 1.0)

RATIONAL = r3.NurbsCurve(3, True, 4, 7)
for _k, _a in enumerate([j * 2 * math.pi / 7 for j in range(7)]):
    _w = 1.0 + 0.5 * (_k == 2) + 0.2 * (_k == 5)
    RATIONAL.Points[_k] = r3.Point4d(4 * math.cos(_a) * _w, 2 * math.sin(_a) * _w, 0.0, _w)
for _i, _v in enumerate([0.0, 0.0, 1.0, 2.2, 3.1, 4.4, 5.5, 6.0, 7.0]):
    RATIONAL.Knots[_i] = _v
RATIONAL_NORMAL = (0.0, 0.0, 1.0)


def fixture_list():
    fixtures = [("synthetic profile +0.25", PROFILE, 0.25, PROFILE_NORMAL),
                ("synthetic profile -0.25", PROFILE, -0.25, PROFILE_NORMAL),
                ("tilted synthetic profile +0.25", TILTED, 0.25, TILTED_NORMAL)]
    if PRIVATE_PROFILE is not None:
        fixtures.extend([("explicit private profile +0.25", PRIVATE_PROFILE, .25, PROFILE_NORMAL),
                         ("explicit private profile -0.25", PRIVATE_PROFILE, -.25, PROFILE_NORMAL)])
    return fixtures


# ---------------------------------------------------------------------------
# 1. parity, preparation reuse and native call budget (synthetic +/-, tilted)
# ---------------------------------------------------------------------------
print("1. compatibility fitter: frozen schedule parity and native inner-call budget")
legacy_calls = {"n": 0}
_real_legacy_dist = off._dist_point_to_curve
_real_prepared = nearest.PreparedCurve
prepared_calls = {"n": 0}


def _counting_dist(curve, p, samples=200):
    legacy_calls["n"] += 1
    return _real_legacy_dist(curve, p, samples)


def _counting_prepared(curve):
    prepared_calls["n"] += 1
    return _real_prepared(curve)


nearest.PreparedCurve = _counting_prepared
off._dist_point_to_curve = _counting_dist
for label, crv, d, n in fixture_list():
    ref_fit, ref_dev, ref_count = legacy_fit(crv, d, n)
    legacy_calls["n"] = 0
    before = native_nearest._native_calls
    prepared_calls["n"] = 0
    res = off._offset_control_polygon_approx(crv, d, n, 1e-3)
    calls = native_nearest._native_calls - before
    check(not res.exact and res.curve is not None, f"{label}: still an honest approximation")
    check(cv_diff(res.curve, ref_fit) <= 1e-12,
          f"{label}: same fit control points as the legacy schedule (diff {cv_diff(res.curve, ref_fit):.1e})")
    check(abs(res.max_deviation - ref_dev) <= 1e-9,
          f"{label}: deviation parity |new-legacy| = {abs(res.max_deviation - ref_dev):.2e} (<= 1e-9)")
    check(prepared_calls["n"] == samples_for(ref_count),
          f"{label}: one preparation per fit, not one per target ({prepared_calls['n']} preparations)")
    expected = 201 * samples_for(ref_count)
    check(calls == expected,
          f"{label}: native calls {calls} == 201 targets x {samples_for(ref_count)} checks = {expected}")
    check(legacy_calls["n"] == 0, f"{label}: the legacy scan did not run ({legacy_calls['n']} calls)")
off._dist_point_to_curve = _real_legacy_dist
nearest.PreparedCurve = _real_prepared

print("1b. public compact output: fewer controls AND independent true-offset accuracy")
for label, source, distance, normal in fixture_list():
    result = off.curve_offset(source, distance, normal, "Sharp", 1e-3)
    check(result.curve is not None and not result.exact and not result.warning,
          f"{label}: valid honest compact result")
    check(len(result.curve.Points) < 80, f"{label}: fewer than 80 controls, not the legacy sample-control polygon")
    d, f = source.Domain, result.curve.Domain
    worst = 0.0
    for i in range(16385):
        u = i / 16384
        p, tangent = source.PointAt(d.T0+(d.T1-d.T0)*u), source.TangentAt(d.T0+(d.T1-d.T0)*u)
        nx, ny, nz = normal
        x, y, z = ny*tangent.Z-nz*tangent.Y, nz*tangent.X-nx*tangent.Z, nx*tangent.Y-ny*tangent.X
        length = math.sqrt(x*x+y*y+z*z)
        q = result.curve.PointAt(f.T0+(f.T1-f.T0)*u)
        worst = max(worst, math.dist((p.X+distance*x/length, p.Y+distance*y/length,
                                     p.Z+distance*z/length), (q.X,q.Y,q.Z)))
    check(worst <= 1e-3 and result.max_deviation <= 8e-4,
          f"{label}: dense independent true-target error {worst:.9g} <= 0.001")

# ---------------------------------------------------------------------------
# 2. honesty: the native point IS PointAt(t), and its distance is to that point
# ---------------------------------------------------------------------------
print("2. native closest points are honest on every fixture")
for label, crv, fit_source in (("synthetic profile fit", PROFILE, (PROFILE, 0.25, PROFILE_NORMAL)),
                               ("cusp fit", CUSP, (CUSP, 0.2, CUSP_NORMAL)),
                               ("rational nonuniform fit", RATIONAL, (RATIONAL, 0.3, RATIONAL_NORMAL))):
    src, d, n = fit_source
    fit = legacy_fit(src, d, n)[0]
    prepared = nearest.PreparedCurve(fit)
    dom = src.Domain
    worst_point = worst_distance = 0.0
    for i in range(201):
        target = off._offset_point(src, dom.T0 + (dom.T1 - dom.T0) * i / 200, d, n)
        cp = prepared.closest(target)
        on_curve = fit.PointAt(cp.t)
        worst_point = max(worst_point, math.dist(cp.point, (on_curve.X, on_curve.Y, on_curve.Z)))
        worst_distance = max(worst_distance, abs(cp.distance - math.dist(cp.point, target)))
    check(worst_point <= 1e-9, f"{label}: point == fit.PointAt(t) (worst {worst_point:.1e})")
    check(worst_distance <= 1e-12, f"{label}: distance == |point - target| (worst {worst_distance:.1e})")

# ---------------------------------------------------------------------------
# 3. the cusp: native is the true nearest distance; the frozen legacy scan misses a basin
# ---------------------------------------------------------------------------
print("3. cusp: dense oracle, and the frozen legacy scan")
cusp_fit = legacy_fit(CUSP, 0.2, CUSP_NORMAL)[0]
cusp_prep = nearest.PreparedCurve(cusp_fit)
cdom = CUSP.Domain
disagree = []
native_dev = 0.0
for i in range(201):
    target = off._offset_point(CUSP, cdom.T0 + (cdom.T1 - cdom.T0) * i / 200, 0.2, CUSP_NORMAL)
    nd = cusp_prep.closest(target).distance
    ld = legacy_dist_point_to_curve(cusp_fit, target)
    native_dev = max(native_dev, nd)
    if abs(nd - ld) > 1e-9:
        disagree.append((target, nd, ld))


def dense_nearest(fit, p, n=20000):
    d = fit.Domain
    best = math.inf
    for i in range(n + 1):
        q = fit.PointAt(d.T0 + (d.T1 - d.T0) * i / n)
        best = min(best, math.dist(p, (q.X, q.Y, q.Z)))
    return best


check(len(disagree) > 0, f"cusp: the legacy scan and native differ on {len(disagree)} targets")
worst_over = 0.0
for target, nd, ld in disagree:
    dense = dense_nearest(cusp_fit, target)
    check(nd <= dense + 1e-9, f"cusp: native {nd:.3e} <= dense upper bound {dense:.3e}")
    worst_over = max(worst_over, ld - nd)
check(worst_over > 1e-4, f"cusp: the frozen legacy scan reports up to {worst_over:.2e} more than native")

# ---------------------------------------------------------------------------
# 4. the offset result on the cusp reports the native deviation, not the legacy one
# ---------------------------------------------------------------------------
print("4. cusp: the offset result uses the native deviation")
res_cusp = off.curve_offset(CUSP, 0.2, CUSP_NORMAL, "Sharp", 1e-3)
check(abs(res_cusp.max_deviation - native_dev) <= 1e-9,
      f"cusp: max_deviation {res_cusp.max_deviation:.6e} == native {native_dev:.6e}")

# ---------------------------------------------------------------------------
# 5. fallbacks: a missing native library, and a mid-run decline, both give the legacy result
# ---------------------------------------------------------------------------
print("5. fallback to the legacy scan when native is missing or declines")
_saved_env = os.environ.get("NARWHAL_PURE_CORE")
os.environ["NARWHAL_PURE_CORE"] = "/nonexistent/libnarwhal_pure_core.so"
check(not native_nearest.available(), "the missing-library control reports native unavailable")
for label, crv, d, n in (("synthetic profile +0.25", PROFILE, 0.25, PROFILE_NORMAL), ("cusp", CUSP, 0.2, CUSP_NORMAL)):
    ref_fit, ref_dev, ref_count = legacy_fit(crv, d, n)
    res = off._offset_control_polygon_approx(crv, d, n, 1e-3)
    check(cv_diff(res.curve, ref_fit) == 0.0 and abs(res.max_deviation - ref_dev) <= 1e-12,
          f"missing core, {label}: identical to the legacy result")
if _saved_env is None:
    del os.environ["NARWHAL_PURE_CORE"]
else:
    os.environ["NARWHAL_PURE_CORE"] = _saved_env
check(native_nearest.available(), "the native library is available again after restoring the env")

_real_search = native_nearest.search
_calls = {"n": 0}


def _declining_search(packed, target):
    _calls["n"] += 1
    if _calls["n"] > 5:
        return None
    return _real_search(packed, target)


native_nearest.search = _declining_search
try:
    ref_fit, ref_dev, _ = legacy_fit(CUSP, 0.2, CUSP_NORMAL)
    res = off.curve_offset(CUSP, 0.2, CUSP_NORMAL, "Sharp", 1e-3)
    check(abs(res.max_deviation - ref_dev) <= 1e-12,
          f"mid-run decline, cusp: legacy deviation {ref_dev:.6e} (not the Python search's value)")
finally:
    native_nearest.search = _real_search

# ---------------------------------------------------------------------------
# 6. the Offset preview memo (commands.OffsetCmd, preview only)
# ---------------------------------------------------------------------------
print("6. Offset preview memo")
fit_calls = {"n": 0}
_real_free_form = off._offset_free_form_approx


def _counting_free_form(*a, **k):
    fit_calls["n"] += 1
    return _real_free_form(*a, **k)


off._offset_free_form_approx = _counting_free_form
SOURCE = PROFILE.Duplicate()
BOX = (-20.0, -20.0, -20.0, 20.0, 20.0, 20.0)
CURSOR_A = (6.0, 4.0, 0.0)
CURSOR_B = (-6.0, -4.0, 0.0)


def resolve(name):
    return SOURCE if name == "src" else None


def preview(cmd, cursor=CURSOR_A):
    cmd.args = ["src"]
    return cmd.preview_curves(resolve, cursor)


def new_cmd(distance=0.25, tolerance=1e-3):
    cmd = commands.OffsetCmd()
    cmd.opt["Distance"] = distance
    cmd.opt["Tolerance"] = tolerance
    return cmd


def same_points(a, b):
    """Two preview outputs (lists of polylines of (x, y, z)) agree to 1e-12."""
    if len(a) != len(b):
        return False
    for pa, pb in zip(a, b):
        if len(pa) != len(pb):
            return False
        for qa, qb in zip(pa, pb):
            if max(abs(x - y) for x, y in zip(qa, qb)) > 1e-12:
                return False
    return True


cmd = new_cmd()
fit_calls["n"] = 0
first = preview(cmd)
check(fit_calls["n"] == 2, f"first preview fits both sides once ({fit_calls['n']} fits)")
second = preview(cmd)
check(fit_calls["n"] == 2 and same_points(first, second),
      f"repeated preview, unchanged source: no new fit ({fit_calls['n']} total), same points")
other_side = preview(cmd, CURSOR_B)
check(fit_calls["n"] == 2, f"a different cursor only picks a side: no new fit ({fit_calls['n']} total)")
fresh = preview(new_cmd(), CURSOR_B)
check(same_points(other_side, fresh), "the cursor side from the memo equals a fresh computation")

SOURCE.Points[0] = r3.Point4d(SOURCE.Points[0].X + 1.5, SOURCE.Points[0].Y, SOURCE.Points[0].Z, 1.0)
fit_calls["n"] = 0
edited = preview(cmd)
check(fit_calls["n"] == 2, f"a control-point edit of the same object refits ({fit_calls['n']} fits)")
check(not same_points(edited, second), "a control-point edit changes the preview (not an identity-only cache)")

fit_calls["n"] = 0
preview(cmd)
check(fit_calls["n"] == 0, "the edited source is memoised after its refit")
cmd.opt["Distance"] = 0.5
fit_calls["n"] = 0
preview(cmd)
check(fit_calls["n"] == 2, f"a distance change refits ({fit_calls['n']} fits)")
cmd.opt["Tolerance"] = 1e-2
fit_calls["n"] = 0
preview(cmd)
check(fit_calls["n"] == 2, f"a tolerance change refits ({fit_calls['n']} fits)")
cmd.opt["InCPlane"] = True
fit_calls["n"] = 0
preview(cmd)
check(fit_calls["n"] == 2, f"an InCPlane change refits ({fit_calls['n']} fits)")

fit_calls["n"] = 0
preview(new_cmd(0.25))
check(fit_calls["n"] == 2, f"a new command has no memo from another command ({fit_calls['n']} fits)")

bounded = new_cmd()
for k in range(6):
    bounded.opt["Distance"] = 0.1 * (k + 1)
    preview(bounded)
memo_limit = getattr(commands.OffsetCmd, "_PREVIEW_MEMO_LIMIT", None)
memo_size = len(getattr(bounded, "_preview_memo", {}))
check(memo_limit is not None and memo_size <= memo_limit,
      f"the memo is bounded ({memo_size} entries, limit {memo_limit})")

independent = new_cmd()
if not hasattr(independent, "_preview_offset_pair"):
    check(False, "a returned curve is independent: the memo method exists")
else:
    pair = independent._preview_offset_pair(SOURCE, 0.25)
    original_cv = (pair[0].Points[0].X, pair[0].Points[0].Y)
    pair[0].Points[0] = r3.Point4d(999.0, 999.0, 999.0, 1.0)
    again = independent._preview_offset_pair(SOURCE, 0.25)
    check(abs(again[0].Points[0].X - original_cv[0]) <= 1e-12
          and abs(again[0].Points[0].Y - original_cv[1]) <= 1e-12,
          "a returned curve is independent: mutating it does not poison the memo")

off._offset_free_form_approx = _real_free_form
print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILED:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
