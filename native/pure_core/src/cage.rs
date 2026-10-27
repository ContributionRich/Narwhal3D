//! CageEdit: a trivariate B-spline volume that deforms captive control points.
//!
//! The cage is a box frame (origin plus three orthogonal edge vectors) carrying a
//! `counts[0] x counts[1] x counts[2]` lattice of control points with clamped uniform
//! knots of the given degrees. At rest every lattice point sits at its Greville
//! abscissa, and B-splines reproduce linear functions, so an unmoved cage maps every
//! point to itself exactly. Moving cage points moves each captive control point to the
//! volume's value at the captive's rest parameters.
//!
//! Only CONTROL POINTS are mapped. A captive NURBS curve or surface therefore stays an
//! exact NURBS of the same degree and knots -- it is never sampled or tessellated.
//! Points outside the box are left where they are (OURS: the reference's global
//! region is not documented precisely enough to extrapolate).

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const MAX_COUNT: usize = 64;

/// A validated cage frame and lattice shape.
#[derive(Clone, Copy, Debug)]
pub struct Cage {
    pub origin: [f64; 3],
    pub axes: [[f64; 3]; 3],
    pub degrees: [usize; 3],
    pub counts: [usize; 3],
}

fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

/// Full clamped uniform knot vector on [0, 1] for `n` control points of degree `p`.
fn knots(n: usize, p: usize) -> Vec<f64> {
    let spans = n - p;
    let mut k = vec![0.0; p + 1];
    for i in 1..spans {
        k.push(i as f64 / spans as f64);
    }
    k.extend(std::iter::repeat_n(1.0, p + 1));
    k
}

/// Greville abscissae: where each control point sits for the identity map.
fn greville(n: usize, p: usize) -> Vec<f64> {
    if p == 0 {
        return vec![0.0; n];
    }
    let k = knots(n, p);
    (0..n)
        .map(|i| k[i + 1..=i + p].iter().sum::<f64>() / p as f64)
        .collect()
}

/// All `n` basis values at `u` (zero outside the support).
fn basis(n: usize, p: usize, u: f64) -> Vec<f64> {
    if p == 0 || n == 1 {
        return vec![1.0; 1].into_iter().chain(vec![0.0; n - 1]).collect();
    }
    let k = knots(n, p);
    let u = u.clamp(0.0, 1.0);
    // span index s with k[s] <= u < k[s+1], the last span closed at u = 1
    let mut s = p;
    while s < n - 1 && u >= k[s + 1] {
        s += 1;
    }
    let mut left = vec![0.0; p + 1];
    let mut right = vec![0.0; p + 1];
    let mut values = vec![0.0; p + 1];
    values[0] = 1.0;
    for j in 1..=p {
        left[j] = u - k[s + 1 - j];
        right[j] = k[s + j] - u;
        let mut saved = 0.0;
        for r in 0..j {
            let denominator = right[r + 1] + left[j - r];
            let term = if denominator == 0.0 {
                0.0
            } else {
                values[r] / denominator
            };
            values[r] = saved + right[r + 1] * term;
            saved = left[j - r] * term;
        }
        values[j] = saved;
    }
    let mut out = vec![0.0; n];
    for (j, v) in values.into_iter().enumerate() {
        out[s - p + j] = v;
    }
    out
}

impl Cage {
    /// Validate the frame: finite, mutually orthogonal axes, and each count/degree
    /// pair buildable (1 <= count <= 64, degree < count). A zero-length axis is a flat
    /// cage in that direction and must have count 1.
    pub fn new(
        origin: [f64; 3],
        axes: [[f64; 3]; 3],
        degrees: [usize; 3],
        counts: [usize; 3],
    ) -> Option<Cage> {
        let values = origin.iter().chain(axes.iter().flatten());
        if values.into_iter().any(|v| !v.is_finite()) {
            return None;
        }
        for d in 0..3 {
            if counts[d] == 0 || counts[d] > MAX_COUNT || degrees[d] >= counts[d] {
                return None;
            }
            let length2 = dot(axes[d], axes[d]);
            if length2 == 0.0 && counts[d] != 1 {
                return None;
            }
        }
        for (a, b) in [(0, 1), (0, 2), (1, 2)] {
            let scale = (dot(axes[a], axes[a]) * dot(axes[b], axes[b])).sqrt();
            if dot(axes[a], axes[b]).abs() > 1e-9 * scale.max(f64::MIN_POSITIVE) {
                return None;
            }
        }
        Some(Cage {
            origin,
            axes,
            degrees,
            counts,
        })
    }

    pub fn point_count(&self) -> usize {
        self.counts[0] * self.counts[1] * self.counts[2]
    }

    /// Rest parameters (u, v, w) of a world point; None when it lies outside the box.
    #[allow(clippy::needless_range_loop)]
    pub fn local(&self, point: [f64; 3]) -> Option<[f64; 3]> {
        let rel = [
            point[0] - self.origin[0],
            point[1] - self.origin[1],
            point[2] - self.origin[2],
        ];
        let mut out = [0.0; 3];
        for d in 0..3 {
            let length2 = dot(self.axes[d], self.axes[d]);
            let t = if length2 == 0.0 {
                0.0
            } else {
                dot(rel, self.axes[d]) / length2
            };
            let slack = 1e-12;
            if t < -slack || t > 1.0 + slack {
                return None;
            }
            out[d] = t.clamp(0.0, 1.0);
        }
        Some(out)
    }

    /// Lattice point index for (i, j, k), i fastest.
    pub fn index(&self, i: usize, j: usize, k: usize) -> usize {
        i + self.counts[0] * (j + self.counts[1] * k)
    }

    /// The rest lattice: control points at their Greville positions.
    #[allow(clippy::needless_range_loop)]
    pub fn rest_points(&self) -> Vec<[f64; 3]> {
        let g: Vec<Vec<f64>> = (0..3)
            .map(|d| greville(self.counts[d], self.degrees[d]))
            .collect();
        let mut out = vec![[0.0; 3]; self.point_count()];
        for k in 0..self.counts[2] {
            for j in 0..self.counts[1] {
                for i in 0..self.counts[0] {
                    let (u, v, w) = (g[0][i], g[1][j], g[2][k]);
                    let mut p = self.origin;
                    for c in 0..3 {
                        p[c] += u * self.axes[0][c] + v * self.axes[1][c] + w * self.axes[2][c];
                    }
                    out[self.index(i, j, k)] = p;
                }
            }
        }
        out
    }

    /// The deformed position of `point` for the lattice `cage_points`, or the point
    /// unchanged when it is outside the box.
    #[allow(clippy::needless_range_loop)]
    pub fn deform(&self, cage_points: &[[f64; 3]], point: [f64; 3]) -> [f64; 3] {
        let Some(uvw) = self.local(point) else {
            return point;
        };
        let b: Vec<Vec<f64>> = (0..3)
            .map(|d| basis(self.counts[d], self.degrees[d], uvw[d]))
            .collect();
        let mut out = [0.0; 3];
        for k in 0..self.counts[2] {
            if b[2][k] == 0.0 {
                continue;
            }
            for j in 0..self.counts[1] {
                let wjk = b[1][j] * b[2][k];
                if wjk == 0.0 {
                    continue;
                }
                for i in 0..self.counts[0] {
                    let weight = b[0][i] * wjk;
                    if weight == 0.0 {
                        continue;
                    }
                    let q = cage_points[self.index(i, j, k)];
                    for c in 0..3 {
                        out[c] += weight * q[c];
                    }
                }
            }
        }
        out
    }
}

unsafe fn read_cage(frame: *const f64, shape: *const u64) -> Option<Cage> {
    let f = crate::checked_slice(frame, 12)?;
    let s = crate::checked_slice(shape, 6)?;
    let mut sizes = [0usize; 6];
    for (slot, value) in sizes.iter_mut().zip(s) {
        *slot = usize::try_from(*value).ok()?;
    }
    Cage::new(
        [f[0], f[1], f[2]],
        [[f[3], f[4], f[5]], [f[6], f[7], f[8]], [f[9], f[10], f[11]]],
        [sizes[0], sizes[1], sizes[2]],
        [sizes[3], sizes[4], sizes[5]],
    )
}

unsafe fn read_points(ptr: *const f64, count: usize) -> Option<Vec<[f64; 3]>> {
    let flat = crate::checked_slice(ptr, count.checked_mul(3)?)?;
    if flat.iter().any(|v| !v.is_finite()) {
        return None;
    }
    Some(flat.chunks_exact(3).map(|v| [v[0], v[1], v[2]]).collect())
}

/// Write the rest lattice. `frame` = origin + three edge vectors (12 doubles);
/// `shape` = degrees then counts (6 values). Returns the point count, or negative.
/// # Safety
/// `frame` holds 12 doubles, `shape` 6 values, `out` `capacity` doubles.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_cage_rest_points(
    frame: *const f64,
    shape: *const u64,
    out: *mut f64,
    capacity: usize,
) -> i64 {
    if frame.is_null() || shape.is_null() || out.is_null() {
        return i64::from(ERR_NULL);
    }
    let Some(cage) = read_cage(frame, shape) else {
        return i64::from(ERR_INPUT);
    };
    let rest = cage.rest_points();
    if capacity < rest.len() * 3 {
        return i64::from(ERR_INPUT);
    }
    let Some(slot) = crate::checked_slice_mut(out, rest.len() * 3) else {
        return i64::from(ERR_NULL);
    };
    for (chunk, p) in slot.chunks_exact_mut(3).zip(&rest) {
        chunk.copy_from_slice(p);
    }
    rest.len() as i64
}

/// Deform `count` points through the cage lattice `cage_points` (point_count xyz).
/// Returns how many points were inside the box, or negative.
/// # Safety
/// Buffers hold the declared number of xyz triples; `out` holds `count` triples.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_cage_deform(
    frame: *const f64,
    shape: *const u64,
    cage_points: *const f64,
    points: *const f64,
    count: usize,
    out: *mut f64,
) -> i64 {
    if frame.is_null() || shape.is_null() || cage_points.is_null() || out.is_null() {
        return i64::from(ERR_NULL);
    }
    if count > 10_000_000 || (count > 0 && points.is_null()) {
        return i64::from(ERR_INPUT);
    }
    let Some(cage) = read_cage(frame, shape) else {
        return i64::from(ERR_INPUT);
    };
    let (Some(lattice), Some(input)) = (
        read_points(cage_points, cage.point_count()),
        read_points(points, count),
    ) else {
        return i64::from(ERR_INPUT);
    };
    let Some(slot) = crate::checked_slice_mut(out, count * 3) else {
        return i64::from(ERR_NULL);
    };
    let mut inside = 0i64;
    for (chunk, p) in slot.chunks_exact_mut(3).zip(&input) {
        if cage.local(*p).is_some() {
            inside += 1;
        }
        chunk.copy_from_slice(&cage.deform(&lattice, *p));
    }
    inside
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unit_cage(degrees: [usize; 3], counts: [usize; 3]) -> Cage {
        Cage::new(
            [1.0, 2.0, 3.0],
            [[10.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 6.0]],
            degrees,
            counts,
        )
        .unwrap()
    }

    fn samples() -> Vec<[f64; 3]> {
        let mut v = Vec::new();
        for a in 0..=4 {
            for b in 0..=3 {
                for c in 0..=2 {
                    v.push([
                        1.0 + 2.5 * a as f64,
                        2.0 + 4.0 * b as f64 / 3.0,
                        3.0 + 3.0 * c as f64,
                    ]);
                }
            }
        }
        v
    }

    #[test]
    fn an_unmoved_cage_is_the_identity_for_every_degree() {
        for (degrees, counts) in [
            ([1, 1, 1], [2, 2, 2]),
            ([2, 3, 1], [4, 5, 3]),
            ([3, 3, 3], [4, 6, 5]),
        ] {
            let cage = unit_cage(degrees, counts);
            let rest = cage.rest_points();
            for p in samples() {
                let q = cage.deform(&rest, p);
                for c in 0..3 {
                    assert!(
                        (p[c] - q[c]).abs() < 1e-12,
                        "{degrees:?} {counts:?} {p:?} -> {q:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn moving_every_cage_point_translates_the_captive() {
        let cage = unit_cage([2, 2, 2], [3, 4, 3]);
        let moved: Vec<[f64; 3]> = cage
            .rest_points()
            .iter()
            .map(|p| [p[0] + 1.5, p[1] - 2.0, p[2] + 0.25])
            .collect();
        for p in samples() {
            let q = cage.deform(&moved, p);
            assert!((q[0] - p[0] - 1.5).abs() < 1e-12 && (q[1] - p[1] + 2.0).abs() < 1e-12);
            assert!((q[2] - p[2] - 0.25).abs() < 1e-12);
        }
    }

    #[test]
    fn moving_one_corner_moves_only_points_it_influences() {
        let cage = unit_cage([1, 1, 1], [2, 2, 2]);
        let mut moved = cage.rest_points();
        let corner = cage.index(1, 1, 1);
        moved[corner][2] += 6.0;
        // Trilinear: the far corner (u,v,w)=(1,1,1) rises by 6, the near corner not at
        // all, and the centre by 6 * 1/8.
        let far = cage.deform(&moved, [11.0, 6.0, 9.0]);
        let near = cage.deform(&moved, [1.0, 2.0, 3.0]);
        let centre = cage.deform(&moved, [6.0, 4.0, 6.0]);
        assert!((far[2] - 15.0).abs() < 1e-12 && (near[2] - 3.0).abs() < 1e-12);
        assert!((centre[2] - 6.75).abs() < 1e-12);
    }

    #[test]
    fn points_outside_the_box_are_left_alone() {
        let cage = unit_cage([1, 1, 1], [2, 2, 2]);
        let moved: Vec<[f64; 3]> = cage
            .rest_points()
            .iter()
            .map(|p| [p[0] + 5.0, p[1], p[2]])
            .collect();
        assert_eq!(cage.deform(&moved, [50.0, 3.0, 4.0]), [50.0, 3.0, 4.0]);
    }

    #[test]
    fn a_flat_cage_needs_one_point_across_its_thickness() {
        let flat = [[10.0, 0.0, 0.0], [0.0, 4.0, 0.0], [0.0, 0.0, 0.0]];
        assert!(Cage::new([0.0; 3], flat, [1, 1, 0], [2, 2, 1]).is_some());
        assert!(Cage::new([0.0; 3], flat, [1, 1, 1], [2, 2, 2]).is_none());
        let skew = [[10.0, 0.0, 0.0], [1.0, 4.0, 0.0], [0.0, 0.0, 6.0]];
        assert!(Cage::new([0.0; 3], skew, [1, 1, 1], [2, 2, 2]).is_none());
        assert!(Cage::new([0.0; 3], flat, [2, 1, 0], [2, 2, 1]).is_none());
    }

    #[test]
    fn abi_round_trips_the_identity() {
        let frame = [1.0, 2.0, 3.0, 10.0, 0.0, 0.0, 0.0, 4.0, 0.0, 0.0, 0.0, 6.0];
        let shape = [2u64, 2, 1, 3, 4, 2];
        let mut rest = vec![0.0; 3 * 24];
        let points = [3.0, 3.0, 4.0, 100.0, 0.0, 0.0];
        let mut out = [0.0; 6];
        unsafe {
            let n = narwhal_pure_core_cage_rest_points(
                frame.as_ptr(),
                shape.as_ptr(),
                rest.as_mut_ptr(),
                rest.len(),
            );
            assert_eq!(n, 24);
            let inside = narwhal_pure_core_cage_deform(
                frame.as_ptr(),
                shape.as_ptr(),
                rest.as_ptr(),
                points.as_ptr(),
                2,
                out.as_mut_ptr(),
            );
            assert_eq!(inside, 1);
            assert_eq!(
                narwhal_pure_core_cage_rest_points(
                    frame.as_ptr(),
                    shape.as_ptr(),
                    std::ptr::null_mut(),
                    0
                ),
                i64::from(ERR_NULL)
            );
        }
        for c in 0..6 {
            assert!((out[c] - points[c]).abs() < 1e-12);
        }
    }
}
