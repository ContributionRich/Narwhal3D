use super::*;
use serde_json::{Value, json};

fn call(registry: &mut Registry, request: Value) -> Result<Value> {
    registry.execute(serde_json::from_value(request).unwrap())
}
fn row(n: u64) -> Value {
    json!({"id":format!("00000000-0000-0000-0000-{n:012x}"),"name":"same","display":null,"payload":n})
}
fn create(r: &mut Registry, rows: Vec<Value>) -> u64 {
    call(r, json!({"op":"create","rows":rows})).unwrap()["owner"]
        .as_u64()
        .unwrap()
}
fn info(r: &mut Registry, owner: u64) -> Value {
    call(r, json!({"op":"info","owner":owner})).unwrap()
}
fn prepare(r: &mut Registry, owner: u64, revision: u64, rows: Vec<Value>) -> u64 {
    call(
        r,
        json!({"op":"prepare","owner":owner,"revision":revision,"rows":rows,"rebuild":false}),
    )
    .unwrap()["ticket"]
        .as_u64()
        .unwrap()
}
fn apply(r: &mut Registry, owner: u64, ticket: u64) -> Value {
    call(r, json!({"op":"begin_apply","owner":owner,"ticket":ticket})).unwrap();
    call(r, json!({"op":"commit","owner":owner,"ticket":ticket})).unwrap()
}

fn witness(n: u64, geometry: &[u8]) -> Vec<u8> {
    let mut bytes = b"NWOBS01\0".to_vec();
    bytes.extend_from_slice(&1u32.to_le_bytes());
    bytes.extend_from_slice(row(n)["id"].as_str().unwrap().as_bytes());
    for field in [b"same".as_slice(), geometry, b"attributes"] {
        bytes.extend_from_slice(&(field.len() as u32).to_le_bytes());
        bytes.extend_from_slice(field);
    }
    bytes
}
fn observed(r: &mut Registry, request: Value, bytes: &[u8]) -> Result<Value> {
    r.observe(serde_json::from_value(request).unwrap(), bytes)
}
fn prepare_observed(
    r: &mut Registry,
    owner: u64,
    revision: u64,
    row: Value,
    data: &[u8],
) -> Result<u64> {
    Ok(observed(
        r,
        json!({"op":"prepare","owner":owner,"revision":revision,"rows":[row],"rebuild":false}),
        data,
    )?["ticket"]
        .as_u64()
        .unwrap())
}
fn commit_observed(r: &mut Registry, owner: u64, ticket: u64, data: &[u8]) -> Result<Value> {
    call(r, json!({"op":"begin_apply","owner":owner,"ticket":ticket}))?;
    observed(
        r,
        json!({"op":"commit","owner":owner,"ticket":ticket}),
        data,
    )
}

fn live_owner(r: &mut Registry) -> u64 {
    let owner = create(r, vec![]);
    call(r, json!({"op":"track","owner":owner})).unwrap();
    owner
}
fn capture_live(r: &mut Registry, owner: u64, data: &[u8], rebuild: bool) -> (u64, Value) {
    let revision = info(r, owner)["revision"].as_u64().unwrap();
    let ticket = observed(
        r,
        json!({"op":"capture","owner":owner,"revision":revision,"rebuild":rebuild}),
        data,
    )
    .unwrap()["ticket"]
        .as_u64()
        .unwrap();
    let rows = call(
        r,
        json!({"op":"ticket_rows","owner":owner,"ticket":ticket,"offset":0,"limit":1024}),
    )
    .unwrap()["rows"]
        .clone();
    (ticket, rows)
}

#[test]
fn live_restore_diff_uses_payload_identity() {
    let mut r = Registry::default();
    let owner = live_owner(&mut r);
    let before = witness(1, b"before");
    let after = witness(1, b"after");
    let (ticket, _) = capture_live(&mut r, owner, &before, false);
    commit_observed(&mut r, owner, ticket, &before).unwrap();
    let saved = call(&mut r, json!({"op":"snapshot","owner":owner})).unwrap()["snapshot"].clone();
    call(
        &mut r,
        json!({"op":"mark","owner":owner,"ids":[row(1)["id"]],"moved":false}),
    )
    .unwrap();
    let (ticket, _) = capture_live(&mut r, owner, &after, false);
    commit_observed(&mut r, owner, ticket, &after).unwrap();
    let revision = info(&mut r, owner)["revision"].clone();
    let ticket = call(
        &mut r,
        json!({"op":"prepare_restore","owner":owner,"revision":revision,"snapshot":saved}),
    )
    .unwrap()["ticket"]
        .clone();
    let diff = call(
        &mut r,
        json!({"op":"ticket_diff","owner":owner,"ticket":ticket}),
    )
    .unwrap();
    assert_eq!(diff["removed"].as_array().unwrap().len(), 1);
    assert_eq!(diff["removed"][0]["id"], row(1)["id"]);
    assert_eq!(diff["added"], json!([row(1)["id"]]));
}

#[test]
fn live_order_only_bypass_refuses_and_sparse_restore_requires_normalization() {
    fn two(reverse: bool) -> Vec<u8> {
        let mut data = b"NWOBS01\0".to_vec();
        data.extend_from_slice(&2u32.to_le_bytes());
        for id in if reverse { [2, 1] } else { [1, 2] } {
            data.extend_from_slice(&witness(id, b"shape")[12..]);
        }
        data
    }
    let mut r = Registry::default();
    let owner = live_owner(&mut r);
    let original = two(false);
    let reordered = two(true);
    let (ticket, _) = capture_live(&mut r, owner, &original, false);
    commit_observed(&mut r, owner, ticket, &original).unwrap();
    let saved = call(&mut r, json!({"op":"snapshot","owner":owner})).unwrap()["snapshot"].clone();
    let revision = info(&mut r, owner)["revision"].clone();
    assert_eq!(
        observed(
            &mut r,
            json!({"op":"capture","owner":owner,"revision":revision,"rebuild":false}),
            &reordered
        ),
        Err("unmediated_object_index_write")
    );
    let (ticket, _) = capture_live(&mut r, owner, &reordered, true);
    commit_observed(&mut r, owner, ticket, &reordered).unwrap();
    let revision = info(&mut r, owner)["revision"].clone();
    let ticket = call(
        &mut r,
        json!({"op":"prepare_restore","owner":owner,"revision":revision,"snapshot":saved}),
    )
    .unwrap()["ticket"]
        .as_u64()
        .unwrap();
    assert_eq!(
        call(
            &mut r,
            json!({"op":"ticket_diff","owner":owner,"ticket":ticket})
        )
        .unwrap(),
        json!({"removed":[],"added":[]})
    );
    commit_observed(&mut r, owner, ticket, &reordered).unwrap();
    assert_eq!(
        call(&mut r, json!({"op":"order_pending","owner":owner})).unwrap()["pending"],
        true
    );
    assert_eq!(
        call(&mut r, json!({"op":"normalize_plan","owner":owner})).unwrap()["ids"],
        json!([row(1)["id"], row(2)["id"]])
    );
    let (ticket, _) = capture_live(&mut r, owner, &original, false);
    commit_observed(&mut r, owner, ticket, &original).unwrap();
    assert_eq!(
        call(&mut r, json!({"op":"order_pending","owner":owner})).unwrap()["pending"],
        false
    );
    assert_eq!(
        call(&mut r, json!({"op":"normalize_plan","owner":owner})).unwrap()["ids"],
        json!([])
    );
}

#[test]
fn live_snapshots_share_unchanged_object_witness_storage() {
    fn batch(first: u8) -> Vec<u8> {
        let mut data = b"NWOBS01\0".to_vec();
        data.extend_from_slice(&64u32.to_le_bytes());
        for n in 1..=64 {
            let geometry = vec![if n == 1 { first } else { 0 }; 64 * 1024];
            data.extend_from_slice(&witness(n, &geometry)[12..]);
        }
        data
    }
    let mut r = Registry::default();
    let owner = live_owner(&mut r);
    let initial = batch(0);
    let (ticket, _) = capture_live(&mut r, owner, &initial, false);
    commit_observed(&mut r, owner, ticket, &initial).unwrap();
    call(&mut r, json!({"op":"snapshot","owner":owner})).unwrap();
    let base = r.retained.load(Ordering::Relaxed);
    for _ in 0..10 {
        let (ticket, _) = capture_live(&mut r, owner, &initial, false);
        commit_observed(&mut r, owner, ticket, &initial).unwrap();
        call(&mut r, json!({"op":"snapshot","owner":owner})).unwrap();
    }
    assert_eq!(
        r.retained.load(Ordering::Relaxed),
        base,
        "unchanged snapshots retain the same state"
    );
    for n in 1..=20 {
        call(
            &mut r,
            json!({"op":"mark","owner":owner,"ids":[row(1)["id"]],"moved":false}),
        )
        .unwrap();
        let data = batch(n);
        let (ticket, _) = capture_live(&mut r, owner, &data, false);
        commit_observed(&mut r, owner, ticket, &data).unwrap();
        call(&mut r, json!({"op":"snapshot","owner":owner})).unwrap();
    }
    assert!(
        r.retained.load(Ordering::Relaxed) - base < 4 * 1024 * 1024,
        "assertion failed: twenty one-object edits must not retain twenty complete observation batches"
    );
    call(&mut r, json!({"op":"close","owner":owner})).unwrap();
    assert_eq!(r.retained.load(Ordering::Relaxed), 0);
}

#[test]
fn live_journal_capture_reuses_exact_payloads_and_rejects_unmarked_writes() {
    let mut r = Registry::default();
    let owner = live_owner(&mut r);
    let before = witness(1, b"before");
    let after = witness(1, b"after");
    let (ticket, initial) = capture_live(&mut r, owner, &before, false);
    commit_observed(&mut r, owner, ticket, &before).unwrap();
    let revision = info(&mut r, owner)["revision"].clone();
    call(
        &mut r,
        json!({"op":"mark","owner":owner,"ids":[row(1)["id"]],"moved":false}),
    )
    .unwrap();
    assert_eq!(info(&mut r, owner)["authoritative"], false);
    let (ticket, unchanged) = capture_live(&mut r, owner, &before, false);
    assert_eq!(initial, unchanged);
    commit_observed(&mut r, owner, ticket, &before).unwrap();
    assert_eq!(info(&mut r, owner)["revision"], revision);
    let revision = revision.as_u64().unwrap();
    assert_eq!(
        observed(
            &mut r,
            json!({"op":"capture","owner":owner,"revision":revision,"rebuild":false}),
            &after
        ),
        Err("unmediated_object_payload_write")
    );
    assert_eq!(info(&mut r, owner)["authoritative"], false);
    let (ticket, rebuilt) = capture_live(&mut r, owner, &after, true);
    assert_ne!(initial[0]["payload"], rebuilt[0]["payload"]);
    commit_observed(&mut r, owner, ticket, &after).unwrap();
    assert_eq!(info(&mut r, owner)["authoritative"], true);
    assert_eq!(
        call(&mut r, json!({"op":"dirty","owner":owner})).unwrap(),
        json!({"all":false,"rows":[]})
    );
}

#[test]
fn live_index_and_associations_are_native_and_unknown_membership_invalidates() {
    let mut r = Registry::default();
    let owner = live_owner(&mut r);
    let before = witness(1, b"shape");
    let (ticket, initial) = capture_live(&mut r, owner, &before, false);
    commit_observed(&mut r, owner, ticket, &before).unwrap();
    call(
        &mut r,
        json!({"op":"bind_display","owner":owner,"id":row(1)["id"],"display":"Display1"}),
    )
    .unwrap();
    let (ticket, bound) = capture_live(&mut r, owner, &before, false);
    assert_eq!(initial[0]["payload"], bound[0]["payload"]);
    assert_eq!(bound[0]["display"], "Display1");
    commit_observed(&mut r, owner, ticket, &before).unwrap();
    let revision = info(&mut r, owner)["revision"].clone();
    let empty = b"NWIDX01\0\0\0\0\0";
    assert_eq!(
        observed(
            &mut r,
            json!({"op":"index","owner":owner,"revision":revision}),
            empty
        ),
        Err("unmediated_object_index_write")
    );
    assert_eq!(info(&mut r, owner)["authoritative"], false);
}

#[test]
fn witnessed_changes_require_fresh_payload_and_confirmed_host_state() {
    let mut r = Registry::default();
    let owner = create(&mut r, vec![]);
    let before = witness(1, b"point at one");
    let after = witness(1, b"point at ninety nine");
    let ticket = prepare_observed(&mut r, owner, 0, row(1), &before).unwrap();
    call(
        &mut r,
        json!({"op":"begin_apply","owner":owner,"ticket":ticket}),
    )
    .unwrap();
    assert_eq!(
        call(&mut r, json!({"op":"commit","owner":owner,"ticket":ticket})),
        Err("observation_confirmation_required")
    );
    observed(
        &mut r,
        json!({"op":"commit","owner":owner,"ticket":ticket}),
        &before,
    )
    .unwrap();
    assert_eq!(info(&mut r, owner)["revision"], 1);
    assert_eq!(
        prepare_observed(&mut r, owner, 1, row(1), &after),
        Err("changed_object_reused_payload")
    );
    assert_eq!(info(&mut r, owner)["pending"], false);
    let mut replacement = row(1);
    replacement["payload"] = json!(2);
    let ticket = prepare_observed(&mut r, owner, 1, replacement, &after).unwrap();
    assert_eq!(
        commit_observed(&mut r, owner, ticket, &before),
        Err("observation_commit_mismatch")
    );
    assert_eq!(info(&mut r, owner)["authoritative"], false);
    assert_eq!(info(&mut r, owner)["pending"], false);
    assert_eq!(
        call(&mut r, json!({"op":"abort","owner":owner,"ticket":ticket})),
        Err("invalid_ticket")
    );
}

#[test]
fn witnessed_snapshot_restore_and_unmediated_change_invalidation() {
    let mut r = Registry::default();
    let owner = create(&mut r, vec![]);
    let before = witness(1, b"first");
    let after = witness(1, b"second");
    let ticket = prepare_observed(&mut r, owner, 0, row(1), &before).unwrap();
    commit_observed(&mut r, owner, ticket, &before).unwrap();
    let snap = call(&mut r, json!({"op":"snapshot","owner":owner})).unwrap()["snapshot"]
        .as_u64()
        .unwrap();
    let retained = r.retained.load(Ordering::Relaxed);
    let noop = prepare_observed(&mut r, owner, 1, row(1), &before).unwrap();
    assert_eq!(
        commit_observed(&mut r, owner, noop, &before).unwrap()["revision"],
        1
    );
    assert!(r.retained.load(Ordering::Relaxed) >= retained);
    let mut replacement = row(1);
    replacement["payload"] = json!(2);
    let ticket = prepare_observed(&mut r, owner, 1, replacement, &after).unwrap();
    commit_observed(&mut r, owner, ticket, &after).unwrap();
    let ticket = call(
        &mut r,
        json!({"op":"prepare_restore","owner":owner,"revision":2,"snapshot":snap}),
    )
    .unwrap()["ticket"]
        .as_u64()
        .unwrap();
    commit_observed(&mut r, owner, ticket, &before).unwrap();
    assert_eq!(
        observed(
            &mut r,
            json!({"op":"check","owner":owner,"revision":3}),
            &before
        )
        .unwrap()["matches"],
        true
    );
    let reply = observed(
        &mut r,
        json!({"op":"check","owner":owner,"revision":3}),
        &after,
    )
    .unwrap();
    assert_eq!(reply["matches"], false);
    assert_eq!(reply["revision"], 4);
    assert_eq!(info(&mut r, owner)["authoritative"], false);
    assert_eq!(
        call(&mut r, json!({"op":"snapshot","owner":owner})),
        Err("authority_invalid")
    );
}

#[test]
fn witnessed_states_cannot_be_downgraded_to_unobserved_edits() {
    let mut r = Registry::default();
    let owner = create(&mut r, vec![]);
    let bytes = witness(1, b"geometry");
    let unobserved = call(&mut r, json!({"op":"snapshot","owner":owner})).unwrap()["snapshot"]
        .as_u64()
        .unwrap();
    let ticket = prepare_observed(&mut r, owner, 0, row(1), &bytes).unwrap();
    commit_observed(&mut r, owner, ticket, &bytes).unwrap();
    assert_eq!(
        call(
            &mut r,
            json!({"op":"prepare_restore","owner":owner,"revision":1,"snapshot":unobserved})
        ),
        Err("observation_required")
    );
    assert_eq!(
        call(
            &mut r,
            json!({"op":"prepare","owner":owner,"revision":1,"rows":[row(1)],"rebuild":false})
        ),
        Err("observation_required")
    );
    assert_eq!(
        call(
            &mut r,
            json!({"op":"prepare_edits","owner":owner,"revision":1,"edits":[]})
        ),
        Err("observation_required")
    );
    let mut wrong_name = row(1);
    wrong_name["name"] = json!("wrong");
    assert_eq!(
        prepare_observed(&mut r, owner, 1, wrong_name, &bytes),
        Err("observation_name_mismatch")
    );
    let mut missing_payload = row(1);
    missing_payload["payload"] = Value::Null;
    assert_eq!(
        prepare_observed(&mut r, owner, 1, missing_payload, &bytes),
        Err("observation_payload_required")
    );
    call(&mut r, json!({"op":"close","owner":owner})).unwrap();
    assert_eq!(r.retained.load(Ordering::Relaxed), 0);
}

#[test]
fn payload_reservations_are_nonpointer_monotonic_and_bounded() {
    let mut r = Registry::default();
    let owner = create(&mut r, vec![]);
    let first = call(
        &mut r,
        json!({"op":"reserve_payloads","owner":owner,"count":3}),
    )
    .unwrap();
    assert_eq!(first["count"], 3);
    assert_eq!(first["first"], owner + 1);
    assert_eq!(info(&mut r, owner)["revision"], 0);
    for count in [0, MAX_ROWS + 1] {
        assert_eq!(
            call(
                &mut r,
                json!({"op":"reserve_payloads","owner":owner,"count":count})
            ),
            Err("payload_reservation_limit")
        );
    }
    let next = call(
        &mut r,
        json!({"op":"reserve_payloads","owner":owner,"count":1}),
    )
    .unwrap();
    assert_eq!(next["first"], owner + 4);
    call(&mut r, json!({"op":"close","owner":owner})).unwrap();
    assert_eq!(
        call(
            &mut r,
            json!({"op":"reserve_payloads","owner":owner,"count":1})
        ),
        Err("invalid_owner")
    );
    assert!(create(&mut r, vec![]) > owner + 4);
}

#[test]
fn batched_edits_preserve_order_associations_and_fail_atomically() {
    let mut r = Registry::default();
    let a = create(&mut r, vec![row(1), row(2), row(3)]);
    let edits = json!([
        {"kind":"delete","id":row(2)["id"]},
        {"kind":"replace","row":row(1),"append":true},
        {"kind":"bind","id":row(3)["id"],"display":"Display3"},
        {"kind":"add","row":row(4)}
    ]);
    let t = call(
        &mut r,
        json!({"op":"prepare_edits","owner":a,"revision":0,"edits":edits}),
    )
    .unwrap()["ticket"]
        .as_u64()
        .unwrap();
    apply(&mut r, a, t);
    let mut third = row(3);
    third["display"] = json!("Display3");
    let actual = call(&mut r, json!({"op":"rows","owner":a,"offset":0,"limit":10})).unwrap();
    assert_eq!(actual["rows"], json!([third, row(1), row(4)]));
    assert_eq!(
        call(
            &mut r,
            json!({"op":"prepare_edits","owner":a,"revision":1,"edits":[
                {"kind":"delete","id":row(1)["id"]}, {"kind":"add","row":row(3)}
            ]})
        ),
        Err("duplicate_uuid")
    );
    assert_eq!(info(&mut r, a)["count"], 3);
    assert_eq!(info(&mut r, a)["pending"], false);
}

#[test]
fn resource_limits_and_exhaustion_refuse_without_partial_publication() {
    let mut r = Registry::default();
    let a = create(&mut r, vec![row(1)]);
    let charged = r.retained.load(Ordering::Relaxed);
    r.retained.store(MAX_RETAINED, Ordering::Relaxed);
    assert_eq!(
        call(
            &mut r,
            json!({"op":"prepare","owner":a,"revision":0,"rows":[row(2)],"rebuild":false})
        ),
        Err("retained_limit")
    );
    r.retained.store(charged, Ordering::Relaxed);
    assert_eq!(info(&mut r, a)["pending"], false);
    assert_eq!(info(&mut r, a)["revision"], 0);
    let mut bad = row(2);
    bad["name"] = json!("x".repeat(MAX_TEXT + 1));
    assert_eq!(
        call(&mut r, json!({"op":"create","rows":[bad]})),
        Err("text_limit")
    );
    let before = r.retained.load(Ordering::Relaxed);
    for _ in 0..MAX_SNAPSHOTS {
        call(&mut r, json!({"op":"snapshot","owner":a})).unwrap();
    }
    assert_eq!(
        r.retained.load(Ordering::Relaxed),
        before,
        "snapshots share immutable state"
    );
    assert_eq!(
        call(&mut r, json!({"op":"snapshot","owner":a})),
        Err("snapshot_limit")
    );
    r.next = u64::MAX;
    assert_eq!(
        call(
            &mut r,
            json!({"op":"prepare","owner":a,"revision":0,"rows":[],"rebuild":false})
        ),
        Err("handle_exhausted")
    );
    assert_eq!(r.retained.load(Ordering::Relaxed), before);
    assert_eq!(info(&mut r, a)["pending"], false);
}

#[test]
fn identity_order_duplicate_names_and_noop_revision() {
    let mut r = Registry::default();
    let owner = create(&mut r, vec![row(2), row(1)]);
    let page = call(
        &mut r,
        json!({"op":"rows","owner":owner,"offset":0,"limit":10}),
    )
    .unwrap();
    assert_eq!(page["rows"], json!([row(2), row(1)]));
    let found = call(
        &mut r,
        json!({"op":"find_name","owner":owner,"name":"SAME"}),
    )
    .unwrap();
    assert_eq!(found["id"], row(2)["id"]);
    let ticket = prepare(&mut r, owner, 0, vec![row(2), row(1)]);
    assert_eq!(apply(&mut r, owner, ticket)["revision"], 0);
    let ticket = prepare(&mut r, owner, 0, vec![row(1), row(2)]);
    assert_eq!(apply(&mut r, owner, ticket)["revision"], 1);
}

#[test]
fn stale_and_cross_owner_tickets_refuse_without_consuming() {
    let mut r = Registry::default();
    let a = create(&mut r, vec![]);
    let b = create(&mut r, vec![]);
    assert_eq!(
        call(
            &mut r,
            json!({"op":"prepare","owner":a,"revision":1,"rows":[],"rebuild":false})
        ),
        Err("stale_revision")
    );
    let ticket = prepare(&mut r, a, 0, vec![row(1)]);
    assert_eq!(
        call(
            &mut r,
            json!({"op":"begin_apply","owner":b,"ticket":ticket})
        ),
        Err("invalid_ticket")
    );
    assert_eq!(
        call(&mut r, json!({"op":"commit","owner":a,"ticket":ticket})),
        Err("apply_not_started")
    );
    assert_eq!(info(&mut r, a)["revision"], 0);
    assert_eq!(apply(&mut r, a, ticket)["revision"], 1);
    assert_eq!(
        call(&mut r, json!({"op":"commit","owner":a,"ticket":ticket})),
        Err("invalid_ticket")
    );
}

#[test]
fn prepared_abort_is_safe_but_applied_abort_is_not() {
    let mut r = Registry::default();
    let a = create(&mut r, vec![row(1)]);
    let t = prepare(&mut r, a, 0, vec![]);
    call(&mut r, json!({"op":"abort","owner":a,"ticket":t})).unwrap();
    assert_eq!(info(&mut r, a)["revision"], 0);
    let t = prepare(&mut r, a, 0, vec![]);
    call(&mut r, json!({"op":"begin_apply","owner":a,"ticket":t})).unwrap();
    assert_eq!(info(&mut r, a)["authoritative"], false);
    assert_eq!(
        call(&mut r, json!({"op":"abort","owner":a,"ticket":t})),
        Err("possibly_applied")
    );
    assert_eq!(
        call(&mut r, json!({"op":"snapshot","owner":a})),
        Err("transaction_pending")
    );
    call(&mut r, json!({"op":"invalidate","owner":a})).unwrap();
    assert_eq!(
        call(&mut r, json!({"op":"commit","owner":a,"ticket":t})),
        Err("invalid_ticket")
    );
    assert_eq!(
        call(&mut r, json!({"op":"rows","owner":a,"offset":0,"limit":1})),
        Err("authority_invalid")
    );
    assert_eq!(
        call(
            &mut r,
            json!({"op":"prepare","owner":a,"revision":1,"rows":[],"rebuild":false})
        ),
        Err("authority_invalid")
    );
    let t = call(
        &mut r,
        json!({"op":"prepare","owner":a,"revision":1,"rows":[],"rebuild":true}),
    )
    .unwrap()["ticket"]
        .as_u64()
        .unwrap();
    assert_eq!(apply(&mut r, a, t)["revision"], 2);
    assert_eq!(info(&mut r, a)["authoritative"], true);
}

#[test]
fn immutable_snapshots_are_owner_scoped_and_restore_is_transactional() {
    let mut r = Registry::default();
    let a = create(&mut r, vec![row(1), row(2)]);
    let b = create(&mut r, vec![]);
    let s = call(&mut r, json!({"op":"snapshot","owner":a})).unwrap()["snapshot"]
        .as_u64()
        .unwrap();
    let t = prepare(&mut r, a, 0, vec![row(2)]);
    apply(&mut r, a, t);
    assert_eq!(
        call(
            &mut r,
            json!({"op":"prepare_restore","owner":b,"revision":0,"snapshot":s})
        ),
        Err("invalid_snapshot")
    );
    let t = call(
        &mut r,
        json!({"op":"prepare_restore","owner":a,"revision":1,"snapshot":s}),
    )
    .unwrap()["ticket"]
        .as_u64()
        .unwrap();
    assert_eq!(info(&mut r, a)["count"], 1);
    assert_eq!(apply(&mut r, a, t)["count"], 2);
    call(
        &mut r,
        json!({"op":"release_snapshot","owner":a,"snapshot":s}),
    )
    .unwrap();
    assert_eq!(
        call(
            &mut r,
            json!({"op":"prepare_restore","owner":a,"revision":2,"snapshot":s})
        ),
        Err("invalid_snapshot")
    );
}

#[test]
fn malformed_rows_and_bounded_queries_refuse() {
    let mut r = Registry::default();
    assert_eq!(
        call(&mut r, json!({"op":"create","rows":[row(1),row(1)]})),
        Err("duplicate_uuid")
    );
    for id in [
        "",
        "00000000-0000-0000-0000-000000000000",
        "00000000-0000-0000-0000-00000000000G",
    ] {
        let mut bad = row(1);
        bad["id"] = json!(id);
        assert_eq!(
            call(&mut r, json!({"op":"create","rows":[bad]})),
            Err("invalid_uuid")
        );
    }
    let mut bad = row(1);
    bad["payload"] = json!(0);
    assert_eq!(
        call(&mut r, json!({"op":"create","rows":[bad]})),
        Err("invalid_payload")
    );
    let a = create(&mut r, vec![]);
    assert_eq!(
        call(
            &mut r,
            json!({"op":"rows","owner":a,"offset":0,"limit":MAX_PAGE+1})
        ),
        Err("page_limit")
    );
    assert_eq!(
        call(&mut r, json!({"op":"rows","owner":a,"offset":1,"limit":1})),
        Err("invalid_offset")
    );
    assert!(serde_json::from_value::<Request>(json!({"op":"info","owner":a,"extra":0})).is_err());
}

#[test]
fn close_releases_memory_snapshots_and_pending_state_handles_never_reuse() {
    let mut r = Registry::default();
    let a = create(&mut r, vec![row(1)]);
    call(&mut r, json!({"op":"snapshot","owner":a})).unwrap();
    prepare(&mut r, a, 0, vec![row(2)]);
    assert!(r.retained.load(Ordering::Relaxed) > 0);
    call(&mut r, json!({"op":"close","owner":a})).unwrap();
    assert_eq!(r.retained.load(Ordering::Relaxed), 0);
    let b = create(&mut r, vec![]);
    assert!(b > a);
    assert_eq!(
        call(&mut r, json!({"op":"info","owner":a})),
        Err("invalid_owner")
    );
}

#[test]
fn second_prepare_and_repeated_begin_are_refused() {
    let mut r = Registry::default();
    let a = create(&mut r, vec![]);
    let t = prepare(&mut r, a, 0, vec![row(1)]);
    assert_eq!(
        call(
            &mut r,
            json!({"op":"prepare","owner":a,"revision":0,"rows":[],"rebuild":false})
        ),
        Err("transaction_pending")
    );
    call(&mut r, json!({"op":"begin_apply","owner":a,"ticket":t})).unwrap();
    assert_eq!(
        call(&mut r, json!({"op":"begin_apply","owner":a,"ticket":t})),
        Err("possibly_applied")
    );
}
