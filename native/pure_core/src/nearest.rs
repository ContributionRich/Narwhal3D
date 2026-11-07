//! Nearest point on a NURBS curve, for the interactive Near object snap.
//!
//! Mirrors `narwhal3d/nearest.py` `_search` on the curve's NURBS form: the same
//! knot breakpoints, `SAMPLES_PER_SPAN` samples per span, local-minimum seeds with
//! infinite end sentinels, Newton on `g(t) = (C(t) - P) . C'(t)` clamped to the
//! domain, and the same candidate order with strict improvement, so an exact tie
//! keeps the earlier candidate. The curve is passed in openNURBS form: `order`,
//! `cv_count + order - 2` knots (no superfluous end knots) and homogeneous control
//! points `(x*w, y*w, z*w, w)`. The answer is a snap location only; it never
//! creates or changes document geometry.

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const SAMPLES_PER_SPAN: usize = 6;
const MAX_NEWTON: usize = 60;
const PARAM_EPS: f64 = 1e-12;
const MAX_ORDER: usize = 32;
const MAX_CVS: usize = 1 << 20;

/// A NURBS curve in the standard (n + p + 1 knot) form.
pub struct Nurbs<'a> {
    degree: usize,
    knots: Vec<f64>,
    cvs: &'a [f64],
    count: usize,
}

impl<'a> Nurbs<'a> {
    /// From openNURBS data; `None` when the arrays do not describe a curve.
    pub fn new(order: usize, on_knots: &[f64], cvs: &'a [f64]) -> Option<Self> {
        if !(2..=MAX_ORDER).contains(&order) || !cvs.len().is_multiple_of(4) {
            return None;
        }
        let count = cvs.len() / 4;
        if count < order || on_knots.len() != count + order - 2 {
            return None;
        }
        if on_knots.windows(2).any(|w| w[1] < w[0]) || cvs.chunks_exact(4).any(|cv| cv[3] <= 0.0) {
            return None;
        }
        // The superfluous end knots never affect the curve on its domain.
        let mut knots = Vec::with_capacity(on_knots.len() + 2);
        knots.push(on_knots[0]);
        knots.extend_from_slice(on_knots);
        knots.push(on_knots[on_knots.len() - 1]);
        Some(Self {
            degree: order - 1,
            knots,
            cvs,
            count,
        })
    }

    pub fn domain(&self) -> (f64, f64) {
        (self.knots[self.degree], self.knots[self.count])
    }

    fn span(&self, t: f64) -> usize {
        let p = self.degree;
        let mut k = p;
        while k < self.count - 1 && self.knots[k + 1] <= t {
            k += 1;
        }
        k
    }

    /// Basis functions and their first two derivatives at `t` on span `k`
    /// (Piegl & Tiller, algorithm A2.3).
    fn basis_derivatives(&self, k: usize, t: f64) -> [[f64; MAX_ORDER]; 3] {
        let p = self.degree;
        let u = &self.knots;
        let mut ndu = [[0.0_f64; MAX_ORDER]; MAX_ORDER];
        let mut left = [0.0_f64; MAX_ORDER];
        let mut right = [0.0_f64; MAX_ORDER];
        ndu[0][0] = 1.0;
        for j in 1..=p {
            left[j] = t - u[k + 1 - j];
            right[j] = u[k + j] - t;
            let mut saved = 0.0;
            for r in 0..j {
                ndu[j][r] = right[r + 1] + left[j - r];
                let temp = if ndu[j][r] == 0.0 {
                    0.0
                } else {
                    ndu[r][j - 1] / ndu[j][r]
                };
                ndu[r][j] = saved + right[r + 1] * temp;
                saved = left[j - r] * temp;
            }
            ndu[j][j] = saved;
        }
        let mut ders = [[0.0_f64; MAX_ORDER]; 3];
        for j in 0..=p {
            ders[0][j] = ndu[j][p];
        }
        let top = p.min(2);
        let mut a = [[0.0_f64; MAX_ORDER]; 2];
        for r in 0..=p {
            let (mut s1, mut s2) = (0usize, 1usize);
            a[0][0] = 1.0;
            for kk in 1..=top {
                let mut d = 0.0;
                let rk = r as isize - kk as isize;
                let pk = p - kk;
                if rk >= 0 {
                    let rk = rk as usize;
                    let den = ndu[pk + 1][rk];
                    a[s2][0] = if den == 0.0 { 0.0 } else { a[s1][0] / den };
                    d = a[s2][0] * ndu[rk][pk];
                }
                let j1 = if rk >= -1 { 1 } else { (-rk) as usize };
                let j2 = if r as isize - 1 <= pk as isize {
                    kk - 1
                } else {
                    p - r
                };
                for j in j1..=j2 {
                    let idx = (rk + j as isize) as usize;
                    let den = ndu[pk + 1][idx];
                    a[s2][j] = if den == 0.0 {
                        0.0
                    } else {
                        (a[s1][j] - a[s1][j - 1]) / den
                    };
                    d += a[s2][j] * ndu[idx][pk];
                }
                if r <= pk {
                    let den = ndu[pk + 1][r];
                    a[s2][kk] = if den == 0.0 {
                        0.0
                    } else {
                        -a[s1][kk - 1] / den
                    };
                    d += a[s2][kk] * ndu[r][pk];
                }
                ders[kk][r] = d;
                std::mem::swap(&mut s1, &mut s2);
            }
        }
        let mut factor = p as f64;
        for row in ders.iter_mut().take(top + 1).skip(1) {
            for value in row.iter_mut().take(p + 1) {
                *value *= factor;
            }
            factor *= (p - 1) as f64;
        }
        ders
    }

    /// `[C, C', C'']` of the rational curve at `t`.
    pub fn derivatives(&self, t: f64) -> [[f64; 3]; 3] {
        let p = self.degree;
        let k = self.span(t);
        let n = self.basis_derivatives(k, t);
        let mut h = [[0.0_f64; 4]; 3];
        let cvs = self.cvs[(k - p) * 4..(k + 1) * 4].chunks_exact(4);
        for (order, row) in h.iter_mut().enumerate() {
            for (basis, cv) in n[order].iter().zip(cvs.clone()) {
                for (value, c) in row.iter_mut().zip(cv) {
                    *value += basis * c;
                }
            }
        }
        let w = h[0][3];
        let c0: [f64; 3] = std::array::from_fn(|i| h[0][i] / w);
        let c1: [f64; 3] = std::array::from_fn(|i| (h[1][i] - h[1][3] * c0[i]) / w);
        let c2: [f64; 3] =
            std::array::from_fn(|i| (h[2][i] - 2.0 * h[1][3] * c1[i] - h[2][3] * c0[i]) / w);
        [c0, c1, c2]
    }

    pub fn point(&self, t: f64) -> [f64; 3] {
        let p = self.degree;
        let k = self.span(t);
        let n = self.basis_derivatives(k, t);
        let mut h = [0.0_f64; 4];
        for (basis, cv) in n[0]
            .iter()
            .zip(self.cvs[(k - p) * 4..(k + 1) * 4].chunks_exact(4))
        {
            for (value, c) in h.iter_mut().zip(cv) {
                *value += basis * c;
            }
        }
        [h[0] / h[3], h[1] / h[3], h[2] / h[3]]
    }

    /// `nearest._breakpoints` with the curve's knots.
    fn breakpoints(&self, t0: f64, t1: f64) -> Vec<f64> {
        let span = t1 - t0;
        let grain = span.abs() * 1e-12;
        let mut vals = vec![t0, t1];
        let on_knots = &self.knots[1..self.knots.len() - 1];
        vals.extend(
            on_knots
                .iter()
                .copied()
                .filter(|&k| t0 + grain < k && k < t1 - grain),
        );
        vals.sort_by(f64::total_cmp);
        let mut out = vec![vals[0]];
        for &v in &vals[1..] {
            if v - out[out.len() - 1] > grain {
                out.push(v);
            }
        }
        if out.len() < 2 {
            out = vec![t0, t1];
        }
        out
    }
}

fn dist(a: [f64; 3], b: [f64; 3]) -> f64 {
    ((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]) + (a[2] - b[2]) * (a[2] - b[2]))
        .sqrt()
}

fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

/// `nearest._local_minimum_seeds`.
fn local_minimum_seeds(dists: &[f64]) -> Vec<usize> {
    let n = dists.len();
    if n <= 1 {
        return (0..n).collect();
    }
    let mut seeds: Vec<usize> = (0..n)
        .filter(|&i| {
            let lo = if i > 0 { dists[i - 1] } else { f64::INFINITY };
            let hi = if i < n - 1 {
                dists[i + 1]
            } else {
                f64::INFINITY
            };
            dists[i] <= lo && dists[i] <= hi
        })
        .collect();
    if seeds.is_empty() {
        let mut best = 0;
        for i in 1..n {
            if dists[i] < dists[best] {
                best = i;
            }
        }
        seeds.push(best);
    }
    seeds
}

/// `nearest._newton`.
fn newton(curve: &Nurbs, target: [f64; 3], mut t: f64, lo: f64, hi: f64) -> f64 {
    let span = hi - lo;
    let stop = PARAM_EPS * if span != 0.0 { span.abs() } else { 1.0 };
    for _ in 0..MAX_NEWTON {
        let [c, c1, c2] = curve.derivatives(t);
        if !c.iter().chain(&c1).chain(&c2).all(|v| v.is_finite()) {
            return t;
        }
        let w = [c[0] - target[0], c[1] - target[1], c[2] - target[2]];
        let g = dot(w, c1);
        let gp = dot(c1, c1) + dot(w, c2);
        if gp.abs() < 1e-300 {
            return t;
        }
        let nt = (t - g / gp).clamp(lo, hi);
        if (nt - t).abs() <= stop {
            return nt;
        }
        t = nt;
    }
    t
}

/// The result of `nearest._search`: parameter, point, distance and the open-end flag.
pub struct Closest {
    pub t: f64,
    pub point: [f64; 3],
    pub distance: f64,
    pub at_end: bool,
}

/// `nearest._search` on a NURBS curve.
pub fn search(curve: &Nurbs, target: [f64; 3], closed: bool) -> Closest {
    let (t0, t1) = curve.domain();
    let span = t1 - t0;
    let peps = PARAM_EPS * if span != 0.0 { span.abs() } else { 1.0 };
    let breaks = curve.breakpoints(t0, t1);
    let mut params = Vec::with_capacity((breaks.len() - 1) * SAMPLES_PER_SPAN + 1);
    for pair in breaks.windows(2) {
        let (a, b) = (pair[0], pair[1]);
        for i in 0..SAMPLES_PER_SPAN {
            params.push(a + (b - a) * i as f64 / SAMPLES_PER_SPAN as f64);
        }
    }
    params.push(breaks[breaks.len() - 1]);
    let dists: Vec<f64> = params
        .iter()
        .map(|&t| dist(curve.point(t), target))
        .collect();
    let (mut best_t, mut best_d) = (f64::NAN, f64::INFINITY);
    for (&t, &d) in params.iter().zip(&dists) {
        if d < best_d {
            best_t = t;
            best_d = d;
        }
    }
    for i in local_minimum_seeds(&dists) {
        let nt = newton(curve, target, params[i], t0, t1);
        let nd = dist(curve.point(nt), target);
        if nd < best_d {
            best_t = nt;
            best_d = nd;
        }
    }
    let point = curve.point(best_t);
    Closest {
        t: best_t,
        point,
        distance: dist(point, target),
        at_end: !closed && (best_t <= t0 + peps || best_t >= t1 - peps),
    }
}

/// Nearest point of a NURBS curve to `target`.
///
/// Writes the parameter, the point (xyz), the distance and the open-end flag.
///
/// # Safety
/// `knots` is readable for `knot_count` values, `cvs` for `cv_count * 4`
/// homogeneous values and `target` for 3; `out_values` is writable for 5 values
/// (t, x, y, z, distance) and `out_at_end` for one byte.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_nearest_on_nurbs(
    order: usize,
    knots: *const f64,
    knot_count: usize,
    cvs: *const f64,
    cv_count: usize,
    closed: u8,
    target: *const f64,
    out_values: *mut f64,
    out_at_end: *mut u8,
) -> i32 {
    if out_values.is_null() || out_at_end.is_null() {
        return ERR_NULL;
    }
    if cv_count > MAX_CVS || closed > 1 {
        return ERR_INPUT;
    }
    let (Some(knot_values), Some(cv_values), Some(point)) = (
        crate::checked_slice(knots, knot_count),
        crate::checked_slice(cvs, cv_count * 4),
        crate::checked_slice(target, 3),
    ) else {
        return ERR_NULL;
    };
    if !knot_values
        .iter()
        .chain(cv_values)
        .chain(point)
        .all(|v| v.is_finite())
    {
        return ERR_INPUT;
    }
    let Some(curve) = Nurbs::new(order, knot_values, cv_values) else {
        return ERR_INPUT;
    };
    let (t0, t1) = curve.domain();
    if t1 <= t0 {
        return ERR_INPUT;
    }
    let result = search(&curve, [point[0], point[1], point[2]], closed == 1);
    let Some(out) = crate::checked_slice_mut(out_values, 5) else {
        return ERR_NULL;
    };
    out.copy_from_slice(&[
        result.t,
        result.point[0],
        result.point[1],
        result.point[2],
        result.distance,
    ]);
    *out_at_end = u8::from(result.at_end);
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cubic() -> (Vec<f64>, Vec<f64>) {
        // A clamped cubic with one interior knot, non-rational.
        let knots = vec![0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 2.0];
        let pts = [
            [0.0, 0.0, 0.0],
            [3.0, 5.0, 0.0],
            [6.0, -2.0, 1.0],
            [10.0, 3.0, 0.0],
            [12.0, 0.0, 0.0],
        ];
        let cvs = pts.iter().flat_map(|p| [p[0], p[1], p[2], 1.0]).collect();
        (knots, cvs)
    }

    fn circle() -> (Vec<f64>, Vec<f64>) {
        let h = std::f64::consts::FRAC_1_SQRT_2;
        let q = std::f64::consts::FRAC_PI_2;
        let knots = vec![
            0.0,
            0.0,
            q,
            q,
            2.0 * q,
            2.0 * q,
            3.0 * q,
            3.0 * q,
            4.0 * q,
            4.0 * q,
        ];
        let euclid = [
            ([5.0, 0.0], 1.0),
            ([5.0, 5.0], h),
            ([0.0, 5.0], 1.0),
            ([-5.0, 5.0], h),
            ([-5.0, 0.0], 1.0),
            ([-5.0, -5.0], h),
            ([0.0, -5.0], 1.0),
            ([5.0, -5.0], h),
            ([5.0, 0.0], 1.0),
        ];
        let cvs = euclid
            .iter()
            .flat_map(|(p, w)| [p[0] * w, p[1] * w, 0.0, *w])
            .collect();
        (knots, cvs)
    }

    #[test]
    fn rational_circle_points_lie_on_the_circle() {
        let (knots, cvs) = circle();
        let curve = Nurbs::new(3, &knots, &cvs).unwrap();
        for i in 0..=40 {
            let t = 2.0 * std::f64::consts::PI * i as f64 / 40.0;
            let p = curve.point(t);
            assert!((p[0].hypot(p[1]) - 5.0).abs() < 1e-12, "{t}: {p:?}");
        }
    }

    #[test]
    fn derivatives_match_central_differences() {
        let (knots, cvs) = circle();
        let curve = Nurbs::new(3, &knots, &cvs).unwrap();
        let h = 1e-5;
        for &t in &[0.3, 1.0, 2.5, 4.0, 5.9] {
            let [_, d1, d2] = curve.derivatives(t);
            let (a, b) = (curve.point(t - h), curve.point(t + h));
            let c = curve.point(t);
            for i in 0..3 {
                assert!((d1[i] - (b[i] - a[i]) / (2.0 * h)).abs() < 1e-6);
                assert!((d2[i] - (b[i] - 2.0 * c[i] + a[i]) / (h * h)).abs() < 1e-3);
            }
        }
    }

    #[test]
    fn nearest_on_the_circle_is_the_radial_projection() {
        let (knots, cvs) = circle();
        let curve = Nurbs::new(3, &knots, &cvs).unwrap();
        let r = search(&curve, [8.0, 6.0, 2.0], true);
        assert!((r.point[0] - 4.0).abs() < 1e-12 && (r.point[1] - 3.0).abs() < 1e-12);
        assert!(!r.at_end);
    }

    #[test]
    fn off_the_end_of_an_open_curve_returns_the_end() {
        let (knots, cvs) = cubic();
        let curve = Nurbs::new(4, &knots, &cvs).unwrap();
        let r = search(&curve, [20.0, 0.0, 0.0], false);
        assert_eq!(r.t, 2.0);
        assert_eq!(r.point, [12.0, 0.0, 0.0]);
        assert!(r.at_end);
    }

    #[test]
    fn exact_ties_keep_the_earlier_candidate() {
        // Symmetric about x = 5: both ends are exactly 125^0.5 from (5, -10).
        let knots = vec![0.0, 0.0, 0.0, 1.0, 1.0, 1.0];
        let pts = [[0.0, 0.0], [2.0, 6.0], [8.0, 6.0], [10.0, 0.0]];
        let cvs: Vec<f64> = pts.iter().flat_map(|p| [p[0], p[1], 0.0, 1.0]).collect();
        let curve = Nurbs::new(4, &knots, &cvs).unwrap();
        let r = search(&curve, [5.0, -10.0, 0.0], false);
        assert_eq!(r.t, 0.0);
    }

    #[test]
    fn export_rejects_bad_curves() {
        let (knots, cvs) = cubic();
        let mut out = [0.0; 5];
        let mut at_end = 9;
        let target = [1.0, 1.0, 0.0];
        let ok = unsafe {
            narwhal_pure_core_nearest_on_nurbs(
                4,
                knots.as_ptr(),
                7,
                cvs.as_ptr(),
                5,
                0,
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut at_end,
            )
        };
        assert_eq!(ok, 0);
        let short = unsafe {
            narwhal_pure_core_nearest_on_nurbs(
                4,
                knots.as_ptr(),
                6,
                cvs.as_ptr(),
                5,
                0,
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut at_end,
            )
        };
        assert_eq!(short, ERR_INPUT);
        let mut zero_weight = cvs.clone();
        zero_weight[3] = 0.0;
        let bad = unsafe {
            narwhal_pure_core_nearest_on_nurbs(
                4,
                knots.as_ptr(),
                7,
                zero_weight.as_ptr(),
                5,
                0,
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut at_end,
            )
        };
        assert_eq!(bad, ERR_INPUT);
    }
}
