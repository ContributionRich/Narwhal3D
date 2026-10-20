"""Exact circles tangent to curves for ``Circle Tangent`` and ``Arc Tangent``.

Two curves plus a radius use the fillet kernel: closed-form solutions for lines and arcs,
and Newton iteration for general curves. A corner can have several valid circles, so the
solution whose tangent points are nearest the user's picks is selected.

Three lines use the incircle/excircle closed form. The centre ``c`` and radius ``r`` solve
``n_i. (c - p_i) = s_i r`` for the three in-plane normals, one linear 3x3 system per sign
pattern; the candidate nearest the picks wins.

Tangent points must lie within the picked curves' extents. Three-curve cases that are not
all lines, and configurations with no solution, are refused rather than approximated.
"""
from __future__ import annotations

import math

try:                                  # normal import, inside the addon package
    from . import fillet
except ImportError:                   # support direct module loading
    import fillet


class TangentCircle:
    """A circle tangent to curves: centre, radius, plane normal and the tangent points
    (in pick order)."""

    __slots__ = ("center", "radius", "normal", "tangents")

    def __init__(self, center, radius, normal, tangents):
        self.center = tuple(center)
        self.radius = float(radius)
        self.normal = tuple(normal)
        self.tangents = [tuple(t) for t in tangents]

    def __repr__(self):
        return f"TangentCircle(c={self.center}, r={self.radius:g})"


def _pt(p):
    return (float(p[0]), float(p[1]), float(p[2])) if not hasattr(p, "X") \
        else (float(p.X), float(p.Y), float(p.Z))


def _score(tangents, picks):
    return sum(math.dist(t, p) for t, p in zip(tangents, picks))


def circle_tangent_to_two(curve_a, curve_b, radius, pick_a, pick_b, tol=1e-7):
    """The circle of `radius` tangent to `curve_a` and `curve_b`, nearest the picks."""
    if not radius > 0:
        raise ValueError("the radius must be positive")
    pick_a, pick_b = _pt(pick_a), _pt(pick_b)
    pa, pb = fillet._primitive(curve_a), fillet._primitive(curve_b)
    if pa is not None and pb is not None:
        cands = _closed_form_two(pa, pb, radius, tol)
    else:
        cands = _general_two(curve_a, curve_b, radius, pick_a, pick_b, tol)
    if not cands:
        raise ValueError(f"no circle of radius {radius:g} is tangent to both curves "
                         f"within their extents")
    best = min(cands, key=lambda c: _score(c.tangents, (pick_a, pick_b)))
    return best


def _closed_form_two(pa, pb, radius, tol):
    O, e1, e2, n = _plane_of(pa, pb)
    la, lb = fillet._to_plane(pa, O, e1, e2), fillet._to_plane(pb, O, e1, e2)
    out = []
    for ca in fillet._offset_loci(la, radius):
        for cb in fillet._offset_loci(lb, radius):
            for c in fillet._locus_intersections(ca, cb):
                ta, tb = fillet._tangent_point(la, c), fillet._tangent_point(lb, c)
                if ta is None or tb is None:
                    continue
                if abs(math.dist(c, ta) - radius) > 1e-6 or \
                        abs(math.dist(c, tb) - radius) > 1e-6:
                    continue
                ta3, tb3 = fillet._from_plane(ta, O, e1, e2), fillet._from_plane(tb, O, e1, e2)
                if not fillet._on_input(pa, ta3, tol) or not fillet._on_input(pb, tb3, tol):
                    continue
                out.append(TangentCircle(fillet._from_plane(c, O, e1, e2), radius, n,
                                         [ta3, tb3]))
    return out


def _plane_of(pa, pb):
    """The plane both primitives lie in. Two PARALLEL lines are handled here (the fillet's
    `_common_plane` falls back to World Z for them, which is wrong off the XY plane)."""
    if pa[0] == "line" and pb[0] == "line":
        da = fillet._unit(fillet._sub(pa[2], pa[1]))
        db = fillet._unit(fillet._sub(pb[2], pb[1]))
        n = fillet._unit(fillet._cross(da, db))
        if n == (0.0, 0.0, 0.0):
            n = fillet._unit(fillet._cross(da, fillet._sub(pb[1], pa[1])))
        if n == (0.0, 0.0, 0.0):
            raise ValueError("the two lines are the same line")
        e2 = fillet._unit(fillet._cross(n, da))
        return pa[1], da, e2, n
    return fillet._common_plane(pa, pb)


def _general_two(a, b, radius, pick_a, pick_b, tol):
    """Newton solve seeded at the picks, returning every converged on-curve circle."""
    near = fillet._nearest()
    fa = near.closest_point_on_curve(a, pick_a)
    fb = near.closest_point_on_curve(b, pick_b)
    tan_a, tan_b = fillet._curve_tangent(a, fa.t), fillet._curve_tangent(b, fb.t)
    n = fillet._unit(fillet._cross(tan_a, tan_b))
    if n == (0.0, 0.0, 0.0):
        n = fillet._unit(fillet._cross(tan_a, fillet._sub(fb.point, fa.point)))
    if n == (0.0, 0.0, 0.0):
        raise ValueError("the curves give no plane for a tangent circle")
    e1 = fillet._unit(tan_a)
    e2 = fillet._unit(fillet._cross(n, e1))
    O = tuple((fa.point[i] + fb.point[i]) / 2.0 for i in range(3))
    na2 = fillet._rot90(fillet._proj2(tan_a, e1, e2))
    nb2 = fillet._rot90(fillet._proj2(tan_b, e1, e2))
    out = []
    for origin in (O, fa.point, fb.point):
        base = fillet._proj(origin, O, e1, e2)
        for sa in (1.0, -1.0):
            for sb in (1.0, -1.0):
                seed = (base[0] + radius * (sa * na2[0] + sb * nb2[0]),
                        base[1] + radius * (sa * na2[1] + sb * nb2[1]))
                sol = fillet._fillet_newton(a, b, radius, O, e1, e2, seed, near, tol)
                if sol is None:
                    continue
                c3, f1, f2 = sol
                if all(math.dist(c3, k.center) > 1e-7 for k in out):
                    out.append(TangentCircle(c3, radius, n, [f1, f2]))
    return out


def circle_tangent_to_three_lines(lines, picks, tol=1e-9):
    """The incircle / excircle tangent to three coplanar LINES, nearest the picks."""
    prims = [fillet._primitive(c) for c in lines]
    if any(p is None or p[0] != "line" for p in prims):
        raise ValueError("a circle tangent to THREE curves is built for three lines; "
                         "curves and arcs need the general (Apollonius) solver, which "
                         "is not implemented yet")
    picks = [_pt(p) for p in picks]
    O, e1, e2, n = _plane_of(prims[0], prims[1])
    for p in prims[2:]:
        for q in (p[1], p[2]):
            if abs(fillet._dot(fillet._sub(q, O), n)) > 1e-9 * max(1.0, math.dist(q, O)):
                raise ValueError("the three lines are not in one plane")
    rows = []
    for p in prims:
        a, b = fillet._proj(p[1], O, e1, e2), fillet._proj(p[2], O, e1, e2)
        d = (b[0] - a[0], b[1] - a[1])
        L = math.hypot(*d)
        if L < 1e-15:
            raise ValueError("a line has zero length")
        nrm = (-d[1] / L, d[0] / L)
        rows.append((nrm, nrm[0] * a[0] + nrm[1] * a[1]))
    cands = []
    for s in ((1, 1, 1), (1, 1, -1), (1, -1, 1), (-1, 1, 1),
              (1, -1, -1), (-1, 1, -1), (-1, -1, 1), (-1, -1, -1)):
        # n_i. c - s_i r = k_i  ->  [n_ix, n_iy, -s_i] [cx, cy, r]^T = k_i
        M = [[rows[i][0][0], rows[i][0][1], -s[i]] for i in range(3)]
        k = [rows[i][1] for i in range(3)]
        sol = _solve3(M, k)
        if sol is None or sol[2] <= 1e-12:
            continue
        c2, r = (sol[0], sol[1]), sol[2]
        tans = [fillet._from_plane((c2[0] - s[i] * r * rows[i][0][0],
                                    c2[1] - s[i] * r * rows[i][0][1]), O, e1, e2)
                for i in range(3)]
        if not all(fillet._on_input(prims[i], tans[i], 1e-9) for i in range(3)):
            continue
        c3 = fillet._from_plane(c2, O, e1, e2)
        if all(math.dist(c3, k_.center) > 1e-9 for k_ in cands):
            cands.append(TangentCircle(c3, r, n, tans))
    if not cands:
        raise ValueError("no circle is tangent to all three lines within their extents "
                         "(two may be parallel, or the lines do not bound a corner)")
    return min(cands, key=lambda c: _score(c.tangents, picks))


def _solve3(M, k):
    """Cramer's rule; None when singular (two lines parallel for that sign pattern)."""
    def det(A):
        return (A[0][0] * (A[1][1] * A[2][2] - A[1][2] * A[2][1])
                - A[0][1] * (A[1][0] * A[2][2] - A[1][2] * A[2][0])
                + A[0][2] * (A[1][0] * A[2][1] - A[1][1] * A[2][0]))
    D = det(M)
    if abs(D) < 1e-12:
        return None
    out = []
    for j in range(3):
        A = [row[:] for row in M]
        for i in range(3):
            A[i][j] = k[i]
        out.append(det(A) / D)
    return out
