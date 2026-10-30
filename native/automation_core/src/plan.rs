//! OURS: bounded CAD plan descriptions, never executable command strings or approval.
use crate::{actions::CircleArgs, box_action::BoxArgs, catalog};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use std::collections::BTreeMap;

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Plan {
    pub version: u8,
    pub goal: String,
    pub units: String,
    pub intent: Intent,
    pub questions: Vec<String>,
    pub steps: Vec<Step>,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Intent {
    Unspecified,
    Advice,
    VisualStudy,
    Manufacturable,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Step {
    pub id: String,
    pub depends_on: Vec<String>,
    pub inputs: Vec<InputRef>,
    pub action: Action,
    pub expected: Expected,
    pub verify: Vec<Predicate>,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(
    tag = "kind",
    content = "id",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum InputRef {
    Object(String),
    Step(String),
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum OutputKind {
    Metadata,
    Curve,
    Surface,
    Solid,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Expected {
    pub kind: OutputKind,
    pub count: u8,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Predicate {
    ObjectCount {
        count: u8,
    },
    ExactGeometry,
    Airtight,
    Volume {
        expected: f64,
        relative_tolerance: f64,
    },
    Bounds {
        min: [f64; 3],
        max: [f64; 3],
    },
    Continuity {
        order: u8,
    },
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(
    tag = "operation",
    content = "arguments",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum Action {
    Read {
        tool: String,
        arguments: Map<String, Value>,
    },
    Box(BoxArgs),
    Circle(CircleArgs),
    Curve {
        points: Vec<[f64; 3]>,
    },
    Loft {
        style: LoftStyle,
    },
    Pipe {
        radius: f64,
        cap: Cap,
    },
    BooleanDifference {},
    Unsupported {
        need: String,
    },
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
pub enum LoftStyle {
    Normal,
    Loose,
    Tight,
    Straight,
    Uniform,
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
pub enum Cap {
    None,
    Flat,
}

fn label(s: &str, max: usize) -> bool {
    !s.trim().is_empty() && s.len() <= max && !s.chars().any(char::is_control)
}
fn identifier(s: &str) -> bool {
    !s.is_empty() && s.len() <= 32 && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
}
fn point(p: &[f64; 3]) -> bool {
    p.iter().all(|x| x.is_finite() && x.abs() <= 1e6)
}
fn positive(x: f64) -> bool {
    x.is_finite() && x > 0. && x <= 1e6
}

impl Action {
    pub fn operation(&self) -> &str {
        match self {
            Self::Read { .. } => "read",
            Self::Box(_) => "box",
            Self::Circle(_) => "circle",
            Self::Curve { .. } => "curve",
            Self::Loft { .. } => "loft",
            Self::Pipe { .. } => "pipe",
            Self::BooleanDifference {} => "boolean_difference",
            Self::Unsupported { .. } => "unsupported",
        }
    }
    fn validate(&self) -> Result<Option<OutputKind>, &'static str> {
        Ok(Some(match self {
            Self::Read { tool, arguments } => {
                catalog::validate_read(tool, arguments)?;
                OutputKind::Metadata
            }
            Self::Box(args) => {
                args.validate()?;
                OutputKind::Solid
            }
            Self::Circle(args) => {
                args.validate()?;
                OutputKind::Curve
            }
            Self::Curve { points } => {
                if !(2..=64).contains(&points.len()) || !points.iter().all(point) {
                    return Err("invalid_plan_points");
                }
                OutputKind::Curve
            }
            Self::Loft { .. } => OutputKind::Surface,
            Self::Pipe { radius, cap } => {
                if !positive(*radius) {
                    return Err("invalid_plan_radius");
                }
                if *cap == Cap::Flat {
                    OutputKind::Solid
                } else {
                    OutputKind::Surface
                }
            }
            Self::BooleanDifference {} => OutputKind::Solid,
            Self::Unsupported { need } => {
                if !label(need, 256) {
                    return Err("invalid_plan_need");
                }
                return Ok(None);
            }
        }))
    }
}
impl Plan {
    pub fn validate(&self) -> Result<(), &'static str> {
        if serde_json::to_vec(self).map_err(|_| "invalid_plan")?.len() > 16_384
            || self.steps.len() > 16
            || self.questions.len() > 4
        {
            return Err("plan_budget");
        }
        if self.version != 1
            || !label(&self.goal, 512)
            || !label(&self.units, 64)
            || !self.questions.iter().all(|q| label(q, 256))
            || self.steps.is_empty()
        {
            return Err("invalid_plan");
        }
        let mut prior = BTreeMap::new();
        for step in &self.steps {
            if !identifier(&step.id) || prior.contains_key(&step.id) {
                return Err("plan_step_identity");
            }
            if step.depends_on.len() > 8 || step.inputs.len() > 8 || step.verify.len() > 8 {
                return Err("plan_budget");
            }
            let mut dependencies = std::collections::BTreeSet::new();
            for dependency in &step.depends_on {
                if !prior.contains_key(dependency) || !dependencies.insert(dependency) {
                    return Err("plan_dependency");
                }
            }
            for input in &step.inputs {
                match input {
                    InputRef::Object(id) if !catalog::uuid(id) => return Err("plan_object_id"),
                    InputRef::Step(id) if !dependencies.contains(id) => {
                        return Err("plan_input_dependency");
                    }
                    _ => {}
                }
            }
            let kind = step.action.validate()?;
            if !(1..=32).contains(&step.expected.count)
                || kind.is_some_and(|k| k != step.expected.kind)
            {
                return Err("plan_output_type");
            }
            if !matches!(
                step.action,
                Action::Unsupported { .. } | Action::BooleanDifference {}
            ) && step.expected.count != 1
            {
                return Err("plan_output_count");
            }
            let required = match step.action {
                Action::Loft { .. } | Action::BooleanDifference {} => 2,
                Action::Pipe { .. } => 1,
                _ => 0,
            };
            if step.inputs.len() < required {
                return Err("plan_missing_inputs");
            }
            for input in &step.inputs {
                if let InputRef::Step(id) = input {
                    let expected = match step.action {
                        Action::Loft { .. } | Action::Pipe { .. } => Some(OutputKind::Curve),
                        Action::BooleanDifference {} => Some(OutputKind::Solid),
                        _ => None,
                    };
                    if expected.is_some_and(|kind| prior[id] != kind) {
                        return Err("plan_input_type");
                    }
                }
            }
            if step.verify.is_empty() {
                return Err("plan_verification_required");
            }
            for predicate in &step.verify {
                match predicate {
                    Predicate::ObjectCount { count } if *count != step.expected.count => {
                        return Err("plan_count_predicate");
                    }
                    Predicate::Volume {
                        expected,
                        relative_tolerance,
                    } if !expected.is_finite()
                        || *expected <= 0.
                        || !relative_tolerance.is_finite()
                        || *relative_tolerance <= 0.
                        || *relative_tolerance > 0.01 =>
                    {
                        return Err("plan_volume_predicate");
                    }
                    Predicate::Volume { .. } | Predicate::Airtight
                        if step.expected.kind != OutputKind::Solid =>
                    {
                        return Err("plan_predicate_type");
                    }
                    Predicate::Bounds { min, max }
                        if !point(min)
                            || !point(max)
                            || min.iter().zip(max).any(|(a, b)| a > b) =>
                    {
                        return Err("plan_bounds_predicate");
                    }
                    Predicate::Continuity { order }
                        if *order > 2
                            || !matches!(
                                step.expected.kind,
                                OutputKind::Curve | OutputKind::Surface
                            ) =>
                    {
                        return Err("plan_continuity_predicate");
                    }
                    Predicate::ExactGeometry | Predicate::Bounds { .. }
                        if step.expected.kind == OutputKind::Metadata =>
                    {
                        return Err("plan_predicate_type");
                    }
                    _ => {}
                }
            }
            // A manufacturable solid needs explicit exactness and closure checks.
            if self.intent == Intent::Manufacturable
                && step.expected.kind == OutputKind::Solid
                && (!step
                    .verify
                    .iter()
                    .any(|p| matches!(p, Predicate::ExactGeometry))
                    || !step.verify.iter().any(|p| matches!(p, Predicate::Airtight)))
            {
                return Err("plan_manufacturing_verification");
            }
            prior.insert(step.id.clone(), step.expected.kind);
        }
        Ok(())
    }
    pub fn describe(&self) -> Result<Value, &'static str> {
        self.validate()?;
        Ok(
            json!({"status":"plan_validated","schema_version":1,"plan":self,
            "executed":false,"approved":false,"verification_status":"not_performed"}),
        )
    }
}

/// Facts accepted from correlated host read results only. Strings in provider
/// prose, object names and plans cannot populate this store. It is live metadata,
/// not an authoritative geometry revision or a grant to modify selected objects.
#[derive(Default)]
pub struct Evidence {
    units: Option<String>,
    idle: Option<bool>,
    layer_available: Option<bool>,
    objects: std::collections::BTreeSet<String>,
}
impl Evidence {
    pub fn observe(
        &mut self,
        call: &crate::provider::ToolCall,
        result: &crate::provider::ToolResult,
    ) {
        if result.is_error {
            return;
        }
        let out = &result.output;
        if call.name == "narwhal_workspace" {
            self.units = out
                .get("unit_system")
                .and_then(Value::as_str)
                .filter(|s| label(s, 64))
                .map(str::to_owned);
            self.idle = out
                .pointer("/active_command/busy")
                .and_then(Value::as_bool)
                .map(|busy| !busy);
            self.layer_available = out.get("current_layer_effective").and_then(|flags| {
                if flags.get("available")?.as_bool()? {
                    Some(flags.get("visible")?.as_bool()? && !flags.get("locked")?.as_bool()?)
                } else {
                    None
                }
            });
        }
        let rows: Vec<&Value> = match call.name.as_str() {
            "narwhal_selection_summary" | "narwhal_objects" => out
                .get("objects")
                .and_then(Value::as_array)
                .map(|r| r.iter().take(64).collect())
                .unwrap_or_default(),
            "narwhal_object_summary" => vec![out],
            _ => vec![],
        };
        for row in rows {
            if self.objects.len() >= 128 {
                break;
            }
            if let Some(id) = row
                .get("object_id")
                .and_then(Value::as_str)
                .filter(|id| catalog::uuid(id))
            {
                // A single-object result must also agree with the requested UUID.
                if call.name != "narwhal_object_summary"
                    || call
                        .arguments
                        .get("object_id")
                        .and_then(Value::as_str)
                        .is_some_and(|wanted| wanted.eq_ignore_ascii_case(id))
                {
                    self.objects.insert(id.to_ascii_lowercase());
                }
            }
        }
    }
}
impl Plan {
    pub fn preflight(&self, evidence: &Evidence) -> Result<Value, &'static str> {
        self.validate()?;
        let catalog = crate::actions::operations();
        let mut steps = vec![];
        for step in &self.steps {
            let operation = step.action.operation();
            let available =
                matches!(step.action, Action::Read { .. })
                    || catalog["operations"].as_array().unwrap().iter().any(|row| {
                        row["operation"] == operation && row["execution_available"] == true
                    });
            let mut blockers: Vec<&str> = vec![];
            if !available {
                blockers.push("executor_not_integrated");
            }
            if !matches!(step.action, Action::Read { .. }) {
                if evidence.units.as_deref() != Some(self.units.as_str()) {
                    blockers.push("workspace_units_missing_or_changed");
                }
                if evidence.idle != Some(true) {
                    blockers.push("command_idle_not_verified");
                }
                if evidence.layer_available != Some(true) {
                    blockers.push("destination_layer_unavailable");
                }
                if !self.questions.is_empty() || self.intent == Intent::Unspecified {
                    blockers.push("clarification_required");
                }
                if available {
                    blockers.push("separate_explicit_action_approval_required");
                }
            }
            for input in &step.inputs {
                if let InputRef::Object(id) = input {
                    if !evidence.objects.contains(&id.to_ascii_lowercase()) {
                        blockers.push("object_not_inspected_in_current_turn");
                    }
                    // Existing metadata cannot prove canonical topology or contents.
                    blockers.push("fresh_geometry_and_scope_verification_required");
                }
            }
            blockers.sort_unstable();
            blockers.dedup();
            steps.push(json!({"id":step.id,"operation":operation,"depends_on":step.depends_on,
                "expected":step.expected,"verification_predicates":step.verify,
                "execution_available":available,"executed":false,"blockers":blockers,
                "unsupported_need":if let Action::Unsupported{need}=&step.action {Some(need)} else {None}}));
        }
        let unavailable = steps
            .iter()
            .filter(|step| step["execution_available"] == false)
            .count();
        let summary = format!(
            "Plan only: {} steps, {unavailable} without live executors. No geometry created or verified. Resolve stated blockers; each available action still needs its own explicit approval.",
            steps.len()
        );
        Ok(
            json!({"status":"plan_preflight","summary":summary,"steps":steps,"questions":self.questions,
            "executed":false,"approved":false,"created_object_ids":[],"verification_status":"not_performed",
            "geometry_substitution":false,"consistency":"live_metadata_not_revision_snapshot",
            "requires_fresh_scope_before_action":true}),
        )
    }
}
