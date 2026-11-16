#!/usr/bin/env python3
"""Required Curve preview: independent captured bits, public routing and refusal.

No live Python sampler/oracle. Performance is measured separately against the
locally archived original, not enforced by noisy wall-clock unit assertions.
"""
import importlib.util
import os
from pathlib import Path
import struct
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
from narwhal3d import native_bspline as native, preview, cplane
from narwhal3d.native_preview import NativePreviewError, PreviewInputError


def fixtures():
    for line in (ROOT / "native/pure_core/tests/data/preview24_linux.tsv").read_text().splitlines():
        op, count, flags, data, lengths, bits = line.split("\t")
        if int(op) == 10:
            vals = [struct.unpack(">d", bytes.fromhex(v))[0] for v in data.split(",")]
            yield int(count), int(flags), list(zip(vals[::3], vals[1::3], vals[2::3])), bits.split(",")


def bits(points):
    return [struct.pack(">d", v).hex() for p in points for v in p]


class ControlPreview(unittest.TestCase):
    def test_independent_original_bits(self):
        count = 0
        for degree, span, pts, expected in fixtures():
            self.assertEqual(bits(preview.control_point_curve_polyline(pts, degree, span)), expected)
            count += 1
        self.assertEqual(count, 160)

    def test_public_curve_routes_native(self):
        pts = [(0, 0, 0), (4, 6, 0), (9, -2, 1)]
        cursor = (12, 3, 0)
        before = native._native_calls
        got = preview.preview_curve(pts, cursor, cplane.WORLD_TOP,
                                    cmd=types.SimpleNamespace(opt={"Degree": "3"}))
        self.assertEqual(native._native_calls, before + 1)
        self.assertEqual(got, [native.control_point_curve_polyline(pts + [cursor], 3, 12)])

    def test_missing_native_explicit_no_replay(self):
        with patch.dict(os.environ, NARWHAL_PURE_CORE=str(ROOT / "absent-preview24.so")):
            self.assertFalse(native.available())
            with self.assertRaises(NativePreviewError):
                preview.control_point_curve_polyline([(0, 0, 0), (1, 2, 3), (3, 4, 5)])

    def test_polygon_degree_is_native_owned(self):
        before = native._native_calls
        pts = [(0., -0., 0.), (1., 2., 3.)]
        self.assertEqual(bits(preview.control_point_curve_polyline(pts, -10)), bits(pts))
        self.assertEqual(native._native_calls, before + 1)

    def test_empty_single(self):
        self.assertEqual(preview.control_point_curve_polyline([]), [])
        self.assertEqual(preview.control_point_curve_polyline([(0, 0, 0)]), [])

    def test_malformed_and_nonfinite_refused(self):
        for pts in [[(0, 0), (1, 2, 3)], [(0, 0, 0), (float("nan"), 1, 2)]]:
            with self.assertRaises(PreviewInputError):
                preview.control_point_curve_polyline(pts)

    def test_span_output_and_work_bounds(self):
        with self.assertRaises(PreviewInputError):
            native.control_point_curve_polyline([(0, 0, 0)] * 4, 3, 1025)
        with self.assertRaises(PreviewInputError):
            native.control_point_curve_polyline([(0, 0, 0)] * 1000, 999, 1024)
        with self.assertRaises(PreviewInputError):
            native.control_point_curve_polyline(((0, 0, 0) for _ in range(100001)), 1, 0)

    def test_native_protocol_failure_not_input_error(self):
        with patch.object(native, "_preview_binding", return_value=lambda *args: 0):
            with self.assertRaises(NativePreviewError):
                native.control_point_curve_polyline([(0, 0, 0), (1, 2, 3)])


if __name__ == "__main__":
    unittest.main()
