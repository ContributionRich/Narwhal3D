//! Surface-construction decisions: loft section alignment and corner-surface checks.
//!
//! The caller samples its exact curves; these functions only decide (reverse a section,
//! which knot becomes the seam, whether corners are usable). Geometry is never built or
//! approximated here, so every result stays the caller's exact NURBS.

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;

fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn norm2(a: [f64; 3]) -> f64 {
    dot(a, a)
}

/// Newell area normal of a closed sample loop: its sign is the winding.
fn area_normal(points: &[[f64; 3]]) -> [f64; 3] {
    let mut n = [0.0; 3];
    for (k, p) in points.iter().enumerate() {
        let q = points[(k + 1) % points.len()];
        n[0] += (p[1] - q[1]) * (p[2] + q[2]);
        n[1] += (p[2] - q[2]) * (p[0] + q[0]);
        n[2] += (p[0] - q[0]) * (p[1] + q[1]);
    }
    n
}

/// Should `current` be reversed to run the same way as `previous`?
///
/// Open sections compare end pairings: reverse when start-to-end + end-to-start is
/// shorter than start-to-start + end-to-end. Closed sections compare winding: reverse
/// when the area normals point in opposite directions.
pub fn should_reverse(previous: &[[f64; 3]], current: &[[f64; 3]], closed: bool) -> bool {
    if closed {
        return dot(area_normal(previous), area_normal(current)) < 0.0;
    }
    let (p0, p1) = (previous[0], previous[previous.len() - 1]);
    let (c0, c1) = (current[0], current[current.len() - 1]);
    let same = norm2(sub(p0, c0)) + norm2(sub(p1, c1));
    let crossed = norm2(sub(p0, c1)) + norm2(sub(p1, c0));
    crossed < same
}

/// Index of the candidate nearest `target` (the first one on a tie).
pub fn nearest_index(target: [f64; 3], candidates: &[[f64; 3]]) -> Option<usize> {
    candidates
        .iter()
        .enumerate()
        .min_by(|a, b| norm2(sub(*a.1, target)).total_cmp(&norm2(sub(*b.1, target))))
        .map(|(index, _)| index)
}

/// 0 when the corners make a surface; otherwise a reason:
/// 1 = not 3 or 4 corners, 2 = two corners coincide, 3 = the first three are in a line.
/// `coincident` receives the 1-based pair for reason 2.
pub fn corner_problem(corners: &[[f64; 3]], tolerance: f64) -> (i32, [usize; 2]) {
    if corners.len() != 3 && corners.len() != 4 {
        return (1, [0, 0]);
    }
    for i in 0..corners.len() {
        for j in i + 1..corners.len() {
            if norm2(sub(corners[i], corners[j])) <= tolerance * tolerance {
                return (2, [i + 1, j + 1]);
            }
        }
    }
    let u = sub(corners[1], corners[0]);
    let v = sub(corners[2], corners[0]);
    if norm2(cross(u, v)).sqrt() <= tolerance * norm2(u).sqrt().max(1.0) {
        return (3, [0, 0]);
    }
    (0, [0, 0])
}

unsafe fn points(ptr: *const f64, count: usize) -> Option<Vec<[f64; 3]>> {
    let flat = crate::checked_slice(ptr, count.checked_mul(3)?)?;
    if flat.iter().any(|v| !v.is_finite()) {
        return None;
    }
    Some(flat.chunks_exact(3).map(|v| [v[0], v[1], v[2]]).collect())
}

/// 1 to reverse `current`, 0 to keep it, negative on bad input.
/// # Safety
/// `previous` and `current` each hold `count` xyz triples.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_loft_should_reverse(
    previous: *const f64,
    current: *const f64,
    count: usize,
    closed: u8,
) -> i32 {
    if previous.is_null() || current.is_null() {
        return ERR_NULL;
    }
    if !(2..=1_000_000).contains(&count) || closed > 1 {
        return ERR_INPUT;
    }
    match (points(previous, count), points(current, count)) {
        (Some(p), Some(c)) => i32::from(should_reverse(&p, &c, closed == 1)),
        _ => ERR_INPUT,
    }
}

/// Index of the nearest candidate, negative on bad input.
/// # Safety
/// `target` holds one xyz triple; `candidates` holds `count` triples.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_nearest_index(
    target: *const f64,
    candidates: *const f64,
    count: usize,
) -> i64 {
    if target.is_null() || candidates.is_null() {
        return i64::from(ERR_NULL);
    }
    if count == 0 || count > 1_000_000 {
        return i64::from(ERR_INPUT);
    }
    match (points(target, 1), points(candidates, count)) {
        (Some(t), Some(c)) => nearest_index(t[0], &c).map_or(i64::from(ERR_INPUT), |i| i as i64),
        _ => i64::from(ERR_INPUT),
    }
}

/// Corner-surface check (see [`corner_problem`]); negative on bad input.
/// # Safety
/// `corners` holds `count` xyz triples; `pair` has room for two values.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_corner_problem(
    corners: *const f64,
    count: usize,
    tolerance: f64,
    pair: *mut u64,
) -> i32 {
    if corners.is_null() || pair.is_null() {
        return ERR_NULL;
    }
    if count > 16 || !tolerance.is_finite() || tolerance < 0.0 {
        return ERR_INPUT;
    }
    let Some(c) = points(corners, count) else {
        return ERR_INPUT;
    };
    let Some(out) = crate::checked_slice_mut(pair, 2) else {
        return ERR_NULL;
    };
    let (code, ij) = corner_problem(&c, tolerance);
    out[0] = ij[0] as u64;
    out[1] = ij[1] as u64;
    code
}

#[cfg(test)]
mod tests {
    use super::*;

    fn line(y: f64, z: f64, flip: bool) -> Vec<[f64; 3]> {
        let mut v: Vec<[f64; 3]> = (0..=4).map(|i| [5.0 * i as f64, y, z]).collect();
        if flip {
            v.reverse();
        }
        v
    }

    fn circle(radius: f64, z: f64, clockwise: bool) -> Vec<[f64; 3]> {
        (0..32)
            .map(|k| {
                let mut t = std::f64::consts::TAU * k as f64 / 32.0;
                if clockwise {
                    t = -t;
                }
                [radius * t.cos(), radius * t.sin(), z]
            })
            .collect()
    }

    #[test]
    fn open_sections_reverse_only_when_their_ends_cross() {
        assert!(!should_reverse(
            &line(0.0, 0.0, false),
            &line(10.0, 3.0, false),
            false
        ));
        assert!(should_reverse(
            &line(0.0, 0.0, false),
            &line(10.0, 3.0, true),
            false
        ));
    }

    #[test]
    fn closed_sections_reverse_only_when_they_wind_against_each_other() {
        assert!(!should_reverse(
            &circle(5.0, 0.0, false),
            &circle(8.0, 10.0, false),
            true
        ));
        assert!(should_reverse(
            &circle(5.0, 0.0, false),
            &circle(8.0, 10.0, true),
            true
        ));
    }

    #[test]
    fn nearest_index_picks_the_closest_candidate() {
        let c = [[0.0, 0.0, 0.0], [5.0, 0.0, 0.0], [5.0, 5.0, 0.0]];
        assert_eq!(nearest_index([4.0, 1.0, 0.0], &c), Some(1));
        assert_eq!(nearest_index([0.0, 0.0, 0.0], &[]), None);
    }

    #[test]
    fn corner_problems_are_named() {
        let ok = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]];
        assert_eq!(corner_problem(&ok, 1e-9).0, 0);
        assert_eq!(corner_problem(&ok[..2], 1e-9).0, 1);
        let twin = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 0.0, 0.0]];
        assert_eq!(corner_problem(&twin, 1e-9), (2, [2, 3]));
        let line = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]];
        assert_eq!(corner_problem(&line, 1e-9).0, 3);
    }

    #[test]
    fn abi_rejects_null_and_non_finite_input() {
        let a = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0];
        let bad = [0.0, 0.0, 0.0, f64::NAN, 0.0, 0.0];
        unsafe {
            assert_eq!(
                narwhal_pure_core_loft_should_reverse(a.as_ptr(), std::ptr::null(), 2, 0),
                ERR_NULL
            );
            assert_eq!(
                narwhal_pure_core_loft_should_reverse(a.as_ptr(), bad.as_ptr(), 2, 0),
                ERR_INPUT
            );
            assert_eq!(
                narwhal_pure_core_nearest_index(a.as_ptr(), a.as_ptr(), 2),
                0
            );
        }
    }
}
