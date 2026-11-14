//! Uniform samples of a `LineCurve`: the `PointAt` loop of `bridge.sample_curve`.
//!
//! `bridge.sample_curve` and `curve_snap_target` sample a curve at `T0 + span * i / n`
//! for `i` in `0..=n`. For a rhino3dm `LineCurve` each `PointAt` is `ON_LineCurve::Evaluate` followed by
//! `ON_Line::PointAt` (opennurbs_linecurve.cpp and opennurbs_line.cpp at eb92af3, the
//! commit rhino3dm 8.35.0 pins). This module repeats both in their operation order:
//!
//! * `Evaluate`: `s = 1.0` when `t == T1`, else `(t - T0) / (T1 - T0)`;
//! * `PointAt(s)`: `from + s * (to - from)` below 0.5, else `to + (1 - s) * (from - to)`,
//!   one coordinate at a time.
//!
//! Every operation is one IEEE-754 multiply, add, subtract or divide; Rust never fuses
//! them. So the samples are bit-identical to rhino3dm's wherever rhino3dm does not fuse
//! them either. The Python adapter (`native_nearest.sample`) admits only a qualified
//! platform whose loaded rhino3dm also reproduces these bits on its witness lines. The
//! parameters are `bridge.sample_curve`'s own, computed with Python's float semantics
//! (`span * i` then `/ n`).

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
/// The snap target asks for 128 intervals; a larger request is refused by name.
pub const MAX_INTERVALS: usize = 1 << 20;

/// `[PointAt(T0 + span * i / n) for i in 0..=n]`, flattened `x, y, z` into `out`.
///
/// `line` is `from` then `to`. The caller checks that `t0 < t1` are finite, `n >= 1` and
/// `out.len() == 3 * (n + 1)`.
pub fn samples(line: &[f64; 6], t0: f64, t1: f64, n: usize, out: &mut [f64]) {
    let (from, to) = (&line[..3], &line[3..]);
    let d: [f64; 3] = std::array::from_fn(|k| to[k] - from[k]);
    let e: [f64; 3] = std::array::from_fn(|k| from[k] - to[k]);
    let span = t1 - t0;
    for (i, p) in out.chunks_exact_mut(3).enumerate() {
        let t = t0 + span * i as f64 / n as f64;
        let s = if t == t1 { 1.0 } else { (t - t0) / (t1 - t0) };
        if s < 0.5 {
            for k in 0..3 {
                p[k] = from[k] + s * d[k];
            }
        } else {
            let r = 1.0 - s;
            for k in 0..3 {
                p[k] = to[k] + r * e[k];
            }
        }
    }
}

/// Write `curve_snap_target`'s `n + 1` samples of the line `line[0..6]` (`from`, `to`)
/// over the domain `[t0, t1]` to `out[..3 * (n + 1)]`.
///
/// Returns 0; -2, writing nothing, for a non-finite coordinate or parameter, `t0 >= t1`
/// (where `Evaluate` fails), `n` of 0 or above [`MAX_INTERVALS`]; -3, writing nothing,
/// when `capacity < 3 * (n + 1)`; -1 for an invalid pointer.
///
/// # Safety
///
/// `line` must reference 6 readable doubles and `out` `capacity` writable doubles.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_line_samples(
    line: *const f64,
    t0: f64,
    t1: f64,
    n: usize,
    out: *mut f64,
    capacity: usize,
) -> i32 {
    let Some(line) = (unsafe { crate::checked_slice(line, 6) }) else {
        return ERR_NULL;
    };
    let line: &[f64; 6] = line.try_into().unwrap();
    let finite = line.iter().chain([&t0, &t1]).all(|v| v.is_finite());
    if !finite || t0 >= t1 || n == 0 || n > MAX_INTERVALS {
        return ERR_INPUT;
    }
    let len = 3 * (n + 1);
    if capacity < len {
        return ERR_SIZE;
    }
    let Some(out) = (unsafe { crate::checked_slice_mut(out, len) }) else {
        return ERR_NULL;
    };
    samples(line, t0, t1, n, out);
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    // rhino3dm 8.35.0 LineCurve: Line.From, Line.To, Domain T0, T1; then PointAt(T0 + span * i / 7) for i in 0..=7.
    const LINE_N: usize = 7;
    const LINES: [([u64; 8], [u64; 24]); 3] = [
        (
            [
                0x8000000000000000,
                0x3fb999999999999a,
                0x4008000000000000,
                0x401c000000000000,
                0x8000000000000000,
                0x3fd5555555555555,
                0x0000000000000000,
                0x401df732362bf072,
            ],
            [
                0x0000000000000000,
                0x3fb999999999999a,
                0x4008000000000000,
                0x3ff0000000000000,
                0x3fb5f15f15f15f16,
                0x4004f3cf3cf3cf3d,
                0x4000000000000000,
                0x3fb2492492492493,
                0x4001e79e79e79e7a,
                0x4008000000000000,
                0x3fad41d41d41d41d,
                0x3ffdb6db6db6db6e,
                0x4010000000000000,
                0x3fa5f15f15f15f17,
                0x3ff79e79e79e79e7,
                0x4013ffffffffffff,
                0x3f9d41d41d41d420,
                0x3ff1861861861862,
                0x4018000000000000,
                0x3f8d41d41d41d41a,
                0x3fe6db6db6db6db5,
                0x401c000000000000,
                0x0000000000000000,
                0x3fd5555555555555,
            ],
        ),
        (
            [
                0x430c6bf526340000,
                0xc2ec6bf526340000,
                0x4303e52b9abe0000,
                0xc2f10d9316ec0000,
                0x4309945ca2620000,
                0x430c6bf526340000,
                0xc00d99999999999a,
                0x4026333333333333,
            ],
            [
                0x430c6bf526340000,
                0xc2ec6bf526340000,
                0x4303e52b9abe0000,
                0x430724b555f72492,
                0xc2d37d3a6356db6e,
                0x43051cff40f36db7,
                0x4301dd7585ba4924,
                0x42d1dd7585ba4924,
                0x430654d2e728db6e,
                0x42f92c6b6afadb6d,
                0x42eb9c12b765b6dc,
                0x43078ca68d5e4925,
                0x42ed3bd795024926,
                0x42f724b555f72492,
                0x4308c47a3393b6db,
                0x42d03db0a81db6e4,
                0x43003db0a81db6da,
                0x4309fc4dd9c92492,
                0xc2d9fc4dd9c92498,
                0x4304e906a53fdb6e,
                0x430b34217ffe9249,
                0xc2f10d9316ec0000,
                0x4309945ca2620000,
                0x430c6bf526340000,
            ],
        ),
        (
            [
                0x3e112e0be826d695,
                0x3e212e0be826d695,
                0x8000000000000000,
                0x3e112e0be826d695,
                0xbe080d43de9cc603,
                0x3e35798ee2308c3a,
                0x412e848000000000,
                0x412e84809999999a,
            ],
            [
                0x3e112e0be826d695,
                0x3e212e0be826d695,
                0x0000000000000000,
                0x3e112e0be826d695,
                0x3e1bbbb422a51892,
                0x3e088aec70377bb0,
                0x3e112e0be826d695,
                0x3e151b5074fc83f9,
                0x3e188aec70377bb0,
                0x3e112e0be826d695,
                0x3e0cf5d98ea7dec2,
                0x3e22683154299cc4,
                0x3e112e0be826d695,
                0x3dff6a2466ad6b22,
                0x3e288aec70377bb0,
                0x3e112e0be826d695,
                0x3dd3a256c02c62f8,
                0x3e2eada78c455a9c,
                0x3e112e0be826d695,
                0xbdf598f9069739a1,
                0x3e32683154299cc4,
                0x3e112e0be826d695,
                0xbe080d43de9cc603,
                0x3e35798ee2308c3a,
            ],
        ),
    ];

    fn call(line: &[f64; 6], t0: f64, t1: f64, n: usize, capacity: usize) -> (i32, Vec<f64>) {
        let mut out = vec![-9.0; capacity];
        let status = unsafe {
            narwhal_pure_core_line_samples(line.as_ptr(), t0, t1, n, out.as_mut_ptr(), capacity)
        };
        (status, out)
    }

    #[test]
    fn samples_are_point_at_bit_for_bit() {
        for (head, want) in LINES {
            let v = head.map(f64::from_bits);
            let line: [f64; 6] = v[..6].try_into().unwrap();
            let (status, out) = call(&line, v[6], v[7], LINE_N, 3 * (LINE_N + 1) + 2);
            assert_eq!(status, 0);
            let got: Vec<u64> = out[..want.len()].iter().map(|x| x.to_bits()).collect();
            assert_eq!(got, want);
            assert_eq!(out[want.len()..], [-9.0, -9.0]);
        }
    }

    #[test]
    fn both_point_at_branches_and_the_end_are_taken() {
        // n = 4 on [0, 1]: s = 0, 0.25 take `from + s*d`; 0.5, 0.75 and t == T1 take
        // `to + (1 - s)*e`. At s = 1 that is `to + 0*e`, so `to`'s -0.0 becomes +0.0, as
        // it does in openNURBS.
        let line = [0.1, 0.2, 0.3, 0.7, -0.0, 1e300];
        let (status, out) = call(&line, 0.0, 1.0, 4, 15);
        assert_eq!(status, 0);
        assert_eq!(out[..3], [0.1, 0.2, 0.3]);
        assert_eq!(out[12..], [0.7, 0.0, 1e300]);
        assert_eq!(out[13].to_bits(), 0);
        assert_eq!(out[6], 0.7 + 0.5 * (0.1 - 0.7));
        assert_eq!(out[3], 0.1 + 0.25 * (0.7 - 0.1));
    }

    #[test]
    fn refusals_write_nothing() {
        let line = [0.0, 0.0, 0.0, 1.0, 2.0, 3.0];
        for (t0, t1, n) in [
            (1.0, 1.0, 4),
            (2.0, 1.0, 4),
            (f64::NAN, 1.0, 4),
            (0.0, f64::INFINITY, 4),
            (0.0, 1.0, 0),
            (0.0, 1.0, MAX_INTERVALS + 1),
            (0.0, 1.0, usize::MAX),
        ] {
            let (status, out) = call(&line, t0, t1, n, 15);
            assert_eq!(status, ERR_INPUT, "{t0} {t1} {n}");
            assert!(out.iter().all(|&v| v == -9.0));
        }
        let mut bad = line;
        bad[4] = f64::NAN;
        assert_eq!(call(&bad, 0.0, 1.0, 4, 15).0, ERR_INPUT);
        let (status, out) = call(&line, 0.0, 1.0, 4, 14);
        assert_eq!(status, ERR_SIZE);
        assert!(out.iter().all(|&v| v == -9.0));
        let mut out = [0.0; 16];
        unsafe {
            assert_eq!(
                narwhal_pure_core_line_samples(std::ptr::null(), 0.0, 1.0, 4, out.as_mut_ptr(), 15),
                ERR_NULL
            );
            assert_eq!(
                narwhal_pure_core_line_samples(
                    line.as_ptr(),
                    0.0,
                    1.0,
                    4,
                    std::ptr::null_mut(),
                    15
                ),
                ERR_NULL
            );
            let misaligned = (out.as_mut_ptr() as *mut u8).add(1).cast::<f64>();
            assert_eq!(
                narwhal_pure_core_line_samples(line.as_ptr(), 0.0, 1.0, 4, misaligned, 15),
                ERR_NULL
            );
        }
        assert_eq!(
            call(&line, 0.0, 1.0, MAX_INTERVALS, 3 * (MAX_INTERVALS + 1)).0,
            0
        );
    }

    #[test]
    fn malformed_abi_input_is_refused_without_a_panic() {
        let mut state = 20261118u64;
        let mut next = || {
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            state >> 11
        };
        let awkward = [
            0.0,
            -0.0,
            1.0,
            -1.0,
            1e300,
            -1e300,
            5e-324,
            f64::NAN,
            f64::INFINITY,
        ];
        let mut answered = 0;
        for _ in 0..20000 {
            let mut pick = || awkward[(next() % awkward.len() as u64) as usize];
            let line = [pick(), pick(), pick(), pick(), pick(), pick()];
            let (t0, t1) = (pick(), pick());
            let n = [0, 1, 2, 7, 128, usize::MAX, MAX_INTERVALS + 1][(next() % 7) as usize];
            let capacity = (next() % 400) as usize;
            let (status, out) = call(&line, t0, t1, n, capacity);
            match status {
                0 => {
                    assert!(capacity >= 3 * (n + 1));
                    answered += 1;
                }
                ERR_INPUT | ERR_SIZE => assert!(out.iter().all(|&v| v == -9.0)),
                other => panic!("status {other}"),
            }
        }
        assert!(answered > 0);
    }
}
