"""Parabola command exact Focus/Vertex construction."""
import importlib.util
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name):
    spec = importlib.util.spec_from_file_location(
        "narwhal3d." + name, os.path.join(ROOT, "narwhal3d", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["narwhal3d." + name] = mod
    setattr(pkg, name, mod)
    spec.loader.exec_module(mod)
    return mod


load("cplane")
load("bridge")
load("conics")
model = load("model")
load("options")
preview = load("preview")
commands = load("commands")
checks = []


def check(condition, label):
    checks.append((bool(condition), label))


class Ctx:
    def __init__(self):
        self.model = model.NarwhalModel()
        self.made = []

    def add_geometry(self, geom, base_name, layer_index=None):
        name = "%s_%d" % (base_name, len(self.made) + 1)
        self.model.add_object(geom, name, 0)
        self.made.append((base_name, geom))
        return name, name

    def note_change(self, _label=None):
        pass

    def selected_objects(self):
        return []


def run(lines):
    ctx = Ctx()
    command = commands.ParabolaCmd()
    message, done = command.begin(ctx)
    for line in lines:
        if done:
            break
        message, done = command.feed(line, ctx)
    return ctx, command, message, done


ctx, command, message, done = run(["1,0,0", "2,0,0", "1,2,0"])
curve = ctx.made[0][1] if ctx.made else None
mid = curve.PointAt((curve.Domain.T0 + curve.Domain.T1) / 2.0) if curve else None
check(done and curve is not None and curve.Degree == 2 and not curve.IsRational
      and not curve.IsPolyline(),
      "default Focus mode creates one exact polynomial quadratic parabola")
check(mid is not None and mid.DistanceTo(r3.Point3d(0, 0, 0)) < 1e-10
      and curve.PointAtStart.DistanceTo(r3.Point3d(1, -2, 0)) < 1e-10
      and curve.PointAtEnd.DistanceTo(r3.Point3d(1, 2, 0)) < 1e-10,
      "Focus/direction/end reconstruct the exact symmetric parabola")
check("focus-to-vertex 1" in message and "exact quadratic" in message,
      "result reports the defining focal distance and exact representation")

ctx, command, _message, done = run(["Vertex", "0,0,0", "1,0,0", "1,2,0"])
curve = ctx.made[0][1] if ctx.made else None
check(done and curve is not None
      and curve.PointAt((curve.Domain.T0 + curve.Domain.T1) / 2).DistanceTo(
          r3.Point3d(0, 0, 0)) < 1e-10,
      "Vertex mode uses vertex/focus/end order and the same exact parabola")

ctx, command, _message, done = run(
    ["Vertex", "Half=Yes", "0,0,0", "1,0,0", "1,2,0"])
half = ctx.made[0][1] if ctx.made else None
check(done and half is not None
      and half.PointAtStart.DistanceTo(r3.Point3d(0, 0, 0)) < 1e-10
      and half.PointAtEnd.DistanceTo(r3.Point3d(1, 2, 0)) < 1e-10,
      "Half draws exactly the vertex-to-end half parabola")

ctx, command, _message, done = run(
    ["MarkFocus=Yes", "1,0,0", "2,0,0", "1,2,0"])
check(done and [kind for kind, _geom in ctx.made] == ["Parabola", "Point"]
      and ctx.made[1][1].Location.DistanceTo(r3.Point3d(1, 0, 0)) < 1e-12,
      "MarkFocus adds the exact focus point")
check("parabola" in commands.COMMANDS, "Parabola is registered")
preview_rows = preview.segments(
    "Parabola", [(1, 0, 0), (2, 0, 0)], (1, 2, 0),
    commands.cplane.WORLD_TOP, cmd=commands.ParabolaCmd())
check(len(preview_rows) == 1 and len(preview_rows[0]) == 65
      and preview_rows[0][32] == (0.0, 0.0, 0.0),
      "live Focus preview shows the exact symmetric parabola")

for passed, label in checks:
    print("[%s] %s" % ("PASS" if passed else "FAIL", label))
failed = sum(not passed for passed, _ in checks)
print("\n%d/%d passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
