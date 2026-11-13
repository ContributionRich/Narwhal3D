// Port of CPython v3.13.7 Modules/mathmodule.c: vector_norm, dl_mul,
// dl_fast_sum (Python Software Foundation and contributors).
// Source: https://github.com/python/cpython/blob/v3.13.7/Modules/mathmodule.c
// SHA256: c62e150ecae146447c96964b1e4ae0226516686681af34c544abb1e2fdd62a6f
// Licensed under the PSF License Version 2; see licenses/CPython-PSF-License.txt.
// Changes: fixed-size Rust storage, IEEE exponent extraction instead of frexp/
// ldexp, Rust mul_add for C fma, and a checked development batch ABI.
// Preserve evaluation order: ideal rounding or nested hypot is NOT a substitute.

fn product(x: f64, y: f64) -> (f64, f64) {
    let hi = x * y;
    (hi, x.mul_add(y, -hi))
}

fn fast_sum(a: f64, b: f64) -> (f64, f64) {
    let hi = a + b;
    (hi, (a - hi) + b)
}

fn vector_norm<const N: usize>(mut values: [f64; N]) -> f64 {
    let mut max = 0.0_f64;
    let mut found_nan = false;
    for x in &mut values {
        *x = x.abs();
        found_nan |= x.is_nan();
        if *x > max {
            max = *x;
        }
    }
    if max.is_infinite() {
        return max;
    }
    if found_nan {
        return f64::NAN;
    }
    if max == 0.0 || N <= 1 {
        return max;
    }
    // frexp(max)'s exponent, including subnormals.
    let bits = max.to_bits();
    let field = ((bits >> 52) & 0x7ff) as i32;
    let exponent = if field == 0 {
        64 - bits.leading_zeros() as i32 - 1074
    } else {
        field - 1022
    };
    if exponent < -1023 {
        for x in &mut values {
            *x /= f64::MIN_POSITIVE;
        }
        return f64::MIN_POSITIVE * vector_norm(values);
    }
    // 2^-exponent: exponent can be 1024, so scale can be subnormal.
    let e = -exponent;
    let scale = if e >= -1022 {
        f64::from_bits(((e + 1023) as u64) << 52)
    } else {
        f64::from_bits(1_u64 << (e + 1074))
    };
    let mut csum = 1.0;
    let mut frac1 = 0.0;
    let mut frac2 = 0.0;
    for x in values {
        let x = x * scale;
        let (hi, lo) = product(x, x);
        let (sum, err) = fast_sum(csum, hi);
        csum = sum;
        frac1 += lo;
        frac2 += err;
    }
    let mut h = (csum - 1.0 + (frac1 + frac2)).sqrt();
    let (hi, lo) = product(-h, h);
    let (sum, err) = fast_sum(csum, hi);
    csum = sum;
    frac1 += lo;
    frac2 += err;
    let x = csum - 1.0 + (frac1 + frac2);
    h += x / (2.0 * h);
    h / scale
}

pub(crate) fn hypot2(x: f64, y: f64) -> f64 {
    vector_norm([x, y])
}

pub(crate) fn dist3(p: [f64; 3], q: [f64; 3]) -> f64 {
    vector_norm([p[0] - q[0], p[1] - q[1], p[2] - q[2]])
}

/// Development proof ABI. mode=2: packed xy; mode=3: packed xyz;
/// mode=6: packed px,py,pz,qx,qy,qz. count is the number of rows.
/// Returns 0 on success, -1 on invalid mode/span/count (no writes).
///
/// # Safety
/// Input/output must be allocated, initialized (input), writable (output), and
/// non-overlapping for their complete spans. Metadata checks cannot prove this.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_norm_v1(
    values: *const f64,
    count: usize,
    mode: u32,
    out: *mut f64,
) -> i32 {
    let width = match mode {
        2 => 2,
        3 => 3,
        6 => 6,
        _ => return -1,
    };
    let Some(len) = count.checked_mul(width) else {
        return -1;
    };
    let Some(values) = (unsafe { crate::checked_slice(values, len) }) else {
        return -1;
    };
    let Some(out) = (unsafe { crate::checked_slice_mut(out, count) }) else {
        return -1;
    };
    for (row, result) in values.chunks_exact(width).zip(out) {
        *result = match mode {
            2 => hypot2(row[0], row[1]),
            3 => vector_norm([row[0], row[1], row[2]]),
            _ => dist3([row[0], row[1], row[2]], [row[3], row[4], row[5]]),
        };
    }
    0
}
