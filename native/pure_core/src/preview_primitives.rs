//! Display-only fixed-count preview sampling. Never document/manufacturing geometry.
//! Arithmetic order follows independently captured CPython 3.13 originals.
use std::f64::consts::PI;
const MAX_SEGMENTS: usize = 100_000;
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Metadata {
    pub values: usize,
    pub rows: usize,
    pub lengths: [usize; 3],
}
type Point = [f64; 3];
fn point(v: &[f64], i: usize) -> Point {
    [v[i], v[i + 1], v[i + 2]]
}
fn sub(a: Point, b: Point) -> Point {
    std::array::from_fn(|i| a[i] - b[i])
}
fn dot(a: Point, b: Point) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
fn length(a: Point) -> f64 {
    dot(a, a).powf(0.5)
}
// CPython 3.12+ sum(generator) starts at integer zero, then compensates floats.
fn py_sum(values: Point) -> f64 {
    let (mut total, mut correction) = (0.0_f64, 0.0_f64);
    for value in values {
        let sum = total + value;
        correction += if total.abs() >= value.abs() {
            (total - sum) + value
        } else {
            (value - sum) + total
        };
        total = sum;
    }
    if correction != 0.0 && correction.is_finite() {
        total + correction
    } else {
        total
    }
}
fn ellipse(
    center: Point,
    u: Point,
    ru: f64,
    v: Point,
    rv: f64,
    n: usize,
    group: u32,
) -> Vec<Point> {
    (0..=n)
        .map(|i| {
            let a = 2.0 * PI * i as f64 / n as f64;
            let (c, s) = (a.cos(), a.sin());
            std::array::from_fn(|k| match group {
                0 => center[k] + u[k] * (c * ru) + v[k] * (s * rv),
                1 => center[k] + ru * (c * u[k] + s * v[k]),
                2 => center[k] + u[k] * c + v[k] * s,
                _ => center[k] + ru * c * u[k] + rv * s * v[k],
            })
        })
        .collect()
}
/// Operations: 1 circle, 2 ring, 3 polygon, 4 picked ellipse, 5 ellipsoid,
/// 6 rectangle in host callback coordinates. `flags` is inscribed/stage count.
pub fn sample(op: u32, d: &[f64], n: usize, flags: u32) -> Result<Vec<Vec<Point>>, i32> {
    let expected = match op {
        1 | 2 => 10,
        3 => 12,
        4 => 12,
        5 => 13,
        6 => 6,
        _ => return Err(-2),
    };
    if d.len() != expected || !d.iter().all(|v| v.is_finite()) {
        return Err(-2);
    }
    if op != 6 && !(1..=MAX_SEGMENTS).contains(&n) {
        return Err(-2);
    }
    if (matches!(op, 1 | 2 | 6) && flags != 0) || (op == 6 && n != 0) {
        return Err(-2);
    }
    let c = point(d, 0);
    let rows = match op {
        1..=3 => {
            let (u, v, r) = (point(d, 3), point(d, 6), d[9]);
            if op == 3 && flags > 1 {
                return Err(-2);
            }
            let out = if r <= 0.0 || (op == 3 && n < 3) {
                Vec::new()
            } else if op == 3 {
                let radius = if flags == 1 {
                    r
                } else {
                    r / (PI / n as f64).cos()
                };
                let start = d[10] + if flags == 1 { 0.0 } else { PI / n as f64 };
                (0..=n)
                    .map(|i| {
                        let a = start + d[11] * 2.0 * PI * i as f64 / n as f64;
                        let (co, si) = (a.cos() * radius, a.sin() * radius);
                        std::array::from_fn(|k| c[k] + u[k] * co + v[k] * si)
                    })
                    .collect()
            } else {
                ellipse(c, u, r, v, r, n, if op == 1 { 0 } else { 1 })
            };
            vec![out]
        }
        4 => {
            if !(1..=3).contains(&flags) {
                return Err(-2);
            }
            let (axis, axis2, cursor) = (point(d, 3), point(d, 6), point(d, 9));
            if flags == 1 {
                vec![vec![c, cursor]]
            } else {
                let (u, w) = (sub(axis, c), sub(axis2, c));
                let uu = dot(u, u);
                let k = if uu > 1e-24 { dot(w, u) / uu } else { 0.0 };
                let v = std::array::from_fn(|i| w[i] - k * u[i]);
                if flags == 2 && length(v) <= 1e-12 {
                    vec![vec![c, axis], vec![c, cursor]]
                } else {
                    let out = ellipse(c, u, 1.0, v, 1.0, n, 2);
                    if flags == 2 {
                        vec![out, vec![c, cursor]]
                    } else {
                        vec![out]
                    }
                }
            }
        }
        5 => {
            if !(1..=3).contains(&flags) {
                return Err(-2);
            }
            let (axis, cursor, z) = (point(d, 3), point(d, 6), point(d, 9));
            if flags == 1 {
                vec![vec![c, cursor]]
            } else {
                let v = sub(axis, c);
                let k = py_sum(std::array::from_fn(|i| v[i] * z[i]));
                let v = std::array::from_fn(|i| v[i] - k * z[i]);
                let a = length(v);
                if a <= 0.0 {
                    vec![]
                } else {
                    let x = v.map(|v| v / a);
                    let y = [
                        z[1] * x[2] - z[2] * x[1],
                        z[2] * x[0] - z[0] * x[2],
                        z[0] * x[1] - z[1] * x[0],
                    ];
                    let b = if flags >= 3 {
                        d[12]
                    } else {
                        length(sub(cursor, c))
                    };
                    if b <= 0.0 {
                        vec![vec![c, axis]]
                    } else {
                        let equator = ellipse(c, x, a, y, b, n, 3);
                        let height = length(sub(cursor, c));
                        if flags == 2 || height <= 0.0 {
                            vec![equator]
                        } else {
                            vec![
                                equator,
                                ellipse(c, x, a, z, height, n, 3),
                                ellipse(c, y, b, z, height, n, 3),
                            ]
                        }
                    }
                }
            }
        }
        6 => {
            let b = point(d, 3);
            vec![vec![
                [c[0], c[1], b[2]],
                [b[0], c[1], b[2]],
                b,
                [c[0], b[1], b[2]],
                [c[0], c[1], b[2]],
            ]]
        }
        _ => unreachable!(),
    };
    if rows.iter().flatten().flatten().any(|v| !v.is_finite()) {
        return Err(-2);
    }
    Ok(rows)
}
fn range<T>(p: *const T, n: usize) -> Option<(usize, usize)> {
    if p.is_null() || !(p as usize).is_multiple_of(std::mem::align_of::<T>()) {
        return None;
    }
    let bytes = n.checked_mul(std::mem::size_of::<T>())?;
    if bytes > isize::MAX as usize {
        return None;
    }
    Some((p as usize, (p as usize).checked_add(bytes)?))
}
fn overlaps(a: (usize, usize), b: (usize, usize)) -> bool {
    a.0 < b.1 && b.0 < a.1
}
/// Caller-buffer ABI. No output writes on failure; metadata is published only on
/// success or -3 (capacity query). Inputs, output and metadata must not overlap.
/// -1 pointer/layout, -2 invalid/nonfinite/resource input, -3 capacity.
/// # Safety
/// All nonempty spans must reference live readable/writable allocations. Metadata
/// must be writable for one Metadata. Pointer validity is the caller's obligation.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_preview_sample_v1(
    op: u32,
    data: *const f64,
    data_len: usize,
    segments: usize,
    flags: u32,
    output: *mut f64,
    capacity: usize,
    metadata: *mut Metadata,
) -> i32 {
    let Some(mr) = range(metadata, 1) else {
        return -1;
    };
    let Some(dr) = range(data, data_len) else {
        return -1;
    };
    if overlaps(mr, dr) {
        return -1;
    }
    if capacity > 3 * (MAX_SEGMENTS + 1) * 3 {
        return -2;
    }
    if capacity != 0 {
        let Some(or) = range(output, capacity) else {
            return -1;
        };
        if overlaps(or, mr) || overlaps(or, dr) {
            return -1;
        }
    }
    if data_len > 13 {
        return -2;
    }
    let d = std::slice::from_raw_parts(data, data_len);
    let rows = match sample(op, d, segments, flags) {
        Ok(v) => v,
        Err(e) => return e,
    };
    let mut meta = Metadata {
        rows: rows.len(),
        ..Metadata::default()
    };
    for (i, row) in rows.iter().enumerate() {
        meta.lengths[i] = row.len();
        meta.values += 3 * row.len()
    }
    if capacity < meta.values {
        *metadata = meta;
        return -3;
    }
    if meta.values != 0 {
        let out = std::slice::from_raw_parts_mut(output, meta.values);
        for (dst, value) in out.iter_mut().zip(rows.iter().flatten().flatten()) {
            *dst = *value
        }
    }
    *metadata = meta;
    0
}
