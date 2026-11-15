"""Required writer host boundaries; no retired implementation is loaded."""
import ctypes
import errno
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "native/pure_core/tests/fixtures/dxf_writer23"
spec = importlib.util.spec_from_file_location("owned_dxf_writer", ROOT / "narwhal3d/dxf_writer.py")
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)
native = writer._native_dxf()
LINE = {"type": "LINE", "p0": (0., 0., 0.), "p1": (1., 2., 3.)}


class DXFWriterOwnedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binding = native._binding()
        cls.capture = json.loads((FIXTURES / "capture.json").read_text())

    def test_original_error_classes_messages_and_unicode_object(self):
        for row in self.capture["errors"]:
            with self.subTest(name=row["name"]):
                try:
                    writer.build_dxf_bytes(row["entities"], row["layers"])
                except Exception as exc:
                    self.assertEqual(type(exc).__name__, row["type"])
                    self.assertEqual(str(exc), row["message"])
                    if isinstance(exc, UnicodeEncodeError):
                        self.assertEqual((exc.object, exc.start, exc.end, exc.encoding, exc.reason),
                                         (row["object"], row["start"], row["end"], row["encoding"], row["reason"]))
                else:
                    self.fail(row["name"])

    def test_unsafe_input_never_mutates_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keep.dxf"
            entities = [dict(LINE, p0=(value, 0., 0.)) for value in (math.nan, math.inf, -math.inf)]
            entities += [dict(LINE, layer=value) for value in ("bad\nname", "bad\rname", "bad\0name")]
            entities += [{"type": "CIRCLE", "center": (0, 0, 0), "radius": -1},
                         {"type": "ARC", "center": (0, 0, 0), "radius": 0, "start_deg": 0, "end_deg": 90}]
            for entity in entities:
                with self.subTest(entity=entity):
                    path.write_bytes(b"keep existing file")
                    with self.assertRaisesRegex(ValueError, "DXF invalid"):
                        writer.write_dxf(path, [entity])
                    self.assertEqual(path.read_bytes(), b"keep existing file")

    def test_missing_invalid_stale_and_wrong_abi_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "keep.dxf"
            invalid = Path(directory) / "invalid.so"
            invalid.write_bytes(b"not a library")
            for lib in (str(Path(directory) / "absent.so"), str(invalid)):
                with patch.dict(os.environ, NARWHAL_PURE_CORE=lib):
                    target.write_bytes(b"keep")
                    with self.assertRaises(native.NativeDXFError):
                        writer.write_dxf(target, [LINE])
                    self.assertEqual(target.read_bytes(), b"keep")
                    with self.assertRaises(native.NativeDXFError):
                        writer.unit_header(None)
            class Stale:
                narwhal_pure_core_abi_version = self.binding["library"].narwhal_pure_core_abi_version
            with patch.object(native, "_cache", None), patch.object(native.C, "CDLL", return_value=Stale()):
                with self.assertRaisesRegex(native.NativeDXFError, "stale"):
                    writer.build_dxf_bytes([LINE])
            library = self.binding["library"]
            class WrongOwned:
                narwhal_pure_core_abi_version = library.narwhal_pure_core_abi_version
                narwhal_pure_core_dxf_owned_abi_version = staticmethod(lambda: 1)
                def __getattr__(self, name):
                    return getattr(library, name)
            with patch.object(native, "_cache", None), patch.object(native.C, "CDLL", return_value=WrongOwned()):
                with self.assertRaisesRegex(native.NativeDXFError, "owned ABI version mismatch"):
                    writer.build_dxf_bytes([LINE])
            class Wrong:
                narwhal_pure_core_abi_version = staticmethod(lambda: 99)
            with patch.object(native, "_cache", None), patch.object(native.C, "CDLL", return_value=Wrong()):
                with self.assertRaisesRegex(native.NativeDXFError, "version mismatch"):
                    writer.build_dxf_bytes([LINE])

    def test_native_errors_sizes_and_malformed_output_never_replay(self):
        for key, result in (("capacity", -1), ("encode", -1), ("encode", 0)):
            binding = dict(self.binding, **{key: lambda *args, result=result: result})
            with self.subTest(key=key, result=result), patch.object(native, "_binding", return_value=binding):
                before = native._native_dxf_calls
                with self.assertRaises(native.NativeDXFError):
                    writer.build_dxf_bytes([LINE])
                self.assertEqual(native._native_dxf_calls, before)
        def excessive(_input, size, _diag):
            ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))[0] = native._MAX_BYTES + 1
            return 0
        binding = dict(self.binding, capacity=excessive)
        binding["encode"] = lambda *args: self.fail("must not encode/allocate oversized output")
        with patch.object(native, "_binding", return_value=binding):
            with self.assertRaisesRegex(native.NativeDXFError, "capacity"):
                writer.build_dxf_bytes([LINE])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keep.dxf"
            path.write_bytes(b"keep")
            with patch.object(native, "_binding", return_value=binding):
                with self.assertRaises(native.NativeDXFError):
                    writer.write_dxf(path, [LINE])
            self.assertEqual(path.read_bytes(), b"keep")

    def test_abi_argument_failures_are_not_domain_errors_or_replay(self):
        def fail(*args):
            raise ctypes.ArgumentError("broken ABI call")
        for name in ("colors", "capacity", "encode"):
            binding = dict(self.binding, **{name: fail})
            with self.subTest(call=name), patch.object(native, "_binding", return_value=binding):
                with self.assertRaisesRegex(native.NativeDXFError, "ABI call failed"):
                    writer.build_dxf_bytes([LINE])
        for typ in (None, [], 123):
            with self.assertRaises(ValueError) as caught:
                writer.build_dxf_bytes([dict(LINE, type=typ)])
            self.assertEqual(str(caught.exception), "unknown entity type %r" % (typ,))

    def test_malformed_success_encoding_counts_and_diagnostics_refuse(self):
        real = self.binding["encode"]
        for defect in ("encoding-one", "encoding-invalid", "count", "diagnostic"):
            def corrupt(descriptor, out, cap, written, encoding, diag):
                rc = real(descriptor, out, cap, written, encoding, diag)
                self.assertEqual(rc, 0)
                if defect.startswith("encoding"):
                    ctypes.cast(encoding, ctypes.POINTER(ctypes.c_int64))[0] = 1 if defect == "encoding-one" else 7
                elif defect == "count":
                    ctypes.cast(written, ctypes.POINTER(ctypes.c_int64))[0] = cap + 1
                else:
                    diag[0] = -44
                return 0
            binding = dict(self.binding, encode=corrupt)
            with self.subTest(defect=defect), patch.object(native, "_binding", return_value=binding):
                before = native._native_dxf_calls
                with self.assertRaises(native.NativeDXFError):
                    writer.build_dxf_bytes([LINE])
                self.assertEqual(native._native_dxf_calls, before)

    def test_one_shot_iterables_are_consumed_once_in_both_public_apis(self):
        controls = [(0, 0, 0), (1, 2, 0), (2, 2, 0), (3, 0, 0)]
        for build in (True, False):
            seen = []
            def points():
                for value in controls:
                    seen.append(value)
                    yield value
            def entities():
                seen.append("entity")
                yield {"type": "SPLINE", "control_points": points()}
            with tempfile.TemporaryDirectory() as directory:
                if build:
                    data = writer.build_dxf_bytes(entities())
                else:
                    path = Path(directory) / "curve.dxf"
                    self.assertEqual(writer.write_dxf(path, entities()), path.stat().st_size)
                    data = path.read_bytes()
            self.assertEqual(len(seen), 5)
            self.assertIn(b"0\nSPLINE\n", data)
            self.assertIn(b"71\n3\n72\n8\n73\n4\n", data)
            self.assertNotIn(b"POLYLINE", data)

    def test_all_units_public_metadata_and_unknown_hashables(self):
        english = {"Inches", "Feet", "Miles", "Microinches", "Mils", "Yards"}
        self.assertEqual(writer.unit_header(None), (4, None))
        self.assertEqual(len(writer.INSUNITS), 17)
        for name, code in writer.INSUNITS.items():
            self.assertEqual(writer.unit_header(name), (code, 0 if name in english else 1))
        for unit in ("unknown", "", 0, False, (), b"Inches"):
            self.assertEqual(writer.unit_header(unit), (0, None))
        for unit in ([], {}):
            with self.assertRaises(TypeError):
                writer.unit_header(unit)

    def test_lone_surrogates_and_non_bmp_are_original_unicode_frames(self):
        for name in ("a\ud800", "x\U0001f40b"):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "keep.dxf"
                path.write_bytes(b"keep")
                with self.assertRaises(UnicodeEncodeError) as caught:
                    writer.write_dxf(path, [dict(LINE, layer=name)])
                exc = caught.exception
                self.assertEqual(exc.encoding, "latin-1")
                self.assertIn(name, exc.object)
                self.assertTrue(exc.object.startswith("0\nSECTION\n2\nHEADER\n"))
                self.assertEqual(path.read_bytes(), b"keep")

    def test_host_key_identity_and_first_seen_order(self):
        # Distinct Python keys may have equal string spellings: do not merge
        # layer 1 and layer "1" merely because both serialize as text "1".
        data = writer.build_dxf_bytes([dict(LINE, layer=1), dict(LINE, layer="1")], {1: 2, "1": 5})
        self.assertIn(b"2\nLAYER\n70\n2\n", data)
        self.assertLess(data.index(b"62\n2\n"), data.index(b"62\n5\n"))
        row = self.capture["host_key_alias"]
        data = writer.build_dxf_bytes(row["entities"], dict(row["layer_pairs"]))
        self.assertEqual(data, (FIXTURES / row["expected_file"]).read_bytes())
        # Repeated aliases share their marshalled text slot, not a million new
        # name entries. This remains a one-layer table even for many entities.
        data = writer.build_dxf_bytes(row["entities"] * 1000, dict(row["layer_pairs"]))
        self.assertIn(b"2\nLAYER\n70\n1\n", data)
        self.assertEqual(data.count(b"0\nLINE\n8\n1\n"), 1000)


    def test_file_errors_exact_type_and_errno(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = PermissionError if sys.platform == "win32" else IsADirectoryError
            number = errno.EACCES if sys.platform == "win32" else errno.EISDIR
            with self.assertRaises(expected) as caught:
                writer.write_dxf(directory, [LINE])
            self.assertIs(type(caught.exception), expected)
            self.assertEqual(caught.exception.errno, number)
            with self.assertRaises(FileNotFoundError):
                writer.write_dxf(Path(directory) / "absent" / "x.dxf", [LINE])

    def test_required_writer_does_not_import_optional_adapter(self):
        with patch.dict(sys.modules, {"narwhal3d_native_pure": None, "narwhal3d.native_pure": None}):
            data = writer.build_dxf_bytes([LINE], units="Inches")
        self.assertIn(b"$INSUNITS\n70\n1\n", data)
        self.assertIn(b"$MEASUREMENT\n70\n0\n", data)
        for name in ("_num", "_entity", "_point", "_emit_color", "_spline_knots", "_merged_layers"):
            self.assertFalse(hasattr(writer, name), name)

    def test_preallocation_input_limits(self):
        for attr, value, entities, layers in (
            ("_MAX_ENTITIES", 0, [LINE], None),
            ("_MAX_LAYERS", 0, [LINE], None),
            ("_MAX_COORDS", 0, [LINE], None),
            ("_MAX_NAMES", 1, [dict(LINE, layer="long")], None),
        ):
            with self.subTest(limit=attr), patch.object(native, attr, value):
                with self.assertRaisesRegex(ValueError, "limit"):
                    writer.build_dxf_bytes(entities, layers)


if __name__ == "__main__":
    unittest.main()
