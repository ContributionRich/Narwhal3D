//! Frozen original Python answers, exercised through the actual production C ABI.
//! Bernstein verification below is independent of the endpoint control-point solve.
use narwhal_pure_core::blend::narwhal_pure_core_blend_bezier_points as blend;

const GOLDEN: &str = include_str!("fixtures/blend_numeric.txt");
fn values(s: &str) -> Vec<f64> {
    s.split(',')
        .map(|v| f64::from_bits(u64::from_str_radix(v, 16).unwrap()))
        .collect()
}
struct Case<'a> {
    kind: &'a str,
    k: [usize; 2],
    scales: Vec<f64>,
    ends: [Vec<f64>; 2],
    expected: Vec<f64>,
    targets: Vec<f64>,
}
fn parse(line: &str) -> Case<'_> {
    let f: Vec<_> = line.split('|').collect();
    assert_eq!(f.len(), 8, "fixture schema");
    let k = [
        f[1].parse::<usize>().unwrap(),
        f[2].parse::<usize>().unwrap(),
    ];
    assert!(k.iter().all(|&n| n <= 4));
    let c = Case {
        kind: f[0],
        k,
        scales: values(f[3]),
        ends: [values(f[4]), values(f[5])],
        expected: values(f[6]),
        targets: values(f[7]),
    };
    assert_eq!(c.scales.len(), 2);
    for (side, order) in k.iter().enumerate() {
        assert_eq!(c.ends[side].len(), 3 * (order + 1));
    }
    assert_eq!(c.expected.len(), 3 * (k[0] + k[1] + 2));
    assert_eq!(c.targets.len(), 3 * (k[0] + k[1] + 2));
    assert!(c
        .scales
        .iter()
        .chain(c.ends.iter().flatten())
        .chain(&c.expected)
        .chain(&c.targets)
        .all(|v| v.is_finite()));
    c
}
fn solve(c: &Case<'_>) -> Vec<f64> {
    let mut out = [f64::NAN; 30];
    let status = unsafe {
        blend(
            c.ends[0].as_ptr(),
            c.ends[0].len(),
            c.ends[1].as_ptr(),
            c.ends[1].len(),
            c.k[0] as u32,
            c.k[1] as u32,
            c.scales[0],
            c.scales[1],
            out.as_mut_ptr(),
            out.len(),
        )
    };
    assert_eq!(status, 0);
    let out = out[..c.expected.len()].to_vec();
    assert!(out.iter().all(|x| x.is_finite()));
    out
}
fn same_bits(got: &[f64], expected: &[f64]) {
    assert_eq!(got.len(), expected.len());
    for (i, (a, b)) in got.iter().zip(expected).enumerate() {
        assert_eq!(a.to_bits(), b.to_bits(), "component {i}");
    }
}

// B^(j)(0) = (n!/(n-j)!) sum (-1)^(j-i) C(j,i) P_i;
// B^(j)(1) = (n!/(n-j)!) sum (-1)^i C(j,i) P_(n-i).
// This evaluates the Bernstein basis, not the solver's forward/backward solve.
fn derivative(pts: &[f64], j: usize, end: usize) -> [f64; 3] {
    let n = pts.len() / 3 - 1;
    let mut lead = 1.0;
    for i in 0..j {
        lead *= (n - i) as f64;
    }
    let mut sum = [0.0; 3];
    let mut choose = 1usize;
    for i in 0..=j {
        let exponent = if end == 0 { j - i } else { i };
        let sign = if exponent % 2 == 0 { 1.0 } else { -1.0 };
        let weight = lead * sign * choose as f64;
        let index = if end == 0 { i } else { n - i };
        for axis in 0..3 {
            sum[axis] += pts[index * 3 + axis] * weight;
        }
        if i < j {
            choose = choose * (j - i) / (i + 1);
        }
    }
    sum
}
#[test]
fn frozen_original_parity_all_records() {
    let mut counts = [0; 5];
    let mut signs = Vec::new();
    for (index, line) in GOLDEN.lines().enumerate() {
        let c = parse(line);
        let group = match c.kind {
            "RECON" => 0,
            "PARITY" => 1,
            "ORIENT" => 2,
            "SIGN" => 3,
            "FINITE" => 4,
            _ => panic!("unknown fixture kind"),
        };
        counts[group] += 1;
        let out = solve(&c);
        assert_eq!(out.len(), c.expected.len(), "record {index}");
        same_bits(&out, &c.expected);
        if c.kind == "SIGN" {
            signs.push(out);
        }
    }
    assert_eq!(counts, [100, 400, 120, 2, 1]);
    assert_ne!(signs[0], signs[1]);
}
#[test]
fn independent_bernstein_all_25_pairs_original_tolerance() {
    let mut pairs = [[0; 5]; 5];
    let mut checked = 0;
    for line in GOLDEN.lines() {
        let c = parse(line);
        if c.kind != "RECON" {
            continue;
        }
        pairs[c.k[0]][c.k[1]] += 1;
        let pts = solve(&c);
        let mut offset = 0;
        for side in 0..2 {
            for j in 0..=c.k[side] {
                let got = derivative(&pts, j, side);
                // Fixed targets preserve Python's exact exponentiation/multiplication order.
                for (axis, &a) in got.iter().enumerate() {
                    let b = c.targets[offset + axis];
                    assert!(
                        (a - b).abs() <= 1e-10 * 1.0_f64.max(a.abs()).max(b.abs()),
                        "orders {:?}, side {side}, derivative {j}, axis {axis}: {a} != {b}",
                        c.k
                    );
                }
                offset += 3;
                checked += 1;
            }
        }
    }
    assert_eq!(pairs, [[4; 5]; 5]);
    assert_eq!(checked, 600);
}
