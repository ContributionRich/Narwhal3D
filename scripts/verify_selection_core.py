#!/usr/bin/env python3
"""Verify reproducible Narwhal Rust selection-core artifacts and their source binding."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "native" / "selection_core"
MANIFEST_NAME = "selection-core-manifest.json"
COMPONENT = "narwhal-selection-core"
COMPONENT_VERSION = "0.1.0"
RUST_TOOLCHAIN = "1.95.0"
ABI_VERSION = 1
MAX_GLIBC = (2, 35)
EXPECTED_EXPORTS = (
    "narwhal_any_projected_segment_crosses_rect_f32",
    "narwhal_any_screen_segment_crosses_rect_f64",
    "narwhal_nearest_projected_curve_f32",
    "narwhal_nearest_projected_point_f64",
    "narwhal_nearest_projected_polylines_f64",
    "narwhal_nearest_screen_polyline_f64",
    "narwhal_project_display_points_f64",
    "narwhal_project_points_f32",
    "narwhal_projected_curves_rect_query_f32",
    "narwhal_projected_points_rect_query_f64",
    "narwhal_projected_segments_rect_query_f32",
    "narwhal_projected_snap_distances_f64",
    "narwhal_screen_boxes_from_clip_f64",
    "narwhal_screen_boxes_from_world_f64",
    "narwhal_screen_rects_from_aabbs_f64",
    "narwhal_selection_core_abi_version",
)
PLATFORMS = {
    "linux-x86_64": {
        "target": "x86_64-unknown-linux-gnu",
        "filename": "libnarwhal_selection_core.so",
    },
    "windows-x86_64": {
        "target": "x86_64-pc-windows-gnu",
        "filename": "narwhal_selection_core.dll",
    },
}
LINUX_NEEDED = ("ld-linux-x86-64.so.2", "libc.so.6", "libgcc_s.so.1")
WINDOWS_IMPORTS = (
    "api-ms-win-core-synch-l1-2-0.dll",
    "bcryptprimitives.dll",
    "kernel32.dll",
    "msvcrt.dll",
    "ntdll.dll",
    "userenv.dll",
    "ws2_32.dll",
)
GLIBC_RE = re.compile(r"GLIBC_(\d+)\.(\d+)")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


class VerificationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_file(path: Path, description: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise VerificationError(f"missing or unsafe {description}: {path}")


def source_inventory() -> list[dict[str, str]]:
    paths = [
        CRATE / ".cargo/config.toml",
        CRATE / "Cargo.lock",
        CRATE / "Cargo.toml",
        CRATE / "rust-toolchain.toml",
        *sorted((CRATE / "src").rglob("*.rs")),
    ]
    records = []
    for path in paths:
        require_file(path, "selection-core source input")
        records.append({
            "path": path.relative_to(CRATE).as_posix(),
            "sha256": sha256(path),
        })
    return records


def source_aggregate(records: list[dict[str, str]] | None = None) -> str:
    digest = hashlib.sha256()
    for record in records or source_inventory():
        digest.update(record["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(record["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def run_tool(command: list[str], description: str) -> str:
    try:
        result = subprocess.run(
            command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError(f"cannot inspect {description}: {exc}") from exc
    return result.stdout + result.stderr


def elf_exports(path: Path) -> tuple[str, ...]:
    text = run_tool(["nm", "-D", "--defined-only", str(path)], "ELF exports")
    return tuple(sorted(line.split()[-1] for line in text.splitlines() if line.split()))


def elf_needed(path: Path) -> tuple[str, ...]:
    text = run_tool(["readelf", "-d", str(path)], "ELF dependencies")
    names = re.findall(r"\(NEEDED\).*Shared library: \[([^]]+)\]", text)
    return tuple(sorted(names))


def elf_max_glibc(path: Path) -> tuple[int, int]:
    text = run_tool(["readelf", "--version-info", str(path)], "ELF symbol versions")
    versions = {(int(major), int(minor)) for major, minor in GLIBC_RE.findall(text)}
    if not versions:
        raise VerificationError("Linux selection core has no measurable GLIBC requirements")
    maximum = max(versions)
    if maximum > MAX_GLIBC:
        raise VerificationError(
            f"Linux selection core requires GLIBC_{maximum[0]}.{maximum[1]}, "
            f"above GLIBC_{MAX_GLIBC[0]}.{MAX_GLIBC[1]}"
        )
    return maximum


def verify_linux_abi(path: Path) -> None:
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        return
    try:
        library = ctypes.CDLL(str(path))
        version = library.narwhal_selection_core_abi_version
        version.argtypes = []
        version.restype = ctypes.c_uint32
        actual = int(version())
    except (AttributeError, OSError) as exc:
        raise VerificationError(f"cannot execute Linux selection-core ABI probe: {exc}") from exc
    if actual != ABI_VERSION:
        raise VerificationError(f"selection-core ABI is {actual}, expected {ABI_VERSION}")


def inspect_linux(path: Path) -> dict[str, object]:
    header = run_tool(["readelf", "-h", str(path)], "ELF header")
    if not re.search(r"Class:\s+ELF64", header) or not re.search(r"Type:\s+DYN", header):
        raise VerificationError("Linux selection core is not an ELF64 shared object")
    if not re.search(r"Machine:\s+Advanced Micro Devices X86-64", header):
        raise VerificationError("Linux selection core is not x86-64")
    exports = elf_exports(path)
    if exports != EXPECTED_EXPORTS:
        raise VerificationError(f"Linux selection-core exports differ: {exports}")
    needed = elf_needed(path)
    if needed != LINUX_NEEDED:
        raise VerificationError(f"unexpected Linux selection-core dependencies: {needed}")
    glibc = elf_max_glibc(path)
    verify_linux_abi(path)
    return {
        "machine": "elf64-x86-64",
        "exports": list(exports),
        "dependencies": list(needed),
        "max_glibc": f"{glibc[0]}.{glibc[1]}",
        "abi_executed": sys.platform == "linux" and platform.machine().lower() in {"x86_64", "amd64"},
    }


def pe_timestamp(path: Path) -> int:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise VerificationError("Windows selection core lacks an MZ header")
        stream.seek(0x3C)
        pe_offset_raw = stream.read(4)
        if len(pe_offset_raw) != 4:
            raise VerificationError("Windows selection core has a truncated DOS header")
        pe_offset = struct.unpack("<I", pe_offset_raw)[0]
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\0\0":
            raise VerificationError("Windows selection core lacks a PE signature")
        coff = stream.read(20)
        if len(coff) != 20:
            raise VerificationError("Windows selection core has a truncated COFF header")
        machine, _sections, timestamp = struct.unpack("<HHI", coff[:8])
        if machine != 0x8664:
            raise VerificationError(f"Windows selection core has machine 0x{machine:04x}, expected x86-64")
        return timestamp


def inspect_windows(path: Path) -> dict[str, object]:
    text = run_tool(["x86_64-w64-mingw32-objdump", "-p", str(path)], "PE metadata")
    if "file format pei-x86-64" not in text or "DLL" not in text:
        raise VerificationError("Windows selection core is not an x86-64 PE DLL")
    exports = tuple(sorted(re.findall(r"\]\s+\S+\s+(narwhal_[A-Za-z0-9_]+)\s*$", text, re.MULTILINE)))
    if exports != EXPECTED_EXPORTS:
        raise VerificationError(f"Windows selection-core exports differ: {exports}")
    imports = tuple(sorted(name.lower() for name in re.findall(r"DLL Name:\s+(\S+)", text)))
    if imports != WINDOWS_IMPORTS:
        raise VerificationError(f"unexpected Windows selection-core DLL imports: {imports}")
    timestamp = pe_timestamp(path)
    if timestamp != 0:
        raise VerificationError(f"Windows selection core embeds non-zero COFF timestamp {timestamp}")
    return {
        "machine": "pe32plus-x86-64",
        "exports": list(exports),
        "dependencies": list(imports),
        "coff_timestamp": timestamp,
        "abi_executed": False,
    }


def inspect_artifact(path: Path, platform_name: str) -> dict[str, object]:
    require_file(path, f"{platform_name} artifact")
    if platform_name == "linux-x86_64":
        measured = inspect_linux(path)
    elif platform_name == "windows-x86_64":
        measured = inspect_windows(path)
    else:
        raise VerificationError(f"unknown selection-core platform: {platform_name}")
    info = PLATFORMS[platform_name]
    return {
        "platform": platform_name,
        "target": info["target"],
        "path": f"{platform_name}/{info['filename']}",
        "size": path.stat().st_size,
        "sha256": sha256(path),
        "abi": ABI_VERSION,
        **measured,
    }


def verify_directory(root: Path) -> dict[str, object]:
    root = root.resolve()
    manifest_path = root / MANIFEST_NAME
    require_file(manifest_path, "selection-core manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read selection-core manifest: {exc}") from exc
    expected_identity = {
        "schema": 1,
        "component": COMPONENT,
        "component_version": COMPONENT_VERSION,
        "rust_toolchain": RUST_TOOLCHAIN,
        "abi": ABI_VERSION,
    }
    for key, value in expected_identity.items():
        if manifest.get(key) != value:
            raise VerificationError(f"manifest {key}: expected {value!r}, got {manifest.get(key)!r}")
    epoch = manifest.get("source_date_epoch")
    if not isinstance(epoch, int) or epoch < 0:
        raise VerificationError("manifest source_date_epoch must be a non-negative integer")
    sources = source_inventory()
    if manifest.get("source_files") != sources:
        raise VerificationError("manifest source inventory differs from the current pinned crate")
    aggregate = source_aggregate(sources)
    if manifest.get("source_aggregate_sha256") != aggregate:
        raise VerificationError("manifest source aggregate differs from the current pinned crate")
    expected_paths = {MANIFEST_NAME}
    for platform_name, info in PLATFORMS.items():
        expected_paths.add(f"{platform_name}/{info['filename']}")
    actual_paths = set()
    for path in root.rglob("*"):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise VerificationError(f"unsafe selection-core artifact entry: {path.relative_to(root)}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if ".." in PurePosixPath(relative).parts:
                raise VerificationError(f"unsafe selection-core artifact path: {relative}")
            actual_paths.add(relative)
    if actual_paths != expected_paths:
        raise VerificationError(
            f"selection-core artifact set differs: expected {sorted(expected_paths)}, got {sorted(actual_paths)}"
        )

    declared_list = manifest.get("artifacts")
    if not isinstance(declared_list, list) or len(declared_list) != len(PLATFORMS):
        raise VerificationError("manifest must declare exactly both selection-core artifacts")
    declared = {}
    for item in declared_list:
        if not isinstance(item, dict) or not isinstance(item.get("platform"), str):
            raise VerificationError("manifest contains an invalid artifact record")
        if item["platform"] in declared:
            raise VerificationError(f"manifest repeats artifact platform {item['platform']}")
        declared[item["platform"]] = item
    if set(declared) != set(PLATFORMS):
        raise VerificationError(f"manifest artifact platforms differ: {sorted(declared)}")

    # Never execute or deeply inspect an artifact until its declared name, size and
    # cryptographic hash match. This keeps a corrupted/mutated ELF out of ctypes.
    measured = []
    for platform_name, info in PLATFORMS.items():
        relative = f"{platform_name}/{info['filename']}"
        path = root / Path(relative)
        require_file(path, f"{platform_name} artifact")
        record = declared[platform_name]
        if record.get("path") != relative:
            raise VerificationError(f"manifest path differs for {platform_name}")
        if record.get("size") != path.stat().st_size:
            raise VerificationError(f"manifest size differs for {platform_name}")
        digest = record.get("sha256")
        if not isinstance(digest, str) or not SHA_RE.fullmatch(digest) or digest != sha256(path):
            raise VerificationError(f"manifest hash differs for {platform_name}")
        measured.append(inspect_artifact(path, platform_name))
    measured.sort(key=lambda item: str(item["platform"]))
    if declared_list != measured:
        raise VerificationError("manifest artifact metadata differs from measured binaries")
    return manifest


def self_check() -> None:
    sources = source_inventory()
    aggregate = source_aggregate(sources)
    if not SHA_RE.fullmatch(aggregate) or len(EXPECTED_EXPORTS) != 16:
        raise VerificationError("selection-core verifier constants are invalid")
    toolchain = (CRATE / "rust-toolchain.toml").read_text(encoding="utf-8")
    config = (CRATE / ".cargo/config.toml").read_text(encoding="utf-8")
    for token in (RUST_TOOLCHAIN, *[str(info["target"]) for info in PLATFORMS.values()]):
        if token not in toolchain:
            raise VerificationError(f"pinned toolchain file lacks {token}")
    if "--build-id=none" not in config or "--no-insert-timestamp" not in config:
        raise VerificationError("deterministic linker flags are absent")
    print(f"verify_selection_core.py self-check: OK ({aggregate})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path, nargs="?")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_check:
            self_check()
        elif args.artifacts is not None:
            manifest = verify_directory(args.artifacts)
            print(
                f"verified {manifest['component']} ABI {manifest['abi']} "
                f"for Linux and Windows x86-64"
            )
        else:
            parser.error("provide an artifact directory or --self-check")
    except VerificationError as exc:
        print(f"selection-core verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
