//! RUST-BEARING-12: complete surveyor grammar and classified errors.
//! MEASURED against spike/bearing_reference.py, including regex capture greed.
//! OURS: 4096 Python-code-point limit and finite numbers only. No geometry.
use crate::numparse_owned::{normalized, LIMIT};

pub const BEARING: u32 = 1;
pub const SURVEYOR: u32 = 2;
pub const ANGLE: u32 = 3;
pub const QUADRANT: u32 = 4;
pub const BOUND: u32 = 5;
pub const FINITE: u32 = 6;

/// ABI 2, 32 bytes. start/end are original UTF-32 code-point offsets.
/// value is the angle on success, or offending angle for QUADRANT;
/// distance is only set on surveyor success. reserved is always zero.
#[repr(C)]
#[derive(Default, Debug, Clone, Copy)]
pub struct Reply {
    pub code: u32,
    pub reserved: u32,
    pub value: f64,
    pub distance: f64,
    pub start: u32,
    pub end: u32,
}
fn error(code: u32, start: usize, end: usize) -> Reply {
    Reply {
        code,
        start: start as u32,
        end: end as u32,
        ..Reply::default()
    }
}
fn space(cp: u32) -> bool {
    char::from_u32(cp).is_some_and(crate::python_text::is_space)
}
fn trim(input: &[u32], mut a: usize, mut b: usize) -> (usize, usize) {
    while a < b && space(input[a]) {
        a += 1;
    }
    while a < b && space(input[b - 1]) {
        b -= 1;
    }
    (a, b)
}
fn ew(cp: u32) -> bool {
    matches!(cp, 69 | 87 | 101 | 119)
}
fn ns(cp: u32) -> bool {
    matches!(cp, 78 | 83 | 110 | 115 | 0x17f)
}
fn natural(s: &[u8], mut p: usize) -> usize {
    while p < s.len() && s[p].is_ascii_digit() {
        p += 1;
    }
    p
}
fn decimal(s: &[u8], p: usize) -> Option<usize> {
    let mut end = natural(s, p);
    if end == p {
        return None;
    }
    if s.get(end) == Some(&b'.') {
        let fraction = natural(s, end + 1);
        if fraction > end + 1 {
            end = fraction;
        }
    }
    Some(end)
}
fn number(s: &[u8]) -> f64 {
    // normalized() produces ASCII, and grammar admits only decimal literals.
    std::str::from_utf8(s)
        .unwrap()
        .parse::<f64>()
        .unwrap_or(f64::INFINITY)
}
fn angle(input: &[u32], norm: &[u8], a: usize, b: usize) -> Result<f64, Reply> {
    let (lo, hi) = trim(input, a, b);
    let s = &norm[lo..hi];
    let bad = || error(ANGLE, a, b);
    let d = natural(s, 0);
    if d == 0 {
        return Err(bad());
    }
    let value = if s.get(d) == Some(&b'd') {
        let m = d + 1;
        let me = decimal(s, m).ok_or_else(bad)?;
        if s.get(me) != Some(&b'\'') {
            return Err(bad());
        }
        let se = decimal(s, me + 1).unwrap_or(me + 1);
        let end = se + usize::from(s.get(se) == Some(&b'"'));
        if end != s.len() {
            return Err(bad());
        }
        let mut value = number(&s[..d]);
        value += number(&s[m..me]) / 60.0;
        if se > me + 1 {
            value += number(&s[me + 1..se]) / 3600.0;
        }
        value
    } else {
        if decimal(s, 0) != Some(s.len()) {
            return Err(bad());
        }
        number(s)
    };
    if !value.is_finite() {
        return Err(error(FINITE, a, b));
    }
    Ok(value)
}
fn bearing(input: &[u32], norm: &[u8], a: usize, b: usize) -> Reply {
    let bad = || error(BEARING, a, b);
    let (lo, hi) = trim(input, a, b);
    if hi - lo < 3 || !ns(input[lo]) || !ew(input[hi - 1]) {
        return bad();
    }
    // [^EW]+ is greedy and retains all trailing whitespace. The preceding
    // greedy \s* gives back one character if the entire angle is whitespace.
    if input[lo + 1..hi - 1].iter().copied().any(ew) {
        return bad();
    }
    let mut start = lo + 1;
    while start < hi - 1 && space(input[start]) {
        start += 1;
    }
    if start == hi - 1 {
        start -= 1;
    }
    let value = match angle(input, norm, start, hi - 1) {
        Ok(v) => v,
        Err(e) => return e,
    };
    if value > 90.0 {
        return Reply {
            value,
            ..error(QUADRANT, start, hi - 1)
        };
    }
    let north = matches!(input[lo], 78 | 110);
    let east = matches!(input[hi - 1], 69 | 101);
    let base = if north { 90.0 } else { 270.0 };
    let sign = if north == east { -1.0 } else { 1.0 };
    Reply {
        value: (base + sign * value) % 360.0,
        ..Reply::default()
    }
}
pub fn parse(input: &[u32], mode: u32) -> Reply {
    if input.len() > LIMIT {
        return error(BOUND, 0, 0);
    }
    let normalized = normalized(input, 151);
    let norm = normalized.as_bytes();
    if mode == 0 {
        return bearing(input, norm, 0, input.len());
    }
    let bad = || error(SURVEYOR, 0, input.len());
    let mut p = 0;
    while p < input.len() && space(input[p]) {
        p += 1;
    }
    let ds = p;
    if matches!(norm.get(p), Some(b'+' | b'-')) {
        p += 1;
    }
    let Some(de) = decimal(norm, p) else {
        return bad();
    };
    p = de;
    while p < input.len() && space(input[p]) {
        p += 1;
    }
    if norm.get(p) != Some(&b'<') {
        return bad();
    }
    p += 1;
    let first = p;
    while p < input.len() && space(input[p]) {
        p += 1;
    }
    // Python .+ excludes LF; $ admits exactly one final LF. Greedy \s*
    // backtracks when no .+ capture remains (including whitespace-only tails).
    let mut end = input.len();
    if input.last() == Some(&10) {
        end -= 1;
    }
    loop {
        if p < end && !input[p..end].contains(&10) {
            break;
        }
        if p == first {
            return bad();
        }
        p -= 1;
    }
    let distance = number(&norm[ds..de]);
    if !distance.is_finite() {
        return error(FINITE, ds, de);
    }
    let mut reply = bearing(input, norm, p, end);
    if reply.code == 0 {
        reply.distance = distance;
    }
    reply
}
#[no_mangle]
pub extern "C" fn narwhal_pure_core_bearing_abi2() -> u32 {
    2
}

/// mode: 0 bearing_angle, 1 parse_surveyor; unicode must be 151.
/// Return 0 for a classified Reply, 1 for invalid output metadata,
/// 2 for invalid input metadata/mode/Unicode. Bounds are checked before reads.
/// # Safety
/// Nonempty spans must be readable/writable, initialized and nonaliasing.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_bearing_v2(
    input: *const u32,
    input_len: usize,
    mode: u32,
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
    if mode > 1 || unicode != 151 {
        return 2;
    }
    if input_len > LIMIT {
        output[0] = error(BOUND, 0, 0);
        return 0;
    }
    let Some(input) = (unsafe { crate::checked_slice(input, input_len) }) else {
        return 2;
    };
    output[0] = parse(input, mode);
    0
}
#[cfg(test)]
mod tests {
    use super::*;
    fn run(s: &str, mode: u32) -> Reply {
        parse(&s.chars().map(u32::from).collect::<Vec<_>>(), mode)
    }
    #[test]
    fn quadrant_and_classification() {
        assert_eq!(run("N30E", 0).value, 60.0);
        assert_eq!(run("S30W", 0).value, 240.0);
        assert_eq!(run("ſ30E", 0).value, 300.0);
        assert_eq!(run("NbadE", 0).code, ANGLE);
        assert_eq!(run("N30EE", 0).code, BEARING);
        assert_eq!(run("N91E", 0).code, QUADRANT);
        assert_eq!(run("1<N30E", 1).distance, 1.0);
    }
    #[test]
    fn abi() {
        assert_eq!(std::mem::size_of::<Reply>(), 32);
        let mut r = Reply::default();
        unsafe {
            assert_eq!(
                narwhal_pure_core_bearing_v2(std::ptr::null(), 1, 0, 151, &mut r, 32),
                2
            );
            assert_eq!(
                narwhal_pure_core_bearing_v2(std::ptr::null(), 4097, 0, 151, &mut r, 32),
                0
            );
            assert_eq!(r.code, BOUND);
            assert_eq!(
                narwhal_pure_core_bearing_v2(std::ptr::null(), 0, 2, 151, &mut r, 32),
                2
            );
            assert_eq!(r.code, 0);
        }
    }
}
