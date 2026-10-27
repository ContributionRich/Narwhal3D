"""CageEdit: captives follow a control cage exactly, through the Rust volume.

MEASURED before: CageEdit did not exist. These checks drive the real command, edit the
cage's points the way the control-point tools do, and compare every captive with the
closed form of a trilinear (degree 1) or reproduced-linear (degree 3) cage. A captive
NURBS must keep its degree, knot count and weights -- a cage moves control points,
never samples the curve.
"""
import importlib.util
import math
import os
import sys
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


_load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
native_surface = _load("narwhal3d.native_surface", "narwhal3d/native_surface.py")
native_cage = _load("narwhal3d.native_cage", "narwhal3d/native_cage.py")
cage = _load("narwhal3d.cage", "narwhal3d/cage.py")
_load("narwhal3d.surface_commands", "narwhal3d/surface_commands.py")

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


if not native_cage.available():
    print("  skip  CageEdit needs the Rust pure core (set NARWHAL_PURE_CORE)")
    sys.exit(0)


class Ctx:
    def __init__(self, named=()):
        self.model = model.NarwhalModel()
        self.added = []
        for nm, g in named:
            self.model.add_object(g, nm, 0)

    def add_geometry(self, geom, base_name, layer_index=None):
        nm = f"{base_name}_{len(self.added) + 1}"
        self.model.add_object(geom, nm, 0)
        self.added.append(nm)
        return nm, nm

    def geom_by_name(self, name):
        for o in self.model.file.Objects:
            if o.Attributes.Name == name:
                return o.Geometry
        return None

    def object_by_name(self, name):
        return None

    def selected_objects(self):
        return [types.SimpleNamespace(name=n) for n in self.selection]

    def active_cplane(self):
        return None

    def note_change(self, label=None):
        pass


def run(named, select, opts_=()):
    ctx = Ctx(named)
    ctx.selection = list(select)
    c = type(commands.COMMANDS["cageedit"])()
    for k, v in opts_:
        c.opt[k] = v
    msg, done = c.begin(ctx)
    if not done:
        msg, done = c.feed("", ctx)
    return ctx, msg


def edit_cage(ctx, cage_name, moves):
    """Move cage lattice points {lattice index: (dx, dy, dz)} like the grip tools do."""
    geom = ctx.geom_by_name(cage_name)
    binding = cage.binding_of(geom)
    order = cage.serpentine(binding["counts"])
    for lattice_index, (dx, dy, dz) in moves.items():
        vertex = order.index(lattice_index)
        x, y, z = bridge.control_point_at(geom, vertex)
        assert bridge.set_control_point(geom, vertex, (x + dx, y + dy, z + dz))
    return cage.refresh(ctx.model, geom)


def euclid(curve):
    out = []
    for i in range(len(curve.Points)):
        p = curve.Points[i]
        out.append((p.X / p.W, p.Y / p.W, p.Z / p.W, p.W))
    return out


print("\n-- the command builds a bounding-box cage --")
circle = bridge.make_circle((5, 3, 2), 4.0)            # box x 1..9, y -1..7, z = 2 (flat)
lifted = bridge.make_circle((5, 3, 6), 4.0)            # gives the cage height 2..6
ctx, msg = run([("ring", circle), ("top", lifted)], ["ring", "top"],
               [("PointCountX", 2), ("PointCountY", 2), ("PointCountZ", 2),
                ("DegreeX", 1), ("DegreeY", 1), ("DegreeZ", 1)])
check(ctx.added == ["Cage_1"] and "2 x 2 x 2" in (msg or ""), f"a 2x2x2 cage is created ({msg!r})")
binding = cage.binding_of(ctx.geom_by_name("Cage_1")) if ctx.added else None
check(binding is not None and binding["origin"] == [1.0, -1.0, 2.0]
      and binding["axes"] == [[8.0, 0.0, 0.0], [0.0, 8.0, 0.0], [0.0, 0.0, 4.0]],
      f"the cage is the captives' exact bounding box ({binding and binding['origin']})")

print("\n-- an unmoved cage changes nothing --")
before = euclid(ctx.geom_by_name("ring"))
edit_cage(ctx, "Cage_1", {})
after = euclid(ctx.geom_by_name("ring"))
check(all(max(abs(a - b) for a, b in zip(p, q)) < 1e-12 for p, q in zip(before, after)),
      "refreshing an untouched cage leaves the captive exactly in place")

print("\n-- moving one corner deforms trilinearly --")
# Lattice index 7 is corner (1,1,1) = (9, 7, 6). Trilinear: a control point at local
# (u, v, w) rises by 3 * u * v * w. The ring is at w = 0, so it must not move at all;
# the top ring (w = 1) rises by 3uv.
n = edit_cage(ctx, "Cage_1", {7: (0.0, 0.0, 3.0)})
ring, top = ctx.geom_by_name("ring"), ctx.geom_by_name("top")
check(n == 2, f"both captives were rebuilt ({n})")
ring_ok = all(abs(p[2] - 2.0) < 1e-12 for p in euclid(ring))
top_ok = True
for (x, y, z, w), (x0, y0, z0, w0) in zip(euclid(top), euclid(lifted)):
    u, v = (x0 - 1.0) / 8.0, (y0 + 1.0) / 8.0
    top_ok &= abs(z - (6.0 + 3.0 * u * v)) < 1e-12 and abs(w - w0) < 1e-15
check(ring_ok, "the bottom ring (w = 0) is untouched")
check(top_ok, "every top-ring control point rises by exactly 3uv, weights unchanged")
check(isinstance(top, r3.NurbsCurve) and top.Degree == 2 and len(top.Points) == 9
      and top.IsRational and top.IsClosed,
      "the deformed circle is still an exact closed rational degree-2 NURBS with 9 points")

print("\n-- edits never accumulate: moving the corner back restores the captive --")
edit_cage(ctx, "Cage_1", {7: (0.0, 0.0, -3.0)})
restored = euclid(ctx.geom_by_name("top"))
check(all(max(abs(a - b) for a, b in zip(p, q)) < 1e-12
          for p, q in zip(restored, euclid(lifted))),
      "the captive is rebuilt from its rest copy, back to the original exactly")

print("\n-- a degree-3 cage moved rigidly translates its captives --")
srf = bridge.make_srf_pt([(0, 0, 0), (10, 0, 0), (10, 6, 3), (0, 6, 0)])
pt = r3.Point(r3.Point3d(5, 3, 1))
ctx, msg = run([("patch", srf), ("dot", pt)], ["patch", "dot"])
check(ctx.added and "4 x 4 x 4" in (msg or ""), f"the default cage is 4 x 4 x 4 ({msg!r})")
total = 64
edit_cage(ctx, "Cage_1", {i: (1.0, -2.0, 0.5) for i in range(total)})
moved = ctx.geom_by_name("patch")
face = moved.Faces[0].UnderlyingSurface().ToNurbsSurface() if isinstance(moved, r3.Brep) else None
corner = face.Points[1, 1] if face is not None else None
check(corner is not None and abs(corner.X - 11.0) < 1e-9 and abs(corner.Y - 4.0) < 1e-9
      and abs(corner.Z - 3.5) < 1e-9 and moved.IsSurface,
      "the surface's control points translate exactly and it stays one untrimmed surface")
dot = ctx.geom_by_name("dot")
check(isinstance(dot, r3.Point) and abs(dot.Location.X - 6.0) < 1e-9
      and abs(dot.Location.Y - 1.0) < 1e-9 and abs(dot.Location.Z - 1.5) < 1e-9,
      "a point captive translates exactly")

print("\n-- a degree-3 cage bends with the cubic basis --")
# 4 points, degree 3 = one Bezier span per direction. Lattice point (1,1,1) has basis
# 3t(1-t)^2 in each direction; at the box centre t = 0.5 that is 0.375, so lifting it
# by 2 lifts the centre by 2 * 0.375^3. A linear (or wrong-degree) volume gives another
# number; a rigid move cannot tell them apart, which is why this check exists.
corners = [r3.Point(r3.Point3d(0, 0, 0)), r3.Point(r3.Point3d(8, 8, 8))]
centre = r3.Point(r3.Point3d(4, 4, 4))
ctx, msg = run([("a", corners[0]), ("b", corners[1]), ("c", centre)], ["a", "b", "c"])
edit_cage(ctx, "Cage_1", {1 + 4 * (1 + 4 * 1): (0.0, 0.0, 2.0)})
lifted_centre = ctx.geom_by_name("c").Location.Z
check(abs(lifted_centre - (4.0 + 2.0 * 0.375 ** 3)) < 1e-12,
      f"the centre rises by 2 x 0.375^3 = {2 * 0.375 ** 3:.9f} ({lifted_centre - 4.0:.9f})")
check(ctx.geom_by_name("a").Location.Z == 0.0, "a corner the moved point does not reach stays put")

print("\n-- honest refusals --")
holed = bridge.make_planar_srf([r3.PolylineCurve([r3.Point3d(*p) for p in
                                                  ((0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0), (0, 0, 0))]),
                                bridge.make_circle((2, 2, 0), 1.0)])[0]
ctx, msg = run([("holed", holed)], ["holed"])
check(not ctx.added and "not implemented yet" in (msg or ""),
      f"a trimmed surface is refused by name ({msg!r})")
box = bridge.make_box((0, 0, 0), (2, 2, 0), 2)
ctx, msg = run([("box", box)], ["box"])
check(not ctx.added and "not implemented yet" in (msg or ""), f"a solid is refused by name ({msg!r})")
ctx, msg = run([("ring", circle)], ["ring"], [("PointCountX", 1)])
check(not ctx.added and "at least 2" in (msg or ""), f"PointCount=1 is refused ({msg!r})")

print("\n-- Rust only: no silent Python deformation --")
saved = os.environ.get("NARWHAL_PURE_CORE")
os.environ["NARWHAL_PURE_CORE"] = os.path.join(ROOT, "tmp", "no-such-pure-core.so")
native_surface._loaded = None
ctx, msg = run([("ring", circle)], ["ring"])
check(not ctx.added and "Rust pure core" in (msg or ""),
      f"without the Rust core CageEdit says so instead of falling back ({msg!r})")
if saved is None:
    os.environ.pop("NARWHAL_PURE_CORE", None)
else:
    os.environ["NARWHAL_PURE_CORE"] = saved
native_surface._loaded = None

real = failures
print(f"\n{checks - len(real)}/{checks} checks passed")
if real:
    for label in real:
        print("FAIL:", label)
    sys.exit(1)
print("CAGEEDIT PASSED")
