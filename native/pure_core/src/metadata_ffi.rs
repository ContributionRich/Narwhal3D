//! Stateless bridges for reviewed metadata ports. Caller-owned buffers only.
use crate::{aci_colors, checked_slice, checked_slice_mut, printwidths};
const MAX_INPUT: usize = 1024 * 1024;
const MAX_OUTPUT: usize = 64 * 1024 * 1024;

/// Validated RGB -> low byte ACI index, bit8 exact. Zero means refusal.
#[no_mangle]
pub extern "C" fn narwhal_pure_core_rgb_to_aci(r: f64, g: f64, b: f64) -> u32 {
    let values = [r, g, b].map(aci_colors::Value::Float);
    match aci_colors::rgb_to_aci(aci_colors::Rgb::Items(&values)) {
        Ok((index, exact)) => u32::from(index) | (u32::from(exact) << 8),
        Err(_) => 0,
    }
}

/// Packed LE report: u32 width count, f64 widths, u32 warning count,
/// then (u32 UTF-8 byte length, bytes) per warning. No writes on short output.
/// Returns 0 on success, 1 for a size query/short buffer, -1 on invalid input.
/// # Safety
/// Spans must be valid caller-owned allocations; output and needed must not overlap.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_printwidths(
    input: *const u8,
    length: usize,
    output: *mut u8,
    capacity: usize,
    needed: *mut usize,
) -> i32 {
    if length > MAX_INPUT || capacity > MAX_OUTPUT {
        return -1;
    }
    let Some(needed) = (unsafe { checked_slice_mut(needed, 1) }) else {
        return -1;
    };
    let Some(bytes) = (unsafe { checked_slice(input, length) }) else {
        return -1;
    };
    let Ok(text) = std::str::from_utf8(bytes) else {
        return -1;
    };
    let report = printwidths::widths_from_text(text);
    let size =
        8 + 8 * report.widths.len() + report.warnings.iter().map(|w| 4 + w.len()).sum::<usize>();
    if size > MAX_OUTPUT {
        return -1;
    }
    needed[0] = size;
    if capacity < size {
        return 1;
    }
    let Some(out) = (unsafe { checked_slice_mut(output, capacity) }) else {
        return -1;
    };
    let mut encoded = Vec::with_capacity(size);
    encoded.extend_from_slice(&(report.widths.len() as u32).to_le_bytes());
    for width in report.widths {
        encoded.extend_from_slice(&width.to_le_bytes());
    }
    encoded.extend_from_slice(&(report.warnings.len() as u32).to_le_bytes());
    for warning in report.warnings {
        encoded.extend_from_slice(&(warning.len() as u32).to_le_bytes());
        encoded.extend_from_slice(warning.as_bytes());
    }
    out[..size].copy_from_slice(&encoded);
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn colors_keep_ties_exact_flag_and_refusals() {
        assert_eq!(narwhal_pure_core_rgb_to_aci(255.0, 0.0, 0.0), 257);
        assert_eq!(narwhal_pure_core_rgb_to_aci(160.0, 160.0, 160.0), 8);
        assert_eq!(narwhal_pure_core_rgb_to_aci(255.0, 127.5, 0.0), 1);
        assert_eq!(narwhal_pure_core_rgb_to_aci(f64::NAN, 0.0, 0.0), 0);
    }
    #[test]
    fn widths_size_query_short_buffer_and_utf8_are_checked() {
        let input = b"2\n1\n2\nNaN";
        let mut needed = 0;
        let mut buffer = [0x55u8; 128];
        unsafe {
            assert_eq!(
                narwhal_pure_core_printwidths(
                    input.as_ptr(),
                    input.len(),
                    buffer.as_mut_ptr(),
                    2,
                    &mut needed
                ),
                1
            );
            assert!(buffer.iter().all(|v| *v == 0x55));
            assert_eq!(
                narwhal_pure_core_printwidths(
                    input.as_ptr(),
                    input.len(),
                    buffer.as_mut_ptr(),
                    buffer.len(),
                    &mut needed
                ),
                0
            );
            assert_eq!(u32::from_le_bytes(buffer[..4].try_into().unwrap()), 2);
            assert_eq!(f64::from_le_bytes(buffer[4..12].try_into().unwrap()), 1.0);
            assert_eq!(f64::from_le_bytes(buffer[12..20].try_into().unwrap()), 2.0);
            assert_eq!(
                narwhal_pure_core_printwidths(
                    [0xff].as_ptr(),
                    1,
                    buffer.as_mut_ptr(),
                    buffer.len(),
                    &mut needed
                ),
                -1
            );
            assert_eq!(
                narwhal_pure_core_printwidths(
                    std::ptr::null(),
                    1,
                    buffer.as_mut_ptr(),
                    buffer.len(),
                    &mut needed
                ),
                -1
            );
            assert_eq!(
                narwhal_pure_core_printwidths(
                    input.as_ptr(),
                    input.len(),
                    buffer.as_mut_ptr(),
                    buffer.len(),
                    std::ptr::null_mut()
                ),
                -1
            );
        }
    }
}
