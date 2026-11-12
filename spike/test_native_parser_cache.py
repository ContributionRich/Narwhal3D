"""Focused host-cache checks and temporary-copy mutation; no live mutation."""
import ctypes
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import os
from pathlib import Path
import threading
import shutil
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LIB = Path(os.environ.get('NARWHAL_PURE_CORE') or ROOT / 'narwhal3d/native/libnarwhal_pure_core.so').resolve()
if not LIB.is_file():
    raise RuntimeError('Qualified native parser library is required')
_fixture = tempfile.TemporaryDirectory(prefix='native-parser-cache-')
HERE = Path(_fixture.name)
(HERE / 'native').mkdir()
shutil.copy2(LIB, HERE / 'native' / LIB.name)


def load(short, source=None):
    path = ROOT / 'narwhal3d' / ('native_' + short + '.py')
    spec = importlib.util.spec_from_file_location(short + str(source), source or path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.__file__ = str(HERE / 'adapter.py')
    return mod


def env_check(mod, short):
    api = mod.evaluate if short == 'calc' else mod.bearing_angle
    text = '1+2' if short == 'calc' else 'N30E'
    os.environ['NARWHAL_PURE_CORE'] = str(LIB)
    api(text)
    # Crucially NO reset: a cache keyed incorrectly would return the old binding.
    os.environ['NARWHAL_PURE_CORE'] = '/no/such/changed-native-library.so'
    try:
        api(text)
    except RuntimeError as error:
        assert 'unavailable' in str(error)
    else:
        raise AssertionError('environment change reused stale native binding')
    os.environ.pop('NARWHAL_PURE_CORE')
    value, error = api(text)
    assert error is None and value == (3.0 if short == 'calc' else 60.0)
    assert mod._cache[0] is None
    os.environ['NARWHAL_PURE_CORE'] = os.path.relpath(LIB, ROOT)
    assert api(text) == (value, None)
    assert mod._cache[0] == os.path.relpath(LIB, ROOT)
    os.environ['NARWHAL_PURE_CORE'] = str(LIB)
    assert api(text) == (value, None)
    assert mod._cache[0] == str(LIB)


for short in ('bearing', 'calc'):
    mod = load(short)
    env_check(mod, short)
    print('PASS', short, 'explicit -> missing -> packaged -> relative -> absolute (no resets)')
    api = mod.evaluate if short == 'calc' else mod.bearing_angle
    inputs = [('1+2', (3.0, None)), ('1/0', (None, "bad number: '1/0'"))] if short == 'calc' else [
        ('N30E', (60.0, None)), ('NbadE', (None, "not an angle: 'bad'"))]

    class NoLock:
        def __enter__(self): raise AssertionError('hot path acquired lock')
        def __exit__(self, *args): pass

    before = getattr(mod, '_native_' + short + '_calls')
    with patch.object(mod, 'Path', side_effect=AssertionError('hot path constructed Path')):
        with patch.object(mod, '_lock', NoLock()):
            for text, expected in inputs:
                assert api(text) == expected
    assert getattr(mod, '_native_' + short + '_calls') - before == len(inputs)
    print('PASS', short, 'warm success/rejection: no Path, no lock, native counter +1 each')

    cold = load(short)
    barrier = threading.Barrier(8)
    real_cdll = ctypes.CDLL
    loads = []

    def counted(path):
        loads.append(path)
        return real_cdll(path)

    def worker(_):
        barrier.wait(timeout=10)
        return cold._required_binding()

    with patch.object(cold.ctypes, 'CDLL', side_effect=counted):
        with ThreadPoolExecutor(max_workers=8) as pool:
            bindings = list(pool.map(worker, range(8)))
    assert len(loads) == 1, loads
    assert all(binding is bindings[0] for binding in bindings)
    assert cold._cache[1] is bindings[0] and cold._cache[2] is cold._library
    # ctypes function objects retain the loaded owner across cache replacement.
    old_binding = bindings[0]
    old_library = cold._library
    os.environ.pop('NARWHAL_PURE_CORE')
    cold._required_binding()
    assert any(value is old_library for value in old_binding._objects.values())
    print('PASS', short, '8 concurrent cold misses load once; old callable retains CDLL owner')
    os.environ['NARWHAL_PURE_CORE'] = str(LIB)
    for length in range(150):
        cold._array_type(length)
    info = cold._array_type.cache_info()
    assert info.maxsize == 128 and info.currsize == 128
    for name in ('_FIXED_ERRORS', '_QUOTED_ERRORS', '_MATH_ERRORS', '_ERROR_LABELS'):
        if hasattr(cold, name):
            try:
                getattr(cold, name)[12345] = 'mutated'
            except TypeError:
                pass
            else:
                raise AssertionError('error table is mutable')
    if short == 'calc':
        first = cold._buffer('123')
        second = cold._buffer('456')
        first[0] = ord('9')
        assert list(second) == list(map(ord, '456'))
        assert first is not second
    print('PASS', short, 'bounded type-only cache, immutable error tables, fresh data buffers')

    path = ROOT / 'narwhal3d' / ('native_' + short + '.py')
    original = path.read_bytes()
    mutant = HERE / ('native_' + short + '-env-mutant.py')
    source = original.decode()
    assert source.count('cached[0] == signature') == 2
    assert source.count('_signature == signature') == 2
    mutant.write_text(source.replace('cached[0] == signature', 'True').replace('_signature == signature', 'True'))
    try:
        env_check(load(short, mutant), short)
    except AssertionError as error:
        assert 'environment change reused stale' in str(error), error
        print('MUTATION RED', short, str(error))
    else:
        raise AssertionError('cache mutation did not go RED')
    assert path.read_bytes() == original
    env_check(load(short), short)
    print('RESTORED GREEN', short, hashlib.sha256(original).hexdigest())

_fixture.cleanup()
