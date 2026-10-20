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
    fn duplicate_chord_rejects() {
        assert_eq!(
            solve(&[[0., 0., 0.], [0., 0., 0.]], 3, 1).err(),
            Some(ERR_INPUT)
        );
    }
}
