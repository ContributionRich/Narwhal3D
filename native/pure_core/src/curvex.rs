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
//! Either curve may instead be an `ON_PolylineCurve` (what Polyline and Rectangle
//! make), read from its encoded record (`narwhal_pure_core_curve_curve_record_hits`):
//!
//! * `PointAt` is `ON_PolylineCurve::Evaluate` (`ON_NurbsSpanIndex` over `m_t`, then
//!   `(1-s)*P[i] + s*P[i+1]`);
//! * `GetBoundingBox` is `ON_GetPointListBoundingBox` over the points;
//! * `Split` is `ON_PolylineCurve::Split`: `ON_Curve::ParameterSearch` (which snaps a
//!   parameter within `ON_SQRT_EPSILON` of a break to it) and `ON_PolylineCurve::Trim`
//!   on each side, including its micro-segment removal.
//!
//! A polyline's `PointAt` and `Split` round each product separately; an SDK build that
//! fuses a multiply-add rounds them differently, so `narwhal3d/native_intersect.py`
//! routes polylines here only where the loaded rhino3dm matches on witness pairs.
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
/// The deepest recursion the export runs; `curve_curve` asks for 40. A deeper request is
/// left to the Python, so the native stack stays small on any caller's thread.
const MAX_DEPTH: usize = 64;
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
#[allow(clippy::excessive_precision)]
const ON_UNSET_POSITIVE_VALUE: f64 = 1.23432101234321e+308;
const ON_UNSET_VALUE: f64 = -ON_UNSET_POSITIVE_VALUE;

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

/// A curve's evaluator and its domain.
struct Piece<'a> {
    eval: Eval<'a>,
    t0: f64,
    t1: f64,
}

enum Eval<'a> {
    Nurbs(Nurbs<'a>),
    Polyline(&'a Polyline),
}

/// A curve the recursion splits: a NURBS curve or a polyline, each split into its own kind
/// as openNURBS does.
#[derive(Clone)]
enum Shape {
    Nurbs(Curve),
    Polyline(Polyline),
}

impl Shape {
    fn piece(&self) -> Option<Piece<'_>> {
        match self {
            Shape::Nurbs(c) => c.piece(),
            Shape::Polyline(p) => p.piece(),
        }
    }

    fn bbox(&self) -> Option<(P3, P3)> {
        match self {
            Shape::Nurbs(c) => c.bbox(),
            Shape::Polyline(p) => point_list_bbox(p.points.iter().map(|&q| Some(q))),
        }
    }

    fn split(&self, t: f64) -> Option<(Shape, Shape)> {
        match self {
            Shape::Nurbs(c) => c.split(t).map(|(l, r)| (Shape::Nurbs(l), Shape::Nurbs(r))),
            Shape::Polyline(p) => p
                .split(t)
                .map(|(l, r)| (Shape::Polyline(l), Shape::Polyline(r))),
        }
    }
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
            eval: Eval::Nurbs(eval),
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
        point_list_bbox(self.cvs.chunks_exact(S).map(|cv| {
            if self.rational {
                if cv[3] == 0.0 {
                    return None;
                }
                let w = 1.0 / cv[3];
                Some([w * cv[0], w * cv[1], w * cv[2]])
            } else {
                Some([cv[0], cv[1], cv[2]])
            }
        }))
    }
}

/// `ON_GetPointListBoundingBox` over Euclidean points; None for an empty list or a point
/// that has none (a zero weight).
fn point_list_bbox(mut points: impl Iterator<Item = Option<P3>>) -> Option<(P3, P3)> {
    let first = points.next()??;
    let (mut lo, mut hi) = (first, first);
    for p in points {
        let p = p?;
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

// ---------------------------------------------------------------------------
// openNURBS: ON_PolylineCurve
// ---------------------------------------------------------------------------

/// `ON_IS_VALID`: strictly between the unset values (so never NaN).
fn on_is_valid(x: f64) -> bool {
    x > ON_UNSET_VALUE && x < ON_UNSET_POSITIVE_VALUE
}

/// `ON_Interval::IsIncreasing`.
fn is_increasing(d: (f64, f64)) -> bool {
    ON_UNSET_VALUE < d.0 && d.0 < d.1 && d.1 < ON_UNSET_POSITIVE_VALUE
}

/// `ON_Interval::Min`, `Max` (NaN when the ends do not compare).
fn interval_min_max(d: (f64, f64)) -> (f64, f64) {
    if d.0 <= d.1 {
        (d.0, d.1)
    } else if d.1 <= d.0 {
        (d.1, d.0)
    } else {
        (f64::NAN, f64::NAN)
    }
}

/// `ON_Interval::Intersection(other)` on `a`: the intersection, or None (openNURBS then
/// leaves the empty interval, which no caller here reads).
fn interval_intersection(a: (f64, f64), b: (f64, f64)) -> Option<(f64, f64)> {
    let empty = |d: (f64, f64)| d.0 == ON_UNSET_VALUE && d.1 == ON_UNSET_VALUE;
    if empty(a) && empty(b) {
        return None;
    }
    let ((a0, a1), (b0, b1)) = (interval_min_max(a), interval_min_max(b));
    let mn = if a0 >= b0 { a0 } else { b0 };
    let mx = if a1 <= b1 { a1 } else { b1 };
    (mn <= mx).then_some((mn, mx))
}

/// `ON_Interval::NormalizedParameterAt(t)`.
fn normalized_parameter_at(d: (f64, f64), t: f64) -> f64 {
    if !on_is_valid(t) {
        return ON_UNSET_VALUE;
    }
    if d.0 != d.1 {
        if t == d.1 {
            1.0
        } else {
            (t - d.0) / (d.1 - d.0)
        }
    } else {
        0.0
    }
}

/// An `ON_PolylineCurve` with `m_dim` 3: `m_pline` and `m_t`, always the same length.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Polyline {
    points: Vec<P3>,
    t: Vec<f64>,
}

impl Polyline {
    /// `Domain()`: `(m_t[0], m_t[count-1])`, or openNURBS's unset interval.
    fn domain(&self) -> (f64, f64) {
        let count = self.points.len();
        if self.t.len() >= count && count >= 2 && self.t[0] < self.t[count - 1] {
            (self.t[0], self.t[count - 1])
        } else {
            (ON_UNSET_VALUE, ON_UNSET_VALUE)
        }
    }

    /// A piece only on an increasing domain; the Python answers any other.
    fn piece(&self) -> Option<Piece<'_>> {
        let (t0, t1) = self.domain();
        is_increasing((t0, t1)).then_some(Piece {
            eval: Eval::Polyline(self),
            t0,
            t1,
        })
    }

    /// `Evaluate(t, 0, 3, v, 0, nullptr)`, behind `PointAt`. The recursion never asks
    /// at a NaN parameter (`newton` hands one to the Python); answering NaN keeps the
    /// span search on the array if anything ever did.
    pub(crate) fn point(&self, t: f64) -> P3 {
        self.evaluate(t)[0]
    }

    /// `Evaluate(t, 2, 3, v, 0, nullptr)`, behind `DerivativeAt(t, 2)`: the point, the
    /// first derivative `1/(t1-t0) * (P[i+1] - P[i])` and a zero second derivative.
    pub(crate) fn derivatives(&self, t: f64) -> [P3; 3] {
        self.evaluate(t)
    }

    fn evaluate(&self, t: f64) -> [P3; 3] {
        if t.is_nan() {
            return [[f64::NAN; 3]; 3];
        }
        let i = span_index(2, self.points.len(), &self.t, t, 0, 0);
        let (t0, t1) = (self.t[i], self.t[i + 1]);
        let s = if t == t1 { 1.0 } else { (t - t0) / (t1 - t0) };
        let (p, q) = (self.points[i], self.points[i + 1]);
        let r = 1.0 / (t1 - t0);
        [
            std::array::from_fn(|k| (1.0 - s) * p[k] + s * q[k]),
            std::array::from_fn(|k| r * (q[k] - p[k])),
            [0.0; 3],
        ]
    }

    /// `Domain()` as the SDK reports it, for a curve `polyline_from_record` accepted.
    pub(crate) fn ends(&self) -> (f64, f64) {
        self.domain()
    }

    /// `SpanCount()`: `ON_Polyline::SegmentCount`.
    pub(crate) fn span_count(&self) -> usize {
        self.points.len().saturating_sub(1)
    }

    /// `ON_Curve::ParameterSearch(t, index, true, m_t, ON_SQRT_EPSILON)`: whether `t` is,
    /// or snaps to, `m_t[index]`.
    fn parameter_search(&self, t: f64) -> (bool, isize) {
        let count = self.t.len();
        let c_dom = self.domain();
        if count <= 1 || !on_is_valid(t) {
            return (false, -1);
        }
        let mut index = search_monotone(&self.t, t);
        let last = count as isize - 1;
        let mut rc = index >= 0 && index <= last && t == self.t[index as usize];
        if !rc {
            let (a, b) = (c_dom.0.abs(), c_dom.1.abs());
            let ktol = ON_SQRT_EPSILON * if a < b { b } else { a };
            if index >= 0 && index < last {
                let i = index as usize;
                let middle_t = 0.5 * (self.t[i] + self.t[i + 1]);
                if t < middle_t && t - self.t[i] <= ktol {
                    rc = true;
                } else if t > middle_t && self.t[i + 1] - t <= ktol {
                    rc = true;
                    index += 1;
                }
            } else if index == count as isize {
                if t - self.t[count - 1] <= ktol {
                    rc = true;
                    index = last;
                }
            } else if index < 0 && self.t[0] - t <= ktol {
                rc = true;
                index = 0;
            }
        }
        (rc, index)
    }

    /// `Trim(domain)`. On false the curve is left as openNURBS leaves it, which may be
    /// partly trimmed.
    fn trim(&mut self, domain: (f64, f64)) -> bool {
        let mut segment_count = self.t.len() as isize - 1;
        if segment_count < 1 || self.t.len() != self.points.len() || !is_increasing(domain) {
            return false;
        }
        let original = self.domain();
        if !is_increasing(original) {
            return false;
        }
        let Some(output) = interval_intersection(domain, original) else {
            return false;
        };
        if !is_increasing(output) {
            return false;
        }
        let mut actual = output;
        let (found, mut s0) = self.parameter_search(output.0);
        if found && s0 >= 0 && s0 <= segment_count {
            actual.0 = self.t[s0 as usize];
        }
        let (found, mut s1) = self.parameter_search(output.1);
        if found && s1 >= 0 && s1 <= segment_count {
            actual.1 = self.t[s1 as usize];
            s1 -= 1;
        }
        if !is_increasing(actual) {
            return false;
        }
        if s0 < 0 || s0 > s1 || s1 >= segment_count {
            return false;
        }
        if actual == original {
            #[cfg(test)]
            tests::bump(&tests::TRIM_WHOLE);
            self.t[0] = output.0;
            self.t[segment_count as usize] = output.1;
            return true;
        }
        if s1 < segment_count - 1 {
            self.t.truncate(s1 as usize + 2);
            self.points.truncate(s1 as usize + 2);
            segment_count = s1 + 1;
        }
        if s0 > 0 {
            let (from, end) = (s0 as usize, segment_count as usize);
            self.t.copy_within(from..=end, 0);
            self.points.copy_within(from..=end, 0);
            s1 -= s0;
            s0 = 0;
            self.t.truncate(s1 as usize + 2);
            self.points.truncate(s1 as usize + 2);
        }
        let trim_first = self.t[0] < actual.0 || (s1 == 0 && actual.1 < self.t[1]);
        let trim_last =
            s1 > s0 && self.t[s1 as usize] < actual.1 && actual.1 < self.t[s1 as usize + 1];
        if trim_first {
            let seg = (self.t[0], self.t[1]);
            let (chord0, chord1) = (self.points[0], self.points[1]);
            let (mut q0, mut q1) = (chord0, chord1);
            let (mut np0, mut np1) = (0.0, 1.0);
            let (mut set0, mut set1) = (false, false);
            if self.t[0] < actual.0 && actual.0 < self.t[1] {
                np0 = normalized_parameter_at(seg, actual.0);
                q0 = line_point_at(chord0, chord1, np0);
                set0 = true;
            }
            if s1 == 0 && self.t[0] < actual.1 && actual.1 < self.t[1] {
                np1 = normalized_parameter_at(seg, actual.1);
                q1 = line_point_at(chord0, chord1, np1);
                set1 = true;
            }
            if np0 >= np1 {
                return false; // trim is not viable
            }
            if set0 {
                if np0 >= 1.0 - ON_SQRT_EPSILON
                    && on_distance(q0, q1) <= ON_ZERO_TOLERANCE
                    && s1 > 0
                    && self.t[1] < actual.1
                {
                    // a micro segment would be left at the start: drop the first segment
                    #[cfg(test)]
                    tests::bump(&tests::MICRO_START);
                    self.t.remove(0);
                    self.points.remove(0);
                    s1 -= 1;
                    actual.0 = self.t[0];
                }
                self.t[0] = actual.0;
                self.points[0] = q0;
            }
            if set1 {
                self.t[1] = actual.1;
                self.points[1] = q1;
            }
        }
        if trim_last {
            let i = s1 as usize;
            let seg = (self.t[i], self.t[i + 1]);
            let q0 = self.points[i];
            let np = normalized_parameter_at(seg, actual.1);
            let q1 = line_point_at(q0, self.points[i + 1], np);
            let mut i = i;
            if np <= ON_SQRT_EPSILON && on_distance(q1, q0) <= ON_ZERO_TOLERANCE && s1 > 0 {
                // a micro segment would be left at the end: drop the last segment
                #[cfg(test)]
                tests::bump(&tests::MICRO_END);
                self.t.truncate(i + 1);
                self.points.truncate(i + 1);
                i -= 1;
                actual.1 = self.t[i + 1];
            }
            self.t[i + 1] = actual.1;
            self.points[i + 1] = q1;
        }
        self.t[0] = output.0;
        let last = self.t.len() - 1;
        self.t[last] = output.1;
        true
    }

    /// `Split(t)`: both sides, or None where openNURBS returns false.
    fn split(&self, t: f64) -> Option<(Polyline, Polyline)> {
        let n = self.t.len();
        let count = n as isize - 1; // segments
        if !(count >= 1 && self.t[0] < t && t < self.t[n - 1]) {
            return None;
        }
        let (at_break, segment_index) = self.parameter_search(t);
        if !((segment_index >= 1 || (!at_break && segment_index == 0)) && segment_index < count) {
            return None;
        }
        let i = segment_index as usize;
        #[cfg(test)]
        if at_break && !self.t.contains(&t) {
            tests::bump(&tests::SNAPPED);
        }
        let left_count = if at_break { i + 1 } else { i + 2 };
        let right_count = n - i;
        let mut left = Polyline {
            points: self.points[..left_count].to_vec(),
            t: self.t[..left_count].to_vec(),
        };
        if at_break {
            left.t[left_count - 1] = t; // reparameterize the last segment
        }
        let mut right = Polyline {
            points: self.points[n - right_count..].to_vec(),
            t: self.t[n - right_count..].to_vec(),
        };
        if at_break {
            right.t[0] = t; // reparameterize the first segment
        }
        let left_domain = (left.t[0], t);
        left.trim(left_domain);
        let right_domain = (t, right.t[right_count - 1]);
        right.trim(right_domain);
        Some((left, right))
    }
}

impl Piece<'_> {
    fn point(&self, t: f64) -> P3 {
        match &self.eval {
            Eval::Nurbs(n) => n.point(t),
            Eval::Polyline(p) => p.point(t),
        }
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
        #[cfg(test)]
        tests::VELOCITY_CALLS.with(|n| n.set(n.get() + 1));
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
fn newton(ca: &Piece, cb: &Piece, mut t_a: f64, mut t_b: f64) -> Option<(f64, f64, f64, P3)> {
    for _ in 0..20 {
        // A NaN parameter (an overflowed step on huge coordinates) leaves the domain
        // arithmetic of `PointAt`; the Python answers such a pair.
        if t_a.is_nan() || t_b.is_nan() {
            return None;
        }
        let (pa, pb) = (ca.point(t_a), cb.point(t_b));
        let (fx, fy, fz) = (pa[0] - pb[0], pa[1] - pb[1], pa[2] - pb[2]);
        let gap = (fx * fx + fy * fy + fz * fz).sqrt();
        if gap <= 1e-14 {
            return Some((t_a, t_b, gap, pa));
        }
        let (va, vb) = (ca.velocity(t_a), cb.velocity(t_b));
        let Some((dta, dtb)) = solve2(va, vb, [fx, fy, fz]) else {
            break;
        };
        let next_a = clamp(t_a - dta, ca.t0, ca.t1);
        let next_b = clamp(t_b - dtb, cb.t0, cb.t1);
        if next_a == t_a && next_b == t_b {
            break; // fixed point: the remaining iterations would repeat it
        }
        (t_a, t_b) = (next_a, next_b);
    }
    if t_a.is_nan() || t_b.is_nan() {
        return None;
    }
    let (pa, pb) = (ca.point(t_a), cb.point(t_b));
    let gap = py_dist(pa, pb);
    let mid = [
        (pa[0] + pb[0]) / 2.0,
        (pa[1] + pb[1]) / 2.0,
        (pa[2] + pb[2]) / 2.0,
    ];
    Some((t_a, t_b, gap, mid))
}

/// `_base_case`: `Some(None)` when the pair does not meet, `None` when the Python must
/// answer (see `newton`).
fn base_case(ca: &Piece, cb: &Piece, tol: f64) -> Option<Option<Hit>> {
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
    let (t_a, t_b, gap, point) = newton(ca, cb, t_a, t_b)?;
    if gap > py_max(tol, 1e-9) {
        return Some(None);
    }
    Some(Some(Hit { point, t_a, t_b }))
}

/// `_boxes_overlap`.
fn boxes_overlap(a: (P3, P3), b: (P3, P3), tol: f64) -> bool {
    (0..3).all(|i| a.0[i] - tol <= b.1[i] && b.0[i] - tol <= a.1[i])
}

/// `_subdivide`; `None` where the Python would reach an operation this port does not
/// cover (an invalid sub-curve), so the caller runs the Python.
fn subdivide(
    ca: &Shape,
    cb: &Shape,
    tol: f64,
    depth: usize,
    max_depth: usize,
    out: &mut Vec<Hit>,
) -> Option<()> {
    let (pa, pb) = (ca.piece()?, cb.piece()?);
    #[cfg(test)]
    tests::DEEPEST.with(|d| d.set(d.get().max(depth)));
    if !boxes_overlap(ca.bbox()?, cb.bbox()?, tol) {
        return Some(());
    }
    let a_lin = pa.is_linear_enough(tol);
    let b_lin = pb.is_linear_enough(tol);
    let forced = depth >= max_depth;
    if (a_lin && b_lin) || forced {
        out.extend(base_case(&pa, &pb, tol)?);
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
        out.extend(base_case(&pa, &pb, tol)?);
        return Some(());
    }
    let xs: Vec<&Shape> = match &a_parts {
        Some((l, r)) => vec![l, r],
        None => vec![ca],
    };
    let ys: Vec<&Shape> = match &b_parts {
        Some((l, r)) => vec![l, r],
        None => vec![cb],
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

/// `curve_curve`'s point hits: `_subdivide` then `_dedupe`.
fn point_hits(a: &Shape, b: &Shape, tol: f64, max_depth: usize) -> Option<Vec<Hit>> {
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
    // Every length is settled from the bounded counts before any slice is formed, so a
    // caller's count larger than its buffer never becomes a slice.
    let expected_knots = order.checked_add(cv_count).and_then(|n| n.checked_sub(2));
    let (Some(expected_knots), Some(cv_len)) = (expected_knots, cv_count.checked_mul(S)) else {
        return Err(ERR_INPUT);
    };
    if knot_count != expected_knots {
        return Err(ERR_INPUT);
    }
    let (Some(k), Some(c)) = (
        crate::checked_slice(knots, expected_knots),
        crate::checked_slice(cvs, cv_len),
    ) else {
        return Err(ERR_NULL);
    };
    curve_from(order, cv_count, rational == 1, k, c)
}

/// A NURBS curve from arrays of settled lengths: finite, with an evaluator and a domain.
fn curve_from(
    order: usize,
    cv_count: usize,
    rational: bool,
    k: &[f64],
    c: &[f64],
) -> Result<Curve, i32> {
    if !k.iter().chain(c).all(|v| v.is_finite()) {
        return Err(ERR_INPUT);
    }
    let curve = Curve {
        order,
        cv_count,
        rational,
        cvs: c.to_vec(),
        knots: k.to_vec(),
    };
    let piece = curve.piece().ok_or(ERR_INPUT)?;
    if piece.t0 >= piece.t1 {
        return Err(ERR_INPUT); // an empty domain: `IsValid` refuses it, and so does `Split`
    }
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
    if !tol.is_finite() || max_depth > MAX_DEPTH {
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
    write_hits(
        &Shape::Nurbs(a),
        &Shape::Nurbs(b),
        tol,
        max_depth,
        out_hits,
        capacity,
        out_count,
    )
}

/// Run the recursion and write its hits as `narwhal_pure_core_curve_curve_hits` does.
unsafe fn write_hits(
    a: &Shape,
    b: &Shape,
    tol: f64,
    max_depth: usize,
    out_hits: *mut f64,
    capacity: usize,
    out_count: *mut usize,
) -> i32 {
    let Some(hits) = point_hits(a, b, tol, max_depth) else {
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

/// One curve from its encoded record: a NURBS curve (checked as `read_curve` checks the
/// arrays) or a 3-D polyline with 2 to `MAX_CVS` points, strictly increasing parameters
/// and every value `ON_IS_VALID`. Anything else is `ERR_INPUT`, for the Python.
fn shape_from_record(data: &[u8]) -> Result<Shape, i32> {
    if let Some(r) = crate::nurbs_record::read(data) {
        if !(2..=MAX_ORDER).contains(&r.order) || r.cv_count > MAX_CVS {
            return Err(ERR_INPUT);
        }
        let (mut knots, mut cvs) = (vec![0.0; r.knot_count], vec![0.0; r.cv_doubles()]);
        r.write(&mut knots, &mut cvs);
        return curve_from(r.order, r.cv_count, r.rational, &knots, &cvs).map(Shape::Nurbs);
    }
    polyline_from_record(data)
        .map(Shape::Polyline)
        .ok_or(ERR_INPUT)
}

/// A 3-D polyline from its encoded record: 2 to `MAX_CVS` points, strictly increasing
/// parameters and every value `ON_IS_VALID`; None otherwise.
pub(crate) fn polyline_from_record(data: &[u8]) -> Option<Polyline> {
    let r = crate::nurbs_record::read_polyline(data)?;
    if !(2..=MAX_CVS).contains(&r.point_count) {
        return None;
    }
    let (points, t) = r.arrays();
    let valid = points.iter().flatten().chain(&t).all(|&v| on_is_valid(v));
    (valid && t.windows(2).all(|w| w[0] < w[1])).then_some(Polyline { points, t })
}

/// `narwhal_pure_core_curve_curve_hits` for two curves given as encoded records
/// (`base64.b64decode(curve.Encode()["data"])`), each an `ON_NurbsCurve` or an
/// `ON_PolylineCurve`: the only way to hand Rust a polyline's parameters, which rhino3dm
/// does not expose. A record of any other class, or one `shape_from_record` refuses, is
/// `ERR_INPUT`, and so is anything the recursion cannot answer: the Python then answers.
///
/// # Safety
/// `a`/`b` reference `a_len`/`b_len` readable bytes, `out_hits` `capacity * 5` writable
/// values and `out_count` one writable `usize`.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_curve_curve_record_hits(
    a: *const u8,
    a_len: usize,
    b: *const u8,
    b_len: usize,
    tol: f64,
    max_depth: usize,
    out_hits: *mut f64,
    capacity: usize,
    out_count: *mut usize,
) -> i32 {
    if out_count.is_null() || !(out_count as usize).is_multiple_of(std::mem::align_of::<usize>()) {
        return ERR_NULL;
    }
    *out_count = 0;
    if !tol.is_finite() || max_depth > MAX_DEPTH {
        return ERR_INPUT;
    }
    let (Some(a), Some(b)) = (
        crate::checked_slice(a, a_len),
        crate::checked_slice(b, b_len),
    ) else {
        return ERR_NULL;
    };
    let (a, b) = match (shape_from_record(a), shape_from_record(b)) {
        (Ok(a), Ok(b)) => (a, b),
        (Err(code), _) | (_, Err(code)) => return code,
    };
    write_hits(&a, &b, tol, max_depth, out_hits, capacity, out_count)
}

#[cfg(test)]
#[path = "curvex_polyline_cases.rs"]
mod polyline_cases;

#[cfg(test)]
/// An `ON_PolylineCurve` record of `points` and `params`, as `curve.Encode()` frames it.
pub(crate) fn polyline_record(points: &[P3], params: &[f64]) -> Vec<u8> {
    let mut payload = vec![0x10];
    payload.extend((points.len() as i32).to_le_bytes());
    payload.extend(points.iter().flatten().flat_map(|v| v.to_le_bytes()));
    payload.extend((params.len() as i32).to_le_bytes());
    payload.extend(params.iter().flat_map(|v| v.to_le_bytes()));
    payload.extend(3i32.to_le_bytes());
    // the framing of a real record, around this payload (nurbs_record checks the CRCs)
    let real = tests::unhex(polyline_cases::CASES[0].record);
    let crc = |b: &[u8]| {
        let mut c = !0u32;
        for &x in b {
            c ^= x as u32;
            for _ in 0..8 {
                c = if c & 1 != 0 {
                    0xEDB8_8320 ^ (c >> 1)
                } else {
                    c >> 1
                };
            }
        }
        !c
    };
    let chunk = |tcode: u32, body: &[u8], with_crc: bool| {
        let mut out = tcode.to_le_bytes().to_vec();
        out.extend(((body.len() + 4 * usize::from(with_crc)) as i64).to_le_bytes());
        out.extend(body);
        if with_crc {
            out.extend(crc(body).to_le_bytes());
        }
        out
    };
    let mut inner = real[12..44].to_vec(); // the class-id chunk
    inner.extend(chunk(0x0002_FFFC, &payload, true));
    inner.extend(real[real.len() - 12..].iter());
    chunk(0x0002_7FFA, &inner, false)
}

#[cfg(test)]
pub(crate) mod tests {
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
        let hits = point_hits(&Shape::Nurbs(a), &Shape::Nurbs(b), 1e-6, 40).unwrap();
        // y = 12 t (1 - t) (1 - 2 t) meets y = 0.5 twice
        assert_eq!(hits.len(), 2, "{hits:?}");
        for h in &hits {
            assert!((h.point[1] - 0.5).abs() < 1e-9);
        }
        assert!(hits.windows(2).all(|w| w[0].t_a <= w[1].t_a));
    }

    /// A small deterministic generator for the malformed-input sweep.
    struct Lcg(u64);

    impl Lcg {
        fn next(&mut self) -> u64 {
            self.0 = self
                .0
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            self.0 >> 11
        }

        fn below(&mut self, n: u64) -> usize {
            (self.next() % n) as usize
        }

        fn unit(&mut self) -> f64 {
            self.next() as f64 / (1u64 << 53) as f64
        }

        /// A value from the awkward ones: zeros, repeats, negatives, huge and tiny.
        fn awkward(&mut self) -> f64 {
            match self.below(8) {
                0 => 0.0,
                1 => -1.0,
                2 => 1.0,
                3 => 1e300 * (self.unit() - 0.5),
                4 => 1e-300 * self.unit(),
                _ => 10.0 * (self.unit() - 0.5),
            }
        }
    }

    /// One curve's ABI arguments: order, knots, control points (stride 4), rational.
    fn malformed_curve(rng: &mut Lcg) -> (usize, Vec<f64>, Vec<f64>, u8) {
        let plausible = rng.below(2) == 0; // right counts, sorted knots, positive weights
        let order = if plausible {
            2 + rng.below(4)
        } else {
            1 + rng.below(6)
        };
        let cv_count = if plausible {
            order + rng.below(4)
        } else {
            rng.below(8)
        };
        let mut knot_count = (order + cv_count).saturating_sub(2);
        if !plausible && rng.below(4) == 0 {
            knot_count = rng.below(12); // a count that disagrees with the curve
        }
        let mut knots: Vec<f64> = (0..knot_count).map(|_| rng.awkward()).collect();
        if plausible || rng.below(2) == 0 {
            knots.sort_by(f64::total_cmp); // nondecreasing, often with a degenerate domain
        }
        let mut cvs: Vec<f64> = (0..cv_count * S).map(|_| rng.awkward()).collect();
        if plausible || rng.below(2) == 0 {
            for w in cvs.iter_mut().skip(3).step_by(S) {
                *w = 1.0;
            }
        }
        (order, knots, cvs, rng.below(3) as u8)
    }

    #[test]
    fn malformed_abi_input_is_refused_without_a_panic() {
        let mut rng = Lcg(0x5eed);
        let mut out = vec![0.0; 5 * 64];
        let mut answered = 0;
        for _ in 0..200000 {
            let (ao, ak, ac, ar) = malformed_curve(&mut rng);
            let (bo, bk, bc, br) = malformed_curve(&mut rng);
            let tol = [1e-6, 0.0, -1.0, 1e300, 0.5][rng.below(5)];
            let depth = rng.below(10);
            let mut count = 0;
            let code = unsafe {
                narwhal_pure_core_curve_curve_hits(
                    ao,
                    ak.as_ptr(),
                    ak.len(),
                    ac.as_ptr(),
                    ac.len() / S,
                    ar,
                    bo,
                    bk.as_ptr(),
                    bk.len(),
                    bc.as_ptr(),
                    bc.len() / S,
                    br,
                    tol,
                    depth,
                    out.as_mut_ptr(),
                    64,
                    &mut count,
                )
            };
            assert!([0, ERR_INPUT, ERR_SIZE].contains(&code), "{code}");
            answered += usize::from(code == 0);
        }
        assert!(
            answered > 5000,
            "the sweep reaches the recursion: {answered}"
        );
    }

    thread_local! {
        pub(super) static VELOCITY_CALLS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
        pub(super) static DEEPEST: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
        // polyline Split/Trim branches the SDK-captured cases reach
        pub(super) static TRIM_WHOLE: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
        pub(super) static MICRO_START: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
        pub(super) static MICRO_END: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
        pub(super) static SNAPPED: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
    }

    pub(super) fn bump(counter: &'static std::thread::LocalKey<std::cell::Cell<usize>>) {
        counter.with(|n| n.set(n.get() + 1));
    }

    fn line(a: P3, b: P3) -> Curve {
        Curve {
            order: 2,
            cv_count: 2,
            rational: false,
            cvs: vec![a[0], a[1], a[2], 1.0, b[0], b[1], b[2], 1.0],
            knots: vec![0.0, 1.0],
        }
    }

    /// `_newton` without its fixed-point exit, as `test_trim_split_once.py` keeps it.
    fn reference_newton(ca: &Piece, cb: &Piece, mut t_a: f64, mut t_b: f64) -> (f64, f64, f64, P3) {
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
        let mid = [
            (pa[0] + pb[0]) / 2.0,
            (pa[1] + pb[1]) / 2.0,
            (pa[2] + pb[2]) / 2.0,
        ];
        (t_a, t_b, py_dist(pa, pb), mid)
    }

    #[test]
    fn newton_stops_at_a_fixed_point_with_the_same_answer() {
        // The witness of `test_trim_split_once.py`: a skew pair of lines never meets, and
        // the iterate settles at a clamp after one step.
        let a = line([0.0, 0.0, 0.0], [10.0, 0.0, 0.0]);
        let b = line([5.0, 1.0, 0.0], [5.0, 11.0, 0.0]);
        let (pa, pb) = (a.piece().unwrap(), b.piece().unwrap());
        VELOCITY_CALLS.with(|n| n.set(0));
        let settled = newton(&pa, &pb, 0.5, 0.0).unwrap();
        let calls = VELOCITY_CALLS.with(|n| n.get());
        assert!(calls <= 4, "{calls} velocity evaluations");
        assert_eq!(settled, reference_newton(&pa, &pb, 0.5, 0.0));
        // The exit changes no answer: a wavy pair from many starts.
        let wave_a = cubic(&[
            [0.0, 0.0, 0.0],
            [2.0, 4.0, 0.0],
            [4.0, -4.0, 0.0],
            [6.0, 0.0, 0.0],
        ]);
        let wave_b = cubic(&[
            [0.0, 0.5, 0.0],
            [2.0, -3.0, 0.0],
            [4.0, 3.0, 0.0],
            [6.0, 0.5, 0.0],
        ]);
        let (wa, wb) = (wave_a.piece().unwrap(), wave_b.piece().unwrap());
        for i in 0..=5 {
            for j in 0..=5 {
                let (s, t) = (i as f64 / 5.0, j as f64 / 5.0);
                assert_eq!(
                    newton(&wa, &wb, s, t).unwrap(),
                    reference_newton(&wa, &wb, s, t)
                );
            }
        }
    }

    unsafe fn hits_of(
        a: &Curve,
        b: &Curve,
        tol: f64,
        depth: usize,
        out: &mut [f64],
    ) -> (i32, usize) {
        let mut count = 0;
        let code = narwhal_pure_core_curve_curve_hits(
            a.order,
            a.knots.as_ptr(),
            a.knots.len(),
            a.cvs.as_ptr(),
            a.cv_count,
            u8::from(a.rational),
            b.order,
            b.knots.as_ptr(),
            b.knots.len(),
            b.cvs.as_ptr(),
            b.cv_count,
            u8::from(b.rational),
            tol,
            depth,
            out.as_mut_ptr(),
            out.len() / 5,
            &mut count,
        );
        (code, count)
    }

    #[test]
    fn the_export_refuses_what_it_cannot_answer() {
        let a = line([0.0, -1.0, 0.0], [0.0, 1.0, 0.0]);
        let b = line([-1.0, 0.0, 0.0], [1.0, 0.0, 0.0]);
        let mut out = [0.0; 5];
        unsafe {
            assert_eq!(hits_of(&a, &b, 1e-6, 40, &mut out), (0, 1));
            assert_eq!(&out[..3], &[0.0, 0.0, 0.0]);
            assert_eq!(hits_of(&a, &b, 1e-6, MAX_DEPTH, &mut out), (0, 1));
            assert_eq!(hits_of(&a, &b, 1e-6, MAX_DEPTH + 1, &mut out).0, ERR_INPUT);
            assert_eq!(hits_of(&a, &b, f64::NAN, 40, &mut out).0, ERR_INPUT);
            assert_eq!(hits_of(&a, &b, f64::INFINITY, 40, &mut out).0, ERR_INPUT);
            // no room: the count comes back for a second call
            assert_eq!(hits_of(&a, &b, 1e-6, 40, &mut []), (ERR_SIZE, 1));
            let mut bad = |edit: &dyn Fn(&mut Curve)| {
                let mut c = a.clone();
                edit(&mut c);
                hits_of(&c, &b, 1e-6, 40, &mut out).0
            };
            assert_eq!(
                bad(&|c| c.knots = vec![1.0, 0.0]),
                ERR_INPUT,
                "decreasing knots"
            );
            assert_eq!(
                bad(&|c| c.knots = vec![1.0, 1.0]),
                ERR_INPUT,
                "an empty domain"
            );
            // Each guard on its own: an empty domain is refused on entry (a NaN
            // parameter on one would run the knot search off its end), and Newton hands
            // a NaN iterate back to the Python instead of evaluating it.
            let empty = read_curve(2, [1.0, 1.0].as_ptr(), 2, a.cvs.as_ptr(), 2, 0);
            assert_eq!(empty.err(), Some(ERR_INPUT));
            let (pa, pb) = (a.piece().unwrap(), b.piece().unwrap());
            assert!(newton(&pa, &pb, f64::NAN, 0.5).is_none());
            assert!(newton(&pa, &pb, 0.5, f64::NAN).is_none());
            assert_eq!(bad(&|c| c.knots[1] = f64::NAN), ERR_INPUT, "a NaN knot");
            assert_eq!(
                bad(&|c| c.cvs[0] = f64::INFINITY),
                ERR_INPUT,
                "an infinite point"
            );
            assert_eq!(bad(&|c| c.cvs[3] = 0.0), ERR_INPUT, "a zero weight");
            assert_eq!(bad(&|c| c.cvs[7] = -1.0), ERR_INPUT, "a negative weight");
            assert_eq!(bad(&|c| c.knots.push(2.0)), ERR_INPUT, "a knot too many");
            assert_eq!(bad(&|c| c.order = 1), ERR_INPUT, "order 1");
            assert_eq!(bad(&|c| c.order = 3), ERR_INPUT, "more order than points");
            assert_eq!(bad(&|c| c.order = MAX_ORDER + 1), ERR_INPUT, "order 33");
            // A count past its buffer is refused before any slice of the buffer is formed:
            // a misaligned pointer (never read) would refuse as ERR_NULL if it were sliced.
            let misaligned = (a.knots.as_ptr() as *const u8).wrapping_add(1) as *const f64;
            for (knot_count, cv_count) in
                [(1 << 40, 2), (usize::MAX, 2), (2, 1 << 40), (2, usize::MAX)]
            {
                let mut count = 0;
                let code = narwhal_pure_core_curve_curve_hits(
                    2,
                    misaligned,
                    knot_count,
                    misaligned,
                    cv_count,
                    0,
                    2,
                    b.knots.as_ptr(),
                    2,
                    b.cvs.as_ptr(),
                    2,
                    0,
                    1e-6,
                    40,
                    out.as_mut_ptr(),
                    1,
                    &mut count,
                );
                assert_eq!(code, ERR_INPUT, "counts {knot_count}, {cv_count}");
            }
            let mut count = 7;
            let nulls = narwhal_pure_core_curve_curve_hits(
                2,
                std::ptr::null(),
                2,
                std::ptr::null(),
                2,
                0,
                2,
                b.knots.as_ptr(),
                2,
                b.cvs.as_ptr(),
                2,
                0,
                1e-6,
                40,
                std::ptr::null_mut(),
                4,
                &mut count,
            );
            assert_eq!((nulls, count), (ERR_NULL, 0));
            let no_count = narwhal_pure_core_curve_curve_hits(
                2,
                a.knots.as_ptr(),
                2,
                a.cvs.as_ptr(),
                2,
                0,
                2,
                b.knots.as_ptr(),
                2,
                b.cvs.as_ptr(),
                2,
                0,
                1e-6,
                40,
                out.as_mut_ptr(),
                1,
                std::ptr::null_mut(),
            );
            assert_eq!(no_count, ERR_NULL);
            // a hit with nowhere to write it
            let mut count = 0;
            let no_out = narwhal_pure_core_curve_curve_hits(
                2,
                a.knots.as_ptr(),
                2,
                a.cvs.as_ptr(),
                2,
                0,
                2,
                b.knots.as_ptr(),
                2,
                b.cvs.as_ptr(),
                2,
                0,
                1e-6,
                40,
                std::ptr::null_mut(),
                1,
                &mut count,
            );
            assert_eq!(no_out, ERR_NULL);
        }
    }

    #[test]
    fn a_deep_recursion_fits_a_small_thread_stack() {
        // tol 0: a curved piece is straight enough only once its halves are too short to
        // bend in floating point, so crossing cubics recurse about as deep as halving
        // allows (40 levels). This debug build needs a 256 KiB thread for them;
        // test_native_intersect.py runs the same pair through the release library on a
        // 64 KiB thread.
        let worker = std::thread::Builder::new().stack_size(256 << 10).spawn(|| {
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
            let mut out = vec![0.0; 5 * 64];
            DEEPEST.with(|d| d.set(0));
            let (code, count) = unsafe { hits_of(&a, &b, 0.0, MAX_DEPTH, &mut out) };
            (code, count, DEEPEST.with(|d| d.get()))
        });
        let (code, count, deepest) = worker.unwrap().join().unwrap();
        assert_eq!((code, count), (0, 2));
        assert!(deepest >= 40, "{deepest} levels");
    }

    pub(crate) fn unhex(s: &str) -> Vec<u8> {
        (0..s.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
            .collect()
    }

    fn rows_of(p: &Polyline) -> Vec<[u64; 4]> {
        p.t.iter()
            .zip(&p.points)
            .map(|(t, q)| [t.to_bits(), q[0].to_bits(), q[1].to_bits(), q[2].to_bits()])
            .collect()
    }

    #[test]
    fn polyline_point_and_split_give_the_sdk_bits() {
        for c in [&TRIM_WHOLE, &MICRO_START, &MICRO_END, &SNAPPED] {
            c.with(|n| n.set(0));
        }
        let (mut points, mut splits, mut refused) = (0, 0, 0);
        for case in polyline_cases::CASES {
            let data = unhex(case.record);
            let Ok(Shape::Polyline(p)) = shape_from_record(&data) else {
                panic!("{}: the record is a valid polyline", case.name);
            };
            for &[t, x, y, z] in case.points {
                let q = p.point(f64::from_bits(t));
                assert_eq!(
                    q.map(f64::to_bits),
                    [x, y, z],
                    "{}: PointAt({})",
                    case.name,
                    f64::from_bits(t)
                );
                points += 1;
            }
            for (t, want) in case.splits {
                let got = p.split(f64::from_bits(*t));
                let got = got.as_ref().map(|(l, r)| (rows_of(l), rows_of(r)));
                let want = want.map(|(l, r)| (l.to_vec(), r.to_vec()));
                assert_eq!(got, want, "{}: Split({})", case.name, f64::from_bits(*t));
                splits += 1;
                refused += usize::from(want.is_none());
            }
        }
        assert_eq!((points, splits, refused), (177, 300, 98));
        // every Split/Trim branch the SDK cases were built to reach
        for (name, c) in [
            ("a whole-domain trim", &TRIM_WHOLE),
            ("a micro segment at the start", &MICRO_START),
            ("a micro segment at the end", &MICRO_END),
            ("a parameter snapped to a break", &SNAPPED),
        ] {
            assert!(c.with(|n| n.get()) > 0, "no case reaches {name}");
        }
    }

    unsafe fn record_hits(
        a: &[u8],
        b: &[u8],
        tol: f64,
        depth: usize,
        out: &mut [f64],
    ) -> (i32, usize) {
        let mut count = 0;
        let code = narwhal_pure_core_curve_curve_record_hits(
            a.as_ptr(),
            a.len(),
            b.as_ptr(),
            b.len(),
            tol,
            depth,
            out.as_mut_ptr(),
            out.len() / 5,
            &mut count,
        );
        (code, count)
    }

    #[test]
    fn the_record_export_answers_a_polyline_and_refuses_what_it_cannot() {
        let rect = polyline_record(
            &[
                [0.0, 0.0, 0.0],
                [4.0, 0.0, 0.0],
                [4.0, 2.0, 0.0],
                [0.0, 2.0, 0.0],
                [0.0, 0.0, 0.0],
            ],
            &[0.0, 1.0, 2.0, 3.0, 4.0],
        );
        let cross = polyline_record(&[[2.0, -1.0, 0.0], [2.0, 3.0, 0.0]], &[0.0, 1.0]);
        let mut out = [0.0; 5 * 4];
        unsafe {
            assert_eq!(record_hits(&rect, &cross, 1e-6, 40, &mut out), (0, 2));
            assert_eq!(&out[..5], &[2.0, 0.0, 0.0, 0.5, 0.25]);
            assert_eq!(&out[5..10], &[2.0, 2.0, 0.0, 2.5, 0.75]);
            assert_eq!(record_hits(&rect, &cross, 1e-6, 40, &mut []), (ERR_SIZE, 2));
            assert_eq!(
                record_hits(&rect, &cross, f64::NAN, 40, &mut out).0,
                ERR_INPUT
            );
            assert_eq!(
                record_hits(&rect, &cross, 1e-6, MAX_DEPTH + 1, &mut out).0,
                ERR_INPUT
            );
            let refused = |points: &[P3], params: &[f64]| {
                let bad = polyline_record(points, params);
                let mut out = [0.0; 5];
                record_hits(&bad, &cross, 1e-6, 40, &mut out).0
            };
            let (a, b) = ([0.0, 0.0, 0.0], [1.0, 0.0, 0.0]);
            assert_eq!(refused(&[a], &[0.0]), ERR_INPUT, "one point");
            assert_eq!(refused(&[a, b], &[1.0, 1.0]), ERR_INPUT, "equal parameters");
            assert_eq!(
                refused(&[a, b], &[1.0, 0.0]),
                ERR_INPUT,
                "decreasing parameters"
            );
            assert_eq!(
                refused(&[a, b], &[0.0, f64::NAN]),
                ERR_INPUT,
                "a NaN parameter"
            );
            assert_eq!(
                refused(&[[f64::INFINITY, 0.0, 0.0], b], &[0.0, 1.0]),
                ERR_INPUT,
                "infinite"
            );
            assert_eq!(
                refused(&[[ON_UNSET_VALUE, 0.0, 0.0], b], &[0.0, 1.0]),
                ERR_INPUT,
                "the unset value"
            );
            assert_eq!(
                refused(&[a, b], &[ON_UNSET_VALUE, 0.0]),
                ERR_INPUT,
                "an unset parameter"
            );
            let many: Vec<P3> = (0..=MAX_CVS).map(|i| [i as f64, 0.0, 0.0]).collect();
            let params: Vec<f64> = (0..=MAX_CVS).map(|i| i as f64).collect();
            assert_eq!(
                refused(&many, &params),
                ERR_INPUT,
                "more than MAX_CVS points"
            );
            assert_eq!(
                record_hits(b"not a record", &cross, 1e-6, 40, &mut out).0,
                ERR_INPUT
            );
            assert_eq!(record_hits(&[], &cross, 1e-6, 40, &mut out).0, ERR_INPUT);
            let mut count = 7;
            let nulls = narwhal_pure_core_curve_curve_record_hits(
                std::ptr::null(),
                rect.len(),
                cross.as_ptr(),
                cross.len(),
                1e-6,
                40,
                out.as_mut_ptr(),
                4,
                &mut count,
            );
            assert_eq!((nulls, count), (ERR_NULL, 0));
            let mut counts = [0usize; 2];
            let misaligned = (counts.as_mut_ptr() as *mut u8).add(1).cast::<usize>();
            let code = narwhal_pure_core_curve_curve_record_hits(
                rect.as_ptr(),
                rect.len(),
                cross.as_ptr(),
                cross.len(),
                1e-6,
                40,
                out.as_mut_ptr(),
                4,
                misaligned,
            );
            assert_eq!((code, counts), (ERR_NULL, [0, 0]));
            let no_out = narwhal_pure_core_curve_curve_record_hits(
                rect.as_ptr(),
                rect.len(),
                cross.as_ptr(),
                cross.len(),
                1e-6,
                40,
                std::ptr::null_mut(),
                4,
                &mut count,
            );
            assert_eq!(no_out, ERR_NULL);
        }
    }

    #[test]
    fn malformed_polylines_and_splits_never_panic() {
        // Release builds abort on a panic, so every index in Split and Trim must hold for
        // any accepted polyline and any parameter.
        let mut rng = Lcg(0x9017);
        let mut out = vec![0.0; 5 * 64];
        let mut answered = 0;
        for round in 0..20000 {
            let n = 2 + rng.below(6);
            let mut params = Vec::with_capacity(n);
            let mut t = rng.awkward();
            for _ in 0..n {
                params.push(t);
                t = match rng.below(4) {
                    0 => t.next_up(),
                    1 => t + 1e-9 * (1.0 + t.abs()),
                    _ => t + 1.0 + rng.unit() * 10.0,
                };
            }
            let points: Vec<P3> = (0..n)
                .map(|_| [rng.awkward(), rng.awkward(), rng.awkward()])
                .collect();
            let p = Polyline {
                points: points.clone(),
                t: params.clone(),
            };
            let (lo, hi) = (params[0], params[n - 1]);
            for _ in 0..8 {
                let s = match rng.below(6) {
                    0 => params[rng.below(n as u64)],
                    1 => params[rng.below(n as u64)].next_up(),
                    2 => params[rng.below(n as u64)].next_down(),
                    3 => [f64::NAN, f64::INFINITY, lo - 1.0, hi + 1.0][rng.below(4)],
                    _ => lo + (hi - lo) * rng.unit(),
                };
                if let Some((l, r)) = p.split(s) {
                    for side in [&l, &r] {
                        assert_eq!(side.t.len(), side.points.len());
                        assert!(side.t.len() >= 2);
                    }
                }
                let _ = p.point(s);
            }
            let other = if round % 2 == 0 {
                polyline_record(&[[0.0, -1.0, 0.0], [0.5, 1.0, 0.25]], &[0.0, 1.0])
            } else {
                polyline_record(&points, &params)
            };
            let (code, _) = unsafe {
                record_hits(
                    &polyline_record(&points, &params),
                    &other,
                    1e-6,
                    rng.below(12),
                    &mut out,
                )
            };
            assert!([0, ERR_INPUT, ERR_SIZE].contains(&code), "{code}");
            answered += usize::from(code == 0);
        }
        assert!(
            answered > 5000,
            "the sweep reaches the recursion: {answered}"
        );
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
