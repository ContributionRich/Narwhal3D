#!/usr/bin/env python3
"""#114: ProjectToCPlane's delete question -- a toggle the user can click, not a Yes/No to type.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_project_to_cplane_delete.py

OURS: the button behaviour (a click flips DeleteInput and shows it, nothing runs) is our
own design. The only measured facts are the prompt text "Delete input objects? <No>
( No  Yes ):" (research21 probe) and that Enter keeps the inputs by default, both pinned
by spike/test_research13_commands.py. The reference docs say only that you "choose to either
keep or delete the input objects" (reference ProjectToCPlane documentation).

Covered (headless, through a FakeCtx backed by a real NarwhalModel):
  * initial No and initial Yes prompts and bar labels;
  * a bare click (`DeleteInput`) toggles, stays on the question, and runs nothing;
  * clicks toggle back and forth; `DeleteInput=Yes/No` uses the standard option grammar;
  * Enter runs with the CURRENT value (Yes stays Yes, No stays No);
  * typed yes/no/y/n still confirm directly, as before;
  * invalid answers re-ask and change nothing;
  * one undo record per executed run, none for toggles alone;
  * the projection is still exact analytic geometry, not a mesh.
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


def _load(name, relpath):
    path = os.path.join(ROOT, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cplane = _load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
project = _load("narwhal3d.project", "narwhal3d/project.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
picture = _load("narwhal3d.picture", "narwhal3d/picture.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")

PROMPT_NO = "Delete input objects? <No> ( No  Yes ):"
PROMPT_YES = "Delete input objects? <Yes> ( No  Yes ):"
LABEL_NO = "Delete input objects? <No>:"
LABEL_YES = "Delete input objects? <Yes>:"

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


class FakeObj:
    def __init__(self, name):
        self.name = name


class FakeCtx:
    """An application-context stand-in: a real NarwhalModel, plus an undo recorder.

    The undo recorder mirrors the documented contract of `begin_undo`/`end_undo`/
    `note_change` (a record is kept only if something was captured). The real
    stack is exercised by the GUI suite, not here.
    """

    def __init__(self, plane):
        self.model = model.NarwhalModel()
        self._plane = plane
        self._selected = []
        self.added = []
        self.depth = 0
        self.frame = None
        self.kept = []

    def active_cplane(self):
        return self._plane

    def add_input(self, geom, name):
        obj_id, _attrs = self.model.add_object(geom, name, 0)
        commands._assign_picture_material(self.model, obj_id, geom)
        self._selected.append(name)
        return name

    def selected_objects(self):
        return [FakeObj(n) for n in self._selected]

    def geom_by_name(self, name):
        for obj in self.model.file.Objects:
            if (obj.Attributes.Name or "").lower() == name.lower():
                return obj.Geometry
        return None

    def object_by_name(self, name):
        return None

    def begin_undo(self, name):
        self.depth += 1
        if self.depth == 1:
            self.frame = {"name": name, "captured": False}

    def end_undo(self):
        self.depth -= 1
        if self.depth == 0:
            if self.frame["captured"]:
                self.kept.append(self.frame["name"])
            self.frame = None

    def note_change(self, label=None):
        if self.frame is not None:
            self.frame["captured"] = True

    def add_geometry(self, geom, base_name, layer_index=0):
        display = f"{base_name}{len(self.added) + 1:02d}"
        obj_id, _attrs = self.model.add_object(geom, display, layer_index)
        commands._assign_picture_material(self.model, obj_id, geom)
        self.added.append((geom, base_name, int(layer_index), display))
        return obj_id, display


def make_ctx():
    ctx = FakeCtx(cplane.WORLD_TOP)
    ctx.add_input(bridge.make_line((0, 0, 5), (3, 2, 7)), "CurveA")
    return ctx


def fresh(ctx, opt=None):
    """A ProjectToCPlane with CurveA preselected, on the delete question."""
    c = type(commands.COMMANDS["projecttocplane"])()
    if opt is not None:
        c.opt["DeleteInput"] = opt
    c.begin(ctx)
    return c


def input_present(ctx):
    return ctx.model.object_id_by_name("CurveA") is not None


print("initial state (preselection puts the command on the delete question)")
ctx = make_ctx()
c = fresh(ctx)
check(c.step_index == 1, "preselection skips straight to the delete question")
check(c.prompt == PROMPT_NO, f"initial prompt is the measured No prompt ({c.prompt!r})")
check(c.steps[1][0] == LABEL_NO, f"the bar label starts at No ({c.steps[1][0]!r})")
check(c.opt["DeleteInput"] is False, "DeleteInput starts No")

ctx = make_ctx()
c = fresh(ctx, opt=True)
check(c.prompt == PROMPT_YES, f"an initial Yes shows <Yes> in the prompt ({c.prompt!r})")
check(c.steps[1][0] == LABEL_YES, f"an initial Yes shows <Yes> in the bar ({c.steps[1][0]!r})")

print("a bare click toggles and shows the state, and runs nothing")
ctx = make_ctx()
c = fresh(ctx)
msg, done = c.feed("DeleteInput", ctx)
check(done is False, f"a click does not finish the command ({msg!r})")
check(c.opt["DeleteInput"] is True, "a click on No turns DeleteInput Yes")
check(c.prompt == PROMPT_YES and c.steps[1][0] == LABEL_YES,
      f"the question now shows Yes ({c.prompt!r}, {c.steps[1][0]!r})")
check(ctx.added == [] and input_present(ctx), "a click executes nothing; the input is kept")
check(msg == "DeleteInput=Yes", f"the click reports the new state ({msg!r})")
msg, done = c.feed("DeleteInput", ctx)
check(done is False and c.opt["DeleteInput"] is False and c.prompt == PROMPT_NO,
      f"a second click toggles back to No and stays on the question ({msg!r})")
check(ctx.added == [] and input_present(ctx), "the second click also runs nothing")

print("DeleteInput=Yes / DeleteInput=No use the standard option grammar")
ctx = make_ctx()
c = fresh(ctx)
msg, done = c.feed("DeleteInput=Yes", ctx)
check(done is False and c.opt["DeleteInput"] is True and c.steps[1][0] == LABEL_YES,
      f"DeleteInput=Yes sets Yes without running ({msg!r})")
c.opt["DeleteInput"] = True
msg, done = c.feed("DeleteInput=No", ctx)
check(done is False and c.opt["DeleteInput"] is False and not ctx.added,
      f"DeleteInput=No sets No from Yes, without running ({msg!r})")
msg, done = c.feed("deleteinput=yes", ctx)
check(done is False and c.opt["DeleteInput"] is True,
      f"the option name is case-insensitive ({msg!r})")
check(ctx.added == [] and input_present(ctx), "option-grammar lines run nothing")

print("Enter runs with the CURRENT value")
ctx = make_ctx()
c = fresh(ctx)
c.feed("DeleteInput", ctx)
msg, done = c.feed("", ctx)
check(done is True and len(ctx.added) == 1 and not input_present(ctx) and "inputs deleted" in msg,
      f"click to Yes, then Enter: runs and deletes the input ({msg!r})")

ctx = make_ctx()
c = fresh(ctx)
msg, done = c.feed("", ctx)
check(done is True and len(ctx.added) == 1 and input_present(ctx) and "inputs kept" in msg,
      f"no click, Enter: runs and keeps the input, the measured default ({msg!r})")

ctx = make_ctx()
c = fresh(ctx)
c.feed("DeleteInput", ctx)
c.feed("DeleteInput", ctx)
msg, done = c.feed("", ctx)
check(done is True and input_present(ctx) and "inputs kept" in msg,
      f"click twice back to No, then Enter: keeps the input ({msg!r})")

ctx = make_ctx()
c = fresh(ctx, opt=True)
msg, done = c.feed("", ctx)
check(done is True and not input_present(ctx) and "inputs deleted" in msg,
      f"an initial Yes and Enter: deletes, it does not reset to No ({msg!r})")

print("typed yes/no still confirm directly")
for text, deleted in (("yes", True), ("Yes", True), ("y", True), ("no", False),
                      ("NO", False), ("n", False)):
    ctx = make_ctx()
    c = fresh(ctx)
    msg, done = c.feed(text, ctx)
    check(done is True and len(ctx.added) == 1 and (not input_present(ctx)) == deleted,
          f"typed {text!r} runs at once, deleting={deleted} ({msg!r})")

ctx = make_ctx()
c = fresh(ctx)
c.feed("DeleteInput=Yes", ctx)
msg, done = c.feed("yes", ctx)
check(done is True and not input_present(ctx),
      f"Yes typed after a click confirms it ({msg!r})")

print("invalid answers re-ask and change nothing")
ctx = make_ctx()
c = fresh(ctx)
msg, done = c.feed("maybe", ctx)
check(done is False and msg == "Please answer No or Yes" and c.opt["DeleteInput"] is False
      and ctx.added == [] and input_present(ctx), f"a non-answer re-asks ({msg!r})")
msg, done = c.feed("DeleteInput=maybe", ctx)
check(done is False and c.opt["DeleteInput"] is False and ctx.added == [] and input_present(ctx)
      and "Yes or No" in (msg or ""), f"DeleteInput=maybe is refused by name ({msg!r})")
c.feed("DeleteInput", ctx)
msg, done = c.feed("maybe", ctx)
check(done is False and c.opt["DeleteInput"] is True and ctx.added == [],
      "a bad word after a click keeps the clicked state")

print("one undo record per run; toggles alone record nothing")
ctx = make_ctx()
c = fresh(ctx)
c.feed("DeleteInput", ctx)
c.feed("DeleteInput", ctx)
c.feed("maybe", ctx)
check(ctx.kept == [], f"toggles and refusals leave no undo record ({ctx.kept})")
c.feed("DeleteInput", ctx)
c.feed("", ctx)
check(ctx.kept == ["ProjectToCPlane"], f"a click then Enter keeps exactly one record ({ctx.kept})")

print("the projection is exact analytic geometry")
ctx = make_ctx()
c = fresh(ctx)
c.feed("DeleteInput", ctx)
c.feed("", ctx)
geom = ctx.added[0][0] if ctx.added else None
check(isinstance(geom, r3.Curve) and not isinstance(geom, r3.Mesh),
      f"the projected curve is a NURBS/line curve, not a mesh ({type(geom).__name__})")
if isinstance(geom, r3.Curve):
    ends = (geom.PointAtStart, geom.PointAtEnd)
    check(all(abs(p.Z) < 1e-9 for p in ends), "its ends lie in the Top CPlane (z = 0)")

print()
print("=" * 60)
if failures:
    print(f"FAILED {len(failures)} of {checks} checks:")
    for label in failures:
        print("  -", label)
    sys.exit(1)
print(f"PROJECT-TO-CPLANE DELETE TESTS PASSED ({checks} checks)")
