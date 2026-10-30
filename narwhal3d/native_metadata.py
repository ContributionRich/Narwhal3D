"""Optional in-process metadata accelerators; Python owns I/O and compatibility.

No subprocess transport or native mutable-object mirror. Missing exports, unsupported
text domains and invalid responses explicitly return None to the complete reference.
"""
import ctypes
from functools import lru_cache
import math
import os
from pathlib import Path
import struct
import sys
import unicodedata

_MAX_INPUT = 1024 * 1024
_MAX_OUTPUT = 64 * 1024 * 1024
_STATUS = {}
_CALLS = {"rgb": 0, "widths": 0}
_LIBRARY_NAME = ("narwhal_pure_core.dll" if sys.platform == "win32" else
                 "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else
                 "libnarwhal_pure_core.so")
_PACKAGED_LIBRARY = str(Path(__file__).resolve().parent / "native" / _LIBRARY_NAME)


@lru_cache(maxsize=4)
def _load(path):
    try:
        library = ctypes.CDLL(path)
        version = library.narwhal_pure_core_abi_version
        version.argtypes = []
        version.restype = ctypes.c_uint32
        if version() != 1:
            return None, None, None
    except (OSError, ValueError, AttributeError):
        return None, None, None
    try:
        rgb = library.narwhal_pure_core_rgb_to_aci
        rgb.argtypes = [ctypes.c_double] * 3
        rgb.restype = ctypes.c_uint32
    except AttributeError:
        rgb = None
    try:
        widths = library.narwhal_pure_core_printwidths
        widths.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p,
                           ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        widths.restype = ctypes.c_int32
    except AttributeError:
        widths = None
    return library, rgb, widths


def _bindings():
    return _load(os.environ.get("NARWHAL_PURE_CORE") or _PACKAGED_LIBRARY)


def status():
    return {"routes": dict(_STATUS), "calls": dict(_CALLS)}


def rgb_to_aci(r, g, b):
    function = _bindings()[1]
    if function is None:
        _STATUS["rgb"] = "reference: native export unavailable"
        return None
    try:
        value = function(r, g, b)
    except (OSError, ctypes.ArgumentError):
        _STATUS["rgb"] = "reference: native call failed"
        return None
    index = value & 255
    if not 1 <= index <= 9 or value not in (index, index | 256):
        _STATUS["rgb"] = "reference: invalid native result"
        return None
    _CALLS["rgb"] += 1
    _STATUS["rgb"] = "native"
    return index, bool(value & 256)


def widths_from_text(text):
    if type(text) is not str:
        _STATUS["widths"] = "reference: non-exact string"
        return None
    if unicodedata.unidata_version != "15.1.0" and not text.isascii():
        _STATUS["widths"] = "reference: Unicode table version mismatch"
        return None
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        _STATUS["widths"] = "reference: surrogate text"
        return None
    if len(data) > _MAX_INPUT:
        _STATUS["widths"] = "reference: native size bound"
        return None
    function = _bindings()[2]
    if function is None:
        _STATUS["widths"] = "reference: native export unavailable"
        return None
    capacity = min(_MAX_OUTPUT, max(64, len(data) * 64 + 64))
    output = ctypes.create_string_buffer(capacity)
    needed = ctypes.c_size_t()
    try:
        code = function(data, len(data), output, capacity, ctypes.byref(needed))
        if code != 0 or not 8 <= needed.value <= capacity:
            raise ValueError("native report refused or out of bounds")
        packed = ctypes.string_at(output, needed.value)
        count = struct.unpack_from("<I", packed)[0]
        if count > (len(packed) - 8) // 8:
            raise ValueError("width count out of bounds")
        widths = list(struct.unpack_from(f"<{count}d", packed, 4))
        previous = 0.0
        for width in widths:
            if not math.isfinite(width) or width <= previous:
                raise ValueError("invalid widths")
            previous = width
        offset = 4 + 8 * count
        count = struct.unpack_from("<I", packed, offset)[0]
        offset += 4
        if count > (len(packed) - offset) // 4:
            raise ValueError("warning count out of bounds")
        warnings = []
        for _ in range(count):
            size = struct.unpack_from("<I", packed, offset)[0]
            offset += 4
            if size > len(packed) - offset:
                raise ValueError("warning length out of bounds")
            warnings.append(packed[offset:offset + size].decode("utf-8"))
            offset += size
        if offset != len(packed):
            raise ValueError("trailing native report bytes")
    except (OSError, ValueError, UnicodeError, struct.error, ctypes.ArgumentError):
        _STATUS["widths"] = "reference: rejected native report"
        return None
    _CALLS["widths"] += 1
    _STATUS["widths"] = "native"
    return widths, warnings
