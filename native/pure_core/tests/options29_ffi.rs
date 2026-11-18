//! OPTIONS-ENGINE-29 (FFI packet) — standalone tests for `src/options_ffi.rs`.
//!
//! References the engine and the FFI directly with `#[path]` so **no `lib.rs`
//! edit is required**. Independent: no external crates, no host (Python).
//!
//! The positive cases call the **actual exported**
//! `narwhal_pure_core_options_resolve` (and `narwhal_pure_core_options_abi`)
//! and assert the decoded output words. The expected outcomes mirror the
//! already-passing engine test (`options29_engine.rs`, 43/43 green) — the same
//! inputs, the same results — so the FFI layer is validated against the
//! engine's frozen behaviour, including:
//!   * Unicode expansion / case / duplicate-name semantics (A/a, A/a/A,
//!     Straße/straße, Sharp/SHARP, empty exact choice);
//!   * assign position vs. u32::MAX "no letter";
//!   * exact > single-codepoint activation > non-digit prefix;
//!   * enum first-exact-wins and empty-prefix unique/ambiguous.
//!
//! The negative cases validate the ABI contract: bad op, truncation,
//! not-exactly-consumed, bounds (word_count / name_count / name length / query
//! length) all checked **before** any pointer read or allocation, null and
//! misaligned pointers, invalid codepoint/flag, short capacity, null out for a
//! non-empty result, and **no writes to `out`/`out_count` on any error**.
//!
//! Run standalone (from the crate directory):
//!   rustc --edition=2021 --test tests/options29_ffi.rs -o /tmp/opts29ffi
//!   /tmp/opts29ffi

use narwhal_pure_core::options_ffi::{
    narwhal_pure_core_options_abi, narwhal_pure_core_options_resolve, OPTIONS_BOUND,
    OPTIONS_MALFORMED, OPTIONS_OK, OPTIONS_SHORT_OUTPUT,
};
use narwhal_pure_core::options_owned::{Error, Name, Query};

/// Sentinel for "the buffer was never written" (checked on every error path).
const SENT: u32 = 0xDEAD_BEEF;
/// Sentinel for "out_count was never written".
const COUNTS: usize = 0xC0FFEE;

// ---------------------------------------------------------------------------
// private host emulation — mirrors Python str.lower()/str.isalpha() for the
// test inputs (a codepoint not in the table folds to itself). Identical to the
// helper in `options29_engine.rs`.
// ---------------------------------------------------------------------------

mod host {
    const FOLDS: &[(u32, &[u32])] = &[
        (0x0041, &[0x0061]),
        (0x0042, &[0x0062]),
        (0x0043, &[0x0063]),
        (0x0044, &[0x0064]),
        (0x0045, &[0x0065]),
        (0x0046, &[0x0066]),
        (0x0047, &[0x0067]),
        (0x0048, &[0x0068]),
        (0x0049, &[0x0069]),
        (0x004A, &[0x006A]),
        (0x004B, &[0x006B]),
        (0x004C, &[0x006C]),
        (0x004D, &[0x006D]),
        (0x004E, &[0x006E]),
        (0x004F, &[0x006F]),
        (0x0050, &[0x0070]),
        (0x0051, &[0x0071]),
        (0x0052, &[0x0072]),
        (0x0053, &[0x0073]),
        (0x0054, &[0x0074]),
        (0x0055, &[0x0075]),
        (0x0056, &[0x0076]),
        (0x0057, &[0x0077]),
        (0x0058, &[0x0078]),
        (0x0059, &[0x0079]),
        (0x005A, &[0x007A]),
        (0x00C7, &[0x00E7]),
        (0x0130, &[0x0069, 0x0307]),
    ];
    const ALPHA: &[u32] = &[
        0x0041, 0x0042, 0x0043, 0x0044, 0x0045, 0x0046, 0x0047, 0x0048, 0x0049, 0x004A, 0x004B,
        0x004C, 0x004D, 0x004E, 0x004F, 0x0050, 0x0051, 0x0052, 0x0053, 0x0054, 0x0055, 0x0056,
        0x0057, 0x0058, 0x0059, 0x005A, 0x0061, 0x0062, 0x0063, 0x0064, 0x0065, 0x0066, 0x0067,
        0x0068, 0x0069, 0x006A, 0x006B, 0x006C, 0x006D, 0x006E, 0x006F, 0x0070, 0x0071, 0x0072,
        0x0073, 0x0074, 0x0075, 0x0076, 0x0077, 0x0078, 0x0079, 0x007A, 0x00C7, 0x0130, 0x00DF,
        0x00E7, 0x00FF,
    ];

    fn fold(cp: u32) -> Vec<u32> {
        FOLDS
            .iter()
            .find(|(c, _)| *c == cp)
            .map(|(_, v)| v.to_vec())
            .unwrap_or_else(|| vec![cp])
    }
    fn is_alpha(cp: u32) -> bool {
        ALPHA.contains(&cp)
    }

    pub fn name(s: &str) -> super::Name {
        let original: Vec<u32> = s.chars().map(|c| c as u32).collect();
        let mut folded = Vec::new();
        let mut per_char = Vec::new();
        let mut alpha = Vec::new();
        for c in original.iter() {
            let f = fold(*c);
            folded.extend_from_slice(&f);
            per_char.push(f);
            alpha.push(is_alpha(*c));
        }
        super::Name {
            original,
            folded,
            per_char_folded: per_char,
            alphabetic: alpha,
        }
    }

    pub fn query(q: &str) -> super::Query {
        let original_len = q.chars().count();
        let mut folded = Vec::new();
        for c in q.chars() {
            folded.extend_from_slice(&fold(c as u32));
        }
        let is_digit = original_len > 0 && q.chars().all(|c| c.is_ascii_digit());
        super::Query {
            folded,
            original_len,
            is_digit,
        }
    }
}

// ---------------------------------------------------------------------------
// word-protocol encoding (mirrors `options_ffi::decode`) and call helpers
// ---------------------------------------------------------------------------

fn encode_name(n: &Name) -> Vec<u32> {
    let mut w = Vec::new();
    w.push(n.original.len() as u32);
    w.extend_from_slice(&n.original);
    w.push(n.folded.len() as u32);
    w.extend_from_slice(&n.folded);
    for i in 0..n.alphabetic.len() {
        w.push(n.alphabetic[i] as u32);
        w.push(n.per_char_folded[i].len() as u32);
        w.extend_from_slice(&n.per_char_folded[i]);
    }
    w
}

fn encode_query(q: &Query) -> Vec<u32> {
    let mut w = Vec::new();
    w.push(q.original_len as u32);
    w.push(q.is_digit as u32);
    w.push(q.folded.len() as u32);
    w.extend_from_slice(&q.folded);
    w
}

fn encode(names: &[Name], query: &Query) -> Vec<u32> {
    let mut w = Vec::new();
    w.push(names.len() as u32);
    for n in names {
        w.extend(encode_name(n));
    }
    w.extend(encode_query(query));
    w
}

/// Raw exported call (raw pointers, for the null/misaligned/alias cases).
fn raw_call(
    words: *const u32,
    word_count: usize,
    op: u32,
    out: *mut u32,
    capacity: usize,
    out_count: *mut usize,
) -> i32 {
    unsafe { narwhal_pure_core_options_resolve(words, word_count, op, out, capacity, out_count) }
}

/// Slice-based call returning (status, out buffer, out_count). The out buffer
/// is pre-filled with `SENT` so any "no write" can be detected.
fn call(words: &[u32], op: u32, capacity: usize) -> (i32, Vec<u32>, usize) {
    let mut out = vec![SENT; capacity];
    let mut cnt = COUNTS;
    let rc = raw_call(
        words.as_ptr(),
        words.len(),
        op,
        out.as_mut_ptr(),
        capacity,
        &mut cnt,
    );
    (rc, out, cnt)
}

/// Assert a successful call whose output words are exactly `want`.
fn assert_ok(words: &[u32], op: u32, want: &[u32]) {
    let (rc, out, cnt) = call(words, op, want.len().max(1));
    assert_eq!(rc, OPTIONS_OK, "expected success; want={want:?}");
    assert_eq!(cnt, want.len(), "out_count must equal the result length");
    let got: Vec<u32> = out.iter().take(cnt).copied().collect();
    assert_eq!(got, want.to_vec(), "result words; want={want:?}");
}

// ===========================================================================
// ABI version
// ===========================================================================

#[test]
fn abi_version_is_one() {
    assert_eq!(narwhal_pure_core_options_abi(), 1);
}

// ===========================================================================
// POSITIVES — op 0: assign (position or u32::MAX "no letter")
// ===========================================================================

#[test]
fn assign_circle_frozen() {
    let words = encode(
        &[
            host::name("Deformable"),
            host::name("2Point"),
            host::name("3Point"),
            host::name("Vertical"),
        ],
        &host::query("d"),
    );
    assert_ok(&words, 0, &[0, 1, 2, 0]);
}

#[test]
fn assign_case_variants_distinct_frozen() {
    // "A" claims `a` (pos 0); "a" is a distinct original name but finds `a`
    // already claimed -> no letter (u32::MAX).
    let words = encode(&[host::name("A"), host::name("a")], &host::query("a"));
    assert_ok(&words, 0, &[0, u32::MAX]);
}

#[test]
fn assign_last_duplicate_wins_all_none_frozen() {
    // "A"(0) claims `a`; "a"(1) none; "A"(2) overwrites "A"'s entry with none.
    // Every occurrence resolves to the final key for its original name -> none.
    let words = encode(
        &[host::name("A"), host::name("a"), host::name("A")],
        &host::query("a"),
    );
    assert_ok(&words, 0, &[u32::MAX, u32::MAX, u32::MAX]);
}

#[test]
fn assign_strasse_variants_distinct_frozen() {
    let words = encode(
        &[host::name("Straße"), host::name("straße")],
        &host::query("s"),
    );
    assert_ok(&words, 0, &[0, 1]);
}

#[test]
fn assign_duplicate_names_share_final_key_frozen() {
    let words = encode(
        &[host::name("Point"), host::name("Point"), host::name("X")],
        &host::query("p"),
    );
    assert_ok(&words, 0, &[1, 1, 0]);
}

#[test]
fn assign_letter_claim_partial_frozen() {
    let words = encode(&[host::name("A"), host::name("AB")], &host::query("a"));
    assert_ok(&words, 0, &[0, 1]);
}

#[test]
fn assign_digit_names_skip_digits_frozen() {
    let words = encode(
        &[host::name("1Point"), host::name("2Point")],
        &host::query("1"),
    );
    assert_ok(&words, 0, &[1, 2]);
}

// ===========================================================================
// POSITIVES — op 1: resolve (selected option indices)
// ===========================================================================

#[test]
fn resolve_case_variants_exact_and_key_frozen() {
    let names = [host::name("Point"), host::name("point"), host::name("X")];
    // "point": exact (case-insensitive) hits both -> ambiguous.
    assert_ok(&encode(&names, &host::query("point")), 1, &[0, 1]);
    // "p": key path -> "Point" (index 0) only.
    assert_ok(&encode(&names, &host::query("p")), 1, &[0]);
    // "x": exact for "X".
    assert_ok(&encode(&names, &host::query("x")), 1, &[2]);
}

#[test]
fn resolve_a_a_exact_ambiguous_frozen() {
    let names = [host::name("A"), host::name("a")];
    assert_ok(&encode(&names, &host::query("a")), 1, &[0, 1]);
    assert_ok(&encode(&names, &host::query("A")), 1, &[0, 1]);
    // "b": no exact, no key, no prefix -> none (empty).
    assert_ok(&encode(&names, &host::query("b")), 1, &[]);
}

#[test]
fn resolve_ab_exact_key_none_frozen() {
    let names = [host::name("A"), host::name("B"), host::name("ab")];
    assert_ok(&encode(&names, &host::query("a")), 1, &[0]);
    assert_ok(&encode(&names, &host::query("b")), 1, &[1]);
    // "ab" is an exact name -> wins over the (absent) key path.
    assert_ok(&encode(&names, &host::query("ab")), 1, &[2]);
    assert_ok(&encode(&names, &host::query("c")), 1, &[]);
}

#[test]
fn resolve_digit_names_frozen() {
    let names = [host::name("1Point"), host::name("2Point")];
    assert_ok(&encode(&names, &host::query("1")), 1, &[]);
    assert_ok(&encode(&names, &host::query("2")), 1, &[]);
    assert_ok(&encode(&names, &host::query("1point")), 1, &[0]);
    assert_ok(&encode(&names, &host::query("2p")), 1, &[1]);
}

#[test]
fn resolve_single_codepoint_key_beats_prefix() {
    let names = [host::name("A"), host::name("Apple")];
    // "a" is "A"'s key -> Set for "A" (index 0), not the prefix match "Apple".
    assert_ok(&encode(&names, &host::query("a")), 1, &[0]);
}

#[test]
fn resolve_two_codepoint_prefix() {
    let names = [host::name("C"), host::name("Cab")];
    assert_ok(&encode(&names, &host::query("c")), 1, &[0]);
    assert_ok(&encode(&names, &host::query("ca")), 1, &[1]);
}

// ===========================================================================
// POSITIVES — op 2: enum (first exact wins; else unique/ambiguous prefix)
// ===========================================================================

#[test]
fn enum_first_exact_wins_over_duplicates_frozen() {
    // (Sharp, SHARP) + "sharp" -> first exact (index 0), not ambiguous.
    let c = [host::name("Sharp"), host::name("SHARP")];
    assert_ok(&encode(&c, &host::query("sharp")), 2, &[0]);
    // (SHARP, Sharp) + "sharp" -> first exact (index 0).
    let c = [host::name("SHARP"), host::name("Sharp")];
    assert_ok(&encode(&c, &host::query("sharp")), 2, &[0]);
    // (Sharp, Sharp) + "sharp" -> first exact (index 0).
    let c = [host::name("Sharp"), host::name("Sharp")];
    assert_ok(&encode(&c, &host::query("sharp")), 2, &[0]);
}

#[test]
fn enum_empty_exact_choice_beats_prefix_frozen() {
    // choices ['', a] + '' -> the empty choice is an exact match (index 0).
    let c = [host::name(""), host::name("a")];
    let empty = Query {
        folded: Vec::new(),
        original_len: 0,
        is_digit: false,
    };
    assert_ok(&encode(&c, &empty), 2, &[0]);
}

#[test]
fn enum_empty_prefix_unique_and_ambiguous_frozen() {
    // (a, b) + '' -> no exact; empty prefix matches all -> ambiguous.
    let c = [host::name("a"), host::name("b")];
    let empty = Query {
        folded: Vec::new(),
        original_len: 0,
        is_digit: false,
    };
    assert_ok(&encode(&c, &empty), 2, &[0, 1]);
    // (Only) + '' -> empty prefix matches the single choice -> set.
    let c = [host::name("Only")];
    assert_ok(&encode(&c, &empty), 2, &[0]);
}

#[test]
fn enum_scale_exact_and_prefix_frozen() {
    let c = [host::name("No"), host::name("1D"), host::name("3D")];
    assert_ok(&encode(&c, &host::query("No")), 2, &[0]);
    assert_ok(&encode(&c, &host::query("1d")), 2, &[1]);
    assert_ok(&encode(&c, &host::query("3")), 2, &[2]);
    assert_ok(&encode(&c, &host::query("n")), 2, &[0]);
    assert_ok(&encode(&c, &host::query("1")), 2, &[1]);
    assert_ok(&encode(&c, &host::query("3d")), 2, &[2]);
    assert_ok(&encode(&c, &host::query("x")), 2, &[]);
}

#[test]
fn enum_prefix_ambiguous_without_exact_frozen() {
    // "sh" is not exact for Sharp/SHARP -> both are prefix hits -> ambiguous.
    let c = [host::name("Sharp"), host::name("SHARP")];
    assert_ok(&encode(&c, &host::query("sh")), 2, &[0, 1]);
}

#[test]
fn enum_ambiguous_prefix_reports_all_frozen() {
    let c = [host::name("No"), host::name("Near"), host::name("Yes")];
    assert_ok(&encode(&c, &host::query("n")), 2, &[0, 1]);
}

// ===========================================================================
// POSITIVES — Unicode (host-supplied fold; engine never classifies)
// ===========================================================================

#[test]
fn unicode_dotted_i_expanded_fold_claim() {
    // 'İ' per-char fold is [i, combining-dot] (two codepoints), a distinct
    // claim from a plain 'i'.
    let n = host::name("\u{130}");
    assert_eq!(n.per_char_folded, vec![vec![0x0069, 0x0307]]);
    assert_ok(&encode(&[n], &host::query("i")), 0, &[0]);
    // 'i' and 'İ' both claim position 0 (distinct fold sequences).
    let names = [host::name("i"), host::name("\u{130}")];
    assert_ok(&encode(&names, &host::query("i")), 0, &[0, 0]);
}

#[test]
fn unicode_eszett_identity_fold() {
    let n = host::name("\u{df}");
    assert_eq!(n.per_char_folded, vec![vec![0x00df]]);
    assert_ok(&encode(&[n], &host::query("ß")), 0, &[0]);
}

#[test]
fn unicode_non_ascii_alpha_letter() {
    let n = host::name("\u{c7}"); // Ç -> ç
    assert_eq!(n.per_char_folded, vec![vec![0x00e7]]);
    assert_ok(&encode(&[n], &host::query("Ç")), 0, &[0]);
}

#[test]
fn unicode_lone_surrogate_preserved() {
    // A lone surrogate is preserved as-is; the host marks it non-alphabetic,
    // so the following 'a' (position 1) is the activation letter.
    let n = Name {
        original: vec![0xD800u32, 0x0061u32],
        folded: vec![0xD800u32, 0x0061u32],
        per_char_folded: vec![vec![0xD800u32], vec![0x0061u32]],
        alphabetic: vec![false, true],
    };
    assert_ok(&encode(&[n], &host::query("a")), 0, &[1]);
}

#[test]
fn unicode_pure_digit_query_is_none() {
    // "１2" is a digit to the host -> never an option via the prefix step.
    let names = [host::name("\u{ff11}Point"), host::name("2Point")];
    let q = Query {
        folded: vec![0xFF11u32, 0x0032u32],
        original_len: 2,
        is_digit: true,
    };
    assert_ok(&encode(&names, &q), 1, &[]);
}

// ===========================================================================
// NEGATIVES — malformed / bound / pointer / short-output / no-write
// ===========================================================================

#[test]
fn bad_op_is_malformed_and_no_write() {
    let words = encode(&[host::name("A")], &host::query("a"));
    for op in [3u32, 4, 100, u32::MAX] {
        let (rc, out, cnt) = call(&words, op, 8);
        assert_eq!(rc, OPTIONS_MALFORMED, "op={op}");
        assert_eq!(cnt, COUNTS, "no out_count write");
        assert!(out.iter().all(|&w| w == SENT), "no out write");
    }
}

#[test]
fn truncated_words_is_malformed_and_no_write() {
    let words = encode(&[host::name("Deformable")], &host::query("d"));
    let trunc = &words[..words.len() - 1];
    let (rc, out, cnt) = call(trunc, 0, 8);
    assert_eq!(rc, OPTIONS_MALFORMED);
    assert_eq!(cnt, COUNTS);
    assert!(out.iter().all(|&w| w == SENT));
}

#[test]
fn extra_trailing_word_is_malformed_and_no_write() {
    let mut words = encode(&[host::name("A")], &host::query("a"));
    words.push(0x1234);
    let (rc, out, cnt) = call(&words, 1, 8);
    assert_eq!(rc, OPTIONS_MALFORMED);
    assert_eq!(cnt, COUNTS);
    assert!(out.iter().all(|&w| w == SENT));
}

#[test]
fn word_count_over_bound_is_bound_before_any_read() {
    // A word_count just over MAX_WORDS (1_048_576) is a bound error checked
    // BEFORE the pointer is validated or read — a null pointer proves no read.
    let rc = raw_call(
        std::ptr::null::<u32>(),
        1_048_577,
        0,
        std::ptr::null_mut(),
        0,
        std::ptr::null_mut(),
    );
    assert_eq!(rc, OPTIONS_BOUND);
}

#[test]
fn name_count_over_bound_is_bound_not_allocated() {
    // name_count = u32::MAX with a 1-word buffer: a bound error, and it must
    // NOT attempt to allocate ~4 billion names.
    let words: Vec<u32> = vec![0xFFFF_FFFF];
    let (rc, out, cnt) = call(&words, 0, 8);
    assert_eq!(rc, OPTIONS_BOUND);
    assert_eq!(cnt, COUNTS);
    assert!(out.iter().all(|&w| w == SENT));
}

#[test]
fn name_too_long_is_bound() {
    // original_len 4097 > MAX_NAME (4096) -> a decode bound, checked before the
    // codepoints are read.
    let words: Vec<u32> = vec![1, 4097, 1, 0, 1, 0x0061];
    let (rc, _out, cnt) = call(&words, 0, 8);
    assert_eq!(rc, OPTIONS_BOUND);
    assert_eq!(cnt, COUNTS);
}

#[test]
fn query_too_long_is_bound() {
    let words: Vec<u32> = vec![0, 4097, 0, 4097];
    let (rc, _out, cnt) = call(&words, 1, 8);
    assert_eq!(rc, OPTIONS_BOUND);
    assert_eq!(cnt, COUNTS);
}

#[test]
fn invalid_codepoint_is_malformed() {
    let n = Name {
        original: vec![0x11_0000], // > 0x10_FFFF
        folded: vec![0x11_0000],
        per_char_folded: vec![vec![0x11_0000]],
        alphabetic: vec![true],
    };
    let words = encode(&[n], &host::query("a"));
    let (rc, out, cnt) = call(&words, 0, 8);
    assert_eq!(rc, OPTIONS_MALFORMED);
    assert_eq!(cnt, COUNTS);
    assert!(out.iter().all(|&w| w == SENT));
}

#[test]
fn invalid_alpha_flag_is_malformed() {
    let words: Vec<u32> = vec![
        1, // name_count
        1, 0x0061, // original_len, original[0]
        1, 0x0061, // folded_len, folded[0]
        2,      // alpha = 2 (invalid, > 1)
        1, 0x0061, // fold_len, fold[0]
        1, 0, // query: original_len, is_digit
        1, 0x0061, // query: folded_len, folded[0]
    ];
    let (rc, _out, cnt) = call(&words, 0, 8);
    assert_eq!(rc, OPTIONS_MALFORMED);
    assert_eq!(cnt, COUNTS);
}

#[test]
fn invalid_is_digit_flag_is_malformed() {
    let words: Vec<u32> = vec![
        1, // name_count
        1, 0x0061, // original
        1, 0x0061, // folded
        0,      // alpha (non-alpha)
        1, 0x0061, // fold
        1,      // query original_len
        2,      // query is_digit = 2 (invalid, > 1)
        1, 0x0061, // query folded
    ];
    let (rc, _out, cnt) = call(&words, 1, 8);
    assert_eq!(rc, OPTIONS_MALFORMED);
    assert_eq!(cnt, COUNTS);
}

#[test]
fn null_words_nonzero_count_is_malformed() {
    let mut cnt = COUNTS;
    let rc = raw_call(
        std::ptr::null::<u32>(),
        1,
        0,
        std::ptr::null_mut(),
        0,
        &mut cnt,
    );
    assert_eq!(rc, OPTIONS_MALFORMED);
    assert_eq!(cnt, COUNTS);
}

#[test]
fn null_words_zero_count_is_malformed() {
    // 0 words: cannot even read name_count -> malformed (the protocol requires it).
    let mut cnt = COUNTS;
    let rc = raw_call(
        std::ptr::null::<u32>(),
        0,
        0,
        std::ptr::null_mut(),
        0,
        &mut cnt,
    );
    assert_eq!(rc, OPTIONS_MALFORMED);
    assert_eq!(cnt, COUNTS);
}

#[test]
fn misaligned_words_is_malformed() {
    let buf: Vec<u32> = vec![0u32; 4];
    let base = buf.as_ptr() as usize;
    assert!(base.is_multiple_of(4), "u32 buffer must be 4-aligned");
    let mis = (base + 1) as *const u32; // 4-aligned + 1 -> misaligned for u32
    let mut cnt = COUNTS;
    let rc = raw_call(mis, 1, 0, std::ptr::null_mut(), 0, &mut cnt);
    assert_eq!(rc, OPTIONS_MALFORMED);
    assert_eq!(cnt, COUNTS);
}

#[test]
fn short_capacity_is_short_output_and_no_write() {
    // assign over 2 names -> result length 2; capacity 1 -> short output.
    let words = encode(&[host::name("A"), host::name("B")], &host::query("a"));
    let (rc, out, cnt) = call(&words, 0, 1);
    assert_eq!(rc, OPTIONS_SHORT_OUTPUT);
    assert_eq!(cnt, COUNTS, "no out_count write on short output");
    assert!(
        out.iter().all(|&w| w == SENT),
        "no out write on short output"
    );
}

#[test]
fn nonempty_result_empty_capacity_is_short() {
    // resolve "a" over [A] -> Set [0] (non-empty). `out` is null and
    // `capacity == 0` (an empty buffer, which is legal): the non-empty result
    // does not fit -> short output, and nothing is written.
    let words = encode(&[host::name("A")], &host::query("a"));
    let mut cnt = COUNTS;
    let rc = raw_call(
        words.as_ptr(),
        words.len(),
        1,
        std::ptr::null_mut(),
        0,
        &mut cnt,
    );
    assert_eq!(rc, OPTIONS_SHORT_OUTPUT);
    assert_eq!(cnt, COUNTS);
}

#[test]
fn empty_result_null_out_is_ok() {
    // resolve "b" over [A] -> None (empty); `out` may be null, count is 0.
    let words = encode(&[host::name("A")], &host::query("b"));
    let mut cnt = COUNTS;
    let rc = raw_call(
        words.as_ptr(),
        words.len(),
        1,
        std::ptr::null_mut(),
        0,
        &mut cnt,
    );
    assert_eq!(rc, OPTIONS_OK);
    assert_eq!(cnt, 0, "empty result records count 0");
}

#[test]
fn empty_result_null_out_count_is_malformed() {
    // Even an empty result must be able to write out_count; null is malformed.
    let words = encode(&[host::name("A")], &host::query("b"));
    let rc = raw_call(
        words.as_ptr(),
        words.len(),
        1,
        std::ptr::null_mut(),
        0,
        std::ptr::null_mut(),
    );
    assert_eq!(rc, OPTIONS_MALFORMED);
}

#[test]
fn empty_result_valid_out_records_zero_and_no_write() {
    let words = encode(&[host::name("A")], &host::query("b"));
    let (rc, out, cnt) = call(&words, 1, 4);
    assert_eq!(rc, OPTIONS_OK);
    assert_eq!(cnt, 0);
    assert!(
        out.iter().all(|&w| w == SENT),
        "out untouched on empty result"
    );
}

#[test]
fn aliasing_input_output_is_malformed() {
    // Put the (would-be) output inside the input span -> alias -> malformed.
    let words = encode(&[host::name("A"), host::name("B")], &host::query("a"));
    let mut buf = words.clone();
    let _ = &mut buf; // keep buf alive across the call
    let base = buf.as_mut_ptr();
    let mut cnt = COUNTS;
    let rc = unsafe { narwhal_pure_core_options_resolve(base, words.len(), 0, base, 2, &mut cnt) };
    assert_eq!(rc, OPTIONS_MALFORMED);
    assert_eq!(cnt, COUNTS);
}

#[test]
fn out_count_aliasing_words_is_malformed() {
    // `out_count` (a `usize`) placed strictly inside the words byte span, 8-aligned.
    // `out` is a separate valid buffer. The only violation is the overlap.
    let words = encode(&[host::name("A"), host::name("B")], &host::query("a"));
    let mut buf: Vec<u32> = words.clone();
    let _ = &mut buf; // keep the allocation alive across the call
    let base = buf.as_ptr() as usize;
    assert!(base.is_multiple_of(8), "Vec<u32> base must be 8-aligned");
    assert!(4 * words.len() > 8, "words span must exceed 8 bytes");
    let cnt_ptr = (base + 8) as *mut usize; // 8-aligned, inside words span
    let mut out: Vec<u32> = vec![SENT; 8];
    let _ = &mut out;
    let rc = unsafe {
        narwhal_pure_core_options_resolve(
            buf.as_ptr(),
            words.len(),
            0,
            out.as_mut_ptr(),
            2,
            cnt_ptr,
        )
    };
    assert_eq!(rc, OPTIONS_MALFORMED);
}

#[test]
fn out_count_aliasing_out_is_malformed() {
    // `out_count` placed strictly inside the `out` span (both 8-aligned) ->
    // a genuine byte overlap, not merely adjacency.
    let words = encode(&[host::name("A"), host::name("B")], &host::query("a"));
    let mut buf: Vec<u32> = vec![SENT; 16];
    let _ = &mut buf;
    let base = buf.as_mut_ptr() as usize;
    assert!(base.is_multiple_of(8), "buffer must be 8-aligned for usize");
    // out span = [base, base + 4*4) = [base, base+16);
    // out_count = [base+8, base+16) -> overlaps out.
    let cnt_ptr = (base + 8) as *mut usize;
    let rc = unsafe {
        narwhal_pure_core_options_resolve(
            words.as_ptr(),
            words.len(),
            0,
            buf.as_mut_ptr(),
            4,
            cnt_ptr,
        )
    };
    assert_eq!(rc, OPTIONS_MALFORMED);
}

#[test]
fn capacity_over_max_options_is_bound() {
    // A non-representable / oversized capacity is a deterministic bound error,
    // checked before any output pointer span is formed.
    let words = encode(&[host::name("A")], &host::query("a"));
    let mut cnt = COUNTS;
    let rc = raw_call(
        words.as_ptr(),
        words.len(),
        1,
        std::ptr::null_mut(),
        65_537,
        &mut cnt,
    );
    assert_eq!(rc, OPTIONS_BOUND);
    assert_eq!(cnt, COUNTS);
}

#[test]
fn misaligned_out_is_malformed() {
    // A misaligned `out` (for u32) is malformed before any span is formed.
    let words = encode(&[host::name("A"), host::name("B")], &host::query("a"));
    let mut buf: Vec<u32> = vec![SENT; 16];
    let _ = &mut buf;
    let misaligned = (buf.as_mut_ptr() as usize + 1) as *mut u32;
    let mut cnt = COUNTS;
    let rc = unsafe {
        narwhal_pure_core_options_resolve(words.as_ptr(), words.len(), 0, misaligned, 2, &mut cnt)
    };
    assert_eq!(rc, OPTIONS_MALFORMED);
    assert_eq!(cnt, COUNTS);
}

#[test]
fn null_out_nonzero_capacity_is_malformed() {
    // `out` may be null only when `capacity == 0`; otherwise it is malformed.
    let words = encode(&[host::name("A")], &host::query("a"));
    let mut cnt = COUNTS;
    let rc = raw_call(
        words.as_ptr(),
        words.len(),
        1,
        std::ptr::null_mut(),
        4,
        &mut cnt,
    );
    assert_eq!(rc, OPTIONS_MALFORMED);
    assert_eq!(cnt, COUNTS);
}

// ===========================================================================
// constructors are kept alive (and agree with the engine) — the FFI decodes via
// struct literals, so `Name::new` / `Query::new` would otherwise be dead code
// in this standalone test crate.
// ===========================================================================

#[test]
fn constructors_reject_shape_and_bounds() {
    // Shape mismatch is rejected by the constructor.
    let bad = Name {
        original: vec![0x0061, 0x0062],
        folded: vec![0x0061, 0x0062],
        per_char_folded: vec![vec![0x0061]], // length mismatch on purpose
        alphabetic: vec![true, true],
    };
    assert_eq!(
        Name::new(
            bad.original.clone(),
            bad.folded.clone(),
            bad.per_char_folded.clone(),
            bad.alphabetic.clone()
        )
        .unwrap_err(),
        Error::ShapeMismatch
    );
    // Over-limit is rejected by the constructor.
    assert_eq!(
        Query::new(vec![0x0061; 4097], 1, false).unwrap_err(),
        Error::QueryTooLong
    );
    // Valid inputs are accepted.
    assert!(Name::new(vec![0x0061], vec![0x0061], vec![vec![0x0061]], vec![false]).is_ok());
    assert!(Query::new(vec![0x0061], 1, false).is_ok());
}
