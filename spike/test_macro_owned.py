"""Macro host qualification: fixed original answers, no Python grammar replay.

Computation/Unicode/seeded loops live in Rust macro_golden. Original benchmarks
and mutation tooling are private only; no test-time native compilation.
"""
import importlib.util
import os
import sys
import tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
PRODUCTION = os.path.join(ROOT, 'narwhal3d', 'macro.py')
ORACLE_FILE = os.path.join(HERE, 'macro_reference.py')
ADAPTER_FILE = os.path.join(ROOT, 'narwhal3d', 'native_pure.py')
CANON = 'narwhal3d_native_pure'
LIB_CANDIDATES = [os.environ.get('NARWHAL_PURE_CORE', ''), os.path.join(ROOT, 'narwhal3d', 'native', 'libnarwhal_pure_core.so'), os.path.join(ROOT, 'native', 'pure_core', 'target', 'x86_64-unknown-linux-gnu', 'release', 'libnarwhal_pure_core.so'), os.path.join(ROOT, 'native', 'pure_core', 'target', 'release', 'libnarwhal_pure_core.so')]

def _locate_lib():
    for cand in LIB_CANDIDATES:
        if cand and os.path.isfile(cand):
            return cand
    return None

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
LIB = _locate_lib()
if LIB is None:
    print('FATAL: the qualified libnarwhal_pure_core.so is not present. Staged it and re-run.', file=sys.stderr)
    sys.exit(2)
os.environ['NARWHAL_PURE_CORE'] = LIB
ORACLE = _load('rust11_macro_oracle', ORACLE_FILE)
NAT = _load(CANON, ADAPTER_FILE)
PROD = _load('rust11_macro_prod', PRODUCTION)
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
    NAT._reset_for_tests()
LONG_MACRO = ('! _Line _Pause 0,0 _Enter ' * 10).strip()
BIG_MACRO = ('! _Line _Pause 0,0 _Enter \'*Zoom -_SelName "A 01" _Mode=_Shaded _Points=10 ' * 60).strip()

@check('the production module keeps the public API (tokenize/MacroError/SPECIAL_COMMANDS)')
def _():
    assert callable(PROD.tokenize)
    assert issubclass(PROD.MacroError, ValueError)
    assert PROD.SPECIAL_COMMANDS == frozenset({'pause', 'multipause', 'enter', 'enterend', 'noecho', 'echo', 'setredrawoff', 'setredrawon', 'run'}), PROD.SPECIAL_COMMANDS
    assert callable(ORACLE.tokenize)
    assert issubclass(ORACLE.MacroError, ValueError)

@check('nonstring coercion and complete MacroError messages match fixed captures')
def _():
    before = NAT._native_macro_calls
    for value in [None, 5, ['_Move'], b'_Move', 3.5, object(), False]:
        try:
            ORACLE.tokenize(value)
        except ORACLE.MacroError as error:
            wanted = str(error)
        else:
            raise AssertionError('fixed nonstring anchor must be an error')
        try:
            PROD.tokenize(value)
        except PROD.MacroError as error:
            assert str(error) == wanted
        else:
            raise AssertionError('nonstring admitted')
    assert NAT._native_macro_calls == before

@check('the retired Python parser is GONE from the production module')
def _():
    for gone in ('_split_tokens', '_classify', '_strip_flags', '_command_step', '_clean_value', '_is_fully_quoted', '_find_eq_outside_quotes', '_with_cancel', '_FLAG_CHARS', 'NATIVE_MACRO_MIN_BYTES', 'NATIVE_MACRO_JSON_MAX_BYTES', 'NATIVE_MACRO_PACKED_MIN_BYTES'):
        assert not hasattr(PROD, gone), 'the module still carries the retired %r' % gone
    assert callable(PROD._steps_from_json)
    assert callable(PROD._steps_from_packed)

@check('the production module delegates to the SAME qualified adapter (by-path route)')
def _():
    assert NAT in sys.modules.values() or sys.modules.get(CANON) is NAT
    native_attr = getattr(PROD, 'native_pure', None)
    if native_attr is None:
        assert PROD._native_macro() is NAT, 'the module must resolve the shared adapter, not a private copy'
    else:
        assert native_attr is NAT, 'the module must use the shared adapter, not a private copy'
        assert PROD.native_pure._native_macro is NAT._native_macro

@check('the production same-directory route (from . import native_pure) works')
def _():
    pkgroot = tempfile.mkdtemp(prefix='rust11_pkg_')
    pkgdir = os.path.join(pkgroot, 'rust11_pkg')
    os.makedirs(pkgdir)
    open(os.path.join(pkgdir, '__init__.py'), 'w', encoding='utf-8').close()
    with open(os.path.join(pkgdir, 'native_pure.py'), 'w', encoding='utf-8') as f:
        f.write('from narwhal3d_native_pure import _native_macro\n')
    with open(PRODUCTION, encoding='utf-8') as src, open(os.path.join(pkgdir, 'macro.py'), 'w', encoding='utf-8') as dst:
        dst.write(src.read())
    sys.path.insert(0, pkgroot)
    try:
        import rust11_pkg.macro as prod_pkg
        assert prod_pkg.__package__ == 'rust11_pkg'
        resolved = getattr(prod_pkg, 'native_pure', None)
        if resolved is None:
            resolved = prod_pkg._native_macro()
        assert resolved._native_macro is NAT._native_macro, 'the production route must resolve the same native function'
        assert prod_pkg.tokenize('! _Line _Pause 0,0 _Enter') == ORACLE.tokenize('! _Line _Pause 0,0 _Enter')
    finally:
        sys.path.remove(pkgroot)
        for name in ('rust11_pkg', 'rust11_pkg.macro', 'rust11_pkg.native_pure'):
            sys.modules.pop(name, None)

@check("a SHORT macro is native (counter moves) -- no 'short stays Python' band")
def _():
    _reset()
    before = NAT._native_macro_calls
    assert isinstance(PROD.tokenize('_Line'), list)
    assert NAT._native_macro_calls == before + 1, 'short macro must be native: counter %d -> %d' % (before, NAT._native_macro_calls)

@check('a LONG macro is native (counter moves)')
def _():
    _reset()
    before = NAT._native_macro_calls
    assert isinstance(PROD.tokenize(LONG_MACRO, lossless=True), list)
    assert NAT._native_macro_calls == before + 1, 'long macro must be native: counter %d -> %d' % (before, NAT._native_macro_calls)

@check('a BIG macro (> the old JSON band) is native (counter moves)')
def _():
    _reset()
    before = NAT._native_macro_calls
    assert isinstance(PROD.tokenize(BIG_MACRO), list)
    assert NAT._native_macro_calls == before + 1, 'big macro must be native: counter %d -> %d' % (before, NAT._native_macro_calls)

@check('a non-string macro is MacroError (native counter unmoved)')
def _():
    _reset()
    before = NAT._native_macro_calls
    for bad in (None, 5, ['_Move'], b'_Move', 3.5, object()):
        try:
            PROD.tokenize(bad)
            raise AssertionError('expected MacroError for %r' % (bad,))
        except PROD.MacroError:
            pass
    assert NAT._native_macro_calls == before, ('non-string rejections must not touch the native counter', before, NAT._native_macro_calls)

@check('over the 1 MiB cap -> RuntimeError (no Python fallback)')
def _():
    _reset()
    before = NAT._native_macro_calls
    big = 'a' * ((1 << 20) + 1)
    try:
        PROD.tokenize(big)
        raise AssertionError('expected RuntimeError for a >1 MiB macro')
    except RuntimeError:
        pass
    assert NAT._native_macro_calls == before, ('a refused (over-cap) native result must not count as a success', before, NAT._native_macro_calls)

def _env_swap(value):
    real = os.environ.get('NARWHAL_PURE_CORE')
    if value is None:
        os.environ.pop('NARWHAL_PURE_CORE', None)
    else:
        os.environ['NARWHAL_PURE_CORE'] = value
    _reset()
    return real

@check('a missing library -> RuntimeError (no Python fallback)')
def _():
    real = _env_swap(os.path.join(tempfile.gettempdir(), 'no_such_lib.so'))
    try:
        assert NAT._macro_binding() is None, 'a missing library must make the binding None'
        before = NAT._native_macro_calls
        try:
            PROD.tokenize('! _Line _Pause 0,0 _Enter')
            raise AssertionError('expected RuntimeError for a missing library')
        except RuntimeError:
            pass
        assert NAT._native_macro_calls == before, ('missing-lib refusal must not touch the native counter', before, NAT._native_macro_calls)
    finally:
        if real is None:
            os.environ.pop('NARWHAL_PURE_CORE', None)
        else:
            os.environ['NARWHAL_PURE_CORE'] = real
        _reset()

@check('a library without the macro exports (stale) -> RuntimeError')
def _():
    stale = os.path.join(tempfile.gettempdir(), 'rust11_stale_lib.so')
    with open(stale, 'w', encoding='utf-8') as f:
        f.write('not a shared object\n')
    real = _env_swap(stale)
    try:
        assert NAT._macro_binding() is None, 'a library without the macro export must make the binding None'
        before = NAT._native_macro_calls
        try:
            PROD.tokenize('!&_Delete')
            raise AssertionError('expected RuntimeError for a stale library')
        except RuntimeError:
            pass
        assert NAT._native_macro_calls == before, ('stale-lib refusal must not touch the native counter', before, NAT._native_macro_calls)
    finally:
        if real is None:
            os.environ.pop('NARWHAL_PURE_CORE', None)
        else:
            os.environ['NARWHAL_PURE_CORE'] = real
        _reset()

@check('an adapter that returns None (refused/malformed) -> RuntimeError')
def _():
    assert NAT._macro_binding() is not None, 'the good library must be bound first'
    real_macro = NAT._native_macro
    NAT._native_macro = lambda *a, **k: None
    try:
        before = NAT._native_macro_calls
        try:
            PROD.tokenize('! _Line _Pause 0,0 _Enter')
            raise AssertionError('expected RuntimeError when the adapter returns None')
        except RuntimeError:
            pass
        assert NAT._native_macro_calls == before, ('a refused native result must not touch the native counter', before, NAT._native_macro_calls)
    finally:
        NAT._native_macro = real_macro
        _reset()

def main():
    for name, status, detail in results:
        print(f'[{status}] {name}' + (f' -- {detail}' if detail else ''))
    failed = sum((status == 'FAIL' for _, status, _ in results))
    print(f'{len(results) - failed}/{len(results)} passed')
    return int(bool(failed))
if __name__ == '__main__':
    sys.exit(main())
