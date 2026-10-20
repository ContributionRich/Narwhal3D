"""Optional checked Rust codec for Narwhal's declared CorelDRAW v15 vector subset."""
from __future__ import annotations

import ctypes
import json
import math
import os
from pathlib import Path
import struct
import sys
import uuid

_ABI = 1
_library = None
_checked = False
_status = "not checked"


def _names():
    if sys.platform.startswith("win"):
        return "narwhal_cdr_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_cdr_core.dylib"
    return "libnarwhal_cdr_core.so"


def _candidates():
    explicit = os.environ.get("NARWHAL_CDR_CORE")
    if explicit:
        yield Path(explicit)
    root = Path(__file__).resolve().parents[1]
    name = _names()
    yield Path(__file__).resolve().parent / "native" / name
    yield root / "runtime" / ("windows-x86_64" if sys.platform.startswith("win")
                              else "linux-x86_64") / "bin" / name
    yield root / "artifacts" / "cdr-core" / ("windows-x86_64" if sys.platform.startswith("win")
                                               else "linux-x86_64") / name


def _load():
    global _library, _checked, _status
    if _checked:
        return _library
    _checked = True
    for path in _candidates():
        if not path.is_file():
            continue
        try:
            library = ctypes.CDLL(str(path))
            library.narwhal_cdr_abi_version.argtypes = []
            library.narwhal_cdr_abi_version.restype = ctypes.c_uint32
            if library.narwhal_cdr_abi_version() != _ABI:
                _status = f"wrong ABI at {path}"
                continue
            function = library.narwhal_cdr_v15_riff
            function.argtypes = [
                ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_size_t),
            ]
            function.restype = ctypes.c_int32
            try:
                read_function = library.narwhal_cdr_v15_read
                read_function.argtypes = [
                    ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_size_t),
                ]
                read_function.restype = ctypes.c_int32
            except AttributeError:
                pass
            _library = library
            _status = f"ready: {path}"
            return library
        except (OSError, AttributeError, ValueError) as exc:
            _status = f"declined {path}: {exc}"
    _status = "native CDR core unavailable"
    return None


def reset_for_tests():
    global _library, _checked, _status
    _library = None
    _checked = False
    _status = "not checked"


def status():
    _load()
    return _status


def _valid_riff(data: bytes) -> bool:
    if (len(data) < 22 or data[:4] != b"RIFF" or data[8:12] != b"CDRF"
            or struct.unpack_from("<I", data, 4)[0] != len(data) - 8
            or data[12:16] != b"vrsn" or struct.unpack_from("<I", data, 16)[0] != 2
            or struct.unpack_from("<H", data, 20)[0] != 1500):
        return False
    pos, tags, list_kinds = 12, set(), set()
    while pos < len(data):
        if pos + 8 > len(data):
            return False
        tag = data[pos:pos + 4]
        length = struct.unpack_from("<I", data, pos + 4)[0]
        stop = pos + 8 + length
        if stop > len(data) or stop <= pos:
            return False
        tags.add(tag)
        if tag == b"LIST":
            if length < 4:
                return False
            list_kinds.add(data[pos + 8:pos + 12])
        pos = stop + (length & 1)
    return (pos == len(data) and {b"vrsn", b"LIST"} <= tags
            and {b"doc ", b"page"} <= list_kinds)


def _finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _valid_drawing(value):
    if (not isinstance(value, dict) or value.get("version") != 1500
            or not _finite_number(value.get("width_mm")) or value["width_mm"] <= 0
            or not _finite_number(value.get("height_mm")) or value["height_mm"] <= 0
            or not isinstance(value.get("objects"), list) or not value["objects"]):
        return False
    for obj in value["objects"]:
        if (not isinstance(obj, dict) or not isinstance(obj.get("layer"), str)
                or not isinstance(obj.get("hairline"), bool)
                or not _finite_number(obj.get("width_mm")) or obj["width_mm"] < 0
                or not isinstance(obj.get("rgb"), list) or len(obj["rgb"]) != 3
                or any(not isinstance(channel, int) or isinstance(channel, bool)
                       or not 0 <= channel <= 255 for channel in obj["rgb"])
                or not isinstance(obj.get("paths"), list) or not obj["paths"]):
            return False
        for path in obj["paths"]:
            if (not isinstance(path, dict) or not isinstance(path.get("closed"), bool)
                    or not isinstance(path.get("beziers"), list)
                    or not path["beziers"]):
                return False
            for bezier in path["beziers"]:
                if (not isinstance(bezier, list) or len(bezier) != 4
                        or any(not isinstance(point, list) or len(point) != 2
                               or any(not _finite_number(axis) for axis in point)
                               for point in bezier)):
                    return False
    return True


def read_v15(riff: bytes, metadata: bytes):
    """Return a validated native drawing dictionary, or None for Python fallback."""
    library = _load()
    function = getattr(library, "narwhal_cdr_v15_read", None) if library else None
    if function is None or not isinstance(riff, bytes) or not isinstance(metadata, bytes):
        return None
    try:
        riff_source = (ctypes.c_uint8 * len(riff)).from_buffer_copy(riff)
        metadata_source = (ctypes.c_uint8 * len(metadata)).from_buffer_copy(metadata)
        required = ctypes.c_size_t()
        code = function(riff_source, len(riff), metadata_source, len(metadata),
                        None, 0, ctypes.byref(required))
        if code != 1 or required.value < 2 or required.value > 512 * 1024 * 1024:
            return None
        output = (ctypes.c_uint8 * required.value)()
        code = function(riff_source, len(riff), metadata_source, len(metadata),
                        output, len(output), ctypes.byref(required))
        if code != 0 or required.value != len(output):
            return None
        value = json.loads(bytes(output))
        return value if _valid_drawing(value) else None
    except (MemoryError, OverflowError, TypeError, ValueError, ctypes.ArgumentError):
        return None


def riff_v15(strokes, page_w_mm, page_h_mm, hairline=False):
    """Return a native RIFF stream, or None to request the complete Python fallback."""
    library = _load()
    if library is None:
        return None
    layers = []
    layer_names = []
    encoded_strokes = []
    try:
        for stroke in strokes:
            if not stroke.beziers:
                continue
            layer = str(stroke.layer or "Layer 1")
            if layer not in layer_names:
                layer_names.append(layer)
                layers.append({"name": layer, "guid_le": list(uuid.uuid4().bytes_le)})
            encoded_strokes.append({
                "beziers": stroke.beziers,
                "closed": bool(stroke.closed),
                "rgb": stroke.rgb,
                "width_mm": float(stroke.width_mm),
                "layer": layer,
            })
        packet = json.dumps({
            "page_w_mm": float(page_w_mm),
            "page_h_mm": float(page_h_mm),
            "hairline": bool(hairline),
            "strokes": encoded_strokes,
            "layers": layers,
        }, separators=(",", ":"), allow_nan=False).encode("utf-8")
        source = (ctypes.c_uint8 * len(packet)).from_buffer_copy(packet)
        required = ctypes.c_size_t()
        function = library.narwhal_cdr_v15_riff
        code = function(source, len(packet), None, 0, ctypes.byref(required))
        if code != 1 or required.value < 12 or required.value > 512 * 1024 * 1024:
            return None
        output = (ctypes.c_uint8 * required.value)()
        code = function(source, len(packet), output, len(output), ctypes.byref(required))
        if code != 0 or required.value != len(output):
            return None
        data = bytes(output)
        return data if _valid_riff(data) else None
    except (MemoryError, OverflowError, TypeError, ValueError, ctypes.ArgumentError):
        return None
