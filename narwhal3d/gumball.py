"""Pure geometry and drag math for Narwhal 3D's Gumball transform widget.

The module has no `bpy` or rhino3dm dependency. It builds move, rotate, scale,
planar-move, origin, extrude, and menu handles in world coordinates; `ui.py`
projects those shapes and feeds pointer input back into the pure drag functions.
Unsupported modes are declared explicitly and report that they are unavailable
instead of substituting another transform.

The widget aligns to CPlane, World, or View frames, keeps a constant screen size
through zoom conversion, and places its origin at the selection's oriented
bounding-box center. Appearance dimensions are centralized in :data:`SIZES_PX`
and project-specific proportions are centralized in :data:`PROPORTIONS`.
"""
from __future__ import annotations

import math

# --- handle kinds ----------------------------------------------------------

MOVE = "move"
ROTATE = "rotate"
SCALE = "scale"
ORIGIN = "origin"
#: Move-2D (plane) handles. Rhino's name for these, from
#: the `Rhino.UI.Gumball.GumballMode` enum, is `TranslateXY` / `TranslateYZ` /
#: `TranslateZX` (MEASURED -- RhinoCommon.xml). We carry ONE kind plus the index
#: of the axis the plane is NORMAL to, which is the same convention `ROTATE`
#: already uses (rotate about axis i), so plane "xy" has `axis == 2`.
PLANE = "plane"

# DRAG MODES. A handle's *kind* is what was grabbed; the *mode* is
# what the drag does, which depends on the modifier keys held. Rhino overloads
# every handle this way (commands/gumball.htm), so the two concepts have to be
# separate -- the same Move-2D handle is a planar move, a Scale2D or an extrude
# depending on Shift/Ctrl.
#
# These two are reached with SHIFT and are implemented:
#   Scale2D  -- "Drag with Shift to re-size the object in two axes with the same
#               scale. The Gumball origin is the base point of scaling." (on a
#               Move-2D handle)
#   Scale3D  -- "Drag with Shift to scale the object in three dimensions." (on a
#               scale handle)
SCALE_2D = "scale2d"
SCALE_3D = "scale3d"

# Reached with CTRL, and NOT implemented -- they need the extrude/boolean kernel
# work. Named here so the widget can say "not implemented yet" instead of
# silently performing the unmodified drag, which would look like it worked
# (rule 4).
EXTRUDE = "extrude"
#: The visible spherical dot on an axis. Distinct from EXTRUDE, which is a
#: drag MODE requested by modifiers on other handles.
EXTRUDE_HANDLE = "extrude_handle"
EXTRUDE_OPEN = "extrude_open"
EXTRUDE_BOTH = "extrude_both"
CUT = "cut"

#: The MENU BALL, which the help calls the "bunny tail":
#:   "Click on the circle to show the Gumball menu."
#:   "Change the size in Gumball options. Set 0 to turn it off."
#: It was deliberately NOT drawn for several rounds, because a ball that draws and
#: does nothing when clicked would look implemented (rule 4). It is drawn now
#: because the menu it opens exists -- see MENU_ENTRIES.
MENU = "menu"

#: Which modes we can actually perform. Anything else must be reported, not done.
IMPLEMENTED_MODES = frozenset({MOVE, ROTATE, SCALE, ORIGIN, PLANE,
                               SCALE_2D, SCALE_3D, MENU})

#: The four alignments `GumballAlignment` documents (commands/gumball.htm,
#: "Gumball menu"), and whether we build each. MEASURED names; the flags are our
#: honest record of what is implemented.
#:
#: `Object` is False and likely to stay so for a while: the help says only "The
#: default alignment is different for different object types" and never says WHAT
#: for which type. RhinoCommon has `RhinoObject.TryGetGumballFrame` for exactly
#: this, but rhino3dm does not expose it, so there is nothing to measure against.
#: Guessing per-type frames would be inventing Rhino behaviour.
ALIGNMENTS = {
    "CPlane": True,
    "Object": False,
    "World": True,
    "View": True,
}

#: Rhino's default. "Rhino's standard viewports come with construction planes that
#: correspond to the viewport", and the Gumball follows the CPlane unless told
#: otherwise.
DEFAULT_ALIGNMENT = "CPlane"

#: The widget's on-screen radius in pixels before zoom conversion.
DEFAULT_SIZE_PX = float(80)

#: How close (in pixels) the cursor must come to a handle to grab it. OURS --
#: Rhino documents no pick tolerance for the Gumball.
PICK_TOLERANCE_PX = 11.0

# ---------------------------------------------------------------------------
# GUMBALL APPEARANCE DIMENSIONS IN PIXELS.
#
# Keys use the compatibility option names consumed by the Gumball renderer. Keeping
# every dimension in one table ensures the geometry builders scale consistently.
# ---------------------------------------------------------------------------

SIZES_PX = {
    # command option       px   profile key      help wording
    "Radius": 80,           # Radius            "size of the arcs and arrows"
    "TipLength": 20,        # TipLength         "length of the arrow heads"
    "TipWidth": 5,          # TipWidth          "width of the arrow heads"
    "TailSize": 4,          # TailSize          "size of the small rectangles"
    "PlaneLocation": 40,    # PTranDistance     "distance between a window icon and the Gumball origin"
    "PlaneSize": 20,        # PTranSize         "size of the window icons"
    "AxisThickness": 2,     # AxisThickness     "thickness of the arrow shafts"
    "ArcThickness": 2,      # ArcThickness      "thickness of the arcs"
    "MenuBallDistance": 30,  # MenuDistance     "distance between the Gumball origin and the menu circle"
    "MenuBallSize": 5,      # MenuButtonSize    "radius of the menu circle"
    "ExtrudeBallSize": 5,   # ExtrudeBallSize   "size of the extrude dots"
}

#: Plane visibility angle in degrees. Zero shows a plane only face-on; ninety
#: always shows it. Kept separate from :data:`SIZES_PX` because it is not a length.
PLANE_VISIBILITY_ANGLE_DEG = 50.0

#: Line widths, in pixels, straight from :data:`SIZES_PX`. Absolute pixel values in
#: Rhino, so they are NOT scaled by the widget size.
AXIS_THICKNESS_PX = float(SIZES_PX["AxisThickness"])
ARC_THICKNESS_PX = float(SIZES_PX["ArcThickness"])


def _frac(key):
    """One of :data:`SIZES_PX` as a fraction of the widget `Radius`.

    Rhino stores each size as an INDEPENDENT pixel count; we store the widget size
    once and everything else as a fraction of it, so our single `size` argument
    still scales the whole widget. At the default Radius the two models agree
    exactly, which is the case that matters -- they differ only if a user changes
    Radius alone, and we have no UI for that yet.
    """
    return SIZES_PX[key] / float(SIZES_PX["Radius"])


#: Widget proportions as fractions of the widget size.
#:
#: Seven are now **MEASURED** (see :data:`SIZES_PX`), two are DERIVED from one
#: measured sentence, and the rest are flagged OURS individually -- the help
#: describes those handles' behaviour but never their geometry, and the binary has
#: no setting for them.
PROPORTIONS = {
    # The arrows run to the full radius. Rhino's single `Radius` setting covers
    # "the size of the arcs and arrows", so in Rhino the arcs run to 1.0 as well --
    # see `arc_radius` below for why ours do not.
    "arrow_tip": 1.0,
    # OURS, AND A DELIBERATE DEVIATION FROM RHINO. Rhino's `Radius` governs the
    # arcs and the arrows together, and McNeel's widget drawing measures the arcs at
    # the full radius, so faithful would be 1.0 -- which is what this was.
    #
    # At 1.0 the arc ends at the arrow tip, so the whole arrow falls within
    # PICK_TOLERANCE_PX of an arc and the rotate handle can win an intended move click.
    #
    # 0.85 leaves 12 px of arrow beyond the arc at the default size -- just over the
    # 11 px tolerance, so the arrow's last stretch is unambiguously the arrow.
    # Recorded rather than silently tuned: set this back to 1.0 to match Rhino
    # exactly and accept the ambiguity.
    "arc_radius": 0.85,
    "head_len": _frac("TipLength"),     # MEASURED: TipLength = 20 px
    # MEASURED value, DERIVED reading: `TipWidth` = 5 and ours is a HALF-width.
    # McNeel's widget drawing settles which: the red arrowhead there is ~12 px
    # across at a scale where the arcs are 80, i.e. ~5 either side of the shaft,
    # so Rhino's 5 is the half-width and not the full width.
    "head_half": _frac("TipWidth"),
    # Same shape of reading: `TailSize` = 4 is the HALF-size of the little scale
    # square (the drawing's squares measure ~8 px across at that scale).
    "scale_half": _frac("TailSize"),
    "plane_at": _frac("PlaneLocation"),     # MEASURED: PTranDistance = 40 px
    "plane_size": _frac("PlaneSize"),       # MEASURED: PTranSize = 20 px
    "menu_at": _frac("MenuBallDistance"),   # MEASURED: MenuDistance = 30 px
    "menu_radius": _frac("MenuBallSize"),   # MEASURED: MenuButtonSize = 5 px
    "extrude_radius": _frac("ExtrudeBallSize"),  # MEASURED: 5 px
    # --- still OURS: no Rhino setting and no measurement exists for these ---
    # Where along the axis the scale square sits. Rhino has no appearance setting
    # for it (its `X/Y/Z scale handle length` strings are per-object, written by
    # SetScaleHandles, not a default). Both McNeel images put the scale squares
    # BEYOND the arcs at the end of a dotted line, so this is > arc_radius.
    "scale_at": 1.15,
    # Where the extrude dot sits on each move axis. Rhino exposes its SIZE but not its
    # location; 0.62 is OURS, inside the cone and clear of the origin/rotation arc.
    "extrude_at": 0.62,
    "arc_span": 90.0,        # rotate arc sweep, in degrees. OURS.
    "arc_segments": 16,      # OURS: tessellation, not appearance
    "origin_half": 0.045,    # half-width of the origin marker. OURS.
    "menu_segments": 12,     # OURS: tessellation, not appearance
    # Dash length of the LEADER lines, as a fraction of the widget size. Rhino's
    # help says the Scale and Menu handles each include a "dotted line connecting
    # to the Gumball origin" (MEASURED, see `_dashed_segment`), but never how long
    # the dashes are. OURS. Gap is the same length as the dash.
    "leader_dash": 0.05,
}

#: Which of the two x/y diagonals the menu ball sits on, as (x_sign, y_sign).
#:
#: The menu ball uses the diagonal opposite the Move-2D window icon, preventing
#: their hit regions from overlapping. The ``-x-y`` placement also clears the other
#: window icons and scale handles.
MENU_BALL_DIAGONAL = (-1.0, -1.0)

#: Which side of its axis the Scale handle sits on, as a multiplier.
#:
#: Scale handles sit opposite move arrows at the end of dotted leaders. Placing the
#: square on the arrow side would overlap both the rotate arc and arrow shaft. Read by BOTH
#: :func:`_scale_handle` (where the square is drawn) and `ui`'s drag setup (which
#: needs the same direction to get the sign of the scale right) -- via
#: :func:`scale_axis_direction`, so there is one definition and not two that can
#: drift apart.
SCALE_HANDLE_SIGN = -1.0


def scale_axis_direction(axis_dir):
    """The direction, from the widget origin, in which the Scale handle lies.

    The single source of truth for :data:`SCALE_HANDLE_SIGN`. A scale drag measures
    the cursor's travel along the handle's own direction, so if this and
    :func:`_scale_handle` disagreed, dragging the square outward would SHRINK the
    object. Keeping this as the single source of truth prevents those paths from diverging.
    """
    return _mul(axis_dir, SCALE_HANDLE_SIGN)

#: Axis colours, RGBA. OURS (red/green/blue is the near-universal axis
#: convention and Rhino lets the user set these anyway).
AXIS_COLORS = (
    (0.86, 0.16, 0.16, 1.0),   # x
    (0.16, 0.68, 0.16, 1.0),   # y
    (0.22, 0.38, 0.92, 1.0),   # z
)

#: Colour of the handle under the cursor / being dragged. OURS.
HIGHLIGHT_COLOR = (1.0, 0.85, 0.1, 1.0)

#: Colour of the origin marker. OURS.
ORIGIN_COLOR = (0.15, 0.15, 0.15, 1.0)

_EPS = 1e-9


# ---------------------------------------------------------------------------
# tiny vector helpers (kept local so this module stays dependency-free, the
# same reason preview.py has its own)
# ---------------------------------------------------------------------------

def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(v, s):
    return (v[0] * s, v[1] * s, v[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _length(v):
    return math.sqrt(_dot(v, v))


def _unit(v):
    n = _length(v)
    if n < _EPS:
        return None
    return (v[0] / n, v[1] / n, v[2] / n)


# ---------------------------------------------------------------------------
# where the widget sits
# ---------------------------------------------------------------------------

def plane_axes(plane):
    """The widget's three axes: the CPlane's x, y and normal, in that order.

    Rhino: "Gumball widgets align to the construction plane xyz directions."
    """
    return (plane.xaxis, plane.yaxis, plane.zaxis)


def _rotate_vec(v, axis, angle):
    """`v` rotated by `angle` radians about the unit `axis` (Rodrigues)."""
    c, s = math.cos(angle), math.sin(angle)
    k = axis
    kxv = _cross(k, v)
    kdv = _dot(k, v)
    return tuple(v[i] * c + kxv[i] * s + k[i] * kdv * (1.0 - c) for i in range(3))


def rotate_frame(plane, axis_dir, angle):
    """`plane` with its x and y axes turned `angle` radians about `axis_dir` (origin
    kept). When auto-reset is off, this is the frame retained after a rotate drag.
    Returns a plane of the same type, or `plane` for no turn."""
    n = _unit(axis_dir)
    if n is None or abs(angle) < 1e-15:
        return plane
    x = _rotate_vec(tuple(plane.xaxis), n, angle)
    y = _rotate_vec(tuple(plane.yaxis), n, angle)
    return type(plane)(plane.origin, x, y)


#: AutoReset is off by default, so a rotated widget keeps its turned orientation.
DEFAULT_AUTORESET = False

#: OURS: within this angle of the view direction an axis points (almost) straight at
#: the viewer, so its arrow and scale handle project onto the widget origin -- see
#: `axis_faces_viewer`. The help states no threshold.
AXIS_HIDE_ANGLE_DEG = 12.0


def axis_faces_viewer(axis, view_dir, angle_deg=AXIS_HIDE_ANGLE_DEG):
    """True when `axis` lies within `angle_deg` of the view direction (either sign).

    An axis aimed at the viewer collapses onto the origin in screen space. Hiding its
    arrow and scale handle preserves access to the origin marker; movement along that
    projected direction is ambiguous anyway (`move_distance` returns zero).
    """
    a = _unit(axis) if axis else None
    v = _unit(view_dir) if view_dir else None
    if a is None or v is None:
        return False
    return abs(_dot(a, v)) >= math.cos(math.radians(float(angle_deg)))


def axis_edge_on(axis, view_dir, angle_deg=AXIS_HIDE_ANGLE_DEG):
    """True when `axis` lies within `angle_deg` of the SCREEN plane -- the rotate arc
    about it is then seen edge-on; see :func:`build`."""
    a = _unit(axis) if axis else None
    v = _unit(view_dir) if view_dir else None
    if a is None or v is None:
        return False
    return abs(_dot(a, v)) <= math.sin(math.radians(float(angle_deg)))


def bbox_center(plane, points):
    """The **CPlane-oriented** bounding-box centre of `points` (world), in world.

    This is `gumball.htm` to the letter: "The Gumball origin will be at the CPlane
    oriented bounding box center of the object." Measuring the box in world axes
    and then using its centre is a *different point* whenever the CPlane is not
    world XY, which is exactly the Front/Right case the project keeps being bitten
    by -- so the extents are taken in plane coordinates and the centre converted
    back.

    Returns None for an empty selection (there is no gumball to place).
    """
    pts = list(points or [])
    if not pts:
        return None
    local = [plane.from_world(p) for p in pts]
    lo = [min(c[i] for c in local) for i in range(3)]
    hi = [max(c[i] for c in local) for i in range(3)]
    mid = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    return plane.to_world(mid[0], mid[1], mid[2])


# ---------------------------------------------------------------------------
# the widget's drawable geometry
# ---------------------------------------------------------------------------

class Handle:
    """One grabbable part of the widget.

    `polylines` is what to DRAW (world points). `hot` is what to HIT-TEST against
    -- usually the same, but the arrowhead is not worth hit-testing separately and
    a square handle is easier to grab as its centre cross. Keeping them apart
    means a handle can be drawn ornately and still picked simply.
    """

    __slots__ = ("kind", "axis", "polylines", "hot", "color", "label", "solids")

    def __init__(self, kind, axis, polylines, hot, color, label, solids=None):
        self.kind = kind
        self.axis = axis
        self.polylines = polylines
        self.hot = hot
        self.color = color
        self.label = label
        #: FILLED 3-D parts as ``(p0, p1, p2, outward_normal)`` triangles: the arrow's
        #: cone tip plus the scale and origin cubes. The drawing side
        #: culls back faces and shades by the normal, so they read as solids.
        self.solids = list(solids or [])

    @property
    def key(self):
        """A stable identity for "the same handle", independent of rebuilds."""
        return (self.kind, self.axis)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Handle({self.kind}, axis={self.axis})"


#: Sides of the arrow's cone tip. OURS -- a rendering detail, like `arc_segments`.
CONE_SEGMENTS = 16


def _cone(base_centre, tip, radius, axis_dir, other_a, other_b, segments=CONE_SEGMENTS):
    """A closed cone as outward-facing triangles: the sides, then the base disc."""
    ring = []
    for i in range(segments):
        t = 2.0 * math.pi * i / segments
        ring.append(_add(base_centre, _add(_mul(other_a, radius * math.cos(t)),
                                           _mul(other_b, radius * math.sin(t)))))
    tris = []
    back = _mul(axis_dir, -1.0)
    for i in range(segments):
        a, b = ring[i], ring[(i + 1) % segments]
        n = _unit(_cross(_sub(b, a), _sub(tip, a)))
        mid = _mul(_add(a, b), 0.5)
        if _dot(n, _sub(mid, base_centre)) < 0.0:      # orient outward, whatever the
            n = _mul(n, -1.0)                           # handedness of other_a/other_b
        tris.append((a, b, tip, n))
        tris.append((base_centre, b, a, back))
    return tris


def _cube(centre, axes, half):
    """A cube of half-width `half` on three unit `axes`, as outward-facing triangles."""
    tris = []
    for k in range(3):
        u, v = axes[(k + 1) % 3], axes[(k + 2) % 3]
        for sign in (1.0, -1.0):
            n = _mul(axes[k], sign)
            c = _add(centre, _mul(n, half))
            du, dv = _mul(u, half), _mul(v, half)
            q = [_add(_add(c, du), dv), _add(_sub(c, du), dv),
                 _sub(_sub(c, du), dv), _sub(_add(c, du), dv)]
            tris.append((q[0], q[1], q[2], n))
            tris.append((q[0], q[2], q[3], n))
    return tris


def _sphere(centre, axes, radius, rings=6, segments=12):
    """A small shaded sphere as outward-facing triangles."""
    tris = []
    x, y, z = (_unit(a) for a in axes)
    rows = []
    for j in range(rings + 1):
        lat = -math.pi / 2.0 + math.pi * j / rings
        row = []
        for i in range(segments):
            lon = 2.0 * math.pi * i / segments
            n = _add(_mul(x, math.cos(lat) * math.cos(lon)),
                     _add(_mul(y, math.cos(lat) * math.sin(lon)),
                          _mul(z, math.sin(lat))))
            row.append((_add(centre, _mul(n, radius)), n))
        rows.append(row)
    for j in range(rings):
        for i in range(segments):
            a, b = rows[j][i], rows[j][(i + 1) % segments]
            c, d = rows[j + 1][(i + 1) % segments], rows[j + 1][i]
            if j > 0:
                tris.append((a[0], b[0], c[0], _unit(_add(a[1], _add(b[1], c[1])))))
            if j < rings - 1:
                tris.append((a[0], c[0], d[0], _unit(_add(a[1], _add(c[1], d[1])))))
    return tris


def _extrude_handle(origin, axis_dir, axes, size, color, axis, props=None):
    """Build the spherical Extrude dot on one axis."""
    p = PROPORTIONS if props is None else props
    centre = _add(origin, _mul(axis_dir, size * p["extrude_at"]))
    radius = size * p["extrude_radius"]
    hot = [[_sub(centre, _mul(a, radius)), _add(centre, _mul(a, radius))]
           for a in axes]
    return Handle(EXTRUDE_HANDLE, axis, [], hot, color, f"Extrude {'xyz'[axis]}",
                  solids=_sphere(centre, axes, radius))


def _arrow(origin, axis_dir, size, color, axis, other_a, other_b, props=None):
    """A move arrow: a shaft along the axis ending in a filled CONE.

    A cone remains legible from every view. Two flat barbs in one transverse axis
    collapse to a line when viewed along that axis.
    """
    p = PROPORTIONS if props is None else props
    tip = _add(origin, _mul(axis_dir, size * p["arrow_tip"]))
    back = _add(tip, _mul(axis_dir, -size * p["head_len"]))
    # the shaft stops at the cone's base, so its line is not drawn through the cone
    shaft = [origin, back]
    head_solid = _cone(back, tip, size * p["head_half"], axis_dir, other_a, other_b)
    # The shaft is DRAWN from the origin but only HOT from just past the origin marker:
    # every shaft touches the centre, so a hot line from the origin tied the origin
    # marker there and, being earlier in `build`, won -- the centre could not be
    # grabbed. The offset is twice the marker's half-width.
    hot_from = _add(origin, _mul(axis_dir, size * 2.0 * p["origin_half"]))
    return Handle(MOVE, axis, [shaft], [[hot_from, tip]], color,
                  f"Move {'xyz'[axis]}", solids=head_solid)


def _dashed_segment(a, b, dash_len):
    """``a`` -> ``b`` as a list of short 2-point polylines: a dotted leader.

    **MEASURED that these leaders exist**, from the `GumballSettings` handle list
    (commands/gumball.htm), which describes each handle's SHAPE:

      "ScaleHandle -- The small rectangles **and the dotted lines connecting to the
       Gumball origin**."
      "MenuHandle -- The small circle **and the dotted line connecting to the
       Gumball origin**."

    So the dotted leader is part of the handle in Rhino, not decoration. We drew
    neither. The dash LENGTH is OURS (`PROPORTIONS["leader_dash"]`).

    Dashed in WORLD space rather than screen space, which is correct here for a
    reason worth stating: the widget is converted to a constant pixel size every
    frame (`ui._gumball_size`), so a fixed fraction of that size is a fixed number
    of pixels at any zoom. Screen-space dashing would need the projection, which
    this module deliberately does not have.

    Returns [] for a non-positive dash length, so "turn it off" is expressible the
    way every other size in this module is.
    """
    if dash_len <= 0.0:
        return []
    span = _sub(b, a)
    total = math.sqrt(sum(c * c for c in span))
    if total <= _EPS:
        return []
    unit = _mul(span, 1.0 / total)
    out = []
    walked = 0.0
    period = dash_len * 2.0          # dash, then an equal gap
    while walked < total:
        end = min(walked + dash_len, total)
        out.append([_add(a, _mul(unit, walked)), _add(a, _mul(unit, end))])
        walked += period
    return out


def _scale_handle(origin, axis_dir, size, color, axis, other_a, other_b,
                  props=None):
    """A Scale1D handle: a small square on a dotted leader from the origin.

    MEASURED shape (commands/gumball.htm): "ScaleHandle -- The small rectangles and
    the dotted lines connecting to the Gumball origin." Both parts are drawn.

    The square sits on the OPPOSITE side of the axis from the Move arrow -- see
    :data:`SCALE_HANDLE_SIGN` for the two McNeel images that establish that, and
    :func:`scale_axis_direction` for why the direction is not computed here.
    """
    p = PROPORTIONS if props is None else props
    direction = scale_axis_direction(axis_dir)
    at = _add(origin, _mul(direction, size * p["scale_at"]))
    a = _mul(other_a, size * p["scale_half"])
    b = _mul(other_b, size * p["scale_half"])
    square = [
        _add(_add(at, a), b),
        _add(_sub(at, a), b),
        _sub(_sub(at, a), b),
        _sub(_add(at, a), b),
    ]
    square.append(square[0])
    leader = _dashed_segment(origin, at, size * p.get("leader_dash", 0.0))
    # hit-test the centre cross: a small closed square is fiddly to hit by edge.
    # The LEADER is deliberately not hot -- it runs the length of the axis and would
    # swallow clicks meant for the arrow underneath it.
    cross = [[_sub(at, a), _add(at, a)], [_sub(at, b), _add(at, b)]]
    # Use a filled cube with the same half-width; the square remains as its outline.
    box = _cube(at, (_unit(axis_dir), _unit(other_a), _unit(other_b)),
                size * p["scale_half"])
    return Handle(SCALE, axis, [square] + leader, cross, color,
                  f"Scale {'xyz'[axis]}", solids=box)


def _rotate_arc(origin, size, color, axis, from_dir, to_dir, props=None):
    """A rotate arc: a quarter circle in the plane the axis is normal to.

    Rotating *about* axis k moves points in the plane spanned by the other two
    axes, so that is where the arc is drawn -- grabbing it then feels like the
    motion it produces.
    """
    p = PROPORTIONS if props is None else props
    radius = size * p["arc_radius"]
    segs = int(p["arc_segments"])
    span = math.radians(p["arc_span"])
    pts = []
    for i in range(segs + 1):
        a = span * i / segs
        c, s = math.cos(a) * radius, math.sin(a) * radius
        pts.append(_add(origin, _add(_mul(from_dir, c), _mul(to_dir, s))))
    return Handle(ROTATE, axis, [pts], [pts], color, f"Rotate {'xyz'[axis]}")


def _menu_ball(origin, axes, size, proportions):
    """The menu ball -- Rhino's "bunny tail" -- on a dotted leader from the origin.

    MEASURED shape (commands/gumball.htm): "MenuHandle -- The small circle **and the
    dotted line connecting to the Gumball origin**." We drew the circle and not the
    leader.

    Placed off the x/y diagonal so it does not sit on any axis handle and cannot be
    grabbed by accident when aiming at one. Drawn as a closed polyline, like the
    rotate arcs, so the existing 2-D hit test works on it unchanged.

    `menu_at` is Rhino's `MenuBallDistance` (30 px), a RADIAL distance from the
    origin -- hence the 0.7071 on each of the two axes below.
    """
    at = proportions.get("menu_at", 0.375)
    radius = proportions.get("menu_radius", 0.0625) * size
    segments = int(proportions.get("menu_segments", 12))
    # centre: out along one of the two x/y diagonals. WHICH one matters -- the XY
    # Move-2D window icon is on +x+y, and putting the ball there made the two
    # intersect. See MENU_BALL_DIAGONAL.
    sx, sy = MENU_BALL_DIAGONAL
    dx = _mul(axes[0], sx * at * size * 0.7071)
    dy = _mul(axes[1], sy * at * size * 0.7071)
    centre = _add(_add(origin, dx), dy)
    points = []
    for n in range(segments + 1):
        t = 2.0 * math.pi * n / segments
        points.append(_add(centre,
                           _add(_mul(axes[0], radius * math.cos(t)),
                                _mul(axes[1], radius * math.sin(t)))))
    # The leader stops at the ball's edge rather than its centre, so it does not
    # draw a spoke across the circle.
    edge = _sub(centre, _mul(_add(dx, dy),
                             radius / max(_EPS, math.sqrt(sum(
                                 c * c for c in _add(dx, dy))))))
    leader = _dashed_segment(origin, edge,
                             size * proportions.get("leader_dash", 0.0))
    # Only the CIRCLE is hot. A hot leader would run back to the origin and
    # compete with the arrows, which is the opposite of the mis-click fix.
    return Handle(MENU, None, [points] + leader, [points], ORIGIN_COLOR,
                  "Gumball menu")


def _origin_marker(origin, axes, size, props=None):
    """The origin point -- "Drag the Gumball origin point to move the whole object"."""
    p = PROPORTIONS if props is None else props
    h = size * p["origin_half"]
    x, y, _z = axes
    a, b = _mul(x, h), _mul(y, h)
    square = [_add(_add(origin, a), b), _add(_sub(origin, a), b),
              _sub(_sub(origin, a), b), _sub(_add(origin, a), b)]
    square.append(square[0])
    cross = [[_sub(origin, a), _add(origin, a)], [_sub(origin, b), _add(origin, b)]]
    # The origin marker is a filled cube at the same size as its outline.
    box = _cube(origin, (_unit(x), _unit(y), _unit(_z)), h)
    return Handle(ORIGIN, None, [square], cross, ORIGIN_COLOR, "Move freely",
                  solids=box)


def plane_visible(normal, view_dir, angle_deg=PLANE_VISIBILITY_ANGLE_DEG):
    """Is the Move-2D handle of the plane with this `normal` visible?

    Rhino's "Plane visibility angle" rule, quoted in full at
    :data:`PLANE_VISIBILITY_ANGLE_DEG`: 0 degrees shows the plane only when the
    gumball is perfectly aligned to the view, 90 degrees always shows it, and the
    default is 50.

    "Aligned to the view" means looking STRAIGHT AT the plane, i.e. along its
    normal. So the quantity to test is the angle between the plane normal and the
    view direction, and the plane is shown while that angle is within
    `angle_deg`. The sign of the normal is irrelevant -- a plane seen from behind
    is just as face-on -- hence the absolute value.

    Returns False when either vector is degenerate: with no view direction there
    is no honest answer, and guessing "visible" would draw a handle Rhino hides.
    """
    n = _unit(normal) if normal else None
    v = _unit(view_dir) if view_dir else None
    if n is None or v is None:
        return False
    try:
        limit = float(angle_deg)
    except (TypeError, ValueError):
        return False
    limit = max(0.0, min(90.0, limit))
    cos_off = abs(_dot(n, v))
    cos_off = max(-1.0, min(1.0, cos_off))
    off_axis = math.acos(cos_off)
    # +_EPS so the documented endpoints behave: 90 always shows, 0 shows only
    # when exactly face-on (where off_axis is 0 but for floating-point dust).
    return off_axis <= math.radians(limit) + _EPS


def _plane_handle(origin, normal_axis, axes, size, color, props=None):
    """A Move-2D handle: a WINDOW ICON in the plane normal to `normal_axis`.

    MEASURED shape (commands/gumball.htm): "PlaneHandle -- The **window icons** on
    the XY, YZ, and XZ planes." A window icon is a small square divided into four
    panes by a cross -- which is exactly how McNeel draws it in
    `image/localization/gumballwidget.png`, where the callout even reads "Move 2D
    (Plane)".

    We used to draw a right-angled corner BRACKET here. That was a guess made when
    the only evidence was the two size settings' names, and it is not what Rhino
    shows.

    Sizes: `PlaneSize` (20 px) is the icon's full extent across, and
    `PlaneLocation` (40 px) is "the distance between a window icon and the Gumball
    origin" -- a radial distance, so it is divided between the two in-plane axes
    rather than applied to each. Reading the distance as running to the icon's
    CENTRE is DERIVED; the help does not say centre or near corner.

    `axis` on the returned Handle is the index of the axis the plane is NORMAL
    to, matching the `ROTATE` convention.
    """
    p = PROPORTIONS if props is None else props
    j, k = (normal_axis + 1) % 3, (normal_axis + 2) % 3
    u, v = axes[j], axes[k]
    # radial distance, split across the two in-plane axes -> /sqrt(2) each
    step = size * p["plane_at"] * 0.7071067811865476
    half = size * p["plane_size"] * 0.5
    centre = _add(origin, _add(_mul(u, step), _mul(v, step)))

    def at(du, dv):
        return _add(centre, _add(_mul(u, du), _mul(v, dv)))

    outer = [at(-half, -half), at(half, -half), at(half, half),
             at(-half, half), at(-half, -half)]
    # the cross that makes it a 2x2 WINDOW rather than a plain square
    mid_u = [at(-half, 0.0), at(half, 0.0)]
    mid_v = [at(0.0, -half), at(0.0, half)]
    label = "Move " + "".join("xyz"[i] for i in sorted((j, k)))
    # Hot over the cross: it spans the icon's full width and height through its
    # centre, so the whole pane area is within a pick tolerance of something,
    # whereas the outer square alone is only hittable on its edges.
    return Handle(PLANE, normal_axis, [outer, mid_u, mid_v],
                  [mid_u, mid_v], color, label)


def plane_delta(normal, start_world, cursor_world):
    """The translation of a Move-2D drag: motion with the normal component removed.

    "Drag a Move 2D handle to constrain movement on the plane"
    (commands/gumball.htm). The cursor moves in space; the object must move only
    within the plane, so the component along the plane normal is projected out.
    Returns a world (dx, dy, dz).
    """
    d = _sub(cursor_world, start_world)
    n = _unit(normal) if normal else None
    if n is None:
        return d
    along = _dot(d, n)
    return (d[0] - n[0] * along, d[1] - n[1] * along, d[2] - n[2] * along)


def drag_mode(kind, shift=False, ctrl=False):
    """What a drag on handle `kind` DOES, given the modifiers held.

    The whole table is quoted from commands/gumball.htm; nothing here is inferred:

    =============  ==========  ====================  =====================
    handle         plain       Shift                 Ctrl
    =============  ==========  ====================  =====================
    move arrow     move axis   (none documented)     Extrude "closed extrude"
    Move-2D plane  move plane  **Scale2D**           Extrude, on the plane
    scale handle   Scale1D     **Scale3D**           Extrude "inwards or outwards"
    rotate arc     rotate      (none documented)     straight loft between angles
    origin         free move   (none documented)     (none documented)
    =============  ==========  ====================  =====================

    Ctrl always means extrude, which we cannot do yet, so it maps to
    :data:`EXTRUDE` -- a mode deliberately NOT in :data:`IMPLEMENTED_MODES`, so
    the caller reports it rather than silently performing the plain drag.

    Shift on a handle with no documented Shift behaviour returns the plain mode:
    Rhino does not invent an action there, so neither do we.
    """
    if kind == EXTRUDE_HANDLE:
        if ctrl:
            return EXTRUDE_OPEN
        if shift:
            return EXTRUDE_BOTH
        return EXTRUDE_AXIS
    if ctrl and kind == MOVE:
        # The move arrow's extrusion is implemented by `bridge.gumball_extrude`.
        return EXTRUDE_AXIS
    if ctrl and kind in (PLANE, SCALE, ROTATE):
        return EXTRUDE
    if shift:
        if kind == PLANE:
            return SCALE_2D
        if kind == SCALE:
            return SCALE_3D
    return kind


#: Ctrl-drag on a move arrow extrudes along that axis. Other Ctrl handles remain
#: unavailable `EXTRUDE` modes.
EXTRUDE_AXIS = "extrude_axis"


def mode_is_implemented(mode):
    """True if :func:`drag_mode` returned something we can actually perform."""
    return mode in IMPLEMENTED_MODES or mode == EXTRUDE_AXIS


def mode_not_implemented_message(mode, kind=None):
    """Explain an unavailable Narwhal Gumball mode without substituting behavior."""
    if mode == EXTRUDE_OPEN:
        return "Gumball: Ctrl-drag Open Extrude from the dot is not implemented yet"
    if mode == EXTRUDE_BOTH:
        return "Gumball: Shift-drag Both Sides from the dot is not implemented yet"
    if mode == EXTRUDE:
        return "Gumball: Ctrl-drag Extrude is not implemented yet"
    if mode == CUT:
        return "Gumball: the Cut handle is not implemented yet"
    return f"Gumball: the {mode} mode is not implemented yet"


# ---------------------------------------------------------------------------
# numeric entry: click a handle, then type a value
#
# commands/gumball.htm, "Common operations": "Numeric input -- Click on any
# handle. Type a number for distance, rotation angle, or scale factor." And per
# handle, explicitly:
#   move handle   -- "Click on the move handle to type the moving distance."
#   rotate handle -- "Click on the rotate handle to type the rotation angle."
#   scale handle  -- "Click on a scale handle to type a scale factor."
#   Move-2D       -- "Shift + click on the Move 2D handle to type a scale factor."
# ---------------------------------------------------------------------------

NUMERIC_DISTANCE = "distance"
NUMERIC_ANGLE = "angle"
NUMERIC_FACTOR = "factor"


def numeric_kind(mode):
    """What a typed number MEANS for `mode`, or None if Rhino documents none.

    `None` is a real answer, not a gap to paper over. A plain click on a Move-2D
    handle is the case that matters: Rhino documents Shift+click there (a scale
    factor) but **not** an unmodified click, and a planar move has no single
    direction a lone number could describe. Inventing one would put geometry
    somewhere the user did not ask for, so the caller must refuse instead.
    """
    if mode == MOVE:
        return NUMERIC_DISTANCE
    if mode == ROTATE:
        return NUMERIC_ANGLE
    if mode in (SCALE, SCALE_2D):
        return NUMERIC_FACTOR
    if mode == SCALE_3D:
        # DERIVED, not quoted: the per-handle line covers a plain click on a
        # scale handle, and the "Common operations" line says "any handle", so a
        # Shift+click (Scale3D) taking a factor follows. Flagged because it is an
        # inference from two sentences rather than one statement.
        return NUMERIC_FACTOR
    return None


#: Prompt wording. **OURS** -- the help says a number is typed but never shows
#: the prompt, so these are our phrasing in Rhino's style, not measured strings.
NUMERIC_PROMPTS = {
    NUMERIC_DISTANCE: "Distance",
    NUMERIC_ANGLE: "Angle (deg)",
    NUMERIC_FACTOR: "Scale factor",
}


def numeric_prompt(mode, axis=None):
    """The command-line prompt for a numeric-entry click, or None.

    Includes which handle was clicked, because with four viewports and three axes
    the widget alone does not make that obvious.
    """
    kind = numeric_kind(mode)
    if kind is None:
        return None
    base = NUMERIC_PROMPTS[kind]
    if mode in (PLANE, SCALE_2D) and isinstance(axis, int):
        where = "".join("xyz"[i] for i in sorted(((axis + 1) % 3,
                                                  (axis + 2) % 3)))
    elif isinstance(axis, int):
        where = "xyz"[axis]
    else:
        where = None
    return f"{base} ({where}):" if where else f"{base}:"


def numeric_not_available_message(mode):
    """Explain why a clicked handle cannot take a typed number."""
    if mode == PLANE:
        return ("Gumball: a Move-2D handle takes a typed scale factor only with "
                "Shift -- Shift+click it instead")
    if mode == ORIGIN:
        return "Gumball: the origin handle does not take a typed value"
    return f"Gumball: the {mode} handle does not take a typed value"


# ---------------------------------------------------------------------------
# alignment: which frame the widget's axes follow (GumballAlignment)
#
# commands/gumball.htm, "Gumball menu", quoted:
#   Align to CPlane -- "Gumball widgets align to the construction plane xyz
#       directions. The Gumball origin will be at the CPlane oriented bounding box
#       center of the object."
#   Align to World  -- "Gumball widgets align to world xyz directions. The Gumball
#       origin will be at the World oriented bounding box center of the object."
#   Align to View   -- "Gumball widgets align to the view plane of the current
#       viewport."
#   Align to Object -- "Gumball widgets align to the object. The default alignment
#       is different for different object types."  <- not enough to implement
#
# Note the second sentence of CPlane and World: the alignment decides the frame
# the BOUNDING BOX is measured in as well as the axis directions. `bbox_center`
# already takes a frame, so returning the frame here is enough to get both.
# ---------------------------------------------------------------------------

def canonical_alignment(name):
    """Map any casing of an alignment name to its declared spelling, or None.

    Needed because the command line is case-insensitive but the declared keys are
    not plain capitalised words: ``str.capitalize()`` turns "CPlane" into
    "Cplane" and the lookup then misses. A dedicated fold avoids that whole class
    of bug -- and it bit immediately when this was written with `.capitalize()`.
    """
    key = str(name or "").strip().lower()
    for declared in ALIGNMENTS:
        if declared.lower() == key:
            return declared
    return None


def frame_for_alignment(name, cplane_frame=None, world_frame=None,
                        view_frame=None):
    """The frame the widget should use, or None if we cannot build it.

    The three candidate frames are passed IN rather than looked up: this module is
    deliberately dependency-free (no `cplane`, no `bpy`, no rhino3dm), and the
    caller already knows all three. Any object with `origin`/`xaxis`/`yaxis` works.

    Returns None for an alignment we do not implement, or when the caller could
    not supply the frame it needs -- the caller then reports rather than silently
    falling back, because a Gumball quietly using the wrong frame is exactly the
    sort of thing nobody notices until geometry lands in the wrong place.
    """
    key = canonical_alignment(name)
    if key == "CPlane":
        return cplane_frame
    if key == "World":
        return world_frame          # "world xyz directions"
    if key == "View":
        return view_frame
    return None                     # Object, or an unknown name


def alignment_is_implemented(name):
    """True if `name` is an alignment we can actually honour."""
    key = canonical_alignment(name)
    return bool(key and ALIGNMENTS.get(key, False))


def alignment_not_implemented_message(name):
    """Explain an unavailable alignment without silently using another frame."""
    if canonical_alignment(name) == "Object":
        return ("Gumball: Align to Object is not implemented yet because the "
                "per-object frame is unavailable")
    return f"Gumball: the {name} alignment is not implemented yet"


def build(origin, plane, size, view_dir=None, proportions=None,
          plane_angle_deg=PLANE_VISIBILITY_ANGLE_DEG, extrude=False):
    """Every handle of the widget, in world coordinates.

    `origin` is the widget centre (see :func:`bbox_center`), `plane` the active
    CPlane, `size` the widget length in WORLD units (the caller converts
    :data:`DEFAULT_SIZE_PX` at the pane's zoom).

    `view_dir` is the pane's view direction, needed ONLY by the Move-2D plane
    handles: Rhino hides those unless the plane is close to face-on (see
    :func:`plane_visible`). Omit it and no plane handles are produced -- without
    knowing where the camera looks there is no honest way to decide, and drawing
    them unconditionally would show handles Rhino hides.

    `proportions` overrides :data:`PROPORTIONS`. Rhino's documented way to hide a
    handle is to set its size to zero (options/modeling_aids_gumball.htm: "Set
    scale handle size to zero to turn off scale handles", and the same wording for
    axis thickness, arc thickness, plane size and menu ball size), so a zero here
    omits that handle rather than drawing a degenerate one.

    The merged table is passed DOWN to every handle builder. It did not used to be:
    each builder read the module-level :data:`PROPORTIONS` directly, so overriding
    any *geometry* value here silently did nothing and only the on/off gating below
    responded -- while this docstring claimed otherwise. A gate on the new leader
    dash caught it.

    Returns [] for a non-positive size so a degenerate zoom draws nothing rather
    than exploding.
    """
    if origin is None or plane is None or size is None or size <= 0:
        return []
    p = dict(PROPORTIONS)
    if proportions:
        p.update(proportions)
    axes = plane_axes(plane)
    # Rhino's "zero turns it off" rule, per handle family.
    want_move = p.get("arrow_tip", 0.0) > 0.0
    want_scale = p.get("scale_half", 0.0) > 0.0
    want_rotate = p.get("arc_radius", 0.0) > 0.0
    want_plane = p.get("plane_size", 0.0) > 0.0 and view_dir is not None

    handles = []
    for i in range(3):
        j, k = (i + 1) % 3, (i + 2) % 3
        color = AXIS_COLORS[i]
        # An axis aimed at the viewer collapses onto the origin on screen; omit its
        # arrow and scale handle so the origin remains selectable.
        end_on = view_dir is not None and axis_faces_viewer(axes[i], view_dir)
        if extrude and p.get("extrude_radius", 0.0) > 0.0 and not end_on:
            # Before the arrow: where their hit regions overlap, the visible dot wins.
            handles.append(_extrude_handle(origin, axes[i], axes, size, color, i, props=p))
        if want_move and not end_on:
            handles.append(_arrow(origin, axes[i], size, color, i,
                                  axes[j], axes[k], props=p))
        if want_scale and not end_on:
            handles.append(_scale_handle(origin, axes[i], size, color, i,
                                         axes[j], axes[k], props=p))
        # An arc whose axis lies in the screen plane is seen edge-on as a line through
        # the origin, making its drag unmeasurable
        # (the cursor ray runs parallel to its rotation plane)
        edge_on = view_dir is not None and axis_edge_on(axes[i], view_dir)
        if want_rotate and not edge_on:
            # the arc about axis i sweeps from axis j toward axis k
            handles.append(_rotate_arc(origin, size, color, i,
                                       axes[j], axes[k], props=p))
    if want_plane:
        for i in range(3):
            # the plane normal to axis i is only drawn when it is near face-on
            if plane_visible(axes[i], view_dir, plane_angle_deg):
                handles.append(_plane_handle(origin, i, axes, size,
                                             AXIS_COLORS[i], props=p))
    handles.append(_origin_marker(origin, axes, size, props=p))
    if p.get("menu_radius", 0.0) > 0.0:
        handles.append(_menu_ball(origin, axes, size, p))
    return handles


# ---------------------------------------------------------------------------
# hit-testing (2-D, because that is where the cursor lives)
# ---------------------------------------------------------------------------

def _point_segment_distance_2d(p, a, b):
    """Shortest distance from `p` to segment `a`-`b`, all 2-D."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    den = dx * dx + dy * dy
    if den < _EPS:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / den
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def polyline_distance_2d(point, polyline):
    """Shortest 2-D distance from `point` to a projected polyline.

    ``inf`` for a polyline with fewer than two usable points (a handle that
    projected off-screen must never look like the nearest thing to the cursor).
    """
    pts = [p for p in (polyline or []) if p is not None]
    if len(pts) < 2:
        return math.inf
    return min(_point_segment_distance_2d(point, a, b)
               for a, b in zip(pts, pts[1:]))


#: Pick tolerance for the MENU BALL specifically, in pixels.
#:
#: The menu ball is a small target near the axis handles. Applying the general
#: :data:`PICK_TOLERANCE_PX` would expand its hit region enough to catch clicks aimed
#: at nearby arrows.
#:
#: So the menu ball is picked against its own radius plus a small margin instead of
#: the general tolerance. The margin is OURS; the radius it is added to is Rhino's.
MENU_PICK_MARGIN_PX = 2.0


def menu_pick_tolerance_px(size_px=None):
    """Pick tolerance for the menu ball, in pixels.

    `size_px` is the widget's drawn size in pixels; it defaults to
    :data:`DEFAULT_SIZE_PX`. The ball scales with the widget, so its pick radius
    has to as well -- a fixed number would be too tight on a zoomed-in widget and
    too loose on a small one.

    Never returns more than :data:`PICK_TOLERANCE_PX`: the menu ball may be FUSSIER
    than the general tolerance but must never be greedier, or this would reintroduce
    the very bug it exists to remove, at large widget sizes.

    In the app `size_px` is always :data:`DEFAULT_SIZE_PX`, because `ui`'s
    `_gumball_size` converts exactly that many pixels through the pane's zoom to get
    the world size -- so the widget's drawn pixel size is constant by construction.
    The argument exists so the clamp above is testable, and so this stays correct if
    the size ever becomes a user setting (Rhino's `Radius`, which it is).
    """
    size = DEFAULT_SIZE_PX if size_px is None else float(size_px)
    radius = PROPORTIONS["menu_radius"] * size
    return min(radius + MENU_PICK_MARGIN_PX, PICK_TOLERANCE_PX)


def pick(projected, mouse, tolerance=PICK_TOLERANCE_PX, size_px=None):
    """Which handle the cursor is grabbing, or None.

    `projected` is a sequence of ``(handle, [polyline_2d, ...])`` -- the caller
    projects each handle's ``hot`` geometry to region pixels, using None for a
    point that would not project. Closest handle inside `tolerance` wins; exact
    ties break toward the earlier entry, so :func:`build`'s order is the
    tie-break and the ORIGIN marker (last, and tiny) can never steal a click from
    the axis handles it sits between. That tie-break is OURS -- the help gives no
    priority rule.

    The MENU ball is held to its own, tighter tolerance
    (:func:`menu_pick_tolerance_px`) rather than `tolerance`, because it is a 5 px
    target sitting 30 px from the origin and the general tolerance made it swallow
    clicks meant for the arrows. `size_px` is the widget's drawn pixel size, needed
    to scale that; omit it and the default size is assumed.
    """
    if mouse is None:
        return None
    menu_tol = menu_pick_tolerance_px(size_px)
    best, best_d, best_tol = None, math.inf, tolerance
    for handle, polylines in projected or []:
        d = min((polyline_distance_2d(mouse, pl) for pl in (polylines or [])),
                default=math.inf)
        tol = menu_tol if handle.kind == MENU else tolerance
        # Compare each candidate against ITS OWN tolerance before letting it win,
        # not afterwards. Picking the globally-nearest handle and only then testing
        # its tolerance would let the menu ball win on distance and then fail the
        # test, returning None -- a dead click rather than the arrow underneath.
        if d > tol:
            continue
        if d < best_d:
            best, best_d, best_tol = handle, d, tol
    if best is None or best_d > best_tol:
        return None
    return best


# ---------------------------------------------------------------------------
# drag math -- one function per handle kind
# ---------------------------------------------------------------------------

def move_distance(axis_start_2d, axis_end_2d, axis_world_length,
                  mouse_start, mouse_now):
    """How far along its axis a Move/Scale drag has travelled, in world units.

    Measured in SCREEN space on purpose. Resolving the cursor onto the CPlane (the
    way a point pick does) cannot move anything along the CPlane **normal** -- the
    z arrow would be dead in every axis-aligned pane. Projecting the mouse travel onto the axis *as drawn* works
    for all three axes, in parallel and perspective panes alike:

        world = (mouse_travel . axis_screen_unit) * world_length / screen_length

    Returns 0.0 when the axis projects to (nearly) nothing -- i.e. you are looking
    straight down it, where a drag has no unambiguous direction.
    """
    if None in (axis_start_2d, axis_end_2d, mouse_start, mouse_now):
        return 0.0
    sx = axis_end_2d[0] - axis_start_2d[0]
    sy = axis_end_2d[1] - axis_start_2d[1]
    screen_len = math.hypot(sx, sy)
    if screen_len < 1e-6 or axis_world_length is None or axis_world_length <= 0:
        return 0.0
    ux, uy = sx / screen_len, sy / screen_len
    travel = ((mouse_now[0] - mouse_start[0]) * ux
              + (mouse_now[1] - mouse_start[1]) * uy)
    return travel * axis_world_length / screen_len


def translation(axis_dir, distance):
    """The world delta for moving `distance` along `axis_dir`."""
    u = _unit(axis_dir)
    if u is None:
        return (0.0, 0.0, 0.0)
    return _mul(u, distance)


def snapped_origin_delta(widget_origin, snap_point):
    """The translation that puts the widget ORIGIN exactly on a snap.

    Snappy dragging moves the widget center to the snap point. Adding cursor-to-snap
    motion instead would leave the center offset by the initial mouse-down distance.
    """
    if widget_origin is None or snap_point is None:
        return (0.0, 0.0, 0.0)
    return _sub(snap_point, widget_origin)


def free_translation(start_world, cursor_world):
    """The world delta for an ORIGIN-handle drag.

    Rhino: "Drag the Gumball origin point to move the whole object in any
    direction." Both points come from the pane's CPlane pick, so the motion is
    free *within that plane* -- which is what "any direction" means for a cursor
    that lives on the construction plane.
    """
    if start_world is None or cursor_world is None:
        return (0.0, 0.0, 0.0)
    return _sub(cursor_world, start_world)


def rotate_angle(origin, axis_dir, start_world, cursor_world):
    """The signed angle (radians) a Rotate drag has swept about `axis_dir`.

    Both world points are intersections of the mouse ray with the **rotation
    plane** (the plane through `origin` whose normal is the axis), so the angle is
    exact rather than a screen-space approximation. The sign follows the
    right-hand rule about the axis, so dragging the arc the way it is drawn gives
    a positive angle.

    Returns 0.0 if either point coincides with the origin (no defined angle).
    """
    n = _unit(axis_dir)
    if n is None or origin is None or start_world is None or cursor_world is None:
        return 0.0
    a = _sub(start_world, origin)
    b = _sub(cursor_world, origin)
    # drop any component along the axis: only motion IN the rotation plane counts
    a = _sub(a, _mul(n, _dot(a, n)))
    b = _sub(b, _mul(n, _dot(b, n)))
    la, lb = _length(a), _length(b)
    if la < _EPS or lb < _EPS:
        return 0.0
    cos_t = max(-1.0, min(1.0, _dot(a, b) / (la * lb)))
    angle = math.acos(cos_t)
    if _dot(_cross(a, b), n) < 0.0:
        angle = -angle
    return angle


def scale_factor(origin, axis_dir, start_world, cursor_world, minimum=1e-4):
    """The Scale1D factor for a drag, about the gumball origin.

    Rhino: "Drag a scale handle to scale in one dimension along the arrow. The
    Gumball origin is the base point of scaling." So the factor is the ratio of
    the cursor's distance along the axis to where the drag started:

        factor = (cursor . axis) / (start . axis)

    Clamped at `minimum` and never negative: dragging past the origin in Rhino
    flattens the object rather than mirroring it ("The object will be made flat
    in the scaling direction"). Returns 1.0 (no change) when the start had no
    extent along the axis, because there is no ratio to take.
    """
    n = _unit(axis_dir)
    if n is None or origin is None or start_world is None or cursor_world is None:
        return 1.0
    d0 = _dot(_sub(start_world, origin), n)
    d1 = _dot(_sub(cursor_world, origin), n)
    if abs(d0) < _EPS:
        return 1.0
    f = d1 / d0
    return max(minimum, f)


def scale_from_distance(start_distance, travelled, minimum=1e-4):
    """Scale1D factor from an axis distance and how far the drag moved along it.

    The screen-space sibling of :func:`scale_factor`, for the same reason
    :func:`move_distance` is screen-space: a scale handle on the CPlane normal
    cannot be dragged by a cursor pinned to the CPlane. `start_distance` is where
    the handle sat along the axis (world units from the origin).
    """
    if start_distance is None or abs(start_distance) < _EPS:
        return 1.0
    return max(minimum, (start_distance + travelled) / start_distance)


# ---------------------------------------------------------------------------
# what a drag reports on the command line
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# COPY WHILE DRAGGING  (commands/gumball.htm)
#
#   "Copy - Tap Alt -- While dragging, tap Alt to make a copy of the object.
#    Tap Alt again to leave copy mode."
#
# It is a TOGGLE held for the duration of one drag, not a modifier you hold: the
# help says "tap", and says tapping again leaves the mode.  The same wording
# appears under the Move handle, the Move 2D handle and the Extrude handle.
# ---------------------------------------------------------------------------

#: Handle kinds whose drag is a pure TRANSLATION, so leaving a copy behind is just
#: the negative of what has been applied so far. Copy on a rotate or a scale would
#: need the inverse of that transform instead, which is a different job -- so those
#: report rather than doing something approximate.
COPYABLE_KINDS = frozenset({MOVE, PLANE, ORIGIN})


def copy_is_available(kind):
    """Whether Alt-tap copy works for a drag of `kind`."""
    return kind in COPYABLE_KINDS


def copy_not_available_message(kind):
    """Why Alt-tap copy is refused for `kind`. Rule 4: say it, do not approximate."""
    return (f"Gumball: copy while dragging is only implemented for the move "
            f"handles, not for {kind}")


def toggle_copy(was_on):
    """Tap Alt during a drag. Returns the new copy-mode state.

    Trivial on purpose: the VALUE of having it here is that the rule -- tap to
    enter, tap again to leave -- is stated and tested in one place rather than
    implied by an `if` in the middle of the modal.
    """
    return not was_on


#: How long Alt must be DOWN for its release to leave copy mode again.
#:
#: Alt supports both gestures: a tap toggles copy mode, while a hold is momentary.
#:
#: A tap and a hold cannot be told apart at PRESS time, only at release, by how long
#: the key was down. That is the same shape `navigate.rmb_release_action` already uses
#: for the right button (click vs drag, decided at release), which is why this lives
#: next to the toggle rather than being improvised in the modal.
#:
#: The THRESHOLD is OURS -- Rhino publishes no number. 250 ms deliberately equals
#: `navigate.DEFAULT_CONTEXT_MENU_DELAY_MS` so the app has ONE idea of how long a tap
#: is, rather than two that can drift apart.
ALT_HOLD_MS = 250


def alt_release_ends_copy(held_ms, hold_ms=ALT_HOLD_MS):
    """True when RELEASING Alt should leave copy mode.

    A HOLD (``held_ms >= hold_ms``) is momentary, so releasing ends it. A TAP is a
    toggle, so releasing changes nothing and copy mode persists until the next tap.
    Non-numeric input is treated as a tap -- the safe answer, because it leaves the
    user in the mode they can see and get out of by tapping.
    """
    try:
        held = float(held_ms)
    except (TypeError, ValueError):
        return False
    return held >= float(hold_ms)


# ---------------------------------------------------------------------------
# DRAG STRENGTH  (commands/gumball.htm, Gumball menu)
#
#   "Drag Strength -- Sets the amount of the gumball drag as a percentage of mouse
#    movement to control the speed of the gumball movement. The default 100% moves
#    the gumball consistently with the mouse. To fine-tune placing objects (such as
#    with control point editing) set the strength lower, so large hand/mouse
#    movements result in smaller gumball changes. When the control is closed, drag
#    strength is reset to 100%."
# ---------------------------------------------------------------------------

#: MEASURED: "The default 100%".
DEFAULT_DRAG_STRENGTH = 100.0

#: MEASURED: the help gives a percentage with no stated range, but 0 would freeze
#: the widget and a negative would invert it, neither of which the help describes.
#: Clamping at 1..1000 is OURS.
MIN_DRAG_STRENGTH = 1.0
MAX_DRAG_STRENGTH = 1000.0


def clamp_drag_strength(percent):
    """A usable drag strength, or None when `percent` is not a number.

    Refuses rather than clamping silently at the ends? No -- it CLAMPS, because the
    help presents this as a slider and a slider has ends. What it refuses is
    nonsense (None, text), because that is a caller bug rather than a user choice.
    """
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return None
    return max(MIN_DRAG_STRENGTH, min(MAX_DRAG_STRENGTH, value))


def apply_drag_strength(delta, percent=DEFAULT_DRAG_STRENGTH):
    """Scale a mouse delta by the drag strength.

    At the documented default of 100 this is the identity, so the ordinary drag is
    bit-for-bit what it was before this existed -- which is the property that makes
    the feature safe to add.
    """
    scale = (clamp_drag_strength(percent) or DEFAULT_DRAG_STRENGTH) / 100.0
    if isinstance(delta, (int, float)):
        return delta * scale
    return tuple(c * scale for c in delta)


# ---------------------------------------------------------------------------
# RELOCATE / RESET  (commands/gumball.htm, Gumball menu)
#
#   "Relocate Gumball -- Drag to relocate gumball widget away from its default
#    position."
#   "Reset Gumball -- Restores the default placement and orientation for the
#    gumball."
#   "Move Gumball -- Double-click on an arrow to relocate Gumball along the
#    direction."  /  "Move gumball planar - Double click -- Double-click on a
#    Move 2D handle to relocate Gumball on the plane."
#
# So relocation is an OFFSET from the computed default (the bounding-box centre),
# and it survives until Reset. Keeping it as an offset rather than an absolute point
# matters: the default moves when the selection changes, and Rhino's relocated
# gumball does not jump back to the box centre on its own.
# ---------------------------------------------------------------------------

ZERO_OFFSET = (0.0, 0.0, 0.0)


def relocated_origin(default_origin, offset):
    """The widget origin after a relocation `offset`."""
    if not offset or not any(offset):
        return tuple(default_origin)
    return (default_origin[0] + offset[0],
            default_origin[1] + offset[1],
            default_origin[2] + offset[2])


def relocate_offset(current_offset, delta):
    """Add a relocation `delta` to the offset already in force."""
    cur = current_offset or ZERO_OFFSET
    return (cur[0] + delta[0], cur[1] + delta[1], cur[2] + delta[2])


def is_relocated(offset):
    """True when the widget has been moved off its default position."""
    return bool(offset) and any(abs(c) > 1e-12 for c in offset)


# ---------------------------------------------------------------------------
# THE GUMBALL MENU  (the "menu ball" / bunny tail)
#
#   "Click on the circle to show the Gumball menu. The same menu can be accessed
#    from the Gumball pane on the status bar."
#   "The alternative way of showing the Gumball menu: Right-click and hold on any
#    part of the Gumball widget. When the page icon appears, release the right
#    mouse button."  (and the timing is the Options > Mouse right-button delay,
#    which `navigate.rmb_release_action` already implements)
#
# Every entry below is quoted from the help's own "Gumball menu" list, in its order.
# `implemented` is our honest record; a False entry is SHOWN and reports itself,
# because a menu that hides what Rhino offers misrepresents the widget.
# ---------------------------------------------------------------------------

#: (label, key, implemented). The order is the help's.
MENU_ENTRIES = (
    ("Relocate Gumball", "relocate", True),
    ("Reset Gumball", "reset", True),
    ("AutoReset Gumball", "autoreset", True),
    ("Gumball On", "on", True),
    ("Gumball Off", "off", True),
    ("Align to CPlane", "align_cplane", True),
    ("Align to Object", "align_object", False),
    ("Align to World", "align_world", True),
    ("Align to View", "align_view", True),
    ("Snappy Dragging", "snappy", True),
    ("Smooth Dragging", "smooth", True),
    ("Merge faces after extrude", "merge_faces", False),
    ("Rotate View Around Gumball", "rotate_view_around", False),
    ("Drag Strength", "drag_strength", True),
    ("Settings...", "settings", False),
)

MENU_KEYS = tuple(key for _label, key, _impl in MENU_ENTRIES)


def menu_entry(key):
    """``(label, key, implemented)`` for `key`, or None."""
    for entry in MENU_ENTRIES:
        if entry[1] == key:
            return entry
    return None


def menu_is_implemented(key):
    entry = menu_entry(key)
    return bool(entry and entry[2])


def menu_not_implemented_message(key):
    """What to report for a menu entry we do not have."""
    entry = menu_entry(key)
    if entry is None:
        return f"Gumball: {key!r} is not a Gumball menu entry"
    return f"Gumball: {entry[0]} is not implemented yet"


# ---------------------------------------------------------------------------
# DRAGGING AND OBJECT SNAPS
#
#   "Snappy Dragging -- Object snaps are used while dragging."
#   "Smooth Dragging -- Object snaps are ignored while dragging."
# ---------------------------------------------------------------------------

#: Snappy dragging is the default. The setting is a two-state choice between using
#: object snaps and ignoring them, so one boolean represents it.
DEFAULT_SNAPPY_DRAGGING = True


#: The drag modes where "Snaps to object snap locations" has a meaning we can
#: honour. All four track a real world POINT as the drag runs, so a snap simply
#: replaces that point and the arithmetic downstream is untouched:
#:
#:   ORIGIN  free movement -- the snapped point IS the new location
#:   ROTATE  the angle is taken from a point on the rotation plane
#:
#: MOVE axis arrows and PLANE squares do not snap. Axis snapping would project a snap
#: location onto the arrow and cause jumps; planar handles intentionally drag smoothly.
#:
#: The three SCALE modes are deliberately absent, and that is a SCOPE limit stated
#: out loud rather than an oversight: a scale drag consumes a RATIO and an object
#: snap supplies a LOCATION, so there is no honest reading of "snap to" for one.
#: Rhino's option text says nothing about scaling either. Deciding it here, in one
#: pure place, is what stops the UI from quietly snapping a scale.
SNAPPY_MODES = (ORIGIN, ROTATE)


def snappy_applies(mode):
    """True when "Snappy gumball" affects drag `mode`. See :data:`SNAPPY_MODES`."""
    return mode in SNAPPY_MODES


def project_onto_axis(origin, axis_dir, point):
    """How far `point` lies along `axis_dir` from `origin`, signed. None if degenerate.

    An object snap hands back a world POINT, but a move-arrow drag is
    one-dimensional: the only thing it can consume is a distance along the arrow.
    So the snapped point is projected onto the axis line and the component ALONG
    the axis is what the drag uses -- exactly the constraint the arrow represents.
    Any part of the snap off the axis is discarded, which is why snapping a
    constrained move lands on the axis rather than on the snap point itself.

    Returns None for a zero-length axis: there is no "along" to measure, and
    returning 0.0 would silently read as "the snap is at the origin".
    """
    n = _unit(axis_dir) if axis_dir else None
    if n is None:
        return None
    return _dot(_sub(point, origin), n)


def relocate_drag_delta(kind, axis_dir, start_world, cursor_world):
    """Where a relocate gesture moves the widget, or None if the handle has none.

    MEASURED (commands/gumball.htm), both entries verbatim:

      "Move Gumball -- Double-click on an arrow to relocate Gumball along the
       DIRECTION. Move the mouse and click, or type a distance and press Enter."
      "Move gumball planar - Double click -- Double-click on a Move 2D handle to
       relocate Gumball ON THE PLANE."

    So the gesture is constrained exactly as that handle's ordinary drag is: an
    arrow contributes one axis, a Move-2D handle the plane it names. `axis_dir` is
    the arrow direction for :data:`MOVE` and the plane NORMAL for :data:`PLANE`,
    matching what the drag state already stores for each.

    Every other handle returns None. The help documents no relocate gesture for the
    rotation arcs, the scale handles or the origin, so falling back to a free move
    would be an invention -- and an invisible one, since it would look like it
    worked.
    """
    if kind == MOVE:
        d = project_onto_axis(start_world, axis_dir, cursor_world)
        n = _unit(axis_dir) if axis_dir else None
        if d is None or n is None:
            return None
        return (n[0] * d, n[1] * d, n[2] * d)
    if kind == PLANE:
        return plane_delta(axis_dir, start_world, cursor_world)
    return None


# ---------------------------------------------------------------------------
# TOOLTIPS  (commands/gumball.htm)
#
#   "To show tooltip for handle actions -- Point the mouse cursor to a handle to
#    show the tooltip for actions and modifier keys. The actions and modifier keys
#    can vary based on the selected object type."
#
# Each line below is transcribed from that page's per-handle sections. Only actions
# WE implement are listed as actions; the modifier keys Rhino documents but we do
# not perform are named as not implemented, so the tooltip cannot promise more than
# the widget does.
# ---------------------------------------------------------------------------

TOOLTIPS = {
    MOVE: ("Drag to move along the axis",
           "Click to type a distance (Shift reverses)",
           "Tap Alt while dragging to copy",
           "Double-click to relocate the Gumball along the axis",
           "Ctrl to extrude - not implemented"),
    PLANE: ("Drag to move on the plane",
            "Shift to Scale2D",
            "Tap Alt while dragging to copy",
            "Double-click to relocate the Gumball on the plane",
            "Ctrl to extrude - not implemented"),
    ROTATE: ("Drag to rotate about the axis",
             "Click to type an angle",
             "Ctrl to loft between angles - not implemented"),
    SCALE: ("Drag to scale along the axis",
            "Shift to Scale3D",
            "Click to type a scale factor"),
    ORIGIN: ("Drag to move freely",
             "Tap Alt while dragging to copy"),
    EXTRUDE_HANDLE: ("Drag to extrude along the axis",
                     "Ctrl Open Extrude - not implemented yet",
                     "Shift Both Sides - not implemented yet"),
    MENU: ("Click to open the Gumball menu",),
}


def tooltip(kind):
    """The tooltip lines for a handle `kind`, or () when it has none."""
    return tuple(TOOLTIPS.get(kind, ()))


def drag_label(kind, axis, amount):
    """Return an unambiguous one-line readout for a finished drag."""
    name = "xyz"[axis] if isinstance(axis, int) else "free"
    if kind == MOVE:
        return f"Gumball: moved {amount:g} along {name}"
    if kind == SCALE:
        return f"Gumball: scaled {amount:g} along {name}"
    if kind == ROTATE:
        return f"Gumball: rotated {math.degrees(amount):g} deg about {name}"
    if kind == PLANE:
        # `axis` is the axis the plane is NORMAL to, so name the two it spans.
        if isinstance(axis, int):
            spanned = "".join("xyz"[i] for i in
                              sorted(((axis + 1) % 3, (axis + 2) % 3)))
        else:
            spanned = "plane"
        return f"Gumball: moved {amount:g} in the {spanned} plane"
    if kind == SCALE_2D:
        if isinstance(axis, int):
            spanned = "".join("xyz"[i] for i in
                              sorted(((axis + 1) % 3, (axis + 2) % 3)))
        else:
            spanned = "plane"
        return f"Gumball: scaled {amount:g} in the {spanned} plane"
    if kind == SCALE_3D:
        return f"Gumball: scaled {amount:g} in three dimensions"
    if kind == ORIGIN:
        return "Gumball: moved freely on the construction plane"
    return f"Gumball: {kind}"
