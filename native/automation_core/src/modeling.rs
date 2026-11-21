//! Generalized exact-CAD batches. No kernel, filesystem, provider or host calls.
//! Authority enters only through trusted-host mode/decision calls, never model text.
use crate::{catalog, conversation::Token, provider::ToolResult};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, HashSet},
    time::{Duration, Instant},
};

/// OURS: deterministic camera basis, looking toward the target from a relative
/// direction. Only display orientation changes; no document geometry is touched.
pub fn view_basis(direction: [f64; 3]) -> Result<Value, &'static str> {
    if direction.iter().any(|v| !v.is_finite() || v.abs() > 1e6) {
        return Err("invalid_view_direction");
    }
    let norm = direction.iter().map(|v| v * v).sum::<f64>().sqrt();
    if norm < 1e-9 {
        return Err("invalid_view_direction");
    }
    let back = direction.map(|v| v / norm);
    let up = if back[2].abs() > 0.99 {
        [0., 1., 0.]
    } else {
        [0., 0., 1.]
    };
    let cross = |a: [f64; 3], b: [f64; 3]| {
        [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ]
    };
    let right = cross(up, back);
    let length = right.iter().map(|v| v * v).sum::<f64>().sqrt();
    let right = right.map(|v| v / length);
    let up = cross(back, right);
    Ok(json!({"rotation_rows":[[right[0],up[0],back[0]],
        [right[1],up[1],back[1]],[right[2],up[2],back[2]]]}))
}

/// Conservative perspective fit for a bounding sphere. Projection diagonal comes
/// from the actual host viewport, so narrow panes and lens changes remain safe.
/// This is display coordination only; no geometry is approximated or changed.
pub fn framing(bounds: Vec<[[f64; 3]; 2]>, projection: [f64; 2]) -> Result<Value, &'static str> {
    if bounds.is_empty()
        || bounds.len() > MAX_STEPS
        || projection
            .iter()
            .any(|s| !s.is_finite() || *s <= 0.0 || *s > 1e6)
    {
        return Err("invalid_framing_input");
    }
    let mut low = [f64::INFINITY; 3];
    let mut high = [f64::NEG_INFINITY; 3];
    for b in bounds {
        for i in 0..3 {
            if !b[0][i].is_finite()
                || !b[1][i].is_finite()
                || b[0][i] > b[1][i]
                || b.iter().any(|p| p[i].abs() > 1e9)
            {
                return Err("invalid_framing_input");
            }
            low[i] = low[i].min(b[0][i]);
            high[i] = high[i].max(b[1][i]);
        }
    }
    let center: [f64; 3] = std::array::from_fn(|i| (low[i] + high[i]) / 2.0);
    let radius = (0..3)
        .map(|i| ((high[i] - low[i]) / 2.0).powi(2))
        .sum::<f64>()
        .sqrt()
        .max(0.001);
    let scale = projection[0].max(projection[1]);
    let distance = radius * (1.0 + scale * scale).sqrt() * 1.15;
    Ok(json!({"center":center,"distance":distance}))
}

pub const MAX_STEPS: usize = 48;
const MAX_BATCH_BYTES: usize = 48_000;
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    #[default]
    Review,
    Build,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Batch {
    pub label: String,
    pub steps: Vec<Step>,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Step {
    pub id: String,
    pub name: String,
    /// Unpublished intermediate geometry can be referenced by later steps.
    pub publish: bool,
    /// Explicit stable UUID replacement; never name-based targeting or deletion.
    pub replace: Option<String>,
    pub action: Action,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Action {
    Undo {},
    Point {
        point: [f64; 3],
    },
    Circle {
        center: [f64; 3],
        radius: f64,
    },
    Curve {
        points: Vec<[f64; 3]>,
        closed: bool,
    },
    Nurbs {
        degree: usize,
        points: Vec<[f64; 4]>,
        knots: Vec<f64>,
        closed: bool,
    },
    Box {
        origin: [f64; 3],
        size: [f64; 3],
    },
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
    Transform {
        source: String,
        translation: [f64; 3],
        scale: [f64; 3],
        center: [f64; 3],
        rotation_degrees: f64,
        axis: [f64; 3],
    },
    Revolve {
        source: String,
        axis_start: [f64; 3],
        axis_end: [f64; 3],
        start_degrees: f64,
        end_degrees: f64,
        solid: bool,
    },
    Pipe {
        source: String,
        radius: f64,
        cap: bool,
    },
    Extrude {
        source: String,
        distance: f64,
        direction: [f64; 3],
        solid: bool,
    },
    Boolean {
        left: String,
        right: String,
        kind: BooleanKind,
    },
    Loft {
        sections: Vec<String>,
    },
    Bevel {
        source: String,
        size: f64,
        chamfer: bool,
    },
    Thickness {
        source: String,
        distance: f64,
    },
    Outline {
        source: String,
    },
    Attributes {
        source: String,
        name: Option<String>,
        layer_id: Option<String>,
        color: Option<[u8; 3]>,
        visible: Option<bool>,
    },
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum BooleanKind {
    Union,
    Difference,
    Intersection,
}
fn bounded(v: f64) -> bool {
    v.is_finite() && v.abs() <= 1e6
}
fn positive(v: f64, tol: f64) -> bool {
    bounded(v) && v > tol.max(1e-9)
}
fn vector(v: &[f64; 3]) -> bool {
    v.iter().all(|x| bounded(*x))
}
fn direction(v: &[f64; 3]) -> bool {
    vector(v) && v.iter().map(|x| x * x).sum::<f64>() > 1e-18
}
impl Action {
    fn attributes_verified(&self, fact: &Value) -> bool {
        let Self::Attributes {
            name,
            layer_id,
            color,
            visible,
            ..
        } = self
        else {
            return true;
        };
        let attrs = &fact["attributes"];
        name.as_ref().is_none_or(|n| fact["name"] == *n)
            && layer_id.as_ref().is_none_or(|id| attrs["layer_id"] == *id)
            && visible.is_none_or(|v| attrs["visible"] == v)
            && color.is_none_or(|rgb| {
                attrs["color"] == json!([rgb[0], rgb[1], rgb[2], 255])
                    && attrs["color_source"]
                        .as_str()
                        .is_some_and(|s| s == "ObjectColorSource.ColorFromObject")
            })
    }

    pub fn references(&self) -> Vec<&str> {
        match self {
            Self::Transform { source, .. }
            | Self::Extrude { source, .. }
            | Self::Bevel { source, .. }
            | Self::Thickness { source, .. }
            | Self::Outline { source }
            | Self::Attributes { source, .. } => vec![source],
            Self::Pipe { source, .. } | Self::Revolve { source, .. } => vec![source],
            Self::Boolean { left, right, .. } => vec![left, right],
            Self::Loft { sections } => sections.iter().map(String::as_str).collect(),
            _ => vec![],
        }
    }
    fn validate(&self, tol: f64) -> bool {
        match self {
            Self::Undo { .. } => true,
            Self::Attributes {
                name,
                layer_id,
                color,
                visible,
                ..
            } => {
                (name.is_some() || layer_id.is_some() || color.is_some() || visible.is_some())
                    && name.as_ref().is_none_or(|n| {
                        !n.trim().is_empty() && n.len() <= 96 && !n.chars().any(char::is_control)
                    })
                    && layer_id.as_ref().is_none_or(|id| catalog::uuid(id))
            }
            Self::Outline { .. } => true,
            Self::Revolve {
                axis_start,
                axis_end,
                start_degrees,
                end_degrees,
                ..
            } => {
                vector(axis_start)
                    && vector(axis_end)
                    && direction(&std::array::from_fn(|i| axis_end[i] - axis_start[i]))
                    && start_degrees.is_finite()
                    && end_degrees.is_finite()
                    && start_degrees.abs() <= 360.
                    && end_degrees.abs() <= 720.
                    && end_degrees - start_degrees > 1e-6
                    && end_degrees - start_degrees <= 360.
            }
            Self::Pipe { radius, .. } => positive(*radius, tol),
            Self::Bevel { size, .. } => positive(*size, tol),
            Self::Thickness { distance, .. } => positive(*distance, tol),
            Self::Point { point } => vector(point),
            Self::Circle { center, radius } | Self::Sphere { center, radius } => {
                vector(center) && positive(*radius, tol)
            }
            Self::Cylinder {
                center,
                radius,
                height,
            }
            | Self::Cone {
                center,
                radius,
                height,
            } => vector(center) && positive(*radius, tol) && positive(*height, tol),
            Self::Box { origin, size } => vector(origin) && size.iter().all(|v| positive(*v, tol)),
            Self::Nurbs {
                degree,
                points,
                knots,
                ..
            } => {
                (2..=7).contains(degree)
                    && points.len() > *degree
                    && points.len() <= 64
                    && knots.len() == points.len() + degree - 1
                    && knots.iter().all(|k| bounded(*k))
                    && knots.windows(2).all(|k| k[0] <= k[1])
                    && knots[*degree - 1] < knots[points.len() - 1]
                    && points.iter().all(|p| {
                        p.iter().all(|x| x.is_finite() && x.abs() <= 1e9)
                            && p[3] >= 1e-9
                            && p[3] <= 1e9
                            && p[..3].iter().all(|x| bounded(x / p[3]))
                    })
            }
            Self::Curve { points, closed } => {
                (4..=256).contains(&points.len())
                    && points.iter().all(vector)
                    && points.windows(2).all(|p| p[0] != p[1])
                    && (!closed || points.first() != points.last())
            }
            Self::Transform {
                translation,
                scale,
                center,
                rotation_degrees,
                axis,
                ..
            } => {
                vector(translation)
                    && vector(center)
                    && scale.iter().all(|v| positive(*v, 0.))
                    && bounded(*rotation_degrees)
                    && direction(axis)
            }
            Self::Extrude {
                distance,
                direction: v,
                ..
            } => positive(distance.abs(), tol) && direction(v),
            Self::Boolean { left, right, .. } => left != right,
            Self::Loft { sections } => (2..=16).contains(&sections.len()),
        }
    }
}
impl Batch {
    pub fn validate(&self, tolerance: f64) -> Result<(), &'static str> {
        if self.label.trim().is_empty()
            || self.label.len() > 128
            || self.label.chars().any(char::is_control)
            || self.steps.is_empty()
            || self.steps.len() > MAX_STEPS
            || serde_json::to_vec(self).map_err(|_| "invalid_batch")?.len() > MAX_BATCH_BYTES
        {
            return Err("batch_budget");
        }
        if self
            .steps
            .iter()
            .any(|s| matches!(s.action, Action::Undo { .. }))
            && (self.steps.len() != 1 || !self.steps[0].publish || self.steps[0].replace.is_some())
        {
            return Err("undo_must_be_separate_batch");
        }
        let mut ids = HashSet::new();
        let mut replaced = HashSet::new();
        for step in &self.steps {
            if step.id.is_empty()
                || step.id.len() > 48
                || !step
                    .id
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
                || step.name.trim().is_empty()
                || step.name.len() > 96
                || step.name.chars().any(char::is_control)
                || !step.action.validate(tolerance)
            {
                return Err("invalid_modeling_step");
            }
            if let Action::Attributes { source, .. } = &step.action {
                if !step.publish || !catalog::uuid(source) || step.replace.as_ref() != Some(source)
                {
                    return Err("attributes_require_existing_replacement");
                }
            }
            for source in step.action.references() {
                if !ids.contains(source) && !catalog::uuid(source) {
                    return Err("unresolved_modeling_reference");
                }
            }
            if let Some(target) = &step.replace {
                if !step.publish || !catalog::uuid(target) || !replaced.insert(target) {
                    return Err("invalid_replacement");
                }
            }
            if !ids.insert(step.id.as_str()) {
                return Err("duplicate_modeling_step");
            }
        }
        if !self.steps.iter().any(|s| s.publish) {
            return Err("no_published_results");
        }
        Ok(())
    }
    pub fn check_output(&self, output: &Value, tolerance: f64) -> Result<(), &'static str> {
        if self.steps.len() == 1 && matches!(self.steps[0].action, Action::Undo { .. }) {
            return if output["applied"] == true
                && output["undo_performed"] == true
                && output["objects"] == json!([])
            {
                Ok(())
            } else {
                Err("modeling_receipt_invalid")
            };
        }
        let objects = output["objects"]
            .as_array()
            .ok_or("modeling_receipt_invalid")?;
        let published: Vec<_> = self.steps.iter().filter(|s| s.publish).collect();
        if objects.len() != published.len() || output["applied"] != true {
            return Err("modeling_receipt_invalid");
        }
        let mut ids = HashSet::new();
        for (fact, step) in objects.iter().zip(published) {
            let id = fact["object_id"]
                .as_str()
                .ok_or("modeling_receipt_invalid")?;
            let bounds: [[f64; 3]; 2] = serde_json::from_value(fact["bounds"].clone())
                .map_err(|_| "modeling_receipt_invalid")?;
            if !catalog::uuid(id)
                || !ids.insert(id)
                || fact["valid"] != true
                || fact["step"] != step.id
                || !bounds.iter().all(vector)
                || (0..3).any(|i| bounds[0][i] > bounds[1][i])
            {
                return Err("modeling_receipt_invalid");
            }
            let near = |a: f64, b: f64| (a - b).abs() <= tolerance.max(1e-7).max(b.abs() * 1e-7);
            let expected = match &step.action {
                Action::Box { origin, size } => Some((
                    *origin,
                    [
                        origin[0] + size[0],
                        origin[1] + size[1],
                        origin[2] + size[2],
                    ],
                    Some(size.iter().product::<f64>()),
                )),
                Action::Sphere { center, radius } => Some((
                    [center[0] - radius, center[1] - radius, center[2] - radius],
                    [center[0] + radius, center[1] + radius, center[2] + radius],
                    Some(4. / 3. * std::f64::consts::PI * radius.powi(3)),
                )),
                Action::Circle { center, radius } => Some((
                    [center[0] - radius, center[1] - radius, center[2]],
                    [center[0] + radius, center[1] + radius, center[2]],
                    None,
                )),
                Action::Cylinder {
                    center,
                    radius,
                    height,
                }
                | Action::Cone {
                    center,
                    radius,
                    height,
                } => Some((
                    [center[0] - radius, center[1] - radius, center[2]],
                    [center[0] + radius, center[1] + radius, center[2] + height],
                    Some(
                        std::f64::consts::PI * radius * radius * height
                            / if matches!(step.action, Action::Cone { .. }) {
                                3.
                            } else {
                                1.
                            },
                    ),
                )),
                _ => None,
            };
            if let Some((min, max, volume)) = expected {
                if (0..3).any(|i| !near(bounds[0][i], min[i]) || !near(bounds[1][i], max[i]))
                    || volume.is_some_and(|v| fact["volume"].as_f64().is_none_or(|n| !near(n, v)))
                {
                    return Err("modeling_dimensions_unverified");
                }
            }
            if let Action::Nurbs { degree, closed, .. } = &step.action {
                if fact["degree"] != *degree || fact["closed"] != *closed {
                    return Err("modeling_curve_unverified");
                }
            }
            if matches!(step.action, Action::Curve { .. })
                && (fact["degree"].as_u64().is_none_or(|d| d < 3))
            {
                return Err("modeling_curve_unverified");
            }
        }
        Ok(())
    }
    pub fn targets(&self) -> Vec<String> {
        let mut targets = std::collections::BTreeSet::new();
        for s in &self.steps {
            for r in s.action.references() {
                if catalog::uuid(r) {
                    targets.insert(r.to_string());
                }
            }
            if let Some(r) = &s.replace {
                targets.insert(r.clone());
            }
        }
        targets.into_iter().collect()
    }
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Context {
    pub document_session: String,
    pub fingerprint: String,
    pub units: String,
    pub tolerance: f64,
    pub command_idle: bool,
    pub undo_ready: bool,
    pub layer_available: bool,
    pub undo_top_serial: u64,
}
impl Context {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        if self.document_session.is_empty()
            || self.document_session.len() > 256
            || self.fingerprint.len() != 64
            || !self.fingerprint.bytes().all(|c| c.is_ascii_hexdigit())
            || self.units.is_empty()
            || self.units.len() > 64
            || !self.tolerance.is_finite()
            || self.tolerance <= 0.
            || !self.command_idle
            || !self.undo_ready
            || !self.layer_available
        {
            return Err("modeling_context_unavailable");
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Ticket {
    pub owner: Token,
    pub call_id: String,
    pub serial: u64,
}
#[derive(Clone, Copy, PartialEq)]
enum State {
    Awaiting,
    Approved,
    Consumed,
    Finished,
}
struct Record {
    ticket: Ticket,
    batch: Batch,
    context: Context,
    deadline: Instant,
    state: State,
    result: Option<ToolResult>,
    require_planar: bool,
}
#[derive(Default)]
pub struct Gate {
    mode: Mode,
    require_planar: bool,
    undo_requested: bool,
    last_undo_serial: Option<u64>,
    serial: u64,
    records: BTreeMap<String, Record>,
}
impl Gate {
    pub fn request_intent(&mut self, text: &str) {
        let lower = text.to_ascii_lowercase();
        self.undo_requested = lower.trim() == "undo" || lower.trim_start().starts_with("undo ");
        let words: Vec<_> = lower.split(|c: char| !c.is_ascii_alphanumeric()).collect();
        self.require_planar = words.contains(&"2d")
            && !words.contains(&"3d")
            && !lower.contains("thick")
            && !lower.contains("extrud");
    }
    pub fn mode(&self) -> Mode {
        self.mode
    }
    pub fn evidence(&self) -> Value {
        let finished: Vec<_> = self
            .records
            .values()
            .filter(|r| r.result.is_some())
            .collect();
        let applied: Vec<_> = finished
            .iter()
            .filter(|r| {
                r.result
                    .as_ref()
                    .is_some_and(|v| !v.is_error && v.output["applied"] == true)
            })
            .collect();
        let last = finished.iter().max_by_key(|r| r.ticket.serial);
        let status=last.map(|r| {
            let result=r.result.as_ref().unwrap();
            if result.is_error {
                "CAD evidence: latest modeling batch did not produce a confirmed success. Inspect its result before retrying.".to_string()
            } else if result.output["undo_performed"]==true {
                "CAD evidence: latest approved AI change was undone.".to_string()
            } else {
                format!("CAD evidence: approved modeling batch applied; {} object result(s) measured at publication. Appearance was not verified.",result.output["objects"].as_array().map_or(0,Vec::len))
            }
        });
        json!({"scope":"publication_receipts","status_text":status,"known_batches":finished.len(),"applied_batches":applied.len(),
            "object_results":applied.iter().map(|r|r.result.as_ref().unwrap().output["objects"].as_array().map_or(0,Vec::len)).sum::<usize>(),
            "last_applied":last.is_some_and(|r|!r.result.as_ref().unwrap().is_error),
            "last_undo":last.is_some_and(|r|r.result.as_ref().unwrap().output["undo_performed"]==true),
            "last_objects":last.and_then(|r|r.result.as_ref().unwrap().output["objects"].as_array()).map_or(0,Vec::len),
            "current_geometry_verified":false,"visual_quality_evaluated":false})
    }
    pub fn lookup(&mut self, call_id: &str) -> Result<Value, &'static str> {
        let r = self
            .records
            .get_mut(call_id)
            .ok_or("unknown_modeling_ticket")?;
        if r.state != State::Finished && Instant::now() > r.deadline {
            let consumed = r.state == State::Consumed;
            r.state = State::Finished;
            r.result = Some(ToolResult {
                call_id: call_id.into(),
                is_error: true,
                output: json!({"code":"modeling_expired","applied":if consumed {Value::Null}else{json!(false)},"retry_safe":false}),
            });
        }
        Ok(if let Some(result) = &r.result {
            json!({"status":"finished","result":result})
        } else {
            json!({"status":"pending","ticket":r.ticket,"expires_in_seconds":r.deadline.saturating_duration_since(Instant::now()).as_secs()})
        })
    }
    pub fn clear(&mut self) {
        self.records.clear();
        self.require_planar = false;
        self.undo_requested = false;
        self.last_undo_serial = None;
        self.mode = Mode::Review;
    }
    pub fn set_mode(&mut self, mode: Mode) -> Result<Value, &'static str> {
        if self.records.values().any(|r| r.state != State::Finished) {
            return Err("modeling_pending");
        }
        self.mode = mode;
        Ok(json!({"status":"modeling_mode","mode":mode}))
    }
    pub fn stage(
        &mut self,
        owner: Token,
        call_id: String,
        batch: Batch,
        context: Context,
    ) -> Result<Value, &'static str> {
        context.validate()?;
        batch.validate(context.tolerance)?;
        if matches!(batch.steps[0].action, Action::Undo { .. })
            && (!self.undo_requested
                || self.last_undo_serial != Some(context.undo_top_serial)
                || context.undo_top_serial == 0)
        {
            return Err("undo_requires_explicit_request_and_latest_ai_record");
        }
        if self.records.len() >= 128 {
            return Err("modeling_history_budget");
        }
        if self.records.contains_key(&call_id) {
            return Err("modeling_call_replayed");
        }
        self.serial += 1;
        let ticket = Ticket {
            owner,
            call_id: call_id.clone(),
            serial: self.serial,
        };
        let state = if self.mode == Mode::Build {
            State::Approved
        } else {
            State::Awaiting
        };
        let result = json!({"status":if state==State::Approved {"modeling_approved"}else{"modeling_review_required"},
            "ticket":ticket,"batch":batch,"context":context,"mode":self.mode,"require_planar":self.require_planar});
        self.records.insert(
            call_id,
            Record {
                ticket,
                batch,
                context,
                deadline: Instant::now() + Duration::from_secs(300),
                state,
                result: None,
                require_planar: self.require_planar,
            },
        );
        Ok(result)
    }
    fn checked(&mut self, ticket: &Ticket, current: &Context) -> Result<&mut Record, &'static str> {
        let record = self
            .records
            .get_mut(&ticket.call_id)
            .ok_or("unknown_modeling_ticket")?;
        if &record.ticket != ticket {
            return Err("stale_modeling_ticket");
        }
        if record.state == State::Finished {
            return Err("modeling_ticket_spent");
        }
        if Instant::now() > record.deadline
            || &record.context != current
            || current.validate().is_err()
        {
            record.state = State::Finished;
            record.result = Some(ToolResult {
                call_id: ticket.call_id.clone(),
                is_error: true,
                output: json!({"code":"modeling_context_stale","applied":false}),
            });
            return Err("modeling_context_stale");
        }
        Ok(record)
    }
    pub fn decide(
        &mut self,
        ticket: &Ticket,
        current: &Context,
        allow: bool,
    ) -> Result<Value, &'static str> {
        let record = self.checked(ticket, current)?;
        if record.state != State::Awaiting {
            return Err("modeling_decision_spent");
        }
        if allow {
            record.state = State::Approved;
        } else {
            record.state = State::Finished;
            record.result = Some(ToolResult {
                call_id: ticket.call_id.clone(),
                is_error: true,
                output: json!({"code":"modeling_rejected","applied":false}),
            });
        }
        Ok(json!({"status":if allow{"modeling_approved"}else{"modeling_rejected"}}))
    }
    pub fn consume(&mut self, ticket: &Ticket, current: &Context) -> Result<Value, &'static str> {
        let record = self.checked(ticket, current)?;
        if record.state != State::Approved {
            return Err("modeling_approval_required");
        }
        record.state = State::Consumed;
        Ok(
            json!({"status":"modeling_consumed","batch":record.batch,"context":record.context,"ticket":record.ticket,"require_planar":record.require_planar}),
        )
    }
    pub fn publication(
        &mut self,
        ticket: &Ticket,
        output: Value,
        is_error: bool,
    ) -> Result<Value, &'static str> {
        let r = self
            .records
            .get_mut(&ticket.call_id)
            .ok_or("unknown_modeling_ticket")?;
        if &r.ticket != ticket || r.state != State::Consumed {
            return Err("modeling_publication_spent");
        }
        if serde_json::to_vec(&output)
            .map_err(|_| "modeling_receipt_invalid")?
            .len()
            > 48_000
        {
            return Err("modeling_receipt_budget");
        }
        if !is_error {
            r.batch.check_output(&output, r.context.tolerance)?;
            if r.require_planar {
                check_planar_output(&output)?;
            }
            let objects = output["objects"]
                .as_array()
                .ok_or("modeling_receipt_invalid")?;
            let published: Vec<_> = r
                .batch
                .steps
                .iter()
                .filter(|s| s.publish && !matches!(s.action, Action::Undo { .. }))
                .collect();
            if objects.len() != published.len() || output["applied"] != true {
                return Err("modeling_receipt_invalid");
            }
            for (fact, step) in objects.iter().zip(published) {
                if fact["step"] != step.id
                    || !fact["object_id"].as_str().is_some_and(catalog::uuid)
                    || fact["valid"] != true
                    || fact["bounds"].as_array().is_none_or(|a| a.len() != 2)
                {
                    return Err("modeling_receipt_invalid");
                }
                if !step.action.attributes_verified(fact) {
                    return Err("modeling_attributes_unverified");
                }
                if let Some(target) = &step.replace {
                    if fact["object_id"] != *target {
                        return Err("modeling_receipt_invalid");
                    }
                }
                if matches!(
                    step.action,
                    Action::Box { .. }
                        | Action::Sphere { .. }
                        | Action::Cylinder { .. }
                        | Action::Cone { .. }
                        | Action::Boolean { .. }
                        | Action::Bevel { .. }
                        | Action::Thickness { .. }
                        | Action::Extrude { solid: true, .. }
                        | Action::Pipe { cap: true, .. }
                        | Action::Revolve { solid: true, .. }
                ) && (fact["solid"] != true
                    || fact["airtight"] != true
                    || fact["volume"]
                        .as_f64()
                        .is_none_or(|v| !v.is_finite() || v <= 0.))
                {
                    return Err("modeling_solid_unverified");
                }
                if matches!(
                    step.action,
                    Action::Curve { closed: true, .. }
                        | Action::Nurbs { closed: true, .. }
                        | Action::Circle { .. }
                ) && fact["closed"] != true
                {
                    return Err("modeling_curve_unverified");
                }
            }
        }
        if !is_error {
            self.last_undo_serial = if output["undo_performed"] == true {
                None
            } else {
                output["undo_serial"].as_u64()
            };
        }
        r.state = State::Finished;
        r.result = Some(ToolResult {
            call_id: ticket.call_id.clone(),
            output,
            is_error,
        });
        Ok(json!({"status":"modeling_recorded"}))
    }
    pub fn result(&self, ticket: &Ticket) -> Result<ToolResult, &'static str> {
        let r = self
            .records
            .get(&ticket.call_id)
            .ok_or("unknown_modeling_ticket")?;
        if &r.ticket != ticket {
            return Err("stale_modeling_ticket");
        }
        r.result.clone().ok_or("modeling_result_pending")
    }
    pub fn check_result(&self, owner: &Token, result: &ToolResult) -> Result<(), &'static str> {
        let r = self
            .records
            .get(&result.call_id)
            .ok_or("modeling_result_missing")?;
        if &r.ticket.owner != owner {
            return Err("stale_modeling_ticket");
        }
        let canonical = r.result.as_ref().ok_or("modeling_result_pending")?;
        if canonical.output != result.output || canonical.is_error != result.is_error {
            return Err("modeling_result_mismatch");
        }
        Ok(())
    }
}
pub fn check_planar_output(output: &Value) -> Result<(), &'static str> {
    let objects = output["objects"]
        .as_array()
        .ok_or("modeling_receipt_invalid")?;
    if objects
        .iter()
        .any(|o| o["solid"] == true || (o["planar"] != true && o["type"] != "Point"))
    {
        return Err("request_requires_planar_2d_geometry_not_relief_solids");
    }
    if objects
        .iter()
        .any(|o| o["closed"] == true && o["profile_usable_for_solid"] != true)
    {
        return Err("modeling_planar_profile_unverified");
    }
    Ok(())
}
pub fn mcp_schema(name: &str) -> Value {
    match name {
        "narwhal.export" => crate::export_action::mcp_schema(false),
        "narwhal.export_result" => crate::export_action::mcp_schema(true),
        "narwhal.curve_samples" => crate::curve_inspection::samples_schema(),
        "narwhal.compare_curves" => crate::curve_inspection::compare_schema(),
        "narwhal.frame" => crate::catalog::frame_schema(),
        "narwhal.measure" => crate::catalog::measurement_schema(),
        "narwhal.model" => obj(
            json!({"request_id":{"type":"string","maxLength":96},"batch":schema()}),
            vec!["request_id", "batch"],
        ),
        "narwhal.model_result" => obj(
            json!({"request_id":{"type":"string","maxLength":96}}),
            vec!["request_id"],
        ),
        _ => crate::catalog::schema("narwhal.object_summary"),
    }
}
pub fn validate_mcp(name: &str, args: &serde_json::Map<String, Value>) -> Result<(), &'static str> {
    if matches!(name, "narwhal.export" | "narwhal.export_result") {
        return crate::export_action::validate_mcp(args, name == "narwhal.export_result");
    }
    if name == "narwhal.curve_samples" {
        return crate::curve_inspection::validate_samples(args);
    }
    if name == "narwhal.compare_curves" {
        return crate::curve_inspection::validate_compare(args);
    }
    if name == "narwhal.frame" {
        return crate::catalog::validate_frame(args);
    }
    if name == "narwhal.measure" {
        return crate::catalog::validate_measurement(args);
    }
    if name == "narwhal.inspect" {
        return crate::catalog::validate_read("narwhal.object_summary", args);
    }
    if !matches!(name, "narwhal.model" | "narwhal.model_result") {
        return Err("tool_not_allowed");
    }
    let id = args
        .get("request_id")
        .and_then(Value::as_str)
        .ok_or("invalid_arguments")?;
    if id.is_empty()
        || id.len() > 96
        || !id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
    {
        return Err("invalid_arguments");
    }
    if name == "narwhal.model_result" {
        return if args.len() == 1 {
            Ok(())
        } else {
            Err("invalid_arguments")
        };
    }
    if args.len() != 2 {
        return Err("invalid_arguments");
    }
    let batch: Batch =
        serde_json::from_value(args["batch"].clone()).map_err(|_| "invalid_modeling_batch")?;
    batch.validate(0.)
}
fn obj(properties: Value, required: Vec<&str>) -> Value {
    json!({"type":"object","properties":properties,"required":required,"additionalProperties":false})
}
pub fn schema() -> Value {
    let point = json!({"type":"array","items":{"type":"number","minimum":-1e6,"maximum":1e6},"minItems":3,"maxItems":3});
    let length = json!({"type":"number","exclusiveMinimum":0,"maximum":1e6});
    let reference = json!({"type":"string","maxLength":48,"description":"Earlier step ID in this batch or an existing object UUID from tool results."});
    let mut variants = vec![];
    for (op, fields) in [
        ("undo", json!({})),
        ("point", json!({"point":point})),
        ("circle", json!({"center":point,"radius":length})),
        (
            "curve",
            json!({"points":{"type":"array","items":point,"minItems":4,"maxItems":256},"closed":{"type":"boolean"}}),
        ),
        ("box", json!({"origin":point,"size":point})),
        ("sphere", json!({"center":point,"radius":length})),
        (
            "cylinder",
            json!({"center":point,"radius":length,"height":length}),
        ),
        (
            "cone",
            json!({"center":point,"radius":length,"height":length}),
        ),
        (
            "transform",
            json!({"source":reference,"translation":point,"scale":point,"center":point,"rotation_degrees":{"type":"number"},"axis":point}),
        ),
        (
            "extrude",
            json!({"source":reference,"distance":{"type":"number"},"direction":point,"solid":{"type":"boolean"}}),
        ),
        (
            "pipe",
            json!({"source":reference,"radius":length,"cap":{"type":"boolean"}}),
        ),
        (
            "revolve",
            json!({"source":reference,"axis_start":point,"axis_end":point,
                "start_degrees":{"type":"number","minimum":-360,"maximum":360},
                "end_degrees":{"type":"number","minimum":-360,"maximum":720},
                "solid":{"type":"boolean"}}),
        ),
        (
            "boolean",
            json!({"left":reference,"right":reference,"kind":{"enum":["union","difference","intersection"]}}),
        ),
        (
            "loft",
            json!({"sections":{"type":"array","items":reference,"minItems":2,"maxItems":16}}),
        ),
        (
            "bevel",
            json!({"source":reference,"size":length,"chamfer":{"type":"boolean"}}),
        ),
        ("thickness", json!({"source":reference,"distance":length})),
        ("outline", json!({"source":reference})),
        (
            "nurbs",
            json!({"degree":{"type":"integer","minimum":2,"maximum":7},
            "points":{"type":"array","minItems":3,"maxItems":64,"description":"Homogeneous weighted control points [x*w,y*w,z*w,w], not interpolation points. Positive weights; degree+1 or more points.","items":{"type":"array","minItems":4,"maxItems":4,"items":{"type":"number"}}},
            "knots":{"type":"array","maxItems":70,"description":"Nondecreasing openNURBS knot vector, length point_count+degree-1 (omit the two superfluous end knots).","items":{"type":"number"}},
            "closed":{"type":"boolean","description":"Expected closure; the host measures and refuses a mismatch."}}),
        ),
        (
            "attributes",
            json!({"source":reference,
            "name":{"type":["string","null"],"minLength":1,"maxLength":96},
            "layer_id":{"type":["string","null"],"format":"uuid"},
            "color":{"type":["array","null"],"items":{"type":"integer","minimum":0,"maximum":255},"minItems":3,"maxItems":3},
            "visible":{"type":["boolean","null"]}}),
        ),
    ] {
        let mut fields = fields.as_object().unwrap().clone();
        fields.insert("op".into(), json!({"const":op}));
        let required: Vec<_> = fields.keys().map(String::as_str).collect();
        let mut variant = obj(json!(fields), required);
        if op == "loft" {
            variant["description"] = json!(
                "Constructs an exact smooth surface through section curves. Does NOT cap a closed solid; inspect airtightness before any solid claim."
            );
        }
        if op == "revolve" {
            variant["description"] = json!(
                "Exact revolution of a curve around the line from axis_start to axis_end. Positive sweep end_degrees-start_degrees must be <=360 degrees. Closed profiles can yield solids; open profiles or partial turns can yield surfaces. solid=true REQUIRES actual airtight-solid validation; it does not automatically cap surfaces. No faceting or guessed closure."
            );
        }
        if op == "pipe" {
            variant["description"] = json!(
                "Exact constant-radius circular sweep along an earlier curve or inspected curve UUID, preserving its analytic/NURBS rail. cap=true requests a verified airtight solid; cap=false permits an open surface. Closed rails may yield closed shells rather than classified solids; cap=true is refused unless actual airtight-solid validation succeeds. Tight/self-intersecting rails may be refused; no mesh fallback."
            );
        }
        if op == "boolean" {
            variant["description"] = json!(
                "Both operands must already be exact airtight solids. Open loft surfaces/skins and curves are not solid boolean operands."
            );
        }
        variants.push(variant);
    }
    obj(
        json!({"label":{"type":"string","maxLength":128},"steps":{"type":"array","minItems":1,"maxItems":MAX_STEPS,"items":obj(json!({
        "id":{"type":"string","minLength":1,"maxLength":48,"pattern":"^[A-Za-z0-9_-]+$"},"name":{"type":"string","maxLength":96},"publish":{"type":"boolean"},
        "replace":{"type":["string","null"],"description":"Existing UUID to replace without changing identity, or null to append."},
        "action":{"oneOf":variants}}),vec!["id","name","publish","replace","action"])}}),
        vec!["label", "steps"],
    )
}
pub fn definition() -> crate::provider::ToolDefinition {
    crate::provider::ToolDefinition{name:"narwhal_model".into(),parameters:schema(),description:"Construct or edit an exact-CAD design in one bounded transaction. Use smooth cubic interpolated curves, explicit rational NURBS (degree2-7, up to64 homogeneous control points and exact knot vector), exact primitives, transforms, extrusion, constant-radius pipe along a curve (cap=true requires verified solidity), exact profile revolve about a specified axis (solid=true requires verified solidity), smooth loft, analytic booleans and all-edge fillet/chamfer (bevel). Attributes edits rename, RGB object color, visibility or placement on an existing unlocked layer UUID; source and replace must be the same existing UUID and publish=true. Null attribute fields preserve their values. Thickness edits untransformed extrusion recipes only. Outline recovers/projects a curve or an extrusion's exact profile to World XY; it is not a general Brep silhouette. Earlier step IDs are dependencies; UUIDs refer to existing inspected objects. Set publish=false for construction intermediates; replace=null appends, replace=UUID edits that identity. Review mode requires approval of the batch; Build mode is separately user-authorized. No file or shell actions. Choose coherent defaults in document units for unspecified cosmetic dimensions and state them. Tool receipts, not prose, establish what was built; inspect results and revise across rounds. Assemblies are not unified solids.".into()}
}

#[cfg(test)]
mod tests {
    use super::*;
    fn context() -> Context {
        Context {
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
    fn batch() -> Batch {
        serde_json::from_value(json!({"label":"part","steps":[{"id":"outline","name":"Outline","publish":false,"replace":null,"action":{"op":"circle","center":[0,0,0],"radius":20}},{"id":"part","name":"Part","publish":true,"replace":null,"action":{"op":"extrude","source":"outline","distance":12,"direction":[0,0,1],"solid":true}}]})).unwrap()
    }
    fn owner() -> Token {
        Token {
            conversation: 1,
            generation: 1,
            request: 1,
        }
    }
    #[test]
    fn explicit_nurbs_requires_smooth_degree_positive_weights_and_valid_knot_domain() {
        let value = json!({"op":"nurbs","degree":3,"closed":false,"points":[[0,0,0,1],[1,2,0,1],[3,2,0,1],[4,0,0,1]],"knots":[0,0,0,1,1,1]});
        let action: Action = serde_json::from_value(value.clone()).unwrap();
        assert!(action.validate(0.01));
        for (path, bad) in [
            ("/degree", json!(1)),
            ("/degree", json!(8)),
            ("/points/1/3", json!(0)),
            ("/points/1/3", json!(-1)),
            ("/knots/3", json!(-1)),
            ("/knots", json!([0, 0, 0, 0, 0, 0])),
            ("/knots", json!([0, 0, 1, 1])),
        ] {
            let mut v = value.clone();
            *v.pointer_mut(path).unwrap() = bad;
            let action: Action = serde_json::from_value(v).unwrap();
            assert!(!action.validate(0.01), "{path}");
        }
    }

    #[test]
    fn attribute_edits_are_bounded_existing_uuid_replacements_only() {
        let id = "00000000-0000-0000-0000-000000000001";
        let value = json!({"label":"Style","steps":[{"id":"style","name":"Style","publish":true,"replace":id,"action":{"op":"attributes","source":id,"name":"Whale eye","color":[30,40,60],"visible":false,"layer_id":null}}]});
        let mut b: Batch = serde_json::from_value(value.clone()).unwrap();
        assert_eq!(b.validate(0.01), Ok(()));
        let receipt = json!({"name":"Whale eye","attributes":{"color":[30,40,60,255],"color_source":"ObjectColorSource.ColorFromObject","visible":false}});
        assert!(b.steps[0].action.attributes_verified(&receipt));
        for (path, value) in [
            ("/name", json!("Unchanged")),
            ("/attributes/color", json!([0, 0, 0, 255])),
            (
                "/attributes/color_source",
                json!("ObjectColorSource.ColorFromLayer"),
            ),
            ("/attributes/visible", json!(true)),
        ] {
            let mut bad = receipt.clone();
            *bad.pointer_mut(path).unwrap() = value;
            assert!(!b.steps[0].action.attributes_verified(&bad));
        }
        b.steps[0].replace = None;
        assert_eq!(
            b.validate(0.01),
            Err("attributes_require_existing_replacement")
        );
        b.steps[0].replace = Some(id.into());
        b.steps[0].publish = false;
        assert!(b.validate(0.01).is_err());
        for bad in [
            json!({"op":"attributes","source":id,"name":"\n"}),
            json!({"op":"attributes","source":id,"layer_id":"Default"}),
            json!({"op":"attributes","source":id}),
        ] {
            let mut v = value.clone();
            v["steps"][0]["action"] = bad;
            assert!(
                serde_json::from_value::<Batch>(v)
                    .unwrap()
                    .validate(0.01)
                    .is_err()
            );
        }
        let mut v = value;
        v["steps"][0]["action"]["color"] = json!([256, 0, 0]);
        assert!(serde_json::from_value::<Batch>(v).is_err());
    }

    #[test]
    fn revolve_contract_rejects_bad_axis_sweep_and_unresolved_profiles() {
        let mut value = json!({"label":"revolution","steps":[
            {"id":"profile","name":"Profile","publish":false,"replace":null,
             "action":{"op":"circle","center":[20,0,0],"radius":3}},
            {"id":"turn","name":"Turn","publish":true,"replace":null,
             "action":{"op":"revolve","source":"profile","axis_start":[0,0,0],"axis_end":[0,0,1],
                       "start_degrees":0,"end_degrees":360,"solid":true}}]});
        let valid: Batch = serde_json::from_value(value.clone()).unwrap();
        assert!(valid.validate(0.001).is_ok());
        assert_eq!(valid.steps[1].action.references(), vec!["profile"]);
        for (key, bad) in [
            ("axis_end", json!([0, 0, 0])),
            ("axis_start", json!([1e9, 0, 0])),
            ("start_degrees", json!(360)),
            ("end_degrees", json!(361)),
            ("end_degrees", json!(-90)),
        ] {
            let mut bad_value = value.clone();
            bad_value["steps"][1]["action"][key] = bad;
            let batch: Batch = serde_json::from_value(bad_value).unwrap();
            assert_eq!(batch.validate(0.001), Err("invalid_modeling_step"));
        }
        value["steps"][1]["action"]["source"] = json!("missing");
        let batch: Batch = serde_json::from_value(value).unwrap();
        assert_eq!(batch.validate(0.001), Err("unresolved_modeling_reference"));
    }
    #[test]
    fn pipe_schema_dependencies_and_radius_are_validated_without_geometry_execution() {
        let mut value = json!({"label":"sweep","steps":[
            {"id":"rail","name":"Rail","publish":false,"replace":null,
             "action":{"op":"circle","center":[0,0,0],"radius":20}},
            {"id":"pipe","name":"Pipe","publish":true,"replace":null,
             "action":{"op":"pipe","source":"rail","radius":3,"cap":true}}]});
        let valid: Batch = serde_json::from_value(value.clone()).unwrap();
        assert!(valid.validate(0.001).is_ok());
        assert_eq!(valid.steps[1].action.references(), vec!["rail"]);
        assert!(schema().to_string().contains("\"pipe\""));
        for radius in [-1., 0., 0.0001, 1e20] {
            value["steps"][1]["action"]["radius"] = json!(radius);
            let invalid: Batch = serde_json::from_value(value.clone()).unwrap();
            assert_eq!(invalid.validate(0.001), Err("invalid_modeling_step"));
        }
        value["steps"][1]["action"]["radius"] = json!(3);
        value["steps"][1]["action"]["source"] = json!("missing");
        let invalid: Batch = serde_json::from_value(value).unwrap();
        assert_eq!(
            invalid.validate(0.001),
            Err("unresolved_modeling_reference")
        );
    }
    #[test]
    fn inspection_directions_produce_orthonormal_right_handed_camera_bases() {
        for direction in [
            [0., 0., 1.],
            [0., 0., -1.],
            [1., -1., 1.],
            [0., 1., 0.],
            [1e6, 0., 0.],
        ] {
            let result = view_basis(direction).unwrap();
            let rows: [[f64; 3]; 3] =
                serde_json::from_value(result["rotation_rows"].clone()).unwrap();
            for a in 0..3 {
                for b in 0..3 {
                    let dot = (0..3).map(|r| rows[r][a] * rows[r][b]).sum::<f64>();
                    assert!((dot - if a == b { 1. } else { 0. }).abs() < 1e-12);
                }
            }
            let norm = direction.iter().map(|v| v * v).sum::<f64>().sqrt();
            for i in 0..3 {
                assert!((rows[i][2] - direction[i] / norm).abs() < 1e-12);
            }
            let determinant = rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
                - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
                + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0]);
            assert!((determinant - 1.).abs() < 1e-12);
            let args = serde_json::from_value(json!({"object_ids":["00000000-0000-0000-0000-000000000001"],"view_direction":direction})).unwrap();
            assert!(crate::catalog::validate_frame(&args).is_ok());
        }
        for direction in [
            [0.; 3],
            [f64::NAN, 0., 1.],
            [f64::INFINITY, 1., 0.],
            [1e7, 0., 0.],
        ] {
            assert!(view_basis(direction).is_err());
        }
        for direction in [
            json!(null),
            json!([0, 0, 0]),
            json!([1, 2]),
            json!(["1", 2, 3]),
            json!([1e7, 0, 0]),
        ] {
            let args = serde_json::from_value(json!({"object_ids":["00000000-0000-0000-0000-000000000001"],"view_direction":direction})).unwrap();
            assert!(crate::catalog::validate_frame(&args).is_err());
        }
    }
    #[test]
    fn perspective_fit_contains_bounding_sphere_and_rejects_bad_input() {
        let fitted = framing(vec![[[19.0, -6.0, 0.0], [31.0, 6.0, 12.0]]], [1.4, 2.0]).unwrap();
        assert_eq!(fitted["center"], json!([25.0, 0.0, 6.0]));
        let distance = fitted["distance"].as_f64().unwrap();
        let radius = 108.0_f64.sqrt();
        assert!(distance > radius * 5.0_f64.sqrt());
        assert!(framing(vec![], [1.0, 1.0]).is_err());
        assert!(framing(vec![[[1.0; 3], [0.0; 3]]], [1.0, 1.0]).is_err());
        assert!(framing(vec![[[0.0; 3], [1.0; 3]]], [f64::NAN, 1.0]).is_err());
        assert!(framing(vec![[[0.0; 3], [1.0; 3]]], [0.0, 1.0]).is_err());
    }

    #[test]
    fn hyphenated_step_ids_match_schema_and_resolve_dependencies() {
        let mut b = batch();
        b.steps[0].id = "body-outline".into();
        if let Action::Extrude { source, .. } = &mut b.steps[1].action {
            *source = "body-outline".into();
        }
        assert_eq!(b.validate(0.01), Ok(()));
        assert_eq!(
            schema()["properties"]["steps"]["items"]["properties"]["id"]["pattern"],
            "^[A-Za-z0-9_-]+$"
        );
        for bad in ["body/outline", "body outline", "", "body\noutline"] {
            b.steps[0].id = bad.into();
            assert_eq!(b.validate(0.01), Err("invalid_modeling_step"));
        }
    }

    #[test]
    fn dependencies_and_strict_arguments() {
        let b = batch();
        assert!(b.validate(0.001).is_ok());
        let mut bad = b.clone();
        bad.steps.swap(0, 1);
        assert_eq!(bad.validate(0.001), Err("unresolved_modeling_reference"));
        let mut bad = b.clone();
        bad.steps[1].action = Action::Sphere {
            center: [0.; 3],
            radius: -1.,
        };
        assert!(bad.validate(0.001).is_err());
        let mut raw = serde_json::to_value(b).unwrap();
        raw["steps"][0]["action"]["code"] = json!("eval");
        assert!(serde_json::from_value::<Batch>(raw).is_err());
    }
    #[test]
    fn approval_consumption_replay_staleness_and_build() {
        let mut gate = Gate::default();
        assert_eq!(gate.mode(), Mode::Review);
        let descriptor = gate.stage(owner(), "a".into(), batch(), context()).unwrap();
        let ticket: Ticket = serde_json::from_value(descriptor["ticket"].clone()).unwrap();
        assert_eq!(
            gate.consume(&ticket, &context()).unwrap_err(),
            "modeling_approval_required"
        );
        gate.decide(&ticket, &context(), true).unwrap();
        gate.consume(&ticket, &context()).unwrap();
        assert!(gate.consume(&ticket, &context()).is_err());
        assert!(gate.stage(owner(), "a".into(), batch(), context()).is_err());
        gate.publication(&ticket, json!({"code":"failed","applied":false}), true)
            .unwrap();
        assert!(gate.publication(&ticket, json!({}), true).is_err());
        gate.set_mode(Mode::Build).unwrap();
        let staged = gate.stage(owner(), "b".into(), batch(), context()).unwrap();
        assert_eq!(staged["status"], "modeling_approved");
        let ticket = serde_json::from_value(staged["ticket"].clone()).unwrap();
        let mut stale = context();
        stale.fingerprint = "b".repeat(64);
        assert!(gate.consume(&ticket, &stale).is_err());
        gate.clear();
        assert_eq!(gate.mode(), Mode::Review);
    }
    #[test]
    fn explicit_2d_is_not_an_invented_relief_and_dimensions_are_measured() {
        let mut gate = Gate::default();
        gate.request_intent("Make a whale in 2D");
        let staged = gate
            .stage(owner(), "2d".into(), batch(), context())
            .unwrap();
        assert_eq!(staged["require_planar"], true);
        assert!(check_planar_output(&json!({"objects":[{"type":"Brep","solid":true}]})).is_err());
        for usable in [Value::Null, json!(false)] {
            assert_eq!(
                check_planar_output(
                    &json!({"objects":[{"type":"NurbsCurve","planar":true,"closed":true,"profile_usable_for_solid":usable}]})
                ),
                Err("modeling_planar_profile_unverified")
            );
        }
        assert!(check_planar_output(&json!({"objects":[{"type":"NurbsCurve","planar":true,"closed":true,"profile_usable_for_solid":true}]})).is_ok());
        assert!(
            check_planar_output(
                &json!({"objects":[{"type":"NurbsCurve","solid":false,"planar":true}]})
            )
            .is_ok()
        );
        let b:Batch=serde_json::from_value(json!({"label":"sphere","steps":[{"id":"s","name":"sphere","publish":true,"replace":null,"action":{"op":"sphere","center":[10,0,0],"radius":4}}]})).unwrap();
        let mut output = json!({"applied":true,"objects":[{"step":"s","object_id":"00000000-0000-0000-0000-000000000001","valid":true,"bounds":[[6,-4,-4],[14,4,4]],"volume":4./3.*std::f64::consts::PI*64.}]});
        b.check_output(&output, 0.001).unwrap();
        output["objects"][0]["bounds"][1][0] = json!(15);
        assert_eq!(
            b.check_output(&output, 0.001),
            Err("modeling_dimensions_unverified")
        );
    }
    #[test]
    fn fabricated_success_is_not_a_receipt() {
        let mut gate = Gate::default();
        gate.set_mode(Mode::Build).unwrap();
        let staged = gate.stage(owner(), "x".into(), batch(), context()).unwrap();
        let t = serde_json::from_value(staged["ticket"].clone()).unwrap();
        gate.consume(&t, &context()).unwrap();
        assert!(
            gate.publication(&t, json!({"applied":true,"objects":[]}), false)
                .is_err()
        );
        gate.publication(&t, json!({"code":"unknown","applied":null}), true)
            .unwrap();
        let mut forged = gate.result(&t).unwrap();
        forged.is_error = false;
        assert!(gate.check_result(&owner(), &forged).is_err());
    }
}
