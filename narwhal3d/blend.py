"""`BlendCrv` -- a blend curve between two curve ends with chosen continuity.

rhino3dm only (no bpy). MEASURED (commands/blendcrv.htm): "creates a blend curve between
curves and / or surface edges with control over the continuity with the input curves";
continuity per end `Position`, `Tangency`, `Curvature`, `G3`, `G4`; `Flip 1 / 2`
"Reverses the direction of the specified curve"; `BlendStart=CurveEnd` "Starts the blend at
the end of the curve where the curve was selected".

THE CONSTRUCTION -- one Bezier, the lowest degree that carries the continuity. Matching k
derivatives at one end and m at the other fixes k + m + 2 control points, so the degree is
k + m + 1: Position (k=0) both ends -> 1 (a line), Tangency (1) -> 3, Curvature (2) -> 5,
G3 -> 7, and G4 -> 9.

THE SOLVE -- the endpoint-derivative control-point solve is NATIVE at every supported
order: `blend_bezier_points` runs the exact Rust solver in the pure-core library
(`native_blend`, the `narwhal_pure_core_blend_bezier_points` export) for all 25
`(k0, k1)` pairs up to G4. The retired pure-Python endpoint solver and the
break-even dispatch threshold are retired (see the native ownership document): a
missing / stale / refused library, or a library that rejects validated inputs, is an
explicit `native_blend.NativeBlendError` -- never a silent Python replay. What stays
Python is the host glue -- the openNURBS `DerivativeAt` extraction, the `NurbsCurve`
construction and the `blend_curves` continuity / flip / scale logic.

The blend B(t), t in [0, 1], takes the input curve's derivatives SCALED by a reparameter-
isation with constant speed factor lambda: B^(j)(0) = lambda^j C^(j). A reparameterisation
with phi'' = phi''' = ... = 0 leaves the tangent direction, the curvature vector and its
higher analogues unchanged, so equal scaled derivatives give G1 / G2 / G3 / G4 exactly --
and C^j continuity of the reparameterised curve, which is stronger. Each end's derivatives
are oriented so the parameter runs OUT of curve 1 into the blend and on INTO curve 2, as a
continuation does.

This implementation chooses lambda so |B'| at each end equals the chord between the two
ends. the reference CAD application's handle lengths are interactive and adjusted in its preview; that interaction
is not implemented here.
"""
from __future__ import annotations

import math

import rhino3dm as r3

CONTINUITIES = ("Position", "Tangency", "Curvature", "G3", "G4")
ORDER = {name: i for i, name in enumerate(CONTINUITIES)}     # derivatives matched


def _xyz(v):
    return (v.X, v.Y, v.Z)


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _mul(a, s):
    return tuple(x * s for x in a)


def _norm(a):
    return math.sqrt(sum(x * x for x in a))


def end_derivatives(curve, at_end, count, outward=True):
    """``[P, D1, ..., D_count]`` at a curve END (`at_end` True) or START, with the parameter
    oriented to run OUT of the curve (`outward`) or INTO it (`outward=False`).

    Running backwards along the curve (s = -t) multiplies the j-th derivative by (-1)^j.
    """
    d = curve.Domain
    t = d.T1 if at_end else d.T0
    raw = curve.DerivativeAt(t, count) if count else [curve.PointAt(t)]
    vals = [_xyz(v) for v in raw]
    # at the END the natural direction already runs out of the curve; at the START it runs in
    flip = (not at_end) if outward else at_end
    return [_mul(v, (-1.0) ** j if flip else 1.0) for j, v in enumerate(vals)]


def blend_bezier_points(p0_derivs, p1_derivs, k0, k1, lam0, lam1):
    """The Bezier control points matching `k0` scaled derivatives at t=0 and `k1` at t=1.

    `p0_derivs` = [P0, D1, ...] (oriented OUT of curve 1), `p1_derivs` = [P1, D1, ...]
    (oriented INTO curve 2). Degree n = k0 + k1 + 1. Solved from the Bezier end-derivative
    identities B^(j)(0) = n!/(n-j)! * Delta^j P_0 (forward differences), and the mirror
    image at t = 1.

    The solve runs NATIVE for every supported order (all 25 (k0, k1) pairs, each end
    0..4 / G4): `native_blend.blend_bezier_points` is the exact Rust implementation of
    that algebra, bit-for-bit the retired pure-Python loop. There is no Python
    endpoint solver left to fall back to: a missing / stale / refused library, or a
    library that rejects validated inputs, raises `native_blend.NativeBlendError`
    (explicit, by name) -- it is never silently replayed through Python.
    """
    try:
        from . import native_blend
    except ImportError:               # direct file-path loading, without a package
        import native_blend

    # the flatten below slices with k+1, so a non-int order must be refused HERE, by
    # name, instead of dying with a bare TypeError -- the native layer is the single
    # validation authority for the solve.
    for k in (k0, k1):
        if not isinstance(k, int) or isinstance(k, bool):
            raise native_blend.NativeBlendError(
                f"unsupported blend order k={k!r}; only integer orders 0..4 (G0..G4) are supported")
    flat0 = [v for d in p0_derivs[:k0 + 1] for v in d]
    flat1 = [v for d in p1_derivs[:k1 + 1] for v in d]
    return native_blend.blend_bezier_points(flat0, flat1, k0, k1, lam0, lam1)


def bezier_curve(points):
    """A clamped NurbsCurve of degree len(points)-1 (a Bezier), non-rational."""
    n = len(points) - 1
    crv = r3.NurbsCurve(3, False, n + 1, n + 1)
    for i, p in enumerate(points):
        crv.Points[i] = r3.Point4d(p[0], p[1], p[2], 1.0)
    for i in range(2 * n):
        crv.Knots[i] = 0.0 if i < n else 1.0
    return crv


def blend_curves(curve1, end1, curve2, end2, cont1="Curvature", cont2="Curvature",
                 flip1=False, flip2=False):
    """The blend from `curve1` at `end1` ("start"/"end") to `curve2` at `end2`.

    Returns ``(blend_curve, info)``; `info` carries the degree and the two scale factors.
    Raises ValueError (by name) for an unknown continuity, a degenerate input end (a zero
    derivative where continuity needs it), or two ends that coincide.
    """
    for c in (cont1, cont2):
        if c not in ORDER:
            raise ValueError(f"unknown continuity {c!r}; use {', '.join(CONTINUITIES)}")
    k0, k1 = ORDER[cont1], ORDER[cont2]
    a = end_derivatives(curve1, end1 == "end", k0, outward=True)
    b = end_derivatives(curve2, end2 == "end", k1, outward=False)
    if flip1:
        a = [v if j % 2 == 0 else _mul(v, -1.0) for j, v in enumerate(a)]
    if flip2:
        b = [v if j % 2 == 0 else _mul(v, -1.0) for j, v in enumerate(b)]
    chord = _norm(_sub(b[0], a[0]))
    if chord <= 1e-12:
        raise ValueError("the two ends are at the same point -- there is nothing to blend")
    lam = []
    for derivs, k, which in ((a, k0, "first"), (b, k1, "second")):
        if k == 0:
            lam.append(1.0)
            continue
        speed = _norm(derivs[1])
        if speed <= 1e-14:
            raise ValueError(f"the {which} curve has no tangent at that end")
        lam.append(chord / speed)
    pts = blend_bezier_points(a, b, k0, k1, lam[0], lam[1])
    return bezier_curve(pts), {"degree": k0 + k1 + 1, "lambda": tuple(lam)}


def nearer_end(curve, point):
    """"start" or "end" -- which end of `curve` is nearer `point` (BlendStart=CurveEnd:
    "Starts the blend at the end of the curve where the curve was selected")."""
    s, e = _xyz(curve.PointAtStart), _xyz(curve.PointAtEnd)
    return "start" if math.dist(s, point) <= math.dist(e, point) else "end"


def curvature_vector(curve, t):
    """The curvature vector at `t` from DERIVATIVES (never samples):
    kappa = (d1 x d2) x d1 / |d1|^4."""
    _p, d1, d2 = (_xyz(v) for v in curve.DerivativeAt(t, 2)[:3])

    def cross(u, v):
        return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])

    s = _norm(d1)
    return _mul(cross(cross(d1, d2), d1), 1.0 / s ** 4)
