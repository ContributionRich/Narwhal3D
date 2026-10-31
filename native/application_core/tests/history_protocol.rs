use narwhal_application_core::{
    MAX_HISTORY_REQUEST_BYTES, MAX_OUTPUT_CAPACITY, narwhal_history_abi, narwhal_history_json,
};
use serde_json::{Value, json};
use std::sync::{Arc, Barrier};

#[test]
fn history_every_mutation_preflights_before_consuming_resources() {
    let h = H::new(2);
    let p = h.payload();
    let unused = h.payload();
    let r = h.step("capacity", &p);
    let n = h.prepare("undo", &p);
    let before = h.info();
    let fields = h.record(&r);
    for request in [
        json!({"op":"close"}),
        json!({"op":"register_payload"}),
        json!({"op":"drop_payload","payload":unused}),
        json!({"op":"begin","name":"no replay"}),
        json!({"op":"capture","payload":p}),
        json!({"op":"end"}),
        json!({"op":"abandon"}),
        json!({"op":"enable","value":false}),
        json!({"op":"retain_record","record":r}),
        json!({"op":"release_record","record":r}),
        json!({"op":"set_before","record":r,"payload":null}),
        json!({"op":"set_after","record":r,"payload":p}),
        json!({"op":"set_name","record":r,"name":"wrong"}),
        json!({"op":"prepare_nav","direction":"redo","current":p}),
        json!({"op":"commit_nav","ticket":n["ticket"]}),
        json!({"op":"abort_nav","ticket":n["ticket"],"unknown":true}),
        json!({"op":"clear"}),
        json!({"op":"ack_garbage","payloads":[p]}),
    ] {
        let input = serde_json::to_vec(&h.req(request)).unwrap();
        let (n, out) = raw(&input, 4095);
        assert_eq!(
            serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["code"],
            "output_too_small"
        );
        assert_eq!(h.info(), before);
        assert_eq!(h.record(&r), fields);
    }
    h.ok(json!({"op":"commit_nav","ticket":n["ticket"]}));
    h.err(
        json!({"op":"release_record","record":r}),
        "record_not_retained",
    );
    h.ok(json!({"op":"drop_payload","payload":unused}));
}

#[test]
fn history_stack_order_and_redo_pruning_with_retained_views() {
    let h = H::new(3);
    let p = h.payload();
    let a = h.step("a", &p);
    let b = h.step("b", &p);
    let c = h.step("c", &p);
    assert_eq!(h.records("undo"), json!([a, b, c]));
    h.ok(json!({"op":"retain_record","record":c}));
    h.nav("undo", &p);
    h.nav("undo", &p);
    assert_eq!(h.records("undo"), json!([a]));
    assert_eq!(h.records("redo"), json!([c, b]));
    assert_eq!(h.info()["redo_record"], b);
    let d = h.step("branch", &p);
    assert_eq!(h.records("undo"), json!([a, d]));
    assert_eq!(h.records("redo"), json!([]));
    h.err(json!({"op":"record","record":b}), "invalid_record");
    assert_eq!(h.record(&c)["name"], "c");
    assert_eq!(h.info()["record_count"], 3);
    h.ok(json!({"op":"release_record","record":c}));
    assert_eq!(h.info()["record_count"], 2);
}

#[test]
fn history_names_order_empty_and_retained_scope() {
    let h = H::new(2);
    let names = |side: &str, expected: Value| {
        let before = h.info();
        assert_eq!(
            h.ok(json!({"op":"names","side":side})),
            json!({"status":"ok","result":{"names":expected},"info":before})
        );
        assert_eq!(h.info(), before);
    };
    names("undo", json!([]));
    names("redo", json!([]));
    let p = h.payload();
    let a = h.step("pruned", &p);
    h.ok(json!({"op":"retain_record","record":a}));
    h.step("older", &p);
    let c = h.step("newest", &p);
    assert_eq!(h.record(&a)["name"], "pruned");
    names("undo", json!(["newest", "older"]));
    names("redo", json!([]));
    h.begin("open record is not a stack entry");
    names("undo", json!(["newest", "older"]));
    h.ok(json!({"op":"abandon"}));
    h.ok(json!({"op":"set_name","record":c,"name":"renamed"}));
    names("undo", json!(["renamed", "older"]));
    let nav = h.prepare("undo", &p);
    names("undo", json!(["renamed", "older"]));
    names("redo", json!([]));
    h.ok(json!({"op":"commit_nav","ticket":nav["ticket"]}));
    h.nav("undo", &p);
    names("undo", json!([]));
    names("redo", json!(["older", "renamed"]));
    h.ok(json!({"op":"retain_record","record":c}));
    h.step("branch", &p);
    names("undo", json!(["branch"]));
    names("redo", json!([]));
    assert_eq!(h.record(&c)["name"], "renamed");
    h.ok(json!({"op":"clear"}));
    names("undo", json!([]));
    names("redo", json!([]));
    assert_eq!(h.info()["record_count"], 2);
    for record in [a, c] {
        h.ok(json!({"op":"release_record","record":record}));
    }
}

#[test]
fn history_names_wrong_history_and_strict_schema() {
    let a = H::new(2);
    let b = H::new(2);
    let p = a.payload();
    let record = a.step("only a", &p);
    let q = b.payload();
    b.step("only b", &q);
    let before_a = a.info();
    let before_b = b.info();
    for side in ["undo", "redo"] {
        for history in [json!(0), json!(u64::MAX), record.clone(), p.clone()] {
            assert_eq!(
                call(json!({"op":"names","history":history,"side":side})),
                json!({"status":"error","code":"invalid_history"})
            );
        }
    }
    a.err(json!({"op":"names"}), "invalid_request");
    a.err(json!({"op":"names","side":"both"}), "invalid_request");
    a.err(
        json!({"op":"names","side":"undo","extra":true}),
        "invalid_request",
    );
    assert_eq!(
        a.ok(json!({"op":"names","side":"undo"}))["result"],
        json!({"names":["only a"]})
    );
    assert_eq!(
        b.ok(json!({"op":"names","side":"undo"}))["result"],
        json!({"names":["only b"]})
    );
    assert_eq!(a.info(), before_a);
    assert_eq!(b.info(), before_b);
    a.ok(json!({"op":"close"}));
    a.err(json!({"op":"names","side":"undo"}), "invalid_history");
    assert_eq!(b.info(), before_b);
}

#[test]
fn history_names_large_read_sizing_is_pure() {
    let h = H::new(3);
    let p = h.payload();
    let escaped = "\u{0}\"é".repeat(4096);
    h.step(&escaped, &p);
    h.step("", &p);
    for side in ["undo", "redo"] {
        if side == "redo" {
            h.nav("undo", &p);
            h.nav("undo", &p);
        }
        let before = h.info();
        let request = serde_json::to_vec(&h.req(json!({"op":"names","side":side}))).unwrap();
        let (required, out) = raw(&request, 4096);
        assert!(required > 4096);
        assert!(out.iter().all(|b| *b == 0xa5));
        assert_eq!(h.info(), before);
        let (retry, out) = raw(&request, required as usize);
        assert_eq!(retry, required);
        let expected = if side == "undo" {
            json!(["", escaped])
        } else {
            json!([escaped, ""])
        };
        assert_eq!(
            serde_json::from_slice::<Value>(&out).unwrap(),
            json!({"status":"ok","result":{"names":expected},"info":before})
        );
        assert_eq!(h.info(), before);
        assert_eq!(
            h.ok(json!({"op":"names","side":side}))["result"]["names"],
            expected
        );
    }
}

fn raw(input: &[u8], capacity: usize) -> (isize, Vec<u8>) {
    let mut out = vec![0xa5; capacity];
    let n =
        unsafe { narwhal_history_json(input.as_ptr(), input.len(), out.as_mut_ptr(), capacity) };
    (n, out)
}
fn call(v: Value) -> Value {
    let input = serde_json::to_vec(&v).unwrap();
    let (n, out) = raw(&input, 4096);
    assert!(n > 0);
    let out = if n as usize > out.len() {
        assert!(
            matches!(v["op"].as_str(), Some("record" | "records" | "names")),
            "mutation requested replay: {v}"
        );
        let (retry, out) = raw(&input, n as usize);
        assert_eq!(retry, n);
        out
    } else {
        out
    };
    serde_json::from_slice(&out[..n as usize]).unwrap()
}
fn ok(v: Value) -> Value {
    let r = call(v);
    assert_eq!(r["status"], "ok", "{r}");
    r
}
struct H(u64);
impl H {
    fn new(limit: u64) -> Self {
        Self(
            ok(json!({"op":"create","limit":limit}))["result"]["history"]
                .as_u64()
                .unwrap(),
        )
    }
    fn req(&self, mut v: Value) -> Value {
        v["history"] = self.0.into();
        v
    }
    fn ok(&self, v: Value) -> Value {
        ok(self.req(v))
    }
    fn err(&self, v: Value, code: &str) {
        assert_eq!(call(self.req(v)), json!({"status":"error","code":code}));
    }
    fn info(&self) -> Value {
        self.ok(json!({"op":"info"}))["info"].clone()
    }
    fn payload(&self) -> Value {
        self.ok(json!({"op":"register_payload"}))["result"]["payload"].clone()
    }
    fn begin(&self, name: &str) -> Value {
        self.ok(json!({"op":"begin","name":name}))["info"]["open_record"].clone()
    }
    fn record(&self, id: &Value) -> Value {
        self.ok(json!({"op":"record","record":id}))["result"].clone()
    }
    fn step(&self, name: &str, p: &Value) -> Value {
        let id = self.begin(name);
        assert_eq!(
            self.ok(json!({"op":"capture","payload":p}))["result"],
            json!({"captured":true})
        );
        assert_eq!(
            self.ok(json!({"op":"end"}))["result"],
            json!({"committed":true})
        );
        id
    }
    fn prepare(&self, side: &str, p: &Value) -> Value {
        self.ok(json!({"op":"prepare_nav","direction":side,"current":p}))["result"].clone()
    }
    fn nav(&self, side: &str, p: &Value) -> Value {
        let r = self.prepare(side, p);
        self.ok(json!({"op":"commit_nav","ticket":r["ticket"]}));
        r
    }
    fn records(&self, side: &str) -> Value {
        self.ok(json!({"op":"records","side":side}))["result"]["records"].clone()
    }
    fn garbage(&self) -> Value {
        self.ok(json!({"op":"garbage","limit":128}))["result"]["payloads"].clone()
    }
}
impl Drop for H {
    fn drop(&mut self) {
        let _ = call(json!({"op":"close","history":self.0}));
    }
}

#[test]
fn history_lazy_nested_none_capture_and_disabled_open() {
    assert_eq!(narwhal_history_abi(), 1);
    let h = H::new(10);
    let initial = h.info();
    let p = h.payload();
    assert_eq!(h.info()["generation"], initial["generation"]);
    assert_eq!(
        h.ok(json!({"op":"capture","payload":p}))["result"]["captured"],
        false
    );
    let r = h.begin("outer");
    assert_eq!(h.info()["current_serial"], 1);
    let gen_before = h.info()["generation"].clone();
    assert_eq!(
        h.ok(json!({"op":"capture","payload":null}))["result"]["captured"],
        true
    );
    assert_eq!(h.info()["captured"], false);
    assert_eq!(h.info()["generation"], gen_before);
    assert_eq!(h.begin("inner"), r);
    assert_eq!(h.record(&r)["name"], "outer");
    h.ok(json!({"op":"enable","value":false}));
    let disabled = h.info();
    assert_eq!(
        h.ok(json!({"op":"begin","name":"disabled"}))["result"]["serial"],
        0
    );
    assert_eq!(h.info(), disabled);
    assert_eq!(
        h.ok(json!({"op":"capture","payload":p}))["result"]["captured"],
        true
    );
    let other = h.payload();
    assert_eq!(
        h.ok(json!({"op":"capture","payload":other}))["result"]["captured"],
        false
    );
    assert_eq!(h.record(&r)["before"], p);
    assert_eq!(h.ok(json!({"op":"end"}))["result"]["committed"], false);
    assert_eq!(h.info()["depth"], 1);
    assert_eq!(h.ok(json!({"op":"end"}))["result"]["committed"], true);
    assert_eq!(h.info()["depth"], 0);
    assert_eq!(h.info()["current_serial"], 0);
    assert_eq!(h.info()["top_serial"], 1);
    assert_ne!(h.info()["state_id"], initial["state_id"]);
    assert!(h.garbage().as_array().unwrap().is_empty());
    h.ok(json!({"op":"drop_payload","payload":other}));
}

#[test]
fn history_noops_abandon_preserve_redo_and_state() {
    let h = H::new(2);
    let p = h.payload();
    let r = h.step("real", &p);
    h.nav("undo", &p);
    let state = h.info()["state_id"].clone();
    let empty = h.begin("empty");
    assert_eq!(h.ok(json!({"op":"end"}))["result"]["committed"], false);
    h.err(json!({"op":"record","record":empty}), "invalid_record");
    assert_eq!(h.records("redo"), json!([r]));
    assert_eq!(h.info()["state_id"], state);
    h.begin("cancel");
    h.ok(json!({"op":"capture","payload":p}));
    h.ok(json!({"op":"abandon"}));
    assert_eq!(h.records("redo"), json!([r]));
    assert_eq!(h.info()["state_id"], state);
    assert_eq!(h.info()["next_serial"], 4);
    let before = h.info();
    h.ok(json!({"op":"end"}));
    h.ok(json!({"op":"abandon"}));
    h.ok(json!({"op":"enable","value":true}));
    h.ok(json!({"op":"set_name","record":r,"name":"real"}));
    h.ok(json!({"op":"set_before","record":r,"payload":p}));
    assert_eq!(h.info(), before);
    let fresh = h.step("branch", &p);
    assert_eq!(h.records("redo"), json!([]));
    assert_eq!(h.records("undo"), json!([fresh]));
    h.err(json!({"op":"record","record":r}), "invalid_record");
}

#[test]
fn history_saved_state_survives_trim_clear_and_navigation() {
    let h = H::new(1);
    let p = h.payload();
    let initial = h.info()["state_id"].clone();
    let a = h.step("a", &p);
    let saved = h.info()["state_id"].clone();
    let b = h.step("b", &p);
    let changed = h.info()["state_id"].clone();
    assert_ne!(saved, initial);
    assert_ne!(saved, changed);
    h.err(json!({"op":"record","record":a}), "invalid_record");
    assert_eq!(h.nav("undo", &p)["record"], b);
    assert_eq!(h.info()["top_serial"], 0);
    assert_eq!(h.info()["state_id"], saved);
    h.nav("redo", &p);
    assert_eq!(h.info()["state_id"], changed);
    h.nav("undo", &p);
    assert_eq!(h.info()["state_id"], saved);
    h.ok(json!({"op":"clear"}));
    assert_eq!(h.info()["state_id"], saved);
    let clear = h.info();
    h.ok(json!({"op":"clear"}));
    assert_eq!(h.info(), clear);
    let c = h.step("c", &p);
    assert!(c.as_u64().unwrap() > b.as_u64().unwrap());
    h.nav("undo", &p);
    assert_eq!(h.info()["state_id"], saved);
}

#[test]
fn history_escaped_leases_and_multiple_payload_fields() {
    let h = H::new(1);
    let p = h.payload();
    let q = h.payload();
    let r = h.step("retained", &p);
    h.ok(json!({"op":"set_after","record":r,"payload":p}));
    let before = h.info();
    h.ok(json!({"op":"retain_record","record":r}));
    h.ok(json!({"op":"retain_record","record":r}));
    h.record(&r);
    h.records("undo");
    assert_eq!(h.info(), before);
    let s = h.step("prune", &q);
    assert_eq!(h.info()["record_count"], 2);
    assert_eq!(h.record(&r)["before"], p);
    h.ok(json!({"op":"set_name","record":r,"name":"escaped\u{0}é"}));
    assert_eq!(h.record(&r)["name"], "escaped\u{0}é");
    h.ok(json!({"op":"set_before","record":r,"payload":null}));
    assert_eq!(h.garbage(), json!([]));
    h.ok(json!({"op":"release_record","record":r}));
    assert_eq!(h.record(&r)["after"], p);
    h.ok(json!({"op":"release_record","record":r}));
    assert_eq!(h.garbage(), json!([p]));
    h.err(json!({"op":"release_record","record":r}), "invalid_record");
    h.err(
        json!({"op":"release_record","record":s}),
        "record_not_retained",
    );
    // Reattachment cancels retirement; one ID in both fields still has two refs.
    h.ok(json!({"op":"set_before","record":s,"payload":p}));
    assert_eq!(h.garbage(), json!([q]));
    h.ok(json!({"op":"set_after","record":s,"payload":p}));
    h.ok(json!({"op":"set_before","record":s,"payload":null}));
    assert_eq!(h.garbage(), json!([q]));
    h.ok(json!({"op":"set_after","record":s,"payload":null}));
    assert_eq!(h.garbage(), json!([p, q]));
    h.ok(json!({"op":"ack_garbage","payloads":[p,q]}));
    assert_eq!(h.info()["payload_count"], 0);
}

#[test]
fn history_before_edits_control_capture_and_leased_abandon() {
    let h = H::new(2);
    let p = h.payload();
    let r = h.begin("edit");
    h.ok(json!({"op":"set_before","record":r,"payload":p}));
    assert_eq!(h.info()["captured"], true);
    h.ok(json!({"op":"set_before","record":r,"payload":null}));
    assert_eq!(h.info()["captured"], false);
    h.ok(json!({"op":"retain_record","record":r}));
    h.ok(json!({"op":"end"}));
    assert_eq!(h.records("undo"), json!([]));
    assert_eq!(h.record(&r)["before"], Value::Null);
    h.ok(json!({"op":"set_after","record":r,"payload":p}));
    assert_eq!(h.garbage(), json!([]));
    h.ok(json!({"op":"clear"}));
    assert_eq!(h.record(&r)["after"], p);
    h.ok(json!({"op":"release_record","record":r}));
    assert_eq!(h.garbage(), json!([p]));
    let r = h.begin("abandon");
    h.ok(json!({"op":"capture","payload":p}));
    h.ok(json!({"op":"retain_record","record":r}));
    h.ok(json!({"op":"abandon"}));
    assert_eq!(h.record(&r)["before"], p);
    h.ok(json!({"op":"release_record","record":r}));
}

#[test]
fn history_navigation_busy_abort_and_uncertain_recovery() {
    let h = H::new(10);
    let p = h.payload();
    let q = h.payload();
    let r = h.step("move", &p);
    let before = h.info();
    let fields = h.record(&r);
    let n = h.prepare("undo", &q);
    assert_eq!(n["target"], p);
    let pending = h.info();
    let edits = [
        json!({"op":"begin","name":"busy"}),
        json!({"op":"capture","payload":p}),
        json!({"op":"end"}),
        json!({"op":"abandon"}),
        json!({"op":"enable","value":false}),
        json!({"op":"set_before","record":r,"payload":null}),
        json!({"op":"set_after","record":r,"payload":p}),
        json!({"op":"set_name","record":r,"name":"busy"}),
        json!({"op":"clear"}),
        json!({"op":"prepare_nav","direction":"redo","current":null}),
    ];
    for edit in &edits {
        h.err(edit.clone(), "navigation_busy");
    }
    h.err(json!({"op":"commit_nav","ticket":0}), "invalid_ticket");
    h.err(
        json!({"op":"abort_nav","ticket":r,"unknown":false}),
        "invalid_ticket",
    );
    h.err(json!({"op":"drop_payload","payload":q}), "payload_in_use");
    h.ok(json!({"op":"retain_record","record":r}));
    h.ok(json!({"op":"release_record","record":r}));
    assert_eq!(h.info(), pending);
    assert_eq!(h.record(&r), fields);
    h.ok(json!({"op":"abort_nav","ticket":n["ticket"],"unknown":false}));
    assert_eq!(h.record(&r), fields);
    assert_eq!(h.records("undo"), json!([r]));
    assert_eq!(h.info()["state_id"], before["state_id"]);
    assert_eq!(h.info()["uncertain"], false);
    assert_eq!(h.garbage(), json!([q]));
    h.err(
        json!({"op":"commit_nav","ticket":n["ticket"]}),
        "invalid_ticket",
    );
    let n = h.prepare("undo", &q);
    assert_eq!(h.garbage(), json!([]));
    h.ok(json!({"op":"abort_nav","ticket":n["ticket"],"unknown":true}));
    assert_eq!(h.record(&r), fields);
    assert_eq!(h.info()["state_id"], before["state_id"]);
    assert_eq!(h.info()["uncertain"], true);
    for edit in &edits {
        if edit["op"] != "clear" {
            h.err(edit.clone(), "history_uncertain");
        }
    }
    h.ok(json!({"op":"retain_record","record":r}));
    h.ok(json!({"op":"release_record","record":r}));
    h.ok(json!({"op":"clear"}));
    assert_eq!(h.info()["uncertain"], false);
    assert_ne!(h.info()["state_id"], before["state_id"]);
    assert_eq!(h.records("undo"), json!([]));
    h.step("recovered", &p);
}

#[test]
fn history_navigation_refreshes_opposite_and_restores_state() {
    let h = H::new(3);
    let p = h.payload();
    let q = h.payload();
    let z = h.payload();
    assert_eq!(h.prepare("undo", &Value::Null), Value::Null);
    let initial = h.info()["state_id"].clone();
    let r = h.begin("x");
    assert_eq!(h.prepare("undo", &q), Value::Null);
    h.ok(json!({"op":"capture","payload":p}));
    h.ok(json!({"op":"end"}));
    let end = h.info()["state_id"].clone();
    assert_eq!(h.nav("undo", &q)["target"], p);
    assert_eq!(h.record(&r)["after"], q);
    assert_eq!(h.info()["state_id"], initial);
    assert_eq!(h.nav("redo", &z)["target"], q);
    assert_eq!(h.record(&r)["before"], z);
    assert_eq!(h.info()["state_id"], end);
    assert_eq!(h.garbage(), json!([p]));
    assert_eq!(h.nav("undo", &Value::Null)["target"], z);
    assert_eq!(h.record(&r)["after"], Value::Null);
    assert_eq!(h.nav("redo", &Value::Null)["target"], Value::Null);
    assert_eq!(h.record(&r)["before"], Value::Null);
    assert_eq!(h.info()["state_id"], end);
}

#[test]
fn history_cross_owner_handles_nonreuse_and_close() {
    let a = H::new(2);
    let b = H::new(2);
    let p = a.payload();
    let q = b.payload();
    let r = a.step("a", &p);
    let s = b.step("b", &q);
    let n = a.prepare("undo", &p);
    let ai = a.info();
    let bi = b.info();
    for record in [json!(0), json!(u64::MAX), p.clone(), json!(a.0), r.clone()] {
        b.err(json!({"op":"record","record":record}), "invalid_record");
        b.err(
            json!({"op":"retain_record","record":record}),
            "invalid_record",
        );
        b.err(
            json!({"op":"release_record","record":record}),
            "invalid_record",
        );
        b.err(
            json!({"op":"set_name","record":record,"name":"bad"}),
            "invalid_record",
        );
    }
    for op in ["capture", "drop_payload"] {
        b.err(json!({"op":op,"payload":p}), "invalid_payload");
    }
    b.err(
        json!({"op":"set_before","record":s,"payload":p}),
        "invalid_payload",
    );
    b.err(
        json!({"op":"prepare_nav","direction":"undo","current":p}),
        "invalid_payload",
    );
    for op in [
        json!({"op":"commit_nav","ticket":n["ticket"]}),
        json!({"op":"abort_nav","ticket":n["ticket"],"unknown":false}),
    ] {
        b.err(op, "invalid_ticket");
    }
    assert_eq!(a.info(), ai);
    assert_eq!(b.info(), bi);
    a.ok(json!({"op":"retain_record","record":r}));
    assert_eq!(
        a.ok(json!({"op":"close"})),
        json!({"status":"ok","result":null,"info":null})
    );
    a.err(json!({"op":"record","record":r}), "invalid_history");
    a.err(
        json!({"op":"commit_nav","ticket":n["ticket"]}),
        "invalid_history",
    );
    let c = H::new(1);
    assert!(c.0 > n["ticket"].as_u64().unwrap());
    let dropped = b.payload();
    b.ok(json!({"op":"drop_payload","payload":dropped}));
    let fresh = b.payload();
    assert!(fresh.as_u64().unwrap() > dropped.as_u64().unwrap());
    b.err(
        json!({"op":"drop_payload","payload":dropped}),
        "invalid_payload",
    );
}

#[test]
fn history_navigation_racing_consumption_is_once() {
    let h = H::new(2);
    let p = h.payload();
    let r = h.step("race", &p);
    let n = h.prepare("undo", &p);
    let barrier = Arc::new(Barrier::new(3));
    let mut threads = Vec::new();
    for _ in 0..2 {
        let barrier = barrier.clone();
        let request = h.req(json!({"op":"commit_nav","ticket":n["ticket"]}));
        threads.push(std::thread::spawn(move || {
            barrier.wait();
            call(request)
        }));
    }
    barrier.wait();
    let results: Vec<_> = threads.into_iter().map(|t| t.join().unwrap()).collect();
    assert_eq!(results.iter().filter(|r| r["status"] == "ok").count(), 1);
    assert_eq!(
        results
            .iter()
            .filter(|r| r["code"] == "invalid_ticket")
            .count(),
        1
    );
    assert_eq!(h.records("redo"), json!([r]));
    assert_eq!(h.records("undo"), json!([]));
    assert_eq!(h.record(&r)["after"], p);
    // A commit/abort race also has exactly one consumer; either outcome is coherent.
    let n = h.prepare("redo", &p);
    let barrier = Arc::new(Barrier::new(3));
    let mut threads = Vec::new();
    for op in ["commit_nav", "abort_nav"] {
        let barrier = barrier.clone();
        let mut request = h.req(json!({"op":op,"ticket":n["ticket"]}));
        if op == "abort_nav" {
            request["unknown"] = false.into();
        }
        threads.push(std::thread::spawn(move || {
            barrier.wait();
            call(request)
        }));
    }
    barrier.wait();
    let results: Vec<_> = threads.into_iter().map(|t| t.join().unwrap()).collect();
    assert_eq!(results.iter().filter(|r| r["status"] == "ok").count(), 1);
    assert_eq!(
        results
            .iter()
            .filter(|r| r["code"] == "invalid_ticket")
            .count(),
        1
    );
    assert_eq!(
        h.info()["undo_count"].as_u64().unwrap() + h.info()["redo_count"].as_u64().unwrap(),
        1
    );
}

#[test]
fn history_gc_bounded_atomic_ack_and_unattached_registration() {
    let h = H::new(200);
    let unused = h.payload();
    let mut payloads = Vec::new();
    for _ in 0..140 {
        let p = h.payload();
        h.step("gc", &p);
        payloads.push(p);
    }
    assert_eq!(h.garbage(), json!([]));
    h.ok(json!({"op":"clear"}));
    assert_eq!(h.info()["gc_pending"], 140);
    assert_eq!(h.garbage().as_array().unwrap().len(), 128);
    let before = h.info();
    let p = payloads[0].clone();
    h.err(
        json!({"op":"ack_garbage","payloads":[p,p]}),
        "duplicate_payload",
    );
    h.err(
        json!({"op":"ack_garbage","payloads":[p,unused]}),
        "invalid_garbage",
    );
    h.err(
        json!({"op":"ack_garbage","payloads":[p,0]}),
        "invalid_garbage",
    );
    h.err(
        json!({"op":"ack_garbage","payloads":payloads}),
        "garbage_limit",
    );
    h.err(json!({"op":"garbage","limit":0}), "invalid_limit");
    h.err(json!({"op":"garbage","limit":129}), "invalid_limit");
    assert_eq!(h.info(), before);
    let batch = h.garbage();
    h.ok(json!({"op":"ack_garbage","payloads":batch}));
    assert_eq!(h.info()["gc_pending"], 12);
    assert_eq!(h.info()["generation"], before["generation"]);
    h.ok(json!({"op":"ack_garbage","payloads":h.garbage()}));
    assert_eq!(h.info()["payload_count"], 1);
    h.ok(json!({"op":"drop_payload","payload":unused}));
    assert_eq!(h.info()["payload_count"], 0);
}

#[test]
fn history_strict_schema_names_and_exact_header() {
    let h = H::new(3);
    let info = h.info();
    let expected = [
        "generation",
        "state_id",
        "current_serial",
        "top_serial",
        "next_serial",
        "depth",
        "enabled",
        "uncertain",
        "undo_count",
        "redo_count",
        "open_record",
        "undo_record",
        "redo_record",
        "captured",
        "gc_pending",
        "record_count",
        "payload_count",
    ];
    assert_eq!(info.as_object().unwrap().len(), expected.len());
    for key in expected {
        assert!(info.get(key).is_some(), "missing {key}");
    }
    for req in [
        json!({"op":"begin","name":1}),
        json!({"op":"capture"}),
        json!({"op":"prepare_nav","direction":"undo"}),
        json!({"op":"set_after","record":0}),
        json!({"op":"set_before","record":0}),
        json!({"op":"enable","value":1}),
        json!({"op":"records","side":"both"}),
        json!({"op":"abort_nav","ticket":0}),
        json!({"op":"info","extra":1}),
        json!({"op":"garbage","limit":1.0}),
        json!({"op":"retain_record","record":-1}),
    ] {
        h.err(req, "invalid_request");
    }
    for text in [
        format!(
            "{{\"op\":\"begin\",\"history\":{},\"name\":\"a\",\"name\":\"b\"}}",
            h.0
        ),
        format!(
            "{{\"op\":\"info\",\"history\":{},\"history\":{}}}",
            h.0, h.0
        ),
        format!("{{\"op\":\"info\",\"op\":\"info\",\"history\":{}}}", h.0),
    ] {
        let (n, out) = raw(text.as_bytes(), 4096);
        assert_eq!(
            serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["code"],
            "invalid_request"
        );
    }
    assert_eq!(h.info(), info);
    h.err(json!({"op":"begin","name":"é".repeat(8193)}), "name_limit");
    let r = h.begin(&"é".repeat(8192));
    let before = h.info();
    h.err(
        json!({"op":"set_name","record":r,"name":"x".repeat(16385)}),
        "name_limit",
    );
    assert_eq!(h.info(), before);
    assert_eq!(h.record(&r)["name"].as_str().unwrap().len(), 16384);
    for limit in [0, 100001] {
        assert_eq!(
            call(json!({"op":"create","limit":limit}))["code"],
            "invalid_limit"
        );
    }
}

#[test]
fn history_mutation_descriptor_preflight_and_pure_large_reads() {
    let h = H::new(2);
    let before = h.info();
    let request = serde_json::to_vec(&h.req(json!({"op":"begin","name":"x"}))).unwrap();
    for capacity in [0, 1, 32, 4095] {
        let (n, out) = raw(&request, capacity);
        assert!(n > 0);
        if n as usize <= capacity {
            assert_eq!(
                serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["code"],
                "output_too_small"
            );
        } else {
            assert!(out.iter().all(|b| *b == 0xa5));
        }
        assert_eq!(h.info(), before);
    }
    let mut out = vec![0xa5; 4096];
    assert_eq!(
        unsafe {
            narwhal_history_json(request.as_ptr(), request.len(), std::ptr::null_mut(), 4096)
        },
        -1
    );
    assert_eq!(
        unsafe {
            narwhal_history_json(
                request.as_ptr(),
                request.len(),
                out.as_mut_ptr(),
                MAX_OUTPUT_CAPACITY + 1,
            )
        },
        -1
    );
    for (ptr, len, code) in [
        (std::ptr::null(), 0, "null_input"),
        (
            request.as_ptr(),
            MAX_HISTORY_REQUEST_BYTES + 1,
            "request_too_large",
        ),
    ] {
        let n = unsafe { narwhal_history_json(ptr, len, out.as_mut_ptr(), out.len()) };
        assert_eq!(
            serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["code"],
            code
        );
    }
    assert_eq!(h.info(), before);
    let r = h.begin(&"\"".repeat(16384));
    let before = h.info();
    let input = serde_json::to_vec(&h.req(json!({"op":"record","record":r}))).unwrap();
    let (n, out) = raw(&input, 4096);
    assert!(n > 4096);
    assert!(out.iter().all(|b| *b == 0xa5));
    let (again, out) = raw(&input, n as usize);
    assert_eq!(again, n);
    assert_eq!(
        serde_json::from_slice::<Value>(&out).unwrap()["result"]["name"],
        "\"".repeat(16384)
    );
    assert_eq!(h.info(), before);
    // Large name mutation returns compact header, never serializes its name.
    let set = h.ok(json!({"op":"set_name","record":r,"name":"z".repeat(16384)}));
    assert!(serde_json::to_vec(&set).unwrap().len() < 4096);
    let text = serde_json::to_vec(&h.req(json!({"op":"info"}))).unwrap();
    let mut buffer = vec![0; 4096];
    buffer[..text.len()].copy_from_slice(&text);
    let n = unsafe {
        narwhal_history_json(
            buffer.as_ptr(),
            text.len(),
            buffer.as_mut_ptr(),
            buffer.len(),
        )
    };
    assert_eq!(
        serde_json::from_slice::<Value>(&buffer[..n as usize]).unwrap()["info"],
        h.info()
    );
}
