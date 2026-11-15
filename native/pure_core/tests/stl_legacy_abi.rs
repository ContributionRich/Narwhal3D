//! Stable ABI-1 tests transferred from Python compatibility-only adapters.
//! Independent hand-authored facets/expected topology, never a retired oracle.
use narwhal_pure_core::stl::*;
const TRI: [f64; 9] = [0., 0., 0., 1., 0., 0., 0., 1., 0.];
const ASCII: &[u8] = b"solid test\n  facet normal 0 0 1\n  outer loop\n    vertex 0 0 0\n    vertex 1 0 0\n    vertex 0 1 0\n  endloop\n  endfacet\nendsolid test\n";
const CUBE: [[f64; 9]; 12] = [
    [0.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0],
    [0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0],
    [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
    [1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
    [1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0],
    [1.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    [0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
    [1.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0],
    [0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 1.0],
    [1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
];

fn binary(triangles: &[[f64; 9]], solid: bool) -> Vec<u8> {
    let mut data = vec![b' '; 80];
    let name = if solid {
        b"solid".as_slice()
    } else {
        b"test".as_slice()
    };
    data[..name.len()].copy_from_slice(name);
    data.extend_from_slice(&(triangles.len() as u32).to_le_bytes());
    for tri in triangles {
        for value in [0f32, 0., 1.] {
            data.extend_from_slice(&value.to_le_bytes());
        }
        for value in tri {
            data.extend_from_slice(&(*value as f32).to_le_bytes());
        }
        data.extend_from_slice(&0u16.to_le_bytes());
    }
    data
}
fn mesh(data: &[u8], vertices: &mut [f64], indices: &mut [u32], nv: &mut i64, nf: &mut i64) -> i32 {
    unsafe {
        narwhal_pure_core_stl_mesh_decode(
            data.as_ptr(),
            data.len(),
            vertices.as_mut_ptr(),
            vertices.len() / 3,
            indices.as_mut_ptr(),
            indices.len(),
            nv,
            nf,
        )
    }
}
#[test]
fn stable_encode_size_bytes_nan_and_capacity_contracts() {
    for (mode, expected) in [(1, binary(&[TRI], false)), (0, ASCII.to_vec())] {
        let name = b"test";
        assert_eq!(
            unsafe { narwhal_pure_core_stl_size(TRI.as_ptr(), 1, name.as_ptr(), name.len(), mode) },
            expected.len() as i64
        );
        let mut out = vec![77; expected.len()];
        assert_eq!(
            unsafe {
                narwhal_pure_core_stl_encode(
                    TRI.as_ptr(),
                    1,
                    name.as_ptr(),
                    name.len(),
                    mode,
                    out.as_mut_ptr(),
                    out.len(),
                )
            },
            0
        );
        assert_eq!(out, expected);
        out.fill(77);
        assert_eq!(
            unsafe {
                narwhal_pure_core_stl_encode(
                    TRI.as_ptr(),
                    1,
                    name.as_ptr(),
                    name.len(),
                    mode,
                    out.as_mut_ptr(),
                    out.len() - 1,
                )
            },
            -3
        );
        assert!(out.iter().all(|x| *x == 77));
    }
    for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let mut bad = TRI;
        bad[0] = value;
        assert_eq!(
            unsafe { narwhal_pure_core_stl_size(bad.as_ptr(), 1, b"test".as_ptr(), 4, 1) },
            -2
        );
        let mut out = [77; 134];
        assert_eq!(
            unsafe {
                narwhal_pure_core_stl_encode(
                    bad.as_ptr(),
                    1,
                    b"test".as_ptr(),
                    4,
                    1,
                    out.as_mut_ptr(),
                    out.len(),
                )
            },
            -2
        );
        assert_eq!(out, [77; 134]);
    }
}
#[test]
fn stable_decoder_retains_binary_first_detection_distinct_from_file_api() {
    for data in [binary(&[TRI], false), binary(&[TRI], true), ASCII.to_vec()] {
        assert_eq!(
            unsafe { narwhal_pure_core_stl_decode_size(data.as_ptr(), data.len()) },
            9
        );
        let mut out = [123.; 9];
        assert_eq!(
            unsafe {
                narwhal_pure_core_stl_decode(data.as_ptr(), data.len(), out.as_mut_ptr(), out.len())
            },
            0
        );
        assert_eq!(out, TRI);
        out.fill(123.);
        assert_eq!(
            unsafe { narwhal_pure_core_stl_decode(data.as_ptr(), data.len(), out.as_mut_ptr(), 8) },
            -3
        );
        assert_eq!(out, [123.; 9]);
    }
    let data = binary(&[TRI], true);
    let mut diag = [99; 3];
    assert_eq!(
        unsafe { narwhal_pure_core_stl_file_size(data.as_ptr(), data.len(), 0, diag.as_mut_ptr()) },
        0
    );
    assert_eq!(diag, [0; 3]);
    assert_eq!(
        unsafe {
            narwhal_pure_core_stl_file_decode(data.as_ptr(), data.len(), 0, std::ptr::null_mut(), 0)
        },
        0
    );
    assert_eq!(
        unsafe { narwhal_pure_core_stl_decode_size(std::ptr::null(), 0) },
        0
    );
    assert_eq!(
        unsafe { narwhal_pure_core_stl_decode_size(std::ptr::null(), 1) },
        -1
    );
    assert_eq!(
        unsafe { narwhal_pure_core_stl_decode_size(b"x".as_ptr(), 1) },
        -2
    );
}
#[test]
fn stable_mesh_cube_order_winding_and_degenerate_signed_zero() {
    let data = binary(&CUBE, false);
    let mut verts = [123.; 108];
    let mut faces = [77; 36];
    let (mut nv, mut nf) = (-1, -1);
    assert_eq!(mesh(&data, &mut verts, &mut faces, &mut nv, &mut nf), 0);
    assert_eq!((nv, nf), (8, 12));
    assert_eq!(
        &verts[..24],
        &[
            0., 0., 1., 1., 0., 1., 1., 1., 1., 0., 1., 1., 1., 0., 0., 0., 0., 0., 0., 1., 0., 1.,
            1., 0.
        ]
    );
    assert_eq!(
        faces,
        [
            0, 1, 2, 0, 2, 3, 4, 5, 6, 7, 4, 6, 4, 7, 2, 2, 1, 4, 6, 5, 0, 3, 6, 0, 7, 6, 3, 3, 2,
            7, 5, 4, 1, 1, 0, 5
        ]
    );
    assert_eq!(&verts[24..], &[123.; 84]);
    let zero = binary(&[[-0., 0., 0., 0., 0., 0., 1., 0., 0.]], false);
    assert_eq!(mesh(&zero, &mut verts, &mut faces, &mut nv, &mut nf), 0);
    assert_eq!((nv, nf), (2, 0));
    assert_eq!(verts[0].to_bits(), (-0f64).to_bits());
    assert_eq!(&verts[3..6], &[1., 0., 0.]);
}
#[test]
fn stable_mesh_nonfinite_both_formats_and_capacity_refuse_atomically() {
    for token in ["nan", "inf", "-inf"] {
        let ascii = String::from_utf8(ASCII.to_vec())
            .unwrap()
            .replacen("vertex 0 0 0", &format!("vertex {token} 0 0"), 1)
            .into_bytes();
        let value = token.parse::<f64>().unwrap();
        let mut tri = TRI;
        tri[0] = value;
        for data in [ascii, binary(&[tri], false)] {
            let mut verts = [123.; 9];
            let mut indices = [77; 3];
            let (mut nv, mut nf) = (-1, -1);
            assert_eq!(mesh(&data, &mut verts, &mut indices, &mut nv, &mut nf), -2);
            assert_eq!((nv, nf), (-1, -1));
            assert_eq!(verts, [123.; 9]);
            assert_eq!(indices, [77; 3]);
        }
    }
    let data = binary(&[TRI], false);
    let mut verts = [123.; 6];
    let mut indices = [77; 3];
    let (mut nv, mut nf) = (-1, -1);
    assert_eq!(mesh(&data, &mut verts, &mut indices, &mut nv, &mut nf), -3);
    assert_eq!((nv, nf), (-1, -1));
    assert_eq!(verts, [123.; 6]);
    assert_eq!(indices, [77; 3]);
}
