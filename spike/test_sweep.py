"""Sweep1, Pipe, and Sweep2 kernel and Blender display tests."""
import importlib.util
import math
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "narwhal3d"))

import rhino3dm as r3  # noqa: E402

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, relpath):
    path = os.path.join(ROOT, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


kernel = _load("narwhal3d.kernel", "narwhal3d/kernel/__init__.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
sweep = _load("narwhal3d.sweep", "narwhal3d/sweep.py")
nearest = _load("narwhal3d.nearest", "narwhal3d/nearest.py")
arclength = _load("narwhal3d.arclength", "narwhal3d/arclength.py")

failures = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        failures.append(msg)


def _vec(p):
    return (p.X, p.Y, p.Z)


def section_fit(sec, n=48):
    """(unit normal, mean radius, centroid) of a planar closed section curve."""
    d = sec.Domain
    pts = [_vec(sec.PointAt(d.T0 + (d.T1 - d.T0) * i / n)) for i in range(n)]
    c = (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n,
         sum(p[2] for p in pts) / n)
    nx = ny = nz = 0.0
    for i in range(n):
        a = sweep._sub(pts[i], c)
        b = sweep._sub(pts[(i + 1) % n], c)
        cr = sweep._cross(a, b)
        nx += cr[0]
        ny += cr[1]
        nz += cr[2]
    normal = sweep._unit((nx, ny, nz)) or (0.0, 0.0, 1.0)
    radius = sum(math.dist(p, c) for p in pts) / n
    return normal, radius, c


def tangent(rail, t):
    return sweep._tan(rail, t)


# ===========================================================================
# 1. a circle swept along a STRAIGHT rail is a cylinder. ---------------------
# ===========================================================================
R_CYL = 3.0
circ_cyl = r3.Circle(r3.Point3d(0, 0, 0), R_CYL).ToNurbsCurve()
rail_line = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 10)).ToNurbsCurve()
cyl = sweep.sweep1(rail_line, circ_cyl, style="Roadlike", up=(0, 1, 0))
check(cyl is not None and len(cyl.Faces) == 1,
      f"cylinder sweep: expected a 1-face Brep, got {cyl}")
check(cyl is not None and cyl.IsSolid is False,
      "an open-ended swept tube must NOT be solid (no caps)")
cyl_secs = sweep.sweep1_sections(rail_line, circ_cyl, style="Roadlike", up=(0, 1, 0))
for i, s in enumerate(cyl_secs):
    n, r, c = section_fit(s)
    check(abs(r - R_CYL) < 1e-9, f"cylinder station {i}: radius {r:.6f} != {R_CYL}")
    check(abs(abs(sweep._dot(n, (0, 0, 1))) - 1.0) < 1e-9,
          f"cylinder station {i}: section not perpendicular to the Z rail")
print(f"cylinder: {len(cyl_secs)} stations, radius {R_CYL}, open tube")

# ===========================================================================
# 2. a circle swept along a CURVED rail keeps a CONSTANT, PERPENDICULAR section
#    -- this is the frame test (a wrong frame makes |n.t| vary / radius wander).
# ===========================================================================
R_TUBE = 1.0
# a curved rail whose START TANGENT is +Z, so the XY circle starts perpendicular.
rail_curve = r3.NurbsCurve.CreateControlPointCurve(
    [r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 5), r3.Point3d(5, 0, 10),
     r3.Point3d(10, 0, 10)], 3)
circ_tube = r3.Circle(r3.Point3d(0, 0, 0), R_TUBE).ToNurbsCurve()
for style, up in (("Freeform", None), ("Roadlike", (0, 1, 0))):
    secs = sweep.sweep1_sections(rail_curve, circ_tube, style=style, up=up)
    params = sweep._station_params(rail_curve, len(secs))
    dots, rads, offs = [], [], []
    for i, s in enumerate(secs):
        n, r, c = section_fit(s)
        t = tangent(rail_curve, params[i])
        dots.append(abs(sweep._dot(n, t)))
        rads.append(r)
        offs.append(math.dist(c, sweep._pt(rail_curve, params[i])))
    check(max(rads) - min(rads) < 1e-9 and abs(rads[0] - R_TUBE) < 1e-9,
          f"{style}: section radius wandered {min(rads):.6f}..{max(rads):.6f}")
    check(min(dots) > 1.0 - 1e-6,
          f"{style}: a section is not perpendicular to the rail (min |n.t| "
          f"{min(dots):.6f}) -- the frame does not reorient")
    check(max(offs) < 1e-9,
          f"{style}: a section centre drifted off the rail ({max(offs):.2e})")
    print(f"curved {style}: {len(secs)} stations, |n.t| in "
          f"[{min(dots):.5f},{max(dots):.5f}], radius {R_TUBE}")

# ===========================================================================
# 3. the surface interpolates the section at the rail's START and END. -------
# ===========================================================================
ends = sweep.sweep1_sections(rail_curve, circ_tube, style="Freeform")
_, r0, c0 = section_fit(ends[0])
_, r1, c1 = section_fit(ends[-1])
check(math.dist(c0, _vec(rail_curve.PointAtStart)) < 1e-9,
      "start section is not centred on the rail start")
check(math.dist(c1, _vec(rail_curve.PointAtEnd)) < 1e-9,
      "end section is not centred on the rail end")
# the first section is the input section, unmoved (identity at station 0).
in_n, in_r, in_c = section_fit(circ_tube)
check(abs(r0 - in_r) < 1e-9 and math.dist(c0, in_c) < 1e-9,
      "start section is not the untouched input section")
print(f"ends: start centre {tuple(round(x,3) for x in c0)}, "
      f"end centre {tuple(round(x,3) for x in c1)}")

# ===========================================================================
# 4. a CLOSED rail produces a geometrically closed surface (first ~ last). ----
# ===========================================================================
rail_closed = r3.Circle(r3.Point3d(0, 0, 0), 8.0).ToNurbsCurve()
sec_closed = r3.Circle(r3.Point3d(8, 0, 0), 1.0).ToNurbsCurve()
csecs = sweep.sweep1_sections(rail_closed, sec_closed, style="Roadlike", up=(0, 0, 1))
seam = math.dist(_vec(csecs[0].PointAtStart), _vec(csecs[-1].PointAtStart))
check(seam < 1e-6,
      f"closed rail: first and last sections do not coincide (seam {seam:.2e}) -- "
      f"the surface is not closed")
cbrep = sweep.sweep1(rail_closed, sec_closed, style="Roadlike", up=(0, 0, 1))
cbrep_master = kernel.master_of(cbrep) if cbrep is not None else None
occt = bridge._occt()
check(cbrep_master is not None and occt is not None and occt.is_valid(cbrep_master),
      f"closed sweep analytic master invalid: {cbrep_master}")
print(f"closed: {len(csecs)} stations, seam gap {seam:.2e}")

# ===========================================================================
# 5. an unbuilt frame style is REFUSED with its own name. --------------------
# ===========================================================================
for style in ("Developable", "AlignWithSurface", "RoadlikeTop"):
    raised = False
    msg = ""
    try:
        sweep.sweep1(rail_line, circ_cyl, style=style)
    except ValueError as e:
        raised = True
        msg = str(e)
    check(raised and f"'{style}'" in msg,
          f"style '{style}' must refuse by name (raised={raised}, msg={msg!r})")

# ===========================================================================
# Pipe: a round tube along a curve (Sweep1 + circular section + cap).
# ===========================================================================
R_PIPE = 2.0
pipe_secs = sweep.pipe_sections(rail_curve, R_PIPE, style="Freeform")
ppar = sweep._station_params(rail_curve, len(pipe_secs))
p_rads, p_dots = [], []
for i, s in enumerate(pipe_secs):
    n, r, c = section_fit(s)
    p_rads.append(r)
    p_dots.append(abs(sweep._dot(n, tangent(rail_curve, ppar[i]))))
check(max(p_rads) - min(p_rads) < 1e-9 and abs(p_rads[0] - R_PIPE) < 1e-9,
      f"pipe radius wandered {min(p_rads):.6f}..{max(p_rads):.6f} (want {R_PIPE})")
check(min(p_dots) > 1.0 - 1e-6,
      f"a pipe section is not perpendicular to the rail (min |n.t| {min(p_dots):.6f})")

# Cap=None leaves the tube OPEN (not solid).
pipe_open = sweep.pipe(rail_line, R_PIPE, cap="None")
check(pipe_open is not None and pipe_open.IsSolid is False,
      "pipe Cap=None must be an open tube (IsSolid False)")
# Check radius on the analytic master when available because a tessellated display
# proxy may sit a chord tolerance inside the true surface.
_m = kernel.master_of(pipe_open)
if _m is not None:
    # Read the CYLINDER'S OWN RADIUS, not a bounding box. `BRepBndLib.Add_s` returns a hull
    # that overshoots -- MEASURED, X came out +/-2.00559 for R=2 even with SetGap(0) -- so a
    # bbox cannot test an exact radius either way. The analytic surface knows its radius, which
    # is the whole point of having analytic geometry.
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    _radii = []
    _exp = TopExp_Explorer(_m, TopAbs_ShapeEnum.TopAbs_FACE)
    while _exp.More():
        _ad = BRepAdaptor_Surface(TopoDS.Face(_exp.Current()))
        if "Cylinder" in str(_ad.GetType()):
            _radii.append(_ad.Cylinder().Radius())
        _exp.Next()
    check(len(_radii) == 1 and abs(_radii[0] - R_PIPE) < 1e-12,
          f"a straight pipe is exactly ONE CYLINDER face of radius {_radii} "
          f"(want [{R_PIPE}]) -- the skinned loft gave a degree-3 B-spline approximating it")
else:
    _bb = pipe_open.GetBoundingBox()
    check(abs(_bb.Max.X - R_PIPE) < 1e-6 and abs(_bb.Min.X + R_PIPE) < 1e-6
          and abs(_bb.Max.Y - R_PIPE) < 1e-6 and abs(_bb.Min.Y + R_PIPE) < 1e-6,
          f"pipe radius bbox wrong: X[{_bb.Min.X},{_bb.Max.X}] Y[{_bb.Min.Y},{_bb.Max.Y}]")

# Cap=Round (hemispherical) and an unknown cap and a bad radius each refuse by name.
for cap in ("Round", "Spherical"):
    raised = False
    msg = ""
    try:
        sweep.pipe(rail_line, R_PIPE, cap=cap)
    except ValueError as e:
        raised = True
        msg = str(e)
    check(raised and ("Round" in msg or "unknown" in msg),
          f"pipe cap '{cap}' must refuse by name (raised={raised}, msg={msg!r})")
raised = False
try:
    sweep.pipe(rail_line, -1.0)
except ValueError:
    raised = True
check(raised, "pipe with a non-positive radius must be refused")
print(f"pipe: {len(pipe_secs)} sections, radius {R_PIPE}, open tube; caps gated")

# ===========================================================================
# Sweep2: a section along two rails.
# ===========================================================================
def _line(p, q):
    return r3.LineCurve(r3.Point3d(*p), r3.Point3d(*q)).ToNurbsCurve()


# 1. a straight section between two PARALLEL straight rails -> a flat ruled surface.
ra = _line((0, 0, 0), (10, 0, 0))
rb = _line((0, 5, 0), (10, 5, 0))
sect = _line((0, 0, 0), (0, 5, 0))
s2 = sweep.sweep2(ra, rb, sect)
check(s2 is not None and len(s2.Faces) == 1 and s2.IsValid,
      f"parallel-rail sweep2 should be one valid face, got {s2}")
_bb2 = s2.GetBoundingBox()
check(abs(_bb2.Max.X - 10) < 1e-6 and abs(_bb2.Max.Y - 5) < 1e-6
      and abs(_bb2.Max.Z) < 1e-6,
      f"flat ruled surface bbox wrong: {(_bb2.Max.X, _bb2.Max.Y, _bb2.Max.Z)}")
print("parallel sweep: 1 flat face, bbox 10x5")

# 2. rails of DIFFERENT LENGTH: the section meets BOTH at every station, and the
#    stations are at equal NORMALISED ARC fractions -- the correspondence check
#    (it fails if the rails are stationed independently by PARAMETER). Arc rails are
#    used so parameter != arc length and the discriminator actually bites.
arc_a = r3.NurbsCurve.CreateFromArc(
    r3.Arc(r3.Point3d(0, 0, 0), r3.Vector3d(0, 1, 0), r3.Point3d(6, 0, 6)))
arc_b = r3.NurbsCurve.CreateFromArc(
    r3.Arc(r3.Point3d(0, 4, 0), r3.Vector3d(0, 1, 0), r3.Point3d(3, 4, 3)))   # shorter
SEC2 = _line((0, 0, 0), (0, 4, 0))
secs2 = sweep.sweep2_sections(arc_a, arc_b, SEC2, stations=7)
La = arclength.length_of(arc_a)
Lb = arclength.length_of(arc_b)
worst_off = worst_frac = 0.0
for i, s in enumerate(secs2):
    f = i / (len(secs2) - 1)
    sa = (s.PointAtStart.X, s.PointAtStart.Y, s.PointAtStart.Z)
    se = (s.PointAtEnd.X, s.PointAtEnd.Y, s.PointAtEnd.Z)
    ra_hit = nearest.closest_point_on_curve(arc_a, sa)
    rb_hit = nearest.closest_point_on_curve(arc_b, se)
    worst_off = max(worst_off, ra_hit.distance, rb_hit.distance)
    frac_a = arclength.length_of(arc_a, arc_a.Domain.T0, ra_hit.t) / La
    worst_frac = max(worst_frac, abs(frac_a - f))
check(worst_off < 1e-6,
      f"a sweep2 section endpoint strays off its rail: worst {worst_off:.2e}")
check(worst_frac < 1e-4,
      f"sweep2 stations are not at equal ARC fractions (worst {worst_frac:.2e}) -- "
      f"the rails were stationed by parameter, not normalised arc length")
print(f"different-length arcs: endpoint off {worst_off:.1e}, arc-frac err {worst_frac:.1e}")

# 3. the surface interpolates the section at the START (sections[0] spans the two
#    rail starts).
first = secs2[0]
check(math.dist((first.PointAtStart.X, first.PointAtStart.Y, first.PointAtStart.Z),
                (arc_a.PointAtStart.X, arc_a.PointAtStart.Y, arc_a.PointAtStart.Z)) < 1e-9
      and math.dist((first.PointAtEnd.X, first.PointAtEnd.Y, first.PointAtEnd.Z),
                    (arc_b.PointAtStart.X, arc_b.PointAtStart.Y, arc_b.PointAtStart.Z)) < 1e-9,
      "sweep2 start section does not span the two rail starts")

# 4. unbuilt options refuse by name.
for kw in ("maintain_height", "closed"):
    raised = False
    msg = ""
    try:
        sweep.sweep2(ra, rb, sect, **{kw: True})
    except ValueError as e:
        raised = True
        msg = str(e)
    check(raised and kw.split("_")[0] in msg,
          f"sweep2 {kw}=True must refuse by name (raised={raised}, msg={msg!r})")

# 5. a section whose endpoints coincide cannot span two rails -> refused by name.
raised = False
try:
    sweep.sweep2(ra, rb, r3.Circle(r3.Point3d(0, 0, 0), 1.0).ToNurbsCurve())
except ValueError as e:
    raised = "endpoints coincide" in str(e)
check(raised, "sweep2 with a closed/zero-span section must be refused by name")
print("sweep2: correspondence, interpolation, and options gated")

# ===========================================================================
# 7. the result TESSELLATES and DRAWS (bpy); a Flat cap makes a SOLID. --------
# ===========================================================================
try:
    import bpy  # noqa: F401
    HAVE_BPY = True
except Exception:
    HAVE_BPY = False

if HAVE_BPY:
    bm = bridge.geometry_to_bmesh(cyl)
    nv, nf = len(bm.verts), len(bm.faces)
    check(nv > 0 and nf > 0, f"cylinder sweep drew an empty bmesh: {nv} v, {nf} f")
    finite = all(math.isfinite(v.co.x) and math.isfinite(v.co.y)
                 and math.isfinite(v.co.z) for v in bm.verts)
    check(finite, "swept cylinder bmesh has a non-finite vertex")
    rr = max(math.hypot(v.co.x, v.co.y) for v in bm.verts)
    check(abs(rr - R_CYL) < 0.05,
          f"swept cylinder mesh radius {rr:.4f} != {R_CYL}")
    print(f"sweep draw (bpy): {nv} verts, {nf} faces, radius {rr:.4f}")
    # A flat cap closes both planar ends into a solid.
    pipe_flat = sweep.pipe(rail_line, R_PIPE, cap="Flat")
    check(pipe_flat is not None and pipe_flat.IsSolid is True,
          f"pipe Cap=Flat must be a closed solid (IsSolid), got {pipe_flat}")
    pbm = bridge.geometry_to_bmesh(pipe_flat)
    check(len(pbm.verts) > 0 and len(pbm.faces) > 0,
          "capped pipe drew an empty bmesh")
    print(f"flat cap (bpy): pipe IsSolid={pipe_flat.IsSolid}, "
          f"{len(pbm.faces)} faces")
    # A two-rail swept surface tessellates.
    s2bm = bridge.geometry_to_bmesh(s2)
    check(len(s2bm.verts) > 0 and len(s2bm.faces) > 0,
          "sweep2 surface drew an empty bmesh")
    print(f"sweep2 draw (bpy): {len(s2bm.faces)} faces")
else:
    print("draw skipped (no bpy; Blender covers geometry_to_bmesh)")

# --- report ------------------------------------------------------------------
print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("SWEEP1 + PIPE + SWEEP2 PASSED")
