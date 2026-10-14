#!/usr/bin/env python3
"""Build deterministic Linux/Windows Narwhal Rust selection-core artifacts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
CRATE = ROOT / "native" / "selection_core"
sys.path.insert(0, str(ROOT))
from scripts.verify_selection_core import (  # noqa: E402
    ABI_VERSION,
    COMPONENT,
    COMPONENT_VERSION,
    MANIFEST_NAME,
    PLATFORMS,
    RUST_TOOLCHAIN,
    VerificationError,
    inspect_artifact,
    source_aggregate,
    source_inventory,
    verify_directory,
)


class BuildError(RuntimeError):
    pass


def run(command: list[str], environment: dict[str, str]) -> None:
    try:
        subprocess.run(command, cwd=CRATE, env=environment, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildError(f"command failed: {' '.join(command)}: {exc}") from exc


def cargo_environment(epoch: int) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "CARGO_INCREMENTAL": "0",
        "LC_ALL": "C",
        "SOURCE_DATE_EPOCH": str(epoch),
        "TZ": "UTC",
    })
    return environment


def cargo_command(action: str, target: str) -> list[str]:
    if action == "build":
        return [
            "cargo", f"+{RUST_TOOLCHAIN}", "build", "--release",
            "--locked", "--offline", "--target", target,
        ]
    if action == "clean":
        return ["cargo", f"+{RUST_TOOLCHAIN}", "clean"]
    raise BuildError(f"unsupported Cargo action: {action}")


def compile_artifacts(epoch: int, clean: bool) -> dict[str, Path]:
    environment = cargo_environment(epoch)
    outputs = {}
    if clean:
        run(cargo_command("clean", ""), environment)
    for platform_name, info in PLATFORMS.items():
        target = str(info["target"])
        run(cargo_command("build", target), environment)
        outputs[platform_name] = (
            CRATE / "target" / target / "release" / str(info["filename"])
        )
    return outputs


def write_staging(staging: Path, binaries: dict[str, Path], epoch: int) -> dict[str, object]:
    sources = source_inventory()
    records: list[dict[str, object]] = []
    for platform_name, source in sorted(binaries.items()):
        info = PLATFORMS[platform_name]
        destination = staging / platform_name / str(info["filename"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        records.append(inspect_artifact(destination, platform_name))
    records.sort(key=lambda item: str(item["platform"]))
    manifest: dict[str, object] = {
        "schema": 1,
        "component": COMPONENT,
        "component_version": COMPONENT_VERSION,
        "rust_toolchain": RUST_TOOLCHAIN,
        "abi": ABI_VERSION,
        "source_date_epoch": epoch,
        "source_aggregate_sha256": source_aggregate(sources),
        "source_files": sources,
        "artifacts": records,
    }
    (staging / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verify_directory(staging)
    return manifest


def replace_output(staging: Path, output: Path) -> None:
    if output.is_symlink():
        raise BuildError(f"refusing symlink output directory: {output}")
    if output.exists():
        if not output.is_dir():
            raise BuildError(f"output exists and is not a directory: {output}")
        marker = output / MANIFEST_NAME
        try:
            identity = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BuildError(f"refusing to replace an unowned output directory: {output}") from exc
        if identity.get("schema") != 1 or identity.get("component") != COMPONENT:
            raise BuildError(f"refusing to replace an unowned output directory: {output}")
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, output)


def one_build(
    parent: Path, epoch: int, clean: bool, label: str,
) -> tuple[Path, dict[str, object]]:
    binaries = compile_artifacts(epoch, clean)
    staging = parent / label
    staging.mkdir()
    manifest = write_staging(staging, binaries, epoch)
    return staging, manifest


def build(output: Path, epoch: int, reproducible: bool) -> dict[str, object]:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="selection-core-build-", dir=output.parent) as temporary:
        temporary_path = Path(temporary)
        first, first_manifest = one_build(temporary_path, epoch, True, "first")
        final = first
        final_manifest = first_manifest
        if reproducible:
            second, second_manifest = one_build(temporary_path, epoch, True, "second")
            first_records = cast(list[dict[str, object]], first_manifest["artifacts"])
            second_records = cast(list[dict[str, object]], second_manifest["artifacts"])
            first_hashes = {item["platform"]: item["sha256"] for item in first_records}
            second_hashes = {item["platform"]: item["sha256"] for item in second_records}
            if first_hashes != second_hashes:
                raise BuildError(
                    f"clean selection-core builds are not reproducible: {first_hashes} != {second_hashes}"
                )
            final = second
            final_manifest = second_manifest
        publish = temporary_path / "publish"
        os.replace(final, publish)
        replace_output(publish, output)
    verified = verify_directory(output)
    verified_records = cast(list[dict[str, object]], verified["artifacts"])
    for item in verified_records:
        print(f"{item['platform']}: {item['sha256']} ({item['size']} bytes)")
    if reproducible:
        print("reproducibility: two clean builds matched for both targets")
    return final_manifest


def self_check() -> None:
    if set(PLATFORMS) != {"linux-x86_64", "windows-x86_64"}:
        raise BuildError("selection-core platform set is incomplete")
    for info in PLATFORMS.values():
        target = str(info["target"])
        clean = cargo_command("clean", target)
        build = cargo_command("build", target)
        if clean != ["cargo", f"+{RUST_TOOLCHAIN}", "clean"]:
            raise BuildError(f"unsafe Cargo clean command: {clean}")
        if (f"+{RUST_TOOLCHAIN}" not in build or "--release" not in build
                or "--locked" not in build or "--offline" not in build):
            raise BuildError(f"unsafe Cargo build command: {build}")
    print("build_selection_core.py self-check: OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/selection-core")
    parser.add_argument("--source-date-epoch", type=int, default=0)
    parser.add_argument("--reproducible", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    try:
        if args.source_date_epoch < 0:
            raise BuildError("--source-date-epoch must be non-negative")
        if args.self_check:
            self_check()
        else:
            build(args.output_dir, args.source_date_epoch, args.reproducible)
    except (BuildError, VerificationError) as exc:
        print(f"selection-core build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
