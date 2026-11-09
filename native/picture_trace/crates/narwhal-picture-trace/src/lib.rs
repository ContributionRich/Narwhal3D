//! Cut lines from images: the Narwhal 3D picture tracer as a standalone library.
//!
//! Give it an image and its printed size; it returns closed Bézier loops around the artwork,
//! offset outward by the requested distance, ready for a vinyl or laser cutter.
//!
//! ```no_run
//! use narwhal_picture_trace::{ArtworkSize, CutSettings, Image, generate_cut};
//!
//! let image = Image::decode(&std::fs::read("sticker.png")?)?;
//! let size = ArtworkSize::from_artwork_width_inches(&image, 4.0);
//! let cut = generate_cut(&image, size, &CutSettings::default())?;
//! std::fs::write("sticker-cut.svg", cut.to_svg_document())?;
//! # Ok::<(), Box<dyn std::error::Error>>(())
//! ```
//!
//! Units are points (1/72 inch). The origin is the image's top-left corner and y points down.
#![forbid(unsafe_code)]

pub mod pipeline;

pub use narwhal_trace_core::{CubicNode, Point};
use pipeline::{POINTS_PER_INCH, PROCESSING_PPI};
use std::fmt;
use vectorcraft_geom::{AnchorKind, BezPath, PathData, Rect};

/// How the background around the artwork is recognized. Transparent pixels are always
/// background, and so is a near-uniform opaque border connected to the image edge.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Background {
    /// Detect a solid border colour and remove it everywhere (the default for
    /// opened images). Transparent-bordered images use transparency only.
    Auto,
    /// Use transparency (and the connected border region) only.
    TransparencyOnly,
    /// Remove this colour everywhere, within the tolerance.
    Color([u8; 3]),
}

/// Cut settings, with the tracer's defaults.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CutSettings {
    /// Distance from the artwork to the cut, in points. Negative cuts inside the art.
    pub offset_pt: f64,
    /// Maximum curve-fitting error, in points at the normalized processing size.
    /// `with_smoothness` sets it from the outline smoothness.
    pub tolerance_pt: f64,
    /// Also cut out holes inside the artwork.
    pub keep_inner_cuts: bool,
    /// Holes smaller than this area (square points) are not cut.
    pub ignore_holes_under_pt2: f64,
    pub background: Background,
    /// How close (0–60%) a colour must be to the background colour to count as background.
    pub background_tolerance_percent: f64,
    /// 0 (Detailed) to 1 (Smooth). Sets `tolerance_pt` through `with_smoothness`, and how
    /// rounded the bridges are when separate islands are joined into one cut.
    pub outline_smoothness: f64,
}

impl CutSettings {
    /// These settings with outline smoothness `smoothness` (0 Detailed to 1 Smooth) and the
    /// matching curve-fitting tolerance, like an Outline smoothness slider.
    #[must_use]
    pub fn with_smoothness(self, smoothness: f64) -> Self {
        Self {
            outline_smoothness: if smoothness.is_finite() {
                smoothness.clamp(0.0, 1.0)
            } else {
                0.5
            },
            tolerance_pt: pipeline::smoothness_tolerance_pt(smoothness),
            ..self
        }
    }
}

impl Default for CutSettings {
    fn default() -> Self {
        Self {
            offset_pt: 0.05 * POINTS_PER_INCH,
            tolerance_pt: 0.0,
            keep_inner_cuts: true,
            ignore_holes_under_pt2: 0.07 * POINTS_PER_INCH * POINTS_PER_INCH,
            background: Background::Auto,
            background_tolerance_percent: 12.0,
            outline_smoothness: 0.0,
        }
        .with_smoothness(0.4)
    }
}

/// An 8-bit straight-alpha RGBA image.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Image {
    width: u32,
    height: u32,
    rgba: Vec<u8>,
}

impl Image {
    /// From row-major RGBA bytes (`width * height * 4`).
    pub fn new(width: u32, height: u32, rgba: Vec<u8>) -> Result<Self, CutError> {
        if width == 0 || height == 0 {
            return Err(CutError::InvalidImage("the image is empty".into()));
        }
        let expected = u64::from(width) * u64::from(height) * 4;
        if rgba.len() as u64 != expected {
            return Err(CutError::InvalidImage(format!(
                "expected {expected} RGBA bytes for {width}x{height}, got {}",
                rgba.len()
            )));
        }
        Ok(Self {
            width,
            height,
            rgba,
        })
    }

    /// Decodes PNG, JPEG, WebP, GIF, TIFF or BMP bytes (at most 64 megapixels).
    pub fn decode(bytes: &[u8]) -> Result<Self, CutError> {
        let raster = vectorcraft_trace::Raster::decode(bytes)
            .map_err(|error| CutError::InvalidImage(error.to_string()))?;
        Self::new(raster.width, raster.height, raster.rgba)
    }

    #[must_use]
    pub const fn width(&self) -> u32 {
        self.width
    }

    #[must_use]
    pub const fn height(&self) -> u32 {
        self.height
    }

    #[must_use]
    pub fn rgba(&self) -> &[u8] {
        &self.rgba
    }

    /// The box `(x, y, width, height)` of pixels that aren't fully transparent: the artwork
    /// Artwork is sized and cut after trimming transparent padding.
    #[must_use]
    pub fn visible_box(&self) -> Option<(u32, u32, u32, u32)> {
        let (mut x0, mut y0, mut x1, mut y1) = (self.width, self.height, 0, 0);
        for (index, pixel) in self.rgba.chunks_exact(4).enumerate() {
            if pixel[3] > 0 {
                let x = (index % self.width as usize) as u32;
                let y = (index / self.width as usize) as u32;
                (x0, y0, x1, y1) = (x0.min(x), y0.min(y), x1.max(x + 1), y1.max(y + 1));
            }
        }
        (x1 > x0 && y1 > y0).then(|| (x0, y0, x1 - x0, y1 - y0))
    }

    fn crop(&self, x: u32, y: u32, width: u32, height: u32) -> Result<Self, CutError> {
        let mut rgba = Vec::with_capacity(width as usize * height as usize * 4);
        for row in y..y + height {
            let start = (row as usize * self.width as usize + x as usize) * 4;
            rgba.extend_from_slice(
                self.rgba
                    .get(start..start + width as usize * 4)
                    .ok_or_else(|| CutError::InvalidImage("crop outside the image".into()))?,
            );
        }
        Self::new(width, height, rgba)
    }

    fn background_color(&self, settings: &CutSettings) -> Result<Option<[u8; 3]>, CutError> {
        match settings.background {
            Background::Auto => {
                pipeline::detect_background(&self.rgba, self.width as usize, self.height as usize)
            }
            Background::TransparencyOnly => Ok(None),
            Background::Color(color) => Ok(Some(color)),
        }
    }
}

/// The printed size of the whole image, in points.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ArtworkSize {
    pub width_pt: f64,
    pub height_pt: f64,
}

impl ArtworkSize {
    #[must_use]
    pub const fn points(width_pt: f64, height_pt: f64) -> Self {
        Self {
            width_pt,
            height_pt,
        }
    }

    /// The image printed at `dpi` pixels per inch.
    #[must_use]
    pub fn from_dpi(image: &Image, dpi: f64) -> Self {
        let points_per_pixel = POINTS_PER_INCH / dpi;
        Self::points(
            f64::from(image.width) * points_per_pixel,
            f64::from(image.height) * points_per_pixel,
        )
    }

    /// The image printed `inches` wide, keeping its aspect ratio.
    #[must_use]
    pub fn from_width_inches(image: &Image, inches: f64) -> Self {
        let width_pt = inches * POINTS_PER_INCH;
        Self::points(
            width_pt,
            width_pt * f64::from(image.height) / f64::from(image.width),
        )
    }

    /// The image printed `inches` tall, keeping its aspect ratio.
    #[must_use]
    pub fn from_height_inches(image: &Image, inches: f64) -> Self {
        let height_pt = inches * POINTS_PER_INCH;
        Self::points(
            height_pt * f64::from(image.width) / f64::from(image.height),
            height_pt,
        )
    }

    /// The image printed so its visible artwork (transparent padding excluded) is `inches`
    /// wide, as when sizing artwork for a cut.
    #[must_use]
    pub fn from_artwork_width_inches(image: &Image, inches: f64) -> Self {
        let visible = image.visible_box().map_or(image.width, |(_, _, w, _)| w);
        let pt_per_px = inches * POINTS_PER_INCH / f64::from(visible);
        Self::points(
            f64::from(image.width) * pt_per_px,
            f64::from(image.height) * pt_per_px,
        )
    }

    /// The image printed so its visible artwork is `inches` tall.
    #[must_use]
    pub fn from_artwork_height_inches(image: &Image, inches: f64) -> Self {
        let visible = image.visible_box().map_or(image.height, |(_, _, _, h)| h);
        let pt_per_px = inches * POINTS_PER_INCH / f64::from(visible);
        Self::points(
            f64::from(image.width) * pt_per_px,
            f64::from(image.height) * pt_per_px,
        )
    }
}

/// One closed cut loop. Each node's `out_handle` and the next node's `in_handle` are the
/// control points of the cubic Bézier between them; the last node connects to the first.
#[derive(Clone, Debug, PartialEq)]
pub struct CutLoop {
    pub nodes: Vec<CubicNode>,
}

/// The generated cut: outer outlines and (with `keep_inner_cuts`) holes, all closed.
#[derive(Clone, Debug, PartialEq)]
pub struct Cut {
    pub loops: Vec<CutLoop>,
    /// The artwork size the cut was generated for, in points.
    pub artwork: ArtworkSize,
    /// When separate islands were joined into one cut, the bridge radius in points.
    pub unified_join_pt: Option<f64>,
}

impl Cut {
    fn from_path(path: &PathData, artwork: ArtworkSize, unified_join_pt: Option<f64>) -> Self {
        let point = |p: vectorcraft_geom::Point| Point::new(p.x, p.y);
        Self {
            loops: path
                .subpaths
                .iter()
                .filter(|subpath| !subpath.anchors.is_empty())
                .map(|subpath| CutLoop {
                    nodes: subpath
                        .anchors
                        .iter()
                        .map(|anchor| CubicNode {
                            anchor: point(anchor.p),
                            in_handle: point(anchor.h_in),
                            out_handle: point(anchor.h_out),
                            corner: anchor.kind == AnchorKind::Corner,
                        })
                        .collect(),
                })
                .collect(),
            artwork,
            unified_join_pt,
        }
    }

    fn bez_path(&self) -> BezPath {
        let point = |p: Point| vectorcraft_geom::Point::new(p.x, p.y);
        let mut path = BezPath::new();
        for cut_loop in &self.loops {
            let Some(first) = cut_loop.nodes.first() else {
                continue;
            };
            path.move_to(point(first.anchor));
            for (index, node) in cut_loop.nodes.iter().enumerate() {
                let next = &cut_loop.nodes[(index + 1) % cut_loop.nodes.len()];
                path.curve_to(
                    point(node.out_handle),
                    point(next.in_handle),
                    point(next.anchor),
                );
            }
            path.close_path();
        }
        path
    }

    /// SVG path data (`d` attribute), in points.
    #[must_use]
    pub fn to_svg_path_data(&self) -> String {
        self.bez_path().to_svg()
    }

    /// A standalone SVG sized in inches, with the cut drawn as a red hairline.
    #[must_use]
    pub fn to_svg_document(&self) -> String {
        let (width, height) = (self.artwork.width_pt, self.artwork.height_pt);
        let (x, y, view_width, view_height) = match self.bounds() {
            Some([x0, y0, x1, y1]) => (
                x0.min(0.0),
                y0.min(0.0),
                x1.max(width) - x0.min(0.0),
                y1.max(height) - y0.min(0.0),
            ),
            None => (0.0, 0.0, width, height),
        };
        format!(
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{}in\" height=\"{}in\" viewBox=\"{x} {y} {view_width} {view_height}\">\n  <path d=\"{}\" fill=\"none\" stroke=\"#ff0000\" stroke-width=\"0.5\"/>\n</svg>\n",
            view_width / POINTS_PER_INCH,
            view_height / POINTS_PER_INCH,
            self.to_svg_path_data()
        )
    }

    /// Bounds of the cut `[x0, y0, x1, y1]`, in points.
    #[must_use]
    pub fn bounds(&self) -> Option<[f64; 4]> {
        use vectorcraft_geom::Shape as _;
        let path = self.bez_path();
        if path.elements().is_empty() {
            return None;
        }
        let rect = path.bounding_box();
        Some([rect.x0, rect.y0, rect.x1, rect.y1])
    }

    /// Each loop as a polyline, with curves split into pieces about `step_pt` long.
    #[must_use]
    pub fn polylines(&self, step_pt: f64) -> Vec<Vec<Point>> {
        use vectorcraft_geom::ParamCurve as _;
        let point = |p: Point| vectorcraft_geom::Point::new(p.x, p.y);
        self.loops
            .iter()
            .map(|cut_loop| {
                let mut out = Vec::new();
                for (index, node) in cut_loop.nodes.iter().enumerate() {
                    let next = &cut_loop.nodes[(index + 1) % cut_loop.nodes.len()];
                    let curve = vectorcraft_geom::CubicBez::new(
                        point(node.anchor),
                        point(node.out_handle),
                        point(next.in_handle),
                        point(next.anchor),
                    );
                    let span = curve.p0.distance(curve.p1)
                        + curve.p1.distance(curve.p2)
                        + curve.p2.distance(curve.p3);
                    let steps = (span / step_pt.max(0.01)).ceil().clamp(1.0, 4096.0) as usize;
                    for step in 0..steps {
                        let p = curve.eval(step as f64 / steps as f64);
                        out.push(Point::new(p.x, p.y));
                    }
                }
                out
            })
            .collect()
    }
}

/// Generates the cut around `image`, printed at `size`.
///
/// Fully transparent padding is trimmed first, the art is resampled to
/// the normalized processing resolution, cleaned up, traced, fitted with curves and offset at
/// full size. Coordinates in the result are relative to the untrimmed image.
pub fn generate_cut(
    image: &Image,
    size: ArtworkSize,
    settings: &CutSettings,
) -> Result<Cut, CutError> {
    if !(size.width_pt.is_finite()
        && size.height_pt.is_finite()
        && size.width_pt > 0.0
        && size.height_pt > 0.0)
    {
        return Err(CutError::InvalidSize);
    }
    // Points per image pixel, before trimming.
    let pt_per_px_x = size.width_pt / f64::from(image.width);
    let pt_per_px_y = size.height_pt / f64::from(image.height);
    let (x, y, width, height) = image.visible_box().ok_or(CutError::NothingToTrace)?;
    let trimmed;
    let image = if (width, height) == (image.width, image.height) {
        image
    } else {
        trimmed = image.crop(x, y, width, height)?;
        &trimmed
    };
    let width_pt = f64::from(width) * pt_per_px_x;
    let height_pt = f64::from(height) * pt_per_px_y;
    let scale = pipeline::processing_scale(width_pt, height_pt);
    let ppi = pipeline::render_ppi(PROCESSING_PPI * scale, width_pt, height_pt);
    let target_width = (width_pt * ppi / POINTS_PER_INCH).round().max(1.0) as u32;
    let target_height = (height_pt * ppi / POINTS_PER_INCH).round().max(1.0) as u32;
    let background = image.background_color(settings)?;
    let resampled;
    let render = if (target_width, target_height) == (image.width, image.height) {
        image
    } else {
        let source = image::RgbaImage::from_raw(image.width, image.height, image.rgba.clone())
            .ok_or_else(|| CutError::InvalidImage("RGBA length mismatch".into()))?;
        let resized = image::imageops::resize(
            &source,
            target_width,
            target_height,
            image::imageops::FilterType::Triangle,
        );
        resampled = Image::new(target_width, target_height, resized.into_raw())?;
        &resampled
    };
    let mask = narwhal_trace_core::BinaryMask::new(
        render.width as usize,
        render.height as usize,
        pipeline::foreground_mask(
            &render.rgba,
            render.width as usize,
            render.height as usize,
            background,
            settings.background_tolerance_percent,
        )?,
    )?;
    let left = f64::from(x) * pt_per_px_x;
    let top = f64::from(y) * pt_per_px_y;
    let bounds = Rect::new(left, top, left + width_pt, top + height_pt);
    let base = pipeline::trace_base(&mask, bounds, ppi, scale, settings)?;
    let path = pipeline::apply_vector_offset(&base.path, base.vector_offset_pt)?;
    Ok(Cut::from_path(&path, size, base.unified_join_pt))
}

/// A copy of `image` with its background made transparent, as an opened image
/// is cleaned before clipping it to the cut.
pub fn remove_background(image: &Image, settings: &CutSettings) -> Result<Image, CutError> {
    let background = image.background_color(settings)?;
    let mask = pipeline::foreground_mask(
        &image.rgba,
        image.width as usize,
        image.height as usize,
        background,
        settings.background_tolerance_percent,
    )?;
    let mut rgba = image.rgba.clone();
    for (pixel, keep) in rgba.chunks_exact_mut(4).zip(mask) {
        if keep == 0 {
            pixel.fill(0);
        }
    }
    Image::new(image.width, image.height, rgba)
}

#[derive(Clone, Debug, PartialEq)]
pub enum CutError {
    InvalidImage(String),
    InvalidSize,
    NothingToTrace,
    NoUsablePath,
    OffsetRemovedEverything,
    Mask(narwhal_trace_core::MaskError),
    Contour(narwhal_trace_core::ContourError),
}

impl fmt::Display for CutError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidImage(reason) => write!(formatter, "invalid image: {reason}"),
            Self::InvalidSize => formatter.write_str("the artwork size must be positive"),
            Self::NothingToTrace => formatter.write_str("no opaque artwork was found to trace"),
            Self::NoUsablePath => {
                formatter.write_str("the traced silhouette did not contain a usable path")
            }
            Self::OffsetRemovedEverything => {
                formatter.write_str("the full-size vector offset produced no cut path")
            }
            Self::Mask(error) => error.fmt(formatter),
            Self::Contour(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for CutError {}

impl From<narwhal_trace_core::MaskError> for CutError {
    fn from(error: narwhal_trace_core::MaskError) -> Self {
        Self::Mask(error)
    }
}

impl From<narwhal_trace_core::ContourError> for CutError {
    fn from(error: narwhal_trace_core::ContourError) -> Self {
        Self::Contour(error)
    }
}

impl From<CutError> for String {
    fn from(error: CutError) -> Self {
        error.to_string()
    }
}

#[cfg(test)]
mod tests;
