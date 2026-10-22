//! SPDX-License-Identifier: GPL-3.0-or-later
//!
//! Native macro execution planner (Q106-13). A faithful port of the pure-Python
//! `narwhal3d/macro_plan.py`: the same measured `NESTABLE_COMMANDS` allow-list,
//! the same transparent-command safety refusal, and the same explicit action
//! dictionaries (cancel / command / pause / enter / text / option).
//!
//! The Rust side tokenizes losslessly (reusing `macro_parse::tokenize_steps` —
//! the Q106-12 reuse point) and emits the action list as bounded JSON, handed
//! back with the crate's standard two-pass "size query + encode into a
//! caller-owned buffer" pattern. A transparent command outside the allow-list
//! is a REJECTION (`PLAN_ERR_NON_NESTABLE`), not an action: the Python hook
//! then runs its unchanged fallback and raises the exact
//! `NonNestableCommandError` (whose message embeds the command name), so the
//! rejection itself never crosses the ABI.
//!
//! Everything the string route can reach is mirrored here: for a valid macro
//! string the lossless tokenizer always emits well-formed steps, so the only
//! planner refusal reachable via a macro string is the transparent-command
//! one. The hand-built-token checks in `build_plan` (invalid command name,
//! bad pause/enter spelling, non-string values, unknown kinds) stay in Python.

use crate::macro_parse::{
    byte_span, flags_json, json_escape, tokenize_steps, Step, MACRO_MAX_INPUT_BYTES,
    PLAN_ERR_NON_NESTABLE,
};
use crate::macro_parse::{ERR_INPUT, ERR_NULL, ERR_SIZE};

/// True when the lower-cased command name is one the reference CAD application
/// documents as nestable (view / construction-plane / object-snap style).
/// MEASURED from `information/cad_scripting`, "Naming conventions".
fn is_nestable(name: &str) -> bool {
    matches!(
        name.to_lowercase().as_str(),
        "cplane"
            | "namedcplane"
            | "namedview"
            | "namedviews"
            | "ortho"
            | "planar"
            | "snap"
            | "osnap"
            | "zoom"
            | "rotateview"
            | "pan"
            | "tilt"
            | "walkabout"
            | "selcrossing"
            | "selwindow"
            | "selpolysrf"
            | "curvaturegraph"
            | "curvaturegraphoff"
            | "hidep"
            | "showp"
    )
}

/// Render one lossless step as its action JSON object (no surrounding array),
/// mirroring `macro_plan.build_plan` exactly: same keys, same order, and the
/// extra command flags present only when set.
fn action_json(step: &Step) -> Option<String> {
    match step {
        Step::Cancel => Some(r#"{"action":"cancel","prefixes":["!"]}"#.to_string()),
        Step::Command {
            name,
            dash,
            underscore,
            transparent,
            history_lock_off,
            repeat,
            suppress_options,
            history_on,
            history_off,
            prefixes,
        } => {
            // The safety boundary: a transparent command outside the measured
            // allow-list is refused, not planned (Python raises the exact
            // NonNestableCommandError on its fallback path).
            if *transparent && !is_nestable(name) {
                return None;
            }
            let mut s = String::from("{\"action\":\"command\",\"name\":");
            json_escape(name, &mut s);
            s.push_str(",\"prefixes\":");
            s.push_str(&flags_json(prefixes.as_deref().unwrap_or(&[])));
            s.push_str(",\"dash\":");
            s.push_str(if *dash { "true" } else { "false" });
            s.push_str(",\"underscore\":");
            s.push_str(if *underscore { "true" } else { "false" });
            s.push_str(",\"transparent\":");
            s.push_str(if *transparent { "true" } else { "false" });
            // Preserve every documented flag, present only when set.
            if *history_lock_off {
                s.push_str(",\"history_lock_off\":true");
            }
            if *repeat {
                s.push_str(",\"repeat\":true");
            }
            if *suppress_options {
                s.push_str(",\"suppress_options\":true");
            }
            if *history_on {
                s.push_str(",\"history_on\":true");
            }
            if *history_off {
                s.push_str(",\"history_off\":true");
            }
            s.push('}');
            Some(s)
        }
        Step::Pause { special, prefixes } => {
            // Lossless input always carries the original spelling.
            let special = special.as_deref().unwrap_or("Pause");
            let mut s = String::from("{\"action\":\"pause\",\"special\":");
            json_escape(special, &mut s);
            s.push_str(",\"prefixes\":");
            s.push_str(&flags_json(prefixes.as_deref().unwrap_or(&[])));
            s.push('}');
            Some(s)
        }
        Step::Enter { special, prefixes } => {
            let special = special.as_deref().unwrap_or("Enter");
            let mut s = String::from("{\"action\":\"enter\",\"special\":");
            json_escape(special, &mut s);
            s.push_str(",\"prefixes\":");
            s.push_str(&flags_json(prefixes.as_deref().unwrap_or(&[])));
            s.push('}');
            Some(s)
        }
        Step::Text { value } => {
            let mut s = String::from("{\"action\":\"text\",\"value\":");
            json_escape(value, &mut s);
            s.push('}');
            Some(s)
        }
        Step::Option {
            name,
            value,
            prefixes,
        } => {
            let mut s = String::from("{\"action\":\"option\",\"name\":");
            json_escape(name, &mut s);
            s.push_str(",\"value\":");
            json_escape(value, &mut s);
            s.push_str(",\"prefixes\":");
            s.push_str(&flags_json(prefixes.as_deref().unwrap_or(&[])));
            s.push('}');
            Some(s)
        }
    }
}

/// Build the bounded JSON action list for a lossless macro, or `None` when the
/// macro contains a transparent command outside the measured allow-list.
fn plan_json(macro_str: &str) -> Option<String> {
    let mut parts: Vec<String> = Vec::new();
    for step in tokenize_steps(macro_str, true) {
        parts.push(action_json(&step)?);
    }
    Some(format!("[{}]", parts.join(",")))
}

/// Packed action kinds for the one-pass planner ABI (Q117-05). The Python
/// converter maps them back to the exact `"cancel" / "command" / "pause" /
/// "enter" / "text" / "option"` action strings. The command flag bits are the
/// same `CMD_*` values the tokenizer packed route (Q117-04) publishes, so the
/// two routes stay structurally identical.
pub(crate) const ACT_CANCEL: u8 = 0;
pub(crate) const ACT_COMMAND: u8 = 1;
pub(crate) const ACT_PAUSE: u8 = 2;
pub(crate) const ACT_ENTER: u8 = 3;
pub(crate) const ACT_TEXT: u8 = 4;
pub(crate) const ACT_OPTION: u8 = 5;

/// The packed representation of one planned action: an action kind code, a
/// command flag bitmask, and three string fields (the same layout the
/// tokenizer packed route uses).
#[derive(Debug)]
struct PackedAction {
    kind: u8,
    flags: u8,
    s0: String,
    s1: String,
    s2: String,
}

/// Build the packed action list from the lossless steps, reusing the exact
/// same safety boundary and key set as the JSON route (so the two are
/// structurally identical). `None` when a transparent command is outside the
/// measured allow-list (the same refusal the JSON route makes).
fn pack_actions(steps: &[Step]) -> Option<Vec<PackedAction>> {
    use crate::macro_parse::{
        prefixes_string, CMD_DASH, CMD_HISTORY_LOCK_OFF, CMD_HISTORY_OFF, CMD_HISTORY_ON,
        CMD_REPEAT, CMD_SUPPRESS_OPTIONS, CMD_TRANSPARENT, CMD_UNDERSCORE,
    };
    let mut out: Vec<PackedAction> = Vec::with_capacity(steps.len());
    for s in steps {
        match s {
            Step::Cancel => out.push(PackedAction {
                kind: ACT_CANCEL,
                flags: 0,
                // The planner always emits `prefixes: ["!"]` on a cancel.
                s0: "!".to_string(),
                s1: String::new(),
                s2: String::new(),
            }),
            Step::Command {
                name,
                dash,
                underscore,
                transparent,
                history_lock_off,
                repeat,
                suppress_options,
                history_on,
                history_off,
                prefixes,
            } => {
                // The same safety boundary as the JSON route: a transparent
                // command outside the allow-list is refused, not planned.
                if *transparent && !is_nestable(name) {
                    return None;
                }
                let mut flags = 0u8;
                if *dash {
                    flags |= CMD_DASH;
                }
                if *underscore {
                    flags |= CMD_UNDERSCORE;
                }
                if *transparent {
                    flags |= CMD_TRANSPARENT;
                }
                if *history_lock_off {
                    flags |= CMD_HISTORY_LOCK_OFF;
                }
                if *repeat {
                    flags |= CMD_REPEAT;
                }
                if *suppress_options {
                    flags |= CMD_SUPPRESS_OPTIONS;
                }
                if *history_on {
                    flags |= CMD_HISTORY_ON;
                }
                if *history_off {
                    flags |= CMD_HISTORY_OFF;
                }
                out.push(PackedAction {
                    kind: ACT_COMMAND,
                    flags,
                    s0: name.clone(),
                    // The planner always emits `prefixes` on a command.
                    s1: prefixes_string(prefixes),
                    s2: String::new(),
                });
            }
            Step::Pause { special, prefixes } => {
                out.push(PackedAction {
                    kind: ACT_PAUSE,
                    flags: 0,
                    s0: special.clone().unwrap_or_else(|| "Pause".to_string()),
                    s1: prefixes_string(prefixes),
                    s2: String::new(),
                });
            }
            Step::Enter { special, prefixes } => {
                out.push(PackedAction {
                    kind: ACT_ENTER,
                    flags: 0,
                    s0: special.clone().unwrap_or_else(|| "Enter".to_string()),
                    s1: prefixes_string(prefixes),
                    s2: String::new(),
                });
            }
            Step::Text { value } => out.push(PackedAction {
                kind: ACT_TEXT,
                flags: 0,
                s0: value.clone(),
                s1: String::new(),
                s2: String::new(),
            }),
            Step::Option {
                name,
                value,
                prefixes,
            } => out.push(PackedAction {
                kind: ACT_OPTION,
                flags: 0,
                s0: name.clone(),
                s1: value.clone(),
                // The planner always emits `prefixes` on an option.
                s2: prefixes_string(prefixes),
            }),
        }
    }
    Some(out)
}

/// Plan a lossless macro into packed action buffers (Q117-05). Returns the
/// exact `(n_actions, string_bytes)` counts on success, or
/// `PLAN_ERR_NON_NESTABLE` when a transparent command is outside the measured
/// allow-list. All output is written to the caller-owned arrays; capacity is
/// validated **before any write** so a rejection never leaves partial output
/// behind.
///
/// # Safety
///
/// `kinds`/`flags`/`s0`/`s1`/`s2`/`strings` must each be writable for the
/// given capacity (null only when the capacity is zero).
#[allow(clippy::too_many_arguments)]
pub unsafe fn macro_plan_packed(
    text: *const u8,
    text_len: usize,
    kinds: *mut u8,
    kinds_cap: usize,
    flags: *mut u8,
    flags_cap: usize,
    s0: *mut u64,
    s0_cap: usize,
    s1: *mut u64,
    s1_cap: usize,
    s2: *mut u64,
    s2_cap: usize,
    strings: *mut u8,
    strings_cap: usize,
    out_n_actions: *mut i64,
    out_string_bytes: *mut i64,
) -> i32 {
    if text_len > MACRO_MAX_INPUT_BYTES {
        return ERR_INPUT;
    }
    // Count pointers are part of the atomic output contract. Reject them before
    // borrowing or writing any caller-owned output buffer.
    if out_n_actions.is_null() || out_string_bytes.is_null() {
        return ERR_NULL;
    }
    let slice = match byte_span(text, text_len) {
        Some(s) => s,
        None => return ERR_NULL,
    };
    let macro_str = match std::str::from_utf8(slice) {
        Ok(s) => s,
        Err(_) => return ERR_INPUT,
    };

    // Capacity bound (proved, arithmetic only -- no JSON, no size query):
    // the planner emits at most one action per lossless step (a step with a
    // leading `!` emits a Cancel + one more, but the `!` and the command's
    // first byte are two distinct input bytes, so the bound still holds), and
    // every step comes from a >=1-byte token, so `n_actions <= text_len`.
    // Each action's three string fields are subsets of its step's fields
    // (plus a cancel's single `!`), so the total string bytes across all
    // actions is at most the input bytes plus one per cancel. The `+ 1`
    // guards the integer bound for the empty/whitespace edge.
    let cap_actions = text_len.max(1);
    let cap_strings = text_len + 1;
    if kinds_cap < cap_actions
        || flags_cap < cap_actions
        || s0_cap < cap_actions
        || s1_cap < cap_actions
        || s2_cap < cap_actions
        || strings_cap < cap_strings
    {
        return ERR_SIZE;
    }
    if (kinds_cap > 0 && kinds.is_null())
        || (flags_cap > 0 && flags.is_null())
        || (s0_cap > 0 && s0.is_null())
        || (s1_cap > 0 && s1.is_null())
        || (s2_cap > 0 && s2.is_null())
        || (strings_cap > 0 && strings.is_null())
    {
        return ERR_NULL;
    }
    let kinds = match unsafe { crate::checked_slice_mut(kinds, kinds_cap) } {
        Some(k) => k,
        None => return ERR_SIZE,
    };
    let flags = match unsafe { crate::checked_slice_mut(flags, flags_cap) } {
        Some(f) => f,
        None => return ERR_SIZE,
    };
    let s0 = match unsafe { crate::checked_slice_mut(s0, s0_cap) } {
        Some(v) => v,
        None => return ERR_SIZE,
    };
    let s1 = match unsafe { crate::checked_slice_mut(s1, s1_cap) } {
        Some(v) => v,
        None => return ERR_SIZE,
    };
    let s2 = match unsafe { crate::checked_slice_mut(s2, s2_cap) } {
        Some(v) => v,
        None => return ERR_SIZE,
    };
    let strings = match unsafe { crate::checked_slice_mut(strings, strings_cap) } {
        Some(b) => b,
        None => return ERR_SIZE,
    };

    let steps = tokenize_steps(macro_str, true);
    let actions = match pack_actions(&steps) {
        Some(a) => a,
        None => return PLAN_ERR_NON_NESTABLE,
    };
    let total_strings: usize = actions
        .iter()
        .map(|p| p.s0.len() + p.s1.len() + p.s2.len())
        .sum();
    if actions.len() > kinds_cap || total_strings > strings_cap {
        return ERR_SIZE;
    }

    let mut si = 0usize;
    for (i, p) in actions.iter().enumerate() {
        kinds[i] = p.kind;
        flags[i] = p.flags;
        let o0 = si;
        strings[si..si + p.s0.len()].copy_from_slice(p.s0.as_bytes());
        si += p.s0.len();
        let o1 = si;
        strings[si..si + p.s1.len()].copy_from_slice(p.s1.as_bytes());
        si += p.s1.len();
        let o2 = si;
        strings[si..si + p.s2.len()].copy_from_slice(p.s2.as_bytes());
        si += p.s2.len();
        s0[i] = if p.s0.is_empty() {
            0
        } else {
            (o0 as u64) << 32 | (p.s0.len() as u64)
        };
        s1[i] = if p.s1.is_empty() {
            0
        } else {
            (o1 as u64) << 32 | (p.s1.len() as u64)
        };
        s2[i] = if p.s2.is_empty() {
            0
        } else {
            (o2 as u64) << 32 | (p.s2.len() as u64)
        };
    }

    unsafe {
        *out_n_actions = actions.len() as i64;
        *out_string_bytes = si as i64;
    }
    0
}

/// Packed planner ABI-1 export (Q117-05).
///
/// # Safety
///
/// See [`macro_plan_packed`] for the pointer contract.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_macro_plan_packed(
    text: *const u8,
    text_len: usize,
    kinds: *mut u8,
    kinds_cap: usize,
    flags: *mut u8,
    flags_cap: usize,
    s0: *mut u64,
    s0_cap: usize,
    s1: *mut u64,
    s1_cap: usize,
    s2: *mut u64,
    s2_cap: usize,
    strings: *mut u8,
    strings_cap: usize,
    out_n_actions: *mut i64,
    out_string_bytes: *mut i64,
) -> i32 {
    unsafe {
        macro_plan_packed(
            text,
            text_len,
            kinds,
            kinds_cap,
            flags,
            flags_cap,
            s0,
            s0_cap,
            s1,
            s1_cap,
            s2,
            s2_cap,
            strings,
            strings_cap,
            out_n_actions,
            out_string_bytes,
        )
    }
}

/// Validate the input and return the exact byte length of the planned action
/// JSON, or a negative stable error code (`PLAN_ERR_NON_NESTABLE` for a
/// transparent command outside the allow-list). No output.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when
/// `text_len == 0`).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_macro_plan_size(
    text: *const u8,
    text_len: usize,
) -> i64 {
    if text_len > MACRO_MAX_INPUT_BYTES {
        return ERR_INPUT as i64;
    }
    let slice = match byte_span(text, text_len) {
        Some(s) => s,
        None => return ERR_NULL as i64,
    };
    let macro_str = match std::str::from_utf8(slice) {
        Ok(s) => s,
        Err(_) => return ERR_INPUT as i64,
    };
    match plan_json(macro_str) {
        Some(json) => json.len() as i64,
        None => PLAN_ERR_NON_NESTABLE as i64,
    }
}

/// Encode the planned action list as bounded JSON into a caller-owned buffer.
///
/// `out` must hold exactly the bytes the encoder will produce (the same length
/// `narwhal_pure_core_macro_plan_size` returns). Returns `0` on success,
/// otherwise a negative stable error code.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when
/// `text_len == 0`) and `out` writable for the exact encoded length.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_macro_plan_encode(
    text: *const u8,
    text_len: usize,
    out: *mut u8,
    out_len: usize,
) -> i32 {
    if text_len > MACRO_MAX_INPUT_BYTES {
        return ERR_INPUT;
    }
    let slice = match byte_span(text, text_len) {
        Some(s) => s,
        None => return ERR_NULL,
    };
    let macro_str = match std::str::from_utf8(slice) {
        Ok(s) => s,
        Err(_) => return ERR_INPUT,
    };
    let json = match plan_json(macro_str) {
        Some(j) => j,
        None => return PLAN_ERR_NON_NESTABLE,
    };
    if out_len != json.len() {
        return ERR_SIZE;
    }
    if out.is_null() {
        return ERR_NULL;
    }
    let out = match unsafe { crate::checked_slice_mut(out, out_len) } {
        Some(out) => out,
        None => return ERR_SIZE,
    };
    out.copy_from_slice(json.as_bytes());
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(macro_str: &str) -> Option<String> {
        plan_json(macro_str)
    }

    /// One decoded packed action row: `(kind, flags, s0, s1, s2)`.
    type PackedRow = (u8, u8, String, String, String);

    /// Run the packed planner ABI with the exact capacities and decode the
    /// actions back into `(kind, flags, s0, s1, s2)` rows.
    fn run_packed(
        text: &str,
        kinds_cap: usize,
        strings_cap: usize,
    ) -> (i32, Vec<PackedRow>, i64, i64) {
        let bytes = text.as_bytes();
        let cap = kinds_cap;
        let mut kinds = vec![0u8; cap];
        let mut flags = vec![0u8; cap];
        let mut s0 = vec![0u64; cap];
        let mut s1 = vec![0u64; cap];
        let mut s2 = vec![0u64; cap];
        let mut strings = vec![0u8; strings_cap];
        let mut n_actions: i64 = -999;
        let mut n_bytes: i64 = -999;
        let rc = unsafe {
            macro_plan_packed(
                bytes.as_ptr(),
                bytes.len(),
                kinds.as_mut_ptr(),
                cap,
                flags.as_mut_ptr(),
                cap,
                s0.as_mut_ptr(),
                cap,
                s1.as_mut_ptr(),
                cap,
                s2.as_mut_ptr(),
                cap,
                strings.as_mut_ptr(),
                strings_cap,
                &mut n_actions,
                &mut n_bytes,
            )
        };
        let rows: Vec<PackedRow> = (0..n_actions.max(0) as usize)
            .map(|i| {
                let get = |v: &u64| {
                    if *v == 0 {
                        String::new()
                    } else {
                        let off = (*v >> 32) as usize;
                        let ln = (*v & 0xFFFFFFFF) as usize;
                        String::from_utf8(strings[off..off + ln].to_vec()).unwrap_or_default()
                    }
                };
                (kinds[i], flags[i], get(&s0[i]), get(&s1[i]), get(&s2[i]))
            })
            .collect();
        (rc, rows, n_actions, n_bytes)
    }

    /// Decode a planned JSON action into the same `(kind, flags, s0, s1, s2)`
    /// shape the packed route publishes, so the two can be compared directly.
    fn json_action_to_row(json: &str) -> PackedRow {
        // The planner JSON is a flat object of string/bool/number fields; the
        // hand-rolled JSON here is simple enough to parse by field name.
        let s = json.trim();
        let inner = s.trim_start_matches('[').trim_end_matches(']');
        let obj = inner.split('}').next().unwrap_or("");
        let field = |key: &str| -> String {
            let pat = format!("\"{}\":\"", key);
            if let Some(i) = obj.find(&pat) {
                let rest = &obj[i + pat.len()..];
                let end = rest.find('"').unwrap_or(0);
                rest[..end].to_string()
            } else {
                String::new()
            }
        };
        let has = |key: &str| obj.contains(&format!("\"{}\":true", key));
        let action = field("action");
        let kind = match action.as_str() {
            "cancel" => ACT_CANCEL,
            "command" => ACT_COMMAND,
            "pause" => ACT_PAUSE,
            "enter" => ACT_ENTER,
            "text" => ACT_TEXT,
            "option" => ACT_OPTION,
            _ => panic!("unknown action {action:?}"),
        };
        let mut flags = 0u8;
        if has("dash") {
            flags |= crate::macro_parse::CMD_DASH;
        }
        if has("underscore") {
            flags |= crate::macro_parse::CMD_UNDERSCORE;
        }
        if has("transparent") {
            flags |= crate::macro_parse::CMD_TRANSPARENT;
        }
        if has("history_lock_off") {
            flags |= crate::macro_parse::CMD_HISTORY_LOCK_OFF;
        }
        if has("repeat") {
            flags |= crate::macro_parse::CMD_REPEAT;
        }
        if has("suppress_options") {
            flags |= crate::macro_parse::CMD_SUPPRESS_OPTIONS;
        }
        if has("history_on") {
            flags |= crate::macro_parse::CMD_HISTORY_ON;
        }
        if has("history_off") {
            flags |= crate::macro_parse::CMD_HISTORY_OFF;
        }
        let s0 = if kind == ACT_COMMAND {
            field("name")
        } else if kind == ACT_PAUSE || kind == ACT_ENTER {
            field("special")
        } else if kind == ACT_TEXT {
            field("value")
        } else if kind == ACT_OPTION {
            field("name")
        } else {
            "!".to_string()
        };
        let s1 = if kind == ACT_COMMAND {
            // `prefixes` is a JSON array of single-char strings: ["'","-","_"].
            let pat = "\"prefixes\":[";
            if let Some(i) = obj.find(pat) {
                let rest = &obj[i + pat.len()..];
                let end = rest.find(']').unwrap_or(0);
                rest[..end]
                    .split(',')
                    .map(|c| c.trim_matches(|c| c == '"' || c == ' '))
                    .collect::<String>()
            } else {
                String::new()
            }
        } else if kind == ACT_PAUSE || kind == ACT_ENTER {
            let pat = "\"prefixes\":[";
            if let Some(i) = obj.find(pat) {
                let rest = &obj[i + pat.len()..];
                let end = rest.find(']').unwrap_or(0);
                rest[..end]
                    .split(',')
                    .map(|c| c.trim_matches(|c| c == '"' || c == ' '))
                    .collect::<String>()
            } else {
                String::new()
            }
        } else if kind == ACT_OPTION {
            field("value")
        } else {
            String::new()
        };
        let s2 = if kind == ACT_OPTION {
            let pat = "\"prefixes\":[";
            if let Some(i) = obj.find(pat) {
                let rest = &obj[i + pat.len()..];
                let end = rest.find(']').unwrap_or(0);
                rest[..end]
                    .split(',')
                    .map(|c| c.trim_matches(|c| c == '"' || c == ' '))
                    .collect::<String>()
            } else {
                String::new()
            }
        } else {
            String::new()
        };
        (kind, flags, s0, s1, s2)
    }

    #[test]
    fn packed_matches_the_json_route() {
        for m in [
            "! '-_Zoom",
            "!&_Delete",
            "_Pause _MultiPause _Enter _EnterEnd",
            "_-SelName \"Table 01\" Enter",
            "'_CPlane _3Point",
            "NoEcho '_Pan 5",
        ] {
            let (rc, rows, n_actions, _n_bytes) = run_packed(m, m.len() + 2, m.len() + 2);
            assert_eq!(rc, 0, "packed must succeed for {m:?}");
            let json = p(m).expect("the JSON route accepted it");
            let objs = json
                .trim_start_matches('[')
                .trim_end_matches(']')
                .split("},")
                .map(|o| o.trim_start_matches('{').trim_end_matches('}').to_string() + "}")
                .collect::<Vec<_>>();
            assert_eq!(n_actions as usize, objs.len(), "{m:?}");
            for (i, obj) in objs.iter().enumerate() {
                assert_eq!(rows[i], json_action_to_row(obj), "{m:?} action {i}");
            }
        }
    }

    #[test]
    fn packed_refuses_non_nestable_and_counts_untouched() {
        let (rc, _rows, n_actions, _n_bytes) = run_packed("'_Box", 10, 10);
        assert_eq!(rc, PLAN_ERR_NON_NESTABLE, "'_Box must be refused");
        assert_eq!(n_actions, -999, "a rejection must not publish a count");
        assert_eq!(
            unsafe { narwhal_pure_core_macro_plan_size("'_Box".as_ptr(), 5) },
            PLAN_ERR_NON_NESTABLE as i64,
            "the JSON size route reports the same rejection"
        );
    }

    #[test]
    fn packed_insufficient_capacity_rejects_and_counts_untouched() {
        let m = "!&_Delete";
        let (rc, _rows, n_actions, _n_bytes) = run_packed(m, 1, 1);
        assert_eq!(rc, ERR_SIZE, "a below-bound capacity must be rejected");
        assert_eq!(n_actions, -999, "a rejection must not publish a count");
    }

    #[test]
    fn packed_empty_plans_zero_and_oversized_is_refused() {
        // The empty macro plans to zero actions (all counts zero, no error).
        let (rc, rows, n_actions, n_bytes) = run_packed("", 1, 1);
        assert_eq!(rc, 0);
        assert_eq!(n_actions, 0);
        assert_eq!(n_bytes, 0);
        assert!(rows.is_empty());
        // An oversized input is refused before any buffer is touched.
        let big = vec![b'a'; MACRO_MAX_INPUT_BYTES + 1];
        let mut kinds = vec![0u8; 2];
        let mut flags = vec![0u8; 2];
        let mut s0 = vec![0u64; 2];
        let mut s1 = vec![0u64; 2];
        let mut s2 = vec![0u64; 2];
        let mut strings = vec![0u8; 2];
        let mut out_n = -999i64;
        let mut out_b = -999i64;
        let rc = unsafe {
            macro_plan_packed(
                big.as_ptr(),
                big.len(),
                kinds.as_mut_ptr(),
                2,
                flags.as_mut_ptr(),
                2,
                s0.as_mut_ptr(),
                2,
                s1.as_mut_ptr(),
                2,
                s2.as_mut_ptr(),
                2,
                strings.as_mut_ptr(),
                2,
                &mut out_n,
                &mut out_b,
            )
        };
        assert_eq!(rc, ERR_INPUT, "an oversized input must be refused");
        assert_eq!(out_n, -999, "no count is published on rejection");
    }

    #[test]
    fn packed_null_count_pointer_writes_nothing() {
        let text = b"_Line";
        let cap = text.len() + 1;
        let mut kinds = vec![0xA5u8; cap];
        let mut flags = vec![0xA5u8; cap];
        let mut s0 = vec![0xA5A5u64; cap];
        let mut s1 = vec![0xA5A5u64; cap];
        let mut s2 = vec![0xA5A5u64; cap];
        let mut strings = vec![0xA5u8; cap];
        let mut n_bytes = -1i64;
        let rc = unsafe {
            macro_plan_packed(
                text.as_ptr(),
                text.len(),
                kinds.as_mut_ptr(),
                cap,
                flags.as_mut_ptr(),
                cap,
                s0.as_mut_ptr(),
                cap,
                s1.as_mut_ptr(),
                cap,
                s2.as_mut_ptr(),
                cap,
                strings.as_mut_ptr(),
                cap,
                std::ptr::null_mut(),
                &mut n_bytes,
            )
        };
        assert_eq!(rc, ERR_NULL);
        assert!(kinds.iter().all(|v| *v == 0xA5));
        assert!(strings.iter().all(|v| *v == 0xA5));
        assert_eq!(n_bytes, -1);
    }

    #[test]
    fn cancel_prefixes_and_flags_are_preserved() {
        assert_eq!(
            p("! '-_Zoom"),
            Some(
                r#"[{"action":"cancel","prefixes":["!"]},{"action":"command","name":"Zoom","prefixes":["'","-","_"],"dash":true,"underscore":true,"transparent":true}]"#
                    .to_string()
            )
        );
        assert_eq!(
            p("!&_Delete"),
            Some(
                r#"[{"action":"cancel","prefixes":["!"]},{"action":"command","name":"Delete","prefixes":["!","&","_"],"dash":false,"underscore":true,"transparent":false,"history_lock_off":true}]"#
                    .to_string()
            )
        );
    }

    /// The mutation target: a transparent command outside the measured
    /// allow-list is refused. If `is_nestable` were loosened (or the
    /// transparency check dropped), `'_Box` would be planned and this
    /// fixture must go RED.
    #[test]
    fn transparent_geometry_command_is_refused() {
        assert_eq!(p("'_Box"), None);
        assert_eq!(p("'_Box _Pause"), None);
    }

    #[test]
    fn transparent_nestable_commands_are_accepted() {
        assert_eq!(
            p("'_Zoom '_CPlane"),
            Some(
                r#"[{"action":"command","name":"Zoom","prefixes":["'","_"],"dash":false,"underscore":true,"transparent":true},{"action":"command","name":"CPlane","prefixes":["'","_"],"dash":false,"underscore":true,"transparent":true}]"#
                    .to_string()
            )
        );
    }

    #[test]
    fn specials_text_option_and_flags_shapes() {
        assert_eq!(
            p("_Pause _MultiPause _Enter _EnterEnd"),
            Some(
                r#"[{"action":"pause","special":"Pause","prefixes":["_"]},{"action":"pause","special":"MultiPause","prefixes":["_"]},{"action":"enter","special":"Enter","prefixes":["_"]},{"action":"enter","special":"EnterEnd","prefixes":["_"]}]"#
                    .to_string()
            )
        );
        assert_eq!(
            p("_-_SetDisplayMode _Mode=_Shaded \"My Mode\""),
            Some(
                r#"[{"action":"command","name":"SetDisplayMode","prefixes":["_","-","_"],"dash":true,"underscore":true,"transparent":false},{"action":"option","name":"Mode","value":"Shaded","prefixes":["_"]},{"action":"text","value":"My Mode"}]"#
                    .to_string()
            )
        );
    }

    #[test]
    fn size_query_matches_encode_and_rejection() {
        // accepted macro
        let m = "'_Zoom _Mode=_Shaded 10";
        let bytes = m.as_bytes();
        let size = unsafe { narwhal_pure_core_macro_plan_size(bytes.as_ptr(), bytes.len()) };
        assert!(size > 0, "size must be positive for {m:?}");
        let mut buf = vec![0u8; size as usize];
        let rc = unsafe {
            narwhal_pure_core_macro_plan_encode(
                bytes.as_ptr(),
                bytes.len(),
                buf.as_mut_ptr(),
                buf.len(),
            )
        };
        assert_eq!(rc, 0, "encode must succeed for {m:?}");
        assert_eq!(
            std::str::from_utf8(&buf).expect("valid utf-8"),
            p(m).expect("plan_json accepted it")
        );
        // refused macro: both exports report the stable rejection code
        let bad = "'_Box".as_bytes();
        assert_eq!(
            unsafe { narwhal_pure_core_macro_plan_size(bad.as_ptr(), bad.len()) },
            PLAN_ERR_NON_NESTABLE as i64
        );
        let small = &mut [0u8; 3];
        assert_eq!(
            unsafe {
                narwhal_pure_core_macro_plan_encode(
                    bad.as_ptr(),
                    bad.len(),
                    small.as_mut_ptr(),
                    small.len(),
                )
            },
            PLAN_ERR_NON_NESTABLE
        );
    }

    #[test]
    fn rejects_null_non_utf8_and_oversized() {
        assert_eq!(
            unsafe { narwhal_pure_core_macro_plan_size(std::ptr::null(), 5) },
            ERR_NULL as i64
        );
        let bad = [0xFFu8, 0xFE, 0xFD];
        assert_eq!(
            unsafe { narwhal_pure_core_macro_plan_size(bad.as_ptr(), bad.len()) },
            ERR_INPUT as i64
        );
        let big = vec![b'a'; MACRO_MAX_INPUT_BYTES + 1];
        assert_eq!(
            unsafe { narwhal_pure_core_macro_plan_size(big.as_ptr(), big.len()) },
            ERR_INPUT as i64
        );
        let dangling = std::ptr::NonNull::<u8>::dangling().as_ptr();
        assert_eq!(
            unsafe { narwhal_pure_core_macro_plan_size(dangling, usize::MAX) },
            ERR_INPUT as i64
        );
        let mut out = [0xA5u8; 8];
        assert_eq!(
            unsafe {
                narwhal_pure_core_macro_plan_encode(
                    std::ptr::null(),
                    5,
                    out.as_mut_ptr(),
                    out.len(),
                )
            },
            ERR_NULL
        );
        assert_eq!(out, [0xA5; 8]);
    }
}
