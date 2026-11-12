"""Real checked-planner ownership, independent parity and protocol-fault tests.

No private archive, scratch runtime, or source mutations are required. Mutants
execute in memory; product source and the installed library stay unchanged.
"""
import ctypes
import importlib
import importlib.util
import json
import os
from pathlib import Path
import random
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

VALID_MACROS = ['', 'Zoom', '_Zoom', '-_Zoom', "! '-_Zoom", 'Zoom Extents _Redraw=0', 'Zoom Extents _Redraw=0 _Dynamic=1', 'SetRedrawOff', 'SetRedrawOn', 'NoEcho', 'Echo', 'Run', "'_Zoom '_CPlane", "'_Ortho '_Planar", "'_Snap '_OSnap '_Hidep '_Showp", "'_Walkabout '_Tilt '_Pan '_RotateView", "'_SelCrossing '_SelWindow '_SelPolysrf", "'_CurvatureGraph '_CurvatureGraphOff", "'_NamedView '_NamedViews '_NamedCPlane", '_Pause', '_MultiPause', '_Enter', '_EnterEnd', "! '&*_~_Zoom", 'Zoom _Redraw=0', 'Zoom _Redraw=1 _Dynamic=0', '_-_SetDisplayMode _Mode=_Shaded "My Mode"', '_-_SetDisplayMode _Mode=_Shaded "My Mode" _Zoom _CPlane', 'SetView _CPlane=World _Zoom _Redraw=1', 'SetCPlane _Name="My CPlane"', 'SetNamedView _Name="Front"', 'SetNamedCPlane _Name="My CPlane" \'_Zoom', "'_OSnap _Point=1", "'_Snap _Entity=0", "'_CurvatureGraph _Type=0", 'Zoom Extents', 'SetRedrawOff _Zoom', 'SetRedrawOn _Zoom', 'NoEcho Zoom', 'Echo Zoom']
LATE_REFUSALS = [("'_Zoom _Pause '_Box", 'Box'), ("Run 'Box", 'Box'), ("'_Zoom '_CPlane 'Box", 'Box'), ("! '_Zoom '_CPlane 'Box", 'Box'), ("'_Zoom '_CPlane 'Box '_Pause", 'Box'), ("'_Zoom '_CPlane 'Box '_Pause 'Box", 'Box')]
REFUSALS = {"'_Box": 'NonNestableCommandError', "'_Circle": 'NonNestableCommandError', "'_Sphere": 'NonNestableCommandError', "'_Point": 'NonNestableCommandError', "'_Line": 'NonNestableCommandError', "'_Polyline": 'NonNestableCommandError', "'_Curve": 'NonNestableCommandError', "'_Surface": 'NonNestableCommandError', "'_Solid": 'NonNestableCommandError', "'_Mesh": 'NonNestableCommandError', "'_Text": 'NonNestableCommandError', "'_Block": 'NonNestableCommandError', "'_Layer": 'NonNestableCommandError', "'_Delete": 'NonNestableCommandError', "'_Move": 'NonNestableCommandError', "'_Copy": 'NonNestableCommandError', "'_Offset": 'NonNestableCommandError', "'_Trim": 'NonNestableCommandError', "'_Extend": 'NonNestableCommandError', "'_Intersect": 'NonNestableCommandError', "'_Project": 'NonNestableCommandError', "'_Explode": 'NonNestableCommandError', "'_Join": 'NonNestableCommandError', "'_Split": 'NonNestableCommandError', "'_Rebuild": 'NonNestableCommandError', "'_Fillet": 'NonNestableCommandError', "'_Chamfer": 'NonNestableCommandError', "'_BooleanUnion": 'NonNestableCommandError'}
CASEFOLD_ACCEPT = ["'_ſnap", "'_selcroßing", "'_selcroẞing", "'_curvaturegraphoﬀ"]
CASEFOLD_REFUSE = ["'_ﬁ", "'_ﬂ", "'_ﬃ", "'_ﬄ", "'_ﬅ", "'_ﬆ", "'_ſſnap", "'_k"]
ERROR_ORDER = [("=value '_Box", 'MacroPlanError', None), ("'_Box =value", 'NonNestableCommandError', 'Box'), ("'_Zoom =value", 'MacroPlanError', None), ("=value '_Zoom", 'MacroPlanError', None), ("'_Box '_Circle", 'NonNestableCommandError', 'Box'), ("'_Circle '_Box", 'NonNestableCommandError', 'Circle'), ("'_Zoom '_Box '_Circle", 'NonNestableCommandError', 'Box'), ("'_Box '_Circle '_Zoom", 'NonNestableCommandError', 'Box')]

def outcome(function, text):
    try:
        return ('ok', function(text))
    except Exception as error:
        return ('error', type(error).__name__, str(error))


class CheckedPlanner(unittest.TestCase):
    def test_complete_independent_corpus(self):
        values = VALID_MACROS + list(REFUSALS) + [v for v, _ in LATE_REFUSALS]
        values += CASEFOLD_ACCEPT + CASEFOLD_REFUSE + [v for v, _, _ in ERROR_ORDER]
        values += ['=value', '_=x', 'a = b', '!123', '_Ⅰ', 'a\x1cb']
        for value in values:
            self.assertEqual(outcome(product.plan_macro, value), outcome(oracle.plan_macro, value), value)
        for value in [None, 3, False, [], b'Zoom']:
            self.assertEqual(outcome(product.plan_macro, value), outcome(oracle.plan_macro, value))

    def test_seeded_and_long_macros(self):
        rng = random.Random(1214)
        pieces = ['_Zoom', '!123', '_Box', "'_Zoom", "'_Box", '_Pause', '_Mode=x',
                  'a', '"two words"', ';comment\n', '=value', 'a\x1cb']
        for _ in range(1500):
            value = ' '.join(rng.choices(pieces, k=rng.randrange(1, 15)))
            self.assertEqual(outcome(product.plan_macro, value), outcome(oracle.plan_macro, value), value)
        for value in ['_Zoom ' * 1500, '_Zoom ' * 1500 + "'_Box", '_Zoom ' * 1500 + '=value']:
            self.assertEqual(outcome(product.plan_macro, value), outcome(oracle.plan_macro, value))

    def test_every_string_is_native_and_no_python_replay(self):
        for value in ['', '_Zoom', "'_Box", '=value', '_Zoom ' * 1500]:
            before = adapter._native_macro_plan_calls
            with patch.object(product, 'build_plan', side_effect=AssertionError('Python replay')), \
                 patch.object(product, 'tokenize', side_effect=AssertionError('Python tokenize replay'), create=True):
                self.assertEqual(outcome(product.plan_macro, value), outcome(oracle.plan_macro, value))
            self.assertGreater(adapter._native_macro_plan_calls, before)
        with patch.object(adapter, '_checked_binding', return_value=None), \
             patch.object(product, 'build_plan', side_effect=AssertionError('Python replay')):
            with self.assertRaises(RuntimeError):
                product.plan_macro('_Zoom')

    def test_typed_error_payload_and_order(self):
        for value, code, name in [("'_Box", -4, 'Box'), ('=value', -5, None),
                                  ("=value '_Box", -5, None), ("'_Box =value", -4, 'Box')]:
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
            for fake in [types.SimpleNamespace(narwhal_pure_core_macro_plan_checked=Mock(),
                                               narwhal_pure_core_abi_version=Mock(return_value=0)),
                         types.SimpleNamespace(narwhal_pure_core_abi_version=Mock(return_value=1))]:
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
        faults = [make(1, 1 << 60), make(1, 1), make(0, -1), make(0, 1 << 60),
                  make(-5, 1), make(-2, 0), make(-4, 2, b'[]'), make(0, 3, b'[0]'),
                  make(0, 2, b'\xff\xff')]
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

    def test_in_memory_mutations_fail_the_real_parity_guard(self):
        with patch.object(product, 'plan_macro', return_value=[]):
            with self.assertRaises(AssertionError):
                self.assertEqual(outcome(product.plan_macro, "'_Box"), outcome(oracle.plan_macro, "'_Box"))
        with patch.object(product, 'plan_macro', side_effect=lambda value: [] if not isinstance(value, str) else oracle.plan_macro(value)):
            with self.assertRaises(AssertionError):
                self.assertEqual(outcome(product.plan_macro, None), outcome(oracle.plan_macro, None))
        self.assertEqual(outcome(product.plan_macro, "'_Box"), outcome(oracle.plan_macro, "'_Box"))
        self.assertEqual(outcome(product.plan_macro, None), outcome(oracle.plan_macro, None))


if __name__ == '__main__':
    unittest.main(verbosity=2)
