"""Dynamic command preview: the wireframe to draw while a command is running.

**No `bpy`, no rhino3dm.** Pure geometry keeps previews usable without launching
Blender.

Why this exists
---------------
the reference CAD application gives continuous feedback while you work: after the first click of `Line` a
line follows the cursor until you click again, `Circle` grows as you move, and so
on. The help calls it a **dynamic preview** ("Preview -- Displays a dynamic
preview", `commands/array.htm`; "as you move the cursor ... you will see a dynamic
preview image", `commands/orientonsrf.htm`). Without it, a command gives no visual
indication of what the next point will build.

The decidable part is a pure function: **given the points already picked, the
current cursor point, and the active construction plane, what wireframe should be
drawn?** That lives here. `ui.py` only turns the returned polylines into GPU line
batches.

The contract
------------
``segments(name, args, cursor, plane)`` returns a **list of polylines**, each a
list of ``(x, y, z)`` world points, to be drawn as connected line strips. An empty
list means "nothing to preview yet", which is the correct answer for a command
that has no preview or has not collected enough points.

Everything is in WORLD coordinates, because that is what the viewport draws in.
`plane` is the active CPlane and is needed by anything round or rectangular: a
circle previewed in a Front viewport must lie in the Front plane, not flat on the
ground.

Adding a command (this is the extension point)
----------------------------------------------
Write a function taking ``(args, cursor, plane)`` and returning a list of
polylines, then add it to :data:`PREVIEWS` under the command's lowercase name.
Nothing else needs to change because `ui.py` dispatches through that table.
"""
from __future__ import annotations

import math

#: how many segments a previewed full circle is drawn with. Enough to read as a
#: circle at any practical zoom; the real geometry is built by rhino3dm later, so
#: this only affects the preview's smoothness.
CIRCLE_SEGMENTS = 64


# ---------------------------------------------------------------------------
# small vector helpers (kept local so this module stays dependency-free)
# ---------------------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _length(v):
    return (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5


def dot(a, b):
    """Dot product. (Named `dot` to match the other geometry modules.)"""
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _is_point(value):
    """True for something shaped like a 3-D point.

    Commands mix points and numbers in `args` (Box collects two corners then a
    height), so a preview must never assume `args[i]` is a point.
    """
    return (isinstance(value, (tuple, list)) and len(value) == 3
            and all(isinstance(c, (int, float)) for c in value))


def points_only(args):
    """Just the point-shaped entries of `args`, in order."""
    return [tuple(a) for a in (args or []) if _is_point(a)]


# ---------------------------------------------------------------------------
# shapes, in a construction plane
# ---------------------------------------------------------------------------

def circle_polyline(plane, center, radius, segments=CIRCLE_SEGMENTS):
    """A closed circle of `radius` about `center`, lying IN `plane`.

    `center` is a world point; the circle is swept in the plane's x/y axes, so it
    is correctly oriented in every viewport. Returns [] for a non-positive radius,
    which is what a zero-size preview should draw.
    """
    if radius <= 0:
        return []
    u, v = plane.xaxis, plane.yaxis
    pts = []
    for i in range(segments + 1):          # +1 closes the loop
        a = 2.0 * math.pi * i / segments
        cos_a, sin_a = math.cos(a) * radius, math.sin(a) * radius
        pts.append((center[0] + u[0] * cos_a + v[0] * sin_a,
                    center[1] + u[1] * cos_a + v[1] * sin_a,
                    center[2] + u[2] * cos_a + v[2] * sin_a))
    return pts


def rectangle_polyline(plane, corner, opposite):
    """A closed rectangle with two OPPOSITE corners, lying IN `plane`.

    Both corners are world points. The rectangle's sides run along the plane's
    axes -- which is what makes a Box previewed in a Front viewport stand up in
    the Front plane instead of lying flat.
    """
    u0, v0, _ = plane.from_world(corner)
    u1, v1, w1 = plane.from_world(opposite)
    return [plane.to_world(u0, v0, w1),
            plane.to_world(u1, v0, w1),
            plane.to_world(u1, v1, w1),
            plane.to_world(u0, v1, w1),
            plane.to_world(u0, v0, w1)]


# ---------------------------------------------------------------------------
# Previews for transform and measurement commands.
#
# Both of these are MEASURED to need one:
#   commands/distance.htm -- "Pick the first point. Pick the second point."
#   commands/mirror.htm   -- "Pick the start of the mirror plane. Pick the end of
#                            the mirror plane. AS YOU MOVE THE CURSOR, THE REFERENCE CAD APPLICATION
#                            PREVIEWS THE LOCATION FOR THE MIRRORED OBJECTS."
#
# `Move` and `Copy` use their own translation-vector preview below.
# ---------------------------------------------------------------------------

def preview_distance(args, cursor, plane):
    """`Distance`: the measuring line from the first point to the cursor.

    MEASURED (commands/distance.htm): "Pick the first point. Pick the second point."
    Two picked points, so the cursor fully describes the second one and the line is
    exactly what the command is about -- it had no preview at all, which made a
    measuring command you could not see while aiming it.

    Geometrically identical to `preview_line`, and deliberately NOT aliased to it:
    they are the same shape today by coincidence, and `Distance` acquires a readout
    the moment we have one to draw, while `Line` never will.
    """
    pts = points_only(args)
    if not pts:
        return []
    return [[pts[0], cursor]]


def preview_mirror(args, cursor, plane):
    """`Mirror`: the mirror LINE from its start to the cursor.

    MEASURED (commands/mirror.htm): "Pick the start of the mirror plane. Pick the end
    of the mirror plane." and "The two end points specify a mirror plane
    PERPENDICULAR TO THE CONSTRUCTION PLANE" -- which is why the line alone is an
    honest preview of it: the plane is fully determined by the segment plus the
    CPlane normal, so there is nothing hidden.

    WHAT THIS IS NOT, and the gap is deliberate. The same page says "as you move the
    cursor, the reference CAD application previews the location for the MIRRORED OBJECTS" -- a ghost of the
    result, not just the axis. That needs the selected objects' geometry, and
    `segments(name, args, cursor, plane)` is handed no geometry at all. Widening that
    signature would be required to draw that ghost. The axis is the complete portion
    that can be drawn from the current inputs.
    """
    pts = points_only(args)
    if not pts:
        return []
    return [[pts[0], cursor]]


def preview_move(args, cursor, plane):
    """`Move` / `Copy`: the translation vector from the "from" point to the cursor.

    MEASURED (commands/move.htm): "Pick a point to move from. ... Pick a point to
    move to." The segment from the first picked point to the cursor IS the delta
    the command is specifying -- the honest preview, exactly like `preview_mirror`'s
    axis. The two-point form provides both the "from" point and the cursor position
    needed for this rubber band.

    WHAT THIS IS NOT: the same deliberate gap as `preview_mirror`. the reference CAD application also ghosts
    the moved objects at the cursor; that needs the selected objects' geometry, which
    `segments()` is not handed. Drawing the vector is the honest part that can be done
    without it.
    """
    pts = points_only(args)
    if not pts:
        return []
    return [[pts[0], cursor]]


# ---------------------------------------------------------------------------
# per-command previews.  (args, cursor, plane) -> list of polylines
# ---------------------------------------------------------------------------

def preview_line(args, cursor, plane):
    """`Line`: a rubber band from the first point to the cursor."""
    pts = points_only(args)
    if not pts:
        return []                      # nothing picked yet -- nothing to show
    return [[pts[0], cursor]]


def preview_polyline(args, cursor, plane):
    """`Polyline`: the chain so far, plus a segment to the cursor."""
    pts = points_only(args)
    if not pts:
        return []
    return [pts + [cursor]]


#: How many straight segments to draw per control-point SPAN when previewing a
#: smooth curve. A span is one piece of the B-spline between interior knots,
#: so the total rises with the number of picks: the smallest cubic (four picks, one
#: span) gets 12 segments and a six-pick curve gets 36. Twelve was chosen over an
#: initial 8 because one cubic span filling a viewport is visibly faceted at 8, which
#: is the very complaint this change exists to fix. Deliberately NOT routed through
#: `tessellate.curve_segments_for`: that path is keyed off arc length and carries
#: geometry dependencies, while this file stays pure and runs per frame.
CURVE_PREVIEW_PER_SPAN = 12


def _clamped_uniform_knots(n, degree):
    """The knot vector `CreateControlPointCurve` uses: clamped, uniform interior.

    ``p+1`` zeros, the interior knots ``1 .. n-p-1``, then ``p+1`` copies of
    ``n-p``; length ``n + p + 1``, domain ``[0, n-p]``.
    """
    interior = list(range(1, n - degree))
    return ([0.0] * (degree + 1) + [float(k) for k in interior]
            + [float(n - degree)] * (degree + 1))


def _de_boor(control, degree, u, knots):
    """Evaluate a B-spline at `u` by de Boor's algorithm. Returns an (x, y, z)."""
    n = len(control)
    # the span containing u, clamped into the valid range [degree, n-1]
    k = degree
    while k < n - 1 and knots[k + 1] <= u:
        k += 1
    d = [tuple(control[j + k - degree]) for j in range(degree + 1)]
    for r in range(1, degree + 1):
        for j in range(degree, r - 1, -1):
            lo = knots[j + k - degree]
            hi = knots[j + 1 + k - r]
            a = 0.0 if hi == lo else (u - lo) / (hi - lo)
            d[j] = tuple((1.0 - a) * d[j - 1][i] + a * d[j][i] for i in range(3))
    return d[degree]


def control_point_curve_polyline(control, max_degree=3,
                                 per_span=CURVE_PREVIEW_PER_SPAN):
    """Sample the smooth curve those CONTROL points define, as a polyline.

    MEASURED (commands/curve.htm): "The Curve command draws a curve from **control
    point** locations. Curve control points match input." and "makes a curve of
    degree <number of points> - 1 as long as the number of control points is less
    than or equal to the degree setting". So the degree rule is
    ``min(max_degree, len(control) - 1)``, which is exactly what
    `bridge.make_polyline` applies -- two picks give a straight line, three a
    quadratic, four or more a cubic.

    This exists because drawing the control polygon for a smooth curve is misleading.
    `Curve` commits a degree-3 NURBS while the old preview
    showed straight chords between the picks, so the shape on screen was not the
    shape you got -- which is the whole point of a preview.

    This stays pure because it runs on every mouse-move frame. Its construction must
    remain aligned with the curve that `bridge.make_polyline` builds.
    """
    pts = [tuple(p) for p in control]
    if len(pts) < 2:
        return []
    degree = min(int(max_degree), len(pts) - 1)
    if degree <= 1:
        return pts          # a degree-1 B-spline IS the control polygon
    knots = _clamped_uniform_knots(len(pts), degree)
    hi = float(len(pts) - degree)
    steps = max(1, int(per_span) * (len(pts) - degree))
    out = [_de_boor(pts, degree, hi * i / steps, knots) for i in range(steps)]
    out.append(tuple(pts[-1]))   # the clamped end is exactly the last control point
    return out


def preview_curve(args, cursor, plane):
    """`Curve`/`InterpCrv`: the SMOOTH curve the picks define, not the chain.

    The cursor is treated as the next control point, so the curve flexes as you
    move -- which is what the reference CAD application shows. See `control_point_curve_polyline`.
    """
    pts = points_only(args)
    if not pts:
        return []
    smooth = control_point_curve_polyline(pts + [cursor])
    return [smooth] if smooth else []


def preview_circle(args, cursor, plane):
    """`Circle`: a circle about the picked centre, sized by the cursor.

    Also draws the radius line, which is what the reference CAD application shows while you drag.
    """
    pts = points_only(args)
    if not pts:
        return []
    center = pts[0]
    radius = _length(_sub(cursor, center))
    circle = circle_polyline(plane, center, radius)
    if not circle:
        return []
    return [circle, [center, cursor]]


def preview_box(args, cursor, plane):
    """`Box`: the base rectangle while picking corner 2, then the full wireframe.


    This function used to stop at the base rectangle, and its own docstring stated
    the reason: *"the command asks for a HEIGHT, which is a number rather than a
    point, so the cursor no longer describes the box"*. That reasoning was wrong,
    because the height step accepts a picked location as well as a typed number,
    and its step kind is `DISTANCE`,
    the same kind `Cylinder` and `Cone` use for their heights. Both of those DO
    follow the cursor. Box was the only previewed command whose preview ignored a
    step the cursor can drive.

    So the height comes from the cursor exactly as `preview_cylinder` takes it:
    measured from the base along the plane normal, matching `bridge.make_box`, so the preview and the committed solid agree in every viewport
    including Front and Right.
    """
    pts = points_only(args)
    if not pts:
        return []
    if len(pts) == 1:
        return [rectangle_polyline(plane, pts[0], cursor)]
    base = rectangle_polyline(plane, pts[0], pts[1])
    if not base:
        return []
    # While height is being picked, args has the two settled corners only;
    # cursor is the prospective third value. The old `len(args) < 3` gate therefore
    # returned the base for the entire live height step and only became false after
    # the command had already committed -- a test had hidden it by inventing a third
    # `0.0` argument that production never has.
    # The height is the cursor's distance from the base along the plane normal --
    # the signed component, not the raw distance, so dragging back through the base
    # does not read as a positive height.
    normal = plane.zaxis
    height = dot(_sub(cursor, pts[0]), normal)
    if abs(height) <= 0.0:
        return [base]
    lift = (normal[0] * height, normal[1] * height, normal[2] * height)
    top = [(p[0] + lift[0], p[1] + lift[1], p[2] + lift[2]) for p in base]
    # the four verticals: `base` is a closed loop, so its last point repeats the
    # first and only the first four corners are distinct
    sides = [[base[i], top[i]] for i in range(4)]
    return [base, top] + sides


def preview_rectangle(args, cursor, plane):
    """`Rectangle`: the rubber-band rectangle while picking corner 2.

    The committed geometry (bridge.make_rectangle) and this preview both run the
    sides along the plane's axes, so they agree in every viewport -- including
    Front/Right, where the old world-XY-only builders went wrong.
    """
    pts = points_only(args)
    if not pts:
        return []
    if len(pts) == 1:
        return [rectangle_polyline(plane, pts[0], cursor)]
    return [rectangle_polyline(plane, pts[0], pts[1])]


def preview_ellipse(args, cursor, plane):
    """`Ellipse`: centre, then the two semi-axes as they are picked.

    One point (the centre) -- show the first semi-axis rubber-banding to the
    cursor. Two points (centre + first axis end) -- show both semi-axes: the
    fixed one (centre->axis1) and the live one (centre->cursor). Three points --
    show the full ellipse, sampled exactly as bridge.make_ellipse maps it
    (centre + u*cos(t) + v*sin(t)), so the preview and the result agree.
    """
    pts = points_only(args)
    if not pts:
        return []
    center = pts[0]
    if len(pts) == 1:
        # only the centre is fixed; the first semi-axis follows the cursor
        return [[center, cursor]]
    if len(pts) == 2:
        # centre + first axis end fixed; the second semi-axis follows the cursor
        return [[center, pts[1]], [center, cursor]]
    # both axes fixed: the full ellipse
    u = _sub(pts[1], center)
    v = _sub(pts[2], center)
    out = []
    for i in range(CIRCLE_SEGMENTS + 1):
        a = 2.0 * math.pi * i / CIRCLE_SEGMENTS
        c, s = math.cos(a), math.sin(a)
        out.append((center[0] + u[0] * c + v[0] * s,
                    center[1] + u[1] * c + v[1] * s,
                    center[2] + u[2] * c + v[2] * s))
    return [out]


def polygon_polyline(plane, center, radius, sides, inscribed=True):
    """A regular polygon of `radius` and `sides` about `center`, in `plane`.

    `inscribed` (the reference CAD application's default) means the corners sit on a circle of the given
    radius; `circumscribed` means the side midpoints do (the radius is the
    apothem), so the corner radius is `radius / cos(pi/sides)`. Returns a closed
    polyline, or [] for a degenerate input. This mirrors bridge.make_polygon's
    inscribed/circumscribed choice so the preview and the result agree.
    """
    if radius <= 0 or sides < 3:
        return []
    corner_radius = radius if inscribed else radius / math.cos(math.pi / sides)
    # the first vertex sits along the plane's x axis, matching the canonical
    # build (a unit circle's 0-parameter point is on its +x axis)
    u, v = plane.xaxis, plane.yaxis
    pts = []
    for i in range(sides + 1):          # +1 closes the loop
        a = 2.0 * math.pi * i / sides
        c, s = math.cos(a) * corner_radius, math.sin(a) * corner_radius
        pts.append((center[0] + u[0] * c + v[0] * s,
                    center[1] + u[1] * c + v[1] * s,
                    center[2] + u[2] * c + v[2] * s))
    return pts


def preview_polygon(args, cursor, plane):
    """`Polygon`: the inscribed circle while picking, then the polygon outline.

    `args` is [center, radius, sides]: the radius is a DISTANCE (stored as a
    float, so points_only drops it) and the sides is a NUMBER. So we read the
    raw `args` rather than points_only. One point -> the inscribed circle sized
    by the cursor. Two (centre + radius) -> the circle at that radius. Three
    (centre + radius + sides) -> the actual polygon outline.
    """
    if not args:
        return []
    center = args[0]
    # the radius, once picked, is args[1] as a float (DISTANCE step)
    radius = args[1] if len(args) >= 2 and not _is_point(args[1]) else None
    sides = args[2] if len(args) >= 3 and not _is_point(args[2]) else None
    if radius is None:
        radius = _length(_sub(cursor, center))
    circle = circle_polyline(plane, center, radius)
    if not circle:
        return []
    if sides is None or int(sides) < 3:
        return [circle]                 # radius known, sides not yet -> the circle
    poly = polygon_polyline(plane, center, radius, int(sides), inscribed=True)
    return [poly] if poly else [circle]


def preview_sphere(args, cursor, plane):
    """`Sphere`: the equator circle (in the CPlane) sized by the radius.

    `args` is [center, radius]; the radius is a DISTANCE (a float), so it is read
    from `args`, not `points_only`. While the radius is being picked the circle
    follows the cursor and the radius line is shown; once picked the settled
    circle is drawn. A sphere is plane-independent, so the CPlane only decides
    WHICH great circle we draw -- the natural one is the one in the active plane.
    """
    pts = points_only(args)
    if not pts:
        return []
    center = pts[0]
    if len(args) >= 2 and not _is_point(args[1]):
        radius = args[1]
    else:
        radius = _length(_sub(cursor, center))
    circle = circle_polyline(plane, center, radius)
    if not circle:
        return []
    if len(args) == 1:
        return [circle, [center, cursor]]
    return [circle]


def preview_cylinder(args, cursor, plane):
    """`Cylinder`: the base disc while picking, then the full wireframe.

    `args` is [center, radius, height]. While the radius is picked, show the base
    circle (sized by the cursor) and the radius line. Once the radius is set and
    the height is being picked, show the real cylinder: the base circle, the top
    circle lifted by the height along the plane NORMAL (which is what
    bridge.make_cylinder builds -- height along the CPlane normal), and the four
    vertical edges. The height is read from the cursor (measured from the base
    centre), exactly as the DISTANCE step does, so we are not guessing a value the
    user has not given.
    """
    pts = points_only(args)
    if not pts:
        return []
    center = pts[0]
    normal = plane.zaxis
    if len(args) >= 2 and not _is_point(args[1]):
        radius = args[1]
    else:
        radius = _length(_sub(cursor, center))
    base = circle_polyline(plane, center, radius)
    if not base:
        return []
    if len(args) == 1:
        return [base, [center, cursor]]
    height = _length(_sub(cursor, center))
    if height <= 0:
        return [base]
    top_center = (center[0] + normal[0] * height,
                  center[1] + normal[1] * height,
                  center[2] + normal[2] * height)
    top = circle_polyline(plane, top_center, radius)
    sides = []
    for i in (0, CIRCLE_SEGMENTS // 4, CIRCLE_SEGMENTS // 2, 3 * CIRCLE_SEGMENTS // 4):
        sides.append([base[i], top[i]])
    return [base, top] + sides


def preview_cone(args, cursor, plane):
    """`Cone`: the base disc while picking, then base + apex + slant edges.

    `args` is [center, base_radius, height]. While the base radius is picked,
    show the base circle (sized by the cursor) and the radius line. Once the
    radius is set and the height is being picked, show the base circle, the apex
    (lifted by the height along the plane normal, as bridge.make_cone builds it),
    and the four slant edges from the base's cardinal points to the apex.
    """
    pts = points_only(args)
    if not pts:
        return []
    center = pts[0]
    normal = plane.zaxis
    if len(args) >= 2 and not _is_point(args[1]):
        radius = args[1]
    else:
        radius = _length(_sub(cursor, center))
    base = circle_polyline(plane, center, radius)
    if not base:
        return []
    if len(args) == 1:
        return [base, [center, cursor]]
    height = _length(_sub(cursor, center))
    if height <= 0:
        return [base]
    apex = (center[0] + normal[0] * height,
            center[1] + normal[1] * height,
            center[2] + normal[2] * height)
    sides = []
    for i in (0, CIRCLE_SEGMENTS // 4, CIRCLE_SEGMENTS // 2, 3 * CIRCLE_SEGMENTS // 4):
        sides.append([base[i], apex])
    return [base] + sides


def _arc_polyline(start, end, through, segments=CIRCLE_SEGMENTS):
    """The arc through three world points, as a list of points (preview math).

    This is pure preview geometry: the committed curve is built by
    bridge.make_arc via rhino3dm. Here we find the circumcircle of the triangle
    (start, through, end) and sample the arc from `start` to `end` that passes
    through `through` -- which is exactly what the reference CAD application's default three-point arc
    is. Returns [] when the points are degenerate (collinear / coincident), in
    which case there is no arc to show.
    """
    def _cross(a, b):
        return (a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    ab = _sub(end, start)
    ac = _sub(through, start)
    n = _cross(ab, ac)
    nl = _length(n)
    if nl < 1e-9:
        return []                       # collinear: no arc
    n = (n[0] / nl, n[1] / nl, n[2] / nl)
    # an in-plane basis, u along start->end, v completing the right-hand frame
    u = (ab[0] / nl, ab[1] / nl, ab[2] / nl)
    v = _cross(n, u)
    # circumcentre: solve for O in the plane with |O-A|==|O-B|==|O-C|.
    # O = A + s*u + t*v. From |O-A|^2 == |O-B|^2:  -2*B.A' + |B|^2 ... in 2D
    # in-plane coords (a=0), b=(L,0), c=(cx,cy): O=(L/2, (cx^2+cy^2-2*cx*L/2)/(2*cy))
    L = _length(ab)
    cx = dot(ac, u)
    cy = dot(ac, v)
    if abs(cy) < 1e-9:
        return []                       # degenerate in-plane
    ox = L / 2.0
    oy = (cx * cx + cy * cy - 2.0 * cx * (L / 2.0)) / (2.0 * cy)
    O = (start[0] + u[0] * ox + v[0] * oy,
         start[1] + u[1] * ox + v[1] * oy,
         start[2] + u[2] * ox + v[2] * oy)
    R = _length(_sub(start, O))
    if R < 1e-9:
        return []

    def _angle(p):
        d = _sub(p, O)
        return math.atan2(dot(d, v), dot(d, u))

    a0, a1, ac_ = _angle(start), _angle(end), _angle(through)

    def _in_sweep(x, s, e):
        sweep = (e - s) % (2.0 * math.pi)
        pos = (x - s) % (2.0 * math.pi)
        return pos <= sweep + 1e-9

    # choose the sweep from a0 to a1 that contains the through-point's angle
    if _in_sweep(ac_, a0, a1):
        a_start, a_end, step_dir = a0, a1, +1
    else:
        a_start, a_end, step_dir = a0, a1, -1

    def _at(angle):
        c, s = math.cos(angle) * R, math.sin(angle) * R
        return (O[0] + u[0] * c + v[0] * s,
                O[1] + u[1] * c + v[1] * s,
                O[2] + u[2] * c + v[2] * s)

    # normalise the sweep to a positive fraction of the circle, then sample it
    sweep = (a_end - a_start) % (2.0 * math.pi)
    if step_dir < 0:
        sweep = 2.0 * math.pi - sweep
        sweep = sweep % (2.0 * math.pi)
    pts = []
    for i in range(segments + 1):
        t = i / segments * sweep
        pts.append(_at(a_start + step_dir * t))
    return pts


def preview_arc(args, cursor, plane):
    """`Arc`: the two fixed endpoints plus the live point on the arc.

    One point (the start) -- a rubber band to the cursor. Two points (start +
    end) -- both endpoints and the live through-point. Three points -- the
    actual arc through all three, sampled by _arc_polyline (the committed curve
    is bridge.make_arc; the preview is its faithful wireframe).
    """
    pts = points_only(args)
    if not pts:
        return []
    if len(pts) == 1:
        return [[pts[0], cursor]]
    if len(pts) == 2:
        return [[pts[0], pts[1]], [pts[1], cursor]]
    arc = _arc_polyline(pts[0], pts[1], pts[2])
    if arc:
        return [arc]
    # degenerate (collinear): show the three points so the user sees the problem
    return [[pts[0], pts[1], pts[2]]]


def _taper_profile(profile, height, draft_deg, flip):
    """APPROXIMATE (preview only): the top profile of a tapered extrusion.

    Each sample moves toward (positive draft) or away from (FlipAngle) the profile's centroid
    by |height| * tan(draft). That is exact for a circle -- the committed shape is a cone
    frustum -- and close for other profiles, whose committed top is a true offset curve built
    by OCCT MakeDraft. Inward-for-positive matches `occt_backend.make_extrusion_ex`.
    """
    inset = abs(height) * math.tan(math.radians(draft_deg))
    if flip:
        inset = -inset
    n = len(profile)
    closed = n > 2 and _length(_sub(profile[0], profile[-1])) < 1e-9
    pts = profile[:-1] if closed else profile
    c = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
    out = []
    for p in profile:
        v = _sub(c, p)
        d = _length(v)
        if d < 1e-12:
            out.append(p)
            continue
        out.append((p[0] + v[0] / d * inset, p[1] + v[1] / d * inset,
                    p[2] + v[2] / d * inset))
    return out


def preview_extrudecrv(args, cursor, plane, resolve, cmd=None):
    """`ExtrudeCrv`: the profile, a ghost of it at the picked height, and the side edges.

    With the running command `cmd` it also follows the options that change the shape:
    `Direction` (the two picked points replace the profile normal; while they are
    still being picked, only the direction rubber-band is drawn), `BothSides` (the ghost is
    drawn at -h and +h with the side edges spanning both, as the commit does from -direction
    to +direction), and `ExtrudeCrvTapered`'s DraftAngle / FlipAngle (see `_taper_profile`,
    which is approximate and says so).

    Accepting a picked point for the distance is only half of interactive extrusion;
    the preview also needs an entry here so geometry is drawn while the mouse moves.

    This is the first preview that needs DOCUMENT geometry: the command's first argument is a
    list of curve NAMES, not a point, so it cannot be drawn from `args` alone. Every selected
    profile is resolved and drawn consistently; the former scalar-name shape is still accepted
    for one-profile callers. This is why the function lives in `PREVIEWS_WITH_DOC`.

    The height follows the SAME rule as the committed geometry (MEASURED,
    commands/extrudecrv.htm): the signed projection of the cursor onto the profile plane's
    normal, measured from a point on the curve. Using anything else here would show a shape
    the user will not get -- the exact defect that `preview_curve` was created to fix when a
    cubic was previewed as its control polygon.
    """
    raw_names = args[0] if args else None
    if isinstance(raw_names, (list, tuple)):
        names = [name for name in raw_names if name]
    else:
        names = [raw_names] if raw_names else []
    if not names:
        return []
    try:
        from . import bridge
    except ImportError:          # loaded standalone (tests / probes)
        import bridge

    geoms = []
    profiles = []
    for name in names:
        geom = resolve(name)
        if geom is None:
            continue
        # MEASURED: `bridge.sample_curve` returns rhino3dm `Point3d` objects, which are NOT
        # subscriptable -- everything else in this module works on plain (x, y, z) tuples, so
        # they are converted here rather than making the vector helpers polymorphic.
        sampled = bridge.sample_curve(geom, 96)
        if sampled and len(sampled) >= 2:
            geoms.append(geom)
            profiles.append([(p.X, p.Y, p.Z) for p in sampled])
    if not profiles:
        return []

    opt = getattr(cmd, "opt", None) or {}
    if getattr(cmd, "_dir_mode", False):
        if len(args) < 3:
            # Still picking the direction: every profile, plus base -> cursor once the base
            # is in. Do not hide inner/disjoint loops while the option is being picked.
            out = [list(profile) for profile in profiles]
            if len(args) == 2 and args[1] is not None:
                out.append([tuple(args[1]), cursor])
            return out
        direction = cmd._picked_direction()
        if direction is None:
            return [list(profile) for profile in profiles]
    elif cmd is not None and hasattr(cmd, "default_direction"):
        # Use the command's own direction rule so the preview shows exactly the extrusion
        # a click or a typed distance makes. All profiles share it; unsupported noncoplanar
        # sets are still shown, then explicitly refused by the command.
        direction = cmd.default_direction(geoms[0], plane)
    else:
        direction = bridge.profile_normal(geoms[0])
    if direction is None:
        # MEASURED fallback: "If the curve is not planar, the default extrusion direction is
        # the z direction of the active CPlane."
        direction = plane.zaxis
    start = profiles[0][0]
    height = dot(_sub(cursor, start), direction)
    if abs(height) < 1e-12:
        return [list(profile) for profile in profiles]

    def shifted(pts, h):
        return [(p[0] + direction[0] * h, p[1] + direction[1] * h, p[2] + direction[2] * h)
                for p in pts]

    draft = cmd._draft() if cmd is not None and hasattr(cmd, "_draft") else None
    out = []
    for profile in profiles:
        top_base = profile
        if draft not in (None, 0, 0.0):
            top_base = _taper_profile(
                profile, height, float(draft), bool(opt.get("FlipAngle")))
        top = shifted(top_base, height)
        bottom = shifted(profile, -height) if opt.get("BothSides") else None
        out.extend([list(profile), top])
        if bottom is not None:
            out.append(bottom)
        # A few side edges rather than one per sample: enough to read as a solid, few enough
        # not to smother the shape in lines. Apply the same density to every selected profile.
        step = max(1, len(profile) // 8)
        for i in range(0, len(profile), step):
            out.append([bottom[i] if bottom is not None else profile[i], top[i]])
    return out


def preview_extrudesrf(args, cursor, plane, resolve, cmd=None):
    """`ExtrudeSrf`: the surface's edges, a ghost of them at the picked distance, and the
    straight paths its corners trace -- "creates a solid by tracing the path of the surface
    edges in a straight line" (MEASURED, commands/extrudesrf.htm).

    The distance follows the same rule as the committed geometry (`ExtrudeSrfCmd._parse_distance`): the
    signed projection of the cursor on the extrusion direction from the surface's first
    vertex. Edges come from `bridge.subobject_parts`, i.e. the exact topology, never a
    display proxy's triangles. BothSides spans -d..+d as the commit does.
    """
    geom = resolve(args[0] if args else None)
    if geom is None or not hasattr(geom, "Vertices") or len(geom.Vertices) == 0:
        return []
    try:
        from . import bridge
    except ImportError:          # loaded directly, without a package
        import bridge
    parts = bridge.subobject_parts(geom)
    if not parts or not parts.get("edges"):
        return []
    edges = [list(pl) for _i, pl in parts["edges"] if len(pl) >= 2]
    corners = [p for _i, p in parts.get("vertices", [])]
    fn = getattr(cmd, "direction_for", None)
    direction = fn(geom, plane) if callable(fn) else tuple(plane.zaxis)
    v = geom.Vertices[0].Location
    dist = dot(_sub(cursor, (v.X, v.Y, v.Z)), direction)
    if abs(dist) < 1e-12:
        return edges
    both = bool((getattr(cmd, "opt", None) or {}).get("BothSides"))
    lo = -dist if both else 0.0

    def shifted(poly, t):
        return [(p[0] + direction[0] * t, p[1] + direction[1] * t, p[2] + direction[2] * t)
                for p in poly]

    out = list(edges) if not both else []
    if both:
        out += [shifted(e, lo) for e in edges]
    out += [shifted(e, dist) for e in edges]
    out += [[shifted([c], lo)[0], shifted([c], dist)[0]] for c in corners]
    return out


# ---------------------------------------------------------------------------
# Previews for the cursor-driven Angle, Picture, and Revolve commands.
# ---------------------------------------------------------------------------

def preview_angle(args, cursor, plane):
    """`Angle`: the two imaginary lines, the unfinished one banded to the cursor.

    MEASURED (commands/angle.htm): "Pick the start of an imaginary angle line.
    Pick the end. Pick the start of a second. Pick the end." -- four POINT picks
    (commands.py: AngleCmd.steps), two lines the angle is between. The preview
    shows the finished first line as soon as it exists, and rubber-bands the
    line currently being aimed from its START: that is the pick the cursor
    completes, so the band's moving end is the answer the command is about.

    The TwoObjects mode (two line/surface objects) has no point picks, and
    `points_only` returns nothing for a list of names -- it previews nothing,
    which is the honest state: the preview would be the objects themselves,
    which `segments()` is not handed.
    """
    pts = points_only(args)
    if not pts:
        return []
    if len(pts) == 1:
        return [[pts[0], cursor]]
    lines = [[pts[0], pts[1]]]              # the finished first line
    if len(pts) == 4:                       # both lines fixed -- the command is done
        return lines + [[pts[2], pts[3]]]
    # rubber band from the START of the line currently being aimed (spec):
    # after two picks that is pts[0] (the first line's start doubles as the
    # second line's start), after three it is pts[2] -- never pts[-1], which
    # after two picks would be the first line's END
    anchor = pts[0] if len(pts) == 2 else pts[-1]
    return lines + [[anchor, cursor]]


def preview_picture(args, cursor, plane):
    """`Picture`: the defining rectangle, from the first corner to the cursor.

    MEASURED (commands/picture.htm): "Select image file. Pick first corner.
    Pick other corner or type length. The defining rectangle retains the same
    aspect (length to width) ratio as the image." The first step is TEXT (the
    file name), so `points_only` skips it and the first point IS the corner.

    Deliberately NOT aliased to `preview_rectangle` even though the shape code
    is the same: the committed picture does NOT have the picked rectangle's
    aspect -- `picture.fit_size` grows it until it reaches the corner, so the
    rectangle as picked is the honest preview of what the corner MEANS, and
    `Rectangle`'s docstring says something different about its own corners.
    """
    pts = points_only(args)
    if not pts:
        return []
    opposite = pts[1] if len(pts) >= 2 else cursor
    return [rectangle_polyline(plane, pts[0], opposite)]


def preview_revolve(args, cursor, plane):
    """`Revolve`: the axis, from axis point 1 to the cursor while it is aimed.

    MEASURED (commands/revolve.htm): "Select curves. Pick start of revolve
    axis. Pick end of revolve axis." The curve name (first step) and the angle
    number (last step) are not points, so `points_only` leaves exactly the two
    axis points -- and the axis IS the thing the curve revolves about, so a
    segment from point 1 to the cursor is the whole preview.

    WHAT THIS IS NOT, the same deliberate gap as `preview_mirror`: the reference CAD application also
    ghosts the revolved surface as the cursor moves. That needs the curve's
    geometry, which `segments(name, args, cursor, plane)` is not handed; the
    axis is the part that can be drawn without it, and the shortfall is
    recorded rather than implied.
    """
    pts = points_only(args)
    if not pts:
        return []
    if len(pts) == 1:
        return [[pts[0], cursor]]
    return [[pts[0], pts[1]]]


#: command name (lowercase) -> preview function. This is the table to extend.
PREVIEWS = {
    "line": preview_line,
    "polyline": preview_polyline,
    # These two used to map to `preview_polyline`, showing the control polygon while
    # `bridge.make_polyline(args, degree=3)` committed a smooth cubic. `InterpCrv`
    # shares the preview because it uses the same builder.
    "curve": preview_curve,
    "interpcrv": preview_curve,
    "circle": preview_circle,
    "box": preview_box,
    "sphere": preview_sphere,
    "cylinder": preview_cylinder,
    "cone": preview_cone,
    "rectangle": preview_rectangle,
    "ellipse": preview_ellipse,
    "polygon": preview_polygon,
    "arc": preview_arc,
    "distance": preview_distance,
    "mirror": preview_mirror,
    # Move/Copy are two-point commands, so the from-point-to-cursor
    # vector is a real preview (it was a documented gap while they took a delta).
    "move": preview_move,
    "copy": preview_move,
    # Additional cursor-driven command previews.
    "angle": preview_angle,
    "picture": preview_picture,
    "revolve": preview_revolve,
}


#: Previews that need DOCUMENT geometry, not just the picked args. Kept as a separate table
#: so the twenty existing `(args, cursor, plane)` functions keep their signature -- adding a
#: fourth parameter to all of them to serve one command would be the wrong trade.
PREVIEWS_WITH_DOC = {
    "extrudecrv": preview_extrudecrv,
    "extrudecrvtapered": preview_extrudecrv,
    "extrudesrf": preview_extrudesrf,
}


def segments(name, args, cursor, plane, resolve=None, cmd=None):
    """The wireframe to draw for command `name`. Always returns a list.

    `cmd` (the running command, optional) lets a document preview follow options that
    change the shape -- see `preview_extrudecrv`.

    Unknown commands, a missing cursor, or a command with no preview all yield
    ``[]`` -- "draw nothing" -- so adding previews can never break a command that
    does not have one.
    """
    if not name or cursor is None or plane is None:
        return []
    key = str(name).lower()
    fn = PREVIEWS.get(key)
    doc_fn = PREVIEWS_WITH_DOC.get(key)
    if fn is None and doc_fn is None:
        return []
    try:
        if doc_fn is not None:
            # No resolver supplied (a unit test, or a caller with no document) means there is
            # nothing to look the curve up in -- draw nothing rather than guess a shape.
            if resolve is None:
                return []
            out = doc_fn(args or [], tuple(cursor), plane, resolve, cmd=cmd)
        else:
            out = fn(args or [], tuple(cursor), plane)
    except Exception:  # noqa: BLE001
        # A preview is cosmetic: never let it break the command it is previewing.
        return []
    return [p for p in (out or []) if p and len(p) >= 2]


def has_preview(name):
    """True if `name` draws a preview at all (used by the modal to skip work)."""
    key = str(name).lower() if name else ""
    return bool(key) and (key in PREVIEWS or key in PREVIEWS_WITH_DOC)
