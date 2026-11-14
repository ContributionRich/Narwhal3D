"""Macro host qualification: fixed original answers, no Python grammar replay.

Computation/Unicode/seeded loops live in Rust macro_golden. Original benchmarks
and mutation tooling are private only; no test-time native compilation.
"""
import ctypes
import importlib.util
import os
import sys
from contextlib import contextmanager
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_CANDIDATES = [os.environ.get('NARWHAL_PURE_CORE', ''), os.path.join(ROOT, 'native', 'pure_core', 'target', 'x86_64-unknown-linux-gnu', 'release', 'libnarwhal_pure_core.so'), os.path.join(ROOT, 'native', 'pure_core', 'target', 'release', 'libnarwhal_pure_core.so')]

def _locate_lib():
    for cand in LIB_CANDIDATES:
        if cand and os.path.isfile(cand):
            return cand
    return None
_LIB = _locate_lib()
if _LIB is not None:
    os.environ['NARWHAL_PURE_CORE'] = _LIB

def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, 'narwhal3d', filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

def _load_oracle():
    spec = importlib.util.spec_from_file_location('native_test_macro_oracle', os.path.join(ROOT, 'spike', 'macro_reference.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['native_test_macro_oracle'] = mod
    spec.loader.exec_module(mod)
    return mod
ORACLE = _load_oracle()
macro = _load('narwhal_native_test_macro', 'macro.py')
tokenize = macro.tokenize
MacroError = macro.MacroError
native = macro._native_macro()
results = []

def check(name):

    def deco(fn):
        try:
            fn()
            results.append((name, 'PASS', ''))
        except Exception as e:
            results.append((name, 'FAIL', repr(e)))
    return deco

def _reset():
    """Forget discovery + counter state so a check can re-point the binding."""
    if native is not None:
        native._reset_for_tests()

def _assert_native_ran(before, label):
    """Tripwire: the counter must have moved (the native route actually ran)."""
    assert native is not None, 'adapter did not resolve the native module'
    assert native._native_macro_calls > before, (label, 'native route did not run (%d -> %d)' % (before, native._native_macro_calls))
VALID_MACROS = [('! _Line _Pause 0,0 _Enter', False), ('! _Line _Pause 0,0 _Enter', True), ('-_SetDisplayMode _Mode=_Shaded', False), ('-_SetDisplayMode _Mode=_Shaded', True), ("'_Zoom", False), ('!&_Delete', False), ('!&_Delete', True), ('*_Circle', False), ('~_Circle', False), ('#Circle', False), ('%Circle', False), ('_3Point', False), ('-15,0,0', False), ('_Points=10', False), ('_Points=10', True), ('"My Mode"', False), ('_-SelName "Table 01"', False), ('_-SelName Table 01', False), ('_Mode=_Shaded', False), ('_Mode="Shaded"', False), ('_Line 0,0,0 pause ;15,0,0', False), ('; This is a test macro\n_Circle 0,0,0 15', False), ('_Pause', False), ('_Pause', True), ('_MultiPause', True), ('_Enter', False), ('_EnterEnd', True), ('NoEcho', False), ('Run', False), ('NoEcho _Circle 5,5 10', False), ('! _-Circle 5,5 10', False), ('! _SelNone _Move', False), ('! _-Layer New "Layer 01:: New Layer" Enter', False), ("'_CPlane _3Point\n_Pause _Pause _Pause _Enter", False), ("'_CPlane _3Point\n_Pause _Pause _Pause _Enter", True), ('! _-DocumentProperties _Mesh _Custom _MaxEdgeSrf .01 _EnterEnd', False), ('-ReadCommandFile "my file.txt"', False), ('', False), ('   \n\t ', False), ('!', False), ('_Move !', False), ('!_Move', False)]
SHORT_MACRO = '_Line'
LONG_MACRO = ('! _Line _Pause 0,0 _Enter ' * 10).strip()
LARGE_MACRO = ('! _Line _Pause 0,0 _Enter \'*Zoom -_SelName "A 01" _Mode=_Shaded _Points=10 ' * 120).strip()
TEXT_CANCEL = ['!123', '!3Point', '!&', '!-15']

@check('native library is built, discovered, and exposes the macro ABI')
def _():
    assert _LIB is not None, 'pure_core .so not present and build failed'
    assert native is not None, 'adapter did not resolve the native module'
    binding = native._macro_binding()
    assert binding is not None and len(binding) == 2 and callable(binding[0]) and callable(binding[1]), 'the macro binding must resolve (size, encode)'
    assert hasattr(native, '_native_macro_calls')

@check('the packed one-pass macro ABI (Q117-04) is present and resolvable')
def _():
    _reset()
    packed = native._macro_packed_binding()
    assert packed is not None, 'the packed macro export must resolve'
    assert callable(packed), 'the packed handle must be callable'
    assert native._macro_packed_native_active(LONG_MACRO) is True
    assert native._macro_packed_native_active(5) is False, 'a non-string is never a packed-route candidate'

@check("a SHORT macro is native (the counter moves) -- no 'short stays Python' band")
def _():
    _reset()
    before = native._native_macro_calls
    assert isinstance(tokenize(SHORT_MACRO), list)
    assert native._native_macro_calls == before + 1, 'short macro must be native: counter %d -> %d' % (before, native._native_macro_calls)

@check('a LONG macro is native (the counter moves)')
def _():
    _reset()
    before = native._native_macro_calls
    assert isinstance(tokenize(LONG_MACRO, lossless=True), list)
    _assert_native_ran(before, 'long macro')

@check('native equals the pure-Python reference on the SAME valid input')
def _():
    _reset()
    for text, lossless in VALID_MACROS:
        got_native = tokenize(text, lossless=lossless)
        got_ref = ORACLE.tokenize(text, lossless=lossless)
        assert got_ref is not None, (text, lossless, got_ref)
        assert got_native == got_ref, (text, lossless, got_native, 'expected', got_ref)

@check('lossless native prefixes are TUPLES (the Python contract)')
def _():
    _reset()
    before = native._native_macro_calls
    got = tokenize(LONG_MACRO, lossless=True)
    for step in got:
        if 'prefixes' in step:
            assert isinstance(step['prefixes'], tuple), step
    _assert_native_ran(before, 'macro lossless')
LONG_MACROS = [LONG_MACRO, '!&_Delete *_Circle ~_Line "My Layer 01" "Table 01:: New" _Mode=_Shaded _Points=10 Pause Enter ' * 4, '! _-DocumentProperties _Mesh _Custom _MaxEdgeSrf .01 _EnterEnd ' * 4, "'_CPlane _3Point\n_Pause _Pause _Pause _Enter " * 5]

@check('long native equals the pure-Python reference on >=240-byte inputs')
def _():
    _reset()
    for text in LONG_MACROS:
        for lossless in (False, True):
            got_native = tokenize(text, lossless=lossless)
            got_ref = ORACLE.tokenize(text, lossless=lossless)
            assert got_native == got_ref, (text[:40], lossless, got_native[:3], 'expected', got_ref[:3])

@check('the _3Point text rule holds on the packed route (host converter)')
def _():
    _reset()
    packed = native._native_macro_packed('_3Point', False)
    assert packed is not None, 'the packed call must succeed for _3Point'
    steps = macro._steps_from_packed(packed, False)
    assert steps == [{'kind': 'text', 'value': '_3Point'}], steps
    packed_lossless = native._native_macro_packed('_3Point', True)
    assert macro._steps_from_packed(packed_lossless, True) == [{'kind': 'text', 'value': '_3Point'}]

@check('the _3Point text rule holds on the native route (mutation target)')
def _():
    _reset()
    before = native._native_macro_calls
    payload = native._native_macro('_3Point', False)
    assert payload is not None
    assert macro._steps_from_json(payload) == [{'kind': 'text', 'value': '_3Point'}]
    _assert_native_ran(before, 'direct macro _3Point')
    assert tokenize('-15,0,0') == [{'kind': 'text', 'value': '-15,0,0'}]
    assert tokenize('_Points=10') == [{'kind': 'option', 'name': 'Points', 'value': '10'}]

@check('a non-string macro is still refused with MacroError (counter unmoved)')
def _():
    _reset()
    before = native._native_macro_calls
    for bad in (None, 5, ['_Move'], b'_Move'):
        try:
            tokenize(bad)
            raise AssertionError('expected MacroError for %r' % (bad,))
        except MacroError:
            pass
    assert native._native_macro_calls == before, ('non-string rejections must not touch the native counter', before, native._native_macro_calls)

@check('a leading-! non-command stays TEXT, no spurious cancel (native == reference)')
def _():
    _reset()
    for text in TEXT_CANCEL:
        got_native = tokenize(text)
        got_ref = ORACLE.tokenize(text)
        assert got_native == got_ref, (text, got_native, 'expected', got_ref)
        assert got_native == [{'kind': 'text', 'value': text}], (text, got_native)

@check('declined native (missing library) -> RuntimeError, counter unmoved')
def _():
    _reset()
    real_env = os.environ.get('NARWHAL_PURE_CORE')
    os.environ['NARWHAL_PURE_CORE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'no_such_lib.so')
    try:
        assert native._macro_binding() is None, 'a missing library must make the binding None'
        before = native._native_macro_calls
        try:
            tokenize('! _Line _Pause 0,0 _Enter')
            raise AssertionError('expected RuntimeError (no Python replay) for a missing library')
        except RuntimeError:
            pass
        assert native._native_macro_calls == before, ('missing-lib refusal must not touch the native counter', before, native._native_macro_calls)
    finally:
        if real_env is None:
            os.environ.pop('NARWHAL_PURE_CORE', None)
        else:
            os.environ['NARWHAL_PURE_CORE'] = real_env
        _reset()

@check('declined native (stale library) -> RuntimeError, counter unmoved')
def _():
    import tempfile
    stale = os.path.join(tempfile.gettempdir(), 'native_macro_stale.so')
    with open(stale, 'w', encoding='utf-8') as f:
        f.write('not a shared object\n')
    _reset()
    real_env = os.environ.get('NARWHAL_PURE_CORE')
    os.environ['NARWHAL_PURE_CORE'] = stale
    try:
        assert native._macro_binding() is None, 'a library without the macro export must make the binding None'
        before = native._native_macro_calls
        try:
            tokenize('!&_Delete')
            raise AssertionError('expected RuntimeError (no Python replay) for a stale library')
        except RuntimeError:
            pass
        assert native._native_macro_calls == before, ('stale-lib refusal must not touch the native counter', before, native._native_macro_calls)
    finally:
        if real_env is None:
            os.environ.pop('NARWHAL_PURE_CORE', None)
        else:
            os.environ['NARWHAL_PURE_CORE'] = real_env
        _reset()

@check('declined native (adapter returns None) -> RuntimeError, counter unmoved')
def _():
    _reset()
    assert native is not None and native._macro_binding() is not None
    real_macro = native._native_macro
    native._native_macro = lambda *a, **k: None
    try:
        before = native._native_macro_calls
        try:
            tokenize('! _Line _Pause 0,0 _Enter')
            raise AssertionError('expected RuntimeError (no Python replay) when the adapter returns None')
        except RuntimeError:
            pass
        assert native._native_macro_calls == before, ('a refused native result must not touch the native counter', before, native._native_macro_calls)
    finally:
        native._native_macro = real_macro
        _reset()

@check('malformed successful macro JSON -> RuntimeError (no Python replay)')
def _():
    _reset()
    payload = b'[{"kind":"command","name":"Move","dash":0,"underscore":true,"transparent":false}]'
    real_binding = native._macro_binding

    def size_fn(_text, _length, _flag):
        return len(payload)

    def encode_fn(_text, _length, _flag, out, out_len):
        assert out_len == len(payload)
        ctypes.memmove(out, payload, len(payload))
        return 0
    native._macro_binding = lambda: (size_fn, encode_fn)
    try:
        before = native._native_macro_calls
        assert native._native_macro('_Move', False) is None
        assert native._native_macro_calls == before
        try:
            tokenize('_Move')
            raise AssertionError('expected RuntimeError (no Python replay) for a malformed native output')
        except RuntimeError:
            pass
        assert native._native_macro_calls == before, ('a malformed native output must not count as a native success', before, native._native_macro_calls)
    finally:
        native._macro_binding = real_binding
        _reset()

@check('packed macro validator rejects invalid UTF-8, gaps, and trailing bytes')
def _():
    assert native._valid_macro_packed(1, 1, [4], [0], [1], [0], [0], bytearray(b'x')) is True
    assert native._valid_macro_packed(1, 1, [4], [0], [1], [0], [0], bytearray(b'\xff')) is False
    gap = 1 << 32 | 1
    assert native._valid_macro_packed(1, 2, [4], [0], [gap], [0], [0], bytearray(b'xy')) is False
failed = 0
for name, status, detail in results:
    print(f'[{status}] {name}' + (f'  -- {detail}' if detail else ''))
    if status == 'FAIL':
        failed += 1
print(f'\n{len(results) - failed}/{len(results)} passed')
sys.exit(1 if failed else 0)
