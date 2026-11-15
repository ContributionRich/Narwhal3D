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
/// Owned Tween v1: mode 0 homogeneous SDK controls -> homogeneous controls;
/// mode 1 Euclidean xyzw -> Euclidean xyzw; mode 2 xyz samples -> xyz samples.
/// Mode 0 flags bits 0/1 mark rational first/second controls. Other modes use 0.
/// Error -10: zero source weight; -11: invalid interpolated control; -12:
/// nonfinite source; -13: resource limit. ABI errors retain -1/-2/-3.
/// All errors leave output AND out_values unchanged. No partially valid curve
/// plan can escape on subtraction, interpolation, or rehomogenization overflow.
/// # Safety
/// Caller provides valid nonoverlapping spans for the declared sizes.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_tween_plan_v1(
    first: *const f64,
    second: *const f64,
    point_count: usize,
    number: usize,
    mode: u32,
    flags: u32,
    output: *mut f64,
    capacity: usize,
    out_values: *mut u64,
) -> i32 {
    if out_values.is_null() {
        return ERR_NULL;
    }
    if point_count == 0 || number == 0 || mode > 2 || flags > 3 || (mode != 0 && flags != 0) {
        return ERR_INPUT;
    }
    let width = if mode == 2 { 3 } else { 4 };
    let Some(count) = point_count.checked_mul(width) else {
        return -13;
    };
    let Some(total) = count.checked_mul(number).filter(|n| *n <= MAX_VALUES) else {
        return -13;
    };
    if capacity < total {
        return ERR_SIZE;
    }
    let Some(a) = crate::checked_slice(first, count) else {
        return ERR_NULL;
    };
    let Some(b) = crate::checked_slice(second, count) else {
        return ERR_NULL;
    };
    let Some(out) = crate::checked_slice_mut(output, total) else {
        return ERR_NULL;
    };
    let normalize = |raw: &[f64], rational: bool| -> Result<Vec<f64>, i32> {
        let mut values = raw.to_vec();
        for p in values.chunks_exact_mut(4) {
            let w = if rational { p[3] } else { 1.0 };
            if w.abs() <= 1e-15 {
                return Err(-10);
            }
            p[0] /= w;
            p[1] /= w;
            p[2] /= w;
            p[3] = w;
        }
        if values.iter().any(|x| !x.is_finite()) {
            return Err(-12);
        }
        Ok(values)
    };
    let (a, b) = if mode == 0 {
        let a = match normalize(a, flags & 1 != 0) {
            Ok(v) => v,
            Err(e) => return e,
        };
        let b = match normalize(b, flags & 2 != 0) {
            Ok(v) => v,
            Err(e) => return e,
        };
        (a, b)
    } else {
        if a.iter().chain(b).any(|x| !x.is_finite()) {
            return -12;
        }
        if mode == 1
            && a.chunks_exact(4)
                .chain(b.chunks_exact(4))
                .any(|p| p[3].abs() <= 1e-15)
        {
            return -10;
        }
        (a.to_vec(), b.to_vec())
    };
    let mut result = Vec::with_capacity(total);
    for curve in 0..number {
        // Deliberately separate subtract/multiply/add, matching CPython;
        // do not replace with a weighted sum or fused mul_add.
        let t = (curve + 1) as f64 / (number as f64 + 1.0);
        let start = result.len();
        for i in 0..count {
            result.push(a[i] + t * (b[i] - a[i]));
        }
        let row = &mut result[start..];
        if row.iter().any(|x| !x.is_finite()) {
            return -11;
        }
        if mode != 2 {
            for p in row.chunks_exact_mut(4) {
                let w = p[3];
                if w.abs() <= 1e-15 {
                    return -11;
                }
                if mode == 0 {
                    p[0] *= w;
                    p[1] *= w;
                    p[2] *= w;
                    if p.iter().any(|x| !x.is_finite()) {
                        return -11;
                    }
                }
            }
        }
    }
    out.copy_from_slice(&result);
    *out_values = total as u64;
    0
}

/// Compare already domain-normalized SDK knot vectors, preserving the original
/// relative tolerance expression. Nonfinite knots are unsafe geometry.
/// # Safety
/// Both pointers are readable for count f64 values.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_tween_knots_v1(
    a: *const f64,
    b: *const f64,
    count: usize,
) -> i32 {
    if count == 0 || count > MAX_VALUES {
        return ERR_INPUT;
    }
    let Some(a) = crate::checked_slice(a, count) else {
        return ERR_NULL;
    };
    let Some(b) = crate::checked_slice(b, count) else {
        return ERR_NULL;
    };
    if a.iter().chain(b).any(|x| !x.is_finite()) {
        return -12;
    }
    if a.iter()
        .zip(b)
        .any(|(&x, &y)| (x - y).abs() > 1e-10 * 1.0f64.max(x.abs()).max(y.abs()))
    {
        return -20;
    }
    0
}

/// Original equal-length target expression total*index/(sample_number-1).
/// # Safety
/// output is writable for capacity f64 values; out_values is writable.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_tween_lengths_v1(
    total: f64,
    samples: usize,
    output: *mut f64,
    capacity: usize,
    out_values: *mut u64,
) -> i32 {
    if out_values.is_null() {
        return ERR_NULL;
    }
    if !total.is_finite() || total < 0.0 || !(2..=9999).contains(&samples) {
        return ERR_INPUT;
    }
    if capacity < samples {
        return ERR_SIZE;
    }
    let Some(out) = crate::checked_slice_mut(output, samples) else {
        return ERR_NULL;
    };
    let values: Vec<_> = (0..samples)
        .map(|i| total * i as f64 / (samples - 1) as f64)
        .collect();
    if values.iter().any(|x| !x.is_finite()) {
        return -11;
    }
    out.copy_from_slice(&values);
    *out_values = samples as u64;
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
