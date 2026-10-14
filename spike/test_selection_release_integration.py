#!/usr/bin/env python3
"""Focused public release integration checks for the Rust selection component."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


selection = load("verify_selection_core", ROOT / "scripts/verify_selection_core.py")
assembler = load("assemble_linux_release", ROOT / "scripts/assemble_linux_release.py")
linux_verify = load("verify_linux_release", ROOT / "scripts/verify_linux_release.py")
windows_verify = load("verify_windows_release", ROOT / "scripts/verify_windows_release.py")
public_branch = load("create_public_branch", ROOT / "scripts/create_public_branch.py")

checks = 0


def check(condition: bool, label: str) -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        raise AssertionError(label)


parser_options = {option for action in assembler.build_parser()._actions for option in action.option_strings}
check("--selection-core-artifacts" in parser_options,
      "Linux assembler requires an external verified selection-core directory")
public = set(public_branch.public_files())
for required in (
    "native/selection_core/src/lib.rs",
    "narwhal3d/native_selection.py",
    "scripts/build_selection_core.py",
    "scripts/verify_selection_core.py",
    "spike/test_selection_core_packaging.py",
    "spike/test_selection_release_integration.py",
    "docs/rust-selection-core.md",
):
    check(required in public, f"public allowlist retains Rust release input: {required}")

run_tests = (ROOT / "run_tests.sh").read_text(encoding="utf-8")
check("spike/test_selection_core_packaging.py" in run_tests
      and "spike/test_selection_release_integration.py" in run_tests
      and "spike/test_native_selection.py" in run_tests,
      "full harness builds, verifies, and exercises the Rust adapter")
check(not any(path.startswith("native/selection_core/target/") for path in public),
      "public allowlist excludes every Cargo target artifact")

linux_info = selection.PLATFORMS["linux-x86_64"]
windows_info = selection.PLATFORMS["windows-x86_64"]
linux_source = (ROOT / "native/selection_core/target" / linux_info["target"]
                / "release" / linux_info["filename"])
windows_source = (ROOT / "native/selection_core/target" / windows_info["target"]
                  / "release" / windows_info["filename"])
if not linux_source.is_file() or not windows_source.is_file():
    raise SystemExit("selection-core target artifacts are absent; run packaging acceptance first")

with tempfile.TemporaryDirectory(prefix="selection-release-integration-") as temporary:
    temporary = Path(temporary)
    linux_appdir = temporary / "Linux.AppDir"
    linux_packaged = linux_appdir / "usr/lib/narwhal3d/narwhal3d/native" / linux_info["filename"]
    linux_packaged.parent.mkdir(parents=True)
    shutil.copyfile(linux_source, linux_packaged)
    linux_record = selection.inspect_artifact(linux_packaged, "linux-x86_64")
    linux_manifest = {"selection_core": {
        "manifest_name": selection.MANIFEST_NAME,
        "source_aggregate_sha256": selection.source_aggregate(),
        "rust_toolchain": selection.RUST_TOOLCHAIN,
        "abi": selection.ABI_VERSION,
        "packaged_path": "usr/lib/narwhal3d/narwhal3d/native/libnarwhal_selection_core.so",
        "artifact": linux_record,
    }}
    linux_verify.verify_selection_core_component(linux_appdir, linux_manifest)
    check(True, "Linux release verifier accepts the exact packaged Rust artifact/provenance")
    bad_linux = copy.deepcopy(linux_manifest)
    bad_linux["selection_core"]["source_aggregate_sha256"] = "0" * 64
    try:
        linux_verify.verify_selection_core_component(linux_appdir, bad_linux)
    except linux_verify.VerificationError:
        refused = True
    else:
        refused = False
    check(refused, "Linux release verifier rejects wrong Rust source provenance")

    windows_root = temporary / "Windows"
    windows_packaged = windows_root / "app/narwhal3d/native" / windows_info["filename"]
    windows_packaged.parent.mkdir(parents=True)
    shutil.copyfile(windows_source, windows_packaged)
    windows_record = selection.inspect_artifact(windows_packaged, "windows-x86_64")
    windows_manifest = {"selection_core": {
        "source_aggregate_sha256": selection.source_aggregate(),
        "rust_toolchain": selection.RUST_TOOLCHAIN,
        "abi": selection.ABI_VERSION,
        "packaged_path": "app/narwhal3d/native/narwhal_selection_core.dll",
        "artifact": windows_record,
    }}
    windows_verify.verify_selection_core_component(windows_root, windows_manifest)
    check(True, "Windows release verifier accepts the exact packaged Rust artifact/provenance")
    bad_windows = copy.deepcopy(windows_manifest)
    bad_windows["selection_core"]["artifact"]["sha256"] = "f" * 64
    try:
        windows_verify.verify_selection_core_component(windows_root, bad_windows)
    except windows_verify.VerificationError:
        refused = True
    else:
        refused = False
    check(refused, "Windows release verifier rejects substituted Rust artifact metadata")

powershell = (ROOT / "scripts/build_windows_preview.ps1").read_text(encoding="utf-8")
check("[Parameter(Mandatory=$true)][string]$SelectionCoreArtifacts" in powershell,
      "Windows builder requires prebuilt selection-core artifacts")
check("Selection-core source hash differs" in powershell
      and "narwhal_selection_core.dll" in powershell,
      "Windows builder verifies source binding and copies the DLL")
check("test_rust_selection_core.py" in powershell
      and "windows_packaged_abi_acceptance = $true" in powershell,
      "Windows builder runs and records packaged ABI acceptance")

print(f"\n{checks}/{checks} Rust selection release-integration checks passed")
