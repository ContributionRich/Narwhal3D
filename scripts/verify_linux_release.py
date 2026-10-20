#!/usr/bin/env python3
"""Verify Narwhal 3D Linux AppDir and AppImage release artifacts."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any

from release_policy import (
    MAX_HOST_GLIBC, format_glibc, glibc_versions, scan_runtime_glibc,
    validate_preview_version,
)


PRODUCT = "Narwhal 3D"
PLATFORM = "linux-x86_64"
PACKAGE_FORMAT = "appimage"
RUNTIME_FLAVOR = "native-narwhal-runtime"
BLENDER_VERSION = "5.2.2"
BLENDER_SOURCE_COMMIT = "d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
RUNTIME_BUILD_EPOCH_KEY = "build_source_date_epoch"
APPIMAGETOOL_VERSION = "1.9.1"
APPIMAGETOOL_SHA256 = "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
APPIMAGE_RUNTIME_VERSION = "20251108"
APPIMAGE_RUNTIME_SHA256 = "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"
APP_ROOT_RELATIVE = Path("usr/lib/narwhal3d")
RUNTIME_RELATIVE = Path("tools/blender-patched")
NATIVE_COMPONENT_RELATIVE = Path("runtime/linux-x86_64/bin")
CDR_MULTIVERSION_PROTOCOL = "narwhal-cdr-multiversion 1 tracedraw 821ba154 read 16-18 write 17"
DOCUMENT_CORE_PROTOCOL = "narwhal-document-core 1"
NATIVE_COMPONENTS = (
    "libnarwhal_cdr_core.so", "libnarwhal_pure_core.so",
    "libnarwhal_selection_core.so", "narwhal-converter-supervisor",
    "narwhal-cdr-multiversion", "narwhal-document-core",
)
MANIFEST_RELATIVE = PurePosixPath("usr/lib/narwhal3d/packaging/release-manifest.json")
LOCK_RELATIVE = APP_ROOT_RELATIVE / "packaging/linux-x86_64.lock.json"
FORBIDDEN_PARTS = {"vendor", "More_Data", "ExamplePhotos", ".git", "bug_reports", "artifacts"}
EXCLUDED_APP_FILES = {
    PurePosixPath("usr/lib/narwhal3d/narwhal3d/kernel/brep_writer.cpp"),
}
EXCLUDED_APP_TREE = PurePosixPath("usr/lib/narwhal3d/narwhal3d/kernel/bin")
DISTRIBUTIONS = {
    "rhino3dm": "8.35.0",
    "cadquery-ocp-novtk": "8.0.1.0.0",
    "cadquery-ocp-proxy": "8.0.1.0.0",
}
REQUIRED_ARTIFACT_IDS = {"appimagetool", "appimage-runtime", "blender-runtime", *DISTRIBUTIONS}
REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
JsonObject = dict[str, Any]

REQUIRED_NOTICE_PATHS = {
    "usr/lib/narwhal3d/LICENSE",
    "usr/lib/narwhal3d/THIRD_PARTY_NOTICES",
    "usr/lib/narwhal3d/licenses",
    "usr/lib/narwhal3d/licenses/GPL-3.0-or-later.txt",
    "usr/lib/narwhal3d/licenses/rhino3dm-MIT.txt",
    "usr/lib/narwhal3d/licenses/OCP-Apache-2.0.txt",
    "usr/lib/narwhal3d/licenses/LGPL-2.1-or-later.txt",
    "usr/lib/narwhal3d/licenses/OCCT-exception-1.0.txt",
    "usr/lib/narwhal3d/licenses/AppImage-MIT.txt",
    "usr/lib/narwhal3d/licenses/TraceDraw-MIT.txt",
    "usr/lib/narwhal3d/licenses/TraceDraw-Rust-Dependencies.txt",
}
REQUIRED_PATHS = {
    *REQUIRED_NOTICE_PATHS,
    ".DirIcon",
    "AppRun",
    "narwhal3d.desktop",
    "narwhal3d.png",
    "usr",
    "usr/lib",
    "usr/lib/narwhal3d",
    "usr/lib/narwhal3d/narwhal3d.sh",
    "usr/lib/narwhal3d/narwhal3d",
    "usr/lib/narwhal3d/launch/bootstrap.py",
    "usr/lib/narwhal3d/launch/open_arg.py",
    "usr/lib/narwhal3d/launch/narwhal3d.desktop.in",
    "usr/lib/narwhal3d/launch/narwhal3d-3dm.xml",
    "usr/lib/narwhal3d/launch/narwhal3d.png",
    "usr/lib/narwhal3d/packaging/linux-x86_64.lock.json",
    "usr/lib/narwhal3d/packaging/README.md",
    "usr/lib/narwhal3d/packaging/NOTICES.md",
    "usr/lib/narwhal3d/tools/blender-patched/blender",
    "usr/lib/narwhal3d/tools/blender-patched/5.2/python/bin/python3.13",
    "usr/lib/narwhal3d/tools/blender-patched/.narwhal3d-native-runtime.json",
    "usr/lib/narwhal3d/runtime/linux-x86_64/bin/libnarwhal_cdr_core.so",
    "usr/lib/narwhal3d/runtime/linux-x86_64/bin/libnarwhal_pure_core.so",
    "usr/lib/narwhal3d/runtime/linux-x86_64/bin/libnarwhal_selection_core.so",
    "usr/lib/narwhal3d/narwhal3d/native/libnarwhal_pure_core.so",
    "usr/lib/narwhal3d/narwhal3d/native/libnarwhal_selection_core.so",
    "usr/lib/narwhal3d/runtime/linux-x86_64/bin/narwhal-converter-supervisor",
    "usr/lib/narwhal3d/runtime/linux-x86_64/bin/narwhal-cdr-multiversion",
    "usr/lib/narwhal3d/runtime/linux-x86_64/bin/narwhal-document-core",
}


class VerificationError(RuntimeError):
    """The AppDir or AppImage is incomplete, tampered with, or unsafe."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def binary_contains(path: Path, marker: bytes) -> bool:
    """Search a large binary for an exact native-branding marker."""
    overlap = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            data = overlap + chunk
            if marker in data:
                return True
            overlap = data[-max(0, len(marker) - 1):]
    return False


def validate_optional_epoch(value: object, description: str) -> int | None:
    """Return an optional nonnegative epoch while rejecting booleans and strings."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VerificationError(f"{description} must be null or a nonnegative integer")
    return value


def read_json(path: Path, description: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"{description} must be a JSON object: {path}")
    return value


def is_excluded_app_payload(path: PurePosixPath) -> bool:
    """Return whether an AppDir-relative path is forbidden private payload."""
    return path in EXCLUDED_APP_FILES or (
        path != EXCLUDED_APP_TREE and path.is_relative_to(EXCLUDED_APP_TREE)
    )


def checked_relative(value: str) -> PurePosixPath:
    """Parse a canonical, AppDir-relative manifest path."""
    if not value or value.startswith("/") or "\\" in value:
        raise VerificationError(f"unsafe manifest path: {value!r}")
    path = PurePosixPath(value)
    if path.as_posix() != value or value in {".", ".."} or ".." in path.parts:
        raise VerificationError(f"non-canonical manifest path: {value!r}")
    if FORBIDDEN_PARTS.intersection(path.parts):
        raise VerificationError(f"forbidden private path in release: {value}")
    if is_excluded_app_payload(path):
        raise VerificationError(f"excluded private application payload in release: {value}")
    tools_root = PurePosixPath("usr/lib/narwhal3d/tools")
    runtime_root = tools_root / "blender-patched"
    if path.is_relative_to(tools_root) and path != tools_root and not path.is_relative_to(runtime_root):
        raise VerificationError(f"unexpected tools payload in release: {value}")
    return path


def actual_inventory(appdir: Path) -> dict[str, JsonObject]:
    """Build the exact filesystem inventory recorded by the assembler."""
    result: dict[str, JsonObject] = {}
    root = appdir.resolve()
    licenses_root = PurePosixPath("usr/lib/narwhal3d/licenses")
    for path in sorted(appdir.rglob("*"), key=lambda item: item.relative_to(appdir).as_posix()):
        relative_text = path.relative_to(appdir).as_posix()
        relative = checked_relative(relative_text)
        if relative == MANIFEST_RELATIVE:
            continue
        info = path.lstat()
        entry: JsonObject = {"path": relative_text, "mode": f"{stat.S_IMODE(info.st_mode):04o}"}
        if stat.S_ISLNK(info.st_mode):
            if relative == EXCLUDED_APP_TREE:
                raise VerificationError("excluded kernel/bin path must not be a symlink")
            if relative.is_relative_to(licenses_root):
                raise VerificationError(f"symlink is not allowed in bundled licenses: {relative}")
            target_text = os.readlink(path)
            if os.path.isabs(target_text):
                raise VerificationError(f"absolute symlink: {relative} -> {target_text}")
            try:
                target = path.resolve(strict=True)
            except OSError as exc:
                raise VerificationError(f"broken symlink: {relative} -> {target_text}") from exc
            if not target.is_relative_to(root):
                raise VerificationError(f"symlink escapes AppDir: {relative} -> {target_text}")
            entry["type"] = "symlink"
            entry["target"] = target_text
        elif stat.S_ISDIR(info.st_mode):
            entry["type"] = "directory"
        elif stat.S_ISREG(info.st_mode):
            entry["type"] = "file"
            entry["size"] = info.st_size
            entry["sha256"] = sha256_file(path)
        else:
            raise VerificationError(f"unsupported filesystem entry: {relative}")
        result[relative_text] = entry
    return result


def expected_inventory(manifest: JsonObject) -> dict[str, JsonObject]:
    entries = manifest.get("inventory")
    if not isinstance(entries, list):
        raise VerificationError("release manifest inventory is not a list")
    result: dict[str, JsonObject] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise VerificationError("release manifest contains an invalid inventory entry")
        relative = checked_relative(entry["path"])
        if relative == MANIFEST_RELATIVE:
            raise VerificationError("release manifest must not recursively inventory itself")
        if entry["path"] in result:
            raise VerificationError(f"duplicate inventory path: {entry['path']}")
        result[entry["path"]] = entry
    return result


def verify_identity(manifest: JsonObject, args: argparse.Namespace) -> JsonObject:
    try:
        validate_preview_version(args.expected_version)
    except ValueError as exc:
        raise VerificationError(str(exc)) from exc
    if manifest.get("schema") != 1:
        raise VerificationError("unsupported release manifest schema")
    identity = manifest.get("identity")
    if not isinstance(identity, dict):
        raise VerificationError("release manifest has no identity object")
    expected = {
        "product": PRODUCT,
        "version": args.expected_version,
        "platform": PLATFORM,
        "package_format": PACKAGE_FORMAT,
        "app_root": APP_ROOT_RELATIVE.as_posix(),
        "runtime_flavor": RUNTIME_FLAVOR,
        "blender_version": BLENDER_VERSION,
        "runtime_source_commit": BLENDER_SOURCE_COMMIT,
        "appimagetool_version": APPIMAGETOOL_VERSION,
        "appimagetool_sha256": APPIMAGETOOL_SHA256,
        "appimage_runtime_version": APPIMAGE_RUNTIME_VERSION,
        "appimage_runtime_sha256": APPIMAGE_RUNTIME_SHA256,
        "source_revision": args.expected_source_revision.lower(),
        "max_host_glibc": format_glibc(MAX_HOST_GLIBC),
    }
    if args.expected_source_date_epoch is not None:
        expected["source_date_epoch"] = args.expected_source_date_epoch
    for key, value in expected.items():
        if identity.get(key) != value:
            raise VerificationError(f"release identity {key!r}: expected {value!r}, got {identity.get(key)!r}")
    source_date_epoch = identity.get("source_date_epoch")
    if isinstance(source_date_epoch, bool) or not isinstance(source_date_epoch, int) or source_date_epoch < 0:
        raise VerificationError("release identity has an invalid source_date_epoch")
    if "runtime_build_source_date_epoch" not in identity:
        raise VerificationError("release identity lacks runtime_build_source_date_epoch")
    validate_optional_epoch(
        identity.get("runtime_build_source_date_epoch"),
        "release identity runtime_build_source_date_epoch",
    )
    required_glibc = identity.get("runtime_required_glibc")
    parsed_glibc = glibc_versions(f"GLIBC_{required_glibc}") \
        if isinstance(required_glibc, str) else set()
    if len(parsed_glibc) != 1 or max(parsed_glibc) > MAX_HOST_GLIBC:
        raise VerificationError("release identity has an invalid runtime_required_glibc")
    for field in ("lock_sha256", "runtime_patch_aggregate_sha256", "runtime_binary_sha256"):
        value = identity.get(field)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise VerificationError(f"release identity has an invalid {field}")
    build_timestamp = identity.get("runtime_build_timestamp_utc")
    if not isinstance(build_timestamp, str) or not build_timestamp.endswith("Z"):
        raise VerificationError("release identity has an invalid native runtime build timestamp")
    if manifest.get("excluded_components") != ["opennurbs-exact-save-helper", "optional-converters"]:
        raise VerificationError("release exclusion policy is absent or altered")
    return identity


def lock_artifacts(lock: JsonObject) -> dict[str, JsonObject]:
    artifacts = lock.get("artifacts")
    if not isinstance(artifacts, list):
        raise VerificationError("bundled lock has no artifact list")
    result: dict[str, JsonObject] = {}
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in result:
            raise VerificationError("bundled lock contains an invalid or duplicate artifact")
        result[item["id"]] = item
    if set(result) != REQUIRED_ARTIFACT_IDS:
        raise VerificationError("bundled lock artifact set is incomplete or unexpected")
    return result


def verify_lock_and_markers(appdir: Path, identity: JsonObject) -> None:
    lock_path = appdir / LOCK_RELATIVE
    if not lock_path.is_file() or lock_path.is_symlink():
        raise VerificationError("bundled artifact lock is missing or is a symlink")
    raw_lock = lock_path.read_bytes()
    if hashlib.sha256(raw_lock).hexdigest() != identity["lock_sha256"]:
        raise VerificationError("bundled artifact lock hash does not match release identity")
    lock = read_json(lock_path, "artifact lock")
    if lock.get("schema") != 1 or lock.get("product") != PRODUCT or lock.get("platform") != PLATFORM:
        raise VerificationError("bundled lock has the wrong identity")
    development_runtime = lock.get("runtime")
    if not isinstance(development_runtime, dict) or development_runtime != {
        "purpose": "development-bootstrap-only",
        "flavor": "official-blender-runtime",
        "blender_version": BLENDER_VERSION,
        "bundle_path": "tools/blender-5.2.2-linux-x64",
    }:
        raise VerificationError("bundled lock must identify the official runtime as development-only")
    artifacts = lock_artifacts(lock)
    appimagetool = artifacts["appimagetool"]
    if (
        appimagetool.get("version") != APPIMAGETOOL_VERSION
        or appimagetool.get("filename") != "appimagetool-x86_64.AppImage"
        or appimagetool.get("sha256") != APPIMAGETOOL_SHA256
        or appimagetool.get("executable") is not True
    ):
        raise VerificationError("bundled appimagetool lock entry is not the required 1.9.1 artifact")
    appimage_runtime = artifacts["appimage-runtime"]
    if (
        appimage_runtime.get("kind") != "appimage-runtime"
        or appimage_runtime.get("version") != APPIMAGE_RUNTIME_VERSION
        or appimage_runtime.get("filename") != "runtime-x86_64"
        or appimage_runtime.get("sha256") != APPIMAGE_RUNTIME_SHA256
    ):
        raise VerificationError("bundled lock has the wrong AppImage type2 runtime")

    runtime = appdir / APP_ROOT_RELATIVE / RUNTIME_RELATIVE
    try:
        runtime_glibc, _glibc_path = scan_runtime_glibc(runtime)
    except RuntimeError as exc:
        raise VerificationError(str(exc)) from exc
    if format_glibc(runtime_glibc) != identity.get("runtime_required_glibc"):
        raise VerificationError("measured runtime GLIBC requirement differs from release identity")
    for official_marker in (".narwhal3d-runtime.json", ".narwhal3d-artifact.json"):
        if (runtime / official_marker).exists() or (runtime / official_marker).is_symlink():
            raise VerificationError(f"official development runtime marker is forbidden: {official_marker}")
    marker = read_json(runtime / ".narwhal3d-native-runtime.json", "native runtime provenance marker")
    if "source_date_epoch" in marker:
        raise VerificationError("native runtime marker uses deprecated ambiguous source_date_epoch")
    if RUNTIME_BUILD_EPOCH_KEY not in marker:
        raise VerificationError("native runtime marker lacks build_source_date_epoch")
    validate_optional_epoch(marker.get(RUNTIME_BUILD_EPOCH_KEY), "native build_source_date_epoch")
    marker_expected = {
        "schema": 1,
        "product": PRODUCT,
        "platform": PLATFORM,
        "runtime_flavor": RUNTIME_FLAVOR,
        "blender_version": BLENDER_VERSION,
        "blender_source_commit": BLENDER_SOURCE_COMMIT,
        "build_source_date_epoch": identity["runtime_build_source_date_epoch"],
        "build_timestamp_utc": identity["runtime_build_timestamp_utc"],
        "patch_aggregate_sha256": identity["runtime_patch_aggregate_sha256"],
        "binary_sha256": identity["runtime_binary_sha256"],
    }
    for key, value in marker_expected.items():
        if marker.get(key) != value:
            raise VerificationError(f"native runtime marker field {key!r} is inconsistent")
    patch_inputs = marker.get("patch_inputs")
    expected_names = [
        "blender_patches/apply_app_drawn.py",
        "blender_patches/apply_cad_boundary_wires.py",
        "blender_patches/apply_deferred_first_present.py",
        "blender_patches/apply_narwhal3d_branding.py",
        "blender_patches/narwhal3d_app_icon.svg",
    ]
    if not isinstance(patch_inputs, list) or [item.get("path") for item in patch_inputs if isinstance(item, dict)] != expected_names:
        raise VerificationError("native runtime marker has an incomplete or unordered patch input list")
    aggregate = hashlib.sha256()
    for item in patch_inputs:
        if not isinstance(item, dict):
            raise VerificationError("native runtime marker has an invalid patch input")
        name = item.get("path")
        digest = item.get("sha256")
        if not isinstance(name, str) or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise VerificationError("native runtime marker has an invalid patch hash")
        aggregate.update(name.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n")
    if aggregate.hexdigest() != marker["patch_aggregate_sha256"]:
        raise VerificationError("native runtime patch aggregate does not match its input hashes")
    binary = runtime / "blender"
    if marker["binary_sha256"] != sha256_file(binary):
        raise VerificationError("native runtime binary hash does not match provenance")
    version_first_line = marker.get("version_first_line")
    if not isinstance(version_first_line, str) or not version_first_line.startswith("Narwhal 3D 5.2.2"):
        raise VerificationError("native runtime marker has the wrong version banner")
    if not binary_contains(binary, b" - Narwhal 3D") or not binary_contains(binary, b"Narwhal 3D quit"):
        raise VerificationError("native runtime binary lacks required Narwhal title/quit markers")


def verify_desktop_version(desktop_text: str, expected_version: str) -> None:
    """Require exactly one AppImage version line matching release identity."""
    expected_line = f"X-AppImage-Version={expected_version}"
    version_lines = [line for line in desktop_text.splitlines() if line.startswith("X-AppImage-Version=")]
    if version_lines != [expected_line]:
        raise VerificationError(
            f"AppDir desktop version must be exactly one {expected_line!r} line; got {version_lines}"
        )


def verify_appdir_shape(appdir: Path, expected_version: str) -> None:
    app_run = appdir / "AppRun"
    desktop = appdir / "narwhal3d.desktop"
    icon = appdir / "narwhal3d.png"
    dir_icon = appdir / ".DirIcon"
    if not app_run.is_file() or app_run.is_symlink() or not os.access(app_run, os.X_OK):
        raise VerificationError("AppDir root AppRun is missing, symlinked, or non-executable")
    app_run_text = app_run.read_text(encoding="utf-8")
    if "NARWHAL3D_APPIMAGE=1" not in app_run_text or "usr/lib/narwhal3d/narwhal3d.sh" not in app_run_text:
        raise VerificationError("AppRun does not identify AppImage mode and launch the internal application")
    if not desktop.is_file() or desktop.is_symlink():
        raise VerificationError("AppDir root narwhal3d.desktop is missing or symlinked")
    desktop_text = desktop.read_text(encoding="utf-8")
    for required in ("Name=Narwhal 3D", "Exec=AppRun %F", "Icon=narwhal3d", "Type=Application"):
        if required not in desktop_text:
            raise VerificationError(f"AppDir desktop entry lacks {required!r}")
    verify_desktop_version(desktop_text, expected_version)
    if not icon.is_file() or icon.is_symlink():
        raise VerificationError("AppDir root icon is missing or symlinked")
    if not dir_icon.is_symlink() or os.readlink(dir_icon) != "narwhal3d.png":
        raise VerificationError("AppDir .DirIcon must be a relative link to narwhal3d.png")
    package = appdir / APP_ROOT_RELATIVE / "narwhal3d"
    if not package.is_dir() or package.is_symlink():
        raise VerificationError("internal narwhal3d application package is missing or symlinked")


def verify_notice_payload(actual: dict[str, JsonObject]) -> None:
    """Require regular project notices and the complete mandatory license set."""
    for path in sorted(REQUIRED_NOTICE_PATHS):
        expected_type = "directory" if path.endswith("/licenses") else "file"
        entry = actual.get(path)
        if entry is None or entry.get("type") != expected_type:
            raise VerificationError(f"required notice path is missing or not a regular {expected_type}: {path}")


def verify_executables_and_imports(appdir: Path) -> None:
    app_root = appdir / APP_ROOT_RELATIVE
    runtime = app_root / RUNTIME_RELATIVE
    blender = runtime / "blender"
    python = runtime / "5.2/python/bin/python3.13"
    launcher = app_root / "narwhal3d.sh"
    native_dir = app_root / NATIVE_COMPONENT_RELATIVE
    cdr_library = native_dir / "libnarwhal_cdr_core.so"
    supervisor = native_dir / "narwhal-converter-supervisor"
    multiversion = native_dir / "narwhal-cdr-multiversion"
    document_core = native_dir / "narwhal-document-core"
    pure_core = app_root / "narwhal3d/native/libnarwhal_pure_core.so"
    selection_core = app_root / "narwhal3d/native/libnarwhal_selection_core.so"
    for library_path in (cdr_library, pure_core, selection_core):
        if not library_path.is_file() or library_path.is_symlink():
            raise VerificationError(f"native library missing or symlinked: {library_path}")
    for path, description in ((launcher, "launcher"), (blender, "Blender"),
                              (python, "bundled Python"),
                              (supervisor, "native converter supervisor"),
                              (multiversion, "bundled multi-version CDR reader"),
                              (document_core, "bundled Narwhal document core")):
        if not path.is_file() or path.is_symlink() or not os.access(path, os.X_OK):
            raise VerificationError(f"{description} is missing, symlinked, or non-executable: {path}")

    try:
        supervised = subprocess.run(
            [str(supervisor), "--timeout-ms", "5000", "--max-output", "4096",
             "--memory-mib", "64", "--", "/bin/true"],
            text=True, capture_output=True, timeout=15, check=True)
        supervisor_packet = json.loads(supervised.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise VerificationError(
            f"packaged converter supervisor lacks enforced memory-limit support: {exc}") from exc
    if (not isinstance(supervisor_packet, dict)
            or supervisor_packet.get("returncode") != 0
            or supervisor_packet.get("timed_out") is not False
            or supervisor_packet.get("error") is not None):
        raise VerificationError(
            f"packaged converter supervisor memory probe failed: {supervisor_packet!r}")

    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(
            [str(blender), "--version"],
            cwd=app_root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError(f"cannot execute bundled Blender: {exc}") from exc
    first_line = completed.stdout.splitlines()[0] if completed.stdout.splitlines() else ""
    if not first_line.startswith("Narwhal 3D 5.2.2"):
        raise VerificationError(f"unexpected native Narwhal runtime identity: {first_line!r}")
    marker = read_json(runtime / ".narwhal3d-native-runtime.json", "native runtime provenance marker")
    if marker.get("version_first_line") != first_line:
        raise VerificationError("executed native version banner differs from provenance")
    try:
        library = ctypes.CDLL(str(cdr_library))
        library.narwhal_cdr_abi_version.argtypes = []
        library.narwhal_cdr_abi_version.restype = ctypes.c_uint32
        cdr_abi = int(library.narwhal_cdr_abi_version())
    except (OSError, AttributeError, ValueError) as exc:
        raise VerificationError(f"cannot load packaged native CDR library: {exc}") from exc
    if cdr_abi != 1:
        raise VerificationError(f"packaged native CDR ABI must be 1, got {cdr_abi}")
    for library_path, symbol in (
        (pure_core, "narwhal_pure_core_abi_version"),
        (selection_core, "narwhal_selection_core_abi_version"),
    ):
        try:
            library = ctypes.CDLL(str(library_path))
            version = getattr(library, symbol)
            version.argtypes = []
            version.restype = ctypes.c_uint32
            actual = int(version())
        except (OSError, AttributeError, ValueError) as exc:
            raise VerificationError(f"cannot load packaged accelerator {library_path.name}: {exc}") from exc
        if actual != 1:
            raise VerificationError(f"packaged accelerator {library_path.name} ABI is {actual}")
    try:
        completed = subprocess.run(
            [str(multiversion), "--version"], text=True, capture_output=True,
            timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError(f"cannot execute packaged multi-version CDR reader: {exc}") from exc
    if completed.stdout.strip() != CDR_MULTIVERSION_PROTOCOL:
        raise VerificationError("packaged multi-version CDR reader has the wrong protocol")
    try:
        completed = subprocess.run(
            [str(document_core), "--version"], text=True, capture_output=True,
            timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError(f"cannot execute packaged Narwhal document core: {exc}") from exc
    if completed.stdout.strip() != DOCUMENT_CORE_PROTOCOL:
        raise VerificationError("packaged Narwhal document core has the wrong protocol")
    with tempfile.TemporaryDirectory(prefix="narwhal-document-core-release-") as temporary:
        root = Path(temporary)
        staging = root / "staging"
        staging.mkdir()
        (staging / "manifest.json").write_text("{}\n", encoding="utf-8")
        (staging / "document.3dm").write_bytes(b"release-probe")
        packed = root / "probe.narwhal"
        unpacked = root / "unpacked"
        inspect_text = ""
        for arguments in (("pack", str(staging), str(packed)),
                          ("inspect", str(packed)),
                          ("unpack", str(packed), str(unpacked))):
            try:
                result = subprocess.run(
                    [str(document_core), *arguments], text=True, capture_output=True,
                    timeout=30, check=True)
            except (OSError, subprocess.SubprocessError) as exc:
                raise VerificationError(
                    f"packaged Narwhal document core failed {arguments[0]}: {exc}") from exc
            if arguments[0] == "inspect":
                inspect_text = result.stdout
        if ((unpacked / "manifest.json").read_text(encoding="utf-8") != "{}\n"
                or (unpacked / "document.3dm").read_bytes() != b"release-probe"
                or "document.3dm" not in inspect_text):
            raise VerificationError("packaged Narwhal document core failed its round trip")

    probe = r'''
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
            [str(python), "-I", "-c", probe],
            cwd=app_root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "")
        raise VerificationError(f"bundled dependency import probe failed: {detail or exc}") from exc
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise VerificationError(f"dependency probe returned invalid output: {completed.stdout!r}") from exc
    expected = {"python": [3, 13], **DISTRIBUTIONS}
    if result != expected:
        raise VerificationError(f"dependency identity mismatch: expected {expected}, got {result}")


def verify_appdir(appdir: Path, args: argparse.Namespace) -> None:
    if not appdir.is_dir() or appdir.is_symlink():
        raise VerificationError(f"AppDir must be a real directory: {appdir}")
    verify_appdir_shape(appdir, args.expected_version)
    manifest_path = appdir / MANIFEST_RELATIVE
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise VerificationError("release manifest is missing or is a symlink")
    manifest = read_json(manifest_path, "release manifest")
    identity = verify_identity(manifest, args)
    native_components = manifest.get("native_components")
    if not isinstance(native_components, dict) or native_components.get("required_glibc") is None:
        raise VerificationError("release manifest lacks native component provenance")
    supervisor_record = native_components.get("narwhal-converter-supervisor")
    if (not isinstance(supervisor_record, dict)
            or supervisor_record.get("memory_limit_probe_mib") != 64):
        raise VerificationError("release manifest lacks converter-supervisor memory-limit evidence")
    expected = expected_inventory(manifest)
    actual = actual_inventory(appdir)
    missing_required = REQUIRED_PATHS - set(actual)
    if missing_required:
        raise VerificationError(f"required AppDir paths are missing: {', '.join(sorted(missing_required))}")
    verify_notice_payload(actual)
    if set(expected) != set(actual):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise VerificationError(f"inventory path mismatch; missing={missing}, extra={extra}")
    for path in sorted(expected):
        if expected[path] != actual[path]:
            raise VerificationError(f"inventory metadata/hash mismatch: {path}")
    verify_lock_and_markers(appdir, identity)
    verify_executables_and_imports(appdir)


def verify_appimage_checksum(appimage: Path) -> None:
    checksum_path = Path(f"{appimage}.sha256")
    if not checksum_path.is_file() or checksum_path.is_symlink():
        raise VerificationError(f"AppImage checksum sidecar is missing: {checksum_path}")
    fields = checksum_path.read_text(encoding="ascii").strip().split()
    if len(fields) != 2 or fields[1] != appimage.name or not SHA256_RE.fullmatch(fields[0]):
        raise VerificationError("AppImage checksum sidecar has an invalid format or filename")
    actual = sha256_file(appimage)
    if fields[0] != actual:
        raise VerificationError(f"AppImage SHA-256 mismatch: expected {fields[0]}, got {actual}")


def extract_appimage(appimage: Path, destination: Path) -> Path:
    """Extract an AppImage through its runtime without requiring FUSE."""
    if not appimage.is_file() or appimage.is_symlink() or not os.access(appimage, os.X_OK):
        raise VerificationError(f"AppImage is missing, symlinked, or non-executable: {appimage}")
    environment = os.environ.copy()
    environment.update({"APPIMAGE_EXTRACT_AND_RUN": "1", "LC_ALL": "C", "TZ": "UTC"})
    try:
        completed = subprocess.run(
            [str(appimage), "--appimage-extract"],
            cwd=destination,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stdout", "")
        raise VerificationError(f"AppImage extraction failed: {detail or exc}") from exc
    extracted = destination / "squashfs-root"
    if not extracted.is_dir() or extracted.is_symlink():
        raise VerificationError("AppImage extraction did not create squashfs-root")
    return extracted


def verify_release(release: Path, args: argparse.Namespace) -> None:
    if release.is_dir() and not release.is_symlink():
        expected_name = f"Narwhal_3D-{args.expected_version}-x86_64.AppDir"
        if release.name != expected_name:
            raise VerificationError(f"unexpected AppDir filename: expected {expected_name}, got {release.name}")
        verify_appdir(release, args)
        return
    expected_name = f"Narwhal_3D-{args.expected_version}-x86_64.AppImage"
    if release.name != expected_name:
        raise VerificationError(f"unexpected AppImage filename: expected {expected_name}, got {release.name}")
    verify_appimage_checksum(release)
    with tempfile.TemporaryDirectory(prefix="narwhal3d-appimage-verify-") as temporary:
        extracted = extract_appimage(release, Path(temporary))
        verify_appdir(extracted, args)


def self_check() -> None:
    for preview in ("0.4.0", "0.4.9-alpha.1"):
        validate_preview_version(preview)
    for forbidden in ("0.3.9", "0.5.0", "1.0.0"):
        try:
            validate_preview_version(forbidden)
        except ValueError:
            pass
        else:
            raise VerificationError(f"self-check accepted release outside 0.4.x: {forbidden}")
    if max(glibc_versions("GLIBC_2.35 GLIBC_2.43")) != (2, 43) \
            or MAX_HOST_GLIBC != (2, 35):
        raise VerificationError("self-check lost the Ubuntu 22.04 host-glibc gate")
    verify_desktop_version("[Desktop Entry]\nX-AppImage-Version=0.1.0\n", "0.1.0")
    for mutated_desktop in (
        "[Desktop Entry]\n",
        "[Desktop Entry]\nX-AppImage-Version=0.1.1\n",
        "[Desktop Entry]\nX-AppImage-Version=0.1.0\nX-AppImage-Version=0.1.0\n",
    ):
        try:
            verify_desktop_version(mutated_desktop, "0.1.0")
        except VerificationError:
            pass
        else:
            raise VerificationError("self-check accepted mutated AppImage desktop version")
    for unsafe in ("../escape", "/absolute", "vendor/private", "dir\\child"):
        try:
            checked_relative(unsafe)
        except VerificationError:
            pass
        else:
            raise VerificationError(f"self-check accepted unsafe path: {unsafe!r}")
    expected_excluded_files = {
        PurePosixPath("usr/lib/narwhal3d/narwhal3d/kernel/brep_writer.cpp"),
    }
    if EXCLUDED_APP_FILES != expected_excluded_files:
        raise VerificationError("self-check found an incomplete excluded application payload set")
    for included in ("cdr_reader.py", "cdr_writer.py"):
        path = APP_ROOT_RELATIVE / "narwhal3d" / included
        if is_excluded_app_payload(PurePosixPath(path.as_posix())):
            raise VerificationError(f"self-check still excludes public CDR source: {included}")
    if NATIVE_COMPONENTS != (
        "libnarwhal_cdr_core.so", "libnarwhal_pure_core.so",
        "libnarwhal_selection_core.so", "narwhal-converter-supervisor",
        "narwhal-cdr-multiversion", "narwhal-document-core",
    ):
        raise VerificationError("self-check found an incomplete native component set")
    for excluded in (*expected_excluded_files, EXCLUDED_APP_TREE / "helper"):
        if not is_excluded_app_payload(excluded):
            raise VerificationError(f"self-check accepted excluded application payload: {excluded}")
        try:
            checked_relative(excluded.as_posix())
        except VerificationError:
            pass
        else:
            raise VerificationError(f"self-check allowed excluded manifest path: {excluded}")
    if is_excluded_app_payload(EXCLUDED_APP_TREE):
        raise VerificationError("self-check rejected an empty kernel/bin directory rather than its payload")
    expected_artifacts = {
        "appimagetool",
        "appimage-runtime",
        "blender-runtime",
        "rhino3dm",
        "cadquery-ocp-novtk",
        "cadquery-ocp-proxy",
    }
    if REQUIRED_ARTIFACT_IDS != expected_artifacts:
        raise VerificationError("self-check found an incomplete required artifact set")
    if (
        APPIMAGE_RUNTIME_VERSION != "20251108"
        or APPIMAGE_RUNTIME_SHA256 != "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"
    ):
        raise VerificationError("self-check found the wrong AppImage type2 runtime identity")
    if RUNTIME_FLAVOR != "native-narwhal-runtime" or RUNTIME_RELATIVE != Path("tools/blender-patched"):
        raise VerificationError("self-check found a non-native release runtime")
    if BLENDER_SOURCE_COMMIT != "d13f752e3b9c4f8c261cda552b1021f8bcc0382c":
        raise VerificationError("self-check found the wrong native Blender commit")
    if RUNTIME_BUILD_EPOCH_KEY != "build_source_date_epoch":
        raise VerificationError("self-check found an ambiguous native build epoch key")
    if validate_optional_epoch(None, "self-check epoch") is not None:
        raise VerificationError("self-check changed null native build epoch")
    if validate_optional_epoch(17, "self-check epoch") != 17:
        raise VerificationError("self-check changed valid native build epoch")
    for invalid_epoch in (-1, False, "17"):
        try:
            validate_optional_epoch(invalid_epoch, "self-check epoch")
        except VerificationError:
            pass
        else:
            raise VerificationError(f"self-check accepted invalid native build epoch: {invalid_epoch!r}")
    expected_notices = {
        "usr/lib/narwhal3d/LICENSE",
        "usr/lib/narwhal3d/THIRD_PARTY_NOTICES",
        "usr/lib/narwhal3d/licenses",
        "usr/lib/narwhal3d/licenses/GPL-3.0-or-later.txt",
        "usr/lib/narwhal3d/licenses/rhino3dm-MIT.txt",
        "usr/lib/narwhal3d/licenses/OCP-Apache-2.0.txt",
        "usr/lib/narwhal3d/licenses/LGPL-2.1-or-later.txt",
        "usr/lib/narwhal3d/licenses/OCCT-exception-1.0.txt",
        "usr/lib/narwhal3d/licenses/AppImage-MIT.txt",
        "usr/lib/narwhal3d/licenses/TraceDraw-MIT.txt",
        "usr/lib/narwhal3d/licenses/TraceDraw-Rust-Dependencies.txt",
    }
    if REQUIRED_NOTICE_PATHS != expected_notices or not expected_notices.issubset(REQUIRED_PATHS):
        raise VerificationError("self-check found an incomplete required notice set")
    notice_inventory = {
        path: {"type": "directory" if path.endswith("/licenses") else "file"}
        for path in REQUIRED_NOTICE_PATHS
    }
    verify_notice_payload(notice_inventory)
    del notice_inventory["usr/lib/narwhal3d/licenses/OCCT-exception-1.0.txt"]
    try:
        verify_notice_payload(notice_inventory)
    except VerificationError:
        pass
    else:
        raise VerificationError("self-check accepted an incomplete notice inventory")
    if not MANIFEST_RELATIVE.is_relative_to(PurePosixPath("usr/lib/narwhal3d")):
        raise VerificationError("self-check found manifest outside internal app root")
    print("verify_linux_release.py self-check: OK")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a Narwhal 3D Linux x86_64 AppDir or final AppImage."
    )
    parser.add_argument("release", nargs="?", type=Path, help="AppDir or AppImage to verify")
    parser.add_argument("--expected-version", help="exact expected public release version")
    parser.add_argument("--expected-source-revision", help="exact expected 40-hex source revision")
    parser.add_argument("--expected-source-date-epoch", type=int, help="optional exact source timestamp")
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
                ("release", args.release),
                ("--expected-version", args.expected_version),
                ("--expected-source-revision", args.expected_source_revision),
            )
            if value is None
        ]
        if missing:
            raise VerificationError(f"required arguments missing: {', '.join(missing)}")
        try:
            validate_preview_version(args.expected_version)
        except ValueError as exc:
            raise VerificationError(str(exc)) from exc
        if not REVISION_RE.fullmatch(args.expected_source_revision.lower()):
            raise VerificationError("--expected-source-revision must be a full 40-hex Git revision")
        if args.expected_source_date_epoch is not None and args.expected_source_date_epoch < 0:
            raise VerificationError("--expected-source-date-epoch must be non-negative")
        verify_release(args.release.resolve(), args)
        print(f"verified Narwhal 3D {args.expected_version} Linux x86_64 release: {args.release}")
        return 0
    except (VerificationError, OSError) as exc:
        print(f"verify_linux_release.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
