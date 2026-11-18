//! OPTIONS-ENGINE-29 (FFI packet) — a dependency-free C ABI over the pure
//! option-resolution engine (`options_owned`).
//!
//! This module is the layer a C host (e.g. a Python `ctypes` wrapper) calls.
//! It contains **no** dependency and **no** I/O, and uses only the engine's
//! public API. It makes **no no-panic promise**: a large but valid input may
//! still fail to allocate (an out-of-memory condition, not a logic error).
//! `lib.rs` is intentionally *not* edited: the
//! standalone test includes this file directly with `#[path]`, and `Main`
//! registers the module separately.
//!
//! # Exports
//!
//! * `narwhal_pure_core_options_abi() -> u32` — returns the ABI version (`1`).
//! * `narwhal_pure_core_options_resolve(words, word_count, op, out, capacity, out_count) -> i32`
//! * `narwhal_pure_core_options_prompt(words, word_count, mode, out, capacity, out_count) -> i32`
//!
//! # `op` codes
//!
//! * `0` — **assign**: one output word per option = the option's activation-letter
//!   **position** within that option's name, or `u32::MAX` when the option has no
//!   letter. The query words are present and validated but ignored.
//! * `1` — **resolve**: output = the selected **option indices** (0 = none,
//!   1 = set, >1 = ambiguous).
//! * `2` — **enum**: output = the selected **choice indices** (0 = none,
//!   1 = set, >1 = ambiguous).
//!
//! # Word protocol (`u32` words, in order)
//!
//! ```text
//! [ name_count,
//!   for each name:
//!     original_len, original_codepoints...,
//!     folded_len,   folded_codepoints...,
//!     for each original char: alpha(0|1), fold_len, fold_codepoints...,
//!   query:
//!     original_len, is_digit(0|1), folded_len, folded_codepoints... ]
//! ```
//!
//! The engine is Unicode-agnostic: it uses the host-supplied `folded`, per-char
//! fold, and `alpha`/`is_digit` exactly as given. Lone surrogates and non-BMP
//! codepoints are therefore preserved and are **never** restricted here.
//!
//! # Return codes
//!
//! * `0`  — success; `out[0..*out_count]` holds the result and `*out_count` is set.
//! * `-1` — malformed: bad `op`, null/misaligned/overflowing pointer, invalid
//!   codepoint or flag, or the words were not consumed exactly.
//! * `-2` — bound: `word_count`, `name_count`, or a name/query length exceeds a
//!   declared limit.
//! * `-3` — short output: `out` is valid but `capacity` is smaller than the result.
//!
//! # C contract (caller obligations)
//!
//! * `words` must reference `word_count` initialized `u32` (null only if `word_count == 0`).
//! * `out` must reference `capacity` writable `u32`; it may be **null only when
//!   `capacity == 0`** (an empty buffer). A non-empty result additionally needs
//!   `capacity >=` the result length (else `-3`).
//! * `out_count` must be a writable, aligned `usize`.
//! * **No two of `words`, `out`, and `out_count` may share a byte** (their byte
//!   ranges are pairwise disjoint), whether or not the result is empty.
//! * On **any** error code the function performs **no** writes to `out` or `out_count`.

use std::slice;

use crate::options_owned::{self, Name, Query};
use crate::options_prompt;

/// Stable ABI version for the options interface.
pub const OPTIONS_ABI_VERSION: u32 = 1;
/// Success.
pub const OPTIONS_OK: i32 = 0;
/// Malformed: bad `op`, null/misaligned/overflowing pointer, invalid codepoint or
/// flag, or the words were not consumed exactly.
pub const OPTIONS_MALFORMED: i32 = -1;
/// A declared bound (`word_count`, `name_count`, or a name/query length) was exceeded.
pub const OPTIONS_BOUND: i32 = -2;
/// `out` is valid but too short for the result.
pub const OPTIONS_SHORT_OUTPUT: i32 = -3;

/// Maximum total words in one call (4 MiB of `u32`).
const MAX_WORDS: usize = 1_048_576;
/// Maximum codepoints in a name (mirrors the engine's `MAX_NAME_CODEPOINTS`).
const MAX_NAME: usize = options_owned::MAX_NAME_CODEPOINTS;
/// Maximum codepoints in a query (mirrors the engine's `MAX_QUERY_CODEPOINTS`).
const MAX_QUERY: usize = options_owned::MAX_QUERY_CODEPOINTS;
/// Maximum options in a set (mirrors the engine's `MAX_OPTIONS`).
const MAX_OPTIONS: u32 = options_owned::MAX_OPTIONS as u32;
/// Maximum output words for a prompt result (deterministic bound, checked
/// before any output span is formed). A valid prompt's result can never
/// exceed this, so a larger `capacity` is a caller error.
const MAX_PROMPT_CAP_WORDS: usize = 2_097_152;

// ---------------------------------------------------------------------------
// local slice helpers (compatible with `crate::checked_slice` in `lib.rs`;
// duplicated locally so this module stays standalone-compilable)
// ---------------------------------------------------------------------------

fn raw_span_is_valid<T>(ptr: *const T, len: usize) -> bool {
    if len == 0 {
        return true;
    }
    if ptr.is_null() || !(ptr as usize).is_multiple_of(std::mem::align_of::<T>()) {
        return false;
    }
    let Some(bytes) = len.checked_mul(std::mem::size_of::<T>()) else {
        return false;
    };
    bytes <= isize::MAX as usize && (ptr as usize).checked_add(bytes).is_some()
}

/// Form a shared slice only after validating all ABI-controlled metadata.
///
/// # Safety
///
/// For non-empty spans, `ptr` must reference `len` initialized `T` values.
unsafe fn checked_slice<'a, T>(ptr: *const T, len: usize) -> Option<&'a [T]> {
    if len == 0 {
        return Some(&[]);
    }
    if !raw_span_is_valid(ptr, len) {
        return None;
    }
    Some(unsafe { slice::from_raw_parts(ptr, len) })
}

// ---------------------------------------------------------------------------
// checked word reader (all span arithmetic is bounds-checked; no panics)
// ---------------------------------------------------------------------------

struct Reader<'a> {
    words: &'a [u32],
    pos: usize,
}

impl<'a> Reader<'a> {
    fn new(words: &'a [u32]) -> Self {
        Self { words, pos: 0 }
    }
    fn remaining(&self) -> usize {
        self.words.len() - self.pos
    }
    /// Read exactly one word; `None` if truncated.
    fn take1(&mut self) -> Option<u32> {
        if self.remaining() < 1 {
            return None;
        }
        let w = self.words[self.pos];
        self.pos += 1;
        Some(w)
    }
    /// Read exactly `n` words; `None` if truncated.
    fn take(&mut self, n: usize) -> Option<&'a [u32]> {
        if self.remaining() < n {
            return None;
        }
        let s = &self.words[self.pos..self.pos + n];
        self.pos += n;
        Some(s)
    }
    /// True when every word has been consumed (required by the protocol).
    fn consumed_all(&self) -> bool {
        self.pos == self.words.len()
    }
}

// ---------------------------------------------------------------------------
// protocol decoding
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DecodeErr {
    Malformed,
    Bound,
}

/// A valid codepoint (lone surrogates and non-BMP are allowed).
fn valid_cp(w: &u32) -> bool {
    *w <= 0x10_FFFF
}

fn decode_name(r: &mut Reader) -> Result<Name, DecodeErr> {
    let original_len = r.take1().ok_or(DecodeErr::Malformed)? as usize;
    if original_len > MAX_NAME {
        return Err(DecodeErr::Bound);
    }
    let original = r.take(original_len).ok_or(DecodeErr::Malformed)?;
    if !original.iter().all(valid_cp) {
        return Err(DecodeErr::Malformed);
    }
    let folded_len = r.take1().ok_or(DecodeErr::Malformed)? as usize;
    if folded_len > MAX_NAME {
        return Err(DecodeErr::Bound);
    }
    let folded = r.take(folded_len).ok_or(DecodeErr::Malformed)?;
    if !folded.iter().all(valid_cp) {
        return Err(DecodeErr::Malformed);
    }
    let mut per_char = Vec::with_capacity(original_len);
    let mut alphabetic = Vec::with_capacity(original_len);
    for _ in 0..original_len {
        let alpha = r.take1().ok_or(DecodeErr::Malformed)?;
        if alpha > 1 {
            return Err(DecodeErr::Malformed);
        }
        let fold_len = r.take1().ok_or(DecodeErr::Malformed)? as usize;
        if fold_len > MAX_NAME {
            return Err(DecodeErr::Bound);
        }
        let fold = r.take(fold_len).ok_or(DecodeErr::Malformed)?;
        if !fold.iter().all(valid_cp) {
            return Err(DecodeErr::Malformed);
        }
        per_char.push(fold.to_vec());
        alphabetic.push(alpha == 1);
    }
    Ok(Name {
        original: original.to_vec(),
        folded: folded.to_vec(),
        per_char_folded: per_char,
        alphabetic,
    })
}

fn decode_query(r: &mut Reader) -> Result<Query, DecodeErr> {
    let original_len = r.take1().ok_or(DecodeErr::Malformed)? as usize;
    if original_len > MAX_QUERY {
        return Err(DecodeErr::Bound);
    }
    let is_digit = r.take1().ok_or(DecodeErr::Malformed)?;
    if is_digit > 1 {
        return Err(DecodeErr::Malformed);
    }
    let folded_len = r.take1().ok_or(DecodeErr::Malformed)? as usize;
    if folded_len > MAX_QUERY {
        return Err(DecodeErr::Bound);
    }
    let folded = r.take(folded_len).ok_or(DecodeErr::Malformed)?;
    if !folded.iter().all(valid_cp) {
        return Err(DecodeErr::Malformed);
    }
    Ok(Query {
        folded: folded.to_vec(),
        original_len,
        is_digit: is_digit == 1,
    })
}

/// Decode the full protocol, requiring exact consumption of the words.
fn decode(words: &[u32]) -> Result<(Vec<Name>, Query), DecodeErr> {
    let mut r = Reader::new(words);
    let name_count = r.take1().ok_or(DecodeErr::Malformed)?;
    if name_count > MAX_OPTIONS {
        return Err(DecodeErr::Bound);
    }
    let mut names = Vec::new();
    for _ in 0..name_count as usize {
        names.push(decode_name(&mut r)?);
    }
    let query = decode_query(&mut r)?;
    if !r.consumed_all() {
        return Err(DecodeErr::Malformed);
    }
    Ok((names, query))
}

/// Decode the prompt protocol: `[base_len, base cps..., field_count, for each
/// field: len, cps...]`, requiring exact consumption. Lone surrogates and
/// non-BMP codepoints are valid; the base/field lengths are bounds-checked.
fn decode_prompt(r: &mut Reader) -> Result<(Vec<u32>, Vec<Vec<u32>>), DecodeErr> {
    let base_len = r.take1().ok_or(DecodeErr::Malformed)? as usize;
    if base_len > options_prompt::MAX_PROMPT_TEXT_CODEPOINTS {
        return Err(DecodeErr::Bound);
    }
    let base = r.take(base_len).ok_or(DecodeErr::Malformed)?;
    if !base.iter().all(valid_cp) {
        return Err(DecodeErr::Malformed);
    }
    let field_count = r.take1().ok_or(DecodeErr::Malformed)?;
    if field_count > options_prompt::MAX_PROMPT_FIELDS as u32 {
        return Err(DecodeErr::Bound);
    }
    let mut fields = Vec::with_capacity(field_count as usize);
    for _ in 0..field_count as usize {
        let len = r.take1().ok_or(DecodeErr::Malformed)? as usize;
        if len > options_prompt::MAX_PROMPT_TEXT_CODEPOINTS {
            return Err(DecodeErr::Bound);
        }
        let field = r.take(len).ok_or(DecodeErr::Malformed)?;
        if !field.iter().all(valid_cp) {
            return Err(DecodeErr::Malformed);
        }
        fields.push(field.to_vec());
    }
    if !r.consumed_all() {
        return Err(DecodeErr::Malformed);
    }
    Ok((base.to_vec(), fields))
}

// ---------------------------------------------------------------------------
// engine dispatch -> output words
// ---------------------------------------------------------------------------

fn engine_err(e: options_owned::Error) -> i32 {
    match e {
        options_owned::Error::ShapeMismatch => OPTIONS_MALFORMED,
        _ => OPTIONS_BOUND,
    }
}

fn result_words(op: u32, names: &[Name], query: &Query) -> Result<Vec<u32>, i32> {
    match op {
        0 => options_owned::assign(names).map_err(engine_err).map(|pos| {
            pos.iter()
                .map(|p| p.map(|i| i as u32).unwrap_or(u32::MAX))
                .collect()
        }),
        1 => options_owned::resolve(names, query)
            .map_err(engine_err)
            .map(|res| res.option_indices.iter().map(|i| *i as u32).collect()),
        2 => options_owned::resolve_enum(names, query)
            .map_err(engine_err)
            .map(|res| res.choice_indices.iter().map(|i| *i as u32).collect()),
        _ => Err(OPTIONS_MALFORMED),
    }
}

/// Map the prompt engine's segments to the output word protocol:
/// `[segment_count, for each segment: field_index (or u32::MAX for
/// punctuation), text_len, text_codepoints...]`.
fn prompt_result_words(segments: &[options_prompt::Segment]) -> Vec<u32> {
    let mut out = Vec::new();
    out.push(segments.len() as u32);
    for s in segments {
        out.push(s.field.map(|i| i as u32).unwrap_or(u32::MAX));
        out.push(s.text.len() as u32);
        out.extend_from_slice(&s.text);
    }
    out
}

/// The byte range `[base, base + count*size)` for a span, or `None` when the
/// end address is not representable (overflow -> treated as unsafe by callers).
/// Uses checked arithmetic and the true element size (no hardcoded stride).
fn span_byte_range(base: usize, count: usize, size: usize) -> Option<(usize, usize)> {
    let bytes = count.checked_mul(size)?;
    let end = base.checked_add(bytes)?;
    Some((base, end))
}

/// True when two byte ranges overlap. A range that cannot be bounded (overflow)
/// is treated as unsafe (overlap); an empty range overlaps nothing.
fn ranges_overlap(a: Option<(usize, usize)>, b: Option<(usize, usize)>) -> bool {
    let (Some((a0, a1)), Some((b0, b1))) = (a, b) else {
        return true;
    };
    if a0 == a1 || b0 == b1 {
        return false;
    }
    a0 < b1 && b0 < a1
}

// ---------------------------------------------------------------------------
// exports
// ---------------------------------------------------------------------------

/// Return the stable ABI version for the options interface.
#[no_mangle]
pub extern "C" fn narwhal_pure_core_options_abi() -> u32 {
    OPTIONS_ABI_VERSION
}

/// Assign (`op=0`), resolve (`op=1`), or enum-resolve (`op=2`) per the module
/// word protocol. See the module docs for return codes and the C contract.
///
/// # Safety
///
/// * `words` must reference `word_count` initialized `u32` (null only if `word_count == 0`).
/// * `out` must reference `capacity` writable `u32` (null only if `capacity == 0`).
/// * `out_count` must be a writable, aligned `usize`.
/// * No two of the `words`, `out`, and `out_count` spans may share a byte.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_options_resolve(
    words: *const u32,
    word_count: usize,
    op: u32,
    out: *mut u32,
    capacity: usize,
    out_count: *mut usize,
) -> i32 {
    // 1. a bad mode is malformed (checked before anything else).
    if op > 2 {
        return OPTIONS_MALFORMED;
    }
    // 2. total-words bound, checked before touching the pointer so a huge count
    //    cannot cause a large read or allocation.
    if word_count > MAX_WORDS {
        return OPTIONS_BOUND;
    }
    // 3. validate the input span before forming a slice (null / alignment /
    //    isize-representable / no address wrap).
    let words_slice = match unsafe { checked_slice::<u32>(words, word_count) } {
        Some(s) => s,
        None => return OPTIONS_MALFORMED,
    };
    // 4. decode the protocol (bounds + valid codepoints/flags + exact consumption).
    let (names, query) = match decode(words_slice) {
        Ok(v) => v,
        Err(DecodeErr::Malformed) => return OPTIONS_MALFORMED,
        Err(DecodeErr::Bound) => return OPTIONS_BOUND,
    };
    // 5. compute the result (all input is decoded before any mutable output).
    let result = match result_words(op, &names, &query) {
        Ok(v) => v,
        Err(code) => return code,
    };

    // 6. Validate the OUTPUT metadata before writing anything, and before the
    //    empty-result early return. No pointer is dereferenced on any error.
    //
    // `out_count` is always written (even for an empty result), so it must be a
    // valid, aligned one-element `usize` span.
    if !raw_span_is_valid(out_count as *const usize, 1) {
        return OPTIONS_MALFORMED;
    }

    // A non-representable / oversized `capacity` is a deterministic bound error,
    // checked before any output pointer span is formed. The result can never
    // exceed `MAX_OPTIONS` (one word per option), so a larger capacity is a
    // caller error.
    if capacity > MAX_OPTIONS as usize {
        return OPTIONS_BOUND;
    }

    // `out` may be null only when `capacity == 0` (an empty buffer). A non-null
    // `out` must reference `capacity` writable `u32`.
    if out.is_null() {
        if capacity != 0 {
            return OPTIONS_MALFORMED;
        }
    } else if !raw_span_is_valid(out as *const u32, capacity) {
        return OPTIONS_MALFORMED;
    }

    // No two of {words, out, out_count} may share a byte. Checked byte ranges
    // (true element sizes, checked arithmetic); an unbounded range is treated
    // as overlap (unsafe). A null `out` (capacity 0) is an empty range.
    let words_range = span_byte_range(words as usize, word_count, std::mem::size_of::<u32>());
    let out_range = if out.is_null() {
        Some((0, 0))
    } else {
        span_byte_range(out as usize, capacity, std::mem::size_of::<u32>())
    };
    let count_range = span_byte_range(out_count as usize, 1, std::mem::size_of::<usize>());
    if ranges_overlap(words_range, out_range)
        || ranges_overlap(words_range, count_range)
        || ranges_overlap(out_range, count_range)
    {
        return OPTIONS_MALFORMED;
    }

    let result_len = result.len();
    if result_len == 0 {
        // Empty result: nothing is written to `out`; record the zero count.
        unsafe {
            *out_count = 0;
        }
        return OPTIONS_OK;
    }

    // Non-empty result: the (validated) `out` span must hold the result.
    if capacity < result_len {
        return OPTIONS_SHORT_OUTPUT;
    }

    // All checks passed: write the result, then the count.
    let out_slice = unsafe { slice::from_raw_parts_mut(out, capacity) };
    out_slice[..result_len].copy_from_slice(&result);
    unsafe {
        *out_count = result_len;
    }
    OPTIONS_OK
}

/// Assemble a CAD-style prompt line into clickable segments. `mode == 0` is an
/// ordinary prompt (base `strip`-ed); `mode == 1` is an enum value prompt (base
/// **not** stripped). See the module docs for the shared return codes and C
/// contract, which is identical to [`narwhal_pure_core_options_resolve`].
///
/// # Word protocol (input, `u32` words in order)
///
/// ```text
/// [ base_len, base_codepoints...,
///   field_count,
///   for each field: len, codepoints... ]
/// ```
///
/// # Output words (on success, `out[0..*out_count]`)
///
/// ```text
/// [ segment_count,
///   for each segment: field_index (u32::MAX for punctuation), text_len,
///     text_codepoints... ]
/// ```
///
/// # Safety
///
/// * `words` must reference `word_count` initialized `u32` (null only if `word_count == 0`).
/// * `out` must reference `capacity` writable `u32` (null only if `capacity == 0`).
/// * `out_count` must be a writable, aligned `usize`.
/// * No two of the `words`, `out`, and `out_count` spans may share a byte.
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_options_prompt(
    words: *const u32,
    word_count: usize,
    mode: u32,
    out: *mut u32,
    capacity: usize,
    out_count: *mut usize,
) -> i32 {
    // 1. a bad mode is malformed (checked before anything else).
    if mode > 1 {
        return OPTIONS_MALFORMED;
    }
    // 2. total-words bound, checked before touching the pointer.
    if word_count > MAX_WORDS {
        return OPTIONS_BOUND;
    }
    // 3. validate the input span before forming a slice.
    let words_slice = match unsafe { checked_slice::<u32>(words, word_count) } {
        Some(s) => s,
        None => return OPTIONS_MALFORMED,
    };
    // 4. decode the prompt protocol (bounds + valid codepoints + exact
    //    consumption) before any output is validated.
    let mut r = Reader::new(words_slice);
    let (base, fields) = match decode_prompt(&mut r) {
        Ok(v) => v,
        Err(DecodeErr::Malformed) => return OPTIONS_MALFORMED,
        Err(DecodeErr::Bound) => return OPTIONS_BOUND,
    };
    // 5. compute the result (all input decoded before any mutable output).
    let segments = match options_prompt::assemble(&base, &fields, mode) {
        Ok(v) => v,
        Err(options_prompt::Error::TextTooLong)
        | Err(options_prompt::Error::TooManyFields)
        | Err(options_prompt::Error::TooManyTotal) => return OPTIONS_BOUND,
        Err(options_prompt::Error::BadMode) => return OPTIONS_MALFORMED,
    };
    let result = prompt_result_words(&segments);

    // 6. Validate the OUTPUT metadata before writing anything, and before the
    //    empty-result early return. No pointer is dereferenced on any error.
    if !raw_span_is_valid(out_count as *const usize, 1) {
        return OPTIONS_MALFORMED;
    }
    if capacity > MAX_PROMPT_CAP_WORDS {
        return OPTIONS_BOUND;
    }
    if out.is_null() {
        if capacity != 0 {
            return OPTIONS_MALFORMED;
        }
    } else if !raw_span_is_valid(out as *const u32, capacity) {
        return OPTIONS_MALFORMED;
    }

    // No two of {words, out, out_count} may share a byte.
    let words_range = span_byte_range(words as usize, word_count, std::mem::size_of::<u32>());
    let out_range = if out.is_null() {
        Some((0, 0))
    } else {
        span_byte_range(out as usize, capacity, std::mem::size_of::<u32>())
    };
    let count_range = span_byte_range(out_count as usize, 1, std::mem::size_of::<usize>());
    if ranges_overlap(words_range, out_range)
        || ranges_overlap(words_range, count_range)
        || ranges_overlap(out_range, count_range)
    {
        return OPTIONS_MALFORMED;
    }

    let result_len = result.len();
    if result_len == 0 {
        unsafe {
            *out_count = 0;
        }
        return OPTIONS_OK;
    }
    if capacity < result_len {
        return OPTIONS_SHORT_OUTPUT;
    }
    let out_slice = unsafe { slice::from_raw_parts_mut(out, capacity) };
    out_slice[..result_len].copy_from_slice(&result);
    unsafe {
        *out_count = result_len;
    }
    OPTIONS_OK
}
