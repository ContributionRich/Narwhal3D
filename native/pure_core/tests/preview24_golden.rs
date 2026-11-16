//! Independent original CPython 3.13 bits; never calls a Python reference engine.
use narwhal_pure_core::{
    bspline,
    preview_primitives::{self as primitive, Metadata},
};
fn values(text: &str) -> Vec<f64> {
    if text.is_empty() {
        return vec![];
    }
    text.split(',')
        .map(|v| f64::from_bits(u64::from_str_radix(v, 16).unwrap()))
        .collect()
}
#[test]
fn original_bits_and_order() {
    let mut count = 0;
    let linux: Vec<_> = include_str!("data/preview24_linux.tsv").lines().collect();
    let windows: Vec<_> = include_str!("data/preview24_windows_msvc.tsv")
        .lines()
        .collect();
    assert_eq!((linux.len(), windows.len()), (978, 978));
    let mut differences = 0;
    for (l, w) in linux.iter().zip(&windows) {
        let l: Vec<_> = l.split('\t').collect();
        let w: Vec<_> = w.split('\t').collect();
        assert_eq!((l.len(), w.len()), (6, 6));
        assert_eq!(
            &l[..5],
            &w[..5],
            "independent platform fixture inputs and row sizes"
        );
        differences += usize::from(l[5] != w[5]);
    }
    assert_eq!(differences, 204, "Windows capture must be non-vacuous");
    let rows = if cfg!(all(
        target_os = "windows",
        target_env = "msvc",
        target_arch = "x86_64"
    )) {
        windows
    } else {
        linux
    };
    for (line_no, line) in rows.iter().enumerate() {
        let f: Vec<_> = line.split('\t').collect();
        let op: u32 = f[0].parse().unwrap();
        let n: i64 = f[1].parse().unwrap();
        let flags: i64 = f[2].parse().unwrap();
        let d = values(f[3]);
        let lengths: Vec<usize> = if f[4].is_empty() {
            vec![]
        } else {
            f[4].split(',').map(|v| v.parse().unwrap()).collect()
        };
        let expected = values(f[5]);
        let rows = if op == 10 {
            let p: Vec<[f64; 3]> = d.chunks_exact(3).map(|v| [v[0], v[1], v[2]]).collect();
            vec![bspline::control_point_curve_polyline(
                &p,
                n.max(0).min(p.len() as i64 - 1) as u32,
                flags.max(0) as u32,
            )]
        } else {
            primitive::sample(op, &d, n as usize, flags as u32).unwrap()
        };
        assert_eq!(
            rows.iter().map(Vec::len).collect::<Vec<_>>(),
            lengths,
            "case {line_no}"
        );
        let got: Vec<_> = rows
            .iter()
            .flatten()
            .flatten()
            .map(|v| v.to_bits())
            .collect();
        assert_eq!(
            got,
            expected.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
            "case {line_no}"
        );
        count += 1;
    }
    assert_eq!(count, 978);
}
#[test]
fn primitive_abi_transactional_and_size_contract() {
    unsafe {
        let d = [0., 0., 0., 1., 0., 0., 0., 1., 0., 2.];
        let mut out = [123.0; 24];
        let sentinel = Metadata {
            values: 17,
            rows: 19,
            lengths: [23; 3],
        };
        let mut meta = sentinel;
        let call = |data: *const f64, len, output: *mut f64, capacity, meta: *mut Metadata| {
            primitive::narwhal_pure_core_preview_sample_v1(
                1, data, len, 7, 0, output, capacity, meta,
            )
        };
        assert_eq!(call(d.as_ptr(), 10, out.as_mut_ptr(), 23, &mut meta), -3);
        assert_eq!(out, [123.; 24]);
        assert_eq!(meta.values, 24);
        assert_eq!(meta.rows, 1);
        assert_eq!(meta.lengths, [8, 0, 0]);
        meta = sentinel;
        let mut bad = d;
        bad[9] = f64::NAN;
        assert_eq!(call(bad.as_ptr(), 10, out.as_mut_ptr(), 24, &mut meta), -2);
        assert_eq!(out, [123.; 24]);
        assert_eq!(meta, sentinel);
        assert_eq!(
            call(d.as_ptr(), 10, std::ptr::null_mut(), 24, &mut meta),
            -1
        );
        assert_eq!(meta, sentinel);
        assert_eq!(
            call(std::ptr::null(), 10, out.as_mut_ptr(), 24, &mut meta),
            -1
        );
        assert_eq!(
            call(d.as_ptr(), 10, d.as_ptr() as *mut f64, 24, &mut meta),
            -1
        );
        assert_eq!(
            call(
                d.as_ptr(),
                10,
                out.as_mut_ptr(),
                24,
                d.as_ptr() as *mut Metadata
            ),
            -1
        );
        assert_eq!(call(d.as_ptr(), 10, out.as_mut_ptr(), 24, &mut meta), 0);
        assert_eq!(meta.values, 24);
    }
}
#[test]
fn primitive_resource_shape_and_overflow_refusals() {
    let d = [0., 0., 0., 1., 0., 0., 0., 1., 0., 2.];
    for n in [0, 100001, usize::MAX] {
        assert_eq!(primitive::sample(1, &d, n, 0), Err(-2));
    }
    assert_eq!(primitive::sample(1, &d[..9], 64, 0), Err(-2));
    assert_eq!(primitive::sample(1, &d, 64, 1), Err(-2));
    assert_eq!(primitive::sample(6, &d[..6], 1, 0), Err(-2));
    let mut huge = d;
    huge[3] = f64::MAX;
    huge[9] = f64::MAX;
    assert_eq!(primitive::sample(1, &huge, 64, 0), Err(-2));
}
#[test]
fn control_abi_bounded_and_nonfinite_transactional() {
    unsafe {
        let p = [f64::MAX; 12];
        let mut out = [123.; 39];
        let mut used = 0;
        // A convex interpolation of finite extremes need not overflow: test NaN directly.
        let mut p = p;
        p[3] = f64::NAN;
        assert_eq!(
            bspline::narwhal_pure_core_control_point_curve_preview(
                p.as_ptr(),
                4,
                3,
                12,
                out.as_mut_ptr(),
                39,
                &mut used
            ),
            -2
        );
        assert_eq!(out, [123.; 39]);
        let p = vec![1.0; 3000];
        assert_eq!(
            bspline::narwhal_pure_core_control_point_curve_preview(
                p.as_ptr(),
                1000,
                999,
                1024,
                std::ptr::null_mut(),
                0,
                &mut used
            ),
            -2
        );
    }
}

#[test]
fn control_abi_layout_and_legacy_capacity_contract() {
    unsafe {
        let p = [0., 0., 0., 1., 2., 3., 4., 5., 6.];
        let mut out = [123.; 39];
        let mut used = 777;
        let call = |output: *mut f64, cap, metadata: *mut u64| {
            bspline::narwhal_pure_core_control_point_curve_preview(
                p.as_ptr(),
                3,
                2,
                12,
                output,
                cap,
                metadata,
            )
        };
        assert_eq!(call(out.as_mut_ptr(), 38, &mut used), -3);
        assert_eq!(used, 39);
        assert_eq!(out, [123.; 39]);
        assert_eq!(call(p.as_ptr() as *mut f64, 9, &mut used), -1);
        assert_eq!(call(out.as_mut_ptr(), 39, p.as_ptr() as *mut u64), -1);
        assert_eq!(call(out.as_mut_ptr(), 39, out.as_mut_ptr() as *mut u64), -1);
        let mut aligned = [0_u64; 2];
        let misaligned = (aligned.as_mut_ptr() as *mut u8).add(1) as *mut u64;
        assert_eq!(call(out.as_mut_ptr(), 39, misaligned), -1);
        assert_eq!(out, [123.; 39]);
    }
}
