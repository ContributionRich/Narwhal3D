//! RUST-REBUILD-GOLDEN-18: golden replay of the retired Python rebuild-fit reference.
//!
//! This integration test replays every recorded fit decision from
//! `tests/fixtures/rebuild_golden.txt` against the REAL C ABI
//! (`narwhal_pure_core_rebuild_fit`) and asserts the exact control points,
//! knots and cv-count (bit for bit) match the expectation captured ONCE from
//! the original independent oracle (`spike/rebuild_reference.py`, the retired
//! Python least-squares fit). No Python engine remains at test time.
//!
//! What is pinned (all of it sourced from the old Python reference, never
//! re-derived here):
//!   * the bit-exact control points of every open, closed and tangent fit
//!   * the bit-exact knot vector of each fit
//!   * the cv-count each fit reports
//!   * the singular refusal (status `-4`), the degree-above-count refusal
//!     (`-2`) and the past-the-unknown-bound refusal (`-5`)
//!
//! The fixture's expected control points/knots come ONLY from the old Python
//! reference. The three rejection rows are the native ABI's documented
//! contract (the reference does not model the bound checks), recorded as fixed
//! status codes.
//!
//! Guardrails (each exists because a prior defect slipped through):
//!   * HARD COUNT   -- the fixture must contain exactly 222 records and the
//!     header `count` must equal the actual data-line count.
//!   * PROVENANCE   -- the header must name the original oracle
//!     (`spike/rebuild_reference.py`), so the fixture is traceable to the
//!     retired engine, not to the native code.
//!   * CHECKSUM     -- an FNV-1a 64 over the data section must match the header
//!     checksum, so a truncated/edited fixture fails loudly.
//!   * NO FALLBACK  -- every row is asserted against the ABI; a mismatch is a
//!     test failure, never a silent replay.

use std::env;
use std::path::Path;

use narwhal_pure_core::fit::narwhal_pure_core_rebuild_fit;

const EXPECTED_COUNT: u32 = 222;
const EXPECTED_PROVENANCE: &str = "spike/rebuild_reference.py";

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
/// The fixture stores the little-endian byte hex
/// (`struct.pack('<d', x).hex()`), i.e. the byte order in memory.
/// `f64::to_bits()` returns the native bit pattern as a u64; reconstruct that
/// u64 with `from_le_bytes` over the 8 bytes so the comparison is byte-exact
/// and endianness-independent (this is what pins signed zeros).
fn bits_of(field: &str, idx: usize) -> u64 {
    assert_eq!(
        field.len(),
        16,
        "rebuild_golden line {idx}: bits field `{field}` not 16 hex digits"
    );
    let mut bytes = [0u8; 8];
    for i in 0..8 {
        bytes[i] = u8::from_str_radix(&field[i * 2..i * 2 + 2], 16)
            .unwrap_or_else(|e| panic!("rebuild_golden line {idx}: bad bits `{field}`: {e}"));
    }
    u64::from_le_bytes(bytes)
}

/// Decode a comma-separated f64 field to its exact bit patterns (empty field -> none).
fn decode_bits(field: &str, idx: usize) -> Vec<u64> {
    if field.is_empty() {
        Vec::new()
    } else {
        field.split(',').map(|s| bits_of(s, idx)).collect()
    }
}

#[test]
fn rebuild_golden_replays_the_original_reference_engine() {
    // ---- Locate the fixture relative to the package root (CWD-independent). ----
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR set by cargo");
    let fixture_path = Path::new(&manifest_dir).join("tests/fixtures/rebuild_golden.txt");
    let text = std::fs::read_to_string(&fixture_path)
        .unwrap_or_else(|e| panic!("rebuild_golden: cannot read {fixture_path:?}: {e}"));
    let lines: Vec<&str> = text.lines().collect();

    // ---- Header guardrails: count, provenance, checksum. ----
    let header_count = lines
        .iter()
        .find_map(|l| {
            l.strip_prefix("# count=")
                .map(|v| v.parse::<u32>().unwrap())
        })
        .expect("rebuild_golden: missing `# count=` header");
    let provenance = lines
        .iter()
        .find_map(|l| l.strip_prefix("# provenance=").map(|s| s.to_string()))
        .expect("rebuild_golden: missing `# provenance=` header");
    let checksum = lines
        .iter()
        .find_map(|l| l.strip_prefix("# checksum=").map(|s| s.to_string()))
        .expect("rebuild_golden: missing `# checksum=` header");

    // HARD COUNT: the fixture is a fixed, lossless capture of exactly 222 cases
    // (221 original parity/random/singular/reject rows + 1 pivot-tie witness row).
    assert_eq!(
        header_count, EXPECTED_COUNT,
        "rebuild_golden: header count {header_count} != expected {EXPECTED_COUNT}"
    );
    // PROVENANCE: traceable to the original oracle, not to native code.
    assert_eq!(
        provenance.as_str(),
        EXPECTED_PROVENANCE,
        "rebuild_golden: provenance {provenance} != {EXPECTED_PROVENANCE}"
    );

    // ---- Split header vs data; re-derive the checksum over the data section. ----
    let data_lines: Vec<&str> = lines
        .iter()
        .filter(|l| !l.starts_with('#'))
        .copied()
        .collect();
    assert_eq!(
        data_lines.len() as u32,
        header_count,
        "rebuild_golden: {} data lines != header count {header_count}",
        data_lines.len()
    );
    let data_section = data_lines.join("\n") + "\n";
    let actual_checksum = format!("{:016x}", fnv1a64(data_section.as_bytes()));
    assert_eq!(
        actual_checksum, checksum,
        "rebuild_golden: fixture checksum {actual_checksum} != header {checksum} (fixture tampered)"
    );

    // ---- Replay every row against the real ABI. ----
    let mut ok_open = 0usize;
    let mut ok_closed = 0usize;
    let mut tangent_fits = 0usize;
    let mut singular = 0usize;
    let mut refused_input = 0usize;
    let mut refused_too_large = 0usize;
    let mut ctrl_bits = 0usize;
    let mut knot_bits = 0usize;

    for (idx, line) in data_lines.iter().enumerate() {
        let f: Vec<&str> = line.split('\t').collect();
        assert_eq!(
            f.len(),
            12,
            "rebuild_golden line {idx}: expected 12 fields, got {}: {line:?}",
            f.len()
        );
        let group = f[0];
        let n: usize = f[1]
            .parse()
            .unwrap_or_else(|e| panic!("rebuild_golden line {idx}: bad n `{}`: {e}", f[1]));
        let p: usize = f[2]
            .parse()
            .unwrap_or_else(|e| panic!("rebuild_golden line {idx}: bad p `{}`: {e}", f[2]));
        let closed: u8 = f[3].parse().unwrap();
        let tangent: u8 = f[4].parse().unwrap();
        let sample_count: usize = f[5].parse().unwrap();
        let points: Vec<f64> = decode_bits(f[6], idx)
            .into_iter()
            .map(f64::from_bits)
            .collect();
        let params: Vec<f64> = decode_bits(f[7], idx)
            .into_iter()
            .map(f64::from_bits)
            .collect();
        let ends: Vec<f64> = decode_bits(f[8], idx)
            .into_iter()
            .map(f64::from_bits)
            .collect();
        let status: i32 = f[9].parse().unwrap();
        let exp_ctrl = decode_bits(f[10], idx);
        let exp_knots = decode_bits(f[11], idx);

        // The input schema must match the row's own metadata.
        assert_eq!(
            points.len(), 3 * sample_count,
            "rebuild_golden line {idx}: points has {} values, expected {} (sample_count={sample_count})",
            points.len(), 3 * sample_count
        );
        assert_eq!(
            params.len(),
            sample_count,
            "rebuild_golden line {idx}: params has {} values, expected {sample_count}",
            params.len()
        );
        let end_count = if closed == 1 {
            0
        } else {
            6 + 6 * usize::from(tangent)
        };
        assert_eq!(
            ends.len(),
            end_count,
            "rebuild_golden line {idx}: ends has {} values, expected {end_count}",
            ends.len()
        );

        // The expected output shape follows the ABI contract for this (n, p, closed).
        let (ctrl_len, knot_len) = if closed == 1 {
            (n + p, n + 2 * p - 1)
        } else {
            (n, n + p - 1)
        };
        if status == 0 {
            assert_eq!(
                exp_ctrl.len(),
                3 * ctrl_len,
                "rebuild_golden line {idx}: ctrl has {} values, expected {}",
                exp_ctrl.len(),
                3 * ctrl_len
            );
            assert_eq!(
                exp_knots.len(),
                knot_len,
                "rebuild_golden line {idx}: knots has {} values, expected {knot_len}",
                exp_knots.len()
            );
        } else {
            assert!(
                exp_ctrl.is_empty() && exp_knots.is_empty(),
                "rebuild_golden line {idx}: a refusal must carry no expected output"
            );
        }

        // Out buffers sized at the ABI maximum for this (n, p); the ABI writes
        // fewer on success and none on a refusal.
        let mut out_ctrl = vec![0f64; 3 * (n + p)];
        let mut out_knots = vec![0f64; n + 2 * p];
        let mut cv_count: usize = usize::MAX;

        let rc = unsafe {
            narwhal_pure_core_rebuild_fit(
                points.as_ptr(),
                params.as_ptr(),
                sample_count,
                n,
                p,
                closed,
                tangent,
                ends.as_ptr(),
                out_ctrl.as_mut_ptr(),
                out_knots.as_mut_ptr(),
                &mut cv_count,
            )
        };
        assert_eq!(
            rc, status,
            "rebuild_golden line {idx} ({group}): rc {rc} != expected status {status}"
        );

        match status {
            0 => {
                assert_eq!(
                    cv_count, ctrl_len,
                    "rebuild_golden line {idx} ({group}): cv_count {cv_count} != {ctrl_len}"
                );
                let got_ctrl: Vec<u64> = out_ctrl
                    .iter()
                    .take(3 * ctrl_len)
                    .map(|v| v.to_bits())
                    .collect();
                assert_eq!(
                    got_ctrl, exp_ctrl,
                    "rebuild_golden line {idx} ({group}): control points differ from the reference"
                );
                let got_knots: Vec<u64> = out_knots
                    .iter()
                    .take(knot_len)
                    .map(|v| v.to_bits())
                    .collect();
                assert_eq!(
                    got_knots, exp_knots,
                    "rebuild_golden line {idx} ({group}): knots differ from the reference"
                );
                if closed == 1 {
                    ok_closed += 1;
                } else {
                    ok_open += 1;
                }
                if tangent == 1 {
                    tangent_fits += 1;
                }
                ctrl_bits += exp_ctrl.len();
                knot_bits += exp_knots.len();
            }
            -4 => {
                singular += 1;
                assert_eq!(
                    cv_count, 0,
                    "rebuild_golden line {idx} ({group}): singular must not report a cv-count"
                );
            }
            -2 => {
                refused_input += 1;
                assert_eq!(
                    cv_count, 0,
                    "rebuild_golden line {idx} ({group}): refused input must not report a cv-count"
                );
            }
            -5 => {
                refused_too_large += 1;
                assert_eq!(
                    cv_count, 0,
                    "rebuild_golden line {idx} ({group}): too large must not report a cv-count"
                );
            }
            _ => panic!("rebuild_golden line {idx} ({group}): unexpected status {status}"),
        }
    }

    // ---- Accounting: a real capture is a mix, and every class is exercised. ----
    assert!(ok_open > 0, "rebuild_golden: no open fit replayed");
    assert!(ok_closed > 0, "rebuild_golden: no closed fit replayed");
    assert!(
        tangent_fits > 0,
        "rebuild_golden: no tangent-constrained fit replayed"
    );
    assert!(singular > 0, "rebuild_golden: no singular refusal replayed");
    assert!(
        refused_input > 0,
        "rebuild_golden: no degree-above-count refusal replayed"
    );
    assert!(
        refused_too_large > 0,
        "rebuild_golden: no too-large refusal replayed"
    );
    assert!(
        ctrl_bits > 0 && knot_bits > 0,
        "rebuild_golden: no control/knot bits replayed"
    );
}
