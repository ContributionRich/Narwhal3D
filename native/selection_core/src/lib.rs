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
fn double_length_mul(x: f64, y: f64) -> (f64, f64) {
    let product = x * y;
    (product, x.mul_add(y, -product))
}

#[inline]
fn double_length_fast_sum(a: f64, b: f64) -> (f64, f64) {
    let sum = a + b;
    (sum, (a - sum) + b)
}

#[inline]
fn frexp_exponent(value: f64) -> i32 {
    let bits = value.to_bits() & 0x7fff_ffff_ffff_ffff;
    let exponent = ((bits >> 52) & 0x7ff) as i32;
    if exponent != 0 {
        return exponent - 1022;
    }
    let mantissa = bits & ((1_u64 << 52) - 1);
    (63 - mantissa.leading_zeros() as i32) - 1073
}

#[inline]
fn exact_power_of_two(exponent: i32) -> f64 {
    if exponent >= -1022 {
        f64::from_bits(((exponent + 1023) as u64) << 52)
    } else {
        f64::from_bits(1_u64 << (exponent + 1074))
    }
}

fn python_vector_norm2(mut values: [f64; 2], maximum: f64) -> f64 {
    if maximum.is_infinite() || maximum == 0.0 {
        return maximum;
    }
    let maximum_exponent = frexp_exponent(maximum);
    if maximum_exponent < -1023 {
        for value in &mut values {
            *value /= f64::MIN_POSITIVE;
        }
        return f64::MIN_POSITIVE * python_vector_norm2(values, maximum / f64::MIN_POSITIVE);
    }
    let scale = exact_power_of_two(-maximum_exponent);
    let mut compensated_sum = 1.0_f64;
    let mut fraction1 = 0.0_f64;
    let mut fraction2 = 0.0_f64;
    for mut value in values {
        value *= scale;
        let product = double_length_mul(value, value);
        let sum = double_length_fast_sum(compensated_sum, product.0);
        compensated_sum = sum.0;
        fraction1 += product.1;
        fraction2 += sum.1;
    }
    let mut hypotenuse = (compensated_sum - 1.0 + (fraction1 + fraction2)).sqrt();
    let product = double_length_mul(-hypotenuse, hypotenuse);
    let sum = double_length_fast_sum(compensated_sum, product.0);
    compensated_sum = sum.0;
    fraction1 += product.1;
    fraction2 += sum.1;
    let correction = compensated_sum - 1.0 + (fraction1 + fraction2);
    hypotenuse += correction / (2.0 * hypotenuse);
    hypotenuse / scale
}

#[inline]
fn python_hypot2(x: f64, y: f64) -> f64 {
    let values = [x.abs(), y.abs()];
    python_vector_norm2(values, values[0].max(values[1]))
}

#[inline]
fn screen_near_parameter(mouse: [f64; 2], a: [f64; 2], b: [f64; 2]) -> (f64, f64) {
    let dx = b[0] - a[0];
    let dy = b[1] - a[1];
    let denominator = dx * dx + dy * dy;
    let parameter = if denominator <= 0.0 {
        0.0
    } else {
        let raw = ((mouse[0] - a[0]) * dx + (mouse[1] - a[1]) * dy) / denominator;
        if raw.is_nan() {
            1.0
        } else {
            raw.clamp(0.0, 1.0)
        }
    };
    let distance = python_hypot2(
        a[0] + dx * parameter - mouse[0],
        a[1] + dy * parameter - mouse[1],
    );
    (parameter, distance)
}

fn screen_segment_aabb_could_beat(mouse: [f64; 2], a: [f64; 2], b: [f64; 2], limit: f64) -> bool {
    let axis_could_beat = |value: f64, first: f64, second: f64| {
        let delta = second - first;
        // The exact scorer reconstructs `first + parameter * delta`. Catastrophic
        // cancellation can make its parameter=1 endpoint differ from `second`; in
        // that case the mathematical endpoint AABB is not a safe floating envelope.
        if first + delta != second {
            return true;
        }
        let low = first.min(second);
        let high = first.max(second);
        let distance = if value < low {
            low - value
        } else if value > high {
            value - high
        } else {
            0.0
        };
        let scale = first.abs() + second.abs() + delta.abs() + value.abs() + limit.abs();
        let slack = 16.0 * f64::EPSILON * scale;
        distance <= limit + slack
    };
    axis_could_beat(mouse[0], a[0], b[0]) && axis_could_beat(mouse[1], a[1], b[1])
}

#[cfg(test)]
static SCREEN_DISTANCE_CALLS: std::sync::atomic::AtomicUsize =
    std::sync::atomic::AtomicUsize::new(0);

fn screen_point_segment_distance(mouse: [f64; 2], a: [f64; 2], b: [f64; 2]) -> f64 {
    #[cfg(test)]
    SCREEN_DISTANCE_CALLS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let dx = b[0] - a[0];
    let dy = b[1] - a[1];
    let denominator = dx * dx + dy * dy;
    if denominator < 1.0e-9 {
        return python_hypot2(mouse[0] - a[0], mouse[1] - a[1]);
    }
    let parameter = ((mouse[0] - a[0]) * dx + (mouse[1] - a[1]) * dy) / denominator;
    // Python spells this `max(0.0, min(1.0, parameter))`. With NaN as the
    // second `min` argument, its stable first-argument comparison returns 1.0.
    let parameter = if parameter.is_nan() {
        1.0
    } else {
        parameter.clamp(0.0, 1.0)
    };
    python_hypot2(
        mouse[0] - (a[0] + parameter * dx),
        mouse[1] - (a[1] + parameter * dy),
    )
}

#[inline]
fn selection_clip_point_f64(matrix: &[f64], point: &[f64]) -> [f64; 4] {
    let x = point[0];
    let y = point[1];
    let z = point[2];
    [
        matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
        matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
        matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
        matrix[12] * x + matrix[13] * y + matrix[14] * z + matrix[15],
    ]
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

/// Project packed f64 display-marker points with Blender's mixed-precision math.
///
/// `out_visible[i]` is one when `out_xy[2*i..2*i+2]` is valid and zero when the
/// point is unprojectable. Hidden output coordinates are NaN. Source coordinates
/// stay f64; only the projection follows mathutils' f32/f64 operation sequence.
///
/// # Safety
///
/// The caller must provide 16 readable matrix values, `count * 3` readable point
/// values, `count * 2` writable pixel values, and `count` writable visibility bytes.
/// Point/output pointers may be null only when `count == 0`; all buffers must be
/// correctly aligned and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_project_display_points_f64(
    matrix_ptr: *const f64,
    points_ptr: *const f64,
    count: usize,
    width: f64,
    height: f64,
    out_xy_ptr: *mut f64,
    out_visible_ptr: *mut u8,
) -> i32 {
    if count > 0 && (out_xy_ptr.is_null() || out_visible_ptr.is_null()) {
        return ERR_NULL;
    }
    let point_values = match count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let output_values = match count.checked_mul(2) {
        Some(value) if valid_slice_len::<f64>(value) && valid_slice_len::<u8>(count) => value,
        _ => return ERR_SIZE,
    };
    if count > 0 {
        ptr::write_bytes(out_visible_ptr, 0, count);
        for index in 0..output_values {
            ptr::write(out_xy_ptr.add(index), f64::NAN);
        }
    }
    if matrix_ptr.is_null() || (count > 0 && points_ptr.is_null()) {
        return ERR_NULL;
    }
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
    for (index, point) in points.chunks_exact(3).enumerate() {
        if let Some((x, y)) = project_display_point_f64(matrix, point, width, height) {
            out_xy[index * 2] = x;
            out_xy[index * 2 + 1] = y;
            out_visible[index] = 1;
        }
    }
    0
}

/// Return the nearest cached screen-space polyline with command-hover semantics.
///
/// Curves are represented by concatenated f64 pixel points and monotonic u64 point
/// offsets. A segment participates only when both endpoint visibility bytes are one.
/// The return value is one for a strict hit inside `max_distance`, zero for no hit,
/// and negative for invalid input. Exact ties keep the earlier curve, matching the
/// command-hover loop's strict `<` update rule.
///
/// # Safety
///
/// The caller must provide `point_count * 2` readable pixel values, `point_count`
/// readable visibility bytes, `curve_count + 1` readable offsets, two readable mouse
/// coordinates, and writable curve-index/distance outputs. Point buffers may be null
/// only when `point_count == 0`; offsets may be null only when `curve_count == 0`.
/// All buffers must be correctly aligned and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_nearest_screen_polyline_f64(
    pixels_ptr: *const f64,
    visible_ptr: *const u8,
    point_count: usize,
    offsets_ptr: *const u64,
    curve_count: usize,
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
    if mouse_ptr.is_null()
        || (point_count > 0 && (pixels_ptr.is_null() || visible_ptr.is_null()))
        || (curve_count > 0 && offsets_ptr.is_null())
    {
        return ERR_NULL;
    }
    let pixel_values = match point_count.checked_mul(2) {
        Some(value) if valid_slice_len::<f64>(value) && valid_slice_len::<u8>(point_count) => value,
        _ => return ERR_SIZE,
    };
    let offset_count = match curve_count.checked_add(1) {
        Some(value) if valid_slice_len::<u64>(value) => value,
        _ => return ERR_SIZE,
    };
    let mouse_slice = slice::from_raw_parts(mouse_ptr, 2);
    let mouse = [mouse_slice[0], mouse_slice[1]];
    if !mouse.iter().all(|value| value.is_finite())
        || !max_distance.is_finite()
        || max_distance < 0.0
    {
        return ERR_INPUT;
    }
    if curve_count == 0 {
        return if point_count == 0 { 0 } else { ERR_INPUT };
    }
    let point_count_u64 = match u64::try_from(point_count) {
        Ok(value) => value,
        Err(_) => return ERR_SIZE,
    };
    let pixels = if pixel_values == 0 {
        &[]
    } else {
        slice::from_raw_parts(pixels_ptr, pixel_values)
    };
    let visible = if point_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(visible_ptr, point_count)
    };
    for (index, visibility) in visible.iter().copied().enumerate() {
        let point = &pixels[index * 2..index * 2 + 2];
        if visibility > 1
            || (visibility == 1 && !point.iter().all(|value| value.is_finite()))
            || (visibility == 0 && !point.iter().all(|value| value.is_nan()))
        {
            return ERR_INPUT;
        }
    }
    let offsets = slice::from_raw_parts(offsets_ptr, offset_count);
    if offsets[0] != 0 || offsets[curve_count] != point_count_u64 {
        return ERR_INPUT;
    }
    for pair in offsets.windows(2) {
        if pair[0] > pair[1] || pair[1] > point_count_u64 {
            return ERR_INPUT;
        }
    }

    let mut best_index = u64::MAX;
    let mut best_distance = max_distance;
    for curve_index in 0..curve_count {
        let start = offsets[curve_index] as usize;
        let end = offsets[curve_index + 1] as usize;
        if end.saturating_sub(start) < 2 {
            continue;
        }
        let mut curve_distance = f64::INFINITY;
        for point_index in start..end - 1 {
            if visible[point_index] == 0 || visible[point_index + 1] == 0 {
                continue;
            }
            let a = [pixels[point_index * 2], pixels[point_index * 2 + 1]];
            let b = [
                pixels[(point_index + 1) * 2],
                pixels[(point_index + 1) * 2 + 1],
            ];
            let limit = best_distance.min(curve_distance);
            if !screen_segment_aabb_could_beat(mouse, a, b, limit) {
                continue;
            }
            curve_distance = curve_distance.min(screen_point_segment_distance(mouse, a, b));
        }
        if curve_distance < best_distance {
            best_index = curve_index as u64;
            best_distance = curve_distance;
        }
    }
    if best_index == u64::MAX {
        return 0;
    }
    *out_curve_index_ptr = best_index;
    *out_distance_ptr = best_distance;
    1
}

/// Project grouped f64 world polylines and return each one's nearest screen segment.
///
/// Outputs are one local segment index, clamped segment parameter, CPython-matched
/// pixel distance and validity byte per input curve. This is display-only coarse
/// search; callers retain exact curve refinement and all snap policy.
///
/// # Safety
///
/// The caller must provide 16 readable matrix values, `point_count * 3` readable
/// coordinates, `curve_count + 1` readable offsets, two readable mouse coordinates,
/// and `curve_count` writable values in each output. Point buffers may be null only
/// for zero points; curve outputs may be null only for zero curves. All buffers must
/// be correctly aligned and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_nearest_projected_polylines_f64(
    matrix_ptr: *const f64,
    points_ptr: *const f64,
    point_count: usize,
    offsets_ptr: *const u64,
    curve_count: usize,
    width: f64,
    height: f64,
    mouse_ptr: *const f64,
    out_segment_ptr: *mut u64,
    out_parameter_ptr: *mut f64,
    out_distance_ptr: *mut f64,
    out_valid_ptr: *mut u8,
) -> i32 {
    if curve_count > 0
        && (out_segment_ptr.is_null()
            || out_parameter_ptr.is_null()
            || out_distance_ptr.is_null()
            || out_valid_ptr.is_null())
    {
        return ERR_NULL;
    }
    let point_values = match point_count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    if !valid_slice_len::<u64>(curve_count)
        || !valid_slice_len::<f64>(curve_count)
        || !valid_slice_len::<u8>(curve_count)
    {
        return ERR_SIZE;
    }
    let offset_count = match curve_count.checked_add(1) {
        Some(value) if valid_slice_len::<u64>(value) => value,
        _ => return ERR_SIZE,
    };
    if curve_count > 0 {
        for index in 0..curve_count {
            ptr::write(out_segment_ptr.add(index), u64::MAX);
            ptr::write(out_parameter_ptr.add(index), f64::NAN);
            ptr::write(out_distance_ptr.add(index), f64::INFINITY);
            ptr::write(out_valid_ptr.add(index), 0);
        }
    }
    if matrix_ptr.is_null()
        || mouse_ptr.is_null()
        || (point_count > 0 && points_ptr.is_null())
        || (curve_count > 0 && offsets_ptr.is_null())
    {
        return ERR_NULL;
    }
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    let mouse_slice = slice::from_raw_parts(mouse_ptr, 2);
    let mouse = [mouse_slice[0], mouse_slice[1]];
    if !valid_matrix(matrix)
        || !valid_dimensions(width, height)
        || !mouse.iter().all(|value| value.is_finite())
    {
        return ERR_INPUT;
    }
    if curve_count == 0 {
        return if point_count == 0 { 0 } else { ERR_INPUT };
    }
    let point_count_u64 = match u64::try_from(point_count) {
        Ok(value) => value,
        Err(_) => return ERR_SIZE,
    };
    let points = if point_values == 0 {
        &[]
    } else {
        slice::from_raw_parts(points_ptr, point_values)
    };
    if !points.iter().all(|value| value.is_finite()) {
        return ERR_INPUT;
    }
    let offsets = slice::from_raw_parts(offsets_ptr, offset_count);
    if offsets[0] != 0 || offsets[curve_count] != point_count_u64 {
        return ERR_INPUT;
    }
    for pair in offsets.windows(2) {
        if pair[0] > pair[1] || pair[1] > point_count_u64 {
            return ERR_INPUT;
        }
    }
    let out_segments = slice::from_raw_parts_mut(out_segment_ptr, curve_count);
    let out_parameters = slice::from_raw_parts_mut(out_parameter_ptr, curve_count);
    let out_distances = slice::from_raw_parts_mut(out_distance_ptr, curve_count);
    let out_valid = slice::from_raw_parts_mut(out_valid_ptr, curve_count);
    for curve_index in 0..curve_count {
        let start = offsets[curve_index] as usize;
        let end = offsets[curve_index + 1] as usize;
        if end.saturating_sub(start) < 2 {
            continue;
        }
        let mut previous =
            project_display_point_f64(matrix, &points[start * 3..start * 3 + 3], width, height);
        let mut best_segment = u64::MAX;
        let mut best_parameter = f64::NAN;
        let mut best_distance = f64::INFINITY;
        for point_index in start..end - 1 {
            let next = project_display_point_f64(
                matrix,
                &points[(point_index + 1) * 3..(point_index + 1) * 3 + 3],
                width,
                height,
            );
            if let (Some(a), Some(b)) = (previous, next) {
                let (parameter, distance) = screen_near_parameter(mouse, [a.0, a.1], [b.0, b.1]);
                if distance < best_distance {
                    best_segment = (point_index - start) as u64;
                    best_parameter = parameter;
                    best_distance = distance;
                }
            }
            previous = next;
        }
        if best_segment != u64::MAX {
            out_segments[curve_index] = best_segment;
            out_parameters[curve_index] = best_parameter;
            out_distances[curve_index] = best_distance;
            out_valid[curve_index] = 1;
        }
    }
    0
}

/// Reduce grouped homogeneous clip coordinates to screen-space object rectangles.
///
/// Each object owns `offsets[i]..offsets[i + 1]` clip rows. Rows with non-finite
/// components, non-positive W, or non-finite pixels are ignored. Point-object flags
/// select the first visible pixel as a zero-area rectangle; ordinary objects reduce
/// all visible pixels to min/max bounds. Invalid objects retain NaN output and zero
/// validity. This export receives display-only clip copies, never document geometry.
///
/// # Safety
///
/// The caller must provide `point_count * 4` readable clip values,
/// `object_count + 1` readable offsets, `object_count` readable point flags,
/// `object_count * 4` writable rectangle values, and `object_count` writable validity
/// bytes. Point input may be null only for zero points; object buffers may be null only
/// for zero objects. All buffers must be correctly aligned and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_screen_boxes_from_clip_f64(
    clip_ptr: *const f64,
    point_count: usize,
    offsets_ptr: *const u64,
    object_count: usize,
    point_flags_ptr: *const u8,
    width: f64,
    height: f64,
    out_boxes_ptr: *mut f64,
    out_valid_ptr: *mut u8,
) -> i32 {
    if object_count > 0 && (out_boxes_ptr.is_null() || out_valid_ptr.is_null()) {
        return ERR_NULL;
    }
    let clip_values = match point_count.checked_mul(4) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let output_values = match object_count.checked_mul(4) {
        Some(value) if valid_slice_len::<f64>(value) && valid_slice_len::<u8>(object_count) => {
            value
        }
        _ => return ERR_SIZE,
    };
    let offset_count = match object_count.checked_add(1) {
        Some(value) if valid_slice_len::<u64>(value) => value,
        _ => return ERR_SIZE,
    };
    if object_count > 0 {
        ptr::write_bytes(out_valid_ptr, 0, object_count);
        for index in 0..output_values {
            ptr::write(out_boxes_ptr.add(index), f64::NAN);
        }
    }
    if (point_count > 0 && clip_ptr.is_null())
        || (object_count > 0 && (offsets_ptr.is_null() || point_flags_ptr.is_null()))
    {
        return ERR_NULL;
    }
    if !valid_dimensions(width, height) {
        return ERR_INPUT;
    }
    if object_count == 0 {
        return if point_count == 0 { 0 } else { ERR_INPUT };
    }
    let point_count_u64 = match u64::try_from(point_count) {
        Ok(value) => value,
        Err(_) => return ERR_SIZE,
    };
    let clip = if clip_values == 0 {
        &[]
    } else {
        slice::from_raw_parts(clip_ptr, clip_values)
    };
    let offsets = slice::from_raw_parts(offsets_ptr, offset_count);
    let point_flags = slice::from_raw_parts(point_flags_ptr, object_count);
    if offsets[0] != 0 || offsets[object_count] != point_count_u64 {
        return ERR_INPUT;
    }
    for pair in offsets.windows(2) {
        if pair[0] > pair[1] || pair[1] > point_count_u64 {
            return ERR_INPUT;
        }
    }
    if point_flags.iter().any(|flag| *flag > 1) {
        return ERR_INPUT;
    }
    let out_boxes = slice::from_raw_parts_mut(out_boxes_ptr, output_values);
    let out_valid = slice::from_raw_parts_mut(out_valid_ptr, object_count);
    for object_index in 0..object_count {
        let start = offsets[object_index] as usize;
        let end = offsets[object_index + 1] as usize;
        let mut first = true;
        let mut bounds = [f64::NAN; 4];
        for point_index in start..end {
            let point = &clip[point_index * 4..point_index * 4 + 4];
            if !point.iter().all(|value| value.is_finite()) || point[3] <= 0.0 {
                continue;
            }
            let pixel_x = (point[0] / point[3] + 1.0) * width * 0.5;
            let pixel_y = (point[1] / point[3] + 1.0) * height * 0.5;
            if !pixel_x.is_finite() || !pixel_y.is_finite() {
                continue;
            }
            if first {
                bounds = [pixel_x, pixel_y, pixel_x, pixel_y];
                first = false;
                if point_flags[object_index] == 1 {
                    break;
                }
            } else {
                bounds[0] = bounds[0].min(pixel_x);
                bounds[1] = bounds[1].min(pixel_y);
                bounds[2] = bounds[2].max(pixel_x);
                bounds[3] = bounds[3].max(pixel_y);
            }
        }
        if !first {
            out_boxes[object_index * 4..object_index * 4 + 4].copy_from_slice(&bounds);
            out_valid[object_index] = 1;
        }
    }
    0
}

/// Project grouped f64 world display samples and reduce them directly to screen boxes.
///
/// This is the fused form of `narwhal_screen_boxes_from_clip_f64`: each point is
/// multiplied by the supplied f64 4x4 matrix in fixed row/term order, then reduced
/// under the same visibility, point-flag, and canonical rectangle rules. Inputs are
/// copied display samples only; this export never owns or mutates document geometry.
///
/// # Safety
///
/// The caller must provide 16 readable matrix values, `point_count * 3` readable world
/// values, `object_count + 1` readable offsets, `object_count` readable point flags,
/// `object_count * 4` writable rectangle values, and `object_count` writable validity
/// bytes. Point input may be null only for zero points; object buffers may be null only
/// for zero objects. All buffers must be correctly aligned and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_screen_boxes_from_world_f64(
    matrix_ptr: *const f64,
    points_ptr: *const f64,
    point_count: usize,
    offsets_ptr: *const u64,
    object_count: usize,
    point_flags_ptr: *const u8,
    width: f64,
    height: f64,
    out_boxes_ptr: *mut f64,
    out_valid_ptr: *mut u8,
) -> i32 {
    if object_count > 0 && (out_boxes_ptr.is_null() || out_valid_ptr.is_null()) {
        return ERR_NULL;
    }
    let point_values = match point_count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let output_values = match object_count.checked_mul(4) {
        Some(value) if valid_slice_len::<f64>(value) && valid_slice_len::<u8>(object_count) => {
            value
        }
        _ => return ERR_SIZE,
    };
    let offset_count = match object_count.checked_add(1) {
        Some(value) if valid_slice_len::<u64>(value) => value,
        _ => return ERR_SIZE,
    };
    if object_count > 0 {
        ptr::write_bytes(out_valid_ptr, 0, object_count);
        for index in 0..output_values {
            ptr::write(out_boxes_ptr.add(index), f64::NAN);
        }
    }
    if matrix_ptr.is_null()
        || (point_count > 0 && points_ptr.is_null())
        || (object_count > 0 && (offsets_ptr.is_null() || point_flags_ptr.is_null()))
    {
        return ERR_NULL;
    }
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    if !valid_matrix(matrix) || !valid_dimensions(width, height) {
        return ERR_INPUT;
    }
    if object_count == 0 {
        return if point_count == 0 { 0 } else { ERR_INPUT };
    }
    let point_count_u64 = match u64::try_from(point_count) {
        Ok(value) => value,
        Err(_) => return ERR_SIZE,
    };
    let points = if point_values == 0 {
        &[]
    } else {
        slice::from_raw_parts(points_ptr, point_values)
    };
    if !points.iter().all(|value| value.is_finite()) {
        return ERR_INPUT;
    }
    let offsets = slice::from_raw_parts(offsets_ptr, offset_count);
    let point_flags = slice::from_raw_parts(point_flags_ptr, object_count);
    if offsets[0] != 0 || offsets[object_count] != point_count_u64 {
        return ERR_INPUT;
    }
    for pair in offsets.windows(2) {
        if pair[0] > pair[1] || pair[1] > point_count_u64 {
            return ERR_INPUT;
        }
    }
    if point_flags.iter().any(|flag| *flag > 1) {
        return ERR_INPUT;
    }
    let out_boxes = slice::from_raw_parts_mut(out_boxes_ptr, output_values);
    let out_valid = slice::from_raw_parts_mut(out_valid_ptr, object_count);
    for object_index in 0..object_count {
        let start = offsets[object_index] as usize;
        let end = offsets[object_index + 1] as usize;
        let mut first = true;
        let mut bounds = [f64::NAN; 4];
        for point_index in start..end {
            let clip =
                selection_clip_point_f64(matrix, &points[point_index * 3..point_index * 3 + 3]);
            if !clip.iter().all(|value| value.is_finite()) || clip[3] <= 0.0 {
                continue;
            }
            let pixel_x = (clip[0] / clip[3] + 1.0) * width * 0.5;
            let pixel_y = (clip[1] / clip[3] + 1.0) * height * 0.5;
            if !pixel_x.is_finite() || !pixel_y.is_finite() {
                continue;
            }
            if first {
                bounds = [pixel_x, pixel_y, pixel_x, pixel_y];
                first = false;
                if point_flags[object_index] == 1 {
                    break;
                }
            } else {
                bounds[0] = bounds[0].min(pixel_x);
                bounds[1] = bounds[1].min(pixel_y);
                bounds[2] = bounds[2].max(pixel_x);
                bounds[3] = bounds[3].max(pixel_y);
            }
        }
        if !first {
            out_boxes[object_index * 4..object_index * 4 + 4].copy_from_slice(&bounds);
            out_valid[object_index] = 1;
        }
    }
    0
}

/// Project f64 world-axis-aligned boxes to cached screen rectangles.
///
/// The output for each source box is `(x0, y0, x1, y1)` plus separate unbounded and
/// all-behind bytes. A box crossing the eye plane remains conservatively unbounded;
/// only a box whose eight corners are clearly behind can be culled by the caller.
///
/// # Safety
///
/// The caller must provide 16 readable matrix values, `box_count * 3` readable lower
/// and upper coordinates, `box_count * 4` writable rectangle values, and two
/// `box_count` writable byte arrays. Box buffers may be null only when `box_count` is
/// zero. All buffers must be correctly aligned and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_screen_rects_from_aabbs_f64(
    matrix_ptr: *const f64,
    lower_ptr: *const f64,
    upper_ptr: *const f64,
    box_count: usize,
    width: f64,
    height: f64,
    out_rects_ptr: *mut f64,
    out_unbounded_ptr: *mut u8,
    out_all_behind_ptr: *mut u8,
) -> i32 {
    if box_count > 0
        && (out_rects_ptr.is_null() || out_unbounded_ptr.is_null() || out_all_behind_ptr.is_null())
    {
        return ERR_NULL;
    }
    let coordinate_values = match box_count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let rectangle_values = match box_count.checked_mul(4) {
        Some(value) if valid_slice_len::<f64>(value) && valid_slice_len::<u8>(box_count) => value,
        _ => return ERR_SIZE,
    };
    if box_count > 0 {
        ptr::write_bytes(out_unbounded_ptr, 0, box_count);
        ptr::write_bytes(out_all_behind_ptr, 0, box_count);
        for index in 0..rectangle_values {
            ptr::write(out_rects_ptr.add(index), f64::NAN);
        }
    }
    if matrix_ptr.is_null() || (box_count > 0 && (lower_ptr.is_null() || upper_ptr.is_null())) {
        return ERR_NULL;
    }
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    if !valid_matrix(matrix) || !valid_dimensions(width, height) {
        return ERR_INPUT;
    }
    if box_count == 0 {
        return 0;
    }
    let lower = slice::from_raw_parts(lower_ptr, coordinate_values);
    let upper = slice::from_raw_parts(upper_ptr, coordinate_values);
    if !lower
        .iter()
        .chain(upper.iter())
        .all(|value| value.is_finite())
    {
        return ERR_INPUT;
    }
    for index in 0..coordinate_values {
        if lower[index] > upper[index] {
            return ERR_INPUT;
        }
    }
    let out_rects = slice::from_raw_parts_mut(out_rects_ptr, rectangle_values);
    let out_unbounded = slice::from_raw_parts_mut(out_unbounded_ptr, box_count);
    let out_all_behind = slice::from_raw_parts_mut(out_all_behind_ptr, box_count);
    let half_width = width / 2.0;
    let half_height = height / 2.0;
    for box_index in 0..box_count {
        let base = box_index * 3;
        let mut bounds = [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ];
        let mut behind_count = 0usize;
        let mut any_nonfront = false;
        for corner in 0..8usize {
            let x = if corner & 4 == 0 {
                lower[base]
            } else {
                upper[base]
            };
            let y = if corner & 2 == 0 {
                lower[base + 1]
            } else {
                upper[base + 1]
            };
            let z = if corner & 1 == 0 {
                lower[base + 2]
            } else {
                upper[base + 2]
            };
            let w0 = matrix[12] * x;
            let w1 = matrix[13] * y;
            let w2 = matrix[14] * z;
            let w = w0 + w1 + w2 + matrix[15];
            if !(w > 0.0) {
                any_nonfront = true;
                let scale = w0.abs() + w1.abs() + w2.abs() + matrix[15].abs();
                if w < -1.0e-5 * scale {
                    behind_count += 1;
                }
                continue;
            }
            let clip_x = matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3];
            let clip_y = matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7];
            let pixel_x = half_width + half_width * (clip_x / w);
            let pixel_y = half_height + half_height * (clip_y / w);
            if pixel_x < bounds[0] {
                bounds[0] = pixel_x;
            }
            if pixel_y < bounds[1] {
                bounds[1] = pixel_y;
            }
            if pixel_x > bounds[2] {
                bounds[2] = pixel_x;
            }
            if pixel_y > bounds[3] {
                bounds[3] = pixel_y;
            }
        }
        let all_behind = behind_count == 8;
        let finite = bounds.iter().all(|value| value.is_finite());
        let unbounded = (any_nonfront && !all_behind) || !finite;
        out_rects[box_index * 4..box_index * 4 + 4].copy_from_slice(&bounds);
        out_unbounded[box_index] = u8::from(unbounded);
        out_all_behind[box_index] = u8::from(all_behind);
    }
    0
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
    fn display_point_projection_export_matches_the_shared_mixed_precision_path() {
        let matrix = [
            1.027_998_685_836_792,
            -0.425_244_897_603_988_65,
            -0.466_378_778_219_223,
            0.643_716_871_738_433_8,
            0.351_474_761_962_890_6,
            1.890_630_245_208_740_2,
            -0.949_153_780_937_194_8,
            9.998_909_950_256_348,
            -0.496_979_504_823_684_7,
            -0.313_879_579_305_648_8,
            -0.809_253_394_603_729_2,
            6.883_465_290_069_58,
            -0.496_880_114_078_521_73,
            -0.313_816_815_614_700_3,
            -0.809_091_567_993_164_1,
            7.082_068_443_298_34,
        ];
        let points = [
            6.764_359_507_293_101_f64,
            -9.856_076_443_526_845,
            0.483_920_382_336_751_94,
            f64::NAN,
            0.0,
            0.0,
        ];
        let mut pixels = [0.0_f64; 4];
        let mut visible = [9_u8; 2];
        let result = unsafe {
            narwhal_project_display_points_f64(
                matrix.as_ptr(),
                points.as_ptr(),
                2,
                1920.0,
                1080.0,
                pixels.as_mut_ptr(),
                visible.as_mut_ptr(),
            )
        };
        let expected = project_display_point_f64(&matrix, &points[..3], 1920.0, 1080.0)
            .expect("the adversarial point is visible");
        assert_eq!(result, 0);
        assert_eq!((pixels[0], pixels[1]), expected);
        assert_eq!(visible, [1, 0]);
        assert!(pixels[2].is_nan() && pixels[3].is_nan());
    }

    #[test]
    fn screen_polyline_query_preserves_strict_earlier_hover_semantics() {
        let pixels = [0.0_f64, 0.0, 10.0, 0.0, 0.0, 0.0, 10.0, 0.0];
        let visible = [1_u8; 4];
        let offsets = [0_u64, 2, 4];
        let mouse = [5.0_f64, 1.0];
        let mut index = u64::MAX;
        let mut distance = f64::INFINITY;
        let hit = unsafe {
            narwhal_nearest_screen_polyline_f64(
                pixels.as_ptr(),
                visible.as_ptr(),
                4,
                offsets.as_ptr(),
                2,
                mouse.as_ptr(),
                9.0,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!((hit, index, distance), (1, 0, 1.0));

        let boundary_mouse = [5.0_f64, 9.0];
        let miss = unsafe {
            narwhal_nearest_screen_polyline_f64(
                pixels.as_ptr(),
                visible.as_ptr(),
                4,
                offsets.as_ptr(),
                2,
                boundary_mouse.as_ptr(),
                9.0,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!((miss, index, distance), (0, u64::MAX, f64::INFINITY));

        let adversarial_mouse = [4.364_241_123_199_463_f64, 4.749_826_908_111_572];
        let python_distance = 6.450_384_193_088_506_f64;
        let degenerate_pixels = [0.0_f64; 4];
        let one_curve = [0_u64, 2];
        let adversarial_miss = unsafe {
            narwhal_nearest_screen_polyline_f64(
                degenerate_pixels.as_ptr(),
                visible.as_ptr(),
                2,
                one_curve.as_ptr(),
                1,
                adversarial_mouse.as_ptr(),
                python_distance,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!(
            (adversarial_miss, index, distance),
            (0, u64::MAX, f64::INFINITY)
        );
        let above_python_distance = f64::from_bits(python_distance.to_bits() + 1);
        let adversarial_hit = unsafe {
            narwhal_nearest_screen_polyline_f64(
                degenerate_pixels.as_ptr(),
                visible.as_ptr(),
                2,
                one_curve.as_ptr(),
                1,
                adversarial_mouse.as_ptr(),
                above_python_distance,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!((adversarial_hit, index, distance), (1, 0, python_distance));

        let threshold_dx = (1.0e-9_f64).sqrt();
        assert_eq!(threshold_dx * threshold_dx, 1.0e-9);
        let below_threshold_dx = f64::from_bits(threshold_dx.to_bits() - 1);
        let epsilon_mouse = [threshold_dx * 2.0, 1.0];
        let below_distance =
            screen_point_segment_distance(epsilon_mouse, [0.0, 0.0], [below_threshold_dx, 0.0]);
        let at_distance =
            screen_point_segment_distance(epsilon_mouse, [0.0, 0.0], [threshold_dx, 0.0]);
        assert_eq!(
            below_distance,
            python_hypot2(epsilon_mouse[0], epsilon_mouse[1])
        );
        assert_eq!(
            at_distance,
            python_hypot2(epsilon_mouse[0] - threshold_dx, epsilon_mouse[1]),
        );
        assert_ne!(below_distance, at_distance);
        let extreme = 1.4e154_f64;
        assert_eq!(
            screen_point_segment_distance([extreme, extreme], [0.0, 0.0], [extreme, extreme],),
            0.0,
        );

        let hidden_pixels = [0.0_f64, 0.0, f64::NAN, f64::NAN, 0.0, 0.0, 10.0, 0.0];
        let hidden_visible = [1_u8, 0, 1, 1];
        let malformed_hidden = unsafe {
            narwhal_nearest_screen_polyline_f64(
                pixels.as_ptr(),
                hidden_visible.as_ptr(),
                4,
                offsets.as_ptr(),
                2,
                mouse.as_ptr(),
                9.0,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!(malformed_hidden, ERR_INPUT);
        let hidden_hit = unsafe {
            narwhal_nearest_screen_polyline_f64(
                hidden_pixels.as_ptr(),
                hidden_visible.as_ptr(),
                4,
                offsets.as_ptr(),
                2,
                mouse.as_ptr(),
                9.0,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!((hidden_hit, index, distance), (1, 1, 1.0));

        assert!(screen_segment_aabb_could_beat(
            [0.0, 0.0],
            [10.0, -1.0],
            [10.0, 1.0],
            10.0,
        ));
        let mut far_pixels = Vec::new();
        for point in 0..101 {
            far_pixels.extend_from_slice(&[10_000.0 + f64::from(point), 10_000.0]);
        }
        let far_visible = vec![1_u8; 101];
        let far_offsets = [0_u64, 101];
        SCREEN_DISTANCE_CALLS.store(0, std::sync::atomic::Ordering::Relaxed);
        let far_miss = unsafe {
            narwhal_nearest_screen_polyline_f64(
                far_pixels.as_ptr(),
                far_visible.as_ptr(),
                101,
                far_offsets.as_ptr(),
                1,
                [0.0_f64, 0.0].as_ptr(),
                10.0,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!(far_miss, 0);
        assert_eq!(
            SCREEN_DISTANCE_CALLS.load(std::sync::atomic::Ordering::Relaxed),
            0,
        );
    }

    #[test]
    fn aabb_rectangles_preserve_front_behind_and_straddling_states() {
        let matrix = [
            1.0_f64, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
        ];
        let lower = [-1.0_f64, -1.0, 1.0, -1.0, -1.0, -2.0, -1.0, -1.0, -1.0];
        let upper = [1.0_f64, 1.0, 1.0, 1.0, 1.0, -1.0, 1.0, 1.0, 1.0];
        let mut rects = [f64::NAN; 12];
        let mut unbounded = [0_u8; 3];
        let mut all_behind = [0_u8; 3];
        let result = unsafe {
            narwhal_screen_rects_from_aabbs_f64(
                matrix.as_ptr(),
                lower.as_ptr(),
                upper.as_ptr(),
                3,
                800.0,
                600.0,
                rects.as_mut_ptr(),
                unbounded.as_mut_ptr(),
                all_behind.as_mut_ptr(),
            )
        };
        assert_eq!(result, 0);
        assert_eq!(&rects[..4], &[0.0, 0.0, 800.0, 600.0]);
        assert_eq!(unbounded, [0, 1, 1]);
        assert_eq!(all_behind, [0, 1, 0]);
    }

    #[test]
    fn clip_box_reduction_preserves_grouping_visibility_and_point_flags() {
        let clip = [
            -1.0_f64,
            -1.0,
            0.0,
            1.0,
            1.0,
            1.0,
            0.0,
            1.0,
            99.0,
            99.0,
            0.0,
            -1.0,
            -0.5,
            0.25,
            0.0,
            1.0,
            0.75,
            -0.75,
            0.0,
            1.0,
            f64::NAN,
            0.0,
            0.0,
            1.0,
        ];
        let offsets = [0_u64, 3, 5, 6];
        let point_flags = [0_u8, 1, 0];
        let mut boxes = [0.0_f64; 12];
        let mut valid = [9_u8; 3];
        let result = unsafe {
            narwhal_screen_boxes_from_clip_f64(
                clip.as_ptr(),
                6,
                offsets.as_ptr(),
                3,
                point_flags.as_ptr(),
                800.0,
                600.0,
                boxes.as_mut_ptr(),
                valid.as_mut_ptr(),
            )
        };
        assert_eq!(result, 0);
        assert_eq!(valid, [1, 1, 0]);
        assert_eq!(&boxes[..4], &[0.0, 0.0, 800.0, 600.0]);
        assert_eq!(&boxes[4..8], &[200.0, 375.0, 200.0, 375.0]);
        assert!(boxes[8..].iter().all(|value| value.is_nan()));

        let matrix = [
            1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0,
        ];
        let world = [
            -1.0_f64, -1.0, 0.0, 1.0, 1.0, 0.0, 99.0, 99.0, -2.0, -0.5, 0.25, 0.0, 0.75, -0.75,
            0.0, 0.0, 0.0, -2.0,
        ];
        let mut fused_boxes = [0.0_f64; 12];
        let mut fused_valid = [9_u8; 3];
        let fused = unsafe {
            narwhal_screen_boxes_from_world_f64(
                matrix.as_ptr(),
                world.as_ptr(),
                6,
                offsets.as_ptr(),
                3,
                point_flags.as_ptr(),
                800.0,
                600.0,
                fused_boxes.as_mut_ptr(),
                fused_valid.as_mut_ptr(),
            )
        };
        assert_eq!(fused, 0);
        assert_eq!(fused_valid, valid);
        assert_eq!(&fused_boxes[..8], &boxes[..8]);
        assert!(fused_boxes[8..].iter().all(|value| value.is_nan()));
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
