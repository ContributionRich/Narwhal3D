"""Required reader24 C ABI, with no DXF or Python numeric-parser fallback.

Rust owns the DXF grammar, field/order semantics, numeric parsing, exact file
entities and errors. Python only does I/O-facing buffer/ctypes marshalling,
constructs Python values from the typed result, and frames Python exceptions.
Returned nonfinite values are preserved file data, never certified geometry.
"""
import ctypes as C
import os
from pathlib import Path
import struct
import sys
import threading

_MAX_INPUT = 512 * 1024 * 1024
_MAX_OUTPUT = 512 * 1024 * 1024
_MAX_ITEMS = 4_000_000
_MAGIC = b"DXFR24\0\x01"
_cache = None
_lock = threading.Lock()
_native_decode_dxf_calls = 0
_U64, _I64, _F64, _POINT = (struct.Struct(fmt) for fmt in ("<Q", "<q", "<d", "<3d"))
# Fixed typed-result layout emitted by Rust for analytic LINE dictionaries.
# These are protocol keys/tags, not DXF input syntax or numeric computation.
def _text_key(value):
    return b"\6" + _U64.pack(len(value)) + value


_LINE_LEAD = _text_key(b"type") + _text_key(b"LINE") + _text_key(b"layer") + b"\6"
_LINE_COLOR = _text_key(b"color")
_LINE_TRUE_COLOR = _text_key(b"true_color")
_LINE_P0 = _text_key(b"p0") + b"\10"
_LINE_P1 = _text_key(b"p1") + b"\10"
_LINE_COORDS = struct.Struct("<12s3d12s3d")


class NativeDXFReaderError(RuntimeError):
    """Required library, ABI, protocol or explicit resource admission failure."""


class FileFormatError(ValueError):
    """Default direct-adapter bad-file type; public reader supplies its own class."""


class Diagnostic(C.Structure):
    _fields_ = [("code", C.c_int64), ("start", C.c_size_t), ("len", C.c_size_t),
                ("detail", C.c_int64), ("required", C.c_size_t)]


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
            library = C.CDLL(str(path))
            core = library.narwhal_pure_core_abi_version
            core.argtypes, core.restype = [], C.c_uint32
            abi = library.narwhal_pure_core_dxf_reader24_abi_version
            abi.argtypes, abi.restype = [], C.c_uint32
            if core() != 1 or abi() != 1:
                raise NativeDXFReaderError("required DXF reader ABI version mismatch")
            fn = library.narwhal_pure_core_dxf_reader24_decode
            fn.argtypes = [C.c_void_p, C.c_size_t, C.c_uint32, C.c_void_p,
                           C.c_size_t, C.POINTER(C.c_size_t), C.POINTER(Diagnostic)]
            fn.restype = C.c_int32
        except (OSError, AttributeError) as exc:
            raise NativeDXFReaderError(f"required DXF reader library missing or stale: {path}: {exc}") from exc
        _cache = key, (library, fn)
        return _cache[1]


def _raise_error(rc, diag, data, int_limit, format_error):
    if diag.code != rc:
        raise NativeDXFReaderError("DXF reader inconsistent diagnostic")
    span_codes = (-102, -110, -111, -115)
    if (diag.required or (rc not in span_codes and (diag.start or diag.len))
            or (rc not in (-112, -115) and diag.detail)):
        raise NativeDXFReaderError("DXF reader invalid error diagnostic fields")
    messages = {
        -100: "binary DXF (AutoCAD Binary DXF sentinel) -- this reader is ASCII R12 only",
        -101: "truncated DXF: group code with no value",
        -103: "DXF layer true color is outside 0x000000..0xFFFFFF",
        -104: "DXF entity true color is outside 0x000000..0xFFFFFF",
        -105: "periodic DXF SPLINE is not implemented exactly",
        -106: "DXF SPLINE has an invalid degree/control count",
        -107: "DXF SPLINE has inconsistent exact NURBS data",
    }
    if rc in messages:
        raise format_error(messages[rc])
    if rc in (-102, -110, -111, -115):
        if diag.start > len(data) or diag.len > len(data) - diag.start:
            raise NativeDXFReaderError("DXF reader invalid diagnostic span")
        token = data[diag.start:diag.start + diag.len].decode("latin-1")
        if rc == -102:
            raise format_error(f"malformed ASCII DXF version ($ACADVER={token})")
        if rc == -110:
            # CPython int() bounds the repr to 200 characters (without adding
            # an ellipsis). This is error framing, not a second numeric parser.
            raise ValueError(f"invalid literal for int() with base 10: {repr(token)[:200]}")
        if rc == -111:
            raise ValueError(f"could not convert string to float: {token!r}")
        if not int_limit or diag.detail <= int_limit:
            raise NativeDXFReaderError("DXF reader invalid integer-limit diagnostic")
        raise ValueError(f"Exceeds the limit ({int_limit} digits) for integer string conversion: "
                         f"value has {diag.detail} digits; use sys.set_int_max_str_digits() to increase the limit")
    if rc == -112 and diag.detail in (10, 20, 30, 11, 21, 31, 40, 50, 51):
        raise KeyError(diag.detail)
    if rc == -113:
        raise OverflowError("cannot convert float infinity to integer")
    if rc == -114:
        raise ValueError("cannot convert float NaN to integer")
    if rc == -3:
        raise NativeDXFReaderError("DXF reader resource limit exceeded")
    raise NativeDXFReaderError(f"DXF reader native protocol failure ({rc})")


def _unpack(payload):
    """Unmarshal the typed C result, not DXF input or numeric strings."""
    view = memoryview(payload)
    offset = 8
    if len(view) < 8 or view[:8] != _MAGIC:
        raise NativeDXFReaderError("DXF reader invalid result magic/version")

    def take(n):
        nonlocal offset
        if n < 0 or n > len(view) - offset:
            raise NativeDXFReaderError("DXF reader truncated typed result")
        result = view[offset:offset + n]
        offset += n
        return result

    def scalar(codec):
        nonlocal offset
        if codec.size > len(view) - offset:
            raise NativeDXFReaderError("DXF reader truncated typed result")
        value = codec.unpack_from(view, offset)
        offset += codec.size
        return value

    def count(item_bytes=1):
        n = scalar(_U64)[0]
        if n > _MAX_ITEMS or n > (len(view) - offset) // item_bytes:
            raise NativeDXFReaderError("DXF reader excessive typed count")
        return n

    texts = {}
    checked_lines = set()

    def text(n):
        raw = take(n).tobytes()
        value = texts.get(raw)
        if value is None:
            value = raw.decode("latin-1")
            if len(texts) < 4096 and n <= 256:
                texts[raw] = value
        return value

    def line(fields, depth):
        nonlocal offset
        offset += len(_LINE_LEAD)
        layer = text(scalar(_U64)[0])
        if take(len(_LINE_COLOR)) != _LINE_COLOR:
            raise NativeDXFReaderError("DXF reader malformed LINE color key")
        color = node(depth + 1)
        if type(color) is not int:
            raise NativeDXFReaderError("DXF reader malformed LINE color type")
        result = {"type": "LINE", "layer": layer, "color": color}
        if fields == 6:
            if take(len(_LINE_TRUE_COLOR)) != _LINE_TRUE_COLOR:
                raise NativeDXFReaderError("DXF reader malformed LINE true-color key")
            rgb = node(depth + 1)
            if type(rgb) is not int:
                raise NativeDXFReaderError("DXF reader malformed LINE true-color type")
            result["true_color"] = rgb
        coordinates = scalar(_LINE_COORDS)
        if coordinates[0] != _LINE_P0 or coordinates[4] != _LINE_P1:
            raise NativeDXFReaderError("DXF reader malformed LINE coordinate keys/tags")
        result["p0"], result["p1"] = coordinates[1:4], coordinates[5:8]
        # Key set/order, exact integer types and tuple/f64 arity were checked
        # while decoding. Do not repeat the generic dictionary schema walk.
        checked_lines.add(id(result))
        return result

    def node(depth=0):
        nonlocal offset
        if depth > 16:
            raise NativeDXFReaderError("DXF reader excessive typed nesting")
        if offset >= len(view):
            raise NativeDXFReaderError("DXF reader truncated typed result")
        tag = view[offset]
        offset += 1
        if tag == 0:
            return None
        if tag in (1, 2):
            return tag == 2
        if tag == 3:
            return scalar(_I64)[0]
        if tag == 4:
            sign = take(1)[0]
            n = count()
            if sign > 1 or n > 8192:
                raise NativeDXFReaderError("DXF reader invalid integer representation")
            # Construct a PyLong from Rust's binary magnitude; no int(str)
            # grammar, limits or decimal conversion is repeated in Python.
            value = int.from_bytes(take(n), "little")
            return -value if sign else value
        if tag == 5:
            return scalar(_F64)[0]
        if tag == 6:
            return text(scalar(_U64)[0])
        if tag == 7:
            return [node(depth + 1) for _ in range(count())]
        if tag == 8:
            return scalar(_POINT)
        if tag == 9:
            n = count(8)
            return list(struct.unpack(f"<{n}d", take(n * 8)))
        if tag == 10:
            n = count(24)
            return list(_POINT.iter_unpack(take(n * 24)))
        if tag == 11:
            fields = count(10)
            if fields in (5, 6) and view[offset:offset + len(_LINE_LEAD)] == _LINE_LEAD:
                return line(fields, depth)
            result = {}
            for _ in range(fields):
                key = node(depth + 1)
                if not isinstance(key, str) or key in result:
                    raise NativeDXFReaderError("DXF reader invalid/duplicate typed key")
                result[key] = node(depth + 1)
            return result
        raise NativeDXFReaderError(f"DXF reader invalid typed tag {tag}")

    result = node()
    if offset != len(view):
        raise NativeDXFReaderError("DXF reader trailing typed bytes")
    if (not isinstance(result, dict) or list(result) !=
            ["entities", "layers", "layer_true_colors", "insunits", "skipped"]):
        raise NativeDXFReaderError("DXF reader malformed root schema")
    if (not isinstance(result["entities"], list)
            or any(not isinstance(result[k], dict) for k in ("layers", "layer_true_colors", "skipped"))
            or result["insunits"] is not None and type(result["insunits"]) is not int):
        raise NativeDXFReaderError("DXF reader malformed root types")
    # Validate the ABI's structural contract, not geometric safety. Nonfinite
    # coordinates remain exact file data; importing geometry has its own guard.
    def point(value):
        return type(value) is tuple and len(value) == 3 and all(type(x) is float for x in value)

    for key in ("layers", "layer_true_colors", "skipped"):
        if any(type(k) is not str or type(v) is not int for k, v in result[key].items()):
            raise NativeDXFReaderError("DXF reader malformed metadata types")
    schemas = {
        "LINE": {"p0", "p1"}, "CIRCLE": {"center", "radius"},
        "ARC": {"center", "radius", "start_deg", "end_deg"},
        "POLYLINE": {"points", "closed"}, "LWPOLYLINE": {"points", "closed"},
        "SPLINE": {"degree", "knots", "points", "weights", "closed"},
    }
    for entity in result["entities"]:
        if id(entity) in checked_lines:
            continue
        if not isinstance(entity, dict) or type(entity.get("type")) is not str:
            raise NativeDXFReaderError("DXF reader malformed entity type")
        fields = schemas.get(entity["type"])
        expected = {"type", "layer", "color"} | (fields or set())
        if "true_color" in entity:
            expected.add("true_color")
        if fields is None or set(entity) != expected:
            raise NativeDXFReaderError("DXF reader malformed entity fields")
        kind = entity["type"]
        valid = (type(entity["layer"]) is str and type(entity["color"]) is int
                 and ("true_color" not in entity or type(entity["true_color"]) is int))
        if kind == "LINE":
            valid = valid and point(entity["p0"]) and point(entity["p1"])
        elif kind in ("CIRCLE", "ARC"):
            valid = valid and point(entity["center"]) and type(entity["radius"]) is float
            if kind == "ARC":
                valid = valid and type(entity["start_deg"]) is float and type(entity["end_deg"]) is float
        else:
            valid = (valid and type(entity["closed"]) is bool
                     and type(entity["points"]) is list and all(point(v) for v in entity["points"]))
            if kind == "SPLINE":
                valid = (valid and type(entity["degree"]) is int
                         and all(type(entity[k]) is list and all(type(v) is float for v in entity[k])
                                 for k in ("knots", "weights")))
        if not valid:
            raise NativeDXFReaderError("DXF reader malformed entity value/arity")
    return result


def decode_dxf(data, *, format_error=FileFormatError):
    global _native_decode_dxf_calls
    if not isinstance(data, bytes):
        data = bytes(data)
    if len(data) > _MAX_INPUT:
        raise NativeDXFReaderError("DXF reader input byte limit exceeded")
    library, decode = _binding()
    int_limit = sys.get_int_max_str_digits()
    bound = min(_MAX_OUTPUT, max(4096, 32 * len(data) + 4096))
    capacity = min(bound, max(4096, 4 * len(data) + 1024))
    for attempt in range(2):
        out = bytearray(capacity)
        buf = (C.c_ubyte * capacity).from_buffer(out)
        written, diag = C.c_size_t(-1), Diagnostic()
        try:
            rc = decode(data, len(data), int_limit, buf, capacity, C.byref(written), C.byref(diag))
        except (C.ArgumentError, OSError, TypeError, ValueError) as exc:
            raise NativeDXFReaderError(f"DXF reader native ABI call failed: {exc}") from exc
        if rc == 1:
            if (attempt or diag.code != 1 or not capacity < diag.required <= bound
                    or written.value != C.c_size_t(-1).value):
                raise NativeDXFReaderError("DXF reader invalid capacity retry")
            capacity = diag.required
            continue
        if rc != 0:
            _raise_error(rc, diag, data, int_limit, format_error)
        if (any((diag.code, diag.start, diag.len, diag.detail, diag.required))
                or not 8 <= written.value <= capacity):
            raise NativeDXFReaderError("DXF reader malformed successful diagnostic/count")
        result = _unpack(memoryview(out)[:written.value])
        _native_decode_dxf_calls += 1
        return result
    raise NativeDXFReaderError("DXF reader capacity retry exhausted")
