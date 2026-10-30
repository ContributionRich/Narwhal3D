//! UI-only approval for appending ONE world-aligned analytic box.
//!
//! This is not the general edit gate: no existing geometry is targeted or changed.
//! Geometry edits elsewhere do not invalidate an independent append. Document
//! identity, units, destination layer and an idle undo/command boundary do.
//! The host supplies these values; provider tool arguments cannot supply authority.
use crate::{catalog::uuid, conversation::Token};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BoxArgs {
    pub origin: [f64; 3],
    pub size: [f64; 3],
}
impl BoxArgs {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.origin.iter().any(|v| !v.is_finite() || v.abs() > 1e6)
            || self
                .size
                .iter()
                .any(|v| !v.is_finite() || *v <= 0.0 || *v > 1e6)
        {
            return Err("invalid_box_arguments");
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Context {
    pub document_session: String,
    pub layer_id: String,
    pub units: String,
    pub tolerance: f64,
    pub layer_available: bool,
    pub command_idle: bool,
    pub undo_ready: bool,
}
impl Context {
    pub fn validate(&self) -> Result<(), &'static str> {
        if !(uuid(&self.document_session)
            || (self.document_session.len() == 32
                && self.document_session.bytes().all(|c| c.is_ascii_hexdigit())))
            || !uuid(&self.layer_id)
            || self.units.is_empty()
            || self.units.len() > 64
            || !self.tolerance.is_finite()
            || self.tolerance <= 0.0
            || !self.layer_available
            || !self.command_idle
            || !self.undo_ready
        {
            return Err("box_context_unavailable");
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Proposal {
    #[serde(rename = "owner")]
    pub request_token: Token,
    pub call_id: String,
    pub arguments: BoxArgs,
    pub expected: Context,
}
#[derive(Default)]
pub struct Gate {
    pending: Option<Proposal>,
}
impl Gate {
    pub fn clear(&mut self) {
        self.pending = None;
    }
    pub fn stage(
        &mut self,
        request_token: Token,
        call_id: String,
        arguments: BoxArgs,
        expected: Context,
    ) -> Result<Proposal, &'static str> {
        arguments.validate()?;
        expected.validate()?;
        if arguments.size.iter().any(|v| *v <= expected.tolerance) {
            return Err("box_below_document_tolerance");
        }
        if self.pending.is_some() {
            return Err("box_approval_pending");
        }
        let proposal = Proposal {
            request_token,
            call_id,
            arguments,
            expected,
        };
        self.pending = Some(proposal.clone());
        Ok(proposal)
    }
    pub fn decide(
        &mut self,
        proposal: Proposal,
        current: Context,
        accepted: bool,
    ) -> Result<Value, &'static str> {
        if self.pending.as_ref() != Some(&proposal) {
            return Err("box_approval_mismatch");
        }
        // Every decision spends this proposal, including rejection/stale state.
        self.pending = None;
        if !accepted {
            return Ok(json!({"status":"rejected"}));
        }
        current.validate()?;
        if proposal.expected != current {
            return Err("box_context_stale");
        }
        Ok(json!({"status":"approved_box", "arguments":proposal.arguments, "context":current}))
    }
}
pub fn schema() -> Value {
    json!({"type":"object","properties":{
        "origin":{"type":"array","items":{"type":"number","minimum":-1e6,"maximum":1e6},"minItems":3,"maxItems":3},
        "size":{"type":"array","items":{"type":"number","exclusiveMinimum":0,"maximum":1e6},"minItems":3,"maxItems":3}},
        "required":["origin","size"],"additionalProperties":false})
}

#[cfg(test)]
mod tests {
    use super::*;
    fn context() -> Context {
        Context {
            document_session: "00000000-0000-0000-0000-000000000001".into(),
            layer_id: "00000000-0000-0000-0000-000000000002".into(),
            units: "Millimeters".into(),
            tolerance: 0.01,
            layer_available: true,
            command_idle: true,
            undo_ready: true,
        }
    }
    fn stage(gate: &mut Gate) -> Proposal {
        gate.stage(
            Token {
                conversation: 1,
                generation: 2,
                request: 3,
            },
            "call-box".into(),
            BoxArgs {
                origin: [1., 2., 3.],
                size: [10., 10., 10.],
            },
            context(),
        )
        .unwrap()
    }
    #[test]
    fn explicit_approval_is_exact_and_one_use() {
        let mut gate = Gate::default();
        let p = stage(&mut gate);
        let mut forged = p.clone();
        forged.arguments.size[0] = 20.;
        assert!(gate.decide(forged, context(), true).is_err());
        assert_eq!(
            gate.decide(p.clone(), context(), true).unwrap()["status"],
            "approved_box"
        );
        assert!(gate.decide(p, context(), true).is_err());
    }
    #[test]
    fn reject_cancel_and_context_changes_never_approve() {
        let mut gate = Gate::default();
        let p = stage(&mut gate);
        assert_eq!(
            gate.decide(p.clone(), context(), false).unwrap()["status"],
            "rejected"
        );
        assert!(gate.decide(p, context(), true).is_err());
        let p = stage(&mut gate);
        gate.clear();
        assert!(gate.decide(p, context(), true).is_err());
        for index in 0..7 {
            let p = stage(&mut gate);
            let mut c = context();
            match index {
                0 => c.document_session = c.layer_id.clone(),
                1 => c.layer_id = c.document_session.clone(),
                2 => c.units = "Inches".into(),
                3 => c.tolerance = 0.02,
                4 => c.layer_available = false,
                5 => c.command_idle = false,
                _ => c.undo_ready = false,
            }
            assert!(gate.decide(p, c, true).is_err());
        }
    }
    #[test]
    fn typed_bounds_and_tolerance_are_enforced() {
        for size in [0., -1., f64::NAN, f64::INFINITY, 1e7] {
            assert!(
                BoxArgs {
                    origin: [0.; 3],
                    size: [size; 3]
                }
                .validate()
                .is_err()
            );
        }
        assert!(
            serde_json::from_value::<BoxArgs>(json!({"origin":[0,0,0],"size":[1,1,true]})).is_err()
        );
        assert!(
            serde_json::from_value::<BoxArgs>(
                json!({"origin":[0,0,0],"size":[1,1,1],"execute":true})
            )
            .is_err()
        );
        let mut gate = Gate::default();
        assert!(
            gate.stage(
                Token {
                    conversation: 1,
                    generation: 1,
                    request: 1
                },
                "x".into(),
                BoxArgs {
                    origin: [0.; 3],
                    size: [0.001; 3]
                },
                context()
            )
            .is_err()
        );
    }
}
