#!/usr/bin/env python3
"""Assemble a self-contained ARM64 app and verify the actual read-only DMG.

Build orchestration only. Requires qualified runtime/native inputs and an exact
clean public source revision. Ad-hoc signing is NOT notarization.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import tempfile
import urllib.request

from build_macos_runtime import inputs as runtime_inputs, BLENDER
from build_macos_native import source_inputs, COMPONENTS, UNAVAILABLE_COMPONENTS
from release_policy import validate_preview_version

MACHO = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
         b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(*args: str, env=None, timeout=600) -> str:
    print(f"RUN {Path(args[0]).name}: {args[1] if len(args) > 1 else ''}", flush=True)
    result = subprocess.run(args, env=env, timeout=timeout, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} exited {result.returncode}\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}")
    return result.stdout


def create_dmg(stage: Path, destination: Path) -> None:
    import time
    if destination.exists():
        raise ValueError("refusing to replace an existing DMG")
    for attempt in range(1, 4):
        candidate = destination.with_name(destination.stem + f".attempt-{attempt}.dmg")
        if candidate.exists() or candidate.with_suffix(".failed").exists():
            raise ValueError("DMG attempt path already exists")
        try:
            run("hdiutil", "create", "-volname", "Narwhal 3D", "-srcfolder", str(stage),
                "-fs", "HFS+", "-format", "UDZO", str(candidate), timeout=1200)
        except RuntimeError as error:
            # Disk Arbitration/Spotlight can transiently hold a newly created image.
            # Retain partial bytes and retry only this measured busy condition.
            if candidate.exists():
                candidate.rename(candidate.with_suffix(".failed"))
            if "Resource busy" not in str(error) or attempt == 3:
                raise
            time.sleep(2 * attempt)
        else:
            candidate.rename(destination)
            return


def inventory(bundle: Path) -> list[dict]:
    bundle = bundle.resolve()
    records = []
    for path in sorted(bundle.rglob("*")):
        relative = path.relative_to(bundle).as_posix()
        if path.is_symlink():
            if not path.resolve(strict=True).is_relative_to(bundle):
                raise ValueError(f"bundle symlink escapes application: {relative}")
            records.append({"path": relative, "link": os.readlink(path)})
        elif path.is_file():
            records.append({"path": relative, "size": path.stat().st_size, "sha256": sha(path)})
    return records


def prime_bytecode(python: Path, bundle: Path) -> None:
    """Compile relocation-safe Python caches with the bundled interpreter before signing.

    Blender ignores the environment-only bytecode switch. Checked-hash caches
    avoid first-launch writes even when relocation changes source timestamps.
    Python owns compilation because its bytecode format is interpreter-specific.
    """
    code = (
        "import compileall,py_compile,sys; "
        "assert sys.version_info[:2] == (3,13); "
        "ok=compileall.compile_dir(sys.argv[1],maxlevels=100,quiet=1,force=True,"
        "stripdir=sys.argv[1],invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH); "
        "raise SystemExit(0 if ok else 1)"
    )
    run(str(python), "-I", "-B", "-c", code, str(bundle), timeout=300)


def inventory_changes(before: list[dict], after: list[dict]) -> dict[str, list[str]]:
    old = {row["path"]: row for row in before}
    new = {row["path"]: row for row in after}
    return {"added": sorted(new.keys() - old.keys()),
            "removed": sorted(old.keys() - new.keys()),
            "changed": sorted(path for path in old.keys() & new.keys() if old[path] != new[path])}


def binaries(bundle: Path):
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(4) in MACHO:
                    yield path


def verify_macho(path: Path) -> None:
    run("lipo", str(path), "-verify_arch", "arm64")
    dependencies = run("otool", "-arch", "arm64", "-L", str(path)).splitlines()[1:]
    for dependency in dependencies:
        name = dependency.strip().split(" (compatibility", 1)[0]
        if name.startswith("/") and not name.startswith(("/System/Library/", "/usr/lib/")):
            raise ValueError(f"nonportable Mach-O dependency in {path.name}: {name}")
    commands = run("otool", "-arch", "arm64", "-l", str(path))
    minimums = re.findall(r"\bminos (\d+\.\d+(?:\.\d+)?)", commands)
    minimums += re.findall(r"cmd LC_VERSION_MIN_MACOSX.*?\n\s*version (\d+\.\d+(?:\.\d+)?)", commands, re.S)
    for minimum in minimums:
        if (tuple(map(int, minimum.split("."))) + (0, 0))[:3] > (14, 0, 0):
            raise ValueError(f"{path.name} requires macOS {minimum}, not 14.0")


def download_wheels(source: Path, cache: Path) -> list[Path]:
    lock = json.loads((source / "packaging/macos/arm64-wheels.lock.json").read_text())
    if lock["platform"] != "macos-arm64" or lock["python"] != "3.13" or lock["minimum_os"] != "14.0":
        raise ValueError("wrong Mac wheel target")
    cache.mkdir(parents=True, exist_ok=True)
    paths = []
    for item in lock["artifacts"]:
        path = cache / item["filename"]
        if not path.exists():
            with urllib.request.urlopen(item["url"], timeout=120) as response, path.open("xb") as output:
                shutil.copyfileobj(response, output, 131072)
        if path.stat().st_size != item["size"] or sha(path) != item["sha256"]:
            raise ValueError(f"wheel integrity mismatch: {path.name}")
        paths.append(path)
    return paths


def stage_application_core(source: Path, native_bin: Path, app: Path) -> None:
    """The session loader uses the adjacent slot, never an optional runtime fallback."""
    library = native_bin / "libnarwhal_application_core.dylib"
    license_source = source / "native/application_core/OPENNURBS_LICENSE"
    for path in (library, license_source, app / "licenses/Native-Rust-Dependencies.txt"):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing regular application-core input: {path}")
    adjacent = app / "narwhal3d/native"
    adjacent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(library, adjacent / library.name)
    shutil.copy2(license_source, app / "licenses/application_core-OPENNURBS.txt")


def verify_application_core(app: Path, python: Path) -> int:
    library = app / "runtime/macos-arm64/bin/libnarwhal_application_core.dylib"
    adjacent = app / "narwhal3d/native" / library.name
    for path in (library, adjacent, app / "licenses/application_core-OPENNURBS.txt",
                 app / "licenses/Native-Rust-Dependencies.txt"):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing regular application-core payload: {path}")
    if sha(library) != sha(adjacent):
        raise ValueError("application-core runtime/adjacent copies differ")
    code = ("import ctypes,sys; lib=ctypes.CDLL(sys.argv[2]); "
            "lib.narwhal_session_json; lib.narwhal_history_json; "
            "fn=getattr(lib,sys.argv[1]); fn.argtypes=[]; fn.restype=ctypes.c_uint32; "
            "print(fn())")
    for symbol in ("narwhal_session_abi", "narwhal_history_abi"):
        actual = run(str(python), "-I", "-B", "-c", code, symbol, str(adjacent), timeout=30).strip()
        if actual != "1":
            raise ValueError(f"application-core {symbol} must be 1, got {actual!r}")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "runtime", "native", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("assembly and DMG qualification require native ARM64 macOS")
    validate_preview_version(args.version)
    source, runtime, native, output = (getattr(args, name).resolve() for name in ("source", "runtime", "native", "output"))
    if run("git", "--no-pager", "-C", str(source), "rev-parse", "HEAD").strip() != args.source_revision:
        raise ValueError("source revision mismatch")
    if run("git", "--no-pager", "--no-optional-locks", "-C", str(source), "status", "--porcelain").strip():
        raise ValueError("build only from a clean frozen source checkout")
    runtime_receipt = json.loads((runtime / "native-runtime-build.json").read_text())
    if runtime_receipt["blender"] != BLENDER or runtime_receipt["patches"] != runtime_inputs(source):
        raise ValueError("runtime patch inputs differ from frozen source")
    native_receipt = json.loads((native / "native-build.json").read_text())
    if native_receipt["source_inputs"] != source_inputs(source):
        raise ValueError("native component source inputs differ from frozen source")
    if native_receipt.get("unavailable_components") != UNAVAILABLE_COMPONENTS:
        raise ValueError("native component availability differs from the approved Mac policy")
    expected = {name for names in COMPONENTS.values() for name in names}
    if set(native_receipt["components"]) != expected:
        raise ValueError("incomplete native component receipt")
    for name, record in native_receipt["components"].items():
        if sha(native / "bin" / name) != record["sha256"]:
            raise ValueError(f"native component changed: {name}")
    libraries = native / "bin/occt"
    actual_libraries = {path.name: sha(path) for path in libraries.glob("*.dylib")}
    if actual_libraries != native_receipt["occt_libraries"]:
        raise ValueError("OCCT libraries differ from the verified native build")
    apps = list(runtime.glob("*.app"))
    if len(apps) != 1:
        raise ValueError("expected exactly one runtime app")
    original_binary = apps[0] / "Contents/MacOS/Blender"
    if sha(original_binary) != runtime_receipt["executable_sha256"]:
        raise ValueError("runtime executable differs from its build receipt")
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / "Narwhal 3D.app"
    dmg = output / f"Narwhal_3D-{args.version}-macOS-arm64.dmg"
    if bundle.exists() or dmg.exists():
        raise ValueError("refusing to replace an existing app or DMG")
    app = bundle / "Contents/Resources/app"
    app.mkdir(parents=True)
    for name in ("narwhal3d", "licenses"):
        shutil.copytree(source / name, app / name)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES", "CHANGELOG.md", "STATUS.md"):
        shutil.copy2(source / name, app / name)
    (app / "launch").mkdir()
    for name in ("bootstrap.py", "open_arg.py", "narwhal3d.png"):
        shutil.copy2(source / "launch" / name, app / "launch" / name)
    runtime_app = bundle / "Contents/Resources/runtime/Blender.app"
    runtime_app.parent.mkdir()
    shutil.copytree(apps[0], runtime_app, symlinks=True)
    native_bin = app / "runtime/macos-arm64/bin"
    native_bin.mkdir(parents=True)
    for name in expected:
        shutil.copy2(native / "bin" / name, native_bin / name)
    stage_application_core(source, native_bin, app)
    interpreters = list(runtime_app.glob("Contents/Resources/**/python/bin/python3.13"))
    if len(interpreters) != 1:
        raise ValueError("runtime lacks one bundled CPython 3.13")
    python = interpreters[0]
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if run(str(python), "-I", "-B", "-c", "import sys;print(sys.version_info[:2])", env=environment).strip() != "(3, 13)":
        raise ValueError("wrong bundled Python ABI")
    wheels = download_wheels(source, output / "wheel-cache")
    run(str(python), "-I", "-B", "-m", "ensurepip", env=environment)
    # The cached runtime's NumPy 2.3.4 was built for macOS26. Replace it with
    # the SAME NumPy version's pinned macOS14 wheel, not a rewritten Mach-O header.
    run(str(python), "-I", "-B", "-m", "pip", "install", "--force-reinstall", "--no-index", "--no-deps", "--no-compile", *map(str, wheels), env=environment)
    sites = list(runtime_app.glob("Contents/Resources/**/site-packages/OCP/.dylibs"))
    if len(sites) != 1:
        raise ValueError("bundled OCP library directory is ambiguous")
    # One physical OCCT copy serves both Python and the native geometry bridge.
    shutil.copytree(native / "bin/occt", sites[0], dirs_exist_ok=True)
    (native_bin / "occt").symlink_to(os.path.relpath(sites[0], native_bin), target_is_directory=True)
    target = output / "launcher-target"
    run("cargo", "+1.95.0", "build", "--release", "--locked", "--manifest-path", str(source / "packaging/macos/launcher/Cargo.toml"),
        env=dict(environment, CARGO_TARGET_DIR=str(target), CARGO_BUILD_JOBS="2", MACOSX_DEPLOYMENT_TARGET="14.0"))
    (bundle / "Contents/MacOS").mkdir()
    shutil.copy2(target / "release/narwhal-macos-launcher", bundle / "Contents/MacOS/Narwhal3D")
    icon = runtime_app / "Contents/Resources/blender_icon_legacy.icns"
    shutil.copy2(icon, bundle / "Contents/Resources/narwhal.icns")
    info = {"CFBundleExecutable": "Narwhal3D", "CFBundleIdentifier": "org.narwhal3d.Narwhal3D",
            "CFBundleName": "Narwhal 3D", "CFBundleDisplayName": "Narwhal 3D", "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": args.version, "CFBundleVersion": args.version,
            "CFBundleIconFile": "narwhal.icns", "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True}
    (app / "MACOS_LIMITATIONS.txt").write_text("\n".join(UNAVAILABLE_COMPONENTS.values()) + "\n")
    (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    (bundle / "Contents/PkgInfo").write_bytes(b"APPL????")
    prime_bytecode(python, bundle)
    for path in binaries(bundle):
        verify_macho(path)
        run("codesign", "--force", "--sign", "-", str(path))
    nested = sorted((p for p in bundle.rglob("*") if p.is_dir() and not p.is_symlink()
                     and p.suffix in (".app", ".framework", ".bundle")), key=lambda p: len(p.parts), reverse=True)
    for path in (*nested, bundle):
        run("codesign", "--force", "--sign", "-", str(path))
    run("codesign", "--verify", "--deep", "--strict", str(bundle))
    application_core_abi = verify_application_core(app, python)
    records = inventory(bundle)
    with tempfile.TemporaryDirectory(prefix="narwhal-dmg-", dir=output) as temporary:
        stage = Path(temporary)
        run("ditto", str(bundle), str(stage / bundle.name))
        (stage / "Applications").symlink_to("/Applications", target_is_directory=True)
        create_dmg(stage, dmg)
    run("hdiutil", "verify", str(dmg))
    mounted = plistlib.loads(run("hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(dmg)).encode())
    volume = next(item for item in mounted["system-entities"] if "mount-point" in item)
    try:
        mounted_app = Path(volume["mount-point"]) / bundle.name
        if inventory(mounted_app) != records:
            raise ValueError("actual DMG application inventory differs from signed staging")
        run("codesign", "--verify", "--deep", "--strict", str(mounted_app))
        verify_application_core(mounted_app / "Contents/Resources/app",
                                mounted_app / python.relative_to(bundle))
        banner = run(str(mounted_app / "Contents/MacOS/Narwhal3D"), env=dict(environment, NARWHAL3D_LAUNCHER_TEST="version"), timeout=60)
        if not banner.startswith("Narwhal 3D 5.2.2"):
            raise ValueError("actual DMG launcher failed its runtime version check")
    finally:
        run("hdiutil", "detach", volume["dev-entry"])
    digest = sha(dmg)
    Path(str(dmg) + ".sha256").write_text(f"{digest}  {dmg.name}\n")
    receipt = {"schema": 1, "source_revision": args.source_revision, "version": args.version,
               "sha256": digest, "size": dmg.stat().st_size, "signing": "ad-hoc, not notarized",
               "actual_dmg_verified": True, "gui_acceptance": False, "inventory": records,
               "application_core_abi": application_core_abi,
               "application_core_history_abi": 1,
               "application_core_exports": ["narwhal_history_abi", "narwhal_history_json",
                                            "narwhal_session_abi", "narwhal_session_json"],
               "python_wheels": json.loads((source / "packaging/macos/arm64-wheels.lock.json").read_text())["artifacts"],
               "runtime_overrides": {"numpy": "same-version 2.3.4 macOS14 wheel replaces incompatible macOS26 prebuild"}}
    Path(str(dmg) + ".build.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Created and verified {dmg.name}; GUI/downloaded-file acceptance is still required.")


if __name__ == "__main__":
    main()
