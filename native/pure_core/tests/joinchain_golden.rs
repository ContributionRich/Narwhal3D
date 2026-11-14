//! RUST-JOIN-GOLDEN-18: golden replay of the retired Python join-chain reference.
//!
//! This integration test replays every recorded Join decision from
//! `tests/fixtures/joinchain_golden.txt` against the REAL C ABI
//! (`narwhal_pure_core_joinchain`) and asserts the chain layout (offsets,
//! lengths, closed flags, head/tail ends), the member layout (segment index +
//! reversal flag), and the `n_chains` / `n_members` counts match the
//! expectation captured ONCE from the original independent oracle
//! (`spike/joinchain_reference.py`, the retired Python Theta(N^2) solver).
//! No Python engine remains at test time.
//!
//! What is pinned (all of it sourced from the old Python reference, never
//! re-derived here):
//!   * the exact chain offsets / lengths / closed flags / head & tail ends
//!   * the exact member order (segment index + reverse flag)
//!   * the exact n_chains / n_members counts
//!   * the empty (n=0) route, which succeeds with zero counts
//!
//! Keys are SEGMENT INDICES: the native ABI reports members as
//! `(segment_index, reverse)` and never sees the caller's labels. The
//! arbitrary-LABEL mapping (index -> host key) is a pure-Python host concern,
//! verified separately by `spike/test_joinchain_owned.py`; it is NOT part of
//! this fixture.
//!
//! Guardrails (each exists because a prior defect slipped through):
//!   * HARD COUNT   -- the fixture must contain exactly 9429 records and the
//!     header `count` must equal the actual data-line count.
//!   * PROVENANCE   -- the header must name the original oracle
//!     (`spike/joinchain_reference.py`), so the fixture is traceable to the
//!     retired engine, not to the native code.
//!   * CHECKSUM     -- an FNV-1a 64 over the data section must match the header
//!     checksum, so a truncated/edited fixture fails loudly.
//!   * NO FALLBACK  -- every row is asserted against the ABI; a mismatch is a
//!     test failure, never a silent replay.

use std::env;
use std::path::Path;

use narwhal_pure_core::joinchain::narwhal_pure_core_joinchain;

const EXPECTED_COUNT: u32 = 9429;
const EXPECTED_PROVENANCE: &str = "spike/joinchain_reference.py";
const JC_OK: i32 = 0;

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
        "joinchain_golden line {idx}: bits field `{field}` not 16 hex digits"
    );
    let mut bytes = [0u8; 8];
    for i in 0..8 {
        bytes[i] = u8::from_str_radix(&field[i * 2..i * 2 + 2], 16)
            .unwrap_or_else(|e| panic!("joinchain_golden line {idx}: bad bits `{field}`: {e}"));
    }
    u64::from_le_bytes(bytes)
}

#[test]
fn joinchain_golden_replays_the_original_reference_engine() {
    // ---- Locate the fixture relative to the package root (CWD-independent). ----
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR set by cargo");
    let fixture_path = Path::new(&manifest_dir).join("tests/fixtures/joinchain_golden.txt");
    let text = std::fs::read_to_string(&fixture_path)
        .unwrap_or_else(|e| panic!("joinchain_golden: cannot read {fixture_path:?}: {e}"));
    let lines: Vec<&str> = text.lines().collect();

    // ---- Header guardrails: count, provenance, checksum. ----
    let header_count = lines
        .iter()
        .find_map(|l| {
            l.strip_prefix("# count=")
                .map(|v| v.parse::<u32>().unwrap())
        })
        .expect("joinchain_golden: missing `# count=` header");
    let provenance = lines
        .iter()
        .find_map(|l| l.strip_prefix("# provenance=").map(|s| s.to_string()))
        .expect("joinchain_golden: missing `# provenance=` header");
    let checksum = lines
        .iter()
        .find_map(|l| l.strip_prefix("# checksum=").map(|s| s.to_string()))
        .expect("joinchain_golden: missing `# checksum=` header");

    // HARD COUNT: the fixture is a fixed, lossless capture of exactly 9426 cases.
    assert_eq!(
        header_count, EXPECTED_COUNT,
        "joinchain_golden: header count {header_count} != expected {EXPECTED_COUNT}"
    );
    // PROVENANCE: traceable to the original oracle, not to native code.
    assert_eq!(
        provenance.as_str(),
        EXPECTED_PROVENANCE,
        "joinchain_golden: provenance {provenance} != {EXPECTED_PROVENANCE}"
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
        "joinchain_golden: {} data lines != header count {header_count}",
        data_lines.len()
    );
    let data_section = data_lines.join("\n") + "\n";
    let actual_checksum = format!("{:016x}", fnv1a64(data_section.as_bytes()));
    assert_eq!(
        actual_checksum, checksum,
        "joinchain_golden: fixture checksum {actual_checksum} != header {checksum} (fixture tampered)"
    );

    // ---- Replay every row against the real ABI. ----
    let mut total_chains = 0i64;
    let mut total_members = 0i64;
    let mut closed_seen = 0usize;
    let mut open_seen = 0usize;
    let mut rev_seen = 0usize;
    let mut norev_seen = 0usize;
    let mut empty_seen = 0usize;

    for (idx, line) in data_lines.iter().enumerate() {
        let f: Vec<&str> = line.split('\t').collect();
        assert_eq!(
            f.len(),
            9,
            "joinchain_golden line {idx}: expected 9 fields, got {}: {line:?}",
            f.len()
        );
        let group = f[0];
        let n: usize = f[1]
            .parse()
            .unwrap_or_else(|e| panic!("joinchain_golden line {idx}: bad n `{}`: {e}", f[1]));
        let tol = f64::from_bits(bits_of(f[2], idx));

        // starts / ends: n*3 f64 (empty when n==0).
        let starts: Vec<f64> = if f[3].is_empty() {
            Vec::new()
        } else {
            f[3].split(',')
                .map(|s| f64::from_bits(bits_of(s, idx)))
                .collect()
        };
        let ends: Vec<f64> = if f[4].is_empty() {
            Vec::new()
        } else {
            f[4].split(',')
                .map(|s| f64::from_bits(bits_of(s, idx)))
                .collect()
        };
        assert_eq!(
            starts.len(),
            3 * n,
            "joinchain_golden line {idx}: starts has {} values, expected {} (n={n})",
            starts.len(),
            3 * n
        );
        assert_eq!(
            ends.len(),
            3 * n,
            "joinchain_golden line {idx}: ends has {} values, expected {} (n={n})",
            ends.len(),
            3 * n
        );
        let exp_nch: i64 = f[5]
            .parse()
            .unwrap_or_else(|e| panic!("joinchain_golden line {idx}: bad nch `{}`: {e}", f[5]));
        let exp_nmem: i64 = f[7]
            .parse()
            .unwrap_or_else(|e| panic!("joinchain_golden line {idx}: bad nmem `{}`: {e}", f[7]));

        // Expected chains: (off, len, closed, [sx,sy,sz,ex,ey,ez] as bit patterns).
        let exp_chains: Vec<(i64, i64, u8, [u64; 6])> = if f[6].is_empty() {
            Vec::new()
        } else {
            f[6].split(';')
                .map(|rec| {
                    let p: Vec<&str> = rec.split(',').collect();
                    assert_eq!(
                        p.len(),
                        9,
                        "joinchain_golden line {idx}: chain record `{rec}` not 9 parts"
                    );
                    (
                        p[0].parse().unwrap(),
                        p[1].parse().unwrap(),
                        p[2].parse().unwrap(),
                        [
                            bits_of(p[3], idx),
                            bits_of(p[4], idx),
                            bits_of(p[5], idx),
                            bits_of(p[6], idx),
                            bits_of(p[7], idx),
                            bits_of(p[8], idx),
                        ],
                    )
                })
                .collect()
        };
        // Expected members: (seg_index, rev).
        let exp_members: Vec<(i64, u8)> = if f[8].is_empty() {
            Vec::new()
        } else {
            f[8].split(';')
                .map(|rec| {
                    let p: Vec<&str> = rec.split(',').collect();
                    assert_eq!(
                        p.len(),
                        2,
                        "joinchain_golden line {idx}: member record `{rec}` not 2 parts"
                    );
                    (p[0].parse().unwrap(), p[1].parse().unwrap())
                })
                .collect()
        };
        assert_eq!(
            exp_chains.len() as i64,
            exp_nch,
            "joinchain_golden line {idx}: {} chain records != nch {exp_nch}",
            exp_chains.len()
        );
        assert_eq!(
            exp_members.len() as i64,
            exp_nmem,
            "joinchain_golden line {idx}: {} member records != nmem {exp_nmem}",
            exp_members.len()
        );

        // Out buffers sized at N (capacity >= n). For n==0 these are empty and
        // the ABI early-returns with zero counts without touching them.
        let mut chain_off = vec![0i64; n];
        let mut chain_len = vec![0i64; n];
        let mut chain_closed = vec![0u8; n];
        let mut chain_sx = vec![0f64; n];
        let mut chain_sy = vec![0f64; n];
        let mut chain_sz = vec![0f64; n];
        let mut chain_ex = vec![0f64; n];
        let mut chain_ey = vec![0f64; n];
        let mut chain_ez = vec![0f64; n];
        let mut member_seg = vec![0i64; n];
        let mut member_rev = vec![0u8; n];
        let mut n_chains: i64 = -1;
        let mut n_members: i64 = -1;

        let rc = unsafe {
            narwhal_pure_core_joinchain(
                n as i64,
                starts.as_ptr(),
                ends.as_ptr(),
                tol,
                chain_off.as_mut_ptr(),
                chain_len.as_mut_ptr(),
                chain_closed.as_mut_ptr(),
                chain_sx.as_mut_ptr(),
                chain_sy.as_mut_ptr(),
                chain_sz.as_mut_ptr(),
                chain_ex.as_mut_ptr(),
                chain_ey.as_mut_ptr(),
                chain_ez.as_mut_ptr(),
                member_seg.as_mut_ptr(),
                member_rev.as_mut_ptr(),
                &mut n_chains,
                &mut n_members,
            )
        };
        assert_eq!(
            rc, JC_OK,
            "joinchain_golden line {idx} ({group}): rc {rc} != OK ({line:?})"
        );
        assert_eq!(
            n_chains, exp_nch,
            "joinchain_golden line {idx} ({group}): n_chains {n_chains} != expected {exp_nch}"
        );
        assert_eq!(
            n_members, exp_nmem,
            "joinchain_golden line {idx} ({group}): n_members {n_members} != expected {exp_nmem}"
        );

        if n == 0 {
            empty_seen += 1;
            continue;
        }

        // Chains: exact offsets, lengths, closed flags, head & tail ends (by bits).
        for (i, (off, len, closed, coords)) in exp_chains.iter().enumerate() {
            assert_eq!(
                chain_off[i], *off,
                "joinchain_golden line {idx} ({group}) chain {i}: off {} != {off}",
                chain_off[i]
            );
            assert_eq!(
                chain_len[i], *len,
                "joinchain_golden line {idx} ({group}) chain {i}: len {} != {len}",
                chain_len[i]
            );
            assert_eq!(
                chain_closed[i], *closed,
                "joinchain_golden line {idx} ({group}) chain {i}: closed {} != {closed}",
                chain_closed[i]
            );
            let got = [
                chain_sx[i].to_bits(),
                chain_sy[i].to_bits(),
                chain_sz[i].to_bits(),
                chain_ex[i].to_bits(),
                chain_ey[i].to_bits(),
                chain_ez[i].to_bits(),
            ];
            assert_eq!(
                got, *coords,
                "joinchain_golden line {idx} ({group}) chain {i}: head/tail {got:?} != expected {coords:?}"
            );
            if *closed == 1 {
                closed_seen += 1;
            } else {
                open_seen += 1;
            }
        }
        // Members: exact segment index + reversal flag, in order.
        for (j, (seg, rev)) in exp_members.iter().enumerate() {
            assert_eq!(
                member_seg[j], *seg,
                "joinchain_golden line {idx} ({group}) member {j}: seg {} != {seg}",
                member_seg[j]
            );
            assert_eq!(
                member_rev[j], *rev,
                "joinchain_golden line {idx} ({group}) member {j}: rev {} != {rev}",
                member_rev[j]
            );
            if *rev == 1 {
                rev_seen += 1;
            } else {
                norev_seen += 1;
            }
        }

        total_chains += exp_nch;
        total_members += exp_nmem;
    }

    // ---- Accounting: a real capture is a mix, and every class is exercised. ----
    assert!(total_chains > 0, "joinchain_golden: no chains replayed");
    assert!(total_members > 0, "joinchain_golden: no members replayed");
    assert!(
        closed_seen > 0,
        "joinchain_golden: no closed chain replayed"
    );
    assert!(open_seen > 0, "joinchain_golden: no open chain replayed");
    assert!(
        rev_seen > 0,
        "joinchain_golden: no reversed member replayed"
    );
    assert!(
        norev_seen > 0,
        "joinchain_golden: no non-reversed member replayed"
    );
    assert!(
        empty_seen > 0,
        "joinchain_golden: no empty (n=0) route replayed"
    );
}
