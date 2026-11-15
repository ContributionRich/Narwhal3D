"""OBJ host boundary tests. Computational byte parity lives in Rust obj_golden.

Required native failures must never replay a Python serializer or touch a file.
No dependency on native_pure's private binding tuple (STL cleanup may shrink it).
"""
import ctypes
import errno
import importlib.util
import os
from pathlib import Path
import sys
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("narwhal_obj_writer", ROOT / "narwhal3d/obj_writer.py")
obj = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obj)
native = obj._native_obj()


def mesh():
    return [("caf\xe9", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])]


class OBJHost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sentinel.obj"
        self.path.write_bytes(b"original destination")

    def refused(self, data, error):
        with self.assertRaises(error):
            obj.write_obj(self.path, data)
        self.assertEqual(self.path.read_bytes(), b"original destination")
        missing = self.path.with_name("absent.obj")
        with self.assertRaises(error):
            obj.write_obj(missing, data)
        self.assertFalse(missing.exists())

    def test_small_file_native_and_count(self):
        before = native._native_obj_calls
        n = obj.write_obj(self.path, mesh())
        self.assertEqual(n, self.path.stat().st_size)
        self.assertEqual(native._native_obj_calls, before + 1)
        self.assertEqual(self.path.read_bytes(), b"# Narwhal 3D\no caf\xe9\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")

    def test_one_shot_iterables(self):
        data = ((name, iter(v), (iter(f) for f in fs)) for name, v, fs in mesh())
        self.assertGreater(obj.write_obj(self.path, data), 13)

    def test_empty(self):
        self.assertEqual(obj.write_obj(self.path, iter(())), 13)
        self.assertEqual(self.path.read_bytes(), b"# Narwhal 3D\n")

    def test_invalid_mesh(self):
        for bad in [None, [("x", None, [])], [("x", [(0, 1)], [])],
                    [("x", [("nonnumeric", 0, 0)], [])]]:
            with self.subTest(bad=bad):
                self.refused(bad, ValueError)

    def test_invalid_faces(self):
        for face in [(0, 1), (-1, 1, 2), (0, 1, 3), (0.0, 1, 2),
                     (0, 1, 1 << 100), ("0", 1, 2)]:
            with self.subTest(face=face):
                self.refused([("x", mesh()[0][1], [face])], ValueError)

    def test_nonfinite_is_intentional_safety_refusal(self):
        for bad in [float("nan"), float("inf"), -float("inf")]:
            self.refused([("x", [(bad, 0, 0)], [])], ValueError)

    def test_unicode_error_is_not_wrapped_as_value_error(self):
        try:
            obj.write_obj(self.path, [("not Latin1: \u20ac", [], [])])
        except UnicodeEncodeError as exc:
            self.assertIsInstance(exc, ValueError)
            self.assertEqual(exc.encoding, "latin-1")
            self.assertEqual(exc.object, "not Latin1: \u20ac")
            self.assertEqual((exc.start, exc.end), (12, 13))
        else:
            self.fail("UnicodeEncodeError swallowed")
        self.assertEqual(self.path.read_bytes(), b"original destination")

    def test_missing_library(self):
        with patch.dict(os.environ, NARWHAL_PURE_CORE=str(self.path.with_name("missing.so"))):
            self.refused(mesh(), RuntimeError)

    def test_stale_library(self):
        with patch.object(native, "_cache", None), patch.object(native.C, "CDLL", return_value=object()):
            self.refused(mesh(), RuntimeError)

    def test_abi_mismatch(self):
        class Function:
            def __call__(self):
                return 999
        class Library:
            narwhal_pure_core_abi_version = Function()
        with patch.object(native, "_cache", None), patch.object(native.C, "CDLL", return_value=Library()):
            self.refused(mesh(), RuntimeError)

    def test_native_protocol(self):
        for rc, count, header in [(-1, -1, True), (-3, -1, True), (7, -1, True),
                                  (0, -1, True), (0, 1 << 62, True), (0, 13, False), (0, 13, True)]:
            def broken(*args):
                if header:
                    ctypes.memmove(args[-3], b"# Narwhal 3D\n", 13)
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_int64))[0] = count
                return rc
            with self.subTest(rc=rc, count=count), patch.object(native, "_binding", return_value=broken):
                self.refused(mesh(), RuntimeError)

    def test_native_call_failure(self):
        with patch.object(native, "_binding", return_value=lambda *args: (_ for _ in ()).throw(ctypes.ArgumentError("bad ABI"))):
            self.refused(mesh(), RuntimeError)

    def test_native_domain_error(self):
        with patch.object(native, "_binding", return_value=lambda *args: -2):
            self.refused(mesh(), ValueError)

    def test_file_oserror_identity(self):
        error = PermissionError(13, "test permission", str(self.path))
        with patch("builtins.open", side_effect=error):
            with self.assertRaises(PermissionError) as caught:
                obj.write_obj(self.path, mesh())
        self.assertIs(caught.exception, error)
        expected_type, expected_errno = (
            (PermissionError, errno.EACCES) if sys.platform == "win32"
            else (IsADirectoryError, errno.EISDIR))
        with self.assertRaises(expected_type) as directory_error:
            obj.write_obj(Path(self.tmp.name), mesh())
        self.assertIs(type(directory_error.exception), expected_type)
        self.assertEqual(directory_error.exception.errno, expected_errno)


class OBJDecodeHost(unittest.TestCase):
    """Unchanged reader boundary coverage; writer retirement does not retire readers."""


def _install_decoder_checks():
    native = obj._native_obj()._packer()
    _LIB = os.environ.get("NARWHAL_PURE_CORE") or str(ROOT / "narwhal3d/native" / native._library_name())
    temp = tempfile.TemporaryDirectory(prefix="native_obj_decode_")
    OBJDecodeHost._temporary = temp
    TMP = temp.name
    spec = importlib.util.spec_from_file_location("narwhal_obj_reader", ROOT / "narwhal3d/obj_reader.py")
    rdr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rdr)
    count = 0

    def _reset():
        native._reset_for_tests()

    def check(name):
        def deco(fn):
            nonlocal count
            count += 1
            def test(self):
                fn()
            test.__doc__ = name
            setattr(OBJDecodeHost, "test_decode_%02d" % count, test)
            return fn
        return deco

    @check("native decode of a single mesh returns (meshes, ignored) and moves the counter")
    def _():
        _reset()
        assert native.available() is True, native.backend_status()
        data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
        before = native._native_decode_obj_calls
        got = native.decode_obj(data, "m")
        assert native._native_decode_obj_calls == before + 1, \
            ("counter must move on a successful native decode",
             before, native._native_decode_obj_calls)
        assert got == ([("m", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                         [(0, 1, 2)])], {}), got


    @check("native decode of a two-mesh file is struct-identical to the Python reader")
    def _():
        _reset()
        assert native.available() is True, native.backend_status()
        # A file the paired writer would emit (mesh A: 4 verts / 2 tris; mesh B:
        # 4 verts / 1 quad, written with GLOBAL indices 5 6 7 8).
        data = (b"o floor\n"
                b"v 0 0 0\nv 10 0 0\nv 10 10 0\nv 0 10 0\n"
                b"f 1 2 3\nf 1 3 4\n"
                b"o roof\n"
                b"v 0 0 5\nv 10 0 5\nv 10 10 5\nv 0 10 5\n"
                b"f 5 6 7 8\n")
        meshes, ignored = native.decode_obj(data, "model")
        assert ignored == {}, ignored
        assert [m[0] for m in meshes] == ["floor", "roof"], [m[0] for m in meshes]
        # Mesh A: two triangles. Mesh B: one quad (kept at its polygon degree).
        assert meshes[0][1] == [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
                                (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)], meshes[0][1]
        assert meshes[0][2] == [(0, 1, 2), (0, 2, 3)], meshes[0][2]
        assert meshes[1][2] == [(0, 1, 2, 3)], meshes[1][2]


    @check("native decode keeps negative indices and all four face forms")
    def _():
        _reset()
        assert native.available() is True, native.backend_status()
        data = (b"v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\n"
                b"f -4 -3 -2 -1\n"
                b"f 1 2 3\n"
                b"f 1/2 2/3 3/4\n"
                b"f 1//2 2//3 3//4\n"
                b"f 1/2/3 2/3/4 3/4/5\n")
        meshes, _ = native.decode_obj(data, "m")
        assert meshes[0][2] == [(0, 1, 2, 3), (0, 1, 2), (0, 1, 2),
                                (0, 1, 2), (0, 1, 2)], meshes[0][2]


    @check("native decode handles CRLF and CR line endings")
    def _():
        _reset()
        assert native.available() is True, native.backend_status()
        for data in (b"v 0 0 0\r\nv 1 0 0\r\nv 0 1 0\r\nf 1 2 3\r\n",
                     b"v 0 0 0\rv 1 0 0\rv 0 1 0\rf 1 2 3\r"):
            meshes, _ = native.decode_obj(data, "m")
            assert meshes[0][1] == [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                                    (0.0, 1.0, 0.0)], meshes[0][1]
            assert meshes[0][2] == [(0, 1, 2)], meshes[0][2]


    @check("native decode counts the ignored kinds (vt vn vp s mtllib usemtl)")
    def _():
        _reset()
        assert native.available() is True, native.backend_status()
        data = (b"mtllib mat.mtl\nusemtl steel\n"
                b"v 0 0 0\nv 1 0 0\nv 0 1 0\n"
                b"vt 0 0\nvt 1 0\n"
                b"vn 0 0 1\n"
                b"vp 0 0 0 1\n"
                b"s 1\n"
                b"f 1 2 3\n")
        meshes, ignored = native.decode_obj(data, "m")
        assert ignored == {"mtllib": 1, "usemtl": 1, "vt": 2, "vn": 1, "vp": 1,
                           "s": 1}, ignored


    @check("read_obj activates the native decode route (counter moves, output matches Python)")
    def _():
        _reset()
        assert native.available() is True, native.backend_status()
        text = ("# comment\n"
                "o A\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
                "g B\nv 5 5 5\nv 6 5 5\nv 5 6 5\nf 4 5 6\n"
                "mtllib m.mtl\nvt 0 0\nvn 0 0 1\ns 1\n")
        p = os.path.join(TMP, "route.obj")
        with open(p, "w", encoding="latin-1") as fh:
            fh.write(text)
        before = native._native_decode_obj_calls
        got = rdr.read_obj(p)
        assert native._native_decode_obj_calls > before, \
            ("route did not use native decode (counter %d -> %d)" %
             (before, native._native_decode_obj_calls))
        assert [m[0] for m in got] == ["A", "B"], [m[0] for m in got]
        assert got[0][2] == [(0, 1, 2)] and got[1][2] == [(0, 1, 2)], got
        kinds = rdr.ignored_kinds()
        assert kinds.get("mtllib") == 1, kinds
        assert kinds.get("vt") == 1, kinds
        assert kinds.get("vn") == 1, kinds
        assert kinds.get("s") == 1, kinds


    @check("read_obj on an empty file returns [] via native decode")
    def _():
        _reset()
        assert native.available() is True, native.backend_status()
        p = os.path.join(TMP, "empty.obj")
        with open(p, "w", encoding="latin-1") as fh:
            fh.write("")
        before = native._native_decode_obj_calls
        got = rdr.read_obj(p)
        assert native._native_decode_obj_calls > before, \
            ("empty file must go through native decode (counter %d -> %d)" %
             (before, native._native_decode_obj_calls))
        assert got == [], got
        assert rdr.ignored_kinds() == {}, rdr.ignored_kinds()


    @check("native decode rejects a v with too few coordinates")
    def _():
        _reset()
        assert native.available() is True, native.backend_status()
        data = b"v 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
        before = native._native_decode_obj_calls
        got = native.decode_obj(data, "m")
        assert got is None, got
        assert native._native_decode_obj_calls == before, \
            "counter must not move on a rejected file"


    @check("native decode rejects a face index beyond the vertex count")
    def _():
        _reset()
        assert native.available() is True, native.backend_status()
        data = b"v 0 0 0\nv 1 0 0\nf 1 2 99\n"
        before = native._native_decode_obj_calls
        got = native.decode_obj(data, "m")
        assert got is None, got
        assert native._native_decode_obj_calls == before, \
            "counter must not move on a rejected file"


    @check("fallback: missing library -> Python reader, counter NOT moved")
    def _():
        _reset()
        text = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
        p = os.path.join(TMP, "fb_missing.obj")
        with open(p, "w", encoding="latin-1") as fh:
            fh.write(text)
        os.environ["NARWHAL_PURE_CORE"] = os.path.join(TMP, "no_such_lib.so")
        _reset()
        before = native._native_decode_obj_calls
        got = rdr.read_obj(p)
        assert native._native_decode_obj_calls == before, \
            "counter must not move on a missing-library fallback"
        assert got == [("fb_missing", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                                      (0.0, 1.0, 0.0)], [(0, 1, 2)])], got
        if _LIB is not None:
            os.environ["NARWHAL_PURE_CORE"] = _LIB


    @check("fallback: a library without the OBJ decode exports -> Python reader")
    def _():
        _reset()
        assert _LIB is not None
        assert native.available() is True, native.backend_status()
        real_binding = native._binding

        def _binding_no_decode():
            b = real_binding()
            if b is None:
                return None
            # Resolve by exported symbol, not tuple positions: STL cleanup removes
            # four unrelated slots concurrently.
            return tuple(None if getattr(fn, "__name__", "") in
                         ("narwhal_pure_core_obj_decode_size", "narwhal_pure_core_obj_decode")
                         else fn for fn in b)

        native._binding = _binding_no_decode
        try:
            data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
            before = native._native_decode_obj_calls
            got = native.decode_obj(data, "m")
            assert got is None, got
            assert native._native_decode_obj_calls == before, \
                "counter must not move when the decode exports are absent"
        finally:
            native._binding = real_binding


    @check("fallback: a malformed native packed output -> Python reader")
    def _():
        _reset()
        assert native.available() is True, native.backend_status()
        real_parse = native._parse_obj_packed

        def _raise(*_a, **_k):
            raise ValueError("bad packed")

        native._parse_obj_packed = _raise
        try:
            data = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
            before = native._native_decode_obj_calls
            got = native.decode_obj(data, "m")
            assert got is None, got
            assert native._native_decode_obj_calls == before, \
                "counter must not move on a malformed packed output"
        finally:
            native._parse_obj_packed = real_parse


    def _obj_packed(*, vcount=9, fcount=1, arity=3, indices=(0, 1, 2),
                    reserved=b"\0" * 8, trailing=b""):
        return b"".join([
            struct.pack("<IIII", 0x33445741, 1, 1, 0), reserved,
            struct.pack("<q", 1), b"m",
            struct.pack("<q", vcount), struct.pack("<q", fcount),
            struct.pack("<q", arity),
            struct.pack("<9d", 0, 0, 0, 1, 0, 0, 0, 1, 0),
            struct.pack("<%dq" % len(indices), *indices), trailing,
        ])


    @check("strict OBJ packed validation rejects counts, arity, indices, and trailing bytes")
    def _():
        assert native._parse_obj_packed(_obj_packed(), "m")[0][0][2] == [(0, 1, 2)]
        bad_payloads = [
            _obj_packed(vcount=8),
            _obj_packed(fcount=-1),
            _obj_packed(arity=2, indices=(0, 1)),
            _obj_packed(indices=(0, 1, 3)),
            _obj_packed(reserved=b"\0" * 7 + b"x"),
            _obj_packed(trailing=b"x"),
        ]
        for payload in bad_payloads:
            try:
                native._parse_obj_packed(payload, "m")
                raise AssertionError("malformed packed OBJ was accepted: %r" % payload[:40])
            except ValueError:
                pass


    @check("malformed successful OBJ packed output returns None without counting")
    def _():
        _reset()
        real_binding = native._binding
        base = real_binding()
        payload = _obj_packed(indices=(0, 1, 3))

        def size_fn(_data, _length):
            return len(payload)

        def decode_fn(_data, _length, out, out_len):
            assert out_len == len(payload)
            ctypes.memmove(out, payload, len(payload))
            return 0

        replacements = {"narwhal_pure_core_obj_decode_size": size_fn,
                        "narwhal_pure_core_obj_decode": decode_fn}
        native._binding = lambda: tuple(replacements.get(getattr(fn, "__name__", ""), fn)
                                        for fn in base)
        try:
            before = native._native_decode_obj_calls
            assert native.decode_obj(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", "m") is None
            assert native._native_decode_obj_calls == before
        finally:
            native._binding = real_binding




_install_decoder_checks()

if __name__ == "__main__":
    unittest.main()
