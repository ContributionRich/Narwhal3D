#!/usr/bin/env python3
"""Build source-bound native helpers in the pinned portable Linux container.

Build orchestration only. Preserves prior compiler targets, uses no network, and
writes receipts after checking that all source inputs stayed unchanged.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

IMAGE = "quay.io/pypa/manylinux_2_28_x86_64@sha256:1e7b8460ad8fb3fb0fbe1cd818845159ac896b4fd17452d56e892c291f3b97a8"
COMPONENTS = {
    "welcome": ("narwhal-welcome",),
    "pure_core": ("libnarwhal_pure_core.so",),
    "geometry_core": ("libnarwhal_geometry_core.so",),
    "platform_core": ("libnarwhal_platform_core.so",),
    "selection_core": ("libnarwhal_selection_core.so",),
    "cdr_core": ("libnarwhal_cdr_core.so",),
    "cdr_multiversion": ("narwhal-cdr-multiversion",),
    "document_core": ("narwhal-document-core",),
    "converter_supervisor": ("narwhal-converter-supervisor",),
    "automation_core": ("libnarwhal_automation.so", "narwhal-automation"),
}


def digest(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def inventory(root: Path, crate: str) -> dict[str, str]:
    roots = [root / "native" / crate]
    if crate == "welcome":
        roots.append(root / "launch/narwhal3d.png")
    if crate == "cdr_multiversion":
        roots.append(root / "native/vendor/tracedraw")
    result = {}
    for base in roots:
        if base.is_file():
            result[base.relative_to(root).as_posix()] = digest(base)
            continue
        if not base.is_dir():
            raise ValueError(f"missing input: {base}")
        for directory, dirs, files in os.walk(base):
            dirs[:] = sorted(n for n in dirs if n not in {".git", "target", "__pycache__"})
            for name in files:
                path = Path(directory) / name
                if path.is_symlink():
                    raise ValueError(f"unreviewed source symlink: {path}")
                result[path.relative_to(root).as_posix()] = digest(path)
    return dict(sorted(result.items()))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "output", "seed-target", "occt-source", "occt-libs"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--crate", choices=COMPONENTS, required=True)
    p.add_argument("--jobs", type=int, default=2)
    p.add_argument("--test", action="store_true")
    args = p.parse_args()
    root, out = args.source_root.resolve(), args.output.resolve()
    if not 1 <= args.jobs <= 4:
        p.error("jobs must be 1..4")
    if out == root or root in out.parents:
        p.error("output must be outside source")
    for path in (root, args.seed_target, args.occt_source, args.occt_libs):
        if not path.is_dir():
            p.error(f"missing input directory: {path}")
    out.mkdir(parents=True, exist_ok=True)
    target = out / (args.crate + "-target")
    if not target.exists():
        subprocess.run(["cp", "-a", "--reflink=auto", str(args.seed_target), str(target)], check=True)
    before = inventory(root, args.crate)
    home = Path.home()
    mode = "test" if args.test else "build"
    command = ["podman", "run", "--rm", "--name", f"narwhal048-native-{args.crate}",
               "--timeout=1800", "--pull=never", "--network=none", "--read-only",
               f"--cpus={args.jobs}", "--memory=4g", "--memory-swap=4g", "--pids-limit=512",
               "--security-opt", "label=disable", "--security-opt", "no-new-privileges",
               "--tmpfs", "/tmp:rw,size=512m", "--tmpfs", "/cargohome:rw,size=32m"]
    for source, destination, access in (
        (root, "/proj", "ro"), (target, "/target", "rw"),
        (home / ".rustup", "/rustup", "ro"),
        (home / ".cargo/registry", "/cargohome/registry", "ro"),
        (home / ".cargo/git", "/cargohome/git", "ro"),
        (args.occt_source.resolve(), "/occt-source", "ro"),
        (args.occt_libs.resolve(), "/occt-libs", "ro"),
    ):
        command += ["-v", f"{source}:{destination}:{access}"]
    for value in (
        "PATH=/rustup/toolchains/1.95.0-x86_64-unknown-linux-gnu/bin:/opt/rh/gcc-toolset-14/root/usr/bin:/usr/local/bin:/usr/bin:/bin",
        "CARGO_HOME=/cargohome", "RUSTUP_HOME=/rustup", "CARGO_TARGET_DIR=/target",
        "CARGO_NET_OFFLINE=true", "CARGO_INCREMENTAL=0", "HOME=/tmp/home",
        "NARWHAL_OCCT_SOURCE_DIR=/occt-source", "NARWHAL_OCCT_LIB_DIR=/occt-libs",
        "LD_LIBRARY_PATH=/occt-libs", "LC_ALL=C", "TZ=UTC",
    ):
        command += ["-e", value]
    command += ["-w", f"/proj/native/{args.crate}", IMAGE, "cargo", mode,
                "--release", "--frozen", "--offline", "--jobs", str(args.jobs)]
    log = out / f"{args.crate}-{mode}.log"
    with log.open("w") as stream:
        completed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=1830)
    after = inventory(root, args.crate)
    receipt = {"command": command, "inputs": before, "source_unchanged": before == after,
               "returncode": completed.returncode, "log_sha256": digest(log)}
    (out / f"{args.crate}-{mode}-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if completed.returncode or before != after:
        raise SystemExit(f"{args.crate}: failed or source changed; inspect {log}")
    if not args.test:
        (out / "bin").mkdir(exist_ok=True)
        for name in COMPONENTS[args.crate]:
            source, destination = target / "release" / name, out / "bin" / name
            if destination.exists() and digest(destination) != digest(source):
                raise SystemExit(f"refusing to overwrite previously staged {destination}")
            shutil.copy2(source, destination)
    print(f"{args.crate} {mode}: passed; receipt {out / (args.crate + '-' + mode + '-receipt.json')}")


if __name__ == "__main__":
    main()
