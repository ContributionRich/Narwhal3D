//! Original coordinate grammar answers. Dependencies and passive stage-mapped
//! metadata provenance are explicit in coords_provenance.txt. No second parser.
use narwhal_pure_core::coords_owned::{
    narwhal_pure_core_coords_owned_abi1, narwhal_pure_core_coords_owned_v1 as owned,
    narwhal_pure_core_coords_split_v1 as split, Reply,
};
use std::{
    collections::BTreeMap,
    mem::{offset_of, size_of},
    ptr::null_mut,
};
fn cps(s: &str) -> Vec<u32> {
    s.split(',')
        .filter(|s| !s.is_empty())
        .map(|s| u32::from_str_radix(s, 16).unwrap())
        .collect()
}
fn decimal(s: &str) -> u64 {
    s.parse().unwrap()
}
fn bits(s: &str) -> u64 {
    u64::from_str_radix(s, 16).unwrap()
}
fn fields(r: &Reply) -> Vec<u64> {
    vec![
        r.code as u64,
        r.flags as u64,
        r.values[0].to_bits(),
        r.values[1].to_bits(),
        r.values[2].to_bits(),
        r.start as u64,
        r.end as u64,
        r.calc.code as u64,
        r.calc.aux as u64,
        r.calc.value.to_bits(),
        r.calc.start as u64,
        r.calc.end as u64,
        r.calc.reserved,
        r.bearing.code as u64,
        r.bearing.reserved as u64,
        r.bearing.value.to_bits(),
        r.bearing.distance.to_bits(),
        r.bearing.start as u64,
        r.bearing.end as u64,
    ]
}
fn expected(r: &[&str]) -> Vec<u64> {
    let mut out = vec![decimal(r[8]), decimal(r[9])];
    out.extend(r[10].split(',').map(bits));
    out.extend([decimal(r[11]), decimal(r[12])]);
    let c: Vec<_> = r[13].split(',').collect();
    out.extend([
        decimal(c[0]),
        decimal(c[1]),
        bits(c[2]),
        decimal(c[3]),
        decimal(c[4]),
        decimal(c[5]),
    ]);
    let b: Vec<_> = r[14].split(',').collect();
    out.extend([
        decimal(b[0]),
        decimal(b[1]),
        bits(b[2]),
        bits(b[3]),
        decimal(b[4]),
        decimal(b[5]),
    ]);
    out
}
fn check_row(row: &str) -> (String, u32, u32, bool) {
    let r: Vec<_> = row.split('|').collect();
    assert_eq!(r.len(), 15);
    let source = cps(r[3]);
    let model = cps(r[6]);
    let mut reply = Reply::default();
    // Poison all 104 bytes so every reserved/nested payload byte must be reset.
    unsafe {
        std::ptr::write_bytes(
            &mut reply as *mut Reply as *mut u8,
            0xa5,
            size_of::<Reply>(),
        );
    }
    assert_eq!(
        unsafe {
            owned(
                source.as_ptr(),
                source.len(),
                model.as_ptr(),
                model.len(),
                r[5].parse().unwrap(),
                r[2].parse().unwrap(),
                151,
                &mut reply,
                size_of::<Reply>(),
            )
        },
        0,
        "case {}",
        r[0]
    );
    assert_eq!(fields(&reply), expected(&r), "case {} group {}", r[0], r[1]);
    assert!(reply.values.iter().all(|v| v.is_finite()));
    assert_eq!(r[7].is_empty(), reply.code == 0);
    if reply.code != 0 {
        assert_eq!(reply.flags, 0);
    }
    (
        r[1].to_owned(),
        r[2].parse().unwrap(),
        reply.code,
        reply
            .values
            .iter()
            .any(|v| v.to_bits() == (-0.0f64).to_bits()),
    )
}
#[test]
fn original_parse_scalar_angle_full_104_byte_metadata_and_bits() {
    assert_eq!(narwhal_pure_core::narwhal_pure_core_abi_version(), 1);
    assert_eq!(narwhal_pure_core_coords_owned_abi1(), 1);
    assert_eq!(size_of::<Reply>(), 104);
    assert_eq!(offset_of!(Reply, calc), 40);
    assert_eq!(offset_of!(Reply, bearing), 72);
    let mut groups = BTreeMap::new();
    let mut codes = BTreeMap::new();
    let mut negative_zero = 0;
    let mut modes = [0; 4];
    let mut differential = 0;
    let mut projection = 0;
    for row in include_str!("fixtures/coords_golden.txt").lines() {
        let (group, mode, code, zero) = check_row(row);
        modes[mode as usize] += 1;
        if group.starts_with("test_") {
            differential += 1;
        }
        if group == "projection_witness" {
            projection += 1;
        }
        *groups.entry(group).or_insert(0usize) += 1;
        *codes.entry(code).or_insert(0usize) += 1;
        negative_zero += usize::from(zero);
    }
    assert_eq!(differential, 20293);
    assert_eq!(groups.values().sum::<usize>(), 20402);
    assert_eq!(modes, [19621, 40, 732, 9]);
    assert_eq!(codes[&0], 8099);
    assert_eq!(codes.values().sum::<usize>() - codes[&0], 12303);
    assert_eq!(groups["test_contract_prefix_polar_error_precedence"], 796);
    assert_eq!(groups["test_scalar_angle_grammars"], 120);
    assert_eq!(groups["test_seeded_differential"], 18014);
    assert_eq!(
        groups["test_bounds_nonfinite_types_unit_control_and_calls"],
        1363
    );
    assert_eq!(projection, 4);
    assert!(negative_zero > 0);
    for code in [
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 20, 21, 22,
    ] {
        assert!(codes.contains_key(&code), "missing code {code}");
    }
}
#[test]
fn all_1005_times_nine_original_splits_query_capacity_and_exact_spans() {
    let fixture = include_str!("fixtures/coords_split.txt");
    assert_eq!(fixture.lines().count(), 1005 * 9);
    let mut separators = [0; 9];
    for (index, row) in fixture.lines().enumerate() {
        let r: Vec<_> = row.split('|').collect();
        assert_eq!(r.len(), 6);
        assert_eq!(decimal(r[0]) as usize, index);
        separators[index % 9] += 1;
        let text = cps(r[1]);
        let kind = r[3].parse().unwrap();
        let sep = r[4].parse().unwrap();
        let want: Vec<u32> = r[5].split(',').map(|s| s.parse().unwrap()).collect();
        assert_eq!(want.len() % 2, 0);
        let count = (want.len() / 2) as u32;
        let mut n = u32::MAX;
        assert_eq!(
            unsafe { split(text.as_ptr(), text.len(), sep, kind, null_mut(), 0, &mut n) },
            0,
            "split {index}"
        );
        assert_eq!(n, count);
        let mut spans = vec![123; want.len() + 2];
        n = 123;
        assert_ne!(
            unsafe {
                split(
                    text.as_ptr(),
                    text.len(),
                    sep,
                    kind,
                    spans.as_mut_ptr(),
                    want.len() - 1,
                    &mut n,
                )
            },
            0
        );
        assert_eq!(n, 123);
        assert!(spans.iter().all(|v| *v == 123));
        assert_eq!(
            unsafe {
                split(
                    text.as_ptr(),
                    text.len(),
                    sep,
                    kind,
                    spans.as_mut_ptr(),
                    want.len(),
                    &mut n,
                )
            },
            0
        );
        assert_eq!(n, count);
        assert_eq!(&spans[..want.len()], &want, "split {index}");
        assert_eq!(&spans[want.len()..], &[123, 123]);
        assert_eq!(want[0], 0);
        assert_eq!(*want.last().unwrap(), text.len() as u32);
    }
    assert_eq!(separators, [1005; 9]);
}
#[test]
fn companion_presentation_rows_are_exact_original_records() {
    let original: std::collections::BTreeSet<_> = include_str!("fixtures/coords_golden.txt")
        .lines()
        .chain(include_str!("fixtures/coords_policy.txt").lines())
        .collect();
    let presentation = include_str!("fixtures/coords_presentation.txt");
    assert_eq!(presentation.lines().count(), 2521);
    for row in presentation.lines() {
        assert!(original.contains(row));
    }
    assert_eq!(
        include_str!("fixtures/coords_host.txt").lines().count(),
        983
    );
}

#[test]
fn original_explicit_safety_policy_and_retained_nonfinite_observations() {
    let policy = include_str!("fixtures/coords_policy.txt");
    assert_eq!(policy.lines().count(), 24);
    for row in policy.lines() {
        check_row(row);
    }
    let legacy = include_str!("fixtures/coords_legacy_nonfinite.txt");
    assert_eq!(legacy.lines().count(), 20);
    for row in legacy.lines() {
        let r: Vec<_> = row.split('|').collect();
        assert_eq!(r[8], "0");
        assert!(r[7].is_empty());
        assert!(r[10]
            .split(',')
            .any(|v| !f64::from_bits(bits(v)).is_finite()));
    }
}

#[test]
fn original_numeric_examples_keep_their_tolerance_contract() {
    let examples = include_str!("fixtures/coords_examples.txt");
    assert_eq!(examples.lines().count(), 13);
    let model: Vec<u32> = "Millimeters".chars().map(u32::from).collect();
    for row in examples.lines() {
        let r: Vec<_> = row.split('|').collect();
        let text = cps(r[0]);
        let want: Vec<_> = r[1].split(',').map(|s| f64::from_bits(bits(s))).collect();
        let mut out = Reply::default();
        assert_eq!(
            unsafe {
                owned(
                    text.as_ptr(),
                    text.len(),
                    model.as_ptr(),
                    model.len(),
                    1,
                    0,
                    151,
                    &mut out,
                    size_of::<Reply>(),
                )
            },
            0
        );
        assert_eq!(out.code, 0);
        assert_eq!(
            out.flags,
            decimal(r[2]) as u32 | (decimal(r[3]) as u32) << 1
        );
        for (a, b) in out.values.iter().zip(want) {
            assert!((a - b).abs() <= 1e-9 * b.abs().max(1.0));
        }
    }
}

#[test]
fn original_transport_errors_preserve_all_outputs() {
    let source: Vec<_> = "1,2".chars().map(u32::from).collect();
    let mut reply = Reply {
        code: 123,
        ..Reply::default()
    };
    let n = std::ptr::null();
    for (input, len, model, ml, kind, mode, unicode) in [
        (n, 1, source.as_ptr(), 3, 1, 0, 151),
        (source.as_ptr(), 3, n, 1, 1, 0, 151),
        (source.as_ptr(), 3, n, 0, 2, 0, 151),
        (source.as_ptr(), 3, n, 0, 1, 4, 151),
        (source.as_ptr(), 3, n, 0, 1, 0, 160),
    ] {
        let before = fields(&reply);
        assert_ne!(
            unsafe {
                owned(
                    input,
                    len,
                    model,
                    ml,
                    kind,
                    mode,
                    unicode,
                    &mut reply,
                    size_of::<Reply>(),
                )
            },
            0
        );
        assert_eq!(fields(&reply), before);
    }
    assert_ne!(
        unsafe {
            owned(
                source.as_ptr(),
                3,
                n,
                0,
                1,
                0,
                151,
                &mut reply,
                size_of::<Reply>() - 1,
            )
        },
        0
    );
    assert_eq!(reply.code, 123);
    assert_eq!(
        unsafe { owned(n, 4097, n, 0, 1, 0, 151, &mut reply, size_of::<Reply>()) },
        0
    );
    assert_eq!(reply.code, 18);
    let misaligned = unsafe { source.as_ptr().cast::<u8>().add(1).cast::<u32>() };
    let before = fields(&reply);
    assert_ne!(
        unsafe {
            owned(
                misaligned,
                1,
                n,
                0,
                1,
                0,
                151,
                &mut reply,
                size_of::<Reply>(),
            )
        },
        0
    );
    assert_eq!(fields(&reply), before);
    assert_ne!(
        unsafe {
            owned(
                source.as_ptr(),
                3,
                n,
                0,
                1,
                0,
                151,
                null_mut(),
                size_of::<Reply>(),
            )
        },
        0
    );
    let mut count = 123;
    let mut spans = [9; 4];
    assert_ne!(
        unsafe { split(source.as_ptr(), 3, 44, 1, spans.as_mut_ptr(), 2, &mut count) },
        0
    );
    assert_eq!((spans, count), ([9; 4], 123));
    assert_ne!(
        unsafe { split(n, 1, 44, 1, spans.as_mut_ptr(), 4, &mut count) },
        0
    );
    assert_eq!((spans, count), ([9; 4], 123));
    assert_eq!(
        unsafe { split(source.as_ptr(), 3, 44, 1, spans.as_mut_ptr(), 4, &mut count) },
        0
    );
    assert_eq!((spans, count), ([0, 1, 2, 3], 2));
}
