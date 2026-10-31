use crate::popup::*;
use crate::popup_ffi::*;
use std::cell::RefCell;
thread_local! { static METRICS: RefCell<Vec<(i64,u32)>> = const { RefCell::new(vec![]) }; }
unsafe extern "C" fn measure(id: i64, kind: u32, _size: f64, out: *mut f64) -> u32 {
    METRICS.with(|m| m.borrow_mut().push((id, kind)));
    out.write(if id < 0 { 11.0 } else { 35.0 });
    0
}
fn popup(count: usize, scale: f64) -> Popup {
    let mut p = Popup::new(1000.0, 800.0, scale);
    p.open(
        vec![Row::default(); count],
        count,
        (120.0, 650.0),
        true,
        measure,
    )
    .unwrap();
    p
}
fn point(p: &Popup, i: usize) -> (f64, f64) {
    let r = p.panels[0].rows[i];
    ((r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0)
}
#[test]
fn popup_fractional_layout() {
    let p = popup(2, 0.85);
    assert!(p.panels[0].up.is_none());
    assert!(p.panels[0].max_scroll < 1e-6);
    assert!((p.panels[0].rect[3] - (650.0 - 3.0 * 0.85)).abs() < 1e-9);
    let mut p = popup(80, 1.0);
    p.event("END", 1, 0.0, 0.0, measure).unwrap();
    let r = p.panels[0].rows[79];
    assert!(r[1] >= p.panels[0].body[1] - 1e-8);
    assert!(p.panels[0].offset > 0.0);
    for (w, h) in [(1.0, 1.0), (10.0, 10.0), (160.0, 120.0)] {
        p.resize(w, h);
        let r = p.panels[0].rect;
        assert!(r[0] >= 0.0 && r[1] >= 0.0 && r[2] <= w && r[3] <= h);
    }
}
#[test]
fn popup_release_and_resize_guards() {
    for key in [
        "LEFTMOUSE",
        "RIGHTMOUSE",
        "MIDDLEMOUSE",
        "BUTTON4MOUSE",
        "BUTTON7MOUSE",
        "PEN",
        "ERASER",
    ] {
        let mut p = popup(2, 1.0);
        assert!(p.event(key, 1, 0.0, 0.0, measure).unwrap().consumed);
        assert!(p.panels.is_empty());
        assert!(p.event(key, 2, 0.0, 0.0, measure).unwrap().consumed);
        assert!(!p.event(key, 2, 0.0, 0.0, measure).unwrap().consumed);
    }
    let mut p = popup(2, 1.0);
    let (x, y) = point(&p, 0);
    assert!(p
        .event("LEFTMOUSE", 2, x, y, measure)
        .unwrap()
        .action
        .is_none());
    p.event("LEFTMOUSE", 1, x, y, measure).unwrap();
    p.resize(900.0, 800.0);
    assert!(p
        .event("LEFTMOUSE", 2, x, y, measure)
        .unwrap()
        .action
        .is_none());
    p.event("LEFTMOUSE", 1, x, y, measure).unwrap();
    assert_eq!(
        p.event("LEFTMOUSE", 2, x, y, measure).unwrap().action,
        Some(0)
    );
    assert!(p.panels.is_empty());
}
#[test]
fn popup_lazy_submenu_measurement() {
    METRICS.with(|m| m.borrow_mut().clear());
    let rows = vec![
        Row {
            child_start: 1,
            child_count: 1,
            separator: 0,
        },
        Row::default(),
    ];
    assert!(validate_rows(&rows, 1));
    let mut p = Popup::new(300.0, 400.0, 1.0);
    p.open(rows, 1, (270.0, 350.0), true, measure).unwrap();
    METRICS.with(|m| assert_eq!(*m.borrow(), vec![(-1, 0), (0, 0), (0, 1)]));
    p.event("HOME", 1, 0.0, 0.0, measure).unwrap();
    p.event("RIGHT_ARROW", 1, 0.0, 0.0, measure).unwrap();
    assert_eq!(p.panels.len(), 2);
    assert_eq!(p.panels[1].selected, Some(0));
    assert!(p.panels[1].rect[0] <= p.panels[0].rect[0]);
    METRICS.with(|m| {
        assert_eq!(
            *m.borrow(),
            vec![(-1, 0), (0, 0), (0, 1), (-2, 0), (1, 0), (1, 1)]
        )
    });
    p.event("LEFT_ARROW", 1, 0.0, 0.0, measure).unwrap();
    assert_eq!(p.panels.len(), 1);
}
unsafe fn apply(handle: u64, r: Request, rows: &[Row], key: &str, out: &mut Reply) -> u32 {
    narwhal_popup_apply(
        handle,
        &r,
        std::mem::size_of::<Request>(),
        key.as_ptr(),
        key.len(),
        rows.as_ptr(),
        rows.len(),
        Some(measure),
        out,
        std::mem::size_of::<Reply>(),
    )
}
#[test]
fn popup_abi_lifetime_bounds_and_editor_unchanged() {
    unsafe {
        assert_eq!(crate::narwhal_editor_abi(), 1);
        assert_eq!(std::mem::size_of::<crate::State>(), 88);
        assert_eq!(narwhal_popup_abi(), 1);
        assert_eq!(std::mem::size_of::<Reply>(), 48);
        let mut out = Reply::default();
        assert_eq!(
            apply(
                0,
                Request {
                    x: 1000.0,
                    y: 800.0,
                    scale: 1.0,
                    ..Default::default()
                },
                &[],
                "",
                &mut out
            ),
            0
        );
        let h = out.handle;
        let open = Request {
            op: 1,
            x: 120.0,
            y: 650.0,
            root_count: 2,
            title: 1,
            ..Default::default()
        };
        let rows = [Row::default(); 2];
        assert_eq!(
            narwhal_popup_apply(
                h,
                &open,
                std::mem::size_of::<Request>(),
                std::ptr::null(),
                0,
                rows.as_ptr(),
                2,
                Some(measure),
                &mut out,
                47
            ),
            1
        );
        assert_eq!(apply(h, open, &rows, "", &mut out), 0);
        assert_eq!(out.panels, 1);
        let revision = out.revision;
        let mut panel = PanelState::default();
        assert_eq!(
            narwhal_popup_panel(
                h,
                revision - 1,
                0,
                &mut panel,
                std::mem::size_of::<PanelState>()
            ),
            2
        );
        assert_eq!(
            narwhal_popup_panel(
                h,
                revision,
                0,
                &mut panel,
                std::mem::size_of::<PanelState>()
            ),
            0
        );
        assert_eq!(
            apply(
                h,
                Request {
                    op: 2,
                    value: 1,
                    ..Default::default()
                },
                &[],
                "ESC",
                &mut out
            ),
            0
        );
        assert_eq!(out.panels, 0);
        assert_eq!(apply(h, open, &rows, "", &mut out), 0);
        assert_eq!(out.handle, h);
        assert_eq!(
            apply(
                h,
                Request {
                    op: 4,
                    ..Default::default()
                },
                &[],
                "",
                &mut out
            ),
            0
        );
        assert_eq!(
            apply(
                h,
                Request {
                    op: 2,
                    value: 2,
                    ..Default::default()
                },
                &[],
                "ESC",
                &mut out
            ),
            0
        );
        assert_eq!(out.consumed, 1);
        assert_eq!(narwhal_popup_destroy(h), 0);
        assert_eq!(narwhal_popup_destroy(h), 2);
        assert_eq!(apply(h, open, &rows, "", &mut out), 2);
        let bad = [Row {
            child_start: 0,
            child_count: 1,
            separator: 0,
        }];
        assert!(!validate_rows(&bad, 1));
    }
}
