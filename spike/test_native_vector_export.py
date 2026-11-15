"""Required vector host boundary. Independent numeric/byte corpus lives in Rust.

No Python encoder/oracle, build-on-test, old shared adapter, or fallback route.
"""
import ctypes as C
import hashlib
import io
import importlib.util
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

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

native = load("native_vector_export")
ve = load("vector_export")
BZ = (((0., 0.), (1., 2.), (3., 4.), (5., 6.)),)
S = ve.Stroke(BZ)

def doubles(s):
    return [] if not s else [struct.unpack(">d", bytes.fromhex(x))[0] for x in s.split(",")]

def ints(s):
    return [] if not s else list(map(int, s.split(",")))

def fixture(row):
    f = row.split("|")
    counts, flags, rgb, widths, coordinates, lens = ints(f[8]), ints(f[9]), doubles(f[10]), doubles(f[11]), doubles(f[12]), ints(f[13])
    names = bytes.fromhex(f[14]); strokes = []; pos = offset = 0; pdf = f[0] == "0"
    for i, count in enumerate(counts):
        segments = []
        for _ in range(count):
            segments.append(tuple(tuple(coordinates[j:j+2]) for j in range(pos, pos+8, 2)))
            pos += 8
        name = names[offset:offset+lens[i]].decode("latin-1" if pdf else "utf-8"); offset += lens[i]
        strokes.append(ve.Stroke(tuple(segments), bool(flags[i]), tuple(rgb[3*i:3*i+3]), widths[i], name))
    unit = (bytes.fromhex(f[6]).decode("utf-8"), doubles(f[4])[0], int(f[5]))
    return pdf, strokes, doubles(f[3]), bytes.fromhex(f[7]).decode("latin-1"), unit, bytes.fromhex(f[15])


class NativeVector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native._binding()  # Required matching candidate; never builds or stages.

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vector23-")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.path = self.directory / "output"
        self.path.write_bytes(b"KEEP EXISTING DESTINATION")

    def test_original_fixture_bytes_container_routing_and_ai_alias(self):
        raw = (ROOT / "native/pure_core/tests/fixtures/vector23_golden.txt").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "83cee4db03dec50a35f767881c399a7e917ab6e5b68b6374fdd186e001cf7e6a")
        rows = raw.decode().splitlines()
        for index in list(range(9)) + list(range(600, 618)) + list(range(726, 744)):
            pdf, strokes, page, title, unit, expected = fixture(rows[index])
            with self.subTest(index=index):
                before = native._native_calls
                if pdf:
                    count = ve.write_pdf(self.path, iter(strokes), *page, title)
                else:
                    count = ve.write_svg(self.path, iter(strokes), *page, unit=unit)
                self.assertEqual(count, len(expected)); self.assertEqual(self.path.read_bytes(), expected)
                self.assertEqual(native._native_calls, before+1)
                if pdf:
                    self.assertEqual(ve.write_ai(self.path, strokes, *page, title), count)
                    self.assertEqual(self.path.read_bytes(), expected)

    def test_public_field_order_frozen_and_one_shot_iterable(self):
        import dataclasses
        self.assertEqual([f.name for f in dataclasses.fields(ve.Stroke)], ["beziers", "closed", "rgb", "width_mm", "layer"])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            S.closed = True
        visits = []
        def once():
            visits.append(True)
            yield S
        ve.write_pdf(self.path, once(), 210., 297.)
        self.assertEqual(visits, [True])

    def test_latin1_unicode_and_nonstring_title_errors_are_not_coerced(self):
        for title, error, message in ((123, TypeError, "'int' object is not iterable"),
                                      (None, TypeError, "'NoneType' object is not iterable"),
                                      (b"abc", TypeError, "sequence item 0: expected str instance, int found")):
            with self.subTest(title=title), self.assertRaises(error) as caught:
                ve.write_pdf(self.path, [], 210., 297., title)
            self.assertEqual(str(caught.exception), message)
        with self.assertRaises(UnicodeEncodeError):
            ve.write_pdf(self.path, [S], 210., 297., "Ω")
        with self.assertRaises(UnicodeEncodeError):
            ve.write_pdf(self.path, [ve.Stroke(BZ, layer="Ω")], 210., 297.)
        self.assertEqual(self.path.read_bytes(), b"KEEP EXISTING DESTINATION")
        ve.write_svg(self.path, [ve.Stroke(BZ, layer='Ω & <"é">')], 210., 297., unit=("微米", .001, 13))
        self.assertIn('Ω &amp; &lt;&quot;é&quot;&gt;'.encode(), self.path.read_bytes())
        self.assertIn('微米'.encode(), self.path.read_bytes())

    def test_original_nonstring_title_iterable_atoms_are_exact(self):
        data = (ROOT / "native/pure_core/tests/fixtures/vector23_titles.txt").read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), "b0e3be1f468939ef0d57a7a924f74abe8e296b679b24e3bbda69fdcfe0346f3f")
        for row in data.decode().splitlines():
            lengths, text, expected = row.split("|")
            raw = bytes.fromhex(text); pos = 0; parts = []
            for length in ints(lengths):
                parts.append(raw[pos:pos+length].decode("latin-1")); pos += length
            count = ve.write_pdf(self.path, [], 210., 297., iter(parts))
            self.assertEqual(self.path.read_bytes(), bytes.fromhex(expected))
            self.assertEqual(count, len(bytes.fromhex(expected)))

    def test_invalid_inputs_never_open_destination(self):
        cases = [([ve.Stroke((((0., 0.), (1., 2.)),))], 210., 297., None),
                 ([ve.Stroke((((0., 0.), (1., 2., 3.), (3., 4.), (5., 6.)),))], 210., 297., None),
                 ([ve.Stroke((((0., 0.), (float("nan"), 2.), (3., 4.), (5., 6.)),))], 210., 297., None),
                 ([ve.Stroke(BZ, rgb=(float("inf"), 0., 0.))], 210., 297., None),
                 ([ve.Stroke(BZ, width_mm=float("nan"))], 210., 297., None),
                 ([S], float("nan"), 297., None), ([S], 210., float("inf"), None),
                 ([S], 210., 297., ("mm", 0., 4)), ([S], 210., 297., ("mm", -1., 4)),
                 ([S], 210., 297., ("mm", float("nan"), 4)),
                 ([S], 210., 297., ("mm", 1., -1)), ([S], 210., 297., ("mm", 1., 4097)),
                 ([S], 210., 297., ("mm", 1e-320, 4))]
        for strokes, w, h, unit in cases:
            with self.subTest(w=w, unit=unit), patch("builtins.open", side_effect=AssertionError("opened invalid destination")):
                with self.assertRaises(native.VectorInputError):
                    ve.write_svg(self.path, strokes, w, h, unit=unit)
            self.assertEqual(self.path.read_bytes(), b"KEEP EXISTING DESTINATION")
        with self.assertRaises(native.VectorInputError):
            ve.write_pdf(self.path, [S], 1e308, 297.)
        self.assertEqual(self.path.read_bytes(), b"KEEP EXISTING DESTINATION")

    def test_missing_library_is_required_and_environment_cache_recovers(self):
        with patch.dict(os.environ, {"NARWHAL_PURE_CORE": str(self.directory / "missing.so")}):
            with self.assertRaisesRegex(native.NativeVectorError, "missing or stale"):
                ve.write_pdf(self.path, [S], 210., 297.)
        self.assertEqual(self.path.read_bytes(), b"KEEP EXISTING DESTINATION")
        self.assertGreater(ve.write_pdf(self.path, [S], 210., 297.), 0)

    def test_old_library_with_no_new_exports_is_explicitly_refused(self):
        class OldLibrary:
            @staticmethod
            def narwhal_pure_core_abi_version(): return 1
        with patch.object(native, "_cache", None), patch.object(native.C, "CDLL", return_value=OldLibrary()):
            with self.assertRaisesRegex(native.NativeVectorError, "missing or stale"):
                ve.write_svg(self.path, [S], 210., 297.)
        self.assertEqual(self.path.read_bytes(), b"KEEP EXISTING DESTINATION")

    def test_success_without_owned_handle_and_native_refusals_are_typed(self):
        binding = native._binding()
        for code, kind in ((0, native.NativeVectorError), (-2, native.VectorInputError), (-3, native.VectorInputError), (-4, native.VectorInputError), (-5, native.VectorInputError), (17, native.NativeVectorError)):
            with self.subTest(code=code), patch.object(native, "_binding", return_value=(lambda *a: code, *binding[1:])):
                with self.assertRaises(kind): ve.write_pdf(self.path, [S], 210., 297.)
            self.assertEqual(self.path.read_bytes(), b"KEEP EXISTING DESTINATION")

    def test_bad_length_copy_count_and_magic_release_native_ownership(self):
        encode, length, copy, free, validate = native._binding()
        def bad_magic(handle, out, capacity, written):
            code = copy(handle, out, capacity, written); out[0] = 0; return code
        for new_length, new_copy in ((lambda h: 0, copy), (lambda h: 512*1024*1024+1, copy), (length, lambda *a: 0), (length, bad_magic)):
            before = native._native_calls
            with patch.object(native, "_binding", return_value=(encode, new_length, new_copy, free, validate)), patch.object(native, "_cache", native._cache):
                released = []
                def release(handle): released.append(handle.value); free(handle)
                with patch.object(native, "_binding", return_value=(encode, new_length, new_copy, release, validate)):
                    with self.assertRaises(native.NativeVectorError): ve.write_pdf(self.path, [S], 210., 297.)
                self.assertEqual(len(released), 1)
            self.assertEqual(native._native_calls, before)
            self.assertEqual(self.path.read_bytes(), b"KEEP EXISTING DESTINATION")

    def test_copied_xref_and_utf8_corruption_are_refused_before_open(self):
        encode, length, copy, free, validate = native._binding()
        for pdf in (True, False):
            def corrupt(handle, out, capacity, written):
                code = copy(handle, out, capacity, written)
                if pdf:
                    data = bytes(out[:capacity]); offset = data.index(b"startxref\n") + 10
                    out[offset] = ord("x")
                else:
                    out[capacity-8] = 255  # keep header/end tags, invalidate UTF8
                return code
            with self.subTest(pdf=pdf), patch.object(native, "_binding", return_value=(encode, length, corrupt, free, validate)):
                with self.assertRaisesRegex(native.NativeVectorError, "framing/xref/UTF-8"):
                    (ve.write_pdf if pdf else ve.write_svg)(self.path, [S], 210., 297.)
            self.assertEqual(self.path.read_bytes(), b"KEEP EXISTING DESTINATION")

    def test_unclamped_rgb_and_large_integer_refusal_are_explicit(self):
        ve.write_svg(self.path, [ve.Stroke(BZ, rgb=(-.1, 1.2, .5))], 210., 297.)
        self.assertIn(b"rgb(-26,306,128)", self.path.read_bytes())
        self.path.write_bytes(b"KEEP EXISTING DESTINATION")
        with self.assertRaisesRegex(native.VectorInputError, "integer RGB exceeds"):
            ve.write_svg(self.path, [ve.Stroke(BZ, rgb=(10**100, 0, 0))], 210., 297.)
        self.assertEqual(self.path.read_bytes(), b"KEEP EXISTING DESTINATION")

    def test_raw_copy_refusal_preserves_count_and_payload(self):
        req, keepalive = native._pack([S], 210., 297., "T", None, True)
        encode, length, copy, free, validate = native._binding(); handle = C.c_void_p()
        self.assertEqual(encode(C.byref(req), C.byref(handle)), 0)
        try:
            out = (C.c_ubyte*8)(*[165]*8); written = C.c_size_t(99)
            self.assertEqual(copy(handle, out, 8, C.byref(written)), -3)
            self.assertEqual((list(out), written.value), ([165]*8, 99))
        finally: free(handle)

    def test_original_open_write_close_and_actual_os_errors_are_preserved(self):
        for path in (self.directory, self.directory / "absent" / "out"):
            # Compare the actual platform's open error, not a simulated Unix
            # directory exception (Windows may raise PermissionError instead).
            with self.assertRaises(OSError) as original:
                with open(path, "wb"):
                    pass
            with self.assertRaises(type(original.exception)) as actual:
                ve.write_pdf(path, [S], 210., 297.)
            self.assertEqual(actual.exception.errno, original.exception.errno)
            self.assertEqual(actual.exception.filename, original.exception.filename)
        denied = PermissionError(13, "injected actual open failure", str(self.path))
        with patch("builtins.open", side_effect=denied) as opened:
            with self.assertRaises(PermissionError) as caught:
                ve.write_pdf(self.path, [S], 210., 297.)
        self.assertIs(caught.exception, denied)
        opened.assert_called_once_with(self.path, "wb")
        self.assertEqual(self.path.read_bytes(), b"KEEP EXISTING DESTINATION")
        failure = OSError("injected stream write failure")
        class BrokenStream(io.BytesIO):
            def write(self, data):
                raise failure
        stream = BrokenStream()
        with patch("builtins.open", return_value=stream) as opened:
            with self.assertRaises(OSError) as caught:
                ve.write_pdf(self.path, [S], 210., 297.)
        self.assertIs(caught.exception, failure)
        self.assertTrue(stream.closed)
        opened.assert_called_once_with(self.path, "wb")
        close_failure = OSError("injected stream close failure")
        class BrokenClose(io.BytesIO):
            def __exit__(self, *args):
                super().__exit__(*args)
                raise close_failure
        stream = BrokenClose()
        with patch("builtins.open", return_value=stream):
            with self.assertRaises(OSError) as caught:
                ve.write_pdf(self.path, [S], 210., 297.)
        self.assertIs(caught.exception, close_failure)
        self.assertTrue(stream.closed)
        # Ordinary open also accepts special files and an already-open fd;
        # serialization must not add path-only or regular-file restrictions.
        self.assertEqual(ve.write_pdf(os.devnull, [S], 210., 297.),
                         len(native.encode([S], 210., 297., pdf=True)))
        fd = os.open(self.path, os.O_WRONLY)
        try:
            count = ve.write_svg(fd, [S], 210., 297.)
            self.assertEqual(count, self.path.stat().st_size)
            with self.assertRaises(OSError):
                os.fstat(fd)  # open(fd, 'wb') closes the supplied fd on exit
            fd = None
        finally:
            if fd is not None:
                os.close(fd)

    @unittest.skipIf(os.name == "nt", "Unix mode bits, symlink and FIFO setup")
    def test_original_inode_hardlink_symlink_mode_and_readonly_parent_semantics(self):
        self.path.chmod(0o640)
        link = self.directory / "link"; link.symlink_to(self.path)
        hard = self.directory / "hard"; os.link(self.path, hard)
        inode = self.path.stat().st_ino
        link_inode = link.lstat().st_ino
        directory_mode = self.directory.stat().st_mode & 0o777
        expected = bytes(native.encode([S], 210., 297., pdf=True))
        try:
            self.directory.chmod(0o555)
            self.assertEqual(ve.write_pdf(link, [S], 210., 297.), len(expected))
        finally:
            self.directory.chmod(directory_mode)
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.lstat().st_ino, link_inode)
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(hard.stat().st_ino, inode)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.path.read_bytes(), expected)
        self.assertEqual(hard.read_bytes(), expected)
        fifo = self.directory / "fifo"
        os.mkfifo(fifo)
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            self.assertEqual(ve.write_pdf(fifo, [S], 210., 297.), len(expected))
            self.assertEqual(os.read(reader, len(expected)+1), expected)
        finally:
            os.close(reader)

    def test_qualified_direct_file_loading_does_not_require_blender_package(self):
        name = "absent_vector_parent.vector_export"
        spec = importlib.util.spec_from_file_location(name, ROOT / "narwhal3d/vector_export.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {name: module}):
            spec.loader.exec_module(module)
            self.assertGreater(module.write_svg(self.path, [module.Stroke(BZ)], 210., 297.), 0)
        self.assertTrue(self.path.read_bytes().endswith(b"</svg>\n"))

    def test_generator_exception_and_native_pure_absence_do_not_trigger_replay(self):
        def broken():
            yield S
            raise RuntimeError("input iterator failed")
        with self.assertRaisesRegex(RuntimeError, "input iterator failed"):
            ve.write_pdf(self.path, broken(), 210., 297.)
        self.assertEqual(self.path.read_bytes(), b"KEEP EXISTING DESTINATION")
        with patch.dict(sys.modules, {"narwhal3d.native_pure": None, "narwhal3d_native_pure": None}):
            self.assertGreater(ve.write_svg(self.path, [S], 210., 297.), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
