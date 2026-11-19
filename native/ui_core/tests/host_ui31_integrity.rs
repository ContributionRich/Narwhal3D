//! UI-DATA-31: integrity guard for the pre-retirement UI acceptance DATA.
//!
//! This test pins the exact raw JSON capture (2,542,173 bytes) by SHA-256
//! in this source file, independently of the provenance file. The joint
//! data+provenance guard rejects a same-size data modification even when
//! the provenance hash is rewritten to match the modified data.
//!
//! `layout_algorithm.rs` is TEST-ONLY (`#[cfg(test)]`); this test does NOT
//! claim it is production code.
//!
//! Source capture commit: 50267dd6d4504b14052d1ab7e54675f3c8750637
//! Extraction revision:   3d72de5f6653d89edfd024737a3cfafa23ef5537
//!
//! Data:       `data/host_ui31.json`
//! Provenance: `data/host_ui31.provenance.json`

#[path = "support/golden27_sha256.rs"]
mod golden27_sha256;

use serde_json::Value;
use std::fs;

const DATA_PATH: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/data/host_ui31.json");
const PROV_PATH: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/tests/data/host_ui31.provenance.json"
);

// --- Independently pinned in this source (NOT in the provenance file) ---
const PINNED_DATA_SHA256: &str = "32c845272f9ee20e012aeaadea9ec9bc8b6e21ca8ad2e9b16a9a4afbbf37faa1";
const PINNED_DATA_LEN: usize = 2_542_173;
const PINNED_PROV_SHA256: &str = "eed060d6153345f448087fe844db8d4b21be9148a58c6c9a7a64e783e423448e";
const PINNED_PROV_LEN: usize = 4_874;
const PINNED_SOURCE_COMMIT: &str = "50267dd6d4504b14052d1ab7e54675f3c8750637";
const PINNED_EXTRACTION: &str = "3d72de5f6653d89edfd024737a3cfafa23ef5537";

fn data_bytes() -> Vec<u8> {
    fs::read(DATA_PATH).expect("read data file")
}
fn prov_bytes() -> Vec<u8> {
    fs::read(PROV_PATH).expect("read prov file")
}
fn data_json() -> Value {
    serde_json::from_slice(&data_bytes()).expect("parse data JSON")
}
fn prov_json() -> Value {
    serde_json::from_slice(&prov_bytes()).expect("parse prov JSON")
}

// 1. Data file: SHA + length pinned in source.
#[test]
fn data_bytes_pinned() {
    let b = data_bytes();
    assert_eq!(b.len(), PINNED_DATA_LEN, "data length mismatch");
    let sha = golden27_sha256::hash(&b);
    assert_eq!(sha, PINNED_DATA_SHA256, "data SHA-256 mismatch");
}

// 2. Provenance file: SHA + length pinned in source.
#[test]
fn prov_bytes_pinned() {
    let b = prov_bytes();
    assert_eq!(b.len(), PINNED_PROV_LEN, "prov length mismatch");
    let sha = golden27_sha256::hash(&b);
    assert_eq!(sha, PINNED_PROV_SHA256, "prov SHA-256 mismatch");
}

// 3. Provenance cross-references the data SHA and length.
#[test]
fn prov_cross_references_data() {
    let prov = prov_json();
    let raw_sha = prov["raw_json"]["sha256"]
        .as_str()
        .expect("raw_json.sha256");
    assert_eq!(
        raw_sha, PINNED_DATA_SHA256,
        "prov raw_json.sha256 != pinned data SHA"
    );
    let raw_len = prov["raw_json"]["length"]
        .as_u64()
        .expect("raw_json.length");
    assert_eq!(
        raw_len as usize, PINNED_DATA_LEN,
        "prov raw_json.length != pinned data len"
    );
}

// 4. Source capture commit pinned in both data and provenance.
#[test]
fn source_commit_pinned() {
    let data = data_json();
    let sc = data["source_commit"].as_str().expect("data.source_commit");
    assert_eq!(sc, PINNED_SOURCE_COMMIT, "data source_commit mismatch");
    let prov = prov_json();
    let psc = prov["source_commit"].as_str().expect("prov.source_commit");
    assert_eq!(psc, PINNED_SOURCE_COMMIT, "prov source_commit mismatch");
}

// 5. Extraction revision pinned in provenance.
#[test]
fn extraction_revision_pinned() {
    let prov = prov_json();
    let er = prov["extraction_revision"]
        .as_str()
        .expect("prov.extraction_revision");
    assert_eq!(er, PINNED_EXTRACTION, "extraction_revision mismatch");
}

// 6. Top-level keys and shapes.
#[test]
fn top_keys_and_shapes() {
    let data = data_json();
    let obj = data.as_object().expect("data is an object");
    assert_eq!(obj.len(), 6, "expected 6 top-level keys, got {}", obj.len());
    for k in [
        "source_commit",
        "layout",
        "popup_metrics",
        "editor_adapter",
        "unicode_adapter",
        "popup_adapter",
    ] {
        assert!(obj.contains_key(k), "missing top-level key: {k}");
    }
    assert!(data["source_commit"].is_string());
    let layout = data["layout"].as_array().expect("layout is a list");
    assert_eq!(layout.len(), 120);
    let pm = data["popup_metrics"]
        .as_object()
        .expect("popup_metrics is a dict");
    assert_eq!(pm.len(), 4);
    for k in ["rows", "initial", "calls", "steps"] {
        assert!(pm.contains_key(k), "popup_metrics missing: {k}");
    }
    let ea = data["editor_adapter"]
        .as_array()
        .expect("editor_adapter is a list");
    assert_eq!(ea.len(), 1);
    let ea0 = ea[0].as_object().expect("editor_adapter[0] is a dict");
    for k in ["args", "initial", "steps"] {
        assert!(ea0.contains_key(k), "editor_adapter[0] missing: {k}");
    }
    let ua = data["unicode_adapter"]
        .as_object()
        .expect("unicode_adapter is a dict");
    assert_eq!(ua.len(), 2);
    assert!(ua.contains_key("15.1.0"));
    assert!(ua.contains_key("16.0.0"));
    for (ver, v) in ua {
        let s = v.as_str().expect("unicode value is a string");
        assert_eq!(s.len(), 64, "{ver} digest length != 64");
        assert!(
            s.bytes().all(|c| c.is_ascii_hexdigit()),
            "{ver} digest not hex"
        );
    }
    let pa = data["popup_adapter"]
        .as_object()
        .expect("popup_adapter is a dict");
    assert_eq!(pa.len(), 2);
    assert!(pa.contains_key("rows"));
    assert!(pa.contains_key("steps"));
}

// 7. Layout: 120 entries, each with the expected 7 keys.
#[test]
fn layout_120_entries_shape() {
    let data = data_json();
    let layout = data["layout"].as_array().expect("layout is a list");
    assert_eq!(layout.len(), 120);
    for (i, entry) in layout.iter().enumerate() {
        let obj = entry
            .as_object()
            .unwrap_or_else(|| panic!("layout[{i}] is not a dict"));
        for k in [
            "calls", "fail", "helpers", "result", "stateful", "text", "width",
        ] {
            assert!(obj.contains_key(k), "layout[{i}] missing: {k}");
        }
        assert!(obj["text"].is_string(), "layout[{i}].text not str");
        assert!(
            obj["stateful"].is_boolean(),
            "layout[{i}].stateful not bool"
        );
        assert!(obj["result"].is_array(), "layout[{i}].result not list");
        assert!(obj["calls"].is_array(), "layout[{i}].calls not list");
        assert!(obj["helpers"].is_array(), "layout[{i}].helpers not list");
    }
}

// 8. Popup adapter: 900 steps x expected keys, 62 rows.
#[test]
fn popup_900_steps_shape() {
    let data = data_json();
    let pa = data["popup_adapter"]
        .as_object()
        .expect("popup_adapter is a dict");
    let rows = pa["rows"].as_array().expect("popup_adapter.rows is a list");
    assert_eq!(rows.len(), 62);
    let steps = pa["steps"]
        .as_array()
        .expect("popup_adapter.steps is a list");
    assert_eq!(steps.len(), 900);
    for (i, step) in steps.iter().enumerate() {
        let obj = step
            .as_object()
            .unwrap_or_else(|| panic!("popup step[{i}] is not a dict"));
        for k in ["event", "consumed", "state", "calls", "hit"] {
            assert!(obj.contains_key(k), "popup step[{i}] missing: {k}");
        }
        let event = obj["event"].as_array().expect("step[{i}].event is a list");
        assert_eq!(event.len(), 3);
        assert!(event[0].is_string(), "step[{i}].event[0] not str");
        assert!(event[1].is_string(), "step[{i}].event[1] not str");
        let coords = event[2].as_array().expect("step[{i}].event[2] is a list");
        assert_eq!(coords.len(), 2);
        for c in coords {
            assert!(c.is_number(), "step[{i}].event[2] coord not number");
        }
        assert!(obj["consumed"].is_boolean(), "step[{i}].consumed not bool");
        let state = obj["state"].as_object().expect("step[{i}].state is a dict");
        for k in ["is_open", "releases", "panels"] {
            assert!(state.contains_key(k), "step[{i}].state missing: {k}");
        }
        assert!(state["is_open"].is_boolean());
        assert!(state["releases"].is_array());
        assert!(state["panels"].is_array());
        let calls = obj["calls"].as_array().expect("step[{i}].calls is a list");
        for pair in calls {
            let pl = pair.as_array().expect("call pair is a list");
            assert_eq!(pl.len(), 2);
            assert!(
                pl[0].is_i64() || pl[0].is_u64(),
                "step[{i}] call pair[0] not int"
            );
            assert!(pl[1].is_null(), "step[{i}] call pair[1] not null");
        }
        let hit = &obj["hit"];
        if !hit.is_null() {
            let hl = hit.as_array().expect("step[{i}].hit is a list");
            assert_eq!(hl.len(), 2);
            assert!(hl[0].is_i64() || hl[0].is_u64(), "step[{i}].hit[0] not int");
            assert!(
                hl[1].is_null() || hl[1].is_string() || hl[1].is_i64() || hl[1].is_u64(),
                "step[{i}].hit[1] unexpected type"
            );
        }
    }
}

// 9. Editor adapter: 1 trace, 59 steps, each [action, args, result[2], after[5]].
#[test]
fn editor_adapter_shape() {
    let data = data_json();
    let ea = data["editor_adapter"]
        .as_array()
        .expect("editor_adapter is a list");
    assert_eq!(ea.len(), 1);
    let ea0 = ea[0].as_object().expect("editor_adapter[0] is a dict");
    assert_eq!(ea0["args"].as_array().expect("args is a list").len(), 3);
    assert_eq!(
        ea0["initial"].as_array().expect("initial is a list").len(),
        5
    );
    let steps = ea0["steps"].as_array().expect("steps is a list");
    assert_eq!(steps.len(), 59);
    for (i, step) in steps.iter().enumerate() {
        let sl = step
            .as_array()
            .unwrap_or_else(|| panic!("editor step[{i}] is not a list"));
        assert_eq!(sl.len(), 4, "editor step[{i}] length != 4");
        assert!(sl[0].is_string(), "step[{i}][0] not str");
        assert!(sl[1].is_array(), "step[{i}][1] not list");
        let result = sl[2].as_array().expect("step[{i}][2] is a list");
        assert_eq!(result.len(), 2);
        assert!(result[0].is_string(), "step[{i}].result[0] not str");
        let after = sl[3].as_array().expect("step[{i}][3] is a list");
        assert_eq!(after.len(), 5);
    }
}

// 10. Popup metrics: 4 keys with expected sub-shapes.
#[test]
fn popup_metrics_shape() {
    let data = data_json();
    let pm = data["popup_metrics"]
        .as_object()
        .expect("popup_metrics is a dict");
    assert_eq!(pm["rows"].as_array().expect("rows is a list").len(), 3);
    let initial = pm["initial"].as_object().expect("initial is a dict");
    for k in ["is_open", "releases", "panels"] {
        assert!(
            initial.contains_key(k),
            "popup_metrics.initial missing: {k}"
        );
    }
    let calls = pm["calls"].as_array().expect("calls is a list");
    assert_eq!(calls.len(), 7);
    for pair in calls {
        let pl = pair.as_array().expect("calls pair is a list");
        assert_eq!(pl.len(), 2);
    }
    let steps = pm["steps"].as_array().expect("steps is a list");
    assert_eq!(steps.len(), 4);
    for step in steps {
        let obj = step.as_object().expect("steps entry is a dict");
        for k in ["key", "state", "calls"] {
            assert!(obj.contains_key(k), "popup_metrics.steps missing: {k}");
        }
    }
}

// 11. Joint data+provenance mutation guard (security).
//
// Attack vectors, all rejected:
//   A: modify data (same length), prov unchanged -> data SHA guard fires.
//   B: modify data (same length) AND rewrite prov.raw_json.sha256 to match
//      -> prov file-level SHA guard fires (pinned in Rust source, not prov).
//   C: modify prov only (e.g. source_commit) -> prov file-level SHA guard fires.
#[test]
fn joint_mutation_rejected() {
    let data = data_bytes();
    let orig_len = data.len();
    let prov_b = prov_bytes();
    let prov_str = std::str::from_utf8(&prov_b).expect("prov is utf-8");

    // A: flip one byte in the data (preserves length).
    let mut modified = data.clone();
    let pos = orig_len / 2;
    modified[pos] ^= 0x01;
    assert_eq!(modified.len(), orig_len);
    let modified_sha = golden27_sha256::hash(&modified);
    assert_ne!(
        modified_sha, PINNED_DATA_SHA256,
        "GUARD A FAILED: modified data SHA matches pinned"
    );

    // B: rewrite prov.raw_json.sha256 to the modified data SHA, re-serialize.
    let mut prov_val: Value = serde_json::from_str(prov_str).expect("parse prov");
    prov_val["raw_json"]["sha256"] = Value::String(modified_sha.clone());
    let rewritten = serde_json::to_string(&prov_val).expect("serialize");
    let rewritten_sha = golden27_sha256::hash(rewritten.as_bytes());
    assert_ne!(
        rewritten_sha, PINNED_PROV_SHA256,
        "GUARD B FAILED: rewritten prov SHA matches pinned"
    );

    // C: tamper prov.source_commit.
    let mut prov_val2: Value = serde_json::from_str(prov_str).expect("parse prov");
    prov_val2["source_commit"] = Value::String("0000000000000000000000000000000000000000".into());
    let tampered = serde_json::to_string(&prov_val2).expect("serialize");
    let tampered_sha = golden27_sha256::hash(tampered.as_bytes());
    assert_ne!(
        tampered_sha, PINNED_PROV_SHA256,
        "GUARD C FAILED: tampered prov SHA matches pinned"
    );
}

// 12. Provenance counts match the data (cross-consistency).
#[test]
fn prov_counts_match_data() {
    let prov = prov_json();
    let data = data_json();
    let pairs: &[(&str, &str, u64)] = &[
        (
            "schema.layout.count",
            "layout",
            data["layout"].as_array().expect("layout").len() as u64,
        ),
        (
            "schema.popup_adapter.steps",
            "popup_adapter.steps",
            data["popup_adapter"]["steps"]
                .as_array()
                .expect("popup steps")
                .len() as u64,
        ),
        (
            "schema.popup_adapter.rows",
            "popup_adapter.rows",
            data["popup_adapter"]["rows"]
                .as_array()
                .expect("popup rows")
                .len() as u64,
        ),
        (
            "schema.editor_adapter.steps_len",
            "editor_adapter[0].steps",
            data["editor_adapter"][0]["steps"]
                .as_array()
                .expect("editor steps")
                .len() as u64,
        ),
    ];
    for (prov_path, data_label, expected) in pairs {
        let mut cur = &prov;
        for part in prov_path.split('.') {
            cur = &cur[part];
        }
        let actual = cur
            .as_u64()
            .unwrap_or_else(|| panic!("prov {prov_path} is not a u64"));
        assert_eq!(actual, *expected, "{data_label} count mismatch");
    }
}
