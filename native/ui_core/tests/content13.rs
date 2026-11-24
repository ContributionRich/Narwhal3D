//! Independently captured pre-retirement Python values and menu callable outputs.
//! Exercises the same two-pass C ABI used by the host, not a second menu engine.
#[path = "support/golden27_sha256.rs"]
mod sha256;
use serde_json::{json, Value};
use std::ptr;
extern "C" {
    fn narwhal_ui_content_abi() -> u32;
    fn narwhal_ui_content_read(
        op: u32,
        input: *const u8,
        len: usize,
        output: *mut u8,
        capacity: usize,
        needed: *mut usize,
    ) -> u32;
}
const GOLDEN: &str = include_str!("data/content13_golden.json");
const PROVENANCE: &str = include_str!("data/content13_provenance.json");
const DATA: &str = include_str!("../src/content_data/content.json");
fn invoke(op: u32, args: Value) -> Value {
    // Force linkage to the crate that provides the actual exported symbols.
    assert_eq!(narwhal_ui_core::narwhal_editor_abi(), 1);
    let input = serde_json::to_vec(&args).unwrap();
    let mut needed = 0;
    unsafe {
        assert_eq!(narwhal_ui_content_abi(), 1);
        assert_eq!(
            narwhal_ui_content_read(
                op,
                input.as_ptr(),
                input.len(),
                ptr::null_mut(),
                0,
                &mut needed
            ),
            0
        );
        assert!((1..=8 * 1024 * 1024).contains(&needed));
        let mut output = vec![0u8; needed];
        let size = needed;
        assert_eq!(
            narwhal_ui_content_read(
                op,
                input.as_ptr(),
                input.len(),
                output.as_mut_ptr(),
                size,
                &mut needed
            ),
            0
        );
        assert_eq!(needed, size);
        serde_json::from_slice(&output).unwrap()
    }
}
fn fixture() -> Value {
    serde_json::from_str(GOLDEN).unwrap()
}
#[test]
fn immutable_original_provenance_and_fixture() {
    // Hard roots are deliberately NOT obtained from the mutable provenance file.
    assert_eq!(
        sha256::hash(GOLDEN.as_bytes()),
        "46f2414e78231460cec4e074c13ed69cc3bc8ad15a7e8514775b591ac19557b0"
    );
    assert_eq!(
        sha256::hash(DATA.as_bytes()),
        "5aae90aa8fa70a455430166433647919645fd4abd5e8706c98f2c286fe727792"
    );
    let p: Value = serde_json::from_str(PROVENANCE).unwrap();
    assert_eq!(p["base"], "438f9260");
    assert_eq!(p["unicode"], "15.1.0");
    assert_eq!(p["fixture_sha256"], sha256::hash(GOLDEN.as_bytes()));
    assert_eq!(p["content_sha256"], sha256::hash(DATA.as_bytes()));
    let roots = [
        (
            "toolbar_data",
            "b2cc1bf849f2d8515701562a51c3df14f81811dc12ea710639671a6abaa7543e",
        ),
        (
            "toolbar_macro_data",
            "3ec388551e7223d26e2719be3524981cc0f75d7ccd9d76934c006b839edd3c92",
        ),
        (
            "menubar_resource_data",
            "cb6cba22408dfddd2dc78a2fb4fd9ba202d72f2c2180f8ca3ad869299594a29e",
        ),
        (
            "flyout_icon_data",
            "1ce1eb6a16296e638890eb0ebf333b218f0e340cd1c9580a67abda194e272986",
        ),
    ];
    assert_eq!(p["sources"].as_object().unwrap().len(), 4);
    for (name, hash) in roots {
        assert_eq!(p["sources"][format!("narwhal3d/{name}.py")], hash);
    }
}
#[test]
fn every_authored_typed_value_matches_original() {
    let f = fixture();
    let exports = f["exports"].as_object().unwrap();
    assert_eq!(exports.len(), 4);
    for (name, expected) in exports {
        assert_eq!(
            invoke(0, json!({"name":name})),
            *expected,
            "{name}: full typed content/order"
        );
    }
}
#[test]
fn five_registry_snapshots_and_synthetic_unicode_match_original() {
    let f = fixture();
    let cases = f["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 6);
    assert_eq!(
        cases
            .iter()
            .map(|c| c["name"].as_str().unwrap())
            .collect::<Vec<_>>(),
        [
            "full_current",
            "empty",
            "subset",
            "missing_future",
            "newly_registered",
            "synthetic_unicode"
        ]
    );
    for case in cases {
        let mut args = json!({"registered":case["registry"]});
        if let Some(tabs) = case.get("tabs") {
            args["tabs"] = tabs.clone();
        }
        assert_eq!(
            invoke(1, args.clone()),
            case["groups"],
            "{} groups",
            case["name"]
        );
        let menus = invoke(2, args);
        assert_eq!(menus, case["menus"], "{} expanded menus", case["name"]);
        if let Some(counts) = case.get("counts") {
            assert_eq!(
                invoke(3, json!({"menus":menus})),
                *counts,
                "{} counts",
                case["name"]
            );
        }
    }
}
#[test]
fn ffi_descriptors_schema_capacity_and_no_partial_output() {
    let input = br#"{"name":"toolbar_data"}"#;
    let mut needed = 999;
    unsafe {
        assert_eq!(
            narwhal_ui_content_read(0, ptr::null(), input.len(), ptr::null_mut(), 0, &mut needed),
            1
        );
        assert_eq!(
            narwhal_ui_content_read(
                0,
                input.as_ptr(),
                input.len(),
                ptr::null_mut(),
                1,
                &mut needed
            ),
            1
        );
        assert_eq!(
            narwhal_ui_content_read(
                0,
                input.as_ptr(),
                8 * 1024 * 1024 + 1,
                ptr::null_mut(),
                0,
                &mut needed
            ),
            3
        );
        assert_eq!(needed, 0);
        assert_eq!(
            narwhal_ui_content_read(
                0,
                input.as_ptr(),
                input.len(),
                ptr::null_mut(),
                0,
                ptr::null_mut()
            ),
            1
        );
        assert_eq!(
            narwhal_ui_content_read(
                99,
                input.as_ptr(),
                input.len(),
                ptr::null_mut(),
                0,
                &mut needed
            ),
            1
        );
        assert_eq!(needed, 0);
        assert_eq!(
            narwhal_ui_content_read(0, b"{".as_ptr(), 1, ptr::null_mut(), 0, &mut needed),
            2
        );
        let mut out = [0xa5; 8];
        assert_eq!(
            narwhal_ui_content_read(
                0,
                input.as_ptr(),
                input.len(),
                out.as_mut_ptr(),
                out.len(),
                &mut needed
            ),
            3
        );
        assert!(needed > out.len());
        assert_eq!(out, [0xa5; 8]);
        let bad=serde_json::to_vec(&json!({"registered":[],"tabs":{"t":"tuple","v":[{"t":"dict","v":[["name","Curve Tools"],["buttons",{"t":"tuple","v":[{"t":"dict","v":[]}]}]]}]}})).unwrap();
        assert_eq!(
            narwhal_ui_content_read(1, bad.as_ptr(), bad.len(), ptr::null_mut(), 0, &mut needed),
            0
        );
        let unknown = br#"{"name":"no such content"}"#;
        assert_eq!(
            narwhal_ui_content_read(
                0,
                unknown.as_ptr(),
                unknown.len(),
                ptr::null_mut(),
                0,
                &mut needed
            ),
            2
        );
    }
}

#[test]
fn command_walker_preserves_nested_order_duplicates_and_empty_exclusion() {
    let row = |command: Value| json!({"t":"tuple","v":["item","label","",command]});
    let nested = json!({"t":"tuple","v":[row(json!("line")),row(json!("new"))]});
    let rows = json!({"t":"tuple","v":[row(json!("new")),row(Value::Null),row(json!("")),
        {"t":"tuple","v":["popup","child","",nested,null]},row(json!("circle")),
        {"t":"tuple","v":["sep","","",null]}]});
    assert_eq!(
        invoke(4, json!({"rows":rows})),
        json!({"t":"tuple","v":["new","line","new","circle"]})
    );
}
