//! Local-destination export authority. Providers can name objects/format, never paths.
use crate::{catalog, conversation::Token, provider::ToolResult};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashSet};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub object_ids: Vec<String>,
    pub format: Format,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Format {
    ThreeDm,
    Step,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Exported,
    Declined,
    Unavailable,
    StaleContext,
    ExistingDestination,
    Failed,
}
impl Request {
    pub fn validate(&self) -> Result<(), &'static str> {
        let mut unique = HashSet::new();
        if !(1..=48).contains(&self.object_ids.len())
            || self
                .object_ids
                .iter()
                .any(|id| !catalog::uuid(id) || !unique.insert(id))
        {
            return Err("invalid_export_arguments");
        }
        Ok(())
    }
}
pub fn schema() -> Value {
    json!({"type":"object","properties":{
        "object_ids":{"type":"array","minItems":1,"maxItems":48,"uniqueItems":true,"items":{"type":"string","format":"uuid","minLength":36,"maxLength":36}},
        "format":{"enum":["three_dm","step"]}},"required":["object_ids","format"],"additionalProperties":false})
}
pub fn validate(args: &serde_json::Map<String, Value>) -> Result<(), &'static str> {
    let request: Request = serde_json::from_value(Value::Object(args.clone()))
        .map_err(|_| "invalid_export_arguments")?;
    request.validate()
}
struct Attempt {
    owner: Token,
    request: Request,
    result: Option<ToolResult>,
}
#[derive(Default)]
pub struct Gate {
    attempts: BTreeMap<String, Attempt>,
}
impl Gate {
    pub fn clear(&mut self) {
        self.attempts.clear();
    }
    pub fn consume(
        &mut self,
        owner: Token,
        call_id: String,
        request: Request,
    ) -> Result<Value, &'static str> {
        request.validate()?;
        if self.attempts.contains_key(&call_id) || self.attempts.len() >= 64 {
            return Err("export_already_consumed");
        }
        self.attempts.insert(
            call_id,
            Attempt {
                owner,
                request: request.clone(),
                result: None,
            },
        );
        Ok(
            json!({"request":request,"destination_policy":"local_new_file_only","overwrite_allowed":false}),
        )
    }
    pub fn finish(
        &mut self,
        owner: &Token,
        call_id: &str,
        outcome: Outcome,
        file_bytes: u64,
    ) -> Result<ToolResult, &'static str> {
        let attempt = self
            .attempts
            .get_mut(call_id)
            .ok_or("export_not_consumed")?;
        if &attempt.owner != owner || attempt.result.is_some() {
            return Err("export_result_stale");
        }
        if (outcome == Outcome::Exported && !(1..=1_073_741_824).contains(&file_bytes))
            || (outcome != Outcome::Exported && file_bytes != 0)
        {
            return Err("invalid_export_result");
        }
        let result = ToolResult {
            call_id: call_id.into(),
            is_error: outcome != Outcome::Exported,
            output: json!({"kind":"local_export","exported":outcome==Outcome::Exported,"outcome":outcome,
                "format":attempt.request.format,"object_ids":attempt.request.object_ids,
                "file_bytes":file_bytes,"destination_policy":"local_new_file_only","destination_disclosed":false,
                "document_mutated":false,"geometry_validity_verified":false}),
        };
        attempt.result = Some(result.clone());
        Ok(result)
    }
    pub fn verify(&self, owner: &Token, result: &ToolResult) -> Result<(), &'static str> {
        let attempt = self
            .attempts
            .get(&result.call_id)
            .ok_or("export_not_consumed")?;
        if &attempt.owner != owner
            || attempt.result.as_ref().is_none_or(|expected| {
                expected.call_id != result.call_id
                    || expected.is_error != result.is_error
                    || expected.output != result.output
            })
        {
            return Err("export_result_not_canonical");
        }
        Ok(())
    }
}

pub fn request_id_valid(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 96
        && id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
}
pub fn mcp_schema(result_only: bool) -> Value {
    if result_only {
        json!({"type":"object","properties":{"request_id":{"type":"string","minLength":1,"maxLength":96}},"required":["request_id"],"additionalProperties":false})
    } else {
        json!({"type":"object","properties":{"request_id":{"type":"string","minLength":1,"maxLength":96},"export":schema()},"required":["request_id","export"],"additionalProperties":false})
    }
}
pub fn validate_mcp(
    args: &serde_json::Map<String, Value>,
    result_only: bool,
) -> Result<(), &'static str> {
    if !args
        .get("request_id")
        .and_then(Value::as_str)
        .is_some_and(request_id_valid)
        || args.len() != if result_only { 1 } else { 2 }
    {
        return Err("invalid_export_arguments");
    }
    if !result_only {
        validate(
            args.get("export")
                .and_then(Value::as_object)
                .ok_or("invalid_export_arguments")?,
        )?;
    }
    Ok(())
}
struct ExternalAttempt {
    owner: Token,
    request: Request,
    context: crate::modeling::Context,
    started: bool,
}
#[derive(Default)]
pub struct ExternalGate {
    attempts: BTreeMap<String, ExternalAttempt>,
    receipts: Gate,
}
impl ExternalGate {
    pub fn clear(&mut self) {
        self.attempts.clear();
        self.receipts.clear();
    }
    pub fn stage(
        &mut self,
        owner: Token,
        id: String,
        request: Request,
        context: crate::modeling::Context,
    ) -> Result<Value, &'static str> {
        if !request_id_valid(&id) || self.attempts.len() >= 64 || self.attempts.contains_key(&id) {
            return Err("export_replayed_or_budget");
        }
        context.validate()?;
        request.validate()?;
        self.receipts
            .consume(owner.clone(), id.clone(), request.clone())?;
        self.attempts.insert(
            id.clone(),
            ExternalAttempt {
                owner,
                request,
                context,
                started: false,
            },
        );
        Ok(
            json!({"status":"awaiting_local_destination","request_id":id,"exported":false,"poll_tool":"narwhal.export_result"}),
        )
    }
    pub fn begin(
        &mut self,
        owner: &Token,
        id: &str,
        current: &crate::modeling::Context,
    ) -> Result<Value, &'static str> {
        let attempt = self.attempts.get_mut(id).ok_or("export_not_found")?;
        if &attempt.owner != owner || attempt.started {
            return Err("export_already_consumed");
        }
        attempt.started = true;
        if &attempt.context != current {
            return Ok(json!({"result":self.receipts.finish(owner,id,Outcome::StaleContext,0)?}));
        }
        Ok(
            json!({"request":attempt.request,"destination_policy":"local_new_file_only","overwrite_allowed":false}),
        )
    }
    pub fn finish(
        &mut self,
        owner: &Token,
        id: &str,
        outcome: Outcome,
        bytes: u64,
        current: &crate::modeling::Context,
    ) -> Result<Value, &'static str> {
        let attempt = self.attempts.get(id).ok_or("export_not_found")?;
        if &attempt.owner != owner
            || !attempt.started
            || (outcome == Outcome::Exported && &attempt.context != current)
        {
            return Err("export_context_stale");
        }
        Ok(json!({"result":self.receipts.finish(owner,id,outcome,bytes)?}))
    }
    pub fn abort(&mut self, owner: &Token, id: &str) -> Result<Value, &'static str> {
        let attempt = self.attempts.get_mut(id).ok_or("export_not_found")?;
        if &attempt.owner != owner {
            return Err("export_context_stale");
        }
        attempt.started = true;
        Ok(json!({"result":self.receipts.finish(owner,id,Outcome::Failed,0)?}))
    }
    pub fn lookup(&self, owner: &Token, id: &str) -> Result<Value, &'static str> {
        let attempt = self.attempts.get(id).ok_or("export_not_found")?;
        if &attempt.owner != owner {
            return Err("export_context_stale");
        }
        let receipt = self
            .receipts
            .attempts
            .get(id)
            .and_then(|a| a.result.as_ref());
        Ok(if let Some(result) = receipt {
            json!({"status":"finished","request_id":id,"result":result})
        } else {
            json!({"status":if attempt.started {"exporting"} else {"awaiting_local_destination"},"request_id":id,"exported":false})
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn context() -> crate::modeling::Context {
        crate::modeling::Context {
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
    fn external_exports_wait_for_local_begin_and_retries_only_poll() {
        let mut gate = ExternalGate::default();
        assert_eq!(
            gate.stage(owner(), "job".into(), request(), context())
                .unwrap()["status"],
            "awaiting_local_destination"
        );
        assert!(
            gate.stage(owner(), "job".into(), request(), context())
                .is_err()
        );
        assert!(
            gate.finish(&owner(), "job", Outcome::Exported, 123, &context())
                .is_err()
        );
        assert_eq!(gate.lookup(&owner(), "job").unwrap()["exported"], false);
        let authority = gate.begin(&owner(), "job", &context()).unwrap();
        assert_eq!(authority["overwrite_allowed"], false);
        assert!(gate.begin(&owner(), "job", &context()).is_err());
        assert_eq!(gate.lookup(&owner(), "job").unwrap()["status"], "exporting");
        let final_result = gate
            .finish(&owner(), "job", Outcome::Exported, 123, &context())
            .unwrap();
        assert_eq!(
            gate.lookup(&owner(), "job").unwrap()["result"],
            final_result["result"]
        );
        assert!(
            gate.finish(&owner(), "job", Outcome::Exported, 123, &context())
                .is_err()
        );
        assert!(gate.abort(&owner(), "job").is_err());
        assert!(
            gate.stage(owner(), "job".into(), request(), context())
                .is_err()
        );
    }
    #[test]
    fn external_context_changes_never_grant_export_and_abort_is_honest() {
        let mut gate = ExternalGate::default();
        gate.stage(owner(), "stale".into(), request(), context())
            .unwrap();
        let mut changed = context();
        changed.fingerprint = "b".repeat(64);
        let declined = gate.begin(&owner(), "stale", &changed).unwrap();
        assert_eq!(declined["result"]["output"]["outcome"], "stale_context");
        assert_eq!(declined["result"]["output"]["exported"], false);
        assert!(gate.begin(&owner(), "stale", &context()).is_err());
        gate.stage(owner(), "abort".into(), request(), context())
            .unwrap();
        assert_eq!(
            gate.abort(&owner(), "abort").unwrap()["result"]["output"]["outcome"],
            "failed"
        );
        assert!(gate.begin(&owner(), "abort", &context()).is_err());
        gate.clear();
        assert!(gate.lookup(&owner(), "stale").is_err());
        assert!(
            validate_mcp(
                json!({"request_id":"id","export":request()})
                    .as_object()
                    .unwrap(),
                false
            )
            .is_ok()
        );
        for args in [
            json!({"request_id":"../path","export":request()}),
            json!({"request_id":"id","export":request(),"path":"/private"}),
            json!({"request_id":"id","export":{"object_ids":request().object_ids,"format":"step","path":"/private"}}),
        ] {
            assert!(validate_mcp(args.as_object().unwrap(), false).is_err());
        }
    }
    fn request() -> Request {
        Request {
            object_ids: vec!["00000000-0000-0000-0000-000000000001".into()],
            format: Format::Step,
        }
    }
    fn owner() -> Token {
        Token {
            conversation: 1,
            generation: 1,
            request: 1,
        }
    }
    #[test]
    fn paths_unknown_formats_duplicates_and_empty_requests_are_refused() {
        for args in [
            json!({"object_ids":[],"format":"step"}),
            json!({"object_ids":["selected"],"format":"step"}),
            json!({"object_ids":request().object_ids,"format":"obj"}),
            json!({"object_ids":request().object_ids,"format":"step","path":"/private/file"}),
            json!({"object_ids":[request().object_ids[0],request().object_ids[0]],"format":"step"}),
        ] {
            assert_eq!(
                validate(args.as_object().unwrap()),
                Err("invalid_export_arguments")
            );
        }
        request().validate().unwrap();
    }
    #[test]
    fn exports_are_one_use_owner_bound_and_receipts_are_canonical() {
        let mut gate = Gate::default();
        let authority = gate.consume(owner(), "call".into(), request()).unwrap();
        assert_eq!(authority["overwrite_allowed"], false);
        assert!(gate.consume(owner(), "call".into(), request()).is_err());
        let mut stale = owner();
        stale.generation += 1;
        assert!(gate.finish(&stale, "call", Outcome::Exported, 12).is_err());
        assert!(gate.finish(&owner(), "call", Outcome::Exported, 0).is_err());
        let result = gate
            .finish(&owner(), "call", Outcome::Exported, 123)
            .unwrap();
        gate.verify(&owner(), &result).unwrap();
        assert_eq!(result.output["file_bytes"], 123);
        assert_eq!(result.output["destination_disclosed"], false);
        let mut forged = result.clone();
        forged.output["file_bytes"] = json!(999);
        assert!(gate.verify(&owner(), &forged).is_err());
        assert!(
            gate.finish(&owner(), "call", Outcome::Exported, 123)
                .is_err()
        );
        gate.clear();
        assert!(gate.verify(&owner(), &result).is_err());
    }
    #[test]
    fn canceled_picker_has_honest_nonexported_receipt_and_cannot_be_reused() {
        let mut gate = Gate::default();
        gate.consume(owner(), "cancel".into(), request()).unwrap();
        let result = gate
            .finish(&owner(), "cancel", Outcome::Declined, 0)
            .unwrap();
        assert!(result.is_error);
        assert_eq!(result.output["exported"], false);
        gate.verify(&owner(), &result).unwrap();
        assert!(gate.consume(owner(), "cancel".into(), request()).is_err());
        assert!(
            gate.finish(&owner(), "cancel", Outcome::Exported, 123)
                .is_err()
        );
    }
}
