//! Shared CAD read/proposal semantics. Aliases never select arbitrary routes.
use crate::{
    actions,
    provider::{ToolCall, ToolDefinition},
};
use serde_json::{Map, Value, json};

pub const READS: [(&str, &str, &str); 9] = [
    (
        "narwhal.application_info",
        "narwhal_application_info",
        "Read application version and read-only permissions.",
    ),
    (
        "narwhal.document_summary",
        "narwhal_document_summary",
        "Read units, tolerance and bounded document metadata; authoritative revision is unavailable.",
    ),
    (
        "narwhal.selection_summary",
        "narwhal_selection_summary",
        "Read up to 64 selected whole-object IDs; component selection is separate.",
    ),
    (
        "narwhal.object_summary",
        "narwhal_object_summary",
        "Read one object's attributes and declared representation, not geometric measurements.",
    ),
    (
        "narwhal.objects",
        "narwhal_objects",
        "Discover stable object UUIDs with bounded live offset pagination, not a consistent revision snapshot.",
    ),
    (
        "narwhal.layers",
        "narwhal_layers",
        "Read bounded layer pages with local and inherited document-layer flags where the initialized workspace can resolve them; not viewport visibility.",
    ),
    (
        "narwhal.workspace",
        "narwhal_workspace",
        "Read units, tolerance, current layer and busy command; missing phase/CPlane/view hooks are explicit.",
    ),
    (
        "narwhal.components",
        "narwhal_components",
        "Read bounded canonical component selections only if Main attached the snapshot API; otherwise unavailable.",
    ),
    (
        "narwhal.operations",
        "narwhal_operations",
        "Discover typed operation schemas and real blockers; registry membership is not execution support.",
    ),
];
pub fn canonical(alias: &str) -> Option<&'static str> {
    READS
        .iter()
        .find(|(_, a, _)| *a == alias)
        .map(|(n, _, _)| *n)
}
pub fn definitions() -> Vec<ToolDefinition> {
    let mut tools: Vec<_> = READS
        .iter()
        .map(|(name, alias, description)| ToolDefinition {
            name: (*alias).into(),
            description: (*description).into(),
            parameters: schema(name),
        })
        .collect();
    tools.push(ToolDefinition {name:"narwhal_propose_circle".into(), description:"Construct a typed center/radius circle proposal for user review. Never executes it; missing authoritative document/Undo hooks keep production writes disabled. Center is world coordinates, orientation is the captured active CPlane.".into(), parameters:actions::circle_schema()});
    tools.push(ToolDefinition { name:"narwhal_create_box".into(),
        description:"Request creation of one new world-XYZ-aligned analytic box (a cube has equal size values) in document units. Origin is the minimum corner; size is positive X/Y/Z length. Narwhal displays exact dimensions for explicit human approval before creating anything. This appends a solid, never modifies selected objects. Use one box call per response; ask for dimensions if unclear.".into(),
        parameters:crate::box_action::schema() });
    tools.push(ToolDefinition { name:"narwhal_plan".into(),
        description:"Validate and preflight a CAD plan against current-turn read results and actual executor availability. Never executes or approves. Read workspace/needed objects first; keep arguments under 4096 bytes. Unknown operations/options are rejected; name missing capabilities using unsupported. Questions are unresolved, not assumed answered.".into(),
        parameters:crate::plan_schema::schema() });
    tools.push(ToolDefinition { name:"narwhal_verify".into(),
        description:"Check a separately approved Box action's host receipt against requested dimensions and a later object-ID read. Does not execute anything. Current geometry verification remains incomplete without fresh authoritative measurements; never infer success from provider prose.".into(),
        parameters:crate::verification::schema() });
    tools.push(crate::modeling::definition());
    tools.push(ToolDefinition { name:"narwhal_inspect".into(), description:"Measure one current object by stable UUID: exact master bounds/volume/airtightness or curve closure/planarity/degree. Not a screenshot or design-quality judgment.".into(), parameters:schema("narwhal.object_summary") });
    tools.push(ToolDefinition{name:"narwhal_measure".into(),description:"Measure the minimum distance between two exact analytic solid/surface objects by UUID, in document units. Zero within tolerance means overlapping/touching, not a unified watertight solid. Meshes and objects without exact shapes are refused.".into(),parameters:measurement_schema()});
    tools.push(ToolDefinition{name:"narwhal_capture_view".into(),description:"Inspect appearance using one small JPEG of the active viewport, only after separate local image-sharing consent for this call. No desktop, other panes, paths or URLs. Use at most one capture per response; inspect and revise recognizability/features before declaring a design finished. A snapshot cannot prove exactness or manufacturability.".into(),parameters:schema("narwhal.capture_view")});
    tools.push(ToolDefinition{name:"narwhal_export".into(),description:"Export explicit existing object UUIDs to STEP or 3dm only after a local file-picker choice. The user must choose a NEW destination on every call, even in Build mode. Paths, overwrites, shell and URLs are not accepted. Canceling applies nothing. STEP requires existing qualified exact geometry; export does not verify design quality or solid validity. Never claim a save/export without its successful tool receipt.".into(),parameters:crate::export_action::schema()});
    tools.push(ToolDefinition{name:"narwhal_frame".into(),description:"Select and frame explicit existing object UUIDs together in the active viewport. Changes selection/view only, not document geometry. Before visual inspection frame all design components, not just the most recently created eye or marker. Hidden/unavailable objects are refused.".into(),parameters:frame_schema()});
    tools.push(ToolDefinition{name:"narwhal_curve_samples".into(),description:"Study an existing curve's actual shape through bounded normalized-parameter point/tangent/curvature samples, degree and control-point count. Works even when full NURBS inspection exceeds its point limit. Fractions refer to curve domain, not arc length. Start with 24 or32 samples; refine interesting fractional intervals. This does not rebuild or change the curve.".into(),parameters:crate::curve_inspection::samples_schema()});
    tools.push(ToolDefinition{name:"narwhal_compare_curves".into(),description:"Compare two existing curves by bidirectional sampled point-to-exact-curve distances and actual control-point counts. Use after synthesizing a simpler curve with curve/nurbs tools. The sampled maximum is approximate, NOT a certified global tolerance or Hausdorff bound; curves remain exact NURBS geometry. Original and candidate are not modified.".into(),parameters:crate::curve_inspection::compare_schema()});
    tools
}
pub fn frame_schema() -> Value {
    json!({"type":"object","properties":{"object_ids":{"type":"array","minItems":1,"maxItems":48,"uniqueItems":true,"items":{"type":"string","format":"uuid","minLength":36,"maxLength":36}},"view_direction":{"type":"array","minItems":3,"maxItems":3,"items":{"type":"number","minimum":-1000000,"maximum":1000000},"description":"Optional nonzero direction FROM target TOWARD camera. [0,0,1] looks down from above; [1,-1,1] is an elevated oblique view. Chooses perspective inspection angle only, never rotates objects; omit to preserve current view."}},"required":["object_ids"],"additionalProperties":false})
}
pub fn validate_frame(args: &Map<String, Value>) -> Result<(), &'static str> {
    let ids = args
        .get("object_ids")
        .and_then(Value::as_array)
        .ok_or("invalid_frame_arguments")?;
    let mut unique = std::collections::HashSet::new();
    if args
        .keys()
        .any(|key| key != "object_ids" && key != "view_direction")
        || !(1..=48).contains(&ids.len())
        || ids
            .iter()
            .any(|id| id.as_str().is_none_or(|s| !uuid(s) || !unique.insert(s)))
    {
        return Err("invalid_frame_arguments");
    }
    if let Some(direction) = args.get("view_direction") {
        let vector: [f64; 3] =
            serde_json::from_value(direction.clone()).map_err(|_| "invalid_frame_arguments")?;
        crate::modeling::view_basis(vector).map_err(|_| "invalid_frame_arguments")?;
    }
    Ok(())
}
pub fn measurement_schema() -> Value {
    json!({"type":"object","properties":{"left":{"type":"string","format":"uuid","minLength":36,"maxLength":36},"right":{"type":"string","format":"uuid","minLength":36,"maxLength":36}},"required":["left","right"],"additionalProperties":false})
}
pub fn validate_measurement(args: &Map<String, Value>) -> Result<(), &'static str> {
    if args.len() != 2
        || !["left", "right"]
            .iter()
            .all(|key| args.get(*key).and_then(Value::as_str).is_some_and(uuid))
    {
        return Err("invalid_measurement_arguments");
    }
    Ok(())
}
/// Current agent surface. Legacy Box/proposal/plan schemas remain available to
/// explicit compatibility callers, but must not constrain a new modeling agent.
pub fn agent_definitions() -> Vec<ToolDefinition> {
    definitions()
        .into_iter()
        .filter(|tool| {
            canonical(&tool.name).is_some()
                || matches!(
                    tool.name.as_str(),
                    "narwhal_export"
                        | "narwhal_model"
                        | "narwhal_inspect"
                        | "narwhal_measure"
                        | "narwhal_capture_view"
                        | "narwhal_frame"
                        | "narwhal_curve_samples"
                        | "narwhal_compare_curves"
                )
        })
        .collect()
}

#[cfg(test)]
#[test]
fn current_agent_uses_general_batches_not_legacy_planner_gates() {
    let tools = agent_definitions();
    assert_eq!(tools.len(), 17);
    assert!(
        tools
            .iter()
            .any(|t| t.name == "narwhal_export" && t.parameters == crate::export_action::schema())
    );
    let id = "00000000-0000-0000-0000-000000000001";
    assert!(validate_frame(&serde_json::from_value(json!({"object_ids":[id]})).unwrap()).is_ok());
    for bad in [
        json!({"object_ids":[]}),
        json!({"object_ids":[id,id]}),
        json!({"object_ids":["selected"]}),
        json!({"object_ids":[id],"path":"private"}),
        json!({"object_ids":vec![id;49]}),
    ] {
        assert_eq!(
            validate_frame(&serde_json::from_value(bad).unwrap()),
            Err("invalid_frame_arguments")
        );
    }
    assert!(
        tools
            .iter()
            .any(|t| t.name == "narwhal_model" && t.parameters == crate::modeling::schema())
    );
    assert!(tools.iter().any(|t| t.name == "narwhal_inspect"));
    assert!(tools.iter().any(|t| t.name == "narwhal_measure"));
    let args=serde_json::from_value(json!({"left":"00000000-0000-0000-0000-000000000001","right":"00000000-0000-0000-0000-000000000002"})).unwrap();
    assert!(validate_measurement(&args).is_ok());
    assert!(
        validate_measurement(
            &serde_json::from_value(json!({"left":"selected","right":"all"})).unwrap()
        )
        .is_err()
    );
    let mut arbitrary = args;
    arbitrary.insert("script".into(), json!("eval"));
    assert!(validate_measurement(&arbitrary).is_err());
    for name in [
        "narwhal_create_box",
        "narwhal_propose_circle",
        "narwhal_plan",
        "narwhal_verify",
    ] {
        assert!(!tools.iter().any(|t| t.name == name));
        assert!(
            definitions().iter().any(|t| t.name == name),
            "compatibility schema lost"
        );
    }
}

pub fn schema(name: &str) -> Value {
    match name {
        "narwhal.object_summary" => {
            json!({"type":"object","properties":{"object_id":{"type":"string","format":"uuid","minLength":36,"maxLength":36}},"required":["object_id"],"additionalProperties":false})
        }
        "narwhal.objects" | "narwhal.layers" => {
            json!({"type":"object","properties":{"offset":{"type":"integer","minimum":0,"maximum":10000},"limit":{"type":"integer","minimum":1,"maximum":32}},"required":["offset","limit"],"additionalProperties":false})
        }
        _ => json!({"type":"object","properties":{},"additionalProperties":false}),
    }
}
pub fn uuid(id: &str) -> bool {
    id.len() == 36
        && id.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_hexdigit()
            }
        })
}
pub fn validate_read(name: &str, args: &Map<String, Value>) -> Result<(), &'static str> {
    if !READS.iter().any(|(n, _, _)| *n == name) {
        return Err("tool_not_allowed");
    }
    let valid = match name {
        "narwhal.object_summary" => {
            args.len() == 1
                && args
                    .get("object_id")
                    .and_then(Value::as_str)
                    .is_some_and(uuid)
        }
        "narwhal.objects" | "narwhal.layers" => {
            args.len() == 2
                && args
                    .get("offset")
                    .and_then(Value::as_u64)
                    .is_some_and(|n| n <= 10000)
                && args
                    .get("limit")
                    .and_then(Value::as_u64)
                    .is_some_and(|n| (1..=32).contains(&n))
        }
        _ => args.is_empty(),
    };
    if !valid {
        return Err("invalid_arguments");
    }
    Ok(())
}
pub fn validate(call: &ToolCall) -> Result<(), &'static str> {
    if call.name == "narwhal_export" {
        return crate::export_action::validate(&call.arguments);
    }
    if call.name == "narwhal_measure" {
        return validate_measurement(&call.arguments);
    }
    if call.name == "narwhal_model" {
        let batch: crate::modeling::Batch =
            serde_json::from_value(Value::Object(call.arguments.clone()))
                .map_err(|_| "invalid_modeling_batch")?;
        return batch.validate(0.);
    }
    if call.name == "narwhal_curve_samples" {
        return crate::curve_inspection::validate_samples(&call.arguments);
    }
    if call.name == "narwhal_compare_curves" {
        return crate::curve_inspection::validate_compare(&call.arguments);
    }
    if call.name == "narwhal_frame" {
        return validate_frame(&call.arguments);
    }
    if call.name == "narwhal_capture_view" {
        return if call.arguments.is_empty() {
            Ok(())
        } else {
            Err("invalid_viewport_arguments")
        };
    }
    if call.name == "narwhal_inspect" {
        return validate_read("narwhal.object_summary", &call.arguments);
    }
    if call.name == "narwhal_verify" {
        let request: crate::verification::Request =
            serde_json::from_value(Value::Object(call.arguments.clone()))
                .map_err(|_| "invalid_verification_request")?;
        return request.validate();
    }
    if call.name == "narwhal_plan" {
        let plan: crate::plan::Plan = serde_json::from_value(Value::Object(call.arguments.clone()))
            .map_err(|_| "invalid_plan")?;
        return plan.validate();
    }
    if call.name == "narwhal_create_box" {
        let args: crate::box_action::BoxArgs =
            serde_json::from_value(Value::Object(call.arguments.clone()))
                .map_err(|_| "invalid_box_arguments")?;
        return args.validate();
    }
    if call.name == "narwhal_propose_circle" {
        let args: actions::CircleArgs =
            serde_json::from_value(Value::Object(call.arguments.clone()))
                .map_err(|_| "invalid_circle_arguments")?;
        return args.validate();
    }
    let name = canonical(&call.name).ok_or("unsupported_tool")?;
    validate_read(name, &call.arguments).map_err(|_| "invalid_tool_arguments")
}

/// Only Rust unit-test binaries can construct this catalog. No flag, JSON field,
/// feature, FFI export or environment variable enables it in normal builds.
#[cfg(test)]
pub(crate) fn primitive_fixture_definitions() -> Vec<ToolDefinition> {
    ["sphere", "cylinder", "cone"]
        .iter()
        .map(|kind| {
            let mut parameters = crate::actions::circle_schema();
            parameters["properties"]["radius"]["maximum"] = json!(1e6);
            parameters["properties"]["center"]["items"] =
                json!({"type":"number","minimum":-1e6,"maximum":1e6});
            if *kind != "sphere" {
                parameters["properties"]["height"] =
                    json!({"type":"number","exclusiveMinimum":0,"maximum":1e6});
                parameters["required"]
                    .as_array_mut()
                    .unwrap()
                    .push(json!("height"));
            }
            ToolDefinition {
                name: format!("narwhal_create_{kind}"),
                description: "TEST ONLY disabled primitive fixture".into(),
                parameters,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn aliases_schemas_and_permissions_are_unambiguous() {
        let names: std::collections::HashSet<_> = READS.iter().map(|(n, _, _)| n).collect();
        let aliases: std::collections::HashSet<_> = READS.iter().map(|(_, a, _)| a).collect();
        assert_eq!(names.len(), 9);
        assert_eq!(aliases.len(), 9);
        assert!(
            validate_read(
                "narwhal.objects",
                json!({"offset":0,"limit":32}).as_object().unwrap()
            )
            .is_ok()
        );
        assert!(
            validate_read(
                "narwhal.layers",
                json!({"offset":false,"limit":32}).as_object().unwrap()
            )
            .is_err()
        );
        assert!(validate_read("narwhal.propose_circle", &Map::new()).is_err());
        assert!(validate_read("narwhal.execute", &Map::new()).is_err());
    }
}
