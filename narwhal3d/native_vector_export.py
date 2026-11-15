"""Required vector ownership ABI v1. Host marshalling/encoding only, no codec.

Rust owns validation, formatting, units, RGB rounding, layers and PDF xrefs.
No native_pure import, optional route, threshold or Python serialization replay.
"""
import array
import ctypes as C
import os
from pathlib import Path
import sys
import threading

_MAX_BYTES = 512 * 1024 * 1024
_RGB_INTEGER_LIMIT = (1 << 53) // 255
_cache = None
_lock = threading.Lock()
_native_calls = 0


class NativeVectorError(RuntimeError):
    """Required library/ABI/protocol failure; must not be treated as bad geometry."""


class VectorInputError(ValueError):
    """Named vector input/resource refusal, before opening a destination."""


def _span(name, element):
    return type(name, (C.Structure,), {"_fields_": [("data", C.POINTER(element)), ("len", C.c_size_t)]})


Doubles = _span("VectorDoublesV1", C.c_double)
Ints = _span("VectorIntsV1", C.c_int64)
Flags = _span("VectorFlagsV1", C.c_int32)
Bytes = _span("VectorBytesV1", C.c_ubyte)


class Request(C.Structure):
    _fields_ = [("size", C.c_size_t), ("format", C.c_uint32), ("digits", C.c_int32),
                ("page_w_mm", C.c_double), ("page_h_mm", C.c_double), ("unit_mm", C.c_double),
                ("segments", C.c_size_t), ("beziers", Doubles), ("counts", Ints),
                ("closed", Flags), ("rgb", Doubles), ("widths", Doubles),
                ("layers", Bytes), ("layer_lengths", Ints), ("title", Bytes), ("unit", Bytes), ("title_parts", Ints)]


def _binding():
    global _cache
    key = os.environ.get("NARWHAL_PURE_CORE") or "<packaged>"
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    filename = ("narwhal_pure_core.dll" if sys.platform == "win32" else
                "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else "libnarwhal_pure_core.so")
    path = Path(key).expanduser() if key != "<packaged>" else Path(__file__).resolve().parent / "native" / filename
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1]
        try:
            lib = C.CDLL(str(path))
            version = lib.narwhal_pure_core_abi_version
            version.argtypes, version.restype = [], C.c_uint32
            if version() != 1:
                raise NativeVectorError("required vector native ABI version mismatch")
            encode = lib.narwhal_pure_core_vector_encode_v1
            encode.argtypes, encode.restype = [C.POINTER(Request), C.POINTER(C.c_void_p)], C.c_int32
            length = lib.narwhal_pure_core_vector_len_v1
            length.argtypes, length.restype = [C.c_void_p], C.c_size_t
            copy = lib.narwhal_pure_core_vector_copy_v1
            copy.argtypes, copy.restype = [C.c_void_p, C.POINTER(C.c_ubyte), C.c_size_t, C.POINTER(C.c_size_t)], C.c_int32
            free = lib.narwhal_pure_core_vector_free_v1
            free.argtypes, free.restype = [C.c_void_p], None
            validate = lib.narwhal_pure_core_vector_validate_v1
            validate.argtypes, validate.restype = [C.c_uint32, C.POINTER(C.c_ubyte), C.c_size_t], C.c_int32
        except (OSError, AttributeError) as exc:
            raise NativeVectorError(f"required vector native library missing or stale: {path}: {exc}") from exc
        binding = encode, length, copy, free, validate
        _cache = key, binding
        return binding


def _bytes(text, encoding):
    # Title/layer encoding is a Python host boundary. In particular do NOT turn
    # non-text titles into str(value), which silently changed legacy errors.
    if not isinstance(text, str):
        text = "".join(list(text))
    return text.encode(encoding)


def _view(values, span):
    element = span._fields_[0][1]._type_
    view = (element * len(values)).from_buffer(values)
    return span(view, len(values))


def _pack(strokes, width, height, title, unit, pdf):
    title_parts = array.array("q")
    if pdf and not isinstance(title, str):
        atoms = list(title)
        title_bytes = bytearray("".join(atoms).encode("latin-1"))
        title_parts.extend(len(atom) for atom in atoms)
    else:
        title_bytes = bytearray(title.encode("latin-1")) if pdf else bytearray()
    try:
        rows = list(strokes)
        if len(rows) > 1_000_000:
            raise VectorInputError("vector export stroke count exceeds 1000000")
        bz, rgb, widths = array.array("d"), array.array("d"), array.array("d")
        counts, lens, flags = array.array("q"), array.array("q"), array.array("i")
        layers = bytearray()
        for row in rows:
            segments = row.beziers
            try:
                nseg = len(segments)
            except TypeError:
                segments = list(segments)
                nseg = len(segments)
            counts.append(nseg)
            if len(bz) // 8 + nseg > 2_000_000:
                raise VectorInputError("vector export segment count exceeds 2000000")
            # Unpacking validates all four 2D controls, including unused p0s,
            # while making one array conversion instead of four per segment.
            for (x0, y0), (x1, y1), (x2, y2), (x3, y3) in segments:
                bz.extend((x0, y0, x1, y1, x2, y2, x3, y3))
            r, g, b = row.rgb
            if not pdf:
                for component in (r, g, b):
                    if isinstance(component, int) and not -_RGB_INTEGER_LIMIT <= component <= _RGB_INTEGER_LIMIT:
                        raise VectorInputError("SVG integer RGB exceeds the exact binary64 product range; supply finite float RGB")
            rgb.extend((r, g, b))
            widths.append(row.width_mm)
            flags.append(1 if row.closed else 0)
            name = _bytes(row.layer, "latin-1" if pdf else "utf-8") if row.layer else b""
            layers.extend(name)
            lens.append(len(name))
            if len(layers) > 64 * 1024 * 1024:
                raise VectorInputError("vector export layer names exceed 64 MiB")
        suffix, scale, digits = ("", 1., 4) if pdf or unit is None else unit
        digits = int(digits)
        if not -(2**31) <= digits < 2**31:
            raise VectorInputError("vector export precision is outside the native integer range")
        unit_bytes = bytearray(_bytes(suffix, "utf-8"))
        req = Request(C.sizeof(Request), 0 if pdf else 1, digits, float(width), float(height), float(scale),
                      len(bz) // 8, _view(bz, Doubles), _view(counts, Ints), _view(flags, Flags),
                      _view(rgb, Doubles), _view(widths, Doubles), _view(layers, Bytes), _view(lens, Ints),
                      _view(title_bytes, Bytes), _view(unit_bytes, Bytes), _view(title_parts, Ints))
        return req, (bz, counts, flags, rgb, widths, layers, lens, title_bytes, unit_bytes, title_parts)
    except UnicodeEncodeError:
        raise
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, VectorInputError):
            raise
        raise VectorInputError(f"vector export invalid control shape or value: {exc}") from exc


def encode(strokes, width, height, *, title="", unit=None, pdf=False):
    """Return a caller-owned bytearray, freeing the Rust result on every path."""
    global _native_calls
    req, keepalive = _pack(strokes, width, height, title, unit, pdf)
    fn, length, copy, free, validate = _binding()
    handle = C.c_void_p()
    try:
        code = fn(C.byref(req), C.byref(handle))
        if code:
            if code in (-2, -3, -4, -5):
                reasons = {-2: "invalid metadata or text", -3: "resource/precision limit exceeded (precision 0..4096; formatted bound 512 MiB)",
                           -4: "nonfinite coordinate, RGB, width, page or converted value", -5: "unit scale must be positive and finite"}
                raise VectorInputError(f"vector export refused: {reasons[code]}")
            raise NativeVectorError(f"vector native encode failed ({code})")
        if not handle.value:
            raise NativeVectorError("vector native encoder returned no owned result")
        n = length(handle)
        if not 16 <= n <= min(_MAX_BYTES, sys.maxsize):
            raise NativeVectorError(f"vector native encoder returned invalid byte count ({n})")
        out = bytearray(n)
        view = (C.c_ubyte * n).from_buffer(out)
        written = C.c_size_t(0)
        if copy(handle, view, n, C.byref(written)) != 0 or written.value != n:
            raise NativeVectorError("vector native result copy failed or returned invalid framing")
        if validate(req.format, view, n) != 0:
            raise NativeVectorError("vector native encoder returned malformed document framing/xref/UTF-8")
        _native_calls += 1
        return out
    except (C.ArgumentError, OSError) as exc:
        raise NativeVectorError(f"vector native ABI call failed: {exc}") from exc
    finally:
        if handle.value:
            free(handle)
