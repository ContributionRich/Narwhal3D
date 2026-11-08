//! Curve/curve intersection of two NURBS curves: `intersect._subdivide` and `_dedupe`.
//!
//! `intersect.curve_curve` prunes span pairs whose bounding boxes do not meet, halves
//! still-curved spans with `Curve.Split`, and resolves near-linear pairs by
//! `Intersection.LineLine` on their chords (else the closest approach of the two
//! segments) followed by Newton on the true sub-curves. This module repeats that
//! recursion for two `NurbsCurve`s. The rhino3dm operations it calls are ported from
//! the openNURBS that rhino3dm 8.35.0 pins (mcneel/opennurbs eb92af3), in their
//! operation order:
//!
//! * `Split` is `ON_NurbsCurve::Split`: `ON_NurbsSpanIndex` from above,
//!   `TweakSplitTrimParameter`, the knot-equal left count, `ON_EvaluateNurbsDeBoor`
//!   on each side and `ClampEnd(2)` (`ON_ClampKnotVector`);
//! * `GetBoundingBox` is `ON_GetPointListBoundingBox` over the control points;
//! * `PointAt` is the Near snap's `ON_NurbsCurve::Evaluate` port (`nearest::Nurbs`);
//! * `Intersection.LineLine` is `ON_Intersect(ON_Line, ON_Line)` with its 2x2
//!   `ON_Matrix::RowReduce` / `BackSolve` and near-singular check.
//!
//! The Python helpers keep Python's semantics: `math.dist` is CPython's `vector_norm`,
//! `sum()` is its compensated sum, `min`/`max` keep the first argument on ties and
//! `sorted` is stable. So every hit is bit-identical to the Python's. The parameters
//! and points are intersection results on existing curves; this never creates or
//! changes document geometry.

use crate::nearest::{search_monotone, Nurbs};

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
const MAX_CVS: usize = 1 << 16;
const MAX_ORDER: usize = 32;
const S: usize = 4; // homogeneous stride of every stored control point

// openNURBS's literals, kept verbatim
#[allow(clippy::excessive_precision)]
const ON_EPSILON: f64 = 2.2204460492503131e-16;
#[allow(clippy::excessive_precision)]
const ON_SQRT_EPSILON: f64 = 1.490116119385000000e-8;
#[allow(clippy::excessive_precision)]
const ON_ZERO_TOLERANCE: f64 = 2.3283064365386962890625e-10;
#[allow(clippy::excessive_precision)]
const ON_DBL_MIN: f64 = 2.22507385850720200e-308;

type P3 = [f64; 3];

// ---------------------------------------------------------------------------
// CPython float semantics
// ---------------------------------------------------------------------------

fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// `sum()` of three floats from the int start 0 (CPython 3.12+ Neumaier).
fn py_sum3(values: [f64; 3]) -> f64 {
    let mut total = 0.0 + values[0];
    let mut compensation = 0.0;
    for &value in &values[1..] {
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

fn double_length_mul(x: f64, y: f64) -> (f64, f64) {
    let product = x * y;
    (product, x.mul_add(y, -product))
}

fn double_length_fast_sum(a: f64, b: f64) -> (f64, f64) {
    let sum = a + b;
    (sum, (a - sum) + b)
}

fn frexp_exponent(value: f64) -> i32 {
    let bits = value.to_bits() & 0x7fff_ffff_ffff_ffff;
    let exponent = ((bits >> 52) & 0x7ff) as i32;
    if exponent != 0 {
        return exponent - 1022;
    }
    let mantissa = bits & ((1_u64 << 52) - 1);
    (63 - mantissa.leading_zeros() as i32) - 1073
}

fn exact_power_of_two(exponent: i32) -> f64 {
    if exponent >= -1022 {
        f64::from_bits(((exponent + 1023) as u64) << 52)
    } else {
        f64::from_bits(1_u64 << (exponent + 1074))
    }
}

/// CPython's `vector_norm` (as in `selection_core`), for finite non-negative values.
fn python_vector_norm(mut values: [f64; 3], maximum: f64) -> f64 {
    if maximum.is_infinite() || maximum == 0.0 {
        return maximum;
    }
    let maximum_exponent = frexp_exponent(maximum);
    if maximum_exponent < -1023 {
        for value in &mut values {
            *value /= f64::MIN_POSITIVE;
        }
        return f64::MIN_POSITIVE * python_vector_norm(values, maximum / f64::MIN_POSITIVE);
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

/// `math.dist(p, q)` in three dimensions.
fn py_dist(p: P3, q: P3) -> f64 {
    let values = [
        (p[0] - q[0]).abs(),
        (p[1] - q[1]).abs(),
        (p[2] - q[2]).abs(),
    ];
    if values.iter().any(|v| v.is_nan()) {
        return f64::NAN;
    }
    python_vector_norm(values, values[0].max(values[1]).max(values[2]))
}

/// `intersect._clamp`.
fn clamp(x: f64, lo: f64, hi: f64) -> f64 {
    if x < lo {
        lo
    } else if x > hi {
        hi
    } else {
        x
    }
}

// ---------------------------------------------------------------------------
// openNURBS: knots, de Boor, split, bounding box
// ---------------------------------------------------------------------------

/// `ON_NurbsSpanIndex(order, cv_count, knot, t, side, hint)`.
fn span_index(
    order: usize,
    cv_count: usize,
    knot: &[f64],
    t: f64,
    side: i32,
    hint: usize,
) -> usize {
    let knot = &knot[order - 2..];
    let mut len = cv_count - order + 2;
    let mut base = 0;
    let mut hint = hint;
    if hint > 0 && hint < len - 1 {
        while hint > 0 && knot[hint - 1] == knot[hint] {
            hint -= 1;
        }
        if hint > 0 {
            if t < knot[hint] {
                len = hint + 1;
                hint = 0;
            } else {
                if side < 0 && t == knot[hint] {
                    hint -= 1;
                }
                base = hint;
                len -= hint;
            }
        }
    } else {
        hint = 0;
    }
    let k = &knot[base..base + len];
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

/// `TweakSplitTrimParameter`.
fn tweak_split_parameter(k0: f64, k1: f64, t: &mut f64) -> bool {
    if k0 < *t && *t < k1 {
        let ktol1 = (k1 - k0) * ON_SQRT_EPSILON;
        let ktol2 = (k0.abs() + k1.abs()) * 8.0 * ON_EPSILON;
        let ktol = if ktol1 > ktol2 { ktol1 } else { ktol2 };
        if *t - k0 <= ktol && k1 - *t > 16.0 * ktol {
            *t = k0;
            return true;
        } else if k1 - *t <= ktol && *t - k0 > 16.0 * ktol {
            *t = k1;
            return true;
        }
    }
    false
}

/// `ON_EvaluateNurbsDeBoor(cv_dim, order, S, cv, knots, side, 0.0, t)` with `side`
/// -1 or +1; `cv` starts at the span's first control point, `knots` at its first knot.
fn de_boor(cv_dim: usize, order: usize, cv: &mut [f64], knots: &[f64], side: i32, t: f64) -> bool {
    let degree = order - 1;
    let t0 = knots[degree - 1];
    let t1 = knots[degree];
    if t0 == t1 {
        return false;
    }
    let mut delta = [0.0_f64; MAX_ORDER];
    if side < 0 {
        if t == t1 && t1 == knots[2 * degree - 1] {
            return true;
        }
        let kb = degree - 1;
        if t0 == knots[0] {
            // side -2: the left end is fully multiple at t0
            let dt = t - t0;
            for k in (1..order).rev() {
                for i in 0..k {
                    let alpha1 = dt / (knots[kb + k - i] - t0);
                    let alpha0 = 1.0 - alpha1;
                    let (c0, c1) = ((order - 2 - i) * S, (order - 1 - i) * S);
                    for c in (0..cv_dim).rev() {
                        cv[c1 + c] = cv[c0 + c] * alpha0 + cv[c1 + c] * alpha1;
                    }
                }
            }
        } else {
            for (m, d) in delta.iter_mut().take(degree).enumerate() {
                *d = t - knots[kb - m];
            }
            for k in (1..order).rev() {
                for i in 0..k {
                    let alpha1 = delta[i] / (knots[kb + k - i] - knots[kb - i]);
                    let alpha0 = 1.0 - alpha1;
                    let (c0, c1) = ((order - 2 - i) * S, (order - 1 - i) * S);
                    for c in (0..cv_dim).rev() {
                        cv[c1 + c] = cv[c0 + c] * alpha0 + cv[c1 + c] * alpha1;
                    }
                }
            }
        }
    } else {
        if t == t0 && t0 == knots[0] {
            return true;
        }
        let kb = degree;
        if t1 == knots[2 * degree - 1] {
            // side 2: the right end is fully multiple at t1
            let dt = t1 - t;
            for k in (1..order).rev() {
                for i in 0..k {
                    let alpha0 = dt / (t1 - knots[kb - k + i]);
                    let alpha1 = 1.0 - alpha0;
                    let (c0, c1) = (i * S, (i + 1) * S);
                    for c in 0..cv_dim {
                        cv[c0 + c] = cv[c0 + c] * alpha0 + cv[c1 + c] * alpha1;
                    }
                }
            }
        } else {
            for (m, d) in delta.iter_mut().take(degree).enumerate() {
                *d = knots[kb + m] - t;
            }
            for k in (1..order).rev() {
                for i in 0..k {
                    let alpha0 = delta[i] / (knots[kb + i] - knots[kb - k + i]);
                    let alpha1 = 1.0 - alpha0;
                    let (c0, c1) = (i * S, (i + 1) * S);
                    for c in 0..cv_dim {
                        cv[c0 + c] = cv[c0 + c] * alpha0 + cv[c1 + c] * alpha1;
                    }
                }
            }
        }
    }
    true
}

/// A NURBS curve in openNURBS form, owned (a split produces new ones).
#[derive(Clone)]
struct Curve {
    order: usize,
    cv_count: usize,
    rational: bool,
    cvs: Vec<f64>,
    knots: Vec<f64>,
}

/// A curve with its evaluator, its domain and its bounding box.
struct Piece<'a> {
    curve: &'a Curve,
    eval: Nurbs<'a>,
    t0: f64,
    t1: f64,
}

impl Curve {
    fn cv_dim(&self) -> usize {
        3 + usize::from(self.rational)
    }

    fn knot_count(order: usize, cv_count: usize) -> usize {
        order + cv_count - 2
    }

    fn piece(&self) -> Option<Piece<'_>> {
        let eval = Nurbs::new(self.order, &self.knots, &self.cvs, self.rational)?;
        Some(Piece {
            curve: self,
            eval,
            t0: self.knots[self.order - 2],
            t1: self.knots[self.cv_count - 1],
        })
    }

    /// `ON_ClampKnotVector(cv_dim, order, cv_count, S, cv, knot, 2)`.
    fn clamp_ends(&mut self) {
        let (order, dim) = (self.order, self.cv_dim());
        let k = self.knots[order - 2];
        if de_boor(dim, order, &mut self.cvs, &self.knots, 1, k) {
            for i in 0..order - 2 {
                self.knots[i] = self.knots[order - 2];
            }
        }
        let i0 = self.cv_count - order;
        let k = self.knots[i0 + order - 1];
        let (cvs, knots) = (&mut self.cvs[i0 * S..], &mut self.knots[i0..]);
        if de_boor(dim, order, cvs, knots, -1, k) {
            for i in (order..=2 * order - 3).rev() {
                knots[i] = knots[order - 1];
            }
        }
    }

    /// `ON_NurbsCurve::Split(t)` (the curve is valid: the caller checks `IsValid`).
    fn split(&self, t: f64) -> Option<(Curve, Curve)> {
        let (order, n, k) = (self.order, self.cv_count, &self.knots);
        if !(t > k[order - 2] && t < k[n - 1]) {
            return None;
        }
        let mut span = span_index(order, n, k, t, 1, 0);
        let mut split_t = t;
        if tweak_split_parameter(k[span + order - 2], k[span + order - 1], &mut split_t) {
            if split_t <= k[order - 2] || split_t >= k[n - 1] {
                return None;
            }
            span = span_index(order, n, k, split_t, 1, span);
        }
        if span > n - order {
            return None;
        }
        let mut left_count = order + span;
        if span > 0 && split_t == k[span + order - 2] {
            let mut j = 0;
            while left_count >= order && j <= span + order - 2 {
                if split_t == k[span + order - 2 - j] {
                    left_count -= 1;
                } else {
                    break;
                }
                j += 1;
            }
        }
        let right_count = n - span;
        if left_count < order || right_count < order {
            return None;
        }
        let dim = self.cv_dim();
        let mut left = Curve {
            order,
            cv_count: left_count,
            rational: self.rational,
            cvs: self.cvs[..left_count * S].to_vec(),
            knots: k[..Self::knot_count(order, left_count)].to_vec(),
        };
        let mut right = Curve {
            order,
            cv_count: right_count,
            rational: self.rational,
            cvs: self.cvs[span * S..(span + right_count) * S].to_vec(),
            knots: k[span..span + Self::knot_count(order, right_count)].to_vec(),
        };
        let i = left_count - order;
        de_boor(
            dim,
            order,
            &mut left.cvs[i * S..],
            &left.knots[i..],
            -1,
            split_t,
        );
        for j in left_count - 1..Self::knot_count(order, left_count) {
            left.knots[j] = t;
        }
        left.clamp_ends();
        de_boor(dim, order, &mut right.cvs, &right.knots, 1, split_t);
        for j in 0..=order - 2 {
            right.knots[j] = t;
        }
        right.clamp_ends();
        Some((left, right))
    }

    /// `ON_NurbsCurve::GetBBox` (`ON_GetPointListBoundingBox` over the control points).
    fn bbox(&self) -> Option<(P3, P3)> {
        let mut points = self.cvs.chunks_exact(S);
        let euclid = |cv: &[f64]| -> Option<P3> {
            if self.rational {
                if cv[3] == 0.0 {
                    return None;
                }
                let w = 1.0 / cv[3];
                Some([w * cv[0], w * cv[1], w * cv[2]])
            } else {
                Some([cv[0], cv[1], cv[2]])
            }
        };
        let first = euclid(points.next()?)?;
        let (mut lo, mut hi) = (first, first);
        for cv in points {
            let p = euclid(cv)?;
            for j in 0..3 {
                if lo[j] > p[j] {
                    lo[j] = p[j];
                } else if hi[j] < p[j] {
                    hi[j] = p[j];
                }
            }
        }
        Some((lo, hi))
    }
}

impl Piece<'_> {
    fn point(&self, t: f64) -> P3 {
        self.eval.point(t)
    }

    /// `_is_linear_enough`.
    fn is_linear_enough(&self, tol: f64) -> bool {
        let (a, b) = (self.point(self.t0), self.point(self.t1));
        let mut dev = 0.0;
        for f in [0.25, 0.5, 0.75] {
            let t = self.t0 + (self.t1 - self.t0) * f;
            dev = py_max(dev, point_line_dist(self.point(t), a, b));
        }
        dev <= tol
    }

    /// `_velocity`.
    fn velocity(&self, t: f64) -> P3 {
        let mut h = (self.t1 - self.t0) * 1e-6;
        if h <= 0.0 {
            h = 1e-9;
        }
        let ta = py_max(t - h, self.t0);
        let mut tb = py_min(t + h, self.t1);
        if tb <= ta {
            tb = py_min(self.t1, ta + h);
        }
        let (p0, p1) = (self.point(ta), self.point(tb));
        let mut dt = tb - ta;
        if dt == 0.0 {
            dt = 1e-12; // `(t1 - t0) or 1e-12`
        }
        [
            (p1[0] - p0[0]) / dt,
            (p1[1] - p0[1]) / dt,
            (p1[2] - p0[2]) / dt,
        ]
    }
}

// ---------------------------------------------------------------------------
// intersect.py helpers
// ---------------------------------------------------------------------------

/// `_point_line_dist`.
fn point_line_dist(p: P3, a: P3, b: P3) -> f64 {
    let (ux, uy, uz) = (b[0] - a[0], b[1] - a[1], b[2] - a[2]);
    let ulen = (ux * ux + uy * uy + uz * uz).sqrt();
    if ulen < 1e-15 {
        return py_dist(p, a);
    }
    let (wx, wy, wz) = (p[0] - a[0], p[1] - a[1], p[2] - a[2]);
    let cx = wy * uz - wz * uy;
    let cy = wz * ux - wx * uz;
    let cz = wx * uy - wy * ux;
    (cx * cx + cy * cy + cz * cz).sqrt() / ulen
}

/// `_segment_closest`.
fn segment_closest(p1: P3, p2: P3, p3: P3, p4: P3) -> (f64, f64) {
    let d1: P3 = std::array::from_fn(|i| p2[i] - p1[i]);
    let d2: P3 = std::array::from_fn(|i| p4[i] - p3[i]);
    let r: P3 = std::array::from_fn(|i| p1[i] - p3[i]);
    let a = py_sum3(std::array::from_fn(|i| d1[i] * d1[i]));
    let e = py_sum3(std::array::from_fn(|i| d2[i] * d2[i]));
    let f = py_sum3(std::array::from_fn(|i| d2[i] * r[i]));
    if a < 1e-18 && e < 1e-18 {
        return (0.0, 0.0);
    }
    if a < 1e-18 {
        return (0.0, clamp(f / e, 0.0, 1.0));
    }
    let c = py_sum3(std::array::from_fn(|i| d1[i] * r[i]));
    if e < 1e-18 {
        return (clamp(-c / a, 0.0, 1.0), 0.0);
    }
    let bb = py_sum3(std::array::from_fn(|i| d1[i] * d2[i]));
    let denom = a * e - bb * bb;
    let mut s = if denom > 1e-18 {
        clamp((bb * f - c * e) / denom, 0.0, 1.0)
    } else {
        0.0
    };
    let mut t = (bb * s + f) / e;
    if t < 0.0 {
        t = 0.0;
        s = clamp(-c / a, 0.0, 1.0);
    } else if t > 1.0 {
        t = 1.0;
        s = clamp((bb - c) / a, 0.0, 1.0);
    }
    (s, t)
}

/// `_solve2`.
fn solve2(va: P3, vb: P3, f: P3) -> Option<(f64, f64)> {
    let mut best: Option<[f64; 7]> = None;
    for i in 0..3 {
        for j in i + 1..3 {
            let (a11, a12) = (va[i], -vb[i]);
            let (a21, a22) = (va[j], -vb[j]);
            let det = a11 * a22 - a12 * a21;
            if best.is_none_or(|b| det.abs() > b[0].abs()) {
                best = Some([det, a11, a12, a21, a22, f[i], f[j]]);
            }
        }
    }
    let [det, a11, a12, a21, a22, c1, c2] = best?;
    if det.abs() < 1e-18 {
        return None;
    }
    Some(((c1 * a22 - a12 * c2) / det, (a11 * c2 - c1 * a21) / det))
}

fn dot(a: P3, b: P3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

/// `ON_Length3d`, behind `ON_3dPoint::DistanceTo`.
fn on_length3d(x: f64, y: f64, z: f64) -> f64 {
    let (mut x, mut y, mut z) = (x.abs(), y.abs(), z.abs());
    if y >= x && y >= z {
        std::mem::swap(&mut x, &mut y);
    } else if z >= x && z >= y {
        std::mem::swap(&mut x, &mut z);
    }
    if x > ON_DBL_MIN {
        y /= x;
        z /= x;
        x * (1.0 + y * y + z * z).sqrt()
    } else if x > 0.0 && x.is_finite() {
        x
    } else {
        0.0
    }
}

fn on_distance(p: P3, q: P3) -> f64 {
    on_length3d(q[0] - p[0], q[1] - p[1], q[2] - p[2])
}

/// `ON_Line::PointAt`.
fn line_point_at(from: P3, to: P3, t: f64) -> P3 {
    if t < 0.5 {
        std::array::from_fn(|i| from[i] + t * (to[i] - from[i]))
    } else {
        std::array::from_fn(|i| to[i] + (1.0 - t) * (from[i] - to[i]))
    }
}

/// `ON_Line::ClosestPointTo(point)` (not clamped to the chord).
fn line_closest_point(from: P3, to: P3, p: P3) -> P3 {
    let d: P3 = std::array::from_fn(|i| to[i] - from[i]);
    let dod = dot(d, d);
    let t = if dod > 0.0 {
        let pf: P3 = std::array::from_fn(|i| p[i] - from[i]);
        let pt: P3 = std::array::from_fn(|i| p[i] - to[i]);
        if dot(pf, pf) <= dot(pt, pt) {
            dot(pf, d) / dod
        } else {
            1.0 + dot(pt, d) / dod
        }
    } else {
        0.0
    };
    line_point_at(from, to, t)
}

/// `ON_Intersect(ON_Line, ON_Line, &a, &b)`: `(ok, a, b)` as `Intersection.LineLine`.
fn line_line(a0: P3, a1: P3, b0: P3, b1: P3) -> (bool, f64, f64) {
    let a: P3 = std::array::from_fn(|i| a1[i] - a0[i]);
    let b: P3 = std::array::from_fn(|i| b1[i] - b0[i]);
    let c: P3 = std::array::from_fn(|i| b0[i] - a0[i]);
    let mut m = [[dot(a, a), -dot(a, b)], [0.0, dot(b, b)]];
    m[1][0] = m[0][1];
    let swapped = m[0][0] < m[1][1];
    if swapped {
        for row in &mut m {
            row.swap(0, 1);
        }
    }
    let (pr_tolerance, zero_tol) = if m[1][1].abs() > 1.0 {
        (
            m[1][1].abs() * ON_SQRT_EPSILON,
            10.0 * m[1][1].abs() * ON_EPSILON,
        )
    } else {
        (ON_SQRT_EPSILON, 10.0 * ON_EPSILON)
    };
    let mut y = [dot(a, c), -dot(b, c)];
    // ON_Matrix::RowReduce(zero_tol, Y, &pivot) on the 2x2 matrix
    let mut piv = 0.0;
    let mut rank = 0;
    for k in 0..2 {
        let mut ix = k;
        let mut x = m[ix][k].abs();
        for (i, row) in m.iter().enumerate().skip(k + 1) {
            if row[k].abs() > x {
                ix = i;
                x = row[k].abs();
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
        // Scale row k; for k = 0 then clear column 0 of row 1. Only column 1 lies right
        // of the pivot, so ON_ArrayScale / ON_Array_aA_plus_B act on one value.
        let x = 1.0 / m[k][k];
        m[k][k] = 1.0;
        if k == 0 {
            m[0][1] *= x;
        }
        y[k] *= x;
        if k == 0 {
            let x = -m[1][0];
            m[1][0] = 0.0;
            if x.abs() > zero_tol {
                m[1][1] += x * m[0][1]; // a*A + B, the sum commutes exactly
                y[1] += x * y[0];
            }
        }
    }
    if rank != 2 {
        return (false, 0.0, 0.0);
    }
    if a0 == b0 {
        return (true, 0.0, 0.0);
    }
    if a0 == b1 {
        return (true, 0.0, 1.0);
    }
    if a1 == b0 {
        return (true, 1.0, 0.0);
    }
    if a1 == b1 {
        return (true, 1.0, 1.0);
    }
    // ON_Matrix::BackSolve(0.0, 2, Y, X)
    let x1 = y[1];
    let x0 = y[0] - m[0][1] * x1;
    let x = [x0, x1];
    let i = usize::from(swapped);
    let (pa_t, pb_t) = (x[i], x[1 - i]);
    let mut ok = true;
    if piv.abs() <= pr_tolerance {
        let pa = line_point_at(a0, a1, pa_t);
        let pb = line_point_at(b0, b1, pb_t);
        let d = on_distance(pa, pb);
        if d > pr_tolerance && d > ON_ZERO_TOLERANCE {
            let qa = line_closest_point(a0, a1, pb);
            let qb = line_closest_point(b0, b1, pa);
            let da = on_distance(pa, qb);
            let db = on_distance(pb, qa);
            if 1.1 * da < d || 1.1 * db < d {
                ok = false;
            }
        }
    }
    (ok, pa_t, pb_t)
}

// ---------------------------------------------------------------------------
// the recursion
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq)]
struct Hit {
    point: P3,
    t_a: f64,
    t_b: f64,
}

/// `_newton`: `(t_a, t_b, gap, point)`.
fn newton(ca: &Piece, cb: &Piece, mut t_a: f64, mut t_b: f64) -> (f64, f64, f64, P3) {
    for _ in 0..20 {
        let (pa, pb) = (ca.point(t_a), cb.point(t_b));
        let (fx, fy, fz) = (pa[0] - pb[0], pa[1] - pb[1], pa[2] - pb[2]);
        let gap = (fx * fx + fy * fy + fz * fz).sqrt();
        if gap <= 1e-14 {
            return (t_a, t_b, gap, pa);
        }
        let (va, vb) = (ca.velocity(t_a), cb.velocity(t_b));
        let Some((dta, dtb)) = solve2(va, vb, [fx, fy, fz]) else {
            break;
        };
        t_a = clamp(t_a - dta, ca.t0, ca.t1);
        t_b = clamp(t_b - dtb, cb.t0, cb.t1);
    }
    let (pa, pb) = (ca.point(t_a), cb.point(t_b));
    let gap = py_dist(pa, pb);
    let mid = [
        (pa[0] + pb[0]) / 2.0,
        (pa[1] + pb[1]) / 2.0,
        (pa[2] + pb[2]) / 2.0,
    ];
    (t_a, t_b, gap, mid)
}

/// `_base_case`.
fn base_case(ca: &Piece, cb: &Piece, tol: f64) -> Option<Hit> {
    let (a0, a1) = (ca.point(ca.t0), ca.point(ca.t1));
    let (b0, b1) = (cb.point(cb.t0), cb.point(cb.t1));
    let in01 = |x: f64| (-1e-9..=1.0 + 1e-9).contains(&x); // `_in01`
    let (ok, u, v) = line_line(a0, a1, b0, b1);
    let (u, v) = if ok && in01(u) && in01(v) {
        (clamp(u, 0.0, 1.0), clamp(v, 0.0, 1.0))
    } else {
        segment_closest(a0, a1, b0, b1)
    };
    let t_a = ca.t0 + (ca.t1 - ca.t0) * u;
    let t_b = cb.t0 + (cb.t1 - cb.t0) * v;
    let (t_a, t_b, gap, point) = newton(ca, cb, t_a, t_b);
    if gap > py_max(tol, 1e-9) {
        return None;
    }
    Some(Hit { point, t_a, t_b })
}

/// `_boxes_overlap`.
fn boxes_overlap(a: (P3, P3), b: (P3, P3), tol: f64) -> bool {
    (0..3).all(|i| a.0[i] - tol <= b.1[i] && b.0[i] - tol <= a.1[i])
}

/// `_subdivide`; `None` where the Python would reach an operation this port does not
/// cover (an invalid sub-curve), so the caller runs the Python.
fn subdivide(
    ca: &Curve,
    cb: &Curve,
    tol: f64,
    depth: usize,
    max_depth: usize,
    out: &mut Vec<Hit>,
) -> Option<()> {
    let (pa, pb) = (ca.piece()?, cb.piece()?);
    if !boxes_overlap(ca.bbox()?, cb.bbox()?, tol) {
        return Some(());
    }
    let a_lin = pa.is_linear_enough(tol);
    let b_lin = pb.is_linear_enough(tol);
    let forced = depth >= max_depth;
    if (a_lin && b_lin) || forced {
        out.extend(base_case(&pa, &pb, tol));
        return Some(());
    }
    let a_parts = if a_lin {
        None
    } else {
        ca.split(0.5 * (pa.t0 + pa.t1))
    };
    let b_parts = if b_lin {
        None
    } else {
        cb.split(0.5 * (pb.t0 + pb.t1))
    };
    if a_parts.is_none() && b_parts.is_none() {
        out.extend(base_case(&pa, &pb, tol));
        return Some(());
    }
    let xs: Vec<&Curve> = match &a_parts {
        Some((l, r)) => vec![l, r],
        None => vec![pa.curve],
    };
    let ys: Vec<&Curve> = match &b_parts {
        Some((l, r)) => vec![l, r],
        None => vec![pb.curve],
    };
    for x in &xs {
        for y in &ys {
            subdivide(x, y, tol, depth + 1, max_depth, out)?;
        }
    }
    Some(())
}

/// `_dedupe`: sorted by `t_a` (stable), dropping hits within the threshold of a kept one.
fn dedupe(mut hits: Vec<Hit>, tol: f64) -> Vec<Hit> {
    let thresh = py_max(tol, 1e-9);
    hits.sort_by(|a, b| {
        a.t_a
            .partial_cmp(&b.t_a)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut kept: Vec<Hit> = Vec::new();
    for h in hits {
        if !kept.iter().any(|k| py_dist(h.point, k.point) <= thresh) {
            kept.push(h);
        }
    }
    kept
}

/// `curve_curve`'s point hits for two NURBS curves: `_subdivide` then `_dedupe`.
fn point_hits(a: &Curve, b: &Curve, tol: f64, max_depth: usize) -> Option<Vec<Hit>> {
    let mut hits = Vec::new();
    subdivide(a, b, tol, 0, max_depth, &mut hits)?;
    Some(dedupe(hits, tol))
}

unsafe fn read_curve(
    order: usize,
    knots: *const f64,
    knot_count: usize,
    cvs: *const f64,
    cv_count: usize,
    rational: u8,
) -> Result<Curve, i32> {
    if !(2..=MAX_ORDER).contains(&order) || cv_count > MAX_CVS || rational > 1 || cv_count < order {
        return Err(ERR_INPUT);
    }
    let (Some(k), Some(c)) = (
        crate::checked_slice(knots, knot_count),
        crate::checked_slice(cvs, cv_count * S),
    ) else {
        return Err(ERR_NULL);
    };
    if knot_count != Curve::knot_count(order, cv_count) || !k.iter().chain(c).all(|v| v.is_finite())
    {
        return Err(ERR_INPUT);
    }
    let curve = Curve {
        order,
        cv_count,
        rational: rational == 1,
        cvs: c.to_vec(),
        knots: k.to_vec(),
    };
    curve.piece().ok_or(ERR_INPUT)?;
    Ok(curve)
}

/// The point hits of `intersect.curve_curve(a, b, tol, max_depth)` after its overlap
/// test: `_subdivide` and `_dedupe` on two valid NURBS curves.
///
/// Each curve is passed in openNURBS form (`cv_count + order - 2` knots, homogeneous
/// control points). Writes the hit count to `out_count` and, when it fits `capacity`,
/// each hit's `(x, y, z, t_a, t_b)` to `out_hits`; a too-small `capacity` returns
/// `ERR_SIZE` with the count set. `ERR_INPUT` means the Python must answer.
///
/// # Safety
/// Each pointer is valid for the counts above; `out_hits` for `capacity * 5` values.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_curve_curve_hits(
    a_order: usize,
    a_knots: *const f64,
    a_knot_count: usize,
    a_cvs: *const f64,
    a_cv_count: usize,
    a_rational: u8,
    b_order: usize,
    b_knots: *const f64,
    b_knot_count: usize,
    b_cvs: *const f64,
    b_cv_count: usize,
    b_rational: u8,
    tol: f64,
    max_depth: usize,
    out_hits: *mut f64,
    capacity: usize,
    out_count: *mut usize,
) -> i32 {
    if out_count.is_null() {
        return ERR_NULL;
    }
    *out_count = 0;
    if !tol.is_finite() || max_depth > 1000 {
        return ERR_INPUT;
    }
    let a = match read_curve(
        a_order,
        a_knots,
        a_knot_count,
        a_cvs,
        a_cv_count,
        a_rational,
    ) {
        Ok(c) => c,
        Err(code) => return code,
    };
    let b = match read_curve(
        b_order,
        b_knots,
        b_knot_count,
        b_cvs,
        b_cv_count,
        b_rational,
    ) {
        Ok(c) => c,
        Err(code) => return code,
    };
    let Some(hits) = point_hits(&a, &b, tol, max_depth) else {
        return ERR_INPUT;
    };
    *out_count = hits.len();
    if hits.len() > capacity {
        return ERR_SIZE;
    }
    let Some(out) = crate::checked_slice_mut(out_hits, hits.len() * 5) else {
        return ERR_NULL;
    };
    for (slot, h) in out.chunks_exact_mut(5).zip(&hits) {
        slot.copy_from_slice(&[h.point[0], h.point[1], h.point[2], h.t_a, h.t_b]);
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cubic(points: &[P3]) -> Curve {
        // A clamped uniform cubic through `points` as control points.
        let n = points.len();
        let mut knots = vec![0.0; 3];
        knots.extend((1..=n - 4).map(|i| i as f64));
        knots.extend([(n - 3) as f64; 3]);
        Curve {
            order: 4,
            cv_count: n,
            rational: false,
            cvs: points
                .iter()
                .flat_map(|p| [p[0], p[1], p[2], 1.0])
                .collect(),
            knots,
        }
    }

    #[test]
    fn a_split_reproduces_the_curve_on_both_sides() {
        let c = cubic(&[
            [0.0, 0.0, 0.0],
            [1.0, 3.0, 0.0],
            [3.0, -1.0, 1.0],
            [5.0, 2.0, 0.0],
            [7.0, 0.0, 0.0],
        ]);
        let whole = c.piece().unwrap();
        let (l, r) = c.split(0.7).unwrap();
        let (pl, pr) = (l.piece().unwrap(), r.piece().unwrap());
        assert_eq!((pl.t0, pl.t1, pr.t0, pr.t1), (0.0, 0.7, 0.7, 2.0));
        for t in [0.0, 0.2, 0.5, 0.69] {
            let (a, b) = (whole.point(t), pl.point(t));
            assert!(
                (0..3).all(|i| (a[i] - b[i]).abs() < 1e-12),
                "{t}: {a:?} {b:?}"
            );
        }
        for t in [0.71, 1.0, 1.5, 2.0] {
            let (a, b) = (whole.point(t), pr.point(t));
            assert!(
                (0..3).all(|i| (a[i] - b[i]).abs() < 1e-12),
                "{t}: {a:?} {b:?}"
            );
        }
        assert!(c.split(0.0).is_none() && c.split(2.0).is_none());
    }

    #[test]
    fn crossing_lines_meet_where_line_line_says() {
        let (ok, a, b) = line_line(
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [1.0, -1.0, 0.0],
            [1.0, 1.0, 0.0],
        );
        assert!(ok && a == 0.5 && b == 0.5);
        let (ok, ..) = line_line(
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [1.0, 1.0, 0.0],
        );
        assert!(!ok, "parallel lines do not intersect");
        assert_eq!(
            line_line([0.0; 3], [1.0, 0.0, 0.0], [0.0; 3], [0.0, 1.0, 0.0]),
            (true, 0.0, 0.0)
        );
    }

    #[test]
    fn two_crossing_cubics_have_their_crossings() {
        let a = cubic(&[
            [0.0, 0.0, 0.0],
            [2.0, 4.0, 0.0],
            [4.0, -4.0, 0.0],
            [6.0, 0.0, 0.0],
        ]);
        let b = cubic(&[
            [0.0, 0.5, 0.0],
            [6.0, 0.5, 0.0],
            [12.0, 0.5, 0.0],
            [18.0, 0.5, 0.0],
        ]);
        let hits = point_hits(&a, &b, 1e-6, 40).unwrap();
        // y = 12 t (1 - t) (1 - 2 t) meets y = 0.5 twice
        assert_eq!(hits.len(), 2, "{hits:?}");
        for h in &hits {
            assert!((h.point[1] - 0.5).abs() < 1e-9);
        }
        assert!(hits.windows(2).all(|w| w[0].t_a <= w[1].t_a));
    }

    #[test]
    fn python_helpers() {
        assert_eq!(py_sum3([1e100, 1.0, -1e100]), 1.0);
        assert_eq!(py_dist([0.0; 3], [3.0, 4.0, 12.0]), 13.0);
        assert_eq!(
            segment_closest([0.0; 3], [2.0, 0.0, 0.0], [1.0, 1.0, 0.0], [1.0, 3.0, 0.0]),
            (0.5, 0.0)
        );
        assert_eq!(on_length3d(3.0, 4.0, 0.0), 5.0);
    }
}
