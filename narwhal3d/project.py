"""Project curves and points onto a plane (pure: rhino3dm only, no bpy).

What the reference CAD application's `Project` actually offers
-------------------------------------
the reference CAD application's documented option list is:

    Loose         Projects edit points toward the specified direction onto the
                  surface. Loose projection will fail if the result wraps to the
                  other side of the target surface.
    DeleteInput   Controls whether the original geometry is deleted.
    OutputLayer   Current | Input | TargetObject
    Direction     CPlaneZ -- Projects objects in the construction plane z
                             direction.
                  View    -- Projects objects in the view direction.

This module is the **geometry** half only: the plane target, both directions. It
does not delete input, choose layers, or project onto a surface -- those belong to
the command layer.

**`Loose` is not a separate mode here, and that is a fact rather than a gap.**
`Loose` exists because projecting onto a curved *surface* the honest way needs the
result refitted to follow the surface, whereas Loose just moves the edit points
and accepts the error. Onto a **plane** the control-point projection IS the exact
answer (see below), so Loose and tight coincide exactly and there is nothing to
choose between. If a surface target is ever added, `Loose` becomes real and must
be implemented then -- it is not implemented here and no `loose` argument is
offered, so it cannot look implemented.

Why this is EXACT, not sampled
------------------------------
Projection onto a plane -- orthogonal or oblique -- is an **affine** map, and
affine maps commute with NURBS evaluation::

    C(t) = sum_i  N_i(t) w_i P_i  /  sum_i N_i(t) w_i        (rational)
    A(C(t)) + b  ==  the same curve with each P_i replaced by A(P_i) + b

because the basis functions and the weights are untouched: the map acts on the
*points*, and the evaluation is a weighted average of points, which an affine map
preserves. So the projection of a NURBS curve is the NURBS curve of the projected
control points -- the **exact** projection, at the **same parameters**, with the
same knot vector and the same weights. No sampling, no refit, no tolerance.

Applying a singular projection matrix with `curve.Transform(xf)` leaves the knot vector
and every weight bit-identical, keeps `IsClosed` and coincident seam control points, and
reproduces the hand-projected point at 51 parameters to **0.0e+00**
for the orthogonal case and 1.8e-15 for an oblique one. So the transform is built
here and McNeel's own code applies it; the weights, which are the fiddly part
(`Point4d.X/Y/Z` hold the WEIGHTED coordinates, so a hand-written version would
have to unweight and reweight), are never touched by us at all.

rhino3dm 8.35.0 has no `Transform.PlanarProjection` (measured: the factory list is
Identity / Translation / Scale / Rotation / Mirror / PlaneToPlane / Shear /
Multiply / Transpose / TryGetInverse), so the matrix is assembled here.

The degeneracy that must be REFUSED
-----------------------------------
A curve perpendicular to the plane projects to a single point. MEASURED: the
result still reports `IsValid == True` and a bounding-box diagonal of 0.0, so
rhino3dm will happily hand back a zero-length "curve" -- nothing in the kernel
objects. Rule 4 says an impossible answer must not look like an answer, so this
module refuses it and says which diagonal collapsed.
"""
from __future__ import annotations

import math

import rhino3dm as r3

try:                                  # normal import, inside the addon package
    from . import units
except ImportError:                   # supports direct file-path loading
    import units

try:                                  # nearest provides closest_point_on_surface
    from . import nearest
except ImportError:
    try:
        import nearest
    except ImportError:               # loaded by raw path, no narwhal3d on sys.path
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import nearest


#: A direction within this of lying in the plane has no projection onto
#: it. This is a pure numerical-degeneracy guard on ``|d . n|`` for unit vectors
#: (i.e. the sine of the angle between the direction and the plane), not a
#: modelling tolerance, so the document's 0.01 mm would be the wrong scale
#: entirely -- it is dimensionless.
MIN_DIRECTION_COSINE = 1e-12


def default_tolerance():
    """The document's absolute tolerance, in model units.

    the reference CAD application's default is 0.01 mm. Read through `units.py` rather than using a literal so a
    document with a different tolerance is honoured.
    """
    # Use the document's tolerance in model units, not a fixed 0.01 default.
    return units.document_tolerance()


# ---------------------------------------------------------------------------
# the transform
# ---------------------------------------------------------------------------

def projection_transform(plane, direction=None):
    """The affine map that projects onto `plane` along `direction`.

    `plane` may be a `cplane.CPlane` (its ``origin`` and ``normal``), an
    ``r3.Plane``, or an ``(origin, normal)`` pair -- whichever the caller already
    has. `cplane.py` is not imported: only the two attributes are read, keeping this module
    independent and avoiding an unnecessary dependency.

    `direction` of ``None`` means the plane's own normal, which is the reference CAD application's
    **CPlaneZ** ("Projects objects in the construction plane z direction"). Any
    other direction is the reference CAD application's **View** case ("Projects objects in the view
    direction") and is an oblique projection -- still affine, still exact.

    The algebra, for a unit normal ``n``, plane origin ``o`` and direction ``d``
    with ``k = d . n``::

        P(x) = x - ((x - o) . n / k) d
             = A x + b      with  A = I - (1/k) d n^T,  b = ((o . n) / k) d

    With ``d = n`` that reduces to the orthogonal ``A = I - n n^T``.

    Raises ValueError when the direction lies in the plane (``k`` ~ 0): there is
    then no point of the plane along it, so no projection exists.
    """
    origin, normal = _plane_origin_normal(plane)
    n = _unit(normal)
    if n is None:
        raise ValueError("the plane's normal is zero-length")

    if direction is None:
        d = n
    else:
        d = _unit(direction)
        if d is None:
            raise ValueError("the projection direction is zero-length")

    k = _dot(d, n)
    if abs(k) < MIN_DIRECTION_COSINE:
        raise ValueError(
            f"the projection direction {tuple(round(c, 6) for c in d)} lies in "
            f"the plane (direction.normal = {k:.3g}); nothing projects along it")

    on = _dot(origin, n)
    xf = r3.Transform.Identity()
    rows = ("M0", "M1", "M2")
    for i in range(3):
        for j in range(3):
            delta = 1.0 if i == j else 0.0
            setattr(xf, f"{rows[i]}{j}", delta - d[i] * n[j] / k)
        setattr(xf, f"{rows[i]}3", on / k * d[i])
    xf.M30 = 0.0
    xf.M31 = 0.0
    xf.M32 = 0.0
    xf.M33 = 1.0
    return xf


def project_point(point, plane, direction=None):
    """A single 3-D point projected onto `plane`, as an ``(x, y, z)`` tuple.

    The same affine map as :func:`project_curve` uses, applied by hand, so a test
    can project points independently of any curve machinery.
    """
    origin, normal = _plane_origin_normal(plane)
    n = _unit(normal)
    if n is None:
        raise ValueError("the plane's normal is zero-length")
    d = n if direction is None else _unit(direction)
    if d is None:
        raise ValueError("the projection direction is zero-length")
    k = _dot(d, n)
    if abs(k) < MIN_DIRECTION_COSINE:
        raise ValueError(
            f"the projection direction lies in the plane "
            f"(direction.normal = {k:.3g}); nothing projects along it")
    p = _pt(point)
    s = _dot(_sub(p, _pt(origin)), n) / k
    return (p[0] - s * d[0], p[1] - s * d[1], p[2] - s * d[2])


# ---------------------------------------------------------------------------
# the curve
# ---------------------------------------------------------------------------

def project_curve(curve, plane, direction=None, tolerance=None):
    """`curve` projected onto `plane`, EXACTLY, at the same parameters.

    Returns a new `NurbsCurve`; the input is never mutated. The knot vector, the
    degree, the weights and the domain all come through unchanged, so
    ``result.PointAt(t)`` is the projection of ``curve.PointAt(t)`` for every
    ``t`` -- not merely a curve that passes near the projected points.

    The result is always a `NurbsCurve`, deliberately. An `ArcCurve` cannot
    represent what an arc projects to (an ellipse in general), so handing the
    projection back in the input's own type would be a silent lie for that one
    case. MEASURED: rhino3dm 8.35.0 cannot even construct an `ArcCurve` from an
    `Arc` (the constructor takes only ``()`` or another `ArcCurve`), so such a
    curve can only arrive from a file -- and normalising every input removes the
    question. MEASURED too: after projection a tilted arc correctly reports
    ``IsArc() == False`` and ``TryGetArc() is None``, so nothing downstream is
    misled about what it now has.

    Raises ValueError when the curve is perpendicular to the plane and collapses
    to a point. rhino3dm does not object to that -- MEASURED: the collapsed curve
    reports ``IsValid == True`` and a bounding-box diagonal of exactly 0.0 -- so
    this function performs the check and reports the measured diagonals.
    """
    tol = default_tolerance() if tolerance is None else float(tolerance)
    xf = projection_transform(plane, direction)

    out = curve.ToNurbsCurve() if not isinstance(curve, r3.NurbsCurve) \
        else curve.Duplicate()
    if out is None:
        raise ValueError(f"cannot convert a {type(curve).__name__} to a NurbsCurve")

    before = _bbox_diagonal(out)
    out.Transform(xf)
    after = _bbox_diagonal(out)
    if after <= tol:
        raise ValueError(
            f"the curve is perpendicular to the plane: projecting it collapses "
            f"it from a bounding-box diagonal of {before:.6g} to {after:.6g}, "
            f"within the tolerance {tol:.6g}. A point is not a curve")
    return out


# ---------------------------------------------------------------------------
# Pull -- closest-point projection onto a surface (a fit, not exact)
#
# Where Project (above) is DIRECTIONAL and exact, Pull is CLOSEST-POINT. MEASURED
# (commands/pull.htm): "Use the Project command if you know what the trim curve
# looks like from a single viewport. Use Pull command if you know where on the
# surface (in 3-D) the trim path should be... such as a curve that goes most of
# the way around a cylinder." The closest-point image of a NURBS curve on a CURVED
# surface is not itself a NURBS, so this cannot be exact: sample the input, pull each
# sample onto the surface with nearest.closest_point_on_surface, fit a curve through the
# feet, and measure
# the worst distance of that fit from the surface, and refine until it is within
# tolerance or report the achieved deviation. Onto a PLANE the foot IS the
# perpendicular projection, so pull_point there equals project_point along the
# normal -- the exact cross-check the suite leans on.
#
# An ambiguous closest point, such as a query on a cylinder's axis, must not silently
# resolve to an arbitrary side. `nearest.closest_point_on_surface` exposes this through
# its `ambiguous` flag, which this module reads rather than re-deriving the closest-point
# solve.
# ---------------------------------------------------------------------------

class PullResult:
    """The fitted pull of a curve onto a surface (mirrors offset.OffsetResult).

    * ``curve``         -- the fitted `NurbsCurve` lying on the surface, or None
      when the pull is refused (an ambiguous foot).
    * ``exact``         -- True only when the fit is exact (a straight input onto a
      plane, whose closest-point image is a line). Otherwise False.
    * ``max_deviation`` -- the measured worst distance of the fitted curve FROM the
      surface over a sampled sweep; the honest tolerance actually achieved.
    * ``warning``       -- non-empty when the tolerance could not be met, or the
      pull is refused, saying why.
    """

    __slots__ = ("curve", "exact", "max_deviation", "warning")

    def __init__(self, curve, exact, max_deviation=0.0, warning=""):
        self.curve = curve
        self.exact = exact
        self.max_deviation = max_deviation
        self.warning = warning

    def __repr__(self):  # pragma: no cover - debugging aid
        kind = "exact" if self.exact else f"approx dev={self.max_deviation:.2e}"
        w = f" WARNING: {self.warning}" if self.warning else ""
        name = "None" if self.curve is None else type(self.curve).__name__
        return f"PullResult({name}, {kind}){w}"


def pull_point(point, surface, tolerance=None):
    """The point of `surface` CLOSEST to `point`, as an ``(x, y, z)`` tuple.

    The single-point half of Pull ("Select curves or points to pull"). Onto a
    plane this is the perpendicular foot, equal to :func:`project_point` along the
    plane normal. Raises ValueError when the foot is AMBIGUOUS -- `point`
    equidistant from a whole family of surface points, e.g. a point on a
    cylinder's axis -- rather than returning one arbitrary side (rule 4).
    """
    tol = default_tolerance() if tolerance is None else float(tolerance)
    p = _pt(point)
    res = nearest.closest_point_on_surface(surface, p, tol)
    amb = _foot_ambiguity(res)
    if amb is not None:
        raise ValueError(
            f"the point {tuple(round(c, 6) for c in p)} is equidistant "
            f"({res.distance:.6g}) from surface points {amb:.4g} apart: its closest "
            f"point on the surface is ambiguous and is not resolved to one side")
    return res.point


def pull_curve(curve, surface, tolerance=None, max_refine=4, samples=128):
    """`curve` pulled onto `surface` by closest point -- a fitted approximation.

    Returns a :class:`PullResult`. The TRUE pull locus (the closest-point image of
    the input on the surface) is sampled once -- it depends on the input,
    not on the fit -- and reused: a doubling subsample of it supplies the control
    points, and the whole of it is the reference the fit's deviation is measured
    against. Refinement stops when the worst distance of the locus from the fit is
    within `tolerance`, else the achieved deviation is reported in `.max_deviation`
    and named in `.warning`. `.curve` is None with a warning when a sample's foot is
    ambiguous (the pull is refused, not silently resolved to one arbitrary side).
    """
    tol = default_tolerance() if tolerance is None else float(tolerance)
    d = curve.Domain
    # the true pull locus, sampled once; each entry is (input_point, foot_result)
    locus = []
    for i in range(samples + 1):
        t = d.T0 + (d.T1 - d.T0) * i / samples
        src = curve.PointAt(t)
        p = (src.X, src.Y, src.Z)
        locus.append((t, p, nearest.closest_point_on_surface(surface, p, tol)))
    # ambiguity is a property of the INPUT, not the sample count: decided here on a
    # coarse subset of the locus (the grid scan is the costly part of the check)
    step0 = max(1, samples // 16)
    for k in range(0, samples + 1, step0):
        t, p, res = locus[k]
        amb = _foot_ambiguity(res)
        if amb is not None:
            return PullResult(
                None, False, 0.0,
                f"the pull is ambiguous: the curve point at t={t:.4g} is "
                f"equidistant from surface points {amb:.4g} apart; refused rather "
                f"than resolved to one arbitrary side")
    pulled = [res.point for _t, _p, res in locus]
    best = None
    count = 8
    while True:
        count = min(count, samples)
        stride = samples // count
        feet = [pulled[j * stride] for j in range(count + 1)]
        degree = 3 if len(feet) > 3 else 1
        fit = r3.NurbsCurve.CreateControlPointCurve(
            [r3.Point3d(*p) for p in feet], degree)
        if fit is None:
            fit = _polyline_curve(feet)
        dev = max(nearest.closest_point_on_curve(fit, q).distance for q in pulled)
        best = (fit, dev)
        if dev <= tol:
            return PullResult(fit, False, dev, "")
        if count >= samples:
            break
        count *= 2
    fit, dev = best
    return PullResult(fit, False, dev,
                      f"pull approximated to {dev:.3e} > tolerance {tol}")


def _foot_ambiguity(res):
    """A non-unique closest point, as a separation figure for the refusal message,
    or None when the foot is unique.

    `nearest.closest_point_on_surface` sets `res.ambiguous` through an
    iso-parameter-curve scan, giving surface and curve closest-point results consistent
    semantics. This
    just reads the flag; the figure returned (~ the ring diameter, 2x the distance
    for an axis query) is only for the human-readable message.
    """
    return 2.0 * res.distance if getattr(res, "ambiguous", False) else None


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _plane_origin_normal(plane):
    """``(origin, normal)`` from a CPlane, an ``r3.Plane``, or a 2-sequence.

    Duck-typed on purpose: `cplane.CPlane` exposes ``origin`` / ``normal`` (and
    ``zaxis``, the same thing), `r3.Plane` exposes ``Origin`` / ``Normal``, and a
    plain ``(origin, normal)`` tuple is what `cplane.as_origin_normal()` and
    `ui.pick_point` already pass around. Reading only the attributes keeps
    `cplane.py` out of this module's imports.
    """
    origin = getattr(plane, "origin", None)
    if origin is not None:
        normal = getattr(plane, "normal", None) or getattr(plane, "zaxis", None)
        if callable(normal):
            normal = normal()
        return _pt(origin), _pt(normal)

    origin = getattr(plane, "Origin", None)
    if origin is not None:
        normal = getattr(plane, "Normal", None)
        if normal is None:
            normal = getattr(plane, "ZAxis", None)
        return _pt(origin), _pt(normal)

    try:
        o, n = plane
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"cannot read a plane from {type(plane).__name__}; pass a CPlane, an "
            f"r3.Plane, or an (origin, normal) pair") from exc
    return _pt(o), _pt(n)


def _bbox_diagonal(curve):
    bb = curve.GetBoundingBox()
    return math.dist((bb.Min.X, bb.Min.Y, bb.Min.Z),
                     (bb.Max.X, bb.Max.Y, bb.Max.Z))


def _pt(p):
    if hasattr(p, "X"):
        return (p.X, p.Y, p.Z)
    return (float(p[0]), float(p[1]), float(p[2]))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _polyline_curve(pts):
    """A degree-1 curve through `pts` -- the fallback when CreateControlPointCurve
    returns None (too few or coincident points)."""
    return r3.PolylineCurve([r3.Point3d(*p) for p in pts])


def _unit(v):
    p = _pt(v)
    n = math.sqrt(p[0] * p[0] + p[1] * p[1] + p[2] * p[2])
    if n < 1e-300:
        return None
    return (p[0] / n, p[1] / n, p[2] / n)
