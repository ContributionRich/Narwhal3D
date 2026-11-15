//! Original forced-CPython Tween answers, including valid SDK rational circles.
use narwhal_pure_core::tween::{
    narwhal_pure_core_tween_controls as legacy, narwhal_pure_core_tween_knots_v1 as knots,
    narwhal_pure_core_tween_lengths_v1 as lengths, narwhal_pure_core_tween_plan_v1 as plan,
};
fn values(s: &str) -> Vec<f64> {
    s.split(',')
        .map(|s| f64::from_bits(u64::from_str_radix(s, 16).unwrap()))
        .collect()
}
#[test]
fn original_bit_exact_plans() {
    let rows: Vec<_> = include_str!("fixtures/tween_original23.txt")
        .lines()
        .collect();
    assert_eq!(rows.len(), 19);
    for (case, row) in rows.iter().enumerate() {
        let f: Vec<_> = row.split('|').collect();
        assert_eq!(f.len(), 6);
        let mode: u32 = f[0].parse().unwrap();
        let number: usize = f[1].parse().unwrap();
        let flags: u32 = f[2].parse().unwrap();
        let a = values(f[3]);
        let b = values(f[4]);
        let expected = values(f[5]);
        let width = if mode == 2 { 3 } else { 4 };
        let mut out = vec![f64::NAN; expected.len()];
        let mut used = u64::MAX;
        let rc = unsafe {
            plan(
                a.as_ptr(),
                b.as_ptr(),
                a.len() / width,
                number,
                mode,
                flags,
                out.as_mut_ptr(),
                out.len(),
                &mut used,
            )
        };
        assert_eq!(rc, 0, "case {case}");
        assert_eq!(used, expected.len() as u64);
        assert_eq!(
            out.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
            expected.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
            "case {case}"
        );
        if mode == 0 && flags == 0 {
            let mut old = vec![0.; expected.len()];
            let mut used = 0;
            assert_eq!(
                unsafe {
                    legacy(
                        a.as_ptr(),
                        b.as_ptr(),
                        a.len() / 4,
                        number,
                        old.as_mut_ptr(),
                        old.len(),
                        &mut used,
                    )
                },
                0
            );
            assert_eq!(
                old.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
                expected.iter().map(|x| x.to_bits()).collect::<Vec<_>>()
            );
        }
    }
}
#[test]
fn errors_are_atomic_and_typed() {
    let good = [0., 1., 2., 1.];
    let cases = [
        ([0., 0., 0., 0.], good, 0, 3, -10),
        ([f64::NAN, 0., 0., 1.], good, 0, 0, -12),
        ([f64::MAX, 0., 0., 1.], [-f64::MAX, 0., 0., 1.], 0, 0, -11),
        ([0., 0., 0., 1.], [0., 0., 0., -1.], 0, 3, -11),
        (
            [f64::MAX, 0., 0., 1.],
            [f64::MAX, 0., 0., f64::MAX],
            0,
            3,
            -11,
        ),
    ];
    for (a, b, mode, flags, error) in cases {
        let mut out = [13.; 4];
        let mut used = 777;
        assert_eq!(
            unsafe {
                plan(
                    a.as_ptr(),
                    b.as_ptr(),
                    1,
                    1,
                    mode,
                    flags,
                    out.as_mut_ptr(),
                    4,
                    &mut used,
                )
            },
            error
        );
        assert_eq!(out, [13.; 4]);
        assert_eq!(used, 777);
    }
    // Overflow in a later row must not publish an earlier valid row.
    let a = [5., 0., 0., 1.];
    let b = [0., 0., 0., f64::MAX];
    let mut out = [13.; 12];
    let mut used = 777;
    assert_eq!(
        unsafe {
            plan(
                a.as_ptr(),
                b.as_ptr(),
                1,
                3,
                0,
                3,
                out.as_mut_ptr(),
                12,
                &mut used,
            )
        },
        -11
    );
    assert_eq!(out, [13.; 12]);
    assert_eq!(used, 777);
    for (count, num, mode, cap, expected) in [
        (1, 1, 0, 3, -3),
        (0, 1, 0, 4, -2),
        (1, 0, 0, 4, -2),
        (1, 1, 99, 4, -2),
        (usize::MAX, 1, 0, 4, -13),
        (1, usize::MAX, 0, 4, -13),
        (1, 12_500_001, 0, 4, -13),
    ] {
        let mut out = [13.; 4];
        let mut used = 777;
        assert_eq!(
            unsafe {
                plan(
                    good.as_ptr(),
                    good.as_ptr(),
                    count,
                    num,
                    mode,
                    0,
                    out.as_mut_ptr(),
                    cap,
                    &mut used,
                )
            },
            expected
        );
        assert_eq!(out, [13.; 4]);
        assert_eq!(used, 777);
    }
    let mut out = [13.; 4];
    let mut used = 777;
    assert_eq!(
        unsafe {
            plan(
                std::ptr::null(),
                good.as_ptr(),
                1,
                1,
                0,
                0,
                out.as_mut_ptr(),
                4,
                &mut used,
            )
        },
        -1
    );
    assert_eq!(used, 777);
    assert_eq!(out, [13.; 4]);
    assert_eq!(
        unsafe {
            plan(
                good.as_ptr(),
                good.as_ptr(),
                1,
                1,
                0,
                0,
                out.as_mut_ptr(),
                4,
                std::ptr::null_mut(),
            )
        },
        -1
    );
}
#[test]
fn knots_and_lengths_boundaries() {
    assert_eq!(
        unsafe { knots([0., 0.5, 1.].as_ptr(), [0., 0.5 + 1e-11, 1.].as_ptr(), 3) },
        0
    );
    assert_eq!(
        unsafe { knots([0., 0.5, 1.].as_ptr(), [0., 0.6, 1.].as_ptr(), 3) },
        -20
    );
    assert_eq!(unsafe { knots([f64::NAN].as_ptr(), [0.].as_ptr(), 1) }, -12);
    let mut out = [13.; 3];
    let mut used = 777;
    assert_eq!(
        unsafe { lengths(f64::MAX, 3, out.as_mut_ptr(), 3, &mut used) },
        -11
    );
    assert_eq!(out, [13.; 3]);
    assert_eq!(used, 777);
    assert_eq!(
        unsafe { lengths(1., 3, out.as_mut_ptr(), 2, &mut used) },
        -3
    );
    assert_eq!(used, 777);
    assert_eq!(unsafe { lengths(1., 3, out.as_mut_ptr(), 3, &mut used) }, 0);
    assert_eq!(out, [0., 0.5, 1.]);
    assert_eq!(used, 3);
}
#[test]
fn original_sample_target_bits() {
    let rows: Vec<_> = include_str!("fixtures/tween_lengths23.txt")
        .lines()
        .collect();
    assert_eq!(rows.len(), 4);
    for row in rows {
        let f: Vec<_> = row.split('|').collect();
        let total = values(f[0])[0];
        let n: usize = f[1].parse().unwrap();
        let expected = values(f[2]);
        let mut out = vec![0.; n];
        let mut used = u64::MAX;
        assert_eq!(
            unsafe { lengths(total, n, out.as_mut_ptr(), n, &mut used) },
            0
        );
        assert_eq!(used, n as u64);
        assert_eq!(
            out.iter().map(|x| x.to_bits()).collect::<Vec<_>>(),
            expected.iter().map(|x| x.to_bits()).collect::<Vec<_>>()
        );
    }
}
