//! HISTORY-TRACE-30 golden trace (acceptance-retirement, not a new algorithm).
//!
//! The production history engine already lives in Rust (`src/history.rs`); the
//! Python `command_history` facade remains the host's opaque-payload adapter.
//! This replays the frozen 350-step pre-retirement lifecycle (origin capture
//! `50267dd6`, extracted from published HEAD `60891bcf`) through the REQUIRED
//! native history ABI and asserts every record field, stack ordering, serial,
//! depth, enabled, and the facade-derived booleans (can_undo/can_redo/
//! recording/captured/current/top/name ordering). The frozen rows are ORIGINAL
//! data (not native output); see `tests/data/history30_trace.provenance.json`.
//!
//! Faithful call mapping (see `native_history.py` facade):
//!   begin(name)    -> "begin"      name = str(name or "Change"); result = serial
//!   capture(p)     -> "capture"    payload = arena id (or null); result = captured
//!   end()          -> "end"                        result = committed
//!   abandon()      -> "abandon"
//!   enabled = v    -> "enable"     value = v
//!   clear()        -> "clear"
//!   undo/redo(p)   -> "prepare_nav"(direction,current) then "commit_nav"(ticket)
//!   record fields  -> "record" / "records" / "names" (pure reads, no state change)
//!
//! The facade keeps the 5 logical payloads alive for the whole trace (arena
//! strong refs), so pre-registering them and skipping its garbage drain leaves
//! every asserted field identical; only internal refcount/garbage bookkeeping
//! differs, and the trace records none of that.
use narwhal_application_core::{narwhal_history_abi, narwhal_history_json};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::path::Path;

const DATA: &str = "tests/data/history30_trace.json";
const PROV: &str = "tests/data/history30_trace.provenance.json";
const CAPACITY: usize = 1 << 20; // generous: every mutation or read reply fits.

// INDEPENDENT code-pinned expectations. These live in the TEST SOURCE, not in the
// (co-modifiable) provenance file, so a data + provenance pair rewritten together
// still fails here. They are the single source of truth the integrity checker
// trusts; provenance is cross-checked against them, never the other way round.
const EXPECTED_ROWS: usize = 350;
const EXPECTED_BYTES: usize = 15588;
const EXPECTED_DATA_SHA256: &str =
    "18b505bed8d133d1fd713a429c357e555b3db41eebbbc6fb5500aa6622e040c2";
const EXPECTED_ORIGIN_CAPTURE: &str = "50267dd6";
const EXPECTED_EXTRACTED_FROM_HEAD: &str = "60891bcf";

fn resolve(p: &str) -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join(p)
}
fn read(p: &str) -> (Vec<u8>, Value) {
    let full = resolve(p);
    let bytes = std::fs::read(&full).unwrap_or_else(|e| panic!("read {}: {e}", full.display()));
    let value = serde_json::from_slice::<Value>(&bytes).expect("valid JSON");
    (bytes, value)
}
fn call(input: &[u8]) -> Value {
    let mut out = vec![0u8; CAPACITY];
    let n =
        unsafe { narwhal_history_json(input.as_ptr(), input.len(), out.as_mut_ptr(), CAPACITY) };
    assert!(n > 0, "history call returned {n}");
    assert!(n as usize <= CAPACITY, "reply {n} exceeded capacity");
    serde_json::from_slice::<Value>(&out[..n as usize]).expect("valid reply")
}

// Test-local SHA-256 (the crate has no SHA dependency); used only to verify the
// frozen data bytes, never production state. Validated against the empty-input
// test vector in the provenance test before it is trusted.
const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];
fn sha256_hex(data: &[u8]) -> String {
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    let bitlen = (data.len() as u64).wrapping_mul(8);
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    for b in bitlen.to_be_bytes() {
        msg.push(b);
    }
    for chunk in msg.chunks(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                chunk[4 * i],
                chunk[4 * i + 1],
                chunk[4 * i + 2],
                chunk[4 * i + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh) =
            (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]);
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let t1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
        h[5] = h[5].wrapping_add(f);
        h[6] = h[6].wrapping_add(g);
        h[7] = h[7].wrapping_add(hh);
    }
    let mut s = String::with_capacity(64);
    for x in h {
        s.push_str(&format!("{:08x}", x));
    }
    s
}

/// Validate the test-local SHA-256 against the empty-input, single-block ("abc"/"ABC")
/// and the 56-char multi-block FIPS vectors before it is trusted to pin the frozen
/// data bytes. The multi-block vector exercises the chunk loop the single-block ones skip.
fn verify_sha_vectors() {
    assert_eq!(
        sha256_hex(b""),
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "empty-input vector"
    );
    assert_eq!(
        sha256_hex(b"abc"),
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "standard FIPS-180 'abc' vector (single block)"
    );
    assert_eq!(
        sha256_hex(b"ABC"),
        "b5d4045c3f466fa91fe2cc6abe79232a1a57cdf104f7a26e716e0a1e2789df78",
        "'ABC' vector (single block, case sensitivity)"
    );
    assert_eq!(
        sha256_hex(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
        "56-char multi-block vector (exercises the chunk loop)"
    );
}

/// Single source of truth for fixture integrity. BOTH tests call this, so running
/// `history30_golden_trace` alone cannot bypass the hash: the data bytes are pinned
/// against the INDEPENDENT code constants (not the co-modifiable provenance file),
/// and provenance is cross-checked against them. The SHA-256 implementation is
/// validated against the standard vectors before it is used to pin the bytes.
fn verify_integrity() {
    verify_sha_vectors();
    let (bytes, data) = read(DATA);
    let prov = read(PROV).1;
    let rows = data.as_array().unwrap();
    assert_eq!(
        rows.len(),
        EXPECTED_ROWS,
        "trace must hold exactly 350 frozen rows"
    );
    assert_eq!(
        bytes.len(),
        EXPECTED_BYTES,
        "data byte count must match the code-pinned 15588"
    );
    assert_eq!(
        sha256_hex(&bytes),
        EXPECTED_DATA_SHA256,
        "data bytes must match the INDEPENDENT code-pinned SHA-256 (a data+provenance pair \
         rewritten together still fails here)"
    );
    // Provenance must agree with the independent constants (and the actual bytes).
    assert_eq!(
        prov["sha256_data"].as_str().unwrap(),
        EXPECTED_DATA_SHA256,
        "provenance sha256_data must equal the code-pinned value"
    );
    assert_eq!(
        prov["bytes_data"].as_u64().unwrap() as usize,
        EXPECTED_BYTES,
        "provenance bytes_data must equal the code-pinned value"
    );
    assert_eq!(prov["rows"].as_u64().unwrap() as usize, EXPECTED_ROWS);
    // Pin the exact capture / extraction revisions (not just "present").
    assert_eq!(
        prov["extraction"]["origin_capture"].as_str().unwrap(),
        EXPECTED_ORIGIN_CAPTURE,
        "origin_capture must be exactly 50267dd6"
    );
    assert_eq!(
        prov["extraction"]["extracted_from_head"].as_str().unwrap(),
        EXPECTED_EXTRACTED_FROM_HEAD,
        "extracted_from_head must be exactly 60891bcf"
    );
    let mut hist: std::collections::BTreeMap<String, u64> = std::collections::BTreeMap::new();
    for r in rows {
        *hist.entry(r[0].as_str().unwrap().to_string()).or_insert(0) += 1;
    }
    assert_eq!(hist.len(), 8, "the trace uses exactly eight ops");
    for (op, n) in &hist {
        assert_eq!(
            prov["op_histogram"][op].as_u64().unwrap(),
            *n,
            "op {op} count"
        );
    }
    assert_eq!(*rows.first().unwrap(), prov["head_row"].clone());
    assert_eq!(*rows.last().unwrap(), prov["tail_row"].clone());
}

/// Frozen record row `[serial, name, before_idx, after_idx]`.
fn frozen(row: &Value) -> (u64, String, u64, u64) {
    (
        row[0].as_u64().unwrap(),
        row[1].as_str().unwrap().to_string(),
        row[2].as_u64().unwrap(),
        row[3].as_u64().unwrap(),
    )
}

/// Host facade does `str(name or "Change")`: null / empty-string / zero / false
/// are falsy -> "Change"; a string passes through; an int stringifies (42 -> "42").
fn begin_name(arg: &Value) -> String {
    match arg {
        Value::Null | Value::Bool(false) => "Change".to_string(),
        Value::String(s) => {
            if s.is_empty() {
                "Change".to_string()
            } else {
                s.clone()
            }
        }
        Value::Number(n) => {
            if n.as_i64() == Some(0) {
                "Change".to_string()
            } else {
                n.to_string()
            }
        }
        _ => "Change".to_string(), // defensive: the trace never begins with these
    }
}

struct H {
    id: u64,
    payloads: Vec<Option<u64>>, // index 0 = None; 1..=4 registered logical payloads
    index_of: HashMap<u64, u64>, // native payload id -> frozen logical index
}

impl H {
    fn new(limit: u64) -> Self {
        let r = call(&serde_json::to_vec(&json!({"op": "create", "limit": limit})).unwrap());
        assert_eq!(r["status"], "ok", "{r}");
        let id = r["result"]["history"].as_u64().unwrap();
        let info = &r["info"];
        assert_eq!(info["depth"], 0);
        assert_eq!(info["undo_count"], 0);
        assert_eq!(info["redo_count"], 0);
        assert!(info["open_record"].is_null());
        assert_eq!(info["enabled"], true);
        assert_eq!(info["next_serial"], 1);
        let mut h = H {
            id,
            payloads: vec![None],
            index_of: HashMap::new(),
        };
        for _ in 1..=4 {
            let p = call(
                &serde_json::to_vec(&json!({
                    "op": "register_payload",
                    "history": id
                }))
                .unwrap(),
            );
            let pid = p["result"]["payload"].as_u64().unwrap();
            h.index_of.insert(pid, h.payloads.len() as u64);
            h.payloads.push(Some(pid));
        }
        h
    }
    fn op(&self, v: Value) -> Value {
        let mut req = v;
        req["history"] = Value::from(self.id);
        let r = call(&serde_json::to_vec(&req).unwrap());
        assert_eq!(r["status"], "ok", "{r}");
        r
    }
    fn idx(&self, i: u64) -> Option<u64> {
        assert!(i <= 4, "payload index {i} out of trace domain");
        self.payloads.get(i as usize).copied().flatten()
    }
    fn begin(&self, name: &str) -> u64 {
        self.op(json!({"op": "begin", "name": name}))["result"]["serial"]
            .as_u64()
            .unwrap()
    }
    fn capture(&self, i: u64) -> bool {
        self.op(json!({"op": "capture", "payload": self.idx(i)}))["result"]["captured"]
            .as_bool()
            .unwrap()
    }
    fn end(&self) -> bool {
        self.op(json!({"op": "end"}))["result"]["committed"]
            .as_bool()
            .unwrap()
    }
    fn abandon(&self) {
        self.op(json!({"op": "abandon"}));
    }
    fn enable(&self, value: bool) {
        self.op(json!({"op": "enable", "value": value}));
    }
    fn clear(&self) {
        self.op(json!({"op": "clear"}));
    }
    fn nav(&self, dir: &str, i: u64) -> Option<u64> {
        let prep = self.op(json!({"op": "prepare_nav", "direction": dir, "current": self.idx(i)}));
        if prep["result"].is_null() {
            return None;
        }
        let result = prep["result"].clone();
        let ticket = result["ticket"].as_u64().unwrap();
        let rec = result["record"].as_u64().unwrap();
        let c = self.op(json!({"op": "commit_nav", "ticket": ticket}));
        assert_eq!(c["result"]["record"].as_u64(), Some(rec), "{c}");
        Some(rec)
    }
    fn record_ids(&self, side: &str) -> Vec<u64> {
        self.op(json!({"op": "records", "side": side}))["result"]["records"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_u64().unwrap())
            .collect()
    }
    fn names(&self, side: &str) -> Vec<String> {
        self.op(json!({"op": "names", "side": side}))["result"]["names"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect()
    }
    fn record_fields(&self, id: u64) -> (u64, String, u64, u64) {
        let r = self.op(json!({"op": "record", "record": id}))["result"].clone();
        (
            r["serial"].as_u64().unwrap(),
            r["name"].as_str().unwrap().to_string(),
            self.payload_index(&r["before"]),
            self.payload_index(&r["after"]),
        )
    }
    fn payload_index(&self, v: &Value) -> u64 {
        match v.as_u64() {
            None => 0,
            Some(id) => self
                .index_of
                .get(&id)
                .copied()
                .expect("unknown payload id in record"),
        }
    }
    fn close(&self) {
        let _ = call(&serde_json::to_vec(&json!({"op": "close", "history": self.id})).unwrap());
    }
}

#[test]
fn history30_provenance_and_integrity() {
    verify_integrity();
}

#[test]
fn history30_golden_trace() {
    assert_eq!(narwhal_history_abi(), 1);
    // Enforce fixture integrity (including the independent data-hash pin) here too,
    // so running ONLY this test cannot bypass the hash.
    verify_integrity();
    let data = read(DATA).1.as_array().unwrap().clone();
    assert_eq!(data.len(), 350);
    let h = H::new(3); // seed-187, limit-3 lifecycle
    let mut nav_exercised = false;
    for (step, row) in data.iter().enumerate() {
        let op = row[0].as_str().unwrap();
        let arg = &row[1];
        let expected_result = &row[2];
        let undo = row[3].as_array().unwrap();
        let redo = row[4].as_array().unwrap();
        let open = &row[5];
        let depth = row[6].as_u64().unwrap();
        let next_serial = row[7].as_u64().unwrap();
        let enabled = row[8].as_bool().unwrap();

        let result_ok = match op {
            "begin" => h.begin(&begin_name(arg)) == expected_result.as_u64().unwrap(),
            "capture" => h.capture(arg.as_u64().unwrap()) == expected_result.as_bool().unwrap(),
            "end" => h.end() == expected_result.as_bool().unwrap(),
            "abandon" | "enable" | "clear" => {
                match op {
                    "abandon" => h.abandon(),
                    "enable" => h.enable(arg.as_bool().unwrap()),
                    _ => h.clear(),
                }
                expected_result.is_null()
            }
            "undo" | "redo" => match h.nav(op, arg.as_u64().unwrap()) {
                None => expected_result.is_null(),
                Some(id) => {
                    nav_exercised = true;
                    h.record_fields(id) == frozen(expected_result)
                }
            },
            other => panic!("unexpected op {other}"),
        };
        assert!(
            result_ok,
            "step {step}: op {op} result mismatch (arg={arg})"
        );

        // The op's reply carries the authoritative post-op header; a dedicated
        // `info` read is a pure, state-preserving snapshot of the same header.
        let info = h.op(json!({"op": "info"}))["info"].clone();
        assert_eq!(
            info["undo_count"].as_u64().unwrap() as usize,
            undo.len(),
            "step {step} undo_count"
        );
        assert_eq!(
            info["redo_count"].as_u64().unwrap() as usize,
            redo.len(),
            "step {step} redo_count"
        );
        assert_eq!(info["depth"].as_u64().unwrap(), depth, "step {step} depth");
        assert_eq!(
            info["next_serial"].as_u64().unwrap(),
            next_serial,
            "step {step} next_serial"
        );
        assert_eq!(
            info["enabled"].as_bool().unwrap(),
            enabled,
            "step {step} enabled"
        );

        // Open record: presence + full fields.
        let open_present = !info["open_record"].is_null();
        assert_eq!(open_present, !open.is_null(), "step {step} open presence");
        if open_present {
            let (s, n, b, a) = h.record_fields(info["open_record"].as_u64().unwrap());
            let (fs, fn_, fb, fa) = frozen(open);
            assert_eq!((s, n, b, a), (fs, fn_, fb, fa), "step {step} open record");
        }

        // Undo + redo stacks: ordering + every record field.
        let u_ids = h.record_ids("undo");
        assert_eq!(u_ids.len(), undo.len(), "step {step} undo ids");
        for (i, (id, row)) in u_ids.iter().zip(undo.iter()).enumerate() {
            assert_eq!(h.record_fields(*id), frozen(row), "step {step} undo[{i}]");
        }
        let r_ids = h.record_ids("redo");
        assert_eq!(r_ids.len(), redo.len(), "step {step} redo ids");
        for (i, (id, row)) in r_ids.iter().zip(redo.iter()).enumerate() {
            assert_eq!(h.record_fields(*id), frozen(row), "step {step} redo[{i}]");
        }

        // Facade-derived booleans + name ordering.
        assert_eq!(
            info["undo_count"].as_u64().unwrap() > 0,
            !undo.is_empty(),
            "step {step} can_undo"
        );
        assert_eq!(
            info["redo_count"].as_u64().unwrap() > 0,
            !redo.is_empty(),
            "step {step} can_redo"
        );
        assert_eq!(open_present, !open.is_null(), "step {step} recording");
        let expected_captured = open_present && open[2].as_u64().unwrap() != 0;
        assert_eq!(
            info["captured"].as_bool().unwrap(),
            expected_captured,
            "step {step} captured"
        );
        assert_eq!(
            info["current_serial"].as_u64().unwrap(),
            if open_present {
                open[0].as_u64().unwrap()
            } else {
                0
            },
            "step {step} current_serial"
        );
        assert_eq!(
            info["top_serial"].as_u64().unwrap(),
            if undo.is_empty() {
                0
            } else {
                undo[undo.len() - 1][0].as_u64().unwrap()
            },
            "step {step} top_serial"
        );
        let expect_undo_names: Vec<String> = undo
            .iter()
            .rev()
            .map(|r| r[1].as_str().unwrap().to_string())
            .collect();
        assert_eq!(h.names("undo"), expect_undo_names, "step {step} undo_names");
        let expect_redo_names: Vec<String> = redo
            .iter()
            .rev()
            .map(|r| r[1].as_str().unwrap().to_string())
            .collect();
        assert_eq!(h.names("redo"), expect_redo_names, "step {step} redo_names");
        let expect_undo_name: Option<&str> = undo.last().map(|r| r[1].as_str().unwrap());
        let actual_undo_name: Option<String> = if u_ids.is_empty() {
            None
        } else {
            Some(h.record_fields(u_ids[u_ids.len() - 1]).1)
        };
        assert_eq!(
            actual_undo_name.as_deref(),
            expect_undo_name,
            "step {step} undo_name"
        );
        let expect_redo_name: Option<&str> = redo.last().map(|r| r[1].as_str().unwrap());
        let actual_redo_name: Option<String> = if r_ids.is_empty() {
            None
        } else {
            Some(h.record_fields(r_ids[r_ids.len() - 1]).1)
        };
        assert_eq!(
            actual_redo_name.as_deref(),
            expect_redo_name,
            "step {step} redo_name"
        );
    }
    assert!(
        nav_exercised,
        "the trace must exercise at least one undo/redo navigation"
    );
    h.close();
}
