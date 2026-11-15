"""Required Rust STL file codec; Python owns I/O and caller-owned ABI buffers.

Nonfinite coordinates round-trip as explicit file data, not safe geometry.
The required import ABI rejects nonfinite/destination-f32 overflow before welding.
No native failure is replayed through a Python codec.
"""
from __future__ import annotations

import array
import ctypes as C
import os
from pathlib import Path
import struct
import sys
import threading

_MAX_BYTES = 512 * 1024 * 1024
_MAX_TRIANGLES = 2_000_000
_MAX_NAME = 64 * 1024 * 1024
_lock = threading.Lock()
_cache = None
_native_calls = 0
_native_decode_calls = 0
_native_mesh_calls = 0


class NativeSTLError(RuntimeError):
    """The required STL library is missing, stale, or violated its contract."""


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
                raise NativeSTLError("required STL native ABI version mismatch")
            signatures = {
                "stl_size_owned": ([C.POINTER(C.c_double), C.c_size_t, C.c_void_p, C.c_size_t, C.c_ubyte], C.c_int64),
                "stl_encode_owned": ([C.POINTER(C.c_double), C.c_size_t, C.c_void_p, C.c_size_t, C.c_ubyte, C.c_void_p, C.c_size_t], C.c_int32),
                "stl_file_size": ([C.c_void_p, C.c_size_t, C.c_ubyte, C.POINTER(C.c_int64)], C.c_int64),
                "stl_file_decode": ([C.c_void_p, C.c_size_t, C.c_ubyte, C.POINTER(C.c_double), C.c_size_t], C.c_int32),
                "stl_import_mesh_decode": ([C.c_void_p, C.c_size_t, C.POINTER(C.c_double), C.c_size_t, C.POINTER(C.c_uint32), C.c_size_t, C.POINTER(C.c_int64), C.POINTER(C.c_int64), C.POINTER(C.c_int64)], C.c_int32),
                "stl_normal": ([C.POINTER(C.c_double), C.POINTER(C.c_double)], C.c_int32),
            }
            functions = {}
            for name, (args, result) in signatures.items():
                fn = getattr(lib, "narwhal_pure_core_" + name)
                fn.argtypes, fn.restype = args, result
                functions[name] = fn
        except (OSError, AttributeError) as exc:
            raise NativeSTLError(f"required STL native library missing or stale: {path}: {exc}") from exc
        functions["library"] = lib  # retain CDLL as well as its function pointers
        _cache = key, functions
        return functions


def _pack(triangles):
    flat = array.array("d")
    extend = flat.extend
    count = 0
    for a, b, c in triangles:
        count += 1
        if count > _MAX_TRIANGLES:
            raise ValueError("STL triangle limit exceeded (2000000)")
        extend((a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]))
    return flat, (C.c_double * len(flat)).from_buffer(flat), count


def encode_stl(triangles, name="Narwhal 3D", binary=True):
    global _native_calls
    binding = _binding()
    flat, coords, count = _pack(triangles)
    # Binary truncates characters BEFORE encoding; Unicode whitespace in the
    # solid-prefix rule is Python's str.strip(), not Rust's byte approximation.
    if binary:
        text = str(name)
        if text.strip().lower().startswith("solid"):
            text = "Narwhal 3D"
        text = text[:80]
    else:
        text = str(name or "Narwhal 3D")
    if len(text) > _MAX_NAME:
        raise ValueError("STL name limit exceeded")
    encoded = text.encode("latin-1")
    n = binding["stl_size_owned"](coords, count, encoded, len(encoded), bool(binary))
    if n == -2 and binary:
        raise OverflowError("float too large to pack with f format")
    bound = 84 + 50 * count if binary else min(_MAX_BYTES, 128 + 2 * len(encoded) + 384 * count)
    if n < 0 or n > bound or n > _MAX_BYTES or (binary and n != bound):
        raise NativeSTLError(f"STL native encoder returned invalid size {n}")
    out = (C.c_ubyte * n)()
    rc = binding["stl_encode_owned"](coords, count, encoded, len(encoded), bool(binary), out, n)
    if rc != 0:
        raise NativeSTLError(f"STL native encoder failed ({rc})")
    data = bytes(out)
    if binary:
        if data[:80] != encoded.ljust(80, b" ") or struct.unpack_from("<I", data, 80)[0] != count:
            raise NativeSTLError("STL native encoder returned malformed binary output")
    elif not (data.startswith(b"solid " + encoded + b"\n") and data.endswith(b"endsolid " + encoded + b"\n")):
        raise NativeSTLError("STL native encoder returned malformed ASCII output")
    _native_calls += 1
    return data


def _decode_error(data, diag):
    code, a, b = diag
    messages = {
        -10: "STL ASCII: facet not closed by endfacet",
        -11: "STL ASCII: vertex outside a facet",
        -12: "STL ASCII: facet has more than 3 vertices",
        -13: "STL ASCII: vertex has fewer than 3 components",
        -14: "STL ASCII: endfacet without an open facet",
        -15: f"STL ASCII: facet has {a} vertices, need exactly 3",
        -20: f"STL binary truncated: {len(data)} bytes, need at least 84 (header + count)",
        -21: f"STL binary truncated: {len(data)} bytes but count={a} requires {b}",
    }
    if code == -16 and 0 <= a <= len(data) and 0 < b <= len(data) - a:
        token = data[a:a + b].decode("latin-1")
        # Match CPython's exception spelling, without executing a second parser.
        raise ValueError(f"could not convert string to float: {token!r}")
    if code in messages:
        raise ValueError(messages[code])
    raise NativeSTLError(f"STL native decoder returned invalid diagnostic {tuple(diag)}")


def decode_stl(data, mode=0):
    global _native_decode_calls
    binding = _binding()
    if not isinstance(data, bytes):
        data = bytes(data)
    if len(data) > _MAX_BYTES:
        raise ValueError("STL input byte limit exceeded (512 MiB)")
    diag = (C.c_int64 * 3)()
    n = binding["stl_file_size"](data, len(data), mode, diag)
    if n < 0:
        if diag[0] != n:
            raise NativeSTLError("STL native decoder returned inconsistent diagnostic")
        _decode_error(data, diag)
    if n % 9 or n // 9 > _MAX_TRIANGLES or n * 8 > min(_MAX_BYTES, 1024 + 2 * len(data)):
        raise NativeSTLError(f"STL native decoder returned excessive coordinate count {n}")
    flat = array.array("d", [0.0]) * n
    out = (C.c_double * n).from_buffer(flat)
    rc = binding["stl_file_decode"](data, len(data), mode, out, n)
    if rc != 0:
        raise NativeSTLError(f"STL native decoder failed ({rc})")
    # array iteration boxes each double once in C; repeated Python indexing and
    # per-facet slicing are avoided. All tuples own their values after return.
    values = iter(flat)
    points = zip(values, values, values)
    triangles = list(zip(points, points, points))
    _native_decode_calls += 1
    return triangles


def facet_normal(a, b, c):
    binding = _binding()
    flat, coords, _ = _pack(((a, b, c),))
    out = (C.c_double * 3)()
    rc = binding["stl_normal"](coords, out)
    if rc != 0:
        raise NativeSTLError(f"STL native normal failed ({rc})")
    return tuple(out)


def decode_stl_mesh(data):
    """Required Rust validated/welded mesh plan; never replay a file decode.

    Rust validates all coordinates against finite destination-f32 range before
    welding or publishing buffers. Python only allocates and checks ABI bounds.
    """
    global _native_mesh_calls
    binding = _binding()
    if not isinstance(data, bytes):
        data = bytes(data)
    if len(data) > _MAX_BYTES:
        raise ValueError("STL input byte limit exceeded (512 MiB)")
    diag = (C.c_int64 * 3)()
    n = binding["stl_file_size"](data, len(data), 3, diag)
    if n < 0:
        if diag[0] != n:
            raise NativeSTLError("STL import returned inconsistent size diagnostic")
        _decode_error(data, diag)
    if n % 9 or n // 9 > _MAX_TRIANGLES or n * 8 > min(_MAX_BYTES, 1024 + 2 * len(data)):
        raise NativeSTLError(f"STL import returned excessive coordinate count {n}")
    # Worst-case capacities: three unique vertices and three indices per facet.
    if n * 8 + n // 3 * 4 > _MAX_BYTES:
        raise NativeSTLError("STL import mesh output byte limit exceeded")
    vertices = array.array("d", [0.0]) * n
    indices = array.array("I", [0]) * (n // 3)
    vbuf = (C.c_double * n).from_buffer(vertices)
    ibuf = (C.c_uint32 * (n // 3)).from_buffer(indices)
    nv, nf = C.c_int64(-1), C.c_int64(-1)
    rc = binding["stl_import_mesh_decode"](
        data, len(data), vbuf, n // 3, ibuf, n // 3,
        C.byref(nv), C.byref(nf), diag)
    if rc != 0:
        if diag[0] != rc:
            raise NativeSTLError("STL import returned inconsistent decode diagnostic")
        if rc == -30 and 0 <= diag[1] < n and diag[2] in (1, 2):
            index = diag[1]
            reason = ("nonfinite (NaN or infinity)" if diag[2] == 1 else
                      "outside finite destination float32 range")
            raise ValueError(
                f"STL import: invalid numeric coordinate at facet {index // 9 + 1}, "
                f"vertex {index % 9 // 3 + 1}, component {'xyz'[index % 3]}: {reason}")
        _decode_error(data, diag)
    if diag[0] != 0 or not 0 <= nv.value <= n // 3 or not 0 <= nf.value <= n // 9:
        raise NativeSTLError("STL import returned malformed mesh counts")
    _native_mesh_calls += 1
    return vertices, indices, nv.value, nf.value
