"""Macro host qualification: fixed original answers, no Python grammar replay.

Computation/Unicode/seeded loops live in Rust macro_golden. Original benchmarks
and mutation tooling are private only; no test-time native compilation.
"""
import ctypes
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager, ExitStack
from unittest.mock import patch
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_CANDIDATES = [os.environ.get('NARWHAL_PURE_CORE', ''), os.path.join(ROOT, 'native', 'pure_core', 'target', 'x86_64-unknown-linux-gnu', 'release', 'libnarwhal_pure_core.so'), os.path.join(ROOT, 'native', 'pure_core', 'target', 'release', 'libnarwhal_pure_core.so')]

def _ensure_lib():
    for candidate in LIB_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise RuntimeError('Required current macro planner library missing; no test-time build')
_LIB = _ensure_lib()
os.environ['NARWHAL_PURE_CORE'] = _LIB

def _load(name, directory, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, directory, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
macro = _load('narwhal_native_test_macro_for_plan', 'narwhal3d', 'macro.py')
sys.modules['macro'] = macro
plan_mod = _load('narwhal_native_test_macro_plan', 'narwhal3d', 'macro_plan.py')
plan_macro = plan_mod.plan_macro
MacroPlanError = plan_mod.MacroPlanError
NonNestableCommandError = plan_mod.NonNestableCommandError
if not hasattr(plan_mod, '_native_macro_plan_adapter'):
    raise RuntimeError('Required checked macro planner production candidate is not activated')
native = plan_mod._native_macro_plan_adapter()
compat = macro.native_pure
oracle = _load('narwhal_test_macro_reference', 'spike', 'macro_reference.py')
assert not hasattr(oracle, '_split_tokens'), 'fixed answers must contain no tokenizer'
results = []
TMP = tempfile.mkdtemp(prefix='native_macro_plan_')

def check(name):

    def deco(fn):
        try:
            fn()
            results.append((name, 'PASS', ''))
        except Exception as error:
            results.append((name, 'FAIL', repr(error)))
    return deco

def _reset():
    native._reset_for_tests()
    compat._reset_for_tests()

def _assert_native_ran(before, label):
    assert native._native_macro_plan_calls > before, (label, 'checked native call did not run', before, native._native_macro_plan_calls)
planner_answers = _load('macro_plan_fixed', 'spike', 'macro_plan_reference.py')

def python_reference(text):
    try:
        return planner_answers.plan_macro(text)
    except planner_answers.NonNestableCommandError as error:
        raise NonNestableCommandError(str(error)) from error
    except planner_answers.MacroPlanError as error:
        raise MacroPlanError(str(error)) from error

@contextmanager
def _no_python_replay():
    """Load-bearing guard: success, typed refusal and faults cannot replay."""
    with ExitStack() as stack:
        for module, name in [(plan_mod, 'tokenize'), (plan_mod, 'build_plan'), (macro, 'tokenize'), (compat, '_native_macro_plan'), (compat, '_native_macro_plan_packed')]:
            stack.enter_context(patch.object(module, name, side_effect=AssertionError('production Python/legacy planner replay: ' + name), create=True))
        yield

@contextmanager
def _library_path(path):
    with patch.dict(os.environ, {'NARWHAL_PURE_CORE': path}):
        _reset()
        try:
            yield
        finally:
            _reset()

def _raises(kind, fn):
    try:
        fn()
    except kind as error:
        return error
    raise AssertionError('expected ' + kind.__name__)
VALID_MACROS = ["! '-_Zoom", "'_Zoom '_CPlane", "'_SelWindow", "NoEcho '_Pan 5", '!&_Delete', '*_RotateView', '~_Tilt 10', '#ShowP', '%HideP', '_-_SetDisplayMode _Mode=_Shaded', '_-_SetDisplayMode _Mode=_Shaded "My Mode"', '_Pause _MultiPause _Enter _EnterEnd', "'_CPlane _3Point\n_Pause _Pause _Pause _Enter", "; comment line\n'_Ortho\n_Zoom 100", '_Move !', '! _-SelName "Table 01" Enter', '']
LONG_MACRO = ('! _Line _Pause 0,0 _Enter ' * 8).strip()
CASEFOLD_MACROS = ["'_ſnap", "'_selcroßing"]
NON_NESTABLE = ["'_Box", "'_Box _Pause", "'_Circle 5,5 10"]
INVALID_OPTIONS = ['=value', '_Zoom =value', "=value '_Box"]

@check('required checked ABI resolves; legacy resolver remains compatibility only')
def _():
    assert callable(native._checked_binding()), 'checked planner export is required'
    assert plan_mod._native_macro_plan_adapter() is native
    assert compat is not native
    assert callable(compat._macro_plan_binding()[0])
    assert callable(compat._macro_plan_packed_binding())

@check('empty, short, long and beyond-former-ceiling strings all call checked Rust')
def _():
    _reset()
    texts = ['', '_Line', LONG_MACRO, '_Line ' * 1000]
    expected = [python_reference(text) for text in texts]
    real = native._checked_binding()
    calls = []

    def counted(*args):
        calls.append(args)
        return real(*args)
    with patch.object(native, '_checked_binding', return_value=counted), _no_python_replay():
        for text, want in zip(texts, expected):
            before = native._native_macro_plan_calls
            count_before = len(calls)
            assert plan_macro(text) == want, text
            _assert_native_ran(before, repr(text[:40]))
            assert native._native_macro_plan_calls - before == len(calls) - count_before
            if text in ('', '_Line'):
                assert len(calls) - count_before == 1
    assert native._native_macro_plan_calls == len(calls)
    assert compat._native_macro_plan_calls == 0

@check('checked planner equals independent baseline for the full valid corpus')
def _():
    _reset()
    for text in VALID_MACROS + [LONG_MACRO] + CASEFOLD_MACROS:
        before = native._native_macro_plan_calls
        expected = python_reference(text)
        assert native._native_macro_plan_calls == before, 'oracle touched product planner'
        with _no_python_replay():
            actual = plan_macro(text)
        assert actual == expected, (text, actual, 'expected', expected)
        _assert_native_ran(before, text)

@check('native plan prefixes remain tuples on short and long string routes')
def _():
    _reset()
    with _no_python_replay():
        for text in VALID_MACROS + [LONG_MACRO]:
            before = native._native_macro_plan_calls
            for action in plan_macro(text):
                if 'prefixes' in action:
                    assert isinstance(action['prefixes'], tuple), action
            _assert_native_ran(before, text)

@check('the exact action structure holds on the native route')
def _():
    _reset()
    got = plan_macro("! '-_Zoom")
    assert got == [{'action': 'cancel', 'prefixes': ('!',)}, {'action': 'command', 'name': 'Zoom', 'prefixes': ("'", '-', '_'), 'dash': True, 'underscore': True, 'transparent': True}], got
    got = plan_macro('_-_SetDisplayMode _Mode=_Shaded "My Mode"')
    assert got == [{'action': 'command', 'name': 'SetDisplayMode', 'prefixes': ('_', '-', '_'), 'dash': True, 'underscore': True, 'transparent': False}, {'action': 'option', 'name': 'Mode', 'value': 'Shaded', 'prefixes': ('_',)}, {'action': 'text', 'value': 'My Mode'}], got
    got = plan_macro('!&_Delete')
    assert got == [{'action': 'cancel', 'prefixes': ('!',)}, {'action': 'command', 'name': 'Delete', 'prefixes': ('!', '&', '_'), 'dash': False, 'underscore': True, 'transparent': False, 'history_lock_off': True}], got

@check('transparent geometry remains typed NonNestableCommandError, native-owned')
def _():
    _reset()
    for text in NON_NESTABLE + ["'_Box " + LONG_MACRO]:
        expected = _raises(NonNestableCommandError, lambda: python_reference(text))
        before = native._native_macro_plan_calls
        with _no_python_replay():
            actual = _raises(NonNestableCommandError, lambda: plan_macro(text))
        assert type(actual) is type(expected) and str(actual) == str(expected), (text, actual, expected)
        _assert_native_ran(before, text)
    for text, name in [("'_Box", 'Box'), ("'_Circle", 'Circle')]:
        before = native._native_macro_plan_calls
        error = _raises(native.NativeMacroPlanError, lambda: native.plan_macro(text))
        assert (error.code, error.name) == (-4, name), (error.code, error.name)
        _assert_native_ran(before, text)

@check('empty option names preserve MacroPlanError and native first-error order')
def _():
    _reset()
    for text in INVALID_OPTIONS + ["'_Box =value", LONG_MACRO + ' =value']:
        expected = _raises(MacroPlanError, lambda: python_reference(text))
        before = native._native_macro_plan_calls
        with _no_python_replay():
            actual = _raises(MacroPlanError, lambda: plan_macro(text))
        assert type(actual) is type(expected) and str(actual) == str(expected), (text, actual, expected)
        _assert_native_ran(before, text)
    error = _raises(native.NativeMacroPlanError, lambda: native.plan_macro('=value'))
    assert (error.code, error.name) == (-5, None)

@check('non-string macro is refused before any native call')
def _():
    _reset()
    for value in (None, 5, ['_Move']):
        with _no_python_replay():
            _raises(MacroPlanError, lambda: plan_macro(value))
    assert native._native_macro_plan_calls == 0

@check('public build_plan(tokens) remains a separate explicit compatibility API')
def _():
    _reset()
    tokens = oracle.tokenize("! '-_Zoom", lossless=True)
    expected = python_reference("! '-_Zoom")
    with patch.object(native, 'plan_macro', side_effect=AssertionError('builder called string planner')):
        assert plan_mod.build_plan(tokens) == expected
    assert native._native_macro_plan_calls == 0

@check('missing/unloadable whole core refuses every string without replay')
def _():
    empty = os.path.join(TMP, 'empty.so')
    with open(empty, 'wb'):
        pass
    for path in [os.path.join(TMP, 'missing.so'), empty]:
        with _library_path(path), _no_python_replay():
            assert native._checked_binding() is None
            for text in ['', '_Line', LONG_MACRO, "'_Box", '=value']:
                _raises(RuntimeError, lambda: plan_macro(text))
            assert native._native_macro_plan_calls == 0

@check('stale core identity or absent checked export refuses every string')
def _():

    class Identity:

        def __init__(self, value):
            self.value = value

        def __call__(self):
            return self.value

    class Stale:
        narwhal_pure_core_abi_version = Identity(999)
        narwhal_pure_core_macro_plan_checked = Identity(0)

    class Missing:
        narwhal_pure_core_abi_version = Identity(1)
    for library in [Stale(), Missing()]:
        with _library_path(_LIB), patch.object(native.ctypes, 'CDLL', return_value=library):
            with _no_python_replay():
                for text in ['', '_Line', LONG_MACRO, "'_Box", '=value']:
                    _raises(RuntimeError, lambda: plan_macro(text))
                assert native._native_macro_plan_calls == 0

@check('checked binding None never invokes tokenize/build_plan even if tokenizer works')
def _():
    _reset()
    assert macro.tokenize('_Zoom', lossless=True)
    with patch.object(native, '_checked_binding', return_value=None), _no_python_replay():
        for text in ['', '_Zoom', LONG_MACRO, "'_Box", '=value']:
            _raises(RuntimeError, lambda: plan_macro(text))
    assert native._native_macro_plan_calls == 0

@check('invalid checked JSON/status/framing raises RuntimeError, no valid fallback')
def _():

    def reply(payload, status=0, needed=None):

        def call(_text, _length, out, capacity, out_needed):
            size = len(payload) if needed is None else needed
            ctypes.cast(out_needed, ctypes.POINTER(ctypes.c_int64))[0] = size
            if payload and len(payload) <= capacity:
                ctypes.memmove(out, payload, len(payload))
            return status
        return call
    malformed = b'[{"action":"command","name":"Zoom","prefixes":["_"],"dash":0,"underscore":true,"transparent":false}]'
    cases = [reply(b'not json'), reply(b'{}'), reply(malformed), reply(b'[]', status=-1), reply(b'[]', needed=0), reply(b'', status=1, needed=1), reply(b'{}', status=-4), reply(b'""', status=-4), reply(b'', status=-5, needed=1)]
    _reset()
    for binding in cases:
        with patch.object(native, '_checked_binding', return_value=binding), _no_python_replay():
            for text in ['_Zoom', "'_Box", '=value', '']:
                before = native._native_macro_plan_calls
                _raises(RuntimeError, lambda: plan_macro(text))
                assert native._native_macro_plan_calls == before + 1, 'attempted ABI calls count'

@check('compatibility ONLY: legacy JSON ABI equals independent planner on valid corpus')
def _():
    _reset()
    assert compat._macro_plan_binding() is not None
    for text in VALID_MACROS + [LONG_MACRO] + CASEFOLD_MACROS:
        before = compat._native_macro_plan_calls
        payload = compat._native_macro_plan(text)
        assert payload is not None, text
        assert plan_mod._actions_from_json(payload) == python_reference(text), text
        assert compat._native_macro_plan_calls == before + 1
    assert native._native_macro_plan_calls == 0

@check('compatibility ONLY: legacy JSON and packed ABIs retain all semantic refusals')
def _():
    _reset()
    for direct in [compat._native_macro_plan, compat._native_macro_plan_packed]:
        for text in NON_NESTABLE + INVALID_OPTIONS:
            assert direct(text) is None, (direct.__name__, text)
    assert compat._native_macro_plan_calls == 0
    assert native._native_macro_plan_calls == 0

@check('compatibility ONLY: malformed successful JSON is declined without counting')
def _():
    _reset()
    payload = b'[{"action":"command","name":"Zoom","prefixes":["_"],"dash":0,"underscore":true,"transparent":false}]'

    def size_fn(_text, _length):
        return len(payload)

    def encode_fn(_text, _length, out, capacity):
        assert capacity == len(payload)
        ctypes.memmove(out, payload, len(payload))
        return 0
    with patch.object(compat, '_macro_plan_binding', return_value=(size_fn, encode_fn)):
        assert compat._native_macro_plan('_Zoom') is None
    assert compat._native_macro_plan_calls == 0

@check('compatibility ONLY: packed ABI counter, refusals and raw converters remain exact')
def _():
    _reset()
    assert compat._macro_plan_packed_binding() is not None
    packed = compat._native_macro_plan_packed("! '-_Zoom")
    assert packed is not None and packed[6] == 2, packed
    assert compat._native_macro_plan_calls == 1
    assert compat._native_macro_plan_packed("'_Box") is None
    assert compat._native_macro_plan_calls == 1
    for text in VALID_MACROS + [LONG_MACRO] + CASEFOLD_MACROS:
        packed = compat._native_macro_plan_packed(text)
        assert packed is not None, text
        actual = plan_mod._actions_from_packed(packed)
        assert actual == python_reference(text), (text, actual)
        for action in actual:
            if 'prefixes' in action:
                assert isinstance(action['prefixes'], tuple), action
    assert native._native_macro_plan_calls == 0

@check('checked raw ABI: short buffers do not write, typed refusals and spans stay distinct')
def _():
    binding = native._checked_binding()
    needed = ctypes.c_int64(-1)
    data = b'_Zoom'
    output = (ctypes.c_ubyte * 512)(*[165] * 512)
    assert binding(data, len(data), output, 1, ctypes.byref(needed)) == 1
    assert needed.value > 1 and bytes(output) == b'\xa5' * 512
    assert binding(data, len(data), output, 512, ctypes.byref(needed)) == 0
    assert isinstance(json.loads(bytes(output[:needed.value])), list)
    for data, status in [(b"'_Box", -4), (b'=value', -5), (b'\xff', -2)]:
        result = binding(data, len(data), output, 512, ctypes.byref(needed))
        assert result == status, (data, result, status)
        if status == -4:
            assert json.loads(bytes(output[:needed.value])) == 'Box'
        else:
            assert needed.value == 0
    assert binding(None, 1, output, 512, ctypes.byref(needed)) == -1
    assert binding(b'_Zoom', 5, output, 512, None) == -1
failed = 0
for name, status, detail in results:
    print(f'[{status}] {name}' + (f'  -- {detail}' if detail else ''))
    if status == 'FAIL':
        failed += 1
shutil.rmtree(TMP, ignore_errors=True)
print(f'\n{len(results) - failed}/{len(results)} passed')
sys.exit(1 if failed else 0)
