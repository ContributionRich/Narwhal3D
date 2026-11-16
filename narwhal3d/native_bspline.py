"""Required Rust control-curve preview; Python owns host marshalling only."""
import array
import ctypes as C
import os
from pathlib import Path
import sys
import threading
from .native_preview import NativePreviewError, PreviewInputError, _point

_lock = threading.Lock()
_cache = None
_native_calls = 0


def _preview_binding():
    global _cache
    key = os.environ.get("NARWHAL_PURE_CORE") or "<packaged>"
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    name = ("narwhal_pure_core.dll" if sys.platform == "win32" else
            "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else "libnarwhal_pure_core.so")
    path = Path(key).expanduser() if key != "<packaged>" else Path(__file__).resolve().parent / "native" / name
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1]
        try:
            lib = C.CDLL(str(path))
            version = lib.narwhal_pure_core_abi_version
            version.argtypes, version.restype = [], C.c_uint32
            if version() != 1:
                raise NativePreviewError("required control preview ABI version mismatch")
            fn = lib.narwhal_pure_core_control_point_curve_preview
            fn.argtypes = [C.POINTER(C.c_double), C.c_size_t, C.c_uint32, C.c_uint32,
                           C.POINTER(C.c_double), C.c_size_t, C.POINTER(C.c_uint64)]
            fn.restype = C.c_int32
        except (OSError, AttributeError) as exc:
            raise NativePreviewError(f"required control preview library missing or stale: {path}: {exc}") from exc
        _cache = key, fn
        return fn


def available():
    try:
        _preview_binding()
        return True
    except NativePreviewError:
        return False


def control_point_curve_polyline(control, max_degree=3, per_span=12):
    """Native sampling; negative degree/span retain polygon/minimum-step behavior.

    OURS safety: at most 100000 controls, per-span at most 1024, and native
    output/work bounds. Empty/single picks draw nothing, as before.
    """
    global _native_calls
    pts = []
    for p in control:
        if len(pts) >= 100000:
            raise PreviewInputError("control preview exceeds 100000 points")
        try:
            point = tuple(p)
        except TypeError as exc:
            raise PreviewInputError("control point must contain three real coordinates") from exc
        _point(point)  # Validate shape/conversion while retaining polygon pick types.
        pts.append(point)
    n = len(pts)
    if n < 2:
        return []
    try:
        degree = max(0, min(int(max_degree), n - 1))
        span = max(0, int(per_span)) if degree > 1 else 0
    except (TypeError, ValueError, OverflowError) as exc:
        raise PreviewInputError("control degree/per_span must convert to bounded integers") from exc
    if span > 1024:
        raise PreviewInputError("control preview per_span exceeds 1024")
    fn = _preview_binding()
    values = array.array("d", (v for p in pts for v in p))
    data = (C.c_double * len(values)).from_buffer(values)
    used = C.c_uint64()
    code = fn(data, n, degree, span, None, 0, C.byref(used))
    if code == -2:
        raise PreviewInputError("native control preview refused nonfinite or excessive input")
    if code != -3 or not 0 < used.value <= 3000000 or used.value % 3:
        raise NativePreviewError(f"native control preview size protocol failure: {code}")
    count = used.value
    out = array.array("d", bytes(count * 8))
    code = fn(data, n, degree, span, (C.c_double * count).from_buffer(out), count, C.byref(used))
    if code == -2:
        raise PreviewInputError("native control preview produced nonfinite output")
    if code != 0 or used.value != count:
        raise NativePreviewError(f"native control preview output protocol failure: {code}")
    _native_calls += 1
    return pts if degree <= 1 else list(zip(out[0::3], out[1::3], out[2::3]))
