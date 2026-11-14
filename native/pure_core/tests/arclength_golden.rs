//! RUST-ARCLENGTH-TEST-20: golden replay of the retired CPython arc-length inversion.
//!
//! This integration test replays every recorded parameter-at-length case from
//! `tests/fixtures/arclength_curves.txt` (134 prepared NURBS curves: named fixtures and
//! 125 seeded random curves of degree 1-5, rational and non-rational, multiple knots and
//! shifted domains) and `tests/fixtures/arclength_golden.txt` (5315 rows) against the REAL
//! C ABI (`narwhal_pure_core_parameters_at_lengths`), asserting the result bits match the
//! expectation captured ONCE from the ORIGINAL independent oracle: the CPython
//! `arclength._invert` fallback (the retired `spike/test_native_arclength.py`, seeds 11 and
//! 20261108). The native library is replayed for routing and value; it never supplied the
//! expectation. No Python engine remains at test time; no Rust code is an oracle anywhere.
//!
//! What is pinned (all of it sourced from the original Python `_invert`, never re-derived):
//!   * the exact parameter bits for every DONE row (5312) — bit-for-bit, signed zeros intact
//!   * the routing: every PYTHON row (0 here) and every refusal (3) must come back
//!     `PYTHON` with a NaN parameter, never an invented value
//!   * the invalid-metadata ABI error path: null input, null output, bad order,
//!     count overflow, non-finite metadata, non-monotonic table, `Nurbs` rejection —
//!     all return -1/-2 and leave the output buffer untouched; the empty (count=0) route
//!     is valid
//!
//! Guardrails (each exists because a prior defect slipped through):
//!   * HARD COUNT   -- each fixture must contain exactly the recorded number of data
//!     lines, and the header `count` must equal the actual count.
//!   * PROVENANCE   -- the header must name the retired test and its SHA-256, and the
//!     private generator and its SHA-256, so the fixture is traceable to
//!     the original oracle (a drifted source fails loudly, not silently).
//!   * CHECKSUM     -- an FNV-1a 64 over the data section must match the header checksum,
//!     so a truncated/edited fixture fails loudly.
//!   * COVERAGE     -- the replay must exercise rational and non-rational curves,
//!     shifted domains and all three tolerances, so the capture is a real
//!     mix and not a degenerate slice.
//!
//! Note the fixtures are BIG-endian (`struct.pack('>d')`); decode with `from_be_bytes`,
//! unlike the little-endian `norm_golden` fixtures.

use std::env;
use std::path::Path;

use narwhal_pure_core::arclength::{narwhal_pure_core_parameters_at_lengths, DONE, PYTHON};

const OK: i32 = 0;
const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;

// ---- Recorded hard counts (the capture's printed accounting). ----
const EXPECTED_PROVENANCE: &str = "spike/test_native_arclength.py";
const EXPECTED_PROVENANCE_SHA: &str =
    "00c3767dd543861fc8c167dc131b1420efdb06a8ce37a78b64329cb9a76e5488";
const EXPECTED_GEN: &str = "legacy/retired-rust20/arclength/capture.py";
const EXPECTED_GEN_SHA: &str = "9bff368a974ac63d0b63113e95a90aa57f5d69673046746ae1009195cce16d6e";

const CURVES: u32 = 134;
const ROWS: u32 = 5315;
const COUNT_D: u32 = 5312;
const COUNT_P: u32 = 0;
const COUNT_E: u32 = 3;

/// FNV-1a 64, byte-identical to the capture's implementation.
fn fnv1a64(data: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for b in data {
        h ^= *b as u64;
        h = h.wrapping_mul(0x00000100000001b3);
    }
    h
}

/// Decode a 16-hex-digit BIG-endian byte field to the exact f64 bit pattern.
/// The fixture stores `struct.pack('>d', x).hex()`; reconstruct the u64 with
/// `from_be_bytes` so the comparison is byte-exact and endianness-independent.
fn decode_hex(field: &str, where_: &str) -> f64 {
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
    f64::from_bits(u64::from_be_bytes(bytes))
}

/// Decode a `;`-group of comma-separated hex doubles; an empty group is an empty list
/// (the 4-point cubic has no interior table knots).
fn decode_hexlist(field: &str, where_: &str) -> Vec<f64> {
    if field.is_empty() {
        return Vec::new();
    }
    field.split(',').map(|s| decode_hex(s, where_)).collect()
}

fn read_header(lines: &[&str], _name: &str, key: &str) -> Option<String> {
    // The capture writes `# key=value` (a space after the `#`).
    lines
        .iter()
        .find_map(|l| l.strip_prefix(&format!("# {key}=")).map(|s| s.to_string()))
}

fn read_u32(lines: &[&str], name: &str, key: &str) -> u32 {
    read_header(lines, name, key)
        .and_then(|v| v.parse::<u32>().ok())
        .unwrap_or_else(|| panic!("{name}: missing or bad `#{key}=` header"))
}

/// Validate one fixture's header guardrails (provenance, generator, count, checksum)
/// and return its data lines.
fn load_data(name: &str, expected_count: u32, expected_checksum: &str) -> Vec<String> {
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR set by cargo");
    let path = Path::new(&manifest_dir).join("tests/fixtures").join(name);
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("{name}: cannot read {:?}: {e}", path));
    let lines: Vec<&str> = text.lines().collect();

    assert_eq!(
        read_header(&lines, name, "provenance").as_deref(),
        Some(EXPECTED_PROVENANCE),
        "{name}: provenance drift"
    );
    assert_eq!(
        read_header(&lines, name, "provenance_sha256").as_deref(),
        Some(EXPECTED_PROVENANCE_SHA),
        "{name}: provenance sha drift"
    );
    assert_eq!(
        read_header(&lines, name, "generator").as_deref(),
        Some(EXPECTED_GEN),
        "{name}: generator drift"
    );
    assert_eq!(
        read_header(&lines, name, "generator_sha256").as_deref(),
        Some(EXPECTED_GEN_SHA),
        "{name}: generator sha drift"
    );

    let header_count = read_u32(&lines, name, "count");
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
        actual, expected_checksum,
        "{name}: fixture checksum {actual} != header {expected_checksum} (tampered)"
    );

    data
}

/// One prepared curve + its span table + the Gauss-Legendre rule, as the ABI needs them.
struct Curve {
    order: usize,
    knots: Vec<f64>,
    cvs: Vec<f64>,
    rational: u8,
    t0: f64,
    t1: f64,
    total: f64,
    table_knots: Vec<f64>,
    cum: Vec<f64>,
    nodes: Vec<f64>,
    weights: Vec<f64>,
}

const CURVES_CHECKSUM: &str = "fa8a3029ed2c6833";

fn load_curves() -> Vec<Curve> {
    let data = load_data("arclength_curves.txt", CURVES, CURVES_CHECKSUM);
    let mut curves = Vec::with_capacity(data.len());
    for (i, line) in data.iter().enumerate() {
        let where_ = format!("arclength_curves line {i}");
        let g: Vec<&str> = line.split(';').collect();
        assert_eq!(g.len(), 11, "{where_}: expected 11 groups, got {}", g.len());

        let order = g[0]
            .parse::<usize>()
            .unwrap_or_else(|e| panic!("{where_}: bad order `{}`: {e}", g[0]));
        let knots = decode_hexlist(g[1], &where_);
        let cvs = decode_hexlist(g[2], &where_);
        let rational = g[3]
            .parse::<u8>()
            .unwrap_or_else(|e| panic!("{where_}: bad rational `{}`: {e}", g[3]));
        let (t0, t1, total) = (
            decode_hex(g[4], &where_),
            decode_hex(g[5], &where_),
            decode_hex(g[6], &where_),
        );
        let table_knots = decode_hexlist(g[7], &where_);
        let cum = decode_hexlist(g[8], &where_);
        let nodes = decode_hexlist(g[9], &where_);
        let weights = decode_hexlist(g[10], &where_);

        assert!(
            cvs.len().is_multiple_of(4),
            "{where_}: cvs count {} not a multiple of 4",
            cvs.len()
        );
        let cv_count = cvs.len() / 4;
        assert!(
            cv_count >= order,
            "{where_}: cv_count {cv_count} < order {order}"
        );
        assert_eq!(
            knots.len(),
            cv_count + order - 2,
            "{where_}: knot_count {} != cv_count + order - 2 ({} + {} - 2)",
            knots.len(),
            cv_count,
            order
        );
        assert_eq!(
            cum.len(),
            table_knots.len() + 2,
            "{where_}: cum length {} != table_knots length + 2 ({} + 2)",
            cum.len(),
            table_knots.len()
        );
        assert_eq!(
            nodes.len(),
            weights.len(),
            "{where_}: nodes/weights length mismatch"
        );
        assert!(!nodes.is_empty(), "{where_}: empty Gauss-Legendre rule");

        curves.push(Curve {
            order,
            knots,
            cvs,
            rational,
            t0,
            t1,
            total,
            table_knots,
            cum,
            nodes,
            weights,
        });
    }
    curves
}

struct Row {
    kind: char,
    curve_idx: usize,
    tol: f64,
    s: f64,
    expected_bits: Option<u64>,
}

const GOLDEN_CHECKSUM: &str = "7ed1a44a63b76b2f";

fn load_golden(curve_count: usize) -> Vec<Row> {
    let manifest_dir = env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR set by cargo");
    let path = Path::new(&manifest_dir)
        .join("tests/fixtures")
        .join("arclength_golden.txt");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("arclength_golden: cannot read {:?}: {e}", path));
    let lines: Vec<&str> = text.lines().collect();
    let name = "arclength_golden";
    assert_eq!(
        read_u32(&lines, name, "count_D"),
        COUNT_D,
        "{name}: count_D drift"
    );
    assert_eq!(
        read_u32(&lines, name, "count_P"),
        COUNT_P,
        "{name}: count_P drift"
    );
    assert_eq!(
        read_u32(&lines, name, "count_E"),
        COUNT_E,
        "{name}: count_E drift"
    );

    // The shared guardrails (provenance/generator/count/checksum) run over the data section.
    let data = load_data("arclength_golden.txt", ROWS, GOLDEN_CHECKSUM);
    let mut rows = Vec::with_capacity(data.len());
    for (i, line) in data.iter().enumerate() {
        let where_ = format!("arclength_golden row {i}");
        let f: Vec<&str> = line.split('|').collect();
        assert_eq!(f.len(), 7, "{where_}: expected 7 fields, got {}", f.len());

        let kind = f[0]
            .chars()
            .next()
            .unwrap_or_else(|| panic!("{where_}: empty kind"));
        let curve_idx = f[1]
            .parse::<usize>()
            .unwrap_or_else(|e| panic!("{where_}: bad curve_idx `{}`: {e}", f[1]));
        assert!(
            curve_idx < curve_count,
            "{where_}: curve_idx {curve_idx} >= curve_count {curve_count}"
        );
        let tol = decode_hex(f[2], &where_);
        let s = decode_hex(f[3], &where_);

        let expected_bits = match kind {
            'D' | 'P' => {
                assert!(
                    f[4].len() == 16,
                    "{where_}: {kind} row expected_bits `{}` not 16 hex",
                    f[4]
                );
                assert!(
                    f[5].is_empty(),
                    "{where_}: {kind} row has errtype `{}`",
                    f[5]
                );
                assert!(
                    f[6].is_empty(),
                    "{where_}: {kind} row has errmsg `{}`",
                    f[6]
                );
                Some(decode_hex(f[4], &where_).to_bits())
            }
            'E' => {
                assert!(
                    f[4].is_empty(),
                    "{where_}: E row has expected_bits `{}`",
                    f[4]
                );
                assert_eq!(
                    f[5], "ValueError",
                    "{where_}: E row errtype `{}` != ValueError",
                    f[5]
                );
                // The error message is the Python's (replayed by the host test), not the
                // native's; here we only pin that the native routes it back to Python.
                assert!(
                    !f[6].is_empty(),
                    "{where_}: E row missing the Python's error message"
                );
                None
            }
            other => panic!("{where_}: unknown kind `{other}`"),
        };

        rows.push(Row {
            kind,
            curve_idx,
            tol,
            s,
            expected_bits,
        });
    }
    rows
}

/// Call the ABI for one length on one curve; returns (rc, parameter, status).
fn one(c: &Curve, tol: f64, s: f64) -> (i32, f64, u8) {
    let mut out = [f64::NAN; 1];
    let mut status = [9u8; 1];
    let rc = unsafe {
        narwhal_pure_core_parameters_at_lengths(
            c.order,
            c.knots.as_ptr(),
            c.knots.len(),
            c.cvs.as_ptr(),
            c.cvs.len() / 4,
            c.rational,
            c.t0,
            c.t1,
            c.table_knots.as_ptr(),
            c.table_knots.len(),
            c.cum.as_ptr(),
            c.total,
            c.nodes.as_ptr(),
            c.weights.as_ptr(),
            c.nodes.len(),
            tol,
            std::slice::from_ref(&s).as_ptr(),
            1,
            out.as_mut_ptr(),
            status.as_mut_ptr(),
        )
    };
    (rc, out[0], status[0])
}

#[test]
fn arclength_golden_replays_the_original_cpython_oracle() {
    let curves = load_curves();
    assert_eq!(curves.len() as u32, CURVES, "parsed curve count mismatch");
    let rows = load_golden(curves.len());
    assert_eq!(rows.len() as u32, ROWS, "parsed row count mismatch");

    // The capture is a real mix, not a degenerate slice.
    let rationals = curves.iter().filter(|c| c.rational == 1).count();
    assert!(
        rationals > 0,
        "no rational curves in the capture (degenerate)"
    );
    assert!(
        curves.iter().any(|c| c.t0 != 0.0 || c.t1 != 1.0),
        "no shifted domains (degenerate)"
    );
    assert!(
        curves.iter().any(|c| c.table_knots.is_empty()),
        "no interior-knot-free curve (degenerate)"
    );
    let tols: std::collections::BTreeSet<u64> = rows.iter().map(|r| r.tol.to_bits()).collect();
    assert!(
        tols.len() >= 3,
        "fewer than 3 distinct tolerances (degenerate)"
    );

    let mut d = 0u32;
    let mut p = 0u32;
    let mut e = 0u32;
    let mut bit_matches = 0u32;

    for (i, row) in rows.iter().enumerate() {
        let where_ = format!("arclength_golden row {i}");
        let curve = &curves[row.curve_idx];
        let (rc, got, status) = one(curve, row.tol, row.s);
        assert_eq!(rc, OK, "{where_}: rc {rc} != OK");

        match row.kind {
            'D' => {
                d += 1;
                assert_eq!(status, DONE, "{where_}: status {status} != DONE");
                let expected_bits = row.expected_bits.expect("D row must carry expected bits");
                assert_eq!(
                    got.to_bits(),
                    expected_bits,
                    "{where_}: got {got} ({:#x}), expected ({:#x})",
                    got.to_bits(),
                    expected_bits
                );
                bit_matches += 1;
            }
            'P' => {
                p += 1;
                assert_eq!(status, PYTHON, "{where_}: status {status} != PYTHON");
                assert!(got.is_nan(), "{where_}: expected NaN, got {got}");
            }
            'E' => {
                e += 1;
                // The native must route the refusal back to Python (NaN), never invent a value.
                assert_eq!(status, PYTHON, "{where_}: status {status} != PYTHON");
                assert!(got.is_nan(), "{where_}: expected NaN, got {got}");
            }
            other => panic!("{where_}: unknown kind `{other}`"),
        }
    }

    assert_eq!(d, COUNT_D, "D count {d} != {COUNT_D}");
    assert_eq!(p, COUNT_P, "P count {p} != {COUNT_P}");
    assert_eq!(e, COUNT_E, "E count {e} != {COUNT_E}");
    assert_eq!(
        bit_matches, COUNT_D,
        "bit-match count {bit_matches} != {COUNT_D}"
    );

    eprintln!(
        "arclength_golden: replayed {ROWS} exact-bit cases (D={d} P={p} E={e}) on {CURVES} curves \
         ({rationals} rational); bit_matches={bit_matches}; tolerances={}",
        tols.len()
    );
}

#[test]
fn arclength_golden_replays_invalid_metadata_error_path() {
    // A valid, readable, aligned clamped cubic (5 cvs, order 4, one interior knot) is
    // the baseline; each case below corrupts exactly ONE field and must be rejected
    // before the ABI touches the output, which must stay untouched.
    #[derive(Clone)]
    struct P {
        order: usize,
        knots: *const f64,
        knot_count: usize,
        cvs: *const f64,
        cv_count: usize,
        rational: u8,
        t0: f64,
        t1: f64,
        table: *const f64,
        table_count: usize,
        cum: *const f64,
        total: f64,
        nodes: *const f64,
        weights: *const f64,
        node_count: usize,
        tolerance: f64,
        lengths: *const f64,
        length_count: usize,
    }
    // The single field to corrupt for a case. `None` = a whole-buffer corruption
    // (non-monotonic table / non-finite control point) handled below.
    #[derive(Clone, Copy)]
    enum Bad {
        NullKnots,
        NullCvs,
        NullTable,
        NullCum,
        NullNodes,
        NullWeights,
        KnotOverflow,
        CvOverflow,
        Rational2,
        NodeZero,
        NanT0,
        InfTotal,
        NanTol,
        Order1,
        KnotMismatch,
    }
    let knots = [0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 2.0];
    let cvs: Vec<f64> = (0..5).flat_map(|i| [i as f64, 0.0, 0.0, 1.0]).collect();
    let table = [1.0];
    let cum = [0.0, 2.0, 4.0];
    let nodes = [-1.0_f64.sqrt().recip(), 1.0_f64.sqrt().recip()];
    let weights = [1.0, 1.0];
    let s = [1.0];
    let sentinel = 123.0;
    let base = P {
        order: 4,
        knots: knots.as_ptr(),
        knot_count: knots.len(),
        cvs: cvs.as_ptr(),
        cv_count: cvs.len() / 4,
        rational: 0,
        t0: 0.0,
        t1: 2.0,
        table: table.as_ptr(),
        table_count: table.len(),
        cum: cum.as_ptr(),
        total: 4.0,
        nodes: nodes.as_ptr(),
        weights: weights.as_ptr(),
        node_count: nodes.len(),
        tolerance: 0.01,
        lengths: s.as_ptr(),
        length_count: 1,
    };

    fn call(p: &P, out: *mut f64, st: *mut u8) -> i32 {
        unsafe {
            narwhal_pure_core_parameters_at_lengths(
                p.order,
                p.knots,
                p.knot_count,
                p.cvs,
                p.cv_count,
                p.rational,
                p.t0,
                p.t1,
                p.table,
                p.table_count,
                p.cum,
                p.total,
                p.nodes,
                p.weights,
                p.node_count,
                p.tolerance,
                p.lengths,
                p.length_count,
                out,
                st,
            )
        }
    }
    // Apply a single-field corruption to the baseline, call, and assert the code and
    // that the sentinel output is untouched (the ABI rejects before writing).
    let run_case = |expect: i32, label: &str, p: &mut P| {
        let mut out = [sentinel];
        let mut st = [9u8];
        let rc = call(p, out.as_mut_ptr(), st.as_mut_ptr());
        assert_eq!(rc, expect, "{label}: rc {rc} != {expect}");
        assert_eq!(out[0], sentinel, "{label}: wrote output {out:?}");
    };
    // A valid call must succeed, proving the buffers are good before we corrupt them.
    {
        let mut out = [sentinel];
        let mut st = [9u8];
        assert_eq!(
            call(&base, out.as_mut_ptr(), st.as_mut_ptr()),
            OK,
            "baseline rc != OK"
        );
    }

    // Null input buffer, or a count that overflows a real allocation -> ERR_NULL.
    let null_cases: [(&str, Bad); 7] = [
        ("null curve knots", Bad::NullKnots),
        ("null cvs", Bad::NullCvs),
        ("null table knots", Bad::NullTable),
        ("null cum", Bad::NullCum),
        ("null nodes", Bad::NullNodes),
        ("null weights", Bad::NullWeights),
        ("knot_count overflow", Bad::KnotOverflow),
    ];
    for (label, bad) in null_cases {
        let mut p = base.clone();
        match bad {
            Bad::NullKnots => p.knots = std::ptr::null(),
            Bad::NullCvs => p.cvs = std::ptr::null(),
            Bad::NullTable => p.table = std::ptr::null(),
            Bad::NullCum => p.cum = std::ptr::null(),
            Bad::NullNodes => p.nodes = std::ptr::null(),
            Bad::NullWeights => p.weights = std::ptr::null(),
            Bad::KnotOverflow => p.knot_count = usize::MAX,
            _ => unreachable!("{label}"),
        }
        run_case(ERR_NULL, label, &mut p);
    }

    // Bad metadata (a value, not a buffer) -> ERR_INPUT.
    let input_cases: [(&str, Bad); 8] = [
        ("cv_count overflow", Bad::CvOverflow),
        ("rational > 1", Bad::Rational2),
        ("node_count zero", Bad::NodeZero),
        ("non-finite t0", Bad::NanT0),
        ("non-finite total", Bad::InfTotal),
        ("non-finite tolerance", Bad::NanTol),
        ("order out of range", Bad::Order1),
        ("knot_count mismatch", Bad::KnotMismatch),
    ];
    for (label, bad) in input_cases {
        let mut p = base.clone();
        match bad {
            Bad::CvOverflow => p.cv_count = usize::MAX,
            Bad::Rational2 => p.rational = 2,
            Bad::NodeZero => p.node_count = 0,
            Bad::NanT0 => p.t0 = f64::NAN,
            Bad::InfTotal => p.total = f64::INFINITY,
            Bad::NanTol => p.tolerance = f64::NAN,
            Bad::Order1 => p.order = 1,
            Bad::KnotMismatch => p.knot_count = base.knot_count - 2,
            _ => unreachable!("{label}"),
        }
        run_case(ERR_INPUT, label, &mut p);
    }

    // Non-monotonic table (a decreasing interior-knot pair) must be refused.
    {
        let bad_table = [2.0, 1.0];
        let mut p = base.clone();
        p.table = bad_table.as_ptr();
        p.table_count = bad_table.len();
        run_case(ERR_INPUT, "non-monotonic table", &mut p);
    }
    // A non-finite control point must be refused.
    {
        let bad_cvs: Vec<f64> = {
            let mut v = cvs.clone();
            v[0] = f64::NAN;
            v
        };
        let mut p = base.clone();
        p.cvs = bad_cvs.as_ptr();
        run_case(ERR_INPUT, "non-finite control point", &mut p);
    }
    // Null output (otherwise-valid metadata) must be refused and cannot be written.
    {
        let rc = call(&base, std::ptr::null_mut(), std::ptr::null_mut());
        assert_eq!(rc, ERR_NULL, "null out_params rc {rc} != ERR_NULL");
    }
    // Empty (count=0) route is valid: valid metadata, zero lengths, null in/out.
    {
        let mut p = base.clone();
        p.length_count = 0;
        p.lengths = std::ptr::null();
        let rc = call(&p, std::ptr::null_mut(), std::ptr::null_mut());
        assert_eq!(rc, OK, "empty (count=0) route rc {rc} != OK");
    }

    eprintln!(
        "arclength_golden: invalid-metadata error path (null/bad-order/overflow/non-finite/\
         non-monotonic) + empty route, output untouched on every rejection"
    );
}
