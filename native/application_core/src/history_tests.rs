//! White-box admission/overflow proofs supplement the public ABI state-machine tests.
use super::*;
use serde_json::{Value, json};
fn run(r: &mut Registry, v: Value) -> Value {
    let req = serde_json::from_value(v).unwrap();
    serde_json::to_value(r.execute(req).unwrap()).unwrap()
}
fn reject(r: &mut Registry, v: Value, expected: &str) {
    let request = serde_json::from_value(v).unwrap();
    assert!(matches!(r.execute(request), Err(code) if code == expected));
}
fn create(r: &mut Registry) -> u64 {
    run(r, json!({"op":"create","limit":100000}))["result"]["history"]
        .as_u64()
        .unwrap()
}
fn begin(r: &mut Registry, h: u64) -> u64 {
    run(r, json!({"op":"begin","history":h,"name":"x"}))["info"]["open_record"]
        .as_u64()
        .unwrap()
}
fn payload(r: &mut Registry, h: u64) -> u64 {
    run(r, json!({"op":"register_payload","history":h}))["result"]["payload"]
        .as_u64()
        .unwrap()
}
fn info(r: &mut Registry, h: u64) -> Value {
    run(r, json!({"op":"info","history":h}))
}

#[test]
fn history_admission_checks_are_transactional() {
    let mut r = Registry::default();
    let h = create(&mut r);
    let initial = info(&mut r, h);
    let next = r.next_id;
    r.budget.records = MAX_RECORDS;
    reject(
        &mut r,
        json!({"op":"begin","history":h,"name":"x"}),
        "record_limit",
    );
    assert_eq!(info(&mut r, h), initial);
    assert_eq!(r.next_id, next);
    r.budget.records = 0;
    r.budget.names = MAX_NAME_BYTES;
    reject(
        &mut r,
        json!({"op":"begin","history":h,"name":"x"}),
        "name_storage_limit",
    );
    assert_eq!(info(&mut r, h), initial);
    assert_eq!(r.next_id, next);
    r.budget.names = 0;
    r.budget.payloads = MAX_PAYLOADS;
    reject(
        &mut r,
        json!({"op":"register_payload","history":h}),
        "payload_limit",
    );
    assert_eq!(info(&mut r, h), initial);
    assert_eq!(r.next_id, next);
    r.budget.payloads = 0;
    let id = begin(&mut r, h);
    let opened = info(&mut r, h);
    r.budget.leases = MAX_LEASES;
    reject(
        &mut r,
        json!({"op":"retain_record","history":h,"record":id}),
        "lease_limit",
    );
    assert_eq!(r.histories[&h].records[&id].leases, 0);
    assert_eq!(info(&mut r, h), opened);
    r.budget.leases = 0;
    r.budget.names = MAX_NAME_BYTES;
    reject(
        &mut r,
        json!({"op":"set_name","history":h,"record":id,"name":"longer"}),
        "name_storage_limit",
    );
    assert_eq!(r.histories[&h].records[&id].name, "x");
    assert_eq!(info(&mut r, h), opened);
    // Equal-byte replacement at the boundary remains legal.
    run(
        &mut r,
        json!({"op":"set_name","history":h,"record":id,"name":"y"}),
    );
    assert_eq!(r.budget.names, MAX_NAME_BYTES);
}

#[test]
fn history_ids_serials_depth_generation_exhaust_without_partial_mutation() {
    let mut r = Registry {
        next_id: u64::MAX - 1,
        ..Registry::default()
    };
    reject(&mut r, json!({"op":"create","limit":1}), "id_exhausted");
    assert_eq!(r.next_id, u64::MAX - 1);
    assert!(r.histories.is_empty());
    let mut r = Registry::default();
    let h = create(&mut r);
    r.next_id = u64::MAX;
    let old = info(&mut r, h);
    reject(
        &mut r,
        json!({"op":"begin","history":h,"name":"x"}),
        "id_exhausted",
    );
    reject(
        &mut r,
        json!({"op":"register_payload","history":h}),
        "id_exhausted",
    );
    assert_eq!(info(&mut r, h), old);
    let mut r = Registry::default();
    let h = create(&mut r);
    r.histories.get_mut(&h).unwrap().next_serial = u64::MAX;
    let old = info(&mut r, h);
    let next = r.next_id;
    reject(
        &mut r,
        json!({"op":"begin","history":h,"name":"x"}),
        "serial_exhausted",
    );
    assert_eq!(info(&mut r, h), old);
    assert_eq!(r.next_id, next);
    r.histories.get_mut(&h).unwrap().next_serial = 1;
    let id = begin(&mut r, h);
    let p = payload(&mut r, h);
    r.histories.get_mut(&h).unwrap().depth = u64::MAX;
    let old = info(&mut r, h);
    reject(
        &mut r,
        json!({"op":"begin","history":h,"name":"nested"}),
        "depth_exhausted",
    );
    assert_eq!(info(&mut r, h), old);
    r.histories.get_mut(&h).unwrap().depth = 1;
    r.histories.get_mut(&h).unwrap().generation = u64::MAX;
    let old = info(&mut r, h);
    for req in [
        json!({"op":"capture","history":h,"payload":p}),
        json!({"op":"end","history":h}),
        json!({"op":"abandon","history":h}),
        json!({"op":"enable","history":h,"value":false}),
        json!({"op":"set_name","history":h,"record":id,"name":"new"}),
        json!({"op":"set_before","history":h,"record":id,"payload":p}),
        json!({"op":"clear","history":h}),
    ] {
        reject(&mut r, req, "generation_exhausted");
        assert_eq!(info(&mut r, h), old);
    }
    r.histories.get_mut(&h).unwrap().generation = 1;
    run(&mut r, json!({"op":"capture","history":h,"payload":p}));
    r.next_id = u64::MAX;
    let old = info(&mut r, h);
    reject(&mut r, json!({"op":"end","history":h}), "id_exhausted");
    assert_eq!(info(&mut r, h), old);
    r.histories.get_mut(&h).unwrap().uncertain = true;
    let old = info(&mut r, h);
    reject(&mut r, json!({"op":"clear","history":h}), "id_exhausted");
    assert_eq!(info(&mut r, h), old);
}

#[test]
fn history_navigation_reserves_completion_generation() {
    let mut r = Registry::default();
    let h = create(&mut r);
    begin(&mut r, h);
    let p = payload(&mut r, h);
    run(&mut r, json!({"op":"capture","history":h,"payload":p}));
    run(&mut r, json!({"op":"end","history":h}));
    r.histories.get_mut(&h).unwrap().generation = u64::MAX - 1;
    let old = info(&mut r, h);
    let next = r.next_id;
    reject(
        &mut r,
        json!({"op":"prepare_nav","history":h,"direction":"undo","current":p}),
        "generation_exhausted",
    );
    assert_eq!(info(&mut r, h), old);
    assert_eq!(r.next_id, next);
    assert_eq!(r.histories[&h].payloads[&p], 1);
    r.histories.get_mut(&h).unwrap().generation = u64::MAX - 2;
    let n = run(
        &mut r,
        json!({"op":"prepare_nav","history":h,"direction":"undo","current":p}),
    );
    assert_eq!(r.histories[&h].payloads[&p], 2);
    run(
        &mut r,
        json!({"op":"commit_nav","history":h,"ticket":n["result"]["ticket"]}),
    );
    assert_eq!(r.histories[&h].generation, u64::MAX);
    assert_eq!(r.histories[&h].payloads[&p], 2);
    assert_eq!(r.histories[&h].redo.len(), 1);
}

#[test]
fn history_global_admission_and_close_release_all_charges() {
    let mut r = Registry::default();
    let mut histories = Vec::new();
    for _ in 0..MAX_HISTORIES {
        histories.push(create(&mut r));
    }
    let next = r.next_id;
    reject(&mut r, json!({"op":"create","limit":1}), "history_limit");
    assert_eq!(r.next_id, next);
    let h = histories[0];
    let id = begin(&mut r, h);
    let p = payload(&mut r, h);
    run(&mut r, json!({"op":"capture","history":h,"payload":p}));
    run(
        &mut r,
        json!({"op":"retain_record","history":h,"record":id}),
    );
    run(
        &mut r,
        json!({"op":"set_after","history":h,"record":id,"payload":p}),
    );
    run(&mut r, json!({"op":"end","history":h}));
    run(
        &mut r,
        json!({"op":"prepare_nav","history":h,"direction":"undo","current":p}),
    );
    assert_eq!(r.histories[&h].payloads[&p], 3);
    run(&mut r, json!({"op":"close","history":h}));
    assert_eq!(
        (
            r.budget.records,
            r.budget.names,
            r.budget.payloads,
            r.budget.leases
        ),
        (0, 0, 0, 0)
    );
    assert!(create(&mut r) > next);
}

#[test]
fn history_refcounts_match_fields_and_pending_without_snapshot_copy() {
    let mut r = Registry::default();
    let h = create(&mut r);
    let p = payload(&mut r, h);
    let id = begin(&mut r, h);
    run(&mut r, json!({"op":"capture","history":h,"payload":p}));
    run(
        &mut r,
        json!({"op":"set_after","history":h,"record":id,"payload":p}),
    );
    run(
        &mut r,
        json!({"op":"retain_record","history":h,"record":id}),
    );
    run(&mut r, json!({"op":"end","history":h}));
    let name_ptr = r.histories[&h].records[&id].name.as_ptr();
    let n = run(
        &mut r,
        json!({"op":"prepare_nav","history":h,"direction":"undo","current":p}),
    );
    assert_eq!(r.histories[&h].payloads[&p], 3);
    run(
        &mut r,
        json!({"op":"abort_nav","history":h,"ticket":n["result"]["ticket"],"unknown":false}),
    );
    assert_eq!(r.histories[&h].payloads[&p], 2);
    assert_eq!(r.histories[&h].records[&id].name.as_ptr(), name_ptr);
    run(&mut r, json!({"op":"clear","history":h}));
    assert_eq!(r.histories[&h].payloads[&p], 2);
    run(
        &mut r,
        json!({"op":"release_record","history":h,"record":id}),
    );
    assert_eq!(r.histories[&h].payloads[&p], 0);
    assert_eq!(r.histories[&h].garbage.len(), 1);
    run(
        &mut r,
        json!({"op":"ack_garbage","history":h,"payloads":[p]}),
    );
    assert_eq!(
        (
            r.budget.records,
            r.budget.names,
            r.budget.payloads,
            r.budget.leases
        ),
        (0, 0, 0, 0)
    );
}
