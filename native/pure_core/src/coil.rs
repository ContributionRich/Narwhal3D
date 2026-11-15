//! Adaptive cubic NURBS planning for axis-based Helix and Spiral curves.

use std::f64::consts::TAU;

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
const ERR_TOLERANCE: i32 = -4;
const MAX_TURNS: f64 = 1_000.0;
const MAX_SPANS_PER_TURN: usize = 96;

#[derive(Clone, Copy)]
struct V3([f64; 3]);

impl V3 {
    fn add(self, other: Self) -> Self {
        Self(std::array::from_fn(|i| self.0[i] + other.0[i]))
    }
    fn sub(self, other: Self) -> Self {
        Self(std::array::from_fn(|i| self.0[i] - other.0[i]))
    }
    fn scale(self, value: f64) -> Self {
        Self(std::array::from_fn(|i| self.0[i] * value))
    }
    fn dot(self, other: Self) -> f64 {
        self.0.iter().zip(other.0).map(|(a, b)| a * b).sum()
    }
    fn cross(self, other: Self) -> Self {
        Self([
            self.0[1] * other.0[2] - self.0[2] * other.0[1],
            self.0[2] * other.0[0] - self.0[0] * other.0[2],
            self.0[0] * other.0[1] - self.0[1] * other.0[0],
        ])
    }
    fn norm(self) -> f64 {
        self.dot(self).sqrt()
    }
    fn unit(self) -> Option<Self> {
        let length = self.norm();
        (length.is_finite() && length > 1.0e-12).then(|| self.scale(1.0 / length))
    }
    fn distance(self, other: Self) -> f64 {
        self.sub(other).norm()
    }
}

// CPython 3.12+ float sum uses compensated addition. Keep this coil-only:
// catenary's existing arithmetic and compatibility contract are independent.
fn coil_dot(a: V3, b: V3) -> f64 {
    let mut hi = 0.0_f64;
    let mut lo = 0.0_f64;
    for i in 0..3 {
        let x = a.0[i] * b.0[i];
        let t = hi + x;
        lo += if hi.abs() >= x.abs() {
            (hi - t) + x
        } else {
            (x - t) + hi
        };
        hi = t;
    }
    if lo != 0.0 && lo.is_finite() {
        hi + lo
    } else {
        hi
    }
}
fn coil_norm(v: V3) -> f64 {
    coil_dot(v, v).sqrt()
}
fn coil_unit(v: V3) -> Option<V3> {
    let length = coil_norm(v);
    (length.is_finite() && length > 1.0e-12).then(|| v.scale(1.0 / length))
}

struct Coil {
    start: V3,
    axis: V3,
    x: V3,
    y: V3,
    axis_length: f64,
    radius0: f64,
    radius1: f64,
    theta_total: f64,
}

impl Coil {
    fn point_derivative(&self, theta: f64) -> (V3, V3) {
        let fraction = theta / self.theta_total;
        let radius = self.radius0 + (self.radius1 - self.radius0) * fraction;
        let dr = (self.radius1 - self.radius0) / self.theta_total;
        let (sin, cos) = theta.sin_cos();
        let radial = self.x.scale(cos).add(self.y.scale(sin));
        let tangent = self.x.scale(-sin).add(self.y.scale(cos));
        let point = self
            .start
            .add(radial.scale(radius))
            .add(self.axis.scale(self.axis_length * fraction));
        let derivative = radial
            .scale(dr)
            .add(tangent.scale(radius))
            .add(self.axis.scale(self.axis_length / self.theta_total));
        (point, derivative)
    }
}

struct Plan {
    poles: Vec<V3>,
    knots: Vec<f64>,
    max_deviation: f64,
    spans: usize,
}

fn bezier_point(control: [V3; 4], t: f64) -> V3 {
    let s = 1.0 - t;
    V3(std::array::from_fn(|i| {
        control[0].0[i] * s.powf(3.0)
            + control[1].0[i] * 3.0 * s * s * t
            + control[2].0[i] * 3.0 * s * t * t
            + control[3].0[i] * t.powf(3.0)
    }))
}

fn controls_and_error(coil: &Coil, spans: usize) -> (Vec<V3>, f64) {
    let h = coil.theta_total / spans as f64;
    let mut poles = Vec::with_capacity(3 * spans + 1);
    let mut maximum = 0.0_f64;
    for span in 0..spans {
        let theta0 = h * span as f64;
        let theta1 = h * (span + 1) as f64;
        let (p0, d0) = coil.point_derivative(theta0);
        let (p1, d1) = coil.point_derivative(theta1);
        let control = [p0, p0.add(d0.scale(h / 3.0)), p1.sub(d1.scale(h / 3.0)), p1];
        if span == 0 {
            poles.extend(control);
        } else {
            poles.extend_from_slice(&control[1..]);
        }
        for sample in 1..16 {
            let t = sample as f64 / 16.0;
            // Match the original per-coordinate multiplication order, not
            // pre-multiplied Bernstein weights (which round differently).
            let s = 1.0 - t;
            let actual = V3(std::array::from_fn(|i| {
                control[0].0[i] * s.powf(3.0)
                    + control[1].0[i] * 3.0 * s * s * t
                    + control[2].0[i] * 3.0 * s * t * t
                    + control[3].0[i] * t.powf(3.0)
            }));
            let expected = coil.point_derivative(theta0 + h * t).0;
            maximum = maximum.max(coil_norm(actual.sub(expected)));
        }
    }
    (poles, maximum)
}

#[allow(clippy::too_many_arguments)]
fn plan(
    start: [f64; 3],
    end: [f64; 3],
    radial: [f64; 3],
    radius0: f64,
    radius1: f64,
    turns: f64,
    reverse: bool,
    tolerance: f64,
    flat: bool,
) -> Result<Plan, i32> {
    if start
        .iter()
        .chain(end.iter())
        .chain(radial.iter())
        .any(|value| !value.is_finite())
        || !radius0.is_finite()
        || !radius1.is_finite()
        || !turns.is_finite()
        || !tolerance.is_finite()
        || turns <= 0.0
        || turns > MAX_TURNS
        || tolerance <= 0.0
    {
        return Err(ERR_INPUT);
    }
    let start = V3(start);
    let axis_vector = V3(end).sub(start);
    let axis_length = coil_norm(axis_vector);
    let axis = coil_unit(axis_vector).ok_or(ERR_INPUT)?;
    let radial = V3(radial).sub(axis.scale(coil_dot(V3(radial), axis)));
    let x = coil_unit(radial).ok_or(ERR_INPUT)?;
    let y = coil_unit(axis.cross(x)).ok_or(ERR_INPUT)?;
    let theta_total = if reverse { -TAU * turns } else { TAU * turns };
    let coil = Coil {
        start,
        axis,
        x,
        y,
        axis_length: if flat { 0.0 } else { axis_length },
        radius0,
        radius1,
        theta_total,
    };
    let base = (turns * 6.0).ceil().max(1.0) as usize;
    let maximum = (turns * MAX_SPANS_PER_TURN as f64).ceil().max(1.0) as usize;
    let mut spans = base;
    loop {
        let (poles, max_deviation) = controls_and_error(&coil, spans);
        if max_deviation <= tolerance {
            let mut knots = Vec::with_capacity(3 * spans + 3);
            knots.extend([0.0; 3]);
            for boundary in 1..spans {
                let value = boundary as f64 / spans as f64;
                knots.extend([value; 3]);
            }
            knots.extend([1.0; 3]);
            return Ok(Plan {
                poles,
                knots,
                max_deviation,
                spans,
            });
        }
        if spans >= maximum {
            return Err(ERR_TOLERANCE);
        }
        spans = spans.saturating_mul(2).min(maximum);
    }
}

/// Stable catenary v2 reasons. Status remains -2 for parameter/frame failures
/// and -4 for unachieved tolerance; null/capacity errors carry reason zero.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u32)]
pub enum CatenaryReason {
    Parameters = 1,
    Direction = 2,
    Height = 3,
    Through = 4,
    Tolerance = 5,
    Arithmetic = 6,
}
impl CatenaryReason {
    fn status(self) -> i32 {
        if self == Self::Tolerance {
            ERR_TOLERANCE
        } else {
            ERR_INPUT
        }
    }
}

// Match the original CPython 3.13 sum and per-coordinate evaluation order.
// Keep these catenary-specific; coil21's qualified implementation is immutable.
fn catenary_dot(a: V3, b: V3) -> f64 {
    crate::fit::py_sum((0..3).map(|i| a.0[i] * b.0[i]))
}
fn catenary_norm(v: V3) -> f64 {
    catenary_dot(v, v).sqrt()
}
fn catenary_unit(v: V3) -> Result<V3, CatenaryReason> {
    let length = catenary_norm(v);
    if !length.is_finite() {
        return Err(CatenaryReason::Arithmetic);
    }
    if length <= 1.0e-12 {
        return Err(CatenaryReason::Direction);
    }
    Ok(v.scale(1.0 / length))
}

fn catenary_value(a: f64, half: f64) -> f64 {
    let z = half / a;
    if z > 700.0 {
        f64::INFINITY
    } else {
        a * (z.cosh() - 1.0)
    }
}

fn catenary_plan(
    start: [f64; 3],
    end: [f64; 3],
    axis_point: [f64; 3],
    through: [f64; 3],
    tolerance: f64,
) -> Result<Plan, CatenaryReason> {
    if start
        .iter()
        .chain(end.iter())
        .chain(axis_point.iter())
        .chain(through.iter())
        .any(|value| !value.is_finite())
        || !tolerance.is_finite()
        || tolerance <= 0.0
    {
        return Err(CatenaryReason::Parameters);
    }
    let start = V3(start);
    let end = V3(end);
    let middle = start.add(end).scale(0.5);
    let gravity = catenary_unit(V3(axis_point).sub(middle))?;
    let chord = end.sub(start);
    let vertical = catenary_dot(chord, gravity);
    let horizontal_vector = chord.sub(gravity.scale(vertical));
    let length = catenary_norm(horizontal_vector);
    if !length.is_finite() || !vertical.is_finite() {
        return Err(CatenaryReason::Arithmetic);
    }
    if length <= 1.0e-12 || vertical.abs() > tolerance {
        return Err(CatenaryReason::Height);
    }
    let horizontal = horizontal_vector.scale(1.0 / length);
    let through_delta = V3(through).sub(middle);
    let through_x = catenary_dot(through_delta, horizontal);
    let off_plane = catenary_norm(
        through_delta
            .sub(horizontal.scale(through_x))
            .sub(gravity.scale(catenary_dot(through_delta, gravity))),
    );
    let sag = catenary_dot(through_delta, gravity);
    if !through_x.is_finite() || !off_plane.is_finite() || !sag.is_finite() {
        return Err(CatenaryReason::Arithmetic);
    }
    if through_x.abs() > tolerance || off_plane > tolerance || sag <= tolerance {
        return Err(CatenaryReason::Through);
    }
    let half = length * 0.5;
    let mut low = half / 700.0;
    let mut high = half.max(sag);
    while catenary_value(high, half) > sag {
        high *= 2.0;
        if !high.is_finite() {
            return Err(CatenaryReason::Arithmetic);
        }
    }
    for _ in 0..100 {
        let mid = (low + high) * 0.5;
        if catenary_value(mid, half) > sag {
            low = mid;
        } else {
            high = mid;
        }
    }
    let a = (low + high) * 0.5;
    if !a.is_finite() || a <= 0.0 {
        return Err(CatenaryReason::Arithmetic);
    }
    let resolved_sag = catenary_value(a, half);
    // A fixed bisection budget must not report success after huge finite inputs
    // lose the requested sag to cancellation or an unresolved bracket.
    if !resolved_sag.is_finite() {
        return Err(CatenaryReason::Arithmetic);
    }
    if (resolved_sag - sag).abs() > tolerance {
        return Err(CatenaryReason::Tolerance);
    }
    let analytic = |x: f64| {
        let point = middle
            .add(horizontal.scale(x))
            .add(gravity.scale(a * ((half / a).cosh() - (x / a).cosh())));
        let derivative = horizontal.sub(gravity.scale((x / a).sinh()));
        (point, derivative)
    };
    let mut spans = 8usize;
    loop {
        let dx = length / spans as f64;
        let mut poles = Vec::with_capacity(3 * spans + 1);
        let mut maximum = 0.0_f64;
        for span in 0..spans {
            let x0 = -half + dx * span as f64;
            let x1 = -half + dx * (span + 1) as f64;
            let (p0, d0) = analytic(x0);
            let (p1, d1) = analytic(x1);
            // Reject overflowing finite arithmetic before publishing a span.
            if !p0.distance(p1).is_finite() {
                return Err(CatenaryReason::Arithmetic);
            }
            let control = [
                p0,
                p0.add(d0.scale(dx / 3.0)),
                p1.sub(d1.scale(dx / 3.0)),
                p1,
            ];
            if control.iter().flat_map(|v| v.0).any(|v| !v.is_finite()) {
                return Err(CatenaryReason::Arithmetic);
            }
            if span == 0 {
                poles.extend(control);
            } else {
                poles.extend_from_slice(&control[1..]);
            }
            for sample in 1..16 {
                let t = sample as f64 / 16.0;
                let error = catenary_norm(bezier_point(control, t).sub(analytic(x0 + dx * t).0));
                if !error.is_finite() {
                    return Err(CatenaryReason::Arithmetic);
                }
                maximum = maximum.max(error);
            }
        }
        if maximum <= tolerance {
            let mut knots = Vec::with_capacity(3 * spans + 3);
            knots.extend([0.0; 3]);
            for boundary in 1..spans {
                knots.extend([boundary as f64 / spans as f64; 3]);
            }
            knots.extend([1.0; 3]);
            return Ok(Plan {
                poles,
                knots,
                max_deviation: maximum,
                spans,
            });
        }
        if spans >= 128 {
            return Err(CatenaryReason::Tolerance);
        }
        spans *= 2;
    }
}

/// Sample the analytic coil locus into caller-owned xyz preview points.
/// # Safety
/// Input triples are readable and output is writable for its declared capacity.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_coil_preview(
    start: *const f64,
    end: *const f64,
    radial: *const f64,
    radius0: f64,
    radius1: f64,
    turns: f64,
    reverse: u8,
    flat: u8,
    point_count: usize,
    output: *mut f64,
    output_capacity: usize,
    out_values: *mut u64,
) -> i32 {
    if out_values.is_null() {
        return ERR_NULL;
    }
    *out_values = 0;
    if reverse > 1 || flat > 1 || !(2..=2049).contains(&point_count) {
        return ERR_INPUT;
    }
    let triple = |ptr| match crate::checked_slice(ptr, 3) {
        Some(v) => Ok([v[0], v[1], v[2]]),
        None => Err(ERR_NULL),
    };
    let s = match triple(start) {
        Ok(v) => V3(v),
        Err(e) => return e,
    };
    let e = match triple(end) {
        Ok(v) => V3(v),
        Err(e) => return e,
    };
    let rv = match triple(radial) {
        Ok(v) => V3(v),
        Err(e) => return e,
    };
    if !radius0.is_finite() || !radius1.is_finite() || !turns.is_finite() || turns <= 0.0 {
        return ERR_INPUT;
    }
    let av = e.sub(s);
    let al = av.norm();
    let axis = match av.unit() {
        Some(v) => v,
        None => return ERR_INPUT,
    };
    let x = match rv.sub(axis.scale(rv.dot(axis))).unit() {
        Some(v) => v,
        None => return ERR_INPUT,
    };
    let y = match axis.cross(x).unit() {
        Some(v) => v,
        None => return ERR_INPUT,
    };
    let total = if reverse != 0 {
        -TAU * turns
    } else {
        TAU * turns
    };
    let coil = Coil {
        start: s,
        axis,
        x,
        y,
        axis_length: if flat != 0 { 0.0 } else { al },
        radius0,
        radius1,
        theta_total: total,
    };
    let needed = match point_count.checked_mul(3) {
        Some(v) if v <= output_capacity => v,
        _ => return ERR_SIZE,
    };
    let out = match crate::checked_slice_mut(output, output_capacity) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    for i in 0..point_count {
        let theta = total * i as f64 / (point_count - 1) as f64;
        out[3 * i..3 * i + 3].copy_from_slice(&coil.point_derivative(theta).0 .0)
    }
    *out_values = needed as u64;
    0
}

/// Build one cubic C1 NURBS plan for an axis-based helix or spiral.
///
/// `radial` is a direction vector, not a point. Output poles are packed xyz.
///
/// # Safety
///
/// Every input/output span is readable/writable for its declared capacity and
/// does not overlap another span. Count outputs and deviation are writable.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_coil_plan(
    start: *const f64,
    end: *const f64,
    radial: *const f64,
    radius0: f64,
    radius1: f64,
    turns: f64,
    reverse: u8,
    tolerance: f64,
    flat: u8,
    poles: *mut f64,
    pole_capacity: usize,
    knots: *mut f64,
    knot_capacity: usize,
    out_poles: *mut u64,
    out_knots: *mut u64,
    out_spans: *mut u64,
    out_deviation: *mut f64,
) -> i32 {
    if out_poles.is_null() || out_knots.is_null() || out_spans.is_null() || out_deviation.is_null()
    {
        return ERR_NULL;
    }
    *out_poles = 0;
    *out_knots = 0;
    *out_spans = 0;
    *out_deviation = 0.0;
    if reverse > 1 || flat > 1 {
        return ERR_INPUT;
    }
    let start = match crate::checked_slice(start, 3) {
        Some(values) => [values[0], values[1], values[2]],
        None => return ERR_NULL,
    };
    let end = match crate::checked_slice(end, 3) {
        Some(values) => [values[0], values[1], values[2]],
        None => return ERR_NULL,
    };
    let radial = match crate::checked_slice(radial, 3) {
        Some(values) => [values[0], values[1], values[2]],
        None => return ERR_NULL,
    };
    let plan = match plan(
        start,
        end,
        radial,
        radius0,
        radius1,
        turns,
        reverse != 0,
        tolerance,
        flat != 0,
    ) {
        Ok(value) => value,
        Err(code) => return code,
    };
    let pole_values = match plan.poles.len().checked_mul(3) {
        Some(value) if value <= pole_capacity => value,
        _ => return ERR_SIZE,
    };
    if plan.knots.len() > knot_capacity {
        return ERR_SIZE;
    }
    let pole_output = match crate::checked_slice_mut(poles, pole_capacity) {
        Some(values) => values,
        None => return ERR_NULL,
    };
    let knot_output = match crate::checked_slice_mut(knots, knot_capacity) {
        Some(values) => values,
        None => return ERR_NULL,
    };
    for (index, point) in plan.poles.iter().enumerate() {
        pole_output[3 * index..3 * index + 3].copy_from_slice(&point.0);
    }
    knot_output[..plan.knots.len()].copy_from_slice(&plan.knots);
    *out_poles = pole_values as u64;
    *out_knots = plan.knots.len() as u64;
    *out_spans = plan.spans as u64;
    *out_deviation = plan.max_deviation;
    0
}

/// Build a symmetric through-point catenary as one cubic C1 NURBS plan.
///
/// # Safety
///
/// Input triples are readable; output spans and scalar outputs are writable for
/// their declared capacities and do not overlap.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_catenary_plan(
    start: *const f64,
    end: *const f64,
    axis_point: *const f64,
    through: *const f64,
    tolerance: f64,
    poles: *mut f64,
    pole_capacity: usize,
    knots: *mut f64,
    knot_capacity: usize,
    out_poles: *mut u64,
    out_knots: *mut u64,
    out_spans: *mut u64,
    out_deviation: *mut f64,
) -> i32 {
    let mut reason = 0;
    narwhal_pure_core_catenary_plan_v2(
        start,
        end,
        axis_point,
        through,
        tolerance,
        poles,
        pole_capacity,
        knots,
        knot_capacity,
        out_poles,
        out_knots,
        out_spans,
        out_deviation,
        &mut reason,
    )
}

/// Required-native catenary planner with stable named error reasons.
/// No output counts or geometry are published on failure. Finite-input and
/// finite-result refusal intentionally fixes the original Python NaN/inf bug.
/// # Safety
/// Same disjoint readable/writable span contract as the legacy catenary export;
/// out_reason additionally points to one writable u32.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_catenary_plan_v2(
    start: *const f64,
    end: *const f64,
    axis_point: *const f64,
    through: *const f64,
    tolerance: f64,
    poles: *mut f64,
    pole_capacity: usize,
    knots: *mut f64,
    knot_capacity: usize,
    out_poles: *mut u64,
    out_knots: *mut u64,
    out_spans: *mut u64,
    out_deviation: *mut f64,
    out_reason: *mut u32,
) -> i32 {
    if !out_poles.is_null() {
        *out_poles = 0;
    }
    if !out_knots.is_null() {
        *out_knots = 0;
    }
    if !out_spans.is_null() {
        *out_spans = 0;
    }
    if !out_deviation.is_null() {
        *out_deviation = 0.0;
    }
    if !out_reason.is_null() {
        *out_reason = 0;
    }
    if out_poles.is_null()
        || out_knots.is_null()
        || out_spans.is_null()
        || out_deviation.is_null()
        || out_reason.is_null()
    {
        return ERR_NULL;
    }
    let triple = |ptr| match crate::checked_slice(ptr, 3) {
        Some(value) => Ok([value[0], value[1], value[2]]),
        None => Err(ERR_NULL),
    };
    let plan = match catenary_plan(
        match triple(start) {
            Ok(v) => v,
            Err(e) => return e,
        },
        match triple(end) {
            Ok(v) => v,
            Err(e) => return e,
        },
        match triple(axis_point) {
            Ok(v) => v,
            Err(e) => return e,
        },
        match triple(through) {
            Ok(v) => v,
            Err(e) => return e,
        },
        tolerance,
    ) {
        Ok(value) => value,
        Err(reason) => {
            *out_reason = reason as u32;
            return reason.status();
        }
    };
    let pole_values = plan.poles.len() * 3;
    if pole_values > pole_capacity || plan.knots.len() > knot_capacity {
        return ERR_SIZE;
    }
    let pole_output = match crate::checked_slice_mut(poles, pole_capacity) {
        Some(value) => value,
        None => return ERR_NULL,
    };
    let knot_output = match crate::checked_slice_mut(knots, knot_capacity) {
        Some(value) => value,
        None => return ERR_NULL,
    };
    for (index, point) in plan.poles.iter().enumerate() {
        pole_output[3 * index..3 * index + 3].copy_from_slice(&point.0);
    }
    knot_output[..plan.knots.len()].copy_from_slice(&plan.knots);
    *out_poles = pole_values as u64;
    *out_knots = plan.knots.len() as u64;
    *out_spans = plan.spans as u64;
    *out_deviation = plan.max_deviation;
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn analytic_preview_has_exact_count_endpoints_and_reverse() {
        let s = [0., 0., 0.];
        let e = [0., 0., 5.];
        let r = [1., 0., 0.];
        let mut out = [0.; 15];
        let mut used = 0;
        let rc = unsafe {
            narwhal_pure_core_coil_preview(
                s.as_ptr(),
                e.as_ptr(),
                r.as_ptr(),
                2.,
                3.,
                1.,
                0,
                0,
                5,
                out.as_mut_ptr(),
                out.len(),
                &mut used,
            )
        };
        assert_eq!(rc, 0);
        assert_eq!(used, 15);
        assert_eq!(&out[..3], &[2., 0., 0.]);
        assert!((out[12] - 3.).abs() < 1e-12);
        assert!((out[14] - 5.).abs() < 1e-12);
    }

    #[test]
    fn helix_plan_has_expected_endpoints_knots_and_deviation() {
        let result = plan(
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 10.0],
            [1.0, 0.0, 0.0],
            2.0,
            2.0,
            3.0,
            false,
            0.001,
            false,
        )
        .expect("helix");
        assert_eq!(result.poles.len(), 3 * result.spans + 1);
        assert_eq!(result.knots.len(), result.poles.len() + 2);
        assert!(result.max_deviation <= 0.001);
        assert!(result.poles[0].distance(V3([2.0, 0.0, 0.0])) < 1.0e-12);
        assert!(result.poles.last().unwrap().distance(V3([2.0, 0.0, 10.0])) < 1.0e-10);
    }

    #[test]
    fn spiral_radius_and_reverse_direction_are_preserved() {
        let forward = plan(
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 4.0],
            [1.0, 0.0, 0.0],
            1.0,
            3.0,
            1.0,
            false,
            0.01,
            false,
        )
        .expect("spiral");
        let reverse = plan(
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 4.0],
            [1.0, 0.0, 0.0],
            1.0,
            3.0,
            1.0,
            true,
            0.01,
            false,
        )
        .expect("reverse");
        assert!(forward.poles.last().unwrap().distance(V3([3.0, 0.0, 4.0])) < 1.0e-10);
        assert!(forward.poles[1].0[1] > 0.0);
        assert!(reverse.poles[1].0[1] < 0.0);
        let flat = plan(
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 4.0],
            [1.0, 0.0, 0.0],
            1.0,
            3.0,
            1.0,
            false,
            0.01,
            true,
        )
        .expect("flat spiral");
        assert!(flat.poles.iter().all(|point| point.0[2].abs() < 1.0e-12));
        assert!(flat.poles.last().unwrap().distance(V3([3.0, 0.0, 0.0])) < 1.0e-10);
    }

    #[test]
    fn symmetric_catenary_has_exact_endpoints_apex_and_tolerance() {
        let result = catenary_plan(
            [-5.0, 0.0, 0.0],
            [5.0, 0.0, 0.0],
            [0.0, -1.0, 0.0],
            [0.0, -2.0, 0.0],
            0.001,
        )
        .expect("catenary");
        assert!(result.poles[0].distance(V3([-5.0, 0.0, 0.0])) < 1e-12);
        assert!(result.poles.last().unwrap().distance(V3([5.0, 0.0, 0.0])) < 1e-12);
        assert!(result.max_deviation <= 0.001);
        let apex = result.poles[3 * (result.spans / 2)];
        assert!(apex.distance(V3([0.0, -2.0, 0.0])) < 1e-10);
    }

    #[test]
    fn invalid_and_insufficient_outputs_publish_no_counts() {
        let start = [0.0, 0.0, 0.0];
        let end = [0.0, 0.0, 1.0];
        let radial = [1.0, 0.0, 0.0];
        let mut poles = [0.0; 3];
        let mut knots = [0.0; 3];
        let mut np = 9_u64;
        let mut nk = 9_u64;
        let mut ns = 9_u64;
        let mut dev = 9.0;
        let code = unsafe {
            narwhal_pure_core_coil_plan(
                start.as_ptr(),
                end.as_ptr(),
                radial.as_ptr(),
                1.0,
                1.0,
                1.0,
                0,
                0.01,
                0,
                poles.as_mut_ptr(),
                poles.len(),
                knots.as_mut_ptr(),
                knots.len(),
                &mut np,
                &mut nk,
                &mut ns,
                &mut dev,
            )
        };
        assert_eq!(code, ERR_SIZE);
        assert_eq!((np, nk, ns, dev), (0, 0, 0, 0.0));
        assert_eq!(
            plan(start, start, radial, 1.0, 1.0, 1.0, false, 0.01, false).err(),
            Some(ERR_INPUT)
        );
    }
}
