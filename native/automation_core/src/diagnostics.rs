//! Bounded public failure classes, never provider payloads/credentials or retry execution.
use serde_json::{Value, json};

pub fn annotate(mut value: Value) -> Value {
    let status = value.get("status").and_then(Value::as_str).unwrap_or("");
    if !matches!(
        status,
        "failed" | "transport_failed" | "timeout" | "cancelled" | "stale"
    ) {
        return value;
    }
    let code = value.get("code").and_then(Value::as_str).unwrap_or(status);
    let (category, recovery) = classify(code);
    value["diagnostic"] = json!({"category":category,"recovery":recovery,"automatic_retry":false});
    value
}
pub fn classify(code: &str) -> (&'static str, &'static str) {
    match code {
        "authoritative_identity_unavailable" | "authoritative_selection_unavailable" => {
            ("unavailable_evidence", "choose_document_only_scope")
        }
        "stale" | "stale_preconditions" | "box_context_stale" | "request_scope_changed" => {
            ("stale_context", "refresh_scope")
        }
        "cancelled" => ("cancelled", "fresh_request"),
        "timeout" => ("timeout", "fresh_request"),
        "turn_budget"
        | "action_budget"
        | "context_budget"
        | "output_budget"
        | "token_budget"
        | "tool_result_budget"
        | "output_token_limit"
        | "normalized_output_limit"
        | "response_size_limit"
        | "tool_call_limit" => ("policy_limit", "reduce_scope"),
        "tool_result_correlation" => ("result_correlation", "correct_result_ids"),
        "replayed_tool_call"
        | "proposal_call_replay"
        | "approval_replay"
        | "proposal_replay"
        | "box_approval_mismatch" => ("replay_rejected", "new_explicit_request"),
        "tools_not_authorized"
        | "context_not_authorized"
        | "approval_required"
        | "box_approval_required" => ("permission_denied", "request_consent"),
        "unsupported_tool"
        | "unsupported_response_block"
        | "unsupported_response_field"
        | "missing_reasoning_state"
        | "unsupported_finish_reason" => ("unsupported", "choose_supported_operation"),
        "invalid_json"
        | "invalid_response"
        | "invalid_arguments"
        | "invalid_tool_call"
        | "invalid_usage"
        | "empty_response"
        | "invalid_choices"
        | "inconsistent_finish_reason"
        | "truncated_response"
        | "refused_response"
        | "nonfinal_response" => ("provider_response", "fresh_request"),
        "transport_failed"
        | "http_error"
        | "transport_busy"
        | "invalid_utf8"
        | "response_too_large"
        | "response_read_limit" => ("transport", "check_provider"),
        _ => ("local_rejection", "correct_request"),
    }
}
/// UI-only projection; the host supplies lifecycle facts, never provider text.
pub fn activity(
    status: &str,
    modeling: bool,
    visual: bool,
    elapsed_ms: u64,
) -> Result<Value, &'static str> {
    if !matches!(
        status,
        "sending"
            | "awaiting_approval"
            | "awaiting_review"
            | "connected"
            | "error"
            | "cancelled"
            | "idle"
            | "disconnected"
    ) {
        return Err("invalid_activity_state");
    }
    let active = matches!(status, "sending" | "awaiting_approval" | "awaiting_review");
    let animated = status == "sending";
    let label = if !animated {
        if visual {
            "Approve image sharing"
        } else {
            "Waiting for your approval"
        }
    } else if modeling {
        "Building geometry"
    } else if visual {
        "Inspecting viewport"
    } else {
        "Waiting for model"
    };
    let seconds = elapsed_ms.min(86_400_000) / 1000;
    let elapsed = if seconds < 60 {
        format!("{seconds}s")
    } else {
        format!("{}m {:02}s", seconds / 60, seconds % 60)
    };
    Ok(
        json!({"active":active,"animated":animated,"frame":(elapsed_ms/125)%8,
        "label":label,"elapsed":elapsed,"scope":"temporary_ui_not_chat_history"}),
    )
}

/// Only structural metadata enters this ring. Raw prompts, argument values,
/// provider bodies, endpoints, environment names/values and images never do.
#[derive(Default)]
pub struct Trace {
    events: std::collections::VecDeque<Value>,
    sequence: u64,
    omitted: u64,
    started: Option<std::time::Instant>,
}
fn operation(op: &str) -> bool {
    matches!(
        op,
        "begin"
            | "complete"
            | "results"
            | "stage_modeling"
            | "decide_modeling"
            | "consume_modeling"
            | "modeling_publication"
            | "external_modeling"
            | "decide_visual"
            | "cancel"
            | "reset"
            | "compatible_short_reasoning"
    )
}
fn safe_code(code: &str) -> &'static str {
    match code {
        "boolean_requires_airtight_solids" => "boolean_requires_airtight_solids",
        "curve_inspection_unavailable" => "curve_inspection_unavailable",
        "view_framing_unavailable" => "view_framing_unavailable",
        "truncated_response" => "truncated_response",
        "output_token_limit" => "output_token_limit",
        "invalid_modeling_step" => "invalid_modeling_step",
        "no_published_results" => "no_published_results",
        "unresolved_modeling_reference" => "unresolved_modeling_reference",
        "modeling_context_stale" => "modeling_context_stale",
        "modeling_approval_required" => "modeling_approval_required",
        "modeling_preparation_failed" => "modeling_preparation_failed",
        "modeling_publication_failed" => "modeling_publication_failed",
        "solid_validation_failed" => "solid_validation_failed",
        "modeling_planar_profile_unverified" => "modeling_planar_profile_unverified",
        "attributes_require_existing_replacement" => "attributes_require_existing_replacement",
        "viewport_sharing_declined" => "viewport_sharing_declined",
        "viewport_capture_unavailable" => "viewport_capture_unavailable",
        "invalid_viewport_image" => "invalid_viewport_image",
        "tool_result_budget" => "tool_result_budget",
        "context_budget" => "context_budget",
        "turn_budget" => "turn_budget",
        "action_budget" => "action_budget",
        "invalid_json" => "invalid_json",
        "unsupported_tool" => "unsupported_tool",
        "http_error" => "http_error",
        "transport_failed" => "transport_failed",
        "transport_busy" => "transport_busy",
        "timeout" => "timeout",
        "cancelled" => "cancelled",
        "stale" => "stale",
        "replayed_tool_call" => "replayed_tool_call",
        _ => "local_rejection",
    }
}
pub struct RequestSummary(Value);
pub fn request_summary(input: &Value) -> RequestSummary {
    let mut out = json!({});
    if let Some(enabled) = input["enabled"].as_bool() {
        out["enabled"] = json!(enabled);
    }
    for field in ["timeout_ms", "max_tokens"] {
        if let Some(n) = input[field].as_u64() {
            out[field] = json!(n.min(1_000_000));
        }
    }
    if let Some(text) = input["text"].as_str() {
        out["prompt_bytes"] = json!(text.len());
    }
    if let Some(raw) = input["raw"].as_str() {
        out["response_bytes"] = json!(raw.len());
    }
    if let Some(results) = input["results"].as_array() {
        out["results"] = json!(
            results
                .iter()
                .take(4)
                .map(|r| json!({"is_error":r["is_error"]==true,
            "object_count":r["output"]["objects"].as_array().map(Vec::len),
            "code":r["output"]["code"].as_str().map(safe_code)}))
                .collect::<Vec<_>>()
        );
    }
    if let Some(steps) = input["batch"]["steps"].as_array() {
        out["step_count"] = json!(steps.len());
    }
    if input["output"].is_object() {
        out["publication"] = json!({"applied":input["output"]["applied"].as_bool(),
            "is_error":input["is_error"]==true,"object_count":input["output"]["objects"].as_array().map(Vec::len),
            "code":input["output"]["code"].as_str().map(safe_code),
            "reason":input["output"]["reason"].as_str().map(safe_code)});
    }
    RequestSummary(out)
}
impl Trace {
    pub fn record(
        &mut self,
        op: &str,
        request: RequestSummary,
        result: &Result<Value, &'static str>,
    ) {
        if !operation(op) {
            return;
        }
        let started = *self.started.get_or_insert_with(std::time::Instant::now);
        self.sequence += 1;
        let response = match result {
            Err(code) => json!({"error":safe_code(code),"category":classify(code).0}),
            Ok(value) => {
                let status = match value["status"].as_str().unwrap_or("") {
                    "ready" => "ready",
                    "tool_calls" => "tool_calls",
                    "retryable_provider_limit" => "retryable_provider_limit",
                    "completed" => "completed",
                    "failed" => "failed",
                    "transport_failed" => "transport_failed",
                    "timeout" => "timeout",
                    "cancelled" => "cancelled",
                    "stale" => "stale",
                    "reset" => "reset",
                    "tool_results_recorded" => "tool_results_recorded",
                    "modeling_recorded" => "modeling_recorded",
                    "modeling_approved" => "modeling_approved",
                    "modeling_review_required" => "modeling_review_required",
                    "modeling_consumed" => "modeling_consumed",
                    "modeling_rejected" => "modeling_rejected",
                    "visual_decision" => "visual_decision",
                    "compatible_short_reasoning" => "compatible_short_reasoning",
                    _ => "other",
                };
                let calls = value["response"]["tool_calls"].as_array();
                let tools=calls.into_iter().flatten().take(4).map(|c|{
                    let name=c["name"].as_str().unwrap_or("");
                    let name=if crate::catalog::definitions().iter().any(|t|t.name==name) {name} else {"unsupported"};
                    json!({"name":name,"argument_bytes":serde_json::to_vec(&c["arguments"]).map(|v|v.len()).unwrap_or(0)})
                }).collect::<Vec<_>>();
                json!({"status":status,"code":value["code"].as_str().map(safe_code),"tools":tools,
                    "output_tokens":value["response"]["output_tokens"].as_u64(),
                    "reply_text_bytes":value["response"]["text"].as_str().map(str::len)})
            }
        };
        self.events.push_back(json!({"sequence":self.sequence,"elapsed_ms":started.elapsed().as_millis().min(u64::MAX as u128) as u64,
            "operation":op,"request":request.0,"response":response}));
        while self.events.len() > 32 || serde_json::to_vec(&self.events).unwrap().len() > 16_000 {
            self.events.pop_front();
            self.omitted += 1;
        }
    }
    pub fn snapshot(&self) -> Value {
        json!({"schema":"narwhal.ai.diagnostics.v1","privacy":"Structural metadata only: no prompts, reply text, argument values, credentials, endpoints, images or document contents.",
            "omitted_events":self.omitted,"events":self.events})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn activity_animates_only_real_work_and_stops_on_terminal_states() {
        let first = activity("sending", false, false, 0).unwrap();
        let next = activity("sending", false, false, 125).unwrap();
        assert_eq!(first["label"], "Waiting for model");
        assert_ne!(first["frame"], next["frame"]);
        assert_eq!(
            activity("sending", true, false, 65_000).unwrap()["label"],
            "Building geometry"
        );
        assert_eq!(
            activity("sending", false, true, 0).unwrap()["label"],
            "Inspecting viewport"
        );
        let review = activity("awaiting_approval", false, true, 65_000).unwrap();
        assert_eq!(review["animated"], false);
        assert_eq!(review["label"], "Approve image sharing");
        assert_eq!(review["elapsed"], "1m 05s");
        for status in ["connected", "error", "cancelled", "idle", "disconnected"] {
            assert_eq!(activity(status, false, false, 0).unwrap()["active"], false);
        }
        assert!(activity("private invalid state", false, false, 0).is_err());
    }
    #[test]
    fn diagnostics_keep_useful_structure_but_never_payloads_or_secrets() {
        let secret = "PRIVATE_PROMPT_KEY_IMAGE_PATH_CANARY";
        let input = json!({"text":secret,"raw":secret,"credential_env":secret,"headers":{"Authorization":secret},
            "binding":{"config":{"endpoint":secret,"model":secret}},"timeout_ms":300000,"max_tokens":8192,
            "results":[{"is_error":true,"output":{"code":secret,"reason":secret,"data":secret}}]});
        let output = Ok(
            json!({"status":"tool_calls","response":{"text":secret,"output_tokens":12,"tool_calls":[
            {"name":"narwhal_model","arguments":{"label":secret,"steps":[{"name":secret}]}},
            {"name":secret,"arguments":{"path":secret}}]}}),
        );
        let mut trace = Trace::default();
        trace.record("complete", request_summary(&input), &output);
        let text = trace.snapshot().to_string();
        assert!(!text.contains(secret));
        assert!(text.contains("narwhal_model"));
        assert!(text.contains("response_bytes"));
        assert!(text.contains("argument_bytes"));
        assert!(text.contains("local_rejection"));
    }
    #[test]
    fn diagnostics_are_bounded_and_idle_polling_cannot_evict_failures() {
        let mut trace = Trace::default();
        trace.record("complete", request_summary(&json!({})), &Err("timeout"));
        for _ in 0..100 {
            trace.record(
                "poll",
                request_summary(&json!({})),
                &Ok(json!({"status":"idle"})),
            );
        }
        assert_eq!(trace.snapshot()["events"].as_array().unwrap().len(), 1);
        for _ in 0..100 {
            trace.record(
                "begin",
                request_summary(&json!({"text":"not retained"})),
                &Ok(json!({"status":"ready"})),
            );
        }
        let out = trace.snapshot();
        assert!(out["events"].as_array().unwrap().len() <= 32);
        assert!(out["omitted_events"].as_u64().unwrap() > 0);
        assert!(out.to_string().len() < 17_000);
        assert!(!out.to_string().contains("not retained"));
    }
    #[test]
    fn public_categories_are_distinct_and_never_auto_retry() {
        assert_eq!(classify("context_budget").0, "policy_limit");
        assert_eq!(classify("invalid_json").0, "provider_response");
        assert_eq!(classify("stale").0, "stale_context");
        assert_eq!(classify("unsupported_tool").0, "unsupported");
        assert_eq!(classify("tool_result_correlation").1, "correct_result_ids");
        assert_eq!(
            annotate(json!({"status":"timeout"}))["diagnostic"]["automatic_retry"],
            false
        );
    }
}
