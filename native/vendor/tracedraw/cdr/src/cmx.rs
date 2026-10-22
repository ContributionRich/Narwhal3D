//! CMX exchange files: a RIFF form `CMX1`.
//!
//! The `cont` chunk describes the file (operating system, byte order,
//! coordinate size, version, unit), tables hold what the drawing refers to
//! by 1-based index (`rclr` colours, `rpen` pens, `rdot` dash patterns,
//! `rott` line types, `rotl` outlines), and each `page` chunk holds one
//! page as a stream of instructions. An instruction is a u16 size (its
//! whole length), an i16 code and fields: a u8 tag, a u16 length that
//! counts the 3 header bytes, the data, and 0xff after the last field.
//! Embedded images sit between instructions, after an instruction that
//! jumps over them to an absolute file offset.
//!
//! Only files with 32-bit coordinates (version 2, as current programs
//! write them) are read. Coordinates are in the unit `cont` gives (0.0001
//! mm in every file seen), with the origin at the page's centre and y up.
//! `docs/cmx-format.md` lists every layout fact with its status.

use crate::parse::{build_path, color_from_model, encode_png, ParseReport};
use crate::{Error, Result};
use std::collections::BTreeSet;
use tracedraw_core::{
    document::{Layer, Page, ParagraphStyle, Shape, ShapeKind, TextAlign, TextSpan},
    geometry::{Affine, BezPath, Point, Rect, Size},
    style::{Arrowhead, Fountain, FountainKind, Stop},
    Color, Document, Fill, LineCap, LineJoin, Stroke,
};

/// The unit of every file seen: 1e-7 m.
const DEFAULT_UNIT_MM: f64 = 0.0001;

const BEGIN_PAGE: i32 = 9;
const END_PAGE: i32 = 10;
const BEGIN_LAYER: i32 = 11;
const END_LAYER: i32 = 12;
const BEGIN_GROUP: i32 = 13;
const END_GROUP: i32 = 14;
const BEGIN_TEXT: i32 = 20;
const END_TEXT: i32 = 21;
const GLYPH_RUN: i32 = 65;
const TEXT_MATRIX: i32 = 72;
const CHAR_STYLE: i32 = 85;
const ELLIPSE: i32 = 66;
const POLY_CURVE: i32 = 67;
const RECTANGLE: i32 = 68;
const DRAW_IMAGE: i32 = 69;
const ADD_CLIP: i32 = 88;
const REMOVE_CLIP: i32 = 89;
const JUMP: i32 = 111;
const COLOR_CONTEXT: i32 = 127;

/// Whether `bytes` is a CMX file (RIFF form `CMX1`).
pub fn is_cmx(bytes: &[u8]) -> bool {
    bytes.get(0..4) == Some(b"RIFF".as_slice()) && bytes.get(8..12) == Some(b"CMX1".as_slice())
}

/// Read a CMX file into a document. The report lists what was skipped.
pub fn open_bytes(bytes: &[u8], title: &str) -> Result<(Document, ParseReport)> {
    if !is_cmx(bytes) {
        if bytes.get(0..4) == Some(b"RIFX".as_slice())
            && bytes.get(8..12) == Some(b"CMX1".as_slice())
        {
            return Err(Error::UnsupportedContainer(
                "CMX files in big-endian byte order are not read yet".into(),
            ));
        }
        return Err(Error::NotCmx);
    }
    let chunks = top_chunks(bytes);
    let cont = chunks
        .iter()
        .find(|c| c.tag == *b"cont")
        .map(|c| c.data)
        .ok_or(Error::NotCmx)?;
    // Coordinate size as an ASCII digit at byte 52: "4" (32-bit) or "2".
    match cont.get(52) {
        Some(b'4') => {}
        Some(b'2') => {
            return Err(Error::UnsupportedContainer(
                "CMX files with 16-bit coordinates are not read yet".into(),
            ))
        }
        _ => return Err(Error::NotCmx),
    }
    let unit_mm = Cur::at(cont, 64)
        .f64()
        .map(|m| m * 1000.0)
        .filter(|u| u.is_finite() && *u > 1e-9 && *u < 1e3)
        .unwrap_or(DEFAULT_UNIT_MM);

    let mut doc = Document::new(title, Size::new(210.0, 297.0));
    doc.pages.clear();
    let mut ctx = Ctx {
        unit_mm,
        colors: Vec::new(),
        pens: Vec::new(),
        line_types: Vec::new(),
        dashes: Vec::new(),
        outlines: Vec::new(),
        arrow_pairs: Vec::new(),
        arrowheads: Vec::new(),
        fonts: Vec::new(),
        report: ParseReport::default(),
        noted: BTreeSet::new(),
    };
    for c in &chunks {
        match &c.tag {
            b"rclr" => ctx.colors = entries(c.data).map(|f| entry_color(&f)).collect(),
            b"rpen" => ctx.pens = entries(c.data).map(|f| pen(&f, unit_mm)).collect(),
            b"rott" => ctx.line_types = entries(c.data).map(|f| line_type(&f)).collect(),
            b"rdot" => ctx.dashes = entries(c.data).map(|f| dashes(&f)).collect(),
            b"rotl" => ctx.outlines = entries(c.data).map(|f| outline(&f)).collect(),
            b"rota" => ctx.arrow_pairs = arrow_pairs(c.data),
            b"rfnt" => ctx.fonts = arrowheads(c.data).map(|f| font_name(&f)).collect(),
            b"rarw" => {
                ctx.arrowheads = arrowheads(c.data)
                    .enumerate()
                    .map(|(i, f)| arrowhead(&f, i + 1))
                    .collect()
            }
            _ => {}
        }
    }
    for c in chunks.iter().filter(|c| c.tag == *b"page") {
        ctx.read_page(c, &mut doc);
    }
    if doc.pages.is_empty() {
        ctx.report.warn("no pages found");
        let page_id = doc.ids_mut().page();
        let layer_id = doc.ids_mut().layer();
        doc.pages.push(Page {
            id: page_id,
            name: "Page 1".into(),
            size: Size::new(210.0, 297.0),
            layers: vec![Layer::new(layer_id, "Layer 1")],
            guides: Vec::new(),
            background: None,
        });
    }
    ctx.report.pages = doc.pages.len();
    ctx.report.layers = doc.pages.iter().map(|p| p.layers.len()).sum();
    ctx.report.shapes = doc
        .pages
        .iter()
        .flat_map(|p| &p.layers)
        .map(|l| l.shapes.len())
        .sum();
    Ok((doc, ctx.report))
}

/// A chunk of the RIFF form: its tag, its data and where the data starts
/// in the file.
struct ChunkRef<'a> {
    tag: [u8; 4],
    data: &'a [u8],
    start: usize,
}

/// The chunks of the form in file order, those of `LIST` chunks in place.
fn top_chunks(bytes: &[u8]) -> Vec<ChunkRef<'_>> {
    let mut out = Vec::new();
    let end = Cur::at(bytes, 4)
        .u32()
        .and_then(|n| (n as usize).checked_add(8))
        .map_or(bytes.len(), |e| e.min(bytes.len()));
    walk_chunks(bytes, 12, end, 0, &mut out);
    out
}

fn walk_chunks<'a>(
    b: &'a [u8],
    mut pos: usize,
    end: usize,
    depth: u32,
    out: &mut Vec<ChunkRef<'a>>,
) {
    while pos.saturating_add(8) <= end {
        let Some(tag) = b
            .get(pos..pos + 4)
            .and_then(|t| <[u8; 4]>::try_from(t).ok())
        else {
            break;
        };
        let Some(size) = Cur::at(b, pos + 4).u32().map(|s| s as usize) else {
            break;
        };
        let start = pos + 8;
        let stop = start.saturating_add(size).min(end);
        if &tag == b"LIST" {
            if depth < 8 {
                walk_chunks(b, start + 4, stop, depth + 1, out);
            }
        } else if let Some(data) = b.get(start..stop) {
            out.push(ChunkRef { tag, data, start });
        }
        pos = start.saturating_add(size).saturating_add(size & 1);
    }
}

/// Little-endian reading over a slice; `None` past its end.
struct Cur<'a> {
    b: &'a [u8],
    pos: usize,
}

impl<'a> Cur<'a> {
    fn at(b: &'a [u8], pos: usize) -> Self {
        Cur { b, pos }
    }
    fn take(&mut self, n: usize) -> Option<&'a [u8]> {
        let s = self.b.get(self.pos..self.pos.checked_add(n)?)?;
        self.pos += n;
        Some(s)
    }
    fn arr<const N: usize>(&mut self) -> Option<[u8; N]> {
        self.take(N)?.try_into().ok()
    }
    fn u8(&mut self) -> Option<u8> {
        self.arr::<1>().map(|[v]| v)
    }
    fn u16(&mut self) -> Option<u16> {
        self.arr().map(u16::from_le_bytes)
    }
    fn i16(&mut self) -> Option<i16> {
        self.arr().map(i16::from_le_bytes)
    }
    fn u32(&mut self) -> Option<u32> {
        self.arr().map(u32::from_le_bytes)
    }
    fn i32(&mut self) -> Option<i32> {
        self.arr().map(i32::from_le_bytes)
    }
    fn f64(&mut self) -> Option<f64> {
        self.arr().map(f64::from_le_bytes)
    }
}

/// The fields of a field list starting at `pos`, up to the 0xff that ends
/// it, and the position after that end.
fn fields(b: &[u8], mut pos: usize) -> (Vec<(u8, &[u8])>, usize) {
    let mut out = Vec::new();
    while let Some(&tag) = b.get(pos) {
        if tag == 0xff {
            return (out, pos + 1);
        }
        let Some(len) = Cur::at(b, pos + 1).u16().map(usize::from) else {
            break;
        };
        let Some(data) = (len >= 3).then(|| b.get(pos + 3..pos + len)).flatten() else {
            break;
        };
        out.push((tag, data));
        pos += len;
    }
    (out, b.len())
}

fn field<'a>(fields: &[(u8, &'a [u8])], tag: u8) -> Option<&'a [u8]> {
    fields.iter().find(|(t, _)| *t == tag).map(|(_, d)| *d)
}

/// The entries of a table: a u16 count, then that many field lists.
fn entries(d: &[u8]) -> impl Iterator<Item = Vec<(u8, &[u8])>> {
    let count = Cur::at(d, 0).u16().unwrap_or(0) as usize;
    let mut pos = 2;
    (0..count).map_while(move |_| {
        if pos >= d.len() {
            return None;
        }
        let (f, next) = fields(d, pos);
        pos = next;
        Some(f)
    })
}

/// A colour entry: field 10 holds the colour as `.cdr` files keep it (a
/// u32 length, then parts of a u8 id, a u32 length and data; part 1 is
/// model u16, palette u16, 4 unknown bytes and 4 value bytes); fields 1
/// (model, palette) and 2 (values) hold the older form.
fn entry_color(f: &[(u8, &[u8])]) -> Option<Color> {
    if let Some(c) = field(f, 10).and_then(record_color) {
        return Some(c);
    }
    let model = field(f, 1)?.first().copied()?;
    let v = field(f, 2)?;
    let byte = |i: usize| v.get(i).copied();
    match model {
        // CMYK in percent and in 0..255.
        2 => Some(Color::cmyk_pct(
            byte(0)? as f32,
            byte(1)? as f32,
            byte(2)? as f32,
            byte(3)? as f32,
        )),
        3 => Some(Color::cmyk8(byte(0)?, byte(1)?, byte(2)?, byte(3)?)),
        // Red, green, blue.
        5 => Some(Color::rgb8(byte(0)?, byte(1)?, byte(2)?)),
        _ => None,
    }
}

fn record_color(d: &[u8]) -> Option<Color> {
    let mut c = Cur::at(d, 0);
    let len = c.u32()? as usize;
    let end = len.saturating_add(4).min(d.len());
    while c.pos.saturating_add(5) <= end {
        let id = c.u8()?;
        let n = c.u32()? as usize;
        let part = c.take(n)?;
        if id == 1 && n >= 12 {
            let mut p = Cur::at(part, 0);
            let model = p.u16()?;
            p.pos = 8;
            return color_from_model(model, p.arr()?);
        }
    }
    None
}

/// A pen: width (u32), aspect in percent (u16), nib angle (i32), then a
/// matrix.
#[derive(Clone, Copy)]
struct Pen {
    width: f64,
    aspect: f64,
    angle: f64,
}

fn pen(f: &[(u8, &[u8])], unit_mm: f64) -> Option<Pen> {
    let mut c = Cur::at(field(f, 1)?, 0);
    let width = c.u32()? as f64 * unit_mm;
    let aspect = c.u16().map_or(100.0, f64::from);
    let angle = c.i32().map_or(0.0, |a| a as f64 / 1_000_000.0);
    Some(Pen {
        width,
        aspect,
        angle,
    })
}

/// A line type: flags (1 no outline, 2 solid, 4 dashed, 8 behind the
/// fill, 16 scaled with the object), then the caps (low 4 bits) and the
/// join (high 4 bits).
fn line_type(f: &[(u8, &[u8])]) -> Option<(u8, u8)> {
    let d = field(f, 1)?;
    Some((d.first().copied()?, d.get(1).copied().unwrap_or(0)))
}

/// A dash pattern: a u16 count, then the lengths in pen widths.
fn dashes(f: &[(u8, &[u8])]) -> Vec<f64> {
    let Some(d) = field(f, 1) else {
        return Vec::new();
    };
    let mut c = Cur::at(d, 0);
    let n = c.u16().unwrap_or(0) as usize;
    (0..n.min(64))
        .map_while(|_| c.u16())
        .map(f64::from)
        .collect()
}

/// An outline: line type, screen, colour, arrowheads, pen and dash pattern.
fn outline(f: &[(u8, &[u8])]) -> Option<[u16; 6]> {
    let mut c = Cur::at(field(f, 1)?, 0);
    let mut o = [0u16; 6];
    for v in &mut o {
        *v = c.u16()?;
    }
    Some(o)
}

/// Arrowhead pairs (`rota`): a u16 count, then the start and end
/// arrowheads (u16 each, 1-based in `rarw`, 0 for none).
fn arrow_pairs(d: &[u8]) -> Vec<(u16, u16)> {
    let mut c = Cur::at(d, 0);
    let n = usize::from(c.u16().unwrap_or(0));
    (0..n).map_while(|_| Some((c.u16()?, c.u16()?))).collect()
}

/// The arrowheads of `rarw`: field lists one after the other, without a
/// count.
fn arrowheads(d: &[u8]) -> impl Iterator<Item = Vec<(u8, &[u8])>> {
    let mut pos = 0;
    std::iter::from_fn(move || {
        if pos >= d.len() || d.get(pos) == Some(&0xff) {
            return None;
        }
        let (f, next) = fields(d, pos);
        if f.is_empty() || next <= pos {
            return None;
        }
        pos = next;
        Some(f)
    })
    .take(10_000)
}

/// An arrowhead: field 1 holds a u16 count, the points (i32 x, y), one
/// node byte each and a u32 (how far the line is pulled back, not used).
/// The line ends at the origin and comes in from -x; the head keeps the
/// placement and size the file gives it (see [`HEAD_UNITS_PER_WIDTH`]),
/// parts of it may lie ahead of the end point. Closed subpaths are filled,
/// open ones are drawn as lines one outline width wide.
fn arrowhead(f: &[(u8, &[u8])], number: usize) -> Arrowhead {
    let Some(d) = field(f, 1) else {
        return Arrowhead::None;
    };
    let mut c = Cur::at(d, 0);
    let n = usize::from(c.u16().unwrap_or(0));
    if n == 0 || n.saturating_mul(9) > d.len().saturating_sub(2) {
        return Arrowhead::None;
    }
    let mut pts = Vec::with_capacity(n);
    for _ in 0..n {
        match (c.i32(), c.i32()) {
            (Some(x), Some(y)) => pts.push(Point::new(f64::from(x), f64::from(y))),
            _ => return Arrowhead::None,
        }
    }
    let Some(types) = c.take(n) else {
        return Arrowhead::None;
    };
    let (path, _) = build_path(&pts, types);
    let shape = stroke_open_subpaths(&path, HEAD_UNITS_PER_WIDTH);
    Arrowhead::from_placed_path(&shape, format!("Arrowhead {number}"), HEAD_UNITS_PER_WIDTH)
}

/// A head used at the start of a line. The file draws it as the mirror
/// image of the end head across the line's normal: the side above the line
/// stays above. Our heads are turned round to face backwards at the start,
/// which also swaps the sides, so the outline is flipped across the line
/// first.
fn start_head(head: Arrowhead) -> Arrowhead {
    match head {
        Arrowhead::Custom { path, name } => Arrowhead::Custom {
            path: Affine::scale_non_uniform(1.0, -1.0) * path,
            name,
        },
        other => other,
    }
}

/// Arrowhead units (`rarw`) per outline width: 127 000 units (half an inch
/// of 1/254 000) are one outline width. Measured on a public sample file
/// against the preview picture it carries (see `docs/cmx-format.md`).
const HEAD_UNITS_PER_WIDTH: f64 = 127_000.0;

/// `path` with every open subpath replaced by the area a round pen of
/// `width` sweeps along it; closed subpaths are kept, turned to one
/// orientation.
fn stroke_open_subpaths(path: &BezPath, width: f64) -> BezPath {
    use tracedraw_core::geometry::PathEl;
    let mut out = BezPath::new();
    let mut sub: Vec<PathEl> = Vec::new();
    let flush = |sub: &mut Vec<PathEl>, closed: bool, out: &mut BezPath| {
        if sub.is_empty() {
            return;
        }
        let part = BezPath::from_vec(std::mem::take(sub));
        if closed {
            // Every closed subpath is filled on its own (a smaller one
            // inside a larger one is not a hole): give them all the
            // orientation of the swept bands so they add up.
            use tracedraw_core::geometry::Shape as _;
            let part = if part.area() < 0.0 {
                part.reverse_subpaths()
            } else {
                part
            };
            out.extend(part.elements().iter().copied());
            if !matches!(out.elements().last(), Some(PathEl::ClosePath)) {
                out.close_path();
            }
        } else {
            let mut line = Vec::new();
            tracedraw_core::geometry::flatten(&part, width / 20.0, &mut |el| match el {
                PathEl::MoveTo(p) | PathEl::LineTo(p) => line.push(p),
                _ => {}
            });
            // A lone move draws nothing.
            if line.len() < 2 {
                return;
            }
            let band = tracedraw_core::shaping::stroke_band(&line, width, false);
            out.extend(band.elements().iter().copied());
        }
    };
    // Where the current subpath started: drawing on after a close starts
    // from there.
    let mut start = Point::ZERO;
    for el in path.elements() {
        match el {
            PathEl::MoveTo(p) => {
                flush(&mut sub, false, &mut out);
                start = *p;
                sub.push(*el);
            }
            PathEl::ClosePath => {
                if !sub.is_empty() {
                    sub.push(*el);
                }
                flush(&mut sub, true, &mut out);
            }
            _ => {
                if sub.is_empty() {
                    sub.push(PathEl::MoveTo(start));
                }
                sub.push(*el);
            }
        }
    }
    flush(&mut sub, false, &mut out);
    out
}

/// An image placement waiting for its pixels: the extent in local
/// millimetres and the transform to the page.
struct Placement {
    rect: Rect,
    transform: Affine,
    index: u16,
}

struct Ctx {
    unit_mm: f64,
    colors: Vec<Option<Color>>,
    pens: Vec<Option<Pen>>,
    line_types: Vec<Option<(u8, u8)>>,
    dashes: Vec<Vec<f64>>,
    outlines: Vec<Option<[u16; 6]>>,
    arrow_pairs: Vec<(u16, u16)>,
    arrowheads: Vec<Arrowhead>,
    /// Font names of `rfnt`, 1-based.
    fonts: Vec<Option<String>>,
    report: ParseReport,
    /// Notes already reported once.
    noted: BTreeSet<String>,
}

/// What a page builds: its layers, and the open layer with its groups.
struct PageBuild {
    size: Size,
    layers: Vec<Layer>,
    /// The open layer's name and its shapes, then one list per open group.
    open: Option<(String, Vec<Vec<Shape>>)>,
    clip_depth: usize,
    pending_image: Option<Placement>,
    images: Vec<Option<(u32, u32, Vec<u8>)>>,
    text: TextState,
}

/// What the text instructions set before a glyph run draws.
#[derive(Default)]
struct TextState {
    /// Text objects begun and not yet drawn by a glyph run.
    undrawn: usize,
    /// The baseline's start and direction on the page.
    matrix: Option<Affine>,
    font: u16,
    size_mm: f64,
    fill: Option<Fill>,
    stroke: Option<Stroke>,
}

impl Ctx {
    fn note(&mut self, msg: String) {
        if self.noted.insert(msg.clone()) {
            self.report.warn(msg);
        }
    }

    fn color(&self, index: u16) -> Option<Color> {
        self.colors
            .get(usize::from(index).checked_sub(1)?)
            .copied()
            .flatten()
    }

    /// One `page` chunk: its instructions in order.
    fn read_page(&mut self, chunk: &ChunkRef<'_>, doc: &mut Document) {
        let d = chunk.data;
        let mut page = PageBuild {
            size: Size::new(210.0, 297.0),
            layers: Vec::new(),
            open: None,
            clip_depth: 0,
            pending_image: None,
            images: Vec::new(),
            text: TextState::default(),
        };
        let mut pos = 0usize;
        let mut steps = 0usize;
        while pos.saturating_add(4) <= d.len() && steps < 5_000_000 {
            steps += 1;
            let mut c = Cur::at(d, pos);
            let (Some(size), Some(code)) = (c.u16(), c.i16()) else {
                break;
            };
            // A negative code marks an instruction whose size is a u32
            // after the code (assumed: no file seen has one).
            let (code, size, head) = if code < 0 {
                let Some(long) = c.u32() else { break };
                (-i32::from(code), long as usize, 8)
            } else {
                (i32::from(code), usize::from(size), 4)
            };
            if size < head {
                self.report.warn(format!(
                    "instruction of {size} bytes at {pos}; rest of the page skipped"
                ));
                break;
            }
            let end = pos.saturating_add(size).min(d.len());
            let body = d.get(pos + head..end).unwrap_or(&[]);
            if code == JUMP {
                // The instruction's field 1 is the file offset to go on
                // from; what it jumps over is embedded data.
                let target = fields(body, 0)
                    .0
                    .first()
                    .and_then(|(_, v)| Cur::at(v, 0).u32())
                    .and_then(|t| (t as usize).checked_sub(chunk.start));
                if let Some(t) = target.filter(|t| *t >= end && *t <= d.len()) {
                    if let Some(embedded) = d.get(end..t) {
                        self.read_embedded(embedded, &mut page, doc);
                    }
                    pos = t;
                    continue;
                }
                pos = end;
                continue;
            }
            self.instruction(code, body, &mut page, doc);
            pos = end;
        }
        if page.text.undrawn > 0 {
            self.report.skipped_objects += page.text.undrawn;
            self.note("text without drawn characters is not read".into());
        }
        // Close what the page left open.
        self.end_layer(&mut page, doc);
        if page.layers.is_empty() {
            page.layers
                .push(Layer::new(doc.ids_mut().layer(), "Layer 1"));
        }
        let page_id = doc.ids_mut().page();
        let name = format!("Page {}", doc.pages.len() + 1);
        doc.pages.push(Page {
            id: page_id,
            name,
            size: page.size,
            layers: page.layers,
            guides: Vec::new(),
            background: None,
        });
    }

    fn instruction(&mut self, code: i32, body: &[u8], page: &mut PageBuild, doc: &mut Document) {
        match code {
            BEGIN_PAGE => {
                // Field 4: the page's width and height.
                let f = fields(body, 0).0;
                if let Some(v) = field(&f, 4) {
                    let mut c = Cur::at(v, 0);
                    if let (Some(w), Some(h)) = (c.u32(), c.u32()) {
                        let size = Size::new(w as f64 * self.unit_mm, h as f64 * self.unit_mm);
                        if size.width > 0.1
                            && size.height > 0.1
                            && size.width < 100_000.0
                            && size.height < 100_000.0
                        {
                            page.size = size;
                        }
                    }
                }
            }
            END_PAGE | COLOR_CONTEXT => {}
            BEGIN_LAYER => {
                self.end_layer(page, doc);
                let f = fields(body, 0).0;
                let name =
                    layer_name(&f).unwrap_or_else(|| format!("Layer {}", page.layers.len() + 1));
                page.open = Some((name, vec![Vec::new()]));
            }
            END_LAYER => self.end_layer(page, doc),
            BEGIN_GROUP => open_lists(page).push(Vec::new()),
            END_GROUP => {
                let lists = open_lists(page);
                if lists.len() > 1 {
                    let children = lists.pop().unwrap_or_default();
                    if let Some(group) = group_of(children, doc.ids_mut().shape()) {
                        push_shape(page, group);
                    }
                }
            }
            POLY_CURVE | RECTANGLE => {
                if let Some(shape) = self.shape(code, body, page.size, doc) {
                    if page.clip_depth > 0 {
                        self.note("clipping regions are not applied".into());
                    }
                    push_shape(page, shape);
                } else {
                    self.report.skipped_objects += 1;
                }
            }
            DRAW_IMAGE => match self.placement(body, page.size) {
                Some(p) => {
                    // An image drawn again is already decoded.
                    let decoded = usize::from(p.index)
                        .checked_sub(1)
                        .and_then(|i| page.images.get(i).cloned().flatten());
                    match decoded {
                        Some(img) => {
                            let shape = image_shape(p, img, doc);
                            push_shape(page, shape);
                        }
                        None => page.pending_image = Some(p),
                    }
                }
                None => {
                    self.report.skipped_objects += 1;
                    self.note("an image placement could not be read".into());
                }
            },
            ADD_CLIP => page.clip_depth += 1,
            REMOVE_CLIP => page.clip_depth = page.clip_depth.saturating_sub(1),
            ELLIPSE => {
                self.report.skipped_objects += 1;
                self.note("ellipse instructions are not read yet".into());
            }
            BEGIN_TEXT => page.text.undrawn += 1,
            TEXT_MATRIX => {
                // Field 2: a matrix (u16 type; 2 is six doubles) placing the
                // baseline's start.
                let f = fields(body, 0).0;
                page.text.matrix = field(&f, 2).and_then(|m| self.matrix(m, page.size));
            }
            CHAR_STYLE => {
                // Field 1: rendering attributes; field 2: font (1-based in
                // `rfnt`) and size.
                let f = fields(body, 0).0;
                if let Some(a) = field(&f, 1) {
                    let (fill, stroke) = self.attributes(a);
                    page.text.fill = Some(fill);
                    page.text.stroke = stroke;
                }
                if let Some(v) = field(&f, 2) {
                    let mut c = Cur::at(v, 0);
                    if let (Some(font), Some(size)) = (c.u16(), c.u32()) {
                        page.text.font = font;
                        page.text.size_mm = f64::from(size) * self.unit_mm;
                    }
                }
            }
            GLYPH_RUN => {
                let lines = glyph_run(body, self.unit_mm);
                if lines.is_empty() {
                    return;
                }
                let Some(matrix) = page.text.matrix else {
                    self.note("text without a placement is not read".into());
                    return;
                };
                let size_pt = page.text.size_mm / 25.4 * 72.0;
                if !(size_pt > 0.01 && size_pt < 100_000.0) {
                    self.note("text without a size is not read".into());
                    return;
                }
                let family = usize::from(page.text.font)
                    .checked_sub(1)
                    .and_then(|i| self.fonts.get(i).cloned().flatten())
                    .unwrap_or_else(|| "Arial".into());
                page.text.undrawn = page.text.undrawn.saturating_sub(1);
                for (at, text) in lines {
                    let mut shape = Shape::new(
                        doc.ids_mut().shape(),
                        ShapeKind::Text {
                            spans: vec![TextSpan::new(&text, &family, size_pt)],
                            origin: Point::ZERO,
                            frame: None,
                            align: TextAlign::Left,
                            para: ParagraphStyle::default(),
                            on_path: None,
                        },
                    );
                    shape.transform = matrix * Affine::translate(at.to_vec2());
                    shape.fill = page.text.fill.clone().unwrap_or(Fill::Solid(Color::BLACK));
                    shape.stroke = page.text.stroke.clone();
                    push_shape(page, shape);
                }
            }
            END_TEXT | 70 | 71 | 73 | 98..=102 => {}
            other => self.note(format!("instruction {other} not read")),
        }
    }

    /// Close the open layer, and the groups it left open.
    fn end_layer(&mut self, page: &mut PageBuild, doc: &mut Document) {
        let Some((name, mut lists)) = page.open.take() else {
            return;
        };
        while lists.len() > 1 {
            let children = lists.pop().unwrap_or_default();
            if let Some(group) = group_of(children, doc.ids_mut().shape()) {
                if let Some(top) = lists.last_mut() {
                    top.push(group);
                }
            }
        }
        let mut layer = Layer::new(doc.ids_mut().layer(), name);
        layer.shapes = lists.pop().unwrap_or_default();
        page.layers.push(layer);
    }

    /// A curve or a rectangle: field 1 holds its rendering attributes,
    /// field 2 its geometry.
    fn shape(&mut self, code: i32, body: &[u8], page: Size, doc: &mut Document) -> Option<Shape> {
        let f = fields(body, 0).0;
        let (fill, stroke) = field(&f, 1).map_or((Fill::None, None), |a| self.attributes(a));
        let geometry = field(&f, 2)?;
        let u = self.unit_mm;
        let centre = Affine::translate((page.width / 2.0, page.height / 2.0));
        let mut c = Cur::at(geometry, 0);
        let (kind, transform) = if code == POLY_CURVE {
            // A u16 count, the points (i32 x, y), one node byte each.
            let n = usize::from(c.u16()?);
            if n == 0 || n.saturating_mul(9) > c.b.len().saturating_sub(c.pos) {
                return None;
            }
            let mut pts = Vec::with_capacity(n);
            for _ in 0..n {
                pts.push(Point::new(c.i32()? as f64 * u, c.i32()? as f64 * u));
            }
            let types = c.take(n)?;
            let (path, closed) = build_path(&pts, types);
            (ShapeKind::Path { path, closed }, centre)
        } else {
            // Centre x and y, width, height, corner radius, angle.
            let cx = c.i32()? as f64 * u;
            let cy = c.i32()? as f64 * u;
            let w = (c.i32()? as f64 * u).abs();
            let h = (c.i32()? as f64 * u).abs();
            let radius = (c.i32().unwrap_or(0) as f64 * u).abs().min(w.min(h) / 2.0);
            let angle = c.i32().unwrap_or(0) as f64 / 1_000_000.0;
            if w <= 0.0 || h <= 0.0 {
                return None;
            }
            (
                ShapeKind::Rect {
                    rect: Rect::new(-w / 2.0, -h / 2.0, w / 2.0, h / 2.0),
                    radius,
                    corners: None,
                },
                centre * Affine::translate((cx, cy)) * Affine::rotate(angle.to_radians()),
            )
        };
        let mut shape = Shape::new(doc.ids_mut().shape(), kind);
        shape.transform = transform;
        shape.fill = fill;
        shape.stroke = stroke;
        Some(shape)
    }

    /// Rendering attributes: a mask (1 fill, 2 outline, 4 lens, 8 canvas,
    /// 16 container), then one field list for each part the mask names.
    fn attributes(&mut self, a: &[u8]) -> (Fill, Option<Stroke>) {
        let Some(&mask) = a.first() else {
            return (Fill::None, None);
        };
        let mut pos = 1;
        let mut fill = Fill::None;
        let mut stroke = None;
        if mask & 1 != 0 {
            let (f, next) = fields(a, pos);
            pos = next;
            if let Some(v) = field(&f, 1) {
                fill = self.fill(v);
            }
        }
        if mask & 2 != 0 {
            let (f, _) = fields(a, pos);
            if let Some(i) = field(&f, 1).and_then(|v| Cur::at(v, 0).u16()) {
                stroke = self.stroke(i);
            }
        }
        if mask & 4 != 0 {
            self.note("lenses are not read yet".into());
        }
        (fill, stroke)
    }

    /// A fill: its type (u16), then a field list. Type 1, uniform: field 1
    /// is the colour and the screen. Type 2, fountain: field 1 the
    /// settings, field 2 the colours.
    fn fill(&mut self, v: &[u8]) -> Fill {
        let ty = Cur::at(v, 0).u16().unwrap_or(0);
        let f = fields(v, 2).0;
        match ty {
            0 => Fill::None,
            1 => field(&f, 1)
                .and_then(|d| Cur::at(d, 0).u16())
                .and_then(|i| self.color(i))
                .map_or(Fill::None, Fill::Solid),
            2 => self.fountain(&f).unwrap_or_else(|| {
                self.note("a fountain fill could not be read; no fill".into());
                Fill::None
            }),
            other => {
                self.note(format!("fill type {other} not read; no fill"));
                Fill::None
            }
        }
    }

    /// Fountain settings: type (0 linear, 1 radial, 2 conical, 3 square),
    /// screen, edge pad (percent), angle (i32), centre x and y offsets
    /// (percent), steps, blend mode, rate method and value. Colours: a
    /// u16 count, then colour index and position (percent) pairs.
    fn fountain(&mut self, f: &[(u8, &[u8])]) -> Option<Fill> {
        let mut c = Cur::at(field(f, 1)?, 0);
        let kind = match c.u16()? {
            1 => FountainKind::Radial,
            2 => FountainKind::Conical,
            3 => FountainKind::Square,
            _ => FountainKind::Linear,
        };
        let _screen = c.u16()?;
        let pad = c.u16()?;
        let angle = c.i32()? as f64 / 1_000_000.0;
        let x = c.i32()?;
        let y = c.i32()?;
        let mut s = Cur::at(field(f, 2)?, 0);
        let n = usize::from(s.u16()?);
        let mut stops = Vec::new();
        for _ in 0..n.min(256) {
            let (Some(i), Some(p)) = (s.u16(), s.u16()) else {
                break;
            };
            stops.push(Stop {
                pos: (f64::from(p) / 100.0).clamp(0.0, 1.0),
                color: self.color(i).unwrap_or(Color::BLACK),
            });
        }
        stops.sort_by(|a, b| a.pos.total_cmp(&b.pos));
        let only = stops.first().copied()?;
        if stops.len() == 1 {
            stops = vec![Stop { pos: 0.0, ..only }, Stop { pos: 1.0, ..only }];
        }
        Some(Fill::Fountain(Fountain {
            kind,
            stops,
            angle: if angle.is_finite() { angle } else { 0.0 },
            offset: Point::new(
                (f64::from(x) / 100.0).clamp(-1.0, 1.0),
                (f64::from(y) / 100.0).clamp(-1.0, 1.0),
            ),
            edge_pad: (f64::from(pad) / 100.0).clamp(0.0, 0.49),
            ..Default::default()
        }))
    }

    /// The outline of `rotl` entry `index`.
    fn stroke(&mut self, index: u16) -> Option<Stroke> {
        let [line, _screen, color, arrows, pen, dash] = self
            .outlines
            .get(usize::from(index).checked_sub(1)?)
            .copied()
            .flatten()?;
        let (flags, caps_join) = usize::from(line)
            .checked_sub(1)
            .and_then(|i| self.line_types.get(i).copied().flatten())
            .unwrap_or((2, 0));
        if flags & 1 != 0 {
            return None;
        }
        let pen = usize::from(pen)
            .checked_sub(1)
            .and_then(|i| self.pens.get(i).copied().flatten());
        let width = pen.map_or(Stroke::HAIRLINE, |p| p.width);
        let width = if width > 0.0 && width.is_finite() {
            width.min(1000.0)
        } else {
            Stroke::HAIRLINE
        };
        let dash = if flags & 4 != 0 {
            usize::from(dash)
                .checked_sub(1)
                .and_then(|i| self.dashes.get(i).cloned())
                .unwrap_or_default()
        } else {
            Vec::new()
        };
        let (start, end) = usize::from(arrows)
            .checked_sub(1)
            .and_then(|i| self.arrow_pairs.get(i).copied())
            .unwrap_or((0, 0));
        let head = |i: u16| {
            usize::from(i)
                .checked_sub(1)
                .and_then(|i| self.arrowheads.get(i).cloned())
                .unwrap_or_default()
        };
        Some(Stroke {
            color: self.color(color).unwrap_or(Color::BLACK),
            start_arrow: start_head(head(start)),
            end_arrow: head(end),
            width,
            cap: match caps_join & 0x0f {
                1 => LineCap::Round,
                2 => LineCap::Square,
                _ => LineCap::Butt,
            },
            join: match caps_join >> 4 {
                1 => LineJoin::Round,
                2 => LineJoin::Bevel,
                _ => LineJoin::Miter,
            },
            dash,
            scale_with_object: flags & 16 != 0,
            behind_fill: flags & 8 != 0,
            stretch: pen.map_or(1.0, |p| (p.aspect / 100.0).clamp(0.01, 1.0)),
            nib_angle: pen.map_or(0.0, |p| if p.angle.is_finite() { p.angle } else { 0.0 }),
            fill: None,
            widths: Vec::new(),
            painterly: None,
        })
    }

    /// An image placement, field 2: the image's extent and its crop (left,
    /// top, right, bottom as i32 each), a matrix (u16 type; type 2 is
    /// followed by six doubles a, b, c, d, e, f with x' = a x + c y + e),
    /// then a u16 and the image's number among the page's images (u16).
    /// A matrix: u16 type, then for type 2 six doubles a, b, c, d, e, f
    /// (x' = a x + c y + e); other types read as no change. The result
    /// maps to the page.
    fn matrix(&self, d: &[u8], page: Size) -> Option<Affine> {
        let mut c = Cur::at(d, 0);
        let u = self.unit_mm;
        let m = match c.u16()? {
            2 => {
                let mut m = [0.0; 6];
                for v in &mut m {
                    *v = c.f64()?;
                }
                if m.iter().any(|v| !v.is_finite()) {
                    return None;
                }
                Affine::new([m[0], m[1], m[2], m[3], m[4] * u, m[5] * u])
            }
            _ => Affine::IDENTITY,
        };
        Some(Affine::translate((page.width / 2.0, page.height / 2.0)) * m)
    }

    fn placement(&mut self, body: &[u8], page: Size) -> Option<Placement> {
        let f = fields(body, 0).0;
        let mut c = Cur::at(field(&f, 2)?, 0);
        let (l, t, r, b) = (c.i32()?, c.i32()?, c.i32()?, c.i32()?);
        c.pos += 16;
        let u = self.unit_mm;
        let matrix = match c.u16()? {
            2 => {
                let mut m = [0.0; 6];
                for v in &mut m {
                    *v = c.f64()?;
                }
                if m.iter().any(|v| !v.is_finite()) {
                    return None;
                }
                Affine::new([m[0], m[1], m[2], m[3], m[4] * u, m[5] * u])
            }
            _ => Affine::IDENTITY,
        };
        let _ = c.u16();
        let index = c.u16().unwrap_or(0);
        let rect = Rect::new(
            f64::from(l) * u,
            f64::from(b) * u,
            f64::from(r) * u,
            f64::from(t) * u,
        )
        .abs();
        if rect.width() <= 0.0 || rect.height() <= 0.0 {
            return None;
        }
        Some(Placement {
            rect,
            transform: Affine::translate((page.width / 2.0, page.height / 2.0)) * matrix,
            index,
        })
    }

    /// Data a jump passes over: `LIST imag` chunks holding an `info` and a
    /// `data` chunk; the image goes to the placement waiting for it.
    fn read_embedded(&mut self, d: &[u8], page: &mut PageBuild, doc: &mut Document) {
        let mut found = Vec::new();
        walk_chunks(d, 0, d.len(), 0, &mut found);
        for c in found.iter().filter(|c| c.tag == *b"data") {
            let image = decode_image(c.data);
            page.images.push(image.clone().ok());
            // Images that fill shapes (patterns) have no placement.
            if let Some(p) = page.pending_image.take() {
                match image {
                    Ok(img) => {
                        let shape = image_shape(p, img, doc);
                        push_shape(page, shape);
                    }
                    Err(note) => {
                        self.report.skipped_objects += 1;
                        self.note(note);
                    }
                }
            }
        }
    }
}

/// An image's `data` chunk: a u8 kind (2: the block follows), a u32
/// length, then a block "RI", its length, 4 bytes, the header length
/// (u32), the model, 1, width, height, planes, bits per pixel and row
/// length (u32 each), and after the header the rows, bottom row first,
/// in blue, green, red order for 24 bits. A note says why an image is
/// left out.
fn decode_image(d: &[u8]) -> std::result::Result<(u32, u32, Vec<u8>), String> {
    let unread = || "an image whose pixels could not be read was left out".to_string();
    if d.first() != Some(&2) {
        return Err("an image stored in an unknown way was left out".into());
    }
    let ri = d.get(5..).unwrap_or(&[]);
    if ri.get(0..2) != Some(b"RI".as_slice()) {
        return Err(unread());
    }
    let mut c = Cur::at(ri, 10);
    let mut next = || c.u32().ok_or_else(unread);
    let header = next()? as usize;
    let _model = next()?;
    let _one = next()?;
    let (w, h) = (next()?, next()?);
    let _planes = next()?;
    let bpp = next()?;
    let row = next()? as usize;
    if w == 0 || h == 0 || w > 32_768 || h > 32_768 {
        return Err(format!("an image of {w} x {h} pixels was left out"));
    }
    if bpp != 24 {
        return Err(format!("images of {bpp} bits per pixel are not read yet"));
    }
    let (wu, hu) = (w as usize, h as usize);
    let pixels = ri.get(header..).unwrap_or(&[]);
    if row < wu * 3 || row.checked_mul(hu).is_none_or(|n| pixels.len() < n) {
        return Err(unread());
    }
    let mut rgba = Vec::with_capacity(wu * hu * 4);
    for y in 0..hu {
        let src = pixels
            .get((hu - 1 - y) * row..)
            .and_then(|r| r.get(..wu * 3))
            .ok_or_else(unread)?;
        for px in src.chunks_exact(3) {
            if let [b, g, r] = px {
                rgba.extend_from_slice(&[*r, *g, *b, 255]);
            }
        }
    }
    Ok((w, h, encode_png(w, h, &rgba)))
}

/// The open layer's shape lists (its own, then one per open group),
/// opening a layer when none is.
fn open_lists(page: &mut PageBuild) -> &mut Vec<Vec<Shape>> {
    let n = page.layers.len();
    let open = page
        .open
        .get_or_insert_with(|| (format!("Layer {}", n + 1), vec![Vec::new()]));
    if open.1.is_empty() {
        open.1.push(Vec::new());
    }
    &mut open.1
}

fn push_shape(page: &mut PageBuild, shape: Shape) {
    if let Some(top) = open_lists(page).last_mut() {
        top.push(shape);
    }
}

fn image_shape(p: Placement, (w, h, png): (u32, u32, Vec<u8>), doc: &mut Document) -> Shape {
    let mut s = Shape::new(
        doc.ids_mut().shape(),
        ShapeKind::Bitmap {
            rect: p.rect,
            width_px: w,
            height_px: h,
            png,
            fx: None,
        },
    );
    s.transform = p.transform;
    s.fill = Fill::None;
    s.stroke = None;
    s
}

/// A group of `children`, or the only child, or nothing.
fn group_of(mut children: Vec<Shape>, id: tracedraw_core::ShapeId) -> Option<Shape> {
    match children.len() {
        0 => None,
        1 => children.pop(),
        _ => {
            let mut g = Shape::new(id, ShapeKind::Group { children });
            g.fill = Fill::None;
            g.stroke = None;
            Some(g)
        }
    }
}

/// A layer's name: field 4 as a u32 count and UTF-16 letters, else the
/// 8-bit name at the end of field 1 (page number, layer number, flags,
/// object count, u16 length, letters).
/// A font of `rfnt`: field 4 holds the name as a u32 length and UTF-16
/// units, field 1 as a u16 length and bytes ending in 0.
fn font_name(f: &[(u8, &[u8])]) -> Option<String> {
    if let Some(v) = field(f, 4) {
        let mut c = Cur::at(v, 0);
        let n = c.u32()? as usize;
        let units: Vec<u16> = (0..n.min(256)).map_while(|_| c.u16()).collect();
        let name = String::from_utf16_lossy(&units);
        let name = name.trim_end_matches('\0').trim();
        if !name.is_empty() {
            return Some(name.to_string());
        }
    }
    let v = field(f, 1)?;
    let n = usize::from(Cur::at(v, 0).u16()?);
    let bytes = v.get(2..2 + n.min(v.len().saturating_sub(2)))?;
    let name: String = bytes
        .iter()
        .take_while(|b| **b != 0)
        .map(|b| char::from(*b))
        .collect();
    let name = name.trim();
    (!name.is_empty()).then(|| name.to_string())
}

/// A glyph run's field 1: a u16 count, that many UTF-16 units, then each
/// character's place from the baseline's start (i32 x, y). Characters on
/// the same y make one line: its place (mm) and its text.
fn glyph_run(body: &[u8], unit_mm: f64) -> Vec<(Point, String)> {
    let f = fields(body, 0).0;
    let Some(v) = field(&f, 1) else {
        return Vec::new();
    };
    let mut c = Cur::at(v, 0);
    let n = usize::from(c.u16().unwrap_or(0));
    if n.saturating_mul(10) > v.len() {
        return Vec::new();
    }
    let units: Vec<u16> = (0..n).map_while(|_| c.u16()).collect();
    let places: Vec<(i32, i32)> = (0..n).map_while(|_| Some((c.i32()?, c.i32()?))).collect();
    let mut lines: Vec<(Point, Vec<u16>)> = Vec::new();
    for (i, unit) in units.iter().enumerate() {
        let (x, y) = places.get(i).copied().unwrap_or((0, 0));
        let at = Point::new(f64::from(x) * unit_mm, f64::from(y) * unit_mm);
        match lines.last_mut() {
            Some((start, line)) if (start.y - at.y).abs() < 1e-9 => line.push(*unit),
            _ => lines.push((at, vec![*unit])),
        }
    }
    lines
        .into_iter()
        .map(|(at, u)| (at, String::from_utf16_lossy(&u)))
        .filter(|(_, t)| !t.trim().is_empty())
        .collect()
}

fn layer_name(f: &[(u8, &[u8])]) -> Option<String> {
    if let Some(v) = field(f, 4) {
        let mut c = Cur::at(v, 0);
        let n = c.u32()? as usize;
        let units: Vec<u16> = (0..n.min(256)).map_while(|_| c.u16()).collect();
        let s = String::from_utf16_lossy(&units);
        if !s.trim().is_empty() {
            return Some(s);
        }
    }
    let mut c = Cur::at(field(f, 1)?, 12);
    let n = usize::from(c.u16()?);
    let bytes = c.take(n.min(256))?;
    let s: String = bytes.iter().map(|&b| char::from(b)).collect();
    (!s.trim().is_empty()).then_some(s)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chunk(tag: &[u8; 4], data: &[u8]) -> Vec<u8> {
        let mut c = tag.to_vec();
        c.extend_from_slice(&(data.len() as u32).to_le_bytes());
        c.extend_from_slice(data);
        if data.len() % 2 == 1 {
            c.push(0);
        }
        c
    }

    fn list(form: &[u8; 4], body: &[u8]) -> Vec<u8> {
        let mut d = form.to_vec();
        d.extend_from_slice(body);
        chunk(b"LIST", &d)
    }

    fn riff(body: &[u8]) -> Vec<u8> {
        let mut d = b"CMX1".to_vec();
        d.extend_from_slice(body);
        chunk(b"RIFF", &d)
    }

    /// `cont` as the files seen have it, with `coord` the coordinate size.
    fn cont(coord: u8) -> Vec<u8> {
        let mut c = vec![0u8; 180];
        c[..24].copy_from_slice(b"{Corel Binary Meta File}");
        c[32..43].copy_from_slice(b"Windows 3.1");
        c[48] = b'2';
        c[52] = coord;
        c[54] = b'2';
        c[58] = b'0';
        c[62] = 35;
        c[64..72].copy_from_slice(&1e-7f64.to_le_bytes());
        chunk(b"cont", &c)
    }

    fn fld(tag: u8, data: &[u8]) -> Vec<u8> {
        let mut f = vec![tag];
        f.extend_from_slice(&((data.len() + 3) as u16).to_le_bytes());
        f.extend_from_slice(data);
        f
    }

    /// A field list: the fields and 0xff.
    fn flist(fields: &[Vec<u8>]) -> Vec<u8> {
        let mut l = fields.concat();
        l.push(0xff);
        l
    }

    fn instr(code: i16, fields: &[Vec<u8>]) -> Vec<u8> {
        let body = flist(fields);
        let mut i = ((body.len() + 4) as u16).to_le_bytes().to_vec();
        i.extend_from_slice(&code.to_le_bytes());
        i.extend_from_slice(&body);
        i
    }

    fn table(tag: &[u8; 4], entries: &[Vec<u8>]) -> Vec<u8> {
        let mut d = (entries.len() as u16).to_le_bytes().to_vec();
        for e in entries {
            d.extend_from_slice(e);
        }
        chunk(tag, &d)
    }

    fn le(v: &[i32]) -> Vec<u8> {
        v.iter().flat_map(|x| x.to_le_bytes()).collect()
    }

    fn u16s(v: &[u16]) -> Vec<u8> {
        v.iter().flat_map(|x| x.to_le_bytes()).collect()
    }

    /// A colour entry with the `.cdr` record (field 10) and the older
    /// fields.
    fn color_entry(model: u16, value: [u8; 4], old: Option<(u8, &[u8])>) -> Vec<u8> {
        let mut part = vec![1u8];
        part.extend_from_slice(&12u32.to_le_bytes());
        part.extend_from_slice(&model.to_le_bytes());
        part.extend_from_slice(&5u16.to_le_bytes());
        part.extend_from_slice(&[0; 4]);
        part.extend_from_slice(&value);
        let mut rec = (part.len() as u32).to_le_bytes().to_vec();
        rec.extend_from_slice(&part);
        let mut fields = vec![fld(10, &rec)];
        if let Some((m, v)) = old {
            fields.push(fld(1, &[m, 5]));
            fields.push(fld(2, v));
        }
        flist(&fields)
    }

    /// Tables: colours (1 red as BGR, 2 black in CMYK percent, 3 yellow
    /// in the older fields only), one pen of 0.2 mm, line types (solid;
    /// none; dashed with round caps), dashes, three outlines and an
    /// arrowhead pair.
    fn tables() -> Vec<u8> {
        let mut yellow = flist(&[fld(1, &[5, 5]), fld(2, &[255, 255, 0])]);
        yellow.shrink_to_fit();
        [
            table(
                b"rclr",
                &[
                    color_entry(5, [0, 0, 255, 0], None),
                    color_entry(2, [0, 0, 0, 100], Some((2, &[0, 0, 0, 100]))),
                    yellow,
                ],
            ),
            table(
                b"rpen",
                &[flist(&[fld(
                    1,
                    &[0xd0, 0x07, 0, 0, 100, 0, 0, 0, 0, 0, 1, 0],
                )])],
            ),
            table(
                b"rott",
                &[
                    flist(&[fld(1, &[2, 0])]),
                    flist(&[fld(1, &[1, 0])]),
                    flist(&[fld(1, &[4, 1])]),
                ],
            ),
            table(
                b"rdot",
                &[
                    flist(&[fld(1, &u16s(&[0]))]),
                    flist(&[fld(1, &u16s(&[2, 3, 1]))]),
                ],
            ),
            table(
                b"rotl",
                &[
                    flist(&[fld(1, &u16s(&[1, 1, 2, 1, 1, 1]))]),
                    flist(&[fld(1, &u16s(&[2, 1, 2, 1, 1, 1]))]),
                    flist(&[fld(1, &u16s(&[3, 1, 1, 2, 1, 2]))]),
                ],
            ),
            {
                let mut pairs = 2u16.to_le_bytes().to_vec();
                pairs.extend_from_slice(&u16s(&[0, 0, 0, 1]));
                chunk(b"rota", &pairs)
            },
            {
                // A closed triangle, tip to +x, 381 000 units (three
                // outline widths) tall: a filled arrow.
                let mut f1 = 4u16.to_le_bytes().to_vec();
                f1.extend_from_slice(&le(&[
                    -381_000, 190_500, 0, 0, -381_000, -190_500, -381_000, 190_500,
                ]));
                f1.extend_from_slice(&[0x0c, 0x44, 0x44, 0x48]);
                f1.extend_from_slice(&le(&[100]));
                chunk(b"rarw", &flist(&[fld(1, &f1), fld(2, &[0; 10])]))
            },
        ]
        .concat()
    }

    /// Rendering attributes: a uniform fill of colour `fill` and outline
    /// `outline` (0 for none of either).
    fn attrs(fill: u16, outline: u16) -> Vec<u8> {
        let mut a = vec![u8::from(fill > 0) | (u8::from(outline > 0) << 1)];
        if fill > 0 {
            let mut f = 1u16.to_le_bytes().to_vec();
            f.extend_from_slice(&flist(&[fld(1, &u16s(&[fill, 1]))]));
            a.extend_from_slice(&flist(&[fld(1, &f)]));
        }
        if outline > 0 {
            a.extend_from_slice(&flist(&[
                fld(1, &u16s(&[outline])),
                fld(2, &[0; 4]),
                fld(4, &le(&[5_000_000])),
            ]));
        }
        a
    }

    fn begin_page(w: i32, h: i32) -> Vec<u8> {
        let mut f1 = u16s(&[1]);
        f1.extend_from_slice(&[0; 4]);
        f1.extend_from_slice(&le(&[-10, 10, 10, -10, 0]));
        f1.extend_from_slice(&u16s(&[1]));
        f1.extend_from_slice(&[0; 4]);
        instr(
            9,
            &[
                fld(1, &f1),
                fld(2, &u16s(&[1])),
                fld(3, &[0]),
                fld(4, &le(&[w, h])),
            ],
        )
    }

    fn begin_layer(name8: &str, name16: Option<&str>) -> Vec<u8> {
        let mut f1 = u16s(&[1, 1]);
        f1.extend_from_slice(&[0; 8]);
        f1.extend_from_slice(&(name8.len() as u16).to_le_bytes());
        f1.extend_from_slice(name8.as_bytes());
        let mut fields = vec![fld(1, &f1), fld(2, &u16s(&[1])), fld(3, &[0])];
        if let Some(n) = name16 {
            let units: Vec<u16> = n.encode_utf16().collect();
            let mut f4 = (units.len() as u32).to_le_bytes().to_vec();
            f4.extend_from_slice(&u16s(&units));
            fields.push(fld(4, &f4));
        }
        instr(11, &fields)
    }

    fn rect(attrs: &[u8], cx: i32, cy: i32, w: i32, h: i32, radius: i32, angle: i32) -> Vec<u8> {
        let mut g = le(&[cx, cy, w, h, radius, angle]);
        g.extend_from_slice(&le(&[0, 0, 0, 0]));
        instr(68, &[fld(1, attrs), fld(2, &g)])
    }

    fn curve(attrs: &[u8], pts: &[(i32, i32)], types: &[u8]) -> Vec<u8> {
        let mut g = (pts.len() as u16).to_le_bytes().to_vec();
        for (x, y) in pts {
            g.extend_from_slice(&le(&[*x, *y]));
        }
        g.extend_from_slice(types);
        instr(
            67,
            &[
                fld(1, attrs),
                fld(2, &g),
                fld(3, &le(&[0, 0, 0, 0])),
                fld(4, &[]),
            ],
        )
    }

    fn page(instructions: &[Vec<u8>]) -> Vec<u8> {
        chunk(b"page", &instructions.concat())
    }

    fn file(pages: &[Vec<u8>]) -> Vec<u8> {
        riff(&[cont(b'4'), pages.concat(), tables()].concat())
    }

    fn shapes(doc: &Document) -> Vec<&Shape> {
        doc.pages
            .iter()
            .flat_map(|p| &p.layers)
            .flat_map(|l| &l.shapes)
            .collect()
    }

    #[test]
    fn pages_layers_and_shapes() {
        let f = file(&[page(&[
            begin_page(2_100_000, 2_970_000),
            begin_layer("Ebene 1", Some("Слой 1")),
            // A 20 x 10 mm rectangle 5 mm right of the page's centre.
            rect(&attrs(1, 1), 50_000, 0, 200_000, 100_000, 0, 0),
            instr(13, &[fld(1, &[0; 26])]),
            curve(
                &attrs(3, 0),
                &[(0, 0), (100_000, 0), (0, 100_000), (0, 0)],
                &[0x0c, 0x44, 0x44, 0x48],
            ),
            curve(&attrs(0, 2), &[(0, 0), (100_000, 0)], &[0x04, 0x44]),
            instr(14, &[]),
            instr(12, &[]),
            instr(10, &[]),
        ])]);
        let (doc, rep) = open_bytes(&f, "t").expect("read");
        assert!(rep.warnings.is_empty(), "{:?}", rep.warnings);
        assert_eq!((rep.pages, rep.layers, rep.shapes), (1, 1, 2));
        let p = &doc.pages[0];
        assert!((p.size.width - 210.0).abs() < 1e-9 && (p.size.height - 297.0).abs() < 1e-9);
        assert_eq!(p.layers[0].name, "Слой 1");
        let s = shapes(&doc);
        let b = s[0].bounds();
        assert!(
            (b.center().x - 110.0).abs() < 1e-6 && (b.center().y - 148.5).abs() < 1e-6,
            "{b:?}"
        );
        assert!((b.width() - 20.0).abs() < 1e-6 && (b.height() - 10.0).abs() < 1e-6);
        assert_eq!(s[0].fill, Fill::Solid(Color::rgb8(255, 0, 0)));
        let stroke = s[0].stroke.as_ref().expect("outline");
        assert!((stroke.width - 0.2).abs() < 1e-9);
        assert_eq!(stroke.color, Color::cmyk_pct(0.0, 0.0, 0.0, 100.0));
        match &s[1].kind {
            ShapeKind::Group { children } => {
                assert_eq!(children.len(), 2);
                // The colour only the older fields give.
                assert_eq!(children[0].fill, Fill::Solid(Color::rgb8(255, 255, 0)));
                assert!(children[0].stroke.is_none());
                // Line type 1 draws no outline.
                assert!(children[1].stroke.is_none());
                assert_eq!(children[1].fill, Fill::None);
                let cb = children[0].bounds();
                assert!(
                    (cb.x0 - 105.0).abs() < 1e-6 && (cb.y1 - 158.5).abs() < 1e-6,
                    "{cb:?}"
                );
            }
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn outlines_dashes_and_arrowheads() {
        let f = file(&[page(&[
            begin_page(1_000_000, 1_000_000),
            begin_layer("Layer 1", None),
            curve(&attrs(0, 3), &[(0, 0), (100_000, 0)], &[0x04, 0x44]),
            instr(12, &[]),
        ])]);
        let (doc, rep) = open_bytes(&f, "t").expect("read");
        assert!(rep.warnings.is_empty(), "{:?}", rep.warnings);
        assert_eq!(doc.pages[0].layers[0].name, "Layer 1");
        let s = shapes(&doc);
        let o = s[0].stroke.as_ref().expect("outline");
        // A count, then the dash and gap lengths.
        assert_eq!(o.dash, vec![3.0, 1.0]);
        assert_eq!(o.cap, LineCap::Round);
        assert_eq!(o.color, Color::rgb8(255, 0, 0));
        assert_eq!(o.start_arrow, Arrowhead::None);
        assert_eq!(o.end_arrow.name(), "Arrowhead 1");
        // The head keeps the file's size: three outline widths tall, as
        // long as it is tall, whatever the width.
        let mut line = tracedraw_core::BezPath::new();
        line.move_to((0.0, 0.0));
        line.line_to((50.0, 0.0));
        for width in [1.0, 2.5] {
            let stroke = Stroke { width, ..o.clone() };
            let heads = tracedraw_core::arrowhead_paths(&line, &stroke);
            assert_eq!(heads.len(), 1);
            let b = tracedraw_core::geometry::Shape::bounding_box(&heads[0]);
            assert!((b.height() - 3.0 * width).abs() < 1e-9, "{b:?}");
            assert!((b.width() - 3.0 * width).abs() < 1e-9, "{b:?}");
            assert!((b.x1 - 50.0).abs() < 1e-9, "{b:?}");
        }
    }

    #[test]
    fn arrowheads_keep_size_and_placement() {
        use tracedraw_core::geometry::Shape as _;
        let head = |pts: &[i32], types: &[u8]| {
            let mut f1 = u16::try_from(types.len()).unwrap().to_le_bytes().to_vec();
            f1.extend_from_slice(&le(pts));
            f1.extend_from_slice(types);
            f1.extend_from_slice(&le(&[0]));
            let data = flist(&[fld(1, &f1)]);
            let f = arrowheads(&data).next().expect("one head");
            arrowhead(&f, 7)
        };
        // Head space: one unit is four outline widths, 508 000 file units.
        let bounds = |a: &Arrowhead| match a {
            Arrowhead::Custom { path, .. } => path.bounding_box(),
            other => panic!("expected a custom head: {other:?}"),
        };
        let near = |a: f64, b: f64| (a - b).abs() < 1e-3;
        // A closed diamond 254 000 units tall: two widths.
        let diamond = head(
            &[
                0, 0, -127_000, 127_000, -254_000, 0, -127_000, -127_000, 0, 0,
            ],
            &[0x0c, 0x44, 0x44, 0x44, 0x48],
        );
        assert_eq!(diamond.name(), "Arrowhead 7");
        let b = bounds(&diamond);
        assert!(
            near(b.height(), 0.5) && near(b.x1, 0.0) && near(b.y0, -0.25),
            "{b:?}"
        );
        // A large one is not shrunk to the preset size.
        let big = head(
            &[0, 0, -1_270_000, 635_000, -1_270_000, -635_000, 0, 0],
            &[0x0c, 0x44, 0x44, 0x48],
        );
        assert!(near(bounds(&big).height(), 2.5));
        // A square behind the end point, one centred on it and one drawn
        // above the line keep their places.
        let square = head(
            &[
                -254_000, -127_000, 0, -127_000, 0, 127_000, -254_000, 127_000, -254_000, -127_000,
            ],
            &[0x0c, 0x44, 0x44, 0x44, 0x48],
        );
        let b = bounds(&square);
        assert!(
            near(b.x1, 0.0) && near(b.y0, -0.25) && near(b.y1, 0.25),
            "{b:?}"
        );
        let centred = head(
            &[
                -127_000, -127_000, 127_000, -127_000, 127_000, 127_000, -127_000, 127_000,
                -127_000, -127_000,
            ],
            &[0x0c, 0x44, 0x44, 0x44, 0x48],
        );
        let b = bounds(&centred);
        assert!(near(b.x0, -0.25) && near(b.x1, 0.25), "{b:?}");
        let above = head(
            &[0, 127_000, -254_000, 127_000, -254_000, 508_000, 0, 127_000],
            &[0x0c, 0x44, 0x44, 0x48],
        );
        let b = bounds(&above);
        assert!(near(b.y0, 0.25) && near(b.y1, 1.0), "{b:?}");
        // An open head is drawn as a line one outline width (0.25) wide
        // with round ends: here a V and a bar across the end point.
        let open = head(
            &[-500_000, 300_000, 0, 0, -500_000, -300_000],
            &[0x04, 0x44, 0x44],
        );
        let b = bounds(&open);
        assert!(
            near(b.x1, 0.125) && near(b.height(), (600_000.0 + 127_000.0) / 508_000.0),
            "{b:?}"
        );
        let bar = head(&[0, -254_000, 0, 254_000], &[0x04, 0x44]);
        let b = bounds(&bar);
        assert!(near(b.width(), 0.25) && near(b.height(), 1.25), "{b:?}");
        // Swept bands turn the same way as the closed subpaths below, so a
        // head mixing both fills every part.
        let Arrowhead::Custom { path, .. } = &bar else {
            panic!("{bar:?}");
        };
        assert!(path.area() > 0.0, "{}", path.area());
        // Closed subpaths add up: an inner square drawn the other way round
        // is no hole.
        let both = head(
            &[
                -254_000, -127_000, 0, -127_000, 0, 127_000, -254_000, 127_000, -254_000, -127_000,
                -190_500, -63_500, -190_500, 63_500, -63_500, 63_500, -63_500, -63_500, -190_500,
                -63_500,
            ],
            &[0x0c, 0x44, 0x44, 0x44, 0x48, 0x0c, 0x44, 0x44, 0x44, 0x48],
        );
        let Arrowhead::Custom { path, .. } = &both else {
            panic!("{both:?}");
        };
        assert!(near(path.area(), 0.25 + 0.0625), "{}", path.area());
        // At the start of a line the head is flipped across the line, so
        // the part above the line stays above it once turned round.
        let b = bounds(&start_head(above));
        assert!(near(b.y0, -1.0) && near(b.y1, -0.25), "{b:?}");
        assert_eq!(start_head(Arrowhead::Circle), Arrowhead::Circle);
        // Unreadable heads give none.
        assert_eq!(head(&[0, 0], &[0x04]), Arrowhead::None);
    }

    #[test]
    fn fountain_fills() {
        let mut settings = u16s(&[1, 1, 10]);
        settings.extend_from_slice(&le(&[45_000_000, 20, -30]));
        settings.extend_from_slice(&u16s(&[0, 3, 0, 50]));
        let mut f = 2u16.to_le_bytes().to_vec();
        f.extend_from_slice(&flist(&[
            fld(1, &settings),
            fld(2, &u16s(&[3, 1, 0, 3, 50, 2, 100])),
        ]));
        let mut a = vec![1u8];
        a.extend_from_slice(&flist(&[fld(1, &f)]));
        let file = file(&[page(&[
            begin_page(1_000_000, 1_000_000),
            rect(&a, 0, 0, 100_000, 100_000, 0, 0),
        ])]);
        let (doc, rep) = open_bytes(&file, "t").expect("read");
        assert!(rep.warnings.is_empty(), "{:?}", rep.warnings);
        match &shapes(&doc)[0].fill {
            Fill::Fountain(g) => {
                assert_eq!(g.kind, FountainKind::Radial);
                assert!((g.angle - 45.0).abs() < 1e-9);
                assert!((g.offset.x - 0.2).abs() < 1e-9 && (g.offset.y + 0.3).abs() < 1e-9);
                assert!((g.edge_pad - 0.1).abs() < 1e-9);
                let pos: Vec<f64> = g.stops.iter().map(|s| s.pos).collect();
                assert_eq!(pos, vec![0.0, 0.5, 1.0]);
                assert_eq!(g.stops[0].color, Color::rgb8(255, 0, 0));
                assert_eq!(g.stops[1].color, Color::rgb8(255, 255, 0));
            }
            other => panic!("{other:?}"),
        }
    }

    /// An image block of `w` x `h` pixels, 24 bits, rows bottom first.
    fn image_data(w: u32, h: u32, rows_bottom_first: &[Vec<[u8; 3]>]) -> Vec<u8> {
        let row = (w as usize * 3).div_ceil(4) * 4;
        let mut px = Vec::new();
        for r in rows_bottom_first {
            let mut line: Vec<u8> = r.iter().flat_map(|[r, g, b]| [*b, *g, *r]).collect();
            line.resize(row, 0);
            px.extend_from_slice(&line);
        }
        let mut ri = b"RI".to_vec();
        ri.extend_from_slice(&((78 + px.len()) as u32).to_le_bytes());
        ri.extend_from_slice(&[0; 4]);
        let mut header = vec![78u32, 1, 1, w, h, 1, 24, row as u32, px.len() as u32];
        header.resize(17, 0);
        for v in header {
            ri.extend_from_slice(&v.to_le_bytes());
        }
        ri.resize(78, 0);
        ri.extend_from_slice(&px);
        let mut data = vec![2u8];
        data.extend_from_slice(&((ri.len() + 5) as u32).to_le_bytes());
        data.extend_from_slice(&ri);
        list(
            b"imag",
            &[
                chunk(b"info", &flist(&[fld(1, &[0; 12])])),
                chunk(b"data", &data),
            ]
            .concat(),
        )
    }

    #[test]
    fn images_after_a_jump() {
        // Placement: extent 0..10 x 0..20 mm, scaled by a half and moved
        // 5 mm left and 5 mm down of the centre.
        let mut g = le(&[0, 200_000, 100_000, 0, 0, 200_000, 100_000, 0]);
        g.extend_from_slice(&u16s(&[2]));
        for v in [0.5f64, 0.0, 0.0, 0.5, -50_000.0, -50_000.0] {
            g.extend_from_slice(&v.to_le_bytes());
        }
        g.extend_from_slice(&u16s(&[5, 1, 0]));
        let draw = instr(69, &[fld(1, &[0]), fld(2, &g), fld(5, &[0])]);
        let embedded = image_data(1, 2, &[vec![[0, 0, 255]], vec![[255, 0, 0]]]);
        let head = [
            begin_page(1_000_000, 1_000_000),
            instr(88, &[fld(1, &[0; 18])]),
            draw,
        ]
        .concat();
        // The jump's target: a file offset, past the embedded list. The
        // page's data starts after the RIFF header, `cont` and the page's
        // own header.
        let page_start = 12 + cont(b'4').len() + 8;
        let jump_len = instr(111, &[fld(1, &[0; 4])]).len();
        let target = (page_start + head.len() + jump_len + embedded.len()) as i32;
        let jump = instr(111, &[fld(1, &le(&[target]))]);
        let f = file(&[page(&[
            head,
            jump,
            embedded,
            instr(89, &[]),
            instr(10, &[]),
        ])]);
        let (doc, rep) = open_bytes(&f, "t").expect("read");
        assert!(rep.warnings.is_empty(), "{:?}", rep.warnings);
        let s = shapes(&doc);
        assert_eq!(s.len(), 1);
        match &s[0].kind {
            ShapeKind::Bitmap {
                width_px,
                height_px,
                png,
                ..
            } => {
                assert_eq!((*width_px, *height_px), (1, 2));
                assert_eq!(png.get(1..4), Some(b"PNG".as_slice()));
            }
            other => panic!("{other:?}"),
        }
        let b = s[0].bounds();
        assert!(
            (b.x0 - 45.0).abs() < 1e-6 && (b.y0 - 45.0).abs() < 1e-6,
            "{b:?}"
        );
        assert!(
            (b.width() - 5.0).abs() < 1e-6 && (b.height() - 10.0).abs() < 1e-6,
            "{b:?}"
        );
        // Top row first in the picture: red above blue. The rows of the
        // PNG (RGBA, each after a filter byte) from its IDAT chunk.
        let ShapeKind::Bitmap { png, .. } = &s[0].kind else {
            panic!("not a picture");
        };
        let at = png
            .windows(4)
            .position(|w| w == b"IDAT")
            .expect("an IDAT chunk");
        let len = u32::from_be_bytes([png[at - 4], png[at - 3], png[at - 2], png[at - 1]]) as usize;
        let mut rows = Vec::new();
        std::io::Read::read_to_end(
            &mut flate2::read::ZlibDecoder::new(&png[at + 4..at + 4 + len]),
            &mut rows,
        )
        .expect("rows");
        assert_eq!(rows.get(1..4), Some([255, 0, 0].as_slice()), "{rows:?}");
        assert_eq!(rows.get(6..9), Some([0, 0, 255].as_slice()), "{rows:?}");
    }

    #[test]
    fn what_is_not_read_is_reported() {
        let f = file(&[page(&[
            begin_page(1_000_000, 1_000_000),
            instr(66, &[]),
            instr(20, &[]),
            instr(102, &[]),
            instr(21, &[]),
            instr(200, &[]),
            curve(
                &{
                    let mut a = vec![1u8];
                    let mut fl = 9u16.to_le_bytes().to_vec();
                    fl.push(0xff);
                    a.extend_from_slice(&flist(&[fld(1, &fl)]));
                    a
                },
                &[(0, 0), (10, 0), (0, 10)],
                &[0x0c, 0x44, 0x48],
            ),
        ])]);
        let (doc, rep) = open_bytes(&f, "t").expect("read");
        assert_eq!(rep.skipped_objects, 2, "{:?}", rep.warnings);
        assert_eq!(rep.shapes, 1);
        assert_eq!(shapes(&doc)[0].fill, Fill::None);
        for w in ["ellipse", "text", "instruction 200", "fill type 9"] {
            assert!(
                rep.warnings.iter().any(|x| x.contains(w)),
                "{w}: {:?}",
                rep.warnings
            );
        }
        // Text is reported once.
        assert_eq!(
            rep.warnings.iter().filter(|x| x.contains("text")).count(),
            1
        );
    }

    #[test]
    fn text_from_glyph_runs() {
        let mut m = u16s(&[2]);
        for v in [1.0f64, 0.0, 0.0, 1.0, -100_000.0, 200_000.0] {
            m.extend_from_slice(&v.to_le_bytes());
        }
        let mut style = u16s(&[2]);
        style.extend_from_slice(&84_667u32.to_le_bytes());
        let mut run = u16s(&[4]);
        run.extend_from_slice(&u16s(&"HiYo".encode_utf16().collect::<Vec<_>>()));
        run.extend_from_slice(&le(&[0, 0, 50_000, 0, 0, -100_000, 50_000, -100_000]));
        let pg = page(&[
            begin_page(1_000_000, 1_000_000),
            begin_layer("L", None),
            instr(20, &[]),
            instr(98, &[]),
            instr(102, &[]),
            instr(21, &[]),
            instr(70, &[]),
            instr(72, &[fld(1, &[0]), fld(2, &m)]),
            instr(85, &[fld(1, &attrs(1, 0)), fld(2, &style)]),
            instr(65, &[fld(1, &run)]),
            instr(73, &[]),
            instr(71, &[]),
            // A text object never drawn.
            instr(20, &[]),
            instr(21, &[]),
        ]);
        let mut arial = 6u16.to_le_bytes().to_vec();
        arial.extend_from_slice(b"Arial\0");
        let units: Vec<u16> = "Verdana".encode_utf16().collect();
        let mut verdana = (units.len() as u32).to_le_bytes().to_vec();
        verdana.extend_from_slice(&u16s(&units));
        let fonts = chunk(
            b"rfnt",
            &[flist(&[fld(1, &arial)]), flist(&[fld(4, &verdana)])].concat(),
        );
        let f = riff(&[cont(b'4'), pg, tables(), fonts].concat());
        let (doc, rep) = open_bytes(&f, "t").expect("read");
        assert_eq!(rep.skipped_objects, 1, "{:?}", rep.warnings);
        let s = shapes(&doc);
        assert_eq!(s.len(), 2);
        for (shape, text, y) in [(s[0], "Hi", 70.0), (s[1], "Yo", 60.0)] {
            let ShapeKind::Text { spans, frame, .. } = &shape.kind else {
                panic!("{:?}", shape.kind);
            };
            assert!(frame.is_none());
            assert_eq!(spans[0].text, text);
            assert_eq!(spans[0].font_family, "Verdana");
            assert!(
                (spans[0].size_pt - 24.0).abs() < 1e-3,
                "{}",
                spans[0].size_pt
            );
            let at = shape.transform.translation();
            assert!(
                (at.x - 40.0).abs() < 1e-9 && (at.y - y).abs() < 1e-9,
                "{at:?}"
            );
            assert_eq!(shape.fill, Fill::Solid(Color::rgb8(255, 0, 0)));
        }
        assert_eq!(
            font_name(&[(1, arial.as_slice())]).as_deref(),
            Some("Arial")
        );
        // Broken runs read as nothing.
        assert!(glyph_run(&flist(&[fld(1, &u16s(&[60_000]))]), 1.0).is_empty());
        assert!(glyph_run(&[], 1.0).is_empty());
    }

    #[test]
    fn long_instructions() {
        // A negative code: the size is a u32 after the code (assumed).
        let short = rect(&attrs(1, 0), 0, 0, 100_000, 100_000, 0, 0);
        let mut long = 0u16.to_le_bytes().to_vec();
        long.extend_from_slice(&(-68i16).to_le_bytes());
        long.extend_from_slice(&((short.len() + 4) as u32).to_le_bytes());
        long.extend_from_slice(&short[4..]);
        let f = file(&[page(&[begin_page(1_000_000, 1_000_000), long, short])]);
        let (_, rep) = open_bytes(&f, "t").expect("read");
        assert_eq!(rep.shapes, 2, "{:?}", rep.warnings);
    }

    #[test]
    fn other_files_and_versions() {
        assert!(matches!(
            open_bytes(b"RIFF\x04\0\0\0CDRE", "t"),
            Err(Error::NotCmx)
        ));
        assert!(matches!(open_bytes(b"", "t"), Err(Error::NotCmx)));
        let sixteen = riff(&[cont(b'2'), page(&[])].concat());
        assert!(matches!(
            open_bytes(&sixteen, "t"),
            Err(Error::UnsupportedContainer(_))
        ));
        let mut big = riff(&cont(b'4'));
        big[..4].copy_from_slice(b"RIFX");
        assert!(matches!(
            open_bytes(&big, "t"),
            Err(Error::UnsupportedContainer(_))
        ));
        // No page: one empty page; the crate's entry point reads CMX too.
        let (doc, rep) = crate::open_bytes(&riff(&cont(b'4')), "t").expect("read");
        assert_eq!(doc.pages.len(), 1);
        assert!(rep.warnings.iter().any(|w| w.contains("no pages")));
    }

    #[test]
    fn broken_files_never_panic() {
        let mut g = le(&[0, 200_000, 100_000, 0, 0, 200_000, 100_000, 0]);
        g.extend_from_slice(&u16s(&[1, 5, 1, 0]));
        let f = file(&[page(&[
            begin_page(1_000_000, 1_000_000),
            begin_layer("L", Some("L")),
            rect(&attrs(1, 1), 0, 0, 100_000, 100_000, 0, 0),
            instr(13, &[]),
            curve(
                &attrs(2, 3),
                &[(0, 0), (10, 0), (0, 10)],
                &[0x0c, 0x44, 0x48],
            ),
            instr(69, &[fld(1, &[0]), fld(2, &g)]),
            instr(111, &[fld(1, &le(&[1]))]),
            image_data(2, 1, &[vec![[1, 2, 3], [4, 5, 6]]]),
            instr(20, &[]),
            instr(72, &[fld(2, &u16s(&[2, 0, 0, 0, 0]))]),
            instr(85, &[fld(1, &attrs(1, 0)), fld(2, &u16s(&[1, 9, 0]))]),
            instr(65, &[fld(1, &[2, 0, 65, 0, 66, 0, 1, 0, 0, 0, 2, 0, 0, 0])]),
        ])]);
        for n in 0..f.len() {
            let _ = open_bytes(&f[..n], "t");
        }
        let mut seed = 0x2545_f491_4f6c_dd1du64;
        for _ in 0..3000 {
            let mut m = f.clone();
            for _ in 0..4 {
                seed ^= seed << 13;
                seed ^= seed >> 7;
                seed ^= seed << 17;
                let i = (seed % m.len() as u64) as usize;
                m[i] = (seed >> 32) as u8;
            }
            let _ = open_bytes(&m, "t");
        }
    }
}
