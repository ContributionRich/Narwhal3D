//! Native source-policy assertions; compiler AST inspection, never Python evaluation.
use crate::*;
include!("policy_sets.rs");
fn source(root: &Path, inputs: &Value, path: &str) -> Result<String, String> {
    inputs
        .get(path)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .map(Ok)
        .unwrap_or_else(|| fs::read_to_string(root.join(path)).map_err(|e| format!("{path}: {e}")))
}
fn nodes(v: &Value) -> Vec<&Value> {
    let mut out = Vec::new();
    python_sdk::walk(v, &mut out);
    out
}
fn tree(root: &Path, inputs: &Value, path: &str) -> Result<Value, String> {
    python_sdk::parse(root, source(root, inputs, path)?.as_bytes(), path)
}
fn named<'a>(tree: &'a Value, kind: &str, name: &str) -> Result<&'a Value, String> {
    tree["body"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|n| n["_type"] == kind && n["name"] == name)
        .ok_or_else(|| format!("missing {kind} {name}"))
}
fn lit(v: &Value) -> Result<Value, String> {
    Ok(match v["_type"].as_str().unwrap_or("") {
        "Constant" => v["value"].clone(),
        "Tuple" | "List" | "Set" => Value::Array(
            v["elts"]
                .as_array()
                .ok_or("literal items missing")?
                .iter()
                .map(lit)
                .collect::<Result<_, _>>()?,
        ),
        _ => return Err("not a reviewed literal".into()),
    })
}
fn assignment(tree: &Value, name: &str) -> Result<Value, String> {
    let v = tree["body"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|n| {
            n["_type"] == "Assign"
                && n["targets"]
                    .as_array()
                    .is_some_and(|a| a.iter().any(|t| t["_type"] == "Name" && t["id"] == name))
        })
        .ok_or_else(|| format!("assignment {name} missing"))?;
    lit(&v["value"])
}
pub fn inputs(root: &Path, name: &str) -> Result<Vec<String>, String> {
    let mut p = match name {
        "product_has_no_retired_engine_or_archive_resolver" => {
            let mut p = PRODUCT_FILES
                .iter()
                .map(|n| format!("narwhal3d/{n}.py"))
                .collect::<Vec<_>>();
            p.extend(
                [
                    "test_rust_history_abi",
                    "test_rust_history_profile",
                    "test_ui_core_editor",
                ]
                .map(|n| format!("spike/{n}.py")),
            );
            for path in recursive_files(root, "narwhal3d", &config())? {
                if path.ends_with(".py") {
                    p.push(path)
                }
            }
            p
        }
        "history_acceptance_is_native_and_host_replay_is_retained" => vec![
            "native/application_core/tests/data/history30_trace.json".into(),
            "spike/test_rust_history_abi.py".into(),
        ],
        "blend_host_has_no_embedded_numeric_reference" => vec!["spike/test_native_blend.py".into()],
        "reference_decoders_cannot_restore_retired_engines" => {
            ["svgpath", "calc", "coords", "macro", "macro_plan"]
                .map(|n| format!("spike/{n}_reference.py"))
                .to_vec()
        }
        "width_format_macro_python_engines_are_retired" => FORBIDDEN_ENGINES
            .iter()
            .map(|(n, _)| format!("narwhal3d/{n}.py"))
            .collect(),
        "string_planner_does_not_replay_public_token_builder" => {
            vec!["narwhal3d/macro_plan.py".into()]
        }
        "admission_cannot_return_optional_backend" => [
            "native_document_session",
            "native_history",
            "native_textfield",
            "native_popup",
            "native_transcript",
            "native_pure",
            "undo",
        ]
        .map(|n| format!("narwhal3d/{n}.py"))
        .to_vec(),
        "finished_lane_public_dependencies" => vec!["scripts/build_linux_native.py".into()],
        "ui_documentation_describes_mandatory_native_policy" => {
            vec!["native/ui_core/README.md".into()]
        }
        "finished_lane_harness_order_and_modes" => vec![
            "run_tests.sh".into(),
            "spike/test_ai_accepted_review_native.py".into(),
        ],
        "required_cores_precede_blender_callers" => vec!["run_tests.sh".into()],
        "retired_public_oracles_are_data_and_rust_only" => Vec::new(),
        _ => return Err(format!("unknown native policy case: {name}")),
    };
    p.sort();
    p.dedup();
    Ok(p)
}
pub fn check(root: &Path, c: &Value, name: &str, files: &Value) -> Result<Value, String> {
    let mut out = checks::Checks::default();
    let selected: BTreeSet<_> = ["NATIVE_FILES", "TEST_FILES", "LAUNCH_FILES"]
        .iter()
        .flat_map(|k| strings(&c[*k]))
        .collect();
    let required: BTreeSet<_> = strings(&c["REQUIRED"]).into_iter().collect();
    let mut check = |ok, label: &str| out.check(ok, label);
    match name {
        "product_has_no_retired_engine_or_archive_resolver" => {
            for path in inputs(root, name)? {
                let blob = source(root, files, &path)?;
                if PRODUCT_FILES
                    .iter()
                    .any(|n| path == format!("narwhal3d/{n}.py"))
                    || path.starts_with("spike/")
                {
                    let t = tree(root, files, &path)?;
                    check(
                        !nodes(&t).iter().any(|n| {
                            let key = match n["_type"].as_str() {
                                Some("Name") => "id",
                                Some("FunctionDef" | "ClassDef") => "name",
                                Some("Attribute") => "attr",
                                _ => return false,
                            };
                            n[key].as_str().is_some_and(|s| RETIRED.contains(&s))
                        }),
                        &format!("no retired definitions/calls: {path}"),
                    );
                }
                check(
                    runtime_archive_problem(root, blob.as_bytes()).is_none(),
                    &format!("no archive resolver: {path}"),
                );
            }
            let mut paths = Vec::new();
            recurse(root, &root.join("narwhal3d"), &mut paths, false)?;
            check(
                paths.iter().all(|p| {
                    retirement_path_problem(&p.strip_prefix(root).unwrap().to_string_lossy())
                        .is_none()
                }),
                "no live archive paths",
            );
            check(
                !root.join("spike/fixtures/textfield_168212d4.py").exists(),
                "retired textfield reference absent",
            );
        }
        "retired_public_oracles_are_data_and_rust_only" => {
            for n in [
                "alias_reference",
                "units_reference",
                "interpolate_reference",
                "test_interpolate_math_owned",
                "bearing_reference",
                "unitformat_reference",
                "joinchain_reference",
                "rebuild_reference",
                "test_python_norm_owned",
                "test_rust_history_trace",
            ] {
                check(
                    !root.join(format!("spike/{n}.py")).exists(),
                    &format!("retired oracle absent: {n}"),
                );
            }
            for n in [
                "alias",
                "unit",
                "interpolate",
                "svgpath",
                "calc",
                "coords",
                "bearing",
                "unitformat",
                "macro",
                "joinchain",
                "rebuild",
                "blend",
                "norm",
                "nurbs",
                "arclength",
            ] {
                check(
                    strings(&c["NATIVE_FILES"])
                        .contains(&format!("native/pure_core/tests/{n}_golden.rs")),
                    &format!("Rust golden selected: {n}"),
                );
            }
        }
        "history_acceptance_is_native_and_host_replay_is_retained" => {
            for p in [
                "native/application_core/tests/history30_golden.rs",
                "native/application_core/tests/data/history30_trace.json",
                "native/application_core/tests/data/history30_trace.provenance.json",
            ] {
                check(selected.contains(p) && required.contains(p), p)
            }
            let d: Value = serde_json::from_str(&source(
                root,
                files,
                "native/application_core/tests/data/history30_trace.json",
            )?)
            .map_err(|e| e.to_string())?;
            check(
                d.as_array().is_some_and(|a| a.len() == 350),
                "350 original history cases",
            );
            check(
                source(root, files, "spike/test_rust_history_abi.py")?
                    .contains("history30_trace.json"),
                "actual host history replay retained",
            );
        }
        "blend_host_has_no_embedded_numeric_reference" => {
            let t = tree(root, files, "spike/test_native_blend.py")?;
            check(
                !nodes(&t).iter().any(|n| {
                    n["_type"] == "FunctionDef"
                        && [
                            "_reference_blend",
                            "_reference_blend_curves",
                            "bernstein_deriv_0",
                            "bernstein_deriv_1",
                            "rand_derivs",
                        ]
                        .contains(&n["name"].as_str().unwrap_or(""))
                }),
                "no numeric blend reference",
            );
        }
        "reference_decoders_cannot_restore_retired_engines" => {
            let forbidden = [
                "_tokenize",
                "_parse_expr",
                "_parse_atom",
                "_arc_to_cubics",
                "_split_tokens",
                "_classify",
                "_prefixes",
                "_command_action",
                "build_plan",
                "evaluate",
                "parse_scalar",
                "parse_angle",
                "_parse_polar",
                "_bearing_angle",
                "_angle_degrees",
                "_POLAR_RE",
            ];
            for p in inputs(root, name)? {
                let t = tree(root, files, &p)?;
                check(
                    !nodes(&t).iter().any(|n| {
                        ["FunctionDef", "ClassDef"].contains(&n["_type"].as_str().unwrap_or(""))
                            && forbidden.contains(&n["name"].as_str().unwrap_or(""))
                    }),
                    &format!("no retired decoder definitions: {p}"),
                );
                check(
                    !nodes(&t).iter().any(|n| {
                        n["_type"] == "Import"
                            && n["names"].as_array().is_some_and(|a| {
                                a.iter().any(|n| {
                                    ["re", "math", "decimal"]
                                        .contains(&n["name"].as_str().unwrap_or(""))
                                })
                            })
                    }),
                    &format!("no parser imports: {p}"),
                );
                check(
                    runtime_archive_problem(root, source(root, files, &p)?.as_bytes()).is_none(),
                    &format!("no decoder archive: {p}"),
                );
            }
        }
        "width_format_macro_python_engines_are_retired" => {
            for (module, names) in FORBIDDEN_ENGINES {
                let p = format!("narwhal3d/{module}.py");
                let t = tree(root, files, &p)?;
                check(
                    !nodes(&t).iter().any(|n| match n["_type"].as_str() {
                        Some("Name") => names.contains(&n["id"].as_str().unwrap_or("")),
                        Some("FunctionDef") => names.contains(&n["name"].as_str().unwrap_or("")),
                        _ => false,
                    }),
                    &format!("no retired computation: {module}"),
                );
                check(
                    !source(root, files, &p)?.contains("legacy/"),
                    &format!("no archive literal: {module}"),
                );
            }
        }
        "string_planner_does_not_replay_public_token_builder" => {
            let t = tree(root, files, "narwhal3d/macro_plan.py")?;
            check(
                named(&t, "FunctionDef", "_native_macro_plan").is_err(),
                "no alternate macro planner",
            );
            let f = named(&t, "FunctionDef", "plan_macro")?;
            check(
                !nodes(f).iter().any(|n| {
                    n["_type"] == "Name"
                        && ["tokenize", "build_plan", "_native_macro_plan"]
                            .contains(&n["id"].as_str().unwrap_or(""))
                }),
                "string planner does not replay token builder",
            );
            check(
                !t["body"].as_array().unwrap().iter().any(|n| {
                    n["_type"] == "Assign"
                        && n["targets"].as_array().is_some_and(|a| {
                            a.iter().any(|n| {
                                n["_type"] == "Name"
                                    && n["id"]
                                        .as_str()
                                        .unwrap_or("")
                                        .starts_with("NATIVE_MACRO_PLAN_")
                            })
                        })
                }),
                "no retired planner thresholds",
            );
        }
        "admission_cannot_return_optional_backend" => {
            for (module, functions) in [
                ("native_document_session", &["load_library"][..]),
                ("native_history", &["try_create", "_configure"]),
                ("native_textfield", &["library"]),
                ("native_popup", &["library", "model"]),
                ("native_transcript", &["library"]),
                ("native_pure", &["_required_parse_binding"]),
            ] {
                let t = tree(root, files, &format!("narwhal3d/{module}.py"))?;
                for function in functions {
                    let f = named(&t, "FunctionDef", function)?;
                    check(
                        !nodes(f).iter().any(|n| {
                            n["_type"] == "Return"
                                && (n["value"].is_null()
                                    || (n["value"]["_type"] == "Constant"
                                        && n["value"]["value"].is_null()))
                        }),
                        &format!("mandatory native admission: {module}.{function}"),
                    );
                }
            }
            let t = tree(root, files, "narwhal3d/undo.py")?;
            let f = named(
                named(&t, "ClassDef", "UndoStack")?,
                "FunctionDef",
                "__init__",
            )?;
            let a = f["body"]
                .as_array()
                .unwrap()
                .iter()
                .find(|n| {
                    n["_type"] == "Assign"
                        && n["targets"].as_array().is_some_and(|a| {
                            a.iter()
                                .any(|n| n["_type"] == "Attribute" && n["attr"] == "_backend")
                        })
                })
                .ok_or("native backend assignment missing")?;
            let v = &a["value"];
            check(
                v["_type"] == "Call"
                    && v["func"]["_type"] == "Name"
                    && v["func"]["id"] == "_native_backend"
                    && v["args"].as_array().is_some_and(|a| {
                        a.len() == 1 && a[0]["_type"] == "Name" && a[0]["id"] == "limit"
                    })
                    && v["keywords"].as_array().is_some_and(Vec::is_empty),
                "UndoStack requires native backend",
            );
        }
        "finished_lane_public_dependencies" => {
            let t = tree(root, files, "scripts/build_linux_native.py")?;
            let exports = assignment(&t, "UI_CORE_EXPORTS")?;
            let sources = assignment(&t, "UI_CORE_REQUIRED_SOURCE")?;
            check(
                exports.as_array().is_some_and(|a| a.len() == 20),
                "20 UI exports",
            );
            check(
                sources.as_array().is_some_and(|a| a.len() == 43),
                "43 required UI sources",
            );
            for p in strings(&sources) {
                let p = format!("native/ui_core/{p}");
                check(selected.contains(&p) && required.contains(&p), &p)
            }
            for stem in [
                "abi",
                "model",
                "consumers",
                "stress",
                "gui",
                "unicode",
                "mutations",
                "benchmark",
            ] {
                let p = format!("spike/test_ui_transcript_{stem}.py");
                check(selected.contains(&p) && required.contains(&p), &p)
            }
            for stem in [
                "exit",
                "reachability",
                "presentation",
                "long_session",
                "plans",
                "lifetime",
                "lifetime_gui",
                "settings",
                "visible",
                "x11",
            ] {
                let p = format!("spike/test_ai_v8_{stem}.py");
                check(selected.contains(&p) && required.contains(&p), &p)
            }
            for file in [
                "Cargo.lock",
                "Cargo.toml",
                "README.md",
                "src/additional.rs",
                "src/additional_catalog_provenance_v1.json",
                "src/additional_catalog_v1.json",
                "src/anchor_overrides_v1.json",
                "src/catalog.json",
                "src/catalog.rs",
                "src/discovery.rs",
                "src/fixtures.rs",
                "src/lib.rs",
                "src/main.rs",
                "src/overrides.rs",
                "tests/cli.rs",
                "tests/registration.rs",
            ] {
                let p = format!("native/automation_test_driver/{file}");
                check(selected.contains(&p) && required.contains(&p), &p)
            }
            for p in [
                "launch/check_native.py",
                "spike/test_desktop_native_startup.py",
                "spike/test_numparse_owned.py",
                "spike/test_pure_core_packaging.py",
            ] {
                check(selected.contains(p) && required.contains(p), p)
            }
        }
        "ui_documentation_describes_mandatory_native_policy" => {
            let s = source(root, files, "native/ui_core/README.md")?;
            for p in [
                "Editor, popup and transcript require native support",
                "all 20 editor/popup/transcript/content exports",
                "invalidates an older source-pinned receipt",
            ] {
                check(s.contains(p), p)
            }
            for p in [
                "complete original Python implementation remains the default",
                "use one-way atomic legacy handover",
                "Other Unicode versions use a named complete legacy path",
                "Python hands over oversized text operations",
            ] {
                check(!s.contains(p), p)
            }
        }
        "finished_lane_harness_order_and_modes" => {
            let s = source(root, files, "run_tests.sh")?;
            let pos = |p: &str| {
                s.find(p)
                    .ok_or_else(|| format!("harness marker missing: {p}"))
            };
            let ui = pos("# UI core source qualification")?;
            for marker in [
                "scripts/source_tools.sh pure-gate",
                "scripts/check_application_core.py --stage-dev",
                "export NARWHAL_PURE_CORE=",
                "export NARWHAL_APPLICATION_CORE=",
            ] {
                check(pos(marker)? < ui, marker)
            }
            let allowed = [
                r#"run "document observer source gate" "$PYTHON" -B scripts/check_document_observer.py --stage-dev --mutations"#,
                r#"run "live document registry mutations" "$PYTHON" -B spike/test_document_registry_model.py --mutations"#,
                r#"run "AI canonical publisher races and mutations" "$PYTHON" -B spike/test_ai_v9_races.py --mutations"#,
                r#"--python spike/test_pick_click_local.py -- --mutations"#,
                r#""$AUTOMATION_DRIVER_TARGET/debug/automation-test-driver" --suite "$suite" --mutations \"#,
            ];
            for line in s.lines().filter(|l| l.contains("--mutations")) {
                check(
                    allowed.contains(&line.trim()),
                    "only reviewed isolated mutation drivers",
                )
            }
            for marker in [
                "for suite in box plan v4 v6 v7 v8; do",
                "for suite in abi model consumers stress;",
                "spike/test_desktop_native_startup.py",
                "spike/test_ai_v8_exit.py",
                "spike/test_ai_v8_long_session.py",
                "for close_case in close_review close_request;",
                r#"--case "$close_case" --ui-library "$UI_CORE_LIBRARY" --ui-sha256 "$UI_CORE_SHA256""#,
            ] {
                check(s.contains(marker), marker)
            }
            let end = pos("# End UI core GUI drivers.")?;
            check(
                pos("spike/test_ui_transcript_gui.py --launch")? > end,
                "transcript GUI follows UI drivers",
            );
            check(
                s[end..]
                    .find("scripts/check_ui_core.py --library")
                    .is_some_and(|i| end + i < pos("for close_case").unwrap_or(0)),
                "UI admission precedes close cases",
            );
            let t = tree(root, files, "spike/test_ai_accepted_review_native.py")?;
            let a = t["body"]
                .as_array()
                .unwrap()
                .iter()
                .find(|n| {
                    n["_type"] == "Assign"
                        && n["targets"]
                            .as_array()
                            .is_some_and(|a| a.iter().any(|n| n["id"] == "TARGET"))
                })
                .ok_or("AI review target missing")?;
            let target = a["value"]["args"][0]["value"]
                .as_str()
                .ok_or("AI review target not a literal")?;
            check(
                s.contains(&format!("AI_REVIEW_TARGET=\"{target}\"")),
                "AI driver actual fixture target",
            );
            check(
                !s.contains("test_ui_transcript_mutations.py")
                    && !s.contains("spike/test_ai_v8_native.py"),
                "retired drivers absent",
            );
        }
        "required_cores_precede_blender_callers" => {
            let s = source(root, files, "run_tests.sh")?;
            let first = s
                .find("run \"Rust selection adapter/UI\"")
                .ok_or("selection adapter marker missing")?;
            for m in [
                "export NARWHAL_UI_CORE_LIBRARY=",
                "export NARWHAL_APPLICATION_CORE=",
                "export NARWHAL_HISTORY_CORE=",
            ] {
                check(s.find(m).is_some_and(|i| i < first), m)
            }
            check(
                s.matches("scripts/check_application_core.py --stage-dev")
                    .count()
                    == 1,
                "single application core admission",
            );
            check(
                s.matches("spike/test_native_only_policy.py").count() == 1,
                "single host/native policy suite",
            );
        }
        _ => return Err(format!("unknown native policy case: {name}")),
    }
    Ok(out.finish())
}
