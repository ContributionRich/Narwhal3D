"""Exact platform error presentation; computation remains in the required Rust DLL.

Windows messages were captured from original Python with all native access denied.
Synthetic replies isolate host formatting; real calls verify the shipped ABI path.
"""
import argparse
import ast
import ctypes
import errno
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'native/pure_core/tests/fixtures'
CAPTURE_SHA256 = 'c1d3a28fb8309c3afcbf904f1d8377fd69aef18e411aad64f3d5bd2b2f496440'


def text(value):
    return ''.join(chr(int(cp, 16)) for cp in value.split(',') if cp)


def load(name):
    spec = importlib.util.spec_from_file_location('format_test_' + name,
                                                  ROOT / 'narwhal3d' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlatformErrorFormat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = (FIXTURES / 'math_error_windows_msvc.txt').read_bytes()
        cls.windows = [row.split('|') for row in raw.decode('utf-8').splitlines()]
        cls.linux = {r[0]: r for row in (FIXTURES / 'calc_golden.txt').read_text(
            encoding='utf-8').splitlines() for r in [row.split('|')] if r[8] == '23'}
        cls.raw = ('\n'.join(raw.decode('utf-8').splitlines()) + '\n').encode('utf-8')

    def test_independent_capture_has_exact_complete_original_error_row_set(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), CAPTURE_SHA256)
        ids = [row[0] for row in self.windows]
        self.assertEqual(len(ids), 40)
        self.assertEqual(len(set(ids)), 40)
        self.assertEqual(set(ids), set(self.linux))
        for row in self.windows:
            original = self.linux[row[0]]
            self.assertEqual(len(row), 5)
            self.assertEqual(row[1:3], original[2:4])
            self.assertEqual(text(row[3]), "pow: (34, 'Result too large')")
            self.assertEqual(text(row[4]), '%r is not a number (%s)' % (
                text(row[1]), text(row[3])))

    def test_both_adapters_use_host_erange_wording_not_a_platform_literal(self):
        for wording in ['Result too large', 'Numerical result out of range',
                        'distinct host diagnostic wording']:
            for name in ['native_calc', 'native_coords']:
                with self.subTest(wording=wording, adapter=name):
                    with patch('os.strerror', return_value=wording) as strerror:
                        module = load(name)
                    self.assertEqual(module._MATH_ERRORS[23], str((errno.ERANGE, wording)))
                    strerror.assert_called_once_with(errno.ERANGE)
                    self.assertEqual(dict(module._MATH_ERRORS) | {23: None}, {
                        18: 'math domain error', 19: 'math range error',
                        20: 'result is not a real number', 21: 'complex exponentiation',
                        22: '0.0 cannot be raised to a negative power', 23: None})

    def test_all_original_windows_messages_through_both_live_formatters(self):
        with patch('os.strerror', return_value='Result too large'):
            calc, coords = load('native_calc'), load('native_coords')
        before_calc, before_coords = calc._native_calc_calls, coords._native_coords_calls
        count = 0
        for row in self.windows:
            original = self.linux[row[0]]
            source, model = text(row[1]), ast.literal_eval(text(row[2]))
            native = calc.CalcReply(int(original[8]), int(original[9]),
                struct.unpack('>d', bytes.fromhex(original[12]))[0],
                int(original[10]), int(original[11]), 0)
            def calc_reply(*args):
                self.assertEqual(list(args[0]), list(map(ord, source)))
                self.assertEqual(args[4:6], (int(isinstance(model, str)), 151))
                ctypes.memmove(args[-2], ctypes.byref(native), ctypes.sizeof(native))
                return 0
            reply = coords.CoordsReply(code=20, start=0, end=len(source))
            reply.calc = coords.CalcReply(native.code, native.aux, native.value,
                                         native.start, native.end, 0)
            def coords_reply(*args):
                self.assertEqual(list(args[0]), list(map(ord, source)))
                self.assertEqual(args[4:7], (int(isinstance(model, str)), 1, 151))
                ctypes.memmove(args[-2], ctypes.byref(reply), ctypes.sizeof(reply))
                return 0
            with self.subTest(case=row[0]):
                with patch.object(calc, '_required_binding', return_value=calc_reply):
                    self.assertEqual(calc.evaluate(source, model), (None, text(row[3])))
                with patch.object(coords, '_required_binding', return_value=coords_reply):
                    self.assertEqual(coords.parse_scalar(source, model), (None, text(row[4])))
            count += 1
        self.assertEqual(count, 40)
        self.assertEqual(calc._native_calc_calls - before_calc, 40)
        self.assertEqual(coords._native_coords_calls - before_coords, 40)

    def test_required_real_native_calls_match_current_host_original_errors(self):
        calc, coords = load('native_calc'), load('native_coords')
        before_calc, before_coords = calc._native_calc_calls, coords._native_coords_calls
        for row in self.windows:
            source, model = text(row[1]), ast.literal_eval(text(row[2]))
            calculator_error = text(row[3]) if sys.platform == 'win32' else text(
                self.linux[row[0]][7])
            coordinate_error = text(row[4]) if sys.platform == 'win32' else (
                '%r is not a number (%s)' % (source, calculator_error))
            with self.subTest(case=row[0]):
                self.assertEqual(calc.evaluate(source, model), (None, calculator_error))
                self.assertEqual(coords.parse_scalar(source, model), (None, coordinate_error))
        self.assertEqual(calc._native_calc_calls - before_calc, 40)
        self.assertEqual(coords._native_coords_calls - before_coords, 40)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', type=Path)
    args, rest = parser.parse_known_args()
    if args.library:
        os.environ['NARWHAL_PURE_CORE'] = str(args.library.resolve())
    unittest.main(argv=[__file__] + rest)


if __name__ == '__main__':
    main()
