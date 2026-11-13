"""Native-only Join-chain solver. No Python numeric fallback.

Replaces the retired Python Θ(N²) solver in ``narwhal3d/joinchain.py`` with the
exact indexed Rust solver (Q117-06, ``native/pure_core/src/joinchain.rs``).
Every valid non-empty input runs native at all sizes. Empty input returns ``[]``
after verifying the binding exists (to detect installation problems early).
Missing, stale, ``None``, or malformed native output raises ``RuntimeError``
-- there is no Python replay.

Public API (unchanged from the retired module):
  * ``TIE_BREAK_IS_SELECTION_ORDER``
  * ``Chain`` (class)
  * ``chains(segments, tolerance, _force_native=False)``
  * ``joinable(segments, tolerance)``

MEASURED GAP
------------
The old Python solver silently accepted non-finite or negative ``tolerance``
(``math.dist`` with NaN returns NaN; ``NaN <= tol`` is False, so nothing joins;
a negative tolerance makes ``dist <= tol`` always False). The Rust solver
rejects both with ``ERR_INPUT``. This candidate inherits the Rust rejection
and raises ``RuntimeError``. That is a deliberate behavior change, not a
regression: an invalid tolerance should be loud, not silently ignored.

NUMERIC BOUNDARY
----------------
The native solver uses the CPython-compensated norm, not sequential hypot.
Exact and adjacent tolerance decisions are verified on long, non-closed arms;
no tolerance is widened and no disconnected curve is silently joined.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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


def _resolve_adapter():
    if __package__:
        from . import native_pure
        return native_pure
    name = "narwhal3d_native_pure"
    if name not in sys.modules:
        path = Path(__file__).with_name("native_pure.py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


def _native_joinchain():
    """Compatibility resolver for direct native ABI tests, never a solver."""
    return _resolve_adapter()


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
    """Group `segments` into end-to-end :class:`Chain` runs. **Native-only.**

    `segments` is an iterable of ``(key, start, end)`` where `start`/`end` are
    3-tuples of numbers. `tolerance` is the largest gap still counted as
    touching. `_force_native` is accepted for API compatibility but has no
    effect: every valid non-empty input runs native at all sizes.

    Every input segment appears in exactly one returned chain, so a curve that
    joins to nothing comes back as a chain of length 1 -- the caller decides
    whether that is worth doing anything about. Chains come back in the order
    their first segment appears in `segments`, which keeps `Join`'s "the order
    the objects are selected determines the layer" rule easy to honour.

    Raises
    ------
    RuntimeError
        If the native binding is unavailable, the input is rejected by the
        Rust solver (non-finite coordinates, negative or non-finite tolerance,
        or a coordinate too large for the index), or the packed output fails
        the strict validator. There is no Python fallback.
    """
    # --- Validate input shape (polite, with clear messages) ---
    segs = []
    for i, seg in enumerate(segments):
        try:
            key, start, end = seg
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"segment {i}: expected a (key, start, end) 3-tuple, "
                f"got {seg!r}") from exc
        try:
            s = tuple(start)
            e = tuple(end)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"segment {i}: start/end must be 3-tuples, "
                f"got start={start!r} end={end!r}") from exc
        if len(s) != 3:
            raise RuntimeError(
                f"segment {i}: start must be a 3-tuple, got {start!r} "
                f"({len(s)} elements)")
        if len(e) != 3:
            raise RuntimeError(
                f"segment {i}: end must be a 3-tuple, got {end!r} "
                f"({len(e)} elements)")
        for j, c in enumerate(s):
            if not isinstance(c, (int, float)):
                raise RuntimeError(
                    f"segment {i}: start[{j}] must be a number, got {c!r}")
        for j, c in enumerate(e):
            if not isinstance(c, (int, float)):
                raise RuntimeError(
                    f"segment {i}: end[{j}] must be a number, got {c!r}")
        segs.append((key, s, e))

    # --- Resolve adapter and verify binding (detect installation problems) ---
    adapter = _resolve_adapter()
    binding = adapter._joinchain_binding()
    if binding is None:
        raise RuntimeError(
            "joinchain native binding unavailable: the native library is "
            "missing, stale (ABI mismatch), or does not export "
            "narwhal_pure_core_joinchain. No Python fallback is available "
            "in native-only mode.")

    n = len(segs)
    if n == 0:
        # Empty input: binding is verified, no computation needed.
        return []

    # --- Pack and call native ---
    try:
        tol = float(tolerance)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"tolerance must be a number, got {tolerance!r}") from exc

    starts = [c for _k, s, e in segs for c in s]
    ends = [c for _k, s, e in segs for c in e]

    packed = adapter._native_joinchain(starts, ends, tol)
    if packed is None:
        raise RuntimeError(
            "native joinchain solver rejected the input or produced a "
            "malformed result. The Rust solver rejects: non-finite "
            "coordinates, negative or non-finite tolerance, and coordinates "
            "too large for the spatial index. "
            f"Input: {n} segments, tolerance={tol!r}. "
            "No Python fallback is available in native-only mode.")

    return _chains_from_packed(segs, packed)


def joinable(segments, tolerance):
    """The chains from :func:`chains` that actually join something (len >= 2).

    A closed single curve is NOT joinable because there is nothing to join it to.
    """
    return [c for c in chains(segments, tolerance) if len(c) >= 2]
