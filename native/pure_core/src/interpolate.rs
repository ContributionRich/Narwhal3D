//! Dense global point interpolation matching Narwhal's Python reference.

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
const ERR_SINGULAR: i32 = -4;
const MAX_POINTS: usize = 2048;
type Interpolation = (Vec<[f64; 3]>, Vec<f64>, usize);

fn parameters(points: &[[f64; 3]], style: u8) -> Result<Vec<f64>, i32> {
    let n = points.len();
    if n < 2 || style > 2 {
        return Err(ERR_INPUT);
    }
    if style == 0 {
        return Ok((0..n).map(|i| i as f64 / (n - 1) as f64).collect());
    }
    let mut deltas = Vec::with_capacity(n - 1);
    for pair in points.windows(2) {
        let d = pair[0]
            .iter()
            .zip(pair[1])
            .map(|(a, b)| (b - a) * (b - a))
            .sum::<f64>()
            .sqrt();
        if d <= 0.0 || !d.is_finite() {
            return Err(ERR_INPUT);
        }
        deltas.push(if style == 2 { d.sqrt() } else { d });
    }
    let total: f64 = deltas.iter().sum();
    let mut out = vec![0.0];
    let mut acc = 0.0;
    for d in deltas {
        acc += d;
        out.push(acc / total);
    }
    *out.last_mut().unwrap() = 1.0;
    Ok(out)
}

fn knots(params: &[f64], p: usize) -> Vec<f64> {
    let n = params.len();
    let mut out = vec![0.0; p + 1];
    for j in 1..n - p {
        out.push(params[j..j + p].iter().sum::<f64>() / p as f64);
    }
    out.extend(std::iter::repeat_n(1.0, p + 1));
    out
}
fn span(ncv: usize, p: usize, u: f64, k: &[f64]) -> usize {
    let n = ncv - 1;
    if u >= k[n + 1] {
        return n;
    }
    if u <= k[p] {
        return p;
    }
    let (mut lo, mut hi) = (p, n + 1);
    let mut m = (lo + hi) / 2;
    while u < k[m] || u >= k[m + 1] {
        if u < k[m] {
            hi = m
        } else {
            lo = m
        };
        m = (lo + hi) / 2;
    }
    m
}
fn basis(s: usize, u: f64, p: usize, k: &[f64]) -> Vec<f64> {
    let mut n = vec![0.0; p + 1];
    let mut l = vec![0.0; p + 1];
    let mut r = vec![0.0; p + 1];
    n[0] = 1.0;
    for j in 1..=p {
        l[j] = u - k[s + 1 - j];
        r[j] = k[s + j] - u;
        let mut saved = 0.0;
        for q in 0..j {
            let den = r[q + 1] + l[j - q];
            let tmp = if den != 0.0 { n[q] / den } else { 0.0 };
            n[q] = saved + r[q + 1] * tmp;
            saved = l[j - q] * tmp;
        }
        n[j] = saved;
    }
    n
}
#[allow(clippy::needless_range_loop)]
fn solve(points: &[[f64; 3]], degree: usize, style: u8) -> Result<Interpolation, i32> {
    let n = points.len();
    if !(2..=MAX_POINTS).contains(&n) || degree < 1 || degree.is_multiple_of(2) {
        return Err(ERR_INPUT);
    }
    let p = degree.min(n - 1);
    let params = parameters(points, style)?;
    if p == 1 {
        let mut k = vec![0.0];
        k.extend(params);
        k.push(1.0);
        return Ok((points.to_vec(), k, p));
    }
    let k = knots(&params, p);
    let mut a = vec![0.0; n * n];
    for (row, &u) in params.iter().enumerate() {
        let s = span(n, p, u, &k);
        for (j, v) in basis(s, u, p, &k).into_iter().enumerate() {
            a[row * n + s - p + j] = v;
        }
    }
    let mut perm: Vec<usize> = (0..n).collect();
    for col in 0..n {
        let pivot = (col..n)
            .max_by(|&i, &j| a[i * n + col].abs().total_cmp(&a[j * n + col].abs()))
            .unwrap();
        if a[pivot * n + col].abs() < 1e-14 {
            return Err(ERR_SINGULAR);
        }
        if pivot != col {
            for j in 0..n {
                a.swap(col * n + j, pivot * n + j)
            }
            perm.swap(col, pivot);
        }
        let inv = 1.0 / a[col * n + col];
        for i in col + 1..n {
            let f = a[i * n + col] * inv;
            a[i * n + col] = f;
            if f != 0.0 {
                for j in col + 1..n {
                    a[i * n + j] -= f * a[col * n + j];
                }
            }
        }
    }
    let mut y: Vec<[f64; 3]> = perm.iter().map(|&i| points[i]).collect();
    for i in 1..n {
        for q in 0..i {
            let f = a[i * n + q];
            for d in 0..3 {
                y[i][d] -= f * y[q][d];
            }
        }
    }
    for i in (0..n).rev() {
        for q in i + 1..n {
            let f = a[i * n + q];
            for d in 0..3 {
                y[i][d] -= f * y[q][d];
            }
        }
        let inv = 1.0 / a[i * n + i];
        for d in 0..3 {
            y[i][d] *= inv;
        }
    }
    Ok((y, k, p))
}

/// Closed-polygon parameters: `n + 1` values on [0, 1], the closing segment included.
fn closed_parameters(points: &[[f64; 3]], style: u8) -> Result<Vec<f64>, i32> {
    let n = points.len();
    if n < 3 || style > 2 {
        return Err(ERR_INPUT);
    }
    let mut deltas = Vec::with_capacity(n);
    for k in 0..n {
        let (a, b) = (points[k], points[(k + 1) % n]);
        let d = a
            .iter()
            .zip(b)
            .map(|(a, b)| (b - a) * (b - a))
            .sum::<f64>()
            .sqrt();
        if d <= 0.0 || !d.is_finite() {
            return Err(ERR_INPUT);
        }
        deltas.push(match style {
            0 => 1.0,
            2 => d.sqrt(),
            _ => d,
        });
    }
    let total: f64 = deltas.iter().sum();
    let mut out = vec![0.0];
    let mut acc = 0.0;
    for d in deltas {
        acc += d;
        out.push(acc / total);
    }
    *out.last_mut().unwrap() = 1.0;
    Ok(out)
}

/// Thomas algorithm for one tridiagonal system; `None` on a vanishing pivot.
fn thomas(sub: &[f64], diag: &[f64], sup: &[f64], rhs: &[[f64; 3]]) -> Option<Vec<[f64; 3]>> {
    let n = diag.len();
    let mut cp = vec![0.0; n];
    let mut dp = vec![[0.0; 3]; n];
    for i in 0..n {
        let denom = if i == 0 {
            diag[0]
        } else {
            diag[i] - sub[i] * cp[i - 1]
        };
        if denom.abs() < 1e-14 {
            return None;
        }
        cp[i] = sup[i] / denom;
        for d in 0..3 {
            let prev = if i == 0 { 0.0 } else { sub[i] * dp[i - 1][d] };
            dp[i][d] = (rhs[i][d] - prev) / denom;
        }
    }
    for i in (0..n - 1).rev() {
        let next = dp[i + 1];
        for (value, below) in dp[i].iter_mut().zip(next) {
            *value -= cp[i] * below;
        }
    }
    Some(dp)
}

/// The periodic cubic through `points`, as in `interpolate_points_periodic`:
/// `n + 3` wrapped controls and the full unclamped knot vector (`n + 7`).
fn solve_periodic(points: &[[f64; 3]], style: u8) -> Result<Interpolation, i32> {
    let n = points.len();
    if !(3..=MAX_POINTS).contains(&n) {
        return Err(ERR_INPUT);
    }
    let params = closed_parameters(points, style)?;
    let k: Vec<f64> = (-3..n as i64 + 4)
        .map(|i| params[i.rem_euclid(n as i64) as usize] + i.div_euclid(n as i64) as f64)
        .collect();
    let (mut sub, mut diag, mut sup) = (vec![0.0; n], vec![0.0; n], vec![0.0; n]);
    for row in 0..n {
        let v = basis(row + 3, params[row], 3, &k);
        (sub[row], diag[row], sup[row]) = (v[0], v[1], v[2]);
    }
    // Sherman-Morrison: corners are sub[0] (row 0, col n-1) and sup[n-1] (row n-1, col 0).
    let (alpha, beta, gamma) = (sup[n - 1], sub[0], -diag[0]);
    let mut bb = diag.clone();
    bb[0] = diag[0] - gamma;
    bb[n - 1] = diag[n - 1] - alpha * beta / gamma;
    let x = thomas(&sub, &bb, &sup, points).ok_or(ERR_SINGULAR)?;
    let mut u = vec![[0.0; 3]; n];
    u[0][0] = gamma;
    u[n - 1][0] = alpha;
    let z = thomas(&sub, &bb, &sup, &u).ok_or(ERR_SINGULAR)?;
    let denom = 1.0 + z[0][0] + beta * z[n - 1][0] / gamma;
    let mut shifted = x.clone();
    for d in 0..3 {
        let fact = (x[0][d] + beta * x[n - 1][d] / gamma) / denom;
        for i in 0..n {
            shifted[i][d] = x[i][d] - fact * z[i][0];
        }
    }
    let mut ctrl: Vec<[f64; 3]> = (0..n).map(|i| shifted[(i + n - 1) % n]).collect();
    ctrl.extend_from_within(0..3);
    if ctrl.iter().flatten().any(|v| !v.is_finite()) {
        return Err(ERR_SINGULAR);
    }
    Ok((ctrl, k, 3))
}

#[allow(clippy::needless_range_loop)]
fn skin(
    sections: &[[f64; 3]],
    k: usize,
    width: usize,
    degree: usize,
    style: u8,
) -> Result<Interpolation, i32> {
    if k < 2 || width == 0 || sections.len() != k * width || style > 2 {
        return Err(ERR_INPUT);
    }
    let q = degree.min(k - 1).max(1);
    let params = if style == 0 || q == 1 {
        (0..k)
            .map(|j| j as f64 / (k - 1) as f64)
            .collect::<Vec<_>>()
    } else {
        let mut acc = vec![0.0; k];
        let mut used = 0usize;
        for i in 0..width {
            let col: Vec<[f64; 3]> = (0..k).map(|j| sections[j * width + i]).collect();
            if let Ok(p) = parameters(&col, style) {
                used += 1;
                for j in 0..k {
                    acc[j] += p[j];
                }
            }
        }
        if used == 0 {
            (0..k).map(|j| j as f64 / (k - 1) as f64).collect()
        } else {
            for v in &mut acc {
                *v /= used as f64
            }
            acc[0] = 0.0;
            acc[k - 1] = 1.0;
            acc
        }
    };
    if q == 1 {
        let mut knots = vec![0.0];
        knots.extend(&params);
        knots.push(1.0);
        let mut out = Vec::with_capacity(k * width);
        for i in 0..width {
            for j in 0..k {
                out.push(sections[j * width + i]);
            }
        }
        return Ok((out, knots, q));
    }
    let knot = knots(&params, q);
    let mut a = vec![0.0; k * k];
    for (row, &u) in params.iter().enumerate() {
        let s = span(k, q, u, &knot);
        for (j, v) in basis(s, u, q, &knot).into_iter().enumerate() {
            a[row * k + s - q + j] = v;
        }
    }
    let mut perm: Vec<usize> = (0..k).collect();
    for col in 0..k {
        let pivot = (col..k)
            .max_by(|&i, &j| a[i * k + col].abs().total_cmp(&a[j * k + col].abs()))
            .unwrap();
        if a[pivot * k + col].abs() < 1e-14 {
            return Err(ERR_SINGULAR);
        }
        if pivot != col {
            for j in 0..k {
                a.swap(col * k + j, pivot * k + j)
            }
            perm.swap(col, pivot)
        }
        let inv = 1.0 / a[col * k + col];
        for i in col + 1..k {
            let f = a[i * k + col] * inv;
            a[i * k + col] = f;
            if f != 0.0 {
                for j in col + 1..k {
                    a[i * k + j] -= f * a[col * k + j];
                }
            }
        }
    }
    let mut out = Vec::with_capacity(k * width);
    for column in 0..width {
        let mut y: Vec<[f64; 3]> = perm.iter().map(|&j| sections[j * width + column]).collect();
        for i in 1..k {
            for p in 0..i {
                let f = a[i * k + p];
                for d in 0..3 {
                    y[i][d] -= f * y[p][d];
                }
            }
        }
        for i in (0..k).rev() {
            for p in i + 1..k {
                let f = a[i * k + p];
                for d in 0..3 {
                    y[i][d] -= f * y[p][d];
                }
            }
            let inv = 1.0 / a[i * k + i];
            for d in 0..3 {
                y[i][d] *= inv;
            }
        }
        out.extend(y);
    }
    Ok((out, knot, q))
}

/// Skin section-major xyz control rows into width-major output columns.
/// # Safety
/// Input/output spans are valid for declared capacities and do not overlap.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_skin_control_net(
    sections: *const f64,
    section_count: usize,
    width: usize,
    degree: i64,
    style: u8,
    output: *mut f64,
    output_capacity: usize,
    out_knots: *mut f64,
    knot_capacity: usize,
    out_degree: *mut u64,
    out_values: *mut u64,
    out_knot_count: *mut u64,
) -> i32 {
    if out_degree.is_null() || out_values.is_null() || out_knot_count.is_null() {
        return ERR_NULL;
    }
    *out_degree = 0;
    *out_values = 0;
    *out_knot_count = 0;
    let point_count = match section_count.checked_mul(width) {
        Some(v) if v <= 2_000_000 => v,
        _ => return ERR_SIZE,
    };
    let coords = match point_count.checked_mul(3) {
        Some(v) => v,
        None => return ERR_SIZE,
    };
    let flat = match crate::checked_slice(sections, coords) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    if flat.iter().any(|v| !v.is_finite()) {
        return ERR_INPUT;
    }
    let pts: Vec<[f64; 3]> = flat.chunks_exact(3).map(|v| [v[0], v[1], v[2]]).collect();
    let degree = match usize::try_from(degree) {
        Ok(v) => v,
        Err(_) => return ERR_INPUT,
    };
    let (net, k, q) = match skin(&pts, section_count, width, degree, style) {
        Ok(v) => v,
        Err(e) => return e,
    };
    if output_capacity < coords || knot_capacity < k.len() {
        return ERR_SIZE;
    }
    let out = match crate::checked_slice_mut(output, output_capacity) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    let ko = match crate::checked_slice_mut(out_knots, knot_capacity) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    for (i, p) in net.iter().enumerate() {
        out[3 * i..3 * i + 3].copy_from_slice(p)
    }
    ko[..k.len()].copy_from_slice(&k);
    *out_degree = q as u64;
    *out_values = coords as u64;
    *out_knot_count = k.len() as u64;
    0
}

/// Interpolate packed xyz points into caller-owned controls and full knots.
/// # Safety
/// Input/output spans are valid for declared capacities and do not overlap.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_interpolate(
    points: *const f64,
    point_count: usize,
    degree: i64,
    style: u8,
    controls: *mut f64,
    control_capacity: usize,
    out_knots: *mut f64,
    knot_capacity: usize,
    out_degree: *mut u64,
    out_control_count: *mut u64,
    out_knot_count: *mut u64,
) -> i32 {
    if out_degree.is_null() || out_control_count.is_null() || out_knot_count.is_null() {
        return ERR_NULL;
    }
    *out_degree = 0;
    *out_control_count = 0;
    *out_knot_count = 0;
    let p = match usize::try_from(degree) {
        Ok(v) => v,
        Err(_) => return ERR_INPUT,
    };
    if point_count > MAX_POINTS {
        return ERR_SIZE;
    }
    let coord_count = match point_count.checked_mul(3) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let flat = match crate::checked_slice(points, coord_count) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    if flat.len() != coord_count || flat.iter().any(|v| !v.is_finite()) {
        return ERR_INPUT;
    }
    let pts: Vec<[f64; 3]> = flat.chunks_exact(3).map(|v| [v[0], v[1], v[2]]).collect();
    let (ctrl, k, actual) = match solve(&pts, p, style) {
        Ok(v) => v,
        Err(e) => return e,
    };
    if control_capacity < ctrl.len() * 3 || knot_capacity < k.len() {
        return ERR_SIZE;
    }
    let co = match crate::checked_slice_mut(controls, control_capacity) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    let ko = match crate::checked_slice_mut(out_knots, knot_capacity) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    for (i, p) in ctrl.iter().enumerate() {
        co[3 * i..3 * i + 3].copy_from_slice(p)
    }
    ko[..k.len()].copy_from_slice(&k);
    *out_degree = actual as u64;
    *out_control_count = ctrl.len() as u64;
    *out_knot_count = k.len() as u64;
    0
}

/// Closed (periodic) cubic interpolation of packed distinct xyz points into
/// caller-owned controls (`n + 3`, the last three repeating the first) and the full
/// unclamped knot vector (`n + 7`).
/// # Safety
/// Input/output spans are valid for declared capacities and do not overlap.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_interpolate_periodic(
    points: *const f64,
    point_count: usize,
    style: u8,
    controls: *mut f64,
    control_capacity: usize,
    out_knots: *mut f64,
    knot_capacity: usize,
    out_control_count: *mut u64,
    out_knot_count: *mut u64,
) -> i32 {
    if out_control_count.is_null() || out_knot_count.is_null() {
        return ERR_NULL;
    }
    *out_control_count = 0;
    *out_knot_count = 0;
    if point_count > MAX_POINTS {
        return ERR_SIZE;
    }
    let coord_count = match point_count.checked_mul(3) {
        Some(value) => value,
        None => return ERR_SIZE,
    };
    let flat = match crate::checked_slice(points, coord_count) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    if flat.len() != coord_count || flat.iter().any(|v| !v.is_finite()) {
        return ERR_INPUT;
    }
    let pts: Vec<[f64; 3]> = flat.chunks_exact(3).map(|v| [v[0], v[1], v[2]]).collect();
    let (ctrl, k, _) = match solve_periodic(&pts, style) {
        Ok(v) => v,
        Err(e) => return e,
    };
    if control_capacity < ctrl.len() * 3 || knot_capacity < k.len() {
        return ERR_SIZE;
    }
    let co = match crate::checked_slice_mut(controls, control_capacity) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    let ko = match crate::checked_slice_mut(out_knots, knot_capacity) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    for (i, p) in ctrl.iter().enumerate() {
        co[3 * i..3 * i + 3].copy_from_slice(p)
    }
    ko[..k.len()].copy_from_slice(&k);
    *out_control_count = ctrl.len() as u64;
    *out_knot_count = k.len() as u64;
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cubic_hits_fixture() {
        let p = [[0., 0., 0.], [1., 2., 0.], [4., 1., 0.], [7., 3., 0.]];
        let (c, k, d) = solve(&p, 3, 1).unwrap();
        assert_eq!(d, 3);
        assert_eq!(c.len(), 4);
        assert_eq!(k.len(), 8);
        assert_eq!(c[0], p[0]);
        assert_eq!(c[3], p[3]);
    }
    #[test]
    fn skinning_preserves_width_major_shape() {
        let sections = vec![
            [0., 0., 0.],
            [1., 0., 0.],
            [0., 1., 1.],
            [1., 1., 1.],
            [0., 2., 0.],
            [1., 2., 0.],
        ];
        let (net, k, q) = skin(&sections, 3, 2, 3, 1).unwrap();
        assert_eq!(q, 2);
        assert_eq!(net.len(), 6);
        assert_eq!(k.len(), 6);
        assert_eq!(net[0], sections[0]);
        assert_eq!(net[2], sections[4]);
    }

    #[test]
    fn duplicate_chord_rejects() {
        assert_eq!(
            solve(&[[0., 0., 0.], [0., 0., 0.]], 3, 1).err(),
            Some(ERR_INPUT)
        );
    }

    fn eval(ctrl: &[[f64; 3]], k: &[f64], u: f64) -> [f64; 3] {
        let s = span(ctrl.len(), 3, u, k);
        let b = basis(s, u, 3, k);
        let mut out = [0.0; 3];
        for (j, v) in b.iter().enumerate() {
            for d in 0..3 {
                out[d] += v * ctrl[s - 3 + j][d];
            }
        }
        out
    }

    #[test]
    fn periodic_passes_through_irregular_points() {
        let p = [
            [0., 0., 0.],
            [4., 1., 0.],
            [7., 5., 1.],
            [3., 9., 0.],
            [-2., 6., 2.],
            [-3., 2., 0.],
        ];
        for style in 0..3 {
            let (c, k, d) = solve_periodic(&p, style).unwrap();
            assert_eq!((d, c.len(), k.len()), (3, 9, 13));
            assert_eq!(&c[6..], &c[..3]);
            let params = closed_parameters(&p, style).unwrap();
            for (u, q) in params.iter().zip(p.iter()) {
                let e = eval(&c, &k, *u);
                for dim in 0..3 {
                    assert!((e[dim] - q[dim]).abs() < 1e-12, "{e:?} vs {q:?}");
                }
            }
        }
    }

    #[test]
    fn periodic_rejects_short_and_duplicate_input() {
        let p = [[0., 0., 0.], [1., 0., 0.]];
        assert_eq!(solve_periodic(&p, 1).err(), Some(ERR_INPUT));
        let q = [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 0.]];
        assert_eq!(solve_periodic(&q, 1).err(), Some(ERR_INPUT));
    }
}
