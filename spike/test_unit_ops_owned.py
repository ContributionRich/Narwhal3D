"""Owned differential suite. Set NARWHAL_PURE_CORE to a registered/scratch artifact.
Run with bundled Python (Unicode 15.1); no builds or activation performed here.
"""
import ctypes as C
import importlib.util
import math
import os
from pathlib import Path
import random
import struct
import sys
import types
import unicodedata
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
ref = load("units_reference", ROOT / "spike/units_reference.py")
native = load("unit_ops_owned", ROOT / "narwhal3d/native_unit_ops.py")
NAMES = tuple(ref.list_units())

def outcome(fn, *args):
    try:
        value = fn(*args)
        if isinstance(value, float):
            return ("float", "nan" if math.isnan(value) else struct.pack("!d", value))
        return ("ok", value)
    except Exception as exc:
        return (type(exc).__name__, str(exc))

def parse(text):
    return native.parse_unit(text, NAMES)

class UnitOps(unittest.TestCase):
    def test_reference_is_independent_under_package_imports(self):
        for package_name in ["unit_oracle_probe", "narwhal3d"]:
            package = types.ModuleType(package_name)
            package.__path__ = []
            trap = types.ModuleType(package_name + ".native_pure")
            trap.unit_table = mock.Mock(side_effect=AssertionError("oracle called native table"))
            package.native_pure = trap
            with mock.patch.dict(sys.modules, {package_name: package, trap.__name__: trap}), mock.patch.object(C, "CDLL", side_effect=AssertionError("oracle loaded CDLL")) as cdll:
                oracle = load(package_name + ".units_reference_owned", ROOT / "spike/units_reference.py")
                self.assertEqual(oracle.UNIT_SCALE_SOURCE, "python")
                self.assertEqual(tuple(oracle.UNIT_SCALE_MM.items()), tuple(ref._PYTHON_UNIT_SCALE_MM.items()))
                self.assertIsNot(oracle.UNIT_SCALE_MM, oracle._PYTHON_UNIT_SCALE_MM)
                scales, source = oracle._unit_scales()
                self.assertEqual((scales, source), (ref._PYTHON_UNIT_SCALE_MM, "python"))
                self.assertIsNot(scales, oracle.UNIT_SCALE_MM)
                self.assertEqual(outcome(oracle.convert, 12.5, "Millimeters", "Inches"), outcome(ref.convert, 12.5, "Millimeters", "Inches"))
                trap.unit_table.assert_not_called()
                cdll.assert_not_called()

    def same_parse(self, text):
        self.assertEqual(outcome(parse, text), outcome(ref.parse_unit, text), repr(text))

    def test_parse_seeded(self):
        cases = [None, False, 0, 42, [], "", " ", "  ", "m", "mi", "mil", "in", "p", "n", "a", "custom", "UNSET", "none", "  m i  ", "\tmm\n", "mm\x00", "\ud800", " \udfff ", "Km", "Kilometers", "İnches", "ſ", "ｍｍ"]
        cases += list(ref.UNIT_ABBREVIATIONS)
        for name in NAMES:
            cases += [name, name.upper(), name.rstrip("s"), " ".join(name), name + "s"]
            cases += [name[:i] for i in range(len(name)+1)]
        rng = random.Random(160016)
        alphabet = " abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ\t\nKİſé\u00a0\x1c\ud800"
        cases += ["".join(rng.choices(alphabet, k=rng.randrange(35))) for _ in range(4000)]
        for text in cases:
            self.same_parse(text)

    def test_unicode_gate_exhaustive(self):
        self.assertEqual(unicodedata.unidata_version, "15.1.0")
        ascii_lowers, multi = [], []
        spaces = []
        for cp in range(0x110000):
            ch = chr(cp)
            low = ch.lower()
            if cp > 127 and low.isascii():
                ascii_lowers.append((cp, low))
            if len(low) > 1:
                multi.append((cp, low))
            if ch.isspace():
                spaces.append(ch)
        self.assertEqual(ascii_lowers, [(0x212a, "k")])
        self.assertEqual(multi, [(0x130, "i\u0307")])
        for ch in spaces:
            self.same_parse(ch + "Millimeters" + ch)
            self.same_parse("m" + ch + "m")
        # Every non-ASCII codepoint exercises native matching, not just the probe.
        for cp in range(128, 0x110000):
            self.same_parse(chr(cp) + "m")

    def test_arithmetic_bits_seeded(self):
        values = [0., -0., 1., -1., math.inf, -math.inf, math.nan, 1e308, 5e-324]
        rng = random.Random(16)
        values += [struct.unpack("!d", rng.randbytes(8))[0] for _ in range(500)]
        def expected(v, a, b):
            return v * a / b
        factors = list(ref.UNIT_SCALE_MM.values()) + [0., -0., math.inf, math.nan, 5e-324, 1e308]
        for v in values:
            for a, b in [(rng.choice(factors), rng.choice(factors)) for _ in range(12)]:
                self.assertEqual(outcome(native.scale, v, a, b), outcome(expected, v, a, b))
        # Reassociation must overflow here rather than become 1e308.
        self.assertEqual(native.scale(1e308, 2., 2.), math.inf)
        for zero in [0., -0.]:
            self.assertEqual(outcome(native.scale, 1., 1., zero), ("ZeroDivisionError", "float division by zero"))

    def test_angles(self):
        names = ["degrees", "radians", "gradians", " DEGREES ", "\x1cRadians\u00a0", "degree", "d", None, "İ", "\ud800"]
        for a in names:
            for b in names:
                for value in [0., -0., 180., math.inf, math.nan, "bad"]:
                    self.assertEqual(outcome(native.convert_angle, value, a, b), outcome(ref.convert_angle, value, a, b))

    def test_bounds_and_abi(self):
        self.same_parse("x" * 4096)
        self.same_parse(" " * 4096)
        self.assertEqual(outcome(parse, "x"*4097), ("ValueError", "unit input exceeds 4096 codepoints (OURS)"))
        binding = native._native()
        i, mask, size = C.c_uint32(99), C.c_uint32(99), C.c_size_t(99)
        raw = C.create_string_buffer(16384)
        self.assertEqual(binding.parse(b"x"*4097, 4097, C.byref(i), C.byref(mask), raw, len(raw), C.byref(size)), 4)
        self.assertEqual((i.value, mask.value, size.value), (99,99,99))
        self.assertEqual(binding.parse(b"mm", 2, C.byref(i), C.byref(mask), raw, 1, C.byref(size)), 5)
        self.assertEqual(binding.parse(b"\xff", 1, C.byref(i), C.byref(mask), raw, len(raw), C.byref(size)), 5)

    def test_length_host_boundary(self):
        # Integration contract: host admission stays the unchanged oracle's;
        # only its final arithmetic moves to native. No dynamic-object API port.
        def convert(value, a, b, custom):
            sf = ref._resolve(a, custom)
            st = ref._resolve(b, custom)
            return native.scale(float(value), sf, st)
        for a in NAMES + ("None", "Unset", "bad", None):
            for b in NAMES + ("None", "Unset", "bad", None):
                for custom in [None, 0., -0., 0.125, "bad", math.inf]:
                    for value in [1.25, "bad"]:
                        args = (value, a, b, custom)
                        self.assertEqual(outcome(convert, *args), outcome(ref.convert, *args))
        for text in ["p", "Km", " mi ", " ", "invalid"]:
            self.assertEqual(outcome(parse, text), outcome(ref.parse_unit, text))

    def test_loader_and_response_fail_closed(self):
        for library in [object(), mock.Mock(narwhal_pure_core_abi_version=mock.Mock(return_value=99))]:
            with mock.patch.object(native.C, "CDLL", return_value=library):
                with self.assertRaises(RuntimeError):
                    native._bindings(("env", "fake-" + str(id(library))))
        # Version present/correct but missing a required operation export.
        class MissingExports:
            narwhal_pure_core_abi_version = mock.Mock(return_value=1)
        with mock.patch.object(native.C,"CDLL",return_value=MissingExports()):
            with self.assertRaises(RuntimeError):
                native._bindings(("env","missing-exports"))
        for status in [-1, 7, 1000]:
            binding = native._Binding(("env", "fake"), object(), lambda *a: status, lambda *a: status, lambda *a: status)
            with mock.patch.object(native, "_native", return_value=binding):
                for fn, args in [(parse, ("mm",)), (native.angle_factor, ("degrees",)), (native.scale, (1.,1.,1.))]:
                    with self.assertRaises(RuntimeError):
                        fn(*args)
        # A warmed exact signature does not revisit paths or reload the CDLL.
        binding = native._native()
        with mock.patch.object(native, "Path", side_effect=AssertionError("warm Path")), mock.patch.object(native.C, "CDLL", side_effect=AssertionError("warm load")):
            self.assertIs(native._native(), binding)

    def test_parse_response_metadata_faults(self):
        def fake(rc, ident, bits, payload, size=None):
            def invoke(text, length, out_id, out_mask, raw, cap, out_size):
                C.cast(out_id, C.POINTER(C.c_uint32))[0] = ident
                C.cast(out_mask, C.POINTER(C.c_uint32))[0] = bits
                C.cast(out_size, C.POINTER(C.c_size_t))[0] = len(payload) if size is None else size
                C.memmove(raw, payload, len(payload))
                return rc
            return native._Binding(("env", "fake"), object(), invoke, None, None)
        faults = [(0,25,0,b"mm",None), (0,0,1,b"mm",None),
                  (1,1,0,b"",None), (1,0,1,b"",None), (1,0,0,b"m",None),
                  (2,1,0,b"mm",None), (2,0,1,b"mm",None),
                  (3,1,3,b"mm",None), (3,0,1,b"mm",None), (3,0,1<<25,b"mm",None),
                  (0,3,0,b"mm",16385), (0,3,0,b"mm",3),
                  (0,3,0,b"\xff",None), (0,3,0,b"\xc0\x80",None),
                  (0,3,0,b"m\x00",None), (0,3,0,b" m",None),
                  (0,3,0,b"",None), (2,0,0,b"m",None),
                  (True,3,0,b"mm",None)]
        for values in faults:
            with self.subTest(values=values), mock.patch.object(native, "_native", return_value=fake(*values)):
                with self.assertRaises(RuntimeError):
                    parse(" " if values[0] == 1 else "mm")

    def test_scalar_response_faults_and_invocation_errors(self):
        def scalar(rc, value):
            def invoke(*args):
                if value is not None:
                    C.cast(args[-1], C.POINTER(C.c_double))[0] = value
                return rc
            return invoke
        for rc, value in [(0,None),(0,0.),(0,-1.),(0,math.inf),(0,math.nan),(2,1.),(6,None),(True,1.)]:
            binding = native._Binding(("env","fake"), object(),None,scalar(rc,value),None)
            with self.subTest(rc=rc,value=value), mock.patch.object(native,"_native",return_value=binding):
                with self.assertRaises(RuntimeError):
                    native.angle_factor("degrees")
        for rc,value,st in [(6,None,1.),(6,0.,0.),(2,None,1.),(0,1.,0.),(True,1.,1.)]:
            binding = native._Binding(("env","fake"), object(),None,None,scalar(rc,value))
            with mock.patch.object(native,"_native",return_value=binding):
                with self.assertRaises(RuntimeError):
                    native.scale(1.,1.,st)
        for error in [C.ArgumentError("bad"), OSError("bad"), ValueError("bad")]:
            fn = mock.Mock(side_effect=error)
            binding = native._Binding(("env","fake"),object(),fn,fn,fn)
            with mock.patch.object(native,"_native",return_value=binding):
                for method,args in [(parse,("mm",)),(native.angle_factor,("degrees",)),(native.scale,(1.,1.,1.))]:
                    with self.assertRaisesRegex(RuntimeError,"native invocation failed"):
                        method(*args)

    def test_unicode_gate_in_production(self):
        # Must also reject on a warmed binding; no native calls on drift.
        native._native()
        for version in ["14.0.0", "15.0.0", "16.0.0"]:
            with mock.patch.object(native.unicodedata,"unidata_version",version):
                for fn,args in [(parse,("mm",)),(native.angle_factor,("degrees",)),(native.scale,(1.,1.,1.))]:
                    with self.assertRaisesRegex(RuntimeError,"Unicode 15.1.0 required"):
                        fn(*args)

    def test_binding_cache_no_fallback(self):
        b = native._native()
        self.assertIs(b, native._native())
        self.assertIsInstance(b, tuple)
        self.assertIsNotNone(b.library)
        self.assertEqual(b.signature, native._signature())
        for value in ["", "/missing/unit-ops-native.so"]:
            with mock.patch.dict(os.environ, {"NARWHAL_PURE_CORE":value}):
                with self.assertRaisesRegex(RuntimeError,"no Python fallback"):
                    parse("mm")
        self.assertIs(native._native(), b)
        # Warm path must precede filesystem work, lock, and CDLL loading.
        class ForbiddenLock:
            def __enter__(self):
                raise AssertionError("warm lock")
            def __exit__(self, *args):
                return False
        with mock.patch.object(native,"_LOCK",ForbiddenLock()), mock.patch.object(native,"Path",side_effect=AssertionError("warm Path")), mock.patch.object(native.C,"CDLL",side_effect=AssertionError("warm CDLL")):
            self.assertIs(native._native(),b)
        # Explicit empty env may not resolve to the packaged artifact.
        with mock.patch.object(native.C,"CDLL",side_effect=AssertionError("empty env loaded")):
            with self.assertRaises(RuntimeError):
                native._bindings(("env",""))
        # Valid packaged selection is a different tagged signature; one immutable
        # record replaces the previous one rather than retaining an LRU of paths.
        selected = b.signature[1] if b.signature[0] == "env" else native._PACKAGED
        native._bindings(("env",selected))
        with mock.patch.object(native,"_PACKAGED",selected):
            packaged = native._bindings(("packaged",None))
            self.assertEqual(packaged.signature,("packaged",None))
            self.assertIs(native._BINDING,packaged)
            self.assertIsNot(packaged,b)
        self.assertEqual(native._native().signature,b.signature)

def load_live():
    # No narwhal3d package import: that entry point may require Blender. This
    # exercises units.py's production direct-file adapter loading itself.
    name = "unit_ops_live_public"
    try:
        return load(name, ROOT / "narwhal3d/units.py")
    except BaseException:
        sys.modules.pop(name, None)
        raise


class LiveUnits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = load_live()

    def test_live_public_parse_and_errors(self):
        self.assertEqual(self.live.UNIT_SCALE_SOURCE, "rust")
        self.assertEqual(self.live.UNIT_MEMBERS, ref.UNIT_MEMBERS)
        self.assertEqual(self.live.list_units(), ref.list_units())
        self.assertEqual(self.live.list_units(False), ref.list_units(False))
        self.assertEqual(self.live.ANGLE_FACTORS, ref.ANGLE_FACTORS)
        cases = [None, False, 0, 42, [], "", " ", "p", "n", "a", "unset", "none",
                 "Nautical mile", "Km", "İnches", "ｍｍ", "m\x00m", "mm\nmm",
                 "\ud800", " \udfff ", "m\u00a0m"]
        cases += list(ref.UNIT_ABBREVIATIONS)
        for name in ref.list_units():
            cases += [name, name.upper(), name.rstrip("s"), " ".join(name), name+"s"]
            cases += [name[:i] for i in range(len(name)+1)]
        rng = random.Random(160017)
        alphabet = " abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZKİé\t\x1c\ud800"
        cases += ["".join(rng.choices(alphabet, k=rng.randrange(30))) for _ in range(1000)]
        for text in cases:
            with self.subTest(text=repr(text)):
                self.assertEqual(outcome(self.live.parse_unit,text),outcome(ref.parse_unit,text))
        # All CPython whitespace still crosses the LIVE entry point.
        for cp in range(0x110000):
            ch = chr(cp)
            if ch.isspace():
                for text in [ch+"mm"+ch,"m"+ch+"m"]:
                    self.assertEqual(outcome(self.live.parse_unit,text),outcome(ref.parse_unit,text))
        class Typed:
            def __init__(self, truth, text): self.truth, self.text = truth, text
            def __bool__(self): return self.truth
            def __str__(self): return self.text
        for value in [Typed(True," Km "),Typed(False,"mm"),Typed(True,"ambiguous")]:
            self.assertEqual(outcome(self.live.parse_unit,value),outcome(ref.parse_unit,value))

    def test_live_parse_coercion_order_and_errors(self):
        class Probe:
            def __init__(self,events,truth,text,fail):
                self.events,self.truth,self.text,self.fail=events,truth,text,fail
            def __bool__(self):
                self.events.append("bool")
                if self.fail == "bool": raise ValueError("bool probe")
                return self.truth
            def __str__(self):
                self.events.append("str")
                if self.fail == "str": raise TypeError("str probe")
                return self.text
        def run(module,truth,text,fail):
            events=[]
            result=outcome(module.parse_unit,Probe(events,truth,text,fail))
            return result,events
        for truth in [False,True]:
            for text in ["mm","p","bad","Km",None]:
                for fail in [None,"bool","str"]:
                    self.assertEqual(run(self.live,truth,text,fail),run(ref,truth,text,fail))
        self.assertEqual(run(self.live,False,"mm",None)[1],["bool"])
        self.assertEqual(run(self.live,True,"mm",None)[1],["bool","str"])

    def test_live_convert_all_576_fixed_pairs(self):
        fixed = tuple(ref.UNIT_SCALE_MM)
        self.assertEqual(len(fixed)**2,576)
        values = [0.,-0.,1.,-12.375,1e308,5e-324,math.inf,math.nan,"2.5"]
        for a in fixed:
            self.assertEqual(outcome(self.live.unit_scale_mm,a),outcome(ref.unit_scale_mm,a))
            for b in fixed:
                for value in values:
                    self.assertEqual(outcome(self.live.convert,value,a,b),outcome(ref.convert,value,a,b),(value,a,b))
                self.assertEqual(outcome(self.live.scale_factor,a,b),outcome(ref.scale_factor,a,b),(a,b))

    def test_live_convert_custom_invalid(self):
        names = ["Millimeters","Inches","CustomUnits","None","Unset","bad",None,[],{}]
        for a in names:
            for b in names:
                for custom in [None,0.,-0.,-0.25,0.125,"2.5","bad",math.inf,math.nan]:
                    for value in [1.25,"bad",None,[]]:
                        self.assertEqual(outcome(self.live.convert,value,a,b,custom),outcome(ref.convert,value,a,b,custom))
            for custom in [None,0.,"bad",0.125]:
                self.assertEqual(outcome(self.live.unit_scale_mm,a,custom),outcome(ref.unit_scale_mm,a,custom))

    def test_live_convert_error_evaluation_order(self):
        class FloatProbe:
            def __init__(self,events,label,value,fail=False):
                self.events,self.label,self.value,self.fail = events,label,value,fail
            def __float__(self):
                self.events.append("float:"+self.label)
                if self.fail: raise ValueError("coercion:"+self.label)
                return self.value
            def __repr__(self): return "<FloatProbe>"
        def run(module,a,b,value_fail,custom_fail):
            events=[]
            value=FloatProbe(events,"value",1.25,value_fail)
            custom=FloatProbe(events,"custom",0.125,custom_fail)
            resolve=module._resolve
            def observed(name,custom_value=None):
                events.append("resolve:"+name)
                return resolve(name,custom_value)
            with mock.patch.object(module,"_resolve",side_effect=observed):
                result=outcome(module.convert,value,a,b,custom)
            return result,events
        for a,b in [("Millimeters","Inches"),("CustomUnits","Millimeters"),
                    ("Millimeters","CustomUnits"),("CustomUnits","CustomUnits"),
                    ("bad","CustomUnits"),("CustomUnits","bad"),("None","Unset")]:
            for vf in [False,True]:
                for cf in [False,True]:
                    self.assertEqual(run(self.live,a,b,vf,cf),run(ref,a,b,vf,cf))
        self.assertEqual(run(self.live,"CustomUnits","CustomUnits",False,False)[1],
                         ["resolve:CustomUnits","float:custom","resolve:CustomUnits","float:custom","float:value"])

    def test_live_angles_nine_pairs_and_host_coercions(self):
        for a in ref.ANGLE_UNITS:
            for b in ref.ANGLE_UNITS:
                for value in [0.,-0.,180.,1e308,5e-324,math.inf,math.nan,"90"]:
                    self.assertEqual(outcome(self.live.convert_angle,value,a,b),outcome(ref.convert_angle,value,a,b))
        names=[" DEGREES ","\x1cRadians\u00a0","gradians","degree","d",None,[],"İ","\ud800"]
        for a in names:
            for b in names:
                for value in [1.,"bad",None]:
                    self.assertEqual(outcome(self.live.convert_angle,value,a,b),outcome(ref.convert_angle,value,a,b))
        class Probe:
            def __init__(self,events,label,text,fail=False):
                self.events,self.label,self.text,self.fail=events,label,text,fail
            def __float__(self):
                self.events.append("float:"+self.label)
                if self.fail: raise ValueError("float:"+self.label)
                return 90.
            def __str__(self):
                self.events.append("str:"+self.label)
                if self.fail: raise ValueError("str:"+self.label)
                return self.text
            def __repr__(self):
                self.events.append("repr:"+self.label)
                return "<Probe:"+self.label+">"
        def run(module,text_a,text_b,fail_v,fail_a,fail_b):
            events=[]
            args=[Probe(events,"value","unused",fail_v),Probe(events,"from",text_a,fail_a),Probe(events,"to",text_b,fail_b)]
            return outcome(module.convert_angle,*args),events
        for a,b in [("DEGREES","radians"),("bad","radians"),("degrees","bad")]:
            for fv,fa,fb in [(False,False,False),(True,False,False),(False,True,False),(False,False,True)]:
                self.assertEqual(run(self.live,a,b,fv,fa,fb),run(ref,a,b,fv,fa,fb))
        self.assertEqual(run(self.live,"degrees","radians",False,False,False)[1],
                         ["float:value","str:from","str:to"])

    def test_live_delegation_and_no_replay(self):
        adapter=self.live._unit_adapter("native_unit_ops")
        for method,args,adapter_name,expected_args in [
            (self.live.parse_unit,("not a Python-accepted unit",),"parse_unit",("not a Python-accepted unit",tuple(ref.list_units()))),
            (self.live.convert,(2.,"Millimeters","Inches"),"scale",(2.,ref.UNIT_SCALE_MM["Millimeters"],ref.UNIT_SCALE_MM["Inches"])),
            (self.live.convert_angle,(90.,"degrees","radians"),"convert_angle",(90.,"degrees","radians")),
        ]:
            sentinel=object()
            with mock.patch.object(adapter,adapter_name,return_value=sentinel) as called:
                self.assertIs(method(*args),sentinel)
                called.assert_called_once_with(*expected_args)
            with mock.patch.object(adapter,adapter_name,side_effect=RuntimeError("native-only-probe")) as called:
                with self.assertRaisesRegex(RuntimeError,"native-only-probe"):
                    method(*args)
                called.assert_called_once()
        binding=adapter._native()
        counted=binding._replace(parse=mock.Mock(wraps=binding.parse),
                                 angle=mock.Mock(wraps=binding.angle),scale=mock.Mock(wraps=binding.scale))
        with mock.patch.object(adapter,"_native",return_value=counted):
            self.live.parse_unit("mm")
            self.live.convert(2.,"Millimeters","Inches")
            self.live.convert_angle(90.,"degrees","radians")
        self.assertEqual((counted.parse.call_count,counted.angle.call_count,counted.scale.call_count),(1,2,2))

    def test_live_missing_native_fails_import_and_operations(self):
        adapter=self.live._unit_adapter("native_unit_ops")
        operations=[(self.live.parse_unit,("mm",)),(self.live.convert,(1.,"Millimeters","Inches")),
                    (self.live.convert_angle,(90.,"degrees","radians"))]
        for path in ["","/missing/live-unit-ops-library.so"]:
            with mock.patch.dict(os.environ,{"NARWHAL_PURE_CORE":path}):
                # Native table admission may reject this import first; neither
                # adapter is permitted to replay a Python implementation.
                with self.assertRaises(RuntimeError):
                    load_live()
                for method,args in operations:
                    with self.assertRaises(RuntimeError): method(*args)
        class MissingOps:
            narwhal_pure_core_abi_version=mock.Mock(return_value=1)
        with mock.patch.dict(os.environ,{"NARWHAL_PURE_CORE":"/fake/has-no-unit-ops.so"}), mock.patch.object(adapter.C,"CDLL",return_value=MissingOps()):
            # Cold import can fail at the table export first (explicitly
            # admitted); operation probes below isolate the unit-ops exports.
            with self.assertRaises(RuntimeError):
                load_live()
            # Existing live module already has its table: this isolates missing
            # unit-operations exports rather than relying on table failure.
            for method,args in operations:
                with self.assertRaisesRegex(RuntimeError,"library/version/export unavailable"):
                    method(*args)
        # Test failures must not poison the original binding or public host.
        self.assertEqual(self.live.parse_unit("mm"),"Millimeters")


if __name__ == "__main__":
    unittest.main()
