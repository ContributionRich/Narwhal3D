#!/usr/bin/env python3
"""Pure Windows launcher and portable-release verifier regression checks."""
from __future__ import annotations

import importlib.util
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

check("CreateProcessW" in launcher and "runtime\\\\blender.exe" in launcher
      and "app\\\\launch\\\\bootstrap.py" in launcher,
      "native launcher starts only the adjacent packaged runtime/bootstrap")
check("CommandLineToArgvW" in launcher and "append_argument" in launcher
      and "for (int i = 1; i < argc; ++i)" in launcher,
      "native launcher preserves quoted Unicode user file arguments")
check("NARWHAL3D_PACKAGE_FORMAT" in launcher and "windows-portable" in launcher
      and "NARWHAL3D_WINDOWS" in launcher,
      "launcher identifies the portable Windows environment")
check("NARWHAL3D_LAUNCHER_TEST" in launcher
      and 'wcscmp(test_mode, L"headless")' in launcher,
      "launcher has an environment-only packaged-bootstrap test route")
check("requestedExecutionLevel level='asInvoker'" in resource
      and "longPathAware" in resource and "dpiAware" in resource,
      "launcher manifest requests no elevation and opts into DPI/long paths")
check("Narwhal 3D early prerelease launcher" in resource
      and "OriginalFilename\", \"Narwhal3D.exe" in resource,
      "launcher version resource carries independent Narwhal identity")
check(icon[:4] == b"\x00\x00\x01\x00" and len(icon) > 100_000,
      "Windows launcher icon is a substantive multi-resolution ICO")
check(verifier.CDR_MULTIVERSION_PROTOCOL in multiversion_source,
      "Windows verifier pins the bundled multi-version CDR helper protocol")

with tempfile.TemporaryDirectory(prefix="narwhal_windows_verifier_test_") as temporary:
    root = Path(temporary)
    (root / "windows-release-manifest.json").write_text("{}", encoding="utf-8")
    base = [{"path": path, "size": 1, "sha256": "0" * 64}
            for path in verifier.REQUIRED if path != "windows-release-manifest.json"]
    verifier.validate_paths(root, base)
    check(all(any(item["path"] == path for item in base) for path in (
        "app/narwhal3d/cdr_reader.py", "app/narwhal3d/cdr_writer.py",
        "app/runtime/windows-x86_64/bin/narwhal_cdr_core.dll",
        "app/runtime/windows-x86_64/bin/narwhal-converter-supervisor.exe",
        "app/runtime/windows-x86_64/bin/narwhal-cdr-multiversion.exe")),
        "Windows release requires public CDR source and native components")
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

check(verifier.normalized(b"a\r\nb\r\n") == b"a\nb\n",
      "public source comparison normalizes Windows checkout line endings only")
check(verifier.FORMAT == "portable-zip" and verifier.PLATFORM == "windows-x86_64",
      "Windows verifier pins portable ZIP and x86-64 identity")

print(f"\n{checks}/{checks} Windows release checks passed")
