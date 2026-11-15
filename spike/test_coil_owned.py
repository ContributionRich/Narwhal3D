"""Required coil adapter: frozen original bits/errors, no legacy engine or private paths.

Build pure_core and set NARWHAL_PURE_CORE to the resulting library, or use the
packaged library. Frozen Linux CPython 3.13 answers also run directly in Rust.
Geometric approximation/continuity checks live separately in test_native_coil.
"""
import importlib.util
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name):
    spec = importlib.util.spec_from_file_location(
        "narwhal3d." + name, ROOT / "narwhal3d" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["narwhal3d." + name] = mod
    setattr(pkg, name, mod)
    spec.loader.exec_module(mod)
    return mod


native = load("native_coil")
coil = load("coil")


def doubles(text):
    return tuple(struct.unpack(">d", bytes.fromhex(v))[0] for v in text.split(","))


def frozen_cases():
    source = ROOT / "native/pure_core/tests/fixtures/coil_numeric.txt"
    originals = [line for line in source.read_text().splitlines()
                 if line and not line.startswith("#")]
    overrides = {}
    if sys.platform == "win32":
        windows = source.with_name("coil_windows_msvc.txt")
        for row in windows.read_text().splitlines():
            if row and not row.startswith("#"):
                key = row.split("|", 1)[0]
                assert key not in overrides, key
                overrides[key] = row
        assert len(overrides) == 19
        assert set(overrides) <= {row.split("|", 1)[0] for row in originals}
    for original in originals:
        key = original.split("|", 1)[0]
        line = overrides.get(key, original)
        if line.startswith("#"):
            continue
        f = line.split("|")
        v = doubles(f[1])
        args = (v[:3], v[3:6], v[6:9], *v[9:12], bool(v[12]), v[13], bool(v[14]))
        answer = ((doubles(f[3]), doubles(f[4]), int(f[5]), doubles(f[6])[0])
                  if f[2] == "ok" else f[3])
        yield f[0], args, answer


def packed(plan):
    return (b"".join(struct.pack(">d", v) for v in plan[0]),
            b"".join(struct.pack(">d", v) for v in plan[1]),
            plan[2], struct.pack(">d", plan[3]))


def reference(name):
    return next(answer for key, _, answer in frozen_cases() if key == name)


def raises(kind, message, fn):
    try:
        fn()
    except kind as exc:
        assert message in str(exc), (message, str(exc))
    else:
        raise AssertionError("expected " + kind.__name__ + ": " + message)


def main():
    count = 0
    for name, args, expected in frozen_cases():
        for force in (False, True):
            if isinstance(expected, str):
                raises(ValueError, expected, lambda: coil.plan(*args, force_native=force))
            else:
                actual = coil.plan(*args, force_native=force)
                assert packed(actual) == packed(expected), name
                curve = coil.curve_from_plan(actual)
                assert curve.IsValid and curve.Degree == 3 and not curve.IsPolyline(), name
            count += 1
    assert not hasattr(coil, "python_plan") and not hasattr(coil, "_native_plan")
    assert not hasattr(coil, "_NATIVE_MIN_SPANS")
    assert coil.MAX_TURNS == 1000.0 and coil.MAX_SPANS_PER_TURN == 96
    args = next(args for name, args, _ in frozen_cases() if name == "helix")
    for point_index in range(3):
        for bad in ((), (0., 1.), (0., 1., 2., 3.)):
            malformed = list(args)
            malformed[point_index] = bad
            raises(ValueError, "coil parameters must be finite", lambda: coil.plan(*malformed))
    # A missing library and an incompatible ABI/symbol are infrastructure errors,
    # including on repeat calls through the cached binding. Never parameter errors.
    real_fn = native._coil_binding()
    with tempfile.TemporaryDirectory() as scratch:
        missing = str(Path(scratch) / "missing-native-library")
        with patch.dict(os.environ, {"NARWHAL_PURE_CORE": missing}):
            native._reset_for_tests()
            for _ in range(2):
                raises(RuntimeError, "required coil native library", lambda: coil.plan(*args))
        present = Path(scratch) / "stale-library"
        present.touch()
        class Version:
            def __init__(self, value):
                self.value = value

            def __call__(self):
                return self.value

        version = types.SimpleNamespace(narwhal_pure_core_abi_version=Version(99))
        for stale in (version, types.SimpleNamespace()):
            with patch.dict(os.environ, {"NARWHAL_PURE_CORE": str(present)}), patch.object(native.ctypes, "CDLL", return_value=stale):
                native._reset_for_tests()
                raises(RuntimeError, "required coil native library", lambda: coil.plan(*args))
        # Matching ABI but retired/missing coil symbol must also fail explicitly.
        version.narwhal_pure_core_abi_version = Version(1)
        with patch.dict(os.environ, {"NARWHAL_PURE_CORE": str(present)}), patch.object(native.ctypes, "CDLL", return_value=version):
            native._reset_for_tests()
            raises(RuntimeError, "required coil native library", lambda: coil.plan(*args))
        good = types.SimpleNamespace(narwhal_pure_core_abi_version=Version(1),
                                     narwhal_pure_core_coil_plan=real_fn)

        def loading_library(path):
            # Do not publish an in-progress load as a cached failure: another
            # thread must wait on the lock, not see a false missing-library error.
            assert native._signature != str(present)
            return good

        with patch.dict(os.environ, {"NARWHAL_PURE_CORE": str(present)}), patch.object(native.ctypes, "CDLL", side_effect=loading_library):
            native._reset_for_tests()
            assert packed(coil.plan(*args)) == packed(reference("helix"))
    native._reset_for_tests()
    assert packed(coil.plan(*args)) == packed(reference("helix"))
    for code, kind, message in ((-2, ValueError, "axis and radial direction must be non-zero"),
                                (-4, ValueError, "requested coil tolerance was not achieved"),
                                (-3, RuntimeError, "invalid plan"),
                                (0, RuntimeError, "invalid plan")):
        with patch.object(native, "_coil_binding", return_value=lambda *args: code):
            raises(kind, message, lambda: coil.plan(*args))
    # Successful ABI status is not proof of a usable result. Exercise the
    # caller-owned count/capacity contract and knot/deviation validation too.
    expected = reference("helix")
    def fake_plan(damage):
        def invoke(*call):
            poles, pole_capacity, knots, knot_capacity = call[9:13]
            for i, value in enumerate(expected[0]):
                poles[i] = value
            for i, value in enumerate(expected[1]):
                knots[i] = value
            for output, value in zip(call[13:],
                                     (len(expected[0]), len(expected[1]), expected[2], expected[3])):
                output._obj.value = value
            if damage == "pole_capacity":
                call[13]._obj.value = pole_capacity + 1
            elif damage == "knot_capacity":
                call[14]._obj.value = knot_capacity + 1
            elif damage == "spans":
                call[15]._obj.value += 1
            elif damage == "deviation":
                call[16]._obj.value = float("nan")
            elif damage == "tolerance":
                call[16]._obj.value = call[7] * 2
            elif damage == "nonfinite_pole":
                poles[0] = float("inf")
            elif damage == "knot_order":
                knots[4] = -1.0
            elif damage == "knot_start":
                knots[0] = -0.1
            elif damage == "knot_end":
                knots[len(expected[1]) - 1] = 1.1
            return 0
        return invoke

    for damage in ("pole_capacity", "knot_capacity", "spans", "deviation", "tolerance",
                   "nonfinite_pole", "knot_order", "knot_start", "knot_end"):
        with patch.object(native, "_coil_binding", return_value=fake_plan(damage)):
            raises(RuntimeError, "invalid plan", lambda: coil.plan(*args))
    print("PASS: %d frozen adapter outcomes (exact bits/errors), host curves, malformed triples, required-library/cache/ABI/symbol/output errors" % count)


if __name__ == "__main__":
    main()
