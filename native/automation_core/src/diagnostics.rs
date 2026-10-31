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
#[cfg(test)]
mod tests {
    use super::*;
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
