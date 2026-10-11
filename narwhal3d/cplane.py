"""Construction planes: Rhino's per-viewport coordinate system. **No `bpy`.**

No Blender imports: this module must be importable from a plain Python
interpreter (no `bpy`) so the picking math can run without launching Blender.
Keep it free of any `bpy` reference and of
relative (package) imports, exactly like `views.py`.

What a CPlane actually is
-------------------------
Rhino's help is explicit that this is not a detail (commands/cplane.htm):

    "The CPlane command sets the origin and orientation of the construction
    plane in the active viewport ... A construction plane is like a tabletop
    that the cursor normally moves on. The construction plane has an origin,
    x and y axes, and a grid ... The construction plane represents the local
    coordinate system for the viewport and can be different from the world
    coordinate system."

So a CPlane is an orthonormal **frame** -- origin plus x/y/z axes -- held
**per viewport**, and it is the local coordinate system in which that viewport
interprets input. Three consequences that a bare normal cannot express:

* **Typed points are CPlane coordinates.** ``1,2,3`` means 1 along the CPlane x
  axis, 2 along its y axis, 3 along its normal -- not world XYZ.
* **The grid IS the CPlane, drawn.** documentproperties/grid.htm: "The grid is
  an array of lines lying on a portion of the construction plane in the
  viewport" and "The construction plane is infinite." A grid in every viewport
  is therefore a *consequence* of every viewport having a CPlane, not a separate
  cosmetic feature.
* **Rotation about the normal is meaningful** (``CPlane Rotate``), and a normal
  alone has no way to record it.

How the standard frames were established (measured, not recalled)
----------------------------------------------------------------
``rhino3dm`` exposes only three canonical planes (``WorldXY``, ``WorldYZ``,
``WorldZX``); Rhino's *named* CPlanes are an application concept, and a ``.3dm``
``ViewportInfo`` does not store the construction plane. The shipped templates do store each
viewport's camera frame, and the CPlane of a standard view is by definition the
viewport's own plane, giving::

    x axis = camera direction X camera up     (screen right)
    y axis = camera up                        (screen up)
    z axis = -camera direction                (toward the viewer)

Applied to the measured template cameras (Small Objects - Millimeters), that rule
reproduces ``rhino3dm.Plane.WorldXY`` for **Top** and ``WorldYZ`` for **Right**
exactly -- two independent sources agreeing, which is what validates the rule.
**Front** then follows from the same validated rule rather than from memory.

``BOTTOM``/``BACK``/``LEFT`` are derived with that same rule, but their camera
``up`` could NOT be measured (Rhino's default template stores only the four
standard viewports), so they are marked as derived below. Treat them as the
weakest claim in this module.
"""
from __future__ import annotations

_EPS = 1e-9


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def normalize(v):
    """Unit vector, or None when `v` is (nearly) zero-length."""
    length = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
    if length < _EPS:
        return None
    return (v[0] / length, v[1] / length, v[2] / length)


class CPlane:
    """An origin plus an orthonormal x/y frame -- Rhino's construction plane.

    The z axis (the normal) is derived as ``x X y``, so a CPlane is always
    right-handed and cannot drift out of sync with its own axes.
    """

    __slots__ = ("origin", "xaxis", "yaxis")

    def __init__(self, origin=(0.0, 0.0, 0.0), xaxis=(1.0, 0.0, 0.0),
                 yaxis=(0.0, 1.0, 0.0)):
        x = normalize(xaxis)
        y = normalize(yaxis)
        if x is None or y is None:
            raise ValueError("CPlane axes must be non-zero")
        if abs(dot(x, y)) > 1e-6:
            raise ValueError(
                f"CPlane axes must be perpendicular (x.y={dot(x, y):.6f})")
        self.origin = tuple(float(c) for c in origin)
        self.xaxis = x
        self.yaxis = y

    # -- derived ----------------------------------------------------------

    @property
    def zaxis(self):
        """The plane normal, always ``x X y`` so the frame stays right-handed."""
        return cross(self.xaxis, self.yaxis)

    #: Rhino's help calls this the plane normal; same thing, kinder name.
    normal = zaxis

    def as_origin_normal(self):
        """``(origin, normal)`` -- the shape :func:`ray_plane_intersect` takes."""
        return self.origin, self.zaxis

    # -- coordinates ------------------------------------------------------

    def to_world(self, u, v, w=0.0):
        """CPlane coordinates -> world. This is how Rhino reads a typed point.

        ``to_world(1, 2, 3)`` is 1 along the x axis, 2 along the y axis and 3
        along the normal, measured from the CPlane origin.
        """
        ox, oy, oz = self.origin
        xa, ya, za = self.xaxis, self.yaxis, self.zaxis
        return (ox + xa[0] * u + ya[0] * v + za[0] * w,
                oy + xa[1] * u + ya[1] * v + za[1] * w,
                oz + xa[2] * u + ya[2] * v + za[2] * w)

    def from_world(self, point):
        """World -> CPlane coordinates ``(u, v, w)``.

        The inverse of :meth:`to_world`. Because the frame is orthonormal the
        inverse is just a projection onto each axis, so no matrix solve is
        needed.
        """
        d = (point[0] - self.origin[0],
             point[1] - self.origin[1],
             point[2] - self.origin[2])
        return dot(d, self.xaxis), dot(d, self.yaxis), dot(d, self.zaxis)

    def vector_to_world(self, u, v, w=0.0):
        """A CPlane-space DIRECTION -> world, ignoring the origin.

        Needed by Rhino's relative coordinates: ``r2,3`` means "2 along the
        CPlane x axis and 3 along its y axis **from the previous point**", so the
        offset must be rotated into world space without being translated to the
        CPlane origin.
        """
        xa, ya, za = self.xaxis, self.yaxis, self.zaxis
        return (xa[0] * u + ya[0] * v + za[0] * w,
                xa[1] * u + ya[1] * v + za[1] * w,
                xa[2] * u + ya[2] * v + za[2] * w)

    # -- the CPlane command's edits, as pure transformations ---------------

    def moved_to(self, origin):
        """Same orientation, new origin (``CPlane Origin``)."""
        return CPlane(origin, self.xaxis, self.yaxis)

    def elevated(self, distance):
        """Shift the origin along the normal (``CPlane Elevation``).

        Rhino: "Positive numbers move the construction plane up along the
        positive z axis. Negative numbers move the construction plane down."
        """
        z = self.zaxis
        return CPlane((self.origin[0] + z[0] * distance,
                       self.origin[1] + z[1] * distance,
                       self.origin[2] + z[2] * distance),
                      self.xaxis, self.yaxis)

    def __eq__(self, other):
        if not isinstance(other, CPlane):
            return NotImplemented
        return all(abs(a - b) < 1e-9 for a, b in
                   zip(self.origin + self.xaxis + self.yaxis,
                       other.origin + other.xaxis + other.yaxis))

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"CPlane(origin={self.origin}, xaxis={self.xaxis}, "
                f"yaxis={self.yaxis})")


def from_camera(direction, up, origin=(0.0, 0.0, 0.0)):
    """The CPlane of a standard view, from its camera frame.

    ``x = direction X up``, ``y = up``, ``z = -direction``. This is the rule
    validated against the shipped templates: it reproduces ``WorldXY`` for Top
    and ``WorldYZ`` for Right (see the module docstring). Returns ``None`` if the
    camera frame is degenerate (direction parallel to up).
    """
    d = normalize(direction)
    u = normalize(up)
    if d is None or u is None:
        return None
    x = normalize(cross(d, u))
    if x is None:
        return None                      # looking straight along `up`
    # re-derive y from x and the view axis so the frame is exactly orthonormal
    # even when the supplied `up` is not quite perpendicular to `direction`.
    y = normalize(cross((-d[0], -d[1], -d[2]), x))
    if y is None:
        return None
    return CPlane(origin, x, y)


# ---------------------------------------------------------------------------
# the named world CPlanes (CPlane > World > Top/Bottom/Front/Back/Left/Right)
#
# MEASURED/CROSS-VALIDATED: TOP matches rhino3dm Plane.WorldXY, RIGHT matches
# Plane.WorldYZ, both also reproduced from the template camera frames.
# FRONT follows from the same validated rule.
# DERIVED ONLY: BOTTOM/BACK/LEFT -- the default template stores just the four
# standard viewports, so their camera `up` was not measurable. Weakest claim here.
# ---------------------------------------------------------------------------

WORLD_TOP = CPlane((0, 0, 0), (1, 0, 0), (0, 1, 0))      # = rhino3dm WorldXY
WORLD_FRONT = CPlane((0, 0, 0), (1, 0, 0), (0, 0, 1))    # normal -Y
WORLD_RIGHT = CPlane((0, 0, 0), (0, 1, 0), (0, 0, 1))    # = rhino3dm WorldYZ
WORLD_BOTTOM = CPlane((0, 0, 0), (-1, 0, 0), (0, 1, 0))  # derived
WORLD_BACK = CPlane((0, 0, 0), (-1, 0, 0), (0, 0, 1))    # derived
WORLD_LEFT = CPlane((0, 0, 0), (0, -1, 0), (0, 0, 1))    # derived

#: `CPlane > World` takes exactly these six names.
WORLD_CPLANES = {
    "Top": WORLD_TOP,
    "Bottom": WORLD_BOTTOM,
    "Front": WORLD_FRONT,
    "Back": WORLD_BACK,
    "Left": WORLD_LEFT,
    "Right": WORLD_RIGHT,
}

# ---------------------------------------------------------------------------
# backward-compatible (origin, normal) pairs
#
# Earlier callers used plain ``(origin, normal)`` tuples and `ui.pick_point` still consumes
# that shape, so they are kept and derived from the frames above -- one source of
# truth, and a frame/normal disagreement becomes impossible.
# ---------------------------------------------------------------------------

TOP = WORLD_TOP.as_origin_normal()      # XY plane, z = 0, normal +Z
FRONT = WORLD_FRONT.as_origin_normal()  # XZ plane, y = 0, normal -Y
RIGHT = WORLD_RIGHT.as_origin_normal()  # YZ plane, x = 0, normal +X

# view label -> CPlane. `views.quat_to_label` returns one of these labels
# ("Top", "Front", "Right", "Perspective", "Custom").
#
# Rhino: "Rhino's standard viewports come with construction planes that
# correspond to the viewport. The default Perspective viewport, however, uses
# the world Top" (commands/cplane.htm).
CPLANE_BY_LABEL = {
    "Top": TOP,
    "Front": FRONT,
    "Right": RIGHT,
    "Perspective": TOP,   # Rhino: the default Perspective viewport uses World Top
    "Custom": TOP,        # unknown orientation -> pick on World Top (the ground)
}

#: the same mapping as full frames, for everything a bare normal cannot do
FRAME_BY_LABEL = {
    "Top": WORLD_TOP,
    "Front": WORLD_FRONT,
    "Right": WORLD_RIGHT,
    "Perspective": WORLD_TOP,
    "Custom": WORLD_TOP,
}


def ray_axis_point(origin, direction, axis_origin, axis_direction):
    """The point on an AXIS nearest to a view ray. Returns an (x, y, z) tuple, or None.

    `ray_plane_intersect` turns a click into a point against the pane's CPlane. For a
    HEIGHT or DISTANCE pick that is wrong whenever the CPlane is perpendicular to the thing
    being measured: in a Perspective pane the CPlane is world XY, so **every click lands at
    z = 0**, and the extrusion distance -- the projection onto the profile's Z normal -- came
    out as 0 every time. `ExtrudeCrv` therefore did nothing in Perspective, and `Cylinder` and
    `Cone` had the same defect for the same reason.

    Rhino constrains such a pick to the AXIS (its elevator / direction constraint), so the
    answer is the point on the axis closest to the ray -- the standard closest-approach of two
    skew lines. Writing

        p(t) = axis_origin + t * a      (the axis)
        q(s) = origin      + s * d      (the view ray)

    the closest approach satisfies two linear equations in s and t; solving for `t` gives

        t = ((d.d)(w.a) - (d.a)(w.d)) / ((a.a)(d.d) - (a.d)^2)      with w = origin - axis_origin

    The denominator vanishes exactly when the ray is PARALLEL to the axis, which is the case
    where a click genuinely carries no information about distance along it -- so that returns
    None rather than a huge or arbitrary number.
    """
    ax, ay, az = (float(axis_direction[0]), float(axis_direction[1]),
                  float(axis_direction[2]))
    dx, dy, dz = float(direction[0]), float(direction[1]), float(direction[2])
    ox, oy, oz = float(axis_origin[0]), float(axis_origin[1]), float(axis_origin[2])
    wx, wy, wz = float(origin[0]) - ox, float(origin[1]) - oy, float(origin[2]) - oz

    aa = ax * ax + ay * ay + az * az
    dd = dx * dx + dy * dy + dz * dz
    ad = ax * dx + ay * dy + az * dz
    wa = wx * ax + wy * ay + wz * az
    wd = wx * dx + wy * dy + wz * dz

    denom = aa * dd - ad * ad
    if aa <= 0.0 or dd <= 0.0 or abs(denom) < 1e-12:
        return None                 # degenerate axis/ray, or the ray is parallel to the axis
    # With w = ray_origin - axis_origin (the convention used above), this IS the axis
    # parameter -- no extra negation. Negating `t` would mirror every pick through the base,
    # so a click 7 above would return 7 below. The sign follows directly from:
    #
    #     w = (0, -20, 7), a = (0,0,1), d = (0,1,0)  ->  aa=1, dd=1, ad=0, wa=7, wd=-20
    #     t = (1*7 - 0*(-20)) / 1 = 7                ->  the point at z=+7, as clicked.
    #
    # The worked example also fixes the sign for points below the base.
    t = (dd * wa - ad * wd) / denom
    return (ox + ax * t, oy + ay * t, oz + az * t)


def ray_plane_intersect(origin, direction, plane_origin, plane_normal):
    """Intersect a ray with a plane. Returns an (x, y, z) tuple, or None.

    Ray:   p(t) = origin + t * direction,   t in [0, +inf)  (t < 0 = behind cam)
    Plane: the set of points q with dot(q - plane_origin, plane_normal) == 0

    Solving  dot(origin + t*direction, n) = dot(plane_origin, n)
    gives      t = (dot(plane_origin, n) - dot(origin, n)) / dot(direction, n)

    Returns None when the ray is (nearly) parallel to the plane
    (|dot(direction, n)| < eps) or when the hit is behind the camera (t < 0).
    """
    n = plane_normal
    denom = dot(direction, n)
    if abs(denom) < _EPS:
        return None                    # ray parallel to the plane -> no unique hit
    t = dot(plane_origin, n) - dot(origin, n)
    t = t / denom
    if t < 0.0:
        return None                    # intersection behind the camera
    return (origin[0] + direction[0] * t,
            origin[1] + direction[1] * t,
            origin[2] + direction[2] * t)
