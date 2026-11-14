"""Coordinate host boundaries, fixed original outputs, required native ABI.

Computational and split corpora run in Rust coords_golden. Original compiler,
mutation qualification tooling and unchanged benchmark are private only.
Use bundled Python 3.13 with --library or the configured/packaged current core.
"""
import argparse
import ast
import ctypes
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import math
import os
from pathlib import Path

import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / 'narwhal3d/coords.py'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CoordinatesOwned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = load('rust13_coords_reference', ROOT / 'spike/coords_reference.py')
        cls.candidate = load('rust13_coords_candidate', CANDIDATE)
        cls.native = cls.candidate._adapter()

    def test_fixed_full_error_presentations(self):
        before=self.native._native_coords_calls
        original_model=self.candidate.MODEL_UNIT
        count=0
        try:
            for r in self.reference.rows('presentation'):
                source=self.reference.text(r[3])
                model=ast.literal_eval(self.reference.text(r[4]))
                mode=int(r[2])
                reply=self.native.CoordsReply(code=int(r[8]),flags=int(r[9]),
                    start=int(r[11]),end=int(r[12]))
                reply.values[:]=list(map(self.reference.number,r[10].split(',')))
                c=r[13].split(','); b=r[14].split(',')
                reply.calc=self.native.CalcReply(int(c[0]),int(c[1]),self.reference.number(c[2]),
                    int(c[3]),int(c[4]),int(c[5]))
                reply.bearing=self.native.BearingReply(int(b[0]),int(b[1]),self.reference.number(b[2]),
                    self.reference.number(b[3]),int(b[4]),int(b[5]))
                def fixed(*args):
                    self.assertEqual(args[1],len(source))
                    self.assertEqual(args[4:7],(int(isinstance(model,str)),mode,151))
                    ctypes.memmove(args[-2],ctypes.byref(reply),ctypes.sizeof(reply))
                    return 0
                self.candidate.MODEL_UNIT=model
                name=('parse','parse_scalar','parse_angle','parse_scalar')[mode]
                admitted=source
                if mode==3:
                    admitted=source.encode('latin-1')
                    if r[1]=='host_bytearray': admitted=bytearray(admitted)
                error=self.reference.text(r[7])
                want=(None,False,False,error) if mode==0 else (None,error)
                with patch.object(self.native,'_required_binding',return_value=fixed):
                    self.assertEqual(getattr(self.candidate,name)(admitted),want)
                count+=1
        finally:
            self.candidate.MODEL_UNIT=original_model
        self.assertEqual(count,2521)
        self.assertEqual(self.native._native_coords_calls-before,count)

    def test_split_host_stitching_and_protocol(self):
        before=self.native._native_coords_calls
        cases=0
        for r in self.reference.rows('host'):
            if r[0]!='split_top_level': continue
            source,sep=ast.literal_eval(self.reference.text(r[1]))
            self.assertEqual(self.candidate.split_top_level(source,sep),
                             self.reference.expected('split_top_level',source,sep))
            cases+=1
        self.assertEqual(cases,45)
        self.assertEqual(self.native._native_coords_calls-before,cases)
        with self.assertRaisesRegex(ValueError,'4096'):
            self.candidate.split_top_level('a'*4097)
        with self.assertRaisesRegex(TypeError,'expects a string'):
            self.candidate.split_top_level(None)
        class Fault:
            def __init__(self,mode): self.mode=mode
            def split(self,source,length,sep,kind,spans,capacity,count):
                n=ctypes.cast(count,ctypes.POINTER(ctypes.c_uint32))
                if self.mode=='status': return 1
                n[0]=1 if self.mode!='count' else 0
                if spans is not None:
                    spans[0]=1 if self.mode=='span' else 0
                    spans[1]=length-1 if self.mode=='incomplete' else length
                return 0
        before=self.native._native_coords_calls
        for mode in ['status','count','span','incomplete']:
            with patch.object(self.native,'_required_binding',return_value=Fault(mode)):
                with self.assertRaisesRegex(RuntimeError,'Rust coordinate split'):
                    self.candidate.split_top_level('1,2')
        self.assertEqual(self.native._native_coords_calls,before)

    def test_binding_cache_and_live_forwarding(self):
        marker=object()
        for name,args,want in [('parse',('1,2',),('1,2','Millimeters')),
                ('parse_scalar',('1',),('1','Millimeters')),('parse_angle',('30',),('30',)),
                ('split_top_level',('a,b',','),('a,b',','))]:
            with patch.object(self.native,name,return_value=marker) as forwarded:
                self.assertIs(getattr(self.candidate,name)(*args),marker)
                forwarded.assert_called_once_with(*want)
        self.native._required_binding()
        path=self.native._library._name
        cdll=ctypes.CDLL
        with patch.object(self.native,'_binding',None), patch.object(self.native,'_cache',None), \
                patch.object(self.native,'_signature',None), patch.object(self.native,'_library',None), \
                patch.object(self.native.ctypes,'CDLL',wraps=cdll) as loader:
            barrier=threading.Barrier(8)
            def cold(_):
                barrier.wait()
                return self.candidate.parse('1,2')
            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes=list(pool.map(cold,range(8)))
            self.assertTrue(all(result==self.reference.expected('parse','1,2','Millimeters') for result in outcomes))
            self.assertEqual(loader.call_count,1)
            record=self.native._cache
            with patch.object(self.native,'Path',side_effect=AssertionError('warm Path')), patch.object(self.native,'_lock',None):
                self.assertIs(self.native._required_binding(),record[1])
            with tempfile.TemporaryDirectory(prefix='coords-cache-') as folder:
                other=Path(folder)/'core.so'; other.symlink_to(Path(path).resolve())
                with patch.dict(os.environ,NARWHAL_PURE_CORE=str(other)):
                    self.assertIsNot(self.native._required_binding(),record[1])
                    self.assertIsNotNone(record[2])
                self.native._required_binding()
            self.assertEqual(loader.call_count,3)
        self.assertLessEqual(self.native._array_type.cache_info().maxsize,128)

    def test_frozen_oracle_and_no_python_engine(self):
        self.assertIn('coords_reference.py sha256 06671557350df35fddfc759d35aaa46cbe27db89a6bb0f83d379b4e52880456e',
                      (self.reference.FIXTURES/'coords_provenance.txt').read_text())
        self.assertFalse(hasattr(self.reference, '_parse_polar'))
        self.assertFalse(hasattr(self.reference, '_native_coords'))
        for path in [ROOT/'narwhal3d/native_coords.py', CANDIDATE]:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    self.assertNotIn(getattr(node, 'module', ''), ('re', 'calc', 'bearing'))
                    if isinstance(node, ast.Import):
                        self.assertNotIn('re', [n.name for n in node.names])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, ('eval', 'exec', '_number', '_parse_polar'))
        self.assertEqual(self.candidate.parse('rw1+2,4'), ((3.0,4.0,0.0), True,True,None))
        self.assertEqual(self.candidate.parse_scalar('1_2'), (12.0,None))

    def test_bounds_nonfinite_types_unit_control_and_calls(self):
        for text in ['nan','NaN','-inf','+Infinity','1e999','-1e999','1_0e999']:
            self.assertEqual(self.native.parse_scalar(text),(None,'coordinate number outside finite range'))
            self.assertEqual(self.native.parse(text+',2'),(None,False,False,'coordinate number outside finite range'))
        for text in ['1<1e999', '1e308,1e308<89','1e308<1<1']:
            self.assertTrue(all(math.isfinite(x) for x in (self.native.parse(text)[0] or [])))
        for mode in range(3):
            for text in [None,False,0,[],{}]:
                self.assertEqual(self.native._parse(text,'Millimeters',mode), self.native._parse('','Millimeters',mode))
            name = ('parse', 'parse_scalar', 'parse_angle')[mode]
            for text in [True,1,1.5,b'1',b' ',b'1cm',bytearray(b'1'),
                         bytearray(b'1cm'),b'\xa01\xa0',b'1_2', b'1.e2',
                         b'1__2',b'\t2\n',[1],{'x':1}]:
                try:
                    expected = self.reference.expected(name,text,'Millimeters')
                except Exception as error:
                    with self.assertRaises(type(error)) as raised:
                        getattr(self.native, name)(text)
                    self.assertEqual(str(raised.exception), str(error))
                else:
                    self.assertEqual(getattr(self.native, name)(text), expected)
        self.assertEqual(self.native.parse('a'*4097)[-1],'coordinate input exceeds 4096 characters')
        self.assertEqual(self.native.parse_scalar('1','m'*4097)[1],'coordinate input exceeds 4096 characters')
        self.assertEqual(self.native.parse_scalar('('*65+'1'+')'*65)[1],
                         repr('('*65+'1'+')'*65)+' is not a number (calculator nesting exceeds 64 levels)')
        self.assertEqual(self.native.parse_scalar('('*64+'1'+')'*64),(1.0,None))
        for unit,expected in [('Inches','Inches'),(None,'Millimeters'),(0,'Millimeters'),(1,1),([], 'Millimeters')]:
            self.candidate.set_model_unit(unit)
            self.assertEqual(self.candidate.MODEL_UNIT,expected)
            self.assertEqual(self.candidate.parse_scalar('2'),(2.0,None))
        self.candidate.MODEL_UNIT='Millimeters'
        real = self.native._required_binding()
        calls=[]
        def counted(*args):
            calls.append(args)
            return real(*args)
        before = self.native._native_coords_calls
        with patch.object(self.native,'_required_binding',return_value=counted):
            for _ in range(10):
                self.native.parse('1,2'); self.native.parse('bad,2')
                self.native.parse_scalar('1_2'); self.native.parse_angle('bad')
        self.assertEqual(len(calls),40)
        self.assertEqual(self.native._native_coords_calls-before,40)

    def test_required_abi_reply_faults_and_spans(self):
        with patch.dict(os.environ,{'NARWHAL_PURE_CORE':'/nonexistent/rust13.so'}):
            with self.assertRaisesRegex(RuntimeError,'unavailable'):
                self.native.parse('1,2')
        class Identity:
            def __call__(self): return 9
        class Stale:
            narwhal_pure_core_abi_version=Identity()
            narwhal_pure_core_coords_owned_abi1=Identity()
        with patch.dict(os.environ,{'NARWHAL_PURE_CORE':'/stale/rust13.so'}):
            with patch.object(self.native.ctypes,'CDLL',return_value=Stale()):
                with self.assertRaisesRegex(RuntimeError,'Stale'): self.native.parse('1,2')
        def fault(**changes):
            def call(*args):
                out=ctypes.cast(args[-2],ctypes.POINTER(self.native.CoordsReply)).contents
                out.code=0
                for k,v in changes.items():
                    if k=='nan': out.values[0]=float('nan')
                    elif k=='calc': out.calc.code=v
                    elif k!='rc': setattr(out,k,v)
                return changes.get('rc',0)
            return call
        for binding in [lambda *args:0,fault(rc=1),fault(code=99),fault(flags=4),fault(nan=True),
                        fault(start=1),fault(end=100),fault(code=2,flags=1),fault(calc=2),
                        fault(code=20),fault(code=21)]:
            with patch.object(self.native,'_required_binding',return_value=binding):
                with self.assertRaisesRegex(RuntimeError,'Invalid Rust coordinate reply'):
                    self.native.parse('1,2')
        real=self.native._required_binding()
        size=ctypes.sizeof(self.native.CoordsReply)
        reply=self.native.CoordsReply(code=123)
        source=self.native._buffer('1,2')
        for args in [(None,1,source,3,1,0,151), (source,3,None,1,1,0,151),
                     (source,3,None,0,2,0,151),(source,3,None,0,1,4,151),
                     (source,3,None,0,1,0,160)]:
            old=bytes(reply)
            self.assertNotEqual(real(*args,ctypes.byref(reply),size),0)
            self.assertEqual(bytes(reply),old)
        self.assertNotEqual(real(source,3,None,0,1,0,151,ctypes.byref(reply),size-1),0)
        self.assertEqual(reply.code,123)
        self.assertEqual(real(None,4097,None,0,1,0,151,ctypes.byref(reply),size),0)
        self.assertEqual(reply.code,18)
        misaligned=ctypes.cast(ctypes.addressof(source)+1,ctypes.POINTER(ctypes.c_uint32))
        old=bytes(reply)
        self.assertNotEqual(real(misaligned,1,None,0,1,0,151,ctypes.byref(reply),size),0)
        self.assertEqual(bytes(reply),old)
        self.assertNotEqual(real(source,3,None,0,1,0,151,None,size),0)
        count=ctypes.c_uint32(123)
        spans=(ctypes.c_uint32*4)(9,9,9,9)
        self.assertNotEqual(real.split(source,3,44,1,spans,2,ctypes.byref(count)),0)
        self.assertEqual((list(spans),count.value),([9]*4,123))
        self.assertNotEqual(real.split(None,1,44,1,spans,4,ctypes.byref(count)),0)
        self.assertEqual((list(spans),count.value),([9]*4,123))
        self.assertEqual(real.split(source,3,44,1,spans,4,ctypes.byref(count)),0)
        self.assertEqual((list(spans),count.value),([0,1,2,3],2))

    def test_host_cplane_and_lossless_format(self):
        class Plane:
            def to_world(self,u,v,w): return (u+10,-w+20,v+30)
            def vector_to_world(self,u,v,w): return (u,-w,v)
        c=self.candidate
        for source,last,callback in [('1,2',None,'to_world'),
                ('r1,2',(4,5,6),'vector_to_world'),('rw1,2',(4,5,6),None),('r1,2',None,None)]:
            plane=Plane()
            with patch.object(plane,'to_world',wraps=plane.to_world) as point_call, \
                    patch.object(plane,'vector_to_world',wraps=plane.vector_to_world) as vector_call:
                self.assertEqual(c.resolve(source,plane,last),
                                 self.reference.expected('resolve',source,'CUSTOM',last))
                self.assertEqual(point_call.call_count,int(callback=='to_world'))
                self.assertEqual(vector_call.call_count,int(callback=='vector_to_world'))
        p=(1/3,math.pi,-1e-200)
        self.assertEqual(c.resolve(c.format_world(p),Plane()),(p,None))


def main():
    global CANDIDATE
    parser=argparse.ArgumentParser()
    parser.add_argument('--library',type=Path)
    parser.add_argument('--candidate',type=Path)
    args,rest=parser.parse_known_args()
    if args.candidate: CANDIDATE=args.candidate.resolve()
    if args.library: os.environ['NARWHAL_PURE_CORE']=str(args.library.resolve())
    unittest.main(argv=[__file__]+rest)


if __name__=='__main__':
    main()
