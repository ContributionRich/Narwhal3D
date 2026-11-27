//! Recompile actual producer faults in an isolated crate; live sources never change.
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
    process::Command,
    time::{Duration, Instant},
};
fn copy(src: &Path, dst: &Path) {
    fs::create_dir_all(dst).unwrap();
    for entry in fs::read_dir(src).unwrap() {
        let entry = entry.unwrap();
        let p = entry.path();
        if p.file_name().unwrap() == "target" {
            continue;
        }
        assert!(!p.is_symlink());
        if p.is_dir() {
            copy(&p, &dst.join(entry.file_name()))
        } else {
            fs::copy(&p, dst.join(entry.file_name())).unwrap();
        }
    }
}
fn replace_body(s: &str, name: &str, body: &str) -> String {
    let masked = narwhal_source_tools::rust_code_mask(s.as_bytes());
    let start = s.find(&format!("pub fn {name}(")).unwrap();
    let at = start + masked[start..].iter().position(|b| *b == b'{').unwrap();
    let mut depth = 0;
    let mut close = None;
    for (i, b) in masked.iter().enumerate().skip(at) {
        if *b == b'{' {
            depth += 1
        } else if *b == b'}' {
            depth -= 1;
            if depth == 0 {
                close = Some(i);
                break;
            }
        }
    }
    let close = close.unwrap();
    format!("{}{{{body}}}{}", &s[..at], &s[close + 1..])
}
fn run(crate_: &Path, target: &Path, root: &Path, test: &str) -> (bool, String) {
    let mut child = Command::new("cargo")
        .args([
            "+1.95.0",
            "test",
            "--release",
            "--offline",
            "--locked",
            "-j",
            "2",
            "--manifest-path",
        ])
        .arg(crate_.join("Cargo.toml"))
        .arg("--target-dir")
        .arg(target)
        .args([
            "--test",
            "contracts",
            test,
            "--",
            "--exact",
            "--test-threads=1",
        ])
        .env("NARWHAL_SOURCE_TEST_ROOT", root)
        .env("RUSTFLAGS", "-Awarnings")
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    // Drain while running: a caught inventory mutant can print more than a pipe buffer.
    let drain = |mut pipe: Box<dyn Read + Send>| {
        std::thread::spawn(move || {
            let mut bytes = Vec::new();
            pipe.read_to_end(&mut bytes).unwrap();
            bytes
        })
    };
    let stdout = drain(Box::new(child.stdout.take().unwrap()));
    let stderr = drain(Box::new(child.stderr.take().unwrap()));
    let started = Instant::now();
    loop {
        if child.try_wait().unwrap().is_some() {
            break;
        }
        if started.elapsed() > Duration::from_secs(180) {
            child.kill().unwrap();
            panic!("mutation compilation/execution timed out (infrastructure, not a killed mutant)")
        }
        std::thread::sleep(Duration::from_millis(50))
    }
    let status = child.wait().unwrap();
    let text = String::from_utf8_lossy(&[stdout.join().unwrap(), stderr.join().unwrap()].concat())
        .into_owned();
    (status.success(), text)
}
#[test]
fn producer_faults_are_assertion_red_restored_green() {
    let original = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let root = original.parent().unwrap().parent().unwrap();
    let scratch = std::env::temp_dir().join(format!("source-tools-mutants-{}", std::process::id()));
    assert!(!scratch.exists());
    let crate_ = scratch.join("crate");
    copy(&original, &crate_);
    let target = scratch.join("target");
    let file = crate_.join("src/lib.rs");
    let before = fs::read_to_string(&file).unwrap();
    let cases = [
        (
            "transient publication classification removed",
            "transient_payload_blocks_selection_audit_and_export_before_git_writes",
            replace_body(&before, "transient_publication_problem", "None"),
        ),
        (
            "early export refusal removed",
            "transient_payload_blocks_selection_audit_and_export_before_git_writes",
            replace_body(&before, "require_no_transient_export", "Ok(())"),
        ),
        (
            "numeric fixture sizes lost during configuration export",
            "wire_preserves_numeric_fixture_sizes_and_byte_payloads",
            before.replace(
                "m.get(\"bytes\").is_some_and(Value::is_string)",
                "m.contains_key(\"bytes\")",
            ),
        ),
        (
            "syntactic recognition removed",
            "syntactic_provenance",
            replace_body(&before, "code_provenance_scan", "blob.to_vec()"),
        ),
        (
            "whole file exemption",
            "provenance_is_not_whole_file_exemption",
            replace_body(&before, "code_provenance_scan", "Vec::new()"),
        ),
        (
            "publisher archive admitted",
            "selection_refuses_nested_archive",
            replace_body(&before, "retirement_path_problem", "None"),
        ),
        (
            "legal attribution not normalized",
            "exact_legal_attribution",
            replace_body(&before, "legacy_scan_bytes", "lower(blob)"),
        ),
        (
            "blanket legal exemption",
            "exact_legal_attribution",
            replace_body(&before, "legacy_scan_bytes", "Vec::new()"),
        ),
        (
            "hash guard removed",
            "frozen_hash_schema_and_duplicates",
            before.replace("if value[\"sha256\"].as_str() != Some(&digest)", "if false"),
        ),
        (
            "import closure removed",
            "recursive_import_closure",
            before.replace("req.extend(deps);", "let _ = deps;"),
        ),
        (
            "secret scan removed",
            "privacy_secrets_and_modes",
            before.replace(
                "for marker in seq(&c[\"SECRET_MARKERS\"])",
                "for marker in Vec::<Value>::new()",
            ),
        ),
        (
            "compiler validation removed",
            "compiler_and_unicode",
            before.replace(
                "if let Err(e) = python_sdk::validate(root, blob, path)",
                "if let Err(e) = Ok::<(),String>(())",
            ),
        ),
        (
            "root export audit removed",
            "root_export_preserves_index_and_history",
            before.replace(
                "let errors = audit_entries(root, &entries, true, c);",
                "let errors:Vec<String> = Vec::new();",
            ),
        ),
        (
            "walk symlinks admitted",
            "source_walk_runtime_filter_and_symlinks",
            before.replace(
                "if p.is_symlink() {\n            return Err",
                "if false && p.is_symlink() {\n            return Err",
            ),
        ),
        (
            "duplicate key guard removed",
            "duplicate_keys_cannot_hide_behind_row_count_failure",
            before.replace(
                "strict_json::parse(&json_surrogates(blob))",
                "serde_json::from_slice(&json_surrogates(blob))",
            ),
        ),
    ];
    for (label, test, mutant) in cases {
        assert_ne!(mutant, before, "missing producer mutation anchor: {label}");
        fs::write(&file, &before).unwrap();
        let (ok, text) = run(&crate_, &target, root, test);
        assert!(
            ok && text.contains("1 passed; 0 failed"),
            "baseline infrastructure {label}: {text}"
        );
        fs::write(&file, mutant).unwrap();
        let (ok, text) = run(&crate_, &target, root, test);
        assert!(
            !ok && text.contains("test result: FAILED")
                && text.contains("panicked at")
                && !text.contains("could not compile"),
            "mutation survived or infrastructure failed {label}: {text}"
        );
        fs::write(&file, &before).unwrap();
        let (ok, text) = run(&crate_, &target, root, test);
        assert!(
            ok && text.contains("1 passed; 0 failed"),
            "restoration {label}: {text}"
        );
        println!("RED actual producer / GREEN restored: {label}");
    }
    let sdk_file = crate_.join("src/python_sdk.rs");
    let sdk = fs::read_to_string(&sdk_file).unwrap();
    let mutant = sdk.replace("self.unicode_with_surrogates(o)", "String::new()");
    assert_ne!(sdk, mutant, "surrogate mutation anchor missing");
    let test = "surrogate_literal_cannot_hide_archive_path";
    let (ok, text) = run(&crate_, &target, root, test);
    assert!(
        ok && text.contains("1 passed; 0 failed"),
        "SDK baseline: {text}"
    );
    fs::write(&sdk_file, mutant).unwrap();
    let (ok, text) = run(&crate_, &target, root, test);
    assert!(
        !ok && text.contains("test result: FAILED")
            && text.contains("panicked at")
            && !text.contains("could not compile"),
        "SDK mutation survived or infrastructure failed: {text}"
    );
    fs::write(&sdk_file, &sdk).unwrap();
    let (ok, text) = run(&crate_, &target, root, test);
    assert!(
        ok && text.contains("1 passed; 0 failed"),
        "SDK restoration: {text}"
    );
    println!("RED actual producer / GREEN restored: surrogate encoding refusal must not erase policy text");
    assert_eq!(
        fs::read(original.join("src/lib.rs")).unwrap(),
        before.as_bytes()
    );
    fs::remove_dir_all(scratch).unwrap();
}
