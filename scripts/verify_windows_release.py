#!/usr/bin/env python3
"""Verify a Narwhal 3D Windows x86_64 prerelease directory or portable ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import zipfile

from release_policy import validate_preview_version

PRODUCT = "Narwhal 3D"
PLATFORM = "windows-x86_64"
FORMAT = "portable-zip"
FORBIDDEN_PARTS = {"bug_" + "reports", "More" + "_Data", "Example" + "Photos", ".git", "artifacts"}
FORBIDDEN_FILES = {"brep_" + "writer.cpp"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".pdb", ".lib", ".exp"}
FORBIDDEN_RUNTIME_TOOLS = {"datatoc.exe", "makesdna.exe", "makesrna.exe"}
REQUIRED = {
    "Narwhal3D.exe", "Narwhal3D.cmd", "README-FIRST.txt",
    "runtime/blender.exe", "runtime/5.2/python/bin/python.exe",
    "app/narwhal3d/__init__.py", "app/narwhal3d/cdr_reader.py",
    "app/narwhal3d/cdr_writer.py", "app/launch/bootstrap.py",
    "app/LICENSE", "app/THIRD_PARTY_NOTICES", "app/STATUS.md", "app/CHANGELOG.md",
    "app/licenses/GPL-3.0-or-later.txt", "app/licenses/rhino3dm-MIT.txt",
    "app/licenses/OCP-Apache-2.0.txt", "app/licenses/LGPL-2.1-or-later.txt",
    "app/licenses/OCCT-exception-1.0.txt", "app/licenses/TraceDraw-MIT.txt",
    "app/licenses/TraceDraw-Rust-Dependencies.txt", "windows-release-manifest.json",
    "app/runtime/windows-x86_64/bin/narwhal_cdr_core.dll",
    "app/runtime/windows-x86_64/bin/narwhal-converter-supervisor.exe",
    "app/runtime/windows-x86_64/bin/narwhal-cdr-multiversion.exe",
}
SOURCE_ROOTS = ("narwhal3d", "launch", "licenses")
SOURCE_FILES = ("LICENSE", "THIRD_PARTY_NOTICES", "CHANGELOG.md", "STATUS.md")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
CDR_MULTIVERSION_PROTOCOL = "narwhal-cdr-multiversion 1 tracedraw 821ba154 read 16-18 write 17"


class VerificationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def inventory(root: Path) -> list[dict[str, object]]:
    out = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise VerificationError(f"Windows package contains a symlink: {path.relative_to(root)}")
        if path.is_file() and path.name != "windows-release-manifest.json":
            out.append({
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            })
    return out


def validate_paths(root: Path, actual: list[dict[str, object]]) -> None:
    paths = {str(item["path"]) for item in actual}
    missing = sorted(REQUIRED - paths - {"windows-release-manifest.json"})
    if missing:
        raise VerificationError("Windows package is missing: " + ", ".join(missing))
    if not (root / "windows-release-manifest.json").is_file():
        raise VerificationError("Windows release manifest is missing")
    for shown in sorted(paths):
        path = PurePosixPath(shown)
        if set(path.parts) & FORBIDDEN_PARTS:
            raise VerificationError(f"private/build payload is forbidden: {shown}")
        if path.name in FORBIDDEN_FILES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise VerificationError(f"cache/debug/private file is forbidden: {shown}")
        if "__pycache__" in path.parts or path.name in FORBIDDEN_RUNTIME_TOOLS:
            raise VerificationError(f"cache/build tool is forbidden: {shown}")
        if any(part == "kernel" for part in path.parts) and "bin" in path.parts:
            raise VerificationError(f"private kernel helper tree is forbidden: {shown}")


def verify_public_source(root: Path, revision: str) -> None:
    paths: list[str] = []
    for directory in SOURCE_ROOTS:
        paths.extend(
            path.relative_to(root / "app").as_posix()
            for path in (root / "app" / directory).rglob("*")
            if path.is_file()
        )
    paths.extend(SOURCE_FILES)
    for relative in paths:
        package_path = root / "app" / relative
        try:
            expected = subprocess.run(
                ["git", "show", f"{revision}:{relative}"], check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise VerificationError(f"cannot read public source {revision}:{relative}") from exc
        if normalized(package_path.read_bytes()) != normalized(expected):
            raise VerificationError(f"packaged application differs from public source: {relative}")


def require_bytes(path: Path, markers: tuple[bytes, ...], description: str) -> None:
    data = path.read_bytes()
    if not data.startswith(b"MZ") or any(marker not in data for marker in markers):
        raise VerificationError(f"{description} has wrong PE identity/content: {path.name}")


def metadata_version(site: Path, distribution: str) -> str | None:
    wanted = distribution.lower().replace("_", "-")
    for metadata in site.glob("*.dist-info/METADATA"):
        fields = {}
        for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                if key in {"Name", "Version"} and key not in fields:
                    fields[key] = value
        if fields.get("Name", "").lower().replace("_", "-") == wanted:
            return fields.get("Version")
    return None


def verify_directory(root: Path, args: argparse.Namespace) -> None:
    manifest_path = root / "windows-release-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read Windows release manifest: {exc}") from exc
    expected_identity = {
        "schema": 1, "product": PRODUCT, "version": args.expected_version,
        "prerelease": True, "platform": PLATFORM, "package_format": FORMAT,
        "narwhal_source_revision": args.expected_source_revision,
    }
    for key, value in expected_identity.items():
        if manifest.get(key) != value:
            raise VerificationError(f"manifest {key}: expected {value!r}, got {manifest.get(key)!r}")
    if manifest.get("runtime_source_revision") != "d13f752e3b9c4f8c261cda552b1021f8bcc0382c":
        raise VerificationError("Windows runtime is not the pinned Blender source revision")
    actual = inventory(root)
    validate_paths(root, actual)
    if manifest.get("inventory") != actual:
        raise VerificationError("Windows package inventory/hash manifest does not match")
    native_components = manifest.get("native_components")
    if not isinstance(native_components, dict) or set(native_components) != {
        "narwhal_cdr_core.dll", "narwhal-converter-supervisor.exe",
        "narwhal-cdr-multiversion.exe"
    }:
        raise VerificationError("Windows manifest lacks the exact native component set")
    for field in ("runtime_binary_sha256", "launcher_sha256", "launcher_source_sha256"):
        if not isinstance(manifest.get(field), str) or not SHA_RE.fullmatch(manifest[field]):
            raise VerificationError(f"manifest has invalid {field}")
    if sha256(root / "runtime/blender.exe") != manifest["runtime_binary_sha256"]:
        raise VerificationError("runtime executable hash does not match manifest")
    if sha256(root / "Narwhal3D.exe") != manifest["launcher_sha256"]:
        raise VerificationError("launcher hash does not match manifest")
    launcher_source = Path(__file__).resolve().parents[1] / "packaging/windows/narwhal3d_launcher.c"
    if sha256(launcher_source) != manifest["launcher_source_sha256"]:
        raise VerificationError("launcher source hash does not match public source")
    require_bytes(
        root / "Narwhal3D.exe",
        ("Narwhal 3D".encode("utf-16le"), "Narwhal3D.exe".encode("utf-16le")),
        "Narwhal launcher")
    for filename in ("narwhal_cdr_core.dll", "narwhal-converter-supervisor.exe",
                     "narwhal-cdr-multiversion.exe"):
        path = root / "app/runtime/windows-x86_64/bin" / filename
        record = native_components[filename]
        data = path.read_bytes()
        if (not isinstance(record, dict) or record.get("sha256") != sha256(path)
                or record.get("size") != path.stat().st_size or not data.startswith(b"MZ")):
            raise VerificationError(f"invalid packaged native component: {filename}")
        if filename == "narwhal-cdr-multiversion.exe" and (
                record.get("protocol") != CDR_MULTIVERSION_PROTOCOL
                or CDR_MULTIVERSION_PROTOCOL.encode("ascii") not in data):
            raise VerificationError("Windows multi-version CDR helper has the wrong protocol")
    require_bytes(
        root / "runtime/blender.exe",
        (b"Narwhal 3D quit", b" - Narwhal 3D"),
        "native runtime")
    site = root / "runtime/5.2/python/lib/site-packages"
    expected_distributions = {
        "rhino3dm": "8.35.0",
        "cadquery-ocp-novtk": "8.0.1.0.0",
        "cadquery-ocp-proxy": "8.0.1.0.0",
    }
    for name, version in expected_distributions.items():
        if metadata_version(site, name) != version:
            raise VerificationError(f"wrong or missing bundled distribution: {name} {version}")
    validation = manifest.get("validation")
    if not isinstance(validation, dict) or validation.get("windows_packaged_headless_bootstrap") is not True:
        raise VerificationError("Windows packaged headless-bootstrap evidence is absent")
    if validation.get("physical_gpu_real_window") is not False or validation.get("code_signed") is not False:
        raise VerificationError("Windows preview overclaims physical-GPU or signing status")
    verify_public_source(root, args.expected_source_revision)


def safe_zip_members(archive: zipfile.ZipFile, expected_root: str) -> None:
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != expected_root:
            raise VerificationError(f"unsafe/unexpected ZIP path: {info.filename}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise VerificationError(f"ZIP contains a symlink: {info.filename}")
    bad = archive.testzip()
    if bad:
        raise VerificationError(f"ZIP CRC failed: {bad}")


def verify_zip(path: Path, args: argparse.Namespace) -> None:
    expected_name = f"Narwhal_3D-{args.expected_version}-Windows-x86_64.zip"
    if path.name != expected_name:
        raise VerificationError(f"expected ZIP name {expected_name}, got {path.name}")
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file():
        raise VerificationError("Windows ZIP checksum sidecar is missing")
    expected_line = f"{sha256(path)}  {path.name}"
    if sidecar.read_text(encoding="ascii").strip() != expected_line:
        raise VerificationError("Windows ZIP checksum sidecar does not match")
    root_name = path.name.removesuffix(".zip")
    with zipfile.ZipFile(path) as archive:
        safe_zip_members(archive, root_name)
        with tempfile.TemporaryDirectory(prefix="narwhal3d-windows-verify-") as temporary:
            archive.extractall(temporary)
            verify_directory(Path(temporary) / root_name, args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-source-revision", required=True)
    args = parser.parse_args()
    try:
        validate_preview_version(args.expected_version)
        if args.release.is_dir():
            verify_directory(args.release.resolve(), args)
        else:
            verify_zip(args.release.resolve(), args)
        print(f"verified Narwhal 3D {args.expected_version} Windows x86_64 prerelease")
        return 0
    except (ValueError, VerificationError, OSError, zipfile.BadZipFile) as exc:
        print(f"verify_windows_release.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
