"""Strict owned unit-operations adapter; no Python grammar or build-tree fallback.

Names are supplied by the host's ordered unit table. Coercion/error rendering
stay here because arbitrary Python objects and their repr are not a Rust API.
"""
from __future__ import annotations
import ctypes as C
import os
import sys
from pathlib import Path
import math
import threading
import unicodedata
from typing import NamedTuple

_LIMIT = 4096
_FILENAME = ("narwhal_pure_core.dll" if sys.platform == "win32" else
             "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else
             "libnarwhal_pure_core.so")
_PACKAGED = str(Path(__file__).resolve().parent / "native" / _FILENAME)

class _Binding(NamedTuple):
    signature: tuple
    library: object
    parse: object
    angle: object
    scale: object

_BINDING = None
_LOCK = threading.Lock()
_CALL_ERRORS = (C.ArgumentError, OSError, ValueError)
_SENTINEL = 0x7ff8000000000016

def _failure(reason):
    return RuntimeError(f"unit operations require valid native exports (no Python fallback): {reason}")

def _unicode_gate():
    if unicodedata.unidata_version != "15.1.0":
        raise _failure("Unicode 15.1.0 required")

def _signature():
    if "NARWHAL_PURE_CORE" in os.environ:
        return ("env", os.environ["NARWHAL_PURE_CORE"])
    return ("packaged", None)

def _bindings(signature):
    global _BINDING
    _unicode_gate()
    current = _BINDING
    if current is not None and current.signature == signature:
        return current
    with _LOCK:
        current = _BINDING
        if current is not None and current.signature == signature:
            return current
        if signature[0] == "env":
            path = signature[1]
            if not isinstance(path, str) or not path:
                raise _failure("explicit native path is empty")
        elif signature == ("packaged", None):
            path = _PACKAGED
        else:
            raise _failure("invalid loader signature")
        try:
            lib = C.CDLL(path)
            version = lib.narwhal_pure_core_abi_version
            version.argtypes, version.restype = [], C.c_uint32
            if version() != 1:
                raise _failure("ABI version mismatch")
            parse = lib.narwhal_pure_core_unit_ops_parse
            parse.argtypes = [C.c_char_p, C.c_size_t, C.POINTER(C.c_uint32),
                              C.POINTER(C.c_uint32), C.c_void_p, C.c_size_t,
                              C.POINTER(C.c_size_t)]
            parse.restype = C.c_int32
            angle = lib.narwhal_pure_core_unit_ops_angle
            angle.argtypes = [C.c_char_p, C.c_size_t, C.POINTER(C.c_double)]
            angle.restype = C.c_int32
            scale = lib.narwhal_pure_core_unit_ops_scale
            scale.argtypes = [C.c_double, C.c_double, C.c_double, C.POINTER(C.c_double)]
            scale.restype = C.c_int32
        except (AttributeError,) + _CALL_ERRORS as exc:
            raise _failure("library/version/export unavailable") from exc
        current = _Binding(signature, lib, parse, angle, scale)
        _BINDING = current
        return current

def _native():
    return _bindings(_signature())

def _call(fn, *args):
    try:
        return fn(*args)
    except _CALL_ERRORS as exc:
        raise _failure("native invocation failed") from exc

def _sentinel():
    return C.c_double.from_buffer_copy(_SENTINEL.to_bytes(8, sys.byteorder))

def _untouched(out):
    return C.c_uint64.from_buffer_copy(out).value == _SENTINEL

def _text(text):
    if len(text) > _LIMIT:
        raise ValueError("unit input exceeds 4096 codepoints (OURS)")
    # Surrogates cannot match ASCII names; native accepts surrogatepass and
    # preserves original bytes for exact Python error repr.
    return text.encode("utf-8", "surrogatepass")

def parse_unit(text, names):
    """Parse using host-supplied canonical names in native ID order (25 names)."""
    original = str(text or "")
    data = _text(original)
    binding = _native()
    if len(names) != 25:
        raise _failure("expected 25 ordered canonical names")
    i, mask, size = C.c_uint32(0xffffffff), C.c_uint32(0xffffffff), C.c_size_t(_LIMIT * 4 + 1)
    raw = C.create_string_buffer(_LIMIT * 4)
    status = _call(binding.parse, data, len(data), C.byref(i), C.byref(mask), raw,
                   len(raw), C.byref(size))
    if type(status) is not int or status not in (0, 1, 2, 3):
        raise _failure(f"invalid parse status {status!r}")
    if size.value > len(raw) or size.value > len(data):
        raise _failure("invalid raw length")
    payload = raw.raw[:size.value]
    try:
        normalized = payload.decode("utf-8", "surrogatepass")
    except UnicodeDecodeError as exc:
        raise _failure("invalid raw UTF-8") from exc
    # Validate transport provenance, not the grammar: strip may only remove
    # CPython whitespace, and may not introduce or discard interior bytes.
    left = data.find(payload) if payload else 0
    if normalized:
        if (left < 0 or not all(c.isspace() for c in data[:left].decode("utf-8", "surrogatepass"))
                or not all(c.isspace() for c in data[left+len(payload):].decode("utf-8", "surrogatepass"))
                or normalized[0].isspace() or normalized[-1].isspace()):
            raise _failure("invalid raw normalization")
    elif original and not original.isspace():
        raise _failure("invalid empty raw normalization")
    if status == 0:
        if i.value >= 25 or mask.value != 0 or not normalized:
            raise _failure("invalid success metadata")
        return names[i.value]
    if status == 1:
        if i.value or mask.value or size.value:
            raise _failure("invalid empty metadata")
        raise ValueError("no unit given")
    if status == 2:
        if i.value or mask.value or not normalized:
            raise _failure("invalid invalid-unit metadata")
        raise ValueError(f"unknown unit system {normalized!r}; valid: {', '.join(names)}")
    if i.value or mask.value >> 25 or mask.value.bit_count() < 2 or not normalized:
        raise _failure("invalid ambiguity metadata")
    hits = [n for j, n in enumerate(names) if mask.value & (1 << j)]
    raise ValueError(f"{normalized!r} is ambiguous: {', '.join(hits)}")

def scale(value, sf, st):
    """Arguments are host-admitted floats; evaluate multiply then divide."""
    out = _sentinel()
    status = _call(_native().scale, value, sf, st, C.byref(out))
    if type(status) is not int:
        raise _failure("invalid scale status type")
    if status == 6 and st == 0.0 and _untouched(out):
        raise ZeroDivisionError("float division by zero")
    if status != 0 or st == 0.0:
        raise _failure(f"invalid scale response {status!r}")
    # Nonfinite results, including any NaN payload, are valid Python arithmetic.
    return out.value

def angle_factor(name):
    data = _text(str(name))
    out = _sentinel()
    status = _call(_native().angle, data, len(data), C.byref(out))
    if type(status) is not int:
        raise _failure("invalid angle status type")
    if status == 2 and _untouched(out):
        raise ValueError(f"unknown angle unit {name!r}; valid: degrees, radians, gradians")
    if status != 0 or not math.isfinite(out.value) or out.value <= 0.0:
        raise _failure(f"invalid angle response {status!r}")
    return out.value

def convert_angle(value, from_unit, to_unit):
    # Coercion and factors retain Python evaluation order. Native multiplication
    # cannot raise, so defer it until both factors are admitted.
    value = float(value)
    sf = angle_factor(from_unit)
    st = angle_factor(to_unit)
    return scale(value, sf, st)
