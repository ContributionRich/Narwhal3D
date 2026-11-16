//! Original reader captures, not writer output as a reader oracle.
use narwhal_pure_core::dxf::*;
const CORPUS: &[u8] = include_bytes!("fixtures/dxf_reader24/corpus.bin");
fn take<'a>(b: &mut &'a [u8], n: usize) -> &'a [u8] {
    let (head, tail) = b.split_at(n);
    *b = tail;
    head
}
fn u32v(b: &mut &[u8]) -> u32 {
    u32::from_le_bytes(take(b, 4).try_into().unwrap())
}
fn u64v(b: &mut &[u8]) -> usize {
    u64::from_le_bytes(take(b, 8).try_into().unwrap()) as usize
}

#[test]
fn reader24_all_independent_original_results_bits_order_and_errors() {
    assert_eq!(narwhal_pure_core_dxf_reader24_abi_version(), 1);
    let mut bytes = CORPUS;
    assert_eq!(take(&mut bytes, 8), b"DXFCAP24");
    let count = u32v(&mut bytes);
    assert_eq!(count, 606);
    for _ in 0..count {
        let n = u32v(&mut bytes) as usize;
        let name = std::str::from_utf8(take(&mut bytes, n)).unwrap();
        let limit = u32v(&mut bytes);
        let n = u64v(&mut bytes);
        let input = take(&mut bytes, n);
        let n = u64v(&mut bytes);
        let expected = take(&mut bytes, n);
        let code = i64::from_le_bytes(take(&mut bytes, 8).try_into().unwrap());
        let mut diag = DxfReader24Diagnostic::default();
        let mut written = usize::MAX;
        let mut output = vec![0xa5; expected.len().max(1024)];
        let rc = unsafe {
            narwhal_pure_core_dxf_reader24_decode(
                input.as_ptr(),
                input.len(),
                limit,
                output.as_mut_ptr(),
                output.len(),
                &mut written,
                &mut diag,
            )
        };
        assert_eq!(
            rc as i64,
            code,
            "{name}: diag={:?}",
            (diag.code, diag.start, diag.len, diag.detail)
        );
        if code == 0 {
            assert_eq!(written, expected.len(), "{name}");
            assert_eq!(&output[..written], expected, "{name}");
            assert_eq!(diag.code, 0);
        } else {
            assert_eq!(written, usize::MAX, "{name}");
            assert!(output.iter().all(|v| *v == 0xa5), "{name}");
            assert_eq!(diag.code, code, "{name}");
        }
    }
    assert!(bytes.is_empty());
}

#[test]
fn reader24_retry_is_atomic_and_the_exact_capacity_is_sufficient() {
    let input = b"0\nSECTION\n2\nENTITIES\n0\nLWPOLYLINE\n10\n-0\n20\n1\n0\nENDSEC\n";
    let mut diag = DxfReader24Diagnostic::default();
    let mut written = usize::MAX;
    let mut tiny = [0xa5; 8];
    let rc = unsafe {
        narwhal_pure_core_dxf_reader24_decode(
            input.as_ptr(),
            input.len(),
            4300,
            tiny.as_mut_ptr(),
            tiny.len(),
            &mut written,
            &mut diag,
        )
    };
    assert_eq!(rc, 1);
    assert_eq!(diag.code, 1);
    assert!(diag.required > tiny.len());
    assert_eq!(written, usize::MAX);
    assert_eq!(tiny, [0xa5; 8]);
    let mut exact = vec![0; diag.required];
    assert_eq!(
        unsafe {
            narwhal_pure_core_dxf_reader24_decode(
                input.as_ptr(),
                input.len(),
                4300,
                exact.as_mut_ptr(),
                exact.len(),
                &mut written,
                &mut diag,
            )
        },
        0
    );
    assert_eq!(written, exact.len());
    assert_eq!(&exact[..8], b"DXFR24\0\x01");
}

#[test]
fn reader24_checked_spans_resources_and_null_slots_are_explicit() {
    let mut diag = DxfReader24Diagnostic::default();
    let mut written = usize::MAX;
    let mut output = [0xa5; 1024];
    unsafe {
        assert_eq!(
            narwhal_pure_core_dxf_reader24_decode(
                std::ptr::null(),
                1,
                4300,
                output.as_mut_ptr(),
                output.len(),
                &mut written,
                &mut diag
            ),
            -1
        );
        assert_eq!(
            narwhal_pure_core_dxf_reader24_decode(
                std::ptr::dangling(),
                usize::MAX,
                4300,
                output.as_mut_ptr(),
                output.len(),
                &mut written,
                &mut diag
            ),
            -3
        );
        assert_eq!(
            narwhal_pure_core_dxf_reader24_decode(
                std::ptr::null(),
                0,
                4300,
                output.as_mut_ptr(),
                output.len(),
                std::ptr::null_mut(),
                &mut diag
            ),
            -1
        );
        assert_eq!(
            narwhal_pure_core_dxf_reader24_decode(
                std::ptr::null(),
                0,
                4300,
                output.as_mut_ptr(),
                output.len(),
                &mut written,
                std::ptr::null_mut()
            ),
            -1
        );
        assert_eq!(
            narwhal_pure_core_dxf_reader24_decode(
                std::ptr::null(),
                0,
                4300,
                std::ptr::null_mut(),
                1024,
                &mut written,
                &mut diag
            ),
            -1
        );
    }
    assert_eq!(written, usize::MAX);
    assert!(output.iter().all(|b| *b == 0xa5));
}

#[test]
fn reader24_numeric_resource_limits_are_explicit_and_atomic() {
    let integer = format!("9\n$INSUNITS\n70\n{}\n", "1".repeat(16_385));
    let float = format!(
        "0\nSECTION\n2\nENTITIES\n0\nLINE\n10\n{}\n",
        "1".repeat(1_048_577)
    );
    for (input, limit, code) in [
        (integer.as_bytes(), 0, -3),
        (integer.as_bytes(), 4300, -115),
        (float.as_bytes(), 4300, -3),
    ] {
        let mut output = [0xa5; 1024];
        let mut written = usize::MAX;
        let mut diagnostic = DxfReader24Diagnostic::default();
        assert_eq!(
            unsafe {
                narwhal_pure_core_dxf_reader24_decode(
                    input.as_ptr(),
                    input.len(),
                    limit,
                    output.as_mut_ptr(),
                    output.len(),
                    &mut written,
                    &mut diagnostic,
                )
            },
            code
        );
        assert_eq!(diagnostic.code, code as i64);
        assert_eq!(written, usize::MAX);
        assert!(output.iter().all(|b| *b == 0xa5));
    }
}
