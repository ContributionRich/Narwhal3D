//! Replaces the old Python fake engines with typed ABI and filesystem/process controls.
use narwhal_source_tools::{pure_abi as abi, pure_gate as gate, *};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    fs,
    path::PathBuf,
    sync::{
        atomic::{AtomicUsize, Ordering},
        OnceLock,
    },
};
fn root() -> PathBuf {
    std::env::var_os("NARWHAL_SOURCE_TEST_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(gate::project_root)
}
struct Scratch(PathBuf);
impl Scratch {
    fn new() -> Self {
        static N: AtomicUsize = AtomicUsize::new(0);
        let p = std::env::temp_dir().join(format!(
            "pure-gate-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        Self(p)
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn library() -> PathBuf {
    static LIB: OnceLock<PathBuf> = OnceLock::new();
    LIB.get_or_init(|| {
        if let Some(p) = std::env::var_os("NARWHAL_PURE_CORE") {
            return PathBuf::from(p);
        }
        let target =
            std::env::temp_dir().join(format!("source-tools-abi-build-{}", std::process::id()));
        let args = vec![
            "cargo".into(),
            "build".into(),
            "--release".into(),
            "--locked".into(),
            "--offline".into(),
            "-j2".into(),
            "--manifest-path".into(),
            root()
                .join("native/pure_core/Cargo.toml")
                .display()
                .to_string(),
            "--target-dir".into(),
            target.display().to_string(),
        ];
        let (code, out, err) = gate::execute(&args, &root(), None, None, 300).unwrap();
        assert_eq!(code, 0, "{out}{err}");
        target.join("release/libnarwhal_pure_core.so")
    })
    .clone()
}
fn rpc(q: Value) -> Result<Value, String> {
    let (code, out, err) = gate::execute(
        &[
            env!("CARGO_BIN_EXE_narwhal-source-tools").into(),
            "rpc".into(),
        ],
        &root(),
        None,
        Some(&serde_json::to_vec(&q).unwrap()),
        60,
    )?;
    if code != 0 {
        return Err(format!("{code}: {err}"));
    }
    let reply: Value = serde_json::from_str(&out).map_err(|e| e.to_string())?;
    if reply["ok"] != true {
        return Err(reply["error"].to_string());
    }
    Ok(reply["value"].clone())
}
fn fixture(name: &str) -> Value {
    serde_json::from_slice(
        &fs::read(
            root()
                .join("native/source_tools/fixtures/pure_abi_v1")
                .join(name),
        )
        .unwrap(),
    )
    .unwrap()
}
#[test]
fn all_original_abi_calls_have_actual_producer_and_identity_evidence() {
    let cases = fixture("cases.json");
    assert_eq!(cases.as_array().unwrap().len(), 19);
    assert_eq!(
        cases
            .as_array()
            .unwrap()
            .iter()
            .map(|c| c["calls"].as_array().unwrap().len())
            .sum::<usize>(),
        411
    );
    for c in cases.as_array().unwrap() {
        let got=rpc(json!({"op":"pure_probe","root":root(),"library":library(),"name":c["name"],"expected_identity":c["identity"]})).unwrap();
        assert_eq!(got, c["identity"], "{}", c["name"]);
    }
}
#[test]
fn every_original_negative_control_is_rejected() {
    let controls = fixture("counterexamples.json");
    assert_eq!(controls.as_array().unwrap().len(), 227);
    let mut counts = BTreeMap::new();
    for c in controls.as_array().unwrap() {
        *counts.entry(c["group"].as_str().unwrap()).or_insert(0) += 1;
        if c["missing_export"] == true {
            continue;
        }
        assert!(
            abi::validate_observation(&root(), &c["expected"], &c["actual"]).is_err(),
            "survived: {}",
            c["mutant"]
        );
    }
    assert_eq!(counts["parser"], 92);
    assert_eq!(counts["retirement"], 135);
}
#[test]
fn every_missing_export_control_hits_the_actual_loader() {
    let scratch = Scratch::new();
    let original = fs::read(library()).unwrap();
    let controls = fixture("counterexamples.json");
    let mut seen = std::collections::BTreeSet::new();
    for c in controls
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| c["missing_export"] == true)
    {
        let name = c["mutant"].as_str().unwrap();
        if !seen.insert((c["group"].as_str().unwrap(), name)) {
            continue;
        }
        let mut bytes = original.clone();
        let mut needle = name.as_bytes().to_vec();
        needle.push(0);
        let offsets: Vec<_> = bytes
            .windows(needle.len())
            .enumerate()
            .filter_map(|(i, b)| (b == needle).then_some(i))
            .collect();
        assert!(!offsets.is_empty(), "missing export anchor {name}");
        for i in offsets {
            bytes[i] = b'x';
        }
        let lib = scratch.0.join("mutant.so");
        fs::write(&lib, bytes).unwrap();
        let name = if c["group"] == "parser" {
            "PURE_CORE_PARSE_PROBE"
        } else {
            "PURE_CORE_RETIREMENT_PROBE"
        };
        let error =
            rpc(json!({"op":"pure_probe","root":root(),"library":lib,"name":name})).unwrap_err();
        assert!(
            error.contains("required export") || error.contains("undefined symbol"),
            "wrong refusal: {error}"
        );
    }
}
#[test]
fn guard_comparisons_reject_and_accept_real_values() {
    assert!(abi::require_value(&json!(1), &json!(2), "scalar").is_err());
    assert!(abi::require_value(&json!(1), &json!(1), "scalar").is_ok());
    assert!(abi::require_bytes(&[1, 0], &[1, 1], "bytes").is_err());
    assert!(abi::require_bytes(&[1, 0], &[1, 0], "bytes").is_ok());
    assert!(abi::require_no_alias(true).is_err());
    assert!(abi::require_no_alias(false).is_ok());
}
#[test]
fn platform_selection_remains_single_pinned_oracle() {
    let p = abi::platforms(&root()).unwrap();
    assert_eq!(
        abi::preview_platform("linux", "x86_64", "gnu").unwrap(),
        "linux"
    );
    assert_eq!(
        abi::preview_platform("windows", "x86_64", "msvc").unwrap(),
        "windows"
    );
    for (os, arch, tool) in [
        ("windows", "x86_64", "gnu"),
        ("linux", "aarch64", "gnu"),
        ("macos", "x86_64", "darwin"),
    ] {
        assert!(abi::preview_platform(os, arch, tool).is_err());
    }
    for os in ["linux", "win32"] {
        let v = &p[os];
        assert_eq!(v["rows"].as_array().unwrap().len(), 6);
        let data = fs::read(
            root()
                .join("native/pure_core/tests/data")
                .join(v["source"].as_str().unwrap()),
        )
        .unwrap();
        assert_eq!(sha(&data), v["source_sha256"].as_str().unwrap());
        for row in v["rows"].as_array().unwrap() {
            let key = row[0].as_u64().unwrap().to_string();
            let hex = v["source_rows"][&key]["output_hex"].as_str().unwrap();
            let bytes: Vec<_> = hex
                .as_bytes()
                .chunks_exact(2)
                .map(|x| u8::from_str_radix(std::str::from_utf8(x).unwrap(), 16).unwrap())
                .collect();
            assert_eq!(sha(&bytes), row[5].as_str().unwrap());
        }
    }
    assert!(!p["windows_blob_overlays"].as_object().unwrap().is_empty());
    for (old, new) in p["windows_blob_overlays"].as_object().unwrap() {
        assert_ne!(
            abi::witness_blob(&root(), old).unwrap(),
            abi::witness_blob(&root(), new.as_str().unwrap()).unwrap()
        );
    }
}
#[test]
fn compiled_unfiltered_evidence_for_every_target_is_mandatory() {
    let c = gate::metadata();
    let summary = "test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out";
    for target in strings(&c["RUST_INTEGRATION_SUITES"]) {
        let a = json!({"reason":"compiler-artifact","target":{"name":target,"kind":["test"]},"profile":{"test":true},"executable":"/fixture/test"});
        let good = format!("{a}\n{summary}");
        assert_eq!(gate::execution_evidence(&good, &target).unwrap(), 2);
        for fault in [
            summary.into(),
            a.to_string(),
            good.replace(&target, "wrong"),
            good.replace("2 passed", "0 passed"),
            good.replace("0 filtered", "1 filtered"),
            good.replace("0 ignored", "1 ignored"),
            good.replace("ok.", "FAILED."),
            good.replace("\"test\":true", "\"test\":false"),
            good.replace("/fixture/test", ""),
        ] {
            assert!(
                gate::execution_evidence(&fault, &target).is_err(),
                "{target}: {fault}"
            );
        }
    }
    assert!(gate::clippy_evidence(&json!({"status":"skipped"})).is_err());
    assert!(gate::clippy_evidence(&json!({"status":"ok","ran":false})).is_err());
    assert!(gate::clippy_evidence(&json!({"status":"ok","ran":true})).is_ok());
}
#[test]
fn filesystem_closure_detects_every_manifest_and_source_omission() {
    let c = gate::metadata();
    let scratch = Scratch::new();
    let crate_ = &scratch.0;
    let all = strings(&c["RUST_INTEGRATION_FILES"]);
    let modules = strings(&c["RUST_REQUIRED_MODULE_FILES"]);
    for path in all.iter().chain(modules.iter()) {
        let rel = path.strip_prefix("native/pure_core/").unwrap();
        let p = crate_.join(rel);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, b"fixture").unwrap();
    }
    let public = json!({"NATIVE_FILES":all.iter().chain(modules.iter()).collect::<Vec<_>>()});
    assert!(gate::closure(&root(), crate_, &c, &public).is_ok());
    for path in all.iter().chain(modules.iter()) {
        let p = crate_.join(path.strip_prefix("native/pure_core/").unwrap());
        fs::remove_file(&p).unwrap();
        assert!(
            gate::closure(&root(), crate_, &c, &public).is_err(),
            "file {path}"
        );
        fs::write(&p, b"fixture").unwrap();
        let mut selected = public.clone();
        selected["NATIVE_FILES"]
            .as_array_mut()
            .unwrap()
            .retain(|v| v != path);
        assert!(
            gate::closure(&root(), crate_, &c, &selected).is_err(),
            "manifest {path}"
        );
    }
    fs::write(crate_.join("tests/unregistered.rs"), b"x").unwrap();
    assert!(gate::closure(&root(), crate_, &c, &public).is_err());
}
#[test]
fn process_bounds_cleanup_and_environment() {
    let scratch = Scratch::new();
    let env = BTreeMap::from([("ONLY_TEST".into(), "witness".into())]);
    let run = |s: &str, input: Option<&[u8]>, seconds| {
        gate::execute(
            &["/bin/sh".into(), "-c".into(), s.into()],
            &scratch.0,
            Some(&env),
            input,
            seconds,
        )
    };
    let (code, out, err) = run("printenv ONLY_TEST; printf diagnostic >&2", None, 2).unwrap();
    assert_eq!(
        (code, out.as_str(), err.as_str()),
        (0, "witness\n", "diagnostic")
    );
    assert_eq!(run("exit 17", None, 2).unwrap().0, 17);
    assert!(run("sleep 10", None, 1).unwrap_err().contains("timed out"));
    assert!(run("sleep 10 & exit 0", None, 1)
        .unwrap_err()
        .contains("timed out"));
    assert!(run("sleep 10", Some(&vec![1; 1024 * 1024]), 1)
        .unwrap_err()
        .contains("timed out"));
    assert!(run("yes x", None, 2).unwrap_err().contains("output limit"));
}
#[test]
fn supervised_probe_refuses_missing_library_wrong_identity_and_timeout() {
    let lib = library();
    assert!(rpc(json!({"op":"pure_probe","root":root(),"library":"/nonexistent","name":"PURE_CORE_PARSE_PROBE"})).is_err());
    let error=rpc(json!({"op":"pure_probe","root":root(),"library":lib,"name":"PURE_CORE_PARSE_PROBE","expected_identity":{}})).unwrap_err();
    assert!(error.contains("evidence differs"));
}

#[test]
fn filesystem_cargo_fixture_checks_real_process_failures_and_every_target() {
    let scratch = Scratch::new();
    let bin = scratch.0.join("bin");
    fs::create_dir(&bin).unwrap();
    let source = bin.join("cargo.rs");
    fs::write(&source,r##"
use std::{env,fs};fn main(){let a:Vec<_>=env::args().collect();if a.iter().any(|s|s=="--installed"){println!("x86_64-pc-windows-gnu");return}let log=env::var("LOG").unwrap();use std::io::Write;writeln!(fs::OpenOptions::new().append(true).create(true).open(log).unwrap(),"{}",a.join(" ")).unwrap();let summary="test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out";let target=a.iter().position(|s|s=="--test").map(|i|a[i+1].as_str()).unwrap_or("");let selected=env::var("SELECTED").unwrap_or_default();let fault=if selected.is_empty()||selected==target{env::var("FAULT").unwrap_or_default()}else{String::new()};if fault=="failed"{std::process::exit(17)}if target.is_empty(){println!("{summary}");return}let name=if fault=="wrong-target"{"wrong"}else{target};let artifact=format!(r#"{{"reason":"compiler-artifact","target":{{"name":"{name}","kind":["test"]}},"profile":{{"test":true}},"executable":"/fixture/test"}}"#);if fault!="missing-compilation"{println!("{artifact}")}if fault!="discovery-only"{println!("{}",match fault.as_str(){"zero-tests"=>summary.replace("2 passed","0 passed"),"filtered"=>summary.replace("0 filtered","1 filtered"),_=>summary.into()})}}
"##).unwrap();
    let (code, out, err) = gate::execute(
        &[
            "rustc".into(),
            source.display().to_string(),
            "-o".into(),
            bin.join("cargo").display().to_string(),
        ],
        &root(),
        None,
        None,
        60,
    )
    .unwrap();
    assert_eq!(code, 0, "{out}{err}");
    let c = gate::metadata();
    let crate_ = scratch.0.join("crate");
    for name in strings(&c["RUST_INTEGRATION_FILES"])
        .iter()
        .chain(strings(&c["RUST_REQUIRED_MODULE_FILES"]).iter())
    {
        let p = crate_.join(name.strip_prefix("native/pure_core/").unwrap());
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, b"fixture").unwrap();
    }
    let public = json!({"NATIVE_FILES":strings(&c["RUST_INTEGRATION_FILES"]).into_iter().chain(strings(&c["RUST_REQUIRED_MODULE_FILES"])).collect::<Vec<_>>()});
    let log = scratch.0.join("log");
    let mut env: BTreeMap<String, String> = std::env::vars().collect();
    env.insert("PATH".into(), format!("{}:/usr/bin:/bin", bin.display()));
    env.insert("LOG".into(), log.display().to_string());
    let run = |env: &BTreeMap<String, String>| {
        gate::step(
            &root(),
            &crate_,
            &c,
            &public,
            "tests",
            PathBuf::new().as_path(),
            &json!({"environment":env}),
        )
    };
    let good = run(&env).unwrap();
    assert_eq!(
        good["integration_suites"].as_object().unwrap().len(),
        strings(&c["RUST_INTEGRATION_SUITES"]).len()
    );
    assert_eq!(
        fs::read_to_string(&log).unwrap().lines().count(),
        1 + strings(&c["RUST_INTEGRATION_SUITES"]).len()
    );
    for target in strings(&c["RUST_INTEGRATION_SUITES"]) {
        for fault in [
            "missing-compilation",
            "discovery-only",
            "wrong-target",
            "zero-tests",
            "filtered",
            "failed",
        ] {
            env.insert("SELECTED".into(), target.clone());
            env.insert("FAULT".into(), fault.into());
            assert!(
                run(&env).is_err(),
                "survived actual process {target}:{fault}"
            );
        }
    }
    // End-to-end native orchestration over real processes and the actual ABI library.
    // Cargo is a filesystem fixture; this is not a full source-gate certification.
    fs::copy(bin.join("cargo"), bin.join("rustup")).unwrap();
    fs::write(
        crate_.join("Cargo.toml"),
        "[package]\nname='fixture'\nversion='0.0.0'\n",
    )
    .unwrap();
    env.remove("FAULT");
    env.remove("SELECTED");
    fs::write(&log, b"").unwrap();
    let mut configured = c.clone();
    configured["PURE_CORE_PYTESTS"] = json!(["spike/test_calc_owned.py"]);
    let report=rpc(json!({"op":"pure_gate","action":"run","root":root(),"crate":crate_,"library":library(),"pure_config":configured,"environment":env})).unwrap();
    assert_eq!(report["ok"], true);
    assert_eq!(
        report["steps"]["direct_abi"]["command"],
        "Rust typed C ABI probe"
    );
    assert_eq!(report["steps"].as_object().unwrap().len(), 23);
    assert_eq!(
        report["steps"]["adapter"]["manifest"],
        json!(["spike/test_calc_owned.py"])
    );
    let order = fs::read_to_string(&log).unwrap();
    let order: Vec<_> = order.lines().collect();
    assert!(order[0].contains(" fmt --check "));
    assert!(order[1].contains(" clippy --all-targets "));
    assert!(order[2].contains(" test --offline "));
    assert!(
        order
            .iter()
            .position(|s| s.contains(" build --release "))
            .unwrap()
            > strings(&c["RUST_INTEGRATION_SUITES"]).len()
    );
    env.remove("SELECTED");
    env.insert("FAULT".into(), "failed".into());
    assert!(gate::step(
        &root(),
        &crate_,
        &c,
        &public,
        "clippy",
        PathBuf::new().as_path(),
        &json!({"environment":env})
    )
    .unwrap_err()
    .contains("clippy"));
}

#[test]
fn witness_inventory_is_closed_hash_pinned_and_raw_secret_scanned() {
    let c = config();
    let selected = strings(&c["NATIVE_FILES"]);
    let required = strings(&c["REQUIRED"]);
    let dir = root().join("native/source_tools/fixtures/pure_abi_v1");
    fn walk(p: &std::path::Path, out: &mut Vec<PathBuf>) {
        for e in fs::read_dir(p).unwrap() {
            let p = e.unwrap().path();
            assert!(!p.is_symlink());
            if p.is_dir() {
                walk(&p, out)
            } else {
                out.push(p)
            }
        }
    }
    let mut paths = Vec::new();
    walk(&dir, &mut paths);
    assert_eq!(
        paths.len(),
        c["FROZEN_ABI_FILES"].as_object().unwrap().len()
    );
    for p in paths {
        let name = p
            .strip_prefix(root())
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        assert!(
            selected.contains(&name) && required.contains(&name),
            "unregistered {name}"
        );
        let data = fs::read(p).unwrap();
        let one = Entries::from([(name.clone(), ("100644".into(), data.clone()))]);
        assert!(audit_entries(&root(), &one, false, &c).is_empty(), "{name}");
        let mut broken = data.clone();
        broken.push(0);
        let one = Entries::from([(name.clone(), ("100644".into(), broken))]);
        assert!(audit_entries(&root(), &one, false, &c)
            .iter()
            .any(|s| s.contains("provenance")));
    }
    let name = "native/source_tools/fixtures/pure_abi_v1/blobs/canary";
    let data = [
        b"\0\0".as_slice(),
        [b"gh".as_slice(), b"p_sensitive".as_slice()]
            .concat()
            .as_slice(),
    ]
    .concat();
    let mut repinned = c.clone();
    repinned["FROZEN_ABI_FILES"][name] = json!({"bytes":data.len(),"sha256":sha(&data)});
    let errors = audit_entries(
        &root(),
        &Entries::from([(name.into(), ("100644".into(), data))]),
        false,
        &repinned,
    );
    assert!(
        errors.iter().any(|e| e.contains("possible secret")),
        "raw secret survived valid pin: {errors:?}"
    );
    assert!(
        !errors.iter().any(|e| e.contains("provenance")),
        "hash failure masked missing secret scan"
    );
}

#[test]
fn child_reply_crash_json_and_identity_refusals() {
    let expected = json!({"abi":1});
    let good = json!({"ok":true,"value":expected}).to_string();
    assert_eq!(
        gate::decode_probe_reply(0, &good, "", &expected).unwrap(),
        expected
    );
    for (status, out, err) in [
        (-11, good.as_str(), "native crash detail"),
        (0, "not JSON", ""),
        (0, "{}", ""),
        (0, "{\"ok\":true}", ""),
        (0, "{\"ok\":true,\"value\":{}}", ""),
    ] {
        let refusal = gate::decode_probe_reply(status, out, err, &expected).unwrap_err();
        if status == -11 {
            assert!(refusal.contains("native crash detail"));
        }
    }
}
