"""CurveThroughPolyline command fidelity and exact vertex conversion."""
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


def load(name):
    spec = importlib.util.spec_from_file_location(
        "narwhal3d." + name, os.path.join(ROOT, "narwhal3d", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["narwhal3d." + name] = mod
    setattr(pkg, name, mod)
    spec.loader.exec_module(mod)
    return mod


load("cplane")
bridge = load("bridge")
interpolate = load("interpolate")
model = load("model")
load("options")
commands = load("commands")
checks = []


def check(condition, label):
    checks.append((bool(condition), label))


VERTICES = [(0.0, 0.0, 0.0), (1.0, 2.0, 0.0),
            (4.0, 3.0, 0.0), (8.0, 0.0, 0.0)]


class Ctx:
    def __init__(self):
        self.model = model.NarwhalModel()
        self.model.add_object(r3.PolylineCurve([r3.Point3d(*p) for p in VERTICES]),
                              "Source", 0)
        self.made = []

    def geom_by_name(self, name):
        item = self.model.file.Objects.FindId(self.model.object_id_by_name(name))
        return item.Geometry if item is not None else None

    def object_by_name(self, _name):
        return None

    def add_geometry(self, geom, base_name, layer_index=None):
        name = "%s_%d" % (base_name, len(self.made) + 1)
        self.model.add_object(geom, name, 0)
        self.made.append(geom)
        return name, name

    def note_change(self, _label=None):
        pass


ctx = Ctx()
command = commands.CurveThroughPolylineCmd()
command.args = [["Source"]]
message, done = command.execute(ctx)
curve = ctx.made[0] if ctx.made else None
controls = [(curve.Points[i].X, curve.Points[i].Y, curve.Points[i].Z)
            for i in range(len(curve.Points))] if curve else []
check(done and controls == VERTICES and "Created 1 curve" in message,
      "default ControlPoint curve controls match every polyline vertex")

ctx = Ctx()
command = commands.CurveThroughPolylineCmd()
command.args = [["Source"]]
command.opt["CurveType"] = "Interpolated"
command.opt["Knots"] = "Chord"
message, done = command.execute(ctx)
curve = ctx.made[0] if ctx.made else None
params = interpolate.parameters(VERTICES, "Chord")
domain = curve.Domain if curve else None
errors = [curve.PointAt(domain.T0 + value * (domain.T1 - domain.T0)).DistanceTo(r3.Point3d(*point))
          for value, point in zip(params, VERTICES)] if curve else [math.inf]
check(done and max(errors) < 1e-9,
      "Interpolated curve passes through every source vertex")

ctx = Ctx()
command = commands.CurveThroughPolylineCmd()
command.args = [["Source"]]
command.opt["DeleteInput"] = True
message, done = command.execute(ctx)
check(done and ctx.model.object_id_by_name("Source") is None
      and ctx.model.object_count() == 1,
      "DeleteInput removes the source only after successful conversion")

ctx = Ctx()
command = commands.CurveThroughPolylineCmd()
command.args = [["Source"]]
command.opt["Degree"] = 2
command.execute(ctx)
check(ctx.made and ctx.made[0].Degree == 2,
      "Degree controls the supported output degree")

ctx = Ctx()
command = commands.CurveThroughPolylineCmd()
message, done = command.on_mode("SubCrv", ctx)
check(not done and "not implemented yet" in message and not ctx.made,
      "SubCrv refuses explicitly")
check("curvethroughpolyline" in commands.COMMANDS,
      "CurveThroughPolyline is registered")

for passed, label in checks:
    print("[%s] %s" % ("PASS" if passed else "FAIL", label))
failed = sum(not passed for passed, _ in checks)
print("\n%d/%d passed" % (len(checks) - failed, len(checks)))
raise SystemExit(1 if failed else 0)
