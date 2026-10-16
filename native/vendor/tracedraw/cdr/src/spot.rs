//! Colours from spot colour libraries (colour models 0x01 and 0x0e): the
//! file holds only the colour's number in its library and a tint, not
//! what it looks like. They are read as spot inks of their own; when the
//! file carries a picture of its first page (versions 14 and later keep
//! one in their ZIP container), each ink takes the colour that picture
//! shows where objects painted with it lie uncovered.

use tracedraw_core::document::{Ink, Shape, ShapeKind};
use tracedraw_core::geometry::{Point, Shape as _};
use tracedraw_core::{Color, Document, Fill};

/// Ink numbers of library colours: the library (palette) in the upper
/// bits, the colour's number in the lower 16.
const LIBRARY_INK: u32 = 0x4000_0000;

/// The ink of library `palette`'s colour `number`.
pub(crate) fn ink_id(palette: u16, number: u16) -> u32 {
    LIBRARY_INK | (u32::from(palette & 0x3FFF) << 16) | u32::from(number)
}

/// The colour `number` of library `palette` at `tint` (0 to 100): a spot
/// ink shown as black at the tint until its colour is known.
pub(crate) fn library_color(palette: u16, number: u16, tint: u16) -> Color {
    Color::Spot {
        ink: ink_id(palette, number),
        tint: f32::from(tint.min(100)) / 100.0,
        c: 0.0,
        m: 0.0,
        y: 0.0,
        k: 1.0,
    }
}

/// The ink entry of a library colour, for `Document::inks`.
pub(crate) fn ink_entry(palette: u16, number: u16) -> Ink {
    Ink {
        id: ink_id(palette, number),
        name: format!("Spot {number}"),
        cmyk: [0.0, 0.0, 0.0, 1.0],
    }
}

/// A picture of the first page: width, height and RGBA pixels, top row
/// first.
pub struct Preview {
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
}

impl Preview {
    /// A PNG or `.bmp` picture.
    pub fn decode(bytes: &[u8]) -> Option<Preview> {
        let png = if bytes.starts_with(b"\x89PNG") {
            bytes.to_vec()
        } else {
            crate::ole::decode_bmp_file(bytes)?.2
        };
        let pm = tiny_skia::Pixmap::decode_png(&png).ok()?;
        let mut rgba = Vec::with_capacity(pm.data().len());
        for p in pm.pixels() {
            let c = p.demultiply();
            rgba.extend_from_slice(&[c.red(), c.green(), c.blue(), c.alpha()]);
        }
        Some(Preview {
            width: pm.width(),
            height: pm.height(),
            rgba,
        })
    }

    /// The colour (0 to 1) at page point `p` of a `w` by `h` mm page
    /// (origin bottom left).
    fn at(&self, p: Point, w: f64, h: f64) -> Option<[f64; 3]> {
        if w <= 0.0 || h <= 0.0 {
            return None;
        }
        let x = (p.x / w * self.width as f64).floor();
        let y = ((1.0 - p.y / h) * self.height as f64).floor();
        if x < 0.0 || y < 0.0 || x >= self.width as f64 || y >= self.height as f64 {
            return None;
        }
        let i = (y as usize * self.width as usize + x as usize) * 4;
        let px = self.rgba.get(i..i + 4)?;
        if px[3] < 250 {
            return None;
        }
        Some([px[0], px[1], px[2]].map(|v| v as f64 / 255.0))
    }
}

/// Every top-level and nested shape of the first page, bottom to top,
/// with its page transform folded in.
fn shapes_bottom_up(doc: &Document) -> Vec<Shape> {
    fn walk(s: &Shape, above: tracedraw_core::geometry::Affine, out: &mut Vec<Shape>) {
        let t = above * s.transform;
        match &s.kind {
            ShapeKind::Group { children } => {
                for c in children {
                    walk(c, t, out);
                }
            }
            ShapeKind::ClipFrame { .. } => {
                let mut c = s.clone();
                c.transform = t;
                out.push(c);
            }
            _ => {
                let mut c = s.clone();
                c.transform = t;
                out.push(c);
            }
        }
    }
    let mut out = Vec::new();
    if let Some(page) = doc.pages.first() {
        for l in page.layers.iter().filter(|l| l.visible) {
            for s in &l.shapes {
                walk(s, tracedraw_core::geometry::Affine::IDENTITY, &mut out);
            }
        }
    }
    out
}

/// Whether `s` paints its inside: it has a fill, is not text (its outline
/// here is its whole frame, its letters cover little) and is not an open
/// curve (left unfilled unless the drawing fills them).
fn paints_inside(s: &Shape, fill_open: bool) -> bool {
    !matches!(s.fill, Fill::None)
        && !matches!(s.kind, ShapeKind::Text { .. })
        && (fill_open || !matches!(s.kind, ShapeKind::Path { closed: false, .. }))
}

/// The library ink and tint a fill paints with, when it is one.
fn fill_ink(f: &Fill) -> Option<(u32, f32)> {
    match f {
        Fill::Solid(Color::Spot { ink, tint, .. }) if ink & LIBRARY_INK != 0 => Some((*ink, *tint)),
        _ => None,
    }
}

/// Give each library ink of `doc` the colour `preview` shows where the
/// objects filled with it lie uncovered (the median of up to a few
/// hundred spots, tints taken back to full strength). Returns how many
/// inks got a colour.
pub fn colours_from_preview(doc: &mut Document, preview: &Preview) -> usize {
    let Some(page) = doc.pages.first() else {
        return 0;
    };
    let (w, h) = (page.size.width, page.size.height);
    let fill_open = doc.metadata.fill_open_curves;
    let shapes = shapes_bottom_up(doc);
    let paths: Vec<_> = shapes.iter().map(|s| s.page_path()).collect();
    let mut samples: std::collections::BTreeMap<u32, Vec<[f64; 3]>> = Default::default();
    for (i, s) in shapes.iter().enumerate() {
        let Some((ink, tint)) = fill_ink(&s.fill) else {
            continue;
        };
        let Some(path) = paths.get(i) else {
            continue;
        };
        let b = path.bounding_box();
        if !(b.width() > 0.0 && b.height() > 0.0) {
            continue;
        }
        let n = 24;
        for gy in 0..n {
            for gx in 0..n {
                let p = Point::new(
                    b.x0 + b.width() * (gx as f64 + 0.5) / n as f64,
                    b.y0 + b.height() * (gy as f64 + 0.5) / n as f64,
                );
                if !path.contains(p) {
                    continue;
                }
                // Covered by an object above it: that one shows there.
                let covered = shapes.iter().zip(&paths).skip(i + 1).any(|(o, op)| {
                    paints_inside(o, fill_open) && op.bounding_box().contains(p) && op.contains(p)
                });
                if covered {
                    continue;
                }
                let Some(rgb) = preview.at(p, w, h) else {
                    continue;
                };
                // A tint lightens the ink towards white.
                let t = f64::from(tint).clamp(0.05, 1.0);
                let full = rgb.map(|v| (1.0 - (1.0 - v) / t).clamp(0.0, 1.0));
                samples.entry(ink).or_default().push(full);
            }
        }
    }
    let mut found = std::collections::BTreeMap::new();
    for (ink, mut list) in samples {
        if list.len() < 3 {
            continue;
        }
        let mut median = [0.0; 3];
        for (ch, m) in median.iter_mut().enumerate() {
            list.sort_by(|a, b| a[ch].total_cmp(&b[ch]));
            *m = list[list.len() / 2][ch];
        }
        let rgb = Color::Rgb {
            r: median[0] as f32,
            g: median[1] as f32,
            b: median[2] as f32,
        };
        let cmyk = match rgb.convert_to("CMYK") {
            Color::Cmyk { c, m, y, k } => [c, m, y, k],
            _ => continue,
        };
        found.insert(ink, cmyk);
    }
    if found.is_empty() {
        return 0;
    }
    for ink in doc.inks.iter_mut() {
        if let Some(c) = found.get(&ink.id) {
            ink.cmyk = *c;
        }
    }
    let recolor = |c: &mut Color| {
        if let Color::Spot {
            ink,
            c: cc,
            m,
            y,
            k,
            ..
        } = c
        {
            if let Some([nc, nm, ny, nk]) = found.get(ink) {
                (*cc, *m, *y, *k) = (*nc, *nm, *ny, *nk);
            }
        }
    };
    for page in &mut doc.pages {
        for layer in &mut page.layers {
            for s in &mut layer.shapes {
                recolor_shape(s, &recolor);
            }
        }
    }
    found.len()
}

fn recolor_shape(s: &mut Shape, f: &impl Fn(&mut Color)) {
    match &mut s.fill {
        Fill::Solid(c) => f(c),
        Fill::Fountain(fo) => fo.stops.iter_mut().for_each(|st| f(&mut st.color)),
        _ => {}
    }
    if let Some(st) = &mut s.stroke {
        f(&mut st.color);
    }
    match &mut s.kind {
        ShapeKind::Group { children } => children.iter_mut().for_each(|c| recolor_shape(c, f)),
        ShapeKind::ClipFrame { frame, contents } => {
            recolor_shape(frame, f);
            contents.iter_mut().for_each(|c| recolor_shape(c, f));
        }
        ShapeKind::Text { spans, .. } => {
            for sp in spans.iter_mut() {
                if let Some(Fill::Solid(c)) = sp.fill.as_mut() {
                    f(c);
                }
            }
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tracedraw_core::geometry::Rect;

    #[test]
    fn library_inks_take_the_colour_their_objects_show_in_the_preview() {
        let mut doc = Document::new("t", tracedraw_core::geometry::Size::new(100.0, 100.0));
        let ink = ink_id(0x22, 0x07db);
        doc.inks.push(ink_entry(0x22, 0x07db));
        let mut s = Shape::new(
            doc.ids_mut().shape(),
            ShapeKind::Rect {
                rect: Rect::new(0.0, 0.0, 50.0, 100.0),
                radius: 0.0,
                corners: None,
            },
        );
        s.fill = Fill::Solid(library_color(0x22, 0x07db, 100));
        if let Some(l) = doc.pages[0].layers.first_mut() {
            l.shapes.push(s);
        }
        // A 10 x 10 preview: the left half pale green, the right white.
        let mut rgba = Vec::new();
        for _y in 0..10 {
            for x in 0..10 {
                let c = if x < 5 {
                    [200, 230, 200, 255]
                } else {
                    [255, 255, 255, 255]
                };
                rgba.extend_from_slice(&c);
            }
        }
        let preview = Preview {
            width: 10,
            height: 10,
            rgba,
        };
        assert_eq!(colours_from_preview(&mut doc, &preview), 1);
        let fill = &doc.pages[0].layers[0].shapes[0].fill;
        let Fill::Solid(c) = fill else {
            panic!("fill");
        };
        let [r, g, b] = c.to_rgb8();
        assert!(g > r && g > b && r > 150, "{r} {g} {b}");
        assert_eq!(doc.inks[0].id, ink);
        assert!(doc.inks[0].cmyk[3] < 0.5);
    }

    #[test]
    fn bad_previews_give_nothing() {
        assert!(Preview::decode(b"").is_none());
        assert!(Preview::decode(b"\x89PNG broken").is_none());
        assert!(Preview::decode(b"BM").is_none());
    }
}
