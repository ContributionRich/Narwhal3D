//! Replay of the frozen Unicode classifier digests captured pre-retirement
//! (source: `spike/test_ui_core_golden.py`, `DATA["unicode"]`).
//!
//! The frozen data was recorded by `test_full_unicode_classifier` (see
//! `spike/test_ui_core_editor.py`): for `start` in `range(0, 0x110000, 8192)` it
//! built `"".join(map(chr, range(start, min(start + 8192, 0x110000))))`
//! (all 1,114,112 code points, including the surrogate range U+D800..U+DFFF),
//! fed it to `TextField(text, True)`, and stored
//! `sha256(result_text.encode("utf-32-le", "surrogatepass"))` per version
//! (`15.1.0` and `16.0.0`), 136 chunk digests each.
//!
//! `TextField` is a private (`mod editor;`) type and is deliberately not
//! re-exported, so the replay goes through the **public FFI** (`narwhal_editor_*`)
//! exactly as the live Python adapter does: construct is op 0 with flags
//! `1` (multiline) and, for the `16.0.0` version only, `8` (Unicode-16.0
//! classifier). The input is supplied as raw `Vec<u32>` code points (lone
//! surrogates pass through; there is never a Rust `char` conversion that
//! would reject them), and the result is read via `narwhal_editor_read`
//! into a buffer sized by `State.length`, hashed as raw u32 little-endian
//! bytes, then the handle is closed (RAII on drop).
//!
//! Hard guards (not merely printed): exactly 136 chunks per version, 272
//! digests total, the full 0x110000 (1,114,112) code point domain at the
//! frozen 8192 chunk stride, the exact original capture commit AND the exact
//! extraction revision, a direct parse rejection of a jointly truncated
//! corpus (declared count and data shortened together, so no hash guard
//! fires), and the fixture, golden-Python and semantic-payload SHA-256
//! cross-checks.
//!
//! Provenance: see `data/unicode28_golden.provenance.json` (original capture
//! commit, extraction revision, golden-Python SHA, semantic payload SHA,
//! text fixture SHA/length, mutation evidence).

#[path = "support/golden27_sha256.rs"]
mod sha256;

use narwhal_ui_core::{
    narwhal_editor_abi, narwhal_editor_close, narwhal_editor_edit, narwhal_editor_read, State,
};

const FIXTURE: &str = include_str!("data/unicode28_golden.txt");
const PROVENANCE: &str = include_str!("data/unicode28_golden.provenance.json");

// Hard corpus invariants (review-required; a truncated corpus must not pass).
const EXPECTED_VERSIONS: [&str; 2] = ["15.1.0", "16.0.0"];
const EXPECTED_CHUNKS_PER_VERSION: usize = 136;
const EXPECTED_TOTAL_DIGESTS: usize = 272;
const EXPECTED_CHUNK_CODEPOINTS: usize = 8192;
const EXPECTED_TOTAL_CODEPOINTS: usize = 0x11_0000; // 1,114,112
                                                    // The ORIGINAL capture commit recorded inside the frozen data
                                                    // (`DATA["source_commit"]`) -- where these digests were generated.
const EXPECTED_SOURCE_COMMIT: &str = "50267dd6d4504b14052d1ab7e54675f3c8750637";
// The public revision from which the frozen data was EXTRACTED at this lease.
// These two differ: pinning only one of them would mislabel provenance.
const EXPECTED_EXTRACTION_REVISION: &str = "0ecb9c8330ee8a4c3c7a5db76f8e54679c28c231";
const EXPECTED_GOLDEN_PYTHON_SHA256: &str =
    "60fd8ae173d0af62c8ec45d8ccc147004fb70f4e1d080c176e143e3277bdd0de";
const EXPECTED_SEMANTIC_PAYLOAD_SHA256: &str =
    "a8947f9fc8c2fa12a7d523a853b5386f0c16aea0ce82715b6bb2da3a93b0009b";
const EXPECTED_FIXTURE_SHA256: &str =
    "9a7a8a9031ab3f2f0441adbc26fe0c46c84c31a1c8714babcb0ca4b82934fbee";

/// The `TextField(text, True)` construct via public FFI: op 0, flags 1
/// (multiline) plus 8 for the Unicode-16.0 classifier. The text is passed as
/// raw `u32` code points (surrogates included); no Rust `char` conversion.
struct Field {
    handle: u64,
    length: usize,
    /// Revision the constructor returned; reads must follow it, not assume
    /// an incidental constant.
    text_revision: u64,
}

impl Drop for Field {
    fn drop(&mut self) {
        let _ = narwhal_editor_close(self.handle); // RAII: the handle is always released
    }
}

impl Field {
    fn new(unicode16: bool, codepoints: &[u32]) -> Self {
        assert_eq!(narwhal_editor_abi(), 1, "native editor ABI mismatch");
        let flags = 1u32 | (unicode16 as u32) << 3;
        let mut out = State::default();
        let rc = unsafe {
            narwhal_editor_edit(
                0, // handle 0: construct a new field
                0, // op 0 = construct
                0, // value: unused by op 0
                flags,
                codepoints.as_ptr(),
                codepoints.len(),
                &mut out,
                std::mem::size_of::<State>(),
            )
        };
        assert_eq!(rc, 0, "editor construct failed with rc {rc}");
        assert_ne!(out.handle, 0, "construct returned no handle");
        Field {
            handle: out.handle,
            length: usize::try_from(out.length).expect("length exceeds the host usize domain"),
            text_revision: out.text_revision,
        }
    }

    /// The full resulting text as raw u32 little-endian bytes (the exact
    /// `str.encode("utf-32-le", "surrogatepass")` bytes the golden digests were
    /// captured from), read via `narwhal_editor_read`.
    fn raw_utf32le(&self) -> Vec<u8> {
        if self.length == 0 {
            return Vec::new();
        }
        let mut buf = vec![0u32; self.length];
        let rc = unsafe {
            narwhal_editor_read(
                self.handle,
                self.text_revision,
                0,
                buf.as_mut_ptr(),
                self.length,
            )
        };
        assert_eq!(rc, 0, "read editor text failed with rc {rc}");
        let mut out = Vec::with_capacity(self.length * 4);
        for c in &buf {
            out.extend_from_slice(&c.to_le_bytes());
        }
        out
    }
}

/// Parse the text fixture: `unicode28` header, then per version a
/// `version count` line followed by exactly `count` 64-hex digests, in the
/// frozen chunk order.
fn parse_fixture(text: &str) -> ([String; 2], Vec<String>, Vec<String>) {
    let mut lines = text.lines().map(str::to_owned);
    let header = lines
        .next()
        .unwrap_or_else(|| panic!("missing fixture header"));
    assert_eq!(header.as_str(), "unicode28", "fixture header");
    let mut versions: [String; 2] = [String::new(), String::new()];
    let mut digests: [Vec<String>; 2] = [Vec::new(), Vec::new()];
    for (i, version) in EXPECTED_VERSIONS.iter().enumerate() {
        let meta = lines
            .next()
            .unwrap_or_else(|| panic!("missing version meta line {i}"));
        let Some((name, count)) = meta.split_once(" ") else {
            panic!("malformed meta line: {meta:?}");
        };
        assert_eq!(name, *version, "version order mismatch at line {i}: {name}");
        let count: usize = count
            .parse()
            .unwrap_or_else(|_| panic!("meta count is not a number: {count:?}"));
        assert_eq!(
            count, EXPECTED_CHUNKS_PER_VERSION,
            "declared chunk count {count}"
        );
        for j in 0..count {
            let d = lines
                .next()
                .unwrap_or_else(|| panic!("missing digest {i}.{j}"));
            assert!(
                d.len() == 64 && d.bytes().all(|b| b.is_ascii_hexdigit()),
                "digest {i}.{j} is not 64 hex chars: {d:?}"
            );
            digests[i].push(d);
        }
        versions[i] = name.to_owned();
    }
    assert!(lines.next().is_none(), "unexpected trailing fixture lines");
    (
        versions,
        std::mem::take(&mut digests[0]),
        std::mem::take(&mut digests[1]),
    )
}

/// Re-encode the parsed digests as the canonical frozen payload
/// (`json.dumps(DATA["unicode"], ensure_ascii=False, sort_keys=True)`) and
/// cross-check its SHA-256 against the provenance record.
fn semantic_sha256(versions: &[String], digests: &[Vec<String>]) -> String {
    let mut s = String::from("{");
    for (i, version) in versions.iter().enumerate() {
        if i > 0 {
            s.push_str(", ");
        }
        s.push('"');
        s.push_str(version);
        s.push_str("\": [");
        for (j, d) in digests[i].iter().enumerate() {
            if j > 0 {
                s.push_str(", ");
            }
            s.push('"');
            s.push_str(d);
            s.push('"');
        }
        s.push(']');
    }
    s.push('}');
    sha256::hash(s.as_bytes())
}

/// Replay one frozen chunk through the public FFI and compare its digest.
fn replay_chunk(unicode16: bool, chunk: usize, expected: &str) {
    let start = chunk * EXPECTED_CHUNK_CODEPOINTS;
    let end = (start + EXPECTED_CHUNK_CODEPOINTS).min(EXPECTED_TOTAL_CODEPOINTS);
    assert!(end > start, "empty chunk {chunk}");
    // Exactly `map(chr, range(start, end))`: raw code points, surrogates included.
    let cps: Vec<u32> = (start..end).map(|cp| cp as u32).collect();
    assert_eq!(cps.len(), end - start);
    let f = Field::new(unicode16, &cps); // TextField(cps, True)
    let digest = sha256::hash(&f.raw_utf32le());
    assert_eq!(
        digest, expected,
        "chunk {chunk} (U+{start:04X}..U+{end:04X}) digest mismatch: actual {digest}"
    );
    drop(f); // explicit close; also RAII-covered
}

/// All 272 chunk digests, both versions, must replay to the frozen values.
fn replay_all() -> ([String; 2], Vec<String>, Vec<String>) {
    let (versions, d15, d16) = parse_fixture(FIXTURE);
    assert_eq!(
        versions,
        [
            EXPECTED_VERSIONS[0].to_owned(),
            EXPECTED_VERSIONS[1].to_owned()
        ]
    );
    assert_eq!(
        d15.len() + d16.len(),
        EXPECTED_TOTAL_DIGESTS,
        "expected exactly {EXPECTED_TOTAL_DIGESTS} digests, found {}",
        d15.len() + d16.len()
    );
    for (i, digests) in [d15.as_slice(), d16.as_slice()].iter().enumerate() {
        assert_eq!(
            digests.len(),
            EXPECTED_CHUNKS_PER_VERSION,
            "version {} has {} chunks, expected exactly {EXPECTED_CHUNKS_PER_VERSION}",
            versions[i],
            digests.len()
        );
    }
    // 136 chunks of 8192 code points cover the full 1,114,112 code point domain.
    assert_eq!(
        EXPECTED_CHUNKS_PER_VERSION * EXPECTED_CHUNK_CODEPOINTS,
        EXPECTED_TOTAL_CODEPOINTS,
        "chunk grid does not cover the full code point domain"
    );
    for (chunk, expected) in d15.iter().enumerate() {
        replay_chunk(false, chunk, expected);
    }
    for (chunk, expected) in d16.iter().enumerate() {
        replay_chunk(true, chunk, expected);
    }
    (versions, d15, d16)
}

#[test]
fn replay_all_272_unicode_classifier_digests() {
    let (versions, d15, d16) = replay_all();
    let differing = (0..EXPECTED_CHUNKS_PER_VERSION)
        .filter(|&i| d15[i] != d16[i])
        .count();
    assert!(
        differing > 0,
        "both versions replayed identical digests; the Unicode16 flag would be untested"
    );
    eprintln!("unicode28: replayed 272 chunk digests across versions {versions:?}; {differing} of 136 chunks differ between 15.1.0 and 16.0.0")
}

#[test]
fn fixture_and_provenance_cross_check() {
    assert_eq!(
        sha256::hash(FIXTURE.as_bytes()),
        EXPECTED_FIXTURE_SHA256,
        "embedded fixture hash does not match the recorded provenance"
    );
    let (versions, d15, d16) = parse_fixture(FIXTURE);
    assert_eq!(
        semantic_sha256(&versions, &[d15, d16]),
        EXPECTED_SEMANTIC_PAYLOAD_SHA256,
        "re-encoded semantic payload hash does not match the recorded provenance"
    );
    let p = PROVENANCE;
    for required in [
        EXPECTED_SOURCE_COMMIT,
        EXPECTED_EXTRACTION_REVISION,
        EXPECTED_GOLDEN_PYTHON_SHA256,
        EXPECTED_SEMANTIC_PAYLOAD_SHA256,
        EXPECTED_FIXTURE_SHA256,
        "136",
        "272",
        "8192",
        "1114112",
        "15.1.0",
        "16.0.0",
    ] {
        assert!(
            p.contains(required),
            "provenance file missing expected value: {required}"
        );
    }
}

/// Guard: a corpus whose declared per-version count AND its digest data are
/// truncated *together* (136 -> 128, both versions) is internally consistent
/// and no SHA cross-check can fire, so the absolute 136/272 pin must be
/// proven by the direct parse itself. The parser must reject it.
#[test]
fn parse_guard_rejects_jointly_truncated_corpus() {
    let lines: Vec<&str> = FIXTURE.lines().collect();
    assert_eq!(
        lines.len(),
        1 + 2 * (1 + EXPECTED_CHUNKS_PER_VERSION),
        "fixture shape precondition"
    );
    let keep = EXPECTED_CHUNKS_PER_VERSION - 8; // 128, jointly in both meta and data
    let mut corpus = String::new();
    corpus.push_str("unicode28\n");
    for (i, name) in EXPECTED_VERSIONS.iter().enumerate() {
        corpus.push_str(name);
        corpus.push(' ');
        corpus.push_str(&keep.to_string());
        corpus.push('\n');
        let first = 2 + i * (1 + EXPECTED_CHUNKS_PER_VERSION);
        for d in &lines[first..first + keep] {
            corpus.push_str(d);
            corpus.push('\n');
        }
    }
    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| parse_fixture(&corpus)));
    match r {
        Ok((versions, a, b)) => panic!(
            "parse guard failed to reject jointly truncated corpus: versions={versions:?} digests={}+{}",
            a.len(),
            b.len()
        ),
        Err(e) => {
            let s = e
                .downcast_ref::<String>()
                .map(|s| s.as_str())
                .or_else(|| e.downcast_ref::<&str>().copied())
                .unwrap_or("<non-string panic>");
            assert!(
                s.contains("declared chunk count 128"),
                "truncation guard fired on the wrong assertion: {s}"
            );
            eprintln!("unicode28: joint-truncation guard correctly rejected: {s}");
        }
    }
}
