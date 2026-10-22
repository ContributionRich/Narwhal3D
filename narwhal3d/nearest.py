"""Closest point on a curve (pure: rhino3dm only, no bpy).

Why this module exists
----------------------
In rhino3dm 8.35.0, **`Curve.ClosestPoint` does
not exist.** Five separate things need it -- the **Near** object snap, `Project`,
`Pull`, `Extend` and `Divide` -- so it is built once, here, and returns the curve
**PARAMETER** as well as the point, because every one of those callers needs the
parameter and not just the location.

What it solves
--------------
Minimise ``f(t) = |C(t) - P|^2`` over the curve's domain. Its derivative is
``2 (C(t) - P) . C'(t)``, so an interior minimum is a root of::

    g(t) = (C(t) - P) . C'(t) = 0

Newton on ``g`` needs ``g'(t) = |C'(t)|^2 + (C(t) - P) . C''(t)``, and
``DerivativeAt(t, 2)`` supplies ``C``, ``C'`` and ``C''`` in one call. It returns a
**list of Point3d of length count+1**, with element 0 holding the position; its values
agree with a central finite-difference cross-check.

**The function is multi-modal and that is designed for, not discovered.** A point
inside a circle is equidistant from a whole family of points; a wiggly NURBS has
one local minimum per wiggle. A single Newton seed therefore finds *a* minimum,
not *the* minimum. So the search:

1. samples every SPAN -- the span breaks come from the knot vector where one is
   available, so the samples straddle every knot; a uniform sample of the whole domain
   can walk straight past a knot discontinuity;
2. keeps every sample that is a **local** minimum of the sampled distance (the
   ends count as local minima when they beat their one neighbour, which is what
   covers a query at a closed curve's SEAM -- see `_local_minimum_seeds`, where
   the mutation test that settled this is recorded);
3. runs Newton from each of those, clamped to the domain;
4. takes the global best over {Newton results} u {all samples} u {both domain
   endpoints}. Including the raw samples means the answer can never be *worse*
   than the coarse sample, and including the endpoints is what makes a query off
   the end of an open curve return the END parameter instead of an extrapolation
   -- Newton alone cannot produce a boundary answer.

The analytic short-cut, and what it is really for
------------------------------------------------
For a line, an arc or a circle the nearest point is closed form. **MEASURED, and
it is not in the capability table:** `Curve.ClosestPoint` is absent but
`Circle.ClosestPoint` / `Circle.ClosestParameter` and `Arc.ClosestPoint` /
`Arc.ClosestParameter` **do exist** in 8.35.0, and `Arc.ClosestPoint` **clamps to
the arc's angle span** rather than wrapping to the underlying circle: querying
(0,-8,0) against the 0..1 rad arc of a radius-5 circle returns the
start point, angle 0). So the exact point comes from McNeel's own primitive.

What that buys is not a few extra digits -- it is that the problem stops being
multi-modal. Searching for a target that is known to lie **on** the curve has a
single minimum of value zero, so the parameter solve cannot land on the wrong
branch. The analytic route therefore feeds the SAME searcher with the exact
nearest point as its target.

**And it is verified at run time, not assumed.** After the parameter solve the
module checks that ``|C(t) - Q| <= tolerance``; if the analytic point is not
actually on the curve the shortcut is abandoned and the general search runs on
the original point. `.method` says which route produced the answer, so a caller
(or a test) can tell.

The trap this avoids
--------------------
It is tempting to write "IsLinear -> t = T0 + s (T1 - T0)". **MEASURED: that is
wrong.** A degree-3 curve whose control points are collinear but unevenly spaced
-- ``(0,0,0) (1,0,0) (2,0,0) (10,0,0)`` -- reports ``IsLinear() == True`` and its
parameterisation deviates from the affine one by **2.67 units**. `IsLinear` says the
curve's *shape* is a line; it says
nothing about its *parameterisation*. So even the linear route solves for the
parameter rather than interpolating it.

Numerical parameters
--------------------
The tolerance is the document's absolute tolerance from `units.py`, never a fixed
literal. The sampling density, Newton iteration cap, and parameter epsilon are internal
numerical choices because the reference CAD application does not expose corresponding values.
"""
from __future__ import annotations

import math

import rhino3dm as r3

try:                                  # normal import, inside the addon package
    from . import units
except ImportError:                   # supports direct file-path loading
    import units


#: Number of sub-samples per span used to seed the search. Six is enough to
#: separate the local minima of a single polynomial span (a span of degree d has
#: at most d-1 interior extrema of distance, and the curves we build are degree
#: <= 3), while keeping the cost low enough for an interactive osnap. There is no
#: the reference CAD application-documented value to measure here.
SAMPLES_PER_SPAN = 6

#: Newton iteration cap. Newton on a well-seeded root converges
#: quadratically, so this is a safety net against a pathological curve, not a
#: working budget; 60 is far beyond what convergence needs.
MAX_NEWTON = 60

#: Relative parameter epsilon. Used to decide "this parameter IS the
#: domain endpoint" and to stop Newton; scaled by the domain span so it means the
#: same thing whether the domain is [0,1] or [0,6.28].
PARAM_EPS = 1e-12


def default_tolerance():
    """The document's absolute tolerance, in model units.

    the reference CAD application's default is 0.01 mm. Read through `units.py` rather than using a literal so a
    document whose tolerance differs is honoured automatically.
    """
    # Use the document's tolerance in model units, not a fixed 0.01 default.
    return units.document_tolerance()


class ClosestPoint:
    """The answer: a parameter first, then everything derived from it.

    * ``t``        -- the curve parameter of the nearest point.
    * ``point``    -- ``curve.PointAt(t)`` as an ``(x, y, z)`` tuple. It IS
      ``PointAt(t)`` and not some separately-computed location, so a caller can
      rely on the two agreeing exactly.
    * ``distance`` -- ``|point - P|``.
    * ``method``   -- ``"line"``, ``"arc"``, ``"circle"`` or ``"nurbs"``: which
      route found it. Only ``"nurbs"`` relied on the multi-seed search alone.
    * ``at_end``   -- True when the answer is a domain endpoint of an OPEN curve,
      i.e. the true perpendicular foot lies off the curve. Callers that need a
      genuine perpendicular (`Extend`, some `Project` cases) must check this;
      the perpendicularity condition does NOT hold at an end.
    * ``ambiguous``-- True when the query point is on a circle's or arc's axis,
      where every point of the curve is equidistant and no nearest point exists.
      The returned parameter is then the domain start, chosen arbitrarily and
      flagged rather than presented as an answer.
    """

    __slots__ = ("t", "point", "distance", "method", "at_end", "ambiguous")

    def __init__(self, t, point, distance, method, at_end=False,
                 ambiguous=False):
        self.t = float(t)
        self.point = tuple(float(c) for c in point)
        self.distance = float(distance)
        self.method = method
        self.at_end = bool(at_end)
        self.ambiguous = bool(ambiguous)

    def __repr__(self):  # pragma: no cover - debugging aid
        flags = "".join((" at_end" if self.at_end else "",
                         " AMBIGUOUS" if self.ambiguous else ""))
        return (f"ClosestPoint(t={self.t!r}, point={self.point!r}, "
                f"distance={self.distance!r}, method={self.method}{flags})")


# ---------------------------------------------------------------------------
# the public entry point
# ---------------------------------------------------------------------------

def closest_point_on_curve(curve, point, tolerance=None):
    """The point of `curve` nearest the 3-D `point`, as a :class:`ClosestPoint`.

    `tolerance` defaults to the document's absolute tolerance
    (:func:`default_tolerance`) and is used for the run-time check on the
    analytic shortcut and for axis-ambiguity detection -- not as the convergence
    target, which is machine precision.

    Raises ValueError when `curve` has no usable domain.
    """
    tol = default_tolerance() if tolerance is None else float(tolerance)
    p = _pt(point)
    t0, t1 = _domain(curve)
    if not (t1 > t0):
        raise ValueError(f"curve has a degenerate domain [{t0}, {t1}]")

    exact = _analytic_target(curve, p, tol)
    if exact is not None:
        method, target, ambiguous = exact
        if ambiguous:
            q = _eval(curve, t0)
            return ClosestPoint(t0, q, _dist(q, p), method, False, True)
        res = _search(curve, target, tol, method)
        # Run-time verification: the analytic point must really be ON the curve.
        # If it is not (a curve that reports IsLinear but doubles back past its
        # own chord, say) the shortcut is abandoned rather than trusted.
        if _dist(res.point, target) <= tol:
            return ClosestPoint(res.t, res.point, _dist(res.point, p), method,
                                res.at_end, False)

    return _search(curve, p, tol, "nurbs")


def distance_to_curve(curve, point, tolerance=None):
    """Shortest distance from `point` to `curve`.

    A thin wrapper so callers that only want the distance do not have to reach
    into the result. `bridge.distance_to_curve` still does this by dense
    sampling; this one solves it.
    """
    return closest_point_on_curve(curve, point, tolerance).distance


# ---------------------------------------------------------------------------
# the analytic routes: they supply an exact TARGET, not an answer
# ---------------------------------------------------------------------------

def _analytic_target(curve, p, tol):
    """``(method, exact_nearest_point, ambiguous)`` or None for a general NURBS.

    Order matters: a full circle must be recognised before the arc test, because
    MEASURED, ``TryGetArc()`` also succeeds on a full circle (returning an Arc
    with ``IsCircle``), and the circle route is the one with the right ambiguity
    story.
    """
    circle = _try(curve, "TryGetCircle")
    if circle is not None:
        return _circle_target(circle, p, tol)

    arc = _try(curve, "TryGetArc")
    if arc is not None:
        return _arc_target(arc, p, tol)

    if _is_linear(curve):
        return _line_target(curve, p)

    return None


def _circle_target(circle, p, tol):
    """Closed form on a full circle, via McNeel's own `Circle.ClosestPoint`.

    A point on the circle's AXIS is equidistant from every point of the circle,
    so there is no nearest point. That is detected from the in-plane radial
    component and reported (`ambiguous`) instead of returning whatever the
    primitive happens to pick. The threshold is the document tolerance: within
    it of the axis the answer is arbitrary anyway.
    """
    if _radial_length(circle.Plane, circle.Center, p) <= tol:
        return ("circle", None, True)
    q = circle.ClosestPoint(_p3(p))
    return ("circle", _pt(q), False)


def _arc_target(arc, p, tol):
    """Closed form on an arc, via `Arc.ClosestPoint`.

    MEASURED: `Arc.ClosestPoint` CLAMPS to the arc's own angle span -- a query
    beyond the arc's end returns the arc's endpoint, it does not wrap round the
    underlying circle. That is exactly the semantics wanted here, so the
    clamping is handled by McNeel's primitive rather than local code.
    """
    if _radial_length(arc.Plane, arc.Center, p) <= tol:
        return ("arc", None, True)
    q = arc.ClosestPoint(_p3(p))
    if q is None:
        return None
    return ("arc", _pt(q), False)


def _line_target(curve, p):
    """Closed-form projection onto the CHORD, clamped to its ends.

    The clamp is what makes a query off the end of the curve resolve to the end
    point rather than to an extrapolation.

    Note what this deliberately does NOT do: it does not turn the projection
    fraction into a parameter. MEASURED -- a degree-3 curve with collinear but
    unevenly spaced control points reports ``IsLinear() == True`` while its
    parameterisation is 2.67 units away from affine, so interpolating the
    parameter would be badly wrong. The exact POINT is used as a search target
    and the parameter is solved for.
    """
    t0, t1 = _domain(curve)
    a = _eval(curve, t0)
    b = _eval(curve, t1)
    d = _sub(b, a)
    dd = _dot(d, d)
    if dd <= 0.0:
        return None                       # zero-length chord: nothing to project
    s = _dot(_sub(p, a), d) / dd
    s = 0.0 if s < 0.0 else (1.0 if s > 1.0 else s)
    return ("line", (a[0] + d[0] * s, a[1] + d[1] * s, a[2] + d[2] * s), False)


def _radial_length(plane, center, p):
    """How far `p` is from the axis through `center` along `plane`'s normal.

    Measured in the plane, as ``hypot`` of the two in-plane components, so it is
    zero exactly on the axis.
    """
    c = _pt(center)
    d = _sub(p, c)
    xa = _pt(plane.XAxis)
    ya = _pt(plane.YAxis)
    return math.hypot(_dot(d, xa), _dot(d, ya))


# ---------------------------------------------------------------------------
# the searcher: one implementation, used by every route
# ---------------------------------------------------------------------------

def _search(curve, target, tol, method):
    """Global minimum of ``|C(t) - target|`` over the whole domain.

    See the module docstring for why this samples spans, seeds Newton from every
    LOCAL minimum, and still considers the raw samples and both endpoints.
    """
    t0, t1 = _domain(curve)
    span = t1 - t0
    peps = PARAM_EPS * (abs(span) if span else 1.0)
    closed = _is_closed(curve)

    params = _seed_params(curve, t0, t1)
    dists = [_dist(_eval(curve, t), target) for t in params]

    # candidates always include every sample and both endpoints: the samples are
    # a floor on the answer's quality, and an endpoint is the only place Newton
    # cannot reach.
    best_t, best_d = None, math.inf
    for t, d in zip(params, dists):
        if d < best_d:
            best_t, best_d = t, d

    for i in _local_minimum_seeds(dists):
        nt = _newton(curve, target, params[i], t0, t1)
        nd = _dist(_eval(curve, nt), target)
        if nd < best_d:
            best_t, best_d = nt, nd

    q = _eval(curve, best_t)
    at_end = (not closed) and (best_t <= t0 + peps or best_t >= t1 - peps)
    return ClosestPoint(best_t, q, _dist(q, target), method, at_end, False)


def _seed_params(curve, t0, t1):
    """Sample parameters: `SAMPLES_PER_SPAN` per span, plus every span break.

    The span breaks come from the KNOT VECTOR when the curve exposes one, so the
    samples straddle each knot rather than stepping over it; uniform sampling of the
    whole domain can miss a knot-localised feature that
    gets missed. Curves with no `Knots` (MEASURED: `LineCurve`, and
    `NurbsCurve.CreateControlPointCurve` with two points returns one) fall back
    to `SpanCount` uniform spans.
    """
    breaks = _breakpoints(curve, t0, t1)
    out = []
    for a, b in zip(breaks, breaks[1:]):
        for i in range(SAMPLES_PER_SPAN):
            out.append(a + (b - a) * i / SAMPLES_PER_SPAN)
    out.append(breaks[-1])
    return out


def _breakpoints(curve, t0, t1):
    """The span boundaries inside ``[t0, t1]``, ends included, strictly sorted."""
    span = t1 - t0
    grain = abs(span) * 1e-12
    vals = [t0, t1]

    knots = _knots(curve)
    if knots:
        vals.extend(k for k in knots if t0 + grain < k < t1 - grain)
    else:
        n = 1
        try:
            n = max(1, int(curve.SpanCount))
        except Exception:  # noqa: BLE001
            n = 1
        vals.extend(t0 + span * i / n for i in range(1, n))

    vals.sort()
    out = [vals[0]]
    for v in vals[1:]:
        if v - out[-1] > grain:          # collapse knot multiplicity
            out.append(v)
    if len(out) < 2:
        out = [t0, t1]
    return out


def _local_minimum_seeds(dists):
    """Indices of the sampled distances that are local minima.

    The ends are treated as if their outside neighbour were at infinity, so the
    first and last samples qualify whenever they beat their single neighbour.
    That matters for a CLOSED curve queried at or near its SEAM, and it is worth
    recording WHY it is written this way rather than with a wrap-around:

    Wrapping the sequence for closed curves would make index 0's neighbours 1 and n-2,
    since the first and last samples represent the same point. That wrap is unnecessary
    because the infinity
    sentinel is a strict SUPERSET at the ends: a minimum sitting exactly on the
    seam has ``d[0] <= d[1]`` and is seeded either way, and a minimum just inside
    the seam is seeded from index 1 either way. The wrap was dead weight dressed
    up as robustness, so it is intentionally omitted.
    """
    n = len(dists)
    if n == 0:
        return []
    if n == 1:
        return [0]
    seeds = []
    for i, d in enumerate(dists):
        lo = dists[i - 1] if i > 0 else math.inf
        hi = dists[i + 1] if i < n - 1 else math.inf
        if d <= lo and d <= hi:
            seeds.append(i)
    if not seeds:                        # monotone sampling: seed from the best
        seeds = [min(range(n), key=lambda i: dists[i])]
    return seeds


def _newton(curve, target, t, lo, hi):
    """Newton on ``g(t) = (C(t) - target) . C'(t)``, clamped to ``[lo, hi]``.

    ``g'(t) = |C'(t)|^2 + (C(t) - target) . C''(t)``. `DerivativeAt(t, 2)`
    returns ``[C, C', C'']`` in one call (MEASURED).
    """
    span = hi - lo
    stop = PARAM_EPS * (abs(span) if span else 1.0)
    for _ in range(MAX_NEWTON):
        try:
            d = curve.DerivativeAt(t, 2)
        except Exception:  # noqa: BLE001
            return t
        if d is None or len(d) < 3:
            return t
        c, c1, c2 = _pt(d[0]), _pt(d[1]), _pt(d[2])
        w = _sub(c, target)
        g = _dot(w, c1)
        gp = _dot(c1, c1) + _dot(w, c2)
        if abs(gp) < 1e-300:
            return t
        nt = t - g / gp
        if nt < lo:
            nt = lo
        elif nt > hi:
            nt = hi
        if abs(nt - t) <= stop:
            return nt
        t = nt
    return t


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _pt(p):
    """An (x, y, z) tuple from a Point3d/Vector3d or any 3-sequence."""
    if hasattr(p, "X"):
        return (p.X, p.Y, p.Z)
    return (float(p[0]), float(p[1]), float(p[2]))


def _p3(p):
    return r3.Point3d(float(p[0]), float(p[1]), float(p[2]))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
                     + (a[2] - b[2]) ** 2)


def _eval(curve, t):
    return _pt(curve.PointAt(t))


def _domain(curve):
    d = curve.Domain
    return float(d.T0), float(d.T1)


def _knots(curve):
    try:
        knots = curve.Knots
    except Exception:  # noqa: BLE001
        return None
    if knots is None:
        return None
    try:
        return [float(k) for k in knots]
    except Exception:  # noqa: BLE001
        return None


def _is_closed(curve):
    try:
        return bool(curve.IsClosed)
    except Exception:  # noqa: BLE001
        return False


def _is_linear(curve):
    try:
        return bool(curve.IsLinear())
    except Exception:  # noqa: BLE001
        return False


def _try(curve, name):
    """Call an optional `TryGet*` and return its result, or None."""
    fn = getattr(curve, name, None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


# ===========================================================================
# Closest point on a surface (the surface twin of closest_point_on_curve)
#
# `Surface.ClosestPoint` and `Brep.ClosestPoint` are both ABSENT in rhino3dm
# (measured). This is the primitive `Pull` sits on -- Pull is closest-point
# projection onto a surface, whereas Project is directional.
#
# Minimise |S(u,v) - P|^2: at the minimum S - P is orthogonal to BOTH partials.
# Gauss-Newton in (u, v) needs only the first derivatives Su, Sv; rhino3dm has no
# Surface.DerivativeAt (measured), so those are central finite differences. The
# multi-modality is worse than for a curve, so seed generously from a grid over
# the knot spans in each direction and keep the global best.
# ===========================================================================

class ClosestSurfacePoint:
    """The nearest point on a surface: its (u, v), the point, and the distance.

    * ``u``, ``v``  -- the surface parameters of the nearest point.
    * ``point``     -- ``surface.PointAt(u, v)`` as a tuple; it IS that, so the
      parameters and the location cannot drift apart.
    * ``distance``  -- ``|point - P|``.
    * ``on_edge``   -- True when the answer sits on a domain BOUNDARY of an open
      direction, i.e. the true foot of the perpendicular lies off the surface.
      The orthogonality condition does NOT hold there, exactly as `at_end` warns
      for a curve.
    * ``ambiguous`` -- True when the foot is NOT unique: the query point is
      equidistant from a whole ring or iso-curve of surface points, e.g. a point on
      a cylinder's axis or a sphere's centre. Mirrors the curve `ClosestPoint`'s
      `ambiguous`: the returned (u, v) is then one arbitrary member of that set,
      flagged rather than presented as THE answer. A point ON the surface is never
      ambiguous. Keeping this detector on the closest-point primitive gives curve and
      surface results consistent semantics.
    """

    __slots__ = ("u", "v", "point", "distance", "on_edge", "ambiguous")

    def __init__(self, u, v, point, distance, on_edge=False, ambiguous=False):
        self.u = float(u)
        self.v = float(v)
        self.point = tuple(float(c) for c in point)
        self.distance = float(distance)
        self.on_edge = bool(on_edge)
        self.ambiguous = bool(ambiguous)

    def __repr__(self):  # pragma: no cover - debugging aid
        flags = "".join((" on_edge" if self.on_edge else "",
                         " AMBIGUOUS" if self.ambiguous else ""))
        return (f"ClosestSurfacePoint(u={self.u!r}, v={self.v!r}, "
                f"point={self.point!r}, distance={self.distance!r}{flags})")


def closest_point_on_surface(surface, point, tolerance=None):
    """The point of `surface` nearest the 3-D `point`, as a ClosestSurfacePoint.

    `tolerance` defaults to the document's absolute tolerance, the same
    convention as `closest_point_on_curve`. Gauss-Newton from a grid of
    seeds over the knot spans; the closest converged result wins.
    """
    tol = default_tolerance() if tolerance is None else float(tolerance)
    p = _pt(point)
    du, dv = surface.Domain(0), surface.Domain(1)
    closed_u, closed_v = _srf_closed(surface, 0), _srf_closed(surface, 1)
    seeds = _surface_seeds(surface, du, dv)
    seeds.sort(key=lambda s: _dist(_srf_eval(surface, s[0], s[1]), p))
    best = None
    for (su0, sv0) in seeds[:6]:        # a few seeds to escape local minima
        res = _surface_gauss_newton(surface, p, su0, sv0, du, dv,
                                    closed_u, closed_v)
        if best is None or res.distance < best.distance:
            best = res
    best.ambiguous = _surface_ambiguous(surface, p, best, tol)
    return best


#: The iso-distance band of a unique foot is a small parabolic disk of
#: diameter ~2*sqrt(2*d*tol); a genuine ambiguity (a ring around a surface of
#: revolution's axis) spans ~2r. A second foot farther apart than several times the
#: disk is counted as a distinct minimum, so the foot is non-unique. Dimensionless.
_AMBIGUITY_DISK_FACTOR = 8.0


def _surface_ambiguous(surface, p, res, tol, n=48):
    """Return whether the closest point is non-unique.

    Scans the two ISO-parameter curves through the foot ``(res.u, res.v)``: a
    non-unique closest point on a surface of revolution -- a ring around a
    cylinder's/sphere's axis -- lies on one of them, so a second foot at the same
    distance but far apart in 3-D exposes it. This is more reliable than a blind grid,
    which catches the ring only when a grid line happens to hit the foot's
    parameter. A point ON the surface (distance <= tol) is never ambiguous.
    """
    if res.distance <= tol:
        return False
    du, dv = surface.Domain(0), surface.Domain(1)
    dmin = res.distance
    disk = 2.0 * math.sqrt(max(2.0 * dmin * tol, 0.0))
    threshold = max(_AMBIGUITY_DISK_FACTOR * disk, 20.0 * tol)
    for fixed_u in (True, False):
        vary = dv if fixed_u else du
        near = []
        for i in range(n + 1):
            w = vary.T0 + (vary.T1 - vary.T0) * i / n
            q = surface.PointAt(res.u, w) if fixed_u else surface.PointAt(w, res.v)
            qp = (q.X, q.Y, q.Z)
            if _dist(qp, p) <= dmin + tol:
                near.append(qp)
        for i, a in enumerate(near):
            for b in near[i + 1:]:
                if _dist(a, b) > threshold:
                    return True
    return False


def _surface_gauss_newton(surface, p, u, v, du, dv, closed_u, closed_v, iters=60):
    for _ in range(iters):
        s = _srf_eval(surface, u, v)
        r = _sub(s, p)
        su = _srf_deriv(surface, u, v, du, 0)
        sv = _srf_deriv(surface, u, v, dv, 1)
        a, b, c = _dot(su, su), _dot(su, sv), _dot(sv, sv)
        g1, g2 = _dot(su, r), _dot(sv, r)
        det = a * c - b * b
        if abs(det) < 1e-18:
            break
        step_u = -(c * g1 - b * g2) / det
        step_v = -(a * g2 - b * g1) / det
        u = _wrap_or_clamp(u + step_u, du, closed_u)
        v = _wrap_or_clamp(v + step_v, dv, closed_v)
        if abs(step_u) * math.sqrt(a) + abs(step_v) * math.sqrt(c) < 1e-14:
            break
    s = _srf_eval(surface, u, v)
    on_edge = ((not closed_u and (abs(u - du.T0) <= 1e-9 or abs(u - du.T1) <= 1e-9))
               or (not closed_v and (abs(v - dv.T0) <= 1e-9 or abs(v - dv.T1) <= 1e-9)))
    return ClosestSurfacePoint(u, v, s, _dist(s, p), on_edge)


def _surface_seeds(surface, du, dv):
    us = _seed_axis(getattr(surface, "KnotsU", None), du)
    vs = _seed_axis(getattr(surface, "KnotsV", None), dv)
    return [(u, v) for u in us for v in vs]


def _seed_axis(knotlist, dom, per_span=3):
    try:
        vals = sorted(set(knotlist.ToList()))
    except Exception:  # noqa: BLE001
        vals = [dom.T0, dom.T1]
    bp = sorted({dom.T0, dom.T1}
                | {v for v in vals if dom.T0 - 1e-9 <= v <= dom.T1 + 1e-9})
    out = set()
    for lo, hi in zip(bp[:-1], bp[1:]):
        for k in range(per_span):
            out.add(lo + (hi - lo) * (k + 0.5) / per_span)
    out.add(dom.T0)
    out.add(dom.T1)
    return sorted(out)


def _srf_eval(surface, u, v):
    q = surface.PointAt(u, v)
    return (q.X, q.Y, q.Z)


def _srf_deriv(surface, u, v, dom, direction):
    """Central-difference partial dS/du or dS/dv (rhino3dm has no
    Surface.DerivativeAt -- measured)."""
    h = (dom.T1 - dom.T0) * 1e-6 or 1e-9
    if direction == 0:
        t = u
    else:
        t = v
    t0 = max(t - h, dom.T0)
    t1 = min(t + h, dom.T1)
    if t1 <= t0:
        t1 = min(dom.T1, t0 + h)
    if direction == 0:
        p0, p1 = surface.PointAt(t0, v), surface.PointAt(t1, v)
    else:
        p0, p1 = surface.PointAt(u, t0), surface.PointAt(u, t1)
    dt = (t1 - t0) or 1e-12
    return ((p1.X - p0.X) / dt, (p1.Y - p0.Y) / dt, (p1.Z - p0.Z) / dt)


def _wrap_or_clamp(x, dom, closed):
    lo, hi = dom.T0, dom.T1
    if closed and hi > lo:
        span = hi - lo
        while x < lo:
            x += span
        while x >= hi:
            x -= span
        return x
    return lo if x < lo else hi if x > hi else x


def _srf_closed(surface, direction):
    try:
        return bool(surface.IsClosed(direction))
    except Exception:  # noqa: BLE001
        return False
