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

/// Stable planner rejection codes (negative) for the checked single-pass ABI.
/// `-4` is the existing `PLAN_ERR_NON_NESTABLE`; `-5` is the new code for an
/// option step whose name is empty (the Python `MacroPlanError('option action
/// has an invalid name')`).
pub(crate) const PLAN_ERR_INVALID_OPTION: i32 = -5;

/// The two semantic planner refusals the checked ABI can return. The first
/// error in token order wins, so a macro with an empty option *before* a
/// non-nestable command reports the empty option, not the command.
#[derive(Debug)]
pub(crate) enum PlannerError {
    /// A transparent command outside the measured nestable allow-list.
    /// The payload is the exact command name (the Python
    /// `NonNestableCommandError` message embeds it).
    NonNestable(String),
    /// An option step whose name is empty.
    InvalidOptionName,
}

impl PlannerError {
    /// The stable negative code for this error (the checked ABI return).
    pub(crate) fn code(&self) -> i32 {
        match self {
            PlannerError::NonNestable(_) => PLAN_ERR_NON_NESTABLE,
            PlannerError::InvalidOptionName => PLAN_ERR_INVALID_OPTION,
        }
    }
}

/// Case-fold `c` to the same string Python's `str.casefold()` produces for it.
///
/// The live Python planner (`macro_plan.py`) uses `name.casefold()` to test
/// against the nestable allow-list, and CPython's casefold does a small set of
/// one-to-many mappings that Rust's `char::to_lowercase` does NOT do:
///
/// | char | casefold | to_lowercase |
/// |------|----------|--------------|
/// | U+017F `ſ` (long s)   | `s`      | `ſ` (identity) |
/// | U+00DF `ß` (sharp s)  | `ss`     | `ß` (identity) |
/// | U+1E9E `ẞ` (caps sharp s)| `ss`    | `ß` |
/// | U+FB00 `ﬀ`            | `ff`     | `ﬀ` (identity) |
/// | U+FB01 `ﬁ`            | `fi`     | `ﬁ` (identity) |
/// | U+FB02 `ﬂ`            | `fl`     | `ﬂ` (identity) |
/// | U+FB03 `ﬃ`            | `ffi`    | `ﬃ` (identity) |
/// | U+FB04 `ﬄ`            | `ffl`    | `ﬄ` (identity) |
/// | U+FB05 `ﬅ`            | `st`     | `ﬅ` (identity) |
/// | U+FB06 `ﬆ`            | `st`     | `ﬆ` (identity) |
///
/// The Kelvin sign (U+212A) already folds to `k` via `to_lowercase`,
/// so it needs no special case. Unknown non-ASCII characters remain mapped
/// through `to_lowercase`, so they are still refused when they do not land on
/// an allow-list name.
fn casefold_char(c: char) -> String {
    match c {
        '\u{017F}' => "s".to_string(),
        '\u{00DF}' | '\u{1E9E}' => "ss".to_string(),
        '\u{FB00}' => "ff".to_string(),
        '\u{FB01}' => "fi".to_string(),
        '\u{FB02}' => "fl".to_string(),
        '\u{FB03}' => "ffi".to_string(),
        '\u{FB04}' => "ffl".to_string(),
        '\u{FB05}' | '\u{FB06}' => "st".to_string(),
        c => c.to_lowercase().to_string(),
    }
}

/// Case-fold `s` the way CPython's `str.casefold()` does (the one-to-many
/// mappings above plus `to_lowercase` for everything else).
fn casefold(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        out.push_str(&casefold_char(c));
    }
    out
}

/// True when the case-folded command name is one the reference CAD application
/// documents as nestable (view / construction-plane / object-snap style).
/// MEASURED from `information/cad_scripting`, "Naming conventions".
///
/// The live Python planner uses `name.casefold()`, so this mirrors it exactly
/// (see [`casefold_char`] for the one-to-many mappings Rust's
/// `to_lowercase` would miss).
fn is_nestable(name: &str) -> bool {
    matches!(
        casefold(name).as_str(),
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
///
/// Returns `Err(PlannerError)` when the step is one the planner must refuse:
/// a transparent command outside the measured allow-list, or an option step
/// whose name is empty. The caller (or the checked ABI) reports the first such
/// error in token order.
fn action_json(step: &Step) -> Result<String, PlannerError> {
    match step {
        Step::Cancel => Ok(r#"{"action":"cancel","prefixes":["!"]}"#.to_string()),
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
                return Err(PlannerError::NonNestable(name.clone()));
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
            Ok(s)
        }
        Step::Pause { special, prefixes } => {
            // Lossless input always carries the original spelling.
            let special = special.as_deref().unwrap_or("Pause");
            let mut s = String::from("{\"action\":\"pause\",\"special\":");
            json_escape(special, &mut s);
            s.push_str(",\"prefixes\":");
            s.push_str(&flags_json(prefixes.as_deref().unwrap_or(&[])));
            s.push('}');
            Ok(s)
        }
        Step::Enter { special, prefixes } => {
            let special = special.as_deref().unwrap_or("Enter");
            let mut s = String::from("{\"action\":\"enter\",\"special\":");
            json_escape(special, &mut s);
            s.push_str(",\"prefixes\":");
            s.push_str(&flags_json(prefixes.as_deref().unwrap_or(&[])));
            s.push('}');
            Ok(s)
        }
        Step::Text { value } => {
            let mut s = String::from("{\"action\":\"text\",\"value\":");
            json_escape(value, &mut s);
            s.push('}');
            Ok(s)
        }
        Step::Option {
            name,
            value,
            prefixes,
        } => {
            // The live Python planner (`macro_plan.build_plan`) refuses an
            // option whose name is empty; the old Rust `action_json` erroneously
            // accepted it. Report the exact stable error code so the checked
            // ABI can classify it (`-5`), distinct from a non-nestable command
            // (`-4`).
            if name.is_empty() {
                return Err(PlannerError::InvalidOptionName);
            }
            let mut s = String::from("{\"action\":\"option\",\"name\":");
            json_escape(name, &mut s);
            s.push_str(",\"value\":");
            json_escape(value, &mut s);
            s.push_str(",\"prefixes\":");
            s.push_str(&flags_json(prefixes.as_deref().unwrap_or(&[])));
            s.push('}');
            Ok(s)
        }
    }
}

/// Build the bounded JSON action list for a lossless macro, or the first
/// semantic error in token order (an empty option name or a transparent
/// command outside the measured allow-list).
fn plan_json(macro_str: &str) -> Result<String, PlannerError> {
    let steps = tokenize_steps(macro_str, true);
    let mut parts: Vec<String> = Vec::with_capacity(steps.len());
    for step in &steps {
        parts.push(action_json(step)?);
    }
    Ok(format!("[{}]", parts.join(",")))
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
            } => {
                if name.is_empty() {
                    return None;
                }
                out.push(PackedAction {
                    kind: ACT_OPTION,
                    flags: 0,
                    s0: name.clone(),
                    s1: value.clone(),
                    // The planner always emits `prefixes` on an option.
                    s2: prefixes_string(prefixes),
                });
            }
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
        Ok(json) => json.len() as i64,
        Err(e) => e.code() as i64,
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
        Ok(j) => j,
        Err(e) => return e.code(),
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

/// Checked single-pass planner ABI (RUST-PLAN-12). The production replacement
/// for the two-pass `size` + `encode` route: one call plans the macro and
/// classifies the FIRST semantic error in token order, so the host no longer
/// replays the Python tokenizer/planner on a rejection.
///
/// Status (return value):
/// * `0`  -- success. `out` holds the exact action-list JSON and `*out_needed`
///   is its byte length.
/// * `1`  -- short buffer. The output buffer is too small to hold the result
///   (the success JSON, or the non-nestable command-name payload). No
///   output is written; `*out_needed` carries the required length so the
///   caller can retry with a large enough buffer.
/// * `-1` -- bad span (a required pointer is null/misaligned for the given
///   length). No output is written.
/// * `-2` -- invalid input (oversized, or not valid UTF-8). No output written.
/// * `-4` -- non-nestable command (a transparent command outside the measured
///   allow-list). `out` holds the command name as a JSON string (via
///   the shared `json_escape`); `*out_needed` is its length.
/// * `-5` -- invalid option name (an option step with an empty name). No
///   output is written.
///
/// A capacity-0 + null-output call is a pure size query: it returns `1` (or the
/// stable error code) with `*out_needed` set and writes nothing, so the caller
/// can safely two-pass without a separate size export.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when `text_len ==
/// 0`). `out` must be writable for `out_len` bytes (null only when `out_len
/// == 0`). `out_needed` must be a valid pointer. No pointer is retained after
/// the call returns.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_macro_plan_checked(
    text: *const u8,
    text_len: usize,
    out: *mut u8,
    out_len: usize,
    out_needed: *mut i64,
) -> i32 {
    // `out_needed` is part of the atomic output contract; a null pointer is a
    // bad span before anything else is inspected.
    let Some(needed) = (unsafe { crate::checked_slice_mut(out_needed, 1) }) else {
        return ERR_NULL;
    };
    needed[0] = 0;
    if out_len > 64 * 1024 * 1024 {
        return ERR_INPUT;
    }
    // Oversized input is refused before reading any bytes.
    if text_len > MACRO_MAX_INPUT_BYTES {
        needed[0] = 0;
        return ERR_INPUT;
    }
    let slice = match byte_span(text, text_len) {
        Some(s) => s,
        None => {
            needed[0] = 0;
            return ERR_NULL;
        }
    };
    let macro_str = match std::str::from_utf8(slice) {
        Ok(s) => s,
        Err(_) => {
            needed[0] = 0;
            return ERR_INPUT;
        }
    };
    match plan_json(macro_str) {
        Ok(json) => write_checked_result(out, out_len, out_needed, json.as_bytes(), 0),
        Err(PlannerError::NonNestable(name)) => {
            let mut payload = String::new();
            json_escape(&name, &mut payload);
            write_checked_result(
                out,
                out_len,
                out_needed,
                payload.as_bytes(),
                PLAN_ERR_NON_NESTABLE,
            )
        }
        Err(PlannerError::InvalidOptionName) => {
            needed[0] = 0;
            PLAN_ERR_INVALID_OPTION
        }
    }
}

/// Shared tail for the checked ABI: publish the byte result into the caller
/// buffer, or report a short buffer / bad span without writing. Returns the
/// status code to hand back.
///
/// # Safety
///
/// `out` must be writable for `out_len` bytes when `out_len > 0` and `out`
/// non-null; `out_needed` must be a valid pointer.
unsafe fn write_checked_result(
    out: *mut u8,
    out_len: usize,
    out_needed: *mut i64,
    payload: &[u8],
    success_code: i32,
) -> i32 {
    let Some(required) = (unsafe { crate::checked_slice_mut(out_needed, 1) }) else {
        return ERR_NULL;
    };
    if payload.len() > 64 * 1024 * 1024 || out_len > 64 * 1024 * 1024 {
        required[0] = 0;
        return ERR_INPUT;
    }
    let needed = payload.len() as i64;
    if out_len < payload.len() {
        required[0] = needed;
        return 1;
    }
    let dest = match unsafe { crate::checked_slice_mut(out, out_len) } {
        Some(d) => d,
        None => {
            required[0] = needed;
            return ERR_NULL;
        }
    };
    dest[..payload.len()].copy_from_slice(payload);
    required[0] = needed;
    success_code
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checked_output_metadata_is_admitted_before_dereference() {
        let mut words = [0u64; 4];
        let misaligned = unsafe { words.as_mut_ptr().cast::<u8>().add(1).cast::<i64>() };
        assert_eq!(
            unsafe {
                narwhal_pure_core_macro_plan_checked(
                    std::ptr::null(),
                    0,
                    std::ptr::null_mut(),
                    0,
                    misaligned,
                )
            },
            ERR_NULL
        );
        let mut needed = 99i64;
        assert_eq!(
            unsafe {
                narwhal_pure_core_macro_plan_checked(
                    std::ptr::null(),
                    0,
                    std::ptr::null_mut(),
                    usize::MAX,
                    &mut needed,
                )
            },
            ERR_INPUT
        );
        assert_eq!(needed, 0);
        assert!(pack_actions(&tokenize_steps("=value", true)).is_none());
    }

    fn p(macro_str: &str) -> Option<String> {
        plan_json(macro_str).ok()
    }

    /// Plan and return the exact `(code, payload)` the checked ABI would
    /// publish, so a test can assert the first semantic error in token order.
    fn checked(macro_str: &str) -> (i32, String) {
        match plan_json(macro_str) {
            Ok(json) => (0, json),
            Err(PlannerError::NonNestable(name)) => {
                let mut payload = String::new();
                json_escape(&name, &mut payload);
                (PLAN_ERR_NON_NESTABLE, payload)
            }
            Err(PlannerError::InvalidOptionName) => (PLAN_ERR_INVALID_OPTION, String::new()),
        }
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

    // ------------------------------------------------------------------
    // RUST-PLAN-12: casefold (not to_lowercase) is the nestable check.
    // ------------------------------------------------------------------

    /// The one-to-many casefold mappings that Rust's `to_lowercase` would miss.
    /// Each of these casefolds to a name on the measured nestable allow-list, so
    /// the live Python planner (which uses `casefold`) accepts a transparent
    /// form. A `to_lowercase` implementation would refuse them -- that is the
    /// regression this test pins down.
    #[test]
    fn casefold_mappings_are_nestable() {
        for (input, folded) in [
            ("\u{017F}nap", "snap"),                          // ſ -> s
            ("selcro\u{00DF}ing", "selcrossing"),             // ß -> ss
            ("selcro\u{1E9E}ing", "selcrossing"),             // ẞ -> ss
            ("curvaturegrapho\u{FB00}", "curvaturegraphoff"), // ﬀ -> ff
            ("\u{017F}elwindow", "selwindow"),                // ſ -> s
        ] {
            let cf = casefold(input);
            assert_eq!(cf, folded, "casefold({input:?})");
            assert!(is_nestable(input), "is_nestable({input:?}) via {folded}");
        }
    }

    /// The other ligatures (U+FB01..=FB06) fold to names that are NOT on the
    /// allow-list, so they remain refused. Unknown non-ASCII stays refused too.
    #[test]
    fn non_allowlist_casefolds_remain_refused() {
        for input in [
            "curvaturegrapho\u{FB01}", // ﬁ -> fi
            "curvaturegrapho\u{FB02}", // ﬂ -> fl
            "curvaturegrapho\u{FB03}", // ﬃ -> ffi
            "curvaturegrapho\u{FB04}", // ﬄ -> ffl
            "curvaturegrapho\u{FB05}", // ﬅ -> st
            "curvaturegrapho\u{FB06}", // ﬆ -> st
            "\u{017F}\u{017F}snap",    // ſſ -> ssnap
        ] {
            assert!(!is_nestable(input), "is_nestable({input:?}) must be false");
        }
        // Kelvin already folds to 'k' via to_lowercase and is not an allow-list name.
        assert!(!is_nestable("\u{212A}ELVIN"));
        assert!(!is_nestable("k"));
    }

    /// A transparent command whose name casefolds to an allow-list entry is
    /// accepted end-to-end through the planner (not just `is_nestable`).
    #[test]
    fn casefold_nestable_is_planned() {
        for m in [
            "'\u{017F}nap",
            "'_selcro\u{00DF}ing",
            "'_curvaturegrapho\u{FB00}",
        ] {
            assert!(p(m).is_some(), "{m:?} must be planned");
        }
    }

    // ------------------------------------------------------------------
    // RUST-PLAN-12: empty option name is now refused (the old Rust accepted it).
    // ------------------------------------------------------------------

    /// An option step whose name is empty is the stable `-5` error, matching the
    /// Python `MacroPlanError('option action has an invalid name')`.
    #[test]
    fn empty_option_name_is_refused() {
        // `=value` tokenizes to an option with an empty name (name="", value="value").
        assert_eq!(checked("=value"), (PLAN_ERR_INVALID_OPTION, String::new()));
        // The legacy size/encode routes now report the same stable code.
        let bad = "=value".as_bytes();
        assert_eq!(
            unsafe { narwhal_pure_core_macro_plan_size(bad.as_ptr(), bad.len()) },
            PLAN_ERR_INVALID_OPTION as i64
        );
        assert_eq!(
            unsafe {
                narwhal_pure_core_macro_plan_encode(
                    bad.as_ptr(),
                    bad.len(),
                    std::ptr::null_mut(),
                    0,
                )
            },
            PLAN_ERR_INVALID_OPTION
        );
    }

    /// A non-empty option still plans (only the empty name is refused).
    #[test]
    fn nonempty_option_still_plans() {
        assert!(p("_Mode=_Shaded").is_some());
    }

    // ------------------------------------------------------------------
    // RUST-PLAN-12: the FIRST semantic error in token order wins.
    // ------------------------------------------------------------------

    /// An empty option BEFORE a non-nestable command reports the empty option
    /// (`-5`), not the command (`-4`).
    #[test]
    fn empty_option_beats_later_nonnestable() {
        assert_eq!(
            checked("=value '_Box"),
            (PLAN_ERR_INVALID_OPTION, String::new())
        );
    }

    /// A non-nestable command BEFORE an empty option reports the command (`-4`)
    /// with its name payload, not the empty option.
    #[test]
    fn nonnestable_beats_later_empty_option() {
        assert_eq!(
            checked("'_Box =value"),
            (PLAN_ERR_NON_NESTABLE, r#""Box""#.to_string())
        );
    }

    // ------------------------------------------------------------------
    // RUST-PLAN-12: the checked single-pass ABI.
    // ------------------------------------------------------------------

    fn run_checked(macro_str: &str, out_len: usize) -> (i32, Vec<u8>, i64) {
        let bytes = macro_str.as_bytes();
        let mut out = vec![0xA5u8; out_len];
        let mut needed: i64 = -999;
        let rc = unsafe {
            narwhal_pure_core_macro_plan_checked(
                bytes.as_ptr(),
                bytes.len(),
                out.as_mut_ptr(),
                out_len,
                &mut needed,
            )
        };
        (rc, out, needed)
    }

    #[test]
    fn checked_success_writes_json_and_needed() {
        let m = "'_Zoom _Mode=_Shaded 10";
        let expected = p(m).expect("the planner accepted it");
        let (rc, out, needed) = run_checked(m, expected.len());
        assert_eq!(rc, 0);
        assert_eq!(needed as usize, expected.len());
        assert_eq!(String::from_utf8(out).expect("utf-8"), expected);
    }

    #[test]
    fn checked_short_buffer_reports_needed_and_writes_nothing() {
        let m = "'_Zoom";
        let expected = p(m).expect("accepted");
        // A buffer one byte too small: the sentinel must survive (no write),
        // and `needed` must carry the exact required length so a retry works.
        let (rc, out, needed) = run_checked(m, expected.len() - 1);
        assert_eq!(rc, 1);
        assert_eq!(needed as usize, expected.len());
        assert!(
            out.iter().all(|v| *v == 0xA5),
            "short buffer must not be written"
        );
        // A retry with the reported length succeeds and is byte-exact.
        let (rc2, out2, needed2) = run_checked(m, needed as usize);
        assert_eq!(rc2, 0);
        assert_eq!(needed2, needed);
        assert_eq!(String::from_utf8(out2).expect("utf-8"), expected);
    }

    #[test]
    fn checked_capacity_zero_is_a_size_query() {
        let m = "'_Zoom";
        let expected = p(m).expect("accepted");
        // out_len == 0 with a null out pointer is a pure query: status 1 (short
        // buffer), `needed` set, and no write attempted.
        let mut needed: i64 = -999;
        let rc = unsafe {
            narwhal_pure_core_macro_plan_checked(
                m.as_ptr(),
                m.len(),
                std::ptr::null_mut(),
                0,
                &mut needed,
            )
        };
        assert_eq!(rc, 1);
        assert_eq!(needed as usize, expected.len());
    }

    #[test]
    fn checked_nonnestable_publishes_name_payload() {
        let m = "'_Box";
        let payload = r#""Box""#;
        let (rc, out, needed) = run_checked(m, payload.len());
        assert_eq!(rc, PLAN_ERR_NON_NESTABLE);
        assert_eq!(needed as usize, payload.len());
        assert_eq!(String::from_utf8(out).expect("utf-8"), payload);
        // A short buffer on a rejection also reports the required length.
        let (rc2, out2, needed2) = run_checked(m, 1);
        assert_eq!(rc2, 1);
        assert_eq!(needed2 as usize, payload.len());
        assert_eq!(out2[0], 0xA5, "no write on a short rejection buffer");
    }

    #[test]
    fn checked_empty_option_is_code_minus_five_no_write() {
        let (rc, out, needed) = run_checked("=value", 8);
        assert_eq!(rc, PLAN_ERR_INVALID_OPTION);
        assert_eq!(needed, 0, "no payload on an empty-option refusal");
        assert!(
            out.iter().all(|v| *v == 0xA5),
            "no write on an empty-option refusal"
        );
    }

    #[test]
    fn checked_null_out_needed_and_bad_input() {
        // null `out_needed` is a bad span before anything else.
        let m = "_Zoom";
        let mut out = [0u8; 16];
        let rc = unsafe {
            narwhal_pure_core_macro_plan_checked(
                m.as_ptr(),
                m.len(),
                out.as_mut_ptr(),
                out.len(),
                std::ptr::null_mut(),
            )
        };
        assert_eq!(rc, ERR_NULL);
        // Non-UTF-8 input is an invalid input, `needed` reset to 0.
        let bad = [0xFFu8, 0xFE];
        let mut needed: i64 = -999;
        let rc = unsafe {
            narwhal_pure_core_macro_plan_checked(
                bad.as_ptr(),
                bad.len(),
                std::ptr::null_mut(),
                0,
                &mut needed,
            )
        };
        assert_eq!(rc, ERR_INPUT);
        assert_eq!(needed, 0);
        // Oversized input is refused before any read.
        let big = vec![b'a'; MACRO_MAX_INPUT_BYTES + 1];
        let rc = unsafe {
            narwhal_pure_core_macro_plan_checked(
                big.as_ptr(),
                big.len(),
                std::ptr::null_mut(),
                0,
                &mut needed,
            )
        };
        assert_eq!(rc, ERR_INPUT);
        assert_eq!(needed, 0);
    }

    #[test]
    fn checked_empty_macro_successes_to_empty_list() {
        let (rc, out, needed) = run_checked("", 2);
        assert_eq!(rc, 0);
        assert_eq!(needed, 2);
        assert_eq!(String::from_utf8(out).expect("utf-8"), "[]");
    }
}
