#!/usr/bin/env python3
"""Stage a signed Fedora Inkscape RPM closure into the local Linux runtime.

This is a Linux development/release-build helper, never run by normal app launch.
First obtain the closure without installing it system-wide::

    dnf download --resolve --destdir /tmp/narwhal-inkscape-rpms inkscape

Then run this script with that directory. Every RPM must pass ``rpm -K`` before
anything is extracted. The generated sysroot, wrapper and inventory live under
ignored ``runtime/linux-x86_64/``; no binary enters Git.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified(path: Path) -> str:
    done = subprocess.run(["rpm", "-K", str(path)], check=False,
                          capture_output=True, text=True)
    detail = (done.stdout or done.stderr).strip()
    if done.returncode or "signatures OK" not in detail:
        raise SystemExit(f"RPM signature verification failed for {path.name}: {detail}")
    return detail


def _extract(path: Path, sysroot: Path) -> None:
    producer = subprocess.Popen(["rpm2cpio", str(path)], stdout=subprocess.PIPE)
    consumer = subprocess.Popen(["cpio", "-idmu", "--quiet"], cwd=sysroot,
                                stdin=producer.stdout, stdout=subprocess.DEVNULL)
    if producer.stdout is not None:
        producer.stdout.close()
    consumer_status = consumer.wait()
    producer_status = producer.wait()
    if producer_status or consumer_status:
        raise SystemExit(f"failed to extract signed RPM {path.name}")


def _wrapper(path: Path) -> None:
    path.write_text("""#!/bin/sh
set -eu
BASE="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
SYSROOT="$BASE/sysroot"
export LD_LIBRARY_PATH="$SYSROOT/usr/lib64:$SYSROOT/usr/lib64/inkscape${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export XDG_DATA_DIRS="$SYSROOT/usr/share${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"
export PYTHONPATH="$SYSROOT/usr/lib/python3.14/site-packages${PYTHONPATH:+:$PYTHONPATH}"
exec "$SYSROOT/usr/bin/inkscape" "$@"
""", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rpm_dir", help="directory produced by dnf download --resolve")
    parser.add_argument("--platform", default="linux-x86_64")
    args = parser.parse_args(argv)
    if args.platform != "linux-x86_64":
        raise SystemExit("Fedora Inkscape staging currently supports linux-x86_64 only")

    rpm_dir = Path(args.rpm_dir).expanduser().resolve()
    rpms = sorted(rpm_dir.glob("*.rpm"))
    names = {path.name for path in rpms}
    if not rpms or not any(name.startswith("inkscape-") for name in names) \
            or not any(name.startswith("inkscape-libs-") for name in names):
        raise SystemExit("RPM directory must contain inkscape and inkscape-libs plus its closure")
    for path in rpms:
        _verified(path)

    runtime = _root() / "runtime" / args.platform
    sysroot = runtime / "sysroot"
    bin_dir = runtime / "bin"
    sysroot.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    for path in rpms:
        _extract(path, sysroot)
    executable = sysroot / "usr" / "bin" / "inkscape"
    if not executable.is_file():
        raise SystemExit("signed RPM closure produced no usr/bin/inkscape")
    wrapper = bin_dir / "inkscape"
    _wrapper(wrapper)

    done = subprocess.run([str(wrapper), "--version"], check=False,
                          capture_output=True, text=True, timeout=120)
    if done.returncode or "Inkscape" not in done.stdout:
        raise SystemExit(f"bundled Inkscape smoke test failed: {(done.stderr or done.stdout).strip()}")

    inventory_path = runtime / "inventory.json"
    inventory = (json.loads(inventory_path.read_text(encoding="utf-8"))
                 if inventory_path.is_file() else {"schema": 1, "platform": args.platform,
                                                    "complete": False, "tools": {}})
    inventory.setdefault("tools", {})["inkscape"] = {
        "executable": "inkscape",
        "present": True,
        "verified": True,
        "verification": "Fedora repository RPM signatures (rpm -K)",
        "version": done.stdout.strip(),
        "packages": [{"file": path.name, "sha256": _sha256(path)} for path in rpms],
    }
    inventory["complete"] = all(
        inventory.get("tools", {}).get(tool, {}).get("verified", False)
        for tool in ("inkscape", "pdfinfo", "gs", "soffice"))
    inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
    print(done.stdout.strip())
    print(f"staged {len(rpms)} signed RPMs in {runtime}")
    print("runtime remains incomplete until pdfinfo, gs and soffice are also bundled/verified"
          if not inventory["complete"] else "runtime bundle complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
