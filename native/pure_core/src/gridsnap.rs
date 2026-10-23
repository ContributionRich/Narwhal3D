//! CPlane-aware Grid Snap computation.
//!
//! The retained reference help does not specify tie behavior. OURS: exact half-spacing ties round
//! symmetrically away from zero.

const OK: i32 = 0;
const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const FRAME_TOLERANCE: f64 = 1.0e-12;

#[derive(Clone, Copy, Debug)]
struct V3([f64; 3]);

impl V3 {
    fn sub(self, other: Self) -> Self {
        Self([
            self.0[0] - other.0[0],
            self.0[1] - other.0[1],
            self.0[2] - other.0[2],
        ])
    }

    fn add(self, other: Self) -> Self {
        Self([
            self.0[0] + other.0[0],
            self.0[1] + other.0[1],
            self.0[2] + other.0[2],
        ])
    }

    fn scale(self, value: f64) -> Self {
        Self([self.0[0] * value, self.0[1] * value, self.0[2] * value])
    }

    fn dot(self, other: Self) -> f64 {
        self.0[0] * other.0[0] + self.0[1] * other.0[1] + self.0[2] * other.0[2]
    }

    fn cross(self, other: Self) -> Self {
        Self([
            self.0[1] * other.0[2] - self.0[2] * other.0[1],
            self.0[2] * other.0[0] - self.0[0] * other.0[2],
            self.0[0] * other.0[1] - self.0[1] * other.0[0],
        ])
    }

    fn finite(self) -> bool {
        self.0.iter().all(|value| value.is_finite())
    }
}

fn valid_frame(x: V3, y: V3, z: V3) -> bool {
    let unit = |axis: V3| (axis.dot(axis) - 1.0).abs() <= FRAME_TOLERANCE;
    unit(x)
        && unit(y)
        && unit(z)
        && x.dot(y).abs() <= FRAME_TOLERANCE
        && y.dot(z).abs() <= FRAME_TOLERANCE
        && z.dot(x).abs() <= FRAME_TOLERANCE
        && x.cross(y)
            .sub(z)
            .0
            .iter()
            .all(|v| v.abs() <= FRAME_TOLERANCE)
}

fn snap_components(
    point: V3,
    origin: V3,
    x: V3,
    y: V3,
    z: V3,
    spacing: f64,
    include_w: bool,
) -> Option<V3> {
    if !point.finite()
        || !origin.finite()
        || !x.finite()
        || !y.finite()
        || !z.finite()
        || !spacing.is_finite()
        || spacing <= 0.0
        || !valid_frame(x, y, z)
    {
        return None;
    }

    let relative = point.sub(origin);
    if !relative.finite() {
        return None;
    }
    let u = relative.dot(x);
    let v = relative.dot(y);
    let w = relative.dot(z);
    if !u.is_finite() || !v.is_finite() || !w.is_finite() {
        return None;
    }

    // OURS: f64::round is deterministic and rounds half ties away from zero.
    let snapped_u = (u / spacing).round() * spacing;
    let snapped_v = (v / spacing).round() * spacing;
    let snapped_w = if include_w {
        (w / spacing).round() * spacing
    } else {
        0.0
    };
    let result = origin
        .add(x.scale(snapped_u))
        .add(y.scale(snapped_v))
        .add(z.scale(snapped_w));
    (snapped_u.is_finite() && snapped_v.is_finite() && snapped_w.is_finite() && result.finite())
        .then_some(result)
}

fn snap(point: V3, origin: V3, x: V3, y: V3, z: V3, spacing: f64) -> Option<V3> {
    snap_components(point, origin, x, y, z, spacing, false)
}

fn snap_3d(point: V3, origin: V3, x: V3, y: V3, z: V3, spacing: f64) -> Option<V3> {
    snap_components(point, origin, x, y, z, spacing, true)
}

unsafe fn read_v3(ptr: *const f64) -> Option<V3> {
    let values = unsafe { crate::checked_slice(ptr, 3) }?;
    Some(V3([values[0], values[1], values[2]]))
}

/// Snap a world point to the U/V grid of a validated orthonormal, right-handed
/// CPlane and write a world point whose local W coordinate is exactly zero.
///
/// Returns zero on success and a negative status on rejection. The output is
/// untouched on every failure.
///
/// # Safety
///
/// Each input pointer must reference three initialized `f64` values. `output`
/// must reference three writable `f64` values and must not overlap an input.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_grid_snap(
    point: *const f64,
    origin: *const f64,
    x_axis: *const f64,
    y_axis: *const f64,
    z_axis: *const f64,
    spacing: f64,
    output: *mut f64,
) -> i32 {
    if output.is_null() {
        return ERR_NULL;
    }
    let Some(point) = (unsafe { read_v3(point) }) else {
        return ERR_NULL;
    };
    let Some(origin) = (unsafe { read_v3(origin) }) else {
        return ERR_NULL;
    };
    let Some(x_axis) = (unsafe { read_v3(x_axis) }) else {
        return ERR_NULL;
    };
    let Some(y_axis) = (unsafe { read_v3(y_axis) }) else {
        return ERR_NULL;
    };
    let Some(z_axis) = (unsafe { read_v3(z_axis) }) else {
        return ERR_NULL;
    };
    let Some(result) = snap(point, origin, x_axis, y_axis, z_axis, spacing) else {
        return ERR_INPUT;
    };
    let Some(output) = (unsafe { crate::checked_slice_mut(output, 3) }) else {
        return ERR_NULL;
    };
    output.copy_from_slice(&result.0);
    OK
}

/// Snap all three local CPlane coordinates. This is for unconstrained 3-D
/// manipulation; ordinary command-point Grid Snap intentionally remains U/V-only.
///
/// # Safety
///
/// The pointer contract is identical to [`narwhal_pure_core_grid_snap`].
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_grid_snap_3d(
    point: *const f64,
    origin: *const f64,
    x_axis: *const f64,
    y_axis: *const f64,
    z_axis: *const f64,
    spacing: f64,
    output: *mut f64,
) -> i32 {
    if output.is_null() {
        return ERR_NULL;
    }
    let Some(point) = (unsafe { read_v3(point) }) else {
        return ERR_NULL;
    };
    let Some(origin) = (unsafe { read_v3(origin) }) else {
        return ERR_NULL;
    };
    let Some(x_axis) = (unsafe { read_v3(x_axis) }) else {
        return ERR_NULL;
    };
    let Some(y_axis) = (unsafe { read_v3(y_axis) }) else {
        return ERR_NULL;
    };
    let Some(z_axis) = (unsafe { read_v3(z_axis) }) else {
        return ERR_NULL;
    };
    let Some(result) = snap_3d(point, origin, x_axis, y_axis, z_axis, spacing) else {
        return ERR_INPUT;
    };
    let Some(output) = (unsafe { crate::checked_slice_mut(output, 3) }) else {
        return ERR_NULL;
    };
    output.copy_from_slice(&result.0);
    OK
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOP: (V3, V3, V3) = (
        V3([1.0, 0.0, 0.0]),
        V3([0.0, 1.0, 0.0]),
        V3([0.0, 0.0, 1.0]),
    );

    fn close(actual: V3, expected: [f64; 3]) {
        for (a, e) in actual.0.into_iter().zip(expected) {
            assert!((a - e).abs() <= 1.0e-12, "{actual:?} != {expected:?}");
        }
    }

    #[test]
    fn top_front_and_right_project_to_their_cplanes() {
        let origin = V3([0.0, 0.0, 0.0]);
        close(
            snap(V3([1.2, -2.6, 9.0]), origin, TOP.0, TOP.1, TOP.2, 1.0).unwrap(),
            [1.0, -3.0, 0.0],
        );
        close(
            snap(
                V3([1.2, 9.0, -2.6]),
                origin,
                V3([1.0, 0.0, 0.0]),
                V3([0.0, 0.0, 1.0]),
                V3([0.0, -1.0, 0.0]),
                1.0,
            )
            .unwrap(),
            [1.0, 0.0, -3.0],
        );
        close(
            snap(
                V3([9.0, 1.2, -2.6]),
                origin,
                V3([0.0, 1.0, 0.0]),
                V3([0.0, 0.0, 1.0]),
                V3([1.0, 0.0, 0.0]),
                1.0,
            )
            .unwrap(),
            [0.0, 1.0, -3.0],
        );
    }

    #[test]
    fn moved_rotated_plane_uses_local_uv_not_world_xy() {
        let q = std::f64::consts::FRAC_1_SQRT_2;
        let origin = V3([10.0, -4.0, 3.0]);
        let x = V3([q, q, 0.0]);
        let y = V3([-q, q, 0.0]);
        let z = V3([0.0, 0.0, 1.0]);
        let point = origin
            .add(x.scale(1.6))
            .add(y.scale(-2.4))
            .add(z.scale(19.0));
        close(
            snap(point, origin, x, y, z, 1.0).unwrap(),
            origin.add(x.scale(2.0)).add(y.scale(-2.0)).0,
        );
    }

    #[test]
    fn three_dimensional_snap_preserves_nonzero_depth_on_the_native_lattice() {
        let origin = V3([10.0, -4.0, 3.0]);
        close(
            snap_3d(V3([11.2, -6.6, 5.6]), origin, TOP.0, TOP.1, TOP.2, 1.0).unwrap(),
            [11.0, -7.0, 6.0],
        );
    }

    #[test]
    fn negative_values_and_half_ties_are_symmetric_away_from_zero() {
        let origin = V3([0.0, 0.0, 0.0]);
        close(
            snap(V3([-1.49, -1.51, 4.0]), origin, TOP.0, TOP.1, TOP.2, 1.0).unwrap(),
            [-1.0, -2.0, 0.0],
        );
        close(
            snap(V3([0.5, -0.5, 4.0]), origin, TOP.0, TOP.1, TOP.2, 1.0).unwrap(),
            [1.0, -1.0, 0.0],
        );
    }

    #[test]
    fn invalid_frame_spacing_nonfinite_and_null_leave_output_untouched() {
        let point = [1.0, 2.0, 3.0];
        let origin = [0.0, 0.0, 0.0];
        let good_x = [1.0, 0.0, 0.0];
        let good_y = [0.0, 1.0, 0.0];
        let good_z = [0.0, 0.0, 1.0];
        let bad_cases = [
            ([f64::NAN, 0.0, 0.0], good_y, good_z, 1.0),
            ([1.0 + 2.0e-12, 0.0, 0.0], good_y, good_z, 1.0),
            (good_x, [1.0e-10, 1.0, 0.0], good_z, 1.0),
            (good_x, good_y, [0.0, 0.0, -1.0], 1.0),
            (good_x, good_y, good_z, 0.0),
            (good_x, good_y, good_z, -1.0),
            (good_x, good_y, good_z, f64::INFINITY),
        ];
        for (x, y, z, spacing) in bad_cases {
            let mut output = [91.0, 92.0, 93.0];
            let status = unsafe {
                narwhal_pure_core_grid_snap(
                    point.as_ptr(),
                    origin.as_ptr(),
                    x.as_ptr(),
                    y.as_ptr(),
                    z.as_ptr(),
                    spacing,
                    output.as_mut_ptr(),
                )
            };
            assert_ne!(status, OK);
            assert_eq!(output, [91.0, 92.0, 93.0]);
        }

        for bad_point in [
            [f64::NAN, 2.0, 3.0],
            [1.0, f64::INFINITY, 3.0],
            [f64::MAX, -f64::MAX, 0.0],
        ] {
            let mut output = [91.0, 92.0, 93.0];
            let status = unsafe {
                narwhal_pure_core_grid_snap(
                    bad_point.as_ptr(),
                    origin.as_ptr(),
                    good_x.as_ptr(),
                    good_y.as_ptr(),
                    good_z.as_ptr(),
                    0.1,
                    output.as_mut_ptr(),
                )
            };
            assert_ne!(status, OK);
            assert_eq!(output, [91.0, 92.0, 93.0]);
        }

        let bad_origin = [0.0, f64::NEG_INFINITY, 0.0];
        let mut output = [91.0, 92.0, 93.0];
        let status = unsafe {
            narwhal_pure_core_grid_snap(
                point.as_ptr(),
                bad_origin.as_ptr(),
                good_x.as_ptr(),
                good_y.as_ptr(),
                good_z.as_ptr(),
                1.0,
                output.as_mut_ptr(),
            )
        };
        assert_eq!(status, ERR_INPUT);
        assert_eq!(output, [91.0, 92.0, 93.0]);

        let status = unsafe {
            narwhal_pure_core_grid_snap(
                std::ptr::null(),
                origin.as_ptr(),
                good_x.as_ptr(),
                good_y.as_ptr(),
                good_z.as_ptr(),
                1.0,
                output.as_mut_ptr(),
            )
        };
        assert_eq!(status, ERR_NULL);
        assert_eq!(output, [91.0, 92.0, 93.0]);
    }
}
