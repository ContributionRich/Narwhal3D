#!/usr/bin/env python3
"""Verify a Narwhal 3D Windows x86_64 prerelease directory or portable ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
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
NATIVE_COMPONENT_PATHS = {
    "narwhal_cdr_core.dll": "app/runtime/windows-x86_64/bin/narwhal_cdr_core.dll",
    "narwhal-converter-supervisor.exe": "app/runtime/windows-x86_64/bin/narwhal-converter-supervisor.exe",
    "narwhal-cdr-multiversion.exe": "app/runtime/windows-x86_64/bin/narwhal-cdr-multiversion.exe",
    "narwhal-document-core.exe": "app/runtime/windows-x86_64/bin/narwhal-document-core.exe",
    "narwhal-welcome.exe": "app/runtime/windows-x86_64/bin/narwhal-welcome.exe",
    "narwhal_geometry_core.dll": "app/narwhal3d/native/narwhal_geometry_core.dll",
    "narwhal_platform_core.dll": "app/narwhal3d/native/narwhal_platform_core.dll",
    "narwhal_pure_core.dll": "app/narwhal3d/native/narwhal_pure_core.dll",
    "narwhal_selection_core.dll": "app/narwhal3d/native/narwhal_selection_core.dll",
}
GEOMETRY_CORE_ABI = 4
GEOMETRY_CORE_EXPORTS = (
    "narwhal_geometry_core_abi_version",
    "narwhal_geometry_core_capabilities",
    "narwhal_geometry_core_edit_session_import",
    "narwhal_geometry_core_edit_session_move",
    "narwhal_geometry_core_edit_session_move_mode",
    "narwhal_geometry_core_edit_session_release",
    "narwhal_geometry_core_inspect_brep",
    "narwhal_geometry_core_make_box_brep",
    "narwhal_geometry_core_move_component_brep",
)
GEOMETRY_REQUIRED_OCCT_IMPORT_PREFIXES = (
    "tkbo-", "tkbrep-", "tkernel-", "tkg3d-", "tkgeomalgo-", "tkgeombase-",
    "tkmath-", "tkprim-", "tkshhealing-", "tktopalgo-",
)
GEOMETRY_ALLOWED_MSVC_EXPORT = re.compile(r"\?\?_F[A-Za-z0-9_?$@]+@@QEAAXXZ\Z")
PLATFORM_CORE_ABI = 1
PLATFORM_CORE_EXPORTS = (
    "narwhal_platform_core_abi_version",
    "narwhal_platform_dialog_result_copy_v1",
    "narwhal_platform_dialog_result_discard_v1",
    "narwhal_platform_file_dialog_v1",
    "narwhal_platform_message_box_v1",
)
PLATFORM_REQUIRED_IMPORTS = {"comdlg32.dll", "user32.dll"}
FORBIDDEN_LAUNCH_DLLS = {"shell32.dll"}
FORBIDDEN_LAUNCH_IMPORTS = {
    "createprocessa", "createprocessw", "createprocessasusera",
    "createprocessasuserw", "createprocesswithlogonw",
    "createprocesswithtokenw", "shellexecutea", "shellexecutew",
    "shellexecuteexa", "shellexecuteexw", "winexec",
}
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
    "app/runtime/windows-x86_64/bin/narwhal-document-core.exe",
    *NATIVE_COMPONENT_PATHS.values(),
}
SOURCE_ROOTS = ("narwhal3d", "launch", "licenses")
SOURCE_FILES = ("LICENSE", "THIRD_PARTY_NOTICES", "CHANGELOG.md", "STATUS.md")
GENERATED_PACKAGE_FILES = {
    "narwhal3d/native/narwhal_geometry_core.dll",
    "narwhal3d/native/narwhal_platform_core.dll",
    "narwhal3d/native/narwhal_pure_core.dll",
    "narwhal3d/native/narwhal_selection_core.dll",
}
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
CDR_MULTIVERSION_PROTOCOL = "narwhal-cdr-multiversion 1 tracedraw 821ba154 read 16-18 write 17"
DOCUMENT_CORE_PROTOCOL = "narwhal-document-core 1"


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
        if relative in GENERATED_PACKAGE_FILES:
            continue
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


def inspect_pe_dll(path: Path) -> dict[str, object]:
    """Read PE32+ DLL exports/imports without trusting a host-side PE tool."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise VerificationError(f"cannot read Windows DLL {path.name}: {exc}") from exc

    def unpack(fmt: str, offset: int):
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(data):
            raise VerificationError(f"truncated PE structure in {path.name}")
        return struct.unpack_from(fmt, data, offset)

    if len(data) < 0x40 or data[:2] != b"MZ":
        raise VerificationError(f"{path.name} lacks a complete DOS header")
    pe_offset = unpack("<I", 0x3c)[0]
    if pe_offset < 0x40 or pe_offset + 24 > len(data) or data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise VerificationError(f"{path.name} lacks a valid PE signature")
    machine, section_count, _timestamp, _symbols, _symbol_count, optional_size, characteristics = unpack(
        "<HHIIIHH", pe_offset + 4)
    optional = pe_offset + 24
    if machine != 0x8664 or unpack("<H", optional)[0] != 0x20b:
        raise VerificationError(f"{path.name} is not an x86-64 PE32+ image")
    if not characteristics & 0x0002 or not characteristics & 0x2000:
        raise VerificationError(f"{path.name} is not marked as an executable DLL")
    if optional_size < 128 or optional + optional_size > len(data) or section_count == 0:
        raise VerificationError(f"{path.name} has a truncated optional/section header")
    size_of_headers = unpack("<I", optional + 60)[0]
    directories = optional + 112
    export_rva, export_size = unpack("<II", directories)
    import_rva, import_size = unpack("<II", directories + 8)
    section_table = optional + optional_size
    sections = []
    for index in range(section_count):
        entry = section_table + index * 40
        virtual_size, virtual_address, raw_size, raw_offset = unpack("<IIII", entry + 8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_offset, raw_size))

    def rva_offset(rva: int, needed: int = 1) -> int:
        if rva < size_of_headers and rva + needed <= len(data):
            return rva
        for virtual_address, span, raw_offset, raw_size in sections:
            delta = rva - virtual_address
            if 0 <= delta and delta + needed <= raw_size and delta < span:
                offset = raw_offset + delta
                if offset + needed <= len(data):
                    return offset
        raise VerificationError(f"{path.name} contains an unmapped PE RVA 0x{rva:x}")

    def c_string(rva: int) -> str:
        offset = rva_offset(rva)
        end = data.find(b"\0", offset, min(len(data), offset + 4096))
        if end < 0:
            raise VerificationError(f"{path.name} contains an unterminated PE name")
        try:
            return data[offset:end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise VerificationError(f"{path.name} contains a non-ASCII PE name") from exc

    if not export_rva or export_size < 40:
        raise VerificationError(f"{path.name} has no PE export directory")
    export_offset = rva_offset(export_rva, 40)
    export_count = unpack("<I", export_offset + 24)[0]
    export_names_rva = unpack("<I", export_offset + 32)[0]
    if export_count > 65536:
        raise VerificationError(f"{path.name} has an unreasonable PE export count")
    exports = tuple(sorted(c_string(unpack("<I", rva_offset(export_names_rva, export_count * 4) + 4 * index)[0])
                           for index in range(export_count)))

    if not import_rva or import_size < 20:
        raise VerificationError(f"{path.name} has no PE import directory")
    imports: dict[str, tuple[str, ...]] = {}
    descriptor = rva_offset(import_rva, 20)
    for _ in range(min(import_size // 20, 4096)):
        original_thunk, timestamp, forwarder, name_rva, first_thunk = unpack("<IIIII", descriptor)
        descriptor += 20
        if not any((original_thunk, timestamp, forwarder, name_rva, first_thunk)):
            break
        dll = c_string(name_rva).lower()
        thunk_rva = original_thunk or first_thunk
        symbols = []
        for index in range(65536):
            value = unpack("<Q", rva_offset(thunk_rva + index * 8, 8))[0]
            if value == 0:
                break
            if value & (1 << 63):
                symbols.append(f"ordinal:{value & 0xffff}")
            else:
                symbols.append(c_string(value + 2))
        else:
            raise VerificationError(f"{path.name} has an unterminated PE import thunk")
        imports[dll] = tuple(sorted(symbols))
    else:
        raise VerificationError(f"{path.name} has an unterminated PE import directory")
    if not imports:
        raise VerificationError(f"{path.name} has an empty PE import directory")
    return {
        "machine": "pe32plus-x86-64",
        "exports": exports,
        "dependencies": tuple(sorted(imports)),
        "import_symbols": imports,
    }


def verify_geometry_core_identity(path: Path) -> dict[str, object]:
    identity = inspect_pe_dll(path)
    exports = identity["exports"]
    dependencies = set(identity["dependencies"])
    public_exports = tuple(name for name in exports
                           if name.startswith("narwhal_geometry_core_"))
    generated_exports = tuple(name for name in exports
                              if not name.startswith("narwhal_geometry_core_"))
    if public_exports != GEOMETRY_CORE_EXPORTS:
        raise VerificationError(f"Windows geometry-core public exports differ: {public_exports}")
    if not all(GEOMETRY_ALLOWED_MSVC_EXPORT.fullmatch(name) for name in generated_exports):
        raise VerificationError(
            f"Windows geometry-core has unexpected non-ABI exports: {generated_exports}")
    missing = [prefix for prefix in GEOMETRY_REQUIRED_OCCT_IMPORT_PREFIXES
               if not any(name.startswith(prefix) and name.endswith(".dll")
                          for name in dependencies)]
    if missing:
        raise VerificationError(
            "Windows geometry core lacks hashed pinned-OCCT imports: " + ", ".join(missing))
    unpinned = sorted(name for name in dependencies
                      if name.lower().startswith("tk") and "-" not in name)
    if unpinned:
        raise VerificationError(
            "Windows geometry core imports unpinned OCCT DLL names: " + ", ".join(unpinned))
    return identity


def verify_platform_core_identity(path: Path) -> dict[str, object]:
    identity = inspect_pe_dll(path)
    exports = identity["exports"]
    dependencies = set(identity["dependencies"])
    symbols = {symbol.lower() for names in identity["import_symbols"].values() for symbol in names}
    if exports != PLATFORM_CORE_EXPORTS:
        raise VerificationError(f"Windows platform-core exports differ: {exports}")
    missing = PLATFORM_REQUIRED_IMPORTS - dependencies
    if missing:
        raise VerificationError("Windows platform core lacks native dialog imports: " + ", ".join(sorted(missing)))
    forbidden_dlls = dependencies & FORBIDDEN_LAUNCH_DLLS
    forbidden_symbols = symbols & FORBIDDEN_LAUNCH_IMPORTS
    if forbidden_dlls or forbidden_symbols:
        found = sorted(forbidden_dlls | forbidden_symbols)
        raise VerificationError("Windows platform core imports forbidden shell/process launch API: " + ", ".join(found))
    return identity


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
    if not isinstance(native_components, dict) or set(native_components) != set(NATIVE_COMPONENT_PATHS):
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
        (
            "Narwhal 3D".encode("utf-16le"),
            "Narwhal3D.exe".encode("utf-16le"),
            "NARWHAL3D_VERSION".encode("utf-16le"),
            "NARWHAL3D_PACKAGE_PATH".encode("utf-16le"),
            args.expected_version.encode("utf-16le"),
        ),
        "Narwhal launcher")
    for filename, relative_path in NATIVE_COMPONENT_PATHS.items():
        path = root / relative_path
        record = native_components[filename]
        data = path.read_bytes()
        if (not isinstance(record, dict) or record.get("sha256") != sha256(path)
                or record.get("size") != path.stat().st_size or not data.startswith(b"MZ")):
            raise VerificationError(f"invalid packaged native component: {filename}")
        if filename == "narwhal-welcome.exe":
            markers = (b"--runtime is required", b"--bootstrap is required",
                       b"NARWHAL3D_WELCOME_REQUEST", b"NARWHAL3D_VERSION",
                       b"releases?per_page=20", b"download sha256 does not match",
                       b"Update ZIP expands beyond the safe limit")
            if record.get("launch_contract") != 1 or not all(marker in data for marker in markers):
                raise VerificationError("Windows native welcome lacks its launch/request contract")
        if filename == "narwhal_geometry_core.dll":
            identity = verify_geometry_core_identity(path)
            expected_identity = {
                "abi": GEOMETRY_CORE_ABI,
                "machine": identity["machine"],
                "exports": list(identity["exports"]),
                "dependencies": list(identity["dependencies"]),
                "real_bintools_move": "airtight",
            }
            for key, expected in expected_identity.items():
                if record.get(key) != expected:
                    raise VerificationError(f"Windows geometry-core manifest {key} differs")
        if filename == "narwhal_platform_core.dll":
            identity = verify_platform_core_identity(path)
            expected_identity = {
                "abi": PLATFORM_CORE_ABI,
                "machine": identity["machine"],
                "exports": list(identity["exports"]),
                "dependencies": list(identity["dependencies"]),
            }
            for key, expected in expected_identity.items():
                if record.get(key) != expected:
                    raise VerificationError(f"Windows platform-core manifest {key} differs")
        if filename == "narwhal-cdr-multiversion.exe" and (
                record.get("protocol") != CDR_MULTIVERSION_PROTOCOL
                or CDR_MULTIVERSION_PROTOCOL.encode("ascii") not in data):
            raise VerificationError("Windows multi-version CDR helper has the wrong protocol")
        if (filename == "narwhal-converter-supervisor.exe"
                and record.get("memory_limit_probe_mib") != 64):
            raise VerificationError(
                "Windows converter supervisor lacks executed memory-limit evidence")
        if filename == "narwhal-document-core.exe":
            if (record.get("protocol") != DOCUMENT_CORE_PROTOCOL
                    or DOCUMENT_CORE_PROTOCOL.encode("ascii") not in data):
                raise VerificationError("Windows Narwhal document core has the wrong protocol")
            try:
                from verify_document_core import source_aggregate
                expected_source = source_aggregate()
            except Exception as exc:  # noqa: BLE001
                raise VerificationError(f"cannot bind document-core source: {exc}") from exc
            if record.get("source_aggregate_sha256") != expected_source:
                raise VerificationError("Windows document core is not bound to current source")
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
    if manifest.get("excluded_components") != [
        "opennurbs-exact-save-helper", "optional-converters", "installer-file-associations",
    ]:
        raise VerificationError("Windows release exclusion policy is absent or altered")
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
