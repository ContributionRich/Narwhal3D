// SPDX-License-Identifier: GPL-3.0-or-later
//! Display-only selection math for the Narwhal 3D Rust feasibility pilot.
//!
//! This crate never owns document geometry. It accepts packed copies of viewport
//! matrices, display points, and display segments through a small C ABI.

use std::{collections::HashSet, mem, ptr, slice};

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

#[cfg(test)]
std::thread_local! {
    static MATRIX_PREPARE_CALLS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
    static SNAP_HYPOT_CALLS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

#[inline]
fn display_matrix_f32(matrix: &[f64]) -> [f32; 16] {
    #[cfg(test)]
    MATRIX_PREPARE_CALLS.with(|calls| calls.set(calls.get() + 1));
    std::array::from_fn(|index| matrix[index] as f32)
}

#[inline]
fn project_display_point_prepared(
    matrix: &[f32; 16],
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
            let product = matrix[base + column] * vector[column];
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

#[cfg(test)]
#[inline]
fn project_display_point_f64(
    matrix: &[f64],
    point: &[f64],
    width: f64,
    height: f64,
) -> Option<(f64, f64)> {
    project_display_point_prepared(&display_matrix_f32(matrix), point, width, height)
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

#[cfg(test)]
static NEAR_DISTANCE_CALLS: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

#[inline]
fn screen_near_parameter(mouse: [f64; 2], a: [f64; 2], b: [f64; 2]) -> (f64, f64) {
    #[cfg(test)]
    NEAR_DISTANCE_CALLS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
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
fn grouped_world_bounds(
    matrix: &[f64],
    points: &[f64],
    start: usize,
    end: usize,
    is_point: bool,
    width: f64,
    height: f64,
) -> Option<[f64; 4]> {
    let mut bounds: Option<[f64; 4]> = None;
    for point_index in start..end {
        let clip = selection_clip_point_f64(matrix, &points[point_index * 3..point_index * 3 + 3]);
        if !clip.iter().all(|value| value.is_finite()) || clip[3] <= 0.0 {
            continue;
        }
        let pixel_x = (clip[0] / clip[3] + 1.0) * width * 0.5;
        let pixel_y = (clip[1] / clip[3] + 1.0) * height * 0.5;
        if !pixel_x.is_finite() || !pixel_y.is_finite() {
            continue;
        }
        match &mut bounds {
            None => {
                bounds = Some([pixel_x, pixel_y, pixel_x, pixel_y]);
                if is_point {
                    break;
                }
            }
            Some(value) => {
                value[0] = value[0].min(pixel_x);
                value[1] = value[1].min(pixel_y);
                value[2] = value[2].max(pixel_x);
                value[3] = value[3].max(pixel_y);
            }
        }
    }
    bounds
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

#[derive(Clone, Copy, Debug, PartialEq)]
struct IntersectionSnap {
    point: [f64; 3],
    target: usize,
}

#[inline]
fn point3(points: &[f64], index: usize) -> [f64; 3] {
    let start = index * 3;
    [points[start], points[start + 1], points[start + 2]]
}

#[inline]
fn segment_boxes_overlap(a: [f64; 3], b: [f64; 3], c: [f64; 3], d: [f64; 3], tol: f64) -> bool {
    (0..3).all(|axis| {
        a[axis].min(b[axis]) <= c[axis].max(d[axis]) + tol
            && c[axis].min(d[axis]) <= a[axis].max(b[axis]) + tol
    })
}

#[inline]
fn finite_norm3(x: f64, y: f64, z: f64) -> f64 {
    let scale = x.abs().max(y.abs()).max(z.abs());
    if scale == 0.0 {
        0.0
    } else {
        scale * ((x / scale).powi(2) + (y / scale).powi(2) + (z / scale).powi(2)).sqrt()
    }
}

#[inline]
fn segment_intersection_point(
    a1: [f64; 3],
    a2: [f64; 3],
    b1: [f64; 3],
    b2: [f64; 3],
    tol: f64,
) -> Option<[f64; 3]> {
    let d1 = std::array::from_fn::<_, 3, _>(|axis| a2[axis] - a1[axis]);
    let d2 = std::array::from_fn::<_, 3, _>(|axis| b2[axis] - b1[axis]);
    let r = std::array::from_fn::<_, 3, _>(|axis| a1[axis] - b1[axis]);
    let dot = |left: [f64; 3], right: [f64; 3]| {
        left[0] * right[0] + left[1] * right[1] + left[2] * right[2]
    };
    let aa = dot(d1, d1);
    let bb = dot(d1, d2);
    let dd = dot(d2, d2);
    let ee = dot(r, d1);
    let ff = dot(r, d2);
    let denom = aa * dd - bb * bb;
    let (mut s, mut t) = if denom > 1.0e-30 {
        ((bb * ff - dd * ee) / denom, (aa * ff - bb * ee) / denom)
    } else {
        (0.0, if dd > 1.0e-30 { ff / dd } else { 0.0 })
    };
    if s < 0.0 {
        s = 0.0;
        t = if dd > 1.0e-30 { ff / dd } else { 0.0 };
    } else if s > 1.0 {
        s = 1.0;
        t = if dd > 1.0e-30 { (bb + ff) / dd } else { 0.0 };
    }
    t = t.clamp(0.0, 1.0);
    let pa = std::array::from_fn::<_, 3, _>(|axis| a1[axis] + d1[axis] * s);
    let pb = std::array::from_fn::<_, 3, _>(|axis| b1[axis] + d2[axis] * t);
    let distance = finite_norm3(pa[0] - pb[0], pa[1] - pb[1], pa[2] - pb[2]);
    (distance <= tol).then(|| std::array::from_fn(|axis| (pa[axis] + pb[axis]) * 0.5))
}

fn intersection_snaps(points: &[f64], offsets: &[u64], tol: f64) -> Vec<IntersectionSnap> {
    let target_count = offsets.len().saturating_sub(1);
    let mut found: Vec<IntersectionSnap> = Vec::new();
    for target_i in 0..target_count {
        let start_i = offsets[target_i] as usize;
        let end_i = offsets[target_i + 1] as usize;
        for target_j in (target_i + 1)..target_count {
            let start_j = offsets[target_j] as usize;
            let end_j = offsets[target_j + 1] as usize;
            for segment_i in start_i..end_i.saturating_sub(1) {
                let a = point3(points, segment_i);
                let b = point3(points, segment_i + 1);
                for segment_j in start_j..end_j.saturating_sub(1) {
                    let c = point3(points, segment_j);
                    let d = point3(points, segment_j + 1);
                    if !segment_boxes_overlap(a, b, c, d, tol) {
                        continue;
                    }
                    let Some(point) = segment_intersection_point(a, b, c, d, tol) else {
                        continue;
                    };
                    if found.iter().any(|prior| {
                        finite_norm3(
                            point[0] - prior.point[0],
                            point[1] - prior.point[1],
                            point[2] - prior.point[2],
                        ) < 1.0e-9
                    }) {
                        continue;
                    }
                    found.push(IntersectionSnap {
                        point,
                        target: target_i,
                    });
                }
            }
        }
    }
    found
}

const DYNAMIC_PERP: u8 = 1;
const DYNAMIC_TAN: u8 = 2;

#[derive(Clone, Copy, Debug, PartialEq)]
struct DynamicSnap {
    point: [f64; 3],
    target: usize,
    kind: u8,
}

#[inline]
fn push_dynamic_unique(found: &mut Vec<DynamicSnap>, point: [f64; 3], target: usize, kind: u8) {
    if found.iter().any(|prior| {
        prior.target == target
            && prior.kind == kind
            && finite_norm3(
                point[0] - prior.point[0],
                point[1] - prior.point[1],
                point[2] - prior.point[2],
            ) < 1.0e-9
    }) {
        return;
    }
    found.push(DynamicSnap {
        point,
        target,
        kind,
    });
}

fn perpendicular_snaps(
    points: &[f64],
    start: usize,
    end: usize,
    base: [f64; 3],
    target: usize,
    found: &mut Vec<DynamicSnap>,
) {
    for segment in start..end.saturating_sub(1) {
        let a = point3(points, segment);
        let b = point3(points, segment + 1);
        let delta = std::array::from_fn::<_, 3, _>(|axis| b[axis] - a[axis]);
        let denominator = delta[0] * delta[0] + delta[1] * delta[1] + delta[2] * delta[2];
        if denominator <= 0.0 {
            continue;
        }
        let t = ((base[0] - a[0]) * delta[0]
            + (base[1] - a[1]) * delta[1]
            + (base[2] - a[2]) * delta[2])
            / denominator;
        if t <= 0.0 || t >= 1.0 {
            continue;
        }
        let foot = std::array::from_fn(|axis| a[axis] + delta[axis] * t);
        push_dynamic_unique(found, foot, target, DYNAMIC_PERP);
    }
}

#[inline]
fn cross3(left: [f64; 3], right: [f64; 3]) -> [f64; 3] {
    [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]
}

fn tangent_snaps(
    points: &[f64],
    start: usize,
    mut end: usize,
    base: [f64; 3],
    closed: bool,
    target: usize,
    found: &mut Vec<DynamicSnap>,
) {
    if end.saturating_sub(start) < 3 {
        return;
    }
    if closed
        && finite_norm3(
            points[start * 3] - points[(end - 1) * 3],
            points[start * 3 + 1] - points[(end - 1) * 3 + 1],
            points[start * 3 + 2] - points[(end - 1) * 3 + 2],
        ) < 1.0e-9
    {
        end -= 1;
    }
    let count = end.saturating_sub(start);
    if count < 3 {
        return;
    }
    let mut crosses = Vec::with_capacity(count);
    for local in 0..count {
        let previous = if closed {
            start + (local + count - 1) % count
        } else {
            start + local.saturating_sub(1)
        };
        let next = if closed {
            start + (local + 1) % count
        } else {
            start + (local + 1).min(count - 1)
        };
        let point = point3(points, start + local);
        let tangent =
            std::array::from_fn(|axis| points[next * 3 + axis] - points[previous * 3 + axis]);
        crosses.push(cross3(
            std::array::from_fn(|axis| point[axis] - base[axis]),
            tangent,
        ));
    }
    let mut reference = crosses[0];
    let mut reference_length =
        reference[0] * reference[0] + reference[1] * reference[1] + reference[2] * reference[2];
    for cross in crosses.iter().copied().skip(1) {
        let length = cross[0] * cross[0] + cross[1] * cross[1] + cross[2] * cross[2];
        if length > reference_length {
            reference = cross;
            reference_length = length;
        }
    }
    if reference_length <= 0.0 {
        return;
    }
    let scalar: Vec<f64> = crosses
        .iter()
        .map(|cross| cross[0] * reference[0] + cross[1] * reference[1] + cross[2] * reference[2])
        .collect();
    let limit = if closed { count } else { count - 1 };
    for local in 0..limit {
        let next = (local + 1) % count;
        let first = scalar[local];
        let second = scalar[next];
        let point = if first == 0.0 {
            point3(points, start + local)
        } else if first * second < 0.0 {
            let parameter = first / (first - second);
            let a = point3(points, start + local);
            let b = point3(points, start + next);
            std::array::from_fn(|axis| a[axis] + (b[axis] - a[axis]) * parameter)
        } else {
            continue;
        };
        push_dynamic_unique(found, point, target, DYNAMIC_TAN);
    }
}

fn dynamic_snaps(
    points: &[f64],
    offsets: &[u64],
    closed: &[u8],
    base: [f64; 3],
    mode_mask: u8,
) -> Vec<DynamicSnap> {
    let mut found = Vec::new();
    for target in 0..closed.len() {
        let start = offsets[target] as usize;
        let end = offsets[target + 1] as usize;
        if mode_mask & DYNAMIC_PERP != 0 {
            perpendicular_snaps(points, start, end, base, target, &mut found);
        }
        if mode_mask & DYNAMIC_TAN != 0 {
            tangent_snaps(
                points,
                start,
                end,
                base,
                closed[target] != 0,
                target,
                &mut found,
            );
        }
    }
    found
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
    let display_matrix = display_matrix_f32(matrix);
    let out_xy = slice::from_raw_parts_mut(out_xy_ptr, output_values);
    let out_visible = slice::from_raw_parts_mut(out_visible_ptr, count);
    for (index, point) in points.chunks_exact(3).enumerate() {
        if let Some((x, y)) = project_display_point_prepared(&display_matrix, point, width, height)
        {
            out_xy[index * 2] = x;
            out_xy[index * 2 + 1] = y;
            out_visible[index] = 1;
        }
    }
    0
}

/// Project f64 display points and classify inclusive pixel-radius candidates.
///
/// Returns compact, source-ordered candidate indices and CPython-matched distances for
/// points inside the inclusive radius. This export performs display arithmetic only;
/// callers retain candidate identity, snap
/// mode priority, rounded-distance ordering and Near fallback policy.
///
/// # Safety
///
/// The caller must provide 16 readable matrix values, `point_count * 3` readable
/// coordinates, two readable mouse values, `point_count` writable index/distance
/// outputs and one writable survivor count. Point/output buffers may be null only for
/// zero points and all
/// buffers must be correctly aligned and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_projected_snap_distances_f64(
    matrix_ptr: *const f64,
    points_ptr: *const f64,
    point_count: usize,
    width: f64,
    height: f64,
    mouse_ptr: *const f64,
    radius: f64,
    out_indices_ptr: *mut u64,
    out_distances_ptr: *mut f64,
    out_count_ptr: *mut u64,
) -> i32 {
    if point_count > 0
        && (out_indices_ptr.is_null() || out_distances_ptr.is_null() || out_count_ptr.is_null())
    {
        return ERR_NULL;
    }
    if !out_count_ptr.is_null() {
        *out_count_ptr = 0;
    }
    let point_values = match point_count.checked_mul(3) {
        Some(value)
            if valid_slice_len::<f64>(value)
                && valid_slice_len::<f64>(point_count)
                && valid_slice_len::<u64>(point_count) =>
        {
            value
        }
        _ => return ERR_SIZE,
    };
    if point_count > 0 {
        for index in 0..point_count {
            ptr::write(out_indices_ptr.add(index), u64::MAX);
            ptr::write(out_distances_ptr.add(index), f64::INFINITY);
        }
    }
    if matrix_ptr.is_null() || mouse_ptr.is_null() || (point_count > 0 && points_ptr.is_null()) {
        return ERR_NULL;
    }
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    let mouse_values = slice::from_raw_parts(mouse_ptr, 2);
    let mouse = [mouse_values[0], mouse_values[1]];
    if !valid_matrix(matrix)
        || !valid_dimensions(width, height)
        || !mouse.iter().all(|value| value.is_finite())
        || !radius.is_finite()
        || radius < 0.0
    {
        return ERR_INPUT;
    }
    if point_count == 0 {
        return 0;
    }
    let points = slice::from_raw_parts(points_ptr, point_values);
    if !points.iter().all(|value| value.is_finite()) {
        return ERR_INPUT;
    }
    let out_indices = slice::from_raw_parts_mut(out_indices_ptr, point_count);
    let out_distances = slice::from_raw_parts_mut(out_distances_ptr, point_count);
    let display_matrix = display_matrix_f32(matrix);
    let mut survivor_count = 0usize;
    for (index, point) in points.chunks_exact(3).enumerate() {
        if let Some((x, y)) = project_display_point_prepared(&display_matrix, point, width, height)
        {
            let dx = x - mouse[0];
            let dy = y - mouse[1];
            if dx.abs() > radius || dy.abs() > radius {
                continue;
            }
            #[cfg(test)]
            SNAP_HYPOT_CALLS.with(|calls| calls.set(calls.get() + 1));
            let distance = python_hypot2(dx, dy);
            if distance.is_finite() && distance <= radius {
                out_indices[survivor_count] = index as u64;
                out_distances[survivor_count] = distance;
                survivor_count += 1;
            }
        }
    }
    *out_count_ptr = survivor_count as u64;
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
    let display_matrix = display_matrix_f32(matrix);
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
        let mut previous = project_display_point_prepared(
            &display_matrix,
            &points[start * 3..start * 3 + 3],
            width,
            height,
        );
        let mut best_segment = u64::MAX;
        let mut best_parameter = f64::NAN;
        let mut best_distance = f64::INFINITY;
        for point_index in start..end - 1 {
            let next = project_display_point_prepared(
                &display_matrix,
                &points[(point_index + 1) * 3..(point_index + 1) * 3 + 3],
                width,
                height,
            );
            if let (Some(a), Some(b)) = (previous, next) {
                let screen_a = [a.0, a.1];
                let screen_b = [b.0, b.1];
                if !screen_segment_aabb_could_beat(mouse, screen_a, screen_b, best_distance) {
                    previous = next;
                    continue;
                }
                let (parameter, distance) = screen_near_parameter(mouse, screen_a, screen_b);
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
        if let Some(bounds) = grouped_world_bounds(
            matrix,
            points,
            start,
            end,
            point_flags[object_index] == 1,
            width,
            height,
        ) {
            out_boxes[object_index * 4..object_index * 4 + 4].copy_from_slice(&bounds);
            out_valid[object_index] = 1;
        }
    }
    0
}

/// Project, reduce, and classify grouped world samples against a screen rectangle.
///
/// `crossing` zero applies strict window containment; one applies inclusive
/// crossing overlap. One byte per object is returned, avoiding screen-box
/// publication for objects that did not hit. Point flags use only the first
/// projectable sample, matching the established box reducer.
///
/// # Safety
///
/// The caller must provide the same matrix/point/offset/flag inputs as
/// `narwhal_screen_boxes_from_world_f64`, a readable four-value rectangle, and
/// `object_count` writable hit bytes. Buffers must be aligned and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_world_rect_hit_mask_f64(
    matrix_ptr: *const f64,
    points_ptr: *const f64,
    point_count: usize,
    offsets_ptr: *const u64,
    object_count: usize,
    point_flags_ptr: *const u8,
    width: f64,
    height: f64,
    rect_ptr: *const f64,
    crossing: u8,
    out_hits_ptr: *mut u8,
) -> i32 {
    if matrix_ptr.is_null() || rect_ptr.is_null() {
        return ERR_NULL;
    }
    if object_count > 0
        && (offsets_ptr.is_null() || point_flags_ptr.is_null() || out_hits_ptr.is_null())
    {
        return ERR_NULL;
    }
    if point_count > 0 && points_ptr.is_null() {
        return ERR_NULL;
    }
    let point_values = match point_count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let offset_count = match object_count.checked_add(1) {
        Some(value) if valid_slice_len::<u64>(value) && valid_slice_len::<u8>(object_count) => {
            value
        }
        _ => return ERR_SIZE,
    };
    if object_count > 0 {
        ptr::write_bytes(out_hits_ptr, 0, object_count);
    }
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    let rect_values = slice::from_raw_parts(rect_ptr, 4);
    let rect = [
        rect_values[0],
        rect_values[1],
        rect_values[2],
        rect_values[3],
    ];
    if !valid_matrix(matrix)
        || !valid_dimensions(width, height)
        || !valid_rect(rect)
        || crossing > 1
    {
        return ERR_INPUT;
    }
    if object_count == 0 {
        return if point_count == 0 { 0 } else { ERR_INPUT };
    }
    let point_count_u64 = match u64::try_from(point_count) {
        Ok(value) => value,
        Err(_) => return ERR_SIZE,
    };
    let points = if point_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(points_ptr, point_values)
    };
    if points.iter().any(|value| !value.is_finite()) {
        return ERR_INPUT;
    }
    let offsets = slice::from_raw_parts(offsets_ptr, offset_count);
    let point_flags = slice::from_raw_parts(point_flags_ptr, object_count);
    if offsets[0] != 0
        || offsets[object_count] != point_count_u64
        || offsets
            .windows(2)
            .any(|pair| pair[0] > pair[1] || pair[1] > point_count_u64)
        || point_flags.iter().any(|flag| *flag > 1)
    {
        return ERR_INPUT;
    }
    let out_hits = slice::from_raw_parts_mut(out_hits_ptr, object_count);
    for object_index in 0..object_count {
        let start = offsets[object_index] as usize;
        let end = offsets[object_index + 1] as usize;
        let Some(bounds) = grouped_world_bounds(
            matrix,
            points,
            start,
            end,
            point_flags[object_index] == 1,
            width,
            height,
        ) else {
            continue;
        };
        let hit = if crossing == 1 {
            !(bounds[2] < rect[0]
                || bounds[0] > rect[2]
                || bounds[3] < rect[1]
                || bounds[1] > rect[3])
        } else {
            bounds[0] >= rect[0]
                && bounds[1] >= rect[1]
                && bounds[2] <= rect[2]
                && bounds[3] <= rect[3]
        };
        out_hits[object_index] = u8::from(hit);
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
            if w <= 0.0 || w.is_nan() {
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

/// Return source-ordered indices of cached screen rectangles that can reach a snap.
///
/// Rows without extents and eye-straddling/unbounded rows are retained; all-behind
/// rows are rejected; bounded rows use inclusive squared distance to the rectangle.
///
/// # Safety
///
/// Each coordinate/mask input contains `count` readable values, mouse contains two,
/// output contains `output_capacity` writable u64 values, and `out_count` is writable.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_reached_screen_rect_indices_f64(
    x0_ptr: *const f64,
    y0_ptr: *const f64,
    x1_ptr: *const f64,
    y1_ptr: *const f64,
    unbounded_ptr: *const u8,
    all_behind_ptr: *const u8,
    has_extent_ptr: *const u8,
    count: usize,
    mouse_ptr: *const f64,
    radius: f64,
    margin: f64,
    out_indices_ptr: *mut u64,
    output_capacity: usize,
    out_count_ptr: *mut u64,
) -> i32 {
    if out_count_ptr.is_null() {
        return ERR_NULL;
    }
    *out_count_ptr = 0;
    if output_capacity < count {
        return ERR_SIZE;
    }
    if mouse_ptr.is_null()
        || (count > 0
            && (x0_ptr.is_null()
                || y0_ptr.is_null()
                || x1_ptr.is_null()
                || y1_ptr.is_null()
                || unbounded_ptr.is_null()
                || all_behind_ptr.is_null()
                || has_extent_ptr.is_null()
                || out_indices_ptr.is_null()))
    {
        return ERR_NULL;
    }
    if !valid_slice_len::<f64>(count)
        || !valid_slice_len::<u8>(count)
        || !valid_slice_len::<u64>(output_capacity)
    {
        return ERR_SIZE;
    }
    let mouse = slice::from_raw_parts(mouse_ptr, 2);
    if !mouse.iter().all(|value| value.is_finite())
        || !radius.is_finite()
        || !margin.is_finite()
        || radius < 0.0
        || margin < 0.0
    {
        return ERR_INPUT;
    }
    let x0 = slice::from_raw_parts(x0_ptr, count);
    let y0 = slice::from_raw_parts(y0_ptr, count);
    let x1 = slice::from_raw_parts(x1_ptr, count);
    let y1 = slice::from_raw_parts(y1_ptr, count);
    let unbounded = slice::from_raw_parts(unbounded_ptr, count);
    let all_behind = slice::from_raw_parts(all_behind_ptr, count);
    let has_extent = slice::from_raw_parts(has_extent_ptr, count);
    let out = slice::from_raw_parts_mut(out_indices_ptr, output_capacity);
    let reach = radius + margin;
    let reach2 = reach * reach;
    let mut written = 0usize;
    for index in 0..count {
        if unbounded[index] > 1 || all_behind[index] > 1 || has_extent[index] > 1 {
            return ERR_INPUT;
        }
        let keep = if has_extent[index] == 0 {
            true
        } else if all_behind[index] == 1 {
            false
        } else if unbounded[index] == 1 {
            true
        } else {
            if !x0[index].is_finite()
                || !y0[index].is_finite()
                || !x1[index].is_finite()
                || !y1[index].is_finite()
                || x0[index] > x1[index]
                || y0[index] > y1[index]
            {
                return ERR_INPUT;
            }
            let dx = if mouse[0] < x0[index] {
                x0[index] - mouse[0]
            } else if mouse[0] > x1[index] {
                mouse[0] - x1[index]
            } else {
                0.0
            };
            let dy = if mouse[1] < y0[index] {
                y0[index] - mouse[1]
            } else if mouse[1] > y1[index] {
                mouse[1] - y1[index]
            } else {
                0.0
            };
            dx * dx + dy * dy <= reach2
        };
        if keep {
            out[written] = index as u64;
            written += 1;
        }
    }
    *out_count_ptr = written as u64;
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
    let display_matrix = display_matrix_f32(matrix);
    let mut best_index = u64::MAX;
    let mut best_distance = max_distance;
    for (index, point) in points.chunks_exact(3).enumerate() {
        if let Some((x, y)) = project_display_point_prepared(&display_matrix, point, width, height)
        {
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

/// Return the nearest projectable sample with strict earliest-index tie semantics.
///
/// Unlike the radius-limited point picker, this always returns the nearest visible
/// sample and publishes squared distance so exact-curve callers can select a bracket.
///
/// # Safety
///
/// The caller provides 16 readable matrix values, `count * 3` readable point values,
/// two readable mouse values, and writable non-overlapping index/distance outputs.
#[no_mangle]
pub unsafe extern "C" fn narwhal_nearest_projected_sample_f64(
    matrix_ptr: *const f64,
    points_ptr: *const f64,
    count: usize,
    width: f64,
    height: f64,
    mouse_ptr: *const f64,
    out_point_index_ptr: *mut u64,
    out_distance2_ptr: *mut f64,
) -> i32 {
    if out_point_index_ptr.is_null() || out_distance2_ptr.is_null() {
        return ERR_NULL;
    }
    *out_point_index_ptr = u64::MAX;
    *out_distance2_ptr = f64::INFINITY;
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
    {
        return ERR_INPUT;
    }
    let points = slice::from_raw_parts(points_ptr, point_values);
    let display_matrix = display_matrix_f32(matrix);
    let mut best_index = u64::MAX;
    let mut best_distance2 = f64::INFINITY;
    for (index, point) in points.chunks_exact(3).enumerate() {
        if let Some((x, y)) = project_display_point_prepared(&display_matrix, point, width, height)
        {
            let dx = x - mouse[0];
            let dy = y - mouse[1];
            let distance2 = dx * dx + dy * dy;
            if distance2.is_finite() && distance2 < best_distance2 {
                best_index = index as u64;
                best_distance2 = distance2;
            }
        }
    }
    if best_index == u64::MAX {
        return 0;
    }
    *out_point_index_ptr = best_index;
    *out_distance2_ptr = best_distance2;
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
    let display_matrix = display_matrix_f32(matrix);
    let out_inside = slice::from_raw_parts_mut(out_inside_ptr, count);
    for (index, point) in points.chunks_exact(3).enumerate() {
        if let Some((x, y)) = project_display_point_prepared(&display_matrix, point, width, height)
        {
            out_inside[index] =
                u8::from(rect[0] <= x && x <= rect[2] && rect[1] <= y && y <= rect[3]);
        }
    }
    0
}

/// Find intersections between different packed sampled polylines.
///
/// The first call may use `capacity == 0` and null output arrays to obtain the
/// required row count in `out_count`. A second call with that exact capacity
/// writes packed XYZ rows and the index of the earlier target in each pair.
/// Results preserve target-pair and segment-pair traversal order and first-seen
/// deduplication from the established scalar Osnap implementation.
///
/// # Safety
///
/// The caller must provide `point_count * 3` readable f64 values,
/// `target_count + 1` readable u64 offsets, and a writable `out_count`. When
/// capacity is nonzero, output arrays must hold `capacity * 3` f64 values and
/// `capacity` u64 values. All provided buffers must be aligned and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_intersection_snaps_f64(
    points_ptr: *const f64,
    point_count: usize,
    offsets_ptr: *const u64,
    target_count: usize,
    tol: f64,
    out_xyz_ptr: *mut f64,
    out_targets_ptr: *mut u64,
    capacity: usize,
    out_count_ptr: *mut u64,
) -> i32 {
    if offsets_ptr.is_null() || out_count_ptr.is_null() || (point_count > 0 && points_ptr.is_null())
    {
        return ERR_NULL;
    }
    if capacity > 0 && (out_xyz_ptr.is_null() || out_targets_ptr.is_null()) {
        return ERR_NULL;
    }
    let point_values = match point_count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let offset_count = match target_count.checked_add(1) {
        Some(value) if valid_slice_len::<u64>(value) => value,
        _ => return ERR_SIZE,
    };
    let output_values = match capacity.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) && valid_slice_len::<u64>(capacity) => value,
        _ => return ERR_SIZE,
    };
    let points = if point_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(points_ptr, point_values)
    };
    let offsets = slice::from_raw_parts(offsets_ptr, offset_count);
    if !tol.is_finite()
        || tol < 0.0
        || points.iter().any(|value| !value.is_finite())
        || offsets.first() != Some(&0)
        || offsets.last() != Some(&(point_count as u64))
        || offsets.windows(2).any(|pair| pair[0] > pair[1])
    {
        return ERR_INPUT;
    }
    let found = intersection_snaps(points, offsets, tol);
    ptr::write(out_count_ptr, found.len() as u64);
    if capacity == 0 {
        return 0;
    }
    if capacity < found.len() {
        return ERR_SIZE;
    }
    let out_xyz = slice::from_raw_parts_mut(out_xyz_ptr, output_values);
    let out_targets = slice::from_raw_parts_mut(out_targets_ptr, capacity);
    out_xyz.fill(f64::NAN);
    out_targets.fill(u64::MAX);
    for (index, snap) in found.iter().enumerate() {
        out_xyz[index * 3..index * 3 + 3].copy_from_slice(&snap.point);
        out_targets[index] = snap.target as u64;
    }
    0
}

/// Compute sampled Perp and/or Tan candidates for packed polylines.
///
/// `mode_mask` uses bit 1 for Perp and bit 2 for Tan. Output rows retain target
/// order, with all Perp rows before Tan rows for each target. The two-pass output
/// contract is the same as `narwhal_intersection_snaps_f64`.
///
/// # Safety
///
/// The caller must provide valid packed point/offset/closed/base inputs and a
/// writable count. Nonzero-capacity XYZ, target and kind outputs must be aligned,
/// non-overlapping, and large enough for the declared capacity.
#[no_mangle]
pub unsafe extern "C" fn narwhal_dynamic_perp_tan_snaps_f64(
    points_ptr: *const f64,
    point_count: usize,
    offsets_ptr: *const u64,
    closed_ptr: *const u8,
    target_count: usize,
    base_ptr: *const f64,
    mode_mask: u8,
    out_xyz_ptr: *mut f64,
    out_targets_ptr: *mut u64,
    out_kinds_ptr: *mut u8,
    capacity: usize,
    out_count_ptr: *mut u64,
) -> i32 {
    if offsets_ptr.is_null()
        || base_ptr.is_null()
        || out_count_ptr.is_null()
        || (point_count > 0 && points_ptr.is_null())
        || (target_count > 0 && closed_ptr.is_null())
    {
        return ERR_NULL;
    }
    if capacity > 0
        && (out_xyz_ptr.is_null() || out_targets_ptr.is_null() || out_kinds_ptr.is_null())
    {
        return ERR_NULL;
    }
    let point_values = match point_count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let offset_count = match target_count.checked_add(1) {
        Some(value) if valid_slice_len::<u64>(value) => value,
        _ => return ERR_SIZE,
    };
    let output_values = match capacity.checked_mul(3) {
        Some(value)
            if valid_slice_len::<f64>(value)
                && valid_slice_len::<u64>(capacity)
                && valid_slice_len::<u8>(capacity) =>
        {
            value
        }
        _ => return ERR_SIZE,
    };
    let points = if point_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(points_ptr, point_values)
    };
    let offsets = slice::from_raw_parts(offsets_ptr, offset_count);
    let closed = if target_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(closed_ptr, target_count)
    };
    let base_slice = slice::from_raw_parts(base_ptr, 3);
    let base = [base_slice[0], base_slice[1], base_slice[2]];
    if mode_mask == 0
        || mode_mask & !(DYNAMIC_PERP | DYNAMIC_TAN) != 0
        || base.iter().any(|value| !value.is_finite())
        || points.iter().any(|value| !value.is_finite())
        || closed.iter().any(|value| *value > 1)
        || offsets.first() != Some(&0)
        || offsets.last() != Some(&(point_count as u64))
        || offsets.windows(2).any(|pair| pair[0] > pair[1])
    {
        return ERR_INPUT;
    }
    let found = dynamic_snaps(points, offsets, closed, base, mode_mask);
    ptr::write(out_count_ptr, found.len() as u64);
    if capacity == 0 {
        return 0;
    }
    if capacity < found.len() {
        return ERR_SIZE;
    }
    let out_xyz = slice::from_raw_parts_mut(out_xyz_ptr, output_values);
    let out_targets = slice::from_raw_parts_mut(out_targets_ptr, capacity);
    let out_kinds = slice::from_raw_parts_mut(out_kinds_ptr, capacity);
    out_xyz.fill(f64::NAN);
    out_targets.fill(u64::MAX);
    out_kinds.fill(0);
    for (index, snap) in found.iter().enumerate() {
        out_xyz[index * 3..index * 3 + 3].copy_from_slice(&snap.point);
        out_targets[index] = snap.target as u64;
        out_kinds[index] = snap.kind;
    }
    0
}

const SNAP_END: u16 = 1;
const SNAP_MID: u16 = 2;
const SNAP_CEN: u16 = 4;
const SNAP_QUAD: u16 = 8;
const SNAP_POINT: u16 = 16;
const SNAP_KNOT: u16 = 32;
const SNAP_ALL_STATIC: u16 = SNAP_END | SNAP_MID | SNAP_CEN | SNAP_QUAD | SNAP_POINT | SNAP_KNOT;
const TARGET_POINT: u8 = 1;
const TARGET_CLOSED: u8 = 2;

#[derive(Clone, Copy, Debug, PartialEq)]
struct StaticSnap {
    point: [f64; 3],
    target: usize,
    mode: u8,
    distance: f64,
}

#[inline]
#[allow(clippy::too_many_arguments)]
fn push_static_survivor(
    found: &mut Vec<StaticSnap>,
    point: [f64; 3],
    target: usize,
    mode: u8,
    display_matrix: &[f32; 16],
    width: f64,
    height: f64,
    mouse: [f64; 2],
    radius: f64,
) {
    let Some(pixel) = project_display_point_prepared(display_matrix, &point, width, height) else {
        return;
    };
    let distance = python_hypot2(pixel.0 - mouse[0], pixel.1 - mouse[1]);
    if distance <= radius {
        found.push(StaticSnap {
            point,
            target,
            mode,
            distance,
        });
    }
}

fn sampled_arc_midpoint(points: &[f64], start: usize, end: usize) -> Option<[f64; 3]> {
    if end.saturating_sub(start) < 2 {
        return None;
    }
    let mut total = 0.0;
    for index in start..end - 1 {
        let a = point3(points, index);
        let b = point3(points, index + 1);
        total += finite_norm3(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
    }
    if total <= 0.0 {
        return None;
    }
    let half = total / 2.0;
    let mut walked = 0.0;
    for index in start..end - 1 {
        let a = point3(points, index);
        let b = point3(points, index + 1);
        let length = finite_norm3(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
        if length <= 0.0 {
            continue;
        }
        if walked + length >= half {
            let parameter = (half - walked) / length;
            return Some(std::array::from_fn(|axis| {
                a[axis] + (b[axis] - a[axis]) * parameter
            }));
        }
        walked += length;
    }
    Some(point3(points, end - 1))
}

#[allow(clippy::too_many_arguments)]
fn static_snap_survivors(
    points: &[f64],
    point_offsets: &[u64],
    vertices: &[f64],
    vertex_offsets: &[u64],
    knots: &[f64],
    knot_offsets: &[u64],
    centers: &[f64],
    center_valid: &[u8],
    target_flags: &[u8],
    frame: &[f64],
    matrix: &[f64],
    width: f64,
    height: f64,
    mouse: [f64; 2],
    radius: f64,
    mode_mask: u16,
) -> Vec<StaticSnap> {
    let display_matrix = display_matrix_f32(matrix);
    let mut found = Vec::new();
    for target in 0..target_flags.len() {
        let point_start = point_offsets[target] as usize;
        let point_end = point_offsets[target + 1] as usize;
        let vertex_start = vertex_offsets[target] as usize;
        let vertex_end = vertex_offsets[target + 1] as usize;
        let knot_start = knot_offsets[target] as usize;
        let knot_end = knot_offsets[target + 1] as usize;
        if target_flags[target] & TARGET_POINT != 0 {
            if mode_mask & SNAP_POINT != 0 && point_start < point_end {
                push_static_survivor(
                    &mut found,
                    point3(points, point_start),
                    target,
                    5,
                    &display_matrix,
                    width,
                    height,
                    mouse,
                    radius,
                );
            }
            continue;
        }
        if mode_mask & SNAP_END != 0 {
            for index in vertex_start..vertex_end {
                push_static_survivor(
                    &mut found,
                    point3(vertices, index),
                    target,
                    1,
                    &display_matrix,
                    width,
                    height,
                    mouse,
                    radius,
                );
            }
        }
        if mode_mask & SNAP_MID != 0 {
            let mut mids = Vec::new();
            for index in vertex_start..vertex_end.saturating_sub(1) {
                let a = point3(vertices, index);
                let b = point3(vertices, index + 1);
                let midpoint = std::array::from_fn(|axis| (a[axis] + b[axis]) / 2.0);
                mids.push(midpoint);
                push_static_survivor(
                    &mut found,
                    midpoint,
                    target,
                    2,
                    &display_matrix,
                    width,
                    height,
                    mouse,
                    radius,
                );
            }
            if target_flags[target] & TARGET_CLOSED != 0
                && vertex_end.saturating_sub(vertex_start) >= 3
            {
                let a = point3(vertices, vertex_end - 1);
                let b = point3(vertices, vertex_start);
                if finite_norm3(a[0] - b[0], a[1] - b[1], a[2] - b[2]) > 1.0e-12 {
                    let midpoint = std::array::from_fn(|axis| (a[axis] + b[axis]) / 2.0);
                    mids.push(midpoint);
                    push_static_survivor(
                        &mut found,
                        midpoint,
                        target,
                        2,
                        &display_matrix,
                        width,
                        height,
                        mouse,
                        radius,
                    );
                }
            }
            if let Some(midpoint) = sampled_arc_midpoint(points, point_start, point_end) {
                let duplicate = mids.iter().any(|prior| {
                    finite_norm3(
                        midpoint[0] - prior[0],
                        midpoint[1] - prior[1],
                        midpoint[2] - prior[2],
                    ) < 1.0e-9
                });
                if !duplicate {
                    push_static_survivor(
                        &mut found,
                        midpoint,
                        target,
                        2,
                        &display_matrix,
                        width,
                        height,
                        mouse,
                        radius,
                    );
                }
            }
        }
        if mode_mask & SNAP_CEN != 0 && center_valid[target] == 1 {
            push_static_survivor(
                &mut found,
                point3(centers, target),
                target,
                3,
                &display_matrix,
                width,
                height,
                mouse,
                radius,
            );
        }
        if mode_mask & SNAP_QUAD != 0 && point_start < point_end {
            let origin = [frame[0], frame[1], frame[2]];
            let xaxis = [frame[3], frame[4], frame[5]];
            let yaxis = [frame[6], frame[7], frame[8]];
            let first = point3(points, point_start);
            let first_delta = [
                first[0] - origin[0],
                first[1] - origin[1],
                first[2] - origin[2],
            ];
            let first_u =
                first_delta[0] * xaxis[0] + first_delta[1] * xaxis[1] + first_delta[2] * xaxis[2];
            let first_v =
                first_delta[0] * yaxis[0] + first_delta[1] * yaxis[1] + first_delta[2] * yaxis[2];
            let mut umin = (first_u, first);
            let mut umax = (first_u, first);
            let mut vmin = (first_v, first);
            let mut vmax = (first_v, first);
            for index in point_start + 1..point_end {
                let point = point3(points, index);
                let delta = [
                    point[0] - origin[0],
                    point[1] - origin[1],
                    point[2] - origin[2],
                ];
                let u = delta[0] * xaxis[0] + delta[1] * xaxis[1] + delta[2] * xaxis[2];
                let v = delta[0] * yaxis[0] + delta[1] * yaxis[1] + delta[2] * yaxis[2];
                if u < umin.0 {
                    umin = (u, point);
                }
                if u > umax.0 {
                    umax = (u, point);
                }
                if v < vmin.0 {
                    vmin = (v, point);
                }
                if v > vmax.0 {
                    vmax = (v, point);
                }
            }
            let mut unique: Vec<[f64; 3]> = Vec::new();
            for point in [umin.1, umax.1, vmin.1, vmax.1] {
                if unique.iter().any(|prior| {
                    finite_norm3(
                        point[0] - prior[0],
                        point[1] - prior[1],
                        point[2] - prior[2],
                    ) < 1.0e-9
                }) {
                    continue;
                }
                unique.push(point);
                push_static_survivor(
                    &mut found,
                    point,
                    target,
                    4,
                    &display_matrix,
                    width,
                    height,
                    mouse,
                    radius,
                );
            }
        }
        if mode_mask & SNAP_KNOT != 0 {
            for index in knot_start..knot_end {
                push_static_survivor(
                    &mut found,
                    point3(knots, index),
                    target,
                    6,
                    &display_matrix,
                    width,
                    height,
                    mouse,
                    radius,
                );
            }
        }
    }
    found
}

/// Derive and score static discrete Osnap candidates for packed targets.
///
/// Modes are End/Mid/Cen/Quad/Point/Knot bits 0..5. Target flags are point bit
/// 0 and closed bit 1. Output uses mode IDs 1..6 in that same named order and
/// preserves the established per-target candidate traversal after radius filtering.
///
/// # Safety
///
/// Every point family must provide its declared packed XYZ buffer and
/// `target_count + 1` offsets. Centers contain three values per target. Frame,
/// display matrix, mouse, output count, and nonzero-capacity outputs are required.
/// Every provided buffer must be valid for its declared length, correctly aligned,
/// and non-overlapping with writable output buffers.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn narwhal_discrete_snap_survivors_f64(
    points_ptr: *const f64,
    point_count: usize,
    point_offsets_ptr: *const u64,
    vertices_ptr: *const f64,
    vertex_count: usize,
    vertex_offsets_ptr: *const u64,
    knots_ptr: *const f64,
    knot_count: usize,
    knot_offsets_ptr: *const u64,
    centers_ptr: *const f64,
    center_valid_ptr: *const u8,
    target_flags_ptr: *const u8,
    target_count: usize,
    frame_ptr: *const f64,
    matrix_ptr: *const f64,
    width: f64,
    height: f64,
    mouse_ptr: *const f64,
    radius: f64,
    mode_mask: u16,
    out_xyz_ptr: *mut f64,
    out_targets_ptr: *mut u64,
    out_modes_ptr: *mut u8,
    out_distances_ptr: *mut f64,
    capacity: usize,
    out_count_ptr: *mut u64,
) -> i32 {
    if out_count_ptr.is_null() {
        return ERR_NULL;
    }
    ptr::write(out_count_ptr, 0);
    if point_offsets_ptr.is_null()
        || vertex_offsets_ptr.is_null()
        || knot_offsets_ptr.is_null()
        || frame_ptr.is_null()
        || matrix_ptr.is_null()
        || mouse_ptr.is_null()
        || (point_count > 0 && points_ptr.is_null())
        || (vertex_count > 0 && vertices_ptr.is_null())
        || (knot_count > 0 && knots_ptr.is_null())
        || (target_count > 0
            && (centers_ptr.is_null() || center_valid_ptr.is_null() || target_flags_ptr.is_null()))
        || (capacity > 0
            && (out_xyz_ptr.is_null()
                || out_targets_ptr.is_null()
                || out_modes_ptr.is_null()
                || out_distances_ptr.is_null()))
    {
        return ERR_NULL;
    }
    let point_values = match point_count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let vertex_values = match vertex_count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let knot_values = match knot_count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let center_values = match target_count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let offset_count = match target_count.checked_add(1) {
        Some(value) if valid_slice_len::<u64>(value) => value,
        _ => return ERR_SIZE,
    };
    let output_values = match capacity.checked_mul(3) {
        Some(value)
            if valid_slice_len::<f64>(value)
                && valid_slice_len::<u64>(capacity)
                && valid_slice_len::<u8>(capacity) =>
        {
            value
        }
        _ => return ERR_SIZE,
    };
    let points = if point_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(points_ptr, point_values)
    };
    let vertices = if vertex_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(vertices_ptr, vertex_values)
    };
    let knots = if knot_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(knots_ptr, knot_values)
    };
    let centers = if target_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(centers_ptr, center_values)
    };
    let center_valid = if target_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(center_valid_ptr, target_count)
    };
    let target_flags = if target_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(target_flags_ptr, target_count)
    };
    let point_offsets = slice::from_raw_parts(point_offsets_ptr, offset_count);
    let vertex_offsets = slice::from_raw_parts(vertex_offsets_ptr, offset_count);
    let knot_offsets = slice::from_raw_parts(knot_offsets_ptr, offset_count);
    let frame = slice::from_raw_parts(frame_ptr, 9);
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    let mouse_values = slice::from_raw_parts(mouse_ptr, 2);
    let mouse = [mouse_values[0], mouse_values[1]];
    let offsets_valid = |offsets: &[u64], count: usize| {
        offsets.first() == Some(&0)
            && offsets.last() == Some(&(count as u64))
            && !offsets
                .windows(2)
                .any(|pair| pair[0] > pair[1] || pair[1] > count as u64)
    };
    if mode_mask == 0
        || mode_mask & !SNAP_ALL_STATIC != 0
        || !valid_matrix(matrix)
        || !valid_dimensions(width, height)
        || !radius.is_finite()
        || radius < 0.0
        || !mouse.iter().all(|value| value.is_finite())
        || frame.iter().any(|value| !value.is_finite())
        || points
            .iter()
            .chain(vertices)
            .chain(knots)
            .chain(centers)
            .any(|value| !value.is_finite())
        || center_valid.iter().any(|value| *value > 1)
        || target_flags
            .iter()
            .any(|value| *value & !(TARGET_POINT | TARGET_CLOSED) != 0)
        || !offsets_valid(point_offsets, point_count)
        || !offsets_valid(vertex_offsets, vertex_count)
        || !offsets_valid(knot_offsets, knot_count)
    {
        return ERR_INPUT;
    }
    let found = static_snap_survivors(
        points,
        point_offsets,
        vertices,
        vertex_offsets,
        knots,
        knot_offsets,
        centers,
        center_valid,
        target_flags,
        frame,
        matrix,
        width,
        height,
        mouse,
        radius,
        mode_mask,
    );
    ptr::write(out_count_ptr, found.len() as u64);
    if capacity == 0 {
        return 0;
    }
    if capacity < found.len() {
        return ERR_SIZE;
    }
    let out_xyz = slice::from_raw_parts_mut(out_xyz_ptr, output_values);
    let out_targets = slice::from_raw_parts_mut(out_targets_ptr, capacity);
    let out_modes = slice::from_raw_parts_mut(out_modes_ptr, capacity);
    let out_distances = slice::from_raw_parts_mut(out_distances_ptr, capacity);
    out_xyz.fill(f64::NAN);
    out_targets.fill(u64::MAX);
    out_modes.fill(0);
    out_distances.fill(f64::INFINITY);
    for (index, snap) in found.iter().enumerate() {
        out_xyz[index * 3..index * 3 + 3].copy_from_slice(&snap.point);
        out_targets[index] = snap.target as u64;
        out_modes[index] = snap.mode;
        out_distances[index] = snap.distance;
    }
    0
}

/// Compute the active-frame-oriented bounding-box center of packed world points.
///
/// The frame is nine f64 values: origin XYZ, X axis XYZ, then Y axis XYZ. Z is
/// derived as X cross Y, matching the CPlane model's right-handed invariant.
///
/// # Safety
///
/// The point buffer must contain `point_count * 3` readable f64 values. Frame,
/// output XYZ and output-valid pointers are always required and non-overlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_oriented_bbox_center_f64(
    points_ptr: *const f64,
    point_count: usize,
    frame_ptr: *const f64,
    out_xyz_ptr: *mut f64,
    out_valid_ptr: *mut u8,
) -> i32 {
    if frame_ptr.is_null() || out_xyz_ptr.is_null() || out_valid_ptr.is_null() {
        return ERR_NULL;
    }
    ptr::write_bytes(out_xyz_ptr, 0xFF, 3);
    ptr::write(out_valid_ptr, 0);
    if point_count > 0 && points_ptr.is_null() {
        return ERR_NULL;
    }
    let point_values = match point_count.checked_mul(3) {
        Some(value) if valid_slice_len::<f64>(value) => value,
        _ => return ERR_SIZE,
    };
    let frame = slice::from_raw_parts(frame_ptr, 9);
    if frame.iter().any(|value| !value.is_finite()) {
        return ERR_INPUT;
    }
    let origin = [frame[0], frame[1], frame[2]];
    let xaxis = [frame[3], frame[4], frame[5]];
    let yaxis = [frame[6], frame[7], frame[8]];
    let x_length = finite_norm3(xaxis[0], xaxis[1], xaxis[2]);
    let y_length = finite_norm3(yaxis[0], yaxis[1], yaxis[2]);
    let orthogonality = xaxis[0] * yaxis[0] + xaxis[1] * yaxis[1] + xaxis[2] * yaxis[2];
    if (x_length - 1.0).abs() > 1.0e-6
        || (y_length - 1.0).abs() > 1.0e-6
        || orthogonality.abs() > 1.0e-6
    {
        return ERR_INPUT;
    }
    if point_count == 0 {
        return 0;
    }
    let points = slice::from_raw_parts(points_ptr, point_values);
    if points.iter().any(|value| !value.is_finite()) {
        return ERR_INPUT;
    }
    let zaxis = cross3(xaxis, yaxis);
    let mut lower = [f64::INFINITY; 3];
    let mut upper = [f64::NEG_INFINITY; 3];
    for point in points.chunks_exact(3) {
        let delta = [
            point[0] - origin[0],
            point[1] - origin[1],
            point[2] - origin[2],
        ];
        let local = [
            delta[0] * xaxis[0] + delta[1] * xaxis[1] + delta[2] * xaxis[2],
            delta[0] * yaxis[0] + delta[1] * yaxis[1] + delta[2] * yaxis[2],
            delta[0] * zaxis[0] + delta[1] * zaxis[1] + delta[2] * zaxis[2],
        ];
        for axis in 0..3 {
            lower[axis] = lower[axis].min(local[axis]);
            upper[axis] = upper[axis].max(local[axis]);
        }
    }
    let middle = [
        (lower[0] + upper[0]) / 2.0,
        (lower[1] + upper[1]) / 2.0,
        (lower[2] + upper[2]) / 2.0,
    ];
    let output = slice::from_raw_parts_mut(out_xyz_ptr, 3);
    output[0] = origin[0] + xaxis[0] * middle[0] + yaxis[0] * middle[1] + zaxis[0] * middle[2];
    output[1] = origin[1] + xaxis[1] * middle[0] + yaxis[1] * middle[1] + zaxis[1] * middle[2];
    output[2] = origin[2] + xaxis[2] * middle[0] + yaxis[2] * middle[1] + zaxis[2] * middle[2];
    ptr::write(out_valid_ptr, 1);
    0
}

/// Apply ordered selection replacement, addition, or removal to opaque u32 IDs.
///
/// Actions are 0=replace, 1=add, and 2=remove. First occurrences win; output
/// always follows input traversal order and never hash-table iteration order.
/// A zero-capacity sizing call writes the required row count without output.
///
/// # Safety
///
/// Input buffers must contain the declared u32 counts. `out_count` is always
/// writable. A nonzero output capacity requires a writable output buffer.
#[no_mangle]
pub unsafe extern "C" fn narwhal_ordered_selection_apply_u32(
    current_ptr: *const u32,
    current_count: usize,
    hits_ptr: *const u32,
    hit_count: usize,
    action: u8,
    output_ptr: *mut u32,
    output_capacity: usize,
    out_count_ptr: *mut u64,
) -> i32 {
    if out_count_ptr.is_null() {
        return ERR_NULL;
    }
    ptr::write(out_count_ptr, 0);
    if (current_count > 0 && current_ptr.is_null())
        || (hit_count > 0 && hits_ptr.is_null())
        || (output_capacity > 0 && output_ptr.is_null())
    {
        return ERR_NULL;
    }
    if action > 2
        || !valid_slice_len::<u32>(current_count)
        || !valid_slice_len::<u32>(hit_count)
        || !valid_slice_len::<u32>(output_capacity)
    {
        return if action > 2 { ERR_INPUT } else { ERR_SIZE };
    }
    let current = if current_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(current_ptr, current_count)
    };
    let hits = if hit_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(hits_ptr, hit_count)
    };
    let maximum = match current_count.checked_add(hit_count) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let mut result = Vec::with_capacity(maximum);
    match action {
        0 => {
            let mut seen = HashSet::with_capacity(hit_count);
            for &value in hits {
                if seen.insert(value) {
                    result.push(value);
                }
            }
        }
        1 => {
            let mut seen = HashSet::with_capacity(maximum);
            for &value in current.iter().chain(hits.iter()) {
                if seen.insert(value) {
                    result.push(value);
                }
            }
        }
        2 => {
            let drop: HashSet<u32> = hits.iter().copied().collect();
            let mut seen = HashSet::with_capacity(current_count);
            for &value in current {
                if !drop.contains(&value) && seen.insert(value) {
                    result.push(value);
                }
            }
        }
        _ => unreachable!(),
    }
    let result_count = match u64::try_from(result.len()) {
        Ok(value) => value,
        Err(_) => return ERR_SIZE,
    };
    ptr::write(out_count_ptr, result_count);
    if output_capacity == 0 {
        return 0;
    }
    if output_capacity < result.len() {
        return ERR_SIZE;
    }
    let output = slice::from_raw_parts_mut(output_ptr, output_capacity);
    output.fill(u32::MAX);
    output[..result.len()].copy_from_slice(&result);
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    const IDENTITY: [f64; 16] = [
        1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0,
    ];

    #[test]
    fn static_discrete_snaps_preserve_mid_seam_quad_and_target_order() {
        let points = [
            0.0_f64, 0.0, 0.0, 2.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 2.0,
            2.0, 0.0, 0.0, 0.0, 0.0,
        ];
        let offsets = [0_u64, 3, 6, 7];
        let knots = [4.0_f64, 0.0, 0.0];
        let knot_offsets = [0_u64, 1, 1, 1];
        let centers = [5.0_f64, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0];
        let center_valid = [1_u8, 0, 0];
        let flags = [0_u8, TARGET_CLOSED, TARGET_POINT];
        let frame = [0.0_f64, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0];
        let snaps = static_snap_survivors(
            &points,
            &offsets,
            &points,
            &offsets,
            &knots,
            &knot_offsets,
            &centers,
            &center_valid,
            &flags,
            &frame,
            &IDENTITY,
            100.0,
            100.0,
            [50.0, 50.0],
            1000.0,
            SNAP_ALL_STATIC,
        );
        assert!(snaps
            .windows(2)
            .all(|pair| pair[0].target <= pair[1].target));
        assert!(snaps
            .iter()
            .any(|snap| snap.target == 0 && snap.mode == 2 && snap.point == [5.0, 0.0, 0.0]));
        assert!(snaps
            .iter()
            .any(|snap| snap.target == 1 && snap.mode == 2 && snap.point == [1.0, 1.0, 0.0]));
        let point_rows: Vec<StaticSnap> = snaps
            .iter()
            .copied()
            .filter(|snap| snap.target == 2)
            .collect();
        assert_eq!(point_rows.len(), 1);
        assert_eq!(point_rows[0].mode, 5);
        assert_eq!(point_rows[0].point, [0.0, 0.0, 0.0]);
        let mode_order: Vec<u8> = snaps
            .iter()
            .filter(|snap| snap.target == 0)
            .map(|snap| snap.mode)
            .collect();
        assert!(mode_order.windows(2).all(|pair| pair[0] <= pair[1]));

        let quad_points = [
            0.0_f64, 0.0, 0.0, 4.0, 0.0, 0.0, 0.0, 4.0, 0.0, 3.0, 3.0, 0.0,
        ];
        let quad_offsets = [0_u64, 4];
        let empty_offsets = [0_u64, 0];
        let quad_centers = [0.0_f64; 3];
        let quad_valid = [0_u8];
        let quad_flags = [0_u8];
        let root = 2.0_f64.sqrt() / 2.0;
        let rotated = [0.0_f64, 0.0, 0.0, root, root, 0.0, -root, root, 0.0];
        let quadrants = static_snap_survivors(
            &quad_points,
            &quad_offsets,
            &[],
            &empty_offsets,
            &[],
            &empty_offsets,
            &quad_centers,
            &quad_valid,
            &quad_flags,
            &rotated,
            &IDENTITY,
            100.0,
            100.0,
            [50.0, 50.0],
            1000.0,
            SNAP_QUAD,
        );
        let quad_rows: Vec<[f64; 3]> = quadrants.iter().map(|snap| snap.point).collect();
        assert_eq!(
            quad_rows,
            vec![
                [0.0, 0.0, 0.0],
                [3.0, 3.0, 0.0],
                [4.0, 0.0, 0.0],
                [0.0, 4.0, 0.0],
            ]
        );

        let boundary_point = [0.2_f64, 0.0, 0.0];
        let boundary_offsets = [0_u64, 1];
        let boundary_flags = [TARGET_POINT];
        let boundary = static_snap_survivors(
            &boundary_point,
            &boundary_offsets,
            &[],
            &empty_offsets,
            &[],
            &empty_offsets,
            &quad_centers,
            &quad_valid,
            &boundary_flags,
            &frame,
            &IDENTITY,
            100.0,
            100.0,
            [50.0, 50.0],
            10.0,
            SNAP_POINT,
        );
        assert_eq!(boundary.len(), 1);
        assert_eq!(boundary[0].distance, 10.0);

        let mouse = [50.0_f64, 50.0];
        let mut xyz = [0.0_f64; 192];
        let mut targets = [0_u64; 64];
        let mut modes = [0_u8; 64];
        let mut distances = [0.0_f64; 64];
        let mut written = 77_u64;
        let valid_code = unsafe {
            narwhal_discrete_snap_survivors_f64(
                points.as_ptr(),
                7,
                offsets.as_ptr(),
                points.as_ptr(),
                7,
                offsets.as_ptr(),
                knots.as_ptr(),
                1,
                knot_offsets.as_ptr(),
                centers.as_ptr(),
                center_valid.as_ptr(),
                flags.as_ptr(),
                3,
                frame.as_ptr(),
                IDENTITY.as_ptr(),
                100.0,
                100.0,
                mouse.as_ptr(),
                1000.0,
                SNAP_ALL_STATIC,
                xyz.as_mut_ptr(),
                targets.as_mut_ptr(),
                modes.as_mut_ptr(),
                distances.as_mut_ptr(),
                64,
                &mut written,
            )
        };
        assert_eq!(valid_code, 0);
        assert_eq!(written as usize, snaps.len());

        written = 77;
        let null_offsets = unsafe {
            narwhal_discrete_snap_survivors_f64(
                points.as_ptr(),
                7,
                ptr::null(),
                points.as_ptr(),
                7,
                offsets.as_ptr(),
                knots.as_ptr(),
                1,
                knot_offsets.as_ptr(),
                centers.as_ptr(),
                center_valid.as_ptr(),
                flags.as_ptr(),
                3,
                frame.as_ptr(),
                IDENTITY.as_ptr(),
                100.0,
                100.0,
                mouse.as_ptr(),
                1000.0,
                SNAP_ALL_STATIC,
                xyz.as_mut_ptr(),
                targets.as_mut_ptr(),
                modes.as_mut_ptr(),
                distances.as_mut_ptr(),
                64,
                &mut written,
            )
        };
        assert_eq!((null_offsets, written), (ERR_NULL, 0));

        written = 77;
        let invalid_mask = unsafe {
            narwhal_discrete_snap_survivors_f64(
                points.as_ptr(),
                7,
                offsets.as_ptr(),
                points.as_ptr(),
                7,
                offsets.as_ptr(),
                knots.as_ptr(),
                1,
                knot_offsets.as_ptr(),
                centers.as_ptr(),
                center_valid.as_ptr(),
                flags.as_ptr(),
                3,
                frame.as_ptr(),
                IDENTITY.as_ptr(),
                100.0,
                100.0,
                mouse.as_ptr(),
                1000.0,
                0,
                xyz.as_mut_ptr(),
                targets.as_mut_ptr(),
                modes.as_mut_ptr(),
                distances.as_mut_ptr(),
                64,
                &mut written,
            )
        };
        assert_eq!((invalid_mask, written), (ERR_INPUT, 0));

        written = 77;
        let undersized = unsafe {
            narwhal_discrete_snap_survivors_f64(
                points.as_ptr(),
                7,
                offsets.as_ptr(),
                points.as_ptr(),
                7,
                offsets.as_ptr(),
                knots.as_ptr(),
                1,
                knot_offsets.as_ptr(),
                centers.as_ptr(),
                center_valid.as_ptr(),
                flags.as_ptr(),
                3,
                frame.as_ptr(),
                IDENTITY.as_ptr(),
                100.0,
                100.0,
                mouse.as_ptr(),
                1000.0,
                SNAP_ALL_STATIC,
                xyz.as_mut_ptr(),
                targets.as_mut_ptr(),
                modes.as_mut_ptr(),
                distances.as_mut_ptr(),
                1,
                &mut written,
            )
        };
        assert_eq!(undersized, ERR_SIZE);
        assert_eq!(written as usize, snaps.len());
    }

    #[test]
    fn oriented_bbox_center_uses_all_active_frame_axes() {
        let root = 2.0_f64.sqrt() / 2.0;
        let frame = [0.0_f64, 0.0, 0.0, root, root, 0.0, -root, root, 0.0];
        let points = [0.0_f64, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 1.0, 0.0];
        let mut output = [0.0_f64; 3];
        let mut valid = 9_u8;
        let code = unsafe {
            narwhal_oriented_bbox_center_f64(
                points.as_ptr(),
                3,
                frame.as_ptr(),
                output.as_mut_ptr(),
                &mut valid,
            )
        };
        assert_eq!((code, valid), (0, 1));
        assert!((output[0] - 0.75).abs() < 1.0e-12);
        assert!((output[1] - 0.25).abs() < 1.0e-12);
        assert_eq!(output[2], 0.0);

        let front = [10.0_f64, 20.0, 30.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0];
        let depth_points = [12.0_f64, 17.0, 34.0, 16.0, 19.0, 38.0];
        let code = unsafe {
            narwhal_oriented_bbox_center_f64(
                depth_points.as_ptr(),
                2,
                front.as_ptr(),
                output.as_mut_ptr(),
                &mut valid,
            )
        };
        assert_eq!((code, valid), (0, 1));
        assert_eq!(output, [14.0, 18.0, 36.0]);

        let empty = unsafe {
            narwhal_oriented_bbox_center_f64(
                ptr::null(),
                0,
                frame.as_ptr(),
                output.as_mut_ptr(),
                &mut valid,
            )
        };
        assert_eq!((empty, valid), (0, 0));
        assert!(output.iter().all(|value| value.is_nan()));
    }

    #[test]
    fn ordered_selection_algebra_preserves_first_occurrence_and_input_order() {
        let current = [3_u32, 1, 3, 2];
        let hits = [2_u32, 4, 4, 1, 5];
        let cases = [
            (0_u8, vec![2_u32, 4, 1, 5]),
            (1_u8, vec![3_u32, 1, 2, 4, 5]),
            (2_u8, vec![3_u32]),
        ];
        for (action, expected) in cases {
            let mut required = u64::MAX;
            let size_code = unsafe {
                narwhal_ordered_selection_apply_u32(
                    current.as_ptr(),
                    current.len(),
                    hits.as_ptr(),
                    hits.len(),
                    action,
                    ptr::null_mut(),
                    0,
                    &mut required,
                )
            };
            assert_eq!(size_code, 0);
            assert_eq!(required as usize, expected.len());
            let mut output = vec![99_u32; current.len() + hits.len()];
            let code = unsafe {
                narwhal_ordered_selection_apply_u32(
                    current.as_ptr(),
                    current.len(),
                    hits.as_ptr(),
                    hits.len(),
                    action,
                    output.as_mut_ptr(),
                    output.len(),
                    &mut required,
                )
            };
            assert_eq!(code, 0);
            assert_eq!(&output[..expected.len()], expected.as_slice());
            assert!(output[expected.len()..]
                .iter()
                .all(|value| *value == u32::MAX));
        }
        let mut count = 77_u64;
        let invalid = unsafe {
            narwhal_ordered_selection_apply_u32(
                ptr::null(),
                0,
                ptr::null(),
                0,
                9,
                ptr::null_mut(),
                0,
                &mut count,
            )
        };
        assert_eq!((invalid, count), (ERR_INPUT, 0));
    }

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
        let far_visible = [1_u8; 101];
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
    fn display_exports_prepare_once_and_snap_rejects_far_axes_before_hypot() {
        let points = [0.0_f64, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 10.0, 0.0];
        let reset_prepare = || MATRIX_PREPARE_CALLS.with(|calls| calls.set(0));
        let prepare_count = || MATRIX_PREPARE_CALLS.with(std::cell::Cell::get);

        let mut pixels = [f64::NAN; 6];
        let mut visible = [0_u8; 3];
        reset_prepare();
        assert_eq!(
            unsafe {
                narwhal_project_display_points_f64(
                    IDENTITY.as_ptr(),
                    points.as_ptr(),
                    3,
                    800.0,
                    600.0,
                    pixels.as_mut_ptr(),
                    visible.as_mut_ptr(),
                )
            },
            0
        );
        assert_eq!(prepare_count(), 1);

        let mut indices = [u64::MAX; 3];
        let mut distances = [f64::INFINITY; 3];
        let mut survivor_count = 0_u64;
        SNAP_HYPOT_CALLS.with(|calls| calls.set(0));
        reset_prepare();
        assert_eq!(
            unsafe {
                narwhal_projected_snap_distances_f64(
                    IDENTITY.as_ptr(),
                    points.as_ptr(),
                    3,
                    800.0,
                    600.0,
                    [400.0_f64, 300.0].as_ptr(),
                    1.0,
                    indices.as_mut_ptr(),
                    distances.as_mut_ptr(),
                    &mut survivor_count,
                )
            },
            0
        );
        assert_eq!(
            (prepare_count(), survivor_count, indices[0], distances[0]),
            (1, 1, 0, 0.0)
        );
        assert_eq!(SNAP_HYPOT_CALLS.with(std::cell::Cell::get), 1);

        let offsets = [0_u64, 3];
        let mut segment = [u64::MAX];
        let mut parameter = [f64::NAN];
        let mut near_distance = [f64::INFINITY];
        let mut near_valid = [0_u8];
        reset_prepare();
        assert_eq!(
            unsafe {
                narwhal_nearest_projected_polylines_f64(
                    IDENTITY.as_ptr(),
                    points.as_ptr(),
                    3,
                    offsets.as_ptr(),
                    1,
                    800.0,
                    600.0,
                    [400.0_f64, 300.0].as_ptr(),
                    segment.as_mut_ptr(),
                    parameter.as_mut_ptr(),
                    near_distance.as_mut_ptr(),
                    near_valid.as_mut_ptr(),
                )
            },
            0
        );
        assert_eq!(prepare_count(), 1);

        let mut point_index = u64::MAX;
        let mut point_distance = f64::INFINITY;
        reset_prepare();
        assert_eq!(
            unsafe {
                narwhal_nearest_projected_point_f64(
                    IDENTITY.as_ptr(),
                    points.as_ptr(),
                    3,
                    800.0,
                    600.0,
                    [400.0_f64, 300.0].as_ptr(),
                    1.0,
                    &mut point_index,
                    &mut point_distance,
                )
            },
            1
        );
        assert_eq!(prepare_count(), 1);

        let mut inside = [0_u8; 3];
        reset_prepare();
        assert_eq!(
            unsafe {
                narwhal_projected_points_rect_query_f64(
                    IDENTITY.as_ptr(),
                    points.as_ptr(),
                    3,
                    800.0,
                    600.0,
                    [399.0_f64, 299.0, 401.0, 301.0].as_ptr(),
                    inside.as_mut_ptr(),
                )
            },
            0
        );
        assert_eq!(prepare_count(), 1);
    }

    #[test]
    fn projected_near_rejects_far_segments_after_each_target_has_a_winner() {
        let mut points = vec![0.0_f64, 0.0, 0.0, 0.0, 0.0, 0.0];
        for point in 0..99 {
            points.extend_from_slice(&[10.0 + f64::from(point), 10.0, 0.0]);
        }
        let offsets = [0_u64, 101];
        let mouse = [400.0_f64, 300.0];
        let mut segment = [u64::MAX];
        let mut parameter = [f64::NAN];
        let mut distance = [f64::INFINITY];
        let mut valid = [0_u8];
        NEAR_DISTANCE_CALLS.store(0, std::sync::atomic::Ordering::Relaxed);
        let result = unsafe {
            narwhal_nearest_projected_polylines_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                101,
                offsets.as_ptr(),
                1,
                800.0,
                600.0,
                mouse.as_ptr(),
                segment.as_mut_ptr(),
                parameter.as_mut_ptr(),
                distance.as_mut_ptr(),
                valid.as_mut_ptr(),
            )
        };
        assert_eq!(
            (result, segment, parameter, distance, valid),
            (0, [0], [0.0], [0.0], [1])
        );
        assert_eq!(
            NEAR_DISTANCE_CALLS.load(std::sync::atomic::Ordering::Relaxed),
            2,
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
    fn compact_world_rect_mask_preserves_modes_edges_and_point_flags() {
        let points = [
            -0.5_f64, -0.5, 0.0, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0,
        ];
        let offsets = [0_u64, 2, 4];
        let flags = [0_u8, 1];
        let window = [300.0, 200.0, 500.0, 400.0];
        let mut hits = [9_u8; 2];
        let code = unsafe {
            narwhal_world_rect_hit_mask_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                4,
                offsets.as_ptr(),
                2,
                flags.as_ptr(),
                800.0,
                600.0,
                window.as_ptr(),
                0,
                hits.as_mut_ptr(),
            )
        };
        assert_eq!(code, 0);
        assert_eq!(hits, [0, 1]);

        let exact_window = [200.0, 150.0, 600.0, 450.0];
        let code = unsafe {
            narwhal_world_rect_hit_mask_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                4,
                offsets.as_ptr(),
                2,
                flags.as_ptr(),
                800.0,
                600.0,
                exact_window.as_ptr(),
                0,
                hits.as_mut_ptr(),
            )
        };
        assert_eq!(code, 0);
        assert_eq!(hits, [1, 1]);

        let crossing = [600.0, 450.0, 700.0, 500.0];
        let code = unsafe {
            narwhal_world_rect_hit_mask_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                4,
                offsets.as_ptr(),
                2,
                flags.as_ptr(),
                800.0,
                600.0,
                crossing.as_ptr(),
                1,
                hits.as_mut_ptr(),
            )
        };
        assert_eq!(code, 0);
        assert_eq!(hits, [1, 0]);

        let point_window = [399.0, 299.0, 401.0, 301.0];
        let code = unsafe {
            narwhal_world_rect_hit_mask_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                4,
                offsets.as_ptr(),
                2,
                flags.as_ptr(),
                800.0,
                600.0,
                point_window.as_ptr(),
                0,
                hits.as_mut_ptr(),
            )
        };
        assert_eq!(code, 0);
        assert_eq!(hits, [0, 1]);

        hits.fill(9);
        let invalid_mode = unsafe {
            narwhal_world_rect_hit_mask_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                4,
                offsets.as_ptr(),
                2,
                flags.as_ptr(),
                800.0,
                600.0,
                window.as_ptr(),
                2,
                hits.as_mut_ptr(),
            )
        };
        assert_eq!(invalid_mode, ERR_INPUT);
        assert_eq!(hits, [0, 0]);
        let bad_offsets = [0_u64, 3, 2];
        let bad_offset_code = unsafe {
            narwhal_world_rect_hit_mask_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                4,
                bad_offsets.as_ptr(),
                2,
                flags.as_ptr(),
                800.0,
                600.0,
                window.as_ptr(),
                0,
                hits.as_mut_ptr(),
            )
        };
        assert_eq!(bad_offset_code, ERR_INPUT);
        let bad_flags = [0_u8, 2];
        let bad_flag_code = unsafe {
            narwhal_world_rect_hit_mask_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                4,
                offsets.as_ptr(),
                2,
                bad_flags.as_ptr(),
                800.0,
                600.0,
                window.as_ptr(),
                0,
                hits.as_mut_ptr(),
            )
        };
        assert_eq!(bad_flag_code, ERR_INPUT);
    }

    #[test]
    fn reached_rectangles_are_compact_ordered_and_inclusive() {
        let x0 = [0.0_f64, 100.0, f64::NAN, 200.0];
        let y0 = [0.0_f64, 100.0, f64::NAN, 200.0];
        let x1 = [10.0_f64, 110.0, f64::NAN, 210.0];
        let y1 = [10.0_f64, 110.0, f64::NAN, 210.0];
        let unbounded = [0_u8, 1, 0, 1];
        let behind = [0_u8, 0, 0, 1];
        let has = [1_u8, 1, 0, 1];
        let mouse = [15.0_f64, 5.0];
        let mut out = [u64::MAX; 4];
        let mut written = u64::MAX;
        let code = unsafe {
            narwhal_reached_screen_rect_indices_f64(
                x0.as_ptr(),
                y0.as_ptr(),
                x1.as_ptr(),
                y1.as_ptr(),
                unbounded.as_ptr(),
                behind.as_ptr(),
                has.as_ptr(),
                4,
                mouse.as_ptr(),
                1.0,
                4.0,
                out.as_mut_ptr(),
                out.len(),
                &mut written,
            )
        };
        assert_eq!(code, 0);
        assert_eq!(written, 3);
        assert_eq!(&out[..3], &[0, 1, 2], "boundary, unbounded, no-extent");
    }

    #[test]
    fn nearest_sample_preserves_earliest_ties_without_a_radius() {
        let points = [-0.25_f64, 0.0, 0.0, 0.25, 0.0, 0.0];
        let mouse = [400.0_f64, 300.0];
        let mut index = u64::MAX;
        let mut distance2 = f64::INFINITY;
        let code = unsafe {
            narwhal_nearest_projected_sample_f64(
                IDENTITY.as_ptr(),
                points.as_ptr(),
                2,
                800.0,
                600.0,
                mouse.as_ptr(),
                &mut index,
                &mut distance2,
            )
        };
        assert_eq!(code, 1);
        assert_eq!(index, 0, "strict tie keeps the earliest coarse bracket");
        assert_eq!(distance2, 10_000.0);
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

    #[test]
    fn intersection_snaps_preserve_order_inclusive_tolerance_and_dedup() {
        let points = [
            0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 10.0, 0.0, 0.0, 8.0, -2.0, 0.0, 8.0, 2.0, 0.0, 5.0, -2.0,
            0.0, 5.0, 2.0, 0.0, 2.0, -2.0, 1.0, 2.0, 2.0, 1.0,
        ];
        let offsets = [0_u64, 3, 5, 7, 9];
        let exact = intersection_snaps(&points, &offsets, 0.0);
        assert_eq!(exact.len(), 2);
        assert_eq!(
            exact[0],
            IntersectionSnap {
                point: [8.0, 0.0, 0.0],
                target: 0
            }
        );
        assert_eq!(
            exact[1],
            IntersectionSnap {
                point: [5.0, 0.0, 0.0],
                target: 0
            }
        );
        let boundary = intersection_snaps(&points, &offsets, 1.0);
        assert!(boundary.iter().any(|snap| snap.point == [2.0, 0.0, 0.5]));

        let mut required = u64::MAX;
        let first = unsafe {
            narwhal_intersection_snaps_f64(
                points.as_ptr(),
                9,
                offsets.as_ptr(),
                4,
                0.0,
                ptr::null_mut(),
                ptr::null_mut(),
                0,
                &mut required,
            )
        };
        assert_eq!((first, required), (0, 2));
        let mut xyz = [f64::NAN; 6];
        let mut targets = [u64::MAX; 2];
        let second = unsafe {
            narwhal_intersection_snaps_f64(
                points.as_ptr(),
                9,
                offsets.as_ptr(),
                4,
                0.0,
                xyz.as_mut_ptr(),
                targets.as_mut_ptr(),
                2,
                &mut required,
            )
        };
        assert_eq!(second, 0);
        assert_eq!(xyz, [8.0, 0.0, 0.0, 5.0, 0.0, 0.0]);
        assert_eq!(targets, [0, 0]);
    }

    #[test]
    fn dynamic_perp_tan_preserves_strict_feet_crossings_and_closed_seam() {
        let points = [
            0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, -1.0,
            0.0,
        ];
        let offsets = [0_u64, 2, 6];
        let closed = [0_u8, 1];
        let base = [4.0, 3.0, 0.0];
        let snaps = dynamic_snaps(&points, &offsets, &closed, base, DYNAMIC_PERP | DYNAMIC_TAN);
        assert!(snaps.contains(&DynamicSnap {
            point: [4.0, 0.0, 0.0],
            target: 0,
            kind: DYNAMIC_PERP,
        }));
        assert!(!snaps.iter().any(|snap| snap.kind == DYNAMIC_PERP
            && (snap.point == [0.0, 0.0, 0.0] || snap.point == [10.0, 0.0, 0.0])));
        let endpoint = dynamic_snaps(&points, &offsets, &closed, [0.0, 3.0, 0.0], DYNAMIC_PERP);
        assert!(!endpoint.iter().any(|snap| snap.target == 0));
        assert!(snaps
            .iter()
            .any(|snap| snap.target == 1 && snap.kind == DYNAMIC_TAN));

        let seam_base = [2.0, -2.0, 0.0];
        let seam = dynamic_snaps(&points, &offsets, &closed, seam_base, DYNAMIC_TAN);
        let seam_points: Vec<[f64; 3]> = seam
            .iter()
            .filter(|snap| snap.target == 1 && snap.kind == DYNAMIC_TAN)
            .map(|snap| snap.point)
            .collect();
        assert_eq!(seam_points, vec![[0.75, 0.25, 0.0], [-0.25, -0.75, 0.0]]);

        let mut required = u64::MAX;
        let sizing = unsafe {
            narwhal_dynamic_perp_tan_snaps_f64(
                points.as_ptr(),
                6,
                offsets.as_ptr(),
                closed.as_ptr(),
                2,
                base.as_ptr(),
                DYNAMIC_PERP | DYNAMIC_TAN,
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
                0,
                &mut required,
            )
        };
        assert_eq!(sizing, 0);
        assert_eq!(required as usize, snaps.len());
    }
}
