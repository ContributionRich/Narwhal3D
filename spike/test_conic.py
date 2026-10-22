"""Conic command and exact rational-quadratic geometry."""
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
conics = load("conics")
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
        self.made.append(geom)
        return name, name

    def note_change(self, _label=None):
        pass

    def selected_objects(self):
        return []


def run(lines):
    ctx = Ctx()
    command = commands.ConicCmd()
    message, done = command.begin(ctx)
    for line in lines:
        if done:
            break
        message, done = command.feed(line, ctx)
    return ctx, command, message, done


ctx, command, message, done = run(["-1,0,0", "1,0,0", "0,1,0", "0.25"])
curve = ctx.made[0] if ctx.made else None
mid = curve.PointAt((curve.Domain.T0 + curve.Domain.T1) / 2.0) if curve else None
check(done and curve is not None and curve.Degree == 2 and curve.IsRational
      and len(curve.Points) == 3 and not curve.IsPolyline(),
      "typed rho creates one exact rational quadratic conic")
check(mid is not None and abs(mid.X) < 1e-12 and abs(mid.Y - 0.25) < 1e-12,
      "rho is the symmetric midpoint's normalized apex influence")
check(curve.PointAtStart.DistanceTo(r3.Point3d(-1, 0, 0)) < 1e-12
      and curve.PointAtEnd.DistanceTo(r3.Point3d(1, 0, 0)) < 1e-12
      and "exact rational quadratic" in message,
      "conic endpoints are exact and the result reports its representation")

ctx, command, _message, done = run(["-1,0,0", "1,0,0", "0,1,0", "0,0.5,0"])
through = ctx.made[0] if ctx.made else None
check(done and through is not None
      and through.PointAt((through.Domain.T0 + through.Domain.T1) / 2.0).DistanceTo(
          r3.Point3d(0, 0.5, 0)) < 1e-10,
      "picked through-point solves rho and lies exactly on the conic")

ctx, command, _message, done = run(["Apex", "-1,0,0", "0,1,0", "1,0,0", "0.5"])
check(done and ctx.made and ctx.made[0].PointAtStart.DistanceTo(r3.Point3d(-1, 0, 0)) < 1e-12,
      "Apex option changes pick order without changing geometry semantics")

ctx, command, message, done = run(["Tangent"])
check(not done and not ctx.made and "not implemented yet" in message,
      "unmeasured Tangent mode refuses explicitly")
check("conic" in commands.COMMANDS, "Conic is registered")
preview_rows = preview.segments(
    "Conic", [(-1, 0, 0), (1, 0, 0), (0, 1, 0)], (0, 0.5, 0),
    commands.cplane.WORLD_TOP, cmd=commands.ConicCmd())
check(len(preview_rows) == 2 and len(preview_rows[1]) == 65
      and abs(preview_rows[1][32][1] - 0.5) < 1e-10,
      "live Conic preview follows the cursor-defined rational curve")

for passed, label in checks:
    print("[%s] %s" % ("PASS" if passed else "FAIL", label))
failed = sum(not passed for passed, _ in checks)
print("\n%d/%d passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
