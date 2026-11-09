//! Display tessellation of one NURBS curve: `bridge._display_samples`.
//!
//! The viewport draws a smooth curve as the polyline `_display_samples` returns:
//! the curve is cut at its full-multiplicity interior knots (C0 corners), each piece
//! is probed uniformly in parameter, and the probe's turn and chord length size it
//! (`tessellate.piece_needs`). A one-span piece of degree <= 3 is walked uniformly at
//! `piece_segments_for`'s count; any other piece places its vertices evenly in need
//! (`need_positions`) and bisects segments whose midpoint is off by more than the
//! tolerance (`_need_spaced`).
//!
//! Every step mirrors the Python in its operation order, and the curve is evaluated
//! with the Near snap's port of openNURBS (`nearest::Nurbs`): `DerivativeAt(t, 1)` for
//! probes and `PointAt(t)` for other vertices. Python's float idioms are repeated
//! exactly: `x ** 2` is libm `pow` (CPython's `float_pow` does not square, and glibc's
//! `pow(x, 2)` differs from `x * x` in about 1 of 1500 values), `math.atan2` keeps
//! CPython's special cases before libm's `atan2`, `sum()` is CPython's compensated
//! sum, and `min` / `max` keep Python's first-argument-on-tie rule. So the parameters
//! and points are bit-identical to the Python route where both use the same libm,
//! which is measured on Linux/glibc only: under Wine, msvcrt's `pow` gave three of 586
//! reference cases different values (up to 227 ulp). `narwhal3d/native_display.py`
//! therefore routes here only on Linux/glibc; elsewhere the Python route stays until
//! parity against that platform's CPython runtime is measured. The output is display
//! vertices only; it never creates or changes document geometry.

use crate::nearest::Nurbs;

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
const MAX_CVS: usize = 1 << 20;
const CV_STRIDE: usize = 4;

/// `bridge.PROBE_PER_SPAN` and `bridge.PROBE_MAX`.
const PROBE_PER_SPAN: usize = 4;
const PROBE_MAX: usize = 512;
/// `bridge.REFINE_ROUNDS`.
const REFINE_ROUNDS: usize = 4;
/// `tessellate.CURVE_MAX_ANGLE_DEG` and `tessellate.CURVE_ANGLE_MIN_CHORD`.
const CURVE_MAX_ANGLE_DEG: f64 = 5.0;
const CURVE_ANGLE_MIN_CHORD: f64 = 4.0;
/// `piece_segments_for`'s and `need_positions`' default `maximum`.
const PIECE_MAXIMUM: f64 = 1024.0;
const NEED_MAXIMUM: f64 = 4096.0;

type P3 = [f64; 3];

extern "C" {
    // The platform libm `pow`, which CPython's `float_pow` calls.
    fn pow(x: f64, y: f64) -> f64;
}

/// CPython `x ** 2` for a float `x`: `float_pow` takes `|x|` (2 is even) and calls
/// libm `pow`. The exponent is opaque so the compiler cannot fold the call to `x * x`.
fn py_square(x: f64) -> f64 {
    if x.is_nan() || x.is_infinite() || x == 0.0 || x.abs() == 1.0 {
        return x * x; // float_pow's special cases, all exact
    }
    unsafe { pow(x.abs(), std::hint::black_box(2.0)) }
}

/// CPython `math.atan2` (`m_atan2`): its special cases, then libm `atan2`.
fn py_atan2(y: f64, x: f64) -> f64 {
    if x.is_nan() || y.is_nan() {
        return f64::NAN;
    }
    if y.is_infinite() {
        let q = std::f64::consts::FRAC_PI_4;
        return if x.is_infinite() {
            if x.is_sign_positive() {
                q.copysign(y)
            } else {
                (3.0 * q).copysign(y)
            }
        } else {
            std::f64::consts::FRAC_PI_2.copysign(y)
        };
    }
    if x.is_infinite() || y == 0.0 {
        return if x.is_sign_positive() {
            0.0 * y
        } else {
            std::f64::consts::PI.copysign(y)
        };
    }
    y.atan2(x)
}

/// Python `max(a, b)`: `a` unless `b > a`.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python `min(a, b)`: `a` unless `b < a`.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// CPython 3.12+'s `sum()` of floats from the int start 0: Neumaier compensated
/// summation, the compensation added once at the end when nonzero and finite.
fn py_sum(values: &[f64]) -> f64 {
    let Some((&first, rest)) = values.split_first() else {
        return 0.0;
    };
    let mut total = 0.0 + first; // `0 + value`: exact, except -0.0 becomes 0.0
    let mut compensation = 0.0;
    for &value in rest {
        let sum = total + value;
        if total.abs() >= value.abs() {
            compensation += (total - sum) + value;
        } else {
            compensation += (value - sum) + total;
        }
        total = sum;
    }
    if compensation != 0.0 && compensation.is_finite() {
        total + compensation
    } else {
        total
    }
}

/// Python `math.ceil` clamped as `max(1, min(int(maximum), ceil(x)))`; `None` where
/// `math.ceil` raises (a non-finite value).
fn clamped_ceil(x: f64, maximum: f64) -> Option<usize> {
    if !x.is_finite() {
        return None;
    }
    Some(x.ceil().clamp(1.0, maximum) as usize)
}

/// `tessellate._turn`.
fn turn(a: P3, b: P3) -> f64 {
    let cx = a[1] * b[2] - a[2] * b[1];
    let cy = a[2] * b[0] - a[0] * b[2];
    let cz = a[0] * b[1] - a[1] * b[0];
    let dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    let cr = (cx * cx + cy * cy + cz * cz).sqrt();
    if cr == 0.0 && dot == 0.0 {
        return 0.0;
    }
    py_atan2(cr, dot)
}

/// `tessellate.piece_needs` with the default `max_angle_deg`.
fn piece_needs(points: &[P3], tangents: &[P3], tolerance: f64, min_chord_factor: f64) -> Vec<f64> {
    // `math.radians`: x * (pi / 180)
    let max_angle = CURVE_MAX_ANGLE_DEG * (std::f64::consts::PI / 180.0);
    let k = points.len() - 1;
    let mut needs = Vec::with_capacity(k);
    for i in 0..k {
        let (p, q) = (points[i], points[i + 1]);
        let seg = (py_square(q[0] - p[0]) + py_square(q[1] - p[1]) + py_square(q[2] - p[2])).sqrt();
        let turn = turn(tangents[i], tangents[i + 1]);
        if turn <= 0.0 {
            needs.push(0.0);
            continue;
        }
        let mut angle_need = if max_angle > 0.0 {
            turn / max_angle
        } else {
            0.0
        };
        let chord_need = if tolerance > 0.0 {
            let chord = (turn * seg / (8.0 * tolerance)).sqrt();
            if min_chord_factor > 0.0 {
                angle_need = py_min(angle_need, seg / (min_chord_factor * tolerance));
            }
            chord
        } else {
            0.0
        };
        needs.push(py_max(chord_need, angle_need));
    }
    needs
}

/// `tessellate.piece_segments_for`.
fn piece_segments_for(points: &[P3], tangents: &[P3], tolerance: f64) -> Option<usize> {
    let needs = piece_needs(points, tangents, tolerance, CURVE_ANGLE_MIN_CHORD);
    let Some((&first, rest)) = needs.split_first() else {
        return Some(1);
    };
    let worst = rest.iter().fold(first, |m, &v| py_max(m, v));
    clamped_ceil(needs.len() as f64 * worst - 1e-9, PIECE_MAXIMUM)
}

/// `tessellate.need_positions`.
fn need_positions(needs: &[f64]) -> Option<Vec<f64>> {
    let total = py_sum(needs);
    let n = clamped_ceil(total - 1e-9, NEED_MAXIMUM)?;
    if n == 1 || total <= 0.0 {
        return Some(Vec::new());
    }
    let mut out = Vec::with_capacity(n - 1);
    let (mut i, mut cum) = (0usize, 0.0);
    for j in 1..n {
        let target = total * j as f64 / n as f64;
        while i < needs.len() - 1 && cum + needs[i] < target {
            cum += needs[i];
            i += 1;
        }
        let frac = if needs[i] > 0.0 {
            (target - cum) / needs[i]
        } else {
            1.0
        };
        out.push(i as f64 + py_min(1.0, py_max(0.0, frac)));
    }
    Some(out)
}

/// `tessellate.point_segment_distance`.
fn point_segment_distance(p: P3, a: P3, b: P3) -> f64 {
    let ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    let ap = [p[0] - a[0], p[1] - a[1], p[2] - a[2]];
    let l2 = ab[0] * ab[0] + ab[1] * ab[1] + ab[2] * ab[2];
    let u = if l2 <= 0.0 {
        0.0
    } else {
        py_max(
            0.0,
            py_min(1.0, (ap[0] * ab[0] + ap[1] * ab[1] + ap[2] * ab[2]) / l2),
        )
    };
    let d = [ap[0] - u * ab[0], ap[1] - u * ab[1], ap[2] - u * ab[2]];
    (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt()
}

/// `tessellate.align_count`.
fn align_count(n: usize, k: usize) -> usize {
    let (n, k) = (n.max(1), k.max(1));
    if n >= k {
        return n.div_ceil(k) * k;
    }
    (n..=k).find(|d| k % d == 0).unwrap_or(k)
}

/// `bridge._knot_groups`: `[value, multiplicity]` of every distinct interior knot.
fn knot_groups(knots: &[f64], lo: f64, hi: f64) -> Vec<(f64, usize)> {
    let eps = (hi - lo) * 1e-9;
    let mut groups: Vec<(f64, usize)> = Vec::new();
    // The knots are already non-decreasing (`Nurbs::new` checks), so `sorted` is a copy.
    for &v in knots {
        if lo + eps < v && v < hi - eps {
            match groups.last_mut() {
                Some(last) if (v - last.0).abs() <= eps => last.1 += 1,
                _ => groups.push((v, 1)),
            }
        }
    }
    groups
}

struct Display<'c, 'a> {
    curve: &'c Nurbs<'a>,
    params: Vec<f64>,
    points: Vec<P3>,
}

impl Display<'_, '_> {
    fn point(&self, t: f64) -> Option<P3> {
        let p = self.curve.point(t);
        p.iter().all(|v| v.is_finite()).then_some(p)
    }

    /// `bridge._need_spaced`.
    #[allow(clippy::too_many_arguments)]
    fn need_spaced(
        &self,
        a: f64,
        b: f64,
        k: usize,
        pts: &[P3],
        tans: &[P3],
        tolerance: f64,
    ) -> Option<(Vec<f64>, Vec<P3>)> {
        let needs = piece_needs(pts, tans, tolerance, 0.0);
        let us = need_positions(&needs)?;
        let mut ts = Vec::with_capacity(us.len() + 2);
        ts.push(a);
        ts.extend(us.iter().map(|&u| a + (b - a) * u / k as f64));
        ts.push(b);
        let mut xs = Vec::with_capacity(ts.len());
        xs.push(pts[0]);
        for &t in &ts[1..ts.len() - 1] {
            xs.push(self.point(t)?);
        }
        xs.push(pts[k]);
        if tolerance <= 0.0 {
            return Some((ts, xs));
        }
        for _ in 0..REFINE_ROUNDS {
            let mut nt = vec![ts[0]];
            let mut nx = vec![xs[0]];
            let mut split = false;
            for i in 0..ts.len() - 1 {
                let tm = 0.5 * (ts[i] + ts[i + 1]);
                let m = self.point(tm)?;
                if point_segment_distance(m, xs[i], xs[i + 1]) > tolerance {
                    nt.push(tm);
                    nx.push(m);
                    split = true;
                }
                nt.push(ts[i + 1]);
                nx.push(xs[i + 1]);
            }
            ts = nt;
            xs = nx;
            if !split {
                break;
            }
        }
        Some((ts, xs))
    }

    /// `bridge._display_samples` for a NURBS curve (degree >= 1) on `[lo, hi]`.
    fn run(mut self, lo: f64, hi: f64, tolerance: f64) -> Option<(Vec<f64>, Vec<P3>)> {
        if hi <= lo {
            // `not hi > lo`; both are finite (the export checks)
            let p = self.point(lo)?;
            return Some((vec![lo], vec![p]));
        }
        let (degree, knots) = self.curve.degree_and_knots();
        let groups = knot_groups(knots, lo, hi);
        let mut breaks = vec![lo];
        breaks.extend(groups.iter().filter(|g| g.1 >= degree).map(|g| g.0));
        breaks.push(hi);
        for w in breaks.windows(2) {
            let (a, b) = (w[0], w[1]);
            let spans = 1 + groups.iter().filter(|g| a < g.0 && g.0 < b).count();
            let k = PROBE_MAX.min(PROBE_PER_SPAN * spans);
            let mut pts = Vec::with_capacity(k + 1);
            let mut tans = Vec::with_capacity(k + 1);
            for j in 0..=k {
                let t = if j == k {
                    b
                } else {
                    a + (b - a) * j as f64 / k as f64
                };
                let [p, v] = self.curve.point_and_tangent(t);
                if !p.iter().chain(&v).all(|c| c.is_finite()) {
                    return None;
                }
                pts.push(p);
                tans.push(v);
            }
            let start = usize::from(!self.params.is_empty());
            if spans > 1 || degree > 3 {
                let (sub_t, sub_p) = self.need_spaced(a, b, k, &pts, &tans, tolerance)?;
                self.params.extend_from_slice(&sub_t[start..]);
                self.points.extend_from_slice(&sub_p[start..]);
                continue;
            }
            let n = align_count(piece_segments_for(&pts, &tans, tolerance)?, k);
            if k.is_multiple_of(n) {
                let step = k / n;
                for i in start..=n {
                    self.params.push(a + (b - a) * i as f64 / n as f64);
                    self.points.push(pts[i * step]);
                }
            } else {
                let m = n / k;
                for i in start..=n {
                    let t = a + (b - a) * i as f64 / n as f64;
                    self.params.push(t);
                    let p = if i % m == 0 {
                        pts[i / m]
                    } else {
                        self.point(t)?
                    };
                    self.points.push(p);
                }
            }
            if let Some(last) = self.params.last_mut() {
                *last = b; // the corner itself, to machine precision
            }
        }
        Some((self.params, self.points))
    }
}

/// `bridge._display_samples(curve, tolerance)` for a NURBS curve.
///
/// The curve is passed in openNURBS form (as for `narwhal_pure_core_nearest_on_nurbs`)
/// with its `Domain` as `lo`/`hi`. Writes the count to `out_count`, then, when it fits
/// `capacity`, the parameters to `out_params` and the points (xyz) to `out_points`;
/// a too-small `capacity` returns `ERR_SIZE` with the count set, so the caller can
/// allocate and call again. Fails (`ERR_INPUT`) where the Python would raise or where
/// openNURBS could not evaluate the curve, so the caller runs the Python.
///
/// # Safety
/// `knots` is readable for `knot_count` values and `cvs` for `cv_count * 4`
/// homogeneous values; `out_params` is writable for `capacity` values,
/// `out_points` for `capacity * 3` and `out_count` for one.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_curve_display_samples(
    order: usize,
    knots: *const f64,
    knot_count: usize,
    cvs: *const f64,
    cv_count: usize,
    rational: u8,
    lo: f64,
    hi: f64,
    tolerance: f64,
    out_params: *mut f64,
    out_points: *mut f64,
    capacity: usize,
    out_count: *mut usize,
) -> i32 {
    if out_count.is_null() {
        return ERR_NULL;
    }
    *out_count = 0;
    if cv_count > MAX_CVS
        || rational > 1
        || !lo.is_finite()
        || !hi.is_finite()
        || tolerance.is_nan()
    {
        return ERR_INPUT;
    }
    let (Some(knot_values), Some(cv_values)) = (
        crate::checked_slice(knots, knot_count),
        crate::checked_slice(cvs, cv_count * CV_STRIDE),
    ) else {
        return ERR_NULL;
    };
    if !knot_values.iter().chain(cv_values).all(|v| v.is_finite()) {
        return ERR_INPUT;
    }
    let Some(curve) = Nurbs::new(order, knot_values, cv_values, rational == 1) else {
        return ERR_INPUT;
    };
    let display = Display {
        curve: &curve,
        params: Vec::new(),
        points: Vec::new(),
    };
    let Some((params, points)) = display.run(lo, hi, tolerance) else {
        return ERR_INPUT;
    };
    *out_count = params.len();
    if params.len() > capacity {
        return ERR_SIZE;
    }
    let (Some(out_t), Some(out_p)) = (
        crate::checked_slice_mut(out_params, params.len()),
        crate::checked_slice_mut(out_points, params.len() * 3),
    ) else {
        return ERR_NULL;
    };
    out_t.copy_from_slice(&params);
    for (slot, p) in out_p.chunks_exact_mut(3).zip(&points) {
        slot.copy_from_slice(p);
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_sum_compensates_like_cpython() {
        // sum([1e100, 1.0, -1e100, 1.0]) is 2.0 in CPython 3.12+, 1.0 by naive addition.
        assert_eq!(py_sum(&[1e100, 1.0, -1e100, 1.0]), 2.0);
        assert_eq!(py_sum(&[]), 0.0);
        assert!(py_sum(&[-0.0]).is_sign_positive());
    }

    #[test]
    fn python_min_max_keep_the_first_argument_on_ties_and_nan() {
        assert!(py_max(0.0, -0.0).is_sign_positive());
        assert!(py_min(-0.0, 0.0).is_sign_negative());
        assert_eq!(py_max(0.0, f64::NAN), 0.0);
        assert_eq!(py_min(1.0, f64::NAN), 1.0);
    }

    // glibc's pow; the expected value is not true of msvcrt (measured under Wine), and
    // other platform libms are unmeasured.
    #[cfg(all(target_os = "linux", target_env = "gnu"))]
    #[test]
    fn square_is_libm_pow_not_a_multiply() {
        // CPython 3.13 on glibc: 157.14081462969125 ** 2 == 24693.235622482985, while
        // the correctly rounded product is 24693.23562248299.
        let x = 157.140_814_629_691_25;
        assert_eq!(py_square(x), 24_693.235_622_482_985);
        assert_eq!(py_square(-x), py_square(x));
        assert_eq!(x * x, 24_693.235_622_482_99);
    }

    #[test]
    fn atan2_keeps_cpython_special_cases() {
        assert_eq!(py_atan2(0.0, 1.0), 0.0);
        assert_eq!(py_atan2(0.0, -1.0), std::f64::consts::PI);
        assert_eq!(py_atan2(1.0, 0.0), std::f64::consts::FRAC_PI_2);
        assert!(py_atan2(f64::NAN, 1.0).is_nan());
    }

    #[test]
    fn align_count_matches_the_python_examples() {
        assert_eq!(
            [3, 5, 9, 1, 4, 0].map(|n| align_count(n, 4)),
            [4, 8, 12, 1, 4, 1]
        );
    }

    #[test]
    fn need_positions_space_vertices_evenly_in_need() {
        assert!(need_positions(&[0.0, 0.0]).unwrap().is_empty());
        assert_eq!(need_positions(&[1.0, 1.0]).unwrap(), vec![1.0]);
        assert_eq!(need_positions(&[2.0, 0.0]).unwrap(), vec![0.5]);
        assert!(need_positions(&[f64::INFINITY]).is_none());
    }

    #[test]
    fn knot_groups_merge_equal_knots_and_skip_the_ends() {
        let knots = [0.0, 0.0, 0.0, 1.0, 1.0, 2.0, 3.0, 3.0, 3.0];
        assert_eq!(knot_groups(&knots, 0.0, 3.0), vec![(1.0, 2), (2.0, 1)]);
    }

    fn samples(
        order: usize,
        knots: &[f64],
        cvs: &[f64],
        lo: f64,
        hi: f64,
        tol: f64,
        capacity: usize,
    ) -> (i32, usize, Vec<f64>, Vec<f64>) {
        let mut params = vec![f64::NAN; capacity];
        let mut points = vec![f64::NAN; capacity * 3];
        let mut count = usize::MAX;
        let code = unsafe {
            narwhal_pure_core_curve_display_samples(
                order,
                knots.as_ptr(),
                knots.len(),
                cvs.as_ptr(),
                cvs.len() / 4,
                0,
                lo,
                hi,
                tol,
                params.as_mut_ptr(),
                points.as_mut_ptr(),
                capacity,
                &mut count,
            )
        };
        (code, count, params, points)
    }

    #[test]
    fn a_straight_cubic_is_one_segment_and_a_corner_is_kept() {
        // Collinear control points: no turn, so the one span is its probe's ends.
        let line: Vec<f64> = (0..4).flat_map(|i| [i as f64, 0.0, 0.0, 1.0]).collect();
        let (code, count, params, points) =
            samples(4, &[0.0, 0.0, 0.0, 1.0, 1.0, 1.0], &line, 0.0, 1.0, 0.01, 8);
        assert_eq!((code, count), (0, 2));
        assert_eq!(&params[..2], &[0.0, 1.0]);
        assert_eq!(&points[..6], &[0.0, 0.0, 0.0, 3.0, 0.0, 0.0]);

        // A quadratic with a full-multiplicity knot at 1: the corner cv is a vertex.
        let bent = [
            0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 1.0, 2.0, 0.0, 0.0, 1.0, 3.0, 1.0, 0.0, 1.0, 4.0,
            0.0, 0.0, 1.0,
        ];
        let knots = [0.0, 0.0, 1.0, 1.0, 2.0, 2.0];
        let (code, count, params, points) = samples(3, &knots, &bent, 0.0, 2.0, 0.01, 4096);
        assert_eq!(code, 0);
        let corner = params[..count]
            .iter()
            .position(|&t| t == 1.0)
            .expect("the corner parameter");
        assert_eq!(&points[corner * 3..corner * 3 + 3], &[2.0, 0.0, 0.0]);
        assert_eq!(params[count - 1], 2.0);
    }

    #[test]
    fn a_small_buffer_reports_the_count_and_bad_input_is_refused() {
        let cvs: Vec<f64> = [[0.0, 0.0], [1.0, 3.0], [3.0, -2.0], [4.0, 1.0]]
            .iter()
            .flat_map(|p| [p[0], p[1], 0.0, 1.0])
            .collect();
        let knots = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0];
        let (code, count, ..) = samples(4, &knots, &cvs, 0.0, 1.0, 0.001, 1);
        assert_eq!(code, ERR_SIZE);
        assert!(count > 2);
        let (code, again, ..) = samples(4, &knots, &cvs, 0.0, 1.0, 0.001, count);
        assert_eq!((code, again), (0, count));
        assert_eq!(
            samples(4, &knots[..5], &cvs, 0.0, 1.0, 0.001, 64).0,
            ERR_INPUT
        );
        assert_eq!(
            samples(4, &knots, &cvs, 0.0, f64::NAN, 0.001, 64).0,
            ERR_INPUT
        );
    }
}
