#!/usr/bin/env python3
"""Nearest25 full public original answers and required-native host boundaries.

Synthetic SDK witnesses replay frozen input evaluations only; no Python search
or independently computed expected answers live in this test.
"""
import ctypes as C
import importlib.util
import json
import math
import os
from pathlib import Path
import struct
import sys
import types
import unittest
from unittest.mock import patch
import rhino3dm as r3
ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
from narwhal3d import nearest, native_nearest as native


def f(word): return struct.unpack(">d", bytes.fromhex(word))[0]
def bits(v): return struct.pack(">d", v).hex()
def fields(r):
    return [bits(r.t), *map(bits, r.point), bits(r.distance), r.method, r.at_end, r.ambiguous]


def fixtures():
    # Windows originals must be independently captured before Windows activation.
    filename = ("nearest25_host_windows_msvc.jsonl" if sys.platform == "win32" else
                "nearest25_host_linux.jsonl")
    file = ROOT / "native/pure_core/tests/fixtures" / filename
    return [json.loads(line) for line in file.read_text().splitlines()]


class FrozenSDK:
    """Original SDK evaluation data, not a curve conversion or numeric oracle."""
    def __init__(self, row):
        self.row, self.index = row, 0
        self.Domain = r3.Interval(*map(f, row["domain"]))
        self.Knots = None if row["knots"] is None else list(map(f, row["knots"]))
        self.SpanCount, self.IsClosed = row["span_count"], row["closed"]
    def IsLinear(self): return False
    def Duplicate(self): return self
    def event(self, order, t):
        e = self.row["events"][self.index]
        self.index += 1
        if e[:2] != [order, bits(t)]:
            raise AssertionError((self.row["name"], self.index, e[:2], order, bits(t)))
        return e
    def PointAt(self, t):
        e = self.event(0, t)
        if e[2] != 0:
            raise LookupError("owned SDK point failure")
        return r3.Point3d(*map(f, e[3]))
    def DerivativeAt(self, t, order):
        e = self.event(2, t)
        if e[2] == 1:
            if self.row["name"].endswith("raise"):
                raise LookupError("owned SDK derivative failure")
            return None if self.row["name"].endswith("none") else []
        if e[2] == -4:
            return [r3.Point3d(0, 0, 0), object(), r3.Vector3d(0, 0, 0)]
        v = list(map(f, e[3]))
        return [r3.Point3d(*v[:3]), r3.Vector3d(*v[3:6]), r3.Vector3d(*v[6:9])]


class RequiredNearest(unittest.TestCase):
    def test_independent_full_public_original_answers(self):
        count = errors = 0
        for row in fixtures():
            curve = r3.CommonObject.Decode(row["sdk_json"]) if row["sdk_json"] else FrozenSDK(row)
            with self.subTest(name=row["name"], query=count):
                try:
                    result = nearest.closest_point_on_curve(curve, tuple(map(f, row["target"])), .01)
                except Exception as exc:
                    self.assertEqual([type(exc).__name__, str(exc)], row.get("error"))
                    errors += 1
                else:
                    self.assertEqual(fields(result), row["result"])
                if isinstance(curve, FrozenSDK):
                    self.assertEqual(curve.index, len(row["events"]))
            count += 1
        self.assertEqual((count, errors), (234, 26))

    def test_original_sdk_callback_exception_identity(self):
        failure = LookupError("SDK identity must survive C")
        class Curve:
            Domain = r3.Interval(0, 1)
            Knots = None
            SpanCount = 1
            IsClosed = False
            def PointAt(self, t): raise failure
        with self.assertRaises(LookupError) as caught:
            native.search(native.pack(Curve()), (0, 0, 0))
        self.assertIs(caught.exception, failure)

    def test_missing_derivatives_stop_newton_without_replay(self):
        for kind in ("raise", "none", "short"):
            class Curve:
                Domain = r3.Interval(0, 1)
                Knots = None
                SpanCount = 1
                IsClosed = False
                calls = 0
                def PointAt(self, t): return r3.Point3d(t, 0, 0)
                def DerivativeAt(self, t, order):
                    self.calls += 1
                    if kind == "raise": raise RuntimeError("SDK has no derivative")
                    return None if kind == "none" else []
            c = Curve()
            answer = native.search(native.pack(c), (.5, 2., 0.))
            self.assertEqual(answer, (.5, (.5, 0., 0.), 2., False))
            self.assertGreater(c.calls, 0)

    def test_derivative_failure_after_progress_keeps_current_parameter(self):
        base = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0))
        class Curve:
            Domain = base.Domain
            Knots = None
            SpanCount = 1
            IsClosed = False
            calls = 0
            def PointAt(self, t): return base.PointAt(t)
            def DerivativeAt(self, t, order):
                self.calls += 1
                if self.calls > 1: raise LookupError("SDK derivative stopped after progress")
                return base.DerivativeAt(t, order)
        curve = Curve()
        self.assertEqual(native.search(native.pack(curve), (.2, 2., 0.)),
                         (.2, (.2, 0., 0.), 2., False))
        self.assertEqual(curve.calls, 2)

    def test_missing_stale_and_wrong_abi_explicit(self):
        with patch.dict(os.environ, NARWHAL_PURE_CORE=str(ROOT / "nearest25-absent")):
            with self.assertRaises(native.NativeNearestError):
                native.pack(r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0)))
        for abi in (1, 999):
            version = lambda: abi
            fake = types.SimpleNamespace(narwhal_pure_core_abi_version=version)
            with patch.object(native, "_curve_cache", None), patch.object(native.ctypes, "CDLL", return_value=fake):
                with self.assertRaises(native.NativeNearestError): native._required_bindings()

    def test_protocol_is_not_sdk_or_input_refusal(self):
        curve = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0)).ToNurbsCurve()
        packed = native.pack(curve)
        with patch.object(native, "_nearest_binding", return_value=lambda *args: -1):
            with self.assertRaises(native.NativeNearestError): native.search(packed, (.5, 0, 0))
        with patch.object(native, "search", return_value=None):
            with self.assertRaises(native.NativeNearestError): nearest._native_search(packed, (.5, 0, 0))

    def test_nonfinite_scalar_batch_and_callback_refuse(self):
        line = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0))
        for curve in (line, line.ToNurbsCurve()):
            packed = native.pack(curve)
            for target in ((math.nan, 0, 0), (0, math.inf, 0), (1e308, 0, 0)):
                with self.assertRaises(native.NearestInputError): native.search(packed, target)
                if isinstance(packed, native.CallbackCurve) or sys.platform in native.BATCH_QUALIFIED_PLATFORMS:
                    with self.assertRaises(native.NearestInputError): native.search_many(packed, [target])
                else:
                    self.assertIsNone(native.search_many(packed, [target]))
                    with self.assertRaises(native.NearestInputError):
                        nearest.PreparedCurve(curve).closest_many([target])

    def test_unqualified_batch_distance_overflow_is_a_typed_refusal(self):
        curve = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0)).ToNurbsCurve()
        before = curve.Encode()
        with patch.object(native, "BATCH_QUALIFIED_PLATFORMS", frozenset()):
            with self.assertRaises(native.NearestInputError):
                nearest.PreparedCurve(curve).closest_many([(1e308, 0., 0.)])
        self.assertEqual(curve.Encode(), before)

    def test_invalid_sdk_derivatives_refuse_without_search_replay(self):
        curve = r3.NurbsCurve(3, False, 4, 8)
        for i, point in enumerate(((0,0,0),(1,3,0),(2,-2,1),(4,2,0),
                                   (5,-1,2),(7,3,1),(8,0,0),(9,2,-1))):
            curve.Points[i] = r3.Point4d(*point, 1.)
        for i, knot in enumerate((0,0,0,0,.4,1.1,3,3,3,3)):
            curve.Knots[i] = knot
        self.assertFalse(curve.IsValid)
        derivatives = curve.DerivativeAt(3., 2)
        self.assertTrue(any(not math.isfinite(value) for point in derivatives
                            for value in (point.X, point.Y, point.Z)))
        before = curve.Encode()
        with self.assertRaises(native.NearestInputError):
            native.search(native.CallbackCurve(curve),
                          (2.597081784348423, -.2094045502309657, -.2480143898458369))
        self.assertEqual(curve.Encode(), before)

    def test_resource_bounds_before_unbounded_consumption(self):
        class Curve:
            Domain = r3.Interval(0, 1)
            Knots = None
            SpanCount = 20001
            IsClosed = False
        with self.assertRaises(native.NearestInputError): native.pack(Curve())
        curve = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0)).ToNurbsCurve()
        prepared = nearest.PreparedCurve(curve)
        with self.assertRaises(native.NearestInputError):
            prepared.closest_many(((0, 0, 0) for _ in range(100001)))
        callback = native.pack(r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0)))
        with patch.object(native, "search", side_effect=AssertionError("no SDK evaluation before size validation")):
            with self.assertRaises(native.NearestInputError):
                native.search_many(callback, ((0, 0, 0) for _ in range(100001)))
        with self.assertRaises(native.NearestInputError):
            native.PackedCurve(4, [0] * 6, [0] * 13, False, False)

    def test_prepared_non_nurbs_snapshot_is_owned(self):
        curve = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0))
        prepared = nearest.PreparedCurve(curve)
        before = fields(prepared.closest((.5, 2, 0)))
        curve.Domain = r3.Interval(20, 21)
        self.assertEqual(fields(prepared.closest((.5, 2, 0))), before)
        self.assertNotEqual(fields(nearest.closest_point_on_curve(curve, (.5, 2, 0))), before)

    def test_batch_platform_restriction_preserved(self):
        self.assertEqual(native.BATCH_QUALIFIED_PLATFORMS, frozenset({"linux"}))
        with patch.object(native.sys, "platform", "win32"):
            self.assertIsNone(native._search_many_binding())
            self.assertTrue(native.batch_capability().startswith("not qualified"))

    def test_surfaces_not_changed_and_no_curve_search_engine(self):
        self.assertFalse(hasattr(nearest, "_newton"))
        self.assertFalse(hasattr(nearest, "_seed_params"))
        self.assertTrue(callable(nearest.closest_point_on_surface))


if __name__ == "__main__": unittest.main()
