//! Required STL geometry boundary: file data is not automatically safe geometry.
use narwhal_pure_core::stl::*;

fn ascii(value: &str, repeat: usize) -> Vec<u8> {
    let facet = format!("facet normal 0 0 1\nouter loop\nvertex {value} 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\n");
    format!("solid test\n{}endsolid test\n", facet.repeat(repeat)).into_bytes()
}
fn binary(value: f32) -> Vec<u8> {
    let mut data = b"test".to_vec();
    data.resize(80, b' ');
    data.extend_from_slice(&1u32.to_le_bytes());
    for v in [0., 0., 1., value, 0., 0., 1., 0., 0., 0., 1., 0.] {
        data.extend_from_slice(&v.to_le_bytes());
    }
    data.extend_from_slice(&0u16.to_le_bytes());
    data
}
fn import(
    data: &[u8],
    vertices: &mut [f64],
    indices: &mut [u32],
    nv: &mut i64,
    nf: &mut i64,
    diag: &mut [i64; 3],
) -> i32 {
    unsafe {
        narwhal_pure_core_stl_import_mesh_decode(
            data.as_ptr(),
            data.len(),
            vertices.as_mut_ptr(),
            vertices.len() / 3,
            indices.as_mut_ptr(),
            indices.len(),
            nv,
            nf,
            diag.as_mut_ptr(),
        )
    }
}

#[test]
fn stl_import_unsafe_ascii_is_rejected_without_any_output() {
    for (token, reason) in [
        ("nan", 1),
        ("-NaN", 1),
        ("inf", 1),
        ("-Infinity", 1),
        ("1e100", 2),
        ("-1e100", 2),
        ("3.4028236e38", 2),
    ] {
        let data = ascii(token, 1);
        let (mut vertices, mut indices) = ([123.; 9], [77; 3]);
        let (mut nv, mut nf, mut diag) = (-1, -1, [99; 3]);
        assert_eq!(
            import(
                &data,
                &mut vertices,
                &mut indices,
                &mut nv,
                &mut nf,
                &mut diag
            ),
            -30,
            "{token}"
        );
        assert_eq!(diag, [-30, 0, reason]);
        assert_eq!((nv, nf), (-1, -1));
        assert_eq!(vertices, [123.; 9]);
        assert_eq!(indices, [77; 3]);
    }
}

#[test]
fn stl_import_binary_nonfinite_is_rejected_without_any_output() {
    for value in [f32::NAN, f32::INFINITY, f32::NEG_INFINITY] {
        let data = binary(value);
        let (mut vertices, mut indices) = ([123.; 9], [77; 3]);
        let (mut nv, mut nf, mut diag) = (-1, -1, [0; 3]);
        assert_eq!(
            import(
                &data,
                &mut vertices,
                &mut indices,
                &mut nv,
                &mut nf,
                &mut diag
            ),
            -30
        );
        assert_eq!(diag, [-30, 0, 1]);
        assert_eq!((nv, nf), (-1, -1));
        assert_eq!(vertices, [123.; 9]);
        assert_eq!(indices, [77; 3]);
    }
}

#[test]
fn stl_import_max_finite_destination_and_normal_rounding_are_accepted() {
    // The legacy %.9g writer prints MAX as 3.40282347e38, slightly above exact
    // MAX in f64 but rounding to finite MAX in the actual f32 destination.
    for data in [
        binary(f32::MAX),
        binary(-f32::MAX),
        ascii("3.4028234663852886e38", 1),
        ascii("3.40282347e38", 1),
        ascii("1_0", 1),
    ] {
        let (mut vertices, mut indices) = ([0.; 9], [0; 3]);
        let (mut nv, mut nf, mut diag) = (-1, -1, [99; 3]);
        assert_eq!(
            import(
                &data,
                &mut vertices,
                &mut indices,
                &mut nv,
                &mut nf,
                &mut diag
            ),
            0
        );
        assert_eq!((nv, nf), (3, 1));
        assert_eq!(diag, [0; 3]);
        assert!(vertices.iter().all(|x| (*x as f32).is_finite()));
        assert_eq!(indices, [0, 1, 2]);
    }
}

#[test]
fn stl_import_validates_late_vertices_before_welding_or_partial_copy() {
    let mut data = ascii("0", 1);
    data.extend_from_slice(&ascii("1e100", 1));
    let (mut vertices, mut indices) = ([123.; 18], [77; 6]);
    let (mut nv, mut nf, mut diag) = (-1, -1, [0; 3]);
    assert_eq!(
        import(
            &data,
            &mut vertices,
            &mut indices,
            &mut nv,
            &mut nf,
            &mut diag
        ),
        -30
    );
    assert_eq!(diag, [-30, 9, 2]);
    assert_eq!((nv, nf), (-1, -1));
    assert_eq!(vertices, [123.; 18]);
    assert_eq!(indices, [77; 6]);
    // All corners duplicate: the face would be dropped, but invalid source
    // coordinates still must not disappear into a supposedly safe empty mesh.
    let data = b"solid\nfacet\nvertex inf 0 0\nvertex inf 0 0\nvertex inf 0 0\nendfacet\n";
    assert_eq!(
        import(
            data,
            &mut vertices,
            &mut indices,
            &mut nv,
            &mut nf,
            &mut diag
        ),
        -30
    );
}

#[test]
fn stl_import_exact_welding_signed_zero_and_degenerate_faces() {
    let data = b"solid\nfacet\nvertex -0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendfacet\nfacet\nvertex 0 0 0\nvertex -0 0 0\nvertex 0 1 0\nendfacet\n";
    let (mut vertices, mut indices) = ([123.; 18], [77; 6]);
    let (mut nv, mut nf, mut diag) = (-1, -1, [0; 3]);
    assert_eq!(
        import(
            data,
            &mut vertices,
            &mut indices,
            &mut nv,
            &mut nf,
            &mut diag
        ),
        0
    );
    assert_eq!((nv, nf), (3, 1));
    assert_eq!(&indices[..3], &[0, 1, 2]);
    assert!(vertices[0].is_sign_negative()); // first-seen point preserved
    assert_eq!(&vertices[..9], &[-0., 0., 0., 1., 0., 0., 0., 1., 0.]);
}

#[test]
fn stl_import_capacity_and_malformed_failures_are_atomic() {
    let data = ascii("0", 1);
    let (mut vertices, mut indices) = ([123.; 6], [77; 3]);
    let (mut nv, mut nf, mut diag) = (-1, -1, [0; 3]);
    assert_eq!(
        import(
            &data,
            &mut vertices,
            &mut indices,
            &mut nv,
            &mut nf,
            &mut diag
        ),
        -3
    );
    assert_eq!((nv, nf), (-1, -1));
    assert_eq!(vertices, [123.; 6]);
    assert_eq!(indices, [77; 3]);
    assert_eq!(
        import(
            b"solid\nfacet",
            &mut vertices,
            &mut indices,
            &mut nv,
            &mut nf,
            &mut diag
        ),
        -10
    );
    assert_eq!((nv, nf), (-1, -1));
    assert_eq!(vertices, [123.; 6]);
}

#[test]
fn stl_import_preserves_solid_binary_detection_but_not_file_semantics() {
    let mut data = binary(0.);
    data[..5].copy_from_slice(b"solid");
    let mut diag = [0; 3];
    assert_eq!(
        unsafe { narwhal_pure_core_stl_file_size(data.as_ptr(), data.len(), 0, diag.as_mut_ptr()) },
        0
    );
    let (mut vertices, mut indices) = ([0.; 9], [0; 3]);
    let (mut nv, mut nf) = (-1, -1);
    assert_eq!(
        import(
            &data,
            &mut vertices,
            &mut indices,
            &mut nv,
            &mut nf,
            &mut diag
        ),
        0
    );
    assert_eq!((nv, nf), (3, 1));
}

#[test]
fn stl_import_checked_spans_and_null_outputs_never_publish() {
    let data = binary(0.);
    let (mut vertices, mut indices) = ([123.; 9], [77; 3]);
    let (mut nv, mut nf, mut diag) = (-1, -1, [0; 3]);
    unsafe {
        assert_eq!(
            narwhal_pure_core_stl_import_mesh_decode(
                std::ptr::null(),
                1,
                vertices.as_mut_ptr(),
                3,
                indices.as_mut_ptr(),
                3,
                &mut nv,
                &mut nf,
                diag.as_mut_ptr()
            ),
            -1
        );
        assert_eq!(
            narwhal_pure_core_stl_import_mesh_decode(
                data.as_ptr(),
                usize::MAX,
                vertices.as_mut_ptr(),
                3,
                indices.as_mut_ptr(),
                3,
                &mut nv,
                &mut nf,
                diag.as_mut_ptr()
            ),
            -1
        );
        assert_eq!(
            narwhal_pure_core_stl_import_mesh_decode(
                data.as_ptr(),
                data.len(),
                vertices.as_mut_ptr(),
                3,
                indices.as_mut_ptr(),
                3,
                std::ptr::null_mut(),
                &mut nf,
                diag.as_mut_ptr()
            ),
            -1
        );
        assert_eq!(
            narwhal_pure_core_stl_import_mesh_decode(
                data.as_ptr(),
                data.len(),
                std::ptr::null_mut(),
                3,
                indices.as_mut_ptr(),
                3,
                &mut nv,
                &mut nf,
                diag.as_mut_ptr()
            ),
            -1
        );
        assert_eq!(
            narwhal_pure_core_stl_import_mesh_decode(
                data.as_ptr(),
                data.len(),
                vertices.as_mut_ptr(),
                3,
                std::ptr::null_mut(),
                3,
                &mut nv,
                &mut nf,
                diag.as_mut_ptr()
            ),
            -1
        );
        assert_eq!(
            narwhal_pure_core_stl_import_mesh_decode(
                data.as_ptr(),
                data.len(),
                vertices.as_mut_ptr(),
                3,
                indices.as_mut_ptr(),
                3,
                &mut nv,
                &mut nf,
                std::ptr::null_mut()
            ),
            -1
        );
    }
    assert_eq!((nv, nf), (-1, -1));
    assert_eq!(vertices, [123.; 9]);
    assert_eq!(indices, [77; 3]);
}
