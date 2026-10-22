//! Native parser for the reference CAD application's numbers, fractions and
//! lengths-with-units (Q106-08).
//!
//! This is a transparent accelerator for `numparse.parse_number` and
//! `numparse.parse_length`. It reproduces the Python grammar **exactly** (the
//! same forms, the same order of rejection, the same values) and returns a
//! *stable error code* plus the parsed value. The Python layer maps the code
//! back to the original error string (which embeds the input), so a native
//! rejection and a Python rejection are indistinguishable to the caller.
//!
//! The grammar mirrors `numparse.py`:
//!
//! * a bare number is a mixed number (`1-3/4`), a fraction (`5/16`), or a
//!   decimal / scientific value (`123`, `123.456`, `.456`, `1.23e10`);
//! * a length is a feet-inches compound (`16'5"`, `1'2-3/4"`) or a bare number
//!   with an optional unit suffix (`27cm`, `5CM`, `1in`), converted to the
//!   model unit;
//! * a denominator of zero is a rejection, never an error-free `inf`.
//!
//! Every rejection is a stable `i32` code (the `ERR_*` constants below) so the
//! Python fallback can be activated on any code the caller does not expect,
//! and the "never a wrong number, always an error string" contract holds on
//! both routes.
//!
//! The crate stays dependency-free: the grammar is a small hand-written
//! parser (no `regex`), and `f64` conversion is the standard library's
//! `str::parse`.

// ---------------------------------------------------------------------------
// Stable error codes (negative). 0 = success.
// ---------------------------------------------------------------------------

/// `out` is null and a value was produced (the caller must not ignore an
/// error that wrote nothing).
pub const NP_OK: i32 = 0;
/// The input contained a space (the reference CAD application forbids spaces
/// in a number).
pub const NP_SPACE: i32 = -1;
/// The input is not a number (`parse_number`).
pub const NP_NOT_NUM: i32 = -2;
/// The input is not a length (`parse_length`).
pub const NP_NOT_LEN: i32 = -3;
/// The unit suffix is not a recognised length unit.
pub const NP_SUFFIX: i32 = -4;
/// The model unit is not one of the canonical names.
pub const NP_MODEL: i32 = -5;
/// `out` is null and a value was produced.
pub const NP_OUT: i32 = -6;

// ---------------------------------------------------------------------------
// Unit tables (OURS: our choice of supported spellings, flagged in numparse.py).
// ---------------------------------------------------------------------------

/// Millimetres per suffix spelling, case-insensitive.
const SUFFIX_MM: [(&str, f64); 33] = [
    ("mm", 1.0),
    ("millimeter", 1.0),
    ("millimeters", 1.0),
    ("millimetre", 1.0),
    ("millimetres", 1.0),
    ("cm", 10.0),
    ("centimeter", 10.0),
    ("centimeters", 10.0),
    ("centimetre", 10.0),
    ("centimetres", 10.0),
    ("m", 1000.0),
    ("meter", 1000.0),
    ("meters", 1000.0),
    ("metre", 1000.0),
    ("metres", 1000.0),
    ("km", 1_000_000.0),
    ("um", 0.001),
    ("micron", 0.001),
    ("microns", 0.001),
    ("in", 25.4),
    ("inch", 25.4),
    ("inches", 25.4),
    ("\"", 25.4),
    ("ft", 304.8),
    ("foot", 304.8),
    ("feet", 304.8),
    ("'", 304.8),
    ("yd", 914.4),
    ("yard", 914.4),
    ("yards", 914.4),
    ("mi", 1_609_344.0),
    ("mile", 1_609_344.0),
    ("miles", 1_609_344.0),
];

/// Millimetres per canonical model unit, case-insensitive.
const MODEL_UNITS: [&str; 9] = [
    "millimeters",
    "centimeters",
    "meters",
    "kilometers",
    "microns",
    "inches",
    "feet",
    "yards",
    "miles",
];

const FOOT_MM: f64 = 304.8;
const INCH_MM: f64 = 25.4;

// A practical upper bound on the input length: a typed CAD number or length
// never exceeds a few hundred bytes, and capping keeps the UTF-8 conversion
// and table scans bounded.
const MAX_INPUT_BYTES: usize = 256;

// ---------------------------------------------------------------------------
// Small helpers.
// ---------------------------------------------------------------------------

/// A checked view over `len` bytes, or `None` on null / overflow / a length
/// beyond the practical bound.
fn byte_span<'a>(ptr: *const u8, len: usize) -> Option<&'a [u8]> {
    if len > MAX_INPUT_BYTES {
        return None;
    }
    unsafe { crate::checked_slice(ptr, len) }
}

/// True if `b` is one of the forbidden in-number spaces.
fn is_space(b: u8) -> bool {
    b == b' ' || b == b'\t' || b == b'\n' || b == b'\r'
}

/// True if `s` contains any forbidden space.
fn contains_space(s: &[u8]) -> bool {
    s.iter().any(|&b| is_space(b))
}

/// True if `b` is an ASCII digit.
fn is_digit(b: u8) -> bool {
    b.is_ascii_digit()
}

/// True if `s` is entirely ASCII digits and non-empty (a `_BARE` integer part).
fn all_digits(s: &[u8]) -> bool {
    !s.is_empty() && s.iter().all(|&b| is_digit(b))
}

/// Case-insensitive equality of two ASCII strings (unit tables are ASCII).
fn eq_ci(a: &str, b: &str) -> bool {
    a.eq_ignore_ascii_case(b)
}

/// Look up a suffix spelling (case-insensitive) in `SUFFIX_MM`.
pub(crate) fn suffix_mm(suffix: &str) -> Option<f64> {
    SUFFIX_MM
        .iter()
        .find(|(name, _)| eq_ci(name, suffix))
        .map(|(_, mm)| *mm)
}

/// Look up a model unit (case-insensitive) in `MODEL_UNITS`.
pub(crate) fn model_mm(unit: &str) -> Option<f64> {
    let name = unit.to_ascii_lowercase();
    let mm = match name.as_str() {
        "millimeters" => 1.0,
        "centimeters" => 10.0,
        "meters" => 1000.0,
        "kilometers" => 1_000_000.0,
        "microns" => 0.001,
        "inches" => 25.4,
        "feet" => 304.8,
        "yards" => 914.4,
        "miles" => 1_609_344.0,
        _ => return None,
    };
    // Keep the table in one place for the "is this a known unit" check.
    debug_assert!(MODEL_UNITS.contains(&name.as_str()), "unit table drift");
    Some(mm)
}

/// Parse a mixed number `whole-num/den` (e.g. `1-3/4`). All three parts are
/// non-empty digit runs; a zero denominator is `None`.
fn parse_mixed(s: &str) -> Option<f64> {
    let (whole, rest) = s.split_once('-')?;
    let (num, den) = rest.split_once('/')?;
    if !all_digits(whole.as_bytes()) || !all_digits(num.as_bytes()) || !all_digits(den.as_bytes()) {
        return None;
    }
    if den == "0" {
        return None;
    }
    let w: f64 = whole.parse().ok()?;
    let n: f64 = num.parse().ok()?;
    let d: f64 = den.parse().ok()?;
    Some(w + n / d)
}

/// Parse a fraction `num/den` (e.g. `5/16`). A zero denominator is `None`.
fn parse_frac(s: &str) -> Option<f64> {
    let (num, den) = s.split_once('/')?;
    if !all_digits(num.as_bytes()) || !all_digits(den.as_bytes()) {
        return None;
    }
    if den == "0" {
        return None;
    }
    let n: f64 = num.parse().ok()?;
    let d: f64 = den.parse().ok()?;
    Some(n / d)
}

/// True if `s` matches the decimal/scientific shape
/// `(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?`.
fn dec_shape(s: &str) -> bool {
    let b = s.as_bytes();
    let n = b.len();
    if n == 0 {
        return false;
    }
    let mut i = 0;
    let mut seen_digit = false;
    while i < n && is_digit(b[i]) {
        i += 1;
        seen_digit = true;
    }
    if i < n && b[i] == b'.' {
        i += 1;
        let mut after_dot = 0usize;
        while i < n && is_digit(b[i]) {
            i += 1;
            after_dot += 1;
        }
        // The Python `_DEC_RE` is `\d+(?:\.\d+)?|\.\d+`: a decimal point is
        // legal only when at least one digit follows it (so a bare `1.` is
        // rejected, and Rust's `f64::parse` -- which would also reject it -- is
        // never fed a shape it cannot handle).
        if after_dot == 0 {
            return false;
        }
        seen_digit = true;
    }
    if !seen_digit {
        return false;
    }
    if i < n && (b[i] == b'e' || b[i] == b'E') {
        i += 1;
        if i < n && (b[i] == b'+' || b[i] == b'-') {
            i += 1;
        }
        let mut exp_digits = 0usize;
        while i < n && is_digit(b[i]) {
            i += 1;
            exp_digits += 1;
        }
        if exp_digits == 0 {
            return false;
        }
    }
    i == n
}

/// Parse a decimal / scientific value (e.g. `123`, `.456`, `1.23e10`).
fn parse_dec(s: &str) -> Option<f64> {
    if !dec_shape(s) {
        return None;
    }
    s.parse::<f64>().ok()
}

/// Interpret a bare number (mixed, then fraction, then decimal/scientific).
pub(crate) fn parse_bare(s: &str) -> Option<f64> {
    parse_mixed(s).or_else(|| parse_frac(s).or_else(|| parse_dec(s)))
}

/// Interpret an optional sign followed by a bare number (the `num` group of
/// a length, `[+-]?<bare>`). The sign is applied, mirroring `parse_number`.
pub(crate) fn parse_signed_bare(s: &str) -> Option<f64> {
    let (sign, bare) = match s.as_bytes().first() {
        Some(b'-') => (-1.0, &s[1..]),
        Some(b'+') => (1.0, &s[1..]),
        _ => (1.0, s),
    };
    parse_bare(bare).map(|v| sign * v)
}

/// Parse a feet-inches compound `sign feet' inches"` and return the value in
/// millimetres (sign applied).
pub(crate) fn feet_inches_mm(s: &str) -> Option<f64> {
    let b = s.as_bytes();
    let quote = b.iter().position(|&c| c == b'\'')?;
    let after = &b[quote + 1..];
    let inch = after.iter().position(|&c| c == b'"')?;
    let feet = &b[..quote];
    let inches = &after[..inch];
    // The closing quote must be the last byte (spaces are rejected up front,
    // so "anything after" is simply "present").
    if inch != after.len() - 1 {
        return None;
    }
    // An optional leading sign on the whole compound (Python `[+-]?`).
    let (sign, feet) = match feet.first() {
        Some(b'-') => (-1.0, &feet[1..]),
        Some(b'+') => (1.0, &feet[1..]),
        _ => (1.0, feet),
    };
    if feet.is_empty() || inches.is_empty() {
        return None;
    }
    let feet_val = parse_bare(std::str::from_utf8(feet).ok()?)?;
    let inches_val = parse_bare(std::str::from_utf8(inches).ok()?)?;
    Some(sign * (feet_val * FOOT_MM + inches_val * INCH_MM))
}

// ---------------------------------------------------------------------------
// Core parse (shared by the two C entry points).
// ---------------------------------------------------------------------------

enum Num {
    Number,
    Length,
}

fn core_parse(text: &[u8], num: Num, model: Option<&str>) -> Result<f64, i32> {
    if text.is_empty() {
        return Err(match num {
            Num::Number => NP_NOT_NUM,
            Num::Length => NP_NOT_LEN,
        });
    }
    if contains_space(text) {
        return Err(NP_SPACE);
    }
    let is_len = matches!(num, Num::Length);
    let model_mm = if is_len {
        let m = model.ok_or(NP_MODEL)?.to_ascii_lowercase();
        if m.len() > 64 {
            return Err(NP_MODEL);
        }
        model_mm(&m).ok_or(NP_MODEL)?
    } else {
        1.0
    };

    let s = match std::str::from_utf8(text) {
        Ok(s) => s,
        // Non-UTF-8 bytes cannot be a valid number; the Python regex would
        // have rejected them too.
        Err(_) => {
            return Err(match num {
                Num::Number => NP_NOT_NUM,
                Num::Length => NP_NOT_LEN,
            })
        }
    };

    if is_len {
        if let Some(mm) = feet_inches_mm(s) {
            return Ok(mm / model_mm);
        }
        // The Python suffix group is the *trailing* run of `[A-Za-z\"']` (a
        // letter, a double-quote, or a single-quote). Walk back from the end
        // over that run; everything before it is the `num` group (`[+-]?<bare>`).
        let b = s.as_bytes();
        let is_suffix_char = |c: u8| c.is_ascii_alphabetic() || c == b'"' || c == b'\'';
        let mut k = b.len();
        while k > 0 && is_suffix_char(b[k - 1]) {
            k -= 1;
        }
        // k = index of the first byte of the trailing suffix run (or b.len() when
        // there is none). Everything before k is the number part.
        let (num_part, suffix) = if k < b.len() {
            (&s[..k], &s[k..])
        } else {
            (s, "")
        };
        if num_part.is_empty() {
            return Err(NP_NOT_LEN);
        }
        let value = parse_signed_bare(num_part).ok_or(match num {
            Num::Number => NP_NOT_NUM,
            Num::Length => NP_NOT_LEN,
        })?;
        if suffix.is_empty() {
            return Ok(value);
        }
        let smm = suffix_mm(suffix).ok_or(NP_SUFFIX)?;
        return Ok(value * smm / model_mm);
    }

    // parse_number: optional sign + bare number, nothing else.
    let (sign, bare) = match s.as_bytes().first() {
        Some(b'-') => (-1.0, &s[1..]),
        Some(b'+') => (1.0, &s[1..]),
        _ => (1.0, s),
    };
    let value = parse_bare(bare).ok_or(match num {
        Num::Number => NP_NOT_NUM,
        Num::Length => NP_NOT_LEN,
    })?;
    Ok(sign * value)
}

/// Parse a bare number (no unit). `NP_*` error code on rejection.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when
/// `text_len == 0`), and `out` must be writable for one `f64` (null only when
/// the caller wants to read only the error code).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_number(
    text: *const u8,
    text_len: usize,
    out: *mut f64,
) -> i32 {
    let out = match unsafe { crate::checked_slice_mut(out, 1) } {
        Some(out) => &mut out[0],
        None => return NP_OUT,
    };
    *out = 0.0;
    let span = match byte_span(text, text_len) {
        Some(s) => s,
        None => return NP_NOT_NUM,
    };
    match core_parse(span, Num::Number, None) {
        Ok(v) => {
            *out = v;
            NP_OK
        }
        Err(code) => code,
    }
}

/// Parse a length (number + optional unit) and convert to `model_unit`.
/// `NP_*` error code on rejection.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when
/// `text_len == 0`), `model` must be readable for `model_len` bytes (null only
/// when `model_len == 0`), and `out` must be writable for one `f64` (null only
/// when the caller wants to read only the error code).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_length(
    text: *const u8,
    text_len: usize,
    model: *const u8,
    model_len: usize,
    out: *mut f64,
) -> i32 {
    let out = match unsafe { crate::checked_slice_mut(out, 1) } {
        Some(out) => &mut out[0],
        None => return NP_OUT,
    };
    *out = 0.0;
    let span = match byte_span(text, text_len) {
        Some(s) => s,
        None => return NP_NOT_LEN,
    };
    let model_span = match byte_span(model, model_len) {
        Some(s) => s,
        None => return NP_MODEL,
    };
    let model = match std::str::from_utf8(model_span) {
        Ok(s) => s,
        Err(_) => return NP_MODEL,
    };
    match core_parse(span, Num::Length, Some(model)) {
        Ok(v) => {
            *out = v;
            NP_OK
        }
        Err(code) => code,
    }
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn num(s: &str) -> (f64, i32) {
        let mut out = 0.0f64;
        let b = s.as_bytes();
        let code = unsafe { narwhal_pure_core_number(b.as_ptr(), b.len(), &mut out) };
        (out, code)
    }

    fn len_(s: &str, model: &str) -> (f64, i32) {
        let mut out = 0.0f64;
        let tb = s.as_bytes();
        let mb = model.as_bytes();
        let code = unsafe {
            narwhal_pure_core_length(tb.as_ptr(), tb.len(), mb.as_ptr(), mb.len(), &mut out)
        };
        (out, code)
    }

    fn assert_num_ok(s: &str, expected: f64) {
        let (v, c) = num(s);
        assert_eq!(c, NP_OK, "{s}: {c}");
        assert!((v - expected).abs() < 1e-9, "{s}: {v} != {expected}");
    }

    fn assert_num_err(s: &str) {
        let (v, c) = num(s);
        assert_ne!(c, NP_OK, "{s} should be an error");
        assert_eq!(v, 0.0, "{s}: out must be cleared");
    }

    fn assert_len_ok(s: &str, model: &str, expected: f64) {
        let (v, c) = len_(s, model);
        assert_eq!(c, NP_OK, "{s} {model}: {c}");
        assert!(
            (v - expected).abs() < 1e-9,
            "{s} {model}: {v} != {expected}"
        );
    }

    fn assert_len_err(s: &str, model: &str) {
        let (v, c) = len_(s, model);
        assert_ne!(c, NP_OK, "{s} {model} should be an error");
        assert_eq!(v, 0.0, "{s} {model}: out must be cleared");
    }

    #[test]
    fn bare_numbers() {
        assert_num_ok("123", 123.0);
        assert_num_ok("123.456", 123.456);
        assert_num_ok("0.456", 0.456);
        assert_num_ok(".456", 0.456);
        assert_num_ok("-1.23456e10", -1.23456e10);
        assert_num_ok("1.23456E10", 1.23456e10);
        assert_num_ok("5/16", 5.0 / 16.0);
        assert_num_ok("1-3/4", 1.75);
        assert_num_ok("+123", 123.0);
        assert_num_ok("-123", -123.0);
        assert_num_ok("-5/16", -(5.0 / 16.0));
    }

    #[test]
    fn bare_number_errors() {
        assert_num_err("1-3");
        assert_num_err("1/0");
        assert_num_err("5/");
        assert_num_err("abc");
        assert_num_err("");
        assert_num_err("1 3");
    }

    #[test]
    fn lengths() {
        assert_len_ok("1.235millimeters", "Millimeters", 1.235);
        assert_len_ok("-1.234cm", "Millimeters", -12.34);
        assert_len_ok("27cm", "Meters", 0.27);
        assert_len_ok("+16'5\"", "Millimeters", 5003.8);
        assert_len_ok("1'2-3/4\"", "Millimeters", 304.8 + 2.75 * 25.4);
        assert_len_ok("42", "Meters", 42.0);
        assert_len_ok("42", "Millimeters", 42.0);
        assert_len_ok("10m", "Millimeters", 10000.0);
        assert_len_ok("2km", "Meters", 2000.0);
        assert_len_ok("1000um", "Millimeters", 1.0);
        assert_len_ok("1in", "Millimeters", 25.4);
        assert_len_ok("1ft", "Millimeters", 304.8);
        assert_len_ok("1yd", "Millimeters", 914.4);
        assert_len_ok("1mi", "Millimeters", 1609344.0);
        assert_len_ok("5CM", "Millimeters", 50.0);
        assert_len_ok("27cm", "meters", 0.27);
        assert_len_ok("1'6\"", "Feet", 1.5);
    }

    #[test]
    fn length_errors() {
        assert_len_err("5xyz", "Millimeters");
        assert_len_err("10", "Furlongs");
        assert_len_err("16'5", "Millimeters");
        assert_len_err("1 3", "Millimeters");
        assert_len_err("", "Millimeters");
    }

    #[test]
    fn zero_denominator_rejected() {
        assert_num_err("1/0");
        assert_num_err("1-0/0");
        assert_len_err("1/0", "Millimeters");
    }

    #[test]
    fn out_param_cleared_on_error() {
        let mut out = 42.0f64;
        let b = b"abc";
        let code = unsafe { narwhal_pure_core_number(b.as_ptr(), 3, &mut out) };
        assert_eq!(code, NP_NOT_NUM);
        assert_eq!(out, 0.0);
    }

    #[test]
    fn raw_null_and_oversized_spans_clear_outputs() {
        let mut out = 42.0f64;
        assert_eq!(
            unsafe { narwhal_pure_core_number(std::ptr::null(), 1, &mut out) },
            NP_NOT_NUM
        );
        assert_eq!(out, 0.0);

        out = 42.0;
        let dangling = std::ptr::NonNull::<u8>::dangling().as_ptr();
        assert_eq!(
            unsafe { narwhal_pure_core_number(dangling, usize::MAX, &mut out) },
            NP_NOT_NUM
        );
        assert_eq!(out, 0.0);

        out = 42.0;
        assert_eq!(
            unsafe { narwhal_pure_core_length(b"1".as_ptr(), 1, std::ptr::null(), 1, &mut out) },
            NP_MODEL
        );
        assert_eq!(out, 0.0);
    }

    #[test]
    fn misaligned_output_is_refused_before_write() {
        let mut storage = [0.0f64; 2];
        let out = storage
            .as_mut_ptr()
            .cast::<u8>()
            .wrapping_add(1)
            .cast::<f64>();
        assert_eq!(
            unsafe { narwhal_pure_core_number(b"1".as_ptr(), 1, out) },
            NP_OUT
        );
    }
}
