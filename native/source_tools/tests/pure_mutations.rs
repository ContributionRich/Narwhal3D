use narwhal_source_tools::{pure_gate as gate, rust_code_mask, sha};
use std::{fs, path::Path};
fn copy(src: &Path, dst: &Path) {
    fs::create_dir_all(dst).unwrap();
    for e in fs::read_dir(src).unwrap() {
        let e = e.unwrap();
        let p = e.path();
        if e.file_name() == "target" {
            continue;
        }
        assert!(!p.is_symlink());
        if p.is_dir() {
            copy(&p, &dst.join(e.file_name()))
        } else {
            fs::copy(p, dst.join(e.file_name())).unwrap();
        }
    }
}
fn body(s: &str, name: &str) -> String {
    let masked = rust_code_mask(s.as_bytes());
    let start = s.find(&format!("pub fn {name}(")).unwrap();
    let at = start + masked[start..].iter().position(|b| *b == b'{').unwrap();
    let mut depth = 0;
    for (i, b) in masked.iter().enumerate().skip(at) {
        if *b == b'{' {
            depth += 1
        } else if *b == b'}' {
            depth -= 1;
            if depth == 0 {
                return format!("{}{{Ok(Default::default())}}{}", &s[..at], &s[i + 1..]);
            }
        }
    }
    panic!("body anchor")
}
#[test]
fn actual_guards_and_joint_input_are_red_then_restored_green() {
    let original = Path::new(env!("CARGO_MANIFEST_DIR"));
    let scratch = std::env::temp_dir().join(format!("pure-gate-mutants-{}", std::process::id()));
    assert!(!scratch.exists());
    let crate_ = scratch.join("native/source_tools");
    copy(original, &crate_);
    // Platform validation uses the independent source TSVs, not only the ABI blobs.
    let data = scratch.join("native/pure_core/tests/data");
    fs::create_dir_all(&data).unwrap();
    for name in ["preview24_linux.tsv", "preview24_windows_msvc.tsv"] {
        fs::copy(
            original
                .parent()
                .unwrap()
                .join("pure_core/tests/data")
                .join(name),
            data.join(name),
        )
        .unwrap();
    }

    let target = scratch.join("target");
    let abi = crate_.join("src/pure_abi.rs");
    let driver = crate_.join("src/pure_gate.rs");
    let before = fs::read_to_string(&abi).unwrap();
    let gate_before = fs::read_to_string(&driver).unwrap();
    let run = |name: &str| {
        let args = vec![
            "cargo".into(),
            "test".into(),
            "--release".into(),
            "--offline".into(),
            "--locked".into(),
            "-j2".into(),
            "--manifest-path".into(),
            crate_.join("Cargo.toml").display().to_string(),
            "--target-dir".into(),
            target.display().to_string(),
            "--test".into(),
            "pure_gate".into(),
            name.into(),
            "--".into(),
            "--exact".into(),
        ];
        let mut env: std::collections::BTreeMap<String, String> = std::env::vars().collect();
        env.insert(
            "NARWHAL_SOURCE_TEST_ROOT".into(),
            scratch.display().to_string(),
        );
        env.insert("RUSTFLAGS".into(), "-Awarnings".into());
        let (code, out, err) = gate::execute(&args, &scratch, Some(&env), None, 240).unwrap();
        (code, format!("{out}{err}"))
    };
    for (file, text, name, function) in [
        (
            &abi,
            &before,
            "guard_comparisons_reject_and_accept_real_values",
            "require_value",
        ),
        (
            &abi,
            &before,
            "guard_comparisons_reject_and_accept_real_values",
            "require_bytes",
        ),
        (
            &abi,
            &before,
            "guard_comparisons_reject_and_accept_real_values",
            "require_no_alias",
        ),
        (
            &driver,
            &gate_before,
            "compiled_unfiltered_evidence_for_every_target_is_mandatory",
            "execution_evidence",
        ),
        (
            &driver,
            &gate_before,
            "compiled_unfiltered_evidence_for_every_target_is_mandatory",
            "clippy_evidence",
        ),
    ] {
        let (code, out) = run(name);
        assert_eq!(code, 0, "baseline {function}: {out}");
        fs::write(file, body(text, function)).unwrap();
        let (code, out) = run(name);
        assert!(
            code != 0
                && out.contains("test result: FAILED")
                && out.contains("panicked at")
                && !out.contains("could not compile"),
            "survived/infrastructure {function}: {out}"
        );
        fs::write(file, text).unwrap();
        let (code, out) = run(name);
        assert_eq!(code, 0, "restored {function}: {out}");
        println!("RED producer / restored GREEN: {function}");
    }
    for (name, function) in [
        (
            "platform_selection_remains_single_pinned_oracle",
            "preview_platform",
        ),
        (
            "platform_selection_remains_single_pinned_oracle",
            "platforms",
        ),
        (
            "filesystem_closure_detects_every_manifest_and_source_omission",
            "closure",
        ),
        (
            "child_reply_crash_json_and_identity_refusals",
            "decode_probe_reply",
        ),
    ] {
        let (file, text) = if function == "platforms" || function == "preview_platform" {
            (&abi, &before)
        } else {
            (&driver, &gate_before)
        };
        let (code, out) = run(name);
        assert_eq!(code, 0, "baseline {function}: {out}");
        fs::write(file, body(text, function)).unwrap();
        let (code, out) = run(name);
        assert!(
            code != 0
                && out.contains("test result: FAILED")
                && out.contains("panicked at")
                && !out.contains("could not compile"),
            "survived/infrastructure {function}: {out}"
        );
        fs::write(file, text).unwrap();
        let (code, out) = run(name);
        assert_eq!(code, 0, "restored {function}: {out}");
        println!("RED producer / restored GREEN: {function}");
    }
    let source = crate_.join("src/lib.rs");
    let original_source = fs::read_to_string(&source).unwrap();
    for (file, text, old, new, test) in [
        (
            &source,
            &original_source,
            "if contains(&lower(blob), &lower(&bytes(&marker)))",
            "if false",
            "witness_inventory_is_closed_hash_pinned_and_raw_secret_scanned",
        ),
        (
            &driver,
            &gate_before,
            "if begin.elapsed() > Duration::from_secs(seconds)",
            "if false",
            "process_bounds_cleanup_and_environment",
        ),
        (
            &driver,
            &gate_before,
            "command.env_clear().envs(env);",
            "let _ = env;",
            "process_bounds_cleanup_and_environment",
        ),
    ] {
        let changed = text.replace(old, new);
        assert_ne!(&changed, text, "mutation anchor {old}");
        let (code, out) = run(test);
        assert_eq!(code, 0, "baseline {test}: {out}");
        fs::write(file, changed).unwrap();
        let (code, out) = run(test);
        assert!(
            code != 0
                && out.contains("test result: FAILED")
                && out.contains("panicked at")
                && !out.contains("could not compile"),
            "survived/infrastructure {old}: {out}"
        );
        fs::write(file, text).unwrap();
        let (code, out) = run(test);
        assert_eq!(code, 0, "restored {test}: {out}");
        println!("RED producer / restored GREEN: {old}");
    }
    // Same real input mutation as the superseded joint packaging test, with reviewed data re-pinned.
    let catalog = crate_.join("fixtures/pure_abi_v1/cases.json");
    let raw = fs::read(&catalog).unwrap();
    let mut cases: serde_json::Value = serde_json::from_slice(&raw).unwrap();
    let case = cases
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|v| v["name"] == "PURE_CORE_REMOVE_JOIN_PROBE")
        .unwrap();
    assert_eq!(case["calls"][0]["args"][5], 3);
    case["calls"][0]["args"][5] = 0.into();
    let changed = serde_json::to_vec(&cases).unwrap();
    let name = "all_original_abi_calls_have_actual_producer_and_identity_evidence";
    let (code, out) = run(name);
    assert_eq!(code, 0, "joint baseline: {out}");
    fs::write(&catalog, &changed).unwrap();
    let replacement = before.replace(&sha(&raw), &sha(&changed));
    assert_ne!(replacement, before);
    fs::write(&abi, replacement).unwrap();
    let (code, out) = run(name);
    assert!(
        code != 0
            && out.contains("test result: FAILED")
            && out.contains("differs")
            && !out.contains("provenance hash mismatch"),
        "joint input survived: {out}"
    );
    fs::write(&catalog, raw).unwrap();
    fs::write(&abi, &before).unwrap();
    let (code, out) = run(name);
    assert_eq!(code, 0, "joint restoration: {out}");
    println!("RED joint input / restored GREEN");
    assert_eq!(
        fs::read(original.join("src/pure_abi.rs")).unwrap(),
        before.as_bytes()
    );
    fs::remove_dir_all(scratch).unwrap();
}
