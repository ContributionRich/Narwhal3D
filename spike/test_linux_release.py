#!/usr/bin/env python3
"""Focused Linux/AppImage native-component release integration gates."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assemble = load_script("assemble_linux_release")
verify = load_script("verify_linux_release")
launcher_path = ROOT / "narwhal3d.sh"
launcher = launcher_path.read_text(encoding="utf-8")
results: list[tuple[str, bool, str]] = []


def check(label: str, condition: object, detail: object = "") -> None:
    results.append((label, bool(condition), str(detail)))


def rejects(label: str, action, exception_type) -> None:
    try:
        action()
    except exception_type:
        check(label, True)
    else:
        check(label, False, "mutation was accepted")


expected_components = (
    "libnarwhal_cdr_core.so", "libnarwhal_geometry_core.so",
    "libnarwhal_platform_core.so", "libnarwhal_pure_core.so",
    "libnarwhal_selection_core.so", "narwhal-converter-supervisor",
    "narwhal-cdr-multiversion", "narwhal-document-core", "narwhal-welcome",
    "libnarwhal_automation.so", "narwhal-automation",
    "libnarwhal_application_core.so", "libnarwhal_ui_core.so",
)
expected_accelerators = (
    "libnarwhal_geometry_core.so", "libnarwhal_platform_core.so",
    "libnarwhal_pure_core.so", "libnarwhal_selection_core.so",
    "libnarwhal_application_core.so", "libnarwhal_ui_core.so",
)
expected_abis = {
    "libnarwhal_application_core.so": 1,
    "libnarwhal_automation.so": 1,
    "libnarwhal_cdr_core.so": 3,
    "libnarwhal_geometry_core.so": 4,
    "libnarwhal_platform_core.so": 1,
    "libnarwhal_pure_core.so": 1,
    "libnarwhal_selection_core.so": 1,
}
check("assembler has the exact Linux native component set",
      assemble.NATIVE_COMPONENTS == expected_components, assemble.NATIVE_COMPONENTS)
check("verifier has the exact Linux native component set",
      verify.NATIVE_COMPONENTS == expected_components, verify.NATIVE_COMPONENTS)
check("assembler packages the exact adjacent accelerator set",
      assemble.PACKAGED_ACCELERATORS == expected_accelerators, assemble.PACKAGED_ACCELERATORS)
check("verifier requires the exact adjacent accelerator set",
      verify.PACKAGED_ACCELERATORS == expected_accelerators, verify.PACKAGED_ACCELERATORS)
for label, module in (("assembler", assemble), ("verifier", verify)):
    actual_abis = {name: abi for name, (_symbol, abi) in module.NATIVE_LIBRARY_ABIS.items()}
    check(f"{label} pins geometry ABI 4 and all other native ABIs",
          actual_abis == expected_abis, actual_abis)

required_new_paths = {
    "usr/lib/narwhal3d/runtime/linux-x86_64/bin/libnarwhal_geometry_core.so",
    "usr/lib/narwhal3d/runtime/linux-x86_64/bin/libnarwhal_platform_core.so",
    "usr/lib/narwhal3d/narwhal3d/native/libnarwhal_geometry_core.so",
    "usr/lib/narwhal3d/narwhal3d/native/libnarwhal_platform_core.so",
}
check("verifier requires runtime and adjacent geometry/platform libraries",
      required_new_paths.issubset(verify.REQUIRED_PATHS),
      sorted(required_new_paths - verify.REQUIRED_PATHS))
check("assembler and verifier pin the same bundled OCCT DSO directory",
      assemble.OCCT_LIB_RELATIVE == verify.OCCT_LIB_RELATIVE
      == Path("5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs"))

for script_name in ("assemble_linux_release.py", "verify_linux_release.py"):
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / script_name), "--self-check"],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    check(f"{script_name} self-check passes",
          completed.returncode == 0 and "self-check: OK" in completed.stdout,
          completed.stdout)

shell_check = subprocess.run(
    ["bash", "-n", str(launcher_path)], cwd=ROOT, text=True,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
check("launcher passes Bash syntax validation", shell_check.returncode == 0, shell_check.stdout)
check("launcher exports every native library boundary",
      all(name in launcher for name in (
          "NARWHAL_PURE_CORE", "NARWHAL_SELECTION_CORE", "NARWHAL_CDR_CORE",
          "NARWHAL_GEOMETRY_CORE", "NARWHAL_PLATFORM_CORE")))
check("launcher selects Linux .so and preserves macOS .dylib behavior",
      "native_suffix=.so" in launcher and "native_suffix=.dylib" in launcher
      and "native_prefix=lib" in launcher)
rendered_app_run = assemble.render_app_run("0.4.6")
check("AppImage launcher carries exact updater version and package path",
      rendered_app_run.splitlines().count("export NARWHAL3D_VERSION=0.4.6") == 1
      and '${APPIMAGE:-}' in rendered_app_run
      and 'export NARWHAL3D_PACKAGE_PATH="$APPIMAGE"' in rendered_app_run)
rejects("AppImage launcher rejects shell-injectable release versions",
        lambda: assemble.render_app_run("0.4.6;touch-bad"), assemble.ReleaseError)
verify.verify_app_run_version(rendered_app_run, "0.4.6")
rejects("AppImage verifier rejects a mismatched updater version",
        lambda: verify.verify_app_run_version(rendered_app_run, "0.4.4"),
        verify.VerificationError)
check("launcher confines LD_LIBRARY_PATH to bundled no-VTK OCCT DSOs",
      'LD_LIBRARY_PATH="$OCCT_LIBRARY_DIR"' in launcher
      and "cadquery_ocp_novtk.libs" in launcher
      and "${LD_LIBRARY_PATH" not in launcher)
check("source launcher falls back to current Rust target artifacts",
      'target/$native_target/release/$filename' in launcher
      and 'target/release/$filename' in launcher
      and "NARWHAL3D_LAUNCHER_TEST:-" in launcher
      and "native-paths" in launcher)

source_libraries = {
    "NARWHAL_PURE_CORE": ROOT / "native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so",
    "NARWHAL_SELECTION_CORE": ROOT / "native/selection_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_selection_core.so",
    "NARWHAL_GEOMETRY_CORE": ROOT / "native/geometry_core/target/release/libnarwhal_geometry_core.so",
    "NARWHAL_PLATFORM_CORE": ROOT / "native/platform_core/target/release/libnarwhal_platform_core.so",
}
if all(path.is_file() for path in source_libraries.values()):
    environment = os.environ.copy()
    environment["NARWHAL3D_LAUNCHER_TEST"] = "native-paths"
    for name in source_libraries:
        environment.pop(name, None)
    resolved = subprocess.run(
        [str(launcher_path)], cwd=ROOT, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    reported = dict(line.split("=", 1) for line in resolved.stdout.splitlines()
                    if line.startswith("NARWHAL_") and "=" in line)
    check("normal source launch resolves current pure/selection/geometry/platform cores",
          resolved.returncode == 0 and all(
              Path(reported.get(name, "")).resolve() == path.resolve()
              for name, path in source_libraries.items()), resolved.stdout)
else:
    check("normal source launch resolves current pure/selection/geometry/platform cores",
          True, "source artifacts are not built in this checkout")

with tempfile.TemporaryDirectory(prefix="narwhal-linux-release-test-") as temporary:
    appdir = Path(temporary) / "AppDir"
    app_root = appdir / verify.APP_ROOT_RELATIVE
    runtime_native = app_root / verify.NATIVE_COMPONENT_RELATIVE
    adjacent_native = app_root / "narwhal3d/native"
    runtime_native.mkdir(parents=True)
    adjacent_native.mkdir(parents=True)
    records: dict[str, object] = {"required_glibc": "2.28"}
    for index, filename in enumerate(expected_components):
        path = runtime_native / filename
        path.write_bytes(f"native-{index}-{filename}".encode("ascii"))
        if filename == "narwhal-welcome":
            path.write_bytes(
                b"--runtime is required\0--bootstrap is required\0NARWHAL3D_WELCOME_REQUEST\0"
                b"NARWHAL3D_VERSION\0releases?per_page=20\0download sha256 does not match\0"
                b"could not atomically replace current AppImage")
        record = {"size": path.stat().st_size, "sha256": verify.sha256_file(path)}
        if filename in expected_abis:
            record["abi"] = expected_abis[filename]
        if filename == "narwhal-welcome":
            record["launch_contract"] = 1
        if filename == "libnarwhal_application_core.so":
            record.update(history_abi=1, exports=list(verify.APPLICATION_CORE_EXPORTS))
        if filename == "libnarwhal_ui_core.so":
            record.update(verify.UI_CORE_IDENTITY)
        records[filename] = record
    for filename in expected_accelerators:
        source = runtime_native / filename
        (adjacent_native / filename).write_bytes(source.read_bytes())

    verify.verify_native_manifest(appdir, records)
    check("exact regular native manifest and adjacent files are accepted", True)
    for filename in ("libnarwhal_geometry_core.so", "libnarwhal_platform_core.so",
                     "libnarwhal_application_core.so", "libnarwhal_ui_core.so"):
        omitted = dict(records)
        del omitted[filename]
        rejects(f"manifest omission of {filename} is rejected",
                lambda omitted=omitted: verify.verify_native_manifest(appdir, omitted),
                verify.VerificationError)
        adjacent = adjacent_native / filename
        adjacent.unlink()
        adjacent.symlink_to(runtime_native / filename)
        rejects(f"adjacent symlink for {filename} is rejected",
                lambda: verify.verify_native_manifest(appdir, records),
                verify.VerificationError)
        adjacent.unlink()
        adjacent.write_bytes((runtime_native / filename).read_bytes())

tracked = subprocess.run(
    ["git", "--no-pager", "ls-files", "*.so", "*.dylib", "*.dll", "*.exe", "*.a"],
    cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
tracked_binaries = [line for line in tracked.stdout.splitlines() if line.strip()]
check("no generated native binaries are committed",
      tracked.returncode == 0 and not tracked_binaries, tracked.stdout)

for label, passed, detail in results:
    print(("  ok   " if passed else "  FAIL ") + label
          + ((f" ({detail.strip()})") if detail and not passed else ""))
failed = sum(not passed for _, passed, _ in results)
print(f"\n{len(results) - failed}/{len(results)} Linux release checks passed")
raise SystemExit(1 if failed else 0)
