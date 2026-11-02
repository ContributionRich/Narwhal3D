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
NATIVE_COIL_PREVIEW_MIN_POINTS = 241


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


def preview_srfpt(args, cursor, plane):
    """`SrfPt` -- MEASURED (research_preview_14 P56): at corner 2 a rubber line from corner 1
    to the cursor; at corner 3 the triangle 1-2-cursor; at corner 4 the outline 1-2-3-cursor
    closed back to corner 1."""
    pts = points_only(args)[:3]
    if not pts:
        return []
    if len(pts) == 1:
        return [[pts[0], cursor]]
    return [pts + [cursor, pts[0]]]


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


def preview_curve(args, cursor, plane, cmd=None):
    """`Curve`/`InterpCrv`: the SMOOTH curve the picks define, not the chain.

    The cursor is treated as the next control point, so the curve flexes as you
    move -- which is what the reference CAD application shows. See `control_point_curve_polyline`.
    """
    pts = points_only(args)
    if not pts:
        return []
    degree = int(getattr(cmd, "opt", {}).get("Degree", "3"))
    smooth = control_point_curve_polyline(pts + [cursor], max_degree=degree)
    return [smooth] if smooth else []


def preview_interpcrv(args, cursor, plane):
    """`InterpCrv` -- MEASURED (research_preview_14 P18): one point gives a straight rubber
    line; from two points the curve passes THROUGH every placed point and on to the cursor.
    It is the commit's own interpolation (`bridge.make_interpolated_curve`) with the cursor
    as the next point -- `preview_curve` showed a control-point curve, which misses them."""
    pts = points_only(args)
    if not pts:
        return []
    if len(pts) == 1:
        return [[pts[0], tuple(cursor)]]
    try:
        from . import bridge
    except ImportError:          # loaded standalone (tests / probes)
        import bridge
    through = pts + [tuple(cursor)]
    if any(_length(_sub(a, b)) <= 1e-9 for a, b in zip(through, through[1:])):
        through = pts
    try:
        curve = bridge.make_interpolated_curve(through, degree=3)
    except Exception:  # noqa: BLE001
        return [pts + [tuple(cursor)]]
    samples = bridge.sample_curve(curve, 24 * (len(through) - 1))
    return [[(p.X, p.Y, p.Z) for p in samples]] if samples else []


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


def preview_plane(args, cursor, plane):
    """`Plane`: the live rectangle -- Box's base, including the typed-length route."""
    segs = preview_box(args[:3] if len(args) >= 3 and not _is_point(args[1]) else args[:2],
                       cursor, plane)
    return segs[:1]


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
    c1 = pts[0]
    if len(args) >= 2 and not _is_point(args[1]):
        # MEASURED (research_preview_14 P01): a typed LENGTH, then the Width step. While
        # the width is picked the cursor rides the far side (the command's axis
        # constraint), so it IS the opposite corner; afterwards it is length x width.
        length = float(args[1])
        if len(args) == 2:
            return [rectangle_polyline(plane, c1, cursor)]
        width = float(args[2])
        corner = tuple(c1[k] + length * plane.xaxis[k] + width * plane.yaxis[k]
                       for k in range(3))
        pts = [c1, corner]
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
    # MEASURED (research_preview_14 P14): at "End of second axis" the whole ellipse is live,
    # its second semi-axis perpendicular to the first with the cursor's distance from the
    # first axis, plus a construction line from the centre to the cursor.
    u = _sub(pts[1], center)
    w = _sub(pts[2] if len(pts) > 2 else cursor, center)
    uu = dot(u, u)
    k = dot(w, u) / uu if uu > 1e-24 else 0.0
    v = tuple(w[i] - k * u[i] for i in range(3))
    if len(pts) == 2 and _length(v) <= 1e-12:
        return [[center, pts[1]], [center, cursor]]
    out = []
    for i in range(CIRCLE_SEGMENTS + 1):
        a = 2.0 * math.pi * i / CIRCLE_SEGMENTS
        c, s = math.cos(a), math.sin(a)
        out.append((center[0] + u[0] * c + v[0] * s,
                    center[1] + u[1] * c + v[1] * s,
                    center[2] + u[2] * c + v[2] * s))
    if len(pts) == 2:
        return [out, [center, tuple(cursor)]]
    return [out]


def polygon_polyline(plane, center, radius, sides, inscribed=True, *,
                     start_angle=0.0, direction=1.0):
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
    # MEASURED: CreateCircumscribedPolygon starts at (r, r*tan(pi/n)),
    # not on +x like CreateInscribedPolygon. Edge supplies its own start/winding.
    start_angle += 0.0 if inscribed else math.pi / sides
    u, v = plane.xaxis, plane.yaxis
    pts = []
    for i in range(sides + 1):          # +1 closes the loop
        a = start_angle + direction * 2.0 * math.pi * i / sides
        c, s = math.cos(a) * corner_radius, math.sin(a) * corner_radius
        pts.append((center[0] + u[0] * c + v[0] * s,
                    center[1] + u[1] * c + v[1] * s,
                    center[2] + u[2] * c + v[2] * s))
    return pts


def _variant_preview(name, args, cursor, plane, cmd):
    """Previews of the measured command variants (research_preview_14 R10), or None when the
    running command is not in one. Display only."""
    pts = [tuple(a) for a in args if _is_point(a)]
    cur = tuple(cursor)
    if name == "circle" and getattr(cmd, "_vertical_circle", False) and pts:
        # a rubber circle in the vertical plane holding the CPlane normal and the cursor
        center = pts[0]
        n = tuple(plane.zaxis)
        d = _sub(cur, center)
        d = tuple(d[i] - dot(d, n) * n[i] for i in range(3))
        u = _unit3(d)
        if u is None:
            return []
        locked = getattr(cmd, "_locked_radius", None)
        r = float(locked) if locked is not None else _length(_sub(cur, center))
        return [_ring(center, u, n, r), [center, cur]]
    if name == "ellipse" and getattr(cmd, "_flow", None) in ("Diameter", "FromFoci") and pts:
        if len(pts) == 1:
            return [[pts[0], cur]]
        if cmd._flow == "Diameter":
            center = tuple((pts[0][i] + pts[1][i]) / 2 for i in range(3))
            a_end, w = pts[1], _sub(pts[2] if len(pts) > 2 else cur, center)
            u = _sub(a_end, center)
            uu = dot(u, u) or 1.0
            v = tuple(w[i] - dot(w, u) / uu * u[i] for i in range(3))
        else:
            f1, f2, on = pts[0], pts[1], (pts[2] if len(pts) > 2 else cur)
            center = tuple((f1[i] + f2[i]) / 2 for i in range(3))
            c = _length(_sub(f2, f1)) / 2
            a = (_length(_sub(on, f1)) + _length(_sub(on, f2))) / 2
            e = _unit3(_sub(f2, f1))
            if e is None or a <= c:
                return [[f1, f2]]
            n = _unit3(_cross3(_sub(f2, f1), _sub(on, f1))) or tuple(plane.zaxis)
            u = tuple(a * e[i] for i in range(3))
            v = tuple(math.sqrt(a * a - c * c) * x for x in _cross3(n, e))
        ring = [tuple(center[i] + u[i] * math.cos(t) + v[i] * math.sin(t) for i in range(3))
                for t in (2 * math.pi * k / CIRCLE_SEGMENTS for k in range(CIRCLE_SEGMENTS + 1))]
        return [ring]
    if name in ("rectangle", "plane") and getattr(cmd, "_vertical_rect", False) and pts:
        if len(pts) == 1:
            return [[pts[0], cur]]                     # MEASURED: a rubber line along the edge
        a, b = pts[0], pts[1]
        n = tuple(plane.zaxis)
        if len(args) >= 3 and not _is_point(args[2]):
            h = abs(float(args[2])) * (-1.0 if dot(_sub(cur, a), n) < 0 else 1.0)
        else:
            h = dot(_sub(cur, a), n)
        top = [tuple(p[i] + h * n[i] for i in range(3)) for p in (b, a)]
        return [[a, b, top[0], top[1], a]]
    if name == "polygon" and getattr(cmd, "_star", False) and pts:
        if len(pts) == 1:
            outer = cur
        else:
            outer = pts[1]
        n = int(getattr(cmd, "_num_sides", 5))
        cu, cv, _ = plane.from_world(pts[0])
        pu, pv, _ = plane.from_world(outer)
        r1 = math.hypot(pu - cu, pv - cv)
        if r1 <= 1e-12:
            return []
        a0 = math.atan2(pv - cv, pu - cu)
        if len(pts) == 1:                                # MEASURED: the live polygon first
            ring = [plane.to_world(cu + r1 * math.cos(a0 + 2 * math.pi * k / n),
                                   cv + r1 * math.sin(a0 + 2 * math.pi * k / n), 0.0)
                    for k in range(n + 1)]
            return [ring]
        cw = plane.from_world(cur)
        r2 = math.hypot(cw[0] - cu, cw[1] - cv)
        ring = [plane.to_world(cu + (r1 if k % 2 == 0 else r2) * math.cos(a0 + math.pi * k / n),
                               cv + (r1 if k % 2 == 0 else r2) * math.sin(a0 + math.pi * k / n),
                               0.0) for k in range(2 * n + 1)]
        return [ring]
    return None


def preview_polygon(args, cursor, plane, cmd=None):
    """Display the running Polygon's draft count/mode, never build kernel geometry.

    Without a command, retain the legacy [center, radius, sides] helper contract
    (a circle until sides are supplied). The live command no longer collects a
    third argument. Python only adapts command options/frames to the existing
    display helper; no new geometry producer or per-frame kernel calls.
    """
    if not args:
        return []
    center = args[0]
    sides = (getattr(cmd, 'opt', {}).get('NumSides') if cmd is not None else
             args[2] if len(args) >= 3 and not _is_point(args[2]) else None)
    if cmd is not None:
        # OURS: same interactive allocation ceiling as PolygonCmd; malformed
        # external helper calls must not allocate an unbounded draw buffer.
        count = float(sides)
        if not math.isfinite(count) or not count.is_integer() or not 3 <= count <= 10_000:
            return []
        sides = int(count)
        if getattr(cmd, '_edge', False):
            end = args[1] if len(args) >= 2 else cursor
            au, av, aw = plane.from_world(center)
            bu, bv, _ = plane.from_world(end)
            du, dv = bu - au, bv - av
            length = math.hypot(du, dv)
            if length <= 1e-12:
                return []
            direction = -1.0 if cmd.opt.get('Flip') else 1.0
            # DERIVED: edge midpoint + signed left-normal apothem. Retain the
            # start's elevation, matching PolygonCmd._edge_polygon's projection.
            offset = direction / (2.0 * math.tan(math.pi / sides))
            cu, cv = (au + bu) / 2.0 - dv * offset, (av + bv) / 2.0 + du * offset
            poly = polygon_polyline(plane, plane.to_world(cu, cv, aw),
                length / (2.0 * math.sin(math.pi / sides)), sides,
                start_angle=math.atan2(av - cv, au - cu), direction=direction)
            return [poly] if poly else []
    start_angle = 0.0
    if cmd is not None:
        corner = args[1] if len(args) >= 2 else cursor
        cu, cv, _ = plane.from_world(center)
        pu, pv, _ = plane.from_world(corner)
        du, dv = pu - cu, pv - cv
        radius = math.hypot(du, dv)
        start_angle = math.atan2(dv, du)
    else:
        # Legacy commandless helpers still accept scalar radius/sides.
        radius = args[1] if len(args) >= 2 and not _is_point(args[1]) else None
        if radius is None:
            radius = _length(_sub(cursor, center))
    if not math.isfinite(radius) or radius <= 0:
        return []
    if sides is None or int(sides) < 3:
        return [circle_polyline(plane, center, radius)]
    poly = polygon_polyline(plane, center, radius, int(sides),
                            inscribed=getattr(cmd, '_inscribed', True),
                            start_angle=start_angle)
    return [poly] if poly else []


def preview_pyramid(args, cursor, plane, cmd=None):
    """`Pyramid`: display-only wireframe, never kernel geometry.

    MEASURED (research_preview_14 P02): at "Corner of pyramid" the base polygon is drawn
    live with a corner (inscribed) on the cursor -- it grows and turns with the mouse; a
    typed radius (`cmd._locked_radius`) fixes the size while the cursor still turns it.
    At "End of pyramid" the whole wire pyramid is drawn, the apex on the CPlane normal
    through the centre at the cursor's height. The polygon follows `bridge.make_pyramid`'s
    rules (`polygon_polyline` mirrors them), so the preview and the result agree.
    """
    if not args or not _is_point(args[0]):
        return []
    center = args[0]
    try:
        sides = int(float(getattr(cmd, "opt", {}).get("NumSides", 5)))
    except (TypeError, ValueError):
        return []
    if not 3 <= sides <= 10_000:
        return []
    inscribed = bool(getattr(cmd, "inscribed", True))
    cu, cv, _ = plane.from_world(center)
    if len(args) == 1:
        pu, pv, _ = plane.from_world(cursor)
        du, dv = pu - cu, pv - cv
        locked = getattr(cmd, "_locked_radius", None)
        radius = float(locked) if locked else math.hypot(du, dv)
        angle = math.atan2(dv, du) if (du or dv) else 0.0
        poly = polygon_polyline(plane, center, radius, sides, inscribed=inscribed,
                                start_angle=angle)
        return [poly] if poly else []
    if _is_point(args[1]):
        return []
    radius = float(args[1])
    angle = 0.0
    corner = getattr(cmd, "_corner", None)
    if corner is not None:
        pu, pv, _ = plane.from_world(corner)
        if pu != cu or pv != cv:
            angle = math.atan2(pv - cv, pu - cu)
    base = polygon_polyline(plane, center, radius, sides, inscribed=inscribed,
                            start_angle=angle)
    if not base:
        return []
    if len(args) > 2:
        return [base]
    n = plane.zaxis
    height = sum((cursor[i] - center[i]) * n[i] for i in range(3))
    if abs(height) <= 1e-12:
        return [base]
    apex = tuple(center[i] + n[i] * height for i in range(3))
    return [base] + [[base[i], apex] for i in range(sides)]


def _ring(center, u, v, radius, segments=CIRCLE_SEGMENTS):
    """A closed circle of `radius` about `center` in the plane of unit vectors u, v."""
    if not radius > 0:
        return []
    return [tuple(center[k] + radius * (math.cos(a) * u[k] + math.sin(a) * v[k])
                  for k in range(3))
            for a in (2.0 * math.pi * i / segments for i in range(segments + 1))]


def _along(point, direction, length):
    return tuple(point[k] + direction[k] * length for k in range(3))


def _signed_height(cursor, base, plane):
    n = plane.zaxis
    return sum((cursor[k] - base[k]) * n[k] for k in range(3))


def _size_and_ring(args, cursor, plane):
    """The first radius step shared by Torus / Tube / TruncatedCone: a circle about the
    centre, its radius the cursor's distance from it (as the DISTANCE step measures)."""
    center = args[0]
    radius = float(args[1]) if len(args) >= 2 else _length(_sub(cursor, center))
    return center, radius, circle_polyline(plane, center, radius)


def _cardinals(ring):
    return [ring[i] for i in (0, CIRCLE_SEGMENTS // 4, CIRCLE_SEGMENTS // 2,
                              3 * CIRCLE_SEGMENTS // 4)]


def preview_torus(args, cursor, plane):
    """`Torus` -- MEASURED (research_preview_14 P06): at "Radius" a live circle of the
    cursor's distance; at "Second radius" the whole torus wire, the ring fixed at the
    picked radius and the tube following the cursor. The tube radius is measured from
    the ring exactly as `TorusCmd._parse_distance` does, so the preview is the result."""
    if not args or not _is_point(args[0]):
        return []
    center, major, ring = _size_and_ring(args, cursor, plane)
    if len(args) == 1 or not ring:
        return [ring] if ring else []
    z = plane.zaxis
    v = _sub(cursor, center)
    h = sum(v[k] * z[k] for k in range(3))
    rho = math.sqrt(max(0.0, sum(c * c for c in v) - h * h))
    minor = float(args[2]) if len(args) >= 3 else math.hypot(rho - major, h)
    if not minor > 0:
        return [ring]
    out = [ring, circle_polyline(plane, center, major + minor)]
    if major - minor > 0:
        out.append(circle_polyline(plane, center, major - minor))
    out += [circle_polyline(plane, _along(center, z, s * minor), major) for s in (1, -1)]
    for a in range(4):
        ang = a * math.pi / 2.0
        radial = tuple(math.cos(ang) * plane.xaxis[k] + math.sin(ang) * plane.yaxis[k]
                       for k in range(3))
        out.append(_ring(_along(center, radial, major), radial, z, minor))
    return [p for p in out if p]


def preview_tube(args, cursor, plane):
    """`Tube` -- MEASURED (research_preview_14 P07): the outer circle follows the cursor,
    then the outer stays and a live inner circle follows it, then at "End of tube" the
    height follows the cursor along the CPlane normal. The full wire at the end step
    (both walls at the top, four edges) is OURS, matching `preview_cylinder`."""
    if not args or not _is_point(args[0]):
        return []
    center, r1, outer = _size_and_ring(args, cursor, plane)
    if len(args) == 1 or not outer:
        return [outer] if outer else []
    r2 = float(args[2]) if len(args) >= 3 else _length(_sub(cursor, center))
    inner = circle_polyline(plane, center, r2)
    rings = [p for p in (outer, inner) if p]
    if len(args) == 2:
        return rings
    h = _signed_height(cursor, center, plane)
    if abs(h) <= 1e-12:
        return rings
    top = _along(center, plane.zaxis, h)
    tops = [p for p in (circle_polyline(plane, top, r1), circle_polyline(plane, top, r2)) if p]
    edges = [[a, b] for ring, lid in zip(rings, tops)
             for a, b in zip(_cardinals(ring), _cardinals(lid))]
    return rings + tops + edges


def preview_truncatedcone(args, cursor, plane):
    """`TruncatedCone` -- MEASURED (research_preview_14 P08): a live base circle; at "Top
    of truncated cone" a live cone wire to an apex at the cursor's height on the CPlane
    normal; at "Radius at top" the top circle (radius from the top centre, as the command
    measures it) and the frustum edges."""
    if not args or not _is_point(args[0]):
        return []
    center, r1, base = _size_and_ring(args, cursor, plane)
    if len(args) == 1 or not base:
        return [base] if base else []
    z = plane.zaxis
    h = float(args[2]) if len(args) >= 3 else _signed_height(cursor, center, plane)
    if abs(h) <= 1e-12:
        return [base]
    top_center = _along(center, z, h)
    if len(args) == 2:
        return [base] + [[p, top_center] for p in _cardinals(base)]
    r2 = _length(_sub(cursor, top_center))
    top = circle_polyline(plane, top_center, r2)
    if not top:
        return [base] + [[p, top_center] for p in _cardinals(base)]
    return [base, top] + [[a, b] for a, b in zip(_cardinals(base), _cardinals(top))]


def preview_ellipsoid(args, cursor, plane):
    """`Ellipsoid` -- MEASURED (research_preview_14 P09): a rubber line from the centre to
    the cursor at the first axis; a live ellipse at the second; the whole wire at the
    third. The frame is `EllipsoidCmd._make`'s: the first axis flattened into the CPlane,
    the second perpendicular to it in the CPlane, the third along the normal; the second
    and third sizes are the cursor's distance from the centre, as the command measures."""
    if not args or not _is_point(args[0]):
        return []
    center = args[0]
    if len(args) == 1:
        return [[center, tuple(cursor)]]
    z = plane.zaxis
    v = _sub(args[1], center)
    k = sum(v[i] * z[i] for i in range(3))
    v = tuple(v[i] - k * z[i] for i in range(3))
    a = _length(v)
    if not a > 0:
        return []
    x = tuple(c / a for c in v)
    y = (z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0])
    b = float(args[2]) if len(args) >= 3 else _length(_sub(cursor, center))
    if not b > 0:
        return [[center, args[1]]]

    def ellipse(u, ru, w, rw):
        return [tuple(center[i] + ru * math.cos(t) * u[i] + rw * math.sin(t) * w[i]
                      for i in range(3))
                for t in (2.0 * math.pi * j / CIRCLE_SEGMENTS
                          for j in range(CIRCLE_SEGMENTS + 1))]

    equator = ellipse(x, a, y, b)
    if len(args) == 2:
        return [equator]
    c = _length(_sub(cursor, center))
    if not c > 0:
        return [equator]
    return [equator, ellipse(x, a, z, c), ellipse(y, b, z, c)]


def preview_arraypolar(args, cursor, plane):
    """`ArrayPolar` -- MEASURED (research_preview_14 P29): at "Angle to fill" a live circle
    about the centre through the cursor, and a guide line from the centre to the cursor.
    (Where the copies are drawn was unclear in the evidence, so none are drawn.)"""
    if len(args) != 3 or not _is_point(args[1]):
        return []
    center = args[1]
    radius = _length(_sub(cursor, center))
    circle = circle_polyline(plane, center, radius)
    return [circle, [center, tuple(cursor)]] if circle else []


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
    # MEASURED (research_preview_14 P05): at the radius prompt a live WIRE sphere -- the
    # equator through the cursor, latitudes at +-45 degrees (one inner circle at 0.71 r seen
    # from Top) and four meridian great circles 45 degrees apart (four diameters in Top).
    x, y, n = tuple(plane.xaxis), tuple(plane.yaxis), tuple(plane.zaxis)
    out = [circle]
    s = math.sqrt(0.5)
    for sign in (1.0, -1.0):
        c = tuple(center[i] + sign * radius * s * n[i] for i in range(3))
        out.append(_ring(c, x, y, radius * s))
    for k in range(4):
        a = math.pi * k / 4
        u = tuple(math.cos(a) * x[i] + math.sin(a) * y[i] for i in range(3))
        out.append(_ring(center, u, n, radius))
    return out


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
    # (u was once ab / |ab x ac|, which is a unit vector only by coincidence)
    L = _length(ab)
    if L < 1e-12:
        return []
    u = (ab[0] / L, ab[1] / L, ab[2] / L)
    v = _cross(n, u)
    # circumcentre: solve for O in the plane with |O-A|==|O-B|==|O-C|.
    # O = A + s*u + t*v. From |O-A|^2 == |O-B|^2:  -2*B.A' + |B|^2 ... in 2D
    # in-plane coords (a=0), b=(L,0), c=(cx,cy): O=(L/2, (cx^2+cy^2-2*cx*L/2)/(2*cy))
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


def _center_arc_preview(center, start, cursor, plane):
    """The counter-clockwise arc about `center` from `start` to the cursor's angle, in the
    CPlane (as `ArcCmd._center_arc` builds it), plus the two construction lines."""
    z = tuple(plane.zaxis)
    d0 = _sub(start, center)
    d0 = tuple(d0[i] - dot(d0, z) * z[i] for i in range(3))
    r = _length(d0)
    if r <= 1e-12:
        return []
    u = tuple(c / r for c in d0)
    v = _cross3(z, u)
    de = _sub(cursor, center)
    phi = math.atan2(dot(de, v), dot(de, u)) % (2 * math.pi)
    if phi <= 1e-9:
        return [[center, start]]
    n = max(2, int(CIRCLE_SEGMENTS * phi / (2 * math.pi)) + 1)
    arc = [tuple(center[i] + r * (math.cos(phi * k / n) * u[i] + math.sin(phi * k / n) * v[i])
                 for i in range(3)) for k in range(n + 1)]
    return [arc, [center, start], [center, tuple(cursor)]]


def preview_arc(args, cursor, plane, cmd=None):
    """`Arc`, by route.

    Centre route (the default, MEASURED research_preview_14 P13 item 8): after the centre a
    radius line to the cursor (OURS: that prompt was not captured); after the start, a
    rubber arc from the start to the cursor's angle with construction lines from the centre.
    StartPoint route (MEASURED items 4-6): a straight rubber line at "End of arc", then the
    arc through the start, the end and the cursor at "Point on arc". Three points -- the
    actual arc through all three (the committed curve is bridge.make_arc).
    """
    pts = points_only(args)
    if not pts:
        return []
    flow = getattr(cmd, "_flow", "StartPoint") if cmd is not None else "StartPoint"
    if flow == "Center" and len(pts) <= 2:
        if len(pts) == 1:
            return [[pts[0], cursor]]
        return _center_arc_preview(pts[0], pts[1], cursor, plane)
    if flow in ("Tangent", "Direction") and len(pts) == 2:
        return [[pts[0], pts[1]], [pts[1], cursor]]
    if len(pts) == 1:
        return [[pts[0], cursor]]
    if len(pts) == 2:
        live = _arc_polyline(pts[0], pts[1], tuple(cursor))
        return [live] if live else [[pts[0], pts[1]], [pts[1], cursor]]
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


def preview_revolve(args, cursor, plane, resolve=None, cmd=None):
    """`Revolve` -- MEASURED (research_preview_14 P54): nothing at the axis start; at the
    axis end a rubber line from the start to the cursor; at both angle prompts the axis
    stays and a circle about the axis passes through the cursor (edge-on in Top it reads as
    a line symmetric about the axis, the cursor's Y ignored). A guide runs from the axis to
    the cursor's projection. The swept surface itself was too faint to measure: not drawn.
    """
    pts = [a for a in args[1:3] if _is_point(a)]
    if not pts:
        return []
    if len(pts) == 1:
        return [[pts[0], cursor]]
    out = [[pts[0], pts[1]]]
    frame_fn = getattr(cmd, "angle_frame", None)
    frame = frame_fn(resolve, plane) if callable(frame_fn) and len(args) >= 3 else None
    if frame is None:
        return out
    # The circle lies in the plane through the axis START (the readout at cursor (2,2) in
    # Top was y 0 for an axis along Y): the cursor is projected into that plane.
    origin, axis, _ref = frame
    return out + _axis_circle(origin, axis, cursor)


def _axis_circle(origin, axis, cursor):
    """The angle guide of Revolve / Rotate / Rotate3D: a circle about the axis through
    `origin`, in the plane through `origin` perpendicular to the axis, passing through the
    cursor's projection, plus the radius line to that projection."""
    a = _unit3(axis)
    if a is None:
        return []
    v = _sub(cursor, origin)
    k = dot(v, a)
    radial = tuple(v[i] - k * a[i] for i in range(3))
    radius = _length(radial)
    if radius <= 1e-9:
        return []
    u = tuple(c / radius for c in radial)
    w = _cross3(a, u)
    return [_ring(tuple(origin), u, w, radius),
            [tuple(origin), tuple(origin[i] + radial[i] for i in range(3))]]


def preview_rotate(args, cursor, plane):
    """`Rotate` -- MEASURED (research_preview_14 P22): at "Angle or first reference point" a
    reference circle about the centre through the cursor and a line from the centre to the
    cursor; the selection does NOT move there. OURS: after a first reference point, the
    reference line and the line to the cursor (the live rotation is the transform ghost)."""
    pts = points_only(args)
    if not pts:
        return []
    if len(pts) == 1:
        return _axis_circle(pts[0], tuple(plane.zaxis), cursor)
    return [[pts[0], pts[1]], [pts[0], tuple(cursor)]]


def preview_scale(args, cursor, plane):
    """`Scale` / `Scale1D` / `Scale2D` -- MEASURED (research_preview_14 P24-P26): at the factor
    prompt only a line from the base point to the cursor (no live copy); the live scaled copy
    at "Second reference point" / "Scale direction" is the transform ghost, with the same line."""
    pts = points_only(args)
    if not pts:
        return []
    return [[pts[0], tuple(cursor)]]


def preview_rotate3d(args, cursor, plane):
    """`Rotate3D` -- MEASURED (research_preview_14 P23): at "End of rotation axis" a rubber
    axis from its start to the cursor; at the angle prompt the axis-perpendicular circle
    through the cursor (edge-on in Top: a line from the axis start to the cursor and a
    reference line opposite it); no live copy there."""
    pts = points_only(args)
    if not pts:
        return []
    if len(pts) == 1:
        return [[pts[0], tuple(cursor)]]
    if len(pts) == 2:
        return [[pts[0], pts[1]]] + _axis_circle(pts[0], _sub(pts[1], pts[0]), cursor)
    return [[pts[0], pts[1]], [pts[0], pts[2]], [pts[0], tuple(cursor)]]


def _unit3(value):
    length = _length(value)
    if length <= 1e-12:
        return None
    return tuple(component / length for component in value)


def _cross3(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _coil_axis(args, cursor, plane, cmd):
    if not args or not _is_point(args[0]):
        return None
    start = tuple(args[0])
    if len(args) < 2:
        return start, tuple(cursor), False
    if getattr(cmd, "_vertical", False) and isinstance(args[1], (int, float)):
        normal = tuple(plane.zaxis)
        end = tuple(start[i] + float(args[1]) * normal[i] for i in range(3))
    elif _is_point(args[1]):
        end = tuple(args[1])
    else:
        return start, tuple(cursor), False
    return start, end, True


def preview_coil(args, cursor, plane, cmd=None, spiral=False):
    """Display-only analytic sampling for Helix/Spiral command stages."""
    if cmd is None:
        return []
    axis_data = _coil_axis(args, cursor, plane, cmd)
    if axis_data is None:
        return []
    start, end, fixed_axis = axis_data
    if not fixed_axis:
        return [[start, end]] if _length(_sub(end, start)) > 1e-12 else []
    axis_vector = _sub(end, start)
    axis_length = _length(axis_vector)
    axis = _unit3(axis_vector)
    if axis is None:
        return []
    radial = tuple(plane.xaxis)
    radial = tuple(radial[i] - axis[i] * dot(radial, axis) for i in range(3))
    x = _unit3(radial)
    if x is None:
        radial = tuple(plane.yaxis)
        x = _unit3(tuple(radial[i] - axis[i] * dot(radial, axis) for i in range(3)))
    if x is None:
        return [[start, end]]
    y = _unit3(_cross3(axis, x))
    if y is None:
        return [[start, end]]

    divisor = 2.0 if getattr(cmd, "size_mode", "radius") == "diameter" else 1.0
    # MEASURED (research_preview_14 P40 Helix, P55 Spiral): a radius typed at the first radius
    # prompt stays LOCKED -- the coil keeps that radius while the cursor still turns it.
    locked = getattr(cmd, "_locked_radius", None) if len(args) == 2 else None
    if spiral:
        if len(args) < 3 or not isinstance(args[2], (int, float)):
            radius0 = (float(locked) if locked is not None
                       else _length(_sub(cursor, start))) / divisor
            radius1 = radius0
        else:
            radius0 = float(args[2]) / divisor
            radius1 = (_length(_sub(cursor, end)) / divisor
                       if len(args) < 4 else float(args[3]) / divisor)
    else:
        radius0 = radius1 = (float(locked) if locked is not None
                             else _length(_sub(cursor, start))) / divisor
    if radius0 <= 0.0 or radius1 <= 0.0:
        return [[start, end]]

    mode = getattr(cmd, "_coil_mode", "Turns")
    if mode == "Pitch" and not getattr(cmd, "_flat", False):
        pitch = float(getattr(cmd, "opt", {}).get("Pitch", 1.0))
        turns = axis_length / pitch if pitch > 0.0 else 0.0
    else:
        turns = float(getattr(cmd, "opt", {}).get("Turns", 10.0))
    if turns <= 0.0:
        return [[start, end]]
    total = (-1.0 if getattr(cmd, "_reverse_twist", False) else 1.0) * 2.0 * math.pi * turns
    count = max(16, min(2048, int(math.ceil(turns * 24.0))))
    flat = bool(getattr(cmd, "_flat", False))
    points = None
    if count + 1 >= NATIVE_COIL_PREVIEW_MIN_POINTS:
        try:
            from . import native_coil
            points = native_coil.preview_points(
                start,end,x,radius0,radius1,turns,
                getattr(cmd,"_reverse_twist",False),flat,count+1)
        except Exception:  # noqa: BLE001 -- pure Python preview remains fallback
            points = None
    if points is None:
        points = []
        for index in range(count + 1):
            fraction = index / count
            theta = total * fraction
            radius = radius0 + (radius1 - radius0) * fraction
            axial = 0.0 if flat else axis_length * fraction
            points.append(tuple(start[k] + radius * (math.cos(theta) * x[k]
                                                       + math.sin(theta) * y[k])
                                + axial * axis[k] for k in range(3)))
    return [[start, end], points]


def preview_handlecurve(args, cursor, plane):
    values=[tuple(value) for value in (args or []) if _is_point(value)]
    if not values:return []
    guides=[values+[tuple(cursor)]]
    complete=values[:-1] if len(values)%2 else values
    if len(complete)<4:return guides
    anchors=complete[0::2];handles=complete[1::2];curve=[]
    for i in range(len(anchors)-1):
        a0,a1=anchors[i],anchors[i+1];h0,h1=handles[i],handles[i+1]
        incoming=tuple(2*a1[k]-h1[k] for k in range(3))

        for sample in range(17):
            if i and sample==0:continue
            t=sample/16;s=1-t
            curve.append(tuple(s**3*a0[k]+3*s*s*t*h0[k]+3*s*t*t*incoming[k]+t**3*a1[k]
                               for k in range(3)))
    return guides+([curve] if curve else [])


def _quadratic_preview(p0, p1, p2):
    points = []
    for index in range(65):
        t = index / 64.0
        s = 1.0 - t
        points.append(tuple(s * s * p0[k] + 2.0 * s * t * p1[k] + t * t * p2[k]
                            for k in range(3)))
    return points


def preview_catenary(args, cursor, plane, cmd=None):
    points = [tuple(value) for value in (args or []) if _is_point(value)]
    if not points:
        return []
    if len(points) < 3:
        return [points + [tuple(cursor)]]
    start, end, axis_point = points[:3]
    through = tuple(cursor)
    middle = tuple((start[k] + end[k]) * 0.5 for k in range(3))
    gravity = _unit3(_sub(axis_point, middle))
    if gravity is None:
        return [[start, end, axis_point]]
    chord = _sub(end, start)
    vertical = dot(chord, gravity)
    horizontal_vector = _sub(chord, tuple(vertical * gravity[k] for k in range(3)))
    length = _length(horizontal_vector)
    horizontal = _unit3(horizontal_vector)
    if horizontal is None or abs(vertical) > 1e-8:
        return [[start, end, through]]
    through_delta = _sub(through, middle)
    sag = dot(through_delta, gravity)
    if abs(dot(through_delta, horizontal)) > 1e-8 or sag <= 0.0:
        return [[start, end, through]]
    half = length * 0.5
    def value(a):
        z = half / a
        return math.inf if z > 700 else a * (math.cosh(z) - 1.0)
    low, high = half / 700.0, max(half, sag)
    while value(high) > sag:
        high *= 2.0
    for _ in range(80):
        mid = (low + high) * 0.5
        if value(mid) > sag:
            low = mid
        else:
            high = mid
    a = (low + high) * 0.5
    curve = []
    for index in range(65):
        x = -half + length * index / 64.0
        displacement = a * (math.cosh(half/a) - math.cosh(x/a))
        curve.append(tuple(middle[k] + x * horizontal[k] + displacement * gravity[k]
                           for k in range(3)))
    return [curve]


def preview_hyperbola(args, cursor, plane, cmd=None):
    points = [tuple(value) for value in (args or []) if _is_point(value)]
    if not points:
        return []
    if len(points) < 2:
        return [points + [tuple(cursor)]]
    center, focus = points[:2]
    end = tuple(cursor)
    axis = _unit3(_sub(focus, center))
    if axis is None:
        return [[center, focus, end]]
    c = _length(_sub(focus, center))
    delta = _sub(end, center)
    x = dot(delta, axis)
    side_vector = _sub(delta, tuple(x * axis[k] for k in range(3)))
    y = _length(side_vector)
    side = _unit3(side_vector)
    total = x * x + y * y + c * c
    discriminant = total * total - 4.0 * x * x * c * c
    if side is None or discriminant <= 0.0:
        return [[center, focus, end]]
    a2 = (total - math.sqrt(discriminant)) / 2.0
    b2 = c * c - a2
    if a2 <= 0.0 or b2 <= 0.0:
        return [[center, focus, end]]
    a, b = math.sqrt(a2), math.sqrt(b2)
    u_end = math.acosh(max(1.0, x / a))
    branch = []
    # The created branch is symmetric about the axis (MEASURED, Research13 #71), so
    # the preview spans the mirrored end through the vertex to the cursor.
    for index in range(65):
        u = u_end * (2.0 * index / 64.0 - 1.0)
        branch.append(tuple(center[k] + a * math.cosh(u) * axis[k]
                            + b * math.sinh(u) * side[k] for k in range(3)))
    out = [branch]
    if bool(getattr(cmd, "opt", {}).get("BothBranches")):
        out.append([tuple(2.0 * center[k] - point[k] for k in range(3))
                    for point in branch])
    if bool(getattr(cmd, "opt", {}).get("ShowAsymptotes")):
        extent = max(a, abs(x))
        for sign in (-1.0, 1.0):
            direction = tuple(axis[k] + sign * (b / a) * side[k] for k in range(3))
            out.append([tuple(center[k] - extent * direction[k] for k in range(3)),
                        tuple(center[k] + extent * direction[k] for k in range(3))])
    return out


def preview_parabola3pt(args, cursor, plane, cmd=None):
    points = [tuple(value) for value in (args or []) if _is_point(value)]
    if len(points) < 2:
        return [points + [tuple(cursor)]] if points else []
    opt = getattr(cmd, "opt", {}) or {}
    if opt.get("Mode", "ThroughPoint") == "ThroughPoint":
        order = opt.get("PickOrder", "Default")
        if len(points) >= 3:
            # MEASURED (P50, R5): at "Parabola direction" the exact parabola for the cursor as
            # the direction, and rubber lines from the cursor to the start and the end.
            if order == "EndsFirst":
                start, end, through = points[:3]
            elif order == "EndsLast":
                through, start, end = points[:3]
            else:
                start, through, end = points[:3]
            try:
                from . import conics
            except ImportError:          # loaded standalone (tests / probes)
                import conics
            out = [[tuple(cursor), start], [tuple(cursor), end]]
            try:
                curve, _focus = conics.parabola_through_direction(start, through, end, cursor)
            except ValueError:
                return out
            d = curve.Domain
            return [[(p.X, p.Y, p.Z) for p in (curve.PointAt(d.T0 + (d.T1 - d.T0) * i / 64)
                                               for i in range(65))]] + out
        if order == "Default":
            # OURS: before the direction is known, the circle through the three points stands
            # in for the curve (measured: a curve from the start through the point to the cursor).
            arc = _arc_polyline(points[0], tuple(cursor), points[1])
            return [arc] if arc else [points + [tuple(cursor)]]
        return [points + [tuple(cursor)]]
    order = opt.get("PickOrder", "Default")
    if order == "EndsFirst":
        start, end, vertex = points[0], points[1], tuple(cursor)
    elif order == "EndsLast":
        vertex, start, end = points[0], points[1], tuple(cursor)
    else:
        start, vertex, end = points[0], points[1], tuple(cursor)
    midpoint = tuple((start[k] + end[k]) * 0.5 for k in range(3))
    axis_vector = _sub(midpoint, vertex)
    axis = _unit3(axis_vector)
    half_chord = _sub(end, midpoint)
    transverse = _length(half_chord)
    axial = _length(axis_vector)
    if axis is None or transverse <= 1e-12 or axial <= 1e-12:
        return [[start, vertex, end]]
    focus = tuple(vertex[k] + transverse*transverse/(4*axial) * axis[k]
                  for k in range(3))
    proxy = type("ParabolaPreviewState", (), {
        "_construction": "Vertex", "opt": {"Half": False}})()
    return preview_parabola([vertex, focus], end, plane, cmd=proxy)


def preview_parabola(args, cursor, plane, cmd=None):
    points = [tuple(value) for value in (args or []) if _is_point(value)]
    if not points:
        return []
    if len(points) < 2:
        return [points + [tuple(cursor)]]
    end = tuple(cursor)
    construction = getattr(cmd, "_construction", "Focus")
    if construction == "Vertex":
        vertex, focus = points[:2]
        axis = _unit3(_sub(focus, vertex))
        if axis is None:
            return [[vertex, focus, end]]
        a = _length(_sub(focus, vertex))
    else:
        focus, direction = points[:2]
        axis = _unit3(_sub(direction, focus))
        if axis is None:
            return [[focus, direction, end]]
        delta = _sub(end, focus)
        x = dot(delta, axis)
        transverse = _sub(delta, tuple(x * axis[k] for k in range(3)))
        y = _length(transverse)
        a = (-x + math.sqrt(x * x + y * y)) / 2.0
        if a <= 1e-12:
            return [[focus, direction, end]]
        vertex = tuple(focus[k] - a * axis[k] for k in range(3))
    delta = _sub(end, vertex)
    axial = dot(delta, axis)
    transverse_vector = _sub(delta, tuple(axial * axis[k] for k in range(3)))
    transverse = _length(transverse_vector)
    side = _unit3(transverse_vector)
    if side is None or a <= 1e-12:
        return [[vertex, end]]
    axial = transverse * transverse / (4.0 * a)
    endpoint = tuple(vertex[k] + axial * axis[k] + transverse * side[k]
                     for k in range(3))
    u = transverse / (2.0 * a)
    if bool(getattr(cmd, "opt", {}).get("Half")):
        control = tuple(vertex[k] + a * u * side[k] for k in range(3))
        return [_quadratic_preview(vertex, control, endpoint)]
    other = tuple(vertex[k] + axial * axis[k] - transverse * side[k]
                  for k in range(3))
    middle = tuple(vertex[k] - axial * axis[k] for k in range(3))
    return [_quadratic_preview(other, middle, endpoint)]


def preview_conic(args, cursor, plane, cmd=None):
    points = [tuple(value) for value in (args or []) if _is_point(value)]
    if not points:
        return []
    if len(points) == 2 and not getattr(cmd, "_apex_first", False):
        # MEASURED (research_preview_14 P31): at "Apex" two rubber lines, from the start and
        # from the end to the cursor -- no chord, no arc yet.
        return [[points[0], tuple(cursor), points[1]]]
    if len(points) < 3:
        return [points + [tuple(cursor)]]
    if getattr(cmd, "_apex_first", False):
        start, apex, end = points[:3]
    else:
        start, end, apex = points[:3]
    q = tuple(cursor)
    v0, v1, v2 = _sub(apex, start), _sub(end, start), _sub(q, start)
    d00, d01, d11 = dot(v0, v0), dot(v0, v1), dot(v1, v1)
    denominator = d00 * d11 - d01 * d01
    if abs(denominator) <= 1e-18:
        return [[start, apex, end]]
    l1 = (d11 * dot(v2, v0) - d01 * dot(v2, v1)) / denominator
    l2 = (d00 * dot(v2, v1) - d01 * dot(v2, v0)) / denominator
    l0 = 1.0 - l1 - l2
    if min(l0, l1, l2) <= 0.0:
        return [[start, apex, end]]
    root0, root2 = math.sqrt(l0), math.sqrt(l2)
    through_t = root2 / (root0 + root2)
    base = 2.0 * through_t * (1.0 - through_t)
    full = (1.0 - through_t) ** 2 / l0
    weight = l1 * full / base
    curve = []
    for index in range(65):
        t = index / 64.0
        s = 1.0 - t
        divisor = s * s + 2.0 * weight * s * t + t * t
        curve.append(tuple((s * s * start[k] + 2.0 * weight * s * t * apex[k]
                            + t * t * end[k]) / divisor for k in range(3)))
    return [[start, apex, end], curve]


def preview_helix(args, cursor, plane, cmd=None):
    return preview_coil(args, cursor, plane, cmd=cmd, spiral=False)


def preview_spiral(args, cursor, plane, cmd=None):
    return preview_coil(args, cursor, plane, cmd=cmd, spiral=True)


#: command name (lowercase) -> preview function. This is the table to extend.
PREVIEWS = {
    "line": preview_line,
    "polyline": preview_polyline,
    "srfpt": preview_srfpt,
    # These two used to map to `preview_polyline`, showing the control polygon while
    # `bridge.make_polyline(args, degree=3)` committed a smooth cubic. `InterpCrv`
    # shares the preview because it uses the same builder.
    "curve": preview_curve,
    "interpcrv": preview_interpcrv,
    "handlecurve": preview_handlecurve,
    "circle": preview_circle,
    "box": preview_box,
    "sphere": preview_sphere,
    "cylinder": preview_cylinder,
    "torus": preview_torus,
    "tube": preview_tube,
    "truncatedcone": preview_truncatedcone,
    "ellipsoid": preview_ellipsoid,
    "cone": preview_cone,
    "rectangle": preview_rectangle,
    # MEASURED (research_preview_14 P53): Plane draws the same live rectangle (and the
    # same length-then-width route as Box's base).
    "plane": preview_plane,
    "arraypolar": preview_arraypolar,
    "ellipse": preview_ellipse,
    "distance": preview_distance,
    "mirror": preview_mirror,
    # Move/Copy are two-point commands, so the from-point-to-cursor
    # vector is a real preview (it was a documented gap while they took a delta).
    "move": preview_move,
    # Additional cursor-driven command previews.
    "angle": preview_angle,
    "picture": preview_picture,
    "rotate": preview_rotate,
    "rotate3d": preview_rotate3d,
    "scale": preview_scale,
    "scale1d": preview_scale,
    "scale2d": preview_scale,
}


#: Previews that need DOCUMENT geometry, not just the picked args. Kept as a separate table
#: so the twenty existing `(args, cursor, plane)` functions keep their signature -- adding a
#: fourth parameter to all of them to serve one command would be the wrong trade.
PREVIEWS_WITH_CMD = {
    "arc": preview_arc,
    "curve": preview_curve,
    "polygon": preview_polygon,
    "pyramid": preview_pyramid,
    "catenary": preview_catenary,
    "conic": preview_conic,
    "hyperbola": preview_hyperbola,
    "parabola": preview_parabola,
    "parabola3pt": preview_parabola3pt,
    "helix": preview_helix,
    "spiral": preview_spiral,
}


def preview_offset(args, cursor, plane, resolve, cmd=None):
    """`Offset` -- MEASURED (research_preview_14 P47): at "Side to offset" the offset curve
    is drawn live on the cursor's side at the Distance, and flips side with the cursor.
    The running command computes it with the same exact offset as the commit."""
    fn = getattr(cmd, "preview_curves", None)
    if not args or not callable(fn):
        return []
    return fn(resolve, cursor)


def preview_pipe(args, cursor, plane, resolve, cmd=None):
    """`Pipe`: the radius circle(s) across the rail, the live one following the cursor
    (a recorded observation of the reference program). Display only."""
    fn = getattr(cmd, "preview_circles", None)
    if not args or not callable(fn):
        return []
    out = []
    for center, tangent, r in fn(resolve, cursor):
        t = _unit3(tangent) if _length(tangent) > 0 else None
        if t is None or not r > 0:
            continue
        helper = (0.0, 0.0, 1.0) if abs(t[2]) < 0.9 else (1.0, 0.0, 0.0)
        u = _unit3(_cross3(t, helper))
        v = _cross3(t, u)
        out.append(_ring(center, u, v, r))
        last_center = center
    if out and cmd is not None and len(args) == cmd.step_index:
        out.append([last_center, tuple(cursor)])      # the radius guide to the cursor
    return out


#: Copies drawn at most by `preview_array` -- a 100 x 100 array is still a real command, but
#: re-drawing ten thousand wireframes per mouse move is not a preview.
ARRAY_PREVIEW_COPIES = 400


def _object_wires(resolve, names, cache=None):
    """The wireframe of each named object as point lists (curves sampled, solids by their
    exact edges). `cache` (a dict on the running command) keeps it across mouse moves."""
    key = tuple(names)
    if cache is not None and cache.get("key") == key:
        return cache["wires"]
    try:
        from . import bridge
    except ImportError:          # loaded standalone (tests / probes)
        import bridge
    wires = []
    for name in names:
        geom = resolve(name) if name else None
        if geom is None:
            continue
        curves = [geom] if hasattr(geom, "PointAtStart") else bridge.extract_wireframe(geom)[0]
        for curve in curves:
            n = 2 if getattr(curve, "IsLinear", lambda: False)() else 48
            pts = bridge.sample_curve(curve, n)
            if pts and len(pts) >= 2:
                wires.append([(p.X, p.Y, p.Z) for p in pts])
        loc = getattr(geom, "Location", None)
        if loc is not None and not curves:
            wires.append([(loc.X, loc.Y, loc.Z), (loc.X, loc.Y, loc.Z)])
    if cache is not None:
        cache["key"], cache["wires"] = key, wires
    return wires


def preview_array(args, cursor, plane, resolve, cmd=None):
    """`Array` -- MEASURED (research_preview_14 P28): nothing at "Unit cell or X spacing";
    after the first cell corner, the cell rectangle from it to the cursor; at "Press Enter to
    accept" the copies of the selection at their places. (Dashed marks seen beside the cell
    were not identified, so they are not drawn.)"""
    if cmd is None:
        return []
    label = cmd._step_label() if hasattr(cmd, "_step_label") else None
    corner = getattr(cmd, "_cell_corner", None)
    if label == getattr(cmd, "OTHER_CORNER", None) and corner is not None:
        return [rectangle_polyline(plane, tuple(corner), tuple(cursor))]
    if label != getattr(cmd, "ACCEPT", None) or not args:
        return []
    names = list(args[0]) if isinstance(args[0], (list, tuple)) else [args[0]]
    if not hasattr(cmd, "_preview_cache"):
        cmd._preview_cache = {}
    wires = _object_wires(resolve, names, cmd._preview_cache)
    out = []
    for d in cmd.offsets(plane)[:ARRAY_PREVIEW_COPIES]:
        for wire in wires:
            out.append([(p[0] + d[0], p[1] + d[1], p[2] + d[2]) for p in wire])
    return out


def preview_copy(args, cursor, plane, resolve, cmd=None):
    """`Copy` -- MEASURED (research_preview_14 P32): a rubber line from the from-point to the
    cursor (the live copy at the cursor is the transform ghost), and every copy already
    placed stays drawn while the command waits for more (the command builds them all at
    Enter, as one undo step, so they are shown here)."""
    if len(args) < 2 or not _is_point(args[1]):
        return []
    out = [[tuple(args[1]), tuple(cursor)]]
    placed = [d for d in args[2:] if _is_point(d)]
    names = args[0] if isinstance(args[0], (list, tuple)) else []
    if placed and names:
        if cmd is not None and not hasattr(cmd, "_preview_cache"):
            cmd._preview_cache = {}
        wires = _object_wires(resolve, list(names), getattr(cmd, "_preview_cache", None))
        for d in placed[:ARRAY_PREVIEW_COPIES]:
            for wire in wires:
                out.append([(p[0] + d[0], p[1] + d[1], p[2] + d[2]) for p in wire])
    return out


def preview_move_subobject(args, cursor, plane, resolve, cmd=None):
    """`MoveEdge` / `MoveFace`: the running command's live stretched wireframe."""
    fn = getattr(cmd, "preview_wires", None)
    return fn(resolve, cursor) if callable(fn) else []


PREVIEWS_WITH_DOC = {
    "moveedge": preview_move_subobject,
    "moveface": preview_move_subobject,
    "copy": preview_copy,
    "array": preview_array,
    "revolve": preview_revolve,
    "pipe": preview_pipe,
    "offset": preview_offset,
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
    cmd_fn = PREVIEWS_WITH_CMD.get(key)
    doc_fn = PREVIEWS_WITH_DOC.get(key)
    if fn is None and cmd_fn is None and doc_fn is None:
        return []
    try:
        variant = (_variant_preview(key, list(args or []), cursor, plane, cmd)
                   if cmd is not None else None)
        if variant is not None:
            out = variant
        elif cmd_fn is not None:
            out = cmd_fn(args or [], tuple(cursor), plane, cmd=cmd)
        elif doc_fn is not None:
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
    return bool(key) and (key in PREVIEWS or key in PREVIEWS_WITH_CMD
                          or key in PREVIEWS_WITH_DOC)
