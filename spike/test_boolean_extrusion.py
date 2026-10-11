"""Boolean-command tests for capped and uncapped ``rhino3dm.Extrusion`` objects.

Capped extrusions are exact solids and retain their OCCT master and recipe through
B-rep conversion. Open extrusions and curves are refused without deleting inputs.
The volume gates use a cube and an intersecting circular extrusion with closed-form
expected values ``216 - 24π`` and ``72π``.
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


print("-- the boolean commands are registered --")
for key in ("booleanunion", "booleandifference", "booleanintersection"):
    check(key in commands.COMMANDS, f"{key!r} is a registered command")

if not ob.available():
    print(f"  skip  extrusion geometry gates need OCCT ({ob.unavailable_reason()})")
    sys.exit(0)

# 6-cube [0,6]^3 and a r=4 disc centred on its (6,6) corner, extruded 6. The overlap is
# exactly a quarter disc (the centre sits on the corner, so the part inside the cube is
# x<=6, y<=6), which is why the closed forms below come out clean.
SIDE, R, H = 6.0, 4.0, 6.0
BOX_VOL = SIDE ** 3                       # 216
CYL_VOL = math.pi * R * R * H             # 96 pi
QUARTER = CYL_VOL / 4.0                   # 24 pi, the disc part inside the cube
BOX_MINUS_EXT = BOX_VOL - QUARTER         # 216 - 24 pi
EXT_MINUS_BOX = CYL_VOL - QUARTER         # 72 pi
REL_TOL = 1e-6


class Ctx:
    """A minimal document context over a real `RhinoModel` -- no bpy, no viewport.

    `object_by_name` returns None, so the command's `_remove_object_from_scene` is a no-op
    and only document-side removal is exercised.
    """

    def __init__(self, named):
        self.model = model.RhinoModel()
        self.added = []
        for nm, g in named:
            self.model.add_object(g, nm, 0)

    def names(self):
        return [o.Attributes.Name for o in self.model.file.Objects]

    def add_geometry(self, geom, base_name, layer_index=None):
        nm = f"{base_name}_{len(self.added) + 1}"
        self.model.add_object(geom, nm, 0)
        self.added.append(geom)
        return nm, nm

    def geom_by_name(self, name):
        for o in self.model.file.Objects:
            if o.Attributes.Name == name:
                return o.Geometry
        return None

    def object_by_name(self, name):
        return None


def drive(named, sets):
    """Run `BooleanDifference` over `sets` (a list of name-lists, one per set).

    Returns `(message, ctx, result_or_None)`. Driving `execute` directly with the two
    sets in `args` is the same path `begin`/`feed` reach for a two-set command, without
    the selection plumbing a headless test does not have.
    """
    ctx = Ctx(named)
    cmd = commands.BooleanDifferenceCmd()
    cmd.args = [list(s) for s in sets]
    msg, _done = cmd.execute(ctx)
    res = ctx.added[0] if ctx.added else None
    return msg, ctx, res


def capped_ext():
    """A capped r=4 x 6 extrusion with an analytic master.

    `adopt_brep` is the same route a loaded file takes (a reopened file adopts it
    again"), so this is the production case, not a synthetic one.
    """
    e = r3.Extrusion.Create(bridge.make_circle((SIDE, SIDE, 0), R), H, True)
    assert bridge.adopt_brep(e), "the capped extrusion could not adopt a master"
    return e


def analytic_and_airtight(res):
    """The result is honest analytic geometry: a real master, airtight, not faceted."""
    master = kernel.master_of(res)
    if master is None:
        return False
    return (ob.is_airtight(master)
            and kernel.honesty_problem(res) is None
            and kernel.representation_of(res) != kernel.MESH_DERIVED)


print("\n-- box - capped extrusion: one analytic airtight solid, inputs removed --")
msg, ctx, res = drive([("box", bridge.make_box((0, 0, 0), (SIDE, SIDE, 0), SIDE)),
                       ("ext", capped_ext())],
                      [["box"], ["ext"]])
check(res is not None, f"creates a result (message: {msg!r})")
check(res is not None and "not a solid" not in msg, "the capped extrusion is accepted")
if res is not None:
    check(isinstance(res, r3.Brep), "the result is a Brep the document can hold")
    check(res.IsSolid, "the result is a solid (IsSolid)")
    check(analytic_and_airtight(res),
          "the result is analytic and airtight (master registered, not faceted)")
    m = kernel.master_of(res)
    vol = ob.volume(m) if m is not None else float("nan")
    check(abs(vol - BOX_MINUS_EXT) / BOX_MINUS_EXT <= REL_TOL,
          f"volume is 216 - 24 pi = {BOX_MINUS_EXT:.6f} (measured {vol:.6f})")
    check(ctx.names() == ["Boolean_1"],
          f"both inputs are removed, only the result remains (now: {ctx.names()})")

print("\n-- reversed: extrusion - box --")
msg, ctx, res = drive([("ext", capped_ext()),
                       ("box", bridge.make_box((0, 0, 0), (SIDE, SIDE, 0), SIDE))],
                      [["ext"], ["box"]])
check(res is not None, f"creates a result (message: {msg!r})")
if res is not None:
    check(analytic_and_airtight(res), "the reversed result is analytic and airtight")
    m = kernel.master_of(res)
    vol = ob.volume(m) if m is not None else float("nan")
    check(abs(vol - EXT_MINUS_BOX) / EXT_MINUS_BOX <= REL_TOL,
          f"volume is 72 pi = {EXT_MINUS_BOX:.6f} (measured {vol:.6f})")
    check(ctx.names() == ["Boolean_1"], "both inputs are removed, only the result remains")

print("\n-- an UNCAPPED extrusion is refused by name, nothing deleted --")
open_ext = r3.Extrusion.Create(bridge.make_circle((SIDE, SIDE, 0), R), H, False)
msg, ctx, res = drive([("box", bridge.make_box((0, 0, 0), (SIDE, SIDE, 0), SIDE)),
                       ("ext", open_ext)],
                      [["box"], ["ext"]])
check(res is None, f"no result is created (message: {msg!r})")
check("not a solid" in (msg or ""), "the refusal names that it is not a solid")
check(sorted(ctx.names()) == ["box", "ext"],
      f"both inputs survive the refusal (now: {ctx.names()})")

print("\n-- a curve is refused by name, nothing deleted --")
crv = bridge.make_circle((SIDE, SIDE, 0), R)
msg, ctx, res = drive([("box", bridge.make_box((0, 0, 0), (SIDE, SIDE, 0), SIDE)),
                       ("crv", crv)],
                      [["box"], ["crv"]])
check(res is None, f"no result is created (message: {msg!r})")
check("not a solid" in (msg or ""), "the refusal names that it is not a solid")
check(sorted(ctx.names()) == ["box", "crv"],
      f"both inputs survive the refusal (now: {ctx.names()})")

print()
if failures:
    print(f"{checks - len(failures)}/{checks} checks passed")
    for f_ in failures:
        print("FAIL:", f_)
    sys.exit(1)
print(f"all {checks} checks passed")
