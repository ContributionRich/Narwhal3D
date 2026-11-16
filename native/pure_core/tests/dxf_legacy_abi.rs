//! Independent legacy C ABI compatibility, separate from required reader24.
use narwhal_pure_core::dxf::{
    narwhal_pure_core_dxf_decode as decode, narwhal_pure_core_dxf_decode_size as size,
};

const ANALYTIC: &[u8] = include_bytes!("fixtures/dxf_legacy_abi/analytic.dxf");
const PACKED: &[u8] = include_bytes!("fixtures/dxf_legacy_abi/analytic.packed");
const EMPTY: &[u8] = include_bytes!("fixtures/dxf_legacy_abi/empty.packed");

#[test]
fn legacy_analytic_entities_metadata_and_float_bits_match_independent_bytes() {
    assert_eq!(PACKED.len(), 898);
    assert_eq!(
        unsafe { size(ANALYTIC.as_ptr(), ANALYTIC.len()) },
        PACKED.len() as i64
    );
    let mut guarded = vec![0xa5; PACKED.len() + 16];
    assert_eq!(
        unsafe {
            decode(
                ANALYTIC.as_ptr(),
                ANALYTIC.len(),
                guarded.as_mut_ptr().add(8),
                PACKED.len(),
            )
        },
        0
    );
    assert_eq!(&guarded[8..8 + PACKED.len()], PACKED);
    assert!(guarded[..8]
        .iter()
        .chain(&guarded[8 + PACKED.len()..])
        .all(|b| *b == 0xa5));
}

#[test]
fn legacy_requires_exact_capacity_and_does_not_publish_partial_output() {
    for capacity in [0, PACKED.len() - 1, PACKED.len() + 1] {
        let mut output = vec![0xa5; PACKED.len() + 1];
        assert_eq!(
            unsafe {
                decode(
                    ANALYTIC.as_ptr(),
                    ANALYTIC.len(),
                    output.as_mut_ptr(),
                    capacity,
                )
            },
            -3
        );
        assert!(output.iter().all(|b| *b == 0xa5));
    }
    assert_eq!(
        unsafe {
            decode(
                ANALYTIC.as_ptr(),
                ANALYTIC.len(),
                std::ptr::null_mut(),
                PACKED.len(),
            )
        },
        -1
    );
}

#[test]
fn legacy_bad_files_return_exact_codes_without_writing() {
    let cases: &[(&[u8], i32)] = &[
        (b"AutoCAD Binary DXF\r\n\x1a\0", -21),
        (b"9\n$ACADVER\n1\nACBAD!\n", -21),
        (b"0\nSECTION\n2\n", -20),
        (b"foo\nbar\n", -22),
        (
            b"0\nSECTION\n2\nENTITIES\n0\nLINE\n10\nbad\n0\nENDSEC\n",
            -22,
        ),
        (b"0\nSECTION\n2\nENTITIES\n0\nLINE\n10\n1\n0\nENDSEC\n", -22),
    ];
    for (input, expected) in cases {
        assert_eq!(
            unsafe { size(input.as_ptr(), input.len()) },
            *expected as i64
        );
        let mut output = [0xa5; 1024];
        assert_eq!(
            unsafe {
                decode(
                    input.as_ptr(),
                    input.len(),
                    output.as_mut_ptr(),
                    output.len(),
                )
            },
            *expected
        );
        assert!(output.iter().all(|b| *b == 0xa5));
    }
}

#[test]
fn legacy_null_and_nonnull_empty_inputs_retain_their_distinct_contracts() {
    let mut output = [0xa5; 40];
    assert_eq!(unsafe { size(std::ptr::null(), 0) }, 0);
    assert_eq!(
        unsafe { decode(std::ptr::null(), 0, output.as_mut_ptr(), output.len()) },
        0
    );
    assert_eq!(output, [0xa5; 40]);
    assert_eq!(unsafe { size(b"".as_ptr(), 0) }, 40);
    assert_eq!(
        unsafe { decode(b"".as_ptr(), 0, output.as_mut_ptr(), output.len()) },
        0
    );
    assert_eq!(&output, EMPTY);
    assert_eq!(
        unsafe { decode(b"".as_ptr(), 0, std::ptr::null_mut(), 0) },
        -1
    );
}

#[test]
fn legacy_null_positive_and_overflow_spans_are_refused_without_writing() {
    let mut output = [0xa5; 1024];
    assert_eq!(unsafe { size(std::ptr::null(), 1) }, -1);
    assert_eq!(
        unsafe { decode(std::ptr::null(), 1, output.as_mut_ptr(), output.len()) },
        -1
    );
    assert_eq!(unsafe { size(std::ptr::dangling(), usize::MAX) }, -3);
    assert_eq!(
        unsafe {
            decode(
                std::ptr::dangling(),
                usize::MAX,
                output.as_mut_ptr(),
                output.len(),
            )
        },
        -3
    );
    assert!(output.iter().all(|b| *b == 0xa5));
    // Overflow is checked before any destination dereference.
    assert_eq!(
        unsafe {
            decode(
                ANALYTIC.as_ptr(),
                ANALYTIC.len(),
                usize::MAX as *mut u8,
                PACKED.len(),
            )
        },
        -3
    );
}
