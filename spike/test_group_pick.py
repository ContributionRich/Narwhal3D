"""Groups are one ordinary overlap-pick candidate; Shift can pick individual members.

Reference help treats groups as selection units and Ctrl+Shift as member picking.
OURS: Shift-click also picks members, as explicitly requested for this application.
"""
import os
import sys
from types import SimpleNamespace

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from narwhal3d import bridge, commands, pick_candidates, ui  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
ui.app_state.set_model(NarwhalModel())
ctx = ui.app_state.ctx
_, one = ctx.add_geometry(bridge.make_line((0, 0, 0), (2, 0, 0)), "Line")
_, two = ctx.add_geometry(bridge.make_line((0, 0, 0), (0, 2, 0)), "Line")
_, other = ctx.add_geometry(bridge.make_line((0, 0, 0), (2, 2, 0)), "Line")
ctx.select_by_names([one, two])
message, done = commands.GroupCmd().begin(ctx)
assert done and set(ctx.expand_group_names([one])) == {one, two}, message


def row(name):
    return pick_candidates.Candidate((name, "Object", 0), name, lines=(((0, 0, 0), (1, 1, 0)),))


members = [row(one), row(two)]
collapsed = ui.grouped_pick_candidates(members)
assert len(collapsed) == 1 and "Group" in collapsed[0].label
assert len(collapsed[0].lines) == 2, "group hover retains both hit members"
assert len(ui.grouped_pick_candidates(members + [row(other)])) == 2, "unrelated overlap stays ambiguous"
old_at = pick_candidates.at
try:
    pick_candidates.at = lambda *args: members
    assert not ui._open_selection_choices(None, None, None, 0, 0, "object"), "one group opens no chooser"
finally:
    pick_candidates.at = old_at

ui.apply_selection([], already_expanded=True)
ui._select_object_hit(one)
assert set(ui.selection_names()) == {one, two}, "plain click selects the group"
ui._select_object_hit(one, ctrl=True)
assert ui.selection_names() == [], "Ctrl removes the whole group"
ui._select_object_hit(one, shift=True)
assert ui.selection_names() == [one], "Shift selects only the clicked group member"
ui._select_object_hit(two, shift=True)
assert set(ui.selection_names()) == {one, two}, "Shift can add another member"
assert ctx.expand_group_names([one]) == [one, two], "picking members never dissolves the group"
print("PASS: grouped overlap picks, unrelated ambiguity, plain/Ctrl group selection and Shift member selection")
