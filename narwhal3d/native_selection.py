"""Optional Rust acceleration for display-only selection projection.

The native library never receives document geometry. It sees packed copies of a
viewport matrix and float32 display segments, and this module returns ordinary
screen-space tuples to the existing Python cache/index. Any discovery, ABI, input,
or call failure returns ``None`` so the caller can preserve the scalar path.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path
from typing import Any

_ABI_VERSION = 1
_ENV_PATH = "NARWHAL_SELECTION_CORE"
_DoubleP = ctypes.POINTER(ctypes.c_double)
_FloatP = ctypes.POINTER(ctypes.c_float)
_ByteP = ctypes.POINTER(ctypes.c_ubyte)
_lock = threading.Lock()
_signature: str | None = None
_native_binding: Any = None
_status = "not checked"


def _library_name() -> str:
    if sys.platform == "win32":
        return "narwhal_selection_core.dll"
    if sys.platform == "darwin":
        return "libnarwhal_selection_core.dylib"
    return "libnarwhal_selection_core.so"


_PACKAGED_LIBRARY = Path(__file__).resolve().parent / "native" / _library_name()


def _binding():
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
        _status = "native selection library unavailable"
        for path in candidates:
            if not path.is_file():
                continue
            try:
                library = ctypes.CDLL(str(path))
                version = library.narwhal_selection_core_abi_version
                version.argtypes = []
                version.restype = ctypes.c_uint32
                if version() != _ABI_VERSION:
                    _status = f"native selection ABI mismatch: {path}"
                    continue
                project = library.narwhal_project_points_f32
                project.argtypes = [
                    _DoubleP, _FloatP, ctypes.c_size_t,
                    ctypes.c_double, ctypes.c_double, _DoubleP, _ByteP,
                ]
                project.restype = ctypes.c_int
            except (AttributeError, OSError) as exc:
                _status = f"native selection load failed: {exc}"
                continue
            _native_binding = (library, project, path)
            _status = f"ready: {path}"
            return _native_binding
        return None


def available() -> bool:
    """Whether an ABI-compatible optional native projector is available."""
    return _binding() is not None


def backend_status() -> str:
    """A diagnostic status; acceleration absence is not a feature failure."""
    _binding()
    return _status


def project_segments(lines, matrix, width, height):
    """Project ``(segment, endpoint, xyz)`` display data, or return ``None``.

    A tuple (possibly empty) means the native call succeeded. ``None`` means the
    caller must use its unchanged Python projection route. Segments with either
    endpoint behind the eye plane are omitted, matching the current UI behavior.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    try:
        import numpy as np

        segments = np.ascontiguousarray(lines, dtype=np.float32)
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        width = float(width)
        height = float(height)
        if segments.ndim != 3 or segments.shape[1:] != (2, 3):
            _status = "native selection input rejected: segments must have shape (n, 2, 3)"
            return None
        if view.shape != (4, 4) or not np.isfinite(view).all():
            _status = "native selection input rejected: matrix must be finite 4x4"
            return None
        if not np.isfinite((width, height)).all() or width < 0.0 or height < 0.0:
            _status = "native selection input rejected: invalid region dimensions"
            return None
        count = int(segments.shape[0])
        if count == 0:
            return ()
        points = segments.reshape(count * 2, 3)
        pixels = np.empty((count * 2, 2), dtype=np.float64)
        visible = np.empty(count * 2, dtype=np.uint8)
        _library, project, path = binding
        code = project(
            view.ctypes.data_as(_DoubleP),
            points.ctypes.data_as(_FloatP),
            count * 2,
            width,
            height,
            pixels.ctypes.data_as(_DoubleP),
            visible.ctypes.data_as(_ByteP),
        )
        if code != 0:
            _status = f"native selection call failed with code {code}: {path}"
            return None
        pixels = pixels.reshape(count, 2, 2)
        visible = visible.reshape(count, 2)
        result = tuple(
            ((float(pixel[0, 0]), float(pixel[0, 1])),
             (float(pixel[1, 0]), float(pixel[1, 1])))
            for pixel, flags in zip(pixels, visible)
            if bool(flags[0]) and bool(flags[1])
        )
        _status = f"ready: {path}"
        return result
    except Exception as exc:  # noqa: BLE001 -- optimization failure must preserve selection
        _status = f"native selection call raised: {exc}"
        return None


def _reset_for_tests() -> None:
    """Forget discovery state so tests can change the explicit library path."""
    global _signature, _native_binding, _status
    with _lock:
        _signature = None
        _native_binding = None
        _status = "not checked"
