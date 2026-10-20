"""Fillet and Chamfer between two curves (pure: rhino3dm only, no bpy).

the reference CAD application's Fillet (commands/fillet.htm) options: Radius, Join, Trim, ExtendArcsBy
(Arc|Line), ExtendOtherCurvesBy (Line|Smooth), Dynamic, FilletCorners. Chamfer
(commands/chamfer.htm): Distances (two), Join, Trim, plus the same Extend
options. Quoted for whoever wires the commands; only the geometry is built here.

SCOPE: the exact LINE/LINE case is built exactly and is most of real drafting use
-- a fillet is the classical arc tangent to both lines (bisector + offset), a
chamfer the straight cut at two distances. NON-LINEAR inputs are REFUSED with a
message rather than approximated, so a wrong arc is never silently returned; a
general fillet needs the tangents at the intersection plus an iteration, left for
later. Radius/distance too large for the corner, and parallel (non-intersecting)
lines, are refused too.
"""
import math

import rhino3dm as r3


class CornerResult:
    __slots__ = ("curve", "trimmed_a", "trimmed_b")

    def __init__(self, curve, trimmed_a, trimmed_b):
        self.curve = curve            # the fillet arc / the chamfer segment
        self.trimmed_a = trimmed_a    # input a, shortened to the tangent point if trim
        self.trimmed_b = trimmed_b

    def __repr__(self):
        return f"CornerResult({type(self.curve).__name__}, trimmed a/b)"


def fillet_lines(line_a, line_b, radius, trim=True):
    """Fillet arc of `radius` in the corner between two LINES. Raises ValueError
    for non-linear input, parallel lines, or a radius too large for the corner.
    """
    p, a, b, arm_a, arm_b, far_a, far_b = _corner(line_a, line_b)
    half = math.acos(_clamp(_dot(a, b), -1.0, 1.0)) / 2.0
    if math.sin(half) < 1e-9 or half < 1e-9:
        raise ValueError("fillet: the two lines are collinear/parallel; no corner")
    tan_len = radius / math.tan(half)
    if tan_len > arm_a + 1e-12 or tan_len > arm_b + 1e-12:
        raise ValueError(f"fillet radius {radius} too large for this corner "
                         f"(needs {tan_len:.4f} along each arm, have "
                         f"{min(arm_a, arm_b):.4f})")
    t1 = _add(p, _scale(a, tan_len))          # tangent point on line a
    t2 = _add(p, _scale(b, tan_len))          # tangent point on line b
    centre = _add(p, _scale(_unit(_add(a, b)), radius / math.sin(half)))
    mid = _add(centre, _scale(_unit(_add(_sub(t1, centre), _sub(t2, centre))), radius))
    arc = r3.NurbsCurve.CreateFromArc(
        r3.Arc(r3.Point3d(*t1), r3.Point3d(*mid), r3.Point3d(*t2)))
    ta = r3.LineCurve(r3.Point3d(*far_a), r3.Point3d(*t1)) if trim else line_a
    tb = r3.LineCurve(r3.Point3d(*far_b), r3.Point3d(*t2)) if trim else line_b
    return CornerResult(arc, ta, tb)


def chamfer_lines(line_a, line_b, dist_a, dist_b, trim=True):
    """Straight chamfer cutting `dist_a`/`dist_b` back from the corner between two
    LINES. Raises ValueError for non-linear input, parallel lines, or a distance
    longer than its arm.
    """
    p, a, b, arm_a, arm_b, far_a, far_b = _corner(line_a, line_b)
    if dist_a > arm_a + 1e-12 or dist_b > arm_b + 1e-12:
        raise ValueError(f"chamfer distances ({dist_a}, {dist_b}) exceed the arms "
                         f"({arm_a:.4f}, {arm_b:.4f})")
    t1 = _add(p, _scale(a, dist_a))
    t2 = _add(p, _scale(b, dist_b))
    seg = r3.LineCurve(r3.Point3d(*t1), r3.Point3d(*t2))
    ta = r3.LineCurve(r3.Point3d(*far_a), r3.Point3d(*t1)) if trim else line_a
    tb = r3.LineCurve(r3.Point3d(*far_b), r3.Point3d(*t2)) if trim else line_b
    return CornerResult(seg, ta, tb)


def chamfer_curves(curve_a, curve_b, dist_a, dist_b, trim=True):
    """Straight chamfer between two curves of any kind, the sibling of
    :func:`fillet_curves`. Two lines delegate to the exact :func:`chamfer_lines`;
    anything else walks each curve back from the corner by `dist_a`/`dist_b` of
    arc length and joins the two points. Raises ValueError when the curves
    do not meet, or a distance is longer than the available curve.
    """
    if dist_a < 0 or dist_b < 0:
        raise ValueError("chamfer distances must be non-negative")
    pa = _primitive(curve_a)
    pb = _primitive(curve_b)
    if pa is not None and pb is not None and pa[0] == "line" and pb[0] == "line":
        return chamfer_lines(curve_a, curve_b, dist_a, dist_b, trim)
    return _chamfer_general(curve_a, curve_b, dist_a, dist_b, trim)


def _chamfer_general(curve_a, curve_b, dist_a, dist_b, trim, tol=1e-6):
    near = _nearest()
    al = _arclength()
    isc = _intersect()
    hits = [h for h in isc.curve_curve(curve_a, curve_b, tol) if not h.overlap]
    if not hits:
        raise ValueError("chamfer: the curves do not meet; there is no corner to cut")
    # the corner is the crossing nearest the coarse closest-approach midpoint, so
    # the choice is deterministic when a pair crosses more than once.
    _ta, _tb, approx, _tA, _tB = _closest_approach(curve_a, curve_b, near)
    hit = min(hits, key=lambda h: _dist(h.point, approx))
    pa3, _pa_t = _walk_back(curve_a, hit.t_a, dist_a, al)
    pb3, _pb_t = _walk_back(curve_b, hit.t_b, dist_b, al)
    seg = r3.LineCurve(r3.Point3d(*pa3), r3.Point3d(*pb3))
    ca = _trim_at_point(curve_a, pa3, pb3, near) if trim else curve_a
    cb = _trim_at_point(curve_b, pb3, pa3, near) if trim else curve_b
    return CornerResult(seg, ca, cb)


def _walk_back(curve, t_corner, dist, al):
    """The point `dist` of ARC LENGTH from the corner along `curve`, walking toward
    the FARTHER end (the longer arm). Raises ValueError if `dist` exceeds that arm.
    `parameter_at_length` measures arc length from the curve's start, so the target
    is the corner's own arc length +/- `dist` depending on the walk direction."""
    dom = curve.Domain
    s_corner = al.length_of(curve, dom.T0, t_corner)
    total = al.length_of(curve)
    toward_t1 = (total - s_corner) >= s_corner
    arm = (total - s_corner) if toward_t1 else s_corner
    if dist > arm + 1e-9:
        raise ValueError(
            f"chamfer distance {dist} exceeds the available curve length {arm:.4f}")
    s = s_corner + dist if toward_t1 else s_corner - dist
    t = al.parameter_at_length(curve, s)
    return _pt(curve.PointAt(t)), t


def _arclength():
    try:
        from . import arclength
    except ImportError:                       # direct file-path loading
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import arclength
    return arclength


def _intersect():
    try:
        from . import intersect
    except ImportError:                       # direct file-path loading
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import intersect
    return intersect


# ===========================================================================
# Fillet beyond line/line -- line/arc and arc/arc, still closed form.
#
# The fillet of radius r tangent to two curves is centred where the two curves,
# each OFFSET by r toward the fillet, cross: for a line that offset is a parallel
# line, for a circle a concentric circle of radius R +/- r, so the centre is the
# analytic intersection of a line/circle pair -- line/line, line/circle,
# circle/circle. The tangent point on each input is then the foot of the
# perpendicular from the centre (a line's nearest point, or a circle's
# centre-radial point), which makes TANGENCY exact by construction. All four offset
# sign combinations and both intersection roots are generated as candidate centres;
# the ones whose tangent points fall OFF the actual segment/arc are discarded, and
# the smallest-sweep fillet among the survivors is returned. A general NURBS pair
# can use the iterative curve/curve solver below.
# ===========================================================================

def fillet_curves(curve_a, curve_b, radius, trim=True, tol=1e-7):
    """Fillet arc of `radius` between two coplanar curves.

    Handles LINE and ARC/CIRCLE inputs in any combination, closed-form. line/line
    delegates to the exact :func:`fillet_lines`; line/arc and arc/arc are
    closed-form. A pair where either side is a GENERAL curve goes to the iterative
    solver :func:`_fillet_general`. Raises ValueError for a radius too large
    to fit, for parallel lines, and for concentric circles (no corner to round).
    """
    if radius <= 0:
        raise ValueError("fillet radius must be positive")
    pa = _primitive(curve_a)
    pb = _primitive(curve_b)
    if pa is None or pb is None:
        return _fillet_general(curve_a, curve_b, radius, trim)
    if pa[0] == "line" and pb[0] == "line":
        return fillet_lines(curve_a, curve_b, radius, trim)

    O, e1, e2, _n = _common_plane(pa, pb)
    la = _to_plane(pa, O, e1, e2)
    lb = _to_plane(pb, O, e1, e2)
    if la[0] == "circle" and lb[0] == "circle" \
            and _dist2(la[1], lb[1]) < tol:
        raise ValueError("fillet: the two circles are concentric; no corner")

    candidates = []
    for ca in _offset_loci(la, radius):
        for cb in _offset_loci(lb, radius):
            candidates.extend(_locus_intersections(ca, cb))
    best = None
    for c in candidates:                      # c is a 2-D centre in plane coords
        tan_a = _tangent_point(la, c)
        tan_b = _tangent_point(lb, c)
        if tan_a is None or tan_b is None:
            continue
        if abs(_dist2(c, tan_a) - radius) > 1e-6 \
                or abs(_dist2(c, tan_b) - radius) > 1e-6:
            continue
        ta3 = _from_plane(tan_a, O, e1, e2)
        tb3 = _from_plane(tan_b, O, e1, e2)
        if not _on_input(pa, ta3, tol) or not _on_input(pb, tb3, tol):
            continue
        c3 = _from_plane(c, O, e1, e2)
        sweep = _sweep_angle(c3, ta3, tb3)
        if best is None or sweep < best[0]:
            best = (sweep, c3, ta3, tb3)
    if best is None:
        raise ValueError(
            f"fillet radius {radius} does not fit between these curves "
            f"(no tangent arc lands on both within their extents)")
    _sweep, c3, ta3, tb3 = best
    mid = _add(c3, _scale(_unit(_add(_sub(ta3, c3), _sub(tb3, c3))), radius))
    arc = r3.NurbsCurve.CreateFromArc(
        r3.Arc(r3.Point3d(*ta3), r3.Point3d(*mid), r3.Point3d(*tb3)))
    if arc is None:
        raise ValueError(
            "fillet: the tangent points coincide (the curves are tangent, so there "
            "is no corner to round at this radius)")
    ta = _trim_input(curve_a, pa, ta3) if trim else curve_a
    tb = _trim_input(curve_b, pb, tb3) if trim else curve_b
    return CornerResult(arc, ta, tb)


# ===========================================================================
# General curve/curve fillet, solved iteratively.
#
# The fillet centre C is the point at distance `radius` from BOTH curves, on the
# inside. That is a root of F(C) = (dist(C,A) - r, dist(C,B) - r) = 0, two
# equations in the two in-plane coordinates of C. `nearest.closest_point_on_curve`
# gives each distance and its foot, and the gradient of a distance-to-curve
# is the unit vector from the foot to C -- so Newton needs nothing else. The system
# is multi-modal (a corner has up to four fillets), so C is seeded from the four
# quadrants around the curves' closest approach and the smallest-sweep valid
# solution wins. Tangency is exact BY CONSTRUCTION: the foot is the perpendicular
# foot, so the arc centred at C through it meets the curve tangentially.
# ===========================================================================

def _fillet_general(curve_a, curve_b, radius, trim, tol=1e-7):
    near = _nearest()
    _ta0, _tb0, corner, tan_a, tan_b = _closest_approach(curve_a, curve_b, near)
    n = _unit(_cross(tan_a, tan_b))
    if n == (0.0, 0.0, 0.0):
        raise ValueError("fillet: the curves are tangent or parallel at their "
                         "nearest approach; there is no corner to round")
    e1 = _unit(tan_a)
    e2 = _unit(_cross(n, e1))
    O = corner
    # unit normals to each curve, in-plane, as 2-D seed directions
    na2 = _rot90(_proj2(tan_a, e1, e2))
    nb2 = _rot90(_proj2(tan_b, e1, e2))
    best = None
    for sa in (1.0, -1.0):
        for sb in (1.0, -1.0):
            seed = (radius * (sa * na2[0] + sb * nb2[0]),
                    radius * (sa * na2[1] + sb * nb2[1]))
            sol = _fillet_newton(curve_a, curve_b, radius, O, e1, e2, seed, near, tol)
            if sol is None:
                continue
            c3, fa3, fb3 = sol
            sweep = _sweep_angle(c3, fa3, fb3)
            if best is None or sweep < best[0]:
                best = (sweep, c3, fa3, fb3)
    if best is None:
        raise ValueError(
            f"fillet radius {radius} does not fit between these curves "
            f"(no tangent arc of that radius lands on both within their extents)")
    _sw, c3, fa3, fb3 = best
    mid = _add(c3, _scale(_unit(_add(_sub(fa3, c3), _sub(fb3, c3))), radius))
    arc = r3.NurbsCurve.CreateFromArc(
        r3.Arc(r3.Point3d(*fa3), r3.Point3d(*mid), r3.Point3d(*fb3)))
    if arc is None:
        raise ValueError("fillet: the tangent points coincide; no corner at this "
                         "radius")
    ta = _trim_at_point(curve_a, fa3, fb3, near) if trim else curve_a
    tb = _trim_at_point(curve_b, fb3, fa3, near) if trim else curve_b
    return CornerResult(arc, ta, tb)


def _fillet_newton(a, b, radius, O, e1, e2, seed, near, tol, iters=80):
    """Newton on C (2-D plane coords) for dist(C,A)=dist(C,B)=radius. Returns
    ``(C3, footA3, footB3)`` for a valid, on-curve, tangent solution, else None."""
    c2 = seed
    ra = rb = None
    for _ in range(iters):
        c3 = _from_plane(c2, O, e1, e2)
        ra = near.closest_point_on_curve(a, c3)
        rb = near.closest_point_on_curve(b, c3)
        ga = _proj2_unit(_sub(c3, ra.point), e1, e2)
        gb = _proj2_unit(_sub(c3, rb.point), e1, e2)
        if ga is None or gb is None:
            return None
        f0, f1 = ra.distance - radius, rb.distance - radius
        det = ga[0] * gb[1] - ga[1] * gb[0]
        if abs(det) < 1e-9:                 # gradients parallel: no local solution
            return None
        dx = (f0 * gb[1] - f1 * ga[1]) / det
        dy = (ga[0] * f1 - gb[0] * f0) / det
        c2 = (c2[0] - dx, c2[1] - dy)
        if math.hypot(dx, dy) < 1e-12:
            break
    if ra is None or rb is None:
        return None
    if abs(ra.distance - radius) > 1e-6 or abs(rb.distance - radius) > 1e-6:
        return None                          # did not converge to the right radius
    if getattr(ra, "at_end", False) or getattr(rb, "at_end", False):
        return None                          # a foot ran off a curve's end
    return _from_plane(c2, O, e1, e2), ra.point, rb.point


def _closest_approach(a, b, near, n=36):
    """The nearest approach of two curves: ``(ta, tb, midpoint, tangentA, tangentB)``.
    Coarse sweep of A, each point's closest point on B, keep the minimum. A coarse
    seed is enough for fillet's Newton; chamfer locates its corner exactly via
    `intersect.curve_curve` instead."""
    da = a.Domain
    best = None
    for i in range(n + 1):
        ta = da.T0 + (da.T1 - da.T0) * i / n
        pa = a.PointAt(ta)
        rb = near.closest_point_on_curve(b, (pa.X, pa.Y, pa.Z))
        if best is None or rb.distance < best[0]:
            best = (rb.distance, ta, rb.t, (pa.X, pa.Y, pa.Z), rb.point)
    _d, ta, tb, pa3, pb3 = best
    corner = ((pa3[0] + pb3[0]) / 2.0, (pa3[1] + pb3[1]) / 2.0,
              (pa3[2] + pb3[2]) / 2.0)
    return ta, tb, corner, _curve_tangent(a, ta), _curve_tangent(b, tb)


def _trim_at_point(curve, foot3, other_foot3, near, tol=1e-9):
    """Shorten `curve` to END at its fillet tangent point `foot3`, keeping the
    piece whose far end is AWAY from the other curve's tangent point. A general
    curve is split at the foot parameter (Curve.Split preserves parameterisation,
    so its analytic structure is retained); if the split fails, the curve is returned unchanged."""
    res = near.closest_point_on_curve(curve, foot3)
    try:
        split = curve.Split(res.t)
    except Exception:  # noqa: BLE001
        split = None
    if not split or len(split) < 2 or split[0] is None or split[1] is None:
        return curve
    def far_end_dist(piece):
        e = piece.PointAtEnd
        s = piece.PointAtStart
        # the end of this piece that is NOT the shared split point
        cand = (e.X, e.Y, e.Z) if _dist((s.X, s.Y, s.Z), foot3) < \
            _dist((e.X, e.Y, e.Z), foot3) else (s.X, s.Y, s.Z)
        return _dist(cand, other_foot3)
    keep = max(split[:2], key=far_end_dist)
    return keep


def _curve_tangent(curve, t):
    d = curve.TangentAt(t)
    return (d.X, d.Y, d.Z)


def _rot90(v2):
    return (-v2[1], v2[0])


def _proj2(v3, e1, e2):
    """A 3-D DIRECTION in plane coords (no origin shift)."""
    return (_dot(v3, e1), _dot(v3, e2))


def _proj2_unit(v3, e1, e2):
    p = (_dot(v3, e1), _dot(v3, e2))
    m = math.hypot(p[0], p[1])
    return None if m < 1e-12 else (p[0] / m, p[1] / m)


def _nearest():
    try:
        from . import nearest
    except ImportError:                       # direct file-path loading
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import nearest
    return nearest


# --- primitive extraction --------------------------------------------------

def _primitive(curve):
    """('line', p0, p1) or ('circle'/'arc', center, radius, plane[, sa, ea]) for a
    LINE or ARC/CIRCLE curve; None for anything else (so the caller refuses it).
    MEASURED: TryGetArc succeeds for an arc AND a full circle (IsCircle tells them
    apart) and returns None for a line; TryGetCircle is None for a line/arc."""
    arc = _try(curve, "TryGetArc")
    if arc is not None:
        if bool(arc.IsCircle):
            return ("circle", _pt(arc.Center), float(arc.Radius), arc.Plane)
        return ("arc", _pt(arc.Center), float(arc.Radius), arc.Plane,
                float(arc.StartAngle), float(arc.EndAngle))
    if _is_linear(curve):
        return ("line", _pt(curve.PointAtStart), _pt(curve.PointAtEnd))
    return None


def _kind(curve):
    arc = _try(curve, "TryGetArc")
    if arc is not None:
        return "circle" if bool(arc.IsCircle) else "arc"
    if _is_linear(curve):
        return "line"
    return type(curve).__name__


def _try(curve, name):
    """Call an optional TryGet* and return its result, or None (MEASURED: these
    return the geometry directly, or None when not applicable)."""
    fn = getattr(curve, name, None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


# --- the common plane, and 2-D projection ----------------------------------

def _common_plane(pa, pb):
    """(origin, e1, e2, normal) of the plane the fillet is built in. An arc/circle
    carries its own plane; two lines define one by their directions."""
    for p in (pa, pb):
        if p[0] in ("circle", "arc"):
            pl = p[3]
            return (_pt(pl.Origin), _pt(pl.XAxis), _pt(pl.YAxis), _pt(pl.ZAxis))
    da = _unit(_sub(pa[2], pa[1]))
    db = _unit(_sub(pb[2], pb[1]))
    n = _unit(_cross(da, db))
    if n == (0.0, 0.0, 0.0):
        n = (0.0, 0.0, 1.0)
    e1 = da if da != (0.0, 0.0, 0.0) else (1.0, 0.0, 0.0)
    e2 = _unit(_cross(n, e1))
    return (pa[1], e1, e2, n)


def _to_plane(prim, O, e1, e2):
    """A primitive rewritten in 2-D plane coords: ('line', p0, p1) or
    ('circle', center2d, R) -- arcs collapse to their circle for the centre math,
    the arc span is re-checked in 3-D by `_on_input`."""
    if prim[0] == "line":
        return ("line", _proj(prim[1], O, e1, e2), _proj(prim[2], O, e1, e2))
    return ("circle", _proj(prim[1], O, e1, e2), prim[2])


def _proj(P, O, e1, e2):
    d = _sub(P, O)
    return (_dot(d, e1), _dot(d, e2))


def _from_plane(xy, O, e1, e2):
    return (O[0] + xy[0] * e1[0] + xy[1] * e2[0],
            O[1] + xy[0] * e1[1] + xy[1] * e2[1],
            O[2] + xy[0] * e1[2] + xy[1] * e2[2])


# --- offset loci and their intersections (all in 2-D) ----------------------

def _offset_loci(prim, r):
    """The one-or-two loci of points at distance r from the primitive: a line
    offsets to two parallel lines, a circle to the concentric R+r and |R-r|."""
    if prim[0] == "line":
        p0, p1 = prim[1], prim[2]
        d = _unit2(_sub2(p1, p0))
        nrm = (-d[1], d[0])
        return [("line", (p0[0] + nrm[0] * r, p0[1] + nrm[1] * r), d),
                ("line", (p0[0] - nrm[0] * r, p0[1] - nrm[1] * r), d)]
    center, R = prim[1], prim[2]
    loci = [("circle", center, R + r)]
    if R - r > 1e-9:
        loci.append(("circle", center, R - r))
    return loci


def _locus_intersections(ca, cb):
    if ca[0] == "line" and cb[0] == "line":
        pt = _line_line_2d(ca, cb)
        return [pt] if pt is not None else []
    if ca[0] == "line" and cb[0] == "circle":
        return _line_circle_2d(ca, cb[1], cb[2])
    if ca[0] == "circle" and cb[0] == "line":
        return _line_circle_2d(cb, ca[1], ca[2])
    return _circle_circle_2d(ca[1], ca[2], cb[1], cb[2])


def _line_line_2d(a, b):
    (ax, ay), (adx, ady) = a[1], a[2]
    (bx, by), (bdx, bdy) = b[1], b[2]
    det = adx * (-bdy) - ady * (-bdx)
    if abs(det) < 1e-12:
        return None                           # parallel offset lines
    rx, ry = bx - ax, by - ay
    t = (rx * (-bdy) - ry * (-bdx)) / det
    return (ax + adx * t, ay + ady * t)


def _line_circle_2d(line, center, R):
    (px, py), (dx, dy) = line[1], line[2]
    fx, fy = px - center[0], py - center[1]
    b = 2.0 * (fx * dx + fy * dy)
    cc = fx * fx + fy * fy - R * R
    disc = b * b - 4.0 * cc
    if disc < -1e-9:
        return []
    disc = max(disc, 0.0)
    root = math.sqrt(disc)
    out = []
    for s in ((-b + root) / 2.0, (-b - root) / 2.0):
        out.append((px + dx * s, py + dy * s))
    return out


def _circle_circle_2d(c0, R0, c1, R1):
    dx, dy = c1[0] - c0[0], c1[1] - c0[1]
    d = math.hypot(dx, dy)
    if d < 1e-12 or d > R0 + R1 + 1e-9 or d < abs(R0 - R1) - 1e-9:
        return []
    a = (R0 * R0 - R1 * R1 + d * d) / (2.0 * d)
    h2 = R0 * R0 - a * a
    h = math.sqrt(max(h2, 0.0))
    mx, my = c0[0] + a * dx / d, c0[1] + a * dy / d
    ox, oy = -dy / d * h, dx / d * h
    return [(mx + ox, my + oy), (mx - ox, my - oy)]


# --- tangent points and validity -------------------------------------------

def _tangent_point(prim2d, c):
    """The point of the 2-D primitive nearest `c` -- the fillet's tangent point."""
    if prim2d[0] == "line":
        p0, p1 = prim2d[1], prim2d[2]
        d = _unit2(_sub2(p1, p0))
        s = (c[0] - p0[0]) * d[0] + (c[1] - p0[1]) * d[1]
        return (p0[0] + d[0] * s, p0[1] + d[1] * s)
    center, R = prim2d[1], prim2d[2]
    v = _sub2(c, center)
    m = math.hypot(v[0], v[1])
    if m < 1e-12:
        return None
    return (center[0] + v[0] / m * R, center[1] + v[1] / m * R)


def _on_input(prim, pt3, tol):
    """Is the 3-D tangent point on the ACTUAL segment/arc (not its extension)?"""
    if prim[0] == "line":
        p0, p1 = prim[1], prim[2]
        d = _sub(p1, p0)
        dd = _dot(d, d)
        if dd < 1e-18:
            return False
        s = _dot(_sub(pt3, p0), d) / dd
        return -1e-9 <= s <= 1.0 + 1e-9
    if prim[0] == "circle":
        return True
    center, plane, sa, ea = prim[1], prim[3], prim[4], prim[5]
    return _angle_in_span(_arc_angle(plane, center, pt3), sa, ea)


def _sweep_angle(center, a, b):
    va = _unit(_sub(a, center))
    vb = _unit(_sub(b, center))
    return math.acos(_clamp(_dot(va, vb), -1.0, 1.0))


# --- trimming --------------------------------------------------------------

def _trim_input(curve, prim, tan3):
    """The input shortened to end at its tangent point, keeping the portion from
    the FAR end. A line becomes the segment far-end..tangent; an arc becomes the
    sub-arc far-end..tangent (a full circle cannot be trimmed to a point, so it is
    returned unchanged)."""
    if prim[0] == "line":
        p0, p1 = prim[1], prim[2]
        far = p0 if _dist(p0, tan3) >= _dist(p1, tan3) else p1
        return r3.LineCurve(r3.Point3d(*far), r3.Point3d(*tan3))
    if prim[0] == "circle":
        return curve                          # a full circle has no far end
    center, R, plane, sa, ea = prim[1], prim[2], prim[3], prim[4], prim[5]
    X, Y = _pt(plane.XAxis), _pt(plane.YAxis)
    tan_angle = sa + ((_arc_angle(plane, center, tan3) - sa) % (2.0 * math.pi))
    start3 = _arc_point(center, R, X, Y, sa)
    end3 = _arc_point(center, R, X, Y, ea)
    far_angle = sa if _dist(start3, tan3) >= _dist(end3, tan3) else ea
    mid_angle = 0.5 * (far_angle + tan_angle)
    far3 = _arc_point(center, R, X, Y, far_angle)
    mid3 = _arc_point(center, R, X, Y, mid_angle)
    return r3.NurbsCurve.CreateFromArc(
        r3.Arc(r3.Point3d(*far3), r3.Point3d(*mid3), r3.Point3d(*tan3)))


def _arc_angle(plane, center, pt3):
    """The angle of `pt3` about the arc's centre, in the arc's own X/Y frame -- the
    same convention Arc.StartAngle/EndAngle and Arc.PointAt use."""
    v = _sub(pt3, center)
    return math.atan2(_dot(v, _pt(plane.YAxis)), _dot(v, _pt(plane.XAxis)))


def _angle_in_span(phi, sa, ea):
    rel = (phi - sa) % (2.0 * math.pi)
    return rel <= (ea - sa) + 1e-9


def _arc_point(center, R, X, Y, ang):
    c, s = math.cos(ang), math.sin(ang)
    return (center[0] + R * (c * X[0] + s * Y[0]),
            center[1] + R * (c * X[1] + s * Y[1]),
            center[2] + R * (c * X[2] + s * Y[2]))


def _corner(line_a, line_b):
    """(corner point P, unit arm dirs a/b, arm lengths, far endpoints) for two
    lines. Raises ValueError if either input is not linear or they do not meet."""
    if not _is_linear(line_a) or not _is_linear(line_b):
        raise ValueError("fillet/chamfer implemented for LINES only; a non-linear "
                         "input needs the general iterative case, not built yet")
    a0, a1 = _pt(line_a.PointAtStart), _pt(line_a.PointAtEnd)
    b0, b1 = _pt(line_b.PointAtStart), _pt(line_b.PointAtEnd)
    ok, u, _v = r3.Intersection.LineLine(
        r3.Line(r3.Point3d(*a0), r3.Point3d(*a1)),
        r3.Line(r3.Point3d(*b0), r3.Point3d(*b1)))
    if not ok:
        raise ValueError("fillet/chamfer: the lines are parallel and do not meet")
    la = r3.Line(r3.Point3d(*a0), r3.Point3d(*a1))
    pp = la.PointAt(u)
    p = (pp.X, pp.Y, pp.Z)
    # each arm points from the corner toward the FAR endpoint of its line
    far_a = a1 if _dist(p, a1) >= _dist(p, a0) else a0
    far_b = b1 if _dist(p, b1) >= _dist(p, b0) else b0
    a = _unit(_sub(far_a, p))
    b = _unit(_sub(far_b, p))
    return p, a, b, _dist(p, far_a), _dist(p, far_b), far_a, far_b


def _is_linear(curve):
    try:
        return bool(curve.IsLinear())
    except Exception:  # noqa: BLE001
        return False


def _pt(p):
    return (p.X, p.Y, p.Z)


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _sub2(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _unit2(v):
    m = math.hypot(v[0], v[1])
    return (0.0, 0.0) if m < 1e-15 else (v[0] / m, v[1] / m)


def _dist2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _dist(a, b):
    return math.dist(a, b)


def _unit(v):
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (0.0, 0.0, 0.0) if m < 1e-15 else (v[0] / m, v[1] / m, v[2] / m)


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


# ===========================================================================
# Fillet picks a segment of a joined curve.
#
# The command help says to select each curve near an end, so the pick location determines
# which segment is intended. The viewport records that point in `cmd.pick_points`; these
# helpers resolve it to a segment.
# ===========================================================================

def segment_near(segments, point):
    """Index of the segment in `segments` nearest the world `point` (exact distance)."""
    near = _nearest()
    best = None
    for i, seg in enumerate(segments):
        d = near.closest_point_on_curve(seg, point).distance
        if best is None or d < best[0]:
            best = (d, i)
    if best is None:
        raise ValueError("the curve has no segments")
    return best[1]


def fillet_adjacent_segments(segments, closed, i, j, radius, trim=True, near=None):
    """Fillet the corner between segments `i` and `j` of ONE joined curve.

    `segments` are the curve's pieces in order (`bridge.explode_curve`), `closed` whether
    the last meets the first. Returns ``(arc, pieces)``: the fillet arc and the curve's
    pieces to join back into ONE curve. Those are every other segment, the two trimmed
    ones, and the arc; with `trim=False`, the untouched segments and no arc.

    Refused by name: the same segment twice, and two segments that do not meet at a
    corner (they are not neighbours along the curve).

    A CLOSED curve of TWO segments has two corners between the same pair. `near` (the
    picks' midpoint) says which one is meant. If the arc lands at the other corner, the
    fillet is refused rather than built in the wrong place.
    """
    n = len(segments)
    if i == j:
        raise ValueError("both picks are on the same segment -- pick the two segments on "
                         "either side of the corner")
    if abs(i - j) == 1:
        first, second = min(i, j), max(i, j)
    elif closed and {i, j} == {0, n - 1}:
        first, second = n - 1, 0
    else:
        raise ValueError(f"segments {i + 1} and {j + 1} of that curve do not meet at a "
                         f"corner -- filleting them is not implemented yet")
    res = fillet_curves(segments[first], segments[second], radius, trim)
    if closed and n == 2 and near is not None:
        corners = [_pt(segments[0].PointAtEnd), _pt(segments[1].PointAtEnd)]
        want = min(corners, key=lambda c: _dist(c, near))
        other = corners[1] if want is corners[0] else corners[0]
        mid = _pt(res.curve.PointAt(res.curve.Domain.Mid))
        if _dist(mid, want) >= _dist(mid, other):
            raise ValueError("the fillet fits only at the OTHER corner of this two-segment "
                             "curve -- filleting the picked corner is not implemented yet")
    if not trim:
        return res.curve, list(segments)
    rest = [s for k, s in enumerate(segments) if k not in (first, second)]
    return res.curve, rest + [res.trimmed_a, res.curve, res.trimmed_b]
