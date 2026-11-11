"""Optional Rust Bezier endpoint-derivative solver (`blend.blend_bezier_points`).

`blend.py` keeps its UNCHANGED Python loop as the fallback for a missing / stale
library and a platform without a built core. When the pure-core library is
loaded, `blend_bezier_points` runs the SAME forward / backward Bezier
end-derivative algebra in Rust (`narwhal_pure_core_blend_bezier_points`),
repeating the Python reference bit for bit (measured on Linux/glibc; the Windows
and macOS runtimes are unmeasured). It is a pure numeric accelerator: it never
touches openNURBS, never samples / meshes / approximates, and changes no document
geometry -- it only solves for the control points from already-extracted finite
endpoint derivative values.

Error contract: this module returns `None` when there is no usable binding
(the caller then uses its Python implementation); it RAISES `NativeBlendError`
when a loaded library's call rejects the (validated) inputs or fails, so a native
execution failure is never silently replayed through Python. The Python caller
only ever passes validated, finite inputs within the supported degree, so a
rejection here is a real defect (a stale / mismatched library), not a
compatibility fallback.

ABI gate: the library must export `narwhal_pure_core_abi_version()` returning 1
AND `narwhal_pure_core_blend_bezier_points`. A library without either is
refused, not loaded.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path

_ENV = "NARWHAL_PURE_CORE"
_lock = threading.Lock()
_signature = None
_binding = None
#: the fixed maximum output buffer (10 control points x xyz = 30 doubles)
_MAX_OUT = 30
#: the highest derivative matched at a single end (G4)
_MAX_K = 4
#: the required ABI version
_ABI_VERSION = 1


class NativeBlendError(RuntimeError):
    """A loaded native library rejected the inputs or failed to run."""


def _library_name():
    if sys.platform == "win32":
        return "narwhal_pure_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_pure_core.dylib"
    return "libnarwhal_pure_core.so"


def _blend_binding():
    """The bound C function, or ``None`` when no usable library is present.

    Follows the `NARWHAL_PURE_CORE` environment variable when set (the
    test / gate path), otherwise the packaged copy next to this file. Only a
    library that (a) exports `narwhal_pure_core_abi_version` returning 1 and
    (b) exports `narwhal_pure_core_blend_bezier_points` is used, so a stale
    build without either symbol is refused rather than loaded.
    """
    global _signature, _binding
    explicit = os.environ.get(_ENV)
    signature = explicit or "<packaged>"
    if _signature == signature:
        return _binding
    with _lock:
        if _signature == signature:
            return _binding
        _signature, _binding = signature, None
        packaged = Path(__file__).resolve().parent / "native" / _library_name()
        candidates = (Path(explicit).expanduser(),) if explicit else (packaged,)
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
            _binding = fn
            return fn
        return None


def available():
    """True when a usable native blend binding is loaded."""
    return _blend_binding() is not None


def _doubles(values):
    buf = (ctypes.c_double * len(values))()
    for i, v in enumerate(values):
        buf[i] = v
    return buf


def _native(p0, p1, k0, k1, lam0, lam1):
    """The native call; raises `NativeBlendError` on any non-zero status."""
    fn = _blend_binding()
    if fn is None:
        return None
    p0c = _doubles(p0)
    p1c = _doubles(p1)
    out = (ctypes.c_double * _MAX_OUT)()
    code = fn(p0c, len(p0), p1c, len(p1),
              k0, k1, lam0, lam1, out, _MAX_OUT)
    if code != 0:
        raise NativeBlendError(
            f"native blend rejected validated inputs (status {code}); "
            "refusing to replay through Python")
    return tuple((out[i * 3], out[i * 3 + 1], out[i * 3 + 2])
                 for i in range(k0 + k1 + 2))


def blend_bezier_points(p0, p1, k0, k1, lam0, lam1):
    """The native control points, or ``None`` when no usable binding is present.

    ``p0`` / ``p1`` are flat lists of doubles, ``3 * (k+1)`` long each (``xyz``
    per derivative). Returns a list of ``(x, y, z)`` tuples (``k0 + k1 + 2``
    points) on success, ``None`` to let the caller use its Python implementation,
    and RAISES `NativeBlendError` when a loaded library fails the call.
    """
    # Safe count validation: reject negative, non-int, or > MAX_K before passing to ctypes
    for k in (k0, k1):
        if not isinstance(k, int) or isinstance(k, bool):
            return None
        if k < 0 or k > _MAX_K:
            return None
    if not (len(p0) == 3 * (k0 + 1) and len(p1) == 3 * (k1 + 1)):
        return None
    try:
        return _native(p0, p1, k0, k1, float(lam0), float(lam1))
    except (OSError, AttributeError) as exc:
        # the library vanished or lost the symbol between `available()` and the
        # call -- a compatibility fallback, not a replayed execution failure
        raise NativeBlendError(f"native blend binding failed: {exc!r}") from exc
