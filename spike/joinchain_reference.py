"""Test-only reference oracle for the retired Python Join-chain solver.

This is a faithful, self-contained copy of the ``chains`` / ``joinable``
behaviour from the retired ``narwhal3d/joinchain.py`` (archived at
``legacy/retired-rust13/joinchain.py``). It is INDEPENDENT: it does not import
the production module, does not import ``native_pure`` at all, and forces the
native accessor to ``None`` by simply never calling it. It exists only so
``spike/test_joinchain_owned.py`` can compare the native-only candidate against
the exact contract the old solver implemented.

It is NOT a runtime import: it is a test oracle. The retired solver's numeric
helpers are reproduced here so the oracle is byte-for-byte the same decision
procedure: ``math.dist(a, b) <= tol`` (Python's compensated hypot, which can
differ from Rust ``dx.hypot(dy).hypot(dz)`` by up to 2 ULPs at the boundary).
"""
from __future__ import annotations

import math

#: At an ambiguous junction, choose the earliest continuation in caller-supplied order.
TIE_BREAK_IS_SELECTION_ORDER = True


class Chain:
    """One joinable run of segments, in append order."""

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


def chains(segments, tolerance, _force_native=False):
    """Group `segments` into end-to-end :class:`Chain` runs.

    The unchanged reference decision procedure (the retired Python solver).
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

            # grow off the START, prepending
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


def joinable(segments, tolerance):
    """The chains from :func:`chains` that actually join something (len >= 2)."""
    return [c for c in chains(segments, tolerance) if len(c) >= 2]
