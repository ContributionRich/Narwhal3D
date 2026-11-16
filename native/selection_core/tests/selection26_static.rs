//! Static (handwritten) C ABI replay of `spike/test_rust_selection_core.py`.
//!
//! The 13 *randomized* batches of that Python script are already replayed by
//! `selection26_random.rs` against the frozen corpora under `data/`. This file
//! ports the remaining **static** checks: every direct C ABI call with
//! hand-written expected values, preserving exact statuses, output capacity,
//! null-pointer handling, non-finite inputs, exact-distance ties, the
//! adversarial-ULP (bit-exact `math.hypot`) cases, and the `isize::MAX`
//! byte-span rejection.
//!
//! All values are compared bit-exactly (sentinels `f64::NAN`,
//! `f64::INFINITY`, `u64::MAX` are compared via their exact float/integer
//! encoding, matching the original Python `math.isinf`/`math.isnan`/
//! `(1 << 64) - 1` checks).

// ---------------------------------------------------------------------------
// Constants (from the original Python script)
// ---------------------------------------------------------------------------
const IDENTITY: [f64; 16] = [
    1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0,
];
const PERSPECTIVE: [f64; 16] = [
    1.2, 0.1, 0.0, 0.2, 0.0, 1.1, 0.1, -0.1, 0.0, 0.0, 1.0, 0.0, 0.15, -0.05, 0.2, 1.0,
];

// C ABI error codes (native/selection_core/src/lib.rs)
const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;

const U64_MAX: u64 = u64::MAX;
const F64_NAN: f64 = f64::NAN;
const F64_INF: f64 = f64::INFINITY;

/// `sys.maxsize // sizeof(c_double) // stride + 1` -- a count whose byte span
/// exceeds `isize::MAX`, which the C ABI rejects with `ERR_SIZE`.
/// `stride` is the number of f64 elements per logical element (2, 3 or 4),
/// matching the original Python formula exactly.
fn oversized_count(stride: usize) -> usize {
    (isize::MAX as usize) / 8 / stride + 1
}

// ---------------------------------------------------------------------------
// Two-pass / query helpers mirroring the original Python wrappers
// ---------------------------------------------------------------------------

/// `narwhal_project_points_f32`: f32-input projection.
fn f32_project(
    matrix: &[f64],
    points: &[f64],
    width: f64,
    height: f64,
) -> (i32, Vec<Option<[f64; 2]>>) {
    let f32points: Vec<f32> = points.iter().map(|v| *v as f32).collect();
    let count = points.len() / 3;
    let mut out = vec![0.0f64; count * 2];
    let mut visible = vec![0u8; count];
    let code = unsafe {
        narwhal_selection_core::narwhal_project_points_f32(
            matrix.as_ptr(),
            f32points.as_ptr(),
            count,
            width,
            height,
            out.as_mut_ptr(),
            visible.as_mut_ptr(),
        )
    };
    let result = (0..count)
        .map(|i| {
            if visible[i] != 0 {
                Some([out[i * 2], out[i * 2 + 1]])
            } else {
                None
            }
        })
        .collect();
    (code, result)
}

/// `narwhal_project_display_points_f64`: f64 display projection.
fn display_project(
    matrix: &[f64],
    points: &[f64],
    width: f64,
    height: f64,
) -> (i32, Vec<Option<[f64; 2]>>) {
    let count = points.len() / 3;
    let mut out = vec![0.0f64; count * 2];
    let mut visible = vec![0u8; count];
    let code = unsafe {
        narwhal_selection_core::narwhal_project_display_points_f64(
            matrix.as_ptr(),
            points.as_ptr(),
            count,
            width,
            height,
            out.as_mut_ptr(),
            visible.as_mut_ptr(),
        )
    };
    let result = (0..count)
        .map(|i| {
            if visible[i] != 0 {
                Some([out[i * 2], out[i * 2 + 1]])
            } else {
                None
            }
        })
        .collect();
    (code, result)
}

/// `narwhal_any_screen_segment_crosses_rect_f64`.
fn screen_any(segments: &[f64], count: usize, rect: &[f64]) -> i32 {
    unsafe {
        narwhal_selection_core::narwhal_any_screen_segment_crosses_rect_f64(
            segments.as_ptr(),
            count,
            rect.as_ptr(),
        )
    }
}

/// `narwhal_projected_segments_rect_query_f32`.
fn projected_query(
    matrix: &[f64],
    segments: &[f32],
    count: usize,
    width: f64,
    height: f64,
    rect: &[f64],
) -> (i32, u8) {
    let mut evidence = 0u8;
    let code = unsafe {
        narwhal_selection_core::narwhal_projected_segments_rect_query_f32(
            matrix.as_ptr(),
            segments.as_ptr(),
            count,
            width,
            height,
            rect.as_ptr(),
            &mut evidence,
        )
    };
    (code, evidence)
}

/// `narwhal_projected_curves_rect_query_f32`.
#[allow(clippy::too_many_arguments)]
fn projected_batch(
    matrix: &[f64],
    segments: &[f32],
    segment_count: usize,
    offsets: &[u64],
    curve_count: usize,
    width: f64,
    height: f64,
    rect: &[f64],
) -> (i32, Vec<u8>) {
    let mut statuses = vec![0u8; curve_count];
    let code = unsafe {
        narwhal_selection_core::narwhal_projected_curves_rect_query_f32(
            matrix.as_ptr(),
            segments.as_ptr(),
            segment_count,
            offsets.as_ptr(),
            curve_count,
            width,
            height,
            rect.as_ptr(),
            statuses.as_mut_ptr(),
        )
    };
    (code, statuses)
}

/// `narwhal_nearest_projected_curve_f32`.
#[allow(clippy::too_many_arguments)]
fn nearest_batch(
    matrix: &[f64],
    segments: &[f32],
    segment_count: usize,
    offsets: &[u64],
    curve_count: usize,
    width: f64,
    height: f64,
    mouse: [f64; 2],
    limit: f64,
) -> (i32, u64, f64) {
    let mut index = U64_MAX;
    let mut distance = F64_INF;
    let code = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_curve_f32(
            matrix.as_ptr(),
            segments.as_ptr(),
            segment_count,
            offsets.as_ptr(),
            curve_count,
            width,
            height,
            mouse.as_ptr(),
            limit,
            &mut index,
            &mut distance,
        )
    };
    (code, index, distance)
}

/// `narwhal_nearest_screen_polyline_f64`.
fn screen_polyline(
    pixels: &[f64],
    visible: &[u8],
    offsets: &[u64],
    mouse: [f64; 2],
    limit: f64,
) -> (i32, u64, f64) {
    let curve_count = offsets.len() - 1;
    let mut index = U64_MAX;
    let mut distance = F64_INF;
    let code = unsafe {
        narwhal_selection_core::narwhal_nearest_screen_polyline_f64(
            pixels.as_ptr(),
            visible.as_ptr(),
            pixels.len() / 2,
            offsets.as_ptr(),
            curve_count,
            mouse.as_ptr(),
            limit,
            &mut index,
            &mut distance,
        )
    };
    (code, index, distance)
}

/// `narwhal_screen_boxes_from_clip_f64`.
fn clip_boxes(
    clip: &[f64],
    offsets: &[u64],
    flags: &[u8],
    width: f64,
    height: f64,
) -> (i32, Vec<Option<[f64; 4]>>) {
    let object_count = flags.len();
    let mut boxes = vec![0.0f64; object_count * 4];
    let mut valid = vec![0u8; object_count];
    let code = unsafe {
        narwhal_selection_core::narwhal_screen_boxes_from_clip_f64(
            clip.as_ptr(),
            clip.len() / 4,
            offsets.as_ptr(),
            object_count,
            flags.as_ptr(),
            width,
            height,
            boxes.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    let result = (0..object_count)
        .map(|i| {
            if valid[i] != 0 {
                Some([
                    boxes[i * 4],
                    boxes[i * 4 + 1],
                    boxes[i * 4 + 2],
                    boxes[i * 4 + 3],
                ])
            } else {
                None
            }
        })
        .collect();
    (code, result)
}

/// `narwhal_screen_boxes_from_world_f64`.
fn world_boxes(
    matrix: &[f64],
    points: &[f64],
    offsets: &[u64],
    flags: &[u8],
    width: f64,
    height: f64,
) -> (i32, Vec<Option<[f64; 4]>>) {
    let object_count = flags.len();
    let mut boxes = vec![0.0f64; object_count * 4];
    let mut valid = vec![0u8; object_count];
    let code = unsafe {
        narwhal_selection_core::narwhal_screen_boxes_from_world_f64(
            matrix.as_ptr(),
            points.as_ptr(),
            points.len() / 3,
            offsets.as_ptr(),
            object_count,
            flags.as_ptr(),
            width,
            height,
            boxes.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    let result = (0..object_count)
        .map(|i| {
            if valid[i] != 0 {
                Some([
                    boxes[i * 4],
                    boxes[i * 4 + 1],
                    boxes[i * 4 + 2],
                    boxes[i * 4 + 3],
                ])
            } else {
                None
            }
        })
        .collect();
    (code, result)
}

/// `narwhal_world_rect_hit_mask_f64`.
#[allow(clippy::too_many_arguments)]
fn world_rect_mask(
    matrix: &[f64],
    points: &[f64],
    offsets: &[u64],
    flags: &[u8],
    width: f64,
    height: f64,
    rect: &[f64],
    crossing: u8,
) -> (i32, Vec<u8>) {
    let object_count = flags.len();
    let mut hits = vec![0u8; object_count];
    let code = unsafe {
        narwhal_selection_core::narwhal_world_rect_hit_mask_f64(
            matrix.as_ptr(),
            points.as_ptr(),
            points.len() / 3,
            offsets.as_ptr(),
            object_count,
            flags.as_ptr(),
            width,
            height,
            rect.as_ptr(),
            crossing,
            hits.as_mut_ptr(),
        )
    };
    (code, hits)
}

/// `narwhal_screen_rects_from_aabbs_f64`.
fn aabb_rects(
    matrix: &[f64],
    lower: &[f64],
    upper: &[f64],
    width: f64,
    height: f64,
) -> (i32, Vec<[f64; 4]>, Vec<u8>, Vec<u8>) {
    let count = lower.len() / 3;
    let mut rects = vec![0.0f64; count * 4];
    let mut unbounded = vec![0u8; count];
    let mut behind = vec![0u8; count];
    let code = unsafe {
        narwhal_selection_core::narwhal_screen_rects_from_aabbs_f64(
            matrix.as_ptr(),
            lower.as_ptr(),
            upper.as_ptr(),
            count,
            width,
            height,
            rects.as_mut_ptr(),
            unbounded.as_mut_ptr(),
            behind.as_mut_ptr(),
        )
    };
    let r = (0..count)
        .map(|i| {
            [
                rects[i * 4],
                rects[i * 4 + 1],
                rects[i * 4 + 2],
                rects[i * 4 + 3],
            ]
        })
        .collect();
    (code, r, unbounded, behind)
}

/// `narwhal_nearest_projected_polylines_f64`.
fn projected_near(
    matrix: &[f64],
    points: &[f64],
    offsets: &[u64],
    curve_count: usize,
    width: f64,
    height: f64,
    mouse: [f64; 2],
) -> (i32, Vec<Option<(u64, f64, f64)>>) {
    let mut segments = vec![0u64; curve_count];
    let mut parameters = vec![0.0f64; curve_count];
    let mut distances = vec![0.0f64; curve_count];
    let mut valid = vec![0u8; curve_count];
    let code = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_polylines_f64(
            matrix.as_ptr(),
            points.as_ptr(),
            points.len() / 3,
            offsets.as_ptr(),
            curve_count,
            width,
            height,
            mouse.as_ptr(),
            segments.as_mut_ptr(),
            parameters.as_mut_ptr(),
            distances.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    let result = (0..curve_count)
        .map(|i| {
            if valid[i] != 0 {
                Some((segments[i], parameters[i], distances[i]))
            } else {
                None
            }
        })
        .collect();
    (code, result)
}

/// `narwhal_nearest_projected_point_f64`.
fn nearest_point(
    matrix: &[f64],
    points: &[f64],
    width: f64,
    height: f64,
    mouse: [f64; 2],
    limit: f64,
) -> (i32, u64, f64) {
    let count = points.len() / 3;
    let mut index = U64_MAX;
    let mut distance = F64_INF;
    let code = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_point_f64(
            matrix.as_ptr(),
            points.as_ptr(),
            count,
            width,
            height,
            mouse.as_ptr(),
            limit,
            &mut index,
            &mut distance,
        )
    };
    (code, index, distance)
}

/// `narwhal_projected_points_rect_query_f64`.
fn points_rect(
    matrix: &[f64],
    points: &[f64],
    width: f64,
    height: f64,
    rect: &[f64],
) -> (i32, Vec<u8>) {
    let count = points.len() / 3;
    let mut inside = vec![0u8; count];
    let code = unsafe {
        narwhal_selection_core::narwhal_projected_points_rect_query_f64(
            matrix.as_ptr(),
            points.as_ptr(),
            count,
            width,
            height,
            rect.as_ptr(),
            inside.as_mut_ptr(),
        )
    };
    (code, inside)
}

/// `narwhal_projected_snap_distances_f64`.
fn snap_distances(
    matrix: &[f64],
    points: &[f64],
    width: f64,
    height: f64,
    mouse: [f64; 2],
    radius: f64,
) -> (i32, Vec<u64>, Vec<f64>) {
    let count = points.len() / 3;
    let mut indices = vec![0u64; count];
    let mut distances = vec![0.0f64; count];
    let mut survivor_count = 0u64;
    let code = unsafe {
        narwhal_selection_core::narwhal_projected_snap_distances_f64(
            matrix.as_ptr(),
            points.as_ptr(),
            count,
            width,
            height,
            mouse.as_ptr(),
            radius,
            indices.as_mut_ptr(),
            distances.as_mut_ptr(),
            &mut survivor_count,
        )
    };
    let kept = survivor_count as usize;
    (code, indices[..kept].to_vec(), distances[..kept].to_vec())
}

/// `narwhal_ordered_selection_apply_u32`.
fn selection_apply(current: &[u32], hits: &[u32], action: u8) -> (i32, Vec<u32>) {
    let capacity = current
        .iter()
        .chain(hits.iter())
        .cloned()
        .collect::<std::collections::HashSet<_>>()
        .len();
    let mut output = vec![0u32; capacity];
    let mut written = U64_MAX;
    let code = unsafe {
        narwhal_selection_core::narwhal_ordered_selection_apply_u32(
            current.as_ptr(),
            current.len(),
            hits.as_ptr(),
            hits.len(),
            action,
            output.as_mut_ptr(),
            capacity,
            &mut written,
        )
    };
    (code, output[..written as usize].to_vec())
}

/// `narwhal_oriented_bbox_center_f64`.
fn oriented_bbox(
    points: &[f64],
    origin: [f64; 3],
    xaxis: [f64; 3],
    yaxis: [f64; 3],
) -> (i32, u8, [f64; 3]) {
    let mut output = [F64_NAN; 3];
    let mut valid = 255u8;
    let frame = [
        origin[0], origin[1], origin[2], xaxis[0], xaxis[1], xaxis[2], yaxis[0], yaxis[1], yaxis[2],
    ];
    let code = unsafe {
        narwhal_selection_core::narwhal_oriented_bbox_center_f64(
            points.as_ptr(),
            points.len() / 3,
            frame.as_ptr(),
            output.as_mut_ptr(),
            &mut valid,
        )
    };
    (code, valid, output)
}

/// `narwhal_intersection_snaps_f64` (two-pass).
fn intersections(
    points: &[f64],
    offsets: &[u64],
    target_count: usize,
    tol: f64,
) -> (i32, Vec<([f64; 3], u64)>) {
    let mut required = 0u64;
    let code = unsafe {
        narwhal_selection_core::narwhal_intersection_snaps_f64(
            points.as_ptr(),
            points.len() / 3,
            offsets.as_ptr(),
            target_count,
            tol,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            0,
            &mut required,
        )
    };
    if code != 0 {
        return (code, vec![]);
    }
    let count = required as usize;
    let mut xyz = vec![0.0f64; count * 3];
    let mut targets = vec![0u64; count];
    let mut written = 0u64;
    let code2 = unsafe {
        narwhal_selection_core::narwhal_intersection_snaps_f64(
            points.as_ptr(),
            points.len() / 3,
            offsets.as_ptr(),
            target_count,
            tol,
            xyz.as_mut_ptr(),
            targets.as_mut_ptr(),
            count,
            &mut written,
        )
    };
    let mut rows = Vec::new();
    for i in 0..written as usize {
        rows.push(([xyz[i * 3], xyz[i * 3 + 1], xyz[i * 3 + 2]], targets[i]));
    }
    (code2, rows)
}

/// `narwhal_dynamic_perp_tan_snaps_f64` (two-pass).
fn dynamic_snaps(
    points: &[f64],
    offsets: &[u64],
    closed: &[u8],
    target_count: usize,
    base: [f64; 3],
    mode_mask: u8,
) -> (i32, Vec<([f64; 3], u64, u8)>) {
    let mut required = 0u64;
    let code = unsafe {
        narwhal_selection_core::narwhal_dynamic_perp_tan_snaps_f64(
            points.as_ptr(),
            points.len() / 3,
            offsets.as_ptr(),
            closed.as_ptr(),
            target_count,
            base.as_ptr(),
            mode_mask,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            0,
            &mut required,
        )
    };
    if code != 0 {
        return (code, vec![]);
    }
    let count = required as usize;
    let mut xyz = vec![0.0f64; count * 3];
    let mut targets = vec![0u64; count];
    let mut kinds = vec![0u8; count];
    let mut written = 0u64;
    let code2 = unsafe {
        narwhal_selection_core::narwhal_dynamic_perp_tan_snaps_f64(
            points.as_ptr(),
            points.len() / 3,
            offsets.as_ptr(),
            closed.as_ptr(),
            target_count,
            base.as_ptr(),
            mode_mask,
            xyz.as_mut_ptr(),
            targets.as_mut_ptr(),
            kinds.as_mut_ptr(),
            count,
            &mut written,
        )
    };
    let mut rows = Vec::new();
    for i in 0..written as usize {
        rows.push((
            [xyz[i * 3], xyz[i * 3 + 1], xyz[i * 3 + 2]],
            targets[i],
            kinds[i],
        ));
    }
    (code2, rows)
}

/// `narwhal_discrete_snap_survivors_f64` (two-pass).
#[allow(clippy::too_many_arguments)]
fn discrete_snaps(
    points: &[f64],
    point_offsets: &[u64],
    vertices: &[f64],
    vertex_offsets: &[u64],
    knots: &[f64],
    knot_offsets: &[u64],
    centers: &[f64],
    center_valid: &[u8],
    flags: &[u8],
    frame: &[f64; 9],
    matrix: &[f64; 16],
    mouse: [f64; 2],
    radius: f64,
    mode_mask: u16,
) -> (i32, Vec<([f64; 3], u64, u8)>) {
    let target_count = flags.len();
    let mut required = 0u64;
    let code = unsafe {
        narwhal_selection_core::narwhal_discrete_snap_survivors_f64(
            points.as_ptr(),
            points.len() / 3,
            point_offsets.as_ptr(),
            vertices.as_ptr(),
            vertices.len() / 3,
            vertex_offsets.as_ptr(),
            knots.as_ptr(),
            knots.len() / 3,
            knot_offsets.as_ptr(),
            centers.as_ptr(),
            center_valid.as_ptr(),
            flags.as_ptr(),
            target_count,
            frame.as_ptr(),
            matrix.as_ptr(),
            100.0,
            100.0,
            mouse.as_ptr(),
            radius,
            mode_mask,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            0,
            &mut required,
        )
    };
    if code != 0 {
        return (code, vec![]);
    }
    let count = required as usize;
    let mut xyz = vec![0.0f64; count * 3];
    let mut targets = vec![0u64; count];
    let mut modes = vec![0u8; count];
    let mut distances = vec![0.0f64; count];
    let mut written = 0u64;
    let code2 = unsafe {
        narwhal_selection_core::narwhal_discrete_snap_survivors_f64(
            points.as_ptr(),
            points.len() / 3,
            point_offsets.as_ptr(),
            vertices.as_ptr(),
            vertices.len() / 3,
            vertex_offsets.as_ptr(),
            knots.as_ptr(),
            knots.len() / 3,
            knot_offsets.as_ptr(),
            centers.as_ptr(),
            center_valid.as_ptr(),
            flags.as_ptr(),
            target_count,
            frame.as_ptr(),
            matrix.as_ptr(),
            100.0,
            100.0,
            mouse.as_ptr(),
            radius,
            mode_mask,
            xyz.as_mut_ptr(),
            targets.as_mut_ptr(),
            modes.as_mut_ptr(),
            distances.as_mut_ptr(),
            count,
            &mut written,
        )
    };
    let mut rows = Vec::new();
    for i in 0..written as usize {
        rows.push((
            [xyz[i * 3], xyz[i * 3 + 1], xyz[i * 3 + 2]],
            targets[i],
            modes[i],
        ));
    }
    (code2, rows)
}

// ---------------------------------------------------------------------------
// C ABI version
// ---------------------------------------------------------------------------
#[test]
fn c_abi_version_is_pinned() {
    // #1 "C ABI version is pinned"
    assert_eq!(
        narwhal_selection_core::narwhal_selection_core_abi_version(),
        1
    );
}

// ---------------------------------------------------------------------------
// Discrete snaps (narwhal_discrete_snap_survivors_f64)
// ---------------------------------------------------------------------------
#[test]
fn discrete_snaps_arc_mid_closed_seam_point_only() {
    // #2 "native discrete snaps preserve arc Mid, closed seam Mid, and point-only mode"
    let static_points: [f64; 21] = [
        0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 2.0, 2.0, 0.0,
        0.0, 0.0, 0.0,
    ];
    let offsets = [0u64, 3, 6, 7];
    let vertices = [
        0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 2.0, 2.0, 0.0,
        0.0, 0.0, 0.0,
    ];
    let vertex_offsets = [0u64, 3, 6, 7];
    let knots = [4.0, 0.0, 0.0];
    let knot_offsets = [0u64, 1, 1, 1];
    let centers = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0];
    let center_valid = [1u8, 0, 0];
    let flags = [0u8, 2, 1];
    let frame = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0];
    let (code, rows) = discrete_snaps(
        &static_points,
        &offsets,
        &vertices,
        &vertex_offsets,
        &knots,
        &knot_offsets,
        &centers,
        &center_valid,
        &flags,
        &frame,
        &IDENTITY,
        [50.0, 50.0],
        1000.0,
        63,
    );
    assert_eq!(code, 0, "code {code}, rows {rows:?}");
    let has_arc_mid = rows
        .iter()
        .any(|(p, t, m)| *p == [5.0, 0.0, 0.0] && *t == 0 && *m == 2);
    let has_seam_mid = rows
        .iter()
        .any(|(p, t, m)| *p == [1.0, 1.0, 0.0] && *t == 1 && *m == 2);
    let point_modes: Vec<u8> = rows
        .iter()
        .filter(|(_, t, _)| *t == 2)
        .map(|(_, _, m)| *m)
        .collect();
    assert!(has_arc_mid, "arc Mid missing in {rows:?}");
    assert!(has_seam_mid, "closed seam Mid missing in {rows:?}");
    assert_eq!(point_modes, vec![5], "point-only mode rows {rows:?}");
}

// ---------------------------------------------------------------------------
// Oriented bbox (narwhal_oriented_bbox_center_f64)
// ---------------------------------------------------------------------------
#[test]
fn oriented_bbox_rotated_cplane_frame() {
    // #3 "native oriented bbox uses the rotated CPlane frame"
    let root_half = std::f64::consts::SQRT_2 / 2.0;
    let points = [0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 1.0, 0.0];
    let (code, valid, center) = oriented_bbox(
        &points,
        [0.0, 0.0, 0.0],
        [root_half, root_half, 0.0],
        [-root_half, root_half, 0.0],
    );
    assert_eq!(code, 0);
    assert_eq!(valid, 1);
    let expected = [0.75, 0.25, 0.0];
    for (a, b) in center.iter().zip(expected) {
        assert!((a - b).abs() < 1e-12, "center {center:?}");
    }
}

#[test]
fn oriented_bbox_derived_z_nonzero_origin() {
    // #4 "native oriented bbox includes derived Z and nonzero plane origin"
    let points = [12.0, 17.0, 34.0, 16.0, 19.0, 38.0];
    let (code, valid, center) = oriented_bbox(
        &points,
        [10.0, 20.0, 30.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    );
    assert_eq!(
        (code, valid, center),
        (0, 1, [14.0, 18.0, 36.0]),
        "(code {code}, valid {valid}, center {center:?})"
    );
}

// ---------------------------------------------------------------------------
// Ordered selection apply (narwhal_ordered_selection_apply_u32)
// ---------------------------------------------------------------------------
#[test]
fn selection_apply_replace() {
    // #5 "native ordered Replace keeps first hit occurrences"
    assert_eq!(
        selection_apply(&[3, 1, 3, 2], &[2, 4, 4, 1, 5], 0),
        (0, vec![2, 4, 1, 5])
    );
}

#[test]
fn selection_apply_add() {
    // #6 "native ordered Add keeps current first then unseen hits"
    assert_eq!(
        selection_apply(&[3, 1, 3, 2], &[2, 4, 4, 1, 5], 1),
        (0, vec![3, 1, 2, 4, 5])
    );
}

#[test]
fn selection_apply_remove() {
    // #7 "native ordered Remove preserves surviving current order"
    assert_eq!(
        selection_apply(&[3, 1, 3, 2], &[2, 4, 4, 1, 5], 2),
        (0, vec![3])
    );
}

// ---------------------------------------------------------------------------
// Intersection snaps (narwhal_intersection_snaps_f64)
// ---------------------------------------------------------------------------
#[test]
fn intersections_pair_segment_order_dedup() {
    // #8 "native intersections preserve pair/segment order and first-seen dedup"
    let lines = [
        0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 10.0, 0.0, 0.0, 8.0, -2.0, 0.0, 8.0, 2.0, 0.0, 5.0, -2.0,
        0.0, 5.0, 2.0, 0.0, 2.0, -2.0, 1.0, 2.0, 2.0, 1.0,
    ];
    let offsets = [0u64, 3, 5, 7, 9];
    let (code, rows) = intersections(&lines, &offsets, 4, 0.0);
    assert_eq!(code, 0, "code {code}, rows {rows:?}");
    assert_eq!(
        rows,
        vec!(([8.0, 0.0, 0.0], 0), ([5.0, 0.0, 0.0], 0)),
        "rows {rows:?}"
    );
}

#[test]
fn intersections_closest_pairs_at_tolerance() {
    // #9 "native intersections include closest segment pairs exactly at tolerance"
    let lines = [
        0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 10.0, 0.0, 0.0, 8.0, -2.0, 0.0, 8.0, 2.0, 0.0, 5.0, -2.0,
        0.0, 5.0, 2.0, 0.0, 2.0, -2.0, 1.0, 2.0, 2.0, 1.0,
    ];
    let offsets = [0u64, 3, 5, 7, 9];
    let (code, rows) = intersections(&lines, &offsets, 4, 1.0);
    assert_eq!(code, 0, "code {code}, rows {rows:?}");
    assert!(rows.contains(&([2.0, 0.0, 0.5], 0)), "rows {rows:?}");
}

// ---------------------------------------------------------------------------
// Dynamic perp/tan snaps (narwhal_dynamic_perp_tan_snaps_f64)
// ---------------------------------------------------------------------------
#[test]
fn dynamic_perp_interior_feet_only() {
    // #10 "native Perp keeps only strict interior segment feet"
    let dynamic_lines = [
        0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, -1.0, 0.0,
    ];
    let offsets = [0u64, 2, 6];
    let closed = [0u8, 1];
    let (code, rows) = dynamic_snaps(&dynamic_lines, &offsets, &closed, 2, [4.0, 3.0, 0.0], 1);
    assert_eq!(code, 0, "code {code}, rows {rows:?}");
    assert_eq!(rows[0], ([4.0, 0.0, 0.0], 0, 1), "rows {rows:?}");
    for (p, t, _) in &rows {
        if *t == 0 {
            assert!(
                *p != [0.0, 0.0, 0.0] && *p != [10.0, 0.0, 0.0],
                "endpoint foot {rows:?}"
            );
        }
    }
}

#[test]
fn dynamic_perp_rejects_endpoint_foot() {
    // #11 "native Perp rejects a foot exactly at a segment endpoint"
    let dynamic_lines = [
        0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, -1.0, 0.0,
    ];
    let offsets = [0u64, 2, 6];
    let closed = [0u8, 1];
    let (code, rows) = dynamic_snaps(&dynamic_lines, &offsets, &closed, 2, [0.0, 3.0, 0.0], 1);
    assert_eq!(code, 0, "code {code}, rows {rows:?}");
    for (_, t, _) in &rows {
        assert!(*t != 0, "Perp row at endpoint {rows:?}");
    }
}

#[test]
fn dynamic_tan_sign_crossings_closed_seam() {
    // #12 "native Tan preserves sign crossings across a closed seam"
    let dynamic_lines = [
        0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, -1.0, 0.0,
    ];
    let offsets = [0u64, 2, 6];
    let closed = [0u8, 1];
    let (code, rows) = dynamic_snaps(&dynamic_lines, &offsets, &closed, 2, [2.0, -2.0, 0.0], 2);
    assert_eq!(code, 0, "code {code}, rows {rows:?}");
    assert_eq!(
        rows,
        vec!(([0.75, 0.25, 0.0], 1, 2), ([-0.25, -0.75, 0.0], 1, 2)),
        "rows {rows:?}"
    );
}

// ---------------------------------------------------------------------------
// Batched projection (narwhal_project_points_f32)
// ---------------------------------------------------------------------------
#[test]
fn batched_projection_valid_visibility_and_reference() {
    // #13 "batched projection accepts valid inputs"
    // #14 "projection preserves eye-plane visibility decisions"
    // #15 "f32-input Rust projection matches the scalar f64 reference"
    let points: [f64; 15] = [
        -1.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0, -10.0, 2.0, -3.0, 4.0,
    ];
    let (code, actual) = f32_project(&PERSPECTIVE, &points, 800.0, 600.0);
    assert_eq!(code, 0, "code {code}");
    // #14 visibility: point 3 (0,0,-10) is behind the eye plane under PERSPECTIVE.
    let actual_vis = actual.iter().map(|v| v.is_some()).collect::<Vec<_>>();
    assert_eq!(
        actual_vis,
        vec![true, true, true, false, true],
        "visibility {actual:?}"
    );
    // #15 f32-input matches scalar f64 reference within 2e-5 (tolerance of original).
    let expected = [
        [-88.88888888888893, -100.00000000000004],
        [480.0, 270.0],
        [945.4545454545453, 572.7272727272727],
        [808.8888888888889, -100.00000000000004],
    ];
    let mut want_iter = expected.iter();
    for got in actual.iter() {
        let Some(g) = got else { continue };
        let want = want_iter.next().expect("expected entry for visible point");
        assert!(
            (g[0] - want[0]).abs() < 2e-5 && (g[1] - want[1]).abs() < 2e-5,
            "got {g:?} want {want:?}"
        );
    }
}

#[test]
fn orthographic_ndc_boundaries_map_to_region() {
    // #16 "orthographic NDC boundaries map exactly to region boundaries"
    let points = [-1.0, -1.0, 0.0, 1.0, 1.0, 0.0];
    let (code, actual) = f32_project(&IDENTITY, &points, 800.0, 600.0);
    assert_eq!(code, 0, "code {code}");
    assert_eq!(
        actual,
        vec![Some([0.0, 0.0]), Some([800.0, 600.0])],
        "actual {actual:?}"
    );
}

#[test]
fn nonfinite_points_unprojectable() {
    // #17 "non-finite points are explicitly unprojectable"
    let points = [f64::NAN, 0.0, 0.0];
    let (code, actual) = f32_project(&IDENTITY, &points, 800.0, 600.0);
    assert_eq!(code, 0, "code {code}");
    assert_eq!(actual, vec![None], "actual {actual:?}");
}

#[test]
fn display_export_adversarial_mixed_precision() {
    // #18 "f64 display export matches adversarial mathutils mixed-precision projection"
    let adversarial_matrix: [f64; 16] = [
        1.027998685836792,
        -0.42524489760398865,
        -0.466378778219223,
        0.6437168717384338,
        0.3514747619628906,
        1.8906302452087402,
        -0.9491537809371948,
        9.998909950256348,
        -0.4969795048236847,
        -0.3138795793056488,
        -0.8092533946037292,
        6.88346529006958,
        -0.49688011407852173,
        -0.3138168156147003,
        -0.8090915679931641,
        7.08206844329834,
    ];
    let points = [
        6.764359507293101,
        -9.856076443526845,
        0.48392038233675194,
        f64::NAN,
        0.0,
        0.0,
    ];
    let (code, actual) = display_project(&adversarial_matrix, &points, 1920.0, 1080.0);
    assert_eq!(code, 0, "code {code}");
    // Point 1: adversarial mixed-precision result (bit-exact Python mathutils emulation).
    assert_eq!(
        actual[0],
        Some([2688.388427734375, -24.773237228393555]),
        "actual {actual:?}"
    );
    // Point 2: non-finite input -> unprojectable.
    assert_eq!(actual[1], None, "actual {actual:?}");
}

// ---------------------------------------------------------------------------
// Screen predicate (narwhal_any_screen_segment_crosses_rect_f64)
// ---------------------------------------------------------------------------
#[test]
fn screen_predicate_crossing_stroke() {
    // #19 "screen predicate accepts a crossing stroke"
    let rect = [4.0, 4.0, 6.0, 6.0];
    assert_eq!(screen_any(&[0.0, 5.0, 10.0, 5.0], 1, &rect), 1);
}

#[test]
fn screen_predicate_zero_length_boundary_point() {
    // #20 "screen predicate includes a zero-length point on the boundary"
    let rect = [4.0, 4.0, 6.0, 6.0];
    assert_eq!(screen_any(&[4.0, 4.0, 4.0, 4.0], 1, &rect), 1);
}

#[test]
fn screen_predicate_closed_stroke_empty_interior() {
    // #21 "rectangle inside a closed stroke's empty interior remains a miss"
    let rect = [4.0, 4.0, 6.0, 6.0];
    let segments = [
        0.0, 0.0, 10.0, 0.0, 10.0, 0.0, 10.0, 10.0, 10.0, 10.0, 0.0, 10.0, 0.0, 10.0, 0.0, 0.0,
    ];
    assert_eq!(screen_any(&segments, 4, &rect), 0);
}

// ---------------------------------------------------------------------------
// Classified query (narwhal_projected_segments_rect_query_f32)
// ---------------------------------------------------------------------------
#[test]
fn classified_query_exact_boundary_endpoint() {
    // #25 "classified query includes an exact projected endpoint on the rectangle boundary"
    let boundary_segment = [-1.0, 0.0, 0.0, 1.0, 0.0, 0.0];
    let boundary_rect = [800.0, 300.0, 810.0, 310.0];
    let (code, evidence) = projected_query(
        &IDENTITY,
        &boundary_segment,
        1,
        800.0,
        600.0,
        &boundary_rect,
    );
    assert_eq!(
        (code, evidence),
        (1, 1),
        "(code {code}, evidence {evidence})"
    );
}

#[test]
fn classified_query_unavailable_evidence() {
    // #26 "classified query distinguishes unavailable screen evidence"
    let no_evidence: [f32; 6] = [0.0, 0.0, f32::NAN, 1.0, 1.0, 0.0];
    let rect = [4.0, 4.0, 6.0, 6.0];
    let (code, evidence) = projected_query(&IDENTITY, &no_evidence, 1, 800.0, 600.0, &rect);
    assert_eq!(
        (code, evidence),
        (0, 0),
        "(code {code}, evidence {evidence})"
    );
}

#[test]
fn classified_query_authoritative_miss() {
    // #27 "classified query distinguishes an authoritative miss"
    let projectable_miss: [f32; 6] = [-1.0, -1.0, 0.0, -0.9, -0.9, 0.0];
    let (code, evidence) = projected_query(
        &IDENTITY,
        &projectable_miss,
        1,
        800.0,
        600.0,
        &[700.0, 500.0, 710.0, 510.0],
    );
    assert_eq!(
        (code, evidence),
        (0, 1),
        "(code {code}, evidence {evidence})"
    );
}

// ---------------------------------------------------------------------------
// Batch query (narwhal_projected_curves_rect_query_f32)
// ---------------------------------------------------------------------------
#[test]
fn batch_query_statuses_hit_miss_no_evidence() {
    // #28 "batch query returns exact hit, miss, and no-evidence statuses in order"
    // curves: [boundary_segment, projectable_miss, no_evidence, []]
    let batch_segments: [f32; 18] = [
        -1.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        -1.0,
        -1.0,
        0.0,
        -0.9,
        -0.9,
        0.0,
        0.0,
        0.0,
        f32::NAN,
        1.0,
        1.0,
        0.0,
    ];
    let offsets = [0u64, 1, 2, 3, 3];
    let batch_rect = [390.0, 290.0, 410.0, 310.0];
    let (code, statuses) = projected_batch(
        &IDENTITY,
        &batch_segments,
        3,
        &offsets,
        4,
        800.0,
        600.0,
        &batch_rect,
    );
    assert_eq!(code, 0, "code {code}");
    assert_eq!(statuses, vec![1, 0, 2, 2], "statuses {statuses:?}");
}

// ---------------------------------------------------------------------------
// Nearest batch (narwhal_nearest_projected_curve_f32)
// ---------------------------------------------------------------------------
#[test]
fn nearest_batch_later_candidate_tie() {
    // #30 "nearest batch preserves the later-candidate exact tie rule"
    let boundary_segment = [-1.0, 0.0, 0.0, 1.0, 0.0, 0.0, -1.0, 0.0, 0.0, 1.0, 0.0, 0.0];
    let offsets = [0u64, 1, 2];
    let (code, index, distance) = nearest_batch(
        &IDENTITY,
        &boundary_segment,
        2,
        &offsets,
        2,
        800.0,
        600.0,
        [400.0, 300.0],
        1.0,
    );
    assert_eq!(
        (code, index, distance),
        (1, 1, 0.0),
        "(code {code}, index {index}, distance {distance})"
    );
}

#[test]
fn nearest_batch_authoritative_no_hit() {
    // #31 "nearest batch distinguishes an authoritative no-hit"
    let projectable_miss = [-1.0, -1.0, 0.0, -0.9, -0.9, 0.0];
    let offsets = [0u64, 1];
    let (code, index, distance) = nearest_batch(
        &IDENTITY,
        &projectable_miss,
        1,
        &offsets,
        1,
        800.0,
        600.0,
        [400.0, 300.0],
        1.0,
    );
    assert_eq!(code, 0, "code {code}");
    assert_eq!(index, U64_MAX, "index {index}");
    assert!(distance.is_infinite(), "distance {distance}");
}

// ---------------------------------------------------------------------------
// Clip-box reduction (narwhal_screen_boxes_from_clip_f64)
// ---------------------------------------------------------------------------
#[test]
fn clip_box_reduction_grouping_hidden_first_pixel() {
    // #39 "clip-box reduction preserves grouping, hidden rows, and first point pixels"
    let box_clip: [f64; 24] = [
        -1.0,
        -1.0,
        0.0,
        1.0,
        1.0,
        1.0,
        0.0,
        1.0,
        99.0,
        99.0,
        0.0,
        -1.0,
        -0.5,
        0.25,
        0.0,
        1.0,
        0.75,
        -0.75,
        0.0,
        1.0,
        f64::NAN,
        0.0,
        0.0,
        1.0,
    ];
    let box_offsets = [0u64, 3, 5, 6];
    let box_flags = [0u8, 1, 0];
    let (code, actual) = clip_boxes(&box_clip, &box_offsets, &box_flags, 800.0, 600.0);
    assert_eq!(code, 0, "code {code}");
    assert_eq!(
        actual,
        vec![
            Some([0.0, 0.0, 800.0, 600.0]),
            Some([200.0, 375.0, 200.0, 375.0]),
            None,
        ],
        "actual {actual:?}"
    );
}

// ---------------------------------------------------------------------------
// Fused world-box (narwhal_screen_boxes_from_world_f64)
// ---------------------------------------------------------------------------
#[test]
fn fused_world_box_fixed_order_projection() {
    // #41 "fused world-box query matches fixed-order f64 projection and reduction"
    let world_matrix = PERSPECTIVE;
    let world_points = [
        -1.0, -1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0, -10.0, 0.25, -0.5, 0.75,
    ];
    let world_offsets = [0u64, 3, 4];
    let world_flags = [0u8, 1];
    let (code, actual) = world_boxes(
        &world_matrix,
        &world_points,
        &world_offsets,
        &world_flags,
        800.0,
        600.0,
    );
    assert_eq!(code, 0, "code {code}");
    assert_eq!(
        actual,
        vec![
            Some([
                -88.88888888888893,
                -100.00000000000004,
                945.4545454545453,
                572.7272727272727
            ]),
            Some([
                548.4536082474227,
                157.73195876288656,
                548.4536082474227,
                157.73195876288656
            ]),
        ],
        "actual {actual:?}"
    );
}

// ---------------------------------------------------------------------------
// World rect hit mask (narwhal_world_rect_hit_mask_f64)
// ---------------------------------------------------------------------------
#[test]
fn world_rect_mask_full_containment() {
    // #42 "compact window mask requires full containment and honors point flags"
    let mask_points = [-0.5, -0.5, 0.0, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
    let mask_offsets = [0u64, 2, 4];
    let mask_flags = [0u8, 1];
    let (code, hits) = world_rect_mask(
        &IDENTITY,
        &mask_points,
        &mask_offsets,
        &mask_flags,
        800.0,
        600.0,
        &[300.0, 200.0, 500.0, 400.0],
        0,
    );
    assert_eq!(code, 0, "code {code}");
    assert_eq!(hits, vec![0, 1], "hits {hits:?}");
}

#[test]
fn world_rect_mask_crossing_exact_edge() {
    // #43 "compact crossing mask includes an exact rectangle edge"
    let mask_points = [-0.5, -0.5, 0.0, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
    let mask_offsets = [0u64, 2, 4];
    let mask_flags = [0u8, 1];
    let (code, hits) = world_rect_mask(
        &IDENTITY,
        &mask_points,
        &mask_offsets,
        &mask_flags,
        800.0,
        600.0,
        &[600.0, 450.0, 700.0, 500.0],
        1,
    );
    assert_eq!(code, 0, "code {code}");
    assert_eq!(hits, vec![1, 0], "hits {hits:?}");
}

// ---------------------------------------------------------------------------
// AABB rects (narwhal_screen_rects_from_aabbs_f64)
// ---------------------------------------------------------------------------
#[test]
fn aabb_bounded_all_behind_eye_straddling() {
    // #45 "AABB rectangles preserve bounded, all-behind, and eye-straddling states"
    let aabb_matrix = [
        1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0,
    ];
    let aabb_lower = [-1.0, -1.0, 1.0, -1.0, -1.0, -2.0, -1.0, -1.0, -1.0];
    let aabb_upper = [1.0, 1.0, 1.0, 1.0, 1.0, -1.0, 1.0, 1.0, 1.0];
    let (code, rects, unbounded, behind) =
        aabb_rects(&aabb_matrix, &aabb_lower, &aabb_upper, 800.0, 600.0);
    let (rects_c, unb_c, beh_c) = (rects.clone(), unbounded.clone(), behind.clone());
    assert_eq!(code, 0, "code {code}");
    assert_eq!(
        (rects, unbounded, behind),
        (
            vec![
                [0.0, 0.0, 800.0, 600.0],
                [
                    f64::INFINITY,
                    f64::INFINITY,
                    f64::NEG_INFINITY,
                    f64::NEG_INFINITY
                ],
                [0.0, 0.0, 800.0, 600.0],
            ],
            vec![0u8, 1, 1],
            vec![0u8, 1, 0],
        ),
        "rects {rects_c:?} unbounded {unb_c:?} behind {beh_c:?}"
    );
}

#[test]
fn aabb_near_eye_cancellation_unbounded() {
    // #46 "relative cancellation near w=0 remains unbounded rather than clearly behind"
    let near_eye_matrix = [
        1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0,
    ];
    let lower = [-1.000001, -1.0, -1.0];
    let upper = [-1.000001, 1.0, 1.0];
    let (code, _rects, unbounded, behind) =
        aabb_rects(&near_eye_matrix, &lower, &upper, 800.0, 600.0);
    assert_eq!(code, 0, "code {code}");
    assert_eq!(unbounded, vec![1u8], "unbounded {unbounded:?}");
    assert_eq!(behind, vec![0u8], "behind {behind:?}");
}

#[test]
fn aabb_positive_infinite_w_front_facing() {
    // #47 "positive-infinite w remains front-facing exactly like the NumPy predicate"
    let overflow_w_matrix = [
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0e308, 0.0, 0.0, 0.0,
    ];
    let lower = [2.0, 0.0, 0.0];
    let upper = [2.0, 0.0, 0.0];
    let (code, rects, unbounded, behind) =
        aabb_rects(&overflow_w_matrix, &lower, &upper, 800.0, 600.0);
    assert_eq!(code, 0, "code {code}");
    assert_eq!(rects, vec![[400.0, 300.0, 400.0, 300.0]], "rects {rects:?}");
    assert_eq!(unbounded, vec![0u8], "unbounded {unbounded:?}");
    assert_eq!(behind, vec![0u8], "behind {behind:?}");
}

// ---------------------------------------------------------------------------
// Projected Near (narwhal_nearest_projected_polylines_f64)
// ---------------------------------------------------------------------------
#[test]
fn projected_near_segment_parameter_degenerates() {
    // #49 "projected Near batches preserve segment parameter, degenerates, and empty curves"
    let near_polylines = [
        -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.25, 0.5, 0.0, 0.25, 0.5, 0.0, 0.0, 0.0, 0.0,
    ];
    let offsets = [0u64, 3, 5, 6];
    let (code, actual) = projected_near(
        &IDENTITY,
        &near_polylines,
        &offsets,
        3,
        800.0,
        600.0,
        [400.0, 300.0],
    );
    assert_eq!(code, 0, "code {code}");
    assert_eq!(
        actual,
        vec![
            Some((0, 1.0, 0.0)),
            Some((0, 0.0, 180.27756377319946)),
            None,
        ],
        "actual {actual:?}"
    );
}

// ---------------------------------------------------------------------------
// Nearest point (narwhal_nearest_projected_point_f64)
// ---------------------------------------------------------------------------
#[test]
fn nearest_point_later_candidate_tie() {
    // #52 "nearest point query preserves the later-candidate exact tie rule"
    let point_batch = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
    let (code, index, distance) =
        nearest_point(&IDENTITY, &point_batch, 800.0, 600.0, [400.0, 300.0], 1.0);
    assert_eq!(
        (code, index, distance),
        (1, 1, 0.0),
        "(code {code}, index {index}, distance {distance})"
    );
}

// ---------------------------------------------------------------------------
// Point rectangle query (narwhal_projected_points_rect_query_f64)
// ---------------------------------------------------------------------------
#[test]
fn point_rect_includes_exact_edges() {
    // #53 "point rectangle query includes points exactly on every rectangle edge"
    let point_batch = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
    let (code, inside) = points_rect(
        &IDENTITY,
        &point_batch,
        800.0,
        600.0,
        &[400.0, 300.0, 800.0, 600.0],
    );
    assert_eq!(code, 0, "code {code}");
    assert_eq!(inside, vec![1, 1, 1], "inside {inside:?}");
}

// ---------------------------------------------------------------------------
// Snap distances (narwhal_projected_snap_distances_f64)
// ---------------------------------------------------------------------------
#[test]
fn snap_distance_exact_radius_boundary() {
    // #55 "snap-distance compact output includes the exact radius boundary"
    let (code, indices, distances) = snap_distances(
        &IDENTITY,
        &[0.0, 0.0, 0.0],
        800.0,
        600.0,
        [400.0, 300.0],
        0.0,
    );
    assert_eq!(code, 0, "code {code}");
    assert_eq!(indices, vec![0u64], "indices {indices:?}");
    assert_eq!(distances, vec![0.0], "distances {distances:?}");
}

// ===========================================================================
// Chunk 1: screen-polyline adversarial + invalid/null + batch + clip-box
// ===========================================================================
#[test]
fn screen_polyline_earlier_exact_ties() {
    // #32 "screen polyline query preserves earlier exact ties"
    let pixels = [0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 10.0, 0.0];
    let visible = [1u8, 1, 1, 1];
    let offsets = [0u64, 2, 4];
    let (code, index, distance) = screen_polyline(&pixels, &visible, &offsets, [5.0, 1.0], 9.0);
    assert_eq!(
        (code, index, distance),
        (1, 0, 1.0),
        "got (code {code}, index {index}, distance {distance})"
    );
}

#[test]
fn screen_polyline_broad_rejection_outside_endpoint_aabbs() {
    // #33 "hover broad rejection preserves floating interpolation outside endpoint AABBs"
    let pixels = [-1.0, 0.5, 1.0, 0.5, 1.0e16, 0.0, 1.0, 0.0];
    let visible = [1u8, 1, 1, 1];
    let offsets = [0u64, 2, 4];
    let (code, index, distance) = screen_polyline(&pixels, &visible, &offsets, [0.0, 0.0], 0.75);
    assert_eq!(
        (code, index, distance),
        (1, 1, 0.0),
        "got (code {code}, index {index}, distance {distance})"
    );
}

#[test]
fn screen_polyline_strict_less_than_tolerance() {
    // #34 "screen polyline query preserves strict distance < tolerance"
    let pixels = [0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 10.0, 0.0];
    let visible = [1u8, 1, 1, 1];
    let offsets = [0u64, 2, 4];
    let (code, index, distance) = screen_polyline(&pixels, &visible, &offsets, [5.0, 9.0], 9.0);
    assert_eq!(code, 0, "code {code}");
    assert_eq!(index, U64_MAX, "index {index}");
    assert!(distance.is_infinite(), "distance {distance}");
}

#[test]
fn screen_polyline_drops_hidden_endpoint_only() {
    // #35 "screen polyline query drops only segments with a hidden endpoint"
    let pixels = [0.0, 0.0, f64::NAN, f64::NAN, 0.0, 0.0, 10.0, 0.0];
    let visible = [1u8, 0, 1, 1];
    let offsets = [0u64, 2, 4];
    let (code, index, distance) = screen_polyline(&pixels, &visible, &offsets, [5.0, 1.0], 9.0);
    assert_eq!(
        (code, index, distance),
        (1, 1, 1.0),
        "got (code {code}, index {index}, distance {distance})"
    );
}

#[test]
fn screen_polyline_bit_exact_adversarial_hypot() {
    // #36 "screen polyline query is bit-exact with adversarial Python math.hypot"
    let mouse = [4.364241123199463, 4.749826908111572];
    let d = 6.450384193088506;
    let pixels = [0.0, 0.0, 0.0, 0.0];
    let visible = [1u8, 1];
    let offsets = [0u64, 2];
    let (miss_code, miss_index, miss_distance) =
        screen_polyline(&pixels, &visible, &offsets, mouse, d);
    let (hit_code, hit_index, hit_distance) =
        screen_polyline(&pixels, &visible, &offsets, mouse, 6.450384193088507);
    assert_eq!(miss_code, 0, "miss code {miss_code}");
    assert_eq!(miss_index, U64_MAX, "miss index {miss_index}");
    assert!(miss_distance.is_infinite(), "miss distance {miss_distance}");
    assert_eq!(
        (hit_code, hit_index, hit_distance),
        (1, 0, d),
        "got (hit_code {hit_code}, hit_index {hit_index}, hit_distance {hit_distance})"
    );
}

#[test]
fn screen_polyline_exact_1e9_degenerate_threshold() {
    // #37 "screen polyline query preserves the exact 1e-9 degenerate threshold"
    let tdx = 3.1622776601683795e-05;
    let below_dx = 3.162277660168379e-05;
    let mouse = [6.324555320336759e-05, 1.0];
    let limit = 1.00000000125;
    let at_pixels = [0.0, 0.0, tdx, 0.0];
    let below_pixels = [0.0, 0.0, below_dx, 0.0];
    let visible = [1u8, 1];
    let offsets = [0u64, 2];
    assert_eq!(tdx * tdx, 1.0e-9, "tdx*tdx = {}", tdx * tdx);
    let (at_code, at_index, at_distance) =
        screen_polyline(&at_pixels, &visible, &offsets, mouse, limit);
    let (below_code, below_index, _) =
        screen_polyline(&below_pixels, &visible, &offsets, mouse, limit);
    assert_eq!(
        (at_code, at_index, at_distance),
        (1, 0, 1.0000000005),
        "got (at_code {at_code}, at_index {at_index}, at_distance {at_distance})"
    );
    assert_eq!(below_code, 0, "below code {below_code}");
    assert_eq!(below_index, U64_MAX, "below index {below_index}");
}

#[test]
fn screen_polyline_nan_parameter_matches_python() {
    // #38 "screen polyline query matches Python max/min when the parameter is NaN"
    let extreme = 1.4e154;
    let pixels = [0.0, 0.0, extreme, extreme];
    let visible = [1u8, 1];
    let offsets = [0u64, 2];
    let (code, index, distance) =
        screen_polyline(&pixels, &visible, &offsets, [extreme, extreme], 9.0);
    assert_eq!(
        (code, index, distance),
        (1, 0, 0.0),
        "got (code {code}, index {index}, distance {distance})"
    );
}

#[test]
fn project_points_f32_invalid_matrix_and_null() {
    // #59 "invalid matrices fail closed with an explicit input error"
    // #60 "null matrix pointers fail closed"
    let mut bad = IDENTITY;
    bad[0] = f64::NAN;
    let point: [f32; 3] = [0.0, 0.0, 0.0];
    let mut out = [0.0f64; 2];
    let mut visible = [0u8; 1];
    let code59 = unsafe {
        narwhal_selection_core::narwhal_project_points_f32(
            bad.as_ptr(),
            point.as_ptr(),
            1,
            800.0,
            600.0,
            out.as_mut_ptr(),
            visible.as_mut_ptr(),
        )
    };
    assert_eq!(code59, ERR_INPUT, "code {code59}");
    let code60 = unsafe {
        narwhal_selection_core::narwhal_project_points_f32(
            std::ptr::null(),
            point.as_ptr(),
            1,
            800.0,
            600.0,
            out.as_mut_ptr(),
            visible.as_mut_ptr(),
        )
    };
    assert_eq!(code60, ERR_NULL, "code {code60}");
}

#[test]
fn empty_batches_clean_miss() {
    // #61 "empty screen-segment batches are a clean miss"
    // #62 "empty world-segment batches are a clean miss"
    // #63 "empty classified batches report no projectable evidence"
    let rect = [4.0, 4.0, 6.0, 6.0];
    assert_eq!(screen_any(&[], 0, &rect), 0, "screen_any");
    let mut evidence = 7u8;
    let code62 = unsafe {
        narwhal_selection_core::narwhal_any_projected_segment_crosses_rect_f32(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            0,
            800.0,
            600.0,
            rect.as_ptr(),
        )
    };
    assert_eq!(code62, 0, "code {code62}");
    let code63 = unsafe {
        narwhal_selection_core::narwhal_projected_segments_rect_query_f32(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            0,
            800.0,
            600.0,
            rect.as_ptr(),
            &mut evidence,
        )
    };
    assert_eq!(code63, 0, "code {code63}");
    assert_eq!(evidence, 0, "evidence {evidence}");
}

#[test]
fn classified_query_null_evidence_and_invalid_inputs() {
    // #64 "classified query rejects a null evidence pointer"
    // #65 (7 sub-cases) "classified query rejects <label> and clears evidence"
    let rect = [4.0, 4.0, 6.0, 6.0];
    let query: [f32; 6] = [0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
    let mut bad = IDENTITY;
    bad[0] = f64::NAN;
    let code64 = unsafe {
        narwhal_selection_core::narwhal_projected_segments_rect_query_f32(
            IDENTITY.as_ptr(),
            query.as_ptr(),
            1,
            800.0,
            600.0,
            rect.as_ptr(),
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code64, ERR_NULL, "code {code64}");
    let cases: [(*const f64, *const f32, f64, *const f64, i32); 7] = [
        (
            std::ptr::null(),
            query.as_ptr(),
            800.0,
            rect.as_ptr(),
            ERR_NULL,
        ),
        (
            IDENTITY.as_ptr(),
            std::ptr::null(),
            800.0,
            rect.as_ptr(),
            ERR_NULL,
        ),
        (
            IDENTITY.as_ptr(),
            query.as_ptr(),
            800.0,
            std::ptr::null(),
            ERR_NULL,
        ),
        (
            bad.as_ptr(),
            query.as_ptr(),
            800.0,
            rect.as_ptr(),
            ERR_INPUT,
        ),
        (
            IDENTITY.as_ptr(),
            query.as_ptr(),
            -1.0,
            rect.as_ptr(),
            ERR_INPUT,
        ),
        (
            IDENTITY.as_ptr(),
            query.as_ptr(),
            800.0,
            [6.0, 4.0, 4.0, 6.0].as_ptr(),
            ERR_INPUT,
        ),
        (
            IDENTITY.as_ptr(),
            query.as_ptr(),
            800.0,
            [4.0, 4.0, f64::NAN, 6.0].as_ptr(),
            ERR_INPUT,
        ),
    ];
    for (m, s, w, r, expected) in cases {
        let mut evidence = 7u8;
        let code = unsafe {
            narwhal_selection_core::narwhal_projected_segments_rect_query_f32(
                m,
                s,
                1,
                w,
                600.0,
                r,
                &mut evidence,
            )
        };
        assert_eq!(code, expected, "code {code} expected {expected}");
        assert_eq!(evidence, 0, "evidence {evidence}");
    }
}

#[test]
fn batch_query_invalid_offsets_clears_statuses() {
    // #66 "batch query rejects non-monotonic offsets before publishing partial results"
    let segments: [f32; 12] = [
        -1.0, 0.0, 0.0, 1.0, 0.0, 0.0, -1.0, -1.0, 0.0, -0.9, -0.9, 0.0,
    ];
    let invalid = [0u64, 2, 1];
    let mut statuses = [9u8, 9];
    let rect = [4.0, 4.0, 6.0, 6.0];
    let code = unsafe {
        narwhal_selection_core::narwhal_projected_curves_rect_query_f32(
            IDENTITY.as_ptr(),
            segments.as_ptr(),
            2,
            invalid.as_ptr(),
            2,
            800.0,
            600.0,
            rect.as_ptr(),
            statuses.as_mut_ptr(),
        )
    };
    assert_eq!(code, ERR_INPUT, "code {code}");
    assert_eq!(statuses, [2u8, 2], "statuses {statuses:?}");
}

#[test]
fn batch_query_null_status_and_segments() {
    // #67 "batch query rejects a null status output"
    // #68 "batch query rejects null non-empty segments"
    let segments: [f32; 12] = [
        -1.0, 0.0, 0.0, 1.0, 0.0, 0.0, -1.0, -1.0, 0.0, -0.9, -0.9, 0.0,
    ];
    let valid = [0u64, 1, 2];
    let rect = [4.0, 4.0, 6.0, 6.0];
    let code67 = unsafe {
        narwhal_selection_core::narwhal_projected_curves_rect_query_f32(
            IDENTITY.as_ptr(),
            segments.as_ptr(),
            2,
            valid.as_ptr(),
            2,
            800.0,
            600.0,
            rect.as_ptr(),
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code67, ERR_NULL, "code {code67}");
    let mut statuses = [9u8, 9];
    let code68 = unsafe {
        narwhal_selection_core::narwhal_projected_curves_rect_query_f32(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            2,
            valid.as_ptr(),
            2,
            800.0,
            600.0,
            rect.as_ptr(),
            statuses.as_mut_ptr(),
        )
    };
    assert_eq!(code68, ERR_NULL, "code {code68}");
}

#[test]
fn batch_query_null_segments_empty_curves_no_ub() {
    // #69 "batch query accepts a null segment pointer for all-empty curves without UB"
    let empty = [0u64, 0, 0];
    let mut statuses = [9u8, 9];
    let rect = [4.0, 4.0, 6.0, 6.0];
    let code = unsafe {
        narwhal_selection_core::narwhal_projected_curves_rect_query_f32(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            0,
            empty.as_ptr(),
            2,
            800.0,
            600.0,
            rect.as_ptr(),
            statuses.as_mut_ptr(),
        )
    };
    assert_eq!(code, 0, "code {code}");
    assert_eq!(statuses, [2u8, 2], "statuses {statuses:?}");
}

#[test]
fn nearest_batch_null_inputs_and_invalid_offsets() {
    // #70 "nearest batch safely accepts multiple empty curves"
    // #71 "nearest batch rejects non-monotonic offsets"
    // #72 "nearest batch rejects a null index output"
    let empty = [0u64, 0, 0];
    let segments: [f32; 12] = [
        -1.0, 0.0, 0.0, 1.0, 0.0, 0.0, -1.0, -1.0, 0.0, -0.9, -0.9, 0.0,
    ];
    let valid = [0u64, 1, 2];
    let invalid = [0u64, 2, 1];
    let mut index = 7u64;
    let mut distance = 7.0f64;
    let code70 = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_curve_f32(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            0,
            empty.as_ptr(),
            2,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            6.0,
            &mut index,
            &mut distance,
        )
    };
    assert_eq!(code70, 0, "code {code70}");
    assert_eq!(index, U64_MAX, "index {index}");
    assert!(distance.is_infinite(), "distance {distance}");
    let code71 = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_curve_f32(
            IDENTITY.as_ptr(),
            segments.as_ptr(),
            2,
            invalid.as_ptr(),
            2,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            6.0,
            &mut index,
            &mut distance,
        )
    };
    assert_eq!(code71, ERR_INPUT, "code {code71}");
    let code72 = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_curve_f32(
            IDENTITY.as_ptr(),
            segments.as_ptr(),
            2,
            valid.as_ptr(),
            2,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            6.0,
            std::ptr::null_mut(),
            &mut distance,
        )
    };
    assert_eq!(code72, ERR_NULL, "code {code72}");
}

#[test]
fn clip_box_null_clip_clears_outputs() {
    // #73 "clip-box reduction clears outputs before rejecting null nonempty clip input"
    let offsets = [0u64, 1, 2];
    let flags = [0u8, 1];
    let mut boxes = [7.0f64; 8];
    let mut valid = [7u8, 7];
    let code = unsafe {
        narwhal_selection_core::narwhal_screen_boxes_from_clip_f64(
            std::ptr::null(),
            2,
            offsets.as_ptr(),
            2,
            flags.as_ptr(),
            800.0,
            600.0,
            boxes.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    assert_eq!(code, ERR_NULL, "code {code}");
    assert_eq!(valid, [0u8, 0], "valid {valid:?}");
    assert!(boxes.iter().all(|v| v.is_nan()), "boxes {boxes:?}");
}

#[test]
fn clip_box_invalid_offsets_flags_and_oversized() {
    // #74 "clip-box reduction rejects non-monotonic offsets and clears outputs"
    // #75 "clip-box reduction rejects undocumented point flags"
    // #76 "clip-box reduction rejects a slice whose byte span exceeds isize::MAX"
    // #77 "clip-box reduction safely accepts an empty null batch"
    let clip: [f64; 8] = [-1.0, -1.0, 0.0, 1.0, 1.0, 1.0, 0.0, 1.0];
    let offsets = [0u64, 1, 2];
    let flags = [0u8, 1];
    let mut boxes = [7.0f64; 8];
    let mut valid = [7u8, 7];
    let code74 = unsafe {
        narwhal_selection_core::narwhal_screen_boxes_from_clip_f64(
            clip.as_ptr(),
            2,
            [0u64, 2, 1].as_ptr(),
            2,
            flags.as_ptr(),
            800.0,
            600.0,
            boxes.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    assert_eq!(code74, ERR_INPUT, "code {code74}");
    assert_eq!(valid, [0u8, 0], "valid {valid:?}");
    assert!(boxes.iter().all(|v| v.is_nan()), "boxes {boxes:?}");
    let code75 = unsafe {
        narwhal_selection_core::narwhal_screen_boxes_from_clip_f64(
            clip.as_ptr(),
            2,
            offsets.as_ptr(),
            2,
            [0u8, 2].as_ptr(),
            800.0,
            600.0,
            boxes.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    assert_eq!(code75, ERR_INPUT, "code {code75}");
    let code76 = unsafe {
        narwhal_selection_core::narwhal_screen_boxes_from_clip_f64(
            clip.as_ptr(),
            oversized_count(4),
            [0u64, 1].as_ptr(),
            1,
            [0u8].as_ptr(),
            800.0,
            600.0,
            boxes.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    assert_eq!(code76, ERR_SIZE, "code {code76}");
    let code77 = unsafe {
        narwhal_selection_core::narwhal_screen_boxes_from_clip_f64(
            std::ptr::null(),
            0,
            std::ptr::null(),
            0,
            std::ptr::null(),
            800.0,
            600.0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code77, 0, "code {code77}");
}

#[test]
fn world_box_null_matrix_and_nonfinite() {
    // #78 "fused world-box query clears outputs before rejecting a null matrix"
    // #79 "fused world-box query rejects nonfinite points and clears outputs"
    let offsets = [0u64, 1, 2];
    let flags = [0u8, 1];
    let mut boxes = [7.0f64; 8];
    let mut valid = [7u8, 7];
    let points: [f64; 6] = [-1.0, -1.0, 0.0, 1.0, 1.0, 0.0];
    let code78 = unsafe {
        narwhal_selection_core::narwhal_screen_boxes_from_world_f64(
            std::ptr::null(),
            points.as_ptr(),
            2,
            offsets.as_ptr(),
            2,
            flags.as_ptr(),
            800.0,
            600.0,
            boxes.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    assert_eq!(code78, ERR_NULL, "code {code78}");
    assert_eq!(valid, [0u8, 0], "valid {valid:?}");
    assert!(boxes.iter().all(|v| v.is_nan()), "boxes {boxes:?}");
    let mut boxes2 = [7.0f64; 8];
    let mut valid2 = [7u8, 7];
    let bad: [f64; 6] = [f64::NAN, 0.0, 0.0, 1.0, 1.0, 0.0];
    let code79 = unsafe {
        narwhal_selection_core::narwhal_screen_boxes_from_world_f64(
            IDENTITY.as_ptr(),
            bad.as_ptr(),
            2,
            offsets.as_ptr(),
            2,
            flags.as_ptr(),
            800.0,
            600.0,
            boxes2.as_mut_ptr(),
            valid2.as_mut_ptr(),
        )
    };
    assert_eq!(code79, ERR_INPUT, "code {code79}");
    assert_eq!(valid2, [0u8, 0], "valid {valid2:?}");
    assert!(boxes2.iter().all(|v| v.is_nan()), "boxes {boxes2:?}");
}

#[test]
fn world_box_oversized_and_empty_null() {
    // #80 "fused world-box query rejects a slice whose byte span exceeds isize::MAX"
    // #81 "fused world-box query safely accepts an empty null batch"
    let mut boxes = [7.0f64; 8];
    let mut valid = [7u8, 7];
    let one: [f64; 3] = [0.0, 0.0, 0.0];
    let code80 = unsafe {
        narwhal_selection_core::narwhal_screen_boxes_from_world_f64(
            IDENTITY.as_ptr(),
            one.as_ptr(),
            oversized_count(3),
            [0u64, 1].as_ptr(),
            1,
            [0u8].as_ptr(),
            800.0,
            600.0,
            boxes.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    assert_eq!(code80, ERR_SIZE, "code {code80}");
    let code81 = unsafe {
        narwhal_selection_core::narwhal_screen_boxes_from_world_f64(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            0,
            std::ptr::null(),
            0,
            std::ptr::null(),
            800.0,
            600.0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code81, 0, "code {code81}");
}

#[test]
fn aabb_null_bounds_and_invalid() {
    // #82 "AABB projection clears outputs before rejecting null bounds"
    // #83 "AABB projection rejects nonfinite bounds"
    // #84 "AABB projection rejects inverted bounds"
    let mut rects = [0.0f64; 4];
    let mut unbounded = [1u8];
    let mut behind = [1u8];
    let upper: [f64; 3] = [1.0, 1.0, 1.0];
    let code82 = unsafe {
        narwhal_selection_core::narwhal_screen_rects_from_aabbs_f64(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            upper.as_ptr(),
            1,
            800.0,
            600.0,
            rects.as_mut_ptr(),
            unbounded.as_mut_ptr(),
            behind.as_mut_ptr(),
        )
    };
    assert_eq!(code82, ERR_NULL, "code {code82}");
    assert_eq!(unbounded, [0u8], "unbounded {unbounded:?}");
    assert_eq!(behind, [0u8], "behind {behind:?}");
    assert!(rects.iter().all(|v| v.is_nan()), "rects {rects:?}");
    let nan: [f64; 3] = [f64::NAN, 0.0, 0.0];
    let code83 = unsafe {
        narwhal_selection_core::narwhal_screen_rects_from_aabbs_f64(
            IDENTITY.as_ptr(),
            nan.as_ptr(),
            upper.as_ptr(),
            1,
            800.0,
            600.0,
            rects.as_mut_ptr(),
            unbounded.as_mut_ptr(),
            behind.as_mut_ptr(),
        )
    };
    assert_eq!(code83, ERR_INPUT, "code {code83}");
    let inv: [f64; 3] = [2.0, 0.0, 0.0];
    let code84 = unsafe {
        narwhal_selection_core::narwhal_screen_rects_from_aabbs_f64(
            IDENTITY.as_ptr(),
            inv.as_ptr(),
            upper.as_ptr(),
            1,
            800.0,
            600.0,
            rects.as_mut_ptr(),
            unbounded.as_mut_ptr(),
            behind.as_mut_ptr(),
        )
    };
    assert_eq!(code84, ERR_INPUT, "code {code84}");
}

#[test]
fn aabb_oversized_and_empty_null() {
    // #85 "AABB projection rejects a slice whose byte span exceeds isize::MAX"
    // #86 "AABB projection safely accepts an empty null batch"
    let one: [f64; 3] = [0.0, 0.0, 0.0];
    let upper: [f64; 3] = [1.0, 1.0, 1.0];
    let mut rects = [0.0f64; 4];
    let mut unbounded = [1u8];
    let mut behind = [1u8];
    let code85 = unsafe {
        narwhal_selection_core::narwhal_screen_rects_from_aabbs_f64(
            IDENTITY.as_ptr(),
            one.as_ptr(),
            upper.as_ptr(),
            oversized_count(3),
            800.0,
            600.0,
            rects.as_mut_ptr(),
            unbounded.as_mut_ptr(),
            behind.as_mut_ptr(),
        )
    };
    assert_eq!(code85, ERR_SIZE, "code {code85}");
    let code86 = unsafe {
        narwhal_selection_core::narwhal_screen_rects_from_aabbs_f64(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            0,
            800.0,
            600.0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code86, 0, "code {code86}");
}

#[test]
fn projected_near_null_matrix_clears_all_outputs() {
    // #87 "projected Near query clears every output before rejecting a null matrix"
    let points: [f64; 9] = [-1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 2.0, 0.0, 0.0];
    let offsets = [0u64, 2, 3];
    let mut segs = [0u64, 0];
    let mut params = [0.0f64, 0.0];
    let mut dists = [0.0f64, 0.0];
    let mut valid = [1u8, 1];
    let code = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_polylines_f64(
            std::ptr::null(),
            points.as_ptr(),
            3,
            offsets.as_ptr(),
            2,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            segs.as_mut_ptr(),
            params.as_mut_ptr(),
            dists.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    assert_eq!(code, ERR_NULL, "code {code}");
    assert_eq!(valid, [0u8, 0], "valid {valid:?}");
    assert!(segs.iter().all(|v| *v == U64_MAX), "segs {segs:?}");
    assert!(params.iter().all(|v| v.is_nan()), "params {params:?}");
    assert!(dists.iter().all(|v| v.is_infinite()), "dists {dists:?}");
}

#[test]
fn projected_near_invalid_points_offsets_and_oversized() {
    // #88 "projected Near query rejects nonfinite world points"
    // #89 "projected Near query rejects non-monotonic offsets"
    // #90 "projected Near query rejects a slice whose byte span exceeds isize::MAX"
    // #91 "projected Near query safely accepts an empty null batch"
    let bad: [f64; 6] = [f64::NAN, 0.0, 0.0, 1.0, 0.0, 0.0];
    let two: [f64; 6] = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0];
    let one: [f64; 3] = [0.0, 0.0, 0.0];
    let mut segs = [0u64, 0];
    let mut params = [0.0f64, 0.0];
    let mut dists = [0.0f64, 0.0];
    let mut valid = [1u8, 1];
    let code88 = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_polylines_f64(
            IDENTITY.as_ptr(),
            bad.as_ptr(),
            2,
            [0u64, 2].as_ptr(),
            1,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            segs.as_mut_ptr(),
            params.as_mut_ptr(),
            dists.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    assert_eq!(code88, ERR_INPUT, "code {code88}");
    let code89 = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_polylines_f64(
            IDENTITY.as_ptr(),
            two.as_ptr(),
            2,
            [0u64, 2, 1].as_ptr(),
            2,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            segs.as_mut_ptr(),
            params.as_mut_ptr(),
            dists.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    assert_eq!(code89, ERR_INPUT, "code {code89}");
    let code90 = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_polylines_f64(
            IDENTITY.as_ptr(),
            one.as_ptr(),
            oversized_count(3),
            [0u64, 1].as_ptr(),
            1,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            segs.as_mut_ptr(),
            params.as_mut_ptr(),
            dists.as_mut_ptr(),
            valid.as_mut_ptr(),
        )
    };
    assert_eq!(code90, ERR_SIZE, "code {code90}");
    let code91 = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_polylines_f64(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            0,
            std::ptr::null(),
            0,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code91, 0, "code {code91}");
}

#[test]
fn screen_polyline_null_inputs_clear_outputs() {
    // #92 (4 sub-cases) "screen polyline query rejects <label> and clears outputs"
    let pixels = [0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 10.0, 0.0];
    let visible = [1u8, 1, 1, 1];
    let offsets = [0u64, 2, 4];
    let mouse = [5.0, 1.0];
    let cases: [(*const f64, *const u8, *const u64, *const f64); 4] = [
        (
            pixels.as_ptr(),
            visible.as_ptr(),
            offsets.as_ptr(),
            std::ptr::null(),
        ),
        (
            std::ptr::null(),
            visible.as_ptr(),
            offsets.as_ptr(),
            mouse.as_ptr(),
        ),
        (
            pixels.as_ptr(),
            std::ptr::null(),
            offsets.as_ptr(),
            mouse.as_ptr(),
        ),
        (
            pixels.as_ptr(),
            visible.as_ptr(),
            std::ptr::null(),
            mouse.as_ptr(),
        ),
    ];
    for (p, v, o, m) in cases {
        let mut index = 7u64;
        let mut distance = 7.0f64;
        let code = unsafe {
            narwhal_selection_core::narwhal_nearest_screen_polyline_f64(
                p,
                v,
                4,
                o,
                2,
                m,
                9.0,
                &mut index,
                &mut distance,
            )
        };
        assert_eq!(code, ERR_NULL, "code {code}");
        assert_eq!(index, U64_MAX, "index {index}");
        assert!(distance.is_infinite(), "distance {distance}");
    }
}

#[test]
fn screen_polyline_null_outputs_and_empty_curves() {
    // #93 "screen polyline query rejects each null output pointer"
    // #94 "screen polyline query safely accepts multiple empty curves"
    let pixels = [0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 10.0, 0.0];
    let visible = [1u8, 1, 1, 1];
    let offsets = [0u64, 2, 4];
    let empty = [0u64, 0, 0];
    let mut index = 7u64;
    let mut distance = 7.0f64;
    let code93a = unsafe {
        narwhal_selection_core::narwhal_nearest_screen_polyline_f64(
            pixels.as_ptr(),
            visible.as_ptr(),
            4,
            offsets.as_ptr(),
            2,
            [5.0, 1.0].as_ptr(),
            9.0,
            std::ptr::null_mut(),
            &mut distance,
        )
    };
    assert_eq!(code93a, ERR_NULL, "code {code93a}");
    let code93b = unsafe {
        narwhal_selection_core::narwhal_nearest_screen_polyline_f64(
            pixels.as_ptr(),
            visible.as_ptr(),
            4,
            offsets.as_ptr(),
            2,
            [5.0, 1.0].as_ptr(),
            9.0,
            &mut index,
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code93b, ERR_NULL, "code {code93b}");
    let code94 = unsafe {
        narwhal_selection_core::narwhal_nearest_screen_polyline_f64(
            std::ptr::null(),
            std::ptr::null(),
            0,
            empty.as_ptr(),
            2,
            [5.0, 1.0].as_ptr(),
            9.0,
            &mut index,
            &mut distance,
        )
    };
    assert_eq!(code94, 0, "code {code94}");
    assert_eq!(index, U64_MAX, "index {index}");
    assert!(distance.is_infinite(), "distance {distance}");
}

#[test]
fn screen_polyline_invalid_offsets_and_visibility() {
    // #95 "screen polyline query rejects non-monotonic offsets and clears outputs"
    // #96 "screen polyline query rejects visibility bytes and clears outputs"
    let pixels = [0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 10.0, 0.0];
    let mouse = [5.0, 1.0];
    let mut index = 7u64;
    let mut distance = 7.0f64;
    let code95 = unsafe {
        narwhal_selection_core::narwhal_nearest_screen_polyline_f64(
            pixels.as_ptr(),
            [1u8, 1, 1, 1].as_ptr(),
            4,
            [0u64, 4, 3].as_ptr(),
            2,
            mouse.as_ptr(),
            9.0,
            &mut index,
            &mut distance,
        )
    };
    assert_eq!(code95, ERR_INPUT, "code {code95}");
    assert_eq!(index, U64_MAX, "index {index}");
    assert!(distance.is_infinite(), "distance {distance}");
    let code96 = unsafe {
        narwhal_selection_core::narwhal_nearest_screen_polyline_f64(
            pixels.as_ptr(),
            [1u8, 2, 1, 1].as_ptr(),
            4,
            [0u64, 2, 4].as_ptr(),
            2,
            mouse.as_ptr(),
            9.0,
            &mut index,
            &mut distance,
        )
    };
    assert_eq!(code96, ERR_INPUT, "code {code96}");
    assert_eq!(index, U64_MAX, "index {index}");
    assert!(distance.is_infinite(), "distance {distance}");
}

#[test]
fn nearest_point_empty_and_null_index() {
    // #97 "nearest point query safely accepts empty null input"
    // #98 "nearest point query rejects a null index output"
    let one: [f64; 3] = [0.0, 0.0, 0.0];
    let mut index = 7u64;
    let mut distance = 7.0f64;
    let code97 = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_point_f64(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            0,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            6.0,
            &mut index,
            &mut distance,
        )
    };
    assert_eq!(code97, 0, "code {code97}");
    assert_eq!(index, U64_MAX, "index {index}");
    assert!(distance.is_infinite(), "distance {distance}");
    let code98 = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_point_f64(
            IDENTITY.as_ptr(),
            one.as_ptr(),
            1,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            6.0,
            std::ptr::null_mut(),
            &mut distance,
        )
    };
    assert_eq!(code98, ERR_NULL, "code {code98}");
}

#[test]
fn point_rect_unordered_and_empty_null() {
    // #99 "point rectangle query rejects unordered bounds and clears every result"
    // #100 "point rectangle query safely accepts empty null input and output"
    let two: [f64; 6] = [0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
    let rect = [4.0, 4.0, 6.0, 6.0];
    let mut inside = [9u8, 9];
    let code99 = unsafe {
        narwhal_selection_core::narwhal_projected_points_rect_query_f64(
            IDENTITY.as_ptr(),
            two.as_ptr(),
            2,
            800.0,
            600.0,
            [800.0, 300.0, 400.0, 600.0].as_ptr(),
            inside.as_mut_ptr(),
        )
    };
    assert_eq!(code99, ERR_INPUT, "code {code99}");
    assert_eq!(inside, [0u8, 0], "inside {inside:?}");
    let code100 = unsafe {
        narwhal_selection_core::narwhal_projected_points_rect_query_f64(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            0,
            800.0,
            600.0,
            rect.as_ptr(),
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code100, 0, "code {code100}");
}

#[test]
fn nearest_point_oversized_and_screen_polyline_oversized() {
    // #101 "nearest point query rejects a slice whose byte span exceeds isize::MAX"
    // #102 "screen polyline query rejects a slice whose byte span exceeds isize::MAX"
    let one: [f64; 3] = [0.0, 0.0, 0.0];
    let mut index = 7u64;
    let mut distance = 7.0f64;
    let code101 = unsafe {
        narwhal_selection_core::narwhal_nearest_projected_point_f64(
            IDENTITY.as_ptr(),
            one.as_ptr(),
            oversized_count(3),
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            6.0,
            &mut index,
            &mut distance,
        )
    };
    assert_eq!(code101, ERR_SIZE, "code {code101}");
    let pixels = [0.0, 0.0];
    let code102 = unsafe {
        narwhal_selection_core::narwhal_nearest_screen_polyline_f64(
            pixels.as_ptr(),
            [1u8].as_ptr(),
            oversized_count(2),
            [0u64, 1].as_ptr(),
            1,
            [0.0, 0.0].as_ptr(),
            1.0,
            &mut index,
            &mut distance,
        )
    };
    assert_eq!(code102, ERR_SIZE, "code {code102}");
}

#[test]
fn display_points_null_matrix_clears_outputs() {
    // #103 "display-point projection clears outputs before rejecting a null matrix"
    let points: [f64; 6] = [0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
    let mut pixels = [1.0f64; 4];
    let mut visible = [9u8, 9];
    let code = unsafe {
        narwhal_selection_core::narwhal_project_display_points_f64(
            std::ptr::null(),
            points.as_ptr(),
            2,
            800.0,
            600.0,
            pixels.as_mut_ptr(),
            visible.as_mut_ptr(),
        )
    };
    assert_eq!(code, ERR_NULL, "code {code}");
    assert_eq!(visible, [0u8, 0], "visible {visible:?}");
    assert!(pixels.iter().all(|v| v.is_nan()), "pixels {pixels:?}");
}

#[test]
fn snap_distance_null_matrix_clears_outputs() {
    // #104 "snap-distance query clears outputs before rejecting a null matrix"
    let points: [f64; 6] = [0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
    let mut indices = [0u64, 0];
    let mut distances = [0.0f64, 0.0];
    let mut count = 99u64;
    let code = unsafe {
        narwhal_selection_core::narwhal_projected_snap_distances_f64(
            std::ptr::null(),
            points.as_ptr(),
            2,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            10.0,
            indices.as_mut_ptr(),
            distances.as_mut_ptr(),
            &mut count,
        )
    };
    assert_eq!(code, ERR_NULL, "code {code}");
    assert_eq!(count, 0, "count {count}");
    assert!(indices.iter().all(|v| *v == U64_MAX), "indices {indices:?}");
    assert!(
        distances.iter().all(|v| v.is_infinite()),
        "distances {distances:?}"
    );
}

#[test]
fn snap_distance_empty_and_null_outputs() {
    // #105 "snap-distance query safely accepts an empty null batch"
    // #106 "empty snap-distance query clears a supplied survivor count"
    // #107 (3 sub-cases) "snap-distance query rejects each null nonempty output"
    let one: [f64; 3] = [0.0, 0.0, 0.0];
    let mut count = 99u64;
    let code105 = unsafe {
        narwhal_selection_core::narwhal_projected_snap_distances_f64(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            0,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            10.0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code105, 0, "code {code105}");
    let code106 = unsafe {
        narwhal_selection_core::narwhal_projected_snap_distances_f64(
            IDENTITY.as_ptr(),
            std::ptr::null(),
            0,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            10.0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut count,
        )
    };
    assert_eq!(code106, 0, "code {code106}");
    assert_eq!(count, 0, "count {count}");
    let mut indices = [0u64, 0];
    let mut distances = [0.0f64, 0.0];
    let mut c = 99u64;
    let code107a = unsafe {
        narwhal_selection_core::narwhal_projected_snap_distances_f64(
            IDENTITY.as_ptr(),
            one.as_ptr(),
            1,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            1.0,
            std::ptr::null_mut(),
            distances.as_mut_ptr(),
            &mut c,
        )
    };
    assert_eq!(code107a, ERR_NULL, "code {code107a}");
    let code107b = unsafe {
        narwhal_selection_core::narwhal_projected_snap_distances_f64(
            IDENTITY.as_ptr(),
            one.as_ptr(),
            1,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            1.0,
            indices.as_mut_ptr(),
            std::ptr::null_mut(),
            &mut c,
        )
    };
    assert_eq!(code107b, ERR_NULL, "code {code107b}");
    let code107c = unsafe {
        narwhal_selection_core::narwhal_projected_snap_distances_f64(
            IDENTITY.as_ptr(),
            one.as_ptr(),
            1,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            1.0,
            indices.as_mut_ptr(),
            distances.as_mut_ptr(),
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code107c, ERR_NULL, "code {code107c}");
}

#[test]
fn snap_distance_invalid_radius_clears_outputs() {
    // #108 "invalid snap radius leaves completely cleared outputs"
    let points: [f64; 6] = [0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
    let mut indices = [0u64, 0];
    let mut distances = [0.0f64, 0.0];
    let mut count = 99u64;
    let code = unsafe {
        narwhal_selection_core::narwhal_projected_snap_distances_f64(
            IDENTITY.as_ptr(),
            points.as_ptr(),
            2,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            -1.0,
            indices.as_mut_ptr(),
            distances.as_mut_ptr(),
            &mut count,
        )
    };
    assert_eq!(code, ERR_INPUT, "code {code}");
    assert_eq!(count, 0, "count {count}");
    assert!(indices.iter().all(|v| *v == U64_MAX), "indices {indices:?}");
    assert!(
        distances.iter().all(|v| v.is_infinite()),
        "distances {distances:?}"
    );
}

#[test]
fn snap_distance_oversized_and_sparse_tail_sentinels() {
    // #109 "snap-distance query rejects a slice whose byte span exceeds isize::MAX"
    // #110 "sparse snap-distance success preserves compact tail sentinels"
    let one: [f64; 3] = [0.0, 0.0, 0.0];
    let mut indices = [0u64, 0];
    let mut distances = [0.0f64, 0.0];
    let mut count = 99u64;
    let code109 = unsafe {
        narwhal_selection_core::narwhal_projected_snap_distances_f64(
            IDENTITY.as_ptr(),
            one.as_ptr(),
            oversized_count(3),
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            1.0,
            indices.as_mut_ptr(),
            distances.as_mut_ptr(),
            &mut count,
        )
    };
    assert_eq!(code109, ERR_SIZE, "code {code109}");
    let points: [f64; 9] = [0.0, 0.0, 0.0, 1.0, 1.0, 0.0, -1.0, -1.0, 0.0];
    let mut sparse_indices = [0u64, 0, 0];
    let mut sparse_distances = [0.0f64, 0.0, 0.0];
    let mut sparse_count = 99u64;
    let code110 = unsafe {
        narwhal_selection_core::narwhal_projected_snap_distances_f64(
            IDENTITY.as_ptr(),
            points.as_ptr(),
            3,
            800.0,
            600.0,
            [400.0, 300.0].as_ptr(),
            0.0,
            sparse_indices.as_mut_ptr(),
            sparse_distances.as_mut_ptr(),
            &mut sparse_count,
        )
    };
    assert_eq!(code110, 0, "code {code110}");
    assert_eq!(sparse_count, 1, "count {sparse_count}");
    assert_eq!(sparse_indices[0], 0, "idx0 {sparse_indices:?}");
    assert_eq!(sparse_distances[0], 0.0, "dist0 {sparse_distances:?}");
    assert_eq!(sparse_indices[1], U64_MAX, "idx1 {sparse_indices:?}");
    assert_eq!(sparse_indices[2], U64_MAX, "idx2 {sparse_indices:?}");
    assert!(
        sparse_distances[1].is_infinite(),
        "dist1 {sparse_distances:?}"
    );
    assert!(
        sparse_distances[2].is_infinite(),
        "dist2 {sparse_distances:?}"
    );
}

#[test]
fn display_oversized_and_point_rect_null_matrix() {
    // #111 "display-point projection rejects a slice whose byte span exceeds isize::MAX"
    // #112 "point rectangle query clears output before rejecting a null matrix"
    let one: [f64; 3] = [0.0, 0.0, 0.0];
    let two: [f64; 6] = [0.0, 0.0, 0.0, 1.0, 1.0, 0.0];
    let rect = [4.0, 4.0, 6.0, 6.0];
    let mut pixels = [1.0f64; 4];
    let mut visible = [9u8, 9];
    let code111 = unsafe {
        narwhal_selection_core::narwhal_project_display_points_f64(
            IDENTITY.as_ptr(),
            one.as_ptr(),
            oversized_count(3),
            800.0,
            600.0,
            pixels.as_mut_ptr(),
            visible.as_mut_ptr(),
        )
    };
    assert_eq!(code111, ERR_SIZE, "code {code111}");
    let mut inside = [9u8, 9];
    let code112 = unsafe {
        narwhal_selection_core::narwhal_projected_points_rect_query_f64(
            std::ptr::null(),
            two.as_ptr(),
            2,
            800.0,
            600.0,
            rect.as_ptr(),
            inside.as_mut_ptr(),
        )
    };
    assert_eq!(code112, ERR_NULL, "code {code112}");
    assert_eq!(inside, [0u8, 0], "inside {inside:?}");
}
