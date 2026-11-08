// SPDX-License-Identifier: GPL-3.0-or-later
//! Schema-1 DISPLAY-proxy comparison, independent of face numbering and planar diagonals.
//! The exact OCCT master remains the geometry. No mesh is saved as replacement geometry.
use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::Read;
use std::path::Path;

type Point = [f64; 3];
type Triangle = [Point; 3];
const MAX_TRIANGLES: usize = 1_000_000;
const MAGIC: &[u8; 8] = b"NWPX0001";

pub fn compare_files(left: &Path, right: &Path) -> Result<bool, String> {
    equivalent(&read(left)?, &read(right)?)
}

fn read(path: &Path) -> Result<Vec<Triangle>, String> {
    let mut file = File::open(path).map_err(|e| e.to_string())?;
    let size = file.metadata().map_err(|e| e.to_string())?.len();
    let mut header = [0_u8; 16];
    file.read_exact(&mut header).map_err(|e| e.to_string())?;
    if &header[..8] != MAGIC {
        return Err("invalid display-proxy comparison header".into());
    }
    let count = u64::from_le_bytes(header[8..16].try_into().map_err(|_| "bad count")?);
    if count == 0 || count > MAX_TRIANGLES as u64 || size != 16 + count * 72 {
        return Err("invalid or oversized display-proxy comparison payload".into());
    }
    let mut triangles = Vec::with_capacity(count as usize);
    let mut bytes = [0_u8; 72];
    for _ in 0..count {
        file.read_exact(&mut bytes).map_err(|e| e.to_string())?;
        let mut triangle = [[0.0; 3]; 3];
        for (i, coordinate) in triangle.iter_mut().flatten().enumerate() {
            *coordinate = f64::from_le_bytes(
                bytes[i * 8..i * 8 + 8]
                    .try_into()
                    .map_err(|_| "bad float")?,
            );
            if !coordinate.is_finite() {
                return Err("non-finite display-proxy vertex".into());
            }
        }
        triangles.push(triangle);
    }
    Ok(triangles)
}

fn key(p: Point) -> [u64; 3] {
    p.map(|v| if v == 0.0 { 0 } else { v.to_bits() })
}

fn length(p: Point) -> f64 {
    // Scaled Euclidean norm; sqrt maps to the CPU instruction, unlike libm hypot.
    let scale = p.iter().fold(0.0_f64, |m, value| m.max(value.abs()));
    if scale == 0.0 {
        return 0.0;
    }
    scale
        * p.iter()
            .map(|v| (v / scale) * (v / scale))
            .sum::<f64>()
            .sqrt()
}

fn cell(p: Point, epsilon: f64) -> [i64; 3] {
    p.map(|v| (v / epsilon).floor() as i64)
}

pub fn equivalent(actual: &[Triangle], expected: &[Triangle]) -> Result<bool, String> {
    if actual.is_empty()
        || expected.is_empty()
        || actual.len() > MAX_TRIANGLES
        || expected.len() > MAX_TRIANGLES
    {
        return Err("invalid display-proxy triangle count".into());
    }
    if actual
        .iter()
        .chain(expected)
        .flatten()
        .flatten()
        .any(|v| !v.is_finite())
    {
        return Err("non-finite display-proxy vertex".into());
    }
    let magnitude = expected
        .iter()
        .flatten()
        .flatten()
        .fold(1.0_f64, |m, v| m.max(v.abs()));
    // r3.Mesh stores f32 positions, while transformed Brep vertices retain f64 positions.
    // This fixed schema-1 display precision budget is NOT a manufacturing tolerance.
    let epsilon = 32.0 * f64::from(f32::EPSILON) * magnitude;
    let mut ids = BTreeMap::new();
    let mut vertices = Vec::new();
    let mut bins: BTreeMap<[i64; 3], Vec<usize>> = BTreeMap::new();
    let mut reference = Vec::with_capacity(expected.len());
    for triangle in expected {
        let mut row = [0; 3];
        for (i, p) in triangle.iter().enumerate() {
            let id = *ids.entry(key(*p)).or_insert_with(|| {
                let id = vertices.len();
                vertices.push(*p);
                bins.entry(cell(*p, epsilon)).or_default().push(id);
                id
            });
            row[i] = id;
        }
        reference.push(row);
    }
    let mut mapping = BTreeMap::new();
    let mut used = BTreeSet::new();
    let mut supplied = Vec::with_capacity(actual.len());
    for triangle in actual {
        let mut row = [0; 3];
        for (i, p) in triangle.iter().enumerate() {
            let id = if let Some(id) = mapping.get(&key(*p)) {
                *id
            } else {
                let c = cell(*p, epsilon);
                let mut best: Option<(f64, usize)> = None;
                for x in -1..=1 {
                    for y in -1..=1 {
                        for z in -1..=1 {
                            if let Some(candidates) = bins.get(&[c[0] + x, c[1] + y, c[2] + z]) {
                                for &id in candidates {
                                    let q = vertices[id];
                                    let d = length([p[0] - q[0], p[1] - q[1], p[2] - q[2]]);
                                    if d <= epsilon && best.is_none_or(|b| (d, id) < b) {
                                        best = Some((d, id));
                                    }
                                }
                            }
                        }
                    }
                }
                let Some((_, id)) = best else {
                    return Ok(false);
                };
                mapping.insert(key(*p), id);
                id
            };
            row[i] = id;
            used.insert(id);
        }
        supplied.push(row);
    }
    if used.len() != vertices.len() {
        return Ok(false);
    }
    Ok(surface(&supplied, &vertices)? == surface(&reference, &vertices)?)
}

fn root(parent: &mut [usize], mut i: usize) -> usize {
    while parent[i] != i {
        parent[i] = parent[parent[i]];
        i = parent[i];
    }
    i
}

type Boundary = Vec<Vec<(usize, usize)>>;
type EdgeIncidents = BTreeMap<(usize, usize), Vec<(usize, usize, usize)>>;
fn surface(triangles: &[[usize; 3]], vertices: &[Point]) -> Result<Boundary, String> {
    let mut parent: Vec<_> = (0..triangles.len()).collect();
    let mut normals = Vec::with_capacity(triangles.len());
    let mut edges: EdgeIncidents = BTreeMap::new();
    let mut unique = BTreeSet::new();
    for (i, t) in triangles.iter().enumerate() {
        let mut sorted = *t;
        sorted.sort();
        if sorted[0] == sorted[1] || sorted[1] == sorted[2] || !unique.insert(sorted) {
            return Err("degenerate or duplicate display-proxy triangle".into());
        }
        let [a, b, c] = t.map(|v| vertices[v]);
        let u: Point = std::array::from_fn(|j| b[j] - a[j]);
        let v: Point = std::array::from_fn(|j| c[j] - a[j]);
        let n = [
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        ];
        let length = length(n);
        if length == 0.0 || !length.is_finite() {
            return Err("invalid display-proxy triangle normal".into());
        }
        normals.push(n.map(|x| x / length));
        for (a, b) in [(t[0], t[1]), (t[1], t[2]), (t[2], t[0])] {
            edges
                .entry((a.min(b), a.max(b)))
                .or_default()
                .push((i, a, b));
        }
    }
    for rows in edges.values() {
        if rows.len() > 2 {
            return Err("nonmanifold display-proxy edge".into());
        }
        if rows.len() == 2 {
            let (i, a, b) = rows[0];
            let (j, c, d) = rows[1];
            if (a, b) != (d, c) {
                return Err("inconsistent display-proxy winding".into());
            }
            let delta = normals[i]
                .iter()
                .zip(normals[j])
                .map(|(a, b)| (a - b) * (a - b))
                .sum::<f64>();
            if delta <= 1e-16 {
                let ri = root(&mut parent, i);
                let rj = root(&mut parent, j);
                parent[ri] = rj;
            }
        }
    }
    let mut boundaries: BTreeMap<usize, Vec<(usize, usize)>> = BTreeMap::new();
    for rows in edges.values() {
        if rows.len() == 2 && root(&mut parent, rows[0].0) == root(&mut parent, rows[1].0) {
            continue;
        }
        for &(i, a, b) in rows {
            boundaries
                .entry(root(&mut parent, i))
                .or_default()
                .push((a, b));
        }
    }
    let mut result: Vec<_> = boundaries.into_values().collect();
    for edges in &mut result {
        edges.sort();
    }
    result.sort();
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn square() -> (Vec<Triangle>, Vec<Triangle>) {
        let (a, b, c, d) = ([0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 0.]);
        (vec![[a, b, c], [a, c, d]], vec![[b, c, d], [b, d, a]])
    }
    #[test]
    fn order_and_planar_diagonals_are_not_geometry() {
        let (a, b) = square();
        assert_eq!(equivalent(&a, &b), Ok(true));
        let mut rounded = a.clone();
        for t in &mut rounded {
            for p in t {
                p[0] += 1e-7;
            }
        }
        assert_eq!(equivalent(&rounded, &b), Ok(true));
    }
    #[test]
    fn a_nonplanar_diagonal_is_geometry_not_a_numbering_choice() {
        let (mut a, mut b) = square();
        for triangle in a.iter_mut().chain(&mut b) {
            for point in triangle {
                if point[0] == 1.0 && point[1] == 1.0 {
                    point[2] = 0.25;
                }
            }
        }
        assert_eq!(equivalent(&a, &b), Ok(false));
    }

    #[test]
    fn geometry_holes_winding_and_duplicates_still_fail() {
        let (a, b) = square();
        let mut bent = a.clone();
        bent[0][0][2] = 0.1;
        assert_eq!(equivalent(&bent, &b), Ok(false));
        assert_ne!(equivalent(&a[..1], &b), Ok(true));
        let reversed: Vec<_> = a.iter().map(|t| [t[0], t[2], t[1]]).collect();
        assert_ne!(equivalent(&reversed, &b), Ok(true));
        assert_ne!(equivalent(&[a[0], a[0]], &b), Ok(true));
        let mut nan = a;
        nan[0][0][0] = f64::NAN;
        assert!(equivalent(&nan, &b).is_err());
    }
}
