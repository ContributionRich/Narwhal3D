"""Checked native implementations for selected pure parsing/interchange workloads.

This is a typed FFI adapter over the ``native/pure_core`` C ABI (see
``native/pure_core/README.md``). It owns no document geometry and never lets a
Rust-owned pointer cross the call: it packs caller-owned geometry (triangle
coordinates, or a mesh's vertices / names / face indices) into contiguous
``array`` spans, asks the native two-pass size query for the exact output
length, allocates a caller-owned buffer, and hands it to the native encoder.
Python keeps the path I/O and is the complete fallback.

Discovery is deliberately conservative and matches ``native_selection.py``:
the library is loaded only from the explicit ``NARWHAL_PURE_CORE`` path, or
from the adjacent packaged location once a future release-integration task
populates it. There is no other auto-discovery and no binary is enabled by
assumption. Any discovery, ABI, input, or call failure returns ``None`` so the
caller runs the unchanged Python implementation.

Native and Python results are byte/structure-identical. Production modules activate
only routes that win end-to-end after packing and conversion; slower direct APIs remain
available for parity tests and future ABI optimization, never as user-facing regressions.

The OBJ exports are newer than the STL ones: a library built before them has
the STL symbols but not the OBJ symbols. The binding resolves the OBJ exports
as OPTIONAL (``None`` when absent) so such an old library keeps its native STL
path and only the OBJ route falls back to Python. The OBJ *decode* exports are
newer still: a library with the OBJ encode but not the decode keeps its encode
route and only the OBJ decode falls back to Python. The DXF exports (encode
*and* decode) are newest of all: a library with the DXF encode but not the
decode keeps its encode route and only the DXF decode falls back to Python.
"""
from __future__ import annotations

import array
import ctypes
import functools
import json
import math
import os
import struct
import sys
import threading
from pathlib import Path
from typing import Any, Optional

_ABI_VERSION = 1
_ENV_PATH = "NARWHAL_PURE_CORE"
_DoubleP = ctypes.POINTER(ctypes.c_double)
_ByteP = ctypes.POINTER(ctypes.c_ubyte)
_lock = threading.Lock()
_signature: str | None = None
_native_binding: Any = None
_status = "not checked"
_native_calls = 0
_native_decode_calls = 0
_native_stl_mesh_calls = 0
_native_obj_calls = 0
_native_decode_obj_calls = 0
_native_dxf_calls = 0
_native_decode_dxf_calls = 0
_native_pdf_calls = 0
_native_svg_vector_calls = 0

# Adapter-side resource ceilings. They are intentionally below address-space
# limits: interchange files above 512 MiB and native outputs above 512 MiB use
# the complete Python fallback instead of trusting a two-pass ABI allocation.
# Structured parsers share Rust's practical 1 MiB text-input limit and cap JSON
# at 64 MiB. Count limits keep native packing below ordinary workstation-scale
# memory use while leaving room for multi-million-facet production exports.
_MAX_INTERCHANGE_INPUT_BYTES = 512 * 1024 * 1024
_MAX_NATIVE_OUTPUT_BYTES = 512 * 1024 * 1024
_MAX_STRUCTURED_INPUT_BYTES = 1 * 1024 * 1024
_MAX_STRUCTURED_OUTPUT_BYTES = 64 * 1024 * 1024
_MAX_STL_TRIANGLES = 2_000_000
_MAX_OBJ_MESHES = 1_000_000
_MAX_OBJ_VERTICES = 5_000_000
_MAX_OBJ_FACES = 5_000_000
_MAX_OBJ_FACE_INDICES = 20_000_000
_MAX_DXF_LAYERS = 1_000_000
_MAX_DXF_ENTITIES = 5_000_000
_MAX_DXF_COORDS = 30_000_000
_MAX_NAME_BYTES = 64 * 1024 * 1024
_OBJ_IMPORT_NATIVE_MIN_BYTES = _MAX_INTERCHANGE_INPUT_BYTES + 1
_DXF_IMPORT_NATIVE_MIN_BYTES = _MAX_INTERCHANGE_INPUT_BYTES + 1


def _obj_import_native_active(data, *, force=False):
    """Whether Import may consume validated OBJ buffers without tuple expansion."""
    return (isinstance(data, bytes)
            and (force or len(data) >= _OBJ_IMPORT_NATIVE_MIN_BYTES))


def _dxf_import_native_active(data, *, force=False):
    """Whether Import may consume validated DXF buffers without entity dicts."""
    return (isinstance(data, bytes)
            and (force or len(data) >= _DXF_IMPORT_NATIVE_MIN_BYTES))


def _size_is_bounded(size: int, derived_limit: int,
                     absolute_limit: int = _MAX_NATIVE_OUTPUT_BYTES) -> bool:
    """Whether an untrusted native size is practical and input-derived."""
    return (isinstance(size, int) and 0 <= size <= absolute_limit
            and size <= derived_limit and size <= sys.maxsize)


def _byte_input(data, cap: int = _MAX_INTERCHANGE_INPUT_BYTES):
    """Return a mutable contiguous bytearray and ctypes view, or ``None``.

    ``from_buffer`` exposes one C-contiguous span without the per-byte Python
    argument expansion of ``(c_ubyte * n)(*data)``.
    """
    if not isinstance(data, (bytes, bytearray)):
        return None
    n = len(data)
    if n > cap:
        return None
    owned = bytearray(data)
    view = (ctypes.c_ubyte * n).from_buffer(owned)
    return owned, view, n


def _bounded_utf8(text):
    """Encode a structured-parser input within the shared 1 MiB cap."""
    if not isinstance(text, str) or len(text) > _MAX_STRUCTURED_INPUT_BYTES:
        return None
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_STRUCTURED_INPUT_BYTES:
        return None
    return (encoded, len(encoded)) if encoded else (None, 0)


def _json_without_duplicate_keys(payload: bytes):
    def object_hook(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate JSON key %r" % key)
            out[key] = value
        return out

    return json.loads(payload, object_pairs_hook=object_hook)


def _has_exact_keys(value, required, optional=()) -> bool:
    return (isinstance(value, dict) and set(required) <= set(value)
            and set(value) <= set(required) | set(optional))


def _is_bool(value) -> bool:
    return type(value) is bool


def _valid_prefixes(value) -> bool:
    return (isinstance(value, list)
            and all(isinstance(p, str) and len(p) == 1
                    and p in "!'-_&*~#%" for p in value))


def _validate_macro_json(payload: bytes, lossless: bool) -> bool:
    """Strictly validate native tokenizer JSON before accepting success."""
    try:
        steps = _json_without_duplicate_keys(payload)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(steps, list):
        return False
    extra_flags = {"history_lock_off", "repeat", "suppress_options",
                   "history_on", "history_off"}
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("kind"), str):
            return False
        kind = step["kind"]
        if kind == "cancel":
            if set(step) != {"kind"}:
                return False
        elif kind == "command":
            required = {"kind", "name", "dash", "underscore", "transparent"}
            if lossless:
                required.add("prefixes")
            if not _has_exact_keys(step, required, extra_flags):
                return False
            if (not isinstance(step["name"], str) or not step["name"]
                    or not step["name"][0].isalpha()):
                return False
            if not all(_is_bool(step[k]) for k in
                       {"dash", "underscore", "transparent"}):
                return False
            if not all(_is_bool(step[k]) for k in extra_flags if k in step):
                return False
            if lossless and not _valid_prefixes(step["prefixes"]):
                return False
        elif kind in {"pause", "enter"}:
            expected = {"kind", "special", "prefixes"} if lossless else {"kind"}
            if set(step) != expected:
                return False
            if lossless:
                allowed = ({"pause", "multipause"} if kind == "pause"
                           else {"enter", "enterend"})
                if (not isinstance(step["special"], str)
                        or step["special"].casefold() not in allowed
                        or not _valid_prefixes(step["prefixes"])):
                    return False
        elif kind == "text":
            if set(step) != {"kind", "value"} or not isinstance(step["value"], str):
                return False
        elif kind == "option":
            expected = {"kind", "name", "value", "prefixes"} if lossless else {
                "kind", "name", "value"}
            if (set(step) != expected or not isinstance(step["name"], str)
                    or not step["name"] or not isinstance(step["value"], str)):
                return False
            if lossless and not _valid_prefixes(step["prefixes"]):
                return False
        else:
            return False
    return True


@functools.lru_cache(maxsize=128)
def _cached_macro_json(payload: bytes, lossless: bool) -> bool:
    return _validate_macro_json(payload, lossless)


def _valid_macro_json(payload: bytes, lossless: bool) -> bool:
    if len(payload) <= 64 * 1024:
        return _cached_macro_json(payload, lossless)
    return _validate_macro_json(payload, lossless)


def _validate_macro_plan_json(payload: bytes) -> bool:
    """Strictly validate native planner JSON before accepting success."""
    try:
        actions = _json_without_duplicate_keys(payload)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(actions, list):
        return False
    extra_flags = {"history_lock_off", "repeat", "suppress_options",
                   "history_on", "history_off"}
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get("action"), str):
            return False
        kind = action["action"]
        if kind == "cancel":
            if set(action) != {"action", "prefixes"} or action["prefixes"] != ["!"]:
                return False
        elif kind == "command":
            required = {"action", "name", "prefixes", "dash", "underscore",
                        "transparent"}
            if not _has_exact_keys(action, required, extra_flags):
                return False
            if (not isinstance(action["name"], str) or not action["name"]
                    or not action["name"][0].isalpha()
                    or not _valid_prefixes(action["prefixes"])):
                return False
            if not all(_is_bool(action[k]) for k in
                       {"dash", "underscore", "transparent"}):
                return False
            if not all(_is_bool(action[k]) for k in extra_flags if k in action):
                return False
        elif kind in {"pause", "enter"}:
            if set(action) != {"action", "special", "prefixes"}:
                return False
            allowed = ({"pause", "multipause"} if kind == "pause"
                       else {"enter", "enterend"})
            if (not isinstance(action["special"], str)
                    or action["special"].casefold() not in allowed
                    or not _valid_prefixes(action["prefixes"])):
                return False
        elif kind == "text":
            if (set(action) != {"action", "value"}
                    or not isinstance(action["value"], str)):
                return False
        elif kind == "option":
            if (set(action) != {"action", "name", "value", "prefixes"}
                    or not isinstance(action["name"], str) or not action["name"]
                    or not isinstance(action["value"], str)
                    or not _valid_prefixes(action["prefixes"])):
                return False
        else:
            return False
    return True


@functools.lru_cache(maxsize=128)
def _cached_macro_plan_json(payload: bytes) -> bool:
    return _validate_macro_plan_json(payload)


def _valid_macro_plan_json(payload: bytes) -> bool:
    if len(payload) <= 64 * 1024:
        return _cached_macro_plan_json(payload)
    return _validate_macro_plan_json(payload)


def _validate_svgpath_json(payload: bytes) -> bool:
    """Strictly validate native SVG subpaths before accepting success."""
    try:
        subpaths = _json_without_duplicate_keys(payload)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(subpaths, list):
        return False
    for subpath in subpaths:
        if (not isinstance(subpath, dict)
                or set(subpath) != {"beziers", "closed"}
                or not isinstance(subpath["beziers"], list)
                or not _is_bool(subpath["closed"])):
            return False
        for bezier in subpath["beziers"]:
            if not isinstance(bezier, list) or len(bezier) != 4:
                return False
            for point in bezier:
                if not isinstance(point, list) or len(point) != 2:
                    return False
                for coordinate in point:
                    if (type(coordinate) not in (int, float)
                            or not math.isfinite(coordinate)):
                        return False
    return True


@functools.lru_cache(maxsize=128)
def _cached_svgpath_json(payload: bytes) -> bool:
    return _validate_svgpath_json(payload)


def _valid_svgpath_json(payload: bytes) -> bool:
    if len(payload) <= 64 * 1024:
        return _cached_svgpath_json(payload)
    return _validate_svgpath_json(payload)


def _library_name() -> str:
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


# The adjacent "verified library" slot. It is empty until a later
# release-integration task owns copying a verified build next to the package;
# until then discovery falls through to ``unavailable`` and the Python writer
# is used. See ``native/selection_core``'s same slot for the pattern.
_PACKAGED_LIBRARY = Path(__file__).resolve().parent / "native" / _library_name()


def _binding():
    """Return ``(stl_size, stl_encode, stl_decode_size, stl_decode,
    obj_size, obj_encode, obj_decode_size, obj_decode,
    dxf_size, dxf_encode, dxf_decode_size, dxf_decode, path)`` or ``None``.

    The STL encode pair is mandatory. The STL decode pair, four OBJ slots and
    four DXF slots are ``None`` on a library built before they existed, so an
    ABI-1 encode-only library retains its working encode route. ``path`` is
    always the last element.
    """
    global _signature, _native_binding, _status
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _signature == signature:
        return _native_binding
    with _lock:
        if _signature == signature:
            return _native_binding
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        _signature = signature
        _native_binding = None
        _status = "native pure-core library unavailable"
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    _status = f"native pure-core ABI mismatch: {path}"
                    continue
                size = library.narwhal_pure_core_stl_size
                size.argtypes = [
                    _DoubleP, ctypes.c_size_t, _ByteP, ctypes.c_size_t,
                    ctypes.c_ubyte,
                ]
                size.restype = ctypes.c_int64
                enc = library.narwhal_pure_core_stl_encode
                enc.argtypes = [
                    _DoubleP, ctypes.c_size_t, _ByteP, ctypes.c_size_t,
                    ctypes.c_ubyte, _ByteP, ctypes.c_size_t,
                ]
                enc.restype = ctypes.c_int32
            except (AttributeError, OSError) as exc:
                _status = f"native pure-core load failed: {exc}"
                continue
            # Decode was added after the original ABI-1 STL encoder. Keep it
            # optional so an encode-only ABI-1 library remains useful.
            dsize = dec = None
            try:
                dsize = library.narwhal_pure_core_stl_decode_size
                dsize.argtypes = [_ByteP, ctypes.c_size_t]
                dsize.restype = ctypes.c_int64
                dec = library.narwhal_pure_core_stl_decode
                dec.argtypes = [_ByteP, ctypes.c_size_t, _DoubleP, ctypes.c_size_t]
                dec.restype = ctypes.c_int32
            except (AttributeError, OSError):
                pass  # STL decode exports absent: encode remains available
            # The OBJ exports are newer than the STL ones: an old library has
            # the STL symbols but not these. Resolve them as OPTIONAL (None when
            # absent) so such a library keeps its native STL path and only the
            # OBJ route falls back to Python.
            _i64p = ctypes.POINTER(ctypes.c_int64)
            _i32p = ctypes.POINTER(ctypes.c_int32)
            obj_size = obj_enc = obj_dsize = obj_dec = None
            try:
                obj_size = library.narwhal_pure_core_obj_size
                obj_size.argtypes = [
                    _DoubleP, ctypes.c_int64, _ByteP, ctypes.c_int64,
                    _i64p, _i64p, _i64p, _i64p, ctypes.c_int64, _i32p,
                    ctypes.c_int64, ctypes.c_int64,
                ]
                obj_size.restype = ctypes.c_int64
                obj_enc = library.narwhal_pure_core_obj_encode
                obj_enc.argtypes = [
                    _DoubleP, ctypes.c_int64, _ByteP, ctypes.c_int64,
                    _i64p, _i64p, _i64p, _i64p, ctypes.c_int64, _i32p,
                    ctypes.c_int64, ctypes.c_int64, _ByteP, ctypes.c_size_t,
                ]
                obj_enc.restype = ctypes.c_int32
            except (AttributeError, OSError):
                pass  # OBJ encode exports absent: STL-only, encode falls back
            # The OBJ decode exports are newer than the encode ones: a library
            # may have the encode but not the decode. Resolve them as OPTIONAL
            # (None when absent) so such a library keeps its encode route and
            # only the decode falls back to Python.
            try:
                obj_dsize = library.narwhal_pure_core_obj_decode_size
                obj_dsize.argtypes = [_ByteP, ctypes.c_size_t]
                obj_dsize.restype = ctypes.c_int64
                obj_dec = library.narwhal_pure_core_obj_decode
                obj_dec.argtypes = [_ByteP, ctypes.c_size_t, _ByteP, ctypes.c_size_t]
                obj_dec.restype = ctypes.c_int32
            except (AttributeError, OSError):
                pass  # OBJ decode exports absent: decode falls back
            # The DXF encode exports are the newest: a library may have the
            # OBJ encode/decode but not the DXF exports. Resolve them as
            # OPTIONAL (None when absent) so such a library keeps every
            # existing route and only the DXF route falls back to Python.
            dxf_size = dxf_enc = None
            try:
                dxf_size = library.narwhal_pure_core_dxf_size
                dxf_size.argtypes = [
                    _ByteP, ctypes.c_int64, _i64p, _i64p, ctypes.c_int64,
                    _i64p, _i64p, _i64p, _DoubleP, ctypes.c_int64, _i64p,
                    _i64p, ctypes.c_int64,
                ]
                dxf_size.restype = ctypes.c_int64
                dxf_enc = library.narwhal_pure_core_dxf_encode
                dxf_enc.argtypes = [
                    _ByteP, ctypes.c_int64, _i64p, _i64p, ctypes.c_int64,
                    _i64p, _i64p, _i64p, _DoubleP, ctypes.c_int64, _i64p,
                    _i64p, ctypes.c_int64, _ByteP, ctypes.c_size_t,
                ]
                dxf_enc.restype = ctypes.c_int32
            except (AttributeError, OSError):
                pass  # DXF encode exports absent: DXF encode falls back
            # The DXF decode exports are newer than the DXF encode ones: a
            # library may have the encode but not the decode. Resolve them as
            # OPTIONAL (None when absent) so such a library keeps its encode
            # route and only the DXF decode falls back to Python.
            dxf_dsize = dxf_dec = None
            try:
                dxf_dsize = library.narwhal_pure_core_dxf_decode_size
                dxf_dsize.argtypes = [_ByteP, ctypes.c_size_t]
                dxf_dsize.restype = ctypes.c_int64
                dxf_dec = library.narwhal_pure_core_dxf_decode
                dxf_dec.argtypes = [_ByteP, ctypes.c_size_t, _ByteP, ctypes.c_size_t]
                dxf_dec.restype = ctypes.c_int32
            except (AttributeError, OSError):
                pass  # DXF decode exports absent: DXF decode falls back
            _native_binding = (
                size, enc, dsize, dec,
                obj_size, obj_enc, obj_dsize, obj_dec,
                dxf_size, dxf_enc, dxf_dsize, dxf_dec, path)
            _status = f"ready: {path}"
            return _native_binding
        return None


def available() -> bool:
    """Whether an ABI-compatible native STL encoder/decoder is available."""
    return _binding() is not None


def backend_status() -> str:
    """A diagnostic status; absence is not a feature failure (Python fallback)."""
    _binding()
    return _status


def _name_ptr(name):
    name_bytes = str(name).encode("latin-1")
    if not name_bytes:
        return (ctypes.c_ubyte * 0)(), 0
    arr = (ctypes.c_ubyte * len(name_bytes))(*name_bytes)
    return arr, len(name_bytes)


# ---------------------------------------------------------------------------
# Numparse (Q106-08) — number / fraction / length-with-unit parser.
#
# A SEPARATE, self-contained resolver (not a grown element of the 13-tuple
# above): the parser functions are per-call FFI with a value in an out-param,
# unlike the bulk two-pass file I/O. Keeping them in their own slot means the
# existing 13-tuple's five unpack sites and the test_native_stl/obj/dxf suites
# (which fabricate that tuple) are untouched by this task, and Q106-09..14 can
# each add their own resolver the same way without colliding with the tuple.
#
# Error contract: the native function returns a stable integer code
# (``NP_OK == 0`` on success, a negative code on rejection) and the parsed
# value in an out-param. The Python layer uses the native route ONLY on a
# success code -- on any error code it runs the unchanged Python parser, so
# the exact public error string (which embeds the input) is always produced
# by the original code. A fallback is therefore a correctness fallback, not a
# slower one: the measured success path never touches the regex.
# ---------------------------------------------------------------------------

_np_signature: str | None = None
_np_binding: Any = None
_native_numparse_calls = 0


def _numparse_binding():
    """Return ``(number, length)`` ctypes handles, or ``None``.

    Both are resolved as OPTIONAL (``None`` when absent) so a library built
    before the numparse exports keeps every existing route and only the
    numparse route falls back to Python. ``number`` and ``length`` are always
    returned together or ``None`` (a half-resolved binding is a load error).
    """
    global _np_signature, _np_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _np_signature == signature:
        return _np_binding
    with _lock:
        if _np_signature == signature:
            return _np_binding
        _np_signature = signature
        _np_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                # The number/length inputs are caller-owned bytes; passing the
                # ``bytes`` object straight through a ``c_char_p`` argtype
                # avoids building a ``(c_ubyte * n)`` array per call (which is
                # the dominant cost on the short-input path and would make the
                # native route slower than the Python regex -- the "never
                # slower" rule). ``c_char_p`` takes a ``bytes`` (or ``None``)
                # and passes its length via the adjacent ``c_size_t``.
                number = library.narwhal_pure_core_number
                number.argtypes = [ctypes.c_char_p, ctypes.c_size_t, _DoubleP]
                number.restype = ctypes.c_int32
                length = library.narwhal_pure_core_length
                length.argtypes = [
                    ctypes.c_char_p, ctypes.c_size_t,
                    ctypes.c_char_p, ctypes.c_size_t, _DoubleP,
                ]
                length.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _np_binding = (number, length)
            return _np_binding
        return None


def _text_bytes(text):
    """Encode *text* to UTF-8 ``bytes`` for a ``c_char_p`` input slot.

    ``c_char_p`` takes a ``bytes`` (or ``None`` for an empty input) and passes
    its length via the adjacent ``c_size_t``; no per-call ctypes array is built,
    which keeps the short-input path as fast as the Python regex.
    """
    b = str(text).encode("utf-8")
    return (b, len(b)) if b else (None, 0)


def _native_number(text):
    """Native ``parse_number``; ``None`` when the native route is unavailable,
    rejected (the caller runs the Python parser), or failed."""
    global _native_numparse_calls
    binding = _numparse_binding()
    if binding is None:
        return None
    number, _ = binding
    arr, n = _text_bytes(text)
    out = ctypes.c_double(0.0)
    try:
        code = number(arr, n, ctypes.byref(out))
    except (ctypes.ArgumentError, OSError):
        return None
    if code != 0:
        return None
    _native_numparse_calls += 1
    return out.value


def _native_length(text, model_unit):
    """Native ``parse_length``; ``None`` when the native route is unavailable,
    rejected (the caller runs the Python parser), or failed."""
    global _native_numparse_calls
    binding = _numparse_binding()
    if binding is None:
        return None
    _, length = binding
    tarr, tn = _text_bytes(text)
    marr, mn = _text_bytes(model_unit)
    out = ctypes.c_double(0.0)
    try:
        code = length(tarr, tn, marr, mn, ctypes.byref(out))
    except (ctypes.ArgumentError, OSError):
        return None
    if code != 0:
        return None
    _native_numparse_calls += 1
    return out.value


# ---------------------------------------------------------------------------
# Native calculator (Q106-09). One export: ``narwhal_pure_core_evaluate``
# (``CALC_OK == 0`` on success with the value in an out-param, a negative code
# on rejection). The Python layer uses the native route ONLY on a success code
# -- on any error code it runs the unchanged Python evaluator (``calc.py``),
# so the exact public error string (which embeds the input) is always produced
# by the original code. A fallback is therefore a correctness fallback, not a
# slower one: the measured success path never touches the Python evaluator.
# ---------------------------------------------------------------------------

_c_signature: str | None = None
_c_binding: Any = None
_native_calc_calls = 0


def _calc_binding():
    """Return the ctypes handle for ``narwhal_pure_core_evaluate``, or ``None``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before the
    calculator export keeps every existing route and only the calculator route
    falls back to Python. (The state globals are ``_c_*`` -- distinct from this
    function's name, as ``_np_*`` are distinct from ``_numparse_binding``.)
    """
    global _c_signature, _c_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _c_signature == signature:
        return _c_binding
    with _lock:
        if _c_signature == signature:
            return _c_binding
        _c_signature = signature
        _c_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                evaluate = library.narwhal_pure_core_evaluate
                evaluate.argtypes = [
                    ctypes.c_char_p, ctypes.c_size_t,
                    ctypes.c_char_p, ctypes.c_size_t, _DoubleP,
                ]
                evaluate.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _c_binding = evaluate
            return _c_binding
        return None


def _native_calc(text, model_unit):
    """Native ``calc.evaluate``; ``None`` when the native route is unavailable,
    rejected (the caller runs the Python evaluator), or failed."""
    global _native_calc_calls
    evaluate = _calc_binding()
    if evaluate is None:
        return None
    tarr, tn = _text_bytes(text)
    marr, mn = _text_bytes(model_unit)
    out = ctypes.c_double(0.0)
    try:
        code = evaluate(tarr, tn, marr, mn, ctypes.byref(out))
    except (ctypes.ArgumentError, OSError):
        return None
    if code != 0:
        return None
    _native_calc_calls += 1
    return out.value


# ---------------------------------------------------------------------------
# Native surveyor bearing parser (Q106-10). Two exports:
#   ``narwhal_pure_core_bearing``  -- bearing string -> CCW-from-+X degrees
#   ``narwhal_pure_core_surveyor`` -- distance<bearing -> (distance, degrees)
# (``BEARING_OK == 0`` on success with the value(s) in an out-param, a negative
# code on rejection). The Python layer uses the native route ONLY on a success
# code -- on any error code it runs the unchanged Python parser (``bearing.py``),
# so the exact public error string (which embeds the input) is always produced
# by the original code. A fallback is therefore a correctness fallback, not a
# slower one: the measured success path never touches the Python parser.
# ---------------------------------------------------------------------------

_b_signature: str | None = None
_b_binding: Any = None
_native_bearing_calls = 0


def _bearing_binding():
    """Return ``(bearing, surveyor)`` ctypes handles, or ``None``.

    Both are resolved as OPTIONAL (``None`` when absent) so a library built
    before the bearing exports keeps every existing route and only the bearing
    route falls back to Python. The two are always returned together or ``None``
    (a half-resolved binding is a load error). (The state globals are ``_b_*``
    -- distinct from this function's name, as ``_c_*`` are distinct from
    ``_calc_binding`` and ``_np_*`` from ``_numparse_binding``.)
    """
    global _b_signature, _b_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _b_signature == signature:
        return _b_binding
    with _lock:
        if _b_signature == signature:
            return _b_binding
        _b_signature = signature
        _b_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                bearing = library.narwhal_pure_core_bearing
                bearing.argtypes = [ctypes.c_char_p, ctypes.c_size_t, _DoubleP]
                bearing.restype = ctypes.c_int32
                surveyor = library.narwhal_pure_core_surveyor
                surveyor.argtypes = [
                    ctypes.c_char_p, ctypes.c_size_t,
                    _DoubleP, _DoubleP,
                ]
                surveyor.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _b_binding = (bearing, surveyor)
            return _b_binding
        return None


def _native_bearing_angle(text):
    """Native ``bearing_angle``; ``None`` when the native route is unavailable,
    rejected (the caller runs the Python parser), or failed."""
    global _native_bearing_calls
    binding = _bearing_binding()
    if binding is None:
        return None
    bearing, _ = binding
    arr, n = _text_bytes(text)
    out = ctypes.c_double(0.0)
    try:
        code = bearing(arr, n, ctypes.byref(out))
    except (ctypes.ArgumentError, OSError):
        return None
    if code != 0:
        return None
    _native_bearing_calls += 1
    return out.value


def _native_parse_surveyor(text):
    """Native ``parse_surveyor`` -> ``(distance, angle_deg)``; ``None`` when the
    native route is unavailable, rejected (the caller runs the Python parser),
    or failed."""
    global _native_bearing_calls
    binding = _bearing_binding()
    if binding is None:
        return None
    _, surveyor = binding
    arr, n = _text_bytes(text)
    dist = ctypes.c_double(0.0)
    ang = ctypes.c_double(0.0)
    try:
        code = surveyor(arr, n, ctypes.byref(dist), ctypes.byref(ang))
    except (ctypes.ArgumentError, OSError):
        return None
    if code != 0:
        return None
    _native_bearing_calls += 1
    return (dist.value, ang.value)


# ---------------------------------------------------------------------------
# Native coordinate text parser (Q106-11). One export:
#   ``narwhal_pure_core_coords`` -- typed text -> (x, y, z, is_world,
#                                    is_relative), in the system the flags describe
# (``COORDS_OK == 0`` on success with the values in the out-params, a negative
# code on rejection). The Python layer uses the native route ONLY on a success
# code -- on any error code it runs the unchanged Python parser (``coords.py``),
# so the exact public error string (which embeds the input) is always produced
# by the original code. A fallback is therefore a correctness fallback, not a
# slower one: the measured success path never touches the Python parser.
# ---------------------------------------------------------------------------

_co_signature: str | None = None
_co_binding: Any = None
_native_coords_calls = 0

# macro tokenizer (Q106-12): the state globals are ``_m_*`` -- distinct from the
# ``_macro_binding`` resolver (the same ``_co_*`` / ``_b_*`` collision trap).
_m_signature: str | None = None
_m_binding: Any = None  # (size_fn, encode_fn) or None
_native_macro_calls = 0

# macro planner (Q106-13): the state globals are ``_mp_*`` -- distinct from the
# ``_macro_plan_binding`` resolver (the same collision trap, and distinct from
# Q106-12's ``_m_*``).
_mp_signature: str | None = None
_mp_binding: Any = None  # (size_fn, encode_fn) or None
_native_macro_plan_calls = 0

# SVG path parser (Q106-14): the state globals are ``_sv_*`` -- distinct from the
# ``_svgpath_binding`` resolver (the same collision trap, and distinct from
# Q106-12's ``_m_*`` / Q106-13's ``_mp_*``).
_sv_signature: str | None = None
_sv_binding: Any = None  # (size_fn, encode_fn) or None
_native_svgpath_calls = 0

# Join-chain solver (Q117-06): the state globals are ``_jc_*`` -- distinct from the
# ``_joinchain_binding`` resolver (the same collision trap) and from every other
# route's slots.
_jc_signature: str | None = None
_jc_binding: Any = None  # narwhal_pure_core_joinchain or None
_native_joinchain_calls = 0

# Q117-06 crossover: the segment count at which the native route (packing + FFI
# + conversion, measured end-to-end) is >=10% faster than the Python solver.
# Set from the 20k-segment measurement (below): the Python route is Theta(N^2)
# and the native route is ~O(N log N), so the win is LARGEST exactly where the
# Python route is worst (the disjoint / ambiguous / reversed cases that take
# 6.6-122 s in Python). Set to a value where the native route clearly wins and
# small inputs (where FFI overhead dominates) stay on Python. See the
# measurement note on ``_joinchain_native_active``.
_JOINCHAIN_NATIVE_MIN_SEGMENTS = 512


def _coords_binding():
    """Return the ``narwhal_pure_core_coords`` ctypes handle, or ``None``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before the
    coordinate export keeps every existing route and only the coordinate route
    falls back to Python. (The state globals are ``_co_*`` -- distinct from this
    function's name, as ``_b_*`` are distinct from ``_bearing_binding``.)
    """
    global _co_signature, _co_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _co_signature == signature:
        return _co_binding
    with _lock:
        if _co_signature == signature:
            return _co_binding
        _co_signature = signature
        _co_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                coords = library.narwhal_pure_core_coords
                coords.argtypes = [
                    ctypes.c_char_p, ctypes.c_size_t,
                    ctypes.c_char_p, ctypes.c_size_t,
                    _DoubleP, _DoubleP, _DoubleP,
                    ctypes.POINTER(ctypes.c_uint8),
                    ctypes.POINTER(ctypes.c_uint8),
                ]
                coords.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _co_binding = coords
            return _co_binding
        return None


def _native_parse(text, model_unit):
    """Native ``coords.parse`` -> ``(x, y, z, is_world, is_relative)``;
    ``None`` when the native route is unavailable, rejected (the caller runs the
    Python parser), or failed."""
    global _native_coords_calls
    coords = _coords_binding()
    if coords is None:
        return None
    tarr, tn = _text_bytes(text)
    marr, mn = _text_bytes(model_unit)
    x = ctypes.c_double(0.0)
    y = ctypes.c_double(0.0)
    z = ctypes.c_double(0.0)
    world = ctypes.c_uint8(0)
    rel = ctypes.c_uint8(0)
    try:
        code = coords(
            tarr, tn, marr, mn,
            ctypes.byref(x), ctypes.byref(y), ctypes.byref(z),
            ctypes.byref(world), ctypes.byref(rel),
        )
    except (ctypes.ArgumentError, OSError):
        return None
    if code != 0:
        return None
    _native_coords_calls += 1
    return (x.value, y.value, z.value, bool(world.value), bool(rel.value))


def _macro_binding():
    """Return ``(narwhal_pure_core_macro_size, narwhal_pure_core_macro_encode)``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before the
    macro export keeps every existing route and only the macro route falls back
    to Python. (The state globals are ``_m_*`` -- distinct from this function's
    name, as ``_co_*`` are distinct from ``_coords_binding``.)
    """
    global _m_signature, _m_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _m_signature == signature:
        return _m_binding
    with _lock:
        if _m_signature == signature:
            return _m_binding
        _m_signature = signature
        _m_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                size = library.narwhal_pure_core_macro_size
                size.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_ubyte]
                size.restype = ctypes.c_int64
                enc = library.narwhal_pure_core_macro_encode
                enc.argtypes = [
                    ctypes.c_char_p, ctypes.c_size_t, ctypes.c_ubyte,
                    _ByteP, ctypes.c_size_t,
                ]
                enc.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _m_binding = (size, enc)
            return _m_binding
        return None


def _native_macro(text, lossless):
    """Native ``macro.tokenize`` -> the JSON step list, or ``None``.

    ``None`` means "use the unchanged Python tokenizer" -- the library is
    unavailable, it predates the macro export, the input was rejected (over the
    byte cap or not valid UTF-8), or the output buffer was mis-sized. The Python
    hook converts the JSON back to the exact step-list contract (including the
    lossless ``prefixes`` tuples), so a successful native route is
    byte-for-byte the Python result.
    """
    global _native_macro_calls
    binding = _macro_binding()
    if binding is None:
        return None
    size_fn, enc_fn = binding
    text_input = _bounded_utf8(text)
    if text_input is None:
        return None
    tarr, tn = text_input
    flag = 1 if lossless else 0
    try:
        nbytes = size_fn(tarr, tn, flag)
        derived = 1024 + 64 * tn
        if not _size_is_bounded(nbytes, derived, _MAX_STRUCTURED_OUTPUT_BYTES):
            return None
        buf = (ctypes.c_ubyte * nbytes)()
        rc = enc_fn(tarr, tn, flag, buf, nbytes)
        if rc != 0:
            return None
        out = bytes(buf)
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    if not _valid_macro_json(out, bool(lossless)):
        return None
    _native_macro_calls += 1
    return out


def _macro_plan_binding():
    """Return ``(narwhal_pure_core_macro_plan_size, ..._encode)``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before the
    planner export keeps every existing route and only the planner route falls
    back to Python. (The state globals are ``_mp_*`` -- distinct from this
    function's name, as ``_m_*`` are distinct from ``_macro_binding``.)
    """
    global _mp_signature, _mp_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _mp_signature == signature:
        return _mp_binding
    with _lock:
        if _mp_signature == signature:
            return _mp_binding
        _mp_signature = signature
        _mp_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                size = library.narwhal_pure_core_macro_plan_size
                size.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
                size.restype = ctypes.c_int64
                enc = library.narwhal_pure_core_macro_plan_encode
                enc.argtypes = [
                    ctypes.c_char_p, ctypes.c_size_t,
                    _ByteP, ctypes.c_size_t,
                ]
                enc.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _mp_binding = (size, enc)
            return _mp_binding
        return None


def _native_macro_plan(text):
    """Native ``macro_plan.plan_macro`` -> the JSON action list, or ``None``.

    ``None`` means "use the unchanged Python planner" -- the library is
    unavailable, it predates the planner export, the input was rejected (over
    the byte cap or not valid UTF-8), the macro contains a transparent command
    outside the measured nestable allow-list (the stable rejection code), or
    the output buffer was mis-sized. The Python hook converts the JSON back to
    the exact action-list contract (including the ``prefixes`` tuples), so a
    successful native route is byte-for-byte the Python result; on a rejection
    the unchanged Python path raises the exact ``NonNestableCommandError``.
    """
    global _native_macro_plan_calls
    binding = _macro_plan_binding()
    if binding is None:
        return None
    size_fn, enc_fn = binding
    text_input = _bounded_utf8(text)
    if text_input is None:
        return None
    tarr, tn = text_input
    try:
        nbytes = size_fn(tarr, tn)
        derived = 1024 + 96 * tn
        if not _size_is_bounded(nbytes, derived, _MAX_STRUCTURED_OUTPUT_BYTES):
            return None
        buf = (ctypes.c_ubyte * nbytes)()
        rc = enc_fn(tarr, tn, buf, nbytes)
        if rc != 0:
            return None
        out = bytes(buf)
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    if not _valid_macro_plan_json(out):
        return None
    _native_macro_plan_calls += 1
    return out


def _svgpath_binding():
    """Return ``(narwhal_pure_core_svgpath_size, ..._encode)``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before the
    SVG export keeps every existing route and only the SVG route falls back to
    Python. (The state globals are ``_sv_*`` -- distinct from this function's
    name, as ``_mp_*`` are distinct from ``_macro_plan_binding``.)
    """
    global _sv_signature, _sv_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _sv_signature == signature:
        return _sv_binding
    with _lock:
        if _sv_signature == signature:
            return _sv_binding
        _sv_signature = signature
        _sv_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                size = library.narwhal_pure_core_svgpath_size
                size.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
                size.restype = ctypes.c_int64
                enc = library.narwhal_pure_core_svgpath_encode
                enc.argtypes = [
                    ctypes.c_char_p, ctypes.c_size_t,
                    _ByteP, ctypes.c_size_t,
                ]
                enc.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _sv_binding = (size, enc)
            return _sv_binding
        return None


def _native_svgpath(text):
    """Native ``svgpath.parse_path`` -> the JSON subpath list, or ``None``.

    ``None`` means "use the unchanged Python parser" -- the library is
    unavailable, it predates the SVG export, the input was rejected (over the
    byte cap or not valid UTF-8), the path is malformed (a number outside a
    command, a command before any moveto, or too few parameters -- the stable
    ``ERR_PARSE`` rejection code), or the output buffer was mis-sized. The
    Python hook converts the JSON back to the exact subpath contract (each
    Bézier a 4-tuple of 2-tuples, ``closed`` a bool), so a successful native
    route is byte-for-byte the Python result.
    """
    global _native_svgpath_calls
    binding = _svgpath_binding()
    if binding is None:
        return None
    size_fn, enc_fn = binding
    text_input = _bounded_utf8(text)
    if text_input is None:
        return None
    tarr, tn = text_input
    try:
        nbytes = size_fn(tarr, tn)
        derived = 1024 + 256 * tn
        if not _size_is_bounded(nbytes, derived, _MAX_STRUCTURED_OUTPUT_BYTES):
            return None
        buf = (ctypes.c_ubyte * nbytes)()
        rc = enc_fn(tarr, tn, buf, nbytes)
        if rc != 0:
            return None
        out = bytes(buf)
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    if not _valid_svgpath_json(out):
        return None
    _native_svgpath_calls += 1
    return out


# Packed SVG path output (Q117-03): the state globals are ``_svgpp_*`` --
# distinct from the ``_svgpath_packed_binding`` resolver (the same collision
# trap as Q117-01's ``_objp_*`` / Q117-02's ``_dxp_*``) and from the two-pass
# JSON slots ``_sv_*``.
_svgpp_signature: str | None = None
_svgpp_binding: Any = None  # narwhal_pure_core_svgpath_packed or None

# Q117-03 crossover: set ABOVE the 1 MiB native input cap, so
# ``_svg_packed_native_active`` is False for every valid input and production
# stays on the complete, unchanged Python parser -- the same "ready to activate
# but not yet" posture as the DXF one-pass ABI. The packed ABI is added, proven
# byte-for-byte against the Python contract, and ready, but the production
# never-slower policy cannot be met with a size-only crossover because the win is
# SHAPE-DEPENDENT, not size-dependent.
#
# Measured end-to-end (packing + FFI + strict validation + tuple conversion,
# best-of-N) against the unchanged Python parser. The old JSON route was
# 11-19% SLOWER at every size, so the packed route is a large improvement over
# it, but it does not produce a clean >=10% size crossover:
#
#   shape (few subpaths, many segments -- the named workload)
#   segments    input B   python   packed    native win
#   1            8        0.006    0.009     -48%    (slower: FFI overhead)
#   4            20        0.011   0.011     +2%     (sub-ms, <10%, noise)
#   16           68        0.030   0.018     +39%
#   128         516        0.22    0.11      +50%
#   256        1028        0.23    0.22      +3% to +47%   (run-to-run)
#   1,024      4100        1.1     1.0       +1% to +43%
#   16,384     65540      17.9    17.0      +5% to +9%
#   65,536    262148      79-82   71-74      +9% to +13%
#   200,000   800004     256-258  245-250    +2% to +5%
#
#   shape (MANY small subpaths -- e.g. 10,000 closed loops, a real CAD shape
#   such as a part with many holes): 110,000 bytes, 20,000 beziers
#   10k Z     110000      27-27   29-31      -4% to -15%  SLOWER (3 runs)
#   25k C     475000      46.3    46.5       -0.4%        (flat/slightly slower)
#
# ROOT CAUSE. The output is a PYTHON TUPLE contract (a list of subpath dicts,
# each a list of 4-tuples of 2-tuples), so BOTH routes pay an O(N) Python
# object-construction cost. Rust only saves Python's TOKENIZATION (O(bytes)).
# For few-subpath / many-segment paths tokenization dominates, so the saving
# wins (+5% to +49%). For many-small-subpath paths the tokenization is a small
# fraction of the work, and the packed route's conversion (one tiny list
# comprehension per subpath) costs MORE than the Python parser's inline
# ``list.append`` per subpath, so native is 4-15% SLOWER. A size-only
# crossover cannot separate the two (the slow shape, 110 KB, is larger than
# the fast ones), so no single threshold both wins the common case and avoids
# the regression. Activating native would ship a measured 4-15% regression on
# a real production shape, violating the never-slower policy.
#
# A future route may take the few-subpath win without the many-subpath
# regression by either (a) making the gate
# SHAPE-AWARE -- activate native only when the subpath count is small relative
# to the Bézier count (which this ABI already publishes as ``n_subs``/``n_pts``);
# or (b) reduce the conversion cost in the ABI (e.g. return a flat structure the
# consumer can build the tuple contract from in bulk, or a per-subpath Bézier
# count so the converter avoids the per-subpath list-comprehension overhead).
# Either way, flipping this constant (or the gate) is the only change needed
# once that decision is made; the packed ABI, its strict validator, and the
# ``parse_path`` hook are all in place and proven.
_SVG_NATIVE_MIN_CHARS = 2_000_000  # > 1 MiB input cap -> generic API stays Python
# Import consumes packed cubics directly and avoids the generic nested-object
# reconstruction. Activate only for long individual path-data strings so many
# tiny SVG elements never pay one FFI call each.
_SVG_IMPORT_NATIVE_MIN_CHARS = 2_000_000  # above 1 MiB cap: measured <10%


def _svgpath_packed_binding():
    """Return the ``narwhal_pure_core_svgpath_packed`` ctypes handle (the
    packed one-pass ABI, Q117-03), or ``None``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before
    the packed export keeps every existing route and only the packed path
    falls back to Python. The state globals are ``_svgpp_*`` -- distinct
    from this resolver's name.
    """
    global _svgpp_signature, _svgpp_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _svgpp_signature == signature:
        return _svgpp_binding
    with _lock:
        if _svgpp_signature == signature:
            return _svgpp_binding
        _svgpp_signature = signature
        _svgpp_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                packed = library.narwhal_pure_core_svgpath_packed
                _i64p = ctypes.POINTER(ctypes.c_int64)
                packed.argtypes = [
                    ctypes.c_char_p, ctypes.c_size_t,
                    _DoubleP, ctypes.c_size_t,
                    _i64p, ctypes.c_size_t,
                    _i64p, ctypes.c_size_t,
                    _i64p, _i64p,
                ]
                packed.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _svgpp_binding = packed
            return _svgpp_binding
        return None


def _valid_svgpath_packed(n_pts, n_subs, pts, offs, flags) -> bool:
    """Strictly validate a packed SVG output before counting it as success.

    The contract the Rust publisher guarantees and the converter relies on:
    ``n_pts`` Béziers (8 finite floats each) fully written, ``n_subs``
    subpaths with non-decreasing Bézier offsets each in ``[0, n_pts]``,
    flags restricted to 0/1, and the published counts consistent with the
    written arrays (complete consumption -- no missing head, no trailing
    garbage the counts do not cover).
    """
    if (not isinstance(n_pts, int) or not isinstance(n_subs, int)
            or n_pts < 0 or n_subs < 0
            or n_subs > len(offs) or n_subs > len(flags)):
        return False
    if len(pts) < 8 * n_pts:
        return False
    if n_subs == 0:
        return n_pts == 0
    if offs[0] != 0 or any(f not in (0, 1) for f in flags[:n_subs]):
        return False
    prev = 0
    for k in range(n_subs):
        o = offs[k]
        if not isinstance(o, int) or o < 0 or o < prev or o > n_pts:
            return False
        prev = o
    for i in range(8 * n_pts):
        if not math.isfinite(pts[i]):
            return False
    return True


def _native_svgpath_packed(text):
    """Native packed ``svgpath.parse_path`` (Q117-03), or ``None``.

    ``None`` means "use the unchanged Python parser" -- the library is
    unavailable, it predates the packed export, the input was rejected
    (over the byte cap, not valid UTF-8, or malformed -- the stable
    ``ERR_PARSE`` rejection code), the packed output failed the strict
    validator, or the capacity was insufficient. On success returns
    ``(pts, offs, flags, n_pts, n_subs)`` -- the packed cubic points,
    per-subpath Bézier offsets and closed flags, and the exact published
    counts -- which ``svgpath._subpaths_from_packed`` converts into the
    exact Python contract (each Bézier a 4-tuple of 2-tuples, ``closed``
    a bool), so a successful native route is byte-for-byte the Python
    result.
    """
    global _native_svgpath_calls
    packed_fn = _svgpath_packed_binding()
    if packed_fn is None:
        return None  # library predates the packed export
    text_input = _bounded_utf8(text)
    if text_input is None:
        return None
    tarr, tn = text_input
    # Capacity bound (proved): every Bézier is produced by a command that
    # consumes at least one numeric byte, and every such command is at least
    # one letter byte, so every Bézier costs >= 2 input bytes (the floor is
    # ``H5``/``V1`` -- one letter + one digit, each a one-parameter line).
    # Hence ``beziers <= tn / 2`` and ``subpaths <= beziers`` (each subpath
    # is opened by an ``M`` that likewise costs >= 2 bytes). The ``+ 1``
    # guards integer division. This is a safe, exact-enough capacity derived
    # from the input length -- no JSON, no size query, no Rust-owned pointer.
    cap_bez = max(tn // 2 + 1, 1)
    cap_pts = 8 * cap_bez
    cap_subs = cap_bez
    if cap_pts > _MAX_NATIVE_OUTPUT_BYTES // 8:
        return None
    pts = array.array("d", [0.0]) * cap_pts
    offs = array.array("q", [0]) * cap_subs
    flags = array.array("q", [0]) * cap_subs
    out_pts = ctypes.c_int64(0)
    out_subs = ctypes.c_int64(0)
    # Typed array views over the packed arrays (the ``c_X_Array_N`` form, as
    # in ``_pack_obj``), NOT ``POINTER(c_X).from_buffer`` -- the latter wraps
    # the buffer in a pointer object whose underlying value is null, so the
    # Rust side sees a misaligned/invalid pointer and rejects with ERR_SIZE.
    pts_view = (ctypes.c_double * cap_pts).from_buffer(pts)
    i64_array = ctypes.c_int64 * cap_subs
    offs_view = i64_array.from_buffer(offs)
    flags_view = i64_array.from_buffer(flags)
    try:
        rc = packed_fn(
            tarr, tn,
            pts_view, cap_pts,
            offs_view, cap_subs,
            flags_view, cap_subs,
            ctypes.byref(out_pts), ctypes.byref(out_subs),
        )
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    if rc != 0:
        return None
    n_pts = out_pts.value
    n_subs = out_subs.value
    if not _valid_svgpath_packed(n_pts, n_subs, pts, offs, flags):
        return None
    # A fully successful native call: record the route activation so a
    # production caller that silently forces the Python path is detectable.
    _native_svgpath_calls += 1
    return pts, offs, flags, n_pts, n_subs


def _svg_import_native_active(text):
    return (isinstance(text, str)
            and len(text.encode("utf-8")) >= _SVG_IMPORT_NATIVE_MIN_CHARS
            and _svgpath_packed_binding() is not None)


def _svg_packed_native_active(text):
    """Whether the packed native route should handle *text* (Q117-03).

    True only when the packed export is available AND the input is at or
    above the measured crossover (where the complete packed call -- packing
    + FFI + conversion -- is >=10% faster than the Python parser). Below
    the crossover the unchanged Python parser stays active, so small
    workloads never regress.
    """
    if _svgpath_packed_binding() is None:
        return False
    if _SVG_NATIVE_MIN_CHARS <= 0:
        return True
    if not isinstance(text, str):
        return False
    return len(text) >= _SVG_NATIVE_MIN_CHARS


# ---------------------------------------------------------------------------
# Join-chain solver (Q117-06): indexed Rust chaining, the packed one-call ABI
# that replaces the Python solver's Theta(N^2) "scan every segment" loop.
# ---------------------------------------------------------------------------
def _joinchain_binding():
    """Return the ``narwhal_pure_core_joinchain`` ctypes handle, or ``None``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before the
    joinchain export keeps the complete, unchanged Python solver. The state
    globals are ``_jc_*`` -- distinct from this resolver's name.
    """
    global _jc_signature, _jc_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _jc_signature == signature:
        return _jc_binding
    with _lock:
        if _jc_signature == signature:
            return _jc_binding
        _jc_signature = signature
        _jc_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                fn = library.narwhal_pure_core_joinchain
                _i64p = ctypes.POINTER(ctypes.c_int64)
                _f64p = _DoubleP
                _u8p = ctypes.POINTER(ctypes.c_uint8)
                fn.argtypes = [
                    ctypes.c_int64,            # n_segments
                    _f64p, _f64p,             # starts, ends
                    ctypes.c_double,          # tol
                    _i64p, _i64p, _u8p,       # chain_off, chain_len, chain_closed
                    _f64p, _f64p, _f64p,      # chain_sx/sy/sz
                    _f64p, _f64p, _f64p,      # chain_ex/ey/ez
                    _i64p, _u8p,              # member_seg, member_rev
                    _i64p, _i64p,             # out_n_chains, out_n_members
                ]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _jc_binding = fn
            return _jc_binding
        return None


def _valid_joinchain_packed(n, n_chains, n_members, chain_off, chain_len,
                            chain_closed, chain_ends, member_seg,
                            member_rev) -> bool:
    """Strictly validate a packed Join-chain output before counting it success.

    The contract the Rust publisher guarantees and the converter relies on:
    exactly ``n`` members (they partition the ``n`` segments), ``n_chains``
    chains each with non-negative length summing to ``n_members`` (the
    complete-partition invariant), ``chain_off`` strictly non-decreasing and
    within range, closed flags restricted to 0/1, and every ``member_seg`` a
    finite segment index in ``[0, n)``.
    """
    if (not isinstance(n, int) or not isinstance(n_chains, int)
            or not isinstance(n_members, int)
            or n < 0 or n_chains < 0 or n_members < 0):
        return False
    if n_members != n:
        return False
    if (n_chains > len(chain_off) or n_chains > len(chain_len)
            or n_chains > len(chain_closed)):
        return False
    if n_chains == 0:
        return n_members == 0
    if n_members == 0:
        return False
    expected_off = 0
    for k in range(n_chains):
        off = chain_off[k]
        ln = chain_len[k]
        if (not isinstance(off, int) or not isinstance(ln, int)
                or off != expected_off or ln <= 0 or off + ln > n_members):
            return False
        if chain_closed[k] not in (0, 1):
            return False
        expected_off += ln
    if expected_off != n_members or len(chain_ends) != 6:
        return False
    if any(len(axis) < n_chains for axis in chain_ends):
        return False
    if any(not math.isfinite(axis[k])
           for axis in chain_ends for k in range(n_chains)):
        return False
    seen = set()
    for i in range(n_members):
        seg = member_seg[i]
        if (not isinstance(seg, int) or seg < 0 or seg >= n
                or seg in seen or member_rev[i] not in (0, 1)):
            return False
        seen.add(seg)
    return len(seen) == n


def _native_joinchain(starts, ends, tol):
    """Native indexed Join-chain solve (Q117-06), or ``None``.

    ``None`` means "use the unchanged Python solver" -- the library is
    unavailable, it predates the joinchain export, the input was rejected
    (non-finite coordinates, a negative or non-finite tolerance, or a
    coordinate too large for the index), or the packed output failed the
    strict validator. On success returns ``(n, chain_off, chain_len,
    chain_closed, chain_ends, member_seg, member_rev, n_chains, n_members)``
    where ``chain_ends`` is a flat ``[sx,sy,sz,ex,ey,ez]`` per chain -- which
    ``joinchain._chains_from_packed`` converts into the exact ``Chain``
    contract (members as ``(index, reverse)`` pairs, the caller mapping index
    to its own key), so a successful native route is the Python result.
    """
    global _native_joinchain_calls
    fn = _joinchain_binding()
    if fn is None:
        return None  # library predates the joinchain export
    n = len(starts) // 3
    if len(starts) != len(ends) or not (len(starts) % 3 == 0) or n == 0:
        return None
    if n > 1_000_000:
        return None  # above the native ceiling -> unchanged Python solver
    cap = n
    starts_arr = array.array("d", [0.0]) * (3 * n)
    ends_arr = array.array("d", [0.0]) * (3 * n)
    chain_off = array.array("q", [0]) * cap
    chain_len = array.array("q", [0]) * cap
    chain_closed = array.array("b", [0]) * cap
    chain_sx = array.array("d", [0.0]) * cap
    chain_sy = array.array("d", [0.0]) * cap
    chain_sz = array.array("d", [0.0]) * cap
    chain_ex = array.array("d", [0.0]) * cap
    chain_ey = array.array("d", [0.0]) * cap
    chain_ez = array.array("d", [0.0]) * cap
    member_seg = array.array("q", [0]) * cap
    member_rev = array.array("b", [0]) * cap
    out_n_chains = ctypes.c_int64(0)
    out_n_members = ctypes.c_int64(0)
    f64_arr = ctypes.c_double * (3 * n)
    i64_arr = ctypes.c_int64 * cap
    u8_arr = ctypes.c_uint8 * cap
    try:
        starts_view = f64_arr.from_buffer(starts_arr)
        ends_view = f64_arr.from_buffer(ends_arr)
        # Copy the caller's endpoint coordinates into the arrays we pass.
        for i, v in enumerate(starts):
            starts_arr[i] = v
        for i, v in enumerate(ends):
            ends_arr[i] = v
        rc = fn(
            n,
            starts_view, ends_view,
            float(tol),
            i64_arr.from_buffer(chain_off),
            i64_arr.from_buffer(chain_len),
            u8_arr.from_buffer(chain_closed),
            (ctypes.c_double * cap).from_buffer(chain_sx),
            (ctypes.c_double * cap).from_buffer(chain_sy),
            (ctypes.c_double * cap).from_buffer(chain_sz),
            (ctypes.c_double * cap).from_buffer(chain_ex),
            (ctypes.c_double * cap).from_buffer(chain_ey),
            (ctypes.c_double * cap).from_buffer(chain_ez),
            i64_arr.from_buffer(member_seg),
            u8_arr.from_buffer(member_rev),
            ctypes.byref(out_n_chains), ctypes.byref(out_n_members),
        )
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    if rc != 0:
        return None
    n_chains = out_n_chains.value
    n_members = out_n_members.value
    chain_ends = (chain_sx, chain_sy, chain_sz, chain_ex, chain_ey, chain_ez)
    if not _valid_joinchain_packed(n, n_chains, n_members, chain_off, chain_len,
                                   chain_closed, chain_ends, member_seg,
                                   member_rev):
        return None
    # A fully successful native call: record the route activation so a
    # production caller that silently forces the Python path is detectable.
    _native_joinchain_calls += 1
    return (n, chain_off, chain_len, chain_closed, chain_ends,
            member_seg, member_rev, n_chains, n_members)


def _joinchain_native_active(n_segments, force=False):
    """Whether the native Join-chain route should handle ``n_segments`` (Q117-06).

    True only when the joinchain export is available AND the segment count is
    at or above the measured crossover (where the complete native call --
    packing + FFI + conversion -- is >=10% faster than the Python solver). Below
    the crossover the unchanged Python solver stays active, so small workloads
    (where the FFI overhead dominates) never regress. ``force`` (tests only)
    runs the native route regardless of the threshold, to exercise it on small
    inputs.

    The crossover is segment-count based (not byte-based) because the Python
    route's cost is Theta(N^2) in the *number of segments* and the win is
    largest exactly where N is largest (the disjoint / ambiguous / reversed
    cases). A size-based gate could not separate "large N, small coordinates"
    (where native wins) from "small N, large coordinates" (where the FFI
    overhead dominates).
    """
    if force:
        return _joinchain_binding() is not None
    if _joinchain_binding() is None:
        return False
    if not isinstance(n_segments, int) or isinstance(n_segments, bool):
        return False
    if n_segments <= 0:
        return False
    return n_segments >= _JOINCHAIN_NATIVE_MIN_SEGMENTS


# Packed macro tokenizer output (Q117-04): the state globals are ``_macropp_*``
# -- distinct from the ``_macropp_binding`` resolver (the same collision trap
# as Q117-01's ``_objp_*`` / Q117-02's ``_dxp_*`` / Q117-03's ``_svgpp_*``) and
# from the two-pass JSON slots ``_m_*``.
_macropp_signature: str | None = None
_macropp_binding: Any = None  # narwhal_pure_core_macro_packed or None


def _macro_packed_binding():
    """Return the ``narwhal_pure_core_macro_packed`` ctypes handle (the packed
    one-pass ABI, Q117-04), or ``None``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before the
    packed export keeps every existing route and only the packed path falls
    back to Python. The state globals are ``_macropp_*`` -- distinct from this
    resolver's name (``_macro_packed_binding``) and from the two-pass JSON slots
    ``_m_*``; the name is kept distinct from the ``_macropp_binding`` cache so
    the resolver never clobbers the function it is caching (the collision trap
    the other packed resolvers avoid).
    """
    global _macropp_signature, _macropp_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _macropp_signature == signature:
        return _macropp_binding
    with _lock:
        if _macropp_signature == signature:
            return _macropp_binding
        _macropp_signature = signature
        _macropp_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                packed = library.narwhal_pure_core_macro_packed
                _u64p = ctypes.POINTER(ctypes.c_uint64)
                _i64p = ctypes.POINTER(ctypes.c_int64)
                packed.argtypes = [
                    ctypes.c_char_p, ctypes.c_size_t, ctypes.c_ubyte,
                    _ByteP, ctypes.c_size_t,
                    _ByteP, ctypes.c_size_t,
                    _u64p, ctypes.c_size_t,
                    _u64p, ctypes.c_size_t,
                    _u64p, ctypes.c_size_t,
                    _ByteP, ctypes.c_size_t,
                    _i64p, _i64p,
                ]
                packed.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _macropp_binding = packed
            return _macropp_binding
        return None


def _valid_macro_packed(n_steps, n_bytes, kinds, flags, s0, s1, s2,
                        strings) -> bool:
    """Strictly validate a packed macro output before counting it as success.

    The contract the Rust publisher guarantees and the converter relies on:
    ``n_steps`` steps fully written (kinds in 0..=5, every command flag bit
    within the documented set), ``n_bytes`` string bytes fully written, and
    each step's three ``(offset << 32) | length`` fields either ``0`` (empty)
    or an in-bounds slice of the string buffer (complete consumption -- no
    offset/length pair may run past the published byte count).
    """
    if (not isinstance(n_steps, int) or not isinstance(n_bytes, int)
            or n_steps < 0 or n_bytes < 0):
        return False
    if (n_steps > len(kinds) or n_steps > len(flags) or n_steps > len(s0)
            or n_steps > len(s1) or n_steps > len(s2)):
        return False
    if n_bytes > len(strings):
        return False
    raw = bytes(strings[:n_bytes])
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    cursor = 0
    for i in range(n_steps):
        if kinds[i] > 5:
            return False
        for pair in (s0[i], s1[i], s2[i]):
            if pair == 0:
                continue
            off = pair >> 32
            ln = pair & 0xFFFFFFFF
            if ln == 0 or off != cursor or off + ln > n_bytes:
                return False
            end = off + ln
            # A field must start/end on UTF-8 code-point boundaries. Continuation
            # bytes have the high-bit pattern 10xxxxxx.
            if ((off and raw[off] & 0xC0 == 0x80)
                    or (end < n_bytes and raw[end] & 0xC0 == 0x80)):
                return False
            cursor = end
    return cursor == n_bytes


def _native_macro_packed(text, lossless):
    """Native packed ``macro.tokenize`` (Q117-04), or ``None``.

    ``None`` means "use the unchanged Python tokenizer" -- the library is
    unavailable, it predates the packed export, the input was rejected (over
    the byte cap or not valid UTF-8), the packed output failed the strict
    validator, or the capacity was insufficient. On success returns
    ``(kinds, flags, s0, s1, s2, strings, n_steps, n_bytes)`` -- the packed
    step kind/flag/field arrays over the shared string buffer and the exact
    published counts -- which ``macro._steps_from_packed`` converts into the
    exact Python step contract (the lossless ``prefixes`` rebuilt as tuples),
    so a successful native route is byte-for-byte the Python result.
    """
    global _native_macro_calls
    packed_fn = _macro_packed_binding()
    if packed_fn is None:
        return None  # library predates the packed export
    text_input = _bounded_utf8(text)
    if text_input is None:
        return None
    tarr, tn = text_input
    # Capacity bound (proved, arithmetic only -- no JSON, no size query):
    # every step comes from at least one raw token, and every raw token is at
    # least one non-whitespace byte, so ``n_steps <= max(tn, 1)`` (the floor
    # covers the empty/whitespace edge). Each step's three string fields are
    # disjoint subsets of its token's bytes, so the total string bytes across
    # all steps is at most the total input bytes (``n_bytes <= tn``). Hence the
    # capacities below are never less than the Rust side needs.
    cap_steps = max(tn, 1)
    cap_strings = tn
    if cap_steps > _MAX_NATIVE_OUTPUT_BYTES:
        return None
    kinds = array.array("B", [0]) * cap_steps
    flags = array.array("B", [0]) * cap_steps
    s0 = array.array("Q", [0]) * cap_steps
    s1 = array.array("Q", [0]) * cap_steps
    s2 = array.array("Q", [0]) * cap_steps
    strings = array.array("B", [0]) * cap_strings
    out_n_steps = ctypes.c_int64(0)
    out_n_bytes = ctypes.c_int64(0)
    # Typed array views over the packed arrays (the ``c_X_Array_N`` form, as in
    # ``_native_svgpath_packed``), NOT ``POINTER(c_X).from_buffer`` -- the
    # latter wraps the buffer in a null pointer the Rust side then rejects.
    ub = ctypes.c_ubyte * cap_steps
    kinds_view = ub.from_buffer(kinds)
    flags_view = ub.from_buffer(flags)
    ub2 = ctypes.c_ubyte * cap_strings
    strings_view = ub2.from_buffer(strings) if cap_strings else None
    u64 = ctypes.c_uint64 * cap_steps
    s0_view = u64.from_buffer(s0)
    s1_view = u64.from_buffer(s1)
    s2_view = u64.from_buffer(s2)
    try:
        rc = packed_fn(
            tarr, tn,
            1 if lossless else 0,
            kinds_view, cap_steps,
            flags_view, cap_steps,
            s0_view, cap_steps,
            s1_view, cap_steps,
            s2_view, cap_steps,
            strings_view, cap_strings,
            ctypes.byref(out_n_steps), ctypes.byref(out_n_bytes),
        )
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    if rc != 0:
        return None
    n_steps = out_n_steps.value
    n_bytes = out_n_bytes.value
    if not _valid_macro_packed(n_steps, n_bytes, kinds, flags, s0, s1, s2,
                               strings):
        return None
    # A fully successful native call: record the route activation so a
    # production caller that silently forces the Python path is detectable.
    _native_macro_calls += 1
    return kinds, flags, s0, s1, s2, strings, n_steps, n_bytes


def _macro_packed_native_active(text):
    """Whether the packed native route may handle *text* (Q117-04).

    True only when the packed export is available and *text* is a string. The
    measured crossover (``NATIVE_MACRO_MIN_BYTES`` in :mod:`macro`) is owned by
    the caller, so the caller performs the byte-length check; this gate only
    reports that the ABI is resolvable and the input is a candidate.
    """
    if _macro_packed_binding() is None:
        return False
    return isinstance(text, str)


# Packed macro planner output (Q117-05): the state globals are ``_macroppp_*``
# -- distinct from the ``_macroppp_binding`` resolver (the same collision trap
# the other packed resolvers avoid) and from the packed tokenizer's
# ``_macropp_*`` state and the two-pass JSON planner slots ``_mp_*``.
_macroppp_signature: str | None = None
_macroppp_binding: Any = None  # narwhal_pure_core_macro_plan_packed or None


def _macro_plan_packed_binding():
    """Return the ``narwhal_pure_core_macro_plan_packed`` ctypes handle (the
    packed one-pass planner ABI, Q117-05), or ``None``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before the
    packed planner export keeps every existing route and only the packed path
    falls back to Python. The state globals are ``_macroppp_*`` -- distinct
    from this resolver's name (``_macro_plan_packed_binding``), the packed
    tokenizer's ``_macropp_*`` state, and the two-pass JSON planner slots
    ``_mp_*``.
    """
    global _macroppp_signature, _macroppp_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _macroppp_signature == signature:
        return _macroppp_binding
    with _lock:
        if _macroppp_signature == signature:
            return _macroppp_binding
        _macroppp_signature = signature
        _macroppp_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                packed = library.narwhal_pure_core_macro_plan_packed
                _u64p = ctypes.POINTER(ctypes.c_uint64)
                _i64p = ctypes.POINTER(ctypes.c_int64)
                packed.argtypes = [
                    ctypes.c_char_p, ctypes.c_size_t,
                    _ByteP, ctypes.c_size_t,
                    _ByteP, ctypes.c_size_t,
                    _u64p, ctypes.c_size_t,
                    _u64p, ctypes.c_size_t,
                    _u64p, ctypes.c_size_t,
                    _ByteP, ctypes.c_size_t,
                    _i64p, _i64p,
                ]
                packed.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _macroppp_binding = packed
            return _macroppp_binding
        return None


def _native_macro_plan_packed(text):
    """Native packed ``macro_plan.plan_macro`` (Q117-05), or ``None``.

    ``None`` means "use the unchanged Python planner" -- the library is
    unavailable, it predates the packed planner export, the input was rejected
    (over the byte cap or not valid UTF-8), the macro contains a transparent
    command outside the measured nestable allow-list (the stable rejection
    code), the packed output failed the strict validator, or the capacity was
    insufficient. On success returns
    ``(kinds, flags, s0, s1, s2, strings, n_actions, n_bytes)`` -- the packed
    action kind/flag/field arrays over the shared string buffer and the exact
    published counts -- which ``macro_plan._actions_from_packed`` converts
    into the exact Python action contract (``prefixes`` rebuilt as tuples),
    so a successful native route is byte-for-byte the Python result.
    """
    global _native_macro_plan_calls
    packed_fn = _macro_plan_packed_binding()
    if packed_fn is None:
        return None  # library predates the packed planner export
    text_input = _bounded_utf8(text)
    if text_input is None:
        return None
    tarr, tn = text_input
    # Capacity bound (proved, arithmetic only -- no JSON, no size query): the
    # planner emits at most one action per lossless step and every step comes
    # from a >=1-byte token, so ``n_actions <= max(tn, 1)`` (the floor covers
    # the empty/whitespace edge). Each action's three string fields are
    # subsets of its step's fields (a cancel carries a single ``!`` that is
    # itself an input byte), so the total string bytes is at most ``tn + 1``
    # (the ``+ 1`` guards the integer bound on the empty edge). Hence the
    # capacities below are never less than the Rust side needs.
    cap_actions = max(tn, 1)
    cap_strings = tn + 1
    if cap_actions > _MAX_NATIVE_OUTPUT_BYTES or cap_strings > _MAX_NATIVE_OUTPUT_BYTES:
        return None
    kinds = array.array("B", [0]) * cap_actions
    flags = array.array("B", [0]) * cap_actions
    s0 = array.array("Q", [0]) * cap_actions
    s1 = array.array("Q", [0]) * cap_actions
    s2 = array.array("Q", [0]) * cap_actions
    strings = array.array("B", [0]) * cap_strings
    out_n_actions = ctypes.c_int64(0)
    out_n_bytes = ctypes.c_int64(0)
    # Typed array views over the packed arrays (the ``c_X_Array_N`` form, as in
    # ``_native_macro_packed``), NOT ``POINTER(c_X).from_buffer`` -- the latter
    # wraps the buffer in a null pointer the Rust side then rejects.
    ub = ctypes.c_ubyte * cap_actions
    kinds_view = ub.from_buffer(kinds)
    flags_view = ub.from_buffer(flags)
    u64 = ctypes.c_uint64 * cap_actions
    s0_view = u64.from_buffer(s0)
    s1_view = u64.from_buffer(s1)
    s2_view = u64.from_buffer(s2)
    strings_view = (ctypes.c_ubyte * cap_strings).from_buffer(strings) \
        if cap_strings else None
    try:
        rc = packed_fn(
            tarr, tn,
            kinds_view, cap_actions,
            flags_view, cap_actions,
            s0_view, cap_actions,
            s1_view, cap_actions,
            s2_view, cap_actions,
            strings_view, cap_strings,
            ctypes.byref(out_n_actions), ctypes.byref(out_n_bytes),
        )
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    if rc != 0:
        return None
    n_actions = out_n_actions.value
    n_bytes = out_n_bytes.value
    # The validator is the same shape the tokenizer packed route uses: it is
    # parameterized on the count, so ``n_actions`` slots in for ``n_steps``.
    if not _valid_macro_packed(n_actions, n_bytes, kinds, flags, s0, s1, s2,
                               strings):
        return None
    # A fully successful native call: record the route activation so a
    # production caller that silently forces the Python path is detectable.
    _native_macro_plan_calls += 1
    return kinds, flags, s0, s1, s2, strings, n_actions, n_bytes


def _macro_plan_packed_native_active(text):
    """Whether the packed planner native route may handle *text* (Q117-05).

    True only when the packed planner export is available and *text* is a
    string. The measured crossover (``NATIVE_MACRO_PLAN_MIN_BYTES`` in
    :mod:`macro_plan`) is owned by the caller, so the caller performs the
    byte-length check; this gate only reports that the ABI is resolvable and
    the input is a candidate.
    """
    if _macro_plan_packed_binding() is None:
        return False
    return isinstance(text, str)



def encode_stl(triangles, name, binary) -> Optional[bytes]:
    """Encode *triangles* to a complete STL file's bytes, or ``None``.

    ``None`` means "use the unchanged Python writer" -- the library is
    unavailable, the ABI is wrong, the input is rejected, or the output is
    malformed. A non-``None`` return is the exact file bytes (binary or ASCII)
    and equals the Python writer's output for the same input.

    The work is split into the four phases the task reports separately:
    packing (the ``9 * N`` ``f64`` array), the Rust size query, the Rust encode
    (into the caller-owned buffer), and the output copy to ``bytes``.
    """
    global _native_calls
    binding = _binding()
    if binding is None:
        return None
    (size_fn, enc_fn, _dsize, _dec, _obj_size, _obj_enc, _obj_dsize, _obj_dec,
     _dxf_size, _dxf_enc, _dxf_dsize, _dxf_dec, _path) = binding
    try:
        tris = list(triangles)
        n = len(tris)
        if n > _MAX_STL_TRIANGLES:
            return None
        # PACKING phase: a contiguous 9*N f64 array, three per vertex (a, b, c).
        # array('d') packs at C speed (measured ~36 ms vs ~67 ms for elementwise
        # ctypes stores at 100,000 triangles); the from_buffer view is a
        # contiguous c_double array over the same 8-byte-double memory, which
        # is exactly the layout the native encoder reads.
        name_text = str(name)
        if len(name_text) > _MAX_NAME_BYTES:
            return None
        flat = array.array("d")
        ext = flat.extend
        for a, b, c in tris:
            ext((a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]))
        arr = (ctypes.c_double * (9 * n)).from_buffer(flat)
        nptr, nl = _name_ptr(name_text)
        bflag = 1 if binary else 0
        # RUST size query phase: exact output length, or a negative error code.
        nbytes = size_fn(arr, n, nptr, nl, bflag)
        derived = 84 + 50 * n if bflag else 128 + 2 * nl + 384 * n
        if not _size_is_bounded(nbytes, derived):
            return None
        if bflag and nbytes != 84 + 50 * n:
            return None
        buf = (ctypes.c_ubyte * nbytes)()
        # RUST encode phase: fills the caller-owned buffer, 0 on success.
        rc = enc_fn(arr, n, nptr, nl, bflag, buf, nbytes)
        if rc != 0:
            return None
        # OUTPUT phase: copy the caller-owned buffer to a Python ``bytes``.
        out = bytes(buf)
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    # A fully successful native encode: record the route activation so a
    # production caller that silently forces the Python path is detectable.
    _native_calls += 1
    return out


def decode_stl(data: bytes) -> Optional[list]:
    """Decode a complete STL file's bytes into triangles, or ``None``.

    ``None`` means "use the unchanged Python reader" -- the library is
    unavailable, the ABI is wrong, or the file is rejected (truncated header,
    facet-count mismatch, incomplete ASCII facet). A non-``None`` return is a
    list of ``(a, b, c)`` triangles with each point a 3-tuple of floats, in
    file order, and equals the Python reader's output for the same file.

    The work is split into the two phases the task reports separately: the
    Rust size query (the exact ``9 * count`` ``f64`` length, or an error) and
    the Rust decode (into the caller-owned ``array('d')`` buffer), then the
    reshape to ``(a, b, c)`` triangles.
    """
    global _native_decode_calls
    binding = _binding()
    if binding is None:
        return None
    # The binding is ``(stl_size, stl_encode, dec_size, dec,
    # obj_size, obj_encode, obj_decode_size, obj_decode,
    # dxf_size, dxf_encode, dxf_decode_size, dxf_decode, path)``.
    (_size_fn, _enc_fn, dec_size_fn, dec_fn, _obj_size, _obj_enc,
     _obj_dsize, _obj_dec, _dxf_size, _dxf_enc, _dxf_dsize, _dxf_dec,
     _path) = binding
    if dec_size_fn is None or dec_fn is None:
        return None  # ABI-1 encode-only library
    byte_input = _byte_input(data)
    if byte_input is None:
        return None
    _owned, dbuf, n = byte_input
    try:
        # RUST size query phase: exact 9*N f64 length, or a negative error code.
        coords = dec_size_fn(dbuf, n)
        derived_bytes = 1024 + 2 * n
        if (not isinstance(coords, int) or coords < 0 or coords % 9 != 0
                or coords // 9 > _MAX_STL_TRIANGLES
                or coords > sys.maxsize // 8
                or coords * 8 > derived_bytes
                or coords * 8 > _MAX_NATIVE_OUTPUT_BYTES):
            return None
        flat = array.array("d", [0.0]) * coords
        cbuf = (ctypes.c_double * coords).from_buffer(flat)
        # RUST decode phase: fills the caller-owned buffer, 0 on success.
        rc = dec_fn(dbuf, n, cbuf, coords)
        if rc != 0:
            return None
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    # RESHAPE phase: 9*N f64 -> (a, b, c) triangles of 3-tuples.
    tris = [
        ((flat[i], flat[i + 1], flat[i + 2]),
         (flat[i + 3], flat[i + 4], flat[i + 5]),
         (flat[i + 6], flat[i + 7], flat[i + 8]))
        for i in range(0, len(flat), 9)
    ]
    # A fully successful native decode: record the route activation so a
    # production caller that silently forces the Python path is detectable.
    _native_decode_calls += 1
    return tris


# Import-specific fused STL decode+weld. The generic tuple-returning decoder
# remains disabled in production because rebuilding triangle tuples is slower.
_stlm_signature: str | None = None
_stlm_binding: Any = None
_STL_MESH_NATIVE_MIN_BYTES = 50_000


def _stl_mesh_decode_binding():
    global _stlm_signature, _stlm_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _stlm_signature == signature:
        return _stlm_binding
    with _lock:
        if _stlm_signature == signature:
            return _stlm_binding
        _stlm_signature = signature
        _stlm_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                fn = library.narwhal_pure_core_stl_mesh_decode
                fn.argtypes = [
                    _ByteP, ctypes.c_size_t,
                    _DoubleP, ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t,
                    ctypes.POINTER(ctypes.c_int64),
                    ctypes.POINTER(ctypes.c_int64),
                ]
                fn.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _stlm_binding = fn
            return fn
        return None


def _stl_mesh_native_active(data) -> bool:
    return (_stl_mesh_decode_binding() is not None
            and isinstance(data, (bytes, bytearray, memoryview))
            and len(data) >= _STL_MESH_NATIVE_MIN_BYTES)


def decode_stl_mesh(data):
    """Return packed welded ``(vertices, indices, nv, nf)`` or ``None``."""
    global _native_stl_mesh_calls
    fn = _stl_mesh_decode_binding()
    binding = _binding()
    if fn is None or binding is None:
        return None
    byte_input = _byte_input(data)
    if byte_input is None:
        return None
    _owned, dbuf, nbytes = byte_input
    dec_size = binding[2]
    if dec_size is None:
        return None
    try:
        coords = dec_size(dbuf, nbytes)
        if (not isinstance(coords, int) or coords < 0 or coords % 9
                or coords // 9 > _MAX_STL_TRIANGLES):
            return None
        triangles = coords // 9
        vertex_cap = 3 * triangles
        index_cap = 3 * triangles
        if (3 * vertex_cap * 8 + index_cap * 4
                > _MAX_NATIVE_OUTPUT_BYTES):
            return None
        vertices = array.array("d", [0.0]) * (3 * vertex_cap)
        indices = array.array("I", [0]) * index_cap
        vertex_view = (ctypes.c_double * len(vertices)).from_buffer(vertices)
        index_view = (ctypes.c_uint32 * len(indices)).from_buffer(indices)
        out_nv = ctypes.c_int64(-1)
        out_nf = ctypes.c_int64(-1)
        rc = fn(dbuf, nbytes, vertex_view, vertex_cap,
                index_view, index_cap, ctypes.byref(out_nv),
                ctypes.byref(out_nf))
    except Exception:  # noqa: BLE001 -- complete Python fallback
        return None
    nv, nf = out_nv.value, out_nf.value
    if (rc != 0 or not isinstance(nv, int) or not isinstance(nf, int)
            or nv < 0 or nf < 0 or nv > vertex_cap or nf > triangles):
        return None
    for value in vertices[:3 * nv]:
        if not math.isfinite(value):
            return None
    for i in range(nf):
        a, b, c = indices[3 * i:3 * i + 3]
        if a >= nv or b >= nv or c >= nv or len({a, b, c}) != 3:
            return None
    _native_stl_mesh_calls += 1
    return vertices, indices, nv, nf


# One-pass OBJ encode (Q117-01): the state globals are ``_objp_*`` -- distinct
# from the ``_obj_encode_into_binding`` resolver (the same collision trap) and
# from the two-pass OBJ slots already in the 13-tuple binding.
_objp_signature: str | None = None
_objp_binding: Any = None  # narwhal_pure_core_obj_encode_into or None

# Q117-01 crossover: the one-pass native route is active for inputs at or
# above this many TOTAL vertices (summed over all meshes); below it the
# unchanged Python writer stays active so a sub-10%-win, sub-millisecond input
# never regresses. Measured end-to-end (packing + FFI + file I/O, best of 5,
# 2 meshes) against the Python writer on this box:
#
#   total verts    native / python    native win
#   200 (100/mesh)  0.31 / 0.34 ms     +9.6%   (sub-ms, <10%, noise)
#   1000 (500/mesh) 1.23 / 1.58 ms     +22.5%
#   10000 (5k/mesh) 11.1 / 15.2 ms     +27.1%
#   30000 (15k/mesh) 48.5 / 71.0 ms    +31.7%
#   60000 (named)   51.3 / 68.4 ms     +25.0%   (1.333x)
#
# Native is >=10% faster at and above 1000 total verts and never slower below
# it (the 200-vert case is sub-millisecond and <10%, i.e. noise), so 1024 is a
# safe documented crossover: it is just above the smallest MEASURED >=10% point
# (so every active case is a measured >=10% win) and keeps the tiny sub-10%
# sub-millisecond cases on Python. The named 60k-vert workload runs native at
# a 25% win.
_OBJ_NATIVE_MIN_VERTS = 1024


def _obj_encode_into_binding():
    """Return the ``narwhal_pure_core_obj_encode_into`` ctypes handle (the
    one-pass caller-capacity ABI, Q117-01), or ``None``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before the
    one-pass export keeps working and only the faster one-pass path falls back
    to the two-pass route (or Python). The state globals are ``_objp_*`` --
    distinct from this resolver's name.
    """
    global _objp_signature, _objp_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _objp_signature == signature:
        return _objp_binding
    with _lock:
        if _objp_signature == signature:
            return _objp_binding
        _objp_signature = signature
        _objp_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                enc_into = library.narwhal_pure_core_obj_encode_into
                _i64p = ctypes.POINTER(ctypes.c_int64)
                _i32p = ctypes.POINTER(ctypes.c_int32)
                enc_into.argtypes = [
                    _DoubleP, ctypes.c_int64, _ByteP, ctypes.c_int64,
                    _i64p, _i64p, _i64p, _i64p, ctypes.c_int64, _i32p,
                    ctypes.c_int64, ctypes.c_int64,
                    _ByteP, ctypes.c_size_t, _i64p,
                ]
                enc_into.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _objp_binding = enc_into
            return _objp_binding
        return None


def _pack_obj_unchecked(meshes):
    """Pack *meshes* into the native OBJ layout (one contiguous array per
    parameter), in the exact layout the encoder reads. Returns a dict of the
    packed views + counts, or ``None`` when the input is over the adapter caps
    or not a valid mesh list. The raw arrays are kept alive by the returned
    ``raw`` tuple for the duration of the FFI call.
    """
    try:
        meshes = list(meshes)
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    mesh_count = len(meshes)
    if mesh_count > _MAX_OBJ_MESHES:
        return None
    total_v = total_faces = total_face_idx = total_name_bytes = 0
    for name, vertices, faces in meshes:
        nv = len(vertices)
        nf = len(faces)
        total_v += nv
        total_faces += nf
        total_name_bytes += len(str(name).encode("latin-1"))
        for face in faces:
            total_face_idx += len(face)
        if (total_v > _MAX_OBJ_VERTICES or total_faces > _MAX_OBJ_FACES
                or total_face_idx > _MAX_OBJ_FACE_INDICES
                or total_name_bytes > _MAX_NAME_BYTES):
            return None
    # One contiguous array per native parameter (native byte order == the Rust
    # target). ``array`` packs at C speed; ``from_buffer`` exposes each as a
    # contiguous typed ctypes view over the same memory.
    vert_buf = array.array("d")
    names_concat = bytearray()
    name_lens = array.array("q")   # i64, per mesh
    vcounts = array.array("q")     # i64, per mesh
    fcounts = array.array("q")     # i64, per mesh
    face_arity = array.array("q")  # i64, per face
    face_idx = array.array("i")    # i32, flat local indices
    vext = vert_buf.extend
    for name, vertices, faces in meshes:
        name_b = str(name).encode("latin-1")
        names_concat.extend(name_b)
        name_lens.append(len(name_b))
        vcounts.append(len(vertices))
        fcounts.append(len(faces))
        for x, y, z in vertices:
            vext((x, y, z))
        for face in faces:
            face_arity.append(len(face))
            face_idx.extend(face)
    packed_total_v = len(vert_buf) // 3
    if (packed_total_v != total_v or len(face_arity) != total_faces
            or len(face_idx) != total_face_idx):
        return None
    views = (
        (ctypes.c_double * len(vert_buf)).from_buffer(vert_buf),
        (ctypes.c_ubyte * len(names_concat)).from_buffer(names_concat),
        (ctypes.c_int64 * len(name_lens)).from_buffer(name_lens),
        (ctypes.c_int64 * len(vcounts)).from_buffer(vcounts),
        (ctypes.c_int64 * len(fcounts)).from_buffer(fcounts),
        (ctypes.c_int64 * len(face_arity)).from_buffer(face_arity),
        (ctypes.c_int32 * len(face_idx)).from_buffer(face_idx),
    )
    return {
        "views": views,
        "raw": (vert_buf, names_concat, name_lens, vcounts, fcounts,
                face_arity, face_idx),
        "packed_total_v": packed_total_v,
        "total_name_bytes": total_name_bytes,
        "total_v": total_v,
        "total_faces": total_faces,
        "total_face_idx": total_face_idx,
        "mesh_count": mesh_count,
    }


def _pack_obj(meshes):
    """Exception-safe wrapper: any packing failure declines to Python."""
    try:
        return _pack_obj_unchecked(meshes)
    except Exception:  # noqa: BLE001 -- preserve the complete Python fallback
        return None


def _native_obj_encode_into(meshes):
    """One-pass native OBJ encode into a Python-owned ``bytearray`` (Q117-01).

    Returns ``(bytearray, written)`` on success, or ``None`` to fall back to
    the unchanged Python writer (library unavailable, it predates the one-pass
    export, the input is rejected, or the output is malformed). The
    ``bytearray`` is caller-owned and may be written directly (``memoryview``)
    to a file without an intermediate ``bytes`` copy. ``written`` is the exact
    byte count the encoder published on complete success.
    """
    global _native_obj_calls
    enc_into = _obj_encode_into_binding()
    if enc_into is None:
        return None  # library predates the one-pass export
    packed = _pack_obj(meshes)
    if packed is None:
        return None
    (vert_view, names_view, name_lens_view, vcounts_view, fcounts_view,
     face_arity_view, face_idx_view) = packed["views"]
    _raw = packed["raw"]  # keep the packed arrays alive for the FFI call
    packed_total_v = packed["packed_total_v"]
    total_name_bytes = packed["total_name_bytes"]
    total_v = packed["total_v"]
    total_faces = packed["total_faces"]
    total_face_idx = packed["total_face_idx"]
    mesh_count = packed["mesh_count"]
    # Conservative capacity: exactly the Rust ``capacity_bound`` (verified equal
    # to this expression), so the buffer is always large enough and the one-pass
    # call never returns ERR_SIZE for a valid input.
    max_index_chars = len(str(max(total_v, 1))) + 1
    derived = (13 + total_name_bytes + 4 * mesh_count + 80 * total_v
               + (max_index_chars + 1) * total_face_idx + 3 * total_faces)
    if derived > _MAX_NATIVE_OUTPUT_BYTES or derived > sys.maxsize:
        return None
    out = bytearray(derived)
    out_view = (ctypes.c_ubyte * derived).from_buffer(out)
    written = ctypes.c_int64(0)
    try:
        rc = enc_into(
            vert_view, packed_total_v,
            names_view, len(names_view),
            name_lens_view, vcounts_view, fcounts_view, face_arity_view,
            total_faces, face_idx_view, total_face_idx, mesh_count,
            out_view, derived, ctypes.byref(written),
        )
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    if rc != 0:
        return None
    w = written.value
    if not (isinstance(w, int) and 0 <= w <= derived):
        return None
    # A fully successful native encode: record the route activation so a
    # production caller that silently forces the Python path is detectable.
    _native_obj_calls += 1
    return out, w


def _obj_native_active(meshes):
    """Whether the one-pass native OBJ route should handle *meshes* (Q117-01).

    True only when the one-pass export is available AND the input is at or
    above the measured crossover (where native is >=10% faster than the Python
    writer). Below the crossover the Python writer stays active, so small
    workloads never regress.
    """
    if _obj_encode_into_binding() is None:
        return False
    if _OBJ_NATIVE_MIN_VERTS <= 0:
        return True
    try:
        return sum(len(v) for _, v, _ in meshes) >= _OBJ_NATIVE_MIN_VERTS
    except Exception:  # noqa: BLE001 -- treat as "not active"
        return False


def encode_obj(meshes) -> Optional[bytes]:
    """Encode *meshes* to a complete OBJ file's bytes, or ``None``.

    ``meshes`` is a sequence of ``(name, vertices, faces)`` exactly as
    ``obj_writer.write_obj`` takes it. ``None`` means "use the unchanged
    Python writer" -- the library is unavailable, it predates the one-pass
    export, the ABI is wrong, the input is rejected, or the output is
    malformed. A non-``None`` return is the exact file bytes and equals the
    Python writer's output for the same input.

    The one-pass route (Q117-01): pack once, then a single native call
    serializes the file exactly once into a Python-owned ``bytearray`` (no
    two-pass duplication of coordinate formatting).
    """
    result = _native_obj_encode_into(meshes)
    if result is None:
        return None
    out, written = result
    return bytes(out[:written])


def _decode_obj_raw(data: bytes) -> Optional[bytes]:
    """Run the checked native OBJ decoder and return its packed bytes."""
    binding = _binding()
    if binding is None:
        return None
    (_size_fn, _enc_fn, _dsize, _dec, _obj_size, _obj_enc,
     dec_size_fn, dec_fn, _dxf_size, _dxf_enc, _dxf_dsize, _dxf_dec,
     _path) = binding
    if dec_size_fn is None or dec_fn is None:
        return None
    byte_input = _byte_input(data)
    if byte_input is None:
        return None
    _owned, dbuf, n = byte_input
    try:
        size = dec_size_fn(dbuf, n)
        if not _size_is_bounded(size, 1024 * 1024 + 8 * n):
            return None
        cbuf = (ctypes.c_ubyte * size)()
        if dec_fn(dbuf, n, cbuf, size) != 0:
            return None
        return bytes(cbuf)
    except Exception:  # noqa: BLE001 -- any failure degrades to fallback
        return None


def decode_obj(data: bytes, file_base: str = None) -> Optional[tuple]:
    """Decode a complete OBJ file's bytes into ``(meshes, ignored)``, or ``None``.

    ``meshes`` is a list of ``(name, vertices, faces)`` in the exact shape
    ``obj_reader.read_obj`` returns (empty mesh names fall back to
    *file_base*; vertices are 3-tuples of floats; faces are tuples of 0-based
    ints, kept at the file's polygon degree). ``ignored`` is a
    ``{kind: count}`` dict of the non-geometry commands the file carried.

    ``None`` means "use the unchanged Python reader" -- the library is
    unavailable, it predates the OBJ decode exports, the ABI is wrong, the
    file is rejected (a ``v`` with too few coordinates, a face index that does
    not resolve to a real vertex, or a non-integer face token), or the packed
    output is malformed. A non-``None`` return is struct-identical to the
    Python reader's output for the same file.

    The work is split into the phases the task reports separately: the Rust
    size query (the exact packed length, or an error), the Rust decode (into
    the caller-owned buffer), the output copy to ``bytes``, and the parse of
    the self-describing packed buffer into ``(meshes, ignored)``.
    """
    global _native_decode_obj_calls
    raw = _decode_obj_raw(data)
    if raw is None:
        return None
    try:
        meshes, ignored = _parse_obj_packed(raw, file_base)
    except Exception:  # noqa: BLE001 -- malformed output degrades to fallback
        return None
    # A fully successful native decode: record the route activation so a
    # production caller that silently forces the Python path is detectable.
    _native_decode_obj_calls += 1
    return (meshes, ignored)


def decode_obj_packed(data: bytes, file_base: str = None) -> Optional[tuple]:
    """Decode OBJ for Import without rebuilding generic vertex/face tuples."""
    global _native_decode_obj_calls
    raw = _decode_obj_raw(data)
    if raw is None:
        return None
    try:
        packed = _parse_obj_packed(raw, file_base, flat=True)
    except Exception:  # noqa: BLE001 -- malformed output degrades to fallback
        return None
    _native_decode_obj_calls += 1
    return packed


# The DXF entity-type codes the Rust encoder understands (dxf.rs).
_DXF_TYPE_CODES = {"LINE": 0, "CIRCLE": 1, "ARC": 2, "POLYLINE": 3, "SPLINE": 4}


def _pack_dxf(entities, layers=None):
    """Pack *entities* into the native DXF layout (one contiguous array per
    parameter), in the exact layout the encoder reads. Returns a dict of the
    packed views + counts, or ``None`` when the input is over the adapter caps
    or not a valid entity list. The raw arrays are kept alive by the returned
    ``raw`` tuple for the duration of the FFI call.

    The layer merge, the non-ASCII refusal, the per-entity arity and the
    coordinate caps all happen here so the two-pass ``encode_dxf`` and the
    one-pass ``_native_dxf_encode_into`` share the exact same packing.
    """
    try:
        entities = list(entities)
        ec = len(entities)
        if ec > _MAX_DXF_ENTITIES:
            return None
        # LAYER merge: caller's layers first (their order, their colors),
        # then any layer an entity names (color 7). Mirrors
        # dxf_writer._merged_layers exactly, so the LAYER table order is the
        # same on both routes.
        layer_map = {}
        if layers:
            for name, color in layers.items():
                layer_map[name] = color
        for e in entities:
            name = e.get("layer", "0")
            if name not in layer_map:
                layer_map[name] = 7
        layer_names = list(layer_map.items())
        lc = len(layer_names)
        if lc > _MAX_DXF_LAYERS:
            return None
        # Rust currently interprets encoder layer bytes as UTF-8 and substitutes
        # an empty name on invalid UTF-8. Non-ASCII Latin-1 must therefore take
        # the byte-exact Python path (not silently become an empty layer).
        if any(not str(name).isascii() for name, _color in layer_names):
            return None
        expected_coords = 0
        for entity in entities:
            typ = _DXF_TYPE_CODES.get(entity["type"])
            if typ is None:
                return None
            points = (entity.get("points") if typ == 3
                      else entity.get("control_points") if typ == 4 else None)
            if typ == 4 and (points is None or len(points) < 4 or (len(points) - 1) % 3):
                return None
            expected_coords += (6 if typ in (0, 2) else 4 if typ == 1
                                else 3 * len(points))
            if expected_coords > _MAX_DXF_COORDS:
                return None
        layer_id_of = {name: i for i, (name, _c) in enumerate(layer_names)}
        # Per-entity packing (one slot per entity, in entity order).
        entity_types = array.array("q")   # i64 type code
        layer_ids = array.array("q")      # i64 layer index
        colors = array.array("q")         # i64 ACI or 0xRRGGBB true color
        point_counts = array.array("q")   # i64 POLYLINE/SPLINE point count
        closed_flags = array.array("q")   # i64 POLYLINE/SPLINE closed (0/1)
        nums = array.array("d")           # f64 flat coordinates
        for e in entities:
            t = _DXF_TYPE_CODES.get(e["type"])
            if t is None:
                return None  # unknown type: Python writer raises for these
            layer_ids.append(layer_id_of[e.get("layer", "0")])
            if "true_color" in e:
                true_color = int(e["true_color"])
                if not 0 <= true_color <= 0xFFFFFF:
                    return None
                colors.append(0x1000000 + true_color)
            else:
                color = int(e.get("color", 7))
                if not 0 <= color <= 255:
                    return None
                colors.append(color)
            entity_types.append(t)
            if t == 0:  # LINE: p0 (3) + p1 (3)
                p0, p1 = e["p0"], e["p1"]
                nums.extend((p0[0], p0[1], p0[2], p1[0], p1[1], p1[2]))
                point_counts.append(0)
                closed_flags.append(0)
            elif t == 1:  # CIRCLE: center (3) + radius (1)
                c, r = e["center"], e["radius"]
                nums.extend((c[0], c[1], c[2], r))
                point_counts.append(0)
                closed_flags.append(0)
            elif t == 2:  # ARC: center (3) + radius (1) + start (1) + end (1)
                c, r = e["center"], e["radius"]
                nums.extend((c[0], c[1], c[2], r, e["start_deg"], e["end_deg"]))
                point_counts.append(0)
                closed_flags.append(0)
            else:  # POLYLINE/SPLINE: 3 coords per point/control
                pts = e["points"] if t == 3 else e["control_points"]
                for p in pts:
                    nums.extend((p[0], p[1], p[2]))
                point_counts.append(len(pts))
                closed_flags.append(1 if e.get("closed") else 0)
        if len(nums) != expected_coords:
            return None
        # Layer name bytes (concatenated) + per-name byte lengths.
        names_concat = bytearray()
        name_lens = array.array("q")
        for name, _c in layer_names:
            nb = str(name).encode("latin-1")
            names_concat.extend(nb)
            name_lens.append(len(nb))
            if len(names_concat) > _MAX_NAME_BYTES:
                return None
        layer_colors = array.array("q")
        for _name, color in layer_names:
            value = int(color)
            if not (0 <= value <= 255 or 0x1000000 <= value <= 0x1FFFFFF):
                return None
            layer_colors.append(value)
        # Names are emitted once in the table, once per entity, and once per
        # POLYLINE vertex. This mirrors Rust's conservative capacity bound and
        # prevents a reused long layer name from under-allocating the FFI output.
        emitted_name_bytes = len(names_concat)
        for e in entities:
            name_len = len(str(e.get("layer", "0")).encode("latin-1"))
            repeats = 1 + (len(e["points"])
                           if e.get("type") == "POLYLINE" else 0)
            emitted_name_bytes += name_len * repeats
        # ctypes pointers over the packed arrays (from_buffer views, the same
        # layout the native encoder reads).
        views = (
            (ctypes.c_ubyte * len(names_concat)).from_buffer(names_concat),
            (ctypes.c_int64 * len(name_lens)).from_buffer(name_lens),
            (ctypes.c_int64 * len(layer_colors)).from_buffer(layer_colors),
            (ctypes.c_int64 * len(entity_types)).from_buffer(entity_types),
            (ctypes.c_int64 * len(layer_ids)).from_buffer(layer_ids),
            (ctypes.c_int64 * len(colors)).from_buffer(colors),
            (ctypes.c_double * len(nums)).from_buffer(nums),
            (ctypes.c_int64 * len(point_counts)).from_buffer(point_counts),
            (ctypes.c_int64 * len(closed_flags)).from_buffer(closed_flags),
        )
        return {
            "views": views,
            "raw": (names_concat, name_lens, layer_colors, entity_types,
                    layer_ids, colors, nums, point_counts, closed_flags),
            "lc": lc,
            "ec": ec,
            "num_coords": len(nums),
            "name_bytes": len(names_concat),
            "emitted_name_bytes": emitted_name_bytes,
        }
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None


def encode_dxf(entities, layers=None) -> Optional[bytes]:
    """Encode *entities* to complete ASCII DXF bytes, or ``None``.

    ``entities`` is a sequence of dicts in the exact shape
    ``dxf_writer.write_dxf`` takes (LINE / CIRCLE / ARC / POLYLINE / SPLINE). ``layers``
    is an optional ``{"name": aci_color}`` map; any layer an entity names but
    the caller did not list is added with color 7, caller order preserved.

    ``None`` means "use the unchanged Python writer" -- the library is
    unavailable, it predates the DXF exports, the ABI is wrong, an entity
    type is not one of the four supported (the Python writer raises for
    those), or the output is malformed. A non-``None`` return is the exact
    file bytes and equals the Python writer's output for the same input.

    The work is split into the phases the task reports separately: packing
    (the layer bytes, the per-entity type/layer/color/point/closed arrays,
    and the flat coordinate array), the Rust size query, the Rust encode
    (into the caller-owned buffer), and the output copy to ``bytes``.

    This is the LEGACY two-pass route (Q117-02): the Rust size query and the
    Rust encode each format every coordinate, so it is measured *slower* than
    the Python writer at every tested size and is kept for the direct ABI
    proof and byte-identity, not for production. The one-pass route
    (``_native_dxf_encode_into``) is the production fast path when it wins.
    """
    global _native_dxf_calls
    binding = _binding()
    if binding is None:
        return None
    (_s, _e, _ds, _d, _os, _oe, _ods, _od,
     dxf_size_fn, dxf_enc_fn, _dxf_dsize, _dxf_dec, _path) = binding
    if dxf_size_fn is None or dxf_enc_fn is None:
        return None  # library predates the DXF encode exports
    packed = _pack_dxf(entities, layers)
    if packed is None:
        return None
    (names_ptr, name_lens_ptr, lco_ptr, et_ptr, lid_ptr, col_ptr,
     nums_ptr, pc_ptr, cf_ptr) = packed["views"]
    _raw = packed["raw"]  # keep the packed arrays alive for the FFI call
    lc = packed["lc"]
    ec = packed["ec"]
    try:
        # RUST size query phase: exact output length, or a negative code.
        nbytes = dxf_size_fn(
            names_ptr, len(names_ptr), name_lens_ptr, lco_ptr, lc,
            et_ptr, lid_ptr, col_ptr, nums_ptr, len(nums_ptr), pc_ptr, cf_ptr,
            ec)
        derived = (512 + 128 * lc + 256 * ec + 64 * len(nums_ptr)
                   + packed["emitted_name_bytes"])
        if not _size_is_bounded(nbytes, derived):
            return None
        buf = (ctypes.c_ubyte * nbytes)()
        # RUST encode phase: fills the caller-owned buffer, 0 on success.
        rc = dxf_enc_fn(
            names_ptr, len(names_ptr), name_lens_ptr, lco_ptr, lc,
            et_ptr, lid_ptr, col_ptr, nums_ptr, len(nums_ptr), pc_ptr, cf_ptr,
            ec, buf, nbytes)
        if rc != 0:
            return None
        # OUTPUT phase: copy the caller-owned buffer to a Python ``bytes``.
        out = bytes(buf)
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    # A fully successful native encode: record the route activation so a
    # production caller that silently forces the Python path is detectable.
    _native_dxf_calls += 1
    return out


# One-pass DXF encode (Q117-02): the state globals are ``_dxp_*`` -- distinct
# from the ``_dxf_encode_into_binding`` resolver (the same collision trap as
# Q117-01) and from the two-pass DXF slots already in the 13-tuple binding.
_dxp_signature: str | None = None
_dxp_binding: Any = None  # narwhal_pure_core_dxf_encode_into or None

# Q117-02 crossover: the one-pass native route is active in PRODUCTION only
# for inputs at or above this many entities; below it the unchanged Python
# writer stays active so a sub-10%-win input never regresses. The measured
# end-to-end (packing + FFI + file I/O, best of 5) native/Python ratios never
# reach a stable >=10% win at ANY tested size (the two-pass route measured
# 1.856x slower at 1 entity up to 0.936x at 262k entities), so this threshold
# is set ABOVE every realistic input (5M is the adapter's hard cap) to keep
# production on the byte-exact Python writer, per the Qwen policy ("leave
# production Python active if no winning workload is demonstrated"). The
# one-pass route is still exercised directly (byte-identity, the mutation,
# the fallbacks) so the ABI is proven correct and ready to activate the
# moment a measured >=10% win appears.
_DXF_NATIVE_MIN_ENTITIES = _MAX_DXF_ENTITIES + 1


def _dxf_encode_into_binding():
    """Return the ``narwhal_pure_core_dxf_encode_into`` ctypes handle (the
    one-pass caller-capacity ABI, Q117-02), or ``None``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before the
    one-pass export keeps working and only the faster one-pass path falls back
    to the two-pass route (or Python). The state globals are ``_dxp_*`` --
    distinct from this resolver's name.
    """
    global _dxp_signature, _dxp_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _dxp_signature == signature:
        return _dxp_binding
    with _lock:
        if _dxp_signature == signature:
            return _dxp_binding
        _dxp_signature = signature
        _dxp_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                enc_into = library.narwhal_pure_core_dxf_encode_into
                _i64p = ctypes.POINTER(ctypes.c_int64)
                enc_into.argtypes = [
                    _ByteP, ctypes.c_int64, _i64p, _i64p, ctypes.c_int64,
                    _i64p, _i64p, _i64p, _DoubleP, ctypes.c_int64, _i64p,
                    _i64p, ctypes.c_int64,
                    _ByteP, ctypes.c_size_t, _i64p,
                ]
                enc_into.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _dxp_binding = enc_into
            return _dxp_binding
        return None


def _native_dxf_encode_into(entities, layers=None):
    """One-pass native DXF encode into a Python-owned ``bytearray`` (Q117-02).

    Returns ``(bytearray, written)`` on success, or ``None`` to fall back to
    the unchanged Python writer (library unavailable, it predates the one-pass
    export, the input is rejected, or the output is malformed). The
    ``bytearray`` is caller-owned and may be written directly (``memoryview``)
    to a file without an intermediate ``bytes`` copy. ``written`` is the exact
    byte count the encoder published on complete success.
    """
    global _native_dxf_calls
    enc_into = _dxf_encode_into_binding()
    if enc_into is None:
        return None  # library predates the one-pass export
    packed = _pack_dxf(entities, layers)
    if packed is None:
        return None
    (names_ptr, name_lens_ptr, lco_ptr, et_ptr, lid_ptr, col_ptr,
     nums_ptr, pc_ptr, cf_ptr) = packed["views"]
    _raw = packed["raw"]  # keep the packed arrays alive for the FFI call
    lc = packed["lc"]
    ec = packed["ec"]
    # Conservative capacity: exactly the Rust ``capacity_bound`` (verified
    # equal to this expression), so the buffer is always large enough and the
    # one-pass call never returns ERR_SIZE for a valid input.
    derived = (512 + 128 * lc + 256 * ec + 64 * len(nums_ptr)
               + packed["emitted_name_bytes"])
    if derived > _MAX_NATIVE_OUTPUT_BYTES or derived > sys.maxsize:
        return None
    out = bytearray(derived)
    out_view = (ctypes.c_ubyte * derived).from_buffer(out)
    written = ctypes.c_int64(0)
    try:
        rc = enc_into(
            names_ptr, len(names_ptr), name_lens_ptr, lco_ptr, lc,
            et_ptr, lid_ptr, col_ptr, nums_ptr, len(nums_ptr), pc_ptr, cf_ptr,
            ec,
            out_view, derived, ctypes.byref(written),
        )
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    if rc != 0:
        return None
    w = written.value
    if not (isinstance(w, int) and 0 <= w <= derived):
        return None
    # A fully successful native encode: record the route activation so a
    # production caller that silently forces the Python path is detectable.
    _native_dxf_calls += 1
    return out, w


def _dxf_native_active(entities):
    """Whether the one-pass native DXF route should handle *entities* (Q117-02).

    True only when the one-pass export is available AND the input is at or
    above the measured crossover (where native is >=10% faster than the Python
    writer). Measured native is never >=10% faster, so the crossover is set
    above every realistic input and this is False in practice -- the complete
    Python fallback stays active, per the Qwen policy.
    """
    if _dxf_encode_into_binding() is None:
        return False
    if _DXF_NATIVE_MIN_ENTITIES <= 0:
        return True
    try:
        return len(entities) >= _DXF_NATIVE_MIN_ENTITIES
    except Exception:  # noqa: BLE001 -- treat as "not active"
        return False


def _decode_dxf_raw(data: bytes) -> Optional[bytes]:
    """Run the checked native DXF decoder and return its packed bytes."""
    binding = _binding()
    if binding is None:
        return None
    (_s, _e, _ds, _d, _os, _oe, _ods, _od,
     _dxf_size, _dxf_enc, dec_size_fn, dec_fn, _path) = binding
    if dec_size_fn is None or dec_fn is None:
        return None
    byte_input = _byte_input(data)
    if byte_input is None:
        return None
    _owned, dbuf, n = byte_input
    try:
        size = dec_size_fn(dbuf, n)
        if not _size_is_bounded(size, 1024 * 1024 + 4 * n):
            return None
        cbuf = (ctypes.c_ubyte * size)()
        if dec_fn(dbuf, n, cbuf, size) != 0:
            return None
        return bytes(cbuf)
    except Exception:  # noqa: BLE001 -- any failure degrades to fallback
        return None


def decode_dxf(data: bytes) -> Optional[dict]:
    """Decode a complete ASCII R12 DXF file's bytes into the reader's dict.

    The return is struct-identical to ``dxf_reader.read_dxf`` for the same
    file::

        {"entities": [...], "layers": {name: aci},
         "insunits": int or None, "skipped": {kind: count}}

    ``None`` means "use the unchanged Python reader" -- the library is
    unavailable, it predates the DXF decode exports, the ABI is wrong, the
    file is rejected (a binary sentinel, a non-``AC1009`` ``$ACADVER``, a
    truncated line count, or an unparseable value), or the packed output is
    malformed. A non-``None`` return is the exact dict the Python reader
    would produce for the same file.

    The work is split into the phases the task reports separately: the Rust
    size query (the exact packed length, or an error), the Rust decode (into
    the caller-owned buffer), the output copy to ``bytes``, and the parse of
    the self-describing packed buffer into the reader's dict.
    """
    global _native_decode_dxf_calls
    raw = _decode_dxf_raw(data)
    if raw is None:
        return None
    try:
        out = _parse_dxf_packed(raw)
    except Exception:  # noqa: BLE001 -- malformed output degrades to fallback
        return None
    # A fully successful native decode: record the route activation so a
    # production caller that silently forces the Python path is detectable.
    _native_decode_dxf_calls += 1
    return out


def decode_dxf_packed(data: bytes) -> Optional[tuple]:
    """Decode DXF for Import without rebuilding generic entity dictionaries."""
    global _native_decode_dxf_calls
    raw = _decode_dxf_raw(data)
    if raw is None:
        return None
    try:
        packed = _parse_dxf_packed(raw, flat=True)
    except Exception:  # noqa: BLE001 -- malformed output degrades to fallback
        return None
    _native_decode_dxf_calls += 1
    return packed


def _parse_dxf_packed(raw: bytes, *, flat=False):
    """Parse and fully validate one native DXF packed result.

    Every count is independently nonnegative and bounded, entity coordinate
    arity is exact, closed fields are literal 0/1, and the final offset must
    consume the buffer exactly. Any inconsistency declines to Python.
    """
    if not isinstance(raw, bytes) or len(raw) < 16 or len(raw) > _MAX_NATIVE_OUTPUT_BYTES:
        raise ValueError("DXF packed buffer has an invalid length")
    magic, version, insunits = struct.unpack_from("<IIq", raw, 0)
    if magic != 0x44584631 or version != 1:
        raise ValueError("bad DXF magic/version: %08x/%d" % (magic, version))
    o = 16

    def _read_i64(count: int) -> tuple:
        nonlocal o
        if count < 0 or count > (len(raw) - o) // 8:
            raise ValueError("DXF packed buffer overruns (i64 run)")
        vals = struct.unpack_from("<%dq" % count, raw, o) if count else ()
        o += count * 8
        return vals

    def _read_f64(count: int) -> tuple:
        nonlocal o
        if count < 0 or count > (len(raw) - o) // 8:
            raise ValueError("DXF packed buffer overruns (f64 run)")
        vals = struct.unpack_from("<%dd" % count, raw, o) if count else ()
        o += count * 8
        return vals

    def _read_run(lengths) -> list:
        nonlocal o
        if any(length < 0 for length in lengths):
            raise ValueError("DXF packed buffer has a negative name length")
        total = sum(lengths)
        if total > len(raw) - o:
            raise ValueError("DXF packed buffer overruns (name run)")
        out = []
        for length in lengths:
            out.append(raw[o:o + length])
            o += length
        return out

    (layer_count,) = _read_i64(1)
    if not 0 <= layer_count <= _MAX_DXF_LAYERS:
        raise ValueError("DXF packed buffer has an invalid layer count")
    layer_name_lens = _read_i64(layer_count)
    layer_names = _read_run(layer_name_lens)
    packed_layer_colors = _read_i64(layer_count)

    (entity_count,) = _read_i64(1)
    if not 0 <= entity_count <= _MAX_DXF_ENTITIES:
        raise ValueError("DXF packed buffer has an invalid entity count")
    entity_types = _read_i64(entity_count)
    ent_name_lens = _read_i64(entity_count)
    ent_layer_names = _read_run(ent_name_lens)
    packed_entity_colors = _read_i64(entity_count)
    entity_closed = _read_i64(entity_count)
    entity_coord_counts = _read_i64(entity_count)
    if any(count < 0 for count in entity_coord_counts):
        raise ValueError("DXF packed buffer has a negative coordinate count")
    total_coords = sum(entity_coord_counts)
    if total_coords > _MAX_DXF_COORDS:
        raise ValueError("DXF packed buffer has too many coordinates")
    for typ, closed, count in zip(entity_types, entity_closed,
                                  entity_coord_counts):
        if typ not in (0, 1, 2, 3, 4, 5):
            raise ValueError("DXF packed buffer has unknown type %d" % typ)
        if closed not in (0, 1):
            raise ValueError("DXF packed buffer has a non-boolean closed field")
        if typ == 0 and (count != 6 or closed != 0):
            raise ValueError("DXF LINE packed arity/closed field is invalid")
        if typ == 1 and (count != 4 or closed != 0):
            raise ValueError("DXF CIRCLE packed arity/closed field is invalid")
        if typ == 2 and (count != 6 or closed != 0):
            raise ValueError("DXF ARC packed arity/closed field is invalid")
        if typ in (3, 4) and count % 3:
            raise ValueError("DXF polyline coordinate count is not divisible by 3")
        if typ == 5 and count < 4:
            raise ValueError("DXF SPLINE packed header is missing")
    coords = _read_f64(total_coords)
    coord_offset = 0
    for typ, count in zip(entity_types, entity_coord_counts):
        if typ == 5:
            header = coords[coord_offset:coord_offset + 4]
            values = tuple(int(value) for value in header)
            if any(float(integer) != value for integer, value in zip(values, header)):
                raise ValueError("DXF SPLINE packed counts are not integers")
            degree, knot_count, point_count, weight_count = values
            expected = 4 + knot_count + point_count * 3 + weight_count
            segment = coords[coord_offset:coord_offset + count]
            knot_start = 4
            weight_start = knot_start + knot_count + point_count * 3
            knots = segment[knot_start:knot_start + knot_count]
            weights = segment[weight_start:]
            if (not 1 <= degree <= 11 or point_count <= degree
                    or knot_count != point_count + degree + 1
                    or weight_count not in (0, point_count) or count != expected
                    or any(not math.isfinite(value) for value in segment[4:])
                    or any(knots[index] > knots[index + 1]
                           for index in range(len(knots) - 1))
                    or any(weight <= 0 for weight in weights)):
                raise ValueError("DXF SPLINE packed arity is invalid")
        coord_offset += count

    (skipped_count,) = _read_i64(1)
    if not 0 <= skipped_count <= _MAX_DXF_ENTITIES:
        raise ValueError("DXF packed buffer has an invalid skipped count")
    skipped_lens = _read_i64(skipped_count)
    skipped_names = _read_run(skipped_lens)
    skipped_counts = _read_i64(skipped_count)
    if any(count < 0 for count in skipped_counts):
        raise ValueError("DXF packed buffer has a negative skipped count")
    if o != len(raw):
        raise ValueError("DXF packed buffer has %d trailing bytes" % (len(raw) - o))

    def _color(value):
        if 1 << 24 <= value <= (1 << 25) - 1:
            return 7, value - (1 << 24)
        return value, None

    decoded_layers = tuple(name.decode("latin-1") for name in layer_names)
    decoded_entity_layers = tuple(name.decode("latin-1") for name in ent_layer_names)
    layer_colors_and_true = tuple(_color(value) for value in packed_layer_colors)
    entity_colors_and_true = tuple(_color(value) for value in packed_entity_colors)
    layer_colors = tuple(value[0] for value in layer_colors_and_true)
    layer_true_colors = {decoded_layers[i]: value[1]
                         for i, value in enumerate(layer_colors_and_true)
                         if value[1] is not None}
    entity_colors = tuple(value[0] for value in entity_colors_and_true)
    entity_true_colors = tuple(value[1] for value in entity_colors_and_true)
    layers = {decoded_layers[i]: layer_colors[i] for i in range(layer_count)}
    skipped = {skipped_names[i].decode("latin-1"): skipped_counts[i]
               for i in range(skipped_count)}
    if flat:
        return (tuple(entity_types), decoded_entity_layers, entity_colors,
                entity_true_colors, tuple(entity_closed), tuple(entity_coord_counts),
                tuple(coords), layers, layer_true_colors,
                (None if insunits == -1 else insunits), skipped)

    entities = []
    c_off = 0
    for i in range(entity_count):
        typ = entity_types[i]
        count = entity_coord_counts[i]
        c = coords[c_off:c_off + count]
        c_off += count
        layer = decoded_entity_layers[i]
        color = entity_colors[i]
        true_color = entity_true_colors[i]
        closed = bool(entity_closed[i])
        if typ == 0:
            ent = {"type": "LINE", "p0": (c[0], c[1], c[2]),
                   "p1": (c[3], c[4], c[5]), "layer": layer, "color": color}
        elif typ == 1:
            ent = {"type": "CIRCLE", "center": (c[0], c[1], c[2]),
                   "radius": c[3], "layer": layer, "color": color}
        elif typ == 2:
            ent = {"type": "ARC", "center": (c[0], c[1], c[2]),
                   "radius": c[3], "start_deg": c[4], "end_deg": c[5],
                   "layer": layer, "color": color}
        elif typ == 5:
            degree, knot_count, point_count, weight_count = map(int, c[:4])
            pos = 4
            knots = list(c[pos:pos + knot_count])
            pos += knot_count
            points = [tuple(c[at:at + 3])
                      for at in range(pos, pos + point_count * 3, 3)]
            pos += point_count * 3
            ent = {"type": "SPLINE", "degree": degree, "knots": knots,
                   "points": points, "weights": list(c[pos:pos + weight_count]),
                   "closed": closed, "layer": layer, "color": color}
        else:
            points = [(c[k], c[k + 1], c[k + 2]) for k in range(0, count, 3)]
            ent = {"type": ("POLYLINE" if typ == 3 else "LWPOLYLINE"),
                   "points": points, "closed": closed,
                   "layer": layer, "color": color}
        if true_color is not None:
            ent["true_color"] = true_color
        entities.append(ent)
    if c_off != len(coords):
        raise ValueError("DXF packed coordinate offsets do not match")

    return {"entities": entities, "layers": layers,
            "layer_true_colors": layer_true_colors,
            "insunits": (None if insunits == -1 else insunits),
            "skipped": skipped}


def _parse_obj_packed(raw: bytes, file_base: str = None, *, flat=False) -> tuple:
    """Parse and fully validate one native OBJ packed result.

    Counts are independently bounded, vertex-coordinate counts are divisible by
    three, face arities are at least three, every local index is in range, the
    reserved header bytes are zero, and all offsets consume the buffer exactly.
    """
    if not isinstance(raw, bytes) or len(raw) < 24 or len(raw) > _MAX_NATIVE_OUTPUT_BYTES:
        raise ValueError("OBJ packed buffer has an invalid length")
    magic, version, mesh_count, ignored_count = struct.unpack_from("<IIII", raw, 0)
    if magic != 0x33445741 or version != 1 or raw[16:24] != b"\0" * 8:
        raise ValueError("bad OBJ magic/version/reserved header")
    if mesh_count > _MAX_OBJ_MESHES or ignored_count > _MAX_OBJ_MESHES:
        raise ValueError("OBJ packed buffer has too many records")
    o = 24

    def _read_i64(count: int) -> tuple:
        nonlocal o
        if count < 0 or count > (len(raw) - o) // 8:
            raise ValueError("OBJ packed buffer overruns (i64 run)")
        vals = struct.unpack_from("<%dq" % count, raw, o) if count else ()
        o += count * 8
        return vals

    def _read_f64(count: int) -> tuple:
        nonlocal o
        if count < 0 or count > (len(raw) - o) // 8:
            raise ValueError("OBJ packed buffer overruns (f64 run)")
        vals = struct.unpack_from("<%dd" % count, raw, o) if count else ()
        o += count * 8
        return vals

    def _read_run(lengths) -> list:
        nonlocal o
        if any(length < 0 for length in lengths):
            raise ValueError("OBJ packed buffer has a negative name length")
        total = sum(lengths)
        if total > len(raw) - o:
            raise ValueError("OBJ packed buffer overruns (name run)")
        out = []
        for length in lengths:
            out.append(raw[o:o + length])
            o += length
        return out

    name_lens = _read_i64(mesh_count)
    names = _read_run(name_lens)
    vcounts = _read_i64(mesh_count)
    fcounts = _read_i64(mesh_count)
    if (any(count < 0 or count % 3 for count in vcounts)
            or sum(vcounts) // 3 > _MAX_OBJ_VERTICES):
        raise ValueError("OBJ packed vertex counts are invalid")
    if any(count < 0 for count in fcounts) or sum(fcounts) > _MAX_OBJ_FACES:
        raise ValueError("OBJ packed face counts are invalid")
    total_faces = sum(fcounts)
    face_arity = _read_i64(total_faces)
    if (any(arity < 3 for arity in face_arity)
            or sum(face_arity) > _MAX_OBJ_FACE_INDICES):
        raise ValueError("OBJ packed face arities are invalid")
    total_coords = sum(vcounts)
    total_indices = sum(face_arity)
    verts = _read_f64(total_coords)
    face_idx = _read_i64(total_indices)
    ignored_lens = _read_i64(ignored_count)
    ign_names = _read_run(ignored_lens)
    ignored_counts = _read_i64(ignored_count)
    if any(count < 0 for count in ignored_counts):
        raise ValueError("OBJ packed ignored counts are negative")
    if o != len(raw):
        raise ValueError("OBJ packed buffer has %d trailing bytes" % (len(raw) - o))

    meshes = []
    decoded_names = []
    v_off = f_off = face_no = 0
    for m in range(mesh_count):
        name = names[m].decode("latin-1") or (file_base or "")
        decoded_names.append(name)
        coordinate_count = vcounts[m]
        vertex_count = coordinate_count // 3
        vertices = None if flat else [tuple(verts[v_off + i:v_off + i + 3])
                                     for i in range(0, coordinate_count, 3)]
        v_off += coordinate_count
        faces = None if flat else []
        for _ in range(fcounts[m]):
            arity = face_arity[face_no]
            face_no += 1
            end = f_off + arity
            if any(face_idx[index] < 0 or face_idx[index] >= vertex_count
                   for index in range(f_off, end)):
                raise ValueError("OBJ packed face index is out of local range")
            if faces is not None:
                faces.append(tuple(face_idx[f_off:end]))
            f_off = end
        if not flat:
            meshes.append((name, vertices, faces))
    if (v_off != len(verts) or f_off != len(face_idx)
            or face_no != len(face_arity)):
        raise ValueError("OBJ packed offsets do not match declared counts")

    ignored = {ign_names[i].decode("latin-1"): ignored_counts[i]
               for i in range(ignored_count)}
    if flat:
        return (tuple(decoded_names), tuple(vcounts), tuple(fcounts),
                tuple(face_arity), tuple(verts), tuple(face_idx), ignored)
    return meshes, ignored


def _reset_for_tests() -> None:
    """Forget discovery state so tests can re-point ``NARWHAL_PURE_CORE``."""
    global _signature, _native_binding, _status
    global _native_calls, _native_decode_calls, _native_stl_mesh_calls, _native_obj_calls
    global _native_decode_obj_calls, _native_dxf_calls
    global _native_decode_dxf_calls
    global _np_signature, _np_binding, _native_numparse_calls
    global _c_signature, _c_binding, _native_calc_calls
    global _b_signature, _b_binding, _native_bearing_calls
    global _co_signature, _co_binding, _native_coords_calls
    global _m_signature, _m_binding, _native_macro_calls
    global _mp_signature, _mp_binding, _native_macro_plan_calls
    global _macropp_signature, _macropp_binding
    global _macroppp_signature, _macroppp_binding
    global _sv_signature, _sv_binding, _native_svgpath_calls
    global _objp_signature, _objp_binding
    global _dxp_signature, _dxp_binding
    global _svgpp_signature, _svgpp_binding
    global _jc_signature, _jc_binding, _native_joinchain_calls
    global _stlm_signature, _stlm_binding
    global _pdfp_signature, _pdfp_binding, _native_pdf_calls
    global _svgvp_signature, _svgvp_binding, _native_svg_vector_calls
    with _lock:
        _signature = None
        _native_binding = None
        _status = "not checked"
        _native_calls = 0
        _native_decode_calls = 0
        _native_stl_mesh_calls = 0
        _native_obj_calls = 0
        _native_decode_obj_calls = 0
        _native_dxf_calls = 0
        _native_decode_dxf_calls = 0
        _native_pdf_calls = 0
        _native_svg_vector_calls = 0
        _np_signature = None
        _np_binding = None
        _native_numparse_calls = 0
        _c_signature = None
        _c_binding = None
        _native_calc_calls = 0
        _b_signature = None
        _b_binding = None
        _native_bearing_calls = 0
        _co_signature = None
        _co_binding = None
        _native_coords_calls = 0
        _m_signature = None
        _m_binding = None
        _native_macro_calls = 0
        _mp_signature = None
        _mp_binding = None
        _native_macro_plan_calls = 0
        _sv_signature = None
        _sv_binding = None
        _native_svgpath_calls = 0
        _objp_signature = None
        _objp_binding = None
        _dxp_signature = None
        _dxp_binding = None
        _svgpp_signature = None
        _svgpp_binding = None
        _macropp_signature = None
        _macropp_binding = None
        _macroppp_signature = None
        _macroppp_binding = None
        _jc_signature = None
        _jc_binding = None
        _native_joinchain_calls = 0
        _stlm_signature = None
        _stlm_binding = None
        _cached_macro_json.cache_clear()
        _cached_macro_plan_json.cache_clear()
        _cached_svgpath_json.cache_clear()
        _pdfp_signature = None
        _pdfp_binding = None
        _svgvp_signature = None
        _svgvp_binding = None


# ---------------------------------------------------------------------------
# One-pass PDF vector serializer (Q117-07)
#
# The state globals are ``_pdfp_*`` -- distinct from the resolver name (the same
# collision trap as Q117-01's ``_objp_*`` / Q117-03's ``_svgpp_*``). Serializes
# the complete single-page PDF 1.4 EXACTLY ONCE into a Python-owned ``bytearray``
# (byte-for-byte the ``vector_export._build_pdf`` output), eliminating the Python
# writer's per-operator ``f"...:.4f"`` formatting -- the dominant cost at scale.
# Python keeps the file I/O and is the complete fallback: library absent, export
# missing, call error, or output malformed all degrade to the unchanged Python
# serializer.
# ---------------------------------------------------------------------------

_pdfp_signature: str | None = None
_pdfp_binding: Any = None  # narwhal_pure_core_pdf_encode_into or None
_svgvp_signature: str | None = None
_svgvp_binding: Any = None  # narwhal_pure_core_svg_encode_into or None

# Q117-07 crossover: the one-pass native route is active only for inputs at or
# above this many strokes; below it the unchanged Python writer stays active so
# a small input never regresses.
#
# MEASURED (best of 6, this box; packing + FFI + conversion, NOT Rust compute
# alone) -- the native one-pass route is SLOWER than the Python writer at every
# size, including the 50,000-stroke named workload:
#   strokes   Python   native   native/Python
#     1        0.01ms   0.03ms      2.18   (slower)
#   1000       5.06ms   5.92ms      1.17   (slower)
#   10,000    54.37ms  60.93ms      1.12   (slower)
#   50,000   263.70ms 308.05ms      1.17   (slower)  [baseline was 197.49ms]
#
# Root cause (MEASURED, not a bug): the PDF content stream is ~90% per-float
# ``:.4f`` formatting. CPython formats floats in C (David Gay's dtoa), which
# beats Rust's ``core::fmt`` float formatter, AND the ctypes route pays an
# extra Python-side tuple->array marshalling pass plus a ``bytes`` slice-copy
# that the pure-Python writer never pays. So a native PDF serializer cannot be
# >=10% faster on this workload with the current toolchain.
#
# DECISION (this task): no winning workload is demonstrated, so per the policy
# ("Leave production Python active if no winning workload is demonstrated")
# production STAYS ON PYTHON. The threshold is set to a stroke count no real
# document reaches, disabling the native route in ``write_pdf`` while keeping
# the verified-correct native encoder available (``encode_pdf``) and the route
# flip-able.
# The Rust float-formatter vs CPython gap is a toolchain-level constraint (not a code
# bug); a win would require a faster fixed-point formatter or a different ABI
# that avoids the Python-side marshalling (e.g. passing the stroke tuples in).
_PDF_NATIVE_MIN_STROKES = 10_000_000  # DISABLED: native is slower at every size


def _pdf_encode_into_binding():
    """Return the ``narwhal_pure_core_pdf_encode_into`` ctypes handle (the
    one-pass caller-capacity ABI, Q117-07), or ``None``.

    Resolved as OPTIONAL (``None`` when absent) so a library built before the
    one-pass export keeps working and only the PDF route falls back to Python.
    The state globals are ``_pdfp_*`` -- distinct from this resolver's name.
    """
    global _pdfp_signature, _pdfp_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _pdfp_signature == signature:
        return _pdfp_binding
    with _lock:
        if _pdfp_signature == signature:
            return _pdfp_binding
        _pdfp_signature = signature
        _pdfp_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                enc_into = library.narwhal_pure_core_pdf_encode_into
                _i64p = ctypes.POINTER(ctypes.c_int64)
                _i32p = ctypes.POINTER(ctypes.c_int32)
                enc_into.argtypes = [
                    _DoubleP, ctypes.c_int64, _i64p, _i32p,
                    _DoubleP, _DoubleP, _ByteP, _i64p,
                    _ByteP, ctypes.c_int64, ctypes.c_double, ctypes.c_double,
                    ctypes.c_int64, _ByteP, ctypes.c_size_t, _i64p,
                ]
                enc_into.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _pdfp_binding = enc_into
            return _pdfp_binding
        return None


def _svg_encode_into_binding():
    """Return the optional Q117-08 one-pass SVG encoder."""
    global _svgvp_signature, _svgvp_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _svgvp_signature == signature:
        return _svgvp_binding
    with _lock:
        if _svgvp_signature == signature:
            return _svgvp_binding
        _svgvp_signature = signature
        _svgvp_binding = None
        candidates = ((Path(explicit).expanduser(),) if explicit
                      else (_PACKAGED_LIBRARY,))
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_pure_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    continue
                enc_into = library.narwhal_pure_core_svg_encode_into
                _i64p = ctypes.POINTER(ctypes.c_int64)
                _i32p = ctypes.POINTER(ctypes.c_int32)
                enc_into.argtypes = [
                    _DoubleP, ctypes.c_int64, _i64p, _i32p,
                    _DoubleP, _DoubleP, _ByteP, _i64p,
                    ctypes.c_double, ctypes.c_double, ctypes.c_int64,
                    _ByteP, ctypes.c_size_t, _i64p,
                ]
                enc_into.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _svgvp_binding = enc_into
            return _svgvp_binding
        return None


def _pack_pdf(strokes, page_w_mm, page_h_mm, title, _layer_encoding="latin-1"):
    """Pack *strokes* (a sequence of ``vector_export.Stroke``) into the native
    PDF layout (one contiguous array per parameter). Returns a dict of the
    packed views + counts, or ``None`` when the input is over the adapter caps
    or not a valid stroke list. The raw arrays are kept alive by the returned
    ``raw`` tuple for the duration of the FFI call.
    """
    try:
        strokes = list(strokes)
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    stroke_count = len(strokes)
    if stroke_count > _MAX_OBJ_FACES:  # reuse the 5M entity cap as a sane bound
        return None
    beziers = array.array("d")
    seg_counts = array.array("q")
    closed = array.array("i")
    rgb = array.array("d")
    widths = array.array("d")
    layer_concat = bytearray()
    layer_lens = array.array("q")
    total_segments = 0
    bext = beziers.extend
    for s in strokes:
        bz = s.beziers
        nseg = len(bz)
        total_segments += nseg
        if (total_segments > _MAX_OBJ_FACE_INDICES
                or len(layer_concat) > _MAX_NAME_BYTES):
            return None
        # The content stream is built from p1, p2, p3 of each cubic (p0 is only
        # the move-to start), so the native layout is 8 floats per segment with
        # the FIRST two (p0) unused by the serializer. Store p0,p1,p2,p3 in
        # order so the Rust side can index ``o+2 .. o+7`` for the cubic control
        # points and ``o+0, o+1`` for the move-to start.
        for (x0, y0), (x1, y1), (x2, y2), (x3, y3) in bz:
            bext((x0, y0, x1, y1, x2, y2, x3, y3))
        seg_counts.append(nseg)
        closed.append(1 if s.closed else 0)
        r, g, b = s.rgb
        rgb.extend((r, g, b))
        widths.append(s.width_mm)
        layer = s.layer
        if layer:
            layer_b = layer.encode(_layer_encoding)
            layer_concat.extend(layer_b)
            layer_lens.append(len(layer_b))
        else:
            layer_lens.append(0)
    if len(beziers) != total_segments * 8:
        return None
    try:
        title_b = str(title).encode("latin-1")
    except Exception:  # noqa: BLE001
        return None
    views = (
        (ctypes.c_double * len(beziers)).from_buffer(beziers),
        (ctypes.c_int64 * len(seg_counts)).from_buffer(seg_counts),
        (ctypes.c_int32 * len(closed)).from_buffer(closed),
        (ctypes.c_double * len(rgb)).from_buffer(rgb),
        (ctypes.c_double * len(widths)).from_buffer(widths),
        (ctypes.c_ubyte * len(layer_concat)).from_buffer(layer_concat),
        (ctypes.c_int64 * len(layer_lens)).from_buffer(layer_lens),
    )
    title_view = (ctypes.c_ubyte * len(title_b)).from_buffer(bytearray(title_b))
    return {
        "views": views,
        "raw": (beziers, seg_counts, closed, rgb, widths, layer_concat,
                layer_lens, title_b),
        "title_view": title_view,
        "total_segments": total_segments,
        "stroke_count": stroke_count,
        "title_len": len(title_b),
        "layer_concat_len": len(layer_concat),
    }


def _pdf_capacity_bound(stroke_count, total_segments, title_len, layer_concat_len):
    """The conservative capacity the Rust ``capacity_bound`` computes. Mirrored
    exactly here so the Python-owned buffer is always large enough and the
    one-pass call never returns ERR_SIZE for a valid input."""
    FLOAT_MAX = 24
    content = stroke_count * (3 * FLOAT_MAX + 8 + FLOAT_MAX + 2
                              + 2 * FLOAT_MAX + 2 + 2 + 40)
    content += total_segments * (6 * FLOAT_MAX + 2 + 1)
    n_layers_upper = stroke_count + 1
    objects = 2048 + n_layers_upper * 128 + (6 + n_layers_upper) * 32
    strings = title_len * 2 + layer_concat_len * 2
    return content + objects + strings


def _valid_pdf_bytes(data) -> bool:
    """Validate the PDF framing and every xref target before accepting native output."""
    try:
        raw = bytes(data)
        if not raw.startswith(b"%PDF-1.4\n") or not raw.endswith(b"%%EOF\n"):
            return False
        marker = raw.rfind(b"startxref\n")
        if marker < 0:
            return False
        end = raw.find(b"\n", marker + len(b"startxref\n"))
        xref_at = int(raw[marker + len(b"startxref\n"):end])
        if raw[xref_at:xref_at + 5] != b"xref\n":
            return False
        lines = raw[xref_at:].splitlines()
        first, count = map(int, lines[1].split())
        if first != 0 or len(lines) < 2 + count:
            return False
        for obj_num in range(1, count):
            entry = lines[2 + obj_num]
            if len(entry) != 19 or entry[17:18] != b"n":
                return False
            off = int(entry[:10])
            if not raw.startswith(("%d 0 obj" % obj_num).encode("ascii"), off):
                return False
        return True
    except (TypeError, ValueError, IndexError):
        return False


def _native_pdf_encode_into(strokes, page_w_mm, page_h_mm, title):
    """One-pass native PDF encode into a Python-owned ``bytearray`` (Q117-07).

    Returns ``(bytearray, written)`` on success, or ``None`` to fall back to
    the unchanged Python serializer (library unavailable, it predates the
    one-pass export, the input is rejected, or the capacity is insufficient).
    The ``bytearray`` is caller-owned and may be written directly to a file
    without an intermediate ``bytes`` copy. ``written`` is the exact byte count
    the encoder published on complete success.
    """
    global _native_pdf_calls
    enc_into = _pdf_encode_into_binding()
    if enc_into is None:
        return None  # library predates the one-pass export
    packed = _pack_pdf(strokes, page_w_mm, page_h_mm, title)
    if packed is None:
        return None
    (bez_view, seg_view, closed_view, rgb_view, widths_view,
     layer_view, layer_lens_view) = packed["views"]
    _raw = packed["raw"]  # keep the packed arrays alive for the FFI call
    title_view = packed["title_view"]
    total_segments = packed["total_segments"]
    stroke_count = packed["stroke_count"]
    title_len = packed["title_len"]
    layer_concat_len = packed["layer_concat_len"]
    derived = _pdf_capacity_bound(stroke_count, total_segments,
                                  title_len, layer_concat_len)
    if derived > _MAX_NATIVE_OUTPUT_BYTES or derived > sys.maxsize:
        return None
    out = bytearray(derived)
    out_view = (ctypes.c_ubyte * derived).from_buffer(out)
    written = ctypes.c_int64(0)
    try:
        rc = enc_into(
            bez_view, total_segments,
            seg_view, closed_view,
            rgb_view, widths_view,
            layer_view, layer_lens_view,
            title_view, title_len,
            page_w_mm, page_h_mm,
            stroke_count,
            out_view, derived, ctypes.byref(written),
        )
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None
    if rc != 0:
        return None
    w = written.value
    if (not isinstance(w, int) or not (0 <= w <= derived)
            or not _valid_pdf_bytes(memoryview(out)[:w])):
        return None
    # A fully successful native encode: record the route activation.
    _native_pdf_calls += 1
    return out, w


def _pdf_native_active(strokes):
    """Whether the one-pass native PDF route should handle *strokes* (Q117-07).

    True only when the one-pass export is available AND the input is at or
    above the measured crossover (where native is >=10% faster than the Python
    writer). Below the crossover the Python writer stays active, so small
    workloads never regress.
    """
    if _pdf_encode_into_binding() is None:
        return False
    if _PDF_NATIVE_MIN_STROKES <= 0:
        return True
    try:
        return len(strokes) >= _PDF_NATIVE_MIN_STROKES
    except Exception:  # noqa: BLE001 -- treat as "not active"
        return False


def encode_pdf(strokes, page_w_mm, page_h_mm, title="") -> Optional[bytes]:
    """Encode *strokes* to a complete single-page PDF 1.4's bytes, or ``None``.

    ``strokes`` is a sequence of ``vector_export.Stroke`` exactly as
    ``vector_export.write_pdf`` takes it. ``None`` means "use the unchanged
    Python serializer" -- the library is unavailable, it predates the one-pass
    export, the ABI is wrong, the input is rejected, or the capacity is
    insufficient. A non-``None`` return is the exact file bytes and equals the
    Python serializer's output for the same input (byte-for-byte, including
    layers, xref, and escaping).
    """
    result = _native_pdf_encode_into(strokes, page_w_mm, page_h_mm, title)
    if result is None:
        return None
    out, written = result
    return bytes(memoryview(out)[:written])


# Q117-08 measured crossover (complete write including packing, FFI and I/O):
# 1/10 strokes are slower, while 1,000 strokes is 10.7-18.7% faster and the
# margin grows to 27.9% at 50,000. Keep small documents on Python.
_SVG_VECTOR_NATIVE_MIN_STROKES = 1000


def _svg_vector_capacity_bound(stroke_count, total_segments, layer_concat_len):
    return (2048 + 256 * stroke_count + (8 * 32 + 32) * total_segments
            + 6 * layer_concat_len)


def _native_svg_encode_into(strokes, page_w_mm, page_h_mm):
    """One-pass native SVG bytes in caller-owned storage, or ``None``."""
    global _native_svg_vector_calls
    enc_into = _svg_encode_into_binding()
    if enc_into is None:
        return None
    packed = _pack_pdf(strokes, page_w_mm, page_h_mm, "",
                       _layer_encoding="utf-8")
    if packed is None:
        return None
    (bez_view, seg_view, closed_view, rgb_view, widths_view,
     layer_view, layer_lens_view) = packed["views"]
    _raw = packed["raw"]
    total_segments = packed["total_segments"]
    stroke_count = packed["stroke_count"]
    derived = _svg_vector_capacity_bound(
        stroke_count, total_segments, packed["layer_concat_len"])
    if derived > _MAX_NATIVE_OUTPUT_BYTES or derived > sys.maxsize:
        return None
    out = bytearray(derived)
    out_view = (ctypes.c_ubyte * derived).from_buffer(out)
    written = ctypes.c_int64(0)
    try:
        rc = enc_into(
            bez_view, total_segments, seg_view, closed_view,
            rgb_view, widths_view, layer_view, layer_lens_view,
            page_w_mm, page_h_mm, stroke_count,
            out_view, derived, ctypes.byref(written),
        )
    except Exception:  # noqa: BLE001 -- preserve the Python fallback
        return None
    w = written.value
    if rc != 0 or not isinstance(w, int) or not (0 <= w <= derived):
        return None
    try:
        memoryview(out)[:w].tobytes().decode("utf-8")
    except UnicodeDecodeError:
        return None
    _native_svg_vector_calls += 1
    return out, w


def _svg_vector_native_active(strokes):
    if _svg_encode_into_binding() is None:
        return False
    try:
        return len(strokes) >= _SVG_VECTOR_NATIVE_MIN_STROKES
    except Exception:  # noqa: BLE001
        return False


def encode_svg(strokes, page_w_mm, page_h_mm) -> Optional[bytes]:
    result = _native_svg_encode_into(strokes, page_w_mm, page_h_mm)
    if result is None:
        return None
    out, written = result
    return bytes(memoryview(out)[:written])
