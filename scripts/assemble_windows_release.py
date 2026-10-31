#!/usr/bin/env python3
"""Assemble a verified Narwhal 3D Windows portable prerelease."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
import zipfile

from release_policy import validate_preview_version
import verify_windows_release as verifier
from verify_document_core import source_aggregate

RUNTIME_REVISION = "d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
SOURCE_DIRECTORIES = ("narwhal3d", "launch", "licenses")
SOURCE_FILES = ("LICENSE", "THIRD_PARTY_NOTICES", "CHANGELOG.md", "STATUS.md")
REMOVE_SUFFIXES = {".pyc", ".pyo", ".pdb", ".lib", ".exp"}
REMOVE_NAMES = {"datatoc.exe", "makesdna.exe", "makesrna.exe"}


class AssemblyError(RuntimeError):
    pass


def require_file(path: Path, label: str) -> Path:
    if not path.is_file() or path.is_symlink():
        raise AssemblyError(f"missing regular {label}: {path}")
    return path


def validate_launcher_version(path: Path, version: str) -> None:
    data = require_file(path, "Windows launcher").read_bytes()
    markers = (
        "NARWHAL3D_VERSION".encode("utf-16le"),
        "NARWHAL3D_PACKAGE_PATH".encode("utf-16le"),
        version.encode("utf-16le"),
    )
    if not data.startswith(b"MZ") or any(marker not in data for marker in markers):
        raise AssemblyError(f"Windows launcher is not compiled for release {version}")


def safe_extract(archive_path: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        roots = set()
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise AssemblyError(f"unsafe engineering ZIP path: {info.filename}")
            if ((info.external_attr >> 16) & 0o170000) == stat.S_IFLNK:
                raise AssemblyError(f"engineering ZIP contains a symlink: {info.filename}")
            roots.add(path.parts[0])
        if len(roots) != 1 or archive.testzip() is not None:
            raise AssemblyError("engineering ZIP must have one CRC-valid root")
        archive.extractall(destination)
    return destination / roots.pop()


def replace_public_source(package: Path, source_root: Path) -> None:
    app = package / "app"
    for name in SOURCE_DIRECTORIES:
        source = source_root / name
        require_file(source / "__init__.py", name) if name == "narwhal3d" else None
        target = app / name
        generated: dict[str, bytes] = {}
        if name == "narwhal3d":
            for filename in verifier.GENERATED_PACKAGE_FILES:
                path = app / filename
                if path.is_file() and not path.is_symlink():
                    generated[Path(filename).name] = path.read_bytes()
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=False)
        if generated:
            native = target / "native"
            native.mkdir(parents=True, exist_ok=True)
            for filename, data in generated.items():
                (native / filename).write_bytes(data)
    for name in SOURCE_FILES:
        shutil.copy2(require_file(source_root / name, name), app / name)
    shutil.copy2(require_file(source_root / "native/application_core/OPENNURBS_LICENSE",
                              "application-core openNURBS license"),
                 app / verifier.APPLICATION_CORE_LICENSE)


def remove_forbidden_payload(package: Path) -> None:
    private_kernel = package / "app/narwhal3d/kernel/bin"
    if private_kernel.exists():
        shutil.rmtree(private_kernel)
    private_source = package / "app/narwhal3d/kernel/brep_writer.cpp"
    private_source.unlink(missing_ok=True)
    for path in sorted(package.rglob("*"), reverse=True):
        if path.is_symlink():
            raise AssemblyError(f"package contains a symlink: {path.relative_to(package)}")
        if path.is_file() and (path.suffix.lower() in REMOVE_SUFFIXES or path.name in REMOVE_NAMES):
            path.unlink()
        elif path.is_dir() and path.name == "__pycache__":
            shutil.rmtree(path)


def copy_native_inputs(package: Path, launcher: Path, cdr_core: Path,
                       cdr_multiversion: Path, welcome: Path, version: str) -> None:
    validate_launcher_version(launcher, version)
    shutil.copy2(launcher, package / "Narwhal3D.exe")
    runtime_native = package / "app/runtime/windows-x86_64/bin"
    runtime_native.mkdir(parents=True, exist_ok=True)
    shutil.copy2(require_file(cdr_core, "Windows CDR core"),
                 runtime_native / "narwhal_cdr_core.dll")
    shutil.copy2(require_file(cdr_multiversion, "Windows CDR multiversion helper"),
                 runtime_native / "narwhal-cdr-multiversion.exe")
    shutil.copy2(require_file(welcome, "Windows native welcome"),
                 runtime_native / "narwhal-welcome.exe")


def native_records(package: Path, source_root: Path | None = None) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    verifier.verify_application_core_copies(package)
    verifier.verify_application_core_source(package, source_root or Path(__file__).resolve().parents[1])
    for name, relative in verifier.NATIVE_COMPONENT_PATHS.items():
        path = require_file(package / relative, f"native component {name}")
        record: dict[str, object] = {"size": path.stat().st_size, "sha256": verifier.sha256(path)}
        if name == "narwhal-welcome.exe":
            data = path.read_bytes()
            markers = (b"--runtime is required", b"--bootstrap is required",
                       b"NARWHAL3D_WELCOME_REQUEST", b"NARWHAL3D_VERSION",
                       b"releases?per_page=20", b"download sha256 does not match",
                       b"Update ZIP expands beyond the safe limit")
            if not all(marker in data for marker in markers):
                raise AssemblyError("Windows native welcome lacks its launch/request contract")
            record["launch_contract"] = 1
        elif name == "narwhal_application_core.dll":
            identity = verifier.verify_application_core_identity(path)
            record.update({"abi": verifier.APPLICATION_CORE_ABI,
                           "machine": identity["machine"], "exports": list(identity["exports"])})
        elif name == "narwhal_automation.dll":
            identity = verifier.verify_automation_core_identity(path)
            record.update({"machine": identity["machine"], "exports": list(identity["exports"])})
        elif name == "narwhal_geometry_core.dll":
            identity = verifier.verify_geometry_core_identity(path)
            record.update({"abi": verifier.GEOMETRY_CORE_ABI,
                           "machine": identity["machine"],
                           "exports": list(identity["exports"]),
                           "dependencies": list(identity["dependencies"]),
                           "real_bintools_move": "airtight"})
        elif name == "narwhal_platform_core.dll":
            identity = verifier.verify_platform_core_identity(path)
            record.update({"abi": verifier.PLATFORM_CORE_ABI,
                           "machine": identity["machine"],
                           "exports": list(identity["exports"]),
                           "dependencies": list(identity["dependencies"])})
        elif name == "narwhal-cdr-multiversion.exe":
            record["protocol"] = verifier.CDR_MULTIVERSION_PROTOCOL
        elif name == "narwhal-converter-supervisor.exe":
            record["memory_limit_probe_mib"] = 64
        elif name == "narwhal-document-core.exe":
            record["protocol"] = verifier.DOCUMENT_CORE_PROTOCOL
            record["source_aggregate_sha256"] = source_aggregate()
        records[name] = record
    return records


EXCLUDED_COMPONENTS = [
    "opennurbs-exact-save-helper",
    "optional-converters",
    "installer-file-associations",
]


def write_manifest(package: Path, source_root: Path, version: str, revision: str) -> None:
    manifest = {
        "schema": 1,
        "product": verifier.PRODUCT,
        "version": version,
        "prerelease": True,
        "platform": verifier.PLATFORM,
        "package_format": verifier.FORMAT,
        "narwhal_source_revision": revision,
        "runtime_source_revision": RUNTIME_REVISION,
        "runtime_binary_sha256": verifier.sha256(package / "runtime/blender.exe"),
        "launcher_sha256": verifier.sha256(package / "Narwhal3D.exe"),
        "launcher_source_sha256": verifier.sha256(source_root / "packaging/windows/narwhal3d_launcher.c"),
        "native_components": native_records(package, source_root),
        "excluded_components": EXCLUDED_COMPONENTS,
        "validation": {
            "windows_packaged_headless_bootstrap": True,
            "physical_gpu_real_window": False,
            "code_signed": False,
        },
        "inventory": verifier.inventory(package),
    }
    (package / "windows-release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_zip(package: Path, output: Path, epoch: int) -> None:
    timestamp = datetime.fromtimestamp(max(epoch, 315532800), timezone.utc)
    stamp = (timestamp.year, timestamp.month, timestamp.day,
             timestamp.hour, timestamp.minute, timestamp.second)
    temporary = output.with_name(output.name + ".part")
    temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, allowZip64=True) as archive:
        for path in sorted(package.rglob("*")):
            if not path.is_file():
                continue
            relative = PurePosixPath(package.name) / path.relative_to(package).as_posix()
            info = zipfile.ZipInfo(relative.as_posix(), stamp)
            info.create_system = 0
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED,
                             compresslevel=9)
    os.replace(temporary, output)
    output.with_name(output.name + ".sha256").write_text(
        f"{verifier.sha256(output)}  {output.name}\n", encoding="ascii")


def assemble(args: argparse.Namespace) -> tuple[Path, Path]:
    validate_preview_version(args.version)
    if len(args.source_revision) != 40 or any(c not in "0123456789abcdef" for c in args.source_revision):
        raise AssemblyError("source revision must be 40 lowercase hexadecimal characters")
    source_root = args.source_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"Narwhal_3D-{args.version}-Windows-x86_64"
    package = output_dir / name
    archive = output_dir / f"{name}.zip"
    sidecar = output_dir / f"{name}.zip.sha256"
    if any(path.exists() for path in (package, archive, sidecar)):
        raise AssemblyError("refusing to overwrite an existing Windows release output")
    with tempfile.TemporaryDirectory(prefix="narwhal-windows-assemble-") as temporary:
        engineering = safe_extract(require_file(args.engineering_zip.resolve(), "engineering ZIP"),
                                   Path(temporary))
        shutil.move(str(engineering), package)
    replace_public_source(package, source_root)
    copy_native_inputs(package, args.launcher.resolve(), args.cdr_core.resolve(),
                       args.cdr_multiversion.resolve(), args.welcome.resolve(), args.version)
    (package / "README-FIRST.txt").write_text(
        f"Narwhal 3D {args.version} early prerelease\n\n"
        "Extract the complete folder and run Narwhal3D.exe. Keep app and runtime beside it.\n"
        "This build is unsigned; Windows SmartScreen may warn. Physical-GPU acceptance is pending.\n",
        encoding="utf-8")
    remove_forbidden_payload(package)
    write_manifest(package, source_root, args.version, args.source_revision)
    write_zip(package, archive, args.source_date_epoch)
    return package, archive


def self_check() -> None:
    assert RUNTIME_REVISION == "d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
    assert REMOVE_SUFFIXES == {".pyc", ".pyo", ".pdb", ".lib", ".exp"}
    assert "narwhal_geometry_core.dll" in verifier.NATIVE_COMPONENT_PATHS
    assert "narwhal_platform_core.dll" in verifier.NATIVE_COMPONENT_PATHS
    assert "narwhal-welcome.exe" in verifier.NATIVE_COMPONENT_PATHS
    assert EXCLUDED_COMPONENTS == [
        "opennurbs-exact-save-helper", "optional-converters", "installer-file-associations",
    ]
    with tempfile.TemporaryDirectory(prefix="windows-assembler-self-check-") as temporary:
        root = Path(temporary)
        source = root / "source"
        package = root / "package"
        for directory in SOURCE_DIRECTORIES:
            (source / directory).mkdir(parents=True)
        (source / "narwhal3d/__init__.py").write_text("# public source\n", encoding="utf-8")
        (source / "launch/bootstrap.py").write_text("# launcher\n", encoding="utf-8")
        (source / "licenses/GPL.txt").write_text("license\n", encoding="utf-8")
        (source / "native/application_core").mkdir(parents=True)
        (source / "native/application_core/OPENNURBS_LICENSE").write_text("ordinal license\n")
        for filename in SOURCE_FILES:
            (source / filename).write_text(filename + "\n", encoding="utf-8")
        native = package / "app/narwhal3d/native"
        native.mkdir(parents=True)
        expected = {}
        for index, relative in enumerate(sorted(verifier.GENERATED_PACKAGE_FILES), 1):
            payload = bytes([index])
            (package / "app" / relative).write_bytes(payload)
            expected[Path(relative).name] = payload
        replace_public_source(package, source)
        assert {(native / name).read_bytes() for name in expected} == set(expected.values())
        assert (package / "app/narwhal3d/__init__.py").read_text() == "# public source\n"
    print("assemble_windows_release.py self-check: OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engineering-zip", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--source-date-epoch", type=int)
    parser.add_argument("--version")
    parser.add_argument("--launcher", type=Path)
    parser.add_argument("--cdr-core", type=Path)
    parser.add_argument("--cdr-multiversion", type=Path)
    parser.add_argument("--welcome", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_check:
            self_check()
            return 0
        required = (args.engineering_zip, args.source_root, args.source_revision,
                    args.source_date_epoch, args.version, args.launcher,
                    args.cdr_core, args.cdr_multiversion, args.welcome, args.output_dir)
        if any(value is None for value in required):
            raise AssemblyError("all release assembly arguments are required")
        package, archive = assemble(args)
        print(package)
        print(archive)
        return 0
    except (AssemblyError, verifier.VerificationError, OSError, ValueError,
            zipfile.BadZipFile) as exc:
        print(f"assemble_windows_release.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
