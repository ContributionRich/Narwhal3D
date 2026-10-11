"""`Extend` a curve's end (pure: rhino3dm only, no bpy).

`Curve.Extend` does not exist in rhino3dm 8.35.0, nor do `ExtendByLine`,
`ExtendByArc`, or `Append` methods on a curve.

What the reference CAD application's `Extend` actually says
-----------------------------------
the reference CAD application's documented `Type` options are:

    Natural  Lines, polylines, and polycurves with a line at the end to extend
             are extended by line. Arcs and polycurves with arc at the end to
             extend are extended by same radius arc. Other curves are extended
             smooth.
    Line     Makes a line extension tangent to the original curve.
    Arc      Makes an arc extension tangent to the original curve.
             (sub-options: Center -- pick the arc's centre and endpoint;
              ToPoint -- pick the arc's endpoint)
    Smooth   Makes a smooth curve extension curvature continuous with the
             original curve.  (sub-option: ToPoint)

and, on joining:

    "The extension will be automatically joined with the original curve."
    Join (Yes/No/Merge) ... Merge -- "The extension is merged into the curve
    itself rather than added as a segment. When the extension is the same kind of
    geometry as the curve end -- a line extending a line, a same-radius arc
    extending an arc, or a smooth extension of a free-form curve -- the result is
    a single, seamless curve span. This produces the cleanest result and is the
    default."

So for the two types in scope the correct default result is **one curve**, not a
joined pair: a longer line, or a wider arc on the same circle. That is what this
module builds, and it is exact in both cases. Where the extension is a different
kind of geometry from the curve's end (a tangent line off a free-form NURBS) Merge
is impossible by the reference CAD application's own definition, so the extension is **joined** as a
`PolyCurve` segment -- which is the documented fallback, not a shortcut.

WHAT IS NOT IMPLEMENTED, and must not look implemented
--------------------------------------------------------------
* **`Smooth` is not implemented.** It is curvature-continuous, so it needs the
  end curvature matched (`CurvatureAt` is present, so the ingredients exist): a
  degree-5-or-higher extension whose first AND second derivatives match the
  curve's at the join, with the extension's length as the remaining free
  parameter. That is a fitting problem, not a formula, and half of it would be
  worse than none. :func:`extend_curve` raises with that explanation.
* **`Natural` on a free-form curve therefore also raises**, because the measured
  rule for that case is "Other curves are extended smooth" -- and falling back to
  a line would silently give the user a different curve from the one the reference CAD application makes.
  This is the whole reason `natural_style_for` is a separate, testable function:
  the dispatch is pinned even where the target is not built.
* **`Arc` on a curve whose end is not already an arc** raises. "Makes an arc
  extension tangent to the original curve" fixes the tangent but not the radius,
  and the reference CAD application resolves that with the measured sub-options `Center` and `ToPoint` --
  neither of which is a length. Guessing a radius (from the end curvature, say)
  would be `Smooth` wearing `Arc`'s name.
* `Center`, `ToPoint`, boundary-object extension ("Select a boundary object"),
  extending to multiple boundaries, and the `Join`=`Yes`/`No` variants are not
  implemented either. `Join` is fixed at the documented default (Merge where the
  kinds match, joined otherwise).

A trap worth naming
-------------------
`SetStartPoint` / `SetEndPoint` exist on every curve type and look like exactly
the tool for this. **They are not.** They *deform* the curve -- dragging its end
to a new location and pulling the rest of it out of shape -- rather than adding
new curve beyond the old end. Using them here would move the curve the user asked
to keep. Nothing in this module calls them, on purpose.
"""
from __future__ import annotations

import math

import rhino3dm as r3

try:                                  # normal import, inside the addon package
    from . import units
except ImportError:                   # supports direct file-path loading
    import units


#: The `Type` values documented by the reference CAD application.
STYLES = ("Natural", "Line", "Arc", "Smooth")

#: Which end to extend. the reference CAD application picks this from where you clicked ("Select a curve
#: or an surface edge near the end"); a command must resolve the click to one of
#: these before calling in.
ENDS = ("Start", "End")

#: The largest arc extension allowed, as a total angle. An arc whose span
#: reaches a full turn is a circle, and beyond that it would overlap itself. The
#: help does not say what the reference CAD application does there, so this is a documented choice and is
#: refused rather than silently clamped.
MAX_ARC_SPAN = 2.0 * math.pi

_SMOOTH_MESSAGE = (
    "Extend Type=Smooth is not implemented yet. It is curvature-continuous "
    "(commands/extend.htm: \"Makes a smooth curve extension curvature continuous "
    "with the original curve\"), which needs an extension whose first AND second "
    "derivatives match the curve's at the join -- a fit, not a formula. Use "
    "Type=Line or Type=Arc, or extend a line/polyline/arc where Natural resolves "
    "to one of those")


def default_tolerance():
    """The document's absolute tolerance, in model units.

    the reference CAD application's default is 0.01 mm. Read through `units.py` rather than written as a literal.
    """
    # Use the document's tolerance in model units, not a fixed 0.01 default.
    return units.document_tolerance()


# ---------------------------------------------------------------------------
# the Natural dispatch -- a separate function so the RULE can be tested even
# where the target style is not implemented
# ---------------------------------------------------------------------------

def natural_style_for(curve, end="End"):
    """Which style `Natural` resolves to for this curve and end.

    Returns ``"Line"``, ``"Arc"`` or ``"Smooth"``, straight from the measured
    sentence: "Lines, polylines, and polycurves with a line at the end to extend
    are extended by line. Arcs and polycurves with arc at the end to extend are
    extended by same radius arc. Other curves are extended smooth."

    Note it inspects the END SEGMENT, not the whole curve -- that is what
    "polycurves with a line at the end to extend" means, and it is why the answer
    depends on `end`.
    """
    seg = _end_segment(curve, _normalise_end(end))
    if _polyline_verts(seg) is not None:
        return "Line"
    if _try(seg, "TryGetArc") is not None:
        return "Arc"
    return "Smooth"


# ---------------------------------------------------------------------------
# the public entry point
# ---------------------------------------------------------------------------

def extend_curve(curve, end, length, style="Natural", tolerance=None):
    """Extend `curve` at `end` ("Start" or "End") by `length`, using `style`.

    Returns a new curve; the input is never mutated. Where the reference CAD application's default
    ``Join=Merge`` applies -- a line extending a line, a same-radius arc
    extending an arc -- the result is ONE curve (a longer line / polyline, or a
    wider arc on the same circle), exactly as measured. Otherwise the tangent
    line extension is joined on as a `PolyCurve` segment.

    Raises ValueError for: a non-positive length, a closed curve (it has no end),
    an unknown end or style, `Smooth` (not implemented -- see the module
    docstring), `Natural` resolving to `Smooth`, `Arc` on a curve whose end is
    not an arc, and an arc extension that would wrap past a full turn.
    """
    tol = default_tolerance() if tolerance is None else float(tolerance)
    which = _normalise_end(end)
    length = float(length)

    if not length > 0.0:
        raise ValueError(
            f"Extend needs a positive length; got {length!r}. the reference CAD application asks you to "
            "\"type a length\" or pick the end of the extension, and neither can "
            "be zero or negative")

    if _is_closed(curve):
        raise ValueError(
            "a closed curve has no end to extend. the reference CAD application's Extend asks you to "
            "\"Select a curve ... near the end\"; a closed curve has none. Split "
            "it first if you meant to open it -- this will not open it for you")

    resolved = _normalise_style(style)
    if resolved == "Natural":
        resolved = natural_style_for(curve, which)

    if resolved == "Smooth":
        raise ValueError(_SMOOTH_MESSAGE)
    if resolved == "Line":
        return _extend_by_line(curve, which, length, tol)
    if resolved == "Arc":
        return _extend_by_arc(curve, which, length, tol)
    raise ValueError(f"unknown Extend type {style!r}; valid: {', '.join(STYLES)}")


# ---------------------------------------------------------------------------
# Line
# ---------------------------------------------------------------------------

def _extend_by_line(curve, which, length, tol):
    """"Makes a line extension tangent to the original curve." (MEASURED)

    A line or polyline MERGES: the end vertex simply moves out along the end
    segment's own direction, which is also the tangent there, so the result is one
    seamless curve with the same vertex count. Anything else gets the tangent line
    joined on as a segment, because Merge is defined only "when the extension is
    the same kind of geometry as the curve end".
    """
    verts = _polyline_verts(curve)
    if verts is not None and len(verts) >= 2:
        return _extend_polyline(verts, which, length)

    p, d = _end_point_and_outward_tangent(curve, which)
    ext = _line_extension(p, d, length, which)
    return _joined(curve, which, ext, tol)


def _extend_polyline(verts, which, length):
    """Move the end vertex out along the end segment. Exact, and MERGED."""
    verts = [tuple(float(c) for c in v) for v in verts]
    if which == "End":
        tip, prev = verts[-1], verts[-2]
    else:
        tip, prev = verts[0], verts[1]
    d = _unit(_sub(tip, prev))
    if d is None:
        raise ValueError(
            "the polyline's end segment has zero length, so it has no direction "
            "to extend along")
    moved = (tip[0] + d[0] * length, tip[1] + d[1] * length, tip[2] + d[2] * length)
    if which == "End":
        verts[-1] = moved
    else:
        verts[0] = moved
    pts = [r3.Point3d(*v) for v in verts]
    # two vertices is a LINE; the reference CAD application's Merge makes that "a single, seamless curve
    # span", and a LineCurve is the honest type for it.
    if len(pts) == 2:
        return r3.LineCurve(pts[0], pts[1])
    return r3.PolylineCurve(pts)


def _line_extension(p, d, length, which):
    """The tangent line segment, oriented so it JOINS the curve at `which`.

    Extending the End, the extension must START at the curve's end; extending the
    Start, it must END there. Getting that backwards makes `PolyCurve.Append`
    report a gap.
    """
    far = (p[0] + d[0] * length, p[1] + d[1] * length, p[2] + d[2] * length)
    if which == "End":
        return r3.LineCurve(r3.Point3d(*p), r3.Point3d(*far))
    return r3.LineCurve(r3.Point3d(*far), r3.Point3d(*p))


# ---------------------------------------------------------------------------
# Arc
# ---------------------------------------------------------------------------

def _extend_by_arc(curve, which, length, tol):
    """"Arcs ... are extended by same radius arc." (MEASURED)

    A whole-curve arc MERGES: the same circle, the same centre, the same radius,
    a wider angle span. `Arc.AngleDomain` is assignable (MEASURED) and widening it
    leaves `Radius` and `Center` bit-identical, so the merge is exact.

    A polycurve whose end SEGMENT is an arc gets a continuation arc -- same centre
    and radius, starting where the segment stops -- joined on as a segment. That
    is the measured "polycurves with arc at the end to extend" case; merging it
    into the segment would need surgery on the polycurve and is not attempted.
    """
    arc = _try(curve, "TryGetArc")
    if arc is not None:
        return _widen_arc(curve, arc, which, length, tol)

    seg = _end_segment(curve, which)
    seg_arc = _try(seg, "TryGetArc")
    if seg_arc is not None:
        ext = _continuation_arc(seg, seg_arc, which, length, tol)
        return _joined(curve, which, ext, tol)

    raise ValueError(
        "Extend Type=Arc needs the end being extended to BE an arc. "
        "commands/extend.htm says an arc extension is \"tangent to the original "
        "curve\", which fixes its direction but not its radius; the reference CAD application resolves "
        "that with the Center and ToPoint sub-options, neither of which is a "
        "length. Choosing a radius here (from the end curvature, say) would be "
        "Type=Smooth wearing Type=Arc's name, so it is refused instead")


def _arc_geometry(arc):
    """``(radius, angle_t0, angle_t1)`` with the span checked for sanity."""
    r = float(arc.Radius)
    if not r > 0.0:
        raise ValueError(f"the arc has a non-positive radius ({r!r})")
    dom = arc.AngleDomain
    return r, float(dom.T0), float(dom.T1)


def _check_span(span, delta, length, radius):
    total = abs(span) + delta
    if total >= MAX_ARC_SPAN:
        raise ValueError(
            f"extending this arc by {length!r} adds {math.degrees(delta):.3f} "
            f"degrees to a {math.degrees(abs(span)):.3f}-degree arc of radius "
            f"{radius!r}, giving {math.degrees(total):.3f} degrees -- a full turn "
            f"or more. That is a circle or a self-overlapping arc, not an "
            f"extension; the reference CAD application's help does not say what it does here, so this is "
            f"refused rather than silently clamped")


def _widen_arc(curve, arc, which, length, tol):
    """Same circle, wider angle span. The MERGE case, and exact."""
    radius, a0, a1 = _arc_geometry(arc)
    delta = length / radius
    _check_span(a1 - a0, delta, length, radius)

    if which == "End":
        lo, hi = a0, a1 + delta
        keep_point, keep_which = _pt(curve.PointAtStart), "start"
    else:
        lo, hi = a0 - delta, a1
        keep_point, keep_which = _pt(curve.PointAtEnd), "end"

    arc.AngleDomain = r3.Interval(lo, hi)
    out = r3.NurbsCurve.CreateFromArc(arc)
    if out is None:
        raise ValueError("could not build the widened arc")
    return _orient(out, keep_point, keep_which, tol, "widened arc")


def _continuation_arc(seg, arc, which, length, tol):
    """The next piece of the SAME circle, starting where `seg` stops."""
    radius, a0, a1 = _arc_geometry(arc)
    delta = length / radius
    _check_span(a1 - a0, delta, length, radius)

    if which == "End":
        lo, hi = a1, a1 + delta
        keep_point, keep_which = _pt(seg.PointAtEnd), "start"
    else:
        lo, hi = a0 - delta, a0
        keep_point, keep_which = _pt(seg.PointAtStart), "end"

    arc.AngleDomain = r3.Interval(lo, hi)
    out = r3.NurbsCurve.CreateFromArc(arc)
    if out is None:
        raise ValueError("could not build the continuation arc")
    return _orient(out, keep_point, keep_which, tol, "continuation arc")


def _orient(crv, point, which, tol, what):
    """Make sure `crv`'s start (or end) is `point`, reversing it if not.

    Why this is done by measurement rather than by assumption: `AngleDomain` runs
    in the arc's own plane, and whether the curve travels with increasing angle
    depends on how that plane was derived. So the result is CHECKED and reversed
    if need be, and a failure to orient raises instead of returning a curve that
    runs the wrong way.
    """
    got = _pt(crv.PointAtStart if which == "start" else crv.PointAtEnd)
    if _dist(got, point) <= tol:
        return crv
    crv.Reverse()
    got = _pt(crv.PointAtStart if which == "start" else crv.PointAtEnd)
    if _dist(got, point) <= tol:
        return crv
    raise ValueError(
        f"internal: could not orient the {what} -- its {which} is {got}, which "
        f"is {_dist(got, point):.6g} from the {which} that must be preserved "
        f"{point}. Refusing rather than returning a curve that runs the wrong way")


# ---------------------------------------------------------------------------
# joining
# ---------------------------------------------------------------------------

def _joined(curve, which, extension, tol):
    """`PolyCurve` of the curve and its extension, in the right order.

    the reference CAD application joins the extension to the original curve. `PolyCurve.Append` provides that
    operation, while `RemoveNesting`
    flattens the result when the input was itself a polycurve, so a curve
    extended twice does not grow a tree of nested polycurves.
    """
    pc = r3.PolyCurve()
    first, second = ((curve, extension) if which == "End"
                     else (extension, curve))
    for part in (first, second):
        if not pc.Append(part):
            raise ValueError(
                f"could not append a {type(part).__name__} to the polycurve while "
                f"joining the extension")
    remove = getattr(pc, "RemoveNesting", None)
    if callable(remove):
        remove()
    gap = getattr(pc, "HasGap", None)
    if callable(gap):
        where = gap()
        # HasGap returns the index of the first gap, or 0 for none.
        if where:
            raise ValueError(
                f"the extension does not meet the curve: PolyCurve.HasGap "
                f"reports a gap at segment {where}")
    elif _dist(_pt(first.PointAtEnd), _pt(second.PointAtStart)) > tol:
        raise ValueError("the extension does not meet the curve")
    return pc


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _normalise_end(end):
    key = str(end).strip().lower()
    for name in ENDS:
        if name.lower() == key:
            return name
    raise ValueError(f"unknown end {end!r}; valid: {', '.join(ENDS)}")


def _normalise_style(style):
    key = str(style).strip().lower()
    for name in STYLES:
        if name.lower() == key:
            return name
    raise ValueError(f"unknown Extend type {style!r}; valid: {', '.join(STYLES)}")


def _end_segment(curve, which):
    """The segment whose end is being extended: a PolyCurve's, or the curve itself."""
    count = getattr(curve, "SegmentCount", None)
    getter = getattr(curve, "SegmentCurve", None)
    if count is None or getter is None:
        return curve
    try:
        n = int(count)
    except Exception:  # noqa: BLE001
        return curve
    if n < 1:
        return curve
    try:
        seg = getter(0 if which == "Start" else n - 1)
    except Exception:  # noqa: BLE001
        return curve
    return seg if seg is not None else curve


def _end_point_and_outward_tangent(curve, which):
    """The end point, and the unit tangent pointing AWAY from the curve there."""
    if which == "End":
        p = _pt(curve.PointAtEnd)
        d = _unit(_pt(curve.TangentAtEnd))
    else:
        p = _pt(curve.PointAtStart)
        t = _unit(_pt(curve.TangentAtStart))
        d = None if t is None else (-t[0], -t[1], -t[2])
    if d is None:
        raise ValueError(f"the curve has no tangent at its {which.lower()}")
    return p, d


def _polyline_verts(curve):
    """The polyline vertices of `curve`, or None when it is not a polyline.

    Mirrors `offset._polyline_verts`: `PolylineCurve` exposes `Point(i)` /
    `PointCount`, while a degree-1 NurbsCurve answers `IsPolyline()` and
    `TryGetPolyline()` (MEASURED: a degree-1 NurbsCurve does report
    ``IsPolyline() == True``). A `LineCurve` answers the same way, so a plain line
    takes this path too -- which is what makes "extended by line" one code path
    for lines and polylines alike, as the help's own sentence groups them.
    """
    try:
        if isinstance(curve, r3.PolylineCurve):
            return [_pt(curve.Point(i)) for i in range(curve.PointCount)]
        if isinstance(curve, r3.LineCurve):
            return [_pt(curve.PointAtStart), _pt(curve.PointAtEnd)]
        if curve.IsPolyline():
            pl = curve.TryGetPolyline()
            if pl is not None:
                return [_pt(p) for p in pl]
    except Exception:  # noqa: BLE001
        return None
    return None


def _is_closed(curve):
    try:
        return bool(curve.IsClosed)
    except Exception:  # noqa: BLE001
        return False


def _try(curve, name):
    fn = getattr(curve, name, None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


def _pt(p):
    if hasattr(p, "X"):
        return (p.X, p.Y, p.Z)
    return (float(p[0]), float(p[1]), float(p[2]))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
                     + (a[2] - b[2]) ** 2)


def _unit(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n < 1e-300:
        return None
    return (v[0] / n, v[1] / n, v[2] / n)
