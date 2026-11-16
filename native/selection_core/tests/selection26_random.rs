//! Replay of the 10 frozen selection corpora under `data/`, captured from the
//! deterministic random batches of `spike/test_rust_selection_core.py`
//! (CPython seed 0x4E415257).
//!
//! Each corpus stores, per row, the original inputs and the original expected
//! outputs recorded against the C ABI. Every test below calls the same
//! `narwhal_selection_core` export the original Python wrapper used and
//! compares the result against the stored expectation. All values are compared
//! bit-exactly except the nearest-point distance of
//! `narwhal_nearest_projected_point_f64`, which keeps the original 1e-12
//! tolerance.
//!
//! The corpora (total 2500 rows):
//!   selection26_screen500      500 screen-space segment/rect crossing batches
//!   selection26_world250       250 projected world-segment/rect crossing
//!                              batches (800x600), with evidence flags
//!   selection26_curves100      100 projected curve batches (800x600) with
//!                              per-curve statuses
//!   selection26_near250        250 nearest projected polyline queries
//!                              (1920x1080): segment, parameter, distance
//!   selection26_clip250        250 grouped clip-space batches (800x600) mapped
//!                              to screen boxes
//!   selection26_worldboxes250  250 grouped world-space batches (800x600)
//!                              mapped to screen boxes
//!   selection26_aabb250        250 AABB batches (1920x1080) mapped to screen
//!                              rects, with unbounded/behind flags
//!   selection26_poly250        250 grouped screen polylines: nearest index
//!                              and distance
//!   selection26_point200       200 display-point batches (800x600):
//!                              projection, nearest point (1e-12 tolerance),
//!                              point/rect query
//!   selection26_snap200        200 projected snap batches (800x600): indices
//!                              and distances within radius

#[path = "support/selection26_fixture.rs"]
mod selection26_fixture;

use selection26_fixture::Value;

const PERSPECTIVE: [f64; 16] = [
    1.2, 0.1, 0.0, 0.2, 0.0, 1.1, 0.1, -0.1, 0.0, 0.0, 1.0, 0.0, 0.15, -0.05, 0.2, 1.0,
];

fn parse_rows(data: &[u8], expected_rows: usize) -> Vec<Value> {
    let top = selection26_fixture::parse(data);
    let count = top.get("count").as_i128().unwrap() as usize;
    let rows = top.get("rows").as_array().unwrap().to_vec();
    assert_eq!(
        count,
        rows.len(),
        "stored count field ({}) != rows length ({})",
        count,
        rows.len()
    );
    assert_eq!(
        rows.len(),
        expected_rows,
        "corpus row count {} != expected {}",
        rows.len(),
        expected_rows
    );
    rows
}

fn to_f64s(v: &Value) -> Vec<f64> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_f64().unwrap())
        .collect()
}
fn to_u64s(v: &Value) -> Vec<u64> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_i128().unwrap() as u64)
        .collect()
}
fn to_u8s(v: &Value) -> Vec<u8> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_i128().unwrap() as u8)
        .collect()
}
fn is_null(v: &Value) -> bool {
    matches!(v, Value::Null)
}

// ---------------------------------------------------------------------------
// screen500: narwhal_any_screen_segment_crosses_rect_f64
// ---------------------------------------------------------------------------
#[test]
fn replay_screen500() {
    let data = include_bytes!("data/selection26_screen500.bin");
    let rows = parse_rows(data, 500);
    for (i, row) in rows.iter().enumerate() {
        let seg_arrays = row.get("segments").as_array().unwrap();
        let flat: Vec<f64> = seg_arrays
            .iter()
            .flat_map(|seg| seg.as_array().unwrap().iter())
            .flat_map(|p| p.as_array().unwrap().iter())
            .map(|v| v.as_f64().unwrap())
            .collect();
        let rect = to_f64s(row.get("rect"));
        let expected_any = row.get("any").as_bool().unwrap();
        let code = unsafe {
            narwhal_selection_core::narwhal_any_screen_segment_crosses_rect_f64(
                flat.as_ptr(),
                seg_arrays.len(),
                rect.as_ptr(),
            )
        };
        assert!(
            (code == 0) || (code == 1),
            "row{}: unexpected code {}",
            i,
            code
        );
        assert_eq!(
            code != 0,
            expected_any,
            "row{}: any mismatch (got code={} expected={})",
            i,
            code,
            expected_any
        );
    }
}

// ---------------------------------------------------------------------------
// world250: narwhal_any_projected_segment_crosses_rect_f32
//           + narwhal_projected_segments_rect_query_f32
// ---------------------------------------------------------------------------
#[test]
fn replay_world250() {
    let data = include_bytes!("data/selection26_world250.bin");
    let rows = parse_rows(data, 250);
    for (i, row) in rows.iter().enumerate() {
        let seg_arrays = row.get("segments").as_array().unwrap();
        let flat: Vec<f32> = seg_arrays
            .iter()
            .flat_map(|seg| seg.as_array().unwrap().iter())
            .flat_map(|p| p.as_array().unwrap().iter())
            .map(|v| v.as_f64().unwrap() as f32)
            .collect();
        let rect = to_f64s(row.get("rect"));
        let expected_any = row.get("any").as_bool().unwrap();
        let expected_ev = row.get("has_evidence").as_bool().unwrap();

        let code = unsafe {
            narwhal_selection_core::narwhal_any_projected_segment_crosses_rect_f32(
                PERSPECTIVE.as_ptr(),
                flat.as_ptr(),
                seg_arrays.len(),
                800.0,
                600.0,
                rect.as_ptr(),
            )
        };
        assert_eq!(
            code != 0,
            expected_any,
            "row{}: any mismatch (code={} expected={})",
            i,
            code,
            expected_any
        );

        let mut ev: u8 = 255;
        let q = unsafe {
            narwhal_selection_core::narwhal_projected_segments_rect_query_f32(
                PERSPECTIVE.as_ptr(),
                flat.as_ptr(),
                seg_arrays.len(),
                800.0,
                600.0,
                rect.as_ptr(),
                &mut ev,
            )
        };
        assert_eq!(
            q as i32, expected_any as i32,
            "row{}: query code mismatch",
            i
        );
        assert_eq!(ev != 0, expected_ev, "row{}: evidence mismatch", i);
    }
}

// ---------------------------------------------------------------------------
// curves100: narwhal_projected_curves_rect_query_f32
// ---------------------------------------------------------------------------
#[test]
fn replay_curves100() {
    let data = include_bytes!("data/selection26_curves100.bin");
    let rows = parse_rows(data, 100);
    for (i, row) in rows.iter().enumerate() {
        let curves = row.get("curves").as_array().unwrap();
        let mut flat: Vec<f32> = Vec::new();
        let mut seg_total = 0u64;
        let mut offsets: Vec<u64> = vec![0];
        for curve in curves {
            let segs = curve.as_array().unwrap();
            seg_total += segs.len() as u64;
            for seg in segs {
                for pt in seg.as_array().unwrap() {
                    for v in pt.as_array().unwrap() {
                        flat.push(v.as_f64().unwrap() as f32);
                    }
                }
            }
            offsets.push(seg_total);
        }
        let rect = to_f64s(row.get("rect"));
        let statuses_exp = row.get("statuses").as_array().unwrap();
        let curve_count = curves.len();
        let mut statuses = vec![0u8; curve_count];
        let code = unsafe {
            narwhal_selection_core::narwhal_projected_curves_rect_query_f32(
                PERSPECTIVE.as_ptr(),
                flat.as_ptr(),
                seg_total as usize,
                offsets.as_ptr(),
                curve_count,
                800.0,
                600.0,
                rect.as_ptr(),
                statuses.as_mut_ptr(),
            )
        };
        assert_eq!(code, 0, "row{}: code {}", i, code);
        for j in 0..curve_count {
            let e = statuses_exp[j].as_i128().unwrap() as u8;
            assert_eq!(
                statuses[j], e,
                "row{} curve{}: status got {} expected {}",
                i, j, statuses[j], e
            );
        }
    }
}

// ---------------------------------------------------------------------------
// near250: narwhal_nearest_projected_polylines_f64
// ---------------------------------------------------------------------------
#[test]
fn replay_near250() {
    let data = include_bytes!("data/selection26_near250.bin");
    let rows = parse_rows(data, 250);
    for (i, row) in rows.iter().enumerate() {
        let matrix = to_f64s(row.get("matrix"));
        let polys = row.get("polylines").as_array().unwrap();
        let mut flat: Vec<f64> = Vec::new();
        let mut pt_total = 0u64;
        let mut offsets: Vec<u64> = vec![0];
        for p in polys {
            let pts = p.as_array().unwrap();
            pt_total += pts.len() as u64;
            for pt in pts {
                for v in pt.as_array().unwrap() {
                    flat.push(v.as_f64().unwrap());
                }
            }
            offsets.push(pt_total);
        }
        let mouse = to_f64s(row.get("mouse"));
        let expected = row.get("rows").as_array().unwrap();
        let curve_count = polys.len();

        let mut seg_out = vec![0u64; curve_count];
        let mut par_out = vec![0.0f64; curve_count];
        let mut dis_out = vec![0.0f64; curve_count];
        let mut valid_out = vec![0u8; curve_count];
        let code = unsafe {
            narwhal_selection_core::narwhal_nearest_projected_polylines_f64(
                matrix.as_ptr(),
                flat.as_ptr(),
                pt_total as usize,
                offsets.as_ptr(),
                curve_count,
                1920.0,
                1080.0,
                mouse.as_ptr(),
                seg_out.as_mut_ptr(),
                par_out.as_mut_ptr(),
                dis_out.as_mut_ptr(),
                valid_out.as_mut_ptr(),
            )
        };
        assert_eq!(code, 0, "row{}: code {}", i, code);
        for j in 0..curve_count {
            if is_null(&expected[j]) {
                assert_eq!(
                    valid_out[j], 0,
                    "row{} curve{}: expected null, got valid",
                    i, j
                );
            } else {
                let e = expected[j].as_array().unwrap();
                assert_eq!(
                    valid_out[j], 1,
                    "row{} curve{}: expected valid, got null",
                    i, j
                );
                assert_eq!(
                    seg_out[j],
                    e[0].as_i128().unwrap() as u64,
                    "row{} curve{}: segment",
                    i,
                    j
                );
                assert_eq!(
                    par_out[j],
                    e[1].as_f64().unwrap(),
                    "row{} curve{}: parameter",
                    i,
                    j
                );
                assert_eq!(
                    dis_out[j],
                    e[2].as_f64().unwrap(),
                    "row{} curve{}: distance",
                    i,
                    j
                );
            }
        }
    }
}

// ---------------------------------------------------------------------------
// clip250: narwhal_screen_boxes_from_clip_f64
// ---------------------------------------------------------------------------
#[test]
fn replay_clip250() {
    let data = include_bytes!("data/selection26_clip250.bin");
    let rows = parse_rows(data, 250);
    for (i, row) in rows.iter().enumerate() {
        let clip = row.get("clip").as_array().unwrap();
        let flat: Vec<f64> = clip
            .iter()
            .flat_map(|p| p.as_array().unwrap().iter())
            .map(|v| v.as_f64().unwrap())
            .collect();
        let point_count = clip.len();
        let offsets = to_u64s(row.get("offsets"));
        let flags = to_u8s(row.get("point_flags"));
        let object_count = flags.len();
        let mut boxes = vec![0.0f64; object_count * 4];
        let mut valid = vec![0u8; object_count];
        let code = unsafe {
            narwhal_selection_core::narwhal_screen_boxes_from_clip_f64(
                flat.as_ptr(),
                point_count,
                offsets.as_ptr(),
                object_count,
                flags.as_ptr(),
                800.0,
                600.0,
                boxes.as_mut_ptr(),
                valid.as_mut_ptr(),
            )
        };
        assert_eq!(code, 0, "row{}: code {}", i, code);
        let exp = row.get("boxes").as_array().unwrap();
        for j in 0..object_count {
            if is_null(&exp[j]) {
                assert_eq!(valid[j], 0, "row{} box{}: expected null, got valid", i, j);
            } else {
                let e = exp[j].as_array().unwrap();
                assert_eq!(valid[j], 1, "row{} box{}: expected valid, got null", i, j);
                for k in 0..4 {
                    assert_eq!(
                        boxes[j * 4 + k],
                        e[k].as_f64().unwrap(),
                        "row{} box{}[{}]",
                        i,
                        j,
                        k
                    );
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// worldboxes250: narwhal_screen_boxes_from_world_f64
// ---------------------------------------------------------------------------
#[test]
fn replay_worldboxes250() {
    let data = include_bytes!("data/selection26_worldboxes250.bin");
    let rows = parse_rows(data, 250);
    for (i, row) in rows.iter().enumerate() {
        let matrix = to_f64s(row.get("matrix"));
        let points = row.get("points").as_array().unwrap();
        let flat: Vec<f64> = points
            .iter()
            .flat_map(|p| p.as_array().unwrap().iter())
            .map(|v| v.as_f64().unwrap())
            .collect();
        let point_count = points.len();
        let offsets = to_u64s(row.get("offsets"));
        let flags = to_u8s(row.get("flags"));
        let object_count = flags.len();
        let mut boxes = vec![0.0f64; object_count * 4];
        let mut valid = vec![0u8; object_count];
        let code = unsafe {
            narwhal_selection_core::narwhal_screen_boxes_from_world_f64(
                matrix.as_ptr(),
                flat.as_ptr(),
                point_count,
                offsets.as_ptr(),
                object_count,
                flags.as_ptr(),
                800.0,
                600.0,
                boxes.as_mut_ptr(),
                valid.as_mut_ptr(),
            )
        };
        assert_eq!(code, 0, "row{}: code {}", i, code);
        let exp = row.get("boxes").as_array().unwrap();
        for j in 0..object_count {
            if is_null(&exp[j]) {
                assert_eq!(valid[j], 0, "row{} box{}: expected null, got valid", i, j);
            } else {
                let e = exp[j].as_array().unwrap();
                assert_eq!(valid[j], 1, "row{} box{}: expected valid, got null", i, j);
                for k in 0..4 {
                    assert_eq!(
                        boxes[j * 4 + k],
                        e[k].as_f64().unwrap(),
                        "row{} box{}[{}]",
                        i,
                        j,
                        k
                    );
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// aabb250: narwhal_screen_rects_from_aabbs_f64
// ---------------------------------------------------------------------------
#[test]
fn replay_aabb250() {
    let data = include_bytes!("data/selection26_aabb250.bin");
    let rows = parse_rows(data, 250);
    for (i, row) in rows.iter().enumerate() {
        let matrix = to_f64s(row.get("matrix"));
        let lower = row.get("lower").as_array().unwrap();
        let upper = row.get("upper").as_array().unwrap();
        let box_count = lower.len();
        let lower_flat: Vec<f64> = lower
            .iter()
            .flat_map(|p| p.as_array().unwrap().iter())
            .map(|v| v.as_f64().unwrap())
            .collect();
        let upper_flat: Vec<f64> = upper
            .iter()
            .flat_map(|p| p.as_array().unwrap().iter())
            .map(|v| v.as_f64().unwrap())
            .collect();
        let mut rects = vec![0.0f64; box_count * 4];
        let mut unb = vec![0u8; box_count];
        let mut behind = vec![0u8; box_count];
        let code = unsafe {
            narwhal_selection_core::narwhal_screen_rects_from_aabbs_f64(
                matrix.as_ptr(),
                lower_flat.as_ptr(),
                upper_flat.as_ptr(),
                box_count,
                1920.0,
                1080.0,
                rects.as_mut_ptr(),
                unb.as_mut_ptr(),
                behind.as_mut_ptr(),
            )
        };
        assert_eq!(code, 0, "row{}: code {}", i, code);
        let expected = row.get("rows").as_array().unwrap();
        for j in 0..box_count {
            let e = expected[j].as_array().unwrap();
            let rect = e[0].as_array().unwrap();
            for k in 0..4 {
                assert_eq!(
                    rects[j * 4 + k],
                    rect[k].as_f64().unwrap(),
                    "row{} box{}[{}]",
                    i,
                    j,
                    k
                );
            }
            assert_eq!(
                unb[j],
                e[1].as_i128().unwrap() as u8,
                "row{} box{}: unbounded",
                i,
                j
            );
            assert_eq!(
                behind[j],
                e[2].as_i128().unwrap() as u8,
                "row{} box{}: behind",
                i,
                j
            );
        }
    }
}

// ---------------------------------------------------------------------------
// point200: narwhal_project_display_points_f64
//           + narwhal_nearest_projected_point_f64
//           + narwhal_projected_points_rect_query_f64
// ---------------------------------------------------------------------------
#[test]
fn replay_point200() {
    let data = include_bytes!("data/selection26_point200.bin");
    let rows = parse_rows(data, 200);
    for (i, row) in rows.iter().enumerate() {
        let points = row.get("points").as_array().unwrap();
        let flat: Vec<f64> = points
            .iter()
            .flat_map(|p| p.as_array().unwrap().iter())
            .map(|v| v.as_f64().unwrap())
            .collect();
        let count = points.len();
        let cursor = to_f64s(row.get("cursor"));
        let limit = row.get("limit").as_f64().unwrap();
        let projected = row.get("projected").as_array().unwrap();
        let exp_index = row.get("index").as_i128().unwrap();
        let exp_distance = row.get("distance").as_f64().unwrap();
        let rect = to_f64s(row.get("rect"));
        let inside = row.get("inside").as_array().unwrap();

        // display projection (mixed precision f32 cast)
        let mut disp_xy = vec![0.0f64; count * 2];
        let mut disp_vis = vec![0u8; count];
        let dcode = unsafe {
            narwhal_selection_core::narwhal_project_display_points_f64(
                PERSPECTIVE.as_ptr(),
                flat.as_ptr(),
                count,
                800.0,
                600.0,
                disp_xy.as_mut_ptr(),
                disp_vis.as_mut_ptr(),
            )
        };
        assert_eq!(dcode, 0, "row{}: display code {}", i, dcode);
        for j in 0..count {
            if is_null(&projected[j]) {
                assert_eq!(
                    disp_vis[j], 0,
                    "row{} pt{}: expected null, got visible",
                    i, j
                );
            } else {
                let e = projected[j].as_array().unwrap();
                assert_eq!(
                    disp_vis[j], 1,
                    "row{} pt{}: expected visible, got null",
                    i, j
                );
                assert_eq!(disp_xy[j * 2], e[0].as_f64().unwrap(), "row{} pt{} x", i, j);
                assert_eq!(
                    disp_xy[j * 2 + 1],
                    e[1].as_f64().unwrap(),
                    "row{} pt{} y",
                    i,
                    j
                );
            }
        }

        // nearest point (tolerance 1e-12, matching original Python)
        let mut np_idx = u64::MAX;
        let mut np_dis = f64::INFINITY;
        let ncode = unsafe {
            narwhal_selection_core::narwhal_nearest_projected_point_f64(
                PERSPECTIVE.as_ptr(),
                flat.as_ptr(),
                count,
                800.0,
                600.0,
                cursor.as_ptr(),
                limit,
                &mut np_idx,
                &mut np_dis,
            )
        };
        let miss = exp_index == i128::from(u64::MAX);
        if miss {
            assert_eq!(ncode, 0, "row{}: nearest code (expected 0)", i);
            assert_eq!(np_idx, u64::MAX, "row{}: nearest idx", i);
            assert!(np_dis.is_infinite(), "row{}: nearest dis should be inf", i);
        } else {
            assert_eq!(ncode, 1, "row{}: nearest code (expected 1)", i);
            assert_eq!(np_idx, exp_index as u64, "row{}: nearest idx", i);
            assert!(
                (np_dis - exp_distance).abs() < 1e-12,
                "row{}: nearest dist {} vs {} (tol 1e-12)",
                i,
                np_dis,
                exp_distance
            );
        }

        // inside rect
        let mut inside_out = vec![0u8; count];
        let rcode = unsafe {
            narwhal_selection_core::narwhal_projected_points_rect_query_f64(
                PERSPECTIVE.as_ptr(),
                flat.as_ptr(),
                count,
                800.0,
                600.0,
                rect.as_ptr(),
                inside_out.as_mut_ptr(),
            )
        };
        assert_eq!(rcode, 0, "row{}: rect code {}", i, rcode);
        for j in 0..count {
            assert_eq!(
                inside_out[j],
                inside[j].as_i128().unwrap() as u8,
                "row{} pt{}: inside",
                i,
                j
            );
        }
    }
}

// ---------------------------------------------------------------------------
// poly250: narwhal_nearest_screen_polyline_f64
// ---------------------------------------------------------------------------
#[test]
fn replay_poly250() {
    let data = include_bytes!("data/selection26_poly250.bin");
    let rows = parse_rows(data, 250);
    for (i, row) in rows.iter().enumerate() {
        let pixels = row.get("pixels").as_array().unwrap();
        let pixel_flat: Vec<f64> = pixels
            .iter()
            .flat_map(|p| p.as_array().unwrap().iter())
            .map(|v| v.as_f64().unwrap())
            .collect();
        let point_count = pixels.len();
        let visibility = to_u8s(row.get("visibility"));
        let offsets = to_u64s(row.get("offsets"));
        let cursor = to_f64s(row.get("cursor"));
        let limit = row.get("limit").as_f64().unwrap();
        let curve_count = offsets.len() - 1;
        let exp_index = row.get("index").as_i128().unwrap();
        let exp_distance = row.get("distance").as_f64().unwrap();

        let mut idx = u64::MAX;
        let mut dis = f64::INFINITY;
        let code = unsafe {
            narwhal_selection_core::narwhal_nearest_screen_polyline_f64(
                pixel_flat.as_ptr(),
                visibility.as_ptr(),
                point_count,
                offsets.as_ptr(),
                curve_count,
                cursor.as_ptr(),
                limit,
                &mut idx,
                &mut dis,
            )
        };
        let miss = exp_index == i128::from(u64::MAX);
        if miss {
            assert_eq!(code, 0, "row{}: expected miss code 0, got {}", i, code);
            assert_eq!(idx, u64::MAX, "row{}: expected idx U64MAX", i);
            assert!(dis.is_infinite(), "row{}: expected dis inf", i);
        } else {
            assert_eq!(code, 1, "row{}: expected hit code 1, got {}", i, code);
            assert_eq!(idx, exp_index as u64, "row{}: idx mismatch", i);
            assert_eq!(dis, exp_distance, "row{}: dist mismatch", i);
        }
    }
}

// ---------------------------------------------------------------------------
// snap200: narwhal_projected_snap_distances_f64
// ---------------------------------------------------------------------------
#[test]
fn replay_snap200() {
    let data = include_bytes!("data/selection26_snap200.bin");
    let rows = parse_rows(data, 200);
    for (i, row) in rows.iter().enumerate() {
        let points = row.get("points").as_array().unwrap();
        let flat: Vec<f64> = points
            .iter()
            .flat_map(|p| p.as_array().unwrap().iter())
            .map(|v| v.as_f64().unwrap())
            .collect();
        let count = points.len();
        let mouse = to_f64s(row.get("mouse"));
        let radius = row.get("radius").as_f64().unwrap();
        let indices_exp = row.get("indices").as_array().unwrap();
        let distances_exp = row.get("distances").as_array().unwrap();
        let k = indices_exp.len();

        let cap = if count == 0 { 1 } else { count };
        let mut indices = vec![0u64; cap];
        let mut distances = vec![0.0f64; cap];
        let mut out_count: u64 = 0;
        let code = unsafe {
            narwhal_selection_core::narwhal_projected_snap_distances_f64(
                PERSPECTIVE.as_ptr(),
                flat.as_ptr(),
                count,
                800.0,
                600.0,
                mouse.as_ptr(),
                radius,
                indices.as_mut_ptr(),
                distances.as_mut_ptr(),
                &mut out_count,
            )
        };
        assert_eq!(code, 0, "row{}: code {}", i, code);
        assert_eq!(
            out_count, k as u64,
            "row{}: out_count {} vs {}",
            i, out_count, k
        );
        for j in 0..k {
            assert_eq!(
                indices[j],
                indices_exp[j].as_i128().unwrap() as u64,
                "row{} idx[{}]",
                i,
                j
            );
            assert_eq!(
                distances[j],
                distances_exp[j].as_f64().unwrap(),
                "row{} dist[{}]",
                i,
                j
            );
        }
    }
}

// ---------------------------------------------------------------------------
// Fixture-integrity guard: a corpus whose rows were truncated and whose stored
// count was updated to match must still be rejected by the per-corpus expected
// count pinned in the tests above (a decoder that trusted the stored count
// field alone would accept such a fixture).
// ---------------------------------------------------------------------------
fn build_tagged_fixture(count: u64, rows: u64) -> Vec<u8> {
    // Root Map { "provenance": "", "count": <count>, "rows": [Null; <rows>] }
    // in the tagged binary format used by the corpora under `data/`.
    let mut buf = Vec::with_capacity(64 + rows as usize);
    buf.push(7); // Map
    buf.extend_from_slice(&3u32.to_le_bytes());
    buf.push(5); // key "provenance"
    buf.extend_from_slice(&10u32.to_le_bytes());
    buf.extend_from_slice(b"provenance");
    buf.push(5); // value ""
    buf.extend_from_slice(&0u32.to_le_bytes());
    buf.push(5); // key "count"
    buf.extend_from_slice(&5u32.to_le_bytes());
    buf.extend_from_slice(b"count");
    buf.push(4); // value i128
    buf.extend_from_slice(&(count as i128).to_le_bytes());
    buf.push(5); // key "rows"
    buf.extend_from_slice(&4u32.to_le_bytes());
    buf.extend_from_slice(b"rows");
    buf.push(6); // value Array of Null
    buf.extend_from_slice(&(rows as u32).to_le_bytes());
    buf.extend_from_slice(&vec![0u8; rows as usize]);
    buf
}

#[test]
fn count_pin_rejects_truncated_fixture() {
    // 499 rows with the stored count updated to 499: accepted only when the
    // expected count is 499, rejected by the screen500 pin of 500.
    let truncated = build_tagged_fixture(499, 499);
    assert!(
        std::panic::catch_unwind(|| parse_rows(&truncated, 500)).is_err(),
        "pinned expected count 500 must reject a 499-row fixture"
    );
    assert_eq!(parse_rows(&truncated, 499).len(), 499);
    // A stored count that disagrees with the rows array is rejected even when
    // the expected count matches the rows.
    let inconsistent = build_tagged_fixture(498, 499);
    assert!(
        std::panic::catch_unwind(|| parse_rows(&inconsistent, 499)).is_err(),
        "stored count field must agree with the rows array"
    );
}
