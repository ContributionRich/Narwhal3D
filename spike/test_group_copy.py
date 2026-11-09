"""Copies of grouped objects form NEW groups -- Gumball Alt-copy and the Copy command.

MEASURED (the reference CAD application's Copy help page): "If the selected objects are in a group,
a new group is created from the copied objects. The copied objects are not added to the
original group."

Bug report: "Alt-dragging a group has the copy no longer grouped." Both paths went
through `_duplicate_with_transform`, whose fresh attributes dropped every membership,
and nothing re-grouped the copies. `commands._group_copies` is now shared by both.

The fixture holds the inputs that can fail: a 2-member group, a SECOND group (so "one
new group for everything" fails), and an ungrouped object (so "group every copy" fails).
The suite then re-runs itself with `_group_copies` disabled and requires RED.
"""
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from narwhal3d import bridge, commands, gumball, ui  # noqa: E402
from narwhal3d.model import NarwhalModel  # noqa: E402

SHIFT = 5.0
# (base name, start point); the start x/y is unique, so a copy is matched to its
# original by where it lands.
LAYOUT = {"a1": (0.0, 0.0), "a2": (0.0, 3.0), "b1": (10.0, 0.0),
          "b2": (10.0, 3.0), "loose": (20.0, 0.0)}


def fresh():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    ui.app_state.set_model(NarwhalModel())
    ctx = ui.app_state.ctx
    names = {}
    for key, (x, y) in LAYOUT.items():
        _, names[key] = ctx.add_geometry(
            bridge.make_line((x, y, 0.0), (x + 1.0, y + 1.0, 0.0)), "Line")
    for pair in (("a1", "a2"), ("b1", "b2")):
        ctx.select_by_names([names[k] for k in pair], already_expanded=True)
        message, done = commands.GroupCmd().begin(ctx)
        assert done and "Group" in message, message
    ctx.select_by_names([], already_expanded=True)
    ctx.undo.clear()
    return ctx, names


def doc_names(ctx):
    return {f.Attributes.Name for f in ctx.model.file.Objects}


def groups_of(ctx, name):
    for f in ctx.model.file.Objects:
        if f.Attributes.Name == name:
            return frozenset(int(g) for g in (f.Attributes.GetGroupList2() or []))
    raise AssertionError(f"{name} is not in the document")


def start_xy(ctx, name):
    bb = ctx.geom_by_name(name).GetBoundingBox()
    return round(bb.Min.X, 6), round(bb.Min.Y, 6)


def check_copy_set(ctx, names, original_groups, copies, dx, label):
    """`copies` are one copy set, offset by `dx` from the originals' start."""
    by_key = {}
    for c in copies:
        x, y = start_xy(ctx, c)
        key = next(k for k, (kx, ky) in LAYOUT.items()
                   if abs(kx + dx - x) < 1e-6 and abs(ky - y) < 1e-6)
        by_key[key] = c
    assert set(by_key) == set(LAYOUT), f"{label}: one copy per original {by_key}"
    old = set().union(*original_groups.values())
    for key in LAYOUT:
        assert groups_of(ctx, names[key]) == original_groups[key], \
            f"{label}: original {key} keeps exactly its own group"
    ga, gb = (groups_of(ctx, by_key["a1"]), groups_of(ctx, by_key["b1"]))
    assert len(ga) == 1 and groups_of(ctx, by_key["a2"]) == ga, \
        f"{label}: the a-copies share ONE group ({ga})"
    assert not ga & old, f"{label}: the a-copies' group is NEW, not the original"
    assert len(gb) == 1 and groups_of(ctx, by_key["b2"]) == gb, \
        f"{label}: the b-copies share ONE group"
    assert not gb & old and ga != gb, f"{label}: two groups copy to two NEW groups"
    assert groups_of(ctx, by_key["loose"]) == frozenset(), \
        f"{label}: an ungrouped original gives an ungrouped copy"
    assert set(ctx.expand_group_names([by_key["a1"]])) == {by_key["a1"], by_key["a2"]}, \
        f"{label}: selecting one copy selects its copied group, not the original"
    return ga | gb


def gumball_alt_copy():
    ctx, names = fresh()
    ctx.select_by_names([names["a1"], names["b1"], names["loose"]])
    assert set(ui.selection_names()) == set(names.values()), "clicking groups selects members"
    original = {k: groups_of(ctx, n) for k, n in names.items()}
    groups_before = len(ctx.model.file.Groups)
    before = doc_names(ctx)

    st = {"mode": gumball.ORIGIN, "moved_total": (SHIFT, 0.0, 0.0)}
    old_drag = ui._gumball_drag
    ui._gumball_drag = st
    ctx.begin_undo("Gumball Move")
    try:
        ui._gumball_defer(st, bridge.translation_xform((SHIFT, 0.0, 0.0)))
        message = ui._gumball_toggle_copy()
    finally:
        ctx.end_undo()
        ui._gumball_drag = old_drag
    assert message and "left 5 copy(ies)" in message, message
    copies = doc_names(ctx) - before
    new = check_copy_set(ctx, names, original, copies, 0.0, "gumball")
    assert len(ctx.model.file.Groups) == groups_before + 2, "exactly two new groups"
    for key, (x, y) in LAYOUT.items():
        assert start_xy(ctx, names[key]) == (x + SHIFT, y), "originals carried by the drag"

    assert ctx.undo.undo_names() == ["Gumball Move"], ctx.undo.undo_names()
    ctx.undo_last()
    assert doc_names(ctx) == before, "one Undo removes every copy"
    assert len(ctx.model.file.Groups) == groups_before, "...and the copies' groups"
    for key, (x, y) in LAYOUT.items():
        assert groups_of(ctx, names[key]) == original[key], "originals keep their groups"
        assert start_xy(ctx, names[key]) == (x, y), "originals back where they started"
    ctx.redo_last()
    assert doc_names(ctx) - before == copies, "Redo brings the copies back"
    assert check_copy_set(ctx, names, original, copies, 0.0, "gumball redo") == new
    print("PASS: gumball Alt-copy -> new groups, ungrouped stays ungrouped, one Undo")


def copy_command():
    ctx, names = fresh()
    ctx.select_by_names([names["a1"], names["b1"], names["loose"]])
    original = {k: groups_of(ctx, n) for k, n in names.items()}
    groups_before = len(ctx.model.file.Groups)
    before = doc_names(ctx)
    cmd = commands.CopyCmd()
    cmd.begin(ctx)
    cmd.feed("0,0,0", ctx)
    cmd.feed("100,0,0", ctx)
    cmd.feed("200,0,0", ctx)
    message, done = cmd.feed("", ctx)
    assert done and "Created 10 copy(ies)" in message, message
    made = doc_names(ctx) - before
    sets = [{c for c in made if abs(start_xy(ctx, c)[0] - dx) < 50.0 + 1e-6
             and start_xy(ctx, c)[0] >= dx} for dx in (100.0, 200.0)]
    g1 = check_copy_set(ctx, names, original, sets[0], 100.0, "Copy #1")
    g2 = check_copy_set(ctx, names, original, sets[1], 200.0, "Copy #2")
    assert not g1 & g2, "each copy set is its own new group"
    assert len(ctx.model.file.Groups) == groups_before + 4
    assert ctx.undo.undo_names() == ["Copy"], ctx.undo.undo_names()
    ctx.undo_last()
    assert doc_names(ctx) == before and len(ctx.model.file.Groups) == groups_before, \
        "one Undo removes the copies and their groups"
    print("PASS: Copy command -> each copy set forms its own new groups")


def run():
    gumball_alt_copy()
    copy_command()


run()

# RED check: with the regrouping disabled (the old behaviour) the suite must fail.
real = commands._group_copies
commands._group_copies = lambda ctx, pairs: 0
try:
    try:
        gumball_alt_copy()
    except AssertionError as error:
        assert "share ONE group" in str(error), error
        print("RED verified: gumball copies lose their group without _group_copies")
    else:
        raise AssertionError("mutation survived: gumball copies kept no group yet passed")
    try:
        copy_command()
    except AssertionError as error:
        assert "share ONE group" in str(error), error
        print("RED verified: Copy command copies lose their group without _group_copies")
    else:
        raise AssertionError("mutation survived: Copy copies kept no group yet passed")
finally:
    commands._group_copies = real
run()
print("PASS: group copy")
