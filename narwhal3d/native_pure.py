"""Checked native implementations for selected pure parsing/interchange workloads.

This is a typed FFI adapter over the ``native/pure_core`` C ABI (see
``native/pure_core/README.md``). It owns no document geometry and never lets a
Rust-owned pointer cross the call. Python marshals caller-owned buffers and
validates native output before returning host objects. Optional parser/decoder
routes may decline to their existing caller fallback; required format encoders
live in separate adapters, not here.

Discovery is deliberately conservative and matches ``native_selection.py``:
the library is loaded only from the explicit ``NARWHAL_PURE_CORE`` path, or
from the adjacent packaged location. There is no other auto-discovery and no binary is enabled by
assumption. Any discovery, ABI, input, or call failure returns ``None`` so the
caller runs the unchanged Python implementation.

Native and Python results are byte/structure-identical. Production modules activate
only routes that win end-to-end after packing and conversion; slower direct APIs remain
available for parity tests and future ABI optimization, never as user-facing regressions.

STL codecs and OBJ/DXF/vector encoding use separate required adapters.
This module retains optional OBJ decoding; missing exports affect only
that format and operation.
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
_native_decode_obj_calls = 0

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
_MAX_OBJ_MESHES = 1_000_000
_MAX_OBJ_VERTICES = 5_000_000
_MAX_OBJ_FACES = 5_000_000
_MAX_OBJ_FACE_INDICES = 20_000_000
_MAX_NAME_BYTES = 64 * 1024 * 1024
_OBJ_IMPORT_NATIVE_MIN_BYTES = _MAX_INTERCHANGE_INPUT_BYTES + 1


def _obj_import_native_active(data, *, force=False):
    """Whether Import may consume validated OBJ buffers without tuple expansion."""
    return (isinstance(data, bytes)
            and (force or len(data) >= _OBJ_IMPORT_NATIVE_MIN_BYTES))


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
                    or not isinstance(step["value"], str)):
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
    """Return optional OBJ decode handles and path, or ``None``.

    The three slots are ``(obj_decode_size, obj_decode, path)``.
    Missing exports affect only that format/operation. Retired STL/OBJ/DXF/vector
    encoders are not prerequisites.
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
            except (AttributeError, OSError) as exc:
                _status = f"native pure-core load failed: {exc}"
                continue
            # OBJ exports are optional.
            obj_dsize = obj_dec = None
            try:
                obj_dsize = library.narwhal_pure_core_obj_decode_size
                obj_dsize.argtypes = [_ByteP, ctypes.c_size_t]
                obj_dsize.restype = ctypes.c_int64
                obj_dec = library.narwhal_pure_core_obj_decode
                obj_dec.argtypes = [_ByteP, ctypes.c_size_t, _ByteP, ctypes.c_size_t]
                obj_dec.restype = ctypes.c_int32
            except (AttributeError, OSError):
                pass  # OBJ decode exports absent: decode falls back
            _native_binding = (obj_dsize, obj_dec, path)
            _status = f"ready: {path}"
            return _native_binding
        return None


def available() -> bool:
    """Whether an ABI-compatible shared OBJ decode library is available."""
    return _binding() is not None


def backend_status() -> str:
    """A diagnostic status; absence is not a feature failure (Python fallback)."""
    _binding()
    return _status


# ---------------------------------------------------------------------------
# CPlane-aware Grid Snap. Rust owns frame validation, projection, rounding and
# world reconstruction. Python only marshals five fixed caller-owned arrays and
# returns ``None`` on unavailable/old/malformed/error paths; there is deliberately
# no Python geometry fallback here.
# ---------------------------------------------------------------------------

_gs_signature: str | None = None
_gs_binding: Any = None
_gs3_signature: str | None = None
_gs3_binding: Any = None
_native_gridsnap_calls = 0
_native_gridsnap3_calls = 0


def _gridsnap_binding():
    """Return the optional ABI-1 Grid Snap export, or ``None``."""
    global _gs_signature, _gs_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _gs_signature == signature:
        return _gs_binding
    with _lock:
        if _gs_signature == signature:
            return _gs_binding
        _gs_signature = signature
        _gs_binding = None
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
                function = library.narwhal_pure_core_grid_snap
                function.argtypes = [
                    _DoubleP, _DoubleP, _DoubleP, _DoubleP, _DoubleP,
                    ctypes.c_double, _DoubleP,
                ]
                function.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _gs_binding = function
            return function
        return None


def _gridsnap3_binding():
    """Return the additive ABI-1 three-dimensional Grid Snap export, or ``None``."""
    global _gs3_signature, _gs3_binding
    explicit = os.environ.get(_ENV_PATH)
    signature = explicit or "<packaged>"
    if _gs3_signature == signature:
        return _gs3_binding
    with _lock:
        if _gs3_signature == signature:
            return _gs3_binding
        _gs3_signature = signature
        _gs3_binding = None
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
                function = library.narwhal_pure_core_grid_snap_3d
                function.argtypes = [
                    _DoubleP, _DoubleP, _DoubleP, _DoubleP, _DoubleP,
                    ctypes.c_double, _DoubleP,
                ]
                function.restype = ctypes.c_int32
            except (AttributeError, OSError):
                continue
            _gs3_binding = function
            return function
        return None


def _fixed_vec3(values):
    """Marshal exactly three values without performing geometry computation."""
    try:
        values = tuple(values)
        if len(values) != 3:
            return None
        return (ctypes.c_double * 3)(*(float(value) for value in values))
    except (TypeError, ValueError, OverflowError):
        return None


def grid_snap(point, origin, x_axis, y_axis, z_axis, spacing):
    """Return the Rust-computed snapped world point, or ``None``.

    OURS: because retained reference help does not specify exact half-spacing ties, the Rust
    contract rounds them symmetrically away from zero.
    """
    global _native_gridsnap_calls
    function = _gridsnap_binding()
    if function is None:
        return None
    arrays = tuple(_fixed_vec3(values) for values in
                   (point, origin, x_axis, y_axis, z_axis))
    if any(values is None for values in arrays):
        return None
    try:
        spacing = float(spacing)
        output = (ctypes.c_double * 3)()
        code = function(*arrays, spacing, output)
    except (ctypes.ArgumentError, TypeError, ValueError, OverflowError, OSError):
        return None
    result = tuple(output)
    if code != 0 or not all(math.isfinite(value) for value in result):
        return None
    _native_gridsnap_calls += 1
    return result


def grid_snap_3d(point, origin, x_axis, y_axis, z_axis, spacing):
    """Return a Rust-snapped U/V/W lattice point, or ``None``."""
    global _native_gridsnap3_calls
    function = _gridsnap3_binding()
    if function is None:
        return None
    arrays = tuple(_fixed_vec3(values) for values in
                   (point, origin, x_axis, y_axis, z_axis))
    if any(values is None for values in arrays):
        return None
    try:
        spacing = float(spacing)
        output = (ctypes.c_double * 3)()
        code = function(*arrays, spacing, output)
    except (ctypes.ArgumentError, TypeError, ValueError, OverflowError, OSError):
        return None
    result = tuple(output)
    if code != 0 or not all(math.isfinite(value) for value in result):
        return None
    _native_gridsnap3_calls += 1
    return result



# Required number/length parser is independent of the older bulk I/O bindings.
_native_numparse_calls = 0


def _text_bytes(text):
    """Encode *text* to UTF-8 ``bytes`` for a ``c_char_p`` input slot.

    ``c_char_p`` takes a ``bytes`` (or ``None`` for an empty input) and passes
    its length via the adjacent ``c_size_t``; no per-call ctypes array is built,
    which keeps the short-input path as fast as the Python regex.
    """
    b = str(text).encode("utf-8")
    return (b, len(b)) if b else (None, 0)



class ParseReply(ctypes.Structure):
    _fields_ = [('code', ctypes.c_int32), ('reserved', ctypes.c_uint32),
                ('value', ctypes.c_double), ('start', ctypes.c_uint32), ('end', ctypes.c_uint32)]


_owned_parse_binding = None
_owned_parse_signature = None


def _required_parse_binding():
    """Mandatory additive parser ABI; never treat a broken override as absent."""
    global _owned_parse_binding, _owned_parse_signature
    configured = os.environ.get(_ENV_PATH)
    signature = configured if configured is not None else '<packaged>'
    if _owned_parse_binding is not None and signature == _owned_parse_signature and configured != '':
        return _owned_parse_binding
    path = Path(configured).expanduser() if configured else _PACKAGED_LIBRARY
    try:
        if configured == '' or not path.is_file():
            raise OSError('native parser library is missing')
        lib = ctypes.CDLL(str(path))
        abi = lib.narwhal_pure_core_parse_abi
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        if abi() != 2:
            raise RuntimeError('number parser ABI 2 required')
        parse = lib.narwhal_pure_core_parse_v2
        parse.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                          ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32,
                          ctypes.POINTER(ParseReply), ctypes.c_size_t]
        parse.restype = ctypes.c_uint32
    except (OSError, AttributeError, RuntimeError) as exc:
        raise RuntimeError('Required Rust number parser unavailable; build or reinstall the current pure core') from exc
    _owned_parse_binding, _owned_parse_signature = parse, signature
    return parse


def parse_owned(text, model_unit=None):
    """One native parse, including errors; Python only marshals and formats repr."""
    import math
    import unicodedata
    unicode = {'15.1.0': 151, '16.0.0': 160}.get(unicodedata.unidata_version)
    if unicode is None:
        raise RuntimeError('Unsupported Unicode database for Rust number parsing')
    parse = _required_parse_binding()
    if len(text) > 4096 or (model_unit is not None and len(model_unit) > 4096):
        # Admission bound only; do not allocate an unbounded ABI buffer.
        return None, 'number parser input exceeds 4096 characters'
    encoding = 'utf-32-le' if sys.byteorder == 'little' else 'utf-32-be'
    def buffer(value):
        return (ctypes.c_uint32 * len(value)).from_buffer_copy(value.encode(encoding, 'surrogatepass'))
    text_buffer = buffer(text)
    model_buffer = buffer(model_unit or '')
    # An unwritten reply must never resemble a valid zero-valued success.
    out = ParseReply(code=2147483647)
    rc = parse(text_buffer, len(text), model_buffer, len(model_unit or ''),
               int(model_unit is not None), unicode, ctypes.byref(out), ctypes.sizeof(out))
    if (rc != 0 or out.reserved != 0 or out.code not in (0, -1, -2, -3, -4, -5, -7, -8)
            or not 0 <= out.start <= out.end <= len(text) or not math.isfinite(out.value)
            or (out.code != 0 and out.value != 0.0)
            or (model_unit is None and out.code in (-3, -4, -5))
            or (out.code == 0 and (out.start != 0 or out.end != 0))):
        raise RuntimeError('Invalid Rust number-parser reply; not replayed')
    global _native_numparse_calls
    _native_numparse_calls += 1
    if out.code == 0:
        return out.value, None
    if out.code == -1:
        return None, 'no spaces are permitted in a number'
    if out.code == -5:
        return None, 'unknown model unit: %r' % (model_unit,)
    if out.code == -7:
        return None, 'number parser input exceeds 4096 characters'
    label = {-2: 'not a number', -3: 'not a length', -4: 'unknown unit suffix',
             -8: 'number outside finite range'}[out.code]
    return None, '%s: %r' % (label, text[out.start:out.end])


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
    """Resolve the required native macro ABI, or None for explicit host refusal."""
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
    """Return checked native JSON or None; the host refuses, never reparses."""
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
    except Exception:  # noqa: BLE001 -- host turns refusal into an explicit error
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
# trap as Q117-02's ``_dxp_*``) and from the two-pass
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
    """Return checked native chain buffers or None for explicit host refusal.

    The required host never replays a Python solver; all nonempty supported
    inputs cross this ABI. Empty collections verify binding in the host.
    """
    global _native_joinchain_calls
    fn = _joinchain_binding()
    if fn is None:
        return None  # library predates the joinchain export
    n = len(starts) // 3
    if len(starts) != len(ends) or not (len(starts) % 3 == 0) or n == 0:
        return None
    if n > 1_000_000:
        return None  # host reports the explicit one-million-segment bound
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


# Packed macro tokenizer output (Q117-04): the state globals are ``_macropp_*``
# -- distinct from the ``_macropp_binding`` resolver (the same collision trap
# as Q117-02's ``_dxp_*`` / Q117-03's ``_svgpp_*``) and
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


def _decode_obj_raw(data: bytes) -> Optional[bytes]:
    """Run the checked native OBJ decoder and return its packed bytes."""
    binding = _binding()
    if binding is None:
        return None
    (dec_size_fn, dec_fn, _path) = binding
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
    global _native_decode_obj_calls
    global _owned_parse_signature, _owned_parse_binding, _native_numparse_calls
    global _c_signature, _c_binding, _native_calc_calls
    global _b_signature, _b_binding, _native_bearing_calls
    global _co_signature, _co_binding, _native_coords_calls
    global _m_signature, _m_binding, _native_macro_calls
    global _mp_signature, _mp_binding, _native_macro_plan_calls
    global _macropp_signature, _macropp_binding
    global _macroppp_signature, _macroppp_binding
    global _sv_signature, _sv_binding, _native_svgpath_calls
    global _svgpp_signature, _svgpp_binding
    global _jc_signature, _jc_binding, _native_joinchain_calls
    global _gs_signature, _gs_binding, _native_gridsnap_calls
    global _gs3_signature, _gs3_binding, _native_gridsnap3_calls
    with _lock:
        _signature = None
        _native_binding = None
        _status = "not checked"
        _native_decode_obj_calls = 0
        _gs_signature = None
        _gs_binding = None
        _native_gridsnap_calls = 0
        _gs3_signature = None
        _gs3_binding = None
        _native_gridsnap3_calls = 0
        _owned_parse_signature = None
        _owned_parse_binding = None
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
        _svgpp_signature = None
        _svgpp_binding = None
        _macropp_signature = None
        _macropp_binding = None
        _macroppp_signature = None
        _macroppp_binding = None
        _jc_signature = None
        _jc_binding = None
        _native_joinchain_calls = 0
        _cached_macro_json.cache_clear()
        _cached_macro_plan_json.cache_clear()
        _cached_svgpath_json.cache_clear()
