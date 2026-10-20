//! Envelopes: a frame of nodes around an object's bounding box, joined by
//! cubic segments, that the object is mapped into.
//!
//! The nodes run from the bottom-left corner along the bottom side, up the
//! right side, back along the top and down the left side; four of them are
//! the corners. Each segment, from a node to the next, has two handles. How
//! the object fills the frame is the [`Mapping`]; how moving a node
//! reshapes the frame is the editing [`Mode`].

use crate::geometry::{
    BezPath, CubicBez, ParamCurve, ParamCurveNearest, PathEl, Point, Rect, Vec2,
};
use serde::{Deserialize, Serialize};

/// The most nodes a frame takes.
pub const MAX_NODES: usize = 96;

/// How an object is mapped into its envelope.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mapping {
    /// Every point blends the four sides, the nodes of each side spread
    /// evenly along it.
    #[default]
    Putty,
    /// As Putty, with each side measured by its length: the object's
    /// edges spread evenly along the frame however its nodes are spaced.
    Original,
    /// Rows stay level: the object fills the frame's height and each row
    /// spans the frame from its left edge to its right edge.
    Horizontal,
    /// Columns stay upright: the object fills the frame's width and each
    /// column spans the frame from its bottom edge to its top edge.
    Vertical,
}

impl Mapping {
    /// In the order the lists show them.
    pub const ALL: [Mapping; 4] = [
        Mapping::Horizontal,
        Mapping::Original,
        Mapping::Putty,
        Mapping::Vertical,
    ];

    pub fn id(self) -> &'static str {
        match self {
            Mapping::Putty => "putty",
            Mapping::Original => "original",
            Mapping::Horizontal => "horizontal",
            Mapping::Vertical => "vertical",
        }
    }

    /// The mapping named `id`; Putty for anything else.
    pub fn from_id(id: &str) -> Mapping {
        Mapping::ALL
            .into_iter()
            .find(|m| m.id() == id)
            .unwrap_or_default()
    }
}

/// How moving a node reshapes the sides it is on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Mode {
    /// The sides run straight from node to node.
    StraightLine,
    /// Each side is one smooth arc through its nodes.
    SingleArc,
    /// Each segment leaves and reaches its nodes along the side, so a node
    /// moved off the side makes an S on each segment it ends.
    DoubleArc,
    /// The node moves with its handles and nothing else changes; handles
    /// move on their own.
    #[default]
    Unconstrained,
}

impl Mode {
    pub const ALL: [Mode; 4] = [
        Mode::StraightLine,
        Mode::SingleArc,
        Mode::DoubleArc,
        Mode::Unconstrained,
    ];

    pub fn id(self) -> &'static str {
        match self {
            Mode::StraightLine => "straight_line",
            Mode::SingleArc => "single_arc",
            Mode::DoubleArc => "double_arc",
            Mode::Unconstrained => "unconstrained",
        }
    }

    /// The mode named `id`; Unconstrained for anything else.
    pub fn from_id(id: &str) -> Mode {
        Mode::ALL
            .into_iter()
            .find(|m| m.id() == id)
            .unwrap_or_default()
    }
}

/// The eight nodes of a fresh frame around `b`: the corners and the middle
/// of each side, from the bottom-left corner: BL, BM, BR, RM, TR, TM, TL,
/// LM.
pub fn default_nodes(b: Rect) -> Vec<Point> {
    vec![
        Point::new(b.x0, b.y0),
        Point::new(b.center().x, b.y0),
        Point::new(b.x1, b.y0),
        Point::new(b.x1, b.center().y),
        Point::new(b.x1, b.y1),
        Point::new(b.center().x, b.y1),
        Point::new(b.x0, b.y1),
        Point::new(b.x0, b.center().y),
    ]
}

fn finite(p: &Point) -> bool {
    p.x.is_finite() && p.y.is_finite()
}

/// An envelope's frame.
#[derive(Debug, Clone, PartialEq)]
pub struct Frame {
    pub nodes: Vec<Point>,
    /// The handles of the segment from node `i` to node `i + 1` (the last
    /// one back to node 0).
    pub handles: Vec<[Point; 2]>,
    /// The corner nodes: bottom-left, bottom-right, top-right, top-left,
    /// in increasing order.
    pub corners: [usize; 4],
}

impl Frame {
    /// The frame around a box: its corners and the middle of each side,
    /// the sides straight. Mapping through it leaves an object as it is.
    pub fn around(b: Rect) -> Frame {
        let mut f = Frame {
            nodes: default_nodes(b),
            handles: Vec::new(),
            corners: [0, 2, 4, 6],
        };
        f.handles = (0..f.nodes.len()).map(|i| f.straight_handles(i)).collect();
        f
    }

    /// The frame an envelope stores. Without handles (envelopes written
    /// before handles existed) each side is the arc through its nodes;
    /// corners that are not four increasing node numbers fall back to the
    /// nodes a quarter of the way round each. None with fewer than four
    /// nodes, more than [`MAX_NODES`] or a node that is not a number.
    pub fn from_parts(nodes: &[Point], handles: &[[Point; 2]], corners: &[usize]) -> Option<Frame> {
        let n = nodes.len();
        if !(4..=MAX_NODES).contains(&n) || !nodes.iter().all(finite) {
            return None;
        }
        let corners = match corners {
            &[a, b, c, d] if a < b && b < c && c < d && d < n => [a, b, c, d],
            _ => [0, n / 4, n / 2, 3 * n / 4],
        };
        let mut f = Frame {
            nodes: nodes.to_vec(),
            handles: Vec::new(),
            corners,
        };
        if handles.len() == n && handles.iter().flatten().all(finite) {
            f.handles = handles.to_vec();
        } else {
            f.handles = (0..n).map(|i| f.straight_handles(i)).collect();
            for side in 0..4 {
                f.reshape_side(side, Mode::SingleArc);
            }
        }
        Some(f)
    }

    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    /// One pair of handles per node and corners that are nodes, whatever
    /// was put in the fields.
    fn fix(&mut self) {
        let n = self.nodes.len();
        self.handles.truncate(n);
        while self.handles.len() < n {
            let h = self.straight_handles(self.handles.len());
            self.handles.push(h);
        }
        let [a, b, c, d] = self.corners;
        if !(a < b && b < c && c < d && d < n) {
            self.corners = [0, n / 4, n / 2, 3 * n / 4];
        }
    }

    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    fn node(&self, i: usize) -> Point {
        let n = self.nodes.len().max(1);
        self.nodes.get(i % n).copied().unwrap_or_default()
    }

    /// The segment from node `i` to the next.
    pub fn segment(&self, i: usize) -> CubicBez {
        let n = self.nodes.len().max(1);
        let [h1, h2] = self
            .handles
            .get(i % n)
            .copied()
            .unwrap_or_else(|| self.straight_handles(i));
        CubicBez::new(self.node(i), h1, h2, self.node(i + 1))
    }

    /// Handles a third and two thirds of the way along the chord.
    fn straight_handles(&self, i: usize) -> [Point; 2] {
        let (a, b) = (self.node(i), self.node(i + 1));
        [a.lerp(b, 1.0 / 3.0), a.lerp(b, 2.0 / 3.0)]
    }

    pub fn is_corner(&self, i: usize) -> bool {
        self.corners.contains(&i)
    }

    /// The nodes of side `s` (0 bottom, 1 right, 2 top, 3 left) in node
    /// order, both corners included.
    pub fn side_nodes(&self, s: usize) -> Vec<usize> {
        let n = self.nodes.len();
        if n == 0 {
            return Vec::new();
        }
        let from = self.corners[s % 4];
        let to = self.corners[(s + 1) % 4];
        let count = (to + n - from) % n;
        (0..=count).map(|k| (from + k) % n).collect()
    }

    /// The segments of side `s` in node order.
    pub fn side_segments(&self, s: usize) -> Vec<usize> {
        let nodes = self.side_nodes(s);
        nodes[..nodes.len().saturating_sub(1)].to_vec()
    }

    /// The sides node `i` is on: two for a corner, one for any other.
    pub fn sides_of(&self, i: usize) -> Vec<usize> {
        (0..4)
            .filter(|s| self.side_nodes(*s).contains(&i))
            .collect()
    }

    /// Give side `s` the shape `mode` gives it (Unconstrained leaves it).
    pub fn reshape_side(&mut self, s: usize, mode: Mode) {
        self.fix();
        let nodes = self.side_nodes(s);
        if nodes.len() < 2 {
            return;
        }
        let pts: Vec<Point> = nodes.iter().map(|i| self.node(*i)).collect();
        let m = pts.len() - 1;
        let mut set = |j: usize, h: [Point; 2]| {
            if let Some(slot) = nodes.get(j).and_then(|i| self.handles.get_mut(*i)) {
                *slot = h;
            }
        };
        match mode {
            Mode::Unconstrained => {}
            Mode::StraightLine => {
                for j in 0..m {
                    let (a, b) = (pts[j], pts[j + 1]);
                    set(j, [a.lerp(b, 1.0 / 3.0), a.lerp(b, 2.0 / 3.0)]);
                }
            }
            Mode::SingleArc => {
                // Tangents (per segment): Catmull-Rom inside, and at the
                // ends the ones that make three nodes one quadratic arc.
                let mut t = vec![Vec2::ZERO; m + 1];
                for (i, ti) in t.iter_mut().enumerate().take(m).skip(1) {
                    *ti = (pts[i + 1] - pts[i - 1]) * 0.5;
                }
                if m == 1 {
                    t[0] = pts[1] - pts[0];
                    t[1] = t[0];
                } else {
                    t[0] = (pts[1] - pts[0]) * 2.0 - t[1];
                    t[m] = (pts[m] - pts[m - 1]) * 2.0 - t[m - 1];
                }
                for j in 0..m {
                    set(j, [pts[j] + t[j] / 3.0, pts[j + 1] - t[j + 1] / 3.0]);
                }
            }
            Mode::DoubleArc => {
                // Along the side's own direction: across for the bottom and
                // the top, up for the right and the left.
                let axis = if s.is_multiple_of(2) {
                    Vec2::new(1.0, 0.0)
                } else {
                    Vec2::new(0.0, 1.0)
                };
                for j in 0..m {
                    let (a, b) = (pts[j], pts[j + 1]);
                    let l = (b - a).dot(axis) / 3.0;
                    set(j, [a + axis * l, b - axis * l]);
                }
            }
        }
    }

    /// Move node `i` by `d` the way `mode` moves it.
    pub fn move_node(&mut self, i: usize, d: Vec2, mode: Mode) {
        self.fix();
        let n = self.nodes.len();
        if i >= n || !(d.x.is_finite() && d.y.is_finite()) {
            return;
        }
        if let Some(p) = self.nodes.get_mut(i) {
            *p += d;
        }
        if mode == Mode::Unconstrained {
            if let Some(h) = self.handles.get_mut(i) {
                h[0] += d;
            }
            if let Some(h) = self.handles.get_mut((i + n - 1) % n) {
                h[1] += d;
            }
            return;
        }
        for s in self.sides_of(i) {
            self.reshape_side(s, mode);
        }
    }

    /// Move handle `end` (0 at the segment's start, 1 at its end) of
    /// segment `seg` by `d`.
    pub fn move_handle(&mut self, seg: usize, end: usize, d: Vec2) {
        self.fix();
        if let Some(h) = self.handles.get_mut(seg).and_then(|h| h.get_mut(end)) {
            if d.x.is_finite() && d.y.is_finite() {
                *h += d;
            }
        }
    }

    /// Split segment `seg` at `t` (0..1) with a new node where the curve
    /// was, keeping the frame's shape. Returns the new node's number.
    pub fn add_node(&mut self, seg: usize, t: f64) -> Option<usize> {
        self.fix();
        let n = self.nodes.len();
        if seg >= n || n >= MAX_NODES || !t.is_finite() {
            return None;
        }
        let t = t.clamp(0.05, 0.95);
        let c = self.segment(seg);
        let (a, b) = (c.subsegment(0.0..t), c.subsegment(t..1.0));
        let at = seg + 1;
        *self.handles.get_mut(seg)? = [a.p1, a.p2];
        self.nodes.insert(at, b.p0);
        self.handles.insert(at, [b.p1, b.p2]);
        for c in self.corners.iter_mut() {
            if *c >= at {
                *c += 1;
            }
        }
        Some(at)
    }

    /// Remove node `i` (not a corner); the segments either side become one
    /// keeping their outer handles. False when it cannot go.
    pub fn remove_node(&mut self, i: usize) -> bool {
        self.fix();
        let n = self.nodes.len();
        if i >= n || n <= 4 || self.is_corner(i) {
            return false;
        }
        let prev = (i + n - 1) % n;
        let Some(out) = self.handles.get(i).map(|h| h[1]) else {
            return false;
        };
        if let Some(h) = self.handles.get_mut(prev) {
            h[1] = out;
        }
        self.nodes.remove(i);
        self.handles.remove(i);
        for c in self.corners.iter_mut() {
            if *c > i {
                *c -= 1;
            }
        }
        true
    }

    /// The segment nearest `p`: (segment, parameter, distance).
    pub fn nearest_segment(&self, p: Point) -> Option<(usize, f64, f64)> {
        (0..self.nodes.len())
            .map(|i| {
                let near = self.segment(i).nearest(p, 1e-6);
                (i, near.t, near.distance_sq.sqrt())
            })
            .min_by(|a, b| a.2.total_cmp(&b.2))
    }

    /// The frame's outline.
    pub fn to_path(&self) -> BezPath {
        let mut path = BezPath::new();
        if self.nodes.is_empty() {
            return path;
        }
        path.move_to(self.node(0));
        for i in 0..self.nodes.len() {
            let c = self.segment(i);
            path.curve_to(c.p1, c.p2, c.p3);
        }
        path.close_path();
        path
    }

    /// The frame moved through `m` (nodes and handles alike).
    pub fn transformed(&self, m: crate::geometry::Affine) -> Frame {
        Frame {
            nodes: self.nodes.iter().map(|p| m * *p).collect(),
            handles: self.handles.iter().map(|[a, b]| [m * *a, m * *b]).collect(),
            corners: self.corners,
        }
    }

    /// The frame's bounding box (of its outline).
    pub fn bounds(&self) -> Rect {
        use crate::geometry::Shape as _;
        self.to_path().bounding_box()
    }
}

/// One side of the frame as a curve from 0 to 1, evenly by segment or by
/// length.
struct Side {
    segs: Vec<CubicBez>,
    /// (fraction of the length, side parameter), for measuring by length.
    by_length: Option<Vec<(f64, f64)>>,
}

impl Side {
    fn new(frame: &Frame, s: usize, reversed: bool, by_length: bool) -> Side {
        let mut segs: Vec<CubicBez> = frame
            .side_segments(s)
            .into_iter()
            .map(|i| frame.segment(i))
            .collect();
        if reversed {
            segs.reverse();
            for c in segs.iter_mut() {
                *c = CubicBez::new(c.p3, c.p2, c.p1, c.p0);
            }
        }
        let mut side = Side {
            segs,
            by_length: None,
        };
        if by_length && !side.segs.is_empty() {
            const STEPS: usize = 32;
            let total_steps = STEPS * side.segs.len();
            let mut table = Vec::with_capacity(total_steps + 1);
            let mut len = 0.0;
            let mut last = side.at_param(0.0);
            table.push((0.0, 0.0));
            for k in 1..=total_steps {
                let u = k as f64 / total_steps as f64;
                let p = side.at_param(u);
                len += (p - last).hypot();
                last = p;
                table.push((len, u));
            }
            if len > 1e-12 {
                for e in table.iter_mut() {
                    e.0 /= len;
                }
                side.by_length = Some(table);
            }
        }
        side
    }

    /// The point at side parameter `u`, each segment taking an equal part.
    fn at_param(&self, u: f64) -> Point {
        let m = self.segs.len();
        if m == 0 {
            return Point::ZERO;
        }
        let x = u.clamp(0.0, 1.0) * m as f64;
        let j = (x.floor() as usize).min(m - 1);
        self.segs[j].eval(x - j as f64)
    }

    fn at(&self, u: f64) -> Point {
        match &self.by_length {
            Some(table) => {
                let f = u.clamp(0.0, 1.0);
                let i = table.partition_point(|e| e.0 < f);
                let param = match (i.checked_sub(1).and_then(|k| table.get(k)), table.get(i)) {
                    (Some(&(l0, p0)), Some(&(l1, p1))) if l1 - l0 > 1e-12 => {
                        p0 + (p1 - p0) * (f - l0) / (l1 - l0)
                    }
                    (_, Some(&(_, p))) | (Some(&(_, p)), None) => p,
                    (None, None) => f,
                };
                self.at_param(param)
            }
            None => self.at_param(u),
        }
    }
}

/// For each of `ROWS` levels across the frame (rows when `across_y`,
/// columns otherwise), where the frame's outline starts and ends there.
struct Spans {
    lo: f64,
    hi: f64,
    spans: Vec<(f64, f64)>,
}

const ROWS: usize = 257;

impl Spans {
    fn new(frame: &Frame, rows: bool) -> Option<Spans> {
        // The outline as a polygon.
        let mut poly: Vec<Point> = Vec::new();
        for i in 0..frame.len() {
            let c = frame.segment(i);
            for k in 0..24 {
                poly.push(c.eval(k as f64 / 24.0));
            }
        }
        if poly.len() < 3 {
            return None;
        }
        // Rows across y read x; columns across x read y.
        let along = |p: &Point| if rows { p.y } else { p.x };
        let across = |p: &Point| if rows { p.x } else { p.y };
        let lo = poly.iter().map(along).fold(f64::INFINITY, f64::min);
        let hi = poly.iter().map(along).fold(f64::NEG_INFINITY, f64::max);
        if !(lo.is_finite() && hi.is_finite()) || hi - lo <= 1e-9 {
            return None;
        }
        let eps = 1e-4;
        let mut spans: Vec<Option<(f64, f64)>> = (0..ROWS)
            .map(|k| {
                let f = eps + (1.0 - 2.0 * eps) * k as f64 / (ROWS - 1) as f64;
                let level = lo + (hi - lo) * f;
                let mut min = f64::INFINITY;
                let mut max = f64::NEG_INFINITY;
                for (i, a) in poly.iter().enumerate() {
                    let b = &poly[(i + 1) % poly.len()];
                    let (la, lb) = (along(a), along(b));
                    if (la <= level && level < lb) || (lb <= level && level < la) {
                        let t = (level - la) / (lb - la);
                        let x = across(a) + (across(b) - across(a)) * t;
                        min = min.min(x);
                        max = max.max(x);
                    }
                }
                (min <= max).then_some((min, max))
            })
            .collect();
        // Levels the outline missed take their nearest neighbour's span.
        let first = spans.iter().position(Option::is_some)?;
        let mut last = spans[first];
        for s in spans.iter_mut() {
            match s {
                Some(v) => last = Some(*v),
                None => *s = last,
            }
        }
        Some(Spans {
            lo,
            hi,
            spans: spans.into_iter().map(|s| s.unwrap_or_default()).collect(),
        })
    }

    /// (level, start, end) at fraction `f` of the way across.
    fn at(&self, f: f64) -> (f64, f64, f64) {
        let f = f.clamp(0.0, 1.0);
        let r = f * (ROWS - 1) as f64;
        let k = (r.floor() as usize).min(ROWS - 2);
        let w = r - k as f64;
        let (a, b) = (self.spans[k], self.spans[k + 1]);
        (
            self.lo + (self.hi - self.lo) * f,
            a.0 + (b.0 - a.0) * w,
            a.1 + (b.1 - a.1) * w,
        )
    }
}

/// Map `path` through `frame`: the path's bounding box goes onto the
/// frame the way `mapping` says. With `keep_lines` only the nodes and
/// control points move, so straight segments stay straight; otherwise
/// the path is divided finely enough to bend.
pub fn apply(path: &BezPath, frame: &Frame, keep_lines: bool, mapping: Mapping) -> BezPath {
    use crate::geometry::Shape as _;
    let b = path.bounding_box();
    if b.width() < 1e-9 || b.height() < 1e-9 {
        return path.clone();
    }
    let unit = |p: Point| {
        (
            ((p.x - b.x0) / b.width()).clamp(0.0, 1.0),
            ((p.y - b.y0) / b.height()).clamp(0.0, 1.0),
        )
    };
    let map: Box<dyn Fn(Point) -> Point> = match mapping {
        Mapping::Putty | Mapping::Original => {
            let by_length = mapping == Mapping::Original;
            let bottom = Side::new(frame, 0, false, by_length);
            let right = Side::new(frame, 1, false, by_length);
            // The top runs from the top-right corner in node order and the
            // left from the top-left: both turned round.
            let top = Side::new(frame, 2, true, by_length);
            let left = Side::new(frame, 3, true, by_length);
            let [c0, c1, c2, c3] = frame.corners;
            let (p00, p10, p11, p01) = (
                frame.node(c0),
                frame.node(c1),
                frame.node(c2),
                frame.node(c3),
            );
            Box::new(move |p: Point| {
                let (u, v) = unit(p);
                let (bu, tu, lv, rv) = (bottom.at(u), top.at(u), left.at(v), right.at(v));
                // The Coons patch: the sides blended, less the corners
                // counted twice.
                let sides = (1.0 - v) * bu.to_vec2()
                    + v * tu.to_vec2()
                    + (1.0 - u) * lv.to_vec2()
                    + u * rv.to_vec2();
                let corners = (1.0 - u) * (1.0 - v) * p00.to_vec2()
                    + u * (1.0 - v) * p10.to_vec2()
                    + (1.0 - u) * v * p01.to_vec2()
                    + u * v * p11.to_vec2();
                (sides - corners).to_point()
            })
        }
        Mapping::Horizontal => {
            let Some(rows) = Spans::new(frame, true) else {
                return path.clone();
            };
            Box::new(move |p: Point| {
                let (u, v) = unit(p);
                let (y, x0, x1) = rows.at(v);
                Point::new(x0 + (x1 - x0) * u, y)
            })
        }
        Mapping::Vertical => {
            let Some(cols) = Spans::new(frame, false) else {
                return path.clone();
            };
            Box::new(move |p: Point| {
                let (u, v) = unit(p);
                let (x, y0, y1) = cols.at(u);
                Point::new(x, y0 + (y1 - y0) * v)
            })
        }
    };
    if keep_lines {
        let mut out = BezPath::new();
        for el in path.elements() {
            out.push(match *el {
                PathEl::MoveTo(p) => PathEl::MoveTo(map(p)),
                PathEl::LineTo(p) => PathEl::LineTo(map(p)),
                PathEl::QuadTo(a, p) => PathEl::QuadTo(map(a), map(p)),
                PathEl::CurveTo(a, c, p) => PathEl::CurveTo(map(a), map(c), map(p)),
                PathEl::ClosePath => PathEl::ClosePath,
            });
        }
        return out;
    }
    crate::live::warp(path, b, map)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geometry::Shape as _;

    fn square() -> BezPath {
        Rect::new(0.0, 0.0, 40.0, 20.0).to_path(0.01)
    }

    fn close(a: Point, b: Point) -> bool {
        (a - b).hypot() < 1e-6
    }

    /// A fresh frame leaves the object where it is, whatever the mapping.
    #[test]
    fn a_fresh_frame_changes_nothing() {
        let path = square();
        let f = Frame::around(path.bounding_box());
        for mapping in Mapping::ALL {
            for keep in [false, true] {
                let out = apply(&path, &f, keep, mapping);
                let (a, b) = (out.bounding_box(), path.bounding_box());
                for (x, y) in [(a.x0, b.x0), (a.y0, b.y0), (a.x1, b.x1), (a.y1, b.y1)] {
                    assert!((x - y).abs() < 1e-3, "{mapping:?} {keep}: {a:?} != {b:?}");
                }
            }
        }
    }

    /// Envelopes without handles (written before they existed) are single
    /// arcs: the side through its three nodes is the quadratic through them.
    #[test]
    fn older_envelopes_are_single_arcs() {
        let b = Rect::new(0.0, 0.0, 40.0, 20.0);
        let mut nodes = default_nodes(b);
        nodes[5].y += 10.0;
        let f = Frame::from_parts(&nodes, &[], &[]).expect("frame");
        assert_eq!(f.corners, [0, 2, 4, 6]);
        // The top's middle node is on the curve at the middle.
        let top = Side::new(&f, 2, true, false);
        assert!(close(top.at(0.5), Point::new(20.0, 30.0)));
        // A quarter of the way: the quadratic (0,20) (20,40) (40,20) at 1/4.
        let q = |t: f64| {
            let ctrl = Point::new(20.0, 40.0);
            let a = Point::new(0.0, 20.0);
            let c = Point::new(40.0, 20.0);
            let u = 1.0 - t;
            Point::new(
                u * u * a.x + 2.0 * u * t * ctrl.x + t * t * c.x,
                u * u * a.y + 2.0 * u * t * ctrl.y + t * t * c.y,
            )
        };
        assert!(close(top.at(0.25), q(0.25)), "{:?}", top.at(0.25));
        // The object's top centre follows.
        let out = apply(&square(), &f, false, Mapping::Putty);
        assert!((out.bounding_box().y1 - 30.0).abs() < 0.05);
        // Too few nodes or a node that is not a number: no frame.
        assert!(Frame::from_parts(&nodes[..3], &[], &[]).is_none());
        let mut bad = nodes.clone();
        bad[2].x = f64::NAN;
        assert!(Frame::from_parts(&bad, &[], &[]).is_none());
    }

    /// Each mode reshapes the sides a moved node is on.
    #[test]
    fn the_modes_shape_the_sides_a_node_moves() {
        let b = Rect::new(0.0, 0.0, 40.0, 20.0);
        let up = Vec2::new(0.0, 10.0);
        // Straight line: the top runs straight to the raised middle.
        let mut f = Frame::around(b);
        f.move_node(5, up, Mode::StraightLine);
        let quarter = f.segment(4).eval(0.5);
        assert!(close(quarter, Point::new(30.0, 25.0)), "{quarter:?}");
        // Single arc: one smooth arc, level at the raised middle.
        let mut f = Frame::around(b);
        f.move_node(5, up, Mode::SingleArc);
        let (into, out) = (f.handles[4][1], f.handles[5][0]);
        assert!((into.y - 30.0).abs() < 1e-9 && (out.y - 30.0).abs() < 1e-9);
        assert!(f.segment(4).eval(0.5).y > 25.0);
        // Double arc: level at both ends of each segment, an S between.
        let mut f = Frame::around(b);
        f.move_node(5, up, Mode::DoubleArc);
        let seg = f.segment(4);
        assert!((seg.p1.y - 20.0).abs() < 1e-9 && (seg.p2.y - 30.0).abs() < 1e-9);
        assert!(close(seg.eval(0.5), Point::new(30.0, 25.0)));
        assert!(seg.eval(0.25).y < 25.0 - 2.0);
        // Unconstrained: the node takes its handles along, nothing else moves.
        let mut f = Frame::around(b);
        let before = f.clone();
        f.move_node(5, up, Mode::Unconstrained);
        assert_eq!(f.handles[4][1], before.handles[4][1] + up);
        assert_eq!(f.handles[5][0], before.handles[5][0] + up);
        assert_eq!(f.handles[4][0], before.handles[4][0]);
        // A corner moved in a mode reshapes both its sides.
        let mut f = Frame::around(b);
        f.move_node(4, up, Mode::StraightLine);
        assert_eq!(f.sides_of(4), vec![1, 2]);
        assert!(close(f.segment(3).eval(0.5), Point::new(40.0, 20.0)));
        assert!(close(f.segment(4).eval(0.5), Point::new(30.0, 25.0)));
        // A handle moves alone.
        let mut f = Frame::around(b);
        f.move_handle(0, 1, up);
        assert_eq!(f.handles[0][1], Frame::around(b).handles[0][1] + up);
    }

    /// Nodes come and go keeping the frame's shape, corners stay.
    #[test]
    fn nodes_are_added_and_removed() {
        let b = Rect::new(0.0, 0.0, 40.0, 20.0);
        let mut f = Frame::around(b);
        f.move_node(5, Vec2::new(0.0, 10.0), Mode::SingleArc);
        let on_curve = f.segment(4).eval(0.5);
        let at = f.add_node(4, 0.5).expect("added");
        assert_eq!(at, 5);
        assert_eq!(f.len(), 9);
        assert!(close(f.nodes[5], on_curve));
        assert_eq!(f.corners, [0, 2, 4, 7]);
        // The halves are the curve as it was.
        assert!(close(f.segment(4).eval(1.0), on_curve));
        // The last segment, back to the first node, takes a node too.
        let n = f.len();
        assert_eq!(f.add_node(n - 1, 0.5), Some(n));
        assert_eq!(f.corners, [0, 2, 4, 7]);
        // Corners stay; other nodes go down to four.
        assert!(!f.remove_node(4));
        assert!(f.remove_node(5));
        assert_eq!(f.corners, [0, 2, 4, 6]);
        let mut f = Frame::around(b);
        for _ in 0..4 {
            let i = (0..f.len())
                .find(|i| !f.is_corner(*i))
                .expect("a middle node");
            assert!(f.remove_node(i));
        }
        assert_eq!(f.len(), 4);
        assert_eq!(f.corners, [0, 1, 2, 3]);
        assert!(!f.remove_node(1));
        // The nearest segment and its parameter.
        let (seg, t, d) = f.nearest_segment(Point::new(20.0, -1.0)).expect("near");
        assert_eq!(seg, 0);
        assert!((t - 0.5).abs() < 1e-6 && (d - 1.0).abs() < 1e-6);
    }

    /// The mappings: Putty and Original differ once a side's nodes are
    /// uneven; Horizontal keeps rows level; Vertical keeps columns upright.
    #[test]
    fn the_mappings_fill_the_frame_differently() {
        let path = square();
        let b = path.bounding_box();
        // The bottom's middle node slid right, the side still straight.
        let mut f = Frame::around(b);
        f.move_node(1, Vec2::new(10.0, 0.0), Mode::StraightLine);
        let mid = Point::new(20.0, 0.0);
        let mut probe = BezPath::new();
        probe.move_to(Point::new(0.0, 0.0));
        probe.line_to(mid);
        probe.line_to(Point::new(40.0, 20.0));
        let putty = apply(&probe, &f, true, Mapping::Putty);
        let original = apply(&probe, &f, true, Mapping::Original);
        let second = |p: &BezPath| match p.elements().get(1) {
            Some(PathEl::LineTo(q)) => *q,
            _ => Point::ZERO,
        };
        // Putty takes the bottom's middle to the moved node, Original to
        // the middle of the bottom's length.
        assert!(
            close(second(&putty), Point::new(30.0, 0.0)),
            "{:?}",
            second(&putty)
        );
        assert!(close(second(&original), mid), "{:?}", second(&original));
        // A frame whose right side bulges: Horizontal stretches the rows,
        // Vertical leaves every column's x where the box puts it.
        let mut f = Frame::around(b);
        f.move_node(3, Vec2::new(10.0, 0.0), Mode::SingleArc);
        let row = apply(&probe, &f, true, Mapping::Horizontal);
        let p = second(&row);
        assert!((p.y - 0.0).abs() < 1e-6);
        let col = apply(&probe, &f, true, Mapping::Vertical);
        let q = second(&col);
        assert!(q.x > 20.0 && q.x < 30.0, "{q:?}");
        // Mid-height on the right edge goes out to the bulge with Horizontal.
        let mut edge = BezPath::new();
        edge.move_to(Point::new(0.0, 0.0));
        edge.line_to(Point::new(40.0, 10.0));
        edge.line_to(Point::new(40.0, 20.0));
        let out = apply(&edge, &f, true, Mapping::Horizontal);
        match out.elements().get(1) {
            Some(PathEl::LineTo(q)) => assert!((q.x - 50.0).abs() < 0.2, "{q:?}"),
            other => panic!("{other:?}"),
        }
        assert_eq!(Mapping::from_id("vertical"), Mapping::Vertical);
        assert_eq!(Mapping::from_id("no.such.key"), Mapping::Putty);
        assert_eq!(Mode::from_id("double_arc"), Mode::DoubleArc);
        assert_eq!(Mode::from_id("no.such.key"), Mode::Unconstrained);
    }
}
