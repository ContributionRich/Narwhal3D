"""Coordinate regression: required owned ABI, exact reference parity, no replay.

All historical examples/rejections are retained as captured fixed outputs.
--module PATH qualifies an inactive replacement; default uses live public coords.
--library PATH or NARWHAL_PURE_CORE selects the existing core; no test-time builds.
The original report-only benchmark is archived privately, not timed via fixtures.
"""
import argparse
import ctypes
import importlib.util
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'narwhal3d/coords.py'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Existing rejected inputs retained; the frozen independent oracle pins errors.
COORDS_REJECTED = [
    "5",            # a bare non-zero number is a distance constraint
    "abc,1",        # garbage component
    "1,2,3,4",      # more than three coordinates
    "w",            # a prefix with no coordinates
    "",             # empty
    "1, 2",         # a space
    ".x",           # a point filter
    ".wxy",
    "<45",          # a bare angle constraint
    "17<45<20<30",  # too many '<'
]



class NativeCoordinates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.coords = load('narwhal_native_test_coords', MODULE)
        cls.reference = load('narwhal_native_coords_reference', ROOT/'spike/coords_reference.py')
        cls.native = cls.coords._adapter()
        cls.coords.set_model_unit('Millimeters')
        cls.valid_coords=cls.reference.examples()

    def assertNativeCalls(self, before, count):
        self.assertEqual(self.native._native_coords_calls-before,count)

    def assertExact(self, actual, expected, text):
        self.assertEqual(actual[1:], expected[1:], (text,actual,expected))
        if expected[0] is None:
            self.assertIsNone(actual[0])
        else:
            aa = actual[0] if isinstance(actual[0],tuple) else (actual[0],)
            bb = expected[0] if isinstance(expected[0],tuple) else (expected[0],)
            self.assertEqual(len(aa),len(bb))
            for a,b in zip(aa,bb):
                self.assertEqual(struct.pack('!d',a),struct.pack('!d',b),(text,a,b))

    def test_required_binding_and_canonical_adapter(self):
        self.assertTrue(callable(self.native._required_binding()))
        self.assertIs(self.coords._adapter(),self.native)
        second=load('narwhal_second_coords',MODULE)
        self.assertIs(second._adapter(),self.native)
        self.assertIs(sys.modules['narwhal3d_native_coords'],self.native)
        # Package-relative resolution must take priority over file-path loading.
        # A synthetic package exercises this without importing Blender __init__.
        from types import ModuleType
        package=ModuleType('_rust13_coords_package')
        package.__path__=[]
        package.native_coords=self.native
        with patch.dict(sys.modules,{'_rust13_coords_package':package}):
            spec=importlib.util.spec_from_file_location('_rust13_coords_package.coords',MODULE)
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertIs(module._adapter(),self.native)

    def test_all_old_valid_values_flags_and_native_route(self):
        before=self.native._native_coords_calls
        for text,want,world,relative in self.valid_coords:
            actual=self.coords.parse(text)
            self.assertEqual(actual[1:],(world,relative,None),(text,actual))
            # Historical expected examples retain their existing 1e-9 guard.
            # The independent oracle below additionally requires exact bits.
            for a,b in zip(actual[0],want):
                self.assertLessEqual(abs(a-b),1e-9*max(1.0,abs(b)),(text,a,b))
            self.assertExact(actual,self.reference.expected('parse',text,'Millimeters'),text)
        self.assertNativeCalls(before,len(self.valid_coords))

    def test_all_old_rejections_are_exact_and_native(self):
        before=self.native._native_coords_calls
        for text in COORDS_REJECTED:
            expected=self.reference.expected('parse',text,'Millimeters')
            self.assertIsNone(expected[0])
            self.assertTrue(expected[-1])
            self.assertExact(self.coords.parse(text),expected,text)
        self.assertNativeCalls(before,len(COORDS_REJECTED))

    def test_documented_error_contract_pins(self):
        cases=[
            ('5', "a bare number is a distance constraint in the reference CAD application (it locks the next pick's distance from the last point), not a coordinate; type x,y or x,y,z for a point"),
            ('.x', "point filters like '.x' are not supported yet"),
            ('w', "'w' has a prefix but no coordinates"),
            ('1, 2','a coordinate may not contain spaces'),
            ('1,2,3,4',"'1,2,3,4' has more than three coordinates")]
        before=self.native._native_coords_calls
        for text,want in cases:
            self.assertEqual(self.coords.parse(text),(None,False,False,want))
        self.assertNativeCalls(before,len(cases))

    def test_prefix_order_and_all_relative_world_forms(self):
        before=self.native._native_coords_calls
        for prefix,world,relative in [('',False,False),('w',True,False),('r',False,True),
                ('@',False,True),('rw',True,True),('@w',True,True),
                ('RW',True,True),('@W',True,True)]:
            for body,point in [('0,5',(0.0,5.0,0.0)),('1,2',(1.0,2.0,0.0)),
                               ('0',(0.0,0.0,0.0))]:
                self.assertEqual(self.coords.parse(prefix+body),(point,world,relative,None))
        self.assertNativeCalls(before,24)
        for text in ['wr1,2','rr1,2','r@1,2','@rw1,2']:
            self.assertExact(self.coords.parse(text),self.reference.expected('parse',text,'Millimeters'),text)

    def test_scalar_angle_success_and_rejection_always_native(self):
        before=self.native._native_coords_calls
        cases=[('parse_scalar','1_2'),('parse_scalar','1-3/4'),('parse_scalar','1cm'),
               ('parse_scalar','bad'),('parse_scalar','sqrt(-1)'),('parse_scalar',''),
               ('parse_angle','30d'),('parse_angle',"0d20'50\""),
               ('parse_angle','bad'),('parse_angle','pi'),('parse_angle','')]
        for name,text in cases:
            self.assertExact(getattr(self.coords,name)(text),self.reference.expected(name,text,'Millimeters'),text)
        self.assertNativeCalls(before,len(cases))

    def test_ours_finite_manufacturing_safety(self):
        # OURS intentional safety policy: float-first legacy NaN/infinity
        # acceptance must never leak an invalid coordinate or scalar.
        before=self.native._native_coords_calls
        for text in ['nan','NaN','inf','-Infinity','1e999','-1e999']:
            self.assertEqual(self.coords.parse_scalar(text),(None,'coordinate number outside finite range'))
            self.assertEqual(self.coords.parse(text+',2'),(None,False,False,'coordinate number outside finite range'))
        self.assertEqual(self.coords.parse_angle('1e999'),(None,'coordinate number outside finite range'))
        self.assertNativeCalls(before,13)

    def assertRequiredFailures(self, message):
        before=self.native._native_coords_calls
        for name,text in [('parse','1,2'),('parse','5/16,1-3/4'),('parse','5'),
                ('parse',''),('parse_scalar','1'),('parse_scalar','bad'),
                ('parse_angle','30'),('parse_angle','bad')]:
            with self.assertRaisesRegex(RuntimeError,message):
                getattr(self.coords,name)(text)
        self.assertNativeCalls(before,0)

    def test_missing_library_never_replays_even_simple_floats(self):
        with tempfile.TemporaryDirectory(prefix='coords-missing-') as temp:
            with patch.dict(os.environ,{'NARWHAL_PURE_CORE':str(Path(temp)/'absent.so')}):
                self.assertRequiredFailures('unavailable')

    def test_old_library_missing_symbol_and_stale_identity_never_replay(self):
        class Identity:
            def __init__(self,value): self.value=value
            def __call__(self): return self.value
        class Old:
            narwhal_pure_core_abi_version=Identity(1)
        class Stale:
            narwhal_pure_core_abi_version=Identity(1)
            narwhal_pure_core_coords_owned_abi1=Identity(9)
        for library,message in [(Old(),'unavailable'),(Stale(),'Stale')]:
            with patch.dict(os.environ,{'NARWHAL_PURE_CORE':'/fake/old-coords.so'}):
                with patch.object(self.native.ctypes,'CDLL',return_value=library):
                    self.assertRequiredFailures(message)

    def test_malformed_native_output_and_failed_calls_never_replay(self):
        def malformed(*args): return 0  # untouched sentinel code is invalid
        def failed(*args): raise OSError('native call broke')
        for binding,message in [(malformed,'Invalid Rust coordinate reply'),(failed,'call failed')]:
            with patch.object(self.native,'_required_binding',return_value=binding):
                self.assertRequiredFailures(message)

    def test_every_unused_nested_payload_byte_is_validated(self):
        # Success has no nested error payload: even reserved/padding bytes
        # must be zero. Exercise every byte, not just error identity fields.
        def corrupted(offset):
            def call(*args):
                out=ctypes.cast(args[-2],ctypes.POINTER(self.native.CoordsReply)).contents
                out.code=0
                out.values[0]=1.0
                ctypes.memmove(ctypes.addressof(out)+offset,b'\x01',1)
                return 0
            return call
        before=self.native._native_coords_calls
        for offset in range(self.native.CoordsReply.calc.offset,
                            ctypes.sizeof(self.native.CoordsReply)):
            for name,text in [('parse','1,0'),('parse_scalar','1'),('parse_angle','1')]:
                with self.subTest(offset=offset,operation=name):
                    with patch.object(self.native,'_required_binding',return_value=corrupted(offset)):
                        with self.assertRaisesRegex(RuntimeError,'Invalid Rust coordinate reply'):
                            getattr(self.coords,name)(text)
        self.assertNativeCalls(before,0)

    def test_host_cplane_resolution_and_precision_contract(self):
        cplane=load('native_coords_cplane',ROOT/'narwhal3d/cplane.py')
        for plane_name in ['WORLD_TOP','WORLD_FRONT','WORLD_RIGHT']:
            plane=getattr(cplane,plane_name)
            for text in ['1,2','w1,2','r1,2','rw1,2','@w1,2','bad']:
                self.assertExact(self.coords.resolve(text,plane,(4,5,6)),
                                 self.reference.expected('resolve',text,plane_name,(4,5,6)),text)
            point=(1/3,math.pi,-1e-200)
            self.assertExact(self.coords.resolve(self.coords.format_world(point),plane),
                             (point,None),'format_world')
        self.assertEqual(self.coords.format_world((1/3,2,3),precision=3),'w0.333,2.000,3.000')


def main():
    global MODULE
    parser=argparse.ArgumentParser()
    parser.add_argument('--module',type=Path)
    parser.add_argument('--library',type=Path,default=os.environ.get('NARWHAL_PURE_CORE'))
    args=parser.parse_args()
    if args.module: MODULE=args.module.resolve()
    if args.library: os.environ['NARWHAL_PURE_CORE']=str(args.library.resolve())
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeCoordinates))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':
    raise SystemExit(main())
