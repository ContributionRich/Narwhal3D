//! Source-pinned PDF redistribution admission. Python only transports these requests.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Component, Path},
    process::Command,
};
const ROOTS: [&str; 6] = [
    "pdfinfo",
    "pdftoppm",
    "pdf-libs",
    "pdf-data",
    "pdf-source",
    "pdf-licenses",
];
fn digest(path: &Path) -> Result<String, String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(fs::read(path).map_err(|e| e.to_string())?)
    ))
}
fn relative(name: &str) -> Result<(), String> {
    let p = Path::new(name);
    if name.contains('\\')
        || name.contains(':')
        || !p.components().all(|c| matches!(c, Component::Normal(_)))
        || !ROOTS.contains(
            &p.components()
                .next()
                .and_then(|c| c.as_os_str().to_str())
                .unwrap_or(""),
        )
    {
        return Err(format!("unsafe PDF bundle path: {name}"));
    }
    Ok(())
}
fn regular(path: &Path, directory: bool) -> Result<(), String> {
    let m = fs::symlink_metadata(path)
        .map_err(|e| format!("PDF bundle missing {}: {e}", path.display()))?;
    if m.file_type().is_symlink() || if directory { !m.is_dir() } else { !m.is_file() } {
        return Err(format!(
            "PDF bundle linked/nonregular entry: {}",
            path.display()
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if m.mode() & 0o022 != 0 {
            return Err(format!(
                "PDF bundle writable by another principal: {}",
                path.display()
            ));
        }
    }
    Ok(())
}
fn collect(root: &Path, path: &Path, out: &mut BTreeMap<String, Value>) -> Result<(), String> {
    let m = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    regular(path, m.is_dir())?;
    if m.is_dir() {
        for e in fs::read_dir(path).map_err(|e| e.to_string())? {
            collect(root, &e.map_err(|e| e.to_string())?.path(), out)?;
        }
    } else {
        let name = path
            .strip_prefix(root)
            .map_err(|e| e.to_string())?
            .to_str()
            .ok_or("non-UTF8 PDF path")?
            .to_owned();
        relative(&name)?;
        let executable = name == "pdfinfo" || name == "pdftoppm";
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if executable && m.permissions().mode() & 0o111 == 0 {
                return Err(format!("PDF renderer is not executable: {name}"));
            }
        }
        out.insert(
            name,
            json!({"sha256":digest(path)?,"bytes":m.len(),"executable":executable}),
        );
    }
    Ok(())
}
fn inventory(directory: &Path) -> Result<BTreeMap<String, Value>, String> {
    regular(directory, true)?;
    let mut files = BTreeMap::new();
    for name in ROOTS {
        collect(directory, &directory.join(name), &mut files)?;
    }
    Ok(files)
}
fn lock(root: &Path, supplied: Option<&str>) -> Result<(Value, String), String> {
    let path = root.join("packaging/linux-pdf.lock.json");
    regular(&path, false)?;
    let bytes = fs::read(&path).map_err(|e| e.to_string())?;
    if let Some(other) = supplied {
        regular(Path::new(other), false)?;
        if fs::read(other).map_err(|e| e.to_string())? != bytes {
            return Err("packaged PDF lock differs from the source-pinned lock".into());
        }
    }
    let v: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    if v["schema"] != 1
        || v["signatures"]["verified"] != true
        || v["renderer_version"] != "20.11.0-14.el8_10"
    {
        return Err("unqualified PDF renderer lock".into());
    }
    let files = v["files"].as_object().ok_or("PDF lock files missing")?;
    for (name, record) in files {
        relative(name)?;
        if record["sha256"]
            .as_str()
            .is_none_or(|s| s.len() != 64 || !s.bytes().all(|c| c.is_ascii_hexdigit()))
            || !record["bytes"].is_u64()
            || !record["executable"].is_boolean()
        {
            return Err(format!("invalid PDF file pin: {name}"));
        }
    }
    for required in [
        "pdfinfo",
        "pdftoppm",
        "pdf-data/fonts.conf",
        "pdf-data/fonts/DejaVuSans.ttf",
        "pdf-licenses/README.txt",
        "pdf-licenses/poppler/COPYING",
    ] {
        if !files.contains_key(required) {
            return Err(format!("PDF required file pin missing: {required}"));
        }
    }
    let packages = v["packages"]
        .as_object()
        .ok_or("PDF package source provenance missing")?;
    if packages.is_empty() {
        return Err("PDF package source provenance empty".into());
    }
    for (name, p) in packages {
        for key in ["binary_url", "source_url"] {
            let prefix = if key == "binary_url" {
                "https://repo.almalinux.org/almalinux/8.10/"
            } else {
                "https://vault.almalinux.org/8.10/"
            };
            if !p[key].as_str().is_some_and(|s| s.starts_with(prefix)) {
                return Err(format!("untrusted PDF package retrieval pin: {name}/{key}"));
            }
        }
        let source = p["source_rpm"]
            .as_str()
            .ok_or("PDF corresponding source filename missing")?;
        if source.contains('/')
            || !source.ends_with(".src.rpm")
            || files
                .get(&format!("pdf-source/{source}"))
                .map(|r| &r["sha256"])
                != Some(&p["source_rpm_sha256"])
        {
            return Err(format!("PDF corresponding source not pinned: {name}"));
        }
    }
    Ok((v, format!("{:x}", Sha256::digest(bytes))))
}
fn elf_admission(
    directory: &Path,
    files: &BTreeMap<String, Value>,
    system: &Value,
) -> Result<String, String> {
    let allowed: BTreeSet<_> = system
        .as_array()
        .ok_or("PDF system SONAME policy missing")?
        .iter()
        .filter_map(Value::as_str)
        .collect();
    let expected: BTreeSet<_> = [
        "libc.so.6",
        "libm.so.6",
        "libpthread.so.0",
        "libdl.so.2",
        "librt.so.1",
        "libresolv.so.2",
        "libutil.so.1",
        "ld-linux-x86-64.so.2",
    ]
    .into_iter()
    .collect();
    if allowed != expected {
        return Err("PDF system-library policy must remain exactly the glibc/loader family".into());
    }
    let mut maximum = (0u32, 0u32);
    for name in files.keys() {
        let path = directory.join(name);
        let mut header = [0; 4];
        use std::io::Read;
        fs::File::open(&path)
            .and_then(|mut f| f.read_exact(&mut header))
            .map_err(|e| e.to_string())?;
        if &header != b"\x7fELF" {
            if name == "pdfinfo" || name == "pdftoppm" || name.starts_with("pdf-libs/") {
                return Err(format!("PDF native file is not ELF: {name}"));
            }
            continue;
        }
        let result = Command::new("readelf")
            .args(["-h", "-d", "--version-info"])
            .arg(&path)
            .output()
            .map_err(|e| e.to_string())?;
        if !result.status.success() {
            return Err(format!("readelf refused PDF native file: {name}"));
        }
        let text = String::from_utf8(result.stdout).map_err(|e| e.to_string())?;
        if !text.contains("ELF64") || !text.contains("Advanced Micro Devices X86-64") {
            return Err(format!("wrong PDF ELF architecture: {name}"));
        }
        for line in text.lines() {
            if line.contains("(NEEDED)") {
                let soname = line
                    .split_once('[')
                    .and_then(|(_, s)| s.split_once(']'))
                    .map(|(s, _)| s)
                    .ok_or("invalid ELF NEEDED")?;
                if !allowed.contains(soname) && !files.contains_key(&format!("pdf-libs/{soname}")) {
                    return Err(format!("unbundled PDF ELF dependency: {name}: {soname}"));
                }
            }
        }
        for rest in text.split("GLIBC_").skip(1) {
            let version: String = rest
                .chars()
                .take_while(|c| c.is_ascii_digit() || *c == '.')
                .collect();
            if let Some((a, b)) = version.split_once('.') {
                if let (Ok(a), Ok(b)) = (a.parse::<u32>(), b.parse::<u32>()) {
                    maximum = maximum.max((a, b));
                }
            }
        }
    }
    if maximum > (2, 35) {
        return Err(format!(
            "PDF bundle exceeds unchanged GLIBC2.35 ceiling: {}.{}",
            maximum.0, maximum.1
        ));
    }
    Ok(format!("{}.{}", maximum.0, maximum.1))
}
pub fn dispatch(request: &Value) -> Result<Value, String> {
    let root = Path::new(request["root"].as_str().ok_or("source root missing")?);
    let (lock, lock_sha) = lock(root, request["lock"].as_str())?;
    let input = Path::new(
        request["directory"]
            .as_str()
            .ok_or("PDF bundle directory missing")?,
    );
    let files = inventory(input)?;
    if serde_json::to_value(&files).map_err(|e| e.to_string())? != lock["files"] {
        return Err("PDF bundle inventory/hash/permissions differ from source pins".into());
    }
    let glibc = elf_admission(input, &files, &lock["system_glibc_sonames"])?;
    match request["action"].as_str() {
        Some("verify") => (),
        Some("stage") => {
            let destination = Path::new(
                request["destination"]
                    .as_str()
                    .ok_or("PDF bundle destination missing")?,
            );
            regular(destination, true)?;
            for name in ROOTS {
                if destination.join(name).exists()
                    || fs::symlink_metadata(destination.join(name)).is_ok()
                {
                    return Err(format!("refusing to overwrite PDF bundle: {name}"));
                }
            }
            for name in files.keys() {
                let target = destination.join(name);
                fs::create_dir_all(target.parent().ok_or("missing target parent")?)
                    .map_err(|e| e.to_string())?;
                fs::copy(input.join(name), target).map_err(|e| e.to_string())?;
            }
            if inventory(destination)? != files {
                return Err("staged PDF bundle changed".into());
            }
        }
        _ => return Err("unknown PDF bundle action".into()),
    }
    let sources: BTreeSet<_> = lock["packages"]
        .as_object()
        .unwrap()
        .values()
        .filter_map(|p| p["source_rpm"].as_str())
        .collect();
    Ok(
        json!({"schema":1,"renderer":"Poppler","version":lock["renderer_version"],"lock_sha256":lock_sha,"files":files.len(),"corresponding_source_packages":sources.len(),"required_glibc":glibc}),
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_escape_and_unrelated_payload() {
        for name in [
            "../pdfinfo",
            "/pdfinfo",
            "pdf-libs/../../owner",
            "pdf-libs\\bad",
            "pdf-libs/x:y",
            "other/tool",
        ] {
            assert!(relative(name).is_err(), "{name}");
        }
        for name in [
            "pdfinfo",
            "pdf-libs/libpoppler.so.104",
            "pdf-source/poppler.src.rpm",
        ] {
            assert!(relative(name).is_ok());
        }
    }
}
