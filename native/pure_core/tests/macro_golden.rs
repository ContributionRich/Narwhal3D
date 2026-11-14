//! Both ORIGINAL Python engines captured together, no native-generated answers.
//! Fixed emitter-order JSON framing preserves original dict/tuple semantics.
//! Only fixture decoding here: no tokenizer/planner or JSON-parser dependency.
use narwhal_pure_core::{
    macro_parse::{narwhal_pure_core_macro_encode as encode, narwhal_pure_core_macro_size as size},
    macro_plan::narwhal_pure_core_macro_plan_checked as checked,
};
use std::{collections::BTreeMap, ptr::null_mut};
fn hex(s: &str) -> Vec<u8> {
    assert_eq!(s.len() % 2, 0);
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}
fn planner(text: &[u8], status: i32, payload: &[u8]) {
    let mut needed = -99;
    let query = unsafe { checked(text.as_ptr(), text.len(), null_mut(), 0, &mut needed) };
    assert_eq!(needed, payload.len() as i64);
    assert_eq!(query, if status == -5 { -5 } else { 1 });
    let mut out = vec![0xa5; payload.len() + 2];
    if !payload.is_empty() {
        needed = -99;
        assert_eq!(
            unsafe {
                checked(
                    text.as_ptr(),
                    text.len(),
                    out.as_mut_ptr(),
                    payload.len() - 1,
                    &mut needed,
                )
            },
            1
        );
        assert_eq!(needed, payload.len() as i64);
        assert!(out.iter().all(|v| *v == 0xa5));
    }
    needed = -99;
    assert_eq!(
        unsafe {
            checked(
                text.as_ptr(),
                text.len(),
                out.as_mut_ptr(),
                payload.len(),
                &mut needed,
            )
        },
        status
    );
    assert_eq!(needed, payload.len() as i64);
    assert_eq!(&out[..payload.len()], payload);
    assert_eq!(&out[payload.len()..], &[0xa5, 0xa5]);
}
#[test]
fn all_original_curated_seeded_unicode_casefold_long_and_first_errors() {
    assert_eq!(narwhal_pure_core::narwhal_pure_core_abi_version(), 1);
    let mut groups = BTreeMap::new();
    let mut modes = BTreeMap::new();
    let mut errors = BTreeMap::new();
    for (index, row) in include_str!("fixtures/macro_golden.txt")
        .lines()
        .enumerate()
    {
        let r: Vec<_> = row.split('|').collect();
        assert_eq!(r.len(), 10);
        assert_eq!(r[0].parse::<usize>().unwrap(), index);
        *groups.entry(r[1]).or_insert(0usize) += 1;
        *modes.entry((r[2], r[3])).or_insert(0usize) += 1;
        let text = hex(r[4]);
        let status: i32 = r[5].parse().unwrap();
        let payload = hex(r[6]);
        if r[2] == "T" {
            assert_eq!(status, 0);
            assert!(r[7..].iter().all(|s| s.is_empty()));
            let flag = r[3].parse().unwrap();
            assert_eq!(
                unsafe { size(text.as_ptr(), text.len(), flag) },
                payload.len() as i64,
                "case {}",
                r[0]
            );
            let mut out = vec![0xa5; payload.len() + 2];
            for capacity in [payload.len() - 1, payload.len() + 1] {
                assert_eq!(
                    unsafe { encode(text.as_ptr(), text.len(), flag, out.as_mut_ptr(), capacity) },
                    -3
                );
                assert!(out.iter().all(|v| *v == 0xa5));
            }
            assert_eq!(
                unsafe {
                    encode(
                        text.as_ptr(),
                        text.len(),
                        flag,
                        out.as_mut_ptr(),
                        payload.len(),
                    )
                },
                0
            );
            assert_eq!(
                &out[..payload.len()],
                &payload,
                "case {} origin {}",
                r[0],
                r[1]
            );
            assert_eq!(&out[payload.len()..], &[0xa5, 0xa5]);
        } else {
            assert_eq!((r[2], r[3]), ("P", "0"));
            if status != 0 {
                *errors.entry(status).or_insert(0usize) += 1;
                assert!(!hex(r[8]).is_empty());
                if status == -4 {
                    assert_eq!(r[7], "NonNestableCommandError");
                    assert!(!hex(r[9]).is_empty());
                } else {
                    assert_eq!(status, -5);
                    assert_eq!(r[7], "MacroPlanError");
                    assert!(payload.is_empty() && r[9].is_empty());
                }
            } else {
                assert!(r[7..].iter().all(|s| s.is_empty()));
            }
            planner(&text, status, &payload);
        }
    }
    assert_eq!(groups.values().sum::<usize>(), 6573);
    assert_eq!(modes[&("T", "0")], 2191);
    assert_eq!(modes[&("T", "1")], 2191);
    assert_eq!(modes[&("P", "0")], 2191);
    for (name, count) in [
        ("option_cancel18", 36),
        ("token_owned_STANDARD", 138),
        ("token_owned_ADVERSARIAL", 48),
        ("token_owned_EMPTY_NAME_OPTIONS", 12),
        ("token_owned_UNICODE_ALPHA_BOUNDARY", 12),
        ("token_owned_UNICODE_WS_BOUNDARY", 12),
        ("token_owned_TEXT_CANCEL", 12),
        ("token_owned_LONG_MACRO", 3),
        ("token_owned_BIG_MACRO", 3),
        ("token_owned_seeded", 1200),
        ("plan_owned_test_complete_independent_corpus", 300),
        ("plan_owned_test_seeded_and_long_macros", 4509),
    ] {
        assert_eq!(groups[name], count, "{name}");
    }
    assert_eq!(errors[&-4], 577);
    assert_eq!(errors[&-5], 496);
    assert_eq!(modes[&("P", "0")] - errors.values().sum::<usize>(), 1118);
}
#[test]
fn original_byte_limit_and_utf8_refusals_no_partial_writes() {
    let fixture = include_str!("fixtures/macro_limits.txt");
    assert_eq!(fixture.lines().count(), 6);
    for row in fixture.lines() {
        let r: Vec<_> = row.split('|').collect();
        assert_eq!(r.len(), 6);
        let mut text = hex(r[2]).repeat(r[3].parse().unwrap());
        text.extend(hex(r[4]));
        let expected: i32 = r[5].parse().unwrap();
        let mut out = [0xa5; 64];
        let mut needed = -99;
        if r[0] == "T" {
            let flag = r[1].parse().unwrap();
            assert_eq!(
                unsafe { size(text.as_ptr(), text.len(), flag) },
                expected as i64
            );
            assert_eq!(
                unsafe { encode(text.as_ptr(), text.len(), flag, out.as_mut_ptr(), out.len()) },
                expected
            );
        } else {
            assert_eq!(
                unsafe {
                    checked(
                        text.as_ptr(),
                        text.len(),
                        out.as_mut_ptr(),
                        out.len(),
                        &mut needed,
                    )
                },
                expected
            );
            assert_eq!(needed, 0);
        }
        assert_eq!(out, [0xa5; 64]);
    }
    assert_eq!(include_str!("fixtures/macro_host.txt").lines().count(), 606);
}
#[test]
fn raw_transport_and_typed_refusal_contract() {
    let mut out = [0xa5; 64];
    let mut needed = 123;
    assert_eq!(
        unsafe { checked(std::ptr::null(), 1, out.as_mut_ptr(), 64, &mut needed) },
        -1
    );
    assert_eq!(needed, 0);
    assert_eq!(out, [0xa5; 64]);
    assert_eq!(
        unsafe { checked(b"_Zoom".as_ptr(), 5, out.as_mut_ptr(), 64, null_mut()) },
        -1
    );
    assert_eq!(
        unsafe { encode(b"_Zoom".as_ptr(), 5, 0, null_mut(), unsafe_size_zoom()) },
        -1
    );
    assert_eq!(unsafe { size(std::ptr::null(), 1, 0) }, -1);
    planner(b"'_Box", -4, b"\"Box\"");
    planner(b"=value", -5, b"");
    planner(b"=value '_Box", -5, b"");
    planner(b"'_Box =value", -4, b"\"Box\"");
}
fn unsafe_size_zoom() -> usize {
    unsafe { size(b"_Zoom".as_ptr(), 5, 0) as usize }
}

#[test]
fn option_cancel18_original_oracle_witnesses() {
    let mut count = 0;
    for row in include_str!("fixtures/macro_golden.txt").lines() {
        let r: Vec<_> = row.split('|').collect();
        if r[1] != "option_cancel18" {
            continue;
        }
        let text = hex(r[4]);
        let payload = hex(r[6]);
        if r[2] == "T" {
            let flag = r[3].parse().unwrap();
            let mut out = vec![0xa5; payload.len()];
            assert_eq!(
                unsafe { size(text.as_ptr(), text.len(), flag) },
                payload.len() as i64,
                "original oracle {} lossless={flag}",
                String::from_utf8_lossy(&text)
            );
            assert_eq!(
                unsafe { encode(text.as_ptr(), text.len(), flag, out.as_mut_ptr(), out.len()) },
                0
            );
            assert_eq!(out, payload);
        } else {
            planner(&text, r[5].parse().unwrap(), &payload);
        }
        count += 1;
    }
    assert_eq!(count, 36);
}
