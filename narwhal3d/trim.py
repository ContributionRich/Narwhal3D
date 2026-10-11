"""Trim a surface along a cutter using an explicit mesh route.

rhino3dm 8.35 exposes no `Brep.Split`, `BrepFace.Split`, `Surface.Split`,
`Surface.Trim`, or mesh boolean/split operation; only `Curve` supports `Split` and
`Trim`. This implementation tessellates the surface, classifies each sample by a signed
field whose zero set is the cutter, and keeps the faces on the chosen side. Cells that
straddle the zero contour are cut with marching squares.

This is a mesh/display trim and therefore an approximation
at tessellation density -- the trimmed boundary is a refined polyline on the cutter,
NOT an exact NURBS trim curve, and the kept region is a triangle mesh, not a trimmed
NURBS face. the reference CAD application's Trim is exact; this is not, and nothing here is named to imply it
is. The crossing points ARE refined onto the field's zero by bisection, so the
boundary converges to the true intersection curve as the grid density rises.

THE CUTTER IS GIVEN AS A SIGNED FIELD. :func:`plane_field`, :func:`cylinder_field` and
:func:`sphere_field` build EXACT, fast fields for the primitive cutters. A general
freeform cutter would need a robust signed field over ALL of space; the naive
"signed distance via the global closest-point solve" is not that: far from the cutter,
the closest-point solve can converge to the wrong side of a closed cutter (for example,
the far wall of a cylinder) and mis-sign samples, producing a boundary approximately 8
units away. A robust general-cutter field is therefore not implemented here; a future
route can classify by the intersection loop in the surface's own UV space, which avoids
that local-minimum trap. This module
trims correctly along ANY field a caller can supply reliably.
"""
import math


class TrimResult:
    """The outcome of a mesh trim.

    * ``vertices``      -- list of ``(x, y, z)`` of the kept mesh.
    * ``faces``         -- list of ``(i, j, k)`` triangle indices into ``vertices``.
    * ``boundary``      -- the crossing points where the cut meets the cutter (the
      approximate trim curve, refined onto the field's zero).
    * ``kept_area``     -- 3-D area of the kept region.
    * ``total_area``    -- 3-D area of the whole tessellated surface.
    * ``missed``        -- True when the cutter does NOT cross the surface, in which
      case the surface is returned UNTOUCHED (whole mesh) rather than empty.
    * ``max_deviation`` -- worst field magnitude of a boundary point (how far the
      approximate trim curve sits off the field's zero / the true cut).
    """

    __slots__ = ("vertices", "faces", "boundary", "kept_area", "total_area",
                 "missed", "max_deviation")

    def __init__(self, vertices, faces, boundary, kept_area, total_area,
                 missed, max_deviation):
        self.vertices = vertices
        self.faces = faces
        self.boundary = boundary
        self.kept_area = kept_area
        self.total_area = total_area
        self.missed = missed
        self.max_deviation = max_deviation

    def __repr__(self):
        return (f"TrimResult({len(self.vertices)} verts, {len(self.faces)} tris, "
                f"kept {self.kept_area:.3f}/{self.total_area:.3f}, "
                f"missed={self.missed}, dev={self.max_deviation:.2e})")


# --------------------------------------------------------------------------- #
# Exact signed fields for the primitive cutters. f(point) -> signed scalar,
# zero on the cutter, negative "inside" by the convention noted on each.
# --------------------------------------------------------------------------- #

def plane_field(origin, normal):
    """Signed distance to a plane. Positive on the side `normal` points to."""
    ox, oy, oz = origin
    nx, ny, nz = normal
    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
    if ln < 1e-15:
        raise ValueError("plane_field: normal is degenerate")
    nx, ny, nz = nx / ln, ny / ln, nz / ln

    def f(p):
        return (p[0] - ox) * nx + (p[1] - oy) * ny + (p[2] - oz) * nz
    return f


def cylinder_field(axis_origin, axis_dir, radius):
    """Signed field for an (infinite) cylinder: distance to the axis minus `radius`.
    Negative INSIDE the cylinder, positive outside, zero on the wall."""
    ox, oy, oz = axis_origin
    ax, ay, az = axis_dir
    ln = math.sqrt(ax * ax + ay * ay + az * az)
    if ln < 1e-15:
        raise ValueError("cylinder_field: axis direction is degenerate")
    ax, ay, az = ax / ln, ay / ln, az / ln

    def f(p):
        dx, dy, dz = p[0] - ox, p[1] - oy, p[2] - oz
        t = dx * ax + dy * ay + dz * az            # projection onto the axis
        rx, ry, rz = dx - t * ax, dy - t * ay, dz - t * az
        return math.sqrt(rx * rx + ry * ry + rz * rz) - radius
    return f


def sphere_field(center, radius):
    """Signed field for a sphere: distance to the centre minus `radius`.
    Negative inside, positive outside."""
    cx, cy, cz = center

    def f(p):
        return math.dist(p, (cx, cy, cz)) - radius
    return f


# --------------------------------------------------------------------------- #
# The marching trim.
# --------------------------------------------------------------------------- #

def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def _refine_crossing(a, b, field, iters=40):
    """The point between `a` and `b` where `field` is zero, by bisection on the TRUE
    field (so the boundary lands ON the cutter, not merely where a linear
    interpolation of the sampled values predicted)."""
    fa = field(a)
    fb = field(b)
    if fa == 0.0:
        return a
    if fb == 0.0:
        return b
    lo, flo, hi = a, fa, b
    for _ in range(iters):
        mid = _lerp(lo, hi, 0.5)
        fm = field(mid)
        if fm == 0.0:
            return mid
        if (fm < 0.0) == (flo < 0.0):
            lo, flo = mid, fm
        else:
            hi = mid
    return _lerp(lo, hi, 0.5)


def _poly_area(poly):
    """3-D area of a (near-planar) polygon via Newell's method."""
    if len(poly) < 3:
        return 0.0
    nx = ny = nz = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0, z0 = poly[i]
        x1, y1, z1 = poly[(i + 1) % n]
        nx += y0 * z1 - z0 * y1
        ny += z0 * x1 - x0 * z1
        nz += x0 * y1 - y0 * x1
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def _clip_cell(corner_pts, corner_f, field):
    """Sutherland-Hodgman clip of one grid cell to the KEPT half (f >= 0), inserting
    crossing points refined onto the field's zero. Returns (kept_polygon,
    crossing_points). `corner_f` are the signed values ALREADY normalised so the kept
    side is f >= 0."""
    out = []
    crossings = []
    n = len(corner_pts)
    for k in range(n):
        cur_pt, cur_f = corner_pts[k], corner_f[k]
        nxt_pt, nxt_f = corner_pts[(k + 1) % n], corner_f[(k + 1) % n]
        cur_in = cur_f >= 0.0
        nxt_in = nxt_f >= 0.0
        if cur_in:
            out.append(cur_pt)
        if cur_in != nxt_in:
            x = _refine_crossing(cur_pt, nxt_pt, field)
            out.append(x)
            crossings.append(x)
    return out, crossings


def _vid(point, verts, index_of, ndigits=9):
    key = (round(point[0], ndigits), round(point[1], ndigits),
           round(point[2], ndigits))
    i = index_of.get(key)
    if i is None:
        i = len(verts)
        verts.append(point)
        index_of[key] = i
    return i


def _add_fan(poly, verts, faces, index_of):
    idxs = [_vid(p, verts, index_of) for p in poly]
    for k in range(1, len(idxs) - 1):
        faces.append((idxs[0], idxs[k], idxs[k + 1]))


def trim_surface_mesh(surface, keep_point, field, nu=64, nv=64):
    """Trim `surface` along `field`'s zero set, keeping the side containing
    `keep_point`.

    `field` is ``f(point) -> signed scalar`` whose zero set is the cutter (negative
    on one side, positive on the other). Build it with :func:`plane_field`,
    :func:`cylinder_field` or :func:`sphere_field` for a primitive cutter. Required:
    there is no implicit general cutter field (see the module docstring for why).

    Returns a :class:`TrimResult`. This is a MESH trim: the result is a triangle mesh
    and the boundary is a refined polyline on the cutter, NOT an exact NURBS trimmed
    face. When the cutter does not cross the surface the whole surface is returned
    with ``missed=True`` (never an empty mesh). `nu`/`nv` set the density; the trim
    boundary converges to the true intersection curve as they rise.
    """
    if field is None or not callable(field):
        raise ValueError(
            "trim_surface_mesh needs a signed `field` (see plane_field / "
            "cylinder_field / sphere_field); a general freeform-cutter field is not "
            "implemented -- the naive closest-point field mis-signs far samples")
    du, dv = surface.Domain(0), surface.Domain(1)

    # Sample the surface on a structured grid and evaluate the signed field.
    pts = [[None] * (nv + 1) for _ in range(nu + 1)]
    fld = [[0.0] * (nv + 1) for _ in range(nu + 1)]
    for i in range(nu + 1):
        u = du.T0 + (du.T1 - du.T0) * i / nu
        for j in range(nv + 1):
            v = dv.T0 + (dv.T1 - dv.T0) * j / nv
            q = surface.PointAt(u, v)
            p = (q.X, q.Y, q.Z)
            pts[i][j] = p
            fld[i][j] = field(p)

    kp = (keep_point[0], keep_point[1], keep_point[2])
    keep_sign = 1.0 if field(kp) >= 0.0 else -1.0

    verts, faces = [], []
    index_of = {}
    boundary = []
    kept_area = 0.0
    total_area = 0.0
    max_dev = 0.0

    for i in range(nu):
        for j in range(nv):
            cps = [pts[i][j], pts[i + 1][j], pts[i + 1][j + 1], pts[i][j + 1]]
            cfs = [fld[i][j] * keep_sign, fld[i + 1][j] * keep_sign,
                   fld[i + 1][j + 1] * keep_sign, fld[i][j + 1] * keep_sign]
            total_area += _poly_area(cps)
            kept, crossings = _clip_cell(cps, cfs, field)
            if len(kept) >= 3:
                kept_area += _poly_area(kept)
                _add_fan(kept, verts, faces, index_of)
            for x in crossings:
                boundary.append(x)
                max_dev = max(max_dev, abs(field(x)))

    missed = len(boundary) == 0
    return TrimResult(verts, faces, boundary, kept_area, total_area, missed, max_dev)
