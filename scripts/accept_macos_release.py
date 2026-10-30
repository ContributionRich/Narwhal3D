#!/usr/bin/env python3
"""Native checks of the actual DMG with disposable profiles and frozen tests."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

from assemble_macos_release import inventory

p = argparse.ArgumentParser()
p.add_argument("--dmg", type=Path, required=True)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--work", type=Path, required=True)
a = p.parse_args()
work = a.work.resolve()
work.mkdir(parents=True, exist_ok=True)
source = a.source.resolve()
records = []

def run(command, *, env=None, timeout=240, label):
    print("START", label, flush=True)
    with (work / (label + ".log")).open("w") as log:
        result = subprocess.run(list(map(str, command)), env=env, cwd=work, stdout=log,
                                stderr=subprocess.STDOUT, timeout=timeout)
    text = (work / (label + ".log")).read_text(errors="replace")
    records.append(dict(label=label, returncode=result.returncode))
    (work / "results.json").write_text(json.dumps(records, indent=2))
    if result.returncode:
        raise RuntimeError(label + " failed:\n" + text[-8000:])
    print("PASS", label, flush=True)
    return text

mounted = plistlib.loads(subprocess.check_output(["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(a.dmg)], timeout=60))
volume = next(v for v in mounted["system-entities"] if "mount-point" in v)
try:
    original = Path(volume["mount-point"]) / "Narwhal 3D.app"
    before = inventory(original)
    bundle = work / "Relocated 日本語" / original.name
    bundle.parent.mkdir()
    run(["ditto", original, bundle], label="relocate")
    assert inventory(bundle) == before
    run(["codesign", "--verify", "--deep", "--strict", bundle], label="relocated-signature")
    app = bundle / "Contents/Resources/app"
    runtime = bundle / "Contents/Resources/runtime/Blender.app/Contents/MacOS/Blender"
    native = app / "runtime/macos-arm64/bin"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NARWHAL", "DYLD_"))}
    profile = work / "profile"
    profile.mkdir()
    env.update(HOME=str(profile), TMPDIR=str(work), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(app),
               NARWHAL3D_SETTINGS_FILE=str(profile / "preferences.json"),
               BLENDER_USER_CONFIG=str(profile / "blender"), NARWHAL3D_VERSION="0.4.8",
               NARWHAL3D_DISABLE_UPDATE_CHECK="1", NARWHAL3D_NO_DIALOGS="1",
               NARWHAL3D_DEFER_FIRST_PRESENT="1", NARWHAL3D_STATUSBAR_HEIGHT="37")
    for core in ("pure", "selection", "geometry", "platform", "cdr"):
        env["NARWHAL_" + core.upper() + "_CORE"] = str(native / ("libnarwhal_" + core + "_core.dylib"))
    env["NARWHAL_DOCUMENT_CORE"] = str(native / "narwhal-document-core")
    env["NARWHAL_CONVERTER_SUPERVISOR"] = str(native / "narwhal-converter-supervisor")
    bootstrap = app / "launch/bootstrap.py"
    tests = work / "tests/spike"
    tests.mkdir(parents=True)
    (tests.parent / "artifacts").mkdir()
    cases = [
        ("document", "test_narwhal_document.py", True, False, "native-document checks passed"),
        ("render", "test_render_quality_gui.py", False, False, "RENDER_QUALITY_RESULT"),
        ("gumball", "test_gumball_extrude_scale_gui.py", False, True, "GUMBALL_GUI_RESULT"),
    ]
    for label, name, background, first, marker in cases:
        test = tests / name
        shutil.copy2(source / "spike" / name, test)
        assert hashlib.sha256(test.read_bytes()).digest() == hashlib.sha256((source / "spike" / name).read_bytes()).digest()
        command = [runtime, "--threads", "2", "--factory-startup", "--python-exit-code", "1"]
        command += ["-b"] if background else ["--enable-event-simulate"]
        for script in ([test, bootstrap] if first else [bootstrap, test]):
            command += ["--python", script]
        case_profile = work / ("profile-" + label)
        case_profile.mkdir()
        case_env = dict(env, HOME=str(case_profile),
                        NARWHAL3D_SETTINGS_FILE=str(case_profile / "preferences.json"),
                        BLENDER_USER_CONFIG=str(case_profile / "blender"))
        text = run(command, env=case_env, timeout=600 if background else 180, label=label)
        assert marker in text, (label, "acceptance marker missing")
        assert "Traceback (most recent call last)" not in text, label
        if not background:
            report = json.loads(next(line[len(marker)+1:] for line in reversed(text.splitlines()) if line.startswith(marker + " ")))
            assert report["status"] == "passed", report
    assert inventory(bundle) == before, "relocated package changed during acceptance"
    assert inventory(original) == before, "mounted DMG changed"
    records.append(dict(label="post-test-inventory", passed=True, files=len(before)))
    (work / "results.json").write_text(json.dumps(records, indent=2))
finally:
    subprocess.run(["hdiutil", "detach", volume["dev-entry"]], check=True, timeout=60)
print("PASS: actual DMG relocation, exact document round trip, GPU rendering, Gumball and unchanged inventories")
