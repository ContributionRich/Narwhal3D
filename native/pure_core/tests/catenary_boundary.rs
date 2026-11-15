//! Direct caller-owned ABI safety, independent of Python validation.
use narwhal_pure_core::coil::narwhal_pure_core_catenary_plan_v2 as plan;
fn invoke(a: &[f64; 13]) -> (i32, u32, u64, u64, u64, f64, Vec<f64>, Vec<f64>) {
    let (mut poles, mut knots) = (vec![123.; 1155], vec![456.; 387]);
    let (mut np, mut nk, mut ns, mut dev, mut reason) = (99, 99, 99, 99., 99);
    let code = unsafe {
        plan(
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
    (code, reason, np, nk, ns, dev, poles, knots)
}
const VALID: [f64; 13] = [-5., 0., 0., 5., 0., 0., 0., -1., 0., 0., -2., 0., 0.001];
#[test]
fn nonfinite_inputs_and_nonpositive_tolerances_publish_nothing() {
    for index in 0..13 {
        for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let mut a = VALID;
            a[index] = bad;
            let (code, reason, np, nk, ns, dev, poles, knots) = invoke(&a);
            assert_eq!(
                (code, reason, np, nk, ns, dev),
                (-2, 1, 0, 0, 0, 0.),
                "index {index}"
            );
            assert!(poles.iter().all(|&v| v == 123.) && knots.iter().all(|&v| v == 456.));
        }
    }
    for bad in [0., -0.0, -1.] {
        let mut a = VALID;
        a[12] = bad;
        let r = invoke(&a);
        assert_eq!((r.0, r.1, r.2, r.3, r.4, r.5), (-2, 1, 0, 0, 0, 0.));
    }
}
#[test]
fn overflowing_finite_arithmetic_never_publishes_garbage() {
    for (a, expected_reason) in [
        (
            [
                1e308, 0., 0., 1e308, 1., 0., 1e308, 0., 1., 0., 0., 0., 0.001,
            ],
            6,
        ),
        (
            [
                -1e308, 0., 0., 1e308, 0., 0., 0., -1., 0., 0., -2., 0., 0.001,
            ],
            6,
        ),
        (
            [-5., 0., 0., 5., 0., 0., 0., -1., 0., 0., -1e308, 0., 0.001],
            5,
        ),
    ] {
        let (code, reason, np, nk, ns, dev, poles, knots) = invoke(&a);
        assert_eq!(
            (code, reason, np, nk, ns, dev),
            (
                if expected_reason == 5 { -4 } else { -2 },
                expected_reason,
                0,
                0,
                0,
                0.
            )
        );
        assert!(poles.iter().all(|&v| v == 123.) && knots.iter().all(|&v| v == 456.));
    }
}
#[test]
fn nulls_and_insufficient_capacities_reset_scalars_and_preserve_arrays() {
    for null in 0..11 {
        let a = VALID;
        let (mut poles, mut knots) = (vec![123.; 1155], vec![456.; 387]);
        let (mut np, mut nk, mut ns, mut dev, mut reason) = (99, 99, 99, 99., 99);
        let mut inputs = [
            a.as_ptr(),
            a[3..].as_ptr(),
            a[6..].as_ptr(),
            a[9..].as_ptr(),
        ];
        if null < 4 {
            inputs[null] = std::ptr::null();
        }
        let code = unsafe {
            plan(
                inputs[0],
                inputs[1],
                inputs[2],
                inputs[3],
                a[12],
                if null == 4 {
                    std::ptr::null_mut()
                } else {
                    poles.as_mut_ptr()
                },
                poles.len(),
                if null == 5 {
                    std::ptr::null_mut()
                } else {
                    knots.as_mut_ptr()
                },
                knots.len(),
                if null == 6 {
                    std::ptr::null_mut()
                } else {
                    &mut np
                },
                if null == 7 {
                    std::ptr::null_mut()
                } else {
                    &mut nk
                },
                if null == 8 {
                    std::ptr::null_mut()
                } else {
                    &mut ns
                },
                if null == 9 {
                    std::ptr::null_mut()
                } else {
                    &mut dev
                },
                if null == 10 {
                    std::ptr::null_mut()
                } else {
                    &mut reason
                },
            )
        };
        assert_eq!(code, -1, "null {null}");
        for (index, value) in [(6, np), (7, nk), (8, ns)] {
            assert_eq!(value, if index == null { 99 } else { 0 });
        }
        assert_eq!(dev, if null == 9 { 99. } else { 0. });
        assert_eq!(reason, if null == 10 { 99 } else { 0 });
        assert!(poles.iter().all(|&v| v == 123.) && knots.iter().all(|&v| v == 456.));
    }
    for (pc, kc) in [(0, 387), (74, 387), (1155, 26)] {
        let a = VALID;
        let (mut poles, mut knots) = (vec![123.; 1155], vec![456.; 387]);
        let (mut np, mut nk, mut ns, mut dev, mut reason) = (99, 99, 99, 99., 99);
        let code = unsafe {
            plan(
                a.as_ptr(),
                a[3..].as_ptr(),
                a[6..].as_ptr(),
                a[9..].as_ptr(),
                a[12],
                poles.as_mut_ptr(),
                pc,
                knots.as_mut_ptr(),
                kc,
                &mut np,
                &mut nk,
                &mut ns,
                &mut dev,
                &mut reason,
            )
        };
        assert_eq!((code, reason, np, nk, ns, dev), (-3, 0, 0, 0, 0, 0.));
        assert!(poles.iter().all(|&v| v == 123.) && knots.iter().all(|&v| v == 456.));
    }
}
