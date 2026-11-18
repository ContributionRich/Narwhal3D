//! OPTIONS-ENGINE-29 — standalone engine tests for `src/options_owned.rs`.
//!
//! This file references the source directly with `#[path]` so **no `lib.rs`
//! edit is required**. It is independent: no external crates, no host (Python)
//! needed to run.
//!
//! Expected outcomes are of two kinds, both stated explicitly:
//! * **hand-derived** — worked out from the documented reference rules, and
//!   cross-checked against the original `options.py` (see
//!   the private original-behavior capture);
//! * **frozen cases** — extracted privately from the original `options.py`
//!   (the CIRCLE / Point-duplicate / A-B-ab / 1Point-2Point / Scale-enum /
//!   Unicode-fold cases), recorded here verbatim.
//!
//! The host (Python) supplies the Unicode coercion metadata — `folded`,
//! `per_char_folded`, `alphabetic`. This file builds it with a small **private**
//! `host` helper that mirrors Python `lower()`/`isalpha()` for the test inputs
//! only. The engine under test performs **no** Unicode classification of its own.
//!
//! Run standalone (from the crate directory):
//!   rustc --edition=2021 --test tests/options29_engine.rs -o /tmp/opts29
//!   /tmp/opts29

use narwhal_pure_core::options_owned::{
    assign, resolve, resolve_enum, EnumStatus, Error, Name, Query, ResolveStatus,
};

// ---------------------------------------------------------------------------
// private host emulation — mirrors Python str.lower()/str.isalpha() for the
// test inputs. A codepoint not in this table folds to itself ("no expansion"),
// the safe default when the host does not expand a codepoint.
// ---------------------------------------------------------------------------

mod host {
    /// `cp -> [folded codepoints]` (Python `str.lower()` for single chars).
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

    pub fn is_alpha(cp: u32) -> bool {
        ALPHA.contains(&cp)
    }

    /// Build a `Name` the way Python would: `folded = name.lower()`;
    /// `per_char_folded[i]` = codepoints of `original[i].lower()` (parallel);
    /// `alphabetic[i]` = `original[i].isalpha()`.
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

    /// Build a `Query` the way Python would: `folded = q.lower()`,
    /// `original_len = len(q)`, `is_digit = q.isdigit()` (decimal digits).
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
// assign_keys — frozen cases from options.py
// ---------------------------------------------------------------------------

/// Frozen: `opts.assign_keys` over the CIRCLE set. Keys: Deformable->D(pos 0),
/// 2Point->P(pos 1), 3Point->o(pos 2), Vertical->V(pos 0).
#[test]
fn assign_circle_frozen() {
    let names = [
        host::name("Deformable"),
        host::name("2Point"),
        host::name("3Point"),
        host::name("Vertical"),
    ];
    let keys = assign(&names).expect("assign CIRCLE");
    assert_eq!(keys, vec![Some(0), Some(1), Some(2), Some(0)]);
}

/// Frozen: duplicate names share one key; the LAST duplicate wins and every
/// duplicate resolves to that same final key (here 'o' at position 1).
#[test]
fn assign_point_duplicate_frozen() {
    let names = [host::name("Point"), host::name("Point"), host::name("X")];
    let keys = assign(&names).expect("assign duplicates");
    assert_eq!(keys, vec![Some(1), Some(1), Some(0)]);
}

/// Frozen: A, B, ab -> A, B, None (a/b claimed when "ab" is reached).
#[test]
fn assign_letter_exhaustion_frozen() {
    let names = [host::name("A"), host::name("B"), host::name("ab")];
    let keys = assign(&names).expect("assign exhaustion");
    assert_eq!(keys, vec![Some(0), Some(0), None]);
}

/// Frozen: digits never claimed; 1Point->P(pos 1), 2Point->o(pos 2).
#[test]
fn assign_digit_names_frozen() {
    let names = [host::name("1Point"), host::name("2Point")];
    let keys = assign(&names).expect("assign digit names");
    assert_eq!(keys, vec![Some(1), Some(2)]);
}

/// Hand-derived: A then AB — 'a' claimed by A, so AB claims 'b' (pos 1).
#[test]
fn assign_partial_claim() {
    let names = [host::name("A"), host::name("AB")];
    let keys = assign(&names).expect("assign A/AB");
    assert_eq!(keys, vec![Some(0), Some(1)]);
}

// ---------------------------------------------------------------------------
// resolve — frozen cases from options.py::match
// ---------------------------------------------------------------------------

fn circle_names() -> Vec<Name> {
    vec![
        host::name("Deformable"),
        host::name("2Point"),
        host::name("3Point"),
        host::name("Vertical"),
    ]
}

/// Frozen: each activation letter resolves to the right option.
#[test]
fn resolve_circle_letters_frozen() {
    let names = circle_names();
    let cases: &[(&str, ResolveStatus, Vec<usize>)] = &[
        ("d", ResolveStatus::Set, vec![0]),
        ("p", ResolveStatus::Set, vec![1]),
        ("o", ResolveStatus::Set, vec![2]),
        ("v", ResolveStatus::Set, vec![3]),
    ];
    for (q, want_status, want_idx) in cases {
        let r = resolve(&names, &host::query(q)).expect("resolve");
        assert_eq!(r.status, *want_status, "query {q}");
        assert_eq!(r.option_indices, *want_idx, "query {q}");
    }
}

/// Frozen: pure-digit single-codepoint queries are never an option.
#[test]
fn resolve_digits_are_none_frozen() {
    let names = circle_names();
    for q in ["2", "3"] {
        let r = resolve(&names, &host::query(q)).expect("resolve digit");
        assert_eq!(r.status, ResolveStatus::None, "digit query {q}");
        assert!(r.option_indices.is_empty(), "digit query {q}");
    }
}

/// Frozen: a non-digit prefix of exactly one name wins (2Point via "2p"); a
/// prefix of zero names is none.
#[test]
fn resolve_prefix_frozen() {
    let names = circle_names();
    let r = resolve(&names, &host::query("2p")).expect("resolve 2p");
    assert_eq!(r.status, ResolveStatus::Set);
    assert_eq!(r.option_indices, vec![1]);

    let r = resolve(&names, &host::query("poin")).expect("resolve poin");
    assert_eq!(r.status, ResolveStatus::None, "no name starts with 'poin'");
}

/// Frozen: an exact duplicate name is always ambiguous, and the shared
/// activation letter of both duplicates is ambiguous too.
#[test]
fn resolve_duplicates_are_ambiguous_frozen() {
    let names = [host::name("Point"), host::name("Point"), host::name("X")];
    for q in ["point", "p"] {
        let r = resolve(&names, &host::query(q)).expect("resolve dup");
        assert_eq!(r.status, ResolveStatus::Ambiguous, "dup query {q}");
        assert_eq!(r.option_indices, vec![0, 1], "dup query {q}");
    }
    let r = resolve(&names, &host::query("x")).expect("resolve x");
    assert_eq!(r.status, ResolveStatus::Set);
    assert_eq!(r.option_indices, vec![2]);
    let r = resolve(&names, &host::query("oi")).expect("resolve oi");
    assert_eq!(r.status, ResolveStatus::None);
}

/// Frozen: A / B / ab — exact wins over key for "ab"; key wins for "a"/"b"; an
/// unmatched single codepoint is none.
#[test]
fn resolve_ab_exhaustion_frozen() {
    let names = [host::name("A"), host::name("B"), host::name("ab")];
    let r = resolve(&names, &host::query("a")).expect("resolve a");
    assert_eq!(r.status, ResolveStatus::Set);
    assert_eq!(r.option_indices, vec![0]);

    let r = resolve(&names, &host::query("b")).expect("resolve b");
    assert_eq!(r.status, ResolveStatus::Set);
    assert_eq!(r.option_indices, vec![1]);

    // "ab" is an EXACT name match -> wins over the (absent) key path.
    let r = resolve(&names, &host::query("ab")).expect("resolve ab");
    assert_eq!(r.status, ResolveStatus::Set);
    assert_eq!(r.option_indices, vec![2]);

    let r = resolve(&names, &host::query("c")).expect("resolve c");
    assert_eq!(r.status, ResolveStatus::None);
}

/// Frozen: digit names are resolvable by their full name or a non-digit
/// prefix, but never by a bare digit.
#[test]
fn resolve_digit_names_frozen() {
    let names = [host::name("1Point"), host::name("2Point")];
    let r = resolve(&names, &host::query("1")).expect("resolve 1");
    assert_eq!(r.status, ResolveStatus::None);
    let r = resolve(&names, &host::query("2")).expect("resolve 2");
    assert_eq!(r.status, ResolveStatus::None);
    let r = resolve(&names, &host::query("1point")).expect("resolve 1point");
    assert_eq!(r.status, ResolveStatus::Set);
    assert_eq!(r.option_indices, vec![0]);
    let r = resolve(&names, &host::query("2p")).expect("resolve 2p");
    assert_eq!(r.status, ResolveStatus::Set);
    assert_eq!(r.option_indices, vec![1]);
}

/// Hand-derived: an empty folded query is always None (deterministic; the
/// caller is responsible for not resolving empty input).
#[test]
fn resolve_empty_query_is_none() {
    let names = [host::name("A"), host::name("B")];
    let q = Query {
        folded: Vec::new(),
        original_len: 0,
        is_digit: false,
    };
    let r = resolve(&names, &q).expect("resolve empty");
    assert_eq!(r.status, ResolveStatus::None);
    assert!(r.option_indices.is_empty());
}

/// Hand-derived: a single-codepoint query that is some option's key resolves
/// via the key path and does NOT fall through to the prefix step (which would
/// have added "Apple").
#[test]
fn resolve_single_codepoint_key_beats_prefix() {
    // "A" (key 'a') and "Apple" (key 'p'). Query "a": key path -> "A" only.
    let names = [host::name("A"), host::name("Apple")];
    let r = resolve(&names, &host::query("a")).expect("resolve a");
    assert_eq!(
        r.status,
        ResolveStatus::Set,
        "key path must win, not prefix"
    );
    assert_eq!(r.option_indices, vec![0]);
}

/// Hand-derived: a two-codepoint query uses the prefix step (not the
/// single-codepoint key step) and can still be unique.
#[test]
fn resolve_two_codepoint_prefix() {
    let names = [host::name("C"), host::name("Cab")];
    // "c" is a single codepoint -> key path -> "C".
    let r = resolve(&names, &host::query("c")).expect("resolve c");
    assert_eq!(r.status, ResolveStatus::Set);
    assert_eq!(r.option_indices, vec![0]);
    // "ca" is two codepoints -> prefix -> "Cab" (unique).
    let r = resolve(&names, &host::query("ca")).expect("resolve ca");
    assert_eq!(r.status, ResolveStatus::Set);
    assert_eq!(r.option_indices, vec![1]);
}

// ---------------------------------------------------------------------------
// resolve_enum — frozen cases from options.py::_coerce (ENUM branch)
// ---------------------------------------------------------------------------

fn scale_choices() -> Vec<Name> {
    vec![host::name("No"), host::name("1D"), host::name("3D")]
}

/// Frozen: exact and unique-prefix matches, and no match.
#[test]
fn resolve_enum_scale_frozen() {
    let c = scale_choices();
    let cases: &[(&str, EnumStatus, Vec<usize>)] = &[
        ("No", EnumStatus::Set, vec![0]),
        ("1d", EnumStatus::Set, vec![1]),
        ("3", EnumStatus::Set, vec![2]),
        ("n", EnumStatus::Set, vec![0]),
        ("1", EnumStatus::Set, vec![1]),
        ("3d", EnumStatus::Set, vec![2]),
    ];
    for (q, want_status, want_idx) in cases {
        let r = resolve_enum(&c, &host::query(q)).expect("resolve_enum");
        assert_eq!(r.status, *want_status, "query {q}");
        assert_eq!(r.choice_indices, *want_idx, "query {q}");
    }
    let r = resolve_enum(&c, &host::query("x")).expect("resolve_enum x");
    assert_eq!(r.status, EnumStatus::None);
    assert!(r.choice_indices.is_empty());
}

/// Frozen: an empty enum prefix matches every choice -> ambiguous (3 here).
#[test]
fn resolve_enum_empty_prefix_ambiguous_frozen() {
    let c = scale_choices();
    let q = Query {
        folded: Vec::new(),
        original_len: 0,
        is_digit: false,
    };
    let r = resolve_enum(&c, &q).expect("resolve_enum empty");
    assert_eq!(r.status, EnumStatus::Ambiguous, "empty prefix matches all");
    assert_eq!(r.choice_indices, vec![0, 1, 2]);
}

/// Hand-derived: a single-choice enum resolves the empty prefix to Set.
#[test]
fn resolve_enum_empty_prefix_unique_single() {
    let c = vec![host::name("Only")];
    let q = Query {
        folded: Vec::new(),
        original_len: 0,
        is_digit: false,
    };
    let r = resolve_enum(&c, &q).expect("resolve_enum single");
    assert_eq!(r.status, EnumStatus::Set);
    assert_eq!(r.choice_indices, vec![0]);
}

/// Hand-derived: an ambiguous enum prefix reports every matching choice.
#[test]
fn resolve_enum_ambiguous_prefix() {
    let c = vec![host::name("No"), host::name("Near"), host::name("Yes")];
    let r = resolve_enum(&c, &host::query("n")).expect("resolve_enum n");
    assert_eq!(r.status, EnumStatus::Ambiguous);
    assert_eq!(r.choice_indices, vec![0, 1]);
}

// ---------------------------------------------------------------------------
// Unicode — host-supplied fold, engine opaque (never restricted)
// ---------------------------------------------------------------------------

/// Frozen: 'İ'.lower() == 'i̇' (two codepoints). The engine must treat the
/// host's per-character fold as the source of truth and not classify 'İ'
/// itself; the claim unit is the two-codepoint sequence [i, combining-dot].
#[test]
fn unicode_dotted_i_fold_is_host_supplied() {
    let n = host::name("\u{130}");
    // original: [0x130]; per_char_folded: [[0x69, 0x307]]; alphabetic: [true].
    assert_eq!(n.original, vec![0x0130]);
    assert_eq!(n.per_char_folded, vec![vec![0x0069, 0x0307]]);
    assert_eq!(n.alphabetic, vec![true]);
    let keys = assign(&[n]).expect("assign İ");
    // 'İ' is alphabetic and its per-char fold [i,·] is unclaimed -> pos 0.
    assert_eq!(keys, vec![Some(0)]);
}

/// Frozen: 'ß'.lower() == 'ß' (one codepoint); the host marks it alphabetic.
#[test]
fn unicode_eszett_fold_is_identity() {
    let n = host::name("\u{df}");
    assert_eq!(n.original, vec![0x00df]);
    assert_eq!(n.per_char_folded, vec![vec![0x00df]]);
    assert_eq!(n.alphabetic, vec![true]);
    let keys = assign(&[n]).expect("assign ß");
    assert_eq!(keys, vec![Some(0)]);
}

/// Hand-derived: a lone surrogate is preserved as-is and treated as
/// non-alphabetic (the host decides); the following 'a' is the key.
#[test]
fn unicode_lone_surrogate_preserved() {
    let n = Name {
        original: vec![0xD800u32, 0x0061u32],
        folded: vec![0xD800u32, 0x0061u32],
        per_char_folded: vec![vec![0xD800u32], vec![0x0061u32]],
        alphabetic: vec![false, true],
    };
    let keys = assign(&[n]).expect("assign surrogate");
    assert_eq!(keys, vec![Some(1)]);
}

/// Hand-derived: a non-ASCII alphabetic letter (Ç -> ç) is a valid activation
/// letter when the host marks it alphabetic.
#[test]
fn unicode_non_ascii_alpha_letter() {
    let n = host::name("\u{c7}"); // Ç -> ç
    assert_eq!(n.per_char_folded, vec![vec![0x00e7]]);
    let keys = assign(&[n]).expect("assign Ç");
    assert_eq!(keys, vec![Some(0)]);
}

/// Hand-derived: a pure-Unicode-digit query ("１2") is a digit to the host
/// (is_digit=true) and is therefore never an option via the prefix step.
#[test]
fn unicode_pure_digit_query_is_none() {
    let names = [host::name("１Point"), host::name("2Point")];
    let q = Query {
        folded: vec![0xFF11u32, 0x0032u32],
        original_len: 2,
        is_digit: true,
    };
    let r = resolve(&names, &q).expect("resolve unicode digit");
    assert_eq!(
        r.status,
        ResolveStatus::None,
        "pure-digit query is never an option"
    );
}

/// Hand-derived: the 'İ' fold ([i,·]) is a DISTINCT claim from a plain 'i', so
/// a name keyed on 'i' does not exhaust the 'İ' letter, and vice versa.
#[test]
fn unicode_expanded_fold_is_distinct_claim() {
    // "i" claims the sequence [i]; "İ" claims [i,·] — a different sequence.
    let names = [host::name("i"), host::name("\u{130}")];
    let keys = assign(&names).expect("assign i/İ");
    assert_eq!(keys, vec![Some(0), Some(0)]);
}

// ---------------------------------------------------------------------------
// deterministic bounds and errors (no panics)
// ---------------------------------------------------------------------------

#[test]
fn bounds_shape_mismatch_is_error() {
    let n = Name {
        original: vec![0x0061, 0x0062],
        folded: vec![0x0061, 0x0062],
        per_char_folded: vec![vec![0x0061]], // length mismatch on purpose
        alphabetic: vec![true, true],
    };
    assert!(assign(&[n]).is_err(), "shape mismatch must be an error");
}

#[test]
fn bounds_name_too_long_is_error() {
    let long = vec![0x0061u32; 4097]; // > MAX_NAME_CODEPOINTS (4096)
    let n = Name {
        original: long.clone(),
        folded: long.clone(),
        per_char_folded: vec![vec![0x0061u32]; 4097],
        alphabetic: vec![true; 4097],
    };
    assert_eq!(assign(&[n]).unwrap_err(), Error::NameTooLong);
}

#[test]
fn bounds_query_too_long_is_error() {
    let long = vec![0x0061u32; 4097]; // > MAX_QUERY_CODEPOINTS (4096)
    let q = Query {
        folded: long,
        original_len: 4097,
        is_digit: false,
    };
    let names = [host::name("A")];
    assert_eq!(resolve(&names, &q).unwrap_err(), Error::QueryTooLong);
}

#[test]
fn bounds_too_many_options_is_error() {
    let names: Vec<Name> = (0..=65_536)
        .map(|i| host::name(&format!("O{i:05}")))
        .collect();
    assert_eq!(names.len(), 65_537);
    assert_eq!(assign(&names).unwrap_err(), Error::TooManyOptions);
}

// ---------------------------------------------------------------------------
// assign/resolve — ORIGINAL-exact name identity (frozen from options.py)
// ---------------------------------------------------------------------------

/// Frozen: case-variants are DISTINCT options (Python dict keyed by original
/// name). "A" claims `a` (pos 0); "a" finds it already claimed -> None.
#[test]
fn assign_case_variants_are_distinct_frozen() {
    let names = [host::name("A"), host::name("a")];
    let keys = assign(&names).expect("assign A/a");
    assert_eq!(keys, vec![Some(0), None]);
}

/// Frozen: last original-identical duplicate wins and EVERY occurrence shares
/// that final key. "A" (idx0) claims `a`; "a" (idx1) is a distinct name but
/// finds `a` claimed; "A" (idx2) overwrites the "A" entry with None.
/// All three resolve to None — measured via options.py::assign_keys.
#[test]
fn assign_case_variant_duplicate_last_wins_frozen() {
    let names = [host::name("A"), host::name("a"), host::name("A")];
    let keys = assign(&names).expect("assign A/a/A");
    assert_eq!(keys, vec![None, None, None]);
}

/// Frozen: "Straße" and "straße" are distinct names; the second claims `t`
/// (pos 1) because `s` is taken — measured via options.py::assign_keys.
#[test]
fn assign_strasse_variants_distinct_frozen() {
    let names = [host::name("Straße"), host::name("straße")];
    let keys = assign(&names).expect("assign Straße/straße");
    assert_eq!(keys, vec![Some(0), Some(1)]);
}

/// Frozen: case-variant exact query is ambiguous (exact is case-insensitive);
/// a query that is only one variant's key is Set for that option.
#[test]
fn resolve_case_variants_semantics_frozen() {
    let names = [host::name("Point"), host::name("point"), host::name("X")];
    // "point": exact (case-insensitive) hits BOTH -> ambiguous.
    let r = resolve(&names, &host::query("point")).expect("resolve point");
    assert_eq!(r.status, ResolveStatus::Ambiguous);
    assert_eq!(r.option_indices, vec![0, 1]);
    // "p": key path — "Point" has key P (pos0) folding to [p]; "point" has key
    // o (pos1). So "p" selects "Point" (index 0). Measured via options.py::match.
    let r = resolve(&names, &host::query("p")).expect("resolve p");
    assert_eq!(r.status, ResolveStatus::Set);
    assert_eq!(r.option_indices, vec![0]);
    // "x": key path -> "X" only.
    let r = resolve(&names, &host::query("x")).expect("resolve x");
    assert_eq!(r.status, ResolveStatus::Set);
    assert_eq!(r.option_indices, vec![2]);
}

/// Frozen: A/a — "a" is exact for both (ambiguous), "b" is none.
#[test]
fn resolve_case_variants_exact_ambiguous_frozen() {
    let names = [host::name("A"), host::name("a")];
    for q in ["a", "A"] {
        let r = resolve(&names, &host::query(q)).expect("resolve");
        assert_eq!(r.status, ResolveStatus::Ambiguous, "query {q}");
        assert_eq!(r.option_indices, vec![0, 1], "query {q}");
    }
    let r = resolve(&names, &host::query("b")).expect("resolve b");
    assert_eq!(r.status, ResolveStatus::None);
}

/// Frozen: the key path (not exact) distinguishes case-variants — "a" is
/// "A"'s key, so Set for "A" even though "a" is also an exact match for "a".
/// (Exact already caught both here; this set has no exact for "a" because the
/// second name is "Apple", whose key is "p".)
#[test]
fn resolve_case_variant_key_beats_prefix_frozen() {
    let names = [host::name("A"), host::name("Apple")];
    let r = resolve(&names, &host::query("a")).expect("resolve a");
    assert_eq!(r.status, ResolveStatus::Set, "key path wins, not prefix");
    assert_eq!(r.option_indices, vec![0]);
}

// ---------------------------------------------------------------------------
// resolve_enum — first-exact-wins (frozen from options.py::_coerce)
// ---------------------------------------------------------------------------

/// Frozen: duplicate exact choices — Python returns the FIRST, not ambiguous.
/// Measured: _coerce(Sharp, Sharp) + "sharp" -> (Sharp, None).
#[test]
fn resolve_enum_duplicate_exact_first_frozen() {
    let c = vec![host::name("Sharp"), host::name("Sharp")];
    let r = resolve_enum(&c, &host::query("sharp")).expect("enum dup");
    assert_eq!(r.status, EnumStatus::Set);
    assert_eq!(r.choice_indices, vec![0]);
}

/// Frozen: case-equivalent exact choices — FIRST in order wins.
/// Measured: _coerce(Sharp, SHARP) + "sharp" -> (Sharp, None);
///          _coerce(SHARP, Sharp) + "sharp" -> (SHARP, None).
#[test]
fn resolve_enum_case_equivalent_first_frozen() {
    let c = vec![host::name("Sharp"), host::name("SHARP")];
    let r = resolve_enum(&c, &host::query("sharp")).expect("enum case-eq");
    assert_eq!(r.status, EnumStatus::Set);
    assert_eq!(r.choice_indices, vec![0]);

    let c = vec![host::name("SHARP"), host::name("Sharp")];
    let r = resolve_enum(&c, &host::query("sharp")).expect("enum case-eq");
    assert_eq!(r.status, EnumStatus::Set);
    assert_eq!(r.choice_indices, vec![0]);
}

/// Frozen: an empty exact choice beats the (ambiguous) prefix path.
/// Measured: _coerce(choices=['','a']) + '' -> ('', None)  [not ambiguous].
#[test]
fn resolve_enum_empty_exact_choice_beats_prefix_frozen() {
    let c = vec![host::name(""), host::name("a")];
    let q = Query {
        folded: Vec::new(),
        original_len: 0,
        is_digit: false,
    };
    let r = resolve_enum(&c, &q).expect("enum empty exact");
    assert_eq!(r.status, EnumStatus::Set);
    assert_eq!(r.choice_indices, vec![0]);

    // Two empty choices: still the FIRST exact (index 0), not ambiguous.
    let c = vec![host::name(""), host::name("")];
    let r = resolve_enum(&c, &q).expect("enum empty exact x2");
    assert_eq!(r.status, EnumStatus::Set);
    assert_eq!(r.choice_indices, vec![0]);
}

/// Frozen: with NO exact match, the prefix path keeps its ambiguity.
/// "sh" is not exact for "Sharp"/"SHARP" -> both are prefix hits -> ambiguous.
#[test]
fn resolve_enum_prefix_ambiguous_without_exact_frozen() {
    let c = vec![host::name("Sharp"), host::name("SHARP")];
    let r = resolve_enum(&c, &host::query("sh")).expect("enum prefix");
    assert_eq!(r.status, EnumStatus::Ambiguous);
    assert_eq!(r.choice_indices, vec![0, 1]);
}

// ---------------------------------------------------------------------------
// consistent bounds — no unchecked blowups, all entry points agree
// ---------------------------------------------------------------------------

/// A name whose folded vector is unbounded (original short) is a bound error
/// at every entry point: assign, resolve, and the Name::new constructor.
#[test]
fn bounds_folded_name_too_long_is_error() {
    let n = Name {
        original: vec![0x0061],
        folded: vec![0x0061; 4097], // > MAX_NAME_CODEPOINTS
        per_char_folded: vec![vec![0x0061]],
        alphabetic: vec![true],
    };
    let names = [n];
    assert_eq!(assign(&names).unwrap_err(), Error::NameTooLong);
    let q = Query {
        folded: vec![0x0061],
        original_len: 1,
        is_digit: false,
    };
    assert_eq!(resolve(&names, &q).unwrap_err(), Error::NameTooLong);
    let bad = Name {
        original: vec![0x0061],
        folded: vec![0x0061; 4097],
        per_char_folded: vec![vec![0x0061]],
        alphabetic: vec![true],
    };
    assert_eq!(
        Name::new(
            bad.original,
            bad.folded,
            bad.per_char_folded,
            bad.alphabetic
        )
        .unwrap_err(),
        Error::NameTooLong
    );
}

/// A query whose original_len exceeds the bound is a bound error at every
/// entry point: resolve, resolve_enum, and Query::new.
#[test]
fn bounds_query_original_len_too_long_is_error() {
    let q = Query {
        folded: vec![0x0061],
        original_len: 4097, // > MAX_QUERY_CODEPOINTS
        is_digit: false,
    };
    let names = [host::name("A")];
    assert_eq!(resolve(&names, &q).unwrap_err(), Error::QueryTooLong);
    assert_eq!(resolve_enum(&names, &q).unwrap_err(), Error::QueryTooLong);
    assert_eq!(
        Query::new(vec![0x0061], 4097, false).unwrap_err(),
        Error::QueryTooLong
    );
}

/// A per-character fold sequence longer than the bound is a bound error
/// (not a shape error) at every entry point.
#[test]
fn bounds_per_char_fold_too_long_is_error() {
    let n = Name {
        original: vec![0x0130],
        folded: vec![0x0061; 4097],
        per_char_folded: vec![vec![0x0061; 4097]],
        alphabetic: vec![true],
    };
    let names = [n];
    assert_eq!(assign(&names).unwrap_err(), Error::NameTooLong);
    let q = Query {
        folded: vec![0x0061],
        original_len: 1,
        is_digit: false,
    };
    assert_eq!(resolve(&names, &q).unwrap_err(), Error::NameTooLong);
    let bad = Name {
        original: vec![0x0130],
        folded: vec![0x0061; 4097],
        per_char_folded: vec![vec![0x0061; 4097]],
        alphabetic: vec![true],
    };
    assert_eq!(
        Name::new(
            bad.original,
            bad.folded,
            bad.per_char_folded,
            bad.alphabetic
        )
        .unwrap_err(),
        Error::NameTooLong
    );
}

/// Aggregate consistency: the SAME invalid input is rejected the same way by
/// the constructor and the resolver entry points (no entry point accepts what
/// another rejects). Valid-at-the-limit inputs are accepted everywhere.
#[test]
fn bounds_aggregate_inputs_consistent() {
    // At-limit (exactly MAX) is valid at every entry point.
    let at = Name {
        original: vec![0x0061; 4096],
        folded: vec![0x0061; 4096],
        per_char_folded: vec![vec![0x0061]; 4096],
        alphabetic: vec![true; 4096],
    };
    assert!(
        Name::new(
            at.original.clone(),
            at.folded.clone(),
            at.per_char_folded.clone(),
            at.alphabetic.clone()
        )
        .is_ok(),
        "at-limit name must be accepted by Name::new"
    );
    assert!(
        assign(std::slice::from_ref(&at)).is_ok(),
        "at-limit name must be accepted by assign"
    );
    let q = Query {
        folded: vec![0x0061; 4096],
        original_len: 4096,
        is_digit: false,
    };
    assert!(
        Query::new(q.folded.clone(), q.original_len, q.is_digit).is_ok(),
        "at-limit query must be accepted by Query::new"
    );
    assert!(
        resolve(&[at], &q).is_ok(),
        "at-limit name+query must resolve"
    );

    // Over-limit is rejected by BOTH constructor and resolver (same error).
    let over = Name {
        original: vec![0x0061; 4097],
        folded: vec![0x0061; 4097],
        per_char_folded: vec![vec![0x0061]; 4097],
        alphabetic: vec![true; 4097],
    };
    assert_eq!(
        Name::new(
            over.original.clone(),
            over.folded.clone(),
            over.per_char_folded.clone(),
            over.alphabetic.clone()
        )
        .unwrap_err(),
        Error::NameTooLong,
        "over-limit name must be rejected by Name::new"
    );
    assert_eq!(
        assign(&[over]).unwrap_err(),
        Error::NameTooLong,
        "over-limit name must be rejected by assign"
    );
}

// ---------------------------------------------------------------------------
// the API is usable standalone (no FFI, no lib.rs edit, no extra crates)
// ---------------------------------------------------------------------------

#[test]
fn api_usable_standalone() {
    let names = [host::name("Box"), host::name("Curve")];
    let keys = assign(&names).expect("assign");
    assert_eq!(keys, vec![Some(0), Some(0)]); // both keyed on their first letter
    let r = resolve(&names, &host::query("b")).expect("resolve");
    assert_eq!(r.status, ResolveStatus::Set);
    let c = vec![host::name("Sharp"), host::name("Smooth")];
    let e = resolve_enum(&c, &host::query("sh")).expect("enum");
    assert_eq!(e.status, EnumStatus::Set);
}
