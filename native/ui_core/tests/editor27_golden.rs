//! Replay of the 12 frozen editor golden traces captured pre-retirement
//! (source: `spike/test_ui_core_golden.py`, `DATA['editor']`).
//!
//! `TextField` is a private (`mod editor;`) type and is deliberately not
//! re-exported, so the traces are driven through the **public FFI**
//! (`narwhal_editor_*`) — the same cross-boundary path the LIVE Python adapter
//! (`narwhal3d/native_textfield.py`, which remains in use) drives. Using the FFI
//! here mirrors that adapter rather than approximating it. (Only the *original
//! algorithm* was retired; the adapter is live.)
//!
//! Every step's outcome and the resulting state (text, caret, anchor,
//! multiline, max_chars) are compared against the frozen expected values.
//!
//! Hard guards (not merely printed): exactly 12 traces, exactly 500 steps each,
//! exactly 6000 steps total, the exact action-count map, and the exact
//! source_commit. Integer conversions are checked (no silent truncation).
//!
//! Provenance: see `data/editor27_golden.provenance.json` (golden Python SHA,
//! extracted semantic payload SHA, binary SHA/length, counts, original source
//! commit, mutation evidence).

#[path = "support/editor27_fixture.rs"]
mod editor27_fixture;

use editor27_fixture::Value;
use narwhal_ui_core::{
    narwhal_editor_abi, narwhal_editor_close, narwhal_editor_edit, narwhal_editor_read, State,
};
use std::collections::BTreeMap;

const BLOB: &[u8] = include_bytes!("data/editor27_golden.bin");
const PROVENANCE: &str = include_str!("data/editor27_golden.provenance.json");

// Hard corpus invariants (review-required; a truncated corpus must not pass).
const EXPECTED_TRACES: usize = 12;
const EXPECTED_STEPS_PER_TRACE: usize = 500;
const EXPECTED_TOTAL_STEPS: usize = 6000;
const EXPECTED_SOURCE_COMMIT: &str = "50267dd6d4504b14052d1ab7e54675f3c8750637";
const EXPECTED_BINARY_SHA256: &str =
    "8861a5fcfd81b356095466290d8a0bca529661a0da013ebe9b7385f981751944";
const EXPECTED_GOLDEN_PYTHON_SHA256: &str =
    "f550df713f03547023ce80c92b0d6c6754fbad5fa521d0d726b5464adba0b26d";
const EXPECTED_SEMANTIC_PAYLOAD_SHA256: &str =
    "7637283e8ff89c2288d5d341935346e339f97308434f4883de0406dcb0ac0bc5";

/// Exact action-count map stated in the report (name, count), sorted by name.
const EXPECTED_ACTION_COUNTS: &[(usize, &str)] = &[
    (518, "_delete_selection"),
    (568, "backspace"),
    (522, "cut"),
    (582, "delete"),
    (545, "end"),
    (564, "home"),
    (503, "insert"),
    (568, "move"),
    (543, "select_all"),
    (550, "set_caret"),
    (537, "set_text"),
];

/// Structural equality for the decoded tagged values (Value does not derive PartialEq).
fn values_eq(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Null, Value::Null) => true,
        (Value::Bool(x), Value::Bool(y)) => x == y,
        (Value::I128(x), Value::I128(y)) => x == y,
        (Value::Str(x), Value::Str(y)) => x == y,
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y.iter()).all(|(p, q)| values_eq(p, q))
        }
        (Value::Map(x), Value::Map(y)) => {
            x.len() == y.len()
                && x.iter()
                    .zip(y.iter())
                    .all(|((pk, pv), (qk, qv))| pk == qk && values_eq(pv, qv))
        }
        _ => false,
    }
}

fn to_u32(s: &str) -> Vec<u32> {
    s.chars().map(|c| c as u32).collect()
}

fn to_str(buf: &[u32]) -> String {
    buf.iter()
        .map(|&c| char::from_u32(c).expect("frozen editor data contains no lone surrogates"))
        .collect()
}

/// A live editor handle plus its last observed `State`. Closed on drop.
struct Editor {
    handle: u64,
    state: State,
}

impl Drop for Editor {
    fn drop(&mut self) {
        let _ = narwhal_editor_close(self.handle);
    }
}

impl Editor {
    /// `TextField(text, multiline, max_chars)` — FFI op 0 (construct + set_text).
    fn new(text: &[u32], multiline: bool, max_chars: Option<i64>) -> Self {
        assert_eq!(narwhal_editor_abi(), 1, "native editor ABI mismatch");
        let flags = (multiline as u32) | (max_chars.is_some() as u32) << 1;
        let value = max_chars.unwrap_or(0);
        let mut out = State::default();
        let rc = unsafe {
            narwhal_editor_edit(
                0,
                0,
                value,
                flags,
                text.as_ptr(),
                text.len(),
                &mut out,
                std::mem::size_of::<State>(),
            )
        };
        assert_eq!(rc, 0, "editor construct failed with rc {rc}");
        Editor {
            handle: out.handle,
            state: out,
        }
    }

    /// One FFI edit; returns the `result` bit (flags & 4) the adapter exposes.
    fn apply(&mut self, op: u32, value: i64, flags: u32, text: Option<&[u32]>) -> bool {
        let input = text.unwrap_or(&[]);
        let mut out = State::default();
        let rc = unsafe {
            narwhal_editor_edit(
                self.handle,
                op,
                value,
                flags,
                input.as_ptr(),
                input.len(),
                &mut out,
                std::mem::size_of::<State>(),
            )
        };
        assert_eq!(rc, 0, "editor op {op} failed with rc {rc}");
        let result = out.flags & 4 != 0;
        self.state = out;
        result
    }

    fn read_text(&self) -> String {
        let len = usize::try_from(self.state.length)
            .expect("editor text length exceeds the host usize domain");
        if len == 0 {
            return String::new();
        }
        let mut buf = vec![0u32; len];
        let rc = unsafe {
            narwhal_editor_read(
                self.handle,
                self.state.text_revision,
                0,
                buf.as_mut_ptr(),
                len,
            )
        };
        assert_eq!(rc, 0, "read editor text failed with rc {rc}");
        to_str(&buf)
    }

    /// The `[text, caret, anchor, multiline, max_chars]` projection the golden data records.
    fn state_value(&self) -> Value {
        let text = self.read_text();
        let caret = i128::from(self.state.caret);
        let anchor = if self.state.anchor < 0 {
            Value::Null
        } else {
            Value::I128(i128::from(self.state.anchor))
        };
        let multiline = self.state.flags & 1 != 0;
        let max_chars = if self.state.flags & 2 != 0 {
            Value::I128(i128::from(self.state.max_chars))
        } else {
            Value::Null
        };
        Value::Array(vec![
            Value::Str(text),
            Value::I128(caret),
            anchor,
            Value::Bool(multiline),
            max_chars,
        ])
    }

    /// The adapter's `cut`: return the selected text and, only when non-empty,
    /// delete the selection via op 3 (backspace) — exactly as the live
    /// `native_textfield.py::cut` does (`apply(3)`), which with a live selection
    /// is `delete_selection`.
    fn cut(&mut self) -> String {
        let text = self.read_text();
        let caret = usize::try_from(self.state.caret).expect("caret exceeds usize domain");
        let anchor = if self.state.anchor < 0 {
            None
        } else {
            Some(usize::try_from(self.state.anchor).expect("anchor exceeds usize domain"))
        };
        let chars: Vec<char> = text.chars().collect();
        let cut_text = match anchor {
            Some(a) if a != caret => chars[a.min(caret)..a.max(caret)].iter().collect(),
            _ => String::new(),
        };
        if !cut_text.is_empty() {
            self.apply(3, 0, 0, None);
        }
        cut_text
    }
}

/// Parse the corpus, enforce the hard count/identity guards, then replay every
/// trace through the FFI and compare outcomes + after-states.
fn replay(corpus: &[u8]) -> (usize, usize, BTreeMap<String, usize>) {
    let top = editor27_fixture::parse(corpus);
    let top_arr = top
        .as_list()
        .unwrap_or_else(|| panic!("corpus top level must be an array"));
    assert_eq!(
        top_arr.len(),
        3,
        "corpus must be [source_commit, trace_count, traces]"
    );

    let source_commit = top_arr[0].as_str().expect("source_commit string");
    assert_eq!(
        source_commit, EXPECTED_SOURCE_COMMIT,
        "source_commit mismatch: {source_commit}"
    );

    let declared_count = usize::try_from(top_arr[1].as_i128().expect("trace count i128"))
        .expect("declared trace count exceeds usize domain");
    let traces: &[Value] = top_arr[2].as_list().expect("traces must be an array");
    assert_eq!(
        traces.len(),
        declared_count,
        "declared trace count {} != actual rows {}",
        declared_count,
        traces.len()
    );

    // Hard exact-count guard: a jointly truncated (declared == rows) corpus that
    // merely agrees with itself must still be rejected.
    assert_eq!(
        traces.len(),
        EXPECTED_TRACES,
        "expected exactly {EXPECTED_TRACES} traces, found {}",
        traces.len()
    );

    let mut total_steps = 0usize;
    let mut action_counts: BTreeMap<String, usize> = BTreeMap::new();

    for (ti, trace) in traces.iter().enumerate() {
        let args = trace
            .get("args")
            .as_list()
            .unwrap_or_else(|| panic!("trace {ti} missing args array"));
        let initial = trace.get("initial");
        let steps = trace
            .get("steps")
            .as_list()
            .unwrap_or_else(|| panic!("trace {ti} missing steps array"));
        assert_eq!(
            steps.len(),
            EXPECTED_STEPS_PER_TRACE,
            "trace {ti} has {} steps, expected exactly {EXPECTED_STEPS_PER_TRACE}",
            steps.len()
        );

        let text = args[0]
            .as_str()
            .unwrap_or_else(|| panic!("trace {ti} args[0] must be text"))
            .to_string();
        let multiline = args[1]
            .as_bool()
            .unwrap_or_else(|| panic!("trace {ti} args[1] must be multiline"));
        let max_chars = match &args[2] {
            Value::Null => None,
            Value::I128(v) => Some(i64::try_from(*v).expect("max_chars exceeds i64 domain")),
            other => panic!(
                "trace {ti} max_chars must be null/int, got {:?}",
                std::mem::discriminant(other)
            ),
        };

        let cps = to_u32(&text);
        let mut ed = Editor::new(&cps, multiline, max_chars);

        // Initial projection must match the frozen `initial`.
        let initial_actual = ed.state_value();
        assert!(
            values_eq(&initial_actual, initial),
            "trace {ti} initial state mismatch\n  actual:   {initial_actual:?}\n  expected: {initial:?}"
        );

        for (si, step) in steps.iter().enumerate() {
            let step_arr = step
                .as_list()
                .unwrap_or_else(|| panic!("trace {ti} step {si} must be an array"));
            assert_eq!(
                step_arr.len(),
                4,
                "trace {ti} step {si} must be [method, args, expected, after]"
            );
            let method = step_arr[0]
                .as_str()
                .unwrap_or_else(|| panic!("trace {ti} step {si} method"))
                .to_string();
            let args_v: &[Value] = step_arr[1]
                .as_list()
                .unwrap_or_else(|| panic!("trace {ti} step {si} args must be an array"));
            let expected = &step_arr[2];
            let after = &step_arr[3];

            let result_value: Value = match method.as_str() {
                "insert" => {
                    let s = args_v[0].as_str().expect("insert text");
                    let cps = to_u32(s);
                    Value::Bool(ed.apply(2, 0, 0, Some(&cps)))
                }
                "set_text" => {
                    let s = args_v[0].as_str().expect("set_text text");
                    let cps = to_u32(s);
                    ed.apply(1, 0, 0, Some(&cps));
                    Value::Null
                }
                "set_caret" => {
                    let index = i64::try_from(args_v[0].as_i128().expect("set_caret index"))
                        .expect("set_caret index exceeds i64 domain");
                    let select = args_v[1].as_bool().expect("set_caret select");
                    ed.apply(5, index, select as u32, None);
                    Value::Null
                }
                "move" => {
                    let delta = i64::try_from(args_v[0].as_i128().expect("move delta"))
                        .expect("move delta exceeds i64 domain");
                    let select = args_v[1].as_bool().expect("move select");
                    ed.apply(6, delta, select as u32, None);
                    Value::Null
                }
                "home" => {
                    let select = args_v[0].as_bool().expect("home select");
                    ed.apply(7, 0, select as u32, None);
                    Value::Null
                }
                "end" => {
                    let select = args_v[0].as_bool().expect("end select");
                    ed.apply(8, 0, select as u32, None);
                    Value::Null
                }
                "select_all" => {
                    ed.apply(9, 0, 0, None);
                    Value::Null
                }
                "backspace" => {
                    ed.apply(3, 0, 0, None);
                    Value::Null
                }
                "delete" => {
                    ed.apply(4, 0, 0, None);
                    Value::Null
                }
                "cut" => Value::Str(ed.cut()),
                "_delete_selection" => Value::Bool(ed.apply(16, 0, 0, None)),
                other => panic!("trace {ti} step {si}: unknown method '{other}'"),
            };

            let outcome_actual = Value::Array(vec![Value::Str("ok".to_string()), result_value]);
            assert!(
                values_eq(&outcome_actual, expected),
                "trace {ti} step {si} ({method}) outcome mismatch\n  actual:   {outcome_actual:?}\n  expected: {expected:?}"
            );

            let after_actual = ed.state_value();
            assert!(
                values_eq(&after_actual, after),
                "trace {ti} step {si} ({method}) after-state mismatch\n  actual:   {after_actual:?}\n  expected: {after:?}"
            );

            total_steps += 1;
            *action_counts.entry(method.clone()).or_insert(0) += 1;
        }
    }

    // Hard exact-total guard.
    assert_eq!(
        total_steps, EXPECTED_TOTAL_STEPS,
        "expected exactly {EXPECTED_TOTAL_STEPS} total steps, found {total_steps}"
    );

    // Hard exact action-count map.
    for (count, name) in EXPECTED_ACTION_COUNTS {
        let actual = action_counts
            .get(*name)
            .copied()
            .unwrap_or_else(|| panic!("action map missing '{name}'; map = {action_counts:?}"));
        assert_eq!(
            actual, *count,
            "action '{name}' count {actual} != expected {count}"
        );
    }
    assert_eq!(
        action_counts.len(),
        EXPECTED_ACTION_COUNTS.len(),
        "unexpected extra actions present; map = {action_counts:?}"
    );

    (traces.len(), total_steps, action_counts)
}

#[test]
fn replay_all_12_editor_golden_traces() {
    let (traces, total, counts) = replay(BLOB);
    let mut sorted: Vec<(&str, usize)> = counts.iter().map(|(k, v)| (k.as_str(), *v)).collect();
    sorted.sort();
    let expected: Vec<(&str, usize)> = EXPECTED_ACTION_COUNTS
        .iter()
        .map(|(c, n)| (*n, *c))
        .collect();
    assert_eq!(sorted, expected, "action-count map mismatch");
    eprintln!(
        "editor27: replayed {traces} traces / {total} steps; source {EXPECTED_SOURCE_COMMIT}"
    );
}

/// Guard: a corpus whose declared count and row count are truncated *together*
/// (so they agree with each other) must still be rejected by the exact-count
/// guard. Builds such a corpus by copying the real one, dropping 4 trailing
/// traces, and rewriting the declared count to match the surviving rows.
#[test]
fn count_guard_rejects_jointly_truncated_corpus() {
    let top = editor27_fixture::parse(BLOB);
    let top_arr = top.as_list().expect("top array");
    let traces: &[Value] = top_arr[2].as_list().expect("traces array");
    assert_eq!(traces.len(), EXPECTED_TRACES, "precondition");

    // Drop the last 4 traces (12 -> 8) and rewrite the declared count to 8 so
    // declared == rows. The exact-count guard (12) must then reject it.
    let keep = 8usize;
    let kept: Vec<u8> = traces[..keep].iter().flat_map(encode_value).collect();
    let mut corpus: Vec<u8> = Vec::new();
    // Top-level array [source_commit, declared_count, traces]
    corpus.push(6); // array tag
    corpus.extend(3u32.to_le_bytes());
    corpus.extend(encode_value(&top_arr[0]));
    corpus.extend(encode_u32count(keep));
    corpus.push(6); // traces array tag
    corpus.extend((keep as u32).to_le_bytes());
    corpus.extend(kept);

    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| replay(&corpus)));
    match r {
        Ok((t, tot, _)) => {
            panic!("count guard failed to reject truncated corpus: traces={t} total={tot}");
        }
        Err(e) => {
            let s = e
                .downcast_ref::<String>()
                .map(|s| s.as_str())
                .or_else(|| e.downcast_ref::<&str>().copied())
                .unwrap_or("<non-string panic>");
            assert!(
                s.contains("expected exactly 12 traces"),
                "truncation guard fired on the wrong assertion: {s}"
            );
            eprintln!("editor27: truncation guard correctly rejected: {s}");
        }
    }
}

/// Serialize a `Value` back into the tagged binary format (inverse of the
/// decoder), used only to build the truncated guard corpus.
fn encode_value(v: &Value) -> Vec<u8> {
    let mut o = Vec::new();
    fn rec(o: &mut Vec<u8>, v: &Value) {
        match v {
            Value::Null => o.push(0),
            Value::Bool(b) => o.push(if *b { 2 } else { 1 }),
            Value::I128(i) => {
                o.push(5);
                o.extend(i.to_le_bytes());
            }
            Value::Str(s) => {
                let b = s.as_bytes();
                o.push(4);
                o.extend((b.len() as u32).to_le_bytes());
                o.extend(b);
            }
            Value::Array(a) => {
                o.push(6);
                o.extend((a.len() as u32).to_le_bytes());
                for x in a {
                    rec(o, x);
                }
            }
            Value::Map(p) => {
                o.push(7);
                o.extend((p.len() as u32).to_le_bytes());
                for (k, val) in p {
                    rec(o, &Value::Str(k.clone()));
                    rec(o, val);
                }
            }
        }
    }
    rec(&mut o, v);
    o
}

fn encode_u32count(n: usize) -> Vec<u8> {
    let mut o = Vec::new();
    o.push(5);
    o.extend((n as i128).to_le_bytes());
    o
}

/// Dependency-free SHA-256 (RFC 6234), used only to cross-check the frozen
/// corpus binary and the recorded golden-Python / payload hashes.
#[path = "support/golden27_sha256.rs"]
mod sha256;

#[test]
fn binary_sha256_matches_provenance() {
    assert_eq!(
        sha256::hash(BLOB),
        EXPECTED_BINARY_SHA256,
        "embedded corpus binary hash does not match the recorded provenance"
    );
}

/// Dependency-free tie between the provenance record and the constants the
/// replay enforces. No JSON parser is available in this crate and adding one is
/// out of scope, so this is a substring cross-check; the authoritative invariants
/// are the hard guards in `replay()` plus `binary_sha256_matches_provenance`.
#[test]
fn provenance_file_records_the_enforced_values() {
    let p = PROVENANCE;
    let required = [
        EXPECTED_SOURCE_COMMIT,
        EXPECTED_GOLDEN_PYTHON_SHA256,
        EXPECTED_SEMANTIC_PAYLOAD_SHA256,
        EXPECTED_BINARY_SHA256,
        &BLOB.len().to_string(),
        "12",
        "500",
        "6000",
        "518",
        "568",
        "522",
        "582",
        "545",
        "564",
        "503",
        "543",
        "550",
        "537",
    ];
    for n in required {
        assert!(p.contains(n), "provenance file missing expected value: {n}");
    }
}
