//! Homogeneous-frustum Near search. Display search only; exact curve refinement
//! remains in the native NURBS/kernel path. No document geometry is changed.
use std::{ptr, slice};

fn lerp(a: [f64; 4], b: [f64; 4], t: f64) -> [f64; 4] {
    std::array::from_fn(|i| a[i] + (b[i] - a[i]) * t)
}

fn clipped_segment(
    a: [f64; 4],
    b: [f64; 4],
    width: f64,
    height: f64,
) -> Option<([f64; 2], [f64; 2], f64, f64, f64, f64)> {
    if !a.iter().chain(b.iter()).all(|x| x.is_finite()) {
        return None;
    }
    let (mut start, mut end) = (0.0_f64, 1.0_f64);
    // Clip before dividing by W. In particular, a segment with one endpoint
    // behind the eye can still cross the near plane and the visible viewport.
    for component in 0..3 {
        for sign in [-1.0, 1.0] {
            let fa = a[3] + sign * a[component];
            let fb = b[3] + sign * b[component];
            if fa < 0.0 && fb < 0.0 {
                return None;
            }
            if fa < 0.0 {
                start = start.max(fa / (fa - fb));
            }
            if fb < 0.0 {
                end = end.min(fa / (fa - fb));
            }
        }
    }
    if start > end {
        return None;
    }
    let p = lerp(a, b, start);
    let q = lerp(a, b, end);
    if p[3] <= 0.0 || q[3] <= 0.0 {
        return None;
    }
    let pixel = |c: [f64; 4]| {
        [
            (c[0] / c[3] + 1.0) * width * 0.5,
            (c[1] / c[3] + 1.0) * height * 0.5,
        ]
    };
    Some((pixel(p), pixel(q), p[3], q[3], start, end))
}

/// Additive Near ABI: same buffers as the legacy query, but it retains visible
/// segment intervals across the near plane and returns a perspective-correct
/// WORLD interpolation parameter, not a screen-linear one.
///
/// # Safety
/// The caller must satisfy all buffer/alignment/non-aliasing requirements of
/// `narwhal_nearest_projected_polylines_f64`. Output indices address the ORIGINAL
/// input polyline, never newly clipped points. Miss rows have sentinel outputs.
#[no_mangle]
pub unsafe extern "C" fn narwhal_nearest_projected_polylines_clipped_f64(
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
    // Retain the already-qualified ABI validation and sentinel initialization.
    let code = super::narwhal_nearest_projected_polylines_f64(
        matrix_ptr,
        points_ptr,
        point_count,
        offsets_ptr,
        curve_count,
        width,
        height,
        mouse_ptr,
        out_segment_ptr,
        out_parameter_ptr,
        out_distance_ptr,
        out_valid_ptr,
    );
    if code != 0 || curve_count == 0 {
        return code;
    }
    let matrix = slice::from_raw_parts(matrix_ptr, 16);
    let points = if point_count == 0 {
        &[]
    } else {
        slice::from_raw_parts(points_ptr, point_count * 3)
    };
    let offsets = slice::from_raw_parts(offsets_ptr, curve_count + 1);
    let mouse = [*mouse_ptr, *mouse_ptr.add(1)];
    for curve in 0..curve_count {
        ptr::write(out_segment_ptr.add(curve), u64::MAX);
        ptr::write(out_parameter_ptr.add(curve), f64::NAN);
        ptr::write(out_distance_ptr.add(curve), f64::INFINITY);
        ptr::write(out_valid_ptr.add(curve), 0);
        let first = offsets[curve] as usize;
        let last = offsets[curve + 1] as usize;
        let mut best = f64::INFINITY;
        for index in first..last.saturating_sub(1) {
            let a = super::selection_clip_point_f64(matrix, &points[index * 3..index * 3 + 3]);
            let b = super::selection_clip_point_f64(
                matrix,
                &points[(index + 1) * 3..(index + 1) * 3 + 3],
            );
            if let Some((p, q, wa, wb, t0, t1)) = clipped_segment(a, b, width, height) {
                let (screen_t, distance) = super::screen_near_parameter(mouse, p, q);
                if distance < best {
                    // Projective interpolation: screen midpoint != world midpoint
                    // when the two endpoints have different depth.
                    let local = screen_t * wa / ((1.0 - screen_t) * wb + screen_t * wa);
                    let world_t = t0 + (t1 - t0) * local;
                    if !world_t.is_finite() {
                        continue;
                    }
                    best = distance;
                    ptr::write(out_segment_ptr.add(curve), (index - first) as u64);
                    ptr::write(out_parameter_ptr.add(curve), world_t.clamp(0.0, 1.0));
                    ptr::write(out_distance_ptr.add(curve), distance);
                    ptr::write(out_valid_ptr.add(curve), 1);
                }
            }
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    const PERSPECTIVE: [f64; 16] = [
        1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., -2., 0., 0., 1., 0.,
    ];
    fn query(points: &[f64], matrix: &[f64; 16]) -> (u64, f64, f64, u8) {
        let offsets = [0, (points.len() / 3) as u64];
        let (mut segment, mut t, mut distance, mut valid) = (u64::MAX, f64::NAN, f64::INFINITY, 0);
        let code = unsafe {
            narwhal_nearest_projected_polylines_clipped_f64(
                matrix.as_ptr(),
                points.as_ptr(),
                points.len() / 3,
                offsets.as_ptr(),
                1,
                800.,
                600.,
                [400., 300.].as_ptr(),
                &mut segment,
                &mut t,
                &mut distance,
                &mut valid,
            )
        };
        assert_eq!(code, 0);
        (segment, t, distance, valid)
    }
    #[test]
    fn snap_clip_near_plane_crossing_keeps_visible_interval() {
        let (segment, t, distance, valid) = query(&[0., -1., -1., 0., 1., 3.], &PERSPECTIVE);
        assert_eq!((segment, valid), (0, 1));
        assert!((t - 0.5).abs() < 1e-12 && distance < 1e-12);
    }
    #[test]
    fn snap_clip_world_parameter_is_perspective_correct() {
        let (_, t, distance, valid) = query(&[-1., 0., 1., 2., 0., 4.], &PERSPECTIVE);
        assert_eq!(valid, 1);
        assert!((t - 1. / 3.).abs() < 1e-12 && distance < 1e-12);
    }
    #[test]
    fn snap_clip_endpoints_outside_frustum_still_cross_center() {
        let (_, t, distance, valid) = query(&[-100., 0., 2., 100., 0., 2.], &PERSPECTIVE);
        assert_eq!(valid, 1);
        assert!((t - 0.5).abs() < 1e-12 && distance < 1e-12);
    }
    #[test]
    fn snap_clip_behind_eye_and_empty_are_misses() {
        for points in [&[0., 0., -2., 1., 0., -1.][..], &[][..], &[0., 0., 2.][..]] {
            let (segment, t, distance, valid) = query(points, &PERSPECTIVE);
            assert_eq!((segment, valid), (u64::MAX, 0));
            assert!(t.is_nan() && distance.is_infinite());
        }
    }
    #[test]
    fn snap_clip_bad_offsets_fail_with_sentinels() {
        let (mut segment, mut t, mut distance, mut valid) = (0, 0., 0., 1);
        let code = unsafe {
            narwhal_nearest_projected_polylines_clipped_f64(
                PERSPECTIVE.as_ptr(),
                [0., 0., 2.].as_ptr(),
                1,
                [1, 0].as_ptr(),
                1,
                800.,
                600.,
                [400., 300.].as_ptr(),
                &mut segment,
                &mut t,
                &mut distance,
                &mut valid,
            )
        };
        assert_ne!(code, 0);
        assert_eq!((segment, valid), (u64::MAX, 0));
        assert!(t.is_nan() && distance.is_infinite());
    }
}
