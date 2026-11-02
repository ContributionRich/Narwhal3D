#!/usr/bin/env python3
"""SetGroupName rename/combine, safe memberships, and Undo/Redo."""
import importlib.util
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


load("narwhal3d.cplane", "narwhal3d/cplane.py")
load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = load("narwhal3d.model", "narwhal3d/model.py")
undo = load("narwhal3d.undo", "narwhal3d/undo.py")
load("narwhal3d.options", "narwhal3d/options.py")
commands = load("narwhal3d.commands", "narwhal3d/commands.py")


class Ctx:
    def __init__(self):
        self.model = model.NarwhalModel()
        self.undo = undo.UndoStack()
        self._selected = []

    def begin_undo(self, name):
        return self.undo.begin(name)

    def end_undo(self):
        return self.undo.end()

    def note_change(self, name=None):
        if not self.undo.recording:
            self.undo.begin(name or "Change")
        return self.undo.capture(self.model.snapshot())

    def undo_last(self):
        record = self.undo.undo(self.model.snapshot())
        if record is None:
            return None
        self.model.restore(record.before)
        return record.name

    def redo_last(self):
        record = self.undo.redo(self.model.snapshot())
        if record is None:
            return None
        self.model.restore(record.after)
        return record.name

    def selected_objects(self):
        return [types.SimpleNamespace(name=o.Attributes.Name)
                for o in self.model.file.Objects
                if (o.Attributes.Name or "") in self._selected]

    def select_by_names(self, names):
        self._selected = list(names or [])
        return self._selected


def group(ctx, name):
    g = r3.Group()
    g.Name = name
    ctx.model.file.Groups.Add(g)
    for i in range(len(ctx.model.file.Groups)):
        if ctx.model.file.Groups.FindIndex(i).Name == name:
            return i
    raise AssertionError("group fixture was not inserted")


def obj(ctx, name, x):
    ctx.model.add_object(r3.Point(r3.Point3d(float(x), 0, 0)), name, 0)
    return next(o for o in ctx.model.file.Objects
                if o.Attributes.Name == name)


def memberships(ctx, name):
    fobj = next(o for o in ctx.model.file.Objects
                if o.Attributes.Name == name)
    return list(fobj.Attributes.GetGroupList2() or [])


def run_direct(ctx, selected, name):
    cmd = commands.SetGroupNameCmd()
    cmd.args = [list(selected), name]
    return cmd._recorded(ctx, lambda: cmd.execute(ctx))


def check(ok, label):
    if not ok:
        raise AssertionError(label)
    print("  ok  " + label)


def main():
    check("setgroupname" in commands.COMMANDS,
          "SetGroupName is registered")

    # One selected group's exact rename preserves its members and table ID.
    ctx = Ctx()
    alpha = group(ctx, "Alpha")
    one = obj(ctx, "one", 1)
    two = obj(ctx, "two", 2)
    one.Attributes.AddToGroup(alpha)
    two.Attributes.AddToGroup(alpha)
    before = ctx.model.group_records()
    msg, done = run_direct(ctx, ["one"], "Renamed")
    check(done and "renamed" in msg, "single selected group is renamed")
    check(ctx.model.group_records()[alpha]["Name"] == "Renamed",
          "single rename keeps exact table row and ID")
    check(memberships(ctx, "one") == [alpha] and memberships(ctx, "two") == [alpha],
          "single rename keeps every membership")
    check(ctx.undo.undo_names() == ["SetGroupName"], "rename is one undo record")
    changed = ctx.model.group_records()
    check(ctx.undo_last() == "SetGroupName", "Undo reports SetGroupName")
    check(ctx.model.group_records() == before, "Undo restores exact group table")
    check(memberships(ctx, "one") == [alpha] and memberships(ctx, "two") == [alpha],
          "Undo restores exact rename memberships")
    check(ctx.redo_last() == "SetGroupName", "Redo reports SetGroupName")
    check(ctx.model.group_records() == changed, "Redo restores exact renamed table")

    # Several selected grouped objects combine all groups they belong to. An
    # overlapping unrelated group remains on every object that had it.
    ctx = Ctx()
    a = group(ctx, "A")
    b = group(ctx, "B")
    unrelated = group(ctx, "Unrelated")
    a_only = obj(ctx, "a_only", 1)
    overlap = obj(ctx, "overlap", 2)
    b_only = obj(ctx, "b_only", 3)
    untouched = obj(ctx, "untouched", 4)
    a_only.Attributes.AddToGroup(a)
    overlap.Attributes.AddToGroup(a)
    overlap.Attributes.AddToGroup(unrelated)
    b_only.Attributes.AddToGroup(b)
    untouched.Attributes.AddToGroup(unrelated)
    before_groups = ctx.model.group_records()
    before_members = {n: memberships(ctx, n)
                      for n in ("a_only", "overlap", "b_only", "untouched")}
    msg, done = run_direct(ctx, ["a_only", "b_only"], "Combined")
    check(done and "combined 2 groups" in msg, "selected groups combine")
    check(ctx.model.file.Groups[a].Name == "Combined" and
          ctx.model.file.Groups[b].Name == "B",
          "survivor is renamed and former row is retained")
    check(set(memberships(ctx, "a_only")) == {a} and
          set(memberships(ctx, "b_only")) == {a} and
          set(memberships(ctx, "overlap")) == {a, unrelated},
          "all combined members move while unrelated overlap survives")
    check(memberships(ctx, "untouched") == [unrelated],
          "unrelated object membership is unchanged")
    check(ctx.undo.undo_names() == ["SetGroupName"], "combine is one undo record")
    after_members = {n: memberships(ctx, n)
                     for n in before_members}
    check(ctx.undo_last() == "SetGroupName", "combined operation undoes normally")
    check(ctx.model.group_records() == before_groups and
          {n: memberships(ctx, n) for n in before_members} == before_members,
          "Undo restores exact table and every membership")
    check(ctx.redo_last() == "SetGroupName" and
          {n: memberships(ctx, n) for n in before_members} == after_members,
          "Redo restores the complete combination")

    # Explicit refusals: exact case, blank, conflicting, duplicate/ambiguous,
    # and a selection that is not grouped. None may create an undo record.
    ctx = Ctx()
    g = group(ctx, "CaseSensitive")
    obj(ctx, "candidate", 1).Attributes.AddToGroup(g)
    other = group(ctx, "Other")
    obj(ctx, "other", 2).Attributes.AddToGroup(other)
    old = ctx.model.group_records()
    for selected, name, needle in (
        (["candidate"], "", "blank"),
        (["candidate"], "Other", "already exists"),
        (["missing"], "New", "not in a group"),
    ):
        msg, done = run_direct(ctx, selected, name)
        check(done and needle in msg, f"explicit refusal/report for {name!r}")
    check(ctx.model.group_records() == old and ctx.undo.undo_names() == [],
          "refusals preserve table and history")
    msg, done = run_direct(ctx, ["candidate"], "casesensitive")
    check(done and "renamed" in msg and ctx.model.file.Groups[g].Name == "casesensitive",
          "group names compare case-sensitively")
    # rhino3dm's group table enforces unique names on insertion, so exercise
    # the documented imported/round-tripped duplicate-name refusal at the
    # command boundary with the same helper result such a document supplies.
    member = obj(ctx, "duplicate_member", 3)
    member.Attributes.AddToGroup(g)
    real_lookup = commands._group_indices_by_name
    commands._group_indices_by_name = lambda file, name: (
        [g, other] if name == "Duplicate" else real_lookup(file, name))
    try:
        msg, done = run_direct(ctx, ["duplicate_member"], "Duplicate")
    finally:
        commands._group_indices_by_name = real_lookup
    check(done and "ambiguous" in msg,
          f"duplicate target name is rejected as ambiguous ({msg!r})")

    # Normal selection/feed route: preselected members are consumed, then the
    # typed text supplies the exact new name.
    ctx = Ctx()
    picked = group(ctx, "Picked")
    obj(ctx, "picked", 1).Attributes.AddToGroup(picked)
    ctx.select_by_names(["picked"])
    cmd = commands.SetGroupNameCmd()
    check(cmd.begin(ctx) == (None, False), "selected member starts SetGroupName")
    msg, done = cmd.feed("FromFeed", ctx)
    check(done and "renamed" in msg and ctx.model.file.Groups[picked].Name == "FromFeed",
          "typed name completes the selection route")

    # MEASURED (research_all_13 note 154): nothing selected, the prompt is
    # "Select groups to name:"; a typed existing group name goes on to "New group
    # name:" and renames; any other token prints "Not a group name." and ends.
    ctx = Ctx()
    g01 = group(ctx, "Group01")
    obj(ctx, "l1", 1).Attributes.AddToGroup(g01)
    obj(ctx, "l2", 2).Attributes.AddToGroup(g01)
    cmd = commands.SetGroupNameCmd()
    check(cmd.begin(ctx) == (None, False) and cmd.prompt.startswith("Select groups to name"),
          f"nothing selected: the reference CAD application's prompt ({cmd.prompt!r})")
    msg, done = cmd.feed("Group01", ctx)
    check(not done and cmd.prompt.startswith("New group name"),
          f"a typed group name goes on to 'New group name:' ({msg!r}, {cmd.prompt!r})")
    msg, done = cmd._recorded(ctx, lambda: cmd.feed("Pair01", ctx))
    check(done and ctx.model.file.Groups[g01].Name == "Pair01"
          and memberships(ctx, "l1") == [g01] and memberships(ctx, "l2") == [g01],
          f"the typed group is renamed, members kept ({msg!r})")
    check(ctx.undo.undo_names() == ["SetGroupName"], "typed rename is one undo record")
    for token in ("_SelLast", "NoSuchGroup", "l1"):
        cmd = commands.SetGroupNameCmd()
        cmd.begin(ctx)
        msg, done = cmd.feed(token, ctx)
        check(done and msg == "Not a group name.",
              f"typed {token!r} is refused: {msg!r}")
    check(ctx.model.file.Groups[g01].Name == "Pair01", "refusals change nothing")

    print("SetGroupName: all checks passed")


if __name__ == "__main__":
    main()
