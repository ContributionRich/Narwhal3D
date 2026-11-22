//! Uniform samples of a `LineCurve` or a `PolylineCurve`: the `PointAt` loop of
//! `bridge.sample_curve`.
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
//! For a `PolylineCurve` each `PointAt` is `ON_PolylineCurve::Evaluate` (opennurbs_polylinecurve.cpp
//! at eb92af3), which `curvex::Polyline` already ports for the curve/curve recursion and
//! the closest-point search: the segment from `ON_NurbsSpanIndex`, then
//! `(1 - s) * P[i] + s * P[i+1]`.
//!
//! Every operation is one IEEE-754 multiply, add, subtract or divide; Rust never fuses
//! them. So the samples are bit-identical to rhino3dm's wherever rhino3dm does not fuse
//! them either. The Python adapter (`native_nearest.sample`) admits only a qualified
//! platform whose loaded rhino3dm also reproduces these bits on its witness curves. The
//! parameters are `bridge.sample_curve`'s own, computed with Python's float semantics
//! (`span * i` then `/ n`).

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;
const NOT_RECOGNISED: i32 = 1;
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
/// (where `Evaluate` fails), overflowing differences/parameter bounds, `n` of 0 or
/// above [`MAX_INTERVALS`]; -3, writing nothing,
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
    // Refuse finite inputs whose differences or parameter construction can overflow.
    // The SDK remains the compatibility route; never publish non-finite native samples.
    let span = t1 - t0;
    if !span.is_finite()
        || !(span * n as f64).is_finite()
        || !(t0.abs() + span).is_finite()
        || (0..3).any(|k| !(line[k + 3] - line[k]).is_finite())
    {
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

/// Write `curve_snap_target`'s `n + 1` samples of the `PolylineCurve` whose encoded record
/// (`base64.b64decode(curve.Encode()["data"])`) is `record[..record_len]` to
/// `out[..3 * (n + 1)]`. The domain is the record's `m_t` ends, which are the SDK's `Domain`.
///
/// Returns 0; 1, writing nothing, for a record that is not a 3-D polyline `curvex` accepts
/// (the host then calls `PointAt`); -2, writing nothing, for `n` of 0 or above
/// [`MAX_INTERVALS`], a domain whose parameter construction overflows, or a non-finite
/// sample; -3, writing nothing, when `capacity < 3 * (n + 1)`; -1 for an invalid pointer
/// or an `out` that overlaps `record`.
///
/// # Safety
///
/// `record` must reference `record_len` readable bytes and `out` `capacity` writable doubles.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_polyline_samples(
    record: *const u8,
    record_len: usize,
    n: usize,
    out: *mut f64,
    capacity: usize,
) -> i32 {
    if n == 0 || n > MAX_INTERVALS {
        return ERR_INPUT;
    }
    let len = 3 * (n + 1);
    if capacity < len {
        return ERR_SIZE;
    }
    let (start, end) = (out as usize, (out as usize).wrapping_add(8 * len));
    let first = record as usize;
    if first < end && start < first.saturating_add(record_len) {
        return ERR_NULL;
    }
    let Some(data) = (unsafe { crate::checked_slice(record, record_len) }) else {
        return ERR_NULL;
    };
    let Some(curve) = crate::curvex::polyline_from_record(data) else {
        return NOT_RECOGNISED;
    };
    // `polyline_from_record` admits only strictly increasing finite parameters.
    let (t0, t1) = curve.ends();
    let span = t1 - t0;
    if !span.is_finite() || !(span * n as f64).is_finite() || !(t0.abs() + span).is_finite() {
        return ERR_INPUT;
    }
    // Stage every sample so a refusal cannot publish a prefix.
    let mut staged = Vec::with_capacity(len);
    for i in 0..=n {
        let p = curve.point(t0 + span * i as f64 / n as f64);
        if !p.iter().all(|v| v.is_finite()) {
            return ERR_INPUT;
        }
        staged.extend(p);
    }
    let Some(out) = (unsafe { crate::checked_slice_mut(out, len) }) else {
        return ERR_NULL;
    };
    out.copy_from_slice(&staged);
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
    fn finite_overflow_is_refused_before_writing() {
        let ordinary = [0.0, 0.0, 0.0, 1.0, 2.0, 3.0];
        let huge = [-f64::MAX, 0.0, 0.0, f64::MAX, 2.0, 3.0];
        for (line, t0, t1, n) in [
            (huge, 0.0, 1.0, 4),
            (ordinary, -f64::MAX, f64::MAX, 4),
            (ordinary, 0.0, f64::MAX / 2.0, 4),
            (ordinary, -f64::MAX, -f64::MAX / 2.0, 1),
        ] {
            assert!(line.iter().chain([&t0, &t1]).all(|x| x.is_finite()));
            let (status, out) = call(&line, t0, t1, n, 15);
            assert_eq!(status, ERR_INPUT);
            assert_eq!(out, vec![-9.0; 15]);
        }
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

#[cfg(test)]
mod polyline_tests {
    use super::*;
    use crate::curvex::{polyline_record, tests::unhex};

    struct Case {
        name: &'static str,
        record: &'static str,
        n: usize,
        samples: &'static [[u64; 3]],
    }

    // rhino3dm 8.35.0 PolylineCurve records and PointAt(T0 + span * i / n) for i in 0..=n,
    // captured with the SDK (the PR's scratch generator).
    const POLYLINES: [Case; 4] = [
        Case {
            name: "an irregular 3-D zigzag on a shifted domain",
            record: "fa7f02000901000000000000fbff02001400000000000000e6d4d74e47e9d311bfe50010830122f04e9cbbdbfcff0200d10000000000000010060000001baf48800ce50dc0c37faf433b62e83f88e75ea35e63d3bf29fa8c0a47e001c035a53808a919f0bf6a9d50e0debdefbfcde903d6b154e9bf512e0d1f89adf03f0ab5afa65913e5bf303dd6c89683f23f74a80d79b772f1bff81b5eb672f5d3bfc81f78ddc4520840183c74a203c0eb3f1e3c9fe02cc1ea3f27da5273dcec1140ae53e4b8a8bfefbf4edf27c6ea1deabf06000000a0e3af71760b3ac0ac2c6444ef092ec06048a295c6f30fc0f41026f317201c40885147cc840e3240d11ec59b03153d40030000003eac34aaff7f02800000000000000000",
            n: 7,
            samples: &[
                [0xc00de50c8048af1b, 0x3fe8623b43af7fc3, 0xbfd3635ea35ee788],
                [0xc0054f5ae30bc025, 0xbfe00897661ec780, 0xbfe9713f00b4daa8],
                [0xbff9db9bde7d624a, 0xbfc06b77bb1ee6c6, 0xbfeb2ba5c79df0b0],
                [0xbfe06c2434eab6f2, 0x3fe79affc9806871, 0xbfe37d8be50b0ae4],
                [0x3fec1e9ff093477f, 0xbfe9255c7d815cb0, 0xbfd7210e39a77045],
                [0x4001ddc7a9971f45, 0x3f9cda50eb691940, 0x3fd605dd8dfb5d45],
                [0x400b9de604a9b7ec, 0x3fd5803710b5dcfc, 0x3fd74c0318ccb1cc],
                [0x4011ecdc7352da26, 0xbfefbfa8b8e453a8, 0xbfea1deac627df49],
            ],
        },
        Case {
            name: "a closed rectangle with -0.0 coordinates",
            record: "fa7f0200e900000000000000fbff02001400000000000000e6d4d74e47e9d311bfe50010830122f04e9cbbdbfcff0200b100000000000000100500000000000000000000800000000000000080000000000000000087ec59ba1a470740000000000000008000000000000000000129c0faed2a04403729cf830bcbfd3f0000000000000080000000000000008084e0dc1ef94ffa3f0000000000000000000000000000008000000000000000800000000000000000050000000000000000000000000000000000f03f00000000000000400000000000000840000000000000104003000000e819359fff7f02800000000000000000",
            n: 10,
            samples: &[
                [0x0000000000000000, 0x8000000000000000, 0x0000000000000000],
                [0x3ff29f489514bd39, 0x8000000000000000, 0x0000000000000000],
                [0x40029f489514bd39, 0x8000000000000000, 0x0000000000000000],
                [0x4006a7de9407f89f, 0x3fd7d5a2cfd8edc4, 0x0000000000000000],
                [0x40056966476410d0, 0x3ff1e03a1be2b255, 0x0000000000000000],
                [0x40042aedfac02901, 0x3ffdcb0b83cf2937, 0x0000000000000000],
                [0x3ff83383f9b36469, 0x3ffc669dc1d4a5bc, 0x0000000000000000],
                [0x3fe02257fbcced9e, 0x3ffb022fffda2242, 0x0000000000000000],
                [0x8000000000000000, 0x3ff50cc74be3e6cf, 0x0000000000000000],
                [0x8000000000000000, 0x3fe50cc74be3e6cf, 0x0000000000000000],
                [0x8000000000000000, 0x0000000000000000, 0x0000000000000000],
            ],
        },
        Case {
            name: "a repeated point (IsValid false)",
            record: "fa7f0200c900000000000000fbff02001400000000000000e6d4d74e47e9d311bfe50010830122f04e9cbbdbfcff0200910000000000000010040000009a9999999999b93f9a9999999999c93f333333333333d33f9a9999999999b93f9a9999999999c93f333333333333d33f333333333333fb3f33333333333307c0333333333333d33f66666666666610403333333333331540666666666666e6bf040000000000000000000000000000000000f03f0000000000000040000000000000084003000000839306d8ff7f02800000000000000000",
            n: 9,
            samples: &[
                [0x3fb999999999999a, 0x3fc999999999999a, 0x3fd3333333333333],
                [0x3fb999999999999a, 0x3fc999999999999a, 0x3fd3333333333333],
                [0x3fb999999999999a, 0x3fc999999999999a, 0x3fd3333333333333],
                [0x3fb999999999999a, 0x3fc999999999999a, 0x3fd3333333333333],
                [0x3fe4444444444443, 0xbfeaaaaaaaaaaaa8, 0x3fd3333333333333],
                [0x3ff2aaaaaaaaaaac, 0xbffddddddddddddf, 0x3fd3333333333333],
                [0x3ffb333333333333, 0xc007333333333333, 0x3fd3333333333333],
                [0x4004000000000000, 0xbfc5555555555528, 0xbfa1111111111124],
                [0x400a666666666665, 0x4004888888888885, 0xbfd7777777777774],
                [0x4010666666666666, 0x4015333333333333, 0xbfe6666666666666],
            ],
        },
        Case {
            name: "a last parameter one ulp past T1",
            record: "fa7f0200c900000000000000fbff02001400000000000000e6d4d74e47e9d311bfe50010830122f04e9cbbdbfcff020091000000000000001004000000a2b4722b4f2b1240b6335d5e75701940000000000000008010b1e14377da1fc09d6c5f7a35f320c0000000000000008080da73126168d03f8894083748e21240000000000000008020f13071ff24f4bf0000000000000080000000000000008004000000a4e10b34700e76c0ebb80259d8c971c06220f3fb800a6bc0efcee045518162c00300000049d52549ff7f02800000000000000000",
            n: 5,
            samples: &[
                [0x40122b4f2b72b4a2, 0x401970755e5d33b6, 0x8000000000000000],
                [0xc007b04fc84caa68, 0xc004542273cda825, 0x8000000000000000],
                [0xc019471166131ae7, 0xc01757e0b8ca29a6, 0x8000000000000000],
                [0xbff6337f329d61e5, 0x4000a77bf627ca01, 0x8000000000000000],
                [0xbfd662f8106f31e4, 0x4006a923753d7f0b, 0x8000000000000000],
                [0xbff424ff7130f124, 0xbce2e24837089488, 0x0000000000000000],
            ],
        },
    ];

    fn call(record: &[u8], n: usize, capacity: usize) -> (i32, Vec<f64>) {
        let mut out = vec![-9.0; capacity];
        let status = unsafe {
            narwhal_pure_core_polyline_samples(
                record.as_ptr(),
                record.len(),
                n,
                out.as_mut_ptr(),
                capacity,
            )
        };
        (status, out)
    }

    #[test]
    fn samples_are_point_at_bit_for_bit() {
        for case in &POLYLINES {
            let record = unhex(case.record);
            let len = 3 * (case.n + 1);
            let (status, out) = call(&record, case.n, len + 2);
            assert_eq!(status, 0, "{}", case.name);
            let got: Vec<[u64; 3]> = out[..len]
                .chunks_exact(3)
                .map(|p| [p[0].to_bits(), p[1].to_bits(), p[2].to_bits()])
                .collect();
            assert_eq!(got, case.samples, "{}", case.name);
            assert_eq!(out[len..], [-9.0, -9.0], "{}", case.name);
        }
    }

    #[test]
    fn the_witness_detects_a_fused_multiply_add() {
        // `(1 - s) * P[i] + s * P[i+1]` with one fused multiply-add, as an FMA-contracting
        // SDK build computes it, misses the SDK's bits on the irregular witness: so the
        // witness (also the Python parity check's first curve) tells the two apart.
        let case = &POLYLINES[0];
        let record = unhex(case.record);
        let polyline = crate::nurbs_record::read_polyline(&record).unwrap();
        let (points, t) = polyline.arrays();
        let (t0, t1) = (t[0], t[t.len() - 1]);
        let differ = (0..=case.n)
            .filter(|&i| {
                let u = t0 + (t1 - t0) * i as f64 / case.n as f64;
                let k = t[..t.len() - 1].iter().rposition(|&x| x <= u).unwrap_or(0);
                let k = k.min(t.len() - 2);
                let s = if u == t[k + 1] {
                    1.0
                } else {
                    (u - t[k]) / (t[k + 1] - t[k])
                };
                let fused: [u64; 3] = std::array::from_fn(|j| {
                    (1.0 - s)
                        .mul_add(points[k][j], s * points[k + 1][j])
                        .to_bits()
                });
                fused != case.samples[i]
            })
            .count();
        assert!(differ > 0, "a fused blend matched every SDK sample");
    }

    #[test]
    fn refusals_write_nothing() {
        let record = unhex(POLYLINES[1].record);
        for n in [0, MAX_INTERVALS + 1, usize::MAX] {
            let (status, out) = call(&record, n, 30);
            assert_eq!(status, ERR_INPUT, "{n}");
            assert!(out.iter().all(|&v| v == -9.0));
        }
        let (status, out) = call(&record, 4, 14);
        assert_eq!(status, ERR_SIZE);
        assert!(out.iter().all(|&v| v == -9.0));
        for bad in [&b"not a record"[..], &record[..record.len() - 1], &[]] {
            let (status, out) = call(bad, 4, 15);
            assert_eq!(status, NOT_RECOGNISED);
            assert!(out.iter().all(|&v| v == -9.0));
        }
        // one point, and parameters that do not increase, are not polylines curvex accepts
        let one = polyline_record(&[[0.0; 3]], &[0.0]);
        let flat = polyline_record(&[[0.0; 3], [1.0, 0.0, 0.0]], &[1.0, 1.0]);
        for record in [one, flat] {
            assert_eq!(call(&record, 4, 15).0, NOT_RECOGNISED);
        }
        // finite (ON_IS_VALID) domains whose span, or span * n, overflows
        for params in [[-1e308, 1e308], [0.0, 1e308]] {
            let wide = polyline_record(&[[0.0; 3], [1.0, 2.0, 3.0]], &params);
            let (status, out) = call(&wide, 4, 15);
            assert_eq!(status, ERR_INPUT, "{params:?}");
            assert!(out.iter().all(|&v| v == -9.0));
        }
        let mut out = [0.0; 16];
        unsafe {
            assert_eq!(
                narwhal_pure_core_polyline_samples(std::ptr::null(), 5, 4, out.as_mut_ptr(), 15),
                ERR_NULL
            );
            assert_eq!(
                narwhal_pure_core_polyline_samples(
                    record.as_ptr(),
                    record.len(),
                    4,
                    std::ptr::null_mut(),
                    15
                ),
                ERR_NULL
            );
            let misaligned = (out.as_mut_ptr() as *mut u8).add(1).cast::<f64>();
            assert_eq!(
                narwhal_pure_core_polyline_samples(
                    record.as_ptr(),
                    record.len(),
                    4,
                    misaligned,
                    15
                ),
                ERR_NULL
            );
            // an output that overlaps the record it reads
            let mut shared = vec![0.0f64; record.len().div_ceil(8) + 15];
            let bytes = shared.as_mut_ptr().cast::<u8>();
            std::ptr::copy_nonoverlapping(record.as_ptr(), bytes, record.len());
            assert_eq!(
                narwhal_pure_core_polyline_samples(bytes, record.len(), 4, shared.as_mut_ptr(), 15),
                ERR_NULL
            );
        }
        assert_eq!(call(&record, MAX_INTERVALS, 3 * (MAX_INTERVALS + 1)).0, 0);
    }

    #[test]
    fn malformed_records_are_refused_without_a_panic() {
        let mut state = 20261121u64;
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
        for k in 0..20000 {
            let mut record = if k % 2 == 0 {
                let count = (next() % 6) as usize;
                let mut pick = || awkward[(next() % awkward.len() as u64) as usize];
                let points: Vec<[f64; 3]> = (0..count).map(|_| [pick(), pick(), pick()]).collect();
                let params: Vec<f64> = (0..count).map(|_| pick()).collect();
                polyline_record(&points, &params)
            } else {
                unhex(POLYLINES[(next() % 4) as usize].record)
            };
            if next() % 3 == 0 && !record.is_empty() {
                let at = (next() % record.len() as u64) as usize;
                record[at] ^= 1 << (next() % 8);
            }
            if next() % 5 == 0 {
                record.truncate((next() % (record.len() as u64 + 1)) as usize);
            }
            let n = [0, 1, 2, 7, 128, usize::MAX, MAX_INTERVALS + 1][(next() % 7) as usize];
            let capacity = (next() % 400) as usize;
            let (status, out) = call(&record, n, capacity);
            match status {
                0 => {
                    assert!(capacity >= 3 * (n + 1));
                    answered += 1;
                }
                NOT_RECOGNISED | ERR_INPUT | ERR_SIZE => assert!(out.iter().all(|&v| v == -9.0)),
                other => panic!("status {other}"),
            }
        }
        assert!(answered > 0);
    }
}
