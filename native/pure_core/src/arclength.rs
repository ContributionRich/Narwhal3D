//! Point at arc length on a NURBS curve: `arclength.PreparedLength.parameter_at`.
//!
//! Mirrors `arclength._invert` on a prepared table: the range check and clamps, the
//! linear-in-t seed, and up to 60 bisection-guarded Newton steps on
//! `length_to(t) - s`, where `length_to` is one prefix sum of the table plus the
//! Gauss-Legendre integral of the partial span (`_length_to`, `_gauss_span`) and the
//! Newton slope is `|C'(t)|` (`_speed`). The curve is evaluated with the Near snap's
//! port of openNURBS (`DerivativeAt(t, 1)`), and every arithmetic step repeats the
//! Python's order, so each parameter is bit-identical to the Python's. The arithmetic is
//! `+ - * /` and `sqrt` only (no libm transcendental), but parity is measured on
//! Linux/glibc; it is unmeasured against the Windows and macOS CPython runtimes.
//!
//! The table (interior knots, prefix sums, total) and the Gauss-Legendre nodes and
//! weights come from Python as computed there, so nothing is re-derived here. A
//! length the Python would refuse (outside the curve), or one whose Newton reaches
//! `_length_to`'s end branch (which takes the full `length_of`), is reported back
//! for the Python to answer. The parameters are positions along an existing curve;
//! this never creates or changes document geometry.

use crate::nearest::Nurbs;

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const MAX_CVS: usize = 1 << 20;
const CV_STRIDE: usize = 4;
/// `arclength._PARAM_EPS`.
const PARAM_EPS: f64 = 1e-12;
const NEWTON_STEPS: usize = 60;

/// Per-length status: answered here.
pub const DONE: u8 = 0;
/// Per-length status: the Python must answer (a refusal, or the end branch).
pub const PYTHON: u8 = 1;

/// Python `max(a, b)`: `a` unless `b > a`.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python `min(a, b)`: `a` unless `b < a`.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// `bisect.bisect_left`.
fn bisect_left(values: &[f64], x: f64) -> usize {
    values.partition_point(|&v| v < x)
}

struct Prepared<'c, 'a> {
    curve: &'c Nurbs<'a>,
    t0: f64,
    t1: f64,
    knots: &'c [f64],
    cum: &'c [f64],
    total: f64,
    nodes: &'c [f64],
    weights: &'c [f64],
}

impl Prepared<'_, '_> {
    /// `_speed`: `|C'(t)|`, `None` where openNURBS cannot evaluate.
    fn speed(&self, t: f64) -> Option<f64> {
        let [_, v] = self.curve.point_and_tangent(t);
        let s = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
        s.is_finite().then_some(s)
    }

    /// `_gauss_span`.
    fn gauss_span(&self, lo: f64, hi: f64) -> Option<f64> {
        let half = 0.5 * (hi - lo);
        let mid = 0.5 * (hi + lo);
        let mut acc = 0.0;
        for (&x, &w) in self.nodes.iter().zip(self.weights) {
            acc += w * self.speed(mid + half * x)?;
        }
        Some(half * acc)
    }

    /// The table's `cuts[k]`: `[t0] + knots + [t1]`.
    fn cut(&self, k: usize) -> f64 {
        if k == 0 {
            self.t0
        } else {
            self.knots[k - 1]
        }
    }

    /// `_length_to`; `None` for its end branch, which the Python answers.
    fn length_to(&self, t: f64) -> Option<f64> {
        if !(t - self.t0 > PARAM_EPS && (t - self.t1).abs() > PARAM_EPS) {
            return None;
        }
        let k = bisect_left(self.knots, t - PARAM_EPS);
        Some(self.cum[k] + self.gauss_span(self.cut(k), t)?)
    }

    /// `_invert`; `None` where the Python must answer.
    fn invert(&self, s: f64, tol: f64) -> Option<f64> {
        let total = self.total;
        if s < -tol || s > total + tol {
            return None; // the Python raises ValueError, by name
        }
        let s = py_min(py_max(s, 0.0), total);
        if s <= tol {
            return Some(self.t0);
        }
        if s >= total - tol {
            return Some(self.t1);
        }
        let (mut lo, mut hi) = (self.t0, self.t1);
        let mut t = self.t0 + (self.t1 - self.t0) * (s / total);
        for _ in 0..NEWTON_STEPS {
            let f = self.length_to(t)? - s;
            if f.abs() <= tol * 1e-3 {
                break;
            }
            if f > 0.0 {
                hi = t;
            } else {
                lo = t;
            }
            let speed = self.speed(t)?;
            let step = if speed > PARAM_EPS {
                t - f / speed
            } else {
                0.5 * (lo + hi)
            };
            t = if lo < step && step < hi {
                step
            } else {
                0.5 * (lo + hi)
            };
        }
        Some(t)
    }
}

/// `PreparedLength.parameter_at(s, tolerance)` for each of `lengths`.
///
/// The curve is passed in openNURBS form (as for `narwhal_pure_core_nearest_on_nurbs`)
/// with its `Domain` as `t0`/`t1`. The prepared table is `knots` (the interior knots
/// of `_prepare`), `cum` (`knot_count + 2` prefix sums) and `total`; `nodes` and
/// `weights` are `_gauss_legendre`'s. Writes each parameter to `out_params` and its
/// status ([`DONE`] or [`PYTHON`]) to `out_status`.
///
/// # Safety
/// Every input pointer is readable for its count (`cvs` for `cv_count * 4`, `cum`
/// for `table_knot_count + 2`, `nodes` and `weights` for `node_count`); `out_params`
/// and `out_status` are writable for `length_count` values.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_parameters_at_lengths(
    order: usize,
    knots: *const f64,
    knot_count: usize,
    cvs: *const f64,
    cv_count: usize,
    rational: u8,
    t0: f64,
    t1: f64,
    table_knots: *const f64,
    table_knot_count: usize,
    cum: *const f64,
    total: f64,
    nodes: *const f64,
    weights: *const f64,
    node_count: usize,
    tolerance: f64,
    lengths: *const f64,
    length_count: usize,
    out_params: *mut f64,
    out_status: *mut u8,
) -> i32 {
    if cv_count > MAX_CVS || rational > 1 || node_count == 0 {
        return ERR_INPUT;
    }
    if ![t0, t1, total, tolerance].iter().all(|v| v.is_finite()) {
        return ERR_INPUT;
    }
    let (
        Some(curve_knots),
        Some(cv_values),
        Some(table),
        Some(cum),
        Some(nodes),
        Some(weights),
        Some(lengths),
    ) = (
        crate::checked_slice(knots, knot_count),
        crate::checked_slice(cvs, cv_count * CV_STRIDE),
        crate::checked_slice(table_knots, table_knot_count),
        crate::checked_slice(cum, table_knot_count + 2),
        crate::checked_slice(nodes, node_count),
        crate::checked_slice(weights, node_count),
        crate::checked_slice(lengths, length_count),
    )
    else {
        return ERR_NULL;
    };
    if !curve_knots
        .iter()
        .chain(cv_values)
        .chain(table)
        .chain(cum)
        .chain(nodes)
        .chain(weights)
        .all(|v| v.is_finite())
        || table.windows(2).any(|w| w[1] < w[0])
    {
        return ERR_INPUT;
    }
    let Some(curve) = Nurbs::new(order, curve_knots, cv_values, rational == 1) else {
        return ERR_INPUT;
    };
    let (Some(out_t), Some(out_s)) = (
        crate::checked_slice_mut(out_params, length_count),
        crate::checked_slice_mut(out_status, length_count),
    ) else {
        return ERR_NULL;
    };
    let prepared = Prepared {
        curve: &curve,
        t0,
        t1,
        knots: table,
        cum,
        total,
        nodes,
        weights,
    };
    for ((&s, t), status) in lengths.iter().zip(out_t).zip(out_s) {
        match prepared.invert(s, tolerance) {
            Some(value) => {
                *t = value;
                *status = DONE;
            }
            None => {
                *t = f64::NAN;
                *status = PYTHON;
            }
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Two-point Gauss-Legendre: exact for the cubic |C'| of a straight line.
    fn rule() -> (Vec<f64>, Vec<f64>) {
        let x = 1.0 / 3.0_f64.sqrt();
        (vec![-x, x], vec![1.0, 1.0])
    }

    fn run(cvs: &[f64], lengths: &[f64], tol: f64) -> (i32, Vec<f64>, Vec<u8>) {
        // A clamped cubic with one interior knot at 1 on [0, 2].
        let knots = [0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 2.0];
        let (nodes, weights) = rule();
        let table = [1.0];
        let curve = Nurbs::new(4, &knots, cvs, false).unwrap();
        let p = Prepared {
            curve: &curve,
            t0: 0.0,
            t1: 2.0,
            knots: &table,
            cum: &[0.0; 3],
            total: 0.0,
            nodes: &nodes,
            weights: &weights,
        };
        let first = p.gauss_span(0.0, 1.0).unwrap();
        let cum = [0.0, first, first + p.gauss_span(1.0, 2.0).unwrap()];
        let mut out = vec![0.0; lengths.len()];
        let mut status = vec![9; lengths.len()];
        let code = unsafe {
            narwhal_pure_core_parameters_at_lengths(
                4,
                knots.as_ptr(),
                knots.len(),
                cvs.as_ptr(),
                cvs.len() / 4,
                0,
                0.0,
                2.0,
                table.as_ptr(),
                table.len(),
                cum.as_ptr(),
                cum[2],
                nodes.as_ptr(),
                weights.as_ptr(),
                nodes.len(),
                tol,
                lengths.as_ptr(),
                lengths.len(),
                out.as_mut_ptr(),
                status.as_mut_ptr(),
            )
        };
        (code, out, status)
    }

    #[test]
    fn a_uniform_line_inverts_to_proportional_parameters() {
        // Control points 0, 2/3, 4/3, 2, ... spaced so the line is traversed at unit
        // speed in x: length s is parameter s (to tolerance).
        let xs = [0.0, 1.0 / 3.0, 1.0, 5.0 / 3.0, 2.0];
        let cvs: Vec<f64> = xs.iter().flat_map(|&x| [x, 0.0, 0.0, 1.0]).collect();
        let (code, out, status) = run(&cvs, &[0.0, 0.5, 1.25, 2.0], 1e-9);
        assert_eq!(code, 0);
        assert_eq!(status, vec![DONE; 4]);
        assert_eq!(out[0], 0.0);
        assert_eq!(out[3], 2.0);
        assert!(
            (out[1] - 0.5).abs() < 1e-9 && (out[2] - 1.25).abs() < 1e-9,
            "{out:?}"
        );
    }

    #[test]
    fn out_of_range_lengths_go_back_to_python() {
        let cvs: Vec<f64> = (0..5).flat_map(|i| [i as f64, 0.0, 0.0, 1.0]).collect();
        let (code, out, status) = run(&cvs, &[-1.0, 1.0, 99.0], 1e-9);
        assert_eq!(code, 0);
        assert_eq!(status, vec![PYTHON, DONE, PYTHON]);
        assert!(out[0].is_nan() && out[2].is_nan());
    }

    #[test]
    fn python_helpers_keep_python_semantics() {
        assert_eq!(bisect_left(&[1.0, 2.0, 2.0, 3.0], 2.0), 1);
        assert_eq!(bisect_left(&[], 2.0), 0);
        assert!(py_max(0.0, -0.0).is_sign_positive());
        assert_eq!(py_min(1.0, f64::NAN), 1.0);
    }
}
