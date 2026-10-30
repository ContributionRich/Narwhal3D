#!/usr/bin/env python3
"""Repack an exact existing Mac candidate without introducing newer application code.

Only target-interpreter bytecode caches and code signatures change. Original source,
input artifact hash and separate packaging revision remain explicit in the receipt.
"""
import argparse
import json
from pathlib import Path
import platform
import plistlib
import subprocess
import tempfile

from assemble_macos_release import create_dmg, inventory, inventory_changes, prime_bytecode, run, sha


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dmg", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--expected-source", required=True)
    a = p.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        p.error("requires native ARM64 macOS")
    source_dmg = a.dmg.resolve()
    receipt = json.loads(Path(str(source_dmg) + ".build.json").read_text())
    if receipt["source_revision"] != a.expected_source or sha(source_dmg) != receipt["sha256"]:
        raise ValueError("input candidate provenance mismatch")
    if source_dmg.stat().st_size != receipt["size"] or not receipt["actual_dmg_verified"]:
        raise ValueError("input candidate was not verified")
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / "Narwhal 3D.app"
    dmg = output / source_dmg.name
    if bundle.exists() or dmg.exists():
        raise ValueError("refusing to replace an existing candidate")
    mounted = plistlib.loads(run("hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(source_dmg)).encode())
    volume = next(v for v in mounted["system-entities"] if "mount-point" in v)
    try:
        original = Path(volume["mount-point"]) / bundle.name
        if inventory(original) != receipt["inventory"]:
            raise ValueError("input DMG inventory changed")
        run("codesign", "--verify", "--deep", "--strict", str(original))
        run("ditto", str(original), str(bundle))
    finally:
        run("hdiutil", "detach", volume["dev-entry"])
    interpreters = list(bundle.glob("Contents/Resources/runtime/Blender.app/Contents/Resources/**/python/bin/python3.13"))
    if len(interpreters) != 1:
        raise ValueError("ambiguous bundled interpreter")
    before = inventory(bundle)
    prime_bytecode(interpreters[0], bundle)
    changes = inventory_changes(before, inventory(bundle))
    if changes["removed"] or any(not name.endswith(".pyc") for name in changes["added"] + changes["changed"]):
        raise ValueError("cache preparation unexpectedly changed application/source content")
    print("Prepared bytecode caches:", {key: len(value) for key, value in changes.items()}, flush=True)
    nested = sorted((x for x in bundle.rglob("*") if x.is_dir() and not x.is_symlink()
                     and x.suffix in (".app", ".framework", ".bundle")), key=lambda x: len(x.parts), reverse=True)
    for path in (*nested, bundle):
        run("codesign", "--force", "--sign", "-", str(path))
    run("codesign", "--verify", "--deep", "--strict", str(bundle))
    records = inventory(bundle)
    with tempfile.TemporaryDirectory(prefix="narwhal-stage-", dir=output) as directory:
        stage = Path(directory)
        run("ditto", str(bundle), str(stage / bundle.name))
        (stage / "Applications").symlink_to("/Applications", target_is_directory=True)
        create_dmg(stage, dmg)
    run("hdiutil", "verify", str(dmg))
    mounted = plistlib.loads(run("hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(dmg)).encode())
    volume = next(v for v in mounted["system-entities"] if "mount-point" in v)
    try:
        actual = Path(volume["mount-point"]) / bundle.name
        if inventory(actual) != records:
            raise ValueError("repacked DMG differs from signed staging")
        run("codesign", "--verify", "--deep", "--strict", str(actual))
    finally:
        run("hdiutil", "detach", volume["dev-entry"])
    revision = subprocess.check_output(["git", "--no-pager", "-C", str(Path(__file__).resolve().parents[1]), "rev-parse", "HEAD"], text=True).strip()
    result = dict(receipt, sha256=sha(dmg), size=dmg.stat().st_size, inventory=records,
                  gui_acceptance=False, input_dmg_sha256=receipt["sha256"],
                  packaging_revision=revision, bytecode="target CPython3.13 checked-hash caches before signing")
    Path(str(dmg) + ".build.json").write_text(json.dumps(result, indent=2) + "\n")
    Path(str(dmg) + ".sha256").write_text(f"{result['sha256']}  {dmg.name}\n")
    print("Verified repacked DMG; fresh actual-package acceptance is still required.")


if __name__ == "__main__":
    main()
