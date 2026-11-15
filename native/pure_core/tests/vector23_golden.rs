//! Original CPython 3.13 bytes captured BEFORE codec changes. No Python oracle
//! ships or executes here. 600 premeasurement + 144 numeric boundary + 6 title cases.
use narwhal_pure_core::vector_export::*;
use std::ptr;
const DATA: &str = include_str!("fixtures/vector23_golden.txt");
fn hex(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}
fn doubles(s: &str) -> Vec<f64> {
    if s.is_empty() {
        vec![]
    } else {
        s.split(',')
            .map(|s| f64::from_bits(u64::from_str_radix(s, 16).unwrap()))
            .collect()
    }
}
fn ints(s: &str) -> Vec<i64> {
    if s.is_empty() {
        vec![]
    } else {
        s.split(',').map(|v| v.parse().unwrap()).collect()
    }
}
fn ds(v: &[f64]) -> VectorDoublesV1 {
    VectorDoublesV1 {
        data: v.as_ptr(),
        len: v.len(),
    }
}
fn is(v: &[i64]) -> VectorIntsV1 {
    VectorIntsV1 {
        data: v.as_ptr(),
        len: v.len(),
    }
}
fn bs(v: &[u8]) -> VectorBytesV1 {
    VectorBytesV1 {
        data: v.as_ptr(),
        len: v.len(),
    }
}
fn fs(v: &[i32]) -> VectorFlagsV1 {
    VectorFlagsV1 {
        data: v.as_ptr(),
        len: v.len(),
    }
}
#[test]
fn independent_original_bytes_all_744_cases() {
    let fingerprint = DATA.bytes().fold(0xcbf29ce484222325u64, |h, b| {
        (h ^ u64::from(b)).wrapping_mul(0x100000001b3)
    });
    assert_eq!(fingerprint, 0x2e9aaa66a86dd39c); // replaced with the independently frozen fingerprint
    let mut count = 0;
    for (index, line) in DATA.lines().enumerate() {
        let f: Vec<_> = line.split('|').collect();
        assert_eq!(f.len(), 16);
        let page = doubles(f[3]);
        let unit_mm = doubles(f[4])[0];
        let unit = hex(f[6]);
        let title = hex(f[7]);
        let counts = ints(f[8]);
        let closed: Vec<i32> = ints(f[9]).iter().map(|v| *v as i32).collect();
        let rgb = doubles(f[10]);
        let widths = doubles(f[11]);
        let bz = doubles(f[12]);
        let lens = ints(f[13]);
        let layers = hex(f[14]);
        let expected = hex(f[15]);
        assert_eq!(counts.len(), f[1].parse::<usize>().unwrap());
        let req = VectorRequestV1 {
            size: std::mem::size_of::<VectorRequestV1>(),
            format: f[0].parse().unwrap(),
            digits: f[5].parse().unwrap(),
            page_w_mm: page[0],
            page_h_mm: page[1],
            unit_mm,
            segments: f[2].parse().unwrap(),
            beziers: ds(&bz),
            counts: is(&counts),
            closed: fs(&closed),
            rgb: ds(&rgb),
            widths: ds(&widths),
            layers: bs(&layers),
            layer_lengths: is(&lens),
            title: bs(&title),
            unit: bs(&unit),
            title_parts: is(&[]),
        };
        let mut handle = ptr::null_mut();
        unsafe {
            assert_eq!(
                narwhal_pure_core_vector_encode_v1(&req, &mut handle),
                0,
                "case {index}"
            );
            assert!(!handle.is_null());
            let len = narwhal_pure_core_vector_len_v1(handle);
            assert_eq!(len, expected.len(), "case {index}");
            let mut out = vec![165; len + 5];
            let mut written = 99;
            assert_eq!(
                narwhal_pure_core_vector_copy_v1(handle, out.as_mut_ptr(), len - 1, &mut written),
                -3
            );
            assert_eq!(written, 99);
            assert!(out.iter().all(|v| *v == 165));
            assert_eq!(
                narwhal_pure_core_vector_copy_v1(handle, out.as_mut_ptr(), out.len(), &mut written),
                0
            );
            narwhal_pure_core_vector_free_v1(handle);
            assert_eq!(written, len);
            assert_eq!(&out[..len], expected, "case {index}");
            assert_eq!(&out[len..], &[165; 5]);
        }
        count += 1;
    }
    assert_eq!(count, 744);
}
fn empty() -> VectorRequestV1 {
    VectorRequestV1 {
        size: std::mem::size_of::<VectorRequestV1>(),
        format: 0,
        digits: 4,
        page_w_mm: 210.,
        page_h_mm: 297.,
        unit_mm: 1.,
        segments: 0,
        beziers: ds(&[]),
        counts: is(&[]),
        closed: fs(&[]),
        rgb: ds(&[]),
        widths: ds(&[]),
        layers: bs(&[]),
        layer_lengths: is(&[]),
        title: bs(&[]),
        unit: bs(&[]),
        title_parts: is(&[]),
    }
}
#[test]
fn invalid_metadata_and_input_are_transactional() {
    unsafe {
        let mut req = empty();
        let mut handle = ptr::dangling_mut::<VectorEncodedV1>();
        assert_eq!(
            narwhal_pure_core_vector_encode_v1(ptr::null(), &mut handle),
            -1
        );
        assert_eq!(handle, ptr::dangling_mut());
        assert_eq!(
            narwhal_pure_core_vector_encode_v1(&req, ptr::null_mut()),
            -1
        );
        req.size = 0;
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -2);
        assert_eq!(handle, ptr::dangling_mut());
        req = empty();
        req.page_h_mm = f64::NAN;
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -4);
        assert!(handle.is_null());
        req = empty();
        req.format = 1;
        req.unit_mm = 0.;
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -5);
        assert!(handle.is_null());
        req = empty();
        req.digits = 4097;
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -3);
        assert!(handle.is_null());
        req = empty();
        req.beziers.len = usize::MAX;
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -1);
        req = empty();
        req.counts.len = 1_000_001;
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -3);
        req = empty();
        let counts = [0];
        let closed = [0];
        let rgb = [0., 0., 0.];
        let widths = [0.25];
        let lengths = [1];
        req.counts = is(&counts);
        req.closed = fs(&closed);
        req.rgb = ds(&rgb);
        req.widths = ds(&widths);
        req.layer_lengths = is(&lengths);
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -2);
        assert!(handle.is_null());
        req.layer_lengths = is(&[0]);
        req.rgb = VectorDoublesV1 {
            data: std::ptr::without_provenance::<f64>(1),
            len: 3,
        };
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -1);
        assert_eq!(narwhal_pure_core_vector_len_v1(ptr::null()), 0);
        narwhal_pure_core_vector_free_v1(ptr::null_mut());
    }
}

#[test]
fn copy_alias_and_encode_metadata_are_all_or_nothing() {
    unsafe {
        let mut req = empty();
        let mut handle = ptr::null_mut();
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), 0);
        let mut output = vec![165u8; narwhal_pure_core_vector_len_v1(handle) + 16];
        let original = output.clone();
        let mut written = 99;
        assert_eq!(
            narwhal_pure_core_vector_copy_v1(
                handle,
                output.as_mut_ptr(),
                output.len(),
                ptr::null_mut()
            ),
            -1
        );
        assert_eq!(output, original);
        assert_eq!(
            narwhal_pure_core_vector_copy_v1(
                handle,
                output.as_mut_ptr(),
                output.len(),
                output.as_mut_ptr().cast()
            ),
            -2
        );
        assert_eq!(output, original);
        assert_eq!(
            narwhal_pure_core_vector_copy_v1(handle, output.as_mut_ptr(), usize::MAX, &mut written),
            -1
        );
        assert_eq!(written, 99);
        assert_eq!(output, original);
        narwhal_pure_core_vector_free_v1(handle);
        let address = (&mut req as *mut VectorRequestV1).cast::<*mut VectorEncodedV1>();
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, address), -2);
        assert_eq!(req.size, std::mem::size_of::<VectorRequestV1>());
        let mut slot = ptr::dangling_mut::<VectorEncodedV1>();
        req.title = VectorBytesV1 {
            data: (&slot as *const *mut VectorEncodedV1).cast(),
            len: std::mem::size_of_val(&slot),
        };
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut slot), -2);
        assert_eq!(slot, ptr::dangling_mut());
    }
}

#[test]
fn owned_result_outlives_input_and_other_results() {
    unsafe {
        let mut title = b"before".to_vec();
        let mut req = empty();
        req.title = bs(&title);
        let mut first = ptr::null_mut();
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut first), 0);
        title[0] = b'X';
        let mut second = ptr::null_mut();
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut second), 0);
        for (handle, text) in [
            (first, b"(before)".as_slice()),
            (second, b"(Xefore)".as_slice()),
        ] {
            let n = narwhal_pure_core_vector_len_v1(handle);
            let mut out = vec![0; n];
            let mut count = 0;
            assert_eq!(
                narwhal_pure_core_vector_copy_v1(handle, out.as_mut_ptr(), n, &mut count),
                0
            );
            assert!(out.windows(text.len()).any(|w| w == text));
            narwhal_pure_core_vector_free_v1(handle);
        }
    }
}

#[test]
fn raw_nonfinite_text_flags_and_overflow_refuse_without_handles() {
    unsafe {
        let mut req = empty();
        let mut handle = ptr::null_mut();
        req.page_w_mm = 1e308;
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -4);
        assert!(handle.is_null());
        req = empty();
        req.format = 2;
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -2);
        req = empty();
        req.format = 1;
        req.unit = bs(&[255]);
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -2);
        let counts = [0, 0];
        let flags = [0, 0];
        let rgb = [0.0; 6];
        let widths = [0.25; 2];
        let lens = [1, 1];
        req.unit = bs(&[]);
        req.counts = is(&counts);
        req.closed = fs(&flags);
        req.rgb = ds(&rgb);
        req.widths = ds(&widths);
        req.layer_lengths = is(&lens);
        req.layers = bs(&[0xc3, 0xa9]);
        assert_eq!(
            narwhal_pure_core_vector_encode_v1(&req, &mut handle),
            -2,
            "individual layer boundaries must be UTF8"
        );
        let lens = [0, 0];
        req.layer_lengths = is(&lens);
        req.layers = bs(&[]);
        req.closed = fs(&[0, 7]);
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -2);
        req.closed = fs(&flags);
        let rgb = [f64::INFINITY; 6];
        req.rgb = ds(&rgb);
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), -4);
        assert!(handle.is_null());
    }
}

#[test]
fn copied_document_validation_is_native_and_bounded() {
    unsafe {
        let req = empty();
        let mut handle = ptr::null_mut();
        assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), 0);
        let n = narwhal_pure_core_vector_len_v1(handle);
        let mut out = vec![0; n];
        let mut count = 0;
        assert_eq!(
            narwhal_pure_core_vector_copy_v1(handle, out.as_mut_ptr(), n, &mut count),
            0
        );
        narwhal_pure_core_vector_free_v1(handle);
        assert_eq!(narwhal_pure_core_vector_validate_v1(0, out.as_ptr(), n), 0);
        let marker = b"startxref\n";
        let index = out.windows(marker.len()).position(|w| w == marker).unwrap() + marker.len();
        out[index] = b'x';
        assert_eq!(narwhal_pure_core_vector_validate_v1(0, out.as_ptr(), n), -6);
        let svg = b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<svg ></svg>\n";
        assert_eq!(
            narwhal_pure_core_vector_validate_v1(1, svg.as_ptr(), svg.len()),
            0
        );
        let mut corrupt = svg.to_vec();
        corrupt[svg.len() - 8] = 255;
        assert_eq!(
            narwhal_pure_core_vector_validate_v1(1, corrupt.as_ptr(), corrupt.len()),
            -6
        );
        assert_eq!(narwhal_pure_core_vector_validate_v1(1, ptr::null(), 1), -1);
        assert_eq!(
            narwhal_pure_core_vector_validate_v1(1, ptr::null(), usize::MAX),
            -3
        );
    }
}

#[test]
fn independent_original_nonstring_title_atoms() {
    let data = include_str!("fixtures/vector23_titles.txt");
    let mut count = 0;
    for row in data.lines() {
        let f: Vec<_> = row.split('|').collect();
        let lengths = ints(f[0]);
        let title = hex(f[1]);
        let expected = hex(f[2]);
        let mut req = empty();
        req.title = bs(&title);
        req.title_parts = is(&lengths);
        unsafe {
            let mut handle = ptr::null_mut();
            assert_eq!(narwhal_pure_core_vector_encode_v1(&req, &mut handle), 0);
            let n = narwhal_pure_core_vector_len_v1(handle);
            assert_eq!(n, expected.len());
            let mut out = vec![0; n];
            let mut written = 0;
            assert_eq!(
                narwhal_pure_core_vector_copy_v1(handle, out.as_mut_ptr(), n, &mut written),
                0
            );
            narwhal_pure_core_vector_free_v1(handle);
            assert_eq!(out, expected);
        }
        count += 1;
    }
    assert_eq!(count, 6);
}

#[test]
fn empty_stroke_width_precision_is_in_preallocation_budget() {
    // Live bounded input buffers; reject before constructing hundreds of MiB of
    // high-precision widths. Empty strokes still emit their widths.
    let n = 100_000;
    let counts = vec![0; n];
    let closed = vec![0; n];
    let rgb = vec![0.0; 3 * n];
    let widths = vec![0.25; n];
    let lengths = vec![0; n];
    let mut req = empty();
    req.format = 1;
    req.digits = 4096;
    req.counts = is(&counts);
    req.closed = fs(&closed);
    req.rgb = ds(&rgb);
    req.widths = ds(&widths);
    req.layer_lengths = is(&lengths);
    let mut handle = std::ptr::null_mut();
    assert_eq!(
        unsafe { narwhal_pure_core_vector_encode_v1(&req, &mut handle) },
        -3
    );
    assert!(handle.is_null());
}
