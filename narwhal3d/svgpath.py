"""SVG path data -> cubic Béziers.

``parse_path(d) -> list[subpath]`` where each subpath is::

    {"beziers": [((x0,y0),(x1,y1),(x2,y2),(x3,y3)), ...], "closed": bool}

Every segment -- line, quadratic, elliptical arc -- is output as a CUBIC
Bézier ``(p0, c1, c2, p1)`` so a single downstream consumer handles all
kinds.  Coordinates are the *model* coordinates (SVG user-space); ``z`` is
not part of an SVG path and is not produced here.

Handled (absolute **and** relative):

* ``M``/``m`` -- start a new subpath at ``(x, y)``; any further coordinate
  pairs in the same command are implicit ``L``/``l`` (per the SVG spec).
  Every ``M``/``m`` opens a fresh subpath.  A relative ``m`` with no current
  point is treated as absolute (SVG spec).
* ``L``/``l``, ``H``/``h``, ``V``/``v`` -- lines; emitted as cubics with
  control points at the 1/3 and 2/3 marks (a line is the degree-1 case of a
  cubic; this keeps every output segment cubic).
* ``C``/``c`` -- cubic Bézier, stored directly.
* ``S``/``s`` -- smooth cubic: the first control point is the REFLECTION of
  the previous command's second control point about the current point
  (or the current point itself when the previous command was not ``C``/``S``).
* ``Q``/``q`` -- quadratic Bézier, elevated EXACTLY to a cubic:
  ``c1 = P0 + (2/3)(P1 − P0)`` and ``c2 = P2 + (2/3)(P1 − P2)`` where ``P1``
  is the quadratic control point.
* ``T``/``t`` -- smooth quadratic: the control point is the REFLECTION of
  the previous ``Q``/``T`` control point about the current point (or the
  current point itself when the previous command was not ``Q``/``T``),
  then elevated exactly as ``Q``.
* ``A``/``a`` -- elliptical arc, per the SVG spec's endpoint-to-centre
  conversion (spec appendix F.6).  The arc is split into segments of at most
  90°; each segment is approximated by a cubic with the standard handle
  length ``k = (4/3)·tan(|step|/4)`` (signed by the sweep direction).  An arc
  with ``rx = 0`` or ``ry = 0`` degenerates to a line (per the SVG spec).
* ``Z``/``z`` -- close the subpath: if the current point differs from the
  subpath's start, emit a closing line (as a cubic) and set
  ``closed = True``.  ``closed`` is only set by an explicit ``Z``; a path
  that happens to end at its start without ``Z`` is not closed.

Number formats accepted: ``-1.5e-3``, ``.5.5`` (two numbers ``.5`` and
``.5`` -- the dot after a digit ends the first number), ``1e5``, commas /
whitespace / command letters as separators.  Command letters keep their
original case; upper/lower selects absolute/relative (``rel = the letter is
lowercase``).

# OURS: the error bound for the arc approximation.  A 90° arc of radius
``r`` approximated by a single cubic (handle ``k = (4/3)·tan(π/8)``) has
maximum radial deviation ``< 2.7e-4 · r``.  Splitting into ≤ 90° segments
keeps the total error at or below that per-segment bound; a full circle from
two 180° arcs (four 90° segments) stays within ``3e-4 · r`` of the true
circle, which is what the test suite asserts.
"""
from __future__ import annotations

import json
import math

# Commands and the number of parameters each consumes.  Keys are the
# UPPERCASE (absolute) form; relative commands are matched case-insensitively.
_CMD_ARITY = {
    "M": 2, "L": 2, "H": 1, "V": 1,
    "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0,
}


def _tokenize(s):
    """Split SVG path data into command letters (original case) and floats.

    Handles ``-1.5e-3``, ``.5.5`` (digit-before-dot ends the first number),
    ``1e5``, commas, whitespace, and command letters as separators.  Command
    letters are kept as written so the parser can tell absolute from relative.
    """
    tokens = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch in " \t\r\n,;":
            i += 1
        elif ch in "MmLlHhVvCcSsQqTtAaZz":
            tokens.append(ch)
            i += 1
        else:
            # start of a number: optional sign, digits, optional .digits,
            # optional e/E [sign] digits.  A dot that is NOT followed by a
            # digit does NOT belong to this number (it starts the next one,
            # as in ".5.5").
            j = i
            if j < n and s[j] in "+-":
                j += 1
            while j < n and s[j].isdigit():
                j += 1
            # optional fractional part: "." followed by at least one digit
            if j < n and s[j] == "." and j + 1 < n and s[j + 1].isdigit():
                j += 1
                while j < n and s[j].isdigit():
                    j += 1
            # optional exponent: e/E [sign] digits
            if j < n and s[j] in "eE":
                k = j + 1
                if k < n and s[k] in "+-":
                    k += 1
                if k < n and s[k].isdigit():
                    while k < n and s[k].isdigit():
                        k += 1
                    j = k
                # else: 'e'/'E' with no exponent digits -- not part of this
                # number (it is a command letter, handled by the loop above)
            if j == i:
                # no number was consumed -- should not happen for valid input
                i += 1
                continue
            tokens.append(float(s[i:j]))
            i = j
    return tokens


def _native_svgpath():
    """Lazily resolve the native pure-core adapter, or ``None``.

    Identical to the ``macro_plan._native_macro_plan()`` / ``macro._native_macro()``
    accessors: returns the ``native_pure`` module when it can be imported
    (production, where this file is ``narwhal3d.svgpath``) or loaded from this
    directory (spike tests load this module by file path, so ``narwhal3d`` is
    not an importable package there). ``None`` means "use the unchanged Python
    parser" -- the native library is absent, it predates the SVG export, or the
    adapter cannot be imported.
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


def _subpaths_from_json(payload):
    """Convert the native JSON subpath list into the Python subpath contract.

    ``json.loads`` returns each Bézier as a *list* of four 2-lists and the
    ``closed`` flag as a bool; the Python reference emits each Bézier as a
    4-tuple of 2-tuples, so each is converted back so the result is
    byte-for-byte the Python output. Everything else (the ``closed`` bool, the
    numeric coordinates) round-trips exactly through JSON.
    """
    subpaths = json.loads(payload)
    for sp in subpaths:
        if isinstance(sp, dict) and isinstance(sp.get("beziers"), list):
            sp["beziers"] = [tuple(tuple(pt) for pt in bez) for bez in sp["beziers"]]
    return subpaths


def _subpaths_from_packed(pts, offs, flags, n_pts, n_subs):
    """Convert the native packed subpaths into the Python subpath contract.

    ``pts`` holds ``8 * n_pts`` floats -- each Bézier ``p0 c1 c2 p1`` as two
    coordinate pairs -- in parse order; ``offs[s]`` is the Bézier index where
    subpath ``s`` begins; ``flags[s]`` is ``1`` when subpath ``s`` was closed
    by an explicit ``Z``/``z``.  The Python reference emits each Bézier as a
    4-tuple of 2-tuples and ``closed`` as a bool, so those are rebuilt here
    and the result is byte-for-byte the Python output (the f64 values are
    the parser's exact floats, no round-trip loss).

    Only called with output the strict packed validator already accepted
    (finite points, valid offsets/flags, complete consumption), so the
    slicing here is in bounds by construction.
    """
    subpaths = []
    for s in range(n_subs):
        start = offs[s]
        end = offs[s + 1] if s + 1 < n_subs else n_pts
        subpaths.append({
            "beziers": [
                ((pts[b * 8], pts[b * 8 + 1]),
                 (pts[b * 8 + 2], pts[b * 8 + 3]),
                 (pts[b * 8 + 4], pts[b * 8 + 5]),
                 (pts[b * 8 + 6], pts[b * 8 + 7]))
                for b in range(start, end)
            ],
            "closed": bool(flags[s]),
        })
    return subpaths


def parse_path(d):
    """Parse SVG path data ``d`` -> ``list[{"beziers":[...], "closed":bool}]``.

    See the module docstring for the full contract.  Raises ``ValueError``
    on a malformed command (wrong parameter count) or a segment before any
    moveto.

    At or above the measured crossover the native packed-output ABI
    (Q117-03: one parse, one write into caller-owned arrays, no JSON) runs
    instead -- it is >=10% faster there end-to-end, and a successful packed
    result is the exact Python contract.  Below the crossover, for a
    malformed path, a rejected input, or an unavailable library the
    unchanged Python parser below runs in full; the legacy JSON ABI is not
    used in production at all (measured slower at every tested size through
    the 1 MiB native input limit).
    """
    if isinstance(d, str):
        _np = _native_svgpath()
        if _np is not None and _np._svg_packed_native_active(d):
            packed = _np._native_svgpath_packed(d)
            if packed is not None:
                return _subpaths_from_packed(*packed)
    toks = _tokenize(d)

    subpaths = []
    cur = None          # current point
    start = None        # subpath start (for Z)
    last_c2 = None      # previous C/S second control point (for S reflection)
    last_q = None       # previous Q/T control point (for T reflection)
    cur_sub = None      # the subpath dict currently being built

    def _line_to(pt):
        nonlocal cur
        cur_sub["beziers"].append(_line_cubic(cur, pt))
        cur = pt

    def _flush():
        nonlocal cur_sub
        if cur_sub is not None:
            subpaths.append(cur_sub)
            cur_sub = None

    def _start_subpath(pt):
        nonlocal cur, start, cur_sub, last_c2, last_q
        _flush()
        cur = pt
        start = pt
        cur_sub = {"beziers": [], "closed": False}
        last_c2 = None
        last_q = None

    def _ensure_open():
        # A segment that follows a Z (cur_sub is None but cur is set) opens a
        # fresh subpath at the current point.
        nonlocal start, cur_sub, last_c2, last_q
        if cur_sub is None:
            start = cur
            cur_sub = {"beziers": [], "closed": False}
            last_c2 = None
            last_q = None

    i = 0
    n = len(toks)
    while i < n:
        tok = toks[i]
        if not isinstance(tok, str):
            raise ValueError(f"unexpected number {tok!r} outside a command")
        cmd = tok
        eff = cmd.upper()
        rel = cmd.islower()
        arity = _CMD_ARITY[eff]
        i += 1

        # --- Z / z: zero-arity -- close the current subpath --------------
        if eff == "Z":
            if cur_sub is not None:
                if cur != start:
                    _line_to(start)
                cur = start
                cur_sub["closed"] = True
                _flush()
                last_c2 = None
                last_q = None
            # a Z with no open subpath is a no-op
            continue

        # --- every other command consumes one or more parameter groups ----
        if cur is None and eff != "M":
            raise ValueError(f"command {eff} before any moveto (M)")

        first = True
        while i < n and isinstance(toks[i], (int, float)):
            params = []
            while len(params) < arity and i < n and isinstance(toks[i], (int, float)):
                params.append(float(toks[i]))
                i += 1
            if len(params) < arity:
                raise ValueError(
                    f"command {eff} needs {arity} params, got {len(params)}")

            if eff == "M" and first:
                # moveto: open a new subpath.  A relative m with no current
                # point is treated as absolute (SVG spec).
                x, y = params
                if rel and cur is not None:
                    x += cur[0]; y += cur[1]
                _start_subpath((x, y))
                first = False
            else:
                _ensure_open()
                if eff == "M":
                    # further M groups are implicit linetos (SVG spec)
                    x, y = params
                    if rel:
                        x += cur[0]; y += cur[1]
                    _line_to((x, y))
                elif eff == "L":
                    x, y = params
                    if rel:
                        x += cur[0]; y += cur[1]
                    _line_to((x, y))
                elif eff == "H":
                    x = params[0]
                    if rel:
                        x += cur[0]
                    _line_to((x, cur[1]))
                elif eff == "V":
                    y = params[0]
                    if rel:
                        y += cur[1]
                    _line_to((cur[0], y))
                elif eff == "C":
                    x1, y1, x2, y2, x, y = params
                    if rel:
                        x1 += cur[0]; y1 += cur[1]
                        x2 += cur[0]; y2 += cur[1]
                        x += cur[0]; y += cur[1]
                    cur_sub["beziers"].append(
                        ((cur[0], cur[1]), (x1, y1), (x2, y2), (x, y)))
                    last_c2 = (x2, y2)
                    last_q = None
                    cur = (x, y)
                elif eff == "S":
                    x2, y2, x, y = params
                    if rel:
                        x2 += cur[0]; y2 += cur[1]
                        x += cur[0]; y += cur[1]
                    if last_c2 is not None:
                        x1 = 2 * cur[0] - last_c2[0]
                        y1 = 2 * cur[1] - last_c2[1]
                    else:
                        x1, y1 = cur
                    cur_sub["beziers"].append(
                        ((cur[0], cur[1]), (x1, y1), (x2, y2), (x, y)))
                    last_c2 = (x2, y2)
                    last_q = None
                    cur = (x, y)
                elif eff == "Q":
                    x1, y1, x, y = params
                    if rel:
                        x1 += cur[0]; y1 += cur[1]
                        x += cur[0]; y += cur[1]
                    cur_sub["beziers"].append(_quad_cubic(cur, (x1, y1), (x, y)))
                    last_q = (x1, y1)
                    last_c2 = None
                    cur = (x, y)
                elif eff == "T":
                    x, y = params
                    if rel:
                        x += cur[0]; y += cur[1]
                    if last_q is not None:
                        qx = 2 * cur[0] - last_q[0]
                        qy = 2 * cur[1] - last_q[1]
                    else:
                        qx, qy = cur
                    cur_sub["beziers"].append(_quad_cubic(cur, (qx, qy), (x, y)))
                    last_q = (qx, qy)
                    last_c2 = None
                    cur = (x, y)
                elif eff == "A":
                    rx, ry, rot, large, sweep, x, y = params
                    if rel:
                        x += cur[0]; y += cur[1]
                    if rx == 0.0 or ry == 0.0:
                        _line_to((x, y))
                    elif (x, y) != cur:
                        cur_sub["beziers"].extend(
                            _arc_cubic(cur, (x, y), rx, ry, rot,
                                       bool(large), bool(sweep)))
                        cur = (x, y)
                    last_c2 = None
                    last_q = None
        # end of parameter groups for this command

    _flush()
    return subpaths


# ---------------------------------------------------------------------------
# segment -> cubic conversions
# ---------------------------------------------------------------------------

def _line_cubic(p0, p1):
    """A straight line as a cubic Bézier (control points at 1/3 and 2/3)."""
    x0, y0 = p0
    x1, y1 = p1
    return ((x0, y0),
            (x0 + (x1 - x0) / 3.0, y0 + (y1 - y0) / 3.0),
            (x0 + 2.0 * (x1 - x0) / 3.0, y0 + 2.0 * (y1 - y0) / 3.0),
            (x1, y1))


def _quad_cubic(p0, qc, p1):
    """Elevate a quadratic Bézier (control ``qc``) EXACTLY to a cubic."""
    x0, y0 = p0
    x1, y1 = qc
    x2, y2 = p1
    c1 = (x0 + (2.0 / 3.0) * (x1 - x0),
          y0 + (2.0 / 3.0) * (y1 - y0))
    c2 = (x2 + (2.0 / 3.0) * (x1 - x2),
          y2 + (2.0 / 3.0) * (y1 - y2))
    return ((x0, y0), c1, c2, (x2, y2))


def _arc_cubic(p0, p1, rx, ry, rot, large, sweep):
    """Elliptical arc (p0 -> p1) as a list of cubic Béziers.

    Per the SVG spec's endpoint-to-centre parametrization (appendix F.6).
    The arc is split into segments of at most 90°; each is approximated by
    a cubic with handle length ``k = (4/3)·tan(|step|/4)`` (signed by the
    sweep direction).  The per-segment error for a 90° arc of radius ``r``
    is ``< 2.7e-4 · r`` (see the module docstring, ``# OURS``).
    """
    x1, y1 = p0
    x2, y2 = p1
    if (x1, y1) == (x2, y2):
        return []  # no-op arc (endpoint == start)

    phi = math.radians(rot)
    cp, sp = math.cos(phi), math.sin(phi)

    # --- F.6: endpoint-to-centre conversion ------------------------------
    # half chord in the rotated frame
    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0
    x1p =  cp * dx + sp * dy
    y1p = -sp * dx + cp * dy

    # ensure radii are large enough for the chord (SVG spec)
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s

    num = ((rx * rx) * (ry * ry)
           - (rx * rx) * (y1p * y1p)
           - (ry * ry) * (x1p * x1p))
    den = ((rx * rx) * (y1p * y1p)
           + (ry * ry) * (x1p * x1p))
    co = math.sqrt(max(0.0, num) / den) if den > 0 else 0.0
    if large == sweep:
        co = -co
    cxp =  co * (rx * y1p) / ry
    cyp = -co * (ry * x1p) / rx

    # centre in the model frame (F.6 step 3 includes the chord midpoint)
    cx =  cp * cxp - sp * cyp + (x1 + x2) / 2.0
    cy =  sp * cxp + cp * cyp + (y1 + y2) / 2.0

    # angles (in the rotated frame, on the unit circle scaled by rx, ry)
    ux =  (cp * (x1 - cx) + sp * (y1 - cy)) / rx
    uy = -(sp * (x1 - cx) - cp * (y1 - cy)) / ry
    vx =  (cp * (x2 - cx) + sp * (y2 - cy)) / rx
    vy = -(sp * (x2 - cx) - cp * (y2 - cy)) / ry
    a0 = math.atan2(uy, ux)
    dtheta = math.atan2(vy, vx) - a0
    # the large flag is already encoded in the sign of `co`; only the sweep
    # direction needs adjusting here (F.6 step 4).
    if not sweep and dtheta > 0:
        dtheta -= 2.0 * math.pi
    elif sweep and dtheta < 0:
        dtheta += 2.0 * math.pi

    # --- split into <= 90° segments, each -> one cubic -------------------
    beziers = []
    remaining = dtheta
    while abs(remaining) > 1e-12:
        step = remaining
        if abs(step) > math.pi / 2.0:
            step = (math.pi / 2.0) * (1 if step > 0 else -1)
        a_s = a0
        a_e = a0 + step
        k = (4.0 / 3.0) * math.tan(abs(step) / 4.0)
        # points on the ellipse (rotated frame)
        sx, sy = rx * math.cos(a_s), ry * math.sin(a_s)
        ex, ey = rx * math.cos(a_e), ry * math.sin(a_e)
        # tangent directions (rotated frame)
        tx0, ty0 = -rx * math.sin(a_s),  ry * math.cos(a_s)
        tx1, ty1 = -rx * math.sin(a_e),  ry * math.cos(a_e)
        # control points (rotated frame); handle length k, signed by the
        # sweep direction so a clockwise arc mirrors a counter-clockwise one
        sgn = 1.0 if step > 0 else -1.0
        c1 = (sx + k * sgn * tx0, sy + k * sgn * ty0)
        c2 = (ex - k * sgn * tx1, ey - k * sgn * ty1)

        def rot(p):
            px, py = p
            return (cp * px - sp * py + cx,
                    sp * px + cp * py + cy)

        beziers.append((rot((sx, sy)), rot(c1), rot(c2), rot((ex, ey))))
        a0 = a_e
        remaining -= step
    return beziers
