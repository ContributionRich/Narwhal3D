//! OURS: correlate an approved action receipt and a later object read. Never
//! upgrade old publisher measurements into proof of current document geometry.
use crate::{
    box_action::Proposal,
    catalog,
    provider::{ToolCall, ToolResult},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::BTreeMap;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub call_id: String,
    pub object_id: String,
}
impl Request {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.call_id.is_empty()
            || self.call_id.len() > 128
            || self.call_id.chars().any(char::is_control)
            || !catalog::uuid(&self.object_id)
        {
            return Err("invalid_verification_request");
        }
        Ok(())
    }
}
struct Receipt {
    request: u64,
    object_id: Option<String>,
    confirmed: bool,
    reason: &'static str,
}
#[derive(Default)]
pub struct Evidence {
    approvals: BTreeMap<String, Proposal>,
    receipts: BTreeMap<String, Receipt>,
    reads: BTreeMap<String, u64>,
}
impl Evidence {
    pub fn approved_box(&mut self, proposal: Proposal) {
        if self.approvals.len() < 8 {
            self.approvals.insert(proposal.call_id.clone(), proposal);
        }
    }
    pub fn observe(&mut self, request: u64, call: &ToolCall, result: &ToolResult) {
        let out = &result.output;
        if call.name == "narwhal_object_summary"
            && !result.is_error
            && self.reads.len() < 128
            && let Some(id) = out
                .get("object_id")
                .and_then(Value::as_str)
                .filter(|id| catalog::uuid(id))
            && call
                .arguments
                .get("object_id")
                .and_then(Value::as_str)
                .is_some_and(|wanted| wanted.eq_ignore_ascii_case(id))
        {
            self.reads.insert(id.to_ascii_lowercase(), request);
        }
        if call.name != "narwhal_create_box" || self.receipts.len() >= 8 {
            return;
        }
        let approval = self.approvals.remove(&call.id);
        let id = out
            .get("object_id")
            .and_then(Value::as_str)
            .filter(|id| catalog::uuid(id))
            .map(str::to_ascii_lowercase);
        let mut reason = "action_failed_or_noop";
        let mut confirmed = false;
        if !result.is_error && out.get("created") == Some(&json!(true)) && id.is_some() {
            reason = "approval_not_observed";
            if let Some(approval) = approval {
                let expected_volume: f64 = approval.arguments.size.iter().product();
                confirmed = approval.request_token.request == request
                    && serde_json::from_value::<crate::box_action::BoxArgs>(Value::Object(
                        call.arguments.clone(),
                    ))
                    .ok()
                    .as_ref()
                        == Some(&approval.arguments)
                    && out["operation"] == "box"
                    && serde_json::from_value::<[f64; 3]>(out["origin"].clone()).ok()
                        == Some(approval.arguments.origin)
                    && serde_json::from_value::<[f64; 3]>(out["size"].clone()).ok()
                        == Some(approval.arguments.size)
                    && out["units"] == approval.expected.units
                    && out["closed_solid"] == true
                    && out["face_types"] == json!({"PLANE":6})
                    && out["volume"].as_f64().is_some_and(|v| {
                        v.is_finite() && (v - expected_volume).abs() <= expected_volume * 1e-10
                    });
                reason = if confirmed {
                    "approved_publisher_receipt"
                } else {
                    "action_receipt_mismatch"
                };
            }
        }
        self.receipts.insert(
            call.id.clone(),
            Receipt {
                request,
                object_id: id,
                confirmed,
                reason,
            },
        );
    }
    pub fn public_summary(&self) -> Value {
        json!({"known_action_results":self.receipts.len(),
            "approved_creation_receipts":self.receipts.values().filter(|receipt|receipt.confirmed).count(),
            "current_geometry_verified":false,"provider_text_is_geometry_evidence":false})
    }
    pub fn verify(&self, input: Request) -> Result<Value, &'static str> {
        input.validate()?;
        let receipt = self.receipts.get(&input.call_id);
        let matches = receipt.is_some_and(|r| {
            r.object_id.as_deref() == Some(input.object_id.to_ascii_lowercase().as_str())
        });
        let confirmed = matches && receipt.is_some_and(|r| r.confirmed);
        let observed = matches
            && receipt.is_some_and(|r| {
                self.reads
                    .get(&input.object_id.to_ascii_lowercase())
                    .is_some_and(|read| *read > r.request)
            });
        let reason = if !matches {
            "unknown_or_mismatched_action_object"
        } else {
            receipt.unwrap().reason
        };
        Ok(
            json!({"status":"verification_incomplete","call_id":input.call_id,"object_id":input.object_id,
            "creation_receipt_verified":confirmed,"post_action_object_observed":observed,
            "current_geometry_verified":false,"verified":false,"reason":reason,
                        "evidence_stages":crate::evidence_contract::verification_stages(confirmed,observed),
            "remaining":["fresh_authoritative_geometry_measurement_required","authoritative_content_revision_unavailable"],
            "summary":"A creation receipt and a later object read are not proof of current dimensions, topology or manufacturability."}),
        )
    }
}
pub fn schema() -> Value {
    json!({"type":"object","properties":{"call_id":{"type":"string","minLength":1,"maxLength":128},
        "object_id":{"type":"string","format":"uuid","minLength":36,"maxLength":36}},
        "required":["call_id","object_id"],"additionalProperties":false})
}
