use narwhal_source_tools::*;
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::atomic::{AtomicU64, Ordering},
};
fn root() -> PathBuf {
    if let Some(p) = std::env::var_os("NARWHAL_SOURCE_TEST_ROOT") {
        return p.into();
    }
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf()
}
struct Scratch(PathBuf);
impl Scratch {
    fn new() -> Self {
        static N: AtomicU64 = AtomicU64::new(0);
        let p = std::env::temp_dir().join(format!(
            "source-tools-contract-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
    fn put(&self, name: &str, data: &[u8]) {
        let p = self.0.join(name);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, data).unwrap()
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap()
    }
}
fn one(path: &str, data: &[u8]) -> Entries {
    Entries::from([(path.into(), ("100644".into(), data.to_vec()))])
}
fn audit(path: &str, data: &[u8]) -> Vec<String> {
    audit_entries(&root(), &one(path, data), false, &config())
}
#[test]
fn compiler_and_unicode() {
    let r = root();
    assert!(python_sdk::validate(&r, b"return 1\n", "scope.py").is_err());
    assert!(python_sdk::parse(&r, b"return 1\n", "scope.py").is_ok());
    assert!(python_sdk::validate(&r, b"# coding: latin1\nx = '\xe9'\n", "latin.py").is_ok());
    assert!(audit("broken.py", b"def broken(:\n")
        .iter()
        .any(|e| e.contains("does not compile")));
    assert!(python_sdk::parse(&r, b"x=1\0", "nul.py").is_err())
}
#[test]
fn privacy_secrets_and_modes() {
    for text in [
        ["Ow", "ner: private"].concat(),
        ["gh", "p_private"].concat(),
        ["github_", "pat_private"].concat(),
        ["AK", "IAprivate"].concat(),
        ["-----BEGIN PRIVATE ", "KEY-----"].concat(),
        ["G-", "112 private"].concat(),
        ["rhi", "no private"].concat(),
    ] {
        assert!(!audit("notes.txt", text.as_bytes()).is_empty(), "{text}")
    }
    assert!(audit("notes.txt", b"normal public text").is_empty());
    let mut entries = one("run_tests.sh", b"#!/bin/sh\n");
    assert!(audit_entries(&root(), &entries, false, &config())
        .iter()
        .any(|e| e.contains("not executable")));
    entries.get_mut("run_tests.sh").unwrap().0 = "100755".into();
    assert!(audit_entries(&root(), &entries, false, &config()).is_empty());
    entries = one("escape", b"../private");
    entries.get_mut("escape").unwrap().0 = "120000".into();
    assert!(audit_entries(&root(), &entries, false, &config())
        .iter()
        .any(|e| e.contains("symlinks")));
}
#[test]
fn syntactic_provenance() {
    let r = root();
    let c = config();
    for (path,subject) in [("narwhal3d/native_transcript.py","class subject:\n pass\n"),("spike/test_ui_transcript_model.py","with manager() as subject:\n pass\n"),("native/ui_core/src/transcript_ffi.rs","struct Snapshot { subject: u64, }\nfn reply(handle: u64, subject: &Transcript) -> Reply {}\nfn f() {let x=Snapshot {subject: handle,};}")]{let blob=subject.replace("subject",if path.starts_with("narwhal"){"Owner"}else{"owner"}).into_bytes();assert!(audit_entries(&r,&one(path,&blob),false,&c).is_empty());assert_eq!(code_provenance_scan(&r,path,&blob).len(),blob.len());assert!(!audit_entries(&r,&one(if path.ends_with(".py"){"other.py"}else{"other.rs"},&blob),false,&c).is_empty());}
}
#[test]
fn provenance_is_not_whole_file_exemption() {
    for subject in [
        "// subject: u64",
        "/* struct Snapshot {subject: u64,} */",
        "/* outer /* nested */ struct Snapshot {subject: u64,} */",
        r#"const S: &str="struct Snapshot {subject: u64,}";"#,
        r####"const S: &str=r###"struct Snapshot {subject: u64,}"###;"####,
        "struct Snapshot {subject: u64, // subject: private\n}",
        "struct Other {subject: u64,}",
        "struct Snapshot {subject: i32,}",
        "fn other(handle:u64, subject: &Transcript)->Reply{}",
        "fn f(){let x=Snapshot {subject: other_handle,};}",
    ] {
        let b = subject.replace("subject", "owner");
        assert!(
            audit("native/ui_core/src/transcript_ffi.rs", b.as_bytes())
                .iter()
                .any(|e| e.contains("private/legacy")),
            "{subject}"
        )
    }
}
#[test]
fn exact_legal_attribution() {
    let r = root();
    for p in [
        "native/application_core/src/ordinal_map.rs",
        "native/application_core/generate_ordinal.rs",
        "native/application_core/OPENNURBS_LICENSE",
    ] {
        let b = fs::read(r.join(p)).unwrap();
        assert!(audit(p, &b).is_empty());
        let mut marked = b.clone();
        marked.extend(["\nRhi", "no private implementation\n"].concat().as_bytes());
        assert!(!audit(p, &marked).is_empty());
        assert!(!audit("notes.txt", &b).is_empty());
    }
}
#[test]
fn archive_refusal() {
    let c = config();
    for p in [
        "legacy/x.py",
        "app/Legacy.zip",
        "narwhal3d/legacy/x.py",
        "Contents/Resources/app/legacy/x.py",
        "runtime/bin/retired-python-old/core.so",
        "spike/fixtures/textfield_168212d4.py",
    ] {
        assert!(retirement_path_problem(p).is_some());
        assert!(path_problem(p, &c).is_some());
        assert!(!audit(p, b"safe").is_empty())
    }
    for b in [
        b"import legacy.old\n".as_slice(),
        b"from . import legacy\n",
        b"p='legacy/retired-python-old/x'\n",
        b"\x7fELF\0legacy/retired-python-old/x\0",
    ] {
        assert!(runtime_archive_problem(&root(), b).is_some());
        assert!(!audit("app/narwhal3d/native/payload.so", b).is_empty());
    }
    assert!(runtime_archive_problem(&root(), b"from . import native_history\n").is_none())
}
#[test]
fn recursive_import_closure() {
    let r = root();
    let c = config();
    let e = Entries::from([
        (
            "run_tests.sh".into(),
            (
                "100755".into(),
                b"if true; then\n bash spike/unlisted.sh\nfi\n".to_vec(),
            ),
        ),
        (
            "spike/unlisted.sh".into(),
            (
                "100755".into(),
                b"python3 spike/test_unlisted.py\n".to_vec(),
            ),
        ),
        (
            "spike/test_unlisted.py".into(),
            ("100644".into(), b"import test_leaf\n".to_vec()),
        ),
        (
            "spike/test_leaf.py".into(),
            ("100644".into(), b"pass\n".to_vec()),
        ),
    ]);
    let found = required_public_paths(&r, &e, &c);
    assert!(e.keys().all(|p| found.contains(p)));
    for p in e.keys().filter(|p| p.as_str() != "run_tests.sh") {
        let mut m = e.clone();
        m.remove(p);
        assert!(
            audit_entries(&r, &m, true, &c).contains(&format!("missing required public file: {p}"))
        )
    }
    let e = one(
        "spike/test_ui_core_gate.py",
        b"from test_release_ui_core import add_transcript_fixture\n",
    );
    let found = required_public_paths(&r, &e, &c);
    assert!(found.contains("scripts/test_release_ui_core.py"));
    assert!(!found.contains("spike/test_release_ui_core.py"));
    let e = one("run_tests.sh", b"# python3 spike/test_comment_only.py\n");
    assert!(!required_public_paths(&r, &e, &c).contains("spike/test_comment_only.py"));
}
#[test]
fn frozen_hash_schema_and_duplicates() {
    let r = root();
    let mut c = config();
    let p = "spike/fixtures/numparse_golden.json";
    let b = fs::read(r.join(p)).unwrap();
    let provenance = c["FROZEN_JSON_FILES"][p].clone();
    assert_eq!(
        serde_json::from_slice::<Value>(&frozen_scan("frozen_json_scan", p, &b, &c).unwrap())
            .unwrap(),
        provenance
    );
    let mut bad = b.clone();
    bad.push(b' ');
    assert!(frozen_scan("frozen_json_scan", p, &bad, &c)
        .unwrap_err()
        .contains("provenance hash"));
    for b in [
        br#"{"15.1.0":"not rows","16.0.0":[]}"#.as_slice(),
        br#"{"15.1.0":[],"15.1.0":[],"16.0.0":[]}"#,
        br#"{"15.1.0":[["x",null,[true,null]]],"16.0.0":[]}"#,
    ] {
        c["FROZEN_JSON_FILES"][p]["sha256"] = sha(b).into();
        assert!(frozen_scan("frozen_json_scan", p, b, &c).is_err());
    }
    let b = b"import json\nDATA=json.loads('[]')\n";
    assert_eq!(
        frozen_scan("frozen_data_scan", "spike/ui_golden_data.py", b, &c).unwrap(),
        b
    );
    let b = [b.as_slice(), ["\n# g", "-9\n"].concat().as_bytes()].concat();
    assert!(!audit("spike/ui_golden_data.py", &b).is_empty());
}
#[test]
fn source_walk_runtime_filter_and_symlinks() {
    let tmp = Scratch::new();
    let c = config();
    for p in [
        "narwhal3d/commands.py",
        "narwhal3d/native/.gitignore",
        "narwhal3d/native/fake.so",
        "narwhal3d/native/fake.dll",
        "narwhal3d/native/fake.dylib",
        "narwhal3d/native/fake.pyd",
        "narwhal3d/native/fake.receipt.json",
        "narwhal3d/native/observer/manifest.json",
        "narwhal3d/kernel/bin/helper",
        "narwhal3d/stray/fake.so",
        "narwhal3d/stray/fake.dll",
        "narwhal3d/stray/fake.dylib",
        "narwhal3d/stray/fake.pyd",
        "narwhal3d/stray/fake.receipt.json",
    ] {
        tmp.put(p, b"fake\n")
    }
    assert_eq!(
        recursive_files(&tmp.0, "narwhal3d", &c).unwrap(),
        ["narwhal3d/commands.py", "narwhal3d/native/.gitignore"]
    );
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink("/does/not/exist", tmp.0.join("narwhal3d/native/escape"))
            .unwrap();
        assert!(recursive_files(&tmp.0, "narwhal3d", &c)
            .unwrap_err()
            .contains("symlink"));
    }
}
#[test]
fn selection_refuses_nested_archive() {
    let tmp = Scratch::new();
    for p in [
        "narwhal3d",
        "packaging",
        "licenses",
        ".github",
        "artwork",
        "native/vendor/tracedraw",
        "native/picture_trace",
        "legacy",
    ] {
        fs::create_dir_all(tmp.0.join(p)).unwrap()
    }
    tmp.put("legacy/old.py", b"private");
    let mut c = config();
    for k in [
        "ROOT_FILES",
        "LAUNCH_FILES",
        "WELCOME_FILES",
        "NATIVE_FILES",
        "RUNTIME_FILES",
        "PATCH_FILES",
        "SCRIPT_FILES",
        "TEST_FILES",
    ] {
        c[k] = json!([])
    }
    assert!(public_files(&tmp.0, &c).unwrap().is_empty());
    tmp.put("narwhal3d/legacy/x.py", b"private");
    assert!(public_files(&tmp.0, &c)
        .unwrap_err()
        .contains("retired payload"));
    fs::remove_file(tmp.0.join("narwhal3d/legacy/x.py")).unwrap();
    c["ROOT_FILES"] = json!(["legacy/old.py"]);
    assert!(public_files(&tmp.0, &c)
        .unwrap_err()
        .contains("retired payload"));
}
fn git(root: &Path, args: &[&str]) -> Vec<u8> {
    let o = Command::new("git")
        .arg("--no-pager")
        .args(args)
        .current_dir(root)
        .env("GIT_EDITOR", "true")
        .output()
        .unwrap();
    assert!(
        o.status.success(),
        "{:?}: {}",
        args,
        String::from_utf8_lossy(&o.stderr)
    );
    o.stdout
}
#[test]
fn root_export_preserves_index_and_history() {
    let tmp = Scratch::new();
    git(&tmp.0, &["init", "--quiet"]);
    tmp.put("safe.txt", b"public\n");
    tmp.put("private.txt", b"local\n");
    git(&tmp.0, &["add", "private.txt"]);
    let before = git(&tmp.0, &["ls-files", "--stage"]);
    let mut c = config();
    c["REQUIRED"] = json!(["safe.txt"]);
    c["REQUIRED_EXECUTABLES"] = json!([]);
    c["REQUIRED_CONTENT"] = json!({});
    c["BRANCH"] = json!("public-test");
    let commit = commit_root(&tmp.0, &["safe.txt".into()], "root", 1790000000, &c).unwrap();
    assert!(root_commit_problem(&tmp.0, &commit).unwrap().is_none());
    assert_eq!(
        git_entries(&tmp.0, &commit).unwrap(),
        one("safe.txt", b"public\n")
    );
    assert_eq!(git(&tmp.0, &["ls-files", "--stage"]), before);
    assert!(
        commit_root(&tmp.0, &["safe.txt".into()], "root", 1790000000, &c)
            .unwrap_err()
            .contains("already exists")
    );
    c["BRANCH"] = json!("bad-root");
    tmp.put("safe.txt", ["Ow", "ner: private"].concat().as_bytes());
    assert!(commit_root(&tmp.0, &["safe.txt".into()], "root", 1790000000, &c).is_err());
    let bad = Command::new("git")
        .args(["show-ref", "--verify", "--quiet", "refs/heads/bad-root"])
        .current_dir(&tmp.0)
        .status()
        .unwrap();
    assert!(!bad.success());
    assert_eq!(git(&tmp.0, &["ls-files", "--stage"]), before);
}
#[test]
fn project_and_complete_source_qualification() {
    let r = root();
    let c = config();
    let report = checks::project(&r, &c).unwrap();
    assert_eq!(report["ok"], true, "{report}");
    let report = checks::completeness(&r, &c).unwrap();
    assert_eq!(report["ok"], true, "{report}");
    let report = dispatch(&json!({"op":"self_check","root":r})).unwrap();
    assert_eq!(report["ok"], true, "{report}");
}
#[test]
fn document_assets_links_licenses_and_source_omissions_are_load_bearing() {
    let r = root();
    let c = config();
    let tmp = Scratch::new();
    for p in public_files(&r, &c).unwrap() {
        let from = r.join(&p);
        tmp.put(&p, &fs::read(&from).unwrap());
    }
    let baseline = checks::project(&tmp.0, &c).unwrap();
    assert_eq!(baseline["ok"], true, "{baseline}");
    let cases = [
        ("README.md", "GPL-3.0-or-later", "license removed"),
        ("README.md", "docs/technical-reference.md", "not-public.md"),
        (
            "licenses/TraceDraw-Rust-Dependencies.txt",
            "PACKAGE ",
            "REMOVED ",
        ),
        ("licenses/VectorCraft-MIT.txt", "Permission", "Removed"),
    ];
    for (p, old, new) in cases {
        let path = tmp.0.join(p);
        let before = fs::read_to_string(&path).unwrap();
        let after = before.replacen(old, new, 1);
        assert_ne!(before, after, "missing document mutation anchor: {p}");
        fs::write(&path, after).unwrap();
        let report = checks::project(&tmp.0, &c).unwrap();
        assert_eq!(report["ok"], false, "document mutation survived: {p}");
        fs::write(&path, &before).unwrap();
        assert_eq!(checks::project(&tmp.0, &c).unwrap()["ok"], true);
    }
    let path = tmp.0.join("docs/images/workspace-light.png");
    let before = fs::read(&path).unwrap();
    fs::write(&path, &before[..before.len() - 12]).unwrap();
    assert_eq!(checks::project(&tmp.0, &c).unwrap()["ok"], false);
    fs::write(&path, &before).unwrap();
    assert_eq!(checks::project(&tmp.0, &c).unwrap()["ok"], true);
    let mut missing = c.clone();
    let list = strings(&c["NATIVE_FILES"]);
    missing["NATIVE_FILES"] = json!(list
        .into_iter()
        .filter(|p| p != "native/pure_core/src/surface_display.rs")
        .collect::<Vec<_>>());
    let paths = public_files(&r, &missing).unwrap();
    assert!(!paths
        .iter()
        .any(|p| p == "native/pure_core/src/surface_display.rs"));
    let selected = selected_entries(&r, &missing).unwrap();
    assert!(audit_entries(&r, &selected, true, &missing)
        .contains(&"missing required public file: native/pure_core/src/surface_display.rs".into()));
}
#[test]
fn malformed_rpc_and_unavailable_compiler_refuse() {
    let exe = env!("CARGO_BIN_EXE_narwhal-source-tools");
    let output =
        Command::new(exe)
            .arg("rpc")
            .env(
                "NARWHAL_SOURCE_COMPILER_SDK",
                "/nonexistent/compiler-sdk.so",
            )
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .and_then(|mut child| {
                use std::io::Write;
                child.stdin.take().unwrap().write_all(
                    br#"{"op":"audit_entries","entries":{},"require_manifest":false}"#,
                )?;
                child.wait_with_output()
            })
            .unwrap();
    assert!(output.status.success());
    let reply: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert!(
        reply["value"].as_array().unwrap().iter().any(|v| v
            .as_str()
            .is_some_and(|s| s.contains("compiler SDK admission failed"))),
        "{reply}"
    );
    assert!(dispatch(&json!({"op":"audit_entries","entries":{"bad":["100644"]}})).is_err());
    assert!(dispatch(&json!({"op":"not_an_operation"})).is_err());
    assert!(
        dispatch(&json!({"op":"audit_entries","entries":{"bad":["100644",{"$bytes":"!!"}]}}))
            .is_err()
    );
    assert!(
        Command::new(exe)
            .args(["create", "--timestamp", "not-a-number"])
            .output()
            .unwrap()
            .status
            .code()
            == Some(2)
    );
    assert!(
        Command::new(exe)
            .args(["audit", "--require-root-commit"])
            .output()
            .unwrap()
            .status
            .code()
            == Some(2)
    );
}
#[test]
fn duplicate_keys_cannot_hide_behind_row_count_failure() {
    let mut c = config();
    let p = "spike/fixtures/numparse_golden.json";
    c["FROZEN_JSON_FILES"][p]["rows"] = json!({"15.1.0":0,"16.0.0":0});
    let good = br#"{"15.1.0":[],"16.0.0":[]}"#;
    c["FROZEN_JSON_FILES"][p]["sha256"] = sha(good).into();
    assert!(frozen_scan("frozen_json_scan", p, good, &c).is_ok());
    let duplicate = br#"{"15.1.0":[],"15.1.0":[],"16.0.0":[]}"#;
    c["FROZEN_JSON_FILES"][p]["sha256"] = sha(duplicate).into();
    assert!(frozen_scan("frozen_json_scan", p, duplicate, &c)
        .unwrap_err()
        .contains("duplicate frozen DATA key"));
}
#[test]
fn surrogate_literal_cannot_hide_archive_path() {
    let blob = b"p = 'legacy/\\ud800/file.py'\n";
    assert!(runtime_archive_problem(&root(), blob).is_some());
    assert!(!audit("app/narwhal3d/surrogate.py", blob).is_empty());
}

#[test]
fn wire_preserves_numeric_fixture_sizes_and_byte_payloads() {
    let c = config();
    let fixture = "native/source_tools/fixtures/pure_abi_v1/cases.json";
    let configured = dispatch(&json!({"op":"config","root":root()})).unwrap();
    assert_eq!(
        configured["FROZEN_ABI_FILES"][fixture],
        c["FROZEN_ABI_FILES"][fixture]
    );
    assert!(
        configured["FROZEN_ABI_FILES"][fixture]["bytes"]
            .as_u64()
            .unwrap()
            > 0
    );
    assert_eq!(wire(&json!({"bytes":"abc"})), json!({"$bytes":"YWJj"}));
    assert_eq!(
        wire(&json!({"bytes_parts":["a","bc"]})),
        json!({"$bytes":"YWJj"})
    );
    assert_eq!(
        wire(&json!({"bytes":123,"sha256":"digest"})),
        json!({"bytes":123,"sha256":"digest"})
    );
}

#[test]
fn native_driver_harness_registration_is_mandatory() {
    let r = root();
    let c = config();
    let case = "finished_lane_harness_order_and_modes";
    assert_eq!(policy::check(&r, &c, case, &json!({})).unwrap()["ok"], true);
    let source = fs::read_to_string(r.join("run_tests.sh")).unwrap();
    let changed = source.replace(
        "scripts/source_tools.sh pure-gate",
        "scripts/check_pure_core.py",
    );
    assert_ne!(source, changed);
    assert!(policy::check(&r, &c, case, &json!({"run_tests.sh":changed})).is_err());
}
