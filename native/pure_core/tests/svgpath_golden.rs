//! Lossless answers captured from the original independent Python oracle.
//! No parser, arc algorithm, native-generated answers, or extra dependencies.
use narwhal_pure_core::svgpath::{
    narwhal_pure_core_svgpath_owned as owned, narwhal_pure_core_svgpath_owned_version, PathError,
};
use std::ptr::null_mut;

const GOLDEN: &str = include_str!("fixtures/svgpath_golden.txt");
fn nums(s: &str) -> Vec<usize> {
    s.split(',')
        .filter(|s| !s.is_empty())
        .map(|s| s.parse().unwrap())
        .collect()
}
fn hex(s: &str) -> Vec<u64> {
    s.split(',')
        .filter(|s| !s.is_empty())
        .map(|s| u64::from_str_radix(s, 16).unwrap())
        .collect()
}
fn fields(e: &PathError) -> [u64; 7] {
    [
        e.kind as u64,
        e.command as u64,
        e.expected as u64,
        e.actual as u64,
        e.start as u64,
        e.end as u64,
        e.value.to_bits(),
    ]
}
unsafe fn call(
    text: &[u32],
    p: &mut [f64],
    o: &mut [usize],
    f: &mut [u8],
    counts: &mut [usize; 2],
    error: &mut PathError,
) -> i32 {
    unsafe {
        owned(
            text.as_ptr(),
            text.len(),
            p.as_mut_ptr(),
            p.len(),
            o.as_mut_ptr(),
            o.len(),
            f.as_mut_ptr(),
            f.len(),
            counts.as_mut_ptr(),
            error,
        )
    }
}

#[test]
fn original_python_all_cases_bits_errors_and_capacity() {
    assert_eq!(narwhal_pure_core_svgpath_owned_version(), 14);
    let mut groups = std::collections::BTreeMap::new();
    for line in GOLDEN.lines() {
        let row: Vec<_> = line.split('|').collect();
        assert_eq!(row.len(), 7);
        *groups.entry(row[1]).or_insert(0usize) += 1;
        let text: Vec<u32> = hex(row[2]).into_iter().map(|v| v as u32).collect();
        let mut counts = [usize::MAX; 2];
        let mut error = PathError {
            kind: 99,
            ..PathError::default()
        };
        let status = unsafe { call(&text, &mut [], &mut [], &mut [], &mut counts, &mut error) };
        if row[3] == "ERR" {
            assert_eq!(row[4], "ValueError", "{}", row[0]);
            let v: Vec<_> = row[6].split(',').collect();
            let expected = [
                v[0].parse().unwrap(),
                v[1].parse().unwrap(),
                v[2].parse().unwrap(),
                v[3].parse().unwrap(),
                v[4].parse().unwrap(),
                v[5].parse().unwrap(),
                u64::from_str_radix(v[6], 16).unwrap(),
            ];
            assert_eq!(status, 2, "case {}", row[0]);
            assert_eq!(counts, [0, 0], "case {}", row[0]);
            assert_eq!(fields(&error), expected, "case {}", row[0]);
            // Typed errors are independent of supplied output capacity and never write geometry.
            let (mut p, mut o, mut f) = ([123.0; 8], [123; 1], [123; 1]);
            assert_eq!(
                unsafe { call(&text, &mut p, &mut o, &mut f, &mut counts, &mut error) },
                2
            );
            assert_eq!(fields(&error), expected);
            assert_eq!(counts, [0, 0]);
            assert_eq!(p, [123.0; 8]);
            assert_eq!(o, [123]);
            assert_eq!(f, [123]);
            continue;
        }
        assert_eq!(row[3], "OK");
        let offsets = nums(row[4]);
        let flags: Vec<u8> = nums(row[5]).into_iter().map(|v| v as u8).collect();
        let expected = hex(row[6]);
        let sizes = [expected.len(), offsets.len()];
        assert_eq!(counts, sizes, "case {}", row[0]);
        assert_eq!(
            status,
            if sizes == [0, 0] { 0 } else { 1 },
            "case {}",
            row[0]
        );
        assert_eq!(fields(&error), [0; 7]);
        let mut points = vec![123.0; sizes[0]];
        let mut out = vec![123; sizes[1]];
        let mut closed = vec![123; sizes[1]];
        // Each independently insufficient buffer must leave ALL three buffers untouched.
        for which in 0..3 {
            if (which == 0 && points.is_empty()) || (which != 0 && out.is_empty()) {
                continue;
            }
            let pc = points.len() - usize::from(which == 0);
            let oc = out.len() - usize::from(which == 1);
            let fc = closed.len() - usize::from(which == 2);
            assert_eq!(
                unsafe {
                    call(
                        &text,
                        &mut points[..pc],
                        &mut out[..oc],
                        &mut closed[..fc],
                        &mut counts,
                        &mut error,
                    )
                },
                1,
                "case {}",
                row[0]
            );
            assert_eq!(counts, sizes);
            assert_eq!(fields(&error), [0; 7]);
            assert!(points.iter().all(|v| *v == 123.0));
            assert!(out.iter().all(|v| *v == 123));
            assert!(closed.iter().all(|v| *v == 123));
        }
        assert_eq!(
            unsafe {
                call(
                    &text,
                    &mut points,
                    &mut out,
                    &mut closed,
                    &mut counts,
                    &mut error,
                )
            },
            0,
            "case {}",
            row[0]
        );
        assert_eq!(counts, sizes);
        assert_eq!(fields(&error), [0; 7]);
        assert_eq!(
            points.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
            expected,
            "case {}",
            row[0]
        );
        assert_eq!(out, offsets, "case {}", row[0]);
        assert_eq!(closed, flags, "case {}", row[0]);
    }
    assert_eq!(groups.values().sum::<usize>(), 3124);
    assert_eq!(groups["test_generated_arcs_exact_bits"], 2000);
    assert_eq!(groups["test_generated_cubics_exact_bits"], 1000);
    assert_eq!(groups["test_generated_errors"], 62);
    assert_eq!(groups["test_standard"], 32);
    assert_eq!(groups["test_finite_extremes"], 4);
    assert_eq!(groups["valid"], 13);
    assert_eq!(groups["anchor"], 13);
}

fn parsed(text: &str) -> (Vec<f64>, Vec<usize>, Vec<u8>) {
    let text: Vec<u32> = text.chars().map(u32::from).collect();
    let mut counts = [0; 2];
    let mut error = PathError::default();
    let status = unsafe { call(&text, &mut [], &mut [], &mut [], &mut counts, &mut error) };
    assert!(status == 0 || status == 1);
    let (mut p, mut o, mut f) = (vec![0.0; counts[0]], vec![0; counts[1]], vec![0; counts[1]]);
    assert_eq!(
        unsafe { call(&text, &mut p, &mut o, &mut f, &mut counts, &mut error) },
        0
    );
    (p, o, f)
}

#[test]
fn transferred_structure_and_off_origin_circle_assertions() {
    let (p, o, f) = parsed("M 0 0 10 0 10 10");
    assert_eq!(o, [0]);
    assert_eq!(f, [0]);
    assert_eq!(p.len(), 16);
    assert_eq!(&p[..2], &[0.0, 0.0]);
    assert_eq!(&p[6..8], &[10.0, 0.0]);
    assert!((p[2] - 10.0 / 3.0).abs() < 1e-12);
    assert!((p[4] - 20.0 / 3.0).abs() < 1e-12);
    let (p, _, f) = parsed("M 0 0 L 10 0 L 10 10 Z");
    assert_eq!(f, [1]);
    assert_eq!(p.len(), 24);
    assert_eq!(&p[16..18], &[10.0, 10.0]);
    assert_eq!(&p[22..24], &[0.0, 0.0]);
    let (p, _, _) = parsed("M 1 0 A 1 1 0 0 1 0 1");
    assert_eq!(p.len(), 8);
    let cubic = |axis: usize, t: f64| {
        let u = 1.0 - t;
        u * u * u * p[axis]
            + 3.0 * u * u * t * p[axis + 2]
            + 3.0 * u * t * t * p[axis + 4]
            + t * t * t * p[axis + 6]
    };
    for t in [0.25, 0.5, 0.75] {
        let (x, y) = (cubic(0, t), cubic(1, t));
        assert!((x.hypot(y) - 1.0).abs() <= 3e-4);
        assert!(x > 0.3 && x < 1.0 && y > 0.3 && y < 1.0);
    }
    assert!(cubic(0, 0.5) > 0.5 && cubic(1, 0.5) > 0.5);
}

#[test]
fn abi_invalid_pointers_and_budget() {
    let text: Vec<u32> = "M0 0L2 3Z".chars().map(u32::from).collect();
    let mut counts = [0; 2];
    let mut error = PathError::default();
    let invoke = |t, n, p, pc, oc, fc, report, e| unsafe {
        owned(t, n, p, pc, null_mut(), oc, null_mut(), fc, report, e)
    };
    assert_eq!(
        invoke(
            std::ptr::null(),
            1,
            null_mut(),
            0,
            0,
            0,
            counts.as_mut_ptr(),
            &mut error
        ),
        -1
    );
    let bad = unsafe { text.as_ptr().cast::<u8>().add(1).cast::<u32>() };
    assert_eq!(
        invoke(bad, 1, null_mut(), 0, 0, 0, counts.as_mut_ptr(), &mut error),
        -1
    );
    assert_eq!(
        invoke(
            text.as_ptr(),
            text.len(),
            null_mut(),
            64 * (1 << 20) + 1,
            0,
            0,
            counts.as_mut_ptr(),
            &mut error
        ),
        -2
    );
    assert_eq!(
        invoke(
            text.as_ptr(),
            text.len(),
            null_mut(),
            (64 << 20) / 8,
            1,
            1,
            counts.as_mut_ptr(),
            &mut error
        ),
        -2
    );
    assert_eq!(
        invoke(
            text.as_ptr(),
            text.len(),
            text.as_ptr() as *mut f64,
            1,
            0,
            0,
            counts.as_mut_ptr(),
            &mut error
        ),
        -2
    );
    assert_eq!(
        invoke(
            text.as_ptr(),
            text.len(),
            null_mut(),
            0,
            0,
            0,
            null_mut(),
            &mut error
        ),
        -1
    );
}
