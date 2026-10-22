//! Native coordinate text parser (Q106-11).
//!
//! A transparent accelerator for `narwhal3d/coords.py::parse`. It implements
//! the SAME text -> `(x, y, z, is_world, is_relative)` grammar as the Python
//! reference (prefixes, top-level commas, the `0`/`w0` origin shortcuts, polar /
//! spherical / surveyor forms, and every rejection) and returns the IDENTICAL
//! value on success; on any rejection it returns an error code and the Python
//! layer re-runs its UNCHANGED parser, so the exact public error string (which
//! embeds the input, e.g. `"a bare number is a distance constraint …"`) is
//! always produced by the original code. A fallback is therefore a correctness
//! fallback, not a slower one: the measured success path never touches the
//! Python parser.
//!
//! It REUSES the Q106-09 calculator (`crate::calc::core_evaluate`) for every
//! scalar (fractions, unit suffixes, formulas) and the Q106-10 bearing /
//! surveyor internals (`crate::bearing::parse_bearing` / `parse_surveyor`) for
//! the surveyor branch, so the two cannot drift: a number is valued by the
//! exact same code that values `calc.evaluate`, and a surveyor coordinate by the
//! exact same code that values `bearing.parse_surveyor`.
//!
//! Error contract: `COORDS_OK` (0) with the value(s) in the out-params on
//! success; `COORDS_ERR` on rejection (the exact error string comes from the
//! Python fallback); `COORDS_OUT` when an out-param is null but a value was
//! produced.
//!
//! Robustness bounds (a typed coordinate is short): the input length is capped.
//! An over-limit input is a rejection (→ Python fallback), never a mis-parse and
//! never an unbounded allocation. The cap (256 bytes) is far below the
//! calculator's own formula cap (1024), so every scalar this route feeds the
//! calculator is within the calculator's acceptance range.

use crate::bearing;
use crate::calc;

/// Success (the value(s) are in the out-params).
pub const COORDS_OK: i32 = 0;
/// A rejection (the exact error string comes from the Python fallback).
pub const COORDS_ERR: i32 = -1;
/// An out-param is null and a value was produced.
pub const COORDS_OUT: i32 = -2;

/// A typed coordinate is short; cap the input so parsing is bounded.
const COORDS_MAX_INPUT_BYTES: usize = 256;

// ---------------------------------------------------------------------------
// Small byte-level helpers (all operate on the ASCII subset this grammar uses;
// a non-ASCII byte simply pushes the input down the rejection → Python-fallback
// path, the safe direction).
// ---------------------------------------------------------------------------

#[inline]
fn is_ascii_ws(b: u8) -> bool {
    matches!(b, b' ' | b'\t' | b'\n' | b'\r' | 0x0C | 0x0B)
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

/// Convert an ASCII numeric byte slice to `f64` using Rust's correctly-rounded
/// conversion, which matches Python's `float(...)` for the forms this grammar
/// admits (`\d+\.?\d*` or `.\d+`, with an optional exponent).
fn ascii_num_to_f64(b: &[u8]) -> Option<f64> {
    if b.is_empty() {
        return None;
    }
    let s = std::str::from_utf8(b).ok()?;
    s.parse::<f64>().ok()
}

/// Parse one numeric token `[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?` starting
/// at `start` (which must point at the first significant character), returning
/// `(value, end)`. A `.` must be followed by at least one digit, and an exponent
/// must carry at least one digit — mirroring the Python `_NUM` shape.
fn parse_num_token(s: &[u8], start: usize) -> Option<(f64, usize)> {
    let n = s.len();
    let mut i = start;
    // optional sign
    if i < n && (s[i] == b'+' || s[i] == b'-') {
        i += 1;
    }
    let first = *s.get(i)?;
    if first == b'.' {
        // `.\d+`
        i += 1;
        if i >= n || !is_digit(s[i]) {
            return None;
        }
        while i < n && is_digit(s[i]) {
            i += 1;
        }
    } else if is_digit(first) {
        // `\d+`
        while i < n && is_digit(s[i]) {
            i += 1;
        }
        // optional `.\d*`
        if i < n && s[i] == b'.' {
            i += 1;
            while i < n && is_digit(s[i]) {
                i += 1;
            }
        }
    } else {
        return None;
    }
    // optional exponent `[eE][+-]?\d+`
    if i < n && (s[i] == b'e' || s[i] == b'E') {
        i += 1;
        if i < n && (s[i] == b'+' || s[i] == b'-') {
            i += 1;
        }
        if i >= n || !is_digit(s[i]) {
            return None;
        }
        while i < n && is_digit(s[i]) {
            i += 1;
        }
    }
    let val = ascii_num_to_f64(&s[start..i])?;
    Some((val, i))
}

/// The degrees-per-unit for a (lower-cased) angle unit word, mirroring
/// `coords.py::_ANGLE_WORDS`. `None` means "not an angle unit word".
fn angle_unit_degrees(word: &str) -> Option<f64> {
    use std::f64::consts::PI;
    Some(match word {
        "" | "d" | "deg" | "degree" | "degrees" => 1.0,
        "radian" | "radians" | "rad" => 180.0 / PI,
        "grade" | "grades" | "grad" | "gradian" | "gradians" => 0.9,
        _ => return None,
    })
}

/// Parse a `[+-]?\d+(?:\.\d+)?` (unsigned parts) starting at `start` (which
/// must point at a digit), returning `(value, end)`. A `.` must be followed by
/// at least one digit. Used for the DMS parts.
fn parse_dms_part(s: &[u8], start: usize) -> Option<(f64, usize)> {
    let n = s.len();
    if start >= n || !is_digit(s[start]) {
        return None;
    }
    let mut i = start;
    while i < n && is_digit(s[i]) {
        i += 1;
    }
    if i < n && s[i] == b'.' {
        i += 1;
        if i >= n || !is_digit(s[i]) {
            return None;
        }
        while i < n && is_digit(s[i]) {
            i += 1;
        }
    }
    let val = ascii_num_to_f64(&s[start..i])?;
    Some((val, i))
}

/// Parse a degrees-minutes-seconds angle (already lower-cased, whitespace
/// trimmed) to degrees, mirroring `coords.py::_DMS_RE`: an optional leading
/// sign, then an optional `d` part, an optional `'` part, an optional `"` part,
/// at least one present. The degree part is an integer; minutes and seconds are
/// unsigned. Returns `None` when the string is not a valid DMS angle.
fn parse_dms_angle(s: &[u8]) -> Option<f64> {
    let n = s.len();
    let mut i = 0;
    let sign: f64;
    if i < n && (s[i] == b'+' || s[i] == b'-') {
        sign = if s[i] == b'-' { -1.0 } else { 1.0 };
        i += 1;
    } else {
        sign = 1.0;
    }
    let mut deg: f64 = 0.0;
    let mut min: f64 = 0.0;
    let mut sec: f64 = 0.0;
    let mut any = false;
    // degree part: \d+ followed by 'd' (or the string end is NOT a valid
    // degree-only form — the regex requires at least one of d/'/").
    if i < n && is_digit(s[i]) {
        let mut j = i;
        while j < n && is_digit(s[j]) {
            j += 1;
        }
        if j < n && s[j] == b'd' {
            deg = ascii_num_to_f64(&s[i..j])?;
            any = true;
            j += 1; // skip 'd'
            if j >= n {
                return None; // "30d" — minutes required (the regex demands them)
            }
            i = j;
        } else {
            // no 'd' separator: this is not the degree part; leave i as-is.
        }
    }
    // minutes part: \d+(?:\.\d+)? followed by '\''
    if i < n && is_digit(s[i]) {
        let (v, next) = parse_dms_part(s, i)?;
        if next >= n || s[next] != b'\'' {
            return None;
        }
        min = v;
        any = true;
        i = next + 1; // skip '\''
    }
    // seconds part: \d+(?:\.\d+)? followed by '"'
    if i < n && is_digit(s[i]) {
        let (v, next) = parse_dms_part(s, i)?;
        if next >= n || s[next] != b'"' {
            return None;
        }
        sec = v;
        any = true;
        i = next + 1; // skip '"'
    }
    if !any || i != n {
        return None;
    }
    Some(sign * (deg + min / 60.0 + sec / 3600.0))
}

/// Parse a typed angle (the Python `parse_angle` ACCEPT path) to degrees.
/// Returns `None` on any rejection (→ Python fallback, which produces the exact
/// string). Mirrors `coords.py::parse_angle`'s two-branch acceptance:
///
/// 1. `[+-]?NUM [a-z]*` where the trailing word is a documented angle unit
///    (`""`, `d`/`deg`/`degree`/`degrees`, `radian`/`radians`/`rad`,
///    `grade`/`grades`/`grad`/`gradian`/`gradians`);
/// 2. degrees-minutes-seconds (`30d22'54.43"`, `22'`, `0d20'50"`, …).
///
/// The Python reference first lower-cases and strips; a `pi` / `π` / `*` / `(`
/// / `)` anywhere is a rejection (the calculator is refused by name) — those
/// characters can never appear in either accepted branch, so they are
/// rejections here too (the safe direction).
fn parse_angle(s: &[u8]) -> Option<f64> {
    let s = trim_ascii(s);
    if s.is_empty() {
        return None;
    }
    let lower: Vec<u8> = s.iter().map(|b| b.to_ascii_lowercase()).collect();
    let l: &[u8] = &lower;
    // Branch 1: number + unit word (the unit word is the trailing a-z run; an
    // empty word is the documented "no unit -> degrees" case).
    if let Some((val, end)) = parse_num_token(l, 0) {
        let unit_part = &l[end..];
        if unit_part.iter().all(|b| b.is_ascii_lowercase()) {
            if let Some(unit) = angle_unit_degrees(std::str::from_utf8(unit_part).ok()?) {
                return Some(val * unit);
            }
        }
    }
    // Branch 2: DMS.
    parse_dms_angle(l)
}

/// `text` split on `sep` (a single ASCII byte) OUTSIDE parentheses, mirroring
/// `coords.py::split_top_level` (`pow(2,3),4` -> 2 parts).
fn split_top_level(text: &str, sep: char) -> Vec<&str> {
    let sep_b = sep as u8;
    let mut parts: Vec<&str> = Vec::new();
    let mut depth: i32 = 0;
    let mut start = 0;
    for (idx, ch) in text.char_indices() {
        match ch {
            '(' => depth += 1,
            ')' => depth -= 1,
            c if c as u8 == sep_b && depth == 0 => {
                parts.push(&text[start..idx]);
                start = idx + 1;
            }
            _ => {}
        }
    }
    parts.push(&text[start..]);
    parts
}

/// A typed number, length or formula -> `f64` (model units), mirroring
/// `coords.py::parse_scalar`: a bare `float(text)` when possible, else the
/// Q106-09 calculator (`core_evaluate`) with the model unit.
fn parse_scalar(text: &str, model: &str) -> Result<f64, ()> {
    let t = text.trim();
    if t.is_empty() {
        return Err(());
    }
    if let Ok(v) = t.parse::<f64>() {
        return Ok(v);
    }
    calc::core_evaluate(t, model)
}

/// Parse one coordinate component (the Python `_number`): a scalar, refusing
/// the empty string.
fn number(text: &str, model: &str) -> Result<f64, ()> {
    let t = text.trim();
    if t.is_empty() {
        return Err(());
    }
    parse_scalar(t, model)
}

/// The parsed coordinate: the 3-tuple plus the world / relative flags.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Coord {
    x: f64,
    y: f64,
    z: f64,
    world: bool,
    relative: bool,
}

/// `d<a` / `d<a,z` (prefix already stripped) -> cartesian in the same system.
/// Mirrors `coords.py::_parse_polar`: spherical (`d<a<b`), `x,y<elevation`,
/// surveyor (`d<N/S…E/W`), and polar (`d<a` / `d<a,z`).
fn parse_polar(parts: &[&str], model: &str, world: bool, relative: bool) -> Result<Coord, ()> {
    use std::f64::consts::PI;
    // SPHERICAL: `d<a<b` (the first part has no comma).
    if parts.len() == 3 && !parts[0].contains(',') {
        let d = number(parts[0], model)?;
        let a1 = parse_angle(parts[1].as_bytes()).ok_or(())?;
        let a2 = parse_angle(parts[2].as_bytes()).ok_or(())?;
        let az = a1 * PI / 180.0;
        let el = a2 * PI / 180.0;
        let cos_el = (el).cos();
        Ok(Coord {
            x: d * cos_el * az.cos(),
            y: d * cos_el * az.sin(),
            z: d * el.sin(),
            world,
            relative,
        })
    } else if parts.len() == 2 && parts[0].contains(',') {
        // x,y<elevation
        let xy = split_top_level(parts[0], ',');
        let a2 = parse_angle(parts[1].as_bytes()).ok_or(())?;
        if xy.len() != 2 {
            return Err(());
        }
        let x = number(xy[0], model)?;
        let y = number(xy[1], model)?;
        let a2r = a2 * PI / 180.0;
        Ok(Coord {
            x,
            y,
            z: (x * x + y * y).sqrt() * a2r.tan(),
            world,
            relative,
        })
    } else if parts.len() > 2 {
        Err(())
    } else {
        // SURVEYOR: the part after the `<` begins with N or S.
        let right = parts[1];
        let first = right.as_bytes().first();
        if matches!(first, Some(b'N') | Some(b'n') | Some(b'S') | Some(b's')) {
            let joined = format!("{}<{}", parts[0], right);
            let (dist, ang) = bearing::parse_surveyor(joined.as_bytes()).ok_or(())?;
            let a = ang * PI / 180.0;
            Ok(Coord {
                x: dist * a.cos(),
                y: dist * a.sin(),
                z: 0.0,
                world,
                relative,
            })
        } else {
            // POLAR: `d<a` or `d<a,z`.
            let dist = number(parts[0], model)?;
            let pieces = split_top_level(right, ',');
            if pieces.len() > 2 {
                return Err(());
            }
            let ang = parse_angle(pieces[0].as_bytes()).ok_or(())?;
            let mut z = 0.0;
            if pieces.len() == 2 {
                z = number(pieces[1], model)?;
            }
            let a = ang * PI / 180.0;
            Ok(Coord {
                x: dist * a.cos(),
                y: dist * a.sin(),
                z,
                world,
                relative,
            })
        }
    }
}

/// Parse a coordinate (the full input) -> `(x, y, z, world, relative)`,
/// mirroring `coords.py::parse`'s ACCEPT path. Returns `Err` on any rejection
/// (the Python fallback produces the exact error string).
fn parse_coord(text: &str, model: &str) -> Result<Coord, ()> {
    let raw = text.trim();
    if raw.is_empty() {
        return Err(());
    }
    // "There are no spaces between the coordinate values."
    if raw.bytes().any(is_ascii_ws) {
        return Err(());
    }
    // Explicitly-refused forms (point filters, a bare `<` angle constraint).
    if raw.starts_with('.') {
        return Err(());
    }
    if raw.starts_with('<') {
        return Err(());
    }
    // Prefixes, longest first so `rw` is not eaten by `r`.
    let lower = raw.to_ascii_lowercase();
    let mut rest = raw;
    let mut is_relative = false;
    let mut is_world = false;
    for prefix in ["rw", "@w", "w", "r", "@"] {
        if lower.starts_with(prefix) {
            is_relative = matches!(prefix, "rw" | "@w" | "r" | "@");
            is_world = prefix.contains('w');
            rest = &rest[prefix.len()..];
            break;
        }
    }
    if rest.is_empty() {
        return Err(());
    }
    if rest.contains('<') {
        let parts: Vec<&str> = rest.split('<').collect();
        return parse_polar(&parts, model, is_world, is_relative);
    }
    let parts = split_top_level(rest, ',');
    if parts.len() > 3 {
        return Err(());
    }
    if parts.len() == 1 {
        // Documented shortcuts: `0` means 0,0 and `w0` means w0,0,0. Any OTHER
        // bare number is a distance constraint, not a coordinate.
        let value = number(parts[0], model)?;
        if value == 0.0 {
            return Ok(Coord {
                x: 0.0,
                y: 0.0,
                z: 0.0,
                world: is_world,
                relative: is_relative,
            });
        }
        return Err(());
    }
    let mut values: Vec<f64> = Vec::with_capacity(parts.len());
    for part in &parts {
        values.push(number(part, model)?);
    }
    while values.len() < 3 {
        values.push(0.0); // "When you omit the z coordinate, it is … 0"
    }
    Ok(Coord {
        x: values[0],
        y: values[1],
        z: values[2],
        world: is_world,
        relative: is_relative,
    })
}

// ---------------------------------------------------------------------------
// C export.
// ---------------------------------------------------------------------------

/// Form a `&[u8]` from a raw pointer + length, or `None` if the pointer is
/// invalid (null with a non-zero length, a length beyond `isize::MAX`, or a
/// length beyond the input cap).
fn byte_span<'a>(ptr: *const u8, len: usize) -> Option<&'a [u8]> {
    if len > COORDS_MAX_INPUT_BYTES {
        return None;
    }
    unsafe { crate::checked_slice(ptr, len) }
}

/// Parse a typed coordinate to `(x, y, z)` in the system the flags describe.
/// `COORDS_OK` + the values in the out-params on success; `COORDS_ERR` on
/// rejection (the Python layer re-runs its unchanged parser to produce the
/// exact error string); `COORDS_OUT` if an out-param is null but a value was
/// produced.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when
/// `text_len == 0`), `model` must be readable for `model_len` bytes (null only
/// when `model_len == 0`), `out_x` / `out_y` / `out_z` must each be writable
/// for one `f64`, and `out_world` / `out_relative` must each be writable for
/// one `u8` (each out-param may be null only when the caller wants to read only
/// the error code).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_coords(
    text: *const u8,
    text_len: usize,
    model: *const u8,
    model_len: usize,
    out_x: *mut f64,
    out_y: *mut f64,
    out_z: *mut f64,
    out_world: *mut u8,
    out_relative: *mut u8,
) -> i32 {
    let mut out_x = unsafe { crate::checked_slice_mut(out_x, 1) }.map(|out| &mut out[0]);
    let mut out_y = unsafe { crate::checked_slice_mut(out_y, 1) }.map(|out| &mut out[0]);
    let mut out_z = unsafe { crate::checked_slice_mut(out_z, 1) }.map(|out| &mut out[0]);
    let mut out_world = unsafe { crate::checked_slice_mut(out_world, 1) }.map(|out| &mut out[0]);
    let mut out_relative =
        unsafe { crate::checked_slice_mut(out_relative, 1) }.map(|out| &mut out[0]);
    for out in [&mut out_x, &mut out_y, &mut out_z] {
        if let Some(out) = out.as_deref_mut() {
            *out = 0.0;
        }
    }
    for out in [&mut out_world, &mut out_relative] {
        if let Some(out) = out.as_deref_mut() {
            *out = 0;
        }
    }
    let (Some(out_x), Some(out_y), Some(out_z), Some(out_world), Some(out_relative)) =
        (out_x, out_y, out_z, out_world, out_relative)
    else {
        return COORDS_OUT;
    };
    let text_span = match byte_span(text, text_len) {
        Some(s) => s,
        None => return COORDS_ERR,
    };
    let model_span = match byte_span(model, model_len) {
        Some(s) => s,
        None => return COORDS_ERR,
    };
    let text_str = match std::str::from_utf8(text_span) {
        Ok(s) => s,
        Err(_) => return COORDS_ERR,
    };
    let model_str = match std::str::from_utf8(model_span) {
        Ok(s) => s,
        Err(_) => return COORDS_ERR,
    };
    match parse_coord(text_str, model_str) {
        Ok(c) => {
            *out_x = c.x;
            *out_y = c.y;
            *out_z = c.z;
            *out_world = if c.world { 1 } else { 0 };
            *out_relative = if c.relative { 1 } else { 0 };
            COORDS_OK
        }
        Err(()) => COORDS_ERR,
    }
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn ok(s: &str) -> Coord {
        parse_coord(s, "Millimeters").unwrap_or_else(|_| panic!("expected ok: {s}"))
    }

    fn ok_model(s: &str, model: &str) -> Coord {
        parse_coord(s, model).unwrap_or_else(|_| panic!("expected ok: {s} ({model})"))
    }

    fn err(s: &str) {
        assert!(
            parse_coord(s, "Millimeters").is_err(),
            "expected reject: {s}"
        );
    }

    fn close(a: f64, b: f64) {
        assert!(
            (a - b).abs() <= 1e-12 * (1.0_f64.max(a.abs()).max(b.abs())),
            "{a} vs {b}"
        );
    }

    #[test]
    fn cartesian_and_flags() {
        let c = ok("1,2");
        close(c.x, 1.0);
        close(c.y, 2.0);
        close(c.z, 0.0);
        assert!(!c.world && !c.relative);

        let c = ok("1,2,3");
        close(c.x, 1.0);
        close(c.y, 2.0);
        close(c.z, 3.0);

        let c = ok("w1,2");
        assert!(c.world && !c.relative);
        let c = ok("r2,3");
        assert!(!c.world && c.relative);
        let c = ok("rw1,2");
        assert!(c.world && c.relative);
        let c = ok("@w1,2");
        assert!(c.world && c.relative);
        let c = ok("@1,2");
        assert!(!c.world && c.relative);
        // `rw` must NOT be eaten by `r`.
        let c = ok("rw0,5");
        assert!(c.world && c.relative, "rw swallowed by r");
        close(c.y, 5.0);
    }

    #[test]
    fn origin_shortcuts() {
        let c = ok("0");
        close(c.x, 0.0);
        close(c.y, 0.0);
        close(c.z, 0.0);
        assert!(!c.world && !c.relative);

        let c = ok("w0");
        close(c.x, 0.0);
        close(c.y, 0.0);
        close(c.z, 0.0);
        assert!(c.world && !c.relative);
    }

    #[test]
    fn polar_and_spherical() {
        // polar `17<45`
        let c = ok("17<45");
        close(c.x, 17.0 * (45.0_f64.to_radians()).cos());
        close(c.y, 17.0 * (45.0_f64.to_radians()).sin());
        close(c.z, 0.0);
        // polar + elevation `17<45,8`
        let c = ok("17<45,8");
        close(c.z, 8.0);
        // spherical `5<30<45` radius 5
        let c = ok("5<30<45");
        let r = (c.x * c.x + c.y * c.y + c.z * c.z).sqrt();
        close(r, 5.0);
        // x,y<elevation `5,6<15` (hypot(5,6) = sqrt(61))
        let c = ok("5,6<15");
        close(c.x, 5.0);
        close(c.y, 6.0);
        close(c.z, 61.0_f64.sqrt() * (15.0_f64.to_radians()).tan());
        // surveyor `11<N30d22'54.43"W`
        let dms: f64 = 30.0 + 22.0 / 60.0 + 54.43 / 3600.0;
        let want: f64 = 90.0 + dms;
        let c = ok("11<N30d22'54.43\"W");
        close(c.x, 11.0 * want.to_radians().cos());
        close(c.y, 11.0 * want.to_radians().sin());
        close(c.z, 0.0);
    }

    #[test]
    fn fractions_units_formulas() {
        let c = ok("5/16,1-3/4");
        close(c.x, 0.3125);
        close(c.y, 1.75);
        let c = ok_model("1.234cm,2", "Millimeters");
        close(c.x, 12.34);
        close(c.y, 2.0);
        let c = ok_model("16'5\",0", "Millimeters");
        close(c.x, 5003.8);
        let c = ok_model("10*sin(30d),pow(2,3)", "Millimeters");
        close(c.x, 5.0);
        close(c.y, 8.0);
        let c = ok_model("25.4mm,1", "Inches");
        close(c.x, 1.0);
        close(c.y, 1.0);
    }

    #[test]
    fn rejections() {
        err("5"); // bare non-zero number
        err("abc,1");
        err("1,2,3,4");
        err("w"); // prefix, no coordinates
        err("");
        err("1, 2"); // a space
        err(".x"); // point filter
        err(".wxy");
        err("<45"); // a bare angle constraint
        err("17<45<20<30"); // too many '<'
        err("w");
    }

    #[test]
    fn malformed_abi_spans_are_refused_and_outputs_are_cleared() {
        let mut x = 1.0f64;
        let mut y = 2.0f64;
        let mut z = 3.0f64;
        let mut world = 1u8;
        let mut relative = 1u8;
        let rc = unsafe {
            narwhal_pure_core_coords(
                std::ptr::null(),
                1,
                b"Millimeters".as_ptr(),
                11,
                &mut x,
                &mut y,
                &mut z,
                &mut world,
                &mut relative,
            )
        };
        assert_eq!(rc, COORDS_ERR);
        assert_eq!((x, y, z, world, relative), (0.0, 0.0, 0.0, 0, 0));

        x = 1.0;
        y = 2.0;
        z = 3.0;
        world = 1;
        relative = 1;
        let dangling = std::ptr::NonNull::<u8>::dangling().as_ptr();
        let rc = unsafe {
            narwhal_pure_core_coords(
                dangling,
                usize::MAX,
                b"Millimeters".as_ptr(),
                11,
                &mut x,
                &mut y,
                &mut z,
                &mut world,
                &mut relative,
            )
        };
        assert_eq!(rc, COORDS_ERR);
        assert_eq!((x, y, z, world, relative), (0.0, 0.0, 0.0, 0, 0));

        x = 1.0;
        y = 2.0;
        z = 3.0;
        relative = 1;
        assert_eq!(
            unsafe {
                narwhal_pure_core_coords(
                    b"1,2".as_ptr(),
                    3,
                    b"Millimeters".as_ptr(),
                    11,
                    &mut x,
                    &mut y,
                    &mut z,
                    std::ptr::null_mut(),
                    &mut relative,
                )
            },
            COORDS_OUT
        );
        assert_eq!((x, y, z, relative), (0.0, 0.0, 0.0, 0));
    }
}
