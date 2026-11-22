//! Bounded per-response tool-call budgets: the provider adapter must distinguish
//! "too many calls in ONE reply" from "a single call's arguments too large",
//! so modest parallel read calls are accepted while genuinely oversized batches
//! fail closed with a distinct, actionable code. Conversation-level MAX_ACTIONS
//! and MAX_TURNS are separate budgets and are left unchanged.
//!
//! Public Conversation API only; deterministic chat-completions fixtures; no host,
//! geometry, mocks, replay execution, or global budget changes.

use narwhal_automation::conversation::{Binding, Config, Conversation, Token};
use narwhal_automation::provider::{MAX_TOOL_CALLS_PER_REPLY, Provider, ToolResult};
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

fn token_of(ready: &Value) -> Token {
    serde_json::from_value(ready["token"].clone()).unwrap()
}

fn conversation() -> (Conversation, Binding) {
    let b = binding();
    (Conversation::new(1, b.clone()).unwrap(), b)
}

/// Deterministic chat-completions envelope with N valid read tool calls.
/// `prefix` keeps call IDs unique across turns (duplicate IDs are replayed
/// and refused by design), so multi-turn conversations can reuse this helper.
fn raw_reads(prefixed: bool, prefix: &str, count: usize) -> String {
    let id = |i: usize| -> String {
        if prefixed {
            format!("{prefix}r{i}")
        } else {
            format!("r{i}")
        }
    };
    let calls: Vec<Value> = (0..count)
        .map(|i| {
            json!({"id":id(i),"type":"function",
                "function":{"name":"narwhal_document_summary","arguments":"{}"}})
        })
        .collect();
    serde_json::to_string(&json!({
        "choices": [{"finish_reason": "tool_calls",
            "message": {"role":"assistant","content":null,"tool_calls":calls}}]
    }))
    .unwrap()
}

#[test]
fn five_valid_read_calls_are_accepted_in_one_reply() {
    let (mut c, b) = conversation();
    let ready = c
        .begin(
            b.clone(),
            Some("inspect the workspace".into()),
            Value::Null,
            30_000,
            4096,
        )
        .unwrap();
    let token = token_of(&ready);
    let outcome = c.complete(token, b, Some(raw_reads(false, "", 5)), None);
    assert_eq!(outcome["status"], "tool_calls");
    let calls = outcome["response"]["tool_calls"].as_array().unwrap();
    assert_eq!(calls.len(), 5);
    assert_eq!(c.public_state()["calls_spent"], 5);
}

#[test]
fn above_max_per_reply_refuses_with_tool_call_limit() {
    let (mut c, b) = conversation();
    // No opt-in: the parser-limit retry is automatic by default.
    // First 17 → retryable (automatic, one retry granted).
    let ready = c
        .begin(b.clone(), Some("inspect".into()), Value::Null, 30_000, 4096)
        .unwrap();
    let token = token_of(&ready);
    let outcome = c.complete(
        token,
        b.clone(),
        Some(raw_reads(false, "", MAX_TOOL_CALLS_PER_REPLY + 1)),
        None,
    );
    assert_eq!(outcome["status"], "retryable_provider_limit");
    assert_eq!(outcome["code"], "tool_call_limit");
    assert_eq!(outcome["automatic_retry"], true);
    assert_eq!(outcome["current_reply_calls_accepted"], false);
    assert_eq!(c.public_state()["calls_spent"], 0);

    // Second 17 → failed (bounded: the one retry is spent).
    let ready = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let token2 = token_of(&ready);
    let outcome2 = c.complete(
        token2,
        b.clone(),
        Some(raw_reads(false, "", MAX_TOOL_CALLS_PER_REPLY + 1)),
        None,
    );
    assert_eq!(outcome2["status"], "failed");
    assert_eq!(outcome2["code"], "tool_call_limit");
}

#[test]
fn oversized_read_arguments_are_a_distinct_code() {
    let (mut c, b) = conversation();
    // No opt-in: the parser-limit retry is automatic by default.
    // First oversized → retryable (automatic, one retry granted).
    let ready = c
        .begin(b.clone(), Some("inspect".into()), Value::Null, 30_000, 4096)
        .unwrap();
    let token = token_of(&ready);
    let raw = serde_json::to_string(&json!({
        "choices": [{"finish_reason": "tool_calls",
            "message": {"role":"assistant","content":null,
                "tool_calls":[{
                    "id":"big","type":"function",
                    "function":{"name":"narwhal_document_summary",
                        "arguments":json!({"blob":"x".repeat(4096)}).to_string()}
                }]}}]
    }))
    .unwrap();
    let outcome = c.complete(token, b.clone(), Some(raw), None);
    assert_eq!(outcome["status"], "retryable_provider_limit");
    // Distinct from the call-count code so the model can shrink arguments
    // rather than split the batch; this is NOT action_budget.
    assert_eq!(outcome["code"], "tool_argument_limit");
    assert_eq!(outcome["automatic_retry"], true);
    assert_eq!(outcome["current_reply_calls_accepted"], false);
    // No calls were accepted, and the budget was not consumed.
    assert_eq!(c.public_state()["calls_spent"], 0);

    // Second oversized → failed (bounded: the one retry is spent).
    let ready = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let token2 = token_of(&ready);
    let raw2 = serde_json::to_string(&json!({
        "choices": [{"finish_reason": "tool_calls",
            "message": {"role":"assistant","content":null,
                "tool_calls":[{
                    "id":"big2","type":"function",
                    "function":{"name":"narwhal_document_summary",
                        "arguments":json!({"blob":"x".repeat(4096)}).to_string()}
                }]}}]
    }))
    .unwrap();
    let outcome2 = c.complete(token2, b.clone(), Some(raw2), None);
    assert_eq!(outcome2["status"], "failed");
    assert_eq!(outcome2["code"], "tool_argument_limit");
}

#[test]
fn modeling_batch_reuses_its_larger_ceiling() {
    let (mut c, b) = conversation();
    let ready = c
        .begin(b.clone(), Some("inspect".into()), Value::Null, 30_000, 4096)
        .unwrap();
    let token = token_of(&ready);
    // ~20 KiB of batch JSON: above the 4 KiB read ceiling, below the 24 KiB
    // modeling ceiling, and NOT malformed, so it must not be refused by
    // tool_argument_limit nor misdiagnosed as tool_call_limit.
    let raw = serde_json::to_string(&json!({
        "choices": [{"finish_reason": "tool_calls",
            "message": {"role":"assistant","content":null,
                "tool_calls":[{
                    "id":"m1","type":"function",
                    "function":{"name":"narwhal_model",
                        "arguments":json!({"note":"x".repeat(20_000)}).to_string()}
                }]}}]
    }))
    .unwrap();
    let outcome = c.complete(token, b, Some(raw), None);
    // The 20 KiB batch is above the 4 KiB read ceiling but below the 24 KiB
    // modeling ceiling, so it must NOT be refused by either the argument-size
    // limit or the call-count limit.
    assert_ne!(
        outcome["code"], "tool_argument_limit",
        "modeling batch must reuse its larger ceiling"
    );
    assert_ne!(outcome["code"], "tool_call_limit");
    // It is accepted at the conversation gate; the (deliberately step-less)
    // batch is handled recoverably at stage time, not by a size limit here.
    assert_eq!(outcome["status"], "tool_calls");
    assert_eq!(
        outcome["response"]["tool_calls"].as_array().unwrap().len(),
        1
    );
}

#[test]
fn conversation_action_budget_is_unchanged() {
    let (mut c, b) = conversation();
    // Sixteen replies of three read calls each = 48 accepted calls, still within
    // the separate 64-call MAX_ACTIONS budget. Each reply's results are recorded
    // so the exchange clears before the next turn.
    for turn in 0..16 {
        let ready = c
            .begin(b.clone(), Some("inspect".into()), Value::Null, 30_000, 4096)
            .unwrap();
        let token = token_of(&ready);
        let raw = serde_json::to_string(&{
            let calls: Vec<Value> = (0..3)
                .map(|i| json!({"id":format!("t{turn}r{i}"),"type":"function",
                    "function":{"name":"narwhal_document_summary","arguments":"{}"}}))
                .collect();
            json!({"choices":[{"finish_reason":"tool_calls","message":{"role":"assistant","content":null,"tool_calls":calls}}]})
        }).unwrap();
        let outcome = c.complete(token.clone(), b.clone(), Some(raw), None);
        assert_eq!(outcome["status"], "tool_calls", "turn {turn}");
        let results: Vec<ToolResult> = (0..3)
            .map(|i| ToolResult {
                call_id: format!("t{turn}r{i}"),
                output: Value::Null,
                is_error: false,
            })
            .collect();
        c.results(token, b.clone(), results).unwrap();
    }
    assert_eq!(c.public_state()["calls_spent"], 48);
}

// ---------------------------------------------------------------------------
// True bounded recovery: the two PARSER-level limits (tool_call_limit,
// tool_argument_limit) are rejected BEFORE any call is accepted and — with the
// opt-in provider-limit recovery enabled — produce ONE automatic retry with the
// existing retryable_provider_limit UI path. Recovery is parse-origin only
// (accept_reply errors such as action_budget never retry), charged
// conservatively, bounded to one per user request, and never replays or
// refunds.
// ---------------------------------------------------------------------------

fn raw_args_oversized(id: &str) -> String {
    serde_json::to_string(&json!({
        "choices": [{"finish_reason": "tool_calls",
            "message": {"role":"assistant","content":null,
                "tool_calls":[{
                    "id":id,"type":"function",
                    "function":{"name":"narwhal_document_summary",
                        "arguments":json!({"blob":"x".repeat(4096)}).to_string()}
                }]}}]
    }))
    .unwrap()
}

fn record(c: &mut Conversation, t: &Token, b: &Binding, ids: &[&str]) {
    c.results(
        t.clone(),
        b.clone(),
        ids.iter()
            .map(|id| ToolResult {
                call_id: id.to_string(),
                output: Value::Null,
                is_error: false,
            })
            .collect::<Vec<_>>(),
    )
    .unwrap();
}

/// Full bounded-recovery flow for the call-count limit:
/// accept -> over-limit (retry) -> accept, history preserved, budgets charged,
/// second over-limit fails bounded.
#[test]
fn tool_call_limit_is_retried_bounded_with_history_preserved() {
    let (mut c, b) = conversation();

    // Step 1: first user request, modest parallel reads accepted + recorded.
    let r = c
        .begin(b.clone(), Some("inspect".into()), Value::Null, 30_000, 4096)
        .unwrap();
    let t1 = token_of(&r);
    let o1 = c.complete(t1.clone(), b.clone(), Some(raw_reads(true, "t1", 5)), None);
    assert_eq!(o1["status"], "tool_calls");
    let h1 = c.public_state()["history_items"].as_u64().unwrap();
    record(&mut c, &t1, &b, &["t1r0", "t1r1", "t1r2", "t1r3", "t1r4"]);

    // Step 2: oversized parse (17 calls) -> the single bounded retry.
    let r = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let t2 = token_of(&r);
    let o2 = c.complete(t2.clone(), b.clone(), Some(raw_reads(true, "t2", 17)), None);
    assert_eq!(o2["status"], "retryable_provider_limit");
    assert_eq!(o2["code"], "tool_call_limit");
    assert_eq!(o2["automatic_retry"], true);
    assert_eq!(o2["retry"], 1);
    assert_eq!(o2["current_reply_calls_accepted"], false);
    assert_eq!(o2["previous_confirmed_work_preserved"], true);
    let s2 = c.public_state();
    // Budgets charged conservatively for the rejected reply; no call accepted.
    assert!(s2["output_bytes_spent"].as_u64().unwrap() > 0);
    assert!(s2["reported_tokens_spent"].as_u64().unwrap() > 0);
    assert_eq!(
        s2["calls_spent"].as_u64().unwrap(),
        5,
        "rejected reply accepts nothing"
    );
    assert_eq!(
        s2["used_call_ids"].as_u64().unwrap(),
        5,
        "rejected reply registers no ids"
    );

    // Step 3: continuation after the hint -> small valid reply accepted.
    let r = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let t3 = token_of(&r);
    let o3 = c.complete(t3.clone(), b.clone(), Some(raw_reads(true, "t3", 2)), None);
    assert_eq!(o3["status"], "tool_calls");
    record(&mut c, &t3, &b, &["t3r0", "t3r1"]);

    // History was NOT cleared: the step-1 exchange still precedes the new one.
    let s3 = c.public_state();
    assert!(
        s3["history_items"].as_u64().unwrap() > h1,
        "history must not be cleared"
    );
    assert!(
        s3["output_bytes_spent"].as_u64().unwrap() > s2["output_bytes_spent"].as_u64().unwrap()
    );
    assert_eq!(s3["calls_spent"].as_u64().unwrap(), 7);

    // Step 4: over-limit AGAIN -> bounded: the one retry is spent, fail closed.
    let r = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let t4 = token_of(&r);
    let o4 = c.complete(t4, b.clone(), Some(raw_reads(true, "t4", 17)), None);
    assert_eq!(o4["status"], "failed");
    assert_eq!(o4["code"], "tool_call_limit");
}

/// The same single bounded retry covers the argument-size limit with its
/// distinct code.
#[test]
fn tool_argument_limit_is_retried_bounded_with_distinct_code() {
    let (mut c, b) = conversation();

    let r = c
        .begin(b.clone(), Some("inspect".into()), Value::Null, 30_000, 4096)
        .unwrap();
    let t1 = token_of(&r);
    let o1 = c.complete(t1.clone(), b.clone(), Some(raw_reads(true, "t1", 2)), None);
    assert_eq!(o1["status"], "tool_calls");
    record(&mut c, &t1, &b, &["t1r0", "t1r1"]);
    let s1 = c.public_state();

    let r = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let t2 = token_of(&r);
    let o2 = c.complete(
        t2.clone(),
        b.clone(),
        Some(raw_args_oversized("big1")),
        None,
    );
    assert_eq!(o2["status"], "retryable_provider_limit");
    assert_eq!(
        o2["code"], "tool_argument_limit",
        "distinct from tool_call_limit"
    );
    assert_eq!(o2["automatic_retry"], true);
    let s2 = c.public_state();
    assert!(
        s2["output_bytes_spent"].as_u64().unwrap() > s1["output_bytes_spent"].as_u64().unwrap()
    );
    assert_eq!(s2["calls_spent"].as_u64().unwrap(), 2);

    let r = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let t3 = token_of(&r);
    let o3 = c.complete(t3.clone(), b.clone(), Some(raw_reads(true, "t3", 2)), None);
    assert_eq!(o3["status"], "tool_calls");
    record(&mut c, &t3, &b, &["t3r0", "t3r1"]);

    let r = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let t4 = token_of(&r);
    let o4 = c.complete(t4, b.clone(), Some(raw_args_oversized("big2")), None);
    assert_eq!(o4["status"], "failed");
    assert_eq!(o4["code"], "tool_argument_limit");
}

/// Cancellation and stale tokens cannot trigger the retry.
#[test]
fn cancellation_and_stale_cannot_retry_parser_limits() {
    // Cancelled transport failure: no retry path at all (recovery enabled,
    // yet the transport failure is refused before any parser-limit recovery).
    let (mut c, b) = conversation();
    let r = c
        .begin(b.clone(), Some("inspect".into()), Value::Null, 30_000, 4096)
        .unwrap();
    let t = token_of(&r);
    let o = c.complete(t, b.clone(), None, Some("cancelled".into()));
    assert_eq!(o["status"], "cancelled");
    assert_eq!(o.get("automatic_retry"), None);
    // After the failed transport the request is abandoned; cancel stays clean.
    c.cancel();
    assert_eq!(
        c.public_state()["request_pending"].as_bool().unwrap(),
        false
    );

    // A stale token with an oversized reply is refused before any recovery.
    let (mut c, b) = conversation();
    c.begin(b.clone(), Some("inspect".into()), Value::Null, 30_000, 4096)
        .unwrap();
    let o = c.complete(
        Token {
            conversation: 999,
            generation: 1,
            request: 1,
        },
        b.clone(),
        Some(raw_reads(true, "t", 17)),
        None,
    );
    assert_eq!(o["status"], "stale");
    assert_eq!(o.get("automatic_retry"), None);

    // Cancellation abandons the pending request, so a later complete with the
    // (now stale) token cannot trigger the retry either.
    let (mut c, b) = conversation();
    let r = c
        .begin(b.clone(), Some("inspect".into()), Value::Null, 30_000, 4096)
        .unwrap();
    let good = token_of(&r);
    c.cancel();
    let o = c.complete(good, b.clone(), Some(raw_reads(true, "t", 17)), None);
    assert_eq!(o["status"], "stale");
    assert_eq!(o.get("automatic_retry"), None);
}

/// The retry budget is SHARED: one per user request across both parser limits,
/// and the next fresh user request resets it (the existing per-user semantics).
#[test]
fn retry_budget_is_shared_and_resets_on_next_user_request() {
    let (mut c, b) = conversation();

    // User request 1: spend the single retry on the call-count limit.
    let r = c
        .begin(b.clone(), Some("inspect".into()), Value::Null, 30_000, 4096)
        .unwrap();
    let t1 = token_of(&r);
    let o1 = c.complete(t1.clone(), b.clone(), Some(raw_reads(true, "t1", 2)), None);
    assert_eq!(o1["status"], "tool_calls");
    record(&mut c, &t1, &b, &["t1r0", "t1r1"]);

    let r = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let t2 = token_of(&r);
    let o2 = c.complete(t2.clone(), b.clone(), Some(raw_reads(true, "t2", 17)), None);
    assert_eq!(o2["status"], "retryable_provider_limit");
    let s2 = c.public_state();

    let r = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let t3 = token_of(&r);
    let o3 = c.complete(t3.clone(), b.clone(), Some(raw_reads(true, "t3", 2)), None);
    assert_eq!(o3["status"], "tool_calls");
    record(&mut c, &t3, &b, &["t3r0", "t3r1"]);

    // The argument-size limit on the same user request cannot retry again.
    let r = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let t4 = token_of(&r);
    let o4 = c.complete(t4, b.clone(), Some(raw_args_oversized("bigx")), None);
    assert_eq!(o4["status"], "failed");
    assert_eq!(o4["code"], "tool_argument_limit");
    let s4 = c.public_state();
    assert!(
        s4["output_bytes_spent"].as_u64().unwrap() > s2["output_bytes_spent"].as_u64().unwrap()
    );

    // A fresh user request resets the retry budget.
    let r = c
        .begin(
            b.clone(),
            Some("continue".into()),
            Value::Null,
            30_000,
            4096,
        )
        .unwrap();
    let t5 = token_of(&r);
    let o5 = c.complete(t5, b.clone(), Some(raw_reads(true, "t5", 17)), None);
    assert_eq!(o5["status"], "retryable_provider_limit");
    assert_eq!(o5["code"], "tool_call_limit");
}

/// accept_reply errors are NEVER retried even with recovery enabled: the
/// retry is parse-origin only, and the action budget stays fail-closed.
#[test]
fn accept_reply_errors_are_never_retried() {
    let (mut c, b) = conversation();
    // 16 rounds x 4 reads = 64 accepted calls = MAX_ACTIONS.
    for turn in 0..16 {
        let r = c
            .begin(b.clone(), Some("inspect".into()), Value::Null, 30_000, 4096)
            .unwrap();
        let t = token_of(&r);
        let raw = serde_json::to_string(&{
            let calls: Vec<Value> = (0..4)
                .map(|i| json!({"id":format!("a{turn}{i}"),"type":"function",
                    "function":{"name":"narwhal_document_summary","arguments":"{}"}}))
                .collect();
            json!({"choices":[{"finish_reason":"tool_calls","message":{"role":"assistant","content":null,"tool_calls":calls}}]})
        }).unwrap();
        let o = c.complete(t.clone(), b.clone(), Some(raw), None);
        assert_eq!(o["status"], "tool_calls", "turn {turn}");
        let owned_ids: Vec<String> = (0..4).map(|i| format!("a{turn}{i}")).collect();
        let ids: Vec<&str> = owned_ids.iter().map(String::as_str).collect();
        record(&mut c, &t, &b, &ids);
    }
    assert_eq!(c.public_state()["calls_spent"].as_u64().unwrap(), 64);
    // One more call in the next reply exceeds MAX_ACTIONS: fail closed, no retry.
    let r = c.begin(b.clone(), None, Value::Null, 30_000, 4096).unwrap();
    let t = token_of(&r);
    let o = c.complete(t, b.clone(), Some(raw_reads(true, "z", 1)), None);
    assert_eq!(o["status"], "failed");
    assert_eq!(
        o["code"], "action_budget",
        "accept_reply error never retries"
    );
    assert_eq!(o.get("automatic_retry"), None);
}
