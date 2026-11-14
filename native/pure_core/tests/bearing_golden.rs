//! RUST-BEARING-GOLDEN-18: golden replay of the retired Python bearing reference.
//!
//! This integration test replays every recorded surveyor-bearing case from
//! `tests/fixtures/bearing_golden.txt` against the REAL C ABI
//! (`narwhal_pure_core_bearing_v2`) in both modes (0 `bearing_angle`,
//! 1 `parse_surveyor`) and asserts the full reply matches the expectation
//! captured ONCE from the original oracle (`spike/bearing_reference.py`, the
//! retired Python regex grammar). No Python engine remains at test time.
//!
//! What is pinned (all of it sourced from the old Python reference, never
//! re-derived here):
//!   * OK          — exact f64 BITS of the bearing angle (and, mode 1, the
//!     distance). Signed zeros are covered by the bits assertion.
//!   * BEARING / ANGLE / SURVEYOR — the classification code and the exact
//!     error SPAN (the substring the old reference embedded in its
//!     error string), asserted as `input[start..end]` equality.
//!   * QUADRANT    — the classification code and the offending-angle contract
//!     (`value > 90`). The old reference exposed only a lossy `%g`,
//!     so the exact bits are deliberately NOT pinned.
//!   * NONSTR      — host-only (the ABI never sees non-strings); the Rust test
//!     skips these rows and the Python host test asserts them.
//!
//! Guardrails (each exists because a prior defect slipped through):
//!   * HARD COUNT   — the fixture must contain exactly 28190 records and the
//!     header `count` must equal the actual data-line count.
//!   * PROVENANCE   — the header must name the original oracle
//!     (`spike/bearing_reference.py`), so the fixture is traceable to the
//!     retired grammar, not to the native code.
//!   * CHECKSUM     — an FNV-1a 64 over the data section must match the header
//!     checksum, so a truncated/edited fixture fails loudly.
//!   * NO FALLBACK  — every case is asserted against the ABI; a mismatch is a
//!     test failure, never a silent replay.
//!
//! The existing unit tests in `src/bearing_owned.rs` (`quadrant_and_classification`,
//! `abi`) are untouched and remain the guard for the reply-metadata contract.

use std::env;
use std::path::Path;

use narwhal_pure_core::bearing_owned::{
    narwhal_pure_core_bearing_v2, Reply, ANGLE, BEARING, QUADRANT, SURVEYOR,
};

const EXPECTED_COUNT: u32 = 28190;
const EXPECTED_PROVENANCE: &str = "spike/bearing_reference.py";
const UNICODE_151: u32 = 151;
const CODE_OK: u32 = 0;
/// A row with no value bits (QUADRANT / NONSTR) uses the all-zero sentinel in
/// the fixture; the OK/grammar rows use 16 lowercase hex digits.
const BITS_ZERO: &str = "0000000000000000";

/// FNV-1a 64, byte-identical to the generator's implementation.
fn fnv1a64(data: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in data {
        h ^= *b as u64;
        h = h.wrapping_mul(0x00000100000001b3);
    }
    h
}

/// Decode a dot-separated hex codepoint field (e.g. `4e.30.45`) to a `Vec<u32>`.
/// An empty field decodes to an empty slice (the empty input).
fn decode_hex_dots(field: &str) -> Vec<u32> {
    if field.is_empty() {
        return Vec::new();
    }
    field
        .split('.')
        .map(|part| {
            u32::from_str_radix(part, 16)
                .unwrap_or_else(|e| panic!("bearing_golden: bad hex codepoint `{part}`: {e}"))
        })
        .collect()
}

/// Decode a 16-hex-digit field to the exact f64 bit pattern.
///
/// The fixture stores the little-endian byte hex (`struct.pack('<d', x).hex()`),
/// i.e. the byte order in memory. `f64::to_bits()` returns the native bit
/// pattern as a u64. Reconstruct that u64 with `from_le_bytes` over the 8 bytes
/// so the comparison is byte-exact and endianness-independent.
fn bits_of(field: &str, idx: usize, line: &str) -> u64 {
    assert_eq!(
        field.len(),
        16,
        "bearing_golden line {idx}: bits field `{field}` not 16 hex digits ({line:?})"
    );
    let mut bytes = [0u8; 8];
    for i in 0..8 {
        bytes[i] = u8::from_str_radix(&field[i * 2..i * 2 + 2], 16).unwrap_or_else(|e| {
            panic!("bearing_golden line {idx}: bad bits `{field}`: {e} ({line:?})")
        });
    }
    u64::from_le_bytes(bytes)
}

#[test]
fn bearing_golden_replays_the_original_reference_grammar() {
    // ---- Locate the fixture relative to the package root (CWD-independent). ----
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR set by cargo");
    let fixture_path = Path::new(&manifest_dir).join("tests/fixtures/bearing_golden.txt");
    let text = std::fs::read_to_string(&fixture_path)
        .unwrap_or_else(|e| panic!("bearing_golden: cannot read {fixture_path:?}: {e}"));
    let lines: Vec<&str> = text.lines().collect();

    // ---- Header guardrails: count, provenance, checksum. ----
    let header_count = lines
        .iter()
        .find_map(|l| {
            l.strip_prefix("# count=")
                .map(|v| v.parse::<u32>().unwrap())
        })
        .expect("bearing_golden: missing `# count=` header");
    let provenance = lines
        .iter()
        .find_map(|l| l.strip_prefix("# provenance=").map(|s| s.to_string()))
        .expect("bearing_golden: missing `# provenance=` header");
    let checksum = lines
        .iter()
        .find_map(|l| l.strip_prefix("# checksum=").map(|s| s.to_string()))
        .expect("bearing_golden: missing `# checksum=` header");

    // HARD COUNT: the fixture is a fixed, lossless capture of exactly 28190 cases.
    assert_eq!(
        header_count, EXPECTED_COUNT,
        "bearing_golden: header count {header_count} != expected {EXPECTED_COUNT}"
    );
    // PROVENANCE: traceable to the original oracle, not to native code.
    assert_eq!(
        provenance.as_str(),
        EXPECTED_PROVENANCE,
        "bearing_golden: provenance {provenance} != {EXPECTED_PROVENANCE}"
    );

    // ---- Split header vs data; re-derive the checksum over the data section. ----
    let data_lines: Vec<&str> = lines
        .iter()
        .filter(|l| !l.starts_with('#'))
        .copied()
        .collect();
    let data_count = data_lines.len();
    assert_eq!(
        data_count as u32, header_count,
        "bearing_golden: {data_count} data lines != header count {header_count}"
    );
    let data_section = data_lines.join("\n") + "\n";
    let actual_checksum = format!("{:016x}", fnv1a64(data_section.as_bytes()));
    assert_eq!(
        actual_checksum, checksum,
        "bearing_golden: fixture checksum {actual_checksum} != header {checksum} (fixture tampered)"
    );

    // ---- Replay every case against the real ABI (mode is encoded per row). ----
    let mut ok_cases = 0usize;
    let mut error_cases = 0usize;
    let mut quadrant_cases = 0usize;
    let mut nonstr_cases = 0usize;
    for (idx, line) in data_lines.iter().enumerate() {
        let fields: Vec<&str> = line.split('\t').collect();
        assert_eq!(
            fields.len(),
            7,
            "bearing_golden line {idx}: expected 7 fields, got {}: {line:?}",
            fields.len()
        );
        let input_field = fields[0];
        let mode: u32 = fields[1]
            .parse()
            .unwrap_or_else(|e| panic!("bearing_golden line {idx}: bad mode `{}`: {e}", fields[1]));
        let cls = fields[2];
        let valuebits = fields[3];
        let distancebits = fields[4];
        let spanhex = fields[5];
        // fields[6] is the capture group (provenance); not asserted here.

        // NONSTR rows are host-only (the ABI never sees non-strings).
        if cls == "NONSTR" {
            nonstr_cases += 1;
            continue;
        }

        let input = decode_hex_dots(input_field);
        let n = input.len();

        let mut reply = Reply::default();
        let rc = unsafe {
            narwhal_pure_core_bearing_v2(
                input.as_ptr(),
                n,
                mode,
                UNICODE_151,
                &mut reply as *mut Reply,
                std::mem::size_of::<Reply>(),
            )
        };
        assert_eq!(
            rc, 0,
            "bearing_golden line {idx}: ABI rc {rc} (input {line:?})"
        );
        assert_eq!(
            reply.reserved, 0,
            "bearing_golden line {idx}: reserved nonzero (input {line:?})"
        );

        match cls {
            // ---- Success: exact f64 bits of angle (and distance for mode 1). ----
            "OK" => {
                ok_cases += 1;
                assert_eq!(
                    reply.code, CODE_OK,
                    "bearing_golden line {idx}: code {} != OK (input {line:?})",
                    reply.code
                );
                assert_eq!(
                    reply.value.to_bits(),
                    bits_of(valuebits, idx, line),
                    "bearing_golden line {idx}: value bits mismatch (input {line:?})"
                );
                assert_eq!(
                    reply.distance.to_bits(),
                    bits_of(distancebits, idx, line),
                    "bearing_golden line {idx}: distance bits mismatch (input {line:?})"
                );
                // A classified success carries no span (host contract: start==end==0).
                assert_eq!(
                    reply.start, 0,
                    "bearing_golden line {idx}: OK start nonzero"
                );
                assert_eq!(reply.end, 0, "bearing_golden line {idx}: OK end nonzero");
            }
            // ---- Grammar error: classification code + exact span content. ----
            "BEARING" => {
                error_cases += 1;
                error_class(
                    idx,
                    line,
                    mode,
                    &input,
                    &reply,
                    BEARING,
                    spanhex,
                    valuebits,
                    distancebits,
                );
            }
            "SURVEYOR" => {
                error_cases += 1;
                error_class(
                    idx,
                    line,
                    mode,
                    &input,
                    &reply,
                    SURVEYOR,
                    spanhex,
                    valuebits,
                    distancebits,
                );
            }
            "ANGLE" => {
                error_cases += 1;
                error_class(
                    idx,
                    line,
                    mode,
                    &input,
                    &reply,
                    ANGLE,
                    spanhex,
                    valuebits,
                    distancebits,
                );
            }
            // ---- Quadrant: classification + offending angle > 90 (bits unpinned). ----
            "QUADRANT" => {
                quadrant_cases += 1;
                assert_eq!(
                    reply.code, QUADRANT,
                    "bearing_golden line {idx}: code {} != QUADRANT (input {line:?})",
                    reply.code
                );
                assert!(
                    reply.value > 90.0,
                    "bearing_golden line {idx}: QUADRANT value {} not > 90 (input {line:?})",
                    reply.value
                );
                assert_eq!(
                    reply.distance, 0.0,
                    "bearing_golden line {idx}: QUADRANT distance nonzero (input {line:?})"
                );
            }
            other => panic!("bearing_golden line {idx}: unexpected class {other:?}"),
        }
    }

    // ---- Accounting: a real capture is a mix, and every row is accounted for. ----
    assert!(ok_cases > 0, "bearing_golden: no OK cases replayed");
    assert!(
        error_cases > 0,
        "bearing_golden: no grammar-error cases replayed"
    );
    assert!(
        quadrant_cases > 0,
        "bearing_golden: no QUADRANT cases replayed"
    );
    assert!(nonstr_cases > 0, "bearing_golden: no NONSTR rows present");
    assert_eq!(
        ok_cases + error_cases + quadrant_cases + nonstr_cases,
        data_lines.len(),
        "bearing_golden: accounting mismatch \
         ({ok_cases}+{error_cases}+{quadrant_cases}+{nonstr_cases} != {})",
        data_lines.len()
    );
}

/// Shared assertion for the three grammar-error classes (BEARING / SURVEYOR /
/// ANGLE): the classification code, a zeroed numeric payload, and the exact span
/// content. The caller owns the `error_cases` counter.
#[allow(clippy::too_many_arguments)]
fn error_class(
    idx: usize,
    line: &str,
    mode: u32,
    input: &[u32],
    reply: &Reply,
    expected_code: u32,
    spanhex: &str,
    valuebits: &str,
    distancebits: &str,
) {
    let n = input.len();
    assert_eq!(
        reply.code, expected_code,
        "bearing_golden line {idx}: code {} != {} (input {line:?})",
        reply.code, expected_code
    );
    // Grammar errors carry no numeric payload (value and distance are zero).
    assert_eq!(
        valuebits, BITS_ZERO,
        "bearing_golden line {idx}: error value bits not zero"
    );
    assert_eq!(
        distancebits, BITS_ZERO,
        "bearing_golden line {idx}: error distance bits not zero"
    );
    assert_eq!(
        reply.value, 0.0,
        "bearing_golden line {idx}: error value nonzero"
    );
    assert_eq!(
        reply.distance, 0.0,
        "bearing_golden line {idx}: error distance nonzero"
    );
    // SURVEYOR is only reachable in mode 1; BEARING/ANGLE appear in both.
    if expected_code == SURVEYOR {
        assert_eq!(mode, 1, "bearing_golden line {idx}: SURVEYOR in mode 0");
    }
    // The span content must equal the substring the old reference embedded in
    // its error string (asserted as input[start..end], not a precomputed offset).
    let span = decode_hex_dots(spanhex);
    let s = reply.start as usize;
    let e = reply.end as usize;
    assert!(
        s <= e && e <= n,
        "bearing_golden line {idx}: span ({s},{e}) out of bounds for len {n} (input {line:?})"
    );
    assert_eq!(
        &input[s..e],
        span.as_slice(),
        "bearing_golden line {idx}: span content input[{s}..{e}] {:?} != expected {:?} (input {line:?})",
        input[s..e].iter().map(|c| format!("{c:x}")).collect::<Vec<_>>().join("."),
        span.iter().map(|c| format!("{c:x}")).collect::<Vec<_>>().join("."),
    );
}
