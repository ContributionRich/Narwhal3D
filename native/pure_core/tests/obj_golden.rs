//! Independent original CPython OBJ captures, frozen before retirement.
//! Only mesh export records o/v/f; no generated reference serializer.
use narwhal_pure_core::obj::narwhal_pure_core_obj_encode_into as encode;
fn numbers<T: std::str::FromStr>(s: &str) -> Vec<T>
where
    T::Err: std::fmt::Debug,
{
    if s.is_empty() {
        vec![]
    } else {
        s.split(',').map(|x| x.parse().unwrap()).collect()
    }
}
fn hex(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}
#[test]
fn original_python_bytes() {
    let rows: Vec<_> = include_str!("fixtures/obj_original22.txt")
        .lines()
        .collect();
    assert_eq!(rows.len(), 84);
    for (case, row) in rows.iter().enumerate() {
        let f: Vec<_> = row.split('|').collect();
        assert_eq!(f.len(), 8);
        let v: Vec<f64> = numbers(f[0]);
        let names = hex(f[1]);
        let nl: Vec<i64> = numbers(f[2]);
        let vc: Vec<i64> = numbers(f[3]);
        let fc: Vec<i64> = numbers(f[4]);
        let arity: Vec<i64> = numbers(f[5]);
        let idx: Vec<i32> = numbers(f[6]);
        let expected = hex(f[7]);
        let mut out = vec![
            0;
            13 + names.len()
                + 4 * nl.len()
                + 80 * (v.len() / 3)
                + ((v.len() / 3).max(1).to_string().len() + 2) * idx.len()
                + 3 * arity.len()
        ];
        let mut written = -1;
        let rc = unsafe {
            encode(
                v.as_ptr(),
                (v.len() / 3) as i64,
                names.as_ptr(),
                names.len() as i64,
                nl.as_ptr(),
                vc.as_ptr(),
                fc.as_ptr(),
                arity.as_ptr(),
                arity.len() as i64,
                idx.as_ptr(),
                idx.len() as i64,
                nl.len() as i64,
                out.as_mut_ptr(),
                out.len(),
                &mut written,
            )
        };
        assert_eq!(rc, 0, "case {case}");
        assert_eq!(written, expected.len() as i64);
        assert_eq!(&out[..written as usize], expected, "case {case}");
    }
}
#[test]
fn invalid_mesh_does_not_publish_output() {
    for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let v = [0., 0., bad, 1., 0., 0., 0., 1., 0.];
        let mut out = [0xa5; 1024];
        let mut written = -77;
        let rc = unsafe {
            encode(
                v.as_ptr(),
                3,
                b"x".as_ptr(),
                1,
                [1].as_ptr(),
                [3].as_ptr(),
                [1].as_ptr(),
                [3].as_ptr(),
                1,
                [0, 1, 2].as_ptr(),
                3,
                1,
                out.as_mut_ptr(),
                out.len(),
                &mut written,
            )
        };
        assert_eq!(rc, -2);
        assert_eq!(written, -77);
        assert_eq!(out, [0xa5; 1024]);
    }
}
