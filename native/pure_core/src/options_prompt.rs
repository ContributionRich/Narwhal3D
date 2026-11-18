//! OPTIONS-PROMPT-29 — command-line prompt assembly, pure engine.
//!
//! This is the Rust core of `narwhal3d/options.py`'s **prompt rendering**: the
//! two functions `format_prompt` and `prompt_segments`, reproduced exactly.
//! Given a base prompt and a list of *already-rendered* option strings, it
//! returns the ordered segments that, joined, are the full prompt line.
//!
//! It is **pure and deterministic** — no I/O, no panics. It owns the
//! *assembly* (bracket placement, trim, separator, trailing colon) and returns
//! typed segments so the host can hit-test clicks. It deliberately owns
//! **none** of the following:
//!
//! * **value rendering** (`format_option` — the `Name=Value`, `Name=Bool`,
//!   `%g` float and bare-MODE branches) — that reads live Python option
//!   objects and their values through Python protocols, so it stays in the
//!   host. The host renders each option to a string and passes the result in
//!   as a `fields` entry.
//! * **command state** (`self.opt`, `pending_option`, `mode`).
//!
//! # Unicode contract (host supplies the rendered text)
//!
//! The host renders each option with Python and passes the result as a
//! `Vec<u32>` of codepoints. `Vec<u32>` is used so **lone surrogates and
//! non-BMP codepoints are preserved as-is**; this module only compares and
//! joins the codepoints the host gave it. Whitespace classification reuses the
//! crate's `python_text::is_space` (CPython's `str.strip` set), so a base
//! trimmed here is trimmed identically to `options.py::format_prompt`.

// ---------------------------------------------------------------------------
// deterministic bounds (checked before any allocation -> Error, never a panic)
// ---------------------------------------------------------------------------

/// Maximum codepoints in the base prompt or in a single rendered field.
pub const MAX_PROMPT_TEXT_CODEPOINTS: usize = 4_096;

/// Maximum number of rendered fields in one prompt.
pub const MAX_PROMPT_FIELDS: usize = 65_536;

/// Maximum aggregate codepoints across the base and all fields (input). The
/// assembled output is bounded by this plus a small, fixed amount of
/// punctuation, so capping the input caps the output.
pub const MAX_PROMPT_TOTAL_CODEPOINTS: usize = 1_048_576;

// ---------------------------------------------------------------------------
// errors
// ---------------------------------------------------------------------------

/// A deterministic failure. The engine never panics: every out-of-bound or
/// malformed input maps to one of these.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    /// `mode` is neither 0 (ordinary prompt) nor 1 (enum value prompt).
    BadMode,
    /// The base, or a rendered field, exceeds
    /// [`MAX_PROMPT_TEXT_CODEPOINTS`].
    TextTooLong,
    /// More than [`MAX_PROMPT_FIELDS`] fields were supplied.
    TooManyFields,
    /// The aggregate input codepoints (base + all fields) exceed
    /// [`MAX_PROMPT_TOTAL_CODEPOINTS`].
    TooManyTotal,
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            Error::BadMode => "bad prompt mode",
            Error::TextTooLong => "prompt text too long",
            Error::TooManyFields => "too many prompt fields",
            Error::TooManyTotal => "too many total prompt codepoints",
        };
        f.write_str(s)
    }
}

impl std::error::Error for Error {}

// ---------------------------------------------------------------------------
// result type
// ---------------------------------------------------------------------------

/// One clickable piece of a prompt, in display order.
///
/// `field == Some(i)` marks the *i*-th rendered option (the segment the user
/// clicks); `field == None` marks surrounding punctuation — the base/head, the
/// two-space separators, and the closing ` )` / ` ):`. Joining every `text`
/// in order reproduces `options.py::format_prompt` exactly.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Segment {
    /// The codepoints of this piece, in order. Surrogates are preserved as-is.
    pub text: Vec<u32>,
    /// The 0-based index of the rendered field this segment shows, or `None`
    /// for punctuation (head, separator, tail).
    pub field: Option<usize>,
}

// ---------------------------------------------------------------------------
// whitespace helpers (reuse the crate's CPython-faithful `is_space`)
// ---------------------------------------------------------------------------

/// Whether a codepoint is whitespace under CPython's `str.strip` set, via the
/// crate's `python_text::is_space`. A surrogate or invalid codepoint (one that
/// is not a valid `char`) is **not** whitespace.
fn is_cp_space(cp: u32) -> bool {
    match char::from_u32(cp) {
        Some(c) => crate::python_text::is_space(c),
        None => false,
    }
}

/// `cp` with leading and trailing whitespace removed (Python `strip()`).
fn trim(cp: &[u32]) -> Vec<u32> {
    let mut start = 0;
    let mut end = cp.len();
    while start < end && is_cp_space(cp[start]) {
        start += 1;
    }
    while end > start && is_cp_space(cp[end - 1]) {
        end -= 1;
    }
    cp[start..end].to_vec()
}

/// `cp` with trailing whitespace removed (Python `rstrip()`).
fn rstrip(cp: &[u32]) -> Vec<u32> {
    let mut end = cp.len();
    while end > 0 && is_cp_space(cp[end - 1]) {
        end -= 1;
    }
    cp[..end].to_vec()
}

// ---------------------------------------------------------------------------
// public API
// ---------------------------------------------------------------------------

/// Assemble a CAD-style prompt line into clickable segments.
///
/// # Modes
///
/// * `mode == 0` — ordinary prompt (reproduces `options.py::format_prompt` /
///   `prompt_segments`). The base is `strip()`-ed. With **zero** fields the
///   result is `[]` when the trimmed base is empty, else a single
///   `[(base, None)]`. With fields the options go inside brackets before the
///   trailing colon: a base ending in `:` keeps the colon (head
///   `<base[:-1].rstrip()> ( `, tail ` ):`); otherwise head `<base> ( `,
///   tail ` )`.
/// * `mode == 1` — enum value prompt. The base is **not** stripped; head
///   `<base> ( `, tail ` ):` — even with zero fields.
///
/// In both modes the rendered `fields` are joined with a two-space separator
/// (`  `) between them, and each field segment carries its 0-based index.
///
/// # Errors
///
/// Returns [`Error::BadMode`] for any `mode` other than 0 or 1, and the
/// bound errors ([`Error::TextTooLong`], [`Error::TooManyFields`],
/// [`Error::TooManyTotal`]) for oversized input. No bound check can overflow:
/// every sum uses `checked_add`.
pub fn assemble(base: &[u32], fields: &[Vec<u32>], mode: u32) -> Result<Vec<Segment>, Error> {
    // Validate every bound *before* any allocation.
    if mode != 0 && mode != 1 {
        return Err(Error::BadMode);
    }
    if base.len() > MAX_PROMPT_TEXT_CODEPOINTS {
        return Err(Error::TextTooLong);
    }
    if fields.len() > MAX_PROMPT_FIELDS {
        return Err(Error::TooManyFields);
    }
    let mut total = base.len();
    for field in fields {
        if field.len() > MAX_PROMPT_TEXT_CODEPOINTS {
            return Err(Error::TextTooLong);
        }
        total = total.checked_add(field.len()).ok_or(Error::TooManyTotal)?;
    }
    if total > MAX_PROMPT_TOTAL_CODEPOINTS {
        return Err(Error::TooManyTotal);
    }

    // Fixed punctuation (codepoints).
    const OPEN: [u32; 3] = [0x20, 0x28, 0x20]; // " ( "
    const CLOSE: [u32; 2] = [0x20, 0x29]; // " )"
    const CLOSE_COLON: [u32; 3] = [0x20, 0x29, 0x3A]; // " ):"
    const SEP: [u32; 2] = [0x20, 0x20]; // "  "
    const COLON: u32 = 0x3A;

    if mode == 0 {
        let b = trim(base);
        if fields.is_empty() {
            return Ok(if b.is_empty() {
                Vec::new()
            } else {
                vec![Segment {
                    text: b,
                    field: None,
                }]
            });
        }
        let (head, tail) = if b.last() == Some(&COLON) {
            // base_without_last_colon.rstrip() + " ( "
            let r = rstrip(&b[..b.len() - 1]);
            let mut h = r;
            h.extend_from_slice(&OPEN);
            (h, CLOSE_COLON.to_vec())
        } else {
            let mut h = b.clone();
            h.extend_from_slice(&OPEN);
            (h, CLOSE.to_vec())
        };
        let mut out = Vec::with_capacity(fields.len() * 2 + 2);
        out.push(Segment {
            text: head,
            field: None,
        });
        for (i, field) in fields.iter().enumerate() {
            if i > 0 {
                out.push(Segment {
                    text: SEP.to_vec(),
                    field: None,
                });
            }
            out.push(Segment {
                text: field.clone(),
                field: Some(i),
            });
        }
        out.push(Segment {
            text: tail,
            field: None,
        });
        Ok(out)
    } else {
        // mode == 1: base NOT stripped.
        let mut head = Vec::with_capacity(base.len() + OPEN.len());
        head.extend_from_slice(base);
        head.extend_from_slice(&OPEN);
        let mut out = Vec::with_capacity(fields.len() * 2 + 2);
        out.push(Segment {
            text: head,
            field: None,
        });
        for (i, field) in fields.iter().enumerate() {
            if i > 0 {
                out.push(Segment {
                    text: SEP.to_vec(),
                    field: None,
                });
            }
            out.push(Segment {
                text: field.clone(),
                field: Some(i),
            });
        }
        out.push(Segment {
            text: CLOSE_COLON.to_vec(),
            field: None,
        });
        Ok(out)
    }
}
