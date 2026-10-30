//! OURS: bounded document-layer inheritance, not viewport/display visibility.
use serde::Deserialize;
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};

const MAX_LAYERS: usize = 512;
const MAX_TARGETS: usize = 32;
const MAX_ANCESTORS: usize = 64;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LayerRow {
    pub id: String,
    pub parent_id: Option<String>,
    pub visible: bool,
    pub locked: bool,
}

/// Compute inherited flags from supplied document metadata only.
///
/// UUID identity is case-insensitive; output keys retain target spelling. Repeated
/// identical targets share one object entry. Roots have `parent_id: None`; this
/// function does not interpret a nil UUID as a root marker.
///
/// Input limits, invalid UUIDs and duplicate layer identities reject the entire
/// call. Missing targets/parents, cycles and paths exceeding 64 ancestors produce
/// an unavailable entry for that target. The target itself is not an ancestor.
/// No short circuit on hidden/locked flags may conceal a broken parent chain.
pub fn layer_flags(layers: Vec<LayerRow>, targets: Vec<String>) -> Result<Value, &'static str> {
    if layers.len() > MAX_LAYERS {
        return Err("too_many_layers");
    }
    if targets.len() > MAX_TARGETS {
        return Err("too_many_targets");
    }
    let mut index = BTreeMap::new();
    for mut layer in layers {
        if !crate::catalog::uuid(&layer.id) {
            return Err("invalid_layer_id");
        }
        if layer
            .parent_id
            .as_deref()
            .is_some_and(|id| !crate::catalog::uuid(id))
        {
            return Err("invalid_parent_id");
        }
        layer.id.make_ascii_lowercase();
        if let Some(parent) = &mut layer.parent_id {
            parent.make_ascii_lowercase();
        }
        if index.insert(layer.id.clone(), layer).is_some() {
            return Err("duplicate_layer_id");
        }
    }
    if targets.iter().any(|id| !crate::catalog::uuid(id)) {
        return Err("invalid_target_id");
    }
    let mut result = Map::new();
    for target in targets {
        let flags = match inherited_flags(&index, &target.to_ascii_lowercase()) {
            Ok((visible, locked)) => json!({
                "available": true,
                "visible": visible,
                "locked": locked,
                "source": "document_layer_parent_chain",
            }),
            Err(reason) => json!({"available": false, "reason": reason}),
        };
        result.insert(target, flags);
    }
    Ok(Value::Object(result))
}

fn inherited_flags(
    index: &BTreeMap<String, LayerRow>,
    target: &str,
) -> Result<(bool, bool), &'static str> {
    let mut current = target;
    let mut visited = BTreeSet::new();
    let mut ancestors = 0;
    let mut visible = true;
    let mut locked = false;
    loop {
        if !visited.insert(current) {
            return Err("cycle");
        }
        let layer = index.get(current).ok_or(if ancestors == 0 {
            "unknown_target"
        } else {
            "missing_parent"
        })?;
        visible &= layer.visible;
        locked |= layer.locked;
        let Some(parent) = layer.parent_id.as_deref() else {
            return Ok((visible, locked));
        };
        if ancestors == MAX_ANCESTORS {
            return Err("depth_limit");
        }
        ancestors += 1;
        current = parent;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ROOT: &str = "aaaaaaaa-0000-0000-0000-000000000001";
    const CHILD: &str = "bbbbbbbb-0000-0000-0000-000000000002";
    const SIBLING: &str = "cccccccc-0000-0000-0000-000000000003";
    const LEAF: &str = "dddddddd-0000-0000-0000-000000000004";
    const MISSING: &str = "eeeeeeee-0000-0000-0000-000000000005";

    fn row(id: &str, parent: Option<&str>, visible: bool, locked: bool) -> LayerRow {
        LayerRow {
            id: id.into(),
            parent_id: parent.map(str::to_owned),
            visible,
            locked,
        }
    }

    fn targets(ids: &[&str]) -> Vec<String> {
        ids.iter().map(|id| (*id).into()).collect()
    }

    #[test]
    fn literal_hierarchy_inherits_hidden_and_locked_ancestors() {
        let result = layer_flags(
            vec![
                row(LEAF, Some(CHILD), true, false),
                row(CHILD, Some(ROOT), true, true),
                row(ROOT, None, false, false),
            ],
            targets(&[LEAF, CHILD, ROOT]),
        )
        .unwrap();
        assert_eq!(
            result,
            json!({
                "dddddddd-0000-0000-0000-000000000004": {
                    "available": true, "visible": false, "locked": true,
                    "source": "document_layer_parent_chain"
                },
                "bbbbbbbb-0000-0000-0000-000000000002": {
                    "available": true, "visible": false, "locked": true,
                    "source": "document_layer_parent_chain"
                },
                "aaaaaaaa-0000-0000-0000-000000000001": {
                    "available": true, "visible": false, "locked": false,
                    "source": "document_layer_parent_chain"
                }
            })
        );
    }

    #[test]
    fn siblings_do_not_leak_local_flags_or_accumulators() {
        let result = layer_flags(
            vec![
                row(ROOT, None, true, false),
                row(CHILD, Some(ROOT), false, true),
                row(SIBLING, Some(ROOT), true, false),
            ],
            targets(&[CHILD, SIBLING, ROOT]),
        )
        .unwrap();
        assert_eq!(
            result[CHILD],
            json!({
                "available": true, "visible": false, "locked": true,
                "source": "document_layer_parent_chain"
            })
        );
        let unaffected = json!({
            "available": true, "visible": true, "locked": false,
            "source": "document_layer_parent_chain"
        });
        assert_eq!(result[SIBLING], unaffected);
        assert_eq!(result[ROOT], unaffected);
    }

    #[test]
    fn unknown_target_and_missing_parent_are_distinct_and_per_target() {
        let result = layer_flags(
            vec![
                row(ROOT, None, true, false),
                row(CHILD, Some(MISSING), false, true),
            ],
            targets(&[MISSING, CHILD, ROOT]),
        )
        .unwrap();
        assert_eq!(
            result[MISSING],
            json!({"available": false, "reason": "unknown_target"})
        );
        // Already hidden AND locked is not permission to skip a missing ancestor.
        assert_eq!(
            result[CHILD],
            json!({"available": false, "reason": "missing_parent"})
        );
        assert_eq!(
            result[ROOT],
            json!({
                "available": true, "visible": true, "locked": false,
                "source": "document_layer_parent_chain"
            })
        );
    }

    #[test]
    fn self_cycle_and_cycle_above_target_have_no_default_flags() {
        let result = layer_flags(
            vec![
                row(ROOT, Some(ROOT), false, true),
                row(CHILD, Some(SIBLING), true, false),
                row(SIBLING, Some(CHILD), true, false),
                row(LEAF, Some(CHILD), true, false),
            ],
            targets(&[ROOT, CHILD, LEAF]),
        )
        .unwrap();
        for id in [ROOT, CHILD, LEAF] {
            assert_eq!(result[id], json!({"available": false, "reason": "cycle"}));
        }
    }

    fn numbered_id(n: usize) -> String {
        format!("00000000-0000-0000-0000-{n:012x}")
    }

    fn chain(count: usize) -> Vec<LayerRow> {
        (1..=count)
            .map(|n| LayerRow {
                id: numbered_id(n),
                parent_id: (n > 1).then(|| numbered_id(n - 1)),
                visible: n != 1,
                locked: n == 1,
            })
            .collect()
    }

    #[test]
    fn exactly_64_ancestors_succeed_but_65_are_unavailable() {
        // Literal sizes intentionally do not depend on MAX_ANCESTORS.
        let at_limit = numbered_id(65);
        let too_deep = numbered_id(66);
        let result = layer_flags(chain(66), vec![at_limit.clone(), too_deep.clone()]).unwrap();
        assert_eq!(
            result[&at_limit],
            json!({
                "available": true, "visible": false, "locked": true,
                "source": "document_layer_parent_chain"
            })
        );
        assert_eq!(
            result[&too_deep],
            json!({"available": false, "reason": "depth_limit"})
        );
    }

    #[test]
    fn layer_and_target_limits_are_inclusive_and_checked_before_deduplication() {
        let rows = (1..=512)
            .map(|n| row(&numbered_id(n), None, true, false))
            .collect();
        let ids: Vec<String> = (1..=32).map(numbered_id).collect();
        let result = layer_flags(rows, ids).unwrap();
        assert_eq!(result.as_object().unwrap().len(), 32);
        assert_eq!(layer_flags(chain(513), vec![]), Err("too_many_layers"));
        assert_eq!(
            layer_flags(vec![], vec![ROOT.into(); 33]),
            Err("too_many_targets")
        );
    }

    #[test]
    fn duplicate_layer_ids_are_refused_even_without_targets() {
        assert_eq!(
            layer_flags(
                vec![row(ROOT, None, true, false), row(ROOT, None, false, true)],
                vec![],
            ),
            Err("duplicate_layer_id")
        );
        assert_eq!(
            layer_flags(
                vec![
                    row(ROOT, None, true, false),
                    row(&ROOT.to_ascii_uppercase(), None, true, false)
                ],
                vec![],
            ),
            Err("duplicate_layer_id")
        );
    }

    #[test]
    fn uuid_identity_is_case_insensitive_but_target_keys_are_preserved() {
        let upper_root = ROOT.to_ascii_uppercase();
        let upper_child = CHILD.to_ascii_uppercase();
        let result = layer_flags(
            vec![
                row(&upper_root, None, false, true),
                row(CHILD, Some(ROOT), true, false),
            ],
            vec![upper_child.clone(), CHILD.into()],
        )
        .unwrap();
        assert_eq!(result.as_object().unwrap().len(), 2);
        for id in [&upper_child[..], CHILD] {
            assert_eq!(
                result[id],
                json!({
                    "available": true, "visible": false, "locked": true,
                    "source": "document_layer_parent_chain"
                })
            );
        }
    }

    #[test]
    fn invalid_ids_reject_the_call_including_unreferenced_rows() {
        for invalid in [
            "",
            "not-a-uuid",
            "aaaaaaaa0000-0000-0000-000000000001",
            "gaaaaaaa-0000-0000-0000-000000000001",
        ] {
            assert_eq!(
                layer_flags(vec![row(invalid, None, true, false)], vec![]),
                Err("invalid_layer_id")
            );
            assert_eq!(
                layer_flags(vec![row(ROOT, Some(invalid), true, false)], vec![]),
                Err("invalid_parent_id")
            );
            assert_eq!(
                layer_flags(vec![], vec![invalid.into()]),
                Err("invalid_target_id")
            );
        }
    }

    #[test]
    fn nil_parent_is_not_silently_treated_as_root() {
        let result = layer_flags(
            vec![row(
                ROOT,
                Some("00000000-0000-0000-0000-000000000000"),
                true,
                false,
            )],
            targets(&[ROOT]),
        )
        .unwrap();
        assert_eq!(
            result[ROOT],
            json!({"available": false, "reason": "missing_parent"})
        );
    }

    #[test]
    fn empty_and_repeated_targets_have_object_results() {
        assert_eq!(layer_flags(vec![], vec![]), Ok(json!({})));
        let result = layer_flags(vec![], targets(&[ROOT, ROOT])).unwrap();
        assert_eq!(
            result,
            json!({
                "aaaaaaaa-0000-0000-0000-000000000001": {
                    "available": false, "reason": "unknown_target"
                }
            })
        );
    }

    #[test]
    fn deserialize_accepts_only_declared_fields_and_safe_scalar_types() {
        let valid = json!({"id": ROOT, "parent_id": null, "visible": true, "locked": false});
        let decoded: LayerRow = serde_json::from_value(valid.clone()).unwrap();
        assert_eq!(decoded.id, ROOT);
        assert_eq!(decoded.parent_id, None);
        assert!(decoded.visible);
        assert!(!decoded.locked);
        for field in ["visible", "locked"] {
            for bad in [
                json!(0),
                json!(1),
                json!("true"),
                Value::Null,
                json!([]),
                json!({}),
            ] {
                let mut input = valid.clone();
                input[field] = bad;
                assert!(serde_json::from_value::<LayerRow>(input).is_err());
            }
        }
        for field in ["id", "parent_id"] {
            for bad in [json!(1), json!(true), json!([]), json!({})] {
                let mut input = valid.clone();
                input[field] = bad;
                assert!(serde_json::from_value::<LayerRow>(input).is_err());
            }
        }
        for field in ["id", "visible", "locked"] {
            let mut input = valid.clone();
            input.as_object_mut().unwrap().remove(field);
            assert!(serde_json::from_value::<LayerRow>(input).is_err());
        }
        let mut input = valid.clone();
        input["id"] = Value::Null;
        assert!(serde_json::from_value::<LayerRow>(input).is_err());
        let mut input = valid;
        input["display_visible"] = json!(true);
        assert!(serde_json::from_value::<LayerRow>(input).is_err());
        assert!(serde_json::from_str::<LayerRow>(
            r#"{"id":"aaaaaaaa-0000-0000-0000-000000000001","parent_id":null,"visible":true,"visible":false,"locked":false}"#
        ).is_err());
    }
}
