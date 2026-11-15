"""Required DXF writer ABI. No Python byte serializer or native_pure dependency.

Python snapshots Python iterables, resolves Python mapping key identity, marshals
objects and frames CPython Unicode errors. Rust owns validation, layer merging,
unit/version headers, numeric formatting and the complete serializer.
"""
import array
import ctypes as C
import os
from pathlib import Path
import sys
import threading

_MAX_BYTES = 512 * 1024 * 1024
_MAX_NAMES = 64 * 1024 * 1024
_MAX_LAYERS = 1_000_000
_MAX_ENTITIES = 5_000_000
_MAX_COORDS = 30_000_000
_TYPES = {"LINE": 0, "CIRCLE": 1, "ARC": 2, "POLYLINE": 3, "SPLINE": 4}
_COLOR_ERROR = "DXF color is neither ACI 0..255 nor true RGB 0x000000..0xFFFFFF"
_cache = None
_lock = threading.Lock()
_native_dxf_calls = 0


class NativeDXFError(RuntimeError):
    """Required DXF library missing/stale or violating its protocol."""


class Layer(C.Structure):
    _fields_ = [("name", C.c_int64), ("color", C.c_int64)]


class Entity(C.Structure):
    _fields_ = [(key, C.c_int64) for key in
                ("kind", "name", "color", "points", "closed", "true_color", "label")]


class Input(C.Structure):
    _fields_ = [("version", C.c_uint64), ("names", C.c_void_p),
                ("names_len", C.c_size_t), ("lengths", C.POINTER(C.c_int64)),
                ("names_count", C.c_size_t), ("layers", C.POINTER(Layer)),
                ("layer_count", C.c_size_t), ("entities", C.POINTER(Entity)),
                ("entity_count", C.c_size_t), ("nums", C.POINTER(C.c_double)),
                ("nums_len", C.c_size_t), ("unit", C.c_void_p),
                ("unit_len", C.c_size_t), ("unit_mode", C.c_ubyte)]


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
            version = library.narwhal_pure_core_abi_version
            version.argtypes, version.restype = [], C.c_uint32
            if version() != 1:
                raise NativeDXFError("required DXF native ABI version mismatch")
            owned_version = library.narwhal_pure_core_dxf_owned_abi_version
            owned_version.argtypes, owned_version.restype = [], C.c_uint32
            if owned_version() != 2:
                raise NativeDXFError("required DXF owned ABI version mismatch")
            ip = C.POINTER(C.c_int64)
            signatures = {
                "colors": [ip, C.c_size_t, ip],
                "units": [C.c_void_p, C.c_size_t, C.c_ubyte, ip],
                "capacity": [C.POINTER(Input), C.POINTER(C.c_size_t), ip],
                "encode": [C.POINTER(Input), C.c_void_p, C.c_size_t, ip, ip, ip],
            }
            functions = {"library": library, "abi_version": owned_version}
            for name, args in signatures.items():
                fn = getattr(library, "narwhal_pure_core_dxf_owned_" + name)
                fn.argtypes, fn.restype = args, C.c_int32
                functions[name] = fn
        except (OSError, AttributeError) as exc:
            raise NativeDXFError(f"required DXF native library missing or stale: {path}: {exc}") from exc
        _cache = key, functions
        return functions


def _call(function, *args):
    try:
        return function(*args)
    except (C.ArgumentError, OSError, TypeError, ValueError) as exc:
        raise NativeDXFError(f"DXF native ABI call failed: {exc}") from exc


def _unit_input(units):
    # Original dict lookup requires a hashable Python key, and only strings can
    # match the public name table. Do not stringify unknown Python objects.
    if units is not None:
        hash(units)
    return (units.encode("utf-8", "surrogatepass") if isinstance(units, str) else b""), int(units is not None)


def unit_header(units):
    name, mode = _unit_input(units)
    out = (C.c_int64 * 2)(-1, -1)
    rc = _call(_binding()["units"], name, len(name), mode, out)
    if rc != 0 or not 0 <= out[0] <= 17 or out[1] not in (-1, 0, 1):
        raise NativeDXFError(f"DXF native unit metadata failure ({rc})")
    return out[0], None if out[1] == -1 else out[1]


def _error(rc, diag, entities):
    if diag[0] != rc:
        raise NativeDXFError("DXF native returned inconsistent diagnostic")
    if rc == -40:
        raise ValueError(_COLOR_ERROR)
    if rc == -41:
        raise ValueError("DXF true_color is outside 0x000000..0xFFFFFF")
    if rc == -42 and 0 <= diag[1] < len(entities):
        raise ValueError("unknown entity type %r" % (entities[diag[1]]["type"],))
    if rc == -43:
        raise ValueError("SPLINE needs a connected cubic Bezier control chain")
    messages = {-44: "DXF invalid numeric coordinate: nonfinite",
                -45: "DXF invalid layer name: NUL or newline",
                -46: "DXF invalid entity flags", -47: "DXF invalid radius: must be positive"}
    if rc in messages:
        raise ValueError(f"{messages[rc]} (index {diag[1]})")
    if rc == -3:
        raise ValueError("DXF input/output limits exceeded")
    raise NativeDXFError(f"DXF native writer failed ({rc}, {tuple(diag)})")


def _color(value, explicit=False):
    value = int(value)
    # Prevent ctypes' silent integer wrapping; range semantics themselves live
    # in Rust. An unrepresentable host int cannot be passed through this ABI.
    if not -(1 << 63) <= value < (1 << 63):
        raise ValueError("DXF true_color is outside 0x000000..0xFFFFFF" if explicit else _COLOR_ERROR)
    return value


def _snapshot(entities):
    result = []
    for entity in entities:
        if len(result) >= _MAX_ENTITIES:
            raise ValueError("DXF entity limit exceeded")
        if entity.get("type") in ("POLYLINE", "SPLINE"):
            entity = dict(entity)
            key = "points" if entity["type"] == "POLYLINE" else "control_points"
            points = []
            for point in entity[key]:
                if len(points) >= _MAX_COORDS // 3:
                    raise ValueError("DXF coordinate limit exceeded")
                points.append(point)
            entity[key] = points
        result.append(entity)
    return result


def _pack(entities, layers, units, binding):
    # Python equality/hash semantics (e.g. 1 == True, but 1 != "1") cannot be
    # reproduced for arbitrary host mapping keys in Rust. Marshal opaque IDs;
    # Rust, not this dictionary, chooses table order, colors and first-seen merge.
    handles, names, lengths = {}, bytearray(), array.array("q")
    key_objects, encoded_names, aliases = [], [], {}
    def handle(key):
        if key not in handles:
            if len(lengths) >= _MAX_LAYERS:
                raise ValueError("DXF layer limit exceeded")
            encoded = str(key).encode("utf-8", "surrogatepass")
            if len(names) + len(encoded) > _MAX_NAMES:
                raise ValueError("DXF layer name byte limit exceeded")
            handles[key] = len(lengths)
            names.extend(encoded)
            lengths.append(len(encoded))
            key_objects.append(key)
            encoded_names.append(encoded)
        return handles[key]
    def label(key, identity):
        if type(key) is str and type(key_objects[identity]) is str:
            return identity
        encoded = str(key).encode("utf-8", "surrogatepass")
        if encoded == encoded_names[identity]:
            return identity
        alias = (identity, encoded)
        if alias in aliases:
            return aliases[alias]
        if len(lengths) >= _MAX_LAYERS or len(names) + len(encoded) > _MAX_NAMES:
            raise ValueError("DXF layer label limit exceeded")
        result = len(lengths)
        names.extend(encoded)
        lengths.append(len(encoded))
        key_objects.append(key)
        encoded_names.append(encoded)
        aliases[alias] = result
        return result
    layer_records = []
    if layers:
        for name, color in layers.items():
            layer_records.append((handle(name), _color(color)))
    color_values = (C.c_int64 * len(layer_records))(*(color for _, color in layer_records))
    diag = (C.c_int64 * 3)()
    rc = _call(binding["colors"], color_values, len(color_values), diag)
    if rc != 0:
        _error(rc, diag, entities)
    records, coords = array.array("q"), array.array("d")
    extend = coords.extend
    for e in entities:
        typ = e["type"]
        kind = _TYPES.get(typ, -1) if isinstance(typ, str) else -1
        key = e.get("layer", "0")
        lid = handle(key)
        text_id = label(key, lid)
        explicit = "true_color" in e
        color = _color(e["true_color"] if explicit else e.get("color", 7), explicit)
        count = 0
        if kind == 0:
            a, b = e["p0"], e["p1"]
            extend((a[0], a[1], a[2], b[0], b[1], b[2]))
        elif kind in (1, 2):
            c = e["center"]
            extend((c[0], c[1], c[2], e["radius"]))
            if kind == 2:
                extend((e["start_deg"], e["end_deg"]))
        elif kind in (3, 4):
            points = e["points"] if kind == 3 else e["control_points"]
            count = len(points)
            for point in points:
                extend((point[0], point[1], point[2]))
        if len(coords) > _MAX_COORDS:
            raise ValueError("DXF coordinate limit exceeded")
        records.extend((kind, lid, color, count, int(bool(e.get("closed"))) if kind in (3, 4) else 0, int(explicit), text_id))
    layer_array = (Layer * len(layer_records))(*(Layer(*row) for row in layer_records))
    entity_array = (Entity * len(entities)).from_buffer(records)
    name_view = (C.c_ubyte * len(names)).from_buffer(names)
    length_view = (C.c_int64 * len(lengths)).from_buffer(lengths)
    coord_view = (C.c_double * len(coords)).from_buffer(coords)
    unit, mode = _unit_input(units)
    if len(unit) > _MAX_NAMES:
        raise ValueError("DXF unit name byte limit exceeded")
    unit_view = C.c_char_p(unit)
    descriptor = Input(2, C.cast(name_view, C.c_void_p), len(names), length_view,
                       len(lengths), layer_array, len(layer_array), entity_array,
                       len(entity_array), coord_view, len(coords),
                       C.cast(unit_view, C.c_void_p), len(unit), mode)
    return descriptor, (names, lengths, coords, records, layer_array, entity_array,
                        name_view, length_view, coord_view, unit, unit_view)


def encode_dxf(entities, layers=None, units=None):
    """Return caller-owned (bytearray, written), or raise; never decline/replay."""
    global _native_dxf_calls
    entities = _snapshot(entities)
    binding = _binding()
    descriptor, keepalive = _pack(entities, layers, units, binding)
    size, diag = C.c_size_t(0), (C.c_int64 * 3)()
    rc = _call(binding["capacity"], C.byref(descriptor), C.byref(size), diag)
    if rc != 0:
        _error(rc, diag, entities)
    if diag[0] != 0 or not 0 < size.value <= min(_MAX_BYTES, sys.maxsize):
        raise NativeDXFError(f"DXF native writer returned invalid capacity {size.value}")
    out = bytearray(size.value)
    view = (C.c_ubyte * len(out)).from_buffer(out)
    written, encoding = C.c_int64(-1), C.c_int64(-1)
    rc = _call(binding["encode"], C.byref(descriptor), view, len(out), C.byref(written), C.byref(encoding), diag)
    if rc != 0:
        _error(rc, diag, entities)
    n = written.value
    if diag[0] != 0 or not 0 < n <= len(out) or encoding.value not in (0, 1):
        raise NativeDXFError("DXF native writer returned malformed output/count")
    if out[:19] != b"0\nSECTION\n2\nHEADER\n" or out[n-6:n] != b"0\nEOF\n":
        raise NativeDXFError("DXF native writer returned malformed file framing")
    if encoding.value == 1:
        # Rust produced the whole original text. This is CPython exception
        # framing, never a fallback serializer and never written to a file.
        bytes(memoryview(out)[:n]).decode("utf-8", "surrogatepass").encode("latin-1")
        raise NativeDXFError("DXF native writer incorrectly flagged Latin-1 data")
    _native_dxf_calls += 1
    return out, n
