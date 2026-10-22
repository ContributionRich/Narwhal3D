#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build reproducible Linux/Windows Narwhal document-core artifacts."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "native" / "document_core"
sys.path.insert(0, str(ROOT))
from scripts.verify_document_core import (  # noqa: E402
    COMPONENT,
    COMPONENT_VERSION,
    LINUX_IMAGE,
    MANIFEST_NAME,
    PLATFORMS,
    PROTOCOL,
    RUST_TOOLCHAIN,
    VerificationError,
    inspect_artifact,
    source_aggregate,
    source_inventory,
    verify_crate_contract,
    verify_directory,
)


class BuildError(RuntimeError):
    """The reproducible build contract could not be completed."""


def run(
    command: list[str],
    description: str,
    *,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
    timeout: float = 900.0,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = f"\n{exc.stdout or ''}{exc.stderr or ''}".rstrip()
        raise BuildError(f"cannot {description}: {exc}{detail}") from exc


def _safe_mount_path(path: Path, description: str) -> str:
    resolved = path.resolve()
    if "," in str(resolved):
        raise BuildError(f"{description} path contains a Docker --mount separator: {resolved}")
    return str(resolved)


def linux_container_command(crate: Path, toolchain: Path, workspace: Path, epoch: int) -> list[str]:
    crate_mount = _safe_mount_path(crate, "crate")
    toolchain_mount = _safe_mount_path(toolchain, "toolchain")
    output_mount = _safe_mount_path(workspace, "output")
    rustflags = " ".join(
        [
            "-C", "link-arg=-Wl,--build-id=none",
            "-C", "link-arg=-L/output/link",
        ]
    )
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--workdir", "/crate",
        "--mount", f"type=bind,src={crate_mount},dst=/crate,readonly",
        "--mount", f"type=bind,src={toolchain_mount},dst=/toolchain,readonly",
        "--mount", f"type=bind,src={output_mount},dst=/output",
        "--env", "CARGO_HOME=/output/cargo-home",
        "--env", "CARGO_INCREMENTAL=0",
        "--env", "CARGO_NET_OFFLINE=true",
        "--env", "CARGO_TARGET_DIR=/output/target",
        "--env", "HOME=/output/home",
        "--env", "LC_ALL=C",
        "--env", f"RUSTFLAGS={rustflags}",
        "--env", "RUSTC=/toolchain/bin/rustc",
        "--env", f"SOURCE_DATE_EPOCH={epoch}",
        "--env", "TMPDIR=/output/tmp",
        "--env", "TZ=UTC",
        LINUX_IMAGE,
        "/toolchain/bin/cargo",
        "build",
        "--release",
        "--locked",
        "--offline",
        "--target",
        str(PLATFORMS["linux-x86_64"]["target"]),
        "--manifest-path",
        "/crate/Cargo.toml",
        "--target-dir",
        "/output/target",
    ]


def windows_cargo_command(toolchain: Path, workspace: Path) -> list[str]:
    return [
        str(toolchain / "bin" / "cargo"),
        "build",
        "--release",
        "--locked",
        "--offline",
        "--target",
        str(PLATFORMS["windows-x86_64"]["target"]),
        "--manifest-path",
        str(CRATE / "Cargo.toml"),
        "--target-dir",
        str(workspace / "target"),
    ]


def _toolchain_root() -> Path:
    result = run(
        ["rustup", "which", "rustc", "--toolchain", RUST_TOOLCHAIN],
        f"locate Rust {RUST_TOOLCHAIN}",
        timeout=30.0,
    )
    rustc = Path(result.stdout.strip()).resolve()
    if rustc.name != "rustc" or rustc.parent.name != "bin":
        raise BuildError(f"rustup returned an unexpected rustc path: {rustc}")
    root = rustc.parent.parent
    for relative in ("bin/cargo", "bin/rustc"):
        candidate = root / relative
        if not candidate.is_file() or candidate.is_symlink():
            raise BuildError(f"Rust toolchain lacks {relative}: {candidate}")
    version = run([str(root / "bin/rustc"), "--version"], "check pinned rustc", timeout=30.0)
    if version.stdout.strip().split()[:2] != ["rustc", RUST_TOOLCHAIN]:
        raise BuildError(f"rustc is not exactly {RUST_TOOLCHAIN}: {version.stdout.strip()}")
    for info in PLATFORMS.values():
        target_lib = root / "lib" / "rustlib" / str(info["target"]) / "lib"
        if not target_lib.is_dir():
            raise BuildError(f"Rust {RUST_TOOLCHAIN} target is not installed: {info['target']}")
    return root


def _check_local_tools(toolchain: Path) -> None:
    run(["docker", "image", "inspect", LINUX_IMAGE], "find the pinned manylinux image", timeout=30.0)
    run(
        ["x86_64-w64-mingw32-gcc", "--version"],
        "find the pinned Windows GNU linker",
        timeout=30.0,
    )
    run(["readelf", "--version"], "find readelf", timeout=30.0)
    run(
        ["x86_64-w64-mingw32-objdump", "--version"],
        "find the Windows PE inspector",
        timeout=30.0,
    )
    cargo = toolchain / "bin" / "cargo"
    run([str(cargo), "--version"], "check pinned cargo", timeout=30.0)


def _empty_workspace(path: Path) -> None:
    if path.exists():
        raise BuildError(f"clean build workspace already exists: {path}")
    path.mkdir(parents=True)


def build_linux(toolchain: Path, workspace: Path, epoch: int) -> Path:
    _empty_workspace(workspace)
    link = workspace / "link"
    link.mkdir()
    # rustc requests -lgcc_s explicitly even with panic=abort.  Resolve that name
    # to GCC's static unwind archive without mutating the crate or container.
    (link / "libgcc_s.so").write_text("INPUT ( -lgcc_eh )\n", encoding="ascii")
    (workspace / "home").mkdir()
    (workspace / "cargo-home").mkdir()
    (workspace / "tmp").mkdir()
    command = linux_container_command(CRATE, toolchain, workspace, epoch)
    run(command, "build Linux document core in the pinned container")
    info = PLATFORMS["linux-x86_64"]
    artifact = workspace / "target" / str(info["target"]) / "release" / str(info["filename"])
    if not artifact.is_file() or artifact.is_symlink():
        raise BuildError(f"Linux build did not produce {artifact}")
    return artifact


def _windows_environment(toolchain: Path, workspace: Path, epoch: int) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "CARGO_HOME": str(workspace / "cargo-home"),
            "CARGO_INCREMENTAL": "0",
            "CARGO_NET_OFFLINE": "true",
            "LC_ALL": "C",
            "RUSTC": str(toolchain / "bin" / "rustc"),
            "RUSTFLAGS": (
                "-C link-arg=-Wl,--no-insert-timestamp "
                "-C link-arg=-static-libgcc -C link-arg=-static-libstdc++"
            ),
            "SOURCE_DATE_EPOCH": str(epoch),
            "TZ": "UTC",
        }
    )
    return environment


def build_windows(toolchain: Path, workspace: Path, epoch: int) -> Path:
    _empty_workspace(workspace)
    (workspace / "cargo-home").mkdir()
    command = windows_cargo_command(toolchain, workspace)
    run(
        command,
        "build Windows document core with the pinned GNU target",
        cwd=CRATE,
        environment=_windows_environment(toolchain, workspace, epoch),
    )
    info = PLATFORMS["windows-x86_64"]
    artifact = workspace / "target" / str(info["target"]) / "release" / str(info["filename"])
    if not artifact.is_file() or artifact.is_symlink():
        raise BuildError(f"Windows build did not produce {artifact}")
    return artifact


def _reproducible_hashes(builds: dict[str, list[Path]]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for platform_name, paths in builds.items():
        if len(paths) != 2:
            raise BuildError(f"{platform_name} did not complete exactly two clean builds")
        first = _sha256(paths[0])
        second = _sha256(paths[1])
        if first != second:
            raise BuildError(
                f"two clean {platform_name} builds differ: {first} != {second}"
            )
        hashes[platform_name] = second
    return hashes


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_staging(
    staging: Path,
    binaries: dict[str, Path],
    epoch: int,
    sources: list[dict[str, str]],
    builds_per_platform: int,
) -> dict[str, object]:
    staging.mkdir()
    records: list[dict[str, object]] = []
    for platform_name, source in sorted(binaries.items()):
        info = PLATFORMS[platform_name]
        destination = staging / platform_name / str(info["filename"])
        destination.parent.mkdir()
        shutil.copy2(source, destination)
        if platform_name == "linux-x86_64":
            destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        records.append(inspect_artifact(destination, platform_name))
    records.sort(key=lambda item: str(item["platform"]))
    manifest: dict[str, object] = {
        "schema": 1,
        "component": COMPONENT,
        "component_version": COMPONENT_VERSION,
        "protocol": PROTOCOL,
        "rust_toolchain": RUST_TOOLCHAIN,
        "linux_build_image": LINUX_IMAGE,
        "windows_target": PLATFORMS["windows-x86_64"]["target"],
        "builds_per_platform": builds_per_platform,
        "source_date_epoch": epoch,
        "source_aggregate_sha256": source_aggregate(sources),
        "source_files": sources,
        "artifacts": records,
    }
    (staging / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    verify_directory(staging)
    return manifest


def _owned_output(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise BuildError(f"refusing unsafe existing output directory: {path}")
    marker = path / MANIFEST_NAME
    try:
        manifest = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"refusing to replace unowned output directory: {path}") from exc
    if manifest.get("schema") != 1 or manifest.get("component") != COMPONENT:
        raise BuildError(f"refusing to replace unowned output directory: {path}")


def _rename_exchange(first: Path, second: Path) -> None:
    if sys.platform != "linux":
        raise BuildError("atomic replacement of an existing artifact directory requires Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise BuildError("host libc does not expose renameat2 for atomic artifact replacement")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_exchange = 2
    result = renameat2(
        at_fdcwd,
        os.fsencode(first),
        at_fdcwd,
        os.fsencode(second),
        rename_exchange,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise BuildError(f"atomic artifact-directory exchange failed: {os.strerror(error)}")


def publish_atomically(staging: Path, output: Path) -> None:
    if output.is_symlink():
        raise BuildError(f"refusing symlink output directory: {output}")
    if not output.exists():
        os.replace(staging, output)
        return
    _owned_output(output)
    _rename_exchange(staging, output)
    # After RENAME_EXCHANGE the newly published tree is at output and the old,
    # owned tree is at staging.  Cleanup cannot expose a partially published tree.
    shutil.rmtree(staging)


def build(output: Path, epoch: int, reproducible: bool) -> dict[str, object]:
    if epoch < 0:
        raise BuildError("--source-date-epoch must be non-negative")
    if sys.platform != "linux" or not hasattr(os, "getuid"):
        raise BuildError("the digest-pinned Linux container build requires a Linux host")
    if output.is_symlink():
        raise BuildError(f"refusing symlink output directory: {output}")
    verify_crate_contract()
    initial_sources = source_inventory()
    toolchain = _toolchain_root()
    _check_local_tools(toolchain)

    builds_per_platform = 2 if reproducible else 1
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="document-core-build-", dir=output.parent) as temporary:
        root = Path(temporary)
        builds: dict[str, list[Path]] = {name: [] for name in PLATFORMS}
        for sequence in range(1, builds_per_platform + 1):
            builds["linux-x86_64"].append(
                build_linux(toolchain, root / f"linux-{sequence}", epoch)
            )
        for sequence in range(1, builds_per_platform + 1):
            builds["windows-x86_64"].append(
                build_windows(toolchain, root / f"windows-{sequence}", epoch)
            )
        hashes = _reproducible_hashes(builds) if reproducible else {}
        if source_inventory() != initial_sources:
            raise BuildError("document-core crate changed during the build")

        binaries = {platform_name: paths[-1] for platform_name, paths in builds.items()}
        staging = root / "publish"
        manifest = write_staging(
            staging,
            binaries,
            epoch,
            initial_sources,
            builds_per_platform,
        )
        publish_atomically(staging, output)

    verified = verify_directory(output)
    records = cast(list[dict[str, object]], verified["artifacts"])
    for item in records:
        print(f"{item['platform']}: {item['sha256']} ({item['size']} bytes)")
    if reproducible:
        print(f"reproducibility: two clean builds matched for both targets ({hashes})")
    return manifest


def self_check() -> None:
    verify_crate_contract()
    if set(PLATFORMS) != {"linux-x86_64", "windows-x86_64"}:
        raise BuildError("document-core platform set is incomplete")
    with tempfile.TemporaryDirectory(prefix="document-core-builder-self-check-") as temporary:
        root = Path(temporary)
        command = linux_container_command(root / "crate", root / "toolchain", root / "output", 0)
    required = {
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        LINUX_IMAGE,
        "--locked",
        "--offline",
        str(PLATFORMS["linux-x86_64"]["target"]),
    }
    if not required.issubset(command):
        raise BuildError(f"Linux container command lacks required isolation or pins: {command}")
    mounts = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "--mount"]
    if len(mounts) != 3 or "--tmpfs" in command or "--volume" in command:
        raise BuildError(f"Linux container command must mount only crate/toolchain/output: {mounts}")
    if not any("dst=/crate,readonly" in value for value in mounts):
        raise BuildError("Linux crate mount is not read-only")
    if not any("dst=/toolchain,readonly" in value for value in mounts):
        raise BuildError("Linux toolchain mount is not read-only")
    if not any("dst=/output" in value and "readonly" not in value for value in mounts):
        raise BuildError("Linux output mount is not writable")
    container_environment = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--env"
    ]
    if not any(
        value.startswith("RUSTFLAGS=") and "--build-id=none" in value
        for value in container_environment
    ):
        raise BuildError("Linux container build does not disable linker build IDs")
    windows = windows_cargo_command(Path("/toolchain"), Path("/output"))
    for token in (
        "--release",
        "--locked",
        "--offline",
        str(PLATFORMS["windows-x86_64"]["target"]),
    ):
        if token not in windows:
            raise BuildError(f"Windows Cargo command lacks {token}: {windows}")
    windows_environment = _windows_environment(Path("/toolchain"), Path("/output"), 0)
    if "--no-insert-timestamp" not in windows_environment.get("RUSTFLAGS", ""):
        raise BuildError("Windows build does not disable the COFF timestamp")
    if windows_environment.get("SOURCE_DATE_EPOCH") != "0":
        raise BuildError("Windows build does not preserve SOURCE_DATE_EPOCH")
    print("build_document_core.py self-check: OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "document-core",
    )
    parser.add_argument("--source-date-epoch", type=int, default=0)
    parser.add_argument(
        "--reproducible",
        action="store_true",
        help="perform two isolated clean builds per platform and compare their hashes",
    )
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_check:
            self_check()
        else:
            build(args.output_dir, args.source_date_epoch, args.reproducible)
    except (BuildError, VerificationError) as exc:
        print(f"document-core build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
