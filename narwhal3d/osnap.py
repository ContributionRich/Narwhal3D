"""the reference CAD application's object snaps (Osnap). **No `bpy`, no rhino3dm** -- pure, so testable.

Why this module exists
---------------------
Object snaps are what makes CAD drawing *accurate*. From
``user_interface/object_snaps.htm``:

    "The object snaps constrain the marker to an exact location on an object such
    as the end of a line or the center of a circle." ... "When an object snap is
    active, moving the cursor near a specified point on an object causes the
    marker to jump to that point."

Without them every point is wherever the pixel happened to be, so no two pieces
of geometry ever actually meet. Everything else we build on top of picking rests
on this.

The split
---------
The *decision* -- "given these objects, this cursor, these enabled snap modes and
this construction plane, what point should the marker jump to?" -- is pure
geometry on plain tuples, and lives here so every rule is unit-tested without
launching Blender. `bridge.py` (which owns rhino3dm) reduces real geometry to the
neutral :class:`SnapTarget` records this module consumes, and `ui.py` supplies the
cursor and draws the marker.

The snap radius is in PIXELS
----------------------------
``options/modeling_aids.htm``: *"Snap radius ___ pixels -- Sets the object snap
activation area radius in pixels."* A pure module cannot know about pixels, so
callers pass a **world-space** radius they derived from the view scale, and
:data:`DEFAULT_RADIUS_PX` is offered for that conversion. This matters: a snap
radius in world units would grow and shrink as you zoom, which is not what the reference CAD application
does.

What is implemented, and what is honestly not
---------------------------------------------
:data:`PERSISTENT` is the exact list the help gives as able to persist. Of those,
:data:`IMPLEMENTED` are real; :data:`NOT_IMPLEMENTED` are recognised, reported as
unimplemented, and never silently treated as something else.

Two documented facts recorded here because they constrain future work:

* `Perp` and `Tan` are *"not effective for the first point of a command"* -- they
  need a previous point to be perpendicular/tangent **to**, so they can only ever
  work from the second pick onward. That previous point is passed to
  :func:`best`/:func:`candidates` as the keyword-only `base`; with no `base` they
  contribute nothing (they are never silently swapped for another snap).
* `Project` *"projects any object snap to the current construction plane"*.

An INFERENCE, flagged as such
-----------------------------
The help says *"As the cursor passes over an object, the appropriate object snap
displays"* but never gives an explicit priority order for when several snaps are
in range. `Near` snaps to *any* point along a curve, so it is almost always the
closest candidate and would drown out `End`/`Mid`/`Cen` entirely if it competed on
distance. We therefore treat `Near` as a **fallback**: it is only used when no
discrete snap is within the radius. That matches how the reference CAD application behaves in practice but
is **not** quoted from the docs -- see :data:`NEAR_IS_FALLBACK`. If it is ever
shown to be wrong, this is the one line to change.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# the snap modes, named exactly as the reference CAD application's Osnap control names them
# ---------------------------------------------------------------------------

CEN = "Cen"
END = "End"
INT = "Int"
KNOT = "Knot"
MID = "Mid"
NEAR = "Near"
PERP = "Perp"
POINT = "Point"
QUAD = "Quad"
TAN = "Tan"
VERTEX = "Vertex"

#: The snaps the help lists as able to PERSIST, in its own order: "Only Cen, End,
#: Int, Knot, Mid, Near, Perp, Point, Quad, Tan, and Vertex object snaps can
#: persist." (user_interface/object_snaps.htm)
PERSISTENT = (CEN, END, INT, KNOT, MID, NEAR, PERP, POINT, QUAD, TAN, VERTEX)

#: Snaps this module really computes. `Perp`/`Tan` compute only when a previous
#: point (`base`) is supplied -- they contribute nothing on a command's first
#: point (see :data:`NEEDS_PREVIOUS_POINT` and :func:`candidates`), which is
#: exactly what the help requires.
IMPLEMENTED = (CEN, END, INT, KNOT, MID, NEAR, PERP, POINT, QUAD, TAN)

#: Recognised but not computed. Never silently substituted for something else.
#: `Vertex` needs mesh/SubD objects, which we do not have at all yet, so it is
#: honestly reported as not implemented.
NOT_IMPLEMENTED = (VERTEX,)

#: Snaps that cannot work on a command's FIRST point, per the help.
NEEDS_PREVIOUS_POINT = (PERP, TAN)

#: See the module docstring -- this is an inference, not a documented rule.
NEAR_IS_FALLBACK = True

#: the reference CAD application sets this in Options > Modeling Aids and the help does not state the
#: shipped default, so this value is OURS and is labelled as such rather than
#: presented as the reference CAD application's.
DEFAULT_RADIUS_PX = 10.0

#: Which snaps start enabled. The help does not document the reference CAD application's shipped set, so
#: this is our choice: every implemented DISCRETE snap, with `Near` off because it
#: matches anywhere along a curve and is surprising when unrequested.
DEFAULT_ENABLED = (CEN, END, MID, POINT, QUAD)


def is_implemented(mode: str) -> bool:
    """True when `mode` is a snap we actually compute."""
    return mode in IMPLEMENTED


def normalize(mode: str):
    """Accept any capitalisation of a snap name -> its canonical form, or None.

    the reference CAD application's command line is case-insensitive, so `end`, `END` and `End` are the
    same snap.
    """
    if not mode:
        return None
    low = str(mode).strip().lower()
    for name in PERSISTENT:
        if name.lower() == low:
            return name
    return None


# ---------------------------------------------------------------------------
# what the engine needs to know about one object
# ---------------------------------------------------------------------------

class SnapTarget:
    """One object, reduced to the facts the snap engine needs.

    Deliberately neutral: plain tuples, no rhino3dm. `bridge.snap_targets`
    produces these from real geometry, which keeps every rule in here testable
    against hand-built inputs.

    points
        A dense sample of the curve in WORLD coordinates, in order. Used for
        `Near`, for the arc-length midpoint, and for `Quad`.
    vertices
        The curve's END and CORNER points -- endpoints, interior polyline
        vertices, and the seam of a closed curve. This is exactly what `End`
        snaps to: "the end of a curve ... interior vertices of polylines and
        joined curves, the seam point on closed curves".
    knots
        The WORLD locations of a curve's interior NURBS knots -- what `Knot`
        snaps to: "a knot on a curve or surface". A deliberately NEW field, not
        `vertices`: an interior knot is a point along a smooth curve, not a
        corner, so mixing it into `vertices` would make it an `End` snap too.
        Empty (the default) for objects that expose no knots.
    center
        The analytic centre for a circle/arc/ellipse, or the average of the
        corners of a closed planar polyline -- both documented `Cen` cases. None
        when the object has no meaningful centre.
    closed
        Whether the curve closes on itself.
    is_point
        True for a point object, whose single location is what `Point` snaps to.
    curve
        Optional escape hatch: the real rhino3dm curve, or None. When
        present, `Near` solves the EXACT closest point on it
        (`nearest.closest_point_on_curve`) instead of the nearest point on the
        display `points` polyline, which is wrong by a segment's sagitta and
        worsens as you zoom. Defaults to None so callers that provide no curve keep
        taking the polyline path unchanged. This is the one place rhino3dm may be
        reached, and only lazily, so `osnap` remains importable without rhino3dm.
    locked
        Whether this object is locked. A plain bool, the same shape as
        `curve`: the engine excludes locked targets unless `SnapToLocked` is on
        (`best(..., snap_to_locked=True)`). MEASURED default is False -- the reference CAD application
        ships a *setting that turns snapping to locked objects ON*
        (options/modeling_aids.htm), which is proof the default is OFF. Populated
        by `bridge.snap_targets` from the object's Mode; hand-built pure tests
        leave it False and so are unaffected.
    """

    __slots__ = ("points", "vertices", "knots", "center", "closed", "is_point",
                 "name", "curve", "locked", "_mids", "_quad")

    def __init__(self, points=(), vertices=(), knots=(), center=None,
                 closed=False, is_point=False, name="", curve=None, locked=False):
        self.points = tuple(tuple(float(c) for c in p) for p in points)
        self.vertices = tuple(tuple(float(c) for c in p) for p in vertices)
        self.knots = tuple(tuple(float(c) for c in p) for p in knots)
        self.center = None if center is None else tuple(float(c) for c in center)
        self.closed = bool(closed)
        self.is_point = bool(is_point)
        self.name = name
        self.curve = curve
        self.locked = bool(locked)
        self._mids = None            # `_target_mids`' answer, once computed
        self._quad = None            # (plane key, `quadrant_points`' answer)

    @classmethod
    def from_floats(cls, points, vertices, center=None, closed=False, name="",
                    curve=None, locked=False):
        """The same target, for a caller whose points are ALREADY tuples of three
        Python floats (`bridge.curve_snap_target`): skips the per-coordinate `float()`
        normalization. On hundreds of targets this avoids tens of thousands of
        generator calls and significant pointer-move latency."""
        t = cls.__new__(cls)
        t.points = tuple(points)
        t.vertices = tuple(vertices)
        t.knots = ()
        t.center = center
        t.closed = bool(closed)
        t.is_point = False
        t.name = name
        t.curve = curve
        t.locked = bool(locked)
        t._mids = None
        t._quad = None
        return t

    @classmethod
    def point(cls, location, name=""):
        """A point object -- the `Point` snap's target."""
        return cls(points=(location,), vertices=(location,), is_point=True,
                   name=name)

    def __repr__(self):  # pragma: no cover - debugging aid
        kind = "point" if self.is_point else "curve"
        return (f"SnapTarget({kind}, {len(self.points)} samples, "
                f"{len(self.vertices)} vertices, {len(self.knots)} knots, "
                f"center={self.center}, closed={self.closed}, name={self.name!r})")


class Snap:
    """A candidate the marker could jump to: a world point plus which snap found it."""

    __slots__ = ("point", "mode", "distance", "target")

    def __init__(self, point, mode, distance, target=None):
        self.point = tuple(float(c) for c in point)
        self.mode = mode
        self.distance = float(distance)
        self.target = target

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"Snap({self.mode}, "
                f"({self.point[0]:.4f}, {self.point[1]:.4f}, {self.point[2]:.4f}), "
                f"d={self.distance:.4f})")

    def __eq__(self, other):
        return (isinstance(other, Snap) and self.mode == other.mode
                and all(abs(a - b) < 1e-9 for a, b in zip(self.point, other.point)))


# ---------------------------------------------------------------------------
# persistent settings -- the Osnap control
# ---------------------------------------------------------------------------

class SnapSettings:
    """The state of the reference CAD application's Osnap control: which snaps are on, and Disable.

    Pure state + the documented rules for reading it, so the "which snaps apply
    right now" question has exactly one answer that tests can pin.
    """

    def __init__(self, enabled=DEFAULT_ENABLED, disabled=False, project=False,
                 radius_px=DEFAULT_RADIUS_PX, snap_to_locked=False):
        self.enabled = set()
        for mode in enabled or ():
            name = normalize(mode)
            if name:
                self.enabled.add(name)
        #: the Osnap control's `Disable` button: "Clicking Disable suspends all
        #: enabled persistent object snaps temporarily."
        self.disabled = bool(disabled)
        #: "The Project checkbox ... projects any object snap to the current
        #: construction plane."
        self.project = bool(project)
        self.radius_px = float(radius_px)
        #: Whether `SnapToLocked` allows object snaps to use locked objects.
        #: MEASURED default is OFF -- the reference CAD application ships a setting "Snap to locked
        #: objects" that turns this ON (options/modeling_aids.htm), and a setting
        #: that enables a behaviour is proof the behaviour is off by default. So
        #: today's engine must EXCLUDE locked targets unless this is set.
        self.snap_to_locked = bool(snap_to_locked)

    def set(self, mode, on=True):
        """Turn one snap on or off. Returns the canonical name, or None if unknown."""
        name = normalize(mode)
        if name is None:
            return None
        if on:
            self.enabled.add(name)
        else:
            self.enabled.discard(name)
        return name

    def toggle(self, mode):
        """Flip one snap. Returns (name, is_now_on) or (None, False)."""
        name = normalize(mode)
        if name is None:
            return None, False
        if name in self.enabled:
            self.enabled.discard(name)
            return name, False
        self.enabled.add(name)
        return name, True

    def only(self, mode):
        """Enable just this snap, disabling the rest.

        The help's right-click behaviour: "Right-click an object snap. The object
        snap is enabled and all the recently enabled object snaps are disabled."
        """
        name = normalize(mode)
        if name is None:
            return None
        self.enabled = {name}
        return name

    def active(self, suspend=False, first_point=False):
        """The snaps that apply to THIS pick, as a sorted tuple.

        `suspend` is the Alt key, whose documented behaviour is to INVERT
        `Disable` rather than simply to turn snaps off: "Press Alt when a command
        prompts to pick a point. When Disable is on, checked object snaps are
        enabled temporarily. When Disable is off, checked object snaps are
        disabled temporarily."

        `first_point` drops `Perp`/`Tan`, which the help says are "not effective
        for the first point of a command".

        Unimplemented snaps are filtered out here, so a caller can never be handed
        a mode this module would silently fail to compute.
        """
        off = self.disabled
        if suspend:
            off = not off           # Alt inverts it, per the help
        if off:
            return ()
        modes = {m for m in self.enabled if is_implemented(m)}
        if first_point:
            modes -= set(NEEDS_PREVIOUS_POINT)
        return tuple(sorted(modes))

    def unimplemented_enabled(self):
        """Enabled snaps we do not compute -- so the UI can say so honestly."""
        return tuple(sorted(m for m in self.enabled if not is_implemented(m)))


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def _dist(a, b):
    return math.dist(a, b)


def _polyline_length(points):
    return sum(map(math.dist, points[:-1], points[1:]))


def arc_length_midpoint(points):
    """The point half way ALONG a sampled curve, or None if it has no length.

    Not the average of the samples and not the middle sample -- those are both
    wrong for an unevenly sampled or curved shape. This walks the polyline to the
    half-length position and interpolates within the straddling segment.
    """
    pts = tuple(points or ())
    if len(pts) < 2:
        return None
    total = _polyline_length(pts)
    if total <= 0.0:
        return None
    half = total / 2.0
    walked = 0.0
    dist = math.dist
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = dist(a, b)
        if seg <= 0.0:
            continue
        if walked + seg >= half:
            t = (half - walked) / seg
            return (a[0] + (b[0] - a[0]) * t,
                    a[1] + (b[1] - a[1]) * t,
                    a[2] + (b[2] - a[2]) * t)
        walked += seg
    return pts[-1]


def nearest_on_polyline(points, cursor):
    """The closest point to `cursor` ON the sampled curve, or None.

    This is the `Near` snap: "Snap onto a curve ... at the cursor location." It
    projects onto each segment rather than just taking the closest sample, so the
    result really lies on the curve instead of on the sampling grid.
    """
    pts = tuple(points or ())
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0]
    best = None
    best_d = math.inf
    for i in range(len(pts) - 1):
        p = _project_on_segment(pts[i], pts[i + 1], cursor)
        d = _dist(p, cursor)
        if d < best_d:
            best_d = d
            best = p
    return best


def _nearest_on_target(target, cursor):
    """The `Near` point on a target: EXACT on the real curve when the target
    carries one, else the nearest point on the display polyline.

    The exact path solves `nearest.closest_point_on_curve`, which is right on the
    true NURBS curve to machine precision -- the polyline answer is off by a
    segment's sagitta and worsens on zoom. `nearest` is imported LAZILY so
    `osnap` still imports without rhino3dm for the hand-built pure tests, which
    carry no curve and therefore take the polyline path exactly as before.
    """
    curve = getattr(target, "curve", None)
    if curve is not None:
        try:
            try:
                from . import nearest
            except ImportError:  # loaded directly, without a package
                import nearest
            return nearest.closest_point_on_curve(curve, cursor).point
        except Exception:  # noqa: BLE001
            pass  # any trouble -> fall back to the polyline, never silently fail
    return nearest_on_polyline(target.points, cursor)


def _project_on_segment(a, b, p):
    """The point on segment a-b closest to p (clamped to the segment)."""
    ax, ay, az = a
    bx, by, bz = b
    dx, dy, dz = bx - ax, by - ay, bz - az
    denom = dx * dx + dy * dy + dz * dz
    if denom <= 0.0:
        return a
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy + (p[2] - az) * dz) / denom
    t = max(0.0, min(1.0, t))
    return (ax + dx * t, ay + dy * t, az + dz * t)


def quadrant_points(points, plane):
    """The `Quad` snap points of a sampled curve, in world coordinates.

    The help is specific, and CPlane-relative: "Snap to the point on a curve that
    is at the maximum x or y point relative to the **current construction
    plane**." ... "The quadrant points of circles and arcs are at the extremes in
    each construction plane axis direction."

    So the extremes are found in the PLANE's coordinates -- which means a circle
    drawn on Front has its quadrants at the Front plane's extremes, not at world
    X/Y extremes. Returns up to four points (min/max of each in-plane axis).
    """
    pts = tuple(points or ())
    if not pts or plane is None:
        return ()
    xa = getattr(plane, "xaxis", None)
    ya = getattr(plane, "yaxis", None)
    org = getattr(plane, "origin", None)
    if xa is not None and ya is not None and org is not None:
        # Inline `plane.from_world`'s u and v because w is not needed. Calling the full
        # conversion and re-deriving the plane normal for hundreds of targets adds
        # substantial pointer-move latency.
        ox, oy, oz = org
        x0, x1, x2 = xa
        y0, y1, y2 = ya
        umin = umax = vmin = vmax = None
        for p in pts:
            d0, d1, d2 = p[0] - ox, p[1] - oy, p[2] - oz
            u = d0 * x0 + d1 * x1 + d2 * x2
            v = d0 * y0 + d1 * y1 + d2 * y2
            if umin is None:
                umin = umax = (u, p)
                vmin = vmax = (v, p)
                continue
            # strict comparisons: the FIRST extreme wins, as `min`/`max` choose
            if u < umin[0]:
                umin = (u, p)
            if u > umax[0]:
                umax = (u, p)
            if v < vmin[0]:
                vmin = (v, p)
            if v > vmax[0]:
                vmax = (v, p)
        out = [umin[1], umax[1], vmin[1], vmax[1]]
    else:
        local = [(plane.from_world(p), p) for p in pts]
        out = []
        for axis in (0, 1):                     # the plane's x, then its y
            lo = min(local, key=lambda lp: lp[0][axis])[1]
            hi = max(local, key=lambda lp: lp[0][axis])[1]
            out.append(lo)
            out.append(hi)
    # de-duplicate while keeping order (a degenerate curve can repeat a point)
    unique = []
    for p in out:
        if not any(_dist(p, q) < 1e-9 for q in unique):
            unique.append(p)
    return tuple(unique)


def segment_midpoints(vertices, closed=False):
    """Midpoints between consecutive corners -- the `Mid` snap on a polycurve.

    The help lists "polycurve segment" as a `Mid` target, so a polyline offers a
    midpoint per segment, not just one for the whole curve.

    A closed curve's `vertices` hold the seam once (`bridge.curve_snap_target` drops the
    repeated last point, so `End` does not offer it twice). Walking consecutive
    pairs alone therefore never reached the CLOSING segment, last corner -> first,
    and it had no `Mid`. With `closed`, that segment is included -- but only when
    there are 3+ corners and the ends really differ: a closed smooth curve carries
    just its seam, and a hand-built list that still repeats the seam has no
    closing segment left to add.
    """
    verts = tuple(vertices or ())
    pairs = [(verts[i], verts[i + 1]) for i in range(len(verts) - 1)]
    if closed and len(verts) >= 3 and _dist(verts[-1], verts[0]) > 1e-12:
        pairs.append((verts[-1], verts[0]))
    return tuple(
        ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0)
        for a, b in pairs
    )


# -- `Perp` and `Tan`: both need the PREVIOUS picked point (`base`) -----------
#
# The help: "Perpendicular (Perp) -- Snap perpendicular to a curve, mesh wire, or
# surface edge" and "Tangent (Tan) -- Snap tangent to a curve", both "not
# effective for the first point of a command". So each is computed relative to
# `base`, the point already picked, and yields nothing when `base` is None.
#
# Both work on `SnapTarget.points` -- the dense ordered world-space sample -- so
# both are NUMERICAL and APPROXIMATE (accuracy scales with sample density), and
# the method is OURS: the help states what the snaps mean, not how to compute
# them off a sample. Each function says what its error depends on.

def _cross(u, v):
    """3-D cross product of two vectors."""
    return (u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0])


def perpendicular_points(points, base):
    """`Perp` candidates: feet of the perpendicular from `base` onto the curve.

    APPROXIMATE and OURS. For each consecutive sample pair (a short straight
    segment) this drops the perpendicular from `base` onto that segment and keeps
    the foot only when it lands STRICTLY INSIDE the segment (0 < t < 1) -- a foot
    exactly at a shared vertex is an `End` snap, not a `Perp`, so it is left out.
    The foot's error from the true perpendicular point is the segment's sagitta,
    about ``spacing**2 / (8 * radius)`` for a smooth curve, so it is tiny for a
    dense sample. Returns world points; a circle seen from an outside `base`
    yields the two points where the line ``base -> centre`` crosses it.
    """
    pts = tuple(points or ())
    if base is None or len(pts) < 2:
        return []
    out = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        denom = dx * dx + dy * dy + dz * dz
        if denom <= 0.0:
            continue
        t = ((base[0] - a[0]) * dx + (base[1] - a[1]) * dy
             + (base[2] - a[2]) * dz) / denom
        if t <= 0.0 or t >= 1.0:        # foot at/beyond an endpoint -> not a Perp
            continue
        foot = (a[0] + dx * t, a[1] + dy * t, a[2] + dz * t)
        if not any(_dist(foot, q) < 1e-9 for q in out):
            out.append(foot)
    return out


def tangent_points(points, base, closed=False):
    """`Tan` candidates: points where the line from `base` is tangent to the curve.

    APPROXIMATE and OURS. A point is tangent when the direction from `base` to it
    is PARALLEL to the curve's tangent there, i.e. the cross product
    ``(sample - base) x tangent`` vanishes. Walking the dense sample, this tracks
    that cross product -- projected onto a stable reference normal taken from the
    sample itself, so it works for a planar curve lying in ANY plane -- and
    brackets a tangent point wherever the projected value changes sign, then
    refines linearly between the two straddling samples. The refined point lies
    within about one sample spacing of the true tangent point, so accuracy scales
    with sample density. Assumes an (approximately) planar curve: tangency from a
    point to a general 3-D curve is not well defined. `closed` makes the sample a
    cycle (a dropped duplicate closing sample is handled).
    """
    pts = tuple(points or ())
    if base is None or len(pts) < 3:
        return []
    if closed and _dist(pts[0], pts[-1]) < 1e-9:
        pts = pts[:-1]                  # a clean cycle, no repeated seam sample
    n = len(pts)
    if n < 3:
        return []

    def tangent_at(i):
        if closed:
            prv, nxt = pts[(i - 1) % n], pts[(i + 1) % n]
        else:
            prv, nxt = pts[max(0, i - 1)], pts[min(n - 1, i + 1)]
        return (nxt[0] - prv[0], nxt[1] - prv[1], nxt[2] - prv[2])

    cross = []
    for i in range(n):
        v = (pts[i][0] - base[0], pts[i][1] - base[1], pts[i][2] - base[2])
        cross.append(_cross(v, tangent_at(i)))
    # a stable reference normal: the largest cross vector. Near a tangent point
    # the cross shrinks to zero, so the largest one is safely away from a flip
    # and (for a planar curve) points along the curve's plane normal.
    ref = max(cross, key=lambda c: c[0] * c[0] + c[1] * c[1] + c[2] * c[2])
    if ref[0] * ref[0] + ref[1] * ref[1] + ref[2] * ref[2] <= 0.0:
        return []                       # degenerate (e.g. base lies on the curve)
    scal = [c[0] * ref[0] + c[1] * ref[1] + c[2] * ref[2] for c in cross]

    out = []
    limit = n if closed else n - 1
    for i in range(limit):
        j = (i + 1) % n
        si, sj = scal[i], scal[j]
        if si == 0.0:
            p = pts[i]
        elif si * sj < 0.0:          # linear zero-crossing between i and j
            t = si / (si - sj)
            a, b = pts[i], pts[j]
            p = (a[0] + (b[0] - a[0]) * t,
                 a[1] + (b[1] - a[1]) * t,
                 a[2] + (b[2] - a[2]) * t)
        else:
            continue
        if not any(_dist(p, q) < 1e-9 for q in out):
            out.append(p)
    return out


# -- `Int`: where two curves cross -------------------------------------------

def _seg_bbox(a, b):
    """Axis-aligned box around segment a-b -- the cheap pre-filter below."""
    return ((min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2])),
            (max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2])))


def _bbox_overlap(lo1, hi1, lo2, hi2, tol):
    """True when the two boxes are within `tol` on every axis (may still cross).

    A single cheap test that rejects most segment pairs without touching the
    exact intersection math -- that is the O(n^2) bound: the expensive test runs
    only for pairs whose boxes actually reach each other.
    """
    return (lo1[0] <= hi2[0] + tol and lo2[0] <= hi1[0] + tol
            and lo1[1] <= hi2[1] + tol and lo2[1] <= hi1[1] + tol
            and lo1[2] <= hi2[2] + tol and lo2[2] <= hi1[2] + tol)


def _segments_cross(a1, a2, b1, b2, tol):
    """The point where segments a1-a2 and b1-b2 cross, or None.

    A true crossing has closest points that coincide, so this uses the standard
    closest-points-between-segments test: compute the parameters of the closest
    pair, and accept the pair only when that distance is within `tol`. This is
    correct for planar crossings (the reference CAD application's `Int` case) and degrades gracefully to
    "no crossing" for segments that merely run near each other.
    """
    d1 = (a2[0] - a1[0], a2[1] - a1[1], a2[2] - a1[2])
    d2 = (b2[0] - b1[0], b2[1] - b1[1], b2[2] - b1[2])
    r = (a1[0] - b1[0], a1[1] - b1[1], a1[2] - b1[2])
    A = d1[0] * d1[0] + d1[1] * d1[1] + d1[2] * d1[2]
    B = d1[0] * d2[0] + d1[1] * d2[1] + d1[2] * d2[2]
    D = d2[0] * d2[0] + d2[1] * d2[1] + d2[2] * d2[2]
    E = r[0] * d1[0] + r[1] * d1[1] + r[2] * d1[2]
    F = r[0] * d2[0] + r[1] * d2[1] + r[2] * d2[2]
    denom = A * D - B * B
    if denom > 1e-30:
        s = (B * F - D * E) / denom
        t = (A * F - B * E) / denom
    else:                              # parallel (or degenerate) segments
        s, t = 0.0, (F / D if D > 1e-30 else 0.0)
    if s < 0.0:                        # closest point is the segment's start
        s = 0.0
        t = F / D if D > 1e-30 else 0.0
    elif s > 1.0:                      # ...or its end
        s = 1.0
        t = (B + F) / D if D > 1e-30 else 0.0
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    pa = (a1[0] + d1[0] * s, a1[1] + d1[1] * s, a1[2] + d1[2] * s)
    pb = (b1[0] + d2[0] * t, b1[1] + d2[1] * t, b1[2] + d2[2] * t)
    if math.dist(pa, pb) <= tol:
        return ((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0,
                (pa[2] + pb[2]) / 2.0)
    return None


def intersection_targets(targets, cursor, tol=1e-6):
    """The `Int` snaps: where any TWO of `targets` cross, as a list of Snap.

    `Int` is "intersections of curves" (user_interface/object_snaps.htm) -- a
    CROSS-TARGET computation, so it is a separate pass over PAIRS of targets,
    not part of the per-target loop in :func:`candidates`. Each target's
    segments are pre-filtered by bounding box (see :func:`_bbox_overlap`) before
    the exact test, which bounds the O(n^2) cost. Self-intersection of one curve
    is not attempted: the reference CAD application's `Int` is between objects, and a single straight
    line correctly yields none.
    """
    found = []
    curve_targets = [t for t in targets or () if not t.is_point and len(t.points) >= 2]
    if len(curve_targets) < 2:
        return found
    segs = []
    for t in curve_targets:
        out = []
        for i in range(len(t.points) - 1):
            a, b = t.points[i], t.points[i + 1]
            out.append((a, b, _seg_bbox(a, b)))
        segs.append(out)
    seen = []
    for i in range(len(curve_targets)):
        for j in range(i + 1, len(curve_targets)):
            for (a, b, box_a) in segs[i]:
                for (c, d, box_b) in segs[j]:
                    if not _bbox_overlap(box_a[0], box_a[1], box_b[0], box_b[1], tol):
                        continue
                    p = _segments_cross(a, b, c, d, tol)
                    if p is None:
                        continue
                    if any(_dist(p, q) < 1e-9 for q in seen):
                        continue
                    seen.append(p)
                    found.append(Snap(p, INT, _dist(p, cursor), curve_targets[i]))
    return found


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------

# A target's `Mid` and `Quad` points depend only on the target (and, for `Quad`, the
# CPlane), never on the cursor -- so they are computed once and kept ON the target.
# Recomputing them for every nearby target added tens of milliseconds to each mouse move.
# Targets are never mutated after construction.

def _target_mids(target):
    """`Mid`'s points for `target`: each segment's midpoint, plus the whole curve's
    arc-length midpoint when it is not one of those. Cached on the target."""
    mids = getattr(target, "_mids", None)
    if mids is None:
        mids = list(segment_midpoints(target.vertices, target.closed))
        whole = arc_length_midpoint(target.points)
        if whole is not None and not any(_dist(whole, m) < 1e-9 for m in mids):
            mids.append(whole)
        mids = tuple(mids)
        try:
            target._mids = mids
        except AttributeError:       # a hand-built stand-in without the slot
            pass
    return mids


def _plane_key(plane):
    try:
        return (tuple(plane.origin), tuple(plane.xaxis), tuple(plane.yaxis))
    except Exception:  # noqa: BLE001 - an object with only `from_world`: never cached
        return None


def _target_quads(target, plane):
    """`quadrant_points(target.points, plane)`, cached on the target per CPlane."""
    key = _plane_key(plane)
    hit = getattr(target, "_quad", None)
    if key is not None and hit is not None and hit[0] == key:
        return hit[1]
    quads = quadrant_points(target.points, plane)
    if key is not None:
        try:
            target._quad = (key, quads)
        except AttributeError:
            pass
    return quads


def candidates(targets, cursor, modes, plane=None, *, base=None):
    """Every snap point the enabled `modes` find, as a list of :class:`Snap`.

    Unsorted and unfiltered by radius -- :func:`best` does that. Split out so
    tests can inspect what each mode contributes.

    `Int` is a CROSS-TARGET snap (where two curves meet), so it is not part of
    the per-target loop below -- it runs once over the pair of targets, after it.

    `base` is the PREVIOUSLY picked point, keyword-only and defaulting to None.
    `Perp` and `Tan` are computed relative to it; with `base is None` they
    contribute nothing at all (and are never quietly replaced by another snap),
    which is exactly the help's "not effective for the first point of a command".
    """
    found = []
    wanted = {m for m in (modes or ()) if is_implemented(m)}
    if not wanted:
        return found

    for target in targets or ():
        if target.is_point:
            if POINT in wanted:
                loc = target.points[0] if target.points else None
                if loc is not None:
                    found.append(Snap(loc, POINT, _dist(loc, cursor), target))
            continue

        if END in wanted:
            for v in target.vertices:
                found.append(Snap(v, END, _dist(v, cursor), target))

        if MID in wanted:
            for m in _target_mids(target):
                found.append(Snap(m, MID, _dist(m, cursor), target))

        if CEN in wanted and target.center is not None:
            found.append(Snap(target.center, CEN,
                              _dist(target.center, cursor), target))

        if QUAD in wanted and plane is not None:
            for q in _target_quads(target, plane):
                found.append(Snap(q, QUAD, _dist(q, cursor), target))

        if NEAR in wanted:
            n = _nearest_on_target(target, cursor)
            if n is not None:
                found.append(Snap(n, NEAR, _dist(n, cursor), target))

        # `Knot`: "a knot on a curve or surface". The bridge populates `knots`
        # from a NurbsCurve's interior knots; an empty field simply offers none,
        # which is how a straight line / polyline (no interior knots) behaves.
        if KNOT in wanted:
            for k in target.knots:
                found.append(Snap(k, KNOT, _dist(k, cursor), target))

        # `Perp`/`Tan`: only with a previous point. No `base` -> no contribution,
        # never a silent fallback (house rule 4).
        if PERP in wanted and base is not None:
            for p in perpendicular_points(target.points, base):
                found.append(Snap(p, PERP, _dist(p, cursor), target))

        if TAN in wanted and base is not None:
            for p in tangent_points(target.points, base, target.closed):
                found.append(Snap(p, TAN, _dist(p, cursor), target))

    if INT in wanted:
        found.extend(intersection_targets(targets, cursor))

    return found


def best(targets, cursor, modes, plane=None, radius=None, *, base=None,
         snap_to_locked=False):
    """The snap the marker should jump to, or None.

    `radius` is a WORLD distance (the caller converts the reference CAD application's pixel radius -- see
    the module docstring). None means "no limit", which is only useful in tests.

    `base` (keyword-only, default None) is the previously picked point that
    `Perp`/`Tan` need; it is threaded straight through to :func:`candidates`.
    Every existing caller that omits it keeps its exact behaviour.

    `snap_to_locked` (keyword-only, default False) controls `SnapToLocked`.
    While False -- the MEASURED the reference CAD application default -- targets flagged `locked` are
    dropped BEFORE any snap is computed, so a locked object can never be the
    answer. This is the one place the engine filters targets, the same spot the
    `exclude_names` pre-filter feeds; doing it here rather than in each snap mode
    means every mode (End, Mid, Near, ...) honours it at once. The default is
    False, so every existing caller excludes locked objects automatically.

    `Near` is applied as a FALLBACK rather than a competitor: it matches anywhere
    along a curve, so on distance alone it would always beat `End`/`Mid`/`Cen` and
    those snaps would become unreachable. This is an inference, not a documented
    rule (:data:`NEAR_IS_FALLBACK`).

    Ties break on the mode's order in :data:`PERSISTENT`, so the answer is stable
    rather than dependent on object order -- a snap that flickers between two
    equally close candidates would be worse than either.
    """
    if not snap_to_locked:
        targets = [t for t in targets if not getattr(t, "locked", False)]
    found = candidates(targets, cursor, modes, plane, base=base)
    if radius is not None:
        found = [s for s in found if s.distance <= radius]
    if not found:
        return None

    discrete = [s for s in found if s.mode != NEAR]
    pool = discrete if (discrete and NEAR_IS_FALLBACK) else found
    if not pool:
        pool = found

    order = {name: i for i, name in enumerate(PERSISTENT)}
    return min(pool, key=lambda s: (round(s.distance, 9),
                                    order.get(s.mode, len(order))))


# ---------------------------------------------------------------------------
# Snapping is measured on screen, in pixels
# ---------------------------------------------------------------------------
#
# World-distance snapping fails when target geometry is away from the construction plane.
# In the Top pane, a cursor 2 px from a line end at z=5 or a box corner at z=10 found no snap,
# while Front/Right snapped the same targets. `best` measures the WORLD distance from
# the cursor's point ON THE CPLANE (z=0) to each target, so anything above or below
# the CPlane -- a box's top, a raised curve -- lay 5 or 10 units "away" and was out of
# range however close it looked.
#
# the reference CAD application's radius is in PIXELS ("Osnap cursor sensitivity ... in pixels",
# options/modeling_aids.htm), i.e. it is measured where the user is looking: on the
# screen. So the snap distance is the pixel distance between the mouse and each
# candidate's projection. That is also what makes perspective right: a near and a far
# target are judged by how close they LOOK, not by their depth.


def _near_on_screen(target, project, mouse):
    """`Near` measured on screen: the curve point whose projection is nearest the mouse.

    Found on the projected polyline, lifted back to 3-D by the segment parameter, then
    made exact on the real curve when the target carries one.
    """
    pts = tuple(target.points or ())
    if len(pts) < 2:
        return None
    proj = [project(q) for q in pts]
    best3, best_d = None, math.inf
    mx, my = mouse
    for i in range(len(pts) - 1):
        a, b = proj[i], proj[i + 1]
        if a is None or b is None:
            continue
        dx, dy = b[0] - a[0], b[1] - a[1]
        den = dx * dx + dy * dy
        t = 0.0 if den <= 0.0 else max(0.0, min(1.0, ((mx - a[0]) * dx
                                                     + (my - a[1]) * dy) / den))
        d = math.hypot(a[0] + dx * t - mx, a[1] + dy * t - my)
        if d < best_d:
            p0, p1 = pts[i], pts[i + 1]
            best_d = d
            best3 = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t,
                     p0[2] + (p1[2] - p0[2]) * t)
    if best3 is None:
        return None
    curve = getattr(target, "curve", None)
    if curve is not None:
        try:
            try:
                from . import nearest
            except ImportError:  # loaded directly, without a package
                import nearest
            return nearest.closest_point_on_curve(curve, best3).point
        except Exception:  # noqa: BLE001
            pass
    return best3


def best_on_screen(targets, cursor, modes, project, radius_px, plane=None, *,
                   base=None, snap_to_locked=False):
    """:func:`best`, with distance measured in SCREEN PIXELS. See the block comment.

    `project` maps a world point to ``(x, y)`` pixels, or None when it cannot be seen
    (behind a perspective camera). `cursor` is the cursor's world point; its
    projection IS the mouse position, so no separate mouse argument can disagree
    with it. Every other rule -- `Near` as a fallback, ties broken by mode order,
    locked targets dropped -- is :func:`best`'s, unchanged.
    """
    mouse = project(cursor) if cursor is not None else None
    if mouse is None:
        return None
    if not snap_to_locked:
        targets = [t for t in targets if not getattr(t, "locked", False)]
    wanted = tuple(m for m in (modes or ()) if is_implemented(m))
    found = candidates(targets, cursor, tuple(m for m in wanted if m != NEAR),
                       plane, base=base)
    if NEAR in wanted:
        for t in targets:
            if t.is_point:
                continue
            n = _near_on_screen(t, project, mouse)
            if n is not None:
                found.append(Snap(n, NEAR, 0.0, t))
    scored = []
    for s in found:
        q = project(s.point)
        if q is None:
            continue
        d = math.hypot(q[0] - mouse[0], q[1] - mouse[1])
        if radius_px is None or d <= radius_px:
            scored.append(Snap(s.point, s.mode, d, s.target))
    if not scored:
        return None
    discrete = [s for s in scored if s.mode != NEAR]
    pool = discrete if (discrete and NEAR_IS_FALLBACK) else scored
    order = {name: i for i, name in enumerate(PERSISTENT)}
    return min(pool, key=lambda s: (round(s.distance, 9),
                                    order.get(s.mode, len(order))))


# ---------------------------------------------------------------------------
# Culling by SCREEN RECTANGLE -- the gumball-drag performance fix
# ---------------------------------------------------------------------------
#
# With thousands of objects and snap targets, every mouse move of a Snappy Gumball drag
# can spend over a second in `candidates`, computing every
# mode's points for EVERY target before `best_on_screen` threw away all but the few
# within the pixel radius. A target can only offer a snap within `radius_px` of the
# mouse if its WORLD bounding box projects to within `radius_px` of it, so the caller
# drops the rest first. That is exact, not a heuristic:
#
# * every point any mode can return for a target lies inside the target's world box
#   (samples, vertices, centres, midpoints, quadrants, Near/Perp/Tan points on the
#   curve; an `Int` point lies inside BOTH boxes) -- `bridge.snap_extent` builds the
#   box so this holds, including an arc's centre, which lies OUTSIDE the arc;
# * with every corner in front of the eye (w > 0), the projection of a box is the
#   convex hull of its eight projected corners, so it is inside their 2-D rectangle;
# * a box with a corner at or behind the eye cannot be bounded this way, so it is
#   NEVER culled (`box_screen_rect` returns None).
#
# `RECT_MARGIN_PX` widens the test: `best_on_screen` measures with Blender's
# single-precision projection, these rectangles are computed in double precision.

#: Slack (pixels) added to the radius when culling, so float32-vs-float64 projection
#: noise can never cull a target that is really in range. Only ever makes the cull
#: keep MORE, so it cannot change which snap wins.
RECT_MARGIN_PX = 4.0

#: `box_screen_rect`'s answer for a box ENTIRELY behind the eye. Blender's projection
#: returns None for every point with ``w <= 0`` and `best_on_screen` skips those, so
#: nothing in such a box can ever snap. Large scenes may have thousands of objects
#: behind the camera, so retaining them adds significant cost to every move.
BEHIND_EYE = ()

#: How far below zero (relative to the size of the terms summed) a corner's `w` must
#: be to count as behind the eye -- float32 noise must never cull a visible corner.
_BEHIND_REL = 1e-5


def box_screen_rect(lo, hi, matrix, width, height):
    """The screen rectangle ``(xmin, ymin, xmax, ymax)`` containing the projection of
    the world box `lo`..`hi`, or None when it cannot be bounded.

    `matrix` is the pane's 4x4 perspective matrix as four row tuples, and the mapping
    is exactly `bpy_extras.view3d_utils.location_3d_to_region_2d`'s:
    ``prj = M @ (x, y, z, 1)``; visible only when ``prj.w > 0``; pixel =
    ``half + half * prj / prj.w``. None ("keep this target") when some corner is at
    or behind the eye and some in front: the visible part's image is unbounded.
    `BEHIND_EYE` when every corner is clearly behind it (w is affine, so then every
    point of the box is too, and none of them projects).
    """
    r0, r1, r2, r3 = matrix
    wh = width / 2.0
    hh = height / 2.0
    xmin = ymin = math.inf
    xmax = ymax = -math.inf
    behind = 0
    straddle = False
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                w = r3[0] * x + r3[1] * y + r3[2] * z + r3[3]
                if not w > 0.0:
                    scale = (abs(r3[0] * x) + abs(r3[1] * y) + abs(r3[2] * z)
                             + abs(r3[3]))
                    if w < -_BEHIND_REL * scale:
                        behind += 1
                    else:
                        straddle = True     # on the eye plane, within noise
                    continue
                px = wh + wh * ((r0[0] * x + r0[1] * y + r0[2] * z + r0[3]) / w)
                py = hh + hh * ((r1[0] * x + r1[1] * y + r1[2] * z + r1[3]) / w)
                if px < xmin:
                    xmin = px
                if px > xmax:
                    xmax = px
                if py < ymin:
                    ymin = py
                if py > ymax:
                    ymax = py
    if behind == 8:
        return BEHIND_EYE
    if behind or straddle:
        return None
    if not (math.isfinite(xmin) and math.isfinite(xmax)
            and math.isfinite(ymin) and math.isfinite(ymax)):
        return None
    return (xmin, ymin, xmax, ymax)


def rect_reaches(rect, mouse, radius_px, margin=RECT_MARGIN_PX):
    """Can anything inside screen rectangle `rect` lie within `radius_px` of `mouse`?

    True for an unknown rectangle (None) or an unknown radius -- this answers
    "may I skip it", and the only safe answer without facts is "no".
    """
    if rect is None or radius_px is None or mouse is None:
        return True
    if rect == BEHIND_EYE:
        return False
    x0, y0, x1, y1 = rect
    mx, my = mouse
    dx = x0 - mx if mx < x0 else (mx - x1 if mx > x1 else 0.0)
    dy = y0 - my if my < y0 else (my - y1 if my > y1 else 0.0)
    r = float(radius_px) + float(margin)
    return dx * dx + dy * dy <= r * r


def boxes_reach(lo, hi, matrix, width, height, mouse, radius_px,
                margin=RECT_MARGIN_PX):
    """`rect_reaches(box_screen_rect(...))` for MANY boxes at once (numpy).

    `lo`, `hi` are ``(N, 3)`` arrays; returns a boolean ``(N,)`` array, True where the
    box may come within `radius_px` of `mouse`. Same rules exactly as the scalar pair:
    a box wholly behind the eye is culled, one straddling the eye plane is KEPT, and
    the rest are tested by the rectangle of their projected corners (plus `margin`).
    The curve picker culls 5,494 curves with this in about a millisecond -- the scalar
    version costs roughly 10 microseconds per box. The vectorized and scalar rules
    must remain equivalent.
    """
    import numpy as np

    return rects_reach(boxes_screen_rects(lo, hi, matrix, width, height), mouse,
                       radius_px, margin)


def boxes_screen_rects(lo, hi, matrix, width, height):
    """The projection half of `boxes_reach`, which depends on the VIEW only -- so a
    caller whose view does not move (a Gumball drag) projects once and tests the mouse
    against the result with `rects_reach` on every move. For thousands of snap entries,
    projection costs much more than the rectangle test and should be reused.

    Returns ``(x0, y0, x1, y1, unbounded, all_behind)``, arrays of length N.
    """
    import numpy as np

    lo = np.asarray(lo, dtype=np.float64).reshape(-1, 3)
    hi = np.asarray(hi, dtype=np.float64).reshape(-1, 3)
    n = len(lo)
    if n == 0:
        z = np.zeros(0, dtype=np.float64)
        b = np.zeros(0, dtype=bool)
        return (z, z, z, z, b, b)
    m = np.asarray(matrix, dtype=np.float64).reshape(4, 4)
    sel = np.array([[(i >> 2) & 1, (i >> 1) & 1, i & 1] for i in range(8)], dtype=bool)
    # (N, 8, 3) corners, the same 8 combinations box_screen_rect visits
    corners = np.where(sel[None, :, :], hi[:, None, :], lo[:, None, :])
    terms = corners[..., None, :] * m[None, None, :, :3]       # (N, 8, 4, 3)
    hom = terms.sum(-1) + m[None, None, :, 3]                    # (N, 8, 4)
    w = hom[..., 3]
    scale = np.abs(terms[..., 3, :]).sum(-1) + abs(m[3, 3])
    front = w > 0.0
    behind = (~front) & (w < -_BEHIND_REL * scale)
    all_behind = behind.all(axis=1)
    unbounded = (~front).any(axis=1) & ~all_behind
    wh = width / 2.0
    hh = height / 2.0
    with np.errstate(divide="ignore", invalid="ignore"):
        px = wh + wh * (hom[..., 0] / w)
        py = hh + hh * (hom[..., 1] / w)
    px = np.where(front, px, np.nan)
    py = np.where(front, py, np.nan)
    with np.errstate(invalid="ignore"):
        x0 = np.nanmin(np.where(front, px, np.inf), axis=1)
        x1 = np.nanmax(np.where(front, px, -np.inf), axis=1)
        y0 = np.nanmin(np.where(front, py, np.inf), axis=1)
        y1 = np.nanmax(np.where(front, py, -np.inf), axis=1)
    finite = np.isfinite(x0) & np.isfinite(x1) & np.isfinite(y0) & np.isfinite(y1)
    return (x0, y0, x1, y1, unbounded | ~finite, all_behind)


def rects_reach(rects, mouse, radius_px, margin=RECT_MARGIN_PX):
    """The mouse half of `boxes_reach`, on `boxes_screen_rects`' answer."""
    import numpy as np

    x0, y0, x1, y1, unbounded, all_behind = rects
    if len(x0) == 0:
        return np.zeros(0, dtype=bool)
    r = float(radius_px) + float(margin)
    with np.errstate(invalid="ignore"):
        near = rects_distance2(rects, mouse) <= r * r
    keep = np.where(unbounded, True, near)
    return keep & ~all_behind


def rects_distance2(rects, mouse):
    """The SQUARED pixel distance from `mouse` to each of `boxes_screen_rects`'
    rectangles (0 inside). Meaningless where the rectangle is unbounded or behind the
    eye -- `rects_reach` decides those cases."""
    import numpy as np

    x0, y0, x1, y1 = rects[:4]
    mx, my = float(mouse[0]), float(mouse[1])
    dx = np.where(mx < x0, x0 - mx, np.where(mx > x1, mx - x1, 0.0))
    dy = np.where(my < y0, y0 - my, np.where(my > y1, my - y1, 0.0))
    return dx * dx + dy * dy


def segments_screen_distance(coords, matrix, width, height, mouse):
    """The smallest SCREEN distance (pixels) from `mouse` to any of the line segments
    `coords` -- an ``(2E, 3)`` array of world points, consecutive pairs being one
    segment (the layout of a GL ``LINES`` batch) -- or ``inf`` if none is visible.

    Projected exactly as `location_3d_to_region_2d` (``half + half * prj / prj.w``).
    A segment crossing the eye plane is CLIPPED to the part in front of the eye
    (``w >= eps``) before dividing, so a long curve passing beside the camera can still
    be picked; a segment wholly behind the eye is ignored.
    """
    import numpy as np

    c = np.asarray(coords, dtype=np.float64).reshape(-1, 3)
    if len(c) < 2:
        return math.inf
    c = c[: len(c) // 2 * 2]
    m = np.asarray(matrix, dtype=np.float64).reshape(4, 4)
    hom = c @ m[:, :3].T + m[:, 3]
    a = hom[0::2]
    b = hom[1::2]
    wa = a[:, 3]
    wb = b[:, 3]
    eps = 1e-9 * max(1.0, float(np.abs(hom[:, 3]).max()))
    keep = (wa >= eps) | (wb >= eps)
    if not keep.any():
        return math.inf
    a, b, wa, wb = a[keep], b[keep], wa[keep], wb[keep]
    d = b - a
    with np.errstate(divide="ignore", invalid="ignore"):
        ta = np.where(wa < eps, (eps - wa) / (wb - wa), 0.0)
        tb = np.where(wb < eps, (eps - wa) / (wb - wa), 1.0)
    a2 = a + ta[:, None] * d
    b2 = a + tb[:, None] * d
    wh = width / 2.0
    hh = height / 2.0
    sa = np.stack([wh + wh * a2[:, 0] / a2[:, 3], hh + hh * a2[:, 1] / a2[:, 3]], 1)
    sb = np.stack([wh + wh * b2[:, 0] / b2[:, 3], hh + hh * b2[:, 1] / b2[:, 3]], 1)
    p = np.array([float(mouse[0]), float(mouse[1])])
    ab = sb - sa
    ll = (ab * ab).sum(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(ll > 0.0, ((p - sa) * ab).sum(1) / ll, 0.0)
    t = np.clip(t, 0.0, 1.0)
    q = sa + t[:, None] * ab
    dist = np.sqrt(((q - p) ** 2).sum(1))
    dist = dist[np.isfinite(dist)]
    return float(dist.min()) if len(dist) else math.inf


def apply_projection(snap, plane, project):
    """`Project`: drop a snap onto the construction plane, per the Osnap control.

    "The Project checkbox on the Osnap control projects any object snap to the
    current construction plane." Returns a new :class:`Snap` (or the original when
    projection is off), never mutating the input.
    """
    if snap is None or not project or plane is None:
        return snap
    u, v, _w = plane.from_world(snap.point)
    return Snap(plane.to_world(u, v, 0.0), snap.mode, snap.distance, snap.target)


def world_radius(radius_px, pixels_per_unit):
    """the reference CAD application's pixel snap radius -> a world distance at this zoom level.

    Kept here, next to the rule it serves, so the conversion is tested rather
    than improvised at the call site. `pixels_per_unit` is how many screen pixels
    one world unit currently covers in the pane being picked in. Returns None when
    that is unknown or nonsensical, which :func:`best` reads as "no limit".
    """
    try:
        ppu = float(pixels_per_unit)
    except (TypeError, ValueError):
        return None
    if ppu <= 0.0 or not math.isfinite(ppu):
        return None
    return float(radius_px) / ppu
