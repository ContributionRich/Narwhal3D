//! Rebuild's least-squares curve fit: `rebuild._fit`'s numeric part.
//!
//! Given arc-length samples and their normalised parameters, `_fit` evaluates the
//! B-spline basis at each parameter (`_basis`, Cox-de Boor), assembles one row per
//! sample coordinate, forms the normal equations (`_least_squares`) and solves them by
//! Gaussian elimination with partial pivoting (`_solve`). An open fit pins the end
//! points (and, with PreserveTangents, the end tangent directions); a closed fit
//! wraps `p` control points onto a uniform periodic knot vector.
//!
//! Every step mirrors the Python in its operation order and keeps Python's semantics:
//! `min`/`max` return the first argument on ties, `max(..., key=abs)` returns the
//! first largest pivot, `if v` skips `0.0` and `-0.0`, float `%` follows the divisor's
//! sign, and the back substitution's `sum()` is CPython's compensated sum. So the
//! control points are bit-identical to the Python's. Python builds the `NurbsCurve`
//! from them; this module never touches document geometry.

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
/// The normal equations are singular: Python's "use fewer points" refusal.
const ERR_SINGULAR: i32 = -4;
/// The fit has more unknowns than [`MAX_UNKNOWNS`]: refused before any allocation.
const ERR_TOO_LARGE: i32 = -5;
const MAX_POINTS: usize = 4096;
const MAX_SAMPLES: usize = 1 << 20;
/// Most unknowns a fit may solve for. The normal matrix and its augmented copy are
/// `k * k` doubles each, so this bounds the solve at about 268 MB (a 1365-point
/// closed fit). Larger requests are refused by name, not diverted to Python, whose
/// fit would allocate as much and more.
const MAX_UNKNOWNS: usize = 4096;

type P3 = [f64; 3];

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

/// Python float `x % y` (`float_rem`): C `fmod`, moved to the divisor's sign.
fn py_mod(x: f64, y: f64) -> f64 {
    let m = x % y;
    if m != 0.0 {
        if (y < 0.0) != (m < 0.0) {
            m + y
        } else {
            m
        }
    } else {
        0.0_f64.copysign(y)
    }
}

/// CPython 3.12+'s `sum()` of floats from the int start 0: Neumaier compensated
/// summation, the compensation added once at the end when nonzero and finite.
fn py_sum(values: impl Iterator<Item = f64>) -> f64 {
    let mut values = values;
    let Some(first) = values.next() else {
        return 0.0;
    };
    let mut total = 0.0 + first; // `0 + value`: exact, except -0.0 becomes 0.0
    let mut compensation = 0.0;
    for value in values {
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

/// Python `max(range(len(v)), key=lambda i: v[i])`: the index of the FIRST largest.
fn first_largest(values: impl Iterator<Item = f64>) -> usize {
    let mut best = (0, f64::NEG_INFINITY);
    for (i, v) in values.enumerate() {
        if i == 0 || v > best.1 {
            best = (i, v);
        }
    }
    best.0
}

/// `rebuild._basis`: all `ncp` basis values at `t` over the full knot vector.
fn basis(full: &[f64], p: usize, t: f64, ncp: usize) -> Vec<f64> {
    let (lo, hi) = (full[p], full[ncp]);
    let t = py_min(py_max(t, lo), hi);
    let span = (p..ncp)
        .find(|&k| full[k] <= t && t < full[k + 1])
        .unwrap_or(ncp - 1);
    let mut left = vec![0.0; p + 1];
    let mut right = vec![0.0; p + 1];
    let mut nb = vec![0.0; p + 1];
    nb[0] = 1.0;
    for j in 1..=p {
        left[j] = t - full[span + 1 - j];
        right[j] = full[span + j] - t;
        let mut saved = 0.0;
        for r in 0..j {
            let denom = right[r + 1] + left[j - r];
            let tmp = if denom != 0.0 { nb[r] / denom } else { 0.0 };
            nb[r] = saved + right[r + 1] * tmp;
            saved = left[j - r] * tmp;
        }
        nb[j] = saved;
    }
    let mut out = vec![0.0; ncp];
    out[span - p..=span].copy_from_slice(&nb);
    out
}

/// `rebuild._least_squares` and `_solve`, fed one row at a time.
struct Normal {
    k: usize,
    ata: Vec<f64>,
    atb: Vec<f64>,
    nz: Vec<(usize, f64)>,
}

impl Normal {
    fn new(k: usize) -> Self {
        Self {
            k,
            ata: vec![0.0; k * k],
            atb: vec![0.0; k],
            nz: Vec::with_capacity(k),
        }
    }

    /// One `(row, y)` of `_least_squares`'s loop.
    fn add(&mut self, row: &[f64], y: f64) {
        self.nz.clear();
        self.nz.extend(
            row.iter()
                .enumerate()
                .filter(|(_, &v)| v != 0.0)
                .map(|(i, &v)| (i, v)),
        );
        for &(i, vi) in &self.nz {
            self.atb[i] += vi * y;
            for &(j, vj) in &self.nz {
                self.ata[i * self.k + j] += vi * vj;
            }
        }
    }

    /// `_solve(ata, atb)`; `None` where it raises (a singular system).
    fn solve(self) -> Option<Vec<f64>> {
        let n = self.k;
        let w = n + 1;
        let mut m = vec![0.0; n * w];
        for r in 0..n {
            m[r * w..r * w + n].copy_from_slice(&self.ata[r * n..(r + 1) * n]);
            m[r * w + n] = self.atb[r];
        }
        for c in 0..n {
            let piv = c + first_largest((c..n).map(|r| m[r * w + c].abs()));
            if m[piv * w + c].abs() < 1e-300 {
                return None;
            }
            if piv != c {
                for k in 0..w {
                    m.swap(c * w + k, piv * w + k);
                }
            }
            for r in c + 1..n {
                let f = m[r * w + c] / m[c * w + c];
                if f != 0.0 {
                    for k in c..w {
                        m[r * w + k] -= f * m[c * w + k];
                    }
                }
            }
        }
        let mut x = vec![0.0; n];
        for r in (0..n).rev() {
            let tail = py_sum((r + 1..n).map(|k| m[r * w + k] * x[k]));
            x[r] = (m[r * w + n] - tail) / m[r * w + r];
        }
        Some(x)
    }
}

/// `_fit` for a closed curve: `n` distinct control points, wrapped.
fn fit_closed(points: &[P3], params: &[f64], n: usize, p: usize) -> Option<(Vec<P3>, Vec<f64>)> {
    let ncp = n + p;
    let full: Vec<f64> = (-1..(ncp + p) as i64).map(|i| i as f64).collect();
    let (lo, hi) = (full[p], full[ncp]);
    let mut normal = Normal::new(3 * n);
    let mut row = vec![0.0; 3 * n];
    for (q, &un) in points.iter().zip(params) {
        let u = lo + (hi - lo) * py_mod(un, 1.0);
        let b = basis(&full, p, u, ncp);
        let mut folded = vec![0.0; n];
        for (j, &v) in b.iter().enumerate() {
            folded[j % n] += v;
        }
        for axis in 0..3 {
            row.fill(0.0);
            row[axis * n..(axis + 1) * n].copy_from_slice(&folded);
            normal.add(&row, q[axis]);
        }
    }
    let x = normal.solve()?;
    let mut ctrl: Vec<P3> = (0..n).map(|j| [x[j], x[n + j], x[2 * n + j]]).collect();
    ctrl.extend_from_within(..p);
    Some((ctrl, full[1..full.len() - 1].to_vec()))
}

/// The open fit's fixed data: its end points and, when constrained, end tangents.
struct Ends {
    start: P3,
    end: P3,
    tangents: Option<(P3, P3)>,
}

/// `_fit` for an open curve: the end points pinned, optionally the end tangents.
fn fit_open(
    points: &[P3],
    params: &[f64],
    n: usize,
    p: usize,
    ends: &Ends,
) -> Option<(Vec<P3>, Vec<f64>)> {
    let inner = n - p - 1;
    let mut full = vec![0.0; p + 1];
    full.extend((0..inner).map(|i| (i + 1) as f64 / (inner + 1) as f64));
    full.extend(std::iter::repeat_n(1.0, p + 1));
    let (start, end) = (ends.start, ends.end);
    let free: Vec<usize> = if ends.tangents.is_some() {
        (2..n - 2).collect()
    } else {
        (1..n - 1).collect()
    };
    let off = if ends.tangents.is_some() { 2 } else { 0 };
    let nk = off + 3 * free.len();
    let mut normal = Normal::new(nk);
    let mut row = vec![0.0; nk];
    for (q, &u) in points.iter().zip(params) {
        let b = basis(&full, p, u, n);
        for axis in 0..3 {
            row.fill(0.0);
            let mut known = b[0] * start[axis] + b[n - 1] * end[axis];
            if let Some((t0, t1)) = ends.tangents {
                known += b[1] * start[axis] + b[n - 2] * end[axis];
                row[0] = b[1] * t0[axis];
                row[1] = -b[n - 2] * t1[axis];
            }
            for (fi, &j) in free.iter().enumerate() {
                row[off + 3 * fi + axis] = b[j];
            }
            normal.add(&row, q[axis] - known);
        }
    }
    let x = normal.solve()?;
    let mut ctrl = vec![[0.0; 3]; n];
    ctrl[0] = start;
    ctrl[n - 1] = end;
    if let Some((t0, t1)) = ends.tangents {
        ctrl[1] = std::array::from_fn(|i| start[i] + x[0] * t0[i]);
        ctrl[n - 2] = std::array::from_fn(|i| end[i] - x[1] * t1[i]);
    }
    for (fi, &j) in free.iter().enumerate() {
        ctrl[j] = std::array::from_fn(|i| x[off + 3 * fi + i]);
    }
    Some((ctrl, full[1..full.len() - 1].to_vec()))
}

/// The control points and rhino3dm knots of `rebuild._fit`.
///
/// `points` holds `sample_count` xyz samples and `params` their normalised
/// parameters. A closed fit (`closed == 1`) writes `point_count + degree` control
/// points; an open one writes `point_count`, with `ends` holding the start and end
/// points then, when `tangent == 1`, the start and end tangents (12 values). Knots
/// are rhino3dm's (`cv_count + degree - 1`). `out_ctrl` must hold `(point_count +
/// degree) * 3` values and `out_knots` `point_count + 2 * degree - 1`. Returns
/// `ERR_SINGULAR` where the Python raises its singular-fit `ValueError`, and
/// `ERR_TOO_LARGE`, before allocating, for more than [`MAX_UNKNOWNS`] unknowns. A
/// closed fit needs `3 <= point_count` and `degree <= point_count`.
///
/// # Safety
/// Every pointer is valid for the counts above.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_rebuild_fit(
    points: *const f64,
    params: *const f64,
    sample_count: usize,
    point_count: usize,
    degree: usize,
    closed: u8,
    tangent: u8,
    ends: *const f64,
    out_ctrl: *mut f64,
    out_knots: *mut f64,
    out_cv_count: *mut usize,
) -> i32 {
    if out_cv_count.is_null() {
        return ERR_NULL;
    }
    *out_cv_count = 0;
    let (n, p) = (point_count, degree);
    if closed > 1
        || tangent > 1
        || p < 1
        || n > MAX_POINTS
        || sample_count > MAX_SAMPLES
        || sample_count == 0
        || (closed == 1 && (n < 3 || p > n))
        || (closed == 0 && n < p + 1)
        || (tangent == 1 && (closed == 1 || n < 4 || p < 2))
    {
        return ERR_INPUT;
    }
    // `_fit`'s unknowns: 3 per control point (closed), or 3 per free point plus the
    // two tangent lengths (open).
    let unknowns = if closed == 1 {
        3 * n
    } else if tangent == 1 {
        2 + 3 * (n - 4)
    } else {
        3 * (n - 2)
    };
    if unknowns > MAX_UNKNOWNS {
        return ERR_TOO_LARGE;
    }
    let end_count = if closed == 1 {
        0
    } else {
        6 + 6 * usize::from(tangent)
    };
    let (Some(flat), Some(params), Some(ends)) = (
        crate::checked_slice(points, sample_count * 3),
        crate::checked_slice(params, sample_count),
        crate::checked_slice(ends, end_count),
    ) else {
        return ERR_NULL;
    };
    if !flat.iter().chain(params).chain(ends).all(|v| v.is_finite()) {
        return ERR_INPUT;
    }
    let pts: Vec<P3> = flat.chunks_exact(3).map(|c| [c[0], c[1], c[2]]).collect();
    let result = if closed == 1 {
        fit_closed(&pts, params, n, p)
    } else {
        let v = |i: usize| [ends[3 * i], ends[3 * i + 1], ends[3 * i + 2]];
        let fixed = Ends {
            start: v(0),
            end: v(1),
            tangents: (tangent == 1).then(|| (v(2), v(3))),
        };
        fit_open(&pts, params, n, p, &fixed)
    };
    let Some((ctrl, knots)) = result else {
        return ERR_SINGULAR;
    };
    if !ctrl.iter().flatten().all(|v| v.is_finite()) {
        return ERR_INPUT;
    }
    let (Some(out_c), Some(out_k)) = (
        crate::checked_slice_mut(out_ctrl, ctrl.len() * 3),
        crate::checked_slice_mut(out_knots, knots.len()),
    ) else {
        return ERR_NULL;
    };
    for (slot, c) in out_c.chunks_exact_mut(3).zip(&ctrl) {
        slot.copy_from_slice(c);
    }
    out_k.copy_from_slice(&knots);
    *out_cv_count = ctrl.len();
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_semantics() {
        assert_eq!(py_mod(1.0, 1.0), 0.0);
        assert_eq!(py_mod(-0.25, 1.0), 0.75);
        assert!(py_mod(-0.0, 1.0).is_sign_positive());
        assert_eq!(py_sum([1e100, 1.0, -1e100, 1.0].into_iter()), 2.0);
        assert_eq!(py_sum(std::iter::empty()), 0.0);
        assert!(py_max(0.0, -0.0).is_sign_positive());
        assert_eq!(py_min(1.0, f64::NAN), 1.0);
        // `max(..., key=abs)` keeps the first of equal pivots
        assert_eq!(first_largest([2.0, 5.0, 5.0, 1.0].into_iter()), 1);
        assert_eq!(first_largest([3.0, 3.0].into_iter()), 0);
    }

    #[test]
    fn basis_is_a_partition_of_unity_and_clamped_at_the_ends() {
        let full = [0.0, 0.0, 0.0, 0.0, 0.5, 1.0, 1.0, 1.0, 1.0];
        for &t in &[0.0, 0.2, 0.5, 0.77, 1.0] {
            let b = basis(&full, 3, t, 5);
            assert!((b.iter().sum::<f64>() - 1.0).abs() < 1e-15, "{t}: {b:?}");
        }
        assert_eq!(basis(&full, 3, 0.0, 5)[0], 1.0);
        assert_eq!(basis(&full, 3, 1.0, 5)[4], 1.0);
        assert_eq!(basis(&full, 3, -1.0, 5), basis(&full, 3, 0.0, 5));
    }

    #[test]
    fn solve_pivots_and_refuses_a_singular_system() {
        let mut normal = Normal::new(2);
        normal.ata = vec![0.0, 2.0, 3.0, 1.0];
        normal.atb = vec![4.0, 5.0];
        let x = normal.solve().unwrap();
        assert_eq!(x, vec![1.0, 2.0]);
        let mut singular = Normal::new(2);
        singular.ata = vec![1.0, 2.0, 2.0, 4.0];
        singular.atb = vec![1.0, 2.0];
        assert!(singular.solve().is_none());
    }

    #[test]
    fn an_open_fit_reproduces_a_cubic_it_can_represent() {
        // Samples of a straight line x = 3u; a 4-point cubic fit holds it exactly.
        let params: Vec<f64> = (0..=20).map(|i| i as f64 / 20.0).collect();
        let pts: Vec<f64> = params.iter().flat_map(|&u| [3.0 * u, 0.0, 0.0]).collect();
        let ends = [0.0, 0.0, 0.0, 3.0, 0.0, 0.0];
        let mut ctrl = vec![f64::NAN; 12];
        let mut knots = vec![f64::NAN; 6];
        let mut count = 0;
        let code = unsafe {
            narwhal_pure_core_rebuild_fit(
                pts.as_ptr(),
                params.as_ptr(),
                params.len(),
                4,
                3,
                0,
                0,
                ends.as_ptr(),
                ctrl.as_mut_ptr(),
                knots.as_mut_ptr(),
                &mut count,
            )
        };
        assert_eq!((code, count), (0, 4));
        assert_eq!(knots, vec![0.0, 0.0, 0.0, 1.0, 1.0, 1.0]);
        for (i, c) in ctrl.chunks_exact(3).enumerate() {
            assert!((c[0] - i as f64).abs() < 1e-12 && c[1] == 0.0, "{c:?}");
        }
    }

    fn closed_call(n: usize, p: usize) -> (i32, usize) {
        let params: Vec<f64> = (0..40).map(|i| i as f64 / 40.0).collect();
        let pts: Vec<f64> = params
            .iter()
            .flat_map(|&u| {
                let a = u * std::f64::consts::TAU;
                [a.cos(), a.sin(), 0.0]
            })
            .collect();
        let mut ctrl = vec![0.0; (n + p) * 3];
        let mut knots = vec![0.0; n + 2 * p];
        let mut count = 0;
        let code = unsafe {
            narwhal_pure_core_rebuild_fit(
                pts.as_ptr(),
                params.as_ptr(),
                params.len(),
                n,
                p,
                1,
                0,
                std::ptr::null(),
                ctrl.as_mut_ptr(),
                knots.as_mut_ptr(),
                &mut count,
            )
        };
        (code, count)
    }

    #[test]
    fn a_closed_fit_needs_degree_at_most_point_count() {
        assert_eq!(closed_call(3, 5).0, ERR_INPUT, "degree above point count");
        assert_eq!(closed_call(2, 2).0, ERR_INPUT, "fewer than three points");
        assert_eq!(
            closed_call(3, 3),
            (0, 6),
            "degree == point count still fits"
        );
        assert_eq!(closed_call(8, 3), (0, 11));
    }

    #[test]
    fn a_fit_past_the_unknown_bound_is_refused_before_allocating() {
        // 1366 closed points need 4098 unknowns: refused without building a matrix.
        let pts = [0.0; 3];
        let params = [0.0];
        let mut count = 0;
        let code = unsafe {
            narwhal_pure_core_rebuild_fit(
                pts.as_ptr(),
                params.as_ptr(),
                1,
                1366,
                3,
                1,
                0,
                std::ptr::null(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut count,
            )
        };
        assert_eq!((code, count), (ERR_TOO_LARGE, 0));
        // the bound itself reaches the solve (40 samples cannot determine 1365 points)
        assert_eq!(closed_call(1365, 3).0, ERR_SINGULAR);
    }

    #[test]
    fn bad_input_is_refused() {
        let params = [0.0, 1.0];
        let pts = [0.0; 6];
        let ends = [0.0; 6];
        let mut ctrl = [0.0; 30];
        let mut knots = [0.0; 30];
        let mut count = 0;
        let call = |n: usize,
                    closed: u8,
                    tangent: u8,
                    count: &mut usize,
                    ctrl: &mut [f64],
                    knots: &mut [f64]| unsafe {
            narwhal_pure_core_rebuild_fit(
                pts.as_ptr(),
                params.as_ptr(),
                2,
                n,
                3,
                closed,
                tangent,
                ends.as_ptr(),
                ctrl.as_mut_ptr(),
                knots.as_mut_ptr(),
                count,
            )
        };
        assert_eq!(call(3, 0, 0, &mut count, &mut ctrl, &mut knots), ERR_INPUT);
        assert_eq!(call(2, 1, 0, &mut count, &mut ctrl, &mut knots), ERR_INPUT);
        assert_eq!(call(4, 1, 1, &mut count, &mut ctrl, &mut knots), ERR_INPUT);
        assert_eq!(call(4, 2, 0, &mut count, &mut ctrl, &mut knots), ERR_INPUT);
    }
}
