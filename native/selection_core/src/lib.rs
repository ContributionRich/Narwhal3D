// SPDX-License-Identifier: GPL-3.0-or-later
//! Display-only selection math for the Narwhal 3D Rust feasibility pilot.
//!
//! This crate never owns document geometry. It accepts packed copies of viewport
//! matrices, display points, and display segments through a small C ABI.

use std::slice;

const ABI_VERSION: u32 = 1;
const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;

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
            if segment_crosses_rect(a, b, rect) {
                return 1;
            }
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
        let matrix = [
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
}
