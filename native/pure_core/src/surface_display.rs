//! The display grid lines of a NURBS surface along one direction: `bridge._surface_axis_params`,
//! read from the surface's `Encode()` record.
//!
//! The viewport draws a surface as a grid whose lines in each direction come from
//! `_surface_axis_params`. The segment count is sized from the length of the middle iso-curve
//! (`_iso_segments`, through `tessellate.curve_segments_for`), and the segments are laid down
//! with a line on every interior knot (`tessellate.distribute_params`). Where any of three
//! iso-curves turns by more than 5.5 degrees between neighbouring lines, the lines are placed by
//! the iso-curves' turn and chord needs instead (`_surface_turn_params`), and neighbouring
//! segments are bisected while they still turn too far (`_surface_turn_verify`). Python ran this
//! through rhino3dm: `PointAt`, `IsClosed`, `IsoCurve`, and `DerivativeAt` / `PointAt` on the
//! iso-curves, several hundred calls per direction.
//!
//! rhino3dm's calls are openNURBS at eb92af3 (the commit rhino3dm 8.35.0 pins), ported in their
//! operation order:
//!
//! * `IsoCurve` is `ON_NurbsSurface::IsoCurve` (opennurbs_nurbssurface.cpp): a curve with the
//!   surface's knots in `dir` whose control points are the other direction's span evaluated at
//!   `c`, through `ON_NurbsCurve::Evaluate` on a curve of dimension `CVSize() * CVCount(dir)`
//!   (`ON_EvaluateBezier` or `ON_EvaluateNurbsNonRationalSpan`, opennurbs_evaluate_nurbs.cpp);
//! * `IsClosed(dir)` is `ON_NurbsSurface::IsClosed` (`ON_IsKnotVectorClamped`,
//!   `ON_PointsAreCoincident`, `ON_IsPointGridClosed` / `ON_ComparePointList`,
//!   `ON_NurbsSurface::IsPeriodic`);
//! * the surface's `PointAt` is `surface_offset::point_at`, and an iso-curve's `PointAt`,
//!   `DerivativeAt(t, 1)` and `IsClosed` are `nearest::Nurbs` and `offset::is_closed`.
//!
//! The rest is `bridge.py` / `tessellate.py` arithmetic with Python float semantics: `x ** 2`
//! is libm `pow`, `sum()` is CPython's compensated sum, `math.atan2` keeps CPython's special
//! cases, `math.acos` is libm `acos`, `round` rounds half to even, `min` / `max` keep the first
//! argument on a tie, and a `set` keeps the first of equal values (0.0 and -0.0). Rust never
//! fuses a multiply and an add, so the parameters are bit-identical to the Python route's
//! wherever rhino3dm does not fuse them either. The adapter
//! (`native_nearest.surface_display_params`) admits only a qualified platform whose loaded
//! rhino3dm reproduces the Python route on its witness surfaces. Anything not decided exactly is
//! NOT_RECOGNISED and Python runs its own route. The output is display parameters only; no
//! geometry is created or changed.

use crate::curvex::ON_ZERO_TOLERANCE;
use crate::display::{
    need_positions, piece_needs, py_atan2, py_max, py_square, py_sum, CURVE_ANGLE_MIN_CHORD,
};
use crate::nearest::{basis, region, separate, Nurbs};
use crate::nurbs_record::read_surface;
use crate::nurbs_trim::span_index;
use crate::offset::{is_closed, knots_are_periodic, on_is_valid, points_are_coincident};
use crate::surface_offset::{cv, domain, point_at, size, surface_is_valid, Surface};

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
/// Python runs `_surface_axis_params` itself.
const NOT_RECOGNISED: i32 = 1;
/// More parameters than `capacity`: only `out_count` is written.
const ROOM: i32 = 4;

/// `bridge.PROBE_PER_SPAN`, `bridge.PROBE_NO_KNOTS`, `bridge.PROBE_MAX`.
const PROBE_PER_SPAN: usize = 4;
const PROBE_NO_KNOTS: usize = 24;
const PROBE_MAX: usize = 512;
/// `bridge.REFINE_ROUNDS`.
const REFINE_ROUNDS: usize = 4;
/// `bridge.SURFACE_MAX_SEGMENTS`.
const SURFACE_MAX_SEGMENTS: usize = 200;
/// `bridge.SURFACE_TURN_ISOS`.
const SURFACE_TURN_ISOS: usize = 3;
/// `bridge.SURFACE_SPLIT_DEG`.
const SURFACE_SPLIT_DEG: f64 = 5.5;
/// `_iso_segments`' default `probe`.
const ISO_PROBE: usize = 24;
/// `tessellate.CURVE_MAX_ANGLE_DEG` and `curve_segments_for`'s `minimum` / `maximum`.
const CURVE_MAX_ANGLE_DEG: f64 = 5.0;
const CURVE_MINIMUM: f64 = 16.0;
const CURVE_MAXIMUM: f64 = 1024.0;

const ON_SQRT_EPSILON: f64 = 1.490116119385e-8;
const ON_RELATIVE_TOLERANCE: f64 = 2.27373675443232059478759765625e-13;

type P3 = [f64; 3];

/// A refusal that sends the whole call to Python.
#[derive(Debug, PartialEq)]
struct Declined;

/// CPython `math.radians`: `x * (pi / 180)`.
fn radians(deg: f64) -> f64 {
    deg * (std::f64::consts::PI / 180.0)
}

/// `bridge._dist3`.
fn dist3(a: P3, b: P3) -> f64 {
    (py_square(a[0] - b[0]) + py_square(a[1] - b[1]) + py_square(a[2] - b[2])).sqrt()
}

/// Python `sorted(s)` of a float `set` built by inserting `values` in order: of equal values
/// (0.0 and -0.0), the first inserted stays. The values are finite.
fn sorted_set(values: impl IntoIterator<Item = f64>) -> Vec<f64> {
    let mut tagged: Vec<(f64, usize)> = values.into_iter().zip(0..).collect();
    tagged.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap().then(a.1.cmp(&b.1)));
    tagged.dedup_by(|later, first| later.0 == first.0);
    tagged.into_iter().map(|(v, _)| v).collect()
}

/// The surface's `PointAt` with `t` along `dir` and `c` across it.
fn surface_point(srf: &Surface, dir: usize, t: f64, c: f64) -> Result<P3, Declined> {
    let p = if dir == 0 {
        point_at(srf, t, c)
    } else {
        point_at(srf, c, t)
    };
    p.map_err(|_| Declined)
}

/// `ON_ComparePoint(dim, is_rat, a, b)`.
fn compare_point(dim: usize, rational: bool, a: &[f64], b: &[f64]) -> i32 {
    let wa = if rational && a[dim] != 0.0 {
        1.0 / a[dim]
    } else {
        1.0
    };
    let wb = if rational && b[dim] != 0.0 {
        1.0 / b[dim]
    } else {
        1.0
    };
    for i in 0..dim {
        let (x, y) = (wa * a[i], wb * b[i]);
        let mut tol = (x.abs() + y.abs()) * ON_RELATIVE_TOLERANCE;
        if tol < ON_ZERO_TOLERANCE {
            tol = ON_ZERO_TOLERANCE;
        }
        if x < y - tol {
            return -1;
        }
        if y < x - tol {
            return 1;
        }
    }
    if wa < wb - ON_SQRT_EPSILON || wb < wa - ON_SQRT_EPSILON {
        return -1;
    }
    0
}

/// `ON_ComparePointList(dim, is_rat, count, ...)` over the points `a(i)` and `b(i)`.
fn compare_point_list<'s>(
    dim: usize,
    rational: bool,
    count: usize,
    a: impl Fn(usize) -> &'s [f64],
    b: impl Fn(usize) -> &'s [f64],
) -> i32 {
    let second_check = rational
        && dim <= 3
        && count > 0
        && on_is_valid(a(0)[dim])
        && a(0)[dim] != 0.0
        && on_is_valid(b(0)[dim])
        && b(0)[dim] != 0.0;
    let (mut rc, mut rc1) = (0, 0);
    let mut i = 0;
    while i < count && rc == 0 {
        let (pa, pb) = (a(i), b(i));
        rc = compare_point(dim, rational, pa, pb);
        if rc != 0 && second_check && pa[dim] != 0.0 && pb[dim] != 0.0 {
            if rc1 == 0 {
                rc1 = rc;
            }
            let ea: P3 = std::array::from_fn(|k| if k < dim { pa[k] / pa[dim] } else { 0.0 });
            let eb: P3 = std::array::from_fn(|k| if k < dim { pb[k] / pb[dim] } else { 0.0 });
            rc = if compare_point(dim, false, &ea, &eb) == 0 {
                0
            } else {
                rc1
            };
        }
        i += 1;
    }
    rc
}

/// `ON_NurbsSurface::IsClosed(dir)`.
fn surface_is_closed(srf: &Surface, dir: usize) -> bool {
    let (order, count, knot) = (srf.order[dir], srf.count[dir], &srf.knots[dir]);
    let rational = srf.rational;
    let [n0, n1] = srf.count;
    // `ON_IsKnotVectorClamped(order, cv_count, knot)` (both ends).
    if knot[0] == knot[order - 2] && knot[count - 1] == knot[order + count - 3] {
        let c0 = cv(srf, 0, 0);
        let (c1, c2) = if dir == 0 {
            (cv(srf, n0 - 1, 0), cv(srf, 0, n1 - 1))
        } else {
            (cv(srf, 0, n1 - 1), cv(srf, n0 - 1, 0))
        };
        let c3 = cv(srf, n0 - 1, n1 - 1);
        // `ON_IsPointGridClosed(dim, is_rat, n0, n1, stride0, stride1, cv, dir)`.
        let grid_closed = if dir == 0 {
            compare_point_list(3, rational, n1, |j| cv(srf, 0, j), |j| cv(srf, n0 - 1, j)) == 0
        } else {
            compare_point_list(3, rational, n0, |i| cv(srf, i, 0), |i| cv(srf, i, n1 - 1)) == 0
        };
        points_are_coincident(3, rational, c0, c1)
            && points_are_coincident(3, rational, c2, c3)
            && grid_closed
    } else {
        surface_is_periodic(srf, dir)
    }
}

/// `ON_NurbsSurface::IsPeriodic(dir)`. openNURBS does not reset its CV indices between rows,
/// so only the first row across `dir` is compared; the port keeps that.
fn surface_is_periodic(srf: &Surface, dir: usize) -> bool {
    let (order, count) = (srf.order[dir], srf.count[dir]);
    if !knots_are_periodic(order, count, &srf.knots[dir]) {
        return false;
    }
    let at = |i: usize| {
        if dir == 1 {
            cv(srf, 0, i)
        } else {
            cv(srf, i, 0)
        }
    };
    (0..=order - 2)
        .all(|j| points_are_coincident(3, srf.rational, at(order - 2 - j), at(count - 1 - j)))
}

/// An iso-curve as `ON_NurbsSurface::IsoCurve` builds it; `cvs` are homogeneous
/// `(x*w, y*w, z*w, w)` (w = 1 when not rational).
struct Iso {
    order: usize,
    knots: Vec<f64>,
    cvs: Vec<f64>,
    rational: bool,
}

/// `ON_EvaluateBezier(dim, false, order, dim, cv, t0, t1, 0, t, dim, v)`: de Casteljau.
fn bezier_point(
    dim: usize,
    order: usize,
    cv: &[f64],
    t0: f64,
    t1: f64,
    t: f64,
) -> Option<Vec<f64>> {
    if t0 == t1 {
        return None;
    }
    let mut buf = Vec::with_capacity(order * dim);
    let (alpha0, alpha1);
    if t0 == t || (t <= 0.5 * (t0 + t1) && t != t1) {
        buf.extend_from_slice(&cv[..order * dim]);
        let a = 1.0 / (t1 - t0);
        alpha0 = (t1 - t) * a;
        alpha1 = a * (t - t0);
    } else {
        for i in (0..order).rev() {
            buf.extend_from_slice(&cv[i * dim..(i + 1) * dim]);
        }
        let a = 1.0 / (t1 - t0);
        alpha1 = (t1 - t) * a;
        alpha0 = a * (t - t0);
    }
    if alpha1 != 0.0 {
        for j in (1..order).rev() {
            for m in 0..j * dim {
                buf[m] = buf[m] * alpha0 + buf[m + dim] * alpha1;
            }
        }
    }
    if order == 2 {
        for i in 0..dim {
            if cv[i] == cv[dim + i] {
                buf[i] = cv[i];
            }
        }
    }
    buf.truncate(dim);
    Some(buf)
}

/// `ON_EvaluateNurbsNonRationalSpan(dim, order, knot, dim, cv, 0, t, dim, v)`.
fn span_point(dim: usize, order: usize, knot: &[f64], cv: &[f64], t: f64) -> Vec<f64> {
    let mut n = vec![0.0; order * order];
    basis(order, knot, t, &mut n);
    let mut v = vec![0.0; dim];
    for (j, &a) in n[..order].iter().enumerate() {
        for (x, &c) in v.iter_mut().zip(&cv[j * dim..(j + 1) * dim]) {
            *x += a * c;
        }
    }
    if order == 2 {
        for i in 0..dim {
            if cv[i] == cv[dim + i] {
                v[i] = cv[i];
            }
        }
    }
    v
}

/// `ON_NurbsSurface::IsoCurve(dir, c)` of a valid surface.
fn iso_curve(srf: &Surface, dir: usize, c: f64) -> Result<Iso, Declined> {
    let other = 1 - dir;
    let (o, n_other) = (srf.order[other], srf.count[other]);
    let span = span_index(o, n_other, &srf.knots[other], c, 1, 0).min(n_other - o);
    let kn = &srf.knots[other][span..span + 2 * o - 2];
    let (m, sz) = (srf.count[dir], size(srf));
    let dim = sz * m;
    // The curve `N` of openNURBS: `o` control points of dimension `dim`.
    let mut ncv = Vec::with_capacity(o * dim);
    for i in 0..o {
        for j in 0..m {
            ncv.extend_from_slice(if dir == 1 {
                cv(srf, i + span, j)
            } else {
                cv(srf, j, i + span)
            });
        }
    }
    // `ON_NurbsCurve::Evaluate` -> `ON_EvaluateNurbsSpan`, side 0 and a single span.
    let v = if kn[0] == kn[o - 2] && kn[o - 1] == kn[2 * o - 3] {
        bezier_point(dim, o, &ncv, kn[o - 2], kn[o - 1], c).ok_or(Declined)?
    } else {
        span_point(dim, o, kn, &ncv, c)
    };
    let mut cvs = Vec::with_capacity(4 * m);
    for p in v.chunks_exact(sz) {
        cvs.extend_from_slice(&[p[0], p[1], p[2], if sz == 4 { p[3] } else { 1.0 }]);
    }
    if !cvs.iter().all(|x| x.is_finite()) {
        return Err(Declined);
    }
    Ok(Iso {
        order: srf.order[dir],
        knots: srf.knots[dir].clone(),
        cvs,
        rational: srf.rational,
    })
}

impl Iso {
    fn nurbs(&self) -> Result<Nurbs<'_>, Declined> {
        Nurbs::new(self.order, &self.knots, &self.cvs, self.rational).ok_or(Declined)
    }
}

fn finite(p: P3) -> Result<P3, Declined> {
    if p.iter().all(|x| x.is_finite()) {
        Ok(p)
    } else {
        Err(Declined)
    }
}

/// `tessellate.curve_segments_for(length, tolerance)` with its defaults.
fn curve_segments_for(length: f64, tolerance: f64) -> Result<usize, Declined> {
    if length <= 0.0 || tolerance <= 0.0 {
        return Ok(CURVE_MINIMUM as usize);
    }
    let angle_count = (360.0 / CURVE_MAX_ANGLE_DEG).ceil();
    let radius = length / (2.0 * std::f64::consts::PI);
    let ratio = if radius > 0.0 {
        1.0 - tolerance / radius
    } else {
        -2.0
    };
    let chord_count = if ratio <= -1.0 {
        CURVE_MINIMUM
    } else {
        // CPython's `math.acos` is libm `acos`, as Rust's.
        let theta = 2.0 * ratio.acos();
        if theta <= 0.0 {
            CURVE_MAXIMUM
        } else {
            let x = 2.0 * std::f64::consts::PI / theta;
            if !x.is_finite() {
                return Err(Declined); // `math.ceil` raises; Python reports it
            }
            x.ceil()
        }
    };
    let n = if angle_count > chord_count {
        angle_count
    } else {
        chord_count
    };
    Ok(n.clamp(CURVE_MINIMUM, CURVE_MAXIMUM) as usize)
}

/// `bridge._iso_segments(face, direction, tolerance)`.
fn iso_segments(srf: &Surface, dir: usize, tolerance: f64) -> Result<usize, Declined> {
    let (t0, t1) = domain(srf, dir);
    let (o0, o1) = domain(srf, 1 - dir);
    let mid = 0.5 * (o0 + o1);
    let mut pts = Vec::with_capacity(ISO_PROBE + 1);
    for k in 0..=ISO_PROBE {
        let t = t0 + (t1 - t0) * k as f64 / ISO_PROBE as f64;
        pts.push(surface_point(srf, dir, t, mid)?);
    }
    let mut length = 0.0;
    for w in pts.windows(2) {
        length += dist3(w[0], w[1]);
    }
    if length <= 1e-9 {
        return Ok(1);
    }
    if !surface_is_closed(srf, dir) {
        let chord = dist3(pts[0], pts[ISO_PROBE]);
        if (length - chord) <= 1e-4 * length {
            return Ok(1);
        }
    }
    curve_segments_for(length, tolerance)
}

/// `bridge._surface_interior_knots(surface, direction)` of a NurbsSurface.
fn interior_knots(srf: &Surface, dir: usize) -> Vec<f64> {
    let (t0, t1) = domain(srf, dir);
    let eps = (t1 - t0) * 1e-9;
    sorted_set(srf.knots[dir].iter().copied())
        .into_iter()
        .filter(|&v| t0 + eps < v && v < t1 - eps)
        .collect()
}

/// `tessellate.distribute_params(breakpoints, total, t0, t1)`.
fn distribute_params(breakpoints: &[f64], total: usize, t0: f64, t1: f64) -> Vec<f64> {
    let span = t1 - t0;
    if span <= 0.0 {
        return vec![t0];
    }
    let bps = sorted_set(
        [t0, t1]
            .into_iter()
            .chain(breakpoints.iter().copied().filter(|&b| t0 < b && b < t1)),
    );
    let total = total.max(bps.len() - 1);
    let mut params = bps.clone();
    for w in bps.windows(2) {
        let (a, b) = (w[0], w[1]);
        // `int(round(x))`: round half to even.
        let k = ((total as f64 * (b - a) / span).round_ties_even() as usize).max(1);
        for i in 1..k {
            params.push(a + (b - a) * i as f64 / k as f64);
        }
    }
    sorted_set(params)
}

/// `bridge._vector_turn`.
fn vector_turn(a: P3, b: P3) -> f64 {
    let cx = a[1] * b[2] - a[2] * b[1];
    let cy = a[2] * b[0] - a[0] * b[2];
    let cz = a[0] * b[1] - a[1] * b[0];
    let dot = py_sum(&[a[0] * b[0], a[1] * b[1], a[2] * b[2]]);
    let cross = (cx * cx + cy * cy + cz * cz).sqrt();
    if cross == 0.0 && dot == 0.0 {
        0.0
    } else {
        py_atan2(cross, dot)
    }
}

/// An iso-curve ready to evaluate, with its `IsClosed`.
struct Probe<'a> {
    curve: Nurbs<'a>,
    closed: bool,
}

impl<'a> Probe<'a> {
    fn new(iso: &'a Iso) -> Result<Self, Declined> {
        let curve = iso.nurbs()?;
        let closed = is_closed(&curve, &iso.cvs, iso.rational).map_err(|_| Declined)?;
        Ok(Self { curve, closed })
    }

    /// The chord directions of the polyline through `PointAt(t)` for `params`.
    fn dirs(&self, params: &[f64]) -> Result<Vec<P3>, Declined> {
        let mut pts = Vec::with_capacity(params.len());
        for &t in params {
            pts.push(finite(self.curve.point(t))?);
        }
        Ok(pts
            .windows(2)
            .map(|w| [w[1][0] - w[0][0], w[1][1] - w[0][1], w[1][2] - w[0][2]])
            .collect())
    }
}

/// `bridge._surface_turn_exceeds(isos, params, limit_deg)`.
fn turn_exceeds(isos: &[Probe], params: &[f64], limit_deg: f64) -> Result<bool, Declined> {
    let limit = radians(limit_deg);
    for iso in isos {
        let dirs = iso.dirs(params)?;
        let mut pairs: Vec<(P3, P3)> = dirs.windows(2).map(|w| (w[0], w[1])).collect();
        if iso.closed && dirs.len() > 1 {
            pairs.push((dirs[dirs.len() - 1], dirs[0]));
        }
        if pairs.iter().any(|&(a, b)| vector_turn(a, b) > limit) {
            return Ok(true);
        }
    }
    Ok(false)
}

/// `bridge._surface_turn_verify(isos, params)`.
fn turn_verify(isos: &[Probe], mut params: Vec<f64>) -> Result<Vec<f64>, Declined> {
    let limit = radians(SURFACE_SPLIT_DEG);
    for _ in 0..REFINE_ROUNDS {
        let mut split: Vec<usize> = Vec::new();
        for iso in isos {
            let dirs = iso.dirs(&params)?;
            for i in 1..dirs.len() {
                if vector_turn(dirs[i - 1], dirs[i]) > limit {
                    split.push(i - 1);
                    split.push(i);
                }
            }
            if iso.closed && dirs.len() > 1 && vector_turn(dirs[dirs.len() - 1], dirs[0]) > limit {
                split.push(dirs.len() - 1);
                split.push(0);
            }
        }
        split.sort_unstable();
        split.dedup();
        if split.is_empty() || params.len() + split.len() > SURFACE_MAX_SEGMENTS + 1 {
            break;
        }
        let mids: Vec<f64> = split
            .iter()
            .map(|&i| 0.5 * (params[i] + params[i + 1]))
            .collect();
        params = sorted_set(params.into_iter().chain(mids));
    }
    Ok(params)
}

/// `bridge._surface_turn_params(surface, direction, tolerance, knots, base)`; `None` where
/// Python returns None.
fn turn_params(
    srf: &Surface,
    dir: usize,
    tolerance: f64,
    knots: &[f64],
    base: &[f64],
) -> Result<Option<Vec<f64>>, Declined> {
    let (lo, hi) = domain(srf, dir);
    let (o0, o1) = domain(srf, 1 - dir);
    if hi <= lo {
        // `not hi > lo`; both are finite (the export checks)
        return Ok(None);
    }
    let k = PROBE_NO_KNOTS.max(PROBE_MAX.min(PROBE_PER_SPAN * (1 + knots.len())));
    let mut isos = Vec::with_capacity(SURFACE_TURN_ISOS);
    for j in 0..SURFACE_TURN_ISOS {
        let c = o0 + (o1 - o0) * j as f64 / (SURFACE_TURN_ISOS - 1).max(1) as f64;
        isos.push(iso_curve(srf, dir, c)?);
    }
    let probes = isos.iter().map(Probe::new).collect::<Result<Vec<_>, _>>()?;
    if !turn_exceeds(&probes, base, SURFACE_SPLIT_DEG)? {
        return Ok(Some(base.to_vec()));
    }
    let mut needs: Option<Vec<f64>> = None;
    let mut useful = Vec::new();
    for probe in probes {
        let mut pts = Vec::with_capacity(k + 1);
        let mut tans = Vec::with_capacity(k + 1);
        for i in 0..=k {
            let t = if i == k {
                hi
            } else {
                lo + (hi - lo) * i as f64 / k as f64
            };
            let [p, v] = probe.curve.point_and_tangent(t);
            pts.push(finite(p)?);
            tans.push(finite(v)?);
        }
        let length: Vec<f64> = pts.windows(2).map(|w| dist3(w[0], w[1])).collect();
        if py_sum(&length) <= 1e-9 {
            continue; // a pole: no turn
        }
        let chord = piece_needs(&pts, &tans, tolerance, CURVE_ANGLE_MIN_CHORD);
        let angle = piece_needs(&pts, &tans, 0.0, CURVE_ANGLE_MIN_CHORD);
        let these: Vec<f64> = chord
            .iter()
            .zip(&angle)
            .map(|(&a, &b)| py_max(a, b))
            .collect();
        needs = Some(match needs {
            None => these,
            Some(n) => n.iter().zip(&these).map(|(&a, &b)| py_max(a, b)).collect(),
        });
        useful.push(probe);
    }
    let Some(needs) = needs else {
        return Ok(None);
    };
    let us = need_positions(&needs, SURFACE_MAX_SEGMENTS as f64).ok_or(Declined)?;
    let params = sorted_set(
        [lo, hi]
            .into_iter()
            .chain(knots.iter().copied())
            .chain(us.iter().map(|&u| lo + (hi - lo) * u / k as f64)),
    );
    turn_verify(&useful, params).map(Some)
}

/// `bridge._surface_axis_params(surface, direction, tolerance)`. `knots_visible` is whether
/// the Python object has `KnotsU` / `KnotsV` (a NurbsSurface; a BrepFace or a plain Surface
/// has none, and Python then places no line on a knot).
fn axis_params(
    srf: &Surface,
    dir: usize,
    tolerance: f64,
    knots_visible: bool,
) -> Result<Vec<f64>, Declined> {
    let (t0, t1) = domain(srf, dir);
    let total = iso_segments(srf, dir, tolerance)?.clamp(1, SURFACE_MAX_SEGMENTS);
    let knots = if knots_visible {
        interior_knots(srf, dir)
    } else {
        Vec::new()
    };
    let base = distribute_params(&knots, total, t0, t1);
    if total > 1 {
        if let Some(by_turn) = turn_params(srf, dir, tolerance, &knots, &base)? {
            return Ok(by_turn);
        }
    }
    Ok(base)
}

/// `bridge._surface_axis_params(surface, direction, tolerance)` for a surface whose `Encode()`
/// record is `record`.
///
/// `knots_visible` is 1 when the Python object is a NurbsSurface (it exposes its knots) and 0
/// for a BrepFace or plain Surface. Returns 0 with the parameters in `out_params` and their
/// number in `out_count`; ROOM (4) with only `out_count` written when they do not fit
/// `capacity`; NOT_RECOGNISED (1) when the record is not a valid ON_NurbsSurface or the answer
/// is not decided exactly here, so Python runs; ERR_INPUT (-2) for a direction other than 0 or
/// 1, a flag other than 0 or 1, or a non-finite tolerance; ERR_NULL (-1) for a null,
/// misaligned or overlapping pointer. Nothing is written on any refusal.
///
/// # Safety
/// `record` is readable for `record_len` bytes, `out_params` writable for `capacity` values
/// and `out_count` for one.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_surface_display_params(
    record: *const u8,
    record_len: usize,
    direction: u32,
    tolerance: f64,
    knots_visible: u8,
    out_params: *mut f64,
    capacity: usize,
    out_count: *mut usize,
) -> i32 {
    if out_count.is_null() {
        return ERR_NULL;
    }
    let ranges = (
        region(record, record_len),
        region(out_params.cast_const(), capacity),
        region(out_count.cast_const(), 1),
    );
    let (Some(rr), Some(pr), Some(cr)) = ranges else {
        return ERR_NULL;
    };
    if !separate(&[rr], &[pr, cr]) {
        return ERR_NULL;
    }
    if direction > 1 || knots_visible > 1 || !tolerance.is_finite() {
        return ERR_INPUT;
    }
    let Some(data) = (unsafe { crate::checked_slice(record, record_len) }) else {
        return ERR_NULL;
    };
    let Some(srf) = read_surface(data) else {
        return NOT_RECOGNISED;
    };
    if !surface_is_valid(&srf)
        || !srf
            .knots
            .iter()
            .flatten()
            .chain(&srf.cvs)
            .all(|v| v.is_finite())
    {
        return NOT_RECOGNISED;
    }
    let Ok(params) = axis_params(&srf, direction as usize, tolerance, knots_visible == 1) else {
        return NOT_RECOGNISED;
    };
    if !params.iter().all(|t| t.is_finite()) {
        return NOT_RECOGNISED;
    }
    if params.len() > capacity {
        unsafe {
            *out_count = params.len();
        }
        return ROOM;
    }
    let Some(out) = (unsafe { crate::checked_slice_mut(out_params, params.len()) }) else {
        return ERR_NULL;
    };
    out.copy_from_slice(&params);
    unsafe {
        *out_count = params.len();
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::nurbs_record::surface_record;

    fn surface(order: [usize; 2], rows: &[Vec<P3>], rational: Option<&[Vec<f64>]>) -> Surface {
        let count = [rows.len(), rows[0].len()];
        let knots = std::array::from_fn(|d| {
            let (o, n) = (order[d], count[d]);
            (0..n + o - 2)
                .map(|i| (i as f64 + 2.0 - o as f64).clamp(0.0, (n - o + 1) as f64))
                .collect()
        });
        let mut cvs = Vec::new();
        for (i, row) in rows.iter().enumerate() {
            for (j, p) in row.iter().enumerate() {
                match rational {
                    Some(w) => {
                        let w = w[i][j];
                        cvs.extend_from_slice(&[p[0] * w, p[1] * w, p[2] * w, w]);
                    }
                    None => cvs.extend_from_slice(p),
                }
            }
        }
        Surface {
            rational: rational.is_some(),
            order,
            count,
            knots,
            cvs,
        }
    }

    fn bump(n: usize) -> Surface {
        let rows: Vec<Vec<P3>> = (0..n)
            .map(|i| {
                (0..n)
                    .map(|j| {
                        let (x, y) = (i as f64, j as f64);
                        [x, y, (x * 1.3).sin() * (y * 0.9).cos() * 2.0]
                    })
                    .collect()
            })
            .collect();
        surface([4, 4], &rows, None)
    }

    /// A cylinder wall: a rational quadratic circle in u (9 CVs) swept along z (linear in v).
    fn cylinder() -> Surface {
        let s = std::f64::consts::FRAC_1_SQRT_2;
        let circle = [
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
        let rows: Vec<Vec<P3>> = circle
            .iter()
            .map(|&(x, y, _)| vec![[x, y, 0.0], [x, y, 3.0]])
            .collect();
        let weights: Vec<Vec<f64>> = circle.iter().map(|&(_, _, w)| vec![w, w]).collect();
        let mut srf = surface([3, 2], &rows, Some(&weights));
        srf.knots[0] = vec![0.0, 0.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0];
        srf
    }

    unsafe fn call(
        record: &[u8],
        dir: u32,
        tol: f64,
        visible: u8,
        cap: usize,
    ) -> (i32, Vec<f64>, usize) {
        let mut out = vec![-7.0; cap.max(1)];
        let mut count = 99usize;
        let code = unsafe {
            narwhal_pure_core_surface_display_params(
                record.as_ptr(),
                record.len(),
                dir,
                tol,
                visible,
                out.as_mut_ptr(),
                cap,
                &mut count,
            )
        };
        (code, out, count)
    }

    #[test]
    fn sets_keep_the_first_of_equal_values_and_sort() {
        let got = sorted_set([3.0, -0.0, 1.0, 0.0, 3.0, 2.0]);
        assert_eq!(got, vec![-0.0, 1.0, 2.0, 3.0]);
        assert!(got[0].is_sign_negative());
        assert!(sorted_set([0.0, -0.0])[0].is_sign_positive());
    }

    #[test]
    fn distribute_params_rounds_half_to_even_and_keeps_every_knot() {
        // Two spans of width 1 sharing 5 segments: 2.5 rounds to 2 in each.
        assert_eq!(
            distribute_params(&[1.0], 5, 0.0, 2.0),
            vec![0.0, 0.5, 1.0, 1.5, 2.0]
        );
        // Knots outside the domain are ignored; too few segments still get one per span, and
        // the last span's 1.5 rounds to 2.
        assert_eq!(
            distribute_params(&[-1.0, 0.25, 0.5, 9.0], 1, 0.0, 1.0),
            vec![0.0, 0.25, 0.5, 0.75, 1.0]
        );
    }

    #[test]
    fn curve_segments_follow_the_circle_model() {
        // A unit circle at 0.01: the angle rule's 72 wins over the chord rule's 23.
        assert_eq!(curve_segments_for(2.0 * std::f64::consts::PI, 0.01), Ok(72));
        assert_eq!(curve_segments_for(1.0e4, 0.01), Ok(887));
        assert_eq!(curve_segments_for(1.0e5, 0.01), Ok(1024));
        assert_eq!(curve_segments_for(0.0, 0.01), Ok(16));
    }

    #[test]
    fn closed_directions_are_recognised() {
        let cyl = cylinder();
        assert!(surface_is_closed(&cyl, 0));
        assert!(!surface_is_closed(&cyl, 1));
        let b = bump(6);
        assert!(!surface_is_closed(&b, 0) && !surface_is_closed(&b, 1));
    }

    #[test]
    fn iso_curves_lie_on_the_surface() {
        for srf in [bump(7), cylinder()] {
            for dir in 0..2 {
                let (o0, o1) = domain(&srf, 1 - dir);
                let (t0, t1) = domain(&srf, dir);
                for c in [o0, 0.37 * o0 + 0.63 * o1, o1] {
                    let iso = iso_curve(&srf, dir, c).unwrap();
                    let curve = iso.nurbs().unwrap();
                    for k in 0..=10 {
                        let t = t0 + (t1 - t0) * k as f64 / 10.0;
                        let p = curve.point(t);
                        let q = surface_point(&srf, dir, t, c).unwrap();
                        assert!(dist3(p, q) < 1e-12, "{dir} {c} {t}: {p:?} {q:?}");
                    }
                }
            }
        }
    }

    #[test]
    fn a_cylinder_keeps_its_circle_and_one_segment_up_its_height() {
        let record = surface_record(&cylinder());
        let (code, out, count) = unsafe { call(&record, 1, 0.01, 1, 64) };
        assert_eq!((code, count), (0, 2));
        assert_eq!(&out[..2], &[0.0, 1.0]);
        let (code, out, count) = unsafe { call(&record, 0, 0.01, 1, 512) };
        assert_eq!(code, 0);
        assert!(count >= 72, "{count}");
        assert_eq!((out[0], out[count - 1]), (0.0, 4.0));
        assert!(out[..count].windows(2).all(|w| w[0] < w[1]));
        // Every knot is a line.
        for knot in [1.0, 2.0, 3.0] {
            assert!(out[..count].contains(&knot));
        }
    }

    #[test]
    fn a_bump_without_visible_knots_differs_only_by_its_knot_lines() {
        let record = surface_record(&bump(9));
        let (code, with, n) = unsafe { call(&record, 0, 0.01, 1, 1024) };
        assert_eq!(code, 0);
        let (code, without, m) = unsafe { call(&record, 0, 0.01, 0, 1024) };
        assert_eq!(code, 0);
        assert!(with[..n].windows(2).all(|w| w[0] < w[1]));
        assert!(without[..m].windows(2).all(|w| w[0] < w[1]));
        assert!((1..7).all(|k| with[..n].contains(&(k as f64))));
    }

    #[test]
    fn room_writes_only_the_count_and_refusals_write_nothing() {
        let record = surface_record(&bump(9));
        let (code, _, need) = unsafe { call(&record, 0, 0.01, 1, 1024) };
        assert_eq!(code, 0);
        let (code, out, count) = unsafe { call(&record, 0, 0.01, 1, need - 1) };
        assert_eq!((code, count), (ROOM, need));
        assert!(out.iter().all(|&x| x == -7.0));
        for (dir, tol, visible) in [
            (2, 0.01, 1),
            (0, f64::NAN, 1),
            (0, f64::INFINITY, 0),
            (0, 0.01, 2),
        ] {
            let (code, out, count) = unsafe { call(&record, dir, tol, visible, 64) };
            assert_eq!((code, count), (ERR_INPUT, 99));
            assert!(out.iter().all(|&x| x == -7.0));
        }
        let mut bad = bump(6);
        bad.knots[0].reverse();
        let (code, out, count) = unsafe { call(&surface_record(&bad), 0, 0.01, 1, 64) };
        assert_eq!((code, count), (NOT_RECOGNISED, 99));
        assert!(out.iter().all(|&x| x == -7.0));
        let (code, _, count) = unsafe { call(b"not a record", 0, 0.01, 1, 64) };
        assert_eq!((code, count), (NOT_RECOGNISED, 99));
    }

    #[test]
    fn bad_pointers_are_refused() {
        let record = surface_record(&bump(6));
        let mut out = [0.0; 8];
        let code = unsafe {
            narwhal_pure_core_surface_display_params(
                record.as_ptr(),
                record.len(),
                0,
                0.01,
                1,
                out.as_mut_ptr(),
                8,
                std::ptr::null_mut(),
            )
        };
        assert_eq!(code, ERR_NULL);
        let mut count = 0usize;
        let code = unsafe {
            narwhal_pure_core_surface_display_params(
                record.as_ptr(),
                record.len(),
                0,
                0.01,
                1,
                std::ptr::null_mut(),
                8,
                &mut count,
            )
        };
        assert_eq!((code, count), (ERR_NULL, 0));
        // Output overlapping the record.
        let mut overlapped = record.clone();
        overlapped.resize(record.len() + 64, 0);
        let base = overlapped.as_mut_ptr();
        let aligned = unsafe { base.add(base.align_offset(8)) };
        let code = unsafe {
            narwhal_pure_core_surface_display_params(
                base,
                overlapped.len(),
                0,
                0.01,
                1,
                aligned.cast(),
                4,
                &mut count,
            )
        };
        assert_eq!((code, count), (ERR_NULL, 0));
    }

    #[test]
    fn flipped_records_never_panic() {
        let record = surface_record(&bump(6));
        let mut seed = 0x2545_F491_4F6C_DD1Du64;
        for _ in 0..300 {
            let mut copy = record.clone();
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            let at = (seed as usize) % copy.len();
            copy[at] ^= 1 << (seed >> 61);
            let (code, _, _) = unsafe { call(&copy, (seed & 1) as u32, 0.01, 1, 4096) };
            assert!([0, NOT_RECOGNISED].contains(&code), "{code}");
        }
    }
}
