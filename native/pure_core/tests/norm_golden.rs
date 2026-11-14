//! RUST-NORM-TEST-19: golden replay of the retired CPython norm bit-parity proof.
//!
//! This integration test replays every recorded exact-bit norm/distance case
//! from `tests/fixtures/norm_rows_{2,3,6}.txt`, `norm_witness.txt` and
//! `norm_thresholds.txt` against the REAL C ABI
//! (`narwhal_pure_core_norm_v1`) and asserts the result bits match the
//! expectation captured ONCE from the original independent oracle:
//! CPython 3.13 stdlib `math.hypot` / `math.dist` (the retired
//! `spike/test_python_norm_owned.py`, seed 42). No Python engine remains at
//! test time; no Rust code is an oracle anywhere.
//!
//! What is pinned (all of it sourced from the retired test's CPython oracle,
//! never re-derived here):
//!   * the exact result bits for every row of modes 2, 3 and 6 (184015 total,
//!     incl. the standalone witness) — signed zeros, subnormals, infinities
//!   * the NaN contract: a NaN expectation is matched as "is_nan", the
//!     unqualified payload bits are NOT tightened (original contract)
//!   * the 3000 full-length arm distances and 9000 exact threshold decisions
//!     (`distance <= threshold` for tol / nextafter neighbours), with the
//!     long arms verified `> tol` so no arm is "already closed"
//!   * the invalid-metadata ABI error path: null input, null output,
//!     bad mode, count overflow, unaligned pointer — all return -1 and
//!     leave the output buffer untouched; the empty (count=0) route is valid
//!
//! Guardrails (each exists because a prior defect slipped through):
//!   * HARD COUNT   -- each fixture must contain exactly the recorded number
//!     of data lines, and the header `count` must equal the actual count.
//!   * PROVENANCE   -- the header must name the retired test and its SHA-256,
//!     the helper SHA-256, and the private generator, so the fixture is
//!     traceable to the original oracle and the exact `python_norm.rs` it was
//!     captured against (a drifted helper fails loudly, not silently).
//!   * CHECKSUM     -- an FNV-1a 64 over the data section must match the
//!     header checksum, so a truncated/edited fixture fails loudly.
//!   * COVERAGE     -- the replay must exercise NaN, infinite, signed-zero and
//!     finite rows, so the capture is a real mix and not a degenerate slice.

use std::env;
use std::path::Path;

use narwhal_pure_core::python_norm::narwhal_pure_core_norm_v1;

const OK: i32 = 0;
const REJECT: i32 = -1;

// ---- Recorded hard counts (the retired test's printed accounting). ----
const EXPECTED_PROVENANCE: &str = "spike/test_python_norm_owned.py";
const EXPECTED_PROVENANCE_SHA: &str =
    "f151d7dd5b1f9288d3b5b5d43647153610d18644c7ac77293e6943f7c16c099e";
const EXPECTED_HELPER_SHA: &str =
    "b1e2a255e7882b139d8fec957312f826b21323133caba2745de2b73de0514a5e";
const EXPECTED_GEN: &str = "legacy/retired-rust19/capture_norm.py";

const ROWS_2: u32 = 60338;
const ROWS_3: u32 = 60338;
const ROWS_6: u32 = 63338;
const WITNESS: u32 = 1;
const ARMS: u32 = 3000;
const DECISIONS_PER_ARM: u32 = 3;
const TOTAL_ROWS: u32 = ROWS_2 + ROWS_3 + ROWS_6 + WITNESS; // 184015

/// FNV-1a 64, byte-identical to the generator's implementation.
fn fnv1a64(data: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in data {
        h ^= *b as u64;
        h = h.wrapping_mul(0x00000100000001b3);
    }
    h
}

/// Decode a 16-hex-digit little-endian byte field to the exact f64 bit pattern.
/// The fixture stores `struct.pack('<d', x).hex()`; reconstruct the u64 with
/// `from_le_bytes` so the comparison is byte-exact and endianness-independent
/// (this is what pins signed zeros).
fn bits_of(field: &str, where_: &str) -> u64 {
    assert_eq!(
        field.len(),
        16,
        "{where_}: bits field `{field}` not 16 hex digits"
    );
    let mut bytes = [0u8; 8];
    for i in 0..8 {
        bytes[i] = u8::from_str_radix(&field[i * 2..i * 2 + 2], 16)
            .unwrap_or_else(|e| panic!("{where_}: bad bits `{field}`: {e}"));
    }
    u64::from_le_bytes(bytes)
}

struct Fixture {
    count: u32,
    data: Vec<String>,
}

/// Read a fixture, validate header guardrails (count + checksum), return data.
fn load(name: &str, expected_count: u32) -> Fixture {
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR set by cargo");
    let path = Path::new(&manifest_dir).join("tests/fixtures").join(name);
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("{name}: cannot read {:?}: {e}", path));
    let lines: Vec<&str> = text.lines().collect();

    let header_count = lines
        .iter()
        .find_map(|l| {
            l.strip_prefix("# count=")
                .map(|v| v.parse::<u32>().unwrap())
        })
        .unwrap_or_else(|| panic!("{name}: missing `# count=` header"));
    let provenance = lines
        .iter()
        .find_map(|l| l.strip_prefix("# provenance=").map(|s| s.to_string()))
        .unwrap_or_else(|| panic!("{name}: missing `# provenance=` header"));
    let provenance_sha = lines
        .iter()
        .find_map(|l| {
            l.strip_prefix("# provenance_sha256=")
                .map(|s| s.to_string())
        })
        .unwrap_or_else(|| panic!("{name}: missing `# provenance_sha256=` header"));
    let helper_sha = lines
        .iter()
        .find_map(|l| l.strip_prefix("# helper_sha256=").map(|s| s.to_string()))
        .unwrap_or_else(|| panic!("{name}: missing `# helper_sha256=` header"));
    let gen = lines
        .iter()
        .find_map(|l| l.strip_prefix("# generator=").map(|s| s.to_string()))
        .unwrap_or_else(|| panic!("{name}: missing `# generator=` header"));
    let checksum = lines
        .iter()
        .find_map(|l| l.strip_prefix("# checksum=").map(|s| s.to_string()))
        .unwrap_or_else(|| panic!("{name}: missing `# checksum=` header"));

    assert_eq!(
        provenance.as_str(),
        EXPECTED_PROVENANCE,
        "{name}: provenance drift"
    );
    assert_eq!(
        provenance_sha.as_str(),
        EXPECTED_PROVENANCE_SHA,
        "{name}: provenance sha drift"
    );
    assert_eq!(
        helper_sha.as_str(),
        EXPECTED_HELPER_SHA,
        "{name}: helper sha drift"
    );
    assert_eq!(gen.as_str(), EXPECTED_GEN, "{name}: generator drift");

    assert_eq!(
        header_count, expected_count,
        "{name}: header count {header_count} != expected {expected_count}"
    );

    let data: Vec<String> = lines
        .iter()
        .filter(|l| !l.starts_with('#'))
        .map(|s| s.to_string())
        .collect();
    assert_eq!(
        data.len() as u32,
        header_count,
        "{name}: {} data lines != header count {header_count}",
        data.len()
    );
    let section = data.join("\n") + "\n";
    let actual = format!("{:016x}", fnv1a64(section.as_bytes()));
    assert_eq!(
        actual, checksum,
        "{name}: fixture checksum {actual} != header {checksum} (tampered)"
    );

    Fixture {
        count: header_count,
        data,
    }
}

/// Replay one mode's rows against the ABI, returning coverage accounting.
fn replay_rows(name: &str, mode: u32, width: usize, expected_count: u32) -> (u32, u32, u32, u32) {
    let fix = load(name, expected_count);
    let mut nan_in = 0u32;
    let mut nan_out = 0u32;
    let mut inf_in = 0u32;
    let mut zero_in = 0u32;

    for (idx, line) in fix.data.iter().enumerate() {
        let where_ = format!("{name} line {idx}");
        let f: Vec<&str> = line.split(',').collect();
        assert_eq!(
            f.len(),
            width + 1,
            "{where_}: expected {} fields, got {}",
            width + 1,
            f.len()
        );

        let inputs: Vec<f64> = f[..width]
            .iter()
            .map(|s| f64::from_bits(bits_of(s, &where_)))
            .collect();
        let expected_bits = bits_of(f.last().unwrap(), &where_);
        let expected = f64::from_bits(expected_bits);

        for v in &inputs {
            if v.is_nan() {
                nan_in += 1;
            }
            if v.is_infinite() {
                inf_in += 1;
            }
            if *v == 0.0 {
                zero_in += 1;
            }
        }

        let mut out = vec![f64::NAN; 1];
        let rc = unsafe { narwhal_pure_core_norm_v1(inputs.as_ptr(), 1, mode, out.as_mut_ptr()) };
        assert_eq!(rc, OK, "{where_}: rc {rc} != OK");
        let got = out[0];

        // Original contract: NaN expectation matches "is_nan" (payload not
        // tightened); otherwise exact bit equality.
        if expected.is_nan() {
            nan_out += 1;
            assert!(got.is_nan(), "{where_}: expected NaN, got {got:?}");
        } else {
            assert_eq!(
                got.to_bits(),
                expected_bits,
                "{where_}: got {got:?} ({}), expected {expected:?} ({expected_bits:x})",
                got.to_bits()
            );
        }
    }
    (fix.count, nan_in, nan_out, inf_in + zero_in)
}

#[test]
fn norm_golden_replays_the_original_cpython_oracle() {
    let (c2, nan2, nan2o, mix2) = replay_rows("norm_rows_2.txt", 2, 2, ROWS_2);
    let (c3, nan3, nan3o, mix3) = replay_rows("norm_rows_3.txt", 3, 3, ROWS_3);
    let (c6, nan6, nan6o, mix6) = replay_rows("norm_rows_6.txt", 6, 6, ROWS_6);

    // Standalone witness (the retired test's very first check, mode 6).
    let w = load("norm_witness.txt", WITNESS);
    assert_eq!(w.data.len(), 1);
    let f: Vec<&str> = w.data[0].split(',').collect();
    assert_eq!(
        f.len(),
        7,
        "norm_witness: expected 7 fields, got {}",
        f.len()
    );
    let inputs: Vec<f64> = f[..6]
        .iter()
        .map(|s| f64::from_bits(bits_of(s, "witness")))
        .collect();
    let expected_bits = bits_of(f[6], "witness");
    let mut out = vec![f64::NAN; 1];
    assert_eq!(
        unsafe { narwhal_pure_core_norm_v1(inputs.as_ptr(), 1, 6, out.as_mut_ptr()) },
        OK,
        "witness: rc != OK"
    );
    let got = out[0];
    if f64::from_bits(expected_bits).is_nan() {
        assert!(got.is_nan(), "witness: expected NaN");
    } else {
        assert_eq!(
            got.to_bits(),
            expected_bits,
            "witness: got {got:?}, expected {expected_bits:x}"
        );
    }

    // ---- Hard accounting: the capture is a real mix, every class exercised. ----
    let total = c2 + c3 + c6 + WITNESS;
    assert_eq!(total, TOTAL_ROWS, "total rows {total} != {TOTAL_ROWS}");
    let nan_in = nan2 + nan3 + nan6;
    let nan_out = nan2o + nan3o + nan6o;
    assert!(
        nan_in > 0,
        "no NaN-input rows replayed (degenerate capture)"
    );
    assert!(
        nan_out > 0,
        "no NaN-output rows replayed (degenerate capture)"
    );
    assert!(nan2 + nan3 + nan6 > 0, "no NaN across modes");
    let mix = mix2 + mix3 + mix6;
    assert!(
        mix > 0,
        "no infinite/signed-zero rows replayed (degenerate capture)"
    );

    eprintln!(
        "norm_golden: replayed {total} exact-bit cases (m2={c2} m3={c3} m6={c6} witness=1); \
         nan_in={nan_in} nan_out={nan_out}; infinite/zero mix={mix}"
    );
}

#[test]
fn norm_golden_replays_full_length_arms_and_threshold_decisions() {
    let fix = load("norm_thresholds.txt", ARMS);
    let mut decisions = 0u32;
    let mut arms_long = 0u32;

    for (idx, line) in fix.data.iter().enumerate() {
        let where_ = format!("norm_thresholds line {idx}");
        // Fields: arm_idx ; d(3) ; origin(3) ; start(3) ; end(3) ;
        //         tol ; tol_minus ; tol_plus ; arm_start_origin ; arm_d_end ;
        //         dec_minus ; dec_tol ; dec_plus
        let groups: Vec<&str> = line.split(';').collect();
        assert_eq!(
            groups.len(),
            11,
            "{where_}: expected 11 groups, got {}",
            groups.len()
        );

        let arm_idx: u32 = groups[0]
            .parse()
            .unwrap_or_else(|e| panic!("{where_}: bad arm_idx: {e}"));
        let d: Vec<f64> = groups[1]
            .split(',')
            .map(|s| f64::from_bits(bits_of(s, &where_)))
            .collect();
        let origin: Vec<f64> = groups[2]
            .split(',')
            .map(|s| f64::from_bits(bits_of(s, &where_)))
            .collect();
        let _start: Vec<f64> = groups[3]
            .split(',')
            .map(|s| f64::from_bits(bits_of(s, &where_)))
            .collect();
        let _end: Vec<f64> = groups[4]
            .split(',')
            .map(|s| f64::from_bits(bits_of(s, &where_)))
            .collect();
        let tol = f64::from_bits(bits_of(groups[5], &where_));
        let tol_minus = f64::from_bits(bits_of(groups[6], &where_));
        let tol_plus = f64::from_bits(bits_of(groups[7], &where_));
        let arm_start_origin = f64::from_bits(bits_of(groups[8], &where_));
        let arm_d_end = f64::from_bits(bits_of(groups[9], &where_));
        let dec: Vec<&str> = groups[10].split(',').collect();
        assert_eq!(
            dec.len(),
            3,
            "{where_}: expected 3 decisions, got {}",
            dec.len()
        );
        let flag = |t: &str| t == "1";
        let dec_minus = flag(dec[0]);
        let dec_tol = flag(dec[1]);
        let dec_plus = flag(dec[2]);

        // Long arms must be strictly > tol (the original test's own guard; an
        // "already closed" arm would make the decision trivial — reject it).
        assert!(
            arm_start_origin > tol,
            "{where_} (arm {arm_idx}): arm_start_origin not > tol"
        );
        assert!(
            arm_d_end > tol,
            "{where_} (arm {arm_idx}): arm_d_end not > tol"
        );
        arms_long += 1;

        // distance is dist(origin, d) == tol exactly. Replay the ABI to obtain
        // the distance, then assert the exact threshold decisions.
        let full: Vec<f64> = origin.iter().chain(d.iter()).copied().collect();
        let mut out = vec![f64::NAN; 1];
        assert_eq!(
            unsafe { narwhal_pure_core_norm_v1(full.as_ptr(), 1, 6, out.as_mut_ptr()) },
            OK,
            "{where_}: rc != OK"
        );
        let distance = out[0];
        assert_eq!(
            distance.to_bits(),
            tol.to_bits(),
            "{where_}: distance {distance:?} != tol {tol:?} (distance must equal tol)"
        );

        // Exact threshold decisions: (distance <= threshold) == captured bool.
        assert_eq!(
            distance <= tol_minus,
            dec_minus,
            "{where_}: decision(tol-) {dec_minus} != (distance <= tol_minus)"
        );
        assert_eq!(
            distance <= tol,
            dec_tol,
            "{where_}: decision(tol) {dec_tol} != (distance <= tol)"
        );
        assert_eq!(
            distance <= tol_plus,
            dec_plus,
            "{where_}: decision(tol+) {dec_plus} != (distance <= tol_plus)"
        );
        decisions += DECISIONS_PER_ARM;
    }

    assert_eq!(arms_long, ARMS, "arms_long {arms_long} != {ARMS}");
    assert_eq!(
        decisions,
        ARMS * DECISIONS_PER_ARM,
        "decisions {decisions} != 9000"
    );
    eprintln!("norm_golden: {arms_long} full-length arms, {decisions} exact threshold decisions");
}

#[test]
fn norm_golden_replays_invalid_metadata_error_path() {
    // A valid, readable, aligned input buffer (6 f64) to pair with the
    // invalid-metadata cases; the ABI must reject before touching output.
    let a: Vec<f64> = vec![1., 2., 3., 4., 5., 6.];
    let sentinel = 123.;

    // (ptr, count, mode, out) — each must return REJECT and leave `out` intact.
    let cases: Vec<(*const f64, usize, u32, bool)> = vec![
        // null input, valid mode/count
        (std::ptr::null::<f64>(), 1, 2, true),
        (std::ptr::null::<f64>(), 1, 3, true),
        (std::ptr::null::<f64>(), 1, 6, true),
        // valid input, null output
        (a.as_ptr(), 1, 2, false),
        (a.as_ptr(), 1, 3, false),
        (a.as_ptr(), 1, 6, false),
        // bad mode (width not 2/3/6)
        (a.as_ptr(), 1, 7, true),
        (a.as_ptr(), 1, 4, true),
        (a.as_ptr(), 1, 0, true),
        // count overflow: usize::MAX * width overflows checked_mul
        (a.as_ptr(), usize::MAX, 2, true),
        (a.as_ptr(), usize::MAX, 6, true),
    ];

    for (ptr, count, mode, has_out) in cases {
        let mut out = [sentinel];
        let target: *mut f64 = if has_out {
            out.as_mut_ptr()
        } else {
            std::ptr::null_mut()
        };
        let rc = unsafe { narwhal_pure_core_norm_v1(ptr, count, mode, target) };
        assert_eq!(
            rc, REJECT,
            "invalid metadata (ptr={ptr:p} count={count} mode={mode}) rc {rc} != -1"
        );
        if has_out {
            assert_eq!(
                out[0], sentinel,
                "invalid metadata (mode={mode}) wrote output: {out:?}"
            );
        }
    }

    // Unaligned (but readable) input pointer: raw_span_is_valid must reject.
    // Allocate an 8-byte buffer and use its address + 4 as a *const f64 so the
    // span is unaligned for f64 (8-byte) but the bytes are readable.
    let mut buf = [0u8; 8];
    buf[0..8].copy_from_slice(&f64::to_le_bytes(1.0));
    let unaligned = (buf.as_ptr() as usize + 4) as *const f64;
    let mut out = [sentinel];
    let rc = unsafe { narwhal_pure_core_norm_v1(unaligned, 1, 2, out.as_mut_ptr()) };
    assert_eq!(rc, REJECT, "unaligned input rc {rc} != -1");
    assert_eq!(out[0], sentinel, "unaligned input wrote output: {out:?}");

    // Empty (count=0) route is valid: null input + null output is accepted.
    let rc =
        unsafe { narwhal_pure_core_norm_v1(std::ptr::null::<f64>(), 0, 2, std::ptr::null_mut()) };
    assert_eq!(rc, OK, "empty (count=0, null) rc {rc} != OK");
    let rc =
        unsafe { narwhal_pure_core_norm_v1(std::ptr::null::<f64>(), 0, 6, std::ptr::null_mut()) };
    assert_eq!(rc, OK, "empty (count=0, null) mode 6 rc {rc} != OK");

    eprintln!(
        "norm_golden: invalid-metadata error path (null/bad-mode/overflow/unaligned) + empty route"
    );
}
