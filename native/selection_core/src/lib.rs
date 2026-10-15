// SPDX-License-Identifier: GPL-3.0-or-later
//! Display-only selection math for the Narwhal 3D Rust feasibility pilot.
//!
//! This crate never owns document geometry. It accepts packed copies of viewport
//! matrices, display points, and display segments through a small C ABI.

use std::{mem, ptr, slice};

const ABI_VERSION: u32 = 1;
const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
const STATUS_MISS: u8 = 0;
const STATUS_HIT: u8 = 1;
const STATUS_NO_EVIDENCE: u8 = 2;

#[inline]
fn valid_matrix(matrix: &[f64]) -> bool {
    matrix.len() == 16 && matrix.iter().all(|value| value.is_finite())
}

#[inline]
fn project_point(
    matrix: &[f64],
    x: f64,
    y: f64,
    z: f64,
    width: f64,
    height: f64,
) -> Option<(f64, f64)> {
    if !x.is_finite() || !y.is_finite() || !z.is_finite() {
        return None;
    }
    let clip_x = matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3];
    let clip_y = matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7];
    let clip_z = matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11];
    let clip_w = matrix[12] * x + matrix[13] * y + matrix[14] * z + matrix[15];
    if !clip_x.is_finite()
        || !clip_y.is_finite()
        || !clip_z.is_finite()
        || !clip_w.is_finite()
        || clip_w <= 0.0
    {
        return None;
    }
    let pixel_x = (clip_x / clip_w + 1.0) * width * 0.5;
    let pixel_y = (clip_y / clip_w + 1.0) * height * 0.5;
    if pixel_x.is_finite() && pixel_y.is_finite() {
        Some((pixel_x, pixel_y))
    } else {
        None
    }
}

#[inline]
fn segment_crosses_rect(a: (f64, f64), b: (f64, f64), rect: [f64; 4]) -> bool {
    let [x0, y0, x1, y1] = rect;
    let (ax, ay) = a;
    let (bx, by) = b;
    let dx = bx - ax;
    let dy = by - ay;
    let mut lo = 0.0_f64;
    let mut hi = 1.0_f64;
    for (p, q) in [(-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)] {
        if p == 0.0 {
            if q < 0.0 {
                return false;
            }
            continue;
        }
        let t = q / p;
        if p < 0.0 {
            lo = lo.max(t);
        } else {
            hi = hi.min(t);
        }
        if lo > hi {
            return false;
        }
    }
    true
}

#[inline]
fn valid_dimensions(width: f64, height: f64) -> bool {
    width.is_finite() && height.is_finite() && width >= 0.0 && height >= 0.0
}

#[inline]
fn valid_rect(rect: [f64; 4]) -> bool {
    rect.iter().all(|value| value.is_finite()) && rect[0] <= rect[2] && rect[1] <= rect[3]
}

#[inline]
fn valid_slice_len<T>(len: usize) -> bool {
    len <= isize::MAX as usize / mem::size_of::<T>()
}

#[inline]
fn project_display_point_f64(
    matrix: &[f64],
    point: &[f64],
    width: f64,
    height: f64,
) -> Option<(f64, f64)> {
    // Blender first multiplies a float32 mathutils Matrix by a float32 Vector.
    // `location_3d_to_region_2d` then reads those rounded components as Python
    // floats for the viewport division and returns another float32 Vector.
    let vector = [point[0] as f32, point[1] as f32, point[2] as f32, 1.0_f32];
    if !vector.iter().all(|value| value.is_finite()) {
        return None;
    }
    let component = |row: usize| {
        let base = row * 4;
        let mut dot = 0.0_f64;
        for column in 0..4 {
            let product = (matrix[base + column] as f32) * vector[column];
            dot += f64::from(product);
        }
        dot as f32
    };
    let clip_x = component(0);
    let clip_y = component(1);
    let clip_w = component(3);
    if !clip_x.is_finite() || !clip_y.is_finite() || !clip_w.is_finite() || clip_w <= 0.0 {
        return None;
    }
    let width_half = width / 2.0;
    let height_half = height / 2.0;
    let pixel_x = width_half + width_half * (f64::from(clip_x) / f64::from(clip_w));
    let pixel_y = height_half + height_half * (f64::from(clip_y) / f64::from(clip_w));
    let pixel_x = pixel_x as f32;
    let pixel_y = pixel_y as f32;
    if pixel_x.is_finite() && pixel_y.is_finite() {
        Some((f64::from(pixel_x), f64::from(pixel_y)))
    } else {
        None
    }
}

#[inline]
fn homogeneous_point(matrix: &[f64], point: &[f32]) -> [f64; 4] {
    let x = f64::from(point[0]);
    let y = f64::from(point[1]);
    let z = f64::from(point[2]);
    [
        matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
        matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
        matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
        matrix[12] * x + matrix[13] * y + matrix[14] * z + matrix[15],
    ]
}

#[inline]
fn curve_screen_distance(
    matrix: &[f64],
    segments: &[f32],
    width: f64,
    height: f64,
    mouse: [f64; 2],
) -> f64 {
    if segments.is_empty() {
        return f64::INFINITY;
    }
    let mut max_abs_w = 1.0_f64;
    let mut has_nan_w = false;
    for point in segments.chunks_exact(3) {
        let w = homogeneous_point(matrix, point)[3];
        if w.is_nan() {
            has_nan_w = true;
        } else {
            max_abs_w = max_abs_w.max(w.abs());
        }
    }
    if has_nan_w {
        // NumPy max becomes NaN, then Python max(1.0, NaN) yields 1.0.
        max_abs_w = 1.0;
    }
    let epsilon = 1.0e-9 * max_abs_w;
    let half_width = width * 0.5;
    let half_height = height * 0.5;
    let mut best_squared = f64::INFINITY;
    for segment in segments.chunks_exact(6) {
        let a = homogeneous_point(matrix, &segment[..3]);
        let b = homogeneous_point(matrix, &segment[3..]);
        let wa = a[3];
        let wb = b[3];
        if wa < epsilon && wb < epsilon {
            continue;
        }
        let delta = [b[0] - a[0], b[1] - a[1], b[2] - a[2], b[3] - a[3]];
        let start_t = if wa < epsilon {
            (epsilon - wa) / (wb - wa)
        } else {
            0.0
        };
        let end_t = if wb < epsilon {
            (epsilon - wa) / (wb - wa)
        } else {
            1.0
        };
        let start = [
            a[0] + start_t * delta[0],
            a[1] + start_t * delta[1],
            a[2] + start_t * delta[2],
            a[3] + start_t * delta[3],
        ];
        let end = [
            a[0] + end_t * delta[0],
            a[1] + end_t * delta[1],
            a[2] + end_t * delta[2],
            a[3] + end_t * delta[3],
        ];
        let screen_start = [
            half_width + half_width * start[0] / start[3],
            half_height + half_height * start[1] / start[3],
        ];
        let screen_end = [
            half_width + half_width * end[0] / end[3],
            half_height + half_height * end[1] / end[3],
        ];
        let dx = screen_end[0] - screen_start[0];
        let dy = screen_end[1] - screen_start[1];
        let length_squared = dx * dx + dy * dy;
        let t = if length_squared > 0.0 {
            ((mouse[0] - screen_start[0]) * dx + (mouse[1] - screen_start[1]) * dy) / length_squared
        } else {
            0.0
        }
        .clamp(0.0, 1.0);
        let nearest_x = screen_start[0] + t * dx;
        let nearest_y = screen_start[1] + t * dy;
        let mouse_dx = nearest_x - mouse[0];
        let mouse_dy = nearest_y - mouse[1];
        let distance_squared = mouse_dx * mouse_dx + mouse_dy * mouse_dy;
        if distance_squared.is_finite() {
            best_squared = best_squared.min(distance_squared);
        }
    }
    best_squared.sqrt()
}

#[inline]
fn classify_projected_segments(
    matrix: &[f64],
    segments: &[f32],
    width: f64,
    height: f64,
    rect: [f64; 4],
) -> (bool, bool) {
    let mut any_projectable = false;
    for segment in segments.chunks_exact(6) {
        let a = project_point(
            matrix,
            f64::from(segment[0]),
            f64::from(segment[1]),
            f64::from(segment[2]),
            width,
            height,
        );
        let b = project_point(
            matrix,
            f64::from(segment[3]),
            f64::from(segment[4]),
            f64::from(segment[5]),
            width,
            height,
        );
        if let (Some(a), Some(b)) = (a, b) {
            any_projectable = true;
            if segment_crosses_rect(a, b, rect) {
                return (true, true);
            }
        }
    }
    (false, any_projectable)
}

/// Return the stable ABI version for the feasibility interface.
#[no_mangle]
pub extern "C" fn narwhal_selection_core_abi_version() -> u32 {
    ABI_VERSION
}

/// Project packed f32 XYZ points into packed f64 XY pixels.
///
/// `out_visible[i]` is one when `out_xy[2*i..2*i+2]` is valid and zero when the
/// point is non-finite, behind the eye plane, or otherwise unprojectable. Hidden
/// output coordinates are written as NaN so accidental use fails visibly.
///
/// # Safety
///
/// The caller must provide readable arrays of 16 matrix values and `count * 3`
/// point values plus writable arrays of `count * 2` output values and `count`
/// visibility bytes. Arrays may be null only when `count == 0`, except that the
/// matrix is always required.
#[no_mangle]
pub unsafe extern "C" fn narwhal_project_points_f32(
    matrix_ptr: *const f64,
    points_ptr: *const f32,
    count: usize,
    width: f64,
    height: f64,
    out_xy_ptr: *mut f64,
    out_visible_ptr: *mut u8,
) -> i32 {
    if matrix_ptr.is_null() {
        return ERR_NULL;
    }
    if count > 0 && (points_ptr.is_null() || out_xy_ptr.is_null() || out_visible_ptr.is_null()) {
        return ERR_NULL;
    }
    let point_values = match count.checked_mul(3) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let output_values = match count.checked_mul(2) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    if !valid_matrix(matrix) || !valid_dimensions(width, height) {
        return ERR_INPUT;
    }
    if count == 0 {
        return 0;
    }
    let points = slice::from_raw_parts(points_ptr, point_values);
    let out_xy = slice::from_raw_parts_mut(out_xy_ptr, output_values);
    let out_visible = slice::from_raw_parts_mut(out_visible_ptr, count);
    for index in 0..count {
        let base = index * 3;
        let projected = project_point(
            matrix,
            f64::from(points[base]),
            f64::from(points[base + 1]),
            f64::from(points[base + 2]),
            width,
            height,
        );
        match projected {
            Some((x, y)) => {
                out_xy[index * 2] = x;
                out_xy[index * 2 + 1] = y;
                out_visible[index] = 1;
            }
            None => {
                out_xy[index * 2] = f64::NAN;
                out_xy[index * 2 + 1] = f64::NAN;
                out_visible[index] = 0;
            }
        }
    }
    0
}

/// Return one when any packed f64 screen segment touches or enters `rect`.
///
/// Segments use the flat layout `(ax, ay, bx, by)`. The predicate is the same
/// inclusive Liang--Barsky test as the current Python selection implementation.
///
/// # Safety
///
/// The caller must provide `count * 4` readable segment values and four readable
/// rectangle values. The segment pointer may be null only when `count == 0`.
#[no_mangle]
pub unsafe extern "C" fn narwhal_any_screen_segment_crosses_rect_f64(
    segments_ptr: *const f64,
    count: usize,
    rect_ptr: *const f64,
) -> i32 {
    if rect_ptr.is_null() || (count > 0 && segments_ptr.is_null()) {
        return ERR_NULL;
    }
    let values = match count.checked_mul(4) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let rect_slice = slice::from_raw_parts(rect_ptr, 4);
    let rect = [rect_slice[0], rect_slice[1], rect_slice[2], rect_slice[3]];
    if !valid_rect(rect) {
        return ERR_INPUT;
    }
    if count == 0 {
        return 0;
    }
    let segments = slice::from_raw_parts(segments_ptr, values);
    for segment in segments.chunks_exact(4) {
        if !segment.iter().all(|value| value.is_finite()) {
            continue;
        }
        if segment_crosses_rect((segment[0], segment[1]), (segment[2], segment[3]), rect) {
            return 1;
        }
    }
    0
}

/// Project packed f32 world segments and return one when any projected segment
/// touches or enters a screen rectangle.
///
/// This models the current cold crossing-refinement path: both endpoints must be
/// projectable, and segment clipping is inclusive. It intentionally does not model
/// the existing warm screen-space cache or uniform-bin candidate index.
///
/// # Safety
///
/// The caller must provide 16 readable matrix values, `count * 6` readable world
/// segment values, and four readable rectangle values. The segment pointer may be
/// null only when `count == 0`.
#[no_mangle]
pub unsafe extern "C" fn narwhal_any_projected_segment_crosses_rect_f32(
    matrix_ptr: *const f64,
    segments_ptr: *const f32,
    count: usize,
    width: f64,
    height: f64,
    rect_ptr: *const f64,
) -> i32 {
    if matrix_ptr.is_null() || rect_ptr.is_null() || (count > 0 && segments_ptr.is_null()) {
        return ERR_NULL;
    }
    let values = match count.checked_mul(6) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    let rect_slice = slice::from_raw_parts(rect_ptr, 4);
    let rect = [rect_slice[0], rect_slice[1], rect_slice[2], rect_slice[3]];
    if !valid_matrix(matrix) || !valid_dimensions(width, height) || !valid_rect(rect) {
        return ERR_INPUT;
    }
    if count == 0 {
        return 0;
    }
    let segments = slice::from_raw_parts(segments_ptr, values);
    i32::from(classify_projected_segments(matrix, segments, width, height, rect).0)
}

/// Classify projected display segments against a screen rectangle without allocating.
///
/// The return value is one for a hit, zero for a miss, and negative for invalid input.
/// On success, `out_any_projectable` is one when at least one complete segment had two
/// projectable endpoints. This distinguishes an authoritative miss from unavailable
/// screen evidence while preserving the older combined predicate's ABI and behavior.
///
/// # Safety
///
/// The caller must provide 16 readable matrix values, `count * 6` readable world
/// segment values, four readable rectangle values, and one writable evidence byte.
/// The segment pointer may be null only when `count == 0`.
#[no_mangle]
pub unsafe extern "C" fn narwhal_projected_segments_rect_query_f32(
    matrix_ptr: *const f64,
    segments_ptr: *const f32,
    count: usize,
    width: f64,
    height: f64,
    rect_ptr: *const f64,
    out_any_projectable_ptr: *mut u8,
) -> i32 {
    if out_any_projectable_ptr.is_null() {
        return ERR_NULL;
    }
    *out_any_projectable_ptr = 0;
    if matrix_ptr.is_null() || rect_ptr.is_null() || (count > 0 && segments_ptr.is_null()) {
        return ERR_NULL;
    }
    let values = match count.checked_mul(6) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    let rect_slice = slice::from_raw_parts(rect_ptr, 4);
    let rect = [rect_slice[0], rect_slice[1], rect_slice[2], rect_slice[3]];
    if !valid_matrix(matrix) || !valid_dimensions(width, height) || !valid_rect(rect) {
        return ERR_INPUT;
    }
    if count == 0 {
        return 0;
    }
    let segments = slice::from_raw_parts(segments_ptr, values);
    let (hit, any_projectable) = classify_projected_segments(matrix, segments, width, height, rect);
    *out_any_projectable_ptr = u8::from(any_projectable);
    i32::from(hit)
}

/// Classify many display curves against one rectangle in a single allocation-free call.
///
/// `offsets` has `curve_count + 1` monotonically increasing segment offsets, starts
/// at zero, and ends at `segment_count`. Each output byte is `0` for a projectable
/// miss, `1` for a hit, or `2` when no complete segment was projectable.
///
/// # Safety
///
/// The caller must provide 16 readable matrix values, `segment_count * 6` readable
/// world-segment values, `curve_count + 1` readable u64 offsets, four readable
/// rectangle values, and `curve_count` writable status bytes. Segment and output
/// pointers may be null only when their corresponding counts are zero.
#[no_mangle]
pub unsafe extern "C" fn narwhal_projected_curves_rect_query_f32(
    matrix_ptr: *const f64,
    segments_ptr: *const f32,
    segment_count: usize,
    offsets_ptr: *const u64,
    curve_count: usize,
    width: f64,
    height: f64,
    rect_ptr: *const f64,
    out_statuses_ptr: *mut u8,
) -> i32 {
    if matrix_ptr.is_null() || rect_ptr.is_null() {
        return ERR_NULL;
    }
    if curve_count > 0 && (offsets_ptr.is_null() || out_statuses_ptr.is_null()) {
        return ERR_NULL;
    }
    if segment_count > 0 && segments_ptr.is_null() {
        return ERR_NULL;
    }
    let segment_values = match segment_count.checked_mul(6) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let offset_count = match curve_count.checked_add(1) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    let rect_slice = slice::from_raw_parts(rect_ptr, 4);
    let rect = [rect_slice[0], rect_slice[1], rect_slice[2], rect_slice[3]];
    if !valid_matrix(matrix) || !valid_dimensions(width, height) || !valid_rect(rect) {
        return ERR_INPUT;
    }
    if curve_count == 0 {
        return if segment_count == 0 { 0 } else { ERR_INPUT };
    }
    let statuses = slice::from_raw_parts_mut(out_statuses_ptr, curve_count);
    statuses.fill(STATUS_NO_EVIDENCE);
    let offsets = slice::from_raw_parts(offsets_ptr, offset_count);
    if offsets[0] != 0 || offsets[curve_count] != segment_count as u64 {
        return ERR_INPUT;
    }
    for pair in offsets.windows(2) {
        let start = match usize::try_from(pair[0]) {
            Ok(value) => value,
            Err(_) => return ERR_SIZE,
        };
        let end = match usize::try_from(pair[1]) {
            Ok(value) => value,
            Err(_) => return ERR_SIZE,
        };
        if start > end || end > segment_count {
            return ERR_INPUT;
        }
    }
    let segments: &[f32] = if segment_values == 0 {
        &[]
    } else {
        slice::from_raw_parts(segments_ptr, segment_values)
    };
    for index in 0..curve_count {
        let start = offsets[index] as usize;
        let end = offsets[index + 1] as usize;
        let (hit, any_projectable) =
            classify_projected_segments(matrix, &segments[start * 6..end * 6], width, height, rect);
        statuses[index] = if hit {
            STATUS_HIT
        } else if any_projectable {
            STATUS_MISS
        } else {
            STATUS_NO_EVIDENCE
        };
    }
    0
}

/// Return the nearest display curve to one screen point using exact picker semantics.
///
/// Curves use the same concatenated segments and monotonic u64 offsets as the batch
/// rectangle query. The return value is one for a hit within `max_distance`, zero for
/// no hit, and negative for invalid input. Exact distance ties are resolved in favor
/// of the later curve, matching the Python picker's `<=` update rule.
///
/// # Safety
///
/// The caller must provide 16 readable matrix values, `segment_count * 6` readable
/// world-segment values, `curve_count + 1` readable offsets, two readable mouse
/// coordinates, and writable curve-index/distance outputs.
#[no_mangle]
pub unsafe extern "C" fn narwhal_nearest_projected_curve_f32(
    matrix_ptr: *const f64,
    segments_ptr: *const f32,
    segment_count: usize,
    offsets_ptr: *const u64,
    curve_count: usize,
    width: f64,
    height: f64,
    mouse_ptr: *const f64,
    max_distance: f64,
    out_curve_index_ptr: *mut u64,
    out_distance_ptr: *mut f64,
) -> i32 {
    if out_curve_index_ptr.is_null() || out_distance_ptr.is_null() {
        return ERR_NULL;
    }
    *out_curve_index_ptr = u64::MAX;
    *out_distance_ptr = f64::INFINITY;
    if matrix_ptr.is_null() || mouse_ptr.is_null() {
        return ERR_NULL;
    }
    if curve_count > 0 && offsets_ptr.is_null() {
        return ERR_NULL;
    }
    if segment_count > 0 && segments_ptr.is_null() {
        return ERR_NULL;
    }
    let segment_values = match segment_count.checked_mul(6) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let offset_count = match curve_count.checked_add(1) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    let mouse_slice = slice::from_raw_parts(mouse_ptr, 2);
    let mouse = [mouse_slice[0], mouse_slice[1]];
    if !valid_matrix(matrix)
        || !valid_dimensions(width, height)
        || !mouse.iter().all(|value| value.is_finite())
        || !max_distance.is_finite()
        || max_distance < 0.0
    {
        return ERR_INPUT;
    }
    if curve_count == 0 {
        return if segment_count == 0 { 0 } else { ERR_INPUT };
    }
    let offsets = slice::from_raw_parts(offsets_ptr, offset_count);
    if offsets[0] != 0 || offsets[curve_count] != segment_count as u64 {
        return ERR_INPUT;
    }
    for pair in offsets.windows(2) {
        let start = match usize::try_from(pair[0]) {
            Ok(value) => value,
            Err(_) => return ERR_SIZE,
        };
        let end = match usize::try_from(pair[1]) {
            Ok(value) => value,
            Err(_) => return ERR_SIZE,
        };
        if start > end || end > segment_count {
            return ERR_INPUT;
        }
    }
    let segments: &[f32] = if segment_values == 0 {
        &[]
    } else {
        slice::from_raw_parts(segments_ptr, segment_values)
    };
    let mut best_index = u64::MAX;
    let mut best_distance = max_distance;
    for index in 0..curve_count {
        let start = offsets[index] as usize;
        let end = offsets[index + 1] as usize;
        let distance =
            curve_screen_distance(matrix, &segments[start * 6..end * 6], width, height, mouse);
        if distance <= best_distance {
            best_index = index as u64;
            best_distance = distance;
        }
    }
    if best_index == u64::MAX {
        return 0;
    }
    *out_curve_index_ptr = best_index;
    *out_distance_ptr = best_distance;
    1
}

/// Return the nearest projectable f64 display point within one screen radius.
///
/// The return value is one for a hit, zero for no hit, and negative for invalid
/// input. Exact distance ties prefer the later point, matching the Python picker's
/// `<=` update rule.
///
/// # Safety
///
/// The caller must provide 16 readable matrix values, `count * 3` readable point
/// values, two readable mouse coordinates, and writable index/distance outputs.
/// The point pointer may be null only when `count == 0`; all buffers must be
/// correctly aligned and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_nearest_projected_point_f64(
    matrix_ptr: *const f64,
    points_ptr: *const f64,
    count: usize,
    width: f64,
    height: f64,
    mouse_ptr: *const f64,
    max_distance: f64,
    out_point_index_ptr: *mut u64,
    out_distance_ptr: *mut f64,
) -> i32 {
    if out_point_index_ptr.is_null() || out_distance_ptr.is_null() {
        return ERR_NULL;
    }
    *out_point_index_ptr = u64::MAX;
    *out_distance_ptr = f64::INFINITY;
    if matrix_ptr.is_null() || mouse_ptr.is_null() || (count > 0 && points_ptr.is_null()) {
        return ERR_NULL;
    }
    let point_values = match count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    let mouse_slice = slice::from_raw_parts(mouse_ptr, 2);
    let mouse = [mouse_slice[0], mouse_slice[1]];
    if !valid_matrix(matrix)
        || !valid_dimensions(width, height)
        || !mouse.iter().all(|value| value.is_finite())
        || !max_distance.is_finite()
        || max_distance < 0.0
    {
        return ERR_INPUT;
    }
    if count == 0 {
        return 0;
    }
    let points = slice::from_raw_parts(points_ptr, point_values);
    let mut best_index = u64::MAX;
    let mut best_distance = max_distance;
    for (index, point) in points.chunks_exact(3).enumerate() {
        if let Some((x, y)) = project_display_point_f64(matrix, point, width, height) {
            let dx = x - mouse[0];
            let dy = y - mouse[1];
            let distance = (dx * dx + dy * dy).sqrt();
            if distance.is_finite() && distance <= best_distance {
                best_index = index as u64;
                best_distance = distance;
            }
        }
    }
    if best_index == u64::MAX {
        return 0;
    }
    *out_point_index_ptr = best_index;
    *out_distance_ptr = best_distance;
    1
}

/// Classify each projectable f64 display point against an inclusive screen rectangle.
///
/// One ordered byte is written per input point: one when the projected point lies
/// on or inside the rectangle, zero otherwise. The return value is zero on success
/// and negative for invalid input.
///
/// # Safety
///
/// The caller must provide 16 readable matrix values, `count * 3` readable point
/// values, four readable rectangle values, and `count` writable result bytes. Point
/// and output pointers may be null only when `count == 0`; all buffers must be
/// correctly aligned and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_projected_points_rect_query_f64(
    matrix_ptr: *const f64,
    points_ptr: *const f64,
    count: usize,
    width: f64,
    height: f64,
    rect_ptr: *const f64,
    out_inside_ptr: *mut u8,
) -> i32 {
    if count > 0 && out_inside_ptr.is_null() {
        return ERR_NULL;
    }
    let point_values = match count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) && valid_slice_len::<u8>(count) => value,
        _ => return ERR_SIZE,
    };
    if count > 0 {
        ptr::write_bytes(out_inside_ptr, 0, count);
    }
    if matrix_ptr.is_null() || rect_ptr.is_null() || (count > 0 && points_ptr.is_null()) {
        return ERR_NULL;
    }
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    let rect_slice = slice::from_raw_parts(rect_ptr, 4);
    let rect = [rect_slice[0], rect_slice[1], rect_slice[2], rect_slice[3]];
    if count == 0 {
        return if valid_matrix(matrix) && valid_dimensions(width, height) && valid_rect(rect) {
            0
        } else {
            ERR_INPUT
        };
    }
    if !valid_matrix(matrix) || !valid_dimensions(width, height) || !valid_rect(rect) {
        return ERR_INPUT;
    }
    let points = slice::from_raw_parts(points_ptr, point_values);
    let out_inside = slice::from_raw_parts_mut(out_inside_ptr, count);
    for (index, point) in points.chunks_exact(3).enumerate() {
        if let Some((x, y)) = project_display_point_f64(matrix, point, width, height) {
            out_inside[index] =
                u8::from(rect[0] <= x && x <= rect[2] && rect[1] <= y && y <= rect[3]);
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    const IDENTITY: [f64; 16] = [
        1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0,
    ];

    #[test]
    fn projection_maps_ndc_to_pixels() {
        assert_eq!(
            project_point(&IDENTITY, -1.0, -1.0, 0.0, 800.0, 600.0),
            Some((0.0, 0.0))
        );
        assert_eq!(
            project_point(&IDENTITY, 1.0, 1.0, 0.0, 800.0, 600.0),
            Some((800.0, 600.0))
        );
    }

    #[test]
    fn projection_rejects_eye_plane_and_non_finite_points() {
        let mut matrix = IDENTITY;
        matrix[15] = 0.0;
        assert_eq!(project_point(&matrix, 0.0, 0.0, 0.0, 800.0, 600.0), None);
        assert_eq!(
            project_point(&IDENTITY, f64::NAN, 0.0, 0.0, 800.0, 600.0),
            None
        );
    }

    #[test]
    fn inclusive_segment_predicate_handles_edges_and_empty_interiors() {
        let rect = [4.0, 4.0, 6.0, 6.0];
        assert!(segment_crosses_rect((0.0, 5.0), (10.0, 5.0), rect));
        assert!(segment_crosses_rect((4.0, 4.0), (4.0, 4.0), rect));
        assert!(!segment_crosses_rect((0.0, 0.0), (10.0, 0.0), rect));
        assert!(!segment_crosses_rect((0.0, 0.0), (0.0, 10.0), rect));
    }

    #[test]
    fn combined_projection_preserves_both_endpoints_visible_rule() {
        let matrix: [f64; 16] = [
            1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
        ];
        let segment = [0.0_f32, 0.0, 1.0, 0.0, 0.0, -1.0];
        let rect = [0.0, 0.0, 800.0, 600.0];
        let result = unsafe {
            narwhal_any_projected_segment_crosses_rect_f32(
                matrix.as_ptr(),
                segment.as_ptr(),
                1,
                800.0,
                600.0,
                rect.as_ptr(),
            )
        };
        assert_eq!(result, 0);
    }

    #[test]
    fn classified_query_distinguishes_miss_from_no_projectable_evidence() {
        let rect = [400.0, 300.0, 410.0, 310.0];
        let miss = [-1.0_f32, -1.0, 0.0, -0.9, -0.9, 0.0];
        let mut evidence = 0_u8;
        let result = unsafe {
            narwhal_projected_segments_rect_query_f32(
                IDENTITY.as_ptr(),
                miss.as_ptr(),
                1,
                800.0,
                600.0,
                rect.as_ptr(),
                &mut evidence,
            )
        };
        assert_eq!((result, evidence), (0, 1));

        let no_evidence = [0.0_f32, 0.0, f32::NAN, 1.0, 1.0, 0.0];
        evidence = 1;
        let result = unsafe {
            narwhal_projected_segments_rect_query_f32(
                IDENTITY.as_ptr(),
                no_evidence.as_ptr(),
                1,
                800.0,
                600.0,
                rect.as_ptr(),
                &mut evidence,
            )
        };
        assert_eq!((result, evidence), (0, 0));
    }

    #[test]
    fn batch_query_returns_one_exact_status_per_curve() {
        let rect = [390.0, 290.0, 410.0, 310.0];
        let segments = [
            -1.0_f32,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            -1.0,
            -1.0,
            0.0,
            -0.9,
            -0.9,
            0.0,
            0.0,
            0.0,
            f32::NAN,
            1.0,
            1.0,
            0.0,
        ];
        let offsets = [0_u64, 1, 2, 3];
        let mut statuses = [9_u8; 3];
        let result = unsafe {
            narwhal_projected_curves_rect_query_f32(
                IDENTITY.as_ptr(),
                segments.as_ptr(),
                3,
                offsets.as_ptr(),
                3,
                800.0,
                600.0,
                rect.as_ptr(),
                statuses.as_mut_ptr(),
            )
        };
        assert_eq!(result, 0);
        assert_eq!(statuses, [STATUS_HIT, STATUS_MISS, STATUS_NO_EVIDENCE]);

        let invalid_offsets = [0_u64, 2, 1, 3];
        statuses.fill(9);
        let result = unsafe {
            narwhal_projected_curves_rect_query_f32(
                IDENTITY.as_ptr(),
                segments.as_ptr(),
                3,
                invalid_offsets.as_ptr(),
                3,
                800.0,
                600.0,
                rect.as_ptr(),
                statuses.as_mut_ptr(),
            )
        };
        assert_eq!(result, ERR_INPUT);
        assert_eq!(statuses, [STATUS_NO_EVIDENCE; 3]);
    }

    #[test]
    fn nearest_curve_clips_eye_plane_and_prefers_later_ties() {
        let segments = [
            -1.0_f32, 0.0, 0.0, 1.0, 0.0, 0.0, -1.0, 0.0, 0.0, 1.0, 0.0, 0.0,
        ];
        let offsets = [0_u64, 1, 2];
        let mouse = [400.0, 300.0];
        let mut index = u64::MAX;
        let mut distance = f64::INFINITY;
        let result = unsafe {
            narwhal_nearest_projected_curve_f32(
                IDENTITY.as_ptr(),
                segments.as_ptr(),
                2,
                offsets.as_ptr(),
                2,
                800.0,
                600.0,
                mouse.as_ptr(),
                1.0,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!((result, index, distance), (1, 1, 0.0));

        let eye_matrix: [f64; 16] = [
            1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
        ];
        let crossing = [0.0_f32, 0.0, 1.0, 0.0, 0.0, -1.0];
        let one_curve = [0_u64, 1];
        index = u64::MAX;
        distance = f64::INFINITY;
        let result = unsafe {
            narwhal_nearest_projected_curve_f32(
                eye_matrix.as_ptr(),
                crossing.as_ptr(),
                1,
                one_curve.as_ptr(),
                1,
                800.0,
                600.0,
                mouse.as_ptr(),
                1.0,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!((result, index), (1, 0));
        assert!(distance < 1.0e-6);

        let finite_and_nan = [
            -1.0_f32,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            f32::NAN,
            0.0,
            0.0,
            1.0,
            1.0,
            0.0,
        ];
        let mixed_curve = [0_u64, 2];
        index = u64::MAX;
        distance = f64::INFINITY;
        let result = unsafe {
            narwhal_nearest_projected_curve_f32(
                IDENTITY.as_ptr(),
                finite_and_nan.as_ptr(),
                2,
                mixed_curve.as_ptr(),
                1,
                800.0,
                600.0,
                mouse.as_ptr(),
                1.0,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!((result, index, distance), (1, 0, 0.0));
    }

    #[test]
    fn point_queries_preserve_later_ties_and_inclusive_edges() {
        let points = [0.0_f64, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
        let mouse = [400.0, 300.0];
        let mut index = u64::MAX;
        let mut distance = f64::INFINITY;
        let result = unsafe {
            narwhal_nearest_projected_point_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                3,
                800.0,
                600.0,
                mouse.as_ptr(),
                1.0,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!((result, index, distance), (1, 1, 0.0));
        let precise = [0.123_456_789_123_f64, -0.456_789_123_456, 0.2];
        let matrix = [
            1.2, 0.1, 0.0, 0.2, 0.0, 1.1, 0.1, -0.1, 0.0, 0.0, 1.0, 0.0, 0.15, -0.05, 0.2, 1.0,
        ];
        let projected = project_display_point_f64(&matrix, &precise, 800.0, 600.0);
        assert!(projected.is_some());
        assert_ne!(
            projected,
            project_point(&matrix, precise[0], precise[1], precise[2], 800.0, 600.0)
        );

        let rect = [400.0, 300.0, 800.0, 600.0];
        let mut inside = [9_u8; 3];
        let result = unsafe {
            narwhal_projected_points_rect_query_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                3,
                800.0,
                600.0,
                rect.as_ptr(),
                inside.as_mut_ptr(),
            )
        };
        assert_eq!(result, 0);
        assert_eq!(inside, [1, 1, 1]);

        let invalid_rect = [800.0, 300.0, 400.0, 600.0];
        inside.fill(9);
        let result = unsafe {
            narwhal_projected_points_rect_query_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                3,
                800.0,
                600.0,
                invalid_rect.as_ptr(),
                inside.as_mut_ptr(),
            )
        };
        assert_eq!(result, ERR_INPUT);
        assert_eq!(inside, [0, 0, 0]);
    }
}
