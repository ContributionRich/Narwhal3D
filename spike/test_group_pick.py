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
    assert ui._open_selection_choices(None, None, None, 0, 0, "object"), "single group consumes the filtered click"
    assert ui.selection_menu.current() is None, "one group opens no chooser"
    assert set(ui.selection_names()) == {one, two}, "filtered single candidate selects the whole group"
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
# Command-phase routing is a UI contract. Real MoveEdge transitions are guarded
# independently by the unchanged test_edge_pick_gui.py; these spies also pin
# modifiers and ensure an empty/occluded result never reaches a legacy picker.
def edge_owner_routing():
    cmd = SimpleNamespace(steps=[('Select edges:', commands.OBJECTS),
                                 ('Select an edge:', commands.POINT)],
                          step_index=0, pending_option=None)
    original = {name: getattr(ui, name) for name in
                ('_select_object_hit', '_answer_objects_step_with_click',
                 '_answer_edge_prompt_with_click', '_pick_object_at', 'pick_point_object')}
    original_at, original_cmd = pick_candidates.at, ui.app_state.active_command
    queried, calls = [], []
    edge = pick_candidates.Candidate((solid, 'Edge', 0), 'edge')
    edge_hits, object_hits = [], [row(solid)]

    def at(*args):
        mode = args[-1]
        queried.append(mode)
        return edge_hits if mode == 'edge' else object_hits

    def forbidden(*args, **kwargs):
        raise AssertionError('filtered command clicks must not use a legacy picker')

    def select_hit(hit, **kwargs):
        calls.append(('object', hit, kwargs['shift'], kwargs['ctrl']))

    def answer_edge(*args, **kwargs):
        calls.append(('edge', kwargs['hit'], kwargs['shift'], kwargs['ctrl']))
        return True

    def click(**flags):
        queried.clear()
        calls.clear()
        assert ui._open_selection_choices(None, None, None, 0, 0, 'edge', **flags)

    try:
        pick_candidates.at = at
        ui.app_state.active_command = cmd
        ui._select_object_hit = select_hit
        ui._answer_objects_step_with_click = lambda *args: calls.append(('answer-owner',))
        ui._answer_edge_prompt_with_click = answer_edge
        ui._pick_object_at = ui.pick_point_object = forbidden
        click()
        assert queried == ['edge', 'object'] and calls == [
            ('object', solid, False, False), ('answer-owner',)], 'face click must answer the owner OBJECTS prompt'
        edge_hits = [edge]
        click()
        assert queried == ['edge'] and calls == [
            ('edge', edge.key, False, False)], 'plain edge click retains one-click owner+edge route'
        for shift, ctrl in ((True, False), (False, True)):
            click(shift=shift, ctrl=ctrl)
            assert queried == ['edge', 'object'] and calls == [
                ('object', solid, shift, ctrl)], 'modified owner click selects but never answers the command'
        edge_hits, object_hits = [], []
        click()
        assert queried == ['edge', 'object'] and calls == [
            ('object', None, False, False)], 'occluded/empty owner is never invented or fed to command'
        cmd.step_index = 1
        click()
        assert queried == ['edge'] and not calls, 'later POINT phase must not become an owner pick'
        cmd.step_index, cmd.pending_option = 0, 'DirectionConstraint'
        click()
        assert queried == ['edge'] and not calls, 'pending option must not become an owner pick'
        cmd.pending_option = None
        cmd.steps[0] = ('Select edges:', commands.OBJECTS_MULTI)
        click()
        assert queried == ['edge'] and not calls, 'multi-edge command keeps its existing capability route'
    finally:
        for name, function in original.items():
            setattr(ui, name, function)
        pick_candidates.at, ui.app_state.active_command = original_at, original_cmd


edge_owner_routing()
import inspect
source = inspect.getsource(ui._open_selection_choices)
anchor = 'if mode == "edge" and (not candidates or shift or ctrl):'
assert source.count(anchor) == 1
original_choices = ui._open_selection_choices
try:
    exec(compile(source.replace(anchor, 'if False:', 1), ui.__file__, 'exec'), ui.__dict__)
    try:
        edge_owner_routing()
    except AssertionError as error:
        assert str(error) == 'face click must answer the owner OBJECTS prompt', error
        print('RED verified: removing filtered edge-command owner routing')
    else:
        raise AssertionError('owner-routing mutation survived')
finally:
    ui._open_selection_choices = original_choices
edge_owner_routing()
print("PASS: grouped picks, Shift members, exact Length convenience and filtered edge-command owner routing")
