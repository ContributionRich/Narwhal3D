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
# Shift edge convenience must not steal group-member selection or other commands.
old_pick = ui.pick_subobject
try:
    _, solid = ctx.add_geometry(bridge.make_box((0, 0, 0), (3, 4, 0), 5), "Box")
    ui.apply_selection([solid], already_expanded=True)
    ui.pick_subobject = lambda *args: (solid, "Edge", 0)
    can_pick = lambda **kw: ui._shift_length_component_pick(None, None, None, 0, 0, **kw)
    assert can_pick(shift=True), "Shift picks an edge on a selected ungrouped solid"
    assert not can_pick() and not can_pick(shift=True, ctrl=True)
    ui.pick_subobject = lambda *args: (one, "Edge", 0)
    ui.apply_selection([one], already_expanded=True)
    assert not can_pick(shift=True), "group-member Shift selection has priority"
    ui.app_state.active_command = commands.LengthCmd()
    ui.apply_selection([], already_expanded=True)
    assert can_pick(shift=True), "Length may pick an edge before selecting its owner"
    ui.pick_subobject = lambda *args: (one, "Segment", 0)
    assert can_pick(shift=True), "Length also accepts an exact curve segment"
    ui.pick_subobject = lambda *args: (solid, "Face", 0)
    assert not can_pick(shift=True), "Length does not measure a face"
    ui.app_state.active_command = commands.LineCmd()
    ui.pick_subobject = lambda *args: (solid, "Edge", 0)
    assert not can_pick(shift=True), "do not steal input from unrelated commands"
finally:
    ui.pick_subobject = old_pick
    ui.app_state.active_command = None
print("PASS: grouped picks, Shift members and exact Length edge-pick convenience")
