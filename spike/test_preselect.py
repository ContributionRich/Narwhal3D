#!/usr/bin/env python3
"""T-105: a command's first input should take the selection, or let you make one.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_preselect.py

Owner-reported: "when running extrude ... i must have my object selected first
right now. the way it should work is for all commands, the first input they
require will be auto filled if already selected, or can be selected after, and
confirmed with enter or right click."

`preselect_names` only covered the FIRST half (consume an existing selection).
The second half -- nothing selected -> the command PROMPTS, you select, and
Enter confirms -- was missing, because an empty answer at a fixed prompt CANCELS
the command (a load-bearing rule for the other step kinds). T-105 adds a new
step kind, `objects`, with its own branch in `Command._feed`, so that rule is
untouched for every other step.

Covered (per the task):
  * with a pre-selection, the step is satisfied and no prompt appears;
  * with none, the prompt appears and Enter after selecting proceeds;
  * Enter with nothing selected reports it and does NOT run;
  * Esc (the cancel path, `state.cancel`) still cancels;
  * a fixed first step (a point) command is UNCHANGED -- the guard against the
    global relaxation T-105 forbids;
  * and the owner's example, `ExtrudeCrv`, driven end to end.

`ExtrudeCrv` is the owner's example, so it is the command driven end to end
(it was the first command converted to an `objects` step. `Offset` now follows
the same click-after-start contract; `Revolve` retains its separate typed-name
behavior until converted and tested.

Loaded by file path (no Blender): `narwhal3d/__init__.py` imports `ui`, which
imports `bpy`, so the package cannot be imported outside a Blender process. Same
trick as spike/test_explode.py.
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


bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")
state = _load("narwhal3d.state", "narwhal3d/state.py")

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
    """A NarwhalContext stand-in for a selection-driven command, headless.

    Backed by a REAL NarwhalModel so `geom_by_name` and `add_geometry` behave as
    in the app. Only the bpy-touching surface is overridden: `selected_objects`
    returns plain name holders (and the selection is mutable mid-test, which is
    the whole point -- you select, then press Enter). `active_cplane` returns
    None so typed points read in world Top.
    """

    def __init__(self):
        self.model = model.NarwhalModel()
        self._selected = []          # display names, in selection order
        self.added = []              # (geom, base_name, layer_index, display)

    def add_input(self, geom, name, layer_index=0):
        self.model.add_object(geom, name, layer_index)
        return name

    def select(self, name):
        self._selected.append(name)

    def selected_objects(self):
        return [FakeObj(n) for n in self._selected]

    def select_by_names(self, names):
        """Replace the selection. F-17 needs this: an `objects_multi` step CLEARS the
        selection after confirming a set, so a two-set command's second prompt starts
        empty instead of silently re-taking the first set."""
        self._selected = [str(n) for n in names]
        return list(self._selected)

    def active_cplane(self):
        return None

    def geom_by_name(self, name):
        for obj in self.model.file.Objects:
            if (obj.Attributes.Name or "").lower() == name.lower():
                return obj.Geometry
        return None

    def add_geometry(self, geom, base_name, layer_index=0):
        display = f"{base_name}{len(self.added) + 1:02d}"
        self.model.add_object(geom, display, layer_index)
        self.added.append((geom, base_name, int(layer_index), display))
        return f"id-{len(self.added)}", display

    def note_change(self, label=None):
        pass

    def count_mode(self, mode):
        return sum(1 for f in self.model.file.Objects if f.Attributes.Mode == mode)

    def modes(self, name):
        out = []
        for f in self.model.file.Objects:
            if (f.Attributes.Name or "").lower() == (name or "").lower():
                out.append(f.Attributes.Mode)
        return out


def fresh(key):
    return type(commands.COMMANDS[key])()


# ===========================================================================
print()
print("ExtrudeCrv's first step is an `objects_multi` set step")
# ===========================================================================
cls = commands.COMMANDS["extrudecrv"]
first_kind = cls.steps[0][1] if cls.steps else None
check(first_kind == commands.OBJECTS_MULTI,
      f"{cls.name}: the curve step is an OBJECTS_MULTI step  (got {first_kind!r})")
# the old `name` step is gone from the curve position; preselect_names is
# no longer what fills it (the OBJECTS step does)
check(cls.steps[0][1] != "name",
      f"{cls.name}: the first step is not a typed `name` step")
# and its siblings, which keep the typed `name` step, are UNTOUCHED -- the
# conversion is the framework's job, applied to the owner's example, not a
# blanket rewrite (their existing suites pin the `name` step)
scls = commands.COMMANDS["revolve"]
check(scls.steps[0][1] == "name",
      f"{scls.name}: its unconverted curve step remains typed-name behavior")
check(commands.OffsetCmd.steps[0][1] == commands.OBJECTS,
      "Offset now accepts a clicked curve after the command starts")

# ===========================================================================
print()
print("pre-selection: the step is satisfied and no prompt appears")
# ===========================================================================
ctx = FakeCtx()
curve = bridge.make_circle((0, 0, 0), 5)
ctx.add_input(curve, "Circle01")
ctx.select("Circle01")

cmd = fresh("extrudecrv")
msg, done = cmd.begin(ctx)
check(cmd.step_index == 1,
      f"with a curve selected, the curve step is already satisfied "
      f"(step_index={cmd.step_index})")
check(not msg, f"no prompt is raised for the pre-selected curve  (got {msg!r})")
check(cmd.args and cmd.args[0] == ["Circle01"],
      f"the selected curve set filled args[0]  (args={cmd.args})")
# the command is now sitting on the Distance step, not the Curve step
check(cmd.step_index < len(cmd.steps) and cmd.steps[cmd.step_index][0] == "Extrusion distance:",
      f"it is now on the Distance step, not the Curve step")

# ===========================================================================
print()
print("nothing selected: Enter reports it and does NOT run")
# ===========================================================================
ctx = FakeCtx()
ctx.add_input(bridge.make_circle((0, 0, 0), 5), "Circle01")
# deliberately NOT selected
cmd = fresh("extrudecrv")
cmd.begin(ctx)
check(cmd.step_index == 0, "with nothing selected, the command stays on the Curve step")
msg, done = cmd.feed("", ctx)          # Enter
check(done is False,
      f"Enter with nothing selected does NOT finish the command  (done={done})")
check("nothing selected" in msg.lower(),
      f"it reports that nothing is selected  (got {msg!r})")
check(ctx.added == [], "no geometry was created")
check(cmd.args == [], "the curve step was not filled")

# ===========================================================================
print()
print("nothing selected, then select + Enter proceeds (the owner's flow)")
# ===========================================================================
ctx = FakeCtx()
ctx.add_input(bridge.make_circle((0, 0, 0), 5), "Circle01")
cmd = fresh("extrudecrv")
cmd.begin(ctx)
# first Enter: nothing selected -> report, stay
msg, done = cmd.feed("", ctx)
check(done is False and "nothing selected" in msg.lower(),
      f"first Enter reports nothing selected and stays  ({msg!r})")
# the user selects the curve (in the viewport), then presses Enter
ctx.select("Circle01")
msg, done = cmd.feed("", ctx)
check(cmd.step_index == 1,
      f"Enter after selecting satisfies the curve step  (step_index={cmd.step_index})")
check(cmd.args and cmd.args[0] == ["Circle01"],
      f"the newly selected curve set filled args[0]  (args={cmd.args})")
# now give the distance and it runs end to end
msg, done = cmd.feed("10", ctx)
check(done is True, f"the distance completes the command  (done={done})")
check(len(ctx.added) == 1,
      f"an extrusion was created  ({len(ctx.added)})")
check(ctx.added and isinstance(ctx.added[0][0], r3.Brep),
      "the result is a solid (Brep), as ExtrudeCrv promises")
check("created" in (msg or "").lower(), f"the message reports the creation  ({msg!r})")

# ===========================================================================
print()
print("a typed name is accepted: the selection is a shortcut, not a replacement")
# ===========================================================================
# The reference CAD application's object prompt takes a name OR the selection. spike/test_a2_draw.py and
# spike/addon_e2e.py both pin that naming the curve keeps working, so the OBJECTS
# step must NOT refuse typed text -- it takes it as a name and validates it when
# the command runs.
ctx = FakeCtx()
ctx.add_input(bridge.make_circle((0, 0, 0), 5), "Circle01")
# deliberately NOT selected: drive the command by typing the name
cmd = fresh("extrudecrv")
cmd.begin(ctx)
check(cmd.step_index == 0, "with nothing selected, the command is on the Curve step")
msg, done = cmd.feed("Circle01", ctx)      # Extrude's one-profile shortcut
check(done is False and cmd.args == [["Circle01"]] and cmd.step_index == 1,
      f"the typed profile advances to distance  (done={done}, args={cmd.args!r})")
msg, done = cmd.feed("10", ctx)            # distance
check(done is True, "the distance completes the command  (done={done})".format(done=done))
check(len(ctx.added) == 1, f"an extrusion was created  ({len(ctx.added)})")
check(ctx.added and isinstance(ctx.added[0][0], r3.Brep),
      "the result is a solid (Brep), as ExtrudeCrv promises")

# ===========================================================================
print()
print("the global cancel rule is UNCHANGED for a fixed (point) step")
# ===========================================================================
# `Line`'s first step is a `point`, not `objects`. An empty Enter there must
# STILL cancel -- this is the guard against T-105 relaxing the rule globally.
ctx = FakeCtx()
cmd = fresh("line")
msg, done = cmd.feed("", ctx)
check(done is True,
      f"an empty Enter on a point step still CANCELS the command  (done={done})")
check("cancelled" in msg.lower(), f"and it says so  (got {msg!r})")
# a second, more concrete check on a number step
ctx = FakeCtx()
ctx.add_input(bridge.make_circle((0, 0, 0), 5), "Circle01")
ctx.select("Circle01")
cmd = fresh("extrudecrv")
# MEASURED (research_preview_14 P36): Enter takes the sticky "Extrusion distance <d>"; with the
# first-use <0> there is nothing to extrude, so the empty Enter still cancels.
type(cmd)._sticky_values.pop("ExtrudeCrv", None)
cmd.begin(ctx)                       # pre-selects the curve, now on Distance:
check(cmd.step_index == 1, "premise: ExtrudeCrv is on its Distance (number) step")
msg, done = cmd.feed("", ctx)        # empty Enter on the number step
check(done is True and "cancelled" in msg.lower(),
      f"an empty Enter on the Distance (number) step still CANCELS  ({msg!r})")

# ===========================================================================
print()
print("Esc (the cancel path) still cancels a running objects-step command")
# ===========================================================================
# Esc is handled by `ui` / `state.cancel`, not by `feed` -- so this drives the
# real cancel path that the modal's ESC handler takes, and proves T-105 did not
# break it for the new step kind.
st = state.AppState()
ctx = FakeCtx()
ctx.add_input(bridge.make_circle((0, 0, 0), 5), "Circle01")
cmd = fresh("extrudecrv")
cmd.begin(ctx)
st.active_command = cmd
st.cancel()
check(st.active_command is None, "cancel clears the active command")
check(any("cancelled" in h.lower() for h in st.history),
      f"cancel is reported  (history={st.history})")
check(ctx.added == [], "a cancelled run created no geometry")

print()
print("=" * 60)
print("F-17: an `objects_multi` step -- \"Select the objects and press Enter\"")
print("=" * 60)
# OWNER-REPORTED: "behvaior of boolean union command isnt right. after i select and
# object and hit enter/ right click to confirm it just ends the command. more commands
# maybe have this issue."
#
# T-105's `objects` step takes `selected[0].name` -- ONE object -- so a command built
# on it silently ignores the rest of your selection, and Boolean (which wanted two
# TYPED NAMES) fell through to a prompt where empty Enter cancels. `objects_multi`
# takes the whole set, and Enter CONFIRMS it.
#
# MEASURED (commands/booleanunion.htm): "Select the objects and press Enter."


class _MultiCmd(commands.Command):
    """One objects_multi step, so the ENGINE is tested without needing bpy."""
    name = "OneSet"
    steps = [("Select objects:", commands.OBJECTS_MULTI)]
    preselect_names = 1
    records_undo = False

    def execute(self, ctx):
        ctx.ran = list(self.args)
        return "ran", True


class _TwoSetCmd(commands.Command):
    """Two objects_multi steps -- BooleanDifference's measured shape."""
    name = "TwoSets"
    steps = [("Set one:", commands.OBJECTS_MULTI),
             ("Set two:", commands.OBJECTS_MULTI)]
    preselect_names = 1
    records_undo = False

    def execute(self, ctx):
        ctx.ran = list(self.args)
        return "ran", True


# --- THE BUG: every selected object must be taken, not just the first --------
ctx = FakeCtx()
ctx.ran = None
for n in ("A", "B", "C"):
    ctx.add_input(bridge.make_circle((0, 0, 0), 1), n)
    ctx.select(n)
cmd = _MultiCmd()
msg, done = cmd.begin(ctx)
check(done and ctx.ran == [["A", "B", "C"]],
      "a preselection of THREE objects is taken WHOLE -- the single-object "
      f"`objects` step would have taken only 'A' (got {ctx.ran!r})")

# --- nothing selected: PROMPT, do not cancel, do not run --------------------
ctx = FakeCtx()
ctx.ran = None
ctx.add_input(bridge.make_circle((0, 0, 0), 1), "A")
cmd = _MultiCmd()
msg, done = cmd.begin(ctx)
check(not done and cmd.prompt == "Select objects:",
      f"with nothing selected the command STARTS and prompts (prompt={cmd.prompt!r})")
msg, done = cmd.feed("", ctx)
check(not done and ctx.ran is None and "Nothing selected" in (msg or ""),
      "Enter with nothing selected REPORTS it and stays running -- it does not "
      f"cancel and does not run (done={done}, msg={msg!r})")
# ...now select and press Enter, which is the owner's "confirmed with enter"
ctx.select("A")
msg, done = cmd.feed("", ctx)
check(done and ctx.ran == [["A"]],
      f"selecting then pressing Enter CONFIRMS and runs (ran={ctx.ran!r})")

# --- typed names ACCUMULATE until Enter -------------------------------------
ctx = FakeCtx()
ctx.ran = None
for n in ("A", "B"):
    ctx.add_input(bridge.make_circle((0, 0, 0), 1), n)
cmd = _MultiCmd()
cmd.begin(ctx)
msg, done = cmd.feed("A", ctx)
check(not done and ctx.ran is None,
      "a typed name does NOT run the command -- the set may have more members "
      f"(done={done})")
msg, done = cmd.feed("B", ctx)
check(not done and ctx.ran is None, "a second typed name accumulates too")
msg, done = cmd.feed("", ctx)
check(done and ctx.ran == [["A", "B"]],
      f"and Enter confirms BOTH typed names as one set (ran={ctx.ran!r})")

# --- TWO sets: the second must not silently re-take the first ---------------
# This is the gate that matters for BooleanDifference: if the selection survived
# the first Enter, a second Enter would subtract the operands from themselves.
ctx = FakeCtx()
ctx.ran = None
for n in ("A", "B"):
    ctx.add_input(bridge.make_circle((0, 0, 0), 1), n)
ctx.select("A")
cmd = _TwoSetCmd()
msg, done = cmd.begin(ctx)
check(not done and ctx.ran is None,
      "a two-set command does NOT run off a preselection -- it still needs set two")
check(ctx.selected_objects() == [] or [o.name for o in ctx.selected_objects()] == [],
      "and the selection is CLEARED after set one, so set two starts empty "
      f"(still selected: {[o.name for o in ctx.selected_objects()]})")
msg, done = cmd.feed("", ctx)
check(not done and "Nothing selected" in (msg or ""),
      "pressing Enter again with nothing re-selected does NOT re-use set one")
ctx.select("B")
msg, done = cmd.feed("", ctx)
check(done and ctx.ran == [["A"], ["B"]],
      f"the two sets stay SEPARATE and in order (ran={ctx.ran!r})")

# --- the boolean commands declare their MEASURED step shapes ----------------
# booleanunion.htm: Union is ONE set; Difference and Intersection are TWO.
# Asserted on the declared steps because `execute` needs bpy -- `spike/addon_e2e.py`
# drives all three end to end with a real Blender.
bu = commands.COMMANDS["booleanunion"]
bd = commands.COMMANDS["booleandifference"]
bi = commands.COMMANDS["booleanintersection"]
check([k for _p, k in bu.steps] == [commands.OBJECTS_MULTI],
      f"BooleanUnion is ONE objects_multi step (got {bu.steps!r})")
check([k for _p, k in bd.steps] == [commands.OBJECTS_MULTI, commands.OBJECTS_MULTI],
      f"BooleanDifference is TWO (subtract from / subtract with) (got {bd.steps!r})")
check([k for _p, k in bi.steps] == [commands.OBJECTS_MULTI, commands.OBJECTS_MULTI],
      f"BooleanIntersection is TWO (got {bi.steps!r})")
check("subtract from" in bd.steps[0][0].lower()
      and "subtract with" in bd.steps[1][0].lower(),
      "and BooleanDifference's prompts are the help's own words, so the DIRECTION "
      f"is legible to the user (got {[p for p, _k in bd.steps]!r})")
check(bi.operation == "INTERSECT",
      "BooleanIntersection passes Blender's enum value INTERSECT -- not "
      f"'INTERSECTION', which is not a valid enum member (got {bi.operation!r})")

check(bu.preselect_names >= 1 and bd.preselect_names >= 1,
      "and all three opt into the selection fill, so \"select the solids then run "
      "the command\" works at ANY count -- which is what the removed exactly-two "
      f"shortcut was approximating (got {bu.preselect_names}, {bd.preselect_names})")

# --- at least two operands, reported by COUNT -------------------------------
class _FoldCmd(commands._BooleanCmd):
    name = "FoldProbe"
    steps = [("Select objects:", commands.OBJECTS_MULTI)]


probe = _FoldCmd()
probe.args = [["OnlyOne"]]
msg, done = probe.execute(FakeCtx())
check(done and "at least two" in (msg or "").lower(),
      f"a boolean with one operand says so by COUNT rather than failing inside "
      f"the solver (msg={msg!r})")
probe = _FoldCmd()
probe.args = [["A", "B"], ["B", "C"]]
check(probe._operand_names() == ["A", "B", "C"],
      "operands from several sets are flattened in ORDER and de-duplicated "
      f"(got {probe._operand_names()!r})")
probe = _FoldCmd()
probe.args = ["A", "B"]        # positional strings, not lists
check(probe._operand_names() == ["A", "B"],
      "a plain string arg still works, so a macro or test can pass names "
      "positionally")

# --- the SINGLE-object `objects` step is UNCHANGED (regression) -------------
# Three commands still use it. If `objects_multi` had been implemented by widening
# `objects` instead of adding a kind, those three would now receive LISTS.
ctx = FakeCtx()
ctx.add_input(bridge.make_circle((0, 0, 0), 5), "Circle01")
ctx.select("Circle01")
ctx.select("Circle02")      # a second selected object, deliberately
offset = type(commands.COMMANDS["offset"])()
offset.begin(ctx)
check(offset.args == ["Circle01"],
      "a single `objects` step takes exactly ONE name as a string "
      f"(got {offset.args!r})")

# ===========================================================================
print()
print("=" * 60)
print("T-116: a command must ASK for its first input, not just refuse")
print("=" * 60)

# --- stubs for the bpy / ui surface the five commands touch (headless) ---
import types as _types_mod

_bpy_stub = _types_mod.ModuleType("bpy")
_bpy_ctx = _types_mod.ModuleType("bpy.context")
_bpy_scene = _types_mod.ModuleType("bpy.context.scene")
_bpy_coll = _types_mod.ModuleType("bpy.context.scene.collection")
_bpy_coll_objs = _types_mod.ModuleType("bpy.context.scene.collection.objects")
_bpy_coll_objs.unlink = lambda obj: None
_bpy_coll.objects = _bpy_coll_objs
_bpy_scene.collection = _bpy_coll
_bpy_ctx.scene = _bpy_scene
_bpy_data = _types_mod.ModuleType("bpy.data")
_bpy_data_objs = _types_mod.ModuleType("bpy.data.objects")
_bpy_data_objs.remove = lambda obj, **kw: None
_bpy_data_meshes = _types_mod.ModuleType("bpy.data.meshes")
_bpy_data_meshes.remove = lambda obj: None
_bpy_data.objects = _bpy_data_objs
_bpy_data.meshes = _bpy_data_meshes
_bpy_stub.context = _bpy_ctx
_bpy_stub.data = _bpy_data
sys.modules.setdefault("bpy", _bpy_stub)

_ui_stub = _types_mod.ModuleType("narwhal3d.ui")
_ui_calls = []
_ui_stub.set_points_on = lambda names, on: _ui_calls.append((list(names), on)) or len(names)
_ui_stub.clear_points_on = lambda: _ui_calls.append(("clear",)) or 0
sys.modules.setdefault("narwhal3d.ui", _ui_stub)

# FakeObj needs .data (DeleteCmd.execute reads ob.data)
FakeObj.data = None

# ---------------------------------------------------------------------------
# 1. preselection: each of the five runs immediately and does what it did before
# ---------------------------------------------------------------------------
print()
print("preselection: the five commands run immediately and do their work")

for key in ("hide", "lock", "delete", "pointson", "pointsoff"):
    ctx = FakeCtx()
    ctx.add_input(bridge.make_polyline([(0, 0, 0), (5, 0, 0)], degree=1), "Obj01")
    ctx.select("Obj01")
    cmd = fresh(key)
    msg, done = cmd.begin(ctx)
    check(done is True,
          f"{key}: with a pre-selection the command runs immediately (done={done})")

# Hide: the object is now Mode==Hidden
ctx = FakeCtx()
ctx.add_input(bridge.make_polyline([(0, 0, 0), (5, 0, 0)], degree=1), "Obj01")
ctx.select("Obj01")
msg, done = fresh("hide").begin(ctx)
check(ctx.count_mode(r3.ObjectMode.Hidden) == 1,
      "Hide: the pre-selected object is Mode==Hidden")
check(msg is None, f"Hide: silent, as the reference CAD application (MEASURED research_all_13 #070) ({msg!r})")

# Lock: the object is now Mode==Locked
ctx = FakeCtx()
ctx.add_input(bridge.make_polyline([(0, 0, 0), (5, 0, 0)], degree=1), "Obj01")
ctx.select("Obj01")
msg, done = fresh("lock").begin(ctx)
check(ctx.count_mode(r3.ObjectMode.Locked) == 1,
      "Lock: the pre-selected object is Mode==Locked")
check(msg is None, f"Lock: silent, as the reference CAD application (MEASURED research_all_13 #084) ({msg!r})")

# Delete: the object is removed from the model
ctx = FakeCtx()
ctx.add_input(bridge.make_polyline([(0, 0, 0), (5, 0, 0)], degree=1), "Obj01")
ctx.select("Obj01")
count_before = ctx.model.object_count()
msg, done = fresh("delete").begin(ctx)
check(ctx.model.object_count() == count_before - 1,
      f"Delete: the pre-selected object is removed (count {count_before} -> {ctx.model.object_count()})")
check("Deleted" in (msg or ""), f"Delete: the message reports the deletion ({msg!r})")

# PointsOn: ui.set_points_on was called with the selected object
# A circle is a NurbsCurve with 9 readable control points, so the success
# path (not the honest "no control points" report) is taken.
_ui_calls.clear()
ctx = FakeCtx()
ctx.add_input(bridge.make_circle((0, 0, 0), 5), "Obj01")
ctx.select("Obj01")
msg, done = fresh("pointson").begin(ctx)
check(any("Obj01" in c[0] for c in _ui_calls if isinstance(c, tuple) and len(c) >= 1),
      f"PointsOn: ui.set_points_on was called with the selected object ({_ui_calls})")
check(msg is None, f"PointsOn: silent, as the reference CAD application (MEASURED research_all_13 #111) ({msg!r})")

# PointsOff: ui.set_points_on was called with on=False
_ui_calls.clear()
ctx = FakeCtx()
ctx.add_input(bridge.make_circle((0, 0, 0), 5), "Obj01")
ctx.select("Obj01")
msg, done = fresh("pointsoff").begin(ctx)
check(any(c[1] is False for c in _ui_calls if isinstance(c, tuple) and len(c) >= 2),
      f"PointsOff: ui.set_points_on was called with on=False ({_ui_calls})")
check(msg is None, f"PointsOff: silent, as the reference CAD application (MEASURED research_all_13 #110) ({msg!r})")

# ---------------------------------------------------------------------------
# 2. nothing selected: the command starts and PROMPTS
# ---------------------------------------------------------------------------
print()
print("nothing selected: the command starts and PROMPTS (not a refusal)")

for key in ("hide", "lock", "delete", "pointson"):
    ctx = FakeCtx()
    ctx.add_input(bridge.make_polyline([(0, 0, 0), (5, 0, 0)], degree=1), "Obj01")
    # deliberately NOT selected
    cmd = fresh(key)
    msg, done = cmd.begin(ctx)
    check(done is False,
          f"{key}: with nothing selected the command STARTS and stays running (done={done})")
    check(cmd.prompt and "select" in cmd.prompt.lower(),
          f"{key}: the prompt names what it wants ({cmd.prompt!r})")
    check(cmd.step_index == 0,
          f"{key}: it is on the set-step, waiting for input (step_index={cmd.step_index})")

# PointsOff never prompts (MEASURED, Research13 #110): with nothing selected it runs
# on the naming Enter and turns every object's points off.
_ui_calls.clear()
ctx = FakeCtx()
ctx.add_input(bridge.make_polyline([(0, 0, 0), (5, 0, 0)], degree=1), "Obj01")
cmd = fresh("pointsoff")
msg, done = cmd.begin(ctx)
check(done is True and list(cmd.steps) == [],
      f"pointsoff: no prompt, runs at once with nothing selected (done={done}, steps={cmd.steps!r})")
check(("clear",) in _ui_calls,
      f"pointsoff: with nothing selected every object's points go off ({msg!r}, {_ui_calls})")

# ---------------------------------------------------------------------------
# 3. select + Enter: the prompt is answered and the command completes
# ---------------------------------------------------------------------------
print()
print("select then Enter: the prompt is answered and the command completes")

for key in ("hide", "lock", "delete", "pointson"):
    ctx = FakeCtx()
    ctx.add_input(bridge.make_polyline([(0, 0, 0), (5, 0, 0)], degree=1), "Obj01")
    cmd = fresh(key)
    cmd.begin(ctx)  # nothing selected -> prompts
    ctx.select("Obj01")
    msg, done = cmd.feed("", ctx)  # Enter confirms the selection
    check(done is True,
          f"{key}: Enter after selecting COMPLETES the command (done={done})")

# Hide: the object is now hidden
ctx = FakeCtx()
ctx.add_input(bridge.make_polyline([(0, 0, 0), (5, 0, 0)], degree=1), "Obj01")
cmd = fresh("hide")
cmd.begin(ctx)
ctx.select("Obj01")
msg, done = cmd.feed("", ctx)
check(ctx.count_mode(r3.ObjectMode.Hidden) == 1,
      "Hide: the object is Mode==Hidden after select+Enter")

# Delete: the object is removed
ctx = FakeCtx()
ctx.add_input(bridge.make_polyline([(0, 0, 0), (5, 0, 0)], degree=1), "Obj01")
count_before = ctx.model.object_count()
cmd = fresh("delete")
cmd.begin(ctx)
ctx.select("Obj01")
msg, done = cmd.feed("", ctx)
check(ctx.model.object_count() == count_before - 1,
      "Delete: the object is removed after select+Enter")

# ---------------------------------------------------------------------------
# 4. Enter with nothing selected: cancels cleanly, document unchanged
# ---------------------------------------------------------------------------
print()
print("Enter with nothing selected: cancels cleanly, document unchanged")

for key in ("hide", "lock", "delete", "pointson"):
    ctx = FakeCtx()
    ctx.add_input(bridge.make_polyline([(0, 0, 0), (5, 0, 0)], degree=1), "Obj01")
    count_before = ctx.model.object_count()
    cmd = fresh(key)
    cmd.begin(ctx)  # prompts
    msg, done = cmd.feed("", ctx)  # Enter with nothing selected
    check(done is True,
          f"{key}: Enter with nothing selected CANCELS the command (done={done})")
    if key in ("hide", "lock"):
        # MEASURED (research_all_13 #070/#084): the reference CAD application returns to Command: silently.
        check(msg is None, f"{key}: the empty Enter is silent ({msg!r})")
    else:
        check("cancel" in (msg or "").lower(),
              f"{key}: the message says it cancelled ({msg!r})")
    check(ctx.model.object_count() == count_before,
          f"{key}: the document is unchanged (count {ctx.model.object_count()} == {count_before})")

# ---------------------------------------------------------------------------
# 5. PointsOn: the honest per-object reporting must survive
# ---------------------------------------------------------------------------
print()
print("PointsOn: the honest per-object reporting survives the new prompt")

# An object whose geometry has no readable control points must be reported
# honestly ("no control points we can display") rather than silently treated
# as if it had points.
# A r3.Mesh has no NurbsCurve .Points, so control_point_count returns 0.
try:
    no_cp_geom = r3.Mesh()
except Exception:
    no_cp_geom = bridge.make_polyline([(0, 0, 0), (5, 0, 0)], degree=1)

check(len(bridge.control_points(no_cp_geom)) == 0,
      f"premise: the test geometry has no readable control points "
      f"({len(bridge.control_points(no_cp_geom))})")

_ui_calls.clear()
ctx = FakeCtx()
ctx.add_input(no_cp_geom, "NoCP01")
ctx.select("NoCP01")
msg, done = fresh("pointson").begin(ctx)
check(done is True, f"PointsOn: runs with a pre-selection (done={done})")
check("no control points" in (msg or "").lower(),
      f"PointsOn: honestly reports it has no control points to display ({msg!r})")

print()
print("=" * 60)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"PRESELECT TESTS PASSED ({checks} checks)")
