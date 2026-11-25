//! openNURBS's `ON_NurbsCurve::Trim` and `ON_Intersect(ON_Line, ON_Line)`, for the
//! boundary-preserving curve offset (`offset_boundary`).
//!
//! Ported from openNURBS at eb92af3 (the commit rhino3dm 8.35.0 pins) in their operation
//! order: `ON_NurbsCurve::Trim` and `TweakSplitTrimParameter` (opennurbs_nurbscurve.cpp),
//! `ON_NurbsSpanIndex` and `ON_ClampKnotVector` (opennurbs_knot.cpp),
//! `ON_EvaluateNurbsDeBoor` (opennurbs_evaluate_nurbs.cpp), and `ON_Intersect` for two lines
//! (opennurbs_intersect.cpp) with the `ON_Matrix::RowReduce` / `BackSolve` it calls
//! (opennurbs_matrix.cpp). Every operation is one IEEE-754 multiply, add, subtract or
//! divide, and Rust never fuses them.

use crate::nearest::{search_monotone, Nurbs};

/// `DBL_EPSILON`.
const ON_EPSILON: f64 = f64::EPSILON;
const ON_SQRT_EPSILON: f64 = 1.490116119385e-8;
const ON_UNSET_POSITIVE_VALUE: f64 = 1.23432101234321e+308;
/// Doubles per stored control point: homogeneous `(x*w, y*w, z*w, w)`, as `nearest::Nurbs`.
const STRIDE: usize = 4;

type P3 = [f64; 3];

/// `ON_NurbsSpanIndex(order, cv_count, knot, t, side, hint)` on openNURBS knots.
pub(crate) fn span_index(
    order: usize,
    cv_count: usize,
    knot: &[f64],
    t: f64,
    side: i32,
    hint: usize,
) -> usize {
    let base = order - 2;
    let mut len = cv_count - order + 2;
    let mut shift = 0;
    let mut hint = hint;
    if hint > 0 && hint < len - 1 {
        while hint > 0 && knot[base + hint - 1] == knot[base + hint] {
            hint -= 1;
        }
        if hint > 0 {
            if t < knot[base + hint] {
                len = hint + 1;
                hint = 0;
            } else {
                if side < 0 && t == knot[base + hint] {
                    hint -= 1;
                }
                shift = hint;
                len -= hint;
            }
        }
    } else {
        hint = 0;
    }
    let k = &knot[base + shift..base + shift + len];
    let j = search_monotone(k, t);
    let j = if j < 0 {
        0
    } else if j as usize >= len - 1 {
        len - 2
    } else {
        let mut j = j as usize;
        if side < 0 {
            while j > 0 && t == k[j] {
                j -= 1;
            }
        }
        j
    };
    j + hint
}

/// `TweakSplitTrimParameter(k0, k1, t)`: Some(the knot) where `t` is within the knot
/// tolerance of one end of `[k0, k1]` and well away from the other.
fn tweak_split_trim_parameter(k0: f64, k1: f64, t: f64) -> Option<f64> {
    if k0 < t && t < k1 {
        let ktol1 = (k1 - k0) * ON_SQRT_EPSILON;
        let ktol2 = (k0.abs() + k1.abs()) * 8.0 * ON_EPSILON;
        let ktol = if ktol1 > ktol2 { ktol1 } else { ktol2 };
        if t - k0 <= ktol && k1 - t > 16.0 * ktol {
            return Some(k0);
        } else if k1 - t <= ktol && t - k0 > 16.0 * ktol {
            return Some(k1);
        }
    }
    None
}

/// `ON_EvaluateNurbsDeBoor(cv_dim, order, STRIDE, cv, knots, side, mult_k, t)`: `cv` holds
/// `order` control points, `knots` the span's `2 * order - 2` knots.
#[allow(clippy::too_many_arguments)]
fn de_boor(
    cv_dim: usize,
    order: usize,
    cv: &mut [f64],
    knots: &[f64],
    side: i32,
    mult_k: f64,
    t: f64,
) -> bool {
    let d = order - 1;
    let mut t0 = knots[d - 1];
    let mut t1 = knots[d];
    if t0 == t1 {
        return false;
    }
    let mut blend = |out: usize, a: usize, b: usize, wa: f64, wb: f64| {
        for j in 0..cv_dim {
            cv[out * STRIDE + j] = cv[a * STRIDE + j] * wa + cv[b * STRIDE + j] * wb;
        }
    };
    if side < 0 {
        if t == t1 && t1 == knots[2 * d - 1] {
            return true;
        }
        let full = if side == -2 {
            t0 = mult_k;
            true
        } else {
            t0 == knots[0]
        };
        if !full {
            let delta: Vec<f64> = (0..d).map(|q| t - knots[d - 1 - q]).collect();
            for k in (1..=d).rev() {
                for r in 0..k {
                    let a1 = delta[r] / (knots[d - 1 + k - r] - knots[d - 1 - r]);
                    let a0 = 1.0 - a1;
                    blend(d - r, d - r - 1, d - r, a0, a1);
                }
            }
        } else {
            let dt = t - t0;
            for k in (1..=d).rev() {
                for r in 0..k {
                    let a1 = dt / (knots[d - 1 + k - r] - t0);
                    let a0 = 1.0 - a1;
                    blend(d - r, d - r - 1, d - r, a0, a1);
                }
            }
        }
    } else {
        if t == t0 && t0 == knots[0] {
            return true;
        }
        let full = if side == 2 {
            t1 = mult_k;
            true
        } else {
            t1 == knots[2 * d - 1]
        };
        if !full {
            let delta: Vec<f64> = (0..d).map(|q| knots[d + q] - t).collect();
            for k in (1..=d).rev() {
                for r in 0..k {
                    let a0 = delta[r] / (knots[d + r] - knots[d - k + r]);
                    let a1 = 1.0 - a0;
                    blend(r, r, r + 1, a0, a1);
                }
            }
        } else {
            let dt = t1 - t;
            for k in (1..=d).rev() {
                for r in 0..k {
                    let a0 = dt / (t1 - knots[d - k + r]);
                    let a1 = 1.0 - a0;
                    blend(r, r, r + 1, a0, a1);
                }
            }
        }
    }
    true
}

/// A NURBS curve that owns its openNURBS data, as `ON_NurbsCurve` does.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Curve {
    pub order: usize,
    pub rational: bool,
    /// openNURBS knots: `cv_count + order - 2`.
    pub knots: Vec<f64>,
    /// Homogeneous control points, `STRIDE` doubles each.
    pub cvs: Vec<f64>,
}

impl Curve {
    pub fn cv_count(&self) -> usize {
        self.cvs.len() / STRIDE
    }

    /// The evaluator over this curve; None where `Nurbs::new` refuses the data.
    pub fn view(&self) -> Option<Nurbs<'_>> {
        Nurbs::new(self.order, &self.knots, &self.cvs, self.rational)
    }

    pub fn domain(&self) -> (f64, f64) {
        (self.knots[self.order - 2], self.knots[self.cv_count() - 1])
    }

    fn cv_dim(&self) -> usize {
        if self.rational {
            4
        } else {
            3
        }
    }

    /// `ON_NurbsCurve::Trim(ON_Interval(t0, t1))`; false where openNURBS returns false
    /// (rhino3dm's `Curve.Trim` then gives None). The curve may be changed either way.
    pub fn trim(&mut self, t0: f64, t1: f64) -> bool {
        // `ON_Interval::IsIncreasing`.
        if !(-ON_UNSET_POSITIVE_VALUE < t0 && t0 < t1 && t1 < ON_UNSET_POSITIVE_VALUE) {
            return false;
        }
        if (t0, t1) == self.domain() {
            return true;
        }
        let order = self.order;
        let cv_dim = self.cv_dim();

        // Cut off the right end.
        let t = t1;
        let mut count = self.cv_count();
        let mut ki = span_index(order, count, &self.knots, t, -1, 0);
        if let Some(split) =
            tweak_split_trim_parameter(self.knots[ki + order - 2], self.knots[ki + order - 1], t)
        {
            ki = span_index(order, count, &self.knots, split, -1, ki);
        }
        if !de_boor(
            cv_dim,
            order,
            &mut self.cvs[ki * STRIDE..(ki + order) * STRIDE],
            &self.knots[ki..ki + 2 * order - 2],
            -1,
            0.0,
            t,
        ) {
            return false;
        }
        count = ki + order;
        self.cvs.truncate(count * STRIDE);
        self.knots.truncate(count + order - 2);
        for i0 in (count - 1..count + order - 2).rev() {
            self.knots[i0] = t;
        }

        // Cut off the left end.
        let t = t0;
        let mut ki = span_index(order, count, &self.knots, t, 1, 0);
        if let Some(split) =
            tweak_split_trim_parameter(self.knots[ki + order - 2], self.knots[ki + order - 1], t)
        {
            ki = span_index(order, count, &self.knots, split, 1, ki);
        }
        if !de_boor(
            cv_dim,
            order,
            &mut self.cvs[ki * STRIDE..(ki + order) * STRIDE],
            &self.knots[ki..ki + 2 * order - 2],
            1,
            0.0,
            t,
        ) {
            return false;
        }
        if ki > 0 {
            self.cvs.drain(..ki * STRIDE);
            self.knots.drain(..ki);
        }
        for k in &mut self.knots[..order - 1] {
            *k = t;
        }
        self.clamp_ends();
        true
    }

    /// `ON_NurbsCurve::ClampEnd(2)` (`ON_ClampKnotVector`); its result is unused.
    fn clamp_ends(&mut self) {
        let (order, count, cv_dim) = (self.order, self.cv_count(), self.cv_dim());
        let k = self.knots[order - 2];
        if de_boor(
            cv_dim,
            order,
            &mut self.cvs[..order * STRIDE],
            &self.knots[..2 * order - 2],
            1,
            0.0,
            k,
        ) {
            for i in 0..order - 2 {
                self.knots[i] = k;
            }
        }
        let i0 = count - order;
        let k = self.knots[i0 + order - 1];
        if de_boor(
            cv_dim,
            order,
            &mut self.cvs[i0 * STRIDE..(i0 + order) * STRIDE],
            &self.knots[i0..i0 + 2 * order - 2],
            -1,
            0.0,
            k,
        ) {
            for i in (order..=2 * order - 3).rev() {
                self.knots[i0 + i] = k;
            }
        }
    }
}

fn dot(a: P3, b: P3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn sub(a: P3, b: P3) -> P3 {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

/// `ON_Intersect(ON_Line(a0, a1), ON_Line(b0, b1), &a, &b)`: Some(Some((a, b))) where it
/// returns true, Some(None) where it returns false, and None for a near-singular system,
/// where openNURBS checks the answer with closest points (not ported).
pub(crate) fn line_line(a0: P3, a1: P3, b0: P3, b1: P3) -> Option<Option<(f64, f64)>> {
    let a = sub(a1, a0);
    let b = sub(b1, b0);
    let c = sub(b0, a0);
    let ab = -dot(a, b);
    let mut m = [[dot(a, a), ab], [ab, dot(b, b)]];
    let i = if m[0][0] < m[1][1] {
        for row in &mut m {
            row.swap(0, 1);
        }
        1
    } else {
        0
    };
    let (pr_tolerance, zero_tol) = if m[1][1].abs() > 1.0 {
        (
            m[1][1].abs() * ON_SQRT_EPSILON,
            10.0 * m[1][1].abs() * ON_EPSILON,
        )
    } else {
        (ON_SQRT_EPSILON, 10.0 * ON_EPSILON)
    };
    let mut y = [dot(a, c), -dot(b, c)];

    // `ON_Matrix::RowReduce(zero_tol, Y, &pivot)` on the 2x2 matrix.
    let mut piv = 0.0;
    let mut rank = 0;
    for k in 0..2 {
        let mut ix = k;
        let mut x = m[ix][k].abs();
        for r in k + 1..2 {
            if m[r][k].abs() > x {
                ix = r;
                x = m[ix][k].abs();
            }
        }
        if x < piv || k == 0 {
            piv = x;
        }
        if x <= zero_tol {
            break;
        }
        rank += 1;
        if ix != k {
            m.swap(ix, k);
            y.swap(ix, k);
        }
        let x = 1.0 / m[k][k];
        m[k][k] = 1.0;
        // `ON_ArrayScale` and `ON_Array_aA_plus_B` over columns k+1..2: only column 1,
        // and only below row 0.
        if k == 0 {
            m[0][1] *= x;
        }
        y[k] *= x;
        if k == 0 {
            let x = -m[1][0];
            m[1][0] = 0.0;
            if x.abs() > zero_tol {
                m[1][1] += x * m[0][1];
                y[1] += x * y[0];
            }
        }
    }
    if rank != 2 {
        return Some(None);
    }
    if a0 == b0 {
        return Some(Some((0.0, 0.0)));
    }
    if a0 == b1 {
        return Some(Some((0.0, 1.0)));
    }
    if a1 == b0 {
        return Some(Some((1.0, 0.0)));
    }
    if a1 == b1 {
        return Some(Some((1.0, 1.0)));
    }
    // `ON_Matrix::BackSolve(0.0, 2, Y, X)`.
    let x1 = y[1];
    let x0 = y[0] - m[0][1] * x1;
    if piv.abs() <= pr_tolerance {
        return None;
    }
    let x = [x0, x1];
    Some(Some((x[i], x[1 - i])))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn curve(order: usize, knots: &[f64], pts: &[P3]) -> Curve {
        Curve {
            order,
            rational: false,
            knots: knots.to_vec(),
            cvs: pts.iter().flat_map(|p| [p[0], p[1], p[2], 1.0]).collect(),
        }
    }

    fn cubic() -> Curve {
        let pts: Vec<P3> = (0..7)
            .map(|i| [i as f64, ((i * i) % 5) as f64, (i % 3) as f64])
            .collect();
        curve(4, &[0.0, 0.0, 0.0, 1.0, 2.5, 3.0, 4.0, 4.0, 4.0], &pts)
    }

    #[test]
    fn span_index_takes_the_side_at_a_knot() {
        let k = [0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 3.0, 3.0];
        assert_eq!(span_index(4, 6, &k, 1.0, 1, 0), 1);
        assert_eq!(span_index(4, 6, &k, 1.0, -1, 0), 0);
        assert_eq!(span_index(4, 6, &k, 3.0, 1, 0), 2);
        assert_eq!(span_index(4, 6, &k, -1.0, 0, 0), 0);
        // A hint at or past t gives the same span.
        assert_eq!(span_index(4, 6, &k, 2.5, -1, 2), 2);
        assert_eq!(span_index(4, 6, &k, 1.5, -1, 2), 1);
    }

    #[test]
    fn a_parameter_next_to_a_knot_is_moved_onto_it() {
        assert_eq!(tweak_split_trim_parameter(1.0, 2.0, 1.0 + 1e-12), Some(1.0));
        assert_eq!(tweak_split_trim_parameter(1.0, 2.0, 2.0 - 1e-12), Some(2.0));
        assert_eq!(tweak_split_trim_parameter(1.0, 2.0, 1.5), None);
        assert_eq!(tweak_split_trim_parameter(1.0, 2.0, 1.0), None);
    }

    #[test]
    fn a_trimmed_curve_has_the_source_shape_on_the_new_domain() {
        let src = cubic();
        let mut piece = src.clone();
        assert!(piece.trim(0.7, 3.2));
        assert_eq!(piece.domain(), (0.7, 3.2));
        assert_eq!(piece.knots.len(), piece.cv_count() + 2);
        let (a, b) = (src.view().unwrap(), piece.view().unwrap());
        for i in 0..=50 {
            let t = 0.7 + 2.5 * i as f64 / 50.0;
            let (p, q) = (a.point(t), b.point(t));
            assert!(
                (0..3).all(|j| (p[j] - q[j]).abs() < 1e-12),
                "{t}: {p:?} {q:?}"
            );
        }
        // The ends are clamped: full multiplicity at each end.
        assert!(piece.knots[..3].iter().all(|&k| k == 0.7));
        assert!(piece.knots[piece.knots.len() - 3..]
            .iter()
            .all(|&k| k == 3.2));
    }

    #[test]
    fn trimming_to_the_domain_or_backwards_is_as_opennurbs_does() {
        let mut same = cubic();
        assert!(same.trim(0.0, 4.0));
        assert_eq!(same, cubic());
        assert!(!cubic().trim(2.0, 1.0));
        assert!(!cubic().trim(f64::NAN, 1.0));
    }

    #[test]
    fn a_trim_at_knots_keeps_the_control_points_between() {
        let src = cubic();
        let mut piece = src.clone();
        assert!(piece.trim(1.0, 3.0));
        assert_eq!(piece.knots, vec![1.0, 1.0, 1.0, 2.5, 3.0, 3.0, 3.0]);
        assert_eq!(piece.cv_count(), 5);
    }

    #[test]
    fn rational_curves_trim_in_homogeneous_form() {
        let mut src = cubic();
        src.rational = true;
        for (i, cv) in src.cvs.chunks_exact_mut(4).enumerate() {
            let w = 1.0 + 0.1 * i as f64;
            for v in &mut cv[..3] {
                *v *= w;
            }
            cv[3] = w;
        }
        let mut piece = src.clone();
        assert!(piece.trim(0.3, 2.9));
        let (a, b) = (src.view().unwrap(), piece.view().unwrap());
        for i in 0..=20 {
            let t = 0.3 + 2.6 * i as f64 / 20.0;
            let (p, q) = (a.point(t), b.point(t));
            assert!((0..3).all(|j| (p[j] - q[j]).abs() < 1e-12));
        }
    }

    #[test]
    fn lines_meet_where_they_cross() {
        let hit = line_line([0.0; 3], [2.0, 0.0, 0.0], [1.0, -1.0, 0.0], [1.0, 3.0, 0.0]);
        assert_eq!(hit, Some(Some((0.5, 0.25))));
        // Parallel lines: rank 1.
        assert_eq!(
            line_line([0.0; 3], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0]),
            Some(None)
        );
        // Shared end points answer exactly.
        assert_eq!(
            line_line([0.0; 3], [1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0]),
            Some(Some((1.0, 0.0)))
        );
        // Nearly parallel long lines are left to openNURBS's closest-point check.
        assert_eq!(
            line_line(
                [0.0; 3],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [1.0, 1.0 + 1e-5, 0.0]
            ),
            None
        );
    }
}
