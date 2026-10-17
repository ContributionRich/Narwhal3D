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
_UInt32P = ctypes.POINTER(ctypes.c_uint32)
_UInt64P = ctypes.POINTER(ctypes.c_uint64)
_lock = threading.Lock()
_signature: str | None = None
_native_binding: Any = None
_status = "not checked"
_near_pack_cache: dict[str, Any] = {
    "source": None, "arrays": None, "packed": None, "offsets": None, "total": 0,
}
_dynamic_snap_pack_cache: dict[str, Any] = {
    "source": None, "arrays": None, "packed": None, "offsets": None, "total": 0,
}
_discrete_snap_pack_cache: dict[str, Any] = {"signature": None, "packed": None}

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
                    snap_distances = library.narwhal_projected_snap_distances_f64
                    snap_distances.argtypes = [
                        _DoubleP, _DoubleP, ctypes.c_size_t, ctypes.c_double,
                        ctypes.c_double, _DoubleP, ctypes.c_double, _UInt64P, _DoubleP,
                        _UInt64P,
                    ]
                    snap_distances.restype = ctypes.c_int
                except AttributeError:
                    pass
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
                    projected_polylines = library.narwhal_nearest_projected_polylines_f64
                    projected_polylines.argtypes = [
                        _DoubleP, _DoubleP, ctypes.c_size_t, _UInt64P, ctypes.c_size_t,
                        ctypes.c_double, ctypes.c_double, _DoubleP,
                        _UInt64P, _DoubleP, _DoubleP, _ByteP,
                    ]
                    projected_polylines.restype = ctypes.c_int
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
                    discrete_snaps = library.narwhal_discrete_snap_survivors_f64
                    discrete_snaps.argtypes = [
                        _DoubleP, ctypes.c_size_t, _UInt64P,
                        _DoubleP, ctypes.c_size_t, _UInt64P,
                        _DoubleP, ctypes.c_size_t, _UInt64P,
                        _DoubleP, _ByteP, _ByteP, ctypes.c_size_t,
                        _DoubleP, _DoubleP, ctypes.c_double, ctypes.c_double,
                        _DoubleP, ctypes.c_double, ctypes.c_uint16,
                        _DoubleP, _UInt64P, _ByteP, _DoubleP,
                        ctypes.c_size_t, _UInt64P,
                    ]
                    discrete_snaps.restype = ctypes.c_int
                except AttributeError:
                    pass
                try:
                    oriented_bbox = library.narwhal_oriented_bbox_center_f64
                    oriented_bbox.argtypes = [
                        _DoubleP, ctypes.c_size_t, _DoubleP, _DoubleP, _ByteP,
                    ]
                    oriented_bbox.restype = ctypes.c_int
                except AttributeError:
                    pass
                try:
                    selection_apply = library.narwhal_ordered_selection_apply_u32
                    selection_apply.argtypes = [
                        _UInt32P, ctypes.c_size_t, _UInt32P, ctypes.c_size_t,
                        ctypes.c_ubyte, _UInt32P, ctypes.c_size_t, _UInt64P,
                    ]
                    selection_apply.restype = ctypes.c_int
                except AttributeError:
                    pass
                try:
                    world_rect_mask = library.narwhal_world_rect_hit_mask_f64
                    world_rect_mask.argtypes = [
                        _DoubleP, _DoubleP, ctypes.c_size_t,
                        _UInt64P, ctypes.c_size_t, _ByteP,
                        ctypes.c_double, ctypes.c_double, _DoubleP,
                        ctypes.c_ubyte, _ByteP,
                    ]
                    world_rect_mask.restype = ctypes.c_int
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
                try:
                    intersections = library.narwhal_intersection_snaps_f64
                    intersections.argtypes = [
                        _DoubleP, ctypes.c_size_t, _UInt64P, ctypes.c_size_t,
                        ctypes.c_double, _DoubleP, _UInt64P, ctypes.c_size_t,
                        _UInt64P,
                    ]
                    intersections.restype = ctypes.c_int
                except AttributeError:
                    pass
                try:
                    dynamic_snaps = library.narwhal_dynamic_perp_tan_snaps_f64
                    dynamic_snaps.argtypes = [
                        _DoubleP, ctypes.c_size_t, _UInt64P, _ByteP,
                        ctypes.c_size_t, _DoubleP, ctypes.c_ubyte,
                        _DoubleP, _UInt64P, _ByteP, ctypes.c_size_t, _UInt64P,
                    ]
                    dynamic_snaps.restype = ctypes.c_int
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


def discrete_snap_survivors(targets, modes, plane, matrix, width, height, mouse, radius):
    """Return ordered in-radius static Osnap rows, or ``None`` for full fallback."""
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    query = getattr(library, "narwhal_discrete_snap_survivors_f64", None)
    if query is None:
        _status = f"native discrete Osnap query unavailable: {path}"
        return None
    mode_bits = {"End": 1, "Mid": 2, "Cen": 4, "Quad": 8, "Point": 16, "Knot": 32}
    mode_ids = {1: "End", 2: "Mid", 3: "Cen", 4: "Quad", 5: "Point", 6: "Knot"}
    try:
        import numpy as np

        targets = tuple(targets)
        wanted = tuple(modes)
        if not wanted or any(mode not in mode_bits for mode in wanted):
            return None
        mask = 0
        for mode in wanted:
            mask |= mode_bits[mode]
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        cursor = np.ascontiguousarray(mouse, dtype=np.float64)
        frame = np.ascontiguousarray((
            *tuple(plane.origin), *tuple(plane.xaxis), *tuple(plane.yaxis)), dtype=np.float64)
        width, height, radius = float(width), float(height), float(radius)
        if (view.shape != (4, 4) or not bool(np.isfinite(view).all())
                or cursor.shape != (2,) or not bool(np.isfinite(cursor).all())
                or frame.shape != (9,) or not bool(np.isfinite(frame).all())
                or not bool(np.isfinite((width, height, radius)).all())
                or width < 0.0 or height < 0.0 or radius < 0.0):
            return None
        signature = tuple((id(target), id(target.points), id(target.vertices),
                           id(target.knots), target.center,
                           bool(target.closed), bool(target.is_point))
                          for target in targets)
        with _lock:
            packed = (_discrete_snap_pack_cache["packed"]
                      if _discrete_snap_pack_cache["signature"] == signature else None)
            if packed is None:
                families = []
                for attribute in ("points", "vertices", "knots"):
                    arrays, offsets, total = [], [0], 0
                    for target in targets:
                        source = getattr(target, attribute)
                        array = (np.empty((0, 3), dtype=np.float64) if len(source) == 0
                                 else _point_array(np, source))
                        if array is None or not bool(np.isfinite(array).all()):
                            return None
                        arrays.append(array)
                        total += len(array)
                        offsets.append(total)
                    values = (np.concatenate(arrays, axis=0) if total
                              else np.empty((0, 3), dtype=np.float64))
                    families.append((values, np.ascontiguousarray(offsets, dtype=np.uint64)))
                centers = np.zeros((len(targets), 3), dtype=np.float64)
                center_valid = np.zeros(len(targets), dtype=np.uint8)
                flags = np.zeros(len(targets), dtype=np.uint8)
                for index, target in enumerate(targets):
                    if target.center is not None:
                        center = np.ascontiguousarray(target.center, dtype=np.float64)
                        if center.shape != (3,) or not bool(np.isfinite(center).all()):
                            return None
                        centers[index] = center
                        center_valid[index] = 1
                    flags[index] = int(bool(target.is_point)) | (int(bool(target.closed)) << 1)
                packed = (*families, centers, center_valid, flags)
                _discrete_snap_pack_cache.update(signature=signature, packed=packed)
        (point_data, vertex_data, knot_data,
         centers, center_valid, flags) = packed
        points, point_offsets = point_data
        vertices, vertex_offsets = vertex_data
        knots, knot_offsets = knot_data
        vertex_counts = np.diff(vertex_offsets).astype(np.int64, copy=False)
        knot_counts = np.diff(knot_offsets).astype(np.int64, copy=False)
        point_counts = np.diff(point_offsets).astype(np.int64, copy=False)
        point_targets = (flags & 1) != 0
        curves = ~point_targets
        capacity = 0
        if "Point" in wanted:
            capacity += int(np.count_nonzero(point_targets & (point_counts > 0)))
        if "End" in wanted:
            capacity += int(vertex_counts[curves].sum())
        if "Mid" in wanted:
            capacity += int((np.maximum(vertex_counts[curves] - 1, 0) + 1).sum())
            capacity += int(np.count_nonzero(
                curves & ((flags & 2) != 0) & (vertex_counts >= 3)))
        if "Cen" in wanted:
            capacity += int(np.count_nonzero(curves & (center_valid != 0)))
        if "Quad" in wanted:
            capacity += 4 * int(np.count_nonzero(curves & (point_counts > 0)))
        if "Knot" in wanted:
            capacity += int(knot_counts[curves].sum())
        if capacity == 0:
            return ()
        arguments = (
            points.ctypes.data_as(_DoubleP), len(points), point_offsets.ctypes.data_as(_UInt64P),
            vertices.ctypes.data_as(_DoubleP), len(vertices), vertex_offsets.ctypes.data_as(_UInt64P),
            knots.ctypes.data_as(_DoubleP), len(knots), knot_offsets.ctypes.data_as(_UInt64P),
            centers.ctypes.data_as(_DoubleP), center_valid.ctypes.data_as(_ByteP),
            flags.ctypes.data_as(_ByteP), len(targets), frame.ctypes.data_as(_DoubleP),
            view.ctypes.data_as(_DoubleP), width, height, cursor.ctypes.data_as(_DoubleP),
            radius, mask,
        )
        xyz = np.full((capacity, 3), np.nan, dtype=np.float64)
        target_ids = np.full(capacity, np.iinfo(np.uint64).max, dtype=np.uint64)
        mode_values = np.zeros(capacity, dtype=np.uint8)
        distances = np.full(capacity, np.inf, dtype=np.float64)
        written = ctypes.c_uint64(np.iinfo(np.uint64).max)
        code = query(
            *arguments, xyz.ctypes.data_as(_DoubleP), target_ids.ctypes.data_as(_UInt64P),
            mode_values.ctypes.data_as(_ByteP), distances.ctypes.data_as(_DoubleP),
            capacity, ctypes.byref(written))
        count = int(written.value)
        if code != 0 or count > capacity:
            return None
        if count == 0:
            if (not bool(np.isnan(xyz).all())
                    or not bool((target_ids == np.iinfo(np.uint64).max).all())
                    or not bool((mode_values == 0).all())
                    or not bool(np.isinf(distances).all())):
                return None
            _status = f"ready: {path}"
            return ()
        allowed_ids = {identifier for identifier, name in mode_ids.items() if name in wanted}
        if (not bool(np.isfinite(xyz[:count]).all())
                or not bool(np.isfinite(distances[:count]).all())
                or bool((distances[:count] < 0.0).any())
                or bool((distances[:count] > radius).any())
                or bool((target_ids[:count] >= len(targets)).any())
                or any(int(value) not in allowed_ids for value in mode_values[:count])
                or (count < capacity and (
                    not bool(np.isnan(xyz[count:]).all())
                    or not bool((target_ids[count:] == np.iinfo(np.uint64).max).all())
                    or not bool((mode_values[count:] == 0).all())
                    or not bool(np.isinf(distances[count:]).all())))):
            return None
        distance_query = getattr(library, "narwhal_projected_snap_distances_f64", None)
        if distance_query is None:
            return None
        check_indices = np.full(count, np.iinfo(np.uint64).max, dtype=np.uint64)
        check_distances = np.full(count, np.inf, dtype=np.float64)
        check_count = ctypes.c_uint64(np.iinfo(np.uint64).max)
        distance_code = distance_query(
            view.ctypes.data_as(_DoubleP), xyz.ctypes.data_as(_DoubleP), count,
            width, height, cursor.ctypes.data_as(_DoubleP), radius,
            check_indices.ctypes.data_as(_UInt64P),
            check_distances.ctypes.data_as(_DoubleP), ctypes.byref(check_count))
        if (distance_code != 0 or check_count.value != count
                or not bool(np.array_equal(
                    check_indices, np.arange(count, dtype=np.uint64)))
                or not bool(np.array_equal(check_distances, distances[:count]))):
            return None

        import math
        semantic_cache = {}

        def semantic_points(target_index, mode):
            key = (target_index, mode)
            if key in semantic_cache:
                return semantic_cache[key]
            target = targets[target_index]
            if mode == "End":
                answer = tuple(target.vertices)
            elif mode == "Cen":
                answer = (() if target.center is None else (target.center,))
            elif mode == "Point":
                answer = tuple(target.points[:1]) if target.is_point else ()
            elif mode == "Knot":
                answer = tuple(target.knots)
            elif mode == "Mid":
                verts = tuple(target.vertices)
                mids = [tuple((a[i] + b[i]) / 2.0 for i in range(3))
                        for a, b in zip(verts[:-1], verts[1:])]
                if (target.closed and len(verts) >= 3
                        and math.dist(verts[-1], verts[0]) > 1.0e-12):
                    mids.append(tuple((verts[-1][i] + verts[0][i]) / 2.0
                                      for i in range(3)))
                samples = tuple(target.points)
                lengths = [math.dist(a, b) for a, b in zip(samples[:-1], samples[1:])]
                total = sum(lengths)
                if total > 0.0:
                    half, walked, arc = total / 2.0, 0.0, samples[-1]
                    for segment, length in enumerate(lengths):
                        if length <= 0.0:
                            continue
                        if walked + length >= half:
                            parameter = (half - walked) / length
                            a, b = samples[segment], samples[segment + 1]
                            arc = tuple(a[i] + (b[i] - a[i]) * parameter
                                        for i in range(3))
                            break
                        walked += length
                    if not any(math.dist(arc, prior) < 1.0e-9 for prior in mids):
                        mids.append(arc)
                answer = tuple(mids)
            else:
                samples = tuple(target.points)
                if not samples:
                    answer = ()
                else:
                    origin = tuple(plane.origin)
                    xaxis = tuple(plane.xaxis)
                    yaxis = tuple(plane.yaxis)
                    scored = []
                    for point in samples:
                        delta = tuple(point[i] - origin[i] for i in range(3))
                        scored.append((sum(delta[i] * xaxis[i] for i in range(3)),
                                       sum(delta[i] * yaxis[i] for i in range(3)), point))
                    raw = (min(scored, key=lambda row: row[0])[2],
                           max(scored, key=lambda row: row[0])[2],
                           min(scored, key=lambda row: row[1])[2],
                           max(scored, key=lambda row: row[1])[2])
                    unique = []
                    for point in raw:
                        if not any(math.dist(point, prior) < 1.0e-9 for prior in unique):
                            unique.append(point)
                    answer = tuple(unique)
            semantic_cache[key] = answer
            return answer

        previous_target, previous_mode = -1, -1
        result = []
        for index in range(count):
            target_index = int(target_ids[index])
            mode_id = int(mode_values[index])
            if (target_index < previous_target
                    or (target_index == previous_target and mode_id < previous_mode)):
                return None
            mode = mode_ids[mode_id]
            target = targets[target_index]
            if bool(target.is_point) != (mode == "Point"):
                return None
            point = tuple(float(value) for value in xyz[index])
            if not any(math.dist(point, expected) < 1.0e-8
                       for expected in semantic_points(target_index, mode)):
                return None
            previous_target, previous_mode = target_index, mode_id
            result.append((point, target_index, mode, float(distances[index])))
        _status = f"ready: {path}"
        return tuple(result)
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves Osnap
        _status = f"native discrete Osnap raised: {exc}"
        return None


def oriented_bbox_center(points, plane):
    """Return a CPlane-oriented world bbox center, ``()`` for empty, or fallback."""
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    query = getattr(library, "narwhal_oriented_bbox_center_f64", None)
    if query is None:
        _status = f"native oriented bbox unavailable: {path}"
        return None
    try:
        import numpy as np

        if len(points) == 0:
            return ()
        values = _point_array(np, points)
        if values is None or not bool(np.isfinite(values).all()):
            _status = "native selection input rejected: oriented bbox points must be finite Nx3"
            return None
        frame = np.ascontiguousarray((
            *tuple(plane.origin), *tuple(plane.xaxis), *tuple(plane.yaxis)),
            dtype=np.float64)
        if frame.shape != (9,) or not bool(np.isfinite(frame).all()):
            _status = "native selection input rejected: oriented bbox frame must be finite origin/X/Y"
            return None
        output = np.full(3, np.nan, dtype=np.float64)
        valid = ctypes.c_ubyte(255)
        code = query(
            values.ctypes.data_as(_DoubleP), len(values),
            frame.ctypes.data_as(_DoubleP), output.ctypes.data_as(_DoubleP),
            ctypes.byref(valid),
        )
        if (code != 0 or valid.value != 1 or not bool(np.isfinite(output).all())):
            _status = f"native oriented bbox output invalid with code {code}: {path}"
            return None
        _status = f"ready: {path}"
        return tuple(float(value) for value in output)
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves Gumball
        _status = f"native oriented bbox raised: {exc}"
        return None


def ordered_selection_apply(current, hits, action):
    """Apply ordered selection algebra to arbitrary hashable Python keys in Rust.

    Keys are mapped to opaque u32 IDs in first-seen order. Native output is
    checked against a linear reference ordering before being mapped back; ``None``
    requests the established ``select.apply`` fallback.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    query = getattr(library, "narwhal_ordered_selection_apply_u32", None)
    if query is None:
        _status = f"native ordered selection algebra unavailable: {path}"
        return None
    actions = {"replace": 0, "add": 1, "remove": 2}
    if action not in actions:
        _status = "native selection input rejected: unknown selection action"
        return None
    try:
        import numpy as np

        current = tuple(current or ())
        hits = tuple(hits or ())
        ids = {}
        values = []

        def encode(items):
            encoded = []
            for item in items:
                if item not in ids:
                    if len(values) >= (1 << 32):
                        raise OverflowError("selection key count exceeds u32")
                    ids[item] = len(values)
                    values.append(item)
                encoded.append(ids[item])
            return encoded

        current_ids = encode(current)
        hit_ids = encode(hits)
        if actions[action] == 0:
            expected = list(dict.fromkeys(hit_ids))
        elif actions[action] == 1:
            expected = list(dict.fromkeys((*current_ids, *hit_ids)))
        else:
            drop = set(hit_ids)
            expected = [value for value in dict.fromkeys(current_ids) if value not in drop]
        if not values:
            return ()
        packed_current = np.ascontiguousarray(current_ids, dtype=np.uint32)
        packed_hits = np.ascontiguousarray(hit_ids, dtype=np.uint32)
        output = np.full(len(values), np.iinfo(np.uint32).max, dtype=np.uint32)
        written = ctypes.c_uint64(np.iinfo(np.uint64).max)
        code = query(
            packed_current.ctypes.data_as(_UInt32P), len(packed_current),
            packed_hits.ctypes.data_as(_UInt32P), len(packed_hits), actions[action],
            output.ctypes.data_as(_UInt32P), len(output), ctypes.byref(written),
        )
        count = int(written.value)
        result_ids = [int(value) for value in output[:min(count, len(output))]]
        if (code != 0 or count > len(output) or result_ids != expected
                or len(result_ids) != len(set(result_ids))
                or any(value >= len(values) for value in result_ids)
                or (count < len(output)
                    and not bool((output[count:] == np.iinfo(np.uint32).max).all()))):
            _status = f"native ordered selection output invalid with code {code}: {path}"
            return None
        _status = f"ready: {path}"
        return tuple(values[value] for value in result_ids)
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves selection
        _status = f"native ordered selection algebra raised: {exc}"
        return None


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


def projected_snap_distances(points, matrix, width, height, mouse, radius):
    """Return compact source-ordered survivor indices/distances, or pixel fallback."""
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    query = getattr(library, "narwhal_projected_snap_distances_f64", None)
    if query is None:
        _status = f"native selection snap-distance query unavailable: {path}"
        return None
    try:
        import numpy as np

        values = _point_array(np, points)
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        cursor = np.ascontiguousarray(mouse, dtype=np.float64)
        width = float(width)
        height = float(height)
        radius = float(radius)
        if values is None:
            _status = "native selection input rejected: snap candidates must be Nx3"
            return None
        if view.shape != (4, 4) or not np.isfinite(view).all():
            _status = "native selection input rejected: snap matrix must be finite 4x4"
            return None
        if cursor.shape != (2,) or not np.isfinite(cursor).all():
            _status = "native selection input rejected: snap mouse must be finite XY"
            return None
        if (not np.isfinite((width, height, radius)).all()
                or width < 0.0 or height < 0.0 or radius < 0.0):
            _status = "native selection input rejected: invalid snap dimensions or radius"
            return None
        count = len(values)
        if count == 0:
            return (np.zeros(0, dtype=np.uint64),
                    np.zeros(0, dtype=np.float64))
        indices = np.full(count, np.iinfo(np.uint64).max, dtype=np.uint64)
        distances = np.full(count, np.inf, dtype=np.float64)
        survivor_count = ctypes.c_uint64(np.iinfo(np.uint64).max)
        code = query(
            view.ctypes.data_as(_DoubleP), values.ctypes.data_as(_DoubleP), count,
            width, height, cursor.ctypes.data_as(_DoubleP), radius,
            indices.ctypes.data_as(_UInt64P), distances.ctypes.data_as(_DoubleP),
            ctypes.byref(survivor_count),
        )
        kept = int(survivor_count.value)
        if code != 0 or kept > count:
            _status = f"native selection snap-distance query failed with code {code}: {path}"
            return None
        selected_indices = indices[:kept]
        selected_distances = distances[:kept]
        if (kept and (bool((selected_indices >= count).any())
                      or bool((selected_indices[1:] <= selected_indices[:-1]).any())
                      or not bool(np.isfinite(selected_distances).all())
                      or bool((selected_distances < 0.0).any())
                      or bool((selected_distances > radius).any()))):
            _status = f"native selection snap-distance output invalid: {path}"
            return None
        if ((kept < count)
                and (not bool((indices[kept:] == np.iinfo(np.uint64).max).all())
                     or not bool(np.isposinf(distances[kept:]).all()))):
            _status = f"native selection snap-distance tail invalid: {path}"
            return None
        _status = f"ready: {path}"
        return selected_indices, selected_distances
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves scoring
        _status = f"native selection snap-distance query raised: {exc}"
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


def nearest_projected_polylines(polylines, matrix, width, height, mouse):
    """Return one coarse nearest world point per display polyline, or scalar fallback."""
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    query = getattr(library, "narwhal_nearest_projected_polylines_f64", None)
    if query is None:
        _status = f"native selection projected-polyline query unavailable: {path}"
        return None
    try:
        import numpy as np

        source = tuple(polylines)
        if not source:
            return ()
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        cursor = np.ascontiguousarray(mouse, dtype=np.float64)
        width = float(width)
        height = float(height)
        if view.shape != (4, 4) or not np.isfinite(view).all():
            _status = "native selection input rejected: Near matrix must be finite 4x4"
            return None
        if cursor.shape != (2,) or not np.isfinite(cursor).all():
            _status = "native selection input rejected: Near mouse must be finite XY"
            return None
        if not np.isfinite((width, height)).all() or width < 0.0 or height < 0.0:
            _status = "native selection input rejected: invalid Near viewport dimensions"
            return None
        with _lock:
            cached_source = _near_pack_cache["source"]
            reuse = (cached_source is not None and len(cached_source) == len(source)
                     and all(old is new for old, new in zip(cached_source, source)))
            if reuse:
                arrays = _near_pack_cache["arrays"]
                packed = _near_pack_cache["packed"]
                packed_offsets = _near_pack_cache["offsets"]
                total = _near_pack_cache["total"]
            else:
                cacheable = all(
                    type(polyline) is tuple
                    and all(type(point) is tuple and len(point) == 3
                            and all(type(value) is float for value in point)
                            for point in polyline)
                    for polyline in source)
                arrays = []
                offsets = [0]
                total = 0
                for polyline in source:
                    points = _point_array(np, polyline)
                    if points is None:
                        _status = "native selection input rejected: Near polylines must be Nx3"
                        return None
                    arrays.append(points)
                    total += len(points)
                    if total > np.iinfo(np.uint64).max:
                        _status = "native selection input rejected: Near point count exceeds u64"
                        return None
                    offsets.append(total)
                packed = (np.concatenate(arrays, axis=0) if total
                          else np.empty((0, 3), dtype=np.float64))
                packed_offsets = np.ascontiguousarray(offsets, dtype=np.uint64)
                if cacheable:
                    _near_pack_cache.update(
                        source=source, arrays=arrays, packed=packed,
                        offsets=packed_offsets, total=total)
        count = len(arrays)
        segments = np.full(count, np.iinfo(np.uint64).max, dtype=np.uint64)
        parameters = np.full(count, np.nan, dtype=np.float64)
        distances = np.full(count, np.inf, dtype=np.float64)
        valid = np.full(count, 255, dtype=np.uint8)
        code = query(
            view.ctypes.data_as(_DoubleP), packed.ctypes.data_as(_DoubleP), total,
            packed_offsets.ctypes.data_as(_UInt64P), count, width, height,
            cursor.ctypes.data_as(_DoubleP), segments.ctypes.data_as(_UInt64P),
            parameters.ctypes.data_as(_DoubleP), distances.ctypes.data_as(_DoubleP),
            valid.ctypes.data_as(_ByteP),
        )
        if code != 0 or not bool((valid <= 1).all()):
            _status = f"native selection projected-polyline query failed with code {code}: {path}"
            return None
        result = []
        sentinel = np.iinfo(np.uint64).max
        for index, points in enumerate(arrays):
            if not valid[index]:
                if (segments[index] != sentinel or not np.isnan(parameters[index])
                        or distances[index] != np.inf):
                    _status = f"native selection hidden Near output invalid: {path}"
                    return None
                result.append(None)
                continue
            segment = int(segments[index])
            parameter = float(parameters[index])
            distance = float(distances[index])
            if (segment >= max(0, len(points) - 1)
                    or not 0.0 <= parameter <= 1.0
                    or not np.isfinite(distance) or distance < 0.0):
                _status = f"native selection visible Near output invalid: {path}"
                return None
            a, b = points[segment], points[segment + 1]
            point = a + (b - a) * parameter
            if not bool(np.isfinite(point).all()):
                _status = f"native selection reconstructed Near point invalid: {path}"
                return None
            result.append(tuple(float(value) for value in point))
        _status = f"ready: {path}"
        return tuple(result)
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves Near
        _status = f"native selection projected-polyline query raised: {exc}"
        return None


def perpendicular_tangent_points(polylines, closed, base, *, perp=True, tan=True):
    """Return ordered native sampled Perp/Tan rows for all target polylines.

    Rows are ``((x, y, z), target_index, "Perp" | "Tan")``. Input packing is
    shared with native intersection snaps; ``None`` requests scalar fallback.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    query = getattr(library, "narwhal_dynamic_perp_tan_snaps_f64", None)
    if query is None:
        _status = f"native selection Perp/Tan query unavailable: {path}"
        return None
    try:
        import numpy as np

        source = tuple(polylines)
        closed_flags = np.ascontiguousarray(tuple(closed), dtype=np.uint8)
        origin = np.ascontiguousarray(base, dtype=np.float64)
        mode_mask = int(bool(perp)) | (int(bool(tan)) << 1)
        if (closed_flags.shape != (len(source),)
                or bool((closed_flags > 1).any())
                or origin.shape != (3,) or not bool(np.isfinite(origin).all())
                or mode_mask == 0):
            _status = "native selection input rejected: invalid Perp/Tan metadata"
            return None
        with _lock:
            cached_source = _dynamic_snap_pack_cache["source"]
            reuse = (cached_source is not None and len(cached_source) == len(source)
                     and all(old is new for old, new in zip(cached_source, source)))
            if reuse:
                arrays = _dynamic_snap_pack_cache["arrays"]
                packed = _dynamic_snap_pack_cache["packed"]
                offsets = _dynamic_snap_pack_cache["offsets"]
                total = _dynamic_snap_pack_cache["total"]
            else:
                cacheable = all(
                    type(polyline) is tuple
                    and all(type(point) is tuple and len(point) == 3
                            and all(type(value) is float for value in point)
                            for point in polyline)
                    for polyline in source)
                arrays = []
                raw_offsets = [0]
                total = 0
                for polyline in source:
                    points = _point_array(np, polyline)
                    if points is None or not bool(np.isfinite(points).all()):
                        _status = "native selection input rejected: Perp/Tan polylines must be finite Nx3"
                        return None
                    arrays.append(points)
                    total += len(points)
                    if total > np.iinfo(np.uint64).max:
                        _status = "native selection input rejected: Perp/Tan point count exceeds u64"
                        return None
                    raw_offsets.append(total)
                packed = (np.concatenate(arrays, axis=0) if total
                          else np.empty((0, 3), dtype=np.float64))
                offsets = np.ascontiguousarray(raw_offsets, dtype=np.uint64)
                if cacheable:
                    _dynamic_snap_pack_cache.update(
                        source=source, arrays=arrays, packed=packed,
                        offsets=offsets, total=total)
        required = ctypes.c_uint64(0)
        code = query(
            packed.ctypes.data_as(_DoubleP), total,
            offsets.ctypes.data_as(_UInt64P),
            closed_flags.ctypes.data_as(_ByteP), len(arrays),
            origin.ctypes.data_as(_DoubleP), mode_mask,
            None, None, None, 0, ctypes.byref(required),
        )
        if code != 0 or required.value > np.iinfo(np.intp).max:
            _status = f"native selection Perp/Tan sizing failed with code {code}: {path}"
            return None
        count = int(required.value)
        if count == 0:
            _status = f"ready: {path}"
            return ()
        xyz = np.full((count, 3), np.nan, dtype=np.float64)
        targets = np.full(count, np.iinfo(np.uint64).max, dtype=np.uint64)
        kinds = np.zeros(count, dtype=np.uint8)
        written = ctypes.c_uint64(0)
        code = query(
            packed.ctypes.data_as(_DoubleP), total,
            offsets.ctypes.data_as(_UInt64P),
            closed_flags.ctypes.data_as(_ByteP), len(arrays),
            origin.ctypes.data_as(_DoubleP), mode_mask,
            xyz.ctypes.data_as(_DoubleP), targets.ctypes.data_as(_UInt64P),
            kinds.ctypes.data_as(_ByteP), count, ctypes.byref(written),
        )
        allowed = {kind for kind, wanted in ((1, perp), (2, tan)) if wanted}
        if (code != 0 or written.value != count or not bool(np.isfinite(xyz).all())
                or bool((targets >= len(arrays)).any())
                or any(int(kind) not in allowed for kind in kinds)):
            _status = f"native selection Perp/Tan output invalid with code {code}: {path}"
            return None
        _status = f"ready: {path}"
        names = {1: "Perp", 2: "Tan"}
        return tuple((tuple(float(value) for value in xyz[index]),
                      int(targets[index]), names[int(kinds[index])])
                     for index in range(count))
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves Osnap
        _status = f"native selection Perp/Tan query raised: {exc}"
        return None


def intersection_points(polylines, tol=1.0e-6):
    """Return ordered ``((x, y, z), earlier_target_index)`` intersection rows.

    Inputs are immutable sampled display polylines. The native result mirrors the
    scalar target-pair/segment-pair traversal and global first-seen deduplication;
    ``None`` requests the complete Python fallback.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    query = getattr(library, "narwhal_intersection_snaps_f64", None)
    if query is None:
        _status = f"native selection intersection query unavailable: {path}"
        return None
    try:
        import numpy as np

        source = tuple(polylines)
        tolerance = float(tol)
        if not np.isfinite(tolerance) or tolerance < 0.0:
            _status = "native selection input rejected: intersection tolerance must be finite and nonnegative"
            return None
        with _lock:
            cached_source = _dynamic_snap_pack_cache["source"]
            reuse = (cached_source is not None and len(cached_source) == len(source)
                     and all(old is new for old, new in zip(cached_source, source)))
            if reuse:
                arrays = _dynamic_snap_pack_cache["arrays"]
                packed = _dynamic_snap_pack_cache["packed"]
                offsets = _dynamic_snap_pack_cache["offsets"]
                total = _dynamic_snap_pack_cache["total"]
            else:
                cacheable = all(
                    type(polyline) is tuple
                    and all(type(point) is tuple and len(point) == 3
                            and all(type(value) is float for value in point)
                            for point in polyline)
                    for polyline in source)
                arrays = []
                raw_offsets = [0]
                total = 0
                for polyline in source:
                    points = _point_array(np, polyline)
                    if points is None or not bool(np.isfinite(points).all()):
                        _status = "native selection input rejected: intersection polylines must be finite Nx3"
                        return None
                    arrays.append(points)
                    total += len(points)
                    if total > np.iinfo(np.uint64).max:
                        _status = "native selection input rejected: intersection point count exceeds u64"
                        return None
                    raw_offsets.append(total)
                packed = (np.concatenate(arrays, axis=0) if total
                          else np.empty((0, 3), dtype=np.float64))
                offsets = np.ascontiguousarray(raw_offsets, dtype=np.uint64)
                if cacheable:
                    _dynamic_snap_pack_cache.update(
                        source=source, arrays=arrays, packed=packed,
                        offsets=offsets, total=total)
        required = ctypes.c_uint64(0)
        code = query(
            packed.ctypes.data_as(_DoubleP), total,
            offsets.ctypes.data_as(_UInt64P), len(arrays), tolerance,
            None, None, 0, ctypes.byref(required),
        )
        if code != 0 or required.value > np.iinfo(np.intp).max:
            _status = f"native selection intersection sizing failed with code {code}: {path}"
            return None
        count = int(required.value)
        if count == 0:
            _status = f"ready: {path}"
            return ()
        xyz = np.full((count, 3), np.nan, dtype=np.float64)
        targets = np.full(count, np.iinfo(np.uint64).max, dtype=np.uint64)
        written = ctypes.c_uint64(0)
        code = query(
            packed.ctypes.data_as(_DoubleP), total,
            offsets.ctypes.data_as(_UInt64P), len(arrays), tolerance,
            xyz.ctypes.data_as(_DoubleP), targets.ctypes.data_as(_UInt64P), count,
            ctypes.byref(written),
        )
        if (code != 0 or written.value != count or not bool(np.isfinite(xyz).all())
                or bool((targets >= len(arrays)).any())):
            _status = f"native selection intersection output invalid with code {code}: {path}"
            return None
        _status = f"ready: {path}"
        return tuple((tuple(float(value) for value in xyz[index]), int(targets[index]))
                     for index in range(count))
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves Osnap
        _status = f"native selection intersection query raised: {exc}"
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


def world_rect_hit_indices(points, matrix, offsets, point_flags, width, height,
                           rect, *, crossing=False):
    """Return compact ordered object indices hit by a window/crossing rectangle.

    Rust fuses projection, grouped reduction and classification into one byte per
    object. ``None`` requests the established screen-box fallback.
    """
    global _status
    binding = _binding()
    if binding is None:
        return None
    library, _project, _rect_query, _batch_query, _nearest_curve, path = binding
    query = getattr(library, "narwhal_world_rect_hit_mask_f64", None)
    if query is None:
        _status = f"native selection compact rectangle mask unavailable: {path}"
        return None
    try:
        import numpy as np

        values = _point_array(np, points)
        view = np.ascontiguousarray(matrix, dtype=np.float64)
        raw_offsets = np.asarray(offsets)
        raw_flags = np.asarray(point_flags)
        bounds = np.ascontiguousarray(rect, dtype=np.float64)
        width = float(width)
        height = float(height)
        if values is None:
            _status = "native selection input rejected: rectangle-mask points must be Nx3"
            return None
        if view.shape != (4, 4) or not bool(np.isfinite(view).all()):
            _status = "native selection input rejected: rectangle-mask matrix must be finite 4x4"
            return None
        if bounds.shape != (4,) or not bool(np.isfinite(bounds).all()) or not (
                bounds[0] <= bounds[2] and bounds[1] <= bounds[3]):
            _status = "native selection input rejected: rectangle-mask bounds are invalid"
            return None
        if (raw_offsets.ndim != 1 or len(raw_offsets) < 1
                or not np.issubdtype(raw_offsets.dtype, np.integer)
                or bool((raw_offsets < 0).any())):
            _status = "native selection input rejected: rectangle-mask offsets must be unsigned"
            return None
        packed_offsets = np.ascontiguousarray(raw_offsets, dtype=np.uint64)
        object_count = len(packed_offsets) - 1
        if (int(packed_offsets[0]) != 0 or int(packed_offsets[-1]) != len(values)
                or bool((packed_offsets[1:] < packed_offsets[:-1]).any())):
            _status = "native selection input rejected: rectangle-mask offsets must span points"
            return None
        if (raw_flags.ndim != 1 or len(raw_flags) != object_count
                or not np.issubdtype(raw_flags.dtype, np.integer)
                or bool((raw_flags < 0).any()) or bool((raw_flags > 1).any())):
            _status = "native selection input rejected: rectangle-mask flags must be zero/one"
            return None
        if not np.isfinite((width, height)).all() or width < 0.0 or height < 0.0:
            _status = "native selection input rejected: invalid rectangle-mask dimensions"
            return None
        packed_flags = np.ascontiguousarray(raw_flags, dtype=np.uint8)
        hits = np.zeros(object_count, dtype=np.uint8)
        if object_count == 0:
            return ()
        code = query(
            view.ctypes.data_as(_DoubleP), values.ctypes.data_as(_DoubleP), len(values),
            packed_offsets.ctypes.data_as(_UInt64P), object_count,
            packed_flags.ctypes.data_as(_ByteP), width, height,
            bounds.ctypes.data_as(_DoubleP), int(bool(crossing)),
            hits.ctypes.data_as(_ByteP),
        )
        if code != 0 or not bool((hits <= 1).all()):
            _status = f"native selection compact rectangle mask failed with code {code}: {path}"
            return None
        _status = f"ready: {path}"
        return tuple(int(index) for index in np.flatnonzero(hits))
    except Exception as exc:  # noqa: BLE001 -- optimization failure preserves selection
        _status = f"native selection compact rectangle mask raised: {exc}"
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
    """Forget discovery and packed-input state so tests can change backends."""
    global _signature, _native_binding, _status
    with _lock:
        _signature = None
        _native_binding = None
        _status = "not checked"
        _near_pack_cache.update(
            source=None, arrays=None, packed=None, offsets=None, total=0)
        _dynamic_snap_pack_cache.update(
            source=None, arrays=None, packed=None, offsets=None, total=0)
        _discrete_snap_pack_cache.update(signature=None, packed=None)
