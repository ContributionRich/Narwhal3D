"""REQUIRED Rust Bezier endpoint-derivative solver (`blend.blend_bezier_points`).

Every supported blend order (all 25 `(k0, k1)` pairs, each end at most G4) is
solved by the pure-core library's
`narwhal_pure_core_blend_bezier_points`: the SAME forward / backward Bezier
end-derivative algebra the retired Python reference used, repeated bit for bit
(measured on Linux/glibc; the Windows and macOS runtimes are unmeasured). It is
a pure numeric accelerator: it never touches openNURBS, never samples / meshes /
approximates, and changes no document geometry -- it only solves for the
control points from already-extracted finite endpoint derivative values.

Error contract: the binding is REQUIRED. `available()` raises `NativeBlendError`
when no usable library can be loaded (missing, wrong ABI, missing symbol) --
it never returns a silent "unavailable". `blend_bezier_points` RAISES
`NativeBlendError` when the loaded library rejects the inputs or fails, so a
native execution failure is never silently replayed through Python (the
endpoint solver no longer has a Python implementation at all). The Python
caller only ever passes validated, finite inputs within the supported degree,
so a rejection here is a real defect (a stale / mismatched library), not a
compatibility fallback.

ABI gate: the library must export `narwhal_pure_core_abi_version()` returning 1
AND `narwhal_pure_core_blend_bezier_points`. A library without either is
refused, not loaded. The packaged copy next to this file is used by default;
the `NARWHAL_PURE_CORE` environment variable selects a specific file (the
test / gate path) instead.
"""
from __future__ import annotations

import ctypes
import math
import os
import sys
import threading
from pathlib import Path
from typing import NamedTuple

_ENV = "NARWHAL_PURE_CORE"
_lock = threading.Lock()


class _Record(NamedTuple):
    """One resolved binding: the signature tuple, the loaded CDLL, and the fn.

    Immutable. The record is the single source of truth for a successful load, so
    the CDLL it holds is retained across every later call (a binding switch never
    drops it), and there is no separate signature/binding pair to fall out of
    sync. There is exactly one record (not an unbounded per-signature cache), and
    a failed resolution never stores a record -- the next call retries, it is not
    cached as a failure.
    """
    signature: tuple[int, str | None]   # ('env', exact) | ('packaged', None)
    library: ctypes.CDLL               # retained CDLL (never dropped on a switch)
    fn: object                         # the bound narwhal_pure_core_blend_bezier_points


#: the single cached record (None until a successful resolution)
_record: _Record | None = None
#: the fixed maximum output buffer (10 control points x xyz = 30 doubles)
_MAX_OUT = 30
#: the highest derivative matched at a single end (G4)
_MAX_K = 4
#: the required ABI version
_ABI_VERSION = 1


class NativeBlendError(RuntimeError):
    """The native blend binding is missing, refused, rejected the inputs, or failed to run."""


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


def _blend_binding():
    """The immutable `_Record` for the current binding (the single cached one).

    Returns the WHOLE record (signature, CDLL, fn) -- not a bare function -- so the
    caller retains the record throughout the operation: the CDLL and fn stay together
    for the call, and a binding switch replaces the whole record rather than leaving
    a dangling fn. Fast path: a cached record is returned WITHOUT touching the lock
    or any file path (the warm cache is consulted before any Path / lock work). There
    is no per-signature cache to grow without bound.

    Raises `NativeBlendError` when no usable library can be loaded, because the
    solver is REQUIRED (there is no Python implementation to fall back to). A
    failed resolution stores NO record, so the next call retries rather than
    replaying a cached failure; it is never a silent None.

    The signature is a tuple, not a string: `('env', exact)` when the
    `NARWHAL_PURE_CORE` environment variable is set (even to the empty string),
    else `('packaged', None)`. This means an empty env is refused -- it can never
    silently collide with the packaged default -- and the literal string
    `"<packaged>"` set as an env value is a distinct, refused signature, not the
    packaged library. Follows the env variable when set (the test / gate path),
    otherwise the packaged copy next to this file. Only a library that
    (a) exports `narwhal_pure_core_abi_version` returning 1 and
    (b) exports `narwhal_pure_core_blend_bezier_points` is used, so a stale
    build without either symbol is refused rather than loaded.
    """
    global _record
    # signature is cheap (env read only, no Path / lock): computed before the
    # warm check so a cached hit never touches a file path or the lock.
    explicit = os.environ.get(_ENV)
    if explicit is None:
        signature = ("packaged", None)
    else:
        # set but empty (or any exact value) is a real signature, refused if
        # unusable -- it is NEVER collapsed onto the packaged default.
        signature = ("env", explicit)
    rec = _record
    if rec is not None and rec.signature == signature:
        # warm cache: served before any Path / lock work, and only for a
        # matching signature -- a different env value is a different binding.
        return rec
    with _lock:
        rec = _record
        if rec is not None and rec.signature == signature:
            return rec
        if explicit is None:
            candidates = (Path(__file__).resolve().parent / "native" / _library_name(),)
        else:
            candidates = (Path(explicit).expanduser(),)
        for path in candidates:
            if not path.is_file():
                continue
            try:
                lib = ctypes.CDLL(str(path))
            except OSError:
                continue
            # ABI version gate: must exist and return 1
            try:
                ver_fn = lib.narwhal_pure_core_abi_version
                ver_fn.argtypes = []
                ver_fn.restype = ctypes.c_uint32
                if int(ver_fn()) != _ABI_VERSION:
                    continue
            except AttributeError:
                continue
            # blend function must exist
            try:
                fn = lib.narwhal_pure_core_blend_bezier_points
            except AttributeError:
                continue
            fn.argtypes = [
                ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                ctypes.c_uint32, ctypes.c_uint32,
                ctypes.c_double, ctypes.c_double,
                ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
            ]
            fn.restype = ctypes.c_int32
            rec = _Record(signature, lib, fn)
            _record = rec
            return rec
        raise NativeBlendError(
            f"no usable pure-core blend library under signature {signature!r} "
            f"(candidates tried: {', '.join(str(c) for c in candidates)}); "
            "the endpoint solver is native-only and cannot run without it")


def available():
    """True when a usable native blend binding is loaded (REQUIRED: raises otherwise)."""
    _blend_binding()
    return True


def _doubles(values):
    buf = (ctypes.c_double * len(values))()
    for i, v in enumerate(values):
        buf[i] = v
    return buf


def _native(p0, p1, k0, k1, lam0, lam1):
    """The native call, retaining the binding record for the whole operation.

    Raises `NativeBlendError` (a `RuntimeError`, never a bare `ctypes.ArgumentError`
    / `ValueError` / other) when the native invocation fails, when it reports a
    non-zero status, or when a SUCCESS result contains a non-finite control
    component. The finite guard is checked against the RETURNED control points, so
    a library that reports success (0) while writing NaN / inf is refused -- it is
    never returned as geometry, and no count is reported for it.
    """
    rec = _blend_binding()        # the immutable record, retained throughout
    fn = rec.fn
    try:
        p0c = _doubles(p0)
        p1c = _doubles(p1)
        out = (ctypes.c_double * _MAX_OUT)()
        code = fn(p0c, len(p0), p1c, len(p1),
                  k0, k1, lam0, lam1, out, _MAX_OUT)
    except Exception as exc:
        # the native invocation itself failed (a bad ctypes conversion raises
        # ctypes.ArgumentError, a direct Exception subclass -- NOT OSError/ValueError;
        # a malformed buffer can raise ValueError). Wrap it by name: the solver is
        # native-only, so this is an explicit NativeBlendError, never a bare raise.
        raise NativeBlendError(f"native blend invocation failed: {exc!r}") from exc
    if code != 0:
        raise NativeBlendError(
            f"native blend rejected validated inputs (status {code}); "
            "refusing to replay through Python")
    points = [(out[i * 3], out[i * 3 + 1], out[i * 3 + 2]) for i in range(k0 + k1 + 2)]
    for (x, y, z) in points:
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            raise NativeBlendError(
                "native blend reported success with a non-finite control component; "
                "refusing to return non-finite geometry")
    return points


def blend_bezier_points(p0, p1, k0, k1, lam0, lam1):
    """The native control points (REQUIRED: no usable binding raises).

    ``p0`` / ``p1`` are flat lists of doubles, ``3 * (k+1)`` long each (``xyz``
    per derivative). Returns a list of ``(x, y, z)`` tuples (``k0 + k1 + 2``
    points) on success. Raises `NativeBlendError` when the binding is missing /
    refused or the library fails the call -- there is no Python implementation
    to replay through.
    """
    # Safe count validation: reject negative, non-int, or > MAX_K before passing to ctypes
    for k in (k0, k1):
        if not isinstance(k, int) or isinstance(k, bool):
            raise NativeBlendError(
                f"unsupported blend order k={k!r}; only integer orders 0..4 (G0..G4) are supported")
        if k < 0 or k > _MAX_K:
            raise NativeBlendError(
                f"unsupported blend order k={k!r}; only integer orders 0..4 (G0..G4) are supported")
    if not (len(p0) == 3 * (k0 + 1) and len(p1) == 3 * (k1 + 1)):
        raise NativeBlendError(
            f"derivative spans must be 3*(k+1) doubles each "
            f"(got {len(p0)} for k0={k0} and {len(p1)} for k1={k1})")
    # _native already raises NativeBlendError for a failed / rejected / non-finite
    # result, so any exception leaking here (a vanished binding, a ctypes
    # conversion) is wrapped by name -- the solver is native-only, no replay.
    try:
        return _native(p0, p1, k0, k1, float(lam0), float(lam1))
    except Exception as exc:
        if isinstance(exc, NativeBlendError):
            raise
        raise NativeBlendError(f"native blend binding failed: {exc!r}") from exc
