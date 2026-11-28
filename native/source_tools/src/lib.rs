mod audit_cases;
pub mod checks;
pub mod pdf_bundle;
pub mod policy;
pub mod pure_abi;
pub mod pure_gate;
pub mod python_sdk;
mod strict_json;
use base64::{engine::general_purpose::STANDARD, Engine};
use regex::{bytes::Regex as BytesRegex, Regex};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    process::{Command, Output},
    time::{SystemTime, UNIX_EPOCH},
};
pub type Entries = BTreeMap<String, (String, Vec<u8>)>;
const RECURSIVE_PUBLIC_DIRS: [&str; 7] = [
    "narwhal3d",
    "packaging",
    "licenses",
    ".github",
    "artwork",
    "native/vendor/tracedraw",
    "native/picture_trace",
];
/// OURS: fail closed on transient editor/atomic-writer payloads in recursively
/// selected source directories. Explicit fixtures elsewhere retain their policy.
pub fn transient_publication_problem(path: &str) -> Option<String> {
    if !RECURSIVE_PUBLIC_DIRS.iter().any(|dir| {
        path.strip_prefix(dir)
            .is_some_and(|tail| tail.starts_with('/'))
    }) {
        return None;
    }
    let name = path.rsplit('/').next().unwrap_or("");
    let folded = name.to_ascii_lowercase();
    if [".tmp", ".swp", ".swo", ".bak"]
        .iter()
        .any(|suffix| folded.ends_with(suffix))
        || name.ends_with('~')
        || (name.starts_with('#') && name.ends_with('#') && name.len() > 2)
        || (name.starts_with(".#") && name.len() > 2)
    {
        Some("unreviewed transient file in recursive publication directory".into())
    } else {
        None
    }
}

pub fn config() -> Value {
    let mut p: Value =
        serde_json::from_str(include_str!("publication.json")).expect("publication policy");
    let a: Value = serde_json::from_str(include_str!("audit.json")).expect("audit policy");
    p.as_object_mut()
        .unwrap()
        .extend(a.as_object().unwrap().clone());
    p.as_object_mut()
        .unwrap()
        .extend(pure_gate::metadata().as_object().unwrap().clone());
    materialize(p)
}
fn materialize(v: Value) -> Value {
    match v {
        Value::Object(m) if m.contains_key("text_parts") => Value::String(
            seq(&m["text_parts"])
                .into_iter()
                .map(materialize)
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect::<Vec<_>>()
                .concat(),
        ),
        Value::Object(m) => {
            Value::Object(m.into_iter().map(|(k, v)| (k, materialize(v))).collect())
        }
        Value::Array(a) => Value::Array(a.into_iter().map(materialize).collect()),
        v => v,
    }
}
pub fn seq(v: &Value) -> Vec<Value> {
    if let Some(a) = v.as_array() {
        a.clone()
    } else {
        v.get("$set")
            .or_else(|| v.get("$tuple"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }
}
pub fn strings(v: &Value) -> Vec<String> {
    seq(v)
        .iter()
        .filter_map(Value::as_str)
        .map(str::to_owned)
        .collect()
}
pub fn bytes(v: &Value) -> Vec<u8> {
    if let Some(s) = v.get("bytes").and_then(Value::as_str) {
        s.as_bytes().to_vec()
    } else if let Some(s) = v.get("$bytes").and_then(Value::as_str) {
        STANDARD.decode(s).unwrap_or_default()
    } else if v.get("bytes_parts").is_some() {
        strings(&v["bytes_parts"]).concat().into_bytes()
    } else {
        Vec::new()
    }
}
pub fn wire(v: &Value) -> Value {
    match v {
        Value::Object(m)
            if m.get("bytes").is_some_and(Value::is_string) || m.contains_key("bytes_parts") =>
        {
            json!({"$bytes":STANDARD.encode(bytes(v))})
        }
        Value::Object(m) if m.contains_key("regex") => {
            json!({"$regex":wire(&m["regex"]),"flags":m["flags"]})
        }
        Value::Object(m) => Value::Object(m.iter().map(|(k, v)| (k.clone(), wire(v))).collect()),
        Value::Array(a) => Value::Array(a.iter().map(wire).collect()),
        _ => v.clone(),
    }
}
pub fn sha(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}
fn contains(data: &[u8], needle: &[u8]) -> bool {
    needle.is_empty() || data.windows(needle.len()).any(|w| w == needle)
}
fn lower(data: &[u8]) -> Vec<u8> {
    data.iter().map(u8::to_ascii_lowercase).collect()
}
fn parts(path: &str) -> Vec<&str> {
    path.split('/')
        .filter(|p| !p.is_empty() && *p != ".")
        .collect()
}
fn suffix(path: &str) -> String {
    let last = path.rsplit('/').next().unwrap_or("");
    match last.rfind('.') {
        Some(i) if i > 0 && i + 1 < last.len() => last[i..].to_string(),
        _ => String::new(),
    }
}
fn bytes_repr(data: &[u8]) -> String {
    let quote = if data.contains(&b'\'') && !data.contains(&b'"') {
        b'"'
    } else {
        b'\''
    };
    let mut out = String::from("b");
    out.push(quote as char);
    for &b in data {
        match b {
            b'\\' => out.push_str("\\\\"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            b'\t' => out.push_str("\\t"),
            b if b == quote => {
                out.push('\\');
                out.push(b as char)
            }
            32..=126 => out.push(b as char),
            _ => out.push_str(&format!("\\x{b:02x}")),
        }
    }
    out.push(quote as char);
    out
}
// Python fnmatchcase: '*' spans separators, bracket classes are case-sensitive.
pub fn matches(pattern: &str, text: &str) -> bool {
    let mut rx = String::from("(?s)\\A");
    let chars: Vec<char> = pattern.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        match chars[i] {
            '*' => rx.push_str(".*"),
            '?' => rx.push('.'),
            '[' => {
                let start = i;
                let mut j = i + 1;
                if j < chars.len() && chars[j] == '!' {
                    j += 1;
                }
                if j < chars.len() && chars[j] == ']' {
                    j += 1;
                }
                while j < chars.len() && chars[j] != ']' {
                    j += 1;
                }
                if j == chars.len() {
                    rx.push_str("\\[")
                } else {
                    rx.push('[');
                    let mut k = start + 1;
                    if chars[k] == '!' {
                        rx.push('^');
                        k += 1;
                    } else if chars[k] == '^' {
                        rx.push('\\');
                    }
                    for &c in &chars[k..j] {
                        if c == '\\' {
                            rx.push_str("\\\\")
                        } else {
                            rx.push(c)
                        }
                    }
                    rx.push(']');
                    i = j;
                }
            }
            c => rx.push_str(&regex::escape(&c.to_string())),
        }
        i += 1;
    }
    rx.push_str("\\z");
    Regex::new(&rx).is_ok_and(|r| r.is_match(text))
}
pub fn retirement_path_problem(path: &str) -> Option<String> {
    let s = path.replace('\\', "/");
    let p = parts(&s);
    if p.iter().any(|v| {
        let v = v.to_lowercase();
        v == "legacy"
            || v.starts_with("retired-python-")
            || ["legacy.zip", "legacy.tar", "legacy.tar.gz", "legacy.tgz"].contains(&v.as_str())
    }) {
        return Some("retired archive is local-only".into());
    }
    if p.last().is_some_and(|v| {
        [
            "textfield_168212d4.py",
            "test_ui_core_golden.py",
            "test_rust_history_trace.py",
            "test_ai_box_native.py",
            "test_ai_plan_native.py",
            "test_ai_v4_native.py",
            "test_ai_v6_review.py",
            "test_ai_v7_native.py",
            "test_ai_v8_native.py",
        ]
        .contains(v)
    }) {
        return Some("retired executable reference".into());
    }
    None
}
fn legacy_match(data: &[u8], word: bool) -> Option<Vec<u8>> {
    let assembled = [b"rhi".as_slice(), b"no"].concat();
    let needle = assembled.as_slice();
    let mut start = 0;
    while start + needle.len() <= data.len() {
        let at = data[start..]
            .windows(needle.len())
            .position(|w| w == needle)?
            + start;
        let boundary =
            !word || at == 0 || !(data[at - 1].is_ascii_alphanumeric() || data[at - 1] == b'_');
        if boundary && !data[at + 5..].starts_with(b"3dm") {
            return Some(needle.to_vec());
        }
        start = at + 1;
    }
    None
}
pub fn path_problem(path: &str, c: &Value) -> Option<String> {
    if let Some(problem) = transient_publication_problem(path) {
        return Some(problem);
    }
    if let Some(p) = retirement_path_problem(path) {
        return Some(p);
    }
    if path.starts_with('/') || parts(path).contains(&"..") || path.contains('\\') {
        return Some("unsafe path spelling".into());
    }
    if legacy_match(&lower(path.as_bytes()), false).is_some() {
        return Some("legacy product name in public path".into());
    }
    if strings(&c["FORBIDDEN_EXACT"]).iter().any(|s| s == path) {
        return Some("forbidden private or excluded component".into());
    }
    if strings(&c["FORBIDDEN_PREFIXES"])
        .iter()
        .any(|s| path.starts_with(s))
    {
        return Some("forbidden private directory".into());
    }
    if strings(&c["FORBIDDEN_GLOBS"])
        .iter()
        .any(|s| matches(s, path))
    {
        return Some("commercial research/mining tool".into());
    }
    if strings(&c["IMAGE_SUFFIXES"]).contains(&suffix(path).to_lowercase())
        && !strings(&c["ALLOWED_IMAGES"])
            .iter()
            .any(|p| matches(p, path))
    {
        return Some("unexpected image or artwork".into());
    }
    None
}
fn walk_values(v: &Value) -> Vec<&Value> {
    let mut out = Vec::new();
    python_sdk::walk(v, &mut out);
    out
}
pub fn runtime_archive_problem(root: &Path, blob: &[u8]) -> Option<String> {
    if let Err(e) = python_sdk::validate(root, b"", "<admission>") {
        return Some(format!("compiler SDK admission failed: {e}"));
    }

    if BytesRegex::new(r"legacy[/\\]retired-python-")
        .unwrap()
        .is_match(&lower(blob))
    {
        return Some("runtime artifact references the retirement archive".into());
    }
    let tree = python_sdk::parse(root, blob, "<unknown>").ok()?;
    for n in walk_values(&tree) {
        let mut names = Vec::new();
        if n["_type"] == "Import" {
            for v in n["names"].as_array().into_iter().flatten() {
                names.push(v["name"].as_str().unwrap_or(""));
            }
        } else if n["_type"] == "ImportFrom" {
            names.push(n["module"].as_str().unwrap_or(""));
            for v in n["names"].as_array().into_iter().flatten() {
                names.push(v["name"].as_str().unwrap_or(""));
            }
        }
        if names
            .iter()
            .any(|s| s.split('.').any(|p| p == "legacy") || s.contains("retired-python-"))
        {
            return Some("runtime imports the retirement archive".into());
        }
        if n["_type"] == "Constant" {
            let value = &n["value"];
            let text = if let Some(s) = value.as_str() {
                s.to_owned()
            } else if let Some(a) = value["_bytes"].as_array() {
                String::from_utf8_lossy(
                    &a.iter()
                        .map(|x| x.as_u64().unwrap_or(0) as u8)
                        .collect::<Vec<_>>(),
                )
                .into_owned()
            } else {
                continue;
            };
            if retirement_path_problem(&text).is_some() {
                return Some("runtime resolves a retirement archive path".into());
            }
        }
    }
    None
}
pub fn required_public_paths(root: &Path, entries: &Entries, c: &Value) -> BTreeSet<String> {
    let mut req: BTreeSet<_> = strings(&c["REQUIRED"]).into_iter().collect();
    let mut pending = vec!["run_tests.sh".to_string(), "uishot.sh".to_string()];
    pending.extend(req.iter().filter(|p| p.starts_with("spike/")).cloned());
    let mut visited = BTreeSet::new();
    let source =
        Regex::new(r"\b(?:spike|scripts|native|launch)/[\w./-]+\.(?:py|sh|rs|toml)").unwrap();
    let word = Regex::new(r"^[\w.]").unwrap();
    while let Some(path) = pending.pop() {
        if transient_publication_problem(&path).is_some() {
            continue;
        }
        if !visited.insert(path.clone()) {
            continue;
        }
        let Some((_, blob)) = entries.get(&path) else {
            continue;
        };
        let mut deps = BTreeSet::new();
        if path.ends_with(".sh") {
            let text = String::from_utf8_lossy(blob)
                .lines()
                .filter(|l| !l.trim_start().starts_with('#'))
                .collect::<Vec<_>>()
                .join("\n");
            for m in source.find_iter(&text) {
                if !word.is_match(&text[m.end()..]) {
                    deps.insert(m.as_str().to_string());
                }
            }
        } else if path.ends_with(".py") {
            if let Ok(tree) = python_sdk::parse(root, blob, &path) {
                for n in walk_values(&tree) {
                    let names = if n["_type"] == "Import" {
                        n["names"]
                            .as_array()
                            .into_iter()
                            .flatten()
                            .map(|n| n["name"].as_str().unwrap_or(""))
                            .collect::<Vec<_>>()
                    } else if n["_type"] == "ImportFrom" {
                        vec![n["module"].as_str().unwrap_or("")]
                    } else {
                        Vec::new()
                    };
                    for name in names {
                        if name.starts_with("test_") {
                            deps.insert(
                                c["TEST_SUPPORT_IMPORTS"][name]
                                    .as_str()
                                    .map(str::to_owned)
                                    .unwrap_or_else(|| {
                                        format!("spike/{}.py", name.replace('.', "/"))
                                    }),
                            );
                        }
                    }
                }
            }
        }
        pending.extend(deps.iter().filter(|p| !visited.contains(*p)).cloned());
        req.extend(deps);
    }
    req
}
pub fn legacy_scan_bytes(path: &str, blob: &[u8]) -> Vec<u8> {
    let mut out = lower(blob);
    let sentence = if [
        "native/application_core/src/ordinal_map.rs",
        "native/application_core/generate_ordinal.rs",
    ]
    .contains(&path)
    {
        Some(concat!(
            "OpenNURBS, Rhi",
            concat!(
                "noceros, and Rhi",
                "no3D are registered trademarks of Robert"
            )
        ))
    } else if path == "native/application_core/OPENNURBS_LICENSE" {
        Some(concat!(
            "Rhi",
            "noceros is a registered trademark of Robert McNeel & Associates."
        ))
    } else {
        None
    };
    if let Some(s) = sentence {
        out = replace(
            &out,
            s.to_ascii_lowercase().as_bytes(),
            b"upstream trademark attribution",
        );
    }
    out
}
fn replace(blob: &[u8], from: &[u8], to: &[u8]) -> Vec<u8> {
    let mut result = Vec::new();
    let mut at = 0;
    while at < blob.len() {
        if blob[at..].starts_with(from) {
            result.extend(to);
            at += from.len();
        } else {
            result.push(blob[at]);
            at += 1;
        }
    }
    result
}
fn py_json(v: &Value) -> Vec<u8> {
    let raw = serde_json::to_string(v).unwrap();
    let mut out = String::new();
    let (mut quoted, mut escaped) = (false, false);
    for ch in raw.chars() {
        out.push(ch);
        if quoted {
            if escaped {
                escaped = false
            } else if ch == '\\' {
                escaped = true
            } else if ch == '"' {
                quoted = false
            }
        } else if ch == '"' {
            quoted = true
        } else if ch == ',' || ch == ':' {
            out.push(' ');
        }
    }
    out.into_bytes()
}
// CPython JSON accepts lone UTF-16 surrogate escapes. The frozen schema only
// inspects string types; normalize those code units after checking the RAW hash.
fn json_surrogates(blob: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(blob.len());
    let mut i = 0;
    while i < blob.len() {
        if blob[i..].starts_with(b"\\u") && i + 6 <= blob.len() {
            let unit = std::str::from_utf8(&blob[i + 2..i + 6])
                .ok()
                .and_then(|s| u16::from_str_radix(s, 16).ok());
            if let Some(u) = unit.filter(|u| (0xd800..=0xdfff).contains(u)) {
                let low = if i + 12 <= blob.len() && blob[i + 6..].starts_with(b"\\u") {
                    std::str::from_utf8(&blob[i + 8..i + 12])
                        .ok()
                        .and_then(|s| u16::from_str_radix(s, 16).ok())
                } else {
                    None
                };
                if u <= 0xdbff && low.is_some_and(|v| (0xdc00..=0xdfff).contains(&v)) {
                    out.extend_from_slice(&blob[i..i + 12]);
                    i += 12;
                } else {
                    out.extend_from_slice(b"\\ufffd");
                    i += 6;
                }
                continue;
            }
        }
        if blob[i] == b'\\' && i + 1 < blob.len() {
            out.extend_from_slice(&blob[i..i + 2]);
            i += 2;
        } else {
            out.push(blob[i]);
            i += 1;
        }
    }
    out
}
pub fn frozen_scan(name: &str, path: &str, blob: &[u8], c: &Value) -> Result<Vec<u8>, String> {
    if name == "frozen_data_scan" {
        return Ok(blob.to_vec());
    }
    let (field, error, kind) = match name {
        "frozen_dxf23_scan" => (
            "FROZEN_DXF23_FILES",
            "DXF23 frozen capture size/provenance hash mismatch",
            "independently captured DXF writer data",
        ),
        "frozen_preview24_scan" => (
            "FROZEN_PREVIEW24_FILES",
            "preview frozen capture size/provenance hash mismatch",
            "independent original preview float-bit data",
        ),
        "frozen_selection_scan" => (
            "FROZEN_SELECTION_FILES",
            "selection frozen capture size/provenance hash mismatch",
            "independent original selection input/result data",
        ),
        "frozen_nearest_scan" => (
            "FROZEN_NEAREST_FILES",
            "nearest frozen capture size/provenance hash mismatch",
            "independent original nearest SDK answers/transcripts",
        ),
        "frozen_json_scan" => (
            "FROZEN_JSON_FILES",
            "frozen JSON DATA provenance hash mismatch",
            "",
        ),
        _ => return Err("unknown frozen scan".into()),
    };
    let value = &c[field][path];
    if value.is_null() {
        return Ok(blob.to_vec());
    }
    let digest = sha(blob);
    if name == "frozen_json_scan" {
        if value["sha256"].as_str() != Some(&digest) {
            return Err(error.into());
        }
        let data: Value = strict_json::parse(&json_surrogates(blob)).map_err(|e| e.to_string())?;
        let versions = value["rows"]
            .as_object()
            .ok_or("unexpected frozen DATA Unicode versions")?;
        if data.as_object().map(|o| o.keys().collect::<BTreeSet<_>>())
            != Some(versions.keys().collect())
        {
            return Err("unexpected frozen DATA Unicode versions".into());
        }
        for (version, count) in versions {
            let rows = data[version]
                .as_array()
                .ok_or("frozen DATA row count mismatch")?;
            if rows.len() as u64 != count.as_u64().unwrap_or(u64::MAX) {
                return Err("frozen DATA row count mismatch".into());
            }
            for row in rows {
                let a = row.as_array().ok_or("invalid numeric DATA row")?;
                if a.len() != 3
                    || !a[0].is_string()
                    || !(a[1].is_null() || a[1].is_string())
                    || a[2].as_array().is_none_or(|a| a.len() != 2)
                {
                    return Err("invalid numeric DATA row".into());
                }
                let v = &a[2][0];
                let e = &a[2][1];
                if !((v.is_number() && v.as_f64().is_some_and(f64::is_finite) && e.is_null())
                    || (v.is_null() && e.is_string()))
                {
                    return Err("invalid numeric DATA result".into());
                }
            }
        }
        return Ok(py_json(value));
    }
    let a = seq(value);
    if a.len() != 2 || a[0].as_u64() != Some(blob.len() as u64) || a[1].as_str() != Some(&digest) {
        return Err(error.into());
    }
    let mut meta = json!({"kind":kind,"size":blob.len(),"sha256":digest});
    if name == "frozen_dxf23_scan" {
        meta["source_sha256"] =
            "6450fa373ebc366e23053a71fbd6efeb6cde94922ab9c752d926e55a10cdacbc".into();
    }
    Ok(py_json(&meta))
}
pub fn rust_code_mask(blob: &[u8]) -> Vec<u8> {
    let mut code = blob.to_vec();
    let mut i = 0;
    while i < blob.len() {
        let start = i;
        if blob[i..].starts_with(b"//") {
            while i < blob.len() && blob[i] != b'\n' {
                i += 1;
            }
        } else if blob[i..].starts_with(b"/*") {
            let mut depth = 1;
            i += 2;
            while i < blob.len() && depth > 0 {
                if blob[i..].starts_with(b"/*") {
                    depth += 1;
                    i += 2;
                } else if blob[i..].starts_with(b"*/") {
                    depth -= 1;
                    i += 2;
                } else {
                    i += 1;
                }
            }
        } else {
            let mut at = i;
            if blob[at..].starts_with(b"br") || blob[at..].starts_with(b"cr") {
                at += 2;
            } else if blob[at] == b'r' {
                at += 1;
            }
            let begin = at;
            while at < blob.len() && blob[at] == b'#' {
                at += 1;
            }
            if at > i && at < blob.len() && blob[at] == b'"' {
                let hashes = at - begin;
                let mut end = vec![b'"'];
                end.extend(vec![b'#'; hashes]);
                i = at + 1;
                while i < blob.len() && !blob[i..].starts_with(&end) {
                    i += 1;
                }
                i = (i + end.len()).min(blob.len());
            } else if blob[i] == b'"' {
                i += 1;
                while i < blob.len() {
                    if blob[i] == b'\\' {
                        i = (i + 2).min(blob.len());
                    } else if blob[i] == b'"' {
                        i += 1;
                        break;
                    } else {
                        i += 1;
                    }
                }
            } else {
                i += 1;
                continue;
            }
        }
        code[start..i.min(blob.len())].fill(b' ');
    }
    code
}
pub fn code_provenance_scan(root: &Path, path: &str, blob: &[u8]) -> Vec<u8> {
    let mut spans = Vec::new();
    if path == "native/ui_core/src/transcript_ffi.rs" {
        let code = rust_code_mask(blob);
        for pattern in [
            r"\bstruct\s+Snapshot\s*\{\s*(owner)\s*:\s*u64\s*,",
            r"\bfn\s+reply\s*\(\s*handle\s*:\s*u64\s*,\s*(owner)\s*:\s*&Transcript\s*\)\s*->\s*Reply\s*\{",
            r"\bSnapshot\s*\{\s*(owner)\s*:\s*handle\s*,",
        ] {
            for m in BytesRegex::new(pattern).unwrap().captures_iter(&code) {
                let m = m.get(1).unwrap();
                spans.push((m.start(), m.end()));
            }
        }
    } else if [
        "native/application_core/src/live_objects.rs",
        "native/application_core/src/object_registry.rs",
        "native/application_core/src/object_registry_tests.rs",
        "native/application_core/tests/object_registry_protocol.rs",
    ]
    .contains(&path)
    {
        let code = rust_code_mask(blob);
        for m in BytesRegex::new(r"\b(owner)\s*:\s*(?:u64|&Value|owner_id)\b")
            .unwrap()
            .captures_iter(&code)
        {
            let m = m.get(1).unwrap();
            spans.push((m.start(), m.end()));
        }
    } else if [
        "narwhal3d/native_transcript.py",
        "spike/test_ui_transcript_model.py",
    ]
    .contains(&path)
    {
        if let Ok(tree) = python_sdk::parse(root, blob, "<unknown>") {
            let mut offsets = vec![0];
            for (i, &b) in blob.iter().enumerate() {
                if b == b'\n' {
                    offsets.push(i + 1);
                }
            }
            let class_pattern = BytesRegex::new(r"\Aclass[ \t]+(Owner)[ \t]*:").unwrap();
            let binding_pattern = BytesRegex::new(r"\Aowner[ \t]*:").unwrap();
            for n in walk_values(&tree) {
                let line = n["lineno"].as_u64().unwrap_or(0) as usize;
                let col = n["col_offset"].as_u64().unwrap_or(0) as usize;
                let offset = offsets
                    .get(line.saturating_sub(1))
                    .copied()
                    .unwrap_or(blob.len())
                    + col;
                if path == "narwhal3d/native_transcript.py"
                    && n["_type"] == "ClassDef"
                    && n["name"] == "Owner"
                    && n["bases"].as_array().is_some_and(Vec::is_empty)
                    && n["keywords"].as_array().is_some_and(Vec::is_empty)
                    && offset < blob.len()
                {
                    if let Some(m) = class_pattern.captures(&blob[offset..]) {
                        let m = m.get(1).unwrap();
                        spans.push((offset + m.start(), offset + m.end()));
                    }
                }
                if path == "spike/test_ui_transcript_model.py" && n["_type"] == "With" {
                    for item in n["items"].as_array().into_iter().flatten() {
                        let v = &item["optional_vars"];
                        if v["_type"] == "Name" && v["id"] == "owner" {
                            let l = v["lineno"].as_u64().unwrap_or(0) as usize;
                            let c = v["col_offset"].as_u64().unwrap_or(0) as usize;
                            let at = offsets
                                .get(l.saturating_sub(1))
                                .copied()
                                .unwrap_or(blob.len())
                                + c;
                            if at < blob.len() && binding_pattern.is_match(&blob[at..]) {
                                spans.push((at, at + 5));
                            }
                        }
                    }
                }
            }
        }
    }
    let mut out = blob.to_vec();
    for (a, b) in spans {
        if b <= out.len() {
            out.splice(a..b, b"value".iter().copied());
        }
    }
    out
}
fn replace_regex(blob: &[u8], pattern: &str, to: &[u8]) -> Vec<u8> {
    BytesRegex::new(pattern)
        .unwrap()
        .replace_all(blob, to)
        .into_owned()
}
pub fn audit_entries(
    root: &Path,
    entries: &Entries,
    require_manifest: bool,
    c: &Value,
) -> Vec<String> {
    let mut errors = Vec::new();
    if let Err(e) = python_sdk::validate(root, b"", "<admission>") {
        return vec![format!("compiler SDK admission failed: {e}")];
    }
    let forbidden_patterns: Vec<BytesRegex> = seq(&c["FORBIDDEN_PATTERNS"])
        .iter()
        .map(|p| {
            BytesRegex::new(&format!(
                "(?-u:{})",
                String::from_utf8(bytes(&p["regex"])).unwrap()
            ))
            .unwrap()
        })
        .collect();
    if require_manifest {
        for n in required_public_paths(root, entries, c) {
            if !entries.contains_key(&n) {
                errors.push(format!("missing required public file: {n}"));
            }
        }
    }
    for (path, (mode, blob)) in entries {
        if let Some(problem) = transient_publication_problem(path) {
            errors.push(format!("{path}: {problem}"));
            continue;
        }
        if let Some(p) = path_problem(path, c) {
            errors.push(format!("{path}: {p}"));
        }
        if parts(path)
            .iter()
            .any(|p| *p == "narwhal3d" || *p == "runtime")
        {
            if let Some(p) = runtime_archive_problem(root, blob) {
                errors.push(format!("{path}: {p}"));
            }
        }
        if mode == "120000" {
            errors.push(format!(
                "{path}: symlinks are not permitted in the public source root"
            ));
            continue;
        }
        if strings(&c["REQUIRED_EXECUTABLES"]).contains(path) && mode != "100755" {
            errors.push(format!(
                "{path}: required launcher or test harness is not executable"
            ));
        }
        for marker in seq(&c["REQUIRED_CONTENT"][path]) {
            let marker = bytes(&marker);
            if !contains(blob, &marker) {
                errors.push(format!(
                    "{path}: missing required structural marker {}",
                    bytes_repr(&marker)
                ));
            }
        }
        if suffix(path) == ".py" {
            if let Err(e) = python_sdk::validate(root, blob, path) {
                errors.push(format!("{path}: Python source does not compile: {e}"));
            }
        }
        if path.starts_with("native/source_tools/fixtures/pure_abi_v1/") {
            let pin = &c["FROZEN_ABI_FILES"][path];
            if pin["bytes"].as_u64() != Some(blob.len() as u64)
                || pin["sha256"].as_str() != Some(sha(blob).as_str())
            {
                errors.push(format!(
                    "{path}: frozen ABI DATA provenance hash/size mismatch"
                ));
            }
            // Frozen bytes are not source code. Scan the raw payload even when NUL-filled,
            // and even when the hash is invalid; a provenance error must not hide a secret.
            for marker in seq(&c["SECRET_MARKERS"]) {
                if contains(&lower(blob), &lower(&bytes(&marker))) {
                    errors.push(format!(
                        "{path}: contains possible secret marker {}",
                        bytes_repr(&bytes(&marker))
                    ));
                }
            }
            continue;
        }
        if blob.contains(&0)
            && c["FROZEN_DXF23_FILES"][path].is_null()
            && c["FROZEN_SELECTION_FILES"][path].is_null()
        {
            continue;
        }
        let mut scan = blob.clone();
        let mut bad = None;
        for name in [
            "frozen_nearest_scan",
            "frozen_selection_scan",
            "frozen_preview24_scan",
            "frozen_dxf23_scan",
            "frozen_json_scan",
            "frozen_data_scan",
        ] {
            match frozen_scan(name, path, &scan, c) {
                Ok(v) => scan = v,
                Err(e) => {
                    bad = Some(e);
                    break;
                }
            }
        }
        if let Some(e) = bad {
            errors.push(format!("{path}: invalid frozen DATA fixture: {e}"));
            scan = blob.clone();
        }
        let folded = lower(&scan);
        if !strings(&c["PROVENANCE_SCAN_EXEMPT"]).contains(path) {
            let mut provenance = lower(&code_provenance_scan(root, path, &scan));
            if [
                "native/automation_core/src/conversation.rs",
                "native/automation_core/src/primitives.rs",
                "native/automation_core/src/approval_lifecycle.rs",
                "native/automation_core/src/plan_review.rs",
                "native/automation_core/src/modeling.rs",
                "native/automation_core/src/export_action.rs",
            ]
            .contains(&path.as_str())
            {
                provenance = replace_regex(
                    &folded,
                    concat!(r"(?m)^[ \t]*(?:pub )?own", r"er: &?token,[ \t]*$"),
                    b"",
                );
                provenance = replace_regex(
                    &provenance,
                    concat!(r"(?m)^([ \t]*let )own", r"er: token([ \t]*=)"),
                    b"${1}request_token${2}",
                );
                provenance = replace_regex(
                    &provenance,
                    concat!(
                        r#"(?m)^([ \t]*(?:pub(?:\(crate\))? )?fn [a-z_]+\([^"\n]*?)own"#,
                        r#"er: (&?token)([,\)])"#
                    ),
                    b"${1}request_token: ${2}${3}",
                );
                provenance = replace_regex(
                    &provenance,
                    concat!(r"(?m)^[ \t]*own", r"er: token\.owner,[ \t]*$"),
                    b"",
                );
            }
            if path == "native/automation_core/tests/modeling_policy.rs" {
                provenance = replace_regex(
                    &provenance,
                    concat!(r"(?m)^[ \t]*own", r"er: token \{[ \t]*$"),
                    b"",
                );
            }
            for marker in seq(&c["FORBIDDEN_TEXT"]) {
                let marker = bytes(&marker);
                if contains(&provenance, &marker) {
                    errors.push(format!(
                        "{path}: contains forbidden private/legacy marker {}",
                        bytes_repr(&marker)
                    ));
                }
            }
            for regex in &forbidden_patterns {
                if let Some(m) = regex.find(&folded) {
                    errors.push(format!(
                        "{path}: contains forbidden development marker {}",
                        bytes_repr(m.as_bytes())
                    ));
                }
            }
            if !strings(&c["LEGACY_TEXT_ALLOWED"]).contains(path) {
                if let Some(m) = legacy_match(&legacy_scan_bytes(path, &scan), true) {
                    errors.push(format!(
                        "{path}: contains forbidden legacy product marker {}",
                        bytes_repr(&m)
                    ));
                }
            }
        }
        for marker in seq(&c["SECRET_MARKERS"]) {
            let marker = bytes(&marker);
            let raw = [
                "FROZEN_DXF23_FILES",
                "FROZEN_PREVIEW24_FILES",
                "FROZEN_SELECTION_FILES",
                "FROZEN_NEAREST_FILES",
            ]
            .iter()
            .any(|k| !c[*k][path].is_null());
            let target = if raw { lower(blob) } else { folded.clone() };
            if contains(&target, &lower(&marker)) {
                errors.push(format!(
                    "{path}: contains possible secret marker {}",
                    bytes_repr(&marker)
                ));
            }
        }
    }
    errors
}
fn recurse(root: &Path, dir: &Path, out: &mut Vec<PathBuf>, skip_git: bool) -> Result<(), String> {
    let mut paths = fs::read_dir(dir)
        .map_err(|e| e.to_string())?
        .map(|r| r.map(|x| x.path()).map_err(|e| e.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    paths.sort();
    for p in paths {
        let rel = p.strip_prefix(root).map_err(|e| e.to_string())?;
        if skip_git
            && rel
                .components()
                .next()
                .is_some_and(|c| c.as_os_str() == ".git")
        {
            continue;
        }
        out.push(p.clone());
        if p.is_dir() && !p.is_symlink() {
            recurse(root, &p, out, skip_git)?;
        }
    }
    Ok(())
}
pub fn recursive_files(root: &Path, directory: &str, c: &Value) -> Result<Vec<String>, String> {
    let dir = root.join(directory);
    if !dir.is_dir() || dir.is_symlink() {
        return Err(format!("missing safe directory: {directory}"));
    }
    let mut all = Vec::new();
    recurse(root, &dir, &mut all, false)?;
    all.sort();
    let mut out = Vec::new();
    for p in all {
        let rel = p
            .strip_prefix(root)
            .unwrap()
            .to_string_lossy()
            .replace('\\', "/");
        if p.is_symlink() {
            return Err(format!("symlink is not allowed in public source: {rel}"));
        }
        if !p.is_file() {
            continue;
        }
        if parts(&rel)
            .iter()
            .any(|s| *s == "target" || *s == "__pycache__")
            || [".pyc", ".pyo"].contains(&suffix(&rel).as_str())
            || rel.starts_with("narwhal3d/kernel/bin/")
            || (rel.starts_with("narwhal3d/native/") && rel != "narwhal3d/native/.gitignore")
            || [".so", ".dll", ".dylib", ".pyd", ".receipt.json"]
                .iter()
                .any(|s| rel.ends_with(s))
        {
            continue;
        }
        if let Some(problem) = transient_publication_problem(&rel) {
            return Err(format!("{rel}: {problem}"));
        }
        if strings(&c["PACKAGE_EXCLUDES"]).contains(&rel) {
            continue;
        }
        out.push(rel);
    }
    Ok(out)
}
pub fn public_files(root: &Path, c: &Value) -> Result<Vec<String>, String> {
    let mut paths = BTreeSet::new();
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
        paths.extend(strings(&c[k]));
    }
    for dir in RECURSIVE_PUBLIC_DIRS {
        paths.extend(recursive_files(root, dir, c)?);
    }
    for path in &paths {
        if let Some(problem) = transient_publication_problem(path) {
            return Err(format!("{path}: {problem}"));
        }
    }
    let rejected: Vec<_> = paths
        .iter()
        .filter(|p| retirement_path_problem(p).is_some())
        .cloned()
        .collect();
    if !rejected.is_empty() {
        return Err(format!(
            "retired payload in public selection: {}",
            rejected.join(", ")
        ));
    }
    let missing: Vec<_> = paths
        .iter()
        .filter(|p| !root.join(p).is_file())
        .cloned()
        .collect();
    if !missing.is_empty() {
        return Err(format!(
            "allowlisted files are missing: {}",
            missing.join(", ")
        ));
    }
    Ok(paths.into_iter().collect())
}
fn run(root: &Path, args: &[&str], env: &BTreeMap<String, String>) -> Result<Output, String> {
    let output = Command::new(args[0])
        .args(&args[1..])
        .env("GIT_EDITOR", "true")
        .envs(env)
        .current_dir(root)
        .output()
        .map_err(|_| format!("command failed: {}", args.join(" ")))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(format!(
            "command failed: {}{}",
            args.join(" "),
            if detail.is_empty() {
                String::new()
            } else {
                format!(": {detail}")
            }
        ));
    }
    Ok(output)
}
pub fn git_entries(root: &Path, reference: &str) -> Result<Entries, String> {
    let output = run(
        root,
        &["git", "--no-pager", "ls-tree", "-rz", reference],
        &BTreeMap::new(),
    )?;
    let mut entries = Entries::new();
    for rec in output.stdout.split(|b| *b == 0).filter(|r| !r.is_empty()) {
        let at = rec
            .iter()
            .position(|b| *b == b'\t')
            .ok_or("malformed Git tree entry")?;
        let meta = String::from_utf8_lossy(&rec[..at]);
        let fields: Vec<_> = meta.split_whitespace().collect();
        if fields.len() != 3 || fields[1] != "blob" {
            continue;
        }
        let path = String::from_utf8(rec[at + 1..].to_vec()).map_err(|_| "non-UTF8 source path")?;
        if let Some(problem) = transient_publication_problem(&path) {
            return Err(format!("{path}: {problem}"));
        }
        let spec = format!("{reference}:{path}");
        let blob = run(
            root,
            &["git", "--no-pager", "show", &spec],
            &BTreeMap::new(),
        )?
        .stdout;
        entries.insert(path, (fields[0].into(), blob));
    }
    Ok(entries)
}
pub fn filesystem_entries(root: &Path) -> Result<Entries, String> {
    let mut paths = Vec::new();
    recurse(root, root, &mut paths, true)?;
    paths.sort();
    let mut out = Entries::new();
    for p in paths {
        let rel = p
            .strip_prefix(root)
            .unwrap()
            .to_string_lossy()
            .replace('\\', "/");
        if let Some(problem) = transient_publication_problem(&rel) {
            return Err(format!("{rel}: {problem}"));
        }
        if p.is_symlink() {
            out.insert(
                rel,
                (
                    "120000".into(),
                    fs::read_link(&p)
                        .map_err(|e| e.to_string())?
                        .to_string_lossy()
                        .as_bytes()
                        .to_vec(),
                ),
            );
        } else if p.is_file() {
            #[cfg(unix)]
            let executable = {
                use std::os::unix::fs::PermissionsExt;
                fs::metadata(&p)
                    .map_err(|e| e.to_string())?
                    .permissions()
                    .mode()
                    & 0o111
                    != 0
            };
            #[cfg(not(unix))]
            let executable = false;
            out.insert(
                rel,
                (
                    if executable { "100755" } else { "100644" }.into(),
                    fs::read(&p).map_err(|e| e.to_string())?,
                ),
            );
        }
    }
    Ok(out)
}
pub fn selected_entries(root: &Path, c: &Value) -> Result<Entries, String> {
    let mut entries = Entries::new();
    for p in public_files(root, c)? {
        let path = root.join(&p);
        if path.is_symlink() {
            entries.insert(
                p,
                (
                    "120000".into(),
                    fs::read_link(path)
                        .map_err(|e| e.to_string())?
                        .to_string_lossy()
                        .as_bytes()
                        .to_vec(),
                ),
            );
            continue;
        }
        #[cfg(unix)]
        let executable = {
            use std::os::unix::fs::PermissionsExt;
            fs::metadata(&path)
                .map_err(|e| e.to_string())?
                .permissions()
                .mode()
                & 0o111
                != 0
        };
        #[cfg(not(unix))]
        let executable = false;
        entries.insert(
            p,
            (
                if executable { "100755" } else { "100644" }.into(),
                fs::read(path).map_err(|e| e.to_string())?,
            ),
        );
    }
    Ok(entries)
}
pub fn root_commit_problem(root: &Path, reference: &str) -> Result<Option<String>, String> {
    let output = run(
        root,
        &["git", "rev-list", "--parents", "-n", "1", reference],
        &BTreeMap::new(),
    )?;
    let line = String::from_utf8_lossy(&output.stdout).trim().to_string();
    Ok((line.split_whitespace().count() != 1)
        .then(|| format!("{reference} is not a root commit: {line}")))
}
fn utc(timestamp: i64) -> String {
    let days = timestamp / 86400;
    let sec = timestamp % 86400;
    let z = days + 719468;
    let era = z / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let mut y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    if m <= 2 {
        y += 1;
    }
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        sec / 3600,
        sec / 60 % 60,
        sec % 60
    )
}
/// Reject a stale or explicitly supplied export list before any Git write.
pub fn require_no_transient_export(root: &Path, paths: &[String], c: &Value) -> Result<(), String> {
    for path in paths {
        if let Some(problem) = transient_publication_problem(path) {
            return Err(format!("{path}: {problem}"));
        }
    }
    for dir in RECURSIVE_PUBLIC_DIRS {
        if root.join(dir).is_dir() {
            recursive_files(root, dir, c)?;
        }
    }
    Ok(())
}
pub fn commit_root(
    root: &Path,
    paths: &[String],
    message: &str,
    timestamp: i64,
    c: &Value,
) -> Result<String, String> {
    if timestamp < 0 {
        return Err("timestamp must be non-negative".into());
    }
    require_no_transient_export(root, paths, c)?;
    let branch = c["BRANCH"].as_str().ok_or("branch policy missing")?;
    let reference = format!("refs/heads/{branch}");
    let exists = Command::new("git")
        .args(["show-ref", "--verify", "--quiet", &reference])
        .current_dir(root)
        .status()
        .map_err(|e| e.to_string())?
        .success();
    if exists {
        return Err(format!("branch already exists: {branch}"));
    }
    let scratch = std::env::temp_dir().join(format!(
        "narwhal-public-index-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir(&scratch).map_err(|e| e.to_string())?;
    let result = (|| {
        let index = scratch.join("index");
        let mut env = BTreeMap::from([(
            "GIT_INDEX_FILE".into(),
            index.to_string_lossy().into_owned(),
        )]);
        run(root, &["git", "read-tree", "--empty"], &env)?;
        let mut args = vec!["git", "add", "-f", "--"];
        args.extend(paths.iter().map(String::as_str));
        run(root, &args, &env)?;
        let tree = String::from_utf8_lossy(&run(root, &["git", "write-tree"], &env)?.stdout)
            .trim()
            .to_owned();
        env.remove("GIT_INDEX_FILE");
        for p in ["AUTHOR", "COMMITTER"] {
            env.insert(format!("GIT_{p}_NAME"), "Narwhal 3D Contributors".into());
            env.insert(format!("GIT_{p}_EMAIL"), "dev@localhost".into());
            env.insert(format!("GIT_{p}_DATE"), utc(timestamp));
        }
        let commit = String::from_utf8_lossy(
            &run(root, &["git", "commit-tree", &tree, "-m", message], &env)?.stdout,
        )
        .trim()
        .to_owned();
        let entries = git_entries(root, &commit)?;
        let errors = audit_entries(root, &entries, true, c);
        if !errors.is_empty() {
            return Err(format!("public root audit failed: {}", errors.join("; ")));
        }
        if let Some(p) = root_commit_problem(root, &commit)? {
            return Err(p);
        }
        run(
            root,
            &[
                "git",
                "update-ref",
                &reference,
                &commit,
                "0000000000000000000000000000000000000000",
            ],
            &BTreeMap::new(),
        )?;
        Ok(commit)
    })();
    let _ = fs::remove_dir_all(scratch);
    result
}
fn request_bytes(v: &Value) -> Result<Vec<u8>, String> {
    if let Some(s) = v.get("$bytes").and_then(Value::as_str) {
        STANDARD
            .decode(s)
            .map_err(|e| format!("invalid base64 byte payload: {e}"))
    } else {
        Err("byte payload must contain a base64 $bytes string".into())
    }
}
pub fn decode_entries(v: &Value) -> Result<Entries, String> {
    v.as_object()
        .ok_or("entries must be an object")?
        .iter()
        .map(|(k, v)| {
            let a = v.as_array().ok_or("entry must be [mode, bytes]")?;
            if a.len() != 2 {
                return Err("entry must have two fields".into());
            }
            Ok((
                k.clone(),
                (
                    a[0].as_str().ok_or("entry mode must be text")?.into(),
                    request_bytes(&a[1])?,
                ),
            ))
        })
        .collect()
}
pub fn dispatch(request: &Value) -> Result<Value, String> {
    let root = PathBuf::from(request["root"].as_str().unwrap_or("."));
    let mut c = config();
    if let Some(overrides) = request["config"].as_object() {
        c.as_object_mut().unwrap().extend(overrides.clone());
    }
    let op = request["op"].as_str().ok_or("operation missing")?;
    if [
        "runtime_archive_problem",
        "required_public_paths",
        "code_provenance_scan",
    ]
    .contains(&op)
    {
        python_sdk::validate(&root, b"", "<admission>")?;
    }
    let path = request["path"].as_str().unwrap_or("");
    let blob = bytes(&request["blob"]);
    Ok(match op {
        "batch" => Value::Array(
            request["requests"]
                .as_array()
                .ok_or("batch requests missing")?
                .iter()
                .map(|q| match dispatch(q) {
                    Ok(v) => json!({"ok":true,"value":v}),
                    Err(e) => json!({"ok":false,"error":e}),
                })
                .collect(),
        ),
        "config" => wire(&c),
        "pure_probe" => pure_gate::probe(request)?,
        "pure_probe_records" => json!(pure_abi::record_witnesses(
            &root,
            request["name"].as_str().ok_or("probe name missing")?
        )?),
        "pdf_bundle" => pdf_bundle::dispatch(request)?,
        "pure_gate_metadata" => wire(&pure_gate::metadata()),
        "pure_gate" => pure_gate::dispatch(request, &c)?,
        "pure_abi_probe" => pure_abi::probe(
            &root,
            Path::new(request["library"].as_str().ok_or("library missing")?),
            request["name"].as_str().ok_or("probe name missing")?,
        )?,
        "audit_selected" => {
            let entries = selected_entries(&root, &c)?;
            let errors = audit_entries(&root, &entries, true, &c);
            json!({"ok":errors.is_empty(),"files":entries.len(),"errors":errors})
        }
        "selected_receipt" => {
            let entries = selected_entries(&root, &c)?;
            Value::Object(
                entries
                    .into_iter()
                    .map(|(p, (mode, b))| {
                        (p, json!({"mode":mode,"sha256":sha(&b),"bytes":b.len()}))
                    })
                    .collect(),
            )
        }
        "preview_check" => {
            let public: BTreeSet<_> = public_files(&root, &c)?.into_iter().collect();
            let missing: Vec<_> = strings(&request["paths"])
                .into_iter()
                .filter(|p| !public.contains(p))
                .collect();
            json!({"ok":missing.is_empty(),"missing":missing})
        }
        "policy_inputs" => json!(policy::inputs(
            &root,
            request["name"].as_str().ok_or("case missing")?
        )?),
        "policy_check" => policy::check(
            &root,
            &c,
            request["name"].as_str().ok_or("case missing")?,
            &request["sources"],
        )?,
        "self_check" => audit_cases::self_check(&root, &c)?,
        "project" => checks::project(&root, &c)?,
        "completeness" => checks::completeness(&root, &c)?,
        "public_files" => json!(public_files(&root, &c)?),
        "recursive_files" => json!(recursive_files(&root, path, &c)?),
        "retirement_path_problem" => json!(retirement_path_problem(path)),
        "path_problem" => json!(path_problem(path, &c)),
        "runtime_archive_problem" => json!(runtime_archive_problem(&root, &blob)),
        "required_public_paths" => {
            json!({"$set":required_public_paths(&root,&decode_entries(&request["entries"])?,&c)})
        }
        "audit_entries" => json!(audit_entries(
            &root,
            &decode_entries(&request["entries"])?,
            request["require_manifest"].as_bool().unwrap_or(true),
            &c
        )),
        "legacy_scan_bytes" => json!({"$bytes":STANDARD.encode(legacy_scan_bytes(path,&blob))}),
        "code_provenance_scan" => {
            json!({"$bytes":STANDARD.encode(code_provenance_scan(&root,path,&blob))})
        }
        "_rust_code_mask" => json!({"$bytes":STANDARD.encode(rust_code_mask(&blob))}),
        "frozen_data_scan"
        | "frozen_json_scan"
        | "frozen_dxf23_scan"
        | "frozen_preview24_scan"
        | "frozen_selection_scan"
        | "frozen_nearest_scan" => {
            json!({"$bytes":STANDARD.encode(frozen_scan(op,path,&blob,&c)?)})
        }
        "require_root_commit" => json!(root_commit_problem(
            &root,
            request["reference"].as_str().ok_or("reference missing")?
        )?),
        "commit_root" => json!(commit_root(
            &root,
            &strings(&request["paths"]),
            request["message"]
                .as_str()
                .unwrap_or("Initial public release of Narwhal 3D"),
            request["timestamp"].as_i64().ok_or("timestamp missing")?,
            &c
        )?),
        "parse_python" => python_sdk::parse(&root, &blob, path)?,
        _ => return Err(format!("unknown source-tools operation: {op}")),
    })
}
