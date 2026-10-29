//! OURS: bounded, rail-interpolating parallel-section sweep (not Freeform).
//! Polynomial interpolation acts on exact rational section control offsets, never
//! on sampled curves. OCCT receives a single rational Bezier surface as a BRep.
use crate::*;

pub const CAPABILITY_SWEEP1_PARALLEL: u64 = 1 << 6;

/// Geometric bounds are control-net bounds, not sampled global-error claims.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
pub struct SweepReport {
    pub struct_size: u32,
    pub version: u32,
    pub status: i32,
    pub reason: u32,
    pub section_control_error_bound: f64,
    pub min_axial_increment: f64,
    pub min_transverse_increment: f64,
    pub projected_jacobian_lower_bound: f64,
}

extern "C" {
    fn narwhal_occt_adapter_sweep_parallel(
        poles: *const f64,
        middle_weight: f64,
        tolerance: f64,
        output: *mut u8,
        capacity: usize,
        required: *mut usize,
    ) -> i32;
}

type Point = [f64; 3];
fn dot(a: Point, b: Point) -> f64 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}
fn sub(a: Point, b: Point) -> Point {
    std::array::from_fn(|i| a[i] - b[i])
}
fn point(a: &[f64]) -> Point {
    [a[0], a[1], a[2]]
}
fn bezier(p: &[Point; 4], t: f64) -> Point {
    let mut q = *p;
    for n in (1..4).rev() {
        for i in 0..n {
            q[i] = std::array::from_fn(|k| (1. - t) * q[i][k] + t * q[i + 1][k]);
        }
    }
    q[0]
}

/// At most four separated stations; explicit low-degree Lagrange interpolation.
pub(crate) fn interpolate(t: &[f64], values: &[Point]) -> [Point; 4] {
    let mut power = [[0.; 3]; 4];
    for (i, value) in values.iter().enumerate() {
        let mut basis = [1., 0., 0., 0.];
        let mut degree = 0;
        for (j, &tj) in t.iter().enumerate() {
            if i == j {
                continue;
            }
            let mut next = [0.; 4];
            for k in 0..=degree {
                next[k] -= tj * basis[k] / (t[i] - tj);
                next[k + 1] += basis[k] / (t[i] - tj);
            }
            basis = next;
            degree += 1;
        }
        for k in 0..4 {
            for (axis, v) in value.iter().enumerate() {
                power[k][axis] += basis[k] * v;
            }
        }
    }
    std::array::from_fn(|j| {
        std::array::from_fn(|k| match j {
            0 => power[0][k],
            1 => power[0][k] + power[1][k] / 3.,
            2 => power[0][k] + 2. * power[1][k] / 3. + power[2][k] / 3.,
            _ => power.iter().map(|p| p[k]).sum(),
        })
    })
}

fn construct(a: &[f64], report: &mut SweepReport) -> Result<([Point; 12], f64, f64), u32> {
    if a.len() < 21 || a.iter().any(|v| !v.is_finite() || v.abs() > 1.0e6) {
        return Err(1);
    }
    let tolerance = a[0];
    let n = a[1] as usize;
    let w = a[2];
    if !(2..=4).contains(&n)
        || a[1] != n as f64
        || a.len() != 21 + 11 * n
        || !(1.0e-10..=1.0e-2).contains(&tolerance)
        || !(0.1..=1.).contains(&w)
    {
        return Err(1);
    }
    let axis = point(&a[3..6]);
    let across = point(&a[6..9]);
    if (dot(axis, axis) - 1.).abs() > 1.0e-12
        || (dot(across, across) - 1.).abs() > 1.0e-12
        || dot(axis, across).abs() > 1.0e-12
    {
        return Err(2);
    }
    let rail: [Point; 4] = std::array::from_fn(|i| point(&a[9 + 3 * i..12 + 3 * i]));
    let t = &a[21..21 + n];
    if t[0] != 0. || t[n - 1] != 1. || t.windows(2).any(|p| p[1] - p[0] < 0.05) {
        return Err(3);
    }
    let reverse = &a[21 + n..21 + 2 * n];
    if reverse.iter().any(|r| *r != 0. && *r != 1.) {
        return Err(1);
    }
    let section = |i: usize, j: usize| {
        let j = if reverse[i] == 1. { 2 - j } else { j };
        point(&a[21 + 2 * n + 9 * i + 3 * j..24 + 2 * n + 9 * i + 3 * j])
    };
    report.min_axial_increment = rail
        .windows(2)
        .map(|p| dot(sub(p[1], p[0]), axis))
        .fold(f64::INFINITY, f64::min);
    if report.min_axial_increment <= tolerance * 10. {
        return Err(4);
    }
    let placement_roundoff = 128. * f64::EPSILON * a.iter().map(|v| v.abs()).fold(1., f64::max);
    for (i, &ti) in t.iter().enumerate() {
        let d = sub(section(i, 0), bezier(&rail, ti));
        if dot(d, d).sqrt() > placement_roundoff {
            return Err(5);
        }
    }
    let mut net = [[0.; 3]; 12];
    net[..4].copy_from_slice(&rail);
    for j in 1..3 {
        let mut offsets = Vec::with_capacity(n);
        for (i, &ti) in t.iter().enumerate() {
            let d = sub(section(i, j), bezier(&rail, ti));
            let axial = dot(d, axis);
            if axial.abs() > placement_roundoff {
                return Err(5);
            }
            offsets.push(std::array::from_fn(|k| {
                d[k] - axial * axis[k] / dot(axis, axis)
            }));
        }
        let controls = interpolate(t, &offsets);
        for v in 0..4 {
            net[j * 4 + v] = std::array::from_fn(|k| rail[v][k] + controls[v][k]);
        }
    }
    // Positive rational weights and identical weights at all stations make the
    // pointwise section residual a convex combination of control residuals.
    for (i, &ti) in t.iter().enumerate() {
        for j in 0..3 {
            let row: [Point; 4] = net[j * 4..j * 4 + 4].try_into().unwrap();
            let d = sub(bezier(&row, ti), section(i, j));
            report.section_control_error_bound =
                report.section_control_error_bound.max(dot(d, d).sqrt());
        }
    }
    let roundoff = 128. * f64::EPSILON * net.iter().flatten().map(|v| v.abs()).fold(1., f64::max);
    // An explicit numerical allowance, not a formal interval-arithmetic certificate.
    report.section_control_error_bound += roundoff;
    if report.section_control_error_bound > tolerance {
        return Err(5);
    }
    // Qualify the ACTUAL stored net, including axial leakage from floating point.
    // For X=S.across, Z=S.axis: Xu >= 2*w*min_du_X,
    // Zv >= 3*min_dv_Z, |Xv| <= 3*max_dv_X, |Zu| <= 4*max_du_Z/w^2.
    // Positive Xu, Zv and determinant make this projected Jacobian a P-matrix.
    // Gale-Nikaido univalence on the parameter rectangle then gives injectivity;
    // a nonzero projected Jacobian also gives surface regularity. No mesh/sample
    // self-intersection test is substituted for these control-net inequalities.
    report.min_transverse_increment = f64::INFINITY;
    report.min_axial_increment = f64::INFINITY;
    let mut max_du_z: f64 = 0.;
    let mut max_dv_x: f64 = 0.;
    for j in 0..2 {
        for v in 0..4 {
            let delta = sub(net[(j + 1) * 4 + v], net[j * 4 + v]);
            report.min_transverse_increment = report
                .min_transverse_increment
                .min(dot(delta, across) - roundoff);
            max_du_z = max_du_z.max(dot(delta, axis).abs() + roundoff);
        }
    }
    for j in 0..3 {
        for v in 0..3 {
            let delta = sub(net[j * 4 + v + 1], net[j * 4 + v]);
            report.min_axial_increment =
                report.min_axial_increment.min(dot(delta, axis) - roundoff);
            max_dv_x = max_dv_x.max(dot(delta, across).abs() + roundoff);
        }
    }
    report.projected_jacobian_lower_bound =
        6. * w * report.min_transverse_increment * report.min_axial_increment
            - 12. * max_dv_x * max_du_z / (w * w);
    if report.min_transverse_increment <= tolerance * 10.
        || report.min_axial_increment <= tolerance * 10.
        || report.projected_jacobian_lower_bound <= tolerance * tolerance * 100.
    {
        return Err(6);
    }
    Ok((net, w, tolerance))
}

/// Packed V1 input is documented in the public header. No input is mutated.
/// # Safety
/// Input, output and report must refer to live caller-owned storage of the stated
/// lengths; writable spans must not overlap each other or the input.
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_sweep1_parallel_v1(
    input: *const f64,
    length: usize,
    output: *mut u8,
    capacity: usize,
    report: *mut SweepReport,
) -> i64 {
    if length > 65 || capacity > MAX_BUFFER_BYTES {
        return ERROR_LIMIT_EXCEEDED;
    }
    if !span_metadata_is_valid(input, length)
        || length == 0
        || !output_metadata_is_valid(output, capacity)
        || !span_metadata_is_valid(report, 1)
    {
        return ERROR_INVALID_ARGUMENT;
    }
    let spans = [
        (input.cast::<u8>(), length * 8),
        (output.cast_const(), capacity),
        (report.cast::<u8>().cast_const(), size_of::<SweepReport>()),
    ];
    if (0..3)
        .any(|i| (i + 1..3).any(|j| spans_overlap(spans[i].0, spans[i].1, spans[j].0, spans[j].1)))
    {
        return ERROR_INVALID_ARGUMENT;
    }
    let mut info = SweepReport {
        struct_size: size_of::<SweepReport>() as u32,
        version: 1,
        ..Default::default()
    };
    let result = catch_unwind(AssertUnwindSafe(|| {
        match construct(
            unsafe { std::slice::from_raw_parts(input, length) },
            &mut info,
        ) {
            Err(reason) => {
                info.reason = reason;
                ERROR_POLICY_REFUSED
            }
            Ok((net, w, tol)) => {
                let mut required = 0;
                let status = unsafe {
                    narwhal_occt_adapter_sweep_parallel(
                        net.as_ptr().cast(),
                        w,
                        tol,
                        output,
                        capacity,
                        &mut required,
                    )
                };
                if required > MAX_BUFFER_BYTES {
                    ERROR_LIMIT_EXCEEDED
                } else if status == ADAPTER_OK || status == ADAPTER_BUFFER_TOO_SMALL {
                    required as i64
                } else {
                    adapter_error(status)
                }
            }
        }
    }))
    .unwrap_or(ERROR_UNEXPECTED_FAILURE);
    info.status = if result < 0 { result as i32 } else { 0 };
    unsafe {
        ptr::write(report, info);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn interpolation_is_not_endpoint_only() {
        let t = [0., 0.4, 1.];
        let p = [[1., 2., 3.], [2., 4., 3.], [0.5, 3., 4.]];
        let net = interpolate(&t, &p);
        for (ti, pi) in t.iter().zip(p) {
            assert!(dot(sub(bezier(&net, *ti), pi), sub(bezier(&net, *ti), pi)) < 1.0e-25);
        }
    }
    #[test]
    fn abi_rejects_invalid_and_overlapping_spans() {
        let mut a = [0.; 65];
        let mut r = SweepReport::default();
        unsafe {
            assert_eq!(
                narwhal_geometry_core_sweep1_parallel_v1(
                    ptr::null(),
                    1,
                    ptr::null_mut(),
                    0,
                    &mut r
                ),
                -1
            );
            assert_eq!(
                narwhal_geometry_core_sweep1_parallel_v1(
                    a.as_ptr(),
                    65,
                    a.as_mut_ptr().cast(),
                    8,
                    &mut r
                ),
                -1
            );
            assert_eq!(
                narwhal_geometry_core_sweep1_parallel_v1(
                    a.as_ptr(),
                    66,
                    ptr::null_mut(),
                    0,
                    &mut r
                ),
                -2
            );
            assert_eq!(
                narwhal_geometry_core_sweep1_parallel_v1(
                    a.as_ptr(),
                    65,
                    ptr::null_mut(),
                    0,
                    &mut r
                ),
                -7
            );
            assert_eq!(r.reason, 1);
        }
    }
}
