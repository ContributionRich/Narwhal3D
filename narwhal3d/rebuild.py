"""Reconstruct a curve to a requested degree and control-point count.

This Rust-fit, rhino3dm-based implementation enforces ``point_count >= degree + 1``.
Changing point count can lower degree, while changing degree can raise point count. Closed
curves count the shared seam once. End tangents can be preserved for open curves of degree
two or greater with at least four control points. The maximum supported degree is 11.

The fit uses uniform knots—clamped for open curves and periodic for closed curves—and linear
least squares over arc-length samples of the original. Samples retain their normalized input
parameters, so curves already in the target space are reproduced exactly. Open-curve end
points stay fixed; with ``PreserveTangents``, adjacent control points are constrained to the
end tangent lines.

Maximum deviation is evaluated in both directions with exact closest-point queries rather
than by comparing equal parameter values.
"""
from __future__ import annotations

import math

import rhino3dm as r3

try:                                  # normal import, inside the addon package
    from . import arclength, nearest
except ImportError:                   # support direct module loading
    import arclength
    import nearest

MAX_DEGREE = 11


def normalize_counts(point_count, degree, last_set="Degree"):
    """``(point_count, degree)`` meeting Point count >= Degree + 1, the way the help says:
    "reducing Point count also reduces Degree; increasing Degree also increases Point
    count" -- whichever was set LAST wins."""
    p = max(1, min(int(degree), MAX_DEGREE))
    n = max(2, int(point_count))
    if n < p + 1:
        if last_set == "PointCount":
            p = n - 1
        else:
            n = p + 1
    return n, p


_SINGULAR = "the rebuild fit is singular -- use fewer points"


def _xyz(p):
    return (p.X, p.Y, p.Z)


def _samples(curve, m, closed):
    """``[(original_parameter, point)]`` at `m` positions uniform in ARC LENGTH."""
    length = arclength.PreparedLength(curve)
    total = length.total
    count = m if closed else m + 1
    inner = iter(length.parameters_at([total * i / m for i in range(1, m)]))
    out = []
    for i in range(count):
        t = next(inner) if 0 < i < m else (curve.Domain.T0 if i == 0 else curve.Domain.T1)
        out.append((t, _xyz(curve.PointAt(t))))
    return out


def rebuild_curve(curve, point_count, degree, preserve_tangents=False, samples=None):
    """The rebuilt curve and a report dict. Raises ValueError, by name, for what it cannot
    do. `point_count` counts DISTINCT points for a closed curve (\"one less point\")."""
    crv = curve if isinstance(curve, r3.NurbsCurve) else curve.ToNurbsCurve()
    if crv is None:
        raise ValueError("this curve has no NURBS form to rebuild")
    closed = bool(curve.IsClosed)
    n, p = normalize_counts(point_count, degree)
    if closed and n < 3:
        raise ValueError("a closed curve needs at least 3 points")
    d = crv.Domain
    m = samples or max(20 * n, 400)
    pts = _samples(crv, m, closed)
    notes = []
    # initial parameters: the ORIGINAL curve's, normalised to [0, 1]
    params = [(t - d.T0) / (d.T1 - d.T0) for t, _q in pts]
    out = None
    for _it in range(PARAM_ITERATIONS + 1):
        out = _fit(crv, pts, params, n, p, closed, preserve_tangents, notes)
        # PARAMETER CORRECTION: each sample's parameter becomes that of its closest point on
        # the current fit, then refit. A fit that is already exact is unchanged by it.
        dom = out.Domain
        fit = nearest.PreparedCurve(out)           # `out` is final: pack it once for all samples
        params = [(c.t - dom.T0) / (dom.T1 - dom.T0) for c in fit.closest_many(q for _t, q in pts)]
        if not closed:
            params[0], params[-1] = 0.0, 1.0
    if not out.IsValid:
        raise ValueError("the rebuilt curve is not valid")
    return out, {"point_count": n, "degree": p, "closed": closed,
                 "deviation": max_deviation(crv, out), "notes": sorted(set(notes))}


#: Rounds of parameter correction after the first fit; inherited parameterization alone
#: can noticeably deviate when rebuilding a circle to degree 3 with 8 points.
PARAM_ITERATIONS = 6


def _fit(crv, pts, params, n, p, closed, preserve_tangents, notes):
    """One linear least-squares fit of the samples `pts` at normalised `params`.

    The required native fit owns the numeric solve; Python only orchestrates geometry.
    """
    if closed:
        if preserve_tangents:
            notes.append("PreserveTangents applies to OPEN curves only (ignored)")
        ends = ()
    else:
        start, end = _xyz(crv.PointAtStart), _xyz(crv.PointAtEnd)
        tangent = preserve_tangents and p >= 2 and n >= 4
        if preserve_tangents and not tangent:
            notes.append("PreserveTangents needs degree >= 2 and point count >= 4 (ignored)")
        t0 = _xyz(crv.TangentAtStart)
        t1 = _xyz(crv.TangentAtEnd)
        ends = (start, end, t0, t1) if tangent else (start, end)
    native = _native_fit(pts, params, n, p, closed, ends)
    if native == "singular":
        raise ValueError(_SINGULAR)
    if native == "too large":
        raise ValueError(f"a rebuild to {n} points is too large to solve -- use fewer points")
    ctrl, knots = native
    out = r3.NurbsCurve(3, False, p + 1, len(ctrl))
    for i, c in enumerate(ctrl):
        out.Points[i] = r3.Point4d(c[0], c[1], c[2], 1.0)
    for i in range(len(out.Knots)):
        out.Knots[i] = knots[i]
    return out


def _native_fit(pts, params, n, p, closed, ends):
    try:
        from . import native_rebuild
    except ImportError:          # direct file-path loading, without a package
        import native_rebuild
    return native_rebuild.fit([q for _t, q in pts], params, n, p, closed, ends)


def max_deviation(a, b, samples=200):
    """The larger of the two one-sided maximum distances between curves `a` and `b`, by
    exact closest points at `samples` arc-length-uniform positions on each."""
    worst = 0.0
    for src, dst in ((a, b), (b, a)):
        target = nearest.PreparedCurve(dst)
        for hit in target.closest_many(q for _t, q in _samples(src, samples, bool(src.IsClosed))):
            worst = max(worst, hit.distance)
    return worst
