//! Independent original SDK transcripts and expected result bits. No Python oracle.
use narwhal_pure_core::nearest::narwhal_pure_core_nearest_on_callbacks_v1 as search;
use std::ffi::c_void;
fn floats(s: &str) -> Vec<f64> {
    if s.is_empty() {
        vec![]
    } else {
        s.split(',')
            .map(|v| f64::from_bits(u64::from_str_radix(v, 16).unwrap()))
            .collect()
    }
}
struct Event {
    order: u32,
    t: u64,
    code: i32,
    values: Vec<f64>,
}
struct Replay {
    events: Vec<Event>,
    index: usize,
    mismatch: bool,
}
unsafe extern "C" fn eval(ctx: *mut c_void, t: f64, order: u32, out: *mut f64) -> i32 {
    let r = &mut *(ctx as *mut Replay);
    let Some(e) = r.events.get(r.index) else {
        r.mismatch = true;
        return -4;
    };
    r.index += 1;
    if t.to_bits() != e.t || order != e.order {
        r.mismatch = true;
        return -4;
    }
    if e.code == 0 {
        std::slice::from_raw_parts_mut(out, e.values.len()).copy_from_slice(&e.values)
    }
    e.code
}
#[test]
fn original_sdk_callback_transcripts_and_result_bits() {
    let fixture = if cfg!(target_os = "windows") {
        std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/nearest25_callbacks_windows_msvc.tsv"),
        )
        .expect("independent original Windows SDK capture required before qualification")
    } else {
        include_str!("fixtures/nearest25_callbacks_linux.tsv").to_owned()
    };
    let lines: Vec<_> = fixture.lines().collect();
    let mut cursor = 0;
    let mut cases = 0;
    while cursor < lines.len() {
        let f: Vec<_> = lines[cursor].split('\t').collect();
        assert_eq!(f[0], "case");
        cursor += 1;
        let domain = floats(f[2]);
        let target = floats(f[3]);
        let knots = floats(f[4]);
        let expected = floats(f[7]);
        let mut replay = Replay {
            events: vec![],
            index: 0,
            mismatch: false,
        };
        while cursor < lines.len() && lines[cursor].starts_with("eval\t") {
            let e: Vec<_> = lines[cursor].split('\t').collect();
            cursor += 1;
            replay.events.push(Event {
                order: e[1].parse().unwrap(),
                t: u64::from_str_radix(e[2], 16).unwrap(),
                code: e[3].parse().unwrap(),
                values: floats(e[4]),
            });
        }
        let mut out = [123.; 5];
        let mut end = 77;
        let code = unsafe {
            search(
                domain[0],
                domain[1],
                knots.as_ptr(),
                knots.len(),
                f[5].parse().unwrap(),
                f[6].parse().unwrap(),
                Some(eval),
                &mut replay as *mut _ as *mut c_void,
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut end,
            )
        };
        assert!(
            !replay.mismatch,
            "callback order/parameter mismatch case {} at {}",
            f[1], replay.index
        );
        assert_eq!(code, f[9].parse::<i32>().unwrap(), "case {}", f[1]);
        assert_eq!(
            replay.index,
            replay.events.len(),
            "unconsumed events case {}",
            f[1]
        );
        if code == 0 {
            assert_eq!(
                out.map(f64::to_bits).to_vec(),
                expected.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
                "case {}",
                f[1]
            );
            assert_eq!(end, f[8].parse::<u8>().unwrap());
        } else {
            assert_eq!(out, [123.; 5]);
            assert_eq!(end, 77)
        }
        cases += 1;
    }
    assert_eq!(cases, 234);
}
#[test]
fn callbacks_layout_input_and_transactional_failures() {
    unsafe {
        unsafe extern "C" fn fail(_: *mut c_void, _: f64, _: u32, _: *mut f64) -> i32 {
            -4
        }
        let target = [1., 2., 3.];
        let mut out = [123.; 5];
        let mut end = 77;
        let call = |lo, hi, n, closed, output: *mut f64, end: *mut u8| {
            search(
                lo,
                hi,
                std::ptr::null(),
                0,
                n,
                closed,
                Some(fail),
                std::ptr::null_mut(),
                target.as_ptr(),
                output,
                end,
            )
        };
        assert_eq!(call(0., 1., 1, 0, out.as_mut_ptr(), &mut end), -4);
        assert_eq!(out, [123.; 5]);
        assert_eq!(end, 77);
        assert_eq!(call(0., 1., 20001, 0, out.as_mut_ptr(), &mut end), -2);
        assert_eq!(call(0., 0., 1, 0, out.as_mut_ptr(), &mut end), -2);
        assert_eq!(call(f64::NAN, 1., 1, 0, out.as_mut_ptr(), &mut end), -2);
        assert_eq!(call(0., 1., 1, 2, out.as_mut_ptr(), &mut end), -2);
        assert_eq!(call(0., 1., 1, 0, std::ptr::null_mut(), &mut end), -1);
        assert_eq!(
            call(0., 1., 1, 0, out.as_mut_ptr(), out.as_mut_ptr() as *mut u8),
            -1
        );
        assert_eq!(
            call(0., 1., 1, 0, target.as_ptr() as *mut f64, &mut end),
            -1
        );
        assert_eq!(out, [123.; 5]);
        assert_eq!(end, 77);
    }
}

#[test]
fn callback_work_budget_stops_with_no_partial_output() {
    unsafe {
        unsafe extern "C" fn busy(ctx: *mut c_void, _: f64, order: u32, out: *mut f64) -> i32 {
            *(ctx as *mut usize) += 1;
            let v = std::slice::from_raw_parts_mut(out, if order == 0 { 3 } else { 9 });
            v.fill(0.);
            if order == 2 {
                v[0] = 0.001;
                v[3] = 1.;
            }
            0
        }
        let target = [0., 1., 0.];
        let mut out = [123.; 5];
        let mut end = 77;
        let mut calls = 0_usize;
        assert_eq!(
            search(
                0.,
                1e6,
                std::ptr::null(),
                0,
                20000,
                0,
                Some(busy),
                &mut calls as *mut _ as *mut c_void,
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut end
            ),
            -2
        );
        assert_eq!(calls, 1_000_000);
        assert_eq!(out, [123.; 5]);
        assert_eq!(end, 77);
    }
}
#[test]
fn nonfinite_sdk_values_are_named_refusals_without_partial_output() {
    unsafe {
        unsafe extern "C" fn bad(_: *mut c_void, _: f64, _: u32, out: *mut f64) -> i32 {
            *out = f64::NAN;
            0
        }
        let target = [0.; 3];
        let mut out = [123.; 5];
        let mut end = 77;
        assert_eq!(
            search(
                0.,
                1.,
                std::ptr::null(),
                0,
                1,
                0,
                Some(bad),
                std::ptr::null_mut(),
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut end
            ),
            -2
        );
        assert_eq!(out, [123.; 5]);
        assert_eq!(end, 77);
    }
}

#[test]
fn legacy_nurbs_batch_work_budget_and_aliases_refuse_before_writes() {
    unsafe {
        use narwhal_pure_core::nearest::{
            narwhal_pure_core_nearest_on_nurbs as one,
            narwhal_pure_core_nearest_on_nurbs_many as many,
        };
        let knots = [0., 0., 0., 1., 1., 1.];
        let cvs = [
            0., 0., 0., 1., 1., 0., 0., 1., 2., 0., 0., 1., 3., 0., 0., 1.,
        ];
        let target = [0.; 3];
        let mut out = [123.; 5];
        let mut end = 77;
        assert_eq!(
            one(
                4,
                knots.as_ptr(),
                6,
                cvs.as_ptr(),
                4,
                0,
                0,
                target.as_ptr(),
                out.as_mut_ptr(),
                out.as_mut_ptr() as *mut u8
            ),
            -1
        );
        assert_eq!(out, [123.; 5]);
        // Mandatory point samples alone exceed the same actual-work limit.
        // The previous cubic fixture did not: charging hypothetical Newton
        // iterations falsely refused ordinary large Rebuild operations.
        let budget_order = 32;
        let budget_knots: Vec<_> = (0..62).map(|i| if i < 31 { 0. } else { 1. }).collect();
        let budget_cvs: Vec<_> = (0..32).flat_map(|i| [i as f64, 0., 0., 1.]).collect();
        let targets = vec![0.; 300000];
        let mut outputs = vec![123.; 500000];
        let mut ends = vec![77; 100000];
        assert_eq!(
            many(
                budget_order,
                budget_knots.as_ptr(),
                budget_knots.len(),
                budget_cvs.as_ptr(),
                budget_order,
                0,
                0,
                targets.as_ptr(),
                100000,
                outputs.as_mut_ptr(),
                ends.as_mut_ptr()
            ),
            -2
        );
        assert!(outputs.iter().all(|v| *v == 123.));
        assert!(ends.iter().all(|v| *v == 77));
        assert_eq!(
            one(
                4,
                knots.as_ptr(),
                6,
                cvs.as_ptr(),
                4,
                0,
                0,
                target.as_ptr(),
                out.as_mut_ptr(),
                &mut end
            ),
            0
        );
    }
}

#[test]
fn finite_input_distance_overflow_is_transactional_scalar_and_batch() {
    unsafe {
        use narwhal_pure_core::nearest::{
            narwhal_pure_core_nearest_on_nurbs as one,
            narwhal_pure_core_nearest_on_nurbs_many as many,
        };
        let knots = [0., 0., 0., 1., 1., 1.];
        let cvs = [
            0., 0., 0., 1., 1., 0., 0., 1., 2., 0., 0., 1., 3., 0., 0., 1.,
        ];
        let targets = [0., 0., 0., 1e308, 0., 0.];
        let mut output = [123.; 10];
        let mut ends = [77; 2];
        assert_eq!(
            many(
                4,
                knots.as_ptr(),
                6,
                cvs.as_ptr(),
                4,
                0,
                0,
                targets.as_ptr(),
                2,
                output.as_mut_ptr(),
                ends.as_mut_ptr()
            ),
            -2
        );
        assert_eq!(output, [123.; 10]);
        assert_eq!(ends, [77; 2]);
        assert_eq!(
            one(
                4,
                knots.as_ptr(),
                6,
                cvs.as_ptr(),
                4,
                0,
                0,
                targets.as_ptr().add(3),
                output.as_mut_ptr(),
                ends.as_mut_ptr()
            ),
            -2
        );
        assert_eq!(output, [123.; 10]);
        assert_eq!(ends, [77; 2]);
    }
}
