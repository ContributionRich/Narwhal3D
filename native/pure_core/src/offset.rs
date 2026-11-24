//! The smooth free-form curve offset of `offset._compact_offset`, for the Offset command
//! and its preview.
//!
//! A free-form `NurbsCurve` (degree >= 2) with no interior knot of full multiplicity, no
//! offset cusp and no kinked closed seam is offset by interpolating its TRUE offset
//! points, `C(t) + distance * unit(n x T(t))`, at 16, 24, 36, 54, 64, 81 or 122 uniform
//! parameters, and accepting the first interpolant whose sampled pointwise error is within
//! 80% of the tolerance at 256 and then at 4096 parameters (plus 17 inside every knot span
//! of the source and of the fit). Python ran this loop through rhino3dm, about 13,000
//! `PointAt` / `TangentAt` calls an offset; here it runs on the curve's `Encode()` record.
//!
//! rhino3dm's evaluators are openNURBS at eb92af3 (the commit rhino3dm 8.35.0 pins), ported
//! in their operation order:
//!
//! * `PointAt` is `ON_NurbsCurve::Evaluate` as `nearest::Nurbs` ports it;
//! * `TangentAt` is `ON_Curve::EvTangent` (opennurbs_curve.cpp): `Ev1Der`, then
//!   `ON_3dVector::Unitize` (opennurbs_point.cpp), which divides by `ON_Length3d`;
//! * `CurvatureAt` is `ON_Curve::EvCurvature`: `Ev2Der`, then `ON_EvCurvature`
//!   (opennurbs_math.cpp);
//! * `fit.Domain = ...` is `ON_NurbsCurve::SetDomain`, `IsValid` is
//!   `ON_NurbsCurve::IsValid` (opennurbs_nurbscurve.cpp, `ON_IsValidKnotVector` in
//!   opennurbs_knot.cpp) and `IsClosed` is `ON_NurbsCurve::IsClosed`: `IsPeriodic`
//!   (`ON_IsKnotVectorPeriodic`), else `ON_Curve::IsClosed`, with `ON_PointsAreCoincident`
//!   (opennurbs_math.cpp).
//!
//! The rest is `offset.py`'s own arithmetic with Python float semantics (`math.dist` is
//! CPython's `vector_norm`, `python_norm::dist3`), and the interpolation is the same
//! `interpolate::solve` / `solve_periodic` the `interpolate` module calls. Every operation
//! is one IEEE-754 multiply, add, subtract, divide or square root, and Rust never fuses
//! them, so the fit is bit-identical to the Python route's wherever rhino3dm does not fuse
//! them either. The adapter (`native_nearest.offset_fit`) admits only a qualified platform
//! whose loaded rhino3dm reproduces the Python route on its witness curves.
//!
//! Anything this does not decide exactly is NOT_RECOGNISED, and Python runs its own route
//! from the start: a boundary, a cusp or a kinked seam (the boundary-preserving fit), a
//! degenerate tangent (openNURBS then takes a parameter-tolerance branch), an evaluation
//! that fails, an interpolation the solver refuses, or a fit that is not valid. The answer
//! is a fitted approximation the host turns into a new curve; this never changes the source.

use crate::curvex::{on_length3d, ON_ZERO_TOLERANCE};
use crate::interpolate;
use crate::nearest::{region, separate, Nurbs};
use crate::nurbs_record;
use crate::python_norm::dist3;

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
/// Python runs `_compact_offset` itself.
const NOT_RECOGNISED: i32 = 1;
/// No smooth interpolant met the tolerance: `_compact_offset` returns None.
const NO_FIT: i32 = 2;

/// The interpolation counts of `_compact_offset`.
const COUNTS: [usize; 7] = [16, 24, 36, 54, 64, 81, 122];
/// The largest fit: a closed periodic cubic through 122 points has 125 control points
/// and 127 openNURBS knots.
pub const MAX_FIT_CVS: usize = 125;
pub const MAX_FIT_KNOTS: usize = 127;
/// Every span costs 17 checks per fit and 33 cusp-search speeds; a source with more spans
/// is refused by name.
pub const MAX_SPANS: usize = 1 << 14;
/// Fixed grid steps per span of `_cusp_parameters`.
const CUSP_STEPS: usize = 32;
/// Bisection steps of `_cusp_parameters`.
const CUSP_BISECTIONS: usize = 60;

/// openNURBS defines `ON_DBL_MIN` as `DBL_MIN` wherever float.h does.
const ON_DBL_MIN: f64 = f64::MIN_POSITIVE;
const ON_SQRT_EPSILON: f64 = 1.490116119385e-8;
const ON_RELATIVE_TOLERANCE: f64 = 2.27373675443232059478759765625e-13;
const ON_UNSET_POSITIVE_VALUE: f64 = 1.23432101234321e+308;
/// 2^1023, `ON_3dVector::Unitize`'s renormalisation factor.
const UNITIZE_SCALE: f64 = f64::from_bits(0x7FE0_0000_0000_0000);

type P3 = [f64; 3];

/// A refusal that sends the whole offset to Python.
#[derive(Debug, PartialEq)]
struct Declined;

/// `ON_3dVector::Unitize`; None where it returns false (and zeroes the vector).
fn on_unitize(v: P3) -> Option<P3> {
    let d = on_length3d(v[0], v[1], v[2]);
    if d.is_finite() {
        if d > ON_DBL_MIN {
            return Some([v[0] / d, v[1] / d, v[2] / d]);
        }
        if d > 0.0 {
            let tmp = [
                v[0] * UNITIZE_SCALE,
                v[1] * UNITIZE_SCALE,
                v[2] * UNITIZE_SCALE,
            ];
            let d = on_length3d(tmp[0], tmp[1], tmp[2]);
            if d.is_finite() && d > ON_DBL_MIN {
                return Some([tmp[0] / d, tmp[1] / d, tmp[2] / d]);
            }
        }
    }
    None
}

fn finite(p: P3) -> Result<P3, Declined> {
    if p.iter().all(|v| v.is_finite()) {
        Ok(p)
    } else {
        Err(Declined)
    }
}

/// `curve.PointAt(t)`.
fn point_at(c: &Nurbs, t: f64) -> Result<P3, Declined> {
    finite(c.point(t))
}

/// `curve.TangentAt(t)`: `ON_Curve::EvTangent` where `Ev1Der` unitizes. A zero first
/// derivative takes openNURBS's l'Hopital branch, which is not ported.
fn tangent_at(c: &Nurbs, t: f64) -> Result<P3, Declined> {
    let [_, d1] = c.point_and_tangent(t);
    on_unitize(finite(d1)?).ok_or(Declined)
}

/// `curve.CurvatureAt(t)`: `ON_EvCurvature(Ev2Der(t))`.
fn curvature_at(c: &Nurbs, t: f64) -> Result<P3, Declined> {
    let [_, d1, d2] = c.derivatives(t);
    let (d1, d2) = (finite(d1)?, finite(d2)?);
    let len = on_length3d(d1[0], d1[1], d1[2]);
    if len == 0.0 {
        return Ok([0.0; 3]);
    }
    // T = D1/d1 is `ON_3dVector::operator/`: a multiply by 1/d1.
    let r = 1.0 / len;
    let t = [d1[0] * r, d1[1] * r, d1[2] * r];
    let neg_d2_o_t = -d2[0] * t[0] + -d2[1] * t[1] + -d2[2] * t[2];
    let s = 1.0 / (len * len);
    Ok(std::array::from_fn(|i| s * (d2[i] + neg_d2_o_t * t[i])))
}

// `offset.py`'s helpers, with Python's operation order.

fn cross(a: P3, b: P3) -> P3 {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn dot(a: P3, b: P3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn unit(v: P3) -> P3 {
    let m = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
    if m < 1e-15 {
        return [0.0; 3];
    }
    [v[0] / m, v[1] / m, v[2] / m]
}

/// The source curve and the offset it is asked for.
struct Offset<'a> {
    curve: Nurbs<'a>,
    distance: f64,
    n: P3,
}

impl Offset<'_> {
    /// `_offset_point`.
    fn point(&self, t: f64) -> Result<P3, Declined> {
        let p = point_at(&self.curve, t)?;
        let d = unit(cross(self.n, tangent_at(&self.curve, t)?));
        Ok(std::array::from_fn(|i| p[i] + d[i] * self.distance))
    }

    /// `_offset_speed`.
    fn speed(&self, t: f64) -> Result<f64, Declined> {
        let normal = unit(cross(self.n, tangent_at(&self.curve, t)?));
        let k = curvature_at(&self.curve, t)?;
        let s = 1.0 - self.distance * dot(k, normal);
        if s.is_finite() {
            Ok(s)
        } else {
            Err(Declined)
        }
    }

    /// True when `_cusp_parameters` finds a cusp.
    fn has_cusp(&self, breaks: &[f64]) -> Result<bool, Declined> {
        for w in breaks.windows(2) {
            let (a, b) = (w[0], w[1]);
            let x = |i: usize| a + (b - a) * i as f64 / CUSP_STEPS as f64;
            let mut prev = self.speed(x(0))?;
            for i in 0..CUSP_STEPS {
                let next = self.speed(x(i + 1))?;
                if prev * next < 0.0 {
                    let (mut lo, mut hi) = (x(i), x(i + 1));
                    for _ in 0..CUSP_BISECTIONS {
                        let mid = 0.5 * (lo + hi);
                        if self.speed(mid)? * prev > 0.0 {
                            lo = mid;
                        } else {
                            hi = mid;
                        }
                    }
                    let cusp = 0.5 * (lo + hi);
                    if (cusp - a).min(b - cusp) > 1e-9 * (b - a) {
                        return Ok(true);
                    }
                }
                prev = next;
            }
        }
        Ok(false)
    }

    /// `_offset_correspondence_error` against `fit`, whose domain is the source's.
    fn error(&self, fit: &Nurbs, samples: usize) -> Result<f64, Declined> {
        let (t0, t1) = self.curve.domain();
        let mut params: Vec<f64> = (0..=samples)
            .map(|i| t0 + (t1 - t0) * i as f64 / samples as f64)
            .collect();
        for crv in [&self.curve, fit] {
            let breaks = breaks(crv.degree_and_knots().1, t0, t1);
            for w in breaks.windows(2) {
                let (a, b) = (w[0], w[1]);
                params.extend((0..=16).map(|i| a + (b - a) * i as f64 / 16.0));
            }
        }
        let mut worst = 0.0_f64;
        for t in params {
            let error = dist3(self.point(t)?, point_at(fit, t)?);
            if !error.is_finite() {
                return Ok(f64::INFINITY);
            }
            if error > worst {
                worst = error;
            }
        }
        Ok(worst)
    }
}

/// `sorted({t0, t1} | {k for k in knots if t0 < k < t1})`.
fn breaks(knots: &[f64], t0: f64, t1: f64) -> Vec<f64> {
    let mut out = vec![t0, t1];
    out.extend(knots.iter().copied().filter(|&k| t0 < k && k < t1));
    out.sort_by(f64::total_cmp);
    out.dedup();
    out
}

/// `ON_IS_VALID`.
fn on_is_valid(x: f64) -> bool {
    x > -ON_UNSET_POSITIVE_VALUE && x < ON_UNSET_POSITIVE_VALUE
}

/// `ON_PointsAreCoincident(dim, is_rat, a, b)`.
fn points_are_coincident(dim: usize, rational: bool, a: &[f64], b: &[f64]) -> bool {
    if rational {
        let (wa, wb) = (a[dim], b[dim]);
        if wa == 0.0 || wb == 0.0 {
            return wa == 0.0 && wb == 0.0 && points_are_coincident(dim, false, a, b);
        }
        (0..dim).all(|i| close(a[i] / wa, b[i] / wb))
    } else {
        (0..dim).all(|i| close(a[i], b[i]))
    }
}

fn close(a: f64, b: f64) -> bool {
    let d = (a - b).abs();
    d <= ON_ZERO_TOLERANCE || d <= (a.abs() + b.abs()) * ON_RELATIVE_TOLERANCE
}

/// `ON_IsKnotVectorPeriodic` on openNURBS knots (`cv_count + order - 2`).
fn knots_are_periodic(order: usize, cv_count: usize, knot: &[f64]) -> bool {
    if order < 2 || cv_count < order || order == 2 {
        return false;
    }
    if order <= 4 {
        if cv_count < order + 2 {
            return false;
        }
    } else if cv_count < 2 * order - 2 {
        return false;
    }
    let mut tol = (knot[order - 1] - knot[order - 3]).abs() * ON_SQRT_EPSILON;
    let span = (knot[cv_count - 1] - knot[order - 2]).abs() * ON_SQRT_EPSILON;
    if tol < span {
        tol = span;
    }
    let k1 = cv_count - order + 1;
    (0..2 * (order - 2))
        .all(|i| (knot[i + 1] - knot[i] + knot[k1 + i] - knot[k1 + i + 1]).abs() <= tol)
}

/// `ON_Interval::ParameterAt(x)` on an increasing interval.
fn parameter_at(t0: f64, t1: f64, x: f64) -> f64 {
    (1.0 - x) * t0 + x * t1
}

/// `ON_NurbsCurve::IsClosed` of a 3-D curve: periodic, else `ON_Curve::IsClosed`.
/// `cvs` are homogeneous `(x*w, y*w, z*w, w)`, `rational` whether the curve stores weights.
fn is_closed(c: &Nurbs, cvs: &[f64], rational: bool) -> Result<bool, Declined> {
    let (degree, knots) = c.degree_and_knots();
    let order = degree + 1;
    let count = cvs.len() / 4;
    if count < 4 {
        return Ok(false);
    }
    // `ON_NurbsCurve::IsPeriodic`: the last order-1 CVs repeat the first.
    if knots_are_periodic(order, count, knots) {
        let cv = |i: usize| -> [f64; 4] {
            let p = &cvs[4 * i..4 * i + 4];
            [p[0], p[1], p[2], p[3]]
        };
        let periodic = (0..order - 1).all(|j| {
            let (a, b) = (cv(order - 2 - j), cv(count - 1 - j));
            if rational {
                points_are_coincident(3, true, &a, &b)
            } else {
                points_are_coincident(3, false, &a[..3], &b[..3])
            }
        });
        if periodic {
            return Ok(true);
        }
    }
    let (t0, t1) = c.domain();
    let a = point_at(c, parameter_at(t0, t1, 0.0))?;
    let p = point_at(c, parameter_at(t0, t1, 1.0))?;
    if !points_are_coincident(3, false, &a, &p) {
        return Ok(false);
    }
    let b = point_at(c, parameter_at(t0, t1, 1.0 / 3.0))?;
    let q = point_at(c, parameter_at(t0, t1, 2.0 / 3.0))?;
    Ok(!points_are_coincident(3, false, &a, &b)
        && !points_are_coincident(3, false, &a, &q)
        && !points_are_coincident(3, false, &p, &b)
        && !points_are_coincident(3, false, &p, &q))
}

/// `ON_NurbsCurve::IsValid` of a non-rational 3-D curve.
fn is_valid(order: usize, cv_count: usize, knot: &[f64], cvs: &[P3]) -> bool {
    if order < 2 || cv_count < order || knot.len() != cv_count + order - 2 {
        return false;
    }
    // `ON_IsValidKnotVector`.
    if !knot.iter().all(|&k| on_is_valid(k))
        || knot[order - 2] >= knot[order - 1]
        || knot[cv_count - 2] >= knot[cv_count - 1]
        || !knot.windows(2).all(|w| w[1] >= w[0])
        || !(0..cv_count - 1).all(|i| knot[i + order - 1] > knot[i])
    {
        return false;
    }
    // `ON_ControlPointsAreValid`.
    cv_count >= 2 && cvs.iter().flatten().all(|&v| on_is_valid(v))
}

/// `ON_NurbsCurve::SetDomain(t0, t1)` on openNURBS knots; false leaves them unchanged.
fn set_domain(order: usize, cv_count: usize, knot: &mut [f64], t0: f64, t1: f64) -> bool {
    if order < 2 || cv_count < order || t0 >= t1 {
        return false;
    }
    let (k0, k1) = (knot[order - 2], knot[cv_count - 1]);
    if k0 == t0 && k1 == t1 {
        return true;
    }
    if k0 >= k1 {
        return false;
    }
    let d = (t1 - t0) / (k1 - k0);
    let km = 0.5 * (k0 + k1);
    for k in knot.iter_mut() {
        *k = if *k <= km {
            (*k - k0) * d + t0
        } else {
            (*k - k1) * d + t1
        };
    }
    true
}

/// A smooth offset fit: openNURBS knots, control points (weight 1) and the error.
#[derive(Debug, PartialEq)]
pub struct Fit {
    pub cvs: Vec<P3>,
    pub knots: Vec<f64>,
    pub deviation: f64,
}

/// `interpolate.interpolate_curve(pts, style="Uniform")` or
/// `interpolate_closed_curve(pts, "Uniform")`, then `fit.Domain = (t0, t1)`: the control
/// points, the knots and whether the fit is closed.
fn interpolant(
    pts: &[P3],
    closed: bool,
    t0: f64,
    t1: f64,
) -> Result<(Vec<P3>, Vec<f64>, bool), Declined> {
    let solved = if closed {
        interpolate::solve_periodic(pts, 0)
    } else {
        interpolate::solve(pts, 3, 0)
    };
    let (ctrl, full, degree) = solved.map_err(|_| Declined)?;
    let order = degree + 1;
    let count = ctrl.len();
    if degree != 3
        || full.len() != count + order
        || !full.iter().all(|k| k.is_finite())
        || !full.windows(2).all(|w| w[0] <= w[1])
        || !ctrl.iter().flatten().all(|v| v.is_finite())
    {
        return Err(Declined);
    }
    let mut knots = full[1..full.len() - 1].to_vec();
    if !is_valid(order, count, &knots, &ctrl) {
        return Err(Declined);
    }
    let cvs: Vec<f64> = ctrl.iter().flat_map(|p| [p[0], p[1], p[2], 1.0]).collect();
    let built_closed = {
        let fit = Nurbs::new(order, &knots, &cvs, false).ok_or(Declined)?;
        is_closed(&fit, &cvs, false)?
    };
    if closed && !built_closed {
        return Err(Declined);
    }
    set_domain(order, count, &mut knots, t0, t1);
    Ok((ctrl, knots, built_closed))
}

/// `_compact_offset` with no boundary: the accepted fit, None where no count meets
/// `tolerance * 0.8`, or Declined where Python must decide.
fn smooth_fit(
    off: &Offset,
    rational: bool,
    cvs: &[f64],
    tolerance: f64,
) -> Result<Option<Fit>, Declined> {
    let c = &off.curve;
    let (degree, knots) = c.degree_and_knots();
    if degree < 2 {
        return Err(Declined);
    }
    let (t0, t1) = c.domain();
    let interior: Vec<f64> = knots
        .iter()
        .copied()
        .filter(|&k| t0 < k && k < t1)
        .collect();
    // A boundary: an interior knot of multiplicity >= degree.
    if interior
        .iter()
        .any(|k| knots.iter().filter(|&&x| x == *k).count() >= degree)
    {
        return Err(Declined);
    }
    if off.has_cusp(&breaks(knots, t0, t1))? {
        return Err(Declined);
    }
    let closed = is_closed(c, cvs, rational)?;
    if closed && dist3(tangent_at(c, t0)?, tangent_at(c, t1)?) > 1e-8 {
        return Err(Declined);
    }
    let limit = tolerance * 0.8;
    for count in COUNTS {
        let pts = (0..count + usize::from(!closed))
            .map(|i| off.point(t0 + (t1 - t0) * i as f64 / count as f64))
            .collect::<Result<Vec<_>, _>>()?;
        let (ctrl, fit_knots, fit_closed) = interpolant(&pts, closed, t0, t1)?;
        if fit_closed != closed {
            continue;
        }
        let fit_cvs: Vec<f64> = ctrl.iter().flat_map(|p| [p[0], p[1], p[2], 1.0]).collect();
        let fit = Nurbs::new(4, &fit_knots, &fit_cvs, false).ok_or(Declined)?;
        let dev = off.error(&fit, 256)?;
        if !dev.is_finite() || dev > limit {
            continue;
        }
        let dev = off.error(&fit, 4096)?;
        if dev.is_finite() && dev <= limit {
            return Ok(Some(Fit {
                cvs: ctrl,
                knots: fit_knots,
                deviation: dev,
            }));
        }
    }
    Ok(None)
}

/// The decision for one encoded `NurbsCurve`: Ok(Some) a fit, Ok(None) no fit, or the
/// status code Python acts on.
fn decide(record: &[u8], distance: f64, n: P3, tolerance: f64) -> Result<Option<Fit>, i32> {
    let rec = nurbs_record::read(record).ok_or(NOT_RECOGNISED)?;
    if rec.knot_count > MAX_SPANS {
        return Err(ERR_SIZE);
    }
    let mut knots = vec![0.0; rec.knot_count];
    let mut cvs = vec![0.0; rec.cv_doubles()];
    rec.write(&mut knots, &mut cvs);
    if !knots.iter().chain(&cvs).all(|v| v.is_finite()) {
        return Err(NOT_RECOGNISED);
    }
    let curve = Nurbs::new(rec.order, &knots, &cvs, rec.rational).ok_or(NOT_RECOGNISED)?;
    let off = Offset { curve, distance, n };
    smooth_fit(&off, rec.rational, &cvs, tolerance).map_err(|Declined| NOT_RECOGNISED)
}

/// `offset._compact_offset(curve, distance, n, tolerance)` for the `NurbsCurve` encoded in
/// `record` (the bytes of `curve.Encode()["data"]`), `n` the unit plane normal.
///
/// Returns 0 with the fit in `out_cvs` (`x, y, z` per control point, weight 1),
/// `out_knots` (openNURBS knots, after `SetDomain` to the source domain), `out_counts`
/// (`[cv_count, knot_count]`) and `out_deviation`; NO_FIT (2) when `_compact_offset`
/// returns None; NOT_RECOGNISED (1) when Python must run it; or a negative error. Nothing
/// is written unless it returns 0.
///
/// # Safety
///
/// `record` must reference `record_len` readable bytes, `normal` 3 readable doubles,
/// `out_cvs` `cv_capacity` and `out_knots` `knot_capacity` writable doubles,
/// `out_counts` 2 writable `u64`s and `out_deviation` one writable double. An output that
/// overlaps an input or another output, or a null or misaligned pointer, is refused (-1).
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_offset_fit(
    record: *const u8,
    record_len: usize,
    distance: f64,
    normal: *const f64,
    tolerance: f64,
    out_cvs: *mut f64,
    cv_capacity: usize,
    out_knots: *mut f64,
    knot_capacity: usize,
    out_counts: *mut u64,
    out_deviation: *mut f64,
) -> i32 {
    if out_counts.is_null() || out_deviation.is_null() {
        return ERR_NULL;
    }
    if cv_capacity < 3 * MAX_FIT_CVS || knot_capacity < MAX_FIT_KNOTS {
        return ERR_SIZE;
    }
    let ranges = (
        region(record, record_len),
        region(normal, 3),
        region(out_cvs.cast_const(), cv_capacity),
        region(out_knots.cast_const(), knot_capacity),
        region(out_counts.cast_const(), 2),
        region(out_deviation.cast_const(), 1),
    );
    let (Some(rr), Some(nr), Some(cr), Some(kr), Some(sr), Some(dr)) = ranges else {
        return ERR_NULL;
    };
    if !separate(&[rr, nr], &[cr, kr, sr, dr]) {
        return ERR_NULL;
    }
    if !distance.is_finite() || distance.abs() < 1e-15 || tolerance.is_nan() {
        return ERR_INPUT;
    }
    let (Some(data), Some(n)) = (
        (unsafe { crate::checked_slice(record, record_len) }),
        (unsafe { crate::checked_slice(normal, 3) }),
    ) else {
        return ERR_NULL;
    };
    let n = [n[0], n[1], n[2]];
    if !n.iter().all(|v| v.is_finite()) {
        return ERR_INPUT;
    }
    let fit = match decide(data, distance, n, tolerance) {
        Ok(Some(fit)) => fit,
        Ok(None) => return NO_FIT,
        Err(code) => return code,
    };
    let (Some(cv_out), Some(knot_out)) = (
        (unsafe { crate::checked_slice_mut(out_cvs, 3 * fit.cvs.len()) }),
        (unsafe { crate::checked_slice_mut(out_knots, fit.knots.len()) }),
    ) else {
        return ERR_NULL;
    };
    for (slot, p) in cv_out.chunks_exact_mut(3).zip(&fit.cvs) {
        slot.copy_from_slice(p);
    }
    knot_out.copy_from_slice(&fit.knots);
    unsafe {
        *out_counts = fit.cvs.len() as u64;
        *out_counts.add(1) = fit.knots.len() as u64;
        *out_deviation = fit.deviation;
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unitize_divides_by_the_opennurbs_length() {
        assert_eq!(on_unitize([3.0, 4.0, 0.0]), Some([0.6, 0.8, 0.0]));
        assert_eq!(on_unitize([0.0; 3]), None);
        assert_eq!(on_unitize([f64::INFINITY, 0.0, 0.0]), None);
        // A subnormal vector is renormalised by 2^1023 first.
        let tiny = on_unitize([1e-310, 0.0, 0.0]).unwrap();
        assert_eq!(tiny, [1.0, 0.0, 0.0]);
    }

    #[test]
    fn curvature_of_a_circle_points_to_its_centre() {
        // D1 = (0, 2, 0), D2 = (-4, 0, 0): radius 1, curvature (-1, 0, 0).
        let d1: P3 = [0.0, 2.0, 0.0];
        let d2: P3 = [-4.0, 0.0, 0.0];
        let len = on_length3d(d1[0], d1[1], d1[2]);
        let r = 1.0 / len;
        let t = [d1[0] * r, d1[1] * r, d1[2] * r];
        let neg = -d2[0] * t[0] + -d2[1] * t[1] + -d2[2] * t[2];
        let s = 1.0 / (len * len);
        let k: P3 = std::array::from_fn(|i| s * (d2[i] + neg * t[i]));
        assert_eq!(k, [-1.0, 0.0, 0.0]);
    }

    #[test]
    fn set_domain_maps_each_half_from_its_own_end() {
        let mut k = vec![0.0, 0.0, 0.0, 0.25, 0.5, 1.0, 1.0, 1.0];
        assert!(set_domain(4, 6, &mut k, 2.0, 6.0));
        assert_eq!(k, vec![2.0, 2.0, 2.0, 3.0, 4.0, 6.0, 6.0, 6.0]);
        assert!(!set_domain(4, 6, &mut k, 6.0, 2.0));
        assert_eq!(k[0], 2.0);
    }

    #[test]
    fn periodic_knots_need_matching_end_spacing() {
        let periodic: Vec<f64> = (-2..8).map(f64::from).collect();
        assert!(knots_are_periodic(4, 8, &periodic));
        let clamped = [0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 3.0, 3.0];
        assert!(!knots_are_periodic(4, 6, &clamped));
    }

    #[test]
    fn validity_checks_the_knot_vector_and_points() {
        let k = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0];
        let cvs = [[0.0; 3], [1.0, 0.0, 0.0], [2.0, 1.0, 0.0], [3.0, 0.0, 0.0]];
        assert!(is_valid(4, 4, &k, &cvs));
        let mut bad = cvs;
        bad[1][0] = ON_UNSET_POSITIVE_VALUE;
        assert!(!is_valid(4, 4, &k, &bad));
        assert!(!is_valid(4, 4, &[0.0, 0.0, 1.0, 1.0, 1.0, 1.0], &cvs));
    }

    fn record_of(order: usize, knots: &[f64], cvs: &[P3]) -> Vec<u8> {
        nurbs_record::nurbs_record(order, knots, cvs)
    }

    /// A smooth open cubic: x runs 0..110, y a gentle wave.
    fn wave() -> (Vec<f64>, Vec<P3>) {
        let cvs: Vec<P3> = (0..12)
            .map(|i| [i as f64 * 10.0, (i as f64 * 0.6).sin() * 8.0, 0.0])
            .collect();
        let knots: Vec<f64> = [0.0, 0.0]
            .into_iter()
            .chain((0..=9).map(f64::from))
            .chain([9.0, 9.0])
            .collect();
        (knots, cvs)
    }

    #[test]
    fn a_smooth_cubic_is_fitted_within_tolerance() {
        let (knots, cvs) = wave();
        let record = record_of(4, &knots, &cvs);
        let fit = decide(&record, 1.0, [0.0, 0.0, 1.0], 0.01)
            .unwrap()
            .unwrap();
        assert!(fit.deviation <= 0.008, "{}", fit.deviation);
        assert_eq!(fit.knots.len(), fit.cvs.len() + 2);
        assert_eq!(fit.knots[2], 0.0);
        assert_eq!(fit.knots[fit.cvs.len() - 1], 9.0);
        // An independent oracle: the true offset (from the source's own first derivative,
        // not `tangent_at`) against the fit at 16384 matching parameters.
        let src_cvs: Vec<f64> = cvs.iter().flat_map(|p| [p[0], p[1], p[2], 1.0]).collect();
        let source = Nurbs::new(4, &knots, &src_cvs, false).unwrap();
        let fit_cvs: Vec<f64> = fit
            .cvs
            .iter()
            .flat_map(|p| [p[0], p[1], p[2], 1.0])
            .collect();
        let curve = Nurbs::new(4, &fit.knots, &fit_cvs, false).unwrap();
        let worst = (0..=16384)
            .map(|i| {
                let t = 9.0 * i as f64 / 16384.0;
                let [p, d] = source.point_and_tangent(t);
                let len = (d[0] * d[0] + d[1] * d[1]).sqrt();
                let q = curve.point(t);
                ((p[0] - d[1] / len - q[0]).powi(2) + (p[1] + d[0] / len - q[1]).powi(2)).sqrt()
            })
            .fold(0.0, f64::max);
        assert!(worst <= 0.01, "{worst}");
    }

    #[test]
    fn an_unreachable_tolerance_is_no_fit() {
        let (knots, cvs) = wave();
        let record = record_of(4, &knots, &cvs);
        assert_eq!(decide(&record, 1.0, [0.0, 0.0, 1.0], 1e-300), Ok(None));
    }

    #[test]
    fn boundaries_and_cusps_go_to_python() {
        let (mut knots, cvs) = wave();
        // A triple interior knot at 4 (a C0 corner).
        knots[5..8].copy_from_slice(&[4.0; 3]);
        let record = record_of(4, &knots, &cvs);
        assert_eq!(
            decide(&record, 1.0, [0.0, 0.0, 1.0], 0.01),
            Err(NOT_RECOGNISED)
        );
        // Offsetting 200 passes the radius of curvature at the wave's crests.
        let (knots, cvs) = wave();
        let record = record_of(4, &knots, &cvs);
        assert_eq!(
            decide(&record, 200.0, [0.0, 0.0, 1.0], 0.01),
            Err(NOT_RECOGNISED)
        );
    }

    #[test]
    fn the_export_refuses_bad_calls_before_writing() {
        let (knots, cvs) = wave();
        let record = record_of(4, &knots, &cvs);
        let n = [0.0, 0.0, 1.0];
        let mut out = vec![7.0; 3 * MAX_FIT_CVS];
        let mut k = vec![7.0; MAX_FIT_KNOTS];
        let mut counts = [9_u64; 2];
        let mut dev = 7.0;
        let call = |rec: &[u8],
                    d: f64,
                    n: &[f64; 3],
                    out: &mut [f64],
                    k: &mut [f64],
                    counts: &mut [u64; 2],
                    dev: &mut f64| unsafe {
            narwhal_pure_core_offset_fit(
                rec.as_ptr(),
                rec.len(),
                d,
                n.as_ptr(),
                0.01,
                out.as_mut_ptr(),
                out.len(),
                k.as_mut_ptr(),
                k.len(),
                counts.as_mut_ptr(),
                dev,
            )
        };
        assert_eq!(
            call(&record, 0.0, &n, &mut out, &mut k, &mut counts, &mut dev),
            ERR_INPUT
        );
        assert_eq!(
            call(
                &record,
                f64::NAN,
                &n,
                &mut out,
                &mut k,
                &mut counts,
                &mut dev
            ),
            ERR_INPUT
        );
        assert_eq!(
            call(
                &record,
                1.0,
                &[f64::NAN, 0.0, 1.0],
                &mut out,
                &mut k,
                &mut counts,
                &mut dev
            ),
            ERR_INPUT
        );
        assert_eq!(
            call(
                &record[..record.len() - 1],
                1.0,
                &n,
                &mut out,
                &mut k,
                &mut counts,
                &mut dev
            ),
            NOT_RECOGNISED
        );
        assert_eq!(
            call(
                &record,
                1.0,
                &n,
                &mut out[..10],
                &mut k,
                &mut counts,
                &mut dev
            ),
            ERR_SIZE
        );
        let mut shared = vec![7.0; 3 * MAX_FIT_CVS + MAX_FIT_KNOTS];
        let (cv_part, knot_part) = shared.split_at_mut(3 * MAX_FIT_CVS);
        let overlapping = unsafe {
            narwhal_pure_core_offset_fit(
                record.as_ptr(),
                record.len(),
                1.0,
                n.as_ptr(),
                0.01,
                cv_part.as_mut_ptr(),
                cv_part.len() + 1,
                knot_part.as_mut_ptr(),
                knot_part.len(),
                counts.as_mut_ptr(),
                &mut dev,
            )
        };
        assert_eq!(overlapping, ERR_NULL);
        assert!(shared.iter().all(|&v| v == 7.0));
        assert!(out.iter().chain(&k).all(|&v| v == 7.0) && counts == [9, 9] && dev == 7.0);
        assert_eq!(
            call(&record, 1.0, &n, &mut out, &mut k, &mut counts, &mut dev),
            0
        );
        assert_eq!(counts[1], counts[0] + 2);
        assert!(dev <= 0.008);
    }

    #[test]
    fn a_source_over_the_span_bound_is_refused_by_name() {
        let count = MAX_SPANS + 2;
        let cvs: Vec<P3> = (0..count)
            .map(|i| [i as f64, (i % 7) as f64, 0.0])
            .collect();
        let knots: Vec<f64> = (0..count + 2).map(|i| i as f64).collect();
        let record = record_of(4, &knots, &cvs);
        assert_eq!(decide(&record, 1.0, [0.0, 0.0, 1.0], 0.01), Err(ERR_SIZE));
    }

    #[test]
    fn malformed_records_never_abort() {
        let (knots, cvs) = wave();
        let record = record_of(4, &knots, &cvs);
        let mut state = 0x2026_1124_u64;
        for _ in 0..400 {
            let mut bytes = record.clone();
            for _ in 0..4 {
                state = state
                    .wrapping_mul(6364136223846793005)
                    .wrapping_add(1442695040888963407);
                let at = (state >> 33) as usize % bytes.len();
                bytes[at] ^= (state >> 20) as u8 | 1;
            }
            let _ = decide(&bytes, 1.0, [0.0, 0.0, 1.0], 0.01);
        }
    }
}
