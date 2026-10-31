#!/usr/bin/env python3
"""GetDocumentUserText reads the authoritative document metadata."""
import importlib.util
import os
import sys
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
load("narwhal3d.model", "narwhal3d/model.py")
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
        self.model = sys.modules["narwhal3d.model"].NarwhalModel()
        self.undo = commands.undo.UndoStack()

    def note_change(self, name=None):
        raise AssertionError("read-only query attempted to record an undo change")


def drive(ctx, lines):
    cmd = commands.GetDocumentUserTextCmd()
    cmd.begin(ctx)
    result = None
    for line in lines:
        result = cmd.feed(line, ctx)
        if result[1]:
            break
    return result


check("getdocumentusertext" in commands.COMMANDS,
      "GetDocumentUserText is registered")
ctx = Ctx()
ctx.model.file.Strings["Material"] = "Brass"
ctx.model.file.Strings[".hidden"] = "secret"

msg, done = drive(ctx, ["Material"])
check(done and msg == "GetDocumentUserText: Material = Brass",
      "an explicit key returns the exact native value")
msg, done = drive(ctx, [".hidden"])
check(done and msg == "GetDocumentUserText: .hidden = secret",
      "an explicit hidden key is queryable")

msg, done = drive(ctx, [""])
check(done and msg == "Material = Brass\n.hidden = secret",
      "Enter lists all native rows, including hidden keys")

msg, done = drive(ctx, ["Missing"])
check(done and msg == "GetDocumentUserText: Missing not found",
      "a missing explicit key has the exact missing-key output")

empty = Ctx()
msg, done = drive(empty, [""])
check(done and msg == "GetDocumentUserText: document has no user text",
      "all-keys on an empty document has the exact empty output")

check(commands.GetDocumentUserTextCmd.records_undo is False,
      "the read-only query never records undo")
check(list(ctx.model.file.Strings) == [("Material", "Brass"), (".hidden", "secret")],
      "querying leaves native File3dm.Strings unchanged")

# Mutation guard: replacing the all-rows source with a panel-style filter must
# make the hidden-key assertion above fail, rather than letting the test pass.
assert ".hidden = secret" in drive(ctx, [""])[0]

print(f"GET DOCUMENT USER TEXT PASSED ({checks} checks)")
