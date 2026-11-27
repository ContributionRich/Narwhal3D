//! Independent fixed canaries captured before replacement; no executable reference.
use super::*;
pub fn self_check(root: &Path, c: &Value) -> Result<Value, String> {
    let mut out = checks::Checks::default();
    let mut safe: Entries = strings(&c["REQUIRED"])
        .into_iter()
        .map(|p| (p, ("100644".into(), b"safe\n".to_vec())))
        .collect();
    for p in c["FROZEN_ABI_FILES"]
        .as_object()
        .into_iter()
        .flat_map(|m| m.keys())
    {
        safe.insert(
            p.clone(),
            (
                "100644".into(),
                fs::read(root.join(p)).map_err(|e| e.to_string())?,
            ),
        );
    }
    for p in strings(&c["REQUIRED_EXECUTABLES"]) {
        safe.insert(p, ("100755".into(), b"#!/bin/sh\nexit 0\n".to_vec()));
    }
    safe.insert(
        "Build-Narwhal3D-macOS.command".into(),
        (
            "100755".into(),
            ["#!/bin/sh\nexit 0\n"].concat().into_bytes(),
        ),
    );
    safe.insert(
        "native/automation_core/src/approval_lifecycle.rs".into(),
        (
            "100644".into(),
            [
                "pub(crate) fn get(&self, own",
                "er: &Token, call_id: &str) {}\n    let own",
                "er: Token = parse(\"public\");\n    own",
                "er: token.owner,\n",
            ]
            .concat()
            .into_bytes(),
        ),
    );
    safe.insert(
        "narwhal3d.sh".into(),
        (
            "100755".into(),
            ["#!/bin/sh\nexit 0\n"].concat().into_bytes(),
        ),
    );
    safe.insert(
        "uishot.sh".into(),
        (
            "100755".into(),
            ["#!/bin/sh\nexit 0\n"].concat().into_bytes(),
        ),
    );
    safe.insert(
        "spike/fixtures/numparse_golden.json".into(),
        (
            "100644".into(),
            fs::read(root.join("spike/fixtures/numparse_golden.json")).unwrap(),
        ),
    );
    safe.insert(
        "native/automation_core/src/modeling.rs".into(),
        (
            "100644".into(),
            ["pub struct Ticket {\n    pub own", "er: Token,\n}\n"]
                .concat()
                .into_bytes(),
        ),
    );
    safe.insert(
        "native/automation_core/src/primitives.rs".into(),
        (
            "100644".into(),
            ["pub struct Proposal {\n    pub own", "er: Token,\n}\n"]
                .concat()
                .into_bytes(),
        ),
    );
    safe.insert(
        "COMPATIBILITY.md".into(),
        (
            "100644".into(),
            ["Target: Rh", "inoceros 3D\nhttps://www.rh", "ino3d.com/\n"]
                .concat()
                .into_bytes(),
        ),
    );
    safe.insert(
        "native/automation_core/src/conversation.rs".into(),
        (
            "100644".into(),
            ["fn scope(\n    own", "er: &Token,\n) {}\n"]
                .concat()
                .into_bytes(),
        ),
    );
    safe.insert("narwhal3d/commands.py".into(),("100644".into(),["class Command:\n    pass\n\ndef _build_registry():\n    return {}\n\nCOMMANDS: dict[str, Command] = _build_registry()\n"].concat().into_bytes()));
    safe.insert("scripts/create_public_branch.py".into(),("100644".into(),["BRANCH = \"narwhal3d-public\"\nfor directory in (\"narwhal3d\", \"packaging\", \"licenses\", \".github\", \"artwork\"):\n    pass\nROOT_GATE = \"--require-root-commit\"\n"].concat().into_bytes()));
    safe.insert(
        "run_tests.sh".into(),
        (
            "100755".into(),
            ["#!/bin/sh\nexit 0\n"].concat().into_bytes(),
        ),
    );
    safe.insert(
        "native/automation_core/src/export_action.rs".into(),
        (
            "100644".into(),
            ["pub struct Ticket {\n    pub own", "er: Token,\n}\n"]
                .concat()
                .into_bytes(),
        ),
    );
    safe.insert(
        "scripts/build_macos_source.sh".into(),
        (
            "100755".into(),
            ["#!/bin/sh\nexit 0\n"].concat().into_bytes(),
        ),
    );
    safe.insert(
        "native/automation_core/tests/modeling_policy.rs".into(),
        (
            "100644".into(),
            [
                "let forged = Ticket {\n    own",
                "er: Token {\n        conversation: 2,\n    },\n};\n",
            ]
            .concat()
            .into_bytes(),
        ),
    );
    safe.insert(
        "scripts/build_blender_linux.sh".into(),
        (
            "100755".into(),
            ["#!/bin/sh\nexit 0\n"].concat().into_bytes(),
        ),
    );
    safe.insert(
        "scripts/build_readme_demo.sh".into(),
        (
            "100755".into(),
            ["#!/bin/sh\nexit 0\n"].concat().into_bytes(),
        ),
    );
    safe.insert(
        "scripts/bootstrap_dev_linux.sh".into(),
        (
            "100755".into(),
            ["#!/bin/sh\nexit 0\n"].concat().into_bytes(),
        ),
    );
    safe.insert(
        "scripts/setup_dev_native.sh".into(),
        (
            "100755".into(),
            ["#!/bin/sh\nexit 0\n"].concat().into_bytes(),
        ),
    );
    safe.insert(
        "narwhal3d/icons/narwhal-box.png".into(),
        ("100644".into(), vec![137, 80, 78, 71, 13, 10, 26, 10, 0]),
    );
    safe.insert(
        "narwhal3d/cdr_reader.py".into(),
        (
            "100644".into(),
            ["def read_cdr(path):\n    return path\n"]
                .concat()
                .into_bytes(),
        ),
    );
    safe.insert(
        "narwhal3d/cdr_writer.py".into(),
        (
            "100644".into(),
            ["def write_cdr(path):\n    return path\n"]
                .concat()
                .into_bytes(),
        ),
    );
    safe.insert(
        "spike/test_cdr.py".into(),
        (
            "100644".into(),
            ["print('public CDR test')\n"].concat().into_bytes(),
        ),
    );
    safe.insert(
        "dependency-name.txt".into(),
        (
            "100644".into(),
            ["rh", "ino3dm is required\n"].concat().into_bytes(),
        ),
    );
    for (p, markers) in c["REQUIRED_CONTENT"].as_object().unwrap() {
        if safe.get(p).is_some_and(|v| v.1 == b"safe\n") {
            let blob = seq(markers)
                .iter()
                .flat_map(|m| {
                    let mut b = bytes(m);
                    b.push(b'\n');
                    b
                })
                .collect();
            safe.insert(p.clone(), ("100644".into(), blob));
        }
    }
    out.check(
        audit_entries(root, &safe, true, c).is_empty(),
        "complete synthetic public manifest admitted",
    );
    {
        let mut candidate = safe.clone();
        candidate.insert(
            concat!("bug_", "reports/report.md").into(),
            ("100644".into(), ["x"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 1",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "notes.txt".into(),
            ("100644".into(), ["Co", "co CAD"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 2",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "native/picture_trace/Cargo.toml".into(),
            (
                "100644".into(),
                ["members = [\"crates/co", "co-cutline\"]\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 3",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "notes.txt".into(),
            (
                "100644".into(),
                ["/home/n", "athaniel/model.3dm"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 4",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "notes.py".into(),
            (
                "100644".into(),
                ["# Own", "er's words\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 5",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "native/automation_core/src/primitives.rs".into(),
            (
                "100644".into(),
                ["// Own", "er: private notes\n    pub own", "er: Token,\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 6",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "native/automation_core/src/conversation.rs".into(),
            (
                "100644".into(),
                ["const NOTE: &str = \"Own", "er: private\";\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 7",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "native/automation_core/src/primitives.rs".into(),
            (
                "100644".into(),
                ["    pub own", "er: Token, // Own", "er: private\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 8",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "native/automation_core/src/primitives.rs".into(),
            (
                "100644".into(),
                ["    pub own", "er: Token,\n// gh", "p_private\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 9",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "notes.py".into(),
            (
                "100644".into(),
                ["# See docs/KE", "RNEL.md\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 10",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "notes.py".into(),
            (
                "100644".into(),
                ["# G-", "112 implementation\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 11",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "notes.py".into(),
            (
                "100644".into(),
                ["# bug rep", "ort #74\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 12",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "notes.py".into(),
            (
                "100644".into(),
                ["# fron", "tier han", "doff\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 13",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "notes.py".into(),
            (
                "100644".into(),
                ["import rh", "ino_addon\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 14",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "notes.py".into(),
            (
                "100644".into(),
                ["# rh", "ino behavior\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 15",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "narwhal3d/commands.py".into(),
            (
                "100644".into(),
                ["PLACEHOLDER_FOR_SIZE\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 16",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "run_tests.sh".into(),
            ("100644".into(), ["safe\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 17",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "broken.py".into(),
            ("100644".into(), ["def broken(:\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 18",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "shot.png".into(),
            ("100644".into(), ["png"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 19",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "narwhal3d/icons/generated-private-screenshot.png".into(),
            ("100644".into(), ["png"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 20",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            concat!("spike/rhi", "no_help.py").into(),
            ("100644".into(), ["x"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 21",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "escape".into(),
            ("120000".into(), ["../private"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 22",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "legacy/retired-python-old/source.py".into(),
            ("100644".into(), ["safe\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 23",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "app/narwhal3d/legacy/source.py".into(),
            ("100644".into(), ["safe\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 24",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "narwhal3d/retired_loader.py".into(),
            (
                "100644".into(),
                ["from legacy import source\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 25",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "native/automation_core/src/modeling.rs".into(),
            (
                "100644".into(),
                ["// Own", "er: private notes\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 26",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "native/automation_core/src/modeling.rs".into(),
            (
                "100644".into(),
                ["const NOTE: &str = \"Own", "er: private\";\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 27",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "native/automation_core/src/export_action.rs".into(),
            (
                "100644".into(),
                ["// Own", "er: private notes\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 28",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "native/automation_core/src/export_action.rs".into(),
            (
                "100644".into(),
                ["const NOTE: &str = \"Own", "er: private\";\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 29",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "native/automation_core/tests/modeling_policy.rs".into(),
            (
                "100644".into(),
                ["// Own", "er: private notes\n"].concat().into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 30",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "native/automation_core/tests/modeling_policy.rs".into(),
            (
                "100644".into(),
                ["const NOTE: &str = \"Own", "er: private\";\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 31",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "spike/test_ai_box_native.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 32",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "app/spike/test_ai_box_native.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 33",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "spike/test_ai_plan_native.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 34",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "app/spike/test_ai_plan_native.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 35",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "spike/test_ai_v4_native.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 36",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "app/spike/test_ai_v4_native.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 37",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "spike/test_ai_v6_review.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 38",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "app/spike/test_ai_v6_review.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 39",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "spike/test_ai_v7_native.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 40",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "app/spike/test_ai_v7_native.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 41",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "spike/test_ai_v8_native.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 42",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "app/spike/test_ai_v8_native.py".into(),
            ("100644".into(), ["pass\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 43",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "spike/test_ui_core_popup_failure.py".into(),
            (
                "100644".into(),
                ["import test_public_closure_fixture\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        candidate.insert(
            "spike/test_public_closure_fixture.py".into(),
            (
                "100644".into(),
                ["from test_public_closure_leaf import value\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        candidate.insert(
            "spike/test_public_closure_leaf.py".into(),
            ("100644".into(), ["value = 1\n"].concat().into_bytes()),
        );
        out.check(
            audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 207",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "spike/test_ui_core_popup_failure.py".into(),
            (
                "100644".into(),
                ["import test_public_closure_fixture\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        candidate.insert(
            "spike/test_public_closure_leaf.py".into(),
            ("100644".into(), ["value = 1\n"].concat().into_bytes()),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 208",
        );
    }
    {
        let mut candidate = safe.clone();
        candidate.insert(
            "spike/test_ui_core_popup_failure.py".into(),
            (
                "100644".into(),
                ["import test_public_closure_fixture\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        candidate.insert(
            "spike/test_public_closure_fixture.py".into(),
            (
                "100644".into(),
                ["from test_public_closure_leaf import value\n"]
                    .concat()
                    .into_bytes(),
            ),
        );
        out.check(
            !audit_entries(root, &candidate, true, c).is_empty(),
            "fixed audit canary 209",
        );
    }
    for p in strings(&c["REQUIRED"]) {
        let mut candidate = safe.clone();
        candidate.remove(&p);
        out.check(
            audit_entries(root, &candidate, true, c)
                .contains(&format!("missing required public file: {p}")),
            format!("required-file omission: {p}"),
        );
    }
    Ok(out.finish())
}
