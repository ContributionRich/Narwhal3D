//! Native surveyor bearing parser (Q106-10).
//!
//! A transparent accelerator for `narwhal3d/bearing.py::bearing_angle` and
//! `parse_surveyor`. It implements the SAME quadrant / degrees-minutes-seconds
//! grammar as the Python reference and returns the SAME value on success; on any
//! rejection it returns an error code and the Python layer re-runs its UNCHANGED
//! parser, so the exact public error string (which embeds the input, e.g.
//! `"not an angle: %r"`) is always produced by the original code. A fallback is
//! therefore a correctness fallback, not a slower one: the measured success path
//! never touches the Python parser.
//!
//! Error contract: `BEARING_OK` (0) with the value(s) in the out-param(s) on
//! success; `BEARING_ERR` on rejection; `BEARING_OUT` when an out-param is null
//! but a value was produced.
//!
//! Acceptance boundary: the Rust route accepts ONLY inputs the Python reference
//! accepts, and computes the identical value. Where the Python regex is
//! permissive (it defers angle validation to `_angle_degrees`), the Rust route
//! mirrors that division of labour: the bearing-level checks (N/S + non-E/W
//! middle + E/W) and the angle-level checks (a valid DMS/plain number in [0,90])
//! are both reproduced, and ANY input that would be rejected by either level is
//! a rejection here (→ Python fallback, which produces the exact string). The
//! only direction a divergence could take is "Rust rejects, Python accepts",
//! which is the safe one (a correct, just slower, Python path).
//!
//! Robustness bounds (a bearing / surveyor coordinate is short): the input
//! length is capped. An over-limit input is a rejection (→ Python fallback),
//! never a mis-parse and never an unbounded allocation.

/// Success (the value is in the out-param).
pub const BEARING_OK: i32 = 0;
/// A rejection (the exact error string comes from the Python fallback).
pub const BEARING_ERR: i32 = -1;
/// An out-param is null and a value was produced.
pub const BEARING_OUT: i32 = -2;

/// A bearing / surveyor coordinate is short; cap the input so parsing is bounded.
const BEARING_MAX_INPUT_BYTES: usize = 256;

// ---------------------------------------------------------------------------
// Small byte-level helpers (all operate on the ASCII subset this grammar uses;
// a non-ASCII byte is simply "not a digit / not N-S / not E-W", which pushes the
// input down the rejection → Python-fallback path, the safe direction).
// ---------------------------------------------------------------------------

#[inline]
fn is_ascii_ws(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0C | 0x0B)
}

#[inline]
fn is_ns(b: u8) -> bool {
    matches!(b, b'N' | b'n' | b'S' | b's')
}

#[inline]
fn is_ew(b: u8) -> bool {
    matches!(b, b'E' | b'e' | b'W' | b'w')
}

#[inline]
fn is_digit(b: u8) -> bool {
    b.is_ascii_digit()
}

/// Trim ASCII whitespace from both ends, returning the interior slice.
fn trim_ascii(s: &[u8]) -> &[u8] {
    let mut start = 0;
    let mut end = s.len();
    while start < end && is_ascii_ws(s[start]) {
        start += 1;
    }
    while end > start && is_ascii_ws(s[end - 1]) {
        end -= 1;
    }
    &s[start..end]
}

/// Convert an ASCII numeric byte slice (integer or decimal, e.g. `"30"` or
/// `"54.43"`) to `f64` using Rust's correctly-rounded conversion, which matches
/// Python's `int(...)` / `float(...)` for these small values.
fn ascii_num_to_f64(b: &[u8]) -> Option<f64> {
    if b.is_empty() {
        return None;
    }
    let s = std::str::from_utf8(b).ok()?;
    s.parse::<f64>().ok()
}

/// Parse `\d+(?:\.\d+)?` starting at `start` (which must point at a digit),
/// returning `(value, end)`. A `.` must be followed by at least one digit.
fn parse_decimal_span(s: &[u8], start: usize) -> Option<(f64, usize)> {
    if start >= s.len() || !is_digit(s[start]) {
        return None;
    }
    let mut i = start;
    while i < s.len() && is_digit(s[i]) {
        i += 1;
    }
    if i < s.len() && s[i] == b'.' {
        i += 1;
        if i >= s.len() || !is_digit(s[i]) {
            return None;
        }
        while i < s.len() && is_digit(s[i]) {
            i += 1;
        }
    }
    let val = ascii_num_to_f64(&s[start..i])?;
    Some((val, i))
}

/// Parse `\d+(?:\.\d+)?` from `start`, requiring it to consume to the end of
/// `s`. Returns the value.
fn parse_decimal(s: &[u8], start: usize) -> Option<f64> {
    let (val, end) = parse_decimal_span(s, start)?;
    if end != s.len() {
        return None;
    }
    Some(val)
}

/// Parse the angle string (already whitespace-trimmed) to degrees, mirroring
/// `bearing.py::_ANGLE_RE`:
///
/// * plain degrees: `\d+` or `\d+(\.\d+)?` (e.g. `30`, `30.5`);
/// * DMS: `\d+ d \d+(?:\.\d+)? ' [\d+(?:\.\d+)?] ["]` (e.g. `30d22'`,
///   `30d22'54.43"`) — the degree part is an integer only, minutes are required,
///   seconds and the trailing `"` are optional.
///
/// Returns `None` when the string is not a valid angle (→ Python fallback).
fn parse_angle(s: &[u8]) -> Option<f64> {
    if s.is_empty() || !is_digit(s[0]) {
        return None;
    }
    // Scan the leading integer run.
    let mut i = 0;
    while i < s.len() && is_digit(s[i]) {
        i += 1;
    }
    match s.get(i) {
        None => {
            // Plain integer degrees.
            ascii_num_to_f64(&s[..i])
        }
        Some(b'.') => {
            // Plain decimal degrees: \d+(\.\d+)?, must consume to the end.
            parse_decimal(s, 0)
        }
        Some(b'd') => {
            // DMS. The degree part is s[..i] (digits only — guaranteed, since we
            // scanned digits and s[i] is the 'd' separator).
            let deg = ascii_num_to_f64(&s[..i])?;
            i += 1; // skip 'd'
            let (min_val, next) = parse_decimal_span(s, i)?; // minutes: required
            i = next;
            if i >= s.len() || s[i] != b'\'' {
                return None;
            }
            i += 1; // skip '\''
            let mut sec_val = 0.0_f64;
            let mut has_sec = false;
            if i < s.len() && is_digit(s[i]) {
                let (v, next) = parse_decimal_span(s, i)?;
                sec_val = v;
                i = next;
                has_sec = true;
            }
            if i < s.len() {
                if s[i] != b'"' {
                    return None;
                }
                i += 1;
            }
            if i != s.len() {
                return None;
            }
            Some(deg + min_val / 60.0 + if has_sec { sec_val / 3600.0 } else { 0.0 })
        }
        _ => None,
    }
}

/// The quadrant `(base, sign)` for `(ns, ew)`, mirroring
/// `bearing.py::_QUADRANT_BASE` (result is `(base + sign * th) % 360`).
fn quadrant_base(ns: u8, ew: u8) -> (f64, f64) {
    match (ns.to_ascii_lowercase(), ew.to_ascii_lowercase()) {
        (b'n', b'e') => (90.0, -1.0), // 90 - th
        (b'n', b'w') => (90.0, 1.0),  // 90 + th
        (b's', b'e') => (270.0, 1.0), // 270 + th
        _ => (270.0, -1.0),           // (s, w): 270 - th
    }
}

/// Parse a bearing string (the full input) to CCW-from-+X degrees, mirroring
/// `bearing.py::bearing_angle`'s ACCEPT path. Returns `None` on any rejection
/// (the Python fallback produces the exact error string).
/// `pub(crate)` so the Q106-11 coordinate parser reuses the exact same bearing
/// grammar — the two cannot drift.
pub(crate) fn parse_bearing(s: &[u8]) -> Option<f64> {
    if s.len() > BEARING_MAX_INPUT_BYTES {
        return None;
    }
    let s = trim_ascii(s);
    let n = s.len();
    if n < 3 {
        return None;
    }
    let first = s[0];
    let last = s[n - 1];
    if !is_ns(first) || !is_ew(last) {
        return None;
    }
    // The middle (between first and last) must contain no E/W (the `[^EW]+`
    // requirement). It is non-empty (n >= 3).
    for &b in &s[1..n - 1] {
        if is_ew(b) {
            return None;
        }
    }
    let angle = trim_ascii(&s[1..n - 1]);
    let ang = parse_angle(angle)?;
    if !(0.0..=90.0).contains(&ang) {
        return None;
    }
    let (base, sign) = quadrant_base(first, last);
    Some((base + sign * ang) % 360.0)
}

/// Parse a surveyor coordinate (the full input) to `(distance, ccw_degrees)`,
/// mirroring `bearing.py::parse_surveyor`'s ACCEPT path. The Python structure
/// is `^\s*([+-]?\d+(?:\.\d+)?)\s*<\s*(.+)$`, then `bearing_angle` on the
/// captured bearing. Returns `None` on any rejection (→ Python fallback).
/// `pub(crate)` so the Q106-11 coordinate parser reuses the exact same surveyor
/// grammar — the two cannot drift.
pub(crate) fn parse_surveyor(s: &[u8]) -> Option<(f64, f64)> {
    if s.len() > BEARING_MAX_INPUT_BYTES {
        return None;
    }
    let mut i = 0;
    // leading \s*
    while i < s.len() && is_ascii_ws(s[i]) {
        i += 1;
    }
    // dist: [+-]?\d+(?:\.\d+)?
    if i < s.len() && (s[i] == b'+' || s[i] == b'-') {
        i += 1;
    }
    if i >= s.len() || !is_digit(s[i]) {
        return None;
    }
    let (dist, next) = parse_decimal_span(s, i)?;
    i = next;
    // \s*
    while i < s.len() && is_ascii_ws(s[i]) {
        i += 1;
    }
    // '<'
    if i >= s.len() || s[i] != b'<' {
        return None;
    }
    i += 1;
    // \s*
    while i < s.len() && is_ascii_ws(s[i]) {
        i += 1;
    }
    // bearing = .+ (one or more)
    if i >= s.len() {
        return None;
    }
    let ang = parse_bearing(&s[i..])?;
    Some((dist, ang))
}

// ---------------------------------------------------------------------------
// C exports.
// ---------------------------------------------------------------------------

/// Form a `&[u8]` from a raw pointer + length, or `None` if the pointer is
/// invalid (null with a non-zero length, or a length beyond `isize::MAX`).
fn byte_span<'a>(ptr: *const u8, len: usize) -> Option<&'a [u8]> {
    if len > BEARING_MAX_INPUT_BYTES {
        return None;
    }
    unsafe { crate::checked_slice(ptr, len) }
}

/// Parse a surveyor bearing (`N th E` / `N th W` / `S th E` / `S th W`) to
/// degrees counter-clockwise from +X. `BEARING_OK` + value in `out` on success;
/// `BEARING_ERR` on rejection (the Python layer produces the exact error string);
/// `BEARING_OUT` if `out` is null but a value was produced.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when `text_len == 0`),
/// and `out` must be writable for one `f64` (null only when the caller wants to
/// read only the error code).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_bearing(
    text: *const u8,
    text_len: usize,
    out: *mut f64,
) -> i32 {
    let out = match unsafe { crate::checked_slice_mut(out, 1) } {
        Some(out) => &mut out[0],
        None => return BEARING_OUT,
    };
    *out = 0.0;
    let span = match byte_span(text, text_len) {
        Some(s) => s,
        None => return BEARING_ERR,
    };
    match parse_bearing(span) {
        Some(v) => {
            *out = v;
            BEARING_OK
        }
        None => BEARING_ERR,
    }
}

/// Parse a surveyor coordinate (`distance<N/Sangle E/W`) to
/// `(distance, degrees CCW from +X)`. `BEARING_OK` + values in the out-params on
/// success; `BEARING_ERR` on rejection (the Python layer produces the exact error
/// string); `BEARING_OUT` if an out-param is null but a value was produced.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when `text_len == 0`),
/// and `out_dist` / `out_ang` must each be writable for one `f64` (null only when
/// the caller wants to read only the error code).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_surveyor(
    text: *const u8,
    text_len: usize,
    out_dist: *mut f64,
    out_ang: *mut f64,
) -> i32 {
    let mut out_dist = unsafe { crate::checked_slice_mut(out_dist, 1) }.map(|out| &mut out[0]);
    let mut out_ang = unsafe { crate::checked_slice_mut(out_ang, 1) }.map(|out| &mut out[0]);
    if let Some(out) = out_dist.as_deref_mut() {
        *out = 0.0;
    }
    if let Some(out) = out_ang.as_deref_mut() {
        *out = 0.0;
    }
    let (Some(out_dist), Some(out_ang)) = (out_dist, out_ang) else {
        return BEARING_OUT;
    };
    let span = match byte_span(text, text_len) {
        Some(s) => s,
        None => return BEARING_ERR,
    };
    match parse_surveyor(span) {
        Some((dist, ang)) => {
            *out_dist = dist;
            *out_ang = ang;
            BEARING_OK
        }
        None => BEARING_ERR,
    }
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn ok(s: &str) -> f64 {
        parse_bearing(s.as_bytes()).unwrap_or_else(|| panic!("expected ok: {s}"))
    }

    fn err(s: &str) {
        assert!(
            parse_bearing(s.as_bytes()).is_none(),
            "expected reject: {s}"
        );
    }

    fn okp(s: &str) -> (f64, f64) {
        parse_surveyor(s.as_bytes()).unwrap_or_else(|| panic!("expected ok: {s}"))
    }

    fn errp(s: &str) {
        assert!(
            parse_surveyor(s.as_bytes()).is_none(),
            "expected reject: {s}"
        );
    }

    #[test]
    fn quadrant_angles() {
        assert_eq!(ok("n30e"), 60.0);
        assert_eq!(ok("n30w"), 120.0);
        assert_eq!(ok("s30e"), 300.0);
        assert_eq!(ok("s30w"), 240.0);
        // 0 / 90 boundaries (both inclusive).
        assert_eq!(ok("N0E"), 90.0);
        assert_eq!(ok("N90E"), 0.0);
        assert_eq!(ok("N90W"), 180.0);
        assert_eq!(ok("S0W"), 270.0);
        assert_eq!(ok("S90E"), 0.0);
        assert_eq!(ok("N45W"), 135.0);
        assert_eq!(ok("S45W"), 225.0);
        assert_eq!(ok("S45E"), 315.0);
    }

    #[test]
    fn dms_angles() {
        let dms = 30.0 + 22.0 / 60.0 + 54.43 / 3600.0;
        // N...W: 90 + dms.
        assert!((ok("N30d22'54.43\"W") - (90.0 + dms)).abs() <= 1e-12);
        // N...W with minutes only (no seconds).
        assert!((ok("N30d22'W") - (90.0 + 30.0 + 22.0 / 60.0)).abs() <= 1e-12);
        // Plain degrees.
        assert_eq!(ok("N30W"), 120.0);
        // Decimal degrees.
        assert_eq!(ok("N30.5E"), 90.0 - 30.5);
    }

    #[test]
    fn case_and_spaces() {
        assert_eq!(ok("n30e"), 60.0);
        assert_eq!(ok("N 30 E"), 60.0);
        assert_eq!(ok("S 45 W"), 225.0);
        assert_eq!(ok("n 30 w"), 120.0);
    }

    #[test]
    fn bearing_rejections() {
        err("N95E"); // > 90
        err("E30N"); // E/W first
        err("N30"); // missing E/W
        err("30W"); // missing N/S
        err("N-5E"); // negative
        err("NabcE"); // non-numeric angle
        err("N30wE"); // E/W in the middle
        err(""); // empty
        err("N E"); // no angle
    }

    #[test]
    fn surveyor_ok() {
        let dms = 30.0 + 22.0 / 60.0 + 54.43 / 3600.0;
        let (d, a) = okp("11<N30d22'54.43\"W");
        assert_eq!(d, 11.0);
        assert!((a - (90.0 + dms)).abs() <= 1e-12);
        let (d, a) = okp("5<N45E");
        assert_eq!(d, 5.0);
        assert_eq!(a, 45.0);
        let (d, a) = okp("2.5<S30W");
        assert_eq!(d, 2.5);
        assert_eq!(a, 240.0);
    }

    #[test]
    fn surveyor_rejections() {
        errp("5<N95E");
        errp("5<E30N");
        errp("5<N30");
        errp("5<30W");
        errp("N30E");
        errp("");
        errp("11<");
    }

    #[test]
    fn malformed_abi_spans_are_refused_and_outputs_are_cleared() {
        let mut out = 42.0f64;
        assert_eq!(
            unsafe { narwhal_pure_core_bearing(std::ptr::null(), 1, &mut out) },
            BEARING_ERR
        );
        assert_eq!(out, 0.0);

        out = 42.0;
        let dangling = std::ptr::NonNull::<u8>::dangling().as_ptr();
        assert_eq!(
            unsafe { narwhal_pure_core_bearing(dangling, usize::MAX, &mut out) },
            BEARING_ERR
        );
        assert_eq!(out, 0.0);

        let mut dist = 42.0f64;
        let mut ang = 43.0f64;
        assert_eq!(
            unsafe { narwhal_pure_core_surveyor(std::ptr::null(), 1, &mut dist, &mut ang,) },
            BEARING_ERR
        );
        assert_eq!((dist, ang), (0.0, 0.0));
        let mut storage = [0.0f64; 2];
        let misaligned = storage
            .as_mut_ptr()
            .cast::<u8>()
            .wrapping_add(1)
            .cast::<f64>();
        assert_eq!(
            unsafe { narwhal_pure_core_bearing(b"N30E".as_ptr(), 4, misaligned) },
            BEARING_OUT
        );
    }
}
