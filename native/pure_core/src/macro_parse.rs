//! SPDX-License-Identifier: GPL-3.0-or-later
//!
//! Native command-macro tokenizer (Q106-12). A faithful port of the pure-Python
//! `narwhal3d/macro.py` tokenizer: the same raw-token splitter (quotes, `;`
//! comments, whitespace) and the same step classifier (cancel / command / pause
//! / enter / text / option), including the lossless `prefixes` and `special`
//! fields used by the execution planner.
//!
//! The output is NESTED (a list of step dicts), so — unlike the scalar
//! `bearing`/`coords` exports — the result is serialized to a bounded JSON
//! string and handed back with the two-pass "size query + encode into a
//! caller-owned buffer" pattern (the same shape as the STL/OBJ/DXF encoders).
//! The Python layer `json.loads` the string and converts the lossless
//! `prefixes` list back into a tuple so it is byte-for-byte the Python result.
//!
//! The crate stays dependency-free: the JSON emitter below is hand-rolled and
//! escapes every string field (names, values, specials) exactly as `json` does.

/// A macro longer than this (in input bytes) is refused by the native route
/// (a Python fallback), not truncated. Real toolbar macros are a few KB at
/// most, so this is a sanity bound, not a practical limit.
pub(crate) const MACRO_MAX_INPUT_BYTES: usize = 1 << 20; // 1 MiB

pub(crate) const ERR_NULL: i32 = -1;
pub(crate) const ERR_INPUT: i32 = -2;
pub(crate) const ERR_SIZE: i32 = -3;

/// Rejection code shared by the macro-plan exports: a transparent command
/// outside the measured nestable allow-list (the Python `macro_plan` module
/// re-raises the exact `NonNestableCommandError` on its fallback path).
pub(crate) const PLAN_ERR_NON_NESTABLE: i32 = -4;

/// True when `b` is one of the documented prefix-flag characters:
/// `- _ ' ! & * ~ # %`.
fn is_flag_byte(b: u8) -> bool {
    matches!(
        b,
        b'-' | b'_' | b'\'' | b'!' | b'&' | b'*' | b'~' | b'#' | b'%'
    )
}

/// True when the lower-cased `name` is one of the special scripting commands.
fn is_special(name: &str) -> bool {
    matches!(
        name,
        "pause"
            | "multipause"
            | "enter"
            | "enterend"
            | "noecho"
            | "echo"
            | "setredrawoff"
            | "setredrawon"
            | "run"
    )
}

fn is_pause(name: &str) -> bool {
    name == "pause" || name == "multipause"
}

fn is_enter(name: &str) -> bool {
    name == "enter" || name == "enterend"
}

/// Split `token` into `(leading_flag_bytes, name)`.
///
/// Leading flag characters (any of the documented prefix characters) are
/// collected; the scan stops at the first character that is not a flag.
/// `strip_flags("!&_Delete")` -> `(b"!&_", "Delete")`.
fn strip_flags(token: &str) -> (Vec<u8>, &str) {
    let b = token.as_bytes();
    let mut i = 0;
    while i < b.len() && is_flag_byte(b[i]) {
        i += 1;
    }
    (b[..i].to_vec(), &token[i..])
}

/// Index of the first `=` outside any double quotes, or `None`.
fn find_eq_outside_quotes(token: &str) -> Option<usize> {
    let b = token.as_bytes();
    let mut in_q = false;
    for (i, &ch) in b.iter().enumerate() {
        if ch == b'"' {
            in_q = !in_q;
        } else if ch == b'=' && !in_q {
            return Some(i);
        }
    }
    None
}

/// A fully-quoted token: length >= 2, first and last char are `"`.
fn is_fully_quoted(token: &str) -> bool {
    let b = token.as_bytes();
    b.len() >= 2 && b[0] == b'"' && b[b.len() - 1] == b'"'
}

/// Normalise an option value: strip surrounding quotes, then leading flags.
/// `"Shaded"` -> `Shaded`; `_Shaded` -> `Shaded`; `10` -> `10`.
fn clean_value(part: &str) -> &str {
    let b = part.as_bytes();
    let s = if b.len() >= 2 && b[0] == b'"' && b[b.len() - 1] == b'"' {
        &part[1..part.len() - 1]
    } else {
        part
    };
    strip_flags(s).1
}

/// Append `s` to `out` as a JSON string literal (surrounding quotes included),
/// escaping exactly as the `json` module does.
pub(crate) fn json_escape(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0C}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// Render a flag byte list as a JSON array of one-character strings, e.g.
/// `b"-_'"` -> `["-","_","'"]`. The flag set is ASCII and never needs escaping.
pub(crate) fn flags_json(flags: &[u8]) -> String {
    let mut s = String::from("[");
    for (i, f) in flags.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push('"');
        s.push(*f as char);
        s.push('"');
    }
    s.push(']');
    s
}

/// The structured result of tokenizing one macro token: a list of steps in
/// order. Q106-13 (`macro_plan`) consumes this directly to build execution
/// actions without re-tokenizing; the JSON output below is derived from it.
#[derive(Debug)]
pub(crate) enum Step {
    Cancel,
    Command {
        name: String,
        dash: bool,
        underscore: bool,
        transparent: bool,
        history_lock_off: bool,
        repeat: bool,
        suppress_options: bool,
        history_on: bool,
        history_off: bool,
        prefixes: Option<Vec<u8>>,
    },
    Pause {
        special: Option<String>,
        prefixes: Option<Vec<u8>>,
    },
    Enter {
        special: Option<String>,
        prefixes: Option<Vec<u8>>,
    },
    Text {
        value: String,
    },
    Option {
        name: String,
        value: String,
        prefixes: Option<Vec<u8>>,
    },
}

impl Step {
    /// Render this step as the exact JSON object the legacy `classify` produced
    /// (no surrounding array). Key order and presence are part of the Q106-12
    /// contract, so the output must stay byte-identical to the old emitter.
    fn to_json(&self) -> String {
        match self {
            Step::Cancel => r#"{"kind":"cancel"}"#.to_string(),
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
                let mut s = String::from("{\"kind\":\"command\",\"name\":");
                json_escape(name, &mut s);
                s.push_str(",\"dash\":");
                s.push_str(if *dash { "true" } else { "false" });
                s.push_str(",\"underscore\":");
                s.push_str(if *underscore { "true" } else { "false" });
                s.push_str(",\"transparent\":");
                s.push_str(if *transparent { "true" } else { "false" });
                // the extra documented flags, present only when set (OURS -- lossless)
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
                if let Some(flags) = prefixes {
                    s.push_str(",\"prefixes\":");
                    s.push_str(&flags_json(flags));
                }
                s.push('}');
                s
            }
            Step::Pause { special, prefixes } => {
                let mut s = String::from("{\"kind\":\"pause\"");
                if let Some(sp) = special {
                    s.push_str(",\"special\":");
                    json_escape(sp, &mut s);
                    s.push_str(",\"prefixes\":");
                    s.push_str(&flags_json(prefixes.as_deref().unwrap_or(&[])));
                }
                s.push('}');
                s
            }
            Step::Enter { special, prefixes } => {
                let mut s = String::from("{\"kind\":\"enter\"");
                if let Some(sp) = special {
                    s.push_str(",\"special\":");
                    json_escape(sp, &mut s);
                    s.push_str(",\"prefixes\":");
                    s.push_str(&flags_json(prefixes.as_deref().unwrap_or(&[])));
                }
                s.push('}');
                s
            }
            Step::Text { value } => {
                let mut s = String::from("{\"kind\":\"text\",\"value\":");
                json_escape(value, &mut s);
                s.push('}');
                s
            }
            Step::Option {
                name,
                value,
                prefixes,
            } => {
                let mut s = String::from("{\"kind\":\"option\",\"name\":");
                json_escape(name, &mut s);
                s.push_str(",\"value\":");
                json_escape(value, &mut s);
                if let Some(flags) = prefixes {
                    s.push_str(",\"prefixes\":");
                    s.push_str(&flags_json(flags));
                }
                s.push('}');
                s
            }
        }
    }
}

/// Classify one raw token into an ordered list of steps (usually one),
/// mirroring `_classify` exactly. A leading `!` flag detaches as a `Cancel`
/// step in front of the rest (the old `_with_cancel` behaviour).
fn classify_steps(token: &str, lossless: bool) -> Vec<Step> {
    // 1. a standalone `!` is a cancel (start, middle or end of a macro)
    if token == "!" {
        return vec![Step::Cancel];
    }
    // 2. a fully quoted string is one text token (a name with spaces)
    if is_fully_quoted(token) {
        return vec![Step::Text {
            value: token[1..token.len() - 1].to_string(),
        }];
    }
    // 3. a `Name=Value` entry is an option ('=' outside any quotes)
    if let Some(i) = find_eq_outside_quotes(token) {
        let (flags, name) = strip_flags(&token[..i]);
        let value = clean_value(&token[i + 1..]);
        let mut out = Vec::with_capacity(2);
        if flags.contains(&b'!') {
            out.push(Step::Cancel);
        }
        out.push(Step::Option {
            name: name.to_string(),
            value: value.to_string(),
            prefixes: if lossless { Some(flags) } else { None },
        });
        return out;
    }
    // 4/5. strip the leading prefix flags and classify the name
    let (flags, name) = strip_flags(token);
    let mut out = Vec::with_capacity(2);
    if flags.contains(&b'!') {
        out.push(Step::Cancel);
    }
    let low = name.to_lowercase();
    if is_special(&low) {
        // the documented special scripting commands
        if is_pause(&low) {
            out.push(Step::Pause {
                special: if lossless {
                    Some(name.to_string())
                } else {
                    None
                },
                prefixes: if lossless { Some(flags) } else { None },
            });
            return out;
        }
        if is_enter(&low) {
            out.push(Step::Enter {
                special: if lossless {
                    Some(name.to_string())
                } else {
                    None
                },
                prefixes: if lossless { Some(flags) } else { None },
            });
            return out;
        }
        // NoEcho / Echo / SetRedrawOff / SetRedrawOn / Run are real commands
    } else if !(!flags.is_empty()
        && !name.is_empty()
        && name
            .chars()
            .next()
            .map(|c| c.is_alphabetic())
            .unwrap_or(false))
    {
        // 6. anything else is a value / keyword argument, kept exactly as written
        out.push(Step::Text {
            value: token.to_string(),
        });
        return out;
    }
    out.push(Step::Command {
        name: name.to_string(),
        dash: flags.contains(&b'-'),
        underscore: flags.contains(&b'_'),
        transparent: flags.contains(&b'\''),
        history_lock_off: flags.contains(&b'&'),
        repeat: flags.contains(&b'*'),
        suppress_options: flags.contains(&b'~'),
        history_on: flags.contains(&b'#'),
        history_off: flags.contains(&b'%'),
        prefixes: if lossless { Some(flags) } else { None },
    });
    out
}

/// Tokenize a macro into an ordered list of structured steps (the Q106-13
/// reuse point: `macro_plan` consumes this directly).
pub(crate) fn tokenize_steps(macro_str: &str, lossless: bool) -> Vec<Step> {
    let mut steps: Vec<Step> = Vec::new();
    for token in split_tokens(macro_str) {
        steps.extend(classify_steps(&token, lossless));
    }
    steps
}

/// Tokenize a macro into a bounded JSON array of steps (the Q106-12 export).
fn tokenize_json(macro_str: &str, lossless: bool) -> String {
    format!(
        "[{}]",
        tokenize_steps(macro_str, lossless)
            .iter()
            .map(Step::to_json)
            .collect::<Vec<_>>()
            .join(",")
    )
}

/// Split a macro into raw token strings, mirroring `_split_tokens` exactly:
/// double quotes keep a space-separated name as ONE token (the quote characters
/// are retained), a `;` outside quotes starts a comment that runs to the end of
/// the current line (so both full-line and trailing comments are dropped), and
/// any whitespace separates tokens.
fn split_tokens(macro_str: &str) -> Vec<String> {
    let chars: Vec<char> = macro_str.chars().collect();
    let n = chars.len();
    let mut tokens: Vec<String> = Vec::new();
    let mut cur: Vec<char> = Vec::new();
    let mut in_quote = false;
    let mut i = 0;
    while i < n {
        let ch = chars[i];
        if in_quote {
            cur.push(ch);
            if ch == '"' {
                in_quote = false;
            }
            i += 1;
            continue;
        }
        if ch == '"' {
            in_quote = true;
            cur.push(ch);
            i += 1;
            continue;
        }
        if ch == ';' {
            if !cur.is_empty() {
                tokens.push(cur.iter().collect());
                cur.clear();
            }
            while i < n && chars[i] != '\n' {
                i += 1;
            }
            continue;
        }
        if ch.is_whitespace() {
            if !cur.is_empty() {
                tokens.push(cur.iter().collect());
                cur.clear();
            }
            i += 1;
            continue;
        }
        cur.push(ch);
        i += 1;
    }
    if !cur.is_empty() {
        tokens.push(cur.iter().collect());
    }
    tokens
}

/// Borrow `len` bytes from `ptr` (null allowed only for `len == 0`).
pub(crate) fn byte_span<'a>(ptr: *const u8, len: usize) -> Option<&'a [u8]> {
    unsafe { crate::checked_slice(ptr, len) }
}

/// Validate the input and return the exact byte length of the encoded JSON, or
/// a negative stable error code. Same validation as the encode export, no output.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when
/// `text_len == 0`).
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_macro_size(
    text: *const u8,
    text_len: usize,
    lossless: u8,
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
    let json = tokenize_json(macro_str, lossless == 1);
    json.len() as i64
}

/// Encode the tokenized steps as bounded JSON into a caller-owned buffer.
///
/// `out` must hold exactly the bytes the encoder will produce (the same length
/// `narwhal_pure_core_macro_size` returns). Returns `0` on success, otherwise a
/// negative stable error code.
///
/// # Safety
///
/// `text` must be readable for `text_len` bytes (null only when `text_len == 0`)
/// and `out` writable for the exact encoded length.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_macro_encode(
    text: *const u8,
    text_len: usize,
    lossless: u8,
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
    let json = tokenize_json(macro_str, lossless == 1);
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

/// Step-kind codes for the packed route (Q117-04). The codes are a stable part
/// of the ABI contract; the Python converter maps them back to the exact
/// `"cancel" / "command" / "pause" / "enter" / "text" / "option"` strings.
pub(crate) const KIND_CANCEL: u8 = 0;
pub(crate) const KIND_COMMAND: u8 = 1;
pub(crate) const KIND_PAUSE: u8 = 2;
pub(crate) const KIND_ENTER: u8 = 3;
pub(crate) const KIND_TEXT: u8 = 4;
pub(crate) const KIND_OPTION: u8 = 5;

/// Command-step flag bits for the packed route (Q117-04). Each bit maps to one
/// documented prefix flag on the command step; the Python converter rebuilds
/// the exact `dash`/`underscore`/`transparent`/`history_*` booleans from them.
pub(crate) const CMD_DASH: u8 = 1 << 0;
pub(crate) const CMD_UNDERSCORE: u8 = 1 << 1;
pub(crate) const CMD_TRANSPARENT: u8 = 1 << 2;
pub(crate) const CMD_HISTORY_LOCK_OFF: u8 = 1 << 3;
pub(crate) const CMD_REPEAT: u8 = 1 << 4;
pub(crate) const CMD_SUPPRESS_OPTIONS: u8 = 1 << 5;
pub(crate) const CMD_HISTORY_ON: u8 = 1 << 6;
pub(crate) const CMD_HISTORY_OFF: u8 = 1 << 7;

/// The packed representation of one step: a kind code, a command flag bitmask,
/// and the (already-classified) string fields. For `command`, `s0` is the
/// command `name`. For `option`, `s0` is the `name` and `s1` is the `value`.
/// For `text`, `s0` is the `value`. For `pause`/`enter`, `s0` is the lossless
/// `special` (empty when not lossless) and `s1` is the lossless `prefixes`
/// (empty when not lossless). `cancel` carries no strings.
/// Render a lossless `prefixes` list as a string of its flag chars (the flag
/// set is ASCII and needs no escaping). `None` yields the empty string.
pub(crate) fn prefixes_string(prefixes: &Option<Vec<u8>>) -> String {
    match prefixes {
        Some(b) => b.iter().map(|f| *f as char).collect(),
        None => String::new(),
    }
}

#[derive(Debug)]
struct PackedStep {
    kind: u8,
    flags: u8,
    s0: String,
    s1: String,
    s2: String,
}

/// Build the packed step list from a structured `Step` list (the same core the
/// JSON route uses, so the two are structurally identical). The `prefixes`
/// `Vec<u8>` are rendered into the string field as one ASCII char per flag
/// (the flag set is ASCII and needs no escaping), matching the lossless tuple
/// the Python contract emits.
fn pack_steps(steps: &[Step]) -> Vec<PackedStep> {
    steps
        .iter()
        .map(|s| match s {
            Step::Cancel => PackedStep {
                kind: KIND_CANCEL,
                flags: 0,
                s0: String::new(),
                s1: String::new(),
                s2: String::new(),
            },
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
                PackedStep {
                    kind: KIND_COMMAND,
                    flags,
                    s0: name.clone(),
                    s1: String::new(),
                    s2: prefixes_string(prefixes),
                }
            }
            Step::Pause { special, prefixes } => PackedStep {
                kind: KIND_PAUSE,
                flags: 0,
                s0: special.clone().unwrap_or_default(),
                s1: prefixes_string(prefixes),
                s2: String::new(),
            },
            Step::Enter { special, prefixes } => PackedStep {
                kind: KIND_ENTER,
                flags: 0,
                s0: special.clone().unwrap_or_default(),
                s1: prefixes_string(prefixes),
                s2: String::new(),
            },
            Step::Text { value } => PackedStep {
                kind: KIND_TEXT,
                flags: 0,
                s0: value.clone(),
                s1: String::new(),
                s2: String::new(),
            },
            Step::Option {
                name,
                value,
                prefixes,
            } => PackedStep {
                kind: KIND_OPTION,
                flags: 0,
                s0: name.clone(),
                s1: value.clone(),
                s2: prefixes_string(prefixes),
            },
        })
        .collect()
}

/// Encode the tokenized steps into packed buffers (Q117-04). Returns the exact
/// `(n_steps, string_bytes)` counts on success. All output is written to the
/// caller-owned arrays; capacity is validated **before any write** so a
/// rejection never leaves partial output behind.
///
/// # Safety
///
/// `kinds`/`flags`/`s0`/`s1`/`s2`/`strings` must each be writable for the
/// given capacity (null only when the capacity is zero).
#[allow(clippy::too_many_arguments)]
pub unsafe fn macro_packed(
    text: *const u8,
    text_len: usize,
    lossless: bool,
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
    out_n_steps: *mut i64,
    out_string_bytes: *mut i64,
) -> i32 {
    if text_len > MACRO_MAX_INPUT_BYTES {
        return ERR_INPUT;
    }
    // Count pointers are part of the atomic output contract. Reject them before
    // borrowing or writing any caller-owned output buffer.
    if out_n_steps.is_null() || out_string_bytes.is_null() {
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
    // every step is produced from at least one raw token, and every raw token
    // is at least one non-whitespace byte; a token and the step it yields both
    // cost >= 1 input byte, so `n_steps <= text_len` (a leading `!` flag splits
    // into a `Cancel` + a `Command`, but the `!` and the command's first byte
    // are two distinct input bytes, so the bound still holds). Each step's
    // three string fields total at most the token's own bytes (name, value,
    // prefixes are all subsets of the token, and `prefixes` is a subset of the
    // token's leading flags), so `string_bytes <= text_len` (summed over steps
    // it is at most the total input bytes, since each input byte belongs to at
    // most one token and each token's fields are disjoint subsets of it). The
    // `+ 1` guards integer division for the empty/whitespace edge.
    let cap_steps = text_len.max(1);
    let cap_strings = text_len;
    if kinds_cap < cap_steps
        || flags_cap < cap_steps
        || s0_cap < cap_steps
        || s1_cap < cap_steps
        || s2_cap < cap_steps
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

    let steps = pack_steps(&tokenize_steps(macro_str, lossless));
    // Total string bytes across all steps' three fields (the strings we
    // actually store -- lossless-only fields are empty otherwise).
    let total_strings: usize = steps
        .iter()
        .map(|p| p.s0.len() + p.s1.len() + p.s2.len())
        .sum();
    if steps.len() > kinds_cap || total_strings > strings_cap {
        return ERR_SIZE;
    }

    let mut si = 0usize;
    for (i, p) in steps.iter().enumerate() {
        kinds[i] = p.kind;
        flags[i] = p.flags;
        // Copy the three fields' bytes in order and record (offset,len) pairs.
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
        *out_n_steps = steps.len() as i64;
        *out_string_bytes = si as i64;
    }
    0
}

/// Encode the tokenized steps into packed buffers (Q117-04).
///
/// # Safety
///
/// See [`macro_packed`] for the pointer contract.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_macro_packed(
    text: *const u8,
    text_len: usize,
    lossless: u8,
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
    out_n_steps: *mut i64,
    out_string_bytes: *mut i64,
) -> i32 {
    unsafe {
        macro_packed(
            text,
            text_len,
            lossless == 1,
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
            out_n_steps,
            out_string_bytes,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// One decoded packed step: `(kind, flags, s0, s1, s2)`. Named so the
    /// `run_packed` return type stays within clippy's complexity budget.
    type PackedStepRow = (u8, u8, String, String, String);

    fn t(macro_str: &str) -> String {
        tokenize_json(macro_str, false)
    }

    fn lt(macro_str: &str) -> String {
        tokenize_json(macro_str, true)
    }

    #[test]
    fn standalone_bang_is_cancel() {
        assert_eq!(t("!"), r#"[{"kind":"cancel"}]"#);
        assert_eq!(
            t("_Move !"),
            r#"[{"kind":"command","name":"Move","dash":false,"underscore":true,"transparent":false},{"kind":"cancel"}]"#
        );
    }

    #[test]
    fn flagged_alpha_word_is_a_command() {
        assert_eq!(
            t("_Move"),
            r#"[{"kind":"command","name":"Move","dash":false,"underscore":true,"transparent":false}]"#
        );
        assert_eq!(
            t("-_SelName"),
            r#"[{"kind":"command","name":"SelName","dash":true,"underscore":true,"transparent":false}]"#
        );
        assert_eq!(
            t("'_Zoom"),
            r#"[{"kind":"command","name":"Zoom","dash":false,"underscore":true,"transparent":true}]"#
        );
    }

    #[test]
    fn leading_bang_emits_cancel_then_command() {
        assert_eq!(
            t("!_Move"),
            r#"[{"kind":"cancel"},{"kind":"command","name":"Move","dash":false,"underscore":true,"transparent":false}]"#
        );
    }

    #[test]
    fn extra_flags_recorded_when_set() {
        assert_eq!(
            t("!&_Delete"),
            r#"[{"kind":"cancel"},{"kind":"command","name":"Delete","dash":false,"underscore":true,"transparent":false,"history_lock_off":true}]"#
        );
        assert_eq!(
            t("*_Circle"),
            r#"[{"kind":"command","name":"Circle","dash":false,"underscore":true,"transparent":false,"repeat":true}]"#
        );
        assert_eq!(
            t("~_Circle"),
            r#"[{"kind":"command","name":"Circle","dash":false,"underscore":true,"transparent":false,"suppress_options":true}]"#
        );
        assert_eq!(
            t("#Circle"),
            r#"[{"kind":"command","name":"Circle","dash":false,"underscore":false,"transparent":false,"history_on":true}]"#
        );
        assert_eq!(
            t("%Circle"),
            r#"[{"kind":"command","name":"Circle","dash":false,"underscore":false,"transparent":false,"history_off":true}]"#
        );
    }

    /// The mutation target: a flagged word whose name starts with a digit is
    /// TEXT, not a command. If the `name[0].isalpha()` requirement were loosened
    /// (e.g. to also accept a leading digit), `_3Point` would become a command
    /// and this fixture must go RED.
    #[test]
    fn flagged_digit_word_is_text() {
        assert_eq!(t("_3Point"), r#"[{"kind":"text","value":"_3Point"}]"#);
        assert_eq!(t("-15,0,0"), r#"[{"kind":"text","value":"-15,0,0"}]"#);
        // the '=' makes it an option regardless of the leading digit
        assert_eq!(
            t("_Points=10"),
            r#"[{"kind":"option","name":"Points","value":"10"}]"#
        );
    }

    #[test]
    fn fully_quoted_string_is_one_text_token() {
        assert_eq!(t("\"My Mode\""), r#"[{"kind":"text","value":"My Mode"}]"#);
        assert_eq!(
            t(r#"_-SelName "Table 01""#),
            r#"[{"kind":"command","name":"SelName","dash":true,"underscore":true,"transparent":false},{"kind":"text","value":"Table 01"}]"#
        );
    }

    #[test]
    fn option_strips_name_and_value_prefixes() {
        assert_eq!(
            t("_Mode=_Shaded"),
            r#"[{"kind":"option","name":"Mode","value":"Shaded"}]"#
        );
        assert_eq!(
            t(r#"_Mode="Shaded""#),
            r#"[{"kind":"option","name":"Mode","value":"Shaded"}]"#
        );
    }

    #[test]
    fn comments_run_to_end_of_line() {
        // trailing comment dropped; 'pause' is the reserved Pause command
        assert_eq!(
            t("_Line 0,0,0 pause ;15,0,0"),
            r#"[{"kind":"command","name":"Line","dash":false,"underscore":true,"transparent":false},{"kind":"text","value":"0,0,0"},{"kind":"pause"}]"#
        );
        // full-line comment dropped
        assert_eq!(
            t("; This is a test macro\n_Circle 0,0,0 15"),
            r#"[{"kind":"command","name":"Circle","dash":false,"underscore":true,"transparent":false},{"kind":"text","value":"0,0,0"},{"kind":"text","value":"15"}]"#
        );
    }

    #[test]
    fn specials_pause_and_enter() {
        assert_eq!(t("_Pause"), r#"[{"kind":"pause"}]"#);
        assert_eq!(t("_MultiPause"), r#"[{"kind":"pause"}]"#);
        assert_eq!(t("_Enter"), r#"[{"kind":"enter"}]"#);
        assert_eq!(t("_EnterEnd"), r#"[{"kind":"enter"}]"#);
    }

    #[test]
    fn command_specials_preserve_name() {
        assert_eq!(
            t("NoEcho"),
            r#"[{"kind":"command","name":"NoEcho","dash":false,"underscore":false,"transparent":false}]"#
        );
        assert_eq!(
            t("Run"),
            r#"[{"kind":"command","name":"Run","dash":false,"underscore":false,"transparent":false}]"#
        );
    }

    #[test]
    fn empty_and_whitespace_macro_is_empty_list() {
        assert_eq!(t(""), "[]");
        assert_eq!(t("   \n\t "), "[]");
    }

    #[test]
    fn lossless_retains_spelling_and_prefixes() {
        assert_eq!(
            lt("_Pause"),
            r#"[{"kind":"pause","special":"Pause","prefixes":["_"]}]"#
        );
        assert_eq!(
            lt("_EnterEnd"),
            r#"[{"kind":"enter","special":"EnterEnd","prefixes":["_"]}]"#
        );
        assert_eq!(
            lt("_Mode=_Shaded"),
            r#"[{"kind":"option","name":"Mode","value":"Shaded","prefixes":["_"]}]"#
        );
        assert_eq!(
            lt("!&_Delete"),
            r#"[{"kind":"cancel"},{"kind":"command","name":"Delete","dash":false,"underscore":true,"transparent":false,"history_lock_off":true,"prefixes":["!","&","_"]}]"#
        );
    }

    #[test]
    fn size_query_matches_encode() {
        let cases = [
            ("!", false),
            ("! _Line _Pause 0,0 _Enter", false),
            ("_-SelName \"Table 01\"", false),
            ("_Mode=_Shaded", true),
            ("!&_Delete", true),
            ("'_CPlane _3Point\n_Pause _Pause _Pause _Enter", false),
        ];
        for (m, lossless) in cases {
            let bytes = m.as_bytes();
            let size = unsafe {
                narwhal_pure_core_macro_size(bytes.as_ptr(), bytes.len(), lossless as u8)
            };
            assert!(size > 0, "size must be positive for {m:?}");
            let mut buf = vec![0u8; size as usize];
            let rc = unsafe {
                narwhal_pure_core_macro_encode(
                    bytes.as_ptr(),
                    bytes.len(),
                    lossless as u8,
                    buf.as_mut_ptr(),
                    buf.len(),
                )
            };
            assert_eq!(rc, 0, "encode must succeed for {m:?}");
            let json = std::str::from_utf8(&buf).expect("valid utf-8");
            assert_eq!(json.len() as i64, size, "encoded length == size");
            // The JSON must parse into a list (a cheap structural check).
            assert!(
                json.starts_with('[') && json.ends_with(']'),
                "array shape: {json}"
            );
        }
    }

    #[test]
    fn rejects_null_when_nonempty() {
        let size = unsafe { narwhal_pure_core_macro_size(std::ptr::null(), 5, 0) };
        assert!(size < 0, "null nonempty input must be rejected: {size}");
    }

    #[test]
    fn rejects_non_utf8() {
        let bad = [0xFFu8, 0xFE, 0xFD, 0xFC, 0xFB];
        let size = unsafe { narwhal_pure_core_macro_size(bad.as_ptr(), bad.len(), 0) };
        assert!(size < 0, "non-utf8 input must be rejected: {size}");
    }

    #[test]
    fn rejects_wrong_output_size() {
        let m = "_Move";
        let bytes = m.as_bytes();
        let small = &mut [0u8; 3];
        let rc = unsafe {
            narwhal_pure_core_macro_encode(
                bytes.as_ptr(),
                bytes.len(),
                0,
                small.as_mut_ptr(),
                small.len(),
            )
        };
        assert_eq!(
            rc, ERR_SIZE,
            "a buffer of the wrong length must be rejected"
        );
    }

    #[test]
    fn rejects_oversized_input() {
        let big = vec![b'a'; MACRO_MAX_INPUT_BYTES + 1];
        let size = unsafe { narwhal_pure_core_macro_size(big.as_ptr(), big.len(), 0) };
        assert!(size < 0, "an over-cap macro must be refused, not truncated");

        let dangling = std::ptr::NonNull::<u8>::dangling().as_ptr();
        assert_eq!(
            unsafe { narwhal_pure_core_macro_size(dangling, usize::MAX, 0) },
            ERR_INPUT as i64
        );
        let input = b"_Move";
        let mut out = [0xA5u8; 8];
        assert_eq!(
            unsafe {
                narwhal_pure_core_macro_encode(
                    std::ptr::null(),
                    input.len(),
                    0,
                    out.as_mut_ptr(),
                    out.len(),
                )
            },
            ERR_NULL
        );
        assert_eq!(out, [0xA5; 8]);
    }

    #[test]
    fn tokenize_steps_is_the_structured_reuse_point() {
        // The Q106-13 planner consumes these steps directly: a leading `!`
        // detaches as its own Cancel step, and the flags are preserved verbatim.
        let steps = tokenize_steps("!&_Delete", true);
        assert_eq!(steps.len(), 2);
        assert!(matches!(steps[0], Step::Cancel));
        match &steps[1] {
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
                assert_eq!(name, "Delete");
                assert!(!dash && *underscore && !transparent);
                assert!(*history_lock_off && !repeat && !suppress_options);
                assert!(!history_on && !history_off);
                assert_eq!(prefixes.as_deref(), Some(b"!&_".as_slice()));
            }
            other => panic!("expected a command step, got {other:?}"),
        }
    }

    #[test]
    fn tokenize_steps_preserves_transparency_and_prefixes() {
        // The planner's safety boundary: `transparent` and the raw prefix
        // sequence must both survive tokenizing (it checks one against the
        // allow-list and copies the other into the action).
        let steps = tokenize_steps("'_Box", true);
        assert_eq!(steps.len(), 1);
        match &steps[0] {
            Step::Command {
                name,
                transparent,
                prefixes,
                ..
            } => {
                assert_eq!(name, "Box");
                assert!(*transparent);
                // `'_Box` keeps both flag chars: the transparency `'` and the `_`.
                assert_eq!(prefixes.as_deref(), Some(b"'_".as_slice()));
            }
            other => panic!("expected a command step, got {other:?}"),
        }
    }

    // ---- packed route (Q117-04) ------------------------------------------

    /// The packed representation a `Step` should have (the structural identity
    /// the packed route must reproduce).
    fn expected_packed(step: &Step) -> (u8, u8, String, String, String) {
        match step {
            Step::Cancel => (KIND_CANCEL, 0, String::new(), String::new(), String::new()),
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
                let mut f = 0u8;
                if *dash {
                    f |= CMD_DASH;
                }
                if *underscore {
                    f |= CMD_UNDERSCORE;
                }
                if *transparent {
                    f |= CMD_TRANSPARENT;
                }
                if *history_lock_off {
                    f |= CMD_HISTORY_LOCK_OFF;
                }
                if *repeat {
                    f |= CMD_REPEAT;
                }
                if *suppress_options {
                    f |= CMD_SUPPRESS_OPTIONS;
                }
                if *history_on {
                    f |= CMD_HISTORY_ON;
                }
                if *history_off {
                    f |= CMD_HISTORY_OFF;
                }
                (
                    KIND_COMMAND,
                    f,
                    name.clone(),
                    String::new(),
                    prefixes_string(prefixes),
                )
            }
            Step::Pause { special, prefixes } => (
                KIND_PAUSE,
                0,
                special.clone().unwrap_or_default(),
                prefixes_string(prefixes),
                String::new(),
            ),
            Step::Enter { special, prefixes } => (
                KIND_ENTER,
                0,
                special.clone().unwrap_or_default(),
                prefixes_string(prefixes),
                String::new(),
            ),
            Step::Text { value } => (KIND_TEXT, 0, value.clone(), String::new(), String::new()),
            Step::Option {
                name,
                value,
                prefixes,
            } => (
                KIND_OPTION,
                0,
                name.clone(),
                value.clone(),
                prefixes_string(prefixes),
            ),
        }
    }

    /// Run the packed ABI with given capacities and return the decoded steps.
    fn run_packed(
        text: &str,
        lossless: bool,
        kinds_cap: usize,
        strings_cap: usize,
    ) -> (i32, Vec<PackedStepRow>, i64, i64) {
        let b = text.as_bytes();
        let mut kinds = vec![0u8; kinds_cap];
        let mut flags = vec![0u8; kinds_cap];
        let mut s0 = vec![0u64; kinds_cap];
        let mut s1 = vec![0u64; kinds_cap];
        let mut s2 = vec![0u64; kinds_cap];
        let mut strings = vec![0u8; strings_cap];
        let mut n_steps: i64 = -99;
        let mut n_bytes: i64 = -99;
        let rc = unsafe {
            macro_packed(
                b.as_ptr(),
                b.len(),
                lossless,
                kinds.as_mut_ptr(),
                kinds_cap,
                flags.as_mut_ptr(),
                kinds_cap,
                s0.as_mut_ptr(),
                kinds_cap,
                s1.as_mut_ptr(),
                kinds_cap,
                s2.as_mut_ptr(),
                kinds_cap,
                strings.as_mut_ptr(),
                strings_cap,
                &mut n_steps,
                &mut n_bytes,
            )
        };
        let decode = |v: u64, strings: &[u8]| -> String {
            let off = (v >> 32) as usize;
            let len = (v & 0xFFFF_FFFF) as usize;
            if len == 0 {
                String::new()
            } else {
                std::str::from_utf8(&strings[off..off + len])
                    .unwrap()
                    .to_string()
            }
        };
        let steps = (0..n_steps.max(0) as usize)
            .map(|i| {
                (
                    kinds.get(i).copied().unwrap_or(0),
                    flags.get(i).copied().unwrap_or(0),
                    decode(s0.get(i).copied().unwrap_or(0), &strings),
                    decode(s1.get(i).copied().unwrap_or(0), &strings),
                    decode(s2.get(i).copied().unwrap_or(0), &strings),
                )
            })
            .collect();
        (rc, steps, n_steps, n_bytes)
    }

    #[test]
    fn packed_matches_the_structured_core() {
        // The packed route must reproduce the exact structured `Step` sequence
        // (kinds, flags, and string fields), for both lossless modes and across
        // every step kind.
        for text in [
            "!&_Delete",
            "! _Line _Pause 0,0 _Enter",
            "-_SetDisplayMode _Mode=_Shaded",
            "'_Zoom",
            "*_Circle",
            "~_Circle",
            "#Circle",
            "%Circle",
            "_3Point",
            "-15,0,0",
            "_Points=10",
            "My Mode",
            r##""My Mode""##, // fully-quoted string -> one text token
            "_-SelName Table 01",
            "_Pause",
            "_MultiPause",
            "_Enter",
            "_EnterEnd",
            "NoEcho",
            "Run",
            "! _-DocumentProperties _Mesh _Custom _MaxEdgeSrf .01 _EnterEnd",
        ] {
            for lossless in [false, true] {
                let cap = text.len().max(1);
                let (rc, got, n_steps, _n_bytes) = run_packed(text, lossless, cap, text.len());
                assert_eq!(rc, 0, "packed should succeed for {text:?}");
                let want = tokenize_steps(text, lossless)
                    .iter()
                    .map(expected_packed)
                    .collect::<Vec<_>>();
                assert_eq!(n_steps as usize, want.len(), "step count {text:?}");
                assert_eq!(got, want, "packed != structured core for {text:?}");
            }
        }
    }

    #[test]
    fn packed_exact_capacity_and_counts() {
        // The capacity bound is `text_len` for steps and strings. A buffer of
        // exactly that size must succeed, and the published counts must be the
        // exact step count and the exact total string bytes (not the capacity).
        let text = "!&_Delete";
        let cap = text.len();
        let (rc, got, n_steps, n_bytes) = run_packed(text, true, cap, cap);
        assert_eq!(rc, 0);
        assert_eq!(n_steps, 2); // Cancel + Command
                                // `!&_Delete`: Cancel (no strings) + Command(name "Delete" 6 bytes,
                                // lossless prefixes "!&_" 3 bytes) -> total stored string bytes == 9.
        assert_eq!(n_bytes, 9);
        assert_eq!(got[0].0, KIND_CANCEL);
        assert_eq!(got[1].0, KIND_COMMAND);
    }

    #[test]
    fn packed_insufficient_capacity_rejects_and_counts_untouched() {
        // A capacity below the bound must be rejected (ERR_SIZE) and the
        // published counts must be left untouched (no partial output).
        let text = "! _Line _Pause 0,0 _Enter"; // 26 bytes
        let (rc, _got, n_steps, n_bytes) = run_packed(text, true, 3, 3);
        assert_eq!(rc, ERR_SIZE, "small capacity must be rejected");
        assert_eq!(n_steps, -99, "counts must be untouched on rejection");
        assert_eq!(n_bytes, -99, "counts must be untouched on rejection");
    }

    #[test]
    fn packed_rejects_null_and_oversized() {
        // A null text pointer (non-empty) is ERR_NULL.
        let mut n_steps: i64 = -99;
        let mut n_bytes: i64 = -99;
        let mut kinds: [u8; 4] = [0u8; 4];
        let u64_null = std::ptr::null_mut::<u64>();
        let u8_null = std::ptr::null_mut::<u8>();
        let rc = unsafe {
            macro_packed(
                std::ptr::null(),
                5,
                false,
                kinds.as_mut_ptr(),
                4,
                kinds.as_mut_ptr(),
                4,
                u64_null,
                4,
                u64_null,
                4,
                u64_null,
                4,
                u8_null,
                4,
                &mut n_steps,
                &mut n_bytes,
            )
        };
        assert_eq!(rc, ERR_NULL);

        // An over-cap input is ERR_INPUT (refused, not truncated).
        let big = vec![b'a'; MACRO_MAX_INPUT_BYTES + 1];
        let mut n_steps2: i64 = -99;
        let mut n_bytes2: i64 = -99;
        let mut kinds2 = vec![0u8; big.len()];
        let rc = unsafe {
            macro_packed(
                big.as_ptr(),
                big.len(),
                false,
                kinds2.as_mut_ptr(),
                kinds2.len(),
                kinds2.as_mut_ptr(),
                kinds2.len(),
                u64_null,
                kinds2.len(),
                u64_null,
                kinds2.len(),
                u64_null,
                kinds2.len(),
                u8_null,
                kinds2.len(),
                &mut n_steps2,
                &mut n_bytes2,
            )
        };
        assert_eq!(rc, ERR_INPUT);
    }

    #[test]
    fn packed_null_count_pointer_writes_nothing() {
        let text = b"_Line";
        let cap = text.len();
        let mut kinds = vec![0xA5u8; cap];
        let mut flags = vec![0xA5u8; cap];
        let mut s0 = vec![0xA5A5u64; cap];
        let mut s1 = vec![0xA5A5u64; cap];
        let mut s2 = vec![0xA5A5u64; cap];
        let mut strings = vec![0xA5u8; cap];
        let mut n_bytes = -1i64;
        let rc = unsafe {
            macro_packed(
                text.as_ptr(),
                cap,
                false,
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
    fn packed_3point_is_text_not_command() {
        // The mutation target: a flagged word whose name starts with a digit is
        // TEXT (kind 4), not a command. If the Rust `name[0].isalpha()` check
        // were loosened to accept a leading digit, this would go RED.
        let (rc, got, n_steps, _n_bytes) = run_packed("_3Point", false, 7, 7);
        assert_eq!(rc, 0);
        assert_eq!(n_steps, 1);
        assert_eq!(got[0].0, KIND_TEXT);
        assert_eq!(got[0].2, "_3Point"); // s0 is the value
    }

    #[test]
    fn packed_preserves_command_flags_and_lossless_prefixes() {
        // `!&_Delete`: Cancel + Command(Delete, underscore + history_lock_off,
        // lossless prefixes "!&_"). Both the flag bitmask and the lossless
        // prefixes string must survive.
        let (rc, got, n_steps, _n_bytes) = run_packed("!&_Delete", true, 9, 9);
        assert_eq!(rc, 0);
        assert_eq!(n_steps, 2);
        let cmd = &got[1];
        assert_eq!(cmd.0, KIND_COMMAND);
        assert_eq!(cmd.1, CMD_UNDERSCORE | CMD_HISTORY_LOCK_OFF);
        assert_eq!(cmd.2, "Delete"); // name
        assert_eq!(cmd.4, "!&_"); // lossless prefixes
    }
}
