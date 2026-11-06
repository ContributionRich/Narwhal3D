//! Display-only ear clipping for planar face fills and caps.
//!
//! Mirrors `narwhal3d/tessellate.py` `ear_clip` step for step: the same signed-area
//! orientation, the strictly-convex fan shortcut, the reflex-vertex candidate set,
//! the inclusive point-in-triangle test, the stall guard and the partial result on
//! a non-simple loop. The triangles index a DISPLAY mesh; they are never stored,
//! measured, booleaned or exported as document geometry.

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
/// Larger than any boundary loop the bridge samples for one planar face.
const MAX_POINTS: usize = 1 << 20;

fn signed_area(poly: &[[f64; 2]]) -> f64 {
    let n = poly.len();
    let mut s = 0.0;
    for i in 0..n {
        let [x0, y0] = poly[i];
        let [x1, y1] = poly[(i + 1) % n];
        s += x0 * y1 - x1 * y0;
    }
    0.5 * s
}

fn cross(a: [f64; 2], b: [f64; 2], c: [f64; 2]) -> f64 {
    (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
}

/// Inclusive barycentric test, as `tessellate._point_in_triangle`.
fn point_in_triangle(p: [f64; 2], a: [f64; 2], b: [f64; 2], c: [f64; 2]) -> bool {
    let [px, py] = p;
    let [ax, ay] = a;
    let [bx, by] = b;
    let [cx, cy] = c;
    let d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
    if d.abs() < 1e-14 {
        return false;
    }
    let l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d;
    let l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d;
    let l3 = 1.0 - l1 - l2;
    let eps = 1e-9;
    l1 >= -eps && l2 >= -eps && l3 >= -eps
}

/// The strictly-convex fan of `tessellate._convex_fan`, or `None`.
fn convex_fan(poly: &[[f64; 2]], idx: &[usize]) -> Option<Vec<[usize; 3]>> {
    let n = idx.len();
    if n < 3 {
        return None;
    }
    for i in 0..n {
        let i0 = idx[(i + n - 1) % n];
        let i2 = idx[(i + 1) % n];
        if cross(poly[i0], poly[idx[i]], poly[i2]) <= 1e-14 {
            return None;
        }
    }
    let anchor = idx[0];
    Some((1..n - 1).map(|i| [anchor, idx[i], idx[i + 1]]).collect())
}

/// Triangles for a simple polygon, in the order `tessellate.ear_clip` returns them.
/// A stalled (non-simple) loop returns the triangles found so far.
pub fn ear_clip(poly: &[[f64; 2]]) -> Vec<[usize; 3]> {
    let n = poly.len();
    if n < 3 {
        return Vec::new();
    }
    let mut idx: Vec<usize> = (0..n).collect();
    if signed_area(poly) < 0.0 {
        idx.reverse();
    }
    if let Some(fan) = convex_fan(poly, &idx) {
        return fan;
    }
    let mut triangles = Vec::with_capacity(n - 2);
    let mut is_reflex = vec![false; n];
    let mut reflex = Vec::new();
    let limit = n.saturating_mul(n).saturating_add(1);
    let mut guard = 0usize;
    while idx.len() > 3 && guard < limit {
        guard += 1;
        let m = idx.len();
        for &q in &reflex {
            is_reflex[q] = false;
        }
        reflex.clear();
        for q in 0..m {
            let q1 = idx[q];
            if cross(poly[idx[(q + m - 1) % m]], poly[q1], poly[idx[(q + 1) % m]]) <= 0.0
                && !is_reflex[q1]
            {
                is_reflex[q1] = true;
                reflex.push(q1);
            }
        }
        let mut clipped = false;
        for i in 0..m {
            let i0 = idx[(i + m - 1) % m];
            let i1 = idx[i];
            let i2 = idx[(i + 1) % m];
            let (a, b, c) = (poly[i0], poly[i1], poly[i2]);
            if cross(a, b, c) <= 0.0 {
                continue;
            }
            if reflex
                .iter()
                .any(|&j| j != i0 && j != i1 && j != i2 && point_in_triangle(poly[j], a, b, c))
            {
                continue;
            }
            triangles.push([i0, i1, i2]);
            idx.remove(i);
            clipped = true;
            break;
        }
        if !clipped {
            break;
        }
    }
    if idx.len() == 3 {
        triangles.push([idx[0], idx[1], idx[2]]);
    }
    triangles
}

/// Ear-clip packed xy `points` into caller-owned display triangle indices.
///
/// Writes up to `point_count - 2` index triples and the number of triangles to
/// `out_triangles`; fewer than `point_count - 2` means the loop stalled (non-simple)
/// and the caller should use its fallback display, exactly as with the Python result.
///
/// # Safety
/// `points` is readable for `point_count * 2` values, `output` is writable for
/// `output_capacity` values and `out_triangles` is writable.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_ear_clip_display(
    points: *const f64,
    point_count: usize,
    output: *mut u64,
    output_capacity: usize,
    out_triangles: *mut u64,
) -> i32 {
    if out_triangles.is_null() {
        return ERR_NULL;
    }
    *out_triangles = 0;
    if point_count > MAX_POINTS {
        return ERR_INPUT;
    }
    let Some(values) = crate::checked_slice(points, point_count * 2) else {
        return ERR_NULL;
    };
    if !values.iter().all(|v| v.is_finite()) {
        return ERR_INPUT;
    }
    let needed = point_count.saturating_sub(2) * 3;
    if output_capacity < needed {
        return ERR_SIZE;
    }
    let Some(out) = crate::checked_slice_mut(output, needed) else {
        return ERR_NULL;
    };
    let poly: Vec<[f64; 2]> = values.chunks_exact(2).map(|p| [p[0], p[1]]).collect();
    let triangles = ear_clip(&poly);
    for (slot, triangle) in out.chunks_exact_mut(3).zip(&triangles) {
        for (value, &index) in slot.iter_mut().zip(triangle) {
            *value = index as u64;
        }
    }
    *out_triangles = triangles.len() as u64;
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn star(n: usize) -> Vec<[f64; 2]> {
        (0..n)
            .map(|i| {
                let r = if i % 2 == 1 { 1.0 } else { 0.45 };
                let t = std::f64::consts::TAU * i as f64 / n as f64;
                [r * t.cos(), r * t.sin()]
            })
            .collect()
    }

    fn area(poly: &[[f64; 2]], tris: &[[usize; 3]]) -> f64 {
        tris.iter()
            .map(|&[a, b, c]| 0.5 * cross(poly[a], poly[b], poly[c]))
            .sum()
    }

    #[test]
    fn convex_loop_is_a_fan_from_the_first_vertex() {
        let square = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]];
        assert_eq!(ear_clip(&square), vec![[0, 1, 2], [0, 2, 3]]);
    }

    #[test]
    fn clockwise_input_is_oriented_counter_clockwise() {
        let square = [[0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]];
        assert_eq!(ear_clip(&square), vec![[3, 2, 1], [3, 1, 0]]);
    }

    #[test]
    fn concave_star_is_fully_triangulated_with_its_exact_area() {
        let poly = star(40);
        let tris = ear_clip(&poly);
        assert_eq!(tris.len(), 38);
        assert!((area(&poly, &tris) - signed_area(&poly)).abs() < 1e-12);
    }

    #[test]
    fn a_reflex_vertex_on_the_diagonal_blocks_the_ear() {
        // The L-shape's reflex corner (1, 1) lies on the diagonal from (2, 1) to (0, 1)...
        let poly = [
            [0.0, 0.0],
            [2.0, 0.0],
            [2.0, 1.0],
            [1.0, 1.0],
            [1.0, 2.0],
            [0.0, 2.0],
        ];
        let tris = ear_clip(&poly);
        assert_eq!(tris.len(), 4);
        assert!((area(&poly, &tris) - 3.0).abs() < 1e-12);
    }

    #[test]
    fn non_simple_loops_stall_with_the_python_partial_result() {
        // Expected values are what tessellate.ear_clip returns for the same loops.
        let figure8 = [
            [0.0, 0.0],
            [2.0, 2.0],
            [4.0, 0.0],
            [4.0, 2.0],
            [2.0, 0.0],
            [0.0, 2.0],
        ];
        assert_eq!(ear_clip(&figure8), vec![[5, 0, 1], [1, 2, 3]]);
        let duplicate = [
            [0.0, 0.0],
            [1.0, 0.0],
            [1.0, 0.0],
            [1.0, 1.0],
            [0.0, 1.0],
            [0.5, 0.5],
        ];
        assert_eq!(ear_clip(&duplicate), vec![[3, 4, 5]]);
    }

    #[test]
    fn export_checks_size_and_input() {
        let poly: Vec<f64> = star(10).concat();
        let mut out = vec![0_u64; 24];
        let mut count = 99;
        let rc = unsafe {
            narwhal_pure_core_ear_clip_display(poly.as_ptr(), 10, out.as_mut_ptr(), 23, &mut count)
        };
        assert_eq!((rc, count), (ERR_SIZE, 0));
        let rc = unsafe {
            narwhal_pure_core_ear_clip_display(poly.as_ptr(), 10, out.as_mut_ptr(), 24, &mut count)
        };
        assert_eq!((rc, count), (0, 8));
        let flat: Vec<u64> = ear_clip(&star(10))
            .concat()
            .iter()
            .map(|&i| i as u64)
            .collect();
        assert_eq!(out, flat);
        let mut bad = poly.clone();
        bad[3] = f64::INFINITY;
        let rc = unsafe {
            narwhal_pure_core_ear_clip_display(bad.as_ptr(), 10, out.as_mut_ptr(), 24, &mut count)
        };
        assert_eq!(rc, ERR_INPUT);
        let rc = unsafe {
            narwhal_pure_core_ear_clip_display(poly.as_ptr(), 2, out.as_mut_ptr(), 0, &mut count)
        };
        assert_eq!((rc, count), (0, 0));
    }
}
