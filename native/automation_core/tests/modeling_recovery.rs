//! Q3 — Modeling recovery: malformed arguments yield canonical recoverable
//! errors, a corrected round can stage/approve/consume, canceled or stale
//! owners cannot stage or consume, forged results are refused, and budgets
//! remain bounded without refunding applied call IDs.
//!
//! Public Conversation API only; deterministic OpenAI-compatible chat
//! completions JSON; no mocks, host, or geometry.

use narwhal_automation::conversation::{Binding, Config, Conversation, Token};
use narwhal_automation::modeling::{Context, Ticket};
use narwhal_automation::provider::{Provider, ToolResult};
use serde_json::{Value, json};

fn binding() -> Binding {
    Binding {
        config: Config {
            provider_id: Provider::OpenaiCompatible,
            model: "fixture".into(),
            endpoint: "http://127.0.0.1/v1".into(),
            credential_env: "NARWHAL_TEST_CREDENTIAL".into(),
            allow_insecure_remote: false,
            include_selection: true,
        },
        document_context: "doc".into(),
        allow_reads: true,
    }
}

fn ctx() -> Context {
    Context {
        document_session: "doc".into(),
        fingerprint: "a".repeat(64),
        units: "Millimeters".into(),
        tolerance: 0.001,
        command_idle: true,
        undo_ready: true,
        layer_available: true,
        undo_top_serial: 0,
    }
}

fn owner(generation: u64) -> Token {
    Token {
        conversation: 1,
        generation,
        request: 1,
    }
}

/// Deterministic chat-completions envelope with one narwhal_model tool call.
fn raw(id: &str, args: &Value) -> String {
    raw_tool(id, "narwhal_model", args)
}

fn raw_tool(id: &str, name: &str, args: &Value) -> String {
    serde_json::to_string(&json!({
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": null,
                "tool_calls": [{
                    "id": id,
                    "type": "function",
                    "function": { "name": name, "arguments": args.to_string() }
                }]
            }
        }],
        "usage": { "completion_tokens": 12 }
    }))
    .unwrap()
}

/// Valid box batch.
fn ok_args() -> Value {
    json!({"label":"part","steps":[
        {"id":"part","name":"Part","publish":true,"replace":null,
         "action":{"op":"box","origin":[10,20,0],"size":[4,8,12]}}]})
}

#[test]
fn rejected_dependencies_explain_how_to_recover_without_retained_intermediates() {
    for (id, args, code, detail) in [
        (
            "unpublished",
            {
                let mut args = ok_args();
                args["steps"][0]["publish"] = json!(false);
                args
            },
            "no_published_results",
            "publish=false intermediates do not persist between calls",
        ),
        (
            "unknown_source",
            json!({"label":"edit","steps":[{
            "id":"edited","name":"Edited","publish":true,"replace":null,
            "action":{"op":"extrude","source":"previous_batch_profile","distance":12,
                      "direction":[0,0,1],"solid":true}}]}),
            "unresolved_modeling_reference",
            "step IDs from other batches are not document UUIDs",
        ),
    ] {
        let mut conv = Conversation::new(81, binding()).unwrap();
        let (token, _) = turn(&mut conv, "Build a part", id, &args);
        let result = conv.resolve_call(&token, binding(), id).unwrap();
        assert_eq!(result["kind"], "result");
        assert_eq!(result["output"]["code"], code);
        assert_eq!(result["output"]["applied"], false);
        assert!(
            result["output"]["guidance"]
                .as_str()
                .unwrap()
                .contains(detail)
        );
    }
}

#[test]
fn local_export_requires_one_use_native_attempt_and_canonical_result() {
    use narwhal_automation::export_action::Outcome;
    let mut conv = Conversation::new(91, binding()).unwrap();
    let ready = conv
        .begin(
            binding(),
            Some("Export this selected part".into()),
            Value::Null,
            60_000,
            2048,
        )
        .unwrap();
    let token: Token = serde_json::from_value(ready["token"].clone()).unwrap();
    let args = json!({"object_ids":["00000000-0000-0000-0000-000000000001"],"format":"step"});
    assert_eq!(
        conv.complete(
            token.clone(),
            binding(),
            Some(raw_tool("export", "narwhal_export", &args)),
            None
        )["status"],
        "tool_calls"
    );
    let forged = ToolResult {
        call_id: "export".into(),
        is_error: false,
        output: json!({"exported":true}),
    };
    assert!(
        conv.results(token.clone(), binding(), vec![forged])
            .is_err()
    );
    conv.consume_export(token.clone(), binding(), "export".into())
        .unwrap();
    assert!(
        conv.consume_export(token.clone(), binding(), "export".into())
            .is_err()
    );
    let completed = conv
        .finish_export(
            token.clone(),
            binding(),
            "export".into(),
            Outcome::Exported,
            321,
        )
        .unwrap();
    let canonical: ToolResult = serde_json::from_value(completed["result"].clone()).unwrap();
    let mut forged = canonical.clone();
    forged.output["destination_disclosed"] = json!(true);
    assert!(
        conv.results(token.clone(), binding(), vec![forged])
            .is_err()
    );
    conv.results(token.clone(), binding(), vec![canonical])
        .unwrap();
    assert!(
        conv.consume_export(token, binding(), "export".into())
            .is_err()
    );
}

/// Malformed batch: negative box dimension → invalid_modeling_step.
fn bad_args() -> Value {
    json!({"label":"part","steps":[
        {"id":"part","name":"Part","publish":true,"replace":null,
         "action":{"op":"box","origin":[10,20,0],"size":[4,8,-12]}}]})
}

/// Begin a turn and complete it with a narwhal_model call; returns (token, outcome).
fn turn(conv: &mut Conversation, text: &str, id: &str, args: &Value) -> (Token, Value) {
    let b = binding();
    let ready = conv
        .begin(b.clone(), Some(text.into()), Value::Null, 60_000, 2048)
        .unwrap();
    let token: Token = serde_json::from_value(ready["token"].clone()).unwrap();
    let outcome = conv.complete(token.clone(), b, Some(raw(id, args)), None);
    (token, outcome)
}

fn visual_turn(c: &mut Conversation, id: &str) -> Token {
    let b = binding();
    let ready = c
        .begin(
            b.clone(),
            Some("Inspect appearance".into()),
            Value::Null,
            60_000,
            2048,
        )
        .unwrap();
    let token: Token = serde_json::from_value(ready["token"].clone()).unwrap();
    let outcome = c.complete(
        token.clone(),
        b,
        Some(raw_tool(id, "narwhal_capture_view", &json!({}))),
        None,
    );
    assert_eq!(outcome["status"], "tool_calls");
    token
}
fn image_fixture(id: &str) -> ToolResult {
    // Bounded protocol fixture, deliberately NOT a real decoded image/evidence.
    ToolResult {
        call_id: id.into(),
        is_error: false,
        output: json!({"kind":"viewport_image","mime_type":"image/jpeg","data":"/9j/AAAAAAAAAAA=","width":16,"height":16,"scope":"active_viewport_only","geometry_validity_verified":false}),
    }
}

#[test]
fn viewport_sharing_requires_one_shot_bound_consent_and_cannot_impersonate_read_results() {
    let b = binding();
    let mut c = Conversation::new(1, b.clone()).unwrap();
    let tok = visual_turn(&mut c, "capture");
    assert_eq!(
        c.resolve_call(&tok, b.clone(), "capture").unwrap()["allowed"],
        false
    );
    assert_eq!(
        c.results(tok.clone(), b.clone(), vec![image_fixture("capture")])
            .unwrap_err(),
        "viewport_sharing_not_approved"
    );
    let mut stale = b.clone();
    stale.document_context = "other".into();
    // The UI's stale binding must not grant image sharing.
    assert!(
        c.decide_visual(tok.clone(), stale, "capture".into(), true)
            .is_err()
    );
    let tok = visual_turn(&mut c, "fresh_capture");
    c.decide_visual(tok.clone(), b.clone(), "fresh_capture".into(), true)
        .unwrap();
    assert!(
        c.decide_visual(tok.clone(), b.clone(), "fresh_capture".into(), true)
            .is_err()
    );
    let mut bad = image_fixture("fresh_capture");
    bad.output["data"] = json!("https://private.invalid/image");
    assert_eq!(
        c.results(tok.clone(), b.clone(), vec![bad]).unwrap_err(),
        "invalid_viewport_image"
    );
    c.results(tok, b.clone(), vec![image_fixture("fresh_capture")])
        .unwrap();
    let next = c.begin(b.clone(), None, Value::Null, 60_000, 2048).unwrap();
    let messages = next["body"]["messages"].as_array().unwrap();
    assert!(messages.iter().any(|m| {
        m["content"]
            .as_array()
            .is_some_and(|a| a.iter().any(|v| v["type"] == "image_url"))
    }));
    assert!(
        messages
            .iter()
            .filter(|m| m["role"] == "tool")
            .all(|m| !m["content"].as_str().unwrap().contains("/9j/"))
    );
    c.cancel();
    let tok = visual_turn(&mut c, "after_cancel");
    assert_eq!(
        c.results(tok, b, vec![image_fixture("after_cancel")])
            .unwrap_err(),
        "viewport_sharing_not_approved"
    );
}

#[test]
fn declined_viewport_is_canonical_and_capture_cancellation_revokes_permits() {
    let b = binding();
    let mut c = Conversation::new(1, b.clone()).unwrap();
    let tok = visual_turn(&mut c, "declined");
    c.decide_visual(tok.clone(), b.clone(), "declined".into(), false)
        .unwrap();
    assert_eq!(
        c.results(tok.clone(), b.clone(), vec![image_fixture("declined")])
            .unwrap_err(),
        "modeling_result_mismatch"
    );
    let resolved = c.resolve_call(&tok, b.clone(), "declined").unwrap();
    assert_eq!(resolved["output"]["code"], "viewport_sharing_declined");
    c.results(
        tok,
        b.clone(),
        vec![ToolResult {
            call_id: "declined".into(),
            is_error: true,
            output: resolved["output"].clone(),
        }],
    )
    .unwrap();
    let tok = visual_turn(&mut c, "cancelled");
    c.decide_visual(tok.clone(), b.clone(), "cancelled".into(), true)
        .unwrap();
    c.cancel();
    assert!(c.results(tok, b, vec![image_fixture("cancelled")]).is_err());
}

#[test]
fn malformed_inspection_arguments_are_canonical_and_recoverable() {
    for name in [
        "narwhal_inspect",
        "narwhal_measure",
        "narwhal_frame",
        "narwhal_curve_samples",
        "narwhal_compare_curves",
    ] {
        let b = binding();
        let mut c = Conversation::new(1, b.clone()).unwrap();
        let ready = c
            .begin(
                b.clone(),
                Some("inspect the parts".into()),
                Value::Null,
                60_000,
                2048,
            )
            .unwrap();
        let tok: Token = serde_json::from_value(ready["token"].clone()).unwrap();
        let outcome = c.complete(
            tok.clone(),
            b.clone(),
            Some(raw_tool("bad_read", name, &json!({"unexpected":true}))),
            None,
        );
        assert_eq!(outcome["status"], "tool_calls", "{name}: {outcome}");
        let resolved = c.resolve_call(&tok, b.clone(), "bad_read").unwrap();
        assert_eq!(resolved["kind"], "result");
        assert_eq!(resolved["is_error"], true);
        assert_eq!(resolved["output"]["applied"], false);
        assert_eq!(
            c.results(
                tok.clone(),
                b.clone(),
                vec![ToolResult {
                    call_id: "bad_read".into(),
                    is_error: false,
                    output: json!({"applied":true})
                }]
            )
            .unwrap_err(),
            "modeling_result_mismatch"
        );
        c.results(
            tok,
            b.clone(),
            vec![ToolResult {
                call_id: "bad_read".into(),
                is_error: true,
                output: resolved["output"].clone(),
            }],
        )
        .unwrap();
        let (next, outcome) = turn(&mut c, "build the corrected part", "corrected", &ok_args());
        assert_eq!(outcome["status"], "tool_calls");
        assert_eq!(
            c.stage_modeling(next, b, "corrected".into(), ctx())
                .unwrap()["status"],
            "modeling_review_required"
        );
    }
}

#[test]
fn malformed_args_rejected_then_corrected_round_proceeds() {
    let b = binding();
    let mut c = Conversation::new(1, b.clone()).unwrap();

    // First request: malformed args are accepted as a bounded tool call, but
    // stage_modeling returns the canonical modeling_rejected result.
    let (tok, outcome) = turn(&mut c, "make a box", "c_bad", &bad_args());
    assert_eq!(outcome["status"], "tool_calls");
    // Stage before results (results clears pending calls).
    let staged = c
        .stage_modeling(tok.clone(), b.clone(), "c_bad".into(), ctx())
        .unwrap();
    assert_eq!(staged["status"], "modeling_rejected");
    let result = staged["result"].clone();
    assert_eq!(result["is_error"], true);
    assert_eq!(result["output"]["applied"], false);
    assert_eq!(result["output"]["code"], "invalid_modeling_step");

    // Record the canonical error; the call ID is now spent.
    let rec = c
        .results(
            tok,
            b.clone(),
            vec![ToolResult {
                call_id: "c_bad".into(),
                is_error: true,
                output: result["output"].clone(),
            }],
        )
        .unwrap();
    assert_eq!(rec["status"], "tool_results_recorded");

    // Request 2: corrected args stage, approve and consume.
    let (tok2, outcome2) = turn(&mut c, "correct it", "c_ok", &ok_args());
    assert_eq!(outcome2["status"], "tool_calls");
    let staged2 = c
        .stage_modeling(tok2.clone(), b.clone(), "c_ok".into(), ctx())
        .unwrap();
    assert_eq!(staged2["status"], "modeling_review_required");
    let t: Ticket = serde_json::from_value(staged2["ticket"].clone()).unwrap();
    c.decide_modeling(t.clone(), b.clone(), ctx(), true)
        .unwrap();
    c.consume_modeling(t, b, ctx()).unwrap();
}

#[test]
fn canceled_or_stale_owner_cannot_stage_or_consume() {
    let b = binding();
    let mut c = Conversation::new(1, b.clone()).unwrap();
    let (_, outcome) = turn(&mut c, "make a box", "c1", &ok_args());
    assert_eq!(outcome["status"], "tool_calls");
    let staged = c
        .stage_modeling(owner(1), b.clone(), "c1".into(), ctx())
        .unwrap();
    let t: Ticket = serde_json::from_value(staged["ticket"].clone()).unwrap();

    // Cancel bumps generation and clears modeling state.
    c.cancel();
    assert_eq!(
        c.stage_modeling(owner(1), b.clone(), "c1".into(), ctx())
            .unwrap_err(),
        "stale_modeling_ticket"
    );
    assert_eq!(
        c.decide_modeling(t.clone(), b.clone(), ctx(), true)
            .unwrap_err(),
        "stale_modeling_ticket"
    );
    assert_eq!(
        c.consume_modeling(t, b.clone(), ctx()).unwrap_err(),
        "stale_modeling_ticket"
    );
    // A mismatched binding is refused at the current generation too.
    let mut wrong = b.clone();
    wrong.document_context = "other".into();
    assert_eq!(
        c.stage_modeling(owner(1), wrong, "c1".into(), ctx())
            .unwrap_err(),
        "stale_modeling_ticket"
    );
}

#[test]
fn forged_success_for_rejected_call_is_refused() {
    let b = binding();
    let mut c = Conversation::new(1, b.clone()).unwrap();
    let (tok, _) = turn(&mut c, "make a box", "c_bad", &bad_args());
    let staged = c
        .stage_modeling(owner(1), b.clone(), "c_bad".into(), ctx())
        .unwrap();
    let canonical = staged["result"].clone();

    // Forged success (is_error=false, applied=true) is refused.
    assert_eq!(
        c.results(
            tok.clone(),
            b.clone(),
            vec![ToolResult {
                call_id: "c_bad".into(),
                is_error: false,
                output: json!({"applied":true,"objects":[]}),
            }]
        )
        .unwrap_err(),
        "modeling_result_mismatch"
    );

    // The canonical error result is still accepted.
    let rec = c
        .results(
            tok,
            b,
            vec![ToolResult {
                call_id: "c_bad".into(),
                is_error: true,
                output: canonical["output"].clone(),
            }],
        )
        .unwrap();
    assert_eq!(rec["status"], "tool_results_recorded");
}

#[test]
fn budgets_bounded_and_call_ids_not_refunded_after_cancel() {
    let b = binding();
    let mut c = Conversation::new(1, b.clone()).unwrap();

    // Three invalid rounds: each consumes a turn, an action, and a call ID.
    for i in 1u64..=3 {
        let id = format!("c{i}");
        let (tok, outcome) = turn(&mut c, &format!("round {i}"), &id, &bad_args());
        assert_eq!(outcome["status"], "tool_calls");
        // Stage before results (results clears pending calls).
        let staged = c
            .stage_modeling(tok.clone(), b.clone(), id.clone(), ctx())
            .unwrap();
        assert_eq!(staged["status"], "modeling_rejected");
        let result = staged["result"].clone();
        c.results(
            tok,
            b.clone(),
            vec![ToolResult {
                call_id: id,
                is_error: true,
                output: result["output"].clone(),
            }],
        )
        .unwrap();
    }

    // Budgets are reduced, not refunded.
    let state = c.public_state();
    assert_eq!(state["turns_spent"], 3);
    assert_eq!(state["calls_spent"], 3);
    assert_eq!(state["remaining_turns"], 21);

    // After cancel, used_call_ids survive: a replayed ID is refused.
    c.cancel();
    let (_, outcome) = turn(&mut c, "request 4", "c1", &ok_args());
    assert_eq!(outcome["status"], "failed");
    assert_eq!(outcome["code"], "replayed_tool_call");
}
