#!/usr/bin/env python3
"""the `ClearUndo` command: discard the undo and redo history, keep the document.

Run (with the native history core, as run_tests.sh sets it up):
  tools/blender-patched/5.2/python/bin/python3.13 spike/test_clear_undo.py

Drives the REAL registered command on a real NarwhalContext and its native
history. The history is built through the public undo boundary (begin_undo /
note_change / end_undo, then undo_last for a redo record), never by poking the
backend.

Covered:
  * the command is registered, prompts once, and records no undo itself;
  * empty history: a no-op message, no prompt;
  * Enter and "No" keep every record;
  * invalid text re-prompts and clears nothing;
  * "Yes" clears both sides and reports both counts;
  * UndoStack.clear returns the cleared counts;
  * the document is identical before and after.

Loaded by file path (no Blender): the same trick as spike/test_visibility.py.
"""
import importlib.util
import json
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [ADDON]
pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = pkg


def _load(name, relpath):
    path = os.path.join(ADDON, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bridge = _load("narwhal3d.bridge", "bridge.py")
model = _load("narwhal3d.model", "model.py")
_load("narwhal3d.options", "options.py")
commands = _load("narwhal3d.commands", "commands.py")

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


def real_ctx():
    """A real NarwhalContext with only the bpy-touching display hooks stubbed."""
    ctx = commands.NarwhalContext(model.NarwhalModel())
    ctx.selected_objects = lambda: []
    ctx.rebuild_display = lambda: None
    ctx.select_by_names = lambda names: None
    return ctx


def add_recorded(ctx, name, x):
    """One undoable change through the public undo boundary."""
    ctx.begin_undo("Line")
    ctx.note_change("Line")
    ctx.model.add_object(bridge.make_polyline([(0, 0, 0), (x, 0, 0)], degree=1), name)
    ctx.end_undo()


def history(ctx, undos, redos):
    """`undos` records left to undo and `redos` records waiting to redo."""
    for i in range(undos + redos):
        add_recorded(ctx, f"L{i}", i + 1)
    for _ in range(redos):
        ctx.undo_last()
    return ctx


def counts(ctx):
    return len(ctx.undo.undo_names()), len(ctx.undo.redo_names())


def document(ctx):
    """Every object's id, attributes and exact geometry, plus the layer table."""
    rows = []
    for f in ctx.model.file.Objects:
        a = f.Attributes
        rows.append((str(a.Id), a.Name, int(a.LayerIndex), int(a.Mode),
                     json.dumps(f.Geometry.Encode(), sort_keys=True)))
    layers = [(l.Name, str(l.Id)) for l in ctx.model.file.Layers]
    return sorted(rows), layers


def run(ctx, *lines):
    """begin() the registered command, then feed each line. Returns (cmd, msg, done)."""
    cmd = type(commands.COMMANDS["clearundo"])()
    msg, done = cmd.begin(ctx)
    for line in lines:
        if done:
            break
        msg, done = cmd.feed(line, ctx)
    return cmd, msg, done


print()
print("ClearUndo is registered and shaped like Undo/Redo")
cmd = commands.COMMANDS.get("clearundo")
check(cmd is not None and cmd.name == "ClearUndo", "ClearUndo is registered")
check(cmd is not None and cmd.records_undo is False,
      "records_undo is False (it works on the history, outside a record)")
check(cmd is not None and [k for _p, k in cmd.steps] == [commands.TEXT],
      "one Yes/No prompt")

print()
print("empty history: a no-op, no prompt")
ctx = real_ctx()
before = document(ctx)
c, msg, done = run(ctx)
check(done and msg == "No undo history to clear.", f"says there is nothing to clear ({msg!r})")
check(c.step_index == 0 and counts(ctx) == (0, 0), "no prompt was answered, nothing changed")

print()
print("Enter keeps the history (the default is No)")
ctx = history(real_ctx(), 2, 1)
check(counts(ctx) == (2, 1), f"precondition: 2 undo and 1 redo records ({counts(ctx)})")
before = document(ctx)
c, msg, done = run(ctx)
check(not done and "<No>" in c.prompt, f"it asks, showing No as the default ({c.prompt!r})")
msg, done = c.feed("", ctx)
check(done and msg == "Undo history kept.", f"Enter keeps the history ({msg!r})")
check(counts(ctx) == (2, 1), f"every record is still there ({counts(ctx)})")
check(document(ctx) == before, "the document is unchanged")

print()
print("No keeps the history")
c, msg, done = run(ctx, "No")
check(done and msg == "Undo history kept." and counts(ctx) == (2, 1),
      f"No keeps every record ({msg!r}, {counts(ctx)})")

print()
print("invalid text re-prompts instead of clearing or cancelling")
c, msg, done = run(ctx, "maybe")
check(not done and msg == "ClearUndo: answer Yes or No", f"it re-prompts ({msg!r})")
check(c.step_index == 0 and counts(ctx) == (2, 1), "nothing was cleared")
msg, done = c.feed("No", ctx)
check(done and counts(ctx) == (2, 1), "a valid answer after the re-prompt still works")

print()
print("Yes clears both sides and reports both counts")
ctx = history(real_ctx(), 3, 2)
check(counts(ctx) == (3, 2), f"precondition: 3 undo and 2 redo records ({counts(ctx)})")
before = document(ctx)
c, msg, done = run(ctx, "Yes")
check(done and msg == "Cleared 3 undo records and 2 redo records.", f"reports both counts ({msg!r})")
check(counts(ctx) == (0, 0), f"no undo or redo record is left ({counts(ctx)})")
check(not ctx.undo.can_undo and not ctx.undo.can_redo, "Undo and Redo have nothing to do")
check(document(ctx) == before, "the whole document is preserved")
check(commands.COMMANDS["undo"].execute(ctx)[0] == "Nothing to undo",
      "Undo afterwards reports there is nothing to undo")
c, msg, done = run(ctx)
check(done and msg == "No undo history to clear.", "a second ClearUndo is a no-op")

ctx = history(real_ctx(), 1, 0)
c, msg, done = run(ctx, "yes")
check(msg == "Cleared 1 undo record and 0 redo records.", f"singular wording ({msg!r})")

print()
print("UndoStack.clear returns the public counts")
ctx = history(real_ctx(), 2, 1)
check(ctx.undo.clear() == {"undo_count": 2, "redo_count": 1},
      "clear() returns {'undo_count': 2, 'redo_count': 1}")
check(ctx.undo.clear() == {"undo_count": 0, "redo_count": 0}, "and zeros once empty")

print()
print("=" * 60)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"CLEARUNDO TESTS PASSED ({checks} checks)")
