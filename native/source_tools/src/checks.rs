//! Authored publication/asset/retirement checks. These replace Python tooling tests,
//! not application/Blender qualification.
use crate::*;
use std::collections::BTreeSet;
#[derive(Default)]
pub struct Checks {
    pub passed: Vec<String>,
    pub failed: Vec<String>,
}
impl Checks {
    pub fn check(&mut self, ok: bool, label: impl Into<String>) {
        if ok {
            self.passed.push(label.into())
        } else {
            self.failed.push(label.into())
        }
    }
    pub fn finish(self) -> Value {
        json!({"ok":self.failed.is_empty(),"passed":self.passed.len(),"failures":self.failed,"labels":self.passed})
    }
}
fn text(root: &Path, path: &str) -> Result<String, String> {
    fs::read_to_string(root.join(path)).map_err(|e| format!("{path}: {e}"))
}
fn all(text: &str, need: &[&str]) -> bool {
    need.iter().all(|s| text.contains(s))
}
fn rx(pattern: &str, text: &str) -> bool {
    Regex::new(pattern).unwrap().is_match(text)
}
fn no_fences(text: &str) -> String {
    Regex::new(r"(?s)```.*?```|~~~.*?~~~|<!--.*?-->")
        .unwrap()
        .replace_all(text, "")
        .into_owned()
}
fn targets(text: &str, images: bool) -> BTreeSet<String> {
    let text = no_fences(text);
    let defs = Regex::new(r"(?m)^\s*\[([^\]]+)\]:\s*<?([^\s>]+)>?")
        .unwrap()
        .captures_iter(&text)
        .map(|c| (c[1].to_string(), c[2].to_string()))
        .collect::<BTreeMap<_, _>>();
    let mut out = BTreeSet::new();
    for c in Regex::new(r#"!?\[[^\]\n]+\]\(<?([^\s)>]+)>?(?:\s+"[^"]*")?\)"#)
        .unwrap()
        .captures_iter(&text)
    {
        let m = c.get(0).unwrap();
        let image = m.as_str().starts_with('!');
        if image == images && (image || m.start() == 0 || text.as_bytes()[m.start() - 1] != b'!') {
            out.insert(c[1].into());
        }
    }
    for c in Regex::new(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]")
        .unwrap()
        .captures_iter(&text)
    {
        let m = c.get(0).unwrap();
        if m.as_str().starts_with('!') == images {
            if let Some(v) = defs.get(if c[2].is_empty() { &c[1] } else { &c[2] }) {
                out.insert(v.clone());
            }
        }
    }
    out
}
fn anchors(text: &str) -> BTreeSet<String> {
    let text = no_fences(text);
    let mut out = BTreeSet::new();
    let remove = Regex::new(r"[^\w\- ]").unwrap();
    for c in Regex::new(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$")
        .unwrap()
        .captures_iter(&text)
    {
        let base = remove
            .replace_all(&c[1].to_lowercase(), "")
            .replace(' ', "-");
        let mut slug = base.clone();
        let mut n = 0;
        while out.contains(&slug) {
            n += 1;
            slug = format!("{base}-{n}");
        }
        out.insert(slug);
    }
    out
}
fn crc32(data: &[u8]) -> u32 {
    let mut c = !0u32;
    for &b in data {
        c ^= b as u32;
        for _ in 0..8 {
            c = if c & 1 != 0 {
                (c >> 1) ^ 0xedb88320
            } else {
                c >> 1
            }
        }
    }
    !c
}
pub fn screenshot(data: &[u8]) -> Result<((u32, u32), String), String> {
    if !data.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Err("not a PNG".into());
    }
    let (mut at, mut chunks) = (8, Vec::new());
    while at < data.len() {
        let header = data.get(at..at + 8).ok_or("truncated PNG header")?;
        let size = u32::from_be_bytes(header[..4].try_into().unwrap()) as usize;
        let end = at
            .checked_add(12)
            .and_then(|n| n.checked_add(size))
            .ok_or("PNG size overflow")?;
        let chunk = data.get(at + 4..end - 4).ok_or("truncated PNG chunk")?;
        let checksum = data.get(end - 4..end).ok_or("truncated PNG checksum")?;
        if crc32(chunk) != u32::from_be_bytes(checksum.try_into().unwrap()) {
            return Err("PNG chunk CRC mismatch".into());
        }
        chunks.push((&chunk[..4], &chunk[4..]));
        at = end;
    }
    if chunks.first().map(|x| x.0) != Some(b"IHDR".as_slice())
        || chunks.last() != Some(&(b"IEND".as_slice(), b"".as_slice()))
    {
        return Err("missing PNG header/end".into());
    }
    let h = chunks[0].1;
    if h.len() != 13 {
        return Err("invalid PNG header".into());
    }
    let w = u32::from_be_bytes(h[..4].try_into().unwrap());
    let height = u32::from_be_bytes(h[4..8].try_into().unwrap());
    if w == 0 || height == 0 || h[8] != 8 || ![2, 6].contains(&h[9]) || h[10..] != [0, 0, 0] {
        return Err("expected a non-interlaced 8-bit RGB/RGBA capture".into());
    }
    let stride = (w as usize)
        .checked_mul(if h[9] == 2 { 3 } else { 4 })
        .and_then(|n| n.checked_add(1))
        .ok_or("PNG size overflow")?;
    let expected = (height as usize)
        .checked_mul(stride)
        .filter(|n| *n <= 512 * 1024 * 1024)
        .ok_or("PNG scanline limit")?;
    let packed: Vec<u8> = chunks
        .iter()
        .filter(|x| x.0 == b"IDAT")
        .flat_map(|x| x.1.iter().copied())
        .collect();
    let mut decoder = flate2::Decompress::new(true);
    let mut pixels = vec![0; expected + 1];
    let status = decoder
        .decompress(&packed, &mut pixels, flate2::FlushDecompress::Finish)
        .map_err(|e| e.to_string())?;
    if status != flate2::Status::StreamEnd
        || decoder.total_out() != expected as u64
        || decoder.total_in() != packed.len() as u64
        || pixels[..expected].chunks(stride).any(|r| r[0] > 4)
    {
        return Err("invalid PNG scanlines".into());
    }
    pixels.truncate(expected);
    Ok(((w, height), sha(&pixels)))
}
fn walk_files(root: &Path, base: &str) -> Result<Vec<String>, String> {
    let path = root.join(base);
    if !path.is_dir() {
        return Ok(Vec::new());
    }
    let mut all = Vec::new();
    recurse(root, &path, &mut all, false)?;
    Ok(all
        .into_iter()
        .filter(|p| p.is_file())
        .map(|p| {
            p.strip_prefix(root)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/")
        })
        .collect())
}
pub fn completeness(root: &Path, c: &Value) -> Result<Value, String> {
    let mut out = Checks::default();
    let public: BTreeSet<_> = public_files(root, c)?.into_iter().collect();
    let mut required = BTreeSet::new();
    for base in [
        "native/automation_core",
        "native/source_tools",
        "native/geometry_core",
        "native/automation_test_driver",
        "native/welcome",
        "packaging/windows/portable",
    ] {
        for file in ["Cargo.toml", "Cargo.lock"] {
            required.insert(format!("{base}/{file}"));
        }
        let dirs: &[&str] = if base == "native/source_tools" {
            &["src", "tests"]
        } else {
            &["src", "cpp", "include"]
        };
        for dir in dirs {
            for p in walk_files(root, &format!("{base}/{dir}"))? {
                if [".rs", ".json", ".cpp", ".inc", ".h"].contains(&suffix(&p).as_str()) {
                    required.insert(p);
                }
            }
        }
    }
    required.insert("native/geometry_core/build.rs".into());
    required.insert("native/pure_core/tests/interpolate32_properties.rs".into());
    for p in COMPLETENESS_SOURCES {
        required.insert((*p).into());
    }
    let source_entries: Entries = public
        .iter()
        .filter(|p| p.ends_with(".sh") || (p.starts_with("spike/") && p.ends_with(".py")))
        .map(|p| {
            Ok((
                p.clone(),
                (
                    "100644".into(),
                    fs::read(root.join(p)).map_err(|e| e.to_string())?,
                ),
            ))
        })
        .collect::<Result<_, String>>()?;
    let audited = required_public_paths(root, &source_entries, c);
    let fixed: BTreeSet<_> = strings(&c["REQUIRED"]).into_iter().collect();
    required.extend(audited.difference(&fixed).cloned());
    out.check(
        required.is_subset(&public),
        "explicit public allowlist closes native and focused-test sources",
    );
    out.check(
        required.is_subset(&audited),
        "public audit requires the complete native/test/harness closure",
    );
    let mut entries: Entries = audited
        .iter()
        .map(|p| (p.clone(), ("100644".into(), b"pass\n".to_vec())))
        .collect();
    entries.extend(
        source_entries
            .iter()
            .filter(|(p, _)| p.ends_with(".sh"))
            .map(|(p, e)| (p.clone(), e.clone())),
    );
    for p in &required {
        let mut candidate = entries.clone();
        candidate.remove(p);
        out.check(
            audit_entries(root, &candidate, true, c)
                .contains(&format!("missing required public file: {p}")),
            format!("source/fixture omission rejected by name: {p}"),
        );
    }
    out.check(
        !public.iter().any(|p| {
            [
                "docs/agent_tasks/",
                "docs/automation/",
                "docs/surfacing/",
                "tmp/",
                "artifacts/",
                "vendor/",
                "tools/",
            ]
            .iter()
            .any(|s| p.starts_with(s))
                || p.contains("/target/")
                || [
                    ".so",
                    ".dll",
                    ".dylib",
                    ".pyd",
                    ".exe",
                    ".pyc",
                    ".receipt.json",
                ]
                .iter()
                .any(|s| p.ends_with(s))
                || (p.starts_with("narwhal3d/native/") && p != "narwhal3d/native/.gitignore")
        }),
        "native closure does not admit private notes, scratch data or runtime binaries",
    );
    let fixture = Entries::from([
        (
            "run_tests.sh".into(),
            (
                "100755".into(),
                b"if test -n DISPLAY; then\n bash spike/closure_gui.sh\nfi\n".to_vec(),
            ),
        ),
        (
            "spike/closure_gui.sh".into(),
            (
                "100755".into(),
                b"python3 spike/test_closure_case.py\n".to_vec(),
            ),
        ),
        (
            "spike/test_closure_case.py".into(),
            ("100644".into(), b"import test_closure_fixture\n".to_vec()),
        ),
        (
            "spike/test_closure_fixture.py".into(),
            ("100644".into(), b"pass\n".to_vec()),
        ),
    ]);
    let found = required_public_paths(root, &fixture, c);
    out.check(
        fixture.keys().all(|p| found.contains(p)),
        "conditional shell suite and imported fixture are required",
    );
    for p in fixture.keys().filter(|p| p.as_str() != "run_tests.sh") {
        let mut missing = fixture.clone();
        missing.remove(p);
        out.check(
            audit_entries(root, &missing, true, c)
                .contains(&format!("missing required public file: {p}")),
            format!("harness dependency omission is rejected: {p}"),
        );
    }
    let comment = Entries::from([(
        "run_tests.sh".into(),
        (
            "100755".into(),
            b"# python3 spike/test_not_executed.py\n".to_vec(),
        ),
    )]);
    out.check(
        !required_public_paths(root, &comment, c).contains("spike/test_not_executed.py"),
        "comment-only references do not admit historical fixtures",
    );
    let image = "scripts/ui_bundle/references/workspace-light.png";
    out.check(
        path_problem(image, c).is_none()
            && path_problem(&image.replace("workspace-light", "unreviewed"), c).is_some(),
        "only the exact reviewed preview screenshot is permitted",
    );
    for (name, digest) in [
        (
            "Inter.woff2",
            "fb865a5087637ba194b14aef6f0558214f3c4b3ec939e3c0812c66de41036a47",
        ),
        (
            "Ubuntu-Light.ttf",
            "80307b8da7649aa4ee4d484b232140e3ce1ec0ca093073d3c53c8f5a5ced7a70",
        ),
        (
            "OFL-1.1.txt",
            "c1a781a6a032bf00b1bbffc388fd6936bf28a0ded472c263bcff88b181dea456",
        ),
        (
            "UFL.txt",
            "2f0015108d68627bd788d313f529c21ff4da2c2c42a5e1f3883acc83480f9002",
        ),
    ] {
        out.check(
            sha(&fs::read(root.join("scripts/ui_bundle/fonts").join(name))
                .map_err(|e| e.to_string())?)
                == digest,
            format!("preview font/license bytes are unmodified: {name}"),
        );
    }
    Ok(out.finish())
}
// Maintained reviewed contracts, not a discovery-based publication allowlist.
include!("source_sets.rs");
trait Has {
    fn has(&self, needle: &str) -> bool;
}
impl Has for str {
    fn has(&self, n: &str) -> bool {
        self.contains(n)
    }
}
impl Has for String {
    fn has(&self, n: &str) -> bool {
        self.contains(n)
    }
}
impl Has for BTreeSet<String> {
    fn has(&self, n: &str) -> bool {
        self.contains(n)
    }
}
impl<T: Has + ?Sized> Has for &T {
    fn has(&self, n: &str) -> bool {
        (*self).has(n)
    }
}
fn has<T: Has + ?Sized>(haystack: &T, needle: impl AsRef<str>) -> bool {
    haystack.has(needle.as_ref())
}
fn union(a: &BTreeSet<String>, b: &BTreeSet<String>) -> BTreeSet<String> {
    a.union(b).cloned().collect()
}
fn toml_file(root: &Path, path: &str) -> Result<toml::Value, String> {
    toml::from_str(&text(root, path)?).map_err(|e| format!("{path}: {e}"))
}
fn percent_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            if let Ok(v) = u8::from_str_radix(&s[i + 1..i + 3], 16) {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1
    }
    String::from_utf8_lossy(&out).into_owned()
}
pub fn project(root: &Path, c: &Value) -> Result<Value, String> {
    let mut out = Checks::default();
    let public: BTreeSet<_> = public_files(root, c)?.into_iter().collect();
    let required_docs = [
        "README.md",
        "STATUS.md",
        "COMPATIBILITY.md",
        "ROADMAP.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SUPPORT.md",
        "GOVERNANCE.md",
        "CODE_OF_CONDUCT.md",
        "docs/gaps.md",
        "docs/github-project.md",
        "docs/windows-build.md",
        "docs/macos-build.md",
        "docs/technical-reference.md",
        "docs/contributing-with-ai.md",
    ];
    out.check(
        required_docs.iter().all(|p| root.join(p).is_file()),
        "the public project and governance document set is complete",
    );
    let readme = text(root, "README.md")?;
    let technical_reference = text(root, "docs/technical-reference.md")?;
    let readme_links = targets(&readme, false);
    let readme_images = targets(&readme, true);
    let reference_links = targets(&technical_reference, false);
    let reference_images = targets(&technical_reference, true);
    for link in ["docs/technical-reference.md", "CONTRIBUTING.md", "LICENSE"] {
        out.check(
            readme_links.contains(link),
            format!("README has a clickable {link} link"),
        );
    }
    let welcome_manifest = toml_file(root, "native/welcome/Cargo.toml")?;
    let source_version = welcome_manifest["package"]["version"]
        .as_str()
        .ok_or("welcome version missing")?;
    let landing_re = Regex::new(r"Current release: \[([0-9]+\.[0-9]+\.[0-9]+)\]").unwrap();
    let landing = landing_re
        .captures(&readme)
        .ok_or("landing release missing")?;
    let landing_version = &landing[1];
    let mut image_data = Vec::new();
    for image in [
        "docs/images/workspace-light.png",
        "docs/images/workspace-dark.png",
    ] {
        out.check(
            readme_images.contains(image),
            format!("README embeds {image}"),
        );
        match fs::read(root.join(image))
            .map_err(|e| e.to_string())
            .and_then(|b| screenshot(&b))
        {
            Ok(value) => {
                out.check(
                    value.0 .0 >= 1200,
                    format!("{image} is at least 1200 pixels wide"),
                );
                image_data.push(value)
            }
            Err(e) => out.check(false, format!("{image} is a readable screenshot PNG: {e}")),
        }
    }
    let status = text(root, "STATUS.md")?;
    let packaging = text(root, "packaging/README.md")?;
    let changelog = text(root, "CHANGELOG.md")?;
    let release_notes = changelog.split("## 0.4.6 —").next().unwrap_or("");
    let capture = text(root, "scripts/capture_readme_demo.py")?;
    let recorder = text(root, "scripts/build_readme_demo.sh")?;
    let roadmap = text(root, "ROADMAP.md")?;
    let gaps = text(root, "docs/gaps.md")?;
    let windows_guide = text(root, "docs/windows-build.md")?;
    let form_names: BTreeSet<String> = fs::read_dir(root.join(".github/ISSUE_TEMPLATE"))
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|s| s.ends_with(".yml"))
        .collect();
    let form_text: BTreeMap<String, String> = form_names
        .iter()
        .map(|name| {
            Ok((
                name.clone(),
                text(root, &format!(".github/ISSUE_TEMPLATE/{name}"))?,
            ))
        })
        .collect::<Result<_, String>>()?;
    let cross_platform_forms = ["bug.yml", "ui.yml", "performance.yml", "import-export.yml"];
    include!("doc_contracts.rs");
    out.check(
        form_text
            .values()
            .all(|t| !format!("\n{t}").contains("\nabout:")),
        "issue forms use the current description schema",
    );
    let welcome_lock = toml_file(root, "native/welcome/Cargo.lock")?;
    let versions: Vec<_> = welcome_lock["package"]
        .as_array()
        .ok_or("welcome lock packages missing")?
        .iter()
        .filter(|p| p["name"].as_str() == Some("narwhal-welcome"))
        .filter_map(|p| p["version"].as_str())
        .collect();
    out.check(
        versions == [source_version]
            && text(root, "native/welcome/src/main.rs")?.contains(&format!(
                "assert_eq!(env!(\"CARGO_PKG_VERSION\"), \"{source_version}\");"
            )),
        "welcome manifest, lock and Rust version assertion agree on the current release",
    );
    let gif = fs::read(root.join("docs/readme-command-loft.gif")).map_err(|e| e.to_string())?;
    out.check(
        gif.starts_with(b"GIF8") && gif.len() > 100_000,
        "the README command demo is a non-empty GIF",
    );
    let logo = fs::read(root.join("docs/images/bugreport.png")).map_err(|e| e.to_string())?;
    out.check(
        logo.starts_with(b"\x89PNG\r\n\x1a\n")
            && logo.get(16..24) == Some([0, 0, 0, 96, 0, 0, 0, 96].as_slice()),
        "the bug-report logo is a 96x96 PNG",
    );
    out.check(
        [".github/PULL_REQUEST_TEMPLATE.md", ".github/CODEOWNERS"]
            .iter()
            .all(|p| root.join(p).is_file()),
        "pull-request guidance and review ownership are present",
    );
    for (document, links) in [
        ("README.md", union(&readme_links, &readme_images)),
        (
            "docs/technical-reference.md",
            union(&reference_links, &reference_images),
        ),
        ("STATUS.md", targets(&status, false)),
        ("CHANGELOG.md", targets(&changelog, false)),
        ("packaging/README.md", targets(&packaging, false)),
    ] {
        for target in links {
            if target.starts_with("//") || rx(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", &target) {
                continue;
            }
            let (path, fragment) = target.split_once('#').unwrap_or((&target, ""));
            let path = percent_decode(path.split('?').next().unwrap_or(""));
            let resolved = if path.is_empty() {
                root.join(document)
            } else {
                root.join(document).parent().unwrap().join(path)
            };
            let relative = resolved.canonicalize().ok().and_then(|p| {
                p.strip_prefix(root.canonicalize().ok()?)
                    .ok()
                    .map(|p| p.to_string_lossy().replace('\\', "/"))
            });
            out.check(
                resolved.is_file() && relative.as_ref().is_some_and(|p| public.contains(p)),
                format!("{document} local link resolves to a public source file: {target}"),
            );
            if !fragment.is_empty()
                && resolved.is_file()
                && resolved.extension().is_some_and(|e| e == "md")
            {
                out.check(
                    anchors(&fs::read_to_string(&resolved).map_err(|e| e.to_string())?)
                        .contains(&percent_decode(fragment)),
                    format!("{document} local link fragment exists: {target}"),
                );
            }
        }
    }
    out.check(
        required_docs.iter().all(|p| public.contains(*p))
            && [
                "docs/images/workspace-light.png",
                "docs/images/workspace-dark.png",
                "docs/images/bugreport.png",
                "scripts/collect_windows_build_info.ps1",
                "scripts/build_windows_preview.ps1",
                "scripts/assemble_windows_release.py",
                "scripts/verify_windows_release.py",
                "scripts/release_policy.py",
                "spike/test_windows_collector.py",
                "spike/test_windows_release.py",
                "spike/test_linux_release.py",
                "spike/test_package_completeness.py",
                "spike/test_release_policy.py",
                "scripts/build_macos_source.sh",
                "spike/test_macos_source.py",
            ]
            .iter()
            .all(|p| public.contains(*p)),
        "public documentation and platform build source closure",
    );
    for (label, paths) in PROJECT_SOURCE_GROUPS {
        out.check(paths.iter().all(|p| public.contains(*p)), *label)
    }
    out.check(
        !public.iter().any(|p| {
            p.contains("/target/")
                || p.starts_with("artifacts/")
                || p == "narwhal3d/kernel/brep_writer.cpp"
        }) && !public.contains("POLICY.md"),
        "instruction inputs, linked binaries, build trees and planning artifacts remain private",
    );
    let base = "native/vendor/tracedraw";
    let manifest: Value =
        serde_json::from_str(&text(root, &format!("{base}/SOURCE_MANIFEST.json"))?)
            .map_err(|e| e.to_string())?;
    let mut files = BTreeSet::from([
        "LICENSE-MIT".into(),
        "LICENSE-APACHE".into(),
        "CLEANROOM.md".into(),
    ]);
    for dir in ["core/src", "cdr/src"] {
        files.extend(
            walk_files(root, &format!("{base}/{dir}"))?
                .into_iter()
                .map(|p| p[base.len() + 1..].to_string()),
        );
    }
    let hash_map = |v: &Value| -> bool {
        v.as_object().is_some_and(|m| {
            m.iter().all(|(p, d)| {
                fs::read(root.join(base).join(p)).is_ok_and(|b| d.as_str() == Some(&sha(&b)))
            })
        })
    };
    out.check(
        manifest["revision"] == "821ba1545e3d3f75843fe7d3f912214800929821"
            && manifest["files"]
                .as_object()
                .is_some_and(|m| m.keys().cloned().collect::<BTreeSet<_>>() == files)
            && hash_map(&manifest["files"])
            && manifest["adapted_files"].as_object().is_some_and(|m| {
                m.keys().map(String::as_str).collect::<BTreeSet<_>>()
                    == BTreeSet::from([
                        "core/Cargo.toml",
                        "core/.gitignore",
                        "cdr/Cargo.toml",
                        "cdr/.gitignore",
                        "README.md",
                    ])
            })
            && hash_map(&manifest["adapted_files"])
            && manifest["dependency_notices_sha256"].as_str()
                == Some(&sha(&fs::read(
                    root.join("licenses/TraceDraw-Rust-Dependencies.txt"),
                )
                .map_err(|e| e.to_string())?)),
        "vendored TraceDraw source matches its pinned cryptographic inventory",
    );
    for (lock, notices, label) in [
        (
            "native/cdr_multiversion/Cargo.lock",
            "licenses/TraceDraw-Rust-Dependencies.txt",
            "static multi-version helper notices cover every locked registry package",
        ),
        (
            "native/picture_trace/Cargo.lock",
            "licenses/PictureTrace-Rust-Dependencies.txt",
            "picture tracer notices cover every locked registry package",
        ),
    ] {
        let lock = toml_file(root, lock)?;
        let packages: Vec<_> = lock["package"]
            .as_array()
            .ok_or("lock packages missing")?
            .iter()
            .filter(|p| {
                p.get("source")
                    .and_then(toml::Value::as_str)
                    .unwrap_or("")
                    .starts_with("registry+")
            })
            .collect();
        let notices = text(root, notices)?;
        out.check(
            !packages.is_empty()
                && packages.iter().all(|p| {
                    notices.contains(&format!(
                        "PACKAGE {} {}\n",
                        p["name"].as_str().unwrap_or(""),
                        p["version"].as_str().unwrap_or("")
                    ))
                }),
            label,
        );
    }
    out.check(
        [
            ("VectorCraft-MIT.txt", "VectorCraft-LICENSE-MIT"),
            ("VectorCraft-Apache-2.0.txt", "VectorCraft-LICENSE-APACHE"),
            ("VectorCraft-NOTICE.txt", "VectorCraft-NOTICE"),
        ]
        .iter()
        .all(|(a, b)| {
            fs::read(root.join("licenses").join(a)).ok()
                == fs::read(root.join("native/picture_trace/LICENSES").join(b)).ok()
        }),
        "vendored VectorCraft license texts are byte-identical",
    );
    let compatibility = text(root, "COMPATIBILITY.md")?;
    let target_name = ["Rhi", "noceros 3D"].concat();
    out.check(
        compatibility.matches(&target_name).count() == 1
            && all(
                &compatibility,
                &[
                    &["https://www.rhi", "no3d.com/"].concat(),
                    "independently developed",
                    "not affiliated",
                ],
            ),
        "compatibility target identified once with independence statement",
    );
    let mut hits = Vec::new();
    for p in &public {
        if legacy_match(&lower(p.as_bytes()), false).is_some() {
            hits.push(p.clone());
            continue;
        }
        if [".png", ".gif", ".svg"].contains(&suffix(p).to_lowercase().as_str())
            || p == "COMPATIBILITY.md"
        {
            continue;
        }
        if legacy_match(
            &legacy_scan_bytes(p, &fs::read(root.join(p)).map_err(|e| e.to_string())?),
            false,
        )
        .is_some()
        {
            hits.push(p.clone())
        }
    }
    out.check(hits.is_empty(),format!("legacy target naming confined to compatibility and exact upstream attribution: {hits:?}"));
    Ok(out.finish())
}
