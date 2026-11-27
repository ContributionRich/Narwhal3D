use narwhal_source_tools::*;
use serde_json::{json, Value};
use std::{
    io::{self, Read},
    path::PathBuf,
};
fn main() {
    if let Err(e) = run() {
        eprintln!("SOURCE TOOLS ERROR: {e}");
        std::process::exit(2)
    }
}
fn report(v: Value) -> Result<(), String> {
    println!("{v}");
    if v["ok"] == false {
        std::process::exit(1)
    }
    Ok(())
}
fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("pure-gate") {
        let mut crate_path = "native/pure_core".to_string();
        let mut as_json = false;
        let mut i = 1;
        while i < args.len() {
            match args[i].as_str() {
                "--crate" => {
                    i += 1;
                    crate_path = args.get(i).ok_or("crate argument missing")?.clone()
                }
                "--json" => as_json = true,
                x => return Err(format!("unknown pure-core gate argument: {x}")),
            }
            i += 1;
        }
        let root = pure_gate::project_root();
        let request =
            json!({"op":"pure_gate","root":root,"crate":root.join(crate_path),"action":"run"});
        match dispatch(&request) {
            Ok(v) => {
                if as_json {
                    println!("{}", serde_json::to_string_pretty(&v).unwrap())
                } else {
                    println!(
                        "pure-core source gate: OK (crate {})",
                        v["crate"].as_str().unwrap_or("")
                    );
                    println!(
                        "{} Rust-supervised qualification steps",
                        v["steps"].as_object().map_or(0, |m| m.len())
                    );
                }
            }
            Err(e) => {
                if as_json {
                    println!(
                        "{}",
                        json!({"gate":"pure-core-source","ok":false,"error":e})
                    )
                } else {
                    eprintln!("pure-core source gate FAILED:\n{e}")
                }
                std::process::exit(1)
            }
        }
        return Ok(());
    }
    if args.first().map(String::as_str) == Some("rpc") {
        let mut input = Vec::new();
        io::stdin()
            .take(128 * 1024 * 1024 + 1)
            .read_to_end(&mut input)
            .map_err(|e| e.to_string())?;
        if input.len() > 128 * 1024 * 1024 {
            return Err("source-tools request limit exceeded".into());
        }
        let q: Value = serde_json::from_slice(&input).map_err(|e| e.to_string())?;
        let reply = match dispatch(&q) {
            Ok(v) => json!({"ok":true,"value":v}),
            Err(e) => json!({"ok":false,"error":e}),
        };
        println!("{reply}");
        return Ok(());
    }
    let mut root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .ok_or("source root missing")?
        .to_path_buf();
    let mut reference = None;
    let mut root_only = false;
    let mut timestamp = None;
    let mut message = "Initial public release of Narwhal 3D".to_string();
    let mut list = false;
    let mut self_check = false;
    let command = args.first().map(String::as_str).unwrap_or("list");
    let mut i = 1;
    while i < args.len() {
        let arg = args[i].as_str();
        match arg {
            "--root" => {
                i += 1;
                root = PathBuf::from(args.get(i).ok_or("root argument missing")?)
            }
            "--git-ref" => {
                i += 1;
                reference = Some(args.get(i).ok_or("ref argument missing")?.clone())
            }
            "--require-root-commit" => root_only = true,
            "--list" => list = true,
            "--self-check" => self_check = true,
            "--timestamp" => {
                i += 1;
                timestamp = Some(
                    args.get(i)
                        .ok_or("timestamp missing")?
                        .parse::<i64>()
                        .map_err(|_| "invalid timestamp")?,
                )
            }
            "--message" => {
                i += 1;
                message = args.get(i).ok_or("message missing")?.clone()
            }
            x => return Err(format!("unknown argument: {x}")),
        }
        i += 1
    }
    let root = root.canonicalize().map_err(|e| e.to_string())?;
    let c = config();
    match command {
        "test" => {
            let exe = std::env::current_exe().map_err(|e| e.to_string())?;
            let target = exe
                .parent()
                .and_then(|p| p.parent())
                .ok_or("test target missing")?;
            let status = std::process::Command::new("cargo")
                .args([
                    "+1.95.0",
                    "test",
                    "--release",
                    "--locked",
                    "--offline",
                    "-j",
                    "2",
                    "--manifest-path",
                ])
                .arg(root.join("native/source_tools/Cargo.toml"))
                .arg("--target-dir")
                .arg(target)
                .current_dir(&root)
                .status()
                .map_err(|e| e.to_string())?;
            if !status.success() {
                return Err("source-tools Rust contracts failed".into());
            }
        }
        "list" => {
            for p in public_files(&root, &c)? {
                println!("{p}")
            }
        }
        "create" => {
            let paths = public_files(&root, &c)?;
            if list {
                for p in paths {
                    println!("{p}")
                }
            } else {
                let commit = commit_root(
                    &root,
                    &paths,
                    &message,
                    timestamp.ok_or("--timestamp is required")?,
                    &c,
                )?;
                println!(
                    "created {} at {commit} with {} files",
                    c["BRANCH"].as_str().unwrap_or(""),
                    paths.len()
                );
            }
        }
        "project" | "completeness" | "self-check" => {
            return report(dispatch(
                &json!({"op":match command{"self-check"=>"self_check",x=>x},"root":root}),
            )?)
        }
        "audit" => {
            if self_check {
                return report(dispatch(&json!({"op":"self_check","root":root}))?);
            }
            if root_only && reference.is_none() {
                return Err("--require-root-commit requires --git-ref".into());
            }
            let entries = if let Some(r) = &reference {
                git_entries(&root, r)?
            } else {
                filesystem_entries(&root)?
            };
            let mut errors = audit_entries(&root, &entries, true, &c);
            if let Some(r) = reference.filter(|_| root_only) {
                if let Some(p) = root_commit_problem(&root, &r)? {
                    errors.insert(0, p)
                }
            }
            if errors.is_empty() {
                println!("PUBLIC TREE AUDIT PASSED: {} file(s)", entries.len())
            } else {
                eprintln!("PUBLIC TREE AUDIT FAILED: {} problem(s)", errors.len());
                for e in errors {
                    eprintln!("- {e}")
                }
                std::process::exit(1)
            }
        }
        _ => return Err(format!("unknown command: {command}")),
    }
    Ok(())
}
