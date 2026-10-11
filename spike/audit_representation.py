#!/usr/bin/env python3
"""Audit document geometry for analytic representation and manufacturability.

The gates check that curved geometry is not stored as planar facets, solids are
airtight, every public ``bridge.make_*`` producer is covered, swept surfaces are
smooth, analytic masters stay coherent with display proxies, and exact geometry
survives a .3dm round trip. Viewport tessellation is intentionally outside the
representation gate.
"""
import importlib.util
import os
import sys
import types
from collections import Counter

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


bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
kspec = importlib.util.spec_from_file_location(
    "narwhal3d.kernel", os.path.join(ROOT, "narwhal3d", "kernel", "__init__.py"))
kernel = importlib.util.module_from_spec(kspec)
sys.modules["narwhal3d.kernel"] = kernel
kernel.__path__ = [os.path.join(ROOT, "narwhal3d", "kernel")]
kspec.loader.exec_module(kernel)
import rhino3dm as r3  # noqa: E402

ROWS = []
CROWS = []


def classify_curve(curve, should_be_smooth):
    """``(kind, degree, points, verdict)`` for one curve.

    A smooth curve such as a circle must remain exact rather than becoming a polygon.

    The trap this is built to catch: a **degree-1 NurbsCurve with many control points is
    a polyline wearing a curve's name.** Checking `isinstance(..., PolylineCurve)` alone
    would miss it entirely, and so would checking the type name.

    `should_be_smooth` is the EXPECTATION, because a polyline is the correct answer for
    `Polyline`, `Rectangle` and `Polygon` -- those really are line segments, and flagging
    them would be a false alarm that trains people to ignore the gate.
    """
    if curve is None:
        return ("None", 0, 0, "NOTHING BUILT")
    kind = type(curve).__name__
    degree = getattr(curve, "Degree", 0) or 0
    points = 0
    try:
        points = len(curve.Points)
    except Exception:  # noqa: BLE001
        try:
            points = curve.PointCount
        except Exception:  # noqa: BLE001
            points = 0
    # a genuine polyline, by shape rather than by class name
    segmented = (kind == "PolylineCurve" or degree <= 1)
    if not should_be_smooth:
        return (kind, degree, points,
                "polyline (correct -- it IS line segments)" if segmented
                else "smooth (richer than required)")
    if segmented and points > 4:
        return (kind, degree, points,
                f"*** SEGMENTED -- a smooth shape returned as {points} line "
                f"segments (degree {degree}) ***")
    if segmented:
        return (kind, degree, points,
                f"*** SEGMENTED -- degree {degree} ***")
    return (kind, degree, points, "SMOOTH")


def classify(geom):
    """``(kind, faces, planar, solid, verdict)`` for one piece of document geometry.

    The tessellation verdict comes from `kernel.looks_tessellated`, NOT from a second
    rule written here -- one definition, so the audit and the guarantee cannot disagree
    about what a facet soup is.

    `solid` is `Brep.IsSolid` ("airtight"), or None where closure is meaningless (a
    single untrimmed surface, a curve). It is reported SEPARATELY from the verdict
    because the two failures are independent: `make_cone` was analytic AND open.

    A bare `Surface` (what `make_loft` returns) is not a brep and has no `.Faces`; it is
    still analytic geometry, so it is classified as one surface rather than dismissed as
    "n/a". A `Mesh` is called a MESH outright -- that is the thing the rule forbids as
    geometry.
    """
    if geom is None:
        return ("None", 0, 0, None, "NOTHING BUILT")
    kind = type(geom).__name__
    if isinstance(geom, r3.Mesh):
        return (kind, len(geom.Faces), 0, None,
                "*** MESH -- facets AS geometry, not analytic ***")
    faces = getattr(geom, "Faces", None)
    if faces is None:
        # a single Surface (NurbsSurface / RevSurface / SumSurface): analytic, 1 face
        if hasattr(geom, "IsPlanar"):
            try:
                planar = 1 if geom.IsPlanar() else 0
            except Exception:  # noqa: BLE001
                planar = 0
            return (kind, 1, planar, None, "ANALYTIC (single surface)")
        return (kind, 0, 0, None, "n/a (not a brep)")
    # A solid may be a display PROXY over an analytic OCCT master. The GEOMETRY is
    # the master -- that is what gets measured, booleaned again and exported -- so the
    # audit reads the master and never the proxy's triangles. Reading the proxy would make
    # a correct analytic boolean look like a facet soup, which is the mirror image of the
    # mistake this whole file exists to prevent.
    master = kernel.master_of(geom)
    if master is not None:
        try:
            from narwhal3d.kernel import occt_backend as _ob
            n = _ob.face_count(master)
            types = _ob.face_types(master)
            planar_n = types.get("PLANE", 0)
            air = _ob.is_airtight(master)
            return (f"{kind}->OCCT", n, planar_n, air,
                    f"ANALYTIC (master: {types})")
        except Exception as e:  # noqa: BLE001
            return (kind, 0, 0, False, f"MASTER UNREADABLE {e!r}"[:60])
    total, planar = kernel.face_summary(geom)
    solid = bool(getattr(geom, "IsSolid", False))
    if total == 0:
        return (kind, 0, 0, solid, "EMPTY")
    if kernel.looks_tessellated(geom):
        return (kind, total, planar, solid,
                "*** FACETED -- a curved surface was subdivided into planes ***")
    return (kind, total, planar, solid, "ANALYTIC")


def row(label, expect, build, closure="solid"):
    """Audit one solid/surface producer.

    `closure` is the EXPECTED airtightness: ``"solid"`` (must be closed), ``"open"``
    (legitimately an open surface -- say WHY in `EXPECTED_OPEN`), or ``"n/a"``.
    """
    try:
        geom = build()
    except Exception as e:  # noqa: BLE001
        ROWS.append((label, expect, "RAISED", 0, 0, None, closure, repr(e)[:44]))
        return
    kind, total, planar, solid, verdict = classify(geom)
    ROWS.append((label, expect, kind, total, planar, solid, closure, verdict))


CIRCLE = r3.Circle(5.0)
SQUARE = [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0), (0, 0, 0)]

print("=" * 116)
print("REPRESENTATION AUDIT -- a cylinder is 3 analytic faces, not planar strips")
print("=" * 116)

# --- primitives: these SHOULD already be analytic -------------------------
row("make_box", "6 planar", lambda: bridge.make_box((0, 0, 0), (2, 2, 0), 2.0))
# A picture is one planar face, open by definition.
row("make_picture", "1 planar",
    lambda: bridge.make_picture({"image": "/x.png", "origin": [0, 0, 0],
                                 "u": [4, 0, 0], "v": [0, 2, 0]}), closure="open")
# PlanarSrf with a circular hole is one planar face with two loops;
# definition); ExtrudeSrf of it (a CYLINDER hole wall, airtight); EdgeSrf of two arcs + two
# lines (a curved Coons patch -- must stay ONE curved face, never strips).
_G91_SQ = r3.PolylineCurve([r3.Point3d(*p) for p in SQUARE])
row("make_planar_srf (square with a round hole)", "1 planar, holed",
    lambda: bridge.make_planar_srf([_G91_SQ, bridge.make_circle((2, 2, 0), 1.0)])[0],
    closure="open")
row("make_extrude_srf (of that holed square)", "PLANE + CYLINDER",
    lambda: bridge.make_extrude_srf(
        bridge.make_planar_srf([_G91_SQ, bridge.make_circle((2, 2, 0), 1.0)])[0], 3.0))
# A curved surface split by a projected circle must stay one cylinder
# face (gate 1), never strips.
row("split_surface (cylinder wall, projected circle)", "1 curved, holed",
    lambda: max(bridge.split_surface(
        bridge.make_revolve(bridge.make_line((5, 0, 0), (5, 0, 10)), (0, 0, 0), (0, 0, 1)),
        [bridge.make_circle((0, 0, 5), 2.0, type("F", (), {"xaxis": (1, 0, 0),
                                                           "yaxis": (0, 0, 1)})())])[0],
        key=lambda g: len(g.Faces[0].Loops)), closure="open")
# Shell yields an airtight 11-plane box; solid surface offset yields
# (a tube {CYLINDER: 2, PLANE: 2} -- two exact cylinders, never strips).
row("shell_occt (10-cube, top removed)", "11 planar, closed",
    lambda: bridge.shell_occt(bridge.make_box((0, 0, 0), (10, 10, 0), 10), [(5, 5, 10)], 1.0))
row("offset_surface_solid (open cylinder wall)", "2 curved + 2 planar",
    lambda: bridge.offset_surface_solid(bridge.make_revolve(
        bridge.make_line((5, 0, 0), (5, 0, 10)), (0, 0, 0), (0, 0, 1)), 1.0))
# A cylinder scaled (2,1,1) is an elliptic cylinder with one smooth side.
row("transform_geometry (cylinder scaled 2,1,1)", "1 curved + 2 planar",
    lambda: bridge.transform_geometry(
        bridge.make_cylinder((0, 0, 0), 3.0, 5.0),
        r3.Transform.Scale(r3.Plane(r3.Point3d(0, 0, 0), r3.Vector3d(1, 0, 0),
                                    r3.Vector3d(0, 1, 0)), 2, 1, 1)))
# A sub-object face extracted from a solid is exact, never its pick triangles.
row("extract_subobject (cylinder side face)", "1 curved",
    lambda: next(f for f in (bridge.extract_subobject(bridge.make_cylinder((0, 0, 0), 3.0,
                                                                        5.0), "face", i)
                             for i in (1, 2, 3)) if not f.Faces[0].IsPlanar()),
    closure="open")
row("make_edge_srf (two arcs + two lines)", "1 curved",
    lambda: bridge.make_edge_srf([
        bridge.make_arc((5, 0, 0), (3.5355339, 3.5355339, 0), (0, 5, 0)),
        bridge.make_arc((5, 0, 9), (3.5355339, 3.5355339, 9), (0, 5, 9))]), closure="open")


# Exact planar Trim/Split: the holed piece is a master + proxy (the proxy is
# triangles, which is fine -- the gate reads the master); the disk is a native trimmed plane.
def _planar_pieces():
    pic = bridge.make_picture({"image": "/x.png", "origin": [0, 0, 0],
                               "u": [10, 0, 0], "v": [0, 10, 0]})
    return bridge.split_planar_surface(pic, [bridge.make_circle((5, 5, 0), 3.0)])


row("split_planar_surface (holed piece)", "1 planar, master",
    lambda: max(_planar_pieces(), key=lambda g: len(g.Faces)), closure="open")
row("split_planar_surface (disk)", "1 planar",
    lambda: min(_planar_pieces(), key=lambda g: len(g.Faces)), closure="open")
row("make_cylinder", "3 faces, 1 curved",
    lambda: bridge.make_cylinder((0, 0, 0), 2.0, 5.0))
row("make_sphere", "1 curved", lambda: bridge.make_sphere((0, 0, 0), 3.0))
row("make_cone", "2 faces, 1 curved", lambda: bridge.make_cone((0, 0, 0), 2.0, 5.0))
# Solid=False explicitly requests an open cone. The remaining primitives are exact,
# closed solids.
row("make_torus", "1 curved (TORUS)", lambda: bridge.make_torus((0, 0, 0), 5.0, 1.5))
row("make_ellipsoid", "1 curved (BSPLINE)",
    lambda: bridge.make_ellipsoid((0, 0, 0), 3.0, 2.0, 1.0))
row("make_truncated_cone", "3 faces, 1 curved",
    lambda: bridge.make_truncated_cone((0, 0, 0), 3.0, 1.0, 4.0))
row("make_tube", "4 faces, 2 curved", lambda: bridge.make_tube((0, 0, 0), 3.0, 1.0, 4.0))
row("make_pyramid", "6 planes", lambda: bridge.make_pyramid((0, 0, 0), 2.0, 5, 3.0))
row("make_cone(solid=False)", "1 curved, open",
    lambda: bridge.make_cone((0, 0, 0), 2.0, 5.0, solid=False), closure="open")

# --- constructed solids ---------------------------------------------------
row("make_extrusion(circle)", "3 faces, 1 curved",
    lambda: bridge.make_extrusion(r3.NurbsCurve.CreateFromCircle(CIRCLE), 4.0))
row("make_extrusion(square)", "6 planar",
    lambda: bridge.make_extrusion(
        r3.PolylineCurve([r3.Point3d(*p) for p in SQUARE]), 4.0))
row("make_revolve(line)", "curved, open",
    lambda: bridge.make_revolve(
        r3.LineCurve(r3.Point3d(2, 0, 0), r3.Point3d(2, 0, 5)),
        (0, 0, 0), (0, 0, 1)), closure="open")

# --- lofts and sweeps: NURBS all the way through, or sampled? ------------
_C1 = r3.NurbsCurve.CreateFromCircle(r3.Circle(5.0))
_C2 = r3.NurbsCurve.CreateFromCircle(r3.Circle(3.0))
_C2.Translate(r3.Vector3d(0, 0, 6))
row("make_loft(2 circles)", "1 ruled surface",
    lambda: bridge.make_loft(_C1, _C2), closure="n/a")
row("loft_brep([c1,c2], Straight)", "curved",
    lambda: bridge.loft_brep([_C1, _C2], style="Straight"), closure="open")
row("loft_brep([c1,c2], Loose)", "curved",
    lambda: bridge.loft_brep([_C1, _C2], style="Loose"), closure="open")

_RAIL = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 10)).ToNurbsCurve()
try:
    sweep = _load("narwhal3d.sweep", "narwhal3d/sweep.py")
    row("sweep.sweep1(line, circle)", "curved",
        lambda: sweep.sweep1(_RAIL, _C1), closure="open")
    row("sweep.pipe(cap=None)", "curved, open",
        lambda: sweep.pipe(_RAIL, 2.0, cap="None"), closure="open")
    row("sweep.pipe(cap=Flat)", "curved, CLOSED",
        lambda: sweep.pipe(_RAIL, 2.0, cap="Flat"), closure="solid")
    # Sweep2's section must SPAN the two rails -- its endpoints land on each rail's
    # start. An arc from (0,0,0) to (8,0,0) does; a circle does not, and the earlier
    # version of this row was refused by name for exactly that reason (a correct
    # refusal, but it audited nothing).
    row("sweep.sweep2(2 rails, arc)", "curved",
        lambda: sweep.sweep2(
            r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(0, 0, 10)).ToNurbsCurve(),
            r3.LineCurve(r3.Point3d(8, 0, 0), r3.Point3d(8, 0, 10)).ToNurbsCurve(),
            bridge.make_arc((0, 0, 0), (4, 2, 0), (8, 0, 0))), closure="open")
except Exception as e:  # noqa: BLE001
    ROWS.append(("sweep.* (module)", "-", "LOAD FAILED", 0, 0, None, "n/a", repr(e)[:44]))

# --- cap / trim / fillet / offset on SURFACES ----------------------------
# An OPEN cylinder wall is the natural input for Cap: 1 curved face, two planar holes.
# Cap a produced open pipe, which carries the recipe needed for exact reconstruction.
_PIPE_RAIL = bridge.make_arc((6, 0, 0), (0, 6, 0), (-6, 0, 0))
row("cap_planar_holes(open pipe)", "curved + 2 PLANE lids, CLOSED",
    lambda: bridge.cap_planar_holes(sweep.pipe(_PIPE_RAIL, 1.5, cap="None")).brep)
# ...and the no-recipe case, named with its reason. This is a REAL limitation, not an oversight:
# a bare rhino3dm brep cannot be handed to the kernel at all.
row("cap_planar_holes(no recipe)", "mesh: nothing to convert",
    lambda: bridge.cap_planar_holes(
        r3.Cylinder(r3.Circle(2.0), 5.0).ToBrep(False, False)).brep)

try:
    offsetm = _load("narwhal3d.offset", "narwhal3d/offset.py")
    _so = offsetm.surface_offset(bridge.make_loft(_C1, _C2), 1.0)
    row("offset.surface_offset(ruled)", "analytic surface",
        lambda: getattr(_so, "curve", None), closure="n/a")
    print(f"    [surface_offset reports exact={getattr(_so, 'exact', '?')} "
          f"max_deviation={getattr(_so, 'max_deviation', '?')}]")
except Exception as e:  # noqa: BLE001
    ROWS.append(("offset.surface_offset", "-", "LOAD FAILED", 0, 0, None, "n/a",
                 repr(e)[:44]))

try:
    edgefillet = _load("narwhal3d.edgefillet", "narwhal3d/edgefillet.py")
    row("edgefillet.fillet_edges(box)", "26 analytic, airtight",
        lambda: edgefillet.fillet_edges(
            bridge.make_box((0, 0, 0), (4, 4, 0), 4.0), 0.5))
    # One named edge is resolved by the application's own picker. This row
    # exists because the all-edges rows cannot catch it: naming edges takes a different path
    # (pick -> indices -> bevel_edges) and used to fall back to the mesh route entirely.
    row("edgefillet.fillet_edges(box, 1 edge)", "7 faces, 1 CYLINDER",
        lambda: edgefillet.fillet_edges(
            bridge.make_box((0, 0, 0), (4, 4, 0), 4.0), 0.5,
            edge_points=[(2.0, 0.0, 4.0)]))
    row("edgefillet.chamfer_edges(box)", "26 planar, airtight",
        lambda: edgefillet.chamfer_edges(
            bridge.make_box((0, 0, 0), (4, 4, 0), 4.0), 0.5))
except Exception as e:  # noqa: BLE001
    ROWS.append(("edgefillet.*", "-", "LOAD FAILED", 0, 0, None, "n/a", repr(e)[:44]))

# --- boolean representation ------------------------------------------------
_a = bridge.make_box((0, 0, 0), (4, 4, 0), 4.0)
_b = bridge.make_cylinder((2, 2, 0), 1.5, 6.0)
for op in ("UNION", "DIFFERENCE", "INTERSECT"):
    row(f"boolean_brep({op}) box+cyl", "3-10 faces, 1 curved",
        lambda op=op: bridge.boolean_brep(_a.Duplicate(), _b.Duplicate(), op))

# Extrusions take a distinct operand-conversion path, so audit them independently.
_EC1 = r3.NurbsCurve.CreateFromCircle(r3.Circle(r3.Point3d(0, 0, 0), 3.0))
_EC2 = r3.NurbsCurve.CreateFromCircle(r3.Circle(r3.Point3d(2, 0, 0), 3.0))
_SQ = r3.PolylineCurve([r3.Point3d(*q) for q in
                        [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0), (0, 0, 0)]])
row("boolean(UNION) extrusion+extrusion", "analytic, 1+ curved",
    lambda: bridge.boolean_brep(bridge.make_extrusion(_EC1, 5.0),
                                bridge.make_extrusion(_EC2, 5.0), "UNION"))
row("boolean(DIFFERENCE) sq-extr - cyl", "analytic",
    lambda: bridge.boolean_brep(
        bridge.make_extrusion(_SQ.ToNurbsCurve(), 5.0),
        bridge.make_cylinder((2, 2, 0), 1.0, 9.0), "DIFFERENCE"))

print()
hdr = (f"{'producer':<32}{'should be':<26}{'type':<12}{'faces':>6}{'planar':>7}"
       f"{'solid':>7}  verdict")
print(hdr)
print("-" * 116)
bad = []
notairtight = []
for label, expect, kind, total, planar, solid, closure, verdict in ROWS:
    st = "-" if solid is None else ("yes" if solid else "NO")
    print(f"{label:<32}{expect:<26}{str(kind):<12}{total:>6}{planar:>7}{st:>7}  {verdict}")
    if "FACETED" in str(verdict) or "MESH --" in str(verdict) or verdict == "NOTHING BUILT":
        bad.append(label)
    if closure == "solid" and solid is False:
        notairtight.append(label)

# ==========================================================================
# CURVES. Smooth inputs must remain exact rather than becoming line segments.
# ==========================================================================


def crow(label, should_be_smooth, build):
    try:
        curve = build()
    except Exception as e:  # noqa: BLE001
        CROWS.append((label, should_be_smooth, "RAISED", 0, 0, repr(e)[:46]))
        return
    CROWS.append((label, should_be_smooth) + classify_curve(curve, should_be_smooth))


CIRC = r3.NurbsCurve.CreateFromCircle(CIRCLE)
LINE = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0))
POLY = r3.PolylineCurve([r3.Point3d(*p) for p in SQUARE])

# --- the exact primitives ------------------------------------------------
crow("make_circle", True, lambda: bridge.make_circle((0, 0, 0), 5.0))
crow("make_arc", True,
     lambda: bridge.make_arc((0, 0, 0), (5, 5, 0), (10, 0, 0)))
crow("make_ellipse", True,
     lambda: bridge.make_ellipse((0, 0, 0), (5, 0, 0), (0, 3, 0)))
# A LINE is degree 1 with two points and that is CORRECT -- the rule is about curved
# shapes. Passing should_be_smooth=False says "a polyline here is right", which is what
# the first version of this audit got wrong, flagging make_line as a violation.
crow("make_line", False, lambda: bridge.make_line((0, 0, 0), (5, 0, 0)))
# these genuinely ARE line segments -- a polyline here is CORRECT
crow("make_polyline(deg=1)", False, lambda: bridge.make_polyline(SQUARE, degree=1))
crow("make_polyline(deg=3) [Curve]", True,
     lambda: bridge.make_polyline([(0, 0, 0), (2, 4, 0), (6, 4, 0), (8, 0, 0)],
                                  degree=3))
crow("make_polyline(closed=True)", True,
     lambda: bridge.make_polyline([(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)],
                                  degree=3, closed=True))
# `InterpCrv` must be smooth and pass through the points; the
# passing-through half is asserted in spike/test_interpolate.py, which is where the
# machinery lives; here it only has to prove it is not a polyline.
crow("make_interpolated_curve", True,
     lambda: bridge.make_interpolated_curve(
         [(0, 0, 0), (1, 2, 0), (3, -1, 0), (5, 3, 0), (8, 0, 0)]))
# Rectangle and Polygon ARE straight-sided in the reference CAD application, so degree 1 is the right answer.
crow("make_rectangle", False, lambda: bridge.make_rectangle((0, 0, 0), (4, 3, 0)))
crow("make_rectangle_3point", False,
     lambda: bridge.make_rectangle_3point((0, 0, 0), (4, 0, 0), (4, 3, 0)))
crow("make_polygon(6)", False, lambda: bridge.make_polygon((0, 0, 0), 5.0, 6))
# Rounded-rectangle sides are lines, but each corner must be a true arc.
# The polycurve as a whole has no single degree, so the corner segment is what is
# classified (segment 0 is the first corner arc).
crow("make_rounded_rectangle (corner)", True,
     lambda: bridge.make_rounded_rectangle(
         [(0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0)], 1.0).SegmentCurve(0))

# --- OPERATIONS on a circle: the real test. Each must stay smooth. -------
# `curve_offset` returns an `offset.OffsetResult` carrying its OWN `exact` flag -- the
# project was already tracking offset honesty, which is the right instinct; this just
# checks the curve as well.
_off = bridge.curve_offset(CIRC, 1.0)
crow("curve_offset(circle).curve", True, lambda: getattr(_off, "curve", None))
print(f"    [curve_offset reports exact={getattr(_off, 'exact', '?')} "
      f"max_deviation={getattr(_off, 'max_deviation', '?')}]")
crow("split_curve(circle)", True,
     lambda: bridge.split_curve(CIRC, [LINE])[0]
     if bridge.split_curve(CIRC, [LINE]) else None)
crow("trim_curve(circle)", True,
     lambda: (bridge.trim_curve(CIRC, [LINE], (5.0, -5.0, 0.0)) or [None])[0])
crow("fillet_curves(two lines).curve", True,
     lambda: bridge.fillet_curves(
         r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)),
         r3.LineCurve(r3.Point3d(10, 0, 0), r3.Point3d(10, 10, 0)), 2.0).curve)
# A CHAMFER is a straight cut across a corner -- a line is the CORRECT answer here,
# unlike a fillet, whose blend is an arc.
crow("chamfer_curves(two lines).curve", False,
     lambda: bridge.chamfer_curves(
         r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)),
         r3.LineCurve(r3.Point3d(10, 0, 0), r3.Point3d(10, 10, 0)), 2.0, 2.0).curve)
crow("join_curves(2 lines)", False,
     lambda: (bridge.join_curves(
         [("a", r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(5, 0, 0)).ToNurbsCurve()),
          ("b", r3.LineCurve(r3.Point3d(5, 0, 0), r3.Point3d(5, 5, 0)).ToNurbsCurve())],
         1e-6) or [(None, None, None)])[0][1])
crow("explode_curve(polyline)[0]", False,
     lambda: (bridge.explode_curve(POLY) or [None])[0])

try:
    project = _load("narwhal3d.project", "narwhal3d/project.py")
    _plane = r3.Plane(r3.Point3d(0, 0, -5), r3.Vector3d(0, 0, 1))
    crow("project.project_curve(circle)", True,
         lambda: project.project_curve(CIRC, _plane))
    crow("project.pull_curve(circle,srf)", True,
         lambda: getattr(
             project.pull_curve(CIRC, bridge.make_loft(_C1, _C2)), "curve", None))
except Exception as e:  # noqa: BLE001
    CROWS.append(("project.*", True, "LOAD FAILED", 0, 0, repr(e)[:40]))
try:
    extend = _load("narwhal3d.extend", "narwhal3d/extend.py")
    crow("extend.extend_curve(arc)", True,
         lambda: extend.extend_curve(
             bridge.make_arc((0, 0, 0), (5, 5, 0), (10, 0, 0)), "End", 2.0))
except Exception as e:  # noqa: BLE001
    CROWS.append(("extend.extend_curve", True, "LOAD FAILED", 0, 0, repr(e)[:40]))
try:
    intersectm = _load("narwhal3d.intersect", "narwhal3d/intersect.py")
    crow("intersect.split_curve_at(circle)", True,
         lambda: (intersectm.split_curve_at(CIRC, [CIRC.Domain.T0 + 0.5]) or [None])[0])
except Exception as e:  # noqa: BLE001
    CROWS.append(("intersect.split_curve_at", True, "LOAD FAILED", 0, 0, repr(e)[:40]))

print()
print("=" * 116)
print("CURVES -- \"real smooth shapes... curves also not being line segments\"")
print("=" * 116)
print(f"{'producer':<34}{'smooth?':<15}{'type':<16}{'deg':>4}{'pts':>5}  verdict")
print("-" * 116)
cbad = []
for label, smooth, kind, degree, points, verdict in CROWS:
    print(f"{label:<34}{('yes' if smooth else 'no (polyline)'):<15}"
          f"{str(kind):<16}{degree:>4}{points:>5}  {verdict}")
    if "SEGMENTED" in str(verdict) or "NOTHING BUILT" in str(verdict):
        cbad.append(label)
bad.extend(cbad)

print()
print("NOTE on display: this audit reads DOCUMENT geometry only. Tessellating for the")
print("VIEWPORT is correct and unavoidable -- the reference CAD application draws a render mesh too. The failure")
print("is a tessellation becoming the GEOMETRY.")
print("Boolean results use an ANALYTIC OCCT master")
print("with a triangulated display proxy over it -- the rows above read the MASTER, which")
print("is what gets measured, booleaned again and exported to STEP.")

# --------------------------------------------------------------------------
# THE GATES. This script exits non-zero when an expected analytic producer facets a
# curve. ``EXPECTED_MESH_DERIVED`` is the explicit exception list.
EXPECTED_MESH_DERIVED = {
    # A bare rhino3dm brep has no analytic master or reconstruction recipe.
    "cap_planar_holes(no recipe)",
}

# Airtightness exceptions for operations that intentionally produce open surfaces.
EXPECTED_OPEN = {
    "make_revolve(line)":
        "the requested revolved surface is intentionally open",
    "make_cone(solid=False)":
        "Solid=False explicitly requests an open cone",
    "make_picture":
        "a picture is a planar surface",
    "split_planar_surface (holed piece)": "a trimmed planar SURFACE -- open is correct",
    "split_planar_surface (disk)": "a trimmed planar SURFACE -- open is correct",
    "make_planar_srf (square with a round hole)":
        "a planar surface is intentionally open",
    "make_edge_srf (two arcs + two lines)":
        "an edge surface is intentionally open",
    "split_surface (cylinder wall, projected circle)":
        "a trimmed curved SURFACE -- open is correct",
}

# Known exceptions for producers that should be closed but are not.
KNOWN_LEAKS = {}

unexpected = [b for b in bad if b not in EXPECTED_MESH_DERIVED]
fixed = [name for name in EXPECTED_MESH_DERIVED
         if name in {r[0] for r in ROWS} and name not in bad]

# ---- COVERAGE: every bridge.make_* producer must be audited here ----------
# New builders must add an audit row so coverage cannot decay silently.
AUDITED = {r[0] for r in ROWS} | {c[0] for c in CROWS}
COVERAGE_EXEMPT = {
    "make_point": "a point has no surface or curve to facet",
}
producers = sorted(n for n in dir(bridge)
                   if n.startswith("make_") and callable(getattr(bridge, n)))
uncovered = [p for p in producers
             if p not in COVERAGE_EXEMPT
             and not any(label.startswith(p) for label in AUDITED)]

print()
print("=" * 116)
print("GATE 1 -- REPRESENTATION: is a curved surface subdivided into planes?")
print("=" * 116)
if unexpected:
    print(f"  FAIL: {len(unexpected)} producer(s) facet a curved surface and are NOT on")
    print("        the documented mesh-route list:")
    for u in unexpected:
        print(f"          *** {u}")
    print("        A curved surface must never be subdivided into planes.")
else:
    print(f"  ok: the only faceted producers are the {len(EXPECTED_MESH_DERIVED)} known")
    print("      mesh-route ones, which declare it. Every other producer is analytic.")
if fixed:
    print(f"  NOTE: {fixed} no longer facet -- remove them from EXPECTED_MESH_DERIVED")
    print("        so the gate starts holding them to the rule.")

print()
print("=" * 116)
print("GATE 2 -- AIRTIGHT: `Brep.IsSolid`, because a leaky solid cannot be made")
print("=" * 116)
leaks = [n for n in notairtight if n not in EXPECTED_OPEN and n not in KNOWN_LEAKS]
if leaks:
    print(f"  FAIL: {len(leaks)} producer(s) claim to build a SOLID but are NOT closed:")
    for n in leaks:
        print(f"          *** {n}")
    print("        An open solid cannot be manufactured. Face topology cannot see this")
    print("        -- make_cone was 1 ANALYTIC face with its base disc missing.")
else:
    print("  ok: every producer expected to be closed measures IsSolid == True,")
    print(f"      apart from the {len(KNOWN_LEAKS)} NAMED KNOWN LEAKS below.")
for name, why in sorted(KNOWN_LEAKS.items()):
    still = name in notairtight
    mark = "KNOWN LEAK" if still else "FIXED -- remove from KNOWN_LEAKS"
    print(f"  {mark}: {name}")
    print(f"      {why}")
for name, why in sorted(EXPECTED_OPEN.items()):
    print(f"  open by design: {name:<26} {why}")

print()
print("=" * 116)
print("GATE 3 -- COVERAGE: is every bridge.make_* producer actually audited?")
print("=" * 116)
if uncovered:
    print(f"  FAIL: {len(uncovered)} producer(s) exist but are NOT audited:")
    for u in uncovered:
        print(f"          *** bridge.{u}")
    print("        Add a row above, or an explicit COVERAGE_EXEMPT reason.")
else:
    print(f"  ok: all {len(producers)} bridge.make_* producers are audited "
          f"({len(COVERAGE_EXEMPT)} exempt by name).")
print(f"       solids/surfaces audited: {len(ROWS)}   curves audited: {len(CROWS)}")

# ==========================================================================
# GATE 4 -- SURFACE SMOOTHNESS along the SWEPT/LOFTED direction.
#
# WHY THIS IS A SEPARATE GATE. Gate 1 asks "is this brep a facet soup?", which is about
# many small faces. A SWEEP is one single face and sails through gate 1 -- while being
# degree 1 in V, i.e. a fan of flat strips. MEASURED, before the fix: a circle swept along
# a quarter-circle rail of radius 10 was degree 2x1 with 18 strips, missing the true torus
# by 0.0114 units mid-strip.
#
# AND THE AUDIT ITSELF HID IT: the sweep rows above use a STRAIGHT rail, where degree 1 in
# V is the EXACT answer. A straight-rail test can never fail. So this gate exists to sweep
# along something CURVED, which is the only input that can tell the two apart.
# ==========================================================================
SROWS = []


def srow(label, build, direction=1):
    """Audit one surface for smoothness in `direction` (0 = U, 1 = V).

    A master-backed surface is read from its MASTER, like `classify` does. Reading the display
    proxy would be measuring the triangulation: every face of it is a bilinear triangle, so a
    perfectly exact TORUS reported as "degree 1 -- FLAT STRIPS". That is precisely the mistake
    this file exists to prevent, pointing the other way.
    """
    try:
        geom = build()
        master = kernel.master_of(geom)
        if master is not None:
            from narwhal3d.kernel import occt_backend as _ob4
            types_ = _ob4.face_types(master)
            curved = sum(n for t, n in types_.items() if t != "PLANE")
            # An analytic curved face is smooth by construction -- there is no "degree along
            # the sweep" to measure, because it is not a sampled net at all.
            SROWS.append((label, 2 if curved else 1, 2 if curved else 1, direction,
                          len(types_), curved, None, f"master {types_}"))
            return
        faces = getattr(geom, "Faces", None)
        srf = (geom.Faces[0].UnderlyingSurface() if faces is not None else geom)
        ns = srf.ToNurbsSurface()
        SROWS.append((label, ns.Degree(0), ns.Degree(1), direction,
                      ns.Points.CountU, ns.Points.CountV, None, ""))
    except Exception as e:  # noqa: BLE001
        SROWS.append((label, 0, 0, direction, 0, 0, repr(e)[:50], ""))


_R = 10.0
_ARC_RAIL = bridge.make_arc(
    (_R, 0, 0),
    (_R * 0.7071067811865476, _R * 0.7071067811865476, 0),
    (0, _R, 0))
_SEC = r3.NurbsCurve.CreateFromCircle(r3.Circle(2.0))
_C3 = r3.NurbsCurve.CreateFromCircle(r3.Circle(6.0))
_C3.Translate(r3.Vector3d(0, 0, 11))

try:
    srow("sweep1(CURVED rail, circle)", lambda: sweep.sweep1(_ARC_RAIL, _SEC))
    srow("pipe(CURVED rail, r=2)", lambda: sweep.pipe(_ARC_RAIL, 2.0, cap="None"))
    srow("sweep2(CURVED rails, arc)",
         lambda: sweep.sweep2(
             _ARC_RAIL,
             bridge.make_arc((_R + 4, 0, 0),
                            ((_R + 4) * 0.7071067811865476,
                             (_R + 4) * 0.7071067811865476, 0),
                            (0, _R + 4, 0)),
             bridge.make_arc((_R, 0, 0), (_R + 2, 0, 2), (_R + 4, 0, 0))))
except NameError:
    SROWS.append(("sweep.* (module not loaded)", 0, 0, 1, 0, 0, "sweep module missing"))
srow("loft_brep([3 circles], Smooth)",
     lambda: bridge.loft_brep([_C1, _C2, _C3], style="Smooth"))
srow("make_revolve(curved profile)",
     lambda: bridge.make_revolve(bridge.make_arc((2, 0, 0), (4, 0, 3), (2, 0, 6)),
                                 (0, 0, 0), (0, 0, 1)), direction=0)

# `Straight` IS degree 1 in V by definition -- a ruled surface is what the user asked for,
# so it is named here rather than flagged. Same for a 2-section loft.
EXPECTED_RULED = {
    "loft_brep([c1,c2], Straight)":
        "a RULED surface is degree 1 in V by definition (Loft's Straight style)",
}

print()
print("=" * 116)
print("GATE 4 -- SURFACE SMOOTHNESS along the swept/lofted direction")
print("=" * 116)
print(f"   {'surface':<36}{'degU':>5}{'degV':>6}{'cvU':>5}{'cvV':>5}   verdict")
print("   " + "-" * 110)
segmented = []
for label, du_, dv_, direction, cu_, cv_, err, extra in SROWS:
    if err:
        print(f"   {label:<36}{'':>5}{'':>6}{'':>5}{'':>5}   RAISED {err}")  # noqa: E501
        continue
    deg = (du_, dv_)[direction]
    axis = "UV"[direction]
    if deg >= 2:
        verdict = (f"ANALYTIC {extra}" if extra
                   else f"SMOOTH in {axis} (degree {deg})")
    else:
        verdict = (f"*** SEGMENTED in {axis} -- degree 1, "
                   f"{(cu_, cv_)[direction] - 1} flat strips ***")
        segmented.append(label)
    print(f"   {label:<36}{du_:>5}{dv_:>6}{cu_:>5}{cv_:>5}   {verdict}")

seg_bad = [s for s in segmented if s not in EXPECTED_RULED]
if seg_bad:
    print()
    print(f"  FAIL: {len(seg_bad)} surface(s) are built from FLAT STRIPS along a curved")
    print("        direction. A curved surface must never be subdivided into planes:")
    for s in seg_bad:
        print(f"          *** {s}")
else:
    print()
    print("  ok: every swept/lofted surface is degree >= 2 along the curved direction,")
    print("      so it is a smooth surface rather than a fan of flat strips.")
for name, why in sorted(EXPECTED_RULED.items()):
    print(f"  ruled by request: {name} -- {why}")
print("=" * 116)

# ==========================================================================
# GATE 5 -- MASTER/PROXY COHERENCE after a transform.
#
# An OCCT master and its display proxy must receive identical transforms. The checks
# below compare their positions and ensure copied geometry has independent state.
# ==========================================================================
COHERENCE = []


def _box_centre(pts):
    return tuple(0.5 * (min(p[i] for p in pts) + max(p[i] for p in pts)) for i in range(3))


def _centre_brep(brep, exact=False):
    """Centre of the document object's box. When the object is the exact polysurface
    (`exact`), its VERTICES are the master's vertices, so their box is compared
    (`GetBoundingBox` on a NURBS brep is its control hull, not its shape). A triangulated
    proxy keeps the old measure, which matched OCCT's triangulation-based `Add_s` box."""
    if exact:
        return _box_centre([(v.Location.X, v.Location.Y, v.Location.Z)
                            for v in brep.Vertices])
    bb = brep.GetBoundingBox()
    return (0.5 * (bb.Min.X + bb.Max.X), 0.5 * (bb.Min.Y + bb.Max.Y),
            0.5 * (bb.Min.Z + bb.Max.Z))


def _centre_shape(shape, exact=False):
    if exact:
        from OCP.BRep import BRep_Tool
        from OCP.TopAbs import TopAbs_VERTEX
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        pts = []
        exp = TopExp_Explorer(shape, TopAbs_VERTEX)
        while exp.More():
            p = BRep_Tool.Pnt_s(TopoDS.Vertex(exp.Current()))
            pts.append((p.X(), p.Y(), p.Z()))
            exp.Next()
        return _box_centre(pts)
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    lo, hi = bb.CornerMin(), bb.CornerMax()
    return (0.5 * (lo.X() + hi.X()), 0.5 * (lo.Y() + hi.Y()), 0.5 * (lo.Z() + hi.Z()))


def _dist(a, b):
    return max(abs(a[i] - b[i]) for i in range(3))


print()
print("=" * 116)
print("GATE 5 -- MASTER/PROXY COHERENCE: does the analytic master follow the display?")
print("=" * 116)
try:
    from narwhal3d.kernel import occt_backend as _ob5
    if not _ob5.available():
        raise ImportError(_ob5.unavailable_reason())
    _MOVE = r3.Plane  # noqa: F841  (placeholder so the import block is obviously used)
    mv = r3.Transform.Translation(100.0, 0.0, 0.0)

    # (a) a boolean result's master must move with its proxy
    _bx = bridge.make_box((0, 0, 0), (4, 4, 0), 4.0)
    _cy = bridge.make_cylinder((2, 2, 0), 1.5, 6.0)
    _u = bridge.boolean_brep(_bx.Duplicate(), _cy.Duplicate(), "UNION")
    _m = kernel.master_of(_u)
    if _m is None:
        COHERENCE.append(("boolean produced no master", False,
                          f"declined: {bridge.last_occt_decline()}"))
    else:
        bridge.transform_geometry(_u, mv)
        _exact = not kernel.looks_tessellated(_u)      # exact polysurface
        d = _dist(_centre_brep(_u, _exact), _centre_shape(kernel.master_of(_u), _exact))
        COHERENCE.append(("a moved boolean result: master follows proxy", d < 1e-9,
                          f"centres differ by {d:.3e}"))

    # (b) a moved primitive's RECIPE must rebuild in the new place
    _c2 = bridge.make_cylinder((2, 2, 0), 1.5, 6.0)
    bridge.transform_geometry(_c2, mv)
    _rb = _ob5.from_recipe(kernel.recipe_of(_c2))
    d2 = _dist(_centre_brep(_c2), _centre_shape(_rb))
    COHERENCE.append(("a moved primitive: its recipe rebuilds in the new place", d2 < 1e-9,
                      f"centres differ by {d2:.3e}"))

    # (c) THE CONSEQUENCE: a boolean with a moved-away operand must NOT cut anything
    _b3 = bridge.make_box((0, 0, 0), (4, 4, 0), 4.0)
    _c3 = bridge.make_cylinder((2, 2, 0), 1.5, 6.0)
    bridge.transform_geometry(_c3, r3.Transform.Translation(20.0, 0.0, 0.0))
    _r3res = bridge.boolean_brep(_b3.Duplicate(), _c3, "DIFFERENCE")
    _m3 = kernel.master_of(_r3res)
    if _m3 is None:
        COHERENCE.append(("a boolean with a moved-away operand", False,
                          f"declined: {bridge.last_occt_decline()}"))
    else:
        vol = _ob5.volume(_m3)
        ftypes = _ob5.face_types(_m3)
        ok = abs(vol - 64.0) < 1e-6 and "CYLINDER" not in ftypes
        COHERENCE.append(("a cylinder moved AWAY cuts nothing from the box", ok,
                          f"volume {vol:.4f} (want 64.0), faces {ftypes}"))

    # (d) a COPY must not corrupt the original's master -- user strings are copied, so the
    #     duplicate starts out sharing the original's master id
    _b4 = bridge.make_box((0, 0, 0), (4, 4, 0), 4.0)
    _c4 = bridge.make_cylinder((2, 2, 0), 1.5, 6.0)
    _orig = bridge.boolean_brep(_b4.Duplicate(), _c4.Duplicate(), "UNION")
    _before = _centre_shape(kernel.master_of(_orig))
    _copy = _orig.Duplicate()
    bridge.transform_geometry(_copy, mv)
    _after = _centre_shape(kernel.master_of(_orig))
    d4 = _dist(_before, _after)
    COHERENCE.append(("moving a COPY leaves the original's master untouched", d4 < 1e-9,
                      f"the original's master shifted by {d4:.3e}"))

    # (e) A non-uniform scale must stay non-uniform in the master and recipe.
    _pl = r3.Plane(r3.Point3d(0, 0, 0), r3.Vector3d(1, 0, 0), r3.Vector3d(0, 1, 0))
    _b5 = bridge.transform_geometry(bridge.make_box((0, 0, 0), (4, 4, 0), 4.0),
                                    r3.Transform.Scale(_pl, 2, 1, 1))
    _s5 = bridge._occt_operand(_b5)
    _bb5 = _ob5.tight_bbox(_s5) if _s5 is not None else None
    _db5 = _b5.GetBoundingBox()
    ok5 = _bb5 is not None and max(
        abs(a - b) for a, b in zip(_bb5[0] + _bb5[1], (_db5.Min.X, _db5.Min.Y, _db5.Min.Z,
                                                      _db5.Max.X, _db5.Max.Y, _db5.Max.Z))
    ) < 1e-6 and abs(_db5.Max.X - 8.0) < 1e-9
    COHERENCE.append(("a NON-UNIFORM scale: the exact shape matches the document", ok5,
                      f"exact {_bb5}, document x to {_db5.Max.X:g}"))
except Exception as e:  # noqa: BLE001
    COHERENCE.append(("gate 5 could not run", False, repr(e)[:70]))

incoherent = [name for name, ok, _ in COHERENCE if not ok]
for name, ok, detail in COHERENCE:
    print(f"   {'ok  ' if ok else '*** '}{name:<58} {detail}")
if incoherent:
    print()
    print(f"  FAIL: {len(incoherent)} coherence check(s) failed. The geometry a machine")
    print("        shop would receive does not match what the user sees. This is the")
    print("        worst class of defect in the project -- silent and wrong.")
else:
    print()
    print("  ok: the analytic master tracks its display proxy through transforms, a moved")
    print("      primitive's recipe rebuilds where it now is, and copying does not corrupt")
    print("      the original's master.")
print("=" * 116)

# ==========================================================================
# GATE 6 -- does analytic geometry SURVIVE SAVE AND RELOAD?
#
# Masters are process-local. Clear the registry before rehydration so this gate proves
# the recipe tree can rebuild exact geometry using only data stored in the .3dm file.
# ==========================================================================
ROUNDTRIP = []
print()
print("=" * 116)
print("GATE 6 -- SAVE/RELOAD: is the analytic master rebuilt, exactly?")
print("=" * 116)
try:
    from narwhal3d.kernel import occt_backend as _ob6
    if not _ob6.available():
        raise ImportError(_ob6.unavailable_reason())

    _b6 = bridge.make_box((0, 0, 0), (4, 4, 0), 4.0)
    _c6 = bridge.make_cylinder((2, 2, 0), 1.5, 6.0)
    _s6 = bridge.make_sphere((2, 2, 6), 1.0)
    _u6 = bridge.boolean_brep(_b6.Duplicate(), _c6.Duplicate(), "UNION")
    _nested = bridge.boolean_brep(_u6, _s6, "DIFFERENCE")      # a boolean OF a boolean
    _m6 = kernel.master_of(_nested)
    if _m6 is None:
        ROUNDTRIP.append(("a nested boolean produced a master", False,
                          f"declined: {bridge.last_occt_decline()}"))
    else:
        want_vol = _ob6.volume(_m6)
        want_types = _ob6.face_types(_m6)
        ROUNDTRIP.append(("a boolean records a recipe TREE",
                          (kernel.recipe_of(_nested) or {}).get("kind") == "boolean",
                          f"recipe kind {(kernel.recipe_of(_nested) or {}).get('kind')!r}"))

        # write, then simulate reopening the application by clearing the registry
        _path = os.path.join(ROOT, "artifacts", "audit_roundtrip.3dm")
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        _f6 = r3.File3dm()
        _f6.Objects.AddBrep(_nested)
        _f6.Write(_path, 8)
        _back = r3.File3dm.Read(_path)
        _geoms = [o.Geometry for o in _back.Objects]
        kernel.forget_masters()          # <- the step that makes this a real test

        _g6 = _geoms[0]
        # The file geometry itself must be the analytic polysurface, not its display proxy.
        from narwhal3d.kernel import onbrep as _onb
        if _onb.ensure():
            _nfaces = sum(want_types.values())
            ROUNDTRIP.append(("the .3dm holds the EXACT polysurface, not triangles",
                              len(_g6.Faces) == _nfaces and not kernel.looks_tessellated(_g6)
                              and _g6.IsValid and _g6.IsSolid,
                              f"{len(_g6.Faces)} faces in the file (master {_nfaces}), "
                              f"valid={_g6.IsValid} solid={_g6.IsSolid}"))
        else:
            print(f"  SKIP the file-geometry check: the openNURBS writer is unavailable "
                  f"({_onb.last_error()}) -- the .3dm holds TRIANGLES for OCCT solids")
        ROUNDTRIP.append(("before rehydration the proxy admits it is BROKEN",
                          kernel.honesty_problem(_g6) is not None,
                          f"describe: {kernel.describe(_g6)[:46]}"))
        restored, lost = bridge.rehydrate_document(_geoms)
        ROUNDTRIP.append(("rehydrate_document restores it", restored == 1 and lost == 0,
                          f"restored={restored} lost={lost}"))
        _m6b = kernel.master_of(_g6)
        if _m6b is None:
            ROUNDTRIP.append(("the rebuilt master is analytic", False, "no master"))
        else:
            ROUNDTRIP.append(("and the rebuild is EXACT -- same faces and volume",
                              _ob6.face_types(_m6b) == want_types
                              and abs(_ob6.volume(_m6b) - want_vol) < 1e-9,
                              f"{_ob6.face_types(_m6b)} vol {_ob6.volume(_m6b):.4f} "
                              f"(wanted {want_types} vol {want_vol:.4f})"))
            ROUNDTRIP.append(("airtight after reload", _ob6.is_airtight(_m6b), ""))
            ROUNDTRIP.append(("and honest again", kernel.honesty_problem(_g6) is None,
                              f"describe: {kernel.describe(_g6)[:46]}"))

    # a TRIANGULATED proxy with NO recipe must be DEMOTED, not left claiming a master
    _orphan = bridge._triangulated_proxy(_ob6.from_recipe({"kind": "sphere", "radius": 2}))
    kernel.register_master(_orphan, object())
    kernel.record_recipe(_orphan, {})
    kernel.forget_masters()
    bridge.rehydrate_master(_orphan)
    ROUNDTRIP.append(("an unrebuildable proxy is DEMOTED to mesh-derived",
                      kernel.representation_of(_orphan) == kernel.MESH_DERIVED,
                      f"tag is now {kernel.representation_of(_orphan)!r}"))
    # An exact document object that loses its master remains analytic.
    _exact = bridge.make_box((0, 0, 0), (2, 2, 0), 2.0)
    kernel.register_master(_exact, object())
    kernel.record_recipe(_exact, {})
    kernel.forget_masters()
    bridge.rehydrate_master(_exact)
    ROUNDTRIP.append(("an EXACT orphan keeps ANALYTIC (it is not triangles)",
                      kernel.representation_of(_exact) == kernel.ANALYTIC,
                      f"tag is now {kernel.representation_of(_exact)!r}"))

    # Reopen every master kind in fresh processes after loading an unrelated document.
    # This detects incomplete recipe dispatch and process-local master-id collisions.
    import json as _json
    import subprocess as _sp

    _helper = os.path.join(ROOT, "spike", "_roundtrip_reader.py")
    _blender = os.path.join(ROOT, "tools", "blender-patched", "blender")
    _tpath = os.path.join(ROOT, "artifacts", "audit_roundtrip_kinds.3dm")
    _opath = os.path.join(ROOT, "artifacts", "audit_roundtrip_other.3dm")

    def _fresh(*args):
        proc = _sp.run([
            _blender, "-b", "--factory-startup", "--python-exit-code", "1",
            "--python", _helper, "--", *args,
        ], capture_output=True, text=True, timeout=300)
        line = next((ln for ln in proc.stdout.splitlines()
                     if ln.startswith("ROUNDTRIP-JSON ")), None)
        return (_json.loads(line[len("ROUNDTRIP-JSON "):]) if line else None,
                (proc.stderr or proc.stdout)[-70:])

    _want, _err = _fresh("write", _tpath, _opath)
    _got, _err2 = _fresh("read", _opath, _tpath) if _want is not None else (None, _err)
    if _want is None or _got is None:
        ROUNDTRIP.append(("fresh processes could write and reopen the document", False,
                          _err2))
    else:
        for _name in sorted(_want):
            ROUNDTRIP.append((f"FRESH PROCESS: a {_name} reopens as itself, in place",
                              _got.get(_name) == _want[_name] and _want[_name] is not None,
                              f"{_got.get(_name)} (wanted {_want[_name]})"))
except Exception as e:  # noqa: BLE001
    ROUNDTRIP.append(("gate 6 could not run", False, repr(e)[:70]))

broken_rt = [n for n, ok, _ in ROUNDTRIP if not ok]
for name, ok, detail in ROUNDTRIP:
    print(f"   {'ok  ' if ok else '*** '}{name:<56} {detail}")
if broken_rt:
    print()
    print(f"  FAIL: {len(broken_rt)} round-trip check(s) failed -- analytic geometry does")
    print("        not survive saving and reopening the document.")
else:
    print()
    print("  ok: a boolean result rebuilds itself from its stored recipe tree after a")
    print("      reload, exactly, and anything unrebuildable is demoted honestly.")
print("=" * 116)

sys.exit(1 if (unexpected or leaks or uncovered or seg_bad or incoherent or broken_rt)
         else 0)
