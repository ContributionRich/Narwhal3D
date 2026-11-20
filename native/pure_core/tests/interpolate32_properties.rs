// Interpolate numeric/property checks retired from spike/test_interpolate.py
// (original SHA256: d152ae180159c212076874a1130b0be10b95545e135deefd83376e34dd222c6e,
// 487 lines / 22,407 bytes). Each test below preserves an INDEPENDENT expectation
// (a hand-computed constant, a residual, or a cross-algorithm comparison) and the
// original tolerance. No test captures the current Rust output as the expected
// value. The frozen golden fixtures in tests/interpolate_golden.rs and
// tests/interpolate_helpers_golden.rs are deliberately untouched.
//
// The public C-ABI under test (native/pure_core/src/interpolate.rs):
//   narwhal_pure_core_interpolate            (open curve)
//   narwhal_pure_core_interpolate_periodic   (closed curve)
//   narwhal_pure_core_skin_control_net       (skin)
//   narwhal_pure_core_interpolate_helper_v1  (ops 1/2/3/4/6/7/8/10)
//
// The cyclic-solver fixture is the EXACT `random.seed(5)` INPUT set from the
// original oracle (orig L304-319): sub/sup/diag/rhs are the seeded inputs, not
// producer outputs, and `diag == 1 - sub - sup`. The check remains cross-algorithm
// (O(n) cyclic solve vs dense LU solve, < 1e-12).

use narwhal_pure_core::interpolate::{
    narwhal_pure_core_interpolate, narwhal_pure_core_interpolate_helper_v1,
    narwhal_pure_core_skin_control_net, InterpolateHelperRequest as Request,
};

fn flat(pts: &[[f64; 3]]) -> Vec<f64> {
    let mut out = Vec::with_capacity(pts.len() * 3);
    for p in pts {
        out.extend_from_slice(p);
    }
    out
}

struct Res {
    code: i32,
    out: Vec<f64>,
}

struct SkinRes {
    code: i32,
    used: u64,
    out: Vec<f64>,
    knots: Vec<f64>,
}

/// One helper transaction, built field-by-field so the call sites stay under
/// clippy's argument limit and only the fields relevant to an op are set.
struct Helper {
    op: u32,
    style: u32,
    rows: u64,
    columns: u64,
    degree: i64,
    index: i64,
    parameter: f64,
    data: Vec<f64>,
}

impl Helper {
    fn new(op: u32, rows: u64, data: Vec<f64>) -> Self {
        Self {
            op,
            style: 0,
            rows,
            columns: 0,
            degree: 0,
            index: 0,
            parameter: 0.0,
            data,
        }
    }
    fn style(mut self, v: u32) -> Self {
        self.style = v;
        self
    }
    fn columns(mut self, v: u64) -> Self {
        self.columns = v;
        self
    }
    fn degree(mut self, v: i64) -> Self {
        self.degree = v;
        self
    }
    fn index(mut self, v: i64) -> Self {
        self.index = v;
        self
    }
    fn parameter(mut self, v: f64) -> Self {
        self.parameter = v;
        self
    }
    fn build(self) -> Res {
        let Helper {
            op,
            style,
            rows,
            columns,
            degree,
            index,
            parameter,
            data,
        } = self;
        let req = Request {
            operation: op,
            style,
            rows,
            columns,
            degree,
            index,
            parameter,
            data: data.as_ptr(),
            data_len: data.len(),
        };
        let (n, dim, p) = (rows as usize, columns as usize, degree as usize);
        let cap = match op {
            1 | 8 => n + 1,
            2 => n + p + 1,
            3 => 1,
            4 => p + 1,
            5 => n * dim,
            6 => n * n + n,
            7 => n * n + n + n * dim,
            9 => n + 2 * p,
            10 => 3 * n + n * dim,
            _ => 0,
        };
        let mut out = vec![f64::NAN; cap];
        let (mut used, mut _detail) = (u64::MAX, u64::MAX);
        let code = unsafe {
            narwhal_pure_core_interpolate_helper_v1(
                &req as *const Request,
                out.as_mut_ptr(),
                out.len(),
                &mut used,
                &mut _detail,
            )
        };
        out.truncate(used as usize);
        Res { code, out }
    }
}

struct Curve {
    code: i32,
    degree: u64,
    nctrl: u64,
    ctrl: Vec<f64>,
    knots: Vec<f64>,
}

fn interp(pts: &[[f64; 3]], degree: i64, style: u8) -> Curve {
    let n = pts.len();
    let data = flat(pts);
    let ctrl_cap = n * 3;
    let knot_cap = n + 8;
    let mut ctrl = vec![f64::NAN; ctrl_cap];
    let mut knots = vec![f64::NAN; knot_cap];
    let (mut d, mut cc, mut kc) = (u64::MAX, u64::MAX, u64::MAX);
    let code = unsafe {
        narwhal_pure_core_interpolate(
            data.as_ptr(),
            n,
            degree,
            style,
            ctrl.as_mut_ptr(),
            ctrl_cap,
            knots.as_mut_ptr(),
            knot_cap,
            &mut d,
            &mut cc,
            &mut kc,
        )
    };
    ctrl.truncate((cc as usize) * 3);
    knots.truncate(kc as usize);
    Curve {
        code,
        degree: d,
        nctrl: cc,
        ctrl,
        knots,
    }
}

fn skin(sections: &[[f64; 3]], k: u64, w: u64, degree: i64, style: u8) -> SkinRes {
    let data: Vec<f64> = sections.iter().flatten().copied().collect();
    let coords = (k as usize) * (w as usize) * 3;
    let mut net = vec![f64::NAN; coords];
    let mut knots = vec![f64::NAN; (k as usize) + 8];
    let (mut q, mut vals, mut kc) = (u64::MAX, u64::MAX, u64::MAX);
    let code = unsafe {
        narwhal_pure_core_skin_control_net(
            data.as_ptr(),
            k as usize,
            w as usize,
            degree,
            style,
            net.as_mut_ptr(),
            coords,
            knots.as_mut_ptr(),
            knots.len(),
            &mut q,
            &mut vals,
            &mut kc,
        )
    };
    net.truncate(vals as usize);
    knots.truncate(kc as usize);
    SkinRes {
        code,
        used: q,
        out: net,
        knots,
    }
}

/// Evaluate a degree-`p` B-spline over `ncv` controls at parameter `u`, via the
/// public span (op 3) + basis (op 4) helpers.
fn eval(ctrl: &[f64], knots: &[f64], p: usize, ncv: usize, u: f64) -> [f64; 3] {
    let s = Helper::new(3, ncv as u64, knots.to_vec())
        .degree(p as i64)
        .parameter(u)
        .build();
    assert_eq!(s.code, 0, "span lookup failed");
    let span = s.out[0] as usize;
    let b = Helper::new(4, ncv as u64, knots.to_vec())
        .degree(p as i64)
        .index(span as i64)
        .parameter(u)
        .build();
    assert_eq!(b.code, 0, "basis evaluation failed");
    let mut out = [0.0f64; 3];
    for (j, w) in b.out.iter().enumerate() {
        let idx = (span - p + j) * 3;
        for d in 0..3 {
            out[d] += w * ctrl[idx + d];
        }
    }
    out
}

fn dist(a: [f64; 3], b: [f64; 3]) -> f64 {
    (0..3).map(|d| (a[d] - b[d]).powi(2)).sum::<f64>().sqrt()
}

const WAVE: [[f64; 3]; 6] = [
    [0.0, 0.0, 0.0],
    [1.0, 2.0, 0.0],
    [3.0, -1.0, 0.0],
    [5.0, 3.0, 0.0],
    [8.0, 0.0, 0.0],
    [10.0, 2.0, 1.0],
];

#[test]
fn parameter_styles_match_independent_constants() {
    let pts = [
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [3.0, 0.0, 0.0],
        [7.0, 0.0, 0.0],
    ];
    // orig L55: Uniform spaces parameters equally, ignoring physical distance.
    let u = Helper::new(1, 4, flat(&pts)).build();
    assert_eq!(u.code, 0);
    assert_eq!(
        u.out,
        vec![0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0],
        "Uniform spacing is exact"
    );
    // orig L59: Chord spaces them by distance (1,2,4 of 7 -> 1/7, 3/7, 1).
    let c = Helper::new(1, 4, flat(&pts)).style(1).build();
    assert_eq!(c.code, 0);
    assert!(
        (c.out[1] - 1.0 / 7.0).abs() < 1e-12,
        "Chord: 1 of 7 -> {}/7",
        c.out[1]
    );
    assert!(
        (c.out[2] - 3.0 / 7.0).abs() < 1e-12,
        "Chord: 3 of 7 -> {}/7",
        c.out[2]
    );
    // orig L63: SqrtChord uses the square root of each distance.
    let tot = 1.0 + 2.0_f64.sqrt() + 2.0;
    let s = Helper::new(1, 4, flat(&pts)).style(2).build();
    assert_eq!(s.code, 0);
    assert!((s.out[1] - 1.0 / tot).abs() < 1e-12, "SqrtChord: 1/tot");
    // orig L65-67: every style is normalised to [0, 1] (first 0, last 1).
    for style in [0u32, 1, 2] {
        let p = Helper::new(1, 4, flat(&pts)).style(style).build();
        assert_eq!(p.code, 0);
        assert_eq!(p.out[0], 0.0, "style {style} starts at 0");
        assert_eq!(*p.out.last().unwrap(), 1.0, "style {style} ends at 1");
    }
}

#[test]
fn knot_vector_is_clamped_and_monotone() {
    let pts = [
        [0.0, 0.0, 0.0],
        [1.0, 1.0, 0.0],
        [2.0, 0.0, 0.0],
        [3.0, 1.0, 0.0],
        [4.0, 0.0, 0.0],
    ];
    let params = Helper::new(1, 5, flat(&pts)).style(1).build();
    assert_eq!(params.code, 0);
    let kn = Helper::new(2, 5, params.out.clone()).degree(3).build();
    assert_eq!(kn.code, 0);
    assert_eq!(kn.out.len(), 9, "n + degree + 1 = 5 + 3 + 1");
    assert!(kn.out[..4].iter().all(|v| *v == 0.0), "first 4 knots are 0");
    assert!(kn.out[5..].iter().all(|v| *v == 1.0), "last 4 knots are 1");
    for w in kn.out.windows(2) {
        assert!(w[0] <= w[1] + 1e-15, "knot vector is non-decreasing");
    }
}

#[test]
fn open_curve_passes_through_every_point() {
    // orig L115-121: a degree-3 curve through 6 points hits every one, per style.
    for style in [0u8, 1, 2] {
        let r = interp(&WAVE, 3, style);
        assert_eq!(r.code, 0);
        assert_eq!(r.degree, 3, "WAVE degree-3 (style {style})");
        assert_eq!(r.nctrl, 6, "one control point per data point");
        let params = Helper::new(1, 6, flat(&WAVE)).style(style as u32).build();
        assert_eq!(params.code, 0);
        let worst = WAVE
            .iter()
            .zip(params.out.iter())
            .map(|(q, &u)| dist(eval(&r.ctrl, &r.knots, 3, 6, u), *q))
            .fold(0.0f64, f64::max);
        assert!(
            worst < 1e-9,
            "style {style}: worst residual {worst:.2e} < 1e-9"
        );
    }
    // orig L123-124: the minimum degree-3 case (4 points) interpolates exactly.
    let pts = [
        [0.0, 0.0, 0.0],
        [1.0, 1.0, 0.0],
        [2.0, 0.0, 0.0],
        [3.0, 1.0, 0.0],
    ];
    let r = interp(&pts, 3, 1);
    assert_eq!(r.code, 0);
    assert_eq!(r.degree, 3, "4-point minimum cubic is degree 3");
    let params = Helper::new(1, 4, flat(&pts)).style(1).build();
    let worst = pts
        .iter()
        .zip(params.out.iter())
        .map(|(q, &u)| dist(eval(&r.ctrl, &r.knots, 3, 4, u), *q))
        .fold(0.0f64, f64::max);
    assert!(
        worst < 1e-9,
        "4-point minimum cubic: worst {worst:.2e} < 1e-9"
    );
    // orig L126-130: 3-D, non-planar, unevenly spaced (an 8-point helix), Chord.
    let helix: Vec<[f64; 3]> = [0.0f64, 0.3, 0.9, 1.1, 2.0, 2.4, 3.6, 5.0]
        .map(|t| [t.cos(), t.sin(), 0.4 * t])
        .into_iter()
        .collect();
    let r = interp(&helix, 3, 1);
    assert_eq!(r.code, 0);
    assert_eq!(r.degree, 3, "8-point helix is degree 3");
    let params = Helper::new(1, 8, flat(&helix)).style(1).build();
    let worst = helix
        .iter()
        .zip(params.out.iter())
        .map(|(q, &u)| dist(eval(&r.ctrl, &r.knots, 3, 8, u), *q))
        .fold(0.0f64, f64::max);
    assert!(
        worst < 1e-9,
        "8-point non-planar helix: worst {worst:.2e} < 1e-9"
    );
}

#[test]
fn degree_clamping_is_exact_and_correct() {
    // orig L138-141: two points clamp down to degree 1 -- a line through 2 points.
    let two = [[0.0, 0.0, 0.0], [2.0, 1.0, 0.0]];
    let r = interp(&two, 3, 1);
    assert_eq!(r.code, 0);
    assert_eq!(r.degree, 1, "two points clamp to degree 1 (a line)");
    assert_eq!(r.nctrl, 2, "two points -> 2 controls");
    let params = Helper::new(1, 2, flat(&two)).style(1).build();
    let worst = two
        .iter()
        .zip(params.out.iter())
        .map(|(q, &u)| dist(eval(&r.ctrl, &r.knots, 1, 2, u), *q))
        .fold(0.0f64, f64::max);
    assert!(worst < 1e-12, "two-point line: worst {worst:.2e} < 1e-12");
    // orig L142-146: three points clamp to degree 2 -- a SMOOTH curve.
    let three = [[0.0, 0.0, 0.0], [1.0, 1.0, 0.0], [3.0, 0.0, 0.0]];
    let r = interp(&three, 3, 1);
    assert_eq!(r.code, 0);
    assert_eq!(r.degree, 2, "three points clamp to degree 2 (smooth)");
    assert_eq!(r.nctrl, 3, "three points -> 3 controls");
    let params = Helper::new(1, 3, flat(&three)).style(1).build();
    let worst = three
        .iter()
        .zip(params.out.iter())
        .map(|(q, &u)| dist(eval(&r.ctrl, &r.knots, 2, 3, u), *q))
        .fold(0.0f64, f64::max);
    assert!(worst < 1e-12, "three-point quad: worst {worst:.2e} < 1e-12");
    // orig L147-149: degree 5 also interpolates exactly.
    let r = interp(&WAVE, 5, 1);
    assert_eq!(r.code, 0);
    assert_eq!(r.degree, 5, "degree 5 is kept (not clamped)");
    assert_eq!(r.nctrl, 6, "six controls");
    let params = Helper::new(1, 6, flat(&WAVE)).style(1).build();
    let worst = WAVE
        .iter()
        .zip(params.out.iter())
        .map(|(q, &u)| dist(eval(&r.ctrl, &r.knots, 5, 6, u), *q))
        .fold(0.0f64, f64::max);
    assert!(worst < 1e-9, "degree-5: worst {worst:.2e} < 1e-9");
}

fn circle_net(radius: f64, z: f64, n: usize) -> Vec<[f64; 3]> {
    (0..n)
        .map(|i| {
            let a = 2.0 * std::f64::consts::PI * (i as f64) / (n as f64 - 1.0);
            [radius * a.cos(), radius * a.sin(), z]
        })
        .collect()
}

#[test]
fn skinning_reproduces_every_section() {
    let sections: Vec<[f64; 3]> = [
        circle_net(5.0, 0.0, 9),
        circle_net(4.0, 3.0, 9),
        circle_net(6.0, 7.0, 9),
        circle_net(5.0, 11.0, 9),
    ]
    .concat();
    let r = skin(&sections, 4, 9, 3, 1);
    assert_eq!(r.code, 0);
    assert_eq!(r.used, 3, "V-degree 3 for 4 sections (orig q == 3)");
    let net_len = r.out.len() / 3;
    assert_eq!(
        net_len, 36,
        "9 U x 4 V = 36 points (orig len(net) == 9, len(net[0]) == 4)"
    );

    // Recover the V parameters the skin used (Chord average over U-columns), then
    // verify each section is reproduced exactly by evaluating its column at that V.
    let mut acc = [0.0f64; 4];
    let mut used = 0u32;
    for i in 0..9 {
        let col: Vec<[f64; 3]> = (0..4).map(|j| sections[j * 9 + i]).collect();
        let p = Helper::new(1, 4, flat(&col)).style(1).build();
        if p.code == 0 {
            used += 1;
            for (j, pv) in p.out.iter().enumerate() {
                acc[j] += *pv;
            }
        }
    }
    let mut vp: Vec<f64> = acc.iter().map(|a| a / used as f64).collect();
    vp[0] = 0.0;
    vp[3] = 1.0;
    // Independent check: the reconstructed V-knots should match the producer's.
    let recon = Helper::new(2, 4, vp.clone()).degree(3).build();
    assert_eq!(recon.code, 0);
    assert_eq!(
        recon.out.len(),
        r.knots.len(),
        "reconstructed knot vector length matches producer"
    );
    for (a, b) in recon.out.iter().zip(r.knots.iter()) {
        assert!(
            (a - b).abs() < 1e-12,
            "reconstructed knot {} ≈ producer knot {}",
            a,
            b
        );
    }

    // Primary check: evaluate using the PRODUCER's knot vector.
    let mut worst = 0.0f64;
    for j in 0..4 {
        let section = &sections[j * 9..(j + 1) * 9];
        for (i, q) in section.iter().enumerate() {
            let col = r.out[(i * 4) * 3..(i * 4 + 4) * 3].to_vec();
            let got = eval(&col, &r.knots, 3, 4, vp[j]);
            worst = worst.max(dist(got, *q));
        }
    }
    assert!(
        worst < 1e-9,
        "every section reproduced: worst {worst:.2e} < 1e-9"
    );

    // orig L205: two sections clamp to degree 1 -- a ruled surface (orig q2 == 1).
    let two = [circle_net(5.0, 0.0, 9), circle_net(4.0, 3.0, 9)].concat();
    let r = skin(&two, 2, 9, 3, 1);
    assert_eq!(r.code, 0);
    assert_eq!(r.used, 1, "two sections -> degree 1 (ruled) (orig q2 == 1)");

    // orig L208: three sections skin to degree 2 -- smooth (orig q3 == 2).
    let three = [
        circle_net(5.0, 0.0, 9),
        circle_net(4.0, 3.0, 9),
        circle_net(6.0, 7.0, 9),
    ]
    .concat();
    let r = skin(&three, 3, 9, 3, 1);
    assert_eq!(r.code, 0);
    assert_eq!(
        r.used, 2,
        "three sections -> degree 2 (smooth) (orig q3 == 2)"
    );
}

#[test]
fn lu_solves_a_3x3_system() {
    let a = [4.0, 1.0, 0.0, 1.0, 4.0, 1.0, 0.0, 1.0, 4.0];
    let lu = Helper::new(6, 3, a.to_vec()).build();
    assert_eq!(lu.code, 0);
    assert_eq!(lu.out.len(), 12, "9 LU + 3 perm");

    // orig L219-225: LU solves a 3x3 system (residual < 1e-12).
    let b1 = [6.0, 12.0, 18.0];
    let mut sol_in = lu.out.clone();
    sol_in.extend_from_slice(&b1);
    let sol = Helper::new(7, 3, sol_in.clone()).columns(1).build();
    assert_eq!(sol.code, 0);
    let residual = (0..3)
        .map(|i| {
            let got: f64 = (0..3).map(|j| a[i * 3 + j] * sol.out[j]).sum();
            (got - b1[i]).abs()
        })
        .fold(0.0f64, f64::max);
    assert!(residual < 1e-12, "LU residual {residual:.2e} < 1e-12");

    // orig L226-229: one factorisation serves several right-hand sides.
    let b2 = [6.0, 1.0, 12.0, 2.0, 18.0, 3.0];
    let mut multi_in = lu.out.clone();
    multi_in.extend_from_slice(&b2);
    let multi = Helper::new(7, 3, multi_in.clone()).columns(2).build();
    assert_eq!(multi.code, 0);
    assert_eq!(multi.out.len(), 6, "3 rows x 2 cols");
    assert!(
        (multi.out[0] - sol.out[0]).abs() < 1e-12,
        "shared factorisation"
    );
}

#[test]
fn closed_parameters_close_the_ring() {
    let ring = [
        [0.0, 0.0, 0.0],
        [4.0, 1.0, 0.0],
        [7.0, 5.0, 1.0],
        [3.0, 9.0, 0.0],
        [-2.0, 6.0, 2.0],
        [-3.0, 2.0, 0.0],
    ];
    // orig L304-308: closed parameters run once around, closing segment included.
    for style in [0u32, 1, 2] {
        let us = Helper::new(8, 6, flat(&ring)).style(style).build();
        assert_eq!(us.code, 0);
        assert_eq!(us.out.len(), 7, "n + 1 parameters");
        assert_eq!(us.out[0], 0.0, "starts at 0");
        assert_eq!(us.out[6], 1.0, "ends at 1");
    }
    // orig L309-311: Chord -- the closing segment's share is its chord length.
    let chord = Helper::new(8, 6, flat(&ring)).style(1).build();
    let d_close = dist(ring[5], ring[0]);
    let d_total: f64 = (0..6).map(|k| dist(ring[k], ring[(k + 1) % 6])).sum();
    assert!(
        (chord.out[5] - (1.0 - d_close / d_total)).abs() < 1e-15,
        "closing segment share"
    );
}

/// Cross-algorithm check (orig L315-319): the O(n) cyclic solve equals the dense LU
/// solve on the same wrapped system, for the EXACT `random.seed(5)` inputs.
fn cyclic_check(n: usize, sub: &[f64], sup: &[f64], diag: &[f64], rhs: &[f64]) {
    // Build the wrapped dense system exactly as the original Python oracle did:
    //   dense[i][(i-1)%n] += sub[i];  dense[i][i] += diag[i];  dense[i][(i+1)%n] += sup[i]
    let mut dense = vec![0.0f64; n * n];
    for i in 0..n {
        dense[i * n + (i + n - 1) % n] += sub[i];
        dense[i * n + i] += diag[i];
        dense[i * n + (i + 1) % n] += sup[i];
    }

    let lu = Helper::new(6, n as u64, dense.clone()).build();
    assert_eq!(lu.code, 0, "dense LU failed for n={n}");
    let mut dense_in = lu.out.clone();
    dense_in.extend_from_slice(rhs);
    let want = Helper::new(7, n as u64, dense_in.clone())
        .columns(3)
        .build();
    assert_eq!(want.code, 0, "dense solve failed for n={n}");

    let mut cyclic_in = Vec::with_capacity(3 * n + n * 3);
    cyclic_in.extend_from_slice(sub);
    cyclic_in.extend_from_slice(diag);
    cyclic_in.extend_from_slice(sup);
    cyclic_in.extend_from_slice(rhs);
    let got = Helper::new(10, n as u64, cyclic_in.clone())
        .columns(3)
        .build();
    assert_eq!(got.code, 0, "cyclic solve failed for n={n}");

    let max_diff = want
        .out
        .iter()
        .zip(got.out.iter())
        .map(|(a, b)| (a - b).abs())
        .fold(0.0f64, f64::max);
    assert!(
        max_diff < 1e-12,
        "n={n}: cyclic vs dense {max_diff:.2e} < 1e-12"
    );
}

#[test]
fn cyclic_tridiagonal_matches_dense_lu() {
    // EXACT original seeded inputs, captured one-time from the original oracle's
    // `random.seed(5)` (spike/test_interpolate.py.orig L304-319). These are INPUTS
    // (not producer outputs); `diag == 1 - sub - sup` in every row.
    cyclic_check(
        3,
        &[
            0.20572542372242547,
            0.23544674731518234,
            0.24879839139142418,
        ],
        &[
            0.28561257094426257,
            0.23497464368498266,
            0.28058124916635424,
        ],
        &[0.508662005333312, 0.529578608999835, 0.4706203594422215],
        &[
            0.029005228283614737,
            0.46562265437810535,
            0.9433567169983137,
            0.6489745531369242,
            0.9009004917506227,
            0.11320596465314436,
            0.46906904778216374,
            0.24657283261983032,
            0.5437608592359304,
        ],
    );
    cyclic_check(
        4,
        &[
            0.1934852969820252,
            0.053278547397225554,
            0.10418245011596204,
            0.11987059150277758,
        ],
        &[
            0.27908634295213797,
            0.24143136290728545,
            0.08990105308950956,
            0.24928674785780114,
        ],
        &[
            0.5274283600658368,
            0.705290089695489,
            0.8059164967945284,
            0.6308426606394213,
        ],
        &[
            0.13876741839890316,
            0.6174525204661166,
            0.1266992325502697,
            0.0017748622025346439,
            0.8714047447242821,
            0.2094563824951179,
            0.21548116922473226,
            0.9824211088259253,
            0.8724077654368019,
            0.2893051677469265,
            0.9614779889500835,
            0.5392234688708106,
        ],
    );
    cyclic_check(
        9,
        &[
            0.21945761931264807,
            0.10119487863344821,
            0.28524400027199975,
            0.22266048527672705,
            0.29164107807929884,
            0.27343541939411964,
            0.12469722446346694,
            0.14029748368059602,
            0.0914890142824364,
        ],
        &[
            0.08642547738517063,
            0.06628492834391907,
            0.1253397751923656,
            0.20077749935191358,
            0.05084577984358919,
            0.2194835623869228,
            0.1344742154069663,
            0.12748948290078221,
            0.2546295186617677,
        ],
        &[
            0.6941169033021813,
            0.8325201930226327,
            0.5894162245356346,
            0.5765620153713593,
            0.657513142077112,
            0.5070810182189576,
            0.7408285601295668,
            0.7322130334186218,
            0.653881467055796,
        ],
        &[
            0.48074518663003896,
            0.31579310584644404,
            0.48121838623686386,
            0.7046691341409093,
            0.057000929535789946,
            0.9750995631442353,
            0.02286556325272071,
            0.7497950222912733,
            0.8448808893881297,
            0.01806753537853012,
            0.7877383039804342,
            0.36618447584186054,
            0.5785188290568746,
            0.009078386819528439,
            0.04672711869894153,
            0.18091948795104784,
            0.9551798995911663,
            0.19652167051300817,
            0.7557364124513177,
            0.9296553195975211,
            0.9420438294276994,
            0.34438181305301196,
            0.35479320505791223,
            0.52470182069312,
            0.7756030146989953,
            0.10805286906483291,
            0.7483980564846631,
        ],
    );
}
