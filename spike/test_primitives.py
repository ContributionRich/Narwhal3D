"""Exact Torus, Ellipsoid, TruncatedCone, Tube, and Pyramid tests.

Every gate uses an OCCT master face-type count and a closed-form volume:

    torus           ONE TORUS face           2 pi^2 R r^2
    ellipsoid       ONE BSPLINE face         4/3 pi a b c   (OCCT has no ellipsoid type)
    truncated cone  {CONE 1, PLANE 2}        pi h (R^2 + R r + r^2) / 3
    tube            {CYLINDER 2, PLANE 2}    pi h (R^2 - r^2)
    pyramid         {PLANE n+1}              A_base h / 3

Placement is checked in a ROTATED CPlane (Front), where a wrong axis cannot hide.

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
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
from narwhal3d import kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as ob  # noqa: E402

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


for key in ("torus", "ellipsoid", "truncatedcone", "tube", "pyramid"):
    check(key in commands.COMMANDS, f"{key!r} is a registered command")
for fn in ("make_torus", "make_ellipsoid", "make_truncated_cone", "make_tube", "make_pyramid"):
    check(hasattr(bridge, fn), f"bridge.{fn} exists")
if failures or not ob.available():
    print(f"\n{checks - len(failures)}/{checks} checks passed")
    sys.exit(1 if failures else 0)


def rel(a, b):
    return abs(a - b) / abs(b)


def solid(g, types_, volume, tol=1e-12, label=""):
    m = kernel.master_of(g) if g is not None else None
    ok = (m is not None and ob.is_airtight(m) and ob.face_types(m) == types_
          and rel(ob.volume(m), volume) < tol and kernel.honesty_problem(g) is None
          and bool(kernel.recipe_of(g)))
    check(ok, f"{label}: {types_}, airtight, honest, volume {volume:.9f} "
              f"({m is not None and ob.face_types(m)}, {m is not None and ob.volume(m)!r})")
    return m


def bb(m):
    lo, hi = ob.tight_bbox(m)
    return tuple(round(c, 6) + 0.0 for c in lo + hi)


FRONT = cplane.WORLD_FRONT          # x = world X, y = world Z, normal = world -Y
C = (1.0, 2.0, 3.0)

print("\n-- the producers, in the FRONT CPlane at (1, 2, 3) --")
m = solid(bridge.make_torus(C, 5.0, 1.5, FRONT), {"TORUS": 1}, 2 * math.pi ** 2 * 5 * 1.5 ** 2,
          label="torus R5 r1.5")
check(m is not None and bb(m) == (-5.5, 0.5, -3.5, 7.5, 3.5, 9.5),
      f"...lying in the Front plane: y spans only 2 r about y = 2 ({m is not None and bb(m)})")
m = solid(bridge.make_ellipsoid(C, 3.0, 2.0, 1.0, FRONT), {"BSPLINE": 1},
          4 / 3 * math.pi * 6, tol=1e-8, label="ellipsoid 3 x 2 x 1")
check(m is not None and bb(m) == (-2.0, 1.0, 1.0, 4.0, 3.0, 5.0),
      f"...a = 3 along world X, b = 2 along world Z, c = 1 along the normal (y) "
      f"({m is not None and bb(m)})")
m = solid(bridge.make_truncated_cone(C, 3.0, 1.0, 4.0, FRONT), {"CONE": 1, "PLANE": 2},
          math.pi * 4 * (9 + 3 + 1) / 3, label="truncated cone R3 r1 h4")
check(m is not None and bb(m) == (-2.0, -2.0, 0.0, 4.0, 2.0, 6.0),
      f"...its height runs along the Front normal (-Y): y from 2 down to -2 "
      f"({m is not None and bb(m)})")
solid(bridge.make_tube(C, 1.0, 3.0, 4.0, FRONT), {"CYLINDER": 2, "PLANE": 2},
      math.pi * 4 * (9 - 1), label="tube 3 / 1, h4 (either radius may be the outer)")
area5 = 5 / 2 * 4 * math.sin(2 * math.pi / 5)
solid(bridge.make_pyramid(C, 2.0, 5, 3.0, True, FRONT), {"PLANE": 6}, area5 * 3 / 3,
      label="pentagonal pyramid, inscribed r2, h3")
solid(bridge.make_pyramid(C, 2.0, 4, -3.0, False), {"PLANE": 5}, 16 * 3 / 3,
      label="square pyramid, circumscribed r2 (side 4), pointing DOWN")

print("\n-- refused by name --")
for fn, args, words in ((bridge.make_torus, (C, 2.0, 2.0), "spindle"),
                        (bridge.make_truncated_cone, (C, 2.0, 2.0, 3.0), "DIFFERENT"),
                        (bridge.make_tube, (C, 2.0, 2.0, 3.0), "inner radius < outer"),
                        (bridge.make_ellipsoid, (C, 1.0, 0.0, 1.0), "positive"),
                        (bridge.make_pyramid, (C, 1.0, 2, 1.0), "at least 3 sides")):
    try:
        fn(*args)
        ok, msg = False, "no error"
    except ValueError as e:
        ok, msg = words in str(e), str(e)
    check(ok, f"{fn.__name__}{args[1:]} ({msg!r})")


class Ctx:
    def __init__(self, plane=None):
        self.model = model.RhinoModel()
        self.added = []
        self.plane = plane

    def add_geometry(self, geom, base_name, layer_index=None):
        nm = f"{base_name}{len(self.added) + 1}"
        self.model.add_object(geom, nm, 0)
        self.added.append(geom)
        return nm, nm

    def geom_by_name(self, name):
        return None

    def object_by_name(self, name):
        return None

    def selected_objects(self):
        return []

    def active_cplane(self):
        return self.plane

    def note_change(self, label=None):
        pass


def run(key, lines, plane=None):
    ctx = Ctx(plane)
    c = type(commands.COMMANDS[key])()
    msg, done = c.begin(ctx)
    last = msg
    for line in lines:
        if done:
            break
        msg, done = c.feed(line, ctx)
        last = msg or last
    g = ctx.added[0] if ctx.added else None
    return g, (kernel.master_of(g) if g is not None else None), last, c


print("\n-- the COMMANDS, typed numbers --")
g, m, msg, c = run("torus", ["0,0,0", "5", "1.5"])
solid(g, {"TORUS": 1}, 2 * math.pi ** 2 * 5 * 1.5 ** 2, label=f"Torus 5 / 1.5 ({msg})")
g, m, msg, c = run("ellipsoid", ["0,0,0", "3,0,0", "2", "1"])
solid(g, {"BSPLINE": 1}, 4 / 3 * math.pi * 6, tol=1e-8, label=f"Ellipsoid 3/2/1 ({msg})")
g, m, msg, c = run("truncatedcone", ["0,0,0", "3", "4", "1"])
solid(g, {"CONE": 1, "PLANE": 2}, math.pi * 4 * 13 / 3, label=f"TruncatedCone ({msg})")
g, m, msg, c = run("tube", ["0,0,0", "3", "1", "4"])
solid(g, {"CYLINDER": 2, "PLANE": 2}, math.pi * 4 * 8, label=f"Tube ({msg})")
g, m, msg, c = run("pyramid", ["NumSides=6", "0,0,0", "2", "3"])
area6 = 6 / 2 * 4 * math.sin(2 * math.pi / 6)
solid(g, {"PLANE": 7}, area6 * 3 / 3, label=f"Pyramid NumSides=6 ({msg})")

print("\n-- the COMMANDS, PICKED points --")
g, m, msg, c = run("torus", ["0,0,0", "5", "6,0,2"])
check(m is not None and rel(ob.volume(m), 2 * math.pi ** 2 * 5 * 5) < 1e-12,
      f"Torus: a picked second radius is its DISTANCE FROM THE BASE CIRCLE: (6,0,2) is "
      f"hypot(1, 2) = sqrt 5 from it ({m is not None and ob.volume(m)!r})")
g, m, msg, c = run("ellipsoid", ["0,0,0", "2,2,0", "1", "1"])
check(m is not None and bb(m)[3] - bb(m)[0] > 3.9 and rel(ob.volume(m), 4 / 3 * math.pi *
                                                          math.sqrt(8)) < 1e-8,
      f"Ellipsoid: the picked first axis (2,2) sets a = sqrt 8 AND the direction (the ellipsoid "
      f"is turned 45 degrees) ({m is not None and bb(m)})")
g, m, msg, c = run("truncatedcone", ["0,0,0", "3", "4", "1.5,0,4"])
check(m is not None and rel(ob.volume(m), math.pi * 4 * (9 + 4.5 + 2.25) / 3) < 1e-12,
      f"TruncatedCone: a picked end radius is measured from the END circle's centre, 1.5 "
      f"({m is not None and ob.volume(m)!r})")
g, m, msg, c = run("pyramid", ["0,0,0", "0,2,0", "3"])
vs = [p for _i, p in bridge.subobject_parts(g)["vertices"]] if g is not None else []
check(any(math.dist(p, (0, 2, 0)) < 1e-9 for p in vs),
      "Pyramid (inscribed): a CORNER lands on the picked point (0, 2)")
g, m, msg, c = run("pyramid", ["Circumscribed", "NumSides=4", "0,0,0", "0,2,0", "3"])
lo_hi = bb(m) if m is not None else None
check(m is not None and rel(ob.volume(m), 16 * 3 / 3) < 1e-12
      and lo_hi[:2] == (-2.0, -2.0) and lo_hi[3:5] == (2.0, 2.0),
      f"Pyramid (circumscribed): a SIDE MIDPOINT lands on the pick -- an axis-aligned 4 x 4 "
      f"base, volume 16 ({lo_hi})")

print("\n-- in the FRONT CPlane, the command builds there --")
g, m, msg, c = run("truncatedcone", ["0,0,0", "3", "4", "1"], plane=FRONT)
check(m is not None and bb(m) == (-3.0, -4.0, -3.0, 3.0, 0.0, 3.0),
      f"the height runs along Front's normal (-Y) ({m is not None and bb(m)})")

print("\n-- options that are not built say so, and build nothing --")
for key, lines, words in (("pyramid", ["Solid=No"], "not implemented"),
                          ("truncatedcone", ["Solid=No"], "not implemented"),
                          ("torus", ["FixInnerDimension"], "not implemented"),
                          ("tube", ["WallThickness"], "not implemented"),
                          ("ellipsoid", ["FromFoci"], "not implemented"),
                          ("pyramid", ["NumSides=2"], ">= 3"),
                          ("tube", ["0,0,0", "2", "2", "3"], "same radius")):
    g, m, msg, c = run(key, lines)
    check(words in (msg or "") and g is None, f"{key} {lines[-1]}: {msg!r}")

print("\n-- save + reload: every one rebuilds from its recipe --")
made = [bridge.make_torus(C, 5.0, 1.5, FRONT), bridge.make_ellipsoid(C, 3.0, 2.0, 1.0, FRONT),
        bridge.make_truncated_cone(C, 3.0, 1.0, 4.0), bridge.make_tube(C, 1.0, 3.0, 4.0),
        bridge.make_pyramid(C, 2.0, 5, 3.0, True, FRONT)]
want = [(ob.face_types(kernel.master_of(g)), round(ob.volume(kernel.master_of(g)), 7))
        for g in made]
f3 = r3.File3dm()
for g in made:
    f3.Objects.Add(g, None)
path = os.path.join(tempfile.mkdtemp(prefix="g106_"), "g106.3dm")
f3.Write(path, 8)
kernel.forget_masters()
back = [o.Geometry for o in r3.File3dm.Read(path).Objects]
restored, lost = bridge.rehydrate_document(back)
got = [(ob.face_types(kernel.master_of(g)), round(ob.volume(kernel.master_of(g)), 7))
       if kernel.master_of(g) is not None else None for g in back]
check(restored == 5 and lost == 0 and got == want, f"all five rebuild ({restored}/{lost})")
cut = bridge.boolean_occt(back[0], bridge.make_box((-10, -10, 0), (10, 10, 0), 20),
                          "INTERSECT")
check(cut is not None and kernel.master_of(cut) is not None,
      f"a reloaded torus still booleans analytically ({bridge.last_occt_decline()})")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f_ in failures:
        print("FAIL:", f_)
    sys.exit(1)
print("EXACT PRIMITIVES PASSED")
