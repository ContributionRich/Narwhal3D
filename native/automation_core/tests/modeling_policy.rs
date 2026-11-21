//! Integration tests for the generalized modeling authority policy:
//! default Review vs explicit Build; Undo requiring an explicit trusted request AND
//! the exact latest AI undo serial (new external serials refused; own separate
//! batch); MCP external_modeling enforcing local Review even after a Build gate,
//! with validate_mcp rejecting unknown request fields; canonical-result
//! forgery/replay refusal. Public API only; no mocks, host or geometry.

use narwhal_automation::conversation::{Binding, Config, Conversation, Token};
use narwhal_automation::modeling::{self, Batch, Context, Gate, Mode, Ticket};
use narwhal_automation::provider::Provider;
use serde_json::{Map, Value, json};

const UUID: &str = "00000000-0000-0000-0000-000000000001";

fn context(undo_top_serial: u64) -> Context {
    Context {
        document_session: "doc".into(),
        fingerprint: "a".repeat(64),
        units: "Millimeters".into(),
        tolerance: 0.001,
        command_idle: true,
        undo_ready: true,
        layer_available: true,
        undo_top_serial,
    }
}

fn box_batch() -> Batch {
    serde_json::from_value(json!({"label": "part", "steps": [
        {"id": "part", "name": "Part", "publish": true, "replace": null,
         "action": {"op": "box", "origin": [10, 20, 0], "size": [4, 8, 12]}}]}))
    .unwrap()
}

fn undo_batch() -> Batch {
    serde_json::from_value(json!({"label": "undo", "steps": [
        {"id": "undo", "name": "Undo", "publish": true, "replace": null,
         "action": {"op": "undo"}}]}))
    .unwrap()
}

/// Host receipt for `box_batch()`: exact bounds/volume; records undo serial 7 as
/// the latest AI record a gate may later undo.
fn box_output() -> Value {
    json!({"applied": true, "undo_serial": 7, "objects": [
        {"step": "part", "object_id": UUID, "valid": true, "solid": true, "airtight": true,
         "volume": 384.0, "bounds": [[10, 20, 0], [14, 28, 12]]}]})
}

fn owner() -> Token {
    Token {
        conversation: 1,
        generation: 1,
        request: 1,
    }
}

fn ticket(v: &Value) -> Ticket {
    serde_json::from_value(v["ticket"].clone()).unwrap()
}

fn binding() -> Binding {
    Binding {
        config: Config {
            provider_id: Provider::Openai,
            model: "fixture".into(),
            endpoint: "http://127.0.0.1/v1".into(),
            credential_env: "NARWHAL_TEST_CREDENTIAL".into(),
            allow_insecure_remote: false,
            include_selection: false,
        },
        document_context: "doc".into(),
        allow_reads: true,
    }
}

/// Approve, consume and publish `box_batch()` as one AI record on a fresh call id.
fn publish_box(gate: &mut Gate, call_id: &str, serial: u64) {
    let staged = gate
        .stage(owner(), call_id.into(), box_batch(), context(serial))
        .unwrap();
    let t = ticket(&staged);
    gate.decide(&t, &context(serial), true).unwrap();
    gate.consume(&t, &context(serial)).unwrap();
    gate.publication(&t, box_output(), false).unwrap();
}

#[test]
fn modeling_receipts_do_not_fall_back_to_box_only_evidence() {
    let mut gate = Gate::default();
    assert_eq!(gate.evidence()["known_batches"], 0);
    publish_box(&mut gate, "built", 0);
    let evidence = gate.evidence();
    assert_eq!(evidence["applied_batches"], 1);
    assert_eq!(evidence["last_objects"], 1);
    assert_eq!(evidence["current_geometry_verified"], false);
    assert!(
        evidence["status_text"]
            .as_str()
            .unwrap()
            .contains("measured at publication")
    );
    gate.request_intent("Undo that last change");
    let staged = gate
        .stage(owner(), "undo".into(), undo_batch(), context(7))
        .unwrap();
    let ticket = ticket(&staged);
    gate.decide(&ticket, &context(7), true).unwrap();
    gate.consume(&ticket, &context(7)).unwrap();
    gate.publication(
        &ticket,
        json!({"applied":true,"objects":[],"undo_performed":true}),
        false,
    )
    .unwrap();
    let evidence = gate.evidence();
    assert_eq!(evidence["known_batches"], 2);
    assert_eq!(evidence["object_results"], 1);
    assert_eq!(evidence["last_undo"], true);
    assert!(evidence["status_text"].as_str().unwrap().contains("undone"));
}

#[test]
fn default_mode_is_review_and_explicit_build_autoruns() {
    let mut gate = Gate::default();
    assert_eq!(gate.mode(), Mode::Review);
    let staged = gate
        .stage(owner(), "r".into(), box_batch(), context(0))
        .unwrap();
    assert_eq!(staged["status"], "modeling_review_required");
    let t = ticket(&staged);
    assert_eq!(
        gate.consume(&t, &context(0)).unwrap_err(),
        "modeling_approval_required"
    );
    gate.decide(&t, &context(0), true).unwrap();
    gate.consume(&t, &context(0)).unwrap();
    gate.publication(&t, box_output(), false).unwrap();
    gate.set_mode(Mode::Build).unwrap();
    let staged = gate
        .stage(owner(), "b".into(), box_batch(), context(0))
        .unwrap();
    assert_eq!(staged["status"], "modeling_approved");
    gate.consume(&ticket(&staged), &context(0)).unwrap();
}

#[test]
fn undo_requires_explicit_request_latest_ai_serial_and_own_batch() {
    let mut gate = Gate::default();
    let err = "undo_requires_explicit_request_and_latest_ai_record";
    gate.request_intent("make another box");
    assert_eq!(
        gate.stage(owner(), "u1".into(), undo_batch(), context(7))
            .unwrap_err(),
        err
    );
    gate.request_intent("undo");
    assert_eq!(
        gate.stage(owner(), "u2".into(), undo_batch(), context(0))
            .unwrap_err(),
        err
    );
    // Publish one AI record (serial 7); only that serial is a legal Undo target.
    publish_box(&mut gate, "p", 7);
    let staged = gate
        .stage(owner(), "u3".into(), undo_batch(), context(7))
        .unwrap();
    assert_eq!(staged["status"], "modeling_review_required");
    // An external action advancing the stack past the AI record is refused.
    publish_box(&mut gate, "q", 3);
    assert_eq!(
        gate.stage(owner(), "u4".into(), undo_batch(), context(8))
            .unwrap_err(),
        err
    );

    // Undo must be a solo published batch; mixed or silent shapes are rejected.
    let mix_err = "undo_must_be_separate_batch";
    for shape in [
        json!({"label": "undo+box", "steps": [
            {"id": "undo", "name": "Undo", "publish": true, "replace": null, "action": {"op": "undo"}},
            {"id": "part", "name": "Part", "publish": true, "replace": null,
             "action": {"op": "box", "origin": [0, 0, 0], "size": [4, 8, 12]}}]}),
        json!({"label": "silent", "steps": [
            {"id": "undo", "name": "Undo", "publish": false, "replace": null, "action": {"op": "undo"}}]}),
    ] {
        let b: Batch = serde_json::from_value(shape).unwrap();
        assert_eq!(b.validate(0.001), Err(mix_err));
    }
    assert!(undo_batch().validate(0.001).is_ok());
    gate.request_intent("undo");
    assert_eq!(
        gate.stage(owner(), "u5".into(), undo_batch(), context(0))
            .unwrap_err(),
        err
    );
}

#[test]
fn mcp_external_modeling_is_review_only_and_strict() {
    let binding = binding();
    let mut conv = Conversation::new(1, binding.clone()).unwrap();
    conv.modeling_mode(Mode::Build).unwrap(); // a previously user-authorized gate
    let staged = conv
        .external_modeling(binding.clone(), "req-1".into(), box_batch(), context(0))
        .unwrap();
    assert_eq!(staged["status"], "modeling_review_required");
    assert_eq!(staged["mode"], "review");
    // A pending batch also blocks a replayed request id (mode cannot switch over it).
    assert_eq!(
        conv.external_modeling(binding.clone(), "req-1".into(), box_batch(), context(0))
            .unwrap_err(),
        "modeling_pending"
    );
    let t = ticket(&staged);
    let forged = Ticket {
        owner: Token {
            conversation: 2,
            generation: t.owner.generation,
            request: t.owner.request,
        },
        call_id: t.call_id.clone(),
        serial: t.serial,
    };
    assert_eq!(
        conv.decide_modeling(forged, binding.clone(), context(0), true)
            .unwrap_err(),
        "stale_modeling_ticket"
    );

    // validate_mcp: exact request shape only — no mode, no extras, no unknown fields.
    let valid = Map::from_iter([
        ("request_id".into(), json!("req-2")),
        ("batch".into(), serde_json::to_value(box_batch()).unwrap()),
    ]);
    let reject = |name: &str, m: &Map<String, Value>| {
        assert_eq!(modeling::validate_mcp(name, m), Err("invalid_arguments"));
    };
    assert!(modeling::validate_mcp("narwhal.model", &valid).is_ok());
    let mut extra = valid.clone();
    extra.insert("mode".into(), json!("build"));
    reject("narwhal.model", &extra);
    let mut bad_batch = valid.clone();
    let mut b = serde_json::to_value(box_batch()).unwrap();
    b["steps"][0]["action"]["code"] = json!("eval");
    bad_batch.insert("batch".into(), b);
    assert_eq!(
        modeling::validate_mcp("narwhal.model", &bad_batch),
        Err("invalid_modeling_batch")
    );
    let mut result_args = Map::from_iter([("request_id".into(), json!("req-2"))]);
    assert!(modeling::validate_mcp("narwhal.model_result", &result_args).is_ok());
    result_args.insert("force".into(), json!(true));
    reject("narwhal.model_result", &result_args);
    assert_eq!(
        modeling::validate_mcp("narwhal.exec", &valid),
        Err("tool_not_allowed")
    );
}

#[test]
fn canonical_result_forgery_and_replay_are_refused() {
    let mut gate = Gate::default();
    gate.set_mode(Mode::Build).unwrap();
    let staged = gate
        .stage(owner(), "x".into(), box_batch(), context(0))
        .unwrap();
    let t = ticket(&staged);
    gate.consume(&t, &context(0)).unwrap();
    gate.publication(&t, box_output(), false).unwrap();
    let canonical = gate.result(&t).unwrap();
    assert!(gate.check_result(&owner(), &canonical).is_ok());
    let mut forged = canonical.clone();
    forged.output["objects"][0]["volume"] = json!(1.0);
    assert_eq!(
        gate.check_result(&owner(), &forged).unwrap_err(),
        "modeling_result_mismatch"
    );
    let mut flipped = canonical.clone();
    flipped.is_error = true;
    assert_eq!(
        gate.check_result(&owner(), &flipped).unwrap_err(),
        "modeling_result_mismatch"
    );
    // A different conversation's owner may not read this ticket's result.
    assert_eq!(
        gate.check_result(
            &Token {
                conversation: 2,
                ..owner()
            },
            &canonical
        )
        .unwrap_err(),
        "stale_modeling_ticket"
    );
    // Replay: spent ticket, spent decision, and the call id can never re-stage.
    assert_eq!(
        gate.decide(&t, &context(0), true).unwrap_err(),
        "modeling_ticket_spent"
    );
    assert_eq!(
        gate.consume(&t, &context(0)).unwrap_err(),
        "modeling_ticket_spent"
    );
    assert_eq!(
        gate.publication(&t, box_output(), false).unwrap_err(),
        "modeling_publication_spent"
    );
    assert_eq!(
        gate.stage(owner(), "x".into(), box_batch(), context(0))
            .unwrap_err(),
        "modeling_call_replayed"
    );
    assert_eq!(
        gate.result(&Ticket {
            serial: 99,
            ..t.clone()
        })
        .unwrap_err(),
        "stale_modeling_ticket"
    );
}
