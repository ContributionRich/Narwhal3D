//! RUST-ALIAS-14: native ownership of the `parse_macro` GRAMMAR ONLY.
//!
//! Faithful, bounded port of the grammar in the live
//! `narwhal3d/alias_table.py::parse_macro`, which is
//!
//! ```text
//! ^(?P<prefix>['!_])?\s*_?(?P<cmd>\w+)(?:\s+(?P<rest>.*))?$
//! ```
//!
//! with the options derived from `rest` by whitespace-splitting and stripping
//! each token's leading underscores. The Python grammar (regex + `str.split` +
//! `str.lstrip`) is retired to this module. The distinct `AliasTable` state,
//! casefold, defaults, status mapping and CRUD remain in Python (a separate,
//! future task); this file owns the parse grammar alone.
//!
//! MEASURED against CPython 3.13 Unicode 15.1. A word character is
//! `calc_owned::alnum` (CPython `str.isalpha` letters | `str.isnumeric`) plus
//! the underscore; a space is `python_text::is_space` (CPython `str.isspace`).
//! Both are re-verified in the test suite against `re` `\w` and `str.isspace`,
//! so no new Unicode table is introduced.
//!
//! OURS bounded policy: at most 4096 codepoints per input (beyond that is an
//! explicit transport error, never a Python replay). A pure, bounded, two-pass
//! query: `narwhal_pure_core_alias_parse` reports the prefix/command spans and
//! the option count; `narwhal_pure_core_alias_options` then fills a
//! caller-owned span array sized to that exact count. No Rust-owned pointer
//! crosses the call boundary and no partial write ever occurs on a short
//! buffer.
use crate::calc_owned::alnum;
use crate::python_text::is_space;

/// Bounded input size (codepoints). Beyond this is an explicit error.
pub const LIMIT: u32 = 4096;
/// The specific owned ABI identity for this module. The host must check BOTH
/// this (module-level) and the core `narwhal_pure_core_abi_version`.
pub const ALIAS_ABI_IDENTITY: u32 = 1;

// Reply codes.
pub const CODE_OK: u32 = 0;
pub const CODE_INVALID: u32 = 1;
pub const CODE_BOUND: u32 = 2;

/// A single (start, end) codepoint span, in the input's original codepoint
/// offsets. `end` may equal `start` only for an empty option (a token that is
/// only underscores).
#[repr(C)]
#[derive(Debug, Default, Clone, Copy)]
pub struct Span {
    pub start: u32,
    pub end: u32,
}

/// Caller-owned fixed reply. `prefix_start`/`prefix_end` are equal when there
/// is no prefix. `command_end` > `command_start` always holds on a valid reply.
/// `option_count` sizes the caller-owned option-span array. Reserved is zero
/// on every path.
#[repr(C)]
#[derive(Debug, Default, Clone, Copy)]
pub struct Reply {
    pub code: u32,
    pub reserved: u32,
    pub prefix_start: u32,
    pub prefix_end: u32,
    pub command_start: u32,
    pub command_end: u32,
    pub option_count: u32,
}

#[derive(Debug)]
pub enum Parsed {
    /// A grammar match: optional prefix, command, and option spans.
    Ok {
        prefix: Option<Span>,
        command: Span,
        options: Vec<Span>,
    },
    /// The grammar does not match (the host maps this to `(None, None, None)`).
    Invalid,
}

fn word(cp: u32) -> bool {
    cp == 0x5f || alnum(cp)
}

/// `python_text::is_space` is `char`-based; the grammar operates on codepoints.
fn space(cp: u32) -> bool {
    char::from_u32(cp).is_some_and(is_space)
}

/// The `(?:\s+(?P<rest>.*))?$` tail semantics, measured against CPython 3.13.
///
/// The tail is a `\s+` (one or more whitespace, which *may* include newlines)
/// followed by `.*` (zero or more *non-newline* codepoints) followed by `$`
/// (the end of the string, or immediately before a single trailing 0x0a). So
/// the remainder after the command is valid exactly when:
///   * it is empty, OR
///   * it begins with whitespace, AND
///   * after its maximal leading whitespace run there is no newline except
///     possibly exactly one as the final codepoint.
///
/// A newline that is neither in the leading whitespace run nor the final
/// codepoint is rejected (the `.` in `rest` excludes 0x0a and `$` cannot anchor
/// before it). Other whitespace codepoints (0x0d, 0x0c, 0x0b, 0x85, ...) are
/// always fine because both `\s` and `.` match them.
///
/// MEASURED: `Zoom\n`, `Zoom\n\n` and `Zoom\n\n\n` all match (rest = ``/`\n`/
/// `\n\n`), while `Zoom A\n\n` does not (the first newline is not the final one)
/// and `Zoom Ext\nMore` / `Zoom x\ny` / `Zoom\nA\nB` do not (non-newline code
/// after an internal newline).
fn tail_ok(cps: &[u32], cmd_end: usize) -> bool {
    let remainder = &cps[cmd_end..];
    if remainder.is_empty() {
        return true;
    }
    // `\s+` requires the first codepoint to be whitespace.
    if !space(remainder[0]) {
        return false;
    }
    // Consume the maximal leading whitespace run (the `\s+` part).
    let mut k = 0;
    while k < remainder.len() && space(remainder[k]) {
        k += 1;
    }
    // `.*$`: no newline, except possibly exactly one as the final codepoint.
    let s = &remainder[k..];
    let mut internal_newline = false;
    for (i, &c) in s.iter().enumerate() {
        if c == 0x0a && i + 1 != s.len() {
            internal_newline = true;
        }
    }
    !internal_newline
}

/// The longest run of word characters from `i`, or `None` when `cps[i]` is not
/// a word character.
fn word_at(cps: &[u32], i: usize) -> Option<usize> {
    let mut end = i;
    while end < cps.len() && word(cps[end]) {
        end += 1;
    }
    if end == i {
        return None;
    }
    Some(end)
}

/// Derive the option spans from `cmd_end`, exactly the reference
/// `(rest or "").split()` + `token.lstrip('_').strip()`.
///
/// `str.split()` (no argument) splits on runs of *Unicode* whitespace (the
/// `is_space` predicate), so a token is the maximal run of non-whitespace
/// codepoints. Each token's leading underscores are removed (`lstrip('_')`);
/// the trailing `.strip()` is a no-op because `split()` already removed
/// surrounding whitespace. An all-underscore token yields an empty option.
fn option_spans(cps: &[u32], cmd_end: usize) -> Vec<Span> {
    let n = cps.len();
    let mut i = cmd_end;
    let mut options = Vec::new();
    while i < n {
        // Skip the full whitespace run (the `str.split()` separator).
        while i < n && space(cps[i]) {
            i += 1;
        }
        if i >= n {
            break;
        }
        // Read the token: maximal run of non-whitespace codepoints.
        let tok_start = i;
        while i < n && !space(cps[i]) {
            i += 1;
        }
        let tok_end = i;
        // Strip leading underscores (`lstrip('_')`).
        let mut s = tok_start;
        while s < tok_end && cps[s] == 0x5f {
            s += 1;
        }
        // `split()` already removed surrounding whitespace, so the token has
        // none; the option is the underscore-stripped span (possibly empty).
        options.push(Span {
            start: s as u32,
            end: tok_end as u32,
        });
    }
    options
}

/// Parse the grammar at position `j` (after the optional leading spaces), with
/// an optional prefix span.
///
/// The `_?` marker is greedy: it is consumed (command starts at `j + 1`) when
/// that yields a valid command and tail; otherwise it backtracks and the
/// underscore is part of the command (command starts at `j`). This mirrors the
/// reference regex `\s*_?(?P<cmd>\w+)`, where `_?` matches first and gives way
/// only when no word character would remain for `\w+`.
fn parse_at(cps: &[u32], j: usize, prefix: Option<Span>) -> Option<Parsed> {
    // Greedy `_?`: try consuming the marker (command starts at `j + 1`).
    if j < cps.len() && cps[j] == 0x5f {
        if let Some(cmd_end) = word_at(cps, j + 1) {
            if tail_ok(cps, cmd_end) {
                return Some(Parsed::Ok {
                    prefix,
                    command: Span {
                        start: (j + 1) as u32,
                        end: cmd_end as u32,
                    },
                    options: option_spans(cps, cmd_end),
                });
            }
        }
    }
    // Backtrack: the marker is not consumed; any underscore at `j` is part of
    // the command (command starts at `j`).
    if let Some(cmd_end) = word_at(cps, j) {
        if tail_ok(cps, cmd_end) {
            return Some(Parsed::Ok {
                prefix,
                command: Span {
                    start: j as u32,
                    end: cmd_end as u32,
                },
                options: option_spans(cps, cmd_end),
            });
        }
    }
    None
}

/// Try the branch where the optional prefix group is present (the first
/// codepoint is one of `'`, `!`, `_`).
fn try_prefix(cps: &[u32], s: usize) -> Option<Parsed> {
    let p = Span {
        start: s as u32,
        end: (s + 1) as u32,
    };
    let n = cps.len();
    let mut j = s + 1;
    while j < n && space(cps[j]) {
        j += 1;
    }
    parse_at(cps, j, Some(p))
}

/// Try the branch where the optional prefix group is absent.
fn try_no_prefix(cps: &[u32], s: usize) -> Option<Parsed> {
    let n = cps.len();
    let mut j = s;
    while j < n && space(cps[j]) {
        j += 1;
    }
    parse_at(cps, j, None)
}

/// The pure grammar, exactly the reference regex. Returns `Invalid` when the
/// grammar does not match. The optional prefix is kept when its branch matches
/// (greedy-first, matching the reference), otherwise the no-prefix branch is
/// used.
pub fn parse_macro(cps: &[u32]) -> Parsed {
    let n = cps.len();
    if n == 0 {
        return Parsed::Invalid;
    }
    // The prefix is only valid at position 0 (immediately after ^). If the
    // first codepoint is a prefix character, try the prefix branch first;
    // otherwise (or if it fails) fall through to the no-prefix branch.
    let first = cps[0];
    if first == '\'' as u32 || first == '!' as u32 || first == '_' as u32 {
        if let Some(p) = try_prefix(cps, 0) {
            return p;
        }
    }
    match try_no_prefix(cps, 0) {
        Some(p) => p,
        None => Parsed::Invalid,
    }
}

fn reply_for(parsed: &Parsed) -> Reply {
    match parsed {
        Parsed::Invalid => Reply {
            code: CODE_INVALID,
            ..Reply::default()
        },
        Parsed::Ok {
            prefix,
            command,
            options,
        } => Reply {
            code: CODE_OK,
            prefix_start: prefix.map(|p| p.start).unwrap_or(0),
            prefix_end: prefix.map(|p| p.end).unwrap_or(0),
            command_start: command.start,
            command_end: command.end,
            option_count: options.len() as u32,
            ..Reply::default()
        },
    }
}

/// The specific owned ABI identity for this module. The host checks this in
/// addition to the core `narwhal_pure_core_abi_version`.
#[no_mangle]
pub extern "C" fn narwhal_pure_core_alias_identity() -> u32 {
    ALIAS_ABI_IDENTITY
}

/// Parse the macro grammar and, when it matches, fill the caller-owned option
/// span array.
///
/// Return codes: 0 = a reply was written (valid or the invalid grammar);
/// 1 = bad caller metadata (wrong `out_size`, `unicode` not 151, or an option
/// span buffer too short for the reported count); 2 = input beyond the bounded
/// limit (an explicit error, never a Python replay).
///
/// `out` must reference `out_size` writable bytes. `options` must reference
/// `options_len` writable `u32` pairs (16 bytes each) when the reply carries
/// options; it may be null with `options_len == 0`.
///
/// # Safety
/// Non-empty spans must be readable `u32` arrays; `out`, `options` and the
/// input must be aligned, writable (for `out`/`options`), exclusive, and not
/// overlap each other or the input.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_alias_parse(
    input: *const u32,
    input_len: usize,
    unicode: u32,
    out: *mut Reply,
    out_size: usize,
    options: *mut u32,
    options_len: usize,
) -> u32 {
    if out_size != std::mem::size_of::<Reply>() || unicode != 151 {
        return 1;
    }
    let Some(output) = (unsafe { crate::checked_slice_mut(out, 1) }) else {
        return 1;
    };
    output[0] = Reply::default();
    // Bound check in usize BEFORE the pointer is read or any metadata is
    // truncated: `input_len as u32` would wrap a length above u32::MAX and
    // could pass the bound, so the comparison never casts.
    if input_len > LIMIT as usize {
        output[0].code = CODE_BOUND;
        return 2;
    }
    let Some(input) = (unsafe { crate::checked_slice(input, input_len) }) else {
        return 1;
    };
    let parsed = parse_macro(input);
    let reply = reply_for(&parsed);
    if let Parsed::Ok { options: opts, .. } = &parsed {
        let count = opts.len();
        if count > 0 {
            if count * 2 > options_len {
                return 1;
            }
            let Some(dst) = (unsafe { crate::checked_slice_mut(options, count * 2) }) else {
                return 1;
            };
            for (idx, o) in opts.iter().enumerate() {
                dst[idx * 2] = o.start;
                dst[idx * 2 + 1] = o.end;
            }
        }
    }
    output[0] = reply;
    0
}

/// Fill the caller-owned option span array for an already-parsed reply.
///
/// Return codes: 0 = filled (a no-op when `count` is 0); 1 = bad caller
/// metadata (wrong `count`/`capacity` or a short buffer); 2 = beyond the
/// bounded limit.
///
/// `spans` must reference `capacity` writable `u32` pairs (16 bytes each).
///
/// # Safety
/// `spans` must reference `capacity` aligned, writable, exclusive,
/// non-overlapping `u32` values.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_alias_options(
    input: *const u32,
    input_len: usize,
    count: usize,
    spans: *mut u32,
    capacity: usize,
) -> u32 {
    // Bound check in usize before the pointer is read; never cast to u32.
    if input_len > LIMIT as usize {
        return 2;
    }
    let Some(input) = (unsafe { crate::checked_slice(input, input_len) }) else {
        return 1;
    };
    let parsed = parse_macro(input);
    let Parsed::Ok { options, .. } = parsed else {
        return 1;
    };
    if options.len() != count {
        return 1;
    }
    if count == 0 {
        return 0;
    }
    if count * 2 > capacity {
        return 1;
    }
    let Some(dst) = (unsafe { crate::checked_slice_mut(spans, count * 2) }) else {
        return 1;
    };
    for (idx, o) in options.iter().enumerate() {
        dst[idx * 2] = o.start;
        dst[idx * 2 + 1] = o.end;
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(s: &str) -> Option<(Option<String>, String, Vec<String>)> {
        let cps: Vec<u32> = s.chars().map(|c| c as u32).collect();
        match parse_macro(&cps) {
            Parsed::Invalid => None,
            Parsed::Ok {
                prefix,
                command,
                options,
            } => {
                let t = |sp: &Span| {
                    cps[sp.start as usize..sp.end as usize]
                        .iter()
                        .map(|c| char::from_u32(*c).unwrap())
                        .collect()
                };
                Some((
                    prefix.as_ref().map(&t),
                    t(&command),
                    options.iter().map(&t).collect(),
                ))
            }
        }
    }

    #[test]
    fn defaults_match_the_reference() {
        let cases: &[(&str, Option<&str>, &str, &[&str])] = &[
            ("'_Zoom", Some("'"), "Zoom", &[]),
            ("'_Zoom _Extents", Some("'"), "Zoom", &["Extents"]),
            (
                "'_Zoom _All _Extents",
                Some("'"),
                "Zoom",
                &["All", "Extents"],
            ),
            ("! _Move", Some("!"), "Move", &[]),
            ("_Undo", Some("_"), "Undo", &[]),
            ("! _PointsOn", Some("!"), "PointsOn", &[]),
            ("'_SelCrossing", Some("'"), "SelCrossing", &[]),
            ("! _DeleteSubCrv", Some("!"), "DeleteSubCrv", &[]),
            ("'_SelPolysrf", Some("'"), "SelPolysrf", &[]),
            (
                "! _OptionsPage _PlugIns",
                Some("!"),
                "OptionsPage",
                &["PlugIns"],
            ),
            (
                "! _OptionsPage _DisplayModes",
                Some("!"),
                "OptionsPage",
                &["DisplayModes"],
            ),
        ];
        for (input, prefix, command, options) in cases {
            let got = run(input).unwrap_or_else(|| panic!("{input:?} did not parse"));
            assert_eq!(got.0.as_deref(), *prefix, "prefix {input:?}");
            assert_eq!(got.1, *command, "command {input:?}");
            assert_eq!(got.2, options.to_vec(), "options {input:?}");
        }
    }

    #[test]
    fn invalid_inputs_return_none() {
        for bad in [
            "",
            "   ",
            "\t",
            "\n",
            "' ",
            "'  ",
            "_!",
            "__!",
            "!!",
            "''",
            "'!",
            "! ",
            "_!_",
            "Zoom A\n\n",
            "Zoom Ext\nMore",
            "Zoom x\ny",
            "Zoom A\nB",
        ] {
            assert!(run(bad).is_none(), "{bad:?} must be invalid");
        }
    }

    #[test]
    fn underscore_ladder_matches_the_reference() {
        assert_eq!(run("_"), Some((None, "_".into(), vec![])));
        assert_eq!(run("__"), Some((Some("_".into()), "_".into(), vec![])));
        assert_eq!(run("___"), Some((Some("_".into()), "_".into(), vec![])));
        assert_eq!(run("____"), Some((Some("_".into()), "__".into(), vec![])));
        assert_eq!(run("_____"), Some((Some("_".into()), "___".into(), vec![])));
        assert_eq!(run("_x"), Some((Some("_".into()), "x".into(), vec![])));
        assert_eq!(run("__x"), Some((Some("_".into()), "x".into(), vec![])));
        assert_eq!(run("___x"), Some((Some("_".into()), "_x".into(), vec![])));
        assert_eq!(run("'_"), Some((Some("'".into()), "_".into(), vec![])));
        assert_eq!(run("'__"), Some((Some("'".into()), "_".into(), vec![])));
        assert_eq!(run("'___"), Some((Some("'".into()), "__".into(), vec![])));
        assert_eq!(run("!_"), Some((Some("!".into()), "_".into(), vec![])));
        assert_eq!(run("!__"), Some((Some("!".into()), "_".into(), vec![])));
        assert_eq!(run("!___"), Some((Some("!".into()), "__".into(), vec![])));
        assert_eq!(run("_ _"), Some((Some("_".into()), "_".into(), vec![])));
        assert_eq!(run("_  "), Some((None, "_".into(), vec![])));
        assert_eq!(run("_ x"), Some((Some("_".into()), "x".into(), vec![])));
        assert_eq!(run("_  x"), Some((Some("_".into()), "x".into(), vec![])));
        assert_eq!(run(" _ "), Some((None, "_".into(), vec![])));
        assert_eq!(
            run("__ _"),
            Some((Some("_".into()), "_".into(), vec!["".into()]))
        );
    }

    #[test]
    fn option_tokens_strip_leading_underscores() {
        assert_eq!(
            run("Zoom _A _B"),
            Some((None, "Zoom".into(), vec!["A".into(), "B".into()]))
        );
        assert_eq!(
            run("Zoom ___"),
            Some((None, "Zoom".into(), vec!["".into()]))
        );
        assert_eq!(run("Zoom _"), Some((None, "Zoom".into(), vec!["".into()])));
        assert_eq!(
            run("Zoom _A___B"),
            Some((None, "Zoom".into(), vec!["A___B".into()]))
        );
        assert_eq!(
            run("Zoom  _A  _B  "),
            Some((None, "Zoom".into(), vec!["A".into(), "B".into()]))
        );
        assert_eq!(
            run("Zoom A_B"),
            Some((None, "Zoom".into(), vec!["A_B".into()]))
        );
        assert_eq!(run("a _"), Some((None, "a".into(), vec!["".into()])));
    }

    #[test]
    fn newline_tail_semantics() {
        assert_eq!(run("Zoom\n"), Some((None, "Zoom".into(), vec![])));
        assert_eq!(run("Zoom\n\n"), Some((None, "Zoom".into(), vec![])));
        assert_eq!(run("Zoom \n"), Some((None, "Zoom".into(), vec![])));
        assert_eq!(
            run("Zoom A\n"),
            Some((None, "Zoom".into(), vec!["A".into()]))
        );
        assert_eq!(
            run("Zoom A B\n"),
            Some((None, "Zoom".into(), vec!["A".into(), "B".into()]))
        );
        assert!(run("Zoom A\n\n").is_none());
        assert!(run("Zoom Ext\nMore").is_none());
        assert!(run("Zoom x\ny").is_none());
        assert_eq!(run("a\nb"), Some((None, "a".into(), vec!["b".into()])));
        assert_eq!(
            run("Zoom \n x"),
            Some((None, "Zoom".into(), vec!["x".into()]))
        );
        assert_eq!(
            run("Zoom\nx"),
            Some((None, "Zoom".into(), vec!["x".into()]))
        );
        assert_eq!(
            run("Zoom\n A"),
            Some((None, "Zoom".into(), vec!["A".into()]))
        );
        assert_eq!(
            run("Zoom\nA B"),
            Some((None, "Zoom".into(), vec!["A".into(), "B".into()]))
        );
    }

    #[test]
    fn prefix_only_at_position_zero() {
        assert_eq!(run(" _Zoom"), Some((None, "Zoom".into(), vec![])));
        assert_eq!(
            run("'\t_ x"),
            Some((Some("'".into()), "_".into(), vec!["x".into()]))
        );
        assert_eq!(
            run("'\tZoom"),
            Some((Some("'".into()), "Zoom".into(), vec![]))
        );
        assert_eq!(run(" _ x"), Some((None, "_".into(), vec!["x".into()])));
        assert_eq!(
            run("'\t_ Zoom"),
            Some((Some("'".into()), "_".into(), vec!["Zoom".into()]))
        );
        assert_eq!(
            run("'\nZoom All"),
            Some((Some("'".into()), "Zoom".into(), vec!["All".into()]))
        );
        assert_eq!(run("' a"), Some((Some("'".into()), "a".into(), vec![])));
        assert_eq!(
            run("' a b"),
            Some((Some("'".into()), "a".into(), vec!["b".into()]))
        );
        assert_eq!(run("!x"), Some((Some("!".into()), "x".into(), vec![])));
        assert_eq!(
            run("! x y"),
            Some((Some("!".into()), "x".into(), vec!["y".into()]))
        );
        assert_eq!(
            run("_ _ _"),
            Some((Some("_".into()), "_".into(), vec!["".into()]))
        );
        assert_eq!(
            run("_ _ _ x"),
            Some((Some("_".into()), "_".into(), vec!["".into(), "x".into()]))
        );
        assert_eq!(run("x_ _"), Some((None, "x_".into(), vec!["".into()])));
        assert_eq!(
            run("x _ y"),
            Some((None, "x".into(), vec!["".into(), "y".into()]))
        );
        assert_eq!(run("a_"), Some((None, "a_".into(), vec![])));
        assert_eq!(run("a _"), Some((None, "a".into(), vec!["".into()])));
        assert_eq!(run("a  _"), Some((None, "a".into(), vec!["".into()])));
        assert_eq!(run("'a"), Some((Some("'".into()), "a".into(), vec![])));
        assert_eq!(run("Zoom_"), Some((None, "Zoom_".into(), vec![])));
        assert_eq!(
            run("_Zoom_"),
            Some((Some("_".into()), "Zoom_".into(), vec![]))
        );
        assert_eq!(run("_a_b"), Some((Some("_".into()), "a_b".into(), vec![])));
    }

    /// The bound must be enforced in `usize` BEFORE the input pointer is read:
    /// a length above `u32::MAX` must never be truncated by `as u32` (which
    /// would wrap to 0 and pass the bound) and must never be dereferenced.
    #[test]
    fn huge_length_returns_bound_without_reading_the_pointer() {
        unsafe {
            // A huge length that wraps to a small u32 when cast: 1 << 32.
            let huge: usize = 1 << 32;
            assert!(huge as u32 == 0);
            // Aligned, dangling pointer: it must never be read on this path.
            let dangling: *const u32 = std::ptr::NonNull::<u32>::dangling().as_ptr();
            assert!((dangling as usize).is_multiple_of(std::mem::align_of::<u32>()));
            let mut reply = Reply::default();
            let rc = narwhal_pure_core_alias_parse(
                dangling,
                huge,
                151,
                &mut reply,
                std::mem::size_of::<Reply>(),
                std::ptr::null_mut(),
                0,
            );
            assert_eq!(rc, 2);
            assert_eq!(reply.code, CODE_BOUND);

            // The options entry point must take the same path.
            let rc = narwhal_pure_core_alias_options(dangling, huge, 0, std::ptr::null_mut(), 0);
            assert_eq!(rc, 2);
        }
    }
}
