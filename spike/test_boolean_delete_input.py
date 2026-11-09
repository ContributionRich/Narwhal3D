#!/usr/bin/env python3
"""Boolean DeleteInput/DeleteCutters command behavior.

Run with the project Python and OCCT available:
  PYTHONPATH=vendor/occt tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 \
      spike/test_boolean_delete_input.py

The boolean construction is deliberately the existing bridge route; this suite only
checks command options, document ownership, and the one undo record around it.
"""
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
bridge = load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = load("narwhal3d.model", "narwhal3d/model.py")
undo = load("narwhal3d.undo", "narwhal3d/undo.py")
load("narwhal3d.options", "narwhal3d/options.py")
commands = load("narwhal3d.commands", "narwhal3d/commands.py")
from narwhal3d import kernel  # noqa: E402
from narwhal3d.kernel import occt_backend as occt  # noqa: E402

checks = 0
failures = []


def check(condition, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


class Ctx:
    """Small real-model context, including the production undo contract."""

    def __init__(self, named):
        self.model = model.NarwhalModel()
        self.undo = undo.UndoStack()
        self.added = []
        self.change_calls = 0
        for name, geom in named:
            self.model.add_object(geom, name, 0)

    def begin_undo(self, name):
        return self.undo.begin(name)

    def end_undo(self):
        return self.undo.end()

    def note_change(self, name=None):
        self.change_calls += 1
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

    def names(self):
        return [o.Attributes.Name for o in self.model.file.Objects]

    def add_geometry(self, geom, base_name, layer_index=None):
        name = f"{base_name}_{len(self.added) + 1}"
        self.model.add_object(geom, name, int(layer_index or 0))
        self.added.append(geom)
        return name, name

    def geom_by_name(self, name):
        for obj in self.model.file.Objects:
            if (obj.Attributes.Name or "").lower() == name.lower():
                return obj.Geometry
        return None

    def object_by_name(self, name):
        return None


def box(x0, x1):
    return bridge.make_box((x0, 0, 0), (x1, 2, 0), 2.0)


def run(cls, named, args, options=None, recorded=False):
    ctx = Ctx(named)
    cmd = cls()
    cmd.args = [list(group) for group in args]
    if options:
        cmd.opt.update(options)
    if recorded:
        result = cmd._recorded(ctx, lambda: cmd.execute(ctx))
    else:
        result = cmd.execute(ctx)
    return cmd, ctx, result


def analytic_solid(geom):
    master = kernel.master_of(geom)
    return (master is not None and occt.is_airtight(master)
            and kernel.honesty_problem(geom) is None
            and kernel.representation_of(geom) != kernel.MESH_DERIVED)


if not occt.available():
    print(f"skip: OCCT unavailable ({occt.unavailable_reason()})")
    sys.exit(0)

print("-- declarations and honest conditional option --")
for cls, expected in ((commands.BooleanUnionCmd, {"DeleteInput"}),
                      (commands.BooleanDifferenceCmd, {"DeleteInput", "DeleteCutters"}),
                      (commands.BooleanIntersectionCmd, {"DeleteInput"})):
    names = {option.name for option in cls.options}
    check(names == expected, f"{cls.name} declares exactly {sorted(expected)}")

diff = commands.BooleanDifferenceCmd()
diff.opt["DeleteInput"] = False
message, done = diff.on_option_set(next(o for o in diff.options if o.name == "DeleteCutters"), True, None)
check(not done and "requires DeleteInput=Yes" in message,
      "DeleteCutters=Yes refuses when DeleteInput=No")

print("\n-- DeleteInput=No keeps every operand and creates exact solids --")
for cls, args in ((commands.BooleanUnionCmd, [["a", "b"]]),
                  (commands.BooleanDifferenceCmd, [["a"], ["b"]]),
                  (commands.BooleanIntersectionCmd, [["a"], ["b"]])):
    _, ctx, result_message = run(
        cls, [("a", box(0, 2)), ("b", box(1, 3))], args,
        {"DeleteInput": False, "DeleteCutters": False}, recorded=True)
    check(len(ctx.added) == 1 and "Boolean_1" in ctx.names(),
          f"{cls.name}: creates one result with DeleteInput=No ({result_message!r})")
    check(set(ctx.names()) == {"a", "b", "Boolean_1"},
          f"{cls.name}: preserves both input identities")
    check(analytic_solid(ctx.added[0]), f"{cls.name}: result remains analytic and airtight")
    check(ctx.undo.undo_names() == [cls.name], f"{cls.name}: one undo record")
    check(ctx.undo_last() == cls.name and set(ctx.names()) == {"a", "b"},
          f"{cls.name}: undo restores the two original objects")
    check(ctx.redo_last() == cls.name and set(ctx.names()) == {"a", "b", "Boolean_1"},
          f"{cls.name}: redo restores the result and kept inputs")

print("\n-- DeleteInput=Yes and Difference DeleteCutters branch --")
_, ctx, _ = run(commands.BooleanUnionCmd,
                [("a", box(0, 2)), ("b", box(1, 3))], [["a", "b"]], recorded=True)
check(ctx.names() == ["Boolean_1"], "BooleanUnion default OURS deletes all inputs")
check(ctx.change_calls == 1 and analytic_solid(ctx.added[0]),
      "BooleanUnion default has one mutation capture and exact solid result")

check(next(o for o in commands.BooleanDifferenceCmd.options if o.name == "DeleteCutters").default
      is False, "BooleanDifference keeps cutters by default (OURS, report #129)")
_, ctx, _ = run(commands.BooleanDifferenceCmd,
                [("a", box(0, 2)), ("b", box(1, 3))], [["a"], ["b"]], recorded=True)
check(set(ctx.names()) == {"b", "Boolean_1"},
      "BooleanDifference default replaces the target and keeps the cutter")

_, ctx, _ = run(commands.BooleanDifferenceCmd,
                [("a", box(0, 2)), ("b", box(1, 3))], [["a"], ["b"]],
                {"DeleteInput": True, "DeleteCutters": False}, recorded=True)
check(set(ctx.names()) == {"b", "Boolean_1"},
      "BooleanDifference DeleteCutters=No deletes target but keeps cutter")
check(analytic_solid(ctx.added[0]), "BooleanDifference DeleteCutters=No stays exact and airtight")

_, ctx, _ = run(commands.BooleanDifferenceCmd,
                [("a", box(0, 2)), ("b", box(1, 3))], [["a"], ["b"]],
                {"DeleteInput": True, "DeleteCutters": True}, recorded=True)
check(ctx.names() == ["Boolean_1"],
      "BooleanDifference DeleteCutters=Yes deletes target and cutter")
check(ctx.change_calls == 1 and ctx.undo.undo_names() == ["BooleanDifference"],
      "BooleanDifference deletion is one undo record")

print()
if failures:
    print(f"{checks - len(failures)}/{checks} checks passed")
    for failure in failures:
        print("FAIL:", failure)
    sys.exit(1)
print(f"all {checks} checks passed")
