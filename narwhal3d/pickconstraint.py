"""Rhino's pick constraints: distance, angle, direction lock. **No `bpy`, no rhino3dm.**

Typed precision constraints are not per-command features in Rhino; they apply to every
point prompt that has a previous point, so the shared behavior lives here.

MEASURED (``user_interface/accurate_modeling.htm``), quoted where each rule is applied:

* **Distance constraint** -- "Type a number and press Enter. The marker moves around the
  last point at the specified distance." -- "Type a negative number ... The marker moves
  around the last point at the specified distance and its integer-multiplied distances.
  For example, -5 constrains the marker at 5, 10, 15, etc."
* **Angle constraint** -- "Type an angle at the command prompt in the format <angle and
  press Enter. The cursor moves along a line at the specified angle." Then "Click to
  place the point, or, type a distance, and press Enter."
* **Both** -- after a distance, "Decide the direction using... Click. Right-click or
  press Enter. Type <angle, press Enter, and click."
* **Direction lock (Tab)** -- "The marker is now constrained along the line between the
  first point and the point where the marker was when you tapped the Tab key."
  "Use direction lock constraint combined with the distance constraint to draw a curve
  of a specified length".
* **Circle lock (second Tab)** -- "Rhino tracks to a circle with the center at the last
  pick point and the radius defined by the Tab tap location."

A constraint applies to the NEXT point only: it is armed against one prompt (the command,
how many arguments it holds, which step it is on) and silently lapses the moment that
prompt is answered or abandoned. That is what "the next point" means, and it makes stale
constraints impossible without every caller remembering to clear them.

A third Tab tap releases the lock. The help documents the first two taps
only; something has to undo them short of Esc (which cancels the whole command).

DOCUMENTED INCONSISTENCY, resolved toward the definition: the angle-constraint section
DEFINES it as "moves along a line at the specified angle", while its worked example says
"restricted to 20-degree angle increments". We implement the definition (a line through
the last point, both directions) -- that is also what makes "<angle then a distance"
place one determinate point, which the same page documents.
"""
from __future__ import annotations

import math

from . import coords

#: lock states, in the order successive Tab taps step through them
LOCK_NONE = None
LOCK_DIRECTION = "direction"
LOCK_CIRCLE = "circle"


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(v, s):
    return (v[0] * s, v[1] * s, v[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(v):
    n = math.sqrt(_dot(v, v))
    if n < 1e-12:
        return None
    return (v[0] / n, v[1] / n, v[2] / n)


def parse(text):
    """Is `text` a constraint rather than a coordinate? ``(kind, value)`` or None.

    ``("distance", d)`` for a bare number (negative = incremental), ``("angle", deg)``
    for ``<angle``. ``0`` is NOT a distance: it is the documented ``0,0`` shortcut and
    `coords` reads it as a point. Anything else (coordinates, options, words) -> None,
    so the caller passes it on untouched.
    """
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("<"):
        deg, err = coords.parse_angle(t[1:])
        if err:
            return ("error", err)
        return ("angle", deg)
    if "<" in t or t[:1] == "@" or "," in coords.split_top_level(t)[0] \
            or len(coords.split_top_level(t)) > 1:
        return None
    # A typed length may be a formula or carry a unit (`27cm`, `10*sin(30d)`);
    # anything the calculator cannot read (an option name, `w`, `r`) is not ours
    value, err = coords.parse_scalar(t)
    if err:
        return None
    if value == 0.0 or not math.isfinite(value):
        return None
    return ("distance", value)


class PickConstraint:
    """The constraint armed for ONE point prompt. See the module docstring."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.key = None            # which prompt this was armed for
        self.base = None           # the last picked point (world)
        self.plane = None          # the active CPlane when armed
        self.distance = None       # abs value; see `incremental`
        self.incremental = False
        self.angle = None          # degrees in the CPlane
        self.angle_first = False   # "<a" typed before the distance
        self.lock = LOCK_NONE
        self.lock_dir = None       # unit vector (direction lock)
        self.lock_radius = None    # circle lock

    # -- arming -----------------------------------------------------------

    def active_for(self, key):
        """True while armed for the prompt identified by `key`. Lapses otherwise."""
        if self.key is not None and self.key != key:
            self.reset()
        return self.key is not None and self.is_constraining()

    def is_constraining(self):
        return (self.distance is not None or self.angle is not None
                or self.lock is not LOCK_NONE)

    def _arm(self, key, base, plane):
        if self.key != key:
            self.reset()
        self.key = key
        self.base = tuple(base)
        self.plane = plane

    def set_distance(self, key, base, plane, value):
        """Arm a distance. Returns the world point when it is now DETERMINED and the
        documentation says Enter places it immediately (angle typed first), else None.
        """
        self._arm(key, base, plane)
        self.distance = abs(float(value))
        self.incremental = value < 0
        if self.angle is not None and self.angle_first and not self.incremental:
            # "<angle ... Click to place the point, or, type a distance, and press
            # Enter." -- the distance finishes the pick.
            return self.fixed_point()
        return None

    def set_angle(self, key, base, plane, degrees):
        self._arm(key, base, plane)
        self.angle = float(degrees)
        self.angle_first = self.distance is None

    def tab(self, key, base, plane, marker):
        """One Tab tap at `marker` (the current, possibly snapped, cursor point).

        Returns the new lock state. A tap where the marker sits ON the base point has no
        direction, so it changes nothing -- better than locking to a zero vector.
        """
        if self.key != key or self.lock is LOCK_NONE:
            self._arm(key, base, plane)
            d = _unit(_sub(marker, self.base))
            if d is None:
                return self.lock
            self.lock, self.lock_dir = LOCK_DIRECTION, d
        elif self.lock == LOCK_DIRECTION:
            r = math.dist(marker, self.base)
            if r < 1e-12:
                return self.lock
            self.lock, self.lock_radius = LOCK_CIRCLE, r
        else:
            # OURS: the third tap releases (see module docstring)
            self.lock, self.lock_dir, self.lock_radius = LOCK_NONE, None, None
        return self.lock

    # -- geometry ---------------------------------------------------------

    def _angle_dir(self):
        a = math.radians(self.angle)
        return _unit(self.plane.vector_to_world(math.cos(a), math.sin(a), 0.0))

    def fixed_point(self):
        """The one point the constraints pin down, or None when they leave freedom."""
        if self.distance is None or self.incremental:
            return None
        if self.angle is not None:
            return _add(self.base, _scale(self._angle_dir(), self.distance))
        if self.lock == LOCK_DIRECTION:
            return _add(self.base, _scale(self.lock_dir, self.distance))
        return None

    def apply(self, cursor):
        """Constrain a world `cursor` point. Returns the point the pick would land on."""
        if cursor is None or self.base is None:
            return cursor
        fixed = self.fixed_point()
        if fixed is not None:
            return fixed
        base = self.base
        p = tuple(cursor)
        if self.angle is not None:
            d = self._angle_dir()
            p = _add(base, _scale(d, _dot(_sub(p, base), d)))
        elif self.lock == LOCK_DIRECTION:
            d = self.lock_dir
            p = _add(base, _scale(d, _dot(_sub(p, base), d)))
        elif self.lock == LOCK_CIRCLE:
            n = self.plane.zaxis
            v = _sub(p, base)
            v = _sub(v, _scale(n, _dot(v, n)))     # into the plane through base
            u = _unit(v)
            if u is None:
                return p
            p = _add(base, _scale(u, self.lock_radius))
        if self.distance is not None:
            v = _sub(p, base)
            length = math.sqrt(_dot(v, v))
            u = _unit(v)
            if u is None:
                return p
            r = self.distance
            if self.incremental:
                r = self.distance * max(1, round(length / self.distance))
            p = _add(base, _scale(u, r))
        return p

    def describe(self):
        """A short command-line note, e.g. 'distance 4' or 'angle 20, distance 5'."""
        bits = []
        if self.angle is not None:
            bits.append(f"angle {self.angle:g}")
        if self.distance is not None:
            bits.append(("increment " if self.incremental else "distance ")
                        + f"{self.distance:g}")
        if self.lock == LOCK_DIRECTION:
            bits.append("direction locked")
        elif self.lock == LOCK_CIRCLE:
            bits.append(f"circle locked (r={self.lock_radius:g})")
        return ", ".join(bits)
