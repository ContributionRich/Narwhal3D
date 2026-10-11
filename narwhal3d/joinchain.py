"""End-to-end chaining for the reference CAD application's `Join`. **No `bpy`, no rhino3dm** -- pure.

Why this module exists
----------------------
`Join` connects curves that touch end-to-end, producing polylines or polycurves in
selection order. Building the result is straightforward with rhino3dm's
`PolyCurve.Append`; the hard part is deciding **which** curves form a chain, in **what
order**, and which must be **reversed** first. That decision is pure endpoint arithmetic,
so it lives here without a Blender or kernel dependency.

Why chaining is resolved before appending
-----------------------------------------
* ``PolyCurve.Append(far_away_curve)`` can return **True** while ``HasGap`` remains
  **False** and ``IsValid`` remains **True**; it does not validate endpoint proximity.
* ``PolyCurve.Append(backwards_curve)`` does not reverse the curve to make the joint meet,
  and its status flags do not provide a reliable gap test.
* ``Curve.Reverse()`` flips a curve in place once the required direction is known.

Append order and reversal must therefore be resolved before constructing the polycurve, or
`Join` can silently produce disconnected geometry.

Tolerance
---------
the reference CAD application joins curves whose ends *touch*, judged against the document's **absolute
tolerance** (``units.default_tolerances()``); the help's note about "within 2x
tolerance" is about surface edges, not curve ends. Callers pass the tolerance in;
this module never assumes one.

Ambiguous junctions
-------------------
At a **T-junction**, three or more curve ends meet and more than one continuation is valid.
The algorithm takes the **first candidate in the caller's order**, preserving deterministic
selection-order behavior. See :data:`TIE_BREAK_IS_SELECTION_ORDER`.
"""
from __future__ import annotations

import math

#: At an ambiguous junction, choose the earliest continuation in caller-supplied order.
TIE_BREAK_IS_SELECTION_ORDER = True


class Chain:
    """One joinable run of segments, in append order.

    `members` is a tuple of ``(key, reverse)`` pairs: `key` is whatever the caller
    used to identify the segment, and `reverse` is True when that segment has to
    be flipped (``Curve.Reverse()``) before being appended so the joint meets.
    """

    __slots__ = ("members", "closed", "start", "end")

    def __init__(self, members, closed, start, end):
        self.members = tuple(members)
        self.closed = bool(closed)
        self.start = tuple(start)
        self.end = tuple(end)

    @property
    def keys(self):
        """Just the keys, in append order."""
        return tuple(k for k, _ in self.members)

    def __len__(self):
        return len(self.members)

    def __repr__(self):  # pragma: no cover - debugging aid
        shape = "closed" if self.closed else "open"
        parts = ", ".join(f"{k}{'~' if r else ''}" for k, r in self.members)
        return f"Chain({parts}) {shape}"


def _near(a, b, tol):
    return math.dist(a, b) <= tol


def _ends(seg, reverse):
    """(start, end) of `seg` as it will be appended."""
    _, s, e = seg
    return (e, s) if reverse else (s, e)


def chains(segments, tolerance):
    """Group `segments` into end-to-end :class:`Chain` runs.

    `segments` is an iterable of ``(key, start, end)`` where `start`/`end` are
    3-tuples. `tolerance` is the largest gap still counted as touching.

    Every input segment appears in exactly one returned chain, so a curve that
    joins to nothing comes back as a chain of length 1 -- the caller decides
    whether that is worth doing anything about. Chains come back in the order
    their first segment appears in `segments`, which keeps `Join`'s "the order
    the objects are selected determines the layer" rule easy to honour.
    """
    segs = [(k, tuple(s), tuple(e)) for k, s, e in segments]
    tol = float(tolerance)
    used = [False] * len(segs)
    out = []

    for seed in range(len(segs)):
        if used[seed]:
            continue
        used[seed] = True
        members = [(segs[seed][0], False)]
        head, tail = _ends(segs[seed], False)

        # A seed that is ALREADY closed (a circle) is a chain on its own. Growing
        # it would be wrong: any continuation starts at the same point the loop
        # ends at, so we would splice a curve into a closed ring. `joinable` then
        # drops it, because joining one curve to nothing is not a join.
        if not _near(head, tail, tol):
            # grow off the END, appending, until the run stops or closes
            while not _near(tail, head, tol):
                nxt = _find(segs, used, tail, tol)
                if nxt is None:
                    break
                i, reverse = nxt
                used[i] = True
                members.append((segs[i][0], reverse))
                _, tail = _ends(segs[i], reverse)

            # grow off the START, prepending -- a seed picked from the MIDDLE of a
            # run must still produce the whole run. Skipped once the run has
            # closed, or we would wrap back round through it.
            while not _near(tail, head, tol):
                prv = _find_before(segs, used, head, tol)
                if prv is None:
                    break
                i, reverse = prv
                used[i] = True
                members.insert(0, (segs[i][0], reverse))
                head, _ = _ends(segs[i], reverse)

        out.append(Chain(members, _near(head, tail, tol), head, tail))
    return out


def _find(segs, used, at, tol):
    """An unused segment with an end at `at`, and whether it needs reversing.

    Prefers the orientation that needs NO reversal, so a run already drawn in
    order keeps its direction. Returns ``(index, reverse)`` or None.
    """
    for i, (_, s, e) in enumerate(segs):
        if used[i]:
            continue
        if _near(s, at, tol):
            return i, False
    for i, (_, s, e) in enumerate(segs):
        if used[i]:
            continue
        if _near(e, at, tol):
            return i, True
    return None


def _find_before(segs, used, at, tol):
    """An unused segment ENDING at `at` (so it can be prepended)."""
    for i, (_, s, e) in enumerate(segs):
        if used[i]:
            continue
        if _near(e, at, tol):
            return i, False
    for i, (_, s, e) in enumerate(segs):
        if used[i]:
            continue
        if _near(s, at, tol):
            return i, True
    return None


def joinable(segments, tolerance):
    """The chains from :func:`chains` that actually join something (len >= 2).

    A closed single curve is NOT joinable because there is nothing to join it to.
    """
    return [c for c in chains(segments, tolerance) if len(c) >= 2]
