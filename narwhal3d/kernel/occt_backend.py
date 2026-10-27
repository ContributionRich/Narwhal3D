"""The Open CASCADE backend and the only module in this project that imports OCP.

Commands call `kernel.*` rather than OCP directly, keeping the backend swappable and
isolating native-kernel failures.

Representative analytic results:

    box UNION cylinder          valid  1 solid 10 faces  {PLANE: 9, CYLINDER: 1}
    box MINUS cylinder          valid  1 solid  7 faces  {PLANE: 6, CYLINDER: 1}
    box INTERSECT sphere        valid  1 solid  4 faces  {PLANE: 3, SPHERE: 1}
    box, 12 edges filleted r1   valid  1 solid 20 faces  {PLANE: 6, CYLINDER: 10,
                                                          SPHERE: 4}
    STEP of the drilled box     19 132 bytes, containing
                                #71 = CYLINDRICAL_SURFACE('',#72,3.);

Compare today's mesh route: a box union is ~40 triangles with NO analytic faces, and
two unioned cylinders were 2616. That is the whole reason this file exists.

`vendor/occt` is gitignored and is not on `sys.path` by default. :func:`available` adds
it when present and reports failure without raising at import time, so the application
can still start without the optional backend.
"""
from __future__ import annotations

import math
import os
import sys

from . import KernelError, ANALYTIC, MESH_DERIVED  # noqa: F401

#: Where the vendored Open CASCADE Python package is installed.
OCCT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vendor", "occt")

_import_error = None
_ocp = None


def available():
    """True when OCP can be imported. Puts `vendor/occt` on the path if needed.

    Never raises: a missing kernel must degrade to the mesh backend with a message, not
    break startup. :func:`unavailable_reason` says why when it is False.
    """
    global _ocp, _import_error
    if _ocp is not None:
        return True
    if OCCT_PATH not in sys.path and os.path.isdir(OCCT_PATH):
        sys.path.insert(0, OCCT_PATH)
    try:
        import OCP  # noqa: F401

        _ocp = OCP
        _import_error = None
        return True
    except Exception as e:  # noqa: BLE001
        _import_error = e
        return False


def unavailable_reason():
    """Why :func:`available` returned False, or None."""
    if _ocp is not None:
        return None
    if not os.path.isdir(OCCT_PATH):
        return (f"the OpenCASCADE kernel is not installed at {OCCT_PATH}; "
                "install the locked OCP runtime dependency")
    return f"OCP could not be imported: {_import_error!r}"


def _need():
    if not available():
        raise KernelError(unavailable_reason())


# --------------------------------------------------------------------------
# primitives -- enough to test the pipeline end to end without a converter
# --------------------------------------------------------------------------

def make_box(dx, dy, dz):
    """An analytic box solid."""
    _need()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    try:
        return BRepPrimAPI_MakeBox(float(dx), float(dy), float(dz)).Shape()
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not build a box: {e}") from e


def make_cylinder(radius, height):
    """An analytic cylinder solid -- a real `CYLINDER` face, not 32 strips."""
    _need()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    try:
        return BRepPrimAPI_MakeCylinder(float(radius), float(height)).Shape()
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not build a cylinder: {e}") from e


def make_sphere(radius):
    """An analytic sphere solid."""
    _need()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

    try:
        return BRepPrimAPI_MakeSphere(float(radius)).Shape()
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not build a sphere: {e}") from e


def make_cone(radius, height):
    """An analytic cone solid (apex on the axis) -- a base disc + one CONE face."""
    _need()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone

    try:
        return BRepPrimAPI_MakeCone(float(radius), 0.0, float(height)).Shape()
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not build a cone: {e}") from e


def make_torus(major, minor):
    """A ring torus about +Z -- one `TORUS` face, volume 2 pi^2 R r^2. A spindle or
    horn torus (minor >= major) intersects itself and is refused."""
    _need()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeTorus

    if not (0.0 < float(minor) < float(major)):
        raise KernelError(f"a torus needs 0 < minor radius < major radius (got major "
                          f"{major}, minor {minor}); a spindle or horn torus intersects "
                          f"itself")
    try:
        return BRepPrimAPI_MakeTorus(float(major), float(minor)).Shape()
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not build a torus: {e}") from e


def make_truncated_cone(radius, top, height):
    """A frustum about +Z, base `radius` at z=0 and `top` at z=`height` --
    `{CONE: 1, PLANE: 2}`, volume pi h (R^2 + R r + r^2) / 3."""
    _need()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone

    r1, r2, h = float(radius), float(top), float(height)
    if r1 <= 0.0 or r2 <= 0.0 or h <= 0.0 or abs(r1 - r2) <= 1e-12:
        raise KernelError(f"a truncated cone needs two DIFFERENT positive radii and a "
                          f"positive height (got {r1}, {r2}, {h})")
    try:
        return BRepPrimAPI_MakeCone(r1, r2, h).Shape()
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not build a truncated cone: {e}") from e


def make_tube(outer, inner, height):
    """A cylinder with a wall about +Z -- `{CYLINDER: 2, PLANE: 2}` (two annular
    caps), volume pi h (R^2 - r^2). Built as outer cylinder MINUS inner, unified."""
    _need()
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    ro, ri, h = float(outer), float(inner), float(height)
    if not (0.0 < ri < ro) or h <= 0.0:
        raise KernelError(f"a tube needs 0 < inner radius < outer radius and a positive "
                          f"height (got outer {ro}, inner {ri}, height {h})")
    a = BRepPrimAPI_MakeCylinder(ro, h).Shape()
    # the cutter overshoots both caps so no face of it is coplanar with a cap
    ax = gp_Ax2(gp_Pnt(0.0, 0.0, -h), gp_Dir(0.0, 0.0, 1.0))
    b = BRepPrimAPI_MakeCylinder(ax, ri, 3.0 * h).Shape()
    return boolean(a, b, "DIFFERENCE")


# --------------------------------------------------------------------------
# RECIPES -- how a rhino3dm solid is REBUILT here, exactly
#
# A rhino3dm Brep cannot be converted into an OCCT shape faithfully, because a
# cylinder's end cap is a degree-1x1 SQUARE plane whose circular disc exists only as a
# TRIM -- and rhino3dm exposes no Loops, no Trims and no Curves2D, so the trim is
# unreachable. Converting face-by-face from natural bounds would turn every cap into a
# square, silently, which is worse than refusing.
#
# So instead of converting the RESULT we rebuild from the RECIPE: the primitive kind, its
# parameters, and the world transform that was applied to it. That is exact, needs no trim
# data, and COMPOSES -- a boolean of OCCT shapes is an OCCT shape, so a boolean of a
# boolean works, which a recipe-only scheme would lose.
#
# A solid whose recipe is unavailable is refused by name rather than approximated.
# --------------------------------------------------------------------------

#: Primitive kinds that can be rebuilt here, and the parameters each needs.
RECIPE_KINDS = {
    "box": ("dx", "dy", "dz"),
    "cylinder": ("radius", "height"),
    "sphere": ("radius",),
    "cone": ("radius", "height"),
    # An extrusion is driven by a serialized planar profile. Without this recipe,
    # unioning two extruded circles falls back to the mesh route and yields 3260 facets.
    "extrusion": ("profile", "dir"),
    # A round tube along a rail, and closing a shape's planar ends. `pipe` carries the
    # rail as a serialized curve; `cap` nests its operand like the booleans do.
    "pipe": ("rail", "radius"),
    "cap": ("a",),
    # One face of another solid -- what Explode makes of a master-backed polysurface.
    # `index` is the face's position in `unique_faces(a)`, so the piece rebuilds from its
    # parent's recipe after a reload exactly as a beveled edge does.
    "face": ("index", "a"),
    # A planar face given by its boundary loops (outer first, then holes), each a list
    # of serialized curves in traversal order. What an exact planar Trim/Split produces, and
    # what a Picture is -- recorded as the RESULT's boundary, so it rebuilds exactly.
    "planar_face": ("loops",),
    # A sweep along one rail (`BRepOffsetAPI_MakePipeShell`) -- `binormal` None is
    # the reference CAD application's Freeform (corrected Frenet), a direction is Roadlike (constant binormal).
    "sweep1": ("rail", "section"),
    # A surface/polysurface given by its own exact faces (`brep_data`) -- what a Loft or
    # Sweep2 produces, recorded as the result so it rebuilds bit-for-bit.
    "brep_data": ("data",),
    # A shape extruded straight (ExtrudeSrf): `a` is the operand's recipe, `dir` the
    # full extrusion vector; `both` = BothSides, `solid` = Solid (a face -> a closed solid).
    "prism": ("a", "dir"),
    # EdgeSrf -- the Coons surface of 2, 3 or 4 boundary curves (in loop order).
    "edge_surface": ("curves",),
    # Revolve: `profile` (a serialized curve) about the axis through `origin` along
    # `axis`, by `angle` degrees. A closed profile revolved a full turn is a solid.
    "revolve": ("profile", "origin", "axis", "angle"),
    # One piece of a curved single-face surface split by curves: `a` is the surface's
    # recipe, `curves` [[curve, normal|None], ...], `signature` {area, centroid} of the piece.
    "split_piece": ("a", "curves", "signature"),
    # Shell (`remove` = 1-based face indices of `a`) and OffsetSrf Solid=Yes.
    "shell": ("a", "remove", "thickness"),
    "thick_surface": ("a", "distance"),
    # A closed solid of planar polygon faces -- `vertices` [[x, y, z], ...] and `faces`
    # [[loop, hole, ...], ...], each loop a list of 1-based vertex indices, the outer one
    # wound OUTWARD. What moving a face, edge
    # or vertex of a planar-faced solid produces, recorded as the RESULT so it rebuilds
    # exactly (every face is a real PLANE through its own corners).
    "polyhedron": ("vertices", "faces"),
    # An exact topology-local edit. The parent recipe is rebuilt first, then only the
    # selected component and its incident topology are reconstructed. Unsupported
    # N-sided surface deformation is refused rather than approximated.
    "local_move": ("a", "component", "index", "vector", "target"),
    # These primitives are built canonically about the origin (axis +Z) and placed by the
    # recipe's `xform`, as box, cylinder, and cone are. An ellipsoid needs no separate kind:
    # it is a unit `sphere` whose transform carries non-uniform axis scaling, applied exactly
    # through `GTransform`.
    "torus": ("major", "minor"),
    "truncated_cone": ("radius", "top", "height"),
    "tube": ("outer", "inner", "height"),
    # Edge bevels. `a` is the operand's recipe, so a filleted boolean rebuilds its
    # whole history -- and so a fillet can itself be an operand of another boolean.
    # `edges` is OPTIONAL: absent means every edge. Recording it is what lets a
    # selectively-beveled solid rebuild identically after a reload.
    "fillet": ("size", "a"),
    "chamfer": ("size", "a"),
    # A recipe is a tree, not just a primitive. A boolean records its operands'
    # recipes and its operation, so it can rebuild itself from nothing but the data stored
    # in the .3dm. That is what lets an analytic solid SURVIVE SAVE AND RELOAD: masters are
    # process-local by design, so without this a reopened boolean result was a 264-face
    # proxy with a dangling master id and its real geometry gone.
    #
    # It also composes to any depth, because an operand's recipe may itself be a boolean.
    "boolean": ("op", "a", "b"),
}

#: Maximum recipe-tree depth, guarding against cyclic or unreasonable input
#: turning a document load into a hang; 64 nested booleans is far beyond real modelling.
MAX_RECIPE_DEPTH = 64


# --------------------------------------------------------------------------
# profile CURVES, for extrusions and revolves
#
# This is not simply "convert the NURBS and be done":
#
#   * A rhino3dm curve's control points are **premultiplied by W**, exactly like a
#     surface's. The corner pole of a radius-5 circle reads
#     hypot(X,Y) = 5.0 and hypot/W = 7.0711, and 7.0711 = 5*sqrt(2) is the true one.
#   * Rebuilding a circle as a generic B-spline and extruding it gives a
#     `SURFACEOFEXTRUSION` face whose volume integrates **3.2 % wrong**. Rebuilding it as a
#     `Geom_Circle` gives `{CYLINDER: 1, PLANE: 2}` and a volume error of **0.00e+00**.
#
# The profile is therefore recognised by type first and only falls back to a B-spline. An
# extruded circle must retain two planar caps and one cylindrical surface rather than a
# generic patch.
# --------------------------------------------------------------------------

#: Profile curve kinds a recipe can carry.
CURVE_TYPES = ("circle", "arc", "line", "polyline", "nurbs", "polycurve")


def _knot_arrays(flat):
    """rhino3dm's flat knot list -> OCCT (values, multiplicities).

    rhino3dm omits the first and last knot of the full clamped vector (MEASURED: a 9-pole
    degree-2 circle stores 10 knots where the full vector is 12), so the end
    multiplicities are bumped by one to make the totals agree.
    """
    from OCP.collections import Array1_double, Array1_int

    uniq = []
    for k in flat:
        if not uniq or abs(k - uniq[-1][0]) > 1e-12:
            uniq.append([float(k), 1])
        else:
            uniq[-1][1] += 1
    if not uniq:
        raise KernelError("a curve recipe has no knots")
    uniq[0][1] += 1
    uniq[-1][1] += 1
    ka = Array1_double(1, len(uniq))
    ma = Array1_int(1, len(uniq))
    for i, (k, m) in enumerate(uniq, start=1):
        ka.SetValue(i, k)
        ma.SetValue(i, int(m))
    return ka, ma


def wire_from_curve(profile):
    """A `TopoDS_Wire` for a serialized profile curve. Raises KernelError if unsupported."""
    _need()
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakePolygon,
                                    BRepBuilderAPI_MakeWire)
    from OCP.collections import Array1_double, Array1_gp_Pnt
    from OCP.Geom import Geom_BSplineCurve, Geom_Circle, Geom_TrimmedCurve
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    if not isinstance(profile, dict):
        raise KernelError(f"a profile must be a dict (got {type(profile).__name__})")
    ctype = profile.get("ctype")
    if ctype not in CURVE_TYPES:
        raise KernelError(
            f"no OCCT builder for a {ctype!r} profile; supported are "
            f"{', '.join(CURVE_TYPES)}")
    if ctype == "polycurve":
        # A joined curve is one edge per segment, matching the reference CAD application, rather than one
        # B-spline with C0 knots inside it (see bridge._polycurve_segment_recipes for what
        # that measured as).
        segs = profile.get("segments")
        if not segs:
            raise KernelError("a 'polycurve' profile has no segments")
        if any(isinstance(s, dict) and s.get("ctype") == "polycurve" for s in segs):
            raise KernelError("a 'polycurve' profile must be flat (no nested polycurves)")
        try:
            return wire_from_curves(segs)
        except KernelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KernelError(f"could not rebuild a 'polycurve' profile: {e}") from e
    try:
        if ctype == "circle":
            c, n = profile["center"], profile["normal"]
            ax = gp_Ax2(gp_Pnt(*[float(v) for v in c]),
                        gp_Dir(*[float(v) for v in n]))
            edge = BRepBuilderAPI_MakeEdge(Geom_Circle(ax, float(profile["radius"]))).Edge()
            return BRepBuilderAPI_MakeWire(edge).Wire()
        if ctype == "arc":
            # A TRIMMED Geom_Circle, not a B-spline. This is what makes a pipe along an arc
            # come back as a real TORUS: MEASURED, serializing the rail generically instead
            # gave {BSPLINE: 1} and a volume 5.5e-05 % off, where the trimmed-circle rail gives
            # {TORUS: 1} exact to 1.4e-14 %. `xaxis` must travel too -- it is what fixes where
            # angle 0 is, and without it the arc would be trimmed from the wrong place.
            c, n, x = profile["center"], profile["normal"], profile["xaxis"]
            ax = gp_Ax2(gp_Pnt(*[float(v) for v in c]),
                        gp_Dir(*[float(v) for v in n]),
                        gp_Dir(*[float(v) for v in x]))
            circle = Geom_Circle(ax, float(profile["radius"]))
            trimmed = Geom_TrimmedCurve(circle, float(profile["start"]),
                                        float(profile["end"]))
            return BRepBuilderAPI_MakeWire(
                BRepBuilderAPI_MakeEdge(trimmed).Edge()).Wire()
        if ctype in ("line", "polyline"):
            pts = profile["points"] if ctype == "polyline" else [profile["a"], profile["b"]]
            poly = BRepBuilderAPI_MakePolygon()
            # a closed polyline repeats its first point; OCCT closes the wire itself, and a
            # duplicated vertex would make a zero-length edge
            seq = [tuple(float(v) for v in q) for q in pts]
            if len(seq) > 2 and max(abs(seq[0][i] - seq[-1][i]) for i in range(3)) < 1e-12:
                seq = seq[:-1]
                closed = True
            else:
                closed = False
            for q in seq:
                poly.Add(gp_Pnt(*q))
            if closed:
                poly.Close()
            return poly.Wire()
        poles = profile["poles"]
        weights = profile.get("weights") or [1.0] * len(poles)
        n = len(poles)
        pa = Array1_gp_Pnt(1, n)
        wa = Array1_double(1, n)
        for i, (q, w) in enumerate(zip(poles, weights), start=1):
            pa.SetValue(i, gp_Pnt(*[float(v) for v in q]))
            wa.SetValue(i, float(w))
        ka, ma = _knot_arrays(profile["knots"])
        # rhino3dm stores a periodic curve as wrapped poles over an
        # unclamped knot vector -- which is already an ordinary (non-periodic) B-spline
        # with the identical shape on its domain (1.9e-15 over a 24-point closed cubic).
        # Telling OCCT `periodic=True` with those poles raised "# Poles and degree
        # mismatch", so the flag is not passed on.
        curve = Geom_BSplineCurve(pa, wa, ka, ma, int(profile["degree"]), False)
        return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(curve).Edge()).Wire()
    except KernelError:
        raise
    except KeyError as e:
        raise KernelError(f"a {ctype!r} profile is missing {e}") from e
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not rebuild a {ctype!r} profile: {e}") from e


#: the reference CAD application's ExtrudeCrvTapered Corners values map to OCCT transition modes (Smooth = G2
#: blends has no OCCT transition and is refused by name).
DRAFT_CORNERS = ("Sharp", "Round")


def make_revolve(profile, origin, axis, angle_deg):
    """Revolve a serialized curve about an axis -- EXACT (`BRepPrimAPI_MakeRevol`): a
    line revolves into a cone/cylinder/plane, an arc into a sphere/torus, a NURBS curve
    into a surface of revolution. A closed profile revolved a full turn becomes a
    closed solid; anything else stays an open surface (the reference command does not
    cap a revolve)."""
    _need()
    import math
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

    angle = float(angle_deg)
    if not 0.0 < abs(angle) <= 360.0:
        raise KernelError(f"revolve: angle {angle:g} must be in (0, 360] degrees")
    wire = wire_from_curve(profile)
    ax = gp_Ax1(gp_Pnt(*[float(v) for v in origin]), gp_Dir(*[float(v) for v in axis]))
    full = abs(abs(angle) - 360.0) <= 1e-9
    maker = BRepPrimAPI_MakeRevol(wire, ax, math.radians(angle) if not full else 2 * math.pi, True)
    maker.Build()
    if not maker.IsDone():
        raise KernelError("revolve: OCCT could not revolve the curve")
    shape = maker.Shape()
    if full and wire.Closed():
        shells = []
        explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SHELL)
        while explorer.More():
            shells.append(TopoDS.Shell(explorer.Current()))
            explorer.Next()
        if len(shells) == 1:
            solid = BRepBuilderAPI_MakeSolid(shells[0]).Solid()
            if volume(solid) < 0:
                solid.Reverse()
            if is_airtight(solid):
                shape = solid
    return shape


def make_extrusion_ex(profile, direction, both=False, solid=True, draft_deg=None,
                      corners="Sharp", flip=False):
    """Build ExtrudeCrv / ExtrudeCrvTapered exactly; `direction` is the full vector.

    * straight: `BRepPrimAPI_MakePrism` of the profile's FACE (closed planar + `solid`:
      a capped solid) or of its WIRE (an open curve, or Solid=No: the side surface only);
    * `both` = BothSides: from -direction to +direction;
    * `draft_deg` = a taper built with `BRepOffsetAPI_MakeDraft`, then capped. `Perform`
      takes the slant length (h / cos a; passing h yields h cos a), while
      `SetDraft(internal)` rather than the angle's sign selects the side. A circle r=5,
      h=4 at 10 degrees produces a cone frustum exact to 2.1e-16; a square produces a
      six-plane frustum exact to 3.3e-16. Positive means inward for a closed profile;
      `flip` implements FlipAngle.
      `corners` Sharp / Round (the reference CAD application's Corners; Smooth is refused).
    """
    _need()
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform,
                                    BRepBuilderAPI_TransitionMode)
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Dir, gp_Trsf, gp_Vec

    d = [float(v) for v in direction]
    length = math.sqrt(sum(v * v for v in d))
    if length <= 0.0:
        raise KernelError("the extrusion distance is zero")
    wire = wire_from_curve(profile)
    closed = wire.Closed()
    face = None
    if closed and solid:
        try:
            mk = BRepBuilderAPI_MakeFace(wire, True)
            face = mk.Face() if mk.IsDone() else None
        except Exception:  # noqa: BLE001
            face = None
    if draft_deg not in (None, 0, 0.0):
        if both:
            raise KernelError("a tapered extrusion has no BothSides")
        if corners not in DRAFT_CORNERS:
            raise KernelError(f"Corners={corners} is not implemented yet (only "
                              f"{', '.join(DRAFT_CORNERS)})")
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeDraft

        a = math.radians(abs(float(draft_deg)))
        if a >= math.pi / 2:
            raise KernelError("the draft angle must be less than 90 degrees")
        inward = (float(draft_deg) > 0) != bool(flip)
        mode = (BRepBuilderAPI_TransitionMode.BRepBuilderAPI_RightCorner
                if corners == "Sharp" else
                BRepBuilderAPI_TransitionMode.BRepBuilderAPI_RoundCorner)
        md = BRepOffsetAPI_MakeDraft(wire, gp_Dir(*[v / length for v in d]), a)
        md.SetOptions(mode)
        # MEASURED: SetDraft(True) is INWARD whichever way the curve runs (a CW and a CCW
        # circle / square give the same frustum), so no orientation fix-up is needed
        md.SetDraft(inward)
        md.Perform(length / math.cos(a))
        if not md.IsDone():
            raise KernelError("the tapered extrusion did not complete")
        shell = md.Shape()
        return cap_planar_holes(shell) if face is not None else shell
    base = face if face is not None else wire
    vec = d
    if both:
        t = gp_Trsf()
        t.SetTranslation(gp_Vec(-d[0], -d[1], -d[2]))
        base = BRepBuilderAPI_Transform(base, t, True).Shape()
        vec = [2 * v for v in d]
    mk = BRepPrimAPI_MakePrism(base, gp_Vec(*vec))
    mk.Build()
    if not mk.IsDone():
        raise KernelError("the extrusion did not complete")
    return mk.Shape()


def make_extrusion(profile, direction):
    """Extrude a CLOSED planar `profile` along `direction`. An analytic solid.

    A radius-3 circle extruded by 5 gives `{CYLINDER: 1, PLANE: 2}` with volume
    141.371669412, matching pi*r^2*h exactly: two planar caps and one wrapping cylinder.
    """
    _need()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    wire = wire_from_curve(profile)
    try:
        face = BRepBuilderAPI_MakeFace(wire, True).Face()
        vec = gp_Vec(*[float(v) for v in direction])
        builder = BRepPrimAPI_MakePrism(face, vec)
        builder.Build()
        if not builder.IsDone():
            raise KernelError("the extrusion did not complete")
        return builder.Shape()
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not extrude the profile: {e}") from e


def make_prism(shape, direction, both=False, solid=True):
    """Extrude `shape` (a face or shell) straight along `direction` -- ExtrudeSrf.

    `BRepPrimAPI_MakePrism`: a FACE sweeps into a closed SOLID (its two copies are the
    caps), exact -- a planar face of area A gives volume A |d| with every face analytic.
    `solid=False` extrudes only the face's BOUNDARY (the side surfaces, open), as the reference CAD application's
    Solid=No. `both` = BothSides: the result runs from -direction to +direction (twice as
    long, MEASURED: "creating the object twice as long as you indicate").
    """
    _need()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Trsf, gp_Vec

    d = [float(v) for v in direction]
    if (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]) <= 0.0:
        raise KernelError("the extrusion distance is zero")
    base = shape
    if not solid:
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound

        comp = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(comp)
        for f in unique_faces(shape):
            builder.Add(comp, BRepTools.OuterWire_s(f))
            for w in _inner_wires(f):
                builder.Add(comp, w)
        base = comp
    try:
        if both:
            t = gp_Trsf()
            t.SetTranslation(gp_Vec(-d[0], -d[1], -d[2]))
            base = BRepBuilderAPI_Transform(base, t, True).Shape()
            d = [2 * v for v in d]
        mk = BRepPrimAPI_MakePrism(base, gp_Vec(*d))
        mk.Build()
        if not mk.IsDone():
            raise KernelError("the extrusion did not complete")
        out = mk.Shape()
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not extrude: {e}") from e
    if solid and solid_count(out) == 1 and volume(out) < 0:
        from OCP.TopoDS import TopoDS

        out = TopoDS.Solid(out.Reversed()) if out.ShapeType().name == "TopAbs_SOLID" \
            else out.Reversed()
    return out


def _inner_wires(face):
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    outer = BRepTools.OuterWire_s(face)
    out = []
    exp = TopExp_Explorer(face, TopAbs_WIRE)
    while exp.More():
        w = TopoDS.Wire(exp.Current())
        if not w.IsSame(outer):
            out.append(w)
        exp.Next()
    return out


def make_edge_surface(curves):
    """EdgeSrf -- the surface bounded by 2, 3 or 4 serialized curves, given in loop
    ORDER, each running from the previous one's end (the caller orders and orients them).

    `GeomFill_BSplineCurves` (Stretch style), a Coons patch, is exact on these closed
    forms: four lines bounding a twisted square give
    z = xy to 1.1e-16; two quarter arcs + two lines give the quarter CYLINDER to 1.8e-15;
    four bulging arcs' surface reproduces the input curves on its boundary to 6.9e-16.
    Three curves: the 3-curve Coons (a singular corner opposite the first, as the help
    says). Two curves: the caller closes the loop with the two straight end segments
    (GeomFill's own 2-curve mode is NOT a ruled surface: 1.46 off the cylinder).
    """
    _need()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.GeomFill import GeomFill_BSplineCurves, GeomFill_FillingStyle

    if len(curves) not in (3, 4):
        raise KernelError("an edge surface needs 3 or 4 curves in loop order (2 curves are "
                          "closed with straight segments by the caller)")
    bs = [_bspline_any(c) for c in curves]
    style = GeomFill_FillingStyle.GeomFill_StretchStyle
    try:
        filler = GeomFill_BSplineCurves(*bs, style)
        surf = filler.Surface()
        face = BRepBuilderAPI_MakeFace(surf, 1e-7).Face()
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"the edge curves do not bound a surface: {e}") from e
    return face


def _bspline_any(d):
    """A serialized curve of ANY ctype as a `Geom_BSplineCurve` (exact conversion)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.Geom import Geom_TrimmedCurve
    from OCP.GeomConvert import GeomConvert
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    w = wire_from_curve(d)
    exp = TopExp_Explorer(w, TopAbs_EDGE)
    edges = []
    while exp.More():
        edges.append(TopoDS.Edge(exp.Current()))
        exp.Next()
    def one(e):
        a = BRepAdaptor_Curve(e)
        c = BRep_Tool.Curve_s(e, a.FirstParameter(), a.LastParameter())
        return GeomConvert.CurveToBSplineCurve_s(
            Geom_TrimmedCurve(c, a.FirstParameter(), a.LastParameter()))

    if len(edges) == 1:
        return one(edges[0])
    if d.get("ctype") != "polycurve" or not edges:
        raise KernelError("an edge-surface boundary must be ONE curve (join polylines into "
                          "a single curve first)")
    # A joined curve arrives segment by segment; an edge surface needs it as
    # ONE B-spline, so the segments are concatenated EXACTLY (C0 knots at the joints), in
    # their stored order -- `bridge._polycurve_segment_recipes` already runs them
    # head-to-tail, and a closed wire's explorer would start at an arbitrary edge.
    from OCP.GeomConvert import GeomConvert_CompCurveToBSplineCurve

    joiner = None
    for seg in d["segments"]:
        bs = _bspline_any(seg)
        if joiner is None:
            joiner = GeomConvert_CompCurveToBSplineCurve(bs)
        # After=True: a closed loop's last segment also touches the START, and without it
        # OCCT prepends it there (MEASURED: the curve then began at the last joint)
        elif not joiner.Add(bs, 1e-7, True):
            raise KernelError("the joined curve's segments do not meet end to end")
    return joiner.BSplineCurve()


def planar_regions(loops):
    """PlanarSrf -- planar faces from closed coplanar curve loops (each loop a list
    of serialized curves). MEASURED (commands/planarsrf.htm): "If a curve is wholly within
    another curve then it will be treated as a hole boundary"; "If the curves partially
    overlap, each curve produces a separate surface." Nesting depth decides: even depth =
    an outer boundary, odd = a hole in the loop directly containing it (an island inside a
    hole is a new face). Returns ``[[outer_loop, hole_loop, ...], ...]`` -- the input to
    `face_from_curve_loops` per face.
    """
    _need()
    faces = [face_from_curve_loops([lp]) for lp in loops]
    areas = [abs(mass_properties(f, solid=False)["area"]) for f in faces]
    samples = [_loop_samples(lp) for lp in loops]

    # OURS (owner-required multi-profile workflow): collapse only numerically coincident
    # boundaries. Duplicate selected loops otherwise become duplicate inner wires in one
    # face, which OCCT correctly rejects as invalid topology. Partial overlaps and genuinely
    # offset loops remain separate, matching the documented PlanarSrf rules.
    keep = []
    for i in range(len(loops)):
        duplicate = False
        for j in keep:
            size = math.sqrt(max(areas[i], areas[j], 1.0))
            distance_tol = max(1e-7, size * 1e-9)
            area_tol = max(1e-12, max(areas[i], areas[j], 1.0) * 1e-7)
            if abs(areas[i] - areas[j]) > area_tol:
                continue
            boundary_i = _boundary(faces[i])
            boundary_j = _boundary(faces[j])
            if all(distance_to_shape(boundary_j, p) <= distance_tol for p in samples[i]) \
                    and all(distance_to_shape(boundary_i, p) <= distance_tol
                            for p in samples[j]):
                duplicate = True
                break
        if not duplicate:
            keep.append(i)
    if len(keep) != len(loops):
        loops = [loops[i] for i in keep]
        faces = [faces[i] for i in keep]
        areas = [areas[i] for i in keep]
        samples = [samples[i] for i in keep]

    n = len(loops)

    def inside(i, j):            # loop i wholly within loop j's region
        if areas[i] >= areas[j]:
            return False
        return all(distance_to_shape(faces[j], p) <= 1e-9 for p in samples[i]) and \
            not any(distance_to_shape(_boundary(faces[j]), p) <= 1e-9 for p in samples[i])

    parent = []
    for i in range(n):
        holders = [j for j in range(n) if j != i and inside(i, j)]
        parent.append(min(holders, key=lambda j: areas[j]) if holders else None)

    def depth(i):
        d, k = 0, parent[i]
        while k is not None:
            d, k = d + 1, parent[k]
        return d

    out = []
    for i in range(n):
        if depth(i) % 2 == 0:
            holes = [loops[k] for k in range(n) if parent[k] == i]
            out.append([loops[i]] + holes)
    return out


def _boundary(face):
    from OCP.BRepTools import BRepTools
    return BRepTools.OuterWire_s(face)


def _loop_samples(loop, per=8):
    pts = []
    for d in loop:
        w = wire_from_curve(d)
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve
        a = BRepAdaptor_CompCurve(w)
        t0, t1 = a.FirstParameter(), a.LastParameter()
        for k in range(per):
            p = a.Value(t0 + (t1 - t0) * (k + 0.5) / per)
            pts.append((p.X(), p.Y(), p.Z()))
    return pts


def make_shell(shape, remove_indices, thickness):
    """Hollow `shape` to a wall `thickness`, removing the faces at `remove_indices`
    (1-based, `unique_faces` order) removed. MEASURED (commands/offsetsrf.htm#Shell): "These
    surfaces are removed and the remainder is offset inward"; "shelling ... always uses
    sharp corners". `BRepOffsetAPI_MakeThickSolid.MakeThickSolidByJoin` with a NEGATIVE
    offset (inward). A 10-unit cube shelled by 1 with its top removed is
    11 planes, airtight, volume 424 (= 1000 - 8*8*9); a cylinder r5 h10 is
    {CYLINDER: 2, PLANE: 3} with pi (25 - 16) 9 + pi 25 exact.
    """
    _need()
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
    from OCP.collections import List_TopoDS_Shape

    t = float(thickness)
    if not t > 0:
        raise KernelError("the shell thickness must be positive")
    faces = unique_faces(shape)
    idx = sorted({int(i) for i in remove_indices})
    if not idx:
        raise KernelError("select at least one face to remove")
    if len(idx) >= len(faces):
        raise KernelError("leave at least one face unselected")
    if idx[0] < 1 or idx[-1] > len(faces):
        raise KernelError(f"face index out of range 1..{len(faces)}")
    lst = List_TopoDS_Shape()
    for i in idx:
        lst.Append(faces[i - 1])
    try:
        mk = BRepOffsetAPI_MakeThickSolid()
        mk.MakeThickSolidByJoin(shape, lst, -t, 1e-6)
        mk.Build()
        if not mk.IsDone():
            raise KernelError("the shell could not be built (the wall may be too thick)")
        out = mk.Shape()
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"the shell could not be built: {e}") from e
    out = _outward(out)
    # MEASURED: a 10-cube "shelled" 6 (the walls would cross) came back IsDone with the
    # UNSHELLED cube -- 6 faces, volume 1000 -- a silent no-op. A real shell removes volume
    # and adds the inner faces, so a result that did neither is refused by name.
    if len(unique_faces(out)) <= len(faces) or volume(out) >= volume(shape) * (1 - 1e-9):
        raise KernelError(f"a {t:g} wall is too thick for this solid -- the walls would "
                          f"cross")
    return out


def make_thick_surface(shape, distance):
    """OffsetSrf Solid=Yes -- the closed solid between a surface and its offset by
    `distance` (signed along the surface normal), joined by ruled side faces (MEASURED,
    offsetsrf.htm: "Makes a closed solid from the input and offset surfaces by lofting a
    ruled surface between all of the matching edges"). `MakeThickSolidBySimple`. MEASURED:
    an open cylinder wall r5 h10 by +1 is {CYLINDER: 2, PLANE: 2} with radii exactly 5 and 6
    -- but came back INSIDE-OUT (volume -345.58), hence `_outward`.
    """
    _need()
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid

    d = float(distance)
    if d == 0.0:
        raise KernelError("the offset distance must not be zero")
    try:
        mk = BRepOffsetAPI_MakeThickSolid()
        mk.MakeThickSolidBySimple(shape, d)
        mk.Build()
        if not mk.IsDone():
            raise KernelError("the offset solid could not be built")
        out = mk.Shape()
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"the offset solid could not be built: {e}") from e
    return _outward(out)


def _outward(shape):
    """Reverse a solid whose negative integrated volume shows that it is inside-out."""
    from OCP.TopoDS import TopoDS

    if solid_count(shape) == 1 and volume(shape) < 0:
        r = shape.Reversed()
        return TopoDS.Solid(r) if r.ShapeType().name == "TopAbs_SOLID" else r
    return shape


#: MakePipeShell's measured 3-D approximation tolerance, in model units.
SWEEP_TOLERANCE = 1e-7


def make_sweep1(rail, section, binormal=None):
    """Sweep the serialized `section` along the serialized `rail` exactly.

    `BRepOffsetAPI_MakePipeShell`: the section keeps its placement relative to the rail's
    START frame (as `sweep.sweep1_sections` does). `binormal` None = corrected Frenet, a
    rotation-minimising frame (the reference CAD application's Freeform: "the cross-section rotates to maintain its
    angle to the rail"); a direction = the constant-binormal trihedron (the reference CAD application's Roadlike:
    x = tangent x up, y = x x tangent). A circle r=1 along a quarter arc R=10 is one
    `TORUS` face, every point 1 from the arc to 3.8e-15 --
    the skinned sweep it replaces missed by 1.7e-5.
    """
    _need()
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    from OCP.gp import gp_Dir

    spine = wire_from_curve(rail)
    profile = wire_from_curve(section)
    try:
        ps = BRepOffsetAPI_MakePipeShell(spine)
        if binormal is None:
            ps.SetMode(False)                 # CorrectedFrenet
        else:
            ps.SetMode(gp_Dir(*[float(v) for v in binormal]))
        # MEASURED: the default 1e-4 left the swept B-spline 5.5e-5 off the frame
        # construction; 1e-7 brings it to 6.7e-8 (ErrorOnSurface 8.7e-8). Accuracy beats
        # convenience (rule 0). Analytic cases (a circle along an arc -> TORUS) are exact
        # either way.
        ps.SetTolerance(SWEEP_TOLERANCE, SWEEP_TOLERANCE, 1e-4)
        ps.Add(profile)
        ps.Build()
        if not ps.IsDone():
            raise KernelError("the sweep did not complete")
        return ps.Shape()
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not sweep the section: {e}") from e


def edge_curves(shape):
    """Every EDGE of `shape` as a serialized NURBS curve. The analytic wireframe.

    This is what `ExtractWireframe` needs, and it is the most direct way for a human to
    VERIFY that a solid really is analytic: a unioned pair of extruded circles yields a
    handful of real arcs and lines, not hundreds of triangle edges.

    ONE UNIFORM ROUTE, deliberately. Every edge is trimmed to its own parameter range and
    converted with `GeomConvert.CurveToBSplineCurve_s`, which is EXACT for conics -- MEASURED:
    a full circle comes back degree 2, 7 poles, rational, and a line degree 1, 2 poles. So
    there is no per-type special casing to get subtly wrong.

    THE CONVENTION TRAP, and it is the opposite of the one in `curve_recipe`: **OCCT stores
    poles EUCLIDEAN with a separate weight array, while rhino3dm stores them PREMULTIPLIED by
    W**. Going out to rhino3dm therefore has to
    MULTIPLY, where coming in had to divide. The serialized dicts here are euclidean, and
    `bridge` multiplies when it builds the `r3.NurbsCurve`.

    Knots are flattened to rhino3dm's convention (the full clamped vector minus its first and
    last) so the same `wire_from_curve`/`curve_recipe` vocabulary round-trips.
    """
    _need()
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.Geom import Geom_TrimmedCurve
    from OCP.GeomConvert import GeomConvert
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    # MEASURED, cadquery-ocp 8.0.1: there is NO `TopTools_IndexedMapOfShape` -- OCP.TopTools
    # holds only the format-version enums and a couple of helpers. The template instantiation
    # lives in OCP.collections as `IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher`, the same
    # relocation as the Array1/Array2 types.
    from OCP.collections import (
        IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape)

    # Each edge is shared by TWO faces, so a TopExp_Explorer visits it twice and emitting
    # both would DOUBLE every wireframe curve. MEASURED: the explorer returned 30 curves for
    # a 9-face union whose real edge count is 15. An ad-hoc hash of the edge was not a
    # reliable key either, so this uses OCCT's own unique-shape map, which is exactly what it
    # is for. A duplicated wireframe would quietly undermine the one tool whose job is to let
    # the user VERIFY the geometry.
    emap = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_ShapeEnum.TopAbs_EDGE, emap)
    out = []
    for idx in range(1, emap.Extent() + 1):
        edge = TopoDS.Edge(emap.FindKey(idx))
        try:
            if BRep_Tool.Degenerated_s(edge):
                continue          # a pole/seam degeneracy is not a drawable edge
            adaptor = BRepAdaptor_Curve(edge)
            first, last = adaptor.FirstParameter(), adaptor.LastParameter()
            curve = BRep_Tool.Curve_s(edge, first, last)
            if curve is None:
                continue
            bs = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(curve, first, last))
            n = bs.NbPoles()
            poles, weights = [], []
            for i in range(1, n + 1):
                q = bs.Pole(i)
                poles.append([q.X(), q.Y(), q.Z()])        # EUCLIDEAN here
                weights.append(bs.Weight(i))
            flat = []
            for i in range(1, bs.NbKnots() + 1):
                flat.extend([bs.Knot(i)] * bs.Multiplicity(i))
            # rhino3dm omits the first and last of the full clamped vector
            if len(flat) == n + bs.Degree() + 1:
                flat = flat[1:-1]
            # `index` is the edge's position in OCCT's unique-shape map, and it is the
            # HANDLE a caller uses to bevel that one edge. It must be carried explicitly:
            # degenerate edges and conversion failures are skipped above, so the position in
            # this returned list is NOT the map index, and using the list position would
            # silently bevel the wrong edge.
            out.append({"ctype": "nurbs", "degree": bs.Degree(), "poles": poles,
                        "weights": weights, "knots": flat, "periodic": False,
                        "index": idx})
        except Exception:  # noqa: BLE001
            continue
    return out


def face_summary_list(shape):
    """``[(surface_type, area)]`` per face -- what `Explode` can honestly report.

    Areas use an explicit tolerance: `BRepGProp` at its DEFAULT tolerance is ~0.2 % wrong on
    a B-spline face, and a tool for verifying geometry must
    not quote a number that is quietly wrong.
    """
    _need()
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    out = []
    exp = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        exp.Next()
        name = str(BRepAdaptor_Surface(face).GetType()).split("_")[-1].upper()
        props = GProp_GProps()
        try:
            BRepGProp.SurfaceProperties_s(face, props, 1e-9)
            out.append((name, props.Mass()))
        except Exception:  # noqa: BLE001
            out.append((name, 0.0))
    return out


def make_pipe(rail, radius):
    """A round tube of `radius` along a serialized `rail` curve. ANALYTIC.

    MEASURED, and this is far better than the skinned approximation it replaces: a circular
    profile swept along a quarter-circle rail of radius 10 comes back as **exactly one TORUS
    face**, and capped its volume is 197.392088 against an exact quarter-torus of 197.392088
    -- an error of 1.4e-14 %. `sweep.pipe`'s loft route produced a degree-3 surface that
    missed the true torus by 3.8e-05 and could never be exact, because its stations are
    samples.

    The profile circle is built PERPENDICULAR to the rail at its start, which is what makes it
    a pipe rather than an arbitrary sweep -- the same construction `sweep._perp_circle` does.
    """
    _need()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe
    from OCP.Geom import Geom_Circle
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    if radius <= 0:
        raise KernelError(f"a pipe radius must be positive (got {radius})")
    rail_wire = wire_from_curve(rail)
    try:
        # the rail's start point and tangent there define the profile's plane
        from OCP.BRepAdaptor import BRepAdaptor_CompCurve

        adaptor = BRepAdaptor_CompCurve(rail_wire)
        t0 = adaptor.FirstParameter()
        origin = adaptor.Value(t0)
        from OCP.gp import gp_Vec

        tangent = gp_Vec()
        adaptor.D1(t0, gp_Pnt(), tangent)
        if tangent.Magnitude() <= 0:
            raise KernelError("the rail has no tangent at its start")
        axis = gp_Ax2(origin, gp_Dir(tangent))
        profile = BRepBuilderAPI_MakeWire(
            BRepBuilderAPI_MakeEdge(Geom_Circle(axis, float(radius))).Edge()).Wire()
        mk = BRepOffsetAPI_MakePipe(rail_wire, profile)
        mk.Build()
        if not mk.IsDone():
            raise KernelError("the pipe could not be built along that rail")
        return mk.Shape()
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"pipe failed: {e}") from e


def cap_planar_holes(shape):
    """Close a shape's PLANAR free boundaries and return a solid. ANALYTIC.

    This is what `bridge.cap_planar_holes` could never be: that one rebuilds the WHOLE brep
    through a mesh, so capping a 1-curved-face open cylinder returned 212 facet-like faces --
    it destroyed the curved wall to add two lids. MEASURED here, a pipe along an arc caps to
    `{TORUS: 1, PLANE: 2}`: the curved surface is untouched and the lids are real planes.

    A NON-planar boundary is left open rather than closed with something approximate, exactly
    as the mesh version promised and for the same reason -- `BRepBuilderAPI_MakeFace(wire,
    onlyPlane=True)` simply declines, so the refusal is the kernel's own rather than a
    separate application-level judgement.
    """
    _need()
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeSolid,
                                    BRepBuilderAPI_Sewing)
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    try:
        bounds = ShapeAnalysis_FreeBounds(shape)
        caps = []
        left_open = 0
        exp = TopExp_Explorer(bounds.GetClosedWires(), TopAbs_ShapeEnum.TopAbs_WIRE)
        while exp.More():
            wire = TopoDS.Wire(exp.Current())
            exp.Next()
            try:
                maker = BRepBuilderAPI_MakeFace(wire, True)
                if maker.IsDone():
                    caps.append(maker.Face())
                else:
                    left_open += 1
            except Exception:  # noqa: BLE001
                left_open += 1
        if not caps:
            raise KernelError(
                f"nothing to cap: the shape has no PLANAR free boundary "
                f"({left_open} non-planar boundary/ies were left open)")
        sew = BRepBuilderAPI_Sewing(1e-6)
        sew.Add(shape)
        for face in caps:
            sew.Add(face)
        sew.Perform()
        sewed = sew.SewedShape()
        if sewed.ShapeType() != TopAbs_ShapeEnum.TopAbs_SHELL:
            raise KernelError("the capped faces did not sew into a single shell")
        solid = BRepBuilderAPI_MakeSolid(TopoDS.Shell(sewed)).Solid()
        if not is_airtight(solid):
            raise KernelError("the capped result is not airtight")
        # An OCCT sweep (MakePipeShell) capped here can come back inside-out --
        # volume -49.348 for a quarter torus of +49.348 -- yet `is_airtight` passed. A
        # solid's shell must face outward, or every boolean on it inverts. MakeSolid takes
        # the shell's orientation as given, so the sign of the volume decides.
        if volume(solid) < 0:
            solid = TopoDS.Solid(solid.Reversed())
        return solid
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"cap failed: {e}") from e


def transform(shape, matrix, *, recognise=True):
    """`shape` moved by a row-major 4x4 `matrix` (16 floats, or 4 rows of 4).

    Mirrors `bridge.to_plane`: our primitives are built canonically at the origin in world
    XY and then mapped onto the active CPlane by ONE `r3.Transform`. Rebuilding in OCCT
    therefore means building the same canonical primitive and applying the same matrix, so
    the OCCT master lands exactly where the rhino3dm solid is.

    `gp_Trsf.SetValues` takes the TOP THREE ROWS only -- it is an affine transform and the
    fourth row must be (0, 0, 0, 1). A non-affine matrix is REFUSED rather than silently
    truncated, because a silently dropped row would place the master somewhere the user
    cannot see.
    """
    _need()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    m = list(matrix)
    if len(m) == 4 and all(hasattr(r, "__len__") for r in m):
        m = [float(v) for row in m for v in row]
    m = [float(v) for v in m]
    if len(m) != 16:
        raise KernelError(f"a transform needs 16 numbers (got {len(m)})")
    last = m[12:]
    if (abs(last[0]) > 1e-12 or abs(last[1]) > 1e-12 or abs(last[2]) > 1e-12
            or abs(last[3] - 1.0) > 1e-12):
        raise KernelError(
            f"only affine transforms can be applied to a solid; this matrix's last row "
            f"is {tuple(last)}, not (0, 0, 0, 1)")
    if not is_similarity(m):
        return _general_transform(shape, m, recognise=recognise)
    trsf = gp_Trsf()
    trsf.SetValues(m[0], m[1], m[2], m[3],
                   m[4], m[5], m[6], m[7],
                   m[8], m[9], m[10], m[11])
    try:
        builder = BRepBuilderAPI_Transform(shape, trsf, True)
        builder.Build()
        if not builder.IsDone():
            raise KernelError("the transform did not complete")
        return builder.Shape()
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not transform the solid: {e}") from e


def is_similarity(m, tol=1e-12):
    """True when the 3x3 part of the row-major 4x4 `m` is a SIMILARITY -- a rotation or
    reflection times one uniform scale (its columns orthogonal and of equal length). Only
    those transforms may go through `gp_Trsf`."""
    cols = [(m[0], m[4], m[8]), (m[1], m[5], m[9]), (m[2], m[6], m[10])]
    g = [[sum(a * b for a, b in zip(cols[i], cols[j])) for j in range(3)] for i in range(3)]
    s2 = (g[0][0] + g[1][1] + g[2][2]) / 3.0
    if s2 <= 0.0:
        return False
    return all(abs(g[i][j] - (s2 if i == j else 0.0)) <= tol * s2
               for i in range(3) for j in range(3))


def _general_transform(shape, m, *, recognise=True):
    """Apply a non-similarity affine map (non-uniform scale or shear) exactly through
    `BRepBuilderAPI_GTransform` (`gp_GTrsf`).

    `gp_Trsf.SetValues` silently projects any matrix
    onto a similarity, so a (2,1,1) scale of a 40x40x10 box rebuilt as a uniform scale of
    2^(1/3) -- bbox 50.3968^2 x 12.5992 instead of 80x40x10 -- and `box MINUS box` returned a
    plausible, analytic, WRONG solid with no decline.

    `GTransform` converts every face to a B-spline (exact: a cylinder scaled (2,1,1) has its
    side on (x/6)^2 + (y/3)^2 = 1 to 1e-15 -- an ELLIPTIC cylinder, still one smooth face,
    never strips). Faces that ARE still planes, cylinders ... are then recognised back
    (`_recognise_faces`), so a scaled box stays {PLANE: 6}. A singular matrix is refused.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_GTransform
    from OCP.gp import gp_GTrsf, gp_Mat, gp_XYZ

    det = (m[0] * (m[5] * m[10] - m[6] * m[9]) - m[1] * (m[4] * m[10] - m[6] * m[8])
           + m[2] * (m[4] * m[9] - m[5] * m[8]))
    if abs(det) < 1e-15:
        raise KernelError("the transform flattens the solid (its matrix is singular) -- a "
                          "zero scale cannot be applied to a solid")
    g = gp_GTrsf()
    g.SetVectorialPart(gp_Mat(m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]))
    g.SetTranslationPart(gp_XYZ(m[3], m[7], m[11]))
    try:
        # MEASURED: a meshed circle extrusion gives InvalidPolygonOnTriangulation
        # after GTransform. Detach exact geometry without display polygons, as in
        # _nurbs_convert; never clean the shared master or discard its display cache.
        # This is OCP-handle interop: the Rust core cannot directly consume this handle.
        clean = BRepBuilderAPI_Copy(shape, True, False).Shape()
        b = BRepBuilderAPI_GTransform(clean, g, True)
        b.Build()
        if not b.IsDone():
            raise KernelError("the non-uniform transform did not complete")
        out = b.Shape()
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not apply the non-uniform transform: {e}") from e
    # MEASURED: a MIRRORING one (det < 0, scale (-2,1,1)) comes back correctly OUTWARD from
    # GTransform (volume +128 for a 4-cube), so no orientation fix-up is needed here.
    return _recognise_faces(out) if recognise else out


def _aligned_plane(ana, bs):
    """A recognised plane re-axed along the B-spline's own u direction.
    `GeomConvert_SurfToAnaSurf` picks arbitrary in-plane axes -- the x = 0 face of a box
    scaled (2,1,1) came back on a plane rotated 45 deg to its edges, so its UNTRIMMED
    surface (what the .3dm stores) was a square 1.41x larger than the face, and rhino3dm's
    bounding box read y -2..6 for a 0..4 solid. Other surface types are returned as is."""
    from OCP.Geom import Geom_Plane
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Vec

    if not isinstance(ana, Geom_Plane):
        return ana
    try:
        u0, u1, v0, v1 = bs.Bounds()
        p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
        bs.D1(u0, v0, p, du, dv)
        n = ana.Pln().Axis().Direction()
        if du.Magnitude() <= 0.0 or n.IsParallel(gp_Dir(du), 1e-9):
            return ana
        # keep the recognised normal (its orientation is what the face expects)
        return Geom_Plane(gp_Ax3(p, n, gp_Dir(du)))
    except Exception:  # noqa: BLE001
        return ana


def _recognise_faces(shape, tol=None):
    """Every B-spline face that IS a plane / cylinder / cone / sphere / torus (to `tol`)
    rebuilt as that surface on its own boundary. A face whose rebuild is not valid keeps its
    B-spline (exact either way); the result is returned only if it stays valid, else the
    input is."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepTools import BRepTools, BRepTools_ReShape
    from OCP.Geom import Geom_BSplineSurface
    from OCP.ShapeFix import ShapeFix_Shape
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopoDS import TopoDS

    tol = ANALYTIC_RECOGNITION_TOL if tol is None else tol
    rs = BRepTools_ReShape()
    changed = 0
    for f in unique_faces(shape):
        s = BRep_Tool.Surface_s(f)
        if not isinstance(s, Geom_BSplineSurface):
            continue
        ana = _recognise_surface(s, tol)
        if ana is s:
            continue
        ana = _aligned_plane(ana, s)
        try:
            mf = BRepBuilderAPI_MakeFace(ana, BRepTools.OuterWire_s(f), True)
            for w in _inner_wires(f):
                mf.Add(w)
            if not mf.IsDone():
                continue
            fx = ShapeFix_Shape(mf.Face())
            fx.Perform()
            new = TopoDS.Face(fx.Shape())
            if not BRepCheck_Analyzer(new).IsValid():
                continue
            if f.Orientation() == TopAbs_REVERSED:
                new = TopoDS.Face(new.Reversed())
            rs.Replace(f, new)
            changed += 1
        except Exception:  # noqa: BLE001
            continue
    if not changed:
        return shape
    out = rs.Apply(shape)
    fx = ShapeFix_Shape(out)
    fx.Perform()
    out = fx.Shape()
    if BRepCheck_Analyzer(out).IsValid() and solid_count(out) == solid_count(shape):
        return out
    return shape


def from_recipe(recipe, _depth=0):
    """Rebuild an OCCT solid from a `recipe` dict or TREE, exactly where the original sits.

    The recipe is ``{"kind": ..., <parameters>, "xform": [16 floats]}``, and for
    ``kind == "boolean"`` the parameters are ``op`` plus the nested recipes ``a`` and ``b``
    -- so the whole construction history rebuilds recursively. An unknown kind is refused
    BY NAME, listing what IS rebuildable, and that refusal is the feature: the alternative
    is a boolean quietly falling back to facets.
    """
    _need()
    if _depth > MAX_RECIPE_DEPTH:
        raise KernelError(
            f"recipe nested deeper than {MAX_RECIPE_DEPTH}; refusing to rebuild rather "
            f"than hang on a cyclic or runaway construction tree")
    if not isinstance(recipe, dict):
        raise KernelError(f"a recipe must be a dict (got {type(recipe).__name__})")
    kind = recipe.get("kind")
    if kind not in RECIPE_KINDS:
        raise KernelError(
            f"no OCCT recipe for {kind!r}; rebuildable kinds are "
            f"{', '.join(sorted(RECIPE_KINDS))}. A solid whose recipe is unknown is "
            f"refused rather than approximated")
    try:
        if kind == "box":
            shape = make_box(recipe["dx"], recipe["dy"], recipe["dz"])
        elif kind == "cylinder":
            shape = make_cylinder(recipe["radius"], recipe["height"])
        elif kind == "sphere":
            shape = make_sphere(recipe["radius"])
        elif kind == "cone":
            shape = make_cone(recipe["radius"], recipe["height"])
        elif kind == "extrusion":
            if any(k in recipe for k in ("both", "solid", "draft")):
                shape = make_extrusion_ex(recipe["profile"], recipe["dir"],
                                          both=bool(recipe.get("both")),
                                          solid=bool(recipe.get("solid", True)),
                                          draft_deg=recipe.get("draft"),
                                          corners=recipe.get("corners", "Sharp"),
                                          flip=bool(recipe.get("flip")))
            else:
                shape = make_extrusion(recipe["profile"], recipe["dir"])
        elif kind == "pipe":
            shape = make_pipe(recipe["rail"], recipe["radius"])
        elif kind == "cap":
            shape = cap_planar_holes(from_recipe(recipe["a"], _depth + 1))
        elif kind == "face":
            shape = face_at(from_recipe(recipe["a"], _depth + 1), recipe["index"])
        elif kind == "planar_face":
            shape = face_from_curve_loops(recipe["loops"])
        elif kind == "sweep1":
            shape = make_sweep1(recipe["rail"], recipe["section"], recipe.get("binormal"))
        elif kind == "prism":
            shape = make_prism(from_recipe(recipe["a"], _depth + 1), recipe["dir"],
                               both=bool(recipe.get("both")),
                               solid=bool(recipe.get("solid", True)))
        elif kind == "edge_surface":
            shape = make_edge_surface(recipe["curves"])
        elif kind == "revolve":
            shape = make_revolve(recipe["profile"], recipe["origin"], recipe["axis"],
                                 recipe["angle"])
        elif kind == "shell":
            shape = make_shell(from_recipe(recipe["a"], _depth + 1), recipe["remove"],
                               recipe["thickness"])
        elif kind == "thick_surface":
            shape = make_thick_surface(from_recipe(recipe["a"], _depth + 1),
                                       recipe["distance"])
        elif kind == "split_piece":
            base = unique_faces(from_recipe(recipe["a"], _depth + 1))
            if len(base) != 1:
                raise KernelError("a split piece's surface must be ONE face")
            shape = split_piece(base[0], [(c, n) for c, n in recipe["curves"]],
                                recipe["signature"])
        elif kind == "polyhedron":
            shape = make_polyhedron(recipe["vertices"], recipe["faces"])
        elif kind == "local_move":
            base = from_recipe(recipe["a"], _depth + 1)
            component = recipe["component"]
            resolved = _local_resolve_target(
                base, component, recipe["index"], recipe["target"], _local_tolerances(base)) + 1
            native_result = None
            try:
                from .. import native_geometry
                native_result = native_geometry.move_component(
                    base, lambda: shape_to_bytes(base, triangles=False),
                    {"face": 1, "edge": 2, "vertex": 3}[component], resolved,
                    recipe["vector"], live=False)
            except (ImportError, KeyError):
                native_result = None
            if native_result is not None and native_result.ok:
                shape = shape_from_bytes(native_result.data)
            elif native_result is not None and not native_result.unavailable:
                raise KernelError(native_result.detail or "native recipe local move failed")
            else:
                shape = move_subobject_local(
                    base, component, resolved, recipe["vector"], target_signature=None)
        elif kind == "torus":
            shape = make_torus(recipe["major"], recipe["minor"])
        elif kind == "truncated_cone":
            shape = make_truncated_cone(recipe["radius"], recipe["top"], recipe["height"])
        elif kind == "tube":
            shape = make_tube(recipe["outer"], recipe["inner"], recipe["height"])
        elif kind == "brep_data":
            shape = shape_from_brep_data(recipe["data"],
                                         sew_tolerance=recipe.get("tolerance", 1e-6))
        elif kind in ("fillet", "chamfer"):
            base = from_recipe(recipe["a"], _depth + 1)
            indices = recipe.get("edges")
            targets = recipe.get("edge_targets")
            if targets is not None:
                if indices is None or not isinstance(targets, list) or len(targets) != len(indices):
                    raise KernelError("a selective bevel recipe has malformed edge targets")
                tolerances = _local_tolerances(base)
                indices = [
                    _local_resolve_target(base, "edge", hint, target, tolerances) + 1
                    for hint, target in zip(indices, targets)]
            radii = recipe.get("edge_radii")
            endpoints = recipe.get("edge_endpoints")
            if radii is not None:
                if (targets is None or not isinstance(radii, list)
                        or not isinstance(endpoints, list)
                        or len(radii) != len(indices) or len(endpoints) != len(indices)
                        or len(set(indices)) != len(indices)):
                    raise KernelError("a variable bevel recipe has malformed endpoint laws")
                radii = dict(zip(indices, radii))
                endpoints = dict(zip(indices, endpoints))
            elif endpoints is not None:
                raise KernelError("a bevel recipe has endpoints but no radius laws")
            shape = bevel_edges(base, recipe["size"], indices,
                                chamfer=(kind == "chamfer"), edge_radii=radii,
                                edge_endpoints=endpoints)
        else:
            shape = boolean(from_recipe(recipe["a"], _depth + 1),
                            from_recipe(recipe["b"], _depth + 1),
                            recipe["op"])
    except KeyError as e:
        raise KernelError(
            f"the {kind!r} recipe is missing {e}; it needs {RECIPE_KINDS[kind]}") from e
    xform = recipe.get("xform")
    if xform:
        shape = transform(shape, xform)
    return shape


# --------------------------------------------------------------------------
# DISPLAY tessellation -- correct, and explicitly NOT the geometry
# --------------------------------------------------------------------------

#: Chord deflection for the display mesh, in model units. the reference CAD application calls this the render mesh
#: and draws one too; exact-geometry checks never read it. A radius-5 cylinder
#: gives 130 vertices / 124 triangles at a deflection of 0.05.)
DISPLAY_DEFLECTION = 0.02

#: Angular deflection in radians, which keeps small curved features round when chord
#: tolerance alone would under-sample a tiny fillet.
DISPLAY_ANGLE = 0.3

#: One bounded fallback for a DISPLAY mesh only. MEASURED: OCCT can return mesher status
#: 6 and no triangles for a valid planar face whose C0 NURBS boundary passes within a few
#: microns of a circular trim at the normal display settings. A coarser 0.08 / 0.3 pass
#: draws that exact face without changing its master or recipe.
DISPLAY_RETRY_SCALE = 4.0


def _tessellate_with_retry(shape, deflection, angle, want_edges, tangent_angle=None):
    """Tessellate once as requested, then retry one coarser DISPLAY mesh if OCCT emits none."""
    try:
        return _tessellate(shape, deflection, angle, want_edges, tangent_angle)
    except KernelError as first:
        if str(first) != "tessellation produced no triangles":
            raise
        retry_angle = max(float(angle), DISPLAY_ANGLE)
        return _tessellate(shape, float(deflection) * DISPLAY_RETRY_SCALE, retry_angle,
                           want_edges, tangent_angle)


def tessellate(shape, deflection=DISPLAY_DEFLECTION, angle=DISPLAY_ANGLE):
    """``(vertices, triangles)`` for DRAWING `shape`. Never the geometry.
    (See `tessellate_with_edges` for the same mesh plus where its real edges run.)"""
    verts, tris, _edges = _tessellate_with_retry(shape, deflection, angle, False)
    return verts, tris


#: Angular deflection for the edge-aware display mesh. A representative extrusion has 724
#: interior turns above 5 degrees at 0.3 radians, versus at most 200 at 0.12. Topological
#: edge filtering is separate; this finer mesh remains necessary for smooth surfaces and
#: silhouettes.
DISPLAY_EDGE_ANGLE = 0.12
def _edge_visibility(shape, tangent_angle=None):
    """OCCT edge map, visible map indices, and CAD-style category counts.

    Shaded mode keeps naked/non-manifold boundaries and creases. Smooth internal edges and
    periodic seams are separate the reference CAD application display switches and default off.
    """
    from OCP.BRep import BRep_Tool
    from OCP.GeomAbs import (GeomAbs_C1, GeomAbs_C2, GeomAbs_C3, GeomAbs_CN,
                             GeomAbs_G1, GeomAbs_G2)
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.collections import (
        IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher)

    mapping = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, mapping)
    visible, counts = set(), {"boundary": 0, "seam": 0, "tangent": 0,
                              "crease": 0, "nonmanifold": 0}
    for index in range(1, mapping.Size() + 1):
        edge = TopoDS.Edge(mapping.FindKey(index))
        faces = [TopoDS.Face(f) for f in mapping.FindFromIndex(index)]
        if len(faces) == 1:
            if BRep_Tool.IsClosed_s(edge, faces[0]):
                counts["seam"] += 1
            else:
                counts["boundary"] += 1
                visible.add(index)
        elif len(faces) == 2:
            # A periodic seam can appear twice in the ancestor list, both entries the SAME
            # face. It is not a structural edge.
            if faces[0].IsSame(faces[1]):
                counts["seam"] += 1
                continue
            # Do not infer tangency from a small angle. Intentionally shallow relief can
            # contain thousands of genuine C0 edges below one degree; an angle heuristic
            # suppresses those edges and incorrectly smooths the model. Suppress only
            # continuity that the B-Rep itself explicitly guarantees.
            try:
                continuity = BRep_Tool.Continuity_s(edge, faces[0], faces[1])
            except Exception:  # noqa: BLE001
                continuity = None
            if continuity in (GeomAbs_G1, GeomAbs_G2, GeomAbs_C1, GeomAbs_C2,
                              GeomAbs_C3, GeomAbs_CN):
                # A tangent join is still a structural boundary between two distinct
                # analytic faces. Fillet-to-plane edges remain visible in shaded CAD views;
                # only a periodic seam whose two ancestors are the SAME face is hidden.
                counts["tangent"] += 1
                visible.add(index)
            else:
                counts["crease"] += 1
                visible.add(index)
        else:
            counts["nonmanifold"] += 1
            visible.add(index)
    return mapping, visible, counts


def edge_visibility_counts(shape, tangent_angle=None):
    """Public measurement helper for the display edge categories of ``shape``."""
    return _edge_visibility(shape, tangent_angle)[2]


def tessellate_with_edges(shape, deflection=DISPLAY_DEFLECTION, angle=DISPLAY_EDGE_ANGLE,
                           tangent_angle=None):
    """``(vertices, triangles, edge_pairs)``: `tessellate` plus the vertex pairs lying on
    the shape's REAL edges -- each face's own trim boundary, seams included.

    The viewport draws structural B-Rep boundaries, including tangent joins between distinct
    faces, rather than every sharp display-triangle boundary. Same-face periodic seams stay hidden.
    Thin slivers produced by OCCT on curved walls can cross an angle threshold; in a
    representative extrusion, about 4% of the drawn mesh edges were not B-Rep edges.
    Within one face OCCT shares nodes between its triangles -- except along a
    seam, where it repeats them -- so an edge used by ONE triangle of its face is exactly
    that face's boundary: the brep edges, and nothing else."""
    return _tessellate_with_retry(shape, deflection, angle, True, tangent_angle)


def _tessellate(shape, deflection, angle, want_edges, tangent_angle=None):
    """The mesher behind `tessellate` / `tessellate_with_edges`. Never the geometry.

    Tessellation is legitimate for viewport rendering, but it must never replace the
    document geometry. The analytic shape remains the master and this mesh is only its
    display representation.

    A REVERSED face has its winding flipped, so normals come out consistent and smooth
    shading works. Without that a unioned solid shades inside-out in patches.
    """
    _need()
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_Orientation, TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    # MEASURED: `BRepMesh_IncrementalMesh` CACHES its triangulation on the shape, and a
    # second call with a different deflection silently REUSES the old one -- asking for
    # 0.002 after 0.5 returned the identical 402 vertices. For a viewport that would mean
    # zooming in never refines the display, and the shape keeping whatever density it was
    # first drawn at. `BRepTools.Clean_s` drops the cached mesh so the deflection actually
    # applies. Two successive density requests must therefore produce distinct meshes.
    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, float(deflection), False, float(angle), True)
    verts = []
    tris = []
    edges = []
    edge_map = visible_edges = None
    if want_edges:
        edge_map, visible_edges, _counts = _edge_visibility(shape, tangent_angle)
    exp = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            exp.Next()
            continue
        trsf = loc.Transformation()
        base = len(verts)
        # Performance only: an identity location moves nothing, so its `Transformed` call
        # is skipped for each node; the resulting values are unchanged.
        if loc.IsIdentity():
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i)
                verts.append((p.X(), p.Y(), p.Z()))
        else:
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                verts.append((p.X(), p.Y(), p.Z()))
        flipped = (face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED)
        tri_list = [tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]
        zipped = _ruled_strip(face, tri, tri_list, verts, base) if want_edges else None
        if zipped is not None:
            tri_list = zipped
        for a, b, c in tri_list:
            if flipped:
                a, c = c, a
            tris.append((base + a - 1, base + b - 1, base + c - 1))
        if want_edges:
            # Ask OCCT which triangulation nodes represent each TOPOLOGICAL edge. This is
            # exact and avoids mistaking arbitrary triangle boundaries for Brep edges.
            seen = set()
            edge_exp = TopExp_Explorer(face, TopAbs_ShapeEnum.TopAbs_EDGE)
            while edge_exp.More():
                edge = TopoDS.Edge(edge_exp.Current())
                map_index = edge_map.FindIndex(edge)
                if map_index in visible_edges and map_index not in seen:
                    seen.add(map_index)
                    polygon = BRep_Tool.PolygonOnTriangulation_s(edge, tri, loc)
                    if polygon is not None:
                        nodes = list(polygon.Nodes())
                        edges.extend((base + a - 1, base + b - 1)
                                     for a, b in zip(nodes, nodes[1:]))
                edge_exp.Next()
        exp.Next()
    if not tris:
        raise KernelError("tessellation produced no triangles")
    return verts, tris, edges


#: Relative tolerance for deciding whether a node lies on a boundary row in `_ruled_strip`.
RULED_ROW_TOL = 1e-7


def _ruled_direction(face):
    """0 / 1 when `face`'s surface is a straight-line RULING in u / v -- an extrusion wall:
    degree 1 with two poles that way, or a surface of linear extrusion (ruled in v) --
    else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_BSplineSurface, GeomAbs_SurfaceOfExtrusion

    s = BRepAdaptor_Surface(face, True)
    kind = s.GetType()
    if kind == GeomAbs_SurfaceOfExtrusion:
        return 1
    if kind == GeomAbs_BSplineSurface:
        bs = s.BSpline()
        if bs.VDegree() == 1 and bs.NbVPoles() == 2:
            return 1
        if bs.UDegree() == 1 and bs.NbUPoles() == 2:
            return 0
    return None


def _ruled_strip(face, tri, tri_list, verts, base):
    """Re-triangulate an extrusion WALL as a strip of rungs, or None to keep OCCT's.

    OCCT can mesh a thin ruled wall with skewed fans, joining one node on the bottom to
    several on the top. Neighbouring triangles then crease and the viewport draws false
    lines across the wall. In a representative model, 82 of 153 walls also contained
    interior nodes and exhibited the artifact. A wall that is straight along its rulings
    is exactly the ruled surface
    between its two boundary rows, so the row nodes alone are zipped in parameter order
    (interior nodes are dropped): each triangle spans one rung, the strip follows the
    rulings and creases only where the profile turns. The row nodes are OCCT's own, shared
    with the caps, so the wall still meets them exactly. Display only."""
    try:
        d = _ruled_direction(face)
        if d is None or not tri.HasUVNodes():
            return None
        uv = [tri.UVNode(i) for i in range(1, tri.NbNodes() + 1)]
        along = [(p.X(), p.Y())[1 - d] for p in uv]   # parameter ALONG the profile
        across = [(p.X(), p.Y())[d] for p in uv]      # parameter along the ruling
        lo, hi = min(across), max(across)
        span = hi - lo
        if span <= 0:
            return None
        tol = RULED_ROW_TOL * max(1.0, abs(lo), abs(hi)) + 1e-9 * span
        row0 = [i for i in range(len(uv)) if abs(across[i] - lo) <= tol]
        row1 = [i for i in range(len(uv)) if abs(across[i] - hi) <= tol]
        if len(row0) < 2 or len(row1) < 2:
            return None                     # not a two-row wall (trimmed): keep OCCT's
        row0.sort(key=lambda i: along[i])
        row1.sort(key=lambda i: along[i])
        a0, a1 = along[row0[0]], along[row0[-1]]
        b0, b1 = along[row1[0]], along[row1[-1]]
        if max(abs(a0 - b0), abs(a1 - b1)) > 1e-6 * max(1.0, abs(a1 - a0)):
            return None                     # the rows do not span the same profile range
        out = []
        i = j = 0
        while i < len(row0) - 1 or j < len(row1) - 1:
            if j >= len(row1) - 1 or (i < len(row0) - 1
                                      and along[row0[i + 1]] <= along[row1[j + 1]]):
                out.append((row0[i] + 1, row0[i + 1] + 1, row1[j] + 1))
                i += 1
            else:
                out.append((row1[j] + 1, row0[i] + 1, row1[j + 1] + 1))
                j += 1

        def normal_sum(ts):
            # Use the same arithmetic and order as the generator form it replaces; avoiding
            # generator overhead saved approximately three seconds on a large model.
            sx = sy = sz = 0.0
            off = base - 1
            for a, b, c in ts:
                pa = verts[off + a]
                pb = verts[off + b]
                pc = verts[off + c]
                ux = pb[0] - pa[0]
                uy = pb[1] - pa[1]
                uz = pb[2] - pa[2]
                vx = pc[0] - pa[0]
                vy = pc[1] - pa[1]
                vz = pc[2] - pa[2]
                sx += uy * vz - uz * vy
                sy += uz * vx - ux * vz
                sz += ux * vy - uy * vx
            return sx, sy, sz

        n_old, n_new = normal_sum(tri_list), normal_sum(out)
        if sum(p * q for p, q in zip(n_old, n_new)) < 0:
            out = [(a, c, b) for a, b, c in out]    # keep OCCT's winding (face side)
        return out
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# Analytic boolean operations
# --------------------------------------------------------------------------

#: The three operations use the same names as the command layer. `INTERSECTION`, rather
#: than Blender's `INTERSECT`, is intentionally the backend operation name.
OPERATIONS = ("UNION", "DIFFERENCE", "INTERSECTION")


def unify(shape):
    """Merge faces and edges lying on the SAME underlying surface. Returns a new shape.

    OCCT booleans do not automatically merge faces lying on the same surface. Without this
    step, unioning two extruded circles leaves the flat top as three coplanar faces with
    spurious seams, whereas the reference CAD application merges them.

    The display mesh was measured FIRST and was innocent (336 welded triangles, area 194.38
    against the master's 194.73 -- 0.2 % chord error, no doubling), so this was a TOPOLOGY
    problem, not a shading one. What gave it away was `edge_curves`: the top rim's arcs summed
    to exactly TWO circumferences, so the internal crossing edges were still present.

    MEASURED. The column that matters is the volume: this is purely topological and must not
    move a single point.

        union of two extruded circles  9 faces {PLANE:6,CYLINDER:3} 15 edges vol 200.241262510
                            unified    4 faces {PLANE:2,CYLINDER:2}  6 edges vol 200.241262510
        box UNION cylinder            10 {PLANE:9,CYLINDER:1} -> 8 {PLANE:7,CYLINDER:1}
        box MINUS cylinder             7 -> 7   (nothing to merge)
        box INTERSECT sphere           4 -> 4
        box with 12 edges filleted    26 -> 26  (a real fillet is NOT degraded)

    Every volume matched to 9 decimal places and every result stayed airtight. The filleted
    box is the case that proves this cannot over-merge: its 12 cylindrical and 8 spherical
    faces are distinct surfaces, and all 26 survive.
    """
    _need()
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    try:
        # (shape, unify EDGES, unify FACES, concatenate B-splines)
        tool = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
        tool.Build()
        out = tool.Shape()
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"could not unify same-domain faces: {e}") from e
    if out is None:
        raise KernelError("unification produced nothing")
    return out


def boolean(shape_a, shape_b, operation):
    """`shape_a` OP `shape_b`, keeping analytic faces. Raises KernelError on failure.

    `BRepAlgoAPI_*` requires an explicit `Build()`/`IsDone()` check before `Shape()`. A
    boolean that silently returns an empty shape must not reach the caller.
    """
    _need()
    if operation not in OPERATIONS:
        raise KernelError(
            f"unknown boolean {operation!r}; expected one of {OPERATIONS}")
    from OCP.BRepAlgoAPI import (BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut,
                                 BRepAlgoAPI_Common)

    builder_for = {"UNION": BRepAlgoAPI_Fuse,
                   "DIFFERENCE": BRepAlgoAPI_Cut,
                   "INTERSECTION": BRepAlgoAPI_Common}
    try:
        builder = builder_for[operation](shape_a, shape_b)
        builder.Build()
        if not builder.IsDone():
            raise KernelError(f"the {operation.lower()} could not be computed")
        result = builder.Shape()
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"{operation.lower()} failed: {e}") from e
    if solid_count(result) < 1:
        raise KernelError(
            f"the {operation.lower()} produced no solid -- the inputs may not "
            f"overlap")
    # MERGE same-surface faces before handing the result back. OCCT leaves a boolean's
    # coplanar pieces split, which the user SEES as seams on a flat top (see `unify`). A
    # unification failure must not lose the boolean, so the raw result is kept on error --
    # worse topology is still correct geometry, and silently returning nothing would not be.
    try:
        unified = unify(result)
        return unified if solid_count(unified) >= 1 else result
    except KernelError:
        return result


# --------------------------------------------------------------------------
# Airtightness -- asserted, not assumed
# --------------------------------------------------------------------------


def is_valid(shape):
    """The kernel's own verdict on the shape's topology/geometry.

    This is what replaces hoping. `BRepCheck_Analyzer` is OCCT's full checker; a solid
    that fails it is not manufacturable and must not be exported.
    """
    _need()
    from OCP.BRepCheck import BRepCheck_Analyzer

    try:
        return bool(BRepCheck_Analyzer(shape).IsValid())
    except Exception:  # noqa: BLE001
        return False


def _count(shape, kind):
    from OCP.TopExp import TopExp_Explorer

    exp = TopExp_Explorer(shape, kind)
    n = 0
    while exp.More():
        n += 1
        exp.Next()
    return n


def solid_count(shape):
    """How many solids `shape` contains."""
    _need()
    from OCP.TopAbs import TopAbs_SOLID

    return _count(shape, TopAbs_SOLID)


def face_count(shape):
    _need()
    from OCP.TopAbs import TopAbs_FACE

    return _count(shape, TopAbs_FACE)


def is_airtight(shape):
    """True when `shape` is a single valid, closed solid.

    \"Airtight\" means three separate things and all three are checked: the topology is
    VALID, there is exactly one SOLID, and its shell is CLOSED. A shape can pass one and
    fail another -- a shell with a missing face is valid geometry and not a solid.
    """
    _need()
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier  # noqa: F401
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.BRep import BRep_Tool  # noqa: F401

    if not is_valid(shape) or solid_count(shape) != 1:
        return False
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        try:
            if not TopoDS.Shell(exp.Current()).Closed():
                # Closed() can be unset on a shape built by some operations; fall back
                # to the solid classifier rather than reporting a false negative.
                from OCP.BRepCheck import BRepCheck_Shell

                if BRepCheck_Shell(TopoDS.Shell(exp.Current())).Closed() != 0:
                    return False
        except Exception:  # noqa: BLE001
            return False
        exp.Next()
    return True


def volume(shape):
    """Exact volume by integration -- not the sampled estimate `analysis.py` uses.

    Uses an explicit integration epsilon. Without one, a capped cone frustum (R5, r3, h6)
    measured
    307.9493 against the closed form 307.8761 -- 2.4e-4 off; at eps 1e-13 it is exact to
    the last digit (1e-9 leaves 4e-14).
    """
    _need()
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props, 1e-13)
    return props.Mass()


def face_types(shape):
    """``{surface type name: count}`` -- the proof that faces stayed analytic.

    This is what a gate asserts instead of trusting a tag: a drilled box must report a
    CYLINDER, not 400 planes.
    """
    _need()
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Sphere,
                             GeomAbs_Cone, GeomAbs_Torus,
                             GeomAbs_BSplineSurface, GeomAbs_BezierSurface,
                             GeomAbs_SurfaceOfRevolution,
                             GeomAbs_SurfaceOfExtrusion)
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    names = {GeomAbs_Plane: "PLANE", GeomAbs_Cylinder: "CYLINDER",
             GeomAbs_Sphere: "SPHERE", GeomAbs_Cone: "CONE",
             GeomAbs_Torus: "TORUS", GeomAbs_BSplineSurface: "BSPLINE",
             GeomAbs_BezierSurface: "BEZIER",
             GeomAbs_SurfaceOfRevolution: "REVOLUTION",
             GeomAbs_SurfaceOfExtrusion: "EXTRUSION"}
    out = {}
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        t = BRepAdaptor_Surface(TopoDS.Face(exp.Current())).GetType()
        key = names.get(t, f"other({t})")
        out[key] = out.get(key, 0) + 1
        exp.Next()
    return out


ANALYTIC_FACE_TYPES = ("PLANE", "CYLINDER", "SPHERE", "CONE", "TORUS",
                       "REVOLUTION", "EXTRUSION", "BSPLINE", "BEZIER")


# --------------------------------------------------------------------------
# Analytic fillet, replacing the mesh bevel fallback
# --------------------------------------------------------------------------

def _unique_edges(shape):
    """Every edge of `shape`, ONCE. (A `TopExp_Explorer` visits each one twice.)"""
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.collections import (
        IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as _Map)

    emap = _Map()
    TopExp.MapShapes_s(shape, TopAbs_ShapeEnum.TopAbs_EDGE, emap)
    return [TopoDS.Edge(emap.FindKey(i)) for i in range(1, emap.Extent() + 1)]


def unique_faces(shape):
    """Every face of `shape`, ONCE, in OCCT's stable unique-shape map order (1-based index).

    The index is what a `face` recipe stores, so it must be reproducible: the same
    construction rebuilt by the same kernel yields the same map order. A `TopExp_Explorer`
    would also work for faces (unlike edges, a face is not shared by two parents within one
    solid), but using the same map as `_unique_edges` keeps one convention for "index".
    """
    _need()
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.collections import (
        IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as _Map)

    fmap = _Map()
    TopExp.MapShapes_s(shape, TopAbs_ShapeEnum.TopAbs_FACE, fmap)
    return [TopoDS.Face(fmap.FindKey(i)) for i in range(1, fmap.Extent() + 1)]


def face_at(shape, index):
    """The `index`-th (1-based) unique face of `shape`. Refused by name when out of range."""
    faces = unique_faces(shape)
    i = int(index)
    if i < 1 or i > len(faces):
        raise KernelError(
            f"face index {i} out of range; this shape has {len(faces)} faces numbered "
            f"1..{len(faces)}")
    return faces[i - 1]


def _unique_vertices(shape):
    """Every vertex of `shape`, ONCE, in the unique-shape map order (1-based index) -- the
    same convention as `unique_faces` / `_unique_edges`."""
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.collections import (
        IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as _Map)

    vmap = _Map()
    TopExp.MapShapes_s(shape, TopAbs_ShapeEnum.TopAbs_VERTEX, vmap)
    return [TopoDS.Vertex(vmap.FindKey(i)) for i in range(1, vmap.Extent() + 1)]


def _newell(points):
    """The (unnormalised) Newell normal of a polygon -- its area vector, times two."""
    nx = ny = nz = 0.0
    n = len(points)
    for i in range(n):
        x0, y0, z0 = points[i]
        x1, y1, z1 = points[(i + 1) % n]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    return nx, ny, nz


def planar_polyhedron(shape):
    """Return `shape` as ``(vertices, faces, edges)``, or raise KernelError.

    * ``vertices`` -- ``[(x, y, z)]`` in `_unique_vertices` order, so vertex ``i + 1``
      has identifier ``i + 1``;
    * ``faces`` -- in `unique_faces` order (face id = position + 1), each a list of LOOPS of
      vertex ids: the outer loop first, wound so its Newell normal points OUT of the solid,
      then any holes;
    * ``edges`` -- ``[(vertex id, vertex id)]`` in `_unique_edges` order (edge id).

    Refused by name, rather than approximated: a shape that is not one closed solid, any
    face that is not a PLANE, and any edge that is not a straight LINE. Those are what this
    representation cannot hold exactly.
    """
    _need()
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.collections import (
        IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as _Map)

    if not is_airtight(shape):
        raise KernelError("the object is not one closed solid")
    vmap = _Map()
    TopExp.MapShapes_s(shape, TopAbs_ShapeEnum.TopAbs_VERTEX, vmap)
    vertices = []
    for i in range(1, vmap.Extent() + 1):
        p = BRep_Tool.Pnt_s(TopoDS.Vertex(vmap.FindKey(i)))
        vertices.append((p.X(), p.Y(), p.Z()))
    edges = []
    for i, e in enumerate(_unique_edges(shape), start=1):
        if BRepAdaptor_Curve(e).GetType() != GeomAbs_Line:
            raise KernelError(f"edge {i} is CURVED")
        a, b = TopExp.FirstVertex_s(e), TopExp.LastVertex_s(e)
        edges.append((vmap.FindIndex(a), vmap.FindIndex(b)))

    def loop_of(wire, face):
        out = []
        wex = BRepTools_WireExplorer(wire, face)
        while wex.More():
            out.append(vmap.FindIndex(wex.CurrentVertex()))
            wex.Next()
        return out

    faces = []
    for i, f in enumerate(unique_faces(shape), start=1):
        plane = face_plane(f)
        if plane is None:
            raise KernelError(f"face {i} is CURVED ({_face_type_name(f)})")
        outer_wire = BRepTools.OuterWire_s(f)
        outer = loop_of(outer_wire, f)
        if len(outer) < 3:
            raise KernelError(f"face {i} has fewer than three corners")
        # MEASURED: `BRepTools_WireExplorer(wire, face)` already walks the outer loop
        # OUTWARD (counter-clockwise seen from outside) on plain, rotated, mirrored and
        # boolean solids alike, once `face_plane` reports the true outward normal. No
        # re-winding is needed.
        loops = [outer]
        exp = TopExp_Explorer(f, TopAbs_ShapeEnum.TopAbs_WIRE)
        while exp.More():
            w = TopoDS.Wire(exp.Current())
            exp.Next()
            if not w.IsSame(outer_wire):
                loops.append(loop_of(w, f))
        faces.append(loops)
    return vertices, faces, edges


def _face_type_name(face):
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    try:
        return str(BRepAdaptor_Surface(face).GetType()).split("_")[-1].upper()
    except Exception:  # noqa: BLE001
        return "not a plane"


def make_polyhedron(vertices, faces):
    """Build a closed, valid solid of planar polygon faces (`planar_polyhedron`'s form:
    each face a list of loops of 1-based vertex indices, outer first, then holes).

    Each face is a real PLANE bounded by straight edges through its own corners. The faces
    are sewn into one shell, and the solid is turned outward if it came back inside-out
    (the `cap_planar_holes` lesson). Refused by name if a face is not planar, the result
    is not airtight, or `BRepAlgoAPI_Check` finds it self-intersecting.
    """
    _need()
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Check
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon,
                                    BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing)
    from OCP.ShapeFix import ShapeFix_Face
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt

    pts = [gp_Pnt(*[float(c) for c in v]) for v in vertices]

    def wire(loop, i):
        poly = BRepBuilderAPI_MakePolygon()
        for j in loop:
            poly.Add(pts[int(j) - 1])
        poly.Close()
        if not poly.IsDone():
            raise KernelError(f"face {i}'s corners do not make a polygon")
        return poly.Wire()

    sew = BRepBuilderAPI_Sewing(1e-7)
    for i, loops in enumerate(faces, start=1):
        mk = BRepBuilderAPI_MakeFace(wire(loops[0], i), True)
        if not mk.IsDone():
            raise KernelError(f"face {i}'s corners are not in one plane")
        for hole in loops[1:]:
            mk.Add(wire(hole, i))
        face = mk.Face()
        if len(loops) > 1:
            fix = ShapeFix_Face(face)          # a hole must run opposite to its outer loop
            fix.FixOrientation()
            fix.Perform()
            face = fix.Face()
        sew.Add(face)
    sew.Perform()
    sewed = sew.SewedShape()
    if sewed.ShapeType() != TopAbs_ShapeEnum.TopAbs_SHELL:
        raise KernelError("the faces did not sew into a single shell")
    solid = BRepBuilderAPI_MakeSolid(TopoDS.Shell(sewed)).Solid()
    if not is_airtight(solid):
        raise KernelError("the faces do not close into a solid")
    if volume(solid) < 0:
        solid = TopoDS.Solid(solid.Reversed())
    if not BRepAlgoAPI_Check(solid).IsValid():
        raise KernelError("the result intersects itself")
    return solid


def _sample_edge(edge, deflection, angle):
    """World points along `edge`'s EXACT curve, chord error <= `deflection`. A straight edge
    is its two ends; a circle is as many points as the tolerance needs. For hit-testing and
    highlighting only -- the edge itself is `edge_at`."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_TangentialDeflection

    a = BRepAdaptor_Curve(edge)
    s = GCPnts_TangentialDeflection(a, float(angle), float(deflection))
    pts = []
    for i in range(1, s.NbPoints() + 1):
        p = s.Value(i)
        pts.append((p.X(), p.Y(), p.Z()))
    return pts


def subobject_topology(shape, deflection=DISPLAY_DEFLECTION, angle=DISPLAY_ANGLE):
    """Return the pickable parts of `shape`, keyed by stable IDs.

    Returns ``{"faces": [(id, [(p, q, r), ...])], "edges": [(id, [p, ...])],
    "vertices": [(id, p)]}`` with every point an ``(x, y, z)`` tuple in world space.

    An id is the 1-based index in OCCT's unique-shape map (`unique_faces`, `_unique_edges`,
    `_unique_vertices`), so it is what a ``face`` recipe stores and what `face_at` /
    `edge_at` / `vertex_at` take back. The same construction rebuilt by the same kernel gives
    the same map, so the IDs survive a save and reload.

    The face triangles are the DISPLAY tessellation (legitimate: they are for hit-testing,
    never the geometry). Edge polylines are samples of the exact edge curves. A DEGENERATED
    edge (a cone's apex, a sphere's pole) has no curve to pick and is left out; its id is
    simply absent, so the other ids do not shift.
    """
    _need()
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_Orientation
    from OCP.TopLoc import TopLoc_Location

    # the mesh is cached on the shape and `tessellate` cleans it before every use, so this
    # cannot leave the display at the wrong density
    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, float(deflection), False, float(angle), True)
    faces = []
    for index, face in enumerate(unique_faces(shape), start=1):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        tris = []
        if tri is not None:
            trsf = loc.Transformation()
            nodes = []
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                nodes.append((p.X(), p.Y(), p.Z()))
            flipped = face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(i).Get()
                if flipped:
                    a, c = c, a
                tris.append((nodes[a - 1], nodes[b - 1], nodes[c - 1]))
        faces.append((index, tris))
    edges = []
    for index, edge in enumerate(_unique_edges(shape), start=1):
        if BRep_Tool.Degenerated_s(edge):
            continue
        edges.append((index, _sample_edge(edge, deflection, angle)))
    vertices = []
    for index, v in enumerate(_unique_vertices(shape), start=1):
        p = BRep_Tool.Pnt_s(v)
        vertices.append((index, (p.X(), p.Y(), p.Z())))
    return {"faces": faces, "edges": edges, "vertices": vertices}


def edge_at(shape, index):
    """Return the `index`-th (1-based) unique edge as an exact serialized curve
    (`_edge_recipe`: a line stays a ``line``, a circle an ``arc``, else NURBS). Refused by
    name when out of range or degenerated (a point, not a curve)."""
    _need()
    from OCP.BRep import BRep_Tool

    edges = _unique_edges(shape)
    i = int(index)
    if i < 1 or i > len(edges):
        raise KernelError(
            f"edge index {i} out of range; this shape has {len(edges)} edges numbered "
            f"1..{len(edges)}")
    if BRep_Tool.Degenerated_s(edges[i - 1]):
        raise KernelError(f"edge {i} is degenerated (a singular point, not a curve)")
    return _edge_recipe(edges[i - 1])


def vertex_at(shape, index):
    """Return the `index`-th (1-based) unique vertex as ``(x, y, z)``."""
    _need()
    from OCP.BRep import BRep_Tool

    verts = _unique_vertices(shape)
    i = int(index)
    if i < 1 or i > len(verts):
        raise KernelError(
            f"vertex index {i} out of range; this shape has {len(verts)} vertices numbered "
            f"1..{len(verts)}")
    p = BRep_Tool.Pnt_s(verts[i - 1])
    return (p.X(), p.Y(), p.Z())


def _local_shape_index(shapes, query):
    return next((i for i, shape in enumerate(shapes) if shape.IsSame(query)), None)


def _local_point(vertex):
    from OCP.BRep import BRep_Tool

    point = BRep_Tool.Pnt_s(vertex)
    return point.X(), point.Y(), point.Z()


def _local_distance(a, b):
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def _local_add(point, vector):
    return tuple(float(point[i]) + float(vector[i]) for i in range(3))


def _local_translation(vector):
    from OCP.gp import gp_Trsf, gp_Vec

    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(*vector))
    return transform


def _local_tolerances(shape):
    """Build/sew and comparison tolerances derived from OCCT and this shape.

    OCCT's 1e-7 geometric confusion and the shape's own stored tolerances govern building.
    Comparison is deliberately tighter: parametric confusion, coordinate ULPs, and at most
    one eighth of an input tolerance. The extent terms keep both values meaningful from
    millimetre-scale micro-parts through kilometre coordinates without permitting an
    already-loose input tolerance to hide a wrong selected component.
    """
    from OCP.BRep import BRep_Tool
    from OCP.Precision import Precision
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX

    low, high = tight_bbox(shape)
    extent = max(max(high[i] - low[i] for i in range(3)), Precision.Confusion_s())
    coordinate = max(1.0, *(abs(value) for value in low + high))
    ulp = math.ulp(coordinate)
    input_tolerance = max(
        BRep_Tool.MaxTolerance_s(shape, TopAbs_VERTEX),
        BRep_Tool.MaxTolerance_s(shape, TopAbs_EDGE),
        BRep_Tool.MaxTolerance_s(shape, TopAbs_FACE),
    )
    loose_limit = max(1000.0 * Precision.Confusion_s(), extent * 1e-4)
    if input_tolerance > loose_limit:
        raise KernelError(
            f"local move refused: input tolerance {input_tolerance:g} is too large for "
            f"shape extent {extent:g}")
    comparison = max(
        Precision.PConfusion_s(), 64.0 * ulp,
        min(input_tolerance / 8.0, extent * 1e-10),
    )
    build = max(Precision.Confusion_s(), input_tolerance, 8.0 * comparison)
    sew = min(4.0 * build, loose_limit)
    return {"build": build, "sew": sew, "compare": comparison,
            "extent": extent, "input": input_tolerance, "ulp": ulp}


def _local_edge_vertices(edge, vertices):
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    result = []
    explorer = TopExp_Explorer(edge, TopAbs_VERTEX)
    while explorer.More():
        index = _local_shape_index(vertices, TopoDS.Vertex(explorer.Current()))
        if index is None:
            raise KernelError("local move refused: an edge vertex is outside the solid")
        if index not in result:
            result.append(index)
        explorer.Next()
    return result


def _local_face_edges(face, edges):
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    result = []
    explorer = TopExp_Explorer(face, TopAbs_EDGE)
    while explorer.More():
        index = _local_shape_index(edges, TopoDS.Edge(explorer.Current()))
        if index is None:
            raise KernelError("local move refused: a face edge is outside the solid")
        if index not in result:
            result.append(index)
        explorer.Next()
    return result


def _local_curve_type(edge):
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.Geom import Geom_TrimmedCurve
    from OCP.GeomAbs import GeomAbs_BSplineCurve, GeomAbs_Circle, GeomAbs_Ellipse, GeomAbs_Line
    from OCP.GeomConvert import GeomConvert_CurveToAnaCurve
    from OCP.Precision import Precision

    if BRep_Tool.Degenerated_s(edge):
        return "DEGENERATED"
    adaptor = BRepAdaptor_Curve(edge)
    curve_type = adaptor.GetType()
    if curve_type != GeomAbs_BSplineCurve:
        return str(curve_type).split("_")[-1].upper()
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    curve = BRep_Tool.Curve_s(edge, first, last)
    if curve is None:
        return "BSPLINECURVE"
    trimmed = Geom_TrimmedCurve(curve, first, last)
    # Curved targets precede LINE: OCCT's converter reports a misleading tiny LINE gap
    # for a rational quarter-circle when output parameters are Python value arguments.
    for target, name in ((GeomAbs_Circle, "CIRCLE"), (GeomAbs_Ellipse, "ELLIPSE"),
                         (GeomAbs_Line, "LINE")):
        converter = GeomConvert_CurveToAnaCurve(trimmed)
        converter.SetTarget(target)
        try:
            converted = converter.ConvertToAnalytical(
                Precision.PConfusion_s(), trimmed, first, last, first, last)
        except Exception:  # noqa: BLE001
            converted = False
        if converted and converter.Gap() <= Precision.PConfusion_s():
            return name
    return "BSPLINECURVE"


def _local_part_type(part, kind):
    if kind == "face":
        return _face_type_name(part)
    if kind == "edge":
        return _local_curve_type(part)
    return "VERTEX"


def _local_signature_adjacency(shape, kind, part):
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    faces, edges = unique_faces(shape), _unique_edges(shape)
    if kind == "face":
        edge_types = []
        degenerated = 0
        explorer = TopExp_Explorer(part, TopAbs_EDGE)
        seen = []
        while explorer.More():
            edge = TopoDS.Edge(explorer.Current())
            if not any(old.IsSame(edge) for old in seen):
                seen.append(edge)
                if BRep_Tool.Degenerated_s(edge):
                    degenerated += 1
                else:
                    edge_types.append(_local_curve_type(edge))
            explorer.Next()
        return {"edge_types": sorted(edge_types), "real_boundaries": len(edge_types),
                "degenerated": degenerated}
    if kind == "edge":
        adjacent_faces = []
        for face in faces:
            explorer = TopExp_Explorer(face, TopAbs_EDGE)
            while explorer.More():
                if TopoDS.Edge(explorer.Current()).IsSame(part):
                    adjacent_faces.append(_face_type_name(face))
                    break
                explorer.Next()
        vertices = _unique_vertices(shape)
        endpoints = _local_edge_vertices(part, vertices)
        valences = []
        for endpoint in endpoints:
            valences.append(sum(endpoint in _local_edge_vertices(edge, vertices)
                                for edge in edges))
        return {"face_types": sorted(adjacent_faces), "endpoint_valences": sorted(valences)}

    vertex_index = _local_shape_index(_unique_vertices(shape), part)
    edge_types = []
    face_types_ = []
    if vertex_index is None:
        raise KernelError("local move target signature: vertex is outside the parent")
    vertices = _unique_vertices(shape)
    incident_edges = [edge for edge in edges
                      if vertex_index in _local_edge_vertices(edge, vertices)]
    edge_types = sorted(_local_curve_type(edge) for edge in incident_edges)
    for face in faces:
        explorer = TopExp_Explorer(face, TopAbs_EDGE)
        found = False
        while explorer.More() and not found:
            edge = TopoDS.Edge(explorer.Current())
            found = any(edge.IsSame(incident) for incident in incident_edges)
            explorer.Next()
        if found:
            face_types_.append(_face_type_name(face))
    return {"edge_types": edge_types, "face_types": sorted(face_types_),
            "edge_valence": len(incident_edges)}


def local_subobject_signature(shape, kind, index):
    """Return a JSON-safe geometric/topological identity for a local-move target."""
    _need()
    if kind not in ("face", "edge", "vertex"):
        raise KernelError(f"local move target signature: unsupported kind {kind!r}")
    parts = {"face": unique_faces(shape), "edge": _unique_edges(shape),
             "vertex": _unique_vertices(shape)}[kind]
    position = int(index) - 1
    if position < 0 or position >= len(parts):
        raise KernelError(
            f"local move target signature: {kind} index {index} is outside 1..{len(parts)}")
    part = parts[position]
    if kind == "vertex":
        centroid = _local_point(part)
        measure = 0.0
        bounds = centroid, centroid
    else:
        measure, centroid = _local_part_measure(part, kind)
        bounds = tight_bbox(part)
    return {
        "version": 1,
        "component": kind,
        "type": _local_part_type(part, kind),
        "measure": float(measure),
        "centroid": [float(value) for value in centroid],
        "bounds": [[float(value) for value in corner] for corner in bounds],
        "adjacency": _local_signature_adjacency(shape, kind, part),
    }


def _local_signature_matches(candidate, target, tolerances):
    if not isinstance(target, dict) or target.get("version") != 1:
        return False
    required = ("component", "type", "measure", "centroid", "bounds", "adjacency")
    if any(name not in target for name in required):
        return False
    if candidate["component"] != target["component"] or candidate["type"] != target["type"]:
        return False
    if candidate["adjacency"] != target["adjacency"]:
        return False
    try:
        measure = float(target["measure"])
        centroid = [float(value) for value in target["centroid"]]
        bounds = [[float(value) for value in corner] for corner in target["bounds"]]
    except (TypeError, ValueError):
        return False
    values = [measure, *centroid, *(value for corner in bounds for value in corner)]
    if len(centroid) != 3 or len(bounds) != 2 or any(len(corner) != 3 for corner in bounds):
        return False
    if not all(math.isfinite(value) for value in values):
        return False
    mass_tolerance = 2.5e-7 * max(tolerances["compare"], abs(measure))
    if abs(candidate["measure"] - measure) > mass_tolerance:
        return False
    comparison = tolerances["compare"]
    if any(abs(candidate["centroid"][i] - centroid[i]) > comparison for i in range(3)):
        return False
    return all(abs(candidate["bounds"][side][axis] - bounds[side][axis]) <= comparison
               for side in range(2) for axis in range(3))


def _local_resolve_target(shape, kind, index, target, tolerances):
    if not isinstance(target, dict) or target.get("component") != kind:
        raise KernelError("local move refused: persisted target signature is missing or malformed")
    parts = {"face": unique_faces(shape), "edge": _unique_edges(shape),
             "vertex": _unique_vertices(shape)}[kind]
    matches = []
    for position in range(1, len(parts) + 1):
        candidate = local_subobject_signature(shape, kind, position)
        if _local_signature_matches(candidate, target, tolerances):
            matches.append(position - 1)
    if not matches and kind == "vertex":
        # A vertex's position is fixed by the recorded moves alone, while the TYPES of the
        # curves and surfaces around it depend on how the kernel rebuilt them. MEASURED
        # (bug #109): a file saved before a rebuild-strategy change replayed its first edit
        # with different neighbouring curve types, so later steps resolved to 0 vertices
        # and the whole solid lost its exact geometry on reload. An exact, unique position
        # match identifies the same vertex; anything ambiguous is still refused.
        try:
            point = [float(value) for value in target["centroid"]]
        except (KeyError, TypeError, ValueError):
            point = None
        if point is not None and len(point) == 3:
            matches = [position for position, vertex in enumerate(parts)
                       if _local_distance(_local_point(vertex), point) <= tolerances["compare"]]
    if len(matches) != 1:
        raise KernelError(
            f"local move refused: persisted target signature resolved to {len(matches)} "
            f"{kind}s instead of one (stored index hint {index})")
    return matches[0]


def _local_orient_edge(edge, start, tolerance):
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS

    first = _local_point(TopExp.FirstVertex_s(edge, True))
    last = _local_point(TopExp.LastVertex_s(edge, True))
    if min(_local_distance(first, start), _local_distance(last, start)) > tolerance:
        raise KernelError("local move refused: a rebuilt edge does not meet its wire vertex")
    return TopoDS.Edge(edge.Reversed()) if _local_distance(last, start) < _local_distance(first, start) else edge


def _local_rebuild_edge(edge, vertex_indices, vertices, moved, vector, tolerance):
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_Transform
    from OCP.Geom import Geom_TrimmedCurve
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.GeomConvert import GeomConvert
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt

    if not vertex_indices:
        raise KernelError("local move refused: a changed edge has no vertices")
    if all(index in moved for index in vertex_indices):
        transformed = BRepBuilderAPI_Transform(
            edge, _local_translation(vector), True).Shape()
        return TopoDS.Edge(transformed), True
    if len(vertex_indices) != 2:
        raise KernelError(
            "local move refused: an incident closed or singular edge cannot move one endpoint")

    old_points = [_local_point(vertices[index]) for index in vertex_indices]
    new_points = [_local_add(point, vector) if index in moved else point
                  for point, index in zip(old_points, vertex_indices)]
    adaptor = BRepAdaptor_Curve(edge)
    if adaptor.GetType() == GeomAbs_Line:
        maker = BRepBuilderAPI_MakeEdge(gp_Pnt(*new_points[0]), gp_Pnt(*new_points[1]))
        if not maker.IsDone():
            raise KernelError("local move refused: an incident line could not be rebuilt")
        return maker.Edge(), False

    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    curve = BRep_Tool.Curve_s(edge, first, last)
    if curve is None:
        raise KernelError("local move refused: an incident edge has no exact 3D curve")
    bspline = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(curve, first, last))
    changed_poles = set()
    for pole_index in (1, bspline.NbPoles()):
        pole = bspline.Pole(pole_index)
        pole_point = pole.X(), pole.Y(), pole.Z()
        for vertex_index, old_point in zip(vertex_indices, old_points):
            if vertex_index in moved and _local_distance(pole_point, old_point) <= tolerance:
                bspline.SetPole(pole_index, gp_Pnt(*_local_add(pole_point, vector)))
                changed_poles.add(pole_index)
    if len(changed_poles) != sum(index in moved for index in vertex_indices):
        raise KernelError(
            "local move refused: an incident curve endpoint is not an endpoint BSpline pole")
    maker = BRepBuilderAPI_MakeEdge(bspline)
    if not maker.IsDone():
        raise KernelError("local move refused: an endpoint-adjusted BSpline edge is invalid")
    return maker.Edge(), False


def _local_boundary_edges(face, edges, replacements, vertices, moved, vector, tolerance):
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    outer = BRepTools.OuterWire_s(face)
    wire_count = 0
    explorer = TopExp_Explorer(face, TopAbs_WIRE)
    while explorer.More():
        wire_count += 1
        explorer.Next()
    if wire_count != 1:
        raise KernelError(
            "local move refused: affected faces with holes or multiple wires are unsupported")

    boundary = []
    occurrences = {}
    wire_explorer = BRepTools_WireExplorer(outer, face)
    while wire_explorer.More():
        old_edge = TopoDS.Edge(wire_explorer.Current())
        edge_index = _local_shape_index(edges, old_edge)
        if edge_index is None:
            raise KernelError("local move refused: an ordered boundary edge is unknown")
        start_vertex = TopoDS.Vertex(wire_explorer.CurrentVertex())
        vertex_index = _local_shape_index(vertices, start_vertex)
        if vertex_index is None:
            raise KernelError("local move refused: an ordered boundary vertex is unknown")
        occurrences[edge_index] = occurrences.get(edge_index, 0) + 1
        if BRep_Tool.Degenerated_s(old_edge):
            if edge_index in replacements:
                raise KernelError(
                    "local move refused: a changed degenerate boundary edge is unsupported")
            # A fixed zero-length pole edge contributes no geometric boundary. Omitting it
            # turns the same singular patch into its mathematically equivalent three-real-
            # boundary Coons form; its one pole vertex is still retained by adjacent edges.
            wire_explorer.Next()
            continue
        if occurrences[edge_index] > 1 or BRep_Tool.IsClosed_s(old_edge, face):
            raise KernelError(
                "local move refused: an affected periodic same-face seam is unsupported")
        start = _local_point(start_vertex)
        if vertex_index in moved:
            start = _local_add(start, vector)
        boundary.append(_local_orient_edge(
            replacements.get(edge_index, edges[edge_index]), start, tolerance))
        wire_explorer.Next()
    if len(boundary) < 3:
        raise KernelError(
            "local move refused: an affected face has fewer than three real boundaries")
    return boundary


def _local_make_wire(boundary):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire

    maker = BRepBuilderAPI_MakeWire()
    for edge in boundary:
        maker.Add(edge)
    if not maker.IsDone():
        raise KernelError("local move refused: affected boundary edges do not form a wire")
    return maker.Wire()


def _local_bspline_edge(edge, tolerance):
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.Geom import Geom_TrimmedCurve
    from OCP.GeomConvert import GeomConvert
    from OCP.TopExp import TopExp

    adaptor = BRepAdaptor_Curve(edge)
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    curve = BRep_Tool.Curve_s(edge, first, last)
    if curve is None:
        raise KernelError("local move refused: a surface boundary has no exact 3D curve")
    bspline = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(curve, first, last))
    start = _local_point(TopExp.FirstVertex_s(edge, True))
    a = bspline.StartPoint()
    b = bspline.EndPoint()
    start_distance = _local_distance((a.X(), a.Y(), a.Z()), start)
    end_distance = _local_distance((b.X(), b.Y(), b.Z()), start)
    if min(start_distance, end_distance) > tolerance:
        raise KernelError("local move refused: a BSpline boundary has the wrong orientation")
    if end_distance < start_distance:
        bspline.Reverse()
    return bspline.Copy()


def _local_normalize_bezier_weights(curve):
    """Exactly reparameterize a rational Bezier to unit endpoint weights.

    ``GeomFill_BSplineCurves`` combines boundary homogeneous coordinates directly. A trimmed
    rational Bezier can have unequal endpoint weights, making geometrically coincident corners
    incompatible and shifting the generated surface boundary. For a degree-n Bezier, multiplying
    homogeneous pole i by ``factor**i`` is the exact projective parameter substitution
    ``t = factor*u / (1-u+factor*u)``. Poles stay fixed while their weights change, so the curve's
    geometric image is unchanged. Multi-span curves remain untouched and are still rejected by
    the downstream strict boundary certificate if OCCT cannot interpolate them exactly.
    """
    if not curve.IsRational():
        return
    degree = curve.Degree()
    if degree < 1 or curve.NbKnots() != 2 or curve.NbPoles() != degree + 1:
        return
    first_weight = curve.Weight(1)
    last_weight = curve.Weight(curve.NbPoles())
    if first_weight <= 0.0 or last_weight <= 0.0:
        return
    factor = (first_weight / last_weight) ** (1.0 / degree)
    for index in range(1, curve.NbPoles() + 1):
        curve.SetWeight(
            index, curve.Weight(index) * factor ** (index - 1) / first_weight)



# Diagnostic reference retained for continuity reports/tests. A larger jump is an accepted
# NURBS crease when every exactness, topology, validity and airtightness gate passes.
LOCAL_COONS_NORMAL_JUMP = 2e-6


def _local_planar_face(boundary):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepCheck import BRepCheck_Analyzer

    try:
        maker = BRepBuilderAPI_MakeFace(_local_make_wire(boundary), True)
    except Exception:  # noqa: BLE001 - OCCT throws instead of returning IsDone=False
        return None
    return maker.Face() if maker.IsDone() and BRepCheck_Analyzer(maker.Face()).IsValid() else None


def _local_coons_face(face, curves, tolerance, *, normalize_weights=True):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.GeomAbs import GeomAbs_BSplineSurface
    from OCP.GeomFill import GeomFill_BSplineCurves, GeomFill_FillingStyle
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopoDS import TopoDS

    try:
        private_curves = [curve.Copy() for curve in curves]
        if normalize_weights:
            for curve in private_curves:
                _local_normalize_bezier_weights(curve)
        surface = GeomFill_BSplineCurves(
            *private_curves, GeomFill_FillingStyle.GeomFill_StretchStyle).Surface()
        maker = BRepBuilderAPI_MakeFace(surface, tolerance)
        rebuilt = maker.Face() if maker.IsDone() else None
    except Exception as exc:  # noqa: BLE001
        raise KernelError(f"local move refused: boundary-exact Coons face failed: {exc}") from exc
    if rebuilt is None or not BRepCheck_Analyzer(rebuilt).IsValid():
        raise KernelError("local move refused: boundary-exact Coons face is invalid")
    adaptor = BRepAdaptor_Surface(rebuilt)
    if adaptor.GetType() != GeomAbs_BSplineSurface:
        raise KernelError("local move refused: Coons reconstruction returned no BSpline face")
    bspline = adaptor.BSpline()
    if bspline.UDegree() > 8 or bspline.VDegree() > 8:
        raise KernelError(
            "local move refused: Coons reconstruction exceeded the certified degree-8 subset "
            f"({bspline.UDegree()}x{bspline.VDegree()})")
    if face.Orientation() == TopAbs_REVERSED:
        rebuilt = TopoDS.Face(rebuilt.Reversed())
    return rebuilt, bspline


def _local_wire_edges(wire):
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    edges = []
    explorer = TopExp_Explorer(wire, TopAbs_EDGE)
    while explorer.More():
        edges.append(TopoDS.Edge(explorer.Current()))
        explorer.Next()
    return edges


def _local_boundary_error(face, boundary):
    from OCP.BRepTools import BRepTools

    result_edges = _local_wire_edges(BRepTools.OuterWire_s(face))
    worst = 0.0
    for edge in boundary:
        for sample in _local_edge_points(edge, 33):
            worst = max(worst, min(
                _local_point_curve_distance(sample, candidate) for candidate in result_edges))
    for edge in result_edges:
        for sample in _local_edge_points(edge, 33):
            worst = max(worst, min(
                _local_point_curve_distance(sample, candidate) for candidate in boundary))
    return worst


def _local_point_curve_distance(point, edge):
    """Tight point-to-edge-curve distance for the strict Coons boundary certificate."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
    from OCP.gp import gp_Pnt

    adaptor = BRepAdaptor_Curve(edge)
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    curve = BRep_Tool.Curve_s(edge, first, last)
    if curve is None:
        return math.inf
    projection = GeomAPI_ProjectPointOnCurve(gp_Pnt(*point), curve, first, last)
    return projection.LowerDistance() if projection.NbPoints() else math.inf


def _local_edge_points(edge, count):
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    adaptor = BRepAdaptor_Curve(edge)
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    return [tuple(getattr(adaptor.Value(first + (last - first) * i / (count - 1)), axis)()
                  for axis in ("X", "Y", "Z"))
            for i in range(count)]


def _local_unit_normal(surface, u, v):
    from OCP.gp import gp_Pnt, gp_Vec

    point, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    surface.D1(u, v, point, du, dv)
    normal = du.Crossed(dv)
    if normal.Magnitude() <= 1e-15:
        raise KernelError("local move refused: composite Coons surface has a singular normal")
    normal.Normalize()
    return normal


def _local_full_knot_normal_jump(surface):
    """Maximum normal jump across every internal knot of full degree multiplicity."""
    u0, u1, v0, v1 = surface.Bounds()
    jumps = []
    for axis in (0, 1):
        degree = surface.UDegree() if axis == 0 else surface.VDegree()
        count = surface.NbUKnots() if axis == 0 else surface.NbVKnots()
        knot = surface.UKnot if axis == 0 else surface.VKnot
        multiplicity = surface.UMultiplicity if axis == 0 else surface.VMultiplicity
        for index in range(2, count):
            if multiplicity(index) < degree:
                continue
            value = knot(index)
            delta = min(value - knot(index - 1), knot(index + 1) - value) * 1e-6
            if delta <= 0.0:
                raise KernelError("local move refused: composite Coons knot span is degenerate")
            worst = 0.0
            for sample in range(1, 10):
                cross = (v0 + (v1 - v0) * sample / 10.0 if axis == 0 else
                         u0 + (u1 - u0) * sample / 10.0)
                before = (_local_unit_normal(surface, value - delta, cross) if axis == 0 else
                          _local_unit_normal(surface, cross, value - delta))
                after = (_local_unit_normal(surface, value + delta, cross) if axis == 0 else
                         _local_unit_normal(surface, cross, value + delta))
                cosine = max(-1.0, min(1.0, before.Dot(after)))
                worst = max(worst, math.acos(cosine))
            jumps.append(worst)
    if not jumps:
        raise KernelError(
            "local move refused: composite Coons surface has no full-multiplicity join knot")
    return max(jumps)


def _local_face_deformation(old_face, new_face, tolerance):
    old_samples = _local_face_interior_samples(old_face, tolerance)
    new_samples = _local_face_interior_samples(new_face, tolerance)
    squared = []
    for sample in old_samples:
        squared.append(_local_point_shape_distance(
            (sample.X(), sample.Y(), sample.Z()), new_face) ** 2)
    for sample in new_samples:
        squared.append(_local_point_shape_distance(
            (sample.X(), sample.Y(), sample.Z()), old_face) ** 2)
    return math.sqrt(sum(squared) / len(squared))


def _local_composite_coons_candidates(face, boundary, tolerances):
    """Five adjacent-pair natural Coons candidates; no N-sided approximation."""
    from OCP.GeomConvert import GeomConvert_CompCurveToBSplineCurve

    curves = [_local_bspline_edge(edge, tolerances["build"]) for edge in boundary]
    candidates = []
    for join_index in range(5):
        ordered = [curve.Copy() for curve in (
            curves[join_index:] + curves[:join_index])]
        joiner = GeomConvert_CompCurveToBSplineCurve(ordered[0])
        full_multiplicity = max(ordered[0].Degree(), ordered[1].Degree())
        if not joiner.Add(ordered[1], tolerances["build"], True, True,
                          full_multiplicity):
            continue
        try:
            rebuilt, surface = _local_coons_face(
                face, [joiner.BSplineCurve(), ordered[2], ordered[3], ordered[4]],
                tolerances["build"], normalize_weights=False)
            boundary_error = _local_boundary_error(rebuilt, boundary)
            normal_jump = _local_full_knot_normal_jump(surface)
            deformation = _local_face_deformation(
                face, rebuilt, tolerances["compare"])
        except KernelError:
            continue
        rank = (max(surface.UDegree(), surface.VDegree()),
                surface.UDegree() + surface.VDegree(),
                surface.NbUPoles() * surface.NbVPoles(),
                surface.NbUPoles() + surface.NbVPoles(), deformation)
        candidates.append({"face": rebuilt, "source_boundary": boundary,
                           "rank": rank, "boundary_error": boundary_error,
                           "normal_jump": normal_jump,
                           "degrees": (surface.UDegree(), surface.VDegree()),
                           "poles": (surface.NbUPoles(), surface.NbVPoles())})
    if not candidates:
        raise KernelError(
            "local move refused: no composite-natural-Coons candidate could be built")
    return candidates


def _local_rebuild_face_options(face, boundary, tolerances):
    planar = _local_planar_face(boundary)
    if planar is not None:
        return [{"face": planar, "rank": (0, 0, 0, 0, 0.0)}]
    if len(boundary) == 5:
        return _local_composite_coons_candidates(face, boundary, tolerances)
    if len(boundary) not in (3, 4):
        raise KernelError(
            f"local move refused: affected nonplanar face has {len(boundary)} real "
            "boundaries; only boundary-exact 3/4-boundary Coons or smooth composite-natural-"
            "Coons reconstruction is supported")
    rebuilt, surface = _local_coons_face(
        face, [_local_bspline_edge(edge, tolerances["build"]) for edge in boundary],
        tolerances["build"])
    return [{"face": rebuilt,
             "rank": (max(surface.UDegree(), surface.VDegree()),
                      surface.UDegree() + surface.VDegree(),
                      surface.NbUPoles() * surface.NbVPoles(),
                      surface.NbUPoles() + surface.NbVPoles(), 0.0),
             "boundary_error": _local_boundary_error(rebuilt, boundary)}]


def _local_point_shape_distance(point, shape):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    extrema = BRepExtrema_DistShapeShape(
        BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex(), shape)
    extrema.Perform()
    return extrema.Value() if extrema.IsDone() else math.inf


def _local_face_interior_samples(face, tolerance):
    """Trim-aware interior samples evaluated directly on the exact face support."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_IN
    from OCP.gp import gp_Pnt2d

    adaptor = BRepAdaptor_Surface(face)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    samples = []
    for i in range(1, 8):
        for j in range(1, 8):
            u = u0 + (u1 - u0) * i / 8.0
            v = v0 + (v1 - v0) * j / 8.0
            classifier = BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), tolerance)
            if classifier.State() == TopAbs_IN:
                samples.append(adaptor.Value(u, v))
    if not samples:
        raise KernelError("local move refused: selected face has no classifiable interior")
    return samples


def _local_part_samples(part, kind):
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    def edge_samples(edge):
        adaptor = BRepAdaptor_Curve(edge)
        first, last = adaptor.FirstParameter(), adaptor.LastParameter()
        return [adaptor.Value(first + (last - first) * i / 20.0) for i in range(21)]

    if kind == "edge":
        return edge_samples(TopoDS.Edge(part))
    samples = _local_face_interior_samples(TopoDS.Face(part), 1e-9)
    explorer = TopExp_Explorer(part, TopAbs_EDGE)
    seen = []
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        if not BRep_Tool.Degenerated_s(edge) and not any(old.IsSame(edge) for old in seen):
            seen.append(edge)
            samples.extend(edge_samples(edge))
        explorer.Next()
    explorer = TopExp_Explorer(part, TopAbs_VERTEX)
    while explorer.More():
        samples.append(BRep_Tool.Pnt_s(TopoDS.Vertex(explorer.Current())))
        explorer.Next()
    return samples


def _local_part_measure(part, kind):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    if kind == "edge":
        BRepGProp.LinearProperties_s(part, props)
    else:
        BRepGProp.SurfaceProperties_s(part, props, 1e-10)
    center = props.CentreOfMass()
    return props.Mass(), (center.X(), center.Y(), center.Z())


def _local_translated_match(old_part, candidate, kind, vector, tolerance):
    if (kind != "edge" and
            _local_part_type(old_part, kind) != _local_part_type(candidate, kind)):
        return False
    old_mass, _old_center = _local_part_measure(old_part, kind)
    new_mass, _new_center = _local_part_measure(candidate, kind)
    if abs(new_mass - old_mass) > 2.5e-7 * max(tolerance, abs(old_mass)):
        return False
    for point in _local_part_samples(old_part, kind):
        translated = _local_add((point.X(), point.Y(), point.Z()), vector)
        distance = (_local_point_curve_distance(translated, candidate) if kind == "edge" else
                    _local_point_shape_distance(translated, candidate))
        if distance > tolerance:
            return False
    reverse = tuple(-value for value in vector)
    for point in _local_part_samples(candidate, kind):
        restored = _local_add((point.X(), point.Y(), point.Z()), reverse)
        distance = (_local_point_curve_distance(restored, old_part) if kind == "edge" else
                    _local_point_shape_distance(restored, old_part))
        if distance > tolerance:
            return False
    return True


def _local_verify_vertex_containment(old_vertices, new_vertices, moved, vector, tolerance):
    if len(old_vertices) != len(new_vertices):
        raise KernelError(
            "local move refused: rebuilding changed the number of topological vertices")
    available = set(range(len(new_vertices)))
    for index, vertex in enumerate(old_vertices):
        expected = _local_point(vertex)
        if index in moved:
            expected = _local_add(expected, vector)
        matches = [candidate for candidate in available
                   if _local_distance(expected, _local_point(new_vertices[candidate])) <= tolerance]
        if len(matches) != 1:
            raise KernelError(
                "local move refused: selected-part translation or strict vertex containment "
                "was not preserved")
        available.remove(matches[0])


def local_continuity_report(shape):
    """Stored OCCT continuity classes for every manifold edge of ``shape``.

    This reports topology metadata; it does not claim that a local stretch preserves G1.
    """
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    faces = unique_faces(shape)
    report = {}
    for edge in _unique_edges(shape):
        ancestors = []
        for face in faces:
            explorer = TopExp_Explorer(face, TopAbs_EDGE)
            while explorer.More():
                if TopoDS.Edge(explorer.Current()).IsSame(edge):
                    ancestors.append(face)
                    break
                explorer.Next()
        if len(ancestors) != 2:
            continue
        try:
            name = str(BRep_Tool.Continuity_s(
                edge, ancestors[0], ancestors[1])).split("_")[-1]
        except Exception:  # noqa: BLE001
            name = "UNKNOWN"
        report[name] = report.get(name, 0) + 1
    return dict(sorted(report.items()))


def move_subobject_local(shape, kind, index, vector, target_signature=None):
    """Translate one face, edge, or vertex by topology-local B-rep reconstruction.

    The only free-form primitive is ``GeomFill_BSplineCurves``: a deterministic,
    boundary-interpolating low-degree NURBS Coons construction for exactly three or four
    ordered curves. It is distinct from approximate N-sided filling, which is never used.
    Translating one component does not promise retained G1 continuity on adjusted neighbours;
    callers can inspect :func:`local_continuity_report` before and after. Recipe replay supplies
    ``target_signature`` so the persisted component is uniquely re-resolved instead of trusting
    a possibly stale topology index.
    """
    _need()
    from OCP.BRep import BRep_Tool
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Check
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing,
                                    BRepBuilderAPI_Transform)
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.ShapeFix import ShapeFix_Solid
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if kind not in ("face", "edge", "vertex"):
        raise KernelError(
            f"local move refused: kind must be face, edge, or vertex (got {kind!r})")
    try:
        vector = tuple(float(value) for value in vector)
    except (TypeError, ValueError) as exc:
        raise KernelError("local move refused: vector must contain three finite numbers") from exc
    if len(vector) != 3 or not all(math.isfinite(value) for value in vector):
        raise KernelError("local move refused: vector must contain three finite numbers")
    if max(abs(value) for value in vector) <= 1e-14:
        raise KernelError("local move refused: translation vector is zero")
    requires_airtight = is_airtight(shape)
    single_face = (shape.ShapeType() == TopAbs_FACE and
                   BRepCheck_Analyzer(shape, True).IsValid())
    if not requires_airtight and not single_face:
        raise KernelError(
            "local move refused: input is neither one valid airtight solid nor one valid face")

    tolerances = _local_tolerances(shape)
    faces, edges, vertices = unique_faces(shape), _unique_edges(shape), _unique_vertices(shape)
    parts = {"face": faces, "edge": edges, "vertex": vertices}[kind]
    selected_index = int(index) - 1
    if selected_index < 0 or selected_index >= len(parts):
        raise KernelError(
            f"local move refused: {kind} index {index} is outside 1..{len(parts)}")
    if target_signature is not None:
        selected_index = _local_resolve_target(
            shape, kind, index, target_signature, tolerances)
    selected = parts[selected_index]
    edge_vertices = [_local_edge_vertices(edge, vertices) for edge in edges]
    face_edges = [_local_face_edges(face, edges) for face in faces]
    if kind == "face":
        selected_edges = set(face_edges[selected_index])
        moved = {vertex for edge in selected_edges for vertex in edge_vertices[edge]}
    elif kind == "edge":
        if BRep_Tool.Degenerated_s(selected):
            raise KernelError("local move refused: selected edge is degenerated")
        selected_edges = {selected_index}
        moved = set(edge_vertices[selected_index])
    else:
        selected_edges = set()
        moved = {selected_index}
    if not moved:
        raise KernelError("local move refused: selected component contains no vertices")

    build_tolerance = tolerances["build"]
    comparison_tolerance = tolerances["compare"]
    changed_edges = {edge_index for edge_index, endpoint_indices in enumerate(edge_vertices)
                     if any(vertex in moved for vertex in endpoint_indices)}
    replacements = {}
    wholesale = {}
    for edge_index in changed_edges:
        if BRep_Tool.Degenerated_s(edges[edge_index]):
            raise KernelError(
                "local move refused: a changed degenerate boundary edge is unsupported")
        replacements[edge_index], wholesale[edge_index] = _local_rebuild_edge(
            edges[edge_index], edge_vertices[edge_index], vertices, moved, vector,
            build_tolerance)
        if wholesale[edge_index] != (edge_index in selected_edges):
            raise KernelError(
                "local move refused: strict containment would translate an unselected edge "
                "wholesale")
    changed_faces = {face_index for face_index, boundary in enumerate(face_edges)
                     if any(edge in changed_edges for edge in boundary)}

    import itertools

    face_options = []
    for face_index, face in enumerate(faces):
        if kind == "face" and face_index == selected_index:
            rebuilt = TopoDS.Face(BRepBuilderAPI_Transform(
                face, _local_translation(vector), True).Shape())
            options = [{"face": rebuilt, "rank": (0, 0, 0, 0, 0.0)}]
        elif face_index in changed_faces:
            boundary = _local_boundary_edges(
                face, edges, replacements, vertices, moved, vector, build_tolerance)
            options = _local_rebuild_face_options(face, boundary, tolerances)
        else:
            options = [{"face": face, "rank": (0, 0, 0, 0, 0.0)}]
        face_options.append(options)
    candidate_count = math.prod(len(options) for options in face_options)
    if candidate_count > 25:
        raise KernelError(
            "local move refused: interacting composite Coons choices are ambiguous")

    accepted = []
    stage_counts = {name: 0 for name in (
        "sewn", "shell", "valid", "topology", "boundary", "mapped")}
    for choices in itertools.product(*face_options):
        reconstructed = [choice for choice in choices if "boundary_error" in choice]
        boundary_exact = not any(
            choice["boundary_error"] > comparison_tolerance for choice in reconstructed)
        if requires_airtight:
            sewing = BRepBuilderAPI_Sewing(tolerances["sew"])
            for choice in choices:
                sewing.Add(choice["face"])
            sewing.Perform()
            if sewing.NbFreeEdges() or sewing.NbMultipleEdges():
                continue
            stage_counts["sewn"] += 1
            sewn = sewing.SewedShape()
            shells = []
            explorer = TopExp_Explorer(sewn, TopAbs_SHELL)
            while explorer.More():
                shells.append(TopoDS.Shell(explorer.Current()))
                explorer.Next()
            if len(shells) != 1:
                continue
            stage_counts["shell"] += 1
            solid = BRepBuilderAPI_MakeSolid(shells[0]).Solid()
            fixer = ShapeFix_Solid(solid)
            fixer.Perform()
            result = fixer.Solid()
            if volume(result) < 0.0:
                result = TopoDS.Solid(result.Reversed())
            valid_result = is_airtight(result) and BRepAlgoAPI_Check(result).IsValid()
        else:
            if len(choices) != 1:
                continue
            result = choices[0]["face"]
            valid_result = (BRepCheck_Analyzer(result, True).IsValid() and
                            BRepAlgoAPI_Check(result).IsValid())
        if not valid_result:
            continue
        stage_counts["valid"] += 1
        if (len(unique_faces(result)), len(_unique_edges(result)),
                len(_unique_vertices(result))) != (len(faces), len(edges), len(vertices)):
            continue
        stage_counts["topology"] += 1
        if not boundary_exact:
            continue
        stage_counts["boundary"] += 1
        try:
            result_vertices = _unique_vertices(result)
            _local_verify_vertex_containment(
                vertices, result_vertices, moved, vector, comparison_tolerance)
            if kind in ("face", "edge"):
                result_parts = (unique_faces(result) if kind == "face" else
                                _unique_edges(result))
                matches = [candidate for candidate in result_parts
                           if _local_translated_match(
                               selected, candidate, kind, vector, comparison_tolerance)]
                if len(matches) != 1:
                    continue
            else:
                target = _local_add(_local_point(selected), vector)
                matches = [vertex for vertex in result_vertices
                           if _local_distance(target, _local_point(vertex))
                           <= comparison_tolerance]
                if len(matches) != 1:
                    continue
        except KernelError:
            continue
        stage_counts["mapped"] += 1
        rank = tuple(sum(choice["rank"][i] for choice in choices)
                     for i in range(5))
        accepted.append({
            "result": result, "rank": rank,
            "max_jump": max((choice.get("normal_jump", 0.0) for choice in choices),
                            default=0.0),
        })

    if not accepted:
        stages = ", ".join(f"{name}={count}" for name, count in stage_counts.items())
        raise KernelError(
            "local move refused: no boundary-exact candidate made one valid result; "
            f"candidate stages: {stages}")
    smooth = [candidate for candidate in accepted
              if candidate["max_jump"] <= LOCAL_COONS_NORMAL_JUMP]
    if smooth:
        accepted = smooth
    accepted.sort(key=lambda candidate: candidate["rank"])
    if len(accepted) > 1:
        first, second = accepted[0]["rank"], accepted[1]["rank"]
        if first[:4] == second[:4] and abs(first[4] - second[4]) <= comparison_tolerance:
            raise KernelError(
                "local move refused: equivalent composite-natural-Coons candidates are "
                "ambiguous")
    return accepted[0]["result"]


def surface_centroid(shape):
    """The area-weighted centre of `shape`'s faces, with an EXPLICIT tolerance.

    Exists so a test can tell two faces of the same type and area apart -- twelve fillet
    cylinders are identical in both, so only WHERE a face is distinguishes it. The tolerance is
    explicit because `BRepGProp` at its default is approximately 0.2% wrong on a B-spline
    face.
    """
    _need()
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, props, 1e-9)
    c = props.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


# --------------------------------------------------------------------------
# Measuring the analytic master for area, volume, and bounding boxes
# --------------------------------------------------------------------------

#: The two tolerances used to integrate mass properties. The tighter result is returned,
#: and their difference estimates convergence. GProp's own reported error can be about
#: 1e-16 even when the result is 1e-8 off, so it is not used.
MASS_EPS = 1e-9
MASS_EPS_CHECK = 1e-11
#: Smallest reported error relative to the value. Floating point alone puts
#: ~1e-15 on a sum like this; claiming better than 1e-12 would be false precision.
MASS_ERR_FLOOR = 1e-12


def _nurbs_convert(shape):
    """`BRepBuilderAPI_NurbsConvert` of an UNTRIANGULATED copy of `shape`. Exact either way.

    NurbsConvert can segfault (in `BRepTools_NurbsConvertModification::
    NewCurve2d`) on a solid made by `_general_transform` (a non-uniform Gumball scale) once
    that solid has been TESSELLATED for display. That covers a scaled cylinder and a scaled
    drilled box; a scaled box does not crash. A clean copy converts fine. So saving or
    booleaning a non-uniformly scaled solid after it was drawn could kill the app. The copy
    is taken without its mesh (`copyMesh=False`); the display mesh is never geometry.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_NurbsConvert

    clean = BRepBuilderAPI_Copy(shape, True, False).Shape()
    return BRepBuilderAPI_NurbsConvert(clean, True).Shape()


def _nurbs_where_needed(shape):
    """`shape` with every NON-elementary face swapped, IN PLACE, for its NURBS conversion.

    With an explicit epsilon, `BRepGProp` is accurate to approximately 1e-16 on PLANE,
    CYLINDER, SPHERE, CONE, and TORUS faces, but on
    a `SurfaceOfExtrusion` face it is wrong by up to 8.5e-3 AND does not converge as eps
    tightens. NURBS-converting that face makes it exact; converting an elementary face
    instead COSTS it 2e-7. So only the faces that need it are converted, and they are
    replaced inside the shape (`BRepTools_ReShape`) so the shell stays closed and the
    volume integral still sees one solid. Returns ``(shape, n_converted)``.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools_ReShape
    from OCP.GeomAbs import (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Sphere,
                             GeomAbs_Cone, GeomAbs_Torus)
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    elementary = (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Sphere, GeomAbs_Cone,
                  GeomAbs_Torus)
    rs = BRepTools_ReShape()
    n = 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        f = TopoDS.Face(exp.Current())
        if BRepAdaptor_Surface(f).GetType() not in elementary:
            rs.Replace(f, _nurbs_convert(f))
            n += 1
        exp.Next()
    return (rs.Apply(shape) if n else shape), n


def mass_properties(shape, solid=True):
    """Exact area (and, for a solid, volume) of `shape`, with honest error estimates.

    Returns a dict: ``area``, ``area_err``, ``area_centroid`` and, when `solid`,
    ``volume``, ``volume_err``, ``volume_centroid``; plus ``converted_faces`` (how many
    faces had to be NURBS-converted to integrate accurately). Integrated at an EXPLICIT
    epsilon; the default is approximately 0.2% wrong on a B-spline face.
    """
    _need()
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    s, converted = _nurbs_where_needed(shape)

    def run(eps):
        pa = GProp_GProps()
        BRepGProp.SurfaceProperties_s(s, pa, eps)
        pv = None
        if solid:
            pv = GProp_GProps()
            BRepGProp.VolumeProperties_s(s, pv, eps)
        return pa, pv

    def err(loose, tight):
        return max(abs(tight - loose), abs(tight) * MASS_ERR_FLOOR)

    def pt(p):
        return (p.X(), p.Y(), p.Z())

    a1, v1 = run(MASS_EPS)
    a2, v2 = run(MASS_EPS_CHECK)
    # A centroid coordinate's error uses the same convergence estimate per axis. Its
    # floor is relative to the shape's SIZE (sqrt of its area), not to the coordinate --
    # a centroid at x = 0 is not known to 1e-12 * 0.
    size = abs(a2.Mass()) ** 0.5

    def cerr(loose, tight):
        return tuple(max(abs(t - lo), size * MASS_ERR_FLOOR)
                     for lo, t in zip(pt(loose), pt(tight)))

    out = {"area": a2.Mass(), "area_err": err(a1.Mass(), a2.Mass()),
           "area_centroid": pt(a2.CentreOfMass()),
           "area_centroid_err": cerr(a1.CentreOfMass(), a2.CentreOfMass()),
           "converted_faces": converted}
    if solid:
        out["volume"] = v2.Mass()
        out["volume_err"] = err(v1.Mass(), v2.Mass())
        out["volume_centroid"] = pt(v2.CentreOfMass())
        out["volume_centroid_err"] = cerr(v1.CentreOfMass(), v2.CentreOfMass())
    return out


def nearest_face_radius(shape, point):
    """The radius of the face of `shape` nearest `point` -- what `Radius` reads on a solid.

    Returns a dict: ``kind`` (the face's surface type, as `face_types` names it),
    ``distance`` (point to face), ``foot`` (the nearest point ON the face), ``radius``
    (float; ``inf`` for a PLANE; None where one face has no single radius -- a TORUS has
    two, a free-form surface a different one in every direction), and for a CYLINDER /
    CONE / SPHERE the ``center`` and ``axis`` of the circle through ``foot``.

    All analytic: the radius is the face's own `gp_Cylinder.Radius()` etc., never a
    measurement of the triangles. On a tie (a pick on the rim, equally near the wall and
    the cap) the CURVED face wins -- the rim's radius IS the wall's.
    """
    _need()
    import math

    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.GeomAbs import (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Sphere,
                             GeomAbs_Cone, GeomAbs_Torus)
    from OCP.gp import gp_Pnt

    vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*[float(c) for c in point])).Vertex()
    found = []
    for face in unique_faces(shape):
        ext = BRepExtrema_DistShapeShape(vertex, face)
        if not ext.IsDone() or ext.NbSolution() < 1:
            continue
        q = ext.PointOnShape2(1)
        found.append((ext.Value(), face, (q.X(), q.Y(), q.Z())))
    if not found:
        return None
    best = min(d for d, _f, _q in found)
    tied = [f for f in found if f[0] <= best + 1e-7]
    curved = [f for f in tied
              if BRepAdaptor_Surface(f[1]).GetType() != GeomAbs_Plane]
    dist, face, foot = (curved or tied)[0]
    surf = BRepAdaptor_Surface(face)
    t = surf.GetType()
    out = {"distance": dist, "foot": foot, "radius": None,
           "kind": str(t).split("_")[-1].upper()}

    def on_axis(ax):
        loc, d = ax.Location(), ax.Direction()
        o = (loc.X(), loc.Y(), loc.Z())
        u = (d.X(), d.Y(), d.Z())
        s = sum((foot[i] - o[i]) * u[i] for i in range(3))
        return tuple(o[i] + s * u[i] for i in range(3)), u

    if t == GeomAbs_Plane:
        out.update(kind="PLANE", radius=math.inf)
    elif t == GeomAbs_Cylinder:
        c, u = on_axis(surf.Cylinder().Axis())
        out.update(kind="CYLINDER", radius=surf.Cylinder().Radius(), center=c, axis=u)
    elif t == GeomAbs_Cone:
        # the circular SECTION through the foot: its radius is the foot's distance from
        # the axis (exact, since the foot lies on the cone)
        c, u = on_axis(surf.Cone().Axis())
        out.update(kind="CONE", radius=math.dist(foot, c), center=c, axis=u)
    elif t == GeomAbs_Sphere:
        loc = surf.Sphere().Location()
        out.update(kind="SPHERE", radius=surf.Sphere().Radius(),
                   center=(loc.X(), loc.Y(), loc.Z()), axis=None)
    elif t == GeomAbs_Torus:
        out.update(kind="TORUS")
    return out


# --------------------------------------------------------------------------
# Exact Trim / Split of a planar face by curves
# --------------------------------------------------------------------------

def wire_from_curves(dicts):
    """One `TopoDS_Wire` through several serialized curves, in order."""
    _need()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    mk = BRepBuilderAPI_MakeWire()
    for d in dicts:
        exp = TopExp_Explorer(wire_from_curve(d), TopAbs_EDGE)
        while exp.More():
            mk.Add(TopoDS.Edge(exp.Current()))
            exp.Next()
    if not mk.IsDone():
        raise KernelError("the boundary curves do not join into one closed loop")
    return mk.Wire()


def face_from_curve_loops(loops):
    """A planar `TopoDS_Face` from boundary loops (outer first, then holes).

    `ShapeFix_Face` sets the hole wires' orientation: a hole must run opposite to the outer
    boundary, and a serialized loop does not know which way it was walked.
    """
    _need()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.ShapeFix import ShapeFix_Face

    if not loops:
        raise KernelError("a planar face needs at least its outer boundary")
    wires = [wire_from_curves(loop) for loop in loops]
    mk = BRepBuilderAPI_MakeFace(wires[0], True)
    if not mk.IsDone():
        raise KernelError("the outer boundary is not a closed PLANAR loop")
    for w in wires[1:]:
        mk.Add(w)
    fix = ShapeFix_Face(mk.Face())
    fix.FixOrientation()
    fix.Perform()
    return fix.Face()


def _edge_dict(edge):
    """One edge as a serialized EUCLIDEAN NURBS dict (as `edge_curves`), in the direction
    the edge is walked in its wire (a REVERSED edge's curve is reversed)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.Geom import Geom_TrimmedCurve
    from OCP.GeomConvert import GeomConvert
    from OCP.TopAbs import TopAbs_REVERSED

    adaptor = BRepAdaptor_Curve(edge)
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    curve = BRep_Tool.Curve_s(edge, first, last)
    bs = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(curve, first, last))
    if edge.Orientation() == TopAbs_REVERSED:
        bs.Reverse()
    n = bs.NbPoles()
    poles, weights = [], []
    for i in range(1, n + 1):
        q = bs.Pole(i)
        poles.append([q.X(), q.Y(), q.Z()])
        weights.append(bs.Weight(i))
    flat = []
    for i in range(1, bs.NbKnots() + 1):
        flat.extend([bs.Knot(i)] * bs.Multiplicity(i))
    if len(flat) == n + bs.Degree() + 1:
        flat = flat[1:-1]
    return {"ctype": "nurbs", "degree": bs.Degree(), "poles": poles, "weights": weights,
            "knots": flat, "periodic": False}


def visible_outlines(shapes, normal, xaxis, tangent_edges=True):
    """Return what a parallel view shows: each shape's visible
    sharp edges, tangent edges (optional) and silhouette OUTLINES, hidden lines removed.

    `HLRBRep_Algo` (OCCT's EXACT hidden-line removal, not the polygonal `HLRBRep_PolyAlgo`)
    with an `HLRAlgo_Projector` looking along ``-normal`` (the eye is on the `normal` side;
    `xaxis` is the screen's right, so the screen's up is ``normal x xaxis``). All shapes are
    hidden TOGETHER, so one object hides another, and the result is split per shape.

    Returns ``[[nurbs dict, ...] per shape]`` in WORLD coordinates: each curve lies in the
    plane through the origin perpendicular to `normal` -- the view projection of the real
    outline -- so a caller that projects onto the screen axes gets it unchanged.

    MEASURED: a sphere r=10 from Top is ONE `Circle` outline (plus its seam, see below); a
    cylinder seen from Front is 3 visible sharp edges + 1 outline line = its 2r x h
    rectangle; a box from Top is 4 visible edges (the 4 bottom ones come back HIDDEN). Seam
    edges (`RgNLine`, a closed face meeting itself) are NOT returned: the reference CAD application's Make2D help
    says "a single surface cylinder seam will not display". Tangent edges (`Rg1Line`, e.g. a
    fillet's boundary) are a Make2D option and are included by default here.
    """
    _need()
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.HLRAlgo import HLRAlgo_Projector
    from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    n = [float(v) for v in normal]
    x = [float(v) for v in xaxis]
    ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*n), gp_Dir(*x))
    X, Y, Z = ax.XDirection(), ax.YDirection(), ax.Direction()
    algo = HLRBRep_Algo()
    for s in shapes:
        algo.Add(s)
    algo.Projector(HLRAlgo_Projector(ax))
    algo.Update()
    algo.Hide()
    hlr = HLRBRep_HLRToShape(algo)

    def world(d):
        # the result is in the projector's frame (x, y, 0): poles map affinely to world
        d["poles"] = [[X.X() * a + Y.X() * b + Z.X() * c, X.Y() * a + Y.Y() * b + Z.Y() * c,
                       X.Z() * a + Y.Z() * b + Z.Z() * c] for a, b, c in d["poles"]]
        return d

    from OCP.BRepLib import BRepLib

    out = []
    for s in shapes:
        curves = []
        kinds = [hlr.VCompound(s), hlr.OutLineVCompound(s)]
        if tangent_edges:
            kinds.append(hlr.Rg1LineVCompound(s))
        for comp in kinds:
            if comp.IsNull():
                continue
            # MEASURED: HLR's edges are 2-D curves ON the projection plane with NO 3-D
            # curve (`BRep_Tool.Curve_s` is None, and reading it crashed the process).
            # On a plane the 3-D curve is the pcurve mapped through the plane -- exact.
            BRepLib.BuildCurves3d_s(comp)
            exp = TopExp_Explorer(comp, TopAbs_EDGE)
            while exp.More():
                curves.append(world(_edge_dict(TopoDS.Edge(exp.Current()))))
                exp.Next()
        out.append(curves)
    return out


def face_loops(face):
    """The face's boundary as ``[outer_loop, hole_loop, ...]``, each a list of serialized
    curves in WALKING order (`BRepTools_WireExplorer`), so consecutive curves join end to
    start -- exactly the input `face_from_curve_loops` and a rhino3dm polycurve need."""
    _need()
    from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    outer = BRepTools.OuterWire_s(face)
    wires = [outer]
    exp = TopExp_Explorer(face, TopAbs_WIRE)
    while exp.More():
        w = TopoDS.Wire(exp.Current())
        if not w.IsSame(outer):
            wires.append(w)
        exp.Next()
    out = []
    for w in wires:
        loop = []
        we = BRepTools_WireExplorer(w, face)
        while we.More():
            loop.append(_edge_recipe(we.Current()))
            we.Next()
        out.append(loop)
    return out


def _edge_recipe(edge):
    """One edge serialized KEEPING ITS TYPE, in walking direction: a straight edge as a
    ``line``, a circular one as an ``arc`` (centre, normal, x axis, radius, angle range --
    a full circle is an arc of 2pi), anything else as `_edge_dict`'s NURBS.

    When `face_loops` emitted NURBS for every edge, a recipe built on
    it (an extruded holed square) came back from a reload with every wall a
    SURFACEOFEXTRUSION -- {EXTRUSION: 5, PLANE: 2} where it was {PLANE: 6, CYLINDER: 1}.
    A line must be serialized as a line and a circle as a circle.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
    from OCP.TopAbs import TopAbs_REVERSED

    a = BRepAdaptor_Curve(edge)
    t0, t1 = a.FirstParameter(), a.LastParameter()
    rev = edge.Orientation() == TopAbs_REVERSED

    def xyz(p):
        return [p.X(), p.Y(), p.Z()]

    kind = a.GetType()
    if kind == GeomAbs_Line:
        p, q = xyz(a.Value(t0)), xyz(a.Value(t1))
        return {"ctype": "line", "a": q if rev else p, "b": p if rev else q}
    if kind == GeomAbs_Circle:
        c = a.Circle()
        ax = c.Position()
        n, x = xyz(ax.Direction()), xyz(ax.XDirection())
        if rev:
            # walking backwards: flip the normal (so y flips) and negate the angles
            n = [-v for v in n]
            t0, t1 = -t1, -t0
        return {"ctype": "arc", "center": xyz(ax.Location()), "normal": n, "xaxis": x,
                "radius": c.Radius(), "start": t0, "end": t1}
    return _edge_dict(edge)


def face_plane(face):
    """``(origin, xdir, ydir, normal)`` of a PLANAR face, or None if it is not planar.

    `normal` is the face's OUTWARD normal: the surface normal ``xdir x ydir``, flipped for
    a reversed face. Using ``Direction()`` instead is incorrect because it equals
    ``xdir x ydir`` only for a right-handed (`Direct`) plane. A MIRRORED solid has
    left-handed planes, so every face normal of a mirrored box came back INWARD (BRepGProp
    gives the true outward normal, and it disagreed on all six faces).
    """
    _need()
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_REVERSED

    surf = BRepAdaptor_Surface(face)
    if surf.GetType() != GeomAbs_Plane:
        return None
    ax = surf.Plane().Position()

    def t(v):
        return (v.X(), v.Y(), v.Z())

    x, y = t(ax.XDirection()), t(ax.YDirection())
    n = (x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0])
    if face.Orientation() == TopAbs_REVERSED:
        n = (-n[0], -n[1], -n[2])
    return t(ax.Location()), t(ax.XDirection()), t(ax.YDirection()), n


def split_face(face, tool_wires):
    """The pieces of `face` cut by `tool_wires` (curves IN its plane): a list of faces.

    `BRepAlgoAPI_Splitter` -- exact (MEASURED: a 10x10 square cut by an r=3 circle gives
    71.7256661176919 + 28.2743338823081, i.e. 100 - 9pi and 9pi to 1e-14). A tool that does
    not separate anything leaves the face whole (one piece).
    """
    _need()
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCP.collections import List_TopoDS_Shape
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    args, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
    args.Append(face)
    for w in tool_wires:
        tools.Append(w)
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments(args)
    sp.SetTools(tools)
    sp.Build()
    if not sp.IsDone():
        raise KernelError("the planar split did not complete")
    out = []
    exp = TopExp_Explorer(sp.Shape(), TopAbs_FACE)
    while exp.More():
        out.append(TopoDS.Face(exp.Current()))
        exp.Next()
    return out


#: The measured 3-D tolerance used to approximate a pulled curve.
PULL_TOLERANCE = 1e-7


def curve_tools_on_face(face, curves, tol=1e-7):
    """Build cutting tools for splitting `face` by serialized curves, following the reference CAD application's
    documented behavior:

    * a curve already ON the face (every sample within `tol`) is used AS IS -- so a ring
      drawn on a cylinder wall stays a `Geom_Circle` edge;
    * a PLANAR curve is projected onto the face along its plane normal ("projected on the
      surface in a direction perpendicular to the curve plane"; in a plan view that IS the
      view direction for a curve drawn there) -- `BRepProj_Projection`;
    * a 3-D curve is "pulled onto the surface by closest points" --
      `BRepOffsetAPI_NormalProjection`.

    A circle r=2 projected through a cylinder wall R=5 gives a hole edge that projects
    back onto the circle to 3.5e-8 and a hole area 5.5e-10
    from the exact integral; splitting by the projection PRISM instead was 6.2e-5 off.
    Each entry of `curves` is ``(curve_dict, normal_or_None)``. Returns ``(tools, report)``
    where `report` names how each curve was used.
    """
    _need()
    from OCP.BRep import BRep_Builder
    from OCP.BRepAdaptor import BRepAdaptor_CompCurve
    from OCP.BRepOffsetAPI import BRepOffsetAPI_NormalProjection
    from OCP.BRepProj import BRepProj_Projection
    from OCP.gp import gp_Dir
    from OCP.TopoDS import TopoDS_Compound

    tools, report = [], []
    for d, normal in curves:
        w = wire_from_curve(d)
        a = BRepAdaptor_CompCurve(w)
        t0, t1 = a.FirstParameter(), a.LastParameter()
        pts = []
        for k in range(33):
            p = a.Value(t0 + (t1 - t0) * k / 32)
            pts.append((p.X(), p.Y(), p.Z()))
        if all(distance_to_shape(face, p) <= tol for p in pts):
            tools.append(w)
            report.append("on the surface")
            continue
        comp = TopoDS_Compound()
        b = BRep_Builder()
        b.MakeCompound(comp)
        n_hits = 0
        if normal is not None:
            proj = BRepProj_Projection(w, face, gp_Dir(*[float(v) for v in normal]))
            while proj.More():
                b.Add(comp, proj.Current())
                n_hits += 1
                proj.Next()
            how = "projected along its plane normal"
        else:
            from OCP.collections import List_TopoDS_Shape

            from OCP.GeomAbs import GeomAbs_C2

            np_ = BRepOffsetAPI_NormalProjection(face)
            np_.Add(w)
            # a pull is an APPROXIMATION by nature (the reference CAD application's too, to its tolerance); OCCT's
            # default left the pulled edge 4.6e-7 off the surface; 1e-7 gives 1.7e-9 (1e-9 made
            # the approximation FAIL -- no wire -- MEASURED, so it is not tighter)
            np_.SetParams(PULL_TOLERANCE, PULL_TOLERANCE, GeomAbs_C2, 14, 64)
            np_.Compute3d(True)
            np_.Build()
            wires = List_TopoDS_Shape()
            # MEASURED: the raw result is loose EDGES (split at the surface seam, their
            # end vertices not shared), which the splitter ignores -- a closed loop around
            # a cylinder cut nothing. `BuildWire` connects them into the closed wire.
            if np_.IsDone() and np_.BuildWire(wires):
                for wire in wires:
                    b.Add(comp, wire)
                    n_hits += 1
            how = "pulled by closest points"
        if n_hits == 0:
            report.append("misses the surface")
            continue
        tools.append(comp)
        report.append(how)
    return tools, report


def merge_seam_pieces(face, pieces, tol=1e-7):
    """Rejoin split pieces separated only by `face`'s periodic seam.

    MEASURED: a cylinder wall cut by two lines into two regions came back as THREE faces --
    the region spanning the seam was split along it (57.96 + 57.96 where the region is
    115.93). The seam is a parameterisation artefact, not a cut, so pieces sharing a seam
    edge are sewn and `ShapeUpgrade_UnifySameDomain` makes them one face again. Pieces
    shared only along a real CUT stay separate (the merge looks at seam edges only).
    """
    _need()
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Compound

    def edges(shape):
        out, e = [], TopExp_Explorer(shape, TopAbs_EDGE)
        while e.More():
            out.append(TopoDS.Edge(e.Current()))
            e.Next()
        return out

    seams = [e for e in edges(face) if BRep_Tool.IsClosed_s(e, face)]
    if not seams or len(pieces) < 2:
        return pieces
    seam_comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(seam_comp)
    for e in seams:
        b.Add(seam_comp, e)

    def on_seam(e):
        a = BRepAdaptor_Curve(e)
        t0, t1 = a.FirstParameter(), a.LastParameter()
        for k in range(9):
            p = a.Value(t0 + (t1 - t0) * (k + 0.5) / 9)
            if distance_to_shape(seam_comp, (p.X(), p.Y(), p.Z())) > tol:
                return False
        return True

    parent = list(range(len(pieces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pe = [edges(p) for p in pieces]
    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            if any(ei.IsSame(ej) and on_seam(ei) for ei in pe[i] for ej in pe[j]):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(pieces)):
        groups.setdefault(find(i), []).append(pieces[i])
    out = []
    for grp in groups.values():
        if len(grp) == 1:
            out.append(grp[0])
            continue
        sew = BRepBuilderAPI_Sewing(tol)
        for f in grp:
            sew.Add(f)
        sew.Perform()
        uni = ShapeUpgrade_UnifySameDomain(sew.SewedShape(), True, True, False)
        uni.Build()
        merged = []
        exp = TopExp_Explorer(uni.Shape(), TopAbs_FACE)
        while exp.More():
            merged.append(TopoDS.Face(exp.Current()))
            exp.Next()
        out.extend(merged if len(merged) == 1 else grp)
    return out


def face_signature(face):
    """``{"area", "centroid"}`` -- how a split piece is recognised again on a rebuild."""
    mp = mass_properties(face, solid=False)
    return {"area": mp["area"], "centroid": [float(v) for v in surface_centroid(face)]}


def split_piece(base_face, curves, signature):
    """Rebuild one piece of `base_face` split by `curves` (as `curve_tools_on_face`):
    the piece whose area and centroid match `signature`. Raises KernelError when none does
    (to 1e-6 of the area) -- never a different piece."""
    tools, _r = curve_tools_on_face(base_face, curves)
    pieces = merge_seam_pieces(base_face, split_face(base_face, tools))
    best, err = None, None
    for p in pieces:
        s = face_signature(p)
        e = abs(s["area"] - signature["area"]) + sum(
            abs(a - b) for a, b in zip(s["centroid"], signature["centroid"]))
        if err is None or e < err:
            best, err = p, e
    if best is None or err > 1e-6 * max(1.0, abs(signature["area"])):
        raise KernelError("the split piece could not be found again")
    return best


def distance_to_shape(shape, point):
    """The distance from `point` to `shape`."""
    _need()
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    v = BRepBuilderAPI_MakeVertex(gp_Pnt(*[float(c) for c in point])).Vertex()
    ext = BRepExtrema_DistShapeShape(v, shape)
    return ext.Value() if ext.IsDone() else float("inf")


# --------------------------------------------------------------------------
# Describe the analytic master as an exact .3dm B-Rep for openNURBS
# --------------------------------------------------------------------------

def _fmt(x):
    return repr(float(x))


def _flat_knots(knot, mult, nb):
    """OCCT knots + multiplicities -> openNURBS/rhino3dm's flat vector (minus the first
    and last of the full clamped one)."""
    flat = []
    for i in range(1, nb + 1):
        flat.extend([knot(i)] * mult(i))
    return flat[1:-1]


def _curve_text(bs, dim):
    """A Geom(2d)_BSplineCurve in the helper's `<curve>` form (homogeneous CVs)."""
    if bs.IsPeriodic():
        bs.SetNotPeriodic()
    n, deg = bs.NbPoles(), bs.Degree()
    rational = bs.IsRational()
    out = [str(dim), "1" if rational else "0", str(deg + 1), str(n)]
    for i in range(1, n + 1):
        p = bs.Pole(i)
        w = bs.Weight(i) if rational else 1.0
        coords = [p.X(), p.Y()] + ([p.Z()] if dim == 3 else [])
        out += [_fmt(c * w) for c in coords] + ([_fmt(w)] if rational else [])
    out += [_fmt(k) for k in _flat_knots(bs.Knot, bs.Multiplicity, bs.NbKnots())]
    return " ".join(out)


def _surface_text(bs):
    if bs.IsUPeriodic():
        bs.SetUNotPeriodic()
    if bs.IsVPeriodic():
        bs.SetVNotPeriodic()
    nu, nv = bs.NbUPoles(), bs.NbVPoles()
    rational = bs.IsURational() or bs.IsVRational()
    out = ["1" if rational else "0", str(bs.UDegree() + 1), str(bs.VDegree() + 1),
           str(nu), str(nv)]
    for i in range(1, nu + 1):
        for j in range(1, nv + 1):
            p = bs.Pole(i, j)
            w = bs.Weight(i, j) if rational else 1.0
            out += [_fmt(p.X() * w), _fmt(p.Y() * w), _fmt(p.Z() * w)]
            if rational:
                out.append(_fmt(w))
    out += [_fmt(k) for k in _flat_knots(bs.UKnot, bs.UMultiplicity, bs.NbUKnots())]
    out += [_fmt(k) for k in _flat_knots(bs.VKnot, bs.VMultiplicity, bs.NbVKnots())]
    return " ".join(out)


def brep_description(shape):
    """`shape` as the `brep_writer` protocol text (see kernel/brep_writer.cpp).

    The shape is NURBS-converted first (`BRepBuilderAPI_NurbsConvert`): exact for planes,
    cylinders, cones, spheres, tori and B-splines -- openNURBS stores every face as a NURBS
    surface anyway, which is how a the reference CAD application cylinder is stored. Topology is shared: an edge
    used by two faces is ONE openNURBS edge with two trims; a seam edge appears twice in its
    face's loop; a degenerated edge (a sphere's pole) becomes a SINGULAR trim at its vertex.
    Each face is explored FORWARD so its outer wire runs counterclockwise in (u, v); its
    REVERSED orientation is carried as `m_bRev`. A trim along a REVERSED edge runs against
    the edge's 3-D curve (`rev3d`) and its parameter-space curve is reversed to match.
    """
    _need()
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
    from OCP.collections import (
        IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as _Map)
    from OCP.Geom import Geom_BSplineSurface, Geom_TrimmedCurve
    from OCP.Geom2d import Geom2d_TrimmedCurve
    from OCP.Geom2dConvert import Geom2dConvert
    from OCP.GeomConvert import GeomConvert
    from OCP.TopAbs import (TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED,
                            TopAbs_VERTEX, TopAbs_WIRE)
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS

    s = _nurbs_convert(shape)
    vmap, emap = _Map(), _Map()
    TopExp.MapShapes_s(s, TopAbs_VERTEX, vmap)
    TopExp.MapShapes_s(s, TopAbs_EDGE, emap)

    tol = 1e-7
    lines = ["ONBREP 1"]
    verts = []
    for i in range(1, vmap.Extent() + 1):
        v = TopoDS.Vertex(vmap.FindKey(i))
        p = BRep_Tool.Pnt_s(v)
        tol = max(tol, BRep_Tool.Tolerance_s(v))
        verts.append(f"{_fmt(p.X())} {_fmt(p.Y())} {_fmt(p.Z())}")
    on_edge = {}                       # OCCT edge map index -> openNURBS edge index
    edges = []
    for i in range(1, emap.Extent() + 1):
        e = TopoDS.Edge(emap.FindKey(i))
        if BRep_Tool.Degenerated_s(e):
            continue
        ef = TopoDS.Edge(e.Oriented(TopAbs_FORWARD))
        first, last = BRep_Tool.Range_s(ef)
        curve = BRep_Tool.Curve_s(ef, first, last)
        bs = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(curve, first, last))
        v0 = vmap.FindIndex(TopExp.FirstVertex_s(ef)) - 1
        v1 = vmap.FindIndex(TopExp.LastVertex_s(ef)) - 1
        tol = max(tol, BRep_Tool.Tolerance_s(ef))
        on_edge[i] = len(edges)
        edges.append(f"{v0} {v1} {_curve_text(bs, 3)}")
    faces = []
    exp = TopExp_Explorer(s, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        exp.Next()
        rev = 1 if face.Orientation() == TopAbs_REVERSED else 0
        ff = TopoDS.Face(face.Oriented(TopAbs_FORWARD))
        surf = BRep_Tool.Surface_s(ff)
        if not isinstance(surf, Geom_BSplineSurface):
            # OCP hands back the DYNAMIC type (MEASURED: there is no DownCast); anything
            # NurbsConvert left non-B-spline (a trimmed/offset wrapper) is converted exactly
            surf = GeomConvert.SurfaceToBSplineSurface_s(surf)
        parts = [str(rev), _surface_text(surf)]
        outer = BRepTools.OuterWire_s(ff)
        wires = [outer]
        wexp = TopExp_Explorer(ff, TopAbs_WIRE)
        while wexp.More():
            w = TopoDS.Wire(wexp.Current())
            if not w.IsSame(outer):
                wires.append(w)
            wexp.Next()
        parts.append(str(len(wires)))
        for li, w in enumerate(wires):
            trims = []
            we = BRepTools_WireExplorer(w, ff)
            while we.More():
                e = we.Current()
                we.Next()
                first, last = BRep_Tool.Range_s(e)
                c2 = BRep_Tool.CurveOnSurface_s(e, ff, first, last)
                b2 = Geom2dConvert.CurveToBSplineCurve_s(Geom2d_TrimmedCurve(c2, first, last))
                reversed_ = e.Orientation() == TopAbs_REVERSED
                if reversed_:
                    b2.Reverse()
                if BRep_Tool.Degenerated_s(e):
                    vi = vmap.FindIndex(TopExp.FirstVertex_s(e)) - 1
                    trims.append(f"-1 0 {vi} {_curve_text(b2, 2)}")
                else:
                    ei = on_edge[emap.FindIndex(e)]
                    trims.append(f"{ei} {1 if reversed_ else 0} -1 {_curve_text(b2, 2)}")
            parts.append(f"{1 if li == 0 else 2} {len(trims)}")
            parts.extend(trims)
        faces.append("\n".join(parts))
    lines.append(f"tol {_fmt(max(tol, 1e-7))}")
    lines.append(f"vertices {len(verts)}")
    lines += verts
    lines.append(f"edges {len(edges)}")
    lines += edges
    lines.append(f"faces {len(faces)}")
    lines += faces
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Convert a .3dm B-Rep into an Open CASCADE master
# --------------------------------------------------------------------------

#: A NURBS face within this distance of a plane / cylinder / cone / sphere / torus is
#: rebuilt as that surface. the reference CAD application stores every face as rational NURBS -- a the reference CAD application
#: cylinder IS a degree-2 rational surface -- so this recovers what the surface already
#: is; it never bends a free-form face (1e-9 is ~6 orders below any manufacturing
#: tolerance and ~4 above the round-off of a rational circle). MEASURED: the 8-face
#: box-union-cylinder comes back {PLANE: 7, CYLINDER: 1} with volume error 0.0, where
#: keeping the NURBS faces gave {BSPLINE: 8} and 2.3e-8 (ShapeFix re-projects pcurves on
#: a B-spline; on a plane or a cylinder the projection is exact).
ANALYTIC_RECOGNITION_TOL = 1e-9


def _bspline_curve(d):
    """A `Geom_BSplineCurve` from a serialized NURBS dict (EUCLIDEAN poles + weights)."""
    from OCP.collections import Array1_double, Array1_gp_Pnt
    from OCP.Geom import Geom_BSplineCurve
    from OCP.gp import gp_Pnt

    # A periodic curve's wrapped poles and unclamped knots form a plain B-spline.
    poles = d["poles"]
    weights = d.get("weights") or [1.0] * len(poles)
    pa, wa = Array1_gp_Pnt(1, len(poles)), Array1_double(1, len(poles))
    for i, (q, w) in enumerate(zip(poles, weights), start=1):
        pa.SetValue(i, gp_Pnt(float(q[0]), float(q[1]), float(q[2])))
        wa.SetValue(i, float(w))
    ka, ma = _knot_arrays(d["knots"])
    return Geom_BSplineCurve(pa, wa, ka, ma, int(d["degree"]), False)


def _bspline_surface(d):
    """A `Geom_BSplineSurface` from ``{degree_u, degree_v, poles[u][v], weights[u][v],
    knots_u, knots_v}`` (rhino3dm's flat knots; EUCLIDEAN poles)."""
    from OCP.collections import Array2_double, Array2_gp_Pnt
    from OCP.Geom import Geom_BSplineSurface
    from OCP.gp import gp_Pnt

    poles, weights = d["poles"], d["weights"]
    cu, cv = len(poles), len(poles[0])
    pa, wa = Array2_gp_Pnt(1, cu, 1, cv), Array2_double(1, cu, 1, cv)
    for i in range(cu):
        for j in range(cv):
            q = poles[i][j]
            pa.SetValue(i + 1, j + 1, gp_Pnt(float(q[0]), float(q[1]), float(q[2])))
            wa.SetValue(i + 1, j + 1, float(weights[i][j]))
    ku, mu = _knot_arrays(d["knots_u"])
    kv, mv = _knot_arrays(d["knots_v"])
    return Geom_BSplineSurface(pa, wa, ku, kv, mu, mv, int(d["degree_u"]),
                               int(d["degree_v"]), False, False)


def _recognise_surface(bs, tol):
    """`bs` as the analytic surface it already is (within `tol`), else `bs` unchanged."""
    from OCP.GeomConvert import GeomConvert_SurfToAnaSurf

    try:
        ana = GeomConvert_SurfToAnaSurf(bs).ConvertToAnalytical(float(tol))
    except Exception:  # noqa: BLE001
        ana = None
    return ana if ana is not None else bs


def shape_from_brep_data(data, sew_tolerance=1e-6,
                         analytic_tolerance=ANALYTIC_RECOGNITION_TOL, report=None):
    """Build an OCCT shape from a foreign B-Rep's own faces, edges, and loops.

    `data` (built by `bridge.brep_data` from an `r3.Brep`, so this module stays free of
    rhino3dm)::

        {"edges": [nurbs-curve dict, ...],
         "faces": [{"surface": nurbs-surface dict, "reversed": bool,
                    "loops": [{"outer": bool, "trims": [[edge_index, reversed], ...]}]}]}

    Each face is its NURBS surface -- recognised as the plane / cylinder / sphere / ...
    it already is (`ANALYTIC_RECOGNITION_TOL`) -- bounded by wires of the SHARED edges,
    each edge used in the direction its TRIM runs:
    that is what makes a hole a hole (openNURBS: outer loops run counterclockwise in the
    surface's (u, v), inner loops clockwise -- MEASURED, letting `MakeWire` re-orient the
    edges instead filled the hole and gave box-union-cylinder 43.48 for 78.14). A seam is
    the same edge twice, once each way; a singular trim (a pole) has no edge and
    `ShapeFix_Face` adds the degenerate edge. `ShapeFix_Face` also computes the
    parameter-space curves (rhino3dm exposes no 2-D trims). Edges get their own vertices
    and sewing merges them. Building edges on the B-Rep's shared vertices made no
    measurable difference, so that extra step was removed. The faces
    are then sewn into a shell, and a closed shell becomes a solid, oriented by
    `ShapeFix_Solid`. `sew_tolerance` is the file's absolute tolerance -- the one its
    author joined at. MEASURED: it is the FACE fixer's precision that closes a joined-at-
    0.001 gap (a box edge end 1e-4 off: airtight at 0.001, open at 1e-6); because faces
    share edges BY INDEX, the sewing itself never had a gap to close in any test case. Raises KernelError --
    never approximates -- when a face cannot be built.

    `report`, a dict if given, gets ``"airtight"``: True exactly when the result is the
    solid this function itself checked with `is_airtight` -- anything else it returns is a
    sewn shell or compound, which holds no solid and so is never airtight. A caller needing
    that verdict reads it here instead of paying for the check twice.
    """
    _need()
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                    BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing)
    from OCP.ShapeFix import ShapeFix_Face, ShapeFix_Solid
    from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_FACE, TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Wire

    edges = []
    for i, c in enumerate(data["edges"]):
        try:
            edges.append(BRepBuilderAPI_MakeEdge(_bspline_curve(c)).Edge())
        except KernelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KernelError(f"edge {i} could not be built: {e}") from e
    builder = BRep_Builder()
    sew = BRepBuilderAPI_Sewing(float(sew_tolerance))
    nfaces = 0
    from OCP.BRepCheck import BRepCheck_Analyzer

    def bounded(surf, fd, fi):
        wires = []
        for loop in fd["loops"]:
            w = TopoDS_Wire()
            builder.MakeWire(w)
            for ei, rev in loop["trims"]:
                if ei >= 0:                     # -1 = a singular trim (a pole)
                    builder.Add(w, edges[ei].Reversed() if rev else edges[ei])
            wires.append((bool(loop["outer"]), w))
        wires.sort(key=lambda w: not w[0])
        if not wires:
            raise KernelError(f"face {fi} has no loop")
        mf = BRepBuilderAPI_MakeFace(surf, wires[0][1], True)
        for _outer, w in wires[1:]:
            mf.Add(w)
        if not mf.IsDone():
            raise KernelError(f"face {fi} could not be bounded by its loops")
        fix = ShapeFix_Face(mf.Face())
        # a file joined at a looser tolerance has trims whose ends meet only within it:
        # let the wire fixer close gaps up to the SAME tolerance the sewing uses
        fix.SetPrecision(float(sew_tolerance))
        fix.SetMaxTolerance(float(sew_tolerance))
        fix.Perform()
        return fix.Face()

    from OCP.ShapeFix import ShapeFix_Shape

    def repaired(surf, fd, fi):
        face = bounded(surf, fd, fi)
        if BRepCheck_Analyzer(face).IsValid():
            return face
        # A ruled loft between two circles (one face whose loop is bottom
        # circle, seam, top circle, seam) came out INVALID from ShapeFix_Face -- it gave the
        # two pcurves of a seam to the CIRCLE edges -- and its cap then failed as "not
        # airtight". ShapeFix_Shape repairs the seam assignment.
        fixer = ShapeFix_Shape(face)
        fixer.Perform()
        fixed = fixer.Shape()
        if fixed.ShapeType() == TopAbs_FACE and BRepCheck_Analyzer(fixed).IsValid():
            return TopoDS.Face(fixed)
        # MEASURED: a lone TORUS face (a capped pipe from a the reference CAD application file) is "invalid" until
        # it is sewn to its caps, and sewing + ShapeFix_Solid make the solid valid -- so
        # the face is left as built; downstream airtightness checks judge the result.
        # Falling back to the B-spline here instead turns that torus into {BSPLINE: 1},
        # 2.3e-10 off.
        return face

    for fi, fd in enumerate(data["faces"]):
        try:
            face = repaired(_recognise_surface(_bspline_surface(fd["surface"]),
                                               analytic_tolerance), fd, fi)
            if fd.get("reversed"):
                face = TopoDS.Face(face.Reversed())
            sew.Add(face)
            nfaces += 1
        except KernelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KernelError(f"face {fi} could not be built: {e}") from e
    if nfaces == 0:
        raise KernelError("the brep has no faces")
    sew.Perform()
    sewn = sew.SewedShape()
    shells = []
    exp = TopExp_Explorer(sewn, TopAbs_SHELL)
    while exp.More():
        shells.append(TopoDS.Shell(exp.Current()))
        exp.Next()
    if not shells and sewn.ShapeType() == TopAbs_FACE:
        # sewing ONE face returns the face (MEASURED: a sphere, whose single face closes on
        # itself through its seam and two poles) -- give it its shell
        from OCP.TopoDS import TopoDS_Shell

        shell = TopoDS_Shell()
        builder.MakeShell(shell)
        builder.Add(shell, sewn)
        shells.append(shell)
    if len(shells) == 1:
        try:
            solid = BRepBuilderAPI_MakeSolid(shells[0]).Solid()
            fs = ShapeFix_Solid(solid)
            fs.Perform()
            out = fs.Solid()
            if is_airtight(out):
                if report is not None:
                    report["airtight"] = True
                return out
        except Exception:  # noqa: BLE001
            pass
    if report is not None:
        report["airtight"] = False
    if sewn.ShapeType() == TopAbs_COMPOUND and len(shells) == 1:
        return shells[0]
    return sewn


def shape_to_bytes(shape, triangles=True):
    """`shape` in OCCT's own BINARY BRep format, for handing a master from a worker
    process to the application (`bridge.prefetch_document`). It is not a public file format.

    Nothing is approximated: every double is written bit for bit, every surface keeps its
    own type (a plane stays a `Geom_Plane`, a NURBS face its poles, weights and knots), and
    topology, orientation, tolerances and any face triangulation travel as they are. The
    OCCT re-normalises unit directions while reading: across 40 representative solids, a
    few direction components near 1e-16 moved by one ULP (about 1e-33). Volumes, areas,
    vertices, and face types read back identically. `triangles=False` omits triangulation
    to compare
    two shapes' GEOMETRY byte for byte).
    """
    _need()
    import io

    from OCP.BinTools import BinTools, BinTools_FormatVersion

    buf = io.BytesIO()
    if triangles:
        BinTools.Write_s(shape, buf)
    else:
        BinTools.Write_s(shape, buf, False, False,
                         BinTools_FormatVersion.BinTools_FormatVersion_CURRENT)
    return buf.getvalue()


def shape_from_bytes(data, as_type="TopoDS_Shape"):
    """The shape `shape_to_bytes` wrote, as the Python class named `as_type` -- the class
    the writer's shape had (`shape_from_brep_data` hands back a plain `TopoDS_Shape` for a
    solid, a `TopoDS_Shell` for an open shell), so a reader gets the very same kind of
    object. Raises KernelError on empty or unreadable data -- never an empty shape."""
    _need()
    import io

    from OCP.BinTools import BinTools
    from OCP.TopoDS import TopoDS, TopoDS_Shape

    shape = TopoDS_Shape()
    try:
        BinTools.Read_s(shape, io.BytesIO(bytes(data)))
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"the binary BRep could not be read: {e}") from e
    if shape.IsNull():
        raise KernelError("the binary BRep held no shape")
    if as_type and as_type != "TopoDS_Shape":
        cast = getattr(TopoDS, as_type.replace("TopoDS_", ""), None)
        if cast is None:
            raise KernelError(f"cannot read a binary BRep back as a {as_type}")
        shape = cast(shape)
    return shape


def tight_bbox(shape):
    """``((xmin, ymin, zmin), (xmax, ymax, zmax))`` of `shape`, TIGHT on its surfaces.

    MEASURED on box-union-cylinder (truth 0..13 x 0..10 x 0..10): `BRepBndLib.Add_s`
    overshoots by 0.0084 (it pads by the triangulation's deflection); `Add_s(useTri=False)`
    and `AddOptimal_s(useShapeTol=True)` still pad by the 1e-7 shape tolerance;
    `AddOptimal_s(useTriangulation=False, useShapeTolerance=False)` with a zero gap is
    the tightest: EXACT on box-union-cylinder, a sphere and a cylinder, and padded by at
    at most the 1e-7 edge tolerance on a torus and a NURBS extrusion. That is the path
    used; the 1e-7 is far below any
    manufacturing tolerance but it is NOT zero, and is not claimed to be.

    On an ellipsoid represented by one rational B-spline face, `AddOptimal_s` is not tight.
    It reads x_min -1.973945 for a true -2.0, which is 0.026 inside
    the solid, at the face's seam. So when any face is not elementary (plane, cylinder,
    cone, sphere, torus), each of the six bounds is MEASURED exactly instead: the distance
    (`BRepExtrema_DistShapeShape`) from a plane parallel to that side, placed well outside,
    gives the extreme point on that side. Elementary-only shapes keep the fast path, which
    was measured exact for them.
    """
    _need()
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    bb.SetGap(0.0)
    BRepBndLib.AddOptimal_s(shape, bb, False, False)
    a, b = bb.CornerMin(), bb.CornerMax()
    lo, hi = [a.X(), a.Y(), a.Z()], [b.X(), b.Y(), b.Z()]
    if set(face_types(shape)) <= set(ELEMENTARY_FACE_TYPES):
        return tuple(lo), tuple(hi)
    return _exact_bounds(shape, lo, hi)


#: Face types whose `AddOptimal_s` box is exact (MEASURED, see `tight_bbox`).
ELEMENTARY_FACE_TYPES = ("PLANE", "CYLINDER", "CONE", "SPHERE", "TORUS")


def _exact_bounds(shape, lo, hi):
    """The six bounds of `shape`, each the exact distance to a far plane (see `tight_bbox`).
    `lo`/`hi` is a first estimate; the planes are placed a full span beyond it, so an
    estimate that is short (the defect being fixed) cannot put a plane inside the shape."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

    span = max(h - l for l, h in zip(lo, hi)) + 1.0
    mid = [(l + h) / 2.0 for l, h in zip(lo, hi)]
    out_lo, out_hi = list(lo), list(hi)
    for ax in range(3):
        for sign in (-1.0, 1.0):
            base = (lo if sign < 0 else hi)[ax] + sign * span
            origin = list(mid)
            origin[ax] = base
            normal = [0.0, 0.0, 0.0]
            normal[ax] = 1.0
            face = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*origin), gp_Dir(*normal)),
                                           -4 * span, 4 * span, -4 * span, 4 * span).Face()
            ext = BRepExtrema_DistShapeShape(shape, face)
            ext.Perform()
            if not ext.IsDone():
                continue                     # keep the estimate for this side
            if sign < 0:
                out_lo[ax] = base + ext.Value()
            else:
                out_hi[ax] = base - ext.Value()
    return tuple(out_lo), tuple(out_hi)


def fillet_all_edges(shape, radius):
    """Round every edge of `shape` by `radius`. Analytic faces throughout.

    MEASURED: a box with all 12 edges filleted is one valid, airtight solid of **26 faces --
    6 PLANE, 12 CYLINDER and 8 SPHERE** at the corners. (An earlier version of this docstring
    said 20 faces / 10 CYLINDER / 4 SPHERE; that was wrong, and the re-measurement is
    recorded here rather than quietly corrected.) `bmesh.ops.bevel` cannot produce
    that at all -- it makes 492 faceted strips.

    Edges are collected ONCE via `_unique_edges`: a `TopExp_Explorer` visits each edge twice,
    so the previous version added every edge to the fillet builder TWICE. Same defect as the
    one `edge_curves` was caught on.
    """
    _need()
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

    try:
        edges = _unique_edges(shape)
        if not edges:
            raise KernelError("the shape has no edges to fillet")
        mk = BRepFilletAPI_MakeFillet(shape)
        for edge in edges:
            mk.Add(float(radius), edge)
        mk.Build()
        if not mk.IsDone():
            raise KernelError(
                f"could not fillet at radius {radius} -- it may exceed the size of "
                f"an adjacent face")
        return mk.Shape()
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"fillet failed: {e}") from e


def bevel_affected_edges(shape, size, indices, chamfer=False):
    """Return every source edge OCCT puts in the named bevel contours."""
    _need()
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet

    if size <= 0:
        raise KernelError(f"a bevel size must be positive (got {size})")
    edges = _unique_edges(shape)
    chosen = sorted({int(i) for i in indices})
    bad = [i for i in chosen if i < 1 or i > len(edges)]
    if bad:
        raise KernelError(
            f"edge index {bad} out of range; this solid has {len(edges)} edges "
            f"numbered 1..{len(edges)}")
    if not chosen:
        return []
    mk = (BRepFilletAPI_MakeChamfer(shape) if chamfer
          else BRepFilletAPI_MakeFillet(shape))
    for i in chosen:
        mk.Add(float(size), edges[i - 1])
    affected = set()
    for contour in range(1, mk.NbContours() + 1):
        for position in range(1, mk.NbEdges(contour) + 1):
            edge = mk.Edge(contour, position)
            index = next((i for i, candidate in enumerate(edges, 1)
                          if candidate.IsSame(edge)), None)
            if index is None:
                raise KernelError("OCCT reported a bevel-contour edge outside the source solid")
            affected.add(index)
    return sorted(affected)


def _bevel_radius_map(edge_radii):
    """OURS: id -> (start, end), or a sequence of (id, (start, end)) pairs."""
    if edge_radii is None:
        return None
    try:
        items = edge_radii.items() if hasattr(edge_radii, "items") else edge_radii
        result = {}
        for index, pair in items:
            i = int(index)
            if i != index or i in result or len(pair) != 2:
                raise ValueError("duplicate/non-integer edge id or malformed radius pair")
            radii = tuple(float(r) for r in pair)
            if not all(math.isfinite(r) and r > 0 for r in radii):
                raise ValueError("endpoint radii must be finite and positive")
            result[i] = radii
        if not result:
            raise ValueError("no endpoint radius laws were supplied")
        return result
    except (TypeError, ValueError, OverflowError) as e:
        raise KernelError(f"invalid edge_radii: {e}") from e


def bevel_edge_endpoints(shape, indices):
    """OURS: exact ordered world endpoints for radius handles, keyed by 1-based edge id.

    Order is the edge's topological walking direction, not sorted coordinates. Persist these
    coordinates with the radii: map order and even edge orientation may change on replay.
    Closed edges have coincident ends; they support constant, not independent-end laws.
    This is OCCT inspection/interop, not a Python geometry implementation.
    """
    from OCP.BRep import BRep_Tool
    from OCP.TopExp import TopExp

    edges = _unique_edges(shape)
    result = {}
    for index in indices:
        i = int(index)
        if i != index or not 1 <= i <= len(edges):
            raise KernelError(f"invalid bevel edge index {index}; expected 1..{len(edges)}")
        edge = edges[i - 1]
        vertices = (TopExp.FirstVertex_s(edge, True), TopExp.LastVertex_s(edge, True))
        if BRep_Tool.Degenerated_s(edge) or any(v.IsNull() for v in vertices):
            raise KernelError(f"edge {i} has no usable fillet endpoints")
        result[i] = [list(BRep_Tool.Pnt_s(v).Coord()) for v in vertices]
    return result


def _bevel_contour_reversed(mk, contour, anchors, index, tolerance):
    """Validate an isolated open contour, returning its direction relative to the anchors."""
    from OCP.BRep import BRep_Tool

    first, last = mk.FirstVertex(contour), mk.LastVertex(contour)
    if first.IsNull() or last.IsNull() or first.IsSame(last):
        raise KernelError(f"edge {index}: independent endpoints on a closed contour are unsupported")
    a, b = anchors
    c, d = BRep_Tool.Pnt_s(first).Coord(), BRep_Tool.Pnt_s(last).Coord()
    if math.dist(c, d) <= tolerance:
        raise KernelError(f"edge {index}: independent endpoints on a closed contour are unsupported")
    if mk.NbEdges(contour) != 1:
        raise KernelError(f"edge {index}: multi-edge tangent contour endpoints are unsupported")
    if math.dist(a, c) <= tolerance and math.dist(b, d) <= tolerance:
        return False
    if math.dist(a, d) <= tolerance and math.dist(b, c) <= tolerance:
        return True
    raise KernelError(f"edge {index}: fillet contour endpoints differ from selected edge")


def _set_bevel_laws(mk, shape, edges, chosen, size, radii, endpoints):
    """Forward endpoint laws to OCCT; return the validated anchors for the supplied laws."""
    anchors = bevel_edge_endpoints(shape, radii)
    if endpoints is not None:
        try:
            supplied = dict(endpoints)
            if set(supplied) != set(radii):
                raise ValueError("endpoint ids must match radius-law ids")
            for i, pair in supplied.items():
                if len(pair) != 2 or any(len(p) != 3 for p in pair):
                    raise ValueError("expected two XYZ endpoints per edge")
                anchors[i] = [[float(c) for c in p] for p in pair]
                if not all(math.isfinite(c) for p in anchors[i] for c in p):
                    raise ValueError("endpoint coordinates must be finite")
        except (TypeError, ValueError, OverflowError) as e:
            raise KernelError(f"invalid edge_endpoints: {e}") from e
    tolerance = _local_tolerances(shape)["build"]

    def same(a, b):
        return math.dist(a, b) <= tolerance

    actual = bevel_edge_endpoints(shape, radii)
    for i, (a, b) in anchors.items():
        c, d = actual[i]
        if not ((same(a, c) and same(b, d)) or (same(a, d) and same(b, c))):
            raise KernelError(f"edge {i}: radius endpoints do not match the selected edge")
    for i in chosen:
        mk.Add(edges[i - 1])
    assigned = {}
    for i in chosen:
        contour = mk.Contour(edges[i - 1])
        if contour <= 0:
            raise KernelError(f"edge {i} has no fillet contour")
        r0, r1 = radii.get(i, (float(size), float(size)))
        if r0 != r1 and _bevel_contour_reversed(mk, contour, anchors[i], i, tolerance):
            r0, r1 = r1, r0
        law = (r0, r1)
        if contour in assigned and assigned[contour] != law:
            raise KernelError(f"edge {i}: conflicting radius laws on one tangent contour")
        assigned[contour] = law
        if r0 == r1:
            mk.SetRadius(r0, contour, 1)
        else:
            # MEASURED OCP overload: R1/R2 refer to FirstVertex/LastVertex of the CONTOUR.
            mk.SetRadius(r0, r1, contour, 1)
    return anchors


def bevel_edge_sections(shape, size, indices, edge_radii=None, edge_endpoints=None):
    """OURS: exact, cacheable endpoint sections without building a candidate solid.

    Return {edge_id: [start_record, end_record]} in anchored order. Each record contains
    anchor XYZ, radius, curve (an exact arc recipe with center/xaxis/yaxis/normal/radius/
    start/end angles in radians), circle_center XYZ and grip_point XYZ (arc midpoint).
    Every coordinate is world-space. The UI may sample these arcs for DISPLAY only.

    Uses OCCT Simulate, never Build/Shape, meshing, or document/master registration. It is
    independent of solid Preview; call on edits and cache, NOT during draw. Simulation is
    not a promise that a subsequent trimmed/closed solid build succeeds. Refuses closed,
    multi-edge and multi-surface contours rather than guessing their endpoint sections.
    """
    _need()
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.ElCLib import ElCLib
    from OCP.gp import gp_Circ

    try:
        if not math.isfinite(size) or size <= 0:
            raise KernelError("a section-preview size must be finite and positive")
        anchors = bevel_edge_endpoints(shape, indices)
        if not anchors:
            raise KernelError("no edges were named for section preview")
        radii = _bevel_radius_map(edge_radii)
        if radii is None:
            radii = {i: (float(size), float(size)) for i in anchors}
        if not set(radii) <= set(anchors):
            raise KernelError("edge_radii names an edge outside the selected edges")
        edges = _unique_edges(shape)
        mk = BRepFilletAPI_MakeFillet(shape)
        anchors.update(_set_bevel_laws(
            mk, shape, edges, list(anchors), size, radii, edge_endpoints))
        tolerance = _local_tolerances(shape)["build"]
        result = {}
        for i, ends in anchors.items():
            contour = mk.Contour(edges[i - 1])
            reverse = _bevel_contour_reversed(mk, contour, ends, i, tolerance)
            mk.Simulate(contour)
            if mk.NbSurf(contour) != 1:
                raise KernelError(f"edge {i}: section preview requires one fillet surface")
            sections = mk.Sect(contour, 1)
            if sections is None or sections.Length() < 2:
                raise KernelError(f"edge {i}: OCCT did not supply two endpoint sections")
            positions = [sections.Lower(), sections.Upper()]
            if reverse:
                positions.reverse()
            records = []
            for anchor, radius, position in zip(ends, radii.get(i, (size, size)), positions):
                circle = gp_Circ()
                start, end = sections.Value(position).Get(circle)
                actual_radius = circle.Radius()
                if (not math.isfinite(actual_radius) or actual_radius <= 0
                        or abs(actual_radius - radius) > tolerance
                        or not math.isfinite(start) or not math.isfinite(end) or end <= start):
                    raise KernelError(f"edge {i}: ambiguous endpoint section radius or angles")
                axis = circle.Position()
                center = list(axis.Location().Coord())
                normal = list(axis.Direction().Coord())
                # The section must be at this source endpoint, not an interior sample or a
                # section of a neighbouring contour. OCCT supplies its exact support plane.
                if abs(sum((anchor[j] - center[j]) * normal[j] for j in range(3))) > tolerance:
                    raise KernelError(f"edge {i}: section plane does not contain its endpoint")
                curve = {"ctype": "arc", "center": center,
                         "xaxis": list(axis.XDirection().Coord()),
                         "yaxis": list(axis.YDirection().Coord()), "normal": normal,
                         "radius": actual_radius, "start": start, "end": end}
                records.append({"anchor": list(anchor), "radius": actual_radius,
                                "curve": curve, "circle_center": list(center),
                                "grip_point": list(ElCLib.Value_s((start + end) / 2, circle).Coord())})
            result[i] = records
        return result
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"fillet section preview failed: {e}") from e


def bevel_edges(shape, size, indices=None, chamfer=False, edge_radii=None,
                edge_endpoints=None):
    """Fillet or chamfer the edges named by `indices` (1-based, from `edge_curves`).

    `indices=None` means EVERY edge, which is what `fillet_all_edges`/`chamfer_all_edges`
    do. Naming a subset is what lets a user round ONE edge of a solid, which is most of what
    the reference CAD application's `FilletEdge` is for.

    The indices are positions in OCCT's unique-shape map, carried on each dict from
    `edge_curves` as `"index"`. An index that does not exist is refused BY NAME rather than
    ignored -- quietly beveling nothing, or the wrong edge, is the failure mode that matters
    here.

    OURS: `edge_radii` maps ids to (start, end) radii (or is a list of such id/pair entries).
    With no indices, only those ids are selected; other explicitly selected edges use size.
    Ends follow `bevel_edge_endpoints`, or explicit `edge_endpoints` anchors. Unequal radii
    use OCCT's native law, never an average. Only isolated open contours are supported for
    unequal radii; tangent chains/closed contours and conflicting constant laws refuse.
    """
    _need()
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet

    if not math.isfinite(size) or size <= 0:
        raise KernelError(f"a bevel size must be finite and positive (got {size})")
    radii = _bevel_radius_map(edge_radii)
    if radii is not None and chamfer:
        raise KernelError("endpoint radius laws are not supported for chamfers")
    if edge_endpoints is not None and radii is None:
        raise KernelError("edge_endpoints requires edge_radii")
    if indices is None and radii is not None:
        indices = list(radii)
    edges = _unique_edges(shape)
    if not edges:
        raise KernelError("the shape has no edges to bevel")
    if indices is None:
        chosen = list(range(1, len(edges) + 1))
    else:
        chosen = sorted({int(i) for i in indices})
        bad = [i for i in chosen if i < 1 or i > len(edges)]
        if bad:
            raise KernelError(
                f"edge index {bad} out of range; this solid has {len(edges)} edges "
                f"numbered 1..{len(edges)}")
        if not chosen:
            raise KernelError("no edges were named to bevel")
    if radii is not None and not set(radii) <= set(chosen):
        raise KernelError("edge_radii names an edge outside the selected edges")
    try:
        mk = (BRepFilletAPI_MakeChamfer(shape) if chamfer
              else BRepFilletAPI_MakeFillet(shape))
        if radii is None:
            for i in chosen:
                mk.Add(float(size), edges[i - 1])
        else:
            _set_bevel_laws(mk, shape, edges, chosen, size, radii, edge_endpoints)
        mk.Build()
        if not mk.IsDone():
            raise KernelError(
                f"could not bevel at size {size} -- it may exceed the size of an "
                f"adjacent face")
        result = mk.Shape()
        if not is_valid(result) or not is_airtight(result):
            raise KernelError("the beveled result is invalid or not airtight")
        return result
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"bevel failed: {e}") from e


def chamfer_all_edges(shape, distance):
    """Flat-cut every edge of `shape` by `distance`. Analytic PLANE faces throughout.

    the reference CAD application's `ChamferEdge`. A chamfer is a planar cut, so unlike a fillet the new faces are
    PLANES -- the honest answer, not a shortcut: there is no curvature to preserve.

    `BRepFilletAPI_MakeChamfer.Add` has a `(Dis, E)` overload, so the adjacent face does
    not need to be supplied and no edge-to-face ancestor map is necessary.
    """
    _need()
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

    try:
        edges = _unique_edges(shape)
        if not edges:
            raise KernelError("the shape has no edges to chamfer")
        mk = BRepFilletAPI_MakeChamfer(shape)
        for edge in edges:
            mk.Add(float(distance), edge)
        mk.Build()
        if not mk.IsDone():
            raise KernelError(
                f"could not chamfer at distance {distance} -- it may exceed the size "
                f"of an adjacent face")
        return mk.Shape()
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"chamfer failed: {e}") from e


# --------------------------------------------------------------------------
# STEP -- the manufacturing deliverable
# --------------------------------------------------------------------------

def compound(shapes):
    """Return one compound shape holding all input shapes for a multi-solid STEP file."""
    _need()
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    shapes = list(shapes)
    if len(shapes) == 1:
        return shapes[0]
    out = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(out)
    for s in shapes:
        b.Add(out, s)
    return out


#: OCCT STEP unit names (`write.step.unit` / `xstep.cascade.unit`) for the reference CAD application units.
STEP_UNITS = {"Millimeters": "MM", "Centimeters": "CM", "Meters": "M",
              "Kilometers": "KM", "Inches": "INCH", "Feet": "FT", "Microns": "UM",
              "Miles": "MI", "Mils": "MIL", "Microinches": "UIN"}


def import_step(path, unit="MM"):
    """Read a STEP file into OCCT shapes converted into `unit` (an OCCT name).

    MEASURED (an inch 1x2x3 box written by `export_step`): the reader converts the file's
    length unit into `xstep.cascade.unit` -- cascade MM gives 25.4 x 50.8 x 76.2, INCH
    gives 1 x 2 x 3, M gives 0.0254... -- and `FileUnits` reports ``['INCH']``. The static
    is process-global, so it is restored afterwards.

    Returns ``{"solids": [...], "shells": [...], "faces": [...], "edges": [nurbs dict],
    "file_units": [names]}`` -- the top-level pieces, each only once (a face inside a
    shell is part of the shell). Raises KernelError naming the reader's status.
    """
    _need()
    from OCP.collections import Sequence_TCollection_AsciiString as _Seq
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    reader = STEPControl_Reader()          # creating it registers the unit statics
    saved = Interface_Static.CVal_s("xstep.cascade.unit")
    try:
        if not Interface_Static.SetCVal_s("xstep.cascade.unit", str(unit)):
            raise KernelError(f"OCCT does not know the unit {unit!r}")
        status = reader.ReadFile(str(path))
        if status != IFSelect_ReturnStatus.IFSelect_RetDone:
            raise KernelError(f"the STEP reader could not read {path} ({status.name})")
        lengths, angles, solid_angles = _Seq(), _Seq(), _Seq()
        reader.FileUnits(lengths, angles, solid_angles)
        file_units = [lengths.Value(i).ToCString() for i in range(1, lengths.Length() + 1)]
        reader.TransferRoots()
        shape = reader.OneShape()
    finally:
        Interface_Static.SetCVal_s("xstep.cascade.unit", saved or "MM")
    out = {"solids": [], "shells": [], "faces": [], "edges": [], "file_units": file_units}
    if shape is None or shape.IsNull():
        return out
    for kind, avoid, key, cast in ((TopAbs_SOLID, None, "solids", TopoDS.Solid),
                                   (TopAbs_SHELL, TopAbs_SOLID, "shells", TopoDS.Shell),
                                   (TopAbs_FACE, TopAbs_SHELL, "faces", TopoDS.Face)):
        exp = TopExp_Explorer(shape, kind) if avoid is None else \
            TopExp_Explorer(shape, kind, avoid)
        while exp.More():
            out[key].append(cast(exp.Current()))
            exp.Next()
    exp = TopExp_Explorer(shape, TopAbs_EDGE, TopAbs_FACE)
    while exp.More():
        out["edges"].append(_edge_dict(TopoDS.Edge(exp.Current())))
        exp.Next()
    return out


def export_step(shape, path, unit="MM"):
    """Write `shape` to `path` as STEP, keeping each face's natural surface type.

    `STEPControl_AsIs` is the mode that matters: it keeps a cylinder a
    `CYLINDRICAL_SURFACE` in the file rather than converting everything to splines.
    MEASURED on a drilled box -- the file contains
    ``#71 = CYLINDRICAL_SURFACE('',#72,3.);``.

    `unit` (an OCCT name from `STEP_UNITS`) is the unit the shape's numbers use and
    the unit the file declares. MEASURED on a 1x1x1 box: setting only `write.step.unit`
    = INCH CONVERTS (the file says INCH but holds 0.03937 -- OCCT took the numbers as
    millimetres); setting `xstep.cascade.unit` too writes `CONVERSION_BASED_UNIT('INCH')`
    with coordinates 1.0. Both are process-global statics, so they are restored after.

    Returns the number of bytes written. Raises KernelError rather than writing a file
    the caller might assume succeeded.
    """
    _need()
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType

    # MEASURED: the unit statics do not EXIST until the STEP controller is initialised --
    # before the first `STEPControl_Writer()`, `SetCVal_s` returns False and `CVal_s` ''.
    writer = STEPControl_Writer()
    keys = ("xstep.cascade.unit", "write.step.unit")
    saved = {k: Interface_Static.CVal_s(k) for k in keys}
    try:
        for k in keys:
            if not Interface_Static.SetCVal_s(k, str(unit)):
                raise KernelError(f"OCCT does not know the STEP unit {unit!r}")
        # `mode` is an ENUM, not an int -- passing 0 raises a pybind TypeError.
        writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
        writer.Write(path)
    except KernelError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KernelError(f"STEP export failed: {e}") from e
    finally:
        for k, v in saved.items():
            Interface_Static.SetCVal_s(k, v)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise KernelError(f"STEP export wrote nothing to {path}")
    return os.path.getsize(path)
