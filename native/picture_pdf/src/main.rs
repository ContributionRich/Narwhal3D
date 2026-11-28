//! OURS: first page only, 144 dpi capped at 4096 pixels/side, local Poppler.
//! The caller MUST use the existing converter supervisor (20s/512MiB).
//! Reference image only: no PDF entities become document geometry.
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};
const MAX_INPUT: u64 = 64 * 1024 * 1024;
const MAX_SIDE: u32 = 4096;
const MAX_PAGES: u32 = 10000;
const POLICY: &str = "pdf-picture-v1-crop-p1-144dpi-4096";

#[derive(Debug)]
struct Page {
    pages: u32,
    width: f64,
    height: f64,
}
fn page_info(text: &str) -> Result<Page, String> {
    let mut pages = None;
    let mut size = None;
    let mut rotation = 0i32;
    let mut encryption_checked = false;
    for line in text.lines() {
        let (key, value) = match line.split_once(':') {
            Some(v) => v,
            None => continue,
        };
        let key = key.trim();
        let value = value.trim();
        if key == "Encrypted" {
            if value != "no" {
                return Err("encrypted PDF is not supported by Picture".into());
            }
            encryption_checked = true;
        }
        if key == "Pages" {
            pages = value.parse::<u32>().ok();
        }
        if key.starts_with("Page") && key.ends_with("size") {
            let words: Vec<_> = value.split_whitespace().collect();
            if words.len() >= 4 && words[1] == "x" && words[3] == "pts" {
                size = words[0]
                    .parse::<f64>()
                    .ok()
                    .zip(words[2].parse::<f64>().ok());
            }
        }
        if key.starts_with("Page") && key.ends_with("rot") {
            rotation = value.parse().map_err(|_| "invalid PDF rotation")?;
        }
    }
    if !encryption_checked {
        return Err("PDF encryption status is unavailable".into());
    }
    let pages = pages
        .filter(|n| *n > 0 && *n <= MAX_PAGES)
        .ok_or("PDF page count exceeds limit or is invalid")?;
    let (mut width, mut height) = size.ok_or("PDF first-page dimensions are unavailable")?;
    if !width.is_finite()
        || !height.is_finite()
        || width <= 0.
        || height <= 0.
        || width.max(height) > 14400.
    {
        return Err("PDF page dimensions exceed the 200-inch Picture limit".into());
    }
    match rotation.rem_euclid(360) {
        0 | 180 => (),
        90 | 270 => std::mem::swap(&mut width, &mut height),
        _ => return Err("unsupported PDF page rotation".into()),
    }
    Ok(Page {
        pages,
        width,
        height,
    })
}
fn png_size(path: &Path) -> Result<(u32, u32), String> {
    let mut head = [0u8; 24];
    fs::File::open(path)
        .and_then(|mut f| f.read_exact(&mut head))
        .map_err(|e| format!("PDF renderer produced no PNG: {e}"))?;
    if &head[..8] != b"\x89PNG\r\n\x1a\n" || &head[12..16] != b"IHDR" {
        return Err("PDF renderer produced invalid PNG".into());
    }
    let w = u32::from_be_bytes(head[16..20].try_into().unwrap());
    let h = u32::from_be_bytes(head[20..24].try_into().unwrap());
    if w == 0 || h == 0 || w > MAX_SIDE || h > MAX_SIDE {
        return Err("PDF raster exceeds pixel limit".into());
    }
    Ok((w, h))
}
#[cfg(target_os = "linux")]
fn limits() -> Result<(), String> {
    // Inherited by Poppler. Its rasterizer does not execute PDF JavaScript/URLs.
    // Deny network socket creation as additional containment, not a general sandbox.
    unsafe {
        for (resource, value) in [
            (libc::RLIMIT_CPU, 12),
            (libc::RLIMIT_AS, 512 * 1024 * 1024),
            (libc::RLIMIT_FSIZE, 64 * 1024 * 1024),
            (libc::RLIMIT_NOFILE, 64),
        ] {
            let limit = libc::rlimit {
                rlim_cur: value,
                rlim_max: value,
            };
            if libc::setrlimit(resource, &limit) != 0 {
                return Err("could not apply PDF resource limits".into());
            }
        }
        let mut filter = [
            libc::sock_filter {
                code: 0x20,
                jt: 0,
                jf: 0,
                k: 0,
            }, // seccomp_data.nr
            libc::sock_filter {
                code: 0x15,
                jt: 0,
                jf: 1,
                k: libc::SYS_socket as u32,
            },
            libc::sock_filter {
                code: 0x06,
                jt: 0,
                jf: 0,
                k: 0x0005_0000 | libc::EPERM as u32,
            },
            libc::sock_filter {
                code: 0x06,
                jt: 0,
                jf: 0,
                k: 0x7fff_0000,
            },
        ];
        let program = libc::sock_fprog {
            len: filter.len() as u16,
            filter: filter.as_mut_ptr(),
        };
        if libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0
            || libc::prctl(libc::PR_SET_SECCOMP, 2, &program) != 0
        {
            return Err("could not contain PDF network access".into());
        }
    }
    Ok(())
}
#[cfg(not(target_os = "linux"))]
fn limits() -> Result<(), String> {
    Err("PDF Pictures currently require Linux Poppler and the native converter supervisor".into())
}
#[derive(Debug)]
struct Renderer {
    info: PathBuf,
    raster: PathBuf,
    bundled: Option<PathBuf>,
}
impl Renderer {
    // Resolve only from the kernel's executable identity, never CWD, argv[0], or an
    // environment search path. A partial/missing bundle must not use host Poppler.
    fn installed() -> Result<Self, String> {
        let exe =
            std::env::current_exe().map_err(|e| format!("PDF helper identity unavailable: {e}"))?;
        Self::bundled_at(&exe)
    }
    fn bundled_at(exe: &Path) -> Result<Self, String> {
        let bin = exe.parent().ok_or("PDF helper has no runtime directory")?;
        let info = bin.join("pdfinfo");
        let raster = bin.join("pdftoppm");
        for (path, directory) in [
            (bin.to_path_buf(), true),
            (info.clone(), false),
            (raster.clone(), false),
            (bin.join("pdf-libs"), true),
            (bin.join("pdf-data"), true),
            (bin.join("pdf-data/fonts.conf"), false),
        ] {
            let metadata = fs::symlink_metadata(&path)
                .map_err(|_| format!("bundled PDF renderer missing: {}; developer use requires --developer-system-poppler", path.display()))?;
            if metadata.file_type().is_symlink()
                || metadata.is_dir() != directory
                || (!directory && !metadata.is_file())
            {
                return Err(format!(
                    "bundled PDF renderer must be regular: {}",
                    path.display()
                ));
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                let owner = fs::metadata(exe).map_err(|e| e.to_string())?.uid();
                if metadata.uid() != owner || metadata.mode() & 0o022 != 0 {
                    return Err(format!(
                        "untrusted bundled PDF renderer permissions: {}",
                        path.display()
                    ));
                }
            }
        }
        Ok(Self {
            info,
            raster,
            bundled: Some(bin.to_path_buf()),
        })
    }
    fn developer_system() -> Self {
        Self {
            info: "/usr/bin/pdfinfo".into(),
            raster: "/usr/bin/pdftoppm".into(),
            bundled: None,
        }
    }
}
struct Stage(PathBuf);
impl Drop for Stage {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn tool(
    program: &Path,
    renderer: &Renderer,
    args: &[&std::ffi::OsStr],
    cwd: &Path,
    name: &str,
) -> Result<String, String> {
    let stdout = cwd.join(format!("{name}.stdout"));
    let stderr = cwd.join(format!("{name}.stderr"));
    let mut command = Command::new(program);
    command.args(args).env_clear().env("LC_ALL", "C");
    if let Some(bin) = &renderer.bundled {
        command
            .env("LD_LIBRARY_PATH", bin.join("pdf-libs"))
            .env("FONTCONFIG_FILE", bin.join("pdf-data/fonts.conf"))
            .env("FONTCONFIG_PATH", bin.join("pdf-data"))
            .env("XDG_CACHE_HOME", cwd)
            .env("POPPLER_DATADIR", bin.join("pdf-data/poppler"));
    }
    let status = command
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(fs::File::create(&stdout).map_err(|e| e.to_string())?)
        .stderr(fs::File::create(&stderr).map_err(|e| e.to_string())?)
        .status()
        .map_err(|e| format!("{} unavailable: {e}", program.display()))?;
    if !status.success() {
        return Err(format!(
            "PDF {name} refused the file (status {status}); corrupt/encrypted or resource limit"
        ));
    }
    let mut bytes = Vec::new();
    fs::File::open(stdout)
        .and_then(|f| f.take(65537).read_to_end(&mut bytes))
        .map_err(|e| e.to_string())?;
    if bytes.len() > 65536 {
        return Err("PDF metadata exceeds limit".into());
    }
    String::from_utf8(bytes).map_err(|_| "invalid PDF metadata encoding".into())
}
fn render(input: &Path, cache: &Path, renderer: &Renderer) -> Result<serde_json::Value, String> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    let f = options
        .open(input)
        .map_err(|e| format!("cannot read PDF: {e}"))?;
    if !f.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("PDF input must be a regular file".into());
    }
    let mut bytes = Vec::new();
    f.take(MAX_INPUT + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_INPUT {
        return Err("PDF exceeds 64MiB input limit".into());
    }
    if !bytes.starts_with(b"%PDF-") {
        return Err("not a PDF file".into());
    }
    let hash = format!("{:x}", Sha256::digest(&bytes));
    fs::create_dir_all(cache).map_err(|e| e.to_string())?;
    let cache = cache.canonicalize().map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&cache, fs::Permissions::from_mode(0o700))
            .map_err(|e| e.to_string())?;
    }
    let stage = Stage(cache.join(format!(
            ".pdf-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|e| e.to_string())?
                .as_nanos()
        )));
    fs::create_dir(&stage.0).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&stage.0, fs::Permissions::from_mode(0o700))
            .map_err(|e| e.to_string())?;
    }
    let source = stage.0.join("source.pdf");
    fs::write(&source, bytes).map_err(|e| e.to_string())?;
    let text = tool(
        &renderer.info,
        renderer,
        &[
            "-f".as_ref(),
            "1".as_ref(),
            "-l".as_ref(),
            "1".as_ref(),
            source.as_os_str(),
        ],
        &stage.0,
        "metadata",
    )?;
    let page = page_info(&text)?;
    let dpi = 144f64.min((MAX_SIDE as f64 - 1.) * 72. / page.width.max(page.height));
    let dpi = dpi.to_string();
    let prefix = stage.0.join("page-1");
    tool(
        &renderer.raster,
        renderer,
        &[
            "-f".as_ref(),
            "1".as_ref(),
            "-l".as_ref(),
            "1".as_ref(),
            "-cropbox".as_ref(),
            "-singlefile".as_ref(),
            "-r".as_ref(),
            dpi.as_ref(),
            "-png".as_ref(),
            source.as_os_str(),
            prefix.as_os_str(),
        ],
        &stage.0,
        "rasterizer",
    )?;
    let png = prefix.with_extension("png");
    let (width, height) = png_size(&png)?;
    let raster_hash = format!(
        "{:x}",
        Sha256::digest(fs::read(&png).map_err(|e| e.to_string())?)
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&png, fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
    }
    let dest = cache.join(format!("{hash}-{POLICY}-{raster_hash}.png"));
    // Atomic replacement uses our freshly rendered output, never trusts a stale
    // preexisting cache entry, and concurrent writers have identical content keys.
    fs::rename(&png, &dest).map_err(|e| format!("cannot publish PDF raster: {e}"))?;
    Ok(
        serde_json::json!({"image":dest,"page":1,"pages":page.pages,"width":width,"height":height,"width_inches":page.width/72.,"height_inches":page.height/72.,"source_sha256":hash,"policy":POLICY}),
    )
}
fn main() {
    let result = (|| {
        limits()?;
        let mut args: Vec<_> = std::env::args_os().skip(1).collect();
        let renderer = if args
            .first()
            .is_some_and(|arg| arg == "--developer-system-poppler")
        {
            args.remove(0);
            Renderer::developer_system()
        } else {
            Renderer::installed()?
        };
        if args.len() != 2 {
            return Err(
                "usage: narwhal-picture-pdf [--developer-system-poppler] INPUT CACHE".into(),
            );
        }
        render(Path::new(&args[0]), Path::new(&args[1]), &renderer)
    })();
    match result {
        Ok(value) => println!("{value}"),
        Err(e) => {
            eprintln!("Picture PDF: {e}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    // Existing actual-host fixtures explicitly opt into developer system Poppler.
    fn render(input: &Path, cache: &Path) -> Result<serde_json::Value, String> {
        super::render(input, cache, &Renderer::developer_system())
    }
    fn temp(label: &str) -> Stage {
        let p = std::env::temp_dir().join(format!(
            "narwhal-pdf-{label}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&p).unwrap();
        Stage(p)
    }
    // Generated public PDF: exact page dimensions, independent of owner files.
    fn fixture(path: &Path, pages: u32, width: f64, rotation: u32) {
        fixture_box(path, pages, width, rotation, "");
    }
    fn fixture_box(path: &Path, pages: u32, width: f64, rotation: u32, crop: &str) {
        let mut objects = vec![
            "<< /Type /Catalog /Pages 2 0 R >>".to_string(),
            format!(
                "<< /Type /Pages /Count {pages} /Kids [{}] >>",
                (0..pages)
                    .map(|i| format!("{} 0 R", 3 + 2 * i))
                    .collect::<Vec<_>>()
                    .join(" ")
            ),
        ];
        for i in 0..pages {
            objects.push(format!("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} 72] {crop}/Rotate {rotation} /Resources << >> /Contents {} 0 R >>",4+2*i));
            let drawing = if i == 0 {
                "1 0 0 rg 20 15 70 40 re f\n"
            } else {
                "0 0 1 rg 0 0 144 72 re f\n"
            };
            objects.push(format!(
                "<< /Length {} >>\nstream\n{drawing}endstream",
                drawing.len()
            ));
        }
        let mut out = b"%PDF-1.4\n".to_vec();
        let mut offsets = vec![0];
        for (i, obj) in objects.iter().enumerate() {
            offsets.push(out.len());
            out.extend_from_slice(format!("{} 0 obj\n{obj}\nendobj\n", i + 1).as_bytes());
        }
        let xref = out.len();
        out.extend_from_slice(
            format!("xref\n0 {}\n0000000000 65535 f \n", offsets.len()).as_bytes(),
        );
        for off in offsets.iter().skip(1) {
            out.extend_from_slice(format!("{off:010} 00000 n \n").as_bytes());
        }
        out.extend_from_slice(
            format!(
                "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n",
                offsets.len()
            )
            .as_bytes(),
        );
        fs::write(path, out).unwrap();
    }
    fn packet(page: &str) -> String {
        format!("Pages: 2\nEncrypted: no\nPage 1 size: {page} pts\nPage 1 rot: 0\n")
    }
    fn bundle_fixture() -> (Stage, PathBuf) {
        let stage = temp("bundle-routing");
        let exe = stage.0.join("narwhal-picture-pdf");
        for name in ["narwhal-picture-pdf", "pdfinfo", "pdftoppm"] {
            fs::write(stage.0.join(name), b"fixture").unwrap();
        }
        fs::create_dir(stage.0.join("pdf-libs")).unwrap();
        fs::create_dir(stage.0.join("pdf-data")).unwrap();
        fs::write(stage.0.join("pdf-data/fonts.conf"), b"fixture").unwrap();
        (stage, exe)
    }
    #[test]
    fn bundled_route_is_executable_relative_not_cwd_or_path() {
        let (stage, exe) = bundle_fixture();
        let renderer = Renderer::bundled_at(&exe).unwrap();
        assert_eq!(renderer.info, stage.0.join("pdfinfo"));
        assert_eq!(renderer.raster, stage.0.join("pdftoppm"));
        assert_eq!(renderer.bundled, Some(stage.0.clone()));
        let developer = Renderer::developer_system();
        assert_eq!(developer.info, Path::new("/usr/bin/pdfinfo"));
        assert_eq!(developer.raster, Path::new("/usr/bin/pdftoppm"));
        assert_eq!(developer.bundled, None);
    }
    #[test]
    fn incomplete_bundle_never_falls_back_to_installed_system_poppler() {
        for name in ["pdfinfo", "pdftoppm", "pdf-libs", "pdf-data/fonts.conf"] {
            let (stage, exe) = bundle_fixture();
            let path = stage.0.join(name);
            if path.is_dir() {
                fs::remove_dir(&path).unwrap();
            } else {
                fs::remove_file(&path).unwrap();
            }
            assert!(
                Renderer::bundled_at(&exe)
                    .unwrap_err()
                    .contains("bundled PDF renderer missing"),
                "{name}"
            );
        }
    }
    #[cfg(unix)]
    #[test]
    fn linked_or_writable_bundle_is_refused() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        for name in ["pdfinfo", "pdftoppm", "pdf-libs", "pdf-data/fonts.conf"] {
            let (stage, exe) = bundle_fixture();
            let path = stage.0.join(name);
            let target = stage.0.join("replaced");
            fs::rename(&path, &target).unwrap();
            symlink(&target, &path).unwrap();
            assert!(Renderer::bundled_at(&exe).is_err(), "linked {name}");
        }
        for name in ["pdfinfo", "pdftoppm", "pdf-libs", "pdf-data"] {
            let (stage, exe) = bundle_fixture();
            fs::set_permissions(stage.0.join(name), fs::Permissions::from_mode(0o777)).unwrap();
            assert!(
                Renderer::bundled_at(&exe)
                    .unwrap_err()
                    .contains("untrusted"),
                "writable {name}"
            );
        }
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn renderer_environment_is_fixed_and_does_not_pass_search_paths() {
        let (stage, exe) = bundle_fixture();
        let renderer = Renderer::bundled_at(&exe).unwrap();
        let environment = tool(
            Path::new("/usr/bin/env"),
            &renderer,
            &[],
            &stage.0,
            "environment",
        )
        .unwrap();
        let fields: std::collections::BTreeMap<_, _> = environment
            .lines()
            .map(|line| line.split_once('=').unwrap())
            .collect();
        assert_eq!(fields.len(), 6);
        assert!(!fields.contains_key("PATH"));
        assert!(!fields.contains_key("LD_PRELOAD"));
        assert_eq!(
            fields["LD_LIBRARY_PATH"],
            stage.0.join("pdf-libs").to_str().unwrap()
        );
        assert_eq!(
            fields["FONTCONFIG_FILE"],
            stage.0.join("pdf-data/fonts.conf").to_str().unwrap()
        );
    }
    #[test]
    fn refuses_metadata_limits_and_encryption() {
        for text in [
            packet("NaN x 72"),
            packet("0 x 72"),
            packet("14401 x 72"),
            packet("144 x 72").replace("Pages: 2", "Pages: 10001"),
            packet("144 x 72").replace("Encrypted: no", "Encrypted: yes"),
        ] {
            assert!(page_info(&text).is_err(), "{text}");
        }
    }
    #[test]
    fn physical_dimensions_rotation() {
        let p = page_info(&packet("144 x 72").replace("rot: 0", "rot: 90")).unwrap();
        assert_eq!((p.width, p.height, p.pages), (72., 144., 2));
    }
    #[test]
    fn real_poppler_first_page_persistent_content_cache() {
        let t = temp("actual");
        let input = t.0.join("reference with spaces.PDF");
        fixture(&input, 2, 144., 0);
        let cache = t.0.join("assets");
        let a = render(&input, &cache).unwrap();
        assert_eq!(
            (
                a["page"].as_u64(),
                a["pages"].as_u64(),
                a["width"].as_u64(),
                a["height"].as_u64()
            ),
            (Some(1), Some(2), Some(288), Some(144))
        );
        assert_eq!(a["width_inches"], 2.);
        assert_eq!(a["height_inches"], 1.);
        let first = fs::read(a["image"].as_str().unwrap()).unwrap();
        let b = render(&input, &cache).unwrap();
        assert_eq!(a, b);
        assert_eq!(first, fs::read(b["image"].as_str().unwrap()).unwrap());
        fs::remove_file(&input).unwrap();
        assert!(Path::new(a["image"].as_str().unwrap()).is_file());
        fixture(&input, 1, 72., 90);
        let c = render(&input, &cache).unwrap();
        assert_ne!(a["image"], c["image"]);
        assert_eq!(c["width_inches"], 1.);
        assert_eq!(c["height_inches"], 1.);
    }
    #[test]
    fn actual_huge_and_corrupt_refuse_before_asset_publication() {
        let t = temp("negative");
        let input = t.0.join("bad.pdf");
        let cache = t.0.join("assets");
        fixture(&input, 1, 14401., 0);
        assert!(render(&input, &cache).is_err());
        fs::write(&input, b"%PDF-1.4\nbroken").unwrap();
        assert!(render(&input, &cache).is_err());
        let f = fs::File::create(&input).unwrap();
        f.set_len(MAX_INPUT + 1).unwrap();
        assert!(render(&input, &cache).unwrap_err().contains("64MiB"));
        assert!(fs::read_dir(cache).unwrap().next().is_none());
    }
    #[test]
    fn actual_encrypted_pdf_refuses() {
        let t = temp("encrypted");
        let input = t.0.join("in.pdf");
        let locked = t.0.join("locked.pdf");
        fixture(&input, 1, 144., 0);
        assert!(Command::new("/usr/bin/mutool")
            .args(["clean", "-E", "aes-256", "-O", "owner", "-U", "user"])
            .arg(&input)
            .arg(&locked)
            .status()
            .unwrap()
            .success());
        assert!(render(&locked, &t.0.join("assets")).is_err());
    }
    #[test]
    fn huge_valid_page_is_pixel_bounded_and_physically_sized() {
        let t = temp("pixels");
        let input = t.0.join("wide.pdf");
        fixture(&input, 1, 14400., 0);
        let p = render(&input, &t.0.join("assets")).unwrap();
        assert!(p["width"].as_u64().unwrap() <= 4096);
        assert_eq!(p["width_inches"], 200.);
    }
    #[test]
    fn actual_cropbox_matches_raster_and_physical_size() {
        let t = temp("crop");
        let input = t.0.join("crop.pdf");
        fixture_box(&input, 1, 144., 0, "/CropBox [10 10 110 50] ");
        let p = render(&input, &t.0.join("assets")).unwrap();
        assert_eq!(
            (p["width"].as_u64(), p["height"].as_u64()),
            (Some(200), Some(80))
        );
        assert_eq!(p["width_inches"], 100. / 72.);
        assert_eq!(p["height_inches"], 40. / 72.);
    }
    #[test]
    fn checked_public_fixture_matches_rust_producer() {
        let t = temp("public-fixture");
        let p = t.0.join("two.pdf");
        fixture(&p, 2, 144., 0);
        assert_eq!(
            fs::read(p).unwrap(),
            include_bytes!("../tests/fixtures/two-pages.pdf")
        );
    }
    #[test]
    fn emit_actual_host_fixtures() {
        if let Some(out) = std::env::var_os("NARWHAL_PDF_FIXTURE_OUT") {
            let out = PathBuf::from(out);
            fs::create_dir_all(&out).unwrap();
            fixture(&out.join("one.pdf"), 1, 144., 0);
            fixture(&out.join("two.PDF"), 2, 144., 0);
            fixture(&out.join("rotated.pdf"), 1, 144., 90);
            fixture(&out.join("huge.pdf"), 1, 14401., 0);
        }
    }
}
