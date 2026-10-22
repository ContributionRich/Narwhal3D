"""Numerical analysis of rhino3dm geometry.

rhino3dm 8.35.0 has no AreaMassProperties / VolumeMassProperties classes, so
these are computed numerically:

- curve length by dense sampling (exact for lines/circles at high n)
- closed planar curve area by the shoelace formula in its plane
- surface area and solid volume from a triangulated grid of each face's domain

The grid approach is deliberately pure Python over rhino3dm points (no bmesh),
so it runs headless and in unit tests. Accuracy: exact for flat faces, ~0.2%
for smooth surfaces at the default 64x64 resolution — commands report results
as approximate where that matters.
"""
from __future__ import annotations

import math

try:                                  # normal import, inside the addon package
    from . import arclength
except ImportError:                   # supports loading this module directly, with
    import os as _os                  # no package context and
    import sys as _sys                # no narwhal3d on sys.path)
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import arclength


# ---------------------------------------------------------------------------
# curves
# ---------------------------------------------------------------------------

def curve_length(curve, n: int = 1024) -> float:
    """Arc length of a curve, exact to tolerance.

    Was a chord-sampled polyline estimate that UNDER-estimated a curved input
    (a radius-5 circle came out 31.415876928 vs the true 31.415926536 -- short by
    4.96e-5). Now delegates to `arclength.length_of` (Gauss-Legendre per knot
    span). `n` is kept for signature compatibility but is unused -- the exact
    integrator needs no sample count. Falls back to the old dense sampling only if
    the exact route raises (a degenerate curve).
    """
    try:
        return arclength.length_of(curve)
    except Exception:  # noqa: BLE001
        d = curve.Domain
        t0, t1 = d.T0, d.T1
        prev = curve.PointAt(t0)
        total = 0.0
        for i in range(1, n + 1):
            p = curve.PointAt(t0 + (t1 - t0) * i / n)
            total += math.dist((prev.X, prev.Y, prev.Z), (p.X, p.Y, p.Z))
            prev = p
        return total


def closed_curve_area(curve, n: int = 512) -> float | None:
    """Area enclosed by a closed planar curve; None if not closed or non-planar."""
    if not curve.IsClosed:
        return None
    d = curve.Domain
    t0, t1 = d.T0, d.T1
    pts = [curve.PointAt(t0 + (t1 - t0) * i / n) for i in range(n)]

    # find the plane's normal from three well-separated samples, then project
    # onto the two axes perpendicular to its dominant component.
    def cross(a, b):
        ax, ay, az = a[0], a[1], a[2]
        bx, by, bz = b[0], b[1], b[2]
        return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)

    m = len(pts)
    v0, v1, v2 = pts[0], pts[m // 3], pts[(2 * m) // 3]
    u = (v1.X - v0.X, v1.Y - v0.Y, v1.Z - v0.Z)
    w = (v2.X - v0.X, v2.Y - v0.Y, v2.Z - v0.Z)
    normal = cross(u, w)
    if max(abs(c) for c in normal) < 1e-9:
        return None  # degenerate (a point or line)

    dominant = max(range(3), key=lambda k: abs(normal[k]))
    keep = [k for k in range(3) if k != dominant]

    def proj(p):
        coords = (p.X, p.Y, p.Z)
        return coords[keep[0]], coords[keep[1]]

    # projecting along the dominant normal axis scales area by |n̂·axis|;
    # undo that to recover the true planar area (exact for any plane tilt)
    nlen = math.sqrt(sum(c * c for c in normal))
    scale = abs(normal[dominant]) / nlen

    poly = [proj(p) for p in pts]
    area = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0 / scale


# ---------------------------------------------------------------------------
# surfaces and solids (grid triangulation)
# ---------------------------------------------------------------------------

def _face_grid(geom, nu: int, nv: int):
    """Yield triangles (a, b, c) covering every face of a brep/surface."""
    faces = geom.Faces if hasattr(geom, "Faces") else [geom]
    for face in faces:
        du, dv = face.Domain(0), face.Domain(1)
        grid = [[face.PointAt(du.T0 + (du.T1 - du.T0) * i / nu,
                              dv.T0 + (dv.T1 - dv.T0) * j / nv)
                 for j in range(nv + 1)] for i in range(nu + 1)]
        for i in range(nu):
            for j in range(nv):
                a, b = grid[i][j], grid[i + 1][j]
                c, dpt = grid[i + 1][j + 1], grid[i][j + 1]
                yield (a, b, c)
                yield (a, c, dpt)


def _tri_area(a, b, c) -> float:
    ux, uy, uz = b.X - a.X, b.Y - a.Y, b.Z - a.Z
    vx, vy, vz = c.X - a.X, c.Y - a.Y, c.Z - a.Z
    cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    return math.sqrt(cx * cx + cy * cy + cz * cz) / 2.0


def analysis_density(geom, default: int = 64) -> int:
    """Pick the grid sampling density for area/volume.

    A brep whose every face is DEGREE <= 1 in both directions is exact at nu=1: a
    bilinear patch is fully determined by its four corners, so one grid cell
    reproduces it (planar or not). That is true of a mesh-derived boolean result
    (CreateFromMesh makes degree-1 triangle/quad faces) AND of an analytic box, so
    nu=1 is both exact and fast. A CURVED face (sphere degree 2, a cylinder wall
    degree 2) is NOT reproduced by its corners and needs the full grid.

    This replaced a `len(Faces) > 64` heuristic: adaptive boolean meshing now
    turns a 19k-face union into ~40 degree-1 faces, so face count no longer tells a
    mesh-derived brep from a primitive -- surface DEGREE does, and exactly (a box was
    being sampled 64x64 for an answer that is exact at 1x1). Degree beats planarity:
    a mesh-derived union can carry one slightly non-planar bilinear quad, which is
    still exact at nu=1. The count check is kept as a cheap fast-path for huge meshes.
    """
    try:
        faces = geom.Faces
        n = len(faces)
        if n > 64:
            return 1
        if n > 0 and all(f.Degree(0) <= 1 and f.Degree(1) <= 1 for f in faces):
            return 1
    except Exception:  # noqa: BLE001
        pass
    return default


def surface_area(geom, nu: int = 64, nv: int = 64) -> float:
    """Total area of a brep or surface via grid triangulation."""
    return sum(_tri_area(*t) for t in _face_grid(geom, nu, nv))


def solid_volume(geom, nu: int = 64, nv: int = 64) -> float:
    """Volume enclosed by a closed brep (divergence theorem over the mesh)."""
    vol = 0.0
    for a, b, c in _face_grid(geom, nu, nv):
        vol += (a.X * (b.Y * c.Z - c.Y * b.Z)
                - a.Y * (b.X * c.Z - c.X * b.Z)
                + a.Z * (b.X * c.Y - c.X * b.Y)) / 6.0
    return abs(vol)


# ---------------------------------------------------------------------------
# bounding boxes
# ---------------------------------------------------------------------------

def bounding_box(geom):
    """(min_pt, max_pt) as Point3d, tight on the actual geometry.

    * a Brep with an analytic master or recipe -> the OCCT shape's tight box. The display
      proxy's box sits INSIDE the true one by up to the chord tolerance (MEASURED: a
      cylinder proxy read y 2.0084..7.9916 for a true 2..8);
    * a curve -> :func:`curve_bounding_box`. rhino3dm's `GetBoundingBox` on a NURBS curve
      is the CONTROL-POINT hull, not the curve (MEASURED: y -4..6 for a true
      -0.975..1.970), and `GetTightBoundingBox` is the same hull;
    * anything else -> `GetBoundingBox`, which is exact for it (a point, a mesh-derived
      brep whose facets ARE the geometry).
    """
    import rhino3dm as r3

    if isinstance(geom, r3.Curve):
        lo, hi = curve_bounding_box(geom)
        return r3.Point3d(*lo), r3.Point3d(*hi)
    if isinstance(geom, r3.Brep):
        shape = occt_shape_of(geom)
        if shape is not None:
            try:
                lo, hi = _kernel_mods()[1].tight_bbox(shape)
                return r3.Point3d(*lo), r3.Point3d(*hi)
            except Exception:  # noqa: BLE001
                pass
    bb = geom.GetBoundingBox()
    return bb.Min, bb.Max


def curve_bounding_box(curve, samples_per_span: int = 32):
    """``((xmin, ymin, zmin), (xmax, ymax, zmax))`` of `curve` ITSELF, to machine precision.

    A coordinate's extremum is at a domain end, a knot (where a C0 kink can put a corner
    extremum), or a zero of that coordinate's derivative inside a span. Each span is
    sampled for sign changes of dC/dt and every bracket is bisected to convergence, so the
    answer is the curve's true box, not its control hull.
    """
    nc = curve if hasattr(curve, "Knots") else curve.ToNurbsCurve()
    d = nc.Domain
    t0, t1 = d.T0, d.T1
    breaks = sorted({t0, t1} | {k for k in nc.Knots.ToList() if t0 < k < t1})
    cands = list(breaks)

    def deriv(t, axis):
        v = nc.DerivativeAt(t, 1)[1]
        return (v.X, v.Y, v.Z)[axis]

    for a, b in zip(breaks, breaks[1:]):
        ts = [a + (b - a) * i / samples_per_span for i in range(samples_per_span + 1)]
        for axis in range(3):
            ds = [deriv(t, axis) for t in ts]
            for (ta, da), (tb, db) in zip(zip(ts, ds), zip(ts[1:], ds[1:])):
                if da == 0.0:
                    cands.append(ta)
                elif (da < 0.0) != (db < 0.0):
                    lo, hi, flo = ta, tb, da
                    for _ in range(80):
                        mid = 0.5 * (lo + hi)
                        fm = deriv(mid, axis)
                        if (fm < 0.0) == (flo < 0.0):
                            lo, flo = mid, fm
                        else:
                            hi = mid
                    cands.append(0.5 * (lo + hi))
    pts = [nc.PointAt(t) for t in cands]
    xs = [p.X for p in pts]
    ys = [p.Y for p in pts]
    zs = [p.Z for p in pts]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


# ---------------------------------------------------------------------------
# Measure the analytic master, not the display proxy
# ---------------------------------------------------------------------------

def _kernel_mods():
    """``(kernel, occt_backend)``. Imported lazily: analysis stays importable with no
    kernel at all, and `occt_backend` only touches OCP inside its functions."""
    try:
        from . import kernel
        from .kernel import occt_backend
    except ImportError:               # loaded raw, by file path
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import kernel
        from kernel import occt_backend
    return kernel, occt_backend


def occt_shape_of(geom):
    """The exact OCCT shape behind `geom` -- its registered master, else rebuilt from its
    recipe -- or None (no master and no recipe, or the kernel is not installed).

    A solid's `.Faces` may belong to its display proxy; when an analytic master exists,
    the master is the geometry used for exact measurement.
    """
    try:
        kernel, ob = _kernel_mods()
        if not ob.available():
            return None
    except Exception:  # noqa: BLE001
        return None
    m = kernel.master_of(geom)
    if m is not None:
        return m
    recipe = kernel.recipe_of(geom)
    if not recipe:
        return None
    try:
        return ob.from_recipe(recipe)
    except Exception:  # noqa: BLE001
        return None


def is_proxy(geom):
    """True when `geom` is the DISPLAY proxy of an analytic master -- its own faces are a
    triangulation and must never be reported as the measurement."""
    try:
        return bool(_kernel_mods()[0].master_key_of(geom))
    except Exception:  # noqa: BLE001
        return False


def exact_mass(geom, solid=True):
    """`occt_backend.mass_properties` of `geom`'s exact shape, or None when it has none."""
    shape = occt_shape_of(geom)
    if shape is None:
        return None
    try:
        return _kernel_mods()[1].mass_properties(shape, solid=solid)
    except Exception:  # noqa: BLE001
        return None


def is_closed_solid(geom):
    """Is `geom` a closed solid? Asked of the exact shape when there is one (airtightness
    measured by OCCT), else of rhino3dm's `IsSolid`."""
    shape = occt_shape_of(geom)
    if shape is not None:
        try:
            return bool(_kernel_mods()[1].is_airtight(shape))
        except Exception:  # noqa: BLE001
            pass
    return bool(getattr(geom, "IsSolid", False))


# ---------------------------------------------------------------------------
# Centroids, radius of curvature, and angles
# ---------------------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _xyz(p):
    return (p.X, p.Y, p.Z) if hasattr(p, "X") else tuple(float(c) for c in p)


def angle_between(u, v):
    """The angle between two direction vectors, in DEGREES, 0..180.

    `atan2(|u x v|, u . v)` rather than `acos` of the normalised dot product: acos loses
    half its digits near 0 and 180 degrees (a 1e-8 rad angle comes back as 0). Raises
    ValueError for a zero-length direction -- an angle to nothing is not 0.
    """
    u, v = _xyz(u), _xyz(v)
    if _norm(u) <= 1e-300 or _norm(v) <= 1e-300:
        raise ValueError("a zero-length line has no direction")
    return math.degrees(math.atan2(_norm(_cross(u, v)), _dot(u, v)))


def curve_plane(curve, samples=64):
    """``(origin, unit_normal)`` of a PLANAR curve, or None if it is not planar.

    rhino3dm 8.35 has `Curve.IsPlanar` but no `TryGetPlane` (MEASURED). Newell's normal
    of points sampled ON the curve is exactly the plane's normal when the curve is planar
    (every sample lies in the plane, and Newell's sum is the polygon's exact vector area).
    """
    try:
        if not curve.IsPlanar():
            return None
    except Exception:  # noqa: BLE001
        pass
    d = curve.Domain
    pts = [_xyz(curve.PointAt(d.T0 + (d.T1 - d.T0) * i / samples))
           for i in range(samples)]
    n = [0.0, 0.0, 0.0]
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)]
        n[0] += (p[1] - q[1]) * (p[2] + q[2])
        n[1] += (p[2] - q[2]) * (p[0] + q[0])
        n[2] += (p[0] - q[0]) * (p[1] + q[1])
    ln = _norm(n)
    if ln <= 1e-300:
        return None
    return pts[0], (n[0] / ln, n[1] / ln, n[2] / ln)


def _green_moments(curve, origin, ex, ey, order):
    """``(A, Mx, My)`` of the region a closed planar curve bounds, in the plane frame
    (`origin`, `ex`, `ey`), by Green's theorem with `order`-point Gauss-Legendre per knot
    span on the EXACT curve:

        A  = 1/2 ∮ (x dy - y dx),   Mx = 1/2 ∮ x² dy,   My = -1/2 ∮ y² dx

    Signed by the curve's orientation (the caller divides, so the sign cancels).
    """
    nodes, weights = arclength._gauss_legendre(order)
    d = curve.Domain
    cuts = [d.T0] + arclength._interior_knots(curve) + [d.T1]
    a = mx = my = 0.0
    for lo, hi in zip(cuts, cuts[1:]):
        half, mid = 0.5 * (hi - lo), 0.5 * (hi + lo)
        for xi, w in zip(nodes, weights):
            t = mid + half * xi
            p, dp = curve.DerivativeAt(t, 1)[:2]
            r = _sub(_xyz(p), origin)
            x, y = _dot(r, ex), _dot(r, ey)
            dx, dy = _dot(_xyz(dp), ex), _dot(_xyz(dp), ey)
            ww = w * half
            a += ww * 0.5 * (x * dy - y * dx)
            mx += ww * 0.5 * x * x * dy
            my -= ww * 0.5 * y * y * dx
    return a, mx, my


def planar_curve_area_centroid(curve):
    """Exact area centroid of the region a closed planar curve bounds, or None.

    Returns ``(area, area_err, centroid_xyz, centroid_err_xyz)``. Green's theorem on the
    exact NURBS (rational spans included -- `DerivativeAt` is the true derivative), at two
    Gauss-Legendre orders; the answer is the higher order's and the error the difference,
    floored at 1e-12 of the curve's size, matching `occt_backend.MASS_ERR_FLOOR`.
    Unlike `closed_curve_area`'s 512-gon this is not an inscribed polygon: a circle's
    centroid is its centre and its area pi r² to ~1e-15.
    """
    if not curve.IsClosed:
        return None
    plane = curve_plane(curve)
    if plane is None:
        return None
    origin, nrm = plane
    seed = (1.0, 0.0, 0.0) if abs(nrm[0]) < 0.9 else (0.0, 1.0, 0.0)
    ex = _cross(seed, nrm)
    ex = tuple(c / _norm(ex) for c in ex)
    ey = _cross(nrm, ex)
    crv = curve if hasattr(curve, "Knots") else curve.ToNurbsCurve()

    def solve(order):
        a, mx, my = _green_moments(crv, origin, ex, ey, order)
        if abs(a) <= 1e-300:
            return None
        cx, cy = mx / a, my / a
        c = tuple(origin[i] + cx * ex[i] + cy * ey[i] for i in range(3))
        return abs(a), c

    lo, hi = solve(12), solve(24)
    if lo is None or hi is None:
        return None
    size = math.sqrt(hi[0])
    floor = size * 1e-12
    area_err = max(abs(hi[0] - lo[0]), hi[0] * 1e-12)
    c_err = tuple(max(abs(hi[1][i] - lo[1][i]), floor) for i in range(3))
    return hi[0], area_err, hi[1], c_err


def _grid_area_centroid(geom, n):
    area = 0.0
    acc = [0.0, 0.0, 0.0]
    for a, b, c in _face_grid(geom, n, n):
        t = _tri_area(a, b, c)
        area += t
        acc[0] += t * (a.X + b.X + c.X) / 3.0
        acc[1] += t * (a.Y + b.Y + c.Y) / 3.0
        acc[2] += t * (a.Z + b.Z + c.Z) / 3.0
    return area, tuple(v / area for v in acc) if area > 0 else None


def _grid_volume_centroid(geom, n):
    """Signed tetrahedra from the origin over the face grid: V = sum v_i, and the centroid
    is sum v_i (a+b+c)/4 / V -- the divergence theorem, as `solid_volume`."""
    vol = 0.0
    acc = [0.0, 0.0, 0.0]
    for a, b, c in _face_grid(geom, n, n):
        v = (a.X * (b.Y * c.Z - c.Y * b.Z) - a.Y * (b.X * c.Z - c.X * b.Z)
             + a.Z * (b.X * c.Y - c.X * b.Y)) / 6.0
        vol += v
        acc[0] += v * (a.X + b.X + c.X) / 4.0
        acc[1] += v * (a.Y + b.Y + c.Y) / 4.0
        acc[2] += v * (a.Z + b.Z + c.Z) / 4.0
    return abs(vol), tuple(x / vol for x in acc) if vol else None


def grid_centroid(geom, solid):
    """``(mass, mass_err, centroid, centroid_err)`` for geometry with NO exact shape.

    The same rule as `commands._grid_measure`: all-bilinear faces are exact at n=1;
    anything curved is taken at n=64 with the error 4/3 |f(64) - f(32)| (the grid is
    second order). `mass` is the area, or the volume when `solid`.
    """
    fn = _grid_volume_centroid if solid else _grid_area_centroid
    if analysis_density(geom) == 1:
        m, c = fn(geom, 1)
        if c is None:
            return None
        size = abs(m) ** (1.0 / (3.0 if solid else 2.0))
        return m, abs(m) * 1e-12, c, (size * 1e-12,) * 3
    m_hi, c_hi = fn(geom, 64)
    m_lo, c_lo = fn(geom, 32)
    if c_hi is None or c_lo is None:
        return None
    size = abs(m_hi) ** (1.0 / (3.0 if solid else 2.0))
    return (m_hi, max(abs(m_hi - m_lo) * 4.0 / 3.0, abs(m_hi) * 1e-12), c_hi,
            tuple(max(abs(c_hi[i] - c_lo[i]) * 4.0 / 3.0, size * 1e-12)
                  for i in range(3)))


def exact_centroid(geom, solid):
    """``(mass, mass_err, centroid, centroid_err)`` from `geom`'s exact OCCT shape, or
    None when it has none (then the caller decides between the grid and a refusal)."""
    mp = exact_mass(geom, solid=solid)
    if mp is None:
        return None
    key = "volume" if solid else "area"
    return (mp[key], mp[key + "_err"], mp[key + "_centroid"],
            mp.get(key + "_centroid_err") or (mp[key + "_err"],) * 3)


def combine_centroids(parts):
    """The cumulative centroid of ``[(mass, mass_err, centroid, centroid_err)]``.

    C = sum m_i c_i / M. Error, first order: dC = sum (m_i dc_i + |c_i - C| dm_i) / M,
    per axis -- each part's centroid error weighted by its share, plus how far a mass
    error would drag C. Returns ``(M, M_err, C, C_err)``.
    """
    total = sum(p[0] for p in parts)
    if not total > 0:
        return None
    c = tuple(sum(p[0] * p[2][i] for p in parts) / total for i in range(3))
    c_err = tuple(sum(p[0] * p[3][i] + abs(p[2][i] - c[i]) * p[1] for p in parts) / total
                  for i in range(3))
    return total, sum(p[1] for p in parts), c, c_err


def curvature_radius(curve, t):
    """``(radius, center)`` of the osculating circle of `curve` at parameter `t`.

    `radius` is ``math.inf`` (and `center` None) where the curve is straight. An arc or
    circle answers from its OWN radius (`TryGetArc` -- `IsCircle()` is False for an arc,
    MEASURED), so a drawn R5 reads 5 exactly; anything else from `CurvatureAt`, the
    analytic curvature vector (magnitude 1/r, pointing at the centre).
    """
    p = _xyz(curve.PointAt(t))
    try:
        if curve.IsLinear():
            return math.inf, None
    except Exception:  # noqa: BLE001
        pass
    try:
        arc = curve.TryGetArc()
    except Exception:  # noqa: BLE001
        arc = None
    if arc is not None and arc.IsValid:
        return float(arc.Radius), _xyz(arc.Center)
    k = _xyz(curve.CurvatureAt(t))
    mag = _norm(k)
    # a curvature this small is a radius past 1e12 model units: straight, honestly
    if mag <= 1e-12:
        return math.inf, None
    r = 1.0 / mag
    return r, tuple(p[i] + k[i] * r * r for i in range(3))
