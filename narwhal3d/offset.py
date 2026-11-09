"""Curve offset in a plane (pure: rhino3dm only, no bpy).

rhino3dm has no `Curve.Offset`. the reference CAD application's Offset command offers these options; this module
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
faked. Where an offset runs past the radius of curvature it cusps at the parameters where
the radius equals the distance; those cusps are kept as kinks and the offset may doubled
back on itself, as the reference's Trim=No does.
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
                            f"offset degenerates (the reference CAD application trims these)")
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

    A general cubic offset is not polynomial. Smooth single NURBS curves first
    use a compact cubic interpolant, refined against true offset targets. If that
    route declines, retain the complete legacy control-polygon refinement and its
    native/compatibility nearest validation. Results remain honestly approximate."""
    compact = _compact_offset(curve, distance, n, tolerance)
    if compact is not None:
        return compact
    return _offset_control_polygon_approx(curve, distance, n, tolerance, max_samples)


def _offset_control_polygon_approx(curve, distance, n, tolerance, max_samples=512):
    """Compatibility route; sampling points are control points, not interpolation data."""
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


def _compact_offset(curve, distance, n, tolerance):
    """OURS: refine a Rust-backed cubic interpolant of TRUE offset targets.

    This is not Rebuild of an already approximate offset. Uniform interpolation
    parameters retain a correspondence with the source; pointwise error bounds
    both directions of the sampled locus (unlike one-sided nearest distances).
    Python only orchestrates existing interpolation/rhino3dm APIs. Their complete
    compatibility implementation is retained when the qualified native ABI declines.

    Do not fit across joined pieces. C0 knots and kinked seams are fitted
    independently, with explicit one-sided constraints, never periodicised.
    Rational INPUT is never rebuilt/mutated; a general offset is non-rational and
    approximate. Exact primitive paths never reach here.
    """
    if not isinstance(curve, r3.NurbsCurve) or curve.Degree < 2:
        return None
    d = curve.Domain
    knots = list(curve.Knots)
    boundaries = sorted(k for k in set(knots)
                        if d.T0 < k < d.T1 and knots.count(k) >= curve.Degree)
    cusps = _cusp_parameters(curve, distance, n)
    boundaries = sorted(set(boundaries) | set(cusps))
    kinked_seam = (curve.IsClosed and
                   _dist(_pt(curve.TangentAtStart), _pt(curve.TangentAtEnd)) > 1e-8)
    if boundaries or kinked_seam:
        try:
            compact = _compact_offset_boundaries(curve, distance, n, tolerance, boundaries, cusps)
        except ValueError:  # existing interpolation can decline degenerate samples
            compact = None
        if compact is not None:
            return compact
        # The legacy whole-curve fit would erase these known tangent boundaries.
        # Refuse explicitly rather than make an unqualified smooth replacement.
        return OffsetResult(None, False, 0.0,
                            "boundary-preserving free-form offset could not meet tolerance; "
                            "larger sharp-corner treatment is not implemented yet")
    try:
        from . import interpolate
    except ImportError:
        import interpolate
    # Leave headroom for unsampled extrema; never increase the caller's tolerance.
    limit = tolerance * 0.8
    for count in (16, 24, 36, 54, 64, 81, 122):
        pts = [_offset_point(curve, d.T0 + (d.T1 - d.T0) * i / count,
                             distance, n)
               for i in range(count + (not curve.IsClosed))]
        try:
            fit = (interpolate.interpolate_closed_curve(pts, "Uniform")
                   if curve.IsClosed else
                   interpolate.interpolate_curve(pts, style="Uniform"))
        except (ValueError, interpolate.InterpolationError):
            return None
        if bool(fit.IsClosed) != bool(curve.IsClosed):
            continue
        fit.Domain = r3.Interval(d.T0, d.T1)
        dev = _offset_correspondence_error(curve, fit, distance, n, 256)
        if not math.isfinite(dev) or dev > limit:
            continue
        dev = _offset_correspondence_error(curve, fit, distance, n, 4096)
        if math.isfinite(dev) and dev <= limit:
            return OffsetResult(fit, False, dev, "")
    return None


def _compact_offset_boundaries(curve, distance, n, tolerance, boundaries, cusps=()):
    """Fit independently on either side of full-multiplicity source knots.

    OURS: a Sharp corner is the apex where the two offset tangent lines meet (the reference
    application's Sharp: extend the adjacent offset segments). The apex must lie within 20% of the
    tolerance of the true offset of the corner vertex; a larger corner declines by name,
    never to a smooth whole-curve replacement. Within that band a small miter moves the
    one-sided endpoints along their tangents; a larger miter is realised exactly: an outer
    corner by two straight tangent segments, an inner corner by trimming each span where
    it crosses the apex. Cusps are boundaries too: a piece between boundaries runs one way
    (its offset speed keeps one sign), and its endpoint tangents carry that direction, so
    the cusp is a true kink. Dense TRUE-target validation includes both one-sided limits
    after all endpoint constraints.
    """
    breaks = [curve.Domain.T0] + boundaries + [curve.Domain.T1]
    spans = [curve.Trim(a, b) for a, b in zip(breaks, breaks[1:])]
    if any(s is None for s in spans):
        return None
    ends = [[_offset_point(s, s.Domain.T0, distance, n),
             _offset_point(s, s.Domain.T1, distance, n)] for s in spans]
    joins = len(spans) if curve.IsClosed else len(spans) - 1
    fills, trim_end, trim_start = {}, {}, {}
    for i in range(joins):
        j = (i + 1) % len(spans)
        a, b = ends[i][1], ends[j][0]
        u, v = _pt(spans[i].TangentAtEnd), _pt(spans[j].TangentAtStart)
        if _dist(a, b) < 1e-9:
            meet = _scale(_add(a, b), 0.5)
        else:
            meet = _line_line_point(a, _add(a, u), b, _add(b, v))
        if (meet is None or not all(math.isfinite(x) for x in meet)
                or _dist_point_to_line(meet, a, u) > 1e-10
                or _dist_point_to_line(meet, b, v) > 1e-10):
            return None
        vertex = _pt(spans[i].PointAtEnd)
        if abs(_dist(meet, vertex) - abs(distance)) > tolerance * 0.2:
            return None
        if max(_dist(meet, a), _dist(meet, b)) <= tolerance * 0.2:
            ends[i][1] = ends[j][0] = meet
            continue
        along_a = _dot(_sub(meet, a), u)
        along_b = _dot(_sub(meet, b), v)
        if along_a >= 0.0 and along_b <= 0.0:
            fills[i] = (a, meet, b)
        elif along_a <= 0.0 and along_b >= 0.0:
            ta = _inner_trim_param(spans[i], meet, distance, n, True, tolerance)
            tb = _inner_trim_param(spans[j], meet, distance, n, False, tolerance)
            if ta is None or tb is None:
                return None
            trim_end[i], trim_start[j] = ta, tb
            ends[i][1] = ends[j][0] = meet
        else:
            return None
    for k, t in trim_end.items():
        spans[k] = spans[k].Trim(spans[k].Domain.T0, t)
    for k, t in trim_start.items():
        spans[k] = spans[k].Trim(t, spans[k].Domain.T1)
    if any(s is None for s in spans):
        return None
    fits, worst = [], 0.0
    try:
        from . import interpolate, native_interpolate
    except ImportError:
        import interpolate
        import native_interpolate
    for k, (source, (start, end)) in enumerate(zip(spans, ends)):
        d = source.Domain
        accepted = None
        direction = _offset_direction(source, distance, n)
        cusp_start, cusp_end = breaks[k] in cusps, breaks[k + 1] in cusps
        for count in (3, 5, 8, 12, 16, 24, 36, 48, 64, 96):
            pts = [_offset_point(source, d.T0 + (d.T1 - d.T0) * i / count, distance, n)
                   for i in range(count + 1)]
            # Small independent spans need fewer than the public interpolator's
            # native crossover of 16 points. Reuse the SAME existing ABI directly;
            # no new solver, binary or global crossover setting is introduced.
            native = native_interpolate.interpolate(pts, 3, "Uniform")
            if native is None:
                fit = interpolate.interpolate_curve(pts, style="Uniform")
            else:
                ctrl, knots, degree = native
                fit = r3.NurbsCurve(3, False, degree + 1, len(ctrl))
                for k, p in enumerate(ctrl):
                    fit.Points[k] = r3.Point4d(*p, 1.0)
                for k, value in enumerate(knots[1:-1]):
                    fit.Knots[k] = value
            fit.Domain = r3.Interval(d.T0, d.T1)
            # Keep each one-sided tangent explicitly. Moving its adjacent control
            # to the endpoint tangent line constrains direction, not just position.
            for endpoint, adjacent, target, tangent, at_cusp in (
                    (0, 1, start, _scale(_pt(source.TangentAtStart), direction), cusp_start),
                    (len(fit.Points)-1, len(fit.Points)-2, end,
                     _scale(_pt(source.TangentAtEnd), direction), cusp_end)):
                old_end = _pt(fit.Points[endpoint])
                arm = _dot(_sub(_pt(fit.Points[adjacent]), old_end), tangent)
                if at_cusp:
                    arm = 0.0  # zero offset speed at a cusp: the handle collapses onto the end
                elif (endpoint == 0 and arm <= 0) or (endpoint != 0 and arm >= 0):
                    break  # reversed/cusped span: do not invent a tangent direction
                fit.Points[endpoint] = r3.Point4d(*target, 1.0)
                fit.Points[adjacent] = r3.Point4d(*_add(target, _scale(tangent, arm)), 1.0)
            else:
                dev = _offset_correspondence_error(source, fit, distance, n, 256)
                if math.isfinite(dev) and dev <= tolerance * 0.8:
                    # Keep at least 256 checks per span and at least the smooth
                    # route's 4096 checks over the full source domain, rather than
                    # paying 4096 checks for EACH tiny imported Bezier span.
                    checks = max(256, math.ceil(4096 * (d.T1-d.T0) /
                                                (curve.Domain.T1-curve.Domain.T0)))
                    dev = _offset_correspondence_error(source, fit, distance, n, checks)
                    if math.isfinite(dev) and dev <= tolerance * 0.8:
                        accepted = fit
                        worst = max(worst, dev)
                        break
        if accepted is None:
            return None
        fits.append(accepted)
    # Concatenate clamped cubic control nets WITHOUT a fit across the joins.
    # Shared positions, multiplicity-three knots: C0 topology with the measured
    # one-sided directions intact, including the closed nonperiodic seam.
    pieces = []
    for k, fit in enumerate(fits):
        pieces.append(fit)
        if k in fills:
            a, m, b = fills[k]
            pieces.extend((_line_piece(a, m), _line_piece(m, b)))
    s = curve.Domain.T0
    for piece in pieces:
        length = piece.Domain.T1 - piece.Domain.T0
        piece.Domain = r3.Interval(s, s + length)
        s += length
    controls = list(pieces[0].Points)
    knots = list(pieces[0].Knots)
    for piece in pieces[1:]:
        controls.extend(list(piece.Points)[1:])
        knots.extend(list(piece.Knots)[3:])
    out = r3.NurbsCurve(3, False, 4, len(controls))
    for i, point in enumerate(controls):
        out.Points[i] = point
    for i, knot in enumerate(knots):
        out.Knots[i] = knot
    if not out.IsValid or bool(out.IsClosed) != bool(curve.IsClosed):
        return None
    out.Domain = r3.Interval(curve.Domain.T0, curve.Domain.T1)
    return OffsetResult(out, False, worst, "")


def _line_piece(a, b):
    """OURS: the straight segment a-b as an exact cubic (collinear control points)."""
    seg = r3.NurbsCurve(3, False, 4, 4)
    for k in range(4):
        seg.Points[k] = r3.Point4d(*_add(a, _scale(_sub(b, a), k / 3.0)), 1.0)
    for k, value in enumerate((0.0, 0.0, 0.0, 1.0, 1.0, 1.0)):
        seg.Knots[k] = value
    seg.Domain = r3.Interval(0.0, _dist(a, b))
    return seg


def _inner_trim_param(span, point, distance, n, end, tolerance):
    """OURS: the source parameter at which an inner corner's offset reaches `point`.

    On the inner side the two offset pieces overlap, and Sharp trims each back to the
    apex. Searches inward from the span's end (or start) for the crossing of the apex's
    tangent plane, and accepts it only if the offset point there is within 20% of the
    tolerance of `point`; otherwise None.
    """
    d = span.Domain
    edge = d.T1 if end else d.T0
    direction = _pt(span.TangentAtEnd if end else span.TangentAtStart)
    step = -1.0 if end else 1.0
    span_len = d.T1 - d.T0

    def side(t):
        return _dot(_sub(_offset_point(span, t, distance, n), point), direction)

    at_edge = side(edge)
    if at_edge == 0.0:
        return None
    w = span_len / 64.0
    while True:
        t = edge + step * w
        if side(t) * at_edge <= 0.0:
            break
        if w >= span_len:
            return None
        w = min(2.0 * w, span_len)
    outside, inside = edge, t
    for _ in range(80):
        mid = 0.5 * (outside + inside)
        if side(mid) * at_edge > 0.0:
            outside = mid
        else:
            inside = mid
    if not d.T0 < inside < d.T1:
        return None
    if _dist(_offset_point(span, inside, distance, n), point) > tolerance * 0.2:
        return None
    return inside


def _offset_speed(curve, t, distance, n):
    """OURS: 1 - distance * kappa_s at t, kappa_s the curvature toward the offset side.

    With N = n x T the offset's derivative is C'(t) * (1 - distance * kappa_s), so the sign
    is the direction the offset runs, and a zero is a cusp: |distance| equals the radius of
    curvature on that side. The offset is the true parallel curve and is never refused here.
    """
    normal = _unit(_cross(n, _pt(curve.TangentAt(t))))
    k = curve.CurvatureAt(t)
    return 1.0 - distance * _dot((k.X, k.Y, k.Z), normal)


def _offset_direction(curve, distance, n):
    """+1 where the offset runs with the curve, -1 where it doubles back between two cusps."""
    d = curve.Domain
    return 1.0 if _offset_speed(curve, 0.5 * (d.T0 + d.T1), distance, n) >= 0.0 else -1.0


def _cusp_parameters(curve, distance, n, per_span=32):
    """OURS: parameters where the offset cusps, found per knot span.

    The sign of `_offset_speed` is sampled on a fixed grid of `per_span` steps in each span
    (rhino3dm CurvatureAt only), and every sign change is bisected to full precision. A cusp
    pair closer together than the grid is not bracketed: the piece that holds it then fails
    the validated tolerance rather than passing silently.
    """
    d = curve.Domain
    breaks = sorted({d.T0, d.T1} | {k for k in curve.Knots if d.T0 < k < d.T1})
    cusps = []
    for a, b in zip(breaks, breaks[1:]):
        xs = [a + (b - a) * i / per_span for i in range(per_span + 1)]
        vals = [_offset_speed(curve, x, distance, n) for x in xs]
        for i in range(per_span):
            if vals[i] * vals[i + 1] >= 0.0:
                continue
            lo, hi = xs[i], xs[i + 1]
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                if _offset_speed(curve, mid, distance, n) * vals[i] > 0.0:
                    lo = mid
                else:
                    hi = mid
            cusp = 0.5 * (lo + hi)
            if min(cusp - a, b - cusp) > 1e-9 * (b - a):
                cusps.append(cusp)
    return cusps


def _dist_point_to_line(point, origin, tangent):
    return _dist(_sub(point, origin), _scale(tangent, _dot(_sub(point, origin), tangent)))


def _offset_correspondence_error(curve, fit, distance, n, samples):
    """Conservative sampled pointwise error, including short source/fit knot spans.

    Uniform samples alone can miss a feature on a highly nonuniform source.
    Check inside every knot span as well, independently of fitting data sites.
    The value is measured, not a certified continuous supremum.
    """
    d = curve.Domain
    params = {d.T0 + (d.T1 - d.T0) * i / samples
              for i in range(samples + 1)}
    for crv in (curve, fit):
        breaks = sorted({d.T0, d.T1} | {
            k for k in crv.Knots if d.T0 < k < d.T1})
        for a, b in zip(breaks, breaks[1:]):
            params.update(a + (b - a) * i / 16 for i in range(17))
    worst = 0.0
    for t in params:
        error = _dist(_offset_point(curve, t, distance, n), _pt(fit.PointAt(t)))
        if not math.isfinite(error):
            return math.inf
        worst = max(worst, error)
    return worst


def _offset_deviation(curve, fit, distance, n, samples=200):
    native = _native_deviation(curve, fit, distance, n, samples)
    if native is not None:
        return native
    d = curve.Domain
    worst = 0.0
    for i in range(samples + 1):
        target = _offset_point(curve, d.T0 + (d.T1 - d.T0) * i / samples, distance, n)
        worst = max(worst, _dist_point_to_curve(fit, target))
    return worst


def _native_deviation(curve, fit, distance, n, samples):
    """The same max deviation, each nearest point found by the Rust search on the fit's NURBS
    form: one `nearest.PreparedCurve(fit)` per check, one native query per target.

    OURS (correctness): the native distance is to `fit.PointAt(t)` at the point it finds, which
    is the global nearest, so it can be LOWER than the legacy scan's where that scan settled in
    a farther basin (a cusp). A lower deviation is a truer one, not a relaxed tolerance, and the
    sampling, the tolerance and the refinement schedule are unchanged. A cusp can therefore be
    accepted with fewer samples.

    Returns None, so the caller runs the legacy scan for the whole check, when the native library
    is missing or any query declines: a partly native measurement is not the same measurement.
    """
    try:
        from . import nearest, native_nearest
    except ImportError:  # support direct-file loading outside the package
        import nearest
        import native_nearest
    if not isinstance(fit, r3.NurbsCurve) or not native_nearest.available():
        return None
    before = native_nearest._native_calls
    prepared = nearest.PreparedCurve(fit)
    d = curve.Domain
    worst = 0.0
    for i in range(samples + 1):
        target = _offset_point(curve, d.T0 + (d.T1 - d.T0) * i / samples, distance, n)
        worst = max(worst, prepared.closest(target).distance)
        if native_nearest._native_calls - before != i + 1:
            return None
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
