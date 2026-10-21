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
import zlib


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
    thumbnail = archive.read("metadata/thumbnails/thumbnail.bmp")
    page_preview = archive.read("metadata/thumbnails/page1.bmp")
    check(archive.testzip() is None and len(names) == 9
          and "metadata/thumbnails/thumbnail.bmp" in names
          and "metadata/thumbnails/page1.bmp" in names,
          "package has nine CRC-valid reference-class entries including previews", names)
    bmp_valid = all(image[:2] == b"BM"
                    and struct.unpack_from("<I", image, 2)[0] == len(image)
                    and any(byte != 0xFF for byte in image[54:])
                    for image in (thumbnail, page_preview))
    check(bmp_valid, "generated BMP previews contain rendered artwork")

check(riff[:4] == b"RIFF" and riff[8:12] == b"CDRF",
      "document payload is a Corel CDRF RIFF stream", riff[:16])
check(struct.unpack_from("<I", riff, 4)[0] == len(riff) - 8,
      "RIFF byte count is exact")
check(riff[12:16] == b"vrsn" and struct.unpack_from("<H", riff, 20)[0] == 1500,
      "stream identifies native CorelDRAW v15")

pos, compressed_records, compressed_tables, cmpr_count = 12, [], [], 0
while pos + 8 <= len(riff):
    length = struct.unpack_from("<I", riff, pos + 4)[0]
    payload = riff[pos + 8:pos + 8 + length]
    if riff[pos:pos + 4] == b"LIST" and payload[:4] == b"cmpr":
        cmpr_count += 1
        span = struct.unpack_from("<I", payload, 4)[0]
        expected = struct.unpack_from("<I", payload, 8)[0]
        records = zlib.decompress(payload[28:28 + span - 8])
        lengths = zlib.decompress(payload[28 + span:])
        check(len(records) == expected and len(lengths) % 4 == 0,
              "cmpr record and length-table streams inflate to declared sizes")
        compressed_records.append(records)
        compressed_tables.append([value[0] for value in struct.iter_unpack("<I", lengths)])
    pos += 8 + length + (length & 1)
expanded = b"".join(compressed_records)
check(cmpr_count == 2 and riff.count(b"CPng\x01\x00\x04\x00") == 4,
      "stream uses the reference two-cmpr/four-CPng compressed structure")
check(all(tag in expanded for tag in (b"doc ", b"page", b"layr", b"obj ", b"loda")),
      "compressed stream contains document, page, layer, object, and path records")
check("Cut & Score".encode("utf-16le") + b"\0\0" in expanded
      and "Outline".encode("utf-16le") + b"\0\0" in expanded,
      "native layer descriptors carry Corel UTF-16LE names")
expanded_nodes = []
for records, lengths in zip(compressed_records, compressed_tables):
    expanded_nodes.extend(reader._records(records, 0, len(records), lengths))
mcfg_nodes = list(reader._all(expanded_nodes, b"mcfg"))
pages = [node for node in expanded_nodes if node.tag == b"LIST" and node.kind == b"page"]
objects = [node for node in reader._all(expanded_nodes, b"LIST") if node.kind == b"obj "]
check(len(mcfg_nodes) == 1 and len(mcfg_nodes[0].payload) == 8323
      and len(pages) == 2 and len(objects) == 2
      and all({b"flgs", b"bbox", b"obbx", b"usdn", b"LIST"}
              <= {child.tag for child in obj.children} for obj in objects),
      "Corel-valid RIFF has fixed mcfg, master+document pages, and full object wrappers")
object_loda = next(reader._all(objects[0].children, b"loda"))
argument_count = struct.unpack_from("<I", object_loda.payload, 4)[0]
offsets_at, types_at = struct.unpack_from("<II", object_loda.payload, 8)
offsets = struct.unpack_from(f"<{argument_count + 1}I", object_loda.payload, offsets_at)
_kind, object_args = reader._args(object_loda.payload)
shape = object_args[0x1E]
node_count = struct.unpack_from("<H", shape)[0]
check(types_at == offsets_at + 4 * (argument_count + 1)
      and offsets[-1] == len(object_loda.payload)
      and len(shape) == 4 + 9 * node_count + 8 and shape[-8:] == b"\0" * 8,
      "v15 loda has count+1 bounded offsets and curve coordinates have the Corel trailer")
box = next(child for child in objects[0].children if child.tag == b"bbox")
obox = next(child for child in objects[0].children if child.tag == b"obbx")
left, top, right, bottom = struct.unpack("<4i", box.payload)
outer = struct.unpack("<8i", obox.payload)[4:]
check(left <= right and top >= bottom
      and outer[0] < left and outer[1] > top
      and outer[2] > right and outer[3] < bottom,
      "Corel object bounds are left/top/right/bottom with outline-expanded obbx")
check("<PageWidth>1000000</PageWidth>" in metadata
      and "<PageHeight>800000</PageHeight>" in metadata,
      "metadata records the 100 x 80 mm page")
check("<Hairline>2</Hairline>" in metadata,
      "metadata identifies both native Hairline outlines")
check("Cut &amp; Score" in textinfo and "Outline" in textinfo,
      "layer names are XML-safe and retained", textinfo)

inch_page_path = os.path.join(TMP, "20x30-inch.cdr")
cdr.write_cdr(inch_page_path, strokes, 508.0, 762.0, "20 x 30 inch", hairline=True)
with zipfile.ZipFile(inch_page_path) as archive:
    inch_metadata = archive.read("metadata/metadata.xml").decode()
inch_drawing = reader.read_cdr(inch_page_path)
check(inch_drawing.width_mm == 508.0 and inch_drawing.height_mm == 762.0
      and "<PageDimensions>20 x 30 in</PageDimensions>" in inch_metadata,
      "20 x 30 inch package reports exactly 508 x 762 mm and authored inch dimensions")

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

register_path = os.path.join(TMP, "register-fill.cdr")
register = ve.Stroke(beziers=strokes[1].beziers, closed=True,
                     rgb=(0.0, 0.0, 0.0), width_mm=0.25, layer="Register")
cdr.write_cdr(register_path, [strokes[0], register], 100.0, 80.0,
              "Registration fill", hairline=True)
register_drawing = reader.read_cdr(register_path)
with zipfile.ZipFile(register_path) as archive:
    register_riff = archive.read("content/riffData.cdr")
    register_metadata = archive.read("metadata/metadata.xml").decode()
state, expanded_state, register_nodes = [0], [0], []
for node in reader._records(register_riff, 12, len(register_riff), state=state):
    if node.tag == b"LIST" and node.kind == b"cmpr":
        register_nodes.extend(reader._decompress(node, state, expanded_state))
register_objects = [node for node in reader._all(register_nodes, b"LIST")
                    if node.kind == b"obj "]
_register_kind, register_args = reader._args(
    next(reader._all(register_objects[1].children, b"loda")).payload)
register_shape = register_args[0x1E]
register_nodes_count = struct.unpack_from("<H", register_shape)[0]
register_types = register_shape[4 + 8 * register_nodes_count:4 + 9 * register_nodes_count]
check(register_drawing.objects[0].hairline
      and register_drawing.objects[0].rgb == (255, 0, 0)
      and register_drawing.objects[1].layer == "Register"
      and register_drawing.objects[1].rgb == (0, 0, 0)
      and register_drawing.objects[1].width_mm == 0.0
      and int.from_bytes(register_args[0x14], "little") == 2
      and int.from_bytes(register_args[0x0A], "little") == 2
      and register_types[0] == 0x0C and register_types[-1] == 0x88
      and all(register_types[index] == 0x84
              for index in range(3, len(register_types) - 1, 3))
      and "<Hairline>1</Hairline>" in register_metadata,
      "closed black Register path is fill 2 with no outline; Cut remains Hairline")

metadata_less_path = os.path.join(TMP, "metadata-less-native.cdr")
with zipfile.ZipFile(metadata_less_path, "w") as archive:
    archive.writestr("content/riffData.cdr", riff)
metadata_less = reader.read_cdr(metadata_less_path)
check(metadata_less.width_mm == 100.0 and metadata_less.height_mm == 80.0
      and len(metadata_less.objects) == 2
      and [obj.rgb for obj in metadata_less.objects] == [(255, 0, 0), (0, 0, 255)],
      "native reader derives mcfg dimensions and curves without package metadata")
check(native_cdr.read_v15(riff, b"") is not None,
      "source-built Rust v15 reader accepts an empty metadata span natively")

opaque = (b"LIST" + struct.pack("<I", 12) + b"stlt"
          + bytes((5, 0, 0, 0, 2, 0, 0, 0)))
opaque_nodes = reader._records(opaque, 0, len(opaque))
check(len(opaque_nodes) == 1 and opaque_nodes[0].kind == b"stlt"
      and not opaque_nodes[0].children,
      "Python compatibility reader also treats real-v15 stlt as opaque")

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
