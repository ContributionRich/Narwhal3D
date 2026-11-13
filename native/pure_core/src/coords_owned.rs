//! RUST-COORDS-13: coordinate/scalar/angle grammar and error ownership.
//! MEASURED legacy precedence: spike/coords_reference.py. No geometry/Undo.
//! OURS: 4096 code points, inherited 64-level formula bound, finite results.
//! UTF-32 offsets retain lone surrogates for host repr; no Python parser replay.
use crate::numparse_owned::{normalized, LIMIT};
use crate::{bearing_owned as bearing, calc_owned as calc};
use std::f64::consts::PI;

#[repr(C)]
#[derive(Default, Debug, Clone, Copy)]
pub struct Reply {
    pub code: u32,
    pub flags: u32,
    pub values: [f64; 3],
    pub start: u32,
    pub end: u32,
    pub calc: calc::Reply,
    pub bearing: bearing::Reply,
}
fn error(code: u32, a: usize, b: usize) -> Reply {
    Reply {
        code,
        start: a as u32,
        end: b as u32,
        ..Reply::default()
    }
}
fn space(c: u32) -> bool {
    char::from_u32(c).is_some_and(crate::python_text::is_space)
}
fn trim(s: &[u32], mut a: usize, mut b: usize) -> (usize, usize) {
    while a < b && space(s[a]) {
        a += 1;
    }
    while a < b && space(s[b - 1]) {
        b -= 1;
    }
    (a, b)
}
/// Matches Python's loop, including negative/unbalanced parenthesis depths.
pub(crate) fn split(s: &[u32], a: usize, b: usize, sep: Option<u32>) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    let mut depth = 0i32;
    let mut start = a;
    for (i, &c) in s.iter().enumerate().take(b).skip(a) {
        if c == 40 {
            depth += 1;
        } else if c == 41 {
            depth -= 1;
        }
        if Some(c) == sep && depth == 0 {
            out.push((start, i));
            start = i + 1;
        }
    }
    out.push((start, b));
    out
}
// Python float(str): decimal Unicode digits, underscores only BETWEEN digits,
// optional sign, decimal point and exponent. No fraction/unit/formula grammar.
fn decimal(s: &[u8], mut p: usize, sign: bool, underscores: bool) -> Option<usize> {
    if sign && matches!(s.get(p), Some(b'+' | b'-')) {
        p += 1;
    }
    let mut digits = 0;
    loop {
        if s.get(p).is_some_and(u8::is_ascii_digit) {
            digits += 1;
            p += 1;
        } else if underscores
            && digits > 0
            && s.get(p) == Some(&b'_')
            && s.get(p + 1).is_some_and(u8::is_ascii_digit)
        {
            p += 1;
        } else {
            break;
        }
    }
    if s.get(p) == Some(&b'.') {
        p += 1;
        let mut frac = 0;
        loop {
            if s.get(p).is_some_and(u8::is_ascii_digit) {
                frac += 1;
                p += 1;
            } else if underscores
                && frac > 0
                && s.get(p) == Some(&b'_')
                && s.get(p + 1).is_some_and(u8::is_ascii_digit)
            {
                p += 1;
            } else {
                break;
            }
        }
        digits += frac;
    }
    if digits == 0 {
        return None;
    }
    if matches!(s.get(p), Some(b'e' | b'E')) {
        // Regex matching backtracks out of an incomplete exponent, float's
        // complete-consumption check subsequently rejects the trailing text.
        let old = p;
        p += 1;
        if matches!(s.get(p), Some(b'+' | b'-')) {
            p += 1;
        }
        let start = p;
        while s.get(p).is_some_and(u8::is_ascii_digit)
            || (underscores
                && p > start
                && s.get(p) == Some(&b'_')
                && s.get(p + 1).is_some_and(u8::is_ascii_digit))
        {
            p += 1;
        }
        if p == start {
            p = old;
        }
    }
    Some(p)
}
fn float_text(s: &[u32]) -> Option<f64> {
    let n = normalized(s, 151);
    let low = n.to_ascii_lowercase();
    if matches!(
        low.as_str(),
        "nan" | "+nan" | "-nan" | "inf" | "+inf" | "-inf" | "infinity" | "+infinity" | "-infinity"
    ) {
        return Some(f64::INFINITY);
    }
    if decimal(n.as_bytes(), 0, true, true) != Some(n.len()) {
        return None;
    }
    n.replace('_', "").parse().ok()
}
fn scalar(
    s: &[u32],
    a: usize,
    b: usize,
    model: &[u32],
    model_string: bool,
    component: bool,
) -> Result<f64, Reply> {
    let (a, b) = trim(s, a, b);
    if a == b {
        return Err(error(if component { 16 } else { 15 }, a, b));
    }
    if let Some(v) = float_text(&s[a..b]) {
        return if v.is_finite() {
            Ok(v)
        } else {
            Err(error(17, a, b))
        };
    }
    let r = calc::evaluate(&s[a..b], model, model_string);
    if r.code != 0 {
        return Err(Reply {
            calc: r,
            ..error(20, a, b)
        });
    }
    Ok(r.value)
}
fn angle(s: &[u32], a: usize, b: usize) -> Result<f64, Reply> {
    let (lo, hi) = trim(s, a, b);
    if lo == hi {
        return Err(error(12, a, b));
    }
    let lower: Vec<u32> = s[lo..hi]
        .iter()
        .flat_map(|&cp| {
            char::from_u32(cp)
                .map(|c| c.to_lowercase().map(u32::from).collect::<Vec<_>>())
                .unwrap_or(vec![cp])
        })
        .collect();
    if lower.contains(&960)
        || lower.windows(2).any(|p| p == [112, 105])
        || lower.iter().any(|c| matches!(c, 40..=42))
    {
        return Err(error(13, a, b));
    }
    let n = normalized(&lower, 151);
    let bytes = n.as_bytes();
    let value = if let Some(end) = decimal(bytes, 0, true, false) {
        let factor = match &n[end..] {
            "" | "d" | "deg" | "degree" | "degrees" => Some(1.0),
            "radian" | "radians" | "rad" => Some(180.0 / PI),
            "grade" | "grades" | "grad" | "gradian" | "gradians" => Some(0.9),
            _ => None,
        };
        factor.map(|f| n[..end].parse::<f64>().unwrap_or(f64::INFINITY) * f)
    } else {
        None
    };
    let value = if let Some(v) = value {
        v
    } else {
        let mut p = 0;
        let mut sign = 1.0;
        if matches!(bytes.first(), Some(b'+' | b'-')) {
            if bytes[0] == b'-' {
                sign = -1.0;
            }
            p = 1;
        }
        let mut parts = [0.0; 3];
        let mut any = false;
        for (i, marker) in [b'd', b'\'', b'"'].iter().enumerate() {
            if let Some(end) = decimal(bytes, p, false, false) {
                if bytes.get(end) == Some(marker) {
                    parts[i] = n[p..end].parse::<f64>().unwrap_or(f64::INFINITY);
                    p = end + 1;
                    any = true;
                }
            }
        }
        if !any || p != bytes.len() {
            return Err(error(14, a, b));
        }
        let v = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0;
        if sign < 0.0 {
            -v
        } else {
            v
        }
    };
    if !value.is_finite() {
        return Err(error(17, a, b));
    }
    Ok(value)
}
fn coord(s: &[u32], model: &[u32], ms: bool) -> Result<Reply, Reply> {
    let (a, b) = trim(s, 0, s.len());
    if a == b {
        return Err(error(1, a, b));
    }
    if s[a..b].iter().copied().any(space) {
        return Err(error(2, a, b));
    }
    if s[a] == 46 {
        return Err(error(3, a, b));
    }
    if s[a] == 60 {
        return Err(error(4, a, b));
    }
    let mut p = a;
    let mut flags = 0;
    // Longest first. Do not consume 'r' before trying 'rw'.
    for (prefix, f) in [("rw", 3), ("@w", 3), ("w", 1), ("r", 2), ("@", 2)] {
        if b - a >= prefix.len()
            && s[a..a + prefix.len()]
                .iter()
                .map(|&c| {
                    if c < 128 {
                        (c as u8).to_ascii_lowercase() as u32
                    } else {
                        c
                    }
                })
                .eq(prefix.bytes().map(u32::from))
        {
            p += prefix.len();
            flags = f;
            break;
        }
    }
    if p == b {
        return Err(error(5, a, b));
    }
    let num = |a, b| scalar(s, a, b, model, ms, true);
    let mut values = [0.0; 3];
    if s[p..b].contains(&60) {
        let mut parts = Vec::new();
        let mut start = p;
        for (i, &c) in s.iter().enumerate().take(b).skip(p) {
            if c == 60 {
                parts.push((start, i));
                start = i + 1;
            }
        }
        parts.push((start, b));
        let (d0, d1) = parts[0];
        if parts.len() == 3 && !s[d0..d1].contains(&44) {
            let d = num(d0, d1)?;
            let az = angle(s, parts[1].0, parts[1].1)? * (PI / 180.0);
            let el = angle(s, parts[2].0, parts[2].1)? * (PI / 180.0);
            values = [
                d * el.cos() * az.cos(),
                d * el.cos() * az.sin(),
                d * el.sin(),
            ];
        } else if parts.len() == 2 && s[d0..d1].contains(&44) {
            let xy = split(s, d0, d1, Some(44));
            // Angle error takes precedence over malformed x,y count.
            let el = angle(s, parts[1].0, parts[1].1)? * (PI / 180.0);
            if xy.len() != 2 {
                return Err(error(7, a, b));
            }
            let x = num(xy[0].0, xy[0].1)?;
            let y = num(xy[1].0, xy[1].1)?;
            values = [x, y, crate::python_norm::hypot2(x, y) * el.tan()];
        } else if parts.len() > 2 {
            return Err(error(8, a, b));
        } else {
            let (r0, r1) = parts[1];
            if r0 < r1 && matches!(s[r0], 78 | 110 | 83 | 115 | 0x17f) {
                let r = bearing::parse(&s[p..b], 1);
                if r.code != 0 {
                    return Err(Reply {
                        bearing: r,
                        ..error(21, p, b)
                    });
                }
                let rad = r.value * (PI / 180.0);
                values = [r.distance * rad.cos(), r.distance * rad.sin(), 0.0];
            } else {
                let d = num(d0, d1)?;
                let pieces = split(s, r0, r1, Some(44));
                if pieces.len() > 2 {
                    return Err(error(9, a, b));
                }
                let rad = angle(s, pieces[0].0, pieces[0].1)? * (PI / 180.0);
                let z = if pieces.len() == 2 {
                    num(pieces[1].0, pieces[1].1)?
                } else {
                    0.0
                };
                values = [d * rad.cos(), d * rad.sin(), z];
            }
        }
    } else {
        let parts = split(s, p, b, Some(44));
        if parts.len() > 3 {
            return Err(error(6, a, b));
        }
        if parts.len() == 1 {
            if num(p, b)? != 0.0 {
                return Err(error(10, a, b));
            }
        } else {
            for (i, (a, b)) in parts.into_iter().enumerate() {
                values[i] = num(a, b)?;
            }
        }
    }
    if values.iter().any(|v| !v.is_finite()) {
        return Err(error(17, a, b));
    }
    Ok(Reply {
        flags,
        values,
        ..Reply::default()
    })
}
pub fn parse(s: &[u32], model: &[u32], ms: bool, mode: u32) -> Reply {
    if s.len() > LIMIT || model.len() > LIMIT {
        return error(18, 0, 0);
    }
    let result = match mode {
        0 => coord(s, model, ms),
        1 => scalar(s, 0, s.len(), model, ms, false).map(|v| Reply {
            values: [v, 0.0, 0.0],
            ..Reply::default()
        }),
        3 => {
            // float(bytes/bytearray) admits ASCII only and bytes.strip() has
            // already run in the host admission layer. Never reinterpret a
            // non-ASCII byte as Unicode whitespace or a decimal digit.
            if s.is_empty() {
                Err(error(15, 0, 0))
            } else if s.iter().any(|&c| c >= 128) {
                Err(error(22, 0, s.len()))
            } else if let Some(v) = float_text(s) {
                if v.is_finite() {
                    Ok(Reply {
                        values: [v, 0.0, 0.0],
                        ..Reply::default()
                    })
                } else {
                    Err(error(17, 0, s.len()))
                }
            } else {
                Err(error(22, 0, s.len()))
            }
        }
        _ => angle(s, 0, s.len()).map(|v| Reply {
            values: [v, 0.0, 0.0],
            ..Reply::default()
        }),
    };
    result.unwrap_or_else(|e| e)
}
#[no_mangle]
pub extern "C" fn narwhal_pure_core_coords_owned_abi1() -> u32 {
    1
}
/// mode 0 coordinate, 1 scalar, 2 angle, 3 ASCII byte-scalar; Unicode 151; model_kind 0 nonstring,
/// 1 string. Transport errors leave ALL outputs unchanged. Reply errors have
/// zero flags/values. No allocation or pointer survives the call.
/// # Safety
/// Spans must be initialized/readable; output writable/exclusive/nonoverlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_coords_owned_v1(
    input: *const u32,
    input_len: usize,
    model: *const u32,
    model_len: usize,
    model_kind: u32,
    mode: u32,
    unicode: u32,
    out: *mut Reply,
    out_size: usize,
) -> u32 {
    if out_size != std::mem::size_of::<Reply>()
        || model_kind > 1
        || mode > 3
        || unicode != 151
        || (model_kind == 0 && model_len != 0)
    {
        return 1;
    }
    if input_len > LIMIT || model_len > LIMIT {
        let Some(out) = (unsafe { crate::checked_slice_mut(out, 1) }) else {
            return 1;
        };
        out[0] = error(18, 0, 0);
        return 0;
    }
    let (Some(s), Some(m)) = (unsafe { crate::checked_slice(input, input_len) }, unsafe {
        crate::checked_slice(model, model_len)
    }) else {
        return 1;
    };
    let Some(out) = (unsafe { crate::checked_slice_mut(out, 1) }) else {
        return 1;
    };
    out[0] = parse(s, m, model_kind == 1, mode);
    0
}
/// Top-level split spans, caller-owned pairs [start,end]. Query with null spans
/// and capacity=0 returns count; insufficient capacity/invalid metadata writes
/// nothing, including count. sep_kind=0 means a separator that cannot match a
/// single character (legacy multi-character/empty separators).
/// # Safety
/// All nonempty spans must be valid, aligned, initialized and nonoverlapping.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_coords_split_v1(
    input: *const u32,
    len: usize,
    sep: u32,
    sep_kind: u32,
    spans: *mut u32,
    capacity: usize,
    count: *mut u32,
) -> u32 {
    if len > LIMIT || sep_kind > 1 {
        return 1;
    }
    let Some(s) = (unsafe { crate::checked_slice(input, len) }) else {
        return 1;
    };
    let result = split(s, 0, len, if sep_kind == 1 { Some(sep) } else { None });
    if capacity != 0 && capacity < result.len() * 2 {
        return 1;
    }
    if capacity == 0 && !spans.is_null() {
        return 1;
    }
    let Some(count) = (unsafe { crate::checked_slice_mut(count, 1) }) else {
        return 1;
    };
    if capacity != 0 {
        let Some(out) = (unsafe { crate::checked_slice_mut(spans, capacity) }) else {
            return 1;
        };
        for (i, (a, b)) in result.iter().enumerate() {
            out[i * 2] = *a as u32;
            out[i * 2 + 1] = *b as u32;
        }
    }
    count[0] = result.len() as u32;
    0
}
#[cfg(test)]
mod tests {
    use super::*;
    fn run(s: &str) -> Reply {
        parse(&s.chars().map(u32::from).collect::<Vec<_>>(), &[], true, 0)
    }
    #[test]
    fn arithmetic() {
        assert_eq!(run("2+3,4").values, [5.0, 4.0, 0.0]);
    }
    #[test]
    fn prefixes() {
        assert_eq!(run("rw1,2").flags, 3);
    }
    #[test]
    fn errors() {
        assert_eq!(run("1,2,3,4").code, 6);
    }
    #[test]
    fn compensated_projection_bits() {
        // CPython 3.13: libm hypot rounds this pair one ULP differently.
        let reply = run("8.48216097256762e99,-4.99676055319471e99<15");
        assert_eq!(reply.code, 0);
        assert_eq!(reply.values[2].to_bits(), 0x54934bca8d4aa5e8);
    }
    #[test]
    fn scalar_safety() {
        assert_eq!(run("nan,1").code, 17);
        assert_eq!(run("1_2,٣").values, [12.0, 3.0, 0.0]);
    }
}
