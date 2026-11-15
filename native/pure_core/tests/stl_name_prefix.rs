//! Independently captured original CPython headers, all 512 Latin-1 prefix cases.
//! Both stable and owned raw C encoders must produce complete readable files.
use narwhal_pure_core::stl::*;
type Size = unsafe extern "C" fn(*const f64, usize, *const u8, usize, u8) -> i64;
type Encode = unsafe extern "C" fn(*const f64, usize, *const u8, usize, u8, *mut u8, usize) -> i32;
const CODECS: [(Size, Encode); 2] = [
    (narwhal_pure_core_stl_size, narwhal_pure_core_stl_encode),
    (
        narwhal_pure_core_stl_size_owned,
        narwhal_pure_core_stl_encode_owned,
    ),
];
const HEADERS: &[u8] = include_bytes!("fixtures/stl_prefix_headers.bin");
const COORDS: [f64; 9] = [0., 0., 0., 1., 0., 0., 0., 1., 0.];

fn expected_record() -> Vec<u8> {
    let mut data = 1u32.to_le_bytes().to_vec();
    for v in [0f32, 0., 1., 0., 0., 0., 1., 0., 0., 0., 1., 0.] {
        data.extend_from_slice(&v.to_le_bytes());
    }
    data.extend_from_slice(&0u16.to_le_bytes());
    data
}

#[test]
fn all_512_original_prefix_files_match_both_raw_encoders_and_round_trip() {
    assert_eq!(HEADERS.len(), 512 * 80);
    assert_eq!(
        HEADERS
            .iter()
            .fold(0xcbf29ce484222325u64, |h, b| (h ^ u64::from(*b))
                .wrapping_mul(0x100000001b3)),
        0x93a2f27783108cb5
    );
    let record = expected_record();
    for (case, keyword) in [b"solid triangle", b"SoLiD triangle"].iter().enumerate() {
        for prefix in 0..=255u8 {
            let mut name = vec![prefix];
            name.extend_from_slice(*keyword);
            let index = case * 256 + usize::from(prefix);
            let mut expected = HEADERS[index * 80..(index + 1) * 80].to_vec();
            expected.extend_from_slice(&record);
            for (size, encode) in CODECS {
                let needed = unsafe { size(COORDS.as_ptr(), 1, name.as_ptr(), name.len(), 1) };
                assert_eq!(needed, 134);
                let mut data = [0; 134];
                assert_eq!(
                    unsafe {
                        encode(
                            COORDS.as_ptr(),
                            1,
                            name.as_ptr(),
                            name.len(),
                            1,
                            data.as_mut_ptr(),
                            data.len(),
                        )
                    },
                    0
                );
                assert_eq!(
                    data.as_slice(),
                    expected,
                    "case={case} prefix={prefix:#04x}"
                );
                let mut diag = [99; 3];
                assert_eq!(
                    unsafe {
                        narwhal_pure_core_stl_file_size(
                            data.as_ptr(),
                            data.len(),
                            0,
                            diag.as_mut_ptr(),
                        )
                    },
                    9,
                    "case={case} prefix={prefix:#04x}"
                );
                assert_eq!(diag, [0; 3]);
                let mut out = [123.; 9];
                assert_eq!(
                    unsafe {
                        narwhal_pure_core_stl_file_decode(
                            data.as_ptr(),
                            data.len(),
                            0,
                            out.as_mut_ptr(),
                            out.len(),
                        )
                    },
                    0
                );
                assert_eq!(out, COORDS);
                assert_eq!(
                    unsafe { narwhal_pure_core_stl_decode_size(data.as_ptr(), data.len()) },
                    9
                );
                assert_eq!(
                    unsafe {
                        narwhal_pure_core_stl_decode(
                            data.as_ptr(),
                            data.len(),
                            out.as_mut_ptr(),
                            out.len(),
                        )
                    },
                    0
                );
                assert_eq!(out, COORDS);
            }
        }
    }
}

#[test]
fn lowercase_tab_solid_header_cannot_silently_lose_a_facet() {
    let name = b"\tsolid triangle";
    for encode in [
        narwhal_pure_core_stl_encode,
        narwhal_pure_core_stl_encode_owned,
    ] {
        let mut data = [0; 134];
        assert_eq!(
            unsafe {
                encode(
                    COORDS.as_ptr(),
                    1,
                    name.as_ptr(),
                    name.len(),
                    1,
                    data.as_mut_ptr(),
                    data.len(),
                )
            },
            0
        );
        let mut out = [123.; 9];
        // Directly check the legacy FILE detector, not binary-first raw decode.
        assert_eq!(
            unsafe {
                narwhal_pure_core_stl_file_decode(
                    data.as_ptr(),
                    data.len(),
                    0,
                    out.as_mut_ptr(),
                    out.len(),
                )
            },
            0
        );
        assert_eq!(out, COORDS);
        let mut diag = [0; 3];
        assert_eq!(
            unsafe {
                narwhal_pure_core_stl_file_size(data.as_ptr(), data.len(), 0, diag.as_mut_ptr())
            },
            9
        );
    }
}
