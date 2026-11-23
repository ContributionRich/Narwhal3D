//! Main-authorized historical anchor repairs, never a refresh from live source.
use crate::{catalog, hash};
use serde_json::Value;

pub const OVERRIDE_SHA: &str = "7ce8c84275afd01897cc7f2acdebc78798e56ff93aee63299f1408601b1e22b1";
pub const APPLIED_CATALOG_SHA: &str =
    "7f2d5151d8e3265bf7fc3e178cbcca45ffac44f0da79e56c8bb263fbe6fc975e";
const AUDIT: &str = include_str!("anchor_overrides_v1.json");
const REVIEW_PREFIX: &str =
    ".result(&token, &call.id, self.review_clock.now_ms())?;\n                ";

pub fn audit() -> Value {
    assert_eq!(hash(AUDIT.as_bytes()), OVERRIDE_SHA, "override audit drift");
    let v: Value = serde_json::from_str(AUDIT).unwrap();
    assert_eq!(v["version"], 1);
    assert_eq!(v["base"], "bb17715d");
    assert_eq!(v["original_catalog_sha256"], super::catalog::SHA);
    assert_eq!(v["overrides"].as_array().unwrap().len(), 2);
    v
}

/// Only two old/new pairs may differ; labels, files, order, and filters stay frozen.
pub fn applied_catalog() -> Value {
    let original = catalog();
    let mut applied = original.clone();
    let audit = audit();
    for (index, (label, file, filter, legacy_count)) in [
        ("production_catalog", "catalog.rs", "v4_a14", 0),
        ("canonical_result", "conversation.rs", "v4_a12", 2),
    ]
    .into_iter()
    .enumerate()
    {
        let entry = &audit["overrides"][index];
        assert_eq!(entry["label"], label);
        assert_eq!(entry["file"], file);
        assert_eq!(entry["test"], filter);
        assert_eq!(entry["original_count"], legacy_count);
        assert_eq!(entry["applied_count"], 1);
        let row = applied["v4"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|r| r[0] == label)
            .unwrap();
        assert_eq!(row[1], file);
        assert_eq!(row[4], filter);
        assert_eq!(row[2], entry["original_old"]);
        assert_eq!(row[3], entry["original_new"]);
        for (column, key) in [(2, "applied_old"), (3, "applied_new")] {
            let old = row[column].as_str().unwrap();
            let expected = if index == 0 {
                old.replace("pub fn schema", "pub fn frame_schema")
            } else {
                format!("{REVIEW_PREFIX}{old}")
            };
            assert_eq!(entry[key], expected, "unauthorized anchor update");
            row[column] = expected.into();
        }
    }
    let mut differences = 0;
    for suite in ["box", "plan", "v4"] {
        for (before, after) in original[suite]
            .as_array()
            .unwrap()
            .iter()
            .zip(applied[suite].as_array().unwrap())
        {
            if before != after {
                differences += 1;
                assert_eq!(suite, "v4");
                assert!(matches!(
                    before[0].as_str(),
                    Some("production_catalog" | "canonical_result")
                ));
                for c in [0, 1, 4] {
                    assert_eq!(before[c], after[c]);
                }
            }
        }
    }
    assert_eq!(differences, 2);
    assert_eq!(
        hash(&serde_json::to_vec(&applied).unwrap()),
        APPLIED_CATALOG_SHA,
        "applied catalog drift"
    );
    applied
}

/// Anchor failures stay infrastructure errors; they never skip or become kills.
pub fn apply_anchor(text: &str, old: &str, new: &str, unique: bool) -> Result<String, String> {
    let count = text.matches(old).count();
    if count == 0 || (unique && count != 1) {
        return Err(format!(
            "anchor changed: expected {}, found {count}",
            if unique {
                "exactly one"
            } else {
                "at least one"
            }
        ));
    }
    Ok(text.replacen(old, new, 1))
}

pub fn apply_variant(suite: &str, row: &Value, text: &str) -> Result<String, String> {
    if suite == "v4" {
        let a = audit();
        if let Some(entry) = a["overrides"]
            .as_array()
            .unwrap()
            .iter()
            .find(|e| e["label"] == row[0])
        {
            if row[1] != entry["file"]
                || row[2] != entry["applied_old"]
                || row[3] != entry["applied_new"]
                || row[4] != entry["test"]
            {
                return Err("override provenance mismatch".into());
            }
            let count = text
                .matches(entry["original_old"].as_str().unwrap())
                .count();
            if count != entry["original_count"].as_u64().unwrap() as usize {
                return Err(format!("historical source count drift: found {count}"));
            }
        }
    }
    apply_anchor(
        text,
        row[2].as_str().unwrap(),
        row[3].as_str().unwrap(),
        suite != "plan",
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_authorized_catalog_changes() {
        let applied = applied_catalog();
        assert_ne!(applied, catalog());
        assert_eq!(
            hash(super::super::catalog::DATA.as_bytes()),
            "17e701223652104d639fa219305d415eac6bebd4e3aa8286b6efa67734b44845"
        );
    }
    #[test]
    fn historical_anchor_failures_are_infra_and_overrides_are_load_bearing() {
        let original = catalog();
        let applied = applied_catalog();
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../automation_core/src");
        for label in ["production_catalog", "canonical_result"] {
            let old = original["v4"]
                .as_array()
                .unwrap()
                .iter()
                .find(|r| r[0] == label)
                .unwrap();
            let row = applied["v4"]
                .as_array()
                .unwrap()
                .iter()
                .find(|r| r[0] == label)
                .unwrap();
            let text = std::fs::read_to_string(root.join(old[1].as_str().unwrap())).unwrap();
            let legacy_attempt = apply_anchor(
                &text,
                old[2].as_str().unwrap(),
                old[3].as_str().unwrap(),
                true,
            )
            .map(|_| crate::Status::Killed);
            assert!(legacy_attempt.is_err());
            assert_eq!(
                crate::attempt_status(&legacy_attempt),
                crate::Status::InfraFailure
            );
            assert_eq!(
                text.matches(old[2].as_str().unwrap()).count(),
                if label == "production_catalog" { 0 } else { 2 }
            );
            // Same Result error path consumed as INFRA_FAILURE by the native CLI.
            let mut reverted = row.clone();
            reverted[2] = old[2].clone();
            reverted[3] = old[3].clone();
            assert!(apply_variant("v4", &reverted, &text).is_err());
            let changed = apply_variant("v4", row, &text).unwrap();
            assert_ne!(changed, text);
            if label == "canonical_result" {
                let modeling = "if result.output != expected.output || result.is_error != expected.is_error {\n                    return Err(\"modeling_result_mismatch\");";
                assert!(text.contains(modeling));
                assert!(changed.contains(modeling));
                assert!(changed.contains(&format!("{REVIEW_PREFIX}if false {{")));
            }
        }
        assert!(apply_anchor("", "absent", "new", true).is_err());
        assert!(apply_anchor("old old", "old", "new", true).is_err());
        assert_eq!(
            apply_anchor("old old", "old", "new", false).unwrap(),
            "new old"
        );
    }
}
