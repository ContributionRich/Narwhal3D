//! Fixed independent CPython-3.13 answers, NOT another interpolation implementation.
//! Format/provenance/performance qualification: fixtures/interpolate_provenance.txt.
use narwhal_pure_core::interpolate::{
    narwhal_pure_core_interpolate as open, narwhal_pure_core_interpolate_periodic as periodic,
    narwhal_pure_core_skin_control_net as skin,
};
use std::time::{Duration, Instant};

const MATH: &str = include_str!("fixtures/interpolate_math.txt");
const HOST: &str = include_str!("fixtures/interpolate_host.txt");
const PERF: &str = include_str!("fixtures/interpolate_performance_inputs.txt");
const BASELINE: &str = include_str!("fixtures/interpolate_performance_baseline.txt");
fn fnv(bytes: &[u8]) -> u64 {
    bytes.iter().fold(0xcbf29ce484222325, |h, b| {
        (h ^ u64::from(*b)).wrapping_mul(0x100000001b3)
    })
}
fn floats(s: &str) -> Vec<f64> {
    if s == "-" {
        return vec![];
    }
    s.split(',')
        .map(|v| {
            assert_eq!(v.len(), 16);
            f64::from_bits(u64::from_str_radix(v, 16).unwrap())
        })
        .collect()
}
struct Case<'a> {
    label: &'a str,
    route: &'a str,
    rows: usize,
    width: usize,
    degree: i64,
    style: u8,
    status: Vec<i32>,
    actual: u64,
    input: Vec<f64>,
    controls: Vec<f64>,
    knots: Vec<f64>,
}
fn cases(text: &str) -> Vec<Case<'_>> {
    text.lines()
        .filter(|l| !l.starts_with('#') && !l.is_empty())
        .map(|line| {
            let f: Vec<_> = line.split('|').collect();
            assert_eq!(f.len(), 11);
            let c = Case {
                label: f[0],
                route: f[1],
                rows: f[2].parse().unwrap(),
                width: f[3].parse().unwrap(),
                degree: f[4].parse().unwrap(),
                style: f[5].parse().unwrap(),
                status: f[6].split(',').map(|s| s.parse().unwrap()).collect(),
                actual: f[7].parse().unwrap(),
                input: floats(f[8]),
                controls: floats(f[9]),
                knots: floats(f[10]),
            };
            assert_eq!(
                c.input.len(),
                c.rows * c.width * 3,
                "{} input framing",
                c.label
            );
            c
        })
        .collect()
}
fn exact(got: &[f64], want: &[f64], label: &str) {
    assert_eq!(got.len(), want.len(), "{label} length");
    for (i, (a, b)) in got.iter().zip(want).enumerate() {
        assert_eq!(
            a.to_bits(),
            b.to_bits(),
            "{label} float {i}: {a:?} != {b:?}"
        );
    }
}
fn replay(c: &Case<'_>) {
    // Canary tails and nonzero counts also detect malformed caller-owned output framing.
    let mut out = vec![12345.0; c.input.len() + 12];
    let mut knots = vec![12345.0; c.rows + 16];
    let (mut degree, mut count, mut nk) = (99, 99, 99);
    let status = unsafe {
        match c.route {
            "open" => open(
                c.input.as_ptr(),
                c.rows,
                c.degree,
                c.style,
                out.as_mut_ptr(),
                out.len(),
                knots.as_mut_ptr(),
                knots.len(),
                &mut degree,
                &mut count,
                &mut nk,
            ),
            "periodic" => {
                degree = 3;
                periodic(
                    c.input.as_ptr(),
                    c.rows,
                    c.style,
                    out.as_mut_ptr(),
                    out.len(),
                    knots.as_mut_ptr(),
                    knots.len(),
                    &mut count,
                    &mut nk,
                )
            }
            "skin" => skin(
                c.input.as_ptr(),
                c.rows,
                c.width,
                c.degree,
                c.style,
                out.as_mut_ptr(),
                out.len(),
                knots.as_mut_ptr(),
                knots.len(),
                &mut degree,
                &mut count,
                &mut nk,
            ),
            _ => panic!("unknown route"),
        }
    };
    assert!(c.status.contains(&status), "{} status {status}", c.label);
    if status != 0 {
        assert_eq!((count, nk), (0, 0), "{} refusal counts", c.label);
        if c.route != "periodic" {
            assert_eq!(degree, 0);
        }
        assert!(out.iter().chain(&knots).all(|v| *v == 12345.0));
        return;
    }
    assert_eq!(degree, c.actual, "{} degree", c.label);
    assert_eq!(
        count as usize,
        if c.route == "skin" {
            c.controls.len()
        } else {
            c.controls.len() / 3
        }
    );
    assert_eq!(nk as usize, c.knots.len());
    exact(&out[..c.controls.len()], &c.controls, c.label);
    exact(&knots[..c.knots.len()], &c.knots, c.label);
    assert!(out[c.controls.len()..]
        .iter()
        .chain(&knots[c.knots.len()..])
        .all(|v| *v == 12345.0));
}
#[test]
fn independent_golden_computations() {
    let provenance = include_str!("fixtures/interpolate_provenance.txt");
    assert_eq!(fnv(provenance.as_bytes()), 0xef2563e6e6143ee5);
    assert!(provenance.contains("ed019e1d8a929cefdbff0d70a6fb0807cc907e698b6370eff74e46abf2ef38eb"));
    assert_eq!(fnv(MATH.as_bytes()), 0x72d3a1f43fef5688);
    assert_eq!(fnv(HOST.as_bytes()), 0x05e92248199ef716);
    let data = cases(MATH);
    assert_eq!(data.len(), 2025);
    let mut counts = [0; 4];
    let (mut negative_zero_in, mut negative_zero_out, mut positive_zero_out) = (0, 0, 0);
    for c in &data {
        let bucket = if c.status != [0] {
            3
        } else {
            match c.route {
                "open" => 0,
                "periodic" => 1,
                "skin" => 2,
                _ => unreachable!(),
            }
        };
        counts[bucket] += 1;
        negative_zero_in += c
            .input
            .iter()
            .filter(|v| v.to_bits() == 1_u64 << 63)
            .count();
        negative_zero_out += c
            .controls
            .iter()
            .chain(&c.knots)
            .filter(|v| v.to_bits() == 1_u64 << 63)
            .count();
        positive_zero_out += c
            .controls
            .iter()
            .chain(&c.knots)
            .filter(|v| v.to_bits() == 0)
            .count();
        replay(c);
    }
    assert_eq!(counts, [759, 249, 1005, 12]);
    // Hard counts protect signed-zero coverage from being silently regenerated away.
    assert_eq!(
        (negative_zero_in, negative_zero_out, positive_zero_out),
        (978, 606, 8499)
    );
    let host = cases(HOST);
    assert_eq!(host.len(), 39);
    for c in &host {
        replay(c);
    }
}
#[test]
fn abi_refusals_clear_counts_and_bound_input() {
    let src = [0., 0., 0., 1., 2., 3., 2., 1., 0., 3., 2., 1.];
    let mut out = [0.; 12];
    let mut knots = [0.; 8];
    let (mut d, mut n, mut k);
    unsafe {
        for route in ["open", "periodic", "skin"] {
            (d, n, k) = (99, 99, 99);
            let code = match route {
                "open" => open(
                    src.as_ptr(),
                    3,
                    3,
                    3,
                    out.as_mut_ptr(),
                    12,
                    knots.as_mut_ptr(),
                    8,
                    &mut d,
                    &mut n,
                    &mut k,
                ),
                "periodic" => periodic(
                    src.as_ptr(),
                    3,
                    3,
                    out.as_mut_ptr(),
                    12,
                    knots.as_mut_ptr(),
                    8,
                    &mut n,
                    &mut k,
                ),
                _ => skin(
                    src.as_ptr(),
                    3,
                    1,
                    3,
                    3,
                    out.as_mut_ptr(),
                    12,
                    knots.as_mut_ptr(),
                    8,
                    &mut d,
                    &mut n,
                    &mut k,
                ),
            };
            assert_eq!(code, -2);
            assert_eq!((n, k), (0, 0));
            if route != "periodic" {
                assert_eq!(d, 0);
            }
        }
        (d, n, k) = (99, 99, 99);
        assert_eq!(
            open(
                src.as_ptr(),
                2049,
                3,
                0,
                out.as_mut_ptr(),
                12,
                knots.as_mut_ptr(),
                8,
                &mut d,
                &mut n,
                &mut k
            ),
            -3
        );
        assert_eq!((d, n, k), (0, 0, 0));
        (d, n, k) = (99, 99, 99);
        assert_eq!(
            open(
                src.as_ptr(),
                4,
                3,
                0,
                out.as_mut_ptr(),
                0,
                knots.as_mut_ptr(),
                8,
                &mut d,
                &mut n,
                &mut k
            ),
            -3
        );
        assert_eq!((d, n, k), (0, 0, 0));
        assert_eq!(
            open(
                src.as_ptr(),
                4,
                3,
                0,
                out.as_mut_ptr(),
                12,
                knots.as_mut_ptr(),
                8,
                std::ptr::null_mut(),
                &mut n,
                &mut k
            ),
            -1
        );
        (n, k) = (99, 99);
        assert_eq!(
            periodic(
                src.as_ptr(),
                2049,
                0,
                out.as_mut_ptr(),
                12,
                knots.as_mut_ptr(),
                8,
                &mut n,
                &mut k
            ),
            -3
        );
        assert_eq!((n, k), (0, 0));
        (d, n, k) = (99, 99, 99);
        assert_eq!(
            skin(
                src.as_ptr(),
                2_000_001,
                1,
                3,
                0,
                out.as_mut_ptr(),
                12,
                knots.as_mut_ptr(),
                8,
                &mut d,
                &mut n,
                &mut k
            ),
            -3
        );
        assert_eq!((d, n, k), (0, 0, 0));
    }
}
#[test]
fn bounded_qualified_workloads() {
    assert_eq!(fnv(PERF.as_bytes()), 0x7cb39f7561befac0);
    assert_eq!(fnv(BASELINE.as_bytes()), 0xe8c43e57e3c0bd40);
    let qualified = cases(PERF);
    assert_eq!(qualified.len(), 9);
    let start = Instant::now();
    // A portable, deliberately broad runaway-work guard, NOT the original 10% claim.
    // That same-host relative qualification remains unchanged in the private runner.
    for c in &qualified {
        for _ in 0..10 {
            replay(c);
        }
    }
    assert!(
        start.elapsed() < Duration::from_secs(30),
        "bounded interpolation workloads exceeded 30 seconds"
    );
}
