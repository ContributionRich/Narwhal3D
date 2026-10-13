#!/usr/bin/env python3
"""Focused tests for the ClearUndo command and UndoStack.clear()."""
from __future__ import annotations

import importlib.util
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


undo = _load("narwhal3d.undo", "narwhal3d/undo.py")
cplane = _load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model_mod = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")

checks = 0
failures: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    global checks
    checks += 1
    status = "ok  " if cond else "FAIL"
    suffix = f" ({detail})" if detail and not cond else ""
    print(f"  {status} {label}{suffix}")
    if not cond:
        failures.append(label)


def make_populated_context():
    model = model_mod.NarwhalModel()
    ctx = commands.NarwhalContext(model)
    ctx.rebuild_display = lambda: None

    # Step 1: add a point inside an undo record
    ctx.begin_undo("Point")
    ctx.note_change("Point")
    pt1_id, _ = model.add_object(r3.Point(r3.Point3d(1.0, 2.0, 3.0)), name="Pt1")
    ctx.end_undo()

    # Step 2: add a second point inside an undo record
    ctx.begin_undo("Point")
    ctx.note_change("Point")
    pt2_id, _ = model.add_object(r3.Point(r3.Point3d(4.0, 5.0, 6.0)), name="Pt2")
    ctx.end_undo()

    # Step 3: add a third point and then undo it so redo stack is also populated
    ctx.begin_undo("Point")
    ctx.note_change("Point")
    model.add_object(r3.Point(r3.Point3d(7.0, 8.0, 9.0)), name="Pt3")
    ctx.end_undo()
    undone = ctx.undo_last()
    assert undone == "Point"

    return ctx, model, (pt1_id, pt2_id)


# 1. Registry and command metadata checks
check("clearundo" in commands.COMMANDS, "'clearundo' is registered in COMMANDS")
reg_cmd = commands.COMMANDS["clearundo"]
check(isinstance(reg_cmd, commands.ClearUndoCmd), "registered instance is ClearUndoCmd")
check(commands.ClearUndoCmd.name == "ClearUndo", "command name is ClearUndo")
check(commands.ClearUndoCmd.records_undo is False, "records_undo is False")

# 2. Empty stacks: reports nothing to clear without prompting
empty_model = model_mod.NarwhalModel()
empty_ctx = commands.NarwhalContext(empty_model)
empty_cmd = commands.ClearUndoCmd()
msg, done = empty_cmd.begin(empty_ctx)
check(done is True and msg == "Nothing to clear",
      "empty history finishes immediately with 'Nothing to clear'", repr((msg, done)))
check(empty_cmd.step_index == 0 and not empty_cmd.args,
      "empty history does not advance or collect prompt args")
check(not empty_ctx.undo.can_undo and not empty_ctx.undo.can_redo,
      "empty history leaves undo and redo stacks empty")

# 3. Enter default (No) leaves history and document unchanged
ctx, model, expected_ids = make_populated_context()
snap_before = model.snapshot()
check(ctx.undo.can_undo and ctx.undo.can_redo,
      "populated context has both undo and redo records")
check(len(ctx.undo.undo_names()) == 2 and len(ctx.undo.redo_names()) == 1,
      "populated context has 2 undo and 1 redo record")

cmd_enter = commands.ClearUndoCmd()
msg, done = cmd_enter.begin(ctx)
check(done is False and msg is None, "populated history waits for confirmation")
check("Yes/No" in cmd_enter.prompt and "<No>" in cmd_enter.prompt,
      "prompt shows explicit Yes/No confirmation with No default", cmd_enter.prompt)

msg, done = cmd_enter.feed("", ctx)
check(done is True and msg == "ClearUndo cancelled",
      "empty Enter defaults to No and cancels ClearUndo", repr((msg, done)))
check(len(ctx.undo.undo_names()) == 2 and len(ctx.undo.redo_names()) == 1,
      "empty Enter leaves both undo and redo stacks intact")
check(model.snapshot() == snap_before and model.object_count() == 2,
      "empty Enter leaves document state unchanged")

# 4. Explicit No leaves history and document unchanged
cmd_no = commands.ClearUndoCmd()
msg, done = cmd_no.begin(ctx)
check(done is False, "begin before explicit No stays active")
msg, done = cmd_no.feed("No", ctx)
check(done is True and msg == "ClearUndo cancelled",
      "explicit No cancels ClearUndo", repr((msg, done)))
check(len(ctx.undo.undo_names()) == 2 and len(ctx.undo.redo_names()) == 1,
      "explicit No leaves both undo and redo stacks intact")
check(model.snapshot() == snap_before,
      "explicit No leaves document state unchanged")

# 5. Invalid input keeps command active with answer-Yes-or-No message
cmd_invalid = commands.ClearUndoCmd()
msg, done = cmd_invalid.begin(ctx)
check(done is False, "begin before invalid input stays active")
msg, done = cmd_invalid.feed("maybe", ctx)
check(done is False and msg == "ClearUndo: answer Yes or No",
      "invalid input stays active and asks for Yes or No", repr((msg, done)))
check(cmd_invalid.step_index == 0 and not cmd_invalid.args,
      "invalid input resets step_index and pops bad token")
check(len(ctx.undo.undo_names()) == 2 and len(ctx.undo.redo_names()) == 1,
      "invalid input does not alter undo or redo stacks")

# Follow invalid input with explicit Yes to confirm recovery
msg, done = cmd_invalid.feed("Yes", ctx)
check(done is True and msg == "Cleared 2 undo and 1 redo record(s)",
      "Yes after invalid input clears both stacks and reports counts", repr((msg, done)))
check(not ctx.undo.can_undo and not ctx.undo.can_redo,
      "Yes clears both undo and redo stacks")
check(len(ctx.undo.undo_names()) == 0 and len(ctx.undo.redo_names()) == 0,
      "undo_names and redo_names are both empty after clear")
check(not ctx.undo.recording,
      "clearing history does not open or leave an undo record")
check(model.snapshot() == snap_before and model.object_count() == 2,
      "clearing history leaves document geometry unchanged")
check([fobj.Attributes.Name for fobj in model.file.Objects] == ["Pt1", "Pt2"],
      "document objects Pt1 and Pt2 remain intact after ClearUndo")

# 6. Subsequent Undo and Redo report nothing available
u_msg, u_done = commands.UndoCmd().begin(ctx)
r_msg, r_done = commands.RedoCmd().begin(ctx)
check(u_done is True and u_msg == "Nothing to undo",
      "Undo after ClearUndo reports 'Nothing to undo'", repr((u_msg, u_done)))
check(r_done is True and r_msg == "Nothing to redo",
      "Redo after ClearUndo reports 'Nothing to redo'", repr((r_msg, r_done)))

if failures:
    print(f"\nFAIL: {len(failures)}/{checks} checks failed: {failures}")
    sys.exit(1)

print(f"\n{checks}/{checks} ClearUndo checks passed")
