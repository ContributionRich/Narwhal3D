"""Reconstruct a curve to a requested degree and control-point count.

This pure-Python, rhino3dm-based implementation enforces ``point_count >= degree + 1``.
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


def _basis(full, p, t, ncp):
    """All `ncp` B-spline basis values at `t` (Cox-de Boor over the FULL knot vector)."""
    lo, hi = full[p], full[ncp]
    t = min(max(t, lo), hi)
    span = p
    for k in range(p, ncp):
        if full[k] <= t < full[k + 1]:
            span = k
            break
    else:
        span = ncp - 1
    left, right = [0.0] * (p + 1), [0.0] * (p + 1)
    nb = [1.0] + [0.0] * p
    for j in range(1, p + 1):
        left[j] = t - full[span + 1 - j]
        right[j] = full[span + j] - t
        saved = 0.0
        for r in range(j):
            denom = right[r + 1] + left[j - r]
            tmp = nb[r] / denom if denom else 0.0
            nb[r] = saved + right[r + 1] * tmp
            saved = left[j - r] * tmp
        nb[j] = saved
    out = [0.0] * ncp
    for r in range(p + 1):
        out[span - p + r] = nb[r]
    return out


def _solve(a, b):
    """Solve the square system a x = b (Gaussian elimination, partial pivoting)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-300:
            raise ValueError("the rebuild fit is singular -- use fewer points")
        m[c], m[piv] = m[piv], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def _least_squares(rows, rhs):
    """Minimise |A x - b| via the normal equations."""
    k = len(rows[0])
    ata = [[0.0] * k for _ in range(k)]
    atb = [0.0] * k
    for row, y in zip(rows, rhs):
        nz = [(i, v) for i, v in enumerate(row) if v]
        for i, vi in nz:
            atb[i] += vi * y
            for j, vj in nz:
                ata[i][j] += vi * vj
    return _solve(ata, atb)


def _xyz(p):
    return (p.X, p.Y, p.Z)


def _samples(curve, m, closed):
    """``[(original_parameter, point)]`` at `m` positions uniform in ARC LENGTH."""
    total = arclength.length_of(curve)
    count = m if closed else m + 1
    out = []
    for i in range(count):
        s = total * i / m
        t = arclength.parameter_at_length(curve, s) if 0 < i < m else (
            curve.Domain.T0 if i == 0 else curve.Domain.T1)
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
        params = [(nearest.closest_point_on_curve(out, q).t - dom.T0) / (dom.T1 - dom.T0)
                  for _t, q in pts]
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
    """One linear least-squares fit of the samples `pts` at normalised `params`."""
    if closed:
        ncp = n + p                                   # wrapped control points
        full = [float(i) for i in range(-1, ncp + p)]  # uniform, periodic
        lo, hi = full[p], full[ncp]
        rows, rhs = [], []
        for (t, q), un in zip(pts, params):
            u = lo + (hi - lo) * (un % 1.0)
            b = _basis(full, p, u, ncp)
            folded = [0.0] * n
            for j, v in enumerate(b):
                folded[j % n] += v
            for axis in range(3):
                row = [0.0] * (3 * n)
                for j in range(n):
                    row[axis * n + j] = folded[j]
                rows.append(row)
                rhs.append(q[axis])
        x = _least_squares(rows, rhs)
        ctrl = [(x[j], x[n + j], x[2 * n + j]) for j in range(n)]
        ctrl = ctrl + ctrl[:p]
        out = r3.NurbsCurve(3, False, p + 1, ncp)
        for i, c in enumerate(ctrl):
            out.Points[i] = r3.Point4d(c[0], c[1], c[2], 1.0)
        for i in range(len(out.Knots)):
            out.Knots[i] = full[i + 1]
        if preserve_tangents:
            notes.append("PreserveTangents applies to OPEN curves only (ignored)")
    else:
        ncp = n
        inner = n - p - 1
        full = [0.0] * (p + 1) + [(i + 1) / (inner + 1) for i in range(inner)] + [1.0] * (p + 1)
        start, end = _xyz(crv.PointAtStart), _xyz(crv.PointAtEnd)
        tangent = preserve_tangents and p >= 2 and n >= 4
        if preserve_tangents and not tangent:
            notes.append("PreserveTangents needs degree >= 2 and point count >= 4 (ignored)")
        t0 = _xyz(crv.TangentAtStart)
        t1 = _xyz(crv.TangentAtEnd)
        free = list(range(2, n - 2)) if tangent else list(range(1, n - 1))
        # unknowns: [a, b] (tangent lengths, if constrained) + 3 coords of each free point
        nk = (2 if tangent else 0) + 3 * len(free)
        rows, rhs = [], []
        for (t, q), u in zip(pts, params):
            b = _basis(full, p, u, ncp)
            for axis in range(3):
                row = [0.0] * nk
                known = b[0] * start[axis] + b[n - 1] * end[axis]
                if tangent:
                    # P1 = start + a t0,  P(n-2) = end - b t1
                    known += b[1] * start[axis] + b[n - 2] * end[axis]
                    row[0] = b[1] * t0[axis]
                    row[1] = -b[n - 2] * t1[axis]
                off = 2 if tangent else 0
                for fi, j in enumerate(free):
                    row[off + 3 * fi + axis] = b[j]
                rows.append(row)
                rhs.append(q[axis] - known)
        x = _least_squares(rows, rhs)
        ctrl = [None] * n
        ctrl[0], ctrl[n - 1] = start, end
        off = 2 if tangent else 0
        if tangent:
            ctrl[1] = tuple(start[i] + x[0] * t0[i] for i in range(3))
            ctrl[n - 2] = tuple(end[i] - x[1] * t1[i] for i in range(3))
        for fi, j in enumerate(free):
            ctrl[j] = tuple(x[off + 3 * fi + i] for i in range(3))
        out = r3.NurbsCurve(3, False, p + 1, ncp)
        for i, c in enumerate(ctrl):
            out.Points[i] = r3.Point4d(c[0], c[1], c[2], 1.0)
        for i in range(len(out.Knots)):
            out.Knots[i] = full[i + 1]
    return out


def max_deviation(a, b, samples=200):
    """The larger of the two one-sided maximum distances between curves `a` and `b`, by
    exact closest points at `samples` arc-length-uniform positions on each."""
    worst = 0.0
    for src, dst in ((a, b), (b, a)):
        for _t, q in _samples(src, samples, bool(src.IsClosed)):
            worst = max(worst, nearest.closest_point_on_curve(dst, q).distance)
    return worst
