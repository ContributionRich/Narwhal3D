"""the reference CAD application's coordinate entry: text -> a world point. **No `bpy`, no rhino3dm.**

Why this module exists
----------------------
the reference CAD application does **not** read a typed point as world XYZ. From
``user_interface/accurate_modeling.htm``:

    "When the reference CAD application prompts you for a point, if you type x and y Cartesian
    coordinates, the point will lie on the construction plane of the current
    viewport." ... "Type ``w`` in front of the coordinates, to use the world
    coordinate system; otherwise the construction plane coordinates of the
    active view are used."

So ``1,2`` means *1 along the active CPlane's x axis, 2 along its y axis*. In a
Front viewport that is world ``(1, 0, 2)`` -- **not** ``(1, 2, 0)``, which is what
``commands.parse_point`` produced before this module existed. That single
difference is why drawing in Front or Right did not behave like the reference CAD application.

Supported, all documented in that page
--------------------------------------
======================  ======================================================
Typed                   Meaning
======================  ======================================================
``x,y``                 CPlane coordinates, z = 0
``x,y,z``               CPlane coordinates
``0``                   documented shortcut for ``0,0``
``wx,y`` / ``wx,y,z``   **world** coordinates
``w0``                  documented shortcut for ``w0,0,0``
``rx,y`` / ``@x,y``     relative to the previous point, along the CPlane axes
``rwx,y`` / ``@wx,y``   relative to the previous point, along the WORLD axes
``d<a``                 POLAR: distance d at angle a in the CPlane
``@d<a`` / ``rd<a``     relative polar, from the previous point
``wd<a`` / ``rwd<a``    world / world-relative polar
``d<a,z``               polar + elevation (z in the same system)
======================  ======================================================

Polar angles: "Vector directions in the reference CAD application start with zero degrees at 3 o'clock
... They change in an anti (counter)-clockwise direction" -- measured in the
CPlane for the plain/relative forms and in world XY for the ``w`` forms. An angle
may carry the documented units: ``30``/``30d``/``30degrees``, ``0d20'50"``,
``22'``, ``2radian``, ``20grade``/``100gradians`` (see :func:`parse_angle`).

Deliberately NOT supported yet, and reported as such rather than mis-parsed
--------------------------------------------------------------------------
Point filters
(``.x``, ``.wxy``, ...), fractions
(``5/16``), unit suffixes (``1.234cm``, ``16'5"``) and the command-line
calculator (``10*sin(30degrees)``). Each returns a clear "not supported yet"
message, because silently reading ``17<45`` as something else would put geometry
in the wrong place -- worse than refusing it.

Also note: a **bare number** is a *distance constraint* in the reference CAD application ("Type a number
and press Enter. The marker moves around the last point at the specified
distance"), not a coordinate -- and so is a bare ``<angle``. Both need the live
cursor, so they are NOT coordinates and this module still refuses them; the
constraint itself lives in `pickconstraint` and is applied by the viewport.
The documented ``0`` / ``w0`` origin shortcuts are the exception. The old
``parse_point`` silently turned ``5`` into ``(5, 0, 0)``, which is not a thing
the reference CAD application does.
"""
from __future__ import annotations

import math
import re

#: returned as the error message when a documented-but-unimplemented form is used
UNSUPPORTED = "not supported yet"

_NUM = r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"

#: MEASURED (accurate_modeling.htm, "Entering numbers"): the angle units the reference CAD application
#: documents by name. Each maps to degrees-per-unit.
_ANGLE_WORDS = {
    "": 1.0, "d": 1.0, "deg": 1.0, "degree": 1.0, "degrees": 1.0,
    "radian": 180.0 / math.pi, "radians": 180.0 / math.pi,
    "rad": 180.0 / math.pi,
    "grade": 0.9, "grades": 0.9, "grad": 0.9,
    "gradian": 0.9, "gradians": 0.9,
}
_ANGLE_WORD_RE = re.compile(r"^([+-]?" + _NUM + r")([a-z]*)$")
# degrees-minutes-seconds: 15d30'22.345"  0d20'50"  22'  (each part optional,
# at least one present). Minutes and seconds are unsigned; the sign leads.
_DMS_RE = re.compile(r"^([+-])?(?:(" + _NUM + r")d)?(?:(" + _NUM + r")')?"
                     r"(?:(" + _NUM + r")\")?$")


def parse_angle(text):
    """Typed angle -> DEGREES. Returns ``(degrees, error)``.

    MEASURED forms (accurate_modeling.htm): ``30``, ``30d``, ``30degrees``,
    ``0d20'50"``, ``15d30'22.3450"``, ``22'`` (arc minutes), ``2radian``,
    ``20grade``, ``100gradians``. "If no units are specified, then degrees are
    assumed" (documentproperties/units.htm). ``pi`` and formulas belong to the
    command-line calculator, which is not built, so they are refused by name.
    """
    t = (text or "").strip().lower()
    if not t:
        return None, "no angle given"
    if "pi" in t or "π" in t or any(c in t for c in "*()"):
        return None, f"the command-line calculator ({text!r}) is {UNSUPPORTED}"
    m = _ANGLE_WORD_RE.match(t)
    if m and m.group(2) in _ANGLE_WORDS:
        return float(m.group(1)) * _ANGLE_WORDS[m.group(2)], None
    m = _DMS_RE.match(t)
    if m and any(m.group(i) for i in (2, 3, 4)):
        deg = (float(m.group(2) or 0.0) + float(m.group(3) or 0.0) / 60.0
               + float(m.group(4) or 0.0) / 3600.0)
        return (-deg if m.group(1) == "-" else deg), None
    return None, f"{text!r} is not an angle"


# ---------------------------------------------------------------------------
# Numbers are read by the command-line calculator, which also handles fractions
# (`5/16`, `1-3/4`) and unit suffixes (`27cm`, `16'5"`) through `numparse`.
# the reference CAD application's accurate-modeling help states: "Any time
# you are entering a number, length, point coordinate, or angle, you can use a math
# formula" and "The numbers will automatically convert to the model's units".
# ---------------------------------------------------------------------------

#: the document's unit system; the app keeps it current (AppState.feed_line)
MODEL_UNIT = "Millimeters"
_side = {}


def _sibling(name):
    """Load a sibling pure module by FILE PATH (coords is itself loaded that way by
    the pure suites, so a package-relative import is not available)."""
    mod = _side.get(name)
    if mod is None:
        import importlib.util as _ilu
        import os as _os

        spec = _ilu.spec_from_file_location(
            f"_coords_{name}", _os.path.join(_os.path.dirname(_os.path.abspath(
                __file__)), f"{name}.py"))
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _side[name] = mod
    return mod


def set_model_unit(name):
    global MODEL_UNIT
    MODEL_UNIT = name or "Millimeters"


def parse_scalar(text):
    """A typed number, length or formula -> ``(value_in_model_units, error)``."""
    t = (text or "").strip()
    if not t:
        return None, "empty number"
    try:
        return float(t), None
    except ValueError:
        pass
    value, err = _sibling("calc").evaluate(t, MODEL_UNIT)
    if err:
        return None, f"{t!r} is not a number ({err})"
    return float(value), None


def split_top_level(text, sep=","):
    """`text` split on `sep` OUTSIDE parentheses (``pow(2,3),4`` -> 2 parts)."""
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _number(text):
    """Parse one coordinate component. Returns (value, error)."""
    t = text.strip()
    if not t:
        return None, "empty coordinate"
    return parse_scalar(t)


def _native_coords():
    """Lazily resolve the native pure-core adapter, or ``None``.

    Identical to the ``bearing._native_bearing()`` / ``calc._native_calc()``
    accessors: returns the ``native_pure`` module when it can be imported
    (production, where this file is ``narwhal3d.coords``) or loaded from this
    directory (spike tests load this module by file path, so ``narwhal3d`` is
    not an importable package there). ``None`` means "use the unchanged Python
    parser" -- the native library is absent, it predates the coordinate export,
    or the adapter cannot be imported.
    """
    try:
        from . import native_pure as _mod  # production package context
        return _mod
    except ImportError:
        pass
    try:
        import importlib.util as _ilu
        import os as _os
        import sys as _sys
        _canon = "narwhal3d_native_pure"
        if _canon in _sys.modules:
            return _sys.modules[_canon]
        _path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                              "native_pure.py")
        if not _os.path.isfile(_path):
            return None
        _spec = _ilu.spec_from_file_location(_canon, _path)
        _mod = _ilu.module_from_spec(_spec)
        _sys.modules[_canon] = _mod
        _spec.loader.exec_module(_mod)
        return _mod
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None


def parse(text):
    """Split typed text into ``(coords, is_world, is_relative, error)``.

    ``coords`` is a 3-tuple of floats in whichever system the flags describe.
    On failure ``coords`` is None and ``error`` explains why, in the reference CAD application's terms.
    """
    if isinstance(text, str):
        _nc = _native_coords()
        if _nc is not None:
            native = _nc._native_parse(text, MODEL_UNIT)
            if native is not None:
                x, y, z, is_world, is_relative = native
                return (x, y, z), is_world, is_relative, None
    raw = (text or "").strip()
    if not raw:
        return None, False, False, "no coordinate given"
    # the reference CAD application: "There are no spaces between the coordinate values."
    if any(c.isspace() for c in raw):
        return None, False, False, "a coordinate may not contain spaces"

    # --- forms we explicitly refuse rather than guess at -------------------
    if raw.startswith("."):
        return None, False, False, f"point filters like {raw!r} are {UNSUPPORTED}"
    if raw.startswith("<"):
        # a bare `<angle` is an ANGLE CONSTRAINT on the next pick, not a point
        return None, False, False, (
            f"{raw!r} is an angle constraint in the reference CAD application, not a coordinate")

    # --- prefixes, longest first so `rw` is not eaten by `r` ---------------
    rest = raw
    is_relative = False
    is_world = False
    for prefix in ("rw", "@w", "w", "r", "@"):
        if rest.lower().startswith(prefix):
            is_relative = prefix in ("rw", "@w", "r", "@")
            is_world = "w" in prefix
            rest = rest[len(prefix):]
            break

    if not rest:
        return None, False, False, f"{raw!r} has a prefix but no coordinates"

    if "<" in rest:
        return _parse_polar(raw, rest, is_world, is_relative)

    parts = split_top_level(rest)
    if len(parts) > 3:
        return None, False, False, f"{raw!r} has more than three coordinates"

    if len(parts) == 1:
        # Documented shortcuts: `0` means 0,0 and `w0` means w0,0,0. Any OTHER
        # bare number is a distance constraint, not a coordinate.
        value, err = _number(parts[0])
        if err:
            return None, False, False, err
        if value == 0.0:
            return (0.0, 0.0, 0.0), is_world, is_relative, None
        return None, False, False, (
            f"a bare number is a distance constraint in the reference CAD application (it locks the "
            f"next pick's distance from the last point), not a coordinate; "
            f"type x,y or x,y,z for a point")

    values = []
    for part in parts:
        value, err = _number(part)
        if err:
            return None, False, False, err
        values.append(value)
    while len(values) < 3:
        values.append(0.0)      # "When you omit the z coordinate, it is ... 0"
    return tuple(values), is_world, is_relative, None


def _parse_polar(raw, rest, is_world, is_relative):
    """``d<a`` / ``d<a,z`` (prefix already stripped) -> cartesian in the same system.

    MEASURED (accurate_modeling.htm, "Polar coordinates"): "a point that is a
    distance and direction away from 0,0 in the current construction plane",
    angle from 3 o'clock, counter-clockwise; "Relative polar coordinates are
    preceded by R or @"; ``w17<45`` / ``rw17<45`` are the world forms;
    ``17<45,8`` adds an elevation. Polar is just another way of writing x,y in
    whichever system the prefix chose, so the result goes through the same
    `resolve` path as a cartesian entry.
    """
    parts = rest.split("<")
    # SPHERICAL, MEASURED forms (accurate_modeling.htm "Other coordinates"):
    #   "Spherical (distance<rotation angle<z elevation angle) 5<30<45"
    #   "Spherical (x,y<elevation angle) 5,6<15"
    # DERIVED, flagged: "elevation angle" is read as the angle UP FROM THE CPLANE (its
    # ordinary meaning -- latitude), the rotation angle as in polar (from x, CCW).
    if len(parts) == 3 and "," not in parts[0]:
        d, err = _number(parts[0])
        a1, err1 = parse_angle(parts[1])
        a2, err2 = parse_angle(parts[2])
        if err or err1 or err2:
            return None, False, False, err or err1 or err2
        az, el = math.radians(a1), math.radians(a2)
        return ((d * math.cos(el) * math.cos(az), d * math.cos(el) * math.sin(az),
                 d * math.sin(el)), is_world, is_relative, None)
    if len(parts) == 2 and "," in parts[0]:
        xy = split_top_level(parts[0])
        a2, err2 = parse_angle(parts[1])
        if len(xy) != 2 or err2:
            return None, False, False, err2 or f"{raw!r} is not x,y<angle"
        (x, ex), (y, ey) = _number(xy[0]), _number(xy[1])
        if ex or ey:
            return None, False, False, ex or ey
        return ((x, y, math.hypot(x, y) * math.tan(math.radians(a2))),
                is_world, is_relative, None)
    if len(parts) > 2:
        return None, False, False, f"{raw!r} has too many '<' for a coordinate"
    right = parts[1]
    if right[:1].upper() in ("N", "S"):
        # Surveyor bearings use the documented `11<N30d22'54.43"W` form.
        got, err = _sibling("bearing").parse_surveyor(f"{parts[0]}<{right}")
        if err:
            return None, False, False, err
        dist, ang = got
        a = math.radians(ang)
        return (dist * math.cos(a), dist * math.sin(a), 0.0), is_world, is_relative, None
    dist, err = _number(parts[0])
    if err:
        return None, False, False, err
    pieces = split_top_level(right)
    if len(pieces) > 2:
        return None, False, False, f"{raw!r} has too many values after the angle"
    ang, err = parse_angle(pieces[0])
    if err:
        return None, False, False, err
    z = 0.0
    if len(pieces) == 2:
        z, err = _number(pieces[1])
        if err:
            return None, False, False, err
    a = math.radians(ang)
    return (dist * math.cos(a), dist * math.sin(a), z), is_world, is_relative, None


def resolve(text, cplane, last_point=None):
    """Typed text -> an ``(x, y, z)`` WORLD point. Returns ``(point, error)``.

    `cplane` is the ACTIVE viewport's construction plane (a `cplane.CPlane`);
    `last_point` is the previously picked world point, needed by the relative
    forms. Exactly one of `point` / `error` is None.
    """
    coords, is_world, is_relative, err = parse(text)
    if err:
        return None, err
    u, v, w = coords

    if is_relative:
        if last_point is None:
            return None, "there is no previous point for a relative coordinate"
        if is_world:
            delta = (u, v, w)
        else:
            # relative CPlane: offset along the CPlane's AXES, without moving to
            # its origin -- "r2,3 ... 2 units in the x direction and 3 units in
            # the y direction from the previous point".
            delta = cplane.vector_to_world(u, v, w)
        return (last_point[0] + delta[0],
                last_point[1] + delta[1],
                last_point[2] + delta[2]), None

    if is_world:
        return (u, v, w), None
    return cplane.to_world(u, v, w), None


def format_world(point, precision=None):
    """A WORLD point -> text :func:`resolve` will read back as the same point.

    Use this for any point the program already knows in world coordinates -- a
    mouse pick, a snap, a computed location -- before feeding it to a command.

    WHY THE WORLD PREFIX IS REQUIRED
    --------------------------------
    A viewport click is converted to a WORLD point, then handed to the command as
    text. It used to be formatted bare (``"5.0000,0.0000,3.0000"``), and bare text
    is read in the **CPlane's** coordinates -- which is correct for something a
    *user* typed and wrong for something we already resolved. The point was
    therefore transformed twice:

        click at world (5, 0, 3) in Front  ->  fed "5,0,3"  ->  (5, -3, 0)

    so the centre dropped onto z = 0 ("it always goes down"), and in Right the
    components scrambled to (3, 0, 5). In Top the CPlane *is* world XY, so the
    double transform was the identity and the bug was invisible -- which is why it
    only showed up in Front and Right.

    The ``w`` prefix is the reference CAD application's own documented way to say "these are world
    coordinates" (``user_interface/accurate_modeling.htm``: "Type ``w`` in front of
    the coordinates, to use the world coordinate system"), so this is fidelity,
    not a workaround.

    The required invariant for every CPlane is:

        resolve(format_world(p), any_plane) == p

    **EXACTLY**, not to 4 decimals. This used to format with ``:.4f``, so every
    click and every SNAP was rounded to 1e-4 on its way into the command: an End
    snap onto a point at x = 1/3 landed at 0.3333, and a distance-constrained pick
    of exactly 3 at 45 degrees came out 2.1213. For geometry that goes to a machine shop that is a defect, not a
    display nicety. The old round-trip test could not see it because every test
    point had four decimals or fewer. ``repr`` is Python's shortest string that
    reads back as the identical float, so nothing is lost. `precision` is kept for a
    caller that wants a short, human-readable echo, and must ask for it.
    """
    x, y, z = (float(c) for c in point)
    if precision is None:
        return f"w{x!r},{y!r},{z!r}"
    return f"w{x:.{precision}f},{y:.{precision}f},{z:.{precision}f}"
