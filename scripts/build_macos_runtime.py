#!/usr/bin/env python3
"""Build only the pinned, fully patched ARM64 runtime on native macOS.

Python orchestrates existing patch modules and native build tools; no application
or geometry logic lives here. Build trees are preserved for incremental resume.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import subprocess
from pathlib import Path

BLENDER = "d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
MAC_LIBS = "a76ef917b4849ba2b1b1deb1a643e131a884a63b"
# GitHub's source mirror does not host Blender's LFS objects.
BLENDER_REMOTE = "https://projects.blender.org/blender/blender.git"
PATCHES = (
    "apply_narwhal3d_branding.py", "apply_app_drawn.py",
    "apply_cad_boundary_wires.py", "apply_deferred_first_present.py",
    "apply_statusbar_height.py", "apply_window_overlay.py",
    "narwhal3d_app_icon.svg", "narwhal3d_window_icons.json",
)


def run(*args: str, cwd: Path | None = None, timeout: int = 600) -> None:
    subprocess.run(args, cwd=cwd, check=True, timeout=timeout)


def output(*args: str) -> str:
    return subprocess.check_output(args, text=True, timeout=30).strip()


def require_xcode(version: str) -> None:
    import re
    match = re.match(r"Xcode (\d+)\.", version)
    if not match or int(match.group(1)) < 16:
        raise ValueError("pinned Blender requires Xcode 16 or newer")


def inputs(root: Path) -> dict[str, str]:
    result = {}
    for name in PATCHES:
        path = root / "blender_patches" / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing/nonregular patch input: {name}")
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def patch(root: Path, source: Path) -> None:
    for index, name in enumerate(PATCHES[:6]):
        path = root / "blender_patches" / name
        spec = importlib.util.spec_from_file_location(f"mac_patch_{index}", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if index == 0:
            module.apply(str(source), str(root / "blender_patches/narwhal3d_app_icon.svg"))
        elif index < 4:
            module.SRC = str(source / "source/blender")
            module.main()
        else:
            module.apply(source / "source/blender")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("requires native ARM64 macOS; not a cross-build script")
    if not 1 <= args.jobs <= 4:
        parser.error("jobs must be between 1 and 4")
    require_xcode(output("xcodebuild", "-version"))
    root = Path(__file__).resolve().parents[1]
    work = args.work.resolve()
    if work == root or root in work.parents:
        parser.error("runtime work/cache must be outside the source checkout")
    work.mkdir(parents=True, exist_ok=True)
    expected = {"blender": BLENDER, "mac_libraries": MAC_LIBS, "patches": inputs(root)}
    marker = work / "inputs.json"
    source = work / "blender"
    if marker.exists() and json.loads(marker.read_text()) != expected:
        raise SystemExit("cache inputs differ; preserve this cache and choose a new work directory")
    if not source.exists():
        source.mkdir()
        run("git", "init", str(source))
        run("git", "-C", str(source), "remote", "add", "origin", BLENDER_REMOTE)
        run("git", "-C", str(source), "fetch", "--depth", "1", "origin", BLENDER, timeout=900)
        run("git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD")
    if output("git", "--no-pager", "-C", str(source), "rev-parse", "HEAD") != BLENDER:
        raise SystemExit("wrong Blender source revision")
    if not marker.exists():
        if output("git", "--no-pager", "--no-optional-locks", "-C", str(source), "status", "--porcelain", "--untracked-files=no"):
            raise SystemExit("unrecorded modified runtime source; refusing to overwrite")
        marker.write_text(json.dumps(expected, indent=2) + "\n")
    run("git", "-C", str(source), "submodule", "update", "--init", "--checkout", "--depth", "1", "lib/macos_arm64", timeout=1800)
    libraries = source / "lib/macos_arm64"
    if output("git", "--no-pager", "-C", str(libraries), "rev-parse", "HEAD") != MAC_LIBS:
        raise SystemExit("wrong macOS library revision")
    run("git", "-C", str(libraries), "lfs", "pull", timeout=1800)
    run("git", "-C", str(libraries), "lfs", "fsck", timeout=600)
    patch(root, source)
    build = work / "build"
    install = work / "install"
    run("cmake", "-S", str(source), "-B", str(build), "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_OSX_ARCHITECTURES=arm64",
        "-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0", "-DWITH_INSTALL_PORTABLE=ON",
        f"-DCMAKE_INSTALL_PREFIX={install}", timeout=600)
    run("cmake", "--build", str(build), "--parallel", str(args.jobs), timeout=18000)
    run("cmake", "--install", str(build), timeout=600)
    apps = list(install.glob("*.app"))
    if len(apps) != 1:
        raise SystemExit("expected exactly one installed runtime app")
    import plistlib
    plist = plistlib.loads((apps[0] / "Contents/Info.plist").read_bytes())
    executable = apps[0] / "Contents/MacOS" / plist["CFBundleExecutable"]
    banner = output(str(executable), "--version")
    if not banner.startswith("Narwhal 3D 5.2.2"):
        raise SystemExit("rebuilt runtime has the wrong branding/version")
    receipt = dict(expected, banner=banner, architecture=platform.machine(),
                   os=platform.mac_ver()[0], xcode=output("xcodebuild", "-version"),
                   cmake=output("cmake", "--version"),
                   executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest())
    (install / "native-runtime-build.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("Native ARM64 runtime built; full application/bundle qualification is a separate gate.")


if __name__ == "__main__":
    main()
