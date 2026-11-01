"""Research13 #76/#134: SelAll and Invert never select hidden or locked objects.

MEASURED (docs/measured/research_all_13/notes/076-Invert.md, 134-SelAll.md): with a line
hidden, `Invert` on an empty selection selected only the two visible lines, and with a
visible line selected it added only the other visible line; `SelAll` never counted hidden
or locked objects. The app used to build its candidate list from EVERY named document
object, so it would have selected hidden and locked ones too.

Headless: a stand-in `narwhal3d.ui` records what the command asks to select.

Run: tools/blender-patched/5.2/python/bin/python3.13 spike/test_research13_selection.py
"""
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")

_pkg = types.ModuleType("narwhal3d")
_pkg.__path__ = [ADDON]
_pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = _pkg


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
_load("narwhal3d.undo", "narwhal3d/undo.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
r3 = bridge.r3

_applied = []
_ui = types.ModuleType("narwhal3d.ui")
_ui.apply_selection = lambda names: (_applied.append(list(names)) or list(names))
sys.modules["narwhal3d.ui"] = _ui
_pkg.ui = _ui


class Obj:
    def __init__(self, name):
        self.name = name


class Ctx:
    def __init__(self):
        self.model = model.NarwhalModel()
        self.selected = []

    def add(self, name, y, mode=None):
        self.model.add_object(bridge.make_line((0, y, 0), (4, y, 0)), name, 0)
        if mode is not None:
            for fobj in self.model.file.Objects:
                if fobj.Attributes.Name == name:
                    fobj.Attributes.Mode = mode

    def selected_objects(self):
        return [Obj(n) for n in self.selected]

    def select_by_names(self, names):
        self.selected = list(names)
        return self.selected

    def geom_by_name(self, name):
        for fobj in self.model.file.Objects:
            if fobj.Attributes.Name == name:
                return fobj.Geometry
        return None


checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(("  ok   " if ok else "  FAIL ") + label)


def run(key, ctx):
    _applied.clear()
    cmd = commands.COMMANDS[key].__class__()
    msg, done = cmd.begin(ctx)
    return set(_applied[-1]) if _applied else None, done


def fixture():
    ctx = Ctx()
    ctx.add("L1", 0)
    ctx.add("L2", 1)
    ctx.add("H", 2, r3.ObjectMode.Hidden)
    ctx.add("K", 3, r3.ObjectMode.Locked)
    return ctx


ctx = fixture()
got, done = run("selall", ctx)
check(done and got == {"L1", "L2"}, f"SelAll selects only visible, unlocked objects (got {got})")

ctx = fixture()
got, done = run("invert", ctx)
check(done and got == {"L1", "L2"},
      f"Invert of an empty selection: the visible objects only, never H or K (got {got})")

ctx = fixture()
ctx.selected = ["L2"]
got, done = run("invert", ctx)
check(done and got == {"L1"}, f"Invert with L2 selected adds only L1 (got {got})")

ctx = fixture()
ctx.selected = ["H"]          # the selection survives a Hide in the app
got, done = run("invert", ctx)
check(done and got == {"L1", "L2"},
      f"Invert drops a selected hidden object and never adds it back (got {got})")

# MEASURED (note 138): `_Select` -> "Select objects:"; typed `All` -> "3 curves added to
# selection." and the prompt stays open; Enter ends it.
ctx = fixture()
ctx.add("L3", 4)
cmd = commands.COMMANDS["select"].__class__()
msg, done = cmd.begin(ctx)
check(not done and cmd.prompt == "Select objects:", f"Select prompts {cmd.prompt!r}")
msg, done = cmd.feed("All", ctx)
check(not done and msg == "3 curves added to selection."
      and set(ctx.selected) == {"L1", "L2", "L3"},
      f"All: the 3 selectable curves, prompt still open ({msg!r}, {ctx.selected})")
msg, done = cmd.feed("", ctx)
check(done and set(ctx.selected) == {"L1", "L2", "L3"}, "Enter ends Select, selection kept")

ctx = fixture()
ctx.selected = ["K"]
cmd = commands.COMMANDS["select"].__class__()
cmd.begin(ctx)
check(ctx.selected == [], "Select starts from an empty selection (the app's earlier behaviour)")
msg, done = cmd.feed("L1", ctx)
msg, done = cmd.feed("L2", ctx)
check(not done and ctx.selected == ["L1", "L2"] and msg == "1 curve added to selection.",
      f"each typed name ADDS to the selection ({msg!r}, {ctx.selected})")

failed = [label for ok, label in checks if not ok]
print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
