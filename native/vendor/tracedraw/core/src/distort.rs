//! Distortions of an outline about a centre: Push and Pull, Zipper and
//! Twister. They work on the path's own nodes and segments, so every
//! subpath stays a subpath and an open curve stays open.
//!
//! - Push and Pull moves each node along the line from the centre by
//!   `amount` percent of its distance (away for a positive amount, in for
//!   a negative one) and the control points of the segments the other way
//!   by the same share, so the segments bow against the nodes: pushed
//!   nodes give spikes with hollow sides, pulled nodes give petals.
//!   Straight segments become curves with their control points at a third
//!   and two thirds.
//! - Zipper puts `frequency` teeth on every segment: the segment is cut
//!   into `2 * frequency` equal steps of its parameter, the inner points
//!   alternate `amplitude` to one side of the segment and to the other,
//!   the nodes stay. Random scales each tooth by a fixed pseudo-random
//!   share; Local scales them by how near they are to the centre (full at
//!   the centre, none at the farthest node); Smooth rounds the teeth.
//! - Twister turns every point about the centre by `angle` times its
//!   distance over the farthest distance, so the centre stays and the
//!   farthest point turns by the whole angle. The outline is first cut
//!   into short steps so the turn bends it smoothly.

use crate::geometry::{
    BezPath, CubicBez, Line, ParamCurve, ParamCurveArclen, PathEl, PathSeg, Point, Shape as _, Vec2,
};
use kurbo::QuadBez;

/// A distortion and its centre (in the space of the path it is applied
/// to; `None` for the middle of the path's bounds).
#[derive(Debug, Clone, Copy, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(tag = "mode", rename_all = "lowercase")]
pub enum Distort {
    /// Nodes pushed away from the centre (positive) or pulled in
    /// (negative), percent of their distance, -200 to 200.
    PushPull {
        amount: f64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        center: Option<Point>,
    },
    /// Teeth `amplitude` mm high, `frequency` on each segment.
    Zipper {
        amplitude: f64,
        frequency: u32,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        center: Option<Point>,
        #[serde(default, skip_serializing_if = "std::ops::Not::not")]
        random: bool,
        #[serde(default, skip_serializing_if = "std::ops::Not::not")]
        smooth: bool,
        #[serde(default, skip_serializing_if = "std::ops::Not::not")]
        local: bool,
    },
    /// Turned about the centre, `angle_deg` at the farthest point
    /// (counterclockwise when positive).
    Twister {
        angle_deg: f64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        center: Option<Point>,
    },
}

impl Distort {
    /// The centre it was given, if any.
    pub fn center(&self) -> Option<Point> {
        match *self {
            Distort::PushPull { center, .. }
            | Distort::Zipper { center, .. }
            | Distort::Twister { center, .. } => center,
        }
    }

    /// The same distortion about another centre.
    pub fn with_center(self, at: Option<Point>) -> Distort {
        match self {
            Distort::PushPull { amount, .. } => Distort::PushPull { amount, center: at },
            Distort::Zipper {
                amplitude,
                frequency,
                random,
                smooth,
                local,
                ..
            } => Distort::Zipper {
                amplitude,
                frequency,
                center: at,
                random,
                smooth,
                local,
            },
            Distort::Twister { angle_deg, .. } => Distort::Twister {
                angle_deg,
                center: at,
            },
        }
    }
}

/// The highest Zipper frequency and amplitude, and the largest Twister
/// angle (degrees) and Push and Pull amount taken.
pub const MAX_FREQUENCY: u32 = 100;
pub const MAX_AMPLITUDE: f64 = 1000.0;
pub const MAX_ANGLE: f64 = 3600.0;
pub const MAX_AMOUNT: f64 = 200.0;

fn finite(p: Point) -> bool {
    p.x.is_finite() && p.y.is_finite()
}

/// Apply `how` to `path`.
pub fn distort(path: &BezPath, how: Distort) -> BezPath {
    let b = path.bounding_box();
    if !(b.x0.is_finite() && b.y0.is_finite() && b.x1.is_finite() && b.y1.is_finite()) {
        return path.clone();
    }
    // A centre far off the outline (a damaged file) is not taken.
    let reach = b.inflate(1e5, 1e5);
    let c = how
        .center()
        .filter(|p| finite(*p) && reach.contains(*p))
        .unwrap_or(b.center());
    let out = match how {
        Distort::PushPull { amount, .. } => {
            let s = if amount.is_finite() {
                amount.clamp(-MAX_AMOUNT, MAX_AMOUNT) / 100.0
            } else {
                0.0
            };
            push_pull(path, c, s)
        }
        Distort::Zipper {
            amplitude,
            frequency,
            random,
            smooth,
            local,
            ..
        } => {
            let a = if amplitude.is_finite() {
                amplitude.clamp(0.0, MAX_AMPLITUDE)
            } else {
                0.0
            };
            zipper(
                path,
                c,
                a,
                frequency.min(MAX_FREQUENCY),
                random,
                smooth,
                local,
            )
        }
        Distort::Twister { angle_deg, .. } => {
            let a = if angle_deg.is_finite() {
                angle_deg.clamp(-MAX_ANGLE, MAX_ANGLE)
            } else {
                0.0
            };
            twister(path, c, a.to_radians())
        }
    };
    let ob = out.bounding_box();
    if ob.x0.is_finite() && ob.y0.is_finite() && ob.x1.is_finite() && ob.y1.is_finite() {
        out
    } else {
        path.clone()
    }
}

/// One subpath: its segments and whether it closes.
struct Sub {
    segs: Vec<PathSeg>,
    closed: bool,
    start: Point,
}

/// The subpaths of `path` as segments, the closing line of a closed one
/// included as a segment when its last point is not its first.
fn subpaths(path: &BezPath) -> Vec<Sub> {
    let mut out: Vec<Sub> = Vec::new();
    let mut cur: Option<Sub> = None;
    let mut last = Point::ZERO;
    for el in path.elements() {
        match *el {
            PathEl::MoveTo(p) => {
                if let Some(s) = cur.take() {
                    out.push(s);
                }
                cur = Some(Sub {
                    segs: Vec::new(),
                    closed: false,
                    start: p,
                });
                last = p;
            }
            PathEl::LineTo(p) => {
                let s = cur.get_or_insert(Sub {
                    segs: Vec::new(),
                    closed: false,
                    start: last,
                });
                s.segs.push(PathSeg::Line(Line::new(last, p)));
                last = p;
            }
            PathEl::QuadTo(q, p) => {
                let s = cur.get_or_insert(Sub {
                    segs: Vec::new(),
                    closed: false,
                    start: last,
                });
                s.segs.push(PathSeg::Quad(QuadBez::new(last, q, p)));
                last = p;
            }
            PathEl::CurveTo(a, b, p) => {
                let s = cur.get_or_insert(Sub {
                    segs: Vec::new(),
                    closed: false,
                    start: last,
                });
                s.segs.push(PathSeg::Cubic(CubicBez::new(last, a, b, p)));
                last = p;
            }
            PathEl::ClosePath => {
                if let Some(mut s) = cur.take() {
                    if (last - s.start).hypot() > 1e-9 {
                        s.segs.push(PathSeg::Line(Line::new(last, s.start)));
                    }
                    s.closed = true;
                    last = s.start;
                    out.push(s);
                }
            }
        }
    }
    if let Some(s) = cur.take() {
        out.push(s);
    }
    // A subpath that ends where it started is closed (an ellipse has no
    // closing element).
    for s in &mut out {
        if !s.closed
            && s.segs.len() > 1
            && s.segs
                .last()
                .is_some_and(|g| (g.end() - s.start).hypot() < 1e-6)
        {
            s.closed = true;
        }
    }
    out
}

/// A segment as a cubic: lines with their control points at a third and
/// two thirds, quadratics raised.
fn as_cubic(seg: &PathSeg) -> (Point, Point, Point, Point) {
    match *seg {
        PathSeg::Line(l) => (
            l.p0,
            l.p0.lerp(l.p1, 1.0 / 3.0),
            l.p0.lerp(l.p1, 2.0 / 3.0),
            l.p1,
        ),
        PathSeg::Quad(q) => (
            q.p0,
            q.p0 + (q.p1 - q.p0) * (2.0 / 3.0),
            q.p2 + (q.p1 - q.p2) * (2.0 / 3.0),
            q.p2,
        ),
        PathSeg::Cubic(c) => (c.p0, c.p1, c.p2, c.p3),
    }
}

fn push_pull(path: &BezPath, c: Point, s: f64) -> BezPath {
    let node = |p: Point| c + (p - c) * (1.0 + s);
    let handle = |p: Point| c + (p - c) * (1.0 - s);
    let mut out = BezPath::new();
    for sub in subpaths(path) {
        out.move_to(node(sub.start));
        for seg in &sub.segs {
            let (_, a, b, p) = as_cubic(seg);
            out.curve_to(handle(a), handle(b), node(p));
        }
        if sub.closed {
            out.close_path();
        }
    }
    out
}

/// A pseudo-random share in 0..1, the same for the same tooth.
fn share(seg: usize, k: usize) -> f64 {
    let mut h = (seg as u64)
        .wrapping_mul(0x9E37_79B9_7F4A_7C15)
        .wrapping_add((k as u64).wrapping_mul(0xC2B2_AE3D_27D4_EB4F));
    h ^= h >> 31;
    h = h.wrapping_mul(0xBF58_476D_1CE4_E5B9);
    h ^= h >> 29;
    (h >> 11) as f64 / (1u64 << 53) as f64
}

/// The unit normal of a segment at `t` (left of its direction).
fn normal(seg: &PathSeg, t: f64) -> Vec2 {
    let e = 1e-4;
    let (t0, t1) = ((t - e).max(0.0), (t + e).min(1.0));
    let d = seg.eval(t1) - seg.eval(t0);
    let len = d.hypot();
    if len < 1e-12 {
        return Vec2::ZERO;
    }
    Vec2::new(-d.y / len, d.x / len)
}

fn zipper(
    path: &BezPath,
    c: Point,
    amplitude: f64,
    frequency: u32,
    random: bool,
    smooth: bool,
    local: bool,
) -> BezPath {
    if frequency == 0 || amplitude <= 0.0 {
        return path.clone();
    }
    let subs = subpaths(path);
    // The farthest node from the centre, for Local.
    let far = subs
        .iter()
        .flat_map(|s| std::iter::once(s.start).chain(s.segs.iter().map(|g| g.end())))
        .map(|p| (p - c).hypot())
        .fold(0.0f64, f64::max)
        .max(1e-9);
    let steps = 2 * frequency as usize;
    let mut out = BezPath::new();
    let mut seg_index = 0usize;
    for sub in subs {
        let mut pts = vec![sub.start];
        for seg in &sub.segs {
            for k in 1..steps {
                let t = k as f64 / steps as f64;
                let p = seg.eval(t);
                let sign = if k % 2 == 1 { 1.0 } else { -1.0 };
                let mut a = amplitude;
                if random {
                    a *= share(seg_index, k);
                }
                if local {
                    a *= (1.0 - (p - c).hypot() / far).clamp(0.0, 1.0);
                }
                pts.push(p + normal(seg, t) * (sign * a));
            }
            pts.push(seg.end());
            seg_index += 1;
        }
        if sub.closed && pts.len() > 1 {
            // The closing point is the start again.
            pts.pop();
        }
        if smooth {
            out.extend(
                crate::geometry::smooth_path(&pts, sub.closed)
                    .elements()
                    .iter()
                    .copied(),
            );
        } else {
            out.extend(
                crate::geometry::polyline_path(&pts, sub.closed)
                    .elements()
                    .iter()
                    .copied(),
            );
        }
    }
    out
}

fn twister(path: &BezPath, c: Point, angle: f64) -> BezPath {
    if angle == 0.0 {
        return path.clone();
    }
    let subs = subpaths(path);
    let b = path.bounding_box();
    // Steps short enough for the turn to bend the outline smoothly.
    let size = b.width().max(b.height()).max(1e-6);
    let step = size / 64.0;
    let mut all: Vec<(Vec<Point>, bool)> = Vec::new();
    for sub in &subs {
        let mut pts = vec![sub.start];
        for seg in &sub.segs {
            let len = seg.arclen(1e-3);
            let n = ((len / step).ceil() as usize).clamp(1, 400);
            for k in 1..=n {
                pts.push(seg.eval(k as f64 / n as f64));
            }
        }
        if sub.closed && pts.len() > 1 {
            pts.pop();
        }
        all.push((pts, sub.closed));
    }
    let far = all
        .iter()
        .flat_map(|(p, _)| p.iter())
        .map(|p| (*p - c).hypot())
        .fold(0.0f64, f64::max)
        .max(1e-9);
    let mut out = BezPath::new();
    for (pts, closed) in all {
        let turned: Vec<Point> = pts
            .iter()
            .map(|p| {
                let v = *p - c;
                let a = angle * v.hypot() / far;
                let (sin, cos) = a.sin_cos();
                c + Vec2::new(v.x * cos - v.y * sin, v.x * sin + v.y * cos)
            })
            .collect();
        out.extend(
            crate::geometry::smooth_path(&turned, closed)
                .elements()
                .iter()
                .copied(),
        );
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geometry::Rect;

    fn square(x: f64, y: f64, s: f64) -> BezPath {
        let mut p = BezPath::new();
        p.move_to((x, y));
        p.line_to((x + s, y));
        p.line_to((x + s, y + s));
        p.line_to((x, y + s));
        p.close_path();
        p
    }

    fn nodes(p: &BezPath) -> Vec<Point> {
        p.elements()
            .iter()
            .filter_map(|e| match e {
                PathEl::MoveTo(p) | PathEl::LineTo(p) => Some(*p),
                PathEl::QuadTo(_, p) | PathEl::CurveTo(_, _, p) => Some(*p),
                PathEl::ClosePath => None,
            })
            .collect()
    }

    fn subpath_count(p: &BezPath) -> usize {
        p.elements()
            .iter()
            .filter(|e| matches!(e, PathEl::MoveTo(_)))
            .count()
    }

    fn closes(p: &BezPath) -> usize {
        p.elements()
            .iter()
            .filter(|e| matches!(e, PathEl::ClosePath))
            .count()
    }

    #[test]
    fn push_and_pull_move_nodes_and_bow_the_sides() {
        let sq = square(-10.0, -10.0, 20.0);
        // Pushed 50 %: the corners go out by half their distance, the
        // sides bow in: the middle of a side comes nearer the centre.
        let pushed = distort(
            &sq,
            Distort::PushPull {
                amount: 50.0,
                center: None,
            },
        );
        let n = nodes(&pushed);
        assert!(n
            .iter()
            .any(|p| (p.x - 15.0).abs() < 1e-9 && (p.y - 15.0).abs() < 1e-9));
        let side_mid = |p: &BezPath| {
            p.segments()
                .map(|s| s.eval(0.5))
                .map(|q| q.to_vec2().hypot())
                .fold(f64::INFINITY, f64::min)
        };
        assert!(side_mid(&pushed) < 10.0, "{}", side_mid(&pushed));
        // Pulled 50 %: corners in, sides bow out past the old sides.
        let pulled = distort(
            &sq,
            Distort::PushPull {
                amount: -50.0,
                center: None,
            },
        );
        assert!(nodes(&pulled)
            .iter()
            .any(|p| (p.x - 5.0).abs() < 1e-9 && (p.y - 5.0).abs() < 1e-9));
        let reach = pulled.bounding_box();
        assert!(reach.width() > 10.0);
        assert_eq!((subpath_count(&pulled), closes(&pulled)), (1, 1));
        // About a centre of its own: a node at the centre stays.
        let off = distort(
            &sq,
            Distort::PushPull {
                amount: 80.0,
                center: Some(Point::new(-10.0, -10.0)),
            },
        );
        assert!(nodes(&off)
            .iter()
            .any(|p| (p.x + 10.0).abs() < 1e-9 && (p.y + 10.0).abs() < 1e-9));
        // An open curve stays open; two squares stay two subpaths.
        let mut open = BezPath::new();
        open.move_to((0.0, 0.0));
        open.line_to((10.0, 0.0));
        open.line_to((10.0, 10.0));
        let o = distort(
            &open,
            Distort::PushPull {
                amount: 30.0,
                center: None,
            },
        );
        assert_eq!((subpath_count(&o), closes(&o)), (1, 0));
        let mut two = square(0.0, 0.0, 10.0);
        two.extend(square(20.0, 0.0, 10.0).elements().iter().copied());
        let t = distort(
            &two,
            Distort::PushPull {
                amount: 30.0,
                center: None,
            },
        );
        assert_eq!((subpath_count(&t), closes(&t)), (2, 2));
    }

    fn zip(random: bool, smooth: bool, local: bool) -> Distort {
        Distort::Zipper {
            amplitude: 2.0,
            frequency: 3,
            center: None,
            random,
            smooth,
            local,
        }
    }

    #[test]
    fn zipper_teeth_on_every_segment() {
        let sq = square(0.0, 0.0, 20.0);
        let out = distort(&sq, zip(false, false, false));
        // 4 sides of 6 steps, one point each, closed.
        assert_eq!(nodes(&out).len(), 24);
        assert_eq!(closes(&out), 1);
        // The corners stay, the teeth reach 2 mm out and in.
        let b = out.bounding_box();
        assert!(
            (b.x0 + 2.0).abs() < 1e-6 && (b.x1 - 22.0).abs() < 1e-6,
            "{b:?}"
        );
        assert!(nodes(&out).contains(&Point::new(20.0, 0.0)));
        // No frequency: unchanged.
        let none = Distort::Zipper {
            amplitude: 2.0,
            frequency: 0,
            center: None,
            random: false,
            smooth: false,
            local: false,
        };
        assert_eq!(distort(&sq, none), sq);
        // Random: smaller teeth, the same each time.
        let a = distort(&sq, zip(true, false, false));
        assert_eq!(a, distort(&sq, zip(true, false, false)));
        let ab = a.bounding_box();
        assert!(ab.width() <= 24.0 + 1e-9 && ab != b);
        // Local: smaller teeth away from the centre (none at the corners).
        let lb = distort(&sq, zip(false, false, true)).bounding_box();
        assert!(lb.width() < b.width());
        // Smooth: curves through the teeth.
        assert!(distort(&sq, zip(false, true, false))
            .elements()
            .iter()
            .any(|e| matches!(e, PathEl::CurveTo(..))));
    }

    #[test]
    fn twister_turns_the_far_points_by_the_angle() {
        let sq = square(-10.0, -10.0, 20.0);
        let t = distort(
            &sq,
            Distort::Twister {
                angle_deg: 90.0,
                center: None,
            },
        );
        // A corner (farthest) turned a quarter: (10, 10) to (-10, 10).
        let n = nodes(&t);
        assert!(
            n.iter()
                .any(|p| (p.x + 10.0).abs() < 1e-6 && (p.y - 10.0).abs() < 1e-6),
            "{n:?}"
        );
        assert_eq!((subpath_count(&t), closes(&t)), (1, 1));
        // The bounds of a quarter turn of a square about its middle stay
        // near the square's.
        let b = t.bounding_box();
        assert!(b.width() < 30.0 && b.width() > 18.0, "{b:?}");
        // An ellipse (no closing element, but closed) stays closed.
        let e = crate::geometry::ellipse_path(Rect::new(0.0, 0.0, 30.0, 20.0));
        let te = distort(
            &e,
            Distort::Twister {
                angle_deg: 60.0,
                center: None,
            },
        );
        assert_eq!((subpath_count(&te), closes(&te)), (1, 1));
        // Zero: unchanged; an odd centre falls back to the middle.
        let z = Distort::Twister {
            angle_deg: 0.0,
            center: None,
        };
        assert_eq!(distort(&sq, z), sq);
        let odd = Distort::Twister {
            angle_deg: 90.0,
            center: Some(Point::new(f64::NAN, 0.0)),
        };
        assert_eq!(distort(&sq, odd), t);
    }

    #[test]
    fn older_files_and_odd_values_read() {
        let d: Distort = serde_json::from_str(r#"{"mode":"twister","angle_deg":140.0}"#)
            .expect("an older twister");
        assert_eq!(
            d,
            Distort::Twister {
                angle_deg: 140.0,
                center: None
            }
        );
        let z: Distort =
            serde_json::from_str(r#"{"mode":"zipper","amplitude":2.0,"frequency":12}"#)
                .expect("an older zipper");
        assert!(matches!(
            z,
            Distort::Zipper {
                random: false,
                smooth: false,
                local: false,
                center: None,
                ..
            }
        ));
        // Written without the defaults.
        let s = serde_json::to_string(&z).expect("json");
        assert!(!s.contains("random") && !s.contains("center"), "{s}");
        // Out of range and not a number: no panic, finite output.
        let sq = square(0.0, 0.0, 10.0);
        for how in [
            Distort::PushPull {
                amount: f64::NAN,
                center: None,
            },
            Distort::PushPull {
                amount: 1e9,
                center: None,
            },
            Distort::Zipper {
                amplitude: f64::INFINITY,
                frequency: u32::MAX,
                center: None,
                random: true,
                smooth: true,
                local: true,
            },
            Distort::Twister {
                angle_deg: 1e12,
                center: Some(Point::new(1e300, 0.0)),
            },
        ] {
            let out = distort(&sq, how);
            let b = out.bounding_box();
            assert!(b.x0.is_finite() && b.y1.is_finite(), "{how:?} {b:?}");
        }
        assert_eq!(
            distort(
                &BezPath::new(),
                Distort::PushPull {
                    amount: 10.0,
                    center: None
                }
            ),
            BezPath::new()
        );
        let _ = Rect::ZERO;
    }
}
