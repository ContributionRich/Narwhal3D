//! OPTIONS-PROMPT-29 — tests for `src/options_prompt.rs` (the prompt engine).
//!
//! Imports the public engine from the built crate (`lib.rs` declares
//! `pub mod options_prompt`); no `#[path]` copies of the source are kept.
//!
//! Expected outcomes are of two kinds, both stated explicitly:
//! * **hand-derived** — worked out from the documented reference rules (the
//!   archived `options.py::format_prompt` / `prompt_segments`, lines 281–326
//!   of the privately archived original options module);
//! * **frozen cases** — the exact prompt shapes that the original `options.py`
//!   produces (the docstring example `Corner 1 ( Diagonal  Cube  Trim=Yes ):`,
//!   the `2Point`/`3Point` activation letters, the U+001C whitespace, the
//!   surrogate codepoint, the fullwidth colon, the enum untrimmed name).
//!
//! Run (from the crate directory):
//!   cargo test --test options29_prompt

use narwhal_pure_core::options_prompt::{
    assemble, Error, Segment, MAX_PROMPT_FIELDS, MAX_PROMPT_TEXT_CODEPOINTS,
    MAX_PROMPT_TOTAL_CODEPOINTS,
};

// ---------------------------------------------------------------------------
// private helpers
// ---------------------------------------------------------------------------

/// Build a `Segment` with `field == Some(i)`.
fn seg(text: &[u32], i: usize) -> Segment {
    Segment {
        text: text.to_vec(),
        field: Some(i),
    }
}

/// Build a `Segment` with `field == None` (punctuation).
fn punct(text: &[u32]) -> Segment {
    Segment {
        text: text.to_vec(),
        field: None,
    }
}

/// `cp` as a `Vec<u32>`.
fn v(cp: &[u32]) -> Vec<u32> {
    cp.to_vec()
}

/// Decode a `Vec<u32>` to a `String` (for display in test failures). Surrogates
/// become `U+XXXX`.
fn decode(cp: &[u32]) -> String {
    cp.iter()
        .map(|&c| match char::from_u32(c) {
            Some(ch) => ch.to_string(),
            None => format!("U+{:04X}", c),
        })
        .collect()
}

/// Assert two segment vectors are equal, with a readable diff.
fn assert_segs(actual: &[Segment], expected: &[Segment], label: &str) {
    assert_eq!(
        actual.len(),
        expected.len(),
        "{}: segment count ({})\n  actual:   {:?}\n  expected: {:?}",
        label,
        actual.len(),
        actual
            .iter()
            .map(|s| (decode(&s.text), s.field))
            .collect::<Vec<_>>(),
        expected
            .iter()
            .map(|s| (decode(&s.text), s.field))
            .collect::<Vec<_>>()
    );
    for (a, e) in actual.iter().zip(expected.iter()) {
        assert_eq!(
            a.field, e.field,
            "{}: field index\n  actual:   {:?}\n  expected: {:?}",
            label, a.field, e.field
        );
        assert_eq!(
            a.text,
            e.text,
            "{}: text\n  actual:   {}\n  expected: {}",
            label,
            decode(&a.text),
            decode(&e.text)
        );
    }
}

// ---------------------------------------------------------------------------
// mode 0: zero fields
// ---------------------------------------------------------------------------

#[test]
fn mode0_empty_base_zero_fields_returns_empty() {
    // (base or "").strip() == "" -> []
    let r = assemble(&[], &[], 0).expect("ok");
    assert!(
        r.is_empty(),
        "expected [], got {:?}",
        r.iter()
            .map(|s| (decode(&s.text), s.field))
            .collect::<Vec<_>>()
    );
}

#[test]
fn mode0_whitespace_only_base_zero_fields_returns_empty() {
    // "   " (spaces) .strip() == "" -> []
    let r = assemble(&[0x20, 0x20, 0x20], &[], 0).expect("ok");
    assert!(
        r.is_empty(),
        "expected [], got {:?}",
        r.iter()
            .map(|s| (decode(&s.text), s.field))
            .collect::<Vec<_>>()
    );
}

#[test]
fn mode0_unicode_whitespace_base_zero_fields_returns_empty() {
    // " \u{1C}\u{A0} " .strip() == "" -> []
    // U+001C and U+00A0 are both in the Python str.strip() set (verified).
    let r = assemble(&[0x20, 0x1C, 0xA0, 0x20], &[], 0).expect("ok");
    assert!(
        r.is_empty(),
        "expected [], got {:?}",
        r.iter()
            .map(|s| (decode(&s.text), s.field))
            .collect::<Vec<_>>()
    );
}

#[test]
fn mode0_base_no_colon_zero_fields_returns_base() {
    // "Point" .strip() == "Point" -> [("Point", None)]
    let r = assemble(&[0x50, 0x6F, 0x69, 0x6E, 0x74], &[], 0).expect("ok");
    assert_segs(
        &r,
        &[punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74])],
        "base no colon zero fields",
    );
}

#[test]
fn mode0_base_with_ws_zero_fields_strips() {
    // "  Point  " .strip() == "Point" -> [("Point", None)]
    let r = assemble(
        &[0x20, 0x20, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x20],
        &[],
        0,
    )
    .expect("ok");
    assert_segs(
        &r,
        &[punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74])],
        "base with ws zero fields",
    );
}

#[test]
fn mode0_base_colon_only_zero_fields() {
    // ":" .strip() == ":" -> [(": ", None)]? No:
    // base = [0x3A], fields empty -> b = [0x3A], b non-empty -> [(b, None)]
    // The base is returned as-is (it's the only segment, no brackets).
    let r = assemble(&[0x3A], &[], 0).expect("ok");
    assert_segs(&r, &[punct(&[0x3A])], "base colon only zero fields");
}

// ---------------------------------------------------------------------------
// mode 0: fields nonempty, base without trailing colon
// ---------------------------------------------------------------------------

#[test]
fn mode0_base_no_colon_one_field() {
    // base="Point", fields=["2Point"]
    // head = "Point ( ", tail = " )"
    // segments: [("Point ( ", None), ("2Point", Some(0)), (" )", None)]
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74]; // "Point"
    let field0 = [0x32, 0x50, 0x6F, 0x69, 0x6E, 0x74]; // "2Point"
    let r = assemble(&base, &[v(&field0)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&field0, 0),
        punct(&[0x20, 0x29]), // " )"
    ];
    assert_segs(&r, &expected, "base no colon one field");
}

#[test]
fn mode0_base_no_colon_two_fields() {
    // base="Point", fields=["2Point", "3Point"]
    // head="Point ( ", sep="  ", tail=" )"
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74];
    let f0 = [0x32, 0x50, 0x6F, 0x69, 0x6E, 0x74]; // "2Point"
    let f1 = [0x33, 0x50, 0x6F, 0x69, 0x6E, 0x74]; // "3Point"
    let r = assemble(&base, &[v(&f0), v(&f1)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&f0, 0),
        punct(&[0x20, 0x20]), // "  "
        seg(&f1, 1),
        punct(&[0x20, 0x29]), // " )"
    ];
    assert_segs(&r, &expected, "base no colon two fields");
}

#[test]
fn mode0_base_ws_stripped_before_brackets() {
    // base="  Corner 1  ", fields=["Diagonal"]
    // trimmed base = "Corner 1"
    // head = "Corner 1 ( ", tail = " )"
    let base = [
        0x20, 0x20, 0x43, 0x6F, 0x72, 0x6E, 0x65, 0x72, 0x20, 0x31, 0x20, 0x20,
    ];
    let f0 = [0x44, 0x69, 0x61, 0x67, 0x6F, 0x6E, 0x61, 0x6C]; // "Diagonal"
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[
            0x43, 0x6F, 0x72, 0x6E, 0x65, 0x72, 0x20, 0x31, 0x20, 0x28, 0x20,
        ]), // "Corner 1 ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29]), // " )"
    ];
    assert_segs(&r, &expected, "base ws stripped before brackets");
}

// ---------------------------------------------------------------------------
// mode 0: fields nonempty, base WITH trailing colon
// ---------------------------------------------------------------------------

#[test]
fn mode0_base_colon_one_field() {
    // base="Point:", fields=["2Point"]
    // base[:-1] = "Point", .rstrip() = "Point"
    // head = "Point ( ", tail = " ):"
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3A]; // "Point:"
    let f0 = [0x32, 0x50, 0x6F, 0x69, 0x6E, 0x74]; // "2Point"
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29, 0x3A]), // " ):"
    ];
    assert_segs(&r, &expected, "base colon one field");
}

#[test]
fn mode0_base_colon_ws_rstripped() {
    // base="Point :", fields=["2Point"]
    // base.strip() = "Point :"
    // base[:-1] = "Point ", .rstrip() = "Point"
    // head = "Point ( ", tail = " ):"
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x3A]; // "Point :"
    let f0 = [0x32, 0x50, 0x6F, 0x69, 0x6E, 0x74];
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29, 0x3A]), // " ):"
    ];
    assert_segs(&r, &expected, "base colon ws rstripped");
}

#[test]
fn mode0_base_internal_colon_no_trailing_colon() {
    // base="Point: 2" has an internal colon but NO trailing colon
    // (last codepoint is '2', 0x32), so the no-colon branch is taken.
    // head = "Point: 2 ( ", tail = " )"
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3A, 0x20, 0x32]; // "Point: 2"
    let f0 = [0x58]; // "X"
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[
            0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3A, 0x20, 0x32, 0x20, 0x28, 0x20,
        ]), // "Point: 2 ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29]), // " )"
    ];
    assert_segs(&r, &expected, "internal colon, no trailing colon");
}

#[test]
fn mode0_base_internal_and_trailing_colon() {
    // base="Point: 2:" has an internal colon AND a trailing colon.
    // base.strip() = "Point: 2:"; base[:-1] = "Point: 2"; .rstrip() = "Point: 2"
    // head = "Point: 2 ( ", tail = " ):"
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3A, 0x20, 0x32, 0x3A]; // "Point: 2:"
    let f0 = [0x58]; // "X"
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[
            0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3A, 0x20, 0x32, 0x20, 0x28, 0x20,
        ]), // "Point: 2 ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29, 0x3A]), // " ):"
    ];
    assert_segs(&r, &expected, "internal and trailing colon");
}

#[test]
fn mode0_docstring_example() {
    // The docstring example from options.py::format_prompt:
    // "Corner 1 ( Diagonal  Cube  Trim=Yes ):"
    // base="Corner 1:", fields=["Diagonal", "Cube", "Trim=Yes"]
    let base = [0x43, 0x6F, 0x72, 0x6E, 0x65, 0x72, 0x20, 0x31, 0x3A]; // "Corner 1:"
    let f0 = [0x44, 0x69, 0x61, 0x67, 0x6F, 0x6E, 0x61, 0x6C]; // "Diagonal"
    let f1 = [0x43, 0x75, 0x62, 0x65]; // "Cube"
    let f2 = [0x54, 0x72, 0x69, 0x6D, 0x3D, 0x59, 0x65, 0x73]; // "Trim=Yes"
    let r = assemble(&base, &[v(&f0), v(&f1), v(&f2)], 0).expect("ok");
    // Verify the joined string matches the docstring example.
    let joined: Vec<u32> = r.iter().flat_map(|s| s.text.iter().copied()).collect();
    let joined_str = decode(&joined);
    assert_eq!(joined_str, "Corner 1 ( Diagonal  Cube  Trim=Yes ):");
    // Verify field indices.
    assert_eq!(r[0].field, None);
    assert_eq!(r[1].field, Some(0));
    assert_eq!(r[2].field, None); // separator
    assert_eq!(r[3].field, Some(1));
    assert_eq!(r[4].field, None); // separator
    assert_eq!(r[5].field, Some(2));
    assert_eq!(r[6].field, None); // tail
}

// ---------------------------------------------------------------------------
// Unicode whitespace (U+001C, U+00A0, U+1680, U+3000)
// ---------------------------------------------------------------------------

#[test]
fn mode0_unicode_whitespace_base_with_fields() {
    // base="\u{1C}Point\u{A0}", fields=["X"]
    // .strip() = "Point" (U+001C and U+00A0 are both stripped)
    // head = "Point ( ", tail = " )"
    let base = [0x1C, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0xA0]; // "\u{1C}Point\u{A0}"
    let f0 = [0x58];
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29]), // " )"
    ];
    assert_segs(&r, &expected, "unicode whitespace base with fields");
}

#[test]
fn mode0_unicode_whitespace_base_colon() {
    // base="\u{1C}Point:\u{A0}", fields=["X"]
    // .strip() = "Point:" (U+001C and U+00A0 stripped)
    // base[:-1] = "Point", .rstrip() = "Point"
    // head = "Point ( ", tail = " ):"
    let base = [0x1C, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3A, 0xA0];
    let f0 = [0x58];
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29, 0x3A]), // " ):"
    ];
    assert_segs(&r, &expected, "unicode whitespace base colon");
}

#[test]
fn mode0_thai_whitespace_base() {
    // base="\u{1680}Point\u{1680}", fields=["X"]
    // U+1680 is in the Python str.strip() set.
    let base = [0x1680, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0x1680];
    let f0 = [0x58];
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29]), // " )"
    ];
    assert_segs(&r, &expected, "thai whitespace base");
}

#[test]
fn mode0_fullwidth_space_base() {
    // base="\u{3000}Point\u{3000}", fields=["X"]
    // U+3000 (CJK fullwidth space) is in the Python str.strip() set.
    let base = [0x3000, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3000];
    let f0 = [0x58];
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29]), // " )"
    ];
    assert_segs(&r, &expected, "fullwidth space base");
}

// ---------------------------------------------------------------------------
// surrogate codepoints preserved
// ---------------------------------------------------------------------------

#[test]
fn mode0_surrogate_in_base_preserved() {
    // base="\u{D800}Point", fields=["X"]
    // U+D800 is a lone surrogate (invalid as a char). is_cp_space returns false.
    // .strip() does not remove it (it's not whitespace).
    // head = "\u{D800}Point ( ", tail = " )"
    let base = [0xD800, 0x50, 0x6F, 0x69, 0x6E, 0x74]; // "\u{D800}Point"
    let f0 = [0x58];
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[0xD800, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "\u{D800}Point ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29]), // " )"
    ];
    assert_segs(&r, &expected, "surrogate in base preserved");
}

#[test]
fn mode0_surrogate_in_field_preserved() {
    // base="Point", fields=["\u{D800}X"]
    // The surrogate is in the field text, not the base.
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74];
    let f0 = [0xD800, 0x58]; // "\u{D800}X"
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&f0, 0),                                              // "\u{D800}X"
        punct(&[0x20, 0x29]),                                     // " )"
    ];
    assert_segs(&r, &expected, "surrogate in field preserved");
}

#[test]
fn mode0_surrogate_not_treated_as_whitespace() {
    // base="\u{D800}\u{D801}", fields=[]
    // Both are lone surrogates (invalid chars). is_cp_space returns false for both.
    // .strip() does not remove them.
    // Result: [("\u{D800}\u{D801}", None)]
    let base = [0xD800, 0xD801];
    let r = assemble(&base, &[], 0).expect("ok");
    assert_segs(&r, &[punct(&[0xD800, 0xD801])], "surrogate not whitespace");
}

// ---------------------------------------------------------------------------
// fullwidth colon (U+FF1A) is NOT the ASCII colon
// ---------------------------------------------------------------------------

#[test]
fn mode0_fullwidth_colon_not_treated_as_colon() {
    // base="Point\u{FF1A}", fields=["X"]
    // U+FF1A (fullwidth colon) is NOT the ASCII colon (U+003A).
    // base.last() != Some(0x3A), so the "no colon" branch is taken.
    // head = "Point\u{FF1A} ( ", tail = " )"
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74, 0xFF1A]; // "Point\uff1a"
    let f0 = [0x58];
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0xFF1A, 0x20, 0x28, 0x20]), // "Point\uff1a ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29]), // " )"
    ];
    assert_segs(&r, &expected, "fullwidth colon not treated as colon");
}

#[test]
fn mode0_fullwidth_colon_zero_fields() {
    // base="Point\u{FF1A}", fields=[]
    // .strip() = "Point\u{FF1A}" (U+FF1A is not whitespace)
    // Result: [("Point\u{FF1A}", None)]
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74, 0xFF1A];
    let r = assemble(&base, &[], 0).expect("ok");
    assert_segs(
        &r,
        &[punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0xFF1A])],
        "fullwidth colon zero fields",
    );
}

// ---------------------------------------------------------------------------
// field emptiness
// ---------------------------------------------------------------------------

#[test]
fn mode0_empty_field_preserved() {
    // base="Point", fields=[""]
    // The empty field is preserved as an empty segment.
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74];
    let f0: [u32; 0] = [];
    let r = assemble(&base, &[v(&f0)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&[], 0),                                              // "" (empty field)
        punct(&[0x20, 0x29]),                                     // " )"
    ];
    assert_segs(&r, &expected, "empty field preserved");
}

#[test]
fn mode0_two_empty_fields() {
    // base="Point", fields=["", ""]
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74];
    let f0: [u32; 0] = [];
    let f1: [u32; 0] = [];
    let r = assemble(&base, &[v(&f0), v(&f1)], 0).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&[], 0),
        punct(&[0x20, 0x20]), // "  "
        seg(&[], 1),
        punct(&[0x20, 0x29]), // " )"
    ];
    assert_segs(&r, &expected, "two empty fields");
}

#[test]
fn mode0_empty_field_between_nonempty() {
    // base="Point", fields=["A", "", "B"]
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74];
    let f0 = [0x41]; // "A"
    let f1: [u32; 0] = [];
    let f2 = [0x42]; // "B"
    let r = assemble(&base, &[v(&f0), v(&f1), v(&f2)], 0).expect("ok");
    assert_eq!(r.len(), 7, "expected 7 segments, got {}", r.len());
    assert_eq!(r[0].field, None); // head
    assert_eq!(r[1].field, Some(0)); // "A"
    assert_eq!(r[2].field, None); // sep
    assert_eq!(r[3].field, Some(1)); // "" (empty)
    assert_eq!(r[4].field, None); // sep
    assert_eq!(r[5].field, Some(2)); // "B"
    assert_eq!(r[6].field, None); // tail
}

// ---------------------------------------------------------------------------
// bounds
// ---------------------------------------------------------------------------

#[test]
fn bounds_base_too_long() {
    // base has 4097 codepoints (> MAX_PROMPT_TEXT_CODEPOINTS=4096)
    let base = vec![0x41; MAX_PROMPT_TEXT_CODEPOINTS + 1]; // 4097
    let r = assemble(&base, &[], 0);
    assert_eq!(
        r,
        Err(Error::TextTooLong),
        "base too long should be TextTooLong"
    );
}

#[test]
fn bounds_base_exactly_at_limit() {
    // base has exactly 4096 codepoints (== MAX_PROMPT_TEXT_CODEPOINTS)
    let base = vec![0x41; MAX_PROMPT_TEXT_CODEPOINTS]; // 4096
    let r = assemble(&base, &[], 0).expect("ok");
    assert_eq!(r.len(), 1);
    assert_eq!(r[0].text.len(), MAX_PROMPT_TEXT_CODEPOINTS);
    assert_eq!(r[0].field, None);
}

#[test]
fn bounds_field_too_long() {
    // field has 4097 codepoints (> MAX_PROMPT_TEXT_CODEPOINTS=4096)
    let base = [0x50]; // "P"
    let field = vec![0x41; MAX_PROMPT_TEXT_CODEPOINTS + 1]; // 4097
    let r = assemble(&base, &[field], 0);
    assert_eq!(
        r,
        Err(Error::TextTooLong),
        "field too long should be TextTooLong"
    );
}

#[test]
fn bounds_field_exactly_at_limit() {
    // field has exactly 4096 codepoints (== MAX_PROMPT_TEXT_CODEPOINTS)
    let base = [0x50]; // "P"
    let field = vec![0x41; MAX_PROMPT_TEXT_CODEPOINTS]; // 4096
    let r = assemble(&base, &[field], 0).expect("ok");
    assert_eq!(r[1].text.len(), MAX_PROMPT_TEXT_CODEPOINTS);
    assert_eq!(r[1].field, Some(0));
}

#[test]
fn bounds_too_many_fields() {
    // 65537 fields (> MAX_PROMPT_FIELDS=65536)
    let base = [0x50]; // "P"
    let fields: Vec<Vec<u32>> = vec![vec![0x41]; MAX_PROMPT_FIELDS + 1];
    let r = assemble(&base, &fields, 0);
    assert_eq!(
        r,
        Err(Error::TooManyFields),
        "too many fields should be TooManyFields"
    );
}

#[test]
fn bounds_fields_exactly_at_limit() {
    // 65536 fields (== MAX_PROMPT_FIELDS)
    let base = [0x50]; // "P"
    let fields: Vec<Vec<u32>> = vec![vec![0x41]; MAX_PROMPT_FIELDS];
    let r = assemble(&base, &fields, 0).expect("ok");
    // 1 head + 65536 fields + 65535 separators + 1 tail = 131074 segments
    assert_eq!(r.len(), 1 + MAX_PROMPT_FIELDS + (MAX_PROMPT_FIELDS - 1) + 1);
}

// The per-field bound (4096) is far smaller than the total bound (1048576), so
// the total bound is only reachable with many fields each at the per-field max:
// 256 fields x 4096 = exactly 1048576, 257 fields x 4096 = 1052672 (> limit).
const PER_FIELD: usize = MAX_PROMPT_TEXT_CODEPOINTS; // 4096
const FIELDS_AT_LIMIT: usize = MAX_PROMPT_TOTAL_CODEPOINTS / PER_FIELD; // 256

#[test]
fn bounds_total_exactly_at_limit() {
    // base=0, 256 fields x 4096 -> total=1048576 (== MAX_PROMPT_TOTAL_CODEPOINTS)
    let base: [u32; 0] = [];
    let fields: Vec<Vec<u32>> = vec![vec![0x41; PER_FIELD]; FIELDS_AT_LIMIT];
    let r = assemble(&base, &fields, 0).expect("ok");
    assert_eq!(r[1].text.len(), PER_FIELD);
    assert_eq!(r[1].field, Some(0));
    // 1 head + 256 fields + 255 separators + 1 tail
    assert_eq!(r.len(), 1 + FIELDS_AT_LIMIT + (FIELDS_AT_LIMIT - 1) + 1);
}

#[test]
fn bounds_total_too_long() {
    // base=0, 257 fields x 4096 -> total=1052672 (> limit)
    let base: [u32; 0] = [];
    let fields: Vec<Vec<u32>> = vec![vec![0x41; PER_FIELD]; FIELDS_AT_LIMIT + 1];
    let r = assemble(&base, &fields, 0);
    assert_eq!(
        r,
        Err(Error::TooManyTotal),
        "total too long should be TooManyTotal"
    );
}

#[test]
fn bounds_total_with_base() {
    // base=1, 256 fields x 4096 -> total=1048577 (> limit) even with base=1
    let base = [0x50]; // "P" (1 cp)
    let fields: Vec<Vec<u32>> = vec![vec![0x41; PER_FIELD]; FIELDS_AT_LIMIT];
    let r = assemble(&base, &fields, 0);
    assert_eq!(
        r,
        Err(Error::TooManyTotal),
        "total with base should be TooManyTotal"
    );
}

#[test]
fn bounds_total_one_field_plus_smaller() {
    // base=0, one field=4096, one field=4095 -> total=8191 (well under limit)
    let base: [u32; 0] = [];
    let f1 = vec![0x41; PER_FIELD];
    let f2 = vec![0x42; PER_FIELD - 1];
    let r = assemble(&base, &[f1, f2], 0).expect("ok");
    assert_eq!(r[1].text.len(), PER_FIELD);
    assert_eq!(r[3].text.len(), PER_FIELD - 1);
}

// ---------------------------------------------------------------------------
// invalid mode
// ---------------------------------------------------------------------------

#[test]
fn bad_mode_2() {
    let r = assemble(&[0x50], &[vec![0x41]], 2);
    assert_eq!(r, Err(Error::BadMode), "mode 2 should be BadMode");
}

#[test]
fn bad_mode_255() {
    let r = assemble(&[0x50], &[vec![0x41]], 255);
    assert_eq!(r, Err(Error::BadMode), "mode 255 should be BadMode");
}

#[test]
fn bad_mode_checked_before_bounds() {
    // Even with an oversized base, bad mode is detected first.
    let base = vec![0x41; MAX_PROMPT_TEXT_CODEPOINTS + 1]; // 4097
    let r = assemble(&base, &[], 2);
    assert_eq!(
        r,
        Err(Error::BadMode),
        "bad mode should be checked before bounds"
    );
}

// ---------------------------------------------------------------------------
// mode 1: enum value prompt (base NOT stripped)
// ---------------------------------------------------------------------------

#[test]
fn mode1_base_not_stripped() {
    // base="  Point  ", fields=["A"]
    // mode 1: base is NOT stripped.
    // head = "  Point  ( ", tail = " ):"
    let base = [0x20, 0x20, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x20]; // "  Point  "
    let f0 = [0x41]; // "A"
    let r = assemble(&base, &[v(&f0)], 1).expect("ok");
    let expected = vec![
        punct(&[
            0x20, 0x20, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x20, 0x20, 0x28, 0x20,
        ]), // "  Point  ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29, 0x3A]), // " ):"
    ];
    assert_segs(&r, &expected, "mode1 base not stripped");
}

#[test]
fn mode1_zero_fields() {
    // base="Point", fields=[]
    // mode 1: head = "Point ( ", tail = " ):"
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74]; // "Point"
    let r = assemble(&base, &[], 1).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        punct(&[0x20, 0x29, 0x3A]),                               // " ):"
    ];
    assert_segs(&r, &expected, "mode1 zero fields");
}

#[test]
fn mode1_two_fields() {
    // base="Point", fields=["A", "B"]
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74];
    let f0 = [0x41]; // "A"
    let f1 = [0x42]; // "B"
    let r = assemble(&base, &[v(&f0), v(&f1)], 1).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x28, 0x20]), // "Point ( "
        seg(&f0, 0),
        punct(&[0x20, 0x20]), // "  "
        seg(&f1, 1),
        punct(&[0x20, 0x29, 0x3A]), // " ):"
    ];
    assert_segs(&r, &expected, "mode1 two fields");
}

#[test]
fn mode1_base_colon_not_special() {
    // base="Point:", fields=["A"]
    // mode 1: base is NOT stripped, and the trailing colon is NOT special
    // (no rstrip of base[:-1]). The colon is just part of the base.
    // head = "Point: ( ", tail = " ):"
    let base = [0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3A]; // "Point:"
    let f0 = [0x41];
    let r = assemble(&base, &[v(&f0)], 1).expect("ok");
    let expected = vec![
        punct(&[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3A, 0x20, 0x28, 0x20]), // "Point: ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29, 0x3A]), // " ):"
    ];
    assert_segs(&r, &expected, "mode1 base colon not special");
}

#[test]
fn mode1_unicode_whitespace_base() {
    // base="\u{1C}Point\u{A0}", fields=["A"]
    // mode 1: base is NOT stripped. U+001C and U+00A0 are preserved.
    // head = "\u{1C}Point\u{A0} ( ", tail = " ):"
    let base = [0x1C, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0xA0];
    let f0 = [0x41];
    let r = assemble(&base, &[v(&f0)], 1).expect("ok");
    let expected = vec![
        punct(&[0x1C, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0xA0, 0x20, 0x28, 0x20]), // "\u{1C}Point\u{A0} ( "
        seg(&f0, 0),
        punct(&[0x20, 0x29, 0x3A]), // " ):"
    ];
    assert_segs(&r, &expected, "mode1 unicode whitespace base");
}

#[test]
fn mode1_empty_base_zero_fields() {
    // base="", fields=[]
    // mode 1: head = " ( ", tail = " ):"
    let base: [u32; 0] = [];
    let r = assemble(&base, &[], 1).expect("ok");
    let expected = vec![
        punct(&[0x20, 0x28, 0x20]), // " ( "
        punct(&[0x20, 0x29, 0x3A]), // " ):"
    ];
    assert_segs(&r, &expected, "mode1 empty base zero fields");
}

// ---------------------------------------------------------------------------
// invariant: join(text) == format_prompt
// ---------------------------------------------------------------------------

#[test]
fn invariant_join_equals_format_prompt() {
    // For a variety of inputs, the joined segments must equal what
    // options.py::format_prompt would produce.
    type Case<'a> = (&'a [u32], &'a [Vec<u32>], u32, &'a str);
    let cases: &[Case] = &[
        // (base, fields, mode, expected_joined)
        (&[], &[], 0, ""),
        (&[0x50, 0x6F, 0x69, 0x6E, 0x74], &[], 0, "Point"),
        (
            &[0x50, 0x6F, 0x69, 0x6E, 0x74],
            &[vec![0x41]],
            0,
            "Point ( A )",
        ),
        (
            &[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3A],
            &[vec![0x41]],
            0,
            "Point ( A ):",
        ),
        (
            &[0x50, 0x6F, 0x69, 0x6E, 0x74],
            &[vec![0x41], vec![0x42]],
            0,
            "Point ( A  B )",
        ),
        (
            &[0x50, 0x6F, 0x69, 0x6E, 0x74, 0x3A],
            &[vec![0x41], vec![0x42]],
            0,
            "Point ( A  B ):",
        ),
        (
            &[0x20, 0x20, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x20],
            &[vec![0x41]],
            0,
            "Point ( A )",
        ),
        (
            &[0x1C, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0xA0],
            &[vec![0x41]],
            0,
            "Point ( A )",
        ),
        (
            &[0x20, 0x20, 0x50, 0x6F, 0x69, 0x6E, 0x74, 0x20, 0x20],
            &[vec![0x41]],
            1,
            "  Point   ( A ):",
        ),
        (&[0x50, 0x6F, 0x69, 0x6E, 0x74], &[], 1, "Point (  ):"),
    ];
    for &(base, fields, mode, expected) in cases {
        let r = assemble(base, fields, mode).expect("ok");
        let joined: Vec<u32> = r.iter().flat_map(|s| s.text.iter().copied()).collect();
        let joined_str = decode(&joined);
        assert_eq!(
            joined_str,
            expected,
            "join mismatch for base={:?} fields={:?} mode={}",
            decode(base),
            fields.iter().map(|f| decode(f)).collect::<Vec<_>>(),
            mode
        );
    }
}
