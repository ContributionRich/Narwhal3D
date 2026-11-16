"""Required reader24 real ABI and hostile-host boundary checks."""
import ctypes as C
import errno
import importlib.util
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from dxf_reader24_fixtures import cases, freeze
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reader24_boundary", ROOT / "narwhal3d/dxf_reader.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
native = reader._native_dxf()
CORPUS = cases()
EMPTY = next(answer for row, raw, answer in CORPUS if row["name"] == "empty")


def encoded(value):
    # Typed protocol generator, not a DXF/numeric parser or a reference reader.
    if value is None:
        return b"\0"
    if type(value) is bool:
        return bytes([2 if value else 1])
    if type(value) is int:
        return b"\3" + struct.pack("<q", value)
    if type(value) is float:
        return b"\5" + struct.pack("<d", value)
    if type(value) is str:
        raw = value.encode("latin-1")
        return b"\6" + struct.pack("<Q", len(raw)) + raw
    if type(value) is tuple:
        return b"\10" + struct.pack("<3d", *value)
    if type(value) is list:
        return b"\7" + struct.pack("<Q", len(value)) + b"".join(encoded(v) for v in value)
    return b"\13" + struct.pack("<Q", len(value)) + b"".join(encoded(k)+encoded(v) for k,v in value.items())


class Reader24(unittest.TestCase):
    def test_all_original_results_errors_bits_and_order(self):
        previous = sys.get_int_max_str_digits()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.dxf"
            try:
                for row, raw, _ in CORPUS:
                    with self.subTest(row=row["name"]):
                        sys.set_int_max_str_digits(row["int_limit"])
                        path.write_bytes(raw)
                        before = native._native_decode_dxf_calls
                        if row["kind"] == "ok":
                            self.assertEqual(freeze(reader.read_dxf(path)), row["value"])
                            self.assertEqual(native._native_decode_dxf_calls, before+1)
                        else:
                            cls = {"FileFormatError": reader.FileFormatError, "ValueError": ValueError,
                                   "OverflowError": OverflowError, "KeyError": KeyError}[row["error_class"]]
                            with self.assertRaises(cls) as caught:
                                reader.read_dxf(path)
                            self.assertIs(type(caught.exception), cls)
                            self.assertEqual(str(caught.exception), row["message"])
                            self.assertEqual([freeze(v) for v in caught.exception.args], row["args"])
                            self.assertEqual(native._native_decode_dxf_calls, before)
            finally:
                sys.set_int_max_str_digits(previous)

    def test_real_binding_and_owned_lifetime(self):
        library, fn = native._binding()
        self.assertIsInstance(fn, C._CFuncPtr)
        self.assertEqual(C.sizeof(native.Diagnostic), 40 if C.sizeof(C.c_void_p)==8 else 32)
        row, raw, _ = next(c for c in CORPUS if c[0]["name"] == "line-bits")
        result = native.decode_dxf(raw)
        for _ in range(20):
            native.decode_dxf(b"")
        self.assertEqual(freeze(result), row["value"])

    def test_file_errors_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError) as caught:
                reader.read_dxf(Path(directory)/"absent")
            self.assertEqual(caught.exception.errno, errno.ENOENT)
            cls = PermissionError if sys.platform == "win32" else IsADirectoryError
            number = errno.EACCES if sys.platform == "win32" else errno.EISDIR
            with self.assertRaises(cls) as caught:
                reader.read_dxf(directory)
            self.assertIs(type(caught.exception), cls)
            self.assertEqual(caught.exception.errno, number)
        with self.assertRaises(TypeError):
            reader.read_dxf(None)

    def test_missing_and_invalid_library(self):
        paths = []
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory)/"invalid-library"
            invalid.write_bytes(b"not a shared library")
            paths += [invalid, Path(directory)/"absent"]
            input_path = Path(directory)/"input.dxf"
            input_path.write_bytes(b"")
            for path in paths:
                with self.subTest(path=path), patch.dict(os.environ, NARWHAL_PURE_CORE=str(path)):
                    native._cache = None
                    before = native._native_decode_dxf_calls
                    with self.assertRaisesRegex(native.NativeDXFReaderError, "missing or stale"):
                        native.decode_dxf(b"")
                    with self.assertRaisesRegex(native.NativeDXFReaderError, "missing or stale"):
                        reader.read_dxf(input_path)
                    self.assertEqual(native._native_decode_dxf_calls, before)
        native._cache = None

    def test_wrong_abis_and_missing_exports(self):
        class Function:
            def __init__(self, result): self.result=result
            def __call__(self): return self.result
        class Library:
            narwhal_pure_core_abi_version=Function(1)
            narwhal_pure_core_dxf_reader24_abi_version=Function(1)
        for core, abi in ((0,1),(1,0),(1,2)):
            lib=Library()
            lib.narwhal_pure_core_abi_version=Function(core)
            lib.narwhal_pure_core_dxf_reader24_abi_version=Function(abi)
            native._cache=None
            with patch.object(native.C,"CDLL",return_value=lib):
                with self.assertRaisesRegex(native.NativeDXFReaderError,"version mismatch"):
                    native.decode_dxf(b"")
        native._cache=None
        with patch.object(native.C,"CDLL",return_value=Library()):
            with self.assertRaisesRegex(native.NativeDXFReaderError,"missing or stale"):
                native.decode_dxf(b"")
        native._cache=None

    def test_typed_protocol_bounds_and_schema(self):
        bad = [b"", b"wrongver", b"wrongver"+EMPTY[8:], EMPTY+b"x", EMPTY[:15],
               native._MAGIC+b"\xff", native._MAGIC+b"\7"+struct.pack("<Q",2**64-1),
               native._MAGIC+b"\4\2"+struct.pack("<Q",0),
               native._MAGIC+b"\6"+struct.pack("<Q",2**64-1),
               native._MAGIC+encoded(None),
               native._MAGIC+b"\13"+struct.pack("<Q",2)+encoded("dup")+encoded(0)+encoded("dup")+encoded(1),
               native._MAGIC+(b"\7"+struct.pack("<Q",1))*18+encoded(None)]
        root={"entities":[],"layers":{},"layer_true_colors":{},"insunits":None,"skipped":{}}
        line={"type":"LINE","layer":"0","color":7,"p0":(0.,0.,0.),"p1":(1.,0.,0.)}
        for change in ({"type":"UNKNOWN"},{"color":True},{"p0":[0.,0.]},{"p1":None},{"closed":2}):
            root["entities"]=[dict(line,**change)]
            bad.append(native._MAGIC+encoded(root))
        root["entities"]=[{"type":"POLYLINE","layer":"0","color":7,"points":[],"closed":2}]
        bad.append(native._MAGIC+encoded(root))
        root["entities"]=[]
        root["layers"]={"0":True}
        bad.append(native._MAGIC+encoded(root))
        for payload in bad:
            with self.subTest(payload=payload[:32]):
                with self.assertRaises(native.NativeDXFReaderError): native._unpack(payload)

    def test_fixed_line_layout_guards_and_exact_order(self):
        row, _, payload = next(c for c in CORPUS if c[0]["name"] == "line-bits")
        self.assertIn(native._LINE_LEAD, payload)
        self.assertEqual(freeze(native._unpack(payload)), row["value"])
        bad = [payload.replace(native._LINE_COLOR, native._LINE_COLOR[:-1]+b"X", 1),
               payload.replace(native._LINE_P0, native._LINE_P0[:-1]+b"\5", 1),
               payload.replace(native._LINE_P1, native._LINE_P1[:-2]+b"X\10", 1)]
        root={"entities":[],"layers":{},"layer_true_colors":{},"insunits":None,"skipped":{}}
        for color, rgb in ((True, 7), (7, True)):
            root["entities"]=[{"type":"LINE","layer":"caf\xe9","color":color,"true_color":rgb,
                               "p0":(-0.,1.,2.),"p1":(3.,4.,5.)}]
            bad.append(native._MAGIC+encoded(root))
        for payload in bad:
            with self.subTest(payload=payload[-80:]):
                with self.assertRaises(native.NativeDXFReaderError):
                    native._unpack(payload)
        # Integer magnitude construction and true-color field insertion must
        # not get narrowed/reordered by the fixed LINE coordinate layout.
        for name in ("big-integers", "entity-color-sequence", "legacy-true_color_line.dxf"):
            row, _, payload = next(c for c in CORPUS if c[0]["name"] == name)
            self.assertEqual(freeze(native._unpack(payload)), row["value"])

    def test_host_native_status_spans_counts_and_retry(self):
        before=native._native_decode_dxf_calls
        def run(callback, pattern):
            with patch.object(native,"_binding",return_value=(None,callback)):
                with self.assertRaisesRegex(native.NativeDXFReaderError,pattern):native.decode_dxf(b"")
            self.assertEqual(native._native_decode_dxf_calls,before)
        def error(code, *, diagcode=None, start=0, length=0, detail=0, required=0, count=None):
            def callback(_data,_n,_limit,_out,_cap,written,diagnostic):
                d=C.cast(diagnostic,C.POINTER(native.Diagnostic)).contents
                d.code=code if diagcode is None else diagcode
                d.start,d.len,d.detail,d.required=start,length,detail,required
                if count is not None:C.cast(written,C.POINTER(C.c_size_t))[0]=count
                return code
            return callback
        for callback,pattern in [(error(-110,start=1),"span"),(error(-110,length=1),"span"),
                                 (error(-110,diagcode=-111),"inconsistent"),(error(-115,detail=1),"integer-limit"),
                                 (error(-999),"protocol"),(error(-3),"resource"),
                                 (error(-100,detail=1),"diagnostic"),
                                 (error(0,count=2**64-1),"count"),(error(0,count=8,detail=1),"diagnostic"),
                                 (error(1,required=1),"retry"),(error(1,required=native._MAX_OUTPUT+1),"retry"),
                                 (error(1,required=4097,count=0),"retry")]:
            run(callback,pattern)
        calls=[]
        def retry(_data,_n,_limit,out,cap,written,diagnostic):
            calls.append(cap)
            if len(calls)==1:
                d=C.cast(diagnostic,C.POINTER(native.Diagnostic)).contents
                d.code,d.required=1,4097
                return 1
            C.memmove(out,EMPTY,len(EMPTY))
            C.cast(written,C.POINTER(C.c_size_t))[0]=len(EMPTY)
            return 0
        with patch.object(native,"_binding",return_value=(None,retry)):
            self.assertEqual(native.decode_dxf(b" ")["entities"],[])
        self.assertEqual(calls,[4096,4097])
        before=native._native_decode_dxf_calls
        run(error(1,required=4097),"retry")

    def test_explicit_resource_admission_before_binding(self):
        with patch.object(native,"_MAX_INPUT",1),patch.object(native,"_binding",side_effect=AssertionError("bound")):
            with self.assertRaisesRegex(native.NativeDXFReaderError,"input byte limit"):
                native.decode_dxf(b"xx")
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/"large.dxf";path.write_bytes(b"xx")
                with self.assertRaisesRegex(native.NativeDXFReaderError,"input byte limit"):
                    reader.read_dxf(path)


if __name__ == "__main__":
    unittest.main()
