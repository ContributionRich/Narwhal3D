//! The individual steps of cut generation, in document points (1/72 inch, y down).
//!
//! Coco Production calls these directly with artwork it renders itself; [`crate::generate_cut`]
//! chains them for a plain image. Both therefore produce the same cut from the same mask.

use crate::{CutError, CutSettings};
use coco_cut::{
    BinaryMask, CleanupSettings, OffsetSettings, Point as CutPoint, SimplifySettings, cleanup_mask,
    connected_components, dilate, drop_small_pieces, erode, offset_mask, pad_mask,
    simplify_closed_loop, union_masks,
};
use std::collections::VecDeque;
use vectorcraft_geom::{Anchor, AnchorKind, CubicBez, ParamCurve, PathData, Point, Rect, SubPath};

pub const POINTS_PER_INCH: f64 = 72.0;
/// Artwork is processed as if its longest side were this long, so small and large art trace
/// with the same detail. Offsets are then applied at the real size.
pub const PROCESSING_LONGEST_SIDE_PT: f64 = 5.0 * POINTS_PER_INCH;
/// Resolution of the normalized artwork.
pub const PROCESSING_PPI: f64 = 300.0;
const MAX_CUT_PIXELS: f64 = 32.0 * 1024.0 * 1024.0;

/// Scale from real size to the normalized processing size.
#[must_use]
pub fn processing_scale(width_pt: f64, height_pt: f64) -> f64 {
    let longest = width_pt.max(height_pt);
    if longest.is_finite() && longest > 1.0e-9 {
        PROCESSING_LONGEST_SIDE_PT / longest
    } else {
        1.0
    }
}

/// `desired` render resolution, capped so the render stays within 32 megapixels.
#[must_use]
pub fn render_ppi(desired: f64, width_pt: f64, height_pt: f64) -> f64 {
    let pixel_cap = POINTS_PER_INCH * (MAX_CUT_PIXELS / (width_pt * height_pt).max(1.0)).sqrt();
    desired.min(pixel_cap).max(1.0)
}

fn check_rgba(rgba: &[u8], width: usize, height: usize) -> Result<(), CutError> {
    let expected = width
        .checked_mul(height)
        .and_then(|pixels| pixels.checked_mul(4))
        .ok_or(CutError::InvalidImage("image dimensions overflow".into()))?;
    if rgba.len() != expected {
        return Err(CutError::InvalidImage(format!(
            "expected {expected} RGBA bytes for {width}x{height}, got {}",
            rgba.len()
        )));
    }
    Ok(())
}

/// The solid colour around an image's border, if the border is mostly opaque.
/// Coco Production uses this to pick a background colour when an image is opened.
pub fn detect_background(
    rgba: &[u8],
    width: usize,
    height: usize,
) -> Result<Option<[u8; 3]>, CutError> {
    check_rgba(rgba, width, height)?;
    if width == 0 || height == 0 {
        return Ok(None);
    }
    let mut border = Vec::with_capacity((width + height) * 2);
    for x in 0..width {
        border.push(x);
        if height > 1 {
            border.push((height - 1) * width + x);
        }
    }
    for y in 1..height.saturating_sub(1) {
        border.push(y * width);
        if width > 1 {
            border.push(y * width + width - 1);
        }
    }
    let transparent = border
        .iter()
        .filter(|index| rgba[index.saturating_mul(4) + 3] < 128)
        .count();
    if transparent * 50 >= border.len().max(1) {
        return Ok(None);
    }

    let mut bins = std::collections::BTreeMap::<u16, (usize, [u64; 3])>::new();
    for index in border {
        let offset = index * 4;
        let Some(pixel) = rgba.get(offset..offset + 4) else {
            continue;
        };
        if pixel[3] < 128 {
            continue;
        }
        let key = (u16::from(pixel[0] >> 4) << 8)
            | (u16::from(pixel[1] >> 4) << 4)
            | u16::from(pixel[2] >> 4);
        let entry = bins.entry(key).or_insert((0, [0; 3]));
        entry.0 += 1;
        for (sum, channel) in entry.1.iter_mut().zip(pixel) {
            *sum += u64::from(*channel);
        }
    }
    let Some((count, sums)) = bins.values().max_by_key(|(count, _)| *count) else {
        return Ok(None);
    };
    let divisor = (*count).max(1) as u64;
    Ok(Some([
        (sums[0] / divisor) as u8,
        (sums[1] / divisor) as u8,
        (sums[2] / divisor) as u8,
    ]))
}

/// One byte per pixel: 1 for artwork, 0 for background.
///
/// Background is transparency (alpha below 128), pixels within `tolerance_percent` of
/// `background` when given, and, when the border is at least 90% opaque, the region of
/// border-like colour connected to the image edge.
pub fn foreground_mask(
    rgba: &[u8],
    width: usize,
    height: usize,
    background: Option<[u8; 3]>,
    tolerance_percent: f64,
) -> Result<Vec<u8>, CutError> {
    check_rgba(rgba, width, height)?;
    let use_background_color = background.is_some();
    let background = background.unwrap_or([255, 255, 255]);
    let tolerance = (255.0 * tolerance_percent.clamp(0.0, 60.0) / 100.0).round() as i16;
    let matches_color = |pixel: &[u8]| {
        (i16::from(pixel[0]) - i16::from(background[0])).abs() <= tolerance
            && (i16::from(pixel[1]) - i16::from(background[1])).abs() <= tolerance
            && (i16::from(pixel[2]) - i16::from(background[2])).abs() <= tolerance
    };
    let mut mask: Vec<u8> = rgba
        .chunks_exact(4)
        .map(|pixel| {
            let empty = pixel[3] < 128 || (use_background_color && matches_color(pixel));
            u8::from(!empty)
        })
        .collect();
    if width == 0 || height == 0 {
        return Ok(mask);
    }

    let mut border_count = 0usize;
    let mut opaque_count = 0usize;
    let mut sums = [0_u64; 3];
    let mut sample = |index: usize| {
        border_count += 1;
        let pixel = &rgba[index * 4..index * 4 + 4];
        if pixel[3] >= 128 {
            opaque_count += 1;
            for channel in 0..3 {
                sums[channel] += u64::from(pixel[channel]);
            }
        }
    };
    for x in 0..width {
        sample(x);
        if height > 1 {
            sample((height - 1) * width + x);
        }
    }
    if height > 2 {
        for y in 1..height - 1 {
            sample(y * width);
            if width > 1 {
                sample(y * width + width - 1);
            }
        }
    }
    if opaque_count * 10 <= border_count * 9 {
        return Ok(mask);
    }

    let average = [
        sums[0] as f64 / opaque_count as f64,
        sums[1] as f64 / opaque_count as f64,
        sums[2] as f64 / opaque_count as f64,
    ];
    let like_border = |index: usize| {
        let pixel = &rgba[index * 4..index * 4 + 4];
        pixel[3] >= 128
            && (f64::from(pixel[0]) - average[0]).abs() < 40.0
            && (f64::from(pixel[1]) - average[1]).abs() < 40.0
            && (f64::from(pixel[2]) - average[2]).abs() < 40.0
    };
    let mut visited = vec![false; width * height];
    let mut queue = VecDeque::new();
    let push = |index: usize, visited: &mut [bool], queue: &mut VecDeque<usize>| {
        if !visited[index] && like_border(index) {
            visited[index] = true;
            queue.push_back(index);
        }
    };
    for x in 0..width {
        push(x, &mut visited, &mut queue);
        push((height - 1) * width + x, &mut visited, &mut queue);
    }
    for y in 0..height {
        push(y * width, &mut visited, &mut queue);
        push(y * width + width - 1, &mut visited, &mut queue);
    }
    while let Some(index) = queue.pop_front() {
        mask[index] = 0;
        let x = index % width;
        let y = index / width;
        if x > 0 {
            push(index - 1, &mut visited, &mut queue);
        }
        if x + 1 < width {
            push(index + 1, &mut visited, &mut queue);
        }
        if y > 0 {
            push(index - width, &mut visited, &mut queue);
        }
        if y + 1 < height {
            push(index + width, &mut visited, &mut queue);
        }
    }
    Ok(mask)
}

/// The traced cut before any full-size vector offset.
#[derive(Clone, Debug)]
pub struct BaseCut {
    pub path: PathData,
    /// Join radius in points when separate islands were merged into one envelope.
    pub unified_join_pt: Option<f64>,
    /// Offset still to apply with [`apply_vector_offset`]: the requested offset when the art
    /// was normalized to the processing size, otherwise zero (it was applied to the mask).
    pub vector_offset_pt: f64,
}

/// Traces the cut around `source_mask`, rendered at `ppi` over `bounds` (points), after
/// normalizing the art by `processing_scale` (see [`processing_scale`]).
pub fn trace_base(
    source_mask: &BinaryMask,
    bounds: Rect,
    ppi: f64,
    processing_scale: f64,
    settings: &CutSettings,
) -> Result<BaseCut, CutError> {
    let source_width = source_mask.width();
    let source_height = source_mask.height();
    let pixels_per_point = ppi / POINTS_PER_INCH;
    // A tightly cropped render is padded by the full offset reach plus six points so positive
    // offsets can never turn into a rectangle at the image edge.
    let safety_pad_pt = 6.0 / processing_scale;
    let pad_px = ((settings.offset_pt.abs() + safety_pad_pt) * pixels_per_point).ceil() as usize;
    let padded = pad_mask(source_mask, pad_px)?;
    let cleaned = cleanup_mask(
        &padded,
        pixels_per_point,
        CleanupSettings {
            minimum_piece_area_pt2: 0.02 * POINTS_PER_INCH * POINTS_PER_INCH,
            minimum_hole_area_pt2: settings.ignore_holes_under_pt2,
            keep_inner_cuts: settings.keep_inner_cuts,
            ..CleanupSettings::default()
        },
    );
    let meaningful_components = connected_components(&cleaned.mask, true).component_count();
    let unified = if meaningful_components > 1 {
        unified_island_envelope(
            &padded,
            pixels_per_point,
            settings.keep_inner_cuts,
            settings.outline_smoothness,
        )?
    } else {
        None
    };
    let unified_join_pt = unified
        .as_ref()
        .map(|(_, radius)| radius / pixels_per_point.max(0.01));
    let envelope = if let Some((mask, _)) = unified {
        cleanup_mask(
            &mask,
            pixels_per_point,
            CleanupSettings {
                minimum_piece_area_pt2: 0.0,
                minimum_hole_area_pt2: settings.ignore_holes_under_pt2,
                keep_inner_cuts: settings.keep_inner_cuts,
                ..CleanupSettings::default()
            },
        )
        .mask
    } else {
        cleaned.mask
    };
    let normalized = (processing_scale - 1.0).abs() > 1.0e-9;
    let raster_offset_pt = if normalized { 0.0 } else { settings.offset_pt };
    let mut processed = offset_mask(
        &envelope,
        pixels_per_point,
        OffsetSettings {
            offset_pt: raster_offset_pt,
            minimum_hole_area_pt2: settings.ignore_holes_under_pt2,
            keep_inner_cuts: settings.keep_inner_cuts,
            ..OffsetSettings::default()
        },
        &[],
    );
    let unified_smoothness = settings.outline_smoothness.clamp(0.0, 1.0);
    let fit_tolerance_pt = if unified_join_pt.is_some() {
        settings.tolerance_pt * (1.0 + 2.0 * unified_smoothness)
    } else {
        settings.tolerance_pt
    };
    if unified_join_pt.is_some() && settings.offset_pt >= 0.0 {
        // Curve fitting may deviate inward by its tolerance. Expanding by the same amount
        // keeps a smoother fitted envelope outside the retained art.
        processed = dilate(&processed, fit_tolerance_pt * pixels_per_point);
    }
    let traced = vectorcraft_trace::trace_mask(
        &processed
            .pixels()
            .iter()
            .map(|&pixel| pixel != 0)
            .collect::<Vec<_>>(),
        processed.width(),
        processed.height(),
    );
    if traced.is_empty() {
        return Err(CutError::NothingToTrace);
    }

    let sx = bounds.width() / source_width as f64;
    let sy = bounds.height() / source_height as f64;
    let origin_x = bounds.x0 - pad_px as f64 * sx;
    let origin_y = bounds.y0 - pad_px as f64 * sy;
    let fit_settings = SimplifySettings {
        tolerance_pt: fit_tolerance_pt,
        corner_angle_deg: 60.0,
        round_sharp_points_pt: 0.0,
        spike_angle_deg: 75.0,
    };
    let mut subpaths = Vec::new();
    for component in traced {
        subpaths.push(fit_loop(
            &component.outer.pts,
            sx,
            sy,
            origin_x,
            origin_y,
            processing_scale,
            fit_settings,
        )?);
        if settings.keep_inner_cuts {
            for hole in component.holes {
                let area_pt2 = hole.area2.unsigned_abs() as f64 * 0.5 * sx * sy;
                if area_pt2 >= settings.ignore_holes_under_pt2 {
                    subpaths.push(fit_loop(
                        &hole.pts,
                        sx,
                        sy,
                        origin_x,
                        origin_y,
                        processing_scale,
                        fit_settings,
                    )?);
                }
            }
        }
    }
    let path = PathData::new(subpaths);
    if path.is_empty() {
        return Err(CutError::NoUsablePath);
    }
    Ok(BaseCut {
        path,
        unified_join_pt,
        vector_offset_pt: if normalized && settings.offset_pt.abs() > 1.0e-9 {
            settings.offset_pt
        } else {
            0.0
        },
    })
}

/// Offsets the cut at full size with round joins, as Coco Production's Offset Path does.
pub fn apply_vector_offset(path: &PathData, offset_pt: f64) -> Result<PathData, CutError> {
    if offset_pt.abs() <= 1.0e-9 {
        return Ok(path.clone());
    }
    let offset =
        vectorcraft_pathops::offset_path(path, offset_pt, vectorcraft_pathops::Join::Round, 4.0);
    if offset.is_empty() {
        return Err(CutError::OffsetRemovedEverything);
    }
    Ok(offset)
}

/// Every pixel corner along a traced loop. `trace_mask` returns only the corners where the
/// boundary turns, so a straight edge arrives as two points however long it is.
fn pixel_steps(corners: &[(i32, i32)]) -> Vec<(i32, i32)> {
    let mut dense = Vec::with_capacity(corners.len() * 2);
    for (index, &(x0, y0)) in corners.iter().enumerate() {
        let (x1, y1) = corners[(index + 1) % corners.len()];
        let steps = (x1 - x0).abs().max((y1 - y0).abs()).max(1);
        for step in 0..steps {
            // Boundary edges are axis-aligned; integer division keeps any other edge exact
            // at its ends and evenly split between them.
            dense.push((x0 + (x1 - x0) * step / steps, y0 + (y1 - y0) * step / steps));
        }
    }
    dense
}

/// Fits smooth curves to one traced pixel-corner loop and maps it to points.
///
/// The curve fitter needs a sample at every pixel step: with only the corners of a long
/// straight edge it can neither measure how far a curve strays along that edge nor estimate
/// a local tangent next to it, and the fitted curve bulges away from the art there.
pub fn fit_loop(
    pixels: &[(i32, i32)],
    sx: f64,
    sy: f64,
    origin_x: f64,
    origin_y: f64,
    processing_scale: f64,
    settings: SimplifySettings,
) -> Result<SubPath, CutError> {
    let dense: Vec<CutPoint> = pixel_steps(pixels)
        .into_iter()
        .map(|(x, y)| {
            CutPoint::new(
                f64::from(x) * sx * processing_scale,
                f64::from(y) * sy * processing_scale,
            )
        })
        .collect();
    let nodes = simplify_closed_loop(&dense, settings)?;
    let map = |point: CutPoint| {
        Point::new(
            origin_x + point.x / processing_scale,
            origin_y + point.y / processing_scale,
        )
    };
    let anchors: Vec<Anchor> = nodes
        .into_iter()
        .map(|node| Anchor {
            p: map(node.anchor),
            h_in: map(node.in_handle),
            h_out: map(node.out_handle),
            kind: if node.corner {
                AnchorKind::Corner
            } else {
                AnchorKind::Smooth
            },
        })
        .collect();
    Ok(repair_subpath_self_intersections(SubPath::new(
        anchors, true,
    )))
}

/// True when segments `ab` and `cd` cross at a point inside both.
#[must_use]
pub fn proper_segment_intersection(a: Point, b: Point, c: Point, d: Point) -> bool {
    let cross = |p: Point, q: Point, r: Point| (q - p).cross(r - p);
    let ab_c = cross(a, b, c);
    let ab_d = cross(a, b, d);
    let cd_a = cross(c, d, a);
    let cd_b = cross(c, d, b);
    let epsilon = 1.0e-8;
    ab_c * ab_d < -epsilon && cd_a * cd_b < -epsilon
}

fn crossing_curve_indices(subpath: &SubPath) -> Option<(usize, usize)> {
    let count = subpath.anchors.len();
    if count < 2 {
        return None;
    }
    let mut segments = Vec::new();
    for curve_index in 0..count {
        let current = subpath.anchors[curve_index];
        let next = subpath.anchors[(curve_index + 1) % count];
        let curve = CubicBez::new(current.p, current.h_out, next.h_in, next.p);
        let span = current.p.distance(current.h_out)
            + current.h_out.distance(next.h_in)
            + next.h_in.distance(next.p);
        let steps = (span / 0.5).ceil().clamp(4.0, 96.0) as usize;
        let mut previous = curve.eval(0.0);
        for step in 1..=steps {
            let point = curve.eval(step as f64 / steps as f64);
            segments.push((previous, point, curve_index));
            previous = point;
        }
    }
    for first in 0..segments.len() {
        let (a, b, first_curve) = segments[first];
        for second in (first + 1)..segments.len() {
            let second_next_to_first =
                second == first + 1 || (first == 0 && second + 1 == segments.len());
            if second_next_to_first {
                continue;
            }
            let (c, d, second_curve) = segments[second];
            if proper_segment_intersection(a, b, c, d) {
                return Some((first_curve, second_curve));
            }
        }
    }
    None
}

/// Shortens the handles of crossing curves until the loop no longer crosses itself.
#[must_use]
pub fn repair_subpath_self_intersections(mut subpath: SubPath) -> SubPath {
    for _ in 0..12 {
        let Some((first, second)) = crossing_curve_indices(&subpath) else {
            return subpath;
        };
        for curve_index in [first, second] {
            let next = (curve_index + 1) % subpath.anchors.len();
            let start = subpath.anchors[curve_index];
            let end = subpath.anchors[next];
            subpath.anchors[curve_index].h_out = start.p + (start.h_out - start.p) * 0.5;
            subpath.anchors[next].h_in = end.p + (end.h_in - end.p) * 0.5;
        }
    }
    subpath
}

fn restore_enclosed_holes(
    envelope: &BinaryMask,
    source: &BinaryMask,
) -> Result<BinaryMask, CutError> {
    let background = connected_components(source, false);
    Ok(BinaryMask::new(
        envelope.width(),
        envelope.height(),
        envelope
            .pixels()
            .iter()
            .enumerate()
            .map(|(index, &pixel)| {
                let component = background.labels[index];
                let enclosed = component != 0 && !background.touches_border[component];
                u8::from(pixel != 0 && !enclosed)
            })
            .collect(),
    )?)
}

/// Joins separate islands into one envelope with the smallest bridge that connects them
/// (at most 0.15 inch), or `None` when they are one piece or too far apart.
/// Returns the envelope and the join radius in pixels.
pub fn unified_island_envelope(
    mask: &BinaryMask,
    pixels_per_point: f64,
    keep_inner_cuts: bool,
    smoothness: f64,
) -> Result<Option<(BinaryMask, f64)>, CutError> {
    let base = drop_small_pieces(mask, 4.0).mask;
    if connected_components(&base, true).component_count() <= 1 {
        return Ok(None);
    }
    let max_radius = 0.15 * POINTS_PER_INCH * pixels_per_point;
    let expanded_max = dilate(&base, max_radius);
    if connected_components(&expanded_max, true).component_count() > 1 {
        return Ok(None);
    }

    let mut low = 0.0;
    let mut high = max_radius;
    for _ in 0..9 {
        let middle = (low + high) / 2.0;
        if connected_components(&dilate(&base, middle), true).component_count() <= 1 {
            high = middle;
        } else {
            low = middle;
        }
    }
    let join_radius = high;
    let expanded = dilate(&base, join_radius);

    let mut erosion_low = 0.0;
    let mut erosion_high = join_radius;
    let mut best = expanded.clone();
    for _ in 0..9 {
        let middle = (erosion_low + erosion_high) / 2.0;
        let contracted = erode(&expanded, middle);
        let candidate = union_masks(&contracted, &base)?;
        if connected_components(&candidate, true).component_count() <= 1 {
            erosion_low = middle;
            best = candidate;
        } else {
            erosion_high = middle;
        }
    }
    // The tightest connected contraction can leave a nearly one-pixel bridge aimed at the
    // closest neighbouring island. A later physical offset turns that bridge into a visible
    // jet. Retain part of the rounded join so its transition stays stable.
    let smoothness = smoothness.clamp(0.0, 1.0);
    let retained_join = (join_radius * 0.4).min(8.0) * smoothness;
    if retained_join > 0.0 {
        let erosion = (erosion_low - retained_join).max(0.0);
        best = union_masks(&erode(&expanded, erosion), &base)?;
    }
    if keep_inner_cuts {
        best = restore_enclosed_holes(&best, &base)?;
    }
    Ok(Some((best, join_radius)))
}
