"""Restrict a picked point to angular increments from the previous point.

Ortho operates in the construction plane, measuring directions from the plane's X axis.
The cursor point is projected onto the nearest allowed direction, preserving its distance
along that line and the previous point's height above the plane. The command, status-bar
control, and F8 key toggle the persistent state; holding Shift temporarily inverts it.
"""
from __future__ import annotations

import math

#: Default Ortho angle in degrees.
DEFAULT_ANGLE = 90.0


def effective(ortho_on, shift_held):
    """Whether Ortho applies to this pick; Shift temporarily inverts its state."""
    return bool(ortho_on) != bool(shift_held)


def constrain(base, point, plane, angle_deg=DEFAULT_ANGLE):
    """`point` restricted to the nearest multiple of `angle_deg` from `base`, in `plane`.

    `base` and `point` are world ``(x, y, z)``; `plane` is a `cplane.CPlane` (anything
    with ``from_world`` / ``to_world``). Returns a world point. With no base, a zero or
    angle, or the cursor exactly on the base, `point` comes back unchanged --
    there is no direction to restrict.
    """
    if base is None or point is None or plane is None:
        return point
    try:
        step = math.radians(float(angle_deg))
    except (TypeError, ValueError):
        return point
    if not step > 0.0:
        return point
    bu, bv, bw = plane.from_world(base)
    pu, pv, _pw = plane.from_world(point)
    du, dv = pu - bu, pv - bv
    if du == 0.0 and dv == 0.0:
        return point
    k = round(math.atan2(dv, du) / step)
    a = k * step
    cu, cv = math.cos(a), math.sin(a)
    # exact on the axes: cos(90 deg) is 6e-17 in floating point, and a line Ortho
    # made vertical must BE vertical (it is geometry, not a display)
    cu = 0.0 if abs(cu) < 1e-12 else (math.copysign(1.0, cu) if abs(abs(cu) - 1.0) < 1e-12 else cu)
    cv = 0.0 if abs(cv) < 1e-12 else (math.copysign(1.0, cv) if abs(abs(cv) - 1.0) < 1e-12 else cv)
    t = du * cu + dv * cv                 # the cursor projected onto that direction
    return plane.to_world(bu + t * cu, bv + t * cv, bw)
