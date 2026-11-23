use crate::{applied_catalog, hash};
use serde_json::Value;
pub const EXECUTION_CATALOG_SHA: &str =
    "6629b3957fdd7e46af51a88cfdb304d87304f10592eb5425f0633d89a0e145aa";
pub const ADDITIONAL_SHA: &str = "bc47b79ce8148c6e183ac0e50c56915b546bbaab69a099955561e46beac7dd12";
pub const ADDITIONAL_PROVENANCE_SHA: &str =
    "4d9ce750aa65f1ac13a5b69f2c006b6b45abdbe751bad73e0adbecdc06ec7343";
pub const DEFAULT_FIXTURE_TARGET: &str = "/tmp/narwhal3d-ai-sustained-v4";
pub fn extended_suite(suite: &str) -> bool {
    matches!(suite, "v6" | "v7" | "v8")
}
pub fn default_filter(suite: &str) -> &str {
    match suite {
        "box" => "box_action::tests",
        "plan" => "",
        "v6" => "v6_",
        "v7" => "v7_review_clock",
        "v8" => "v8_display_projection",
        _ => "v4_",
    }
}
pub fn always_mutates(suite: &str) -> bool {
    matches!(suite, "box" | "v7" | "v8")
}
pub fn execution_catalog() -> Value {
    let raw = include_str!("additional_catalog_v1.json");
    assert_eq!(
        hash(raw.as_bytes()),
        ADDITIONAL_SHA,
        "additional catalog drift"
    );
    let provenance = include_str!("additional_catalog_provenance_v1.json");
    assert_eq!(
        hash(provenance.as_bytes()),
        ADDITIONAL_PROVENANCE_SHA,
        "additional provenance drift"
    );
    let audit: Value = serde_json::from_str(provenance).unwrap();
    assert_eq!(audit["version"], 1);
    assert_eq!(audit["frozen_original_catalog_sha256"], super::catalog::SHA);
    let extra: Value = serde_json::from_str(raw).unwrap();
    assert_eq!(extra.as_object().unwrap().len(), 3);
    let mut all = applied_catalog();
    for (suite, count) in [("v6", 4), ("v7", 1), ("v8", 2)] {
        assert_eq!(extra[suite].as_array().unwrap().len(), count);
        assert_eq!(audit["defaults"][suite]["filter"], default_filter(suite));
        assert_eq!(audit["defaults"][suite]["memory_mib"], 1024);
        assert_eq!(audit["defaults"][suite]["target"], DEFAULT_FIXTURE_TARGET);
        assert_eq!(audit["defaults"][suite]["mutations"], always_mutates(suite));
        all[suite] = extra[suite].clone();
    }
    let mut labels = std::collections::BTreeSet::new();
    for rows in all.as_object().unwrap().values() {
        for row in rows.as_array().unwrap() {
            assert_eq!(row.as_array().unwrap().len(), 5);
            assert!(labels.insert(row[0].as_str().unwrap()));
        }
    }
    assert_eq!(labels.len(), 71);
    assert_eq!(
        hash(&serde_json::to_vec(&all).unwrap()),
        EXECUTION_CATALOG_SHA,
        "execution catalog drift"
    );
    all
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn additional_catalog_and_defaults() {
        let all = execution_catalog();
        assert_eq!(all["v6"].as_array().unwrap().len(), 4);
        assert!(always_mutates("v7"));
        assert!(always_mutates("v8"));
        assert!(!always_mutates("v6"));
        assert_eq!(default_filter("v7"), "v7_review_clock");
        assert_eq!(default_filter("v8"), "v8_display_projection");
    }
}
