"""Soft ground-shadow geometry for Rendered display mode; no `bpy` dependency.

The automatic ground plane follows the lowest meshable object and is visible only from its
top side. Technical and Wireframe display modes do not use this shadow.

Blender's Solid mode cannot cast a shadow onto a floor that does not exist, so this is a
footprint approximation rather than a light simulation. Each object's footprint is the
convex hull of its display vertices seen from above (a cylinder is round and a box is
square), drawn at full darkness and expanded outward in round-cornered rings whose opacity
falls off quadratically. `SPREAD_XY`, `SPREAD_Z`, and `MAX_ALPHA` produce a broad, soft fade.
A concave object such as an L-bracket shades its hull rather than its exact outline; this is
the deliberate limit of the approximation.
"""
from __future__ import annotations

import math

#: opacity of black directly under the object
MAX_ALPHA = 0.4
#: how far the shadow spreads beyond the footprint, per unit of footprint size / height
SPREAD_XY = 0.25
SPREAD_Z = 0.65
#: rings between the footprint and the fully faded edge
RINGS = 10
#: the largest angle one segment of a rounded corner may turn through
ARC_STEP = math.radians(15.0)


def convex_hull(points):
    """The convex hull of 2-D `points`, counter-clockwise, no repeated end point.

    Andrew's monotone chain. Fewer than three distinct non-collinear points give what is
    left (0, 1 or 2 points) -- a footprint with no area.
    """
    pts = sorted(set((float(p[0]), float(p[1])) for p in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 1e-12:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 1e-12:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def ground_altitude(objects):
    """The ground plane's z: the lowest point of the ``(hull, zmin, zmax)`` objects, or None."""
    zs = [o[1] for o in objects or []]
    return min(zs) if zs else None


def visible_from(ground_z, eye=None, view_dir_z=None):
    """Is the TOP of the ground plane facing the camera?

    `eye` is the camera position (perspective); `view_dir_z` is the z of the unit vector
    pointing from the scene TOWARD the viewer (orthographic). A horizontal view (Front,
    Right) sees the plane edge-on, so nothing is drawn there either.
    """
    if eye is not None:
        return eye[2] > ground_z
    if view_dir_z is not None:
        return view_dir_z > 1e-6
    return False


def spread_of(hull, zmin, zmax):
    """How far beyond its footprint one object's shadow reaches."""
    if not hull:
        return 0.0
    dx = max(p[0] for p in hull) - min(p[0] for p in hull)
    dy = max(p[1] for p in hull) - min(p[1] for p in hull)
    return SPREAD_XY * max(dx, dy) + SPREAD_Z * max(zmax - zmin, 0.0)


def falloff(t):
    """Opacity at fraction `t` (0 = footprint edge, 1 = outer edge) of the spread."""
    t = min(max(t, 0.0), 1.0)
    return MAX_ALPHA * (1.0 - t) ** 2


def _corner_angles(hull):
    """Per hull vertex, the outward-normal angles its rounded corner sweeps through.

    The same list serves every ring, so ring k and ring k+1 have matching vertices and the
    band between them triangulates as a simple strip.
    """
    n = len(hull)
    out = []
    for i in range(n):
        a, b, c = hull[i - 1], hull[i], hull[(i + 1) % n]
        # outward normal of a CCW edge (dx, dy) is (dy, -dx)
        a0 = math.atan2(-(b[0] - a[0]), b[1] - a[1])
        a1 = math.atan2(-(c[0] - b[0]), c[1] - b[1])
        turn = (a1 - a0) % (2.0 * math.pi)
        k = max(1, int(math.ceil(turn / ARC_STEP)))
        out.append([a0 + turn * j / k for j in range(k + 1)])
    return out


def _ring(hull, angles, d, z):
    pts = []
    for (x, y), arc in zip(hull, angles):
        for a in arc:
            pts.append((x + d * math.cos(a), y + d * math.sin(a), z))
    return pts


def shadow_triangles(hull, zmin, zmax, ground_z):
    """Triangles for one object's shadow on the plane z = `ground_z`.

    `hull` is the object's footprint, CCW (see `convex_hull`). Returns
    ``(positions, alphas)`` -- three entries per triangle, ready for a TRIS batch with a
    per-vertex colour of ``(0, 0, 0, alpha)``. Empty for a footprint with no area.
    """
    if len(hull) < 3 or not all(math.isfinite(v) for p in hull for v in p):
        return [], []
    z = ground_z
    pos, alpha = [], []
    a = falloff(0.0)
    x0, y0 = hull[0]
    for i in range(1, len(hull) - 1):            # convex, so a fan is a valid fill
        pos.extend(((x0, y0, z), (hull[i][0], hull[i][1], z),
                    (hull[i + 1][0], hull[i + 1][1], z)))
        alpha.extend((a, a, a))
    spread = spread_of(hull, zmin, zmax)
    if spread <= 0.0:
        return pos, alpha
    angles = _corner_angles(hull)
    rings = [_ring(hull, angles, spread * i / RINGS, z) for i in range(RINGS + 1)]
    n = len(rings[0])
    for i in range(RINGS):
        inner, outer = rings[i], rings[i + 1]
        ai, ao = falloff(i / RINGS), falloff((i + 1) / RINGS)
        for k in range(n):
            k2 = (k + 1) % n
            pos.extend((inner[k], outer[k], outer[k2]))
            alpha.extend((ai, ao, ao))
            pos.extend((inner[k], outer[k2], inner[k2]))
            alpha.extend((ai, ao, ai))
    return pos, alpha


def scene_shadow(objects):
    """Every object's shadow on the shared automatic ground plane: ``(positions, alphas)``.

    `objects` is a list of ``(hull, zmin, zmax)``. The plane sits a hair BELOW the lowest
    point, so an object's own bottom face (which a camera above the plane cannot see anyway)
    does not z-fight with it.
    """
    z = ground_altitude(objects)
    if z is None:
        return [], []
    size = max(max(o[2] - o[1], spread_of(o[0], 0.0, 0.0) / SPREAD_XY) for o in objects)
    z -= 1e-4 * max(size, 1.0)
    pos, alpha = [], []
    for hull, zmin, zmax in objects:
        p, a = shadow_triangles(hull, zmin, zmax, z)
        pos.extend(p)
        alpha.extend(a)
    return pos, alpha
