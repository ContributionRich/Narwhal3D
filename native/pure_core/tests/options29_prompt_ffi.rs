//! OPTIONS-PROMPT-29 (FFI packet) — tests for the prompt C ABI export.
//!
//! Exercises the **actual exported**
//! `narwhal_pure_core_options_prompt` against the `options_prompt` engine,
//! validating:
//!   * positive shapes — empty base, nonempty, trailing-colon base, enum
//!     (mode 1) untrimmed base, Unicode whitespace, surrogate codepoints, and
//!     the field/label metadata (`field == Some(i)` vs punctuation `u32::MAX`);
//!   * the ABI contract — malformed (bad mode, truncation, not-exactly-consumed,
//!     invalid codepoint, null/misaligned pointer), bound (word_count, base/field
//!     length, field_count), short capacity, null `out` for a non-empty result,
//!     and **no writes to `out`/`out_count` on any error** — including every
//!     pairwise overlap of `words`/`out`/`out_count`.
//!
//! Compiled standalone:
//!   rustc --edition=2021 --test tests/options29_prompt_ffi.rs -o /tmp/opts29pffi
//!
//! (In-crate it is a normal integration test importing the public modules.)

// Imports the public prompt FFI from the built crate (`lib.rs` declares
// `pub mod options_ffi` and `pub mod options_prompt`); no `#[path]` copies of
// the source are kept.

use narwhal_pure_core::options_ffi::{
    narwhal_pure_core_options_prompt, OPTIONS_BOUND, OPTIONS_MALFORMED, OPTIONS_OK,
    OPTIONS_SHORT_OUTPUT,
};

/// Sentinel for "the buffer was never written" (checked on every error path).
const SENT: u32 = 0xDEAD_BEEF;
/// Sentinel for "out_count was never written".
const COUNTS: usize = 0xC0FFEE;

// ---------------------------------------------------------------------------
// protocol encoders
// ---------------------------------------------------------------------------

/// Encode `[base_len, base cps..., field_count, for each field: len, cps...]`.
fn encode_prompt(base: &[u32], fields: &[&[u32]]) -> Vec<u32> {
    let mut w = Vec::new();
    w.push(base.len() as u32);
    w.extend_from_slice(base);
    w.push(fields.len() as u32);
    for f in fields {
        w.push(f.len() as u32);
        w.extend_from_slice(f);
    }
    w
}

/// Slice-based call returning `(status, out buffer, out_count)`. The `out`
/// buffer is pre-filled with `SENT` so any "no write" can be detected.
fn call(words: &[u32], mode: u32, capacity: usize) -> (i32, Vec<u32>, usize) {
    let mut out = vec![SENT; capacity];
    let mut cnt = COUNTS;
    let rc = unsafe {
        narwhal_pure_core_options_prompt(
            words.as_ptr(),
            words.len(),
            mode,
            out.as_mut_ptr(),
            capacity,
            &mut cnt,
        )
    };
    (rc, out, cnt)
}

/// Build the exact expected output words for `want`, mirroring the ABI.
fn expect_words(want: &[(Option<usize>, &[u32])]) -> Vec<u32> {
    let mut out = vec![want.len() as u32];
    for (f, t) in want {
        out.push(match f {
            Some(i) => *i as u32,
            None => u32::MAX,
        });
        out.push(t.len() as u32);
        out.extend_from_slice(t);
    }
    out
}

fn expect_ok(words: &[u32], mode: u32, want: &[(Option<usize>, &[u32])]) {
    let wantv = expect_words(want);
    let (rc, out, cnt) = call(words, mode, wantv.len());
    assert_eq!(rc, OPTIONS_OK, "expected success");
    assert_eq!(cnt, wantv.len(), "out_count must equal the result length");
    let got: Vec<u32> = out.iter().take(cnt).copied().collect();
    assert_eq!(got, wantv, "result words; got={:?}", got);
}

// ===========================================================================
// positive
// ===========================================================================

#[test]
fn prompt_empty_base_zero_fields_mode0_zero_segments() {
    // base="", fields=[] -> engine returns zero segments -> output is just
    // `[segment_count=0]` (one word). The prompt output is never truly empty:
    // it always carries the leading segment_count.
    let words = encode_prompt(&[], &[]);
    let (rc, out, cnt) = call(&words, 0, 4);
    assert_eq!(rc, OPTIONS_OK);
    assert_eq!(cnt, 1, "zero segments -> one word (segment_count)");
    assert_eq!(out[0], 0, "segment_count == 0");
}

#[test]
fn prompt_base_no_colon_zero_fields() {
    // base="Point", fields=[] -> [("Point", None)]
    let base: [u32; 5] = [0x50, 0x6F, 0x69, 0x6E, 0x74];
    let words = encode_prompt(&base, &[]);
    expect_ok(&words, 0, &[(None, &[0x50, 0x6F, 0x69, 0x6E, 0x74])]);
}

#[test]
fn prompt_base_no_colon_one_field() {
    // base="Point", fields=["A"] -> [("Point ( ",None),("A",0),(" )",None)]
    let base: [u32; 5] = [0x50, 0x6F, 0x69, 0x6E, 0x74];
    let f: [u32; 1] = [0x41];
    let words = encode_prompt(&base, &[&f]);
    expect_ok(
        &words,
        0,
        &[
            (None, &[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]),
            (Some(0), &[0x41]),
            (None, &[0x20, 0x29]),
        ],
    );
}

#[test]
fn prompt_base_colon_two_fields_labels() {
    // base="Point:", fields=["A","B"]
    // -> head "Point ( ", A(0), sep, B(1), tail " ):"
    let base: [u32; 6] = [0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3A];
    let a: [u32; 1] = [0x41];
    let b: [u32; 1] = [0x42];
    let words = encode_prompt(&base, &[&a, &b]);
    expect_ok(
        &words,
        0,
        &[
            (None, &[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]),
            (Some(0), &[0x41]),
            (None, &[0x20, 0x20]),
            (Some(1), &[0x42]),
            (None, &[0x20, 0x29, 0x3A]),
        ],
    );
}

#[test]
fn prompt_unicode_whitespace_base_stripped() {
    // base="\u{1C}Point\u{A0}", fields=["X"] -> trimmed "Point ( X )"
    let base: [u32; 7] = [0x1C, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0xA0];
    let f: [u32; 1] = [0x58];
    let words = encode_prompt(&base, &[&f]);
    expect_ok(
        &words,
        0,
        &[
            (None, &[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]),
            (Some(0), &[0x58]),
            (None, &[0x20, 0x29]),
        ],
    );
}

#[test]
fn prompt_surrogate_preserved_in_base_and_field() {
    // base="\u{D800}P", fields=["\u{D801}X"] — surrogates preserved as-is.
    let base: [u32; 2] = [0xD800, 0x50];
    let f: [u32; 2] = [0xD801, 0x58];
    let words = encode_prompt(&base, &[&f]);
    expect_ok(
        &words,
        0,
        &[
            (None, &[0xD800, 0x50, 0x20, 0x28, 0x20]),
            (Some(0), &[0xD801, 0x58]),
            (None, &[0x20, 0x29]),
        ],
    );
}

#[test]
fn prompt_enum_mode1_base_not_stripped() {
    // mode 1, base="  Point  " (untrimmed), fields=["A"]
    // -> head "  Point  ( " (base kept verbatim), tail " ):"
    let base: [u32; 9] = [0x20, 0x20, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x20];
    let f: [u32; 1] = [0x41];
    let words = encode_prompt(&base, &[&f]);
    expect_ok(
        &words,
        1,
        &[
            (
                None,
                &[
                    0x20, 0x20, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x20, 0x20, 0x28, 0x20,
                ],
            ),
            (Some(0), &[0x41]),
            (None, &[0x20, 0x29, 0x3A]),
        ],
    );
}

#[test]
fn prompt_enum_mode1_zero_fields() {
    // mode 1, base="Point", fields=[] -> head "Point ( ", tail " ):"
    let base: [u32; 5] = [0x50, 0x6F, 0x69, 0x6E, 0x74];
    let words = encode_prompt(&base, &[]);
    expect_ok(
        &words,
        1,
        &[
            (None, &[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]),
            (None, &[0x20, 0x29, 0x3A]),
        ],
    );
}

// ===========================================================================
// malformed
// ===========================================================================

#[test]
fn prompt_bad_mode() {
    let words = encode_prompt(&[0x50], &[&[0x41]]);
    let (rc, _out, cnt) = call(&words, 2, 8);
    assert_eq!(rc, OPTIONS_MALFORMED, "mode 2 is invalid");
    assert_eq!(cnt, COUNTS, "no write on bad mode");
}

#[test]
fn prompt_bad_mode_checked_before_bounds() {
    // Even an oversized base, bad mode wins.
    let words = vec![1_000_000u32]; // base_len far beyond any bound
    let (rc, _out, cnt) = call(&words, 7, 8);
    assert_eq!(
        rc, OPTIONS_MALFORMED,
        "mode 7 is invalid even with huge base_len"
    );
    assert_eq!(cnt, COUNTS);
}

#[test]
fn prompt_truncated_base() {
    // base_len=5 but only 2 base cps supplied, then field_count... -> truncation
    let w = vec![5u32, 0x50, 0x6F, 0]; // base_len=5, only 2 cps, field_count=0
    let (rc, _out, cnt) = call(&w, 0, 8);
    assert_eq!(rc, OPTIONS_MALFORMED, "truncated base -> malformed");
    assert_eq!(cnt, COUNTS);
}

#[test]
fn prompt_not_exactly_consumed() {
    // Valid prompt + one extra trailing word -> not consumed exactly.
    let mut w = encode_prompt(&[0x50], &[&[0x41]]);
    w.push(0xDEAD);
    let (rc, _out, cnt) = call(&w, 0, 8);
    assert_eq!(rc, OPTIONS_MALFORMED, "trailing word -> malformed");
    assert_eq!(cnt, COUNTS);
}

#[test]
fn prompt_invalid_codepoint() {
    // base contains 0x11_0000 (> 0x10_FFFF) -> invalid.
    let w = encode_prompt(&[0x11_0000], &[]);
    let (rc, _out, cnt) = call(&w, 0, 8);
    assert_eq!(rc, OPTIONS_MALFORMED, "codepoint > 0x10FFFF -> malformed");
    assert_eq!(cnt, COUNTS);
}

#[test]
fn prompt_empty_words() {
    // word_count == 0 -> no base_len word -> malformed.
    let (rc, _out, cnt) = call(&[], 0, 8);
    assert_eq!(rc, OPTIONS_MALFORMED, "empty words -> malformed");
    assert_eq!(cnt, COUNTS);
}

#[test]
fn prompt_null_words_nonempty() {
    let mut out = vec![SENT; 8];
    let mut cnt = COUNTS;
    let rc = unsafe {
        narwhal_pure_core_options_prompt(std::ptr::null(), 5, 0, out.as_mut_ptr(), 8, &mut cnt)
    };
    assert_eq!(
        rc, OPTIONS_MALFORMED,
        "null words with word_count>0 -> malformed"
    );
    assert_eq!(cnt, COUNTS);
}

#[test]
fn prompt_misaligned_words() {
    // A 4-aligned (but not 8-aligned) address for a u32 span is valid for u32;
    // instead use an odd address -> misaligned for u32.
    let buf = encode_prompt(&[0x50], &[&[0x41]]);
    let base = buf.as_ptr() as usize;
    let misaligned = (base + 1) as *const u32; // 4-byte misaligned
    let mut out = vec![SENT; 8];
    let mut cnt = COUNTS;
    let rc = unsafe {
        narwhal_pure_core_options_prompt(misaligned, buf.len(), 0, out.as_mut_ptr(), 8, &mut cnt)
    };
    assert_eq!(rc, OPTIONS_MALFORMED, "misaligned words -> malformed");
    assert_eq!(cnt, COUNTS);
}

// ===========================================================================
// bound
// ===========================================================================

#[test]
fn prompt_word_count_bound() {
    // word_count > MAX_WORDS (1_048_576) -> bound, checked before the pointer.
    let buf = encode_prompt(&[0x50], &[&[0x41]]);
    let base = buf.as_ptr() as usize;
    let mut out = vec![SENT; 8];
    let mut cnt = COUNTS;
    let rc = unsafe {
        narwhal_pure_core_options_prompt(
            buf.as_ptr(),
            1_048_577, // > MAX_WORDS
            0,
            out.as_mut_ptr(),
            8,
            &mut cnt,
        )
    };
    let _ = base;
    assert_eq!(rc, OPTIONS_BOUND, "word_count over MAX_WORDS -> bound");
    assert_eq!(cnt, COUNTS);
}

#[test]
fn prompt_base_len_bound() {
    // base_len = 4097 (> 4096) -> bound.
    let mut w = vec![4097u32];
    w.extend(std::iter::repeat_n(0x41, 4097)); // actually supply the cps
    w.push(0u32); // field_count
    let (rc, _out, cnt) = call(&w, 0, 8);
    assert_eq!(rc, OPTIONS_BOUND, "base_len 4097 -> bound");
    assert_eq!(cnt, COUNTS);
}

#[test]
fn prompt_field_count_bound() {
    // field_count = 65537 (> 65536) -> bound.
    let mut w = vec![0u32]; // base_len = 0
    w.push(65537u32); // field_count
    let (rc, _out, cnt) = call(&w, 0, 8);
    assert_eq!(rc, OPTIONS_BOUND, "field_count 65537 -> bound");
    assert_eq!(cnt, COUNTS);
}

#[test]
fn prompt_capacity_bound() {
    // Valid call but capacity > MAX_PROMPT_CAP_WORDS -> bound (and no write).
    let words = encode_prompt(&[0x50], &[&[0x41]]);
    let (rc, _out, cnt) = call(&words, 0, 2_097_153); // > MAX_PROMPT_CAP_WORDS
    assert_eq!(rc, OPTIONS_BOUND, "capacity over the prompt cap -> bound");
    assert_eq!(cnt, COUNTS);
}

// ===========================================================================
// short output
// ===========================================================================

#[test]
fn prompt_short_capacity() {
    // Result for base="Point", fields=["A"] is 3 segments:
    //   [3, MAX,8,<8>, 0,1,<1>, MAX,2,<2>] = 3 + (1+1+8) + (1+1+1) + (1+1+2) = 21 words
    let base: [u32; 5] = [0x50, 0x6F, 0x69, 0x6E, 0x74];
    let f: [u32; 1] = [0x41];
    let words = encode_prompt(&base, &[&f]);
    let (rc, out, cnt) = call(&words, 0, 5); // capacity 5 < 21
    assert_eq!(
        rc, OPTIONS_SHORT_OUTPUT,
        "capacity too small -> short output"
    );
    assert_eq!(cnt, COUNTS, "no out_count write on short output");
    assert!(
        out.iter().all(|&w| w == SENT),
        "out untouched on short output"
    );
}

#[test]
fn prompt_null_out_nonempty_result() {
    // Non-empty result but out is null with capacity > 0 -> malformed.
    let base: [u32; 5] = [0x50, 0x6F, 0x69, 0x6E, 0x74];
    let f: [u32; 1] = [0x41];
    let words = encode_prompt(&base, &[&f]);
    let mut cnt = COUNTS;
    let rc = unsafe {
        narwhal_pure_core_options_prompt(
            words.as_ptr(),
            words.len(),
            0,
            std::ptr::null_mut(),
            16, // capacity > 0 but out null
            &mut cnt,
        )
    };
    assert_eq!(
        rc, OPTIONS_MALFORMED,
        "null out with capacity>0 -> malformed"
    );
    assert_eq!(cnt, COUNTS);
}

// ===========================================================================
// overlap / alias (no writes on error)
// ===========================================================================

#[test]
fn prompt_out_aliases_words() {
    // Put the (would-be) output inside the input span -> alias -> malformed.
    let words = encode_prompt(&[0x50], &[&[0x41]]);
    let mut buf = words.clone();
    let _ = &mut buf; // keep alive
    let base = buf.as_mut_ptr();
    let mut cnt = COUNTS;
    let rc = unsafe { narwhal_pure_core_options_prompt(base, words.len(), 0, base, 8, &mut cnt) };
    assert_eq!(rc, OPTIONS_MALFORMED, "out aliasing words -> malformed");
    assert_eq!(cnt, COUNTS, "no write on overlap");
}

#[test]
fn prompt_out_count_aliases_words() {
    // `out_count` (a usize) strictly inside the words byte span, 8-aligned.
    let words = encode_prompt(&[0x50], &[&[0x41]]);
    let mut buf: Vec<u32> = words.clone();
    let _ = &mut buf;
    let base = buf.as_ptr() as usize;
    assert!(base.is_multiple_of(8), "Vec<u32> base must be 8-aligned");
    assert!(4 * words.len() > 8, "words span must exceed 8 bytes");
    let cnt_ptr = (base + 8) as *mut usize; // inside words span
    let mut out: Vec<u32> = vec![SENT; 8];
    let _ = &mut out;
    let rc = unsafe {
        narwhal_pure_core_options_prompt(buf.as_ptr(), words.len(), 0, out.as_mut_ptr(), 8, cnt_ptr)
    };
    assert_eq!(
        rc, OPTIONS_MALFORMED,
        "out_count aliasing words -> malformed"
    );
    assert!(out.iter().all(|&w| w == SENT), "out untouched");
}

#[test]
fn prompt_out_count_aliases_out() {
    // `out_count` strictly inside the `out` span (both 8-aligned).
    let words = encode_prompt(&[0x50], &[&[0x41]]);
    let mut buf: Vec<u32> = vec![SENT; 16];
    let _ = &mut buf;
    let base = buf.as_mut_ptr() as usize;
    assert!(base.is_multiple_of(8));
    let cnt_ptr = (base + 8) as *mut usize; // out span=[base,base+64), cnt in it
    let rc = unsafe {
        narwhal_pure_core_options_prompt(
            words.as_ptr(),
            words.len(),
            0,
            buf.as_mut_ptr(),
            16,
            cnt_ptr,
        )
    };
    assert_eq!(rc, OPTIONS_MALFORMED, "out_count aliasing out -> malformed");
    assert!(buf.iter().all(|&w| w == SENT), "out untouched");
}
