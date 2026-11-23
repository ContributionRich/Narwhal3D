#![cfg(target_os = "linux")]
mod additional;
mod catalog;
mod discovery;
mod fixtures;
pub use additional::*;
pub use discovery::*;
pub use fixtures::*;
mod overrides;
pub use overrides::{OVERRIDE_SHA, applied_catalog, apply_variant};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
};
pub fn hash(b: &[u8]) -> String {
    format!("{:x}", Sha256::digest(b))
}
pub fn catalog() -> serde_json::Value {
    assert_eq!(
        hash(catalog::DATA.as_bytes()),
        catalog::SHA,
        "catalog drift"
    );
    let v: serde_json::Value = serde_json::from_str(catalog::DATA).unwrap();
    for (s, n) in [("box", 4), ("plan", 18), ("v4", 42)] {
        assert_eq!(v[s].as_array().unwrap().len(), n);
    }
    v
}
// Resolve existing ancestors before interpreting '..': symlink/../ escapes must not
// be normalized lexically. No output directories are created during validation.
pub fn resolve(p: &Path) -> Result<PathBuf, String> {
    use std::path::Component;
    if !p.is_absolute() {
        return Err("absolute path required".into());
    }
    let mut out = PathBuf::from("/");
    for c in p.components() {
        match c {
            Component::RootDir => {}
            Component::CurDir => {}
            Component::ParentDir => {
                out.pop();
            }
            Component::Normal(s) => {
                out.push(s);
                if fs::symlink_metadata(&out).is_ok() {
                    out = fs::canonicalize(&out).map_err(|e| e.to_string())?;
                }
            }
            _ => return Err("unsupported path".into()),
        }
    }
    Ok(out)
}
pub fn external(root: &Path, target: &Path) -> Result<PathBuf, String> {
    let t = resolve(target)?;
    let r = fs::canonicalize(root).map_err(|e| e.to_string())?;
    if t.starts_with(r) {
        return Err("target must be outside repository".into());
    }
    Ok(t)
}
pub fn snapshot(src: &Path, dst: &Path) -> Result<(), String> {
    fs::create_dir(dst).map_err(|e| e.to_string())?;
    fn copy(a: &Path, b: &Path) -> Result<(), String> {
        for ent in fs::read_dir(a).map_err(|e| e.to_string())? {
            let ent = ent.map_err(|e| e.to_string())?;
            let ty = ent.file_type().map_err(|e| e.to_string())?;
            let d = b.join(ent.file_name());
            if ty.is_symlink() {
                return Err("snapshot refuses symlinks".into());
            }
            if ty.is_dir() {
                fs::create_dir(&d).map_err(|e| e.to_string())?;
                copy(&ent.path(), &d)?;
            } else if ty.is_file() {
                fs::copy(ent.path(), d).map_err(|e| e.to_string())?;
            } else {
                return Err("nonregular source".into());
            }
        }
        Ok(())
    }
    fs::create_dir(dst.join("src")).map_err(|e| e.to_string())?;
    copy(&src.join("src"), &dst.join("src"))?;
    for n in ["Cargo.toml", "Cargo.lock"] {
        let p = src.join(n);
        if !fs::symlink_metadata(&p)
            .map_err(|e| e.to_string())?
            .is_file()
        {
            return Err("nonregular manifest".into());
        }
        fs::copy(p, dst.join(n)).map_err(|e| e.to_string())?;
    }
    let manifest = fs::read_to_string(dst.join("Cargo.toml")).map_err(|e| e.to_string())?;
    if src.join("build.rs").exists() || manifest.contains("build =") || manifest.contains("path =")
    {
        return Err("unsupported build hook/path dependency".into());
    }
    Ok(())
}
pub fn source_hash(src: &Path) -> String {
    let mut paths: Vec<_> = fs::read_dir(src.join("src"))
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|s| s == "rs"))
        .collect();
    paths.sort();
    let mut bytes = Vec::new();
    for p in paths {
        bytes.extend(p.file_name().unwrap().as_encoded_bytes());
        bytes.extend(fs::read(p).unwrap());
    }
    hash(&bytes)
}
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Status {
    Killed,
    Survived,
    InfraFailure,
}
/// The CLI uses this same boundary for every failed anchor/build attempt.
pub fn attempt_status(result: &Result<Status, String>) -> Status {
    result.as_ref().copied().unwrap_or(Status::InfraFailure)
}
pub fn positive(out: &str, rust: bool) -> bool {
    if rust {
        out.split("test result: ok. ").skip(1).any(|s| {
            s.split_once(" passed;")
                .and_then(|(n, _)| n.parse::<usize>().ok())
                .is_some_and(|n| n > 0)
        })
    } else {
        out.contains("\nOK") && known_cases(out)
    }
}
fn known_cases(out: &str) -> bool {
    out.lines().any(|s| {
        s.starts_with("Ran ")
            && s.split_whitespace()
                .nth(1)
                .and_then(|n| n.parse::<usize>().ok())
                .is_some_and(|n| n > 0)
    }) && out.contains("test_")
}
pub fn classify(code: i32, out: &str, rust: bool) -> Status {
    if code == 0 {
        return if positive(out, rust) {
            Status::Survived
        } else {
            Status::InfraFailure
        };
    }
    let rust_assertion = code == 101
        && rust
        && out.contains("test result: FAILED")
        && out.contains("panicked at")
        && out.contains("... FAILED");
    let host_assertion = code == 1
        && !rust
        && known_cases(out)
        && out.contains("FAILED (")
        && out.contains("FAIL: test_")
        && out.contains("AssertionError");
    if rust_assertion || host_assertion {
        Status::Killed
    } else {
        Status::InfraFailure
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn catalog_guard() {
        catalog();
    }
    #[test]
    fn classifier_rejects_compilation_failure() {
        assert_eq!(
            classify(101, "error[E0001]: failed to compile", true),
            Status::InfraFailure
        );
    }
    #[test]
    fn classification() {
        assert_eq!(
            classify(
                101,
                "test x ... FAILED\nx ... FAILED\nthread panicked at x\ntest result: FAILED",
                true
            ),
            Status::Killed
        );
        for (c, s) in [
            (0, "test result: ok. 0 passed;"),
            (124, "panicked at\ntest result: FAILED"),
        ] {
            assert_eq!(classify(c, s, true), Status::InfraFailure);
        }
        assert_eq!(
            classify(
                1,
                "test_x ... FAIL\nFAIL: test_x\nAssertionError\nRan 1 test\nFAILED (failures=1)",
                false
            ),
            Status::Killed
        );
        assert_eq!(
            classify(1, "ImportError\nFAILED (errors=1)", false),
            Status::InfraFailure
        );
    }
    #[test]
    fn paths_and_snapshot() {
        let base =
            std::env::temp_dir().join(format!("automation-driver-test-{}", std::process::id()));
        fs::create_dir_all(base.join("root/src")).unwrap();
        let r = base.join("root");
        std::os::unix::fs::symlink(&r, base.join("link")).unwrap();
        assert!(external(&r, &base.join("link/missing/../output")).is_err());
        fs::write(r.join("Cargo.toml"), "[package]").unwrap();
        fs::write(r.join("Cargo.lock"), "").unwrap();
        fs::write(r.join("src/a.rs"), "original").unwrap();
        fs::write(r.join("hook"), "ignore").unwrap();
        snapshot(&r, &base.join("copy")).unwrap();
        fs::write(base.join("copy/src/a.rs"), "mutant").unwrap();
        assert_eq!(fs::read_to_string(r.join("src/a.rs")).unwrap(), "original");
        assert!(!base.join("copy/hook").exists());
        fs::remove_dir_all(base).unwrap();
    }
}

pub fn scope_args(cap: u64, build: bool, unit: &str) -> Result<Vec<String>, String> {
    if ![1024, 1536].contains(&cap) {
        return Err("unsupported resident cap".into());
    }
    Ok([
        "--user",
        "--scope",
        "--quiet",
        "--unit",
        unit,
        "-p",
        &format!("MemoryMax={cap}M"),
        "-p",
        "MemorySwapMax=0",
        "-p",
        "RuntimeMaxSec=175s",
        "-p",
        "TimeoutStopSec=2s",
        "-p",
        "KillMode=control-group",
        "timeout",
        "--signal=TERM",
        "--kill-after=2s",
        if build { "170s" } else { "30s" },
    ]
    .into_iter()
    .map(str::to_owned)
    .collect())
}
pub fn headroom(kib: u64, cap: u64) -> bool {
    [1024, 1536].contains(&cap) && kib >= (4096 + cap) * 1024
}
pub fn output_tail(mut input: impl std::io::Read) -> std::io::Result<Vec<u8>> {
    let mut buf = std::collections::VecDeque::new();
    let mut chunk = [0; 8192];
    loop {
        let n = input.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        for byte in &chunk[..n] {
            if buf.len() == 524288 {
                buf.pop_front();
            }
            buf.push_back(*byte);
        }
    }
    Ok(buf.into_iter().collect())
}
#[cfg(test)]
mod resource_tests {
    use super::*;
    #[test]
    fn bounds() {
        for cap in [1024, 1536] {
            let a = scope_args(cap, true, "test-unit").unwrap();
            for expected in [
                format!("MemoryMax={cap}M"),
                "MemorySwapMax=0".into(),
                "RuntimeMaxSec=175s".into(),
                "TimeoutStopSec=2s".into(),
                "KillMode=control-group".into(),
                "170s".into(),
                "--kill-after=2s".into(),
            ] {
                assert!(a.contains(&expected));
            }
            assert!(!headroom((4096 + cap) * 1024 - 1, cap));
            assert!(headroom((4096 + cap) * 1024, cap));
        }
        assert!(scope_args(2048, true, "test").is_err());
        let input = vec![b'x'; 1048576];
        let tail = output_tail(input.as_slice()).unwrap();
        assert_eq!(tail.len(), 524288);
    }
}
