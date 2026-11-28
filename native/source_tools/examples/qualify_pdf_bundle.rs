//! Real pinned bundle admission and mutation controls; no Python packaging oracle.
use narwhal_source_tools::pdf_bundle;
use serde_json::{json, Value};
use std::{fs, path::PathBuf, process::Command};
fn main() {
    let a: Vec<_> = std::env::args_os().skip(1).map(PathBuf::from).collect();
    assert_eq!(
        a.len(),
        3,
        "qualify_pdf_bundle SOURCE_ROOT BUNDLE NEW_ARTIFACT_DIRECTORY"
    );
    let root = a[0].canonicalize().unwrap();
    let bundle = a[1].canonicalize().unwrap();
    assert!(!a[2].exists());
    fs::create_dir_all(&a[2]).unwrap();
    let artifacts = a[2].canonicalize().unwrap();
    let copy = artifacts.join("mutant-bundle");
    assert!(Command::new("cp")
        .args(["-a", "--reflink=auto", "--"])
        .arg(&bundle)
        .arg(&copy)
        .status()
        .unwrap()
        .success());
    let request = json!({"root":root,"action":"verify","directory":copy});
    let before = pdf_bundle::dispatch(&request).expect("original pinned bundle");
    let mut mutations = Vec::new();
    for path in [
        "pdfinfo",
        "pdf-libs/libpoppler.so.104",
        "pdf-source/poppler-20.11.0-14.el8_10.src.rpm",
        "pdf-licenses/poppler/COPYING",
        "pdf-data/fonts.conf",
    ] {
        let file = copy.join(path);
        let bytes = fs::read(&file).unwrap();
        fs::write(&file, b"deliberately broken PDF package input").unwrap();
        let error =
            pdf_bundle::dispatch(&request).expect_err("mutation must fail source-pinned admission");
        mutations.push(json!({"path":path,"red":true,"error":error}));
        fs::write(file, bytes).unwrap();
        assert_eq!(pdf_bundle::dispatch(&request).unwrap(), before);
    }
    let source = copy.join("pdf-source/poppler-20.11.0-14.el8_10.src.rpm");
    let bytes = fs::read(&source).unwrap();
    fs::remove_file(&source).unwrap();
    let error = pdf_bundle::dispatch(&request).expect_err("missing corresponding source must fail");
    mutations.push(json!({"missing_source_red":true,"error":error}));
    fs::write(source, bytes).unwrap();
    let extra = copy.join("pdf-libs/unreviewed.so");
    fs::write(&extra, b"unreviewed").unwrap();
    let error = pdf_bundle::dispatch(&request).expect_err("unreviewed library must fail");
    mutations.push(json!({"extra_library_red":true,"error":error}));
    fs::remove_file(extra).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let file = copy.join("pdfinfo");
        let bytes = fs::read(&file).unwrap();
        let permissions = fs::metadata(&file).unwrap().permissions();
        fs::remove_file(&file).unwrap();
        symlink(bundle.join("pdfinfo"), &file).unwrap();
        let error = pdf_bundle::dispatch(&request).expect_err("linked renderer must fail");
        mutations.push(json!({"linked_renderer_red":true,"error":error}));
        fs::remove_file(&file).unwrap();
        fs::write(&file, bytes).unwrap();
        fs::set_permissions(&file, fs::Permissions::from_mode(0o777)).unwrap();
        let error = pdf_bundle::dispatch(&request).expect_err("writable renderer must fail");
        mutations.push(json!({"writable_renderer_red":true,"error":error}));
        fs::set_permissions(file, permissions).unwrap();
    }
    let restored = pdf_bundle::dispatch(&request).unwrap();
    assert_eq!(restored, before);
    let destination = artifacts.join("staged");
    fs::create_dir(&destination).unwrap();
    assert_eq!(
        pdf_bundle::dispatch(
            &json!({"root":root,"action":"stage","directory":copy,"destination":destination})
        )
        .unwrap(),
        before
    );
    assert_eq!(
        pdf_bundle::dispatch(&json!({"root":root,"action":"verify","directory":destination}))
            .unwrap(),
        before
    );
    assert!(
        pdf_bundle::dispatch(
            &json!({"root":root,"action":"stage","directory":copy,"destination":destination})
        )
        .is_err(),
        "must not overwrite prior payload"
    );
    let receipt: Value = json!({"ok":true,"before":before,"mutations":mutations,"restored":restored,"stage_verified":true,"overwrite_refused":true});
    fs::write(
        artifacts.join("receipt.json"),
        serde_json::to_vec_pretty(&receipt).unwrap(),
    )
    .unwrap();
    println!("{}", receipt);
}
