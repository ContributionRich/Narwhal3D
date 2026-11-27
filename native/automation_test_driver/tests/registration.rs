use std::{fs, path::PathBuf};
const REQUIRED_DRIVER_PATHS: &[&str] = &[
    "native/automation_test_driver/Cargo.lock",
    "native/automation_test_driver/Cargo.toml",
    "native/automation_test_driver/README.md",
    "native/automation_test_driver/src/additional.rs",
    "native/automation_test_driver/src/additional_catalog_provenance_v1.json",
    "native/automation_test_driver/src/additional_catalog_v1.json",
    "native/automation_test_driver/src/anchor_overrides_v1.json",
    "native/automation_test_driver/src/catalog.json",
    "native/automation_test_driver/src/catalog.rs",
    "native/automation_test_driver/src/discovery.rs",
    "native/automation_test_driver/src/fixtures.rs",
    "native/automation_test_driver/src/lib.rs",
    "native/automation_test_driver/src/main.rs",
    "native/automation_test_driver/src/overrides.rs",
    "native/automation_test_driver/tests/cli.rs",
    "native/automation_test_driver/tests/registration.rs",
];
fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}
#[test]
fn complete_driver_source_is_registered() {
    let root = root();
    let builder: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(root.join("native/source_tools/src/publication.json")).unwrap(),
    )
    .unwrap();
    let audit: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(root.join("native/source_tools/src/audit.json")).unwrap(),
    )
    .unwrap();
    for name in REQUIRED_DRIVER_PATHS {
        assert!(root.join(name).is_file(), "missing {name}");
        assert!(
            builder["NATIVE_FILES"]["$tuple"]
                .as_array()
                .unwrap()
                .contains(&serde_json::json!(name)),
            "builder omitted {name}"
        );
        assert!(
            audit["REQUIRED"]["$set"]
                .as_array()
                .unwrap()
                .contains(&serde_json::json!(name)),
            "audit omitted {name}"
        );
    }
}
#[test]
fn retirement_preserves_actual_sdk_clients() {
    let root = root();
    for name in [
        "test_ai_box_native.py",
        "test_ai_plan_native.py",
        "test_ai_v4_native.py",
        "test_ai_v6_review.py",
        "test_ai_v7_native.py",
        "test_ai_v8_native.py",
    ] {
        assert!(
            !root.join("spike").join(name).exists(),
            "retired driver returned: {name}"
        );
    }
    for (name, expected) in [
        (
            "spike/test_ai_box.py",
            "2f5dd67dff026c7d66eb0c087cb06b4d8b2daf2c2b1591578ed4a50f5468e388",
        ),
        (
            "spike/test_ai_plan.py",
            "22118faf226f82505304125327a8c4552bed9e9d9b2f11eecf073c2ccc41b0a3",
        ),
        (
            "spike/test_ai_primitives.py",
            "0a3f355f794f9aa71be94296fec9c5035812e70393843be6efa45b68d52d5b30",
        ),
        (
            "spike/test_ai_accepted_review.py",
            "bb1352551421fcb49f75d010f4971b3173d498bf8712c46cfb3e3b64e89984c5",
        ),
        (
            "spike/test_ai_accepted_review_native.py",
            "2fc2abfb49d4fbc13c47433474665fc09111b9a45b22daf955335209afa7fb73",
        ),
    ] {
        assert_eq!(
            automation_test_driver::hash(&fs::read(root.join(name)).unwrap()),
            expected,
            "SDK client changed: {name}"
        );
    }
}
#[test]
fn shared_gate_keeps_all_six_mutation_suites_mandatory() {
    let text = fs::read_to_string(root().join("run_tests.sh")).unwrap();
    for marker in [
        "Rust automation test-driver format",
        "Rust automation test-driver Clippy",
        "Rust automation test-driver tests and source registration",
        "for suite in box plan v4 v6 v7 v8; do",
        "--mutations",
        "--python \"$PYTHON\"",
        "/var/tmp/narwhal-automation-driver-XXXXXX",
    ] {
        assert!(text.contains(marker), "gate omitted {marker}");
    }
    let catalog = automation_test_driver::execution_catalog();
    assert_eq!(
        ["box", "plan", "v4", "v6", "v7", "v8"]
            .iter()
            .map(|name| catalog[name].as_array().unwrap().len())
            .sum::<usize>(),
        71
    );
}

#[test]
fn documentation_and_license_preserve_tooling_boundary() {
    let root = root();
    let guide = fs::read_to_string(root.join("native/automation_test_driver/README.md")).unwrap();
    for marker in [
        "Linux-only test orchestration",
        "no Windows",
        "71 actual mutations",
        "native_modeling_update_required",
        "not reported as a passing legacy",
    ] {
        assert!(
            guide.contains(marker),
            "guide lost qualification limit: {marker}"
        );
    }
    let manifest =
        fs::read_to_string(root.join("native/automation_test_driver/Cargo.toml")).unwrap();
    for marker in [
        "license = \"GPL-3.0-or-later\"",
        "serde_json = \"=1.0.154\"",
        "sha2 = \"=0.10.9\"",
    ] {
        assert!(
            manifest.contains(marker),
            "manifest lost reviewed metadata: {marker}"
        );
    }
    let contributing = fs::read_to_string(root.join("CONTRIBUTING.md")).unwrap();
    assert!(contributing.contains("native/automation_test_driver/README.md"));
}

#[test]
fn every_source_omission_in_both_native_inventories_is_rejected() {
    let root = root();
    for (file, key, tag) in [
        ("publication.json", "NATIVE_FILES", "$tuple"),
        ("audit.json", "REQUIRED", "$set"),
    ] {
        let original: serde_json::Value = serde_json::from_str(
            &fs::read_to_string(root.join("native/source_tools/src").join(file)).unwrap(),
        )
        .unwrap();
        let complete = |value: &serde_json::Value| {
            REQUIRED_DRIVER_PATHS.iter().all(|p| {
                value[key][tag]
                    .as_array()
                    .unwrap()
                    .contains(&serde_json::json!(p))
            })
        };
        assert!(complete(&original));
        for path in REQUIRED_DRIVER_PATHS {
            let mut mutant = original.clone();
            mutant[key][tag]
                .as_array_mut()
                .unwrap()
                .retain(|v| v != path);
            assert!(!complete(&mutant), "{file} omission survived: {path}");
            assert!(complete(&original));
        }
    }
}
