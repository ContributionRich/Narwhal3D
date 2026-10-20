"""The `Divide` command's pure arithmetic.

MEASURED (commands/divide.htm):

    "The Divide command creates point objects along a curve or splits a curve
     by equal length, equal chord length or number of segments."
    "Select curves. Type the number of segments. Or, click the Length or
     EqualChordLength option and enter a number."

Three modes, all pure functions over a sampled polyline ``[(x, y, z), ...]``
(no rhino3dm, so they can be used through direct file-path loading):

* ``by_count(points, n)`` — n+1 positions (both ends included), evenly spaced
  along the ARC length of the curve. n is the number of SEGMENTS (the reference CAD application's
  default), so n=1 gives the two ends; n=0 and n<0 are refused.
* ``by_length(points, length)`` — a point every ``length`` units of ARC length
  from the curve's start. The short tail (remainder < ``length``) is LEFT
  unmarked. **OURS** — the help does not state the point-mode remainder
  behaviour (``DeleteRemainder`` is Split-only); leaving it matches the reference CAD application's
  Split=No default. ``length`` > total arc length is refused rather than
  producing a single point.
* ``by_chord(points, chord)`` — a point every ``chord`` units of STRAIGHT-LINE
  (chord) distance from the previously placed point. On a straight line this
  agrees EXACTLY with ``by_length`` (collinear ⇒ chord = arc); on a curved input the chord span is shorter than
  the arc span, so the positions DIFFER — the whole reason the reference CAD application offers both.

All three raise ``ValueError`` on invalid input so the command can report a
clean refusal rather than producing degenerate geometry.
"""
from __future__ import annotations

import math

_TOL = 1e-9


def _validate(points):
    """Raise ValueError unless `points` is a usable polyline."""
    if not points:
        raise ValueError("need at least one point")
    if len(points) < 2:
        raise ValueError("need at least two points (a degenerate curve)")
    for p in points:
        if len(p) < 2:
            raise ValueError(f"invalid point {p!r} (need at least 2 coords)")


def _cumulative_arc(points):
    """Cumulative arc length at each vertex: ``[0, L1, L1+L2, ..., total]``."""
    cum = [0.0]
    for i in range(1, len(points)):
        cum.append(cum[-1] + math.dist(points[i - 1], points[i]))
    return cum


def _interpolate_arc(points, cum, target):
    """Point on the polyline at cumulative arc position ``target``."""
    total = cum[-1]
    if target <= _TOL:
        return tuple(points[0])
    if target >= total - _TOL:
        return tuple(points[-1])
    for i in range(1, len(cum)):
        if cum[i] >= target - _TOL:
            seg_len = cum[i] - cum[i - 1]
            if seg_len < _TOL:
                return tuple(points[i - 1])
            t = (target - cum[i - 1]) / seg_len
            a, b = points[i - 1], points[i]
            dim = len(a)
            return tuple(a[j] + t * (b[j] - a[j]) for j in range(dim))
    return tuple(points[-1])


def by_count(points, n):
    """n+1 positions along the curve, evenly spaced by ARC length.

    ``n`` is the number of SEGMENTS (the reference CAD application's default Divide mode), so the
    result has ``n + 1`` points including both ends. ``n=1`` gives the two
    ends; ``n=0`` and ``n<0`` are refused.
    """
    _validate(points)
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError("n must be an integer (got %r)" % (n,))
    if n < 1:
        raise ValueError("n must be >= 1 (got %r)" % (n,))
    cum = _cumulative_arc(points)
    total = cum[-1]
    if total < _TOL:
        raise ValueError("the curve has zero length")
    return [_interpolate_arc(points, cum, total * k / n) for k in range(n + 1)]


def by_length(points, length):
    """A point every ``length`` units of ARC length from the curve's start.

    The short tail (remainder < ``length``) is left unmarked. **OURS** — the
    help's ``DeleteRemainder`` option is Split-only and does not state the
    point-mode behaviour; leaving the tail matches the reference CAD application's Split=No default.

    ``length`` > total arc length is refused (it would produce a single point
    at the start, which is not a useful divide).
    """
    _validate(points)
    try:
        length = float(length)
    except (TypeError, ValueError):
        raise ValueError("length must be a number (got %r)" % (length,)) from None
    if length <= 0:
        raise ValueError("length must be positive (got %r)" % (length,))
    cum = _cumulative_arc(points)
    total = cum[-1]
    if total < _TOL:
        raise ValueError("the curve has zero length")
    if length > total + _TOL:
        raise ValueError(
            "length (%g) is longer than the curve (%g)" % (length, total))
    result = []
    k = 0
    while k * length <= total + _TOL:
        result.append(_interpolate_arc(points, cum, k * length))
        k += 1
    return result


def _find_forward_chord(points, cum, anchor, anchor_s, chord):
    """The first curve point FORWARD of ``anchor_s`` whose straight-line
    distance from ``anchor`` is ``chord``, or (None, None) when the curve ends
    before reaching one.

    Walks the sampled polyline in forward order (arc position increasing) and
    interpolates between the two CONSECUTIVE samples that bracket the chord
    crossing. Because the two bracketing samples are consecutive, the chord
    between them is a short local approximation of the curve, so the linear
    interpolation is accurate — and on a straight line (collinear samples) it
    is EXACT, which is what makes ``by_chord`` agree with ``by_length`` there.
    """
    prev_pt = anchor
    prev_s = anchor_s
    prev_d = 0.0
    for i in range(len(points)):
        s = cum[i]
        if s < anchor_s - _TOL:
            continue                       # behind the anchor; not forward
        d = math.dist(points[i], anchor)
        if d >= chord - _TOL:
            span = d - prev_d
            if span < _TOL:
                return tuple(points[i]), s
            f = (chord - prev_d) / span
            a, b = prev_pt, points[i]
            dim = len(a)
            pt = tuple(a[k] + f * (b[k] - a[k]) for k in range(dim))
            arc = prev_s + f * (s - prev_s)
            return pt, arc
        prev_pt, prev_s, prev_d = points[i], s, d
    return None, None


def by_chord(points, chord):
    """A point every ``chord`` units of STRAIGHT-LINE distance apart.

    Each placed point is ``chord`` straight-line distance from the previous
    one, walking the curve in order. On a straight line this agrees EXACTLY
    with ``by_length`` (collinear ⇒ chord = arc); on a curved input the chord
    span is shorter than the arc span, so the positions DIFFER from
    ``by_length`` for the same numeric value — the whole reason the reference CAD application offers
    both modes.

    The curve's end stops the walk (any short tail shorter than ``chord`` is
    left unmarked, matching ``by_length``'s tail behaviour).
    """
    _validate(points)
    try:
        chord = float(chord)
    except (TypeError, ValueError):
        raise ValueError("chord must be a number (got %r)" % (chord,)) from None
    if chord <= 0:
        raise ValueError("chord must be positive (got %r)" % (chord,))
    cum = _cumulative_arc(points)
    total = cum[-1]
    if total < _TOL:
        raise ValueError("the curve has zero length")
    if chord > total + _TOL:
        # a chord longer than the whole curve leaves only the start point --
        # the same degenerate case by_length refuses, so refuse here too (a
        # one-point "divide" would look implemented when it is not).
        raise ValueError(
            "chord (%g) is longer than the curve (%g)" % (chord, total))
    result = [tuple(points[0])]
    anchor = tuple(points[0])
    anchor_s = 0.0
    while True:
        pt, arc = _find_forward_chord(points, cum, anchor, anchor_s, chord)
        if pt is None:
            break
        result.append(tuple(pt))
        anchor = tuple(pt)
        anchor_s = arc
        if len(result) > 100000:          # safety valve against a numerical loop
            break
    return result


# ---------------------------------------------------------------------------
# Curve-based siblings -- divide on EXACT arc length, not a chord polyline
#
# The three functions above take a sampled POLYLINE and so measure arc length by
# chords, which under-estimates a curved input; commands.py calls them that way
# and its signatures must not change. These siblings take the CURVE itself and
# use arclength.parameter_at_length, so a division lands on the true arc length --
# a circle split into 4 gives points exactly 90 degrees apart, where the polyline
# version is slightly off. (There is no by_chord sibling: an equal-CHORD division
# is a straight-line-distance walk, not an arc-length one, so arclength does not
# apply -- by_chord stays point-based.)
# ---------------------------------------------------------------------------

def by_count_on_curve(curve, n):
    """n+1 points along `curve`, evenly spaced by EXACT arc length.

    The curve-based sibling of `by_count`: `n` is the number of SEGMENTS, so the
    result has `n + 1` points including both ends (on a closed curve the last
    coincides with the first). `n < 1` is refused.
    """
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError("n must be an integer (got %r)" % (n,))
    if n < 1:
        raise ValueError("n must be >= 1 (got %r)" % (n,))
    al = _arclength()
    total = al.length_of(curve)
    if total < _TOL:
        raise ValueError("the curve has zero length")
    d = curve.Domain
    out = []
    for k in range(n + 1):
        s = total * k / n
        if k == 0:
            t = d.T0
        elif k == n:
            t = d.T1
        else:
            t = al.parameter_at_length(curve, s)
        p = curve.PointAt(t)
        out.append((p.X, p.Y, p.Z))
    return out


def by_length_on_curve(curve, length):
    """A point every `length` units of EXACT arc length from the curve's start.

    The curve-based sibling of `by_length`; same remainder/refusal rules. `length`
    longer than the curve, and non-positive, are refused.
    """
    try:
        length = float(length)
    except (TypeError, ValueError):
        raise ValueError("length must be a number (got %r)" % (length,)) from None
    if length <= 0:
        raise ValueError("length must be positive (got %r)" % (length,))
    al = _arclength()
    total = al.length_of(curve)
    if total < _TOL:
        raise ValueError("the curve has zero length")
    if length > total + _TOL:
        raise ValueError(
            "length (%g) is longer than the curve (%g)" % (length, total))
    d = curve.Domain
    out = []
    k = 0
    while k * length <= total + _TOL:
        s = min(k * length, total)
        if s <= _TOL:
            t = d.T0
        elif s >= total - _TOL:
            t = d.T1
        else:
            t = al.parameter_at_length(curve, s)
        p = curve.PointAt(t)
        out.append((p.X, p.Y, p.Z))
        k += 1
    return out


def _arclength():
    try:
        from . import arclength
    except ImportError:                   # direct file-path loading, without a package
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import arclength
    return arclength
