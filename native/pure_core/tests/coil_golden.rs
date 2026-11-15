//! Independent frozen CPython 3.13 original answers; exact IEEE754, not geometric tolerance.
use narwhal_pure_core::coil::narwhal_pure_core_coil_plan;

// Actual original CPython/MSVC capture; admission runs on every platform.
fn capture_fingerprint(text: &str) -> u64 {
    text.lines()
        .flat_map(|line| line.bytes().chain(std::iter::once(b'\n')))
        .fold(0xcbf29ce484222325, |h, b| {
            (h ^ u64::from(b)).wrapping_mul(0x100000001b3)
        })
}
fn platform_coil_rows() -> Vec<&'static str> {
    fn rows(text: &'static str) -> impl Iterator<Item = &'static str> {
        text.lines()
            .filter(|row| !row.is_empty() && !row.starts_with('#'))
    }
    let linux = include_str!("fixtures/coil_numeric.txt");
    let windows = include_str!("fixtures/coil_windows_msvc.txt");
    let provenance = include_str!("fixtures/coil_windows_msvc_provenance.txt");
    assert_eq!(capture_fingerprint(linux), 0xcc0dc814d56a2008);
    assert_eq!(capture_fingerprint(windows), 0xf3b1dcbe64702ec4);
    assert_eq!(capture_fingerprint(provenance), 0xa1a1336474f739b1);
    let originals: std::collections::BTreeMap<_, _> = rows(linux)
        .map(|row| (row.split('|').next().unwrap(), row))
        .collect();
    assert_eq!(originals.len(), 33);
    assert_eq!(
        originals.len(),
        rows(linux).count(),
        "duplicate original coil ID"
    );
    let mut overrides = std::collections::BTreeMap::new();
    for row in rows(windows) {
        let id = row.split('|').next().unwrap();
        let original = originals.get(id).expect("unknown Windows coil ID");
        let old: Vec<_> = original.split('|').collect();
        let new: Vec<_> = row.split('|').collect();
        assert_eq!(old.len(), new.len());
        for column in 0..old.len() {
            if ![3, 6].contains(&column) {
                assert_eq!(
                    old[column], new[column],
                    "coil input/status/structure {id} column {column}"
                );
            }
        }
        assert_ne!(*original, row, "vacuous Windows coil override {id}");
        assert!(
            overrides.insert(id, row).is_none(),
            "duplicate Windows coil ID {id}"
        );
    }
    assert_eq!(overrides.len(), 19);
    let windows_msvc = cfg!(all(
        target_os = "windows",
        target_env = "msvc",
        target_arch = "x86_64"
    ));
    let selected: Vec<_> = rows(linux)
        .map(|row| {
            let id = row.split('|').next().unwrap();
            if windows_msvc {
                overrides.get(id).copied().unwrap_or(row)
            } else {
                row
            }
        })
        .collect();
    assert_eq!(selected.len(), 33);
    assert!(selected
        .iter()
        .map(|row| row.split('|').next().unwrap())
        .eq(rows(linux).map(|row| row.split('|').next().unwrap())));
    selected
}

fn values(s: &str) -> Vec<f64> {
    s.split(',')
        .map(|v| f64::from_bits(u64::from_str_radix(v, 16).unwrap()))
        .collect()
}
#[test]
fn original_coil_answers_are_bit_exact() {
    for line in platform_coil_rows() {
        let f: Vec<_> = line.split('|').collect();
        let a = values(f[1]);
        let mut poles = vec![0.; 60000];
        let mut knots = vec![0.; 20002];
        let (mut np, mut nk, mut ns, mut dev) = (0, 0, 0, 0.);
        let code = unsafe {
            narwhal_pure_core_coil_plan(
                a.as_ptr(),
                a[3..].as_ptr(),
                a[6..].as_ptr(),
                a[9],
                a[10],
                a[11],
                a[12] as u8,
                a[13],
                a[14] as u8,
                poles.as_mut_ptr(),
                poles.len(),
                knots.as_mut_ptr(),
                knots.len(),
                &mut np,
                &mut nk,
                &mut ns,
                &mut dev,
            )
        };
        if f[2] == "error" {
            assert_eq!(
                code,
                if f[3] == "requested coil tolerance was not achieved" {
                    -4
                } else {
                    -2
                },
                "{}",
                f[0]
            );
            assert_eq!((np, nk, ns, dev), (0, 0, 0, 0.));
            continue;
        }
        assert_eq!(code, 0, "{}", f[0]);
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
    }
}
