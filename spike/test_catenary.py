"""Catenary command smooth approximation and explicit output modes."""
import importlib.util
import os
import sys
import types

import rhino3dm as r3

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


load("cplane")
load("bridge")
load("coil")
native_coil = load("native_coil")
load("catenary")
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
    command = commands.CatenaryCmd()
    message, done = command.begin(ctx)
    for line in lines:
        if done:
            break
        message, done = command.feed(line, ctx)
    return ctx, command, message, done


inputs = ["-5,0,0", "5,0,0", "0,-1,0", "0,-2,0"]
native_coil._reset_for_tests()
before_native = native_coil._native_catenary_calls
ctx, command, message, done = run(inputs)
curve = ctx.made[0][1] if ctx.made else None
mid = curve.PointAt((curve.Domain.T0 + curve.Domain.T1)/2) if curve else None
check(done and curve is not None and curve.Degree == 3 and not curve.IsPolyline(),
      "default Catenary creates one smooth cubic NURBS")
check(native_coil._native_catenary_calls == before_native + 1,
      "production Catenary uses the measured-winning Rust planner")
check(curve is not None
      and curve.PointAtStart.DistanceTo(r3.Point3d(-5, 0, 0)) < 1e-12
      and curve.PointAtEnd.DistanceTo(r3.Point3d(5, 0, 0)) < 1e-12
      and mid.DistanceTo(r3.Point3d(0, -2, 0)) < 1e-9,
      "smooth catenary has exact endpoints and analytic midpoint sag")
check("approximate NURBS deviation" in message,
      "result explicitly reports that Catenary is approximate and gives deviation")

ctx, command, _message, done = run(
    ["Output=Polyline", "PointCount=17"] + inputs)
polyline = ctx.made[0][1] if ctx.made else None
check(done and isinstance(polyline, r3.PolylineCurve) and polyline.PointCount == 17,
      "explicit Polyline output uses the requested point count")

ctx, command, _message, done = run(["MarkApex=Yes"] + inputs)
points = [geom for kind, geom in ctx.made if kind == "Point"]
check(done and len(points) == 1
      and points[0].Location.DistanceTo(r3.Point3d(0, -2, 0)) < 1e-12,
      "MarkApex adds the analytic apex point")
ctx, command, message, done = run(
    ["-5,0,0", "5,0,0", "0,-1,0", "1,-2,0"])
check(done and not ctx.made and "midpoint sag" in message,
      "unsupported asymmetric ThroughPoint refuses explicitly")
ctx, command, message, done = run(["Mode=Length"] + inputs)
check(done and not ctx.made and "not implemented yet" in message,
      "unbuilt Length mode refuses explicitly")
check("catenary" in commands.COMMANDS, "Catenary is registered")
preview_rows = preview.segments(
    "Catenary", [(-5, 0, 0), (5, 0, 0), (0, -1, 0)], (0, -2, 0),
    commands.cplane.WORLD_TOP, cmd=commands.CatenaryCmd())
check(len(preview_rows) == 1 and len(preview_rows[0]) == 65
      and abs(preview_rows[0][32][1] + 2.0) < 1e-9,
      "live Catenary preview follows the analytic midpoint sag")

for passed, label in checks:
    print("[%s] %s" % ("PASS" if passed else "FAIL", label))
failed = sum(not passed for passed, _ in checks)
print("\n%d/%d passed" % (len(checks)-failed, len(checks)))
raise SystemExit(1 if failed else 0)
