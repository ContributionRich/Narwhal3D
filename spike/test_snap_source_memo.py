"""The snap index reuses each analytic master's snap sources across edits -- and never stale.

Run:
  tools/blender-5.2.2-linux-x64/blender -b --factory-startup --python spike/test_snap_source_memo.py

Reported usage-monitor finding (2026-11-08): ~0.4-0.7 s UI stalls right after Delete, Move,
Polyline and Boolean. MEASURED on a private copy of 0130.3dm: every edit that moved
`_doc_key` made `_snap_build_step` re-derive `bridge.object_snap_sources` for EVERY object,
masters included (about 240 ms in total, and one whole master per slice because a slice
never splits an object). The fix keys the sources by the master id, which names one
immutable shape, so an edit to other objects re-derives nothing.

What this suite checks, each against a from-scratch build:
  * after an edit to an UNRELATED object, the build derives no master's sources;
  * the index equals a from-scratch index after edits, a transform and a replace;
  * a transformed master gets a fresh id, so its old sources are never served again;
  * the memo holds only the masters of the current document (bounded).
"""
import math
import os
import sys
import time
import traceback

import bpy
import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from narwhal3d import bridge, cplane, kernel, ui  # noqa: E402
from narwhal3d.kernel import occt_backend  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402
from narwhal3d.state import app_state  # noqa: E402


def analytic(geom):
    """Bind an exact OCCT master to `geom`, as the Box and Cylinder producers do."""
    kernel.register_master(geom, occt_backend.from_recipe(kernel.recipe_of(geom)))
    return geom

results = []

#: Reuse must cost well under a full rebuild. Generous, because the box is small.
REUSE_BUDGET_RATIO = 0.5
REUSE_BUDGET_SLACK_MS = 2.0


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))


def entries_flat():
    return [(e[0], bool(e[6]), _flat(e[2])) for e in ui._snap_index["entries"]]


def _flat(extent):
    if extent is None:
        return None
    lo, hi = extent
    return tuple(round(float(v), 9) for v in (*lo, *hi))


def same_entries(a, b):
    if len(a) != len(b):
        return False
    for (na, ea, xa), (nb, eb, xb) in zip(a, b):
        if na != nb or ea != eb:
            return False
        if (xa is None) != (xb is None):
            return False
        if xa is not None and any(abs(p - q) > 1e-7 for p, q in zip(xa, xb)):
            return False
    return True


def target_key(targets):
    return tuple((t.name, repr(getattr(t, 'vertices', ())), repr(getattr(t, 'points', ())))
                  for t in targets)


class Counting:
    """Records whether each `bridge.object_snap_sources` call was for an analytic master."""

    def __init__(self):
        self.calls = []
        self._original = bridge.object_snap_sources

    def __enter__(self):
        original = self._original

        def counted(geom):
            self.calls.append(kernel.master_of(geom) is not None)
            return original(geom)
        bridge.object_snap_sources = counted
        return self

    def __exit__(self, *exc):
        bridge.object_snap_sources = self._original

    @property
    def masters(self):
        return sum(1 for is_master in self.calls if is_master)


def rebuild_from_scratch():
    """A from-scratch index: forget the index AND the memo, then build it."""
    ui._snap_index.update(key=None, entries=None, stale=set())
    ui._snap_build["key"] = None
    ui._snap_sources_memo.clear()
    ui._snap_build_step(None)
    return entries_flat()


def timed_build():
    ui._snap_index.update(key=None, entries=None, stale=set())
    ui._snap_build["key"] = None
    started = time.perf_counter()
    ui._snap_build_step(None)
    return (time.perf_counter() - started) * 1e3


def object_by_name(name):
    for fobj in app_state.model.file.Objects:
        if (fobj.Attributes.Name or "") == name:
            return fobj
    return None


try:
    app_state.set_model(NarwhalModel())
    app_state.active_command = None
    ctx = app_state.ctx

    def add(geom, base):
        ctx.begin_undo(f"add {base}")
        _id, name = ctx.add_geometry(geom, base)
        ctx.end_undo()
        return name

    B1 = add(analytic(bridge.make_box((0, 0, 0), (4, 4, 0), 3.0, cplane.WORLD_TOP)), "Box")
    B2 = add(analytic(bridge.make_box((10, 0, 0), (16, 5, 0), 2.0, cplane.WORLD_TOP)), "Box")
    CY = add(analytic(bridge.make_cylinder((0, 12, 0), 2.0, 4.0, cplane.WORLD_TOP)), "Cylinder")
    L1 = add(bridge.make_line((20, 0, 0), (25, 3, 0)), "Line")
    masters_before = {n for n in (B1, B2, CY) if kernel.master_of(object_by_name(n).Geometry)}
    check(len(masters_before) == 3, "the three primitives are analytic masters",
          repr(sorted(masters_before)))

    # ---- cold build: each master derived once, the memo holds exactly those masters
    with Counting() as cold:
        t_full = timed_build()
    check(cold.masters == 3, "cold build derives each master once", repr(cold.calls))
    memo_keys = set(ui._snap_sources_memo)
    check(len(memo_keys) == 3, "memo holds one entry per live master", repr(len(memo_keys)))
    reference = entries_flat()
    check(len(reference) > 0, "the index is not vacuous", repr(len(reference)))

    # ---- an edit to an UNRELATED object: no master is re-derived
    add(bridge.make_line((30, 0, 0), (31, 1, 0)), "Line")
    with Counting() as unrelated:
        t_reuse = timed_build()
    reused = entries_flat()
    check(unrelated.masters == 0,
          "edit to an unrelated object re-derives NO master", repr(unrelated.calls))
    check(t_reuse <= REUSE_BUDGET_RATIO * t_full + REUSE_BUDGET_SLACK_MS,
          "reuse build within budget",
          f"full {t_full:.2f} ms, after unrelated edit {t_reuse:.2f} ms")
    exact = rebuild_from_scratch()
    check(same_entries(reused, exact),
          "reused index equals a from-scratch index after an unrelated edit",
          f"{len(reused)} reused vs {len(exact)} from scratch")

    # ---- snap targets follow their source: an unrelated edit re-samples no master's targets
    for entry in ui._snap_index["entries"]:
        ui._entry_targets(entry)
    sampled = [(e[0], target_key(e[3])) for e in ui._snap_index["entries"]]
    add(bridge.make_line((40, 0, 0), (41, 1, 0)), "Line")
    sampled_again = []
    original_targets = bridge.snap_source_targets

    def counted_targets(source, is_edge, name=""):
        sampled_again.append(name)
        return original_targets(source, is_edge, name=name)
    bridge.snap_source_targets = counted_targets
    try:
        timed_build()
        for entry in ui._snap_index["entries"]:
            ui._entry_targets(entry)
    finally:
        bridge.snap_source_targets = original_targets
    masters = {B1, B2, CY}
    check(not (masters & set(sampled_again)),
          "an unrelated edit re-samples no master's snap targets", repr(sorted(sampled_again)))
    now = [(e[0], target_key(e[3])) for e in ui._snap_index["entries"]]
    check(now[:len(sampled)] == sampled,
          "reused snap targets equal the targets sampled before the edit", "")

    # ---- a plain curve moved IN PLACE (as a transform does) gets fresh targets, never old ones
    slide = add(bridge.make_line((60, 0, 0), (61, 0, 0)), "Line")
    timed_build()
    for entry in ui._snap_index["entries"]:
        ui._entry_targets(entry)
    slide_before = [target_key(e[3]) for e in ui._snap_index["entries"] if e[0] == slide]
    object_by_name(slide).Geometry.Transform(r3.Transform.Translation(7.0, 0.0, 0.0))
    timed_build()
    for entry in ui._snap_index["entries"]:
        ui._entry_targets(entry)
    slide_after = [target_key(e[3]) for e in ui._snap_index["entries"] if e[0] == slide]
    check(bool(slide_before) and slide_after != slide_before,
          "an in-place moved curve has new snap targets after the rebuild",
          f"{slide_before!r} -> {slide_after!r}")
    fresh_slide = rebuild_from_scratch()
    ui._snap_build_step(None)
    fresh_targets = [target_key(ui._entry_targets(e)) for e in ui._snap_index["entries"]
                     if e[0] == slide]
    check(slide_after == fresh_targets,
          "in-place moved curve's targets equal a from-scratch sampling", "")

    # ---- a transform: the moved master gets a fresh id and fresh sources, exactly
    before_transform = entries_flat()
    old_key = kernel.master_key_of(object_by_name(B1).Geometry)
    ctx.begin_undo("Gumball Move")
    fobj = object_by_name(B1)
    moved = bridge.transform_geometry(fobj.Geometry, r3.Transform.Translation(7.0, 0.0, 0.0))
    app_state.model.replace_geometry(fobj.Attributes.Id, moved)
    ctx.end_undo()
    new_key = kernel.master_key_of(object_by_name(B1).Geometry)
    check(new_key is not None and new_key != old_key, "a moved master registers a fresh id",
          f"{old_key!r} -> {new_key!r}")
    with Counting() as moved_counts:
        ui._snap_index.update(key=None, entries=None, stale=set())
        ui._snap_build["key"] = None
        ui._snap_build_step(None)
    check(moved_counts.masters == 1, "after the transform only the moved master is derived",
          repr(moved_counts.calls))
    check(old_key not in ui._snap_sources_memo, "the old master id is dropped from the memo",
          repr(sorted(ui._snap_sources_memo)))
    check(len(ui._snap_sources_memo) == 3, "memo bounded to the live masters",
          repr(len(ui._snap_sources_memo)))
    moved_index = entries_flat()
    fresh = rebuild_from_scratch()
    check(same_entries(moved_index, fresh),
          "index after the transform equals a from-scratch index (no stale extents)", "")
    check(not same_entries(fresh, before_transform),
          "the transform changed the index (the test can see staleness)", "")

    print(f"[snap-memo] full build {t_full:.2f} ms; build after unrelated edit "
          f"{t_reuse:.2f} ms; masters derived: cold {cold.masters}, unrelated "
          f"{unrelated.masters}, moved {moved_counts.masters}", flush=True)
except BaseException:
    results.append((False, "suite crashed", traceback.format_exc()))

failed = [r for r in results if not r[0]]
for ok, label, detail in results:
    print(("PASS " if ok else "FAIL ") + label + (("  -- " + detail) if (detail and not ok) else ""))
print(f"snap_source_memo: {len(results) - len(failed)}/{len(results)} passed")
if failed:
    sys.exit(1)
