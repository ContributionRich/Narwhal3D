//! Nearest point on a NURBS curve, for the interactive Near object snap.
//!
//! Mirrors `narwhal3d/nearest.py` `_search` on the curve's NURBS form: the same
//! knot breakpoints, `SAMPLES_PER_SPAN` samples per span, local-minimum seeds with
//! infinite end sentinels, Newton on `g(t) = (C(t) - P) . C'(t)` clamped to the
//! domain, and the same candidate order with strict improvement, so an exact tie
//! keeps the earlier candidate. The curve is passed in openNURBS form: `order`,
//! `cv_count + order - 2` knots (no superfluous end knots), homogeneous control
//! points `(x*w, y*w, z*w, w)` and the rational flag. The curve is evaluated with
//! openNURBS's own algorithms in its operation order, so every step, and the
//! answer, is bit-identical to the Python search on the same `NurbsCurve`. The
//! answer is a snap location only; it never creates or changes document geometry.
//!
//! `narwhal_pure_core_sample_nurbs` uses the same evaluator for the uniform samples
//! of `bridge.sample_curve`, which draw previews and wireframes; they are display
//! points only.

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const SAMPLES_PER_SPAN: usize = 6;
const MAX_NEWTON: usize = 60;
const PARAM_EPS: f64 = 1e-12;
const MAX_ORDER: usize = 32;
const MAX_CVS: usize = 1 << 20;

/// openNURBS `ON_SQRT_EPSILON`, used by the end-knot fix in `ON_EvaluateNurbsBasis`.
const ON_SQRT_EPSILON: f64 = 1.490116119385e-8;
/// Doubles per packed control point: homogeneous `(x*w, y*w, z*w, w)`.
const CV_STRIDE: usize = 4;

/// A NURBS curve in openNURBS form (`cv_count + order - 2` knots).
///
/// Evaluation is a line-for-line port of openNURBS (`ON_NurbsCurve::Evaluate` and
/// the routines it calls), with the same operations in the same order, so a point
/// or derivative is bit-identical to rhino3dm's `PointAt` / `DerivativeAt` on the
/// same curve. Rust never contracts `a * b + c` into a fused multiply-add, and
/// neither does a baseline x86-64 build of openNURBS.
pub struct Nurbs<'a> {
    order: usize,
    knots: &'a [f64],
    cvs: &'a [f64],
    count: usize,
    rational: bool,
}

impl<'a> Nurbs<'a> {
    /// From openNURBS data; `None` when the arrays do not describe a curve.
    pub fn new(order: usize, knots: &'a [f64], cvs: &'a [f64], rational: bool) -> Option<Self> {
        if !(2..=MAX_ORDER).contains(&order) || !cvs.len().is_multiple_of(CV_STRIDE) {
            return None;
        }
        let count = cvs.len() / CV_STRIDE;
        if count < order || knots.len() != count + order - 2 {
            return None;
        }
        if knots.windows(2).any(|w| w[1] < w[0])
            || cvs.chunks_exact(CV_STRIDE).any(|cv| cv[3] <= 0.0)
        {
            return None;
        }
        Some(Self {
            order,
            knots,
            cvs,
            count,
            rational,
        })
    }

    pub fn domain(&self) -> (f64, f64) {
        (self.knots[self.order - 2], self.knots[self.count - 1])
    }

    /// `ON_NurbsSpanIndex` with no side and no hint.
    fn span_index(&self, t: f64) -> usize {
        let len = self.count - self.order + 2;
        let j = search_monotone(&self.knots[self.order - 2..self.order - 2 + len], t);
        if j < 0 {
            0
        } else if j as usize >= len - 1 {
            len - 2
        } else {
            j as usize
        }
    }

    /// `ON_NurbsCurve::Evaluate`: rows `C, C', C''` up to `der_count` (<= 2).
    fn evaluate(&self, t: f64, der_count: usize) -> [[f64; 3]; 3] {
        let order = self.order;
        let span = self.span_index(t);
        let knot = &self.knots[span..span + 2 * order - 2];
        let cv = &self.cvs[span * CV_STRIDE..(span + order) * CV_STRIDE];
        let cvdim = if self.rational { 4 } else { 3 };
        let mut v = [0.0_f64; 3 * CV_STRIDE];
        let ok = if knot[0] == knot[order - 2] && knot[order - 1] == knot[2 * order - 3] {
            evaluate_bezier(
                order,
                cv,
                cvdim,
                self.rational,
                knot[order - 2],
                knot[order - 1],
                der_count,
                t,
                &mut v,
            )
        } else {
            evaluate_span(order, knot, cv, cvdim, der_count, t, &mut v);
            !self.rational || quotient_rule(&mut v, der_count)
        };
        if !ok {
            return [[f64::NAN; 3]; 3];
        }
        std::array::from_fn(|i| [v[i * 4], v[i * 4 + 1], v[i * 4 + 2]])
    }

    /// `[C, C', C'']` at `t`, as `DerivativeAt(t, 2)`.
    pub fn derivatives(&self, t: f64) -> [[f64; 3]; 3] {
        self.evaluate(t, 2)
    }

    /// `C(t)`, as `PointAt(t)`.
    pub fn point(&self, t: f64) -> [f64; 3] {
        self.evaluate(t, 0)[0]
    }

    /// `nearest._breakpoints` with the curve's knots.
    fn breakpoints(&self, t0: f64, t1: f64) -> Vec<f64> {
        let span = t1 - t0;
        let grain = span.abs() * 1e-12;
        let mut vals = vec![t0, t1];
        vals.extend(
            self.knots
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

/// `ON_SearchMonotoneArray`: -1 below, `i` with `a[i] <= t < a[i+1]`, `len-1` at the
/// last value, `len` above.
pub(crate) fn search_monotone(a: &[f64], t: f64) -> isize {
    let length = a.len() - 1;
    if t < a[0] {
        return -1;
    }
    if t >= a[length] {
        return if t > a[length] {
            length as isize + 1
        } else {
            length as isize
        };
    }
    if t < a[1] {
        return 0;
    }
    if t >= a[length - 1] {
        return length as isize - 1;
    }
    let (mut i0, mut i1) = (0, length);
    while a[i0] == a[i0 + 1] {
        i0 += 1;
    }
    while a[i1] == a[i1 - 1] {
        i1 -= 1;
    }
    while i0 + 1 < i1 {
        let i = (i0 + i1) >> 1;
        if t < a[i] {
            i1 = i;
            while a[i1] == a[i1 - 1] {
                i1 -= 1;
            }
        } else {
            i0 = i;
            while a[i0] == a[i0 + 1] {
                i0 += 1;
            }
        }
    }
    i0 as isize
}

/// `ON_EvaluateNurbsBasis`: the degree-d basis lands in `n[0..order]`, lower degrees
/// in the triangle above it (row `d - j`, from column `d - j`), which
/// `basis_derivatives` reads.
fn basis(order: usize, knot: &[f64], t: f64, n: &mut [f64]) {
    let d = order - 1;
    if knot[d - 1] == knot[d] {
        n[..order * order].fill(0.0);
        return;
    }
    let mut t_k = [0.0_f64; MAX_ORDER];
    let mut k_t = [0.0_f64; MAX_ORDER];
    let mut p = order * order - 1;
    n[p] = 1.0;
    for j in 0..d {
        let p0 = p;
        p -= order + 1;
        t_k[j] = t - knot[d - 1 - j];
        k_t[j] = knot[d + j] - t;
        let mut x = 0.0;
        for r in 0..=j {
            let a0 = t_k[j - r];
            let a1 = k_t[r];
            let y = n[p0 + r] / (a0 + a1);
            n[p + r] = x + a1 * y;
            x = a0 * y;
        }
        n[p + j + 1] = x;
    }
    // At an end knot the sum can miss 1 by a bit; openNURBS snaps it.
    let x = 1.0 - ON_SQRT_EPSILON;
    if n[0] >= x {
        if n[0] != 1.0 && n[0] <= 1.0 + ON_SQRT_EPSILON && n[1..=d].iter().all(|&v| v == 0.0) {
            n[0] = 1.0;
        }
    } else if n[d] >= x
        && n[d] != 1.0
        && n[d] <= 1.0 + ON_SQRT_EPSILON
        && n[..d].iter().all(|&v| v == 0.0)
    {
        n[d] = 1.0;
    }
}

/// `ON_EvaluateNurbsBasisDerivatives`: derivative `k` of the basis into row `k`.
fn basis_derivatives(order: usize, knot: &[f64], der_count: usize, n: &mut [f64]) {
    let d = order - 1;
    // dk[k][i] = 1 / (knot[d + i] - knot[k - 1 + i])
    let mut dk = [[0.0_f64; MAX_ORDER]; 3];
    for k in 0..der_count {
        let j = d - k;
        for i in 0..j {
            dk[k + 1][i] = 1.0 / (knot[k + i + j] - knot[k + i]);
        }
    }
    let mut a = [[0.0_f64; MAX_ORDER]; 2];
    let (mut a0, mut a1) = (0, 1);
    let mut base = order;
    for i in 0..order {
        a[a0][0] = 1.0;
        for k in 1..=der_count {
            let mut dn = 0.0;
            let mut j = k as isize - i as isize;
            if j <= 0 {
                a[a1][0] = a[a0][0] * dk[k][i - k];
                dn = a[a1][0] * n[base + i];
                j = 1;
            }
            let jmax = d as isize - i as isize;
            let last = if jmax < k as isize {
                jmax + 1
            } else {
                k as isize
            };
            while j < last {
                let ju = j as usize;
                a[a1][ju] = (a[a0][ju] - a[a0][ju - 1]) * dk[k][i + ju - k];
                dn += a[a1][ju] * n[base + i + ju];
                j += 1;
            }
            if jmax >= k as isize {
                a[a1][k] = -a[a0][k - 1] * dk[k][i];
                dn += a[a1][k] * n[base + i + k];
            }
            n[base + i] = dn;
            base += order;
            std::mem::swap(&mut a0, &mut a1);
        }
        base -= der_count * order;
    }
    let mut dn = d as f64;
    let mut c = dn;
    for _ in 0..der_count {
        for value in &mut n[base..base + order] {
            *value *= c;
        }
        base += order;
        dn -= 1.0;
        c *= dn;
    }
}

/// Run `f` on a zeroed basis table of exactly `order * order` values.
///
/// Zeroing the table for the largest order (`MAX_ORDER`², 8 KiB) on every evaluation
/// cost about as much as the evaluation of a cubic; a small order gets a small table.
/// The values are the same either way: `basis` writes every entry it and
/// `basis_derivatives` read, and the slice bounds would catch a read past them.
fn with_basis_scratch<R>(order: usize, f: impl FnOnce(&mut [f64]) -> R) -> R {
    let len = order * order;
    if len <= 16 {
        f(&mut [0.0_f64; 16][..len])
    } else if len <= 64 {
        f(&mut [0.0_f64; 64][..len])
    } else {
        f(&mut [0.0_f64; MAX_ORDER * MAX_ORDER][..len])
    }
}

/// `ON_EvaluateNurbsNonRationalSpan` on `cvdim` coordinates (4 for the homogeneous
/// numerator of a rational span); rows of `v` are `CV_STRIDE` apart.
fn evaluate_span(
    order: usize,
    knot: &[f64],
    cv: &[f64],
    cvdim: usize,
    der_count: usize,
    t: f64,
    v: &mut [f64],
) {
    let der = der_count.min(order - 1);
    with_basis_scratch(order, |n| {
        basis(order, knot, t, n);
        if der > 0 {
            basis_derivatives(order, knot, der, n);
        }
        for i in 0..=der {
            for j in 0..order {
                let a = n[i * order + j];
                for k in 0..cvdim {
                    v[i * CV_STRIDE + k] += a * cv[j * CV_STRIDE + k];
                }
            }
        }
    });
    if order == 2 {
        // openNURBS: keep a constant coordinate exact on a linear span.
        for i in 0..cvdim {
            if cv[i] == cv[CV_STRIDE + i] {
                v[i] = cv[i];
            }
        }
    }
}

/// `ON_EvaluateQuotientRule` for three dimensions; rows of `v` are `CV_STRIDE` apart.
fn quotient_rule(v: &mut [f64], der_count: usize) -> bool {
    let w = v[3];
    if w == 0.0 {
        return false;
    }
    let wt = 1.0 / w;
    for x in &mut v[..(der_count + 1) * CV_STRIDE] {
        *x *= wt;
    }
    if der_count > 0 {
        let wt = -v[CV_STRIDE + 3];
        for j in 0..3 {
            v[CV_STRIDE + j] += wt * v[j];
        }
        if der_count > 1 {
            let wt = wt * 2.0;
            let w2 = -v[2 * CV_STRIDE + 3];
            for j in 0..3 {
                v[2 * CV_STRIDE + j] += w2 * v[j] + wt * v[CV_STRIDE + j];
            }
        }
    }
    true
}

/// `ON_EvaluateBezier` (de Casteljau, then Lee for the derivatives) on a Bezier span
/// with domain `[t0, t1]`. Writes rows `CV_STRIDE` apart into `v`.
#[allow(clippy::too_many_arguments)]
fn evaluate_bezier(
    order: usize,
    cv: &[f64],
    cvdim: usize,
    rational: bool,
    t0: f64,
    t1: f64,
    der_count: usize,
    t: f64,
    v: &mut [f64],
) -> bool {
    if t0 == t1 {
        return false;
    }
    let degree = order - 1;
    let mut der = der_count;
    if der > degree && !rational {
        der = degree;
    }
    // Scratch rows, `cvdim` apart; a rational curve gets zero rows past the degree.
    let mut buf = [0.0_f64; (MAX_ORDER + 2) * CV_STRIDE];
    let (delta_t, alpha0, alpha1);
    if t0 == t || (t <= 0.5 * (t0 + t1) && t != t1) {
        for i in 0..order {
            buf[i * cvdim..(i + 1) * cvdim]
                .copy_from_slice(&cv[i * CV_STRIDE..i * CV_STRIDE + cvdim]);
        }
        delta_t = 1.0 / (t1 - t);
        let a = 1.0 / (t1 - t0);
        alpha0 = (t1 - t) * a;
        alpha1 = a * (t - t0);
    } else {
        for i in 0..order {
            let src = (order - 1 - i) * CV_STRIDE;
            buf[i * cvdim..(i + 1) * cvdim].copy_from_slice(&cv[src..src + cvdim]);
        }
        delta_t = 1.0 / (t0 - t);
        let a = 1.0 / (t1 - t0);
        alpha1 = (t1 - t) * a;
        alpha0 = a * (t - t0);
    }
    if alpha1 != 0.0 {
        for j in (1..order).rev() {
            for m in 0..j * cvdim {
                buf[m] = buf[m] * alpha0 + buf[m + cvdim] * alpha1;
            }
        }
    }
    if rational && buf[3] == 0.0 {
        // openNURBS removes the singularity; positive weights never reach it.
        return false;
    }
    if der > 0 {
        let mut a0 = order as f64;
        let rows = if der >= order { order } else { der + 1 };
        let end = cvdim * rows;
        for j in (1..rows).rev() {
            a0 -= 1.0;
            let a1 = a0 * delta_t;
            for m in (end - j * cvdim..end).rev() {
                buf[m] = a1 * (buf[m] - buf[m - cvdim]);
            }
        }
    }
    if order == 2 {
        for i in 0..cvdim {
            if cv[i] == cv[CV_STRIDE + i] {
                buf[i] = cv[i];
            }
        }
    }
    if rational {
        // The quotient rule over rows `cvdim` (= CV_STRIDE) apart.
        quotient_rule(&mut buf, der);
    }
    for i in 0..=der {
        v[i * CV_STRIDE..i * CV_STRIDE + 3].copy_from_slice(&buf[i * cvdim..i * cvdim + 3]);
    }
    true
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

/// [`narwhal_pure_core_nearest_on_nurbs`] for each of `target_count` targets.
///
/// One call answers every closest-point query of a prepared curve (a Rebuild fit
/// queries every sample). Each target's five values (t, x, y, z, distance) and end
/// flag are exactly what the single-target export writes for it; a non-finite target
/// gets NaN values, which the caller answers in Python.
///
/// # Safety
/// As for the single-target export, with `targets` readable for `target_count * 3`
/// values, `out_values` writable for `target_count * 5` and `out_at_end` for
/// `target_count` bytes.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_nearest_on_nurbs_many(
    order: usize,
    knots: *const f64,
    knot_count: usize,
    cvs: *const f64,
    cv_count: usize,
    rational: u8,
    closed: u8,
    targets: *const f64,
    target_count: usize,
    out_values: *mut f64,
    out_at_end: *mut u8,
) -> i32 {
    if cv_count > MAX_CVS || rational > 1 || closed > 1 {
        return ERR_INPUT;
    }
    let (Some(knot_values), Some(cv_values), Some(points)) = (
        crate::checked_slice(knots, knot_count),
        crate::checked_slice(cvs, cv_count * 4),
        crate::checked_slice(targets, target_count * 3),
    ) else {
        return ERR_NULL;
    };
    if !knot_values.iter().chain(cv_values).all(|v| v.is_finite()) {
        return ERR_INPUT;
    }
    let Some(curve) = Nurbs::new(order, knot_values, cv_values, rational == 1) else {
        return ERR_INPUT;
    };
    let (t0, t1) = curve.domain();
    if t1 <= t0 {
        return ERR_INPUT;
    }
    let (Some(out), Some(ends)) = (
        crate::checked_slice_mut(out_values, target_count * 5),
        crate::checked_slice_mut(out_at_end, target_count),
    ) else {
        return ERR_NULL;
    };
    for ((point, slot), end) in points
        .chunks_exact(3)
        .zip(out.chunks_exact_mut(5))
        .zip(ends)
    {
        if !point.iter().all(|v| v.is_finite()) {
            slot.fill(f64::NAN);
            *end = 0;
            continue;
        }
        let r = search(&curve, [point[0], point[1], point[2]], closed == 1);
        slot.copy_from_slice(&[r.t, r.point[0], r.point[1], r.point[2], r.distance]);
        *end = u8::from(r.at_end);
    }
    0
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
    rational: u8,
    closed: u8,
    target: *const f64,
    out_values: *mut f64,
    out_at_end: *mut u8,
) -> i32 {
    if out_values.is_null() || out_at_end.is_null() {
        return ERR_NULL;
    }
    if cv_count > MAX_CVS || rational > 1 || closed > 1 {
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
    let Some(curve) = Nurbs::new(order, knot_values, cv_values, rational == 1) else {
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

/// Most samples [`narwhal_pure_core_sample_nurbs`] writes in one call.
const MAX_SAMPLE_SEGMENTS: usize = 1 << 20;

/// `bridge.sample_curve` on a NURBS curve: `segments + 1` points of `C(t)` at
/// `t = t0 + (t1 - t0) * i / segments`, each as `PointAt(t)` gives it.
///
/// `t0` and `t1` are the curve's `Domain`, passed in so the parameters repeat the
/// Python expression on the same values. Fails (`ERR_INPUT`) rather than writing a
/// point openNURBS could not evaluate, so the caller keeps its own sampling.
///
/// # Safety
/// `knots` is readable for `knot_count` values and `cvs` for `cv_count * 4`
/// homogeneous values; `out_points` is writable for `out_capacity` values, at least
/// `(segments + 1) * 3`.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_sample_nurbs(
    order: usize,
    knots: *const f64,
    knot_count: usize,
    cvs: *const f64,
    cv_count: usize,
    rational: u8,
    t0: f64,
    t1: f64,
    segments: usize,
    out_points: *mut f64,
    out_capacity: usize,
) -> i32 {
    if cv_count > MAX_CVS
        || rational > 1
        || !(1..=MAX_SAMPLE_SEGMENTS).contains(&segments)
        || !t0.is_finite()
        || !t1.is_finite()
    {
        return ERR_INPUT;
    }
    let needed = (segments + 1) * 3;
    if out_capacity < needed {
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
    let Some(out) = crate::checked_slice_mut(out_points, needed) else {
        return ERR_NULL;
    };
    let span = t1 - t0;
    for (i, slot) in out.chunks_exact_mut(3).enumerate() {
        // The same operations as Python's `T0 + (T1 - T0) * i / n`.
        let p = curve.point(t0 + span * i as f64 / segments as f64);
        if !p.iter().all(|v| v.is_finite()) {
            return ERR_INPUT;
        }
        slot.copy_from_slice(&p);
    }
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
    fn the_sized_basis_table_gives_the_full_tables_values_at_every_order() {
        // Orders 2-12 cover all three table sizes (16, 64 and MAX_ORDER^2 values).
        for order in 2..=12 {
            let d = order - 1;
            let knot: Vec<f64> = (0..2 * d)
                .map(|i| i as f64 * 0.75 + (i % 3) as f64 * 0.1)
                .collect();
            let t = 0.5 * (knot[d - 1] + knot[d]) + 0.01;
            for der in 0..=2.min(d) {
                let mut full = [0.0_f64; MAX_ORDER * MAX_ORDER];
                basis(order, &knot, t, &mut full);
                if der > 0 {
                    basis_derivatives(order, &knot, der, &mut full);
                }
                let sized = with_basis_scratch(order, |n| {
                    basis(order, &knot, t, n);
                    if der > 0 {
                        basis_derivatives(order, &knot, der, n);
                    }
                    n.to_vec()
                });
                assert_eq!(sized.len(), order * order);
                let rows = (der + 1) * order;
                assert_eq!(
                    &sized[..rows],
                    &full[..rows],
                    "order {order}, derivative {der}"
                );
            }
        }
    }

    #[test]
    fn rational_circle_points_lie_on_the_circle() {
        let (knots, cvs) = circle();
        let curve = Nurbs::new(3, &knots, &cvs, true).unwrap();
        for i in 0..=40 {
            let t = 2.0 * std::f64::consts::PI * i as f64 / 40.0;
            let p = curve.point(t);
            assert!((p[0].hypot(p[1]) - 5.0).abs() < 1e-12, "{t}: {p:?}");
        }
    }

    #[test]
    fn the_batch_answers_each_target_as_the_single_export_does() {
        let (knots, cvs) = cubic();
        let targets = [
            3.0,
            4.0,
            0.0,
            -2.0,
            1.0,
            0.5,
            f64::NAN,
            0.0,
            0.0,
            20.0,
            0.0,
            0.0,
        ];
        let mut out = [0.0; 20];
        let mut ends = [9u8; 4];
        let code = unsafe {
            narwhal_pure_core_nearest_on_nurbs_many(
                4,
                knots.as_ptr(),
                7,
                cvs.as_ptr(),
                5,
                0,
                0,
                targets.as_ptr(),
                4,
                out.as_mut_ptr(),
                ends.as_mut_ptr(),
            )
        };
        assert_eq!(code, 0);
        for (i, target) in targets.chunks_exact(3).enumerate() {
            let slot = &out[i * 5..i * 5 + 5];
            if target[0].is_nan() {
                assert!(slot.iter().all(|v| v.is_nan()) && ends[i] == 0);
                continue;
            }
            let mut single = [0.0; 5];
            let mut end = 0u8;
            let code = unsafe {
                narwhal_pure_core_nearest_on_nurbs(
                    4,
                    knots.as_ptr(),
                    7,
                    cvs.as_ptr(),
                    5,
                    0,
                    0,
                    target.as_ptr(),
                    single.as_mut_ptr(),
                    &mut end,
                )
            };
            assert_eq!(code, 0);
            assert_eq!((slot, ends[i]), (&single[..], end));
        }
        assert_eq!(ends[3], 1, "past the open end");
    }

    #[test]
    fn derivatives_match_central_differences() {
        let (knots, cvs) = circle();
        let curve = Nurbs::new(3, &knots, &cvs, true).unwrap();
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
    fn general_spans_match_central_differences_and_hit_the_end_cvs() {
        // The interior knot makes both spans general (non-Bezier) spans.
        let (knots, cvs) = cubic();
        let curve = Nurbs::new(4, &knots, &cvs, false).unwrap();
        assert_eq!(curve.point(0.0), [0.0, 0.0, 0.0]);
        assert_eq!(curve.point(2.0), [12.0, 0.0, 0.0]);
        let h = 1e-5;
        for &t in &[0.2, 0.7, 1.3, 1.8] {
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
    fn span_search_matches_open_nurbs() {
        let a = [0.0, 0.0, 1.0, 1.0, 2.0, 3.0, 3.0];
        assert_eq!(search_monotone(&a, -1.0), -1);
        assert_eq!(search_monotone(&a, 0.0), 1);
        assert_eq!(search_monotone(&a, 1.0), 3);
        assert_eq!(search_monotone(&a, 2.5), 4);
        assert_eq!(search_monotone(&a, 3.0), 6);
        assert_eq!(search_monotone(&a, 4.0), 7);
    }

    #[test]
    fn nearest_on_the_circle_is_the_radial_projection() {
        let (knots, cvs) = circle();
        let curve = Nurbs::new(3, &knots, &cvs, true).unwrap();
        let r = search(&curve, [8.0, 6.0, 2.0], true);
        assert!((r.point[0] - 4.0).abs() < 1e-12 && (r.point[1] - 3.0).abs() < 1e-12);
        assert!(!r.at_end);
    }

    #[test]
    fn off_the_end_of_an_open_curve_returns_the_end() {
        let (knots, cvs) = cubic();
        let curve = Nurbs::new(4, &knots, &cvs, false).unwrap();
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
        let curve = Nurbs::new(4, &knots, &cvs, false).unwrap();
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
                0,
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut at_end,
            )
        };
        assert_eq!(bad, ERR_INPUT);
        let bad_flag = unsafe {
            narwhal_pure_core_nearest_on_nurbs(
                4,
                knots.as_ptr(),
                7,
                cvs.as_ptr(),
                5,
                2,
                0,
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut at_end,
            )
        };
        assert_eq!(bad_flag, ERR_INPUT);
    }

    fn sample(
        order: usize,
        knots: &[f64],
        cvs: &[f64],
        rational: u8,
        t: (f64, f64),
        n: usize,
    ) -> (i32, Vec<f64>) {
        let mut out = vec![f64::NAN; (n + 1) * 3];
        let code = unsafe {
            narwhal_pure_core_sample_nurbs(
                order,
                knots.as_ptr(),
                knots.len(),
                cvs.as_ptr(),
                cvs.len() / 4,
                rational,
                t.0,
                t.1,
                n,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        (code, out)
    }

    #[test]
    fn samples_are_point_at_uniform_parameters() {
        let (knots, cvs) = cubic();
        let curve = Nurbs::new(4, &knots, &cvs, false).unwrap();
        let (code, out) = sample(4, &knots, &cvs, 0, (0.0, 2.0), 7);
        assert_eq!(code, 0);
        for (i, p) in out.chunks_exact(3).enumerate() {
            assert_eq!(p, curve.point(0.0 + 2.0 * i as f64 / 7.0));
        }
        // A clamped curve starts and ends exactly on its end control points.
        assert_eq!(&out[..3], &[0.0, 0.0, 0.0]);
        assert_eq!(&out[out.len() - 3..], &[12.0, 0.0, 0.0]);

        let (knots, cvs) = circle();
        let (code, out) = sample(3, &knots, &cvs, 1, (0.0, 2.0 * std::f64::consts::PI), 24);
        assert_eq!(code, 0);
        for p in out.chunks_exact(3) {
            assert!((p[0].hypot(p[1]) - 5.0).abs() < 1e-12, "{p:?}");
        }
    }

    #[test]
    fn sampling_refuses_bad_input_without_a_partial_answer() {
        let (knots, cvs) = cubic();
        assert_eq!(sample(4, &knots, &cvs, 0, (0.0, 2.0), 0).0, ERR_INPUT);
        assert_eq!(sample(4, &knots, &cvs, 0, (0.0, f64::NAN), 4).0, ERR_INPUT);
        assert_eq!(sample(4, &knots, &cvs, 2, (0.0, 2.0), 4).0, ERR_INPUT);
        assert_eq!(sample(4, &knots[..6], &cvs, 0, (0.0, 2.0), 4).0, ERR_INPUT);
        let mut short = vec![0.0; 3 * 4];
        let code = unsafe {
            narwhal_pure_core_sample_nurbs(
                4,
                knots.as_ptr(),
                knots.len(),
                cvs.as_ptr(),
                5,
                0,
                0.0,
                2.0,
                4,
                short.as_mut_ptr(),
                short.len(),
            )
        };
        assert_eq!(code, ERR_INPUT);
    }
}
