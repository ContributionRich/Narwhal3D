//! Typed proposal/review gates only. No command, kernel, filesystem or execution API.
use crate::catalog::uuid;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashSet;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CircleArgs {
    pub center: [f64; 3],
    pub radius: f64,
}
impl CircleArgs {
    pub fn validate(&self) -> Result<(), &'static str> {
        if !self.center.iter().all(|v| v.is_finite() && v.abs() <= 1e9)
            || !self.radius.is_finite()
            || self.radius <= 0.0
            || self.radius > 1e9
        {
            return Err("invalid_circle_arguments");
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Target {
    pub object_id: String,
    pub content_token: String,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Preconditions {
    pub document_session: String,
    /// Main must supply an authoritative, monotonically changing edit/Undo/Redo token.
    pub change_token: u64,
    pub selection: Vec<Target>,
    pub layer_id: String,
    pub layer_locked: bool,
    pub layer_visible: bool,
    /// Origin, X, Y and Z axes from the active canonical construction plane.
    pub cplane: [[f64; 3]; 4],
    pub units: String,
    pub tolerance: f64,
    pub command_idle: bool,
}
impl Preconditions {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.document_session.is_empty()
            || self.document_session.len() > 128
            || self.units.is_empty()
            || self.units.len() > 64
            || !uuid(&self.layer_id)
            || !self.tolerance.is_finite()
            || self.tolerance <= 0.0
            || self.selection.len() > 64
        {
            return Err("invalid_preconditions");
        }
        if !self.command_idle {
            return Err("command_busy");
        }
        if self.layer_locked || !self.layer_visible {
            return Err("layer_unavailable");
        }
        let mut ids = HashSet::new();
        for target in &self.selection {
            if !uuid(&target.object_id)
                || target.content_token.is_empty()
                || target.content_token.len() > 128
                || !ids.insert(&target.object_id)
            {
                return Err("invalid_targets");
            }
        }
        if !self
            .cplane
            .iter()
            .flatten()
            .all(|v| v.is_finite() && v.abs() <= 1e9)
        {
            return Err("invalid_cplane");
        }
        let dot = |a: [f64; 3], b: [f64; 3]| a.iter().zip(b).map(|(a, b)| a * b).sum::<f64>();
        let [_, x, y, z] = self.cplane;
        let cross = [
            x[1] * y[2] - x[2] * y[1],
            x[2] * y[0] - x[0] * y[2],
            x[0] * y[1] - x[1] * y[0],
        ];
        if [x, y, z].iter().any(|a| (dot(*a, *a) - 1.).abs() > 1e-9)
            || dot(x, y).abs() > 1e-9
            || cross.iter().zip(z).any(|(a, b)| (a - b).abs() > 1e-9)
        {
            return Err("invalid_cplane");
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(
    tag = "operation",
    content = "arguments",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum Action {
    Circle(CircleArgs),
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Proposal {
    pub id: u64,
    #[serde(rename = "owner")]
    pub request_token: crate::conversation::Token,
    pub action: Action,
    pub expected: Option<Preconditions>,
}
struct Pending {
    proposal: Proposal,
    approved: bool,
    spent: bool,
}
#[derive(Default)]
pub struct ActionGate {
    next: u64,
    pending: Option<Pending>,
}
impl ActionGate {
    pub fn clear(&mut self) {
        self.pending = None;
    }
    pub fn stage_for(
        &mut self,
        request_token: crate::conversation::Token,
        args: CircleArgs,
        expected: Option<Preconditions>,
    ) -> Result<Proposal, &'static str> {
        args.validate()?;
        if self.pending.is_some() {
            return Err("proposal_pending");
        }
        if let Some(ref expected) = expected {
            expected.validate()?;
        }
        self.next += 1;
        let proposal = Proposal {
            id: self.next,
            request_token,
            action: Action::Circle(args),
            expected,
        };
        self.pending = Some(Pending {
            proposal: proposal.clone(),
            approved: false,
            spent: false,
        });
        Ok(proposal)
    }
    #[cfg(test)]
    fn stage(
        &mut self,
        args: CircleArgs,
        expected: Option<Preconditions>,
    ) -> Result<Proposal, &'static str> {
        self.stage_for(
            crate::conversation::Token {
                conversation: 1,
                generation: 1,
                request: 1,
            },
            args,
            expected,
        )
    }

    pub fn review(
        &mut self,
        proposal: Proposal,
        current: Option<Preconditions>,
        accepted: bool,
    ) -> Result<Value, &'static str> {
        let pending = self.pending.as_mut().ok_or("no_proposal")?;
        if pending.proposal != proposal {
            return Err("approval_mismatch");
        }
        if pending.approved || pending.spent {
            return Err("approval_replay");
        }
        if !accepted {
            self.clear();
            return Ok(json!({"status":"rejected","dispatch_enabled":false}));
        }
        let expected = pending
            .proposal
            .expected
            .as_ref()
            .ok_or("authoritative_preconditions_required")?;
        let current = current.ok_or("authoritative_preconditions_required")?;
        current.validate()?;
        if *expected != current {
            return Err("stale_preconditions");
        }
        pending.approved = true;
        Ok(
            json!({"status":"reviewed","dispatch_enabled":false,"blocker":"main_command_undo_dispatch_not_integrated"}),
        )
    }
    pub fn owner(&self, id: u64) -> Result<&crate::conversation::Token, &'static str> {
        let pending = self.pending.as_ref().ok_or("no_proposal")?;
        if pending.proposal.id != id || pending.spent {
            return Err("proposal_replay");
        }
        Ok(&pending.proposal.request_token)
    }

    fn checked(&self, id: u64, current: &Preconditions) -> Result<&Pending, &'static str> {
        let pending = self.pending.as_ref().ok_or("no_proposal")?;
        if pending.proposal.id != id || pending.spent {
            return Err("proposal_replay");
        }
        if !pending.approved {
            return Err("approval_required");
        }
        current.validate()?;
        if pending.proposal.expected.as_ref() != Some(current) {
            return Err("stale_preconditions");
        }
        Ok(pending)
    }
    /// Pure, one-use validation token consumption for a future reviewed dispatcher.
    /// NOT exported over the current C ABI; no production caller can execute a CAD action.
    pub fn consume(&mut self, id: u64, current: &Preconditions) -> Result<Action, &'static str> {
        let action = self.checked(id, current)?.proposal.action.clone();
        self.pending.as_mut().unwrap().spent = true;
        Ok(action)
    }
    pub fn production_check(
        &self,
        id: u64,
        current: Option<Preconditions>,
    ) -> Result<Value, &'static str> {
        let current = current.ok_or("authoritative_preconditions_required")?;
        self.checked(id, &current)?;
        Ok(
            json!({"status":"blocked","dispatch_enabled":false,"blocker":"main_command_undo_dispatch_not_integrated"}),
        )
    }
}

pub fn operations() -> Value {
    let disabled_prototypes = ["sphere", "cylinder", "cone"]
        .iter()
        .map(|operation| {
            json!({
        "operation":operation,"prototype_implemented":true,"execution_available":false,
        "proposal_available":false,"scope":"new_world_aligned_analytic_solid_only",
        "blocker":"main_geometry_approval_rollback_undo_review_and_ui_activation_required"})
        })
        .collect::<Vec<_>>();
    json!({"operations":[{"operation":"circle","command":"Circle","mode":"center_radius_active_cplane",
        "arguments_schema":circle_schema(),"proposal_available":true,"execution_available":false,
        "scope":"create_one_curve_only","representation":"exact_rational_nurbs",
        "blockers":["authoritative_document_session_and_change_token","checked_target_content_and_cplane_layer_state","approved_main_thread_command_undo_dispatch"],
        "evidence":{"source":"commands.CircleCmd.execute","status":"source_inspected_not_workflow_executed"}},
        {"operation":"box","command":"Box","mode":"world_xyz_append_only",
         "arguments_schema":crate::box_action::schema(),"proposal_available":true,
         "execution_available":true,"requires_explicit_ui_approval":true,
         "scope":"in_app_assistant_only_create_one_new_solid_never_edit_targets",
         "representation":"analytic_brep_and_occt_master",
         "blockers":["explicit_in_app_approval","unchanged_document_units_and_available_layer","idle_command_and_enabled_undo"]}],
        "read_tools":crate::catalog::READS.iter().map(|(name,alias,_)|json!({"name":name,"alias":alias,"mutation":false})).collect::<Vec<_>>(),
        "planning_operations":planning_operations(),
        "disabled_prototypes":disabled_prototypes,
        "provenance":{"source":"native_automation_catalog","registry_membership_is_not_execution_support":true}})
}

/// Source-inspected command implementations, not callable executors or promises
/// about arbitrary input geometry. OURS: the assistant plans a bounded subset.
pub fn planning_operations() -> Value {
    json!([
        {"operation":"curve","command":"Curve","execution_available":false,
         "source":"commands.CurveCmd.execute/bridge.make_polyline",
         "representation":"nurbs_curve","unsupported_options":[],
         "blockers":["curve_executor_not_integrated"]},
        {"operation":"loft","command":"Loft","execution_available":false,
         "source":"commands.LoftCmd.execute/bridge.loft_brep",
         "representation":"nurbs_surface","unsupported_options":["Closed=Yes","Point"],
         "blockers":["surfacing_executor_not_integrated","closure_and_continuity_need_verification"]},
        {"operation":"pipe","command":"Pipe","execution_available":false,
         "source":"commands.PipeCmd.execute/bridge.pipe_occt",
         "representation":"analytic_constant_radius_pipe","unsupported_options":["Thick","Cap=Round","tapered_radius"],
         "blockers":["pipe_executor_not_integrated","rail_support_requires_analytic_kernel_check"]},
        {"operation":"boolean_difference","command":"BooleanDifference","execution_available":false,
         "source":"commands.BooleanDifferenceCmd/bridge.boolean_brep",
         "representation":"requires_analytic_operands_and_master","unsupported_options":[],
         "blockers":["boolean_executor_not_integrated","analytic_route_must_not_fall_back_to_facets"]}
    ])
}
pub fn circle_schema() -> Value {
    json!({"type":"object","properties":{"center":{"type":"array","items":{"type":"number"},"minItems":3,"maxItems":3},"radius":{"type":"number","exclusiveMinimum":0,"maximum":1e9}},"required":["center","radius"],"additionalProperties":false})
}

#[cfg(test)]
mod tests {
    use super::*;
    fn state() -> Preconditions {
        Preconditions {
            document_session: "test-fixture-not-authority".into(),
            change_token: 9,
            selection: vec![],
            layer_id: "00000000-0000-0000-0000-000000000001".into(),
            layer_locked: false,
            layer_visible: true,
            cplane: [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]],
            units: "Millimeters".into(),
            tolerance: 0.01,
            command_idle: true,
        }
    }
    fn args() -> CircleArgs {
        CircleArgs {
            center: [1., 2., 3.],
            radius: 5.,
        }
    }
    #[test]
    fn missing_authority_approval_and_production_dispatch_fail_closed() {
        let mut g = ActionGate::default();
        let p = g.stage(args(), None).unwrap();
        assert_eq!(
            g.review(p, None, true).unwrap_err(),
            "authoritative_preconditions_required"
        );
        g.clear();
        let p = g.stage(args(), Some(state())).unwrap();
        assert_eq!(g.consume(p.id, &state()).unwrap_err(), "approval_required");
        g.review(p.clone(), Some(state()), true).unwrap();
        assert_eq!(
            g.production_check(p.id, Some(state())).unwrap()["dispatch_enabled"],
            false
        );
        assert!(g.consume(p.id, &state()).is_ok());
        assert_eq!(g.consume(p.id, &state()).unwrap_err(), "proposal_replay");
    }
    #[test]
    fn exact_proposal_and_preconditions_bind_approval() {
        let mut g = ActionGate::default();
        let p = g.stage(args(), Some(state())).unwrap();
        let mut changed = p.clone();
        changed.action = Action::Circle(CircleArgs {
            radius: 6.,
            ..args()
        });
        assert_eq!(
            g.review(changed, Some(state()), true).unwrap_err(),
            "approval_mismatch"
        );
        g.review(p.clone(), Some(state()), true).unwrap();
        assert_eq!(
            g.review(p.clone(), Some(state()), true).unwrap_err(),
            "approval_replay"
        );
        for kind in 0..5 {
            let mut current = state();
            match kind {
                0 => current.change_token += 1,
                1 => current.document_session = "replacement".into(),
                2 => current.units = "Inches".into(),
                3 => current.cplane[0][0] = 2.,
                _ => current.layer_id = "00000000-0000-0000-0000-000000000002".into(),
            };
            assert_eq!(
                g.consume(p.id, &current).unwrap_err(),
                "stale_preconditions"
            );
        }
        let mut busy = state();
        busy.command_idle = false;
        assert_eq!(g.consume(p.id, &busy).unwrap_err(), "command_busy");
        let mut locked = state();
        locked.layer_locked = true;
        assert_eq!(g.consume(p.id, &locked).unwrap_err(), "layer_unavailable");
        g.clear();
        assert_eq!(g.consume(p.id, &state()).unwrap_err(), "no_proposal");
    }
}
