//! Display-only sampling of the clamped uniform B-spline a Curve preview shows.
//!
//! Mirrors `narwhal3d/preview.py` `control_point_curve_polyline` operation for
//! operation (same knot vector, span search, de Boor update order and parameter
//! arithmetic), so the samples match the Python fallback. The output is a preview
//! polyline only: it is never stored, measured or exported as document geometry.

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
/// More control points than any interactive Curve preview draws.
const MAX_POINTS: usize = 100_000;
const MAX_PER_SPAN: u32 = 1_024;

/// `p+1` zeros, the interior knots `1 .. n-p-1`, then `p+1` copies of `n-p`.
fn clamped_uniform_knots(n: usize, degree: usize) -> Vec<f64> {
    let mut knots = vec![0.0; degree + 1];
    knots.extend((1..n - degree).map(|k| k as f64));
    knots.extend(std::iter::repeat_n((n - degree) as f64, degree + 1));
    knots
}

fn de_boor(
    control: &[[f64; 3]],
    degree: usize,
    u: f64,
    knots: &[f64],
    d: &mut Vec<[f64; 3]>,
) -> [f64; 3] {
    let n = control.len();
    let mut k = degree;
    while k < n - 1 && knots[k + 1] <= u {
        k += 1;
    }
    d.clear();
    d.extend_from_slice(&control[k - degree..=k]);
    for r in 1..=degree {
        for j in (r..=degree).rev() {
            let lo = knots[j + k - degree];
            let hi = knots[j + 1 + k - r];
            let a = if hi == lo { 0.0 } else { (u - lo) / (hi - lo) };
            d[j] = std::array::from_fn(|i| (1.0 - a) * d[j - 1][i] + a * d[j][i]);
        }
    }
    d[degree]
}

/// Number of samples `control_point_curve_polyline` returns for these inputs.
pub fn sample_count(point_count: usize, max_degree: u32, per_span: u32) -> usize {
    if point_count < 2 {
        return 0;
    }
    let degree = (max_degree as usize).min(point_count - 1);
    if degree <= 1 {
        return point_count;
    }
    (per_span as usize * (point_count - degree)).max(1) + 1
}

/// The preview polyline for `control`: the control polygon at degree one or less,
/// otherwise `per_span` samples per span plus the exact last control point.
pub fn control_point_curve_polyline(
    control: &[[f64; 3]],
    max_degree: u32,
    per_span: u32,
) -> Vec<[f64; 3]> {
    let n = control.len();
    if n < 2 {
        return Vec::new();
    }
    let degree = (max_degree as usize).min(n - 1);
    if degree <= 1 {
        return control.to_vec();
    }
    let knots = clamped_uniform_knots(n, degree);
    let hi = (n - degree) as f64;
    let steps = (per_span as usize * (n - degree)).max(1);
    let mut scratch = Vec::with_capacity(degree + 1);
    let mut out: Vec<[f64; 3]> = (0..steps)
        .map(|i| {
            de_boor(
                control,
                degree,
                hi * i as f64 / steps as f64,
                &knots,
                &mut scratch,
            )
        })
        .collect();
    out.push(control[n - 1]);
    out
}

/// Sample a Curve preview into caller-owned xyz output.
///
/// `out_values` always receives the number of f64 values the full result needs, so
/// a too-small `output` (`ERR_SIZE`) tells the caller what to allocate.
///
/// # Safety
/// `control` is readable for `point_count * 3` values and `output` is writable for
/// `output_capacity` values; `out_values` is writable.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_control_point_curve_preview(
    control: *const f64,
    point_count: usize,
    max_degree: u32,
    per_span: u32,
    output: *mut f64,
    output_capacity: usize,
    out_values: *mut u64,
) -> i32 {
    if out_values.is_null() {
        return ERR_NULL;
    }
    *out_values = 0;
    if !(2..=MAX_POINTS).contains(&point_count) || per_span > MAX_PER_SPAN {
        return ERR_INPUT;
    }
    let Some(values) = crate::checked_slice(control, point_count * 3) else {
        return ERR_NULL;
    };
    if !values.iter().all(|v| v.is_finite()) {
        return ERR_INPUT;
    }
    let needed = sample_count(point_count, max_degree, per_span) * 3;
    *out_values = needed as u64;
    if output_capacity < needed {
        return ERR_SIZE;
    }
    let Some(out) = crate::checked_slice_mut(output, needed) else {
        return ERR_NULL;
    };
    let points: Vec<[f64; 3]> = values.chunks_exact(3).map(|p| [p[0], p[1], p[2]]).collect();
    let samples = control_point_curve_polyline(&points, max_degree, per_span);
    for (slot, point) in out.chunks_exact_mut(3).zip(&samples) {
        slot.copy_from_slice(point);
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn points(n: usize) -> Vec<[f64; 3]> {
        (0..n)
            .map(|i| [i as f64, (i * i) as f64 * 0.5, -(i as f64)])
            .collect()
    }

    #[test]
    fn knots_are_clamped_with_uniform_interior() {
        assert_eq!(
            clamped_uniform_knots(4, 3),
            vec![0., 0., 0., 0., 1., 1., 1., 1.]
        );
        assert_eq!(
            clamped_uniform_knots(6, 3),
            vec![0., 0., 0., 0., 1., 2., 3., 3., 3., 3.]
        );
        assert_eq!(
            clamped_uniform_knots(5, 2),
            vec![0., 0., 0., 1., 2., 3., 3., 3.]
        );
    }

    #[test]
    fn degree_is_clamped_to_point_count_minus_one() {
        // Three points at max degree 3 is a quadratic: one span, so 12 + 1 samples.
        assert_eq!(control_point_curve_polyline(&points(3), 3, 12).len(), 13);
        // Six points: degree 3, three spans.
        assert_eq!(control_point_curve_polyline(&points(6), 3, 12).len(), 37);
        assert_eq!(sample_count(6, 3, 12), 37);
        assert_eq!(sample_count(6, 5, 12), 13);
    }

    #[test]
    fn end_points_are_exactly_the_control_end_points() {
        for n in 3..12 {
            let control = points(n);
            let out = control_point_curve_polyline(&control, 3, 12);
            assert_eq!(out[0], control[0]);
            assert_eq!(out[out.len() - 1], control[n - 1]);
        }
    }

    #[test]
    fn degree_one_is_the_control_polygon() {
        let control = points(5);
        assert_eq!(control_point_curve_polyline(&control, 1, 12), control);
        assert_eq!(control_point_curve_polyline(&points(2), 3, 12), points(2));
        assert!(control_point_curve_polyline(&points(1), 3, 12).is_empty());
    }

    #[test]
    fn quadratic_midpoint_matches_the_closed_form() {
        // One quadratic Bezier span: B(1/2) = (P0 + 2 P1 + P2) / 4.
        let control = [[0., 0., 0.], [2., 4., 0.], [4., 0., 0.]];
        let out = control_point_curve_polyline(&control, 2, 2);
        assert_eq!(out, vec![[0., 0., 0.], [2., 2., 0.], [4., 0., 0.]]);
    }

    #[test]
    fn export_reports_size_and_rejects_bad_input() {
        let control: Vec<f64> = points(6).concat();
        let mut used = 0;
        let mut small = [0.0; 3];
        let rc = unsafe {
            narwhal_pure_core_control_point_curve_preview(
                control.as_ptr(),
                6,
                3,
                12,
                small.as_mut_ptr(),
                3,
                &mut used,
            )
        };
        assert_eq!((rc, used), (ERR_SIZE, 37 * 3));
        let mut out = vec![0.0; 37 * 3];
        let rc = unsafe {
            narwhal_pure_core_control_point_curve_preview(
                control.as_ptr(),
                6,
                3,
                12,
                out.as_mut_ptr(),
                out.len(),
                &mut used,
            )
        };
        assert_eq!((rc, used), (0, 37 * 3));
        let expected: Vec<f64> = control_point_curve_polyline(&points(6), 3, 12).concat();
        assert_eq!(out, expected);
        let mut bad = control.clone();
        bad[4] = f64::NAN;
        let rc = unsafe {
            narwhal_pure_core_control_point_curve_preview(
                bad.as_ptr(),
                6,
                3,
                12,
                out.as_mut_ptr(),
                out.len(),
                &mut used,
            )
        };
        assert_eq!(rc, ERR_INPUT);
        let rc = unsafe {
            narwhal_pure_core_control_point_curve_preview(
                control.as_ptr(),
                1,
                3,
                12,
                out.as_mut_ptr(),
                out.len(),
                &mut used,
            )
        };
        assert_eq!(rc, ERR_INPUT);
    }
}
