//! Host bootstrap transport contract; Rust remains the only probe/audit engine.
use std::{fs, path::PathBuf};
fn root() -> PathBuf {
    std::env::var_os("NARWHAL_SOURCE_TEST_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .parent()
                .unwrap()
                .to_owned()
        })
}
#[test]
fn windows_bootstrap_builds_pinned_native_cli_without_posix_or_python_engine() {
    let adapter = fs::read_to_string(root().join("scripts/source_tools.py")).unwrap();
    for required in [
        "command = [str(LAUNCHER), 'rpc']",
        "if sys.platform == 'win32':",
        "Path.home() / '.cargo/bin/rustup.exe'",
        "'run', '1.95.0'",
        "'cargo', 'run', '--manifest-path'",
        "ROOT / 'native/source_tools/Cargo.toml'",
        "ROOT / 'native/source_tools/target/transport-host'",
        "'--bin', 'narwhal-source-tools', '--release', '--locked', '--offline'",
        "'-j', '2', '--', 'rpc'",
        "result = subprocess.run(command, input=json.dumps(_encode(request)).encode()",
    ] {
        assert!(
            adapter.contains(required),
            "missing source-bound Windows transport: {required}"
        );
    }
}

#[test]
fn windows_builder_builds_driver_before_python_adapter_imports() {
    let builder = fs::read_to_string(root().join("scripts/build_windows_preview.ps1")).unwrap();
    let bootstrap = builder
        .find("\"build source-bound Rust packaging driver\"")
        .unwrap();
    let first_adapter = builder
        .find("from build_linux_native import inventory, digest")
        .unwrap();
    assert!(bootstrap < first_adapter);
    assert!(builder.contains("$SourceToolsTarget = Join-Path $NarwhalSource \"native\\source_tools\\target\\transport-host\""));
    assert!(builder.contains("\"--target-dir\", $SourceToolsTarget)"));
    let adapter = fs::read_to_string(root().join("scripts/source_tools.py")).unwrap();
    assert!(adapter.contains("'--target', 'x86_64-pc-windows-msvc'"));
    assert!(adapter.contains("cwd=ROOT"));
}
