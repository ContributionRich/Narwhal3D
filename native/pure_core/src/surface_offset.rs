//! The surface offset of `offset.surface_offset` (OffsetSrf), read from the surface's
//! `Encode()` record.
//!
//! Each round refines a copy of the surface by inserting a knot at the middle of every knot
//! span in both directions, moves each control point by `distance` along the surface normal
//! at its Greville abscissae, and measures the result against the TRUE offset
//! `P(u, v) + distance * N(u, v)` on a 25 x 25 grid, refusing a surface whose normal turns
//! over. Python ran this through rhino3dm: about 2,500 `PointAt` / `NormalAt` calls a round
//! plus one `NormalAt` per control point, for up to seven rounds.
//!
//! rhino3dm's calls are openNURBS at eb92af3 (the commit rhino3dm 8.35.0 pins), ported in
//! their operation order:
//!
//! * `PointAt` / `NormalAt` are `ON_NurbsSurface::Evaluate` (`ON_EvaluateNurbsSurfaceSpan`
//!   and `ON_EvaluateQuotientRule2`, opennurbs_evaluate_nurbs.cpp / opennurbs_math.cpp) under
//!   `ON_Surface::EvPoint` and `ON_Surface::EvNormal` (opennurbs_surface.cpp), including its
//!   singular-point branch (`ON_EvNormal`, `ON_EvJacobian`);
//! * `KnotsU.InsertKnot(m, 1)` is `ON_NurbsSurface::InsertKnot` (opennurbs_nurbssurface.cpp)
//!   through `ON_NurbsCurve::InsertKnot` and `ON_InsertKnot` (opennurbs_knot.cpp);
//! * `GetControlPoint` / `Points[i, j] = ...` are `ON_NurbsSurface::GetCV` / `SetCV`.
//!
//! The rest is `offset.py`'s arithmetic with Python float semantics (`sum` is CPython's
//! compensated float sum, `math.dist` is `python_norm::dist3`). Every operation is one
//! IEEE-754 multiply, add, subtract, divide or square root, apart from one `cos` of a
//! constant, and Rust never fuses them, so the result is bit-identical to the Python
//! route's wherever rhino3dm does not fuse them either. The adapter
//! (`native_nearest.surface_offset_fit`) admits only a qualified platform whose loaded
//! rhino3dm reproduces the Python route on its witness surfaces. Anything not decided
//! exactly is NOT_RECOGNISED and Python runs its own route.

use crate::curvex::on_length3d;
use crate::nearest::{basis, basis_derivatives, region, separate};
use crate::nurbs_record::{read_surface, surface_record, SurfaceRecord, MAX_SURFACE_CVS};
use crate::nurbs_trim::span_index;
use crate::offset::{knot_vector_is_valid, knots_are_periodic, on_unitize};
use crate::python_norm::dist3;

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
/// Python runs `surface_offset` itself.
const NOT_RECOGNISED: i32 = 1;
/// The offset turns the surface inside out: refused.
const FOLDED: i32 = 3;
/// The result needs more room than the capacities: only `out_shape` is written.
const ROOM: i32 = 4;
/// No round met the tolerance: the last round's surface and deviation.
const APPROXIMATE: i32 = 5;

/// The most refinement rounds a call may ask for.
pub const MAX_REFINE: u32 = 10;
/// The sample grid of `_offset_surface_quality`.
const GRID: usize = 24;

const ON_EPSILON: f64 = f64::EPSILON;
const ON_SQRT_EPSILON: f64 = 1.490116119385e-8;
/// `ON_DEFAULT_ANGLE_TOLERANCE`: `ON_PI / 180.0`.
const ON_DEFAULT_ANGLE_TOLERANCE: f64 = std::f64::consts::PI / 180.0;

type P3 = [f64; 3];

/// A refusal that sends the whole offset to Python.
#[derive(Debug, PartialEq)]
struct Declined;

fn cross(a: P3, b: P3) -> P3 {
    // `ON_CrossProduct`.
    [
        a[1] * b[2] - b[1] * a[2],
        a[2] * b[0] - b[2] * a[0],
        a[0] * b[1] - b[0] * a[1],
    ]
}

fn dot(a: P3, b: P3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn length(v: P3) -> f64 {
    on_length3d(v[0], v[1], v[2])
}

/// `ON_3dVector::operator/(double)`: a multiply by `1/d`.
fn over(v: P3, d: f64) -> P3 {
    let r = 1.0 / d;
    [v[0] * r, v[1] * r, v[2] * r]
}

/// `ON_3dVector::IsParallelTo(v, angle_tolerance)`.
fn parallel(a: P3, b: P3, angle_tolerance: f64) -> i32 {
    let ll = length(a) * length(b);
    if ll > 0.0 {
        let cos_angle = (a[0] * b[0] + a[1] * b[1] + a[2] * b[2]) / ll;
        let cos_tol = angle_tolerance.cos();
        if cos_angle >= cos_tol {
            return 1;
        } else if cos_angle <= -cos_tol {
            return -1;
        }
    }
    0
}

/// `ON_EvJacobian(ds_o_ds, ds_o_dt, dt_o_dt, nullptr)`.
fn jacobian(ds_o_ds: f64, ds_o_dt: f64, dt_o_dt: f64) -> bool {
    let a = ds_o_ds * dt_o_dt;
    let b = ds_o_dt * ds_o_dt;
    let det = a - b;
    if ds_o_ds <= dt_o_dt * ON_EPSILON || dt_o_dt <= ds_o_ds * ON_EPSILON {
        false
    } else {
        det.abs() > (if a > b { a } else { b }) * ON_SQRT_EPSILON
    }
}

/// `ON_3dVector::IsValid`.
fn vector_is_valid(v: P3) -> bool {
    v.iter().all(|&x| crate::offset::on_is_valid(x))
}

/// `ON_EvNormal(limit_dir, Du, Dv, Duu, Duv, Dvv, N)`: the unit normal, or None where
/// `Unitize` fails.
fn ev_normal(limit_dir: i32, du: P3, dv: P3, duu: P3, duv: P3, dvv: P3) -> Option<P3> {
    let du_o_du = du[0] * du[0] + du[1] * du[1] + du[2] * du[2];
    let du_o_dv = dot(du, dv);
    let dv_o_dv = dv[0] * dv[0] + dv[1] * dv[1] + dv[2] * dv[2];
    let n = if jacobian(du_o_du, du_o_dv, dv_o_dv) {
        cross(du, dv)
    } else if vector_is_valid(duu) && vector_is_valid(duv) && vector_is_valid(dvv) {
        let (a, b) = match limit_dir {
            2 => (-1.0, 1.0),
            3 => (-1.0, -1.0),
            4 => (1.0, -1.0),
            _ => (1.0, 1.0),
        };
        let v: P3 = std::array::from_fn(|i| a * duv[i] + b * dvv[i]);
        let av = [
            du[1] * v[2] - du[2] * v[1],
            du[2] * v[0] - du[0] * v[2],
            du[0] * v[1] - du[1] * v[0],
        ];
        let v: P3 = std::array::from_fn(|i| a * duu[i] + b * duv[i]);
        let au = [
            v[1] * dv[2] - v[2] * dv[1],
            v[2] * dv[0] - v[0] * dv[2],
            v[0] * dv[1] - v[1] * dv[0],
        ];
        [av[0] + au[0], av[1] + au[1], av[2] + au[2]]
    } else {
        [0.0; 3]
    };
    on_unitize(n)
}

/// The surface as `ON_NurbsSurface` holds it.
type Surface = SurfaceRecord;

fn size(s: &Surface) -> usize {
    if s.rational {
        4
    } else {
        3
    }
}

fn cv(s: &Surface, i: usize, j: usize) -> &[f64] {
    let n = size(s);
    let at = (i * s.count[1] + j) * n;
    &s.cvs[at..at + n]
}

/// `ON_NurbsSurface::Domain(dir)`.
fn domain(s: &Surface, dir: usize) -> (f64, f64) {
    (
        s.knots[dir][s.order[dir] - 2],
        s.knots[dir][s.count[dir] - 1],
    )
}

/// `ON_NurbsSurface::IsValid` of a 3-D surface.
fn surface_is_valid(s: &Surface) -> bool {
    (0..2).all(|d| knot_vector_is_valid(s.order[d], s.count[d], &s.knots[d]))
        && s.cvs.iter().all(|&x| -1.0e307 < x && x < 1.0e307)
}

/// `ON_NurbsSurface::Evaluate(s, t, der_count, 3, v, side, nullptr)` for `der_count <= 2`
/// (index loops, as openNURBS's pointer walks):
/// `[P, Ds, Dt, Dss, Dst, Dtt]`, the rows past `der_count` zero.
#[allow(clippy::needless_range_loop)]
fn evaluate(
    srf: &Surface,
    s: f64,
    t: f64,
    der_count: usize,
    side: i32,
) -> Result<[P3; 6], Declined> {
    let [o0, o1] = srf.order;
    let si = span_index(
        o0,
        srf.count[0],
        &srf.knots[0],
        s,
        if side == 2 || side == 3 { -1 } else { 1 },
        0,
    );
    let ti = span_index(
        o1,
        srf.count[1],
        &srf.knots[1],
        t,
        if side == 3 || side == 4 { -1 } else { 1 },
        0,
    );
    let k0 = &srf.knots[0][si..si + 2 * o0 - 2];
    let k1 = &srf.knots[1][ti..ti + 2 * o1 - 2];
    let der0 = der_count.min(o0 - 1);
    let der1 = der_count.min(o1 - 1);
    let mut n0 = vec![0.0; o0 * o0];
    let mut n1 = vec![0.0; o1 * o1];
    basis(o0, k0, s, &mut n0);
    basis(o1, k1, t, &mut n1);
    if der0 > 0 {
        basis_derivatives(o0, k0, der0, &mut n0);
        basis_derivatives(o1, k1, der1, &mut n1);
    }
    let cvdim = size(srf);
    let mut p = [[0.0_f64; 4]; 6];
    let mut add = |slot: usize, c: f64, cv: &[f64]| {
        for k in 0..cvdim {
            p[slot][k] += c * cv[k];
        }
    };
    for j0 in 0..o0 {
        for j1 in 0..o1 {
            add(0, n0[j0] * n1[j1], cv(srf, si + j0, ti + j1));
        }
    }
    if der_count > 0 {
        for j0 in 0..o0 {
            for j1 in 0..o1 {
                let c = cv(srf, si + j0, ti + j1);
                add(1, n0[j0 + o0] * n1[j1], c);
                add(2, n0[j0] * n1[j1 + o1], c);
            }
        }
        if der_count > 1 && der0 + der1 > 1 {
            for j0 in 0..o0 {
                for j1 in 0..o1 {
                    let c = cv(srf, si + j0, ti + j1);
                    if der0 > 1 {
                        add(3, n0[j0 + 2 * o0] * n1[j1], c);
                    }
                    add(4, n0[j0 + o0] * n1[j1 + o1], c);
                    if der1 > 1 {
                        add(5, n0[j0] * n1[j1 + 2 * o1], c);
                    }
                }
            }
        }
    }
    if srf.rational && !quotient_rule(&mut p, der_count) {
        return Err(Declined);
    }
    if !p
        .iter()
        .take((der_count + 1) * (der_count + 2) / 2)
        .flatten()
        .all(|x| x.is_finite())
    {
        return Err(Declined);
    }
    Ok(std::array::from_fn(|i| [p[i][0], p[i][1], p[i][2]]))
}

/// `ON_EvaluateQuotientRule2(3, der_count, 4, v)` for `der_count <= 2`; false for a zero
/// weight (where openNURBS returns the undivided values).
#[allow(clippy::needless_range_loop)]
fn quotient_rule(v: &mut [[f64; 4]; 6], der_count: usize) -> bool {
    if v[0][3] == 0.0 {
        return false;
    }
    let f = 1.0 / v[0][3];
    let rows = (der_count + 1) * (der_count + 2) / 2;
    for row in v.iter_mut().take(rows) {
        for x in row.iter_mut() {
            *x *= f;
        }
    }
    if der_count > 0 {
        let ws = -v[1][3];
        let wt = -v[2][3];
        for j in 0..3 {
            let f = v[0][j];
            v[1][j] += ws * f;
            v[2][j] += wt * f;
        }
        if der_count > 1 {
            let wss = -v[3][3];
            let wst = -v[4][3];
            let wtt = -v[5][3];
            for j in 0..3 {
                let f = v[0][j];
                let ft = v[2][j];
                let fs = v[1][j];
                v[3][j] += wss * f + 2.0 * ws * fs;
                v[4][j] += wst * f + wt * fs + ws * ft;
                v[5][j] += wtt * f + 2.0 * wt * ft;
            }
        }
    }
    true
}

/// `surface.PointAt(s, t)`.
fn point_at(srf: &Surface, s: f64, t: f64) -> Result<P3, Declined> {
    Ok(evaluate(srf, s, t, 0, 0)?[0])
}

/// `surface.NormalAt(s, t)`: `ON_Surface::EvNormal` with side 0; the zero vector where it
/// fails.
fn normal_at(srf: &Surface, s: f64, t: f64) -> Result<P3, Declined> {
    let [_, ds, dt, ..] = evaluate(srf, s, t, 1, 0)?;
    let len_ds = length(ds);
    let len_dt = length(dt);
    let unit_cross = || on_unitize(cross(over(ds, len_ds), over(dt, len_dt)));
    let normal = if len_ds > ON_SQRT_EPSILON * len_dt
        && len_dt > ON_SQRT_EPSILON * len_ds
        && parallel(ds, dt, 0.01 * ON_DEFAULT_ANGLE_TOLERANCE) == 0
    {
        unit_cross()
    } else {
        let mut side = 0;
        let mut on_side = false;
        let (s0, s1) = domain(srf, 0);
        let (t0, t1) = domain(srf, 1);
        if s == s0 {
            side = if side >= 3 { 4 } else { 1 };
            on_side = true;
        } else if s == s1 {
            side = if side >= 3 { 3 } else { 2 };
            on_side = true;
        }
        if t == t0 {
            side = if side == 2 || side == 3 { 2 } else { 1 };
            on_side = true;
        } else if t == t1 {
            side = if side == 2 || side == 3 { 3 } else { 4 };
            on_side = true;
        }
        if !on_side {
            if len_ds > ON_EPSILON * len_dt && len_dt > ON_EPSILON * len_ds {
                unit_cross()
            } else {
                None
            }
        } else {
            let [_, du, dv, duu, duv, dvv] = evaluate(srf, s, t, 2, side)?;
            ev_normal(side, du, dv, duu, duv, dvv)
        }
    };
    let n = normal.unwrap_or([0.0; 3]);
    if n.iter().all(|v| v.is_finite()) {
        Ok(n)
    } else {
        Err(Declined)
    }
}

/// `ON_DomainTolerance(a, b)`.
fn domain_tolerance(a: f64, b: f64) -> f64 {
    if a == b {
        return 0.0;
    }
    let tol = (a.abs() + b.abs() + (a - b).abs()) * ON_SQRT_EPSILON;
    if tol < ON_EPSILON {
        ON_EPSILON
    } else {
        tol
    }
}

/// `ON_InsertKnot(value, 1, dim, order, count, dim, cv, knot, &hint)` on a curve whose
/// control points are the `dim`-double rows of `cvs`.
fn insert_one_knot(
    order: usize,
    dim: usize,
    knots: &mut Vec<f64>,
    cvs: &mut Vec<f64>,
    value: f64,
    hint: usize,
) {
    let count = cvs.len() / dim;
    let si = span_index(order, count, knots, value, 1, hint);
    let k = &knots[si..];
    let local_count = count - si;
    let tol = domain_tolerance(k[order - 2], k[order - 1]);
    if si == 0 && value < k[order - 1] && value <= k[order - 2] + tol {
        return; // at the start of the domain
    }
    if si == local_count - order && value > k[order - 2] && value >= k[order - 1] - tol {
        return; // at the end of the domain
    }
    let mut value = value;
    if value <= 0.5 * (k[order - 2] + k[order - 1]) && (value - k[order - 2]).abs() <= tol {
        value = k[order - 2];
    } else if (value - k[order - 1]).abs() <= tol {
        value = k[order - 1];
    }
    if value == k[order - 2] || value == k[order - 1] {
        return; // the knot is there already
    }
    let degree = order - 1;
    // `ON_InsertSingleKnot` on copies of the span's 2*degree knots and order CVs.
    if value < k[order - 2] || k[order - 1] <= value {
        return;
    }
    let mut nk: Vec<f64> = k[..2 * degree].to_vec();
    nk.insert(degree, value);
    let mut nc: Vec<f64> = cvs[si * dim..(si + order) * dim].to_vec();
    nc.extend_from_slice(&cvs[(si + degree) * dim..(si + order) * dim]);
    let lower = value - nk[degree - 1] <= nk[2 * degree] - value;
    for r in 0..degree {
        let (k0, k1) = (nk[degree - 1 - r], nk[2 * degree - r]);
        let (a0, a1) = if lower {
            let a1 = (value - k0) / (k1 - k0);
            (1.0 - a1, a1)
        } else {
            let a0 = (k1 - value) / (k1 - k0);
            (a0, 1.0 - a0)
        };
        let (at, prev) = (degree - r, degree - r - 1);
        for d in 0..dim {
            nc[at * dim + d] = nc[at * dim + d] * a1 + nc[prev * dim + d] * a0;
        }
    }
    knots.insert(si + degree, value);
    cvs.splice(si * dim..(si + order) * dim, nc);
}

/// `surface.Knots{U,V}.InsertKnot(value, 1)`: `ON_NurbsSurface::InsertKnot(dir, value, 1)`.
fn insert_knot(srf: &mut Surface, dir: usize, value: f64) -> Result<(), Declined> {
    let order = srf.order[dir];
    if !surface_is_valid(srf) {
        return Ok(());
    }
    let (t0, t1) = domain(srf, dir);
    if value < t0 || value > t1 || value == t0 || value == t1 {
        return Ok(());
    }
    if order > 2 && knots_are_periodic(order, srf.count[dir], &srf.knots[dir]) {
        return Err(Declined); // `ON_NurbsCurve::InsertKnot` restores periodic form: not ported
    }
    // `ON_Internal_ConvertToCurve`: one control point per row (dir 0) or column (dir 1).
    let n = size(srf);
    let c1 = srf.count[1];
    let other = srf.count[1 - dir];
    let dim = n * other;
    let mut rows = Vec::with_capacity(srf.cvs.len());
    for a in 0..srf.count[dir] {
        for b in 0..other {
            let (i, j) = if dir == 0 { (a, b) } else { (b, a) };
            rows.extend_from_slice(&srf.cvs[(i * c1 + j) * n..(i * c1 + j + 1) * n]);
        }
    }
    let hint = span_index(order, srf.count[dir], &srf.knots[dir], value, 0, 0);
    let mut knots = srf.knots[dir].clone();
    insert_one_knot(order, dim, &mut knots, &mut rows, value, hint);
    let count = rows.len() / dim;
    if count == srf.count[dir] {
        return Ok(());
    }
    if count.checked_mul(other).is_none_or(|t| t > MAX_SURFACE_CVS) {
        return Err(Declined);
    }
    let mut cvs = vec![0.0; count * other * n];
    let c1 = if dir == 0 { c1 } else { count };
    for a in 0..count {
        for b in 0..other {
            let (i, j) = if dir == 0 { (a, b) } else { (b, a) };
            let from = (a * other + b) * n;
            cvs[(i * c1 + j) * n..(i * c1 + j + 1) * n].copy_from_slice(&rows[from..from + n]);
        }
    }
    srf.knots[dir] = knots;
    srf.count[dir] = count;
    srf.cvs = cvs;
    Ok(())
}

/// One round of `_refine_surface`: a knot at the middle of every span, U then V.
fn refine(srf: &mut Surface) -> Result<(), Declined> {
    for dir in 0..2 {
        let mut vals = srf.knots[dir].clone();
        vals.sort_by(f64::total_cmp);
        vals.dedup();
        for m in vals.windows(2).map(|w| 0.5 * (w[0] + w[1])) {
            insert_knot(srf, dir, m)?;
        }
    }
    Ok(())
}

/// CPython's `sum` of floats (an int 0 start): Neumaier-compensated.
fn py_sum(xs: &[f64]) -> f64 {
    let Some((&first, rest)) = xs.split_first() else {
        return 0.0;
    };
    let mut f = 0.0 + first;
    let mut c = 0.0;
    for &x in rest {
        let t = f + x;
        if f.abs() >= x.abs() {
            c += (f - t) + x;
        } else {
            c += (x - t) + f;
        }
        f = t;
    }
    if c != 0.0 && c.is_finite() {
        f += c;
    }
    f
}

/// Python's `min(max(g, lo), hi)`.
fn clamp(g: f64, lo: f64, hi: f64) -> f64 {
    let m = if lo > g { lo } else { g };
    if hi < m {
        hi
    } else {
        m
    }
}

/// `_offset_control_net(work, distance)`.
fn control_net(work: &Surface, distance: f64) -> Result<Surface, Declined> {
    let mut off = work.clone();
    let [nu, nv] = work.count;
    let (deg_u, deg_v) = (work.order[0] - 1, work.order[1] - 1);
    let (du, dv) = (domain(work, 0), domain(work, 1));
    let n = size(work);
    for i in 0..nu {
        let gu = py_sum(&work.knots[0][i..i + deg_u]) / deg_u as f64;
        let gu = clamp(gu, du.0, du.1);
        for j in 0..nv {
            let gv = py_sum(&work.knots[1][j..j + deg_v]) / deg_v as f64;
            let gv = clamp(gv, dv.0, dv.1);
            let normal = normal_at(work, gu, gv)?;
            let c = cv(work, i, j);
            // `GetControlPoint`: homogeneous, w = 1 when not rational; `or 1.0` for w = 0.
            let w = if n == 4 && c[3] != 0.0 { c[3] } else { 1.0 };
            let moved: P3 = std::array::from_fn(|k| (c[k] / w + distance * normal[k]) * w);
            let at = (i * nv + j) * n;
            if n == 4 {
                // `SetCV` of a rational surface stores the homogeneous point.
                off.cvs[at..at + 3].copy_from_slice(&moved);
                off.cvs[at + 3] = w;
            } else {
                // Non-rational: multiplied by 1/w = 1.
                off.cvs[at..at + 3].copy_from_slice(&moved);
            }
        }
    }
    Ok(off)
}

/// `_offset_surface_quality(ns, off, distance)`: the worst deviation and whether the
/// normal turned over.
fn quality(ns: &Surface, off: &Surface, distance: f64) -> Result<(f64, bool), Declined> {
    let (du, dv) = (domain(ns, 0), domain(ns, 1));
    let mut worst = 0.0;
    let mut folded = false;
    for a in 0..=GRID {
        let u = du.0 + (du.1 - du.0) * a as f64 / GRID as f64;
        for b in 0..=GRID {
            let v = dv.0 + (dv.1 - dv.0) * b as f64 / GRID as f64;
            let p = point_at(ns, u, v)?;
            let n = normal_at(ns, u, v)?;
            let truth = [
                p[0] + distance * n[0],
                p[1] + distance * n[1],
                p[2] + distance * n[2],
            ];
            let q = point_at(off, u, v)?;
            let d = dist3(q, truth);
            if d > worst {
                worst = d;
            }
            let no = normal_at(off, u, v)?;
            if no[0] * n[0] + no[1] * n[1] + no[2] * n[2] < 0.0 {
                folded = true;
            }
        }
    }
    Ok((worst, folded))
}

/// What `surface_offset`'s loop gives.
#[derive(Debug, PartialEq)]
enum Outcome {
    /// The offset surface, its deviation, and whether it meets the tolerance.
    Fit(Surface, f64, bool),
    /// The offset inverts the surface.
    Folded,
}

/// The refinement loop of `surface_offset` on the record's surface.
fn decide(
    ns: &Surface,
    distance: f64,
    tolerance: f64,
    max_refine: u32,
) -> Result<Outcome, Declined> {
    if !surface_is_valid(ns)
        || !ns
            .knots
            .iter()
            .flatten()
            .chain(&ns.cvs)
            .all(|v| v.is_finite())
    {
        return Err(Declined);
    }
    let mut work = ns.clone();
    let mut best = None;
    for level in 0..=max_refine {
        if level > 0 {
            refine(&mut work)?;
        }
        let off = control_net(&work, distance)?;
        let (dev, folded) = quality(ns, &off, distance)?;
        if folded {
            return Ok(Outcome::Folded);
        }
        if dev <= tolerance {
            return Ok(Outcome::Fit(off, dev, true));
        }
        best = Some((off, dev));
    }
    let (off, dev) = best.ok_or(Declined)?;
    Ok(Outcome::Fit(off, dev, false))
}

/// `offset.surface_offset`'s refinement loop for the `NurbsSurface` encoded in `record`
/// (the bytes of `surface.Encode()["data"]`).
///
/// Returns 0 (within the tolerance) or APPROXIMATE (5, the last round's surface) with the
/// offset surface written to `out_record` as an `ON_NurbsSurface` archive record (what
/// `CommonObject.Decode` reads), its length in `out_len` and the deviation in
/// `out_deviation`; FOLDED (3) when the offset turns the surface inside out; ROOM (4), with
/// only `out_len` written (the length needed), when the record does not fit in
/// `record_capacity`; NOT_RECOGNISED (1) when Python must run it; or a negative error.
/// Nothing is written otherwise.
///
/// # Safety
///
/// `record` must reference `record_len` readable bytes, `out_record` `record_capacity`
/// writable bytes, `out_len` one writable `u64` and `out_deviation` one writable double.
/// An output that overlaps an input or another output, or a null or misaligned pointer, is
/// refused (-1).
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_surface_offset(
    record: *const u8,
    record_len: usize,
    distance: f64,
    tolerance: f64,
    max_refine: u32,
    out_record: *mut u8,
    record_capacity: usize,
    out_len: *mut u64,
    out_deviation: *mut f64,
) -> i32 {
    if out_len.is_null() || out_deviation.is_null() {
        return ERR_NULL;
    }
    let ranges = (
        region(record, record_len),
        region(out_record.cast_const(), record_capacity),
        region(out_len.cast_const(), 1),
        region(out_deviation.cast_const(), 1),
    );
    let (Some(rr), Some(or), Some(lr), Some(dr)) = ranges else {
        return ERR_NULL;
    };
    if !separate(&[rr], &[or, lr, dr]) {
        return ERR_NULL;
    }
    if !distance.is_finite()
        || distance.abs() < 1e-15
        || tolerance.is_nan()
        || max_refine > MAX_REFINE
    {
        return ERR_INPUT;
    }
    let Some(data) = (unsafe { crate::checked_slice(record, record_len) }) else {
        return ERR_NULL;
    };
    let Some(ns) = read_surface(data) else {
        return NOT_RECOGNISED;
    };
    let (off, dev, within) = match decide(&ns, distance, tolerance, max_refine) {
        Ok(Outcome::Fit(off, dev, within)) => (off, dev, within),
        Ok(Outcome::Folded) => return FOLDED,
        Err(Declined) => return NOT_RECOGNISED,
    };
    let bytes = surface_record(&off);
    let Some(out) =
        (unsafe { crate::checked_slice_mut(out_record, bytes.len().min(record_capacity)) })
    else {
        return ERR_NULL;
    };
    unsafe {
        *out_len = bytes.len() as u64;
    }
    if bytes.len() > record_capacity {
        return ROOM;
    }
    out.copy_from_slice(&bytes);
    unsafe {
        *out_deviation = dev;
    }
    if within {
        0
    } else {
        APPROXIMATE
    }
}

/// The most grid nodes one call evaluates.
pub const MAX_GRID_NODES: usize = 1 << 20;

/// `surface.PointAt(u, v)` and `surface.NormalAt(u, v)` at every node of `us` x `vs` for the
/// `NurbsSurface` encoded in `record`: the display grid of `bridge._build_grid`.
///
/// Returns 0 with the points and normals (`x, y, z` each, node `(i, j)` at `i * nv + j`) in
/// `out_points` / `out_normals`; NOT_RECOGNISED (1) when Python must evaluate it; -2 for a
/// non-finite parameter, -3 for more than `MAX_GRID_NODES` nodes or a short buffer, or -1
/// for a null, misaligned or overlapping pointer. Nothing is written unless it returns 0.
///
/// # Safety
///
/// `record` must reference `record_len` readable bytes, `us` `nu` and `vs` `nv` readable
/// doubles, and `out_points` / `out_normals` `capacity` writable doubles each.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_surface_grid(
    record: *const u8,
    record_len: usize,
    us: *const f64,
    nu: usize,
    vs: *const f64,
    nv: usize,
    out_points: *mut f64,
    out_normals: *mut f64,
    capacity: usize,
) -> i32 {
    let Some(nodes) = nu.checked_mul(nv) else {
        return ERR_SIZE;
    };
    if nodes > MAX_GRID_NODES || capacity < 3 * nodes {
        return ERR_SIZE;
    }
    let ranges = (
        region(record, record_len),
        region(us, nu),
        region(vs, nv),
        region(out_points.cast_const(), capacity),
        region(out_normals.cast_const(), capacity),
    );
    let (Some(rr), Some(ur), Some(vr), Some(pr), Some(qr)) = ranges else {
        return ERR_NULL;
    };
    if !separate(&[rr, ur, vr], &[pr, qr]) {
        return ERR_NULL;
    }
    let (Some(data), Some(us), Some(vs)) = (
        (unsafe { crate::checked_slice(record, record_len) }),
        (unsafe { crate::checked_slice(us, nu) }),
        (unsafe { crate::checked_slice(vs, nv) }),
    ) else {
        return ERR_NULL;
    };
    if !us.iter().chain(vs).all(|x| x.is_finite()) {
        return ERR_INPUT;
    }
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
    let mut points = Vec::with_capacity(3 * nodes);
    let mut normals = Vec::with_capacity(3 * nodes);
    for &u in us {
        for &v in vs {
            let (Ok(p), Ok(n)) = (point_at(&srf, u, v), normal_at(&srf, u, v)) else {
                return NOT_RECOGNISED;
            };
            points.extend(p);
            normals.extend(n);
        }
    }
    let (Some(po), Some(no)) = (
        (unsafe { crate::checked_slice_mut(out_points, 3 * nodes) }),
        (unsafe { crate::checked_slice_mut(out_normals, 3 * nodes) }),
    ) else {
        return ERR_NULL;
    };
    po.copy_from_slice(&points);
    no.copy_from_slice(&normals);
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A bilinear (or higher) surface from rows of points, clamped uniform knots.
    fn surface(order: [usize; 2], rows: &[Vec<P3>]) -> Surface {
        let count = [rows.len(), rows[0].len()];
        let knots = std::array::from_fn(|d| {
            let (o, c) = (order[d], count[d]);
            (0..c + o - 2)
                .map(|i| (i.saturating_sub(o - 2)).min(c - o + 1) as f64)
                .collect()
        });
        Surface {
            rational: false,
            order,
            count,
            knots,
            cvs: rows.iter().flatten().flatten().copied().collect(),
        }
    }

    fn plane() -> Surface {
        let rows: Vec<Vec<P3>> = (0..4)
            .map(|i| {
                (0..3)
                    .map(|j| [i as f64 * 2.0, j as f64 * 3.0, 1.0])
                    .collect()
            })
            .collect();
        surface([3, 2], &rows)
    }

    /// A cubic-by-cubic bump.
    fn bump(height: f64) -> Surface {
        let rows: Vec<Vec<P3>> = (0..6)
            .map(|i| {
                (0..6)
                    .map(|j| {
                        let h = if (2..4).contains(&i) && (2..4).contains(&j) {
                            height
                        } else {
                            0.0
                        };
                        [i as f64, j as f64, h]
                    })
                    .collect()
            })
            .collect();
        surface([4, 4], &rows)
    }

    #[test]
    fn untrusted_surface_shapes_refuse_without_writing() {
        let rows: Vec<Vec<P3>> = (0..33)
            .map(|i| vec![[i as f64, 0.0, 0.0], [i as f64, 1.0, 0.0]])
            .collect();
        let high = surface([33, 2], &rows);
        assert!(read_surface(&surface_record(&high)).is_none());
        let mut reversed = plane();
        reversed.knots[0].reverse();
        let mut collapsed = plane();
        collapsed.knots[1].fill(0.0);
        for bad in [high, reversed, collapsed] {
            let record = surface_record(&bad);
            let result = call(&record, 0.3, 0.01, 0, 1024);
            assert_eq!(result.0, NOT_RECOGNISED);
            assert!(result.1.iter().all(|&b| b == 0xA5));
            assert_eq!((result.2, result.3), (7, -9.0));
            let mut points = [-9.0; 3];
            let mut normals = [-9.0; 3];
            let u = [0.3];
            let v = [0.7];
            let code = unsafe {
                narwhal_pure_core_surface_grid(
                    record.as_ptr(),
                    record.len(),
                    u.as_ptr(),
                    1,
                    v.as_ptr(),
                    1,
                    points.as_mut_ptr(),
                    normals.as_mut_ptr(),
                    3,
                )
            };
            assert_eq!(code, NOT_RECOGNISED);
            assert_eq!((points, normals), ([-9.0; 3], [-9.0; 3]));
        }
    }

    #[test]
    fn overflowed_surface_evaluations_are_declined() {
        let s = plane();
        assert!(point_at(&s, f64::MAX, 0.7).is_err());
    }

    #[test]
    fn python_sum_is_compensated() {
        assert_eq!(py_sum(&[0.1, 0.2, 0.3]), 0.6);
        assert_eq!(py_sum(&[1e16, 1.0, -1e16]), 1.0);
        assert_eq!(py_sum(&[-0.0]).to_bits(), 0.0_f64.to_bits());
        assert_eq!(py_sum(&[]), 0.0);
    }

    #[test]
    fn a_plane_evaluates_exactly_and_offsets_exactly() {
        let p = plane();
        assert_eq!(point_at(&p, 0.5, 0.5).unwrap()[1..], [1.5, 1.0]);
        assert_eq!(normal_at(&p, 0.5, 0.5).unwrap(), [0.0, 0.0, 1.0]);
        match decide(&p, 0.25, 1e-9, 6).unwrap() {
            Outcome::Fit(off, dev, true) => {
                assert!(dev < 1e-12);
                assert!(off.cvs.chunks(3).all(|c| (c[2] - 1.25).abs() < 1e-12));
            }
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn a_collapsed_edge_takes_the_singular_normal() {
        // Row 0 collapses to one point: Ds is zero along u = 0.
        let rows = vec![
            vec![[0.0, 0.0, 0.0]; 3],
            vec![[1.0, -1.0, 0.0], [1.0, 0.0, 0.2], [1.0, 1.0, 0.0]],
            vec![[2.0, -2.0, 0.0], [2.0, 0.0, 0.4], [2.0, 2.0, 0.0]],
        ];
        let s = surface([3, 3], &rows);
        let n = normal_at(&s, 0.0, 0.5).unwrap();
        assert!((length(n) - 1.0).abs() < 1e-12, "{n:?}");
        // The limit normal continues the nearby ones.
        let near = normal_at(&s, 1e-6, 0.5).unwrap();
        assert!(dot(n, near) > 0.999, "{n:?} {near:?}");
    }

    #[test]
    fn knot_insertion_keeps_the_shape() {
        let before = bump(1.5);
        let mut after = before.clone();
        insert_knot(&mut after, 0, 1.25).unwrap();
        insert_knot(&mut after, 1, 0.5).unwrap();
        assert_eq!(after.count, [7, 7]);
        assert!(after.knots[0].contains(&1.25) && after.knots[1].contains(&0.5));
        for a in 0..=10 {
            for b in 0..=10 {
                let (u, v) = (3.0 * a as f64 / 10.0, 3.0 * b as f64 / 10.0);
                let (p, q) = (
                    point_at(&before, u, v).unwrap(),
                    point_at(&after, u, v).unwrap(),
                );
                assert!(dist3(p, q) < 1e-12, "{u} {v}: {p:?} {q:?}");
            }
        }
        // At an existing knot or outside the domain nothing changes.
        let mut same = before.clone();
        insert_knot(&mut same, 0, 1.0).unwrap();
        insert_knot(&mut same, 0, 7.0).unwrap();
        assert_eq!(same, before);
    }

    #[test]
    fn a_bump_offset_meets_the_tolerance_by_refining() {
        let s = bump(1.0);
        let Outcome::Fit(off, dev, true) = decide(&s, 0.3, 1e-2, 6).unwrap() else {
            panic!("no fit");
        };
        assert!(dev <= 1e-2 && off.count[0] > s.count[0]);
        // An independent check of the deviation, off the quality grid.
        for a in 0..=37 {
            for b in 0..=37 {
                let (u, v) = (3.0 * a as f64 / 37.0, 3.0 * b as f64 / 37.0);
                let p = point_at(&s, u, v).unwrap();
                let n = normal_at(&s, u, v).unwrap();
                let q = point_at(&off, u, v).unwrap();
                let truth = [p[0] + 0.3 * n[0], p[1] + 0.3 * n[1], p[2] + 0.3 * n[2]];
                assert!(dist3(q, truth) < 2e-2, "{u} {v}");
            }
        }
        // A capped round count reports the last round's deviation instead.
        let Outcome::Fit(_, coarse, false) = decide(&s, 0.3, 1e-6, 1).unwrap() else {
            panic!("expected an approximation");
        };
        assert!(coarse > 1e-6);
    }

    #[test]
    fn an_offset_past_the_curvature_folds() {
        assert_eq!(decide(&bump(2.0), -3.0, 1e-2, 6), Ok(Outcome::Folded));
    }

    fn call(
        record: &[u8],
        distance: f64,
        tolerance: f64,
        max_refine: u32,
        cap: usize,
    ) -> (i32, Vec<u8>, u64, f64) {
        let mut out = vec![0xA5_u8; cap];
        let mut len = 7_u64;
        let mut dev = -9.0;
        let code = unsafe {
            narwhal_pure_core_surface_offset(
                record.as_ptr(),
                record.len(),
                distance,
                tolerance,
                max_refine,
                out.as_mut_ptr(),
                out.len(),
                &mut len,
                &mut dev,
            )
        };
        (code, out, len, dev)
    }

    #[test]
    fn the_export_answers_and_refuses_before_writing() {
        let record = surface_record(&bump(1.0));
        let (code, out, len, dev) = call(&record, 0.3, 1e-2, 6, 1 << 16);
        assert_eq!(code, 0);
        let Outcome::Fit(off, want, true) = decide(&bump(1.0), 0.3, 1e-2, 6).unwrap() else {
            panic!();
        };
        assert_eq!(read_surface(&out[..len as usize]), Some(off));
        assert_eq!(dev, want);
        assert_eq!(call(&record, -3.0, 1e-2, 6, 1 << 16).0, FOLDED);
        assert_eq!(call(&record, 0.3, 1e-6, 1, 1 << 16).0, APPROXIMATE);
        let untouched = |r: &(i32, Vec<u8>, u64, f64)| {
            r.1.iter().all(|&b| b == 0xA5) && r.2 == 7 && r.3 == -9.0
        };
        for (r, want) in [
            (call(&record, 0.0, 1e-2, 6, 1 << 16), ERR_INPUT),
            (call(&record, f64::NAN, 1e-2, 6, 1 << 16), ERR_INPUT),
            (call(&record, 0.3, f64::NAN, 6, 1 << 16), ERR_INPUT),
            (call(&record, 0.3, 1e-2, MAX_REFINE + 1, 1 << 16), ERR_INPUT),
            (
                call(&record[..record.len() - 1], 0.3, 1e-2, 6, 1 << 16),
                NOT_RECOGNISED,
            ),
            (call(b"not a record", 0.3, 1e-2, 6, 1 << 16), NOT_RECOGNISED),
        ] {
            assert_eq!(r.0, want);
            assert!(untouched(&r));
        }
        // Too little room: only the length, so the caller can size the buffer.
        let small = call(&record, 0.3, 1e-2, 6, 16);
        assert_eq!((small.0, small.2), (ROOM, len));
        assert!(small.1.iter().all(|&b| b == 0xA5) && small.3 == -9.0);
        // An output overlapping the record is refused.
        let mut shared = record.clone();
        let mut l = 7_u64;
        let mut d = -9.0;
        let code = unsafe {
            narwhal_pure_core_surface_offset(
                shared.as_ptr(),
                shared.len(),
                0.3,
                1e-2,
                6,
                shared.as_mut_ptr(),
                shared.len(),
                &mut l,
                &mut d,
            )
        };
        assert_eq!(code, ERR_NULL);
        assert!(shared == record && l == 7 && d == -9.0);
    }

    #[test]
    fn the_grid_export_gives_point_at_and_normal_at() {
        let s = bump(1.0);
        let record = surface_record(&s);
        let us = [0.0, 0.7, 1.5, 3.0];
        let vs = [0.0, 1.1, 3.0];
        let grid = |us: &[f64], vs: &[f64], cap: usize, rec: &[u8]| {
            let mut p = vec![-9.0; cap.max(1)];
            let mut n = vec![-9.0; cap.max(1)];
            let code = unsafe {
                narwhal_pure_core_surface_grid(
                    rec.as_ptr(),
                    rec.len(),
                    us.as_ptr(),
                    us.len(),
                    vs.as_ptr(),
                    vs.len(),
                    p.as_mut_ptr(),
                    n.as_mut_ptr(),
                    cap,
                )
            };
            (code, p, n)
        };
        let (code, p, n) = grid(&us, &vs, 36, &record);
        assert_eq!(code, 0);
        for (i, &u) in us.iter().enumerate() {
            for (j, &v) in vs.iter().enumerate() {
                let at = 3 * (i * vs.len() + j);
                assert_eq!(p[at..at + 3], point_at(&s, u, v).unwrap());
                assert_eq!(n[at..at + 3], normal_at(&s, u, v).unwrap());
            }
        }
        let untouched = |r: &(i32, Vec<f64>, Vec<f64>)| r.1.iter().chain(&r.2).all(|&x| x == -9.0);
        for (r, want) in [
            (grid(&us, &vs, 35, &record), ERR_SIZE),
            (grid(&us, &[0.0, f64::NAN, 1.0], 36, &record), ERR_INPUT),
            (grid(&us, &vs, 36, b"not a record"), NOT_RECOGNISED),
            (
                grid(&us, &vs, 36, &record[..record.len() - 1]),
                NOT_RECOGNISED,
            ),
        ] {
            assert_eq!(r.0, want);
            assert!(untouched(&r));
        }
    }

    #[test]
    fn malformed_records_never_abort() {
        let record = surface_record(&bump(1.0));
        let mut state = 0x2026_1126_u64;
        for _ in 0..300 {
            let mut bytes = record.clone();
            for _ in 0..4 {
                state = state
                    .wrapping_mul(6364136223846793005)
                    .wrapping_add(1442695040888963407);
                let at = (state >> 33) as usize % bytes.len();
                bytes[at] ^= (state >> 20) as u8 | 1;
            }
            let _ = call(&bytes, 0.3, 1e-1, 2, 1 << 12);
        }
    }

    #[test]
    fn a_record_round_trips() {
        let s = bump(1.0);
        assert_eq!(read_surface(&surface_record(&s)), Some(s));
    }
}
