//! Persistent code-point editor. See ../README.md for the ABI and ownership contract.
#[cfg(test)]
mod allocation_tests;
mod editor;
#[cfg(test)]
mod layout_algorithm;
#[cfg(test)]
mod layout_contract;
mod popup;
mod popup_ffi;
#[cfg(test)]
mod popup_tests;
mod unicode16;
use editor::TextField;
use std::collections::BTreeMap;
use std::sync::{Mutex, OnceLock};

const LIMIT: usize = 16 * 1024 * 1024;
#[repr(C)]
#[derive(Default, Clone, Copy, Debug)]
pub struct State {
    pub handle: u64,
    pub revision: u64,
    pub text_revision: u64,
    pub length: u64,
    pub caret: u64,
    pub anchor: i64,
    pub max_chars: i64,
    pub flags: u64,
    pub change_start: u64,
    pub change_end: u64,
    pub inserted: u64,
}
#[derive(Clone)]
struct Entry {
    field: TextField,
    revision: u64,
    text_revision: u64,
}
impl Entry {
    fn state(&self, handle: u64, result: bool) -> State {
        State {
            handle,
            revision: self.revision,
            text_revision: self.text_revision,
            length: self.field.text().len() as u64,
            caret: self.field.caret() as u64,
            anchor: self.field.anchor().map_or(-1, |v| v as i64),
            max_chars: self.field.max_chars.unwrap_or(0),
            flags: u64::from(self.field.multiline)
                | (u64::from(self.field.max_chars.is_some()) << 1)
                | (u64::from(result) << 2)
                | (u64::from(self.field.unicode16) << 3),
            change_start: 0,
            change_end: 0,
            inserted: 0,
        }
    }
}
#[derive(Default)]
struct Registry {
    next: u64,
    entries: BTreeMap<u64, Entry>,
}
static REGISTRY: OnceLock<Mutex<Registry>> = OnceLock::new();
fn registry() -> &'static Mutex<Registry> {
    REGISTRY.get_or_init(Default::default)
}

#[no_mangle]
pub extern "C" fn narwhal_editor_abi() -> u32 {
    1
}

/// Status: 0 success, 1 descriptor/argument, 2 stale handle/revision, 3 bound,
/// 4 poisoned registry. No mutation on a returned error.
///
/// # Safety
/// `output` must be aligned and writable for one `State` for the entire call.
/// For nonzero `length`, `input` must be aligned and readable for `length`
/// initialized `u32` values in a single allocation; it may be null for zero length.
/// The input and output ranges must not overlap. Neither may be concurrently
/// mutated, and the output must not be accessed by another thread during the call.
/// Null/size checks do not validate the provenance or capacity of nonnull pointers.
#[no_mangle]
pub unsafe extern "C" fn narwhal_editor_edit(
    handle: u64,
    op: u32,
    value: i64,
    flags: u32,
    input: *const u32,
    length: usize,
    output: *mut State,
    size: usize,
) -> u32 {
    if output.is_null()
        || size != std::mem::size_of::<State>()
        || length > LIMIT
        || (length != 0 && input.is_null())
        || op > 16
    {
        return 1;
    }
    // Reject unexpected payloads/flags before reading input or accessing a field.
    let mask = match op {
        0 => 11,
        5..=8 | 13 | 15 => 1,
        _ => 0,
    };
    if flags & !mask != 0 || (!matches!(op, 0..=2 | 11) && length != 0) {
        return 1;
    }
    let data = if length == 0 {
        &[]
    } else {
        std::slice::from_raw_parts(input, length)
    };
    if data.iter().any(|&c| c > 0x10ffff) {
        return 1;
    }
    let Ok(mut reg) = registry().lock() else {
        return 4;
    };
    if op == 0 || op == 10 {
        if op == 0 && handle != 0 {
            return 1;
        }
        let entry = if op == 10 {
            let Some(e) = reg.entries.get(&handle) else {
                return 2;
            };
            e.clone()
        } else {
            let mut field = TextField::new(&[], flags & 1 != 0, (flags & 2 != 0).then_some(value));
            field.unicode16 = flags & 8 != 0;
            field.set_text(data);
            Entry {
                field,
                revision: 1,
                text_revision: 1,
            }
        };
        let Some(id) = reg.next.checked_add(1) else {
            return 3;
        };
        if reg.entries.len() >= 65536 {
            return 3;
        }
        reg.next = id;
        let reply = entry.state(id, false);
        reg.entries.insert(id, entry);
        output.write(reply);
        return 0;
    }
    let Some(e) = reg.entries.get_mut(&handle) else {
        return 2;
    };
    if e.revision == u64::MAX || e.text_revision == u64::MAX {
        return 3;
    }
    let f = &mut e.field;
    if op == 2 && f.text().len().saturating_add(length) > LIMIT {
        return 3;
    }
    if op == 11 && (f.caret() > length || f.anchor().is_some_and(|a| a > length)) {
        return 1;
    }
    if (op == 12 || (op == 13 && flags == 0)) && (value < 0 || value as usize > f.text().len()) {
        return 1;
    }
    if op == 14 && !(0..=1).contains(&value) {
        return 1;
    }
    let old_len = f.text().len();
    let range = match op {
        1 | 11 => (0, old_len),
        2 | 3 | 4 | 16 => f.selection().unwrap_or_else(|| {
            let c = f.caret();
            match op {
                3 if c > 0 => (c - 1, c),
                4 if c < old_len => (c, c + 1),
                _ => (c, c),
            }
        }),
        _ => (0, 0),
    };
    let mut result = false;
    match op {
        1 => f.set_text(data),
        2 => result = f.insert(data),
        3 => f.backspace(),
        4 => f.delete(),
        5 => f.set_caret(value, flags != 0),
        6 => f.r#move(value, flags != 0),
        7 => f.home(flags != 0),
        8 => f.end(flags != 0),
        9 => f.select_all(),
        11 => {
            assert!(f.assign_text(data));
        }
        12 => f.assign_caret(value as usize),
        13 => f.assign_anchor(if flags != 0 {
            None
        } else {
            Some(value as usize)
        }),
        14 => f.multiline = value != 0,
        15 => f.max_chars = if flags != 0 { None } else { Some(value) },
        16 => result = f.delete_selection(),
        _ => return 1,
    }
    // Text revision changes only on potential content changes; movement never scans text.
    let changed = matches!(op, 1 | 11) || (op == 2 && result) || old_len != f.text().len();
    if changed {
        e.text_revision += 1;
    }
    e.revision += 1;
    let mut reply = e.state(handle, result);
    if changed {
        reply.change_start = range.0 as u64;
        reply.change_end = range.1 as u64;
        reply.inserted = reply.length - (old_len - (range.1 - range.0)) as u64;
    }
    output.write(reply);
    0
}

#[no_mangle]
pub extern "C" fn narwhal_editor_close(handle: u64) -> u32 {
    let Ok(mut r) = registry().lock() else {
        return 4;
    };
    if r.entries.remove(&handle).is_some() {
        0
    } else {
        2
    }
}

/// Read-only, exact revision/length. Never negotiate capacity by repeating an edit.
///
/// # Safety
/// `output` must be nonnull and aligned even when `capacity` is zero. Otherwise it
/// must be writable for `capacity` contiguous `u32` values in a single allocation,
/// with exclusive access for the entire call. The range must not overlap the
/// editor's stored text (no pointer to that storage is exported by this ABI).
#[no_mangle]
pub unsafe extern "C" fn narwhal_editor_read(
    handle: u64,
    revision: u64,
    start: usize,
    output: *mut u32,
    capacity: usize,
) -> u32 {
    if output.is_null() || capacity > LIMIT {
        return 1;
    }
    let Ok(r) = registry().lock() else {
        return 4;
    };
    let Some(e) = r.entries.get(&handle) else {
        return 2;
    };
    if revision != e.text_revision {
        return 2;
    }
    if start > e.field.text().len() || capacity > e.field.text().len() - start {
        return 3;
    }
    std::ptr::copy_nonoverlapping(e.field.text().as_ptr().add(start), output, capacity);
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    fn edit(h: u64, op: u32, value: i64, flags: u32, text: &[u32]) -> State {
        let mut s = State::default();
        assert_eq!(
            unsafe {
                narwhal_editor_edit(h, op, value, flags, text.as_ptr(), text.len(), &mut s, 88)
            },
            0
        );
        s
    }
    #[test]
    fn cut_reference_matches_read_then_delete() {
        let text = [65, 0x1f600, 66, 67];
        for (caret, anchor) in [(1, None), (1, Some(1)), (1, Some(3)), (3, Some(1))] {
            let mut reference = TextField::new(&text, false, None);
            let mut state = edit(0, 0, 0, 0, &text);
            if let Some(anchor) = anchor {
                reference.set_caret(anchor, false);
                edit(state.handle, 5, anchor, 0, &[]);
            }
            reference.set_caret(caret, anchor.is_some());
            state = edit(state.handle, 5, caret, u32::from(anchor.is_some()), &[]);
            let expected: &[u32] = if anchor.is_some_and(|a| a != caret) {
                &[0x1f600, 66]
            } else {
                &[]
            };
            assert_eq!(reference.selected_text(), expected);
            let mut selected = vec![0; reference.selected_text().len()];
            let start = reference.selection().map_or(caret as usize, |s| s.0);
            assert_eq!(
                unsafe {
                    narwhal_editor_read(
                        state.handle,
                        state.text_revision,
                        start,
                        selected.as_mut_ptr(),
                        selected.len(),
                    )
                },
                0
            );
            assert_eq!(selected, reference.cut());
            if !selected.is_empty() {
                state = edit(state.handle, 16, 0, 0, &[]);
            }
            let mut remaining = vec![0; state.length as usize];
            assert_eq!(
                unsafe {
                    narwhal_editor_read(
                        state.handle,
                        state.text_revision,
                        0,
                        remaining.as_mut_ptr(),
                        remaining.len(),
                    )
                },
                0
            );
            assert_eq!(remaining, reference.text());
            assert_eq!(state.caret, reference.caret() as u64);
            assert_eq!(state.anchor, reference.anchor().map_or(-1, |a| a as i64));
            assert_eq!(narwhal_editor_close(state.handle), 0);
        }
    }
    #[test]
    fn handles_and_capacity_are_atomic() {
        let s = edit(0, 0, 0, 0, &[65, 66]);
        let mut out = State::default();
        assert_eq!(
            unsafe { narwhal_editor_edit(s.handle, 2, 0, 0, [67].as_ptr(), 1, &mut out, 63) },
            1
        );
        let moved = edit(s.handle, 5, 0, 0, &[]);
        assert_eq!((moved.length, moved.text_revision), (2, s.text_revision));
        let copy = edit(s.handle, 10, 0, 0, &[]);
        assert_ne!(copy.handle, s.handle);
        assert_eq!(edit(copy.handle, 2, 0, 0, &[88]).length, 3);
        assert_eq!(edit(s.handle, 9, 0, 0, &[]).length, 2);
        assert_eq!(narwhal_editor_close(s.handle), 0);
        assert_eq!(narwhal_editor_close(s.handle), 2);
        assert_eq!(unsafe { narwhal_editor_read(s.handle, 1, 0, &mut 0, 0) }, 2);
        assert_eq!(narwhal_editor_close(copy.handle), 0);
    }
    #[test]
    fn rejects_bad_arguments_without_mutation() {
        let s = edit(0, 0, 0, 0, &[65]);
        let mut out = State::default();
        for (op, v, flags, text) in [
            (12, -1, 0, vec![]),
            (13, 8, 0, vec![]),
            (14, 2, 0, vec![]),
            (2, 0, 0, vec![0x110000]),
            (11, 0, 0, vec![]),
            (6, 0, 2, vec![]),
        ] {
            assert_eq!(
                unsafe {
                    narwhal_editor_edit(
                        s.handle,
                        op,
                        v,
                        flags,
                        text.as_ptr(),
                        text.len(),
                        &mut out,
                        88,
                    )
                },
                1
            );
        }
        let mut cp = 0;
        assert_eq!(
            unsafe { narwhal_editor_read(s.handle, s.text_revision, 0, &mut cp, 1) },
            0
        );
        assert_eq!(cp, 65);
        assert_eq!(
            unsafe { narwhal_editor_read(s.handle, s.text_revision + 1, 0, &mut cp, 1) },
            2
        );
        narwhal_editor_close(s.handle);
    }
}
