// Independently captured original CPython 3.13.13 Linux x86_64 helper answers.
// Archived original interpolate.py SHA256:
// 0ed2085fdd83bc93c20a86dc88aef028418a87811166f3115cd19d41b1bc03cf
// 2465 fixed cases, seed 21017: all ten helpers, degrees 1/3/n-1, endpoint
// extrapolation, arbitrary 1/2/3/5-wide RHS, first-equal pivots and signed zeros.
// Original exception captures include singular LU and adjacent duplicates.
// No executable Python oracle or native-generated expectation is distributed.
// New helper corpus is Linux-captured; Windows CRT/libm qualification must be
// measured independently before activating this new ABI there. Existing
// interpolate_golden.rs's Linux/Windows corpus is deliberately unchanged.
const CAPTURE: &str = include_str!("fixtures/interpolate_helpers_linux.txt");

use narwhal_pure_core::interpolate::{
    narwhal_pure_core_interpolate_helper_v1 as helper, InterpolateHelperRequest as Request,
};
fn floats(s: &str) -> Vec<f64> {
    if s.is_empty() {
        return vec![];
    }
    s.split(',')
        .map(|v| f64::from_bits(u64::from_str_radix(v, 16).unwrap()))
        .collect()
}
#[test]
fn independently_captured_helpers_exact_bits() {
    assert_eq!(
        CAPTURE
            .bytes()
            .fold(0xcbf29ce484222325u64, |h, b| (h ^ u64::from(b))
                .wrapping_mul(0x100000001b3)),
        0xf46c4e1697239a8d
    );
    let mut count = 0;
    for (line, row) in CAPTURE.lines().filter(|s| !s.is_empty()).enumerate() {
        let v: Vec<_> = row.split('|').collect();
        let data = floats(v[8]);
        let expected = floats(v[9]);
        let request = Request {
            operation: v[0].parse().unwrap(),
            rows: v[1].parse().unwrap(),
            columns: v[2].parse().unwrap(),
            degree: v[3].parse().unwrap(),
            index: v[4].parse().unwrap(),
            parameter: f64::from_bits(u64::from_str_radix(v[5], 16).unwrap()),
            style: v[6].parse().unwrap(),
            data: data.as_ptr(),
            data_len: data.len(),
        };
        let mut output = vec![f64::from_bits(0x7ff8_0000_0000_0123); expected.len() + 2];
        let (mut used, mut detail) = (99, 99);
        let code = unsafe {
            helper(
                &request,
                output.as_mut_ptr(),
                output.len(),
                &mut used,
                &mut detail,
            )
        };
        assert_eq!(
            code,
            v[7].parse::<i32>().unwrap(),
            "case {line} op {}",
            request.operation
        );
        if code == 0 {
            assert_eq!(used as usize, expected.len(), "case {line}");
            assert_eq!(
                output[..expected.len()]
                    .iter()
                    .map(|v| v.to_bits())
                    .collect::<Vec<_>>(),
                expected.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
                "case {line} op {}",
                request.operation
            );
        } else {
            assert_eq!(used, 0);
        }
        assert!(output[expected.len()..]
            .iter()
            .all(|v| v.to_bits() == 0x7ff8_0000_0000_0123));
        count += 1;
    }
    assert_eq!(count, 2465);
}

fn request(data: &[f64]) -> Request {
    Request {
        operation: 6,
        style: 0,
        rows: 2,
        columns: 0,
        degree: 0,
        index: 0,
        parameter: 0.0,
        data: data.as_ptr(),
        data_len: data.len(),
    }
}
#[test]
fn helper_raw_metadata_is_transactional() {
    let data = [1.0, 2.0, 3.0, 5.0];
    let mut req = request(&data);
    let mut out = [123.0; 8];
    let (mut used, mut detail) = (99, 99);
    unsafe {
        assert_eq!(
            helper(&req, out.as_mut_ptr(), 5, &mut used, &mut detail),
            -3
        );
        assert_eq!((used, detail), (0, 0));
        assert_eq!(out, [123.0; 8]);
        assert_eq!(helper(&req, out.as_mut_ptr(), 8, &mut used, &mut used), -2);
        assert_eq!(
            helper(
                std::ptr::null(),
                out.as_mut_ptr(),
                8,
                &mut used,
                &mut detail
            ),
            -1
        );
        assert_eq!(
            helper(&req, std::ptr::null_mut(), 8, &mut used, &mut detail),
            -1
        );
        req.data_len = usize::MAX;
        assert_eq!(
            helper(&req, out.as_mut_ptr(), 8, &mut used, &mut detail),
            -1
        );
        req = request(&data);
        req.rows = u64::MAX;
        assert_eq!(
            helper(&req, out.as_mut_ptr(), 8, &mut used, &mut detail),
            -3
        );
        req = request(&data);
        req.data = std::ptr::without_provenance::<f64>(1);
        assert_eq!(
            helper(&req, out.as_mut_ptr(), 8, &mut used, &mut detail),
            -1
        );
        req = request(&out[..4]);
        assert_eq!(
            helper(&req, out.as_mut_ptr(), 8, &mut used, &mut detail),
            -2
        );
        assert_eq!(out, [123.0; 8]);
        req = request(&data);
        assert_eq!(
            helper(
                &req,
                out.as_mut_ptr(),
                8,
                out.as_mut_ptr().cast(),
                &mut detail
            ),
            -2
        );
        assert_eq!(out, [123.0; 8]);
    }
}
#[test]
fn helper_invalid_shapes_and_named_failures() {
    let mut out = [123.0; 16];
    let (mut used, mut detail) = (99, 99);
    for (op, n, dim, p, index, data, want) in [
        (0, 2, 0, 0, 0, vec![1.0; 4], -2),
        (6, 2, 0, 0, 0, vec![1.0; 3], -2),
        (6, 2, 0, 0, 0, vec![1.0, 2.0, 2.0, 4.0], -4),
        (
            7,
            2,
            1,
            0,
            0,
            vec![1.0, 0.0, 0.0, 1.0, -1.0, 1.0, 2.0, 3.0],
            -2,
        ),
        (
            7,
            2,
            1,
            0,
            0,
            vec![1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0],
            -6,
        ),
        (10, 3, 1, 0, 0, vec![0.0; 12], -6),
        (2, 3, 0, 0, 0, vec![0.0, 0.5, 1.0], -6),
        (4, 0, 0, 3, -1, vec![0.0; 8], -2),
        (3, 3, 0, 2, 0, vec![1.0, 0.0, 0.0, 1.0, 1.0, 1.0], -2),
        (1, 2, 0, 0, 0, vec![f64::NAN; 6], -2),
    ] {
        let req = Request {
            operation: op,
            style: 0,
            rows: n,
            columns: dim,
            degree: p,
            index,
            parameter: 0.5,
            data: data.as_ptr(),
            data_len: data.len(),
        };
        let code = unsafe { helper(&req, out.as_mut_ptr(), out.len(), &mut used, &mut detail) };
        assert_eq!(code, want, "op {op}");
        assert_eq!(used, 0);
        assert_eq!(out, [123.0; 16]);
    }
    let data = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0];
    let req = Request {
        operation: 1,
        style: 1,
        rows: 3,
        columns: 0,
        degree: 0,
        index: 0,
        parameter: 0.0,
        data: data.as_ptr(),
        data_len: data.len(),
    };
    assert_eq!(
        unsafe { helper(&req, out.as_mut_ptr(), out.len(), &mut used, &mut detail) },
        -5
    );
    assert_eq!((used, detail), (0, 1));
    assert_eq!(out, [123.0; 16]);
}

#[test]
fn helper_empty_and_maximum_closed_metadata() {
    let req = request(&[]);
    let req = Request { rows: 0, ..req };
    let (mut used, mut detail) = (99, 99);
    assert_eq!(
        unsafe { helper(&req, std::ptr::null_mut(), 0, &mut used, &mut detail) },
        0
    );
    assert_eq!((used, detail), (0, 0));
    let data = vec![0.0; 2049];
    let req = Request {
        operation: 9,
        rows: 2049,
        degree: 3,
        data: data.as_ptr(),
        data_len: data.len(),
        ..req
    };
    let mut out = vec![123.0; 2057];
    assert_eq!(
        unsafe { helper(&req, out.as_mut_ptr(), out.len(), &mut used, &mut detail) },
        0
    );
    assert_eq!(used, 2055);
    assert_eq!(&out[2055..], &[123.0, 123.0]);
}

#[test]
fn helper_bad_spans_leave_all_storage_untouched() {
    let data = [1.0, 0.0, 0.0, 1.0];
    let mut req = request(&data);
    let mut out = [123.0; 8];
    let (mut used, mut detail) = (99, 99);
    unsafe {
        assert_eq!(
            helper(&req, std::ptr::null_mut(), 8, &mut used, &mut detail),
            -1
        );
        assert_eq!((used, detail), (99, 99));
        assert_eq!(
            helper(&req, out.as_mut_ptr(), usize::MAX, &mut used, &mut detail),
            -1
        );
        assert_eq!((used, detail), (99, 99));
        req.data = (usize::MAX - 7) as *const f64;
        assert_eq!(
            helper(&req, out.as_mut_ptr(), 8, &mut used, &mut detail),
            -1
        );
        assert_eq!((used, detail), (99, 99));
        req = request(&data);
        assert_eq!(helper(&req, out.as_mut_ptr(), 8, &mut used, &mut used), -2);
        assert_eq!(used, 99);
        assert_eq!(
            helper(
                &req,
                out.as_mut_ptr(),
                8,
                out.as_mut_ptr().cast(),
                &mut detail
            ),
            -2
        );
        assert_eq!(out, [123.0; 8]);
        assert_eq!(detail, 99);
        let unaligned = out.as_mut_ptr().cast::<u8>().add(1).cast::<f64>();
        assert_eq!(helper(&req, unaligned, 7, &mut used, &mut detail), -1);
        assert_eq!((used, detail), (99, 99));
        assert_eq!(out, [123.0; 8]);
    }
}
