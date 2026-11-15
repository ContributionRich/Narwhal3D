"""Required OBJ ABI adapter: caller-owned buffers, no Python serializer.

Python only snapshots/ marshals host iterables and manages the library and
buffers. Rust validates finite coordinates, face arities and local indices.
The stable native_pure packing helper is reused without its optional binding,
threshold, exception-swallowing wrapper, or private binding tuple.
"""
import ctypes as C
import importlib.util
import os
from pathlib import Path
import sys
import threading

_cache = None
_lock = threading.Lock()
_native_obj_calls = 0
_MAX_BYTES = 512 * 1024 * 1024


class NativeOBJError(RuntimeError):
    """Required OBJ native library missing, stale, or violating its ABI."""


def _packer():
    if __package__:
        from . import native_pure
        return native_pure
    key = "narwhal3d_native_pure"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            key, Path(__file__).with_name("native_pure.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[key] = mod
    return sys.modules[key]


def _binding():
    global _cache
    explicit = os.environ.get("NARWHAL_PURE_CORE")
    key = explicit or "<packaged>"
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    filename = ("narwhal_pure_core.dll" if sys.platform == "win32" else
                "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else
                "libnarwhal_pure_core.so")
    path = Path(explicit).expanduser() if explicit else Path(__file__).resolve().parent / "native" / filename
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1]
        try:
            lib = C.CDLL(str(path))
            version = lib.narwhal_pure_core_abi_version
            version.argtypes, version.restype = [], C.c_uint32
            if version() != 1:
                raise NativeOBJError("required OBJ native ABI version mismatch")
            fn = lib.narwhal_pure_core_obj_encode_into
            dp, bp = C.POINTER(C.c_double), C.POINTER(C.c_ubyte)
            ip, jp = C.POINTER(C.c_int64), C.POINTER(C.c_int32)
            fn.argtypes = [dp, C.c_int64, bp, C.c_int64, ip, ip, ip, ip,
                           C.c_int64, jp, C.c_int64, C.c_int64,
                           bp, C.c_size_t, ip]
            fn.restype = C.c_int32
        except (OSError, AttributeError) as exc:
            raise NativeOBJError(f"required OBJ native library missing or stale: {path}: {exc}") from exc
        _cache = key, fn
        return fn


def encode_obj(meshes):
    """Return (caller-owned bytearray, written); never decline or fall back."""
    global _native_obj_calls
    try:
        materialized = [(name, list(vertices), [tuple(f) for f in faces])
                        for name, vertices, faces in meshes]
        packed = _packer()._pack_obj_unchecked(materialized)
    except UnicodeEncodeError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"OBJ invalid mesh input: {exc}") from exc
    if packed is None:
        raise ValueError("OBJ invalid mesh input or mesh limits exceeded")
    nv, nf, ni, nm = (packed[k] for k in
                     ("total_v", "total_faces", "total_face_idx", "mesh_count"))
    capacity = (13 + packed["total_name_bytes"] + 4 * nm + 80 * nv
                + (len(str(max(nv, 1))) + 2) * ni + 3 * nf)
    if capacity > min(_MAX_BYTES, sys.maxsize):
        raise ValueError("OBJ output byte limit exceeded")
    fn = _binding()
    out = bytearray(capacity)
    view = (C.c_ubyte * capacity).from_buffer(out)
    v, names, nl, vc, fc, arity, indices = packed["views"]
    written = C.c_int64(-1)
    try:
        rc = fn(v, nv, names, len(names), nl, vc, fc, arity, nf, indices,
                ni, nm, view, capacity, C.byref(written))
    except (C.ArgumentError, OSError, TypeError, ValueError) as exc:
        raise NativeOBJError(f"OBJ native encoder ABI call failed: {exc}") from exc
    if rc == -2:
        raise ValueError("OBJ invalid mesh: nonfinite vertex, invalid face arity or out-of-range index")
    if rc != 0:
        raise NativeOBJError(f"OBJ native encoder failed ({rc})")
    n = written.value
    minimum = 13 + len(names) + 3 * nm + 8 * nv + 2 * ni + 2 * nf
    if not minimum <= n <= capacity or out[:13] != b"# Narwhal 3D\n" or out[n - 1] != 10:
        raise NativeOBJError(f"OBJ native encoder returned malformed output/count ({n})")
    _native_obj_calls += 1
    return out, n
