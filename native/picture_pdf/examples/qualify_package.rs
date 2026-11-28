//! Actual isolated-package acceptance; requires bwrap, not host Poppler or a Python oracle.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};
fn hash(path: &Path) -> String {
    format!("{:x}", Sha256::digest(fs::read(path).unwrap()))
}
fn run(bin: &Path, fixture: &Path, cache: &Path, developer: bool) -> Value {
    let inside = "/package";
    let mut c = Command::new("bwrap");
    c.args(["--unshare-all", "--die-with-parent", "--dir", "/lib64"]);
    // Admit only the host's glibc/loader family. No host renderer, non-glibc
    // dependency, font directory, config, home or application source is visible.
    for name in [
        "ld-linux-x86-64.so.2",
        "libc.so.6",
        "libm.so.6",
        "libpthread.so.0",
        "libdl.so.2",
        "librt.so.1",
        "libresolv.so.2",
        "libutil.so.1",
    ] {
        let source = [
            PathBuf::from("/usr/lib64").join(name),
            PathBuf::from("/lib/x86_64-linux-gnu").join(name),
            PathBuf::from("/lib64").join(name),
        ]
        .into_iter()
        .find(|p| p.is_file())
        .unwrap_or_else(|| panic!("host glibc component missing: {name}"));
        c.arg("--ro-bind")
            .arg(source.canonicalize().unwrap())
            .arg(format!("/lib64/{name}"));
    }
    c.args([
        "--dir",
        "/usr/bin",
        "--ro-bind",
        "/dev/null",
        "/usr/bin/pdfinfo",
        "--ro-bind",
        "/dev/null",
        "/usr/bin/pdftoppm",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/etc",
    ])
    .arg("--ro-bind")
    .arg(bin)
    .arg(inside)
    .arg("--bind")
    .arg(cache)
    .arg("/cache")
    .arg("--ro-bind")
    .arg(fixture)
    .arg("/input.pdf")
    .args([
        "--clearenv",
        "--setenv",
        "PATH",
        "/untrusted-current-directory",
        "--setenv",
        "LD_LIBRARY_PATH",
        "/package/pdf-libs",
        "--chdir",
        "/tmp",
        "/package/narwhal-converter-supervisor",
        "--timeout-ms",
        "20000",
        "--max-output",
        "65536",
        "--memory-mib",
        "512",
        "--",
        "/package/narwhal-picture-pdf",
    ]);
    if developer {
        c.arg("--developer-system-poppler");
    }
    c.args(["/input.pdf", "/cache"]);
    let command: Vec<_> = std::iter::once(c.get_program())
        .chain(c.get_args())
        .map(|a| a.to_string_lossy().into_owned())
        .collect();
    let o = c.output().unwrap();
    assert!(
        o.status.success(),
        "bwrap/supervisor failed: {}",
        String::from_utf8_lossy(&o.stderr)
    );
    let packet: Value = serde_json::from_slice(&o.stdout).expect("supervisor JSON");
    assert_eq!(packet["timed_out"], false);
    assert!(packet["error"].is_null());
    json!({"command":command,"supervisor":packet,"transport_stderr":String::from_utf8_lossy(&o.stderr)})
}
fn main() {
    let a: Vec<_> = std::env::args_os().skip(1).map(PathBuf::from).collect();
    assert_eq!(
        a.len(),
        4,
        "qualify_package PACKAGE_BIN PUBLIC_PDF NEW_ARTIFACT_DIRECTORY CONTAINMENT_PROBE"
    );
    let bin = a[0].canonicalize().unwrap();
    let fixture = a[1].canonicalize().unwrap();
    assert!(!a[2].exists(), "preserve existing qualification artifacts");
    fs::create_dir_all(&a[2]).unwrap();
    let artifacts = a[2].canonicalize().unwrap();
    let cache = artifacts.join("cache");
    fs::create_dir(&cache).unwrap();
    let before = hash(&bin.join("narwhal-picture-pdf"));
    let green = run(&bin, &fixture, &cache, false);
    fs::write(
        artifacts.join("green.json"),
        serde_json::to_vec_pretty(&green).unwrap(),
    )
    .unwrap();
    assert_eq!(
        green["supervisor"]["returncode"], 0,
        "packaged route failed: {green}"
    );
    let page: Value =
        serde_json::from_str(green["supervisor"]["stdout"].as_str().unwrap()).unwrap();
    assert_eq!(
        (
            page["page"].as_u64(),
            page["pages"].as_u64(),
            page["width"].as_u64(),
            page["height"].as_u64()
        ),
        (Some(1), Some(2), Some(288), Some(144))
    );
    assert_eq!(
        (
            page["width_inches"].as_f64(),
            page["height_inches"].as_f64()
        ),
        (Some(2.0), Some(1.0))
    );
    assert_eq!(page["source_sha256"], hash(&fixture));
    let raster = cache.join(
        Path::new(page["image"].as_str().unwrap())
            .file_name()
            .unwrap(),
    );
    let raster_hash = hash(&raster);
    let red = run(&bin, &fixture, &cache, true);
    fs::write(
        artifacts.join("system-route-RED.json"),
        serde_json::to_vec_pretty(&red).unwrap(),
    )
    .unwrap();
    assert_ne!(
        red["supervisor"]["returncode"], 0,
        "host-route negative control survived"
    );
    assert!(red["supervisor"]["stderr"]
        .as_str()
        .unwrap()
        .contains("/usr/bin/pdfinfo"));
    let restored = run(&bin, &fixture, &cache, false);
    fs::write(
        artifacts.join("restored-green.json"),
        serde_json::to_vec_pretty(&restored).unwrap(),
    )
    .unwrap();
    assert_eq!(restored["supervisor"]["returncode"], 0);
    assert_eq!(
        restored["supervisor"]["stdout"],
        green["supervisor"]["stdout"]
    );
    assert_eq!(hash(&raster), raster_hash);
    assert_eq!(hash(&bin.join("narwhal-picture-pdf")), before);
    // This separate payload is deliberately not distribution-hash qualified. It
    // witnesses the real helper -> renderer inheritance without altering the
    // original package or substituting a stub for the positive Poppler test.
    let witness = artifacts.join("containment-payload");
    assert!(Command::new("cp")
        .args(["-a", "--reflink=auto", "--"])
        .arg(&bin)
        .arg(&witness)
        .status()
        .unwrap()
        .success());
    fs::copy(a[3].canonicalize().unwrap(), witness.join("pdfinfo")).unwrap();
    let inherited = run(&witness, &fixture, &cache, false);
    assert_eq!(
        inherited["supervisor"]["returncode"], 0,
        "renderer inheritance: {inherited}"
    );
    let containment: Value =
        serde_json::from_slice(&fs::read(cache.join("renderer-containment.json")).unwrap())
            .unwrap();
    assert_eq!(containment["ok"], true);
    fs::write(
        artifacts.join("inherited-renderer-containment.json"),
        serde_json::to_vec_pretty(&containment).unwrap(),
    )
    .unwrap();
    let receipt = json!({"ok":true,"helper_sha256":before,"fixture_sha256":hash(&fixture),"raster_sha256":raster_hash,"host_commands_hidden":true,"only_host_glibc_visible":true,"native_supervisor":{"timeout_ms":20000,"memory_mib":512,"max_output":65536},"system_route_red_restored_green":true,"inherited_renderer_containment":containment,"final_appimage":false});
    fs::write(
        artifacts.join("receipt.json"),
        serde_json::to_vec_pretty(&receipt).unwrap(),
    )
    .unwrap();
    println!("{}", receipt);
}
