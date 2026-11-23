//! Read-only equivalent of the retained SDK adapter's trusted local discovery.
use crate::{external, hash};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
};
pub const BRIDGE_TEST: &str = "v4_tests::v4_main_publisher_bridge";
pub fn file_hash(path: &Path) -> Result<String, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let mut h = Sha256::new();
    let mut buf = [0; 65536];
    loop {
        let n = f.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(format!("{:x}", h.finalize()))
}
pub fn input_hash(crate_: &Path) -> Result<String, String> {
    fn collect(
        root: &Path,
        dir: &Path,
        recursive: bool,
        map: &mut BTreeMap<String, String>,
    ) -> Result<(), String> {
        for e in fs::read_dir(dir).map_err(|e| e.to_string())? {
            let p = e.map_err(|e| e.to_string())?.path();
            // Root glob is only *.rs: unrelated build/cache links are not inputs.
            if !recursive && p.extension().is_none_or(|s| s != "rs") {
                continue;
            }
            if p.is_symlink() {
                return Err("source symlink refused".into());
            }
            if p.is_dir() && recursive {
                collect(root, &p, true, map)?;
            } else if p.is_file() && p.extension().is_some_and(|s| s == "rs") {
                map.insert(
                    p.strip_prefix(root)
                        .map_err(|e| e.to_string())?
                        .to_str()
                        .ok_or("non UTF-8 source path")?
                        .to_owned(),
                    file_hash(&p)?,
                );
            }
        }
        Ok(())
    }
    let mut inventory = BTreeMap::new();
    for f in ["Cargo.toml", "Cargo.lock"] {
        inventory.insert(f.into(), file_hash(&crate_.join(f))?);
    }
    collect(crate_, crate_, false, &mut inventory)?;
    collect(crate_, &crate_.join("src"), true, &mut inventory)?;
    Ok(hash(
        &serde_json::to_vec(&inventory).map_err(|e| e.to_string())?,
    ))
}
// The receipt schema is deliberately flat. Streaming each key/value independently
// preserves duplicate-key rejection without introducing another JSON dependency.
fn strict_record(raw: &[u8]) -> Result<BTreeMap<String, Value>, String> {
    if raw.len() > 8192 {
        return Err("discovery record exceeds size limit".into());
    }
    let mut pos = 0;
    let mut map = BTreeMap::new();
    fn ws(raw: &[u8], pos: &mut usize) {
        while raw
            .get(*pos)
            .is_some_and(|b| matches!(b, b' ' | b'\t' | b'\r' | b'\n'))
        {
            *pos += 1;
        }
    }
    ws(raw, &mut pos);
    if raw.get(pos) != Some(&b'{') {
        return Err("record must be an object".into());
    }
    pos += 1;
    loop {
        ws(raw, &mut pos);
        if raw.get(pos) == Some(&b'}') {
            pos += 1;
            break;
        }
        let mut keys = serde_json::Deserializer::from_slice(&raw[pos..]).into_iter::<String>();
        let key = keys
            .next()
            .ok_or("missing record key")?
            .map_err(|e| e.to_string())?;
        pos += keys.byte_offset();
        ws(raw, &mut pos);
        if raw.get(pos) != Some(&b':') {
            return Err("missing record colon".into());
        }
        pos += 1;
        ws(raw, &mut pos);
        let mut values = serde_json::Deserializer::from_slice(&raw[pos..]).into_iter::<Value>();
        let value = values
            .next()
            .ok_or("missing record value")?
            .map_err(|e| e.to_string())?;
        pos += values.byte_offset();
        if map.insert(key, value).is_some() {
            return Err("duplicate discovery record field".into());
        }
        ws(raw, &mut pos);
        match raw.get(pos) {
            Some(b',') => {
                pos += 1;
                ws(raw, &mut pos);
                if raw.get(pos) == Some(&b'}') {
                    return Err("trailing record comma".into());
                }
            }
            Some(b'}') => {
                pos += 1;
                break;
            }
            _ => return Err("invalid discovery separator".into()),
        }
    }
    ws(raw, &mut pos);
    if pos != raw.len() {
        return Err("trailing discovery data".into());
    }
    Ok(map)
}
pub fn validate_discovery(root: &Path, target: &Path) -> Result<PathBuf, String> {
    let target = external(root, target)?;
    let mut raw = Vec::new();
    File::open(target.join("v4-publisher-bridge.json"))
        .map_err(|e| e.to_string())?
        .take(8193)
        .read_to_end(&mut raw)
        .map_err(|e| e.to_string())?;
    let row = strict_record(&raw)?;
    let keys = [
        "executable",
        "executable_sha256",
        "library_sha256",
        "source_sha256",
        "test",
        "version",
    ];
    if row.len() != keys.len()
        || !keys.iter().all(|key| row.contains_key(*key))
        || row["version"].as_u64() != Some(1)
        || row["test"] != BRIDGE_TEST
    {
        return Err("invalid or obsolete discovery record; rebuild explicitly".into());
    }
    for key in ["source_sha256", "executable_sha256", "library_sha256"] {
        let s = row[key].as_str().ok_or("invalid discovery hash type")?;
        if s.len() != 64
            || !s
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(format!("invalid discovery hash: {key}"));
        }
    }
    let executable = PathBuf::from(
        row["executable"]
            .as_str()
            .ok_or("invalid executable type")?,
    );
    let canonical = fs::canonicalize(&executable).map_err(|e| e.to_string())?;
    let library = target.join("debug/libnarwhal_automation.so");
    if canonical != executable
        || executable.parent() != Some(target.join("debug/deps").as_path())
        || fs::canonicalize(&library).map_err(|e| e.to_string())? != library
    {
        return Err("discovery artifact path mismatch".into());
    }
    if row["source_sha256"] != input_hash(&root.join("native/automation_core"))? {
        return Err("stale native source/Cargo discovery hash; rebuild explicitly".into());
    }
    if row["executable_sha256"] != file_hash(&executable)? {
        return Err("stale native test executable discovery hash; rebuild explicitly".into());
    }
    if row["library_sha256"] != file_hash(&library)? {
        return Err("stale normal library discovery hash; rebuild explicitly".into());
    }
    Ok(executable)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn strict_json() {
        for raw in [
            b"{\"x\":1,\"x\":2}".as_slice(),
            b"{\"x\":1,}",
            b"[]",
            b"\x0b{}",
            b"{} null",
            b"{\"x\":",
        ] {
            assert!(strict_record(raw).is_err());
        }
        assert!(strict_record(&vec![b' '; 8193]).is_err());
        assert_eq!(strict_record(b" {\"version\":1} ").unwrap()["version"], 1);
    }
    #[test]
    fn discovery_rejects_stale_forged_and_missing_records() {
        let base = std::env::temp_dir().join(format!("driver-discovery-{}", std::process::id()));
        let root = base.join("root");
        let target = base.join("target");
        fs::create_dir_all(root.join("native/automation_core/src")).unwrap();
        fs::create_dir_all(target.join("debug/deps")).unwrap();
        for (f, s) in [
            ("Cargo.toml", "package"),
            ("Cargo.lock", "lock"),
            ("src/a.rs", "rust"),
        ] {
            fs::write(root.join("native/automation_core").join(f), s).unwrap();
        }
        std::os::unix::fs::symlink(&target, root.join("native/automation_core/target")).unwrap();
        let exe = target.join("debug/deps/test");
        let lib = target.join("debug/libnarwhal_automation.so");
        fs::write(&exe, "executable").unwrap();
        fs::write(&lib, "library").unwrap();
        let record = target.join("v4-publisher-bridge.json");
        assert!(validate_discovery(&root, &target).is_err());
        let row = serde_json::json!({"version":1,"test":BRIDGE_TEST,"executable":exe,"source_sha256":input_hash(&root.join("native/automation_core")).unwrap(),"executable_sha256":file_hash(&exe).unwrap(),"library_sha256":file_hash(&lib).unwrap()});
        fs::write(&record, serde_json::to_vec(&row).unwrap()).unwrap();
        assert_eq!(validate_discovery(&root, &target).unwrap(), exe);
        for (key, value) in [
            ("version", serde_json::json!(true)),
            ("version", serde_json::json!(1.0)),
            ("test", serde_json::json!("wrong")),
            ("source_sha256", serde_json::json!("0".repeat(64))),
            ("executable_sha256", serde_json::json!("0".repeat(64))),
            ("library_sha256", serde_json::json!("0".repeat(64))),
            ("extra", serde_json::json!(0)),
        ] {
            let mut bad = row.clone();
            bad[key] = value;
            fs::write(&record, serde_json::to_vec(&bad).unwrap()).unwrap();
            assert!(validate_discovery(&root, &target).is_err(), "{key}");
        }
        fs::write(&record, serde_json::to_vec(&row).unwrap()).unwrap();
        fs::write(root.join("native/automation_core/src/new.rs"), "new").unwrap();
        assert!(validate_discovery(&root, &target).is_err());
        fs::remove_file(root.join("native/automation_core/src/new.rs")).unwrap();
        fs::remove_file(&lib).unwrap();
        std::os::unix::fs::symlink(&exe, &lib).unwrap();
        assert!(validate_discovery(&root, &target).is_err());
        fs::remove_dir_all(base).unwrap();
    }
}
