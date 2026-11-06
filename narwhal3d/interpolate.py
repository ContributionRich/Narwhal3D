"""Global NURBS INTERPOLATION — the smooth answer to "a curve through these points".

Two geometry defects share one root cause: interpolation must pass through its data.

1. **`InterpCrv` was not interpolating.** MEASURED (commands/curve.htm): *"The InterpCrv
   command draws a curve **through** selected locations in space... Curve matches
   input."* The previous implementation called `bridge.make_polyline(pts, degree=3)`, i.e.
   rhino3dm's
   `CreateControlPointCurve`, which treats the picked points as CONTROL points. The
   result is smooth but it is the WRONG CURVE — it does not pass through what the user
   clicked. (`Curve` is the control-point command and was already right: *"draws a curve
   from control point locations. Curve control points match input."*)

2. **Swept surfaces were faceted along the sweep.** `sweep1` sampled the rail at N
   stations and lofted them with `style="Straight"`, i.e. degree 1 in V. MEASURED
   along the sweep: a circle swept along a quarter-circle rail of radius 10 came back
   **degree 2x1 — 18 flat strips** whose mid-strip points miss the true torus by 0.0114
   units. A curved surface must not be represented as flat strips. A straight-rail check
   cannot expose this defect because degree 1 is exact on a straight rail.

Both are "find the curve/surface that PASSES THROUGH this data", so both are solved by
global interpolation. rhino3dm has NO interpolation primitive — MEASURED, the whole
static API is `NurbsCurve.Create{,ControlPointCurve,FromArc,FromCircle,FromEllipse,
FromLine}` — so the algorithm is implemented here.

THE ALGORITHM is the standard one (Piegl & Tiller, *The NURBS Book*, §9.2.1 / A9.1):
pick a parameter for each data point, average them into a knot vector (eq. 9.8), then
solve ``N P = Q`` for the control points, where ``N[k][i] = N_{i,p}(u_k)``. The result
passes through every data point to machine precision — which is what the tests assert,
rather than merely checking that something smooth came back.

KNOT STYLES follow CAD SDK's `the reference CAD application.Geometry.CurveKnotStyle` API:

    Uniform          "Parameter spacing between consecutive knots is 1.0."
    Chord            "Chord length spacing, requires degree=3 ..."
    ChordSquareRoot  "Square root of chord length, requires degree=3 ..."

and the command-line names in commands/curve.htm are `Uniform`, `Chord`, `SqrtChrd`
(note the spelling — not "SqrtChord").

The math here is deliberately free of `bpy` and of `rhino3dm`: `interpolate_points` and
`skin_control_net` take and return plain tuples, so they are testable on their own. Only
the thin builders at the bottom touch `rhino3dm`.
"""
from __future__ import annotations

import math

#: the reference CAD application's knot styles, using the names accepted by its command line and mapped to the
#: corresponding CAD SDK enumeration values.
KNOT_STYLES = ("Uniform", "Chord", "SqrtChrd")

#: Neither the reference CAD application's command documentation nor CAD SDK states which style `InterpCrv`
#: starts with, so this is an implementation choice. `Chord` is chosen because the help
#: says it "makes curves that have widely
#: varying point spacing behave better than uniform curves", which is the common case for
#: hand-picked points.
DEFAULT_KNOT_STYLE = "Chord"
NATIVE_INTERPOLATE_MIN_POINTS = 16
NATIVE_SKIN_MIN_VALUES = 64


def _native_skinning(sections, degree, style):
    try:
        from . import native_interpolate
        return native_interpolate.skin_control_net(sections, degree, style)
    except Exception:  # noqa: BLE001 -- complete Python solver remains fallback
        return None


def _native_interpolation(points, degree, style):
    try:
        from . import native_interpolate
        return native_interpolate.interpolate(points, degree, style)
    except Exception:  # noqa: BLE001 -- complete Python solver remains fallback
        return None


class InterpolationError(ValueError):
    """Interpolation was refused, with a message fit to show the user."""


# ---------------------------------------------------------------------------
# parameters and knots
# ---------------------------------------------------------------------------

def parameters(points, style=DEFAULT_KNOT_STYLE):
    """The parameter assigned to each data point, normalised to ``[0, 1]``.

    * ``Uniform``  — equal spacing. MEASURED: *"The knot spacing is always 1 and is not
      based on the physical spacing of the points."* (Normalising to [0,1] afterwards is
      a reparametrisation, which does not change the SHAPE of the curve.)
    * ``Chord``    — spacing proportional to the distance between points.
    * ``SqrtChrd`` — proportional to the square root of that distance.

    Coincident consecutive points would give a zero-length span and a singular system, so
    they are refused BY NAME rather than silently nudged.
    """
    if style not in KNOT_STYLES:
        raise InterpolationError(
            f"knot style {style!r} not implemented; the reference CAD application's are {', '.join(KNOT_STYLES)}")
    n = len(points)
    if n < 2:
        raise InterpolationError(f"interpolation needs at least 2 points (got {n})")
    if style == "Uniform":
        return [k / float(n - 1) for k in range(n)]

    deltas = []
    for k in range(1, n):
        a, b = points[k - 1], points[k]
        d = math.sqrt(sum((b[i] - a[i]) ** 2 for i in range(3)))
        if d <= 0.0:
            raise InterpolationError(
                f"points {k - 1} and {k} are coincident, so a {style} parameterisation "
                f"is undefined; remove the duplicate or use Uniform")
        deltas.append(math.sqrt(d) if style == "SqrtChrd" else d)
    total = sum(deltas)
    out = [0.0]
    acc = 0.0
    for d in deltas:
        acc += d
        out.append(acc / total)
    out[-1] = 1.0
    return out


def averaged_knots(params, degree):
    """The clamped knot vector for interpolation at `params` (P&T eq. 9.8).

    Full length ``len(params) + degree + 1``: ``degree + 1`` zeros, then each interior
    knot the average of `degree` consecutive parameters, then ``degree + 1`` ones. The
    averaging is what makes the collocation matrix well conditioned and totally positive,
    so the solve below needs no pivoting heroics.
    """
    n = len(params)
    p = int(degree)
    if n < p + 1:
        raise InterpolationError(
            f"a degree-{p} interpolation needs at least {p + 1} points (got {n})")
    knots = [0.0] * (p + 1)
    for j in range(1, n - p):
        knots.append(sum(params[j:j + p]) / float(p))
    knots.extend([1.0] * (p + 1))
    return knots


# ---------------------------------------------------------------------------
# B-spline basis (Cox–de Boor), P&T A2.1
# ---------------------------------------------------------------------------

def find_span(n_cv, degree, u, knots):
    """Index of the knot span containing `u` (P&T A2.1 FindSpan)."""
    n = n_cv - 1
    if u >= knots[n + 1]:
        return n
    if u <= knots[degree]:
        return degree
    low, high = degree, n + 1
    mid = (low + high) // 2
    while u < knots[mid] or u >= knots[mid + 1]:
        if u < knots[mid]:
            high = mid
        else:
            low = mid
        mid = (low + high) // 2
    return mid


def basis_values(span, u, degree, knots):
    """The `degree + 1` non-zero basis functions at `u` (P&T A2.2 BasisFuns)."""
    N = [0.0] * (degree + 1)
    left = [0.0] * (degree + 1)
    right = [0.0] * (degree + 1)
    N[0] = 1.0
    for j in range(1, degree + 1):
        left[j] = u - knots[span + 1 - j]
        right[j] = knots[span + j] - u
        saved = 0.0
        for r in range(j):
            denom = right[r + 1] + left[j - r]
            temp = N[r] / denom if denom != 0.0 else 0.0
            N[r] = saved + right[r + 1] * temp
            saved = left[j - r] * temp
        N[j] = saved
    return N


# ---------------------------------------------------------------------------
# dense LU, reused across columns (that is what makes skinning affordable)
# ---------------------------------------------------------------------------

def lu_factor(matrix):
    """LU-decompose a square matrix in place-ish, returning ``(lu, perm)``.

    Partial pivoting. Returned so that ONE factorisation can be reused for many
    right-hand sides: skinning a surface solves the same system once per control-point
    column, and refactoring each time would turn an O(n^3) job into O(n^4).
    """
    n = len(matrix)
    lu = [list(map(float, row)) for row in matrix]
    perm = list(range(n))
    for k in range(n):
        pivot = max(range(k, n), key=lambda i: abs(lu[i][k]))
        if abs(lu[pivot][k]) < 1e-14:
            raise InterpolationError(
                "the interpolation system is singular; the points are probably "
                "coincident or collinear in a way this parameterisation cannot express")
        if pivot != k:
            lu[k], lu[pivot] = lu[pivot], lu[k]
            perm[k], perm[pivot] = perm[pivot], perm[k]
        inv = 1.0 / lu[k][k]
        for i in range(k + 1, n):
            f = lu[i][k] * inv
            lu[i][k] = f
            if f != 0.0:
                row_i, row_k = lu[i], lu[k]
                for j in range(k + 1, n):
                    row_i[j] -= f * row_k[j]
    return lu, perm


def lu_solve(lu, perm, rhs):
    """Solve for one right-hand side (a list of equal-length vectors)."""
    n = len(lu)
    dim = len(rhs[0])
    y = [list(rhs[perm[i]]) for i in range(n)]
    for i in range(1, n):
        for k in range(i):
            f = lu[i][k]
            if f != 0.0:
                for d in range(dim):
                    y[i][d] -= f * y[k][d]
    for i in range(n - 1, -1, -1):
        for k in range(i + 1, n):
            f = lu[i][k]
            if f != 0.0:
                for d in range(dim):
                    y[i][d] -= f * y[k][d]
        inv = 1.0 / lu[i][i]
        for d in range(dim):
            y[i][d] *= inv
    return y


# ---------------------------------------------------------------------------
# the interpolation itself
# ---------------------------------------------------------------------------

def collocation(params, knots, n_cv, degree):
    """The matrix ``N[k][i] = N_{i,p}(params[k])``."""
    rows = []
    for u in params:
        span = find_span(n_cv, degree, u, knots)
        vals = basis_values(span, u, degree, knots)
        row = [0.0] * n_cv
        for j, v in enumerate(vals):
            row[span - degree + j] = v
        rows.append(row)
    return rows


def interpolate_points(points, degree=3, style=DEFAULT_KNOT_STYLE):
    """``(control_points, knots, degree)`` of the curve PASSING THROUGH `points`.

    Pure: takes and returns plain ``(x, y, z)`` tuples, so it is testable without
    rhino3dm. `knots` is the FULL clamped vector (length ``n + degree + 1``); the
    rhino3dm builder drops the first and last, which is that API's convention.

    Degree is clamped DOWN to what the data can support (a degree-3 curve needs 4 points),
    exactly as `Curve` does, rather than refusing a 2- or 3-point interpolation.

    CAD SDK's `Curve.CreateInterpolatedCurve` documentation states that the routine
    works best at degree 3, requires degree >= 1, and requires an odd requested degree. An even degree is
    therefore refused BY NAME instead of being quietly rounded, because a caller asking
    for degree 2 has a misconception worth surfacing.
    """
    pts = [tuple(float(c) for c in p[:3]) for p in points]
    n = len(pts)
    if n < 2:
        raise InterpolationError(f"interpolation needs at least 2 points (got {n})")
    p = int(degree)
    if p < 1:
        raise InterpolationError(f"degree must be >= 1 (got {p})")
    if p % 2 == 0:
        raise InterpolationError(
            f"interpolation degree must be ODD (got {p}); the reference CAD application's own "
            f"CreateInterpolatedCurve documents 'Degree must be odd' and works best at 3")
    # clamp DOWN to what the data supports. The clamp deliberately does NOT force an odd
    # degree: the odd rule above is about the degree a CALLER asks for (CAD SDK's
    # constraint on CreateInterpolatedCurve), and forcing it here would drop 3 points to
    # degree 1 -- a POLYLINE through three points, which is precisely the "curves made of
    # line segments. Three points require the degree-2 curve to remain smooth.
    if p > n - 1:
        p = max(1, n - 1)
    if n >= NATIVE_INTERPOLATE_MIN_POINTS:
        native = _native_interpolation(pts, degree, style)
        if native is not None:
            return native
    if p == 1:
        # a degree-1 interpolation IS the polyline through the points, and that is the
        # correct, exact answer -- not a fallback. Two points cannot describe a curve.
        knots = [0.0] + list(parameters(pts, style)) + [1.0]
        return pts, knots, 1
    params = parameters(pts, style)
    knots = averaged_knots(params, p)
    matrix = collocation(params, knots, n, p)
    lu, perm = lu_factor(matrix)
    ctrl = lu_solve(lu, perm, pts)
    return [tuple(c) for c in ctrl], knots, p


# ---------------------------------------------------------------------------
# closed (periodic) interpolation
# ---------------------------------------------------------------------------

#: MEASURED (i7-9700K, 2026-11-05): Rust's periodic solve beat this module's at every
#: size tried, 16 vs 28 us at 4 points up to 1.9 vs 12.0 ms at 2048, so it is routed
#: from the smallest closed input.
NATIVE_PERIODIC_MIN_POINTS = 3


def _native_periodic(points, style):
    try:
        from . import native_interpolate
        return native_interpolate.interpolate_periodic(points, style)
    except Exception:  # noqa: BLE001 -- complete Python solver remains fallback
        return None


def closed_parameters(points, style=DEFAULT_KNOT_STYLE):
    """`n + 1` parameters on ``[0, 1]`` around the CLOSED polygon of `points`.

    The same rule as :func:`parameters`, applied to the closed polygon: the closing
    segment from the last point back to the first counts like any other, so the last
    entry (1.0) is the first point again. Coincident neighbours, including a last point
    equal to the first, are refused by name.
    """
    if style not in KNOT_STYLES:
        raise InterpolationError(
            f"knot style {style!r} not implemented; the reference CAD application's are {', '.join(KNOT_STYLES)}")
    n = len(points)
    if n < 3:
        raise InterpolationError(f"a closed interpolation needs at least 3 points (got {n})")
    deltas = []
    for k in range(n):
        a, b = points[k], points[(k + 1) % n]
        d = math.sqrt(sum((b[i] - a[i]) ** 2 for i in range(3)))
        if d <= 0.0:
            raise InterpolationError(
                f"points {k} and {(k + 1) % n} are coincident, so a closed curve "
                f"through them is undefined; remove the duplicate")
        deltas.append(1.0 if style == "Uniform" else math.sqrt(d) if style == "SqrtChrd" else d)
    total = sum(deltas)
    out = [0.0]
    acc = 0.0
    for d in deltas:
        acc += d
        out.append(acc / total)
    out[-1] = 1.0
    return out


def periodic_knots(params, degree=3):
    """The full unclamped knot vector of the periodic interpolant (length ``n + 2p + 1``).

    OURS (#43): for odd degree the knots sit AT the data parameters, extended by one
    period on each side. Averaging (eq. 9.8) exists to place the clamped end conditions;
    a periodic curve has none, and knots at the parameters give the classic periodic
    spline interpolant, C2 everywhere including the seam.
    """
    n = len(params) - 1
    p = int(degree)
    return [params[k % n] + (k // n) for k in range(-p, n + p + 1)]


def cyclic_tridiagonal_solve(sub, diag, sup, rhs):
    """Solve ``sub[i] x[i-1] + diag[i] x[i] + sup[i] x[i+1] = rhs[i]`` with wrap-around.

    O(n) Sherman–Morrison on the Thomas algorithm (Numerical Recipes `cyclic`): the
    corner entries are ``sub[0]`` (row 0, column n-1) and ``sup[n-1]`` (row n-1,
    column 0). `rhs` rows are equal-length vectors. Needs ``n >= 3``.
    """
    n = len(diag)
    dim = len(rhs[0])
    alpha, beta = sup[n - 1], sub[0]
    gamma = -diag[0]
    bb = list(diag)
    bb[0] = diag[0] - gamma
    bb[n - 1] = diag[n - 1] - alpha * beta / gamma

    def thomas(r):
        cp = [0.0] * n
        dp = [[0.0] * len(r[0]) for _ in range(n)]
        denom = bb[0]
        for i in range(n):
            if i:
                denom = bb[i] - sub[i] * cp[i - 1]
            if abs(denom) < 1e-14:
                raise InterpolationError(
                    "the closed interpolation system is singular; the points are probably "
                    "spaced too unevenly for this parameterisation")
            cp[i] = sup[i] / denom
            prev = dp[i - 1] if i else None
            dp[i] = [(r[i][d] - (sub[i] * prev[d] if i else 0.0)) / denom
                     for d in range(len(r[0]))]
        for i in range(n - 2, -1, -1):
            dp[i] = [dp[i][d] - cp[i] * dp[i + 1][d] for d in range(len(r[0]))]
        return dp

    x = thomas(rhs)
    u = [[0.0] for _ in range(n)]
    u[0][0], u[n - 1][0] = gamma, alpha
    z = [row[0] for row in thomas(u)]
    denom = 1.0 + z[0] + beta * z[n - 1] / gamma
    out = []
    for d in range(dim):
        fact = (x[0][d] + beta * x[n - 1][d] / gamma) / denom
        out.append([x[i][d] - fact * z[i] for i in range(n)])
    return [tuple(out[d][i] for d in range(dim)) for i in range(n)]


def interpolate_points_periodic(points, style=DEFAULT_KNOT_STYLE):
    """``(control_points, knots, 3)`` of the CLOSED cubic passing through `points`.

    `points` are the distinct picks (do not repeat the first). The result is periodic:
    ``n + 3`` control points whose last three repeat the first three, and the full
    unclamped knot vector of :func:`periodic_knots` (length ``n + 7``), so position,
    first and second derivative match at the seam. At each data parameter only three
    cubic basis functions are non-zero, so the system is cyclic tridiagonal and is
    solved exactly in O(n).
    """
    pts = [tuple(float(c) for c in p[:3]) for p in points]
    n = len(pts)
    if n >= NATIVE_PERIODIC_MIN_POINTS:
        # Rust validates the same input; a refusal falls through to the named error below.
        native = _native_periodic(pts, style)
        if native is not None:
            return native
    params = closed_parameters(pts, style)
    knots = periodic_knots(params, 3)
    sub, diag, sup = [], [], []
    for k in range(n):
        vals = basis_values(k + 3, params[k], 3, knots)
        sub.append(vals[0])
        diag.append(vals[1])
        sup.append(vals[2])
    # Row k reads control points k, k+1, k+2; solving for R_k = P_{k+1} makes it
    # tridiagonal around the diagonal.
    shifted = cyclic_tridiagonal_solve(sub, diag, sup, pts)
    unique = [shifted[(i - 1) % n] for i in range(n)]
    return unique + unique[:3], knots, 3


def skin_control_net(sections, degree=3, style=DEFAULT_KNOT_STYLE):
    """``(net, v_knots, v_degree)`` of a surface PASSING THROUGH every section.

    `sections` is a list of equal-length control-point lists — one per section curve, all
    already made compatible (same U degree, knots and control-point count). The returned
    `net[i][j]` is the control point at U index `i`, V index `j`.

    This is tensor-product skinning (P&T §10.3): interpolate ACROSS the sections, once per
    U control-point column, reusing a single LU factorisation. Because interpolation is
    exact, the surface contains each input section exactly — which is what makes a sweep
    pass through its stations instead of chording between them.

    The V parameterisation is computed from the AVERAGE of each column's own spacing, so
    one wild column cannot dominate the whole surface's parameterisation.
    """
    k = len(sections)
    if k < 2:
        raise InterpolationError(f"skinning needs at least 2 sections (got {k})")
    width = len(sections[0])
    for idx, s in enumerate(sections):
        if len(s) != width:
            raise InterpolationError(
                f"section {idx} has {len(s)} control points but section 0 has {width}; "
                f"make the sections compatible before skinning")
    q = int(degree)
    # As in `interpolate_points`, the clamp does not force an odd degree -- three sections
    # must skin to a smooth degree-2 surface, not to a degree-1 fan of flat strips. The
    # odd-degree rule is the reference CAD application's constraint on an interpolated CURVE's requested degree,
    # not a property of tensor-product skinning.
    if q > k - 1:
        q = max(1, k - 1)
    if k * width >= NATIVE_SKIN_MIN_VALUES:
        native = _native_skinning(sections, degree, style)
        if native is not None:
            return native

    # averaged V parameters across all columns (P&T eq. 10.8's spirit)
    if style == "Uniform" or q == 1:
        vparams = [j / float(k - 1) for j in range(k)]
    else:
        acc = [0.0] * k
        used = 0
        for i in range(width):
            column = [s[i] for s in sections]
            try:
                ps = parameters(column, style)
            except InterpolationError:
                continue          # a degenerate column (a pole) contributes nothing
            used += 1
            for j in range(k):
                acc[j] += ps[j]
        if used == 0:
            vparams = [j / float(k - 1) for j in range(k)]
        else:
            vparams = [a / used for a in acc]
            vparams[0], vparams[-1] = 0.0, 1.0

    if q == 1:
        vknots = [0.0] + list(vparams) + [1.0]
        net = [[tuple(sections[j][i]) for j in range(k)] for i in range(width)]
        return net, vknots, 1

    vknots = averaged_knots(vparams, q)
    matrix = collocation(vparams, vknots, k, q)
    lu, perm = lu_factor(matrix)
    net = []
    for i in range(width):
        column = [tuple(float(c) for c in sections[j][i][:3]) for j in range(k)]
        net.append([tuple(c) for c in lu_solve(lu, perm, column)])
    return net, vknots, q


# ---------------------------------------------------------------------------
# thin rhino3dm builders
# ---------------------------------------------------------------------------

def _r3():
    import rhino3dm
    return rhino3dm


def interpolate_curve(points, degree=3, style=DEFAULT_KNOT_STYLE):
    """A `NurbsCurve` passing THROUGH `points` — what `InterpCrv` must build.

    rhino3dm's knot array omits the first and last of the full clamped vector (its
    length is ``cv_count + order - 2``), so the full vector from
    :func:`interpolate_points` is trimmed on the way in.
    """
    r3 = _r3()
    ctrl, knots, p = interpolate_points(points, degree=degree, style=style)
    curve = r3.NurbsCurve(3, False, p + 1, len(ctrl))
    for i, c in enumerate(ctrl):
        curve.Points[i] = r3.Point4d(c[0], c[1], c[2], 1.0)
    interior = knots[1:-1]
    if len(interior) != len(curve.Knots):
        raise InterpolationError(
            f"knot count mismatch: built {len(interior)}, rhino3dm wants "
            f"{len(curve.Knots)} for {len(ctrl)} control points at degree {p}")
    for i, kv in enumerate(interior):
        curve.Knots[i] = float(kv)
    if not curve.IsValid:
        raise InterpolationError("the interpolated curve came back invalid")
    return curve


def interpolate_closed_curve(points, style=DEFAULT_KNOT_STYLE):
    """A closed periodic cubic `NurbsCurve` passing THROUGH every one of `points`."""
    r3 = _r3()
    ctrl, knots, p = interpolate_points_periodic(points, style=style)
    curve = r3.NurbsCurve(3, False, p + 1, len(ctrl))
    for i, c in enumerate(ctrl):
        curve.Points[i] = r3.Point4d(c[0], c[1], c[2], 1.0)
    interior = knots[1:-1]
    if len(interior) != len(curve.Knots):
        raise InterpolationError(
            f"knot count mismatch: built {len(interior)}, rhino3dm wants "
            f"{len(curve.Knots)} for {len(ctrl)} control points at degree {p}")
    for i, kv in enumerate(interior):
        curve.Knots[i] = float(kv)
    if not curve.IsValid or not curve.IsClosed:
        raise InterpolationError("the closed interpolated curve came back invalid")
    return curve
