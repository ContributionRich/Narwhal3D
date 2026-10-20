#!/usr/bin/env python3
"""`ExtrudeCrv`'s distance step accepts a picked point, not just a number.

Regression: after selecting a curve, the distance prompt must accept a viewport click.

MEASURED (commands/extrudecrv.htm):

  * "Select a curve. Specify a distance."
  * "A planar curve extrudes vertically to the curve plane by default. If the curve is not
    planar, the default extrusion direction is the z direction of the active CPlane."
  * "The extrusion preview direction in the viewport defines the positive direction for
    distance input."

So the distance from a picked point is its **signed** projection onto the extrusion
direction. Signed matters: picking below the curve must extrude downwards, not take an
absolute value, and a test that only picks above could not tell the difference.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_extrude_distance.py
"""
import importlib.util
import math
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, rel, is_pkg=False):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    if is_pkg:
        mod.__path__ = [os.path.dirname(os.path.join(ROOT, rel))]
    spec.loader.exec_module(mod)
    return mod


_load("narwhal3d.kernel", "narwhal3d/kernel/__init__.py", is_pkg=True)
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
import rhino3dm as r3  # noqa: E402

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


print("=" * 74)
print("ExtrudeCrv: a picked point answers the distance step")
print("=" * 74)

print("\n-- bridge.profile_normal: which way does an extrusion run? --")
circle_xy = r3.NurbsCurve.CreateFromCircle(r3.Circle(r3.Point3d(0, 0, 0), 3.0))
n = bridge.profile_normal(circle_xy)
check(n is not None and abs(abs(n[2]) - 1.0) < 1e-9,
      f"a circle in world XY has a normal along Z {n}")
# A circle in the XZ plane -- the case that catches an assumed world +Z.
# MEASURED: `r3.Circle` takes only (radius) or (center, radius), with NO plane overload, so
# the circle is built in XY and mapped. (This API detail has now bitten three probes.)
_xy = r3.Plane(r3.Point3d(0, 0, 0), r3.Vector3d(1, 0, 0), r3.Vector3d(0, 1, 0))
plane_xz = r3.Plane(r3.Point3d(0, 0, 0), r3.Vector3d(1, 0, 0), r3.Vector3d(0, 0, 1))
circle_xz = r3.NurbsCurve.CreateFromCircle(r3.Circle(3.0))
circle_xz.Transform(r3.Transform.PlaneToPlane(_xy, plane_xz))
n2 = bridge.profile_normal(circle_xz)
check(n2 is not None and abs(abs(n2[1]) - 1.0) < 1e-9,
      f"a circle in the XZ plane has a normal along Y {n2} -- an extrusion that assumed "
      f"world +Z would run along the curve's own plane and collapse")
square = r3.PolylineCurve([r3.Point3d(*p) for p in
                           [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0), (0, 0, 0)]])
n3 = bridge.profile_normal(square.ToNurbsCurve())
check(n3 is not None and abs(abs(n3[2]) - 1.0) < 1e-9,
      f"a polyline square's normal is found too (Newell) {n3}")
line = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(5, 0, 0)).ToNurbsCurve()
check(bridge.profile_normal(line) is None,
      "a straight LINE has no plane normal, and None is the honest answer -- a cross "
      "product of collinear samples would have returned a meaningless direction")

print("\n-- the signed projection the command uses --")


def signed(curve, picked):
    """What ExtrudeCmd._parse_distance computes for a picked point."""
    start = curve.PointAtStart
    d = bridge.profile_normal(curve)
    return sum((picked[i] - (start.X, start.Y, start.Z)[i]) * d[i] for i in range(3))


check(abs(signed(circle_xy, (0.0, 0.0, 7.0)) - 7.0) < 1e-9,
      "picking 7 above a world-XY circle gives distance 7")
check(abs(signed(circle_xy, (5.0, -2.0, 7.0)) - 7.0) < 1e-9,
      "and only the component ALONG the direction counts -- sideways movement does not "
      "change the extrusion distance")
below = signed(circle_xy, (0.0, 0.0, -4.0))
check(abs(below + 4.0) < 1e-9,
      f"picking BELOW gives a NEGATIVE distance ({below}) -- the help makes the preview "
      f"direction the positive one, so an absolute value would extrude the wrong way")
check(abs(signed(circle_xy, (0.0, 0.0, 0.0))) < 1e-9,
      "picking in the curve's own plane gives 0")
# the XZ circle: distance must be measured along Y, not Z
check(abs(abs(signed(circle_xz, (0.0, 6.0, 0.0))) - 6.0) < 1e-9,
      "for a circle in the XZ plane the distance is measured along Y")
check(abs(signed(circle_xz, (0.0, 0.0, 9.0))) < 1e-9,
      "and a point moved along Z -- inside that circle's own plane -- extrudes 0, which "
      "is the check that would fail if the direction were hard-coded to world Z")

print("\n-- and the resulting solid is analytic, in the right place --")
ext = bridge.make_extrusion(circle_xy, signed(circle_xy, (0.0, 0.0, 5.0)))
bb = ext.GetBoundingBox()
check(abs(bb.Min.Z) < 1e-9 and abs(bb.Max.Z - 5.0) < 1e-9,
      f"a picked height of 5 extrudes from z=0 to z=5 ({bb.Min.Z:.3f}..{bb.Max.Z:.3f})")
down = bridge.make_extrusion(circle_xy, signed(circle_xy, (0.0, 0.0, -4.0)))
bbd = down.GetBoundingBox()
check(abs(bbd.Min.Z + 4.0) < 1e-9 and abs(bbd.Max.Z) < 1e-9,
      f"and a point picked BELOW extrudes downwards ({bbd.Min.Z:.3f}..{bbd.Max.Z:.3f})")

print("\n-- the step is declared clickable, which is what makes the mouse work --")
commands = None
try:
    commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
except Exception as e:  # noqa: BLE001
    print(f"  .. commands.py needs bpy; step-kind checks skipped ({type(e).__name__})")
if commands is not None:
    cmd = type(commands.COMMANDS["extrudecrv"])()
    kinds = [k for _, k in cmd.steps]
    check(commands.DISTANCE in kinds,
          f"ExtrudeCrv's steps are {kinds} -- the distance step is DISTANCE, not NUMBER")
    check(commands.DISTANCE in commands.CLICKABLE_KINDS,
          "and DISTANCE is in CLICKABLE_KINDS, which is what lets a viewport click answer "
          "the prompt at all")
    check(cmd.steps[0][1] == commands.OBJECTS_MULTI,
          "the curve step takes the whole pre-selected profile set for nested holes")

print("\n-- the live preview, which is what 'interactive' actually means --")
# Accepting a picked point answers the prompt, but that is only half the interaction. Nothing was
# DRAWN while the mouse moved, because `PREVIEWS` had no extrudecrv entry at all.
preview = _load("narwhal3d.preview", "narwhal3d/preview.py")


class _Plane:
    zaxis = (0.0, 0.0, 1.0)
    normal = (0.0, 0.0, 1.0)


check(preview.has_preview("extrudecrv"),
      "ExtrudeCrv now HAS a preview -- it had none, which is why nothing moved with the "
      "mouse even after the step became clickable")
check("extrudecrv" in preview.PREVIEWS_WITH_DOC,
      "and it is in PREVIEWS_WITH_DOC, because its first arg is a curve NAME rather than a "
      "point, so it cannot be drawn from args alone")

_doc = {"curve1": circle_xy}
segs = preview.segments("extrudecrv", ["curve1"], (0.0, 0.0, 5.0), _Plane(),
                        resolve=lambda n: _doc.get(n))
check(len(segs) >= 2,
      f"it draws {len(segs)} polylines for a 5-unit pick: the profile, a ghost at the "
      f"extruded height, and side edges")
zs = [round(p[2], 6) for poly in segs for p in poly]
check(0.0 in zs and 5.0 in zs,
      f"spanning z=0 (the profile) to z=5 (the cursor height) -- so the preview shows the "
      f"shape the command will actually build")
deep = preview.segments("extrudecrv", ["curve1"], (0.0, 0.0, -3.0), _Plane(),
                        resolve=lambda n: _doc.get(n))
zs2 = [round(p[2], 6) for poly in deep for p in poly]
check(-3.0 in zs2,
      "picking BELOW previews downwards, matching the signed distance the command uses")
check(preview.segments("extrudecrv", ["curve1"], (0, 0, 5), _Plane(), resolve=None) == [],
      "with NO resolver it draws nothing rather than guessing a shape")
check(preview.segments("extrudecrv", ["nope"], (0, 0, 5), _Plane(),
                       resolve=lambda n: _doc.get(n)) == [],
      "and an unknown curve name draws nothing")
# the other previews must keep working through the changed dispatcher
line_segs = preview.segments("line", [(0.0, 0.0, 0.0)], (3.0, 0.0, 0.0), _Plane())
check(len(line_segs) == 1,
      "while the 20 existing previews still work unchanged through the new dispatcher "
      "(Line still draws its rubber band)")

print("\n-- the preview follows the OPTIONS that change the shape (BothSides / Direction / "
      "Tapered) --")


class _FakeCmd:
    """Just the surface `preview_extrudecrv` reads from a running ExtrudeCrv."""

    def __init__(self, opt=None, dir_mode=False, args=None, draft=None):
        self.opt = dict(opt or {})
        self._dir_mode = dir_mode
        self.args = list(args or [])
        self.__draft = draft

    def _picked_direction(self):
        a, b = self.args[1], self.args[2]
        v = [b[i] - a[i] for i in range(3)]
        n = math.sqrt(sum(c * c for c in v))
        return tuple(c / n for c in v)

    def _draft(self):
        return self.__draft


def _span(polys, axis):
    vals = [p[axis] for poly in polys for p in poly]
    return (round(min(vals), 6), round(max(vals), 6))


def _seg(name, args, cursor, cmd):
    return preview.segments(name, args, cursor, _Plane(), resolve=lambda n: _doc.get(n),
                            cmd=cmd)


plain = _seg("extrudecrv", ["curve1"], (0.0, 0.0, 4.0), _FakeCmd())
check(_span(plain, 2) == (0.0, 4.0),
      f"plain, h=4: the preview spans z 0..4 {_span(plain, 2)}")
both = _seg("extrudecrv", ["curve1"], (0.0, 0.0, 4.0), _FakeCmd({"BothSides": True}))
check(_span(both, 2) == (-4.0, 4.0),
      f"BothSides, h=4: it spans z -4..4 {_span(both, 2)} -- the commit runs from "
      f"-direction to +direction (MEASURED extrudecrv.htm), so a one-sided ghost lied")

# Direction: a slanted direction (1, 0, 1)/sqrt2. Height is measured from the curve's START
# point (3,0,0), as `_parse_distance` does, so the cursor (3,0,4) projects to h = 4/sqrt2 and
# the top ghost is shifted by (2, 0, 2) -- X moves, which the profile-normal route never does
# for a world-XY circle (r=3, so plain X spans exactly -3..3).
dcmd = _FakeCmd(dir_mode=True, args=["curve1", (0.0, 0.0, 0.0), (1.0, 0.0, 1.0)])
directed = _seg("extrudecrv", dcmd.args, (3.0, 0.0, 4.0), dcmd)
check(_span(directed, 0) == (-3.0, 5.0) and _span(directed, 2) == (0.0, 2.0),
      f"Direction (1,0,1): the ghost slides along the PICKED direction -- x "
      f"{_span(directed, 0)}, z {_span(directed, 2)}; expected x -3..5, z 0..2")
picking = _FakeCmd(dir_mode=True, args=["curve1", (1.0, 1.0, 0.0)])
mid = _seg("extrudecrv", picking.args, (1.0, 1.0, 6.0), picking)
check(any(poly == [(1.0, 1.0, 0.0), (1.0, 1.0, 6.0)] for poly in mid)
      and _span(mid, 2) == (0.0, 6.0) and len(mid) == 2,
      "while the direction is still being picked it draws the profile and the "
      "base->cursor band only, not an extrusion along a direction not yet chosen")

# Tapered: r=3 circle, h=4, 10 deg inward -> the top radius is 3 - 4 tan(10) (exact for a
# circle, where the commit is a cone frustum).
check(preview.has_preview("extrudecrvtapered"), "ExtrudeCrvTapered has a preview")
taper = _seg("extrudecrvtapered", ["curve1"], (0.0, 0.0, 4.0), _FakeCmd(draft=10.0))
top_r = max(math.hypot(p[0], p[1]) for poly in taper for p in poly if abs(p[2] - 4.0) < 1e-9)
want = 3.0 - 4.0 * math.tan(math.radians(10.0))
check(abs(top_r - want) < 1e-6,
      f"DraftAngle=10, h=4: the top ghost has radius {top_r:.6f} (want {want:.6f}) -- "
      f"tapering INWARD like the committed frustum")
flipped = _seg("extrudecrvtapered", ["curve1"], (0.0, 0.0, 4.0),
               _FakeCmd({"FlipAngle": True}, draft=10.0))
flip_r = max(math.hypot(p[0], p[1]) for poly in flipped for p in poly
             if abs(p[2] - 4.0) < 1e-9)
check(abs(flip_r - (3.0 + 4.0 * math.tan(math.radians(10.0)))) < 1e-6,
      f"and FlipAngle tapers OUTWARD instead (top radius {flip_r:.6f})")
base_r = [math.hypot(p[0], p[1]) for poly in taper for p in poly if abs(p[2]) < 1e-9]
check(base_r and all(abs(r - 3.0) < 1e-6 for r in base_r),
      "while the profile itself stays at radius 3")

if commands is not None:
    print("\n-- Direction mode: the distance click is constrained to the PICKED axis --")
    ecmd = type(commands.COMMANDS["extrudecrv"])()
    ecmd.args = ["curve1", (0.0, 0.0, 0.0), (1.0, 0.0, 1.0)]
    ecmd._dir_mode = True
    ax = ecmd.axis_constraint(_Plane(), resolve=lambda n: _doc.get(n))
    s2 = 1 / math.sqrt(2)
    check(ax is not None and all(abs(ax[1][i] - (s2, 0.0, s2)[i]) < 1e-9 for i in range(3)),
          f"axis_constraint at the distance step follows the picked direction ({ax}) -- it "
          f"returned None, so the click fell flat onto the CPlane")
    ecmd.args = ["curve1", (0.0, 0.0, 0.0)]
    check(ecmd.axis_constraint(_Plane(), resolve=lambda n: _doc.get(n)) is None,
          "and there is no axis constraint while the direction itself is being picked")

print()
print("=" * 74)
print(f"{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("EXTRUDECRV DISTANCE OK")
