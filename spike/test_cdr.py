#!/usr/bin/env python3
"""Native CorelDRAW writer: package, RIFF records, and libcdr decode gate."""
from __future__ import annotations

import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import xml.etree.ElementTree as ET
import zipfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDR_CRATE = os.path.join(ROOT, "native", "cdr_core")
_build = subprocess.run(
    ["cargo", "+1.95.0", "build", "--release", "--locked", "--offline"],
    cwd=CDR_CRATE, capture_output=True, text=True,
)
if _build.returncode:
    raise SystemExit(_build.stderr or _build.stdout)
CDR_LIBRARY = os.path.join(CDR_CRATE, "target", "release", "libnarwhal_cdr_core.so")
PRIOR_CDR_CORE = os.environ.get("NARWHAL_CDR_CORE")
os.environ["NARWHAL_CDR_CORE"] = CDR_LIBRARY
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name):
    path = os.path.join(ROOT, "narwhal3d", name + ".py")
    spec = importlib.util.spec_from_file_location("narwhal3d." + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ve = load("vector_export")
native_cdr = load("native_cdr")
cdr = load("cdr_writer")
reader = load("cdr_reader")
TMP = tempfile.mkdtemp(prefix="narwhal_cdr_")
checks = 0
failures = []


def check(condition, label, detail=""):
    global checks
    checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(f"{label}: {detail}")


strokes = [
    ve.Stroke(
        beziers=(((10.0, 20.0), (20.0, 35.0), (30.0, 5.0), (40.0, 20.0)),),
        rgb=(1.0, 0.0, 0.0), width_mm=0.25, layer="Cut & Score"),
    ve.Stroke(
        beziers=(
            ((55.0, 15.0), (60.0, 15.0), (65.0, 15.0), (70.0, 15.0)),
            ((70.0, 15.0), (70.0, 25.0), (70.0, 35.0), (70.0, 45.0)),
            ((70.0, 45.0), (65.0, 45.0), (60.0, 45.0), (55.0, 45.0)),
            ((55.0, 45.0), (55.0, 35.0), (55.0, 25.0), (55.0, 15.0)),
        ),
        closed=True, rgb=(0.0, 0.0, 1.0), width_mm=0.5, layer="Outline"),
]
path = os.path.join(TMP, "native.cdr")
size = cdr.write_cdr(path, strokes, 100.0, 80.0,
                     "Native CDR gate", hairline=True)
raw = open(path, "rb").read()

check(size == len(raw) and size > 0,
      "direct native writer produces the genuine CDR file", (size, len(raw)))
check(native_cdr.status() == f"ready: {CDR_LIBRARY}",
      "detailed CDR checks are bound to the exact source-built Rust library",
      native_cdr.status())
check(not raw.startswith((b"%PDF", b"<svg")), "CDR is not disguised PDF or SVG", raw[:16])

with zipfile.ZipFile(path) as archive:
    names = archive.namelist()
    check(names[0] == "mimetype", "mimetype is the first package member", names)
    check(archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED,
          "mimetype is stored, not deflated")
    check(archive.read("mimetype").decode() == cdr.MIMETYPE,
          "package declares the CorelDRAW MIME type")
    riff = archive.read("content/riffData.cdr")
    metadata = archive.read("metadata/metadata.xml").decode()
    textinfo = archive.read("metadata/textinfo.xml").decode()

check(riff[:4] == b"RIFF" and riff[8:12] == b"CDRF",
      "document payload is a Corel CDRF RIFF stream", riff[:16])
check(struct.unpack_from("<I", riff, 4)[0] == len(riff) - 8,
      "RIFF byte count is exact")
check(riff[12:16] == b"vrsn" and struct.unpack_from("<H", riff, 20)[0] == 1500,
      "stream identifies native CorelDRAW v15")
check(b"LIST" in riff and b"doc " in riff and b"page" in riff
      and b"layr" in riff and b"obj " in riff and b"loda" in riff,
      "stream contains document, page, layer, object, and path records")
check("Cut & Score".encode("utf-16le") + b"\0\0" in riff
      and "Outline".encode("utf-16le") + b"\0\0" in riff,
      "native layer descriptors carry Corel UTF-16LE names")
check("<PageWidth>1000000</PageWidth>" in metadata
      and "<PageHeight>800000</PageHeight>" in metadata,
      "metadata records the 100 x 80 mm page")
check("<Hairline>2</Hairline>" in metadata,
      "metadata identifies both native Hairline outlines")
check("Cut &amp; Score" in textinfo and "Outline" in textinfo,
      "layer names are XML-safe and retained", textinfo)

drawing = reader.read_cdr(path)
check(len(drawing.objects) == 2
      and [obj.layer for obj in drawing.objects] == ["Cut & Score", "Outline"],
      "native reader recovers object boundaries and layer names",
      [(obj.layer, len(obj.paths)) for obj in drawing.objects])
check(all(obj.hairline and abs(obj.width_mm - 0.0762) < 1e-12
          for obj in drawing.objects),
      "native Hairline round-trips as exactly 0.003 inch")
check([obj.rgb for obj in drawing.objects] == [(255, 0, 0), (0, 0, 255)],
      "native reader recovers per-object outline colors")

# Independent decode: LibreOffice's CDR filter is backed by libcdr.  This proves
# our generated records are interpreted as vector paths, not merely ZIP-valid.
soffice = shutil.which("soffice")
if soffice:
    outdir = os.path.join(TMP, "decoded")
    profile = os.path.join(TMP, "profile")
    os.makedirs(outdir)
    os.makedirs(profile)
    command = [
        soffice, "-env:UserInstallation=file://" + profile,
        "--headless", "--norestore", "--nodefault", "--nolockcheck",
        "--convert-to", "svg", "--outdir", outdir, path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    svg_path = os.path.join(outdir, "native.svg")
    check(result.returncode == 0 and os.path.isfile(svg_path),
          "libcdr accepts and converts the native CDR", result.stderr + result.stdout)
    if os.path.isfile(svg_path):
        root = ET.parse(svg_path).getroot()
        ns = "{http://www.w3.org/2000/svg}"
        visible = [node for node in root.iter(ns + "path") if node.get("stroke") not in (None, "none")]
        colors = {node.get("stroke") for node in visible}
        check(len(visible) == 2, "libcdr recovers both vector curves", len(visible))
        check(colors == {"rgb(255,0,0)", "rgb(0,0,255)"},
              "libcdr recovers both RGB outline colors", colors)
        check(any("C " in (node.get("d") or "") for node in visible),
              "the free-form segment remains a cubic Bezier")
        check(root.get("width") == "100mm" and root.get("height") == "80mm",
              "libcdr recovers physical page dimensions",
              (root.get("width"), root.get("height")))
else:
    print("  SKIP independent libcdr decode: soffice is not installed")



print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for failure in failures:
        print("FAIL:", failure)
    sys.exit(1)
print("NATIVE CDR WRITER PASSED")
if PRIOR_CDR_CORE is None:
    os.environ.pop("NARWHAL_CDR_CORE", None)
else:
    os.environ["NARWHAL_CDR_CORE"] = PRIOR_CDR_CORE
