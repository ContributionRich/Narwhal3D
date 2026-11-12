"""Required native bearing routes, independent oracle, and compatibility ABI.

Default: qualify live bearing.py with NARWHAL_PURE_CORE. For pre-activation
qualification pass --module tmp/rust12-bearing-candidate.py and an external
scratch library. Missing/stale/broken native routes must NEVER replay Python.
Timing is reported, not used to select a Python/native routing band.
"""
import argparse
import ctypes
import importlib.util
import os
from pathlib import Path
import sys
import time
from contextlib import contextmanager
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


args = argparse.ArgumentParser(description=__doc__)
args.add_argument('--module', type=Path, default=ROOT / 'narwhal3d/bearing.py')
options = args.parse_args()
brg = _load('narwhal_native_test_bearing', options.module.resolve())
reference = _load('narwhal_native_bearing_reference', ROOT / 'spike/bearing_reference.py')
native = getattr(brg, 'native_bearing', None)
if native is None:
    native = brg._adapter()
compat = _load('narwhal_native_bearing_compat', ROOT / 'narwhal3d/native_pure.py')
results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, 'PASS', ''))
        except Exception as error:
            results.append((name, 'FAIL', repr(error)))
    return deco


def _reset():
    # Owned adapter caches its library/binding; no changes to production helpers.
    native._binding = native._signature = native._library = None


@contextmanager
def _native_calls(expected):
    """Count actual ABI calls even if a future adapter drops its debug counter."""
    binding = native._required_binding()
    calls = []
    before = getattr(native, '_native_bearing_calls', None)

    def counted(*args):
        calls.append(1)
        return binding(*args)

    with patch.object(native, '_required_binding', return_value=counted):
        yield
    assert len(calls) == expected, ('ABI call count', len(calls), expected)
    if before is not None:
        assert native._native_bearing_calls - before == expected, (
            'native classified-reply counter', before, native._native_bearing_calls, expected)


def _runtime_error(fn, text):
    try:
        fn(text)
    except RuntimeError as error:
        assert 'replayed' in str(error), str(error)
    else:
        raise AssertionError(('required RuntimeError, not Python replay', text))


DMS = 30.0 + 22.0 / 60.0 + 54.43 / 3600.0
VALID_BEARING = [
    ('N0E', 90.0), ('N90E', 0.0), ('N90W', 180.0), ('S0W', 270.0),
    ('S90E', 0.0), ('n30e', 60.0), ('n30w', 120.0), ('s30e', 300.0),
    ('s30w', 240.0), ('N45W', 135.0), ('S45W', 225.0), ('S45E', 315.0),
    ('N30W', 120.0), ('N30.5E', 59.5), ('N 30 E', 60.0), ('S 45 W', 225.0),
    ("N30d22'W", 90.0 + 30.0 + 22.0 / 60.0),
    ('N30d22\'54.43"W', 90.0 + DMS),
]
VALID_SURVEYOR = [
    ('11<N30d22\'54.43"W', 11.0, 90.0 + DMS),
    ('5<N45E', 5.0, 45.0), ('2.5<S30W', 2.5, 240.0),
]
BEARING_REJECTED = ['N95E', 'E30N', 'N30', '30W', 'N-5E', 'NabcE', 'N30wE', 'N E', '']
SURVEYOR_REJECTED = ['5<N95E', '5<E30N', '5<N30', '5<30W', 'N30E', '11<', '']
LONG_BEARING = ' ' * 124 + 'N30E'


@check('required core ABI 1 and bearing ABI 2 are present, with real parse results')
def _():
    _reset()
    binding = native._required_binding()
    assert ctypes.sizeof(native.BearingReply) == 32
    reply = native.BearingReply(code=99)
    source = (ctypes.c_uint32 * 4)(*map(ord, 'N30E'))
    assert binding(source, 4, 0, 151, ctypes.byref(reply), 32) == 0
    assert (reply.code, reply.value, reply.distance, reply.reserved,
            reply.start, reply.end) == (0, 60.0, 0.0, 0, 0, 0)


@check('standalone short/long bearings and surveyor all require native calls')
def _():
    with _native_calls(3):
        assert brg.bearing_angle('N30E') == (60.0, None)
        assert brg.bearing_angle(LONG_BEARING) == (60.0, None)
        assert brg.parse_surveyor('5<N45E') == ((5.0, 45.0), None)


@check('native successes exactly match independent Python oracle and quadrant pins')
def _():
    with _native_calls(len(VALID_BEARING) + len(VALID_SURVEYOR)):
        for text, want in VALID_BEARING:
            actual = brg.bearing_angle(text)
            expected = reference.bearing_angle(text)
            assert expected[0] is not None and expected[1] is None, (text, expected)
            assert actual == expected, (text, actual, expected)
            assert actual[0].hex() == expected[0].hex(), (text, actual, expected)
            # Historical hand-computed DMS pin uses a different addition order.
            assert abs(actual[0] - want) <= 1e-12, (text, actual, want)
        for text, dist, want in VALID_SURVEYOR:
            actual = brg.parse_surveyor(text)
            expected = reference.parse_surveyor(text)
            assert expected[0] is not None and expected[1] is None, (text, expected)
            assert actual == expected, (text, actual, expected)
            assert actual[0][0].hex() == expected[0][0].hex()
            assert actual[0][1].hex() == expected[0][1].hex()
            assert actual[0][0] == dist and abs(actual[0][1] - want) <= 1e-12


@check('all grammar rejections match independent oracle and execute native')
def _():
    with _native_calls(len(BEARING_REJECTED) + len(SURVEYOR_REJECTED)):
        for name, texts in [('bearing_angle', BEARING_REJECTED),
                            ('parse_surveyor', SURVEYOR_REJECTED)]:
            for text in texts:
                expected = getattr(reference, name)(text)
                assert expected[0] is None and isinstance(expected[1], str) and expected[1]
                actual = getattr(brg, name)(text)
                assert actual == expected, (name, text, actual, expected)


@check('exact error contract and Unicode/capture edge cases require native')
def _():
    pins = [
        ('N95E', 'quadrant angle must be in [0, 90]: 95'),
        ('N30', "not a bearing (need N/S + angle + E/W): 'N30'"),
        ('NabcE', "not an angle: 'abc'"), ('N-5E', "not an angle: '-5'"),
        ('N  bad  E', "not an angle: 'bad  '"),
    ]
    extra = ['ſ30E', '\u2003N٣٠E\u0085', 'N30d22\'"W', 'N\nE']
    surveyor = ['1<N30E\n', '1<N30E\n\n', '1<\n ', '1< N bad E  ']
    with _native_calls(len(pins) + len(extra) + len(surveyor)):
        for text, want in pins:
            assert brg.bearing_angle(text) == (None, want), (text, want)
        for text in extra:
            assert brg.bearing_angle(text) == reference.bearing_angle(text), text
        for text in surveyor:
            assert brg.parse_surveyor(text) == reference.parse_surveyor(text), repr(text)


@check('legacy native_pure direct ABI remains compatibility-only, not public routing')
def _():
    compat._reset_for_tests()
    assert compat._bearing_binding() is not None, 'old exports required for coords compatibility'
    before = compat._native_bearing_calls
    for text, want in VALID_BEARING:
        value = compat._native_bearing_angle(text)
        assert value is not None and abs(value - want) <= 1e-12, (text, value, want)
    for text, distance, want in VALID_SURVEYOR:
        value = compat._native_parse_surveyor(text)
        assert value is not None and value[0] == distance and abs(value[1] - want) <= 1e-12
    assert compat._native_bearing_calls - before == len(VALID_BEARING) + len(VALID_SURVEYOR)
    before = compat._native_bearing_calls
    for text in BEARING_REJECTED:
        assert compat._native_bearing_angle(text) is None, text
    for text in SURVEYOR_REJECTED:
        assert compat._native_parse_surveyor(text) is None, text
    assert compat._native_bearing_calls == before
    with _native_calls(2):
        assert brg.bearing_angle('N30E') == (60.0, None)
        assert brg.parse_surveyor('5<N45E') == ((5.0, 45.0), None)
    assert compat._native_bearing_calls == before, 'public API used compatibility parser'


def _failure_inputs():
    return [(brg.bearing_angle, 'n30e'), (brg.bearing_angle, 'N95E'),
            (brg.parse_surveyor, '5<N45E'), (brg.parse_surveyor, '5<N95E')]


@check('missing library raises RuntimeError for successes and grammar rejections')
def _():
    _reset()
    before = getattr(native, '_native_bearing_calls', None)
    try:
        with patch.dict(os.environ, NARWHAL_PURE_CORE='/no/such/native-bearing-library.so'):
            for fn, text in _failure_inputs():
                _runtime_error(fn, text)
        assert getattr(native, '_native_bearing_calls', None) == before
    finally:
        _reset()


@check('missing symbols and stale core/specific ABI raise RuntimeError, never replay')
def _():
    class Function:
        def __init__(self, value): self.value = value
        def __call__(self): return self.value

    class FakeLibrary:
        def __init__(self, core, bearing):
            self.narwhal_pure_core_abi_version = Function(core)
            self.narwhal_pure_core_bearing_abi2 = Function(bearing)

    before = getattr(native, '_native_bearing_calls', None)
    try:
        for library in [object(), FakeLibrary(0, 2), FakeLibrary(1, 1), FakeLibrary(1, 2)]:
            with patch.object(native.ctypes, 'CDLL', return_value=library):
                for fn, text in _failure_inputs():
                    _reset()
                    _runtime_error(fn, text)
        assert getattr(native, '_native_bearing_calls', None) == before
    finally:
        _reset()


@check('injected call failures and invalid replies raise RuntimeError, never replay')
def _():
    def bad_reply(field, value):
        def call(source, length, mode, unicode, out, size):
            reply = ctypes.cast(out, ctypes.POINTER(native.BearingReply)).contents
            reply.code = 0
            setattr(reply, field, value)
            return 0
        return call

    def failed(*args): return 2
    def missing(*args): return None
    def exploded(*args): raise OSError('injected native failure')

    cases = [failed, missing, exploded, bad_reply('code', 99),
             bad_reply('value', float('nan')), bad_reply('value', float('inf')),
             bad_reply('value', 360.0), bad_reply('reserved', 1),
             bad_reply('end', 4097), bad_reply('start', 1)]
    before = getattr(native, '_native_bearing_calls', None)
    for binding in cases:
        with patch.object(native, '_required_binding', return_value=binding):
            for fn, text in _failure_inputs():
                _runtime_error(fn, text)
    assert getattr(native, '_native_bearing_calls', None) == before


@check('measured native/reference costs; no speed threshold or routing exemption')
def _():
    count = 3000
    for name, texts in [('bearing_angle', ['N30E', LONG_BEARING, 'S45W', 'N95E']),
                        ('parse_surveyor', ['5<N45E', '12.5<S30W', '100<N0E', '5<N95E'])]:
        def measure(fn):
            best = float('inf')
            for _ in range(3):
                start = time.perf_counter()
                for i in range(count):
                    fn(texts[i % len(texts)])
                best = min(best, time.perf_counter() - start)
            return best
        # Do not include the monkeypatch/Mock overhead in the timing sample.
        with _native_calls(1):
            getattr(brg, name)(texts[0])
        before = getattr(native, '_native_bearing_calls', None)
        elapsed = measure(getattr(brg, name))
        if before is not None:
            assert native._native_bearing_calls - before == 3 * count
        baseline = measure(getattr(reference, name))
        print('  [perf] %s: required-native %.3f us/call; reference %.3f us/call; ratio %.2fx'
              % (name, elapsed * 1e6 / count, baseline * 1e6 / count, elapsed / baseline))


failed = 0
for name, status, detail in results:
    print('[%s] %s%s' % (status, name, ' -- ' + detail if detail else ''))
    failed += status == 'FAIL'
print('\n%d/%d passed (%s)' % (len(results) - failed, len(results), options.module))
sys.exit(1 if failed else 0)
