//! Shapes: traced outlines that are a circle, an ellipse, a rectangle (its corners square or
//! rounded) or a regular polygon are told so, and come out as exactly that shape.
//!
//! An outline is a shape when every point of it lies within a small tolerance of the shape that
//! its own area, middle and extents describe: nothing is taken for a shape that it merely
//! resembles.

use vectorcraft_geom::{Affine, ParamCurve, PathData, Point, Rect, SubPath, shapes};

/// A shape an outline was found to be (pixel coordinates; angles in degrees, clockwise on screen).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum TracedShape {
    /// A rectangle `width` × `height` about `center`, turned by `rotation`, its corners rounded
    /// with `radius` (0: square corners).
    Rectangle { center: Point, width: f64, height: f64, radius: f64, rotation: f64 },
    /// An ellipse with radii `rx` and `ry` about `center`, turned by `rotation` (a circle: equal
    /// radii, no rotation).
    Ellipse { center: Point, rx: f64, ry: f64, rotation: f64 },
    /// A regular polygon: its first corner `rotation` from straight up.
    Polygon { center: Point, radius: f64, sides: u32, rotation: f64 },
}

impl TracedShape {
    /// The shape's outline.
    pub fn path(&self) -> PathData {
        let turned = |p: PathData, c: Point, deg: f64| p.transformed(Affine::rotate_about(deg.to_radians(), c));
        match *self {
            TracedShape::Rectangle { center, width, height, radius, rotation } => {
                let r = Rect::from_center_size(center, (width, height));
                turned(if radius > 0.0 { shapes::rounded_rectangle(r, radius) } else { shapes::rectangle(r) }, center, rotation)
            }
            TracedShape::Ellipse { center, rx, ry, rotation } => {
                turned(shapes::ellipse(Rect::from_center_size(center, (2.0 * rx, 2.0 * ry))), center, rotation)
            }
            TracedShape::Polygon { center, radius, sides, rotation } => shapes::polygon(center, radius, sides, rotation),
        }
    }
}

/// How far a point may lie from the shape: half a pixel, and for large shapes 0.2% of their size.
fn tolerance(size: f64) -> f64 {
    (0.002 * size).max(0.5)
}

/// The shape the closed outline `sp` is, if it is one (and at least 6 px across).
pub(crate) fn recognise(sp: &SubPath) -> Option<TracedShape> {
    if !sp.closed || sp.anchors.len() < 2 {
        return None;
    }
    // Points all along the outline.
    let pts: Vec<Point> = (0..sp.segment_count()).flat_map(|i| (0..24).map(move |k| sp.segment(i).eval(f64::from(k) / 24.0))).collect();
    let n = pts.len();
    if n < 8 || !pts.iter().all(|p| p.x.is_finite() && p.y.is_finite()) {
        return None;
    }
    // Area, middle and second moments of the polygon through them.
    let (mut area2, mut cx, mut cy) = (0.0, 0.0, 0.0);
    for i in 0..n {
        let (a, b) = (pts[i], pts[(i + 1) % n]);
        let cross = a.x * b.y - b.x * a.y;
        area2 += cross;
        cx += (a.x + b.x) * cross;
        cy += (a.y + b.y) * cross;
    }
    let area = area2.abs() / 2.0;
    if area < 20.0 {
        return None;
    }
    let c = Point::new(cx / (3.0 * area2), cy / (3.0 * area2));
    let (mut ixx, mut iyy, mut ixy) = (0.0, 0.0, 0.0);
    for i in 0..n {
        let (a, b) = (pts[i] - c, pts[(i + 1) % n] - c);
        let cross = a.x * b.y - b.x * a.y;
        ixx += (a.x * a.x + a.x * b.x + b.x * b.x) * cross;
        iyy += (a.y * a.y + a.y * b.y + b.y * b.y) * cross;
        ixy += (a.x * b.y + 2.0 * a.x * a.y + 2.0 * b.x * b.y + b.x * a.y) * cross;
    }
    // Per unit of area: the variances of x and y and their covariance.
    let (sxx, syy, sxy) = (ixx / (6.0 * area2), iyy / (6.0 * area2), ixy / (12.0 * area2));
    let mean = (sxx + syy) / 2.0;
    let spread = ((sxx - syy) / 2.0).hypot(sxy);
    let (major, minor) = (mean + spread, (mean - spread).max(0.0));
    // The direction of the longer axis (none when the shape is as wide every way).
    let axis = if spread < 0.01 * mean { 0.0 } else { 0.5 * (2.0 * sxy).atan2(sxx - syy) };
    let size = 4.0 * major.sqrt();
    let tol = tolerance(size);
    if size < 6.0 || !size.is_finite() {
        return None;
    }
    let local = |p: Point, angle: f64| {
        let d = p - c;
        (d.x * angle.cos() + d.y * angle.sin(), d.y * angle.cos() - d.x * angle.sin())
    };

    // An ellipse: its radii are twice the roots of its variances.
    let (rx, ry) = (2.0 * major.sqrt(), 2.0 * minor.sqrt());
    if ry > 2.0 {
        let off = pts.iter().map(|&p| {
            let (x, y) = local(p, axis);
            // Scaled to a circle: how far from it, in pixels of the larger radius (no nearer than
            // that to the ellipse: a long thin bar is no flat ellipse).
            ((x / rx).hypot(y / ry) - 1.0).abs() * rx
        });
        if off.fold(0.0, f64::max) <= tol {
            // Radii this close are a circle's, of the outline's area: a circle without anti-aliasing
            // is fitted a little wider one way than the other.
            let circle = (rx - ry).abs() <= 2.0 * tol;
            let (rx, ry) = if circle { ((rx * ry).sqrt(), (rx * ry).sqrt()) } else { (rx, ry) };
            return Some(TracedShape::Ellipse { center: c, rx, ry, rotation: if circle { 0.0 } else { axis.to_degrees() } });
        }
    }

    // A rectangle, along its longest straight side (a square has no longer axis to go by).
    let side = (0..sp.segment_count())
        .filter(|&i| sp.segment_is_line(i))
        .map(|i| sp.segment(i))
        .max_by(|a, b| a.p0.distance(a.p3).total_cmp(&b.p0.distance(b.p3)));
    if let Some(side) = side {
        let d = side.p3 - side.p0;
        // Turned by under 45° either way: a rectangle is the same a quarter turn on.
        let quarter = std::f64::consts::FRAC_PI_2;
        let angle = d.y.atan2(d.x);
        let angle = angle - quarter * (angle / quarter).round();
        let xy: Vec<(f64, f64)> = pts.iter().map(|&p| local(p, angle)).collect();
        let (hw, hh) = xy.iter().fold((0.0f64, 0.0f64), |m, p| (m.0.max(p.0.abs()), m.1.max(p.1.abs())));
        // Square corners first; else rounded ones, which take (4 − π) r² off the area.
        let rounded = ((4.0 * hw * hh - area) / (4.0 - std::f64::consts::PI)).max(0.0).sqrt().min(hw.min(hh));
        for radius in [0.0, rounded] {
            let off = xy.iter().map(|&(x, y)| {
                // Distance to the rounded rectangle's edge.
                let (qx, qy) = (x.abs() - hw + radius, y.abs() - hh + radius);
                (qx.max(0.0).hypot(qy.max(0.0)) + qx.max(qy).min(0.0) - radius).abs()
            });
            if hw > 2.0 && hh > 2.0 && (radius == 0.0 || radius >= 0.75) && off.fold(0.0, f64::max) <= tol {
                return Some(TracedShape::Rectangle { center: c, width: 2.0 * hw, height: 2.0 * hh, radius, rotation: angle.to_degrees() });
            }
        }
    }

    // A regular polygon: its corners evenly round the middle, its sides straight between them.
    let sides = sp.anchors.len();
    if (3..=12).contains(&sides) {
        let radius = sp.anchors.iter().map(|a| a.p.distance(c)).sum::<f64>() / sides as f64;
        let first = sp.anchors.first()?.p - c;
        let rotation = (first.y.atan2(first.x).to_degrees() + 90.0).rem_euclid(360.0 / sides as f64);
        let shape = TracedShape::Polygon { center: c, radius, sides: sides as u32, rotation };
        let ideal: Vec<Point> = shape.path().subpaths.first()?.anchors.iter().map(|a| a.p).collect();
        // Every corner of the polygon has one of the outline's on it, and every point of the
        // outline lies on one of the polygon's sides.
        let cornered = ideal.iter().all(|q| sp.anchors.iter().any(|a| q.distance(a.p) <= 1.5 * tol));
        let on_a_side = |p: Point| {
            (0..ideal.len()).any(|i| {
                let (a, b) = (ideal[i], ideal[(i + 1) % ideal.len()]);
                let t = ((p - a).dot(b - a) / (b - a).hypot2()).clamp(0.0, 1.0);
                p.distance(a + (b - a) * t) <= tol
            })
        };
        if cornered && pts.iter().all(|&p| on_a_side(p)) {
            // Four equal sides are a square: a rectangle, its sides half a right angle from its corners.
            let side = radius * std::f64::consts::SQRT_2;
            let turned = (rotation + 45.0).rem_euclid(90.0);
            let square = TracedShape::Rectangle {
                center: c,
                width: side,
                height: side,
                radius: 0.0,
                rotation: if turned > 45.0 { turned - 90.0 } else { turned },
            };
            return Some(if sides == 4 { square } else { shape });
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn is(shape: TracedShape) -> Option<TracedShape> {
        recognise(shape.path().subpaths.first().unwrap())
    }

    #[test]
    fn each_shape_is_told_from_its_own_outline() {
        let c = Point::new(60.3, 45.6);
        match is(TracedShape::Ellipse { center: c, rx: 20.0, ry: 20.0, rotation: 0.0 }) {
            Some(TracedShape::Ellipse { center, rx, ry, rotation }) => {
                assert!(center.distance(c) < 0.05 && (rx - 20.0).abs() < 0.05 && rx == ry && rotation == 0.0, "{center:?} {rx} {ry}")
            }
            other => panic!("{other:?}"),
        }
        match is(TracedShape::Ellipse { center: c, rx: 30.0, ry: 12.0, rotation: 25.0 }) {
            Some(TracedShape::Ellipse { rx, ry, rotation, .. }) => {
                assert!((rx - 30.0).abs() < 0.3 && (ry - 12.0).abs() < 0.3 && (rotation - 25.0).abs() < 0.5, "{rx} {ry} {rotation}")
            }
            other => panic!("{other:?}"),
        }
        match is(TracedShape::Rectangle { center: c, width: 50.0, height: 20.0, radius: 0.0, rotation: 10.0 }) {
            Some(TracedShape::Rectangle { width, height, radius, rotation, .. }) => {
                assert!((width - 50.0).abs() < 0.1 && (height - 20.0).abs() < 0.1 && radius == 0.0 && (rotation - 10.0).abs() < 0.1)
            }
            other => panic!("{other:?}"),
        }
        match is(TracedShape::Rectangle { center: c, width: 50.0, height: 30.0, radius: 8.0, rotation: 0.0 }) {
            Some(TracedShape::Rectangle { width, radius, .. }) => {
                assert!((width - 50.0).abs() < 0.2 && (radius - 8.0).abs() < 0.5, "{width} {radius}")
            }
            other => panic!("{other:?}"),
        }
        for sides in [3, 5, 6, 8] {
            match is(TracedShape::Polygon { center: c, radius: 25.0, sides, rotation: 7.0 }) {
                Some(TracedShape::Polygon { radius, sides: n, rotation, .. }) => {
                    assert!(n == sides && (radius - 25.0).abs() < 0.1 && (rotation - 7.0).abs() < 0.1, "{sides}: {radius} {rotation}")
                }
                other => panic!("{sides} sides: {other:?}"),
            }
        }
    }

    #[test]
    fn what_only_resembles_a_shape_is_none() {
        // A star, an L, a rectangle with one corner cut, an egg.
        let star = shapes::star(Point::new(50.0, 50.0), 30.0, 12.0, 5, 0.0);
        assert_eq!(recognise(&star.subpaths[0]), None);
        let poly = |pts: &[(f64, f64)]| SubPath::polyline(&pts.iter().map(|&p| Point::from(p)).collect::<Vec<_>>(), true);
        assert_eq!(recognise(&poly(&[(0.0, 0.0), (40.0, 0.0), (40.0, 15.0), (15.0, 15.0), (15.0, 40.0), (0.0, 40.0)])), None);
        assert_eq!(recognise(&poly(&[(0.0, 0.0), (40.0, 0.0), (40.0, 22.0), (34.0, 30.0), (0.0, 30.0)])), None);
        let mut egg = shapes::ellipse(Rect::new(10.0, 10.0, 70.0, 50.0));
        egg.subpaths[0].anchors[2].p.x += 4.0;
        assert_eq!(recognise(&egg.subpaths[0]), None);
        // Too small to say, open, or not a number.
        assert_eq!(is(TracedShape::Ellipse { center: Point::new(5.0, 5.0), rx: 2.0, ry: 2.0, rotation: 0.0 }), None);
        let mut open = shapes::rectangle(Rect::new(0.0, 0.0, 30.0, 20.0)).subpaths[0].clone();
        open.closed = false;
        assert_eq!(recognise(&open), None);
        let mut bad = shapes::rectangle(Rect::new(0.0, 0.0, 30.0, 20.0)).subpaths[0].clone();
        bad.anchors[1].p.x = f64::NAN;
        assert_eq!(recognise(&bad), None);
    }
}
