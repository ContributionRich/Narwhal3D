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


def _native_joinchain():
    """Lazily resolve the native pure-core adapter, or ``None``.

    Identical to the ``svgpath._native_svgpath()`` / ``macro._native_macro()``
    accessors: returns the ``native_pure`` module when it can be imported
    (production, where this file is ``narwhal3d.joinchain``) or loaded from this
    directory (spike tests load this module by file path, so ``narwhal3d`` is
    not an importable package there). ``None`` means "use the unchanged Python
    solver" -- the native library is absent, it predates the joinchain export,
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


def _chains_from_packed(segs, packed):
    """Convert the native packed Join-chain output into the Python ``Chain`` contract.

    ``packed`` is ``(n, chain_off, chain_len, chain_closed, chain_ends,
    member_seg, member_rev, n_chains, n_members)``. The native solver reports
    members as ``(segment_index, reverse)`` in append order and chains in the
    same "first-segment-appearance" order the Python reference emits; here the
    segment index is mapped back to the caller's own key, so the result is the
    exact ``Chain`` list the unchanged Python solver would have built.
    ``chain_ends`` is the flat ``[sx,sy,sz,ex,ey,ez]`` per chain.
    """
    (n, chain_off, chain_len, chain_closed, chain_ends,
     member_seg, member_rev, n_chains, n_members) = packed
    sx, sy, sz, ex, ey, ez = chain_ends
    out = []
    for k in range(n_chains):
        off = chain_off[k]
        ln = chain_len[k]
        members = [(segs[member_seg[off + j]][0], bool(member_rev[off + j]))
                   for j in range(ln)]
        out.append(Chain(
            members,
            bool(chain_closed[k]),
            (sx[k], sy[k], sz[k]),
            (ex[k], ey[k], ez[k]),
        ))
    return out


def chains(segments, tolerance, _force_native=False):
    """Group `segments` into end-to-end :class:`Chain` runs.

    `segments` is an iterable of ``(key, start, end)`` where `start`/`end` are
    3-tuples. `tolerance` is the largest gap still counted as touching.

    Every input segment appears in exactly one returned chain, so a curve that
    joins to nothing comes back as a chain of length 1 -- the caller decides
    whether that is worth doing anything about. Chains come back in the order
    their first segment appears in `segments`, which keeps `Join`'s "the order
    the objects are selected determines the layer" rule easy to honour.

    At or above the measured crossover (and for ``_force_native=True``, tests
    only) the indexed native solver (Q117-06) runs instead -- it is >=10% faster
    end-to-end there and a successful result is the exact Python contract. Below
    the crossover, for a rejected input, or an unavailable library the unchanged
    Python solver below runs in full.
    """
    segs = [(k, tuple(s), tuple(e)) for k, s, e in segments]
    tol = float(tolerance)
    _np = _native_joinchain() if (segs or _force_native) else None
    if _np is not None and _np._joinchain_native_active(len(segs), force=_force_native):
        starts = [c for _k, s, e in segs for c in s]
        ends = [c for _k, s, e in segs for c in e]
        packed = _np._native_joinchain(starts, ends, tol)
        if packed is not None:
            return _chains_from_packed(segs, packed)
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
