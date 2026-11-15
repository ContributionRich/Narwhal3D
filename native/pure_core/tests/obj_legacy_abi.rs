//! Stable two-pass OBJ exports retain the independent original 84-case corpus.
//! This replaces public optional Python encoder-adapter coverage, not the ABI.
use narwhal_pure_core::obj::{
    narwhal_pure_core_obj_encode as encode, narwhal_pure_core_obj_size as size,
};
fn numbers<T: std::str::FromStr>(s: &str) -> Vec<T>
where
    T::Err: std::fmt::Debug,
{
    if s.is_empty() {
        vec![]
    } else {
        s.split(',').map(|v| v.parse().unwrap()).collect()
    }
}
fn hex(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}
#[test]
fn legacy_size_and_encode_match_independent_original_bytes() {
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
        let needed = unsafe {
            size(
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
            )
        };
        assert_eq!(needed, expected.len() as i64, "case {case}");
        let mut out = vec![0xa5; expected.len() + 1];
        let call = |out: &mut [u8], capacity| unsafe {
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
                capacity,
            )
        };
        assert_eq!(call(&mut out, expected.len()), 0);
        assert_eq!(&out[..expected.len()], expected, "case {case}");
        assert_eq!(out[expected.len()], 0xa5);
        out.fill(0xa5);
        assert_eq!(call(&mut out, expected.len() - 1), -3);
        assert!(out.iter().all(|v| *v == 0xa5));
        assert_eq!(call(&mut out, expected.len() + 1), -3);
        assert!(out.iter().all(|v| *v == 0xa5));
    }
}
#[test]
fn legacy_domain_and_null_refusals_do_not_publish() {
    for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let v = [0., 0., bad, 1., 0., 0., 0., 1., 0.];
        let mut out = [0xa5; 1024];
        assert_eq!(
            unsafe {
                size(
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
                )
            },
            -2
        );
        assert_eq!(
            unsafe {
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
                )
            },
            -2
        );
        assert_eq!(out, [0xa5; 1024]);
    }
    assert_eq!(
        unsafe {
            size(
                std::ptr::null(),
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
            )
        },
        -1
    );
}
