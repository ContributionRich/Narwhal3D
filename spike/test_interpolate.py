#!/usr/bin/env python3
"""TESTS for narwhal3d/interpolate.py -- global NURBS interpolation.

THE ONE PROPERTY THAT MATTERS: an interpolating curve PASSES THROUGH its data points.
Checking that "something smooth came back" is what let the bug live -- the old `InterpCrv`
returned a perfectly smooth degree-3 curve that simply did not go through the clicked
points. So every check here evaluates the result and compares against the input.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_interpolate.py
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


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


interp = _load("narwhal3d.interpolate", "narwhal3d/interpolate.py")

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
print("GLOBAL NURBS INTERPOLATION")
print("=" * 74)

print("\n-- parameterisation (the reference application's three knot styles) --")
PTS = [(0, 0, 0), (1, 0, 0), (3, 0, 0), (7, 0, 0)]
u = interp.parameters(PTS, "Uniform")
check(u == [0.0, 1 / 3, 2 / 3, 1.0],
      "Uniform spaces parameters equally, ignoring physical distance "
      "(MEASURED: 'not based on the physical spacing of the points')")
c = interp.parameters(PTS, "Chord")
check(abs(c[1] - 1 / 7) < 1e-12 and abs(c[2] - 3 / 7) < 1e-12,
      "Chord spaces them by distance (1,2,4 of 7 -> 1/7, 3/7, 1)")
s = interp.parameters(PTS, "SqrtChrd")
tot = 1 + math.sqrt(2) + 2
check(abs(s[1] - 1 / tot) < 1e-12,
      "SqrtChrd uses the square root of each distance")
check(c[0] == 0.0 and c[-1] == 1.0, "and every style is normalised to [0, 1]")
for style in interp.KNOT_STYLES:
    check(interp.parameters(PTS, style)[-1] == 1.0, f"  {style} ends exactly at 1.0")

try:
    interp.parameters(PTS, "Nonsense")
    check(False, "an unknown knot style is refused")
except interp.InterpolationError as e:
    check("Nonsense" in str(e) and "Uniform" in str(e),
          "an unknown knot style is refused BY NAME, listing the reference application's own")
try:
    interp.parameters([(0, 0, 0), (0, 0, 0), (1, 0, 0)], "Chord")
    check(False, "coincident points are refused")
except interp.InterpolationError as e:
    check("coincident" in str(e),
          "COINCIDENT consecutive points are refused by name -- they would make a "
          "zero span and a singular system, not a silently nudged curve")

print("\n-- the knot vector (P&T eq. 9.8) --")
params = interp.parameters([(0, 0, 0), (1, 1, 0), (2, 0, 0), (3, 1, 0), (4, 0, 0)],
                           "Chord")
kn = interp.averaged_knots(params, 3)
check(len(kn) == 5 + 3 + 1, "full clamped vector is n + degree + 1 long")
check(kn[:4] == [0.0] * 4 and kn[-4:] == [1.0] * 4,
      "clamped: degree+1 zeros at the start and ones at the end")
check(all(kn[i] <= kn[i + 1] + 1e-15 for i in range(len(kn) - 1)),
      "and it is non-decreasing")

print("\n-- INTERPOLATION: the curve passes THROUGH the points --")


def through(points, degree=3, style=interp.DEFAULT_KNOT_STYLE):
    """Worst distance from each data point to the curve evaluated at its parameter."""
    ctrl, knots, p = interp.interpolate_points(points, degree, style)
    us = interp.parameters(points, style)
    worst = 0.0
    for k, target in enumerate(points):
        uu = us[k]
        span = interp.find_span(len(ctrl), p, uu, knots)
        vals = interp.basis_values(span, uu, p, knots)
        pt = [0.0, 0.0, 0.0]
        for j, w in enumerate(vals):
            cp = ctrl[span - p + j]
            for d in range(3):
                pt[d] += w * cp[d]
        worst = max(worst, math.dist(pt, target))
    return worst, ctrl, p


WAVE = [(0, 0, 0), (1, 2, 0), (3, -1, 0), (5, 3, 0), (8, 0, 0), (10, 2, 1)]
for style in interp.KNOT_STYLES:
    w, ctrl, p = through(WAVE, 3, style)
    check(w < 1e-9,
          f"{style}: a degree-3 curve through 6 points hits every one "
          f"(worst error {w:.2e})")
    check(p == 3 and len(ctrl) == 6,
          f"  {style}: degree 3 with one control point per data point")

w, ctrl, p = through([(0, 0, 0), (1, 1, 0), (2, 0, 0), (3, 1, 0)], 3)
check(w < 1e-9, "the minimum degree-3 case (4 points) interpolates exactly")

# 3-D, non-planar, unevenly spaced -- the realistic case
HELIX = [(math.cos(t), math.sin(t), t * 0.4)
         for t in (0.0, 0.3, 0.9, 1.1, 2.0, 2.4, 3.6, 5.0)]
w, _, _ = through(HELIX, 3, "Chord")
check(w < 1e-9, f"an 8-point non-planar helix sample interpolates exactly ({w:.2e})")

print("\n-- degree rules (MEASURED from the reference API docs) --")
try:
    interp.interpolate_points(WAVE, 2)
    check(False, "an even degree is refused")
except interp.InterpolationError as e:
    check("ODD" in str(e).upper(),
          "an EVEN degree is refused BY NAME -- the reference API documents "
          "'Degree must be odd', so quietly rounding it would hide a misconception")
w, ctrl, p = through([(0, 0, 0), (2, 1, 0)], 3)
check(p == 1 and w < 1e-12,
      "two points clamp down to degree 1 -- a line through 2 points IS the exact "
      "answer, not a fallback")
w, ctrl, p = through([(0, 0, 0), (1, 1, 0), (3, 0, 0)], 3)
check(p == 2 and w < 1e-12,
      "three points clamp to degree 2 -- a SMOOTH curve through three points. Clamping "
      "to an odd degree would give degree 1, i.e. a POLYLINE, which is exactly the "
      "'curves made of line segments' the project rule forbids")
w5, _, p5 = through(WAVE, 5)
check(p5 == 5 and w5 < 1e-9, f"degree 5 also interpolates exactly ({w5:.2e})")
try:
    interp.interpolate_points([(0, 0, 0)], 3)
    check(False, "one point is refused")
except interp.InterpolationError as e:
    check("at least 2" in str(e), "a single point is refused by name")

print("\n-- SKINNING: the surface passes through every section --")
# three circles stacked and shrinking: the control nets are compatible by construction
def circle_net(radius, z, n=9):
    return [(radius * math.cos(2 * math.pi * i / (n - 1)),
             radius * math.sin(2 * math.pi * i / (n - 1)), z) for i in range(n)]


SECTIONS = [circle_net(5, 0), circle_net(4, 3), circle_net(6, 7), circle_net(5, 11)]
net, vknots, q = interp.skin_control_net(SECTIONS, 3, "Chord")
check(len(net) == 9 and len(net[0]) == 4,
      "the net is (U control points) x (sections)")
check(q == 3, "V degree 3 -- SMOOTH along the sweep, which is the whole point")


def surface_point(net, vknots, q, i, v):
    """Evaluate control-column `i` of the net at V parameter `v`."""
    column = net[i]
    span = interp.find_span(len(column), q, v, vknots)
    vals = interp.basis_values(span, v, q, vknots)
    pt = [0.0, 0.0, 0.0]
    for j, w in enumerate(vals):
        cp = column[span - q + j]
        for d in range(3):
            pt[d] += w * cp[d]
    return pt


# recover the V parameters the skin used, then check each section is reproduced exactly
acc = [0.0] * len(SECTIONS)
used = 0
for i in range(len(SECTIONS[0])):
    col = [s[i] for s in SECTIONS]
    try:
        ps = interp.parameters(col, "Chord")
    except interp.InterpolationError:
        continue
    used += 1
    for j, pv in enumerate(ps):
        acc[j] += pv
vp = [a / used for a in acc]
vp[0], vp[-1] = 0.0, 1.0
worst = 0.0
for j, section in enumerate(SECTIONS):
    for i in range(len(section)):
        got = surface_point(net, vknots, q, i, vp[j])
        worst = max(worst, math.dist(got, section[i]))
check(worst < 1e-9,
      f"every section is reproduced EXACTLY by the skinned surface ({worst:.2e}) -- "
      f"so a sweep passes through its stations instead of chording between them")

try:
    interp.skin_control_net([circle_net(5, 0)], 3)
    check(False, "one section is refused")
except interp.InterpolationError as e:
    check("at least 2" in str(e), "a single section is refused by name")
try:
    interp.skin_control_net([circle_net(5, 0), circle_net(5, 2, n=7)], 3)
    check(False, "incompatible sections are refused")
except interp.InterpolationError as e:
    check("compatible" in str(e),
          "sections with different control-point counts are refused BY NAME rather "
          "than skinned into nonsense")
net2, vk2, q2 = interp.skin_control_net(SECTIONS[:2], 3, "Chord")
check(q2 == 1, "two sections clamp to degree 1 -- a ruled surface, which is correct "
               "and is what Loft's Straight style means")
net3, vk3, q3 = interp.skin_control_net(SECTIONS[:3], 3, "Chord")
check(q3 == 2,
      "THREE sections skin to degree 2 -- smooth. An odd-degree clamp would give "
      "degree 1 here, turning a three-section loft into flat strips")

print("\n-- the linear algebra --")
A = [[4.0, 1.0, 0.0], [1.0, 4.0, 1.0], [0.0, 1.0, 4.0]]
lu, perm = interp.lu_factor(A)
sol = interp.lu_solve(lu, perm, [(6.0,), (12.0,), (18.0,)])
# verify by substitution rather than by a hard-coded answer
res = 0.0
for i in range(3):
    acc2 = sum(A[i][j] * sol[j][0] for j in range(3))
    res = max(res, abs(acc2 - [6.0, 12.0, 18.0][i]))
check(res < 1e-12, f"LU solves a 3x3 system (residual {res:.2e})")
multi = interp.lu_solve(lu, perm, [(6.0, 1.0), (12.0, 2.0), (18.0, 3.0)])
check(abs(multi[0][0] - sol[0][0]) < 1e-12,
      "and one factorisation serves several right-hand sides -- which is what makes "
      "skinning O(n^3) once rather than O(n^4)")
try:
    interp.lu_factor([[1.0, 2.0], [2.0, 4.0]])
    check(False, "a singular system is refused")
except interp.InterpolationError as e:
    check("singular" in str(e), "a SINGULAR system is refused by name, not solved to inf")

print("\n-- the rhino3dm builder --")
try:
    import rhino3dm  # noqa: F401
    crv = interp.interpolate_curve(WAVE, 3, "Chord")
    check(crv is not None and crv.IsValid, "interpolate_curve returns a valid NurbsCurve")
    check(crv.Degree == 3, "of degree 3 -- a SMOOTH curve, not line segments")
    check(len(crv.Points) == len(WAVE),
          "with one control point per data point")
    worst = 0.0
    us = interp.parameters(WAVE, "Chord")
    dom = crv.Domain
    for k, target in enumerate(WAVE):
        # the built curve is parameterised over its own domain; map [0,1] onto it
        t = dom.T0 + (dom.T1 - dom.T0) * us[k]
        p = crv.PointAt(t)
        worst = max(worst, math.dist((p.X, p.Y, p.Z), target))
    check(worst < 1e-9,
          f"and the BUILT curve really does pass through every picked point "
          f"({worst:.2e}) -- this is the check the old InterpCrv would have failed")
    # the regression, stated as a comparison against what InterpCrv used to do
    cpc = rhino3dm.NurbsCurve.CreateControlPointCurve(
        [rhino3dm.Point3d(*p) for p in WAVE], 3)
    cdom = cpc.Domain
    cworst = 0.0
    for k, target in enumerate(WAVE):
        t = cdom.T0 + (cdom.T1 - cdom.T0) * us[k]
        p = cpc.PointAt(t)
        cworst = max(cworst, math.dist((p.X, p.Y, p.Z), target))
    check(cworst > 0.1,
          f"while CreateControlPointCurve -- what InterpCrv called -- misses them by "
          f"{cworst:.4f}, which is the bug this module fixes")
except ImportError:
    print("  .. rhino3dm unavailable; builder checks skipped")

# --------------------------------------------------------------------------
print("\n-- THE SWEEP REGRESSION: smooth along a CURVED rail --")
# This is the check whose ABSENCE let the bug live. The representation audit swept along a
# STRAIGHT rail, where degree 1 in V is exact, so it could not fail. A curved rail is the
# only input that can tell a skinned surface from a chorded one.
try:
    import rhino3dm as r3

    bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
    sweep = _load("narwhal3d.sweep", "narwhal3d/sweep.py")

    R = 10.0
    r_sec = 2.0
    arc = bridge.make_arc(
        (R, 0, 0),
        (R * math.cos(math.pi / 4), R * math.sin(math.pi / 4), 0),
        (0, R, 0))
    circle = r3.NurbsCurve.CreateFromCircle(r3.Circle(r_sec))

    swept = sweep.sweep1(arc, circle)
    ns = swept.Faces[0].UnderlyingSurface().ToNurbsSurface()
    check(ns.Degree(1) >= 2,
          f"sweep1 along an ARC is degree {ns.Degree(1)} in V -- SMOOTH. It was degree 1 "
          f"(a fan of flat strips), which the project rules rule 0 forbids")
    check(ns.Degree(0) == 2,
          "and still degree 2 in U, so the circular section stays an exact circle")

    # A circle swept along a circular arc is a TORUS, so every point satisfies
    # (sqrt(x^2+y^2) - R)^2 + z^2 == r^2. Measuring the surface against that equation is
    # independent of how we built it.
    #
    # This must use `pipe`, NOT `sweep1(arc, circle)`: `sweep1` transports whatever
    # section it is given from the rail-START frame, and `r3.Circle(2.0)` is centred on
    # the ORIGIN, not on the rail start at (R,0,0) -- so that tube is offset from the rail
    # and is not a torus at all. `pipe` builds the section centred ON the rail
    # (`_perp_circle`), which is the torus case. The first version of this check got that
    # wrong and measured a residual of 60.
    # `pipe` no longer skins at all -- it builds the surface in OCCT, and a circular
    # profile along a circular rail is a TORUS by construction. So the right assertion is no
    # longer "smooth and close": it is EXACT, and it is read off the MASTER. Reading the
    # returned object would measure the display triangulation (residual 5.9e-02, degree 1 --
    # correct for a render mesh, and nothing to do with the geometry).
    # Use bridge's OWN accessor rather than loading the kernel again: it handles every load
    # path, and a second kernel module would mean a second master registry, so `master_of`
    # would quietly miss.
    kernel_mod = bridge._kernel()
    torus = sweep.pipe(arc, r_sec, cap="None")
    master = kernel_mod.master_of(torus)
    check(master is not None,
          f"a pipe along an arc is built analytically ({bridge.last_occt_decline()})")
    if master is not None:
        occt_mod = bridge._occt()
        types_ = occt_mod.face_types(master)
        check(types_ == {"TORUS": 1},
              f"and it is exactly ONE TORUS face {types_} -- not a skinned approximation of "
              f"one. The loft route gave a degree-3 B-spline that missed the ideal by 0.0114 "
              f"units mid-strip")
        # the torus's OWN radii, which is what analytic geometry means
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.TopAbs import TopAbs_ShapeEnum
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        exp = TopExp_Explorer(master, TopAbs_ShapeEnum.TopAbs_FACE)
        tor = BRepAdaptor_Surface(TopoDS.Face(exp.Current())).Torus()
        check(abs(tor.MajorRadius() - R) < 1e-12
              and abs(tor.MinorRadius() - r_sec) < 1e-12,
              f"with the EXACT radii it was asked for: major {tor.MajorRadius()}, minor "
              f"{tor.MinorRadius()} (wanted {R}, {r_sec}) -- exact to 1e-12, where the "
              f"skinned surface could only ever approximate")

    # a STRAIGHT rail must stay exact -- the fix must not make the easy case worse
    straight = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 10)).ToNurbsCurve()
    sw2 = sweep.sweep1(straight, circle)
    sns = sw2.Faces[0].UnderlyingSurface().ToNurbsSurface()
    worst_cyl = 0.0
    sdu, sdv = sns.Domain(0), sns.Domain(1)
    for a in range(17):
        for b in range(17):
            u = sdu.T0 + (sdu.T1 - sdu.T0) * a / 16.0
            v = sdv.T0 + (sdv.T1 - sdv.T0) * b / 16.0
            p = sns.PointAt(u, v)
            worst_cyl = max(worst_cyl, abs(math.hypot(p.X, p.Y) - r_sec))
    check(worst_cyl < 1e-9,
          f"a sweep along a STRAIGHT rail is still EXACTLY a cylinder ({worst_cyl:.2e}) "
          f"-- the fix did not trade the easy case away")

    # the Smooth loft style passes through its sections; Straight/Loose still work
    c1 = r3.NurbsCurve.CreateFromCircle(r3.Circle(5.0))
    c2 = r3.NurbsCurve.CreateFromCircle(r3.Circle(3.0))
    c2.Translate(r3.Vector3d(0, 0, 4))
    c3 = r3.NurbsCurve.CreateFromCircle(r3.Circle(6.0))
    c3.Translate(r3.Vector3d(0, 0, 9))
    smooth = bridge.loft_brep([c1, c2, c3], style="Smooth")
    lns = smooth.Faces[0].UnderlyingSurface().ToNurbsSurface()
    check(lns.Degree(1) >= 2,
          f"loft_brep style='Smooth' is degree {lns.Degree(1)} in V")
    # it must CONTAIN the middle section: radius 3 somewhere in the interior
    ldv = lns.Domain(1)
    radii = []
    for b in range(201):
        v = ldv.T0 + (ldv.T1 - ldv.T0) * b / 200.0
        p = lns.PointAt(lns.Domain(0).T0, v)
        radii.append(math.hypot(p.X, p.Y))
    check(min(radii) < 3.0 + 1e-6,
          f"and it REACHES the middle section's radius 3 (min seen {min(radii):.6f}) -- "
          f"an interpolating loft passes through its sections, unlike 'Loose'")
    try:
        bridge.loft_brep([c1, c2], style="Nonsense")
        check(False, "an unknown loft style is refused")
    except ValueError as e:
        check("Nonsense" in str(e) and all(s in str(e) for s in bridge.LOFT_STYLES),
              "an unknown loft style is refused BY NAME, listing every buildable style")
except ImportError as e:
    print(f"  .. sweep regression skipped ({e})")

print()
print("=" * 74)
print(f"{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("INTERPOLATION + SMOOTH SWEEP OK")
