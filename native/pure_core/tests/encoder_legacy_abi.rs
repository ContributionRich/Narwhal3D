//! Frozen independent captures protect retired Python adapters' stable C exports.
use narwhal_pure_core::{dxf, vector_export as vector};
fn hex(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}
fn ints(s: &str) -> Vec<i64> {
    if s.is_empty() {
        vec![]
    } else {
        s.split(',').map(|v| v.parse().unwrap()).collect()
    }
}
#[test]
fn legacy_dxf_exact_capacity_and_independent_bytes() {
    let rows: Vec<_> = include_str!("fixtures/dxf_legacy23.txt").lines().collect();
    assert_eq!(rows.len(), 4);
    for row in rows {
        let f: Vec<_> = row.split('|').collect();
        assert_eq!(f.len(), 11);
        let names = hex(f[1]);
        let lens = ints(f[2]);
        let lc = ints(f[3]);
        let types = ints(f[4]);
        let lids = ints(f[5]);
        let colors = ints(f[6]);
        let nums: Vec<f64> = if f[7].is_empty() {
            vec![]
        } else {
            f[7].split(',')
                .map(|s| f64::from_bits(u64::from_str_radix(s, 16).unwrap()))
                .collect()
        };
        let counts = ints(f[8]);
        let closed = ints(f[9]);
        let expected = hex(f[10]);
        macro_rules! call { ($fun:path $(, $arg:expr)*) => { unsafe { $fun(
            names.as_ptr(), names.len() as i64, lens.as_ptr(), lc.as_ptr(), lens.len() as i64,
            types.as_ptr(), lids.as_ptr(), colors.as_ptr(), nums.as_ptr(), nums.len() as i64,
            counts.as_ptr(), closed.as_ptr(), types.len() as i64 $(, $arg)*) } }; }
        assert_eq!(
            call!(dxf::narwhal_pure_core_dxf_size),
            expected.len() as i64,
            "{}",
            f[0]
        );
        let mut out = vec![0xa5; 1024 * 1024];
        assert_eq!(
            call!(
                dxf::narwhal_pure_core_dxf_encode,
                out.as_mut_ptr(),
                expected.len()
            ),
            0
        );
        assert_eq!(&out[..expected.len()], expected);
        assert!(out[expected.len()..].iter().all(|b| *b == 0xa5));
        for cap in [expected.len() - 1, expected.len() + 1] {
            assert_ne!(
                call!(dxf::narwhal_pure_core_dxf_encode, out.as_mut_ptr(), cap),
                0
            );
        }
        let mut written = -1;
        out.fill(0xa5);
        assert_eq!(
            call!(
                dxf::narwhal_pure_core_dxf_encode_into,
                out.as_mut_ptr(),
                out.len(),
                &mut written
            ),
            0
        );
        assert_eq!(written, expected.len() as i64);
        assert_eq!(&out[..expected.len()], expected);
        assert!(out[expected.len()..].iter().all(|b| *b == 0xa5));
        assert_ne!(
            call!(
                dxf::narwhal_pure_core_dxf_encode_into,
                out.as_mut_ptr(),
                expected.len() - 1,
                &mut written
            ),
            0
        );
        assert_ne!(
            call!(
                dxf::narwhal_pure_core_dxf_encode_into,
                std::ptr::null_mut(),
                out.len(),
                &mut written
            ),
            0
        );
        assert_ne!(
            call!(
                dxf::narwhal_pure_core_dxf_encode_into,
                out.as_mut_ptr(),
                out.len(),
                std::ptr::null_mut()
            ),
            0
        );
    }
}
#[test]
fn legacy_vector_all_three_exports_match_independent_originals() {
    let rows: Vec<_> = include_str!("fixtures/vector_legacy23.txt")
        .lines()
        .collect();
    assert_eq!(rows.len(), 3);
    let bz = [1., 2., 3., 4., 5., 6., 7., 8.];
    let rgb = [1., 0., 0.];
    let counts = [1i64];
    let closed = [0i32];
    let widths = [0.5];
    let lens = [7i64];
    for (mode, row) in rows.iter().enumerate() {
        let (label, data) = row.split_once('|').unwrap();
        let expected = hex(data);
        let mut out = vec![0xa5; 1024 * 1024];
        let mut written = -1;
        let run = |out: *mut u8, cap, written: *mut i64| unsafe {
            match mode {
                0 => vector::narwhal_pure_core_pdf_encode_into(
                    bz.as_ptr(),
                    1,
                    counts.as_ptr(),
                    closed.as_ptr(),
                    rgb.as_ptr(),
                    widths.as_ptr(),
                    b"Startup".as_ptr(),
                    lens.as_ptr(),
                    b"startup".as_ptr(),
                    7,
                    100.,
                    80.,
                    1,
                    out,
                    cap,
                    written,
                ),
                1 => vector::narwhal_pure_core_svg_encode_into(
                    bz.as_ptr(),
                    1,
                    counts.as_ptr(),
                    closed.as_ptr(),
                    rgb.as_ptr(),
                    widths.as_ptr(),
                    b"Startup".as_ptr(),
                    lens.as_ptr(),
                    100.,
                    80.,
                    1,
                    out,
                    cap,
                    written,
                ),
                _ => vector::narwhal_pure_core_svg_encode_unit_into(
                    bz.as_ptr(),
                    1,
                    counts.as_ptr(),
                    closed.as_ptr(),
                    rgb.as_ptr(),
                    widths.as_ptr(),
                    b"Startup".as_ptr(),
                    lens.as_ptr(),
                    100.,
                    80.,
                    1,
                    b"in".as_ptr(),
                    2,
                    25.4,
                    4,
                    out,
                    cap,
                    written,
                ),
            }
        };
        assert_eq!(run(out.as_mut_ptr(), out.len(), &mut written), 0, "{label}");
        assert_eq!(written, expected.len() as i64, "{label}");
        assert_eq!(&out[..expected.len()], expected, "{label}");
        assert!(out[expected.len()..].iter().all(|b| *b == 0xa5));
        assert_ne!(run(out.as_mut_ptr(), expected.len() - 1, &mut written), 0);
        assert_ne!(run(std::ptr::null_mut(), out.len(), &mut written), 0);
        assert_ne!(run(out.as_mut_ptr(), out.len(), std::ptr::null_mut()), 0);
    }
}
