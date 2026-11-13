"""Required SVG owned ABI 14: admission, validation and presentation only.

No Python tokenizer, numeric geometry, replay, input/result cache or size routing.
Unicode 15.1 matches bundled CPython 3.13. Historical SVG elliptical arcs remain
approximate cubics, not analytic ellipses. Empty non-string sequences retain the
legacy [] admission exception without consulting native code.
"""
import ctypes as C
from functools import lru_cache
import math
import os
from pathlib import Path
import sys
import threading
from typing import NamedTuple
import unicodedata

_LIMIT = 1 << 20
_OUTPUT_BYTES = 64 << 20
_ENCODING = 'utf-32-le' if sys.byteorder == 'little' else 'utf-32-be'
_ARITIES = dict(M=2, L=2, H=1, V=1, C=6, S=4, Q=4, T=2, A=7)


class _Error(C.Structure):
    _fields_ = [("kind", C.c_uint32), ("command", C.c_uint32),
                ("expected", C.c_uint32), ("actual", C.c_uint32),
                ("start", C.c_size_t), ("end", C.c_size_t), ("value", C.c_double)]


class _Binding(NamedTuple):
    signature: tuple
    library: object
    function: object


_binding = None
_lock = threading.Lock()
_native_svgpath_calls = 0
_ZERO_ERROR = bytes(_Error())


@lru_cache(maxsize=128)
def _array_type(length):
    return C.c_uint32 * length


def _required_binding():
    """Return an immutable record retaining the matching CDLL, including warm reads."""
    global _binding
    explicit = os.environ.get('NARWHAL_PURE_CORE')
    signature = ('env', explicit) if explicit is not None else ('packaged', None)
    cached = _binding
    if cached is not None and cached.signature == signature:
        return cached
    with _lock:
        cached = _binding
        if cached is not None and cached.signature == signature:
            return cached
        suffix = ('narwhal_pure_core.dll' if sys.platform == 'win32' else
                  'libnarwhal_pure_core.dylib' if sys.platform == 'darwin' else
                  'libnarwhal_pure_core.so')
        path = (Path(explicit).expanduser() if explicit is not None else
                Path(__file__).resolve().parent / 'native' / suffix)
        try:
            library = C.CDLL(str(path))
            core = library.narwhal_pure_core_abi_version
            core.argtypes = []
            core.restype = C.c_uint32
            if core() != 1:
                raise RuntimeError('Stale SVG core ABI; requires core ABI 1')
            identity = library.narwhal_pure_core_svgpath_owned_version
            identity.argtypes = []
            identity.restype = C.c_uint32
            if identity() != 14:
                raise RuntimeError('Stale SVG owned ABI; requires version 14')
            fn = library.narwhal_pure_core_svgpath_owned
            fn.argtypes = [C.POINTER(C.c_uint32), C.c_size_t,
                           C.POINTER(C.c_double), C.c_size_t,
                           C.POINTER(C.c_size_t), C.c_size_t,
                           C.POINTER(C.c_uint8), C.c_size_t,
                           C.POINTER(C.c_size_t), C.POINTER(_Error)]
            fn.restype = C.c_int32
        except (OSError, AttributeError, C.ArgumentError) as exc:
            raise RuntimeError('Required SVG owned ABI unavailable; not replayed') from exc
        record = _Binding(signature, library, fn)
        _binding = record
        return record


def _load():
    record = _required_binding()
    return record.library, record.function


def _buffer(text):
    return _array_type(len(text)).from_buffer_copy(text.encode(_ENCODING, 'surrogatepass'))


def _call(fn, *args):
    try:
        return fn(*args)
    except (OSError, C.ArgumentError) as exc:
        raise RuntimeError('SVG native call failed; not replayed') from exc


def _error_exception(error, text):
    """Validate every byte, including unused fields, before formatting an error."""
    expected = _Error(kind=error.kind)
    k = error.kind
    exception = None
    if k == 1 and math.isfinite(error.value):
        expected.value = error.value
        exception = ValueError(f"unexpected number {error.value!r} outside a command")
    elif k in (2, 3) and error.command in map(ord, _ARITIES):
        cmd = chr(error.command)
        expected.command = error.command
        if k == 2 and cmd != 'M':
            exception = ValueError(f"command {cmd} before any moveto (M)")
        elif k == 3 and error.expected == _ARITIES[cmd] and error.actual < error.expected:
            expected.expected, expected.actual = error.expected, error.actual
            exception = ValueError(f"command {cmd} needs {error.expected} params, got {error.actual}")
    elif k == 4 and 0 <= error.start < error.end <= len(text):
        expected.start, expected.end = error.start, error.end
        exception = ValueError(f"could not convert string to float: {text[error.start:error.end]!r}")
    elif k == 5:
        exception = ValueError('SVG path exceeds finite geometry safety bounds (OURS)')
    if exception is None or bytes(error) != bytes(expected):
        raise RuntimeError('Invalid SVG native error report; not replayed')
    return exception


def _allocate_output(n, m):
    return (C.c_double * n)(), (C.c_size_t * m)(), (C.c_uint8 * m)()


def _subpaths_from_packed(pts, offs, flags, n_pts, n_subs):
    return [{"beziers": [tuple((pts[b*8+j], pts[b*8+j+1]) for j in (0, 2, 4, 6))
                          for b in range(offs[s], offs[s+1] if s+1 < n_subs else n_pts)],
             "closed": bool(flags[s])} for s in range(n_subs)]


def _subpaths_from_json(payload):
    import json
    subs = json.loads(payload)
    for sub in subs:
        sub["beziers"] = [tuple(tuple(pt) for pt in bez) for bez in sub["beziers"]]
    return subs


def parse_path(d):
    global _native_svgpath_calls
    if not isinstance(d, str):
        if len(d) == 0:
            return []
        raise TypeError(f"'in <string>' requires string as left operand, not {type(d[0]).__name__}")
    if unicodedata.unidata_version != '15.1.0':
        raise RuntimeError('Unsupported Unicode database for SVG (requires 15.1)')
    library, fn = _load()  # Retain owner throughout both calls, even on ENV changes.
    if len(d) > _LIMIT:
        raise ValueError('SVG path exceeds finite geometry safety bounds (OURS)')
    text = _buffer(d)
    counts = (C.c_size_t * 2)()
    err = _Error()
    status = _call(fn, text, len(d), None, 0, None, 0, None, 0, counts, C.byref(err))
    if status == 2:
        if tuple(counts) != (0, 0):
            raise RuntimeError('Invalid SVG native error counts; not replayed')
        exception = _error_exception(err, d)
        _native_svgpath_calls += 1
        raise exception
    if status not in (0, 1) or bytes(err) != _ZERO_ERROR:
        raise RuntimeError('Invalid SVG native query report; not replayed')
    n, m = counts
    # Counts are size_t but Python integer arithmetic cannot overflow here.
    # Reject before constructing ANY output ctypes array type or allocation.
    if (n % 8 or n > 64 * len(d) or m > len(d) or (m == 0 and n)
            or n * C.sizeof(C.c_double) + m * (C.sizeof(C.c_size_t) + 1) > _OUTPUT_BYTES
            or status != (1 if n or m else 0)):
        raise RuntimeError('Invalid SVG native output sizes; not replayed')
    pts, offs, flags = _allocate_output(n, m)
    status = _call(fn, text, len(d), pts, n, offs, m, flags, m, counts, C.byref(err))
    if status != 0 or bytes(err) != _ZERO_ERROR or tuple(counts) != (n, m):
        raise RuntimeError('Invalid SVG native write report; not replayed')
    if (any(not math.isfinite(x) for x in pts)
            or any(f not in (0, 1) for f in flags)
            or (m and offs[0] != 0)
            or any(o > n//8 for o in offs)
            or any(offs[i] > offs[i+1] for i in range(m-1))):
        raise RuntimeError('Invalid SVG native packed geometry; not replayed')
    result = _subpaths_from_packed(pts, offs, flags, n//8, m)
    _native_svgpath_calls += 1
    return result
