"""Exact global/periodic interpolation and skinning with Rust-owned computation.

Public helpers retain their tuple/list contracts. Python validates host shapes,
marshals ctypes and constructs rhino3dm objects; it never replays numeric math.
The matching native helper ABI v1 is required even for the smallest input.
"""
from __future__ import annotations

import operator

try:
    from . import native_interpolate as _native
except ImportError:
    # edgepick also loads this sibling by file path outside a Blender package.
    # Resolve only the ctypes adapter, never the application __init__ or a solver.
    import importlib.util
    from pathlib import Path
    import sys
    _name = "_narwhal_interpolation_ctypes"
    _native = sys.modules.get(_name)
    if _native is None:
        _spec = importlib.util.spec_from_file_location(_name, Path(__file__).with_name("native_interpolate.py"))
        _native = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _native
        _spec.loader.exec_module(_native)

KNOT_STYLES = ("Uniform", "Chord", "SqrtChrd")
DEFAULT_KNOT_STYLE = "Chord"
# Compatibility names, not routing thresholds: every supported size is native.
NATIVE_INTERPOLATE_MIN_POINTS = 16
NATIVE_SKIN_MIN_VALUES = 64
NATIVE_PERIODIC_MIN_POINTS = 3


class InterpolationError(ValueError):
    """Interpolation was refused, with a message fit to show the user."""


_SINGULAR = ("the interpolation system is singular; the points are probably "
             "coincident or collinear in a way this parameterisation cannot express")
_CLOSED_SINGULAR = ("the closed interpolation system is singular; the points are probably "
                    "spaced too unevenly for this parameterisation")


def _style(style):
    if style not in KNOT_STYLES:
        raise InterpolationError(
            f"knot style {style!r} not implemented; the reference CAD application's are {', '.join(KNOT_STYLES)}")
    return _native.STYLE[style]


def _points(points):
    pts = [tuple(float(c) for c in p[:3]) for p in points]
    if any(len(p) != 3 for p in pts):
        raise InterpolationError("interpolation needs three coordinates per point")
    return pts


def _raise_native(error, *, style=DEFAULT_KNOT_STYLE, closed=False, count=0):
    if isinstance(error, _native.NativeInterpolationUnavailable):
        # Callers may decline ValueError geometry, but must not silently replace
        # a missing required backend with a different geometric construction.
        raise RuntimeError(str(error)) from error
    if error.status == -5:
        k = error.detail
        if closed:
            message = (f"points {k} and {(k + 1) % count} are coincident, so a closed curve "
                       "through them is undefined; remove the duplicate")
        else:
            message = (f"points {k} and {k + 1} are coincident, so a {style} parameterisation "
                       "is undefined; remove the duplicate or use Uniform")
    elif error.status == -4:
        message = _CLOSED_SINGULAR if closed else _SINGULAR
    elif error.status == -6:
        raise ZeroDivisionError("float division by zero") from error
    elif error.status == -3:
        message = "interpolation exceeds the native size or degree limit (2048)"
    elif error.status == -7:
        raise RuntimeError(
            "native interpolation returned invalid output framing; install the matching native core "
            "(protocol error; no Python fallback)") from error
    else:
        message = "native interpolation refused invalid or non-finite input"
    raise InterpolationError(message) from error


def _helper(operation, data, *, style=DEFAULT_KNOT_STYLE, closed=False, division_message=None, **kwargs):
    try:
        return _native.helper(operation, data, style=kwargs.pop("style_code", 0), **kwargs)
    except (_native.NativeInterpolationFailure, _native.NativeInterpolationUnavailable) as error:
        if isinstance(error, _native.NativeInterpolationFailure) and error.status == -6 and division_message:
            raise ZeroDivisionError(division_message) from error
        _raise_native(error, style=style, closed=closed, count=kwargs.get("rows", 0))


def parameters(points, style=DEFAULT_KNOT_STYLE):
    """Open parameters on [0,1]; coincident chord neighbours are refused by name."""
    st = _style(style)
    n = len(points)
    if n < 2:
        raise InterpolationError(f"interpolation needs at least 2 points (got {n})")
    pts = _points(points)
    return _helper(1, [c for p in pts for c in p], rows=n, capacity=n,
                   style=style, **{"style_code": st})


def averaged_knots(params, degree):
    """Full clamped knot vector, preserving P&T eq. 9.8 arithmetic in Rust."""
    n, p = len(params), int(degree)
    if n < p + 1:
        raise InterpolationError(f"a degree-{p} interpolation needs at least {p + 1} points (got {n})")
    return _helper(2, params, rows=n, degree=p, capacity=n + p + 1)


def find_span(n_cv, degree, u, knots):
    """Index of the knot span containing u (P&T A2.1)."""
    return int(_helper(3, knots, rows=n_cv, degree=degree, parameter=u, capacity=1)[0])


def basis_values(span, u, degree, knots):
    """The degree+1 nonzero Cox-de Boor basis values (P&T A2.2)."""
    return _helper(4, knots, index=span, degree=degree, parameter=u, capacity=degree + 1)


def collocation(params, knots, n_cv, degree):
    """Row-major B-spline collocation matrix, returned as independent Python rows."""
    out = _helper(5, list(params) + list(knots), rows=len(params), columns=n_cv,
                  degree=degree, capacity=len(params) * n_cv)
    return [out[i:i + n_cv] for i in range(0, len(out), n_cv)]


def lu_factor(matrix):
    """Partial-pivoted LU and permutation; input is not modified."""
    n = len(matrix)
    if any(len(row) != n for row in matrix):
        raise InterpolationError("the interpolation matrix must be square")
    out = _helper(6, [float(v) for row in matrix for v in row], rows=n, capacity=n * n + n)
    return [out[i * n:(i + 1) * n] for i in range(n)], [int(v) for v in out[n * n:]]


def lu_solve(lu, perm, rhs):
    """Solve arbitrary-width right-hand sides, retaining list-of-lists output."""
    n = len(lu)
    dim = len(rhs[0]) if rhs else 0
    if n == 0 or len(rhs) != n or len(perm) != n or any(len(row) != n for row in lu) or any(len(row) != dim for row in rhs):
        raise InterpolationError("incompatible LU, permutation or right-hand-side dimensions")
    data = [v for row in lu for v in row] + [operator.index(i) for i in perm] + [v for row in rhs for v in row]
    out = _helper(7, data, rows=n, columns=dim, capacity=n * dim)
    return [out[i * dim:(i + 1) * dim] for i in range(n)]


def closed_parameters(points, style=DEFAULT_KNOT_STYLE):
    """n+1 closed-polygon parameters, with the closing segment included."""
    st = _style(style)
    n = len(points)
    if n < 3:
        raise InterpolationError(f"a closed interpolation needs at least 3 points (got {n})")
    pts = _points(points)
    return _helper(8, [c for p in pts for c in p], rows=n, capacity=n + 1,
                   style=style, closed=True, **{"style_code": st})


def periodic_knots(params, degree=3):
    """Full unclamped vector, including both period extensions (OURS #43)."""
    n, p = len(params), int(degree)
    return _helper(9, params, rows=n, degree=p, capacity=n + 2 * p)


def cyclic_tridiagonal_solve(sub, diag, sup, rhs):
    """O(n) cyclic solve for arbitrary-width RHS, returned as tuples per row."""
    n = len(diag)
    dim = len(rhs[0]) if rhs else 0
    if n < 3 or len(sub) != n or len(sup) != n or len(rhs) != n or any(len(row) != dim for row in rhs):
        raise InterpolationError("incompatible cyclic tridiagonal dimensions (needs at least 3 rows)")
    data = list(sub) + list(diag) + list(sup) + [v for row in rhs for v in row]
    # Rust receives doubles, so only the host can preserve Python's integer-vs-
    # float exception spelling when the original gamma divisor was zero.
    message = "division by zero" if diag[0] == 0 and all(isinstance(v, int) for v in (sub[0], sup[-1], diag[0])) else "float division by zero"
    out = _helper(10, data, rows=n, columns=dim, capacity=n * dim, closed=True, division_message=message)
    return [tuple(out[i * dim:(i + 1) * dim]) for i in range(n)]


def _required_solve(fn, points, *args, style, closed=False):
    try:
        result = fn(points, *args, style, required=True)
        if result is None:
            # A legacy/incompatible adapter may still use the optional sentinel.
            # Refuse here, before builders unpack it or callers try another fit.
            raise RuntimeError(
                "required native interpolation returned no result; install the matching native core "
                "(protocol error; no Python fallback)")
        return result
    except _native.NativeInterpolationFailure as error:
        if error.status == -2 and not fn is _native.skin_control_net:
            # On a native refusal only, ask its parameter helper for the original
            # adjacent-pair diagnostic. This is not a solve or a Python replay.
            (closed_parameters if closed else parameters)(points, style)
        _raise_native(error, style=style, closed=closed, count=len(points))
    except _native.NativeInterpolationUnavailable as error:
        _raise_native(error)


def interpolate_points(points, degree=3, style=DEFAULT_KNOT_STYLE):
    """Controls, full knots and degree of the exact curve through these points."""
    pts = _points(points)
    n, p = len(pts), int(degree)
    if n < 2:
        raise InterpolationError(f"interpolation needs at least 2 points (got {n})")
    if p < 1:
        raise InterpolationError(f"degree must be >= 1 (got {p})")
    if p % 2 == 0:
        raise InterpolationError(
            f"interpolation degree must be ODD (got {p}); the reference CAD application's own "
            "CreateInterpolatedCurve documents 'Degree must be odd' and works best at 3")
    _style(style)
    return _required_solve(_native.interpolate, pts, min(p, 2049), style=style)


def interpolate_points_periodic(points, style=DEFAULT_KNOT_STYLE):
    """Controls, full knots and degree 3 of the closed periodic interpolant."""
    pts = _points(points)
    if len(pts) < 3:
        raise InterpolationError(f"a closed interpolation needs at least 3 points (got {len(pts)})")
    _style(style)
    return _required_solve(_native.interpolate_periodic, pts, style=style, closed=True)


def skin_control_net(sections, degree=3, style=DEFAULT_KNOT_STYLE):
    """Exact tensor-product skin, net[u][v], full V knots and V degree."""
    k = len(sections)
    if k < 2:
        raise InterpolationError(f"skinning needs at least 2 sections (got {k})")
    width = len(sections[0])
    for idx, row in enumerate(sections):
        if len(row) != width:
            raise InterpolationError(
                f"section {idx} has {len(row)} control points but section 0 has {width}; "
                "make the sections compatible before skinning")
    q = int(degree)
    if q == 0:
        # MEASURED legacy edge contract: the old native-sized path clamped zero
        # to one, but the old small/unknown-style path raised division by zero.
        if k * width >= 64 and style in KNOT_STYLES:
            return _required_solve(_native.skin_control_net, sections, 0, style=style)
        averaged_knots([0.0] * k, 0)
    if q < 0:
        raise InterpolationError(f"degree must be >= 1 (got {q})")
    # Legacy skinning ignores unknown styles: every column's parameterisation
    # refuses, so none contributes, giving Uniform. Preserve without Python math.
    native_style = style if style in KNOT_STYLES else "Uniform"
    if width == 0:
        ps = parameters([(0.0, 0.0, 0.0)] * k, "Uniform")
        actual = min(q, k - 1)
        return [], averaged_knots(ps, actual), actual
    result = _required_solve(_native.skin_control_net, sections, min(q, k - 1), style=native_style)
    if result[2] == 1 and k * width < 64:
        # The old small degree-one path transposed host tuples verbatim, including
        # any extra coordinate. Rust still validates/solves; only framing is host.
        return [[tuple(sections[j][i]) for j in range(k)] for i in range(width)], result[1], result[2]
    return result


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
