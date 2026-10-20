//! Static effects on paths: blend (interpolate between two shapes), extrude
//! (parallel), distort (push/pull, zipper, twister) and brush-style
//! deformations (smear, twirl, smooth). All work on flattened polylines
//! and return new paths; the results are baked, not kept live.

use crate::geometry::{BezPath, PathEl, Point, Vec2};

/// Resample the first subpath of a path into `n` evenly spaced points.
/// Vertices of the flattened first subpath (0.05 mm tolerance), with the
/// first point repeated at the end when the subpath is closed. Corners are
/// kept exactly, unlike `resample`.
pub fn flat_points(path: &BezPath) -> Vec<Point> {
    let mut pts: Vec<Point> = Vec::new();
    kurbo::flatten(path.elements().iter().copied(), 0.05, &mut |el| match el {
        PathEl::MoveTo(p) => {
            if pts.is_empty() {
                pts.push(p)
            }
        }
        PathEl::LineTo(p) => pts.push(p),
        PathEl::ClosePath => {
            if let Some(f) = pts.first().copied() {
                pts.push(f);
            }
        }
        _ => {}
    });
    pts
}

pub fn resample(path: &BezPath, n: usize) -> Vec<Point> {
    let pts = flat_points(path);
    if pts.len() < 2 || n < 2 {
        return pts;
    }
    let total: f64 = pts.windows(2).map(|w| (w[1] - w[0]).hypot()).sum();
    if total <= 0.0 {
        return vec![pts[0]; n];
    }
    let mut out = Vec::with_capacity(n);
    let mut seg = 0;
    let mut seg_start = 0.0;
    for i in 0..n {
        let target = total * i as f64 / (n - 1) as f64;
        while seg + 1 < pts.len() - 1 && seg_start + (pts[seg + 1] - pts[seg]).hypot() < target {
            seg_start += (pts[seg + 1] - pts[seg]).hypot();
            seg += 1;
        }
        let len = (pts[seg + 1] - pts[seg]).hypot().max(1e-12);
        let t = ((target - seg_start) / len).clamp(0.0, 1.0);
        out.push(pts[seg].lerp(pts[seg + 1], t));
    }
    out
}

/// Closed polygon through points.
pub fn polygon(pts: &[Point]) -> BezPath {
    let mut p = BezPath::new();
    for (i, pt) in pts.iter().enumerate() {
        if i == 0 {
            p.move_to(*pt);
        } else {
            p.line_to(*pt);
        }
    }
    if !pts.is_empty() {
        p.close_path();
    }
    p
}

/// Intermediate shapes between `a` and `b` (excluding both), `steps` of them.
pub fn blend(a: &BezPath, b: &BezPath, steps: usize, samples: usize) -> Vec<BezPath> {
    let pa = resample(a, samples);
    let pb = resample(b, samples);
    if pa.len() != pb.len() || pa.is_empty() {
        return Vec::new();
    }
    // Match the two outlines by shape, not by position: wind b the way a
    // is wound, then start b at the point whose direction from b's centre
    // is nearest the direction of a's start from a's centre. Otherwise the
    // intermediate steps twist through the middle.
    let centroid = |pts: &[Point]| {
        let n = pts.len().max(1) as f64;
        Point::new(
            pts.iter().map(|p| p.x).sum::<f64>() / n,
            pts.iter().map(|p| p.y).sum::<f64>() / n,
        )
    };
    let area = |pts: &[Point]| -> f64 {
        pts.iter()
            .zip(pts.iter().cycle().skip(1))
            .map(|(p, q)| p.x * q.y - q.x * p.y)
            .sum()
    };
    let mut pb = pb;
    if area(&pa) * area(&pb) < 0.0 {
        pb.reverse();
    }
    let (ca, cb) = (centroid(&pa), centroid(&pb));
    let da = pa[0] - ca;
    let start = (0..pb.len())
        .min_by(|i, j| {
            let di = (pb[*i] - cb) - da;
            let dj = (pb[*j] - cb) - da;
            di.hypot()
                .partial_cmp(&dj.hypot())
                .unwrap_or(std::cmp::Ordering::Equal)
        })
        .unwrap_or(0);
    let pb: Vec<Point> = (0..pb.len()).map(|i| pb[(i + start) % pb.len()]).collect();
    (1..=steps)
        .map(|k| {
            let t = k as f64 / (steps + 1) as f64;
            polygon(
                &pa.iter()
                    .zip(&pb)
                    .map(|(x, y)| x.lerp(*y, t))
                    .collect::<Vec<_>>(),
            )
        })
        .collect()
}

/// Parallel extrusion: side faces from each edge toward `depth` offset.
/// Returns the side polygons (back to front order) and the back face.
pub fn extrude(path: &BezPath, depth: Vec2) -> (Vec<BezPath>, BezPath) {
    let pts = resample(path, 64);
    let mut sides = Vec::new();
    for w in pts.windows(2) {
        sides.push(polygon(&[w[0], w[1], w[1] + depth, w[0] + depth]));
    }
    let back = polygon(&pts.iter().map(|p| *p + depth).collect::<Vec<_>>());
    (sides, back)
}

pub use crate::distort::{distort, Distort};

/// The subpaths of `path`, each with whether it is closed.
pub fn subpaths(path: &BezPath) -> Vec<(BezPath, bool)> {
    let mut out = Vec::new();
    let mut cur = BezPath::new();
    let mut closed = false;
    for el in path.elements() {
        match el {
            PathEl::MoveTo(_) => {
                if !cur.elements().is_empty() {
                    out.push((std::mem::take(&mut cur), closed));
                }
                closed = false;
                cur.push(*el);
            }
            PathEl::ClosePath => {
                closed = true;
                cur.push(*el);
            }
            _ => cur.push(*el),
        }
    }
    if !cur.elements().is_empty() {
        out.push((cur, closed));
    }
    out
}

/// A brush stroke step on each subpath the nib (`radius` around `at`)
/// reaches: its points `max_len` apart go through `f` and come back as
/// smooth curves (or straight lines with `sharp`). Subpaths out of reach
/// stay exactly as they were.
fn brush_each(
    path: &BezPath,
    at: Point,
    radius: f64,
    max_len: f64,
    sharp: bool,
    mut f: impl FnMut(Vec<Point>, bool) -> Vec<Point>,
) -> BezPath {
    use crate::geometry::Shape as _;
    let mut out = BezPath::new();
    for (sub, closed) in subpaths(path) {
        if !sub.bounding_box().inflate(radius, radius).contains(at) {
            out.extend(sub.elements().iter().copied());
            continue;
        }
        let pts = f(densify(&sub, max_len), closed);
        let rebuilt = if sharp {
            crate::geometry::polyline_path(&pts, closed)
        } else {
            crate::geometry::smooth_path(&pts, closed)
        };
        out.extend(rebuilt.elements().iter().copied());
    }
    out
}

/// Push points within `radius` of `at` along `delta` (Smear tool).
pub fn smear(path: &BezPath, at: Point, delta: Vec2, radius: f64) -> BezPath {
    brush_each(path, at, radius, radius / 3.0, false, |pts, _| {
        pts.into_iter()
            .map(|p| {
                let d = (p - at).hypot();
                if d < radius {
                    let w = 1.0 - d / radius;
                    p + delta * (w * w)
                } else {
                    p
                }
            })
            .collect()
    })
}

/// Rotate points within `radius` of `at` by up to `angle` (Twirl tool).
pub fn twirl(path: &BezPath, at: Point, angle: f64, radius: f64) -> BezPath {
    brush_each(path, at, radius, radius / 3.0, false, |pts, _| {
        pts.into_iter()
            .map(|p| {
                let v = p - at;
                let d = v.hypot();
                if d < radius {
                    let a = angle * (1.0 - d / radius);
                    at + Vec2::new(v.x * a.cos() - v.y * a.sin(), v.x * a.sin() + v.y * a.cos())
                } else {
                    p
                }
            })
            .collect()
    })
}

/// Reduce nodes near `at` (Smooth tool): simplify and re-smooth.
pub fn smooth(path: &BezPath, at: Point, radius: f64, tolerance: f64) -> BezPath {
    brush_each(path, at, radius, radius / 4.0, false, |pts, _| {
        // Only simplify the run of points near the brush; keep others exact.
        let mut out: Vec<Point> = Vec::new();
        let mut run: Vec<Point> = Vec::new();
        let flush = |run: &mut Vec<Point>, out: &mut Vec<Point>| {
            if run.len() > 2 {
                out.extend(crate::geometry::simplify(run, tolerance));
            } else {
                out.append(run);
            }
            run.clear();
        };
        for p in pts {
            if (p - at).hypot() < radius {
                run.push(p);
            } else {
                flush(&mut run, &mut out);
                out.push(p);
            }
        }
        flush(&mut run, &mut out);
        out
    })
}

/// Pull (`amount` > 0) or push (< 0) the nodes and control points within
/// `radius` of `at` towards or away from it (Attract and Repel tools);
/// nearer ones move more, none past the middle.
pub fn attract(path: &BezPath, at: Point, radius: f64, amount: f64) -> BezPath {
    let f = |p: Point| -> Point {
        let v = at - p;
        let d = v.hypot();
        if d > radius || d < 1e-9 {
            return p;
        }
        let k = (1.0 - d / radius) * amount;
        p + v * (k / d).min(0.9)
    };
    let mut out = BezPath::new();
    for el in path.elements() {
        out.push(match *el {
            PathEl::MoveTo(p) => PathEl::MoveTo(f(p)),
            PathEl::LineTo(p) => PathEl::LineTo(f(p)),
            PathEl::QuadTo(a, p) => PathEl::QuadTo(f(a), f(p)),
            PathEl::CurveTo(a, b, p) => PathEl::CurveTo(f(a), f(b), f(p)),
            PathEl::ClosePath => PathEl::ClosePath,
        });
    }
    out
}

/// A jagged edge within `radius` of `at` (Roughen tool): the curve in
/// points `spacing` apart, each within the nib pushed off the curve by up
/// to `amount` (less towards the nib's edge), joined by straight lines.
/// The spikes come from the points' places, so a step over the same spot
/// gives the same spikes.
pub fn roughen(path: &BezPath, at: Point, radius: f64, amount: f64, spacing: f64) -> BezPath {
    brush_each(path, at, radius, spacing.max(0.05), true, |pts, closed| {
        let n = pts.len();
        if n < 3 {
            return pts;
        }
        (0..n)
            .map(|i| {
                let p = pts[i];
                let d = (p - at).hypot();
                if d > radius {
                    return p;
                }
                let (prev, next) = if closed {
                    (pts[(i + n - 1) % n], pts[(i + 1) % n])
                } else {
                    (pts[i.saturating_sub(1)], pts[(i + 1).min(n - 1)])
                };
                let t = next - prev;
                let len = t.hypot();
                if len < 1e-12 {
                    return p;
                }
                let normal = Vec2::new(-t.y / len, t.x / len);
                // A number from -1 to 1 that depends on the place only.
                let h = ((p.x * 12.9898 + p.y * 78.233).sin() * 43758.5453).fract();
                let r = 2.0 * h.abs() - 1.0;
                p + normal * (r * amount * (1.0 - d / radius))
            })
            .collect()
    })
}

/// Flatten and insert points so no segment is longer than `max_len`.
fn densify(path: &BezPath, max_len: f64) -> Vec<Point> {
    let mut pts: Vec<Point> = Vec::new();
    kurbo::flatten(path.elements().iter().copied(), 0.05, &mut |el| match el {
        PathEl::MoveTo(p) => {
            if pts.is_empty() {
                pts.push(p)
            }
        }
        PathEl::LineTo(p) => pts.push(p),
        _ => {}
    });
    let mut out = Vec::new();
    for w in pts.windows(2) {
        let len = (w[1] - w[0]).hypot();
        let n = (len / max_len.max(0.05)).ceil().max(1.0) as usize;
        for k in 0..n {
            out.push(w[0].lerp(w[1], k as f64 / n as f64));
        }
    }
    if let Some(l) = pts.last() {
        out.push(*l);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geometry::{Rect, Shape as _};

    /// The brushes change only the subpaths the nib reaches, keep open
    /// curves open and closed ones closed.
    #[test]
    fn brushes_touch_only_the_subpaths_they_reach() {
        let mut two = Rect::new(0.0, 0.0, 10.0, 10.0).to_path(0.01);
        let far = Rect::new(100.0, 0.0, 110.0, 10.0).to_path(0.01);
        two.extend(far.elements().iter().copied());
        let at = Point::new(10.0, 5.0);
        for out in [
            smear(&two, at, Vec2::new(3.0, 0.0), 4.0),
            twirl(&two, at, 0.5, 4.0),
            smooth(&two, at, 4.0, 0.3),
            roughen(&two, at, 4.0, 1.0, 0.5),
        ] {
            let subs = subpaths(&out);
            assert_eq!(subs.len(), 2);
            assert!(subs.iter().all(|(_, closed)| *closed));
            // The far square is exactly as it was.
            assert_eq!(subs[1].0, far);
            assert_ne!(subs[0].0.bounding_box(), Rect::ZERO);
        }
        // Smear pushed the near square's right side out.
        let pushed = smear(&two, at, Vec2::new(3.0, 0.0), 4.0);
        assert!(subpaths(&pushed)[0].0.bounding_box().x1 > 11.0);
        // An open curve stays open.
        let mut line = BezPath::new();
        line.move_to(Point::new(0.0, 0.0));
        line.line_to(Point::new(20.0, 0.0));
        let rough = roughen(&line, Point::new(10.0, 0.0), 5.0, 1.0, 0.5);
        assert_eq!(subpaths(&rough).len(), 1);
        assert!(!subpaths(&rough)[0].1);
        let b = rough.bounding_box();
        assert!(b.height() > 0.2 && b.height() <= 2.0, "{b:?}");
        // Attract pulls the corners near the nib inwards; repel pushes.
        let sq = Rect::new(0.0, 0.0, 10.0, 10.0).to_path(0.01);
        let c = Point::new(8.0, 8.0);
        // How far out the corner nearest the nib (10, 10) now is.
        let reach = |p: &BezPath| {
            p.elements()
                .iter()
                .filter_map(|e| match e {
                    PathEl::MoveTo(q) | PathEl::LineTo(q) => Some(q.x + q.y),
                    _ => None,
                })
                .fold(f64::MIN, f64::max)
        };
        let (pulled, pushed) = (attract(&sq, c, 5.0, 1.0), attract(&sq, c, 5.0, -1.0));
        assert!(reach(&pulled) < 20.0 && reach(&pushed) > 20.0);
    }

    #[test]
    fn blend_between_squares_is_in_between() {
        let a = Rect::new(0.0, 0.0, 10.0, 10.0).to_path(0.01);
        let b = Rect::new(20.0, 0.0, 30.0, 10.0).to_path(0.01);
        let mid = blend(&a, &b, 1, 32);
        assert_eq!(mid.len(), 1);
        let c = mid[0].bounding_box().center();
        assert!((c.x - 15.0).abs() < 0.5, "{c:?}");
    }

    #[test]
    fn blend_between_offset_ellipses_keeps_their_size_midway() {
        // Two equal ellipses, one down and to the right of the other: the
        // middle step must be the same size, not collapsed by a twisted
        // point correspondence.
        let a = crate::geometry::Ellipse::from_rect(Rect::new(0.0, 40.0, 30.0, 60.0)).to_path(0.01);
        let b =
            crate::geometry::Ellipse::from_rect(Rect::new(100.0, 0.0, 130.0, 20.0)).to_path(0.01);
        let steps = blend(&a, &b, 5, 64);
        assert_eq!(steps.len(), 5);
        for s in &steps {
            let bb = s.bounding_box();
            assert!((bb.width() - 30.0).abs() < 1.0, "{bb:?}");
            assert!((bb.height() - 20.0).abs() < 1.0, "{bb:?}");
        }
        // A reversed second outline is matched the same way.
        let mut rb = crate::nodes::reverse(&b);
        if rb.elements().is_empty() {
            rb = b.clone();
        }
        for s in blend(&a, &rb, 3, 64) {
            let bb = s.bounding_box();
            assert!((bb.width() - 30.0).abs() < 1.0, "{bb:?}");
        }
    }

    #[test]
    fn twister_keeps_bounds_roughly() {
        let a = Rect::new(0.0, 0.0, 10.0, 10.0).to_path(0.01);
        let t = distort(
            &a,
            Distort::Twister {
                angle_deg: 45.0,
                center: None,
            },
        );
        let b = t.bounding_box();
        assert!(b.width() > 8.0 && b.width() < 16.0);
    }

    #[test]
    fn extrude_makes_side_faces() {
        let a = Rect::new(0.0, 0.0, 10.0, 10.0).to_path(0.01);
        let (sides, back) = extrude(&a, Vec2::new(5.0, 5.0));
        assert!(!sides.is_empty());
        assert!((back.bounding_box().x0 - 5.0).abs() < 1e-6);
    }
}
