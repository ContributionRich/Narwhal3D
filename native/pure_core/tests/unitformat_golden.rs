//! RUST-FORMAT-GOLDEN-18: golden replay of the retired Python unit-format reference.
//!
//! This integration test replays every recorded length-display case from
//! `tests/fixtures/unitformat_golden.txt` against the REAL C ABI
//! (`narwhal_pure_core_unitformat_format`) and asserts the rendered string (OK)
//! or the error code (ERR) matches the expectation captured ONCE from the
//! original oracle (`spike/unitformat_reference.py`, the retired Python decimal
//! engine). No Python engine remains at test time.
//!
//! What is pinned (all of it sourced from the old Python reference, never
//! re-derived here):
//!   * OK   — the exact rendered string the reference produced (signed zeros and
//!     trailing zeros are covered by the byte-exact string assertion).
//!   * ERR  — the native error code the reference's exception maps to:
//!     10 PRECISION_RANGE, 11 NAN, 12 INVALID_OP.
//!   * HOST — host-only (object admission, coercion order, unsupported-mode
//!     message, context guards). The ABI never sees these: the Rust test
//!     skips these rows and the Python host test asserts them.
//!
//! Guardrails (each exists because a prior defect slipped through):
//!   * HARD COUNT   — the fixture must contain exactly 43791 records and the
//!     header `count` must equal the actual data-line count.
//!   * PROVENANCE   — the header must name the original oracle
//!     (`spike/unitformat_reference.py`), so the fixture is traceable to the
//!     retired engine, not to the native code.
//!   * CHECKSUM     — an FNV-1a 64 over the data section must match the header
//!     checksum, so a truncated/edited fixture fails loudly.
//!   * NO FALLBACK  — every non-HOST case is asserted against the ABI; a
//!     mismatch is a test failure, never a silent replay.
//!
//! The existing unit tests in `src/unitformat.rs` (`abi_tests`) are untouched
//! and remain the guard for the reply-metadata / short-buffer contract.

use std::env;
use std::path::Path;

use narwhal_pure_core::unitformat::narwhal_pure_core_unitformat_format;

const EXPECTED_COUNT: u32 = 43791;
const EXPECTED_PROVENANCE: &str = "spike/unitformat_reference.py";
const UF_OK: i32 = 0;
const UF_PRECISION_RANGE: i32 = 10;
const UF_NAN: i32 = 11;
const UF_INVALID_OP: i32 = 12;
const UF_SHORT_BUFFER: i32 = 14;

/// FNV-1a 64, byte-identical to the generator's implementation.
fn fnv1a64(data: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in data {
        h ^= *b as u64;
        h = h.wrapping_mul(0x00000100000001b3);
    }
    h
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
        "unitformat_golden line {idx}: bits field `{field}` not 16 hex digits ({line:?})"
    );
    let mut bytes = [0u8; 8];
    for i in 0..8 {
        bytes[i] = u8::from_str_radix(&field[i * 2..i * 2 + 2], 16).unwrap_or_else(|e| {
            panic!("unitformat_golden line {idx}: bad bits `{field}`: {e} ({line:?})")
        });
    }
    u64::from_le_bytes(bytes)
}

#[test]
fn unitformat_golden_replays_the_original_reference_engine() {
    // ---- Locate the fixture relative to the package root (CWD-independent). ----
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR set by cargo");
    let fixture_path = Path::new(&manifest_dir).join("tests/fixtures/unitformat_golden.txt");
    let text = std::fs::read_to_string(&fixture_path)
        .unwrap_or_else(|e| panic!("unitformat_golden: cannot read {fixture_path:?}: {e}"));
    let lines: Vec<&str> = text.lines().collect();

    // ---- Header guardrails: count, provenance, checksum. ----
    let header_count = lines
        .iter()
        .find_map(|l| {
            l.strip_prefix("# count=")
                .map(|v| v.parse::<u32>().unwrap())
        })
        .expect("unitformat_golden: missing `# count=` header");
    let provenance = lines
        .iter()
        .find_map(|l| l.strip_prefix("# provenance=").map(|s| s.to_string()))
        .expect("unitformat_golden: missing `# provenance=` header");
    let checksum = lines
        .iter()
        .find_map(|l| l.strip_prefix("# checksum=").map(|s| s.to_string()))
        .expect("unitformat_golden: missing `# checksum=` header");

    // HARD COUNT: the fixture is a fixed, lossless capture of exactly 43791 cases.
    assert_eq!(
        header_count, EXPECTED_COUNT,
        "unitformat_golden: header count {header_count} != expected {EXPECTED_COUNT}"
    );
    // PROVENANCE: traceable to the original oracle, not to native code.
    assert_eq!(
        provenance.as_str(),
        EXPECTED_PROVENANCE,
        "unitformat_golden: provenance {provenance} != {EXPECTED_PROVENANCE}"
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
        "unitformat_golden: {data_count} data lines != header count {header_count}"
    );
    let data_section = data_lines.join("\n") + "\n";
    let actual_checksum = format!("{:016x}", fnv1a64(data_section.as_bytes()));
    assert_eq!(
        actual_checksum, checksum,
        "unitformat_golden: fixture checksum {actual_checksum} != header {checksum} (fixture tampered)"
    );

    // ---- Replay every case against the real ABI (mode/precision/ctx encoded per row). ----
    let mut ok_cases = 0usize;
    let mut err_cases = 0usize;
    let mut host_cases = 0usize;
    let mut err_by_code = [0usize; 16];
    for (idx, line) in data_lines.iter().enumerate() {
        let fields: Vec<&str> = line.split('\t').collect();
        assert_eq!(
            fields.len(),
            7,
            "unitformat_golden line {idx}: expected 7 fields, got {}: {line:?}",
            fields.len()
        );
        let cls = fields[0];
        // fields[1] value, fields[2] mode_code, fields[3] precision, fields[4] ctx,
        // fields[5] payload, fields[6] group.

        // HOST rows are host-only (the ABI never sees non-numeric value, a bad mode
        // name, a non-integer precision, or a custom decimal context).
        if cls == "HOST" {
            host_cases += 1;
            continue;
        }

        let value = f64::from_bits(bits_of(fields[1], idx, line));
        let mode_code: u32 = fields[2].parse().unwrap_or_else(|e| {
            panic!(
                "unitformat_golden line {idx}: bad mode_code `{}`: {e} ({line:?})",
                fields[2]
            )
        });
        let precision: i64 = fields[3].parse().unwrap_or_else(|e| {
            panic!(
                "unitformat_golden line {idx}: bad precision `{}`: {e} ({line:?})",
                fields[3]
            )
        });
        let ctx: u32 = fields[4].parse().unwrap_or_else(|e| {
            panic!(
                "unitformat_golden line {idx}: bad ctx `{}`: {e} ({line:?})",
                fields[4]
            )
        });
        let payload = fields[5];

        // A generous caller-owned buffer: the largest OK rendering under any context
        // in this fixture is bounded by the 28-digit coefficient (~31 bytes), so 4096
        // can never be short (a SHORT_BUFFER here would be a real bug, not a retry).
        let mut out = [0u8; 4096];
        let mut needed: usize = 0;
        let code = unsafe {
            narwhal_pure_core_unitformat_format(
                value,
                mode_code,
                precision,
                ctx,
                out.as_mut_ptr(),
                out.len(),
                &mut needed,
            )
        };
        assert_ne!(
            code, UF_SHORT_BUFFER,
            "unitformat_golden line {idx}: unexpected SHORT_BUFFER (line {line:?})"
        );

        match cls {
            // ---- Success: exact rendered string (signed zeros / trailing zeros pinned). ----
            "OK" => {
                ok_cases += 1;
                assert_eq!(
                    code, UF_OK,
                    "unitformat_golden line {idx}: code {code} != OK ({line:?})"
                );
                let rendered = String::from_utf8(out[..needed].to_vec())
                    .unwrap_or_else(|e| panic!("unitformat_golden line {idx}: non-UTF-8: {e}"));
                assert_eq!(
                    rendered, payload,
                    "unitformat_golden line {idx}: rendered {rendered:?} != expected {payload:?} ({line:?})"
                );
            }
            // ---- Computation error: the exact native code the reference maps to. ----
            "ERR" => {
                err_cases += 1;
                let expected_code: i32 = payload.parse().unwrap_or_else(|e| {
                    panic!("unitformat_golden line {idx}: bad ERR code `{payload}`: {e} ({line:?})")
                });
                assert!(
                    matches!(expected_code, UF_PRECISION_RANGE | UF_NAN | UF_INVALID_OP),
                    "unitformat_golden line {idx}: unexpected ERR code {expected_code} ({line:?})"
                );
                err_by_code[expected_code as usize] += 1;
                assert_eq!(
                    code, expected_code,
                    "unitformat_golden line {idx}: code {code} != expected {expected_code} ({line:?})"
                );
                // Error replies carry no payload (the FFI zeroes `needed` and writes nothing).
                assert_eq!(
                    needed, 0,
                    "unitformat_golden line {idx}: ERR reported needed={needed} != 0 ({line:?})"
                );
            }
            other => panic!("unitformat_golden line {idx}: unexpected class {other:?}"),
        }
    }

    // ---- Accounting: a real capture is a mix, and every row is accounted for. ----
    assert!(ok_cases > 0, "unitformat_golden: no OK cases replayed");
    assert!(err_cases > 0, "unitformat_golden: no ERR cases replayed");
    assert!(host_cases > 0, "unitformat_golden: no HOST rows present");
    assert_eq!(
        ok_cases + err_cases + host_cases,
        data_lines.len(),
        "unitformat_golden: accounting mismatch \
         ({ok_cases}+{err_cases}+{host_cases} != {})",
        data_lines.len()
    );
    // The three error classes must each be exercised (range, NaN, InvalidOperation).
    assert!(
        err_by_code[UF_PRECISION_RANGE as usize] > 0,
        "unitformat_golden: no PRECISION_RANGE cases replayed"
    );
    assert!(
        err_by_code[UF_NAN as usize] > 0,
        "unitformat_golden: no NAN cases replayed"
    );
    assert!(
        err_by_code[UF_INVALID_OP as usize] > 0,
        "unitformat_golden: no INVALID_OP cases replayed"
    );
}
