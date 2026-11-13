//! Native Bezier endpoint-derivative solver (RUST-BLEND-10).
//!
//! A dependency-free re-implementation of
//! `narwhal3d/blend.py::blend_bezier_points`: given the two ends' ORIENTED
//! derivative lists (`[P, D1, ...]`, already scaled-free) and the two constant
//! speed factors `lambda`, it solves the unique Bezier control points of degree
//! `n = k0 + k1 + 1` whose first `k0` derivatives at `t = 0` and first `k1` at
//! `t = 1` equal `lambda^j D_j`.
//!
//! This is the SAME forward / backward Bezier end-derivative algebra the Python
//! reference uses (the `n!/(n-j)! Delta^j P_0` identities and their mirror at
//! `t = 1`), so on a supported platform it returns the control points bit for
//! bit. It is required for every supported continuity pair; the redundant Python
//! endpoint solver is retired. Missing/stale support and native execution failures
//! are explicit errors, never Python replay. The openNURBS extraction
//! (`DerivativeAt`) and the `NurbsCurve` construction stay in Python -- this
//! module never samples, meshes or approximates, it only does exact arithmetic
//! on already-extracted finite values.
//!
//! Error contract: `BLEND_OK` (0) with the control points in `out` on success;
//! `BLEND_ERR_INPUT` on any malformed count / range / pointer or a non-finite
//! input; `BLEND_ERR_OUT` when the output buffer is not the fixed maximum size.
//! On every refusal the output buffer is left untouched (no partial result).
//!
//! Robustness bounds: a blend of continuity G4 at both ends is the maximum the
//! app exposes, so each end matches at most 4 derivatives (degree at most 9, at
//! most 10 control points). Counts beyond that are a refusal, never a
//! mis-solve and never an unbounded allocation.

use std::mem::size_of;

/// Success (the control points are in `out`).
pub const BLEND_OK: i32 = 0;
/// A rejection: a count / range / pointer is malformed, or an input is non-finite.
pub const BLEND_ERR_INPUT: i32 = -1;
/// The output buffer is not the fixed maximum size (or not writable).
pub const BLEND_ERR_OUT: i32 = -2;

/// The highest derivative matched at a single end (G4 = position + 4).
pub const BLEND_MAX_K: usize = 4;
/// The most control points any supported blend produces (degree 9 -> 10 points).
pub const BLEND_MAX_POINTS: usize = 9 + 1;
/// The fixed maximum output: `BLEND_MAX_POINTS * 3` doubles (xyz per point).
pub const BLEND_MAX_OUT_DOUBLES: usize = BLEND_MAX_POINTS * 3;

/// Exact small binomial coefficient `C(j, i)` (both small; never rounds).
fn binom(j: usize, i: usize) -> u64 {
    if i == 0 || i == j {
        return 1;
    }
    let k = i.min(j - i);
    let mut r = 1u64;
    for t in 1..=k {
        r = r * (j - t + 1) as u64 / t as u64;
    }
    r
}

/// Solve the Bezier control points from endpoint derivative targets, as a
/// dependency-free pure function.
///
/// `p0` holds `3 * (k0 + 1)` finite doubles (end 1's oriented point + first
/// `k0` derivatives, `xyz` per entry); `p1` likewise for end 2. `n = k0 + k1 + 1`
/// is the degree. Returns the `n + 1` control points (at most 10, `xyz` per
/// point) in a fixed 30-double array, or `None` if any input is out of range or
/// non-finite.
///
/// The arithmetic mirrors the Python reference operation for operation (each
/// scalar is computed once, then applied element by element, in the same order),
/// so a bit-for-bit match holds on a conforming platform.
fn compute(
    p0: &[f64],
    p1: &[f64],
    k0: usize,
    k1: usize,
    lam0: f64,
    lam1: f64,
) -> Option<[f64; BLEND_MAX_OUT_DOUBLES]> {
    if k0 > BLEND_MAX_K || k1 > BLEND_MAX_K {
        return None;
    }
    if p0.len() != 3 * (k0 + 1) || p1.len() != 3 * (k1 + 1) {
        return None;
    }
    if p0.iter().any(|v| !v.is_finite())
        || p1.iter().any(|v| !v.is_finite())
        || !lam0.is_finite()
        || !lam1.is_finite()
    {
        return None;
    }

    let n = k0 + k1 + 1; // 1..=9

    // n!/(n-j)! = (n)(n-1)...(n-j+1); exact (small integers).
    fn falling(n: usize, j: usize) -> f64 {
        let mut out = 1.0f64;
        for i in 0..j {
            out *= (n - i) as f64;
        }
        out
    }

    let mut pts = [0.0f64; BLEND_MAX_OUT_DOUBLES];
    let idx = |i: usize| i * 3;
    // P_0 and P_n are the two given endpoints.
    for c in 0..3 {
        pts[idx(0) + c] = p0[c];
        pts[idx(n) + c] = p1[c];
    }

    // t = 0 side: B^(j)(0) = n!/(n-j)! Delta^j P_0, solved for P_j.
    for j in 1..=k0 {
        let scalar = lam0.powf(j as f64) / falling(n, j);
        let mut acc = [
            p0[idx(j)] * scalar,
            p0[idx(j) + 1] * scalar,
            p0[idx(j) + 2] * scalar,
        ];
        for i in 0..j {
            let s = if (j - i) % 2 == 0 { 1.0 } else { -1.0 } * binom(j, i) as f64;
            for c in 0..3 {
                acc[c] -= pts[idx(i) + c] * s;
            }
        }
        for c in 0..3 {
            pts[idx(j) + c] = acc[c];
        }
    }
    // t = 1 side: B^(j)(1) = n!/(n-j)! nabla^j P_n, solved for P_(n-j).
    for j in 1..=k1 {
        let scalar = lam1.powf(j as f64) / falling(n, j);
        let mut acc = [
            p1[idx(j)] * scalar,
            p1[idx(j) + 1] * scalar,
            p1[idx(j) + 2] * scalar,
        ];
        for i in 0..j {
            let s = if i % 2 == 0 { 1.0 } else { -1.0 } * binom(j, i) as f64;
            for c in 0..3 {
                acc[c] -= pts[idx(n - i) + c] * s;
            }
        }
        let sign = if j % 2 == 0 { 1.0 } else { -1.0 };
        for c in 0..3 {
            pts[idx(n - j) + c] = acc[c] * sign;
        }
    }
    Some(pts)
}

/// Check whether two byte ranges overlap (input/output aliasing detection).
/// Returns `false` on overflow (the range is too large to be valid).
fn ranges_overlap(a: *const f64, a_len: usize, b: *mut f64, b_len: usize) -> bool {
    if a_len == 0 || b_len == 0 {
        return false;
    }
    let Some(a_bytes) = a_len.checked_mul(size_of::<f64>()) else {
        return false;
    };
    let Some(b_bytes) = b_len.checked_mul(size_of::<f64>()) else {
        return false;
    };
    let Some(a_end) = (a as usize).checked_add(a_bytes) else {
        return false;
    };
    let Some(b_end) = (b as usize).checked_add(b_bytes) else {
        return false;
    };
    (a as usize) < b_end && (b as usize) < a_end
}

/// The C ABI entry point.
///
/// `p0` / `p1` are caller-owned arrays of `3 * (k0 + 1)` / `3 * (k1 + 1)`
/// doubles; `out` is a caller-owned buffer of exactly `BLEND_MAX_OUT_DOUBLES`
/// doubles. `k0` / `k1` are the derivative counts (each <= `BLEND_MAX_K`).
/// On success the `n + 1` control points (xyz per point) are written to `out`
/// and `BLEND_OK` returned; otherwise the appropriate error code is returned
/// and `out` is left untouched.
///
/// Validation order (all pre-slice checks dereference nothing):
/// 1. `k0` / `k1` within range; `out_len` exact; input lengths exact.
/// 2. `lam0` / `lam1` finite.
/// 3. No overlap between input and output ranges.
/// 4. Input values finite (first dereference).
/// 5. Computed result finite (no partial writes on overflow).
///
/// # Safety
///
/// `p0` must be readable for `p0_len` doubles, `p1` likewise, and `out` must
/// be writable for `out_len` doubles.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_blend_bezier_points(
    p0: *const f64,
    p0_len: usize,
    p1: *const f64,
    p1_len: usize,
    k0: u32,
    k1: u32,
    lam0: f64,
    lam1: f64,
    out: *mut f64,
    out_len: usize,
) -> i32 {
    // 1. Validate counts and lengths BEFORE forming any slices (no dereferencing).
    if k0 as usize > BLEND_MAX_K || k1 as usize > BLEND_MAX_K {
        return BLEND_ERR_INPUT;
    }
    if out_len != BLEND_MAX_OUT_DOUBLES {
        return BLEND_ERR_OUT;
    }
    if p0_len != 3 * (k0 as usize + 1) || p1_len != 3 * (k1 as usize + 1) {
        return BLEND_ERR_INPUT;
    }
    // 2. Finite lambda.
    if !lam0.is_finite() || !lam1.is_finite() {
        return BLEND_ERR_INPUT;
    }
    // 3. Reject input/output overlap BEFORE forming any references (no aliasing UB).
    if ranges_overlap(p0, p0_len, out, out_len) || ranges_overlap(p1, p1_len, out, out_len) {
        return BLEND_ERR_INPUT;
    }
    // 4. Form input slices (all metadata validated above).
    let p0_slice = match unsafe { crate::checked_slice(p0, p0_len) } {
        Some(s) => s,
        None => return BLEND_ERR_INPUT,
    };
    let p1_slice = match unsafe { crate::checked_slice(p1, p1_len) } {
        Some(s) => s,
        None => return BLEND_ERR_INPUT,
    };
    // 5. Validate input finiteness (first dereference, after all pre-checks).
    if p0_slice.iter().any(|v| !v.is_finite()) || p1_slice.iter().any(|v| !v.is_finite()) {
        return BLEND_ERR_INPUT;
    }
    // 6. Compute into a local array (no writes to `out` yet).
    let result = match compute(p0_slice, p1_slice, k0 as usize, k1 as usize, lam0, lam1) {
        Some(r) => r,
        None => return BLEND_ERR_INPUT,
    };
    // 7. Reject non-finite computed output without writes.
    if result.iter().any(|v| !v.is_finite()) {
        return BLEND_ERR_INPUT;
    }
    // 8. Form the mutable out slice and copy (all preconditions met).
    let out_slice = match unsafe { crate::checked_slice_mut(out, out_len) } {
        Some(s) => s,
        None => return BLEND_ERR_OUT,
    };
    out_slice.copy_from_slice(&result);
    BLEND_OK
}

// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn call(
        p0: &[f64],
        p1: &[f64],
        k0: usize,
        k1: usize,
        lam0: f64,
        lam1: f64,
    ) -> (i32, [f64; BLEND_MAX_OUT_DOUBLES]) {
        let mut out = [99.0f64; BLEND_MAX_OUT_DOUBLES];
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                p0.as_ptr(),
                p0.len(),
                p1.as_ptr(),
                p1.len(),
                k0 as u32,
                k1 as u32,
                lam0,
                lam1,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        (code, out)
    }

    #[test]
    fn position_position_is_a_line() {
        let (code, pts) = call(&[0.0, 0.0, 0.0], &[4.0, 3.0, 2.0], 0, 0, 1.0, 1.0);
        assert_eq!(code, BLEND_OK);
        assert_eq!(&pts[..6], &[0.0, 0.0, 0.0, 4.0, 3.0, 2.0]);
    }

    #[test]
    fn tangency_matches_the_hand_solved_cubic() {
        // k0=k1=1, n=3. B(t) = (1-t)^3 P0 + 3(1-t)^2 t P1 + 3(1-t) t^2 P2 + t^3 P3.
        // B'(0) = 3(P1-P0) -> P1 = P0 + B'(0)/3 ; B'(1) = 3(P3-P2) -> P2 = P3 - B'(1)/3.
        let (code, pts) = call(
            &[0.0, 0.0, 0.0, 3.0, 0.0, 0.0],
            &[6.0, 0.0, 0.0, -3.0, 0.0, 0.0],
            1,
            1,
            1.0,
            1.0,
        );
        assert_eq!(code, BLEND_OK);
        assert_eq!(
            &pts[..12],
            &[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 7.0, 0.0, 0.0, 6.0, 0.0, 0.0]
        );
    }

    #[test]
    fn curvature_at_one_end_is_hand_verifiable() {
        // k0=2, k1=0, n=3. P0, P3 given; P1, P2 solved from B'(0) and B''(0).
        // B'(0) = 3(P1-P0) -> P1 = P0 + B'(0)/3 = (1,0,0).
        // B''(0) = 6(P0 - 2P1 + P2) -> P2 = (B''(0)/6) + 2P1 - P0 = (2,1,0).
        let (code, pts) = call(
            &[0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 6.0, 0.0],
            &[9.0, 0.0, 0.0],
            2,
            0,
            1.0,
            1.0,
        );
        assert_eq!(code, BLEND_OK);
        assert_eq!(
            &pts[..12],
            &[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 2.0, 1.0, 0.0, 9.0, 0.0, 0.0]
        );
    }

    #[test]
    fn negative_and_zero_scales_are_supported() {
        // lam = -1: targets flip sign by (-1)^j; lam = 0: every j>=1 target is zero.
        let base_p0 = [0.0, 0.0, 0.0, 2.0, 2.0, 0.0, 1.0, 1.0, 1.0];
        let base_p1 = [5.0, 5.0, 0.0, -2.0, -2.0, 0.0, 1.0, 1.0, 1.0];
        for (lam0, lam1) in [(-1.0, 1.0), (1.0, -1.0), (-1.0, -1.0), (0.0, 0.0)] {
            let (code, _) = call(&base_p0, &base_p1, 2, 2, lam0, lam1);
            assert_eq!(code, BLEND_OK, "scale ({lam0},{lam1}) must be accepted");
        }
    }

    #[test]
    fn huge_input_length_is_refused_before_any_dereference() {
        // A tiny 3-double buffer with a caller-claimed span of usize::MAX doubles:
        // the exact-length check must reject it (k0 = 0 wants exactly 3) without
        // forming a slice -- no fault, no dereference of the absurd span.
        let p0 = [0.0f64; 3];
        let p1 = [5.0, 0.0, 0.0, -1.0, 0.0, 0.0];
        let mut out = [99.0f64; BLEND_MAX_OUT_DOUBLES];
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                p0.as_ptr(),
                usize::MAX,
                p1.as_ptr(),
                p1.len(),
                0,
                1,
                1.0,
                1.0,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert_eq!(code, BLEND_ERR_INPUT);
        assert_eq!(out, [99.0f64; BLEND_MAX_OUT_DOUBLES]);

        // a huge k count is refused by the range check, likewise before anything else
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                p0.as_ptr(),
                3,
                p1.as_ptr(),
                p1.len(),
                u32::MAX,
                1,
                1.0,
                1.0,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert_eq!(code, BLEND_ERR_INPUT);
        assert_eq!(out, [99.0f64; BLEND_MAX_OUT_DOUBLES]);
    }

    #[test]
    fn input_output_overlap_is_refused_without_partial_output() {
        // One buffer holds p0 at [0..6] and the 30-double out at [3..33]: the
        // ranges overlap, so the call must be refused BEFORE any mutable slice is
        // created (aliasing a shared input slice with a mutable out slice would be
        // UB), and out must be left untouched.
        let mut buf = [99.0f64; 60];
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                buf.as_ptr(),
                6,
                buf.as_ptr().add(30),
                6,
                1,
                1,
                1.0,
                1.0,
                buf.as_mut_ptr().add(3),
                30,
            )
        };
        assert_eq!(code, BLEND_ERR_INPUT);
        assert!(
            buf[3..33].iter().all(|v| *v == 99.0),
            "refusal must not touch out"
        );

        // positive control: disjoint ranges in the same buffer DO run
        let mut buf = [99.0f64; 66];
        buf[..6].copy_from_slice(&[0.0, 0.0, 0.0, 3.0, 0.0, 0.0]);
        buf[30..36].copy_from_slice(&[6.0, 0.0, 0.0, -3.0, 0.0, 0.0]);
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                buf.as_ptr(),
                6,
                buf.as_ptr().add(30),
                6,
                1,
                1,
                1.0,
                1.0,
                buf.as_mut_ptr().add(36),
                30,
            )
        };
        assert_eq!(code, BLEND_OK);
        assert_eq!(
            &buf[36..48],
            &[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 7.0, 0.0, 0.0, 6.0, 0.0, 0.0]
        );
    }

    #[test]
    fn nonfinite_computed_result_is_refused_without_partial_output() {
        // Finite inputs whose product overflows to infinity: the COMPUTED result is
        // non-finite, so the call must be refused and out left untouched (no
        // partial write of a result that overflowed mid-solve).
        let p0 = [0.0, 0.0, 0.0, 1e308, 0.0, 0.0];
        let p1 = [5.0, 0.0, 0.0, 0.0, 0.0, 0.0];
        let mut out = [99.0f64; BLEND_MAX_OUT_DOUBLES];
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                p0.as_ptr(),
                p0.len(),
                p1.as_ptr(),
                p1.len(),
                1,
                1,
                1e308,
                1.0,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert_eq!(code, BLEND_ERR_INPUT);
        assert_eq!(
            out, [99.0f64; BLEND_MAX_OUT_DOUBLES],
            "overflow must not touch out"
        );
    }

    #[test]
    fn malformed_counts_ranges_and_pointers_are_refused_without_partial_output() {
        let mut out = [99.0f64; BLEND_MAX_OUT_DOUBLES];
        let p0 = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0];
        let p1 = [5.0, 0.0, 0.0, -1.0, 0.0, 0.0];

        // k out of range.
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                p0.as_ptr(),
                p0.len(),
                p1.as_ptr(),
                p1.len(),
                5,
                1,
                1.0,
                1.0,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert_eq!(code, BLEND_ERR_INPUT);
        assert_eq!(
            out, [99.0f64; BLEND_MAX_OUT_DOUBLES],
            "refusal must not touch out"
        );

        // wrong span length.
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                p0.as_ptr(),
                p0.len() - 1,
                p1.as_ptr(),
                p1.len(),
                1,
                1,
                1.0,
                1.0,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert_eq!(code, BLEND_ERR_INPUT);

        // non-finite input.
        let nan_p0 = [0.0, f64::NAN, 0.0, 1.0, 0.0, 0.0];
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                nan_p0.as_ptr(),
                nan_p0.len(),
                p1.as_ptr(),
                p1.len(),
                1,
                1,
                1.0,
                1.0,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert_eq!(code, BLEND_ERR_INPUT);
        assert_eq!(out, [99.0f64; BLEND_MAX_OUT_DOUBLES]);

        // non-finite lambda.
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                p0.as_ptr(),
                p0.len(),
                p1.as_ptr(),
                p1.len(),
                1,
                1,
                f64::INFINITY,
                1.0,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert_eq!(code, BLEND_ERR_INPUT);

        // null span with a non-zero length.
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                std::ptr::null(),
                3,
                p1.as_ptr(),
                p1.len(),
                1,
                1,
                1.0,
                1.0,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert_eq!(code, BLEND_ERR_INPUT);
        assert_eq!(out, [99.0f64; BLEND_MAX_OUT_DOUBLES]);

        // wrong output size.
        let code = unsafe {
            narwhal_pure_core_blend_bezier_points(
                p0.as_ptr(),
                p0.len(),
                p1.as_ptr(),
                p1.len(),
                1,
                1,
                1.0,
                1.0,
                out.as_mut_ptr(),
                29,
            )
        };
        assert_eq!(code, BLEND_ERR_OUT);
        assert_eq!(out, [99.0f64; BLEND_MAX_OUT_DOUBLES]);
    }
}
