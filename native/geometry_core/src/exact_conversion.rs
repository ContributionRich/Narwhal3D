//! SDK conversion boundary: exact elementary support construction at shifted U seams.
//! Raw BinTools callers must isolate untrusted decoding; live SDK handles are serialized by the caller.
use crate::*;

extern "C" {
    fn narwhal_occt_adapter_nurbs_convert_seam_safe(
        input: *const u8,
        length: usize,
        output: *mut u8,
        capacity: usize,
        required: *mut usize,
    ) -> i32;
}

/// NURBS conversion without OCCT's periodic/angular seam segmentation defect.
/// # Safety
/// Input/output spans must be live, caller-owned, readable/writable and disjoint.
#[no_mangle]
pub unsafe extern "C" fn narwhal_geometry_core_nurbs_convert_seam_safe_v1(
    input: *const u8,
    length: usize,
    output: *mut u8,
    capacity: usize,
) -> i64 {
    if length == 0 || length > MAX_BUFFER_BYTES || capacity > MAX_BUFFER_BYTES {
        return ERROR_LIMIT_EXCEEDED;
    }
    if !span_metadata_is_valid(input, length)
        || (capacity != 0 && !span_metadata_is_valid(output, capacity))
        || spans_overlap(input, length, output, capacity)
    {
        return ERROR_INVALID_ARGUMENT;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let mut required = 0;
        let status = unsafe {
            narwhal_occt_adapter_nurbs_convert_seam_safe(
                input,
                length,
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

#[cfg(test)]
mod tests {
    use super::*;
    extern "C" {
        fn narwhal_occt_adapter_conversion_fixture(
            kind: u32,
            output: *mut u8,
            capacity: usize,
            required: *mut usize,
        ) -> i32;
        fn narwhal_occt_adapter_conversion_corner_error(
            input: *const u8,
            length: usize,
            converted: *const u8,
            converted_length: usize,
            error: *mut f64,
        ) -> i32;
    }
    #[test]
    fn angular_endpoint_loci_are_preserved_for_shifted_elementary_surfaces() {
        for kind in 0..4 {
            let mut n = 0;
            assert_eq!(
                unsafe {
                    narwhal_occt_adapter_conversion_fixture(kind, ptr::null_mut(), 0, &mut n)
                },
                1
            );
            let mut source = vec![0; n];
            assert_eq!(
                unsafe {
                    narwhal_occt_adapter_conversion_fixture(kind, source.as_mut_ptr(), n, &mut n)
                },
                0
            );
            let original = source.clone();
            let n = unsafe {
                narwhal_geometry_core_nurbs_convert_seam_safe_v1(
                    source.as_ptr(),
                    source.len(),
                    ptr::null_mut(),
                    0,
                )
            };
            assert!(n > 0, "fixture {kind}: {n}");
            let mut output = vec![0; n as usize];
            assert_eq!(
                unsafe {
                    narwhal_geometry_core_nurbs_convert_seam_safe_v1(
                        source.as_ptr(),
                        source.len(),
                        output.as_mut_ptr(),
                        output.len(),
                    )
                },
                n
            );
            let mut error = 0.;
            assert_eq!(
                unsafe {
                    narwhal_occt_adapter_conversion_corner_error(
                        source.as_ptr(),
                        source.len(),
                        output.as_ptr(),
                        output.len(),
                        &mut error,
                    )
                },
                0
            );
            assert!(error < 1e-12, "fixture {kind}: changed seam locus {error}");
            assert_eq!(source, original);
        }
    }
    #[test]
    fn transport_rejects_overlapping_null_and_oversize_spans() {
        let mut data = [0x5a; 8];
        unsafe {
            assert_eq!(
                narwhal_geometry_core_nurbs_convert_seam_safe_v1(
                    ptr::null(),
                    1,
                    ptr::null_mut(),
                    0
                ),
                ERROR_INVALID_ARGUMENT
            );
            assert_eq!(
                narwhal_geometry_core_nurbs_convert_seam_safe_v1(
                    ptr::null(),
                    MAX_BUFFER_BYTES + 1,
                    ptr::null_mut(),
                    0
                ),
                ERROR_LIMIT_EXCEEDED
            );
            assert_eq!(
                narwhal_geometry_core_nurbs_convert_seam_safe_v1(
                    data.as_ptr(),
                    data.len(),
                    data.as_mut_ptr(),
                    data.len()
                ),
                ERROR_INVALID_ARGUMENT
            );
        }
        assert_eq!(data, [0x5a; 8]);
    }
}
