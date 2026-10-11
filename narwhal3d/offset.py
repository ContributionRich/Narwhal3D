"""Curve offset in a plane (pure: rhino3dm only, no bpy).

rhino3dm has no `Curve.Offset`. Rhino's Offset command offers these options; this module
builds only the geometry:

    Distance        the offset distance
    Loose  Yes|No   Yes offsets edit points (segments break at corners); No offsets
                    within Tolerance (more control points)
    Corner          Sharp | Round | Smooth | Chamfer  -- how convex corners are filled
    Trim   Yes|No   trim self-intersections at corners
    ThroughPoint    offset through a picked point instead of a distance
    Tolerance       offset tolerance (0 = document tolerance)
    BothSides       offset on both sides
    InCPlane        offset in the CPlane instead of the curve's own plane
    Cap    None|Flat|Round   close the gap at open ends
    OutputLayer     Current | Input

An exact NURBS offset does not exist in general -- the offset of
a NURBS curve is usually NOT a NURBS curve, so every CAD system APPROXIMATES it to
a tolerance. Here the EXACT cases are built exactly (a line -> a line, a
circle/arc -> a concentric circle/arc with radius +/- d, a polyline -> a polyline
with the chosen corner), and the general NURBS case is a fitted approximation
whose ACHIEVED maximum deviation from the true offset locus is measured and
reported. `Round`/`Smooth` corner fills are not implemented and are reported, not
faked. An inward offset larger than the radius of curvature (self-intersecting) is
reported, not silently returned.
"""
import math

import rhino3dm as r3


class OffsetResult:
    __slots__ = ("curve", "exact", "max_deviation", "warning")

    def __init__(self, curve, exact, max_deviation=0.0, warning=""):
        self.curve = curve
        self.exact = exact
        self.max_deviation = max_deviation
        self.warning = warning

    def __repr__(self):
        kind = "exact" if self.exact else f"approx dev={self.max_deviation:.2e}"
        w = f" WARNING: {self.warning}" if self.warning else ""
        return f"OffsetResult({'None' if self.curve is None else type(self.curve).__name__}, {kind}){w}"


def curve_offset(curve, distance, normal=(0.0, 0.0, 1.0), corner="Sharp",
                 tolerance=1e-3):
    """Offset `curve` by `distance` in the plane with the given `normal`.

    Positive `distance` offsets toward ``cross(normal, tangent)`` (the curve's
    left when looking along +normal); negative offsets the other side. Returns an
    `OffsetResult`; `.curve` is None when the offset is degenerate or the corner
    style is unimplemented, with `.warning` saying why.
    """
    n = _unit(normal)
    if abs(distance) < 1e-15:
        return OffsetResult(curve.Duplicate(), True, 0.0, "")

    circle = curve.TryGetCircle()
    if circle is not None:
        return _offset_circle(curve, circle, distance, n, tolerance)

    arc = curve.TryGetArc()
    if arc is not None:
        return _offset_arc(curve, arc, distance, n, tolerance)

    if _is_linear(curve):
        return _offset_line(curve, distance, n)

    verts = _polyline_verts(curve)
    if verts is not None and len(verts) >= 2:
        return _offset_polyline(verts, distance, n, corner)

    return _offset_free_form_approx(curve, distance, n, tolerance)


# ---------------------------------------------------------------------------
# exact cases
# ---------------------------------------------------------------------------

def _offset_circle(curve, circle, distance, n, tolerance):
    r = circle.Radius
    center = (circle.Center.X, circle.Center.Y, circle.Center.Z)
    p0 = _pt(curve.PointAt(curve.Domain.T0))
    q = _offset_point(curve, curve.Domain.T0, distance, n)
    new_r = _signed_radius(center, p0, q)
    if new_r <= tolerance:
        return OffsetResult(None, True, 0.0,
                            f"inward offset {abs(distance)} >= radius {r}; the "
                            f"offset degenerates (Rhino trims these)")
    circle.Radius = new_r
    return OffsetResult(r3.NurbsCurve.CreateFromCircle(circle), True, 0.0, "")


def _offset_arc(curve, arc, distance, n, tolerance):
    r = arc.Radius
    center = (arc.Center.X, arc.Center.Y, arc.Center.Z)
    p0 = _pt(curve.PointAt(curve.Domain.T0))
    q = _offset_point(curve, curve.Domain.T0, distance, n)
    new_r = _signed_radius(center, p0, q)
    if new_r <= tolerance:
        return OffsetResult(None, True, 0.0,
                            f"inward offset {abs(distance)} >= radius {r}; "
                            f"degenerate")
    arc.Radius = new_r
    return OffsetResult(r3.NurbsCurve.CreateFromArc(arc), True, 0.0, "")


def _signed_radius(center, on_curve_point, offset_point):
    """New radius of a concentric offset, NEGATIVE when the offset crossed the
    centre (an inward offset larger than the radius), so the caller can reject it
    instead of returning the flipped-to-the-other-side circle."""
    radial = _sub(on_curve_point, center)
    qv = _sub(offset_point, center)
    qr = math.sqrt(qv[0] * qv[0] + qv[1] * qv[1] + qv[2] * qv[2])
    return qr if _dot(qv, radial) >= 0.0 else -qr


def _offset_line(curve, distance, n):
    t = curve.TangentAt(curve.Domain.T0)
    d = _unit(_cross(n, (t.X, t.Y, t.Z)))
    a = _add(_pt(curve.PointAtStart), _scale(d, distance))
    b = _add(_pt(curve.PointAtEnd), _scale(d, distance))
    return OffsetResult(r3.LineCurve(r3.Point3d(*a), r3.Point3d(*b)), True, 0.0, "")


def _offset_polyline(verts, distance, n, corner):
    closed = _dist(verts[0], verts[-1]) < 1e-9
    v = verts[:-1] if closed else verts
    m = len(v)
    # per-segment offset endpoints
    seg = []
    idxs = range(m) if closed else range(m - 1)
    for i in idxs:
        a = v[i]
        b = v[(i + 1) % m]
        d = _unit(_cross(n, _sub(b, a)))
        seg.append((_add(a, _scale(d, distance)), _add(b, _scale(d, distance))))

    style = corner.lower()
    if style in ("round", "smooth"):
        return OffsetResult(None, True, 0.0,
                            f"corner style '{corner}' not implemented "
                            f"(Sharp and Chamfer are)")
    if style == "chamfer":
        out = []
        for (a, b) in seg:
            out.append(a)
            out.append(b)
        if closed:
            out.append(out[0])
        return OffsetResult(_polyline_curve(out), True, 0.0, "")

    # Sharp (default): meet adjacent offset segments at their intersection
    ns = len(seg)
    if closed:
        out = []
        for i in range(ns):
            a0, b0 = seg[i]
            a1, b1 = seg[(i + 1) % ns]
            p = _line_line_point(a0, b0, a1, b1)
            out.append(p if p is not None else b0)
        out.append(out[0])
        return OffsetResult(_polyline_curve(out), True, 0.0, "")
    out = [seg[0][0]]                       # start cap = first segment's A
    for i in range(ns - 1):
        a0, b0 = seg[i]
        a1, b1 = seg[i + 1]
        p = _line_line_point(a0, b0, a1, b1)
        out.append(p if p is not None else b0)   # parallel -> shared point
    out.append(seg[-1][1])                  # end cap = last segment's B
    return OffsetResult(_polyline_curve(out), True, 0.0, "")


# ---------------------------------------------------------------------------
# general (approximate) case
# ---------------------------------------------------------------------------

def _offset_free_form_approx(curve, distance, n, tolerance, max_samples=512):
    """Approximate a free-form NURBS offset and report the achieved deviation.

    The offset is generally not a NURBS curve (the offset of a cubic is not a polynomial
    curve), so no
    exact route exists in any kernel -- Rhino fits one to a tolerance too. Exact points ON
    the true offset are fitted with a cubic, doubling the sample count until the MEASURED
    max deviation from the true offset locus is within `tolerance`. The result says
    `exact=False` and reports the deviation it achieved."""
    d = curve.Domain
    count = 48
    best = None
    while count <= max_samples:
        pts = [_offset_point(curve, d.T0 + (d.T1 - d.T0) * i / count, distance, n)
               for i in range(count + 1)]
        degree = 3 if len(pts) > 3 else 1
        fit = r3.NurbsCurve.CreateControlPointCurve([r3.Point3d(*p) for p in pts], degree)
        if fit is None:
            fit = _polyline_curve(pts)
        dev = _offset_deviation(curve, fit, distance, n)
        best = (fit, dev)
        if dev <= tolerance:
            return OffsetResult(fit, False, dev, "")
        count *= 2
    fit, dev = best
    return OffsetResult(fit, False, dev,
                        f"offset approximated to {dev:.3e} > tolerance {tolerance}")


def _offset_deviation(curve, fit, distance, n, samples=200):
    d = curve.Domain
    worst = 0.0
    for i in range(samples + 1):
        target = _offset_point(curve, d.T0 + (d.T1 - d.T0) * i / samples, distance, n)
        worst = max(worst, _dist_point_to_curve(fit, target))
    return worst


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _offset_point(curve, t, distance, n):
    p = curve.PointAt(t)
    tan = curve.TangentAt(t)
    d = _unit(_cross(n, (tan.X, tan.Y, tan.Z)))
    return _add((p.X, p.Y, p.Z), _scale(d, distance))


def _line_line_point(a0, b0, a1, b1):
    ok, u, _v = r3.Intersection.LineLine(
        r3.Line(r3.Point3d(*a0), r3.Point3d(*b0)),
        r3.Line(r3.Point3d(*a1), r3.Point3d(*b1)))
    if not ok:
        return None
    return _add(a0, _scale(_sub(b0, a0), u))


def _polyline_curve(pts):
    return r3.PolylineCurve([r3.Point3d(*p) for p in pts])


def _is_linear(curve):
    try:
        return bool(curve.IsLinear())
    except Exception:  # noqa: BLE001
        return False


def _polyline_verts(curve):
    try:
        if isinstance(curve, r3.PolylineCurve):
            return [_pt(curve.Point(i)) for i in range(curve.PointCount)]
        if curve.IsPolyline():
            pl = curve.TryGetPolyline()
            if pl is not None:
                return [_pt(p) for p in pl]
    except Exception:  # noqa: BLE001
        return None
    return None


def _dist_point_to_curve(curve, p, samples=200):
    d = curve.Domain
    best_t, best = d.T0, math.inf
    for i in range(samples + 1):
        t = d.T0 + (d.T1 - d.T0) * i / samples
        q = curve.PointAt(t)
        dd = math.dist(p, (q.X, q.Y, q.Z))
        if dd < best:
            best, best_t = dd, t
    step = (d.T1 - d.T0) / samples
    lo, hi = max(d.T0, best_t - step), min(d.T1, best_t + step)

    def at(t):
        q = curve.PointAt(t)
        return math.dist(p, (q.X, q.Y, q.Z))

    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c1, c2 = hi - gr * (hi - lo), lo + gr * (hi - lo)
    f1, f2 = at(c1), at(c2)
    for _ in range(50):
        if hi - lo < 1e-15:
            break
        if f1 < f2:
            hi, c2, f2 = c2, c1, f1
            c1 = hi - gr * (hi - lo)
            f1 = at(c1)
        else:
            lo, c1, f1 = c1, c2, f2
            c2 = lo + gr * (hi - lo)
            f2 = at(c2)
    return at(0.5 * (lo + hi))


def _pt(p):
    return (p.X, p.Y, p.Z)


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _dist(a, b):
    return math.dist(a, b)


def _unit(v):
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if m < 1e-15:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


# ===========================================================================
# Surface.Offset -- a fitted approximation along the normal
#
# Surface.Offset is ABSENT in rhino3dm (measured). Offsetting a NURBS surface
# EXACTLY is not generally possible (the true offset of a NURBS is not a NURBS),
# so this is a FITTED approximation, exactly as the curve offset above is -- the
# achieved deviation is measured and reported, and it is never presented as
# exact except for a plane (whose offset IS a parallel plane). Method: move each
# control point along the surface normal at its Greville parameter by `distance`
# (exact for a plane, whose normal is constant); a coarse control net is refined
# by knot insertion until the sampled deviation meets the tolerance. NormalAt is
# all that is needed. An offset that inverts the surface (|d| past the radius of
# curvature -- e.g. a cylinder with d < -r) is REFUSED, not built.
# ===========================================================================

def surface_offset(surface, distance, tolerance=1e-2, max_refine=6):
    """Offset `surface` by a signed `distance` along its normal (OffsetSrf).

    Returns an `OffsetResult` whose `.curve` field holds the offset SURFACE (the
    result type is shared with the curve offset). `.exact` is True only for a
    plane; otherwise `.max_deviation` is the measured worst error over a sample
    grid, within `tolerance` when achievable. `.curve` is None with a warning when
    the offset inverts the surface.
    """
    ns = surface
    if not (hasattr(ns, "Points") and hasattr(ns.Points, "CountU")):
        try:
            ns = surface.ToNurbsSurface()
        except Exception:  # noqa: BLE001
            return OffsetResult(None, False, 0.0, "not a NURBS surface")
    if abs(distance) < 1e-15:
        return OffsetResult(ns.Duplicate(), True, 0.0, "")

    planar = False
    try:
        planar = bool(ns.IsPlanar())
    except Exception:  # noqa: BLE001
        pass

    best = None
    for level in range(max_refine + 1):
        work = _refine_surface(ns, level)
        off = _offset_control_net(work, distance)
        dev, folded = _offset_surface_quality(ns, off, distance)
        if folded:
            return OffsetResult(None, False, 0.0,
                                "offset inverts the surface (|distance| exceeds "
                                "the radius of curvature); refused")
        best = (off, dev)
        if dev <= tolerance:
            return OffsetResult(off, planar, dev, "")
    off, dev = best
    return OffsetResult(off, planar, dev,
                        f"surface offset approximated to {dev:.3e} > "
                        f"tolerance {tolerance}")


def _refine_surface(ns, level):
    """A geometrically identical copy with `level` rounds of span-midpoint knot
    insertion in both directions -- more control points, so the control-net move
    approximates the true offset more closely."""
    work = ns.Duplicate()
    for _ in range(level):
        for knots in (work.KnotsU, work.KnotsV):
            vals = sorted(set(knots.ToList()))
            for m in [0.5 * (a + b) for a, b in zip(vals[:-1], vals[1:])]:
                knots.InsertKnot(m, 1)
    return work


def _offset_control_net(work, distance):
    off = work.Duplicate()
    ku, kv = work.KnotsU.ToList(), work.KnotsV.ToList()
    nu, nv = work.Points.CountU, work.Points.CountV
    deg_u, deg_v = len(ku) - nu + 1, len(kv) - nv + 1
    du, dv = work.Domain(0), work.Domain(1)
    for i in range(nu):
        gu = min(max(_greville(ku, i, deg_u), du.T0), du.T1)
        for j in range(nv):
            gv = min(max(_greville(kv, j, deg_v), dv.T0), dv.T1)
            n = work.NormalAt(gu, gv)
            cp = work.Points.GetControlPoint(i, j)
            w = getattr(cp, "W", 1.0) or 1.0
            lx, ly, lz = cp.X / w, cp.Y / w, cp.Z / w
            off.Points[i, j] = r3.Point4d((lx + distance * n.X) * w,
                                          (ly + distance * n.Y) * w,
                                          (lz + distance * n.Z) * w, w)
    return off


def _greville(knots, i, degree):
    return sum(knots[i:i + degree]) / float(degree)


def _offset_surface_quality(ns, off, distance, grid=24):
    """(worst deviation from the true offset locus, folded?) over a sample grid.

    The true offset at (u,v) is ``ns.PointAt(u,v) + d*ns.NormalAt(u,v)``. `off`
    shares `ns`'s parameterisation (knot insertion preserves it), so the two are
    compared at the same (u,v). `folded` is True where the offset surface's normal
    has REVERSED relative to the original -- the surface has turned inside out.
    """
    du, dv = ns.Domain(0), ns.Domain(1)
    worst = 0.0
    folded = False
    for a in range(grid + 1):
        u = du.T0 + (du.T1 - du.T0) * a / grid
        for b in range(grid + 1):
            v = dv.T0 + (dv.T1 - dv.T0) * b / grid
            p = ns.PointAt(u, v)
            n = ns.NormalAt(u, v)
            true = (p.X + distance * n.X, p.Y + distance * n.Y, p.Z + distance * n.Z)
            q = off.PointAt(u, v)
            worst = max(worst, math.dist((q.X, q.Y, q.Z), true))
            no = off.NormalAt(u, v)
            if no.X * n.X + no.Y * n.Y + no.Z * n.Z < 0:
                folded = True
    return worst, folded
