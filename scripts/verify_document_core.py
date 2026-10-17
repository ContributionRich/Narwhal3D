#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify reproducible Narwhal document-core artifacts and their source binding."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "native" / "document_core"
MANIFEST_NAME = "document-core-manifest.json"
COMPONENT = "narwhal-document-core"
COMPONENT_VERSION = "1.0.0"
PROTOCOL = "narwhal-document-core 1"
RUST_TOOLCHAIN = "1.95.0"
LINUX_IMAGE = (
    "quay.io/pypa/manylinux_2_28_x86_64@"
    "sha256:1e7b8460ad8fb3fb0fbe1cd818845159ac896b4fd17452d56e892c291f3b97a8"
)
MAX_GLIBC = (2, 28)
PLATFORMS = {
    "linux-x86_64": {
        "target": "x86_64-unknown-linux-gnu",
        "filename": "narwhal-document-core",
    },
    "windows-x86_64": {
        "target": "x86_64-pc-windows-gnu",
        "filename": "narwhal-document-core.exe",
    },
}
LINUX_DEPENDENCIES = ("libc.so.6", "libpthread.so.0")
WINDOWS_IMPORTS = (
    "api-ms-win-core-synch-l1-2-0.dll",
    "kernel32.dll",
    "msvcrt.dll",
    "ntdll.dll",
)
SOURCE_EXCLUDED_TOP_LEVEL = {".git", "target"}
GLIBC_RE = re.compile(r"GLIBC_(\d+)\.(\d+)")
PROTOCOL_RE = re.compile(rb"narwhal-document-core [0-9]+")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


class VerificationError(RuntimeError):
    """An artifact or its provenance did not satisfy the release contract."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise VerificationError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def require_regular_file(path: Path, description: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise VerificationError(f"missing {description}: {path}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise VerificationError(f"missing or unsafe {description}: {path}")


def _source_paths() -> list[Path]:
    if not CRATE.is_dir() or CRATE.is_symlink():
        raise VerificationError(f"missing or unsafe document-core crate: {CRATE}")
    paths: list[Path] = []
    for path in CRATE.rglob("*"):
        relative = path.relative_to(CRATE)
        if relative.parts and relative.parts[0] in SOURCE_EXCLUDED_TOP_LEVEL:
            continue
        if path.is_symlink():
            raise VerificationError(f"symlink is not a permitted crate source: {relative.as_posix()}")
        if path.is_dir():
            continue
        require_regular_file(path, "document-core source file")
        paths.append(path)
    if not paths:
        raise VerificationError("document-core source inventory is empty")
    return sorted(paths, key=lambda item: item.relative_to(CRATE).as_posix())


def source_inventory() -> list[dict[str, str]]:
    return [
        {
            "path": path.relative_to(CRATE).as_posix(),
            "sha256": sha256(path),
        }
        for path in _source_paths()
    ]


def source_aggregate(records: list[dict[str, str]] | None = None) -> str:
    digest = hashlib.sha256()
    for record in records if records is not None else source_inventory():
        digest.update(record["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(record["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _read_source(relative: str) -> str:
    path = CRATE / relative
    require_regular_file(path, f"crate source {relative}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise VerificationError(f"cannot read crate source {relative}: {exc}") from exc


def verify_crate_contract() -> None:
    cargo_toml = _read_source("Cargo.toml")
    cargo_lock = _read_source("Cargo.lock")
    toolchain = _read_source("rust-toolchain.toml")
    config = _read_source(".cargo/config.toml")
    library = _read_source("src/lib.rs")

    dependency_sections = re.findall(
        r"^\s*\[(?:[^]\n.]+\.)?(?:build-|dev-)?dependencies(?:\.[^]\n]+)?\]\s*$",
        cargo_toml,
        re.MULTILINE,
    )
    if dependency_sections:
        raise VerificationError("document-core Cargo.toml must remain dependency-free")
    if cargo_lock.count("[[package]]") != 1:
        raise VerificationError("document-core Cargo.lock must contain exactly the root package")
    if f'name = "{COMPONENT}"' not in cargo_lock:
        raise VerificationError("document-core Cargo.lock does not name the root package")
    if f'name = "{COMPONENT}"' not in cargo_toml:
        raise VerificationError("document-core Cargo.toml component name differs")
    if f'version = "{COMPONENT_VERSION}"' not in cargo_toml:
        raise VerificationError("document-core Cargo.toml version differs")
    if 'rust-version = "1.95"' not in cargo_toml:
        raise VerificationError("document-core minimum Rust version is not pinned to 1.95")
    if f'channel = "{RUST_TOOLCHAIN}"' not in toolchain:
        raise VerificationError("document-core Rust toolchain is not pinned to 1.95.0")
    for info in PLATFORMS.values():
        if f'"{info["target"]}"' not in toolchain:
            raise VerificationError(f'toolchain does not pin target {info["target"]}')
    if "--build-id=none" not in config or "--no-insert-timestamp" not in config:
        raise VerificationError("document-core deterministic linker flags are absent")
    if f'pub const PROTOCOL_VERSION: &str = "{PROTOCOL}";' not in library:
        raise VerificationError("document-core source does not declare the required protocol")


def run_tool(
    command: list[str],
    description: str,
    *,
    cwd: Path | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env={"LC_ALL": "C", "PATH": os.defpath, "TZ": "UTC"},
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError(f"cannot {description}: {exc}") from exc


def _decoded_output(result: subprocess.CompletedProcess[bytes]) -> str:
    return (result.stdout + result.stderr).decode("utf-8", errors="replace")


def elf_dependencies(path: Path) -> tuple[str, ...]:
    text = _decoded_output(run_tool(["readelf", "-d", str(path)], "inspect ELF dependencies"))
    return tuple(sorted(re.findall(r"\(NEEDED\).*Shared library: \[([^]]+)\]", text)))


def elf_max_glibc(path: Path) -> tuple[int, int]:
    text = _decoded_output(
        run_tool(["readelf", "--version-info", str(path)], "inspect ELF symbol versions")
    )
    versions = {(int(major), int(minor)) for major, minor in GLIBC_RE.findall(text)}
    if not versions:
        raise VerificationError("Linux document core has no measurable GLIBC requirement")
    maximum = max(versions)
    if maximum > MAX_GLIBC:
        raise VerificationError(
            f"Linux document core requires GLIBC_{maximum[0]}.{maximum[1]}, "
            f"above GLIBC_{MAX_GLIBC[0]}.{MAX_GLIBC[1]}"
        )
    return maximum


def verify_no_elf_build_id(path: Path) -> None:
    text = _decoded_output(run_tool(["readelf", "--notes", str(path)], "inspect ELF notes"))
    if re.search(r"(?:GNU_BUILD_ID|Build ID:)", text):
        raise VerificationError("Linux document core contains a forbidden linker build ID")


def execute_protocol(path: Path) -> None:
    result = run_tool([str(path), "--version"], "execute Linux document-core protocol")
    expected = (PROTOCOL + "\n").encode("ascii")
    if result.stdout != expected or result.stderr:
        raise VerificationError(
            "Linux document-core --version output differs from the exact required protocol"
        )


def _fixture_files() -> dict[str, bytes]:
    return {
        "assets/note.txt": b"document-core artifact smoke\n",
        "document.3dm": b"deterministic document payload\x00\x01",
        "manifest.json": b'{"format":"smoke","version":1}\n',
        "masters/12345678-1234-abcd-9876-0123456789ab.brep": b"exact master payload\n",
    }


def execute_container_smoke(path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="document-core-verify-") as temporary:
        root = Path(temporary)
        staging = root / "staging"
        expected = _fixture_files()
        for relative, payload in expected.items():
            destination = staging / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)

        archive = root / "smoke.narwhal"
        unpacked = root / "unpacked"
        run_tool([str(path), "pack", str(staging), str(archive)], "run document-core pack smoke")
        inspected = run_tool(
            [str(path), "inspect", str(archive)], "run document-core inspect smoke"
        )
        lines = inspected.stdout.decode("utf-8", errors="strict").splitlines()
        expected_lines = []
        for relative, payload in sorted(expected.items()):
            expected_lines.append(f"{hashlib.sha256(payload).hexdigest()}\t{len(payload)}\t{relative}")
        if lines != expected_lines or inspected.stderr:
            raise VerificationError("Linux document-core inspect smoke output differs")

        run_tool(
            [str(path), "unpack", str(archive), str(unpacked)],
            "run document-core unpack smoke",
        )
        actual_paths = {
            item.relative_to(unpacked).as_posix()
            for item in unpacked.rglob("*")
            if item.is_file()
        }
        if actual_paths != set(expected):
            raise VerificationError("Linux document-core unpack smoke member set differs")
        for relative, payload in expected.items():
            if (unpacked / Path(relative)).read_bytes() != payload:
                raise VerificationError(f"Linux document-core unpack changed {relative}")


def inspect_linux(path: Path) -> dict[str, object]:
    header = _decoded_output(run_tool(["readelf", "-h", str(path)], "inspect ELF header"))
    if not re.search(r"Class:\s+ELF64", header):
        raise VerificationError("Linux document core is not ELF64")
    if not re.search(r"Type:\s+DYN", header):
        raise VerificationError("Linux document core is not a position-independent executable")
    if not re.search(r"Machine:\s+Advanced Micro Devices X86-64", header):
        raise VerificationError("Linux document core is not x86-64")
    verify_no_elf_build_id(path)
    dependencies = elf_dependencies(path)
    forbidden = {"libgcc_s.so.1", "libstdc++.so.6"}.intersection(dependencies)
    if forbidden:
        raise VerificationError(
            f"Linux document core dynamically links forbidden runtime libraries: {sorted(forbidden)}"
        )
    if dependencies != LINUX_DEPENDENCIES:
        raise VerificationError(f"unexpected Linux document-core dependencies: {dependencies}")
    maximum = elf_max_glibc(path)
    execute_protocol(path)
    execute_container_smoke(path)
    return {
        "format": "ELF64-x86-64",
        "dependencies": list(dependencies),
        "max_glibc": f"{maximum[0]}.{maximum[1]}",
        "build_id": None,
        "protocol": PROTOCOL,
        "protocol_check": "executed-version-pack-inspect-unpack",
    }


def pe_timestamp(path: Path) -> int:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            file_size = stream.tell()
            if file_size < 0x40:
                raise VerificationError("Windows document core has a truncated DOS header")
            stream.seek(0)
            if stream.read(2) != b"MZ":
                raise VerificationError("Windows document core lacks an MZ header")
            stream.seek(0x3C)
            offset_bytes = stream.read(4)
            if len(offset_bytes) != 4:
                raise VerificationError("Windows document core has a truncated DOS header")
            pe_offset = struct.unpack("<I", offset_bytes)[0]
            if pe_offset < 0x40 or pe_offset > file_size - 26:
                raise VerificationError("Windows document core has an invalid PE header offset")
            stream.seek(pe_offset)
            if stream.read(4) != b"PE\0\0":
                raise VerificationError("Windows document core lacks a PE signature")
            coff = stream.read(20)
            if len(coff) != 20:
                raise VerificationError("Windows document core has a truncated COFF header")
            (
                machine,
                sections,
                timestamp,
                _symbol_table,
                _symbol_count,
                optional_size,
                characteristics,
            ) = struct.unpack("<HHIIIHH", coff)
            if optional_size < 2 or pe_offset + 24 + optional_size > file_size:
                raise VerificationError("Windows document core has a truncated optional header")
            if stream.read(2) != b"\x0b\x02":
                raise VerificationError("Windows document core is not PE32+")
    except OSError as exc:
        raise VerificationError(f"cannot read Windows document core: {exc}") from exc
    if machine != 0x8664:
        raise VerificationError(
            f"Windows document core has machine 0x{machine:04x}, expected x86-64"
        )
    if sections == 0 or characteristics & 0x0002 == 0:
        raise VerificationError("Windows document core is not marked executable")
    return timestamp


def inspect_windows_protocol_bytes(path: Path) -> None:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise VerificationError(f"cannot read Windows protocol bytes: {exc}") from exc
    required = PROTOCOL.encode("ascii")
    discovered = sorted(set(PROTOCOL_RE.findall(content)))
    if content.count(required) != 1 or discovered != [required]:
        printable = [item.decode("ascii", errors="replace") for item in discovered]
        raise VerificationError(
            f"Windows document-core protocol bytes differ: {printable}, required exactly {PROTOCOL!r}"
        )


def inspect_windows(path: Path) -> dict[str, object]:
    text = _decoded_output(
        run_tool(
            ["x86_64-w64-mingw32-objdump", "-p", str(path)],
            "inspect Windows PE metadata",
        )
    )
    if "file format pei-x86-64" not in text or "PE32+" not in text:
        raise VerificationError("Windows document core is not an x86-64 PE32+ executable")
    if not re.search(r"Characteristics\s+0x[0-9a-f]+\s+executable", text, re.IGNORECASE):
        raise VerificationError("Windows document core is not marked executable")
    imports = tuple(sorted(name.lower() for name in re.findall(r"DLL Name:\s+(\S+)", text)))
    if imports != WINDOWS_IMPORTS:
        raise VerificationError(f"unexpected Windows document-core imports: {imports}")
    timestamp = pe_timestamp(path)
    if timestamp != 0:
        raise VerificationError(f"Windows document core embeds nonzero PE timestamp {timestamp}")
    inspect_windows_protocol_bytes(path)
    return {
        "format": "PE32+-x86-64",
        "dependencies": list(imports),
        "coff_timestamp": timestamp,
        "protocol": PROTOCOL,
        "protocol_check": "inspected-binary-bytes",
    }


def inspect_artifact(path: Path, platform_name: str) -> dict[str, object]:
    require_regular_file(path, f"{platform_name} document-core artifact")
    info = PLATFORMS.get(platform_name)
    if info is None:
        raise VerificationError(f"unknown document-core platform: {platform_name}")
    if platform_name == "linux-x86_64":
        measured = inspect_linux(path)
    else:
        measured = inspect_windows(path)
    return {
        "platform": platform_name,
        "target": info["target"],
        "path": f"{platform_name}/{info['filename']}",
        "size": path.stat().st_size,
        "sha256": sha256(path),
        **measured,
    }


def _load_manifest(path: Path) -> dict[str, object]:
    require_regular_file(path, "document-core manifest")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read document-core manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError("document-core manifest root must be an object")
    return value


def _verify_tree(root: Path) -> None:
    expected_files = {MANIFEST_NAME}
    expected_directories = set(PLATFORMS)
    for platform_name, info in PLATFORMS.items():
        expected_files.add(f"{platform_name}/{info['filename']}")
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if ".." in PurePosixPath(relative).parts or path.is_symlink():
            raise VerificationError(f"unsafe document-core artifact entry: {relative}")
        if path.is_dir():
            actual_directories.add(relative)
        elif path.is_file():
            actual_files.add(relative)
        else:
            raise VerificationError(f"unsafe document-core artifact entry: {relative}")
    if actual_files != expected_files or actual_directories != expected_directories:
        raise VerificationError(
            "document-core artifact set differs: "
            f"expected files {sorted(expected_files)} and directories {sorted(expected_directories)}, "
            f"got files {sorted(actual_files)} and directories {sorted(actual_directories)}"
        )


def verify_directory(root: Path) -> dict[str, object]:
    if root.is_symlink():
        raise VerificationError(f"missing or unsafe document-core artifact directory: {root}")
    root = root.resolve()
    if not root.is_dir():
        raise VerificationError(f"missing or unsafe document-core artifact directory: {root}")
    manifest = _load_manifest(root / MANIFEST_NAME)
    expected_keys = {
        "schema",
        "component",
        "component_version",
        "protocol",
        "rust_toolchain",
        "linux_build_image",
        "windows_target",
        "builds_per_platform",
        "source_date_epoch",
        "source_aggregate_sha256",
        "source_files",
        "artifacts",
    }
    if set(manifest) != expected_keys:
        raise VerificationError(f"manifest fields differ: {sorted(manifest)}")
    identity = {
        "schema": 1,
        "component": COMPONENT,
        "component_version": COMPONENT_VERSION,
        "protocol": PROTOCOL,
        "rust_toolchain": RUST_TOOLCHAIN,
        "linux_build_image": LINUX_IMAGE,
        "windows_target": PLATFORMS["windows-x86_64"]["target"],
    }
    for key, expected in identity.items():
        if manifest.get(key) != expected:
            raise VerificationError(
                f"manifest {key}: expected {expected!r}, got {manifest.get(key)!r}"
            )
    builds_per_platform = manifest.get("builds_per_platform")
    if (
        not isinstance(builds_per_platform, int)
        or isinstance(builds_per_platform, bool)
        or builds_per_platform not in {1, 2}
    ):
        raise VerificationError("manifest builds_per_platform must be 1 or 2")
    epoch = manifest.get("source_date_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise VerificationError("manifest source_date_epoch must be a non-negative integer")

    sources = source_inventory()
    if manifest.get("source_files") != sources:
        raise VerificationError("manifest source inventory differs from the current pinned crate")
    aggregate = source_aggregate(sources)
    if manifest.get("source_aggregate_sha256") != aggregate:
        raise VerificationError("manifest source aggregate differs from the current pinned crate")
    _verify_tree(root)

    artifact_list = manifest.get("artifacts")
    if not isinstance(artifact_list, list) or len(artifact_list) != len(PLATFORMS):
        raise VerificationError("manifest must declare exactly both document-core artifacts")
    declared: dict[str, dict[str, object]] = {}
    for item in artifact_list:
        if not isinstance(item, dict) or not isinstance(item.get("platform"), str):
            raise VerificationError("manifest contains an invalid artifact record")
        platform_name = str(item["platform"])
        if platform_name in declared:
            raise VerificationError(f"manifest repeats artifact platform {platform_name}")
        declared[platform_name] = item
    if set(declared) != set(PLATFORMS):
        raise VerificationError(f"manifest artifact platforms differ: {sorted(declared)}")

    artifact_paths: dict[str, Path] = {}
    for platform_name, info in PLATFORMS.items():
        relative = f"{platform_name}/{info['filename']}"
        path = root / Path(relative)
        require_regular_file(path, f"{platform_name} artifact")
        item = declared[platform_name]
        if item.get("path") != relative:
            raise VerificationError(f"manifest path differs for {platform_name}")
        if item.get("size") != path.stat().st_size:
            raise VerificationError(f"manifest size differs for {platform_name}")
        digest = item.get("sha256")
        if not isinstance(digest, str) or not SHA_RE.fullmatch(digest) or digest != sha256(path):
            raise VerificationError(f"manifest hash differs for {platform_name}")
        artifact_paths[platform_name] = path

    measured = [
        inspect_artifact(artifact_paths[platform_name], platform_name)
        for platform_name in PLATFORMS
    ]
    measured.sort(key=lambda item: str(item["platform"]))
    if artifact_list != measured:
        raise VerificationError("manifest artifact metadata differs from measured binaries")
    return manifest


def write_synthetic_pe(path: Path, timestamp: int) -> None:
    content = bytearray(0x9A)
    content[:2] = b"MZ"
    struct.pack_into("<I", content, 0x3C, 0x80)
    content[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", content, 0x84, 0x8664, 1, timestamp, 0, 0, 2, 0x22E)
    content[0x98:0x9A] = b"\x0b\x02"
    path.write_bytes(content)


def self_check() -> None:
    verify_crate_contract()
    records = source_inventory()
    aggregate = source_aggregate(records)
    if not SHA_RE.fullmatch(aggregate):
        raise VerificationError("document-core source aggregate is invalid")
    if not LINUX_IMAGE.endswith(
        "@sha256:1e7b8460ad8fb3fb0fbe1cd818845159ac896b4fd17452d56e892c291f3b97a8"
    ):
        raise VerificationError("document-core Linux image is not digest-pinned")
    with tempfile.TemporaryDirectory(prefix="document-core-verifier-self-check-") as temporary:
        root = Path(temporary)
        pe = root / "sample.exe"
        write_synthetic_pe(pe, 0)
        if pe_timestamp(pe) != 0:
            raise VerificationError("PE timestamp parser self-check failed")
        protocol = root / "protocol.bin"
        protocol.write_bytes(b"prefix\0" + PROTOCOL.encode("ascii") + b"\0suffix")
        inspect_windows_protocol_bytes(protocol)
    print(f"verify_document_core.py self-check: OK ({aggregate})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path, nargs="?")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_check:
            if args.artifacts is not None:
                parser.error("artifact directory cannot be combined with --self-check")
            self_check()
        elif args.artifacts is not None:
            manifest = verify_directory(args.artifacts)
            print(
                f"verified {manifest['component']} protocol {manifest['protocol']} "
                "for Linux and Windows x86-64"
            )
        else:
            parser.error("provide an artifact directory or --self-check")
    except VerificationError as exc:
        print(f"document-core verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
