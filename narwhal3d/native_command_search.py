"""Optional packed Rust search for high-cardinality command-name tables."""
from __future__ import annotations

import array
import ctypes
import os
from pathlib import Path
import sys
import threading

_ABI = 1
_ENV = "NARWHAL_PURE_CORE"
_lock = threading.Lock()
_signature = None
_binding = None
_fuzzy_signature = None
_fuzzy_binding = None
_pack_cache = {"names": None, "packed": None}
_MIN_NAMES = 128
_native_prefix_calls = 0
_native_fuzzy_calls = 0


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


def _prefix_binding():
    global _signature, _binding
    explicit = os.environ.get(_ENV)
    signature = explicit or "<packaged>"
    if _signature == signature:
        return _binding
    with _lock:
        if _signature == signature:
            return _binding
        _signature, _binding = signature, None
        packaged = Path(__file__).resolve().parent / "native" / _library_name()
        candidates = (Path(explicit).expanduser(),) if explicit else (packaged,)
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI:
                    continue
                fn = library.narwhal_pure_core_command_prefix
                fn.argtypes = [
                    ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_int64), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_uint64),
                ]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _binding = fn
            return fn
        return None


def _fuzzy_binding_for_library():
    global _fuzzy_signature, _fuzzy_binding
    explicit = os.environ.get(_ENV)
    signature = explicit or "<packaged>"
    if _fuzzy_signature == signature:
        return _fuzzy_binding
    with _lock:
        if _fuzzy_signature == signature:
            return _fuzzy_binding
        _fuzzy_signature, _fuzzy_binding = signature, None
        packaged = Path(__file__).resolve().parent / "native" / _library_name()
        candidates = (Path(explicit).expanduser(),) if explicit else (packaged,)
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI:
                    continue
                fn = library.narwhal_pure_core_command_fuzzy
                fn.argtypes = [
                    ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_int64),
                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint64),
                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_uint64),
                    ctypes.POINTER(ctypes.c_uint64),
                ]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _fuzzy_binding = fn
            return fn
        return None


def _pack(names):
    names = tuple(names)
    if _pack_cache["names"] == names:
        return _pack_cache["packed"]
    try:
        encoded = [name.encode("ascii") for name in names]
    except (AttributeError, UnicodeEncodeError):
        return None
    concat = bytearray().join(encoded)
    lengths = array.array("q", (len(value) for value in encoded))
    packed = (
        names,
        concat,
        lengths,
        (ctypes.c_ubyte * len(concat)).from_buffer(concat),
        (ctypes.c_int64 * len(lengths)).from_buffer(lengths),
    )
    _pack_cache["names"], _pack_cache["packed"] = names, packed
    return packed


def prefix_matches(prefix, names, *, force=False):
    """Return native ordered names, or ``None`` for the Python reference path."""
    global _native_prefix_calls
    if not isinstance(prefix, str) or not prefix:
        return [] if prefix == "" else None
    names = tuple(names)
    if not force and len(names) < _MIN_NAMES:
        return None
    fn = _prefix_binding()
    packed = _pack(names)
    if fn is None or packed is None:
        return None
    try:
        query = prefix.encode("ascii")
    except UnicodeEncodeError:
        return None
    _names, concat, lengths, concat_view, lengths_view = packed
    query_buffer = bytearray(query)
    query_view = (ctypes.c_ubyte * len(query_buffer)).from_buffer(query_buffer)
    output = array.array("Q", [0]) * len(names)
    output_view = (ctypes.c_uint64 * len(output)).from_buffer(output)
    count = ctypes.c_uint64(0)
    try:
        code = fn(query_view, len(query), concat_view, len(concat), lengths_view,
                  len(names), output_view, len(output), ctypes.byref(count))
    except Exception:  # noqa: BLE001 -- command entry retains Python fallback
        return None
    used = int(count.value)
    if code != 0 or used > len(names):
        return None
    indices = tuple(int(value) for value in output[:used])
    if (len(set(indices)) != len(indices)
            or any(index < 0 or index >= len(names) for index in indices)):
        return None
    _native_prefix_calls += 1
    return [names[index] for index in indices]


def fuzzy_matches(typed, names, usage=None, *, force=False):
    """Return native ``(exact, inexact)``, or ``None`` for Python fallback."""
    global _native_fuzzy_calls
    if not isinstance(typed, str) or not typed:
        return ([], []) if typed == "" else None
    names = tuple(names)
    if not force and len(names) < _MIN_NAMES:
        return None
    fn = _fuzzy_binding_for_library()
    packed = _pack(names)
    if fn is None or packed is None:
        return None
    try:
        query = typed.encode("ascii")
        usage_values = array.array("q", (usage.get(name, 0) if usage else 0
                                          for name in names))
    except (AttributeError, OverflowError, TypeError, UnicodeEncodeError, ValueError):
        return None
    _names, concat, lengths, concat_view, lengths_view = packed
    query_buffer = bytearray(query)
    query_view = (ctypes.c_ubyte * len(query_buffer)).from_buffer(query_buffer)
    usage_view = (ctypes.c_int64 * len(usage_values)).from_buffer(usage_values)
    output = array.array("Q", [0]) * len(names)
    output_view = (ctypes.c_uint64 * len(output)).from_buffer(output)
    exact_count = ctypes.c_uint64(0)
    total_count = ctypes.c_uint64(0)
    try:
        code = fn(query_view, len(query), concat_view, len(concat), lengths_view,
                  usage_view, len(names), output_view, len(output),
                  ctypes.byref(exact_count), ctypes.byref(total_count))
    except Exception:  # noqa: BLE001 -- command entry retains Python fallback
        return None
    exact_used = int(exact_count.value)
    total_used = int(total_count.value)
    if code != 0 or exact_used > total_used or total_used > len(names):
        return None
    indices = tuple(int(value) for value in output[:total_used])
    if (len(set(indices)) != len(indices)
            or any(index < 0 or index >= len(names) for index in indices)):
        return None
    _native_fuzzy_calls += 1
    exact = [names[index] for index in indices[:exact_used]]
    inexact = [names[index] for index in indices[exact_used:]]
    return exact, inexact


def _reset_for_tests():
    global _signature, _binding, _fuzzy_signature, _fuzzy_binding
    global _native_prefix_calls, _native_fuzzy_calls
    with _lock:
        _signature = None
        _binding = None
        _fuzzy_signature = None
        _fuzzy_binding = None
        _native_prefix_calls = 0
        _native_fuzzy_calls = 0
        _pack_cache["names"] = None
        _pack_cache["packed"] = None
