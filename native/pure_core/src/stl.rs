//! SPDX-License-Identifier: GPL-3.0-or-later
//!
//! Native STL facet encoding for the Narwhal 3D interchange layer.
//!
//! This module turns caller-owned packed `9 * count` `f64` triangle coordinates
//! into a complete STL file (binary or ASCII dialect). Python owns the path I/O
//! and is the unchanged fallback. Two invariants are load-bearing:
//!
//! * The facet normal is the normalised cross product `(b - a) x (c - a)`, and a
//!   degenerate (zero-area) facet is KEPT with a zero normal -- never dropped.
//! * The binary layout is exactly `84 + 50 * count` bytes and the 80-byte header
//!   must not begin with the ASCII keyword `solid` (some readers then read a
//!   binary file as ASCII).
//!
//! The output is a caller-owned buffer sized by the two-pass `..._size` export;
//! no Rust-owned pointer ever crosses the call.

use std::collections::HashMap;

const BIN_HEADER_LEN: usize = 80;
const BIN_FACET_LEN: usize = 50;
const DEFAULT_NAME: &[u8] = b"Narwhal 3D";

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;

/// Does the name begin with the ASCII keyword `solid` (case-insensitive, after
/// leading spaces)? Mirrors Python's `str(name).strip().lower().startswith("solid")`.
fn name_starts_with_solid(name: &[u8]) -> bool {
    let start = name.iter().position(|b| *b != b' ').unwrap_or(name.len());
    let rest = &name[start..];
    let prefix = b"solid";
    rest.len() >= prefix.len() && rest[..prefix.len()].eq_ignore_ascii_case(prefix)
}

/// Format an `f64` exactly as CPython's `"%.9g" % value`.
///
/// CPython's `%g` prints the value at 9 significant digits (IEEE double
/// rounding), strips trailing zeros, and switches to `%e` when the decimal
/// exponent is `< -4` or `>= precision` (9). The exponent is signed and
/// zero-padded to at least two digits (`e-05`, `e+09`). This reproduces that:
/// it takes the 9 significant digits and exponent from Rust's `{:.8e}` (which
/// uses the same IEEE rounding) and re-emits them in the `%g` shape.
fn format_9g(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_string();
    }
    if value.is_infinite() {
        return if value < 0.0 {
            "-inf".to_string()
        } else {
            "inf".to_string()
        };
    }
    let negative = value.is_sign_negative();
    let v = value.abs();
    if v == 0.0 {
        return if negative {
            "-0".to_string()
        } else {
            "0".to_string()
        };
    }
    // "d.ddddddde±EXP" with 9 significant digits (IEEE double rounding).
    let s = format!("{v:.8e}");
    let (mantissa, exp_str) = s.split_once('e').expect("exponent is present");
    let e: i32 = exp_str.parse().expect("exponent is a small integer");
    // The 9 significant digits: strip the leading digit, decimal point.
    let digits: String = mantissa.chars().filter(|c| c.is_ascii_digit()).collect();
    let mut out = String::with_capacity(24);
    if negative {
        out.push('-');
    }
    if !(-4..9).contains(&e) {
        // Scientific: `d.ddd e±EXP`, trailing fractional zeros stripped.
        let mut frac = digits[1..].to_string();
        while frac.ends_with('0') {
            frac.pop();
        }
        out.push(digits.as_bytes()[0] as char);
        if !frac.is_empty() {
            out.push('.');
            out.push_str(&frac);
        }
        out.push('e');
        out.push_str(&format!("{e:+03}"));
    } else {
        // Fixed: place the decimal point at the 10^e place, then strip zeros.
        let int_len = (e + 1).max(0) as usize;
        let (int_part, frac_part) = if int_len > 0 {
            (digits[..int_len].to_string(), digits[int_len..].to_string())
        } else {
            let lead = (-e - 1).max(0) as usize;
            (String::from("0"), format!("{}{}", "0".repeat(lead), digits))
        };
        // Only the fractional part may lose trailing zeros; the integer
        // part must keep every digit (`1e8` is `100000000`, not `1`).
        let mut frac = frac_part;
        while frac.ends_with('0') {
            frac.pop();
        }
        let mut fixed = int_part;
        if !frac.is_empty() {
            fixed.push('.');
            fixed.push_str(&frac);
        }
        out.push_str(&fixed);
    }
    out
}

/// Normalised `(b - a) x (c - a)`, or the zero vector for a degenerate facet.
///
/// Mirrors `stl_writer._facet_normal` bit-for-bit: the same f64 arithmetic order,
/// the same `m == 0` guard, and the same `n / m` divisions.
///
/// The norm uses `powf(0.5)`, NOT `sqrt()`: Python computes the norm as
/// `(...) ** 0.5`, which resolves to libm `pow(x, 0.5)`, and that is not
/// bit-identical to libm `sqrt(x)` in the last ULP (~1e-3 of random inputs).
/// Matching the exact operation keeps the native normal bit-identical to the
/// unchanged Python fallback, so the `%.9g` output can never drift.
fn facet_normal(a: [f64; 3], b: [f64; 3], c: [f64; 3]) -> [f64; 3] {
    let ux = b[0] - a[0];
    let uy = b[1] - a[1];
    let uz = b[2] - a[2];
    let vx = c[0] - a[0];
    let vy = c[1] - a[1];
    let vz = c[2] - a[2];
    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    let m = (nx * nx + ny * ny + nz * nz).powf(0.5);
    if m == 0.0 {
        [0.0, 0.0, 0.0]
    } else {
        [nx / m, ny / m, nz / m]
    }
}

/// A finite `f64` that does not overflow to `inf` when rounded to `f32`.
fn finite_f32(x: &f64) -> bool {
    x.is_finite() && (*x as f32).is_finite()
}

/// The three vertices of facet `i` from a packed `9 * count` coordinate array.
fn facet(coords: &[f64], i: usize) -> ([f64; 3], [f64; 3], [f64; 3]) {
    let base = i * 9;

    (
        [coords[base], coords[base + 1], coords[base + 2]],
        [coords[base + 3], coords[base + 4], coords[base + 5]],
        [coords[base + 6], coords[base + 7], coords[base + 8]],
    )
}

/// A checked view over `9 * count` `f64` coordinates, or `None` when the
/// pointer is null (for a non-empty input) or the span length would overflow.
///
/// This is the "checked byte span" the ABI requires before any slice is formed.
/// The lifetime is bound to the caller's buffer, which is valid across the call.
fn coords_span<'a>(coordinates: *const f64, count: usize) -> Option<&'a [f64]> {
    let len = count.checked_mul(9)?;
    unsafe { crate::checked_slice(coordinates, len) }
}

/// The exact byte length of the encoded ASCII STL file, measured from the
/// coordinates so the size query and the encoder can never drift apart.
///
/// `name` is the already-resolved (non-empty) ASCII name; the caller applies the
/// `name or "Narwhal 3D"` rule, so this function just measures the bytes.
fn add_size(total: &mut usize, amount: usize) -> Option<()> {
    *total = total.checked_add(amount)?;
    Some(())
}

fn ascii_size(count: usize, name: &[u8], coords: &[f64]) -> Option<usize> {
    let mut total = 0usize;
    add_size(&mut total, "solid ".len())?;
    add_size(&mut total, name.len())?;
    add_size(&mut total, 1)?;
    add_size(&mut total, "endsolid ".len())?;
    add_size(&mut total, name.len())?;
    add_size(&mut total, 1)?;
    for i in 0..count {
        let (a, b, c) = facet(coords, i);
        let n = facet_normal(a, b, c);
        add_size(&mut total, "  facet normal ".len())?;
        add_size(&mut total, format_9g(n[0]).len())?;
        add_size(&mut total, 1)?;
        add_size(&mut total, format_9g(n[1]).len())?;
        add_size(&mut total, 1)?;
        add_size(&mut total, format_9g(n[2]).len())?;
        add_size(&mut total, "\n  outer loop\n".len())?;
        for p in [a, b, c] {
            add_size(&mut total, "    vertex ".len())?;
            add_size(&mut total, format_9g(p[0]).len())?;
            add_size(&mut total, 1)?;
            add_size(&mut total, format_9g(p[1]).len())?;
            add_size(&mut total, 1)?;
            add_size(&mut total, format_9g(p[2]).len())?;
            add_size(&mut total, 1)?;
        }
        add_size(&mut total, "  endloop\n  endfacet\n".len())?;
    }
    Some(total)
}

fn encode_binary(name: &[u8], count: usize, coords: &[f64], out: &mut [u8]) {
    let mut off = 0usize;
    for b in name {
        out[off] = *b;
        off += 1;
    }
    while off < BIN_HEADER_LEN {
        out[off] = b' ';
        off += 1;
    }
    out[80..84].copy_from_slice(&(count as u32).to_le_bytes());
    let mut off = 84usize;
    for i in 0..count {
        let (a, b, c) = facet(coords, i);
        let n = facet_normal(a, b, c);
        let floats: [f32; 12] = [
            n[0] as f32,
            n[1] as f32,
            n[2] as f32,
            a[0] as f32,
            a[1] as f32,
            a[2] as f32,
            b[0] as f32,
            b[1] as f32,
            b[2] as f32,
            c[0] as f32,
            c[1] as f32,
            c[2] as f32,
        ];
        for f in floats {
            let bytes = f.to_le_bytes();
            out[off..off + 4].copy_from_slice(&bytes);
            off += 4;
        }
        out[off] = 0;
        out[off + 1] = 0;
        off += 2;
    }
}

fn encode_ascii(name: &[u8], count: usize, coords: &[f64], out: &mut [u8]) {
    // Build the ASCII dialect byte-by-byte into a Vec, then copy into the

    // caller-owned buffer. The name is appended raw (byte-for-byte), never

    // round-tripped through UTF-8, so any Latin-1 byte in the name is preserved

    // exactly as Python's `.encode("latin-1")` would emit it.

    let mut s: Vec<u8> = Vec::with_capacity(out.len());

    s.extend_from_slice(b"solid ");

    s.extend_from_slice(name);

    s.push(b'\n');

    for i in 0..count {
        let (a, b, c) = facet(coords, i);

        let n = facet_normal(a, b, c);

        s.extend_from_slice(b"  facet normal ");

        s.extend_from_slice(format_9g(n[0]).as_bytes());

        s.push(b' ');

        s.extend_from_slice(format_9g(n[1]).as_bytes());

        s.push(b' ');

        s.extend_from_slice(format_9g(n[2]).as_bytes());

        s.extend_from_slice(b"\n  outer loop\n");

        for p in [a, b, c] {
            s.extend_from_slice(b"    vertex ");

            s.extend_from_slice(format_9g(p[0]).as_bytes());

            s.push(b' ');

            s.extend_from_slice(format_9g(p[1]).as_bytes());

            s.push(b' ');

            s.extend_from_slice(format_9g(p[2]).as_bytes());

            s.push(b'\n');
        }

        s.extend_from_slice(b"  endloop\n  endfacet\n");
    }

    s.extend_from_slice(b"endsolid ");

    s.extend_from_slice(name);

    s.push(b'\n');

    let bytes = s.as_slice();

    out[..bytes.len()].copy_from_slice(bytes);
}

/// Encode a complete STL file (binary or ASCII) into a caller-owned buffer.
///
/// `coordinates` holds `9 * count` `f64` values, three per vertex in the order
/// `(a, b, c)`. `name` is a non-empty, valid Latin-1 string of at most 80
/// bytes. `out` must hold exactly the bytes the encoder will produce (the same
/// length `narwhal_pure_core_stl_size` returns).
///
/// Returns `0` on success, otherwise a negative stable error code.
///
/// # Safety
///
/// `coordinates` must be readable for `9 * count` `f64` values (null only when
/// `count == 0`), `name` readable for `name_len` bytes, and `out` writable for
/// the exact encoded length.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_stl_encode(
    coordinates: *const f64,
    count: usize,
    name: *const u8,
    name_len: usize,
    binary: u8,
    out: *mut u8,
    out_len: usize,
) -> i32 {
    if count > u32::MAX as usize {
        return ERR_SIZE;
    }
    let name_slice = match unsafe { crate::checked_slice(name, name_len) } {
        Some(name) => name,
        None => return ERR_NULL,
    };

    let coords = match coords_span(coordinates, count) {
        Some(c) if c.iter().all(finite_f32) => c,
        Some(_) => return ERR_INPUT,

        None => return ERR_NULL,
    };

    if binary == 1 {
        // Mirrors the Python binary branch exactly: the `solid` fallback and the
        // 80-byte truncation are binary-only; a non-solid name keeps its length.
        let name = if name_starts_with_solid(name_slice) {
            DEFAULT_NAME
        } else {
            &name_slice[..name_slice.len().min(BIN_HEADER_LEN)]
        };

        let need = match count
            .checked_mul(BIN_FACET_LEN)
            .and_then(|n| 84usize.checked_add(n))
        {
            Some(n) => n,

            None => return ERR_SIZE,
        };

        if out_len != need {
            return ERR_SIZE;
        }
        if out.is_null() {
            return ERR_NULL;
        }

        let out_slice = match unsafe { crate::checked_slice_mut(out, need) } {
            Some(out) => out,
            None => return ERR_SIZE,
        };

        encode_binary(name, count, coords, out_slice);

        return 0;
    }
    // Mirrors the Python ASCII branch exactly: `name or "Narwhal 3D"`, with no
    // `solid` fallback and no truncation.
    let name = if name_slice.is_empty() {
        DEFAULT_NAME
    } else {
        name_slice
    };

    let need = match ascii_size(count, name, coords) {
        Some(n) => n,
        None => return ERR_SIZE,
    };
    if out_len != need {
        return ERR_SIZE;
    }
    if out.is_null() {
        return ERR_NULL;
    }
    let out_slice = match unsafe { crate::checked_slice_mut(out, need) } {
        Some(out) => out,
        None => return ERR_SIZE,
    };
    encode_ascii(name, count, coords, out_slice);

    0
}

/// Two-pass size query: the exact byte length of the encoded STL file for a
/// valid input. Same validation as the encode export, no output.
///
/// # Safety
///
/// `coordinates` must be readable for `9 * count` `f64` values (null only when
/// `count == 0`) and `name` readable for `name_len` valid Latin-1 bytes.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_stl_size(
    coordinates: *const f64,
    count: usize,
    name: *const u8,
    name_len: usize,
    binary: u8,
) -> i64 {
    if count > u32::MAX as usize {
        return ERR_SIZE as i64;
    }
    let name_slice = match unsafe { crate::checked_slice(name, name_len) } {
        Some(name) => name,
        None => return ERR_NULL as i64,
    };

    let coords = match coords_span(coordinates, count) {
        Some(c) if c.iter().all(finite_f32) => c,
        Some(_) => return ERR_INPUT as i64,

        None => return ERR_NULL as i64,
    };

    if binary == 1 {
        // Binary size is `84 + 50 * count` regardless of the name (the header is

        // always 80 bytes), so the name does not enter the measurement here.

        return match count
            .checked_mul(BIN_FACET_LEN)
            .and_then(|n| 84usize.checked_add(n))
        {
            Some(n) => i64::try_from(n).unwrap_or(ERR_SIZE as i64),

            None => ERR_SIZE as i64,
        };
    }
    // ASCII branch: `name or "Narwhal 3D"`, no fallback/truncation.
    let name = if name_slice.is_empty() {
        DEFAULT_NAME
    } else {
        name_slice
    };
    ascii_size(count, name, coords)
        .and_then(|n| i64::try_from(n).ok())
        .unwrap_or(ERR_SIZE as i64)
}

// ---------------------------------------------------------------------------
// Decoding (two-pass, caller-owned buffers; the inverse of the encode above).
//
// `narwhal_pure_core_stl_size_decode` returns the number of `f64` vertex
// coordinates a valid STL file decodes to (or a negative error code);
// `narwhal_pure_core_stl_decode` fills a caller-owned `9 * count` `f64` buffer
// with the facet vertices in file order. Both detect the dialect themselves
// (a leading `solid` keyword after optional whitespace means ASCII, else
// binary), so the Python caller passes the raw file bytes and gets the
// triangles back. The binary layout is exactly `84 + 50 * count` bytes -- a
// different length is a truncated header or a facet-count mismatch and is
// rejected, never partially read.

/// True when `line` begins with the byte word `word`, where a word is followed
/// by a space, a tab, or end-of-line (so `facet` does not match `facets`, and
/// `vertex` does not match `vertices`). Case-sensitive, matching the writer's
/// lowercase output and Python's `str.startswith`.
fn word_start(line: &[u8], word: &[u8]) -> bool {
    line.len() >= word.len()
        && line[..word.len()].eq(word)
        && (line.len() == word.len() || line[word.len()] == b' ' || line[word.len()] == b'\t')
}

/// A facet line begins with the `vertex` keyword (word-bounded).
fn is_vertex_line(line: &[u8]) -> bool {
    word_start(line, b"vertex")
}

/// Split a byte slice on runs of spaces and tabs (the only whitespace that
/// survives the caller's line split + `trim_ascii`). A leading/trailing run
/// yields no empty token, matching `str.split()` on the trimmed line.
fn split_ws(b: &[u8]) -> Vec<&[u8]> {
    let mut out = Vec::new();
    let mut start = None;
    for (i, &c) in b.iter().enumerate() {
        if c == b' ' || c == b'\t' {
            if let Some(s) = start {
                out.push(&b[s..i]);
                start = None;
            }
        } else if start.is_none() {
            start = Some(i);
        }
    }
    if let Some(s) = start {
        out.push(&b[s..]);
    }
    out
}

/// Parse a `vertex x y z` line into three `f64` values, or `None` when the
/// line carries fewer/more than three components (an incomplete or malformed
/// vertex). The three tokens parse as Rust `f64`, matching CPython `%g` output.
fn parse_vertex(line: &[u8]) -> Option<[f64; 3]> {
    let rest = split_ws(&line[6..]);
    if rest.len() != 3 {
        return None;
    }
    let mut out = [0.0f64; 3];
    for (i, token) in rest.iter().enumerate() {
        let value = std::str::from_utf8(token).ok()?.parse::<f64>().ok()?;
        out[i] = value;
    }
    Some(out)
}

/// State machine shared by the size query and the decoder so the two passes
/// can never drift apart.
#[derive(Clone, Copy, PartialEq, Eq)]
enum AsciiState {
    BeforeFacet,
    InFacet,
    AfterSolid,
}

/// Scan an ASCII STL body, validating every facet has exactly three vertices
/// and a closing `endfacet`. Returns the number of `f64` values a decode would
/// produce, or `None` when the file is incomplete or malformed. A dangling
/// facet (no `endfacet` before `endsolid` or EOF) is rejected.
fn ascii_decode_size(data: &[u8]) -> Option<usize> {
    let mut state = AsciiState::BeforeFacet;
    let mut nv = 0usize;
    let mut facets = 0usize;
    for raw in data.split(|&b| b == b'\n' || b == b'\r') {
        let line = raw.trim_ascii();
        if line.is_empty() {
            continue;
        }
        match state {
            AsciiState::BeforeFacet => {
                if is_vertex_line(line) {
                    return None;
                }
                if word_start(line, b"facet") {
                    state = AsciiState::InFacet;
                    nv = 0;
                }
            }
            AsciiState::InFacet => {
                // A facet must be closed by `endfacet` before the next one
                // opens; a new `facet` here means the previous is incomplete.
                if word_start(line, b"facet") {
                    return None;
                }
                if is_vertex_line(line) {
                    parse_vertex(line)?;
                    if nv == 3 {
                        return None;
                    }
                    nv += 1;
                } else if word_start(line, b"endfacet") {
                    if nv != 3 {
                        return None;
                    }
                    facets = facets.checked_add(1)?;
                    state = AsciiState::BeforeFacet;
                }
            }
            AsciiState::AfterSolid => {
                return None;
            }
        }
        if state == AsciiState::BeforeFacet && word_start(line, b"endsolid") {
            state = AsciiState::AfterSolid;
        }
    }
    if state == AsciiState::InFacet {
        return None;
    }
    let values = facets.checked_mul(9)?;
    values
        .checked_mul(std::mem::size_of::<f64>())
        .filter(|&bytes| bytes <= isize::MAX as usize)?;
    Some(values)
}

/// Binary-consistency-first dialect detection, shared by the size query and
/// the decoder so the two passes can never disagree.
///
/// A file is BINARY when its length and facet count agree exactly with the
/// binary layout (`84 + 50 * count` bytes, `count` at offset 80). This is
/// checked BEFORE the `solid` keyword so that a binary file whose 80-byte
/// header happens to begin `solid` is still read as binary -- the keyword test
/// alone would misread it as ASCII. Otherwise, a file whose body begins the
/// `solid` keyword (after optional whitespace) is ASCII. Anything that matches
/// neither is rejected.
fn is_binary(bytes: &[u8]) -> Option<bool> {
    if bytes.len() >= 84 && (bytes.len() - 84).is_multiple_of(BIN_FACET_LEN) {
        let count = u32::from_le_bytes(bytes[80..84].try_into().unwrap()) as usize;
        if count
            .checked_mul(BIN_FACET_LEN)
            .and_then(|n| 84usize.checked_add(n))
            == Some(bytes.len())
        {
            return Some(true);
        }
    }
    // Skip any run of ASCII whitespace at the start of the body, then look
    // for the `solid` keyword (case-insensitive, word-bounded).
    let mut i = 0usize;
    while i < bytes.len()
        && (bytes[i] == b' ' || bytes[i] == b'\t' || bytes[i] == b'\n' || bytes[i] == b'\r')
    {
        i += 1;
    }
    let rest = &bytes[i..];
    if rest.len() >= 5 && rest[..5].eq_ignore_ascii_case(b"solid") {
        return Some(false);
    }
    None
}

/// Two-pass size query for the decoder: how many `f64` vertex coordinates
/// *data* decodes to, or a negative error code. Detects the dialect itself
/// (binary-consistency-first, then the ASCII `solid` keyword).
///
/// # Safety
///
/// `data` must be readable for `data_len` bytes (null only when `data_len == 0`).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_stl_decode_size(
    data: *const u8,
    data_len: usize,
) -> i64 {
    if data.is_null() {
        if data_len == 0 {
            return 0;
        }
        return ERR_NULL as i64;
    }
    let bytes = match unsafe { crate::checked_slice(data, data_len) } {
        Some(bytes) => bytes,
        None => return ERR_SIZE as i64,
    };
    let dialect = match is_binary(bytes) {
        Some(d) => d,
        None => return ERR_INPUT as i64,
    };
    if dialect {
        let count = u32::from_le_bytes(bytes[80..84].try_into().unwrap()) as usize;
        count
            .checked_mul(9)
            .and_then(|n| {
                n.checked_mul(std::mem::size_of::<f64>())
                    .map(|bytes| (n, bytes))
            })
            .filter(|(_, bytes)| *bytes <= isize::MAX as usize)
            .and_then(|(n, _)| i64::try_from(n).ok())
            .unwrap_or(ERR_SIZE as i64)
    } else {
        ascii_decode_size(bytes)
            .map(|n| n as i64)
            .unwrap_or(ERR_INPUT as i64)
    }
}

/// Decode *data* (a complete binary or ASCII STL file) into a caller-owned
/// `9 * count` `f64` buffer of facet vertices, in file order. Returns 0 on
/// success, a negative error code otherwise. The buffer length must equal the
/// value returned by `narwhal_pure_core_stl_decode_size`.
///
/// # Safety
///
/// `data` must be readable for `data_len` bytes and `out` writable for
/// `out_len` `f64` values (null only when `out_len == 0`).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_stl_decode(
    data: *const u8,
    data_len: usize,
    out: *mut f64,
    out_len: usize,
) -> i32 {
    if data.is_null() {
        // A null pointer with a zero length is an empty file (valid, decodes
        // to nothing); a null pointer with a positive length is a domain error.
        return if data_len == 0 { 0 } else { ERR_NULL };
    }
    let bytes = match unsafe { crate::checked_slice(data, data_len) } {
        Some(bytes) => bytes,
        None => return ERR_SIZE,
    };
    // The size query already validated the dialect and the binary length /
    // facet-count consistency (or the ASCII facet/vertex structure), so by the
    // time we get here `bytes` is a well-formed file of the reported size.
    let size = match narwhal_pure_core_stl_decode_size(data, data_len) {
        n if n >= 0 => n as usize,
        _ => return ERR_INPUT,
    };
    if out.is_null() {
        return if size == 0 { 0 } else { ERR_NULL };
    }
    if out_len != size {
        return ERR_SIZE;
    }
    let out_slice = match unsafe { crate::checked_slice_mut(out, size) } {
        Some(out) => out,
        None => return ERR_SIZE,
    };
    match is_binary(bytes) {
        Some(true) => {
            let count = u32::from_le_bytes(bytes[80..84].try_into().unwrap()) as usize;
            let mut o = 0usize;
            for i in 0..count {
                let fstart = 84 + i * BIN_FACET_LEN;
                // Skip the 12-byte normal; the next 9 floats are the vertices.
                for j in 0..9 {
                    let off = fstart + 12 + j * 4;
                    let word = u32::from_le_bytes(bytes[off..off + 4].try_into().unwrap());
                    out_slice[o] = f32::from_bits(word) as f64;
                    o += 1;
                }
            }
            0
        }
        Some(false) => {
            let mut state = AsciiState::BeforeFacet;
            let mut verts = [0.0f64; 9];
            let mut nvert = 0usize;
            let mut o = 0usize;
            for raw in bytes.split(|&b| b == b'\n' || b == b'\r') {
                let line = raw.trim_ascii();
                if line.is_empty() {
                    continue;
                }
                match state {
                    AsciiState::BeforeFacet => {
                        if word_start(line, b"facet") {
                            state = AsciiState::InFacet;
                            nvert = 0;
                        }
                    }
                    AsciiState::InFacet => {
                        if word_start(line, b"facet") {
                            // The size query already rejected an open facet
                            // before a new one opens; this is unreachable.
                            return ERR_INPUT;
                        }
                        if is_vertex_line(line) {
                            // The size query already validated that each vertex
                            // line carries exactly three parseable components
                            // and that each facet has exactly three vertices, so
                            // the index and the parse cannot fail here.
                            let value = parse_vertex(line).unwrap_or([0.0f64; 3]);
                            for (k, v) in value.iter().enumerate() {
                                verts[nvert + k] = *v;
                            }
                            nvert += 3;
                        } else if word_start(line, b"endfacet") {
                            for v in verts.iter() {
                                out_slice[o] = *v;
                                o += 1;
                            }
                            nvert = 0;
                            state = AsciiState::BeforeFacet;
                        }
                    }
                    AsciiState::AfterSolid => {}
                }
                if state == AsciiState::BeforeFacet && word_start(line, b"endsolid") {
                    state = AsciiState::AfterSolid;
                }
            }
            0
        }
        // The size query already rejected a file that matches neither dialect.
        None => ERR_INPUT,
    }
}

type WeldedMesh = (Vec<[f64; 3]>, Vec<[u32; 3]>);

fn weld_flat(flat: &[f64]) -> Option<WeldedMesh> {
    if !flat.len().is_multiple_of(9) {
        return None;
    }
    let mut by_point: HashMap<(u64, u64, u64), u32> = HashMap::new();
    let mut vertices = Vec::with_capacity(flat.len() / 3);
    let mut faces = Vec::with_capacity(flat.len() / 9);
    let key = |v: f64| if v == 0.0 { 0 } else { v.to_bits() };
    for triangle in flat.chunks_exact(9) {
        let mut face = [0u32; 3];
        for corner in 0..3 {
            let point = [
                triangle[3 * corner],
                triangle[3 * corner + 1],
                triangle[3 * corner + 2],
            ];
            if !point.iter().all(|v| v.is_finite()) {
                return None;
            }
            let point_key = (key(point[0]), key(point[1]), key(point[2]));
            face[corner] = match by_point.get(&point_key) {
                Some(index) => *index,
                None => {
                    let index = u32::try_from(vertices.len()).ok()?;
                    vertices.push(point);
                    by_point.insert(point_key, index);
                    index
                }
            };
        }
        if face[0] != face[1] && face[0] != face[2] && face[1] != face[2] {
            faces.push(face);
        }
    }
    Some((vertices, faces))
}

/// Decode and weld an STL directly into caller-owned indexed-mesh buffers.
/// Counts are published only after a complete successful parse, weld and copy.
///
/// # Safety
///
/// Input data is readable for `data_len`; output pointers are exclusively
/// writable for their declared capacities; count pointers are valid slots; and
/// input/output regions do not overlap.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_stl_mesh_decode(
    data: *const u8,
    data_len: usize,
    out_vertices: *mut f64,
    vertex_capacity: usize,
    out_indices: *mut u32,
    index_capacity: usize,
    out_vertex_count: *mut i64,
    out_face_count: *mut i64,
) -> i32 {
    if out_vertex_count.is_null() || out_face_count.is_null() {
        return ERR_NULL;
    }
    let coords = match unsafe { narwhal_pure_core_stl_decode_size(data, data_len) } {
        n if n >= 0 => n as usize,
        n => return n as i32,
    };
    let triangle_count = coords / 9;
    if vertex_capacity < triangle_count.saturating_mul(3)
        || index_capacity < triangle_count.saturating_mul(3)
    {
        return ERR_SIZE;
    }
    let mut flat = vec![0.0f64; coords];
    let rc = unsafe { narwhal_pure_core_stl_decode(data, data_len, flat.as_mut_ptr(), flat.len()) };
    if rc != 0 {
        return rc;
    }
    let (vertices, faces) = match weld_flat(&flat) {
        Some(result) => result,
        None => return ERR_INPUT,
    };
    let vertex_values = match vertices.len().checked_mul(3) {
        Some(n) => n,
        None => return ERR_SIZE,
    };
    let index_values = match faces.len().checked_mul(3) {
        Some(n) => n,
        None => return ERR_SIZE,
    };
    let vertex_out = match unsafe { crate::checked_slice_mut(out_vertices, vertex_values) } {
        Some(out) => out,
        None => return if vertex_values == 0 { 0 } else { ERR_NULL },
    };
    let index_out = match unsafe { crate::checked_slice_mut(out_indices, index_values) } {
        Some(out) => out,
        None => return if index_values == 0 { 0 } else { ERR_NULL },
    };
    for (i, point) in vertices.iter().enumerate() {
        vertex_out[3 * i..3 * i + 3].copy_from_slice(point);
    }
    for (i, face) in faces.iter().enumerate() {
        index_out[3 * i..3 * i + 3].copy_from_slice(face);
    }
    *out_vertex_count = vertices.len() as i64;
    *out_face_count = faces.len() as i64;
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unit_cube() -> [[f64; 3]; 36] {
        let v000 = [0.0, 0.0, 0.0];
        let v100 = [1.0, 0.0, 0.0];
        let v010 = [0.0, 1.0, 0.0];
        let v110 = [1.0, 1.0, 0.0];
        let v001 = [0.0, 0.0, 1.0];
        let v101 = [1.0, 0.0, 1.0];
        let v011 = [0.0, 1.0, 1.0];
        let v111 = [1.0, 1.0, 1.0];
        [
            v001, v101, v111, v001, v111, v011, v100, v000, v010, v110, v100, v010, v100, v110,
            v111, v111, v101, v100, v010, v000, v001, v011, v010, v001, v110, v010, v011, v011,
            v111, v110, v000, v100, v101, v101, v001, v000,
        ]
    }

    fn flat(tris: &[[f64; 3]; 36]) -> Vec<f64> {
        tris.iter().flat_map(|t| t.iter().copied()).collect()
    }

    #[test]
    fn welded_cube_is_first_seen_indexed_mesh() {
        let source = flat(&unit_cube());
        let (vertices, faces) = weld_flat(&source).expect("cube weld");
        assert_eq!(vertices.len(), 8);
        assert_eq!(faces.len(), 12);
        assert_eq!(vertices[0], [0.0, 0.0, 1.0]);
        assert_eq!(faces[0], [0, 1, 2]);
    }

    #[test]
    fn weld_canonicalizes_signed_zero_and_drops_degenerate_faces() {
        let flat = [
            -0.0, 0.0, 0.0, 0.0, -0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0,
            0.0,
        ];
        let (vertices, faces) = weld_flat(&flat).expect("finite weld");
        assert_eq!(vertices.len(), 3, "signed zeros are one Python tuple key");
        assert_eq!(faces, vec![[0, 2, 1]], "first degenerate face is dropped");
    }

    #[test]
    fn weld_rejects_non_finite_coordinates() {
        let mut flat = vec![0.0; 9];
        flat[4] = f64::NAN;
        assert!(weld_flat(&flat).is_none());
    }

    #[test]
    fn format_9g_matches_python_semantics() {
        assert_eq!(format_9g(0.0), "0");
        assert_eq!(format_9g(-0.0), "-0");
        assert_eq!(format_9g(1.0), "1");
        assert_eq!(format_9g(0.1), "0.1");
        assert_eq!(format_9g(0.001), "0.001");
        assert_eq!(format_9g(0.0001), "0.0001");
        assert_eq!(format_9g(0.00001), "1e-05");
        // CPython MEASURED: `"%.9g" % (1e7,)` -> `10000000` (exp 7 < 9 stays fixed).
        assert_eq!(format_9g(1e7), "10000000");
        assert_eq!(format_9g(1e8), "100000000");
        assert_eq!(format_9g(1e-4), "0.0001");
        assert_eq!(format_9g(9e-5), "9e-05");
        assert_eq!(format_9g(1e9), "1e+09");
        assert_eq!(format_9g(1e15), "1e+15");
        assert_eq!(format_9g(1e16), "1e+16");
        assert_eq!(format_9g(-1.5), "-1.5");
        assert_eq!(format_9g(123456.789), "123456.789");
        assert_eq!(format_9g(1.23456789e-07), "1.23456789e-07");
        assert_eq!(format_9g(1e308), "1e+308");
        assert_eq!(format_9g(f64::NAN), "nan");
        assert_eq!(format_9g(f64::INFINITY), "inf");
    }

    #[test]
    fn facet_normal_is_unit_and_outward() {
        let n = facet_normal([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]);
        assert!((n[2] - 1.0).abs() < 1e-15);
        assert!(n[0].abs() < 1e-15 && n[1].abs() < 1e-15);
    }

    #[test]
    fn facet_normal_zero_area_is_zero() {
        let n = facet_normal([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]);
        assert_eq!(n, [0.0, 0.0, 0.0]);
    }

    unsafe fn encode(
        count: usize,
        name: &[u8],
        binary: bool,
        coords: *const f64,
    ) -> Option<Vec<u8>> {
        let size =
            narwhal_pure_core_stl_size(coords, count, name.as_ptr(), name.len(), binary as u8);
        if size < 0 {
            return None;
        }
        let mut out = vec![0u8; size as usize];
        let rc = narwhal_pure_core_stl_encode(
            coords,
            count,
            name.as_ptr(),
            name.len(),
            binary as u8,
            out.as_mut_ptr(),
            out.len(),
        );
        if rc != 0 {
            return None;
        }
        Some(out)
    }

    #[test]
    fn binary_cube_layout_and_header() {
        let tris = unit_cube();
        let coords = flat(&tris);
        let out = unsafe { encode(12, b"Narwhal 3D", true, coords.as_ptr()) }.expect("encode");
        assert_eq!(out.len(), 84 + 12 * 50);
        assert_eq!(&out[..10], b"Narwhal 3D");
        assert!(out[10..80].iter().all(|b| *b == b' '));
        let count = u32::from_le_bytes(out[80..84].try_into().unwrap());
        assert_eq!(count, 12);
    }

    #[test]

    fn binary_name_solid_falls_back() {
        let tris = unit_cube();

        let coords = flat(&tris);

        let out = unsafe { encode(12, b"solid cube", true, coords.as_ptr()) }.expect("encode");

        assert_eq!(u32::from_le_bytes(out[80..84].try_into().unwrap()), 12);

        assert!(
            out[..80].windows(5).all(|w| w != b"solid"),
            "header must not start with solid"
        );
    }

    #[test]
    fn binary_name_long_truncates_to_80() {
        let tris = unit_cube();
        let coords = flat(&tris);
        let name = vec![b'X'; 100];

        let out = unsafe { encode(12, &name, true, coords.as_ptr()) }.expect("encode");

        // 100-char name must NOT be rejected; it is truncated to the 80-byte header.

        assert_eq!(out.len(), 84 + 12 * 50);

        assert_eq!(&out[..80], vec![b'X'; 80].as_slice());

        assert_eq!(u32::from_le_bytes(out[80..84].try_into().unwrap()), 12);
    }

    #[test]
    fn binary_name_empty_is_all_spaces() {
        let tris = unit_cube();
        let coords = flat(&tris);
        let out = unsafe { encode(12, b"", true, coords.as_ptr()) }.expect("encode");
        assert_eq!(out.len(), 84 + 12 * 50);
        assert!(
            out[..80].iter().all(|b| *b == b' '),
            "empty name -> 80 spaces"
        );
    }

    #[test]
    fn ascii_name_no_solid_fallback_and_no_truncation() {
        let tris = unit_cube();
        let coords = flat(&tris);
        // A `solid`-prefixed name is NOT replaced in the ASCII dialect (Python parity).
        let out = unsafe { encode(12, b"solid cube", false, coords.as_ptr()) }.expect("encode");
        let text = core::str::from_utf8(&out).unwrap();
        assert!(text.starts_with("solid solid cube\n"), "{}", text);
        assert!(text.ends_with("endsolid solid cube\n"), "{}", text);
        // A long name is NOT truncated in the ASCII dialect either.
        let name = vec![b'Y'; 100];

        let out = unsafe { encode(12, &name, false, coords.as_ptr()) }.expect("encode");
        let text = core::str::from_utf8(&out).unwrap();
        assert!(text.starts_with("solid "), "{}", text);
        assert!(
            text.contains(&"Y".repeat(100)),
            "ascii name must be untruncated"
        );
        assert!(
            text.ends_with(&format!("endsolid {}\n", "Y".repeat(100))),
            "{}",
            text
        );
    }

    #[test]
    fn ascii_name_empty_falls_back_to_default() {
        let tris = unit_cube();
        let coords = flat(&tris);
        let out = unsafe { encode(12, b"", false, coords.as_ptr()) }.expect("encode");
        let text = core::str::from_utf8(&out).unwrap();
        assert!(text.starts_with("solid Narwhal 3D\n"), "{}", text);
        assert!(text.ends_with("endsolid Narwhal 3D\n"), "{}", text);
    }

    #[test]
    fn ascii_layout() {
        let tris = unit_cube();
        let coords = flat(&tris);
        let out = unsafe { encode(12, b"Narwhal 3D", false, coords.as_ptr()) }.expect("encode");
        let text = core::str::from_utf8(&out).unwrap();
        assert!(text.starts_with("solid Narwhal 3D\n"), "{}", text);
        assert!(text.ends_with("endsolid Narwhal 3D\n"), "{}", text);
        assert!(
            text.contains("  facet normal 0 1 0"),
            "normal of the -z face"
        );
        assert!(text.contains("    vertex 0 0 0"), "{}", text);
    }

    #[test]
    fn degenerate_facet_kept_zero_normal() {
        let coords: Vec<f64> = vec![0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0];
        let out = unsafe { encode(1, b"Narwhal 3D", true, coords.as_ptr()) }.expect("encode");
        assert_eq!(out.len(), 84 + 50);
        assert_eq!(&out[84..96], &[0u8; 12], "zero normal");
    }

    #[test]
    fn size_query_matches_encode() {
        let tris = unit_cube();
        let coords = flat(&tris);
        for binary in [true, false] {
            let size = unsafe {
                narwhal_pure_core_stl_size(
                    coords.as_ptr(),
                    12,
                    b"Narwhal 3D".as_ptr(),
                    10,
                    binary as u8,
                )
            };
            assert!(size > 0);
            let out =
                unsafe { encode(12, b"Narwhal 3D", binary, coords.as_ptr()) }.expect("encode");
            assert_eq!(out.len() as i64, size);
        }
    }

    #[test]
    fn rejects_non_finite_coordinates() {
        let coords: Vec<f64> = vec![0.0, 0.0, 0.0, 1.0, 0.0, 0.0, f64::NAN, 0.0, 0.0];
        let rc = unsafe {
            narwhal_pure_core_stl_size(coords.as_ptr(), 1, b"Narwhal 3D".as_ptr(), 10, 1)
        };
        assert_eq!(rc, ERR_INPUT as i64);
    }

    #[test]
    fn rejects_null_coordinates_when_nonempty() {
        let rc = unsafe {
            narwhal_pure_core_stl_encode(
                core::ptr::null(),
                1,
                b"Narwhal 3D".as_ptr(),
                10,
                1,
                [0u8; 84 + 50].as_mut_ptr(),
                84 + 50,
            )
        };
        assert_eq!(rc, ERR_NULL);
    }

    #[test]
    fn rejects_wrong_output_size() {
        let tris = unit_cube();
        let coords = flat(&tris);
        let rc = unsafe {
            narwhal_pure_core_stl_encode(
                coords.as_ptr(),
                12,
                b"Narwhal 3D".as_ptr(),
                10,
                1,
                [0u8; 100].as_mut_ptr(),
                100,
            )
        };
        assert_eq!(rc, ERR_SIZE);
    }

    #[test]
    fn empty_input_is_valid() {
        let out = unsafe { encode(0, b"Narwhal 3D", true, core::ptr::null()) }.expect("encode");
        assert_eq!(out.len(), 84);
        assert_eq!(u32::from_le_bytes(out[80..84].try_into().unwrap()), 0);
    }

    // ------------------------------------------------------------------
    // Decoder tests (the inverse of the encode above).
    // ------------------------------------------------------------------

    unsafe fn decode(data: &[u8]) -> Option<Vec<f64>> {
        let size = narwhal_pure_core_stl_decode_size(data.as_ptr(), data.len());
        if size < 0 {
            return None;
        }
        let mut out = vec![0.0f64; size as usize];
        let rc =
            narwhal_pure_core_stl_decode(data.as_ptr(), data.len(), out.as_mut_ptr(), out.len());
        if rc != 0 {
            return None;
        }
        Some(out)
    }

    fn push_facet(data: &mut Vec<u8>, v: [f32; 3]) {
        // A single 50-byte facet: zero normal (12), the same vertex three times
        // (9 floats / 36), and a zero attribute byte count (2).
        data.extend_from_slice(&[0u8; 12]);
        for _ in 0..3 {
            for c in v.iter() {
                data.extend_from_slice(&c.to_le_bytes());
            }
        }
        data.extend_from_slice(&0u16.to_le_bytes());
    }

    #[test]
    fn decode_binary_cube_round_trips() {
        let tris = unit_cube();
        let coords = flat(&tris);
        let out = unsafe { encode(12, b"Narwhal 3D", true, coords.as_ptr()) }.expect("encode");
        let decoded = unsafe { decode(&out) }.expect("decode");
        assert_eq!(decoded.len(), 108, "12 facets * 9 floats");
        for i in 0..coords.len() {
            assert!(
                (decoded[i] - coords[i]).abs() <= 1e-6 * (1.0 + coords[i].abs()),
                "{}",
                i
            );
        }
    }

    #[test]
    fn decode_ascii_cube_round_trips() {
        let tris = unit_cube();
        let coords = flat(&tris);
        let out = unsafe { encode(12, b"Narwhal 3D", false, coords.as_ptr()) }.expect("encode");
        let decoded = unsafe { decode(&out) }.expect("decode");
        assert_eq!(decoded.len(), 108, "12 facets * 9 floats");
        for i in 0..coords.len() {
            assert_eq!(decoded[i], coords[i], "{}", i);
        }
    }

    #[test]
    fn decode_empty_binary_is_zero() {
        let out = unsafe { encode(0, b"Narwhal 3D", true, core::ptr::null()) }.expect("encode");
        assert_eq!(out.len(), 84);
        let decoded = unsafe { decode(&out) }.expect("decode");
        assert_eq!(decoded.len(), 0);
    }

    #[test]
    fn decode_empty_ascii_is_zero() {
        let data = b"solid Narwhal 3D\nendsolid Narwhal 3D\n";
        let decoded = unsafe { decode(data) }.expect("decode");
        assert_eq!(decoded.len(), 0);
    }

    #[test]
    fn decode_truncated_binary_header_is_rejected() {
        let tris = unit_cube();
        let coords = flat(&tris);
        let full = unsafe { encode(12, b"Narwhal 3D", true, coords.as_ptr()) }.expect("encode");
        // 80 bytes: the facet count is missing, so it is neither a valid
        // binary layout nor an ASCII body.
        let data = &full[..80];
        let size = unsafe { narwhal_pure_core_stl_decode_size(data.as_ptr(), data.len()) };
        assert!(size < 0, "{}", size);
    }

    #[test]
    fn decode_truncated_facet_is_rejected() {
        let tris = unit_cube();
        let coords = flat(&tris);
        let full = unsafe { encode(2, b"Narwhal 3D", true, coords.as_ptr()) }.expect("encode");
        assert_eq!(full.len(), 84 + 2 * 50);
        // The file claims two facets but carries only one: the binary length
        // does not match 84 + 50*count, so it must be rejected.
        let data = &full[..84 + 50];
        let size = unsafe { narwhal_pure_core_stl_decode_size(data.as_ptr(), data.len()) };
        assert!(size < 0, "{}", size);
    }

    #[test]
    fn decode_incomplete_ascii_is_rejected() {
        // A facet opened but never closed by `endfacet` before `endsolid`.
        let data = b"solid cube\n  facet normal 0 0 1\n    vertex 0 0 0\nendsolid cube\n";
        let size = unsafe { narwhal_pure_core_stl_decode_size(data.as_ptr(), data.len()) };
        assert!(size < 0, "{}", size);
        // And a facet with too few vertices.
        let data2 = b"solid cube\n  facet normal 0 0 1\n    vertex 0 0 0\n    vertex 1 0 0\n  endfacet\nendsolid cube\n";
        let size2 = unsafe { narwhal_pure_core_stl_decode_size(data2.as_ptr(), data2.len()) };
        assert!(size2 < 0, "{}", size2);
    }

    #[test]
    fn decode_binary_header_solid_is_still_binary() {
        // A binary STL whose 80-byte header begins with the word `solid`. The
        // dialect test must read it as binary (length + count are consistent
        // with the binary layout), not as ASCII.
        let mut data = Vec::with_capacity(84 + 50);
        let mut header = [0u8; 80];
        header[..5].copy_from_slice(b"solid");
        for b in header.iter_mut().skip(5) {
            *b = b' ';
        }
        data.extend_from_slice(&header);
        data.extend_from_slice(&1u32.to_le_bytes());
        data.extend_from_slice(&[0u8; 12]); // zero normal
        let v: [[f32; 3]; 3] = [[0.5, 0.0, 0.0], [0.5, 1.0, 0.0], [0.5, 0.5, 1.0]];
        for p in v.iter() {
            for c in p.iter() {
                data.extend_from_slice(&c.to_le_bytes());
            }
        }
        data.extend_from_slice(&0u16.to_le_bytes());
        let decoded = unsafe { decode(&data) }.expect("decode");
        let want: Vec<f64> = v.iter().flat_map(|p| p.iter().map(|c| *c as f64)).collect();
        assert_eq!(decoded, want);
    }

    #[test]
    fn decode_stride_is_50_bytes() {
        // A 2-facet binary STL where the two facets carry different vertex
        // values. The decoder must step each facet by exactly 50 bytes; a bug
        // that uses a 48- or 52-byte stride reads the second facet's vertices
        // from the wrong offset and returns wrong values for the second
        // triangle, which this test catches.
        let mut data = Vec::with_capacity(84 + 2 * 50);
        let header = [0x20u8; 80]; // spaces
        data.extend_from_slice(&header);
        data.extend_from_slice(&2u32.to_le_bytes());
        push_facet(&mut data, [1.0, 2.0, 3.0]);
        push_facet(&mut data, [4.0, 5.0, 6.0]);
        assert_eq!(data.len(), 84 + 2 * 50);
        let decoded = unsafe { decode(&data) }.expect("decode");
        assert_eq!(decoded.len(), 18);
        assert_eq!(
            &decoded[0..9],
            &[1.0, 2.0, 3.0, 1.0, 2.0, 3.0, 1.0, 2.0, 3.0]
        );
        assert_eq!(
            &decoded[9..18],
            &[4.0, 5.0, 6.0, 4.0, 5.0, 6.0, 4.0, 5.0, 6.0]
        );
    }

    #[test]
    fn raw_name_and_input_spans_are_checked_before_slicing() {
        let dangling = std::ptr::NonNull::<u8>::dangling().as_ptr();
        assert_eq!(
            unsafe { narwhal_pure_core_stl_size(std::ptr::null(), 0, std::ptr::null(), 1, 1) },
            ERR_NULL as i64
        );
        assert_eq!(
            unsafe { narwhal_pure_core_stl_size(std::ptr::null(), 0, dangling, usize::MAX, 1) },
            ERR_NULL as i64
        );
        assert_eq!(
            unsafe { narwhal_pure_core_stl_decode_size(dangling, usize::MAX) },
            ERR_SIZE as i64
        );
    }

    #[test]
    fn triangle_count_must_fit_binary_u32_without_partial_output() {
        let count = u32::MAX as usize + 1;
        let coords = std::ptr::NonNull::<f64>::dangling().as_ptr();
        let mut out = [0xA5u8; 84];
        assert_eq!(
            unsafe { narwhal_pure_core_stl_size(coords, count, std::ptr::null(), 0, 1) },
            ERR_SIZE as i64
        );
        assert_eq!(
            unsafe {
                narwhal_pure_core_stl_encode(
                    coords,
                    count,
                    std::ptr::null(),
                    0,
                    1,
                    out.as_mut_ptr(),
                    out.len(),
                )
            },
            ERR_SIZE
        );
        assert_eq!(out, [0xA5; 84]);
    }
}
