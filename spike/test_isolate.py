"""the `Isolate` / `Unisolate` commands.

=============================================================================
What this pins
=============================================================================

`Isolate` (commands/hide.htm#Isolate, MEASURED):
    "The Isolate command hides all objects except the currently selected and
    locked objects."

`Unisolate` (commands/hide.htm#Unisolate, MEASURED):
    "The Unisolate command shows all objects hidden by the Isolate command."

The load-bearing difference from `Show` / `ShowSelected`: `Unisolate` restores
EXACTLY the set of names that `Isolate` hid, which `Isolate` records in the
pure `isolate_state` module (keyed by the model). The document alone cannot
tell Isolate-hidden objects apart from manually-hidden ones (both carry
`ObjectMode.Hidden`), so the set is remembered separately. This test pins:

  * both commands are registered, have no steps, and record an undo step;
  * `Isolate` hides every object that is neither selected nor locked, and
    leaves the selected + locked ones visible;
  * `Isolate` with nothing to hide changes nothing and leaves no undo record;
  * `Unisolate` shows EXACTLY the set `Isolate` hid, and does NOT show a
    separately-hidden object;
  * `Unisolate` with no prior `Isolate` refuses by name (rule 4) and changes
    nothing;
  * a successful `Isolate` is one "Isolate" undo step: undo restores the
    pre-isolate Modes, redo re-hides the set.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_isolate.py
"""
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Bare `narwhal3d` package so `from . import x` in commands.py resolves
# without running __init__.py (which imports ui -> bpy). Same trick as
# spike/test_rename.py.
_pkg = types.ModuleType("narwhal3d")
_pkg.__path__ = [ADDON]
_pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = _pkg

_load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
undo = _load("narwhal3d.undo", "narwhal3d/undo.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
r3 = bridge.r3


# ---------------------------------------------------------------------------
# a headless stand-in for NarwhalContext (no bpy)
# ---------------------------------------------------------------------------
class FakeObj:
    def __init__(self, name):
        self.name = name


class FakeCtx:
    """A NarwhalContext stand-in, driven headlessly (no bpy).

    Carries a real :class:`undo.UndoStack` and a real :class:`NarwhalModel`, and
    mirrors the real ``NarwhalContext.note_change`` / ``undo_last`` / ``redo_last``
    so the undo/redo path is exercised for real.
    """

    def __init__(self):
        self.model = model.NarwhalModel()
        self.undo = undo.UndoStack()
        self._selected = []

    def select(self, *names):
        self._selected.extend(names)

    def selected_objects(self):
        return [FakeObj(n) for n in self._selected]

    def object_by_name(self, name):
        return None

    def add_object(self, geom, name, layer_index=0):
        self.model.add_object(geom, name, layer_index)
        return name

    def mode_of(self, name):
        for fobj in self.model.file.Objects:
            if (fobj.Attributes.Name or "").lower() == name.lower():
                return fobj.Attributes.Mode
        return None

    def begin_undo(self, name):
        return self.undo.begin(name)

    def end_undo(self):
        return self.undo.end()

    def note_change(self, name=None):
        if not self.undo.enabled:
            return False
        implicit = not self.undo.recording
        if implicit:
            self.undo.begin(name or "Change")
        captured = self.undo.capture(self.model.snapshot())
        if implicit:
            self.undo.end()
        return captured

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


def new_isolate():
    assert hasattr(commands, "IsolateCmd"), "no IsolateCmd in commands.py"
    return commands.IsolateCmd()


def new_unisolate():
    assert hasattr(commands, "UnisolateCmd"), "no UnisolateCmd in commands.py"
    return commands.UnisolateCmd()


# ---------------------------------------------------------------------------
results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


def _ctx_with(names, locked=(), selected=()):
    """A fresh FakeCtx with `names` as Normal lines; `locked` set to Locked;
    the `selected` names put into the selection."""
    ctx = FakeCtx()
    for i, n in enumerate(names):
        ctx.add_object(bridge.make_line((i, 0, 0), (i + 1, 0, 0)), n)
    for n in locked:
        for fobj in ctx.model.file.Objects:
            if (fobj.Attributes.Name or "").lower() == n.lower():
                fobj.Attributes.Mode = r3.ObjectMode.Locked
    for n in selected:
        ctx.select(n)
    return ctx


# ===========================================================================
# DECLARATION / FIDELITY
# ===========================================================================
print("Isolate.declare")


@check("Isolate is registered under the name 'isolate'")
def _():
    assert "isolate" in commands.COMMANDS, (
        f"registry missing 'isolate'; has {[k for k in commands.COMMANDS if 'isolate' in k or 'isolat' in k]}")


@check("Unisolate is registered under the name 'unisolate'")
def _():
    assert "unisolate" in commands.COMMANDS, (
        f"registry missing 'unisolate'; has {[k for k in commands.COMMANDS if 'unisolat' in k]}")


@check("Isolate asks 'Select objects to isolate:' (MEASURED Research13 #77)")
def _():
    assert [(p, k) for p, k in commands.IsolateCmd.steps] == [
        ("Select objects to isolate:", commands.OBJECTS_MULTI)], (
        f"Isolate must ask for the objects to keep, got {commands.IsolateCmd.steps}")
    assert commands.IsolateCmd.preselect_names == 1
    assert commands.IsolateCmd.cancel_on_empty_set is True


@check("Isolate with NOTHING selected prompts and hides nothing (MEASURED Research13 #77)")
def _():
    ctx = _ctx_with(["A", "B", "C"])
    cmd = new_isolate()
    msg, done = cmd.begin(ctx)
    assert done is False, (msg, "with no selection the reference CAD application prompts instead of hiding")
    assert cmd.prompt.startswith("Select objects to isolate"), cmd.prompt
    assert all(ctx.mode_of(n) == r3.ObjectMode.Normal for n in "ABC"), \
        "nothing may be hidden before the user selects"
    msg, done = cmd.feed("", ctx)
    assert done is True and "cancelled" in (msg or "").lower(), (msg, done)
    assert all(ctx.mode_of(n) == r3.ObjectMode.Normal for n in "ABC"), \
        "an empty Enter cancels with nothing hidden"
    assert ctx.undo.undo_names() == [], "a cancelled Isolate leaves no undo record"


@check("Isolate.execute with no selection never hides everything (the old defect)")
def _():
    ctx = _ctx_with(["A", "B"])
    msg, done = new_isolate().execute(ctx)
    assert done is True and "cancelled" in (msg or "").lower(), (msg, done)
    assert all(ctx.mode_of(n) == r3.ObjectMode.Normal for n in "AB"), \
        "a direct execute with nothing selected must not hide every object"


@check("Unisolate is a zero-step command (it acts on the recorded set)")
def _():
    assert commands.UnisolateCmd.steps == [], (
        f"Unisolate should have no steps, got {commands.UnisolateCmd.steps}")


@check("Isolate records an undo step (a Mode change is a document change)")
def _():
    assert commands.IsolateCmd.records_undo is True


@check("Unisolate records an undo step (a Mode change is a document change)")
def _():
    assert commands.UnisolateCmd.records_undo is True


# ===========================================================================
# ISOLATE: hide everything not selected (and not locked)
# ===========================================================================
print("Isolate.build")


@check("Isolate hides every object that is not selected")
def _():
    ctx = _ctx_with(["A", "B", "C"], selected=["A"])
    msg, done = new_isolate().begin(ctx)
    assert done is True, (msg, "Isolate should run and finish")
    assert ctx.mode_of("A") == r3.ObjectMode.Normal, "the selected object stays visible"
    assert ctx.mode_of("B") == r3.ObjectMode.Hidden, "an unselected object is hidden"
    assert ctx.mode_of("C") == r3.ObjectMode.Hidden, "an unselected object is hidden"
    assert "hid" in (msg or "").lower(), f"the message should report a hide, got {msg!r}"


@check("Isolate leaves LOCKED objects visible (MEASURED: except selected and locked)")
def _():
    ctx = _ctx_with(["A", "B", "L"], locked=["L"], selected=["A"])
    msg, done = new_isolate().begin(ctx)
    assert done is True, (msg, "Isolate should run and finish")
    assert ctx.mode_of("A") == r3.ObjectMode.Normal, "the selected object stays visible"
    assert ctx.mode_of("L") == r3.ObjectMode.Locked, "a locked object stays visible"
    assert ctx.mode_of("B") == r3.ObjectMode.Hidden, "an unselected non-locked object is hidden"


@check("Isolate with nothing to hide changes nothing and leaves no undo record")
def _():
    ctx = _ctx_with(["A"], selected=["A"])
    msg, done = new_isolate().begin(ctx)
    assert done is True, (msg, "Isolate should finish (even with nothing to hide)")
    assert "nothing to hide" in (msg or "").lower(), (
        f"must say there is nothing to hide, got {msg!r}")
    assert ctx.mode_of("A") == r3.ObjectMode.Normal, "the object must be untouched"
    assert ctx.undo.undo_names() == [], "a no-op Isolate must leave no undo record"


@check("a successful Isolate yields exactly ONE 'Isolate' undo step")
def _():
    ctx = _ctx_with(["A", "B", "C"], selected=["A"])
    new_isolate().begin(ctx)
    assert ctx.undo.undo_names() == ["Isolate"], (
        f"one Isolate must be a single 'Isolate' undo step, got {ctx.undo.undo_names()}")


@check("undo restores the pre-Isolate Modes; redo re-hides the set")
def _():
    ctx = _ctx_with(["A", "B", "C"], selected=["A"])
    new_isolate().begin(ctx)
    assert ctx.mode_of("B") == r3.ObjectMode.Hidden
    name = ctx.undo_last()
    assert name == "Isolate", f"top undo step should be 'Isolate', got {name!r}"
    assert ctx.mode_of("B") == r3.ObjectMode.Normal, "undo must restore B to visible"
    assert ctx.mode_of("C") == r3.ObjectMode.Normal, "undo must restore C to visible"
    name = ctx.redo_last()
    assert name == "Isolate", f"redo should re-apply 'Isolate', got {name!r}"
    assert ctx.mode_of("B") == r3.ObjectMode.Hidden, "redo must re-hide B"
    assert ctx.mode_of("C") == r3.ObjectMode.Hidden, "redo must re-hide C"


# ===========================================================================
# UNISOLATE: show EXACTLY the set Isolate hid
# ===========================================================================
print("Unisolate.build")


@check("Unisolate restores exactly the set Isolate hid")
def _():
    ctx = _ctx_with(["A", "B", "C"], selected=["A"])
    new_isolate().begin(ctx)          # hides B, C; records {B, C}
    assert ctx.mode_of("B") == r3.ObjectMode.Hidden
    assert ctx.mode_of("C") == r3.ObjectMode.Hidden
    msg, done = new_unisolate().begin(ctx)
    assert done is True, (msg, "Unisolate should run and finish")
    assert ctx.mode_of("B") == r3.ObjectMode.Normal, "Unisolate must show B"
    assert ctx.mode_of("C") == r3.ObjectMode.Normal, "Unisolate must show C"
    assert ctx.mode_of("A") == r3.ObjectMode.Normal, "A was never hidden"
    # MEASURED (research_all_13 #179): Unisolate reports in Show's words.
    assert msg == "Showing 2 hidden objects.", f"the message should report a show, got {msg!r}"


@check("Unisolate does NOT show a separately-hidden object (only Isolate's set)")
def _():
    ctx = _ctx_with(["A", "B", "C"], selected=["A"])
    new_isolate().begin(ctx)          # records {B, C}
    # A NEW object is added and hidden by a separate (manual) Hide -- it is NOT
    # part of Isolate's set, so Unisolate must leave it hidden.
    ctx.add_object(bridge.make_line((9, 0, 0), (10, 0, 0)), "E")
    for fobj in ctx.model.file.Objects:
        if (fobj.Attributes.Name or "").lower() == "e":
            fobj.Attributes.Mode = r3.ObjectMode.Hidden
    new_unisolate().begin(ctx)
    assert ctx.mode_of("B") == r3.ObjectMode.Normal, "Unisolate must show Isolate's B"
    assert ctx.mode_of("C") == r3.ObjectMode.Normal, "Unisolate must show Isolate's C"
    assert ctx.mode_of("E") == r3.ObjectMode.Hidden, (
        "a separately-hidden object must stay hidden -- Unisolate is not 'show all'")


@check("Unisolate consumes the record (a second run finds nothing to restore)")
def _():
    ctx = _ctx_with(["A", "B"], selected=["A"])
    new_isolate().begin(ctx)          # hides B; records {B}
    new_unisolate().begin(ctx)        # consumes the record
    msg, done = new_unisolate().begin(ctx)
    assert done is True, (msg, "a repeat Unisolate should finish (not loop)")
    assert "not run first" in (msg or "").lower() or "nothing to restore" in (msg or "").lower(), (
        f"a repeat Unisolate must refuse, got {msg!r}")
    assert ctx.mode_of("B") == r3.ObjectMode.Normal, "B stays visible (already restored)"


@check("Unisolate with no prior Isolate refuses and changes nothing")
def _():
    ctx = _ctx_with(["A", "B"])
    for fobj in ctx.model.file.Objects:
        if (fobj.Attributes.Name or "").lower() == "b":
            fobj.Attributes.Mode = r3.ObjectMode.Hidden    # B hidden by a manual Hide
    msg, done = new_unisolate().begin(ctx)
    assert done is True, (msg, "a refusal should finish the command")
    assert "not run first" in (msg or "").lower() or "nothing to restore" in (msg or "").lower(), (
        f"must refuse when Isolate never ran, got {msg!r}")
    assert ctx.mode_of("B") == r3.ObjectMode.Hidden, (
        "a refused Unisolate must leave the document untouched")
    assert ctx.mode_of("A") == r3.ObjectMode.Normal, "A is untouched"
    assert ctx.undo.undo_names() == [], "a refused Unisolate must leave no undo record"


@check("a successful Unisolate yields exactly ONE 'Unisolate' undo step")
def _():
    ctx = _ctx_with(["A", "B", "C"], selected=["A"])
    new_isolate().begin(ctx)
    new_unisolate().begin(ctx)
    names = ctx.undo.undo_names()     # most recent first
    assert names[0] == "Unisolate", (
        f"the top undo step should be 'Unisolate', got {names}")
    assert "Isolate" in names, "the earlier Isolate step is still on the stack"


# ===========================================================================
failed = 0
print()
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
