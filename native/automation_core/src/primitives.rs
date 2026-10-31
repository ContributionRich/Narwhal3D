//! NEXT-RELEASE additive prototypes. Deliberately absent from all live tool catalogs.
//! Rust owns dimensions, context pins, review and one-use publication consumption.
use crate::{box_action, conversation::Token};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashSet;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(
    tag = "operation",
    content = "arguments",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum Arguments {
    Sphere {
        center: [f64; 3],
        radius: f64,
    },
    Cylinder {
        center: [f64; 3],
        radius: f64,
        height: f64,
    },
    Cone {
        center: [f64; 3],
        radius: f64,
        height: f64,
    },
}
impl Arguments {
    pub(crate) fn from_accepted_call(
        call: &crate::provider::ToolCall,
    ) -> Result<Self, &'static str> {
        let operation = match call.name.as_str() {
            "narwhal_create_sphere" => "sphere",
            "narwhal_create_cylinder" => "cylinder",
            "narwhal_create_cone" => "cone",
            _ => return Err("unsupported_tool"),
        };
        let args: Self =
            serde_json::from_value(json!({"operation":operation,"arguments":call.arguments}))
                .map_err(|_| "invalid_primitive_arguments")?;
        args.validate(0.)?;
        Ok(args)
    }
    pub(crate) fn validate(&self, tolerance: f64) -> Result<(), &'static str> {
        let (center, radius, height) = match self {
            Self::Sphere { center, radius } => (center, radius, None),
            Self::Cylinder {
                center,
                radius,
                height,
            }
            | Self::Cone {
                center,
                radius,
                height,
            } => (center, radius, Some(*height)),
        };
        if height.is_some_and(|h| {
            !h.is_finite() || h <= tolerance || h > 1e6 || (center[2] + h).abs() > 1e6
        }) {
            return Err("invalid_primitive_dimensions");
        }
        if !center.iter().all(|x| x.is_finite() && x.abs() <= 1e6)
            || !radius.is_finite()
            || *radius <= tolerance
            || *radius > 1e6
            || center.iter().any(|x| x.abs() + radius > 1e6)
        {
            return Err("invalid_primitive_dimensions");
        }
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GeometryFacts {
    pub proxy_solid: bool,
    pub airtight: bool,
    pub face_types: std::collections::BTreeMap<String, u64>,
    pub volume: f64,
    pub bounds: [[f64; 3]; 2],
    pub recipe_kind: String,
}
impl Arguments {
    pub fn check_geometry(&self, facts: GeometryFacts) -> Result<Value, &'static str> {
        self.validate(0.)?;
        let (kind, volume, bounds, faces) = match self {
            Self::Sphere { center, radius } => (
                "sphere",
                4. / 3. * std::f64::consts::PI * radius.powi(3),
                [center.map(|x| x - radius), center.map(|x| x + radius)],
                std::collections::BTreeMap::from([("SPHERE".into(), 1)]),
            ),
            Self::Cylinder {
                center,
                radius,
                height,
            } => (
                "cylinder",
                std::f64::consts::PI * radius.powi(2) * height,
                [
                    [center[0] - radius, center[1] - radius, center[2]],
                    [center[0] + radius, center[1] + radius, center[2] + height],
                ],
                std::collections::BTreeMap::from([("CYLINDER".into(), 1), ("PLANE".into(), 2)]),
            ),
            Self::Cone {
                center,
                radius,
                height,
            } => (
                "cone",
                std::f64::consts::PI * radius.powi(2) * height / 3.,
                [
                    [center[0] - radius, center[1] - radius, center[2]],
                    [center[0] + radius, center[1] + radius, center[2] + height],
                ],
                std::collections::BTreeMap::from([("CONE".into(), 1), ("PLANE".into(), 1)]),
            ),
        };
        if !facts.proxy_solid
            || !facts.airtight
            || facts.recipe_kind != kind
            || facts.face_types != faces
            || !facts.volume.is_finite()
            || (facts.volume - volume).abs() > volume * 1e-9
            || facts
                .bounds
                .iter()
                .flatten()
                .zip(bounds.iter().flatten())
                .any(|(a, b)| !a.is_finite() || (a - b).abs() > 1e-8 * b.abs().max(1.))
        {
            return Err("primitive_geometry_verification_failed");
        }
        Ok(
            json!({"status":"primitive_geometry_checked","prototype_only":true,"live_enabled":false}),
        )
    }
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Context {
    pub append: box_action::Context,
    pub undo_top_serial: u64,
    pub undo_next_serial: u64,
}
impl Context {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        self.append.validate()?;
        if self.undo_next_serial == 0 || self.undo_top_serial >= self.undo_next_serial {
            return Err("primitive_undo_state");
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Proposal {
    pub owner: Token,
    pub arguments: Arguments,
    pub expected: Context,
}
struct Pending {
    proposal: Proposal,
    approved: bool,
}
#[derive(Default)]
pub struct Gate {
    pending: Option<Pending>,
    used: HashSet<u64>,
}
impl Gate {
    pub fn clear(&mut self) {
        self.pending = None;
    }
    pub fn stage(
        &mut self,
        owner: Token,
        arguments: Arguments,
        expected: Context,
    ) -> Result<Value, &'static str> {
        expected.validate()?;
        arguments.validate(expected.append.tolerance)?;
        if self.pending.is_some() {
            return Err("primitive_approval_pending");
        }
        if self.used.len() >= 8 || !self.used.insert(owner.request) {
            return Err("primitive_request_replay");
        }
        let proposal = Proposal {
            owner,
            arguments,
            expected,
        };
        self.pending = Some(Pending {
            proposal: proposal.clone(),
            approved: false,
        });
        Ok(
            json!({"status":"primitive_review_required","prototype_only":true,"live_enabled":false,"proposal":proposal}),
        )
    }
    pub fn decide(
        &mut self,
        proposal: Proposal,
        current: Context,
        accepted: bool,
    ) -> Result<Value, &'static str> {
        let pending = self.pending.as_ref().ok_or("primitive_approval_missing")?;
        if pending.proposal != proposal || pending.approved {
            return Err("primitive_approval_mismatch");
        }
        if !accepted {
            self.clear();
            return Ok(json!({"status":"rejected","live_enabled":false}));
        }
        if current != proposal.expected || current.validate().is_err() {
            self.clear();
            return Err("primitive_context_stale");
        }
        self.pending.as_mut().unwrap().approved = true;
        Ok(
            json!({"status":"primitive_prototype_approved","prototype_only":true,"live_enabled":false}),
        )
    }
    pub fn take(&mut self, proposal: Proposal, current: Context) -> Result<Value, &'static str> {
        let pending = self.pending.as_ref().ok_or("primitive_approval_missing")?;
        if pending.proposal != proposal {
            return Err("primitive_approval_mismatch");
        }
        if !pending.approved {
            return Err("primitive_approval_required");
        }
        // Spend before checking freshness: a failed publication must never retry itself.
        self.clear();
        current.validate()?;
        if current != proposal.expected {
            return Err("primitive_context_stale");
        }
        Ok(
            json!({"status":"primitive_prototype_consumed","prototype_only":true,"live_enabled":false,
            "action":proposal.arguments,"context":proposal.expected}),
        )
    }
}
