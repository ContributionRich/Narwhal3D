"""Required native calculator ABI 2. Host admission, framing and presentation only.

No tokenizer, expression parser, arithmetic, literal evaluator or error replay.
Unicode 15.1 is pinned to the bundled CPython 3.13 predicates in pure_core.
Missing/stale libraries and invalid replies are installation/protocol errors.
"""
import ctypes
from functools import lru_cache
from types import MappingProxyType
import math
import os
from pathlib import Path
import sys
import threading
import unicodedata

__all__ = ['evaluate']

_FIXED_ERRORS = MappingProxyType({
    1: 'no spaces in formulas', 6: 'empty formula', 7: 'expected an operand',
    15: 'unbalanced parentheses', 16: 'division by zero',
    17: 'result is not a finite number', 24: 'expected a model unit string',
    28: 'calculator input exceeds 4096 characters',
    29: 'calculator nesting exceeds 64 levels',
})
_QUOTED_ERRORS = MappingProxyType({
    2: 'bad number at', 3: 'bad number', 4: 'unknown unit',
    5: 'unexpected character', 9: 'unexpected token',
    26: 'not a length', 27: 'number outside finite range',
})
_MATH_ERRORS = MappingProxyType({
    18: 'math domain error', 19: 'math range error',
    20: 'result is not a real number', 21: 'complex exponentiation',
    22: '0.0 cannot be raised to a negative power',
    23: "(34, 'Numerical result out of range')",
})


class CalcReply(ctypes.Structure):
    _fields_ = [('code', ctypes.c_uint32), ('aux', ctypes.c_uint32),
                ('value', ctypes.c_double), ('start', ctypes.c_uint32),
                ('end', ctypes.c_uint32), ('reserved', ctypes.c_uint64)]


_binding = None
_signature = None
_library = None
# Publish one immutable record: a lock-free reader keeps the matching CDLL
# alive even when another thread replaces the binding on an environment change.
_cache = None
_lock = threading.Lock()
_native_calc_calls = 0


@lru_cache(maxsize=128)
def _array_type(length):
    # Cache only immutable ctypes types, never caller data or native results.
    return ctypes.c_uint32 * length


def _required_binding():
    global _binding, _signature, _library, _cache
    explicit = os.environ.get('NARWHAL_PURE_CORE')
    signature = explicit or None  # None distinguishes packaged from every path.
    cached = _cache
    if (cached is not None and cached[0] == signature
            and _binding is cached[1] and _signature == signature):
        return cached[1]
    with _lock:
        cached = _cache
        if (cached is not None and cached[0] == signature
                and _binding is cached[1] and _signature == signature):
            return cached[1]
        suffix = ('narwhal_pure_core.dll' if sys.platform == 'win32' else
                  'libnarwhal_pure_core.dylib' if sys.platform == 'darwin' else
                  'libnarwhal_pure_core.so')
        path = (Path(explicit).expanduser() if explicit else
                Path(__file__).resolve().parent / 'native' / suffix)
        _binding = _library = _signature = None
        try:
            library = ctypes.CDLL(str(path))
            core = library.narwhal_pure_core_abi_version
            core.argtypes = []
            core.restype = ctypes.c_uint32
            if core() != 1:
                raise RuntimeError('Stale Rust calculator core ABI; not replayed')
            identity = library.narwhal_pure_core_calc_abi2
            identity.argtypes = []
            identity.restype = ctypes.c_uint32
            if identity() != 2:
                raise RuntimeError('Stale Rust calculator ABI; not replayed')
            binding = library.narwhal_pure_core_calc_v2
            point = ctypes.POINTER(ctypes.c_uint32)
            binding.argtypes = [point, ctypes.c_size_t, point, ctypes.c_size_t,
                                ctypes.c_uint32, ctypes.c_uint32,
                                ctypes.POINTER(CalcReply), ctypes.c_size_t]
            binding.restype = ctypes.c_uint32
        except (OSError, AttributeError) as error:
            raise RuntimeError('Required Rust calculator ABI 2 unavailable; not replayed') from error
        _signature, _library, _binding = signature, library, binding
        _cache = (signature, binding, library)
        return binding


def _buffer(value):
    # Admission only: the native bound check precedes reading either span.
    if len(value) > 4096:
        return None
    encoding = 'utf-32-le' if sys.byteorder == 'little' else 'utf-32-be'
    return _array_type(len(value)).from_buffer_copy(
        value.encode(encoding, 'surrogatepass'))


def evaluate(text, model_unit='Millimeters'):
    """Return (float, None) or (None, exact legacy error), never emulate Rust."""
    if not isinstance(text, str):
        return None, 'expected a string'
    if unicodedata.unidata_version != '15.1.0':
        raise RuntimeError('Unsupported Unicode database for Rust calculator (requires 15.1)')
    binding = _required_binding()
    model_string = isinstance(model_unit, str)
    model = model_unit if model_string else ''
    source = _buffer(text)
    unit = _buffer(model)
    reply = CalcReply(code=0xffffffff)
    try:
        rc = binding(source, len(text), unit, len(model), int(model_string), 151,
                     ctypes.byref(reply), ctypes.sizeof(reply))
    except Exception as error:
        raise RuntimeError('Rust calculator call failed; not replayed') from error
    code, start, end = reply.code, reply.start, reply.end
    invalid = (rc != 0 or reply.reserved != 0 or code > 29
               or not 0 <= start <= end <= len(text)
               or not math.isfinite(reply.value)
               or (code not in (0, 10) and reply.value != 0.0)
               or (code != 14 and reply.aux != 0)
               or (code == 0 and (start != 0 or end != 0))
               or (code == 28 and (start != 0 or end != 0))
               or (code == 14 and (reply.aux >> 16 not in (1, 2)
                   or not 1 <= (reply.aux & 65535) <= 4096
                   or (reply.aux & 65535) == reply.aux >> 16))
               or (code in (2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14,
                            18, 19, 20, 21, 22, 23, 26, 27) and start == end)
               or (code in (1, 5) and end != start + 1)
               or (code == 24 and model_string)
               or (code == 25 and not model_string))
    if invalid:
        raise RuntimeError('Invalid Rust calculator reply; not replayed')
    global _native_calc_calls
    _native_calc_calls += 1
    if code == 0:
        return reply.value, None
    label = text[start:end]
    if code in _FIXED_ERRORS:
        return None, _FIXED_ERRORS[code]
    if code in _QUOTED_ERRORS:
        return None, '%s: %r' % (_QUOTED_ERRORS[code], label)
    if code == 10:
        return None, 'unexpected token: %r' % (reply.value,)
    if code == 8:
        return None, 'unknown name: %s' % label
    if code == 11:
        return None, '%s requires parentheses' % label
    if code == 12:
        return None, '%s requires at least one argument' % label
    if code == 13:
        return None, 'missing ) after %s' % label
    if code == 14:
        return None, '%s takes %d argument(s), got %d' % (
            label, reply.aux >> 16, reply.aux & 65535)
    if code == 25:
        return None, 'unknown model unit: %r' % (model_unit,)
    return None, '%s: %s' % (label, _MATH_ERRORS[code])
