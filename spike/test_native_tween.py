"""Required Tween native boundary/SDK tests; computational goldens live in Rust."""
import ctypes
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import rhino3dm as r3

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
from narwhal3d import tween, native_tween as native


def bits(x):
    return struct.pack(">d", float(x)).hex()


def snapshot(c):
    return {
        "degree": c.Degree, "rational": c.IsRational, "closed": c.IsClosed, "valid": c.IsValid,
        "points": [bits(v) for p in c.Points for v in (p.X, p.Y, p.Z, p.W)],
        "knots": [bits(x) for x in c.Knots.ToList()],
        "domain": [bits(c.Domain.T0), bits(c.Domain.T1)],
        "evaluations": [[bits(v) for v in (p.X, p.Y, p.Z)]
                        for p in [c.PointAt(t) for t in (0., .125, .5, .875, 1.)]],
    }


def curve(y=0):
    return r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(i, y, i % 2) for i in range(4)], 3)


class TweenHost(unittest.TestCase):
    def test_admission(self):
        self.assertIs(native.require_support(), True)

    def test_full_sdk_original_captures(self):
        data = json.loads((ROOT / "native/pure_core/tests/fixtures/tween_host23.json").read_text())
        self.assertEqual(len(data["cases"]), 19)
        for case in data["cases"]:
            with self.subTest(case=case["name"]):
                a, b = r3.CommonObject.Decode(case["a"]), r3.CommonObject.Decode(case["b"])
                self.assertTrue(a.IsValid and b.IsValid)
                before = native._native_calls
                if case["samples"]:
                    out = tween.sample_point_tweens(a, b, case["number"], case["samples"], case["flip"])
                else:
                    out = tween.compatible_tweens(a, b, case["number"], case["flip"])
                self.assertGreater(native._native_calls, before)
                self.assertEqual([snapshot(c) for c in out], case["expected"])

    def test_small_production_requires_native(self):
        before = native._native_calls
        self.assertEqual(len(tween.compatible_tweens(curve(), curve(10))), 1)
        self.assertEqual(native._native_calls, before + 1)

    def test_original_count_errors(self):
        with self.assertRaisesRegex(ValueError, "Number must be at least 1"):
            tween.compatible_tweens(curve(), curve(1), 0)
        for number, samples in [(0, 10), (1, 1), (1, 10000)]:
            with self.assertRaisesRegex(ValueError, "Number must be >=1 and SampleNumber must be 2..9999"):
                tween.sample_point_tweens(curve(), curve(1), number, samples)

    def test_captured_error_contracts(self):
        data = json.loads((ROOT / "native/pure_core/tests/fixtures/tween_host23.json").read_text())
        a, b = curve(), curve(1)
        mismatch = r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(i, 0, 0) for i in range(5)], 3)
        zero = r3.Circle(2).ToNurbsCurve()
        zero.Points[0] = r3.Point4d(0, 0, 0, 0)
        ka = r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(i, i % 3, 0) for i in range(6)], 3)
        kb = ka.Duplicate()
        kb.Knots[3] = kb.Knots[3] + .1
        operations = {
            "number": lambda: tween.compatible_tweens(a, b, 0),
            "structure": lambda: tween.compatible_tweens(a, mismatch),
            "sample-number": lambda: tween.sample_point_tweens(a, b, 0, 4),
            "sample-range": lambda: tween.sample_point_tweens(a, b, 1, 1),
            "zero-weight": lambda: tween.compatible_tweens(zero, r3.Circle(2).ToNurbsCurve()),
            "knots": lambda: tween.compatible_tweens(ka, kb),
        }
        self.assertEqual(len(data["errors"]), 6)
        for expected in data["errors"]:
            with self.subTest(case=expected["name"]):
                with self.assertRaises(ValueError) as caught:
                    operations[expected["name"]]()
                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(str(caught.exception), expected["message"])

    def test_structure(self):
        with self.assertRaisesRegex(ValueError, "requires equal degree and control-point structure"):
            tween.compatible_tweens(curve(), r3.Circle(2).ToNurbsCurve())

    def test_zero_weight(self):
        a = r3.Circle(2).ToNurbsCurve()
        a.Points[0] = r3.Point4d(0, 0, 0, 0)
        with self.assertRaisesRegex(ValueError, "a control point has zero weight"):
            tween.compatible_tweens(a, r3.Circle(2).ToNurbsCurve())

    def test_geometric_native_refusals(self):
        for first, second in [([(0, 0, 0, 1)], [(0, 0, 0, -1)]),
                              ([(float("inf"), 0, 0, 1)], [(0, 0, 0, 1)]),
                              ([(1.7976931348623157e308, 0, 0, 1)], [(-1.7976931348623157e308, 0, 0, 1)])]:
            with self.assertRaises(ValueError):
                native.interpolate(first, second, 1)

    def test_shape_and_limits(self):
        for first, second, n in [([], [], 1), ([(0, 0, 1)], [(0, 0, 1)], 1),
                                  ([(0, 0, 0, 1)], [], 1),
                                  ([(0, 0, 0, 1)], [(0, 0, 0, 1)], 12500001)]:
            with self.assertRaises(ValueError):
                native.interpolate(first, second, n)

    def test_knot_refusal(self):
        with self.assertRaisesRegex(ValueError, "requires matching knot vectors"):
            native.matching_knots([0., .5, 1.], [0., .6, 1.])

    def test_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, NARWHAL_PURE_CORE=str(Path(tmp) / "missing")):
                with self.assertRaises(RuntimeError):
                    tween.compatible_tweens(curve(), curve(1))

    def test_stale(self):
        with patch.object(native, "_cache", None), patch.object(native.C, "CDLL", return_value=object()):
            with self.assertRaises(RuntimeError):
                native.require_support()

    def test_bad_abi(self):
        class Function:
            def __call__(self):
                return 999
        class Library:
            narwhal_pure_core_abi_version = Function()
        with patch.object(native, "_cache", None), patch.object(native.C, "CDLL", return_value=Library()):
            with self.assertRaises(RuntimeError):
                native.require_support()

    def test_protocol(self):
        for rc, used in [(0, 0), (0, 5), (-1, 0xffffffffffffffff),
                         (-3, 0xffffffffffffffff), (123, 0xffffffffffffffff), (-11, 4)]:
            def bad(*args):
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_uint64))[0] = used
                return rc
            with self.subTest(rc=rc, used=used), patch.object(native, "_binding", return_value={"plan": bad}):
                with self.assertRaises(RuntimeError):
                    native.interpolate([(0, 0, 0, 1)], [(1, 1, 1, 1)], 1)

    def test_knot_call_protocol(self):
        def bad(*args):
            raise ctypes.ArgumentError("invalid knot ABI")
        with patch.object(native, "_binding", return_value={"knots": bad}):
            with self.assertRaises(RuntimeError):
                native.matching_knots([0., 1.], [0., 1.])

    def test_nonfinite_success_protocol(self):
        def bad(*args):
            args[-3][0] = float("nan")
            ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_uint64))[0] = 4
            return 0
        with patch.object(native, "_binding", return_value={"plan": bad}):
            with self.assertRaises(RuntimeError):
                native.interpolate([(0, 0, 0, 1)], [(1, 1, 1, 1)], 1)

    def test_no_curve_construction_after_failure(self):
        a, b = curve(), curve(1)
        with patch.object(native, "interpolate", side_effect=RuntimeError("native unavailable")):
            with patch.object(tween.r3, "NurbsCurve", side_effect=AssertionError("must not construct")):
                with self.assertRaisesRegex(RuntimeError, "native unavailable"):
                    tween.compatible_tweens(a, b)


if __name__ == "__main__":
    unittest.main()
