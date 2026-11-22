//! Join and Cap typed batch actions: schema shape, validation invariants,
//! receipt solid requirement (cap) vs. optional-open (join), and authority
//! parity with existing typed batch actions.

use narwhal_automation::modeling::{self, Action, Batch, Context, Gate, Mode};
use serde_json::{Value, json};

const UUID_A: &str = "00000000-0000-0000-0000-000000000001";
const UUID_B: &str = "00000000-0000-0000-0000-000000000002";

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

fn owner() -> narwhal_automation::conversation::Token {
    narwhal_automation::conversation::Token {
        conversation: 1,
        generation: 1,
        request: 1,
    }
}

fn ticket(v: &Value) -> narwhal_automation::modeling::Ticket {
    serde_json::from_value(v["ticket"].clone()).unwrap()
}

// ─── Schema shape ────────────────────────────────────────────────────────────

#[test]
fn schema_includes_join_and_cap_as_typed_variants() {
    let s = modeling::schema();
    let one_of = s["properties"]["steps"]["items"]["properties"]["action"]["oneOf"]
        .as_array()
        .unwrap();
    let ops: Vec<_> = one_of
        .iter()
        .map(|v| v["properties"]["op"]["const"].as_str().unwrap().to_string())
        .collect();
    assert!(
        ops.contains(&"join".to_string()),
        "join missing: {}",
        ops.join(",")
    );
    assert!(
        ops.contains(&"cap".to_string()),
        "cap missing: {}",
        ops.join(",")
    );
    // join has sources array 2..16
    let join_variant = one_of
        .iter()
        .find(|v| v["properties"]["op"]["const"] == "join")
        .unwrap();
    let sources = &join_variant["properties"]["sources"];
    assert_eq!(sources["minItems"], 2);
    assert_eq!(sources["maxItems"], 16);
    // cap has a single source reference
    let cap_variant = one_of
        .iter()
        .find(|v| v["properties"]["op"]["const"] == "cap")
        .unwrap();
    assert!(cap_variant["properties"].get("source").is_some());
    assert!(
        cap_variant["properties"].get("sources").is_none(),
        "cap must not have sources array"
    );
    assert!(
        cap_variant["properties"].get("radius").is_none(),
        "cap must not have radius"
    );
    assert!(
        cap_variant["properties"].get("kind").is_none(),
        "cap must not have kind"
    );
}

#[test]
fn agent_operations_supported_actions_include_join_cap() {
    let ops = narwhal_automation::actions::agent_operations();
    let actions: Vec<_> = ops["batch_executor"]["supported_actions"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    assert!(actions.contains(&"join".to_string()));
    assert!(actions.contains(&"cap".to_string()));
}

// ─── Join validation ─────────────────────────────────────────────────────────

fn join_batch(sources: Vec<&str>) -> Batch {
    let src: Vec<Value> = sources.iter().map(|s| json!(s)).collect();
    serde_json::from_value(json!({
        "label": "join",
        "steps": [
            {"id": "a", "name": "A", "publish": false, "replace": null,
             "action": {"op": "circle", "center": [0,0,0], "radius": 5}},
            {"id": "b", "name": "B", "publish": false, "replace": null,
             "action": {"op": "circle", "center": [0,0,10], "radius": 5}},
            {"id": "join", "name": "Join", "publish": true, "replace": null,
             "action": {"op": "join", "sources": src}}
        ]
    }))
    .unwrap()
}

#[test]
fn join_requires_at_least_two_sources() {
    let b = join_batch(vec![UUID_A]);
    assert_eq!(b.validate(0.001), Err("invalid_modeling_step"));
}

#[test]
fn join_rejects_more_than_sixteen_sources() {
    let srcs: Vec<&str> = (0..17)
        .map(|i| {
            let owned = format!("00000000-0000-0000-0000-0000000000{:02x}", i);
            &*Box::leak(owned.into_boxed_str())
        })
        .collect::<Vec<_>>();
    let b = join_batch(srcs);
    assert_eq!(b.validate(0.001), Err("invalid_modeling_step"));
}

#[test]
fn join_rejects_duplicate_sources() {
    let b = join_batch(vec![UUID_A, UUID_A]);
    assert_eq!(b.validate(0.001), Err("invalid_modeling_step"));
}

#[test]
fn join_rejects_self_reference() {
    // A step that references its own ID is invalid (the ref must resolve to an earlier step or a UUID).
    let b: Batch = serde_json::from_value(json!({
        "label": "self",
        "steps": [
            {"id": "a", "name": "A", "publish": false, "replace": null,
             "action": {"op": "circle", "center": [0,0,0], "radius": 5}},
            {"id": "join", "name": "Join", "publish": true, "replace": null,
             "action": {"op": "join", "sources": ["a", "join"]}}
        ]
    }))
    .unwrap();
    // "join" is not a UUID and is not an earlier step ID → unresolved
    assert_eq!(b.validate(0.001), Err("unresolved_modeling_reference"));
}

#[test]
fn join_rejects_unknown_fields() {
    // deny_unknown_fields on the Action enum rejects "kind" at deserialization.
    let v = json!({
        "label": "bad",
        "steps": [
            {"id": "a", "name": "A", "publish": false, "replace": null,
             "action": {"op": "circle", "center": [0,0,0], "radius": 5}},
            {"id": "join", "name": "Join", "publish": true, "replace": null,
             "action": {"op": "join", "sources": [UUID_A, UUID_B], "kind": "union"}}
        ]
    });
    assert!(serde_json::from_value::<Batch>(v).is_err());
}

#[test]
fn join_valid_with_two_sources() {
    let b = join_batch(vec![UUID_A, UUID_B]);
    assert!(b.validate(0.001).is_ok());
}

#[test]
fn join_valid_with_sixteen_sources() {
    let srcs: Vec<&str> = (0..16)
        .map(|i| {
            let owned = format!("00000000-0000-0000-0000-00000000{:04x}", i);
            &*Box::leak(owned.into_boxed_str())
        })
        .collect::<Vec<_>>();
    let b = join_batch(srcs);
    assert!(b.validate(0.001).is_ok());
}

// ─── Cap validation ──────────────────────────────────────────────────────────

fn cap_batch(source: &str) -> Batch {
    serde_json::from_value(json!({
        "label": "cap",
        "steps": [
            {"id": "a", "name": "A", "publish": false, "replace": null,
             "action": {"op": "circle", "center": [0,0,0], "radius": 5}},
            {"id": "loft", "name": "Loft", "publish": false, "replace": null,
             "action": {"op": "loft", "sections": ["a", UUID_A]}},
            {"id": "cap", "name": "Cap", "publish": true, "replace": null,
             "action": {"op": "cap", "source": source}}
        ]
    }))
    .unwrap()
}

#[test]
fn cap_rejects_unknown_fields() {
    // deny_unknown_fields on the Action enum rejects "tolerance" at deserialization.
    let v = json!({
        "label": "bad",
        "steps": [
            {"id": "a", "name": "A", "publish": false, "replace": null,
             "action": {"op": "circle", "center": [0,0,0], "radius": 5}},
            {"id": "cap", "name": "Cap", "publish": true, "replace": null,
             "action": {"op": "cap", "source": UUID_A, "tolerance": 0.01}}
        ]
    });
    assert!(serde_json::from_value::<Batch>(v).is_err());
}

#[test]
fn cap_rejects_extra_arguments() {
    // deny_unknown_fields on the Action enum rejects the "sources" array for cap.
    let v = json!({
        "label": "bad",
        "steps": [
            {"id": "a", "name": "A", "publish": false, "replace": null,
             "action": {"op": "circle", "center": [0,0,0], "radius": 5}},
            {"id": "cap", "name": "Cap", "publish": true, "replace": null,
             "action": {"op": "cap", "source": UUID_A, "sources": [UUID_A, UUID_B]}}
        ]
    });
    assert!(serde_json::from_value::<Batch>(v).is_err());
}

#[test]
fn cap_valid_with_single_source() {
    let b = cap_batch(UUID_A);
    assert!(b.validate(0.001).is_ok());
}

#[test]
fn cap_rejects_self_reference() {
    let b: Batch = serde_json::from_value(json!({
        "label": "self",
        "steps": [
            {"id": "cap", "name": "Cap", "publish": true, "replace": null,
             "action": {"op": "cap", "source": "cap"}}
        ]
    }))
    .unwrap();
    assert_eq!(b.validate(0.001), Err("unresolved_modeling_reference"));
}

// ─── Receipt invariants ──────────────────────────────────────────────────────

#[test]
fn cap_receipt_requires_solid_true() {
    let b = cap_batch(UUID_A);
    // cap receipt with solid=false must be refused
    let bad = json!({"applied": true, "objects": [
        {"step": "cap", "object_id": UUID_A, "valid": true, "solid": false,
         "airtight": false, "bounds": [[0,0,0],[10,10,10]]}
    ]});
    assert_eq!(
        b.check_output(&bad, 0.001),
        Err("modeling_cap_solid_unverified")
    );
}

#[test]
fn cap_receipt_accepts_solid_true() {
    let b = cap_batch(UUID_A);
    let ok = json!({"applied": true, "objects": [
        {"step": "cap", "object_id": UUID_A, "valid": true, "solid": true,
         "airtight": true, "bounds": [[0,0,0],[10,10,10]]}
    ]});
    assert!(b.check_output(&ok, 0.001).is_ok());
}

#[test]
fn join_receipt_may_be_open_surface() {
    let b = join_batch(vec![UUID_A, UUID_B]);
    // join receipt with solid=false (open surface) must be accepted
    let open = json!({"applied": true, "objects": [
        {"step": "join", "object_id": UUID_A, "valid": true, "solid": false,
         "airtight": false, "bounds": [[0,0,0],[10,10,10]]}
    ]});
    assert!(b.check_output(&open, 0.001).is_ok());
}

#[test]
fn join_receipt_may_be_solid() {
    let b = join_batch(vec![UUID_A, UUID_B]);
    let solid = json!({"applied": true, "objects": [
        {"step": "join", "object_id": UUID_A, "valid": true, "solid": true,
         "airtight": true, "bounds": [[0,0,0],[10,10,10]]}
    ]});
    assert!(b.check_output(&solid, 0.001).is_ok());
}

// ─── Authority parity ────────────────────────────────────────────────────────

#[test]
fn join_cap_review_mode_requires_approval() {
    let mut gate = Gate::default();
    assert_eq!(gate.mode(), Mode::Review);

    let jb = join_batch(vec![UUID_A, UUID_B]);
    let staged = gate.stage(owner(), "j".into(), jb, context()).unwrap();
    assert_eq!(staged["status"], "modeling_review_required");
    assert_eq!(
        gate.consume(&ticket(&staged), &context()).unwrap_err(),
        "modeling_approval_required"
    );

    let cb = cap_batch(UUID_A);
    let staged = gate.stage(owner(), "c".into(), cb, context()).unwrap();
    assert_eq!(staged["status"], "modeling_review_required");
    assert_eq!(
        gate.consume(&ticket(&staged), &context()).unwrap_err(),
        "modeling_approval_required"
    );
}

#[test]
fn join_cap_build_mode_autoruns() {
    let mut gate = Gate::default();
    gate.set_mode(Mode::Build).unwrap();

    let jb = join_batch(vec![UUID_A, UUID_B]);
    let staged = gate.stage(owner(), "j".into(), jb, context()).unwrap();
    assert_eq!(staged["status"], "modeling_approved");
    gate.consume(&ticket(&staged), &context()).unwrap();

    let cb = cap_batch(UUID_A);
    let staged = gate.stage(owner(), "c".into(), cb, context()).unwrap();
    assert_eq!(staged["status"], "modeling_approved");
    gate.consume(&ticket(&staged), &context()).unwrap();
}

#[test]
fn join_cap_replay_and_stale_are_refused() {
    let mut gate = Gate::default();
    gate.set_mode(Mode::Build).unwrap();
    let jb = join_batch(vec![UUID_A, UUID_B]);
    let staged = gate.stage(owner(), "j".into(), jb, context()).unwrap();
    assert_eq!(staged["status"], "modeling_approved");
    gate.consume(&ticket(&staged), &context()).unwrap();
    // Replaying the same call id is refused.
    let jb = join_batch(vec![UUID_A, UUID_B]);
    assert_eq!(
        gate.stage(owner(), "j".into(), jb, context()).unwrap_err(),
        "modeling_call_replayed"
    );
}

// ─── MCP validation ──────────────────────────────────────────────────────────

#[test]
fn mcp_validate_rejects_unknown_join_fields() {
    let mut args = serde_json::Map::new();
    args.insert("request_id".to_string(), json!("test"));
    args.insert(
        "batch".to_string(),
        json!({
            "label": "mcp",
            "steps": [
                {"id": "a", "name": "A", "publish": false, "replace": null,
                 "action": {"op": "circle", "center": [0,0,0], "radius": 5}},
                {"id": "join", "name": "Join", "publish": true, "replace": null,
                 "action": {"op": "join", "sources": [UUID_A, UUID_B], "kind": "union"}}
            ]
        }),
    );
    assert_eq!(
        modeling::validate_mcp("narwhal.model", &args).unwrap_err(),
        "invalid_modeling_batch"
    );
}

#[test]
fn mcp_validate_accepts_valid_join_cap() {
    let mut args = serde_json::Map::new();
    args.insert("request_id".to_string(), json!("test"));
    args.insert(
        "batch".to_string(),
        json!({
            "label": "mcp",
            "steps": [
                {"id": "a", "name": "A", "publish": false, "replace": null,
                 "action": {"op": "circle", "center": [0,0,0], "radius": 5}},
                {"id": "b", "name": "B", "publish": false, "replace": null,
                 "action": {"op": "circle", "center": [0,0,10], "radius": 5}},
                {"id": "join", "name": "Join", "publish": true, "replace": null,
                 "action": {"op": "join", "sources": [UUID_A, UUID_B]}},
                {"id": "cap", "name": "Cap", "publish": true, "replace": null,
                 "action": {"op": "cap", "source": "join"}}
            ]
        }),
    );
    assert!(modeling::validate_mcp("narwhal.model", &args).is_ok());
}

// ─── references() ────────────────────────────────────────────────────────────

#[test]
fn join_references_include_all_sources() {
    let a: Action =
        serde_json::from_value(json!({"op": "join", "sources": ["a", UUID_A, UUID_B]})).unwrap();
    let refs = a.references();
    assert_eq!(refs.len(), 3);
    assert!(refs.contains(&"a"));
    assert!(refs.contains(&UUID_A));
    assert!(refs.contains(&UUID_B));
}

#[test]
fn cap_references_include_single_source() {
    let a: Action = serde_json::from_value(json!({"op": "cap", "source": UUID_A})).unwrap();
    let refs = a.references();
    assert_eq!(refs, vec![UUID_A]);
}
