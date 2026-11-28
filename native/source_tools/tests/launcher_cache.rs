//! Regression for stale Cargo fingerprints and executable replacement across roots.
use std::{fs, path::PathBuf};
#[test]
fn launcher_uses_immutable_source_key_and_post_build_admission() {
    let root = std::env::var_os("NARWHAL_SOURCE_TEST_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .parent()
                .unwrap()
                .to_owned()
        });
    let script = fs::read_to_string(root.join("scripts/source_tools.sh")).unwrap();
    for token in [
        "KEY=${PIN%% *}",
        "TARGET=\"$CACHE/targets/$KEY\"",
        "--target-dir \"$TARGET\"",
        "if [[ \"$(source_pin)\" != \"$PIN\" ]]",
        "exec \"$TARGET/release/narwhal-source-tools\" \"$@\"",
    ] {
        assert!(
            script.contains(token),
            "source-bound cache guard missing: {token}"
        );
    }
    assert!(!script.contains("exec \"$CACHE/target/release/narwhal-source-tools\""));
}
