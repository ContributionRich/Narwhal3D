"""Hyperbola command exact rational branch construction."""
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
    command = commands.HyperbolaCmd()
    message, done = command.begin(ctx)
    for line in lines:
        if done:
            break
        message, done = command.feed(line, ctx)
    return ctx, command, message, done


# A=3, B=4, focus distance C=5; endpoint (3.75, 3) lies on x²/9-y²/16=1.
ctx, command, message, done = run(["0,0,0", "5,0,0", "3.75,3,0"])
curve = ctx.made[0][1] if ctx.made else None
check(done and curve is not None and curve.Degree == 2 and curve.IsRational
      and len(curve.Points) == 3 and not curve.IsPolyline(),
      "default Center mode creates one exact rational quadratic branch")
check(curve is not None
      and curve.PointAtStart.DistanceTo(r3.Point3d(3, 0, 0)) < 1e-10
      and curve.PointAtEnd.DistanceTo(r3.Point3d(3.75, 3, 0)) < 1e-10
      and "A=3" in message and "B=4" in message,
      "center/focus/end recover the exact A=3 B=4 hyperbola")
maximum = 0.0
if curve:
    for index in range(101):
        point = curve.PointAt(curve.Domain.T0 + (curve.Domain.T1 - curve.Domain.T0) * index / 100)
        maximum = max(maximum, abs(point.X * point.X / 9.0 - point.Y * point.Y / 16.0 - 1.0))
check(maximum < 1e-12, "every sampled point satisfies the analytic hyperbola equation")

ctx, command, message, done = run(
    ["BothBranches=Yes", "0,0,0", "5,0,0", "3.75,3,0"])
branches = [geom for kind, geom in ctx.made if kind == "Hyperbola"]
check(done and len(branches) == 2
      and branches[1].PointAtStart.DistanceTo(r3.Point3d(-3, 0, 0)) < 1e-10
      and "2 hyperbola branches" in message,
      "BothBranches creates the centrally reflected exact branch")

ctx, command, _message, done = run(
    ["MarkFoci=Yes", "0,0,0", "5,0,0", "3.75,3,0"])
points = [geom.Location for kind, geom in ctx.made if kind == "Point"]
check(done and len(points) == 2
      and points[0].DistanceTo(r3.Point3d(5, 0, 0)) < 1e-12
      and points[1].DistanceTo(r3.Point3d(-5, 0, 0)) < 1e-12,
      "MarkFoci creates both exact focus points")
ctx, command, message, done = run(["FromCoefficient"])
check(not done and not ctx.made and "not implemented yet" in message,
      "unbuilt coefficient mode refuses explicitly")
check("hyperbola" in commands.COMMANDS, "Hyperbola is registered")
preview_cmd = commands.HyperbolaCmd()
preview_cmd.opt["ShowAsymptotes"] = True
preview_rows = preview.segments(
    "Hyperbola", [(0, 0, 0), (5, 0, 0)], (3.75, 3, 0),
    commands.cplane.WORLD_TOP, cmd=preview_cmd)
check(len(preview_rows) == 3 and len(preview_rows[0]) == 65
      and abs(preview_rows[0][-1][0] - 3.75) < 1e-10
      and abs(preview_rows[0][-1][1] - 3.0) < 1e-10,
      "live Hyperbola preview follows the exact branch and shows asymptotes")

for passed, label in checks:
    print("[%s] %s" % ("PASS" if passed else "FAIL", label))
failed = sum(not passed for passed, _ in checks)
print("\n%d/%d passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
