"""Calculator host boundaries; fixed original answers, required real-native ABI.

Run with bundled CPython 3.13 and --library or NARWHAL_PURE_CORE.
Differential loops live in Rust calc_golden; engineering compilers/mutations and
the unchanged original benchmark are private, never public test dependencies.
"""
import argparse
import ast
import ctypes
import importlib.util
import struct
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CalculatorOwned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = load('rust12_calc_reference', ROOT / 'spike/calc_reference.py')
        cls.candidate = load('rust12_calc_candidate', ROOT / 'narwhal3d/calc.py')
        cls.native = cls.candidate._adapter()

    def assert_outcome(self, actual, expected):
        self.assertEqual(actual[1], expected[1])
        if expected[0] is None:
            self.assertIsNone(actual[0])
        else:
            self.assertEqual(struct.pack('>d', actual[0]), struct.pack('>d', expected[0]))

    def test_live_wrapper_and_host_admission(self):
        again = load('calc_boundary_again', ROOT / 'narwhal3d/calc.py')
        self.assertIs(again._adapter(), self.native)
        marker = object()
        with patch.object(self.native, 'evaluate', return_value=marker) as forwarded:
            for source, model in [('1+2', 'Meters'), ('', []), (None, None)]:
                self.assertIs(self.candidate.evaluate(source, model), marker)
                forwarded.assert_called_with(source, model)
            self.assertEqual(forwarded.call_count, 3)
        with patch.object(self.native, '_required_binding', side_effect=AssertionError('nonstring admission')):
            for source in (None, 0, 1.5, [], b'1'):
                before = self.native._native_calc_calls
                self.assertEqual(self.candidate.evaluate(source), self.reference.expected(source))
                self.assertEqual(self.native._native_calc_calls, before)
        class NoCoercion:
            def __str__(self): raise AssertionError('model str coercion')
            def __repr__(self): raise AssertionError('model repr coercion')
            def __float__(self): raise AssertionError('model float coercion')
        with patch.object(self.native, '_required_binding', side_effect=AssertionError('object admission')):
            self.assertEqual(self.candidate.evaluate(NoCoercion(), NoCoercion()),
                             self.reference.expected(None))
        for source in ['1+2*3', '1mm', "16'5\"", "1'0/0\"", '1\t@']:
            self.assert_outcome(self.candidate.evaluate(source, NoCoercion()),
                                self.reference.expected(source, None))
        with patch.object(self.native.unicodedata, 'unidata_version', '16.0.0'):
            self.assertEqual(self.candidate.evaluate(None), self.reference.expected(None))
            with self.assertRaisesRegex(RuntimeError, 'Unicode'):
                self.candidate.evaluate('1')

    def test_all_captured_full_error_presentations(self):
        before = self.native._native_calc_calls
        count = 0
        for source, model, want, r in self.reference.rows('presentation'):
            def fixed_reply(*args):
                self.assertEqual(list(args[0]), list(map(ord, source)))
                self.assertEqual(args[4:6], (int(isinstance(model, str)), 151))
                reply = ctypes.cast(args[-2], ctypes.POINTER(self.native.CalcReply)).contents
                reply.code, reply.aux = int(r[8]), int(r[9])
                reply.start, reply.end = int(r[10]), int(r[11])
                reply.value = self.reference.number(r[12])
                return 0
            with patch.object(self.native, '_required_binding', return_value=fixed_reply):
                self.assert_outcome(self.candidate.evaluate(source, model), want)
            count += 1
        self.assertEqual(count, 4575)
        self.assertEqual(self.native._native_calc_calls - before, count)

    def test_published_contract_and_archive(self):
        self.assertFalse(hasattr(self.reference, '_tokenize'))
        self.assertFalse(hasattr(self.reference, '_parse_expr'))
        for text, model, want, _ in self.reference.rows():
            self.assert_outcome(self.candidate.evaluate(text, model), want)
        self.assertEqual(self.candidate.evaluate('1+2*3'), (7.0, None))
        for source in [ROOT / 'narwhal3d/native_calc.py', ROOT / 'narwhal3d/calc.py']:
            tree = ast.parse(source.read_text())
            imports = {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
            self.assertNotIn('re', imports)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, ('eval', 'exec', '_tokenize', '_parse_expr'))

    def test_explicit_bounds_and_native_call_count(self):
        cases = [(text, model, want) for text, model, want, _ in self.reference.rows('limits')]
        real = self.native._required_binding()
        calls = []
        def counted(*args):
            calls.append(args)
            return real(*args)
        before = self.native._native_calc_calls
        with patch.object(self.native, '_required_binding', return_value=counted):
            for text,model,want in cases:
                self.assert_outcome(self.native.evaluate(text,model),want)
        self.assertEqual(len(calls),len(cases))
        self.assertEqual(self.native._native_calc_calls-before,len(cases))

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', type=Path)
    options, rest = parser.parse_known_args()
    if options.library:
        os.environ['NARWHAL_PURE_CORE'] = str(options.library.resolve())
    unittest.main(argv=[__file__] + rest)


if __name__ == '__main__':
    main()
