"""Checked native macro-planner ABI (RUST-PLAN-12).

Typed FFI adapter over ``narwhal_pure_core_macro_plan_checked`` in
``native/pure_core``. It owns no document state: it hands the macro string to
Rust, reads the caller-owned output, and maps the stable status codes to
typed host errors. Missing, stale, or refused native execution is an explicit
``RuntimeError`` -- there is never a Python planner replay.

Status codes (the return value of the native call):
* ``0``  success -- ``out`` holds the action-list JSON;
* ``1``  short buffer -- no output written; ``*out_needed`` carries the
  required length, so the caller retries with a bounded buffer;
* ``-1`` bad span (a required pointer is null for the given length);
* ``-2`` invalid input (oversized, or not valid UTF-8);
* ``-4`` non-nestable command -- ``out`` holds the command name as a JSON
  string (no array);
* ``-5`` invalid option name -- no output is written.

A capacity-0 + null-output call is a pure size query: it returns ``1`` (or the
stable error code) with ``*out_needed`` set and writes nothing, so a single
export serves both passes.
"""
from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

__all__ = ["plan_macro", "NativeMacroPlanError", "available"]

_ENV = "NARWHAL_PURE_CORE"
_REQUIRED_ABI_VERSION = 1

#: Hard ceiling for the success payload (bounded derived size: the input is
#: at most 1 MiB and every step renders to JSON at most linearly larger).
_OUTPUT_MAX_BYTES = 64 * 1024 * 1024

#: First read of either payload (success JSON or the non-nestable command
#: name) is capped at 4096 bytes; one bounded retry at the size-query
#: length (capped at :data:`_OUTPUT_MAX_BYTES`) follows when the first read
#: is too small.
_FIRST_READ_BYTES = 4096

#: The native planner refuses input above 1 MiB (``MACRO_MAX_INPUT_BYTES``).
_INPUT_MAX_BYTES = 1 << 20

_lock = threading.Lock()
_signature = None
_checked = None
_native_macro_plan_calls = 0


class NativeMacroPlanError(RuntimeError):
    """Internal boundary error for a semantic planner refusal.

    ``code`` is the stable native status (``-4`` non-nestable command, ``-5``
    invalid option name); ``name`` is the JSON-decoded command name for
    ``-4`` and ``None`` otherwise. The candidate module maps this to the
    public ``NonNestableCommandError`` / ``MacroPlanError`` contract; the
    live string path must never replay Python on it.
    """

    def __init__(self, code, name=None):
        self.code = code
        self.name = name
        super().__init__("native macro planner refusal code %d" % code)


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


def _checked_binding():
    """Resolve ``narwhal_pure_core_macro_plan_checked``, or ``None``.

    The library is loaded only from the explicit ``NARWHAL_PURE_CORE`` path
    or the adjacent packaged location (the same conservative discovery as
    the other pure-core adapters). A library that lacks the export predates
    RUST-PLAN-12 and is stale for this route.
    """
    global _signature, _checked
    explicit = os.environ.get(_ENV)
    signature = explicit or "<packaged>"
    if _signature == signature:
        return _checked
    with _lock:
        if _signature == signature:
            return _checked
        _signature, _checked = signature, None
        packaged = Path(__file__).resolve().parent / "native" / _library_name()
        candidates = (Path(explicit).expanduser(),) if explicit else (packaged,)
        for path in candidates:
            if not path.is_file():
                continue
            try:
                lib = ctypes.CDLL(str(path))
                fn = lib.narwhal_pure_core_macro_plan_checked
            except (AttributeError, OSError):
                continue
            try:
                if lib.narwhal_pure_core_abi_version() != _REQUIRED_ABI_VERSION:
                    # A stale library must not serve this route: refusing is
                    # explicit rather than silently replaying Python.
                    continue
            except AttributeError:
                continue
            fn.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                           ctypes.POINTER(ctypes.c_int64)]
            fn.restype = ctypes.c_int32
            _checked = fn
            return fn
        return None


def available():
    """True when the checked planner export can be resolved and is current."""
    return _checked_binding() is not None


def _shared_validator():
    """The package's shared strict validator, or ``None`` if unloadable.

    Works in package context and under the file-path loader (the same
    fallback ``macro.py`` uses); ``native_pure.py`` is stdlib-only.
    """
    if __package__:
        from . import native_pure as _np
        return _np
    name = "narwhal3d_native_pure"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).with_name("native_pure.py")
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _valid_plan_payload(payload):
    shared = _shared_validator()
    if shared is None:
        raise RuntimeError("Required native planner response validator is unavailable")
    return shared._valid_macro_plan_json(payload)


def _decode_error_name(raw):
    """Decode the non-nestable command name from a bounded native payload.

    The payload is a JSON string (via the shared ``json_escape``) and must be
    a non-empty string; any other shape is a boundary violation.
    """
    try:
        name = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        name = None
    if not isinstance(name, str) or not name:
        name = None
    return name


def _native_call(fn, data, out_len):
    """One native call. Returns ``(status, buf, needed)``.

    ``buf`` is the caller-owned output (empty on the size-query pass);
    ``needed`` is the ``*out_needed`` value the native side set.
    """
    global _native_macro_plan_calls
    with _lock:
        _native_macro_plan_calls += 1
    needed = ctypes.c_int64(-1)
    if out_len == 0:
        status = fn(data, len(data), None, 0, ctypes.byref(needed))
        return status, b"", needed.value
    buf = (ctypes.c_ubyte * out_len)()
    status = fn(data, len(data), buf, out_len, ctypes.byref(needed))
    return status, bytes(buf), needed.value


def _plan_checked(text):
    """One bounded read and at most one exact-sized retry; never replay Python."""
    fn = _checked_binding()
    if fn is None:
        raise RuntimeError("native macro planner unavailable or stale; no Python fallback")
    try:
        data = text.encode("utf-8")
    except UnicodeError as error:
        raise RuntimeError("native macro planner requires valid UTF-8") from error
    if len(data) > _INPUT_MAX_BYTES:
        raise RuntimeError("native macro planner input exceeds the 1 MiB limit")
    bound = min(_OUTPUT_MAX_BYTES, 1024 + 128 * len(data))
    capacity = min(_FIRST_READ_BYTES, bound)
    try:
        status, buf, needed = _native_call(fn, data, capacity)
        if status == 1:
            if not capacity < needed <= bound:
                raise RuntimeError("invalid native planner size report")
            capacity = needed
            status, buf, needed = _native_call(fn, data, capacity)
            if needed != capacity:
                raise RuntimeError("inconsistent native planner retry size")
    except (OSError, ctypes.ArgumentError) as error:
        raise RuntimeError("native macro planner call failed; no Python fallback") from error
    if status == -5:
        if needed != 0:
            raise RuntimeError("invalid native planner empty-option refusal")
        return -5, None, None
    if status not in (0, -4) or not 0 < needed <= capacity:
        raise RuntimeError("invalid native macro planner reply")
    payload = buf[:needed]
    if status == 0:
        if not _valid_plan_payload(payload):
            raise RuntimeError("native macro planner returned an invalid action list")
        return 0, payload, None
    name = _decode_error_name(payload)
    if name is None:
        raise RuntimeError("invalid native macro planner non-nestable name")
    return -4, None, name


def plan_macro(text):
    """Plan ``text`` natively; raises a typed error for every refusal.

    Returns the decoded action list (``prefixes`` stay JSON lists -- the
    candidate module converts them to tuples). Raises:

    * ``NativeMacroPlanError(-4, name)`` -- non-nestable transparent command;
    * ``NativeMacroPlanError(-5)`` -- option action with an empty name;
    * ``RuntimeError`` -- missing/stale library, invalid or oversized input,
      or any unclassifiable boundary condition.
    """
    if not isinstance(text, str):
        raise RuntimeError(
            "native macro planner takes a string; got %s"
            % type(text).__name__)
    status, payload, name = _plan_checked(text)
    if status == 0:
        return json.loads(payload)
    if status == -4:
        raise NativeMacroPlanError(-4, name)
    if status == -5:
        raise NativeMacroPlanError(-5)
    raise RuntimeError(
        "native macro planner returned status %d; no Python fallback" % status)


def _reset_for_tests():
    """Forget the resolved binding (tests re-point ``NARWHAL_PURE_CORE``)."""
    global _signature, _checked, _native_macro_plan_calls
    with _lock:
        _signature, _checked, _native_macro_plan_calls = None, None, 0
