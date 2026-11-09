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
# TracePicture: the PACKAGED helper must be found and must trace a placed picture into
# closed curves. 0.4.11 shipped without it, so the command only said it was missing.
import struct, tempfile, zlib
import rhino3dm as r3
from narwhal3d import picture, trace_picture as tp
tracer = os.path.join(root, "runtime", "linux-x86_64", "bin", "narwhal-picture-trace")
found = tp.helper_path()
print("ACCEPT tracer", found)
assert found and os.path.realpath(found) == os.path.realpath(tracer), (found, tracer)
size = 64
rows = b"".join(b"\x00" + b"".join(
    bytes((30, 30, 30, 255)) if (x - 31.5) ** 2 + (y - 31.5) ** 2 <= 400 else bytes(4)
    for x in range(size)) for y in range(size))
def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
image = os.path.join(tempfile.mkdtemp(), "disc.png")
with open(image, "wb") as stream:
    stream.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
                 + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
frame = picture.make_frame(image, (0.0, 0.0, 0.0), (64.0, 0.0, 0.0), (0.0, 64.0, 0.0))
settings = {"offset": 0.0, "min_hole": 1.0, "inner_cuts": True, "background": "Auto",
            "color": "#ffffff", "color_tolerance": 12.0, "smoothness": 40.0}
cut = tp.trace(found, tp.helper_args(image, size, frame, 1 / 25.4, settings))
controls = tp.loop_controls(cut, frame)
curves = [tp.bezier_loop(r3, ctrl) for ctrl in controls]
print("ACCEPT trace loops", len(curves), "closed", [c.IsClosed for c in curves])
assert len(curves) == 1 and curves[0].IsClosed
box = curves[0].GetBoundingBox()
assert abs((box.Max.X - box.Min.X) - 40.0) < 2.0 and abs((box.Max.Y - box.Min.Y) - 40.0) < 2.0, box
print("ACCEPT_TRACE_PASSED")
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
