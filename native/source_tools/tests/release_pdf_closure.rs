use narwhal_source_tools::{audit_entries, config, public_files, strings, Entries};
use serde_json::{json, Value};
use std::{collections::BTreeSet, path::Path};
const REQUIRED: &[&str] = &[
    "licenses/PDF-Poppler-NOTICE.txt",
    "native/picture_pdf/.cargo/config.toml",
    "native/picture_pdf/examples/containment_probe.rs",
    "native/picture_pdf/examples/qualify_package.rs",
    "native/source_tools/examples/qualify_pdf_bundle.rs",
    "native/source_tools/src/pdf_bundle.rs",
    "native/source_tools/tests/launcher_cache.rs",
    "native/source_tools/tests/release_pdf_closure.rs",
    "native/source_tools/tests/windows_preclose_release.rs",
    "native/source_tools/tests/windows_transport.rs",
    "packaging/linux-pdf.lock.json",
    "packaging/pdf-fonts.conf",
];
fn qualifies(root: &Path, c: &Value) -> Result<(), String> {
    let selected: BTreeSet<_> = public_files(root, c)?.into_iter().collect();
    let required: BTreeSet<_> = strings(&c["REQUIRED"]).into_iter().collect();
    for path in REQUIRED {
        if !selected.contains(*path) || !required.contains(*path) {
            return Err(format!(
                "release prerequisite not selected and required: {path}"
            ));
        }
    }
    Ok(())
}
#[test]
fn pdf_runtime_and_native_release_sources_cannot_be_dropped() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap();
    let c = config();
    qualifies(root, &c).unwrap();
    let entries: Entries = strings(&c["REQUIRED"])
        .into_iter()
        .map(|p| (p, ("100644".into(), b"pass\n".to_vec())))
        .collect();
    for path in REQUIRED {
        let mut missing = entries.clone();
        missing.remove(*path);
        assert!(
            audit_entries(root, &missing, true, &c)
                .contains(&format!("missing required public file: {path}")),
            "{path}"
        );
        let mut weakened = c.clone();
        weakened["REQUIRED"] = json!(strings(&c["REQUIRED"])
            .into_iter()
            .filter(|p| p != path)
            .collect::<Vec<_>>());
        assert!(
            qualifies(root, &weakened).is_err(),
            "omission mutation survived: {path}"
        );
    }
}
