//! Popup-28: replay of the 900 frozen popup steps (source
//! `spike/test_ui_core_popup.py::test_golden_event_trace`, `DATA['popup']`)
//! against the existing public popup ABI 1, from Rust.
//!
//! `mod popup;` / `mod popup_ffi;` are private and deliberately not
//! re-exported by `narwhal_ui_core`, so the replay drives the crate through
//! the **exported C symbols** — the same cross-boundary path the live Python
//! adapter (`narwhal3d/native_popup.py`) drives. The integration test
//! re-declares the `#[repr(C)]` structs and the seven `extern "C"` functions
//! (matching `narwhal_ui_core.h` / `src/popup_ffi.rs` byte-for-byte) and pulls
//! the crate in through a real Rust import
//! (`narwhal_ui_core::narwhal_editor_abi`), which forces the Rlib to be
//! linked into the test binary.
//!
//! Checked per step (900 hard, none omitted):
//!   * `narwhal_popup_apply` returns 0 and a reply passing every invariant the
//!     live adapter enforces (handle/revision monotonicity, panels bound,
//!     consumed bound, action-token bound, "closed after activation");
//!   * `Reply.consumed` equals the frozen `state` per step;
//!   * `Reply.action` equals the frozen cumulative callback log (the action
//!     token is retained for the callback-semantics phase; the callback
//!     itself is Python and out of scope here);
//!   * panel projections: count, per-depth `PanelState` (start/count/
//!     parent/selected/rect/body/up/down/offset/max_scroll/width/scrolling)
//!     and the exact row-rectangle range, floats at the original
//!     `assertAlmostEqual(places=7)` bar (absolute `|diff| < 5e-8`);
//!   * guard projection equals the frozen sorted NUL-separated key set;
//!   * hit projection (depth/part) equals the frozen `[depth, row|up|down|
//!     null]` per step at the event coordinates.
//!
//! Scope: this replay retires ONLY the numeric layout/state acceptance
//! (floats, rects, panel/row projections, `consumed`, guards, hit, releases)
//! to Rust. The full host-side row metadata (`enabled`, `action`, `children`,
//! `shortcut`) and the real callback/event/hit semantics remain in the live
//! Python adapter (`narwhal3d/native_popup.py`) and are NOT replaced here;
//! `Reply.action` is retained for the callback-semantics phase.
//!
//! The measure callback is the frozen default `len(text) * size * .6` over
//! the flattened row map (id -1 = root title, -2 = empty submenu title,
//! kind 0 = label, 1 = shortcut), served from immutable thread-local/static
//! data so it can never panic or unwind across the C boundary.
//!
//! Provenance: `data/popup28_golden.provenance.json` (source commit
//! 50267dd6d4504b14052d1ab7e54675f3c8750637, extraction
//! 0ecb9c8330ee8a4c3c7a5db76f8e54679c28c231, binary SHA-256
//! 858391be1a25be7342f06df0bd06bb40d9d82db305a9b8c553f22df7e64e5259,
//! 6,012,434 bytes; 62 rows, 85 opens, 32 resizes, 900 events).

#[path = "support/popup28_fixture.rs"]
mod popup28_fixture;

#[path = "support/golden27_sha256.rs"]
mod golden27_sha256;

use popup28_fixture::Value;
use std::collections::BTreeSet;

// Pulls the Rlib into the link (the popup module is private; this is the
// public symbol used to force crate linkage, as in `editor27_golden.rs`).
use narwhal_ui_core::narwhal_editor_abi;

// ---- ABI 1 declarations, matching narwhal_ui_core.h exactly ----

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Request {
    op: u32,
    value: u32,
    index: i64,
    x: f64,
    y: f64,
    scale: f64,
    source: u64,
    root_count: u64,
    title: u64,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Reply {
    handle: u64,
    revision: u64,
    layout_revision: u64,
    panels: u64,
    action: i64,
    consumed: u64,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct PanelState {
    start: u64,
    count: u64,
    parent: i64,
    selected: i64,
    rect: [f64; 4],
    body: [f64; 4],
    up: [f64; 4],
    down: [f64; 4],
    offset: f64,
    max_scroll: f64,
    width: f64,
    scrolling: u64,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Row {
    child_start: u64,
    child_count: u64,
    separator: u64,
}

unsafe extern "C" {
    fn narwhal_popup_abi() -> u32;
    fn narwhal_popup_destroy(handle: u64) -> u32;
    fn narwhal_popup_apply(
        handle: u64,
        request: *const Request,
        request_bytes: usize,
        key: *const u8,
        key_len: usize,
        rows: *const Row,
        row_count: usize,
        measure: Option<unsafe extern "C" fn(i64, u32, f64, *mut f64) -> u32>,
        output: *mut Reply,
        output_bytes: usize,
    ) -> u32;
    fn narwhal_popup_panel(
        handle: u64,
        revision: u64,
        depth: usize,
        output: *mut PanelState,
        bytes: usize,
    ) -> u32;
    fn narwhal_popup_rows(
        handle: u64,
        revision: u64,
        depth: usize,
        start: usize,
        output: *mut [f64; 4],
        count: usize,
    ) -> u32;
    fn narwhal_popup_hit(
        handle: u64,
        revision: u64,
        x: f64,
        y: f64,
        depth: *mut i64,
        part: *mut i64,
    ) -> u32;
    fn narwhal_popup_guards(
        handle: u64,
        revision: u64,
        output: *mut u8,
        capacity: usize,
        written: *mut usize,
    ) -> u32;
}

// ---- corpus constants (from the provenance; hard, not advisory) ----

const EXPECTED_SOURCE_COMMIT: &str = "50267dd6d4504b14052d1ab7e54675f3c8750637";
const EXPECTED_STEPS: usize = 900;
const EXPECTED_ROWS: usize = 62;
const EXPECTED_OPENS: usize = 85;
const EXPECTED_RESIZES: usize = 32;
const TITLE: &str = "Root"; // the trace opens with title "Root"
const GUARD_CAPACITY: usize = 512 * 65; // native bound: 33,280 bytes always suffices

const BLOB: &[u8] = include_bytes!("data/popup28_golden.bin");
const PROVENANCE: &str = include_str!("data/popup28_golden.provenance.json");
const EXPECTED_BLOB_LENGTH: usize = 6012434;
const EXPECTED_BLOB_SHA256: &str =
    "858391be1a25be7342f06df0bd06bb40d9d82db305a9b8c553f22df7e64e5259";
const EXPECTED_GOLDEN_PYTHON_SHA256: &str =
    "60fd8ae173d0af62c8ec45d8ccc147004fb70f4e1d080c176e143e3277bdd0de";
const EXPECTED_EXTRACTION_REVISION: &str = "0ecb9c8330ee8a4c3c7a5db76f8e54679c28c231";

// ---- measure callback: frozen default over immutable static data ----
//
// The golden trace ran with NO measurer supplied, so the adapter's default
// `len(text) * size * .6` applies (corpus has zero non-ASCII strings, so
// byte length == Python `len`). Data is set once before any apply() and
// never mutated; the callback cannot panic (all branches return a status).

static MEASURE_TITLE: std::sync::OnceLock<String> = std::sync::OnceLock::new();
static MEASURE_LABELS: std::sync::OnceLock<Vec<String>> = std::sync::OnceLock::new();
static MEASURE_SHORTCUTS: std::sync::OnceLock<Vec<String>> = std::sync::OnceLock::new();

unsafe extern "C" fn measure_cb(id: i64, kind: u32, size: f64, width: *mut f64) -> u32 {
    if !size.is_finite() {
        return 1;
    }
    let title = match MEASURE_TITLE.get() {
        Some(t) => t,
        None => return 1,
    };
    let labels = match MEASURE_LABELS.get() {
        Some(l) => l,
        None => return 1,
    };
    let shortcuts = match MEASURE_SHORTCUTS.get() {
        Some(s) => s,
        None => return 1,
    };
    let text: &str = if id == -1 {
        title
    } else if id == -2 {
        ""
    } else if (0..labels.len() as i64).contains(&id) && (kind == 0 || kind == 1) {
        if kind == 0 {
            &labels[id as usize]
        } else {
            &shortcuts[id as usize]
        }
    } else {
        return 1;
    };
    // This original corpus is ASCII; byte length therefore equals Python len.
    // A future non-ASCII corpus must explicitly qualify character measurement.
    if !text.is_ascii() {
        return 1;
    }
    let w = text.len() as f64 * size * 0.6;
    if !w.is_finite() || w.abs() > 1e12 {
        return 1;
    }
    if width.is_null() {
        return 1;
    }
    width.write(w);
    0
}

// ---- small helpers over the decoded corpus ----

fn f64_of(v: &Value, what: &str) -> f64 {
    match v {
        Value::F64(f) => *f,
        Value::I128(i) => *i as f64,
        _ => panic!("expected number for {}", what),
    }
}

fn i64_of(v: &Value, what: &str) -> i64 {
    match v {
        Value::I128(i) => {
            i64::try_from(*i).unwrap_or_else(|_| panic!("out-of-range integer for {}", what))
        }
        Value::F64(f) => {
            if f.fract() != 0.0 {
                panic!("non-integral float for {}", what);
            }
            *f as i64
        }
        _ => panic!("expected integer for {}", what),
    }
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::I128(i) => *i != 0,
        _ => false,
    }
}

fn str_of(v: &Value, what: &str) -> String {
    match v {
        Value::Str(s) => s.clone(),
        _ => panic!("expected string for {}", what),
    }
}

/// `places=7` semantics: `assertAlmostEqual(x, y, places=7)` passes iff
/// `round(abs(x-y), 7) == 0`, i.e. `abs(x-y) < 5e-8`. Strict inequality:
/// a diff of exactly 5e-8 would round up to 5e-08 and FAIL, so it must fail
/// here too. Non-zero sub-bar diffs (0 < diff < 5e-8) pass; the harness
/// counts and reports them. They are genuine sub-bar differences, NOT
/// rounding-boundary ties (the observed max is 1.36e-12, far below 5e-8).
fn approx7(x: f64, y: f64, ctx: &mut Ctx, where_: String) {
    let d = (x - y).abs();
    if d >= 5e-8 {
        panic!("float mismatch at {where_}: {x} vs {y} (|diff| = {d})");
    }
    if d > 0.0 {
        ctx.nonzero_diffs += 1;
    }
    if d > ctx.max_diff {
        ctx.max_diff = d;
    }
}

struct Ctx {
    nonzero_diffs: usize,
    max_diff: f64,
}

/// Flatten the frozen row tree **exactly as `native_popup._tree` does**:
/// `visit()` first appends the whole sibling block, then — for each sibling
/// in order — appends its children block (recursively). Roots occupy
/// `[0, root_count)`; a parent's `child_start` is the index where its
/// children block begins (after the whole current block), so the ranges the
/// native `validate_rows` accepts (no overlap of sibling blocks) hold and the
/// measure row-ids match the golden capture.
#[derive(Clone, Copy, PartialEq)]
enum ActionValue {
    Int(i128),
    Str(&'static str),
}

#[derive(Clone)]
struct RowMeta {
    label: String,
    shortcut: String,
    enabled: bool,
    action: Option<ActionValue>,
}

fn action_of(v: &Value) -> Option<ActionValue> {
    match v {
        Value::I128(i) => Some(ActionValue::Int(*i)),
        Value::Str(s) => {
            // the corpus strings live in the static include_bytes blob; borrow
            // them as 'static for the test's lifetime (sound: the data never moves).
            Some(ActionValue::Str(unsafe {
                std::mem::transmute::<&str, &'static str>(s.as_str())
            }))
        }
        _ => None,
    }
}

fn visit(source: &[Value], out_rows: &mut Vec<Row>, out_meta: &mut Vec<RowMeta>) -> usize {
    let start = out_rows.len();
    for row in source {
        assert!(matches!(row, Value::Map(_)), "row entry is not a map");
        let label = match row.get_opt("label") {
            Some(v) => str_of(v, "row label"),
            None => String::new(),
        };
        if !label.is_ascii() {
            panic!("non-ASCII label would break the byte-length default measure");
        }
        let shortcut = match row.get_opt("shortcut") {
            Some(v) => str_of(v, "row shortcut"),
            None => String::new(),
        };
        if !shortcut.is_ascii() {
            panic!("non-ASCII shortcut would break the byte-length default measure");
        }
        out_rows.push(Row {
            child_start: 0,
            child_count: 0,
            separator: row.get_opt("separator").is_some_and(truthy) as u64,
        });
        let action = row.get_opt("action").and_then(action_of);
        out_meta.push(RowMeta {
            label,
            shortcut,
            enabled: row.get_opt("enabled").is_none_or(truthy),
            action,
        });
    }
    for (offset, row) in source.iter().enumerate() {
        if let Some(children) = row.get_opt("children") {
            let list = children
                .as_list()
                .unwrap_or_else(|| panic!("children is not an array"));
            let child_start = out_rows.len() as u64;
            let child_count = visit(list, out_rows, out_meta) as u64;
            let idx = start + offset;
            out_rows[idx].child_start = child_start;
            out_rows[idx].child_count = child_count;
        }
    }
    // The adapter's visit() returns the DIRECT sibling count (the block it
    // appended), not the subtree size; child ranges must not overlap.
    source.len()
}

// ---- ABI calls with the live adapter's reply invariants ----

/// Mirrors `NativePopup._apply`'s reply validation (native_popup.py):
/// nonzero handle, stable owner, positive bounded revisions, layout revision
/// monotone and within 3x the operation revision, panels within the depth
/// bound, consumed a bit, action a valid global row id that only events
/// produce (with the native stack closed), and strict revision advance.
fn check_reply(out: &Reply, op: u32, prev: Option<&Reply>, flat_len: usize, step: usize) {
    assert_ne!(out.handle, 0, "step {step}: zero handle");
    if let Some(p) = prev {
        assert_eq!(out.handle, p.handle, "step {step}: handle changed");
        assert!(
            out.revision > p.revision,
            "step {step}: operation revision did not advance ({} -> {})",
            p.revision,
            out.revision
        );
        assert!(
            out.layout_revision >= p.layout_revision,
            "step {step}: layout revision went backwards ({} -> {})",
            p.layout_revision,
            out.layout_revision
        );
    }
    assert_ne!(out.revision, 0, "step {step}: zero revision");
    assert!(
        0 < out.layout_revision && out.layout_revision <= out.revision * 3,
        "step {step}: layout_revision {} outside (0, 3*revision={}]",
        out.layout_revision,
        out.revision * 3
    );
    assert!(
        out.panels <= 64,
        "step {step}: {} panels exceeds 64",
        out.panels
    );
    assert!(
        out.consumed <= 1,
        "step {step}: consumed {} not 0/1",
        out.consumed
    );
    assert!(
        out.action >= -1,
        "step {step}: action {} below -1",
        out.action
    );
    if out.action >= 0 {
        assert_eq!(op, 2, "step {step}: action outside an event");
        assert!(
            (out.action as usize) < flat_len,
            "step {step}: action {} outside the {} uploaded rows",
            out.action,
            flat_len
        );
        assert_eq!(
            out.panels, 0,
            "step {step}: activation must leave the native stack closed"
        );
    }
}

fn read_guards(handle: u64, revision: u64, step: usize) -> Vec<String> {
    let mut buf = vec![0u8; GUARD_CAPACITY];
    let mut written: usize = 0;
    let rc = unsafe {
        narwhal_popup_guards(handle, revision, buf.as_mut_ptr(), buf.len(), &mut written)
    };
    assert_eq!(rc, 0, "step {step}: guards read rc={rc}");
    let s = std::str::from_utf8(&buf[..written]).expect("guards not valid UTF-8");
    if s.is_empty() {
        return Vec::new();
    }
    let body = s
        .strip_suffix('\0')
        .unwrap_or_else(|| panic!("step {step}: guards blob not NUL-terminated"));
    body.split('\0').map(str::to_string).collect()
}

fn expected_guards(state: &Value, step: usize) -> Vec<String> {
    let list = state
        .get("releases")
        .as_list()
        .unwrap_or_else(|| panic!("step {step}: state.releases missing"));
    let mut v: Vec<String> = list.iter().map(|x| str_of(x, "release key")).collect();
    v.sort();
    v
}

/// Compare one frozen panel projection against the live ABI reads.
/// `row_rects` comes from `narwhal_popup_rows`; `title` is the adapter's
/// depth-0-only projection (the golden value must agree with it).
#[allow(clippy::too_many_arguments)]
fn compare_panel(
    gp: &Value,
    handle: u64,
    revision: u64,
    depth: usize,
    rows: &[Row],
    meta: &[RowMeta],
    ctx: &mut Ctx,
    step: usize,
) {
    let where_ = |name: &str| format!("step {step} depth {depth} {name}");
    let mut ps = PanelState::default();
    let rc = unsafe {
        narwhal_popup_panel(
            handle,
            revision,
            depth,
            &mut ps,
            std::mem::size_of::<PanelState>(),
        )
    };
    assert_eq!(rc, 0, "step {step}: panel read depth {depth} rc={rc}");

    let start = ps.start as usize;
    let count = ps.count as usize;
    assert!(
        start + count <= meta.len(),
        "step {step} depth {depth}: panel range [{start}, +{count}) outside {} rows",
        meta.len()
    );

    // title: depth 0 carries the popup title, submenus the empty string.
    let expected_title = if depth == 0 { TITLE } else { "" };
    let golden_title = gp
        .get("title")
        .as_str()
        .unwrap_or_else(|| panic!("step {step} depth {depth}: panel.title missing"));
    assert_eq!(
        golden_title, expected_title,
        "step {step} depth {depth}: title"
    );

    // parent / selected: -1 encodes None in both the ABI and the golden.
    let gparent = match gp.get("parent_index") {
        Value::Null => -1i64,
        v => i64_of(v, "panel.parent_index"),
    };
    assert_eq!(ps.parent, gparent, "step {step} depth {depth}: parent");
    let gselected = match gp.get("selected") {
        Value::Null => -1i64,
        v => i64_of(v, "panel.selected"),
    };
    assert_eq!(
        ps.selected, gselected,
        "step {step} depth {depth}: selected"
    );

    // rows: the adapter projects the flat row dicts of this range
    // (native_popup._row_bindings over the flattened tree); the golden stores
    // those same dicts. The native projection checked here is separator +
    // label ONLY. `enabled`/`action`/`children`/`shortcut` are host-side row
    // metadata the ABI carries none of (POPUP.md: "No enabled/action field is
    // needed for the native activation token"); they are retained by the live
    // Python adapter and are NOT asserted by this Rust replay.
    let grows = gp
        .get("rows")
        .as_list()
        .unwrap_or_else(|| panic!("step {step} depth {depth}: panel.rows missing"));
    assert_eq!(grows.len(), count, "step {step} depth {depth}: row count");
    for (j, r) in grows.iter().enumerate() {
        let g = match r {
            Value::Map(_) => r,
            _ => panic!("step {step} depth {depth}: rows[{j}] is not a row dict"),
        };
        let is_sep = g.get_opt("separator").is_some_and(truthy);
        assert_eq!(
            is_sep,
            rows[start + j].separator != 0,
            "step {step} depth {depth}: rows[{j}] separator"
        );
        if is_sep {
            assert!(
                g.get_opt("label").is_none(),
                "step {step} depth {depth}: rows[{j}] separator has no label"
            );
            assert!(
                meta[start + j].label.is_empty(),
                "step {step} depth {depth}: rows[{j}] native label"
            );
        } else {
            let label = str_of(g.get("label"), "panel row label");
            assert_eq!(
                label,
                meta[start + j].label,
                "step {step} depth {depth}: rows[{j}] label"
            );
        }
        // `enabled`/`action`/`children` are host-side fields the ABI carries
        // none of (POPUP.md: "No enabled/action field is needed for the
        // native activation token"); the adapter passes the same dicts
        // through, so label + separator above is the native projection check.
    }

    // scalar floats.
    approx7(
        ps.offset,
        f64_of(gp.get("offset"), "panel.offset"),
        ctx,
        where_("offset"),
    );
    approx7(
        ps.max_scroll,
        f64_of(gp.get("max_scroll"), "panel.max_scroll"),
        ctx,
        where_("max_scroll"),
    );
    approx7(
        ps.width,
        f64_of(gp.get("width"), "panel.width"),
        ctx,
        where_("width"),
    );

    // rects: always present in the golden; up/down null exactly when not scrolling.
    let golden_rect = gp.get("rect");
    let gr = golden_rect.as_list().expect("panel.rect not an array");
    assert_eq!(gr.len(), 4, "step {step} depth {depth}: rect length");
    for (k, grv) in gr.iter().enumerate() {
        approx7(
            ps.rect[k],
            f64_of(grv, "panel.rect"),
            ctx,
            format!("step {step} depth {depth} rect[{k}]"),
        );
    }
    let gb = gp.get("body").as_list().expect("panel.body not an array");
    assert_eq!(gb.len(), 4, "step {step} depth {depth}: body length");
    for (k, gbv) in gb.iter().enumerate() {
        approx7(
            ps.body[k],
            f64_of(gbv, "panel.body"),
            ctx,
            format!("step {step} depth {depth} body[{k}]"),
        );
    }
    let up = gp.get("up");
    match up {
        Value::Null => assert_eq!(
            ps.scrolling, 0,
            "step {step} depth {depth}: up set but scrolling=0"
        ),
        arr => {
            let a = arr.as_list().expect("panel.up not an array");
            assert_eq!(
                ps.scrolling, 1,
                "step {step} depth {depth}: up present but scrolling=0"
            );
            assert_eq!(a.len(), 4, "step {step} depth {depth}: up length");
            for (k, av) in a.iter().enumerate() {
                approx7(
                    ps.up[k],
                    f64_of(av, "panel.up"),
                    ctx,
                    format!("step {step} depth {depth} up[{k}]"),
                );
            }
        }
    }
    let down = gp.get("down");
    match down {
        Value::Null => {}
        arr => {
            let a = arr.as_list().expect("panel.down not an array");
            assert_eq!(a.len(), 4, "step {step} depth {depth}: down length");
            for (k, av) in a.iter().enumerate() {
                approx7(
                    ps.down[k],
                    f64_of(av, "panel.down"),
                    ctx,
                    format!("step {step} depth {depth} down[{k}]"),
                );
            }
        }
    }

    // row rectangles: exact range read, one [x0, y0, x1, y1] per row.
    let grr = gp
        .get("row_rects")
        .as_list()
        .unwrap_or_else(|| panic!("step {step} depth {depth}: panel.row_rects missing"));
    assert_eq!(
        grr.len(),
        count,
        "step {step} depth {depth}: row_rects length"
    );
    let mut rects = vec![0f64; 4 * count];
    if count > 0 {
        let rc = unsafe {
            narwhal_popup_rows(
                handle,
                revision,
                depth,
                0,
                rects.as_mut_ptr() as *mut [f64; 4],
                count,
            )
        };
        assert_eq!(rc, 0, "step {step} depth {depth}: rows read rc={rc}");
    }
    for j in 0..count {
        let grj = grr[j]
            .as_list()
            .unwrap_or_else(|| panic!("step {step} depth {depth}: row_rects[{j}] not an array"));
        assert_eq!(
            grj.len(),
            4,
            "step {step} depth {depth}: row_rects[{j}] length"
        );
        for k in 0..4 {
            approx7(
                rects[4 * j + k],
                f64_of(&grj[k], "row_rects"),
                ctx,
                format!("step {step} depth {depth} row_rects[{j}][{k}]"),
            );
        }
    }
}

fn event_value(name: &str) -> u32 {
    match name {
        "NOTHING" => 0,
        "PRESS" => 1,
        "RELEASE" => 2,
        "CLICK" => 3,
        "DOUBLE_CLICK" => 4,
        _ => 5,
    }
}

fn apply_op0(w: f64, h: f64, scale: f64) -> (u64, Reply) {
    let req = Request {
        op: 0,
        x: w,
        y: h,
        scale,
        ..Default::default()
    };
    let mut out = Reply::default();
    let rc = unsafe {
        narwhal_popup_apply(
            0,
            &req as *const Request,
            std::mem::size_of::<Request>(),
            std::ptr::null(),
            0,
            std::ptr::null(),
            0,
            Some(measure_cb),
            &mut out,
            std::mem::size_of::<Reply>(),
        )
    };
    assert_eq!(rc, 0, "create rc={rc}");
    (out.handle, out)
}

fn apply_op1(
    handle: u64,
    anchor: (f64, f64),
    title: bool,
    rows: &[Row],
    root_count: usize,
) -> Reply {
    let req = Request {
        op: 1,
        x: anchor.0,
        y: anchor.1,
        root_count: root_count as u64,
        title: u64::from(title),
        ..Default::default()
    };
    let mut out = Reply::default();
    let rc = unsafe {
        narwhal_popup_apply(
            handle,
            &req as *const Request,
            std::mem::size_of::<Request>(),
            std::ptr::null(),
            0,
            rows.as_ptr(),
            rows.len(),
            Some(measure_cb),
            &mut out,
            std::mem::size_of::<Reply>(),
        )
    };
    assert_eq!(rc, 0, "open rc={rc}");
    out
}

fn apply_op2(handle: u64, key: &str, value: u32, x: f64, y: f64) -> Reply {
    let req = Request {
        op: 2,
        value,
        x,
        y,
        ..Default::default()
    };
    let kb = key.as_bytes();
    let mut out = Reply::default();
    let rc = unsafe {
        narwhal_popup_apply(
            handle,
            &req as *const Request,
            std::mem::size_of::<Request>(),
            kb.as_ptr(),
            kb.len(),
            std::ptr::null(),
            0,
            Some(measure_cb),
            &mut out,
            std::mem::size_of::<Reply>(),
        )
    };
    assert_eq!(rc, 0, "event rc={rc}");
    out
}

fn apply_op3(handle: u64, w: f64, h: f64) -> Reply {
    let req = Request {
        op: 3,
        x: w,
        y: h,
        ..Default::default()
    };
    let mut out = Reply::default();
    let rc = unsafe {
        narwhal_popup_apply(
            handle,
            &req as *const Request,
            std::mem::size_of::<Request>(),
            std::ptr::null(),
            0,
            std::ptr::null(),
            0,
            Some(measure_cb),
            &mut out,
            std::mem::size_of::<Reply>(),
        )
    };
    assert_eq!(rc, 0, "resize rc={rc}");
    out
}

/// Hard corpus guard (runs purely on the parsed value, BEFORE any FFI): the
/// declared step count (`top[1]`) and the actual number of steps
/// (`popup.steps.len()`) must both equal 900. A corpus truncated to 899 is
/// rejected here, so it can never be replayed through the ABI.
fn corpus_guard(top: &[Value]) -> (&Value, &[Value]) {
    assert_eq!(top.len(), 3, "top = [source_commit, 900, popup]");
    assert_eq!(
        str_of(&top[0], "source_commit"),
        EXPECTED_SOURCE_COMMIT,
        "source commit"
    );
    assert_eq!(
        i64_of(&top[1], "step count literal"),
        EXPECTED_STEPS as i64,
        "declared step count"
    );
    let popup = &top[2];
    let rows_v = popup.get("rows");
    let steps = popup
        .get("steps")
        .as_list()
        .expect("popup.steps is an array");
    assert_eq!(steps.len(), EXPECTED_STEPS, "900 steps, none omitted");
    (rows_v, steps)
}

#[test]
fn popup28_provenance_guard() {
    // Joint provenance guard: the exact binary identity (size + SHA-256) is
    // asserted independently, and every pinned identity in the provenance file
    // is present, so the corpus and its provenance cannot silently drift.
    assert_eq!(
        BLOB.len(),
        EXPECTED_BLOB_LENGTH,
        "golden binary length must stay 6,012,434 bytes"
    );
    assert_eq!(
        golden27_sha256::hash(BLOB),
        EXPECTED_BLOB_SHA256,
        "golden binary SHA-256"
    );
    for (what, needle) in [
        ("binary SHA-256", EXPECTED_BLOB_SHA256),
        ("golden Python SHA-256", EXPECTED_GOLDEN_PYTHON_SHA256),
        ("source commit", EXPECTED_SOURCE_COMMIT),
        ("extraction revision", EXPECTED_EXTRACTION_REVISION),
    ] {
        assert!(
            PROVENANCE.contains(needle),
            "provenance must pin the {what} ({needle})"
        );
    }
}

#[test]
#[should_panic(expected = "declared step count")]
fn popup28_truncation_guard() {
    // Truncate BOTH the declared count (top[1]) and the actual steps to 899,
    // then confirm the hard-900 guard rejects before any FFI. Reuses
    // `Value::clone` (no binary re-encoder).
    let top_val = popup28_fixture::parse(BLOB);
    let mut top = top_val.as_list().expect("top level is an array").to_vec();
    top[1] = Value::I128(899);
    let popup = match top.get_mut(2) {
        Some(Value::Map(pairs)) => pairs,
        _ => panic!("popup must be a map"),
    };
    for (k, v) in popup.iter_mut() {
        if k == "steps" {
            if let Value::Array(a) = v {
                a.truncate(899);
            }
        }
    }
    corpus_guard(&top);
}

#[test]
fn popup28_native_replay() {
    // ABI contract pinned before anything else.
    assert_eq!(narwhal_editor_abi(), 1, "editor ABI must stay 1");
    assert_eq!(unsafe { narwhal_popup_abi() }, 1, "popup ABI mismatch");
    assert_eq!(
        std::mem::size_of::<Request>(),
        64,
        "Request is 64 bytes (op,value,index,x,y,scale,source,root_count,title)"
    );
    assert_eq!(std::mem::size_of::<Reply>(), 48, "Reply is 48 bytes");
    assert_eq!(
        std::mem::size_of::<PanelState>(),
        192,
        "PanelState is 192 bytes"
    );
    assert_eq!(std::mem::size_of::<Row>(), 24, "Row is 24 bytes");
    assert_eq!(
        std::mem::size_of::<[f64; 4]>() as usize,
        32,
        "Rect is 32 bytes"
    );

    // Decode the frozen corpus, then hard-guard it (declared count AND actual
    // step count must both be 900) purely on the parsed value — before any FFI.
    let top_val = popup28_fixture::parse(BLOB);
    let top = top_val.as_list().expect("top level is an array");
    let (rows_v, steps) = corpus_guard(top);

    let mut rows = Vec::new();
    let mut meta = Vec::new();
    let root_count = visit(rows_v.as_list().expect("rows array"), &mut rows, &mut meta);
    assert_eq!(
        rows_v.as_list().expect("rows array").len(),
        EXPECTED_ROWS,
        "62 top-level rows"
    );
    assert_eq!(
        meta.len(),
        65,
        "65 flattened nodes (nested/deeper/leaf + nested/child + 60 others)"
    );
    assert!(root_count > 0 && root_count <= meta.len());
    let mut opens = 0usize;
    let mut resizes = 0usize;
    for step in steps {
        if step.get_opt("open").is_some() {
            opens += 1;
        }
        if step.get_opt("resize").is_some() {
            resizes += 1;
        }
    }
    assert_eq!(opens, EXPECTED_OPENS, "85 open steps");
    assert_eq!(resizes, EXPECTED_RESIZES, "32 resize steps");

    // Install the measure data (immutable from here on).
    MEASURE_TITLE
        .set(TITLE.to_string())
        .expect("measure title set twice");
    MEASURE_LABELS
        .set(meta.iter().map(|m| m.label.clone()).collect())
        .expect("measure labels set twice");
    MEASURE_SHORTCUTS
        .set(meta.iter().map(|m| m.shortcut.clone()).collect())
        .expect("measure shortcuts set twice");

    // op 0: create the closed owner. Window 1000x800, scale 0.85 — the
    // adapter constructor of the golden trace (NativePopup((1000, 800), .85)).
    let (handle, mut prev) = apply_op0(1000.0, 800.0, 0.85);
    assert_eq!(prev.handle, handle);
    assert_eq!(prev.revision, 1, "revisions begin at 1");
    assert_eq!(prev.layout_revision, 1, "layout revisions begin at 1");
    assert_eq!(prev.panels, 0, "owner starts closed");
    assert_eq!(prev.consumed, 0);
    assert_eq!(prev.action, -1);

    let mut ctx = Ctx {
        nonzero_diffs: 0,
        max_diff: 0.0,
    };
    let mut prev_calls = 0usize;
    let mut consumed_true = 0usize;
    let mut consumed_false = 0usize;
    let mut actions = 0usize;
    let mut closed_steps = 0usize;

    for (i, step) in steps.iter().enumerate() {
        // open (85 steps): anchor point + title "Root", re-uploading the tree.
        if let Some(open) = step.get_opt("open") {
            let xy = open
                .as_list()
                .unwrap_or_else(|| panic!("step {i}: open not [x, y]"));
            assert_eq!(xy.len(), 2, "step {i}: open length");
            let reply = apply_op1(
                handle,
                (f64_of(&xy[0], "open.x"), f64_of(&xy[1], "open.y")),
                true,
                &rows,
                root_count,
            );
            check_reply(&reply, 1, Some(&prev), meta.len(), i);
            prev = reply;
        }

        // resize (32 steps): new window dimensions.
        if let Some(resize) = step.get_opt("resize") {
            let wh = resize
                .as_list()
                .unwrap_or_else(|| panic!("step {i}: resize not [w, h]"));
            assert_eq!(wh.len(), 2, "step {i}: resize length");
            let reply = apply_op3(
                handle,
                f64_of(&wh[0], "resize.w"),
                f64_of(&wh[1], "resize.h"),
            );
            check_reply(&reply, 3, Some(&prev), meta.len(), i);
            prev = reply;
        }

        // event (all 900 steps): key, value enum, window mouse point.
        let ev = step
            .get("event")
            .as_list()
            .unwrap_or_else(|| panic!("step {i}: event missing"));
        assert_eq!(ev.len(), 3, "step {i}: event = [key, value, [x, y]]");
        let key = str_of(&ev[0], "event key");
        let value = event_value(&str_of(&ev[1], "event value"));
        let pt = ev[2]
            .as_list()
            .unwrap_or_else(|| panic!("step {i}: event point missing"));
        assert_eq!(pt.len(), 2, "step {i}: event point length");
        let x = f64_of(&pt[0], "event x");
        let y = f64_of(&pt[1], "event y");
        let reply = apply_op2(handle, &key, value, x, y);
        check_reply(&reply, 2, Some(&prev), meta.len(), i);

        // consumed: the golden bool per step.
        let want_consumed = step
            .get("consumed")
            .as_bool()
            .unwrap_or_else(|| panic!("step {i}: consumed missing"));
        assert_eq!(
            reply.consumed == 1,
            want_consumed,
            "step {i}: consumed (key={key} value={value})"
        );
        if want_consumed {
            consumed_true += 1;
        } else {
            consumed_false += 1;
        }

        // Reply.action vs the frozen cumulative callback log. The log grows
        // exactly when this step's activation fired; its new element is
        // [action_row_id, explanation=None] (available row).
        let calls = step
            .get("calls")
            .as_list()
            .unwrap_or_else(|| panic!("step {i}: calls missing"));
        let activated = calls.len() > prev_calls;
        if activated {
            actions += 1;
            assert!(reply.action >= 0, "step {i}: activation without a row id");
            let row_id = reply.action as usize;
            assert!(row_id < meta.len(), "step {i}: row id out of range");
            let row = &meta[row_id];
            let pair = calls
                .last()
                .expect("new callback entry")
                .as_list()
                .expect("callback pair");
            assert_eq!(pair.len(), 2, "step {i}: callback pair length");
            if row.enabled && row.action.is_some() {
                // available row: the callback gets its opaque action value and
                // no explanation.
                let golden_a = &pair[0];
                let golden_b = &pair[1];
                match row.action {
                    Some(ActionValue::Int(v)) => {
                        assert_eq!(golden_a.as_i128(), Some(v), "step {i}: action value")
                    }
                    Some(ActionValue::Str(t)) => {
                        assert_eq!(golden_a.as_str(), Some(t), "step {i}: action value")
                    }
                    None => unreachable!(),
                }
                assert!(
                    matches!(golden_b, Value::Null),
                    "step {i}: available row must carry no explanation"
                );
            } else {
                // disabled/no action: the callback gets None plus the
                // explanation the adapter composes.
                assert!(
                    matches!(pair[0], Value::Null),
                    "step {i}: unavailable row must carry a null action"
                );
                let want = format!("{}: not implemented yet", row.label);
                let got = pair[1]
                    .as_str()
                    .unwrap_or_else(|| panic!("step {i}: explanation"));
                assert_eq!(got, want, "step {i}: explanation");
            }
        } else {
            assert_eq!(
                reply.action, -1,
                "step {i}: action without a callback entry"
            );
        }
        prev_calls = calls.len();

        // state: is_open, releases (guards), panels (projections).
        let state = step.get("state");
        let is_open = state
            .get("is_open")
            .as_bool()
            .unwrap_or_else(|| panic!("step {i}: is_open missing"));
        assert_eq!(
            reply.panels > 0,
            is_open,
            "step {i}: is_open ({} panels)",
            reply.panels
        );
        if !is_open {
            closed_steps += 1;
        }

        let got_guards = read_guards(handle, reply.revision, i);
        let want_guards = expected_guards(state, i);
        assert_eq!(
            got_guards, want_guards,
            "step {i}: guards (key={key} value={value})"
        );
        // BTreeSet order is the native read order; the golden is sorted.
        let set: BTreeSet<&String> = got_guards.iter().collect();
        let mut want_sorted = want_guards.clone();
        want_sorted.sort();
        want_sorted.dedup();
        let want_set: BTreeSet<&String> = want_sorted.iter().collect();
        assert_eq!(set, want_set, "step {i}: guard set");

        let gpanels = state
            .get("panels")
            .as_list()
            .unwrap_or_else(|| panic!("step {i}: panels missing"));
        assert_eq!(gpanels.len() as u64, reply.panels, "step {i}: panel count");
        for (depth, gp) in gpanels.iter().enumerate() {
            compare_panel(gp, handle, reply.revision, depth, &rows, &meta, &mut ctx, i);
        }

        // hit at the event coordinates: null = outside; [d, null] border;
        // [d, "up"/"down"]; [d, row index].
        let gh = step.get("hit");
        let mut depth_out: i64 = -99;
        let mut part_out: i64 = -99;
        let rc = unsafe {
            narwhal_popup_hit(handle, reply.revision, x, y, &mut depth_out, &mut part_out)
        };
        assert_eq!(rc, 0, "step {i}: hit rc={rc}");
        match gh {
            Value::Null => {
                assert_eq!(depth_out, -1, "step {i}: hit outside");
            }
            Value::Array(h) => {
                assert_eq!(h.len(), 2, "step {i}: hit pair");
                let gd = i64_of(&h[0], "hit depth");
                assert_eq!(depth_out, gd, "step {i}: hit depth");
                let want_part: i64 = match &h[1] {
                    Value::Null => -1,
                    Value::Str(s) => match s.as_str() {
                        "up" => -2,
                        "down" => -3,
                        other => panic!("step {i}: unknown hit part {other}"),
                    },
                    Value::I128(v) => *v as i64,
                    _ => panic!("step {i}: hit part not int/str/null"),
                };
                assert_eq!(part_out, want_part, "step {i}: hit part");
            }
            _ => panic!("step {i}: hit not null/pair"),
        }

        prev = reply;
    }

    // Lifetime: explicit release, then stale.
    assert_eq!(unsafe { narwhal_popup_destroy(handle) }, 0, "destroy");
    assert_eq!(
        unsafe { narwhal_popup_destroy(handle) },
        2,
        "destroy again is stale"
    );

    eprintln!(
        "popup28 replay: 900 steps; consumed true={consumed_true} false={consumed_false}; \
         actions={actions}; closed-state steps={closed_steps}; opens=85 resizes=32; \
         non-zero float diffs (all < 5e-8, not rounding ties)={} max_diff={}",
        ctx.nonzero_diffs, ctx.max_diff
    );
}
