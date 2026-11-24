//! Opt-in actual Rust producer mutations in an external disposable crate copy.
//! Run: cargo test --test content13_mutations -- --ignored --nocapture
use serde_json::Value;
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    time::{SystemTime, UNIX_EPOCH},
};
#[path = "support/golden27_sha256.rs"]
mod sha256;
fn copy(source: &Path, dest: &Path) {
    fs::create_dir_all(dest).unwrap();
    for entry in fs::read_dir(source).unwrap() {
        let entry = entry.unwrap();
        let ty = entry.file_type().unwrap();
        if entry.file_name() == "target" || ty.is_symlink() {
            continue;
        }
        let to = dest.join(entry.file_name());
        if ty.is_dir() {
            copy(&entry.path(), &to);
        } else {
            fs::copy(entry.path(), to).unwrap();
        }
    }
}
fn run(root: &Path, label: &str, green: bool) {
    let output = Command::new("timeout")
        .args([
            "-k",
            "5s",
            "90s",
            "cargo",
            "test",
            "--offline",
            "--manifest-path",
        ])
        .arg(root.join("crate/Cargo.toml"))
        .arg("--target-dir")
        .arg(root.join("target"))
        .args([
            "--locked",
            "-j",
            "1",
            "--test",
            "content13",
            "--",
            "--nocapture",
        ])
        .output()
        .unwrap();
    fs::write(
        root.join(format!("{label}.log")),
        [output.stdout.clone(), output.stderr.clone()].concat(),
    )
    .unwrap();
    let combined = format!(
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    if green {
        assert!(output.status.success(), "{label}: see {}", root.display());
    } else {
        assert!(
            output.status.code() == Some(101)
                && combined.contains("test result: FAILED")
                && combined.contains("... FAILED")
                && combined.contains("panicked at")
                && !combined.contains("0 passed; 0 failed"),
            "{label} did not produce an actual assertion RED: see {}",
            root.display()
        );
    }
    println!(
        "{label}: {}",
        if green {
            "GREEN"
        } else {
            "RED (assertion failure, not compile failure)"
        }
    );
}
fn replace_once(s: &str, old: &str, new: &str) -> String {
    assert_eq!(s.matches(old).count(), 1, "mutation anchor {old}");
    s.replacen(old, new, 1)
}
fn alter_first_string(v: &mut Value, old: &str, new: &str) -> bool {
    match v {
        Value::String(s) if s == old => {
            *s = new.to_owned();
            true
        }
        Value::Array(a) => a.iter_mut().any(|v| alter_first_string(v, old, new)),
        Value::Object(o) => o.values_mut().any(|v| alter_first_string(v, old, new)),
        _ => false,
    }
}
#[test]
#[ignore = "explicit bounded producer mutation qualification; creates only external scratch copies"]
fn real_producer_mutations_go_red_and_restored_control_green() {
    let root: PathBuf = std::env::temp_dir().join(format!(
        "narwhal-content13-mutants-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    copy(Path::new(env!("CARGO_MANIFEST_DIR")), &root.join("crate"));
    println!("private mutation logs: {}", root.display());
    let source = root.join("crate/src/content.rs");
    let original = fs::read_to_string(&source).unwrap();
    run(&root, "control-before", true);
    let inject = |code: &str| {
        let s = replace_once(
            &original,
            "let result = match op {",
            "let mut result = match op {",
        );
        replace_once(
            &s,
            "let bytes = serde_json::to_vec(&result)",
            &format!("{code}\n    let bytes = serde_json::to_vec(&result)"),
        )
    };
    let mutants=[
        ("macro-lost",inject("if op==0 && args.get(\"name\").and_then(Value::as_str)==Some(\"toolbar_macro_data\") {result[\"v\"][0][1][\"v\"].as_array_mut().unwrap().pop();}")),
        ("todo-inverted",replace_once(&original,"if !registered.contains(command) {","if registered.contains(command) {")),
        ("label-corrupted",replace_once(&original,"let mut label = self.strip(label).to_owned();","let mut label = format!(\"{} WRONG\", self.strip(label));")),
        ("icon-variant-wrong",inject("if op==0 && args.get(\"name\").and_then(Value::as_str)==Some(\"flyout_icon_data\") {result[\"v\"][0][1][\"v\"][0][1]=json!(\"wrong-variant.png\");}")),
        ("menu-order-reversed",replace_once(&original,"expanded.push(tup(vec![json!(menu), tup(rows)]));","rows.reverse(); expanded.push(tup(vec![json!(menu), tup(rows)]));")),
        ("full-casefold-lost",replace_once(&original,"result.push_str(f);","let _ = f; result.push(c);")),
    ];
    let walker_mutant = replace_once(
        &original,
        "output.push(row[3].clone());",
        "let _ = &row[3];",
    );
    fs::write(&source, walker_mutant).unwrap();
    run(&root, "ordered-walker-command-lost", false);
    fs::write(&source, &original).unwrap();
    for (label, mutant) in mutants {
        fs::write(&source, mutant).unwrap();
        run(&root, label, false);
        fs::write(&source, &original).unwrap();
    }
    // Rewriting data AND their claimed hashes must not bless new evidence.
    let data = root.join("crate/src/content_data/content.json");
    let golden = root.join("crate/tests/data/content13_golden.json");
    let provenance = root.join("crate/tests/data/content13_provenance.json");
    let data_original = fs::read(&data).unwrap();
    let golden_original = fs::read(&golden).unwrap();
    let provenance_original = fs::read(&provenance).unwrap();
    for path in [&data, &golden] {
        let mut v: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
        assert!(alter_first_string(&mut v, "New...", "WRONG New..."));
        fs::write(path, serde_json::to_vec(&v).unwrap()).unwrap();
    }
    let mut p: Value = serde_json::from_slice(&provenance_original).unwrap();
    p["content_sha256"] = Value::String(sha256::hash(&fs::read(&data).unwrap()));
    p["fixture_sha256"] = Value::String(sha256::hash(&fs::read(&golden).unwrap()));
    fs::write(&provenance, serde_json::to_vec(&p).unwrap()).unwrap();
    run(&root, "joint-data-fixture-provenance-forgery", false);
    fs::write(data, data_original).unwrap();
    fs::write(golden, golden_original).unwrap();
    fs::write(provenance, provenance_original).unwrap();
    run(&root, "control-restored", true);
}
