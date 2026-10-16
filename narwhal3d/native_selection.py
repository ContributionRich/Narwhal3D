"""Optional Rust acceleration for display-only selection projection.

The native library never receives document geometry. It sees packed copies of a
viewport matrix, float32 display segments, float64 display-marker points, and cached
screen polylines. It can return ordinary screen tuples to an existing cache, answer nearest/rectangle queries
with stable indices, or classify curve crossings without materializing tuples. Any
discovery, ABI, input, or call failure returns ``None`` so callers preserve their
scalar paths.
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
_UInt64P = ctypes.POINTER(ctypes.c_uint64)
_lock = threading.Lock()
_signature: str | None = None
_native_binding: Any = None
_status = "not checked"

RECT_MISS = 0
RECT_HIT = 1
RECT_NO_EVIDENCE = 2
PICK_NO_HIT = -1


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
                try:
                    rect_query = library.narwhal_projected_segments_rect_query_f32
                    rect_query.argtypes = [
                        _DoubleP, _FloatP, ctypes.c_size_t,
                        ctypes.c_double, ctypes.c_double, _DoubleP, _ByteP,
                    ]
                    rect_query.restype = ctypes.c_int
                except AttributeError:
                    rect_query = None
                try:
                    batch_query = library.narwhal_projected_curves_rect_query_f32
                    batch_query.argtypes = [
                        _DoubleP, _FloatP, ctypes.c_size_t,
                        _UInt64P, ctypes.c_size_t,
                        ctypes.c_double, ctypes.c_double, _DoubleP, _ByteP,
                    ]
                    batch_query.restype = ctypes.c_int
                except AttributeError:
                    batch_query = None
                try:
                    nearest_curve = library.narwhal_nearest_projected_curve_f32
                    nearest_curve.argtypes = [
                        _DoubleP, _FloatP, ctypes.c_size_t,
                        _UInt64P, ctypes.c_size_t,
                        ctypes.c_double, ctypes.c_double, _DoubleP, ctypes.c_double,
                        _UInt64P, _DoubleP,
                    ]
                    nearest_curve.restype = ctypes.c_int
                except AttributeError:
                    nearest_curve = None
                try:
                    display_points = library.narwhal_project_display_points_f64
                    display_points.argtypes = [
                        _DoubleP, _DoubleP, ctypes.c_size_t,
                        ctypes.c_double, ctypes.c_double, _DoubleP, _ByteP,
                    ]
                    display_points.restype = ctypes.c_int
                except AttributeError:
                    pass
                try:
                    screen_polyline = library.narwhal_nearest_screen_polyline_f64
                    screen_polyline.argtypes = [
                        _DoubleP, _ByteP, ctypes.c_size_t,
                        _UInt64P, ctypes.c_size_t, _DoubleP, ctypes.c_double,
                        _UInt64P, _DoubleP,
                    ]
                    screen_polyline.restype = ctypes.c_int
                except AttributeError:
                    pass
                try:
                    aabb_rects = library.narwhal_screen_rects_from_aabbs_f64
                    aabb_rects.argtypes = [
                        _DoubleP, _DoubleP, _DoubleP, ctypes.c_size_t,
                        ctypes.c_double, ctypes.c_double, _DoubleP, _ByteP, _ByteP,
                    ]
                    aabb_rects.restype = ctypes.c_int
                except AttributeError:
                    pass
                try:
                    world_boxes = library.narwhal_screen_boxes_from_world_f64
                    world_boxes.argtypes = [
                        _DoubleP, _DoubleP, ctypes.c_size_t,
                        _UInt64P, ctypes.c_size_t, _ByteP,
                        ctypes.c_double, ctypes.c_double, _DoubleP, _ByteP,
                    ]
                    world_boxes.restype = ctypes.c_int
                except AttributeError:
                    pass
                try:
                    clip_boxes = library.narwhal_screen_boxes_from_clip_f64
                    clip_boxes.argtypes = [
                        _DoubleP, ctypes.c_size_t, _UInt64P, ctypes.c_size_t,
                        _ByteP, ctypes.c_double, ctypes.c_double, _DoubleP, _ByteP,
                    ]
                    clip_boxes.restype = ctypes.c_int
                except AttributeError:
                    pass
                try:
                    nearest_point = library.narwhal_nearest_projected_point_f64
                    nearest_point.argtypes = [
                        _DoubleP, _DoubleP, ctypes.c_size_t,
                        ctypes.c_double, ctypes.c_double, _DoubleP, ctypes.c_double,
                        _UInt64P, _DoubleP,
                    ]
                    nearest_point.restype = ctypes.c_int
                except AttributeError:
                    pass
                try:
                    points_rect = library.narwhal_projected_points_rect_query_f64
                    points_rect.argtypes = [
                        _DoubleP, _DoubleP, ctypes.c_size_t,
                        ctypes.c_double, ctypes.c_double, _DoubleP, _ByteP,
                    ]
                    points_rect.restype = ctypes.c_int
                except AttributeError:
                    pass
            except (AttributeError, OSError) as exc:
                _status = f"native selection load failed: {exc}"
                continue
            _native_binding = (
                library, project, rect_query, batch_query, nearest_curve, path)
            _status = f"ready: {path}"
            return _native_binding
        return None


def _segment_array(np, lines):
    """Contiguous f32 ``(E, 2, 3)`` view for either production segment layout."""
    segments = np.ascontiguousarray(lines, dtype=np.float32)
    if segments.ndim == 3 and segments.shape[1:] == (2, 3):
        return segments
    if (segments.ndim == 2 and segments.shape[1:] == (3,)
            and segments.shape[0] % 2 == 0):
        return segments.reshape(-1, 2, 3)
    return None


def _point_array(np, points):
    """Contiguous f64 ``(N, 3)`` display points, preserving document precision."""
    values = np.ascontiguousarray(points, dtype=np.float64)
    return values if values.ndim == 2 and values.shape[1:] == (3,) else None


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

        segments = _segment_array(np, lines)
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        width = float(width)
        height = float(height)
        if segments is None:
            _status = "native selection input rejected: segments must contain XYZ endpoint pairs"
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
        _library, project, _rect_query, _batch_query, _nearest_curve, path = binding
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


def projected_segments_rect_query(lines, matrix, width, height, rect):
    """Classify a display curve against ``rect`` without creating Python tuples.

    Returns ``RECT_HIT``, ``RECT_MISS``, or ``RECT_NO_EVIDENCE``. The last state
    means no complete segment had two projectable endpoints and lets the UI retain
    its conservative behavior. ``None`` requests the unchanged Python fallback.
    Existing ABI-1 libraries without the additive query symbol remain valid but use
    that fallback.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    _library, _project, rect_query, _batch_query, _nearest_curve, path = binding
    if rect_query is None:
        _status = f"native selection rectangle query unavailable: {path}"
        return None
    try:
        import numpy as np

        segments = _segment_array(np, lines)
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        rectangle = np.ascontiguousarray(rect, dtype=np.float64)
        width = float(width)
        height = float(height)
        if segments is None:
            _status = "native selection input rejected: segments must contain XYZ endpoint pairs"
            return None
        if view.shape != (4, 4) or not np.isfinite(view).all():
            _status = "native selection input rejected: matrix must be finite 4x4"
            return None
        if (rectangle.shape != (4,) or not np.isfinite(rectangle).all()
                or rectangle[0] > rectangle[2] or rectangle[1] > rectangle[3]):
            _status = "native selection input rejected: rectangle must be finite and ordered"
            return None
        if not np.isfinite((width, height)).all() or width < 0.0 or height < 0.0:
            _status = "native selection input rejected: invalid region dimensions"
            return None
        count = int(segments.shape[0])
        if count == 0:
            return RECT_NO_EVIDENCE
        evidence = ctypes.c_ubyte(0)
        code = rect_query(
            view.ctypes.data_as(_DoubleP),
            segments.ctypes.data_as(_FloatP),
            count,
            width,
            height,
            rectangle.ctypes.data_as(_DoubleP),
            ctypes.byref(evidence),
        )
        if (code not in (0, 1) or evidence.value not in (0, 1)
                or (code == 1 and not evidence.value)):
            _status = f"native selection rectangle query failed with code {code}: {path}"
            return None
        _status = f"ready: {path}"
        if code == 1:
            return RECT_HIT
        return RECT_MISS if evidence.value else RECT_NO_EVIDENCE
    except Exception as exc:  # noqa: BLE001 -- optimization failure must preserve selection
        _status = f"native selection rectangle query raised: {exc}"
        return None


def projected_curves_rect_query(lines_by_curve, matrix, width, height, rect):
    """Classify many display curves in one native call, or return ``None``.

    The successful tuple contains one ``RECT_*`` status per input curve in the same
    order. Any malformed input, unavailable additive symbol, native error, or
    unexpected output rejects the whole batch so callers can run their unchanged
    per-curve fallback.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    _library, _project, _rect_query, batch_query, _nearest_curve, path = binding
    if batch_query is None:
        _status = f"native selection batch query unavailable: {path}"
        return None
    try:
        import numpy as np

        curves = list(lines_by_curve)
        if not curves:
            return ()
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        rectangle = np.ascontiguousarray(rect, dtype=np.float64)
        width = float(width)
        height = float(height)
        if view.shape != (4, 4) or not np.isfinite(view).all():
            _status = "native selection input rejected: matrix must be finite 4x4"
            return None
        if (rectangle.shape != (4,) or not np.isfinite(rectangle).all()
                or rectangle[0] > rectangle[2] or rectangle[1] > rectangle[3]):
            _status = "native selection input rejected: rectangle must be finite and ordered"
            return None
        if not np.isfinite((width, height)).all() or width < 0.0 or height < 0.0:
            _status = "native selection input rejected: invalid region dimensions"
            return None
        arrays = []
        offsets = [0]
        total = 0
        for lines in curves:
            segments = _segment_array(np, lines)
            if segments is None:
                _status = "native selection input rejected: segments must contain XYZ endpoint pairs"
                return None
            total += int(segments.shape[0])
            if total > np.iinfo(np.uint64).max:
                _status = "native selection input rejected: segment count exceeds u64"
                return None
            arrays.append(segments)
            offsets.append(total)
        if len(arrays) == 1:
            packed = arrays[0]
        elif total:
            packed = np.ascontiguousarray(np.concatenate(arrays, axis=0), dtype=np.float32)
        else:
            packed = np.empty((0, 2, 3), dtype=np.float32)
        packed_offsets = np.ascontiguousarray(offsets, dtype=np.uint64)
        statuses = np.empty(len(curves), dtype=np.uint8)
        code = batch_query(
            view.ctypes.data_as(_DoubleP),
            packed.ctypes.data_as(_FloatP),
            total,
            packed_offsets.ctypes.data_as(_UInt64P),
            len(curves),
            width,
            height,
            rectangle.ctypes.data_as(_DoubleP),
            statuses.ctypes.data_as(_ByteP),
        )
        if code != 0 or not bool((statuses <= RECT_NO_EVIDENCE).all()):
            _status = f"native selection batch query failed with code {code}: {path}"
            return None
        _status = f"ready: {path}"
        return tuple(int(status) for status in statuses)
    except Exception as exc:  # noqa: BLE001 -- optimization failure must preserve selection
        _status = f"native selection batch query raised: {exc}"
        return None


def nearest_projected_curve(lines_by_curve, matrix, width, height, mouse, max_distance):
    """Return ``(curve_index, pixel_distance)`` from one native pick batch.

    ``(PICK_NO_HIT, inf)`` is a successful no-hit result. ``None`` means the caller
    must use the unchanged per-curve NumPy path. Input and output order are stable;
    the native implementation preserves the existing later-candidate tie rule.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    _library, _project, _rect_query, _batch_query, nearest_curve, path = binding
    if nearest_curve is None:
        _status = f"native selection nearest-curve query unavailable: {path}"
        return None
    try:
        import numpy as np

        curves = list(lines_by_curve)
        if not curves:
            return (PICK_NO_HIT, float("inf"))
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        cursor = np.ascontiguousarray(mouse, dtype=np.float64)
        width = float(width)
        height = float(height)
        limit = float(max_distance)
        if view.shape != (4, 4) or not np.isfinite(view).all():
            _status = "native selection input rejected: matrix must be finite 4x4"
            return None
        if cursor.shape != (2,) or not np.isfinite(cursor).all():
            _status = "native selection input rejected: mouse must be finite XY"
            return None
        if (not np.isfinite((width, height, limit)).all()
                or width < 0.0 or height < 0.0 or limit < 0.0):
            _status = "native selection input rejected: invalid pick dimensions or distance"
            return None
        arrays = []
        offsets = [0]
        total = 0
        for lines in curves:
            segments = _segment_array(np, lines)
            if segments is None:
                _status = "native selection input rejected: segments must contain XYZ endpoint pairs"
                return None
            total += int(segments.shape[0])
            if total > np.iinfo(np.uint64).max:
                _status = "native selection input rejected: segment count exceeds u64"
                return None
            arrays.append(segments)
            offsets.append(total)
        if len(arrays) == 1:
            packed = arrays[0]
        elif total:
            packed = np.ascontiguousarray(np.concatenate(arrays, axis=0), dtype=np.float32)
        else:
            packed = np.empty((0, 2, 3), dtype=np.float32)
        packed_offsets = np.ascontiguousarray(offsets, dtype=np.uint64)
        curve_index = ctypes.c_uint64((1 << 64) - 1)
        distance = ctypes.c_double(float("inf"))
        code = nearest_curve(
            view.ctypes.data_as(_DoubleP),
            packed.ctypes.data_as(_FloatP),
            total,
            packed_offsets.ctypes.data_as(_UInt64P),
            len(curves),
            width,
            height,
            cursor.ctypes.data_as(_DoubleP),
            limit,
            ctypes.byref(curve_index),
            ctypes.byref(distance),
        )
        if code == 0:
            if curve_index.value != (1 << 64) - 1 or distance.value != float("inf"):
                _status = f"native selection nearest-curve no-hit output invalid: {path}"
                return None
            _status = f"ready: {path}"
            return (PICK_NO_HIT, float("inf"))
        if (code != 1 or curve_index.value >= len(curves)
                or not np.isfinite(distance.value)
                or distance.value < 0.0 or distance.value > limit):
            _status = f"native selection nearest-curve query failed with code {code}: {path}"
            return None
        _status = f"ready: {path}"
        return (int(curve_index.value), float(distance.value))
    except Exception as exc:  # noqa: BLE001 -- optimization failure must preserve selection
        _status = f"native selection nearest-curve query raised: {exc}"
        return None


def project_display_points(points, matrix, width, height):
    """Project f64 display points with mathutils-exact mixed precision.

    Returns ``(pixels, visible)`` NumPy arrays without Python tuple materialization.
    ``None`` requests the unchanged scalar projection path for unavailable/old native
    symbols, malformed inputs or outputs, native errors, and call exceptions.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    display_points = getattr(library, "narwhal_project_display_points_f64", None)
    if display_points is None:
        _status = f"native selection display-point projection unavailable: {path}"
        return None
    try:
        import numpy as np

        values = _point_array(np, points)
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        width = float(width)
        height = float(height)
        if values is None:
            _status = "native selection input rejected: points must be an Nx3 array"
            return None
        if view.shape != (4, 4) or not np.isfinite(view).all():
            _status = "native selection input rejected: matrix must be finite 4x4"
            return None
        if not np.isfinite((width, height)).all() or width < 0.0 or height < 0.0:
            _status = "native selection input rejected: invalid region dimensions"
            return None
        pixels = np.empty((len(values), 2), dtype=np.float64)
        visible = np.empty(len(values), dtype=np.uint8)
        if len(values) == 0:
            return pixels, visible
        code = display_points(
            view.ctypes.data_as(_DoubleP),
            values.ctypes.data_as(_DoubleP),
            len(values),
            width,
            height,
            pixels.ctypes.data_as(_DoubleP),
            visible.ctypes.data_as(_ByteP),
        )
        if code != 0 or not bool((visible <= 1).all()):
            _status = f"native selection display-point projection failed with code {code}: {path}"
            return None
        shown = visible == 1
        if ((shown.any() and not bool(np.isfinite(pixels[shown]).all()))
                or ((~shown).any() and not bool(np.isnan(pixels[~shown]).all()))):
            _status = f"native selection display-point output invalid: {path}"
            return None
        _status = f"ready: {path}"
        return pixels, visible
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves hover
        _status = f"native selection display-point projection raised: {exc}"
        return None


def nearest_screen_polyline(pixels, visible, offsets, mouse, max_distance):
    """Return the nearest cached display polyline with strict earlier-tie semantics.

    ``(PICK_NO_HIT, inf)`` is an authoritative no-hit. ``None`` requests the
    established projected-array/Python-distance fallback. Rust receives only cached
    pixels, visibility and stable point offsets; curve names remain in Python.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    nearest_polyline = getattr(library, "narwhal_nearest_screen_polyline_f64", None)
    if nearest_polyline is None:
        _status = f"native selection screen-polyline query unavailable: {path}"
        return None
    try:
        import numpy as np

        points = np.ascontiguousarray(pixels, dtype=np.float64)
        raw_visible = np.asarray(visible)
        raw_offsets = np.asarray(offsets)
        cursor = np.ascontiguousarray(mouse, dtype=np.float64)
        limit = float(max_distance)
        if points.ndim != 2 or points.shape[1:] != (2,):
            _status = "native selection input rejected: pixels must be an Nx2 array"
            return None
        if (raw_visible.ndim != 1 or len(raw_visible) != len(points)
                or not np.issubdtype(raw_visible.dtype, np.integer)
                or bool((raw_visible < 0).any()) or bool((raw_visible > 1).any())):
            _status = "native selection input rejected: visibility must be N zero/one integers"
            return None
        if (raw_offsets.ndim != 1 or len(raw_offsets) < 1
                or not np.issubdtype(raw_offsets.dtype, np.integer)
                or bool((raw_offsets < 0).any())):
            _status = "native selection input rejected: offsets must be unsigned integers"
            return None
        packed_visible = np.ascontiguousarray(raw_visible, dtype=np.uint8)
        packed_offsets = np.ascontiguousarray(raw_offsets, dtype=np.uint64)
        shown = packed_visible == 1
        if ((shown.any() and not bool(np.isfinite(points[shown]).all()))
                or ((~shown).any() and not bool(np.isnan(points[~shown]).all()))):
            _status = "native selection input rejected: pixels disagree with visibility"
            return None
        if (int(packed_offsets[0]) != 0 or int(packed_offsets[-1]) != len(points)
                or bool((packed_offsets[1:] < packed_offsets[:-1]).any())):
            _status = "native selection input rejected: offsets must span monotonic points"
            return None
        if cursor.shape != (2,) or not np.isfinite(cursor).all():
            _status = "native selection input rejected: mouse must be finite XY"
            return None
        if not np.isfinite(limit) or limit < 0.0:
            _status = "native selection input rejected: invalid pick distance"
            return None
        curve_count = len(packed_offsets) - 1
        if curve_count == 0:
            return (PICK_NO_HIT, float("inf"))
        curve_index = ctypes.c_uint64((1 << 64) - 1)
        distance = ctypes.c_double(float("inf"))
        code = nearest_polyline(
            points.ctypes.data_as(_DoubleP),
            packed_visible.ctypes.data_as(_ByteP),
            len(points),
            packed_offsets.ctypes.data_as(_UInt64P),
            curve_count,
            cursor.ctypes.data_as(_DoubleP),
            limit,
            ctypes.byref(curve_index),
            ctypes.byref(distance),
        )
        if code == 0:
            if curve_index.value != (1 << 64) - 1 or distance.value != float("inf"):
                _status = f"native selection screen-polyline no-hit output invalid: {path}"
                return None
            _status = f"ready: {path}"
            return (PICK_NO_HIT, float("inf"))
        if (code != 1 or curve_index.value >= curve_count
                or not np.isfinite(distance.value)
                or distance.value < 0.0 or distance.value >= limit):
            _status = f"native selection screen-polyline query failed with code {code}: {path}"
            return None
        _status = f"ready: {path}"
        return int(curve_index.value), float(distance.value)
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves hover
        _status = f"native selection screen-polyline query raised: {exc}"
        return None


def screen_boxes_from_clip(clip, offsets, point_flags, width, height):
    """Reduce grouped homogeneous clip rows to screen rectangles.

    Returns ``(boxes, valid)`` NumPy arrays or ``None`` for the established
    NumPy/pixel-tuple fallback. Inputs are display-only clip copies and grouping
    metadata; object names and world/document geometry remain outside Rust.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    clip_boxes = getattr(library, "narwhal_screen_boxes_from_clip_f64", None)
    if clip_boxes is None:
        _status = f"native selection clip-box reduction unavailable: {path}"
        return None
    try:
        import numpy as np

        values = np.ascontiguousarray(clip, dtype=np.float64)
        raw_offsets = np.asarray(offsets)
        raw_flags = np.asarray(point_flags)
        width = float(width)
        height = float(height)
        if values.ndim != 2 or values.shape[1:] != (4,):
            _status = "native selection input rejected: clip coordinates must be Nx4"
            return None
        if (raw_offsets.ndim != 1 or len(raw_offsets) < 1
                or not np.issubdtype(raw_offsets.dtype, np.integer)
                or bool((raw_offsets < 0).any())):
            _status = "native selection input rejected: box offsets must be unsigned integers"
            return None
        packed_offsets = np.ascontiguousarray(raw_offsets, dtype=np.uint64)
        object_count = len(packed_offsets) - 1
        if (int(packed_offsets[0]) != 0 or int(packed_offsets[-1]) != len(values)
                or bool((packed_offsets[1:] < packed_offsets[:-1]).any())):
            _status = "native selection input rejected: box offsets must span monotonic points"
            return None
        if (raw_flags.ndim != 1 or len(raw_flags) != object_count
                or not np.issubdtype(raw_flags.dtype, np.integer)
                or bool((raw_flags < 0).any()) or bool((raw_flags > 1).any())):
            _status = "native selection input rejected: point flags must be zero/one integers"
            return None
        if not np.isfinite((width, height)).all() or width < 0.0 or height < 0.0:
            _status = "native selection input rejected: invalid box dimensions"
            return None
        packed_flags = np.ascontiguousarray(raw_flags, dtype=np.uint8)
        boxes = np.empty((object_count, 4), dtype=np.float64)
        valid = np.empty(object_count, dtype=np.uint8)
        if object_count == 0:
            return boxes, valid
        code = clip_boxes(
            values.ctypes.data_as(_DoubleP),
            len(values),
            packed_offsets.ctypes.data_as(_UInt64P),
            object_count,
            packed_flags.ctypes.data_as(_ByteP),
            width,
            height,
            boxes.ctypes.data_as(_DoubleP),
            valid.ctypes.data_as(_ByteP),
        )
        if code != 0 or not bool((valid <= 1).all()):
            _status = f"native selection clip-box reduction failed with code {code}: {path}"
            return None
        shown = valid == 1
        if ((shown.any() and not bool(np.isfinite(boxes[shown]).all()))
                or ((~shown).any() and not bool(np.isnan(boxes[~shown]).all()))):
            _status = f"native selection clip-box output invalid: {path}"
            return None
        if shown.any():
            shown_boxes = boxes[shown]
            if (bool((shown_boxes[:, 0] > shown_boxes[:, 2]).any())
                    or bool((shown_boxes[:, 1] > shown_boxes[:, 3]).any())):
                _status = f"native selection clip-box bounds inverted: {path}"
                return None
            point_boxes = shown_boxes[packed_flags[shown] == 1]
            if (len(point_boxes)
                    and (bool((point_boxes[:, 0] != point_boxes[:, 2]).any())
                         or bool((point_boxes[:, 1] != point_boxes[:, 3]).any()))):
                _status = f"native selection point box has area: {path}"
                return None
        _status = f"ready: {path}"
        return boxes, valid
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves selection
        _status = f"native selection clip-box reduction raised: {exc}"
        return None


def screen_boxes_from_world(points, matrix, offsets, point_flags, width, height):
    """Project grouped f64 display samples directly to canonical screen rectangles.

    Returns ``(boxes, valid)`` NumPy arrays or ``None`` for the established
    clip-buffer reducer. Object names and document geometry remain outside Rust.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    world_boxes = getattr(library, "narwhal_screen_boxes_from_world_f64", None)
    if world_boxes is None:
        _status = f"native selection fused world-box projection unavailable: {path}"
        return None
    try:
        import numpy as np

        values = _point_array(np, points)
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        raw_offsets = np.asarray(offsets)
        raw_flags = np.asarray(point_flags)
        width = float(width)
        height = float(height)
        if values is None:
            _status = "native selection input rejected: box points must be an Nx3 array"
            return None
        if view.shape != (4, 4) or not np.isfinite(view).all():
            _status = "native selection input rejected: box matrix must be finite 4x4"
            return None
        if (raw_offsets.ndim != 1 or len(raw_offsets) < 1
                or not np.issubdtype(raw_offsets.dtype, np.integer)
                or bool((raw_offsets < 0).any())):
            _status = "native selection input rejected: world-box offsets must be unsigned integers"
            return None
        packed_offsets = np.ascontiguousarray(raw_offsets, dtype=np.uint64)
        object_count = len(packed_offsets) - 1
        if (int(packed_offsets[0]) != 0 or int(packed_offsets[-1]) != len(values)
                or bool((packed_offsets[1:] < packed_offsets[:-1]).any())):
            _status = "native selection input rejected: world-box offsets must span monotonic points"
            return None
        if (raw_flags.ndim != 1 or len(raw_flags) != object_count
                or not np.issubdtype(raw_flags.dtype, np.integer)
                or bool((raw_flags < 0).any()) or bool((raw_flags > 1).any())):
            _status = "native selection input rejected: world-box flags must be zero/one integers"
            return None
        if not np.isfinite((width, height)).all() or width < 0.0 or height < 0.0:
            _status = "native selection input rejected: invalid world-box dimensions"
            return None
        packed_flags = np.ascontiguousarray(raw_flags, dtype=np.uint8)
        boxes = np.empty((object_count, 4), dtype=np.float64)
        valid = np.empty(object_count, dtype=np.uint8)
        if object_count == 0:
            return boxes, valid
        code = world_boxes(
            view.ctypes.data_as(_DoubleP),
            values.ctypes.data_as(_DoubleP),
            len(values),
            packed_offsets.ctypes.data_as(_UInt64P),
            object_count,
            packed_flags.ctypes.data_as(_ByteP),
            width,
            height,
            boxes.ctypes.data_as(_DoubleP),
            valid.ctypes.data_as(_ByteP),
        )
        if code != 0 or not bool((valid <= 1).all()):
            _status = f"native selection fused world-box projection failed with code {code}: {path}"
            return None
        shown = valid == 1
        if ((shown.any() and not bool(np.isfinite(boxes[shown]).all()))
                or ((~shown).any() and not bool(np.isnan(boxes[~shown]).all()))):
            _status = f"native selection fused world-box output invalid: {path}"
            return None
        if shown.any():
            shown_boxes = boxes[shown]
            if (bool((shown_boxes[:, 0] > shown_boxes[:, 2]).any())
                    or bool((shown_boxes[:, 1] > shown_boxes[:, 3]).any())):
                _status = f"native selection fused world-box bounds inverted: {path}"
                return None
            point_boxes = shown_boxes[packed_flags[shown] == 1]
            if (len(point_boxes)
                    and (bool((point_boxes[:, 0] != point_boxes[:, 2]).any())
                         or bool((point_boxes[:, 1] != point_boxes[:, 3]).any()))):
                _status = f"native selection fused point box has area: {path}"
                return None
        _status = f"ready: {path}"
        return boxes, valid
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves selection
        _status = f"native selection fused world-box projection raised: {exc}"
        return None


def screen_rects_from_aabbs(lower, upper, matrix, width, height):
    """Project f64 world AABBs to cached screen rectangles, or request NumPy fallback."""
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    aabb_rects = getattr(library, "narwhal_screen_rects_from_aabbs_f64", None)
    if aabb_rects is None:
        _status = f"native selection AABB rectangle projection unavailable: {path}"
        return None
    try:
        import numpy as np

        low = _point_array(np, lower)
        high = _point_array(np, upper)
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        width = float(width)
        height = float(height)
        if low is None or high is None or low.shape != high.shape:
            _status = "native selection input rejected: AABB bounds must be matching Nx3 arrays"
            return None
        if bool((low > high).any()):
            _status = "native selection input rejected: AABB lower bounds exceed upper bounds"
            return None
        if view.shape != (4, 4) or not np.isfinite(view).all():
            _status = "native selection input rejected: AABB matrix must be finite 4x4"
            return None
        if not np.isfinite((width, height)).all() or width < 0.0 or height < 0.0:
            _status = "native selection input rejected: invalid AABB viewport dimensions"
            return None
        count = len(low)
        rects = np.full((count, 4), np.nan, dtype=np.float64)
        unbounded = np.full(count, 255, dtype=np.uint8)
        all_behind = np.full(count, 255, dtype=np.uint8)
        if count == 0:
            empty = np.zeros(0, dtype=np.float64)
            masks = np.zeros(0, dtype=bool)
            return (empty, empty, empty, empty, masks, masks)
        code = aabb_rects(
            view.ctypes.data_as(_DoubleP),
            low.ctypes.data_as(_DoubleP),
            high.ctypes.data_as(_DoubleP),
            count,
            width,
            height,
            rects.ctypes.data_as(_DoubleP),
            unbounded.ctypes.data_as(_ByteP),
            all_behind.ctypes.data_as(_ByteP),
        )
        if (code != 0 or not bool((unbounded <= 1).all())
                or not bool((all_behind <= 1).all())):
            _status = f"native selection AABB projection failed with code {code}: {path}"
            return None
        if bool(np.isnan(rects).any()):
            _status = f"native selection AABB rectangle output contains NaN: {path}"
            return None
        bounded = unbounded == 0
        if (bounded.any()
                and (not bool(np.isfinite(rects[bounded]).all())
                     or bool((rects[bounded, 0] > rects[bounded, 2]).any())
                     or bool((rects[bounded, 1] > rects[bounded, 3]).any()))):
            _status = f"native selection AABB bounded rectangle invalid: {path}"
            return None
        if bool(((all_behind == 1) & (unbounded == 0)).any()):
            _status = f"native selection AABB behind mask is inconsistent: {path}"
            return None
        _status = f"ready: {path}"
        return (rects[:, 0], rects[:, 1], rects[:, 2], rects[:, 3],
                unbounded.astype(bool), all_behind.astype(bool))
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves culling
        _status = f"native selection AABB rectangle projection raised: {exc}"
        return None


def nearest_projected_point(points, matrix, width, height, mouse, max_distance):
    """Return ``(point_index, pixel_distance)`` from one native f64 point query.

    ``(PICK_NO_HIT, inf)`` is a successful no-hit result. ``None`` requests the
    unchanged scalar projection loop. Input order is stable and exact ties prefer
    the later point, matching the current picker.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    nearest_point = getattr(library, "narwhal_nearest_projected_point_f64", None)
    if nearest_point is None:
        _status = f"native selection nearest-point query unavailable: {path}"
        return None
    try:
        import numpy as np

        values = _point_array(np, points)
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        cursor = np.ascontiguousarray(mouse, dtype=np.float64)
        width = float(width)
        height = float(height)
        limit = float(max_distance)
        if values is None:
            _status = "native selection input rejected: points must be an Nx3 array"
            return None
        if view.shape != (4, 4) or not np.isfinite(view).all():
            _status = "native selection input rejected: matrix must be finite 4x4"
            return None
        if cursor.shape != (2,) or not np.isfinite(cursor).all():
            _status = "native selection input rejected: mouse must be finite XY"
            return None
        if (not np.isfinite((width, height, limit)).all()
                or width < 0.0 or height < 0.0 or limit < 0.0):
            _status = "native selection input rejected: invalid pick dimensions or distance"
            return None
        if len(values) == 0:
            return (PICK_NO_HIT, float("inf"))
        point_index = ctypes.c_uint64((1 << 64) - 1)
        distance = ctypes.c_double(float("inf"))
        code = nearest_point(
            view.ctypes.data_as(_DoubleP),
            values.ctypes.data_as(_DoubleP),
            len(values),
            width,
            height,
            cursor.ctypes.data_as(_DoubleP),
            limit,
            ctypes.byref(point_index),
            ctypes.byref(distance),
        )
        if code == 0:
            if point_index.value != (1 << 64) - 1 or distance.value != float("inf"):
                _status = f"native selection nearest-point no-hit output invalid: {path}"
                return None
            _status = f"ready: {path}"
            return (PICK_NO_HIT, float("inf"))
        if (code != 1 or point_index.value >= len(values)
                or not np.isfinite(distance.value)
                or distance.value < 0.0 or distance.value > limit):
            _status = f"native selection nearest-point query failed with code {code}: {path}"
            return None
        _status = f"ready: {path}"
        return (int(point_index.value), float(distance.value))
    except Exception as exc:  # noqa: BLE001 -- optimization failure must preserve picking
        _status = f"native selection nearest-point query raised: {exc}"
        return None


def projected_points_in_rect(points, matrix, width, height, rect):
    """Return ordered indices of f64 points projected inside ``rect``, or ``None``.

    Rectangle edges are inclusive. Missing additive symbols, malformed input/output,
    native errors, or call exceptions request the unchanged scalar fallback.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    points_rect = getattr(library, "narwhal_projected_points_rect_query_f64", None)
    if points_rect is None:
        _status = f"native selection point-rectangle query unavailable: {path}"
        return None
    try:
        import numpy as np

        values = _point_array(np, points)
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        rectangle = np.ascontiguousarray(rect, dtype=np.float64)
        width = float(width)
        height = float(height)
        if values is None:
            _status = "native selection input rejected: points must be an Nx3 array"
            return None
        if view.shape != (4, 4) or not np.isfinite(view).all():
            _status = "native selection input rejected: matrix must be finite 4x4"
            return None
        if (rectangle.shape != (4,) or not np.isfinite(rectangle).all()
                or rectangle[0] > rectangle[2] or rectangle[1] > rectangle[3]):
            _status = "native selection input rejected: rectangle must be finite and ordered"
            return None
        if not np.isfinite((width, height)).all() or width < 0.0 or height < 0.0:
            _status = "native selection input rejected: invalid region dimensions"
            return None
        if len(values) == 0:
            return ()
        inside = np.empty(len(values), dtype=np.uint8)
        code = points_rect(
            view.ctypes.data_as(_DoubleP),
            values.ctypes.data_as(_DoubleP),
            len(values),
            width,
            height,
            rectangle.ctypes.data_as(_DoubleP),
            inside.ctypes.data_as(_ByteP),
        )
        if code != 0 or not bool((inside <= 1).all()):
            _status = f"native selection point-rectangle query failed with code {code}: {path}"
            return None
        _status = f"ready: {path}"
        return tuple(int(index) for index in np.flatnonzero(inside))
    except Exception as exc:  # noqa: BLE001 -- optimization failure must preserve selection
        _status = f"native selection point-rectangle query raised: {exc}"
        return None


def _reset_for_tests() -> None:
    """Forget discovery state so tests can change the explicit library path."""
    global _signature, _native_binding, _status
    with _lock:
        _signature = None
        _native_binding = None
        _status = "not checked"
