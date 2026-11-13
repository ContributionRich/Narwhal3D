//! BEFORE data captured from the retired Python 3.13 oracle, never the native core.
//! Calls the public checked-pointer unit_ops ABI, not a second Rust grammar.
use narwhal_pure_core::unit_ops::{
    narwhal_pure_core_unit_ops_angle as angle, narwhal_pure_core_unit_ops_parse as parse,
    narwhal_pure_core_unit_ops_scale as scale,
};
use std::collections::BTreeMap;

fn unhex(s: &str) -> Vec<u8> {
    if s == "-" {
        return Vec::new();
    }
    assert_eq!(s.len() % 2, 0);
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}
fn float(s: &str) -> f64 {
    f64::from_bits(u64::from_str_radix(s, 16).unwrap())
}
fn check_float(actual: f64, expected: &str) {
    if expected == "nan" {
        assert!(actual.is_nan());
    } else {
        assert_eq!(actual.to_bits(), float(expected).to_bits());
    }
}
fn parsed(input: &[u8], expected: (i32, u32, u32), raw: &[u8]) {
    let (mut id, mut mask, mut len) = (99, 99, usize::MAX);
    let mut output = vec![0x55; input.len().max(1)];
    let rc = unsafe {
        parse(
            input.as_ptr(),
            input.len(),
            &mut id,
            &mut mask,
            output.as_mut_ptr(),
            output.len(),
            &mut len,
        )
    };
    assert_eq!((rc, id, mask), expected, "input {input:?}");
    assert_eq!(len, raw.len());
    assert_eq!(&output[..len], raw);
    assert!(output[len..].iter().all(|&v| v == 0x55));
}
fn parse_rows(data: &str, count: usize) {
    assert_eq!(data.lines().count(), count);
    for line in data.lines() {
        let fields: Vec<_> = line.split('\t').collect();
        assert_eq!(fields.len(), 6);
        // Last field retains the exact Python result/error repr for host audits.
        assert!(!unhex(fields[5]).is_empty());
        parsed(
            &unhex(fields[0]),
            (
                fields[1].parse().unwrap(),
                fields[2].parse().unwrap(),
                fields[3].parse().unwrap(),
            ),
            &unhex(fields[4]),
        );
    }
}
#[test]
fn seeded_grammar_and_all_whitespace_records() {
    parse_rows(include_str!("fixtures/unit_parse.txt"), 4437); // 4000 seeded + 437 named/host inputs
    parse_rows(include_str!("fixtures/unit_whitespace.txt"), 58);
}
#[test]
fn unicode_151_exhaustive() {
    let lines: Vec<_> = include_str!("fixtures/unit_unicode.txt").lines().collect();
    assert_eq!(lines.len(), 5);
    assert_eq!(lines[0], "15.1.0");
    let spaces: Vec<u32> = lines[1]
        .split_whitespace()
        .map(|v| v.parse().unwrap())
        .collect();
    assert_eq!(spaces.len(), 29);
    assert_eq!(lines[2], "8490 107"); // only non-ASCII lowercase-to-ASCII: Kelvin -> k
    assert_eq!(lines[3], "304 105 775"); // only multi-codepoint lowercase: dotted I
    let mut count = 0;
    for cp in 128..0x110000u32 {
        let mut input = Vec::with_capacity(5);
        if let Some(ch) = char::from_u32(cp) {
            let mut buf = [0; 4];
            input.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
        } else {
            // Python surrogatepass inputs remain covered, even though Rust char excludes them.
            input.extend_from_slice(&[
                (0xe0 | (cp >> 12)) as u8,
                (0x80 | ((cp >> 6) & 63)) as u8,
                (0x80 | (cp & 63)) as u8,
            ]);
        }
        input.push(b'm');
        let (status, id, raw) = if spaces.contains(&cp) {
            (0, 6, &b"m"[..])
        } else if cp == 8490 {
            (0, 9, input.as_slice())
        } else {
            (2, 0, input.as_slice())
        };
        parsed(&input, (status, id, 0), raw);
        count += 1;
    }
    assert_eq!(count, 1113984);
    assert_eq!(count.to_string(), lines[4]);
}
#[test]
fn arithmetic_bits_fixed_custom_angles_zero_and_nonfinite() {
    let mut counts = BTreeMap::new();
    let mut zeros = 0;
    for line in include_str!("fixtures/unit_scale.txt").lines() {
        let f: Vec<_> = line.split_whitespace().collect();
        assert_eq!(f.len(), 6);
        let mut out = f64::from_bits(0x7ff8000000000016);
        let rc = unsafe { scale(float(f[0]), float(f[1]), float(f[2]), &mut out) };
        assert_eq!(rc, f[3].parse::<i32>().unwrap(), "{line}");
        if f[4] == "untouched" {
            assert_eq!(out.to_bits(), 0x7ff8000000000016);
            zeros += 1;
        } else {
            check_float(out, f[4]);
        }
        *counts.entry(f[5]).or_insert(0usize) += 1;
    }
    assert!(zeros > 2); // random/custom zero denominators, not just the two explicit signs
    assert_eq!(
        counts,
        BTreeMap::from([
            ("seeded", 6108),
            ("overflow", 1),
            ("zero", 2),
            ("fixed", 5184),
            ("factor", 576),
            ("custom", 3652),
            ("live_custom", 71),
            ("angles", 125),
            ("live_angles", 72)
        ])
    );
    assert_eq!(counts["fixed"], 576 * 9);
}
#[test]
fn angle_factor_status_and_exact_bits() {
    let rows = include_str!("fixtures/unit_angle.txt");
    assert_eq!(rows.lines().count(), 10);
    for line in rows.lines() {
        let f: Vec<_> = line.split_whitespace().collect();
        let input = unhex(f[0]);
        let mut out = f64::from_bits(0x7ff8000000000016);
        let rc = unsafe { angle(input.as_ptr(), input.len(), &mut out) };
        assert_eq!(rc, f[1].parse::<i32>().unwrap());
        if f[2] == "untouched" {
            assert_eq!(out.to_bits(), 0x7ff8000000000016);
        } else {
            check_float(out, f[2]);
        }
    }
}
#[test]
fn bound_and_bad_pointer_status_leave_outputs_untouched() {
    let input = vec![b'x'; 4097];
    let mut raw = vec![0x55; 16384];
    let (mut id, mut mask, mut len) = (99, 99, 99);
    for (bytes, cap, expected) in [
        (input.as_slice(), 16384, 4),
        (&b"mm"[..], 1, 5),
        (&b"\xff"[..], 16384, 5),
    ] {
        let rc = unsafe {
            parse(
                bytes.as_ptr(),
                bytes.len(),
                &mut id,
                &mut mask,
                raw.as_mut_ptr(),
                cap,
                &mut len,
            )
        };
        assert_eq!(rc, expected);
        assert_eq!((id, mask, len), (99, 99, 99));
        assert!(raw.iter().all(|&v| v == 0x55));
    }
    unsafe {
        assert_eq!(scale(1., 1., 1., std::ptr::null_mut()), 5);
        assert_eq!(angle(b"degrees".as_ptr(), 7, std::ptr::null_mut()), 5);
    }
}

#[test]
fn all_numeric_angle_pairs_preserve_error_status_and_bits() {
    let data = include_str!("fixtures/unit_angle_grid.txt");
    assert_eq!(data.lines().count(), 500);
    let (mut success, mut invalid) = (0, 0);
    for line in data.lines() {
        let f: Vec<_> = line.split_whitespace().collect();
        let (a, b) = (unhex(f[0]), unhex(f[1]));
        let (mut sf, mut st) = (
            f64::from_bits(0x7ff8000000000016),
            f64::from_bits(0x7ff8000000000016),
        );
        let mut rc = unsafe { angle(a.as_ptr(), a.len(), &mut sf) };
        if rc == 0 {
            rc = unsafe { angle(b.as_ptr(), b.len(), &mut st) };
        }
        let mut out = f64::from_bits(0x7ff8000000000016);
        if rc == 0 {
            rc = unsafe { scale(float(f[2]), sf, st, &mut out) };
        }
        assert_eq!(rc, f[3].parse::<i32>().unwrap(), "{line}");
        if rc == 0 {
            check_float(out, f[4]);
            success += 1;
        } else {
            assert_eq!(out.to_bits(), 0x7ff8000000000016);
            invalid += 1;
        }
    }
    assert_eq!((success, invalid), (125, 375));
}
#[test]
fn static_before_fixture_counts_and_fingerprints() {
    fn fnv(bytes: &[u8]) -> u64 {
        bytes.iter().fold(0xcbf29ce484222325u64, |h, b| {
            (h ^ u64::from(*b)).wrapping_mul(0x100000001b3)
        })
    }
    assert_eq!(
        fnv(include_bytes!("fixtures/unit_parse.txt")),
        0x869d003cc3aac6b4,
        "unit_parse.txt"
    );
    assert_eq!(
        fnv(include_bytes!("fixtures/unit_unicode.txt")),
        0x8b482ac9cb162c82,
        "unit_unicode.txt"
    );
    assert_eq!(
        fnv(include_bytes!("fixtures/unit_whitespace.txt")),
        0x879c47992d15bbdc,
        "unit_whitespace.txt"
    );
    assert_eq!(
        fnv(include_bytes!("fixtures/unit_scale.txt")),
        0x546ea0f22dcabfe7,
        "unit_scale.txt"
    );
    assert_eq!(
        fnv(include_bytes!("fixtures/unit_angle.txt")),
        0xd48925aa5367ec93,
        "unit_angle.txt"
    );
    assert_eq!(
        fnv(include_bytes!("fixtures/unit_angle_grid.txt")),
        0x32e0d152a6914be4,
        "unit_angle_grid.txt"
    );
    assert_eq!(
        fnv(include_bytes!("fixtures/unit_table.txt")),
        0x38051c7163a066e5,
        "unit_table.txt"
    );
}
