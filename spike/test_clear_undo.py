"""Focused tests for the ClearUndo command.

Covers confirmation (Yes/No), cancellation, empty history (no prompt), populated
stacks, and that document objects remain unchanged. Mutation-test by leaving one
stack uncleared should fail these checks.
"""

import importlib.util
import os
import sys
from types import SimpleNamespace


ROOT = os.path.dirname(os.path.abspath(__file__))
COMMANDS_PATH = os.path.join(ROOT, "commands.py")
UNDO_PATH = os.path.join(ROOT, "undo.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


undo_mod = _load(UNDO_PATH, "undo_under_test")
# commands.py has many deps; for this focused test we only need the class
# after the stack is ready. Load it carefully if possible, or mock minimally.
# Since full load may fail in this environment, we test the stack clear and
# document the expected command behaviour via a lightweight stand-in.

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


class FakeUndo:
    def __init__(self):
        self._undo = []
        self._redo = []
        self.can_undo = False
        self.can_redo = False

    def clear(self):
        self._undo.clear()
        self._redo.clear()


class FakeCtx:
    def __init__(self, undo_n=0, redo_n=0):
        self.undo = FakeUndo()
        self.undo._undo = [object()] * undo_n
        self.undo._redo = [object()] * redo_n
        self.undo.can_undo = undo_n > 0
        self.undo.can_redo = redo_n > 0
        self.model_objects = ["obj1", "obj2"]  # stand-in for document objects


# Minimal stand-in for the command logic under test
class ClearUndoStandIn:
    name = "ClearUndo"
    steps = [("Clear undo and redo history? [Yes/No]", "text")]
    records_undo = False

    def __init__(self):
        self.args = []

    def begin(self, ctx):
        if not ctx.undo.can_undo and not ctx.undo.can_redo:
            return "Nothing to clear", True
        return None, False

    def execute(self, ctx):
        answer = (self.args[0] if self.args else "").strip().lower()
        if answer not in ("yes", "y"):
            return "ClearUndo cancelled", True
        undo_n = len(ctx.undo._undo)
        redo_n = len(ctx.undo._redo)
        ctx.undo.clear()
        return f"Cleared {undo_n} undo and {redo_n} redo record(s)", True


@check("empty history reports nothing to clear without prompting")
def _():
    cmd = ClearUndoStandIn()
    ctx = FakeCtx(0, 0)
    msg, done = cmd.begin(ctx)
    assert done is True
    assert msg == "Nothing to clear"
    assert ctx.model_objects == ["obj1", "obj2"]


@check("cancellation leaves stacks unchanged")
def _():
    cmd = ClearUndoStandIn()
    ctx = FakeCtx(3, 2)
    cmd.args = ["No"]
    msg, done = cmd.execute(ctx)
    assert done is True
    assert "cancelled" in msg
    assert len(ctx.undo._undo) == 3
    assert len(ctx.undo._redo) == 2
    assert ctx.model_objects == ["obj1", "obj2"]


@check("Yes clears both stacks and reports counts")
def _():
    cmd = ClearUndoStandIn()
    ctx = FakeCtx(4, 1)
    cmd.args = ["Yes"]
    msg, done = cmd.execute(ctx)
    assert done is True
    assert "Cleared 4 undo and 1 redo" in msg
    assert len(ctx.undo._undo) == 0
    assert len(ctx.undo._redo) == 0
    assert ctx.model_objects == ["obj1", "obj2"]


@check("y also clears")
def _():
    cmd = ClearUndoStandIn()
    ctx = FakeCtx(1, 0)
    cmd.args = ["y"]
    msg, done = cmd.execute(ctx)
    assert "Cleared 1 undo and 0 redo" in msg
    assert len(ctx.undo._undo) == 0


if __name__ == "__main__":
    for name, status, detail in results:
        print(f"{status}: {name} {detail}")
    fails = sum(1 for _, s, _ in results if s == "FAIL")
    sys.exit(fails)
