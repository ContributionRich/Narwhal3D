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

print("\n-- INTERPOLATION: the curve passes THROUGH the points --")

WAVE = [(0, 0, 0), (1, 2, 0), (3, -1, 0), (5, 3, 0), (8, 0, 0), (10, 2, 1)]

print("\n-- degree rules (MEASURED from the reference API docs) --")
try:
    interp.interpolate_points(WAVE, 2)
    check(False, "an even degree is refused")
except interp.InterpolationError as e:
    check("ODD" in str(e).upper(),
          "an EVEN degree is refused BY NAME -- the reference API documents "
          "'Degree must be odd', so quietly rounding it would hide a misconception")
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

print("\n-- the linear algebra --")
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
print("\n-- CLOSED (periodic) interpolation (#43) --")
# Every production size uses Rust; exact original answers are frozen in Rust golden tests.
RING = [(0, 0, 0), (4, 1, 0), (7, 5, 1), (3, 9, 0), (-2, 6, 2), (-3, 2, 0)]
try:
    interp.closed_parameters(RING + [RING[0]])
    check(False, "a repeated first point is refused")
except interp.InterpolationError as e:
    check("coincident" in str(e), f"a repeated first point is refused by name ({e})")
try:
    interp.interpolate_points_periodic(RING[:2])
    check(False, "two points cannot close")
except interp.InterpolationError as e:
    check("at least 3" in str(e), f"two points are refused by name ({e})")

try:
    import rhino3dm

    for style in interp.KNOT_STYLES:
        crv = interp.interpolate_closed_curve(RING, style)
        dom = crv.Domain
        us = interp.closed_parameters(RING, style)
        worst = max(math.dist(tuple(getattr(crv.PointAt(dom.T0 + (dom.T1 - dom.T0) * u), a)
                                    for a in "XYZ"), q) for u, q in zip(us, RING))
        check(crv.IsClosed and crv.IsPeriodic and crv.Degree == 3,
              f"{style}: a closed, periodic cubic")
        check(worst < 1e-9, f"{style}: it passes through all 6 irregular points ({worst:.2e})")
        check(crv.PointAtStart.DistanceTo(crv.PointAtEnd) < 1e-12,
              f"{style}: PointAtStart equals PointAtEnd")
        d0, d1 = crv.DerivativeAt(dom.T0, 2), crv.DerivativeAt(dom.T1, 2)
        for order in (1, 2):
            a, b = d0[order], d1[order]
            rel = a.DistanceTo(b) / max(1.0, math.sqrt(a.X ** 2 + a.Y ** 2 + a.Z ** 2))
            check(rel < 1e-7, f"{style}: derivative {order} matches across the seam ({rel:.1e})")
    # what Close used to build: the periodic CONTROL-POINT curve, which misses the picks
    cpc = rhino3dm.NurbsCurve.Create(True, 3, [rhino3dm.Point3d(*p) for p in RING])
    miss = max(min(cpc.PointAt(cpc.Domain.T0 + (cpc.Domain.T1 - cpc.Domain.T0) * i / 4000)
                   .DistanceTo(rhino3dm.Point3d(*q)) for i in range(4001)) for q in RING)
    check(miss > 0.1, f"while the old periodic control-point curve misses them by {miss:.3f}")
except ImportError:
    print("  .. rhino3dm unavailable; closed builder checks skipped")

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

    # `sweep1` now builds in OCCT like `pipe` below: the returned Brep is the display
    # triangulation (degree 1, hundreds of flat faces -- correct for a render mesh), and
    # the geometry is the MASTER. So the smoothness is read off the master.
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_CurveType, GeomAbs_SurfaceType
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    def master_faces(shape):
        out = []
        exp = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
        while exp.More():
            out.append(BRepAdaptor_Surface(TopoDS.Face(exp.Current())))
            exp.Next()
        return out

    swept = sweep.sweep1(arc, circle)
    swept_master = bridge._kernel().master_of(swept)
    faces = master_faces(swept_master) if swept_master is not None else []
    revolved = (len(faces) == 1
                and faces[0].GetType() == GeomAbs_SurfaceType.GeomAbs_SurfaceOfRevolution)
    check(revolved,
          f"sweep1 along an ARC is ONE exact surface of revolution "
          f"({[f.GetType() for f in faces]}) -- SMOOTH along the sweep. It was degree 1 "
          f"(a fan of flat strips), which the project rules rule 0 forbids")
    section = faces[0].BasisCurve() if revolved else None
    check(section is not None and section.GetType() == GeomAbs_CurveType.GeomAbs_Circle
          and abs(section.Circle().Radius() - r_sec) < 1e-12,
          "and the swept section is still an exact circle of the radius it was given")

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
    sw2_master = bridge._kernel().master_of(sw2)
    cyl_faces = master_faces(sw2_master) if sw2_master is not None else []
    cyl_radius = (cyl_faces[0].Cylinder().Radius()
                  if len(cyl_faces) == 1
                  and cyl_faces[0].GetType() == GeomAbs_SurfaceType.GeomAbs_Cylinder
                  else None)
    check(cyl_radius is not None and abs(cyl_radius - r_sec) < 1e-12,
          f"a sweep along a STRAIGHT rail is still EXACTLY a cylinder (radius {cyl_radius}) "
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
