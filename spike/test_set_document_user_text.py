#!/usr/bin/env python3
"""SetDocumentUserText against the File3dm.Strings compatibility interface."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = pkg


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


for module_name in ["cplane", "bridge", "gumball", "osnap", "coords", "options",
                    "undo", "sel_commands", "divide", "project", "extend",
                    "named_cplanes", "isolate_state", "selection_state",
                    "property_select", "user_text"]:
    load("narwhal3d." + module_name, "narwhal3d/" + module_name + ".py")
bridge = sys.modules["narwhal3d.bridge"]
model = load("narwhal3d.model", "narwhal3d/model.py")
commands = load("narwhal3d.commands", "narwhal3d/commands.py")

checks = 0


def check(condition, label):
    global checks
    checks += 1
    if not condition:
        raise AssertionError(label)
    print("  ok  " + label)


class Ctx:
    def __init__(self):
        self.model = model.NarwhalModel()
        self.undo = commands.undo.UndoStack()

    def note_change(self, name=None):
        if not self.undo.recording:
            self.undo.begin(name or "Change")
            self.undo.capture(self.model.snapshot())
            self.undo.end()
            return True
        return self.undo.capture(self.model.snapshot())


def drive(ctx, lines):
    cmd = commands.SetDocumentUserTextCmd()
    cmd.begin(ctx)
    result = None
    for line in lines:
        result = cmd.feed(line, ctx)
        if result[1]:
            break
    return result


check("setdocumentusertext" in commands.COMMANDS,
      "SetDocumentUserText is registered")
ctx = Ctx()
msg, done = drive(ctx, ["Material", "Brass"])
check(done and ctx.model.file.Strings["Material"] == "Brass",
      "the first key/value pair is written to native File3dm.Strings")
check(list(ctx.model.file.Strings) == [("Material", "Brass")],
      "the document table has one ordered row")

msg, done = drive(ctx, [".hidden", "secret"])
check(done and list(ctx.model.file.Strings) == [("Material", "Brass"),
                                                 (".hidden", "secret")],
      "period-prefixed hidden keys remain in the native table")

msg, done = drive(ctx, ["Material", '""'])
check(done and list(ctx.model.file.Strings) == [(".hidden", "secret")],
      "the literal two quotes delete an existing key")
msg, done = drive(ctx, ["Missing", '""'])
check(done and list(ctx.model.file.Strings) == [(".hidden", "secret")]
      and "unchanged" in msg.lower(),
      "deleting a missing key is a no-op without an undo change")

cmd = commands.SetDocumentUserTextCmd()
cmd.begin(ctx)
msg, done = cmd.feed("", ctx)
check(done and "cancelled" in msg.lower(),
      "bare Enter at the first key prompt cancels")
cmd = commands.SetDocumentUserTextCmd()
cmd.begin(ctx)
cmd.feed("New", ctx)
msg, done = cmd.feed("", ctx)
check(done and "cancelled" in msg.lower()
      and list(ctx.model.file.Strings) == [(".hidden", "secret")],
      "bare Enter at the value prompt cancels without writing")

with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, "document-text.3dm")
    check(ctx.model.save(path), "the document saves")
    fresh = r3.File3dm.Read(path)
    check(list(fresh.Strings) == [(".hidden", "secret")],
          "native document strings survive File3dm save/reload")
    probe = subprocess.run([
        sys.executable, "-c",
        "import rhino3dm,sys; f=rhino3dm.File3dm.Read(sys.argv[1]); "
        "print(list(f.Strings))", path], capture_output=True, text=True, check=True)
    check(".hidden" in probe.stdout and "secret" in probe.stdout,
          "a fresh process reads the persisted hidden key")

undo_ctx = Ctx()
msg, done = drive(undo_ctx, ["A", "one"])
msg, done = drive(undo_ctx, [".B", "two"])
check(list(undo_ctx.model.file.Strings) == [("A", "one"), (".B", "two")],
      "two independent sets preserve order")
record = undo_ctx.undo.undo(undo_ctx.model.snapshot())
check(record is not None and record.name == "SetDocumentUserText",
      "each set creates one named undo record")
if record is not None:
    undo_ctx.model.restore(record.before)
check(list(undo_ctx.model.file.Strings) == [("A", "one")],
      "undo restores the complete prior document string table")
if record is not None:
    redo = undo_ctx.undo.redo(undo_ctx.model.snapshot())
    if redo is not None:
        undo_ctx.model.restore(redo.after)
check(list(undo_ctx.model.file.Strings) == [("A", "one"), (".B", "two")],
      "redo restores the hidden key and its order")

print(f"SET DOCUMENT USER TEXT PASSED ({checks} checks)")
