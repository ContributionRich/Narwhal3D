"""Owned static BEFORE-record boundary suite. Set NARWHAL_PURE_CORE to a registered/scratch artifact.
Run with bundled Python (Unicode 15.1); no builds or activation performed here.
"""
import ctypes as C
import importlib.util
import math
import os
from pathlib import Path
import struct
import sys
import unittest
from unittest import mock
import ast
import hashlib
import json
FIXTURES = Path(__file__).resolve().parents[1] / 'native/pure_core/tests/fixtures'
_HOST = ast.literal_eval((FIXTURES / 'unit_host.txt').read_text())
_EXPECTED = {}
_USED = set()

def expected(key):
    _USED.add(key)
    return next(_EXPECTED[key])
ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
native = load('unit_ops_owned', ROOT / 'narwhal3d/native_unit_ops.py')
NAMES = tuple(['Angstroms', 'Nanometers', 'Microns', 'Millimeters', 'Centimeters', 'Decimeters', 'Meters', 'Dekameters', 'Hectometers', 'Kilometers', 'Megameters', 'Gigameters', 'Microinches', 'Mils', 'Inches', 'Feet', 'Yards', 'Miles', 'PrinterPoints', 'PrinterPicas', 'NauticalMiles', 'AstronomicalUnits', 'LightYears', 'Parsecs', 'CustomUnits'])

def outcome(fn, *args):
    try:
        value = fn(*args)
        if isinstance(value, float):
            return ('float', 'nan' if math.isnan(value) else struct.pack('!d', value))
        return ('ok', value)
    except Exception as exc:
        return (type(exc).__name__, str(exc))

def parse(text):
    return native.parse_unit(text, NAMES)

class GoldenCase(unittest.TestCase):

    def setUp(self):
        _EXPECTED.clear()
        _EXPECTED.update({key: iter(values) for key, values in _HOST.items()})
        _USED.clear()

    def tearDown(self):
        keys = {key for key in _HOST if key.startswith(self._testMethodName + ':')}
        self.assertTrue(keys <= _USED, keys - _USED)
        for key in _USED:
            self.assertEqual(list(_EXPECTED[key]), [], 'unconsumed BEFORE records: ' + key)

class UnitOps(GoldenCase):

    def test_parse_error_repr_from_fixed_records(self):
        # Grammar/status matching is Rust-owned; Python still renders exceptions.
        errors = 0
        for line in (FIXTURES / 'unit_parse.txt').read_text().splitlines():
            text, status, _, _, _, result = line.split('\t')
            if status == '0':
                continue
            text = '' if text == '-' else bytes.fromhex(text).decode('utf8', 'surrogatepass')
            captured = ast.literal_eval(bytes.fromhex(result).decode('utf8', 'surrogatepass'))
            self.assertEqual(outcome(parse, text), captured, repr(text))
            errors += 1
        self.assertEqual(errors, 4059)

    def test_static_before_fixture_integrity(self):
        manifest = (FIXTURES / 'unit_manifest.txt').read_bytes()
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), '062452f41f857132fba4a5c7e504cd469ab6750ed15937783af7124d11a9fbe3')
        metadata = json.loads(manifest)
        self.assertEqual(metadata['parse_cases'], 4437)
        self.assertEqual(metadata['unicode_non_ascii'], 1113984)
        for name, record in metadata.items():
            if not name.startswith('unit_'):
                continue
            self.assertEqual(Path(name).name, name)
            data = (FIXTURES / name).read_bytes()
            self.assertEqual(len(data), record['bytes'], name)
            self.assertEqual(len(data.splitlines()), record['lines'], name)
            self.assertEqual(hashlib.sha256(data).hexdigest(), record['sha256'], name)


    def same_parse(self, text):
        self.assertEqual(outcome(parse, text), expected('same_parse:61'), repr(text))

    def test_angles(self):
        names = ['degrees', 'radians', 'gradians', ' DEGREES ', '\x1cRadians\xa0', 'degree', 'd', None, 'İ', '\ud800']
        for a in names:
            for b in names:
                for value in [0.0, -0.0, 180.0, math.inf, math.nan, 'bad']:
                    if a in ['degrees', 'radians', 'gradians', ' DEGREES ', '\x1cRadians\xa0'] and b in ['degrees', 'radians', 'gradians', ' DEGREES ', '\x1cRadians\xa0'] and (value != 'bad'):
                        continue
                    self.assertEqual(outcome(native.convert_angle, value, a, b), expected('test_angles:117'))

    def test_bounds_and_abi(self):
        self.same_parse('x' * 4096)
        self.same_parse(' ' * 4096)
        self.assertEqual(outcome(parse, 'x' * 4097), ('ValueError', 'unit input exceeds 4096 codepoints (OURS)'))
        binding = native._native()
        i, mask, size = (C.c_uint32(99), C.c_uint32(99), C.c_size_t(99))
        raw = C.create_string_buffer(16384)
        self.assertEqual(binding.parse(b'x' * 4097, 4097, C.byref(i), C.byref(mask), raw, len(raw), C.byref(size)), 4)
        self.assertEqual((i.value, mask.value, size.value), (99, 99, 99))
        self.assertEqual(binding.parse(b'mm', 2, C.byref(i), C.byref(mask), raw, 1, C.byref(size)), 5)
        self.assertEqual(binding.parse(b'\xff', 1, C.byref(i), C.byref(mask), raw, len(raw), C.byref(size)), 5)

    def test_length_host_boundary(self):
        convert = load_live().convert
        for a in NAMES + ('None', 'Unset', 'bad', None):
            for b in NAMES + ('None', 'Unset', 'bad', None):
                for custom in [None, 0.0, -0.0, 0.125, 'bad', math.inf]:
                    for value in [1.25, 'bad']:
                        args = (value, a, b, custom)
                        self.assertEqual(outcome(convert, *args), expected('test_length_host_boundary:143'))
        for text in ['p', 'Km', ' mi ', ' ', 'invalid']:
            self.assertEqual(outcome(parse, text), expected('test_length_host_boundary:145'))

    def test_loader_and_response_fail_closed(self):
        for library in [object(), mock.Mock(narwhal_pure_core_abi_version=mock.Mock(return_value=99))]:
            with mock.patch.object(native.C, 'CDLL', return_value=library):
                with self.assertRaises(RuntimeError):
                    native._bindings(('env', 'fake-' + str(id(library))))

        class MissingExports:
            narwhal_pure_core_abi_version = mock.Mock(return_value=1)
        with mock.patch.object(native.C, 'CDLL', return_value=MissingExports()):
            with self.assertRaises(RuntimeError):
                native._bindings(('env', 'missing-exports'))
        for status in [-1, 7, 1000]:
            binding = native._Binding(('env', 'fake'), object(), lambda *a: status, lambda *a: status, lambda *a: status)
            with mock.patch.object(native, '_native', return_value=binding):
                for fn, args in [(parse, ('mm',)), (native.angle_factor, ('degrees',)), (native.scale, (1.0, 1.0, 1.0))]:
                    with self.assertRaises(RuntimeError):
                        fn(*args)
        binding = native._native()
        with mock.patch.object(native, 'Path', side_effect=AssertionError('warm Path')), mock.patch.object(native.C, 'CDLL', side_effect=AssertionError('warm load')):
            self.assertIs(native._native(), binding)

    def test_parse_response_metadata_faults(self):

        def fake(rc, ident, bits, payload, size=None):

            def invoke(text, length, out_id, out_mask, raw, cap, out_size):
                C.cast(out_id, C.POINTER(C.c_uint32))[0] = ident
                C.cast(out_mask, C.POINTER(C.c_uint32))[0] = bits
                C.cast(out_size, C.POINTER(C.c_size_t))[0] = len(payload) if size is None else size
                C.memmove(raw, payload, len(payload))
                return rc
            return native._Binding(('env', 'fake'), object(), invoke, None, None)
        faults = [(0, 25, 0, b'mm', None), (0, 0, 1, b'mm', None), (1, 1, 0, b'', None), (1, 0, 1, b'', None), (1, 0, 0, b'm', None), (2, 1, 0, b'mm', None), (2, 0, 1, b'mm', None), (3, 1, 3, b'mm', None), (3, 0, 1, b'mm', None), (3, 0, 1 << 25, b'mm', None), (0, 3, 0, b'mm', 16385), (0, 3, 0, b'mm', 3), (0, 3, 0, b'\xff', None), (0, 3, 0, b'\xc0\x80', None), (0, 3, 0, b'm\x00', None), (0, 3, 0, b' m', None), (0, 3, 0, b'', None), (2, 0, 0, b'm', None), (True, 3, 0, b'mm', None)]
        for values in faults:
            with self.subTest(values=values), mock.patch.object(native, '_native', return_value=fake(*values)):
                with self.assertRaises(RuntimeError):
                    parse(' ' if values[0] == 1 else 'mm')

    def test_scalar_response_faults_and_invocation_errors(self):

        def scalar(rc, value):

            def invoke(*args):
                if value is not None:
                    C.cast(args[-1], C.POINTER(C.c_double))[0] = value
                return rc
            return invoke
        for rc, value in [(0, None), (0, 0.0), (0, -1.0), (0, math.inf), (0, math.nan), (2, 1.0), (6, None), (True, 1.0)]:
            binding = native._Binding(('env', 'fake'), object(), None, scalar(rc, value), None)
            with self.subTest(rc=rc, value=value), mock.patch.object(native, '_native', return_value=binding):
                with self.assertRaises(RuntimeError):
                    native.angle_factor('degrees')
        for rc, value, st in [(6, None, 1.0), (6, 0.0, 0.0), (2, None, 1.0), (0, 1.0, 0.0), (True, 1.0, 1.0)]:
            binding = native._Binding(('env', 'fake'), object(), None, None, scalar(rc, value))
            with mock.patch.object(native, '_native', return_value=binding):
                with self.assertRaises(RuntimeError):
                    native.scale(1.0, 1.0, st)
        for error in [C.ArgumentError('bad'), OSError('bad'), ValueError('bad')]:
            fn = mock.Mock(side_effect=error)
            binding = native._Binding(('env', 'fake'), object(), fn, fn, fn)
            with mock.patch.object(native, '_native', return_value=binding):
                for method, args in [(parse, ('mm',)), (native.angle_factor, ('degrees',)), (native.scale, (1.0, 1.0, 1.0))]:
                    with self.assertRaisesRegex(RuntimeError, 'native invocation failed'):
                        method(*args)

    def test_unicode_gate_in_production(self):
        native._native()
        for version in ['14.0.0', '15.0.0', '16.0.0']:
            with mock.patch.object(native.unicodedata, 'unidata_version', version):
                for fn, args in [(parse, ('mm',)), (native.angle_factor, ('degrees',)), (native.scale, (1.0, 1.0, 1.0))]:
                    with self.assertRaisesRegex(RuntimeError, 'Unicode 15.1.0 required'):
                        fn(*args)

    def test_binding_cache_no_fallback(self):
        b = native._native()
        self.assertIs(b, native._native())
        self.assertIsInstance(b, tuple)
        self.assertIsNotNone(b.library)
        self.assertEqual(b.signature, native._signature())
        for value in ['', '/missing/unit-ops-native.so']:
            with mock.patch.dict(os.environ, {'NARWHAL_PURE_CORE': value}):
                with self.assertRaisesRegex(RuntimeError, 'no Python fallback'):
                    parse('mm')
        self.assertIs(native._native(), b)

        class ForbiddenLock:

            def __enter__(self):
                raise AssertionError('warm lock')

            def __exit__(self, *args):
                return False
        with mock.patch.object(native, '_LOCK', ForbiddenLock()), mock.patch.object(native, 'Path', side_effect=AssertionError('warm Path')), mock.patch.object(native.C, 'CDLL', side_effect=AssertionError('warm CDLL')):
            self.assertIs(native._native(), b)
        with mock.patch.object(native.C, 'CDLL', side_effect=AssertionError('empty env loaded')):
            with self.assertRaises(RuntimeError):
                native._bindings(('env', ''))
        selected = b.signature[1] if b.signature[0] == 'env' else native._PACKAGED
        native._bindings(('env', selected))
        with mock.patch.object(native, '_PACKAGED', selected):
            packaged = native._bindings(('packaged', None))
            self.assertEqual(packaged.signature, ('packaged', None))
            self.assertIs(native._BINDING, packaged)
            self.assertIsNot(packaged, b)
        self.assertEqual(native._native().signature, b.signature)

def load_live():
    name = 'unit_ops_live_public'
    try:
        return load(name, ROOT / 'narwhal3d/units.py')
    except BaseException:
        sys.modules.pop(name, None)
        raise

class LiveUnits(GoldenCase):

    @classmethod
    def setUpClass(cls):
        cls.live = load_live()

    def test_live_public_parse_and_errors(self):
        self.assertEqual(self.live.UNIT_SCALE_SOURCE, 'rust')
        self.assertEqual(self.live.UNIT_MEMBERS, expected('test_live_public_parse_and_errors:278'))
        self.assertEqual(self.live.list_units(), expected('test_live_public_parse_and_errors:279'))
        self.assertEqual(self.live.list_units(False), expected('test_live_public_parse_and_errors:280'))
        self.assertEqual(self.live.ANGLE_FACTORS, expected('test_live_public_parse_and_errors:281'))
        cases = json.loads((FIXTURES / 'unit_live_inputs.txt').read_text())
        for text in cases:
            with self.subTest(text=repr(text)):
                self.assertEqual(outcome(self.live.parse_unit, text), expected('test_live_public_parse_and_errors:294'))
        for cp in [9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288]:
            ch = chr(cp)
            for text in [ch + 'mm' + ch, 'm' + ch + 'm']:
                self.assertEqual(outcome(self.live.parse_unit, text), expected('test_live_public_parse_and_errors:300'))

        class Typed:

            def __init__(self, truth, text):
                self.truth, self.text = (truth, text)

            def __bool__(self):
                return self.truth

            def __str__(self):
                return self.text
        for value in [Typed(True, ' Km '), Typed(False, 'mm'), Typed(True, 'ambiguous')]:
            self.assertEqual(outcome(self.live.parse_unit, value), expected('test_live_public_parse_and_errors:306'))

    def test_live_parse_coercion_order_and_errors(self):

        class Probe:

            def __init__(self, events, truth, text, fail):
                self.events, self.truth, self.text, self.fail = (events, truth, text, fail)

            def __bool__(self):
                self.events.append('bool')
                if self.fail == 'bool':
                    raise ValueError('bool probe')
                return self.truth

            def __str__(self):
                self.events.append('str')
                if self.fail == 'str':
                    raise TypeError('str probe')
                return self.text

        def run(module, truth, text, fail):
            events = []
            result = outcome(module.parse_unit, Probe(events, truth, text, fail))
            return (result, events)
        for truth in [False, True]:
            for text in ['mm', 'p', 'bad', 'Km', None]:
                for fail in [None, 'bool', 'str']:
                    self.assertEqual(run(self.live, truth, text, fail), expected('test_live_parse_coercion_order_and_errors:327'))
        self.assertEqual(run(self.live, False, 'mm', None)[1], ['bool'])
        self.assertEqual(run(self.live, True, 'mm', None)[1], ['bool', 'str'])

    def test_live_convert_all_576_fixed_pairs(self):
        fixed = tuple({'Angstroms': 1e-07, 'Nanometers': 1e-06, 'Microns': 0.001, 'Millimeters': 1.0, 'Centimeters': 10.0, 'Decimeters': 100.0, 'Meters': 1000.0, 'Dekameters': 10000.0, 'Hectometers': 100000.0, 'Kilometers': 1000000.0, 'Megameters': 1000000000.0, 'Gigameters': 1000000000000.0, 'Microinches': 2.54e-05, 'Mils': 0.0254, 'Inches': 25.4, 'Feet': 304.8, 'Yards': 914.4, 'Miles': 1609344.0, 'PrinterPoints': 0.35277777777777775, 'PrinterPicas': 2.1166666666666667, 'NauticalMiles': 1852000.0, 'AstronomicalUnits': 149597870700.0, 'LightYears': 9460730472580800.0, 'Parsecs': 3.085677581491367e+16})
        self.assertEqual(len(fixed) ** 2, 576)
        values = [0.0, -0.0, 1.0, -12.375, 1e+308, 5e-324, math.inf, math.nan, '2.5']
        for a in fixed:
            self.assertEqual(outcome(self.live.unit_scale_mm, a), expected('test_live_convert_all_576_fixed_pairs:336'))
            for b in fixed:
                for value in values:
                    self.assertEqual(outcome(self.live.convert, value, a, b), expected('test_live_convert_all_576_fixed_pairs:339'), (value, a, b))
                self.assertEqual(outcome(self.live.scale_factor, a, b), expected('test_live_convert_all_576_fixed_pairs:340'), (a, b))

    def test_live_convert_custom_invalid(self):
        names = ['Millimeters', 'Inches', 'CustomUnits', 'None', 'Unset', 'bad', None, [], {}]
        for a in names:
            for b in names:
                for custom in [None, 0.0, -0.0, -0.25, 0.125, '2.5', 'bad', math.inf, math.nan]:
                    for value in [1.25, 'bad', None, []]:
                        self.assertEqual(outcome(self.live.convert, value, a, b, custom), expected('test_live_convert_custom_invalid:348'))
            for custom in [None, 0.0, 'bad', 0.125]:
                self.assertEqual(outcome(self.live.unit_scale_mm, a, custom), expected('test_live_convert_custom_invalid:350'))

    def test_live_convert_error_evaluation_order(self):

        class FloatProbe:

            def __init__(self, events, label, value, fail=False):
                self.events, self.label, self.value, self.fail = (events, label, value, fail)

            def __float__(self):
                self.events.append('float:' + self.label)
                if self.fail:
                    raise ValueError('coercion:' + self.label)
                return self.value

            def __repr__(self):
                return '<FloatProbe>'

        def run(module, a, b, value_fail, custom_fail):
            events = []
            value = FloatProbe(events, 'value', 1.25, value_fail)
            custom = FloatProbe(events, 'custom', 0.125, custom_fail)
            resolve = module._resolve

            def observed(name, custom_value=None):
                events.append('resolve:' + name)
                return resolve(name, custom_value)
            with mock.patch.object(module, '_resolve', side_effect=observed):
                result = outcome(module.convert, value, a, b, custom)
            return (result, events)
        for a, b in [('Millimeters', 'Inches'), ('CustomUnits', 'Millimeters'), ('Millimeters', 'CustomUnits'), ('CustomUnits', 'CustomUnits'), ('bad', 'CustomUnits'), ('CustomUnits', 'bad'), ('None', 'Unset')]:
            for vf in [False, True]:
                for cf in [False, True]:
                    self.assertEqual(run(self.live, a, b, vf, cf), expected('test_live_convert_error_evaluation_order:377'))
        self.assertEqual(run(self.live, 'CustomUnits', 'CustomUnits', False, False)[1], ['resolve:CustomUnits', 'float:custom', 'resolve:CustomUnits', 'float:custom', 'float:value'])

    def test_live_angles_nine_pairs_and_host_coercions(self):
        for a in ('degrees', 'radians', 'gradians'):
            for b in ('degrees', 'radians', 'gradians'):
                for value in [0.0, -0.0, 180.0, 1e+308, 5e-324, math.inf, math.nan, '90']:
                    self.assertEqual(outcome(self.live.convert_angle, value, a, b), expected('test_live_angles_nine_pairs_and_host_coercions:385'))
        names = [' DEGREES ', '\x1cRadians\xa0', 'gradians', 'degree', 'd', None, [], 'İ', '\ud800']
        for a in names:
            for b in names:
                for value in [1.0, 'bad', None]:
                    self.assertEqual(outcome(self.live.convert_angle, value, a, b), expected('test_live_angles_nine_pairs_and_host_coercions:390'))

        class Probe:

            def __init__(self, events, label, text, fail=False):
                self.events, self.label, self.text, self.fail = (events, label, text, fail)

            def __float__(self):
                self.events.append('float:' + self.label)
                if self.fail:
                    raise ValueError('float:' + self.label)
                return 90.0

            def __str__(self):
                self.events.append('str:' + self.label)
                if self.fail:
                    raise ValueError('str:' + self.label)
                return self.text

            def __repr__(self):
                self.events.append('repr:' + self.label)
                return '<Probe:' + self.label + '>'

        def run(module, text_a, text_b, fail_v, fail_a, fail_b):
            events = []
            args = [Probe(events, 'value', 'unused', fail_v), Probe(events, 'from', text_a, fail_a), Probe(events, 'to', text_b, fail_b)]
            return (outcome(module.convert_angle, *args), events)
        for a, b in [('DEGREES', 'radians'), ('bad', 'radians'), ('degrees', 'bad')]:
            for fv, fa, fb in [(False, False, False), (True, False, False), (False, True, False), (False, False, True)]:
                self.assertEqual(run(self.live, a, b, fv, fa, fb), expected('test_live_angles_nine_pairs_and_host_coercions:411'))
        self.assertEqual(run(self.live, 'degrees', 'radians', False, False, False)[1], ['float:value', 'str:from', 'str:to'])

    def test_live_delegation_and_no_replay(self):
        adapter = self.live._unit_adapter('native_unit_ops')
        for method, args, adapter_name, expected_args in [(self.live.parse_unit, ('not a Python-accepted unit',), 'parse_unit', ('not a Python-accepted unit', tuple(['Angstroms', 'Nanometers', 'Microns', 'Millimeters', 'Centimeters', 'Decimeters', 'Meters', 'Dekameters', 'Hectometers', 'Kilometers', 'Megameters', 'Gigameters', 'Microinches', 'Mils', 'Inches', 'Feet', 'Yards', 'Miles', 'PrinterPoints', 'PrinterPicas', 'NauticalMiles', 'AstronomicalUnits', 'LightYears', 'Parsecs', 'CustomUnits']))), (self.live.convert, (2.0, 'Millimeters', 'Inches'), 'scale', (2.0, 1.0, 25.4)), (self.live.convert_angle, (90.0, 'degrees', 'radians'), 'convert_angle', (90.0, 'degrees', 'radians'))]:
            sentinel = object()
            with mock.patch.object(adapter, adapter_name, return_value=sentinel) as called:
                self.assertIs(method(*args), sentinel)
                called.assert_called_once_with(*expected_args)
            with mock.patch.object(adapter, adapter_name, side_effect=RuntimeError('native-only-probe')) as called:
                with self.assertRaisesRegex(RuntimeError, 'native-only-probe'):
                    method(*args)
                called.assert_called_once()
        binding = adapter._native()
        counted = binding._replace(parse=mock.Mock(wraps=binding.parse), angle=mock.Mock(wraps=binding.angle), scale=mock.Mock(wraps=binding.scale))
        with mock.patch.object(adapter, '_native', return_value=counted):
            self.live.parse_unit('mm')
            self.live.convert(2.0, 'Millimeters', 'Inches')
            self.live.convert_angle(90.0, 'degrees', 'radians')
        self.assertEqual((counted.parse.call_count, counted.angle.call_count, counted.scale.call_count), (1, 2, 2))

    def test_live_missing_native_fails_import_and_operations(self):
        adapter = self.live._unit_adapter('native_unit_ops')
        operations = [(self.live.parse_unit, ('mm',)), (self.live.convert, (1.0, 'Millimeters', 'Inches')), (self.live.convert_angle, (90.0, 'degrees', 'radians'))]
        for path in ['', '/missing/live-unit-ops-library.so']:
            with mock.patch.dict(os.environ, {'NARWHAL_PURE_CORE': path}):
                with self.assertRaises(RuntimeError):
                    load_live()
                for method, args in operations:
                    with self.assertRaises(RuntimeError):
                        method(*args)

        class MissingOps:
            narwhal_pure_core_abi_version = mock.Mock(return_value=1)
        with mock.patch.dict(os.environ, {'NARWHAL_PURE_CORE': '/fake/has-no-unit-ops.so'}), mock.patch.object(adapter.C, 'CDLL', return_value=MissingOps()):
            with self.assertRaises(RuntimeError):
                load_live()
            for method, args in operations:
                with self.assertRaisesRegex(RuntimeError, 'library/version/export unavailable'):
                    method(*args)
        self.assertEqual(self.live.parse_unit('mm'), 'Millimeters')
if __name__ == '__main__':
    unittest.main()
