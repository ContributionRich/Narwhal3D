"""TracePicture: the Rust cut-line helper traced onto a placed Picture as exact NURBS curves.

Uses synthetic PNGs whose answer is known: an opaque block (with a window) on a transparent
or white background, placed on a Picture frame that is NOT at the origin and NOT square, so a
wrong axis, a missing y flip or a unit slip moves the result measurably.
"""
import importlib.util
import math
import os
import struct
import sys
import tempfile
import types
import zlib

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(n):
    s = importlib.util.spec_from_file_location("narwhal3d." + n, os.path.join(ROOT, "narwhal3d", n + ".py"))
    m = importlib.util.module_from_spec(s)
    sys.modules["narwhal3d." + n] = m
    setattr(pkg, n, m)
    s.loader.exec_module(m)
    return m


load("cplane"); load("bridge"); model = load("model"); load("options"); load("preview")
picture = load("picture"); tp = load("trace_picture"); commands = load("commands")
checks = []


def check(v, label):
    checks.append((bool(v), label))
    print(("  ok   " if v else "  FAIL ") + label)


def png(path, w, h, pixel):
    """Write an RGBA PNG; `pixel(x, y)` (y down) returns (r, g, b, a)."""
    raw = b"".join(b"\0" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def block(x, y):
    """Opaque black block at x 100..200, y 20..100 (top-down) with a 40x30 window at 130..170, 45..75."""
    inside = 100 <= x < 200 and 20 <= y < 100
    window = 130 <= x < 170 and 45 <= y < 75
    return inside and not window


helper = tp.helper_path()
check(helper is not None, "the coco-cutline helper is built (scripts/setup_dev_native.sh)")
if helper is None:
    raise SystemExit(1)

tmp = tempfile.mkdtemp(prefix="narwhal-trace-")
clear_png = os.path.join(tmp, "clear.png")
white_png = os.path.join(tmp, "white.png")
def art(x, y):
    """Shaded artwork: a flat block would read as a solid border colour once trimmed."""
    return ((2 * x) % 200, (3 * y) % 200, 60, 255)


png(clear_png, 300, 200, lambda x, y: art(x, y) if block(x, y) else (0, 0, 0, 0))
png(white_png, 300, 200, lambda x, y: art(x, y) if block(x, y) else (255, 255, 255, 255))

# 300x200 px on a 30 x 20 mm frame at (10, 20, 5): 10 px per mm.
frame = picture.make_frame(clear_png, (10.0, 20.0, 5.0), (30.0, 0.0, 0.0), (0.0, 20.0, 0.0))
MM_TO_IN = 1 / 25.4
base = {"offset": 0.0, "min_hole": 1.0, "inner_cuts": True, "background": "Auto",
        "color": "#ffffff", "color_tolerance": 12.0, "smoothness": 60.0}


def loops(image, **changes):
    f = dict(frame, image=image)
    settings = dict(base, **changes)
    return tp.loop_controls(tp.trace(helper, tp.helper_args(image, 300, f, MM_TO_IN, settings)), f)


def bbox(ctrl):
    return [(min(p[i] for p in ctrl), max(p[i] for p in ctrl)) for i in range(3)]


# --- arguments --------------------------------------------------------------------------
args = tp.helper_args(clear_png, 300, frame, MM_TO_IN, dict(base, offset=2.54, inner_cuts=False,
                                                             background="Color", color="FFFFFF"))
check(abs(float(args[args.index("--dpi") + 1]) - 254.0) < 1e-9,
      "the image is sized so its width spans the Picture (300 px over 30 mm = 254 dpi)")
check(abs(float(args[args.index("--offset-in") + 1]) - 0.1) < 1e-12,
      "the Offset is converted from model units to inches")
check(args[args.index("--background") + 1] == "#ffffff" and "--no-inner-cuts" in args,
      "Background=Color passes the colour, InnerCuts=No passes --no-inner-cuts")

# --- tracing maps onto the frame --------------------------------------------------------
traced = loops(clear_png)
outer = max(traced, key=lambda c: (bbox(c)[0][1] - bbox(c)[0][0]))
(x0, x1), (y0, y1), (z0, z1) = bbox(outer)
# block x 100..200 px -> 20..30 mm; rows 20..100 (top-down) -> v 0.9..0.5 -> y 38..30 mm.
check(abs(x0 - 20) < 0.3 and abs(x1 - 30) < 0.3, f"outline spans x 20..30 mm ({x0:.3f}..{x1:.3f})")
check(abs(y0 - 30) < 0.3 and abs(y1 - 38) < 0.3,
      f"outline spans y 30..38 mm: image rows run top-down ({y0:.3f}..{y1:.3f})")
check(abs(z0 - 5) < 1e-9 and abs(z1 - 5) < 1e-9, "the cut lies in the Picture's plane (z = 5)")
check(len(traced) == 2, f"InnerCuts=Yes cuts the window as a second loop ({len(traced)} loops)")

grown = max(loops(clear_png, offset=1.0), key=lambda c: (bbox(c)[0][1] - bbox(c)[0][0]))
(gx0, gx1), (gy0, gy1), _ = bbox(grown)
check(abs((gx1 - gx0) - (x1 - x0) - 2.0) < 0.15 and abs((gy1 - gy0) - (y1 - y0) - 2.0) < 0.15,
      f"Offset=1 mm grows the outline by 1 mm each side ({gx1 - gx0:.3f} x {gy1 - gy0:.3f})")

check(len(loops(clear_png, inner_cuts=False)) == 1, "InnerCuts=No cuts the outline only")
# the window is 4 x 3 mm = 12 mm^2.
check(len(loops(clear_png, min_hole=11.0)) == 2 and len(loops(clear_png, min_hole=13.0)) == 1,
      "MinHoleArea skips windows smaller than the given area (12 mm^2 window kept at 11, skipped at 13)")

# --- colour background ------------------------------------------------------------------
check(tp.sample_color(helper, white_png, 5, 5) == "#ffffff"
      and tp.sample_color(helper, white_png, 150, 30) == "#645a3c",
      "the helper samples the clicked pixel's colour")
uv = picture.uv_of(frame, (25.0, 37.0, 5.0))
check(tp.pixel_at(frame, uv, (300, 200)) == (150, 30) and tp.pixel_at(frame, (1.2, 0.5), (300, 200)) is None,
      "a world point maps to its image pixel (y down); off the picture is None")
colored = loops(white_png, background="Color", color="#ffffff")
(cx0, cx1), (cy0, cy1), _ = bbox(max(colored, key=lambda c: bbox(c)[0][1] - bbox(c)[0][0]))
check(abs(cx0 - 20) < 0.3 and abs(cx1 - 30) < 0.3 and abs(cy0 - 30) < 0.3 and abs(cy1 - 38) < 0.3,
      f"Background=Color removes the white background ({len(colored)} loop(s))")

# --- exact NURBS -------------------------------------------------------------------------
curve = tp.nurbs_curve(r3, outer)
spans = (len(outer) - 1) // 3
mid = [c / 8 + 3 * a / 8 + 3 * b / 8 + d / 8 for a, b, c, d in zip(outer[1], outer[2], outer[0], outer[3])]
at = curve.PointAt(0.5)
check(curve.IsValid and curve.IsClosed and curve.Degree == 3 and curve.SpanCount == spans,
      f"each loop is ONE closed degree-3 NURBS curve with one span per cubic ({spans})")
check(math.dist((at.X, at.Y, at.Z), mid) < 1e-9, "the first span IS the first cubic Bezier (exact)")

# --- the command --------------------------------------------------------------------------
pic = sys.modules["narwhal3d.bridge"].make_picture(frame)
check(sys.modules["narwhal3d.bridge"].picture_frame(pic) is not None, "the test picture is a real Picture")
plain = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0))


class Obj:
    def __init__(self, name):
        self.name = name


class Ctx:
    def __init__(self, selected="Picture_1"):
        self.model = model.NarwhalModel()
        self.made = []
        self.selected = selected

    def geom_by_name(self, name):
        return {"Picture_1": pic, "Line_1": plain}.get(name)

    def add_geometry(self, g, b, layer_index=None):
        self.made.append(g)
        return "id", f"{b}_{len(self.made)}"

    def note_change(self, _=None):
        pass

    def selected_objects(self):
        return [Obj(self.selected)] if self.selected else []


def run(lines, selected="Picture_1"):
    commands.TracePictureCmd._sticky.clear()
    c = Ctx(selected)
    cmd = commands.TracePictureCmd()
    msg, done = cmd.begin(c)
    for line in lines:
        if done:
            break
        msg, done = cmd.feed(line, c)
    return c, cmd, msg, done


unit = model.NarwhalModel().unit_system()
check(unit == "Millimeters", f"test model is in millimeters ({unit})")
ctx, cmd, msg, done = run(["InnerCuts=No", "Offset=0.5", ""])
check(done and len(ctx.made) == 1 and all(isinstance(g, r3.NurbsCurve) and g.IsClosed for g in ctx.made),
      f"Enter adds the closed cut curves ({msg})")
ctx, cmd, msg, done = run([])
check(not done and abs(cmd.opt["Offset"] - 1.27) < 1e-9,
      "the default Offset is 0.05 in, shown in model units (1.27 mm)")
check(len(cmd.preview_curves(ctx.geom_by_name, (0, 0, 0))) == 1,
      "the preview draws the cut before Enter (the default MinHoleArea, 0.07 in^2, skips the window)")
ctx, cmd, msg, done = run(["Offset=0", "MinHoleArea=1"])
check(len(cmd.preview_curves(ctx.geom_by_name, (0, 0, 0))) == 2,
      "the preview follows option changes (MinHoleArea=1 brings the window back)")
ctx, cmd, msg, done = run(["PickColor", "25,37,5", ""])
check(cmd.opt["Background"] == "Color" and cmd.opt["Color"] == "#645a3c",
      "PickColor takes the background colour from the clicked point on the picture")
ctx, cmd, msg, done = run(["Color=#fff"])
check(not done and "#ffffff" in msg, f"a malformed Color is refused by name ({msg})")
ctx, cmd, msg, done = run(["ColorTolerance=80"])
check(not done and "between 0 and 60" in msg, "ColorTolerance is limited to 0-60 percent")
ctx, cmd, msg, done = run([], selected="Line_1")
check(done and not ctx.made and "Picture" in msg, f"a non-Picture is refused by name ({msg})")

failed = [label for ok, label in checks if not ok]
print(f"\n{len(checks) - len(failed)}/{len(checks)} TracePicture checks passed")
raise SystemExit(bool(failed))
