"""Required catenary v2: frozen original bits, host boundary, safety and timing.

No archived-source imports or Python planner. NARWHAL_PURE_CORE selects a build;
otherwise the packaged library is used. Independent Linux and Windows/MSVC captures retain exact platform answers.
"""
import argparse
import json
import math
import os
import platform
import sys
from pathlib import Path
import tempfile
import time
import types
from unittest.mock import patch

from test_coil_owned import ROOT, coil, native, load, doubles, packed, raises

catenary = load("catenary")
FIXTURES = ROOT / "native/pure_core/tests/fixtures"
WINDOWS_MSVC = (sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64")
                and "MSC" in platform.python_compiler())


def platform_rows(kind):
    linux = [r for r in (FIXTURES / ("catenary_%s_linux.txt" % kind)).read_text().splitlines()
             if not r.startswith("#")]
    windows = [r for r in (FIXTURES / ("catenary_%s_windows_msvc.txt" % kind)).read_text().splitlines()
               if not r.startswith("#")]
    assert len(linux) == len(windows) == (42 if kind == "numeric" else 34)
    assert len({r.split("|")[0] for r in linux}) == len(linux)
    fields = 3 if kind == "numeric" else 2
    assert all(l.split("|")[:fields] == w.split("|")[:fields] for l, w in zip(linux, windows))
    assert sum(l != w for l, w in zip(linux, windows)) == (25 if kind == "numeric" else 22)
    return windows if WINDOWS_MSVC else linux



def frozen_cases():
    for row in platform_rows("numeric"):
        if row.startswith("#"):
            continue
        f = row.split("|")
        v = doubles(f[1])
        args = (v[:3], v[3:6], v[6:9], v[9:12], v[12])
        result = ((doubles(f[3]), doubles(f[4]), int(f[5]), doubles(f[6])[0])
                  if f[2] == "ok" else f[3])
        yield f[0], args, result


def reference(name):
    return next(answer for key, _, answer in frozen_cases() if key == name)


def best_seconds(fn):
    elapsed = []
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(20):
            fn()
        elapsed.append((time.perf_counter() - start) / 20)
    return min(elapsed)


def benchmark(path):
    baseline = json.loads(Path(path).read_text())
    args_by_name = {name: args for name, args, _ in frozen_cases()}
    for name, old in baseline["cases"].items():
        args = args_by_name[name]
        assert catenary.plan(*args)[2] == old["spans"]
        seconds = best_seconds(lambda: catenary.make_curve(*args))
        ratio = seconds / old["seconds"]
        print("[perf] catenary spans=%d native=%.9fs original=%.9fs ratio=%.6f" %
              (old["spans"], seconds, old["seconds"], ratio))
        assert ratio <= 0.90, "full-adapter catenary must win by at least 10%"


def host_boundary():
    cases = {name: args for name, args, expected in frozen_cases()
             if not isinstance(expected, str)}
    points_checked = 0
    for row in platform_rows("host"):
        if row.startswith("#"):
            continue
        name, params, points, derivatives = row.split("|")
        curve, _ = catenary.make_curve(*cases[name])
        assert curve.IsValid and curve.Degree == 3 and not curve.IsPolyline(), name
        xyz, dxyz = [], []
        for u in doubles(params):
            t = curve.Domain.T0 + (curve.Domain.T1 - curve.Domain.T0) * u
            point = curve.PointAt(t)
            derivative = curve.DerivativeAt(t, 1)[1]
            xyz.extend((point.X, point.Y, point.Z))
            dxyz.extend((derivative.X, derivative.Y, derivative.Z))
            points_checked += 1
        # Equality here is of captured host evaluations, not analytic exactness.
        assert packed((xyz, dxyz, 0, 0.)) == packed((doubles(points), doubles(derivatives), 0, 0.)), name
    assert points_checked == 34 * 17
    return points_checked


def geometric_check():
    # Independent closed-form locus with known a; no numerical reference solver.
    a, half, tolerance = 3.0, 5.0, 0.0001
    sag = a * (math.cosh(half / a) - 1.0)
    args = ((-half, 0., 0.), (half, 0., 0.), (0., -1., 0.), (0., -sag, 0.), tolerance)
    curve, deviation = catenary.make_curve(*args)
    maximum = 0.0
    for i in range(2001):
        u = i / 2000
        x = -half + 2 * half * u
        y = a * (math.cosh(x / a) - math.cosh(half / a))
        actual = curve.PointAt(curve.Domain.T0 + (curve.Domain.T1 - curve.Domain.T0) * u)
        maximum = max(maximum, actual.DistanceTo(coil.r3.Point3d(x, y, 0.)))
    assert maximum <= tolerance and maximum <= deviation * 1.02
    plan = catenary.plan(*args)
    for knot in sorted(set(plan[1]))[1:-1]:
        left = curve.DerivativeAt(knot - 1e-8, 1)[1]
        right = curve.DerivativeAt(knot + 1e-8, 1)[1]
        ll = math.sqrt(left.X ** 2 + left.Y ** 2 + left.Z ** 2)
        rl = math.sqrt(right.X ** 2 + right.Y ** 2 + right.Z ** 2)
        dot = (left.X * right.X + left.Y * right.Y + left.Z * right.Z) / (ll * rl)
        assert dot > 1.0 - 1e-8 and abs(ll - rl) / max(ll, rl) < 1e-6


def errors_and_loading():
    args = next(args for name, args, _ in frozen_cases() if name == "frame_0_0")
    for index in range(13):
        for bad in (float("nan"), float("inf"), -float("inf")):
            values = [v for point in args[:4] for v in point] + [args[4]]
            values[index] = bad
            invalid = (values[:3], values[3:6], values[6:9], values[9:12], values[12])
            raises(ValueError, "catenary parameters must be finite; tolerance must be positive",
                   lambda: catenary.make_curve(*invalid))
    for tol in (0., -0., -1.):
        raises(ValueError, "tolerance must be positive", lambda: catenary.make_curve(*args[:4], tol))
    for index in range(4):
        for point in ((), (0., 1.), (0., 1., 2., 3.)):
            invalid = list(args)
            invalid[index] = point
            raises(ValueError, "three coordinates", lambda: catenary.plan(*invalid))
    overflow = ((1e308, 0., 0.), (1e308, 1., 0.), (1e308, 0., 1.), (0., 0., 0.), .001)
    raises(ValueError, "catenary arithmetic could not resolve a finite curve",
           lambda: catenary.make_curve(*overflow))
    for tolerance in (.001, 1e-30):
        sag = -1e308 if tolerance == .001 else -2.
        unresolved = ((-5., 0., 0.), (5., 0., 0.), (0., -1., 0.), (0., sag, 0.), tolerance)
        raises(ValueError, "requested catenary tolerance was not achieved",
               lambda: catenary.make_curve(*unresolved))
    real_fn = native._catenary_binding_for_library()
    class Version:
        def __init__(self, version):
            self.version = version

        def __call__(self):
            return self.version

    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "missing-library"
        with patch.dict(os.environ, {"NARWHAL_PURE_CORE": str(path)}):
            native._reset_for_tests()
            for _ in range(2):
                raises(RuntimeError, "required catenary native v2", lambda: catenary.plan(*args))
        path.touch()
        libraries = (
            types.SimpleNamespace(),
            types.SimpleNamespace(narwhal_pure_core_abi_version=Version(99)),
            # ABI-1 v1-only library is stale, not an invalid catenary input.
            types.SimpleNamespace(narwhal_pure_core_abi_version=Version(1),
                                  narwhal_pure_core_catenary_plan=real_fn),
        )
        for library in libraries:
            with patch.dict(os.environ, {"NARWHAL_PURE_CORE": str(path)}), patch.object(native.ctypes, "CDLL", return_value=library):
                native._reset_for_tests()
                raises(RuntimeError, "required catenary native v2", lambda: catenary.plan(*args))
        library = types.SimpleNamespace(narwhal_pure_core_abi_version=Version(1),
                                        narwhal_pure_core_catenary_plan_v2=real_fn)
        def load_complete(path_string):
            assert native._catenary_signature != str(path)
            return library
        with patch.dict(os.environ, {"NARWHAL_PURE_CORE": str(path)}), patch.object(native.ctypes, "CDLL", side_effect=load_complete):
            native._reset_for_tests()
            assert packed(catenary.plan(*args)) == packed(reference("frame_0_0"))
    native._reset_for_tests()
    assert packed(catenary.plan(*args)) == packed(reference("frame_0_0"))
    expected = reference("frame_0_0")
    def fake(damage):
        def invoke(*call):
            poles, pc, knots, kc = call[5:9]
            for i, v in enumerate(expected[0]):
                poles[i] = v
            for i, v in enumerate(expected[1]):
                knots[i] = v
            for output, value in zip(call[9:],
                                     (len(expected[0]), len(expected[1]), expected[2], expected[3], 0)):
                output._obj.value = value
            if damage == "pole_capacity": call[9]._obj.value = pc + 1
            elif damage == "knot_capacity": call[10]._obj.value = kc + 1
            elif damage == "span": call[11]._obj.value = 7
            elif damage == "deviation": call[12]._obj.value = float("nan")
            elif damage == "tolerance": call[12]._obj.value = call[4] * 2
            elif damage == "pole": poles[0] = float("inf")
            elif damage == "knots": knots[4] = -1.
            elif damage == "start": knots[0] = -.1
            elif damage == "end": knots[len(expected[1]) - 1] = 1.1
            elif damage == "success_reason": call[13]._obj.value = 2
            elif damage == "unknown_reason":
                call[13]._obj.value = 99
                return -2
            elif damage == "mismatched_reason":
                call[13]._obj.value = 5
                return -2
            elif damage == "capacity_status": return -3
            return 0
        return invoke
    for damage in ("pole_capacity", "knot_capacity", "span", "deviation", "tolerance", "pole",
                   "knots", "start", "end", "success_reason", "unknown_reason", "mismatched_reason", "capacity_status"):
        before = native._native_catenary_calls
        with patch.object(native, "_catenary_binding_for_library", return_value=fake(damage)):
            raises(RuntimeError, "required catenary native planner", lambda: catenary.plan(*args))
        assert native._native_catenary_calls == before


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-baseline", help="same-machine original full-adapter timing JSON")
    options = parser.parse_args()
    outcomes = 0
    for name, args, expected in frozen_cases():
        for force in (False, True):
            if isinstance(expected, str):
                raises(ValueError, expected, lambda: catenary.plan(*args, force_native=force))
            else:
                assert packed(catenary.plan(*args, force_native=force)) == packed(expected), name
            outcomes += 1
    assert not hasattr(catenary, "python_plan") and not hasattr(catenary, "_NATIVE_MIN_SPANS")
    points = host_boundary()
    geometric_check()
    errors_and_loading()
    if options.benchmark_baseline:
        benchmark(options.benchmark_baseline)
    print("PASS: %d frozen outcomes; %d host points + derivatives; independent analytic/C1 checks; finite/malformed/required-native/corrupt-output refusal" % (outcomes, points))


if __name__ == "__main__":
    main()
