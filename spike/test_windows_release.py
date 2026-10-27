#!/usr/bin/env python3
"""Pure Windows launcher and portable-release verifier regression checks."""
from __future__ import annotations

import importlib.util
import runpy
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "windows_verifier", ROOT / "scripts" / "verify_windows_release.py")
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
assembler_spec = importlib.util.spec_from_file_location(
    "windows_assembler", ROOT / "scripts" / "assemble_windows_release.py")
assert assembler_spec is not None and assembler_spec.loader is not None
assembler = importlib.util.module_from_spec(assembler_spec)
assembler_spec.loader.exec_module(assembler)

checks = 0


def check(condition: bool, label: str) -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        raise AssertionError(label)


launcher = (ROOT / "packaging/windows/narwhal3d_launcher.c").read_text(encoding="utf-8")
resource = (ROOT / "packaging/windows/narwhal3d_launcher.rc.in").read_text(encoding="utf-8")
icon = (ROOT / "packaging/windows/narwhal3d.ico").read_bytes()
multiversion_source = (ROOT / "native/cdr_multiversion/src/main.rs").read_text(encoding="utf-8")
document_core_source = (ROOT / "native/document_core/src/lib.rs").read_text(encoding="utf-8")
build_script = (ROOT / "scripts/build_windows_preview.ps1").read_text(encoding="utf-8")

check("CreateProcessW" in launcher and "runtime\\\\blender.exe" in launcher
      and "app\\\\launch\\\\bootstrap.py" in launcher
      and "narwhal-welcome.exe" in launcher
      and "launch_program = welcome" in launcher
      and 'L"--runtime"' in launcher and 'L"--bootstrap"' in launcher,
      "native launcher routes normal startup through adjacent welcome/runtime/bootstrap")
check("CommandLineToArgvW" in launcher and "append_argument" in launcher
      and "for (int i = 1; i < argc; ++i)" in launcher,
      "native launcher preserves quoted Unicode user file arguments")
check('wcscmp(test_mode, L"headless")' in launcher
      and "launch_program = runtime" in launcher,
      "headless/version acceptance routes remain direct and deterministic")
check("NARWHAL3D_PACKAGE_FORMAT" in launcher and "windows-portable" in launcher
      and "NARWHAL3D_WINDOWS" in launcher
      and '#define NARWHAL3D_VERSION L"development"' in launcher
      and 'SetEnvironmentVariableW(L"NARWHAL3D_VERSION", NARWHAL3D_VERSION)' in launcher
      and 'SetEnvironmentVariableW(L"NARWHAL3D_PACKAGE_PATH", root)' in launcher,
      "launcher identifies the portable Windows environment and exact update target")
check("NARWHAL3D_LAUNCHER_TEST" in launcher
      and 'wcscmp(test_mode, L"headless")' in launcher,
      "launcher has an environment-only packaged-bootstrap test route")
check("requestedExecutionLevel level='asInvoker'" in resource
      and "longPathAware" in resource and "dpiAware" in resource,
      "launcher manifest requests no elevation and opts into DPI/long paths")
check("Narwhal 3D early prerelease launcher" in resource
      and "OriginalFilename\", \"Narwhal3D.exe" in resource
      and resource.count("__VERSION_COMMA__") == 2
      and resource.count("__VERSION__ prerelease") == 2,
      "launcher resource carries independent identity and rendered-version placeholders")
check(icon[:4] == b"\x00\x00\x01\x00" and len(icon) > 100_000,
      "Windows launcher icon is a substantive multi-resolution ICO")
check(verifier.CDR_MULTIVERSION_PROTOCOL in multiversion_source,
      "Windows verifier pins the bundled multi-version CDR helper protocol")
check(verifier.DOCUMENT_CORE_PROTOCOL in document_core_source,
      "Windows verifier pins the bundled Narwhal document-core protocol")
check("[Parameter(Mandatory=$true)]" in build_script
      and "[ValidatePattern('^[0-9a-f]{40}$')]" in build_script
      and "[string]$NarwhalRevision" in build_script
      and '$NarwhalRevision = "' not in build_script
      and "[string]$Version = \"0.4.7\"" in build_script
      and "[ValidatePattern('^0\\.4\\.[0-9]+$')]" in build_script,
      "Windows build requires immutable source and a validated 0.4.x version")
check("native\\converter_supervisor\\Cargo.toml" in build_script
      and '"--memory-mib", "64"' in build_script
      and "narwhal-converter-supervisor.exe" in build_script
      and "run packaged native-document Save/Open round trip" in build_script,
      "Windows build packages a memory-bounded supervisor and proves native Save/Open")
check("b8f597c677811d1f9f4d8a97f5ae2825c0353a42" in build_script
      and "native\\geometry_core\\Cargo.toml" in build_script
      and "build exact geometry core with pinned OCCT and MSVC" in build_script
      and "narwhal_geometry_core.dll" in build_script
      and '"geometry_move": "airtight"' in build_script,
      "Windows build compiles, packages and executes exact Rust geometry against pinned OCCT")

chooser_routes = {
    "Open/Save": ROOT / "narwhal3d/file_commands.py",
    "Import": ROOT / "narwhal3d/import_commands.py",
    "Export": ROOT / "narwhal3d/export_commands.py",
    "Picture": ROOT / "narwhal3d/commands.py",
    "Print command": ROOT / "narwhal3d/print_commands.py",
    "Print dialog": ROOT / "narwhal3d/ui.py",
}
for label, path in chooser_routes.items():
    text = path.read_text(encoding="utf-8")
    check("filedialog.ask_open_path" in text or "filedialog.ask_save_path" in text,
          f"{label} routes its chooser through the centralized native backend")
    check("GetOpenFileName" not in text and "GetSaveFileName" not in text,
          f"{label} does not bypass platform_core with a private Windows chooser")

with tempfile.TemporaryDirectory(prefix="narwhal_windows_verifier_test_") as temporary:
    root = Path(temporary)
    compiled_launcher = root / "Narwhal3D.exe"
    compiled_launcher.write_bytes(
        b"MZ" + "NARWHAL3D_VERSION".encode("utf-16le")
        + "NARWHAL3D_PACKAGE_PATH".encode("utf-16le")
        + "0.4.6".encode("utf-16le"))
    assembler.validate_launcher_version(compiled_launcher, "0.4.6")
    check(True, "Windows assembler accepts only a launcher carrying exact release identity")
    try:
        assembler.validate_launcher_version(compiled_launcher, "0.4.4")
    except assembler.AssemblyError:
        stale_launcher_refused = True
    else:
        stale_launcher_refused = False
    check(stale_launcher_refused, "Windows assembler rejects a stale launcher version")
    (root / "windows-release-manifest.json").write_text("{}", encoding="utf-8")
    base = [{"path": path, "size": 1, "sha256": "0" * 64}
            for path in verifier.REQUIRED if path != "windows-release-manifest.json"]
    verifier.validate_paths(root, base)
    check(verifier.GENERATED_PACKAGE_FILES == {
        "narwhal3d/native/narwhal_geometry_core.dll",
        "narwhal3d/native/narwhal_platform_core.dll",
        "narwhal3d/native/narwhal_pure_core.dll",
        "narwhal3d/native/narwhal_selection_core.dll"},
        "Windows source comparison excludes only manifest-verified native DLLs")
    check(all(any(item["path"] == path for item in base) for path in (
        "app/narwhal3d/cdr_reader.py", "app/narwhal3d/cdr_writer.py",
        "app/runtime/windows-x86_64/bin/narwhal_cdr_core.dll",
        "app/runtime/windows-x86_64/bin/narwhal-converter-supervisor.exe",
        "app/runtime/windows-x86_64/bin/narwhal-cdr-multiversion.exe",
        "app/runtime/windows-x86_64/bin/narwhal-document-core.exe",
        "app/narwhal3d/native/narwhal_geometry_core.dll",
        "app/narwhal3d/native/narwhal_platform_core.dll",
        "app/narwhal3d/native/narwhal_pure_core.dll",
        "app/narwhal3d/native/narwhal_selection_core.dll")),
    "Windows release requires CDR, document, platform and Rust accelerator components")
    for bad in (
        "app/narwhal3d/kernel/bin/helper.exe",
        "runtime/blender.pdb", "app/launch/__pycache__/open_arg.pyc",
        "runtime/makesrna.exe", "bug_" + "reports/private.txt",
    ):
        try:
            verifier.validate_paths(root, base + [{"path": bad, "size": 1, "sha256": "0" * 64}])
        except verifier.VerificationError:
            refused = True
        else:
            refused = False
        check(refused, f"Windows verifier refuses private/cache/debug/build payload: {bad}")

    good_zip = root / "good.zip"
    with zipfile.ZipFile(good_zip, "w") as archive:
        archive.writestr("Package/file.txt", "ok")
    with zipfile.ZipFile(good_zip) as archive:
        verifier.safe_zip_members(archive, "Package")
    check(True, "Windows verifier accepts a confined CRC-valid ZIP")

    bad_zip = root / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w") as archive:
        archive.writestr("../escape.txt", "bad")
    try:
        with zipfile.ZipFile(bad_zip) as archive:
            verifier.safe_zip_members(archive, "Package")
    except verifier.VerificationError:
        traversal_refused = True
    else:
        traversal_refused = False
    check(traversal_refused, "Windows verifier refuses ZIP traversal")

old_inspector = verifier.inspect_pe_dll
try:
    good_dependencies = tuple(sorted((
        "kernel32.dll", "tkbo-a95b458a.dll", "tkbrep-20caede0.dll",
        "tkernel-17e892f6.dll", "tkg3d-bffb89c7.dll", "tkgeomalgo-b6e9f0c2.dll",
        "tkgeombase-8b58f227.dll", "tkmath-0d10d50e.dll", "tkprim-589f0279.dll",
        "tkshhealing-a2154896.dll", "tktopalgo-460f2aa9.dll",
    )))
    verifier.inspect_pe_dll = lambda _path: {
        "machine": "pe32plus-x86-64",
        "exports": ("??_FBRepBuilderAPI_Sewing@@QEAAXXZ",
                    *verifier.GEOMETRY_CORE_EXPORTS),
        "dependencies": good_dependencies,
        "import_symbols": {},
    }
    identity = verifier.verify_geometry_core_identity(Path("geometry.dll"))
    check(identity["dependencies"] == good_dependencies,
          "geometry verifier requires exact public ABI, bounded MSVC thunks and hashed OCCT imports")
    verifier.inspect_pe_dll = lambda _path: {
        "machine": "pe32plus-x86-64",
        "exports": (*verifier.GEOMETRY_CORE_EXPORTS, "unexpected_export"),
        "dependencies": good_dependencies,
        "import_symbols": {},
    }
    try:
        verifier.verify_geometry_core_identity(Path("geometry.dll"))
    except verifier.VerificationError:
        extra_refused = True
    else:
        extra_refused = False
    check(extra_refused, "geometry verifier refuses arbitrary non-ABI exports")
    verifier.inspect_pe_dll = lambda _path: {
        "machine": "pe32plus-x86-64",
        "exports": verifier.GEOMETRY_CORE_EXPORTS,
        "dependencies": ("TKBin.dll",),
        "import_symbols": {},
    }
    try:
        verifier.verify_geometry_core_identity(Path("geometry.dll"))
    except verifier.VerificationError:
        unpinned_refused = True
    else:
        unpinned_refused = False
    check(unpinned_refused, "geometry verifier refuses unpinned or incomplete OCCT imports")
finally:
    verifier.inspect_pe_dll = old_inspector

check(verifier.normalized(b"a\r\nb\r\n") == b"a\nb\n",
      "public source comparison normalizes Windows checkout line endings only")
check(verifier.FORMAT == "portable-zip" and verifier.PLATFORM == "windows-x86_64",
      "Windows verifier pins portable ZIP and x86-64 identity")

runpy.run_path(str(ROOT / "spike/test_windows_build.py"))["run_checks"](check)

print(f"\n{checks}/{checks} Windows release checks passed")
