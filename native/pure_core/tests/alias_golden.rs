//! RUST-ALIAS-17: golden replay of the retired Python reference grammar.
//!
//! This integration test replays every recorded alias case from
//! `tests/fixtures/alias_golden.txt` against the REAL C ABI
//! (`narwhal_pure_core_alias_parse`) and asserts the full reply
//! (prefix span, command span, and every option span) matches the expected
//! value. The fixture was produced ONCE from the original oracle
//! (`spike/alias_reference.py`, the live Python regex grammar) and is the sole
//! source of expectations — no Python engine remains at test time.
//!
//! Guardrails (each exists because a prior defect slipped through):
//!   * HARD COUNT   — the fixture must contain exactly 5364 records and the
//!     header `count` must equal the actual data-line count.
//!   * PROVENANCE   — the header must name the original oracle
//!     (`spike/alias_reference.py`), so the fixture is traceable
//!     to the retired grammar, not to the native code.
//!   * CHECKSUM     — an FNV-1a 64 over the data section must match the header
//!     checksum, so a truncated/edited fixture fails loudly.
//!   * NO FALLBACK  — every case is asserted against the ABI; a mismatch is a
//!     test failure, never a silent replay.
//!
//! The existing unit tests in `src/alias_owned.rs` (fault/bound/metadata
//! admission) are untouched and remain the guard for the reply-metadata
//! contract.

use std::env;
use std::path::Path;

use narwhal_pure_core::alias_owned::{narwhal_pure_core_alias_parse, Reply};

const EXPECTED_COUNT: u32 = 5364;
const EXPECTED_PROVENANCE: &str = "spike/alias_reference.py";
const UNICODE_151: u32 = 151;
const CODE_OK: u32 = 0;
const CODE_INVALID: u32 = 1;

/// FNV-1a 64, byte-identical to the generator's implementation.
fn fnv1a64(data: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in data {
        h ^= *b as u64;
        h = h.wrapping_mul(0x00000100000001b3);
    }
    h
}

/// Decode a dot-separated hex codepoint field (e.g. `5a.6f.6f.6d`) to a
/// `Vec<u32>`. An empty field decodes to an empty slice.
fn decode_hex_dots(field: &str) -> Vec<u32> {
    if field.is_empty() {
        return Vec::new();
    }
    field
        .split('.')
        .map(|part| {
            u32::from_str_radix(part, 16)
                .unwrap_or_else(|e| panic!("alias_golden: bad hex codepoint `{part}`: {e}"))
        })
        .collect()
}

/// Encode a `Vec<u32>` back to dot-separated lowercase hex (for messages).
fn encode_hex_dots(cps: &[u32]) -> String {
    cps.iter()
        .map(|c| format!("{c:x}"))
        .collect::<Vec<_>>()
        .join(".")
}

struct Expected {
    prefix: Option<Vec<u32>>,
    command: Option<Vec<u32>>,
    options: Vec<Vec<u32>>,
}

#[test]
fn alias_golden_replays_the_original_reference_grammar() {
    // ---- Locate the fixture relative to the package root (CWD-independent). ----
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR set by cargo");
    let fixture_path = Path::new(&manifest_dir).join("tests/fixtures/alias_golden.txt");
    let text = std::fs::read_to_string(&fixture_path)
        .unwrap_or_else(|e| panic!("alias_golden: cannot read {fixture_path:?}: {e}"));
    let lines: Vec<&str> = text.lines().collect();

    // ---- Header guardrails: count, provenance, checksum. ----
    let header_count = lines
        .iter()
        .find_map(|l| {
            l.strip_prefix("# count=")
                .map(|v| v.parse::<u32>().unwrap())
        })
        .expect("alias_golden: missing `# count=` header");
    let provenance = lines
        .iter()
        .find_map(|l| l.strip_prefix("# provenance=").map(|s| s.to_string()))
        .expect("alias_golden: missing `# provenance=` header");
    let checksum = lines
        .iter()
        .find_map(|l| l.strip_prefix("# checksum=").map(|s| s.to_string()))
        .expect("alias_golden: missing `# checksum=` header");

    // HARD COUNT: the fixture is a fixed, lossless capture of exactly 5364 cases.
    assert_eq!(
        header_count, EXPECTED_COUNT,
        "alias_golden: header count {header_count} != expected {EXPECTED_COUNT}"
    );
    // PROVENANCE: traceable to the original oracle, not to native code.
    assert_eq!(
        provenance.as_str(),
        EXPECTED_PROVENANCE,
        "alias_golden: provenance {provenance} != {EXPECTED_PROVENANCE}"
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
        "alias_golden: {data_count} data lines != header count {header_count}"
    );
    let data_section = data_lines.join("\n") + "\n";
    let actual_checksum = format!("{:016x}", fnv1a64(data_section.as_bytes()));
    assert_eq!(
        actual_checksum, checksum,
        "alias_golden: fixture checksum {actual_checksum} != header {checksum} (fixture tampered)"
    );

    // ---- Replay every case against the real ABI. ----
    let mut ok_cases = 0usize;
    let mut invalid_cases = 0usize;
    for (idx, line) in data_lines.iter().enumerate() {
        let fields: Vec<&str> = line.split('\t').collect();
        let field_count = fields.len();
        assert!(
            field_count >= 4,
            "alias_golden line {idx}: expected >=4 fields, got {field_count}: {line:?}"
        );
        let input = decode_hex_dots(fields[0]);
        let expected = Expected {
            prefix: if fields[1] == "NONE" {
                None
            } else {
                Some(decode_hex_dots(fields[1]))
            },
            command: if fields[2] == "NONE" {
                None
            } else {
                Some(decode_hex_dots(fields[2]))
            },
            options: fields[4..].iter().map(|f| decode_hex_dots(f)).collect(),
        };
        let expected_option_count = expected.options.len() as u32;
        let n = input.len();
        let n32 = n as u32;

        let mut reply = Reply::default();
        // Allocate n option-span pairs (2n u32s), the safe upper bound: each
        // option is at least one codepoint, so there can be at most n options.
        let mut opts = vec![0u32; n * 2];
        let rc = unsafe {
            narwhal_pure_core_alias_parse(
                input.as_ptr(),
                n,
                UNICODE_151,
                &mut reply as *mut Reply,
                std::mem::size_of::<Reply>(),
                opts.as_mut_ptr(),
                opts.len(),
            )
        };
        assert_eq!(
            rc, 0,
            "alias_golden line {idx}: ABI rc {rc} (input {line:?})"
        );
        assert_eq!(
            reply.reserved, 0,
            "alias_golden line {idx}: reserved nonzero"
        );

        // The option count in the reply must equal the oracle's option count.
        assert_eq!(
            reply.option_count, expected_option_count,
            "alias_golden line {idx}: option_count {} != expected {} (input {line:?})",
            reply.option_count, expected_option_count
        );

        match (reply.code, expected.command.as_ref()) {
            // ---- Valid: full span equality (prefix, command, every option). ----
            (CODE_OK, Some(exp_command)) => {
                ok_cases += 1;
                // Prefix span.
                let (ps, pe) = (reply.prefix_start, reply.prefix_end);
                let got_prefix = if ps == pe {
                    None
                } else {
                    Some(&input[ps as usize..pe as usize])
                };
                let exp_prefix = expected.prefix.as_deref();
                assert_eq!(
                    got_prefix, exp_prefix,
                    "alias_golden line {idx}: prefix {got_prefix:?} != expected {exp_prefix:?} (input {line:?})"
                );
                // Command span.
                let (cs, ce) = (reply.command_start, reply.command_end);
                assert!(
                    cs < ce && cs <= n32 && ce <= n32,
                    "alias_golden line {idx}: command span ({cs},{ce}) out of range (input {line:?})"
                );
                assert_eq!(
                    &input[cs as usize..ce as usize],
                    exp_command.as_slice(),
                    "alias_golden line {idx}: command {} != expected {} (input {line:?})",
                    encode_hex_dots(&input[cs as usize..ce as usize]),
                    encode_hex_dots(exp_command)
                );
                // Command must begin at or after the prefix end.
                assert!(
                    cs >= pe,
                    "alias_golden line {idx}: command start {cs} < prefix end {pe} (input {line:?})"
                );
                // Every option span, in order, non-overlapping, after the command.
                let mut prev_end = ce;
                for (oi, exp_opt) in expected.options.iter().enumerate() {
                    let s = opts[oi * 2];
                    let e = opts[oi * 2 + 1];
                    assert!(
                        s >= prev_end && s <= e && e <= n32,
                        "alias_golden line {idx}: option {oi} span ({s},{e}) invalid (input {line:?})"
                    );
                    assert_eq!(
                        &input[s as usize..e as usize],
                        exp_opt.as_slice(),
                        "alias_golden line {idx}: option {oi} {} != expected {} (input {line:?})",
                        encode_hex_dots(&input[s as usize..e as usize]),
                        encode_hex_dots(exp_opt)
                    );
                    prev_end = e;
                }
            }
            // ---- Invalid: the ABI must report no grammar match, no metadata. ----
            (CODE_INVALID, None) => {
                invalid_cases += 1;
                assert_eq!(
                    reply.prefix_start, reply.prefix_end,
                    "alias_golden line {idx}: invalid reply carries prefix span (input {line:?})"
                );
                assert_eq!(
                    reply.command_start, reply.command_end,
                    "alias_golden line {idx}: invalid reply carries command span (input {line:?})"
                );
                assert_eq!(
                    reply.option_count, 0,
                    "alias_golden line {idx}: invalid reply carries options (input {line:?})"
                );
            }
            (code, command) => {
                panic!(
                    "alias_golden line {idx}: code {code} with command {command:?} is \
                     internally inconsistent (input {line:?})"
                );
            }
        }
    }

    // The capture is a mix of valid and invalid grammar inputs; both classes
    // must be present or the fixture has silently lost coverage.
    assert!(ok_cases > 0, "alias_golden: no valid cases replayed");
    assert!(invalid_cases > 0, "alias_golden: no invalid cases replayed");
    assert_eq!(
        ok_cases + invalid_cases,
        data_lines.len(),
        "alias_golden: case accounting mismatch"
    );
}
