//! OPTIONS-ENGINE-29 — the command-line option framework, pure engine.
//!
//! This is the Rust core of `narwhal3d/options.py`: what an option *is*, how a
//! typed name is matched against a set of options, and which option(s) a typed
//! name selects. It is **pure and deterministic** — no I/O, no panics, no
//! Unicode classification. It deliberately owns *none* of the following:
//!
//! * **value coercion** (`parse_bool` / `coerce`),
//! * **prompt rendering** (`format_option` / `prompt_segments` / the `%g` float
//!   branch),
//! * **command state** (`self.opt`, `pending_option`, `mode`).
//!
//! Those stay in the host (Python) for a later packet. This engine is only the
//! identity/matching resolver: given option *names* and a typed *query*, which
//! option index is selected, and whether the selection is ambiguous.
//!
//! # Unicode contract (host supplies the coercion metadata)
//!
//! The engine never classifies Unicode itself. The host (Python) builds
//! [`Name`] / [`Query`] using the host's own semantics and passes them in:
//!
//! * `Name::folded`          — Python `name.lower()` (whole-name fold).
//! * `Name::per_char_folded` — Python `[c.lower() for c in name]` (one codepoint
//!   per original char, parallel to `original`).
//! * `Name::alphabetic`      — Python `[c.isalpha() for c in name]` (parallel).
//! * `Query::folded`         — Python `name_part.lower()`.
//! * `Query::original_len`   — Python `len(name_part)` (original codepoints).
//! * `Query::is_digit`       — Python `name_part.isdigit()`.
//!
//! `original` / `per_char_folded` are `Vec<u32>` so **lone surrogates are
//! preserved as-is**; the host is free to keep non-BMP and surrogate codepoints
//! untouched. The engine only compares the codepoints the host gave it. This is
//! *host-supplied* metadata, not a new Rust Unicode classifier, and it must not
//! be used to restrict existing option names.

use std::collections::{BTreeSet, HashMap};

// ---------------------------------------------------------------------------
// deterministic bounds (checked before any allocation -> Error, never a panic)
// ---------------------------------------------------------------------------

/// Maximum number of options in one set (mirrors the crate's existing
/// conservative admission style; checked before any span is formed).
pub const MAX_OPTIONS: usize = 65_536;

/// Maximum codepoints in a single option name.
pub const MAX_NAME_CODEPOINTS: usize = 4_096;

/// Maximum codepoints in a typed query.
pub const MAX_QUERY_CODEPOINTS: usize = 4_096;

// ---------------------------------------------------------------------------
// errors
// ---------------------------------------------------------------------------

/// A deterministic failure. The engine never panics: every out-of-bound or
/// malformed input maps to one of these.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    /// More than [`MAX_OPTIONS`] options were supplied.
    TooManyOptions,
    /// An option name is longer than [`MAX_NAME_CODEPOINTS`].
    NameTooLong,
    /// A query is longer than [`MAX_QUERY_CODEPOINTS`].
    QueryTooLong,
    /// The per-character vectors of a [`Name`] are not parallel to `original`
    /// (a host-side shape error).
    ShapeMismatch,
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            Error::TooManyOptions => "too many options",
            Error::NameTooLong => "option name too long",
            Error::QueryTooLong => "query too long",
            Error::ShapeMismatch => "name vectors not parallel",
        };
        f.write_str(s)
    }
}

impl std::error::Error for Error {}

// ---------------------------------------------------------------------------
// input types
// ---------------------------------------------------------------------------

/// One option *name* (not an option's value). The host fills the folded and
/// classification vectors from Python; the engine treats them as opaque data.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Name {
    /// Original codepoints, in order. Lone surrogates are preserved as-is.
    pub original: Vec<u32>,
    /// Whole-name folded codepoints (host: Python `name.lower()`). Used for exact
    /// and prefix matching.
    pub folded: Vec<u32>,
    /// Per-character folded codepoints, **parallel to `original`**: one entry per
    /// original character, each entry that character's folded codepoint sequence
    /// (host: `[[c.lower() for c in name]]` flattened per char, i.e.
    /// `per_char_folded[i]` = the codepoints of `original[i].lower()`). A
    /// single codepoint maps to a one-element inner vec; an expansion like
    /// `İ -> i̇` maps to two. The engine compares these whole sequences, exactly
    /// as Python compares `ch.lower()` strings.
    pub per_char_folded: Vec<Vec<u32>>,
    /// Per-character alphabetic flag, **parallel to `original`**
    /// (host: `[c.isalpha() for c in name]`). The engine only walks the positions
    /// the host marked alphabetic.
    pub alphabetic: Vec<bool>,
}

impl Name {
    /// Convenience constructor for callers that already have the four vectors.
    /// Returns `None` (not a panic) when the parallel vectors disagree in length.
    pub fn new(
        original: Vec<u32>,
        folded: Vec<u32>,
        per_char_folded: Vec<Vec<u32>>,
        alphabetic: Vec<bool>,
    ) -> Result<Self, Error> {
        let n = Self {
            original,
            folded,
            per_char_folded,
            alphabetic,
        };
        validate(&n)?;
        Ok(n)
    }
}

/// A typed query (the name-part of what the user typed, before any `=value`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Query {
    /// Folded codepoints (host: Python `name_part.lower()`).
    pub folded: Vec<u32>,
    /// Original codepoint count (host: `len(name_part)`). Used for the
    /// single-codepoint activation-letter guard — it is the *original* length,
    /// not the folded length, because the host checks `len(name_part) == 1`.
    pub original_len: usize,
    /// Host: Python `name_part.isdigit()`. A pure-digit query is never an option
    /// (digits are the number path), so the prefix step is skipped for these.
    pub is_digit: bool,
}

impl Query {
    pub fn new(folded: Vec<u32>, original_len: usize, is_digit: bool) -> Result<Self, Error> {
        if folded.len() > MAX_QUERY_CODEPOINTS || original_len > MAX_QUERY_CODEPOINTS {
            return Err(Error::QueryTooLong);
        }
        Ok(Self {
            folded,
            original_len,
            is_digit,
        })
    }
}

// ---------------------------------------------------------------------------
// activation-letter assignment (options.py::assign_keys)
// ---------------------------------------------------------------------------

/// Assign each option its activation letter, faithfully mirroring
/// `options.py::assign_keys`.
///
/// Options are walked in display order; each option claims the first alphabetic
/// position of its name (host `isalpha()`) whose per-character fold no earlier
/// option has already claimed. Digits are never claimed because the host marks
/// only alphabetic positions.
///
/// **The key is the ORIGINAL exact name** — Python builds a `name -> key` dict
/// keyed by `opt.name`, so two names that are case-variants (`"A"` and `"a"`,
/// `"Straße"` and `"straße"`) are **distinct options** that must not overwrite
/// one another. Only original-identical duplicates overwrite: the dict assigns
/// in order, so the LAST duplicate's key is stored under the name, and the
/// matcher resolves **every** occurrence of that name through that same final
/// key (e.g. `["A", "a", "A"]` -> `[None, None, None]`, because the final
/// assignment for the name "A" happens at index 2, after "a" claimed `a`).
/// An all-letters-claimed name maps to `None` (it stays type-able by name,
/// just has no highlighted letter).
///
/// Returns, indexed by option index, the **position** (within that option's
/// name) of its activation letter, or `None`. The host recovers the original
/// letter from `Name::original[pos]` and its folded form from
/// `Name::per_char_folded[pos]`.
pub fn assign(names: &[Name]) -> Result<Vec<Option<usize>>, Error> {
    if names.len() > MAX_OPTIONS {
        return Err(Error::TooManyOptions);
    }
    // Python's `keys = {}` is keyed by the ORIGINAL exact name: an
    // original-identical duplicate OVERWRITES its entry (last wins), while
    // case-variants ("A" vs "a") are distinct keys. HashMap mirrors this in O(n).
    let mut name_key: HashMap<Vec<u32>, Option<usize>> = HashMap::with_capacity(names.len());
    let mut claimed: BTreeSet<Vec<u32>> = BTreeSet::new();

    for n in names {
        validate(n)?;
        // Every occurrence re-claims: the claimed set only ever grows.
        let k = first_free_alpha(n, &claimed);
        if let Some(pos) = k {
            claimed.insert(n.per_char_folded[pos].clone());
        }
        name_key.insert(n.original.clone(), k); // last original-identical wins
    }
    // The stored value is the key for EACH occurrence of that original name.
    let per_option: Vec<Option<usize>> = names
        .iter()
        .map(|n| name_key.get(&n.original).copied().flatten())
        .collect();
    Ok(per_option)
}

/// First alphabetic position of `n` whose per-character fold is not yet claimed.
/// The "claimed" unit is the whole per-character fold sequence (exactly as
/// Python claims `ch.lower()` strings), so an expansion like `İ -> i̇` claims a
/// two-codepoint sequence and is distinct from a plain `i`.
fn first_free_alpha(n: &Name, claimed: &BTreeSet<Vec<u32>>) -> Option<usize> {
    for (i, is_alpha) in n.alphabetic.iter().enumerate() {
        if !is_alpha {
            continue;
        }
        if !claimed.contains(&n.per_char_folded[i]) {
            return Some(i);
        }
    }
    None
}

// ---------------------------------------------------------------------------
// name resolution (the option half of options.py::match)
// ---------------------------------------------------------------------------

/// The outcome of resolving one typed name-part against a set of option names.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResolveStatus {
    /// The query looked like no option (the host treats it as a step argument).
    None,
    /// The query fits more than one option; the reference reports, not guesses.
    Ambiguous,
    /// Exactly one option was selected.
    Set,
}

/// Result of [`resolve`]. `option_indices` are the *original* option indices
/// (positions in the supplied slice) that the query selected.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolveResult {
    pub status: ResolveStatus,
    /// Selected option indices: empty for `None`, one for `Set`, more than one
    /// for `Ambiguous`.
    pub option_indices: Vec<usize>,
}

/// Resolve one typed name-part against `names`, in the reference's priority
/// order: exact (case-insensitive) name, then — only for a one-original-codepoint
/// query — the activation letter, then — only for non-digit queries — prefix.
/// Every matched option is reported, so duplicates can be `Ambiguous`.
/// Empty folded queries resolve to `None` (the caller avoids calling with them).
pub fn resolve(names: &[Name], query: &Query) -> Result<ResolveResult, Error> {
    if names.len() > MAX_OPTIONS {
        return Err(Error::TooManyOptions);
    }
    for n in names {
        validate(n)?;
    }
    if query.folded.len() > MAX_QUERY_CODEPOINTS || query.original_len > MAX_QUERY_CODEPOINTS {
        return Err(Error::QueryTooLong);
    }

    let qf = &query.folded;
    if qf.is_empty() {
        return Ok(ResolveResult {
            status: ResolveStatus::None,
            option_indices: Vec::new(),
        });
    }

    // 1. exact (case-insensitive) name
    let exact: Vec<usize> = names
        .iter()
        .enumerate()
        .filter(|(_, n)| n.folded == *qf)
        .map(|(i, _)| i)
        .collect();
    if !exact.is_empty() {
        return Ok(finish(exact));
    }

    // 2. single-codepoint activation letter (guard on ORIGINAL codepoint count)
    if query.original_len == 1 {
        let keys = assign(names)?;
        let hits: Vec<usize> = names
            .iter()
            .enumerate()
            .filter(|(i, n)| {
                keys[*i]
                    .map(|pos| n.per_char_folded[pos] == *qf)
                    .unwrap_or(false)
            })
            .map(|(i, _)| i)
            .collect();
        if !hits.is_empty() {
            return Ok(finish(hits));
        }
    }

    // 3. prefix (skipped for pure-digit queries)
    if !query.is_digit {
        let hits: Vec<usize> = names
            .iter()
            .enumerate()
            .filter(|(_, n)| n.folded.starts_with(qf))
            .map(|(i, _)| i)
            .collect();
        return Ok(finish(hits));
    }

    Ok(ResolveResult {
        status: ResolveStatus::None,
        option_indices: Vec::new(),
    })
}

fn finish(indices: Vec<usize>) -> ResolveResult {
    let status = if indices.is_empty() {
        ResolveStatus::None
    } else if indices.len() > 1 {
        ResolveStatus::Ambiguous
    } else {
        ResolveStatus::Set
    };
    ResolveResult {
        status,
        option_indices: indices,
    }
}

// ---------------------------------------------------------------------------
// enum value resolution (the ENUM half of options.py::_coerce)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EnumStatus {
    /// No choice matched.
    None,
    /// More than one choice matched; the reference reports the candidates.
    Ambiguous,
    /// Exactly one choice matched.
    Set,
}

/// Result of [`resolve_enum`]. `choice_indices` are indices into the *choices*
/// slice that the query selected.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnumResult {
    pub status: EnumStatus,
    pub choice_indices: Vec<usize>,
}

/// Resolve a typed value against an ENUM's `choices`, mirroring
/// `options.py::_coerce` for the ENUM kind: the **first** exact (case-insensitive)
/// choice wins — even over later duplicate or case-equivalent choices; else a
/// unique prefix selects that choice; else an ambiguous prefix reports all
/// matching choices; else no match.
/// An **empty** query matches every choice: `Set` if exactly one, else
/// `Ambiguous` (the main [`resolve`] returns `None` for an empty query).
pub fn resolve_enum(choices: &[Name], query: &Query) -> Result<EnumResult, Error> {
    if choices.len() > MAX_OPTIONS {
        return Err(Error::TooManyOptions);
    }
    for c in choices {
        validate(c)?;
    }
    if query.folded.len() > MAX_QUERY_CODEPOINTS || query.original_len > MAX_QUERY_CODEPOINTS {
        return Err(Error::QueryTooLong);
    }

    let qf = &query.folded;

    // exact — Python's _coerce returns the FIRST exact choice immediately,
    // even with duplicate or case-equivalent siblings; only when there is no
    // exact match at all does the prefix path (and its ambiguity) apply.
    let first_exact = choices
        .iter()
        .enumerate()
        .find(|(_, c)| c.folded == *qf)
        .map(|(i, _)| i);
    if let Some(i) = first_exact {
        return Ok(enum_finish(vec![i]));
    }

    // prefix (an empty prefix matches everything -> unique or ambiguous)
    let hits: Vec<usize> = choices
        .iter()
        .enumerate()
        .filter(|(_, c)| c.folded.starts_with(qf))
        .map(|(i, _)| i)
        .collect();
    Ok(enum_finish(hits))
}

fn enum_finish(indices: Vec<usize>) -> EnumResult {
    let status = if indices.is_empty() {
        EnumStatus::None
    } else if indices.len() > 1 {
        EnumStatus::Ambiguous
    } else {
        EnumStatus::Set
    };
    EnumResult {
        status,
        choice_indices: indices,
    }
}

// ---------------------------------------------------------------------------
// validation
// ---------------------------------------------------------------------------

fn validate(n: &Name) -> Result<(), Error> {
    if n.original.len() > MAX_NAME_CODEPOINTS || n.folded.len() > MAX_NAME_CODEPOINTS {
        return Err(Error::NameTooLong);
    }
    if n.original.len() != n.per_char_folded.len() || n.original.len() != n.alphabetic.len() {
        return Err(Error::ShapeMismatch);
    }
    for pc in n.per_char_folded.iter() {
        if pc.len() > MAX_NAME_CODEPOINTS {
            return Err(Error::NameTooLong);
        }
    }
    Ok(())
}
