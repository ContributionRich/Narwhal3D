"""Test suite for Picture EdgeMidpoint option (#71).

Exercises the EdgeMidpoint mode of PictureCmd:
- FLOWS and step transitions
- Frame calculation for EdgeMidpoint (corners, aspect ratio, orientation)
- Refusal cases (same midpoint/end, collinear third point, mode after first corner)
- Preservation of AroundCurve refusal
"""
import importlib.util
import math
import os
import sys
import tempfile
import types

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
_load("narwhal3d.options", "narwhal3d/options.py")
filedialog = _load("narwhal3d.filedialog", "narwhal3d/filedialog.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
picture = _load("narwhal3d.picture", "narwhal3d/picture.py")


class MockCtx:
    def __init__(self, plane=None):
        self.plane = plane or cplane.CPlane()
        self.added = []

    def active_cplane(self):
        return self.plane

    def add_geometry(self, geom, base_name):
        self.added.append((geom, base_name))
        return len(self.added), f"{base_name}_{len(self.added)}"


# Create a temporary dummy PNG image (400x200, aspect 2.0)
def make_dummy_png():
    import struct
    import zlib
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 400, 200, 8, 2, 0, 0, 0)
    ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data))
    raw = b"\x00" * (1 + 400 * 3) * 200
    comp = zlib.compress(raw)
    idat = struct.pack(">I", len(comp)) + b"IDAT" + comp + struct.pack(">I", zlib.crc32(b"IDAT" + comp))
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))
    f.write(sig + ihdr + idat + iend)
    f.close()
    return f.name


PNG_PATH = make_dummy_png()

checks = 0
failures = []


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        failures.append(msg)
        print(f"  FAIL {msg}")
    else:
        print(f"  ok   {msg}")


try:
    print("-- Picture EdgeMidpoint FLOW and Mode selection --")
    cmd = commands.PictureCmd()
    ctx = MockCtx()
    filedialog.ask_open_path = lambda title, start, filters=None: (filedialog.OK, PNG_PATH)
    msg, done = cmd.begin(ctx)
    check(not done and cmd.step_index == 1, "begin advances to step 1 with image path")

    # Mode selection
    res, done = cmd.on_mode("EdgeMidpoint", ctx)
    check(cmd._flow == "EdgeMidpoint", "flow set to EdgeMidpoint")
    check(len(cmd.steps) == 4, "steps count is 4 (file, midpoint, end, opposite)")
    check(cmd.steps[1][0] == "Midpoint of first edge:", "prompt 1 is 'Midpoint of first edge:'")
    check(cmd.steps[2][0] == "End of first edge:", "prompt 2 is 'End of first edge:'")
    check(cmd.steps[3][0] == "Opposite side:", "prompt 3 is 'Opposite side:'")

    # Feed inputs: midpoint (2,0,0), end (4,0,0), side below (1,-1,0)
    res = cmd._feed("2,0,0", ctx)
    res = cmd._feed("4,0,0", ctx)
    res = cmd._feed("1,-1,0", ctx)
    check(len(ctx.added) == 1, "picture geometry added to context")
    geom, name = ctx.added[0]
    check(isinstance(geom, r3.Brep), "geometry is Brep")
    check(len(geom.Faces) == 1, "geometry has exactly 1 planar face")
    bb = geom.GetBoundingBox()
    check(abs(bb.Min.X - 0.0) < 1e-9 and abs(bb.Max.X - 4.0) < 1e-9, "X extent is [0, 4]")
    check(abs(bb.Min.Y - (-2.0)) < 1e-9 and abs(bb.Max.Y - 0.0) < 1e-9, "Y extent is [-2, 0]")
    check(abs(bb.Min.Z - 0.0) < 1e-9 and abs(bb.Max.Z - 0.0) < 1e-9, "Z extent is [0, 0]")

    # Check side above (1,1,0)
    cmd2 = commands.PictureCmd()
    ctx2 = MockCtx()
    cmd2.begin(ctx2)
    cmd2.on_mode("EdgeMidpoint", ctx2)
    cmd2._feed("2,0,0", ctx2)
    cmd2._feed("4,0,0", ctx2)
    cmd2._feed("1,1,0", ctx2)
    check(len(ctx2.added) == 1, "side above picture added")
    bb2 = ctx2.added[0][0].GetBoundingBox()
    check(abs(bb2.Min.X - 0.0) < 1e-9 and abs(bb2.Max.X - 4.0) < 1e-9, "side above X extent is [0, 4]")
    check(abs(bb2.Min.Y - 0.0) < 1e-9 and abs(bb2.Max.Y - 2.0) < 1e-9, "side above Y extent is [0, 2]")

    # Check UV coordinates
    frame = bridge.picture_frame(ctx2.added[0][0])
    check(picture.uv_of(frame, (0, 0, 0)) == (0.0, 0.0), "UV at (0,0,0) is (0,0)")
    check(picture.uv_of(frame, (4, 2, 0)) == (1.0, 1.0), "UV at (4,2,0) is (1,1)")
    check(picture.uv_of(frame, (2, 1, 0)) == (0.5, 0.5), "UV at (2,1,0) is (0.5, 0.5)")

    # Check degenerate: midpoint == end
    cmd3 = commands.PictureCmd()
    ctx3 = MockCtx()
    cmd3.begin(ctx3)
    cmd3.on_mode("EdgeMidpoint", ctx3)
    cmd3._feed("2,0,0", ctx3)
    cmd3._feed("2,0,0", ctx3)
    msg3, done3 = cmd3._feed("1,1,0", ctx3)
    check(len(ctx3.added) == 0, "degenerate input creates no geometry")
    check("same point" in (msg3 or ""), "degenerate midpoint == end reports 'same point'")

    # Check collinear third point
    cmd4 = commands.PictureCmd()
    ctx4 = MockCtx()
    cmd4.begin(ctx4)
    cmd4.on_mode("EdgeMidpoint", ctx4)
    cmd4._feed("2,0,0", ctx4)
    cmd4._feed("4,0,0", ctx4)
    msg4, done4 = cmd4._feed("6,0,0", ctx4)
    check(len(ctx4.added) == 0, "collinear third point creates no geometry")
    check("on the line" in (msg4 or ""), "collinear third point reports 'on the line'")

    # Check choosing EdgeMidpoint after first corner
    cmd5 = commands.PictureCmd()
    ctx5 = MockCtx()
    cmd5.begin(ctx5)
    cmd5._feed("0,0,0", ctx5)
    msg5, _ = cmd5.on_mode("EdgeMidpoint", ctx5)
    check("before the first corner" in (msg5 or ""), "EdgeMidpoint after first point is refused")

    # Check AroundCurve still explicitly refuses
    cmd6 = commands.PictureCmd()
    ctx6 = MockCtx()
    cmd6.begin(ctx6)
    msg6, _ = cmd6.on_mode("AroundCurve", ctx6)
    check("AroundCurve option is not implemented yet" in (msg6 or ""),
          "AroundCurve continues to be refused by name")

finally:
    if os.path.exists(PNG_PATH):
        try:
            os.remove(PNG_PATH)
        except OSError:
            pass

print(f"\n{checks} checks, {len(failures)} failures.")
if failures:
    sys.exit(1)
