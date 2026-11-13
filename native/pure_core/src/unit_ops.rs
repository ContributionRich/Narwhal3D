//! Unit grammar/arithmetic port. Fixed names/scales come only from numparse.
//! OURS: reject over 4096 Unicode codepoints before allocating normalized buffers.
use crate::{numparse::UNIT_SYSTEM_MM, python_text::is_space};
pub const OK: i32 = 0;
pub const EMPTY: i32 = 1;
pub const INVALID: i32 = 2;
pub const AMBIGUOUS: i32 = 3;
pub const BOUND: i32 = 4;
pub const ABI: i32 = 5;
pub const ZERO: i32 = 6;
const LIMIT: usize = 4096;
fn name(i: usize) -> &'static str {
    if i == 24 {
        "CustomUnits"
    } else {
        UNIT_SYSTEM_MM[i].0
    }
}
// MEASURED Unicode 15.1: Kelvin is the only non-ASCII lowercase-to-ASCII.
// Every other non-ASCII character prevents a successful ASCII-name match.
fn lower(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c == '\u{212a}' {
                'k'
            } else {
                c.to_ascii_lowercase()
            }
        })
        .collect()
}
fn parse(raw: &str) -> (i32, u32, u32) {
    let key = lower(raw);
    if key.is_empty() {
        return (EMPTY, 0, 0);
    }
    let alias = match key.as_str() {
        "mic" | "micron" | "microns" => Some(2),
        "mm" | "millimeter" => Some(3),
        "c" | "cm" | "centimeter" => Some(4),
        "m" | "meter" => Some(6),
        "km" | "kilometer" => Some(9),
        "microinch" | "microinches" => Some(12),
        "mil" | "mils" => Some(13),
        "\"" | "in" | "inch" | "inches" => Some(14),
        "'" | "ft" | "foot" | "feet" => Some(15),
        "mi" | "mile" => Some(17),
        _ => None,
    };
    if let Some(i) = alias {
        return (OK, i, 0);
    }
    let key = key.replace(' ', "");
    for i in 0..25 {
        let low = name(i).to_ascii_lowercase();
        if key == low || key == low.trim_end_matches('s') {
            return (OK, i as u32, 0);
        }
    }
    let mut mask = 0u32;
    for i in 0..25 {
        if name(i).to_ascii_lowercase().starts_with(&key) {
            mask |= 1 << i;
        }
    }
    match mask.count_ones() {
        0 => (INVALID, 0, 0),
        1 => (OK, mask.trailing_zeros(), 0),
        _ => (AMBIGUOUS, 0, mask),
    }
}
// Metadata checks never dereference a pointer. Provenance/readability remains
// the caller's obligation, as with the crate's checked_slice helpers.
fn span<T>(ptr: *const T, len: usize) -> Option<(usize, usize)> {
    if ptr.is_null() || !crate::raw_span_is_valid(ptr, len) {
        return None;
    }
    let start = ptr as usize;
    Some((
        start,
        start.checked_add(len.checked_mul(std::mem::size_of::<T>())?)?,
    ))
}
fn disjoint(ranges: &[(usize, usize)]) -> bool {
    ranges.iter().enumerate().all(|(i, &(a, b))| {
        ranges[i + 1..]
            .iter()
            .all(|&(c, d)| a == b || c == d || b <= c || d <= a)
    })
}
unsafe fn input(text: *const u8, len: usize) -> Result<String, i32> {
    if len > LIMIT * 4 {
        return Err(BOUND);
    }
    if text.is_null() {
        return Err(ABI);
    }
    let bytes = crate::checked_slice(text, len).ok_or(ABI)?;
    if bytes.iter().filter(|b| **b & 0xc0 != 0x80).count() > LIMIT {
        return Err(BOUND);
    }
    // Python surrogatepass: substitute one nonmatching 3-byte scalar per
    // surrogate, preserving original byte offsets for the raw error payload.
    let mut valid = bytes.to_vec();
    let mut i = 0;
    while i + 2 < valid.len() {
        if valid[i] == 0xed
            && (0xa0..=0xbf).contains(&valid[i + 1])
            && (0x80..=0xbf).contains(&valid[i + 2])
        {
            valid[i..i + 3].copy_from_slice(&[0xee, 0x80, 0x80]);
            i += 3;
        } else {
            i += 1;
        }
    }
    let s = String::from_utf8(valid).map_err(|_| ABI)?;
    if s.chars().count() > LIMIT {
        return Err(BOUND);
    }
    Ok(s)
}
/// Parse a bounded UTF-8 string; raw receives the stripped ORIGINAL bytes.
/// # Safety
/// Input must span len readable bytes; output pointers must be writable and
/// nonoverlapping, raw must span cap bytes. Outputs untouched on ABI/bound errors.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_unit_ops_parse(
    text: *const u8,
    len: usize,
    id: *mut u32,
    mask: *mut u32,
    raw: *mut u8,
    cap: usize,
    raw_len: *mut usize,
) -> i32 {
    if len > LIMIT * 4 {
        return BOUND;
    }
    if cap > LIMIT * 4 {
        return ABI;
    }
    let ranges = match (
        span(text, len),
        span(id, 1),
        span(mask, 1),
        span(raw, cap),
        span(raw_len, 1),
    ) {
        (Some(a), Some(b), Some(c), Some(d), Some(e)) => [a, b, c, d, e],
        _ => return ABI,
    };
    if !disjoint(&ranges) {
        return ABI;
    }
    let s = match input(text, len) {
        Ok(s) => s,
        Err(e) => return e,
    };
    if cap < s.trim_matches(is_space).len() {
        return ABI;
    }
    let trimmed = s.trim_matches(is_space);
    let offset = trimmed.as_ptr() as usize - s.as_ptr() as usize;
    let (status, i, m) = parse(trimmed);
    std::ptr::copy_nonoverlapping(text.add(offset), raw, trimmed.len());
    *raw_len = trimmed.len();
    *id = i;
    *mask = m;
    status
}
/// Resolve angle factor with the reference's exact constant evaluation order.
/// # Safety
/// text spans len readable bytes; out points to a writable f64.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_unit_ops_angle(
    text: *const u8,
    len: usize,
    out: *mut f64,
) -> i32 {
    if len > LIMIT * 4 {
        return BOUND;
    }
    let ranges = match (span(text, len), span(out, 1)) {
        (Some(a), Some(b)) => [a, b],
        _ => return ABI,
    };
    if !disjoint(&ranges) {
        return ABI;
    }
    let s = match input(text, len) {
        Ok(s) => s,
        Err(e) => return e,
    };
    *out = match lower(s.trim_matches(is_space)).as_str() {
        "degrees" => (1.0 / 180.0) * std::f64::consts::PI,
        "radians" => 1.0,
        "gradians" => (1.0 / 100.0) * std::f64::consts::FRAC_PI_2,
        _ => return INVALID,
    };
    OK
}
/// Preserve Python multiply-then-divide, including signed-zero rejection.
/// # Safety
/// out points to a writable f64.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_unit_ops_scale(
    value: f64,
    sf: f64,
    st: f64,
    out: *mut f64,
) -> i32 {
    if span(out, 1).is_none() {
        return ABI;
    }
    if st == 0.0 {
        return ZERO;
    }
    *out = (value * sf) / st;
    OK
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ptr;

    #[test]
    fn metadata_helpers() {
        let arena = [0u64; 4];
        let base = arena.as_ptr().cast::<u8>();
        assert!(span(base.cast::<u32>(), 1).is_some());
        assert!(span(unsafe { base.add(1) }.cast::<u32>(), 1).is_none());
        assert!(span(ptr::null::<u32>(), 1).is_none());
        assert!(span(base, usize::MAX).is_none());
        assert!(!disjoint(&[(8, 16), (15, 24)]));
        assert!(!disjoint(&[(8, 16), (8, 16)]));
        assert!(disjoint(&[(8, 16), (16, 24)]));
        assert!(disjoint(&[(8, 8), (8, 16)]));
    }

    #[test]
    fn cap_admission() {
        let mut raw = vec![0x55u8; LIMIT * 4 + 1];
        let (mut id, mut mask, mut n) = (99, 99, 99);
        unsafe {
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    b"mm".as_ptr(),
                    2,
                    &mut id,
                    &mut mask,
                    raw.as_mut_ptr(),
                    raw.len(),
                    &mut n
                ),
                ABI
            );
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    b"mm".as_ptr(),
                    2,
                    &mut id,
                    &mut mask,
                    raw.as_mut_ptr(),
                    0,
                    &mut n
                ),
                ABI
            );
        }
        assert_eq!((id, mask, n), (99, 99, 99));
        assert!(raw.iter().all(|&b| b == 0x55));
    }

    #[test]
    fn grammar_metadata_and_original_bytes() {
        for (text, status, expected_id, expected_mask, expected_raw) in [
            (" \u{1c}Km\u{a0}", OK, 9, 0, "Km"),
            (" ", EMPTY, 0, 0, ""),
            ("bad", INVALID, 0, 0, "bad"),
            ("p", AMBIGUOUS, 0, (1 << 18) | (1 << 19) | (1 << 23), "p"),
        ] {
            let (mut id, mut mask, mut n) = (99, 99, 99);
            let mut raw = [0x55; LIMIT * 4];
            let rc = unsafe {
                narwhal_pure_core_unit_ops_parse(
                    text.as_ptr(),
                    text.len(),
                    &mut id,
                    &mut mask,
                    raw.as_mut_ptr(),
                    raw.len(),
                    &mut n,
                )
            };
            assert_eq!(
                (rc, id, mask, n),
                (status, expected_id, expected_mask, expected_raw.len())
            );
            assert_eq!(&raw[..n], expected_raw.as_bytes());
            assert!(raw[n..].iter().all(|&b| b == 0x55));
        }
    }

    #[test]
    fn parse_metadata_rejects_before_dereference_and_leaves_outputs() {
        let mut arena = [0u64; 32];
        let base = arena.as_mut_ptr().cast::<u8>();
        let dangling = ptr::NonNull::<u8>::dangling().as_ptr();
        let (mut id, mut mask, mut n) = (99u32, 99u32, 99usize);
        let mut raw = [0x55u8; 16];
        // Bad metadata paired with unreadable input proves rejection occurs
        // before input reads. No fabricated pointer is dereferenced here.
        unsafe {
            for bad in [ptr::null_mut(), base.add(1).cast::<u32>()] {
                assert_eq!(
                    narwhal_pure_core_unit_ops_parse(
                        dangling,
                        2,
                        bad,
                        &mut mask,
                        raw.as_mut_ptr(),
                        raw.len(),
                        &mut n
                    ),
                    ABI
                );
            }
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    dangling,
                    usize::MAX,
                    &mut id,
                    &mut mask,
                    raw.as_mut_ptr(),
                    raw.len(),
                    &mut n
                ),
                BOUND
            );
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    dangling,
                    2,
                    &mut id,
                    &mut mask,
                    raw.as_mut_ptr(),
                    usize::MAX,
                    &mut n
                ),
                ABI
            );
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    ptr::null(),
                    0,
                    &mut id,
                    &mut mask,
                    raw.as_mut_ptr(),
                    raw.len(),
                    &mut n
                ),
                ABI
            );
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    dangling,
                    2,
                    &mut id,
                    ptr::null_mut(),
                    raw.as_mut_ptr(),
                    raw.len(),
                    &mut n
                ),
                ABI
            );
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    dangling,
                    2,
                    &mut id,
                    &mut mask,
                    ptr::null_mut(),
                    0,
                    &mut n
                ),
                ABI
            );
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    dangling,
                    2,
                    &mut id,
                    &mut mask,
                    raw.as_mut_ptr(),
                    raw.len(),
                    base.add(1).cast()
                ),
                ABI
            );
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    dangling,
                    2,
                    &mut id,
                    &mut mask,
                    raw.as_mut_ptr(),
                    raw.len(),
                    ptr::null_mut()
                ),
                ABI
            );
            // Pairwise overlap among every input/output range. The remaining
            // pointers refer to aligned, disjoint slots in the same allocation.
            for a in 0..5 {
                for b in a + 1..5 {
                    let mut pointers = [
                        base,
                        base.add(32),
                        base.add(64),
                        base.add(96),
                        base.add(128),
                    ];
                    pointers[b] = pointers[a];
                    assert_eq!(
                        narwhal_pure_core_unit_ops_parse(
                            pointers[0],
                            8,
                            pointers[1].cast(),
                            pointers[2].cast(),
                            pointers[3],
                            8,
                            pointers[4].cast()
                        ),
                        ABI,
                        "overlap {a} {b}"
                    );
                }
            }
            // Address-wrap metadata, still never read.
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    (usize::MAX - 1) as *const u8,
                    8,
                    &mut id,
                    &mut mask,
                    raw.as_mut_ptr(),
                    raw.len(),
                    &mut n
                ),
                ABI
            );
            for text in [b"mm".as_slice(), b"\xff".as_slice()] {
                assert_eq!(
                    narwhal_pure_core_unit_ops_parse(
                        text.as_ptr(),
                        text.len(),
                        &mut id,
                        &mut mask,
                        raw.as_mut_ptr(),
                        1,
                        &mut n
                    ),
                    ABI
                );
            }
        }
        assert_eq!((id, mask, n), (99, 99, 99));
        assert_eq!(raw, [0x55; 16]);
        assert_eq!(arena, [0; 32]);
    }

    #[test]
    fn scalar_outputs_alignment_overlap_and_untouched_errors() {
        let mut arena = [0u64; 4];
        let base = arena.as_mut_ptr().cast::<u8>();
        let mut out = 123.0;
        unsafe {
            for bad in [ptr::null_mut(), base.add(1).cast::<f64>()] {
                assert_eq!(narwhal_pure_core_unit_ops_scale(1., 2., 3., bad), ABI);
                assert_eq!(
                    narwhal_pure_core_unit_ops_angle(
                        ptr::NonNull::<u8>::dangling().as_ptr(),
                        2,
                        bad
                    ),
                    ABI
                );
            }
            assert_eq!(narwhal_pure_core_unit_ops_angle(base, 8, base.cast()), ABI);
            assert_eq!(
                narwhal_pure_core_unit_ops_angle(base.add(7), 2, base.cast()),
                ABI
            );
            assert_eq!(
                narwhal_pure_core_unit_ops_angle(
                    ptr::NonNull::<u8>::dangling().as_ptr(),
                    usize::MAX,
                    &mut out
                ),
                BOUND
            );
            assert_eq!(
                narwhal_pure_core_unit_ops_angle(b"bad".as_ptr(), 3, &mut out),
                INVALID
            );
            assert_eq!(out, 123.);
            for st in [0., -0.] {
                assert_eq!(narwhal_pure_core_unit_ops_scale(1., 2., st, &mut out), ZERO);
                assert_eq!(out, 123.);
            }
            assert_eq!(
                narwhal_pure_core_unit_ops_scale(1e308, 2., 2., &mut out),
                OK
            );
            assert_eq!(out, f64::INFINITY);
        }
        assert_eq!(arena, [0; 4]);
    }

    #[test]
    fn codepoint_bound_and_surrogatepass() {
        let bytes = vec![b'x'; LIMIT + 1];
        let (mut id, mut mask, mut n) = (99, 99, 99);
        let mut raw = [0x55; LIMIT * 4];
        unsafe {
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    bytes.as_ptr(),
                    bytes.len(),
                    &mut id,
                    &mut mask,
                    raw.as_mut_ptr(),
                    raw.len(),
                    &mut n
                ),
                BOUND
            );
            assert_eq!((id, mask, n), (99, 99, 99));
            assert!(raw.iter().all(|&b| b == 0x55));
            let text = b" \xed\xa0\x80 ";
            assert_eq!(
                narwhal_pure_core_unit_ops_parse(
                    text.as_ptr(),
                    text.len(),
                    &mut id,
                    &mut mask,
                    raw.as_mut_ptr(),
                    raw.len(),
                    &mut n
                ),
                INVALID
            );
            assert_eq!((id, mask, n), (0, 0, 3));
            assert_eq!(&raw[..n], &text[1..4]);
        }
    }
}
