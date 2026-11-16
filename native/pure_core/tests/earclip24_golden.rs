//! Independent original Python inputs, IEEE areas and exact ordered/partial triples.
use narwhal_pure_core::earclip::{
    narwhal_pure_core_ear_clip_display as clip, narwhal_pure_core_earclip_signed_area_v1 as area,
};
fn values(s: &str) -> Vec<f64> {
    if s.is_empty() {
        vec![]
    } else {
        s.split(',')
            .map(|v| f64::from_bits(u64::from_str_radix(v, 16).unwrap()))
            .collect()
    }
}
fn indices(s: &str) -> Vec<u64> {
    if s.is_empty() {
        vec![]
    } else {
        s.split(',').map(|v| v.parse().unwrap()).collect()
    }
}
#[test]
fn original_108_loops_exact_order_partial_and_area_bits() {
    let rows: Vec<_> = include_str!("fixtures/earclip24_original.txt")
        .lines()
        .collect();
    assert_eq!(rows.len(), 108);
    let mut partial = 0;
    for row in rows {
        let f: Vec<_> = row.split('|').collect();
        assert_eq!(f.len(), 4);
        let xy = values(f[1]);
        let n = xy.len() / 2;
        let expected = indices(f[3]);
        let capacity = n.saturating_sub(2) * 3;
        let mut out = vec![u64::MAX; capacity + 8];
        let mut count = u64::MAX;
        assert_eq!(
            unsafe { clip(xy.as_ptr(), n, out.as_mut_ptr(), capacity, &mut count) },
            0,
            "{}",
            f[0]
        );
        assert_eq!(count as usize, expected.len() / 3, "{}", f[0]);
        assert_eq!(&out[..expected.len()], expected, "{}", f[0]);
        assert!(
            out[expected.len()..].iter().all(|v| *v == u64::MAX),
            "{}",
            f[0]
        );
        if n >= 3 && expected.len() / 3 != n - 2 {
            partial += 1;
        }
        let mut actual = f64::from_bits(0x7ff8000000000024);
        count = u64::MAX;
        assert_eq!(
            unsafe { area(xy.as_ptr(), n, &mut actual, &mut count) },
            0,
            "{}",
            f[0]
        );
        assert_eq!(count, 1);
        assert_eq!(
            actual.to_bits(),
            u64::from_str_radix(f[2], 16).unwrap(),
            "{}",
            f[0]
        );
    }
    assert_eq!(partial, 16);
}
#[test]
fn legacy_refusals_preserve_payload_and_clear_count() {
    let good = [0., 0., 1., 0., 1., 1., 0., 1.];
    let mut out = [77u64; 9];
    let mut count = 123;
    assert_eq!(
        unsafe { clip(good.as_ptr(), 4, out.as_mut_ptr(), 5, &mut count) },
        -3
    );
    assert_eq!(count, 0);
    assert_eq!(out, [77; 9]);
    for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let xy = [0., 0., 1., 0., bad, 1., 0., 1.];
        count = 123;
        assert_eq!(
            unsafe { clip(xy.as_ptr(), 4, out.as_mut_ptr(), 9, &mut count) },
            -2
        );
        assert_eq!(count, 0);
        assert_eq!(out, [77; 9]);
    }
    count = 123;
    assert_eq!(
        unsafe { clip(std::ptr::null(), 4, out.as_mut_ptr(), 9, &mut count) },
        -1
    );
    assert_eq!(count, 0);
    assert_eq!(out, [77; 9]);
    assert_eq!(
        unsafe { clip(good.as_ptr(), 4, out.as_mut_ptr(), 9, std::ptr::null_mut()) },
        -1
    );
    count = 123;
    assert_eq!(
        unsafe {
            clip(
                std::ptr::null(),
                (1 << 20) + 1,
                out.as_mut_ptr(),
                9,
                &mut count,
            )
        },
        -2
    );
    assert_eq!(count, 0);
    assert_eq!(out, [77; 9]);
    assert_eq!(
        unsafe { clip(std::ptr::null(), 0, std::ptr::null_mut(), 0, &mut count) },
        0
    );
    assert_eq!(count, 0);
}
#[test]
fn signed_area_refusals_are_atomic_and_empty_is_native_zero() {
    let mut out = 123.;
    let mut count = 99;
    assert_eq!(
        unsafe { area(std::ptr::null(), 1, &mut out, &mut count) },
        -1
    );
    assert_eq!((out, count), (123., 0));
    count = 99;
    assert_eq!(
        unsafe { area(std::ptr::null(), (1 << 20) + 1, &mut out, &mut count) },
        -2
    );
    assert_eq!((out, count), (123., 0));
    assert_eq!(
        unsafe { area(std::ptr::null(), 0, &mut out, std::ptr::null_mut()) },
        -1
    );
    assert_eq!(out, 123.);
    assert_eq!(
        unsafe { area(std::ptr::null(), 0, std::ptr::null_mut(), &mut count) },
        -1
    );
    assert_eq!(count, 0);
    assert_eq!(
        unsafe { area(std::ptr::null(), 0, &mut out, &mut count) },
        0
    );
    assert_eq!(out.to_bits(), 0f64.to_bits());
    assert_eq!(count, 1);
}
