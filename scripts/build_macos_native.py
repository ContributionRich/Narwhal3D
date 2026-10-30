#!/usr/bin/env python3
"""Native ARM64 build/qualification of the packaged helpers, not the GUI runtime.

Python coordinates pinned downloads, Cargo and platform inspection tools. Geometry
and application operations remain in their existing native implementations.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

OCCT = "b8f597c677811d1f9f4d8a97f5ae2825c0353a42"
WHEEL = "cadquery_ocp_novtk-8.0.1.0.0-cp313-cp313-macosx_11_0_arm64.whl"
WHEEL_SHA = "733c976b2102d9684e4cd3f7fbad8aba8726426322f2b1d547a5cc6aceb0d6fb"
COMPONENTS = {
    "pure_core": ("libnarwhal_pure_core.dylib",),
    "selection_core": ("libnarwhal_selection_core.dylib",),
    "cdr_core": ("libnarwhal_cdr_core.dylib",),
    "platform_core": ("libnarwhal_platform_core.dylib",),
    "document_core": ("narwhal-document-core",),
    "converter_supervisor": ("narwhal-converter-supervisor",),
    "cdr_multiversion": ("narwhal-cdr-multiversion",),
    "automation_core": ("libnarwhal_automation.dylib", "narwhal-automation"),
    "geometry_core": ("libnarwhal_geometry_core.dylib",),
    "welcome": ("narwhal-welcome",),
}


def sha(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def run(*args: str, env=None, timeout=600, log=None) -> None:
    subprocess.run(args, env=env, check=True, timeout=timeout, stdout=log, stderr=log)


def source_inputs(root: Path) -> dict[str, str]:
    result = {"launch/narwhal3d.png": sha(root / "launch/narwhal3d.png")}
    for directory, dirs, files in os.walk(root / "native"):
        dirs[:] = [name for name in dirs if name not in {"target", ".git", "__pycache__"}]
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError(f"source symlink requires review: {path}")
            result[path.relative_to(root).as_posix()] = sha(path)
    return dict(sorted(result.items()))


def build_environment(work: Path, occt: Path, libraries: Path, test_tmp: Path) -> dict[str, str]:
    return dict(os.environ, TMPDIR=str(test_tmp), CARGO_TARGET_DIR=str(work / "target"),
                CARGO_BUILD_JOBS="2", MACOSX_DEPLOYMENT_TARGET="14.0",
                NARWHAL_OCCT_SOURCE_DIR=str(occt), NARWHAL_OCCT_LIB_DIR=str(libraries),
                DYLD_LIBRARY_PATH=str(libraries))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("native ARM64 macOS is required")
    root = Path(__file__).resolve().parents[1]
    inputs = source_inputs(root)
    work = args.work.resolve()
    if work == root or root in work.parents:
        parser.error("build outside the source checkout")
    work.mkdir(parents=True, exist_ok=True)
    wheel = work / WHEEL
    if not wheel.exists():
        with urllib.request.urlopen("https://pypi.org/pypi/cadquery-ocp-novtk/8.0.1.0.0/json", timeout=30) as response:
            metadata = json.loads(response.read(2_000_000))
        entry = next(item for item in metadata["urls"] if item["filename"] == WHEEL)
        if entry["digests"]["sha256"] != WHEEL_SHA:
            raise SystemExit("wheel metadata differs from the approved pin")
        with urllib.request.urlopen(entry["url"], timeout=120) as response, wheel.open("xb") as output:
            shutil.copyfileobj(response, output, 131072)
    if sha(wheel) != WHEEL_SHA:
        raise SystemExit("wheel SHA256 mismatch")
    python = work / "python"
    if not python.exists():
        python.mkdir()
        with zipfile.ZipFile(wheel) as archive:
            for entry in archive.infolist():
                path = Path(entry.filename)
                if path.is_absolute() or ".." in path.parts or ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                    raise SystemExit("unsafe wheel member")
            archive.extractall(python)
    libraries = python / "OCP/.dylibs"
    dylibs = sorted(libraries.glob("*.dylib"))
    if len(dylibs) != 77:
        raise SystemExit("unexpected pinned OCCT dylib inventory")
    for library in dylibs:
        run("install_name_tool", "-id", "@rpath/" + library.name, str(library))
        run("codesign", "--force", "--sign", "-", str(library))
    occt = work / "occt"
    if not occt.exists():
        run("git", "init", str(occt))
        run("git", "-C", str(occt), "remote", "add", "origin", "https://github.com/Open-Cascade-SAS/OCCT.git")
        run("git", "-C", str(occt), "fetch", "--depth", "1", "origin", OCCT)
        run("git", "-C", str(occt), "checkout", "--detach", "FETCH_HEAD")
    if subprocess.check_output(["git", "--no-pager", "-C", str(occt), "rev-parse", "HEAD"], text=True).strip() != OCCT:
        raise SystemExit("OCCT checkout differs from the approved pin")
    # Darwin's normal /var/folders temporary prefix exceeds sockaddr_un limits
    # for several nested socket fixtures. Keep those real socket tests intact.
    test_tmp = tempfile.TemporaryDirectory(prefix="nw-", dir="/tmp")
    env = build_environment(work, occt, libraries, Path(test_tmp.name))
    output = work / "output"
    output.mkdir(exist_ok=True)
    bin_dir = output / "bin"
    bin_dir.mkdir(exist_ok=True)
    receipts = {}
    for crate, names in COMPONENTS.items():
        manifest = str(root / "native" / crate / "Cargo.toml")
        with (output / f"{crate}.log").open("w") as log:
            for mode in ("test", "build"):
                run("cargo", "+1.95.0", mode, "--release", "--locked", "--jobs", "2", "--manifest-path", manifest,
                    env=env, timeout=1800, log=log)
        for name in names:
            destination = bin_dir / name
            shutil.copy2(work / "target/release" / name, destination)
            if name.endswith(".dylib"):
                run("install_name_tool", "-id", "@rpath/" + name, str(destination))
            run("codesign", "--force", "--sign", "-", str(destination))
            architecture = subprocess.check_output(["lipo", "-archs", str(destination)], text=True).strip()
            if architecture != "arm64":
                raise SystemExit(f"wrong architecture: {name}: {architecture}")
            dependencies = subprocess.check_output(["otool", "-L", str(destination)], text=True)
            if "/Users/" in "\n".join(dependencies.splitlines()[1:]) or "/DLC/" in dependencies:
                raise SystemExit(f"build-path dependency: {name}")
            receipts[name] = {"sha256": sha(destination), "size": destination.stat().st_size,
                              "architecture": architecture, "dependencies": dependencies.splitlines()[1:]}
        print(f"{crate}: native tests and release build passed", flush=True)
    shutil.copytree(libraries, bin_dir / "occt", dirs_exist_ok=True)
    if source_inputs(root) != inputs:
        raise SystemExit("native sources changed during compilation")
    receipt = {"source_inputs": inputs, "source_revision": subprocess.check_output(["git", "--no-pager", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
               "occt_source": OCCT, "occt_wheel_sha256": WHEEL_SHA, "components": receipts,
               "boundary": "Native helper tests only; not GUI, DMG or Gatekeeper acceptance"}
    (output / "native-build.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
