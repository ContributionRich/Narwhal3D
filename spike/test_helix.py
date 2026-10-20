"""Helix command: measured flow/options and smooth NURBS geometry."""
import importlib.util
import math
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
    command = commands.HelixCmd()
    message, done = command.begin(ctx)
    for line in lines:
        if done:
            break
        message, done = command.feed(line, ctx)
    return ctx, command, message, done


ctx, command, message, done = run(["0,0,0", "0,0,10", "2"])
curve = ctx.made[0][1] if ctx.made else None
check(done and curve is not None and curve.Degree == 3 and not curve.IsPolyline(),
      "default Helix creates one smooth cubic NURBS")
check(curve is not None and curve.PointAtStart.DistanceTo(
      commands.r3.Point3d(2, 0, 0)) < 1e-9
      and curve.PointAtEnd.DistanceTo(commands.r3.Point3d(2, 0, 10)) < 1e-8,
      "default axis and radius determine exact helix endpoints")
check("10 turns" in message and "NURBS deviation" in message,
      "result reports turns, pitch and achieved approximation deviation")

ctx, command, message, done = run(
    ["Pitch=2", "0,0,0", "0,0,10", "2"])
check(done and len(ctx.made) == 1 and "5 turns" in message,
      "Pitch takes precedence and derives the turn count from axis length")

ctx, command, message, done = run(
    ["Diameter/Radius=Diameter", "0,0,0", "0,0,10", "4"])
diameter_curve = ctx.made[0][1] if ctx.made else None
check(done and diameter_curve is not None
      and diameter_curve.PointAtStart.DistanceTo(commands.r3.Point3d(2, 0, 0)) < 1e-9
      and "Diameter:" in command.steps[2][0],
      "Diameter mode uses half the entered value and keeps the prompt truthful")

forward = curve
ctx, command, _message, done = run(
    ["ReverseTwist", "0,0,0", "0,0,10", "2"])
reverse = ctx.made[0][1] if ctx.made else None
check(done and reverse is not None
      and forward.Points[1].Y > 0.0 and reverse.Points[1].Y < 0.0,
      "ReverseTwist changes winding direction")

ctx, command, _message, done = run(
    ["Vertical", "0,0,0", "10", "2"], cplane.WORLD_FRONT)
vertical = ctx.made[0][1] if ctx.made else None
check(done and vertical is not None
      and vertical.PointAtEnd.DistanceTo(commands.r3.Point3d(2, -10, 0)) < 1e-8,
      "Vertical follows the active Front CPlane normal")

ctx, command, message, done = run(["AroundCurve"])
check(not done and not ctx.made and "not implemented yet" in message,
      "AroundCurve refuses explicitly instead of drawing the wrong geometry")
check("helix" in commands.COMMANDS and isinstance(commands.COMMANDS["helix"], commands.HelixCmd),
      "Helix is registered as a runnable command")


class UndoCtx(commands.NarwhalContext):
    def __init__(self):
        super().__init__(model.NarwhalModel())

    def active_cplane(self):
        return cplane.WORLD_TOP

    def add_geometry(self, geom, base_name, layer_index=None):
        self.note_change(base_name)
        name = self.next_name(base_name)
        object_id, _attrs = self.model.add_object(geom, name, 0)
        return object_id, name

    def rebuild_display(self):
        pass

    def selected_objects(self):
        return []


undo_ctx = UndoCtx()
for cls, values in ((commands.HelixCmd, ["0,0,0", "0,0,5", "1"]),
                    (commands.SpiralCmd, ["0,0,0", "0,0,5", "1", "2"])):
    instance = cls()
    instance.begin(undo_ctx)
    for value in values:
        instance.feed(value, undo_ctx)
check(undo_ctx.model.object_count() == 2
      and undo_ctx.undo.undo_names() == ["Spiral", "Helix"],
      "Helix and Spiral each create one correctly named undo record")
check(undo_ctx.undo_last() == "Spiral" and undo_ctx.model.object_count() == 1
      and undo_ctx.undo_last() == "Helix" and undo_ctx.model.object_count() == 0,
      "undo removes each coil command atomically")

for passed, label in checks:
    print("[%s] %s" % ("PASS" if passed else "FAIL", label))
failed = sum(not passed for passed, _ in checks)
print("\n%d/%d passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
