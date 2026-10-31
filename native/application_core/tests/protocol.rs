use narwhal_application_core::{
    MAX_OUTPUT_CAPACITY, MAX_REQUEST_BYTES, narwhal_session_abi, narwhal_session_json,
};
use serde_json::{Value, json};

fn raw(input: &[u8], capacity: usize) -> (isize, Vec<u8>) {
    let mut output = vec![0xa5; capacity];
    let n =
        unsafe { narwhal_session_json(input.as_ptr(), input.len(), output.as_mut_ptr(), capacity) };
    (n, output)
}
fn call(v: Value) -> Value {
    let input = serde_json::to_vec(&v).unwrap();
    let (n, mut out) = raw(&input, 4096);
    assert!(n > 0);
    if n as usize > out.len() {
        let (retry, large) = raw(&input, n as usize);
        assert_eq!(retry, n, "read retry changed without any intervening write");
        out = large;
    }
    serde_json::from_slice(&out[..n as usize]).unwrap()
}
fn ok(v: Value) -> Value {
    let reply = call(v);
    assert_eq!(reply["status"], "ok", "{reply}");
    reply
}
fn code(v: Value, expected: &str) {
    let reply = call(v);
    assert_eq!(reply, json!({"status":"error","code":expected}));
}
struct Session(u64);
impl Session {
    fn new(rows: Value) -> Self {
        Self(
            ok(json!({"op":"create","rows":rows}))["session"]
                .as_u64()
                .unwrap(),
        )
    }
    fn request(&self, mut v: Value) -> Value {
        v["session"] = self.0.into();
        v
    }
    fn ok(&self, v: Value) -> Value {
        ok(self.request(v))
    }
    fn code(&self, v: Value, expected: &str) {
        code(self.request(v), expected);
    }
    fn info(&self) -> Value {
        self.ok(json!({"op":"info"}))
    }
    fn rows(&self) -> Value {
        self.ok(json!({"op":"rows"}))["rows"].clone()
    }
    fn snapshot(&self) -> u64 {
        self.ok(json!({"op":"snapshot"}))["snapshot"]
            .as_u64()
            .unwrap()
    }
    fn prepare(&self, key: &str, value: Value) -> Value {
        self.ok(
            json!({"op":"prepare","generation":self.info()["generation"],"key":key,"value":value}),
        )
    }
    fn set(&self, key: &str, value: Value) -> Value {
        let p = self.prepare(key, value);
        assert_eq!(p["changed"], true);
        self.ok(json!({"op":"commit","ticket":p["ticket"]}))
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        let _ = call(json!({"op":"close","session":self.0}));
    }
}

#[test]
fn native_replay_order_spelling_empty_and_exact_lookup() {
    assert_eq!(narwhal_session_abi(), 1);
    let s = Session::new(json!([
        ["First", "1"],
        ["Second", "2"],
        ["FIRST", "3"],
        ["", "ignored"],
        ["second", ""],
        ["THIRD", "4"],
        ["second", "5"],
        ["absent", ""]
    ]));
    assert_eq!(
        s.rows(),
        json!([["First", "3"], ["THIRD", "4"], ["second", "5"]])
    );
    assert_eq!(s.info()["generation"], 0);
    assert_eq!(s.info()["count"], 3);
    assert_eq!(
        s.ok(json!({"op":"get","key":"FIRST","exact":false})),
        json!({"status":"ok","found":true,"value":"3","key":"First"})
    );
    assert_eq!(
        s.ok(json!({"op":"get","key":"FIRST","exact":true})),
        json!({"status":"ok","found":false,"value":null,"key":null})
    );
    assert_eq!(
        s.ok(json!({"op":"get","key":"First","exact":true}))["found"],
        true
    );
    for key in ["", "absent"] {
        assert_eq!(
            s.ok(json!({"op":"get","key":key,"exact":false}))["found"],
            false
        );
    }
    for (index, expected) in [(0, json!(["First", "3"])), (2, json!(["second", "5"]))] {
        assert_eq!(
            s.ok(json!({"op":"at","index":index})),
            json!({"status":"ok","found":true,"row":expected})
        );
    }
    for index in [3, u64::MAX] {
        assert_eq!(
            s.ok(json!({"op":"at","index":index})),
            json!({"status":"ok","found":false,"row":null})
        );
    }
    s.set("FIRST", json!("changed"));
    assert_eq!(s.rows()[0], json!(["First", "changed"]));
    s.set("THIRD", Value::Null);
    s.set("third", json!("back"));
    assert_eq!(
        s.rows(),
        json!([["First", "changed"], ["second", "5"], ["third", "back"]])
    );
}

#[test]
fn document_user_text_count_matches_native_and_is_read_only() {
    // MEASURED from rhino3dm 8.35.0, including Main's integration fixtures.
    let fixtures = [
        ("plain", 0),
        (".hidden", 0),
        ("Section\\Entry", 1),
        ("section/entry", 1),
        ("a\\b\\c", 2),
        ("\\", 3),
        ("\\leading", 4),
        ("trailing\\", 5),
        ("value-only", 5),
        ("fullwidth＼", 5),
        ("double\\\\slash", 6),
    ];
    let s = Session::new(json!([]));
    let assert_count = |expected: u64| {
        let before = s.info();
        for _ in 0..2 {
            assert_eq!(
                s.ok(json!({"op":"document_user_text_count"})),
                json!({"status":"ok","count":expected})
            );
        }
        assert_eq!(s.info(), before);
    };
    assert_count(0);
    for (i, (key, expected)) in fixtures.iter().enumerate() {
        s.set(key, json!("value\\ignored"));
        assert_count(*expected);
        assert_eq!(s.info()["count"], i + 1, "info.count still means all rows");
        // Import replay must have the same count as committed individual writes.
        let replay = Session::new(json!(
            fixtures[..=i]
                .iter()
                .map(|(key, _)| [*key, "value\\ignored"])
                .collect::<Vec<_>>()
        ));
        assert_eq!(
            replay.ok(json!({"op":"document_user_text_count"}))["count"],
            *expected
        );
    }
    let snap = s.snapshot();
    let prepared = s.prepare("pending\\key", json!("v"));
    assert_count(6);
    s.ok(json!({"op":"abort","ticket":prepared["ticket"]}));
    assert_count(6);
    s.set("SECTION\\ENTRY", json!("replacement"));
    assert_count(6);
    s.set("section\\entry", Value::Null);
    assert_count(5);
    s.set("double\\\\slash", json!(""));
    assert_count(4);
    s.ok(json!({"op":"restore","snapshot":snap}));
    assert_count(6);
    s.code(
        json!({"op":"document_user_text_count","extra":true}),
        "invalid_request",
    );
    code(json!({"op":"document_user_text_count"}), "invalid_request");
    code(
        json!({"op":"document_user_text_count","session":"1"}),
        "invalid_request",
    );
    code(
        json!({"op":"document_user_text_count","session":0}),
        "invalid_session",
    );
    s.ok(json!({"op":"close"}));
    s.code(json!({"op":"document_user_text_count"}), "invalid_session");
}

#[test]
fn exact_pinned_ordinal_not_unicode_casefold() {
    let cases = [
        ("A", "a", true),
        ("ß", "ss", false),
        ("Σ", "σ", true),
        ("Σ", "ς", false),
        ("İ", "i", true),
        ("é", "É", true),
        ("K", "k", false),
        ("ı", "I", false),
        ("ÿ", "Ÿ", true),
        ("ſ", "S", false),
        ("ŉ", "N", false),
        ("Ａ", "ａ", false),
        ("Ｂ", "ｂ", true),
        ("Я", "я", true),
        ("Ё", "ё", false),
        ("𐐀", "𐐨", false),
        ("𞤀", "𞤢", false),
        ("e\u{301}", "é", false),
        ("😀", "😀", true),
    ];
    for (a, b, equal) in cases {
        let s = Session::new(json!([[a, "first"], [b, "second"]]));
        let expected = if equal {
            json!([[a, "second"]])
        } else {
            json!([[a, "first"], [b, "second"]])
        };
        assert_eq!(s.rows(), expected, "pinned ordinal pair {a:?}/{b:?}");
        assert_eq!(
            s.ok(json!({"op":"get","key":b,"exact":false}))["value"],
            "second"
        );
    }
}

#[test]
fn no_op_generation_and_identity_never_change() {
    let s = Session::new(json!([["Key", "v"]]));
    let before = s.info();
    for (key, value) in [
        ("KEY", json!("v")),
        ("missing", Value::Null),
        ("missing", json!("")),
        ("", json!("anything")),
        ("", Value::Null),
    ] {
        assert_eq!(
            s.prepare(key, value),
            json!({"status":"ok","changed":false,"ticket":null})
        );
        assert_eq!(s.info(), before);
    }
    s.set("key", json!(""));
    assert_eq!(s.info()["generation"], 1);
    assert_eq!(s.rows(), json!([]));
    let before = s.info();
    assert_eq!(s.prepare("key", Value::Null)["changed"], false);
    assert_eq!(s.info(), before);
}

#[test]
fn staged_abort_conflict_and_single_use_tickets() {
    let s = Session::new(json!([["K", "old"]]));
    let before = s.info();
    let p = s.prepare("K", json!("aborted"));
    assert_eq!(s.rows(), json!([["K", "old"]]));
    assert_eq!(s.info(), before);
    s.ok(json!({"op":"abort","ticket":p["ticket"]}));
    s.code(
        json!({"op":"commit","ticket":p["ticket"]}),
        "invalid_ticket",
    );
    assert_eq!(s.info(), before);
    let a = s.prepare("K", json!("first"));
    let b = s.prepare("B", json!("second"));
    s.ok(json!({"op":"commit","ticket":a["ticket"]}));
    assert_eq!(s.rows(), json!([["K", "first"]]));
    assert_eq!(s.info()["generation"], 1);
    s.code(
        json!({"op":"commit","ticket":a["ticket"]}),
        "invalid_ticket",
    );
    s.code(
        json!({"op":"commit","ticket":b["ticket"]}),
        "generation_conflict",
    );
    s.code(
        json!({"op":"commit","ticket":b["ticket"]}),
        "invalid_ticket",
    );
    s.code(
        json!({"op":"prepare","generation":0,"key":"K","value":"first"}),
        "generation_conflict",
    );
    assert_eq!(s.rows(), json!([["K", "first"]]));
}

#[test]
fn restore_identity_equality_and_conflicts() {
    let s = Session::new(json!([["K", "old"]]));
    let original = s.info()["state_id"].clone();
    let snap = s.snapshot();
    let same = s.snapshot();
    assert_eq!(
        s.ok(json!({"op":"equal","left":snap,"right":same}))["equal"],
        true
    );
    s.set("K", json!("new"));
    let changed = s.snapshot();
    assert_eq!(
        s.ok(json!({"op":"equal","left":snap,"right":changed}))["equal"],
        false
    );
    assert_eq!(
        s.ok(json!({"op":"snapshot_rows","snapshot":snap}))["rows"],
        json!([["K", "old"]])
    );
    s.set("K", json!("old"));
    let content_equal = s.snapshot();
    assert_ne!(s.info()["state_id"], original);
    assert_eq!(
        s.ok(json!({"op":"equal","left":snap,"right":content_equal}))["equal"],
        true
    );
    let pending = s.prepare("K", json!("pending"));
    for generation in [3, 4] {
        assert_eq!(
            s.ok(json!({"op":"restore","snapshot":snap})),
            json!({"status":"ok","generation":generation,"state_id":original})
        );
    }
    s.code(
        json!({"op":"commit","ticket":pending["ticket"]}),
        "generation_conflict",
    );
    assert_eq!(s.rows(), json!([["K", "old"]]));
    s.ok(json!({"op":"release_snapshot","snapshot":snap}));
    s.code(json!({"op":"restore","snapshot":snap}), "invalid_snapshot");
    assert_eq!(s.info()["generation"], 4);
}

#[test]
fn snapshot_equal_observes_order_spelling_and_values() {
    let s = Session::new(json!([["A", "1"], ["B", "2"]]));
    let a = s.snapshot();
    s.set("A", Value::Null);
    s.set("A", json!("1"));
    let reordered = s.snapshot();
    assert_eq!(
        s.ok(json!({"op":"equal","left":a,"right":reordered}))["equal"],
        false
    );
    s.ok(json!({"op":"restore","snapshot":a}));
    s.set("B", Value::Null);
    s.set("b", json!("2"));
    let spelling = s.snapshot();
    assert_eq!(
        s.ok(json!({"op":"equal","left":a,"right":spelling}))["equal"],
        false
    );
}

#[test]
fn cross_session_and_closed_handles_cannot_be_reused() {
    let a = Session::new(json!([]));
    let b = Session::new(json!([]));
    let snap = a.snapshot();
    let ticket = a.prepare("k", json!("v"))["ticket"].clone();
    for op in ["restore", "snapshot_rows", "release_snapshot"] {
        b.code(json!({"op":op,"snapshot":snap}), "snapshot_owner_mismatch");
    }
    b.code(
        json!({"op":"equal","left":snap,"right":snap}),
        "snapshot_owner_mismatch",
    );
    for op in ["commit", "abort"] {
        b.code(json!({"op":op,"ticket":ticket}), "ticket_owner_mismatch");
    }
    a.ok(json!({"op":"commit","ticket":ticket}));
    let pending = a.prepare("k", json!("close discards me"))["ticket"].clone();
    a.ok(json!({"op":"close"}));
    a.code(json!({"op":"info"}), "invalid_session");
    b.code(json!({"op":"restore","snapshot":snap}), "invalid_snapshot");
    b.code(json!({"op":"commit","ticket":pending}), "invalid_ticket");
    let c = Session::new(json!([]));
    assert!(c.0 > pending.as_u64().unwrap());
    assert!(c.snapshot() > snap);
    assert_eq!(b.info()["generation"], 0);
}

#[test]
fn strict_json_and_named_string_limits() {
    let s = Session::new(json!([]));
    for request in [
        json!({"op":"create","rows":[],"extra":1}),
        json!({"op":"create"}),
        json!({"op":"create","rows":[["k"]]}),
        json!({"op":"create","rows":[["k","v","extra"]]}),
        json!({"op":"create","rows":[[1,"v"]]}),
        json!({"op":"create","rows":null}),
        json!({"op":"unknown"}),
        json!([]),
        json!(null),
        s.request(json!({"op":"get","key":"k","exact":1})),
        s.request(json!({"op":"get","key":"k"})),
        s.request(json!({"op":"at","index":-1})),
        s.request(json!({"op":"at","index":1.0})),
        s.request(json!({"op":"prepare","generation":0,"key":"k"})),
        s.request(json!({"op":"prepare","generation":0,"key":"k","value":false})),
        s.request(json!({"op":"prepare","generation":"0","key":"k","value":"v"})),
    ] {
        code(request, "invalid_request");
    }
    for input in [
        br#"{"op":"create","op":"create","rows":[]}"#.as_slice(),
        br#"{"op":"create","rows":[],"rows":[]}"#.as_slice(),
    ] {
        let (n, out) = raw(input, 4096);
        assert_eq!(
            serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["status"],
            "error"
        );
    }
    for input in [
        b"".as_slice(),
        b"{}{}",
        b"{",
        br#"{"op":"create","rows":[["\uD800","v"]]}"#,
    ] {
        let (n, out) = raw(input, 4096);
        assert_eq!(
            serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["code"],
            "invalid_json"
        );
    }
    let (n, out) = raw(&[0xff], 4096);
    assert_eq!(
        serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["code"],
        "invalid_utf8"
    );
    for key in ["\0", "a\0b"] {
        code(
            json!({"op":"create","rows":[[key,"v"]]}),
            "nul_not_supported",
        );
    }
    s.code(
        json!({"op":"prepare","generation":0,"key":"k","value":"a\0b"}),
        "nul_not_supported",
    );
    let max = "x".repeat(8 * 1024 * 1024);
    let ticket = s.prepare("large", json!(max))["ticket"].clone();
    s.ok(json!({"op":"abort","ticket":ticket}));
    s.code(
        json!({"op":"prepare","generation":0,"key":"k","value":max + "x"}),
        "string_limit",
    );
    assert_eq!(s.info()["count"], 0);
}

#[test]
fn large_read_is_pure_retryable_and_never_partially_copied() {
    let large = "\n\\\"é😀".repeat(3000);
    let s = Session::new(json!([["Key", large]]));
    let snap = s.snapshot();
    let before = s.info();
    for request in [
        s.request(json!({"op":"rows"})),
        s.request(json!({"op":"snapshot_rows","snapshot":snap})),
        s.request(json!({"op":"get","key":"KEY","exact":false})),
        s.request(json!({"op":"at","index":0})),
    ] {
        let input = serde_json::to_vec(&request).unwrap();
        let (required, out) = raw(&input, 4096);
        assert!(required > 4096);
        assert!(out.iter().all(|v| *v == 0xa5));
        let (written, larger) = raw(&input, required as usize);
        assert_eq!(written, required);
        assert_eq!(
            serde_json::from_slice::<Value>(&larger).unwrap()["status"],
            "ok"
        );
        assert_eq!(s.info(), before);
    }
    let ticket = s.prepare("KEY", json!("small reply even though old value is huge"));
    assert_eq!(ticket["changed"], true);
    s.ok(json!({"op":"commit","ticket":ticket["ticket"]}));
    assert_eq!(s.info()["generation"], 1);
}

#[test]
fn descriptors_reject_before_mutation_and_compact_replies_fit() {
    let s = Session::new(json!([["K", "old"]]));
    let snap = s.snapshot();
    let ticket = s.prepare("K", json!("new"))["ticket"].clone();
    let before = s.info();
    for request in [
        json!({"op":"create","rows":[]}),
        s.request(json!({"op":"close"})),
        s.request(json!({"op":"snapshot"})),
        s.request(json!({"op":"release_snapshot","snapshot":snap})),
        s.request(json!({"op":"prepare","generation":0,"key":"extra","value":"v"})),
        s.request(json!({"op":"commit","ticket":ticket})),
        s.request(json!({"op":"abort","ticket":ticket})),
        s.request(json!({"op":"restore","snapshot":snap})),
    ] {
        let input = serde_json::to_vec(&request).unwrap();
        for capacity in [0, 1, 64, 4095] {
            let (n, out) = raw(&input, capacity);
            if n as usize <= capacity {
                assert_eq!(
                    serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["code"],
                    "output_too_small"
                );
            } else {
                assert!(out.iter().all(|v| *v == 0xa5));
            }
            assert_eq!(s.info(), before);
        }
        let mut out = vec![0xa5; 4096];
        unsafe {
            assert_eq!(
                narwhal_session_json(input.as_ptr(), input.len(), std::ptr::null_mut(), 4096),
                -1
            );
            assert_eq!(
                narwhal_session_json(
                    input.as_ptr(),
                    input.len(),
                    out.as_mut_ptr(),
                    MAX_OUTPUT_CAPACITY + 1
                ),
                -1
            );
        }
        assert!(out.iter().all(|v| *v == 0xa5));
        assert_eq!(s.info(), before);
    }
    // Abort/release/close must not have happened on any rejected call.
    s.ok(json!({"op":"snapshot_rows","snapshot":snap}));
    let commit = s.ok(json!({"op":"commit","ticket":ticket}));
    assert_eq!(commit["generation"], 1);
    assert_eq!(s.rows(), json!([["K", "new"]]));
    let input = br#"{"op":"create","rows":[]}"#;
    let mut out = vec![0; 4096];
    for (ptr, len, expected) in [
        (std::ptr::null(), 0, "null_input"),
        (input.as_ptr(), MAX_REQUEST_BYTES + 1, "request_too_large"),
        (input.as_ptr(), usize::MAX, "request_too_large"),
    ] {
        let n = unsafe { narwhal_session_json(ptr, len, out.as_mut_ptr(), out.len()) };
        assert_eq!(
            serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["code"],
            expected
        );
    }
}

#[test]
fn input_output_can_overlap_without_pointer_ownership() {
    let mut buffer = vec![0; 4096];
    let request = br#"{"op":"create","rows":[["K","V"]]}"#;
    buffer[..request.len()].copy_from_slice(request);
    let n = unsafe {
        narwhal_session_json(
            buffer.as_ptr(),
            request.len(),
            buffer.as_mut_ptr(),
            buffer.len(),
        )
    };
    let created: Value = serde_json::from_slice(&buffer[..n as usize]).unwrap();
    let s = Session(created["session"].as_u64().unwrap());
    drop(buffer);
    assert_eq!(s.rows(), json!([["K", "V"]]));
}

#[test]
fn row_limits_reject_import_and_prepare_without_truncation() {
    let rows: Vec<_> = (0..100_000)
        .map(|i| json!([format!("K{i}"), "v"]))
        .collect();
    let s = Session::new(json!(rows));
    assert_eq!(s.info()["count"], 100_000);
    assert_eq!(
        s.ok(json!({"op":"at","index":99999}))["row"],
        json!(["K99999", "v"])
    );
    s.code(
        json!({"op":"prepare","generation":0,"key":"extra","value":"v"}),
        "row_limit",
    );
    let mut oversized = rows;
    oversized.push(json!(["extra", "v"]));
    code(json!({"op":"create","rows":oversized}), "row_limit");
    assert_eq!(s.info()["generation"], 0);
    s.set("K50000", json!("updated at the row bound"));
    s.set("K99999", Value::Null);
    s.set("extra", json!("v"));
    assert_eq!(s.info()["count"], 100_000);
}

#[test]
fn concurrent_callers_are_serialized_and_handles_remain_owned() {
    let threads: Vec<_> = (0..8)
        .map(|thread| {
            std::thread::spawn(move || {
                let s = Session::new(json!([]));
                for i in 0..20 {
                    s.set(&format!("K{thread}-{i}"), json!("v"));
                }
                assert_eq!(s.info()["generation"], 20);
                assert_eq!(s.info()["count"], 20);
                s.0
            })
        })
        .collect();
    let mut ids: Vec<_> = threads.into_iter().map(|t| t.join().unwrap()).collect();
    ids.sort_unstable();
    ids.dedup();
    assert_eq!(ids.len(), 8);
    for id in ids {
        code(json!({"op":"info","session":id}), "invalid_session");
    }
}
