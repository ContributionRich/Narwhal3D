"""EDGE PICKING: identify which edge of a solid the user clicked.

Edge identification is intentionally separate from kernel-specific bevel construction:

  1. **identify** which edge a world point refers to, and
  2. **build** the bevel on that edge.

Only (2) genuinely needs a kernel. (1) is plain geometry, so it works with any available edge
representation -- an OCCT master's edges, a rhino3dm `Brep`'s edges, or a list of sampled
points. A missing kernel therefore prevents bevel construction without preventing edge
identification. The geometric selection logic also remains independent of `bpy`.

The edges arrive as the serialized dicts `occt_backend.edge_curves` returns:
``{"ctype": "nurbs", "degree", "poles", "weights", "knots", "index"}`` with **euclidean**
poles. Evaluating them needs a rational NURBS evaluation, which `interpolate.py` already has
the pieces for (`find_span`, `basis_values`), so the B-spline maths is shared rather than
written twice -- two copies of a Cox-de Boor would be two things to get subtly wrong.

AMBIGUITY IS REFUSED, NOT GUESSED. If two edges are near-equidistant from the picked point,
`pick` raises instead of choosing. Beveling the wrong edge of a part is a silent, expensive
mistake, and "I could not tell which edge you meant" is a far better answer than a coin flip.
"""
from __future__ import annotations

import math


def _basis_module():
    """Return `interpolate` for package, top-level, or direct-file loading.

    Direct-file loading may satisfy neither `from . import ...` nor a bare `import ...`, so
    the final fallback resolves the sibling module from this file's directory.
    """
    import sys

    try:
        from . import interpolate
        return interpolate
    except ImportError:
        pass
    try:
        import interpolate
        return interpolate
    except ImportError:
        pass
    name = "narwhal3d.interpolate"
    if name in sys.modules:
        return sys.modules[name]
    import importlib.util
    import os

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "interpolate.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class EdgePickError(ValueError):
    """An edge could not be identified, with a message fit to show the user."""


#: How much closer the best candidate must be than the runner-up to count as unambiguous.
#: A pick 2x nearer one edge than any other is treated as clear intent; closer than that on a
#: solid whose edges meet at a corner is genuinely ambiguous, because near a vertex two edges
#: really are almost equidistant.
AMBIGUITY_RATIO = 2.0

#: A pick this close to an edge counts as being ON it, in model units. This is not a licence
#: to skip the ambiguity test. Landing on an edge is only a clear
#: answer when the point is on exactly ONE edge -- at a vertex it is on several, which is the
#: most ambiguous case, not the least. See `pick`.
ON_EDGE_TOLERANCE = 1e-7


def _full_knots(curve):
    """The FULL clamped knot vector for a serialized curve.

    The stored list omits the first and last knot (rhino3dm's convention, which
    `occt_backend.edge_curves` writes), so they are restored by duplicating the ends. Without
    this the span search runs off the start of the vector and every evaluation is wrong near
    the ends.
    """
    knots = [float(k) for k in curve["knots"]]
    n = len(curve["poles"])
    degree = int(curve["degree"])
    if len(knots) == n + degree + 1:
        return knots                      # already full
    if len(knots) == n + degree - 1:
        return [knots[0]] + knots + [knots[-1]]
    raise EdgePickError(
        f"a curve with {n} poles at degree {degree} cannot have {len(knots)} knots")


def evaluate(curve, t, _basis=None):
    """The point on a serialized rational NURBS `curve` at parameter `t`.

    Rational, so the weights are applied in homogeneous space:
    ``P(t) = sum(N_i w_i P_i) / sum(N_i w_i)``. Dividing afterwards rather than before is what
    makes a weighted circle come out round; averaging euclidean poles by weight does not.
    """
    if _basis is None:
        _basis = _basis_module()
    poles = curve["poles"]
    weights = curve.get("weights") or [1.0] * len(poles)
    degree = int(curve["degree"])
    knots = _full_knots(curve)
    lo, hi = knots[degree], knots[len(poles)]
    u = min(max(float(t), lo), hi)
    span = _basis.find_span(len(poles), degree, u, knots)
    vals = _basis.basis_values(span, u, degree, knots)
    x = y = z = w_sum = 0.0
    for j, nj in enumerate(vals):
        i = span - degree + j
        if i < 0 or i >= len(poles):
            continue
        w = float(weights[i]) * nj
        p = poles[i]
        x += w * float(p[0])
        y += w * float(p[1])
        z += w * float(p[2])
        w_sum += w
    if abs(w_sum) < 1e-300:
        raise EdgePickError("a curve evaluated to a zero total weight")
    return (x / w_sum, y / w_sum, z / w_sum)


def sample(curve, n=48):
    """`n + 1` points along a serialized curve, spanning its whole domain."""
    _basis = _basis_module()

    poles = curve["poles"]
    degree = int(curve["degree"])
    knots = _full_knots(curve)
    lo, hi = knots[degree], knots[len(poles)]
    if hi <= lo:
        return [evaluate(curve, lo, _basis)]
    return [evaluate(curve, lo + (hi - lo) * i / n, _basis) for i in range(n + 1)]


def distance_to(curve, point, n=48):
    """Shortest distance from `point` to a serialized curve, by polyline approximation.

    Distance is taken to the SEGMENTS between samples, not merely to the samples. On a
    coarsely sampled long edge, point-to-sample distance can be wrong by half a sample
    spacing, which is enough
    to pick the wrong edge when two run close together.
    """
    pts = sample(curve, n)
    best = float("inf")
    px, py, pz = float(point[0]), float(point[1]), float(point[2])
    for i in range(len(pts) - 1):
        ax, ay, az = pts[i]
        bx, by, bz = pts[i + 1]
        dx, dy, dz = bx - ax, by - ay, bz - az
        length2 = dx * dx + dy * dy + dz * dz
        if length2 <= 0.0:
            t = 0.0
        else:
            t = ((px - ax) * dx + (py - ay) * dy + (pz - az) * dz) / length2
            t = min(max(t, 0.0), 1.0)
        qx, qy, qz = ax + dx * t, ay + dy * t, az + dz * t
        d = math.dist((px, py, pz), (qx, qy, qz))
        if d < best:
            best = d
    if len(pts) == 1:
        best = math.dist((px, py, pz), pts[0])
    return best


def ranked(curves, point, n=48):
    """``[(distance, index)]`` for every curve, nearest first."""
    out = []
    for i, curve in enumerate(curves):
        try:
            d = distance_to(curve, point, n)
        except EdgePickError:
            continue
        out.append((d, int(curve.get("index", i + 1))))
    out.sort()
    return out


def pick(curves, point, n=48, ratio=AMBIGUITY_RATIO):
    """The `index` of the edge nearest `point`. Raises when the pick is AMBIGUOUS.

    Refusing is the whole point: beveling the wrong edge of a part is silent and expensive, so
    "I could not tell which edge you meant" is the better answer. Near a vertex two edges
    genuinely are almost equidistant, which is exactly when a nearest-wins rule would pick
    arbitrarily.
    """
    order = ranked(curves, point, n)
    if not order:
        raise EdgePickError("this object has no edges to pick from")
    best_d, best_i = order[0]
    if len(order) > 1:
        second_d, second_i = order[1]
        # A VERTEX is the case that caught the first version of this out. At a corner the
        # point lies ON two or three edges at once, so `best_d` is 0 and an "it is on an edge,
        # so it must be unambiguous" shortcut waved it straight through -- picking whichever
        # edge the map happened to list first. Being on SEVERAL edges is the most ambiguous
        # case there is, not the least, so the runner-up is tested first.
        if second_d <= ON_EDGE_TOLERANCE:
            raise EdgePickError(
                f"the point lies on {sum(1 for d, _ in order if d <= ON_EDGE_TOLERANCE)} "
                f"edges at once (it is a vertex or a seam) -- edges {best_i} and {second_i} "
                f"both pass through it; pick nearer the middle of the edge you mean")
        if best_d > ON_EDGE_TOLERANCE and second_d < best_d * ratio:
            raise EdgePickError(
                f"the point is ambiguous between edge {best_i} ({best_d:.6g} away) and "
                f"edge {second_i} ({second_d:.6g} away); pick nearer the middle of the "
                f"edge you mean")
    return best_i


def pick_all(curves, points, n=48, ratio=AMBIGUITY_RATIO):
    """Indices for several points, de-duplicated and sorted.

    A point that resolves to an edge already chosen is NOT an error -- two clicks on one edge
    is a harmless thing for a user to do -- but it must not add the edge twice, because
    `BRepFilletAPI` adding one edge twice is not the same as adding it once.
    """
    chosen = []
    for p in points:
        idx = pick(curves, p, n, ratio)
        if idx not in chosen:
            chosen.append(idx)
    return sorted(chosen)
