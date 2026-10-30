#!/usr/bin/env python3
"""Assemble a reproducible Narwhal 3D Linux x86_64 AppImage."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any

from release_policy import (
    MAX_HOST_GLIBC, format_glibc, glibc_versions, scan_runtime_glibc,
    validate_preview_version,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK_RELATIVE = Path("packaging/linux-x86_64.lock.json")
APP_ROOT_RELATIVE = Path("usr/lib/narwhal3d")
RUNTIME_RELATIVE = Path("tools/blender-patched")
MANIFEST_RELATIVE = APP_ROOT_RELATIVE / "packaging/release-manifest.json"
NATIVE_RUNTIME_MARKER = ".narwhal3d-native-runtime.json"
OFFICIAL_RUNTIME_MARKERS = (".narwhal3d-runtime.json", ".narwhal3d-artifact.json")
PRODUCT = "Narwhal 3D"
PLATFORM = "linux-x86_64"
PACKAGE_FORMAT = "appimage"
RUNTIME_FLAVOR = "native-narwhal-runtime"
FORBIDDEN_PARTS = {"vendor", "More_Data", "ExamplePhotos", ".git", "bug_reports", "artifacts"}
EXCLUDED_PACKAGE_FILES = {PurePosixPath("kernel/brep_writer.cpp")}
EXCLUDED_PACKAGE_TREE = PurePosixPath("kernel/bin")
NATIVE_COMPONENT_RELATIVE = Path("runtime/linux-x86_64/bin")
OCCT_LIB_RELATIVE = Path("5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs")
CDR_MULTIVERSION_PROTOCOL = "narwhal-cdr-multiversion 1 tracedraw 821ba154 read 16-18 write 17"
DOCUMENT_CORE_PROTOCOL = "narwhal-document-core 1"
NATIVE_COMPONENTS = (
    "libnarwhal_cdr_core.so", "libnarwhal_geometry_core.so",
    "libnarwhal_platform_core.so", "libnarwhal_pure_core.so",
    "libnarwhal_selection_core.so", "narwhal-converter-supervisor",
    "narwhal-cdr-multiversion", "narwhal-document-core", "narwhal-welcome",
    "libnarwhal_automation.so", "narwhal-automation",
)
PACKAGED_ACCELERATORS = (
    "libnarwhal_geometry_core.so", "libnarwhal_platform_core.so",
    "libnarwhal_pure_core.so", "libnarwhal_selection_core.so",
)
NATIVE_LIBRARY_ABIS = {
    "libnarwhal_automation.so": ("narwhal_workflow_abi", 1),
    "libnarwhal_cdr_core.so": ("narwhal_cdr_abi_version", 3),
    "libnarwhal_geometry_core.so": ("narwhal_geometry_core_abi_version", 4),
    "libnarwhal_platform_core.so": ("narwhal_platform_core_abi_version", 1),
    "libnarwhal_pure_core.so": ("narwhal_pure_core_abi_version", 1),
    "libnarwhal_selection_core.so": ("narwhal_selection_core_abi_version", 1),
}
LAUNCH_FILES = (
    "bootstrap.py",
    "open_arg.py",
    "narwhal3d.desktop.in",
    "narwhal3d-3dm.xml",
    "narwhal3d-project.xml",
    "narwhal3d.png",
)
PACKAGING_FILES = ("linux-x86_64.lock.json", "README.md", "NOTICES.md")
REQUIRED_LICENSE_FILES = (
    "GPL-3.0-or-later.txt",
    "rhino3dm-MIT.txt",
    "OCP-Apache-2.0.txt",
    "LGPL-2.1-or-later.txt",
    "OCCT-exception-1.0.txt",
    "AppImage-MIT.txt",
    "TraceDraw-MIT.txt",
    "TraceDraw-Rust-Dependencies.txt",
)
DISTRIBUTIONS = {
    "rhino3dm": "8.35.0",
    "cadquery-ocp-novtk": "8.0.1.0.0",
    "cadquery-ocp-proxy": "8.0.1.0.0",
}
REQUIRED_ARTIFACT_IDS = {"appimagetool", "appimage-runtime", "blender-runtime", *DISTRIBUTIONS}
APPIMAGETOOL_SHA256 = "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
APPIMAGE_RUNTIME_VERSION = "20251108"
APPIMAGE_RUNTIME_SHA256 = "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"
BLENDER_SOURCE_COMMIT = "d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
RUNTIME_BUILD_EPOCH_KEY = "build_source_date_epoch"
PATCH_INPUTS = (
    "blender_patches/apply_narwhal3d_branding.py",
    "blender_patches/apply_app_drawn.py",
    "blender_patches/apply_cad_boundary_wires.py",
    "blender_patches/apply_deferred_first_present.py",
    "blender_patches/apply_statusbar_height.py",
    "blender_patches/narwhal3d_app_icon.svg",
    "blender_patches/narwhal3d_window_icons.json",
    "blender_patches/apply_window_overlay.py",
)
VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*\Z")
REVISION_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
JsonObject = dict[str, Any]

APP_RUN_VERSION_TOKEN = "__NARWHAL3D_APPIMAGE_VERSION__"
APP_RUN = """#!/bin/sh
set -eu
APPDIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export APPDIR
export NARWHAL3D_APPIMAGE=1
export NARWHAL3D_PACKAGE_FORMAT=appimage
export NARWHAL3D_VERSION=__NARWHAL3D_APPIMAGE_VERSION__
if [ -n "${APPIMAGE:-}" ]; then
    export NARWHAL3D_PACKAGE_PATH="$APPIMAGE"
fi
exec "$APPDIR/usr/lib/narwhal3d/narwhal3d.sh" "$@"
"""

DESKTOP_VERSION_TOKEN = "__NARWHAL3D_APPIMAGE_VERSION__"
DESKTOP_ENTRY_TEMPLATE = """[Desktop Entry]
Type=Application
Version=1.0
X-AppImage-Version=__NARWHAL3D_APPIMAGE_VERSION__
Name=Narwhal 3D
GenericName=NURBS CAD
Comment=Precision NURBS computer-aided design
Exec=AppRun %F
Icon=narwhal3d
Terminal=false
Categories=Engineering;Graphics;X-CAD;
Keywords=cad;nurbs;3d;modeling;engineering;design;
StartupNotify=true
StartupWMClass=Narwhal 3D
MimeType=application/x-3dm;
"""


class ReleaseError(RuntimeError):
    """A required AppImage input is absent, inconsistent, or unsafe."""


def render_app_run(version: str) -> str:
    """Render the immutable product version consumed by the native updater."""
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError("unsafe AppImage launcher version")
    rendered = APP_RUN.replace(APP_RUN_VERSION_TOKEN, version)
    expected_line = f"export NARWHAL3D_VERSION={version}"
    if APP_RUN_VERSION_TOKEN in rendered or rendered.splitlines().count(expected_line) != 1:
        raise ReleaseError("AppRun template must render exactly one matching product-version line")
    return rendered


def render_desktop_entry(version: str) -> str:
    """Render one immutable desktop version line before inventory hashing."""
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError("unsafe AppImage desktop version")
    rendered = DESKTOP_ENTRY_TEMPLATE.replace(DESKTOP_VERSION_TOKEN, version)
    expected_line = f"X-AppImage-Version={version}"
    if DESKTOP_VERSION_TOKEN in rendered or rendered.splitlines().count(expected_line) != 1:
        raise ReleaseError("desktop template must render exactly one matching X-AppImage-Version line")
    return rendered


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, description: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"{description} must contain a JSON object: {path}")
    return value


def validate_optional_epoch(value: object, description: str) -> int | None:
    """Return an optional nonnegative epoch while rejecting booleans and strings."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReleaseError(f"{description} must be null or a nonnegative integer")
    return value


def require_regular_file(path: Path, description: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ReleaseError(f"missing regular {description}: {path}")


def is_excluded_package_payload(relative: PurePosixPath) -> bool:
    """Return whether a package-relative path is forbidden from public releases."""
    return relative in EXCLUDED_PACKAGE_FILES or (
        relative != EXCLUDED_PACKAGE_TREE and relative.is_relative_to(EXCLUDED_PACKAGE_TREE)
    )


def validate_source_tree(root: Path, description: str, *, reject_symlinks: bool = False) -> None:
    """Reject unsafe entries and broken, external, or disallowed symlinks."""
    if not root.is_dir() or root.is_symlink():
        raise ReleaseError(f"missing source directory for {description}: {root}")
    resolved_root = root.resolve()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if FORBIDDEN_PARTS.intersection(relative.parts):
            raise ReleaseError(f"forbidden path in {description}: {relative}")
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            if reject_symlinks:
                raise ReleaseError(f"symlink is not allowed in {description}: {relative}")
            try:
                target = path.resolve(strict=True)
            except OSError as exc:
                raise ReleaseError(f"broken symlink in {description}: {relative}") from exc
            if not target.is_relative_to(resolved_root):
                raise ReleaseError(f"symlink escapes {description}: {relative} -> {os.readlink(path)}")
        elif not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ReleaseError(f"unsafe special entry in {description}: {relative}")


def validate_release_source_package(root: Path) -> None:
    """Require a sanitized public package; never silently omit private helpers."""
    validate_source_tree(root, "narwhal3d package")
    for path in root.rglob("*"):
        relative = PurePosixPath(path.relative_to(root).as_posix())
        if is_excluded_package_payload(relative):
            raise ReleaseError(f"excluded private payload is present in source package: narwhal3d/{relative}")
        if relative == EXCLUDED_PACKAGE_TREE and path.is_symlink():
            raise ReleaseError("excluded kernel/bin path must not be a symlink")


def locked_artifacts(lock: JsonObject) -> dict[str, JsonObject]:
    artifacts = lock.get("artifacts")
    if not isinstance(artifacts, list):
        raise ReleaseError("artifact lock has no artifacts list")
    result: dict[str, JsonObject] = {}
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ReleaseError("artifact lock contains an invalid artifact entry")
        if item["id"] in result:
            raise ReleaseError(f"artifact lock contains duplicate id: {item['id']}")
        result[item["id"]] = item
    if set(result) != REQUIRED_ARTIFACT_IDS:
        raise ReleaseError("artifact lock does not contain exactly the required release artifacts")
    return result


def expected_patch_provenance(source_root: Path) -> tuple[list[JsonObject], str]:
    """Hash the exact native patch scripts/assets used for a publishable runtime."""
    records: list[JsonObject] = []
    aggregate = hashlib.sha256()
    for name in sorted(PATCH_INPUTS):
        path = source_root / name
        require_regular_file(path, f"native patch input {name}")
        digest = sha256_file(path)
        records.append({"path": name, "sha256": digest})
        aggregate.update(name.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n")
    return records, aggregate.hexdigest()


def binary_contains(path: Path, marker: bytes) -> bool:
    """Search a large binary for an exact branding marker without loading it whole."""
    overlap = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            data = overlap + chunk
            if marker in data:
                return True
            overlap = data[-max(0, len(marker) - 1):]
    return False


def validate_lock_and_runtime(
    lock_path: Path,
    runtime_dir: Path,
    source_root: Path,
) -> tuple[JsonObject, dict[str, JsonObject], str, JsonObject, tuple[int, int]]:
    """Require exact native runtime provenance; the locked official runtime is dev-only."""
    require_regular_file(lock_path, "artifact lock")
    raw_lock = lock_path.read_bytes()
    lock_sha = hashlib.sha256(raw_lock).hexdigest()
    lock = read_json(lock_path, "artifact lock")
    if lock.get("schema") != 1 or lock.get("product") != PRODUCT or lock.get("platform") != PLATFORM:
        raise ReleaseError("artifact lock identity is not the supported Narwhal 3D Linux lock")
    development_runtime = lock.get("runtime")
    if not isinstance(development_runtime, dict) or development_runtime != {
        "purpose": "development-bootstrap-only",
        "flavor": "official-blender-runtime",
        "blender_version": "5.2.2",
        "bundle_path": "tools/blender-5.2.2-linux-x64",
    }:
        raise ReleaseError("locked official runtime must remain explicitly development-only")
    artifacts = locked_artifacts(lock)

    validate_source_tree(runtime_dir, "native Narwhal runtime")
    try:
        runtime_glibc, _glibc_path = scan_runtime_glibc(runtime_dir)
    except RuntimeError as exc:
        raise ReleaseError(str(exc)) from exc
    for official_marker in OFFICIAL_RUNTIME_MARKERS:
        if (runtime_dir / official_marker).exists() or (runtime_dir / official_marker).is_symlink():
            raise ReleaseError(f"official development runtime marker is forbidden in release runtime: {official_marker}")
    binary = runtime_dir / "blender"
    python = runtime_dir / "5.2/python/bin/python3.13"
    require_regular_file(binary, "native Narwhal executable")
    require_regular_file(python, "native bundled CPython 3.13")
    if not os.access(binary, os.X_OK) or not os.access(python, os.X_OK):
        raise ReleaseError("native Narwhal executable and bundled CPython must be executable")

    marker = read_json(runtime_dir / NATIVE_RUNTIME_MARKER, "native runtime provenance marker")
    if "source_date_epoch" in marker:
        raise ReleaseError("native runtime marker uses deprecated ambiguous source_date_epoch")
    if RUNTIME_BUILD_EPOCH_KEY not in marker:
        raise ReleaseError("native runtime marker lacks build_source_date_epoch")
    validate_optional_epoch(marker.get(RUNTIME_BUILD_EPOCH_KEY), "native build_source_date_epoch")
    build_timestamp = marker.get("build_timestamp_utc")
    if not isinstance(build_timestamp, str) or not build_timestamp.endswith("Z"):
        raise ReleaseError("native runtime marker has an invalid build_timestamp_utc")
    expected_inputs, expected_aggregate = expected_patch_provenance(source_root)
    expected_marker = {
        "schema": 1,
        "product": PRODUCT,
        "platform": PLATFORM,
        "runtime_flavor": RUNTIME_FLAVOR,
        "blender_version": "5.2.2",
        "blender_source_commit": BLENDER_SOURCE_COMMIT,
        "patch_inputs": expected_inputs,
        "patch_aggregate_sha256": expected_aggregate,
    }
    for key, expected in expected_marker.items():
        if marker.get(key) != expected:
            raise ReleaseError(f"native runtime marker field {key!r} does not match release inputs")
    if marker.get("binary_sha256") != sha256_file(binary):
        raise ReleaseError("native runtime binary hash does not match its provenance marker")
    try:
        completed = subprocess.run(
            [str(binary), "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(f"cannot execute native Narwhal runtime: {exc}") from exc
    first_line = completed.stdout.splitlines()[0] if completed.stdout.splitlines() else ""
    if not first_line.startswith("Narwhal 3D 5.2.2"):
        raise ReleaseError(f"native runtime version banner is not Narwhal 3D 5.2.2: {first_line!r}")
    if marker.get("version_first_line") != first_line:
        raise ReleaseError("native runtime version banner differs from provenance marker")
    if not binary_contains(binary, b" - Narwhal 3D"):
        raise ReleaseError("native runtime binary lacks the Narwhal title marker")
    if not binary_contains(binary, b"Narwhal 3D quit"):
        raise ReleaseError("native runtime binary lacks the Narwhal quit marker")
    dependency_probe = r'''
import importlib.metadata
import json
import sys
import rhino3dm
import OCP
print(json.dumps({
    "python": list(sys.version_info[:2]),
    "rhino3dm": importlib.metadata.version("rhino3dm"),
    "cadquery-ocp-novtk": importlib.metadata.version("cadquery-ocp-novtk"),
    "cadquery-ocp-proxy": importlib.metadata.version("cadquery-ocp-proxy"),
}, sort_keys=True))
'''
    try:
        completed = subprocess.run(
            [str(python), "-I", "-c", dependency_probe],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "")
        raise ReleaseError(f"native runtime dependency import probe failed: {detail or exc}") from exc
    try:
        dependency_result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ReleaseError("native runtime dependency probe returned invalid output") from exc
    expected_dependencies = {"python": [3, 13], **DISTRIBUTIONS}
    if dependency_result != expected_dependencies:
        raise ReleaseError(
            f"native runtime dependency mismatch: expected {expected_dependencies}, got {dependency_result}"
        )
    return lock, artifacts, lock_sha, marker, runtime_glibc


def validate_appimagetool(path: Path, artifacts: dict[str, JsonObject]) -> None:
    """Require the provided appimagetool to be the exact locked executable."""
    require_regular_file(path, "appimagetool")
    artifact = artifacts["appimagetool"]
    if (
        artifact.get("version") != "1.9.1"
        or artifact.get("filename") != "appimagetool-x86_64.AppImage"
        or artifact.get("sha256") != APPIMAGETOOL_SHA256
        or artifact.get("executable") is not True
    ):
        raise ReleaseError("appimagetool lock entry does not match the supported 1.9.1 artifact")
    actual = sha256_file(path)
    if actual != APPIMAGETOOL_SHA256:
        raise ReleaseError(f"appimagetool SHA-256 mismatch: expected {APPIMAGETOOL_SHA256}, got {actual}")
    if not os.access(path, os.X_OK):
        raise ReleaseError(f"locked appimagetool is not executable: {path}")


def validate_appimage_runtime(path: Path, artifacts: dict[str, JsonObject]) -> None:
    """Require the exact locked type2 runtime used to make the AppImage."""
    require_regular_file(path, "AppImage type2 runtime")
    artifact = artifacts["appimage-runtime"]
    if (
        artifact.get("kind") != "appimage-runtime"
        or artifact.get("version") != APPIMAGE_RUNTIME_VERSION
        or artifact.get("filename") != "runtime-x86_64"
        or artifact.get("sha256") != APPIMAGE_RUNTIME_SHA256
    ):
        raise ReleaseError("AppImage runtime lock entry does not match required type2 runtime 20251108")
    actual = sha256_file(path)
    if actual != APPIMAGE_RUNTIME_SHA256:
        raise ReleaseError(f"AppImage runtime SHA-256 mismatch: expected {APPIMAGE_RUNTIME_SHA256}, got {actual}")


def probe_supervisor_memory(supervisor: Path) -> None:
    """Require an executed child under the memory-limit protocol used by native Open."""
    try:
        supervised = subprocess.run(
            [str(supervisor), "--timeout-ms", "5000", "--max-output", "4096",
             "--memory-mib", "64", "--", "/bin/true"],
            text=True, capture_output=True, timeout=15, check=True)
        packet = json.loads(supervised.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ReleaseError(
            f"native converter supervisor lacks enforced memory-limit support: {exc}") from exc
    if (not isinstance(packet, dict) or packet.get("returncode") != 0
            or packet.get("timed_out") is not False or packet.get("error") is not None):
        raise ReleaseError(f"native converter supervisor memory probe failed: {packet!r}")


def probe_native_library_abi(
    path: Path, symbol: str, expected: int, occt_library_dir: Path,
) -> int:
    """Load one component with only the bundled OCCT DSO directory in its search path."""
    if not occt_library_dir.is_dir() or occt_library_dir.is_symlink():
        raise ReleaseError(f"bundled OCCT library directory is missing or symlinked: {occt_library_dir}")
    probe = (
        "import ctypes,sys; "
        "lib=ctypes.CDLL(sys.argv[1]); "
        "fn=getattr(lib,sys.argv[2]); "
        "fn.argtypes=[]; fn.restype=ctypes.c_uint32; "
        "print(int(fn()))"
    )
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = str(occt_library_dir)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", probe, str(path), symbol],
            env=environment, text=True, capture_output=True, timeout=30, check=True)
        actual = int(completed.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        detail = getattr(exc, "stderr", "")
        raise ReleaseError(f"cannot verify {path.name} ABI: {detail or exc}") from exc
    if actual != expected:
        raise ReleaseError(f"{path.name} ABI must be {expected}, got {actual}")
    return actual


def validate_native_components(
    directory: Path, document_core_artifacts: Path, occt_library_dir: Path,
) -> dict[str, dict[str, object]]:
    if not directory.is_dir() or directory.is_symlink():
        raise ReleaseError(f"native component directory is missing or symlinked: {directory}")
    try:
        from verify_document_core import verify_directory as verify_document_core
        document_manifest = verify_document_core(document_core_artifacts)
    except Exception as exc:  # noqa: BLE001
        raise ReleaseError(f"document-core artifacts are not source-bound: {exc}") from exc
    linux_records = [record for record in document_manifest["artifacts"]
                     if record.get("platform") == "linux-x86_64"]
    if len(linux_records) != 1:
        raise ReleaseError("document-core artifact manifest lacks one Linux record")
    records = {}
    for filename in NATIVE_COMPONENTS:
        path = directory / filename
        require_regular_file(path, f"native component {filename}")
        if not filename.endswith(".so") and not os.access(path, os.X_OK):
            raise ReleaseError(f"native component is not executable: {filename}")
        records[filename] = {"size": path.stat().st_size, "sha256": sha256_file(path)}
    if (records["narwhal-document-core"]["sha256"] != linux_records[0].get("sha256")
            or records["narwhal-document-core"]["size"] != linux_records[0].get("size")):
        raise ReleaseError("packaged document core differs from source-bound Linux artifact")
    records["narwhal-document-core"]["source_aggregate_sha256"] = (
        document_manifest["source_aggregate_sha256"])
    welcome_data = (directory / "narwhal-welcome").read_bytes()
    welcome_markers = (b"--runtime is required", b"--bootstrap is required",
                       b"NARWHAL3D_WELCOME_REQUEST", b"NARWHAL3D_VERSION",
                       b"releases?per_page=20", b"download sha256 does not match",
                       b"could not atomically replace current AppImage")
    if not all(marker in welcome_data for marker in welcome_markers):
        raise ReleaseError("native welcome executable lacks its runtime/bootstrap request contract")
    records["narwhal-welcome"]["launch_contract"] = 1
    supervisor = directory / "narwhal-converter-supervisor"
    probe_supervisor_memory(supervisor)
    records["narwhal-converter-supervisor"]["memory_limit_probe_mib"] = 64
    try:
        required_glibc, _path = scan_runtime_glibc(directory)
    except RuntimeError as exc:
        raise ReleaseError(str(exc)) from exc
    for filename, (symbol, expected_abi) in NATIVE_LIBRARY_ABIS.items():
        records[filename]["abi"] = probe_native_library_abi(
            directory / filename, symbol, expected_abi, occt_library_dir)
    try:
        completed = subprocess.run(
            [str(directory / "narwhal-cdr-multiversion"), "--version"],
            text=True, capture_output=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(f"cannot execute bundled multi-version CDR reader: {exc}") from exc
    protocol = completed.stdout.strip()
    if protocol != CDR_MULTIVERSION_PROTOCOL:
        raise ReleaseError(f"wrong multi-version CDR protocol: {protocol!r}")
    records["narwhal-cdr-multiversion"]["protocol"] = protocol
    try:
        document_version = subprocess.run(
            [str(directory / "narwhal-document-core"), "--version"],
            text=True, capture_output=True, timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(f"cannot execute bundled Narwhal document core: {exc}") from exc
    if document_version != DOCUMENT_CORE_PROTOCOL:
        raise ReleaseError(f"wrong Narwhal document-core protocol: {document_version!r}")
    records["narwhal-document-core"]["protocol"] = document_version
    records["required_glibc"] = format_glibc(required_glibc)
    return records


def copy_file(source: Path, target: Path, description: str) -> None:
    require_regular_file(source, description)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def ignore_python_cache(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))}


def inventory(appdir: Path) -> list[JsonObject]:
    """Return a sorted cryptographic AppDir inventory, excluding the manifest itself."""
    entries: list[JsonObject] = []
    manifest_relative = PurePosixPath(MANIFEST_RELATIVE.as_posix())
    for path in sorted(appdir.rglob("*"), key=lambda item: item.relative_to(appdir).as_posix()):
        relative = PurePosixPath(path.relative_to(appdir).as_posix())
        if relative == manifest_relative:
            continue
        info = path.lstat()
        entry: JsonObject = {"path": relative.as_posix(), "mode": f"{stat.S_IMODE(info.st_mode):04o}"}
        if stat.S_ISLNK(info.st_mode):
            entry["type"] = "symlink"
            entry["target"] = os.readlink(path)
        elif stat.S_ISDIR(info.st_mode):
            entry["type"] = "directory"
        elif stat.S_ISREG(info.st_mode):
            entry["type"] = "file"
            entry["size"] = info.st_size
            entry["sha256"] = sha256_file(path)
        else:
            raise ReleaseError(f"unsupported filesystem entry in AppDir: {relative}")
        entries.append(entry)
    return entries


def validate_appdir_paths(appdir: Path) -> None:
    """Reject forbidden names, unsafe entries, and links escaping the AppDir."""
    root = appdir.resolve()
    tools_root = APP_ROOT_RELATIVE / "tools"
    expected_runtime = APP_ROOT_RELATIVE / RUNTIME_RELATIVE
    for path in appdir.rglob("*"):
        relative = path.relative_to(appdir)
        if FORBIDDEN_PARTS.intersection(relative.parts):
            raise ReleaseError(f"forbidden private path entered AppDir: {relative}")
        if relative.is_relative_to(tools_root) and relative != tools_root and not relative.is_relative_to(expected_runtime):
            raise ReleaseError(f"unexpected tools payload entered AppDir: {relative}")
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            target_text = os.readlink(path)
            if os.path.isabs(target_text):
                raise ReleaseError(f"absolute symlink entered AppDir: {relative} -> {target_text}")
            try:
                target = path.resolve(strict=True)
            except OSError as exc:
                raise ReleaseError(f"broken symlink entered AppDir: {relative} -> {target_text}") from exc
            if not target.is_relative_to(root):
                raise ReleaseError(f"escaping symlink entered AppDir: {relative} -> {target_text}")
        elif not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ReleaseError(f"unsafe special entry entered AppDir: {relative}")


def normalize_mtimes(root: Path, epoch: int) -> None:
    """Normalize AppDir timestamps before invoking appimagetool."""
    for path in [root, *sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())]:
        os.utime(path, (epoch, epoch), follow_symlinks=False)


def appimagetool_command(tool: Path, runtime: Path, appdir: Path, appimage: Path) -> list[str]:
    """Return the offline appimagetool command with an explicit locked runtime."""
    return [
        str(tool),
        "--appimage-extract-and-run",
        "--runtime-file",
        str(runtime),
        "--mksquashfs-opt=-processors", "--mksquashfs-opt=4",
        "--mksquashfs-opt=-mem", "--mksquashfs-opt=512M",
        str(appdir),
        str(appimage),
    ]


def invoke_appimagetool(
    tool: Path,
    runtime: Path,
    appdir: Path,
    appimage: Path,
    version: str,
    epoch: int,
) -> None:
    """Build through locked appimagetool/runtime without FUSE or network fallback."""
    environment = os.environ.copy()
    environment.update(
        {
            "APPIMAGE_EXTRACT_AND_RUN": "1",
            "ARCH": "x86_64",
            "LC_ALL": "C",
            "SOURCE_DATE_EPOCH": str(epoch),
            "TZ": "UTC",
            "VERSION": version,
        }
    )
    try:
        subprocess.run(
            appimagetool_command(tool, runtime, appdir, appimage),
            cwd=appimage.parent,
            env=environment,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(f"locked appimagetool failed: {exc}") from exc
    require_regular_file(appimage, "AppImage output")
    if appimage.stat().st_size == 0:
        raise ReleaseError("appimagetool produced an empty AppImage")
    os.chmod(appimage, 0o755)


def assemble(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    if not VERSION_RE.fullmatch(args.version):
        raise ReleaseError("--version must contain only letters, digits, dots, underscores, and hyphens")
    try:
        validate_preview_version(args.version)
    except ValueError as exc:
        raise ReleaseError(str(exc)) from exc
    if not REVISION_RE.fullmatch(args.source_revision):
        raise ReleaseError("--source-revision must be a full 40-hex Git revision")
    if args.source_date_epoch < 0:
        raise ReleaseError("--source-date-epoch must be non-negative")

    source_root = args.source_root.resolve()
    runtime_dir = args.runtime_dir.resolve()
    appimagetool = args.appimagetool.resolve()
    appimage_runtime = args.appimage_runtime.resolve()
    output_dir = args.output_dir.resolve()
    native_components_dir = args.native_components_dir.resolve()
    document_core_artifacts = args.document_core_artifacts.resolve()
    if not source_root.is_dir():
        raise ReleaseError(f"source root does not exist: {source_root}")
    lock_path = source_root / SOURCE_LOCK_RELATIVE
    _lock, artifacts, lock_sha, native_marker, runtime_glibc = validate_lock_and_runtime(
        lock_path,
        runtime_dir,
        source_root,
    )
    validate_appimagetool(appimagetool, artifacts)
    validate_appimage_runtime(appimage_runtime, artifacts)
    native_components = validate_native_components(
        native_components_dir, document_core_artifacts, runtime_dir / OCCT_LIB_RELATIVE)

    launcher = source_root / "narwhal3d.sh"
    package = source_root / "narwhal3d"
    licenses = source_root / "licenses"
    require_regular_file(launcher, "Narwhal 3D launcher")
    require_regular_file(source_root / "LICENSE", "project LICENSE")
    require_regular_file(source_root / "THIRD_PARTY_NOTICES", "project THIRD_PARTY_NOTICES")
    validate_release_source_package(package)
    validate_source_tree(licenses, "project licenses", reject_symlinks=True)
    for filename in REQUIRED_LICENSE_FILES:
        require_regular_file(licenses / filename, f"required license text {filename}")
    for filename in LAUNCH_FILES:
        require_regular_file(source_root / "launch" / filename, f"launch asset {filename}")
    for filename in PACKAGING_FILES:
        require_regular_file(source_root / "packaging" / filename, f"packaging file {filename}")

    appdir = output_dir / f"Narwhal_3D-{args.version}-x86_64.AppDir"
    appimage = output_dir / f"Narwhal_3D-{args.version}-x86_64.AppImage"
    checksum = output_dir / f"{appimage.name}.sha256"
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in (appdir, appimage, checksum):
        if path.exists() or path.is_symlink():
            raise ReleaseError(f"output already exists; refusing to overwrite: {path}")

    try:
        appdir.mkdir(mode=0o755)
        app_root = appdir / APP_ROOT_RELATIVE
        app_root.mkdir(parents=True, mode=0o755)

        app_run = appdir / "AppRun"
        app_run.write_text(render_app_run(args.version), encoding="utf-8")
        os.chmod(app_run, 0o755)
        desktop = appdir / "narwhal3d.desktop"
        desktop.write_text(render_desktop_entry(args.version), encoding="utf-8")
        os.chmod(desktop, 0o644)
        copy_file(source_root / "launch/narwhal3d.png", appdir / "narwhal3d.png", "AppImage icon")
        (appdir / ".DirIcon").symlink_to("narwhal3d.png")

        copy_file(launcher, app_root / "narwhal3d.sh", "Narwhal 3D launcher")
        shutil.copytree(package, app_root / "narwhal3d", symlinks=True, ignore=ignore_python_cache)
        for filename in LAUNCH_FILES:
            copy_file(source_root / "launch" / filename, app_root / "launch" / filename, f"launch asset {filename}")
        for filename in PACKAGING_FILES:
            copy_file(
                source_root / "packaging" / filename,
                app_root / "packaging" / filename,
                f"packaging file {filename}",
            )
        copy_file(source_root / "LICENSE", app_root / "LICENSE", "project LICENSE")
        copy_file(
            source_root / "THIRD_PARTY_NOTICES",
            app_root / "THIRD_PARTY_NOTICES",
            "project THIRD_PARTY_NOTICES",
        )
        shutil.copytree(licenses, app_root / "licenses", symlinks=True)
        (app_root / "tools").mkdir(mode=0o755)
        shutil.copytree(runtime_dir, app_root / RUNTIME_RELATIVE, symlinks=True)
        native_target = app_root / NATIVE_COMPONENT_RELATIVE
        native_target.mkdir(parents=True, mode=0o755)
        for filename in NATIVE_COMPONENTS:
            copy_file(native_components_dir / filename, native_target / filename,
                      f"native component {filename}")
        packaged_native = app_root / "narwhal3d" / "native"
        packaged_native.mkdir(parents=True, exist_ok=True)
        for filename in PACKAGED_ACCELERATORS:
            copy_file(native_components_dir / filename, packaged_native / filename,
                      f"packaged accelerator {filename}")

        validate_appdir_paths(appdir)
        manifest = {
            "schema": 1,
            "identity": {
                "product": PRODUCT,
                "version": args.version,
                "platform": PLATFORM,
                "package_format": PACKAGE_FORMAT,
                "app_root": APP_ROOT_RELATIVE.as_posix(),
                "runtime_flavor": RUNTIME_FLAVOR,
                "blender_version": "5.2.2",
                "runtime_source_commit": BLENDER_SOURCE_COMMIT,
                "runtime_patch_aggregate_sha256": native_marker["patch_aggregate_sha256"],
                "runtime_binary_sha256": native_marker["binary_sha256"],
                "runtime_build_timestamp_utc": native_marker["build_timestamp_utc"],
                "runtime_build_source_date_epoch": native_marker.get(RUNTIME_BUILD_EPOCH_KEY),
                "appimagetool_version": "1.9.1",
                "appimagetool_sha256": APPIMAGETOOL_SHA256,
                "appimage_runtime_version": APPIMAGE_RUNTIME_VERSION,
                "appimage_runtime_sha256": APPIMAGE_RUNTIME_SHA256,
                "source_revision": args.source_revision.lower(),
                "source_date_epoch": args.source_date_epoch,
                "max_host_glibc": format_glibc(MAX_HOST_GLIBC),
                "runtime_required_glibc": format_glibc(runtime_glibc),
                "lock_sha256": lock_sha,
            },
            "native_components": native_components,
            "excluded_components": ["opennurbs-exact-save-helper", "optional-converters"],
            "inventory": inventory(appdir),
        }
        manifest_path = appdir / MANIFEST_RELATIVE
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        validate_appdir_paths(appdir)
        normalize_mtimes(appdir, args.source_date_epoch)
        invoke_appimagetool(
            appimagetool,
            appimage_runtime,
            appdir,
            appimage,
            args.version,
            args.source_date_epoch,
        )
        checksum.write_text(f"{sha256_file(appimage)}  {appimage.name}\n", encoding="ascii")
    except Exception:
        shutil.rmtree(appdir, ignore_errors=True)
        appimage.unlink(missing_ok=True)
        checksum.unlink(missing_ok=True)
        raise
    return appdir, appimage, checksum


def self_check() -> None:
    for good in ("1.0.0", "2026.10-beta_1"):
        if not VERSION_RE.fullmatch(good):
            raise ReleaseError(f"self-check rejected syntactically safe version: {good}")
        if f"export NARWHAL3D_VERSION={good}" not in render_app_run(good):
            raise ReleaseError(f"self-check did not render AppRun version: {good}")
    for bad in ("", "../escape", "name/child", " space"):
        if VERSION_RE.fullmatch(bad):
            raise ReleaseError(f"self-check accepted unsafe version: {bad!r}")
    for preview in ("0.4.0", "0.4.12-alpha.1"):
        validate_preview_version(preview)
    for forbidden in ("0.3.9", "0.5.0", "1.0.0"):
        try:
            validate_preview_version(forbidden)
        except ValueError:
            pass
        else:
            raise ReleaseError(f"self-check accepted release outside 0.4.x: {forbidden}")
    parsed_glibc = glibc_versions("GLIBC_2.35 GLIBC_2.38 GLIBC_2.43")
    if max(parsed_glibc) != (2, 43) or MAX_HOST_GLIBC != (2, 35):
        raise ReleaseError("self-check lost the Ubuntu 22.04 host-glibc gate")
    if "launch/build_brep_writer.sh" in {f"launch/{name}" for name in LAUNCH_FILES}:
        raise ReleaseError("self-check found exact-save helper in launch whitelist")
    expected_excluded_files = {PurePosixPath("kernel/brep_writer.cpp")}
    if EXCLUDED_PACKAGE_FILES != expected_excluded_files:
        raise ReleaseError("self-check found an incomplete excluded package file set")
    for included in (PurePosixPath("cdr_reader.py"), PurePosixPath("cdr_writer.py")):
        if is_excluded_package_payload(included):
            raise ReleaseError(f"self-check still excludes public CDR source: {included}")
    for excluded in (*expected_excluded_files, PurePosixPath("kernel/bin/helper")):
        if not is_excluded_package_payload(excluded):
            raise ReleaseError(f"self-check accepted excluded package payload: {excluded}")
    if is_excluded_package_payload(EXCLUDED_PACKAGE_TREE):
        raise ReleaseError("self-check rejected an empty kernel/bin directory rather than its payload")
    expected_artifacts = {
        "appimagetool",
        "appimage-runtime",
        "blender-runtime",
        "rhino3dm",
        "cadquery-ocp-novtk",
        "cadquery-ocp-proxy",
    }
    if REQUIRED_ARTIFACT_IDS != expected_artifacts:
        raise ReleaseError("self-check found an incomplete required artifact set")
    if (
        APPIMAGE_RUNTIME_VERSION != "20251108"
        or APPIMAGE_RUNTIME_SHA256 != "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"
    ):
        raise ReleaseError("self-check found the wrong AppImage type2 runtime identity")
    command = appimagetool_command(
        Path("appimagetool"),
        Path("runtime-x86_64"),
        Path("Narwhal.AppDir"),
        Path("Narwhal.AppImage"),
    )
    if command[2:4] != ["--runtime-file", "runtime-x86_64"]:
        raise ReleaseError("self-check found appimagetool command without locked --runtime-file")
    if RUNTIME_FLAVOR != "native-narwhal-runtime" or RUNTIME_RELATIVE != Path("tools/blender-patched"):
        raise ReleaseError("self-check found a non-native release runtime")
    if BLENDER_SOURCE_COMMIT != "d13f752e3b9c4f8c261cda552b1021f8bcc0382c":
        raise ReleaseError("self-check found the wrong Blender source commit")
    if RUNTIME_BUILD_EPOCH_KEY != "build_source_date_epoch":
        raise ReleaseError("self-check found an ambiguous native build epoch key")
    if validate_optional_epoch(None, "self-check epoch") is not None:
        raise ReleaseError("self-check changed null native build epoch")
    if validate_optional_epoch(0, "self-check epoch") != 0:
        raise ReleaseError("self-check changed valid native build epoch")
    for invalid_epoch in (-1, True, "123"):
        try:
            validate_optional_epoch(invalid_epoch, "self-check epoch")
        except ReleaseError:
            pass
        else:
            raise ReleaseError(f"self-check accepted invalid native build epoch: {invalid_epoch!r}")
    expected_licenses = {
        "GPL-3.0-or-later.txt",
        "rhino3dm-MIT.txt",
        "OCP-Apache-2.0.txt",
        "LGPL-2.1-or-later.txt",
        "OCCT-exception-1.0.txt",
        "AppImage-MIT.txt",
        "TraceDraw-MIT.txt",
        "TraceDraw-Rust-Dependencies.txt",
    }
    if set(REQUIRED_LICENSE_FILES) != expected_licenses:
        raise ReleaseError("self-check found an incomplete required license set")
    if "NARWHAL3D_APPIMAGE=1" not in APP_RUN or "usr/lib/narwhal3d/narwhal3d.sh" not in APP_RUN:
        raise ReleaseError("self-check found an invalid AppRun launcher")
    rendered_desktop = render_desktop_entry("0.1.0")
    if "Exec=AppRun %F" not in rendered_desktop or "Icon=narwhal3d" not in rendered_desktop:
        raise ReleaseError("self-check found an invalid AppImage desktop entry")
    if rendered_desktop.splitlines().count("X-AppImage-Version=0.1.0") != 1:
        raise ReleaseError("self-check found missing or duplicate exact AppImage desktop version")
    with tempfile.TemporaryDirectory(prefix="narwhal3d-license-self-check-") as temporary:
        app_run = Path(temporary) / "AppRun"
        app_run.write_text(APP_RUN, encoding="utf-8")
        try:
            subprocess.run(
                ["sh", "-n", str(app_run)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ReleaseError(f"generated AppRun failed shell syntax validation: {exc}") from exc
        runtime_artifacts = {
            "appimage-runtime": {
                "kind": "appimage-runtime",
                "version": APPIMAGE_RUNTIME_VERSION,
                "filename": "runtime-x86_64",
                "sha256": APPIMAGE_RUNTIME_SHA256,
            }
        }
        missing_runtime = Path(temporary) / "missing-runtime-x86_64"
        wrong_runtime = Path(temporary) / "runtime-x86_64"
        wrong_runtime.write_bytes(b"wrong runtime")
        for candidate in (missing_runtime, wrong_runtime):
            try:
                validate_appimage_runtime(candidate, runtime_artifacts)
            except ReleaseError:
                pass
            else:
                raise ReleaseError(f"self-check accepted missing/wrong AppImage runtime: {candidate}")
        licenses = Path(temporary) / "licenses"
        licenses.mkdir()
        target = licenses / "target.txt"
        target.write_text("license\n", encoding="utf-8")
        (licenses / "linked.txt").symlink_to(target.name)
        try:
            validate_source_tree(licenses, "self-check licenses", reject_symlinks=True)
        except ReleaseError:
            pass
        else:
            raise ReleaseError("self-check accepted a symlink in the license directory")
        if NATIVE_COMPONENTS != (
            "libnarwhal_cdr_core.so", "libnarwhal_geometry_core.so",
            "libnarwhal_platform_core.so", "libnarwhal_pure_core.so",
            "libnarwhal_selection_core.so", "narwhal-converter-supervisor",
            "narwhal-cdr-multiversion", "narwhal-document-core", "narwhal-welcome",
            "libnarwhal_automation.so", "narwhal-automation",
        ):
            raise ReleaseError("self-check found an incomplete native component set")
        if PACKAGED_ACCELERATORS != (
            "libnarwhal_geometry_core.so", "libnarwhal_platform_core.so",
            "libnarwhal_pure_core.so", "libnarwhal_selection_core.so",
        ) or {name: abi for name, (_symbol, abi) in NATIVE_LIBRARY_ABIS.items()} != {
            "libnarwhal_automation.so": 1,
            "libnarwhal_cdr_core.so": 3,
            "libnarwhal_geometry_core.so": 4,
            "libnarwhal_platform_core.so": 1,
            "libnarwhal_pure_core.so": 1,
            "libnarwhal_selection_core.so": 1,
        }:
            raise ReleaseError("self-check found an incomplete accelerator ABI contract")
        supervisor_probe = Path(temporary) / "supervisor-probe"
        supervisor_probe.write_text(
            "#!/bin/sh\n"
            "case \" $* \" in\n"
            "  *\" --memory-mib 64 \"*) "
            "printf '%s\\n' '{\"returncode\":0,\"stdout\":\"\",\"stderr\":\"\","
            "\"timed_out\":false,\"error\":null}' ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n",
            encoding="utf-8")
        supervisor_probe.chmod(0o755)
        probe_supervisor_memory(supervisor_probe)
    print("assemble_linux_release.py self-check: OK")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assemble a native-runtime Narwhal 3D Linux x86_64 AppImage."
    )
    parser.add_argument("--version", help="explicit public release version")
    parser.add_argument("--source-revision", help="full 40-hex source Git revision")
    parser.add_argument("--source-date-epoch", type=int, help="fixed AppImage build timestamp")
    parser.add_argument("--source-root", type=Path, default=ROOT, help="public source checkout root")
    parser.add_argument("--runtime-dir", type=Path, help="verified native Narwhal runtime")
    parser.add_argument("--native-components-dir", type=Path,
                        help="verified native component directory")
    parser.add_argument("--document-core-artifacts", type=Path,
                        help="source-bound Linux/Windows document-core artifact directory")
    parser.add_argument("--appimagetool", type=Path, help="locked appimagetool 1.9.1 executable")
    parser.add_argument("--appimage-runtime", type=Path, help="locked type2 runtime 20251108 file")
    parser.add_argument("--output-dir", type=Path, help="new release output directory")
    parser.add_argument("--self-check", action="store_true", help="run local pure checks and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.self_check:
            self_check()
            return 0
        missing = [
            option
            for option, value in (
                ("--version", args.version),
                ("--source-revision", args.source_revision),
                ("--source-date-epoch", args.source_date_epoch),
                ("--runtime-dir", args.runtime_dir),
                ("--native-components-dir", args.native_components_dir),
                ("--document-core-artifacts", args.document_core_artifacts),
                ("--appimagetool", args.appimagetool),
                ("--appimage-runtime", args.appimage_runtime),
                ("--output-dir", args.output_dir),
            )
            if value is None
        ]
        if missing:
            raise ReleaseError(f"required arguments missing: {', '.join(missing)}")
        appdir, appimage, checksum = assemble(args)
        print(f"appdir={appdir}")
        print(f"appimage={appimage}")
        print(f"checksum={checksum}")
        return 0
    except (ReleaseError, OSError, shutil.Error) as exc:
        print(f"assemble_linux_release.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
