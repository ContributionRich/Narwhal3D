"""Live previews for Torus, Tube, TruncatedCone and Ellipsoid.

MEASURED (docs/measured/research_preview_14/notes/P06-P09): each draws while the mouse
moves -- a live circle (or rubber line) at the first size prompt, then the growing wire
shape at each later prompt, following the cursor.

The strongest check is that a preview SHOWS WHAT A CLICK WOULD BUILD: at each command's
last prompt the cursor is fed to the real command as the pick, and the preview's extent
must equal the exact OCCT result's tight bounding box. A preview that guessed a different
size (or ignored the cursor) fails that.

Run: tools/blender-patched/5.2/python/bin/python3.13 spike/test_solid_previews.py
"""
import importlib.util
import os
import sys
import types

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
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
preview = _load("narwhal3d.preview", "narwhal3d/preview.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
from narwhal3d import kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402

TOP = cplane.WORLD_TOP
checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(("  ok   " if ok else "  FAIL ") + label)


class Ctx:
    def __init__(self):
        self.model = model.NarwhalModel()
        self.added = []

    def add_geometry(self, geom, base_name, layer_index=None):
        self.added.append(geom)
        return base_name, base_name

    def active_cplane(self):
        return TOP

    def selected_objects(self):
        return []

    def note_change(self, label=None):
        pass


def extent(segs):
    pts = [p for s in segs for p in s]
    return tuple(round(min(p[i] for p in pts), 6) + 0.0 for i in range(3)) + \
        tuple(round(max(p[i] for p in pts), 6) + 0.0 for i in range(3))


def built_box(ctx):
    lo, hi = ob.tight_bbox(kernel.master_of(ctx.added[0]))
    return tuple(round(c, 6) + 0.0 for c in lo + hi)


def drive(key, lines):
    ctx = Ctx()
    c = type(commands.COMMANDS[key])()
    c.begin(ctx)
    for line in lines:
        c.feed(line, ctx)
    return ctx, c


def fmt(p):
    return ",".join(repr(float(v)) for v in p)


# (command, inputs before the measured prompts, [cursor at each later prompt]); every
# cursor is used first for the preview, then fed as that prompt's pick.
CASES = (
    ("torus", ["0,0,0"], [(5, 0, 0), (6.5, 0, 0)]),
    ("tube", ["0,0,0"], [(3, 0, 0), (0, 1, 0), (0, 0, 4)]),
    ("truncatedcone", ["0,0,0"], [(3, 0, 0), (0, 0, 4), (1, 0, 4)]),
    ("ellipsoid", ["0,0,0"], [(4, 0, 0), (0, 2, 0), (0, 0, 1)]),
)

for key, first, cursors in CASES:
    check(preview.has_preview(key), f"{key} has a live preview")
    ctx = Ctx()
    c = type(commands.COMMANDS[key])()
    c.begin(ctx)
    for line in first:
        c.feed(line, ctx)
    check(preview.segments(key, c.args, (2, 2, 0), TOP) != [],
          f"{key}: something is drawn at the first prompt after the centre")
    moved = preview.segments(key, c.args, (7, 1, 0), TOP)
    check(extent(moved) != extent(preview.segments(key, c.args, (2, 2, 0), TOP)),
          f"{key}: the drawing FOLLOWS the cursor")
    last = None
    for cursor in cursors:
        last = preview.segments(key, c.args, cursor, TOP)
        c.feed(fmt(cursor), ctx)
    check(ctx.added and kernel.master_of(ctx.added[0]) is not None,
          f"{key}: the command built an exact solid from the same picks")
    if ctx.added:
        check(extent(last) == built_box(ctx),
              f"{key}: the last preview is the built solid ({extent(last)} vs {built_box(ctx)})")

# Torus: the second radius is measured from the RING (TorusCmd._parse_distance).
ctx, c = drive("torus", ["0,0,0", "5"])
segs = preview.segments("torus", c.args, (5, 0, 2), TOP)
check(extent(segs)[5] == 2.0 and extent(segs)[3] == 7.0,
      f"Torus: a cursor 2 above the ring gives a tube of radius 2 ({extent(segs)})")
# Tube / TruncatedCone heights are SIGNED along the CPlane normal.
ctx, c = drive("tube", ["0,0,0", "3", "1"])
check(extent(preview.segments("tube", c.args, (0, 0, -4), TOP))[2] == -4.0,
      "Tube: a cursor below the CPlane draws the tube downward")
ctx, c = drive("truncatedcone", ["0,0,0", "3"])
segs = preview.segments("truncatedcone", c.args, (0, 0, 4), TOP)
check(len(segs) == 5 and all(s[1] == (0.0, 0.0, 4.0) for s in segs[1:]),
      "TruncatedCone: at the top prompt, a cone wire to an apex at the cursor height")
ctx, c = drive("ellipsoid", ["0,0,0"])
check(preview.segments("ellipsoid", c.args, (3, 1, 0), TOP) == [[(0.0, 0.0, 0.0), (3, 1, 0)]],
      "Ellipsoid: a rubber line from the centre at the first axis (MEASURED)")

failed = [label for ok, label in checks if not ok]
print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
