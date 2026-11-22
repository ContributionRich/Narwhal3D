//! OURS: bounded exact planar closure. No healing, extension or faceted fallback.
//! Post-decode budgets are not decoder allocation limits: callers must put
//! untrusted BinTools reads/verification under a hard memory/time supervisor.
//! The application entry point always does so through cap_worker.py.
use crate::*;

pub const CAPABILITY: u64 = 1 << 10;

// Cap has no fixed document-unit ceiling. Squared-distance arithmetic must
// remain representable; actual geometric scale admission belongs to OCCT below.
fn cap_tolerance_is_valid(tolerance: f64) -> bool {
    tolerance.is_finite() && tolerance >= 1e-9 && (tolerance * tolerance).is_finite()
}

extern "C" {
    fn narwhal_occt_adapter_cap_exact(
        input: *const u8,
        length: usize,
        tolerance: f64,
        output: *mut u8,
        capacity: usize,
        required: *mut usize,
    ) -> i32;
}

/// Add only planar lids; the supplied tolerance is the document absolute tolerance,
/// used without multiplication. Existing exact surfaces and trim domains are retained.
/// Returns byte count, or an existing negative core error. Buffer queries return
/// the required count; errors never publish partial output.
/// # Safety
/// Spans must be live caller-owned allocations and must not overlap.
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_cap_exact_v1(
    input: *const u8,
    length: usize,
    tolerance: f64,
    output: *mut u8,
    capacity: usize,
) -> i64 {
    if length == 0 || length > MAX_BUFFER_BYTES || capacity > MAX_BUFFER_BYTES {
        return ERROR_LIMIT_EXCEEDED;
    }
    if !span_metadata_is_valid(input, length)
        || (capacity != 0 && !span_metadata_is_valid(output, capacity))
        || spans_overlap(input, length, output, capacity)
        || !cap_tolerance_is_valid(tolerance)
    {
        return ERROR_INVALID_ARGUMENT;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let mut required = 0;
        let status = unsafe {
            narwhal_occt_adapter_cap_exact(
                input,
                length,
                tolerance,
                output,
                capacity,
                &mut required,
            )
        };
        if status == ADAPTER_OK || (status == ADAPTER_BUFFER_TOO_SMALL && capacity == 0) {
            if required > MAX_BUFFER_BYTES {
                ERROR_LIMIT_EXCEEDED
            } else {
                required as i64
            }
        } else if status == ADAPTER_BUFFER_TOO_SMALL {
            ERROR_BUFFER_TOO_SMALL
        } else {
            adapter_error(status)
        }
    }))
    .unwrap_or(ERROR_UNEXPECTED_FAILURE)
}

extern "C" {
    fn narwhal_occt_adapter_verify_cap(
        input: *const u8,
        length: usize,
        snapshot: *const u8,
        snapshot_length: usize,
        target: *const u8,
        target_length: usize,
        tolerance: f64,
    ) -> i32;
}
/// Verify the source snapshot AND the frozen exact result against a rebuilt tree.
/// # Safety
/// All three spans must be live readable caller-owned allocations.
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_verify_cap_v1(
    input: *const u8,
    length: usize,
    snapshot: *const u8,
    snapshot_length: usize,
    target: *const u8,
    target_length: usize,
    tolerance: f64,
) -> i32 {
    for n in [length, snapshot_length, target_length] {
        if n == 0 || n > MAX_BUFFER_BYTES {
            return ERROR_LIMIT_EXCEEDED as i32;
        }
    }
    if !span_metadata_is_valid(input, length)
        || !span_metadata_is_valid(snapshot, snapshot_length)
        || !span_metadata_is_valid(target, target_length)
        || !cap_tolerance_is_valid(tolerance)
    {
        return ERROR_INVALID_ARGUMENT as i32;
    }
    catch_unwind(AssertUnwindSafe(|| unsafe {
        narwhal_occt_adapter_verify_cap(
            input,
            length,
            snapshot,
            snapshot_length,
            target,
            target_length,
            tolerance,
        )
    }))
    .unwrap_or(ERROR_UNEXPECTED_FAILURE as i32)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn transport_refuses_without_dereferencing_loose_metadata() {
        unsafe {
            assert_eq!(
                narwhal_geometry_core_cap_exact_v1(ptr::null(), 1, 1e-6, ptr::null_mut(), 0),
                ERROR_INVALID_ARGUMENT
            );
            assert_eq!(
                narwhal_geometry_core_cap_exact_v1(
                    ptr::null(),
                    MAX_BUFFER_BYTES + 1,
                    1e-6,
                    ptr::null_mut(),
                    0
                ),
                ERROR_LIMIT_EXCEEDED
            );
            let bytes = [0u8; 16];
            for tolerance in [0., -1., f64::NAN, f64::INFINITY, 1e200] {
                assert_eq!(
                    narwhal_geometry_core_cap_exact_v1(
                        bytes.as_ptr(),
                        bytes.len(),
                        tolerance,
                        ptr::null_mut(),
                        0
                    ),
                    ERROR_INVALID_ARGUMENT
                );
            }
            assert_eq!(
                narwhal_geometry_core_cap_exact_v1(
                    bytes.as_ptr(),
                    bytes.len(),
                    1e-6,
                    bytes.as_ptr() as *mut u8,
                    bytes.len()
                ),
                ERROR_INVALID_ARGUMENT
            );
            assert_eq!(
                narwhal_geometry_core_verify_cap_v1(
                    ptr::null(),
                    1,
                    ptr::null(),
                    1,
                    ptr::null(),
                    1,
                    1e-6
                ),
                ERROR_INVALID_ARGUMENT as i32
            );
        }
    }
}

#[cfg(test)]
mod exact_tests {
    use super::*;
    extern "C" {
        fn narwhal_occt_adapter_cap_fixture(
            kind: u32,
            output: *mut u8,
            capacity: usize,
            required: *mut usize,
        ) -> i32;
        fn narwhal_occt_adapter_cap_stats(input: *const u8, length: usize, stats: *mut f64) -> i32;
    }
    fn fixture(kind: u32) -> Vec<u8> {
        let mut n = 0;
        assert_eq!(
            unsafe { narwhal_occt_adapter_cap_fixture(kind, ptr::null_mut(), 0, &mut n) },
            1
        );
        let mut b = vec![0; n];
        assert_eq!(
            unsafe { narwhal_occt_adapter_cap_fixture(kind, b.as_mut_ptr(), n, &mut n) },
            0
        );
        b
    }
    fn cap(b: &[u8], t: f64) -> Result<Vec<u8>, i64> {
        let n = unsafe {
            narwhal_geometry_core_cap_exact_v1(b.as_ptr(), b.len(), t, ptr::null_mut(), 0)
        };
        if n < 0 {
            return Err(n);
        }
        let mut out = vec![0; n as usize];
        assert_eq!(
            unsafe {
                narwhal_geometry_core_cap_exact_v1(
                    b.as_ptr(),
                    b.len(),
                    t,
                    out.as_mut_ptr(),
                    out.len(),
                )
            },
            n
        );
        Ok(out)
    }
    #[test]
    fn cylinder_and_annular_bore_exact_volumes_and_frozen_binding() {
        for (kind, faces, volume) in [
            (0, 3., 45. * std::f64::consts::PI),
            (1, 4., 40. * std::f64::consts::PI),
            (3, 3., 45. * std::f64::consts::PI),
        ] {
            let source = fixture(kind);
            let before = source.clone();
            let out = cap(&source, 1e-6).unwrap();
            let mut stats = [0.; 3];
            assert_eq!(
                unsafe {
                    narwhal_occt_adapter_cap_stats(out.as_ptr(), out.len(), stats.as_mut_ptr())
                },
                0
            );
            assert_eq!(stats[0], faces);
            assert!((stats[1] - volume).abs() < 1e-9);
            assert_eq!(stats[2], 1.);
            assert_eq!(source, before);
            assert_eq!(
                unsafe {
                    narwhal_geometry_core_verify_cap_v1(
                        source.as_ptr(),
                        source.len(),
                        source.as_ptr(),
                        source.len(),
                        out.as_ptr(),
                        out.len(),
                        1e-6,
                    )
                },
                0
            );
            let wrong = fixture(2);
            assert_eq!(
                unsafe {
                    narwhal_geometry_core_verify_cap_v1(
                        source.as_ptr(),
                        source.len(),
                        wrong.as_ptr(),
                        wrong.len(),
                        out.as_ptr(),
                        out.len(),
                        1e-6,
                    )
                },
                ERROR_POLICY_REFUSED as i32
            );
        }
    }
    #[test]
    fn pcurves_occurrences_and_all_binding_operands_are_admitted_before_work() {
        let source = fixture(0);
        let output = cap(&source, 1e-6).unwrap();
        for kind in [5, 7, 8, 9, 10, 11, 12, 13] {
            let huge = fixture(kind);
            assert_eq!(
                cap(&huge, 1e-6),
                Err(ERROR_LIMIT_EXCEEDED),
                "fixture {kind}"
            );
            for slot in 0..3 {
                let operands = [
                    if slot == 0 { &huge } else { &source },
                    if slot == 1 { &huge } else { &source },
                    if slot == 2 { &huge } else { &output },
                ];
                assert_eq!(
                    unsafe {
                        narwhal_geometry_core_verify_cap_v1(
                            operands[0].as_ptr(),
                            operands[0].len(),
                            operands[1].as_ptr(),
                            operands[1].len(),
                            operands[2].as_ptr(),
                            operands[2].len(),
                            1e-6,
                        )
                    },
                    ERROR_LIMIT_EXCEEDED as i32,
                    "fixture {kind} slot {slot}"
                );
            }
        }
    }

    #[test]
    fn refuses_nonplanarity_resource_overrun_and_explicit_tolerance_budget() {
        assert_eq!(cap(&fixture(2), 1e-6), Err(ERROR_POLICY_REFUSED));
        assert_eq!(cap(&fixture(5), 1e-6), Err(ERROR_LIMIT_EXCEEDED));
        assert_eq!(cap(&fixture(6), 1e-6), Err(ERROR_POLICY_REFUSED));
        assert_eq!(cap(&fixture(7), 1e-6), Err(ERROR_LIMIT_EXCEEDED));
        let source = fixture(4);
        assert!(cap(&source, 0.01).is_ok());
        assert_eq!(cap(&source, 0.001), Err(ERROR_POLICY_REFUSED));
        let source = fixture(0);
        let mut out = [0x5a; 8];
        assert_eq!(
            unsafe {
                narwhal_geometry_core_cap_exact_v1(
                    source.as_ptr(),
                    source.len(),
                    1e-6,
                    out.as_mut_ptr(),
                    out.len(),
                )
            },
            ERROR_BUFFER_TOO_SMALL
        );
        assert_eq!(out, [0x5a; 8]);
    }
}
