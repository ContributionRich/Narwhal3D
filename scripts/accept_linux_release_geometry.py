#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise exact solids in an extracted Linux AppImage, without developer overrides.

The 0.4.9 AppImage shipped without the exact-save helper: a large solid became a
triangulated document stand-in, the document observer refused it, and every later
command (for example Curve) failed. Static verification could not see that, and the
source suite always had a local helper. This gate runs the PACKAGED runtime with a
clean environment and requires: the packaged helper is the one discovered, a large
torus is stored exactly, and the model can still snapshot after the torus and a curve.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PROBE = r'''
import os, sys
root = os.environ["NARWHAL_ACCEPT_ROOT"]
sys.path.insert(0, root)
from narwhal3d import bridge, kernel, model
from narwhal3d.kernel import onbrep
expected = os.path.join(root, "narwhal3d", "kernel", "bin", "brep_writer-linux-x86_64")
found = onbrep.helper_path()
print("ACCEPT helper", found)
assert found and os.path.realpath(found) == os.path.realpath(expected), (found, expected)
m = model.NarwhalModel()
m.add_object(bridge.make_polyline([(0, 0, 0), (5, 5, 0), (10, 0, 0), (15, 4, 0)], degree=3), "Curve1")
torus = bridge.make_torus((40, 0, 0), 100, 30)
form = torus.GetUserString(kernel.DOCUMENT_FORM_KEY)
print("ACCEPT torus form", form, "faces", len(torus.Faces))
assert form == "exact" and len(torus.Faces) == 1, (form, len(torus.Faces))
m.add_object(torus, "Torus1")
m.add_object(bridge.make_polyline([(0, 20, 0), (5, 25, 0), (10, 20, 0), (15, 24, 0)], degree=3), "Curve2")
m.snapshot()
print("ACCEPT snapshot ok objects", len(m.file.Objects))
assert len(m.file.Objects) == 3
print("ACCEPT_GEOMETRY_PASSED")
'''


def app_environment(app: Path) -> dict[str, str]:
    native = app / "narwhal3d/native"
    runtime_bin = app / "runtime/linux-x86_64/bin"
    occt = app / "tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs"
    env = {"HOME": os.environ.get("HOME", "/tmp"), "PATH": f"{runtime_bin}:/usr/bin:/bin",
           "LC_ALL": "C", "TZ": "UTC", "LD_LIBRARY_PATH": str(occt), "NARWHAL_ACCEPT_ROOT": str(app),
           "NARWHAL_DOCUMENT_CORE": str(runtime_bin / "narwhal-document-core"),
           "NARWHAL_CDR_CORE": str(runtime_bin / "libnarwhal_cdr_core.so")}
    for variable, name in (("NARWHAL_PURE_CORE", "pure_core"), ("NARWHAL_SELECTION_CORE", "selection_core"),
                           ("NARWHAL_GEOMETRY_CORE", "geometry_core"), ("NARWHAL_PLATFORM_CORE", "platform_core"),
                           ("NARWHAL_APPLICATION_CORE", "application_core")):
        env[variable] = str(native / f"libnarwhal_{name}.so")
    env["NARWHAL_UI_CORE_LIBRARY"] = str(native / "libnarwhal_ui_core.so")
    return env  # deliberately no NARWHAL3D_BREP_WRITER


def accept(app: Path) -> None:
    blender = app / "tools/blender-patched/blender"
    if not blender.is_file():
        raise SystemExit(f"packaged runtime not found: {blender}")
    with tempfile.TemporaryDirectory(prefix="narwhal-accept-geometry-") as work:
        probe = Path(work) / "probe.py"
        probe.write_text(PROBE, encoding="utf-8")
        result = subprocess.run([str(blender), "-b", "--factory-startup", "--python-exit-code", "1",
                                 "--python", str(probe)], env=app_environment(app), cwd=work,
                                text=True, capture_output=True, timeout=600)
    lines = [line for line in (result.stdout + result.stderr).splitlines()
             if line.startswith("ACCEPT") or "Error" in line or "assert" in line.lower()]
    print("\n".join(lines))
    if result.returncode != 0 or "ACCEPT_GEOMETRY_PASSED" not in result.stdout:
        raise SystemExit("packaged exact-solid acceptance FAILED")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path, help="AppImage or extracted AppDir")
    args = parser.parse_args()
    release = args.release.resolve()
    if release.is_dir():
        accept(release / "usr/lib/narwhal3d")
        return 0
    with tempfile.TemporaryDirectory(prefix="narwhal-accept-extract-") as work:
        subprocess.run([str(release), "--appimage-extract"], cwd=work, check=True, timeout=300,
                       stdout=subprocess.DEVNULL, env=dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1"))
        accept(Path(work) / "squashfs-root/usr/lib/narwhal3d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
