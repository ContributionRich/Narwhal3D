"""Curve/curve intersection for rhino3dm curves (pure: rhino3dm only, no bpy).

rhino3dm ships only analytic primitive intersections
(`Intersection.LineLine/LineCircle/...`); there is nothing for a general
curve/curve intersection, which `Trim`, `Split`, `Fillet`, `CurveBoolean`,
`Extend` and intersection-`Divide` all need.

ALGORITHM -- recursive bounding-box subdivision:

* A NURBS curve lies inside the bounding box of its control polygon (convex-hull
  property), and `Curve.GetBoundingBox` returns exactly that, so two spans whose
  boxes (inflated by `tol`) do not overlap cannot intersect and are pruned.
* Otherwise each still-curved span is halved with `Curve.Split` (exact, and it
  PRESERVES the original parameterisation -- MEASURED -- so a sub-curve's
  parameter is directly a parameter on the input curve), and the pairs recurse.
* A near-linear span pair is the base case: `Intersection.LineLine` on the two
  chords (falling back to the closest approach of the two segments when the
  chords are parallel -- the tangent case), then a few Newton steps on the true
  sub-curves refine the hit. A depth limit stops the otherwise non-terminating
  recursion at a tangency, where the boxes overlap to arbitrary depth.

ACCURACY: transversal crossings are
refined by Newton to <= 1e-12 (two lines match `Intersection.LineLine` to machine
precision; two circles match the analytic answer to ~1e-12). A tangency is only
located to the span size at the depth limit (~1e-9 in parameter here), because
Newton's Jacobian is singular there; the module reports it, it is not exact.

CASES, none silently wrong:
* no intersection -> `[]` (never None, never an exception)
* tangential contact -> one result (depth limit)
* overlapping / collinear / identical curves -> ONE result flagged `overlap`
  carrying the parameter INTERVAL on each curve, never an arbitrary point from an
  infinite set. Short overlaps (below the sampling resolution) may instead be
  reported as a point -- documented limitation.
* shared endpoints / a crossing on a span boundary -> exactly one result (deduped)
* self-intersection (`a is b`) -> REFUSED with ValueError (not supported)
"""
import math

import rhino3dm as r3

_NEAREST = None


def _nearest():
    """The exact closest-point module, imported lazily (cached), or None if it
    cannot be loaded (then the sampled fallback is used)."""
    global _NEAREST
    if _NEAREST is None:
        try:
            from . import nearest as _n
        except ImportError:
            try:
                import nearest as _n
            except ImportError:
                import os as _os
                import sys as _sys
                _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
                try:
                    import nearest as _n
                except ImportError:
                    _n = False
        _NEAREST = _n
    return _NEAREST or None

# A single result. For a point hit `overlap` is False and the *_end fields are
# None. For an overlap, `overlap` is True, (t_a, t_a_end) and (t_b, t_b_end) are
# the coincident parameter intervals, and `point` is the interval's start.
class CurveX:
    __slots__ = ("point", "t_a", "t_b", "overlap", "t_a_end", "t_b_end")

    def __init__(self, point, t_a, t_b, overlap=False, t_a_end=None, t_b_end=None):
        self.point = point
        self.t_a = t_a
        self.t_b = t_b
        self.overlap = overlap
        self.t_a_end = t_a_end
        self.t_b_end = t_b_end

    def __repr__(self):
        def g(x):
            return "None" if x is None else f"{x:.6g}"
        if self.overlap:
            return (f"CurveX(overlap a=[{g(self.t_a)},{g(self.t_a_end)}] "
                    f"b=[{g(self.t_b)},{g(self.t_b_end)}])")
        return f"CurveX(point={tuple(round(c, 6) for c in self.point)}, " \
               f"t_a={g(self.t_a)}, t_b={g(self.t_b)})"


def curve_curve(a, b, tol=1e-6, max_depth=40):
    """Intersect two rhino3dm curves. Returns a list of `CurveX`, possibly empty.

    `tol` is the distance tolerance used both to inflate bounding boxes and to
    accept a base-case hit. `a is b` is refused (self-intersection unsupported).
    """
    if a is b:
        raise ValueError("curve_curve does not support self-intersection "
                         "(a is b); pass two distinct curves")

    overlap = _detect_overlap(a, b, tol)
    if overlap is not None:
        return [overlap]

    hits = []
    _subdivide(a, b, tol, 0, max_depth, hits)
    return _dedupe(hits, tol)


# ---------------------------------------------------------------------------
# recursion
# ---------------------------------------------------------------------------

def _subdivide(ca, cb, tol, depth, max_depth, out):
    if not _boxes_overlap(ca.GetBoundingBox(), cb.GetBoundingBox(), tol):
        return
    a_lin = _is_linear_enough(ca, tol)
    b_lin = _is_linear_enough(cb, tol)
    forced = depth >= max_depth
    if (a_lin and b_lin) or forced:
        hit = _base_case(ca, cb, tol)
        if hit is not None:
            out.append(hit)
        return
    a_parts = (ca,) if a_lin else (_split_mid(ca) or (ca,))
    b_parts = (cb,) if b_lin else (_split_mid(cb) or (cb,))
    if a_parts == (ca,) and b_parts == (cb,):
        # neither could be split further -> treat as base case (avoid infinite loop)
        hit = _base_case(ca, cb, tol)
        if hit is not None:
            out.append(hit)
        return
    for x in a_parts:
        for y in b_parts:
            _subdivide(x, y, tol, depth + 1, max_depth, out)


def _split_mid(c):
    d = c.Domain
    tm = 0.5 * (d.T0 + d.T1)
    try:
        pieces = c.Split(tm)
    except Exception:  # noqa: BLE001
        return None
    if not pieces or len(pieces) < 2 or pieces[0] is None or pieces[1] is None:
        return None
    return (pieces[0], pieces[1])


def _base_case(ca, cb, tol):
    a0, a1 = _pt(ca.PointAtStart), _pt(ca.PointAtEnd)
    b0, b1 = _pt(cb.PointAtStart), _pt(cb.PointAtEnd)
    # chord parameters u (on a's chord) and v (on b's chord), both in [0, 1]
    la = r3.Line(ca.PointAtStart, ca.PointAtEnd)
    lb = r3.Line(cb.PointAtStart, cb.PointAtEnd)
    ok, u, v = r3.Intersection.LineLine(la, lb)
    if ok and _in01(u) and _in01(v):
        u = _clamp(u, 0.0, 1.0)
        v = _clamp(v, 0.0, 1.0)
    else:
        # parallel chords / crossing outside the segments: closest approach
        u, v = _segment_closest(a0, a1, b0, b1)

    da, db = ca.Domain, cb.Domain
    t_a = da.T0 + (da.T1 - da.T0) * u
    t_b = db.T0 + (db.T1 - db.T0) * v
    t_a, t_b, gap, point = _newton(ca, cb, t_a, t_b)
    if gap > max(tol, 1e-9):
        return None
    return CurveX(point, t_a, t_b)


# ---------------------------------------------------------------------------
# Newton refinement on the true sub-curves
# ---------------------------------------------------------------------------

def _newton(ca, cb, t_a, t_b, iters=20):
    da, db = ca.Domain, cb.Domain
    for _ in range(iters):
        Pa, Pb = ca.PointAt(t_a), cb.PointAt(t_b)
        fx, fy, fz = Pa.X - Pb.X, Pa.Y - Pb.Y, Pa.Z - Pb.Z
        gap = math.sqrt(fx * fx + fy * fy + fz * fz)
        if gap <= 1e-14:
            return t_a, t_b, gap, (Pa.X, Pa.Y, Pa.Z)
        va = _velocity(ca, t_a)
        vb = _velocity(cb, t_b)
        # solve [va, -vb] . [dta, dtb] = -(Pa - Pb), in the plane of largest spread
        step = _solve2(va, vb, (fx, fy, fz))
        if step is None:
            break                       # singular (parallel) -> tangency
        dta, dtb = step
        t_a = _clamp(t_a - dta, da.T0, da.T1)
        t_b = _clamp(t_b - dtb, db.T0, db.T1)
    Pa, Pb = ca.PointAt(t_a), cb.PointAt(t_b)
    gap = math.dist((Pa.X, Pa.Y, Pa.Z), (Pb.X, Pb.Y, Pb.Z))
    mid = ((Pa.X + Pb.X) / 2, (Pa.Y + Pb.Y) / 2, (Pa.Z + Pb.Z) / 2)
    return t_a, t_b, gap, mid


def _solve2(va, vb, f):
    """Solve [va -vb][dta dtb]^T = f for the two best-conditioned coordinates."""
    # unknowns dta, dtb; equations per coordinate: va[k]*dta - vb[k]*dtb = f[k]
    coords = (0, 1, 2)
    best = None
    for i in coords:
        for j in coords:
            if j <= i:
                continue
            a11, a12 = va[i], -vb[i]
            a21, a22 = va[j], -vb[j]
            det = a11 * a22 - a12 * a21
            if best is None or abs(det) > abs(best[0]):
                best = (det, a11, a12, a21, a22, f[i], f[j])
    if best is None or abs(best[0]) < 1e-18:
        return None
    det, a11, a12, a21, a22, c1, c2 = best
    dta = (c1 * a22 - a12 * c2) / det
    dtb = (a11 * c2 - c1 * a21) / det
    return dta, dtb


def _velocity(c, t):
    d = c.Domain
    h = (d.T1 - d.T0) * 1e-6
    if h <= 0:
        h = 1e-9
    t0 = max(t - h, d.T0)
    t1 = min(t + h, d.T1)
    if t1 <= t0:
        t1 = min(d.T1, t0 + h)
    p0, p1 = c.PointAt(t0), c.PointAt(t1)
    dt = (t1 - t0) or 1e-12
    return ((p1.X - p0.X) / dt, (p1.Y - p0.Y) / dt, (p1.Z - p0.Z) / dt)


# ---------------------------------------------------------------------------
# overlap detection
# ---------------------------------------------------------------------------

def _detect_overlap(a, b, tol, samples=120, run=3):
    """Return one overlap CurveX if a contiguous run of `a` lies on `b`, else None.

    A crossing or a tangency touches `b` at an isolated parameter (a run of 1),
    so requiring a run of >= `run` samples distinguishes a genuine overlap from a
    mere touch. Short overlaps below the sample spacing are missed (documented).
    """
    da = a.Domain
    on = []
    for i in range(samples + 1):
        t = da.T0 + (da.T1 - da.T0) * i / samples
        p = a.PointAt(t)
        on.append(_dist_point_to_curve(b, (p.X, p.Y, p.Z)) <= tol)
    # longest contiguous run of True
    best_i = best_len = cur_i = cur_len = 0
    for i, flag in enumerate(on):
        if flag:
            if cur_len == 0:
                cur_i = i
            cur_len += 1
            if cur_len > best_len:
                best_len, best_i = cur_len, cur_i
        else:
            cur_len = 0
    if best_len < run:
        return None
    t0 = da.T0 + (da.T1 - da.T0) * best_i / samples
    t1 = da.T0 + (da.T1 - da.T0) * (best_i + best_len - 1) / samples
    pa0 = a.PointAt(t0)
    tb0 = _closest_param(b, (pa0.X, pa0.Y, pa0.Z))
    pa1 = a.PointAt(t1)
    tb1 = _closest_param(b, (pa1.X, pa1.Y, pa1.Z))
    return CurveX((pa0.X, pa0.Y, pa0.Z), t0, tb0, overlap=True,
                  t_a_end=t1, t_b_end=tb1)


def _dist_point_to_curve(curve, p, samples=200):
    """Distance from `p` to the closest point on `curve`.

    Uses the exact solver `nearest.closest_point_on_curve`, whose tolerance comes from
    `units.py`; the old coarse-sample estimate survives only as a fallback if that route
    raises.
    """
    near = _nearest()
    if near is not None:
        try:
            return near.closest_point_on_curve(curve, p).distance
        except Exception:  # noqa: BLE001
            pass
    q = curve.PointAt(_closest_param_sampled(curve, p, samples))
    return math.dist(p, (q.X, q.Y, q.Z))


def _closest_param(curve, p, samples=200):
    """Parameter of the closest point on `curve` to `p`.

    Delegates to the exact `nearest.closest_point_on_curve`. The old
    coarse-sample-then-golden-section estimate below seeded both this function and the
    overlap detector from 200 uniform samples, which can bracket the wrong local minimum
    when the true closest point falls between samples. It survives only as a fallback
    when the exact route raises.
    """
    near = _nearest()
    if near is not None:
        try:
            return near.closest_point_on_curve(curve, p).t
        except Exception:  # noqa: BLE001
            pass
    return _closest_param_sampled(curve, p, samples)


def _closest_param_sampled(curve, p, samples=200):
    """Parameter of the closest point on `curve` to `p`: coarse sample, then a
    golden-section refine over the best sample's neighbourhood (so the returned
    distance is the true closest distance, not the nearest-sample distance)."""
    d = curve.Domain
    best_t, best = d.T0, math.inf
    for i in range(samples + 1):
        t = d.T0 + (d.T1 - d.T0) * i / samples
        q = curve.PointAt(t)
        dd = math.dist(p, (q.X, q.Y, q.Z))
        if dd < best:
            best, best_t = dd, t
    step = (d.T1 - d.T0) / samples
    lo = max(d.T0, best_t - step)
    hi = min(d.T1, best_t + step)

    def dist_at(t):
        q = curve.PointAt(t)
        return math.dist(p, (q.X, q.Y, q.Z))

    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c1 = hi - gr * (hi - lo)
    c2 = lo + gr * (hi - lo)
    f1, f2 = dist_at(c1), dist_at(c2)
    for _ in range(60):
        if hi - lo < 1e-15:
            break
        if f1 < f2:
            hi, c2, f2 = c2, c1, f1
            c1 = hi - gr * (hi - lo)
            f1 = dist_at(c1)
        else:
            lo, c1, f1 = c1, c2, f2
            c2 = lo + gr * (hi - lo)
            f2 = dist_at(c2)
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# dedupe + small geometry helpers
# ---------------------------------------------------------------------------

def _dedupe(hits, tol):
    thresh = max(tol, 1e-9)
    kept = []
    for h in sorted(hits, key=lambda x: x.t_a):
        if any(math.dist(h.point, k.point) <= thresh for k in kept):
            continue
        kept.append(h)
    return kept


def _boxes_overlap(a, b, tol):
    return (a.Min.X - tol <= b.Max.X and b.Min.X - tol <= a.Max.X and
            a.Min.Y - tol <= b.Max.Y and b.Min.Y - tol <= a.Max.Y and
            a.Min.Z - tol <= b.Max.Z and b.Min.Z - tol <= a.Max.Z)


def _is_linear_enough(c, tol):
    d = c.Domain
    a, b = _pt(c.PointAtStart), _pt(c.PointAtEnd)
    dev = 0.0
    for f in (0.25, 0.5, 0.75):
        t = d.T0 + (d.T1 - d.T0) * f
        dev = max(dev, _point_line_dist(_pt(c.PointAt(t)), a, b))
    return dev <= tol


def _point_line_dist(p, a, b):
    ax, ay, az = a
    bx, by, bz = b
    px, py, pz = p
    ux, uy, uz = bx - ax, by - ay, bz - az
    ulen = math.sqrt(ux * ux + uy * uy + uz * uz)
    if ulen < 1e-15:
        return math.dist(p, a)
    wx, wy, wz = px - ax, py - ay, pz - az
    cx = wy * uz - wz * uy
    cy = wz * ux - wx * uz
    cz = wx * uy - wy * ux
    return math.sqrt(cx * cx + cy * cy + cz * cz) / ulen


def _segment_closest(p1, p2, p3, p4):
    """Parameters (s, t) in [0,1] of the closest points on segments p1p2, p3p4."""
    d1 = tuple(p2[i] - p1[i] for i in range(3))
    d2 = tuple(p4[i] - p3[i] for i in range(3))
    r = tuple(p1[i] - p3[i] for i in range(3))
    a = sum(d1[i] * d1[i] for i in range(3))
    e = sum(d2[i] * d2[i] for i in range(3))
    f = sum(d2[i] * r[i] for i in range(3))
    if a < 1e-18 and e < 1e-18:
        return 0.0, 0.0
    if a < 1e-18:
        return 0.0, _clamp(f / e, 0.0, 1.0)
    c = sum(d1[i] * r[i] for i in range(3))
    if e < 1e-18:
        return _clamp(-c / a, 0.0, 1.0), 0.0
    bb = sum(d1[i] * d2[i] for i in range(3))
    denom = a * e - bb * bb
    s = _clamp((bb * f - c * e) / denom, 0.0, 1.0) if denom > 1e-18 else 0.0
    t = (bb * s + f) / e
    if t < 0.0:
        t = 0.0
        s = _clamp(-c / a, 0.0, 1.0)
    elif t > 1.0:
        t = 1.0
        s = _clamp((bb - c) / a, 0.0, 1.0)
    return s, t


def _pt(p):
    return (p.X, p.Y, p.Z)


def _in01(x, slack=1e-9):
    return -slack <= x <= 1.0 + slack


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


# ---------------------------------------------------------------------------
# Split / Trim a curve where cutters cross it
# ---------------------------------------------------------------------------

def split_curve_at(curve, params):
    """Split `curve` at each parameter in `params`; pieces returned in order.

    Works LEFT TO RIGHT on the shrinking tail. `Curve.Split(t)` PRESERVES the
    original parameterisation, so after splitting off
    ``[start..t1]`` the remaining tail still spans ``[t1..end]`` and the later
    parameters stay valid -- no re-mapping is needed. Parameters at the curve's
    own ends are ignored (they would only shave off an empty piece).
    """
    d = curve.Domain
    ordered = sorted(t for t in params if d.T0 + 1e-9 < t < d.T1 - 1e-9)
    pieces = []
    remaining = curve
    for t in ordered:
        res = remaining.Split(t)
        if not res or len(res) < 2 or res[0] is None or res[1] is None:
            continue
        pieces.append(res[0])
        remaining = res[1]
    pieces.append(remaining)
    return pieces


def split_curve_by(curve, cutters, tol=1e-6):
    """Split `curve` where `cutters` cross it (this is Rhino's Split).

    Returns the pieces in order. When nothing cuts the curve it returns a
    single-element list holding the ORIGINAL curve (not an empty list), so the
    caller can tell "unchanged" from "gone".

    A CLOSED curve needs special handling: it already has a seam, so a single cut
    opens it into ONE curve (closed -> open), and N cuts give N pieces -- not
    N+1. `split_curve_at` on a closed curve yields the extra seam piece, so the
    first and last pieces (which meet at the seam, not at a real cut) are rejoined.
    """
    params = []
    for cutter in cutters:
        for x in curve_curve(curve, cutter, tol):
            if not x.overlap:
                params.append(x.t_a)
    params = _dedupe_params(curve, params)
    if not params:
        return [curve]
    pieces = split_curve_at(curve, params)
    if _is_closed(curve) and len(pieces) >= 2:
        # A cutter crossing AT the seam is a real cut, and then the seam pieces must
        # NOT be rejoined. MEASURED: a circle cut by a diameter through its seam (x axis,
        # t = 0 and pi) came back as ONE piece -- the pi cut opened it and the rejoin
        # closed the seam cut back up -- so Split reported "not crossed".
        d = curve.Domain
        eps = (d.T1 - d.T0) * 1e-7
        if any(abs(t - d.T0) <= eps or abs(t - d.T1) <= eps for t in params):
            return pieces
        # first and last meet at the seam (param 0), which is not a real cut
        rejoined = _join([pieces[-1], pieces[0]])
        return [rejoined] + pieces[1:-1]
    return pieces


def trim_curve_by(curve, cutters, keep_point, tol=1e-6):
    """Rhino's Trim: split by `cutters`, then DISCARD the piece the user clicked.

    `keep_point` is the location clicked to "trim away" (commands/trim.htm: "Select
    the parts ... to trim away"), so the piece nearest it is removed and the rest
    returned. Returns the curve unchanged (single-element list) when nothing cut
    it.
    """
    pieces = split_curve_by(curve, cutters, tol)
    if len(pieces) <= 1:
        return pieces
    drop = min(range(len(pieces)),
               key=lambda i: _dist_point_to_curve(pieces[i], keep_point))
    return [p for i, p in enumerate(pieces) if i != drop]


def _dedupe_params(curve, params):
    d = curve.Domain
    span_tol = (d.T1 - d.T0) * 1e-7
    out = []
    for t in sorted(params):
        if not out or t - out[-1] > span_tol:
            out.append(t)
    return out


def _is_closed(curve):
    try:
        return bool(curve.IsClosed)
    except Exception:  # noqa: BLE001
        return False


def _join(curves):
    pc = r3.PolyCurve()
    for c in curves:
        pc.Append(c)
    return pc


# ---------------------------------------------------------------------------
# Curve / surface intersection
# ---------------------------------------------------------------------------

def curve_plane(curve, origin, normal, tol=1e-6, max_depth=40):
    """Intersect a curve with an infinite PLANE (origin + normal). EXACT.

    This is Project's core. Recursive bounding-box subdivision on the curve
    (`Curve.Split`), pruning a span whose control-hull box lies wholly on one
    side of the plane; the base case is `Intersection.LinePlane` on the near-
    linear span's chord (also the test's ORACLE), Newton-refined on the signed
    distance. Returns a list of `CurveX` (point, curve param in `t_a`, `t_b`
    None); a curve lying IN the plane returns one `overlap` result spanning its
    domain. Measured crossings are accurate to approximately 1e-12.
    """
    normal = _unit3(normal)
    if _curve_in_plane(curve, origin, normal, tol):
        d = curve.Domain
        p0 = curve.PointAt(d.T0)
        return [CurveX(_pt(p0), d.T0, None, overlap=True, t_a_end=d.T1)]
    hits = []
    _cp_subdiv(curve, origin, normal, tol, 0, max_depth, hits)
    return _dedupe(hits, tol)


def curve_surface(curve, surface, tol=1e-6, samples=200):
    """Intersect a curve with a general NURBS SURFACE. APPROXIMATE.

    rhino3dm has no `Surface.ClosestPoint`, so the surface's signed distance is
    found with our own grid-sample-then-refine closest point plus `NormalAt`. The
    curve is sampled; where the signed distance changes sign a crossing is
    bracketed and found by bisection. ACCURACY is bounded by the crossing angle
    and the refine; MEASURED on a planar ruled surface it matches the exact
    curve/plane answer to ~1e-7, and it is stated rather than claimed exact.

    TRIMMED FACES: rhino3dm exposes no trim pcurves, so a hit on the
    untrimmed surface CANNOT be tested for lying inside a face's trim. Hits are
    reported against the untrimmed surface; deciding trim membership is not
    possible here and is not faked.
    """
    d = curve.Domain

    def signed(t):
        p = curve.PointAt(t)
        pt = (p.X, p.Y, p.Z)
        (u, v), q = _surface_closest(surface, pt)
        n = surface.NormalAt(u, v)
        return _dot(_sub3(pt, q), (n.X, n.Y, n.Z))

    ts = [d.T0 + (d.T1 - d.T0) * i / samples for i in range(samples + 1)]
    fs = [signed(t) for t in ts]
    hits = []
    for i in range(samples):
        f0, f1 = fs[i], fs[i + 1]
        if f0 == 0.0 or f0 * f1 < 0.0:
            t = _bisect(signed, ts[i], ts[i + 1], f0, f1)
            p = curve.PointAt(t)
            hits.append(CurveX((p.X, p.Y, p.Z), t, None))
    return _dedupe(hits, tol)


def _cp_subdiv(c, origin, normal, tol, depth, max_depth, out):
    lo, hi = _bbox_signed_range(c.GetBoundingBox(), origin, normal)
    if lo > tol or hi < -tol:
        return                                   # box wholly on one side
    if _is_linear_enough(c, tol) or depth >= max_depth:
        hit = _cp_base(c, origin, normal, tol)
        if hit is not None:
            out.append(hit)
        return
    parts = _split_mid(c)
    if not parts:
        hit = _cp_base(c, origin, normal, tol)
        if hit is not None:
            out.append(hit)
        return
    for x in parts:
        _cp_subdiv(x, origin, normal, tol, depth + 1, max_depth, out)


def _cp_base(c, origin, normal, tol):
    ok, u = r3.Intersection.LinePlane(
        r3.Line(c.PointAtStart, c.PointAtEnd),
        r3.Plane(r3.Point3d(*origin), r3.Vector3d(*normal)))
    if not ok or not _in01(u):
        return None
    d = c.Domain
    t = d.T0 + (d.T1 - d.T0) * _clamp(u, 0.0, 1.0)
    t, gap, point = _cp_newton(c, origin, normal, t)
    if gap > max(tol, 1e-9):
        return None
    return CurveX(point, t, None)


def _cp_newton(c, origin, normal, t, iters=30):
    d = c.Domain
    for _ in range(iters):
        p = c.PointAt(t)
        f = _dot(_sub3(_pt(p), origin), normal)
        if abs(f) <= 1e-14:
            break
        v = _velocity(c, t)
        fp = _dot(v, normal)
        if abs(fp) < 1e-18:
            break
        t = _clamp(t - f / fp, d.T0, d.T1)
    p = c.PointAt(t)
    return t, abs(_dot(_sub3(_pt(p), origin), normal)), _pt(p)


def _curve_in_plane(curve, origin, normal, tol, samples=24):
    d = curve.Domain
    for i in range(samples + 1):
        p = curve.PointAt(d.T0 + (d.T1 - d.T0) * i / samples)
        if abs(_dot(_sub3(_pt(p), origin), normal)) > tol:
            return False
    return True


def _bbox_signed_range(bb, origin, normal):
    lo, hi = math.inf, -math.inf
    for cx in (bb.Min.X, bb.Max.X):
        for cy in (bb.Min.Y, bb.Max.Y):
            for cz in (bb.Min.Z, bb.Max.Z):
                s = _dot(_sub3((cx, cy, cz), origin), normal)
                lo, hi = min(lo, s), max(hi, s)
    return lo, hi


def _bisect(f, a, b, fa, fb, iters=60):
    if fa == 0.0:
        return a
    for _ in range(iters):
        m = 0.5 * (a + b)
        fm = f(m)
        if abs(fm) < 1e-13 or (b - a) < 1e-15:
            return m
        if (fa < 0) != (fm < 0):
            b, fb = m, fm
        else:
            a, fa = m, fm
    return 0.5 * (a + b)


def _surface_closest(surface, p, nu=32, nv=32):
    du, dv = surface.Domain(0), surface.Domain(1)
    best = (du.T0, dv.T0)
    bestd = math.inf
    for i in range(nu + 1):
        u = du.T0 + (du.T1 - du.T0) * i / nu
        for j in range(nv + 1):
            v = dv.T0 + (dv.T1 - dv.T0) * j / nv
            q = surface.PointAt(u, v)
            dd = math.dist(p, (q.X, q.Y, q.Z))
            if dd < bestd:
                bestd, best = dd, (u, v)
    # coordinate-descent golden refine around the best grid node
    u, v = best
    su = (du.T1 - du.T0) / nu
    sv = (dv.T1 - dv.T0) / nv
    for _ in range(6):
        u = _golden(lambda x: math.dist(p, _spt(surface, x, v)),
                    max(du.T0, u - su), min(du.T1, u + su))
        v = _golden(lambda y: math.dist(p, _spt(surface, u, y)),
                    max(dv.T0, v - sv), min(dv.T1, v + sv))
        su *= 0.5
        sv *= 0.5
    return (u, v), _spt(surface, u, v)


def _spt(surface, u, v):
    q = surface.PointAt(u, v)
    return (q.X, q.Y, q.Z)


def _golden(f, lo, hi, iters=40):
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c1, c2 = hi - gr * (hi - lo), lo + gr * (hi - lo)
    f1, f2 = f(c1), f(c2)
    for _ in range(iters):
        if hi - lo < 1e-15:
            break
        if f1 < f2:
            hi, c2, f2 = c2, c1, f1
            c1 = hi - gr * (hi - lo)
            f1 = f(c1)
        else:
            lo, c1, f1 = c1, c2, f2
            c2 = lo + gr * (hi - lo)
            f2 = f(c2)
    return 0.5 * (lo + hi)


def _sub3(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _dist(a, b):
    return math.dist(a, b)


def _unit3(v):
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (0.0, 0.0, 1.0) if m < 1e-15 else (v[0] / m, v[1] / m, v[2] / m)


# ---------------------------------------------------------------------------
# Surface / surface intersection -- the basis for trimming.
#
# Plane/plane is exact through native Intersection.PlanePlane. The general case is a
# marched polyline per branch with reported deviation: seed where the surfaces are close
# (signed-distance sign change on a grid of A), refine each seed onto both surfaces by
# alternating projection with closest_point_on_surface, then walk along cross(nA, nB) --
# the local intersection direction --
# re-refining each step, until the branch closes or leaves a surface. Entry point:
# `surface_surface(a, b)` -> list of `SurfaceX`. Output is a POLYLINE; fitting a
# NURBS curve and the trimming itself are deliberately NOT done here.
# ---------------------------------------------------------------------------

class SurfaceX:
    """One branch of a surface/surface intersection.

    * ``polyline``      -- ordered list of ``(x, y, z)`` along the branch.
    * ``closed``        -- True when the branch is a loop (last ~ first).
    * ``exact``         -- True only for plane/plane (the native PlanePlane line).
    * ``max_deviation`` -- worst distance of a polyline vertex from EITHER surface.
    * ``warning``       -- non-empty when the tolerance could not be met.
    """

    __slots__ = ("polyline", "closed", "exact", "max_deviation", "warning")

    def __init__(self, polyline, closed=False, exact=False, max_deviation=0.0,
                 warning=""):
        self.polyline = polyline
        self.closed = closed
        self.exact = exact
        self.max_deviation = max_deviation
        self.warning = warning

    def __repr__(self):  # pragma: no cover - debugging aid
        kind = "exact" if self.exact else f"approx dev={self.max_deviation:.2e}"
        shape = "closed" if self.closed else "open"
        return f"SurfaceX({len(self.polyline)} pts, {shape}, {kind})"


def surface_surface(a, b, tol=1e-6, step=None):
    """Intersect two surfaces. Returns a list of `SurfaceX`, one per branch,
    possibly empty. Plane/plane is exact via the native `Intersection.PlanePlane`
    (parallel planes -> ``[]``); every other pair is a marched polyline whose
    achieved deviation from both surfaces is reported.
    """
    pa, pb = _surface_as_plane(a), _surface_as_plane(b)
    if pa is not None and pb is not None:
        ok, line = r3.Intersection.PlanePlane(pa, pb)
        if not ok:
            return []
        return [SurfaceX([(line.From.X, line.From.Y, line.From.Z),
                          (line.To.X, line.To.Y, line.To.Z)], exact=True)]
    near = _nearest()
    if near is None:
        return []
    if step is None or step <= 0:
        step = (_srf_bbox_diag(a) or 1.0) / 160.0
    brackets = _ss_brackets(a, b, near, step)
    branches = []
    covered = []
    attempts = 0
    for mid in brackets:
        if any(_dist(mid, q) < 2.0 * step for pl in covered for q in pl):
            continue                            # already on a marched/tried region
        if attempts >= 24:                      # safety valve: a tangent LINE can
            break                               # bracket densely; a few samples settle it
        attempts += 1
        seed, ra, rb = _ss_refine(a, b, mid, near)
        if ra.distance > step or rb.distance > step:
            covered.append([mid])               # bracket did not refine: mark tried
            continue
        if any(_dist(seed, q) < 2.0 * step for pl in covered for q in pl):
            continue
        poly, closed, dev = _ss_march(a, b, seed, near, step, tol)
        if len(poly) >= 3:
            branches.append(SurfaceX(
                poly, closed=closed, max_deviation=dev,
                warning="" if dev <= max(tol, 1e-5)
                else f"intersection polyline off the surfaces by {dev:.3e}"))
            covered.append(poly)
        else:
            covered.append([seed])              # tangent/degenerate: mark tried
    return branches


def _surface_as_plane(s):
    """An `r3.Plane` for a planar surface (built from a mid point + normal, since
    surfaces have no TryGetPlane -- MEASURED), else None."""
    try:
        if not s.IsPlanar():
            return None
    except Exception:  # noqa: BLE001
        return None
    du, dv = s.Domain(0), s.Domain(1)
    um, vm = 0.5 * (du.T0 + du.T1), 0.5 * (dv.T0 + dv.T1)
    o, n = s.PointAt(um, vm), s.NormalAt(um, vm)
    return r3.Plane(r3.Point3d(o.X, o.Y, o.Z), r3.Vector3d(n.X, n.Y, n.Z))


def _ss_brackets(a, b, near, step, na=28, nb=28):
    """Cheap, loosely-deduped seed brackets near the intersection. B is sampled
    ONCE into a point+normal grid; the SIGNED distance of A's grid to the nearest B
    sample is scanned for sign changes (pure arithmetic, no per-point solve). The
    bracket MIDPOINTS are returned deduped by `step` -- the caller refines only the
    few it actually marches, so a long branch costs one exact refine, not hundreds."""
    bdu, bdv = b.Domain(0), b.Domain(1)
    bgrid = []
    for i in range(nb + 1):
        bu = bdu.T0 + (bdu.T1 - bdu.T0) * i / nb
        for j in range(nb + 1):
            pt, nm = b.PointAt(bu, bdv.T0 + (bdv.T1 - bdv.T0) * j / nb), None
            nm = b.NormalAt(bu, bdv.T0 + (bdv.T1 - bdv.T0) * j / nb)
            bgrid.append(((pt.X, pt.Y, pt.Z), (nm.X, nm.Y, nm.Z)))

    def signed(p):
        best = None
        for bp, bn in bgrid:
            dd = ((p[0] - bp[0]) ** 2 + (p[1] - bp[1]) ** 2 + (p[2] - bp[2]) ** 2)
            if best is None or dd < best[0]:
                best = (dd, bp, bn)
        _d, bp, bn = best
        return _dot(_sub3(p, bp), bn)

    du, dv = a.Domain(0), a.Domain(1)
    us = [du.T0 + (du.T1 - du.T0) * i / na for i in range(na + 1)]
    vs = [dv.T0 + (dv.T1 - dv.T0) * j / na for j in range(na + 1)]
    pts = [[_spt(a, u, v) for v in vs] for u in us]
    sgn = [[signed(pts[i][j]) for j in range(na + 1)] for i in range(na + 1)]
    mids = []
    for i in range(na + 1):
        for j in range(na + 1):
            for di, dj in ((1, 0), (0, 1)):
                i2, j2 = i + di, j + dj
                if i2 > na or j2 > na:
                    continue
                if (sgn[i][j] <= 0.0) != (sgn[i2][j2] <= 0.0):
                    mid = tuple(0.5 * (pts[i][j][k] + pts[i2][j2][k]) for k in range(3))
                    if not _dup(mid, mids, step):
                        mids.append(mid)
    return mids


def _ss_refine(a, b, p, near, iters=30):
    """Alternating projection onto A then B with the EXACT solver -> a point ON
    BOTH (used once per branch, where accuracy matters more than speed)."""
    ra = near.closest_point_on_surface(a, p)
    rb = near.closest_point_on_surface(b, ra.point)
    for _ in range(iters):
        ra = near.closest_point_on_surface(a, rb.point)
        nxt = near.closest_point_on_surface(b, ra.point)
        if _dist(nxt.point, rb.point) < 1e-13:
            rb = nxt
            break
        rb = nxt
    return rb.point, near.closest_point_on_surface(a, rb.point), rb


def _ss_march(a, b, seed, near, step, tol):
    """March one branch from `seed` along cross(nA, nB), re-projecting each step with
    the cheap LOCAL projector (seeded from the previous (u,v)) so the hot loop never
    pays for the global solver. Returns ``(polyline, closed, max_deviation)``."""
    rA = near.closest_point_on_surface(a, seed)
    rB = near.closest_point_on_surface(b, seed)
    ca = (_srf_closed(a, 0), _srf_closed(a, 1))
    cb = (_srf_closed(b, 0), _srf_closed(b, 1))
    poly = [seed]
    closed = False
    worst = max(rA.distance, rB.distance)
    for direction in (1.0, -1.0):
        p = seed
        uA, vA, uB, vB = rA.u, rA.v, rB.u, rB.v
        for _ in range(20000):
            na = _srf_normal_local(a, uA, vA)
            nb = _srf_normal_local(b, uB, vB)
            d = _cross3(na, nb)
            dd = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
            if dd < 1e-9:
                break                            # surfaces tangent: no direction
            guess = (p[0] + d[0] / dd * step * direction,
                     p[1] + d[1] / dd * step * direction,
                     p[2] + d[2] / dd * step * direction)
            ba = bb = False
            pa_ = pb_ = guess
            for _round in range(3):              # alternating projection onto both
                uA, vA, pa_, ba = _project_local(a, pb_, uA, vA, ca)
                uB, vB, pb_, bb = _project_local(b, pa_, uB, vB, cb)
            resid = _dist(pa_, pb_)
            if resid > max(tol, 1e-3):
                break                            # fell off the intersection
            q = pb_
            if ba or bb:                         # hit an open surface's boundary
                (poly.append(q) if direction > 0 else poly.insert(0, q))
                worst = max(worst, resid)
                break
            if direction > 0 and len(poly) > 3 and _dist(q, seed) < step * 0.9:
                closed = True
                break
            (poly.append(q) if direction > 0 else poly.insert(0, q))
            worst = max(worst, resid)
            p = q
        if closed:
            break
    return poly, closed, worst


def _project_local(surface, p, u, v, closed, iters=10):
    """Gauss-Newton projection of `p` onto `surface` from the seed ``(u, v)`` --
    local and cheap (finite-difference partials, no multi-seed search), for the
    marching hot loop. Wraps a closed direction and clamps an open one; the
    returned flag is True when it clamped to an OPEN boundary (a real surface edge).
    """
    du, dv = surface.Domain(0), surface.Domain(1)
    cu, cv = closed
    hu = (du.T1 - du.T0) * 1e-6 or 1e-9
    hv = (dv.T1 - dv.T0) * 1e-6 or 1e-9
    on_bound = False
    for _ in range(iters):
        s = _spt(surface, u, v)
        su = _srf_partial(surface, u, v, hu, 0)
        sv = _srf_partial(surface, u, v, hv, 1)
        r = _sub3(s, p)
        aa, bb, cc = _dot(su, su), _dot(su, sv), _dot(sv, sv)
        g1, g2 = _dot(su, r), _dot(sv, r)
        det = aa * cc - bb * bb
        if abs(det) < 1e-18:
            break
        du_ = -(cc * g1 - bb * g2) / det
        dv_ = -(aa * g2 - bb * g1) / det
        u, ou = _wrapclamp(u + du_, du, cu)
        v, ov = _wrapclamp(v + dv_, dv, cv)
        on_bound = ou or ov
        if abs(du_) * math.sqrt(aa) + abs(dv_) * math.sqrt(cc) < 1e-13:
            break
    return u, v, _spt(surface, u, v), on_bound


def _srf_partial(surface, u, v, h, direction):
    du, dv = surface.Domain(0), surface.Domain(1)
    if direction == 0:
        t0, t1 = max(u - h, du.T0), min(u + h, du.T1)
        if t1 <= t0:
            t1 = min(du.T1, t0 + h)
        p0, p1 = surface.PointAt(t0, v), surface.PointAt(t1, v)
    else:
        t0, t1 = max(v - h, dv.T0), min(v + h, dv.T1)
        if t1 <= t0:
            t1 = min(dv.T1, t0 + h)
        p0, p1 = surface.PointAt(u, t0), surface.PointAt(u, t1)
    dt = (t1 - t0) or 1e-12
    return ((p1.X - p0.X) / dt, (p1.Y - p0.Y) / dt, (p1.Z - p0.Z) / dt)


def _srf_normal_local(surface, u, v):
    du, dv = surface.Domain(0), surface.Domain(1)
    hu = (du.T1 - du.T0) * 1e-6 or 1e-9
    hv = (dv.T1 - dv.T0) * 1e-6 or 1e-9
    return _unit3(_cross3(_srf_partial(surface, u, v, hu, 0),
                          _srf_partial(surface, u, v, hv, 1)))


def _wrapclamp(x, dom, closed):
    lo, hi = dom.T0, dom.T1
    if closed and hi > lo:
        span = hi - lo
        while x < lo:
            x += span
        while x >= hi:
            x -= span
        return x, False
    if x < lo:
        return lo, True
    if x > hi:
        return hi, True
    return x, False


def _srf_closed(surface, direction):
    try:
        return bool(surface.IsClosed(direction))
    except Exception:  # noqa: BLE001
        return False


def _cross3(u, v):
    return (u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0])


def _dup(q, seeds, step):
    return any(_dist(q, s) < step for s in seeds)


def _srf_bbox_diag(surface):
    try:
        bb = surface.GetBoundingBox()
        return math.dist((bb.Min.X, bb.Min.Y, bb.Min.Z),
                         (bb.Max.X, bb.Max.Y, bb.Max.Z))
    except Exception:  # noqa: BLE001
        return 0.0
