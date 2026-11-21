//! Q9 — Provider limit recovery: strict-vs-enabled retry bound, budget charging,
//! busy-refusal, stale/cancel/binding invalidation, receipt preservation,
//! Review-authority invariance. Public API only; deterministic OpenAI JSON.

use narwhal_automation::conversation::{Binding, Config, Conversation, Token};
use narwhal_automation::modeling::{Context, Ticket};
use narwhal_automation::provider::{Provider, ToolResult};
use serde_json::{Value, json};

const UUID: &str = "00000000-0000-0000-0000-000000000001";

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

#[test]
fn compatible_reasoning_is_explicit_provider_scoped_and_immutable_while_pending() {
    let b = binding();
    let mut c = Conversation::new(1, b.clone()).unwrap();
    let ready = c
        .begin(b.clone(), Some("inspect".into()), Value::Null, 60000, 2048)
        .unwrap();
    assert!(ready["body"].get("reasoning_effort").is_none());
    c.cancel();
    c.compatible_short_reasoning(true).unwrap();
    let ready = c
        .begin(b.clone(), Some("inspect".into()), Value::Null, 60000, 2048)
        .unwrap();
    assert_eq!(ready["body"]["reasoning_effort"], "low");
    assert_eq!(
        c.compatible_short_reasoning(false).unwrap_err(),
        "reasoning_setting_busy"
    );
    c.cancel();
    c.compatible_short_reasoning(false).unwrap();
    let ready = c
        .begin(b.clone(), Some("inspect".into()), Value::Null, 60000, 2048)
        .unwrap();
    assert!(ready["body"].get("reasoning_effort").is_none());
    c.cancel();
    let mut other = b;
    other.config.provider_id = Provider::Anthropic;
    c.rebind(other).unwrap();
    assert_eq!(
        c.compatible_short_reasoning(true).unwrap_err(),
        "compatible_reasoning_setting_only"
    );
}

fn ok_args() -> Value {
    json!({"label":"part","steps":[
        {"id":"part","name":"Part","publish":true,"replace":null,
         "action":{"op":"box","origin":[0,0,0],"size":[4,8,12]}}]})
}

fn raw_tool(id: &str, args: &Value) -> String {
    serde_json::to_string(&json!({
        "choices":[{"finish_reason":"tool_calls","message":{"role":"assistant","content":null,
            "tool_calls":[{"id":id,"type":"function",
                "function":{"name":"narwhal_model","arguments":args.to_string()}}]}
        }],"usage":{"completion_tokens":12}
    }))
    .unwrap()
}

fn truncated_raw() -> String {
    serde_json::to_string(&json!({
        "choices":[{"finish_reason":"length","message":{"role":"assistant","content":"partial"}}],
        "usage":{"completion_tokens":12}
    }))
    .unwrap()
}

fn raw_text(text: &str) -> String {
    serde_json::to_string(&json!({
        "choices":[{"finish_reason":"stop","message":{"role":"assistant","content":text}}],
        "usage":{"completion_tokens":5}
    }))
    .unwrap()
}

fn receipt() -> Value {
    json!({"applied":true,"undo_serial":1,"objects":[
        {"step":"part","object_id":UUID,"valid":true,"solid":true,"airtight":true,
         "volume":384.0,"bounds":[[0,0,0],[4,8,12]]}]})
}

/// Begin a turn (fresh text or continuation) and complete with `raw`; returns (token, outcome).
fn turn(c: &mut Conversation, b: &Binding, text: Option<&str>, raw: String) -> (Token, Value) {
    let r = c
        .begin(
            b.clone(),
            text.map(|t| t.to_string()),
            Value::Null,
            60_000,
            2048,
        )
        .unwrap();
    let t: Token = serde_json::from_value(r["token"].clone()).unwrap();
    (t.clone(), c.complete(t, b.clone(), Some(raw), None))
}

fn tok(ready: &Value) -> Token {
    serde_json::from_value(ready["token"].clone()).unwrap()
}

#[test]
fn default_strict_vs_enabled_retry_budgets_and_busy_refusal() {
    let b = binding();
    // Default (disabled): truncated → failed.
    let mut c = Conversation::new(1, b.clone()).unwrap();
    let (_, o) = turn(&mut c, &b, Some("make a box"), truncated_raw());
    assert_eq!(o["status"], "failed");
    assert_eq!(o["code"], "truncated_response");
    // Enabled: truncated → retryable_provider_limit.
    let mut c = Conversation::new(1, b.clone()).unwrap();
    c.provider_limit_recovery(true).unwrap();
    let (_, o) = turn(&mut c, &b, Some("make a box"), truncated_raw());
    assert_eq!(o["status"], "retryable_provider_limit");
    assert_eq!(o["code"], "truncated_response");
    assert_eq!(o["automatic_retry"], true);
    assert_eq!(o["retry"], 1);
    assert_eq!(o["current_reply_calls_accepted"], false);
    assert_eq!(o["previous_confirmed_work_preserved"], true);
    let s = c.public_state();
    assert!(s["output_bytes_spent"].as_u64().unwrap() > 0);
    assert!(s["reported_tokens_spent"].as_u64().unwrap() > 0);
    // Second truncate on the same request: retry already spent → failed.
    let (_, o) = turn(&mut c, &b, None, truncated_raw());
    assert_eq!(o["status"], "failed");
    assert_eq!(o["code"], "truncated_response");
    // Busy: setter refused while a request is pending.
    let mut c = Conversation::new(1, b.clone()).unwrap();
    c.provider_limit_recovery(true).unwrap();
    c.begin(b.clone(), Some("x".into()), Value::Null, 60_000, 2048)
        .unwrap();
    assert_eq!(
        c.provider_limit_recovery(true).unwrap_err(),
        "provider_recovery_busy"
    );
}

#[test]
fn stale_token_cancel_and_binding_invalidation_cannot_recover() {
    let b = binding();
    // Stale token → "stale".
    let mut c = Conversation::new(1, b.clone()).unwrap();
    c.provider_limit_recovery(true).unwrap();
    let r = c
        .begin(
            b.clone(),
            Some("make a box".into()),
            Value::Null,
            60_000,
            2048,
        )
        .unwrap();
    let good = tok(&r);
    let o = c.complete(
        Token {
            conversation: 999,
            generation: 1,
            request: 1,
        },
        b.clone(),
        Some(truncated_raw()),
        None,
    );
    assert_eq!(o["status"], "stale");
    // Cancel → "stale".
    c.cancel();
    let o = c.complete(good, b.clone(), Some(truncated_raw()), None);
    assert_eq!(o["status"], "stale");
    // Binding mismatch → "stale".
    let mut c = Conversation::new(1, b.clone()).unwrap();
    c.provider_limit_recovery(true).unwrap();
    let r = c
        .begin(
            b.clone(),
            Some("make a box".into()),
            Value::Null,
            60_000,
            2048,
        )
        .unwrap();
    let mut wrong = b.clone();
    wrong.document_context = "other".into();
    let o = c.complete(tok(&r), wrong, Some(truncated_raw()), None);
    assert_eq!(o["status"], "stale");
}

#[test]
fn truncated_retry_preserves_prior_receipt_and_rejects_replay() {
    let b = binding();
    let mut c = Conversation::new(1, b.clone()).unwrap();
    c.provider_limit_recovery(true).unwrap();
    // First request: apply a box batch with canonical receipt.
    let (t, o) = turn(&mut c, &b, Some("make a box"), raw_tool("c1", &ok_args()));
    assert_eq!(o["status"], "tool_calls");
    let s = c
        .stage_modeling(t.clone(), b.clone(), "c1".into(), ctx())
        .unwrap();
    assert_eq!(s["status"], "modeling_review_required");
    let tk: Ticket = serde_json::from_value(s["ticket"].clone()).unwrap();
    c.decide_modeling(tk.clone(), b.clone(), ctx(), true)
        .unwrap();
    c.consume_modeling(tk.clone(), b.clone(), ctx()).unwrap();
    c.modeling_publication(tk.clone(), b.clone(), receipt(), false)
        .unwrap();
    c.results(
        t,
        b.clone(),
        vec![ToolResult {
            call_id: "c1".into(),
            is_error: false,
            output: receipt(),
        }],
    )
    .unwrap();
    assert_eq!(
        c.public_state()["cad_state"]["modeling"]["applied_batches"],
        1
    );
    // Request 2: truncated continuation → retryable, receipt preserved.
    let (_, o) = turn(&mut c, &b, None, truncated_raw());
    assert_eq!(o["status"], "retryable_provider_limit");
    assert_eq!(o["previous_confirmed_work_preserved"], true);
    assert_eq!(
        c.public_state()["cad_state"]["modeling"]["applied_batches"],
        1
    );
    assert_eq!(c.public_state()["used_call_ids"], 1);
    // Request 3: valid continuation completes.
    let (_, o) = turn(&mut c, &b, None, raw_text("done"));
    assert_eq!(o["status"], "completed");
    // Replay of the applied call ID is refused.
    let (_, o) = turn(&mut c, &b, None, raw_tool("c1", &ok_args()));
    assert_eq!(o["status"], "failed");
    assert_eq!(o["code"], "replayed_tool_call");
}

#[test]
fn retry_cannot_bypass_review_authority() {
    let b = binding();
    let mut c = Conversation::new(1, b.clone()).unwrap();
    c.provider_limit_recovery(true).unwrap();
    let (_, o) = turn(&mut c, &b, Some("make a box"), truncated_raw());
    assert_eq!(o["status"], "retryable_provider_limit");
    let (t, o) = turn(&mut c, &b, None, raw_tool("c2", &ok_args()));
    assert_eq!(o["status"], "tool_calls");
    let s = c.stage_modeling(t, b, "c2".into(), ctx()).unwrap();
    assert_eq!(s["status"], "modeling_review_required");
}
