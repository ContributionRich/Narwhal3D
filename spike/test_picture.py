"""`Picture` -- an image on an exact planar surface.

Runs under the bpy-free interpreter (everything but the display mesh) AND under
`blender -b` (adds the display-mesh UV gates, which need bmesh). Every gate FAILS on the
earlier code: there was no `Picture` command, no image reader, no frame, no Picture
material (watched: `commands.COMMANDS.get("picture")` is None -> every drive is RED).

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_picture.py
     tools/blender-5.2.2-linux-x64/blender -b --python spike/test_picture.py
"""
import importlib.util
import math
import os
import struct
import subprocess
import sys
import tempfile
import types
import zlib

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cplane = _load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
analysis = _load("narwhal3d.analysis", "narwhal3d/analysis.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
filedialog = _load("narwhal3d.filedialog", "narwhal3d/filedialog.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
try:
    picture = _load("narwhal3d.picture", "narwhal3d/picture.py")
except FileNotFoundError:
    picture = None

try:
    import bmesh  # noqa: F401
    HAVE_BMESH = True
except ImportError:
    HAVE_BMESH = False

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


# ------------------------------------------------------------------ test images
TMP = tempfile.mkdtemp(prefix="g79_")


def write_png(path, w, h, ppm=None):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    raw = b"".join(b"\x00" + b"\x80\x40\x20" * w for _ in range(h))
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    if ppm:
        data += chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
    data += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    open(path, "wb").write(data)
    return path


def write_jpeg_header(path, w, h, dpi=None):
    """SOI, a JFIF APP0 with density, an SOF0 frame header, EOI -- what the reader reads."""
    app0 = b"JFIF\x00\x01\x02" + (struct.pack(">BHH", 1, dpi, dpi) if dpi
                                     else struct.pack(">BHH", 0, 1, 1)) + b"\x00\x00"
    sof = struct.pack(">BHHB", 8, h, w, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    data = (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
            + b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xd9")
    open(path, "wb").write(data)
    return path


PNG = write_png(os.path.join(TMP, "img.png"), 400, 200)
PNG300 = write_png(os.path.join(TMP, "dpi.png"), 600, 300, ppm=11811)   # 299.9994 dpi
JPG300 = write_jpeg_header(os.path.join(TMP, "photo.jpg"), 600, 400, dpi=300)
GIF = os.path.join(TMP, "anim.gif")
open(GIF, "wb").write(b"GIF89a" + b"\x00" * 20)

# ------------------------------------------------------------------ header reader
print("-- the image header reader --")
if picture is not None:
    i = picture.read_image_info(PNG)
    check((i.format, i.width, i.height, i.dpi_declared) == ("PNG", 400, 200, False),
          f"PNG IHDR: 400 x 200, no density declared ({i})")
    i = picture.read_image_info(PNG300)
    check(abs(i.dpi_x - 11811 * 0.0254) < 1e-9 and i.dpi_declared,
          f"PNG pHYs: 11811 px/m = {11811 * 0.0254:.4f} dpi ({i.dpi_x})")
    i = picture.read_image_info(JPG300)
    check((i.format, i.width, i.height, i.dpi_x) == ("JPEG", 600, 400, 300.0),
          f"JPEG SOF0 + JFIF: 600 x 400 at 300 dpi ({i})")
    for bad, name in ((GIF, "GIF"),):
        try:
            picture.read_image_info(bad)
            msg = ""
        except ValueError as e:
            msg = str(e)
        check(msg.startswith(f"{name} images are not supported yet"),
              f"{name} is refused BY NAME ({msg!r})")
else:
    check(False, "narwhal3d/picture.py exists")

# ------------------------------------------------------------------ the command
print("\n-- the Picture command --")


class FakeObj:
    def __init__(self, name):
        self.name = name


class Ctx:
    """NarwhalContext's document half: add_object + the Picture material, as add_geometry."""

    def __init__(self, plane=None):
        self.model = model.NarwhalModel()
        self.plane = plane
        self.n = 0
        self.ids = {}

    def add_geometry(self, geom, base_name, layer_index=None):
        self.n += 1
        nm = f"{base_name}_{self.n}"
        obj_id, _a = self.model.add_object(geom, nm, 0)
        commands._assign_picture_material(self.model, obj_id, geom)
        self.ids[nm] = obj_id
        return obj_id, nm

    def active_cplane(self):
        return self.plane

    def selected_objects(self):
        return []

    def note_change(self, label=None):
        pass

    def geom_by_name(self, name):
        for o in self.model.file.Objects:
            if o.Attributes.Name == name:
                return o.Geometry
        return None


def dialog(status, path=None):
    filedialog.ask_open_path = lambda title, start, filters=None: (status, path)


def draw(lines, path=PNG, plane=None, status=None):
    dialog(status or filedialog.OK, path)
    ctx = Ctx(plane)
    proto = commands.COMMANDS.get("picture")
    if proto is None:
        return ctx, None, "(no Picture command)", True
    c = type(proto)()
    msg, done = c.begin(ctx)
    for line in lines:
        if done:
            break
        msg, done = c.feed(line, ctx)
    g = [o.Geometry for o in ctx.model.file.Objects]
    return ctx, (g[0] if len(g) == 1 else None), msg, done


def extent(g):
    lo, hi = analysis.bounding_box(g)
    return (round(lo.X, 9) + 0, round(lo.Y, 9) + 0, round(lo.Z, 9) + 0,
            round(hi.X, 9) + 0, round(hi.Y, 9) + 0, round(hi.Z, 9) + 0)


ctx, g, msg, done = draw(["0,0,0", "10,0,0"])
check(g is not None and extent(g) == (0, 0, 0, 10, 5, 0),
      f"a 400x200 PNG, corners (0,0) and (10,0): a 10 x 5 surface ({g and extent(g)}; {msg!r})")
check(g is not None and len(g.Faces) == 1 and g.Faces[0].IsPlanar(),
      "...ONE exact planar face (nothing to facet)")
frame = bridge.picture_frame(g) if g is not None else None
check(frame is not None and frame["image"] == PNG and frame["u"] == [10, 0, 0]
      and frame["v"] == [0, 5, 0], f"...carrying its image frame ({frame})")
mat = ctx.model.object_material(ctx.ids.get("Picture_1")) if g is not None else None
tex = mat.GetBitmapTexture() if mat is not None else None
check(mat is not None and mat.Name == "img.png" and tex is not None and tex.FileName == PNG,
      f"a Picture material named after the file, its texture the image path "
      f"({mat and mat.Name!r}, {tex and tex.FileName!r})")
ctx2, g2, msg2, _d = draw(["0,0,0", "4,7,0"])
check(g2 is not None and extent(g2) == (0, 0, 0, 14, 7, 0),
      f"the aspect is LOCKED: a pick at (4,7) gives 14 x 7, reaching the corner "
      f"({g2 and extent(g2)})")
ctx3, g3, msg3, _d = draw(["0,0,0", "8"])
check(g3 is not None and extent(g3) == (0, 0, 0, 8, 4, 0), f"a typed length 8: 8 x 4 ({msg3!r})")
_c, g4, msg4, _d = draw(["Center", "0,0,0", "5,1,0"])
check(g4 is not None and extent(g4) == (-5, -2.5, 0, 5, 2.5, 0),
      f"Center (0,0) + corner (5,1): 10 x 5 about the centre ({g4 and extent(g4)})")
_c, g5, msg5, _d = draw(["3Point", "0,0,0", "4,0,0", "1,-1,0"])
check(g5 is not None and extent(g5) == (0, -2, 0, 4, 0, 0),
      f"3Point (0,0)-(4,0), side below: 4 x 2 on that side ({g5 and extent(g5)})")
_c, g6, msg6, _d = draw(["Vertical", "0,0,0", "6,0,0"])
check(g6 is not None and extent(g6) == (0, 0, 0, 6, 0, 3),
      f"Vertical: standing up the CPlane normal, 6 x 3 ({g6 and extent(g6)})")
_c, g7, msg7, _d = draw(["1to1", "0,0,0"], path=JPG300)
check(g7 is not None and extent(g7) == (0, 0, 0, 50.8, 33.866666667, 0),
      f"1to1: a 600 px image at 300 dpi is 2 in = 50.8 mm wide ({g7 and extent(g7)}; {msg7!r})")
_c, g8, msg8, _d = draw(["1to1", "0,0,0"], path=PNG)
check(g8 is not None and "72 dpi was assumed" in (msg8 or ""),
      f"an image with NO density says so when 1to1 has to assume ({msg8!r})")

print("\n-- the file dialog paths --")
ctx, g, msg, done = draw([], status=filedialog.CANCEL)
check(done and "cancelled" in (msg or ""), f"dialog cancelled -> command cancelled ({msg!r})")
ctx, g, msg, done = draw([GIF], status=filedialog.UNAVAILABLE)
check(not done and "GIF images are not supported yet" in (msg or ""),
      f"no dialog: a typed path; an unsupported one RE-PROMPTS by name ({msg!r})")
ctx, g, msg, done = draw([JPG300, "0,0,0", "6,0,0"], status=filedialog.UNAVAILABLE)
check(g is not None and extent(g) == (0, 0, 0, 6, 4, 0),
      f"a typed JPEG path works: 6 x 4 for a 3:2 image ({msg!r})")

# ------------------------------------------------------------------ the frame / UVs
print("\n-- the frame keeps the image registered --")
ctx, g, msg, done = draw(["0,0,0", "10,0,0"])
if g is not None and picture is not None:
    f = bridge.picture_frame(g)
    check(picture.uv_of(f, (0, 0, 0)) == (0.0, 0.0) and picture.uv_of(f, (10, 5, 0)) == (1.0, 1.0)
          and picture.uv_of(f, (5, 2.5, 0)) == (0.5, 0.5),
          "UV (0,0) at the origin corner, (1,1) at the far corner, (0.5,0.5) at the centre")
    bridge.transform_geometry(g, r3.Transform.Rotation(math.pi / 2, r3.Vector3d(0, 0, 1),
                                                       r3.Point3d(0, 0, 0)))
    f2 = bridge.picture_frame(g)
    uv = picture.uv_of(f2, (0, 10, 0))
    check(abs(uv[0] - 1) < 1e-12 and abs(uv[1]) < 1e-12,
          f"after Rotate 90 the frame follows: the old (10,0) corner, now at (0,10), is still "
          f"UV (1,0) ({uv})")
else:
    check(False, "a picture to test the frame on")

print("\n-- persistence: saved here, read in a FRESH process --")
ctx, g, msg, done = draw(["0,0,0", "10,0,0"])
path = os.path.join(TMP, "pic.3dm")
ctx.model.save(path)
READER = f"""
import json, rhino3dm as r3
f = r3.File3dm.Read({path!r})
o = f.Objects[0]
m = f.Materials[o.Attributes.MaterialIndex] if o.Attributes.MaterialIndex >= 0 else None
print(json.dumps([m and m.Name, m and m.GetBitmapTexture().FileName,
                  json.loads(o.Geometry.GetUserString("Narwhal3D:Picture") or "null"),
                  str(o.Attributes.MaterialSource)]))
"""
out = subprocess.run([sys.executable if not HAVE_BMESH else
                      os.path.join(ROOT, "tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13"),
                      "-c", READER], capture_output=True, text=True)
import json  # noqa: E402

try:
    name, texfile, fr, src = json.loads(out.stdout)
except Exception:  # noqa: BLE001
    name = texfile = fr = src = None
check(name == "img.png" and texfile == PNG and src.endswith("MaterialFromObject"),
      f"the material, its image path and its assignment survive ({out.stdout.strip()[:120]!r} "
      f"{out.stderr.strip()[-160:]!r})")
check(fr is not None and fr["u"] == [10, 0, 0] and fr["v"] == [0, 5, 0] and fr["image"] == PNG,
      "...and so does the image frame")

if HAVE_BMESH:
    import bmesh
    import bpy

    print("\n-- imported Picture replacements inherit material-derived registration --")
    IMPORTED_FRAME = picture.make_frame(PNG, (2, 3, 0), (10, 0, 0), (0, 8, 0))

    def imported_picture_context():
        for ob in list(bpy.data.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
        rm = model.NarwhalModel()
        plane = r3.Plane(r3.Point3d(2, 3, 0), r3.Vector3d(1, 0, 0),
                         r3.Vector3d(0, 1, 0))
        geom = r3.Brep.CreateFromSurface(
            r3.PlaneSurface(plane, r3.Interval(0, 10), r3.Interval(0, 8)))
        attrs = r3.ObjectAttributes()
        attrs.Name = "ImportedPicture"
        attrs.LayerIndex = 0
        attrs.MaterialIndex = rm.add_picture_material(PNG)
        attrs.MaterialSource = r3.ObjectMaterialSource.MaterialFromObject
        obj_id = rm.file.Objects.Add(geom, attrs)
        fobj = rm.file.Objects.FindId(str(obj_id))
        ctx = commands.NarwhalContext(rm)
        display = ctx._make_file_object_display(fobj, rm.layers())
        rm.register_blender_object(str(obj_id), display.name)
        _cutter_id, cutter = ctx.add_geometry(
            bridge.make_line((0, 6, 0), (14, 6, 0)), "Cutter", 0)
        return ctx, fobj, display, cutter

    def picture_replacements(ctx):
        return [fobj for fobj in ctx.model.file.Objects
                if isinstance(fobj.Geometry, r3.Brep)]

    def check_replacements(ctx, expected_count, label):
        rows = picture_replacements(ctx)
        frames = [bridge.picture_frame(fobj.Geometry) for fobj in rows]
        check(len(rows) == expected_count and all(frame == IMPORTED_FRAME for frame in frames),
              f"{label}: every replacement has the original Picture marker/frame ({frames})")

        materials = [ctx.model.object_material(str(fobj.Attributes.Id)) for fobj in rows]
        textures = [mat.GetBitmapTexture() if mat is not None else None for mat in materials]
        check(all(mat is not None and tex is not None and os.path.abspath(tex.FileName) == PNG
                  for mat, tex in zip(materials, textures)),
              f"{label}: every replacement has an object Picture material for the original image")

        displays = [ctx.object_by_name(fobj.Attributes.Name) for fobj in rows]
        display_images = []
        for ob in displays:
            if ob is None:
                continue
            for mat in ob.data.materials:
                if mat is not None and mat.node_tree is not None:
                    display_images.extend(node.image for node in mat.node_tree.nodes
                                          if node.type == "TEX_IMAGE" and node.image is not None)
        check(len(displays) == expected_count and all(ob is not None and ob.get("narwhal_picture")
                                                     for ob in displays)
              and len(display_images) == expected_count
              and all(os.path.abspath(bpy.path.abspath(image.filepath)) == PNG
                      for image in display_images),
              f"{label}: Blender displays remain Pictures textured from the original image")

        max_error = 0.0
        saw_original_cut_uv = False
        for ob in displays:
            bm = bmesh.new()
            bm.from_mesh(ob.data)
            uv_layer = bm.loops.layers.uv.active
            if uv_layer is None:
                max_error = float("inf")
            else:
                for face in bm.faces:
                    for loop in face.loops:
                        actual = tuple(loop[uv_layer].uv)
                        expected = picture.uv_of(IMPORTED_FRAME, tuple(loop.vert.co))
                        max_error = max(max_error, abs(actual[0] - expected[0]),
                                        abs(actual[1] - expected[1]))
                        saw_original_cut_uv |= abs(actual[1] - 3 / 8) < 1e-6
            bm.free()
        check(max_error < 1e-6 and saw_original_cut_uv,
              f"{label}: UVs use the original 10x8 frame; split edge V=3/8, not 0 or 1 "
              f"(max error {max_error})")

    split_ctx, split_fobj, split_display, split_cutter = imported_picture_context()
    check(bridge.picture_frame(split_fobj.Geometry) is None
          and split_display.get("narwhal_picture"),
          "fixture is a marker-less imported Picture recognized only from its material")
    split = commands.SplitCmd()
    split.args = [["ImportedPicture"], [split_cutter]]
    split_msg, split_done = split.execute(split_ctx)
    check(split_done and "2 pieces" in split_msg,
          f"production Split command replaces the imported Picture ({split_msg!r})")
    check_replacements(split_ctx, 2, "Split")

    trim_ctx, trim_fobj, trim_display, trim_cutter = imported_picture_context()
    check(bridge.picture_frame(trim_fobj.Geometry) is None and trim_display.get("narwhal_picture"),
          "Trim fixture is likewise marker-less and material-derived")
    trim = commands.TrimCmd()
    trim.args = [[trim_cutter], (4, 4, 0)]
    trim_msg, trim_done = trim.execute(trim_ctx)
    check(not trim_done and "Trimmed ImportedPicture" in trim_msg,
          f"production Trim command replaces the imported Picture ({trim_msg!r})")
    check_replacements(trim_ctx, 1, "Trim")

    print("\n-- the display mesh carries UVs (blender -b) --")
    ctx, g, msg, done = draw(["2,1,0", "12,1,0"])
    bm = bridge.geometry_to_bmesh(g)
    layer = bm.loops.layers.uv.active
    uvs = {}
    for face in bm.faces:
        for loop in face.loops:
            co = loop.vert.co
            uvs[(round(co.x, 9), round(co.y, 9))] = tuple(round(c, 6) for c in loop[layer].uv)
    check(layer is not None and uvs.get((2.0, 1.0)) == (0.0, 0.0)
          and uvs.get((12.0, 6.0)) == (1.0, 1.0) and uvs.get((12.0, 1.0)) == (1.0, 0.0),
          f"UVs run 0..1 across the frame ({uvs})")
    bm.free()
    dm = commands._picture_display_material(PNG)
    img = [n.image for n in dm.node_tree.nodes if n.type == "TEX_IMAGE"]
    linked = any(lk.to_node.type == "BSDF_PRINCIPLED" and lk.from_node.type == "TEX_IMAGE"
                 for lk in dm.node_tree.links)
    check(img and img[0] is not None and os.path.abspath(img[0].filepath) == PNG and linked
          and tuple(img[0].size) == (400, 200),
          f"the display material loads the image ({img and img[0] and img[0].filepath}) into "
          f"the base colour")
else:
    print("\n  (display-mesh UV gates run under blender -b)")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("PICTURE TESTS PASSED")
