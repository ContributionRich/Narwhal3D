//! Platform-neutral binary-mask processing for Narwhal 3D picture tracing.
//!
//! Distances are measured between pixel centers. Morphological operations use
//! exact squared Euclidean distances and intentionally treat only in-bounds
//! background pixels as background, matching the proven Illustrator pipeline.
#![forbid(unsafe_code)]

pub mod contour;

pub use contour::{ContourError, CubicNode, Point, SimplifySettings, simplify_closed_loop};

use std::fmt;

const EDT_INFINITY: f64 = 1.0e20;

/// A row-major binary image. Input bytes are normalized to zero or one.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinaryMask {
    width: usize,
    height: usize,
    pixels: Vec<u8>,
}

impl BinaryMask {
    /// Creates a mask from row-major bytes, where any nonzero byte is material.
    pub fn new(width: usize, height: usize, pixels: Vec<u8>) -> Result<Self, MaskError> {
        let expected = width
            .checked_mul(height)
            .ok_or(MaskError::DimensionsOverflow { width, height })?;
        if pixels.len() != expected {
            return Err(MaskError::LengthMismatch {
                expected,
                actual: pixels.len(),
            });
        }
        Ok(Self {
            width,
            height,
            pixels: pixels
                .into_iter()
                .map(|pixel| u8::from(pixel != 0))
                .collect(),
        })
    }

    /// Creates a uniformly empty or filled mask.
    pub fn filled(width: usize, height: usize, value: bool) -> Result<Self, MaskError> {
        let len = width
            .checked_mul(height)
            .ok_or(MaskError::DimensionsOverflow { width, height })?;
        Ok(Self {
            width,
            height,
            pixels: vec![u8::from(value); len],
        })
    }

    #[must_use]
    pub const fn width(&self) -> usize {
        self.width
    }

    #[must_use]
    pub const fn height(&self) -> usize {
        self.height
    }

    #[must_use]
    pub fn pixels(&self) -> &[u8] {
        &self.pixels
    }

    #[must_use]
    pub fn into_pixels(self) -> Vec<u8> {
        self.pixels
    }

    #[must_use]
    pub fn get(&self, x: usize, y: usize) -> Option<bool> {
        self.index(x, y).map(|index| self.pixels[index] != 0)
    }

    pub fn set(&mut self, x: usize, y: usize, value: bool) -> Result<(), MaskError> {
        let index = self
            .index(x, y)
            .ok_or(MaskError::PixelOutOfBounds { x, y })?;
        self.pixels[index] = u8::from(value);
        Ok(())
    }

    fn index(&self, x: usize, y: usize) -> Option<usize> {
        (x < self.width && y < self.height).then_some(y * self.width + x)
    }

    fn from_normalized(width: usize, height: usize, pixels: Vec<u8>) -> Self {
        Self {
            width,
            height,
            pixels,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MaskError {
    DimensionsOverflow { width: usize, height: usize },
    LengthMismatch { expected: usize, actual: usize },
    PixelOutOfBounds { x: usize, y: usize },
}

impl fmt::Display for MaskError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::DimensionsOverflow { width, height } => {
                write!(formatter, "mask dimensions {width}x{height} overflow")
            }
            Self::LengthMismatch { expected, actual } => {
                write!(
                    formatter,
                    "mask needs {expected} pixels, but received {actual}"
                )
            }
            Self::PixelOutOfBounds { x, y } => {
                write!(formatter, "pixel ({x}, {y}) is outside the mask")
            }
        }
    }
}

impl std::error::Error for MaskError {}

/// Copies `mask` into a larger empty mask with `pad` background pixels on every side.
pub fn pad_mask(mask: &BinaryMask, pad: usize) -> Result<BinaryMask, MaskError> {
    let overflow = MaskError::DimensionsOverflow {
        width: mask.width(),
        height: mask.height(),
    };
    let width = mask
        .width()
        .checked_add(pad.saturating_mul(2))
        .ok_or(overflow)?;
    let height = mask
        .height()
        .checked_add(pad.saturating_mul(2))
        .ok_or(overflow)?;
    let len = width.checked_mul(height).ok_or(overflow)?;
    let mut pixels = vec![0_u8; len];
    for y in 0..mask.height() {
        let source_start = y * mask.width();
        let source_end = source_start + mask.width();
        let target_start = (y + pad) * width + pad;
        let target_end = target_start + mask.width();
        if let (Some(source), Some(target)) = (
            mask.pixels().get(source_start..source_end),
            pixels.get_mut(target_start..target_end),
        ) {
            target.copy_from_slice(source);
        }
    }
    Ok(BinaryMask::from_normalized(width, height, pixels))
}

/// Pixel-wise union of two masks of the same size.
pub fn union_masks(left: &BinaryMask, right: &BinaryMask) -> Result<BinaryMask, MaskError> {
    if left.width() != right.width() || left.height() != right.height() {
        return Err(MaskError::LengthMismatch {
            expected: left.pixels().len(),
            actual: right.pixels().len(),
        });
    }
    Ok(BinaryMask::from_normalized(
        left.width(),
        left.height(),
        left.pixels()
            .iter()
            .zip(right.pixels())
            .map(|(&a, &b)| u8::from(a != 0 || b != 0))
            .collect(),
    ))
}

/// Squared distance from every pixel to its nearest material pixel.
/// Empty masks contain the same finite sentinel (`1e20`) used by `cut.js`.
#[must_use]
pub fn squared_euclidean_distance_transform(mask: &BinaryMask) -> Vec<f64> {
    edt_2d(mask, false).distances
}

#[derive(Clone, Debug, PartialEq)]
pub struct FeatureTransform {
    pub squared_distances: Vec<f64>,
    pub nearest_x: Vec<usize>,
    pub nearest_y: Vec<usize>,
}

/// Exact Euclidean distance transform plus nearest-feature coordinates.
#[must_use]
pub fn euclidean_feature_transform(mask: &BinaryMask) -> FeatureTransform {
    let result = edt_2d(mask, true);
    FeatureTransform {
        squared_distances: result.distances,
        nearest_x: result.nearest_x,
        nearest_y: result.nearest_y,
    }
}

struct EdtResult {
    distances: Vec<f64>,
    nearest_x: Vec<usize>,
    nearest_y: Vec<usize>,
}

fn edt_1d(values: &[f64], sources: &[usize], distances: &mut [f64], winners: &mut [usize]) {
    let n = values.len();
    if n == 0 {
        return;
    }
    let mut vertices = vec![0_usize; n];
    let mut boundaries = vec![0.0_f64; n + 1];
    let mut k = 0_usize;
    vertices[0] = 0;
    boundaries[0] = -EDT_INFINITY;
    boundaries[1] = EDT_INFINITY;

    for q in 1..n {
        let qf = q as f64;
        let mut vertex = vertices[k];
        let mut vf = vertex as f64;
        let mut intersection =
            ((values[q] + qf * qf) - (values[vertex] + vf * vf)) / (2.0 * (qf - vf));
        while k > 0 && intersection <= boundaries[k] {
            k -= 1;
            vertex = vertices[k];
            vf = vertex as f64;
            intersection = ((values[q] + qf * qf) - (values[vertex] + vf * vf)) / (2.0 * (qf - vf));
        }
        k += 1;
        vertices[k] = q;
        boundaries[k] = intersection;
        boundaries[k + 1] = EDT_INFINITY;
    }

    k = 0;
    for q in 0..n {
        while boundaries[k + 1] < q as f64 {
            k += 1;
        }
        let vertex = vertices[k];
        let delta = q as f64 - vertex as f64;
        distances[q] = delta * delta + values[vertex];
        winners[q] = sources[vertex];
    }
}

fn edt_2d(mask: &BinaryMask, features: bool) -> EdtResult {
    let width = mask.width;
    let height = mask.height;
    let len = mask.pixels.len();
    let mut distances: Vec<f64> = mask
        .pixels
        .iter()
        .map(|&pixel| if pixel != 0 { 0.0 } else { EDT_INFINITY })
        .collect();
    if len == 0 {
        return EdtResult {
            distances,
            nearest_x: Vec::new(),
            nearest_y: Vec::new(),
        };
    }

    let scratch_len = width.max(height);
    let mut values = vec![0.0; scratch_len];
    let mut transformed = vec![0.0; scratch_len];
    let mut sources = vec![0_usize; scratch_len];
    let mut winners = vec![0_usize; scratch_len];
    let mut nearest_y = vec![0_usize; len];

    for x in 0..width {
        for y in 0..height {
            values[y] = distances[y * width + x];
            sources[y] = y;
        }
        edt_1d(
            &values[..height],
            &sources[..height],
            &mut transformed[..height],
            &mut winners[..height],
        );
        for y in 0..height {
            let index = y * width + x;
            distances[index] = transformed[y];
            if features {
                nearest_y[index] = winners[y];
            }
        }
    }

    let mut nearest_x = vec![0_usize; len];
    let mut final_y = vec![0_usize; len];
    for y in 0..height {
        for x in 0..width {
            values[x] = distances[y * width + x];
            sources[x] = x;
        }
        edt_1d(
            &values[..width],
            &sources[..width],
            &mut transformed[..width],
            &mut winners[..width],
        );
        for x in 0..width {
            let index = y * width + x;
            let winning_x = winners[x];
            distances[index] = transformed[x];
            if features {
                nearest_x[index] = winning_x;
                final_y[index] = nearest_y[y * width + winning_x];
            }
        }
    }

    EdtResult {
        distances,
        nearest_x,
        nearest_y: final_y,
    }
}

/// Grows material by `radius` pixels using exact Euclidean distance.
#[must_use]
pub fn dilate(mask: &BinaryMask, radius: f64) -> BinaryMask {
    if !radius.is_finite() || radius <= 0.0 {
        return mask.clone();
    }
    let distances = squared_euclidean_distance_transform(mask);
    let radius_squared = radius * radius;
    let pixels = distances
        .into_iter()
        .map(|distance| u8::from(distance <= radius_squared))
        .collect();
    BinaryMask::from_normalized(mask.width, mask.height, pixels)
}

/// Shrinks material by `radius` pixels using exact Euclidean distance.
/// Pixels beyond the image boundary are not implicitly background.
#[must_use]
pub fn erode(mask: &BinaryMask, radius: f64) -> BinaryMask {
    if !radius.is_finite() || radius <= 0.0 {
        return mask.clone();
    }
    let inverse = BinaryMask::from_normalized(
        mask.width,
        mask.height,
        mask.pixels
            .iter()
            .map(|&pixel| u8::from(pixel == 0))
            .collect(),
    );
    let distances = squared_euclidean_distance_transform(&inverse);
    let radius_squared = radius * radius;
    let pixels = mask
        .pixels
        .iter()
        .zip(distances)
        .map(|(&pixel, distance)| u8::from(pixel != 0 && distance > radius_squared))
        .collect();
    BinaryMask::from_normalized(mask.width, mask.height, pixels)
}

/// Four-connected component labels for either material or background pixels.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ComponentLabels {
    pub labels: Vec<usize>,
    /// Component zero is the unlabeled/non-target sentinel.
    pub sizes: Vec<usize>,
    /// Whether each component touches an image edge.
    pub touches_border: Vec<bool>,
}

impl ComponentLabels {
    #[must_use]
    pub fn component_count(&self) -> usize {
        self.sizes.len().saturating_sub(1)
    }
}

#[must_use]
pub fn connected_components(mask: &BinaryMask, value: bool) -> ComponentLabels {
    let target = u8::from(value);
    let len = mask.pixels.len();
    let mut labels = vec![0_usize; len];
    let mut sizes = vec![0_usize];
    let mut touches_border = vec![false];
    let mut stack = Vec::new();

    for seed in 0..len {
        if mask.pixels[seed] != target || labels[seed] != 0 {
            continue;
        }
        let current = sizes.len();
        sizes.push(0);
        touches_border.push(false);
        labels[seed] = current;
        stack.push(seed);

        while let Some(pixel) = stack.pop() {
            let x = pixel % mask.width;
            let y = pixel / mask.width;
            sizes[current] += 1;
            if x == 0 || y == 0 || x + 1 == mask.width || y + 1 == mask.height {
                touches_border[current] = true;
            }
            if x > 0 {
                push_unlabeled(pixel - 1, target, current, mask, &mut labels, &mut stack);
            }
            if x + 1 < mask.width {
                push_unlabeled(pixel + 1, target, current, mask, &mut labels, &mut stack);
            }
            if y > 0 {
                push_unlabeled(
                    pixel - mask.width,
                    target,
                    current,
                    mask,
                    &mut labels,
                    &mut stack,
                );
            }
            if y + 1 < mask.height {
                push_unlabeled(
                    pixel + mask.width,
                    target,
                    current,
                    mask,
                    &mut labels,
                    &mut stack,
                );
            }
        }
    }

    ComponentLabels {
        labels,
        sizes,
        touches_border,
    }
}

fn push_unlabeled(
    index: usize,
    target: u8,
    component: usize,
    mask: &BinaryMask,
    labels: &mut [usize],
    stack: &mut Vec<usize>,
) {
    if labels[index] == 0 && mask.pixels[index] == target {
        labels[index] = component;
        stack.push(index);
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MaskChange {
    pub mask: BinaryMask,
    pub changed_components: usize,
}

/// Removes material components whose pixel count is strictly below `minimum_pixels`.
#[must_use]
pub fn drop_small_pieces(mask: &BinaryMask, minimum_pixels: f64) -> MaskChange {
    let components = connected_components(mask, true);
    let mut output = mask.clone();
    for (index, pixel) in output.pixels.iter_mut().enumerate() {
        let label = components.labels[index];
        if *pixel != 0 && (components.sizes[label] as f64) < minimum_pixels {
            *pixel = 0;
        }
    }
    let changed_components = components
        .sizes
        .iter()
        .skip(1)
        .filter(|&&size| (size as f64) < minimum_pixels)
        .count();
    MaskChange {
        mask: output,
        changed_components,
    }
}

/// Fills enclosed background components below the threshold, or all enclosed
/// components when `fill_all` is true.
#[must_use]
pub fn fill_small_holes(mask: &BinaryMask, minimum_pixels: f64, fill_all: bool) -> MaskChange {
    let components = connected_components(mask, false);
    let fill: Vec<bool> = components
        .sizes
        .iter()
        .zip(&components.touches_border)
        .map(|(&size, &border)| !border && (fill_all || (size as f64) < minimum_pixels))
        .collect();
    let mut output = mask.clone();
    for (index, pixel) in output.pixels.iter_mut().enumerate() {
        if *pixel == 0 && fill[components.labels[index]] {
            *pixel = 1;
        }
    }
    MaskChange {
        mask: output,
        changed_components: fill.iter().skip(1).filter(|&&selected| selected).count(),
    }
}

/// Opening that restores small corner slivers while retaining large removals.
#[must_use]
pub fn open_keep_corners(mask: &BinaryMask, radius: f64) -> BinaryMask {
    if !radius.is_finite() || radius <= 0.0 {
        return mask.clone();
    }
    let opened = dilate(&erode(mask, radius), radius);
    let difference = BinaryMask::from_normalized(
        mask.width,
        mask.height,
        mask.pixels
            .iter()
            .zip(&opened.pixels)
            .map(|(&original, &result)| u8::from(original != 0 && result == 0))
            .collect(),
    );
    let components = connected_components(&difference, true);
    let limit = 5.0 * radius * radius;
    let mut output = opened;
    for (index, pixel) in difference.pixels.iter().enumerate() {
        if *pixel != 0 && (components.sizes[components.labels[index]] as f64) < limit {
            output.pixels[index] = 1;
        }
    }
    output
}

/// Closing that removes small filled corner slivers while retaining real bridges.
#[must_use]
pub fn close_keep_corners(mask: &BinaryMask, radius: f64) -> BinaryMask {
    if !radius.is_finite() || radius <= 0.0 {
        return mask.clone();
    }
    let closed = erode(&dilate(mask, radius), radius);
    let difference = BinaryMask::from_normalized(
        mask.width,
        mask.height,
        closed
            .pixels
            .iter()
            .zip(&mask.pixels)
            .map(|(&result, &original)| u8::from(result != 0 && original == 0))
            .collect(),
    );
    let components = connected_components(&difference, true);
    let limit = 5.0 * radius * radius;
    let mut output = closed;
    for (index, pixel) in difference.pixels.iter().enumerate() {
        if *pixel != 0 && (components.sizes[components.labels[index]] as f64) < limit {
            output.pixels[index] = 0;
        }
    }
    output
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CleanupSettings {
    pub minimum_piece_area_pt2: f64,
    pub bridge_width_pt: f64,
    pub thin_feature_width_pt: f64,
    pub minimum_hole_area_pt2: f64,
    pub keep_inner_cuts: bool,
}

impl Default for CleanupSettings {
    fn default() -> Self {
        Self {
            minimum_piece_area_pt2: 0.0,
            bridge_width_pt: 0.0,
            thin_feature_width_pt: 0.0,
            minimum_hole_area_pt2: 0.0,
            keep_inner_cuts: true,
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct CleanupReport {
    pub dropped_pieces: usize,
    pub dropped_holes: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CleanupResult {
    pub mask: BinaryMask,
    pub report: CleanupReport,
}

/// Runs piece, bridge, thin-feature, and hole cleanup at zero offset.
#[must_use]
pub fn cleanup_mask(
    mask: &BinaryMask,
    pixels_per_point: f64,
    settings: CleanupSettings,
) -> CleanupResult {
    let scale = valid_scale(pixels_per_point);
    let pieces = drop_small_pieces(mask, settings.minimum_piece_area_pt2 * scale * scale);
    let mut output = pieces.mask;
    if settings.bridge_width_pt > 0.2 {
        output = close_keep_corners(&output, settings.bridge_width_pt * scale / 2.0);
    }
    if settings.thin_feature_width_pt > 0.2 {
        output = open_keep_corners(&output, settings.thin_feature_width_pt * scale / 2.0);
    }
    let holes = fill_small_holes(
        &output,
        settings.minimum_hole_area_pt2 * scale * scale,
        !settings.keep_inner_cuts,
    );
    CleanupResult {
        mask: holes.mask,
        report: CleanupReport {
            dropped_pieces: pieces.changed_components,
            dropped_holes: holes.changed_components,
        },
    }
}

fn valid_scale(scale: f64) -> f64 {
    if scale.is_finite() && scale > 0.0 {
        scale
    } else {
        0.0
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Spike {
    pub x: f64,
    pub y: f64,
    pub reach: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct OffsetSettings {
    pub offset_pt: f64,
    pub round_sharp_points_pt: f64,
    /// Detail retention percentage, clamped to 0..=100.
    pub detail_percent: f64,
    pub minimum_hole_area_pt2: f64,
    pub keep_inner_cuts: bool,
}

impl Default for OffsetSettings {
    fn default() -> Self {
        Self {
            offset_pt: 0.0,
            round_sharp_points_pt: 0.0,
            detail_percent: 0.0,
            minimum_hole_area_pt2: 0.0,
            keep_inner_cuts: true,
        }
    }
}

/// Locally rounds selected sharp points, then applies an exact mask offset.
/// Positive offsets grow material and negative offsets shrink it.
#[must_use]
pub fn offset_mask(
    mask: &BinaryMask,
    pixels_per_point: f64,
    settings: OffsetSettings,
    spikes: &[Spike],
) -> BinaryMask {
    let scale = valid_scale(pixels_per_point);
    let distance = settings.offset_pt * scale;
    let requested_radius = settings.round_sharp_points_pt * scale;
    let tip_radius = (requested_radius - distance.max(0.0)).max(0.0);
    let mut output = mask.clone();

    if tip_radius > 0.2 && !spikes.is_empty() {
        let opened = dilate(&erode(mask, tip_radius), tip_radius);
        for spike in spikes {
            apply_local_opening(&mut output, &opened, *spike, tip_radius);
        }
    }

    if distance > 0.2 {
        if settings.detail_percent > 0.0 {
            output = offset_keep_detail(
                &output,
                distance,
                (settings.detail_percent / 100.0).clamp(0.0, 1.0),
            );
            output = fill_small_holes(
                &output,
                settings.minimum_hole_area_pt2 * scale * scale,
                !settings.keep_inner_cuts,
            )
            .mask;
        } else {
            output = dilate(&output, distance);
        }
    } else if distance < -0.2 {
        output = erode(&output, -distance);
    }
    output
}

fn apply_local_opening(output: &mut BinaryMask, opened: &BinaryMask, spike: Spike, radius: f64) {
    if !spike.x.is_finite() || !spike.y.is_finite() || !spike.reach.is_finite() {
        return;
    }
    let reach = (spike.reach + radius + 2.0).max(0.0);
    let reach_squared = reach * reach;
    let x_start = (spike.x - reach).floor().max(0.0) as usize;
    let y_start = (spike.y - reach).floor().max(0.0) as usize;
    let x_end = (spike.x + reach)
        .ceil()
        .min(output.width.saturating_sub(1) as f64) as usize;
    let y_end = (spike.y + reach)
        .ceil()
        .min(output.height.saturating_sub(1) as f64) as usize;
    if output.width == 0 || output.height == 0 || x_start > x_end || y_start > y_end {
        return;
    }
    for y in y_start..=y_end {
        for x in x_start..=x_end {
            let dx = x as f64 - spike.x;
            let dy = y as f64 - spike.y;
            if dx * dx + dy * dy <= reach_squared {
                let index = y * output.width + x;
                output.pixels[index] = opened.pixels[index];
            }
        }
    }
}

/// Detail-preserving positive offset from the Illustrator pipeline.
#[must_use]
pub fn offset_keep_detail(mask: &BinaryMask, distance: f64, detail: f64) -> BinaryMask {
    if !distance.is_finite() || distance <= 0.0 {
        return mask.clone();
    }
    let detail = if detail.is_finite() {
        detail.clamp(0.0, 1.0)
    } else {
        0.0
    };
    let width = mask.width;
    let height = mask.height;
    let len = mask.pixels.len();
    let offset = dilate(mask, distance);
    let feature = euclidean_feature_transform(mask);
    let floor_radius = 0.3 * distance;
    let channel_radius = (0.5 * distance * detail).max(1.0);
    let mut ridge = vec![0_u8; len];

    if width >= 3 && height >= 3 {
        for y in 1..height - 1 {
            for x in 1..width - 1 {
                let index = y * width + x;
                let squared_distance = feature.squared_distances[index];
                if mask.pixels[index] != 0 || offset.pixels[index] == 0 || squared_distance < 4.0 {
                    continue;
                }
                let distance_to_art = squared_distance.sqrt();
                let threshold_squared = (0.8 * distance_to_art).max(3.0).powi(2);
                let right = index + 1;
                let below = index + width;
                if feature_separation_squared(&feature, index, right) > threshold_squared
                    || feature_separation_squared(&feature, index, below) > threshold_squared
                {
                    ridge[index] = 1;
                }
            }
        }
    }

    let closed = erode(&offset, distance);
    let inverse_closed = BinaryMask::from_normalized(
        width,
        height,
        closed
            .pixels
            .iter()
            .map(|&pixel| u8::from(pixel == 0))
            .collect(),
    );
    let depth_squared = squared_euclidean_distance_transform(&inverse_closed);
    let expanded_ridge = dilate(&BinaryMask::from_normalized(width, height, ridge), 1.0);
    let ridge_components = connected_components(&expanded_ridge, true);
    let minimum_depth_squared = (0.5 * distance).powi(2);
    let mut deep = vec![false; ridge_components.sizes.len()];
    for index in 0..len {
        if expanded_ridge.pixels[index] != 0
            && closed.pixels[index] != 0
            && depth_squared[index] >= minimum_depth_squared
        {
            deep[ridge_components.labels[index]] = true;
        }
    }
    let selected = BinaryMask::from_normalized(
        width,
        height,
        (0..len)
            .map(|index| {
                u8::from(expanded_ridge.pixels[index] != 0 && deep[ridge_components.labels[index]])
            })
            .collect(),
    );
    let carved = dilate(&selected, channel_radius);
    let floor = dilate(mask, floor_radius);
    let mut output = BinaryMask::from_normalized(
        width,
        height,
        (0..len)
            .map(|index| {
                u8::from(
                    (offset.pixels[index] != 0 && carved.pixels[index] == 0)
                        || floor.pixels[index] != 0,
                )
            })
            .collect(),
    );

    let output_components = connected_components(&output, true);
    let mut contains_art = vec![false; output_components.sizes.len()];
    for index in 0..len {
        if output.pixels[index] != 0 && mask.pixels[index] != 0 {
            contains_art[output_components.labels[index]] = true;
        }
    }
    for index in 0..len {
        if output.pixels[index] != 0 && !contains_art[output_components.labels[index]] {
            output.pixels[index] = 0;
        }
    }
    output
}

fn feature_separation_squared(feature: &FeatureTransform, first: usize, second: usize) -> f64 {
    let dx = feature.nearest_x[first] as f64 - feature.nearest_x[second] as f64;
    let dy = feature.nearest_y[first] as f64 - feature.nearest_y[second] as f64;
    dx * dx + dy * dy
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_mask() -> BinaryMask {
        let width = 200;
        let height = 200;
        let mut mask =
            BinaryMask::filled(width, height, false).unwrap_or_else(|_| std::process::abort());
        for y in 50..150 {
            for x in 50..150 {
                assert!(mask.set(x, y, true).is_ok());
            }
        }
        for y in 98..103 {
            for x in 150..190 {
                assert!(mask.set(x, y, true).is_ok());
            }
        }
        for y in 50..150 {
            for x in 99..102 {
                assert!(mask.set(x, y, false).is_ok());
            }
        }
        mask
    }

    #[test]
    fn morphology_matches_cut_js_regression() {
        let mask = sample_mask();
        let opened = open_keep_corners(&mask, 4.0);
        let closed = close_keep_corners(&mask, 4.0);
        assert_eq!(opened.get(170, 100), Some(false));
        assert_eq!(opened.get(50, 50), Some(true));
        assert_eq!(closed.get(100, 100), Some(true));
        assert_eq!(closed.get(150, 97), Some(false));

        let dilated = dilate(&mask, 10.0);
        let mut maximum_growth = 0_usize;
        for y in 0..200 {
            for x in 0..140 {
                if dilated.get(x, y) == Some(true) {
                    maximum_growth = maximum_growth.max(50_usize.saturating_sub(x));
                }
            }
        }
        assert_eq!(maximum_growth, 10);
    }

    #[test]
    fn distance_transform_is_exact_at_pixel_centers() {
        let mut mask = BinaryMask::filled(5, 5, false).unwrap_or_else(|_| std::process::abort());
        assert!(mask.set(2, 2, true).is_ok());
        assert_eq!(
            squared_euclidean_distance_transform(&mask),
            vec![
                8.0, 5.0, 4.0, 5.0, 8.0, 5.0, 2.0, 1.0, 2.0, 5.0, 4.0, 1.0, 0.0, 1.0, 4.0, 5.0,
                2.0, 1.0, 2.0, 5.0, 8.0, 5.0, 4.0, 5.0, 8.0
            ]
        );
    }

    #[test]
    fn components_are_four_connected_and_small_pieces_drop() {
        let mask = BinaryMask::new(3, 3, vec![1, 0, 0, 0, 1, 0, 0, 0, 1])
            .unwrap_or_else(|_| std::process::abort());
        let labels = connected_components(&mask, true);
        assert_eq!(labels.component_count(), 3);
        let dropped = drop_small_pieces(&mask, 2.0);
        assert_eq!(dropped.changed_components, 3);
        assert!(dropped.mask.pixels().iter().all(|&pixel| pixel == 0));
    }

    #[test]
    fn cleanup_reports_removed_piece_and_hole() {
        let mut mask = BinaryMask::filled(7, 7, false).unwrap_or_else(|_| std::process::abort());
        for y in 1..6 {
            for x in 1..6 {
                assert!(mask.set(x, y, true).is_ok());
            }
        }
        assert!(mask.set(3, 3, false).is_ok());
        assert!(mask.set(0, 0, true).is_ok());
        let result = cleanup_mask(
            &mask,
            1.0,
            CleanupSettings {
                minimum_piece_area_pt2: 2.0,
                minimum_hole_area_pt2: 2.0,
                ..CleanupSettings::default()
            },
        );
        assert_eq!(result.report.dropped_pieces, 1);
        assert_eq!(result.report.dropped_holes, 1);
        assert_eq!(result.mask.get(0, 0), Some(false));
        assert_eq!(result.mask.get(3, 3), Some(true));
    }
}
