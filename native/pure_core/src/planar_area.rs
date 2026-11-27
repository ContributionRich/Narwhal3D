//! The existing quadrature policy for area and area centroid on an exact closed planar curve:
//! `analysis.planar_curve_area_centroid`, read from the curve's `Encode()` record.
//!
//! Area, AreaCentroid and the object details measure a closed planar curve with Green's
//! theorem on the exact NURBS. The plane is Newell's normal of 64 points on the curve
//! (`analysis.curve_plane`), and `analysis._green_moments` integrates
//! `A = 1/2 ∮ (x dy - y dx)`, `Mx = 1/2 ∮ x² dy` and `My = -1/2 ∮ y² dx` with 12- and
//! 24-point Gauss-Legendre per knot span; the answer is the 24-point one and the error the
//! difference. Python ran it through rhino3dm: 64 `PointAt` and 36 `DerivativeAt` calls per
//! knot span, about 0.67 ms a span (200 ms for a 300-span curve).
//!
//! `PointAt` and `DerivativeAt(t, 1)` are `nearest::Nurbs` (openNURBS `ON_NurbsCurve::Evaluate`
//! at eb92af3, the commit rhino3dm 8.35.0 pins, in its operation order). The Gauss-Legendre
//! nodes and weights are `arclength._gauss_legendre`'s Newton iteration from libm `cos`
//! starting points, as CPython's `math.cos` calls it. The rest is `analysis.py`'s arithmetic in
//! its order; `max` keeps the first argument on a tie and a knot `set` keeps the first of
//! equal values. Rust never fuses a multiply and an add, so the results are bit-identical to
//! the Python route's wherever rhino3dm does not fuse them either. The adapter
//! (`native_nearest.planar_area_centroid`) checks `IsClosed` and `IsPlanar` with rhino3dm and
//! admits only a qualified platform whose loaded rhino3dm reproduces the Python route on its
//! witness curves. Anything else is NOT_RECOGNISED and Python runs its own route.

use crate::curvex::{polyline_from_record, Polyline};
use crate::line_samples::{polycurve_from_record, PolyEval};
use crate::nearest::{region, separate, Nurbs};
use crate::nurbs_record;
use crate::offset::knot_vector_is_valid;

const ERR_NULL: i32 = -1;
/// Python runs `planar_curve_area_centroid` itself.
const NOT_RECOGNISED: i32 = 1;
/// Python returns None: no plane, or a region of no area.
const NO_REGION: i32 = 3;

/// `curve_plane`'s default `samples`.
const PLANE_SAMPLES: usize = 64;
/// The two Gauss-Legendre orders `planar_curve_area_centroid` compares.
const LOW_ORDER: usize = 12;
const HIGH_ORDER: usize = 24;
/// The most knots a record may carry here (36 evaluations per span).
const MAX_KNOTS: usize = 1 << 16;
/// The values the export writes: area, its error, the centroid and its error.
pub const RESULT_LEN: usize = 8;

type P3 = [f64; 3];

/// A refusal that sends the whole call to Python.
#[derive(Debug, PartialEq)]
struct Declined;

/// Python `max(a, b)`: `a` unless `b > a`.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// `analysis._dot`.
fn dot(a: P3, b: P3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

/// `analysis._cross`.
fn cross(a: P3, b: P3) -> P3 {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

/// `analysis._norm`.
fn norm(a: P3) -> f64 {
    dot(a, a).sqrt()
}

fn finite(p: P3) -> Result<P3, Declined> {
    if p.iter().all(|x| x.is_finite()) {
        Ok(p)
    } else {
        Err(Declined)
    }
}

/// `arclength._gauss_legendre(n)`: the nodes and weights of the n-point rule on [-1, 1].
fn gauss_legendre(n: usize) -> (Vec<f64>, Vec<f64>) {
    let mut nodes = vec![0.0; n];
    let mut weights = vec![0.0; n];
    let nf = n as f64;
    for i in 0..n {
        // CPython's `math.cos` is libm `cos`, as Rust's.
        let mut x = (std::f64::consts::PI * (i as f64 + 0.75) / (nf + 0.5)).cos();
        let mut dp = 0.0;
        for _ in 0..100 {
            let (mut p0, mut p1) = (1.0, 0.0);
            for k in 1..=n {
                let kf = k as f64;
                let next = ((2.0 * kf - 1.0) * x * p0 - (kf - 1.0) * p1) / kf;
                p1 = p0;
                p0 = next;
            }
            dp = nf * (x * p0 - p1) / (x * x - 1.0);
            let dx = p0 / dp;
            x -= dx;
            if dx.abs() <= 1e-15 {
                break;
            }
        }
        nodes[i] = x;
        weights[i] = 2.0 / ((1.0 - x * x) * dp * dp);
    }
    (nodes, weights)
}

/// The curve `curve_plane` samples: the Python object itself, whose `PointAt` is its own
/// class's `Evaluate`.
enum PlaneCurve<'a> {
    Nurbs(&'a Nurbs<'a>),
    Poly(PolyEval<'a>, (f64, f64)),
    Polyline(&'a Polyline),
}

impl PlaneCurve<'_> {
    /// `Domain()`.
    fn domain(&self) -> (f64, f64) {
        match self {
            PlaneCurve::Nurbs(c) => c.domain(),
            PlaneCurve::Poly(_, ends) => *ends,
            PlaneCurve::Polyline(p) => p.ends(),
        }
    }

    /// `PointAt(t)`.
    fn point(&self, t: f64) -> P3 {
        match self {
            PlaneCurve::Nurbs(c) => c.point(t),
            PlaneCurve::Poly(p, _) => p.point(t),
            PlaneCurve::Polyline(p) => p.point(t),
        }
    }
}

/// `analysis.curve_plane(curve)` after `IsPlanar`: `(origin, unit normal)`, or None.
fn curve_plane(curve: &PlaneCurve) -> Result<Option<(P3, P3)>, Declined> {
    let (t0, t1) = curve.domain();
    let mut pts = Vec::with_capacity(PLANE_SAMPLES);
    for i in 0..PLANE_SAMPLES {
        pts.push(finite(
            curve.point(t0 + (t1 - t0) * i as f64 / PLANE_SAMPLES as f64),
        )?);
    }
    let mut n = [0.0; 3];
    for (i, &p) in pts.iter().enumerate() {
        let q = pts[(i + 1) % PLANE_SAMPLES];
        n[0] += (p[1] - q[1]) * (p[2] + q[2]);
        n[1] += (p[2] - q[2]) * (p[0] + q[0]);
        n[2] += (p[0] - q[0]) * (p[1] + q[1]);
    }
    let ln = norm(n);
    if ln <= 1e-300 {
        return Ok(None);
    }
    Ok(Some((pts[0], [n[0] / ln, n[1] / ln, n[2] / ln])))
}

/// `arclength._interior_knots(curve)` of a NurbsCurve.
fn interior_knots(curve: &Nurbs) -> Vec<f64> {
    let (t0, t1) = curve.domain();
    let eps = (t1 - t0) * 1e-12;
    let (_, knots) = curve.degree_and_knots();
    // The knots are non-decreasing (`Nurbs::new` checks), so the sorted set keeps the first of
    // each run of equal values.
    let mut out: Vec<f64> = Vec::new();
    for &v in knots {
        if t0 + eps < v && v < t1 - eps && out.last() != Some(&v) {
            out.push(v);
        }
    }
    out
}

/// `analysis._green_moments(curve, origin, ex, ey, order)`: `(A, Mx, My)`.
fn green_moments(
    curve: &Nurbs,
    cuts: &[f64],
    origin: P3,
    ex: P3,
    ey: P3,
    rule: &(Vec<f64>, Vec<f64>),
) -> Result<(f64, f64, f64), Declined> {
    let (nodes, weights) = rule;
    let (mut a, mut mx, mut my) = (0.0, 0.0, 0.0);
    for w2 in cuts.windows(2) {
        let (lo, hi) = (w2[0], w2[1]);
        let (half, mid) = (0.5 * (hi - lo), 0.5 * (hi + lo));
        for (&xi, &w) in nodes.iter().zip(weights) {
            let t = mid + half * xi;
            let [p, dp] = curve.point_and_tangent(t);
            let (p, dp) = (finite(p)?, finite(dp)?);
            let r = [p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]];
            let (x, y) = (dot(r, ex), dot(r, ey));
            let (dx, dy) = (dot(dp, ex), dot(dp, ey));
            let ww = w * half;
            a += ww * 0.5 * (x * dy - y * dx);
            mx += ww * 0.5 * x * x * dy;
            my -= ww * 0.5 * y * y * dx;
        }
    }
    Ok((a, mx, my))
}

/// `planar_curve_area_centroid` after `IsClosed` and `IsPlanar`: `(area, area_err,
/// centroid, centroid_err)`, or None where Python returns None. `shape` is the curve the plane
/// is sampled on, `curve` its NURBS form (`curve.ToNurbsCurve()` unless it is a NurbsCurve).
fn area_centroid(
    shape: &PlaneCurve,
    curve: &Nurbs,
) -> Result<Option<(f64, f64, P3, P3)>, Declined> {
    let Some((origin, nrm)) = curve_plane(shape)? else {
        return Ok(None);
    };
    let seed = if nrm[0].abs() < 0.9 {
        [1.0, 0.0, 0.0]
    } else {
        [0.0, 1.0, 0.0]
    };
    let ex = cross(seed, nrm);
    let ex = [ex[0] / norm(ex), ex[1] / norm(ex), ex[2] / norm(ex)];
    let ey = cross(nrm, ex);
    let (t0, t1) = curve.domain();
    let mut cuts = vec![t0];
    cuts.extend(interior_knots(curve));
    cuts.push(t1);
    let solve = |order: usize| -> Result<Option<(f64, P3)>, Declined> {
        let (a, mx, my) = green_moments(curve, &cuts, origin, ex, ey, &gauss_legendre(order))?;
        if a.abs() <= 1e-300 {
            return Ok(None);
        }
        let (cx, cy) = (mx / a, my / a);
        let c = std::array::from_fn(|i| origin[i] + cx * ex[i] + cy * ey[i]);
        Ok(Some((a.abs(), c)))
    };
    let (Some(lo), Some(hi)) = (solve(LOW_ORDER)?, solve(HIGH_ORDER)?) else {
        return Ok(None);
    };
    let size = hi.0.sqrt();
    let floor = size * 1e-12;
    let area_err = py_max((hi.0 - lo.0).abs(), hi.0 * 1e-12);
    let c_err = std::array::from_fn(|i| py_max((hi.1[i] - lo.1[i]).abs(), floor));
    Ok(Some((hi.0, area_err, hi.1, c_err)))
}

fn decide(shape_record: &[u8], record: &[u8]) -> Result<Option<[f64; RESULT_LEN]>, Declined> {
    let rec = nurbs_record::read(record).ok_or(Declined)?;
    if rec.knot_count > MAX_KNOTS {
        return Err(Declined);
    }
    let mut knots = vec![0.0; rec.knot_count];
    let mut cvs = vec![0.0; rec.cv_doubles()];
    rec.write(&mut knots, &mut cvs);
    if !knots.iter().chain(&cvs).all(|v| v.is_finite())
        || !knot_vector_is_valid(rec.order, rec.cv_count, &knots)
    {
        return Err(Declined);
    }
    let curve = Nurbs::new(rec.order, &knots, &cvs, rec.rational).ok_or(Declined)?;
    // The curve itself: the NURBS record when no other is given, else a PolyCurve or a
    // PolylineCurve.
    let poly;
    let polyline;
    let shape = if shape_record.is_empty() {
        PlaneCurve::Nurbs(&curve)
    } else if let Some(p) = polycurve_from_record(shape_record) {
        poly = p;
        PlaneCurve::Poly(poly.eval().ok_or(Declined)?, poly.ends())
    } else if let Some(p) = polyline_from_record(shape_record) {
        polyline = p;
        PlaneCurve::Polyline(&polyline)
    } else {
        return Err(Declined);
    };
    let Some((area, area_err, c, c_err)) = area_centroid(&shape, &curve)? else {
        return Ok(None);
    };
    let out = [
        area, area_err, c[0], c[1], c[2], c_err[0], c_err[1], c_err[2],
    ];
    if out.iter().all(|v| v.is_finite()) {
        Ok(Some(out))
    } else {
        Err(Declined)
    }
}

/// `analysis.planar_curve_area_centroid(curve)` for a closed, planar curve (the caller checks
/// `IsClosed` and `IsPlanar`). `record` is the `Encode()` record of its NURBS form: the curve
/// itself when it is a `NurbsCurve`, with `shape_len` 0, else `curve.ToNurbsCurve()`, with the
/// curve's own `PolyCurve` or `PolylineCurve` record in `shape`, on which the plane is
/// sampled.
///
/// Returns 0 with `area, area_err, cx, cy, cz, cx_err, cy_err, cz_err` in `out`; NO_REGION (3)
/// where Python returns None (no plane, or no area); NOT_RECOGNISED (1) when the record is not
/// a valid 3-D `ON_NurbsCurve`, `shape` is not a polycurve or polyline this crate evaluates,
/// there are more than 2^16 knots, or a value is not finite, so Python runs; ERR_NULL (-1) for a null, misaligned or overlapping pointer. `out` is written only on
/// 0.
///
/// # Safety
/// `shape` is readable for `shape_len` bytes, `record` for `record_len` bytes and `out`
/// writable for 8 doubles.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_planar_area_centroid(
    shape: *const u8,
    shape_len: usize,
    record: *const u8,
    record_len: usize,
    out: *mut f64,
) -> i32 {
    let ranges = (
        region(shape, shape_len),
        region(record, record_len),
        region(out.cast_const(), RESULT_LEN),
    );
    let (Some(sr), Some(rr), Some(or)) = ranges else {
        return ERR_NULL;
    };
    if out.is_null() || !separate(&[sr, rr], &[or]) {
        return ERR_NULL;
    }
    let (Some(shape_data), Some(data)) = (
        (unsafe { crate::checked_slice(shape, shape_len) }),
        (unsafe { crate::checked_slice(record, record_len) }),
    ) else {
        return ERR_NULL;
    };
    match decide(shape_data, data) {
        Ok(Some(values)) => {
            let Some(slot) = (unsafe { crate::checked_slice_mut(out, RESULT_LEN) }) else {
                return ERR_NULL;
            };
            slot.copy_from_slice(&values);
            0
        }
        Ok(None) => NO_REGION,
        Err(Declined) => NOT_RECOGNISED,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::nurbs_record::nurbs_record;

    /// A closed degree-1 polygon: the square [0, 2] x [0, 3] in the plane z = 1.
    fn rectangle() -> Vec<u8> {
        let pts = [
            [0.0, 0.0, 1.0],
            [2.0, 0.0, 1.0],
            [2.0, 3.0, 1.0],
            [0.0, 3.0, 1.0],
            [0.0, 0.0, 1.0],
        ];
        nurbs_record(2, &[0.0, 1.0, 2.0, 3.0, 4.0], &pts)
    }

    unsafe fn call(record: &[u8]) -> (i32, [f64; RESULT_LEN]) {
        let mut out = [-7.0; RESULT_LEN];
        let code = unsafe {
            narwhal_pure_core_planar_area_centroid(
                std::ptr::null(),
                0,
                record.as_ptr(),
                record.len(),
                out.as_mut_ptr(),
            )
        };
        (code, out)
    }

    #[test]
    fn gauss_legendre_integrates_polynomials_exactly() {
        for n in [LOW_ORDER, HIGH_ORDER] {
            let (x, w) = gauss_legendre(n);
            assert!((w.iter().sum::<f64>() - 2.0).abs() < 1e-14);
            // ∫ x^(2n-2) over [-1, 1] = 2 / (2n - 1)
            let p = (2 * n - 2) as i32;
            let got: f64 = x.iter().zip(&w).map(|(x, w)| w * x.powi(p)).sum();
            assert!((got - 2.0 / (2 * n - 1) as f64).abs() < 1e-13, "{n}: {got}");
        }
    }

    #[test]
    fn a_rectangle_has_its_area_and_centre() {
        let (code, out) = unsafe { call(&rectangle()) };
        assert_eq!(code, 0);
        assert!((out[0] - 6.0).abs() < 1e-12, "{out:?}");
        assert!((out[2] - 1.0).abs() < 1e-12 && (out[3] - 1.5).abs() < 1e-12);
        assert!((out[4] - 1.0).abs() < 1e-12);
        assert!(out[1] < 1e-9 && out[5..].iter().all(|&e| e < 1e-9));
    }

    unsafe fn call_shape(shape: &[u8], record: &[u8]) -> (i32, [f64; RESULT_LEN]) {
        let mut out = [-7.0; RESULT_LEN];
        let code = unsafe {
            narwhal_pure_core_planar_area_centroid(
                shape.as_ptr(),
                shape.len(),
                record.as_ptr(),
                record.len(),
                out.as_mut_ptr(),
            )
        };
        (code, out)
    }

    #[test]
    fn a_polyline_or_polycurve_samples_its_plane_on_itself() {
        let corners = [
            [0.0, 0.0, 1.0],
            [2.0, 0.0, 1.0],
            [2.0, 3.0, 1.0],
            [0.0, 3.0, 1.0],
            [0.0, 0.0, 1.0],
        ];
        let polyline = crate::curvex::polyline_record(&corners, &[0.0, 1.0, 2.0, 3.0, 4.0]);
        let lines: Vec<Vec<u8>> = corners
            .windows(2)
            .enumerate()
            .map(|(i, w)| {
                let line = [w[0][0], w[0][1], w[0][2], w[1][0], w[1][1], w[1][2]];
                crate::nurbs_record::line_record(&line, (i as f64, i as f64 + 1.0))
            })
            .collect();
        let joined = crate::nurbs_record::polycurve_record(&[0.0, 1.0, 2.0, 3.0, 4.0], &lines);
        let nurbs = rectangle();
        let (code, alone) = unsafe { call(&nurbs) };
        assert_eq!(code, 0);
        for shape in [&polyline, &joined] {
            let (code, out) = unsafe { call_shape(shape, &nurbs) };
            assert_eq!(code, 0);
            for (a, b) in out.iter().zip(&alone).take(5) {
                assert!((a - b).abs() < 1e-12, "{out:?} {alone:?}");
            }
        }
        // A shape that is neither is not recognised; the NURBS record must be a NURBS curve.
        let (code, out) = unsafe { call_shape(b"not a record", &nurbs) };
        assert_eq!((code, out), (NOT_RECOGNISED, [-7.0; RESULT_LEN]));
        let (code, _) = unsafe { call_shape(&polyline, &polyline) };
        assert_eq!(code, NOT_RECOGNISED);
    }

    #[test]
    fn a_rational_circle_has_area_pi_r_squared() {
        let s = std::f64::consts::FRAC_1_SQRT_2;
        let (r, cx, cy) = (2.5, 1.0, -3.0);
        let ring = [
            (1.0, 0.0, 1.0),
            (1.0, 1.0, s),
            (0.0, 1.0, 1.0),
            (-1.0, 1.0, s),
            (-1.0, 0.0, 1.0),
            (-1.0, -1.0, s),
            (0.0, -1.0, 1.0),
            (1.0, -1.0, s),
            (1.0, 0.0, 1.0),
        ];
        let mut body = Vec::new();
        for &(x, y, w) in &ring {
            body.extend_from_slice(&[(cx + r * x) * w, (cy + r * y) * w, 0.0, w]);
        }
        let knots = [0.0, 0.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0];
        let curve = Nurbs::new(3, &knots, &body, true).unwrap();
        let (area, _, c, _) = area_centroid(&PlaneCurve::Nurbs(&curve), &curve)
            .unwrap()
            .unwrap();
        assert!(
            (area - std::f64::consts::PI * r * r).abs() < 1e-12,
            "{area}"
        );
        assert!((c[0] - cx).abs() < 1e-12 && (c[1] - cy).abs() < 1e-12);
    }

    #[test]
    fn a_line_has_no_plane_and_refusals_write_nothing() {
        let line = nurbs_record(2, &[0.0, 1.0], &[[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]]);
        let (code, out) = unsafe { call(&line) };
        assert_eq!(code, NO_REGION);
        assert_eq!(out, [-7.0; RESULT_LEN]);
        let (code, out) = unsafe { call(b"not a record") };
        assert_eq!(code, NOT_RECOGNISED);
        assert_eq!(out, [-7.0; RESULT_LEN]);
        let record = rectangle();
        let code = unsafe {
            narwhal_pure_core_planar_area_centroid(
                std::ptr::null(),
                0,
                record.as_ptr(),
                record.len(),
                std::ptr::null_mut(),
            )
        };
        assert_eq!(code, ERR_NULL);
        let mut buf = record.clone();
        buf.resize(record.len() + 128, 0);
        let base = buf.as_mut_ptr();
        let aligned = unsafe { base.add(base.align_offset(8)) };
        let code = unsafe {
            narwhal_pure_core_planar_area_centroid(
                std::ptr::null(),
                0,
                base,
                buf.len(),
                aligned.cast(),
            )
        };
        assert_eq!(code, ERR_NULL);
    }

    #[test]
    fn flipped_records_never_panic() {
        let record = rectangle();
        let mut seed = 0xD1B5_4A32_D192_ED03u64;
        for _ in 0..300 {
            let mut copy = record.clone();
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            let at = (seed as usize) % copy.len();
            copy[at] ^= 1 << (seed >> 61);
            let (code, _) = unsafe { call(&copy) };
            assert!([0, NOT_RECOGNISED, NO_REGION].contains(&code), "{code}");
        }
    }
}
