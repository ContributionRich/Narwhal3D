"""Spiral command: variable-radius, Flat, options and smooth NURBS geometry."""
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "native", "pure_core", "target",
                   "x86_64-unknown-linux-gnu", "release", "libnarwhal_pure_core.so")
os.environ["NARWHAL_PURE_CORE"] = LIB
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


cplane = load("cplane")
load("bridge")
model = load("model")
load("options")
commands = load("commands")
checks = []


def check(condition, label):
    checks.append((bool(condition), label))


class Ctx:
    def __init__(self, plane=cplane.WORLD_TOP):
        self.model = model.NarwhalModel()
        self.plane = plane
        self.made = []

    def active_cplane(self):
        return self.plane

    def add_geometry(self, geom, base_name, layer_index=None):
        name = "%s_%d" % (base_name, len(self.made) + 1)
        self.model.add_object(geom, name, 0)
        self.made.append((name, geom))
        return name, name

    def note_change(self, _label=None):
        pass

    def selected_objects(self):
        return []


def run(lines, plane=cplane.WORLD_TOP):
    ctx = Ctx(plane)
    command = commands.SpiralCmd()
    message, done = command.begin(ctx)
    for line in lines:
        if done:
            break
        message, done = command.feed(line, ctx)
    return ctx, command, message, done


ctx, command, message, done = run(["0,0,0", "0,0,4", "1", "3"])
curve = ctx.made[0][1] if ctx.made else None
check(done and curve is not None and curve.Degree == 3 and not curve.IsPolyline(),
      "default Spiral creates one smooth cubic NURBS")
check(curve is not None
      and curve.PointAtStart.DistanceTo(commands.r3.Point3d(1, 0, 0)) < 1e-9
      and curve.PointAtEnd.DistanceTo(commands.r3.Point3d(3, 0, 4)) < 1e-8,
      "start/end radii and axis determine the spiral endpoints")
check("radii 1->3" in message and "NURBS deviation" in message,
      "result reports radii and achieved approximation deviation")

ctx, command, message, done = run(
    ["Pitch=2", "0,0,0", "0,0,10", "1", "2"])
check(done and "5 turns" in message,
      "Pitch precedence derives Spiral turn count")

ctx, command, _message, done = run(
    ["Diameter/Radius=Diameter", "0,0,0", "0,0,4", "2", "6"])
diameter = ctx.made[0][1] if ctx.made else None
check(done and diameter is not None
      and diameter.PointAtStart.DistanceTo(commands.r3.Point3d(1, 0, 0)) < 1e-9
      and diameter.PointAtEnd.DistanceTo(commands.r3.Point3d(3, 0, 4)) < 1e-8
      and command.steps[2][0] == "First diameter:"
      and command.steps[3][0] == "Second diameter:",
      "Diameter mode applies to both radii and both prompts")

ctx, command, message, done = run(
    ["Flat", "Turns=2", "0,0,0", "0,0,5", "1", "4"])
flat = ctx.made[0][1] if ctx.made else None
check(done and flat is not None
      and abs(flat.PointAtStart.Z) < 1e-12 and abs(flat.PointAtEnd.Z) < 1e-12
      and flat.PointAtEnd.DistanceTo(commands.r3.Point3d(4, 0, 0)) < 1e-8
      and "pitch 0" in message,
      "Flat creates a truly planar variable-radius spiral")

ctx, command, _message, done = run(
    ["ReverseTwist", "0,0,0", "0,0,4", "1", "3"])
reverse = ctx.made[0][1] if ctx.made else None
check(done and reverse is not None and curve.Points[1].Y > 0.0 and reverse.Points[1].Y < 0.0,
      "ReverseTwist reverses Spiral winding")

ctx, command, _message, done = run(
    ["Vertical", "0,0,0", "4", "1", "3"], cplane.WORLD_RIGHT)
vertical = ctx.made[0][1] if ctx.made else None
check(done and vertical is not None
      and vertical.PointAtEnd.DistanceTo(commands.r3.Point3d(4, 3, 0)) < 1e-8,
      "Vertical follows the active Right CPlane normal")

ctx, command, message, done = run(["AroundCurve"])
check(not done and not ctx.made and "not implemented yet" in message,
      "AroundCurve refuses explicitly")
check("spiral" in commands.COMMANDS
      and isinstance(commands.COMMANDS["spiral"], commands.SpiralCmd),
      "Spiral is registered as a runnable command")

for passed, label in checks:
    print("[%s] %s" % ("PASS" if passed else "FAIL", label))
failed = sum(not passed for passed, _ in checks)
print("\n%d/%d passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
