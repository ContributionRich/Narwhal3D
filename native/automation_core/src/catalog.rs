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
        "Read bounded layer pages with local visibility/lock flags; inherited flags are unavailable.",
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
    tools
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
    match name {
        "narwhal.object_summary" => {
            if args.len() != 1
                || !args
                    .get("object_id")
                    .and_then(Value::as_str)
                    .is_some_and(uuid)
            {
                return Err("invalid_arguments");
            }
        }
        "narwhal.objects" | "narwhal.layers" => {
            if args.len() != 2
                || !args
                    .get("offset")
                    .and_then(Value::as_u64)
                    .is_some_and(|n| n <= 10000)
                || !args
                    .get("limit")
                    .and_then(Value::as_u64)
                    .is_some_and(|n| (1..=32).contains(&n))
            {
                return Err("invalid_arguments");
            }
        }
        _ if !args.is_empty() => return Err("invalid_arguments"),
        _ => {}
    }
    Ok(())
}
pub fn validate(call: &ToolCall) -> Result<(), &'static str> {
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
