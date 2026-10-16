//! Painterly brushes: an outline drawn as many small marks (dabs, grains,
//! bristles, hairs, ribbons) along its path instead of a solid line. The
//! marks come from a fixed pseudo-random sequence (the brush's seed), so
//! a brushstroke looks the same every time it is drawn or exported, and
//! the path stays an ordinary curve that can be reshaped.

use crate::geometry::{BezPath, PathEl, Point};
use serde::{Deserialize, Serialize};

/// The brushes, in the order and groups the brush picker lists them.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PainterlyBrush {
    Chalk,
    Charcoal,
    Pastel,
    InkWash,
    Watercolor,
    DryBrush,
    Bristle,
    Fur,
    Spray,
    Speckle,
    Cloud,
    Ribbon,
}

impl PainterlyBrush {
    pub const ALL: [PainterlyBrush; 12] = [
        PainterlyBrush::Chalk,
        PainterlyBrush::Charcoal,
        PainterlyBrush::Pastel,
        PainterlyBrush::InkWash,
        PainterlyBrush::Watercolor,
        PainterlyBrush::DryBrush,
        PainterlyBrush::Bristle,
        PainterlyBrush::Fur,
        PainterlyBrush::Spray,
        PainterlyBrush::Speckle,
        PainterlyBrush::Cloud,
        PainterlyBrush::Ribbon,
    ];

    /// Stable name (settings, i18n key suffix).
    pub fn id(self) -> &'static str {
        match self {
            PainterlyBrush::Chalk => "chalk",
            PainterlyBrush::Charcoal => "charcoal",
            PainterlyBrush::Pastel => "pastel",
            PainterlyBrush::InkWash => "ink_wash",
            PainterlyBrush::Watercolor => "watercolor",
            PainterlyBrush::DryBrush => "dry_brush",
            PainterlyBrush::Bristle => "bristle",
            PainterlyBrush::Fur => "fur",
            PainterlyBrush::Spray => "spray",
            PainterlyBrush::Speckle => "speckle",
            PainterlyBrush::Cloud => "cloud",
            PainterlyBrush::Ribbon => "ribbon",
        }
    }

    /// The picker's group: 0 dry media, 1 wet media, 2 textures.
    pub fn group(self) -> usize {
        match self {
            PainterlyBrush::Chalk | PainterlyBrush::Charcoal | PainterlyBrush::Pastel => 0,
            PainterlyBrush::InkWash | PainterlyBrush::Watercolor | PainterlyBrush::DryBrush => 1,
            _ => 2,
        }
    }
}

/// A painterly outline: the brush, the transparency of its marks
/// (0 to 1) and the seed of its pseudo-random sequence. The brush size
/// is the outline width.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Painterly {
    pub brush: PainterlyBrush,
    #[serde(default)]
    pub transparency: f64,
    #[serde(default)]
    pub seed: u32,
}

impl Painterly {
    pub fn new(brush: PainterlyBrush) -> Self {
        Painterly {
            brush,
            transparency: 0.2,
            seed: 1,
        }
    }
}

/// One mark: a closed area and how opaque it is (0 to 1).
#[derive(Debug, Clone)]
pub struct Mark {
    pub path: BezPath,
    pub alpha: f64,
}

/// Small, fast, deterministic random numbers (xorshift).
struct Rng(u64);

impl Rng {
    fn new(seed: u32, salt: u64) -> Self {
        let mut s =
            (seed as u64) ^ salt.wrapping_mul(0x9E37_79B9_7F4A_7C15) ^ 0x2545_F491_4F6C_DD1D;
        if s == 0 {
            s = 0x1234_5678;
        }
        let mut r = Rng(s);
        for _ in 0..4 {
            r.next();
        }
        r
    }
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
    /// 0 to 1.
    fn unit(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
    /// -1 to 1.
    fn signed(&mut self) -> f64 {
        self.unit() * 2.0 - 1.0
    }
}

/// A point along the path with its direction (unit) and place (0 to 1).
#[derive(Clone, Copy)]
struct Sample {
    p: Point,
    d: [f64; 2],
    t: f64,
}

/// Points every `step` mm along the path (all subpaths), at most `max`.
fn samples(path: &BezPath, step: f64, max: usize) -> Vec<Sample> {
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
    if cur.len() >= 2 {
        lines.push(cur);
    }
    let total: f64 = lines
        .iter()
        .map(|l| l.windows(2).map(|w| (w[1] - w[0]).hypot()).sum::<f64>())
        .sum();
    if total <= 1e-9 {
        return Vec::new();
    }
    let step = step.max(total / max.max(1) as f64).max(0.01);
    let mut out = Vec::new();
    let mut run = 0.0;
    let mut next = 0.0;
    for line in &lines {
        for w in line.windows(2) {
            let (a, b) = (w[0], w[1]);
            let len = (b - a).hypot();
            if len <= 1e-12 {
                continue;
            }
            let d = [(b.x - a.x) / len, (b.y - a.y) / len];
            while next <= run + len {
                let u = (next - run) / len;
                out.push(Sample {
                    p: a + (b - a) * u,
                    d,
                    t: next / total,
                });
                next += step;
            }
            run += len;
        }
    }
    out
}

fn polygon(pts: &[Point]) -> BezPath {
    let mut path = BezPath::new();
    for (i, p) in pts.iter().enumerate() {
        if i == 0 {
            path.move_to(*p);
        } else {
            path.line_to(*p);
        }
    }
    path.close_path();
    path
}

/// A rough round blob: `n` points at radius `r` with `rough` noise.
fn blob(c: Point, rx: f64, ry: f64, angle: f64, n: usize, rough: f64, rng: &mut Rng) -> BezPath {
    let (ca, sa) = (angle.cos(), angle.sin());
    let pts: Vec<Point> = (0..n)
        .map(|i| {
            let a = i as f64 / n as f64 * std::f64::consts::TAU;
            let k = 1.0 + rough * rng.signed();
            let (x, y) = (a.cos() * rx * k, a.sin() * ry * k);
            Point::new(c.x + x * ca - y * sa, c.y + x * sa + y * ca)
        })
        .collect();
    polygon(&pts)
}

/// A thin band along a polyline, `w` wide.
fn band(points: &[Point], w: f64) -> Option<BezPath> {
    if points.len() < 2 {
        return None;
    }
    let mut left = Vec::with_capacity(points.len());
    let mut right = Vec::with_capacity(points.len());
    for (i, p) in points.iter().enumerate() {
        let a = points.get(i.saturating_sub(1)).copied().unwrap_or(*p);
        let b = points.get(i + 1).copied().unwrap_or(*p);
        let d = b - a;
        let len = d.hypot().max(1e-9);
        let n = [-d.y / len * w / 2.0, d.x / len * w / 2.0];
        left.push(Point::new(p.x + n[0], p.y + n[1]));
        right.push(Point::new(p.x - n[0], p.y - n[1]));
    }
    right.reverse();
    left.extend(right);
    Some(polygon(&left))
}

/// Thinner towards both ends of the stroke.
fn taper(t: f64, ends: f64) -> f64 {
    let e = ends.max(1e-6);
    (t / e).min((1.0 - t) / e).clamp(0.15, 1.0).sqrt()
}

/// The marks of a painterly outline of `size` mm along `path` (page
/// space), at most about 40000 of them.
pub fn marks(path: &BezPath, size: f64, brush: &Painterly) -> Vec<Mark> {
    let size = size.max(0.05);
    let opacity = (1.0 - brush.transparency).clamp(0.0, 1.0);
    let mut rng = Rng::new(brush.seed, brush.brush as u64 + 1);
    let mut out: Vec<Mark> = Vec::new();
    let mut push = |path: BezPath, alpha: f64| {
        if alpha > 0.003 {
            out.push(Mark {
                path,
                alpha: alpha.clamp(0.0, 1.0),
            });
        }
    };
    let at = |s: &Sample, along: f64, across: f64| {
        let n = [-s.d[1], s.d[0]];
        Point::new(
            s.p.x + s.d[0] * along + n[0] * across,
            s.p.y + s.d[1] * along + n[1] * across,
        )
    };
    match brush.brush {
        PainterlyBrush::Chalk | PainterlyBrush::Charcoal | PainterlyBrush::Pastel => {
            // Grains scattered over the stroke's width: chalk coarse and
            // light, charcoal dense and dark, pastel soft and wide.
            let (grains, grain, alpha, spread) = match brush.brush {
                PainterlyBrush::Chalk => (26, 0.08, 0.75, 0.48),
                PainterlyBrush::Charcoal => (40, 0.055, 0.9, 0.45),
                _ => (16, 0.15, 0.45, 0.55),
            };
            for s in samples(path, size * 0.25, 2500) {
                let w = size * taper(s.t, 0.06);
                for _ in 0..grains {
                    let across = rng.signed() * w * spread;
                    let along = rng.signed() * size * 0.2;
                    let r = size * grain * (0.5 + rng.unit());
                    let c = at(&s, along, across);
                    let angle = rng.unit() * std::f64::consts::PI;
                    push(
                        blob(c, r, r * 0.7, angle, 5, 0.35, &mut rng),
                        alpha * opacity * (0.5 + 0.5 * rng.unit()),
                    );
                }
            }
        }
        PainterlyBrush::InkWash | PainterlyBrush::Watercolor => {
            // Soft overlapping dabs; watercolour wider, paler, with a
            // darker rim.
            let water = brush.brush == PainterlyBrush::Watercolor;
            for s in samples(path, size * if water { 0.3 } else { 0.18 }, 4000) {
                let w = size * taper(s.t, if water { 0.04 } else { 0.12 });
                let r = w / 2.0 * (0.85 + 0.3 * rng.unit());
                let c = at(&s, 0.0, rng.signed() * w * if water { 0.12 } else { 0.04 });
                let angle = s.d[1].atan2(s.d[0]);
                let alpha = if water { 0.12 } else { 0.22 };
                push(
                    blob(
                        c,
                        r * 1.1,
                        r,
                        angle,
                        14,
                        if water { 0.18 } else { 0.06 },
                        &mut rng,
                    ),
                    alpha * opacity,
                );
                if water && rng.unit() < 0.35 {
                    push(
                        blob(c, r * 0.45, r * 0.4, angle, 10, 0.25, &mut rng),
                        0.08 * opacity,
                    );
                }
            }
        }
        PainterlyBrush::DryBrush | PainterlyBrush::Bristle => {
            // Parallel bristle streaks; the dry brush leaves gaps.
            let dry = brush.brush == PainterlyBrush::DryBrush;
            let n = if dry { 9 } else { 14 };
            let pts = samples(path, (size * 0.12).max(0.05), 6000);
            let offsets: Vec<f64> = (0..n)
                .map(|i| (i as f64 + 0.5) / n as f64 - 0.5 + rng.signed() * 0.02)
                .collect();
            for (k, off) in offsets.iter().enumerate() {
                let mut run: Vec<Point> = Vec::new();
                let alpha = (if dry { 0.7 } else { 0.45 }) * (0.6 + 0.4 * rng.unit());
                let thick = size / n as f64 * if dry { 0.8 } else { 1.1 };
                let mut gap_left = 0usize;
                for (i, s) in pts.iter().enumerate() {
                    let w = size * taper(s.t, 0.08);
                    let wobble = ((i as f64 * 0.21) + k as f64).sin() * size * 0.02;
                    let skip = dry && {
                        if gap_left > 0 {
                            gap_left -= 1;
                            true
                        } else if rng.unit() < 0.04 + 0.5 * (off.abs() * 2.0).powi(3) * 0.1 {
                            gap_left = (rng.unit() * 8.0) as usize;
                            true
                        } else {
                            false
                        }
                    };
                    if skip {
                        if let Some(b) = band(&run, thick) {
                            push(b, alpha * opacity);
                        }
                        run.clear();
                        continue;
                    }
                    run.push(at(s, 0.0, off * w + wobble));
                }
                if let Some(b) = band(&run, thick) {
                    push(b, alpha * opacity);
                }
            }
        }
        PainterlyBrush::Fur => {
            // Short hairs leaning across the stroke.
            for s in samples(path, size * 0.08, 6000) {
                let w = size * taper(s.t, 0.1);
                for _ in 0..2 {
                    let base = at(&s, 0.0, rng.signed() * w * 0.3);
                    let lean = rng.signed() * 0.9;
                    let len = w * (0.4 + 0.5 * rng.unit());
                    let n = [-s.d[1], s.d[0]];
                    let side = if rng.unit() < 0.5 { 1.0 } else { -1.0 };
                    let dir = [
                        n[0] * side * (1.0 - lean.abs()) + s.d[0] * lean,
                        n[1] * side * (1.0 - lean.abs()) + s.d[1] * lean,
                    ];
                    let tip = Point::new(base.x + dir[0] * len, base.y + dir[1] * len);
                    let mid = Point::new(
                        (base.x + tip.x) / 2.0 + s.d[0] * len * 0.15,
                        (base.y + tip.y) / 2.0 + s.d[1] * len * 0.15,
                    );
                    if let Some(b) = band(&[base, mid, tip], size * 0.03) {
                        push(b, 0.6 * opacity);
                    }
                }
            }
        }
        PainterlyBrush::Spray | PainterlyBrush::Speckle => {
            // Dots sprayed around the path: fine and dense, or coarse
            // flecks thrown wider.
            let speckle = brush.brush == PainterlyBrush::Speckle;
            for s in samples(path, size * 0.3, 3000) {
                let w = size * taper(s.t, 0.05);
                let count = if speckle { 6 } else { 45 };
                for _ in 0..count {
                    // More dots near the middle.
                    let across = rng.signed() * rng.unit() * w * if speckle { 1.4 } else { 0.8 };
                    let along = rng.signed() * size * 0.3;
                    let r = size
                        * if speckle {
                            0.05 + 0.08 * rng.unit()
                        } else {
                            0.015 + 0.025 * rng.unit()
                        };
                    let c = at(&s, along, across);
                    push(
                        blob(c, r, r, 0.0, 6, 0.2, &mut rng),
                        (0.5 + 0.5 * rng.unit()) * opacity,
                    );
                }
            }
        }
        PainterlyBrush::Cloud => {
            // Large soft puffs.
            for s in samples(path, size * 0.4, 1500) {
                let w = size * taper(s.t, 0.1);
                for _ in 0..2 {
                    let c = at(&s, rng.signed() * size * 0.3, rng.signed() * w * 0.35);
                    let r = w * (0.25 + 0.3 * rng.unit());
                    for (k, a) in [(1.0, 0.05), (0.7, 0.06), (0.4, 0.08)] {
                        push(
                            blob(c, r * k, r * k * 0.85, rng.unit() * 3.0, 16, 0.2, &mut rng),
                            a * opacity,
                        );
                    }
                }
            }
        }
        PainterlyBrush::Ribbon => {
            // Silky threads weaving across the stroke.
            let pts = samples(path, (size * 0.1).max(0.05), 6000);
            let threads = 9;
            for k in 0..threads {
                let phase = rng.unit() * std::f64::consts::TAU;
                let freq = 2.0 + rng.unit() * 3.0;
                let amp = 0.25 + 0.25 * rng.unit();
                let line: Vec<Point> = pts
                    .iter()
                    .map(|s| {
                        let w = size * taper(s.t, 0.12);
                        let off = (s.t * freq * std::f64::consts::TAU + phase).sin() * amp
                            + (k as f64 / threads as f64 - 0.5) * 0.4;
                        at(s, 0.0, off * w)
                    })
                    .collect();
                if let Some(b) = band(&line, size * 0.03) {
                    push(b, 0.35 * opacity);
                }
            }
        }
    }
    out
}

/// The marks merged into one path per opacity step (`levels` steps), for
/// exports that write one filled path per opacity: (opacity, path).
pub fn marks_by_opacity(
    path: &BezPath,
    size: f64,
    brush: &Painterly,
    levels: usize,
) -> Vec<(f64, BezPath)> {
    let levels = levels.max(1);
    let mut groups: Vec<BezPath> = vec![BezPath::new(); levels];
    for m in marks(path, size, brush) {
        let k = ((m.alpha * levels as f64).round() as usize).clamp(1, levels) - 1;
        if let Some(g) = groups.get_mut(k) {
            g.extend(m.path.elements().iter().copied());
        }
    }
    groups
        .into_iter()
        .enumerate()
        .filter(|(_, p)| !p.elements().is_empty())
        .map(|(k, p)| ((k + 1) as f64 / levels as f64, p))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geometry::Shape as _;

    fn line() -> BezPath {
        let mut p = BezPath::new();
        p.move_to(Point::new(0.0, 0.0));
        p.line_to(Point::new(100.0, 0.0));
        p
    }

    #[test]
    fn every_brush_leaves_marks_along_the_stroke_and_within_reach() {
        for b in PainterlyBrush::ALL {
            let m = marks(&line(), 6.0, &Painterly::new(b));
            assert!(m.len() > 5, "{b:?}: {}", m.len());
            let mut bounds: Option<crate::Rect> = None;
            for mark in &m {
                assert!(mark.alpha > 0.0 && mark.alpha <= 1.0, "{b:?}");
                let r = mark.path.bounding_box();
                bounds = Some(bounds.map(|x| x.union(r)).unwrap_or(r));
            }
            let r = bounds.unwrap_or_default();
            // Along the whole line, not far across it.
            assert!(r.x0 < 8.0 && r.x1 > 92.0, "{b:?} {r:?}");
            assert!(r.y0 > -10.0 && r.y1 < 10.0, "{b:?} {r:?}");
        }
    }

    #[test]
    fn the_same_seed_gives_the_same_strokes_and_transparency_fades_them() {
        let p = Painterly::new(PainterlyBrush::Chalk);
        let a = marks(&line(), 5.0, &p);
        let b = marks(&line(), 5.0, &p);
        assert_eq!(a.len(), b.len());
        assert_eq!(a[10].path.bounding_box(), b[10].path.bounding_box());
        let other = marks(&line(), 5.0, &Painterly { seed: 7, ..p });
        assert_ne!(a[10].path.bounding_box(), other[10].path.bounding_box());
        let clear = marks(
            &line(),
            5.0,
            &Painterly {
                transparency: 0.9,
                ..p
            },
        );
        let sum = |m: &[Mark]| m.iter().map(|m| m.alpha).sum::<f64>();
        assert!(sum(&clear) < sum(&a) * 0.2);
        // A path with no length has no marks.
        let mut dot = BezPath::new();
        dot.move_to(Point::new(1.0, 1.0));
        assert!(marks(&dot, 5.0, &p).is_empty());
    }
}
