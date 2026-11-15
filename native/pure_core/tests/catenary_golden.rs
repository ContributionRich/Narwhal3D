//! Frozen independent original CPython 3.13 answers; no tolerance in bit parity.
use narwhal_pure_core::coil::{
    narwhal_pure_core_catenary_plan, narwhal_pure_core_catenary_plan_v2,
};
fn values(text: &str) -> Vec<f64> {
    text.split(',')
        .map(|v| f64::from_bits(u64::from_str_radix(v, 16).unwrap()))
        .collect()
}
#[test]
fn original_catenary_answers_are_bit_exact() {
    let mut successes = 0;
    let mut failures = 0;
    let linux: Vec<_> = include_str!("fixtures/catenary_numeric_linux.txt")
        .lines()
        .filter(|s| !s.starts_with('#'))
        .collect();
    let windows: Vec<_> = include_str!("fixtures/catenary_numeric_windows_msvc.txt")
        .lines()
        .filter(|s| !s.starts_with('#'))
        .collect();
    assert_eq!((linux.len(), windows.len()), (42, 42));
    let mut names = std::collections::HashSet::new();
    let mut differing = 0;
    for (l, w) in linux.iter().zip(&windows) {
        let l: Vec<_> = l.split('|').collect();
        let w: Vec<_> = w.split('|').collect();
        assert!(names.insert(l[0]));
        assert_eq!(&l[..3], &w[..3], "platform fixture row/input/status set");
        if l[2] == "error" {
            assert_eq!(l, w);
        }
        differing += usize::from(l != w);
    }
    assert_eq!(
        differing, 25,
        "independent Windows capture must not be vacuous"
    );
    let rows = if cfg!(all(
        target_os = "windows",
        target_env = "msvc",
        target_arch = "x86_64"
    )) {
        windows
    } else {
        linux
    };
    for row in rows {
        let f: Vec<_> = row.split('|').collect();
        let a = values(f[1]);
        let (mut poles, mut knots) = (vec![123.; 1155], vec![456.; 387]);
        let (mut np, mut nk, mut ns, mut dev) = (99, 99, 99, 99.);
        let mut reason = 99;
        let code = unsafe {
            narwhal_pure_core_catenary_plan_v2(
                a.as_ptr(),
                a[3..].as_ptr(),
                a[6..].as_ptr(),
                a[9..].as_ptr(),
                a[12],
                poles.as_mut_ptr(),
                poles.len(),
                knots.as_mut_ptr(),
                knots.len(),
                &mut np,
                &mut nk,
                &mut ns,
                &mut dev,
                &mut reason,
            )
        };
        let mut legacy_poles = vec![123.; 1155];
        let mut legacy_knots = vec![456.; 387];
        let (mut lp, mut lk, mut ls, mut ld) = (99, 99, 99, 99.);
        let legacy_code = unsafe {
            narwhal_pure_core_catenary_plan(
                a.as_ptr(),
                a[3..].as_ptr(),
                a[6..].as_ptr(),
                a[9..].as_ptr(),
                a[12],
                legacy_poles.as_mut_ptr(),
                legacy_poles.len(),
                legacy_knots.as_mut_ptr(),
                legacy_knots.len(),
                &mut lp,
                &mut lk,
                &mut ls,
                &mut ld,
            )
        };
        assert_eq!(legacy_code, code);
        assert_eq!((lp, lk, ls, ld.to_bits()), (np, nk, ns, dev.to_bits()));
        assert_eq!(
            legacy_poles.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
            poles.iter().map(|v| v.to_bits()).collect::<Vec<_>>()
        );
        assert_eq!(
            legacy_knots.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
            knots.iter().map(|v| v.to_bits()).collect::<Vec<_>>()
        );
        if f[2] == "error" {
            let expected_reason = match f[3] {
                "direction must be non-zero" => 2,
                "the supported catenary needs equal-height endpoints" => 3,
                "ThroughPoint must be the positive midpoint sag on the catenary axis" => 4,
                "requested catenary tolerance was not achieved" => 5,
                _ => panic!("unknown original error"),
            };
            assert_eq!(reason, expected_reason, "{}", f[0]);
            assert_eq!(
                code,
                if f[3] == "requested catenary tolerance was not achieved" {
                    -4
                } else {
                    -2
                },
                "{}",
                f[0]
            );
            assert_eq!((np, nk, ns, dev), (0, 0, 0, 0.));
            assert!(poles.iter().all(|&v| v == 123.) && knots.iter().all(|&v| v == 456.));
            failures += 1;
            continue;
        }
        assert_eq!(code, 0, "{}", f[0]);
        assert_eq!(reason, 0, "{}", f[0]);
        for (label, actual, expected) in [
            ("poles", &poles[..np as usize], values(f[3])),
            ("knots", &knots[..nk as usize], values(f[4])),
        ] {
            assert_eq!(actual.len(), expected.len(), "{} {label}", f[0]);
            for (i, (a, b)) in actual.iter().zip(expected).enumerate() {
                assert_eq!(a.to_bits(), b.to_bits(), "{} {label}[{i}]", f[0]);
            }
        }
        assert_eq!(ns, f[5].parse::<u64>().unwrap(), "{} spans", f[0]);
        assert_eq!(
            dev.to_bits(),
            values(f[6])[0].to_bits(),
            "{} deviation",
            f[0]
        );
        successes += 1;
    }
    assert_eq!((successes, failures), (34, 8));
}
