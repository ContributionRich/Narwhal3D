//! Independent frozen CPython 3.13/openNURBS answers; no Python numeric engine in this test.
//! Display bits are qualified ONLY on Linux/glibc. See fixtures/nurbs_provenance.txt.
use narwhal_pure_core::{
    display::narwhal_pure_core_curve_display_samples as display,
    nearest::{
        narwhal_pure_core_nearest_on_nurbs as nearest,
        narwhal_pure_core_nearest_on_nurbs_many as many, narwhal_pure_core_sample_nurbs as sample,
    },
};

fn hash(bytes: impl IntoIterator<Item = u8>) -> u64 {
    bytes.into_iter().fold(0xcbf29ce484222325, |h, b| {
        (h ^ u64::from(b)).wrapping_mul(0x100000001b3)
    })
}
fn fixture(text: &'static str, rows: usize, digest: u64) -> Vec<Vec<&'static str>> {
    assert_eq!(hash(text.bytes()), digest, "fixture byte integrity");
    let lines: Vec<_> = text
        .lines()
        .map(|s| s.split_whitespace().collect())
        .collect();
    assert_eq!(lines.len(), rows);
    lines
}
fn n(s: &str) -> usize {
    s.parse().unwrap()
}
fn bits(s: &str) -> u64 {
    u64::from_str_radix(s, 16).unwrap()
}
fn f(s: &str) -> f64 {
    f64::from_bits(bits(s))
}
struct Curve {
    order: usize,
    count: usize,
    rat: u8,
    closed: u8,
    lo: f64,
    hi: f64,
    knots: Vec<f64>,
    cvs: Vec<f64>,
}
fn curves() -> Vec<Curve> {
    let rows = fixture(
        include_str!("fixtures/nurbs_curves.txt"),
        762,
        0xf4078bf20f741d92,
    );
    let curves: Vec<_> = rows
        .into_iter()
        .enumerate()
        .map(|(i, r)| {
            assert_eq!(n(r[0]), i);
            let k = n(r[7]);
            let count = n(r[2]);
            assert_eq!(r.len(), 8 + k + count * 4);
            Curve {
                order: n(r[1]),
                count,
                rat: n(r[3]) as u8,
                closed: n(r[4]) as u8,
                lo: f(r[5]),
                hi: f(r[6]),
                knots: r[8..8 + k].iter().map(|s| f(s)).collect(),
                cvs: r[8 + k..].iter().map(|s| f(s)).collect(),
            }
        })
        .collect();
    for (degree, count) in [(1, 45), (2, 87), (3, 406), (4, 111), (5, 113)] {
        assert_eq!(
            curves.iter().filter(|c| c.order == degree + 1).count(),
            count
        );
    }
    assert_eq!(curves.iter().filter(|c| c.rat == 1).count(), 230);
    assert_eq!(curves.iter().filter(|c| c.closed == 1).count(), 5);
    assert!(curves.iter().any(|c| c.lo < 0.0));
    assert!(curves
        .iter()
        .any(|c| c.cvs.iter().any(|x| x.abs() > 100000.0)));
    assert!(curves
        .iter()
        .any(|c| c.knots.windows(2).any(|w| w[0] == w[1])));
    curves
}
#[test]
fn nearest_all_original_queries_scalar_and_batch_bits() {
    let cs = curves();
    let rows = fixture(
        include_str!("fixtures/nurbs_nearest.txt"),
        5678,
        0xf644a1490a8284d1,
    );
    // Batch each original query group by packed curve; every row independently has Python expectations.
    for (id, c) in cs.iter().enumerate() {
        let group: Vec<_> = rows.iter().filter(|r| n(r[0]) == id).collect();
        if group.is_empty() {
            continue;
        }
        let targets: Vec<_> = group
            .iter()
            .flat_map(|r| r[1..4].iter().map(|s| f(s)))
            .collect();
        let mut out = vec![0.0; 5 * group.len()];
        let mut ends = vec![255; group.len()];
        unsafe {
            assert_eq!(
                many(
                    c.order,
                    c.knots.as_ptr(),
                    c.knots.len(),
                    c.cvs.as_ptr(),
                    c.count,
                    c.rat,
                    c.closed,
                    targets.as_ptr(),
                    group.len(),
                    out.as_mut_ptr(),
                    ends.as_mut_ptr()
                ),
                0
            );
        }
        for (j, r) in group.iter().enumerate() {
            assert_eq!(r.len(), 10);
            let mut scalar = [0.0; 5];
            let mut end = 255;
            unsafe {
                assert_eq!(
                    nearest(
                        c.order,
                        c.knots.as_ptr(),
                        c.knots.len(),
                        c.cvs.as_ptr(),
                        c.count,
                        c.rat,
                        c.closed,
                        targets[j * 3..].as_ptr(),
                        scalar.as_mut_ptr(),
                        &mut end
                    ),
                    0
                );
            }
            for k in 0..5 {
                assert_eq!(
                    scalar[k].to_bits(),
                    bits(r[4 + k]),
                    "scalar curve {id} row {j} field {k}"
                );
                assert_eq!(
                    out[j * 5 + k].to_bits(),
                    bits(r[4 + k]),
                    "batch curve {id} row {j} field {k}"
                );
            }
            assert_eq!(usize::from(end), n(r[9]));
            assert_eq!(ends[j], end);
        }
    }
}
#[test]
fn independent_opennurbs_uniform_samples() {
    let cs = curves();
    let rows = fixture(
        include_str!("fixtures/nurbs_sample.txt"),
        3048,
        0x5677d24adf9aaee0,
    );
    for (i, r) in rows.iter().enumerate() {
        let c = &cs[n(r[0])];
        let segments = n(r[1]);
        let mut out = vec![0.0; 3 * (segments + 1)];
        assert_eq!(r.len(), 2 + out.len());
        unsafe {
            assert_eq!(
                sample(
                    c.order,
                    c.knots.as_ptr(),
                    c.knots.len(),
                    c.cvs.as_ptr(),
                    c.count,
                    c.rat,
                    c.lo,
                    c.hi,
                    segments,
                    out.as_mut_ptr(),
                    out.len()
                ),
                0
            );
        }
        for (j, x) in out.iter().enumerate() {
            assert_eq!(x.to_bits(), bits(r[2 + j]), "sample {i} field {j}");
        }
    }
}
#[test]
#[cfg(all(target_os = "linux", target_env = "gnu"))]
fn display_all_original_tolerances_exact_bitstream() {
    let cs = curves();
    let rows = fixture(
        include_str!("fixtures/nurbs_display.txt"),
        2141,
        0x5a956895cd58d814,
    );
    let mut vertices = 0;
    for (i, r) in rows.iter().enumerate() {
        assert_eq!(r.len(), 4);
        let c = &cs[n(r[0])];
        let count = n(r[2]);
        vertices += count;
        let mut params = vec![0.0; count];
        let mut xyz = vec![0.0; 3 * count];
        let mut needed = usize::MAX;
        unsafe {
            // The host's actual first-buffer protocol, including exact count and refusal status.
            let capacity = count.min(256);
            let code = display(
                c.order,
                c.knots.as_ptr(),
                c.knots.len(),
                c.cvs.as_ptr(),
                c.count,
                c.rat,
                c.lo,
                c.hi,
                f(r[1]),
                params.as_mut_ptr(),
                xyz.as_mut_ptr(),
                capacity,
                &mut needed,
            );
            assert_eq!(needed, count, "display {i} count");
            assert_eq!(code, if count > capacity { -3 } else { 0 });
            if count > capacity {
                assert_eq!(
                    display(
                        c.order,
                        c.knots.as_ptr(),
                        c.knots.len(),
                        c.cvs.as_ptr(),
                        c.count,
                        c.rat,
                        c.lo,
                        c.hi,
                        f(r[1]),
                        params.as_mut_ptr(),
                        xyz.as_mut_ptr(),
                        count,
                        &mut needed
                    ),
                    0
                );
            }
        }
        assert_eq!(needed, count);
        // Big-endian IEEE-754 bytes, parameters then xyz; includes signed zero, every field and ordering.
        let digest = hash(
            params
                .iter()
                .chain(&xyz)
                .flat_map(|x| x.to_bits().to_be_bytes()),
        );
        assert_eq!(digest, bits(r[3]), "display {i} all output bits");
    }
    assert_eq!(vertices, 10_371_637);
}
#[test]
fn abi_refusals_and_nonfinite_batch_slots() {
    let cs = curves();
    let c = &cs[0];
    let mut out = [123.0; 5];
    let mut end = 255;
    unsafe {
        for target in [[f64::NAN, 0.0, 0.0], [0.0, f64::INFINITY, 0.0]] {
            assert_eq!(
                nearest(
                    c.order,
                    c.knots.as_ptr(),
                    c.knots.len(),
                    c.cvs.as_ptr(),
                    c.count,
                    c.rat,
                    c.closed,
                    target.as_ptr(),
                    out.as_mut_ptr(),
                    &mut end
                ),
                -2
            );
            assert_eq!(out, [123.0; 5]);
            assert_eq!(
                many(
                    c.order,
                    c.knots.as_ptr(),
                    c.knots.len(),
                    c.cvs.as_ptr(),
                    c.count,
                    c.rat,
                    c.closed,
                    target.as_ptr(),
                    1,
                    out.as_mut_ptr(),
                    &mut end
                ),
                0
            );
            assert!(out.iter().all(|v| v.is_nan()));
            assert_eq!(end, 0);
            out.fill(123.0);
        }
        let target = [0.0; 3];
        assert_eq!(
            nearest(
                c.order,
                c.knots.as_ptr(),
                c.knots.len(),
                c.cvs.as_ptr(),
                c.count,
                2,
                c.closed,
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut end
            ),
            -2
        );
        assert_eq!(
            nearest(
                c.order,
                std::ptr::null(),
                c.knots.len(),
                c.cvs.as_ptr(),
                c.count,
                c.rat,
                c.closed,
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut end
            ),
            -1
        );
        assert_eq!(
            nearest(
                0,
                c.knots.as_ptr(),
                c.knots.len(),
                c.cvs.as_ptr(),
                c.count,
                c.rat,
                c.closed,
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut end
            ),
            -2
        );
        let mut needed = 777;
        let mut xyz = [0.0; 3];
        assert_eq!(
            display(
                c.order,
                c.knots.as_ptr(),
                c.knots.len(),
                c.cvs.as_ptr(),
                c.count,
                c.rat,
                c.lo,
                c.hi,
                f64::NAN,
                out.as_mut_ptr(),
                xyz.as_mut_ptr(),
                1,
                &mut needed
            ),
            -2
        );
        assert_eq!(needed, 0);
        assert_eq!(
            sample(
                c.order,
                c.knots.as_ptr(),
                c.knots.len(),
                c.cvs.as_ptr(),
                c.count,
                c.rat,
                c.lo,
                c.hi,
                0,
                xyz.as_mut_ptr(),
                3
            ),
            -2
        );
    }
}
