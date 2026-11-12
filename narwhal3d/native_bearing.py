"""Required bearing ABI 2: UTF-32 framing and host-only error presentation.

Rust owns all success/error grammar. OURS safety policy: <=4096 code points,
finite numbers only. No regex, numeric parser, or Python error replay.
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

__all__ = ['bearing_angle', 'parse_surveyor']

_ERROR_LABELS = MappingProxyType({
    1: 'not a bearing (need N/S + angle + E/W)',
    2: 'not a surveyor coordinate (need dist<N/SangE/W)',
    3: 'not an angle',
})


class BearingReply(ctypes.Structure):
    _fields_ = [('code', ctypes.c_uint32), ('reserved', ctypes.c_uint32),
                ('value', ctypes.c_double), ('distance', ctypes.c_double),
                ('start', ctypes.c_uint32), ('end', ctypes.c_uint32)]


_binding = _signature = _library = None
# Publish one immutable record: a lock-free reader keeps the matching CDLL
# alive even when another thread replaces the binding on an environment change.
_cache = None
_lock = threading.Lock()
_native_bearing_calls = 0


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
        _binding = _signature = _library = None
        try:
            library = ctypes.CDLL(str(path))
            core = library.narwhal_pure_core_abi_version
            core.argtypes = []
            core.restype = ctypes.c_uint32
            identity = library.narwhal_pure_core_bearing_abi2
            identity.argtypes = []
            identity.restype = ctypes.c_uint32
            if core() != 1 or identity() != 2:
                raise RuntimeError('Stale Rust bearing ABI; not replayed')
            binding = library.narwhal_pure_core_bearing_v2
            binding.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t,
                                ctypes.c_uint32, ctypes.c_uint32,
                                ctypes.POINTER(BearingReply), ctypes.c_size_t]
            binding.restype = ctypes.c_uint32
        except (OSError, AttributeError) as error:
            raise RuntimeError('Required Rust bearing ABI 2 unavailable; not replayed') from error
        _signature, _library, _binding = signature, library, binding
        _cache = (signature, binding, library)
        return binding


def _parse(text, mode):
    if not isinstance(text, str):
        return None, 'expected a string'
    if unicodedata.unidata_version != '15.1.0':
        raise RuntimeError('Unsupported Unicode database for Rust bearing (requires 15.1)')
    binding = _required_binding()
    source = None
    if len(text) <= 4096:
        encoding = 'utf-32-le' if sys.byteorder == 'little' else 'utf-32-be'
        source = _array_type(len(text)).from_buffer_copy(
            text.encode(encoding, 'surrogatepass'))
    reply = BearingReply(code=0xffffffff)
    try:
        rc = binding(source, len(text), mode, 151, ctypes.byref(reply), ctypes.sizeof(reply))
    except Exception as error:
        raise RuntimeError('Rust bearing call failed; not replayed') from error
    code, start, end = reply.code, reply.start, reply.end
    if (rc != 0 or reply.reserved != 0 or code > 6
            or not 0 <= start <= end <= len(text)
            or not math.isfinite(reply.value) or not math.isfinite(reply.distance)
            or (code not in (0, 4) and reply.value != 0.0)
            or ((code != 0 or mode == 0) and reply.distance != 0.0)
            or (code in (0, 5) and (start != 0 or end != 0))
            or (code == 0 and not 0 <= reply.value < 360)
            or (code == 4 and reply.value <= 90)
            or (code == 2 and mode != 1)):
        raise RuntimeError('Invalid Rust bearing reply; not replayed')
    global _native_bearing_calls
    _native_bearing_calls += 1
    if code == 0:
        return ((reply.distance, reply.value) if mode else reply.value), None
    if code == 4:
        return None, 'quadrant angle must be in [0, 90]: %g' % reply.value
    if code == 5:
        return None, 'bearing input exceeds 4096 characters'
    if code == 6:
        return None, 'bearing number outside finite range'
    label = _ERROR_LABELS[code]
    return None, '%s: %r' % (label, text[start:end])


def bearing_angle(text):
    return _parse(text, 0)


def parse_surveyor(text):
    return _parse(text, 1)
