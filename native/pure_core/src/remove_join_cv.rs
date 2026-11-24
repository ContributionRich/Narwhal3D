//! MEASURED: reference CAD application 8.35 native List before/after RemoveControlPoint and grip Delete,
//! owned synthetic mixed-degree, rational arc and closed-seam fixtures, 2026-11-24.
//! SDK glue first degree-elevates and concatenates the two exact clamped segments.
//! Deletion retains every other homogeneous CV and removes one equal join knot.
//! It is NOT knot removal by fitting, a polyline bridge, or an approximation.

const MAX_CVS: usize = 65_536;

pub struct Removed {
    pub cvs: Vec<f64>,
    pub knots: Vec<f64>,
}

pub fn remove(cvs: &[f64], knots: &[f64], degree: usize, joint: usize) -> Option<Removed> {
    let n = cvs.len().checked_div(4)?;
    if !cvs.len().is_multiple_of(4)
        || n > MAX_CVS
        || !(1..=32).contains(&degree)
        || n <= degree + 1
        || knots.len() != n + degree - 1
        || joint < degree
        || joint >= n - 1
        || joint + degree >= knots.len()
        || !cvs.iter().all(|v| v.is_finite())
        || !knots.iter().all(|v| v.is_finite())
        || !knots.windows(2).all(|v| v[0] <= v[1])
        || !cvs.chunks_exact(4).all(|p| p[3] > 0.0)
    {
        return None;
    }
    let start = knots[0];
    let end = *knots.last()?;
    let t = knots[joint];
    if start >= t
        || t >= end
        || !knots[..degree].iter().all(|k| *k == start)
        || !knots[knots.len() - degree..].iter().all(|k| *k == end)
        || !knots[joint..joint + degree].iter().all(|k| *k == t)
        || knots[joint - 1] >= t
        || knots[joint + degree] <= t
    {
        return None;
    }
    let mut out_cvs = Vec::with_capacity(cvs.len() - 4);
    out_cvs.extend_from_slice(&cvs[..4 * joint]);
    out_cvs.extend_from_slice(&cvs[4 * (joint + 1)..]);
    let mut out_knots = Vec::with_capacity(knots.len() - 1);
    out_knots.extend_from_slice(&knots[..joint]);
    out_knots.extend_from_slice(&knots[joint + 1..]);
    Some(Removed {
        cvs: out_cvs,
        knots: out_knots,
    })
}

fn overlap(a: *const f64, an: usize, b: *const f64, bn: usize) -> bool {
    let a0 = a as usize;
    let b0 = b as usize;
    a0 < b0 + bn * 8 && b0 < a0 + an * 8
}

/// Delete a certified internal join CV in already degree-elevated SDK NURBS data.
/// Returns 0 on success, -1 on invalid/unsupported input, -2 on output refusal.
/// No output is written before all validation succeeds.
///
/// # Safety
/// Input pointers must reference their stated initialized double spans; output
/// pointers must reference writable spans. All buffers must be independently owned.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_remove_join_cv_v1(
    cvs: *const f64,
    cv_len: usize,
    knots: *const f64,
    knot_len: usize,
    degree: usize,
    joint: usize,
    out_cvs: *mut f64,
    out_cv_len: usize,
    out_knots: *mut f64,
    out_knot_len: usize,
) -> i32 {
    if cv_len > MAX_CVS * 4 || knot_len > MAX_CVS + 31 {
        return -1;
    }
    let Some(c) = (unsafe { crate::checked_slice(cvs, cv_len) }) else {
        return -1;
    };
    let Some(k) = (unsafe { crate::checked_slice(knots, knot_len) }) else {
        return -1;
    };
    let Some(result) = remove(c, k, degree, joint) else {
        return -1;
    };
    let cn = result.cvs.len();
    let kn = result.knots.len();
    if out_cv_len < cn
        || out_knot_len < kn
        || !crate::raw_span_is_valid(out_cvs, cn)
        || !crate::raw_span_is_valid(out_knots, kn)
        || overlap(out_cvs, cn, out_knots, kn)
        || overlap(out_cvs, cn, cvs, cv_len)
        || overlap(out_cvs, cn, knots, knot_len)
        || overlap(out_knots, kn, cvs, cv_len)
        || overlap(out_knots, kn, knots, knot_len)
    {
        return -2;
    }
    unsafe {
        crate::checked_slice_mut(out_cvs, cn)
            .unwrap()
            .copy_from_slice(&result.cvs);
        crate::checked_slice_mut(out_knots, kn)
            .unwrap()
            .copy_from_slice(&result.knots);
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (Vec<f64>, Vec<f64>) {
        let points = [
            [0., 0., 0., 1.],
            [1., 0., 0., 1.],
            [1., 1., 0., 1.],
            [2., 1., 0., 1.],
            [3., 1., 0., 1.],
            [3., 0., 0., 1.],
            [4., 0., 0., 1.],
        ];
        (
            points.into_iter().flatten().collect(),
            vec![0., 0., 0., 3., 3., 3., 6., 6., 6.],
        )
    }
    #[test]
    fn measured_cubic_joint() {
        let (c, k) = fixture();
        let out = remove(&c, &k, 3, 3).unwrap();
        assert_eq!(out.knots, vec![0., 0., 0., 3., 3., 6., 6., 6.]);
        assert_eq!(out.cvs, [&c[..12], &c[16..]].concat());
    }
    #[test]
    fn homogeneous_values_are_not_divided_or_refitted() {
        let (mut c, k) = fixture();
        c[16] = 2.8856180831641267;
        c[19] = 0.8047378541243649;
        let out = remove(&c, &k, 3, 3).unwrap();
        assert_eq!(&out.cvs[12..16], &c[16..20]);
    }
    #[test]
    fn nonuniform_parameter_span_is_preserved() {
        let (c, _) = fixture();
        let k = [-2., -2., -2., 0.25, 0.25, 0.25, 9., 9., 9.];
        assert_eq!(
            remove(&c, &k, 3, 3).unwrap().knots,
            vec![-2., -2., -2., 0.25, 0.25, 9., 9., 9.]
        );
    }
    #[test]
    fn unsupported_or_nonfinite_data_is_refused() {
        let (mut c, mut k) = fixture();
        assert!(remove(&c, &k, 3, 0).is_none());
        assert!(remove(&c, &k, 3, usize::MAX).is_none());
        k[4] = 3.1;
        assert!(remove(&c, &k, 3, 3).is_none());
        k[4] = 3.;
        c[3] = 0.;
        assert!(remove(&c, &k, 3, 3).is_none());
        c[3] = 1.;
        c[1] = f64::NAN;
        assert!(remove(&c, &k, 3, 3).is_none());
    }
    #[test]
    fn ffi_capacity_refusal_does_not_write() {
        let (c, k) = fixture();
        let mut out = [777.; 24];
        let mut knots = [888.; 8];
        let rc = unsafe {
            narwhal_pure_core_remove_join_cv_v1(
                c.as_ptr(),
                c.len(),
                k.as_ptr(),
                k.len(),
                3,
                3,
                out.as_mut_ptr(),
                23,
                knots.as_mut_ptr(),
                8,
            )
        };
        assert_eq!(rc, -2);
        assert_eq!(out, [777.; 24]);
        assert_eq!(knots, [888.; 8]);
    }
    #[test]
    fn ffi_accepts_measured_data_and_rejects_alias() {
        let (mut c, k) = fixture();
        let mut out = [0.; 24];
        let mut knots = [0.; 8];
        assert_eq!(
            unsafe {
                narwhal_pure_core_remove_join_cv_v1(
                    c.as_ptr(),
                    c.len(),
                    k.as_ptr(),
                    k.len(),
                    3,
                    3,
                    out.as_mut_ptr(),
                    24,
                    knots.as_mut_ptr(),
                    8,
                )
            },
            0
        );
        assert_eq!(
            unsafe {
                narwhal_pure_core_remove_join_cv_v1(
                    c.as_ptr(),
                    c.len(),
                    k.as_ptr(),
                    k.len(),
                    3,
                    3,
                    c.as_mut_ptr(),
                    24,
                    knots.as_mut_ptr(),
                    8,
                )
            },
            -2
        );
    }
}
