#!/usr/bin/env python3
"""T-117: the remaining selection-driven commands ASK for their first input.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_t117.py

OWNER-REPORTED (via T-116): "command behavior seems wrong ... it should either
take whatever i have selected as the first command, or ask for input."

T-116 fixed five pure-selection commands (Delete/Hide/Lock/PointsOn/PointsOff).
T-117 does the rest that read `ctx.selected_objects()` but could not prompt:
Rotate, Scale, Array, ArrayPolar, Mirror (selection + geometry inputs), and
Join, Explode, Divide (selection, like Delete). Each gains a LEADING
`objects_multi` set-step with `preselect_names=1` and `cancel_on_empty_set=True`.

**The mechanism change that makes selection+geometry commands work.** An
`objects_multi` step used to CLEAR the selection after confirming a set (so a
two-set Boolean's second set starts empty). That clear now fires ONLY before
ANOTHER selection step -- so a transform's leading object step keeps the
selection LIVE through its point/number steps, and `execute` reads it unchanged.
This suite pins that invariant directly.

MEASURED "Select" lines (the command reference):
  Rotate "Select the objects."  Scale "Select objects."  Array "Select the
  objects."  ArrayPolar "Select the objects."  Mirror "Select the objects."
  Join "Select the objects ... to join ... press Enter."  Explode "Select
  objects."  Divide "Select curves."

`Scale.execute` and `Rotate.execute` bake through `bpy`
(`_apply_transform_to_selection`; Scale also reads `bound_box`), so Scale is
flow-tested only and Rotate's transform helper is patched. Mirror/Array/
ArrayPolar/Divide `execute` are bpy-free (rhino3dm `Duplicate`/`Transform`,
sampling + the pure `divide` math), so they run for real -- which is what
catches an arg-index misalignment from prepending the object step. The real
Blender bake of Array/ArrayPolar/Mirror is also covered by spike/addon_e2e.py.
"""
import importlib.util
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")

_pkg = types.ModuleType("narwhal3d")
_pkg.__path__ = [ADDON]
_pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = _pkg


def _load(name, relpath):
    path = os.path.join(ADDON, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bridge = _load("narwhal3d.bridge", "bridge.py")
_load("narwhal3d.options", "options.py")
cplane = _load("narwhal3d.cplane", "cplane.py")
_load("narwhal3d.osnap", "osnap.py")
_load("narwhal3d.coords", "coords.py")
_load("narwhal3d.gumball", "gumball.py")
_load("narwhal3d.undo", "undo.py")
_load("narwhal3d.sel_commands", "sel_commands.py")
_load("narwhal3d.divide", "divide.py")
_load("narwhal3d.named_cplanes", "named_cplanes.py")
model = _load("narwhal3d.model", "model.py")
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


class FakeObj:
    def __init__(self, name):
        self.name = name


class FakeCtx:
    """A NarwhalContext stand-in for a selection-driven command, headless."""

    def __init__(self):
        self.model = model.NarwhalModel()
        self._selected = []
        self.changes = []
        self.added = []

    def begin_undo(self, name):
        return None

    def end_undo(self):
        return None

    def add_input(self, geom, name, layer_index=0):
        """Place a document object and select it."""
        self.model.add_object(geom, name, layer_index)
        self._selected.append(name)
        return name

    def selected_objects(self):
        return [FakeObj(n) for n in self._selected]

    def select_by_names(self, names):
        self._selected = [str(n) for n in names]
        return list(self._selected)

    def geom_by_name(self, name):
        for obj in self.model.file.Objects:
            if (obj.Attributes.Name or "").lower() == name.lower():
                return obj.Geometry
        return None

    def object_by_name(self, name):
        return None                  # no Blender object headlessly

    def object_id_by_name(self, name):
        return self.model.object_id_by_name(name)

    def note_change(self, label=None):
        self.changes.append(label)

    def active_cplane(self):
        return None

    def add_geometry(self, geom, base_name, layer_index=0):
        display = f"{base_name}{len(self.added) + 1:02d}"
        self.model.add_object(geom, display, layer_index)
        self.added.append((geom, base_name, int(layer_index), display))
        return display, display


def fresh(key):
    import copy
    return copy.deepcopy(commands.COMMANDS[key])


def a_line(p0=(0, 0, 0), p1=(5, 0, 0)):
    return r3.LineCurve(r3.Point3d(*p0), r3.Point3d(*p1))


# The eight commands and the leading-prompt text each should show.
SPECS = [
    ("rotate", "Rotate", "select objects"),
    ("scale", "Scale", "select objects"),
    ("array", "Array", "select objects"),
    ("arraypolar", "ArrayPolar", "select objects"),
    ("mirror", "Mirror", "select objects"),
    ("join", "Join", "select objects"),
    ("explode", "Explode", "select objects"),
    ("divide", "Divide", "select curves"),
]


# ===========================================================================
print()
print("every one of the eight leads with an objects_multi set-step")
# ===========================================================================
for key, name, prompt in SPECS:
    cmd = fresh(key)
    k0 = cmd.steps[0][1] if cmd.steps else None
    check(k0 == commands.OBJECTS_MULTI,
          f"{name}: step 0 is an objects_multi set-step (got {k0!r})")
    check(cmd.preselect_names == 1 and cmd.cancel_on_empty_set is True,
          f"{name}: preselect_names=1 and cancel_on_empty_set=True")
    check(prompt in cmd.steps[0][0].lower(),
          f"{name}: the prompt names what it wants ({cmd.steps[0][0]!r})")


# ===========================================================================
print()
print("nothing selected: the command PROMPTS, and Enter cancels cleanly")
# ===========================================================================
for key, name, _prompt in SPECS:
    cmd = fresh(key)
    ctx = FakeCtx()                 # empty selection
    cmd.begin(ctx)
    at_object_step = (cmd.step_index == 0 and not cmd.args)
    check(at_object_step, f"{name}: with nothing selected it waits on the object step")
    msg, done = cmd.feed("", ctx)   # Enter / right-click with nothing picked
    check(done is True and "cancel" in (msg or "").lower(),
          f"{name}: an empty confirm cancels cleanly ({msg!r})")
    check(ctx.added == [] and ctx.changes == [],
          f"{name}: nothing was created or changed by the cancel")


# ===========================================================================
print()
print("a pre-selection is consumed AND stays live through to execute")
# ===========================================================================
for key, name, _prompt in SPECS:
    cmd = fresh(key)
    ctx = FakeCtx()
    ctx.add_input(a_line(), "L1")   # adds + selects L1
    cmd.begin(ctx)
    check(cmd.args and cmd.args[0] == ["L1"],
          f"{name}: begin consumed the pre-selection into the object step ({cmd.args[:1]})")
    # THE invariant: a following point/number step must NOT clear the selection.
    live = [o.name for o in ctx.selected_objects()]
    check(live == ["L1"],
          f"{name}: the selection stays live for execute (got {live})")


# ===========================================================================
print()
print("arg alignment: the bpy-free commands run for real after the object step")
# ===========================================================================

# -- Mirror: select, then two plane points -> one mirror image ---------------
cmd = fresh("mirror")
ctx = FakeCtx()
ctx.add_input(a_line((1, 0, 0), (4, 0, 0)), "L1")   # spans x [1,4]
cmd.begin(ctx)                                       # object step prefilled
cmd.feed("0,0,0", ctx)                               # plane point 1
msg, done = cmd.feed("0,5,0", ctx)                   # plane point 2 (mirror x=0)
check(done is True, f"Mirror: finishes after the two plane points ({msg!r})")
check(len(ctx.added) == 1, f"Mirror: one image created ({len(ctx.added)})")
if ctx.added:
    bb = ctx.added[0][0].GetBoundingBox()
    check(abs(bb.Min.X + 4) < 1e-6 and abs(bb.Max.X + 1) < 1e-6,
          f"Mirror: image mirrored across x=0 to [-4,-1] (X[{bb.Min.X:.3g},{bb.Max.X:.3g}])")

# -- Array: X/Y/Z counts, spacings, accept (MEASURED research_all_13 #006) ----------
cmd = fresh("array")
ctx = FakeCtx()
ctx.add_input(a_line((0, 0, 0), (1, 0, 0)), "L1")
cmd.begin(ctx)
for line in ("3", "1", "1", "10"):                   # X=3, Y=1, Z=1, X spacing 10
    cmd.feed(line, ctx)
check(cmd.prompt == "Press Enter to accept:", f"Array: then the accept prompt ({cmd.prompt!r})")
msg, done = cmd.feed("", ctx)
check(done is True, f"Array: Enter accepts ({msg!r})")
check(len(ctx.added) == 2, f"Array: 3 x 1 x 1 -> 2 copies ({len(ctx.added)})")

# a count below 1 is refused and asked again; 1 x 1 x 1 makes nothing
cmd = fresh("array")
ctx = FakeCtx()
ctx.add_input(a_line(), "L1")
cmd.begin(ctx)
msg, done = cmd.feed("0", ctx)
check(not done and msg == "Number must be bigger than or equal to 1",
      f"Array: a count of 0 is asked again ({msg!r})")
for line in ("1", "1", "1"):
    msg, done = cmd.feed(line, ctx)
msg, done = cmd.feed("", ctx)
check(ctx.added == [] and done and "only the original" in (msg or ""),
      f"Array: 1 x 1 x 1 creates nothing and says so ({msg!r})")

# -- ArrayPolar: center, count, axis -> count-1 copies -----------------------
cmd = fresh("arraypolar")
ctx = FakeCtx()
ctx.add_input(a_line((1, 0, 0), (2, 0, 0)), "L1")
cmd.begin(ctx)
cmd.feed("0,0,0", ctx)                               # center
cmd.feed("4", ctx)                                   # count
cmd.feed("", ctx)                                    # fill angle: Enter = 360
msg, done = cmd.feed("", ctx)                        # accept
check(done is True, f"ArrayPolar: finishes after center, count, fill, accept ({msg!r})")
check(len(ctx.added) == 3, f"ArrayPolar: count=4 -> 3 copies ({len(ctx.added)})")
check(len(ctx.added) == 3 and all(abs(a[0].PointAtStart.Z) < 1e-12 for a in ctx.added),
      "ArrayPolar: about the CPlane normal -- the copies stay in the Top plane")
# MEASURED (research_all_13 #007): Enter at the centre = CPlane origin; a count below 2
# is refused with the measured message and asked again; a non-whole count is asked again.
cmd = fresh("arraypolar")
ctx = FakeCtx()
ctx.add_input(a_line((1, 0, 0), (2, 0, 0)), "L1")
cmd.begin(ctx)
check(cmd.prompt.startswith("Center of polar array. Press Enter to use the CPlane origin"),
      f"ArrayPolar: the measured centre prompt ({cmd.prompt!r})")
cmd.feed("", ctx)
check(cmd.args[1:] == [(0.0, 0.0, 0.0)] and cmd.prompt.startswith("Number of items"),
      f"ArrayPolar: Enter takes the CPlane origin ({cmd.args[1:]})")
msg, done = cmd.feed("1", ctx)
check(not done and msg == "Number must be bigger than or equal to 2"
      and cmd.prompt.startswith("Number of items"), f"ArrayPolar: count 1 is asked again ({msg!r})")
msg, done = cmd.feed("2.5", ctx)
check(not done and msg is None and cmd.prompt.startswith("Number of items"),
      f"ArrayPolar: 2.5 is silently asked again ({msg!r})")
cmd.feed("3", ctx)
cmd.feed("", ctx)
msg, done = cmd.feed("", ctx)
check(done and len(ctx.added) == 2, f"ArrayPolar: then count 3 -> 2 copies ({msg!r})")

# -- Divide: select curves, then the segment count ---------------------------
cmd = fresh("divide")
ctx = FakeCtx()
ctx.add_input(a_line((0, 0, 0), (10, 0, 0)), "L1")
cmd.begin(ctx)
msg, done = cmd.feed("4", ctx)                        # 4 segments -> 5 points
check(done is True, f"Divide: finishes after the count ({msg!r})")
check(len(ctx.added) == 5, f"Divide: n=4 -> 5 point objects ({len(ctx.added)})")

# Divide's Length option still relabels the NUMBER step (found by kind, not index)
cmd = fresh("divide")
ctx = FakeCtx()
ctx.add_input(a_line((0, 0, 0), (10, 0, 0)), "L1")
cmd.begin(ctx)
cmd.feed("Length", ctx)
check(any(p == "Length:" for p, _k in cmd.steps),
      f"Divide: the Length option relabels the number step ({[p for p, _ in cmd.steps]})")
msg, done = cmd.feed("2.5", ctx)
check(done is True and len(ctx.added) == 5,
      f"Divide: Length=2.5 on a 10-unit line -> 5 points ({len(ctx.added)})")


# ===========================================================================
print()
print("Rotate: the transform helper is patched; args[1:] feed it correctly")
# ===========================================================================
captured = {}


def _recording_transform(ctx, xform):
    captured["xform"] = xform
    return [o.name for o in ctx.selected_objects()]


_orig = commands._apply_transform_to_selection
commands._apply_transform_to_selection = _recording_transform
try:
    cmd = fresh("rotate")
    ctx = FakeCtx()
    ctx.add_input(a_line(), "L1")
    cmd.begin(ctx)                                   # object step prefilled
    cmd.feed("0,0,0", ctx)                           # center
    # G-77: the reference Rotate has NO axis prompt ("Pick the center of rotation. Type the
    # angle, or pick two reference points" -- the axis is the CPlane normal), so the
    # angle finishes it. The old typed x/y/z step is gone.
    msg, done = cmd.feed("90", ctx)                  # angle
    check(done is True and "Rotated" in (msg or ""),
          f"Rotate: center/angle read from args[1:] ({msg!r})")
    check("xform" in captured, "Rotate: reached the transform helper (no arg crash)")
finally:
    commands._apply_transform_to_selection = _orig


# ===========================================================================
print()
print("Scale: flow (G-77 made it headless; spike/test_transform_cmds.py runs it for real)")
# ===========================================================================
cmd = fresh("scale")
ctx = FakeCtx()
ctx.add_input(a_line(), "L1")
cmd.begin(ctx)
check(cmd.args and cmd.args[0] == ["L1"] and cmd.step_index == 1,
      f"Scale: object step consumed, now at the next step ({cmd.args[:1]}, i={cmd.step_index})")
# G-77: the reference Scale picks a BASE POINT next ("Pick a base point, or press Enter to use
# the center of the bounding box") -- the factor follows it.
check(cmd.steps[cmd.step_index][1] == commands.POINT
      and cmd.steps[cmd.step_index][0].startswith("Base point"),
      "Scale: the next step is the base point")


print()
print("=" * 60)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"T-117 TESTS PASSED ({checks} checks)")
