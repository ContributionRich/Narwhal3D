//! The DXF export's polyline for a free-form curve: `export_commands._approx_points`, read
//! from the curve's `Encode()` record.
//!
//! DXF R12 has no spline entity here, so Export writes a curve that is not a line, a polyline
//! or a horizontal circle or arc as a POLYLINE whose chords stay within the tolerance.
//! `_approx_points(curve, tol, t0, t1)` evaluates the curve at both ends of a parameter
//! interval and at its quarter, half and three-quarter points, measures how far those points
//! fall from the chord, and halves the interval while the worst exceeds `tol`, to a depth of 18.
//! Python ran it through rhino3dm: five `PointAt` calls per interval, 12-36 ms for a 20-point
//! cubic at the usual tolerances.
//!
//! `PointAt` is `nearest::Nurbs::point` (openNURBS `ON_NurbsCurve::Evaluate` at eb92af3, the
//! commit rhino3dm 8.35.0 pins, in its operation order). The rest is `_approx_points`'
//! arithmetic with Python float semantics: `x ** 2` is libm `pow`, `sum()` is CPython's
//! compensated sum, `math.dist` is CPython's `vector_norm`, and `min` / `max` keep the first
//! argument on a tie. Rust never fuses a multiply and an add, so the points are bit-identical
//! to the Python route's wherever rhino3dm does not fuse them either. The adapter
//! (`native_nearest.dxf_approx_points`) admits only a qualified platform whose loaded rhino3dm
//! reproduces the Python route on its witness curves. Anything else is NOT_RECOGNISED and
//! Python runs its own route. The output is export coordinates only; no geometry changes.

use crate::fit::py_sum;
use crate::nearest::{region, separate, Nurbs};
use crate::nurbs_record;
use crate::offset::knot_vector_is_valid;
use crate::python_norm::{dist3, py_square};

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
/// Python runs `_approx_points` itself.
const NOT_RECOGNISED: i32 = 1;
/// More points than `capacity`: only `out_count` is written.
const ROOM: i32 = 4;

/// `_approx_points`' recursion limit.
const MAX_DEPTH: u32 = 18;
/// The most knots a record may carry here.
const MAX_KNOTS: usize = 1 << 20;

type P3 = [f64; 3];

/// A refusal that sends the whole call to Python.
#[derive(Debug, PartialEq)]
struct Declined;

/// Python `max(a, b)`: `a` unless `b > a`.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python `min(a, b)`: `a` unless `b < a`.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

fn point(curve: &Nurbs, t: f64) -> Result<P3, Declined> {
    let p = curve.point(t);
    if p.iter().all(|x| x.is_finite()) {
        Ok(p)
    } else {
        Err(Declined)
    }
}

/// Python's `x ** 2`, which raises OverflowError where a finite `x` squares past the
/// largest double; the export declines there so that the Python route raises it.
fn square(x: f64) -> Result<f64, Declined> {
    let s = py_square(x);
    if x.is_finite() && !s.is_finite() {
        Err(Declined)
    } else {
        Ok(s)
    }
}

/// `_approx_points(curve, tol, t0, t1, depth)`. Python returns `[a, b]` at a leaf and joins
/// `left[:-1] + right`; the dropped point and the kept one are both `PointAt(tm)`, so the
/// list is each leaf's start in order and then the last leaf's end, `PointAt(t1)`. Only the
/// leaf starts are pushed here.
fn approx(
    curve: &Nurbs,
    tol: f64,
    t0: f64,
    t1: f64,
    depth: u32,
    out: &mut Vec<P3>,
) -> Result<(), Declined> {
    let a = point(curve, t0)?;
    let b = point(curve, t1)?;
    let mut worst = 0.0;
    for f in [0.25, 0.5, 0.75] {
        let p = point(curve, t0 + (t1 - t0) * f)?;
        let ab = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
        let ap = [p[0] - a[0], p[1] - a[1], p[2] - a[2]];
        let l2 = square(ab[0])? + square(ab[1])? + square(ab[2])?;
        let s = if l2 == 0.0 {
            0.0
        } else {
            let dot = py_sum([ab[0] * ap[0], ab[1] * ap[1], ab[2] * ap[2]].into_iter());
            py_max(0.0, py_min(1.0, dot / l2))
        };
        let foot = [a[0] + ab[0] * s, a[1] + ab[1] * s, a[2] + ab[2] * s];
        worst = py_max(worst, dist3(p, foot));
    }
    if worst <= tol || depth >= MAX_DEPTH {
        out.push(a);
        return Ok(());
    }
    let tm = (t0 + t1) / 2.0;
    approx(curve, tol, t0, tm, depth + 1, out)?;
    approx(curve, tol, tm, t1, depth + 1, out)
}

/// The points for the curve in `record` between `t0` and `t1`.
fn approx_points(record: &[u8], tol: f64, t0: f64, t1: f64) -> Result<Vec<P3>, Declined> {
    let rec = nurbs_record::read(record).ok_or(Declined)?;
    if rec.knot_count > MAX_KNOTS {
        return Err(Declined);
    }
    let mut knots = vec![0.0; rec.knot_count];
    let mut cvs = vec![0.0; rec.cv_doubles()];
    rec.write(&mut knots, &mut cvs);
    if !knots.iter().chain(&cvs).all(|v| v.is_finite())
        || !knot_vector_is_valid(rec.order, rec.cv_count, &knots)
    {
        return Err(Declined);
    }
    let curve = Nurbs::new(rec.order, &knots, &cvs, rec.rational).ok_or(Declined)?;
    let mut out = Vec::new();
    approx(&curve, tol, t0, t1, 0, &mut out)?;
    out.push(point(&curve, t1)?);
    Ok(out)
}

/// `export_commands._approx_points(curve, tol, t0, t1)` for the `NurbsCurve` whose `Encode()`
/// record is `record`.
///
/// Returns 0 with the points (`x, y, z` each) in `out_points` and their number in `out_count`;
/// ROOM (4) with only `out_count` written when they do not fit `capacity` points;
/// NOT_RECOGNISED (1) when the record is not a valid 3-D `ON_NurbsCurve` or a point is not
/// finite, so Python runs; ERR_INPUT (-2) for a non-finite `tol`, `t0` or `t1`; ERR_NULL (-1)
/// for a null, misaligned or overlapping pointer. Nothing is written on any refusal.
///
/// # Safety
/// `record` is readable for `record_len` bytes, `out_points` writable for `3 * capacity`
/// doubles and `out_count` for one.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_dxf_approx_points(
    record: *const u8,
    record_len: usize,
    tol: f64,
    t0: f64,
    t1: f64,
    out_points: *mut f64,
    capacity: usize,
    out_count: *mut usize,
) -> i32 {
    if out_count.is_null() {
        return ERR_NULL;
    }
    let Some(doubles) = capacity.checked_mul(3) else {
        return ERR_NULL;
    };
    let ranges = (
        region(record, record_len),
        region(out_points.cast_const(), doubles),
        region(out_count.cast_const(), 1),
    );
    let (Some(rr), Some(pr), Some(cr)) = ranges else {
        return ERR_NULL;
    };
    if !separate(&[rr], &[pr, cr]) {
        return ERR_NULL;
    }
    if !(tol.is_finite() && t0.is_finite() && t1.is_finite()) {
        return ERR_INPUT;
    }
    let Some(data) = (unsafe { crate::checked_slice(record, record_len) }) else {
        return ERR_NULL;
    };
    let Ok(points) = approx_points(data, tol, t0, t1) else {
        return NOT_RECOGNISED;
    };
    if points.len() > capacity {
        unsafe {
            *out_count = points.len();
        }
        return ROOM;
    }
    let Some(out) = (unsafe { crate::checked_slice_mut(out_points, 3 * points.len()) }) else {
        return ERR_NULL;
    };
    for (slot, p) in out.chunks_exact_mut(3).zip(&points) {
        slot.copy_from_slice(p);
    }
    unsafe {
        *out_count = points.len();
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::nurbs_record::nurbs_record;

    /// A clamped cubic through a wave of `n` control points.
    fn wave(n: usize) -> Vec<u8> {
        let cvs: Vec<[f64; 3]> = (0..n)
            .map(|i| [i as f64, (i as f64 * 0.8).sin() * 3.0, 0.0])
            .collect();
        let knots: Vec<f64> = (0..n + 2)
            .map(|i| (i as f64 - 2.0).clamp(0.0, (n - 3) as f64))
            .collect();
        nurbs_record(4, &knots, &cvs)
    }

    unsafe fn call(
        record: &[u8],
        tol: f64,
        t0: f64,
        t1: f64,
        cap: usize,
    ) -> (i32, Vec<f64>, usize) {
        let mut out = vec![-7.0; 3 * cap.max(1)];
        let mut count = 99usize;
        let code = unsafe {
            narwhal_pure_core_dxf_approx_points(
                record.as_ptr(),
                record.len(),
                tol,
                t0,
                t1,
                out.as_mut_ptr(),
                cap,
                &mut count,
            )
        };
        (code, out, count)
    }

    #[test]
    fn chords_stay_within_the_tolerance_and_end_on_the_curve() {
        let record = wave(20);
        let rec = nurbs_record::read(&record).unwrap();
        let mut knots = vec![0.0; rec.knot_count];
        let mut cvs = vec![0.0; rec.cv_doubles()];
        rec.write(&mut knots, &mut cvs);
        let curve = Nurbs::new(rec.order, &knots, &cvs, false).unwrap();
        let (t0, t1) = curve.domain();
        for tol in [0.1, 0.01, 0.001] {
            let (code, out, n) = unsafe { call(&record, tol, t0, t1, 4096) };
            assert_eq!(code, 0);
            let pts: Vec<P3> = out[..3 * n]
                .chunks_exact(3)
                .map(|p| [p[0], p[1], p[2]])
                .collect();
            assert_eq!(pts[0], curve.point(t0));
            assert_eq!(pts[n - 1], curve.point(t1));
            // Every vertex lies on the curve: it is PointAt of some parameter, so a dense
            // search finds it within rounding.
            for p in &pts {
                let best = (0..=20000)
                    .map(|k| dist3(*p, curve.point(t0 + (t1 - t0) * k as f64 / 20000.0)))
                    .fold(f64::INFINITY, f64::min);
                assert!(best < 2e-3, "{tol}: {p:?} {best}");
            }
        }
        let coarse = unsafe { call(&record, 0.1, t0, t1, 4096) }.2;
        let fine = unsafe { call(&record, 0.001, t0, t1, 4096) }.2;
        assert!(coarse < fine);
    }

    #[test]
    fn a_line_is_one_chord_and_a_negative_tolerance_reaches_the_depth_limit() {
        let record = nurbs_record(2, &[0.0, 1.0], &[[0.0, 0.0, 0.0], [3.0, 4.0, 0.0]]);
        let (code, out, n) = unsafe { call(&record, 0.01, 0.0, 1.0, 8) };
        assert_eq!((code, n), (0, 2));
        assert_eq!(&out[..6], &[0.0, 0.0, 0.0, 3.0, 4.0, 0.0]);
        let (code, _, n) = unsafe { call(&record, -1.0, 0.0, 1.0, 1 << 19) };
        assert_eq!((code, n), (0, (1 << MAX_DEPTH) + 1));
    }

    #[test]
    fn room_writes_only_the_count_and_refusals_write_nothing() {
        let record = wave(12);
        let (code, _, need) = unsafe { call(&record, 0.001, 0.0, 9.0, 4096) };
        assert_eq!(code, 0);
        let (code, out, count) = unsafe { call(&record, 0.001, 0.0, 9.0, need - 1) };
        assert_eq!((code, count), (ROOM, need));
        assert!(out.iter().all(|&x| x == -7.0));
        for (tol, t0, t1) in [
            (f64::NAN, 0.0, 9.0),
            (0.01, f64::INFINITY, 9.0),
            (0.01, 0.0, f64::NAN),
        ] {
            let (code, out, count) = unsafe { call(&record, tol, t0, t1, 64) };
            assert_eq!((code, count), (ERR_INPUT, 99));
            assert!(out.iter().all(|&x| x == -7.0));
        }
        let mut reversed = wave(12);
        let (code, _, count) = unsafe { call(b"not a record", 0.01, 0.0, 1.0, 64) };
        assert_eq!((code, count), (NOT_RECOGNISED, 99));
        // A bad CRC: flip a knot byte.
        let at = reversed.len() / 2;
        reversed[at] ^= 0x10;
        let (code, _, count) = unsafe { call(&reversed, 0.01, 0.0, 1.0, 64) };
        assert_eq!((code, count), (NOT_RECOGNISED, 99));
    }

    #[test]
    fn a_chord_whose_square_overflows_is_declined() {
        // Python's `ab[0] ** 2` raises OverflowError for a chord about 2e200 long; a chord
        // whose components square to infinity from infinity itself does not raise.
        let cvs = [[-1e200, 0.0, 0.0], [0.0, 1.0, 0.0], [1e200, 0.0, 0.0]];
        let record = nurbs_record(3, &[0.0, 0.0, 1.0, 1.0], &cvs);
        let (code, out, count) = unsafe { call(&record, 0.01, 0.0, 1.0, 64) };
        assert_eq!((code, count), (NOT_RECOGNISED, 99));
        assert!(out.iter().all(|&v| v == -7.0));
        assert_eq!(square(1e154), Ok(1e154 * 1e154));
        assert_eq!(square(1e155), Err(Declined));
        assert_eq!(square(f64::INFINITY), Ok(f64::INFINITY));
        assert!(square(f64::NAN).unwrap().is_nan());
    }

    #[test]
    fn bad_pointers_are_refused() {
        let record = wave(8);
        let mut out = [0.0; 24];
        let code = unsafe {
            narwhal_pure_core_dxf_approx_points(
                record.as_ptr(),
                record.len(),
                0.01,
                0.0,
                5.0,
                out.as_mut_ptr(),
                8,
                std::ptr::null_mut(),
            )
        };
        assert_eq!(code, ERR_NULL);
        let mut count = 0usize;
        let code = unsafe {
            narwhal_pure_core_dxf_approx_points(
                record.as_ptr(),
                record.len(),
                0.01,
                0.0,
                5.0,
                std::ptr::null_mut(),
                8,
                &mut count,
            )
        };
        assert_eq!((code, count), (ERR_NULL, 0));
        let code = unsafe {
            narwhal_pure_core_dxf_approx_points(
                record.as_ptr(),
                record.len(),
                0.01,
                0.0,
                5.0,
                out.as_mut_ptr(),
                usize::MAX,
                &mut count,
            )
        };
        assert_eq!((code, count), (ERR_NULL, 0));
    }

    #[test]
    fn flipped_records_never_panic() {
        let record = wave(9);
        let mut seed = 0x9E37_79B9_7F4A_7C15u64;
        for _ in 0..300 {
            let mut copy = record.clone();
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            let at = (seed as usize) % copy.len();
            copy[at] ^= 1 << (seed >> 61);
            let (code, _, _) = unsafe { call(&copy, 0.01, 0.0, 6.0, 4096) };
            assert!([0, NOT_RECOGNISED].contains(&code), "{code}");
        }
    }
}
