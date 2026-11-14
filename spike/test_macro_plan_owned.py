"""Checked macro planner host boundaries against fixed original answers.
Rust owns computational qualification; no public mutation/compiler harness."""
import ctypes
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
ROOT = Path(__file__).resolve().parents[1]
PKG = 'checked_plan_review'
package = types.ModuleType(PKG)
package.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules[PKG] = package
product = importlib.import_module(PKG + '.macro_plan')
adapter = importlib.import_module(PKG + '.native_macro_plan')
spec = importlib.util.spec_from_file_location('checked_plan_oracle', ROOT / 'spike/macro_plan_reference.py')
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)
CASEFOLD_ACCEPT = ["'_ſnap", "'_selcroßing", "'_selcroẞing", "'_curvaturegraphoﬀ"]

def outcome(function, text):
    try:
        return ('ok', function(text))
    except Exception as error:
        return ('error', type(error).__name__, str(error))

class CheckedPlanner(unittest.TestCase):

    def test_fixed_full_refusal_messages_and_first_error_payloads(self):
        count = 0
        for r in oracle.rows():
            if r[0] != 'P' or not r[2].startswith('S') or not r[5]:
                continue
            text = bytes.fromhex(r[2][1:]).decode('utf-8')
            want = ('error', r[5], bytes.fromhex(r[6]).decode('utf-8'))
            with patch.object(product, 'build_plan', side_effect=AssertionError('Python replay')):
                self.assertEqual(outcome(product.plan_macro, text), want)
            with self.assertRaises(adapter.NativeMacroPlanError) as caught:
                adapter.plan_macro(text)
            name = bytes.fromhex(r[7]).decode('utf-8') if r[7] else None
            self.assertEqual((caught.exception.code, caught.exception.name), (int(r[3]), name))
            count += 1
        self.assertEqual(count, 62)
        before = adapter._native_macro_plan_calls
        for value in [None, 3, False, [], b'Zoom']:
            self.assertEqual(outcome(product.plan_macro, value), outcome(oracle.plan_macro, value))
        self.assertEqual(adapter._native_macro_plan_calls, before)

    def test_every_string_is_native_and_no_python_replay(self):
        for value in ['', '_Zoom', "'_Box", '=value', '_Zoom ' * 1500]:
            before = adapter._native_macro_plan_calls
            with patch.object(product, 'build_plan', side_effect=AssertionError('Python replay')), patch.object(product, 'tokenize', side_effect=AssertionError('Python tokenize replay'), create=True):
                self.assertEqual(outcome(product.plan_macro, value), outcome(oracle.plan_macro, value))
            self.assertGreater(adapter._native_macro_plan_calls, before)
        with patch.object(adapter, '_checked_binding', return_value=None), patch.object(product, 'build_plan', side_effect=AssertionError('Python replay')):
            with self.assertRaises(RuntimeError):
                product.plan_macro('_Zoom')

    def test_typed_error_payload_and_order(self):
        for value, code, name in [("'_Box", -4, 'Box'), ('=value', -5, None), ("=value '_Box", -5, None), ("'_Box =value", -4, 'Box')]:
            with self.assertRaises(adapter.NativeMacroPlanError) as result:
                adapter.plan_macro(value)
            self.assertEqual((result.exception.code, result.exception.name), (code, name))
        for value in CASEFOLD_ACCEPT:
            self.assertEqual(product.plan_macro(value), oracle.plan_macro(value))

    def test_input_bound_and_unavailable_core(self):
        for value in ['a' * ((1 << 20) + 1), '\ud800']:
            with self.assertRaises(RuntimeError):
                product.plan_macro(value)
        with patch.dict(os.environ, NARWHAL_PURE_CORE='/nonexistent/required-planner.so'):
            with self.assertRaises(RuntimeError):
                product.plan_macro('_Zoom')
        adapter._reset_for_tests()
        library_path = os.environ.get('NARWHAL_PURE_CORE') or str(ROOT / 'narwhal3d/native/libnarwhal_pure_core.so')
        with patch.dict(os.environ, NARWHAL_PURE_CORE=library_path):
            for fake in [types.SimpleNamespace(narwhal_pure_core_macro_plan_checked=Mock(), narwhal_pure_core_abi_version=Mock(return_value=0)), types.SimpleNamespace(narwhal_pure_core_abi_version=Mock(return_value=1))]:
                adapter._reset_for_tests()
                with patch.object(adapter.ctypes, 'CDLL', return_value=fake):
                    with self.assertRaises(RuntimeError):
                        product.plan_macro('_Zoom')
        adapter._reset_for_tests()

    def test_untrusted_size_status_and_json_are_not_success(self):

        def make(status, needed, payload=b''):

            def call(text, length, output, capacity, out_needed):
                ctypes.cast(out_needed, ctypes.POINTER(ctypes.c_int64))[0] = needed
                if payload and capacity >= len(payload):
                    ctypes.memmove(output, payload, len(payload))
                return status
            return call
        faults = [make(1, 1 << 60), make(1, 1), make(0, -1), make(0, 1 << 60), make(-5, 1), make(-2, 0), make(-4, 2, b'[]'), make(0, 3, b'[0]'), make(0, 2, b'\xff\xff')]
        for function in faults:
            with patch.object(adapter, '_checked_binding', return_value=function):
                with self.assertRaises(RuntimeError):
                    product.plan_macro('_Zoom')

        def bad_retry(text, length, output, capacity, needed):
            ctypes.cast(needed, ctypes.POINTER(ctypes.c_int64))[0] = 1500 if capacity < 1500 else 1499
            return 1 if capacity < 1500 else 0
        with patch.object(adapter, '_checked_binding', return_value=bad_retry):
            with self.assertRaises(RuntimeError):
                product.plan_macro('Zoom')
if __name__ == '__main__':
    unittest.main(verbosity=2)
