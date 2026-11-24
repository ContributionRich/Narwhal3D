"""Required Rust UI content transport, not a second content/menu implementation.

Python is necessary for ctypes and renderer-facing tuples, frozensets and dict keys.
The native owner supplies all authored data, validation and menu expansion.
"""
import ctypes as C
from functools import lru_cache
import json
import os
from pathlib import Path
import platform
import sys

_LIMIT = 8 * 1024 * 1024


@lru_cache(maxsize=1)
def _binding():
    name = ('narwhal_ui_core.dll' if sys.platform == 'win32' else
            'libnarwhal_ui_core.dylib' if sys.platform == 'darwin' else
            'libnarwhal_ui_core.so')
    configured = os.environ.get('NARWHAL_UI_CORE_LIBRARY')
    path = Path(configured) if configured else Path(__file__).with_name('native') / name
    if configured == '':
        raise RuntimeError('empty NARWHAL_UI_CORE_LIBRARY path')
    if configured is None and not path.is_file():
        system = {'win32': 'windows', 'darwin': 'macos'}.get(sys.platform, sys.platform)
        machine = {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(
            platform.machine().lower(), platform.machine().lower())
        path = Path(__file__).resolve().parents[1] / 'artifacts/ui-core' / (system + '-' + machine) / name
    try:
        lib = C.CDLL(str(path))
        abi = lib.narwhal_ui_content_abi
        abi.argtypes, abi.restype = [], C.c_uint32
        if abi() != 1:
            raise RuntimeError('content ABI mismatch')
        read = lib.narwhal_ui_content_read
        read.argtypes = [C.c_uint32, C.c_void_p, C.c_size_t,
                         C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
        read.restype = C.c_uint32
    except (OSError, AttributeError, RuntimeError) as error:
        raise RuntimeError('required native UI content ABI 1 is unavailable') from error
    return lib, read


def _encode(value):
    if value is None or type(value) in (str, int, bool, float):
        return value
    if type(value) is dict:
        return {'t': 'dict', 'v': [[_encode(k), _encode(v)] for k, v in value.items()]}
    if type(value) in (tuple, list, frozenset, set):
        return {'t': type(value).__name__, 'v': [_encode(v) for v in value]}
    raise TypeError('unsupported UI content transport value: ' + type(value).__name__)


def _decode(value):
    if value is None or type(value) in (str, int, bool, float):
        return value
    if type(value) is not dict or set(value) != {'t', 'v'} or type(value['v']) is not list:
        raise ValueError('invalid typed content tree')
    tag, rows = value['t'], value['v']
    if tag == 'dict':
        if any(type(pair) is not list or len(pair) != 2 for pair in rows):
            raise ValueError('invalid content dictionary')
        result = {_decode(k): _decode(v) for k, v in rows}
        if len(result) != len(rows):
            raise ValueError('duplicate content dictionary key')
        return result
    constructors = {'tuple': tuple, 'list': list, 'frozenset': frozenset, 'set': set}
    if tag not in constructors:
        raise ValueError('unknown content type')
    result = constructors[tag](_decode(v) for v in rows)
    if tag in ('set', 'frozenset') and len(result) != len(rows):
        raise ValueError('duplicate content set entry')
    return result


def read(op, **arguments):
    payload = json.dumps(arguments, ensure_ascii=True, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
    if not 0 < len(payload) <= _LIMIT:
        raise ValueError('UI content input exceeds transport budget')
    _lib, function = _binding()
    source = C.create_string_buffer(payload)
    needed = C.c_size_t()
    status = function(op, source, len(payload), None, 0, C.byref(needed))
    if status or not 0 < needed.value <= _LIMIT:
        raise RuntimeError(f'native UI content sizing failed: status {status}')
    capacity = needed.value
    output = C.create_string_buffer(capacity)
    status = function(op, source, len(payload), output, capacity, C.byref(needed))
    if status or needed.value != capacity:
        raise RuntimeError(f'native UI content read failed: status {status}')
    try:
        return _decode(json.loads(output.raw.decode('utf-8')))
    except (ValueError, TypeError, RecursionError) as error:
        raise RuntimeError('native UI content returned a malformed typed tree') from error


def resource(name):
    return read(0, name=name)


def menu(op, registered, tabs):
    return read(op, registered=sorted(registered), tabs=_encode(tabs))


def counts(menus):
    return read(3, menus=_encode(menus))
