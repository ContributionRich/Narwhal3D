#!/usr/bin/env python3
"""Cross-platform release inventory contract: mandatory payloads may not drift."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


linux = load("assemble_linux_release")
windows = load("verify_windows_release")
windows_assembler = load("assemble_windows_release")

linux_native = {
    "libnarwhal_cdr_core.so", "libnarwhal_geometry_core.so",
    "libnarwhal_platform_core.so", "libnarwhal_pure_core.so",
    "libnarwhal_selection_core.so", "narwhal-converter-supervisor",
    "narwhal-cdr-multiversion", "narwhal-document-core", "narwhal-welcome",
    "libnarwhal_automation.so", "narwhal-automation", "libnarwhal_application_core.so",
    "libnarwhal_ui_core.so", "narwhal-picture-trace", "narwhal-library-core",
}
windows_native = {
    "narwhal_cdr_core.dll", "narwhal_geometry_core.dll",
    "narwhal_platform_core.dll", "narwhal_pure_core.dll",
    "narwhal_selection_core.dll", "narwhal-converter-supervisor.exe",
    "narwhal-cdr-multiversion.exe", "narwhal-document-core.exe",
    "narwhal-welcome.exe", "narwhal_automation.dll", "narwhal_application_core.dll",
    "narwhal_ui_core.dll", "narwhal-picture-trace.exe", "narwhal-library-core.exe",
}
assert set(linux.NATIVE_COMPONENTS) == linux_native
assert set(windows.NATIVE_COMPONENT_PATHS) == windows_native
assert set(windows_assembler.SOURCE_DIRECTORIES) == {"narwhal3d", "launch", "licenses"}
assert set(windows_assembler.SOURCE_FILES) == {
    "LICENSE", "THIRD_PARTY_NOTICES", "CHANGELOG.md", "STATUS.md",
}
assert windows_assembler.EXCLUDED_COMPONENTS == ["optional-converters", "installer-file-associations"]
assert {
    "Narwhal3D.exe", "Narwhal3D.cmd", "README-FIRST.txt",
    "runtime/blender.exe", "runtime/5.2/python/bin/python.exe",
    "app/launch/bootstrap.py", "app/narwhal3d/__init__.py",
    "app/licenses/GPL-3.0-or-later.txt", "app/licenses/OCP-Apache-2.0.txt",
    "app/licenses/OCCT-exception-1.0.txt", "windows-release-manifest.json",
    "app/licenses/PictureTrace-Rust-Dependencies.txt", "app/licenses/VectorCraft-MIT.txt",
    "app/licenses/VectorCraft-Apache-2.0.txt", "app/licenses/VectorCraft-NOTICE.txt",
} <= windows.REQUIRED
assert {"PictureTrace-Rust-Dependencies.txt", "VectorCraft-MIT.txt", "VectorCraft-Apache-2.0.txt",
        "VectorCraft-NOTICE.txt"} <= set(linux.REQUIRED_LICENSE_FILES)
assert "Library-Rust-Dependencies.txt" in linux.REQUIRED_LICENSE_FILES
assert "app/licenses/Library-Rust-Dependencies.txt" in windows.REQUIRED
assert set(windows.GENERATED_PACKAGE_FILES) == {
    "narwhal3d/native/narwhal_application_core.dll",
    "narwhal3d/native/narwhal_ui_core.dll",
    "narwhal3d/native/narwhal_geometry_core.dll",
    "narwhal3d/native/narwhal_platform_core.dll",
    "narwhal3d/native/narwhal_pure_core.dll",
    "narwhal3d/native/narwhal_selection_core.dll",
}
for source, names in {
    "native_document.py": ("narwhal-document-core",),
    "native_document_session.py": ("narwhal_application_core", "narwhal_session_abi"),
    "native_cdr.py": ("narwhal_cdr_core",),
    "native_geometry.py": ("narwhal_geometry_core",),
    "native_platform.py": ("narwhal_platform_core",),
    "native_pure.py": ("narwhal_pure_core",),
    "native_selection.py": ("narwhal_selection_core",),
    "embedded_cdr.py": ("narwhal-cdr-multiversion",),
    "converter_process.py": ("narwhal-converter-supervisor",),
    "ai_assistant.py": ("libnarwhal_automation.so", "narwhal_automation.dll"),
    "native_library.py": ("narwhal-library-core", "narwhal-library-core.exe", "runtime"),
    "trace_picture.py": ("narwhal-picture-trace", "narwhal-picture-trace.exe", "runtime"),
}.items():
    text = (ROOT / "narwhal3d" / source).read_text(encoding="utf-8")
    assert all(name in text for name in names), (source, names)
launcher = (ROOT / "narwhal3d.sh").read_text(encoding="utf-8")
assert "narwhal-welcome" in launcher and "runtime/${runtime_platform}-${runtime_arch}/bin" in launcher
windows_launcher = (ROOT / "packaging/windows/narwhal3d_launcher.c").read_text(encoding="utf-8")
assert "narwhal-welcome.exe" in windows_launcher and "launch_program = welcome" in windows_launcher
linux_source = (ROOT / "scripts/assemble_linux_release.py").read_text(encoding="utf-8")
linux_verifier = (ROOT / "scripts/verify_linux_release.py").read_text(encoding="utf-8")
windows_verifier = (ROOT / "scripts/verify_windows_release.py").read_text(encoding="utf-8")
assert "optional-converters" in linux_source and "optional-converters" in linux_verifier
assert linux.EXCLUDED_COMPONENTS == ["optional-converters"]
assert "opennurbs-exact-save-helper" not in linux_verifier
assert 'manifest.get("excluded_components") != ["optional-converters"]' in linux_verifier
assert linux.EXACT_SAVE_HELPER == "brep_writer-linux-x86_64"
assert "--exact-save-helper-dir" in linux_source and "def validate_exact_save_helper(" in linux_source
assert "def verify_exact_save_helper(" in linux_verifier
for exclusion in windows_assembler.EXCLUDED_COMPONENTS:
    assert exclusion in windows_verifier
windows_assembler_source = (ROOT / "scripts/assemble_windows_release.py").read_text(encoding="utf-8")
assert "--exact-save-helper-dir" in windows_assembler_source and "--document-observer-dir" in windows_assembler_source
assert 'manifest.get("excluded_components") != ["optional-converters", "installer-file-associations"]' in windows_verifier
assert '"physical_gpu_real_window": False' in (ROOT / "scripts/assemble_windows_release.py").read_text(encoding="utf-8")
assert '"code_signed": False' in (ROOT / "scripts/assemble_windows_release.py").read_text(encoding="utf-8")
print(f"package completeness: Linux {len(linux_native)} native, Windows {len(windows_native)} native, source/launch/licenses/runtime and exclusions complete")
