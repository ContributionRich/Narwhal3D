"""RUST-COORDS-13 real-native qualification (bundled CPython 3.13).

Default: external scratch crate, exact repo sources and checked span helpers.
--library PATH / NARWHAL_PURE_CORE: integrated library, no scratch build.
--candidate PATH: optional inactive candidate; default tests public live coords.
--mutations: external source copies only; no live mutation or archive dependency.
"""
import argparse
import ast
import ctypes
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('numparse', 'numparse_owned', 'python_text', 'calc_owned', 'bearing_owned', 'python_norm', 'coords_owned')
CANDIDATE = ROOT / 'narwhal3d/coords.py'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scratch(directory, mutation=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'Cargo.toml').write_text(
        '[package]\nname="rust13-coords-qualification"\nversion="0.0.0"\nedition="2021"\n'
        '[lib]\nname="narwhal_pure_core"\ncrate-type=["cdylib","rlib"]\npath="lib.rs"\n')
    original = (ROOT / 'native/pure_core/src/lib.rs').read_text()
    start = original.index('use std::mem::{align_of, size_of};')
    helpers = original[start:original.index('\n#[cfg(test)]', start)]
    for name in MODULES:
        path = ROOT / 'native/pure_core/src' / (name + '.rs')
        if mutation:
            content = path.read_text()
            if name == mutation[0]:
                old, new = mutation[1:]
                assert content.count(old) == 1, (name, old)
                content = content.replace(old, new)
            path = directory / (name + '.rs')
            path.write_text(content)
        helpers += '\n#[path=%s]\npub mod %s;\n' % (json.dumps(str(path)), name)
    (directory / 'lib.rs').write_text(helpers)
    return directory / 'Cargo.toml'


def cargo(manifest, command):
    result = subprocess.run(['cargo', '+1.95.0', command, '--offline', '--manifest-path', str(manifest)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90)
    if result.returncode:
        raise AssertionError(result.stdout)
    return result.stdout


class CoordinatesOwned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = load('rust13_native_coords', ROOT / 'narwhal3d/native_coords.py')
        cls.reference = load('rust13_coords_reference', ROOT / 'spike/coords_reference.py')
        cls.candidate = load('rust13_coords_candidate', CANDIDATE)
        cls.comparisons = 0

    def compare(self, text, model='Millimeters', mode=0):
        self.reference.MODEL_UNIT = model
        name = ('parse', 'parse_scalar', 'parse_angle')[mode]
        expected = getattr(self.reference, name)(text)
        actual = (self.native.parse(text, model) if mode == 0 else
                  self.native.parse_scalar(text, model) if mode == 1 else self.native.parse_angle(text))
        type(self).comparisons += 1
        self.assertEqual(actual[1:] if mode == 0 else actual[1],
                         expected[1:] if mode == 0 else expected[1],
                         (ascii(text), model, actual, expected))
        if expected[0] is None:
            self.assertIsNone(actual[0])
            return
        pairs = zip(actual[0], expected[0]) if mode == 0 else [(actual[0], expected[0])]
        for a, b in pairs:
            self.assertTrue(math.isfinite(a), (text, actual))
            # CPython-compensated hypot now preserves projection bits too.
            self.assertEqual(struct.pack('!d', a), struct.pack('!d', b),
                             (ascii(text), model, actual, expected))

    def test_frozen_oracle_and_no_python_engine(self):
        self.assertEqual(hashlib.sha256((ROOT/'spike/coords_reference.py').read_bytes()).hexdigest(),
                         '06671557350df35fddfc759d35aaa46cbe27db89a6bb0f83d379b4e52880456e')
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

    def test_contract_prefix_polar_error_precedence(self):
        texts = ['', ' ', '0','-0','0.0','1','w','rw','@w','W0','rw0', '.5,2','.x','<30',
                 '1,2','1,2,3','1,2,3,4',',','1,','1,,3', '1, 2', '1\t,2',
                 '5<30', '5<30,2','5<30<45','5,6<15','1,2,3<bad','1,2,3<30',
                 '1<2<3<4','1,2<3<4','<','1<','1<<','1<2,3,4','bad<2,3,4',
                 '1<N30E','1<N91W','1<NbadE','1<N30EE','1<N E','1<ſ30E',
                 '1e2<N30E', '1cm<N30E','1<N30E,2','1<N30d20\'30"W',
                 'pow(2,3),4','atan2(2,3),sqrt(4)','pow(2,3)<30',
                 'pow(2,3)<bad','pow(2,3)<30<20','1<pi','1<sin(1)',
                 '1<1e2d','1<.5d2.3\'4e-1"','1<22\'','1<1d',
                 '1/0,2','sqrt(-1),2','pow(-1,.5),2','sin(),2','(1,2',')1,2',
                 '1_234.5_6,2e1_0','١٢,３','𝟜.٥e２,1','1\ud800,2','\udfff,1',
                 'foo,1','1cm,2','1\'2",2','1.,2','1.e2,2','-0,0']
        for prefix in ['', 'rw','@w','w','r','@','RW','@W','wr','rr']:
            for text in texts:
                self.compare(prefix+text)
        for model in ['Millimeters','Inches','Meters','bad','',None,1,[],{},'\ud800','Kilometers']:
            for text in ['1cm,2', '1,2','pow(2,3),1', 'bad,2','1\'2",0','1mm<30']:
                self.compare(text,model)

    def test_scalar_angle_grammars(self):
        for text in ['', ' ','0','-0','1.','1.e2','.5','1_2','1__2','1_.2','1._2',
                     '1e1_0','1e_2','1/0','1-3/4','2cm','sqrt(-1)','pow(2)',
                     'sin(1,2)','1+@','²','١٢.３','\ud800','pi','1\u00a02',
                     '\x1c2\x1f', '1e-999','-1e-999']:
            self.compare(text,mode=1)
        angles = ['',' ', '0','-0','+0', '1.', '.5','30d','30deg','30degree','30degrees',
                  '2radian','2radians','2rad','20grade','20grades','20grad','100gradian',
                  '100gradians','0d20\'50"','15.5d30.5\'2e1"','22\'','2"',
                  '-0d0\'0"','2d','2d3"','2\'3"','2e2d3e-2\'4e+1"',
                  '1e','1E+','pi','π','PI','1*2','(1)','1+2','bad','١٢d３\'４"',
                  '\ud800','1_0','2\n','1\x1c','1K','1İ','1ſ', '1d2\'3"4']
        for text in angles:
            self.compare(text,mode=2)
            self.compare('1<'+text)

    def test_seeded_differential(self):
        rng = random.Random(1313)
        atoms = ['0','-0','1','2','1_2','.5','1.','1e-2','1/0','1cm','pi',
                 'pow(2,3)','sqrt(-1)','1-3/4','bad','١٢','\ud800','(',')','']
        for _ in range(14000):
            a,b,c = rng.choices(atoms,k=3)
            self.compare(rng.choice(['','w','r','rw','@w','@']) + rng.choice(
                [a+','+b,a+','+b+','+c,a+'<'+b,a+'<'+b+','+c,a+'<'+b+'<'+c,
                 a+','+b+'<'+c,a,a+','+b+','+c+',4']),rng.choice(['Millimeters','Meters','bad',None]))
        for _ in range(2000):
            x,y,a = [rng.uniform(-1e100,1e100) for _ in range(3)]
            self.compare('%r,%r<%r' % (x,y,a))
            self.compare('%r<%r<%r' % (x,y,a))
        for cp in [0x85,0xa0,0x1c,0x2007,0x2028,0x3000,0xfeff]:
            for text in [chr(cp)+'1,2'+chr(cp),'1'+chr(cp)+',2']:
                self.compare(text)

    def test_split_native_spans(self):
        rng = random.Random(1314)
        for text in ['','pow(2,3),4',')a,b(', '((,)),', '\ud800,٢'] + [
                ''.join(rng.choices('(),ab<>',k=rng.randrange(80))) for _ in range(1000)]:
            for sep in [',','(',')','a','',',,',None,60,'\ud800']:
                self.assertEqual(self.native.split_top_level(text,sep),self.reference.split_top_level(text,sep))
        with self.assertRaises(ValueError):
            self.native.split_top_level('a'*4097)

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
                    expected = getattr(self.reference, name)(text)
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
        self.compare('-'*4095+'1', mode=1)
        self.compare('1+'*2046+'1,0')
        self.compare('9'*2000+'/'+'8'*2000+',0')
        # Decimal Unicode predicates are independent of the calculator's
        # broader isdigit predicate; every 15.1 Nd code point is exercised.
        for cp in range(0x110000):
            ch=chr(cp)
            if ch.isdecimal():
                self.compare(ch+'_0,2')
                self.compare(ch+'d', mode=2)

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
        self.assertEqual(c.resolve('1,2',Plane()),((11,20,32),None))
        self.assertEqual(c.resolve('r1,2',Plane(),(4,5,6)),((5,5,8),None))
        self.assertEqual(c.resolve('rw1,2',Plane(),(4,5,6)),((5,7,6),None))
        self.assertEqual(c.resolve('r1,2',Plane()),(None,'there is no previous point for a relative coordinate'))
        p=(1/3,math.pi,-1e-200)
        self.assertEqual(c.resolve(c.format_world(p),Plane()),(p,None))

    @classmethod
    def tearDownClass(cls):
        print('Real-native bit-exact comparisons:',cls.comparisons)


def main():
    global CANDIDATE
    parser=argparse.ArgumentParser()
    parser.add_argument('--library',type=Path,default=os.environ.get('NARWHAL_PURE_CORE'))
    parser.add_argument('--candidate',type=Path)
    parser.add_argument('--mutations',action='store_true')
    args=parser.parse_args()
    if args.candidate: CANDIDATE=args.candidate.resolve()
    with tempfile.TemporaryDirectory(prefix='rust13-coords-qualification-') as temp:
        if args.library:
            library=Path(args.library).resolve()
            if not library.is_file(): raise RuntimeError('Explicit real-native library missing')
        else:
            manifest=scratch(Path(temp)/'exact')
            print(cargo(manifest,'test'))
            cargo(manifest,'build')
            library=manifest.parent/'target/debug/libnarwhal_pure_core.so'
        os.environ['NARWHAL_PURE_CORE']=str(library)
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CoordinatesOwned))
        if not result.wasSuccessful(): return 1
        if args.mutations:
            mutations=[('calc_owned','v + r','v - r'),
                       ('coords_owned',
                        '[("rw", 3), ("@w", 3), ("w", 1), ("r", 2), ("@", 2)]',
                        '[("r", 2), ("rw", 3), ("@w", 3), ("w", 1), ("@", 2)]'),
                       ('coords_owned','return Err(error(6, a, b));','return Err(error(7, a, b));'),
                       ('coords_owned','crate::python_norm::hypot2(x, y)','x.hypot(y)')]
            for i,mutation in enumerate(mutations):
                manifest=scratch(Path(temp)/('mutant'+str(i)),mutation)
                red=subprocess.run(['cargo','+1.95.0','test','--offline','--manifest-path',str(manifest)],
                                   stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=90)
                if red.returncode!=101 or 'test result: FAILED' not in red.stdout:
                    raise AssertionError('Mutation failed to go RED: '+str(mutation)+'\n'+red.stdout)
                print('MUTATION RED confirmed:',mutation[:2])
            print(cargo(scratch(Path(temp)/'restored'),'test'))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
