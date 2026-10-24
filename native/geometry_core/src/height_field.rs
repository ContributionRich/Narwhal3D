//! SPDX-License-Identifier: GPL-3.0-or-later
//!
//! Smooth bicubic height-field fill for a planar face whose boundary was lifted.
//!
//! MEASURED (bug #108, reference application): moving one vertex of a planar six-edge extrusion cap
//! out of plane gives a degree-3x3 cap whose control points stay on a regular grid over
//! the original cap plane -- only their height changes -- with every control-point row
//! along an unmoved, flat boundary side pinned exactly to the plane. That is a smooth
//! height field `z = h(x, y)` in the source plane's frame.
//!
//! OURS (algorithm): `h` is a clamped uniform bicubic B-spline over the boundary's
//! bounding rectangle. Its poles sit at the Greville abscissae, so the surface parameter
//! IS the plane coordinate (linear precision); the caller's p-curves are then exact
//! projections of the 3D edges and the only edge gap is the height error, which this
//! module minimises and reports. Heights minimise
//!
//! ```text
//!   mean_s (h(x_s, y_s) - z_s)^2  +  smoothing * area * Integral(h_xx^2 + 2 h_xy^2 + h_yy^2)
//! ```
//!
//! (scale invariant: scaling x, y and z by the same factor scales both terms equally).
//! The thin-plate term reproduces planes exactly and has no preference for bulging, which
//! is what keeps the cap from ballooning the way a free 3D plate did. The grid is refined
//! until the boundary error is within `target_error`; nothing is accepted silently above it.

// Banded linear algebra reads more clearly with explicit indices, and `!(x > 0.0)`
// deliberately treats NaN as invalid input.
#![allow(clippy::needless_range_loop, clippy::neg_cmp_op_on_partial_ord)]

pub const STATUS_OK: i32 = 0;
pub const STATUS_INVALID_INPUT: i32 = 1;
pub const STATUS_NOT_SIMPLE: i32 = 2;
pub const STATUS_NOT_CONVERGED: i32 = 3;
pub const STATUS_SOLVE_FAILED: i32 = 4;
pub const STATUS_CAPACITY: i32 = 5;

const DEGREE: usize = 3;

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct NarwhalHeightFieldV1 {
    pub u_pole_count: u32,
    pub v_pole_count: u32,
    pub pinned_sides: u32,
    pub reserved: u32,
    pub x_min: f64,
    pub x_max: f64,
    pub y_min: f64,
    pub y_max: f64,
    pub max_error: f64,
}

const _: [(); 56] = [(); std::mem::size_of::<NarwhalHeightFieldV1>()];

pub const PIN_X_MIN: u32 = 1;
pub const PIN_X_MAX: u32 = 2;
pub const PIN_Y_MIN: u32 = 4;
pub const PIN_Y_MAX: u32 = 8;

#[derive(Clone, Debug)]
pub struct HeightField {
    pub info: NarwhalHeightFieldV1,
    /// Row-major heights: index `i * v_pole_count + j`, `i` along x, `j` along y.
    pub heights: Vec<f64>,
}

#[derive(Clone, Copy)]
struct Axis {
    start: f64,
    end: f64,
    spans: usize,
}

impl Axis {
    fn poles(self) -> usize {
        self.spans + DEGREE
    }

    fn step(self) -> f64 {
        (self.end - self.start) / self.spans as f64
    }

    fn knot(self, index: isize) -> f64 {
        let clamped =
            index.clamp(DEGREE as isize, (self.spans + DEGREE) as isize) - DEGREE as isize;
        if clamped as usize == self.spans {
            self.end
        } else {
            self.start + self.step() * clamped as f64
        }
    }

    /// Span `s` (pole indices `s-3 ..= s`) containing `t`.
    fn span(self, t: f64) -> usize {
        let relative = ((t - self.start) / self.step()).floor();
        let index = if relative.is_finite() {
            relative as isize
        } else {
            0
        };
        index.clamp(0, self.spans as isize - 1) as usize + DEGREE
    }

    /// Basis values and first/second derivatives of the four non-zero functions at `t`
    /// (The NURBS Book, A2.3).
    fn basis(self, t: f64) -> (usize, [[f64; 4]; 3]) {
        let span = self.span(t);
        let knot = |offset: isize| self.knot(span as isize + offset);
        let mut ndu = [[0.0_f64; 4]; 4];
        let mut left = [0.0_f64; 4];
        let mut right = [0.0_f64; 4];
        ndu[0][0] = 1.0;
        for j in 1..=DEGREE {
            left[j] = t - knot(1 - j as isize);
            right[j] = knot(j as isize) - t;
            let mut saved = 0.0;
            for r in 0..j {
                ndu[j][r] = right[r + 1] + left[j - r];
                let temp = ndu[r][j - 1] / ndu[j][r];
                ndu[r][j] = saved + right[r + 1] * temp;
                saved = left[j - r] * temp;
            }
            ndu[j][j] = saved;
        }
        let mut ders = [[0.0_f64; 4]; 3];
        for j in 0..=DEGREE {
            ders[0][j] = ndu[j][DEGREE];
        }
        for r in 0..=DEGREE as isize {
            let mut a = [[0.0_f64; 4]; 2];
            let (mut s1, mut s2) = (0usize, 1usize);
            a[0][0] = 1.0;
            for k in 1..=2isize {
                let mut d = 0.0;
                let rk = r - k;
                let pk = DEGREE as isize - k;
                if r >= k {
                    a[s2][0] = a[s1][0] / ndu[(pk + 1) as usize][rk as usize];
                    d = a[s2][0] * ndu[rk as usize][pk as usize];
                }
                let j1 = if rk >= -1 { 1 } else { -rk };
                let j2 = if r - 1 <= pk {
                    k - 1
                } else {
                    DEGREE as isize - r
                };
                for j in j1..=j2 {
                    a[s2][j as usize] = (a[s1][j as usize] - a[s1][(j - 1) as usize])
                        / ndu[(pk + 1) as usize][(rk + j) as usize];
                    d += a[s2][j as usize] * ndu[(rk + j) as usize][pk as usize];
                }
                if r <= pk {
                    a[s2][k as usize] =
                        -a[s1][(k - 1) as usize] / ndu[(pk + 1) as usize][r as usize];
                    d += a[s2][k as usize] * ndu[r as usize][pk as usize];
                }
                ders[k as usize][r as usize] = d;
                std::mem::swap(&mut s1, &mut s2);
            }
        }
        let mut factor = DEGREE as f64;
        for row in ders.iter_mut().skip(1) {
            for value in row.iter_mut() {
                *value *= factor;
            }
            factor *= (DEGREE - 1) as f64;
        }
        (span, ders)
    }

    /// Banded Gram matrices G_k[a][b] = Integral(N_a^(k) N_b^(k)), k = 0, 1, 2,
    /// stored as `[k][a][b - a + 3]` for |a - b| <= 3.
    fn grams(self) -> Vec<[[f64; 7]; 3]> {
        const NODES: [f64; 4] = [
            -0.861_136_311_594_052_6,
            -0.339_981_043_584_856_3,
            0.339_981_043_584_856_3,
            0.861_136_311_594_052_6,
        ];
        const WEIGHTS: [f64; 4] = [
            0.347_854_845_137_453_9,
            0.652_145_154_862_546_1,
            0.652_145_154_862_546_1,
            0.347_854_845_137_453_9,
        ];
        let mut grams = vec![[[0.0_f64; 7]; 3]; self.poles()];
        for span_index in 0..self.spans {
            let a = self.start + self.step() * span_index as f64;
            let b = a + self.step();
            for (node, weight) in NODES.iter().zip(WEIGHTS) {
                let t = 0.5 * (a + b) + 0.5 * (b - a) * node;
                let w = 0.5 * (b - a) * weight;
                let (span, ders) = self.basis(t);
                for (order, values) in ders.iter().enumerate() {
                    for p in 0..4 {
                        for q in 0..4 {
                            let row = span - DEGREE + p;
                            let column = span - DEGREE + q;
                            grams[row][order][column + 3 - row] += w * values[p] * values[q];
                        }
                    }
                }
            }
        }
        grams
    }
}

struct Banded {
    size: usize,
    bandwidth: usize,
    values: Vec<f64>,
}

impl Banded {
    fn new(size: usize, bandwidth: usize) -> Self {
        Self {
            size,
            bandwidth,
            values: vec![0.0; size * (bandwidth + 1)],
        }
    }

    fn at(&mut self, row: usize, column: usize) -> &mut f64 {
        debug_assert!(column <= row && row - column <= self.bandwidth);
        &mut self.values[row * (self.bandwidth + 1) + row - column]
    }

    fn get(&self, row: usize, column: usize) -> f64 {
        self.values[row * (self.bandwidth + 1) + row - column]
    }

    /// In-place banded Cholesky factorisation.
    fn factor(&mut self) -> bool {
        let (n, bw) = (self.size, self.bandwidth);
        for row in 0..n {
            let first = row.saturating_sub(bw);
            for column in first..=row {
                let start = first.max(column.saturating_sub(bw));
                let mut sum = self.get(row, column);
                for k in start..column {
                    sum -= self.get(row, k) * self.get(column, k);
                }
                if row == column {
                    if !(sum > 0.0) || !sum.is_finite() {
                        return false;
                    }
                    *self.at(row, row) = sum.sqrt();
                } else {
                    *self.at(row, column) = sum / self.get(column, column);
                }
            }
        }
        true
    }

    /// Solves with the factor from `factor`.
    fn solve(&self, rhs: &mut [f64]) -> bool {
        let (n, bw) = (self.size, self.bandwidth);
        for row in 0..n {
            let mut sum = rhs[row];
            for k in row.saturating_sub(bw)..row {
                sum -= self.get(row, k) * rhs[k];
            }
            rhs[row] = sum / self.get(row, row);
        }
        for row in (0..n).rev() {
            let mut sum = rhs[row];
            for k in row + 1..n.min(row + bw + 1) {
                sum -= self.get(k, row) * rhs[k];
            }
            rhs[row] = sum / self.get(row, row);
        }
        rhs.iter().all(|value| value.is_finite())
    }
}

fn orientation(a: [f64; 2], b: [f64; 2], c: [f64; 2]) -> f64 {
    (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
}

fn segments_cross(a: [f64; 2], b: [f64; 2], c: [f64; 2], d: [f64; 2], epsilon: f64) -> bool {
    let (d1, d2) = (orientation(c, d, a), orientation(c, d, b));
    let (d3, d4) = (orientation(a, b, c), orientation(a, b, d));
    if ((d1 > epsilon && d2 < -epsilon) || (d1 < -epsilon && d2 > epsilon))
        && ((d3 > epsilon && d4 < -epsilon) || (d3 < -epsilon && d4 > epsilon))
    {
        return true;
    }
    let near = |p: [f64; 2], q: [f64; 2], r: [f64; 2], o: f64| {
        o.abs() <= epsilon
            && p[0].min(q[0]) - 1e-12 <= r[0]
            && r[0] <= p[0].max(q[0]) + 1e-12
            && p[1].min(q[1]) - 1e-12 <= r[1]
            && r[1] <= p[1].max(q[1]) + 1e-12
    };
    near(c, d, a, d1) || near(c, d, b, d2) || near(a, b, c, d3) || near(a, b, d, d4)
}

/// True when the closed XY polygon through `points` does not touch itself.
pub fn is_simple_polygon(points: &[[f64; 2]], scale: f64) -> bool {
    let n = points.len();
    if n < 3 {
        return false;
    }
    let epsilon = scale * scale * 1.0e-14;
    for i in 0..n {
        let (a, b) = (points[i], points[(i + 1) % n]);
        for j in i + 2..n {
            if i == 0 && j == n - 1 {
                continue;
            }
            let (c, d) = (points[j], points[(j + 1) % n]);
            if a[0].max(b[0]) < c[0].min(d[0])
                || c[0].max(d[0]) < a[0].min(b[0])
                || a[1].max(b[1]) < c[1].min(d[1])
                || c[1].max(d[1]) < a[1].min(b[1])
            {
                continue;
            }
            if segments_cross(a, b, c, d, epsilon) {
                return false;
            }
        }
    }
    true
}

/// Spans along the longer side of the first grid tried. Coarser grids reach the boundary
/// tolerance sooner but leave the interior visibly stiffer than the thin-plate limit.
const START_SPANS: usize = 24;

/// Maximum iterated-Tikhonov passes per grid before refining it.
const MAX_PASSES: usize = 64;

fn sample_stencil(u: Axis, v: Axis, x: f64, y: f64) -> ([usize; 16], [f64; 16]) {
    let nv = v.poles();
    let (su, bu) = u.basis(x);
    let (sv, bv) = v.basis(y);
    let mut indices = [0usize; 16];
    let mut values = [0.0f64; 16];
    for p in 0..4 {
        for q in 0..4 {
            indices[p * 4 + q] = (su - DEGREE + p) * nv + sv - DEGREE + q;
            values[p * 4 + q] = bu[0][p] * bv[0][q];
        }
    }
    (indices, values)
}

/// Minimises the smoothed objective, then repeats the solve on the remaining boundary
/// residual (iterated Tikhonov). Each pass reuses one factorisation, so the boundary is
/// matched far more tightly than a single solve could without making the system
/// ill-conditioned -- the limit is the minimum-energy interpolant on this grid.
/// Returns the heights and the final maximum boundary error.
fn solve_heights(
    samples: &[[f64; 3]],
    u: Axis,
    v: Axis,
    smoothing: f64,
    pinned: u32,
    target_error: f64,
) -> Option<(Vec<f64>, f64)> {
    let (nu, nv) = (u.poles(), v.poles());
    let n = nu * nv;
    let bandwidth = DEGREE * nv + DEGREE;
    let mut matrix = Banded::new(n, bandwidth);
    let weight = 1.0 / samples.len() as f64;
    let stencils: Vec<([usize; 16], [f64; 16])> = samples
        .iter()
        .map(|sample| sample_stencil(u, v, sample[0], sample[1]))
        .collect();
    for (indices, values) in &stencils {
        for a in 0..16 {
            for b in 0..16 {
                if indices[b] <= indices[a] {
                    *matrix.at(indices[a], indices[b]) += weight * values[a] * values[b];
                }
            }
        }
    }
    let area = (u.end - u.start) * (v.end - v.start);
    let energy = smoothing * area;
    let (gu, gv) = (u.grams(), v.grams());
    for i in 0..nu {
        for j in 0..nv {
            let row = i * nv + j;
            for di in -3isize..=3 {
                let k = i as isize + di;
                if k < 0 || k >= nu as isize {
                    continue;
                }
                for dj in -3isize..=3 {
                    let l = j as isize + dj;
                    if l < 0 || l >= nv as isize {
                        continue;
                    }
                    let column = k as usize * nv + l as usize;
                    if column > row {
                        continue;
                    }
                    let (bi, bj) = ((di + 3) as usize, (dj + 3) as usize);
                    let value = gu[i][2][bi] * gv[j][0][bj]
                        + 2.0 * gu[i][1][bi] * gv[j][1][bj]
                        + gu[i][0][bi] * gv[j][2][bj];
                    *matrix.at(row, column) += energy * value;
                }
            }
        }
    }
    let is_pinned = |i: usize, j: usize| {
        (pinned & PIN_X_MIN != 0 && i == 0)
            || (pinned & PIN_X_MAX != 0 && i == nu - 1)
            || (pinned & PIN_Y_MIN != 0 && j == 0)
            || (pinned & PIN_Y_MAX != 0 && j == nv - 1)
    };
    for i in 0..nu {
        for j in 0..nv {
            if !is_pinned(i, j) {
                continue;
            }
            let index = i * nv + j;
            for column in index.saturating_sub(bandwidth)..index {
                *matrix.at(index, column) = 0.0;
            }
            for row in index + 1..n.min(index + bandwidth + 1) {
                *matrix.at(row, index) = 0.0;
            }
            *matrix.at(index, index) = 1.0;
        }
    }
    if !matrix.factor() {
        return None;
    }
    let mut heights = vec![0.0; n];
    let mut residual: Vec<f64> = samples.iter().map(|sample| sample[2]).collect();
    let mut max_error = residual.iter().fold(0.0_f64, |m, r| m.max(r.abs()));
    for _ in 0..MAX_PASSES {
        if max_error <= target_error {
            break;
        }
        let mut rhs = vec![0.0; n];
        for ((indices, values), r) in stencils.iter().zip(&residual) {
            for a in 0..16 {
                rhs[indices[a]] += weight * values[a] * r;
            }
        }
        for i in 0..nu {
            for j in 0..nv {
                if is_pinned(i, j) {
                    rhs[i * nv + j] = 0.0;
                }
            }
        }
        if !matrix.solve(&mut rhs) {
            return None;
        }
        for (height, delta) in heights.iter_mut().zip(&rhs) {
            *height += delta;
        }
        let previous = max_error;
        max_error = 0.0;
        for (((indices, values), sample), r) in
            stencils.iter().zip(samples).zip(residual.iter_mut())
        {
            let value: f64 = (0..16).map(|a| values[a] * heights[indices[a]]).sum();
            *r = sample[2] - value;
            max_error = max_error.max(r.abs());
        }
        if max_error > previous * 0.999 {
            break; // stalled: the grid cannot represent the remainder; refine it
        }
    }
    Some((heights, max_error))
}

#[cfg(test)]
fn evaluate(heights: &[f64], u: Axis, v: Axis, x: f64, y: f64) -> f64 {
    let nv = v.poles();
    let (su, bu) = u.basis(x);
    let (sv, bv) = v.basis(y);
    let mut value = 0.0;
    for p in 0..4 {
        for q in 0..4 {
            value += bu[0][p] * bv[0][q] * heights[(su - DEGREE + p) * nv + sv - DEGREE + q];
        }
    }
    value
}

/// Fits a height field to closed-loop boundary samples `(x, y, z)` given in the source
/// plane's frame (z = height above it), in loop order without repeating the first point.
pub fn fit(
    samples: &[[f64; 3]],
    smoothing: f64,
    target_error: f64,
    max_spans: u32,
) -> Result<HeightField, (i32, Option<HeightField>)> {
    if samples.len() < 8
        || !(smoothing > 0.0)
        || !(target_error > 0.0)
        || max_spans < 4
        || samples.iter().flatten().any(|value| !value.is_finite())
    {
        return Err((STATUS_INVALID_INPUT, None));
    }
    let (mut x0, mut x1, mut y0, mut y1) = (f64::MAX, f64::MIN, f64::MAX, f64::MIN);
    for sample in samples {
        x0 = x0.min(sample[0]);
        x1 = x1.max(sample[0]);
        y0 = y0.min(sample[1]);
        y1 = y1.max(sample[1]);
    }
    let diagonal = (x1 - x0).hypot(y1 - y0);
    if !(diagonal > 0.0) || (x1 - x0) <= diagonal * 1.0e-6 || (y1 - y0) <= diagonal * 1.0e-6 {
        return Err((STATUS_INVALID_INPUT, None));
    }
    let outline: Vec<[f64; 2]> = samples.iter().map(|s| [s[0], s[1]]).collect();
    if !is_simple_polygon(&outline, diagonal) {
        return Err((STATUS_NOT_SIMPLE, None));
    }
    let flat = diagonal * 1.0e-12;
    let mut pinned = 0;
    for (bit, axis, value) in [
        (PIN_X_MIN, 0, x0),
        (PIN_X_MAX, 0, x1),
        (PIN_Y_MIN, 1, y0),
        (PIN_Y_MAX, 1, y1),
    ] {
        let on_side: Vec<&[f64; 3]> = samples
            .iter()
            .filter(|sample| (sample[axis] - value).abs() <= flat)
            .collect();
        if on_side.len() >= 3 && on_side.iter().all(|sample| sample[2].abs() <= flat) {
            pinned |= bit;
        }
    }
    let longest = (x1 - x0).max(y1 - y0);
    let spans_for =
        |length: f64, base: usize| ((base as f64 * length / longest).round() as usize).max(4);
    let mut base = (START_SPANS as u32).min(max_spans) as usize;
    let mut best: Option<HeightField> = None;
    loop {
        let u = Axis {
            start: x0,
            end: x1,
            spans: spans_for(x1 - x0, base).min(max_spans as usize),
        };
        let v = Axis {
            start: y0,
            end: y1,
            spans: spans_for(y1 - y0, base).min(max_spans as usize),
        };
        let (heights, max_error) = solve_heights(samples, u, v, smoothing, pinned, target_error)
            .ok_or((STATUS_SOLVE_FAILED, best.clone()))?;
        let result = HeightField {
            info: NarwhalHeightFieldV1 {
                u_pole_count: u.poles() as u32,
                v_pole_count: v.poles() as u32,
                pinned_sides: pinned,
                reserved: 0,
                x_min: x0,
                x_max: x1,
                y_min: y0,
                y_max: y1,
                max_error,
            },
            heights,
        };
        if max_error <= target_error {
            return Ok(result);
        }
        let exhausted = base >= max_spans as usize;
        best = Some(result);
        if exhausted {
            return Err((STATUS_NOT_CONVERGED, best));
        }
        base = (base * 2).min(max_spans as usize);
    }
}

/// C ABI handed to the C++ adapter as a function pointer (deliberately not an exported
/// symbol). `samples` is `sample_count` packed `(x, y, z)` triples. On `STATUS_OK` (and on
/// `STATUS_NOT_CONVERGED`, for diagnostics) `result` and `heights` hold the fit.
///
/// # Safety
/// `samples` must point to `3 * sample_count` doubles, `heights` to `capacity` doubles and
/// `result` to one writable `NarwhalHeightFieldV1`.
pub unsafe extern "C" fn fit_height_field_ffi(
    samples: *const f64,
    sample_count: usize,
    smoothing: f64,
    target_error: f64,
    max_spans: u32,
    heights: *mut f64,
    capacity: usize,
    result: *mut NarwhalHeightFieldV1,
) -> i32 {
    if samples.is_null()
        || heights.is_null()
        || result.is_null()
        || sample_count == 0
        || sample_count > usize::MAX / 3
    {
        return STATUS_INVALID_INPUT;
    }
    // SAFETY: the caller guarantees `samples` holds `3 * sample_count` doubles.
    let flat = unsafe { std::slice::from_raw_parts(samples, sample_count * 3) };
    let points: Vec<[f64; 3]> = flat.chunks_exact(3).map(|c| [c[0], c[1], c[2]]).collect();
    let (status, field) = match fit(&points, smoothing, target_error, max_spans) {
        Ok(field) => (STATUS_OK, Some(field)),
        Err((status, field)) => (status, field),
    };
    // SAFETY: `result` is a valid writable pointer per the contract.
    unsafe { *result = NarwhalHeightFieldV1::default() };
    let Some(field) = field else { return status };
    if field.heights.len() > capacity {
        return STATUS_CAPACITY;
    }
    // SAFETY: `heights` holds `capacity >= field.heights.len()` doubles.
    unsafe {
        std::ptr::copy_nonoverlapping(field.heights.as_ptr(), heights, field.heights.len());
        *result = field.info;
    }
    status
}

#[cfg(test)]
mod tests {
    use super::*;

    fn loop_samples(
        corners: &[[f64; 3]],
        per_side: usize,
        lift: impl Fn(f64, f64) -> f64,
    ) -> Vec<[f64; 3]> {
        let mut out = Vec::new();
        for k in 0..corners.len() {
            let (a, b) = (corners[k], corners[(k + 1) % corners.len()]);
            for s in 0..per_side {
                let t = s as f64 / per_side as f64;
                let (x, y) = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t);
                out.push([x, y, lift(x, y)]);
            }
        }
        out
    }

    #[test]
    fn basis_is_a_partition_of_unity_with_matching_derivatives() {
        let axis = Axis {
            start: -2.0,
            end: 5.0,
            spans: 7,
        };
        for k in 0..=70 {
            let t = -2.0 + 7.0 * k as f64 / 70.0;
            let (_, ders) = axis.basis(t);
            assert!((ders[0].iter().sum::<f64>() - 1.0).abs() < 1e-13);
            assert!(ders[1].iter().sum::<f64>().abs() < 1e-11);
            assert!(ders[2].iter().sum::<f64>().abs() < 1e-9);
        }
        // linear precision: Greville abscissae reproduce t itself.
        let greville: Vec<f64> = (0..axis.poles())
            .map(|i| (1..=3).map(|o| axis.knot((i + o) as isize)).sum::<f64>() / 3.0)
            .collect();
        for k in 0..=70 {
            let t = -2.0 + 7.0 * k as f64 / 70.0;
            let (span, ders) = axis.basis(t);
            let value: f64 = (0..4).map(|p| ders[0][p] * greville[span - 3 + p]).sum();
            assert!((value - t).abs() < 1e-12, "{value} vs {t}");
        }
    }

    #[test]
    fn a_tilted_plane_boundary_is_reproduced_exactly() {
        let corners = [
            [0.0, 0.0, 0.0],
            [40.0, 0.0, 0.0],
            [40.0, 25.0, 0.0],
            [10.0, 30.0, 0.0],
            [-5.0, 12.0, 0.0],
        ];
        let plane = |x: f64, y: f64| 0.3 * x - 0.2 * y + 4.0;
        let samples = loop_samples(&corners, 40, plane);
        let field = fit(&samples, 1e-12, 1e-9, 24).expect("plane fits");
        assert_eq!(field.info.pinned_sides, 0);
        assert!(field.info.max_error < 1e-9, "{}", field.info.max_error);
        let u = Axis {
            start: field.info.x_min,
            end: field.info.x_max,
            spans: field.info.u_pole_count as usize - 3,
        };
        let v = Axis {
            start: field.info.y_min,
            end: field.info.y_max,
            spans: field.info.v_pole_count as usize - 3,
        };
        for (x, y) in [(20.0, 10.0), (5.0, 20.0), (35.0, 5.0)] {
            assert!((evaluate(&field.heights, u, v, x, y) - plane(x, y)).abs() < 1e-8);
        }
    }

    #[test]
    fn a_lifted_corner_is_smooth_and_does_not_balloon() {
        // A planar L-shaped cap with one corner lifted by 20 and its two incident edges
        // ramping linearly -- the bug #108 situation.
        let corners = [
            [0.0, 0.0, 0.0],
            [60.0, 0.0, 0.0],
            [60.0, 50.0, 0.0],
            [30.0, 50.0, 0.0],
            [30.0, 25.0, 0.0],
            [0.0, 25.0, 0.0],
        ];
        let lifted = [30.0, 25.0];
        let lift = |x: f64, y: f64| {
            if (y - 25.0).abs() < 1e-12 && x <= 30.0 {
                20.0 * x / 30.0
            } else if (x - 30.0).abs() < 1e-12 && y >= 25.0 {
                20.0 * (50.0 - y) / 25.0
            } else {
                0.0
            }
        };
        assert_eq!(lift(lifted[0], lifted[1]), 20.0);
        let samples = loop_samples(&corners, 64, lift);
        let field = match fit(&samples, 1e-12, 1e-5, 24) {
            Ok(f) => f,
            Err((s, f)) => panic!("status {s} {:?}", f.map(|f| f.info)),
        };
        assert_eq!(
            field.info.pinned_sides,
            PIN_X_MIN | PIN_X_MAX | PIN_Y_MIN | PIN_Y_MAX
        );
        let u = Axis {
            start: field.info.x_min,
            end: field.info.x_max,
            spans: field.info.u_pole_count as usize - 3,
        };
        let v = Axis {
            start: field.info.y_min,
            end: field.info.y_max,
            spans: field.info.v_pole_count as usize - 3,
        };
        let mut highest = f64::MIN;
        let mut lowest = f64::MAX;
        for i in 0..=60 {
            for j in 0..=50 {
                let (x, y) = (i as f64, j as f64);
                if x <= 30.0 && y >= 25.0 {
                    continue; // the notch, outside the face
                }
                let z = evaluate(&field.heights, u, v, x, y);
                highest = highest.max(z);
                lowest = lowest.min(z);
            }
        }
        // A thin plate overshoots a reflex corner a little (reference: 18% on bug #108); the
        // free 3D plate this replaced overshot by 260%.
        assert!(highest < 20.0 * 1.5, "cap balloons to {highest}");
        assert!(lowest > -20.0 * 0.25, "cap sags to {lowest}");
    }

    #[test]
    fn a_self_overlapping_outline_is_refused() {
        let bow_tie = [
            [0.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [10.0, 0.0, 0.0],
            [0.0, 10.0, 0.0],
        ];
        let samples = loop_samples(&bow_tie, 10, |_, _| 0.0);
        assert_eq!(
            fit(&samples, 1e-8, 1e-6, 32).unwrap_err().0,
            STATUS_NOT_SIMPLE
        );
    }

    #[test]
    fn ffi_reports_capacity_and_status() {
        let corners = [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ];
        let flat: Vec<f64> = loop_samples(&corners, 8, |x, _| x * 0.1)
            .into_iter()
            .flatten()
            .collect();
        let mut info = NarwhalHeightFieldV1::default();
        let mut tiny = [0.0; 4];
        let status = unsafe {
            fit_height_field_ffi(
                flat.as_ptr(),
                flat.len() / 3,
                1e-8,
                1e-9,
                32,
                tiny.as_mut_ptr(),
                tiny.len(),
                &mut info,
            )
        };
        assert_eq!(status, STATUS_CAPACITY);
        let mut heights = vec![0.0; 64 * 64];
        let status = unsafe {
            fit_height_field_ffi(
                flat.as_ptr(),
                flat.len() / 3,
                1e-8,
                1e-9,
                32,
                heights.as_mut_ptr(),
                heights.len(),
                &mut info,
            )
        };
        assert_eq!(status, STATUS_OK);
        assert!(info.max_error < 1e-9);
    }
}
