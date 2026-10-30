#!/usr/bin/env python3
"""Exercise actual DMG launcher/Welcome startup; never substitute a fake runtime."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument("--dmg", type=Path, required=True)
p.add_argument("--work", type=Path, required=True)
a = p.parse_args()
work = a.work.resolve()
work.mkdir(parents=True, exist_ok=True)
mounted = plistlib.loads(subprocess.check_output(["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(a.dmg)], timeout=60))
volume = next(v for v in mounted["system-entities"] if "mount-point" in v)
try:
    bundle = Path(volume["mount-point"]) / "Narwhal 3D.app"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NARWHAL", "DYLD_"))}
    env.update(HOME=str(work), NARWHAL3D_DISABLE_UPDATE_CHECK="1", NARWHAL3D_NO_DIALOGS="1",
               NARWHAL3D_SETTINGS_FILE=str(work / "preferences.json"),
               BLENDER_USER_CONFIG=str(work / "blender"), PYTHONDONTWRITEBYTECODE="1")
    log_path = work / "launcher.log"
    with log_path.open("w") as log:
        process = subprocess.Popen([str(bundle / "Contents/MacOS/Narwhal3D")], env=env,
                                   cwd=work, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 90
            ready = False
            while process.poll() is None and time.monotonic() < deadline:
                if "[narwhal-welcome] runtime ready" in log_path.read_text(errors="replace"):
                    ready = True
                    break
                time.sleep(.25)
            if not ready:
                raise RuntimeError("Actual app/Welcome did not reach runtime readiness:\n" + log_path.read_text(errors="replace")[-6000:])
            print("PASS: actual mounted app launcher, native Welcome and runtime readiness", flush=True)
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    assessment = subprocess.run(["spctl", "--assess", "--type", "execute", "--verbose=2", str(bundle)], capture_output=True, text=True, timeout=30)
    receipt = {"launcher_ready": ready, "real_runtime": True,
               "cleanup": "owned disposable process group terminated; not a graceful-quit test",
               "gatekeeper_exit": assessment.returncode,
               "gatekeeper_output": (assessment.stdout + assessment.stderr)[-4000:],
               "signing_boundary": "ad-hoc, not Developer ID/notarized; interactive download approval is not certified"}
    (work / "launcher-result.json").write_text(json.dumps(receipt, indent=2))
    print("Gatekeeper assessment:", assessment.returncode, receipt["gatekeeper_output"], flush=True)
finally:
    subprocess.run(["hdiutil", "detach", volume["dev-entry"]], check=True, timeout=60)
