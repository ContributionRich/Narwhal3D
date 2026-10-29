//! Physical station preparation: exact OCCT candidates, Rust admission/ambiguity policy.
use crate::*;

pub const CAPABILITY: u64 = 1 << 8;
const MAX_INPUT: usize = 16 * 1024 * 1024;
const CANDIDATES: usize = 64;
const STRIDE: usize = 6;

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct StationReport {
    pub struct_size: u32,
    pub version: u32,
    pub status: i32,
    pub reason: u32,
    pub count: u32,
    pub reserved: u32,
    pub first: f64,
    pub last: f64,
    pub parameters: [f64; 4],
    pub points: [[f64; 3]; 4],
    pub max_error: f64,
}

extern "C" {
    fn narwhal_occt_adapter_prepare_stations(
        input: *const u8,
        length: usize,
        points: *const f64,
        reverse: *const u8,
        count: usize,
        domain: *mut f64,
        candidates: *mut f64,
        counts: *mut u32,
        reason: *mut u32,
    ) -> i32;
}

fn resolve(
    candidates: &[f64],
    count: usize,
    tolerance: f64,
    point: [f64; 3],
    domain: [f64; 2],
) -> Result<[f64; STRIDE], u32> {
    if count > CANDIDATES {
        return Err(8);
    }
    let param_epsilon = 64. * f64::EPSILON * domain[0].abs().max(domain[1].abs()).max(1.);
    let mut chosen: Option<[f64; STRIDE]> = None;
    for row in candidates.chunks_exact(STRIDE).take(count) {
        if row.iter().any(|x| !x.is_finite())
            || row[1] < 0.
            || row[5] < 0.
            || row[0] < domain[0]
            || row[0] > domain[1]
        {
            return Err(1);
        }
        if row[1] > tolerance {
            continue;
        }
        if let Some(previous) = chosen {
            if (previous[0] - row[0]).abs() > param_epsilon {
                return Err(6);
            }
            // Endpoints arrive first. Retain their exact bound parameter rather
            // than a numerically nearby stationary solution from the projector.
        } else {
            chosen = Some(row.try_into().unwrap());
        }
    }
    let chosen = chosen.ok_or(7_u32)?;
    let magnitude = point[0].hypot(point[1]).hypot(point[2]);
    if !magnitude.is_finite() {
        return Err(1);
    }
    let placement = (128. * f64::EPSILON * magnitude.max(1.)).max(1e-12);
    if chosen[1] > placement || chosen[5] > placement {
        return Err(5);
    }
    Ok(chosen)
}

/// Map physical points onto an exact serialized rail without assuming affine
/// correspondence to the source application's curve parameters.
/// # Safety
/// All spans must refer to live caller-owned storage; report must not overlap inputs.
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_prepare_freeform_v1(
    input: *const u8,
    length: usize,
    points: *const f64,
    reverse: *const u8,
    count: usize,
    tolerance: f64,
    report: *mut StationReport,
) -> i32 {
    if length > MAX_INPUT {
        return ERROR_LIMIT_EXCEEDED as i32;
    }
    if length == 0
        || !(2..=4).contains(&count)
        || !span_metadata_is_valid(input, length)
        || !span_metadata_is_valid(points, count * 3)
        || !span_metadata_is_valid(reverse, count)
        || !span_metadata_is_valid(report, 1)
    {
        return ERROR_INVALID_ARGUMENT as i32;
    }
    let out = report.cast::<u8>().cast_const();
    for (p, n) in [
        (input, length),
        (points.cast(), count * 3 * 8),
        (reverse, count),
    ] {
        if spans_overlap(p, n, out, size_of::<StationReport>()) {
            return ERROR_INVALID_ARGUMENT as i32;
        }
    }
    let mut info = StationReport {
        struct_size: size_of::<StationReport>() as u32,
        version: 1,
        count: count as u32,
        ..Default::default()
    };
    let result = catch_unwind(AssertUnwindSafe(|| {
        let points = unsafe { std::slice::from_raw_parts(points, count * 3) };
        let reverse = unsafe { std::slice::from_raw_parts(reverse, count) };
        if !tolerance.is_finite()
            || !(1e-9..=1e-3).contains(&tolerance)
            || points.iter().any(|p| !p.is_finite())
            || reverse.iter().any(|r| *r > 1)
        {
            info.reason = 1;
            return ERROR_POLICY_REFUSED as i32;
        }
        let mut candidates = [0.; 4 * CANDIDATES * STRIDE];
        let mut counts = [0_u32; 4];
        let mut domain = [0.; 2];
        let status = unsafe {
            narwhal_occt_adapter_prepare_stations(
                input,
                length,
                points.as_ptr(),
                reverse.as_ptr(),
                count,
                domain.as_mut_ptr(),
                candidates.as_mut_ptr(),
                counts.as_mut_ptr(),
                &mut info.reason,
            )
        };
        if status != 0 {
            return adapter_error(status) as i32;
        }
        info.first = domain[0];
        info.last = domain[1];
        let width = domain[1] - domain[0];
        if !width.is_finite() || width <= 0. || domain.iter().any(|p| !p.is_finite()) {
            info.reason = 3;
            return ERROR_POLICY_REFUSED as i32;
        }
        for i in 0..count {
            let point = [points[i * 3], points[i * 3 + 1], points[i * 3 + 2]];
            match resolve(
                &candidates[i * CANDIDATES * STRIDE..(i + 1) * CANDIDATES * STRIDE],
                counts[i] as usize,
                tolerance,
                point,
                domain,
            ) {
                Ok(row) => {
                    info.parameters[i] = row[0];
                    info.points[i] = [row[2], row[3], row[4]];
                    info.max_error = info.max_error.max(row[1]).max(row[5]);
                }
                Err(reason) => {
                    info.reason = reason;
                    return ERROR_POLICY_REFUSED as i32;
                }
            }
        }
        if info.parameters[0] != domain[0]
            || info.parameters[count - 1] != domain[1]
            || info.parameters[..count]
                .windows(2)
                .any(|p| (p[1] - p[0]) / width < 0.05)
        {
            info.reason = 3;
            return ERROR_POLICY_REFUSED as i32;
        }
        0
    }))
    .unwrap_or(ERROR_UNEXPECTED_FAILURE as i32);
    info.status = result;
    if result != 0 {
        info.parameters = [0.; 4];
        info.points = [[0.; 3]; 4];
    }
    unsafe {
        ptr::write(report, info);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unique_physical_parameter_not_nearest_only() {
        let good = [
            0.25,
            0.,
            1.,
            2.,
            3.,
            0.,
            0.25 + 1e-15,
            1e-15,
            1.,
            2.,
            3.,
            0.,
        ];
        assert_eq!(
            resolve(&good, 2, 1e-7, [1., 2., 3.], [0., 1.]).unwrap()[0],
            0.25
        );
        let crossing = [0.25, 0., 1., 2., 3., 0., 0.75, 1e-10, 1., 2., 3., 0.];
        assert_eq!(resolve(&crossing, 2, 1e-7, [1., 2., 3.], [0., 1.]), Err(6));
        assert_eq!(
            resolve(
                &[0.25, 0.1, 1., 2., 3., 0.],
                1,
                1e-7,
                [1., 2., 3.],
                [0., 1.]
            ),
            Err(7)
        );
        assert_eq!(
            resolve(
                &[0.25, 1e-8, 1., 2., 3., 0.],
                1,
                1e-7,
                [1., 2., 3.],
                [0., 1.]
            ),
            Err(5)
        );
    }
    #[test]
    fn abi_layout_and_bad_spans() {
        assert_eq!(size_of::<StationReport>(), 176);
        let mut report = StationReport::default();
        unsafe {
            assert_eq!(
                narwhal_geometry_core_prepare_freeform_v1(
                    ptr::null(),
                    1,
                    ptr::null(),
                    ptr::null(),
                    3,
                    1e-7,
                    &mut report
                ),
                -1
            );
        }
    }
}
