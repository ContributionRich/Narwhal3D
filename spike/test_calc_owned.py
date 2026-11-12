"""RUST-CALC-12 qualification; never skips a missing real-native library.

Run with bundled CPython 3.13. By default builds a bounded external scratch
crate pointing at the EXACT repository source, without editing lib.rs or gates.
--library qualifies an explicit integrated build instead. --mutations builds
three external source copies and requires each to fail the same Rust tests.
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
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('numparse', 'numparse_owned', 'python_text', 'calc_owned')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scratch(directory, mutation=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'Cargo.toml').write_text(
        '[package]\nname="rust12-calc-qualification"\nversion="0.0.0"\nedition="2021"\n'
        '[lib]\nname="narwhal_pure_core"\ncrate-type=["cdylib","rlib"]\npath="lib.rs"\n')
    original = (ROOT / 'native/pure_core/src/lib.rs').read_text()
    start = original.index('use std::mem::{align_of, size_of};')
    helpers = original[start:original.index('\n#[cfg(test)]', start)]
    for name in MODULES:
        path = ROOT / 'native/pure_core/src' / (name + '.rs')
        if mutation is not None:
            content = path.read_text()
            if name == 'calc_owned':
                old, new = mutation
                assert content.count(old) == 1, old
                content = content.replace(old, new)
            path = directory / (name + '.rs')
            path.write_text(content)
        helpers += '\n#[path=%s]\npub mod %s;\n' % (json.dumps(str(path)), name)
    (directory / 'lib.rs').write_text(helpers)
    return directory / 'Cargo.toml'


def cargo(manifest, command, expect=0):
    result = subprocess.run(['cargo', '+1.95.0', command, '--offline', '--manifest-path', str(manifest)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=90)
    if result.returncode != expect:
        raise AssertionError(result.stdout)
    return result


class CalculatorOwned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = load('rust12_native_calc', ROOT / 'narwhal3d/native_calc.py')
        cls.reference = load('rust12_calc_reference', ROOT / 'spike/calc_reference.py')
        cls.candidate = load('rust12_calc_candidate', ROOT / 'narwhal3d/calc.py')
        cls.comparisons = 0

    def compare(self, text, model='Millimeters'):
        expected = self.reference.evaluate(text, model)
        actual = self.native.evaluate(text, model)
        type(self).comparisons += 1
        self.assertEqual(actual[1], expected[1], (ascii(text), model, actual, expected))
        if expected[0] is not None:
            self.assertIsNotNone(actual[0], (text, expected, actual))
            self.assertEqual(actual[0], expected[0],
                             (ascii(text), model, actual, expected))
            if actual[0] == expected[0] == 0.0:
                self.assertEqual(math.copysign(1, actual[0]), math.copysign(1, expected[0]))
        else:
            self.assertIsNone(actual[0])

    def test_published_contract_and_archive(self):
        self.assertEqual(hashlib.sha256((ROOT / 'spike/calc_reference.py').read_bytes()).hexdigest(), '354bae15356baaabbbaa8acfb246eeaebd64a06c512d62d248ae427a0b4ec5ed')
        self.assertIsNone(self.reference._native_calc())
        for text, expected in [('', 'empty formula'), ('1/0', "bad number: '1/0'"),
                ('1/(0)', 'division by zero'), ('1.', "bad number at: '.'"),
                ('12xyz', "unknown unit: 'xyz'"), ('sin()', 'sin requires at least one argument'),
                ('sin(1,2)', 'sin takes 1 argument(s), got 2'),
                ('pow(-1,.5)', 'pow: result is not a real number'),
                ('pow(-1e308,1.5)', 'pow: complex exponentiation'),
                ('pow(0,-1)', 'pow: 0.0 cannot be raised to a negative power'),
                ('pow(1e308,2)', "pow: (34, 'Numerical result out of range')"),
                ('exp(999)', 'exp: math range error'), ('sqrt(-1)', 'sqrt: math domain error'),
                ('sin(1e308*2)', 'sin: math domain error')]:
            self.assertEqual(self.native.evaluate(text), (None, expected))
        self.assertEqual(self.candidate.evaluate('1+2*3'), (7.0, None))
        for source in [ROOT / 'narwhal3d/native_calc.py', ROOT / 'narwhal3d/calc.py']:
            tree = ast.parse(source.read_text())
            imports = {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
            self.assertNotIn('re', imports)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, ('eval', 'exec', '_tokenize', '_parse_expr'))

    def test_grammar_units_and_error_precedence(self):
        texts = ['1+2*3', '(1+2)*3', '1-3/4', '1-2/3/4', '10/4', '5/(3-3)',
            '--5', '---0', '+-+-1', '1e-3+2E+2', '1.2.3', 'pi(1)', 'piπ',
            'π', 'sin', 'Sin(1)', 'unknown', 'unknown(1)', 'sin()', 'sin(,)', 'sin(1,)',
            'sin(1', 'sin(1,2,3)', 'sin(atan2(1,2))', 'pow(2)', 'atan2(1,2,3)',
            '()', '(1', '1)', '(1,2)', '+', '*', ',', '1+*2', '1**2', '2^3',
            '1\t@', 'sqrt(-1)@', 'unknown@', 'sin(1/0)@', '\n', '\r', ' ',
            "16'5\"", "1'2-3/4\"+1", "1'2\"mm", "1'2\"5", "1'0/0\"", "0/0'2\"",
            "1e999'2\"", '27cm+10cm', '2Km', '1mm', "2'", '2"', '1e', '.e1',
            '1e+', '1.e2', '.', '"', "'", "1'\"", '1/00cm', '1/00', '1e999cm',
            '9007199254740993/9007199254740992',
            '9007199254740995/9007199254740992']
        for suffix in ['d','degree','degrees','radian','radians','grade','gradians',
                       'mm','cm','m','km','um','micron','in','inch','ft','foot','yd','mi','MM']:
            texts.append('30' + suffix)
        for model in ['Millimeters','Meters','Feet','Kilometers','bad','',None,1,[],{}]:
            for text in texts:
                self.compare(text, model)
        for text in [None, 0, 1.5, [], b'1']:
            self.assertEqual(self.native.evaluate(text), (None, 'expected a string'))

    def test_unicode_predicates_and_offsets(self):
        texts = ['١٢+３', '𝟙-𝟛/𝟜', '𝟙٢.３e４', '²', '¹', '⑴', '𞅀', '一',
                 'foo²', 'fooⅧ', 'foo½', 'fooᢅ', 'foo\u0301', 'foo\u0345',
                 '\ud800', '1\udfff', '1Km', '١٢xyz', '¹cm', '＿', '𞓰']
        for cp in [0x85,0xa0,0xb,0xc,0x1c,0x2007,0x2028,0x2029,0x3000,0xfeff]:
            texts.append('1' + chr(cp) + '2')
        rng = random.Random(1312)
        numeric = [chr(c) for c in range(0x110000) if chr(c).isnumeric()]
        for char in numeric:
            texts.extend([char, 'foo' + char])
        for _ in range(500):
            c = chr(rng.randrange(0x110000))
            texts.extend([c, 'foo' + c, '1' + c])
        for text in texts:
            self.compare(text)
        for model in ['\ud800', '\u212ailometers', 'MILLIMETERS', 'MİLLİMETERS']:
            self.compare("1'2\"", model)

    def test_math_specials_and_exact_errors(self):
        values = ['0','-0','1','-1','.5','-.5','2','-2','1e308','-1e308','1e-308',
                  '(1e308*2)','(-1e308*2)','((1e308*2)-(1e308*2))',
                  '(0*(1e308*2))','2.2250738585072014e-308','5e-324']
        for name in ['sin','cos','tan','asin','acos','atan','ln','log10','exp',
                     'sinh','cosh','tanh','sqrt']:
            for value in values + ['709.782712893384','710.475860073944','-745.1332191019411']:
                self.compare('%s(%s)' % (name, value))
        for name in ['pow','atan2']:
            for a in values:
                for b in values:
                    self.compare('%s(%s,%s)' % (name, a, b))
        for text in ['1e308*2', '(1e308*2)-(1e308*2)', 'ln(1e308*2)@',
                     '0/(5e-324)', 'pow(-5e-324,-.5)', 'pow(-1e308,-1.5)',
                     'pow(-1e-308,-1.5)', 'pow(-0,-.5)']:
            self.compare(text)

    def test_seeded_libm_exact_parity(self):
        rng = random.Random(1213)
        for _ in range(3000):
            a, b = rng.uniform(-700, 700), rng.uniform(-10, 10)
            name = rng.choice(['sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'atan2',
                               'ln', 'log10', 'exp', 'sinh', 'cosh', 'tanh', 'sqrt', 'pow'])
            args = repr(a) + (',' + repr(b) if name in ('pow', 'atan2') else '')
            self.compare(name + '(' + args + ')')

    def test_seeded_differential(self):
        rng = random.Random(12)
        atoms = ['0','-0','1','2','3','1/0','1-3/4','1/2','pi','π','.5','1e308','1e-308',
                 '1cm',"16'5\"",'1e999','²','١٢','foo','sin','foo²','ᢅ','Ⅷ','\ud800']
        for _ in range(12000):
            a,b,c = rng.choices(atoms,k=3)
            text = rng.choice([a+'+'+b,a+'*'+b,a+'/'+b,a+'-'+b,'('+a+')',a+b,
                'pow('+a+','+b+')','sin('+a+')','sqrt('+a+')',a+','+b,a+' '+b,a+'@'+b,a+b+c])
            self.compare(text,rng.choice(['Millimeters','Meters','bad',None,[],'Kilometers']))

    def test_explicit_bounds_and_native_call_count(self):
        cases = [('(' * 64 + '1' + ')' * 64, 'Millimeters', (1.0,None)),
                 ('(' * 65 + '1' + ')' * 65, 'Millimeters',
                  (None,'calculator nesting exceeds 64 levels')),
                 ('sin(' * 65 + '0' + ')' * 65, 'Millimeters',
                  (None,'calculator nesting exceeds 64 levels')),
                 ('-' * 4095 + '1', 'Millimeters', (-1.0,None)),
                 ('1' * 4097, 'Millimeters', (None,'calculator input exceeds 4096 characters')),
                 ('1','m' * 4097,(None,'calculator input exceeds 4096 characters')),
                 ('1+2', 'Millimeters', (3.0,None)),
                 ('sqrt(-1)', 'Millimeters', (None,'sqrt: math domain error'))]
        real = self.native._required_binding()
        calls = []
        def counted(*args):
            calls.append(args)
            return real(*args)
        before = self.native._native_calc_calls
        with patch.object(self.native, '_required_binding', return_value=counted):
            for text,model,want in cases:
                self.assertEqual(self.native.evaluate(text,model),want)
        self.assertEqual(len(calls),len(cases))
        self.assertEqual(self.native._native_calc_calls-before,len(cases))
        self.assertEqual(self.native.evaluate('sin(' + ','.join(['1']*1000) + ')')[1],
                         'sin takes 1 argument(s), got 1000')
        # Full-size exact ratio and long flat operators exercise bounded allocation.
        self.compare('9'*2000 + '/' + '8'*2000)
        self.compare('1+' * 2047 + '1')

    def test_absent_stale_and_fault_injection_never_replay(self):
        with patch.dict(os.environ, {'NARWHAL_PURE_CORE': '/nonexistent/rust12-calculator.so'}):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                self.native.evaluate('1+2')
        class Identity:
            def __call__(self):
                return 1
        class Stale:
            narwhal_pure_core_abi_version = Identity()
            narwhal_pure_core_calc_abi2 = Identity()
        with patch.dict(os.environ, {'NARWHAL_PURE_CORE': '/stale/rust12-calculator.so'}):
            with patch.object(self.native.ctypes, 'CDLL', return_value=Stale()):
                with self.assertRaisesRegex(RuntimeError, 'Stale'):
                    self.native.evaluate('1+2')
        def broken_call(*args):
            raise OSError('broken call')
        with patch.object(self.native, '_required_binding', return_value=broken_call):
            with self.assertRaisesRegex(RuntimeError, 'call failed'):
                self.native.evaluate('1+2')
        def fault(**changes):
            def call(*args):
                out = ctypes.cast(args[-2],ctypes.POINTER(self.native.CalcReply)).contents
                out.code = 0
                for key,value in changes.items():
                    if key != 'rc':
                        setattr(out,key,value)
                return changes.get('rc',0)
            return call
        faults = [lambda *args: 0, fault(rc=1), fault(code=100), fault(reserved=1),
                  fault(value=float('inf')), fault(value=float('nan')),
                  fault(start=2,end=1), fault(end=100), fault(aux=1),
                  fault(code=3,value=1,start=0,end=1), fault(code=5,start=0,end=0),
                  fault(code=14,start=0,end=1,aux=(1<<16)|1),
                  fault(code=24), fault(code=28,start=1,end=1)]
        for binding in faults:
            with patch.object(self.native, '_required_binding', return_value=binding):
                with self.assertRaisesRegex(RuntimeError, 'Invalid Rust calculator reply'):
                    self.native.evaluate('1+2')

    @classmethod
    def tearDownClass(cls):
        print('Real-native differential comparisons:', cls.comparisons)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', type=Path, default=os.environ.get('NARWHAL_PURE_CORE'))
    parser.add_argument('--mutations', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='rust12-calc-qualification-') as temp:
        if args.library:
            library = Path(args.library).resolve()
            if not library.is_file():
                raise RuntimeError('Explicit real-native qualification library missing')
        else:
            manifest = scratch(Path(temp)/'exact')
            result = cargo(manifest, 'test')
            print(result.stdout)
            cargo(manifest, 'build')
            library = manifest.parent/'target/debug/libnarwhal_pure_core.so'
        os.environ['NARWHAL_PURE_CORE'] = str(library)
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(CalculatorOwned)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            return 1
        if args.mutations:
            mutations = [('addition', 'v + r', 'v - r'),
                         ('domain classification',
                          '"sqrt" if a < 0.0 => return fail(DOMAIN),',
                          '"sqrt" if a < 0.0 => return fail(MATH_RANGE),'),
                         ('exact literal ownership', 'let mut v = r.value;',
                          'let mut v = r.value + 1.0;')]
            # Match unique sites after rustfmt; no live source is ever mutated.
            for index,(label,old,new) in enumerate(mutations):
                manifest = scratch(Path(temp)/('mutant'+str(index)), (old,new))
                red = subprocess.run(['cargo','+1.95.0','test','--offline','--manifest-path',str(manifest)],
                                     stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=90)
                if red.returncode != 101 or 'test result: FAILED' not in red.stdout:
                    raise AssertionError('Mutation did not fail tests: '+label+'\n'+red.stdout)
                print('MUTATION RED confirmed:',label)
            # The exact original source was never modified; rerun its tests green.
            print(cargo(scratch(Path(temp)/'restored'), 'test').stdout)
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
