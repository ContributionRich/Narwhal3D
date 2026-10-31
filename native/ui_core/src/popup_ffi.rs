//! Additive popup ABI 1. Editor exports and State are deliberately independent.
use crate::popup::*;
use std::collections::BTreeMap;
use std::sync::{Mutex, OnceLock};

#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct Request {
    pub op: u32,
    pub value: u32,
    pub index: i64,
    pub x: f64,
    pub y: f64,
    pub scale: f64,
    pub source: u64,
    pub root_count: u64,
    pub title: u64,
}
#[repr(C)]
#[derive(Default, Clone, Copy, Debug)]
pub struct Reply {
    pub handle: u64,
    pub revision: u64,
    pub layout_revision: u64,
    pub panels: u64,
    pub action: i64,
    pub consumed: u64,
}
#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct PanelState {
    pub start: u64,
    pub count: u64,
    pub parent: i64,
    pub selected: i64,
    pub rect: Rect,
    pub body: Rect,
    pub up: Rect,
    pub down: Rect,
    pub offset: f64,
    pub max_scroll: f64,
    pub width: f64,
    pub scrolling: u64,
}
#[derive(Default)]
struct Registry {
    next: u64,
    entries: BTreeMap<u64, Option<Popup>>,
}
static REGISTRY: OnceLock<Mutex<Registry>> = OnceLock::new();
fn registry() -> &'static Mutex<Registry> {
    REGISTRY.get_or_init(Default::default)
}
fn reply(handle: u64, p: &Popup, out: Outcome) -> Reply {
    Reply {
        handle,
        revision: p.revision,
        layout_revision: p.layout_revision,
        panels: p.panels.len() as u64,
        action: out.action.map_or(-1, |i| i as i64),
        consumed: u64::from(out.consumed),
    }
}
#[no_mangle]
pub extern "C" fn narwhal_popup_abi() -> u32 {
    1
}

/// Fixed reply is required before mutation. 0=success, 1=argument, 2=stale,
/// 3=bound, 4=busy/poisoned, 5=measurement failure (terminal; do not replay).
/// No Rust lock is held during a host callback. The borrowed handle is busy.
#[no_mangle]
pub unsafe extern "C" fn narwhal_popup_apply(
    handle: u64,
    request: *const Request,
    request_bytes: usize,
    key: *const u8,
    key_len: usize,
    rows: *const Row,
    row_count: usize,
    measure: Option<Measure>,
    output: *mut Reply,
    output_bytes: usize,
) -> u32 {
    if request.is_null()
        || request_bytes != std::mem::size_of::<Request>()
        || output.is_null()
        || output_bytes != std::mem::size_of::<Reply>()
        || key_len > 64
        || row_count > MAX_ROWS
        || (key_len > 0 && key.is_null())
        || (row_count > 0 && rows.is_null())
    {
        return 1;
    }
    let r = *request;
    if r.op > 8
        || (!matches!(r.op, 2 | 8) && key_len != 0)
        || (r.op != 1 && row_count != 0)
        || (matches!(r.op, 1 | 2) && measure.is_none())
    {
        return 1;
    }
    let key = if key_len == 0 {
        ""
    } else {
        let Ok(s) = std::str::from_utf8(std::slice::from_raw_parts(key, key_len)) else {
            return 1;
        };
        if s.bytes()
            .any(|b| !b.is_ascii_uppercase() && !b.is_ascii_digit() && b != b'_')
        {
            return 1;
        }
        s
    };
    if matches!(r.op, 2 | 8) && key.is_empty() {
        return 1;
    }
    if matches!(r.op, 0 | 3) && (!dimension(r.x) || !dimension(r.y)) {
        return 1;
    }
    if r.op == 0 && (!dimension(r.scale) || r.scale > 1e6 || handle != 0) {
        return 1;
    }
    if matches!(r.op, 1 | 2)
        && (!r.x.is_finite() || !r.y.is_finite() || r.x.abs() > 1e12 || r.y.abs() > 1e12)
    {
        return 1;
    }
    if r.op == 2 && r.value > 5 {
        return 1;
    }
    let data = if row_count == 0 {
        &[]
    } else {
        std::slice::from_raw_parts(rows, row_count)
    };
    if r.op == 1
        && (r.title > 1
            || r.root_count > MAX_ROWS as u64
            || !validate_rows(data, r.root_count as usize))
    {
        return 1;
    }
    let Ok(mut registry) = registry().lock() else {
        return 4;
    };
    if r.op == 0 {
        if registry.next == u64::MAX || registry.entries.len() >= 65536 {
            return 3;
        }
        registry.next += 1;
        let id = registry.next;
        let p = Popup::new(r.x, r.y, r.scale);
        output.write(reply(id, &p, Outcome::default()));
        registry.entries.insert(id, Some(p));
        return 0;
    }
    let inherited = if r.op == 7 {
        match registry.entries.get(&r.source) {
            Some(Some(p)) => Some(p.guards.clone()),
            Some(None) => return 4,
            None => return 2,
        }
    } else {
        None
    };
    let Some(slot) = registry.entries.get_mut(&handle) else {
        return 2;
    };
    let Some(p) = slot.as_ref() else {
        return 4;
    };
    if p.revision == u64::MAX || p.layout_revision > u64::MAX - 1024 {
        return 3;
    }
    if (r.op == 8 || (r.op == 2 && matches!(r.value, 1 | 4)))
        && !p.guards.contains(key)
        && p.guards.len() >= MAX_GUARDS
    {
        return 3;
    }
    if r.op == 6
        && (r.value as usize >= p.panels.len()
            || r.index < -1
            || r.index >= p.panels[r.value as usize].count as i64)
    {
        return 1;
    }
    if let Some(g) = inherited.as_ref() {
        if p.guards.union(g).count() > MAX_GUARDS {
            return 3;
        }
    }
    let mut p = slot.take().unwrap();
    drop(registry);
    let mut out = Outcome::default();
    let result = match r.op {
        1 => p.open(
            data.to_vec(),
            r.root_count as usize,
            (r.x, r.y),
            r.title != 0,
            measure.unwrap(),
        ),
        2 => p
            .event(key, r.value, r.x, r.y, measure.unwrap())
            .map(|v| out = v),
        3 => {
            p.resize(r.x, r.y);
            Ok(())
        }
        4 => {
            p.close();
            Ok(())
        }
        5 => {
            p.layout();
            Ok(())
        }
        6 => {
            p.panels[r.value as usize].selected = (r.index >= 0).then_some(r.index as usize);
            Ok(())
        }
        7 => {
            p.guards.extend(inherited.unwrap());
            Ok(())
        }
        8 => {
            p.guards.insert(key.to_owned());
            Ok(())
        }
        _ => unreachable!(),
    };
    p.revision += 1;
    let state = reply(handle, &p, out);
    let Ok(mut registry) = self::registry().lock() else {
        return 4;
    };
    *registry.entries.get_mut(&handle).unwrap() = Some(p);
    match result {
        Ok(()) => {
            output.write(state);
            0
        }
        Err(rc) => rc,
    }
}

#[no_mangle]
pub extern "C" fn narwhal_popup_destroy(handle: u64) -> u32 {
    let Ok(mut r) = registry().lock() else {
        return 4;
    };
    match r.entries.get(&handle) {
        None => 2,
        Some(None) => 4,
        Some(Some(_)) => {
            r.entries.remove(&handle);
            0
        }
    }
}
fn read<T>(handle: u64, revision: u64, f: impl FnOnce(&Popup) -> Result<T, u32>) -> Result<T, u32> {
    let r = registry().lock().map_err(|_| 4u32)?;
    let p = r.entries.get(&handle).ok_or(2u32)?.as_ref().ok_or(4u32)?;
    if p.revision != revision {
        return Err(2);
    }
    f(p)
}
#[no_mangle]
pub unsafe extern "C" fn narwhal_popup_panel(
    handle: u64,
    revision: u64,
    depth: usize,
    output: *mut PanelState,
    bytes: usize,
) -> u32 {
    if output.is_null() || bytes != std::mem::size_of::<PanelState>() {
        return 1;
    }
    match read(handle, revision, |p| {
        let p = p.panels.get(depth).ok_or(3u32)?;
        Ok(PanelState {
            start: p.start as u64,
            count: p.count as u64,
            parent: p.parent.map_or(-1, |v| v as i64),
            selected: p.selected.map_or(-1, |v| v as i64),
            rect: p.rect,
            body: p.body,
            up: p.up.unwrap_or_default(),
            down: p.down.unwrap_or_default(),
            offset: p.offset,
            max_scroll: p.max_scroll,
            width: p.width,
            scrolling: u64::from(p.up.is_some()),
        })
    }) {
        Ok(v) => {
            output.write(v);
            0
        }
        Err(rc) => rc,
    }
}
#[no_mangle]
pub unsafe extern "C" fn narwhal_popup_rows(
    handle: u64,
    revision: u64,
    depth: usize,
    start: usize,
    output: *mut Rect,
    count: usize,
) -> u32 {
    if output.is_null() && count != 0 {
        return 1;
    }
    match read(handle, revision, |p| {
        let p = p.panels.get(depth).ok_or(3u32)?;
        if start > p.rows.len() || count > p.rows.len() - start {
            return Err(3);
        }
        if count != 0 {
            std::ptr::copy_nonoverlapping(p.rows.as_ptr().add(start), output, count);
        }
        Ok(())
    }) {
        Ok(()) => 0,
        Err(rc) => rc,
    }
}
#[no_mangle]
pub unsafe extern "C" fn narwhal_popup_hit(
    handle: u64,
    revision: u64,
    x: f64,
    y: f64,
    depth: *mut i64,
    part: *mut i64,
) -> u32 {
    if depth.is_null() || part.is_null() || !x.is_finite() || !y.is_finite() {
        return 1;
    }
    match read(handle, revision, |p| Ok(p.hit(x, y))) {
        Ok(hit) => {
            let (d, v) = hit.map_or((-1, -1), |(d, p)| {
                (
                    d as i64,
                    match p {
                        Part::Row(i) => i as i64,
                        Part::Up => -2,
                        Part::Down => -3,
                        Part::Border => -1,
                    },
                )
            });
            depth.write(d);
            part.write(v);
            0
        }
        Err(rc) => rc,
    }
}
#[no_mangle]
pub unsafe extern "C" fn narwhal_popup_guards(
    handle: u64,
    revision: u64,
    output: *mut u8,
    capacity: usize,
    written: *mut usize,
) -> u32 {
    if output.is_null() || written.is_null() {
        return 1;
    }
    match read(handle, revision, |p| {
        let size = p.guards.iter().map(|s| s.len() + 1).sum();
        if capacity < size {
            return Err(3);
        }
        let mut offset = 0;
        for s in &p.guards {
            std::ptr::copy_nonoverlapping(s.as_ptr(), output.add(offset), s.len());
            offset += s.len();
            output.add(offset).write(0);
            offset += 1;
        }
        written.write(size);
        Ok(())
    }) {
        Ok(()) => 0,
        Err(rc) => rc,
    }
}
