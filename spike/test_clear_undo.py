"""Focused tests for the ClearUndo command.

Imports the real narwhal3d.commands.ClearUndoCmd (and registry) so regressions
in production code are caught. Covers confirmation (Yes/No with No default),
invalid input, empty history, stack clearing via public API, and document
preservation via model.snapshot().
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from narwhal3d.commands import ClearUndoCmd, COMMANDS
from narwhal3d.model import RhinoModel
from narwhal3d.undo import UndoStack

results = []


def check(name):
    def decorate(function):
        try:
            function()
            results.append((name, "PASS", ""))
        except Exception as exc:
            results.append((name, "FAIL", repr(exc)))
        return function
    return decorate


def _ctx_with_history(undo_n=0, redo_n=0, with_objects=False):
    model = RhinoModel()
    # document is empty but snapshot still captures settings/layers for comparison
    undo = UndoStack()
    # populate stacks with dummy records that have .name
    class Rec:
        def __init__(self, name):
            self.name = name
    for i in range(undo_n):
        undo._undo.append(Rec(f"u{i}"))
    for i in range(redo_n):
        undo._redo.append(Rec(f"r{i}"))

    class Ctx:
        pass
    ctx = Ctx()
    ctx.undo = undo
    ctx.model = model
    return ctx


@check("registry contains ClearUndo")
def _():
    assert "ClearUndo" in COMMANDS
    assert COMMANDS["ClearUndo"].name == "ClearUndo"
    assert type(COMMANDS["ClearUndo"]) is ClearUndoCmd


@check("empty history reports nothing to clear without prompting")
def _():
    cmd = ClearUndoCmd()
    ctx = _ctx_with_history(0, 0)
    msg, done = cmd.begin(ctx)
    assert done is True
    assert "Nothing to clear" in msg


@check("Enter defaults to No (cancellation)")
def _():
    cmd = ClearUndoCmd()
    ctx = _ctx_with_history(2, 1)
    cmd.begin(ctx)
    # simulate Enter: _enter_default returns "No", which becomes the arg
    default = cmd._enter_default(ctx)
    assert default == "No"
    cmd.args = [default]
    msg, done = cmd.execute(ctx)
    assert done is True
    assert "cancelled" in msg.lower()
    assert len(ctx.undo.undo_names()) == 2


@check("invalid input keeps command active")
def _():
    cmd = ClearUndoCmd()
    ctx = _ctx_with_history(1, 1)
    cmd.begin(ctx)
    cmd.args = ["maybe"]
    msg, done = cmd.execute(ctx)
    assert done is False
    assert "Yes or No" in msg
    assert cmd.step_index == 0
    assert len(ctx.undo.undo_names()) == 1


@check("Yes clears both stacks and preserves document snapshot")
def _():
    cmd = ClearUndoCmd()
    ctx = _ctx_with_history(3, 2)
    before = ctx.model.snapshot()
    cmd.begin(ctx)
    cmd.args = ["yes"]
    msg, done = cmd.execute(ctx)
    assert done is True
    assert "Cleared 3 undo and 2 redo" in msg
    assert len(ctx.undo.undo_names()) == 0
    assert len(ctx.undo.redo_names()) == 0
    after = ctx.model.snapshot()
    # compare object tables / settings; snapshots should be equal for unchanged doc
    assert before.settings == after.settings
    assert len(before) == len(after)  # same number of objects


@check("short y is accepted")
def _():
    cmd = ClearUndoCmd()
    ctx = _ctx_with_history(1, 0)
    cmd.begin(ctx)
    cmd.args = ["y"]
    msg, done = cmd.execute(ctx)
    assert done is True
    assert "Cleared 1 undo" in msg
    assert len(ctx.undo.undo_names()) == 0


if __name__ == "__main__":
    for name, status, detail in results:
        print(f"{status}: {name}" + (f" -- {detail}" if detail else ""))
    failed = sum(1 for _, s, _ in results if s == "FAIL")
    raise SystemExit(failed)
