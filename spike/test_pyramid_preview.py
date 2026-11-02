"""Pyramid's live preview: the measured drawing at each mouse prompt.

MEASURED (docs/measured/research_preview_14/notes/P02-Pyramid.md):

* "Corner of pyramid": the base polygon is drawn live with a corner ON the cursor -- it
  grows and turns with the mouse; a typed radius fixes the size while the cursor still
  turns it;
* "End of pyramid": the whole wire pyramid -- base plus an edge from every corner to an
  apex on the CPlane normal at the cursor's height.

The preview must agree with `bridge.make_pyramid`, so the drawn base is compared with the
exact result's vertices, and nothing here builds kernel geometry.

Run: tools/blender-patched/5.2/python/bin/python3.13 spike/test_pyramid_preview.py
"""
import importlib.util
import math
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

checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(("  ok   " if ok else "  FAIL ") + label)


class Ctx:
    def __init__(self, plane=None):
        self.model = model.NarwhalModel()
        self.plane = plane

    def active_cplane(self):
        return self.plane

    def selected_objects(self):
        return []

    def note_change(self, label=None):
        pass


def near(a, b, tol=1e-9):
    return math.dist(a, b) <= tol


def corners(poly):
    return poly[:-1]          # polygon_polyline closes the loop with its first point


TOP, FRONT = cplane.WORLD_TOP, cplane.WORLD_FRONT
check(preview.has_preview("Pyramid"), "Pyramid has a live preview")

c = type(commands.COMMANDS["pyramid"])()
ctx = Ctx()
c.begin(ctx)
check(preview.segments("pyramid", [], (1, 1, 0), TOP, cmd=c) == [],
      "nothing is drawn before the centre (MEASURED: none at the first prompt)")
c.feed("0,0,0", ctx)

segs = preview.segments("pyramid", c.args, (0, 3, 0), TOP, cmd=c)
check(len(segs) == 1 and len(corners(segs[0])) == 5
      and any(near(p, (0, 3, 0)) for p in corners(segs[0])),
      f"corner prompt: a pentagon with a corner ON the cursor ({segs and segs[0][:2]})")
segs2 = preview.segments("pyramid", c.args, (4, 0, 0), TOP, cmd=c)
check(segs2 and any(near(p, (4, 0, 0)) for p in corners(segs2[0])),
      "moving the cursor redraws it: size AND rotation follow")

c.feed("2", ctx)                                   # MEASURED: the typed radius LOCKS
segs = preview.segments("pyramid", c.args, (0, 9, 0), TOP, cmd=c)
check(segs and any(near(p, (0, 2, 0)) for p in corners(segs[0]))
      and all(abs(math.hypot(p[0], p[1]) - 2.0) < 1e-9 for p in corners(segs[0])),
      "a typed radius fixes the size while the cursor still turns the base")

c.feed("0,5,0", ctx)                               # the pick sets only the rotation
segs = preview.segments("pyramid", c.args, (0, 0, 4), TOP, cmd=c)
base, edges = (segs[0], segs[1:]) if segs else (None, [])
check(len(edges) == 5 and all(near(e[1], (0, 0, 4)) for e in edges)
      and all(near(e[0], p) for e, p in zip(edges, corners(base))),
      f"end prompt: base + 5 edges to an apex at the cursor's height ({len(edges)} edges)")
segs = preview.segments("pyramid", c.args, (0, 0, -3), TOP, cmd=c)
check(segs and near(segs[1][1], (0, 0, -3)), "a cursor below the CPlane points the apex down")

# The drawn base must be the base the command builds.
g = bridge.make_pyramid((0, 0, 0), 2.0, 5, 4.0, True, types.SimpleNamespace(
    xaxis=(0.0, 1.0, 0.0), yaxis=(-1.0, 0.0, 0.0), zaxis=(0.0, 0.0, 1.0)))
from narwhal3d.kernel import occt_backend as ob  # noqa: E402
from narwhal3d import kernel  # noqa: E402
verts, _faces, _edges = ob.planar_polyhedron(kernel.master_of(g))
made = [tuple(v) for v in verts if abs(v[2]) < 1e-9]
check(len(made) == 5 and all(any(near(p, q, 1e-7) for q in made) for p in corners(base)),
      "the preview base is exactly the built pyramid's base")

# Circumscribed: a side MIDPOINT is on the cursor (as `make_pyramid`).
c = type(commands.COMMANDS["pyramid"])()
c.begin(ctx)
c.feed("Circumscribed", ctx)
c.feed("NumSides=4", ctx)
c.feed("0,0,0", ctx)
segs = preview.segments("pyramid", c.args, (0, 2, 0), TOP, cmd=c)
cs = corners(segs[0]) if segs else []
mids = [tuple((cs[i][k] + cs[(i + 1) % 4][k]) / 2 for k in range(3)) for i in range(len(cs))]
check(len(cs) == 4 and any(near(m, (0, 2, 0)) for m in mids),
      "Circumscribed: a side midpoint follows the cursor")

# Front CPlane: the base lies in world XZ, the apex runs along -Y (Front's normal).
c = type(commands.COMMANDS["pyramid"])()
fctx = Ctx(FRONT)
c.begin(fctx)
c.feed("0,0", fctx)
c.feed("2", fctx)
c.feed("", fctx)
segs = preview.segments("pyramid", c.args, (0, -5, 0), FRONT, cmd=c)
check(segs and all(abs(p[1]) < 1e-9 for p in segs[0]) and near(segs[1][1], (0, -5, 0)),
      "in Front the base is in XZ and the apex along the CPlane normal")

failed = [label for ok, label in checks if not ok]
print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
