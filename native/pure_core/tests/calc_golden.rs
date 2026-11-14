//! Original independent Python calculator answers, not native-generated goldens.
//! Metadata is mapped from original token positions and failure-stage frames.
use narwhal_pure_core::calc_owned::{
    narwhal_pure_core_calc_abi2, narwhal_pure_core_calc_v2, Reply,
};
use std::{collections::BTreeMap, mem::size_of};

fn cps(s: &str) -> Vec<u32> {
    s.split(',')
        .filter(|s| !s.is_empty())
        .map(|s| u32::from_str_radix(s, 16).unwrap())
        .collect()
}
fn bits(s: &str) -> u64 {
    u64::from_str_radix(s, 16).unwrap()
}
fn invoke(text: &[u32], model: &[u32], kind: u32) -> Reply {
    let mut out = Reply {
        code: u32::MAX,
        aux: u32::MAX,
        value: f64::NAN,
        start: u32::MAX,
        end: u32::MAX,
        reserved: u64::MAX,
    };
    assert_eq!(
        unsafe {
            narwhal_pure_core_calc_v2(
                text.as_ptr(),
                text.len(),
                model.as_ptr(),
                model.len(),
                kind,
                151,
                &mut out,
                size_of::<Reply>(),
            )
        },
        0
    );
    out
}

#[test]
fn all_original_22107_differentials_and_host_anchors() {
    assert_eq!(narwhal_pure_core::narwhal_pure_core_abi_version(), 1);
    assert_eq!(narwhal_pure_core_calc_abi2(), 2);
    assert_eq!(size_of::<Reply>(), 32);
    let mut groups = BTreeMap::new();
    let mut codes = BTreeMap::new();
    let mut differential = 0;
    let mut negative_zeros = 0;
    let mut models = [0; 2];
    for line in include_str!("fixtures/calc_golden.txt").lines() {
        let r: Vec<_> = line.split('|').collect();
        assert_eq!(r.len(), 13);
        let id = r[0];
        *groups.entry(r[1]).or_insert(0usize) += 1;
        if r[1].starts_with("test_") {
            differential += 1;
        }
        let (text, model) = (cps(r[2]), cps(r[5]));
        let kind: u32 = r[4].parse().unwrap();
        models[kind as usize] += 1;
        if kind == 0 {
            assert!(model.is_empty());
        }
        let expected_code: u32 = r[8].parse().unwrap();
        *codes.entry(expected_code).or_insert(0usize) += 1;
        let out = invoke(&text, &model, kind);
        assert_eq!(out.code, expected_code, "case {id}");
        assert_eq!(out.aux, r[9].parse::<u32>().unwrap(), "case {id}");
        assert_eq!(out.start, r[10].parse::<u32>().unwrap(), "case {id}");
        assert_eq!(out.end, r[11].parse::<u32>().unwrap(), "case {id}");
        assert_eq!(out.value.to_bits(), bits(r[12]), "case {id}");
        assert_eq!(out.reserved, 0, "case {id}");
        assert!(out.start <= out.end && out.end as usize <= text.len());
        if expected_code == 0 {
            assert!(r[7].is_empty());
            assert!(out.value.is_finite());
            assert_eq!(out.value.to_bits(), bits(r[6]), "answer case {id}");
            if bits(r[6]) == (-0.0f64).to_bits() {
                negative_zeros += 1;
            }
        } else {
            assert_eq!(r[6], "-");
            assert!(
                !cps(r[7]).is_empty(),
                "full original error string case {id}"
            );
        }
    }
    assert_eq!(differential, 22107);
    assert_eq!(groups.values().sum::<usize>(), 22197);
    for (name, count) in [
        ("test_grammar_units_and_error_precedence", 880),
        ("test_unicode_predicates_and_offsets", 5379),
        ("test_math_specials_and_exact_errors", 846),
        ("test_seeded_libm_exact_parity", 3000),
        ("test_seeded_differential", 12000),
        ("test_explicit_bounds_and_native_call_count", 2),
    ] {
        assert_eq!(groups[name], count, "{name}");
    }
    assert!(negative_zeros > 0);
    assert!(models[0] > 0 && models[1] > 0);
    for code in 0..=27 {
        assert!(codes.contains_key(&code), "missing reply code {code}");
    }
}

#[test]
fn companion_fixture_counts_and_original_limits() {
    let originals: std::collections::BTreeSet<_> =
        include_str!("fixtures/calc_golden.txt").lines().collect();
    for (fixture, count) in [
        (include_str!("fixtures/calc_boundary.txt"), 203),
        (include_str!("fixtures/calc_presentation.txt"), 4575),
    ] {
        assert_eq!(fixture.lines().count(), count);
        for row in fixture.lines() {
            assert!(originals.contains(row));
        }
    }
    assert_eq!(include_str!("fixtures/calc_host.txt").lines().count(), 5);
    let limits = include_str!("fixtures/calc_limits.txt");
    assert_eq!(limits.lines().count(), 9);
    for row in limits.lines() {
        let r: Vec<_> = row.split('|').collect();
        assert_eq!(r.len(), 13);
        let out = invoke(&cps(r[2]), &cps(r[5]), r[4].parse().unwrap());
        assert_eq!(out.code, r[8].parse::<u32>().unwrap());
        assert_eq!(out.aux, r[9].parse::<u32>().unwrap());
        assert_eq!(out.start, r[10].parse::<u32>().unwrap());
        assert_eq!(out.end, r[11].parse::<u32>().unwrap());
        assert_eq!(out.value.to_bits(), bits(r[12]));
        assert_eq!(out.reserved, 0);
        assert_eq!(r[7].is_empty(), out.code == 0);
    }
}

#[test]
fn original_limits_and_transport_protocol() {
    let model = cps("4d,69,6c,6c,69,6d,65,74,65,72,73");
    for (text, code, start, end, value) in [
        (
            format!("{}1{}", "(".repeat(64), ")".repeat(64)),
            0,
            0,
            0,
            1.0f64,
        ),
        (
            format!("{}1{}", "(".repeat(65), ")".repeat(65)),
            29,
            64,
            65,
            0.0,
        ),
        (
            format!("{}0{}", "sin(".repeat(65), ")".repeat(65)),
            29,
            256,
            259,
            0.0,
        ),
        (format!("{}1", "-".repeat(4095)), 0, 0, 0, -1.0),
        ("1".repeat(4097), 28, 0, 0, 0.0),
    ] {
        let text: Vec<_> = text.chars().map(u32::from).collect();
        let out = invoke(&text, &model, 1);
        assert_eq!(
            (out.code, out.aux, out.start, out.end, out.reserved),
            (code, 0, start, end, 0)
        );
        assert_eq!(out.value.to_bits(), value.to_bits());
    }
    let out = invoke(&[49], &vec![109; 4097], 1);
    assert_eq!(
        (out.code, out.aux, out.start, out.end, out.reserved),
        (28, 0, 0, 0, 0)
    );
    assert_eq!(out.value.to_bits(), 0);
    for (kind, unicode, length, size, expected) in [
        (2, 151, 1, size_of::<Reply>(), 2),
        (1, 160, 1, size_of::<Reply>(), 2),
        (1, 151, 1, size_of::<Reply>() - 1, 1),
        (1, 151, 1, size_of::<Reply>(), 1),
    ] {
        let mut out = Reply::default();
        assert_eq!(
            unsafe {
                narwhal_pure_core_calc_v2(
                    std::ptr::null(),
                    length,
                    model.as_ptr(),
                    model.len(),
                    kind,
                    unicode,
                    &mut out,
                    size,
                )
            },
            expected
        );
    }
    let text: Vec<_> = format!("sin({})", vec!["1"; 1000].join(","))
        .chars()
        .map(u32::from)
        .collect();
    let out = invoke(&text, &model, 1);
    assert_eq!(
        (out.code, out.aux, out.start, out.end),
        (14, (1 << 16) | 1000, 0, 3)
    );
}
