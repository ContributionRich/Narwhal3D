//! Dense closed-contour simplification and cubic fitting.
//!
//! This is a dependency-free Rust port of Coco's proven Illustrator contour
//! fitter. Coordinates and tolerances are in document points.

use std::f64::consts::PI;
use std::fmt;

const POINT_EPSILON_SQUARED: f64 = 1.0e-6;

/// A point in document coordinates.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

impl Point {
    #[must_use]
    pub const fn new(x: f64, y: f64) -> Self {
        Self { x, y }
    }

    fn length(self) -> f64 {
        self.x.hypot(self.y)
    }

    fn normalized(self) -> Self {
        let length = self.length();
        if length < 1.0e-12 {
            Self::default()
        } else {
            self * (1.0 / length)
        }
    }

    fn dot(self, other: Self) -> f64 {
        self.x * other.x + self.y * other.y
    }
}

impl std::ops::Add for Point {
    type Output = Self;

    fn add(self, rhs: Self) -> Self::Output {
        Self::new(self.x + rhs.x, self.y + rhs.y)
    }
}

impl std::ops::Sub for Point {
    type Output = Self;

    fn sub(self, rhs: Self) -> Self::Output {
        Self::new(self.x - rhs.x, self.y - rhs.y)
    }
}

impl std::ops::Mul<f64> for Point {
    type Output = Self;

    fn mul(self, rhs: f64) -> Self::Output {
        Self::new(self.x * rhs, self.y * rhs)
    }
}

/// One editable node of a closed cubic path.
///
/// The segment beginning at this node is
/// `anchor -> out_handle -> next.in_handle -> next.anchor`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CubicNode {
    pub anchor: Point,
    pub in_handle: Point,
    pub out_handle: Point,
    pub corner: bool,
}

/// Controls dense-loop simplification. All distances are document points.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SimplifySettings {
    pub tolerance_pt: f64,
    pub corner_angle_deg: f64,
    pub round_sharp_points_pt: f64,
    pub spike_angle_deg: f64,
}

impl Default for SimplifySettings {
    fn default() -> Self {
        Self {
            tolerance_pt: 0.58,
            corner_angle_deg: 60.0,
            round_sharp_points_pt: 0.0,
            spike_angle_deg: 75.0,
        }
    }
}

/// Why a dense loop could not be converted to a cubic path.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ContourError {
    NonFinitePoint,
    InvalidSettings,
    TooFewUniquePoints,
    FitFailed,
}

impl fmt::Display for ContourError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NonFinitePoint => formatter.write_str("contour contains a non-finite coordinate"),
            Self::InvalidSettings => {
                formatter.write_str("contour simplification settings are invalid")
            }
            Self::TooFewUniquePoints => {
                formatter.write_str("contour has fewer than three unique points")
            }
            Self::FitFailed => formatter.write_str("contour could not be fitted"),
        }
    }
}

impl std::error::Error for ContourError {}

type Cubic = [Point; 4];

#[derive(Clone, Debug)]
struct CornerCandidate {
    index: usize,
    previous: Point,
    next: Point,
}

#[derive(Clone, Debug)]
struct Replacement {
    from: usize,
    to: usize,
    arc: Vec<Point>,
    at: usize,
}

/// Converts a dense closed loop into editable closed cubic nodes.
///
/// The input must not repeat its first point at the end, although a repeated
/// closing point is harmless and will be removed. Winding is preserved.
pub fn simplify_closed_loop(
    loop_points: &[Point],
    settings: SimplifySettings,
) -> Result<Vec<CubicNode>, ContourError> {
    validate(loop_points, settings)?;
    let points = dedupe(loop_points);
    let point_count = points.len();
    if point_count < 3 {
        return Err(ContourError::TooFewUniquePoints);
    }

    let mut kept = dp_closed_indices(&points, settings.tolerance_pt.min(0.35));
    if kept.len() < 3 {
        return Err(ContourError::TooFewUniquePoints);
    }
    kept = spread_indices(&points, &kept);

    let candidates = detect_corners(&points, &kept, settings.corner_angle_deg);
    let (mut dense, mut dense_corners) = build_dense_loop(
        &points,
        &candidates,
        settings.round_sharp_points_pt,
        settings.spike_angle_deg,
    );
    dedupe_with_flags(&mut dense, &mut dense_corners);
    if dense.len() < 3 {
        return Err(ContourError::TooFewUniquePoints);
    }

    smooth_staircase(&mut dense, &dense_corners);
    fit_closed_runs(&dense, &dense_corners, settings.tolerance_pt)
}

fn validate(points: &[Point], settings: SimplifySettings) -> Result<(), ContourError> {
    if points
        .iter()
        .any(|point| !point.x.is_finite() || !point.y.is_finite())
    {
        return Err(ContourError::NonFinitePoint);
    }
    if !settings.tolerance_pt.is_finite()
        || settings.tolerance_pt <= 0.0
        || !settings.corner_angle_deg.is_finite()
        || !(0.0..=180.0).contains(&settings.corner_angle_deg)
        || !settings.round_sharp_points_pt.is_finite()
        || settings.round_sharp_points_pt < 0.0
        || !settings.spike_angle_deg.is_finite()
        || !(0.0..=180.0).contains(&settings.spike_angle_deg)
    {
        return Err(ContourError::InvalidSettings);
    }
    Ok(())
}

fn distance_squared(a: Point, b: Point) -> f64 {
    let delta = a - b;
    delta.dot(delta)
}

fn segment_distance_squared(point: Point, start: Point, end: Point) -> f64 {
    let segment = end - start;
    let length_squared = segment.dot(segment);
    if length_squared < 1.0e-12 {
        return distance_squared(point, start);
    }
    let parameter = ((point - start).dot(segment) / length_squared).clamp(0.0, 1.0);
    distance_squared(point, start + segment * parameter)
}

fn dedupe(points: &[Point]) -> Vec<Point> {
    let mut output = Vec::with_capacity(points.len());
    for &point in points {
        if output
            .last()
            .is_none_or(|&previous| distance_squared(point, previous) > POINT_EPSILON_SQUARED)
        {
            output.push(point);
        }
    }
    if output.len() > 1
        && distance_squared(output[0], output[output.len() - 1]) < POINT_EPSILON_SQUARED
    {
        output.pop();
    }
    output
}

fn dp_closed_indices(points: &[Point], tolerance: f64) -> Vec<usize> {
    let count = points.len();
    if count < 4 {
        return (0..count).collect();
    }

    let mut min_x = 0;
    let mut max_x = 0;
    let mut min_y = 0;
    let mut max_y = 0;
    for index in 1..count {
        if points[index].x < points[min_x].x {
            min_x = index;
        }
        if points[index].x > points[max_x].x {
            max_x = index;
        }
        if points[index].y < points[min_y].y {
            min_y = index;
        }
        if points[index].y > points[max_y].y {
            max_y = index;
        }
    }

    let mut cuts = vec![min_x, max_x, min_y, max_y];
    cuts.sort_unstable();
    cuts.dedup();
    if cuts.len() < 2 {
        cuts = vec![0, count / 2];
    }

    let mut kept = vec![false; count];
    let tolerance_squared = tolerance * tolerance;
    for cut_index in 0..cuts.len() {
        let start = cuts[cut_index];
        let end = cuts[(cut_index + 1) % cuts.len()];
        let mut arc = vec![start];
        let mut cursor = start;
        while cursor != end {
            cursor = (cursor + 1) % count;
            arc.push(cursor);
        }
        kept[start] = true;
        kept[end] = true;
        let mut stack = vec![(0, arc.len() - 1)];
        while let Some((first, last)) = stack.pop() {
            let mut best = None;
            let mut best_distance = 0.0;
            for offset in (first + 1)..last {
                let distance = segment_distance_squared(
                    points[arc[offset]],
                    points[arc[first]],
                    points[arc[last]],
                );
                if distance > best_distance {
                    best_distance = distance;
                    best = Some(offset);
                }
            }
            if let Some(best_offset) = best.filter(|_| best_distance > tolerance_squared) {
                kept[arc[best_offset]] = true;
                stack.push((first, best_offset));
                stack.push((best_offset, last));
            }
        }
    }

    kept.into_iter()
        .enumerate()
        .filter_map(|(index, retain)| retain.then_some(index))
        .collect()
}

fn spread_indices(points: &[Point], indices: &[usize]) -> Vec<usize> {
    let mut spread = Vec::with_capacity(indices.len());
    if let Some(&first) = indices.first() {
        spread.push(first);
    }
    for &index in indices.iter().skip(1) {
        let previous = spread[spread.len() - 1];
        if (points[index] - points[previous]).length() >= 1.5 {
            spread.push(index);
        }
    }
    if spread.len() > 2 && (points[spread[spread.len() - 1]] - points[spread[0]]).length() < 1.5 {
        spread.pop();
    }
    if spread.len() >= 3 {
        spread
    } else {
        indices.to_vec()
    }
}

fn turn_angle(previous: Point, point: Point, next: Point) -> f64 {
    let incoming = point - previous;
    let outgoing = next - point;
    let denominator = incoming.length() * outgoing.length();
    if denominator < 1.0e-9 {
        0.0
    } else {
        (incoming.dot(outgoing) / denominator)
            .clamp(-1.0, 1.0)
            .acos()
            * 180.0
            / PI
    }
}

fn detect_corners(points: &[Point], kept: &[usize], threshold: f64) -> Vec<CornerCandidate> {
    let mut corners = Vec::new();
    for position in 0..kept.len() {
        let previous = points[kept[(position + kept.len() - 1) % kept.len()]];
        let index = kept[position];
        let next = points[kept[(position + 1) % kept.len()]];
        if turn_angle(previous, points[index], next) >= threshold {
            corners.push(CornerCandidate {
                index,
                previous,
                next,
            });
        }
    }
    corners
}

fn build_dense_loop(
    points: &[Point],
    candidates: &[CornerCandidate],
    round_radius: f64,
    spike_angle: f64,
) -> (Vec<Point>, Vec<bool>) {
    let count = points.len();
    let signed_double_area: f64 = (0..count)
        .map(|index| {
            let point = points[index];
            let next = points[(index + 1) % count];
            point.x * next.y - next.x * point.y
        })
        .sum();
    let counter_clockwise = signed_double_area > 0.0;
    let mut corner_flags = vec![false; count];
    let mut replacements = Vec::new();

    for candidate in candidates {
        let tip = points[candidate.index];
        let previous_direction = (candidate.previous - tip).normalized();
        let next_direction = (candidate.next - tip).normalized();
        let interior = previous_direction
            .dot(next_direction)
            .clamp(-1.0, 1.0)
            .acos()
            * 180.0
            / PI;
        let incoming = tip - candidate.previous;
        let outgoing = candidate.next - tip;
        let cross = incoming.x * outgoing.y - incoming.y * outgoing.x;
        let convex = if counter_clockwise {
            cross > 0.0
        } else {
            cross < 0.0
        };

        if round_radius > 0.05
            && convex
            && interior < spike_angle
            && let Some(replacement) =
                fillet_replacement(points, candidate.index, interior, round_radius)
        {
            replacements.push(replacement);
            continue;
        }
        corner_flags[candidate.index] = true;
    }

    replacements.sort_unstable_by_key(|replacement| replacement.from);
    let mut non_overlapping = Vec::with_capacity(replacements.len());
    let mut last_to = None;
    for replacement in replacements {
        let wraps = replacement.to < replacement.from;
        let overlaps = last_to.is_some_and(|last| !wraps && replacement.from <= last);
        if overlaps {
            corner_flags[replacement.at] = true;
        } else {
            last_to = Some(if wraps { count } else { replacement.to });
            non_overlapping.push(replacement);
        }
    }

    if non_overlapping.is_empty() {
        return (points.to_vec(), corner_flags);
    }

    let mut skipped = vec![false; count];
    let mut insertions: Vec<Option<Vec<Point>>> = vec![None; count];
    for replacement in non_overlapping {
        let mut cursor = replacement.from;
        while cursor != replacement.to {
            cursor = (cursor + 1) % count;
            if cursor != replacement.to {
                skipped[cursor] = true;
            }
        }
        insertions[replacement.from] = Some(replacement.arc);
    }

    let mut dense = Vec::new();
    let mut dense_corners = Vec::new();
    for index in 0..count {
        if let Some(arc) = &insertions[index] {
            dense.extend(arc.iter().copied());
            dense_corners.resize(dense.len(), false);
        } else if !skipped[index] {
            dense.push(points[index]);
            dense_corners.push(corner_flags[index]);
        }
    }
    (dense, dense_corners)
}

fn fillet_replacement(
    points: &[Point],
    tip_index: usize,
    interior_deg: f64,
    round_radius: f64,
) -> Option<Replacement> {
    let count = points.len();
    let half_angle = interior_deg * PI / 360.0;
    let trim = (round_radius / half_angle.tan()).min(2.5 * round_radius);

    let mut before = tip_index;
    let mut distance = 0.0;
    while distance < trim {
        let previous = (before + count - 1) % count;
        distance += (points[before] - points[previous]).length();
        before = previous;
        if before == tip_index {
            break;
        }
    }
    let mut after = tip_index;
    distance = 0.0;
    while distance < trim {
        let next = (after + 1) % count;
        distance += (points[after] - points[next]).length();
        after = next;
        if after == tip_index {
            break;
        }
    }

    let tip = points[tip_index];
    let first = points[before];
    let last = points[after];
    let first_direction = (first - tip).normalized();
    let last_direction = (last - tip).normalized();
    let half = first_direction.dot(last_direction).clamp(-1.0, 1.0).acos() / 2.0;
    if half <= 0.01 {
        return None;
    }
    let radius = round_radius.min((first - tip).length() * half.tan());
    let center = tip + (first_direction + last_direction).normalized() * (radius / half.sin());
    let first_angle = (first.y - center.y).atan2(first.x - center.x);
    let last_angle = (last.y - center.y).atan2(last.x - center.x);
    let mut sweep = last_angle - first_angle;
    while sweep > PI {
        sweep -= 2.0 * PI;
    }
    while sweep < -PI {
        sweep += 2.0 * PI;
    }
    let arc = (0..=8)
        .map(|sample| {
            let angle = first_angle + sweep * f64::from(sample) / 8.0;
            Point::new(
                center.x + radius * angle.cos(),
                center.y + radius * angle.sin(),
            )
        })
        .collect();
    Some(Replacement {
        from: before,
        to: after,
        arc,
        at: tip_index,
    })
}

fn dedupe_with_flags(points: &mut Vec<Point>, flags: &mut Vec<bool>) {
    let mut output = Vec::with_capacity(points.len());
    let mut output_flags = Vec::with_capacity(flags.len());
    for (&point, &flag) in points.iter().zip(flags.iter()) {
        if output
            .last()
            .is_none_or(|&previous| distance_squared(point, previous) > POINT_EPSILON_SQUARED)
        {
            output.push(point);
            output_flags.push(flag);
        }
    }
    *points = output;
    *flags = output_flags;
}

fn smooth_staircase(points: &mut Vec<Point>, corner_flags: &[bool]) {
    let count = points.len();
    let perimeter: f64 = (0..count)
        .map(|index| (points[(index + 1) % count] - points[index]).length())
        .sum();
    let step = perimeter / count as f64;
    if step > 0.6 || step <= 1.0e-12 {
        return;
    }

    let sigma = 0.6;
    let radius = (3.0 * sigma / step).round().clamp(1.0, 12.0 * count as f64) as usize;
    let weights: Vec<f64> = (0..=(2 * radius))
        .map(|index| {
            let offset = index as f64 - radius as f64;
            (-(offset * step).powi(2) / (2.0 * sigma * sigma)).exp()
        })
        .collect();
    let guard = (1.0 / step).round().max(1.0) as usize;
    let mut fixed = vec![false; count];
    for (corner, &is_corner) in corner_flags.iter().enumerate() {
        if is_corner {
            for offset in 0..=(2 * guard) {
                let index = (corner + count + offset % count - guard % count) % count;
                fixed[index] = true;
            }
        }
    }

    let mut smoothed = vec![Point::default(); count];
    for index in 0..count {
        if fixed[index] {
            smoothed[index] = points[index];
            continue;
        }
        let mut weighted = Point::default();
        let mut total_weight = 0.0;
        for (weight_index, &weight) in weights.iter().enumerate() {
            let sample = (index + count + weight_index % count - radius % count) % count;
            weighted = weighted + points[sample] * weight;
            total_weight += weight;
        }
        smoothed[index] = weighted * (1.0 / total_weight);
    }
    *points = smoothed;
}

/// A straight stretch of the dense loop from `start` to `end` (indices; it may wrap).
#[derive(Clone, Copy, Debug)]
struct StraightRun {
    start: usize,
    end: usize,
    direction: Point,
}

impl StraightRun {
    /// The best-fit line through the run's points, with its ends projected onto it.
    /// A line through the two end samples instead tilts by their staircase error.
    fn fitted(&self, points: &[Point]) -> (Point, Point, Point) {
        let count = points.len();
        let span = (self.end + count - self.start) % count;
        let run: Vec<Point> = (0..=span)
            .map(|offset| points[(self.start + offset) % count])
            .collect();
        let n = run.len() as f64;
        let centre = run.iter().fold(Point::default(), |sum, &p| sum + p) * (1.0 / n);
        let (mut xx, mut xy, mut yy) = (0.0, 0.0, 0.0);
        for &p in &run {
            let d = p - centre;
            xx += d.x * d.x;
            xy += d.x * d.y;
            yy += d.y * d.y;
        }
        let angle = 0.5 * (2.0 * xy).atan2(xx - yy);
        let mut direction = Point::new(angle.cos(), angle.sin());
        if direction.dot(self.direction) < 0.0 {
            direction = direction * -1.0;
        }
        let project = |p: Point| centre + direction * (p - centre).dot(direction);
        (
            project(points[self.start]),
            project(points[self.end]),
            direction,
        )
    }
}

fn median_step(points: &[Point]) -> f64 {
    let mut steps: Vec<f64> = (0..points.len())
        .map(|index| (points[(index + 1) % points.len()] - points[index]).length())
        .collect();
    steps.sort_by(f64::total_cmp);
    steps.get(steps.len() / 2).copied().unwrap_or(0.0)
}

/// Where a loop without corners bends most, so straight runs never straddle its start.
fn sharpest_bend(points: &[Point], step: f64) -> usize {
    let count = points.len();
    let window = ((2.0 / step).ceil() as usize).clamp(1, count / 3);
    (0..count)
        .max_by(|&a, &b| {
            let bend = |index: usize| {
                turn_angle(
                    points[(index + count - window) % count],
                    points[index],
                    points[(index + window) % count],
                )
            };
            bend(a).total_cmp(&bend(b))
        })
        .unwrap_or(0)
}

/// How far a straight line from offset `first` can reach, keeping every point between
/// within `band` of it. Tracks the cone of directions from the first point that pass within
/// `band` of every later point, so each point is visited once.
fn straight_reach(at: &impl Fn(usize) -> Point, first: usize, last: usize, band: f64) -> usize {
    let origin = at(first);
    let mut reference = None;
    let (mut low, mut high) = (0.0_f64, 0.0_f64);
    let mut reach = first;
    for offset in (first + 1)..=last {
        let delta = at(offset) - origin;
        let distance = delta.length();
        if distance <= band {
            continue;
        }
        let angle = delta.y.atan2(delta.x);
        let half = (band / distance).asin();
        let Some(base) = reference else {
            reference = Some(angle);
            (low, high) = (-half, half);
            reach = offset;
            continue;
        };
        let mut relative = angle - base;
        if relative > PI {
            relative -= 2.0 * PI;
        } else if relative <= -PI {
            relative += 2.0 * PI;
        }
        if relative < low || relative > high {
            break;
        }
        reach = offset;
        low = low.max(relative - half);
        high = high.min(relative + half);
        if low > high {
            break;
        }
    }
    reach
}

/// Long straight stretches of a dense (pixel-step) loop. Fitting these with curves makes the
/// cut swing by the full tolerance on either side of a flat edge.
///
/// Any arc looks straight over a short enough distance. A run must stay within `band` of a
/// line for 160 pixel steps, which only a very large arc can do (radius over 2,000 steps),
/// and such an arc is itself within `band` (at most half the tolerance) of the line.
fn straight_runs(points: &[Point], corner_flags: &[bool], tolerance: f64) -> Vec<StraightRun> {
    let count = points.len();
    let step = median_step(points);
    // Only traced pixel outlines are dense enough to tell straight from curved.
    if count < 8 || !(step > 1.0e-9 && step <= 0.6) {
        return Vec::new();
    }
    // Resampled anti-aliased edges flip between two pixel rows where they cross the alpha
    // threshold, so a straight edge must be allowed a full pixel (1.5 smoothed steps) of
    // noise. Half the fitting tolerance caps it: a run within the band is within tolerance.
    let band = (1.5 * step).min(tolerance * 0.5);
    let min_length = 160.0 * step;
    let mut starts: Vec<usize> = corner_flags
        .iter()
        .enumerate()
        .filter_map(|(index, &corner)| corner.then_some(index))
        .collect();
    if starts.is_empty() {
        starts.push(sharpest_bend(points, step));
    }

    let mut runs = Vec::new();
    for (position, &start) in starts.iter().enumerate() {
        let end = starts[(position + 1) % starts.len()];
        let span = match (end + count - start) % count {
            0 => count,
            span => span,
        };
        let at = |offset: usize| points[(start + offset) % count];
        let mut first = 0;
        while first < span {
            let reach = straight_reach(&at, first, span, band);
            let chord = at(reach) - at(first);
            if chord.length() >= min_length {
                runs.push(StraightRun {
                    start: (start + first) % count,
                    end: (start + reach) % count,
                    direction: chord.normalized(),
                });
                first = reach;
            } else {
                first += 1;
            }
        }
    }

    runs
}

fn fit_closed_runs(
    points: &[Point],
    corner_flags: &[bool],
    tolerance: f64,
) -> Result<Vec<CubicNode>, ContourError> {
    let count = points.len();
    let lines = straight_runs(points, corner_flags, tolerance);
    let mut line_end_at = vec![None; count];
    let mut line_into = vec![None; count];
    let mut line_out_of = vec![None; count];
    // Line ends move onto their fitted lines (by at most the straightness band); the curves
    // beside them start from the moved points, so the outline stays connected.
    let mut points = points.to_vec();
    let mut moved = Vec::with_capacity(lines.len() * 2);
    for line in &lines {
        let (start, end, direction) = line.fitted(&points);
        moved.push((line.start, start));
        moved.push((line.end, end));
        line_end_at[line.start] = Some(line.end);
        line_out_of[line.start] = Some(direction);
        line_into[line.end] = Some(direction);
    }
    for (index, point) in moved {
        points[index] = point;
    }
    let points = points.as_slice();
    let mut breaks: Vec<usize> = (0..count)
        .filter(|&index| {
            corner_flags[index] || line_out_of[index].is_some() || line_into[index].is_some()
        })
        .collect();
    let single_run = breaks.is_empty();
    if single_run {
        breaks.push(0);
    }

    let mut segments = Vec::new();
    let mut corner_starts = Vec::new();
    for position in 0..breaks.len() {
        let start = breaks[position];
        let end = breaks[(position + 1) % breaks.len()];
        if line_end_at[start] == Some(end) {
            segments.push([points[start], points[start], points[end], points[end]]);
            corner_starts.push(corner_flags[start]);
            continue;
        }
        let mut run = vec![points[start]];
        let mut cursor = start;
        while {
            cursor = (cursor + 1) % count;
            cursor != end
        } {
            run.push(points[cursor]);
        }
        run.push(points[end]);
        // Beside a straight segment, continue its direction so the join stays smooth.
        let run_segments = fit_run(
            &run,
            tolerance,
            line_into[start],
            line_out_of[end].map(|direction| direction * -1.0),
        );
        for (index, segment) in run_segments.into_iter().enumerate() {
            segments.push(straighten(segment));
            corner_starts.push(index == 0 && !single_run && corner_flags[start]);
        }
    }
    if segments.is_empty() {
        return Err(ContourError::FitFailed);
    }

    let mut nodes = Vec::with_capacity(segments.len());
    for index in 0..segments.len() {
        let segment = segments[index];
        let previous = segments[(index + segments.len() - 1) % segments.len()];
        let line_corner = segment[1] == segment[0] && previous[2] == previous[3];
        nodes.push(CubicNode {
            anchor: segment[0],
            in_handle: previous[2],
            out_handle: segment[1],
            corner: corner_starts[index] || line_corner,
        });
    }
    Ok(nodes)
}

fn fit_run(
    points: &[Point],
    tolerance: f64,
    start_tangent: Option<Point>,
    end_tangent: Option<Point>,
) -> Vec<Cubic> {
    let mut output = Vec::new();
    if points.len() < 2 {
        return output;
    }
    let window = tangent_window(points);
    let start_tangent = start_tangent.unwrap_or_else(|| tangent_at(points, 0, 0, window));
    let end_tangent =
        end_tangent.unwrap_or_else(|| tangent_at(points, points.len() - 1, window, 0) * -1.0);
    fit_cubic(
        points,
        start_tangent,
        end_tangent,
        tolerance * tolerance,
        &mut output,
        0,
    );
    output
}

fn fit_cubic(
    points: &[Point],
    start_tangent: Point,
    end_tangent: Point,
    error_squared: f64,
    output: &mut Vec<Cubic>,
    depth: usize,
) {
    if points.len() == 2 || depth > 40 {
        let handle_length = (points[points.len() - 1] - points[0]).length() / 3.0;
        output.push([
            points[0],
            points[0] + start_tangent * handle_length,
            points[points.len() - 1] + end_tangent * handle_length,
            points[points.len() - 1],
        ]);
        return;
    }

    let mut parameters = chord_parameters(points);
    let mut cubic = generate_bezier(points, &parameters, start_tangent, end_tangent);
    let (mut error, mut split) = maximum_error(points, &parameters, cubic);
    if error < error_squared {
        output.push(cubic);
        return;
    }
    if error < error_squared * 4.0 {
        for _ in 0..6 {
            parameters = reparameterize(points, &parameters, cubic);
            cubic = generate_bezier(points, &parameters, start_tangent, end_tangent);
            (error, split) = maximum_error(points, &parameters, cubic);
            if error < error_squared {
                output.push(cubic);
                return;
            }
        }
    }

    if split == 0 || split >= points.len() - 1 {
        split = points.len() / 2;
    }
    let window = tangent_window(points);
    let forward = tangent_at(points, split, window, window);
    fit_cubic(
        &points[..=split],
        start_tangent,
        forward * -1.0,
        error_squared,
        output,
        depth + 1,
    );
    fit_cubic(
        &points[split..],
        forward,
        end_tangent,
        error_squared,
        output,
        depth + 1,
    );
}

fn tangent_window(points: &[Point]) -> usize {
    let length: f64 = points
        .windows(2)
        .map(|pair| (pair[1] - pair[0]).length())
        .sum();
    let step = length / (points.len().saturating_sub(1).max(1) as f64);
    (2.0 / if step == 0.0 { 1.0 } else { step })
        .round()
        .clamp(1.0, 12.0) as usize
}

fn tangent_at(points: &[Point], index: usize, backward: usize, forward: usize) -> Point {
    let low = index.saturating_sub(backward);
    let high = index.saturating_add(forward).min(points.len() - 1);
    if high == low {
        Point::default()
    } else {
        (points[high] - points[low]).normalized()
    }
}

fn chord_parameters(points: &[Point]) -> Vec<f64> {
    let mut parameters = Vec::with_capacity(points.len());
    parameters.push(0.0);
    for pair in points.windows(2) {
        let next = parameters[parameters.len() - 1] + (pair[1] - pair[0]).length();
        parameters.push(next);
    }
    let length = parameters.last().copied().unwrap_or(0.0);
    let divisor = if length == 0.0 { 1.0 } else { length };
    for parameter in &mut parameters {
        *parameter /= divisor;
    }
    parameters
}

fn generate_bezier(
    points: &[Point],
    parameters: &[f64],
    start_tangent: Point,
    end_tangent: Point,
) -> Cubic {
    let first = points[0];
    let last = points[points.len() - 1];
    let mut c00 = 0.0;
    let mut c01 = 0.0;
    let mut c11 = 0.0;
    let mut x0 = 0.0;
    let mut x1 = 0.0;
    for (&point, &parameter) in points.iter().zip(parameters) {
        let inverse = 1.0 - parameter;
        let b0 = inverse.powi(3);
        let b1 = 3.0 * inverse * inverse * parameter;
        let b2 = 3.0 * inverse * parameter * parameter;
        let b3 = parameter.powi(3);
        let a1 = start_tangent * b1;
        let a2 = end_tangent * b2;
        c00 += a1.dot(a1);
        c01 += a1.dot(a2);
        c11 += a2.dot(a2);
        let residual = point - (first * (b0 + b1) + last * (b2 + b3));
        x0 += a1.dot(residual);
        x1 += a2.dot(residual);
    }

    let determinant = c00 * c11 - c01 * c01;
    let (mut left_length, mut right_length) = if determinant.abs() > 1.0e-12 {
        (
            (x0 * c11 - x1 * c01) / determinant,
            (c00 * x1 - c01 * x0) / determinant,
        )
    } else {
        (0.0, 0.0)
    };
    let segment_length = (last - first).length();
    let epsilon = 1.0e-6 * segment_length;
    // Nearly parallel end tangents make the system almost singular, and a few samples
    // (e.g. a one-pixel jog beside a long straight edge) can then be "fitted" by handles
    // hundreds of points long whose loop still passes through every sample. Handles
    // that overshoot each other along the chord are never a real fit: use the
    // Wu/Barsky chord/3 heuristic instead (as paper.js does for Schneider's method).
    let chord = last - first;
    let overshoot = (start_tangent * left_length).dot(chord)
        - (end_tangent * right_length).dot(chord)
        > chord.dot(chord);
    if left_length < epsilon || right_length < epsilon || overshoot {
        left_length = segment_length / 3.0;
        right_length = segment_length / 3.0;
    }
    [
        first,
        first + start_tangent * left_length,
        last + end_tangent * right_length,
        last,
    ]
}

fn bezier(cubic: Cubic, parameter: f64) -> Point {
    let inverse = 1.0 - parameter;
    cubic[0] * inverse.powi(3)
        + cubic[1] * (3.0 * inverse * inverse * parameter)
        + cubic[2] * (3.0 * inverse * parameter * parameter)
        + cubic[3] * parameter.powi(3)
}

fn bezier_first_derivative(cubic: Cubic, parameter: f64) -> Point {
    let inverse = 1.0 - parameter;
    (cubic[1] - cubic[0]) * (3.0 * inverse * inverse)
        + (cubic[2] - cubic[1]) * (6.0 * inverse * parameter)
        + (cubic[3] - cubic[2]) * (3.0 * parameter * parameter)
}

fn bezier_second_derivative(cubic: Cubic, parameter: f64) -> Point {
    let inverse = 1.0 - parameter;
    (cubic[2] - cubic[1] * 2.0 + cubic[0]) * (6.0 * inverse)
        + (cubic[3] - cubic[2] * 2.0 + cubic[1]) * (6.0 * parameter)
}

fn reparameterize(points: &[Point], parameters: &[f64], cubic: Cubic) -> Vec<f64> {
    points
        .iter()
        .zip(parameters)
        .map(|(&point, &parameter)| {
            let difference = bezier(cubic, parameter) - point;
            let first = bezier_first_derivative(cubic, parameter);
            let second = bezier_second_derivative(cubic, parameter);
            let numerator = difference.dot(first);
            let denominator = first.dot(first) + difference.dot(second);
            if denominator.abs() < 1.0e-12 {
                parameter
            } else {
                (parameter - numerator / denominator).clamp(0.0, 1.0)
            }
        })
        .collect()
}

fn maximum_error(points: &[Point], parameters: &[f64], cubic: Cubic) -> (f64, usize) {
    let mut maximum = 0.0;
    let mut split = points.len() / 2;
    for index in 1..(points.len() - 1) {
        let error = distance_squared(bezier(cubic, parameters[index]), points[index]);
        if error > maximum {
            maximum = error;
            split = index;
        }
    }
    (maximum, split)
}

fn straighten(cubic: Cubic) -> Cubic {
    if (cubic[3] - cubic[0]).length() < 1.0e-9 {
        return cubic;
    }
    if segment_distance_squared(cubic[1], cubic[0], cubic[3]).sqrt() < 0.05
        && segment_distance_squared(cubic[2], cubic[0], cubic[3]).sqrt() < 0.05
    {
        [cubic[0], cubic[0], cubic[3], cubic[3]]
    } else {
        cubic
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings() -> SimplifySettings {
        SimplifySettings::default()
    }

    fn circle(sample_count: usize, quantization: Option<f64>) -> Vec<Point> {
        (0..sample_count)
            .map(|index| {
                let angle = 2.0 * PI * index as f64 / sample_count as f64;
                let mut x = 100.0 * angle.cos();
                let mut y = 100.0 * angle.sin();
                if let Some(grid) = quantization {
                    x = (x / grid).round() * grid;
                    y = (y / grid).round() * grid;
                }
                Point::new(x, y)
            })
            .collect()
    }

    fn fitted_deviation(nodes: &[CubicNode], dense: &[Point]) -> f64 {
        let mut maximum: f64 = 0.0;
        for index in 0..nodes.len() {
            let node = nodes[index];
            let next = nodes[(index + 1) % nodes.len()];
            let cubic = [node.anchor, node.out_handle, next.in_handle, next.anchor];
            for sample in 0..=20 {
                let point = bezier(cubic, f64::from(sample) / 20.0);
                let best = (0..dense.len())
                    .map(|dense_index| {
                        segment_distance_squared(
                            point,
                            dense[dense_index],
                            dense[(dense_index + 1) % dense.len()],
                        )
                    })
                    .fold(f64::INFINITY, f64::min);
                maximum = maximum.max(best);
            }
        }
        maximum.sqrt()
    }

    #[test]
    fn nearly_vertical_pixel_edges_do_not_fit_runaway_handles() -> Result<(), ContourError> {
        // Bug report mvpzy4r3: the From Image/Bleed White plate of a 300-PPI sticker had
        // spikes hundreds of points long beside its nearly vertical side edges, where the
        // trace is long straight pixel runs joined by one-pixel jogs.
        let dense: Vec<Point> = include_str!("../testdata/report-mvpzy4r3-outline.txt")
            .lines()
            .filter(|line| !line.starts_with('#'))
            .filter_map(|line| {
                let (x, y) = line.split_once(',')?;
                Some(Point::new(
                    x.parse::<f64>().ok()? * 0.24,
                    y.parse::<f64>().ok()? * 0.24,
                ))
            })
            .collect();
        assert_eq!(dense.len(), 2302);
        let nodes = simplify_closed_loop(
            &dense,
            SimplifySettings {
                tolerance_pt: 0.18,
                corner_angle_deg: 60.0,
                round_sharp_points_pt: 0.0,
                spike_angle_deg: 75.0,
            },
        )?;
        let deviation = fitted_deviation(&nodes, &dense);
        assert!(
            deviation <= 1.0,
            "{deviation:.2} pt away from the traced outline"
        );
        Ok(())
    }

    #[test]
    fn dense_circle_fits_within_tolerance_using_few_nodes() -> Result<(), ContourError> {
        let dense = circle(720, None);
        let nodes = simplify_closed_loop(&dense, settings())?;
        assert!(nodes.len() <= 12, "{} nodes", nodes.len());
        let deviation = fitted_deviation(&nodes, &dense);
        assert!(deviation <= 0.6, "{deviation:.3} pt deviation");
        Ok(())
    }

    #[test]
    fn star_retains_all_ten_corners() -> Result<(), ContourError> {
        let dense: Vec<Point> = (0..10)
            .map(|index| {
                let radius = if index % 2 == 0 { 100.0 } else { 40.0 };
                let angle = PI * index as f64 / 5.0;
                Point::new(radius * angle.cos(), radius * angle.sin())
            })
            .collect();
        let nodes = simplify_closed_loop(&dense, settings())?;
        let corners = nodes.iter().filter(|node| node.corner).count();
        assert!(corners >= 10, "{corners} corners");
        Ok(())
    }

    #[test]
    fn staircase_circle_is_compact_without_invented_corners() -> Result<(), ContourError> {
        let dense = circle(1_500, Some(0.24));
        let nodes = simplify_closed_loop(&dense, settings())?;
        assert!(nodes.len() <= 16, "{} nodes", nodes.len());
        let corners = nodes.iter().filter(|node| node.corner).count();
        assert_eq!(corners, 0, "invented corners");
        Ok(())
    }

    /// A stadium (a long flat-sided rectangle with semicircular ends) on a 0.24 pt pixel
    /// grid, rotated by `degrees`, traced one pixel step at a time.
    fn stadium(degrees: f64) -> (Vec<Point>, f64) {
        let (half_length, radius, grid) = (120.0, 20.0, 0.24);
        let (sin, cos) = degrees.to_radians().sin_cos();
        let mut dense = Vec::new();
        let perimeter = 4.0 * half_length + 2.0 * PI * radius;
        let steps = (perimeter / (grid * 0.5)) as usize;
        let (side, arc) = (2.0 * half_length, PI * radius);
        for step in 0..steps {
            let s = step as f64 * perimeter / steps as f64;
            let local = if s < side {
                Point::new(-half_length + s, -radius)
            } else if s < side + arc {
                let angle = -PI / 2.0 + (s - side) / radius;
                Point::new(half_length + radius * angle.cos(), radius * angle.sin())
            } else if s < 2.0 * side + arc {
                Point::new(half_length - (s - side - arc), radius)
            } else {
                let angle = PI / 2.0 + (s - 2.0 * side - arc) / radius;
                Point::new(-half_length + radius * angle.cos(), radius * angle.sin())
            };
            let rotated = Point::new(local.x * cos - local.y * sin, local.x * sin + local.y * cos);
            let snapped = Point::new(
                (rotated.x / grid).round() * grid,
                (rotated.y / grid).round() * grid,
            );
            if dense.last() != Some(&snapped) {
                dense.push(snapped);
            }
        }
        (dense, radius)
    }

    /// Largest distance of the fitted curve from the stadium's top flat side, sampled
    /// along its middle 80%.
    fn flat_side_wobble(nodes: &[CubicNode], degrees: f64, radius: f64) -> f64 {
        let (sin, cos) = degrees.to_radians().sin_cos();
        let mut worst: f64 = 0.0;
        for index in 0..nodes.len() {
            let node = nodes[index];
            let next = nodes[(index + 1) % nodes.len()];
            let cubic = [node.anchor, node.out_handle, next.in_handle, next.anchor];
            for sample in 0..=50 {
                let point = bezier(cubic, f64::from(sample) / 50.0);
                // Back into the unrotated frame.
                let x = point.x * cos + point.y * sin;
                let y = -point.x * sin + point.y * cos;
                if x.abs() <= 96.0 && y < 0.0 {
                    worst = worst.max((y + radius).abs());
                }
            }
        }
        worst
    }

    #[test]
    fn large_gentle_curves_are_not_mistaken_for_straight_lines() -> Result<(), ContourError> {
        // A 5-inch circle on the 300 ppi processing grid: its sides look nearly straight.
        let dense = circle(7_500, Some(0.24))
            .into_iter()
            .map(|point| point * 1.8)
            .collect::<Vec<_>>();
        let nodes = simplify_closed_loop(&dense, settings())?;
        assert!(nodes.len() <= 16, "{} nodes", nodes.len());
        let straight = nodes
            .iter()
            .filter(|node| node.out_handle == node.anchor)
            .count();
        assert_eq!(straight, 0, "a circle has no straight segments");
        Ok(())
    }

    #[test]
    fn long_straight_edges_fit_as_straight_lines_without_wobble() -> Result<(), ContourError> {
        // Bug report mw5rege5: the flat base of a sticker was fitted with curves that
        // swung by the full tolerance above and below the straight edge.
        for degrees in [0.0, 1.5, 30.0] {
            let (dense, radius) = stadium(degrees);
            let nodes = simplify_closed_loop(&dense, settings())?;
            let wobble = flat_side_wobble(&nodes, degrees, radius);

            // A pixel staircase is within half a grid step of the true edge.
            assert!(
                wobble <= 0.15,
                "{degrees}°: flat side wobbles {wobble:.3} pt (nodes {})",
                nodes.len()
            );
            assert!(fitted_deviation(&nodes, &dense) <= 0.6);
            assert!(nodes.len() <= 16, "{degrees}°: {} nodes", nodes.len());
            // A stadium has no corners: every node, including where a flat side meets a
            // round end, continues the curve in the same direction.
            for (index, node) in nodes.iter().enumerate() {
                let previous = nodes[(index + nodes.len() - 1) % nodes.len()].anchor;
                let next = nodes[(index + 1) % nodes.len()].anchor;
                let incoming = if node.in_handle == node.anchor {
                    node.anchor - previous
                } else {
                    node.anchor - node.in_handle
                };
                let outgoing = if node.out_handle == node.anchor {
                    next - node.anchor
                } else {
                    node.out_handle - node.anchor
                };
                let kink = turn_angle(node.anchor - incoming, node.anchor, node.anchor + outgoing);
                assert!(
                    kink < 5.0,
                    "{degrees}°: {kink:.1}° kink at {:?}",
                    node.anchor
                );
            }
        }
        Ok(())
    }

    #[test]
    fn malformed_input_returns_errors_instead_of_panicking() {
        assert_eq!(
            simplify_closed_loop(&[Point::new(0.0, 0.0)], settings()),
            Err(ContourError::TooFewUniquePoints)
        );
        assert_eq!(
            simplify_closed_loop(&[Point::new(f64::NAN, 0.0); 3], settings()),
            Err(ContourError::NonFinitePoint)
        );
    }
}
