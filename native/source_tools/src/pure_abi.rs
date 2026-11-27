//! Fixed C-ABI witness execution. All control, relocation and validation is Rust.
//! The catalog contains only finite calls/answers, not Python source, AST or bytecode.
const CATALOG_SHA: &str = "ab415dcd7cabb340a34ced3b0cb4d16dcbff660fa3a787f64904eef4518d9adc";
use crate::{seq, sha, strings};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::c_void,
    fs,
    path::Path,
};
fn unhex(s: &str) -> Result<Vec<u8>, String> {
    if !s.len().is_multiple_of(2) || !s.is_ascii() {
        return Err("invalid hex".into());
    }
    s.as_bytes()
        .chunks_exact(2)
        .map(|c| u8::from_str_radix(std::str::from_utf8(c).unwrap(), 16).map_err(|e| e.to_string()))
        .collect()
}
#[derive(Clone)]
enum Arg {
    Ptr(*mut c_void),
    Integer(u64),
    Float(f64),
}
impl Arg {
    fn pointer(&self) -> Result<*mut c_void, String> {
        match self {
            Self::Ptr(p) => Ok(*p),
            _ => Err("pointer type mismatch".into()),
        }
    }
    fn integer(&self) -> Result<u64, String> {
        match self {
            Self::Integer(n) => Ok(*n),
            _ => Err("integer type mismatch".into()),
        }
    }
    fn float(&self) -> Result<f64, String> {
        match self {
            Self::Float(n) => Ok(*n),
            _ => Err("float type mismatch".into()),
        }
    }
}
include!("pure_abi_calls.rs");
const GUARD: u128 = 0x61_75_64_69_74_5f_67_75_61_72_64_5f_61_62_69_31;
struct Region {
    words: Vec<u128>,
    size: usize,
}
impl Region {
    fn new(size: usize) -> Result<Self, String> {
        if size > 32 * 1024 * 1024 {
            return Err("witness region limit".into());
        }
        Ok(Self {
            words: vec![GUARD; size.div_ceil(16) + 4],
            size,
        })
    }
    fn pointer(&mut self) -> *mut u8 {
        unsafe { self.words.as_mut_ptr().add(2).cast() }
    }
    fn load(&mut self, b: &[u8]) -> Result<(), String> {
        if b.len() != self.size {
            return Err("witness region size differs".into());
        }
        unsafe { std::ptr::copy_nonoverlapping(b.as_ptr(), self.pointer(), b.len()) }
        Ok(())
    }
    fn bytes(&mut self) -> &[u8] {
        unsafe { std::slice::from_raw_parts(self.pointer(), self.size) }
    }
    fn guarded(&self) -> bool {
        let raw = unsafe {
            std::slice::from_raw_parts(self.words.as_ptr().cast::<u8>(), self.words.len() * 16)
        };
        let guard = GUARD.to_ne_bytes();
        raw[..32]
            .iter()
            .enumerate()
            .all(|(i, b)| *b == guard[i % 16])
            && raw[32 + self.size..]
                .iter()
                .enumerate()
                .all(|(i, b)| *b == guard[(self.size + i) % 16])
    }
}
unsafe extern "C" fn line_evaluator(_: *mut c_void, t: f64, order: u32, out: *mut f64) -> i32 {
    if out.is_null() {
        return -1;
    }
    let n = if order == 0 { 3 } else { 9 };
    let p = std::slice::from_raw_parts_mut(out, n);
    p.fill(0.0);
    p[0] = t;
    if order > 0 {
        p[3] = 1.0
    }
    0
}
fn address(
    v: &Value,
    regions: &mut [Region],
    handles: &BTreeMap<u64, usize>,
) -> Result<usize, String> {
    if v["null"] == true {
        return Ok(0);
    }
    if v["callback"] == "line_evaluator" {
        return Ok(line_evaluator as *const () as usize);
    }
    if let Some(n) = v["invalid"].as_u64() {
        if n > 4096 && n < u64::MAX - 4096 {
            return Err("invalid-pointer witness outside reviewed sentinel range".into());
        }
        return Ok(n as usize);
    }
    if let Some(n) = v["handle"].as_u64() {
        return handles
            .get(&n)
            .copied()
            .ok_or_else(|| format!("unbound owned result {n}"));
    }
    if let Some(n) = v["region"].as_u64() {
        let r = regions.get_mut(n as usize).ok_or("witness region index")?;
        let offset = v["offset"].as_u64().ok_or("witness pointer offset")? as usize;
        if offset > r.size {
            return Err("witness pointer bounds".into());
        }
        return Ok(r.pointer() as usize + offset);
    }
    Err("unknown pointer witness".into())
}
pub fn witness_blob(root: &Path, id: &str) -> Result<Vec<u8>, String> {
    if id.len() != 64 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("unsafe witness blob name".into());
    }
    let path = root
        .join("native/source_tools/fixtures/pure_abi_v1/blobs")
        .join(id);
    if path.is_symlink() {
        return Err("witness symlink refused".into());
    }
    let data = fs::read(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    if sha(&data) != id {
        return Err(format!("witness provenance hash mismatch: {id}"));
    }
    Ok(data)
}
fn expected(
    root: &Path,
    platforms: &Value,
    image: &Value,
    regions: &mut [Region],
    handles: &BTreeMap<u64, usize>,
) -> Result<Vec<u8>, String> {
    let id = image["blob"].as_str().ok_or("witness blob missing")?;
    let id = if cfg!(all(target_os = "windows", target_env = "msvc")) {
        platforms["windows_blob_overlays"][id]
            .as_str()
            .unwrap_or(id)
    } else {
        id
    };
    let mut b = witness_blob(root, id)?;
    for fix in image["relocations"]
        .as_array()
        .ok_or("witness relocations missing")?
    {
        let at = fix["offset"].as_u64().ok_or("relocation offset")? as usize;
        let ptr = address(&fix["pointer"], regions, handles)?;
        let dst = b
            .get_mut(at..at.checked_add(8).ok_or("relocation overflow")?)
            .ok_or("relocation bounds")?;
        dst.copy_from_slice(&(ptr as u64).to_le_bytes());
    }
    Ok(b)
}
pub fn names(root: &Path) -> Result<Vec<String>, String> {
    let all = catalog(root)?;
    Ok(all
        .iter()
        .filter_map(|v| v["name"].as_str().map(str::to_owned))
        .collect())
}
fn catalog(root: &Path) -> Result<Vec<Value>, String> {
    let path = root.join("native/source_tools/fixtures/pure_abi_v1/cases.json");
    if path.is_symlink() {
        return Err("witness catalog symlink refused".into());
    }
    let raw = fs::read(path).map_err(|e| e.to_string())?;
    if raw.len() > 16 * 1024 * 1024 {
        return Err("witness catalog limit".into());
    }
    if sha(&raw) != CATALOG_SHA {
        return Err("ABI catalog provenance hash mismatch".into());
    }
    let d: Value = serde_json::from_slice(&raw).map_err(|e| e.to_string())?;
    d.as_array()
        .cloned()
        .ok_or("witness catalog must be a list".into())
}
/// Raw, inert C-ABI input bytes for independent loaded-SDK record comparisons.
pub fn record_witnesses(root: &Path, name: &str) -> Result<String, String> {
    let cases = catalog(root)?;
    let case = cases
        .iter()
        .find(|v| v["name"] == name)
        .ok_or("unknown ABI witness group")?;
    let mut ids = BTreeSet::new();
    for call in seq(&case["calls"]) {
        for image in seq(&call["before"]) {
            ids.insert(
                image["blob"]
                    .as_str()
                    .ok_or("missing witness blob")?
                    .to_owned(),
            );
        }
    }
    let mut out = Vec::new();
    for id in ids {
        out.push(
            witness_blob(root, &id)?
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect::<String>(),
        );
    }
    Ok(out.join("\n"))
}
pub fn identity(root: &Path, name: &str) -> Result<Value, String> {
    catalog(root)?
        .into_iter()
        .find(|v| v["name"] == name)
        .map(|v| v["identity"].clone())
        .ok_or_else(|| format!("unknown pure ABI witness group: {name}"))
}
/// Invoked only in a supervised disposable child, never in the editor/application process.
pub fn probe(root: &Path, library: &Path, name: &str) -> Result<Value, String> {
    if usize::BITS != 64 || !cfg!(target_endian = "little") {
        return Err("pure ABI witness layout unqualified on this target".into());
    }
    if !library.is_file() || library.is_symlink() {
        return Err("missing regular library".into());
    }
    let platform_data = platforms(root)?;
    let cases = catalog(root)?;
    let case = cases
        .iter()
        .find(|v| v["name"] == name)
        .ok_or_else(|| format!("unknown pure ABI witness group: {name}"))?;
    if name == "PURE_CORE_RETIREMENT_PROBE" {
        preview_platform(
            std::env::consts::OS,
            std::env::consts::ARCH,
            if cfg!(target_env = "msvc") {
                "msvc"
            } else {
                "gnu"
            },
        )?;
    }
    let mut handles = BTreeMap::new();
    let mut live = BTreeSet::new();
    let lib = unsafe { libloading::Library::new(library) }.map_err(|e| e.to_string())?;
    for name in strings(&case["exports"]) {
        if name == "narwhal_pure_core_interpolate" && !cfg!(target_os = "linux") {
            continue;
        }
        if !name.starts_with("narwhal_pure_core_") {
            return Err("unreviewed export prefix".into());
        }
        unsafe { lib.get::<unsafe extern "C" fn()>(name.as_bytes()) }
            .map_err(|e| format!("required export {name}: {e}"))?;
    }
    for (index, call) in case["calls"]
        .as_array()
        .ok_or("witness calls missing")?
        .iter()
        .enumerate()
    {
        if call["export"] == "narwhal_pure_core_interpolate" && !cfg!(target_os = "linux") {
            continue;
        }
        let result = (|| {
            let sizes = seq(&call["regions"]);
            if sizes.len() > 256
                || sizes
                    .iter()
                    .try_fold(0u64, |n, v| {
                        n.checked_add(v["size"].as_u64().unwrap_or(u64::MAX))
                    })
                    .is_none_or(|n| n > 64 * 1024 * 1024)
            {
                return Err("witness aggregate region limit".into());
            }
            let mut regions = seq(&call["regions"])
                .iter()
                .map(|v| Region::new(v["size"].as_u64().ok_or("region size")? as usize))
                .collect::<Result<Vec<_>, String>>()?;
            for (i, image) in call["before"]
                .as_array()
                .ok_or("before image missing")?
                .iter()
                .enumerate()
            {
                let b = expected(root, &platform_data, image, &mut regions, &handles)?;
                regions.get_mut(i).ok_or("before image index")?.load(&b)?;
            }
            let signature = strings(&call["signature"]);
            let values = call["args"].as_array().ok_or("arguments missing")?;
            if values.len() != signature.len() {
                return Err("argument signature count".into());
            }
            let mut args = Vec::new();
            for (t, v) in signature.iter().zip(values) {
                args.push(if t == "ptr" {
                    Arg::Ptr(address(v, &mut regions, &handles)? as *mut c_void)
                } else if t.starts_with('f') {
                    let raw = unhex(v["float_bits"].as_str().ok_or("float bits missing")?)?;
                    Arg::Float(f64::from_le_bytes(
                        raw.try_into().map_err(|_| "float width")?,
                    ))
                } else {
                    Arg::Integer(
                        v.as_u64()
                            .or_else(|| v.as_i64().map(|n| n as u64))
                            .ok_or("integer argument")?,
                    )
                })
            }
            let name = call["export"].as_str().ok_or("export missing")?;
            let kind = call["result_type"].as_str().ok_or("result type missing")?;
            let actual = unsafe {
                invoke(
                    &lib,
                    name.as_bytes(),
                    &format!("{kind}:{}", signature.join(",")),
                    &args,
                )
            }?;
            require_value(&call["expected_result"], &actual, "return value")?;
            if let Some(id) = call["new_handle"].as_u64() {
                let after = call["after"].as_array().ok_or("after image missing")?;
                let mut bound = None;
                for (i, image) in after.iter().enumerate() {
                    for fix in seq(&image["relocations"]) {
                        if fix["pointer"]["handle"].as_u64() == Some(id) {
                            let at = fix["offset"].as_u64().ok_or("owned handle offset")? as usize;
                            let r = regions.get_mut(i).ok_or("owned handle region")?;
                            let raw = r.bytes().get(at..at + 8).ok_or("owned handle bounds")?;
                            bound = Some(u64::from_le_bytes(raw.try_into().unwrap()) as usize);
                        }
                    }
                }
                let ptr = bound
                    .filter(|p| *p != 0)
                    .ok_or("owned result was not published")?;
                require_no_alias(live.iter().any(|id| handles.get(id) == Some(&ptr)))?;
                handles.insert(id, ptr);
                live.insert(id);
            }
            for (i, image) in call["after"]
                .as_array()
                .ok_or("after image missing")?
                .iter()
                .enumerate()
            {
                let want = expected(root, &platform_data, image, &mut regions, &handles)?;
                let r = regions.get_mut(i).ok_or("after image index")?;
                require_bytes(&want, r.bytes(), &format!("output/atomicity region {i}"))?;
                if !r.guarded() {
                    return Err(format!("output guard overwritten in region {i}"));
                }
            }
            if name == "narwhal_pure_core_vector_free_v1" {
                if let Some(id) = values.first().and_then(|v| v["handle"].as_u64()) {
                    live.remove(&id);
                }
            }
            Ok::<(), String>(())
        })();
        if let Err(e) = result {
            return Err(format!("{name} witness {index} {}: {e}", call["export"]));
        }
    }
    if !live.is_empty() {
        return Err("owned result lifecycle left live results".into());
    }
    Ok(case["identity"].clone())
}

pub fn require_value(expected: &Value, actual: &Value, label: &str) -> Result<(), String> {
    if expected != actual {
        Err(format!(
            "{label} differs: expected {expected}, got {actual}"
        ))
    } else {
        Ok(())
    }
}
pub fn require_bytes(expected: &[u8], actual: &[u8], label: &str) -> Result<(), String> {
    if expected != actual {
        Err(format!("{label} differs"))
    } else {
        Ok(())
    }
}
pub fn require_no_alias(aliased: bool) -> Result<(), String> {
    if aliased {
        Err("owned results alias an active handle".into())
    } else {
        Ok(())
    }
}
pub fn validate_observation(root: &Path, expected: &Value, actual: &Value) -> Result<(), String> {
    require_value(&expected["result"], &actual["result"], "return value")?;
    require_no_alias(!actual["new_handle_alias"].is_null())?;
    let images = |v: &Value| -> Result<Vec<u8>, String> {
        let mut out = Vec::new();
        for image in seq(&v["after"]) {
            let data = witness_blob(root, image["blob"].as_str().ok_or("mock observation blob")?)?;
            out.extend((data.len() as u64).to_le_bytes());
            out.extend(data);
            let slots = serde_json::to_vec(&image["relocations"]).unwrap();
            out.extend((slots.len() as u64).to_le_bytes());
            out.extend(slots);
        }
        Ok(out)
    };
    require_bytes(&images(expected)?, &images(actual)?, "output/atomicity")
}

pub fn preview_platform(os: &str, arch: &str, abi: &str) -> Result<&'static str, String> {
    match (os, arch, abi) {
        ("linux", "x86_64", _) => Ok("linux"),
        ("windows", "x86_64", "msvc") => Ok("windows"),
        _ => Err("preview bit witnesses unqualified on this platform".into()),
    }
}
pub fn platforms(root: &Path) -> Result<Value, String> {
    let path = root.join("native/source_tools/fixtures/pure_abi_v1/platforms.json");
    if path.is_symlink() {
        return Err("platform witness symlink refused".into());
    }
    let data = fs::read(path).map_err(|e| e.to_string())?;
    if sha(&data) != "09654661dbe31503732c450cf5dc9945e4573c2bf3963ca527368cf695f111c6" {
        return Err("platform witness provenance hash mismatch".into());
    }
    serde_json::from_slice(&data).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn every_guard_byte_is_checked() {
        for n in 0usize..35 {
            for at in 0..(n.div_ceil(16) + 4) * 16 {
                if (32..32 + n).contains(&at) {
                    continue;
                }
                let mut r = Region::new(n).unwrap();
                unsafe {
                    *r.words.as_mut_ptr().cast::<u8>().add(at) ^= 1;
                }
                assert!(!r.guarded(), "size {n} offset {at}");
            }
            assert!(Region::new(n).unwrap().guarded());
        }
    }
}
