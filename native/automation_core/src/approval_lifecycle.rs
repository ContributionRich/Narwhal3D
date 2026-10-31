//! OURS: bounded human review, separate from provider HTTP lifetime. No publisher.
use crate::{
    conversation::Token,
    primitives::{Arguments, Context},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

pub(crate) struct Clock {
    origin: std::time::Instant,
    #[cfg(test)]
    injected_ms: Option<u64>,
}
impl Default for Clock {
    fn default() -> Self {
        Self {
            origin: std::time::Instant::now(),
            #[cfg(test)]
            injected_ms: None,
        }
    }
}
impl Clock {
    pub(crate) fn now_ms(&self) -> u64 {
        #[cfg(test)]
        if let Some(now) = self.injected_ms {
            return now;
        }
        u64::try_from(self.origin.elapsed().as_millis()).unwrap_or(u64::MAX)
    }
    #[cfg(test)]
    pub(crate) fn set_ms(&mut self, now: u64) {
        assert!(self.injected_ms.is_none_or(|old| now >= old));
        self.injected_ms = Some(now);
    }
}

pub const REVIEW_TTL_MS: u64 = 300_000;
pub const MAX_REVIEWS: usize = 8;
pub const PUBLICATION_TTL_MS: u64 = 60_000;

#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureReason {
    BeforePublication,
    RolledBack,
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UnknownReason {
    ReceiptMissing,
    PublisherDisconnected,
    Timeout,
    RollbackUnconfirmed,
    ReceiptMismatch,
}
/// Trusted-host publisher event only. Provider/read-tool payloads cannot call it.
#[derive(Deserialize)]
#[serde(tag = "outcome", rename_all = "snake_case", deny_unknown_fields)]
pub enum PublicationOutcome {
    Succeeded {
        object_id: String,
        units: String,
        action: Arguments,
        geometry: Box<crate::primitives::GeometryFacts>,
    },
    Failed {
        reason: FailureReason,
    },
    Unknown {
        reason: UnknownReason,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum State {
    AcceptedCall,
    AwaitingHuman,
    Approved,
    Consumed,
    Denied,
    Expired,
    Invalidated,
    PublicationOutcomeUnknown,
    PublisherReceiptConfirmed,
    PublicationFailed,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Event {
    BeginReview,
    Allow,
    Deny,
    Consume,
    Expire,
    Invalidate,
    Published,
    Failed,
    OutcomeUnknown,
}
impl State {
    pub fn transition(self, event: Event) -> Result<Self, &'static str> {
        use Event::*;
        use State::*;
        Ok(match (self, event) {
            (AcceptedCall, BeginReview) => AwaitingHuman,
            (AwaitingHuman, Allow) => Approved,
            (AwaitingHuman, Deny) => Denied,
            (Approved, Consume) => Consumed,
            (AcceptedCall | AwaitingHuman | Approved, Expire) => Expired,
            (AcceptedCall | AwaitingHuman | Approved | Consumed, Invalidate) => Invalidated,
            (Consumed, Published) => PublisherReceiptConfirmed,
            (Consumed, Failed) => PublicationFailed,
            (Consumed, OutcomeUnknown) => PublicationOutcomeUnknown,
            _ => return Err("review_transition_refused"),
        })
    }
    pub fn result_ready(self) -> bool {
        matches!(
            self,
            Self::Denied
                | Self::Invalidated
                | Self::Expired
                | Self::PublicationOutcomeUnknown
                | Self::PublisherReceiptConfirmed
                | Self::PublicationFailed
        )
    }
}

/// A trusted host must return this exact token, never reconstruct it from prose.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionToken {
    pub owner: Token,
    pub review_id: u64,
    pub call_id: String,
}
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Descriptor {
    pub token: DecisionToken,
    pub action: Arguments,
    pub expected: Context,
    pub expires_at_ms: u64,
    pub review_ttl_ms: u64,
    pub remaining_ms: u64,
    pub unavailable_identity: Value,
    pub scope: &'static str,
    pub enabled: bool,
}

pub(crate) struct Record {
    pub token: DecisionToken,
    pub action: Arguments,
    pub binding: crate::conversation::Binding,
    pub state: State,
    expected: Option<Context>,
    expires_at_ms: Option<u64>,
    consumed_at_ms: Option<u64>,
    outcome: Option<Value>,
    scope_current: bool,
    result_recorded: bool,
}
#[derive(Default)]
pub(crate) struct Book {
    next_id: u64,
    entries: std::collections::BTreeMap<String, Record>,
    later_reads: std::collections::BTreeMap<String, u64>,
}
impl Book {
    /// Only Conversation's validated, budgeted call-acceptance path calls this.
    pub(crate) fn accept(
        &mut self,
        owner: Token,
        binding: crate::conversation::Binding,
        call: &crate::provider::ToolCall,
        now: u64,
    ) -> Result<(), &'static str> {
        let action = Arguments::from_accepted_call(call)?;
        let expires_at_ms = now.checked_add(REVIEW_TTL_MS).ok_or("review_clock_limit")?;
        if self.entries.len() >= MAX_REVIEWS || self.entries.contains_key(&call.id) {
            return Err("review_retention_limit");
        }
        self.next_id = self
            .next_id
            .checked_add(1)
            .ok_or("review_retention_limit")?;
        self.entries.insert(
            call.id.clone(),
            Record {
                token: DecisionToken {
                    owner,
                    review_id: self.next_id,
                    call_id: call.id.clone(),
                },
                action,
                binding,
                state: State::AcceptedCall,
                expected: None,
                expires_at_ms: Some(expires_at_ms),
                consumed_at_ms: None,
                outcome: None,
                scope_current: true,
                result_recorded: false,
            },
        );
        Ok(())
    }
    pub(crate) fn get(&self, owner: &Token, call_id: &str) -> Result<&Record, &'static str> {
        let record = self
            .entries
            .get(call_id)
            .ok_or("accepted_review_call_required")?;
        if record.token.owner != *owner {
            return Err("stale_review");
        }
        Ok(record)
    }
    pub(crate) fn check_decision(&self, token: &DecisionToken) -> Result<(), &'static str> {
        if self.get(&token.owner, &token.call_id)?.token != *token {
            return Err("stale_review");
        }
        Ok(())
    }
    pub(crate) fn invalidate_call(
        &mut self,
        owner: &Token,
        call_id: &str,
    ) -> Result<(), &'static str> {
        self.get(owner, call_id)?;
        let record = self.entries.get_mut(call_id).unwrap();
        if let Ok(next) = record.state.transition(Event::Invalidate) {
            record.state = next;
            record.scope_current = false;
        }
        Ok(())
    }
    pub(crate) fn begin(
        &mut self,
        owner: &Token,
        call_id: &str,
        binding: &crate::conversation::Binding,
        context: Context,
        now: u64,
    ) -> Result<Descriptor, &'static str> {
        let record = self.get(owner, call_id)?;
        if &record.binding != binding {
            return Err("stale_review");
        }
        // At most MAX_REVIEWS records. A due sibling must not keep the single
        // active-review slot occupied merely because nobody queried it first.
        for record in self.entries.values_mut() {
            Self::expire_record(record, now)?;
        }
        let record = self.get(owner, call_id)?;
        if record.state == State::Expired {
            return Err("review_expired");
        }
        if self.entries.values().any(|r| {
            matches!(
                r.state,
                State::AwaitingHuman | State::Approved | State::Consumed
            )
        }) {
            return Err("human_review_already_pending");
        }
        context.validate()?;
        if context.append.document_session != binding.document_context {
            return Err("review_context_stale");
        }
        record.action.validate(context.append.tolerance)?;
        let state = record.state.transition(Event::BeginReview)?;
        let expires_at_ms = record.expires_at_ms.ok_or("review_clock_limit")?;
        let record = self.entries.get_mut(call_id).unwrap();
        record.state = state;
        record.expected = Some(context.clone());
        record.expires_at_ms = Some(expires_at_ms);
        Ok(Descriptor {
            token: record.token.clone(),
            action: record.action.clone(),
            expected: context,
            expires_at_ms,
            review_ttl_ms: REVIEW_TTL_MS,
            remaining_ms: expires_at_ms.saturating_sub(now),
            unavailable_identity: crate::evidence_contract::capabilities(),
            scope: "create_one_world_aligned_analytic_solid_never_modify_selection",
            enabled: false,
        })
    }
    fn expire(&mut self, owner: &Token, call_id: &str, now: u64) -> Result<(), &'static str> {
        self.get(owner, call_id)?;
        Self::expire_record(self.entries.get_mut(call_id).unwrap(), now)
    }
    fn expire_record(record: &mut Record, now: u64) -> Result<(), &'static str> {
        if record.state == State::Consumed
            && record
                .consumed_at_ms
                .is_some_and(|start| now >= start.saturating_add(PUBLICATION_TTL_MS))
        {
            record.state = record.state.transition(Event::OutcomeUnknown)?;
            record.outcome = Some(
                json!({"created":null,"outcome":"unknown","may_have_published":true,"reason":UnknownReason::Timeout,"retry_allowed":false,
                                    "provenance":{"source":"native_publication_timeout","decision_token":record.token}}),
            );
        }
        if record.expires_at_ms.is_some_and(|expiry| now >= expiry)
            && let Ok(next) = record.state.transition(Event::Expire)
        {
            record.state = next;
        }
        Ok(())
    }
    pub(crate) fn decide(
        &mut self,
        token: DecisionToken,
        binding: &crate::conversation::Binding,
        action: Arguments,
        current: Context,
        allow: bool,
        now: u64,
    ) -> Result<Value, &'static str> {
        if self.get(&token.owner, &token.call_id)?.token != token {
            return Err("stale_review");
        }
        self.expire(&token.owner, &token.call_id, now)?;
        let record = self.entries.get_mut(&token.call_id).unwrap();
        if record.state == State::Expired {
            return Err("review_expired");
        }
        if record.state != State::AwaitingHuman {
            return Err("review_transition_refused");
        }
        if record.action != action {
            record.state = record.state.transition(Event::Invalidate)?;
            return Err("review_action_mismatch");
        }
        if &record.binding != binding
            || record.expected.as_ref() != Some(&current)
            || current.validate().is_err()
        {
            record.state = record.state.transition(Event::Invalidate)?;
            return Err("review_context_stale");
        }
        record.state = record
            .state
            .transition(if allow { Event::Allow } else { Event::Deny })?;
        self.state(&token.owner, &token.call_id, now)
    }
    pub(crate) fn consume(
        &mut self,
        token: DecisionToken,
        binding: &crate::conversation::Binding,
        current: Context,
        now: u64,
    ) -> Result<Value, &'static str> {
        if self.get(&token.owner, &token.call_id)?.token != token {
            return Err("stale_review");
        }
        self.expire(&token.owner, &token.call_id, now)?;
        let record = self.entries.get_mut(&token.call_id).unwrap();
        if record.state == State::Expired {
            return Err("review_expired");
        }
        if record.state != State::Approved {
            return Err("review_approval_required_or_spent");
        }
        // Spend first: stale pins at publication must not leave an approval reusable.
        record.state = record.state.transition(Event::Consume)?;
        record.consumed_at_ms = Some(now);
        if &record.binding != binding
            || record.expected.as_ref() != Some(&current)
            || current.validate().is_err()
        {
            record.state = record.state.transition(Event::Invalidate)?;
            return Err("review_context_stale");
        }
        Ok(
            json!({"status":"review_consumed","token":record.token,"action":record.action,
            "context":record.expected,"enabled":false,"current_geometry_verified":false}),
        )
    }
    pub(crate) fn publication(
        &mut self,
        token: DecisionToken,
        binding: &crate::conversation::Binding,
        outcome: PublicationOutcome,
        now: u64,
    ) -> Result<Value, &'static str> {
        if self.get(&token.owner, &token.call_id)?.token != token {
            return Err("stale_review");
        }
        self.expire(&token.owner, &token.call_id, now)?;
        let record = self.entries.get_mut(&token.call_id).unwrap();
        if &record.binding != binding || record.state != State::Consumed {
            return Err("publication_not_expected");
        }
        let (event, mut output) = match outcome {
            PublicationOutcome::Succeeded {
                object_id,
                units,
                action,
                geometry,
            } => {
                if action != record.action
                    || record
                        .expected
                        .as_ref()
                        .is_none_or(|context| context.append.units != units)
                    || !crate::catalog::uuid(&object_id)
                    || object_id == "00000000-0000-0000-0000-000000000000"
                    || record.action.check_geometry(*geometry).is_err()
                {
                    record.state = record.state.transition(Event::OutcomeUnknown)?;
                    record.outcome = Some(
                        json!({"created":null,"outcome":"unknown","may_have_published":true,"reason":UnknownReason::ReceiptMismatch,"retry_allowed":false,
                                                    "provenance":{"source":"native_receipt_validation","decision_token":record.token}}),
                    );
                    return Err("review_receipt_mismatch");
                }
                (
                    Event::Published,
                    json!({"created":true,"object_id":object_id.to_ascii_lowercase(),"action":record.action,
                    "units":units,"evidence":"publisher_receipt_confirmed","current_geometry_verified":false,"retry_allowed":false}),
                )
            }
            PublicationOutcome::Failed { reason } => (
                Event::Failed,
                json!({"created":false,"outcome":"failed","reason":reason,"retry_allowed":false}),
            ),
            PublicationOutcome::Unknown { reason } => (
                Event::OutcomeUnknown,
                json!({"created":null,"outcome":"unknown","may_have_published":true,"reason":reason,"retry_allowed":false}),
            ),
        };
        output["provenance"] =
            json!({"source":"trusted_publisher_event","decision_token":record.token});
        record.state = record.state.transition(event)?;
        record.outcome = Some(output);
        self.state(&token.owner, &token.call_id, now)
    }
    pub(crate) fn state(
        &mut self,
        owner: &Token,
        call_id: &str,
        now: u64,
    ) -> Result<Value, &'static str> {
        self.expire(owner, call_id, now)?;
        let record = self.get(owner, call_id)?;
        Ok(
            json!({"status":"review_state","state":record.state,"token":record.token,
            "remaining_ms":record.expires_at_ms.map(|expiry|expiry.saturating_sub(now)),
            "http_pending_required":false,"enabled":false,"retained_reviews":self.entries.len(),"publication":record.outcome,"evidence_scope_current":record.scope_current,
                        "publication_remaining_ms":record.consumed_at_ms.map(|start|start.saturating_add(PUBLICATION_TTL_MS).saturating_sub(now))}),
        )
    }
    pub(crate) fn result(
        &mut self,
        owner: &Token,
        call_id: &str,
        now: u64,
    ) -> Result<crate::provider::ToolResult, &'static str> {
        self.expire(owner, call_id, now)?;
        let record = self.get(owner, call_id)?;
        if !record.scope_current {
            return Err("stale_review");
        }
        if record.result_recorded {
            return Err("review_result_already_recorded");
        }
        if !record.state.result_ready() {
            return Err("human_review_pending");
        }
        let output = record.outcome.clone().unwrap_or_else(|| {
            json!({
            "created":if record.consumed_at_ms.is_some(){Value::Null}else{json!(false)},
            "review_state":record.state,"retry_allowed":false,
            "provenance":{"source":"native_review_resolution","decision_token":record.token}})
        });
        Ok(crate::provider::ToolResult {
            call_id: call_id.into(),
            output,
            is_error: record.state != State::PublisherReceiptConfirmed,
        })
    }
    pub(crate) fn mark_result_recorded(&mut self, owner: &Token, call_id: &str) {
        if let Some(record) = self.entries.get_mut(call_id)
            && record.token.owner == *owner
        {
            record.result_recorded = true;
        }
    }
    pub(crate) fn owns_call(&self, owner: &Token, call_id: &str) -> bool {
        self.get(owner, call_id).is_ok()
    }
    pub(crate) fn observe_read(
        &mut self,
        request: u64,
        call: &crate::provider::ToolCall,
        result: &crate::provider::ToolResult,
    ) {
        if call.name != "narwhal_object_summary" || result.is_error || self.later_reads.len() >= 128
        {
            return;
        }
        if let Some(id) = result
            .output
            .get("object_id")
            .and_then(Value::as_str)
            .filter(|id| crate::catalog::uuid(id))
            && call
                .arguments
                .get("object_id")
                .and_then(Value::as_str)
                .is_some_and(|wanted| wanted.eq_ignore_ascii_case(id))
        {
            self.later_reads.insert(id.to_ascii_lowercase(), request);
        }
    }
    pub(crate) fn verify(&self, input: &crate::verification::Request) -> Option<Value> {
        let record = self.entries.get(&input.call_id)?;
        let matches = record.scope_current
            && record
                .outcome
                .as_ref()
                .and_then(|out| out.get("object_id"))
                .and_then(Value::as_str)
                .is_some_and(|id| id.eq_ignore_ascii_case(&input.object_id));
        let confirmed = matches && record.state == State::PublisherReceiptConfirmed;
        let observed = matches
            && self
                .later_reads
                .get(&input.object_id.to_ascii_lowercase())
                .is_some_and(|request| *request > record.token.owner.request);
        Some(
            json!({"status":"verification_incomplete","call_id":input.call_id,"object_id":input.object_id,
            "creation_receipt_verified":confirmed,"post_action_object_observed":observed,
            "current_geometry_verified":false,"verified":false,
            "evidence_stages":crate::evidence_contract::verification_stages(confirmed,observed),
            "remaining":["fresh_authoritative_geometry_measurement_required","authoritative_content_revision_unavailable"]}),
        )
    }
    pub(crate) fn summary_counts(&self) -> (usize, usize) {
        let current = self.entries.values().filter(|record| record.scope_current);
        (
            current
                .clone()
                .filter(|record| record.outcome.is_some())
                .count(),
            current
                .filter(|record| record.state == State::PublisherReceiptConfirmed)
                .count(),
        )
    }
    pub(crate) fn invalidate_all(&mut self) {
        self.later_reads.clear();
        for record in self.entries.values_mut() {
            record.scope_current = false;
            if let Ok(next) = record.state.transition(Event::Invalidate) {
                record.state = next;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn v4_a03_review_contract_never_skips_or_renews_human_approval() {
        assert_eq!(
            State::AcceptedCall.transition(Event::BeginReview),
            Ok(State::AwaitingHuman)
        );
        assert_eq!(
            State::AwaitingHuman.transition(Event::Allow),
            Ok(State::Approved)
        );
        assert_eq!(
            State::Approved.transition(Event::Consume),
            Ok(State::Consumed)
        );
        assert_eq!(
            State::Consumed.transition(Event::OutcomeUnknown),
            Ok(State::PublicationOutcomeUnknown)
        );
        for state in [
            State::Denied,
            State::Expired,
            State::Invalidated,
            State::PublicationOutcomeUnknown,
            State::PublisherReceiptConfirmed,
            State::PublicationFailed,
        ] {
            for event in [
                Event::BeginReview,
                Event::Allow,
                Event::Consume,
                Event::Published,
            ] {
                assert!(
                    state.transition(event).is_err(),
                    "terminal state must not replay"
                );
            }
        }
        assert!(State::AcceptedCall.transition(Event::Consume).is_err());
        assert!(State::AwaitingHuman.transition(Event::Published).is_err());
        assert!(
            !State::Consumed.result_ready(),
            "no success from missing outcome"
        );
        assert_eq!(REVIEW_TTL_MS, 300_000);
        assert_eq!(MAX_REVIEWS, 8);
    }
}
