//! Native-only number/length parsing, including rejection classification.
//!
//! Additive ABI 2 leaves old fused calculator/coordinate entry points unchanged.
//! Inputs are Python code points; errors carry original code-point ranges so the
//! host only formats repr(), never reparses. OURS: 4096-code-point bound, finite
//! results only. Decimal fractions use exact integer division with ties-to-even.
use crate::numparse::{model_mm, parse_bare, suffix_mm};
use std::cmp::Ordering;

pub const LIMIT: usize = 4096;
const SPACE: i32 = -1;
const NUMBER: i32 = -2;
const LENGTH: i32 = -3;
const SUFFIX: i32 = -4;
const MODEL: i32 = -5;
const BOUND: i32 = -7;
const RANGE: i32 = -8;

#[repr(C)]
#[derive(Debug, Default, Clone, Copy)]
pub struct Reply {
    pub code: i32,
    pub reserved: u32,
    pub value: f64,
    pub start: u32,
    pub end: u32,
}

// MEASURED from CPython Unicode 15.1 and 16 decimal properties. Each start
// denotes exactly ten Nd characters, including mathematical digit variants.
const ZEROS: &[u32] = &[
    0x30, 0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6, 0xd66,
    0xde6, 0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90,
    0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0, 0xff10,
    0x104a0, 0x10d30, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450, 0x114d0, 0x11650,
    0x116c0, 0x11730, 0x118e0, 0x11950, 0x11c50, 0x11d50, 0x11da0, 0x11f50, 0x16a60, 0x16ac0,
    0x16b50, 0x1d7ce, 0x1d7d8, 0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e4f0, 0x1e950,
    0x1fbf0,
];
const NEW_ZEROS: &[u32] = &[
    0x10d40, 0x116d0, 0x116da, 0x11bf0, 0x16130, 0x16d70, 0x1ccf0, 0x1e5f1,
];

fn normalized(input: &[u32], unicode: u32) -> String {
    input
        .iter()
        .map(|&cp| {
            if cp < 128 {
                return char::from_u32(cp).unwrap();
            }
            for &zero in ZEROS
                .iter()
                .chain(if unicode == 160 { NEW_ZEROS } else { &[] })
            {
                if cp >= zero && cp < zero + 10 {
                    return (b'0' + (cp - zero) as u8) as char;
                }
            }
            // Every non-ASCII nondecimal character is invalid in this grammar.
            // One sentinel preserves code-point offsets, including lone surrogates.
            '?'
        })
        .collect()
}

#[derive(Clone, Debug)]
struct Natural(Vec<u32>);
impl Natural {
    fn decimal(s: &str) -> Self {
        let mut limbs = vec![0u32];
        for digit in s.bytes() {
            let mut carry = (digit - b'0') as u64;
            for limb in &mut limbs {
                carry += u64::from(*limb) * 10;
                *limb = carry as u32;
                carry >>= 32;
            }
            if carry != 0 {
                limbs.push(carry as u32);
            }
        }
        Self(limbs)
    }
    fn bits(&self) -> i32 {
        (32 * (self.0.len() - 1)) as i32 + (32 - self.0.last().unwrap().leading_zeros()) as i32
    }
    fn shift(&self, bits: usize) -> Self {
        let mut limbs = vec![0; bits / 32];
        let mut carry = 0u64;
        for &limb in &self.0 {
            let v = (u64::from(limb) << (bits % 32)) | carry;
            limbs.push(v as u32);
            carry = v >> 32;
        }
        if carry != 0 {
            limbs.push(carry as u32);
        }
        let mut result = Self(limbs);
        result.trim();
        result
    }
    fn trim(&mut self) {
        while self.0.len() > 1 && self.0.last() == Some(&0) {
            self.0.pop();
        }
    }
    fn cmp(&self, other: &Self) -> Ordering {
        self.0
            .len()
            .cmp(&other.0.len())
            .then_with(|| self.0.iter().rev().cmp(other.0.iter().rev()))
    }
    fn subtract(&mut self, other: &Self) {
        debug_assert!(self.cmp(other) != Ordering::Less);
        let mut borrow = 0u64;
        for (i, limb) in self.0.iter_mut().enumerate() {
            let rhs = u64::from(*other.0.get(i).unwrap_or(&0)) + borrow;
            let lhs = u64::from(*limb);
            *limb = lhs.wrapping_sub(rhs) as u32;
            borrow = u64::from(lhs < rhs);
        }
        self.trim();
    }
}

fn fraction(num: &str, den: &str) -> Result<f64, i32> {
    let n = Natural::decimal(num);
    let d = Natural::decimal(den);
    if d.bits() == 0 {
        return Err(NUMBER);
    }
    if n.bits() == 0 {
        return Ok(0.0);
    }
    let mut exponent = n.bits() - d.bits();
    let comparison = if exponent >= 0 {
        n.cmp(&d.shift(exponent as usize))
    } else {
        n.shift((-exponent) as usize).cmp(&d)
    };
    if comparison == Ordering::Less {
        exponent -= 1;
    }
    if exponent > 1023 {
        return Err(RANGE);
    }
    if exponent < -1075 {
        return Ok(0.0);
    }
    let scale = 52 - exponent.max(-1022);
    let (mut remainder, divisor) = if scale >= 0 {
        (n.shift(scale as usize), d)
    } else {
        (n, d.shift((-scale) as usize))
    };
    let mut quotient = 0u64;
    let high = remainder.bits() - divisor.bits();
    for bit in (0..=high).rev() {
        let part = divisor.shift(bit as usize);
        if remainder.cmp(&part) != Ordering::Less {
            remainder.subtract(&part);
            quotient |= 1u64 << bit;
        }
    }
    let halfway = remainder.shift(1).cmp(&divisor);
    if halfway == Ordering::Greater || (halfway == Ordering::Equal && quotient & 1 != 0) {
        quotient += 1;
    }
    if exponent < -1022 {
        return Ok(f64::from_bits(quotient));
    }
    if quotient == 1u64 << 53 {
        quotient >>= 1;
        exponent += 1;
    }
    if exponent > 1023 {
        return Err(RANGE);
    }
    Ok(f64::from_bits(
        ((exponent + 1023) as u64) << 52 | (quotient - (1u64 << 52)),
    ))
}
fn digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}
fn finite(value: f64) -> Result<f64, i32> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(RANGE)
    }
}

// None means grammar mismatch, Some(Err) means valid grammar but bad value.
fn bare(s: &str) -> Option<Result<f64, i32>> {
    if let Some((prefix, den)) = s.split_once('/') {
        if !digits(den) {
            return None;
        }
        let (whole, num) = if let Some((w, n)) = prefix.split_once('-') {
            if !digits(w) {
                return None;
            }
            (Some(w), n)
        } else {
            (None, prefix)
        };
        if !digits(num) {
            return None;
        }
        return Some(fraction(num, den).and_then(|v| match whole {
            Some(w) => finite(w.parse::<f64>().unwrap() + v),
            None => Ok(v),
        }));
    }
    parse_bare(s).map(finite)
}
fn signed(s: &str) -> Option<Result<f64, i32>> {
    let (sign, rest) = if let Some(rest) = s.strip_prefix('-') {
        (-1.0, rest)
    } else {
        (1.0, s.strip_prefix('+').unwrap_or(s))
    };
    bare(rest).map(|v| v.map(|v| v * sign))
}
fn error(code: i32, start: usize, end: usize) -> Reply {
    Reply {
        code,
        start: start as u32,
        end: end as u32,
        ..Reply::default()
    }
}
fn value(v: Result<f64, i32>, start: usize, end: usize) -> Reply {
    match v {
        Ok(value) => Reply {
            value,
            ..Reply::default()
        },
        Err(code) => error(code, start, end),
    }
}
fn parse(input: &[u32], model: &[u32], length: bool, unicode: u32) -> Reply {
    let n = input.len();
    if input.iter().any(|c| [32, 9, 10, 13].contains(c)) {
        return error(SPACE, 0, n);
    }
    let unit = if length {
        // Keep whitespace-first error precedence, but never drop invalid scalars.
        if model.iter().any(|&c| char::from_u32(c).is_none()) {
            return error(MODEL, 0, 0);
        }
        let model: String = model
            .iter()
            .filter_map(|&c| char::from_u32(c))
            .flat_map(char::to_lowercase)
            .collect();
        if model.chars().count() == 0 {
            return error(MODEL, 0, 0);
        }
        match model_mm(&model) {
            Some(v) => v,
            None => return error(MODEL, 0, 0),
        }
    } else {
        1.0
    };
    let s = normalized(input, unicode);
    if !length {
        return value(signed(&s).unwrap_or(Err(NUMBER)), 0, n);
    }
    let (sign, rest) = if let Some(rest) = s.strip_prefix('-') {
        (-1.0, rest)
    } else {
        (1.0, s.strip_prefix('+').unwrap_or(&s))
    };
    if let Some((feet, inches)) = rest.split_once('\'') {
        if let Some(inches) = inches.strip_suffix('"') {
            if let (Some(f), Some(i)) = (bare(feet), bare(inches)) {
                return value(
                    match (f, i) {
                        (Ok(f), Ok(i)) => finite(sign * (f * 304.8 + i * 25.4) / unit),
                        (Err(RANGE), _) | (_, Err(RANGE)) => Err(RANGE),
                        _ => Err(LENGTH),
                    },
                    0,
                    n,
                );
            }
        }
    }
    let mut k = s.len();
    while k > 0
        && (s.as_bytes()[k - 1].is_ascii_alphabetic()
            || [b'\'', b'"'].contains(&s.as_bytes()[k - 1]))
    {
        k -= 1;
    }
    let Some(number) = signed(&s[..k]) else {
        return error(LENGTH, 0, n);
    };
    let number = match number {
        Ok(v) => v,
        Err(code) => return error(code, 0, k),
    };
    if k == s.len() {
        return value(Ok(number), 0, n);
    }
    let Some(factor) = suffix_mm(&s[k..]) else {
        return error(SUFFIX, k, n);
    };
    value(finite(number * factor / unit), 0, n)
}

#[no_mangle]
pub extern "C" fn narwhal_pure_core_parse_abi() -> u32 {
    2
}

/// Parse one number or length; result/error goes in a fixed 24-byte reply.
/// Return 0 means valid protocol, 1 bad output/input spans, 2 unsupported flags.
/// Grammar rejection is reply.code, not a transport failure. Inputs bounded to4096.
/// # Safety
/// Nonempty inputs must be readable for their declared u32 lengths. `out` must
/// point to one aligned writable Reply, not overlap either input, and size match.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_parse_v2(
    text: *const u32,
    text_len: usize,
    model: *const u32,
    model_len: usize,
    length: u32,
    unicode: u32,
    out: *mut Reply,
    out_size: usize,
) -> u32 {
    if out_size != std::mem::size_of::<Reply>() {
        return 1;
    }
    let Some(output) = (unsafe { crate::checked_slice_mut(out, 1) }) else {
        return 1;
    };
    output[0] = Reply::default();
    if length > 1 || ![151, 160].contains(&unicode) {
        return 2;
    }
    if text_len > LIMIT || model_len > LIMIT {
        output[0] = error(BOUND, 0, 0);
        return 0;
    }
    let (Some(text), Some(model)) = (unsafe { crate::checked_slice(text, text_len) }, unsafe {
        crate::checked_slice(model, model_len)
    }) else {
        return 1;
    };

    output[0] = parse(text, model, length == 1, unicode);
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    fn run(text: &str, model: Option<&str>) -> Reply {
        parse(
            &text.chars().map(|c| c as u32).collect::<Vec<_>>(),
            &model
                .unwrap_or("")
                .chars()
                .map(|c| c as u32)
                .collect::<Vec<_>>(),
            model.is_some(),
            160,
        )
    }
    #[test]
    fn rejection_ranges_and_precedence() {
        assert_eq!(run("", Some("bad")).code, MODEL);
        assert_eq!(run(" ", Some("bad")).code, SPACE);
        assert_eq!(
            (
                run("1/00cm", Some("Meters")).code,
                run("1/00cm", Some("Meters")).end
            ),
            (NUMBER, 4)
        );
        let r = run("5xyz", Some("Meters"));
        assert_eq!((r.code, r.start, r.end), (SUFFIX, 1, 4));
        assert_eq!(run("1/00", None).code, NUMBER);
        assert_eq!(run("1'2/00\"", Some("Meters")).code, LENGTH);
        assert_eq!(run("1e999", None).code, RANGE);
    }
    #[test]
    fn unicode_and_units() {
        assert_eq!(run("١-٣/٤", None).value, 1.75);
        assert_eq!(run("２７cm", Some("Meters")).value, 0.27);
        assert_eq!(run("2m", Some("Kilometers")).value, 0.002);
        assert_eq!(parse(&[0x10d41], &[], false, 151).code, NUMBER);
        assert_eq!(parse(&[0x10d41], &[], false, 160).value, 1.0);
        assert_eq!(parse(&[0xd800], &[], false, 160).code, NUMBER);
        assert_eq!(run("-0", None).value.to_bits(), (-0.0f64).to_bits());
    }
    #[test]
    fn exact_ratio_rounding() {
        assert_eq!(
            fraction("9007199254740993", "9007199254740992").unwrap(),
            1.0
        );
        assert_eq!(
            fraction("9007199254740995", "9007199254740992")
                .unwrap()
                .to_bits(),
            1.0f64.to_bits() + 2
        );
        let huge = "9".repeat(350);
        assert_eq!(fraction(&huge, &huge).unwrap(), 1.0);
        assert_eq!(
            fraction("1", &format!("1{}", "0".repeat(400))).unwrap(),
            0.0
        );
        assert_eq!(
            fraction("1", &format!("1{}", "0".repeat(309))).unwrap(),
            1e-309
        );
        assert_eq!(fraction("1", "00"), Err(NUMBER));
    }
    #[test]
    fn output_contract() {
        let mut r = Reply {
            value: 17.0,
            ..Reply::default()
        };
        unsafe {
            assert_eq!(
                narwhal_pure_core_parse_v2(
                    std::ptr::null(),
                    1,
                    std::ptr::null(),
                    0,
                    0,
                    160,
                    &mut r,
                    24
                ),
                1
            );
            assert_eq!(r.value, 0.0);
            assert_eq!(
                narwhal_pure_core_parse_v2(
                    std::ptr::null(),
                    LIMIT + 1,
                    std::ptr::null(),
                    0,
                    0,
                    160,
                    &mut r,
                    24
                ),
                0
            );
            assert_eq!(r.code, BOUND);
            assert_eq!(
                narwhal_pure_core_parse_v2(
                    std::ptr::null(),
                    0,
                    std::ptr::null(),
                    0,
                    0,
                    160,
                    &mut r,
                    23
                ),
                1
            );
        }
    }
}
