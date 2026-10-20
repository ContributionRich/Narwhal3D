//! Batched compatible NURBS control interpolation for TweenCurves.
const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
const MAX_VALUES: usize = 50_000_000;
/// Interpolate two packed xyzw control arrays at i/(number+1).
/// # Safety
/// Inputs are readable and output is writable for declared capacities; spans do not overlap.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_tween_controls(
    first: *const f64,
    second: *const f64,
    control_count: usize,
    number: usize,
    output: *mut f64,
    output_capacity: usize,
    out_values: *mut u64,
) -> i32 {
    if out_values.is_null() {
        return ERR_NULL;
    }
    *out_values = 0;
    if control_count == 0 || number == 0 {
        return ERR_INPUT;
    }
    let input_count = match control_count.checked_mul(4) {
        Some(v) => v,
        None => return ERR_SIZE,
    };
    let total = match input_count.checked_mul(number) {
        Some(v) if v <= MAX_VALUES => v,
        _ => return ERR_SIZE,
    };
    if output_capacity < total {
        return ERR_SIZE;
    }
    let a = match crate::checked_slice(first, input_count) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    let b = match crate::checked_slice(second, input_count) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    if a.iter().chain(b).any(|v| !v.is_finite())
        || a.chunks_exact(4)
            .chain(b.chunks_exact(4))
            .any(|p| p[3].abs() <= 1e-15)
    {
        return ERR_INPUT;
    }
    let out = match crate::checked_slice_mut(output, output_capacity) {
        Some(v) => v,
        None => return ERR_NULL,
    };
    for curve in 0..number {
        let t = (curve + 1) as f64 / (number + 1) as f64;
        let base = curve * input_count;
        for i in 0..input_count {
            out[base + i] = a[i] + t * (b[i] - a[i]);
        }
    }
    *out_values = total as u64;
    0
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fractions_are_strictly_interior() {
        let a = [0., 0., 0., 1.];
        let b = [0., 10., 0., 1.];
        let mut out = [0.; 12];
        let mut used = 0;
        let rc = unsafe {
            narwhal_pure_core_tween_controls(
                a.as_ptr(),
                b.as_ptr(),
                1,
                3,
                out.as_mut_ptr(),
                out.len(),
                &mut used,
            )
        };
        assert_eq!(rc, 0);
        assert_eq!(used, 12);
        assert_eq!([out[1], out[5], out[9]], [2.5, 5., 7.5]);
    }
}
