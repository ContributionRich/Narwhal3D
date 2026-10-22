//! Shaping: weld, trim, intersect, simplify, front-minus-back, back-minus-
//! front and boundary. Implemented on flattened polygons with i_overlay,
//! so curves become dense polylines (0.02 mm tolerance). A curve-preserving
//! version is on the roadmap.

use crate::geometry::{BezPath, PathEl, Point};
use i_overlay::core::fill_rule::FillRule as IFill;
use i_overlay::core::overlay_rule::OverlayRule;
use i_overlay::float::single::SingleFloatOverlay;

const TOL: f64 = 0.02;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Op {
    Weld,
    Intersect,
    /// Subject minus clip.
    Trim,
    /// Symmetric difference.
    Exclude,
}

/// Flatten a path into closed contours (one per subpath).
pub fn contours(path: &BezPath) -> Vec<Vec<[f64; 2]>> {
    let mut out: Vec<Vec<[f64; 2]>> = Vec::new();
    let mut cur: Vec<[f64; 2]> = Vec::new();
    kurbo::flatten(path.elements().iter().copied(), TOL, &mut |el| match el {
        PathEl::MoveTo(p) => {
            if cur.len() >= 3 {
                out.push(std::mem::take(&mut cur));
            } else {
                cur.clear();
            }
            cur.push([p.x, p.y]);
        }
        PathEl::LineTo(p) => cur.push([p.x, p.y]),
        PathEl::ClosePath => {}
        _ => {}
    });
    if cur.len() >= 3 {
        out.push(cur);
    }
    out
}

/// Build a path from shapes (outer contour + holes each).
fn from_shapes(shapes: Vec<Vec<Vec<[f64; 2]>>>) -> BezPath {
    let mut path = BezPath::new();
    for shape in shapes {
        for contour in shape {
            let mut first = true;
            for p in contour {
                let pt = Point::new(p[0], p[1]);
                if first {
                    path.move_to(pt);
                    first = false;
                } else {
                    path.line_to(pt);
                }
            }
            path.close_path();
        }
    }
    path
}

/// Combine `subject` and `clip` (both in the same coordinate space).
pub fn overlay(subject: &BezPath, clip: &BezPath, op: Op) -> BezPath {
    let subj = contours(subject);
    let clp = contours(clip);
    let rule = match op {
        Op::Weld => OverlayRule::Union,
        Op::Intersect => OverlayRule::Intersect,
        Op::Trim => OverlayRule::Difference,
        Op::Exclude => OverlayRule::Xor,
    };
    let result = subj.overlay(&clp, rule, IFill::EvenOdd);
    from_shapes(result)
}

/// Combine `subject` and `clip` as [`overlay`] does, each separate piece
/// of the result (its outline with its holes) a path of its own.
pub fn overlay_parts(subject: &BezPath, clip: &BezPath, op: Op) -> Vec<BezPath> {
    let subj = contours(subject);
    let clp = contours(clip);
    let rule = match op {
        Op::Weld => OverlayRule::Union,
        Op::Intersect => OverlayRule::Intersect,
        Op::Trim => OverlayRule::Difference,
        Op::Exclude => OverlayRule::Xor,
    };
    subj.overlay(&clp, rule, IFill::EvenOdd)
        .into_iter()
        .map(|shape| from_shapes(vec![shape]))
        .filter(|p| !p.elements().is_empty())
        .collect()
}

/// How an offset outline turns its corners.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Join {
    /// Sharp, the sides run on until they meet (very sharp corners, under
    /// about 9 degrees, are cut off).
    #[default]
    Miter,
    Round,
    /// Cut off straight.
    Bevel,
}

/// The smallest corner a mitred join keeps sharp, radians.
const MITER_MIN: f64 = 0.15;

/// Offset a closed path outward (positive) or inward (negative) by `d` mm,
/// with round corners. Used by Contour and outline-to-object.
pub fn offset(path: &BezPath, d: f64) -> BezPath {
    offset_with(path, d, Join::Round)
}

/// Offset a closed path outward (positive) or inward (negative) by `d` mm,
/// the corners turned as `join` says.
pub fn offset_with(path: &BezPath, d: f64, join: Join) -> BezPath {
    use i_overlay::float::simplify::SimplifyShape;
    use i_overlay::mesh::float::outline::offset::OutlineOffset;
    use i_overlay::mesh::float::style::{LineJoin, OutlineStyle};
    if !d.is_finite() {
        return BezPath::new();
    }
    let subj = contours(path);
    // Simplify guarantees orientation (outer CCW, holes CW) and no self-intersections.
    let clean = subj.simplify_shape(IFill::EvenOdd);
    let style = OutlineStyle::new(d).line_join(match join {
        Join::Miter => LineJoin::Miter(MITER_MIN),
        Join::Round => LineJoin::Round(0.05),
        Join::Bevel => LineJoin::Bevel,
    });
    let out = clean.outline(&style);
    from_shapes(out)
}

/// The area swept by a round or square nib of width `width` along a
/// polyline (the Eraser's band). A single point gives a dot.
pub fn stroke_band(points: &[Point], width: f64, square: bool) -> BezPath {
    use crate::geometry::Shape as _;
    use i_overlay::mesh::float::stroke::offset::StrokeOffset;
    use i_overlay::mesh::float::style::{LineCap, LineJoin, StrokeStyle};
    if points.is_empty() || width <= 0.0 {
        return BezPath::new();
    }
    let r = width / 2.0;
    if points.len() == 1 || points.iter().all(|p| (*p - points[0]).hypot() < 1e-9) {
        let c = points[0];
        return if square {
            crate::geometry::Rect::new(c.x - r, c.y - r, c.x + r, c.y + r).to_path(0.01)
        } else {
            crate::geometry::Circle::new(c, r).to_path(0.01)
        };
    }
    let pts: Vec<[f64; 2]> = points.iter().map(|p| [p.x, p.y]).collect();
    let style = if square {
        StrokeStyle::new(width)
            .line_join(LineJoin::Miter(MITER_MIN))
            .start_cap(LineCap::Square)
            .end_cap(LineCap::Square)
    } else {
        StrokeStyle::new(width)
            .line_join(LineJoin::Round(0.05))
            .start_cap(LineCap::Round(0.05))
            .end_cap(LineCap::Round(0.05))
    };
    from_shapes(pts.stroke(style, false))
}

/// Area swept by a calligraphic nib along a path: an ellipse of width
/// `width` along `nib_angle_deg` and `width * stretch` across it. Each
/// flattened segment contributes the parallelogram the nib's support
/// sweeps, each vertex the nib itself; the union of them is the outline
/// (open subpaths get nib-shaped ends). `stretch` 1 is the round pen.
pub fn calligraphic_band(path: &BezPath, width: f64, stretch: f64, nib_angle_deg: f64) -> BezPath {
    if width <= 0.0 {
        return BezPath::new();
    }
    let a = width / 2.0;
    let b = a * stretch.clamp(0.01, 1.0);
    let ang = nib_angle_deg.to_radians();
    let e1 = [ang.cos(), ang.sin()];
    let e2 = [-ang.sin(), ang.cos()];
    // Polylines of every subpath, closed ones with the first point repeated.
    let mut lines: Vec<Vec<Point>> = Vec::new();
    let mut cur: Vec<Point> = Vec::new();
    kurbo::flatten(path.elements().iter().copied(), 0.05, &mut |el| match el {
        PathEl::MoveTo(p) => {
            if cur.len() >= 2 {
                lines.push(std::mem::take(&mut cur));
            } else {
                cur.clear();
            }
            cur.push(p);
        }
        PathEl::LineTo(p) => cur.push(p),
        PathEl::ClosePath => {
            if let Some(f) = cur.first().copied() {
                cur.push(f);
            }
        }
        _ => {}
    });
    if !cur.is_empty() {
        lines.push(cur);
    }
    let ellipse = |c: Point| -> Vec<[f64; 2]> {
        (0..24)
            .map(|i| {
                let t = i as f64 / 24.0 * std::f64::consts::TAU;
                let (x, y) = (a * t.cos(), b * t.sin());
                [c.x + x * e1[0] + y * e2[0], c.y + x * e1[1] + y * e2[1]]
            })
            .collect()
    };
    let mut polys: Vec<Vec<[f64; 2]>> = Vec::new();
    for line in &lines {
        for (i, p) in line.iter().enumerate() {
            polys.push(ellipse(*p));
            let Some(q) = line.get(i + 1) else {
                continue;
            };
            let d = *q - *p;
            let len = d.hypot();
            if len < 1e-9 {
                continue;
            }
            let n = [-d.y / len, d.x / len];
            // Support half-width of the ellipse along n.
            let h = ((a * (n[0] * e1[0] + n[1] * e1[1])).powi(2)
                + (b * (n[0] * e2[0] + n[1] * e2[1])).powi(2))
            .sqrt();
            if h < 1e-9 {
                continue;
            }
            polys.push(vec![
                [p.x + n[0] * h, p.y + n[1] * h],
                [q.x + n[0] * h, q.y + n[1] * h],
                [q.x - n[0] * h, q.y - n[1] * h],
                [p.x - n[0] * h, p.y - n[1] * h],
            ]);
        }
    }
    // Consistent orientation so the non-zero union adds up.
    for poly in polys.iter_mut() {
        let area: f64 = poly
            .iter()
            .zip(poly.iter().cycle().skip(1))
            .map(|(p, q)| p[0] * q[1] - q[0] * p[1])
            .sum();
        if area < 0.0 {
            poly.reverse();
        }
    }
    let empty: Vec<Vec<[f64; 2]>> = Vec::new();
    from_shapes(polys.overlay(&empty, OverlayRule::Subject, IFill::NonZero))
}

/// The polylines of every subpath (flattened to `tol`), closed ones with
/// the first point repeated at the end.
fn polylines(path: &BezPath, tol: f64) -> Vec<(Vec<Point>, bool)> {
    let mut lines: Vec<(Vec<Point>, bool)> = Vec::new();
    let mut cur: Vec<Point> = Vec::new();
    kurbo::flatten(path.elements().iter().copied(), tol, &mut |el| match el {
        PathEl::MoveTo(p) => {
            if cur.len() >= 2 {
                lines.push((std::mem::take(&mut cur), false));
            } else {
                cur.clear();
            }
            cur.push(p);
        }
        PathEl::LineTo(p) => cur.push(p),
        PathEl::ClosePath => {
            if let Some(f) = cur.first().copied() {
                cur.push(f);
            }
            if cur.len() >= 2 {
                lines.push((std::mem::take(&mut cur), true));
            } else {
                cur.clear();
            }
        }
        _ => {}
    });
    if cur.len() >= 2 {
        lines.push((cur, false));
    }
    lines
}

/// Total length of a path's flattened subpaths.
pub fn path_length(path: &BezPath) -> f64 {
    polylines(path, 0.05)
        .iter()
        .map(|(l, _)| l.windows(2).map(|w| (w[1] - w[0]).hypot()).sum::<f64>())
        .sum()
}

/// Where along the path (0 to 1, by length over all subpaths) the point
/// nearest to `p` is, its distance and which side of the path's direction
/// `p` is on (true: left).
pub fn locate_on_path(path: &BezPath, p: Point) -> Option<(f64, f64, bool)> {
    let lines = polylines(path, 0.05);
    let total: f64 = lines
        .iter()
        .map(|(l, _)| l.windows(2).map(|w| (w[1] - w[0]).hypot()).sum::<f64>())
        .sum();
    if total <= 1e-12 {
        return None;
    }
    let mut best: Option<(f64, f64, bool)> = None;
    let mut run = 0.0;
    for (line, _) in &lines {
        for w in line.windows(2) {
            let (a, b) = (w[0], w[1]);
            let d = b - a;
            let len2 = d.x * d.x + d.y * d.y;
            if len2 <= 1e-18 {
                continue;
            }
            let len = len2.sqrt();
            let u = (((p - a).x * d.x + (p - a).y * d.y) / len2).clamp(0.0, 1.0);
            let q = a + d * u;
            let dist = (p - q).hypot();
            if best.map(|(_, bd, _)| dist < bd).unwrap_or(true) {
                let cross = d.x * (p - a).y - d.y * (p - a).x;
                best = Some(((run + u * len) / total, dist, cross >= 0.0));
            }
            run += len;
        }
    }
    best
}

/// The point `t` of the way along the path (0 to 1, by length over all
/// subpaths) and the unit normal to the left of its direction there.
pub fn point_on_path(path: &BezPath, t: f64) -> Option<(Point, [f64; 2])> {
    let lines = polylines(path, 0.05);
    let total: f64 = lines
        .iter()
        .map(|(l, _)| l.windows(2).map(|w| (w[1] - w[0]).hypot()).sum::<f64>())
        .sum();
    if total <= 1e-12 {
        return None;
    }
    let want = t.clamp(0.0, 1.0) * total;
    let mut run = 0.0;
    let mut last = None;
    for (line, _) in &lines {
        for w in line.windows(2) {
            let d = w[1] - w[0];
            let len = d.hypot();
            if len <= 1e-12 {
                continue;
            }
            let n = [-d.y / len, d.x / len];
            if run + len >= want {
                let u = ((want - run) / len).clamp(0.0, 1.0);
                return Some((w[0] + d * u, n));
            }
            run += len;
            last = Some((w[1], n));
        }
    }
    last
}

/// The area of a variable-width outline: every segment of the flattened
/// path widened by the reach `width_at` gives at its ends (left and right
/// separately), joined with wedges at the corners; butt ends.
pub fn variable_band(path: &BezPath, base: f64, nodes: &[crate::style::WidthNode]) -> BezPath {
    let lines = polylines(path, 0.05);
    let total: f64 = lines
        .iter()
        .map(|(l, _)| l.windows(2).map(|w| (w[1] - w[0]).hypot()).sum::<f64>())
        .sum();
    if total <= 1e-12 {
        return BezPath::new();
    }
    let reach = |s: f64| crate::style::width_at(base, nodes, s / total);
    // Short pieces so the width can change along long straight segments,
    // split at the nodes so their reach is met exactly.
    let step = (total / 256.0).max(0.05);
    let mut marks: Vec<f64> = nodes.iter().map(|n| n.at.clamp(0.0, 1.0) * total).collect();
    marks.sort_by(|a, b| a.total_cmp(b));
    let mut polys: Vec<Vec<[f64; 2]>> = Vec::new();
    let mut run = 0.0;
    for (line, closed) in &lines {
        // Resampled points with their length along the whole path.
        let mut pts: Vec<(Point, f64)> = Vec::new();
        for w in line.windows(2) {
            let (a, b) = (w[0], w[1]);
            let len = (b - a).hypot();
            if pts.is_empty() {
                pts.push((a, run));
            }
            if len <= 1e-12 {
                continue;
            }
            let k = ((len / step).ceil() as usize).clamp(1, 4096);
            let mut cuts: Vec<f64> = (1..k).map(|i| i as f64 / k as f64).collect();
            cuts.extend(
                marks
                    .iter()
                    .filter(|m| **m > run + 1e-9 && **m < run + len - 1e-9)
                    .map(|m| (m - run) / len),
            );
            cuts.sort_by(|x, y| x.total_cmp(y));
            for u in cuts {
                pts.push((a + (b - a) * u, run + u * len));
            }
            run += len;
            pts.push((b, run));
        }
        // Unit normals (to the left) of each piece.
        let normals: Vec<Option<[f64; 2]>> = pts
            .windows(2)
            .map(|w| {
                let d = w[1].0 - w[0].0;
                let len = d.hypot();
                (len > 1e-12).then(|| [-d.y / len, d.x / len])
            })
            .collect();
        for (i, w) in pts.windows(2).enumerate() {
            let Some(n) = normals.get(i).copied().flatten() else {
                continue;
            };
            let ((p, s0), (q, s1)) = (w[0], w[1]);
            let (l0, r0) = reach(s0);
            let (l1, r1) = reach(s1);
            polys.push(vec![
                [p.x + n[0] * l0, p.y + n[1] * l0],
                [q.x + n[0] * l1, q.y + n[1] * l1],
                [q.x - n[0] * r1, q.y - n[1] * r1],
                [p.x - n[0] * r0, p.y - n[1] * r0],
            ]);
        }
        // Round joins between consecutive pieces (and around the seam of
        // a closed subpath): a fan from one normal to the next on each
        // side.
        let valid: Vec<(usize, [f64; 2])> = normals
            .iter()
            .enumerate()
            .filter_map(|(i, n)| n.map(|n| (i, n)))
            .collect();
        let count = valid.len();
        let pairs = if *closed {
            count
        } else {
            count.saturating_sub(1)
        };
        for k in 0..pairs {
            let (Some(&(i, a)), Some(&(_, b))) = (valid.get(k), valid.get((k + 1) % count.max(1)))
            else {
                continue;
            };
            let Some(&(c, sc)) = pts.get(i + 1) else {
                continue;
            };
            let turn = (a[0] * b[1] - a[1] * b[0]).atan2(a[0] * b[0] + a[1] * b[1]);
            if turn.abs() < 1e-6 {
                continue;
            }
            let (l, r) = reach(sc);
            let a0 = a[1].atan2(a[0]);
            let steps = ((turn.abs() / 0.2).ceil() as usize).clamp(1, 32);
            for (sign, len) in [(1.0, l), (-1.0, r)] {
                if len <= 0.0 {
                    continue;
                }
                let mut fan = vec![[c.x, c.y]];
                for j in 0..=steps {
                    let ang = a0 + turn * j as f64 / steps as f64;
                    fan.push([c.x + sign * ang.cos() * len, c.y + sign * ang.sin() * len]);
                }
                polys.push(fan);
            }
        }
    }
    for poly in polys.iter_mut() {
        let area: f64 = poly
            .iter()
            .zip(poly.iter().cycle().skip(1))
            .map(|(p, q)| p[0] * q[1] - q[0] * p[1])
            .sum();
        if area < 0.0 {
            poly.reverse();
        }
    }
    polys.retain(|p| {
        p.iter()
            .zip(p.iter().cycle().skip(1))
            .map(|(a, b)| a[0] * b[1] - b[0] * a[1])
            .sum::<f64>()
            .abs()
            > 1e-12
    });
    let empty: Vec<Vec<[f64; 2]>> = Vec::new();
    from_shapes(polys.overlay(&empty, OverlayRule::Subject, IFill::NonZero))
}

/// The filled area that stands for an outline that cannot be drawn as a
/// plain stroke (a calligraphic nib or a variable width), for a path in
/// page space and the object's scale (1 when the outline does not scale
/// with the object).
pub fn outline_band(path: &BezPath, stroke: &crate::style::Stroke, scale: f64) -> Option<BezPath> {
    if !stroke.widths.is_empty() {
        let nodes: Vec<crate::style::WidthNode> = stroke
            .widths
            .iter()
            .map(|n| crate::style::WidthNode {
                at: n.at,
                left: n.left * scale,
                right: n.right * scale,
            })
            .collect();
        return Some(variable_band(path, stroke.width * scale, &nodes));
    }
    if stroke.stretch < 0.999 && stroke.width > crate::style::Stroke::HAIRLINE + 1e-9 {
        return Some(calligraphic_band(
            path,
            stroke.width * scale,
            stroke.stretch,
            stroke.nib_angle,
        ));
    }
    None
}

/// The area a plain outline covers along `path` (page space), as a path
/// to fill with the non-zero rule: its width, caps, joins (miter limit
/// 4) and dashes. A hairline covers no area: the result is empty.
pub fn stroke_area(path: &BezPath, stroke: &crate::style::Stroke) -> BezPath {
    use kurbo::{Cap, Join};
    if !stroke.width.is_finite() || stroke.width <= crate::style::Stroke::HAIRLINE + 1e-9 {
        return BezPath::new();
    }
    let cap = match stroke.cap {
        crate::style::LineCap::Butt => Cap::Butt,
        crate::style::LineCap::Round => Cap::Round,
        crate::style::LineCap::Square => Cap::Square,
    };
    let join = match stroke.join {
        crate::style::LineJoin::Miter => Join::Miter,
        crate::style::LineJoin::Round => Join::Round,
        crate::style::LineJoin::Bevel => Join::Bevel,
    };
    let mut style = kurbo::Stroke::new(stroke.width)
        .with_caps(cap)
        .with_join(join)
        .with_miter_limit(4.0);
    let dashes: Vec<f64> = stroke
        .dash
        .iter()
        .map(|d| (d * stroke.width).max(0.0))
        .collect();
    if !dashes.is_empty() && dashes.iter().sum::<f64>() > 1e-9 {
        style = style.with_dashes(0.0, dashes);
    }
    kurbo::stroke(
        path.iter(),
        &style,
        &kurbo::StrokeOpts::default(),
        (stroke.width / 100.0).max(1e-3),
    )
}

/// Union of one path with itself: removes self-overlaps (Simplify).
pub fn simplify(path: &BezPath) -> BezPath {
    let subj = contours(path);
    let empty: Vec<Vec<[f64; 2]>> = Vec::new();
    let result = subj.overlay(&empty, OverlayRule::Subject, IFill::NonZero);
    from_shapes(result)
}

#[cfg(test)]
mod tests {
    #[test]
    fn variable_band_follows_the_width_nodes() {
        use super::{locate_on_path, outline_band, variable_band};
        use crate::geometry::{BezPath, Point, Shape as _};
        use crate::style::{Stroke, WidthNode};
        let mut line = BezPath::new();
        line.move_to(Point::new(0.0, 0.0));
        line.line_to(Point::new(100.0, 0.0));
        // No nodes: the base width everywhere, butt ends.
        let b = variable_band(&line, 2.0, &[]).bounding_box();
        assert!((b.height() - 2.0).abs() < 1e-6 && (b.width() - 100.0).abs() < 1e-6);
        // A 10 mm node in the middle: 10 mm there, 2 mm at the ends.
        let band = variable_band(&line, 2.0, &[WidthNode::symmetric(0.5, 10.0)]);
        let b = band.bounding_box();
        assert!((b.height() - 10.0).abs() < 1e-3, "{b:?}");
        assert!(band.contains(Point::new(50.0, 4.9)));
        assert!(!band.contains(Point::new(2.0, 1.5)));
        assert!(band.contains(Point::new(2.0, 0.9)));
        // Only to the left (up, for a line going right).
        let node = WidthNode {
            at: 0.5,
            left: 6.0,
            right: 0.0,
        };
        let b = variable_band(&line, 0.0, &[node]).bounding_box();
        assert!(b.y0 > -1e-6 && (b.y1 - 6.0).abs() < 1e-3, "{b:?}");
        // Through the stroke, scaled with the object.
        let mut s = Stroke::new(crate::color::Color::BLACK, 2.0);
        assert!(outline_band(&line, &s, 1.0).is_none());
        s.widths.push(WidthNode::symmetric(0.5, 10.0));
        let b = outline_band(&line, &s, 2.0)
            .map(|p| p.bounding_box())
            .unwrap_or_default();
        assert!((b.height() - 20.0).abs() < 1e-3, "{b:?}");
        // Locating: a quarter along, 3 mm to the right.
        let (t, d, left) = locate_on_path(&line, Point::new(25.0, -3.0)).unwrap_or_default();
        assert!((t - 0.25).abs() < 1e-9 && (d - 3.0).abs() < 1e-9 && !left);
    }

    #[test]
    fn closed_variable_bands_have_no_gaps_at_the_corners() {
        use super::variable_band;
        use crate::geometry::{Point, Rect, Shape as _};
        let sq = Rect::new(0.0, 0.0, 50.0, 50.0).to_path(0.01);
        let band = variable_band(&sq, 4.0, &[]);
        // The outer corner is covered on both sides of the seam.
        assert!(band.contains(Point::new(-1.2, -1.2)));
        assert!(band.contains(Point::new(51.2, 51.2)));
        assert!(!band.contains(Point::new(-1.6, -1.6)));
        assert!(!band.contains(Point::new(25.0, 25.0)));
    }

    #[test]
    fn calligraphic_band_is_thin_along_the_nib_and_full_across_it() {
        use super::calligraphic_band;
        use crate::geometry::{BezPath, Point, Shape as _};
        // Flat nib at 0 degrees, 2 mm wide, stretch 0.1.
        let mut horizontal = BezPath::new();
        horizontal.move_to(Point::new(0.0, 0.0));
        horizontal.line_to(Point::new(50.0, 0.0));
        let hb = calligraphic_band(&horizontal, 2.0, 0.1, 0.0).bounding_box();
        // Across a horizontal stroke the nib offers only its 0.2 mm side.
        assert!(hb.height() < 0.25 && hb.height() > 0.15, "{hb:?}");
        let mut vertical = BezPath::new();
        vertical.move_to(Point::new(0.0, 0.0));
        vertical.line_to(Point::new(0.0, 50.0));
        let vb = calligraphic_band(&vertical, 2.0, 0.1, 0.0).bounding_box();
        assert!((vb.width() - 2.0).abs() < 0.05, "{vb:?}");
        // The round pen (stretch 1) is a plain 2 mm stroke with round ends.
        let rb = calligraphic_band(&horizontal, 2.0, 1.0, 0.0).bounding_box();
        assert!(
            (rb.height() - 2.0).abs() < 0.05 && (rb.width() - 52.0).abs() < 0.05,
            "{rb:?}"
        );
    }

    use super::*;
    use crate::geometry::{Rect, Shape as _};

    fn square(x: f64) -> BezPath {
        Rect::new(x, 0.0, x + 10.0, 10.0).to_path(0.01)
    }

    #[test]
    fn stroke_area_covers_the_outline_and_hairlines_cover_nothing() {
        let mut line = BezPath::new();
        line.move_to((0.0, 0.0));
        line.line_to((50.0, 0.0));
        let s = crate::style::Stroke::new(crate::Color::BLACK, 2.0);
        let a = stroke_area(&line, &s);
        let b = a.bounding_box();
        assert!(
            (b.width() - 50.0).abs() < 0.01 && (b.height() - 2.0).abs() < 0.01,
            "{b:?}"
        );
        assert!((a.area().abs() - 100.0).abs() < 0.5, "{}", a.area());
        // Round caps reach half the width past the ends.
        let mut round = s.clone();
        round.cap = crate::style::LineCap::Round;
        assert!((stroke_area(&line, &round).bounding_box().width() - 52.0).abs() < 0.05);
        // Dashes leave gaps: half on, half off.
        let mut dashed = s.clone();
        dashed.dash = vec![2.5, 2.5];
        let d = stroke_area(&line, &dashed).area().abs();
        assert!((d - 50.0).abs() < 2.0, "{d}");
        let hair = crate::style::Stroke::hairline(crate::Color::BLACK);
        assert!(stroke_area(&line, &hair).elements().is_empty());
    }

    #[test]
    fn weld_two_overlapping_squares() {
        let w = overlay(&square(0.0), &square(5.0), Op::Weld);
        let b = w.bounding_box();
        assert!((b.x0 - 0.0).abs() < 1e-6 && (b.x1 - 15.0).abs() < 1e-6);
        assert!((w.area().abs() - 150.0).abs() < 1e-3);
    }

    #[test]
    fn offset_grows_and_shrinks() {
        let grown = offset(&square(0.0), 2.0);
        let b = grown.bounding_box();
        assert!(
            (b.x0 + 2.0).abs() < 0.05 && (b.x1 - 12.0).abs() < 0.05,
            "{b:?}"
        );
        let shrunk = offset(&square(0.0), -2.0);
        let b = shrunk.bounding_box();
        assert!(
            (b.x0 - 2.0).abs() < 0.05 && (b.x1 - 8.0).abs() < 0.05,
            "{b:?}"
        );
    }

    #[test]
    fn offset_corners_are_mitred_round_or_bevelled() {
        let area = |j: Join| offset_with(&square(0.0), 2.0, j).area().abs();
        assert!(
            (area(Join::Miter) - 196.0).abs() < 0.2,
            "{}",
            area(Join::Miter)
        );
        let round = 180.0 + std::f64::consts::PI * 4.0;
        assert!(
            (area(Join::Round) - round).abs() < 0.3,
            "{}",
            area(Join::Round)
        );
        assert!(
            (area(Join::Bevel) - 188.0).abs() < 0.2,
            "{}",
            area(Join::Bevel)
        );
        assert!(offset_with(&square(0.0), f64::NAN, Join::Miter)
            .elements()
            .is_empty());
    }

    #[test]
    fn intersect_and_trim() {
        let i = overlay(&square(0.0), &square(5.0), Op::Intersect);
        assert!((i.area().abs() - 50.0).abs() < 1e-3);
        let t = overlay(&square(0.0), &square(5.0), Op::Trim);
        assert!((t.area().abs() - 50.0).abs() < 1e-3);
        assert!((t.bounding_box().x1 - 5.0).abs() < 1e-6);
    }
}
