#!/usr/bin/env python3
"""Preview24 host/caller boundaries, independent original-bit fixtures."""
import ctypes as C
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
from narwhal3d import native_preview as native, preview, cplane


def fixture_path():
    # Independently captured original answers; no tolerance or cross-platform replay.
    filename = ("preview24_windows_msvc.tsv" if sys.platform == "win32" else
                "preview24_linux.tsv")
    return ROOT / "native/pure_core/tests/data" / filename


def fixtures():
    for line in fixture_path().read_text().splitlines():
        op, n, flags, data, lengths, bits = line.split("\t")
        if int(op) != 10:
            yield (int(op), int(n), int(flags),
                   [struct.unpack(">d", bytes.fromhex(v))[0] for v in data.split(",")],
                   list(map(int, lengths.split(","))) if lengths else [],
                   bits.split(",") if bits else [])


def encoded(rows):
    return [struct.pack(">d", v).hex() for row in rows for p in row for v in p]


class PreviewBoundaries(unittest.TestCase):
    def test_fixture_platform_selects_independent_originals(self):
        for platform, filename in (("win32", "preview24_windows_msvc.tsv"),
                                   ("linux", "preview24_linux.tsv"),
                                   ("darwin", "preview24_linux.tsv")):
            with self.subTest(platform=platform), patch.object(sys, "platform", platform):
                self.assertEqual(fixture_path(), ROOT / "native/pure_core/tests/data" / filename)
                self.assertTrue(fixture_path().is_file())

    def test_all_independent_primitive_bits_through_ctypes(self):
        count = 0
        for op, n, flags, data, lengths, bits in fixtures():
            rows = native._sample(op, data, n, flags)
            self.assertEqual(list(map(len, rows)), lengths)
            self.assertEqual(encoded(rows), bits)
            count += 1
        self.assertEqual(count, 818)

    def test_public_wrappers_match_frozen_bits(self):
        for op, n, flags, d, lengths, bits in fixtures():
            if op in (1, 2, 3):
                pl = types.SimpleNamespace(xaxis=d[3:6], yaxis=d[6:9])
                if op == 1:
                    rows = [preview.circle_polyline(pl, d[:3], d[9], n)]
                elif op == 2:
                    rows = [preview._ring(d[:3], d[3:6], d[6:9], d[9], n)]
                else:
                    rows = [preview.polygon_polyline(pl, d[:3], d[9], n, bool(flags), start_angle=d[10], direction=d[11])]
            elif op == 4:
                points = [d[:3], d[3:6], d[6:9]][:flags]
                rows = preview.preview_ellipse(points, d[9:12], cplane.WORLD_TOP)
            elif op == 5:
                args = [d[:3], d[3:6], d[12]][:flags]
                rows = preview.preview_ellipsoid(args, d[6:9], types.SimpleNamespace(zaxis=d[9:12]))
            else:
                continue
            self.assertEqual(encoded(rows), bits)

    def test_custom_rectangle_callbacks_order_and_depth(self):
        class Plane:
            def __init__(self): self.calls = []
            def from_world(self, p):
                self.calls.append(("from", p))
                return (1, 2, 3) if p == "corner" else (4, 5, 6)
            def to_world(self, *p):
                self.calls.append(("to", p))
                return ("host", p)
        p = Plane()
        got = preview.rectangle_polyline(p, "corner", "opposite")
        wanted = [(1, 2, 6), (4, 2, 6), (4, 5, 6), (1, 5, 6), (1, 2, 6)]
        self.assertEqual(got, [("host", v) for v in wanted])
        self.assertEqual(p.calls, [("from", "corner"), ("from", "opposite")] + [("to", v) for v in wanted])

    def test_construction_line_pick_types_are_preserved(self):
        center, axis, cursor = [0, 0, 0], [3, 0, 0], [4, 0, 0]
        one = preview.preview_ellipse([center], cursor, cplane.WORLD_TOP)
        self.assertIs(one[0][1], cursor)
        two = preview.preview_ellipse([center, axis], cursor, cplane.WORLD_TOP)
        self.assertIs(two[1][1], cursor)
        solid = preview.preview_ellipsoid([center], cursor, None)
        self.assertIs(solid[0][0], center)
        self.assertIsInstance(solid[0][1], tuple)
        degenerate = preview.preview_ellipsoid([center, axis, 0], cursor, cplane.WORLD_TOP)
        self.assertIs(degenerate[0][0], center)
        self.assertIs(degenerate[0][1], axis)

    def test_no_document_geometry_dependency(self):
        before = native._native_calls
        preview.circle_polyline(cplane.WORLD_FRONT, (1, 2, 3), 2)
        self.assertEqual(native._native_calls, before + 1)
        self.assertNotIn("narwhal3d.bridge", sys.modules)

    def test_missing_and_stale_library_explicit(self):
        with patch.dict(os.environ, NARWHAL_PURE_CORE=str(ROOT / "no-preview24-library")):
            with self.assertRaises(native.NativePreviewError):
                native._binding()
        for number in (1, 999):
            version = lambda: number
            fake = types.SimpleNamespace(narwhal_pure_core_abi_version=version)
            with patch.object(native, "_cache", None), patch.object(native.C, "CDLL", return_value=fake):
                with self.assertRaises(native.NativePreviewError):
                    native._binding()

    def test_invalid_shape_nonfinite_and_overflow(self):
        for center, radius in [((0, 0), 2), ((float("nan"), 0, 0), 2), ((0, 0, 0), float("inf"))]:
            with self.assertRaises(native.PreviewInputError):
                preview.circle_polyline(cplane.WORLD_TOP, center, radius)
        with self.assertRaises(native.PreviewInputError):
            preview.circle_polyline(types.SimpleNamespace(xaxis=(1e308, 0, 0), yaxis=(0, 1, 0)), (0, 0, 0), 1e308)

    def test_segments_bound_before_allocation(self):
        for count in [0, -1, 100001, 1 << 100, 3.5]:
            with self.assertRaises(native.PreviewInputError):
                preview.circle_polyline(cplane.WORLD_TOP, (0, 0, 0), 2, count)

    def test_degenerate_polygons_and_empty_picks(self):
        self.assertEqual(preview.polygon_polyline(cplane.WORLD_TOP, (0, 0, 0), 2, 2), [])
        self.assertEqual(preview.preview_ellipse([], (0, 0, 0), cplane.WORLD_TOP), [])
        self.assertEqual(preview.preview_ellipsoid([], (0, 0, 0), cplane.WORLD_TOP), [])

    def test_bad_status_is_protocol_failure(self):
        with patch.object(native, "_binding", return_value=lambda *a: -1):
            with self.assertRaises(native.NativePreviewError):
                preview.circle_polyline(cplane.WORLD_TOP, (0, 0, 0), 2)

    def test_bad_metadata_is_protocol_failure(self):
        def bad(*args):
            meta = C.cast(args[-1], C.POINTER(native.Metadata)).contents
            meta.rows = 4
            return 0
        with patch.object(native, "_binding", return_value=bad):
            with self.assertRaises(native.NativePreviewError):
                preview.circle_polyline(cplane.WORLD_TOP, (0, 0, 0), 2)


if __name__ == "__main__":
    unittest.main()
