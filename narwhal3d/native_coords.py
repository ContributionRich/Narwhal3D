"""Required coordinate ABI 1: host framing and repr only, never numeric replay."""
import ctypes
from functools import lru_cache
from types import MappingProxyType
import math
import os
from pathlib import Path
import sys
import threading
import unicodedata

__all__ = ['parse', 'parse_scalar', 'parse_angle', 'split_top_level']

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


class BearingReply(ctypes.Structure):
    _fields_ = [('code', ctypes.c_uint32), ('reserved', ctypes.c_uint32),
                ('value', ctypes.c_double), ('distance', ctypes.c_double),
                ('start', ctypes.c_uint32), ('end', ctypes.c_uint32)]


class CoordsReply(ctypes.Structure):
    _fields_ = [('code', ctypes.c_uint32), ('flags', ctypes.c_uint32),
                ('values', ctypes.c_double * 3), ('start', ctypes.c_uint32),
                ('end', ctypes.c_uint32), ('calc', CalcReply), ('bearing', BearingReply)]


# Immutable ABI-layout metadata, never caller data or cached native results.
_REPLY_SIZE = ctypes.sizeof(CoordsReply)
_CALC_START = CoordsReply.calc.offset
_BEARING_START = CoordsReply.bearing.offset
_CALC_ZERO = bytes(ctypes.sizeof(CalcReply))
_BEARING_ZERO = bytes(ctypes.sizeof(BearingReply))


_binding = None
_signature = None
_library = None
# Publish one immutable record: a lock-free reader keeps the matching CDLL
# alive even when another thread replaces the binding on an environment change.
_cache = None
_lock = threading.Lock()
_native_coords_calls = 0


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
                raise RuntimeError('Stale Rust coordinates core ABI; not replayed')
            identity = library.narwhal_pure_core_coords_owned_abi1
            identity.argtypes = []
            identity.restype = ctypes.c_uint32
            if identity() != 1:
                raise RuntimeError('Stale Rust coordinates ABI; not replayed')
            binding = library.narwhal_pure_core_coords_owned_v1
            point = ctypes.POINTER(ctypes.c_uint32)
            binding.argtypes = [point, ctypes.c_size_t, point, ctypes.c_size_t,
                                ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                ctypes.POINTER(CoordsReply), ctypes.c_size_t]
            binding.restype = ctypes.c_uint32
            split = library.narwhal_pure_core_coords_split_v1
            split.argtypes = [point, ctypes.c_size_t, ctypes.c_uint32,
                              ctypes.c_uint32, point, ctypes.c_size_t, point]
            split.restype = ctypes.c_uint32
            binding.split = split
        except (OSError, AttributeError) as error:
            raise RuntimeError('Required Rust coordinates ABI 1 unavailable; not replayed') from error
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


def _calc_error(text, model_unit, reply):
    code, start, end = reply.code, reply.start, reply.end
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

_FIXED_COORDS = {
    1: 'no coordinate given', 2: 'a coordinate may not contain spaces',
    10: "a bare number is a distance constraint in the reference CAD application (it locks the next pick's distance from the last point), not a coordinate; type x,y or x,y,z for a point",
    12: 'no angle given', 15: 'empty number', 16: 'empty coordinate',
    17: 'coordinate number outside finite range',
    18: 'coordinate input exceeds 4096 characters',
}
_QUOTED_COORDS = {
    4: 'is an angle constraint in the reference CAD application, not a coordinate',
    5: 'has a prefix but no coordinates', 6: 'has more than three coordinates',
    7: 'is not x,y<angle', 8: "has too many '<' for a coordinate",
    9: 'has too many values after the angle', 14: 'is not an angle',
}


def _error(text, model, reply):
    code = reply.code
    label = text[reply.start:reply.end]
    if code in _FIXED_COORDS:
        return _FIXED_COORDS[code]
    if code == 3:
        return 'point filters like %r are not supported yet' % label
    if code == 13:
        return 'the command-line calculator (%r) is not supported yet' % label
    if code in _QUOTED_COORDS:
        return '%r %s' % (label, _QUOTED_COORDS[code])
    if code == 20:
        return '%r is not a number (%s)' % (label, _calc_error(label, model, reply.calc)[1])
    b = reply.bearing
    if b.code == 4:
        return 'quadrant angle must be in [0, 90]: %g' % b.value
    if b.code == 5:
        return 'bearing input exceeds 4096 characters'
    if b.code == 6:
        return 'bearing number outside finite range'
    return '%s: %r' % ({1: 'not a bearing (need N/S + angle + E/W)',
                            2: 'not a surveyor coordinate (need dist<N/SangE/W)',
                            3: 'not an angle'}[b.code], label[b.start:b.end])


def _parse(text, model, mode, byte_label=None):
    # Compatibility admission only: retain legacy empty conventions and
    # exceptions, including float(bytes/bytearray), without Python float().
    if not isinstance(text, str):
        admitted = (text or '').strip()
        if not admitted:
            text = ''
        elif isinstance(admitted, (bytes, bytearray)):
            if mode == 0:
                # The legacy whitespace predicate sees integer byte elements.
                next(iter(admitted)).isspace()
            if mode == 2:
                # Legacy angle admission checks a str marker in a bytes object.
                'pi' in admitted
            byte_label = admitted
            text = bytes(admitted).decode('latin-1')
            mode = 3
        else:
            text = admitted
    if not isinstance(text, str):
        raise TypeError('expected a coordinate string after strip')
    if unicodedata.unidata_version != '15.1.0':
        raise RuntimeError('Unsupported Unicode database for Rust coordinates (requires 15.1)')
    binding = _required_binding()
    ms = isinstance(model, str)
    unit = model if ms else ''
    reply = CoordsReply(code=0xffffffff)
    try:
        rc = binding(_buffer(text), len(text), _buffer(unit), len(unit),
                     int(ms), mode, 151, ctypes.byref(reply), _REPLY_SIZE)
    except Exception as error:
        raise RuntimeError('Rust coordinate call failed; not replayed') from error
    code, start, end = reply.code, reply.start, reply.end
    flags = reply.flags
    values = tuple(reply.values)
    payload = bytes(reply)
    invalid = (rc != 0 or code not in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
                                      12, 13, 14, 15, 16, 17, 18, 20, 21, 22)
               or not 0 <= start <= end <= len(text)
               or flags > 3
               or not (math.isfinite(values[0]) and math.isfinite(values[1])
                       and math.isfinite(values[2]))
               or (code != 0 and (flags != 0 or any(values)))
               or (code == 0 and (start or end))
               or (mode != 0 and (flags or values[1] or values[2]))
               or (mode == 0 and code in (15, 22))
               or (mode in (1, 3) and code not in (0, 15, 17, 18, 20, 22))
               or (mode == 2 and code not in (0, 12, 13, 14, 17, 18))
               or (code == 22 and (mode != 3 or byte_label is None))
               or (code != 20 and payload[_CALC_START:_BEARING_START] != _CALC_ZERO)
               or (code != 21 and payload[_BEARING_START:_REPLY_SIZE] != _BEARING_ZERO))
    if code == 20:
        c = reply.calc
        invalid |= (c.reserved != 0 or not 1 <= c.code <= 29 or not 0 <= c.start <= c.end <= end-start
                    or not math.isfinite(c.value)
                    or (c.code != 10 and c.value != 0)
                    or (c.code != 14 and c.aux != 0)
                    or (c.code in (2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14,
                                   18, 19, 20, 21, 22, 23, 26, 27) and c.start == c.end)
                    or (c.code in (1, 5) and c.end != c.start + 1)
                    or (c.code == 24 and ms)
                    or (c.code == 25 and not ms)
                    or (c.code == 28 and (c.start or c.end))
                    or (c.code == 14 and (c.aux >> 16 not in (1, 2)
                        or not 1 <= (c.aux & 65535) <= 4096
                        or (c.aux & 65535) == c.aux >> 16)))
    if code == 21:
        b = reply.bearing
        invalid |= (b.reserved != 0 or not 1 <= b.code <= 6 or not 0 <= b.start <= b.end <= end-start
                    or not math.isfinite(b.value) or b.distance != 0
                    or (b.code != 4 and b.value != 0)
                    or (b.code == 4 and b.value <= 90)
                    or (b.code == 5 and (b.start or b.end)))
    if invalid:
        raise RuntimeError('Invalid Rust coordinate reply; not replayed')
    global _native_coords_calls
    _native_coords_calls += 1
    if code == 22:
        return None, '%r is not a number (expected a string)' % (byte_label,)
    if code:
        return None, _error(text, model, reply)
    if mode:
        return values[0], None
    return (values, bool(flags & 1), bool(flags & 2)), None


def parse(text, model_unit='Millimeters'):
    result, err = _parse(text, model_unit, 0)
    return (None, False, False, err) if err else (*result, None)


def parse_scalar(text, model_unit='Millimeters'):
    return _parse(text, model_unit, 1)


def parse_angle(text):
    return _parse(text, '', 2)


def split_top_level(text, sep=','):
    if not isinstance(text, str):
        raise TypeError('coordinate split expects a string')
    if len(text) > 4096:
        raise ValueError('coordinate input exceeds 4096 characters')
    binding = _required_binding()
    source = _buffer(text)
    single = isinstance(sep, str) and len(sep) == 1
    separator = ord(sep) if single else 0
    count = ctypes.c_uint32(0xffffffff)
    if binding.split(source, len(text), separator, int(single), None, 0,
                     ctypes.byref(count)) != 0 or not 1 <= count.value <= len(text)+1:
        raise RuntimeError('Invalid Rust coordinate split query; not replayed')
    expected = count.value
    spans = _array_type(expected * 2)()
    if binding.split(source, len(text), separator, int(single), spans, len(spans),
                     ctypes.byref(count)) != 0 or count.value != expected:
        raise RuntimeError('Invalid Rust coordinate split reply; not replayed')
    previous = 0
    parts = []
    for i in range(expected):
        start, end = spans[2*i], spans[2*i+1]
        if start != previous or not start <= end <= len(text):
            raise RuntimeError('Invalid Rust coordinate split spans; not replayed')
        parts.append(text[start:end])
        previous = end + 1
    if previous != len(text)+1:
        raise RuntimeError('Incomplete Rust coordinate split reply; not replayed')
    global _native_coords_calls
    _native_coords_calls += 1
    return parts
