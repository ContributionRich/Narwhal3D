//! `offset._compact_offset_boundaries`: the free-form curve offset that keeps tangent
//! boundaries, for a curve with an interior knot of full multiplicity (a C0 corner), an
//! offset cusp, or a kinked closed seam.
//!
//! The source is trimmed at each boundary (`ON_NurbsCurve::Trim`, `nurbs_trim`); each
//! corner's offset tangent lines meet at a Sharp apex (`ON_Intersect` for two lines), which
//! moves the pieces' end points, adds two straight segments at an outer corner, or trims both
//! pieces back to the apex at an inner corner (`_inner_trim_param`). Each piece is then
//! fitted on its own by interpolation with its one-sided end tangents imposed, checked
//! against the TRUE offset, and the clamped cubic pieces are joined with triple knots.
//!
//! The arithmetic is `offset.py`'s, with Python float semantics, and rhino3dm's calls are the
//! openNURBS ports in `offset` and `nurbs_trim`, so the result is bit-identical to the Python
//! route wherever rhino3dm does not fuse a multiply-add. Anything not decided exactly is
//! Declined and Python runs the route.

use crate::interpolate;
use crate::nearest::Nurbs;
use crate::nurbs_trim::{line_line, Curve};
use crate::offset::{
    dot, is_closed, is_valid, point_at, set_domain, tangent_at, Declined, Fit, Offset, Outcome, P3,
};
use crate::python_norm::dist3;

/// The per-piece interpolation counts of `_compact_offset_boundaries`.
const COUNTS: [usize; 10] = [3, 5, 8, 12, 16, 24, 36, 48, 64, 96];
/// More pieces than this go to Python: each costs up to ten fits and their checks.
pub(crate) const MAX_PIECES: usize = 4096;
/// Bisection steps of `_inner_trim_param`.
const TRIM_BISECTIONS: usize = 80;

fn add(a: P3, b: P3) -> P3 {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}

fn sub(a: P3, b: P3) -> P3 {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

fn scale(a: P3, s: f64) -> P3 {
    [a[0] * s, a[1] * s, a[2] * s]
}

/// `_dist_point_to_line`.
fn dist_point_to_line(point: P3, origin: P3, tangent: P3) -> f64 {
    let v = sub(point, origin);
    dist3(v, scale(tangent, dot(v, tangent)))
}

fn finite(p: P3) -> bool {
    p.iter().all(|v| v.is_finite())
}

/// Python's `min(a, b)` / `max(a, b)` on floats: the first unless the second is
/// strictly smaller / larger.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// A fitted piece: control points (weight 1) and openNURBS knots.
struct Piece {
    cvs: Vec<P3>,
    knots: Vec<f64>,
}

impl Piece {
    fn domain(&self) -> (f64, f64) {
        (self.knots[2], self.knots[self.cvs.len() - 1])
    }

    fn set_domain(&mut self, t0: f64, t1: f64) {
        set_domain(4, self.cvs.len(), &mut self.knots, t0, t1);
    }
}

/// `_line_piece(a, b)`: the segment as an exact cubic on `[0, |b - a|]`.
fn line_piece(a: P3, b: P3) -> Piece {
    let mut piece = Piece {
        cvs: (0..4)
            .map(|k| add(a, scale(sub(b, a), k as f64 / 3.0)))
            .collect(),
        knots: vec![0.0, 0.0, 0.0, 1.0, 1.0, 1.0],
    };
    piece.set_domain(0.0, dist3(a, b));
    piece
}

/// `_inner_trim_param`: Ok(None) where Python returns None.
fn inner_trim_param(
    span: &Offset,
    point: P3,
    end: bool,
    tolerance: f64,
) -> Result<Option<f64>, Declined> {
    let (t0, t1) = span.curve.domain();
    let edge = if end { t1 } else { t0 };
    let direction = tangent_at(&span.curve, edge)?;
    let step = if end { -1.0 } else { 1.0 };
    let span_len = t1 - t0;
    let side = |t: f64| -> Result<f64, Declined> { Ok(dot(sub(span.point(t)?, point), direction)) };
    let at_edge = side(edge)?;
    if at_edge == 0.0 {
        return Ok(None);
    }
    let mut w = span_len / 64.0;
    let t = loop {
        let t = edge + step * w;
        if side(t)? * at_edge <= 0.0 {
            break t;
        }
        if w >= span_len {
            return Ok(None);
        }
        w = py_min(2.0 * w, span_len);
    };
    let (mut outside, mut inside) = (edge, t);
    for _ in 0..TRIM_BISECTIONS {
        let mid = 0.5 * (outside + inside);
        if side(mid)? * at_edge > 0.0 {
            outside = mid;
        } else {
            inside = mid;
        }
    }
    if !(t0 < inside && inside < t1) {
        return Ok(None);
    }
    if dist3(span.point(inside)?, point) > tolerance * 0.2 {
        return Ok(None);
    }
    Ok(Some(inside))
}

/// One piece's fit: `(control points, knots, deviation)`, or None where no count meets
/// the tolerance.
#[allow(clippy::too_many_arguments)]
fn fit_piece(
    source: &Offset,
    start: P3,
    end: P3,
    cusp_start: bool,
    cusp_end: bool,
    tolerance: f64,
    whole: f64,
) -> Result<Option<(Piece, f64)>, Declined> {
    let (d0, d1) = source.curve.domain();
    let direction = if source.speed(0.5 * (d0 + d1))? >= 0.0 {
        1.0
    } else {
        -1.0
    };
    let tangent_start = scale(tangent_at(&source.curve, d0)?, direction);
    let tangent_end = scale(tangent_at(&source.curve, d1)?, direction);
    let limit = tolerance * 0.8;
    for count in COUNTS {
        let pts = (0..=count)
            .map(|i| source.point(d0 + (d1 - d0) * i as f64 / count as f64))
            .collect::<Result<Vec<_>, _>>()?;
        if !pts.iter().all(|&p| finite(p)) {
            return Err(Declined);
        }
        // `native_interpolate.interpolate(pts, 3, "Uniform")`; where it would refuse, Python
        // runs its own interpolator.
        let (cvs, full, degree) = interpolate::solve(&pts, 3, 0).map_err(|_| Declined)?;
        if degree != 3
            || cvs.len() != pts.len()
            || full.len() != pts.len() + 4
            || !cvs.iter().all(|&p| finite(p))
            || !full.iter().all(|k| k.is_finite())
            || full.windows(2).any(|w| w[0] > w[1])
        {
            return Err(Declined);
        }
        let last = cvs.len() - 1;
        let mut piece = Piece {
            cvs,
            knots: full[1..full.len() - 1].to_vec(),
        };
        piece.set_domain(d0, d1);
        let mut kept = true;
        for (endpoint, adjacent, target, tangent, at_cusp) in [
            (0, 1, start, tangent_start, cusp_start),
            (last, last - 1, end, tangent_end, cusp_end),
        ] {
            let old_end = piece.cvs[endpoint];
            let mut arm = dot(sub(piece.cvs[adjacent], old_end), tangent);
            if at_cusp {
                arm = 0.0;
            } else if (endpoint == 0 && arm <= 0.0) || (endpoint != 0 && arm >= 0.0) {
                kept = false;
                break;
            }
            piece.cvs[endpoint] = target;
            piece.cvs[adjacent] = add(target, scale(tangent, arm));
        }
        if !kept {
            continue;
        }
        let flat: Vec<f64> = piece
            .cvs
            .iter()
            .flat_map(|p| [p[0], p[1], p[2], 1.0])
            .collect();
        let fit = Nurbs::new(4, &piece.knots, &flat, false).ok_or(Declined)?;
        let dev = source.error(&fit, 256)?;
        if dev.is_finite() && dev <= limit {
            let checks = (4096.0 * (d1 - d0) / whole).ceil();
            if !checks.is_finite() || checks > 1e6 {
                return Err(Declined);
            }
            let checks = (checks.max(0.0) as usize).max(256);
            let dev = source.error(&fit, checks)?;
            if dev.is_finite() && dev <= limit {
                return Ok(Some((piece, dev)));
            }
        }
    }
    Ok(None)
}

/// `_compact_offset_boundaries(curve, distance, n, tolerance, boundaries, cusps)`.
/// `source` is the curve `off` evaluates and `closed` its `IsClosed`.
pub(crate) fn fit(
    source: &Curve,
    off: &Offset,
    closed: bool,
    tolerance: f64,
    boundaries: &[f64],
    cusps: &[f64],
) -> Result<Outcome, Declined> {
    if boundaries.len() + 1 > MAX_PIECES {
        return Err(Declined);
    }
    let (distance, n) = (off.distance, off.n);
    let (t0, t1) = source.domain();
    let mut breaks = vec![t0];
    breaks.extend_from_slice(boundaries);
    breaks.push(t1);
    let mut spans = Vec::with_capacity(breaks.len() - 1);
    for w in breaks.windows(2) {
        let mut span = source.clone();
        if !span.trim(w[0], w[1]) {
            return Ok(Outcome::Refused);
        }
        span.view().ok_or(Declined)?;
        spans.push(span);
    }
    let offset_of = |span: &Curve| -> Result<(P3, P3), Declined> {
        let curve = span.view().ok_or(Declined)?;
        let (a, b) = curve.domain();
        let o = Offset { curve, distance, n };
        Ok((o.point(a)?, o.point(b)?))
    };
    let mut ends: Vec<[P3; 2]> = Vec::with_capacity(spans.len());
    for span in &spans {
        let (a, b) = offset_of(span)?;
        ends.push([a, b]);
    }
    let count = spans.len();
    let joins = if closed { count } else { count - 1 };
    let mut fills: Vec<Option<(P3, P3, P3)>> = vec![None; count];
    let mut trim_end: Vec<Option<f64>> = vec![None; count];
    let mut trim_start: Vec<Option<f64>> = vec![None; count];
    let band = tolerance * 0.2;
    for i in 0..joins {
        let j = (i + 1) % count;
        let (a, b) = (ends[i][1], ends[j][0]);
        let (vi, vj) = (
            spans[i].view().ok_or(Declined)?,
            spans[j].view().ok_or(Declined)?,
        );
        let u = tangent_at(&vi, vi.domain().1)?;
        let v = tangent_at(&vj, vj.domain().0)?;
        let meet = if dist3(a, b) < 1e-9 {
            Some(scale(add(a, b), 0.5))
        } else {
            let (a1, b1) = (add(a, u), add(b, v));
            line_line(a, a1, b, b1)
                .ok_or(Declined)?
                .map(|(pa, _)| add(a, scale(sub(a1, a), pa)))
        };
        let Some(meet) = meet else {
            return Ok(Outcome::Refused);
        };
        if !finite(meet)
            || dist_point_to_line(meet, a, u) > 1e-10
            || dist_point_to_line(meet, b, v) > 1e-10
        {
            return Ok(Outcome::Refused);
        }
        let vertex = point_at(&vi, vi.domain().1)?;
        if (dist3(meet, vertex) - distance.abs()).abs() > band {
            return Ok(Outcome::Refused);
        }
        if py_max(dist3(meet, a), dist3(meet, b)) <= band {
            ends[i][1] = meet;
            ends[j][0] = meet;
            continue;
        }
        let along_a = dot(sub(meet, a), u);
        let along_b = dot(sub(meet, b), v);
        if along_a >= 0.0 && along_b <= 0.0 {
            fills[i] = Some((a, meet, b));
        } else if along_a <= 0.0 && along_b >= 0.0 {
            let oi = Offset {
                curve: vi,
                distance,
                n,
            };
            let oj = Offset {
                curve: vj,
                distance,
                n,
            };
            let ta = inner_trim_param(&oi, meet, true, tolerance)?;
            let tb = inner_trim_param(&oj, meet, false, tolerance)?;
            let (Some(ta), Some(tb)) = (ta, tb) else {
                return Ok(Outcome::Refused);
            };
            trim_end[i] = Some(ta);
            trim_start[j] = Some(tb);
            ends[i][1] = meet;
            ends[j][0] = meet;
        } else {
            return Ok(Outcome::Refused);
        }
    }
    // A failed end trim leaves Python's span None, and a start trim of it then raises.
    for (span, t) in spans.iter_mut().zip(&trim_end) {
        if let Some(t) = *t {
            let start = span.domain().0;
            if !span.trim(start, t) {
                return Err(Declined);
            }
        }
    }
    for (span, t) in spans.iter_mut().zip(&trim_start) {
        if let Some(t) = *t {
            let stop = span.domain().1;
            if !span.trim(t, stop) {
                return Ok(Outcome::Refused);
            }
        }
    }

    let whole = t1 - t0;
    let mut pieces = Vec::new();
    let mut worst = 0.0;
    for (k, span) in spans.iter().enumerate() {
        let curve = span.view().ok_or(Declined)?;
        let piece_off = Offset { curve, distance, n };
        let [start, end] = ends[k];
        let fitted = fit_piece(
            &piece_off,
            start,
            end,
            cusps.contains(&breaks[k]),
            cusps.contains(&breaks[k + 1]),
            tolerance,
            whole,
        )?;
        let Some((piece, dev)) = fitted else {
            return Ok(Outcome::Refused);
        };
        worst = py_max(worst, dev);
        pieces.push(piece);
        if let Some((a, m, b)) = fills[k] {
            pieces.push(line_piece(a, m));
            pieces.push(line_piece(m, b));
        }
    }
    // Concatenate the clamped cubic nets with no fit across the joins.
    let mut s = t0;
    for piece in &mut pieces {
        let (p0, p1) = piece.domain();
        let length = p1 - p0;
        piece.set_domain(s, s + length);
        s += length;
    }
    let mut cvs = pieces[0].cvs.clone();
    let mut knots = pieces[0].knots.clone();
    for piece in &pieces[1..] {
        cvs.extend_from_slice(&piece.cvs[1..]);
        knots.extend_from_slice(&piece.knots[3..]);
    }
    let m = cvs.len();
    if !is_valid(4, m, &knots, &cvs) {
        return Ok(Outcome::Refused);
    }
    let flat: Vec<f64> = cvs.iter().flat_map(|p| [p[0], p[1], p[2], 1.0]).collect();
    let out_closed = {
        let out = Nurbs::new(4, &knots, &flat, false).ok_or(Declined)?;
        is_closed(&out, &flat, false)?
    };
    if out_closed != closed {
        return Ok(Outcome::Refused);
    }
    set_domain(4, m, &mut knots, t0, t1);
    Ok(Outcome::Fit(Fit {
        cvs,
        knots,
        deviation: worst,
    }))
}
