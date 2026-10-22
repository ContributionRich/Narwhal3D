"""How the reference CAD application draws points, as 2-D screen geometry. **No `bpy`, no rhino3dm.**

The geometry is pure so every style can be exercised without launching Blender.

Why this exists
---------------
`Point` and `Points` build real `r3.Point` geometry that round-trips through a
`.3dm`, but a
single-vertex mesh draws **nothing** in a Blender object-mode viewport, so a
point object was invisible the moment it was created. And nothing displayed the
*control* points of a curve at all.

A point has no extent, so it cannot be drawn by the line-segment preview path the
way a circle can -- it needs a marker of a fixed PIXEL size, like the snap marker.
This module produces that marker in region pixels; `ui` only uploads it.

The styles are the reference CAD application's own (MEASURED)
-------------------------------------
`Tools > Options > View > Display Modes > [Mode] > Objects > Points` offers four
for point objects, and gives their size "in pixels":

    Round with white center / Square with white center / Solid circle / Solid square

`the reference CAD application.Display.PointStyle` in `CAD SDK.xml` names the same shapes and settles
what they are called internally, with its own one-line descriptions:

    Simple ................ "Square"
    ControlPoint .......... "Square with white center"
    ActivePoint ........... "Like a control point but includes vertical/horizontal
                             crosshair lines."
    RoundSimple ........... "Circular"
    RoundControlPoint ..... "Circular with white center"
    RoundActivePoint ...... "Round control point with crosshair lines"
    X ..................... "X shape"

Two things fall out of that, and neither is a guess:

* **A control point is drawn as a square with a white centre** -- the enum member
  used for them is *literally named* `ControlPoint` and described that way.
* **A SELECTED control point adds crosshairs** -- that is what `ActivePoint`
  is ("like a control point but includes ... crosshair lines").

What a marker is
----------------
:func:`marker` returns ``(fill_triangles, outline_segments)``, both lists of 2-D
pixel points, plus which colour each wants. Keeping fill and outline apart is
what lets "with white center" mean what it says: the fill is drawn white and the
outline in the object's colour.
"""
from __future__ import annotations

import math

# --- style names, taken from the reference CAD application.Display.PointStyle (MEASURED) -----------

SIMPLE = "Simple"                            # solid square
CONTROL_POINT = "ControlPoint"               # square with white centre
ACTIVE_POINT = "ActivePoint"                 # control point + crosshairs
ROUND_SIMPLE = "RoundSimple"                 # solid circle
ROUND_CONTROL_POINT = "RoundControlPoint"    # circle with white centre
ROUND_ACTIVE_POINT = "RoundActivePoint"      # round control point + crosshairs
X_SHAPE = "X"                                # an X

#: The four the Options page offers for POINT OBJECTS, in the order it lists them.
POINT_OBJECT_STYLES = (ROUND_CONTROL_POINT, CONTROL_POINT,
                       ROUND_SIMPLE, SIMPLE)

#: Everything :func:`marker` can draw.
STYLES = (SIMPLE, CONTROL_POINT, ACTIVE_POINT, ROUND_SIMPLE,
          ROUND_CONTROL_POINT, ROUND_ACTIVE_POINT, X_SHAPE)

#: Style for a point OBJECT. **OURS** -- the Options page lists the four choices
#: but never says which is default, and no file we hold records it.
DEFAULT_POINT_STYLE = ROUND_CONTROL_POINT

#: Style for a CONTROL point. MEASURED by name: the enum member the reference CAD application uses for
#: control points is `ControlPoint`, "Square with white center".
CONTROL_POINT_STYLE = CONTROL_POINT

#: Style for a SELECTED control point. MEASURED by description: `ActivePoint` is
#: "like a control point but includes vertical/horizontal crosshair lines".
ACTIVE_CONTROL_POINT_STYLE = ACTIVE_POINT

#: Marker size in pixels. The Options page says the setting is "Point object size
#: in pixels" but states no default, so the NUMBER is **OURS**. Chosen by eye to
#: be clearly visible without being obtrusive: at 3 px a round marker's white
#: centre came to 3 pixels on screen, which is technically drawn but not something
#: you would notice. The snap marker nearby uses a 7 px half-width for the same
#: readability reason.
DEFAULT_SIZE_PX = 7.0

#: Segments used to approximate a round marker. OURS -- a rendering detail.
CIRCLE_SEGMENTS = 12

#: How far the crosshair of an ActivePoint extends past the marker, as a multiple
#: of the marker half-size. OURS.
CROSSHAIR_SCALE = 2.0


def is_round(style):
    """True for the circular styles."""
    return style in (ROUND_SIMPLE, ROUND_CONTROL_POINT, ROUND_ACTIVE_POINT)


def has_white_center(style):
    """True for the styles the reference CAD application describes as having a white centre.

    The `ActivePoint` styles count: they are defined as a control point -- which
    is the white-centred one -- plus crosshairs.
    """
    return style in (CONTROL_POINT, ACTIVE_POINT,
                     ROUND_CONTROL_POINT, ROUND_ACTIVE_POINT)


def has_crosshair(style):
    """True for the `ActivePoint` styles, which add crosshair lines."""
    return style in (ACTIVE_POINT, ROUND_ACTIVE_POINT)


def _square(x, y, h):
    return [(x - h, y - h), (x + h, y - h), (x + h, y + h), (x - h, y + h)]


def _circle(x, y, h, segments=CIRCLE_SEGMENTS):
    pts = []
    for i in range(segments):
        a = 2.0 * math.pi * i / segments
        pts.append((x + math.cos(a) * h, y + math.sin(a) * h))
    return pts


def _loop_segments(pts):
    """A closed outline as a flat list of segment endpoints (for GPU LINES)."""
    out = []
    for i, p in enumerate(pts):
        out.append(p)
        out.append(pts[(i + 1) % len(pts)])
    return out


def _fan(x, y, pts):
    """A filled polygon as a triangle fan, flattened (for GPU TRIS)."""
    out = []
    for i in range(len(pts)):
        out.append((x, y))
        out.append(pts[i])
        out.append(pts[(i + 1) % len(pts)])
    return out


def marker(style, x, y, size_px=DEFAULT_SIZE_PX):
    """The 2-D pixel geometry for one point marker.

    Returns ``(fill, outline)``:

    * ``fill``   -- triangles to fill, or ``[]``. For a "with white center" style
      the caller draws these WHITE; for a solid style, in the object's colour.
    * ``outline`` -- line-segment endpoints to stroke in the object's colour.

    `size_px` is the marker's SIZE, matching the reference CAD application's "size in pixels" setting; the
    marker is drawn within a box of that size, so the half-extent is size/2.
    Returns ``([], [])`` for a non-positive size -- an invisible marker is the
    right answer for a zero size, not a degenerate one.
    """
    if size_px is None or size_px <= 0:
        return [], []
    h = float(size_px) / 2.0
    x, y = float(x), float(y)

    if style == X_SHAPE:
        # no interior, so no fill -- just the two diagonals
        return [], [(x - h, y - h), (x + h, y + h),
                    (x - h, y + h), (x + h, y - h)]

    ring = _circle(x, y, h) if is_round(style) else _square(x, y, h)
    outline = _loop_segments(ring)
    fill = _fan(x, y, ring)

    if has_crosshair(style):
        reach = h * CROSSHAIR_SCALE
        outline = outline + [(x - reach, y), (x + reach, y),
                             (x, y - reach), (x, y + reach)]
    return fill, outline


def marker_template(style, size_px=DEFAULT_SIZE_PX):
    """:func:`marker` at the origin as SHARED vertices plus indices.

    Returns ``(offsets, fill_tris, outline_lines)``: pixel offsets ``(dx, dy)`` from the
    point, index triples into them for the fill, and index pairs for the outline.
    Expanding it at ``(x, y)`` -- ``[(x + dx, y + dy) for each index]`` -- gives back
    exactly :func:`marker`'s ``(fill, outline)``, vertex for vertex and in order.

    A large file can display hundreds of thousands of control points. Building each
    marker in Python every frame is prohibitively expensive; one template copied per
    point into a GPU batch once, draws them all with the same shape.
    """
    fill, outline = marker(style, 0.0, 0.0, size_px)
    offsets, where = [], {}

    def idx(p):
        if p not in where:
            where[p] = len(offsets)
            offsets.append(p)
        return where[p]

    flat_fill = [idx(p) for p in fill]
    flat_line = [idx(p) for p in outline]
    tris = [tuple(flat_fill[i:i + 3]) for i in range(0, len(flat_fill), 3)]
    lines = [tuple(flat_line[i:i + 2]) for i in range(0, len(flat_line), 2)]
    return offsets, tris, lines


def fill_is_white(style):
    """Should :func:`marker`'s fill be drawn white, or in the object's colour?

    White for the "with white center" styles; the object's colour for the solid
    ones. This is the whole difference between `Simple` ("Square") and
    `ControlPoint` ("Square with white center").
    """
    return has_white_center(style)


# ---------------------------------------------------------------------------
# THE CONTROL POLYGON
#
# MEASURED that it exists and what it is:
#   commands/pointson.htm -- "To select a loop of NURBS or SubD control points:
#   Turn on **Make control polygon pickable** in Options > Mouse for loop
#   selection to work. **When Control Points are turned on, double-click a
#   control polygon segment.**"
# So the control polygon is drawn whenever control points are on, and its
# SEGMENTS are things the user can aim at.
#
# MEASURED what its geometry is:
#   commands/extractcontrolpolygon.htm -- "This creates polylines and/or meshes
#   that **have vertices at the original objects' control points**."
# i.e. the polyline through the control points in index order. Not a guess.
#
# The help does not specify the dash pattern or colour. These defaults produce the
# subtle dotted connections used for the control polygon.
# ---------------------------------------------------------------------------

#: Dash and gap lengths for the control polygon, in pixels.
CONTROL_POLYGON_DASH_PX = 4.0
CONTROL_POLYGON_GAP_PX = 4.0


def dashed_polyline(points, dash_px=CONTROL_POLYGON_DASH_PX,
                    gap_px=CONTROL_POLYGON_GAP_PX):
    """A 2-D polyline as dashed LINES vertices: ``[(x, y), (x, y), ...]`` pairs.

    `points` is a sequence of 2-D points; None entries (a vertex that would not
    project) **break** the run rather than being skipped, because joining across a
    gap would draw a segment that does not exist.

    The dash phase is carried continuously along the whole polyline instead of
    being restarted per segment. Restarting per segment puts a dash at every
    control point, which reads as a solid blob exactly where the point marker
    already is -- the thing this is meant to sit behind, not fight with.

    Returns [] for anything with fewer than two consecutive usable points, and for
    a non-positive dash or gap (so "turn it off" is expressible, as it is for every
    gumball handle).
    """
    if dash_px <= 0 or gap_px <= 0:
        return []
    out = []
    # The dash/gap state is carried as an explicit (drawing?, length left) pair, NOT as
    # `phase % period`. The modulo form could hang at interface scale 1.3:
    # with a 5.2 px dash, `phase % period` could land an ulp short of the dash end, so
    # the step was ~1e-15, `phase += step` was absorbed by the larger `phase`, and the
    # loop appended the same vertices forever -- 30 GB of RAM. The 4 px default is exact
    # in binary, which is why it never showed. Here every pass either finishes the
    # segment or finishes a dash/gap, so the loop always terminates.
    drawing = True
    left = float(dash_px)  # length remaining in the current dash (or gap)
    # `dash_phases` is the vectorised twin of this loop for many polylines in one
    # GPU batch. Keep the dash rule identical in both implementations.
    runs = []
    cur = []
    for p in points or []:
        if p is None:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
        else:
            cur.append(p)
    if len(cur) >= 2:
        runs.append(cur)

    for run in runs:
        for a, b in zip(run, run[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            seg = math.hypot(dx, dy)
            if seg <= 0.0:
                continue
            ux, uy = dx / seg, dy / seg
            walked = 0.0
            while True:
                rest = seg - walked
                if left >= rest - 1e-9:
                    # the current dash/gap reaches the end of this segment (to within
                    # 1e-9 px, so neither end can leave a sub-ulp sliver behind)
                    if drawing and rest > 0.0:
                        out.append((a[0] + ux * walked, a[1] + uy * walked))
                        out.append((b[0], b[1]))
                    left -= rest
                    if left <= 1e-9:
                        # it ended ON the vertex: flip now, or the next segment
                        # would start with a sub-ulp sliver
                        drawing = not drawing
                        left = float(dash_px if drawing else gap_px)
                    break
                if drawing and left > 0.0:
                    out.append((a[0] + ux * walked, a[1] + uy * walked))
                    out.append((a[0] + ux * (walked + left),
                                a[1] + uy * (walked + left)))
                walked += left
                drawing = not drawing
                left = float(dash_px if drawing else gap_px)
    return out


def dash_phases(xy, valid, first, period):
    """The dash phase of MANY polylines at once -- :func:`dashed_polyline`'s pattern,
    for a renderer that dashes on the GPU (`mod(phase, dash + gap) < dash` is ink).

    `xy` is an ``(n, 2)`` array of every polyline's projected vertices, concatenated;
    `valid` is False where a vertex did not project (a ``None`` in `dashed_polyline`);
    `first` is True at the first vertex of each polyline. Returns ``(seg, a0, a1)``:
    the index ``i`` of every DRAWN segment (vertex ``i`` to ``i + 1``) and the phase at
    its two ends, reduced modulo `period` at the start so float32 keeps sub-pixel
    precision however long the polyline.

    Same rules as `dashed_polyline`: the phase starts at 0 on each polyline and runs on
    continuously across vertices (no dash restart at every control point); a segment
    touching an unprojectable vertex is not drawn and adds no length, so the pattern
    carries over the break; zero-length segments are skipped. Needs numpy.
    """
    import numpy as np

    xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    n = len(xy)
    if n < 2 or period <= 0:
        empty = np.zeros(0)
        return np.zeros(0, dtype=np.int64), empty, empty
    valid = np.asarray(valid, dtype=bool)
    first = np.asarray(first, dtype=bool)
    joined = valid[:-1] & valid[1:] & ~first[1:]
    d = np.hypot(xy[1:, 0] - xy[:-1, 0], xy[1:, 1] - xy[:-1, 1])
    d = np.where(joined, d, 0.0)
    cum = np.concatenate(([0.0], np.cumsum(d)))      # phase at vertex i, from vertex 0
    start = np.maximum.accumulate(np.where(first, np.arange(n), 0))
    phase = cum - cum[start]                          # ...from its own polyline's start
    seg = np.nonzero(joined & (d > 0.0))[0]
    a0 = np.mod(phase[seg], float(period))
    return seg, a0, a0 + d[seg]
