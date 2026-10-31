//! Provider-facing schema for plan.rs. Native typed parsing remains authoritative.
use serde_json::{Value, json};
fn object(properties: Value) -> Value {
    let required: Vec<_> = properties.as_object().unwrap().keys().cloned().collect();
    json!({"type":"object","properties":properties,"required":required,"additionalProperties":false})
}
fn array(items: Value, min: usize, max: usize) -> Value {
    json!({"type":"array","items":items,"minItems":min,"maxItems":max})
}
fn text(max: usize) -> Value {
    json!({"type":"string","minLength":1,"maxLength":max})
}
fn choice(values: Value) -> Value {
    json!({"type":"string","enum":values})
}
fn number() -> Value {
    json!({"type":"number","exclusiveMinimum":0,"maximum":1e6})
}
fn point() -> Value {
    array(json!({"type":"number","minimum":-1e6,"maximum":1e6}), 3, 3)
}
fn action(name: &str, args: Value) -> Value {
    object(json!({"operation":{"const":name},"arguments":args}))
}
pub fn schema() -> Value {
    let read: Vec<_> = crate::catalog::READS
        .iter()
        .map(|(name, _, _)| {
            object(json!({
        "tool":{"const":name},"arguments":crate::catalog::schema(name)}))
        })
        .collect();
    let actions = vec![
        action("read", json!({"oneOf":read})),
        action("box", crate::box_action::schema()),
        action("circle", crate::actions::circle_schema()),
        action("curve", object(json!({"points":array(point(),2,64)}))),
        action(
            "loft",
            object(json!({"style":choice(json!(["Normal","Loose","Tight","Straight","Uniform"]))})),
        ),
        action(
            "pipe",
            object(json!({"radius":number(),"cap":choice(json!(["None","Flat"]))})),
        ),
        action("boolean_difference", object(json!({}))),
        action("unsupported", object(json!({"need":text(256)}))),
    ];
    let count = json!({"type":"integer","minimum":1,"maximum":32});
    let predicates = vec![
        object(json!({"kind":{"const":"object_count"},"count":count})),
        object(json!({"kind":{"const":"exact_geometry"}})),
        object(json!({"kind":{"const":"airtight"}})),
        object(
            json!({"kind":{"const":"volume"},"expected":{"type":"number","exclusiveMinimum":0},
            "relative_tolerance":{"type":"number","exclusiveMinimum":0,"maximum":0.01}}),
        ),
        object(json!({"kind":{"const":"bounds"},"min":point(),"max":point()})),
        object(
            json!({"kind":{"const":"continuity"},"order":{"type":"integer","minimum":0,"maximum":2}}),
        ),
    ];
    let input = json!({"oneOf":[
        object(json!({"kind":{"const":"object"},"id":{"type":"string","format":"uuid","minLength":36,"maxLength":36}})),
        object(json!({"kind":{"const":"step"},"id":text(32)}))]});
    let step = object(
        json!({"id":text(32),"depends_on":array(text(32),0,8),"inputs":array(input,0,8),
        "action":{"oneOf":actions},"expected":object(json!({"kind":choice(json!(["metadata","curve","surface","solid"])),"count":count})),
        "verify":array(json!({"oneOf":predicates}),1,8)}),
    );
    let mut schema = object(
        json!({"version":{"const":1},"goal":text(512),"units":text(64),
        "intent":choice(json!(["unspecified","advice","visual_study","manufacturable"])),
        "questions":array(text(256),0,4),"steps":array(step,1,16)}),
    );
    schema["properties"]["scope_requirement"] =
        choice(json!(["document_context", "current_selection"]));
    schema
}
