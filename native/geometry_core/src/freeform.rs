//! Bounded Freeform sweep coordination; exact local sections, fitted OCCT transport.
use crate::*;

pub const CAPABILITY: u64 = 1 << 7;
const MAX_INPUT: usize = 16 * 1024 * 1024;

#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct FreeformReport {
    pub struct_size: u32,
    pub version: u32,
    pub status: i32,
    pub reason: u32,
    pub samples: u32,
    pub patches: u32,
    pub proof_cells: u32,
    pub continuity: u32,
    pub fit_error: f64,
    pub section_error: f64,
    pub rail_error: f64,
    pub law_error: f64,
    pub min_cell_determinant: f64,
}

type Interpolate = unsafe extern "C" fn(*const f64, *const f64, usize, *mut f64) -> i32;
type Qualify = unsafe extern "C" fn(
    *const f64,
    *const f64,
    usize,
    *const f64,
    *const f64,
    *mut FreeformReport,
) -> i32;
extern "C" {
    fn narwhal_occt_adapter_freeform(
        input: *const u8,
        length: usize,
        stations: *const f64,
        count: usize,
        tolerance: f64,
        interpolate: Interpolate,
        qualify: Qualify,
        output: *mut u8,
        capacity: usize,
        required: *mut usize,
        report: *mut FreeformReport,
    ) -> i32;
}

unsafe extern "C" fn interpolate(
    local: *const f64,
    stations: *const f64,
    n: usize,
    output: *mut f64,
) -> i32 {
    catch_unwind(AssertUnwindSafe(|| {
        if !(2..=4).contains(&n) {
            return -1;
        }
        let data = unsafe { std::slice::from_raw_parts(local, n * 9) };
        let t = unsafe { std::slice::from_raw_parts(stations, n) };
        if data.iter().chain(t).any(|v| !v.is_finite()) {
            return -1;
        }
        let out = unsafe { std::slice::from_raw_parts_mut(output, 36) };
        for u in 0..3 {
            let values: Vec<[f64; 3]> = (0..n)
                .map(|i| std::array::from_fn(|k| data[9 * i + 3 * u + k]))
                .collect();
            let controls = crate::surfacing::interpolate(t, &values);
            for v in 0..4 {
                for k in 0..3 {
                    out[(u * 4 + v) * 3 + k] = controls[v][k];
                }
            }
        }
        if out.iter().any(|v| !v.is_finite()) {
            return -1;
        }
        0
    }))
    .unwrap_or(-6)
}

unsafe extern "C" fn qualify(
    poles: *const f64,
    weights: *const f64,
    degree: usize,
    axis: *const f64,
    across: *const f64,
    report: *mut FreeformReport,
) -> i32 {
    catch_unwind(AssertUnwindSafe(|| {
        if !(2..=12).contains(&degree) {
            return -7;
        }
        let count = 3 * (degree + 1);
        let poles = unsafe { std::slice::from_raw_parts(poles.cast::<[f64; 3]>(), count) };
        let weights = unsafe { std::slice::from_raw_parts(weights, count) };
        let axis = unsafe { *axis.cast::<[f64; 3]>() };
        let across = unsafe { *across.cast::<[f64; 3]>() };
        match crate::freeform_math::qualify_patch(poles, weights, degree, axis, across) {
            Ok(q) => {
                let r = unsafe { &mut *report };
                r.proof_cells += q.cells;
                r.min_cell_determinant = r.min_cell_determinant.min(q.min_determinant);
                0
            }
            Err(_) => -7,
        }
    }))
    .unwrap_or(-6)
}

/// Exact BinTools compound: rail wire first, then ordered section wires.
/// `stations` contains (native rail parameter, reverse 0/1) pairs.
/// # Safety
/// Caller supplies live, correctly sized spans; writable spans must not overlap.
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_sweep1_freeform_approx_v1(
    input: *const u8,
    length: usize,
    stations: *const f64,
    count: usize,
    tolerance: f64,
    output: *mut u8,
    capacity: usize,
    report: *mut FreeformReport,
) -> i64 {
    if length > MAX_INPUT || capacity > MAX_BUFFER_BYTES {
        return ERROR_LIMIT_EXCEEDED;
    }
    if length == 0
        || !(2..=4).contains(&count)
        || !span_metadata_is_valid(input, length)
        || !span_metadata_is_valid(stations, 2 * count)
        || !span_metadata_is_valid(report, 1)
        || !output_metadata_is_valid(output, capacity)
    {
        return ERROR_INVALID_ARGUMENT;
    }
    let spans = [
        (input, length),
        (stations.cast(), 16 * count),
        (output.cast_const(), capacity),
        (
            report.cast::<u8>().cast_const(),
            size_of::<FreeformReport>(),
        ),
    ];
    if (0..4)
        .any(|i| (i + 1..4).any(|j| spans_overlap(spans[i].0, spans[i].1, spans[j].0, spans[j].1)))
    {
        return ERROR_INVALID_ARGUMENT;
    }
    let mut info = FreeformReport {
        struct_size: size_of::<FreeformReport>() as u32,
        version: 1,
        min_cell_determinant: f64::INFINITY,
        ..Default::default()
    };
    let result = catch_unwind(AssertUnwindSafe(|| {
        let values = unsafe { std::slice::from_raw_parts(stations, 2 * count) };
        if !tolerance.is_finite()
            || !(1e-9..=1e-3).contains(&tolerance)
            || values.iter().any(|v| !v.is_finite())
            || values.chunks_exact(2).any(|p| p[1] != 0. && p[1] != 1.)
        {
            info.reason = 1;
            return ERROR_POLICY_REFUSED;
        }
        let range = values[2 * (count - 1)] - values[0];
        if !range.is_finite()
            || range <= 0.
            || values
                .chunks_exact(2)
                .collect::<Vec<_>>()
                .windows(2)
                .any(|p| (p[1][0] - p[0][0]) / range < 0.05)
        {
            info.reason = 3;
            return ERROR_POLICY_REFUSED;
        }
        let mut required = 0;
        let status = unsafe {
            narwhal_occt_adapter_freeform(
                input,
                length,
                stations,
                count,
                tolerance,
                interpolate,
                qualify,
                output,
                capacity,
                &mut required,
                &mut info,
            )
        };
        if required > MAX_BUFFER_BYTES {
            ERROR_LIMIT_EXCEEDED
        } else if status == ADAPTER_OK || status == ADAPTER_BUFFER_TOO_SMALL {
            required as i64
        } else {
            adapter_error(status)
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
    fn overflowing_finite_inputs_fail_before_occt() {
        let source = [0_u8; 8];
        let t = [-1e308, 0., 1e308, 0.];
        let mut report = FreeformReport::default();
        let stations = [0., 0.5, 1.];
        let mut local = [0.; 27];
        local[9] = 1e308;
        let mut output = [0.; 36];
        unsafe {
            assert_eq!(
                narwhal_geometry_core_sweep1_freeform_approx_v1(
                    source.as_ptr(),
                    8,
                    t.as_ptr(),
                    2,
                    1e-7,
                    ptr::null_mut(),
                    0,
                    &mut report
                ),
                -7
            );
            assert_eq!(report.reason, 3);
            assert_eq!(
                interpolate(local.as_ptr(), stations.as_ptr(), 3, output.as_mut_ptr()),
                -1
            );
        }
    }

    #[test]
    fn preserves_layout_and_rejects_bad_public_spans() {
        assert_eq!(size_of::<FreeformReport>(), 72);
        let source = [0_u8; 8];
        let t = [0., 0., 0.5, 0., 1., 0.];
        let mut r = FreeformReport::default();
        unsafe {
            assert_eq!(
                narwhal_geometry_core_sweep1_freeform_approx_v1(
                    ptr::null(),
                    1,
                    t.as_ptr(),
                    3,
                    1e-7,
                    ptr::null_mut(),
                    0,
                    &mut r
                ),
                -1
            );
            assert_eq!(
                narwhal_geometry_core_sweep1_freeform_approx_v1(
                    source.as_ptr(),
                    8,
                    t.as_ptr(),
                    3,
                    f64::NAN,
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
