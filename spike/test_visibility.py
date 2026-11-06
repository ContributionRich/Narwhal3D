#!/usr/bin/env python3
"""the `Hide` / `Show` / `Lock` / `Unlock` visibility commands.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_visibility.py

The three right-click context menus (navigate.py) name these and none existed,
so four menu rows were dead. They are a pure DOCUMENT change, not new geometry:
`rhino3dm.ObjectAttributes.Mode` is one of ObjectMode.{Normal, Hidden, Locked,
InstanceDefinitionObject} (MEASURED: Normal 0, Hidden 1, Locked 2), so

  Hide   -> the SELECTION  gets Mode = Hidden
  Lock   -> the SELECTION  gets Mode = Locked
  Show   -> ALL hidden     get  Mode = Normal   (a hidden object cannot be
  Unlock -> ALL locked     get  Mode = Normal    selected, so these two act on
                                                    the whole document)

MEASURED (commands/hide.htm, commands/lock.htm):
  Hide   -- "conceals SELECTED objects from view."
  Lock   -- "Select objects to lock."
  Show   -- "re-displays ALL hidden objects."
  Unlock -- "unlocks ALL locked objects."

Covered (per the task):
  * hide then show round-trips the document (count objects with Mode==Hidden
    before/after);
  * `Hide` with nothing selected is refused rather than hiding everything;
  * `Show` with nothing hidden reports that instead of claiming success;
  * a locked object is still IN the document (asserting it was deleted would
    pass for the entirely wrong reason);
  * and the mixed case: `Show` unhides only the hidden ones, `Unlock` unlocks
    only the locked ones, leaving Normal objects alone.

The DISPLAY half is intentionally NOT tested here: a hidden object must
disappear from the viewport and a locked one must stay visible but
unselectable, but that lives in bridge.py (the geometry module) and ui.py (the
shell), neither of which honours Mode yet. This suite pins the document
half; see the recorded gap in the test history.

Loaded by file path (no Blender): the same trick as spike/test_explode.py.
"""
import importlib.util
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


class FakeObj:
    def __init__(self, name):
        self.name = name


class FakeCtx:
    """A NarwhalContext stand-in for a selection-driven command, headless.

    Backed by a REAL NarwhalModel so `model.file.Objects` (where the Mode
    attribute lives) behaves exactly as in the app. The only bpy-touching
    surface is overridden: `selected_objects` returns plain name holders and
    `object_by_name` returns None (no Blender object headlessly).
    """

    def __init__(self):
        self.model = model.NarwhalModel()
        self._selected = []
        self.changes = []

    def add_input(self, geom, name, layer_index=0):
        self.model.add_object(geom, name, layer_index)
        return name

    def select(self, *names):
        self._selected = list(names)

    def selected_objects(self):
        return [FakeObj(n) for n in self._selected]

    def object_by_name(self, name):
        return None

    def note_change(self, label=None):
        self.changes.append(label)

    # -- the document table, queried the way the commands do
    def modes(self, name):
        out = []
        for f in self.model.file.Objects:
            if (f.Attributes.Name or "").lower() == (name or "").lower():
                out.append(f.Attributes.Mode)
        return out

    def count_mode(self, mode):
        return sum(1 for f in self.model.file.Objects if f.Attributes.Mode == mode)

    def exists(self, name):
        return self.model.object_id_by_name(name) is not None


def fresh(name):
    return type(commands.COMMANDS[name])()


def seg():
    return bridge.make_polyline([(0, 0, 0), (5, 0, 0), (5, 5, 0)], degree=1)


def _select(ctx, name):
    ctx.select(name)
    return ctx


# ===========================================================================
print()
print("the four commands are registered and selection- / document-driven")
# ===========================================================================
for name in ("Hide", "Show", "Lock", "Unlock"):
    key = name.lower()
    check(key in commands.COMMANDS, f"{name} is registered")
    cmd = commands.COMMANDS.get(key)
    if cmd is not None:
        check(cmd.name == name, f"{key} -> name is {name}")
        if name in ("Hide", "Lock"):
            # these now ASK for the selection (one objects_multi set-step)
            # instead of refusing -- a pre-selection runs them, an empty Enter
            # cancels. The all-objects forms below keep their zero-step shape.
            check([k for _p, k in cmd.steps] == [commands.OBJECTS_MULTI],
                  f"{name} asks for the selection (one set-step; "
                  f"got {[k for _p, k in cmd.steps]!r})")
        else:
            check(list(cmd.steps) == [], f"{name} has no prompts (selection/document-driven)")

# ===========================================================================
print()
print("hide then show round-trips the document")
# ===========================================================================
ctx = FakeCtx()
ctx.add_input(seg(), "Line01")
ctx.select("Line01")

check(ctx.count_mode(r3.ObjectMode.Hidden) == 0, "starts with nothing hidden")
msg, done = fresh("hide").execute(ctx)
check(done is True, "Hide completes")
check(ctx.modes("Line01") == [r3.ObjectMode.Hidden],
      f"the hidden object is Mode==Hidden in the document  ({ctx.modes('Line01')})")
check(ctx.count_mode(r3.ObjectMode.Hidden) == 1, "one object is now hidden")
check(ctx.changes == ["Hide"], f"note_change fired once, BEFORE mutating  ({ctx.changes})")
check(msg is None, f"a preselected Hide is silent (MEASURED research_all_13 #070)  ({msg!r})")

msg, done = fresh("show").execute(ctx)
check(done is True, "Show completes")
check(ctx.modes("Line01") == [r3.ObjectMode.Normal],
      f"the object is back to Mode==Normal  ({ctx.modes('Line01')})")
check(ctx.count_mode(r3.ObjectMode.Hidden) == 0, "nothing is hidden after Show")
check(msg == "Showing one hidden object.", f"the message is the reference CAD application's (MEASURED Research13 #160)  ({msg})")
check(ctx.exists("Line01"), "the object is still in the document")

# ===========================================================================
print()
print("Hide with nothing selected is refused (not 'hide everything')")
# ===========================================================================
ctx = FakeCtx()
ctx.add_input(seg(), "Line01")          # present, but NOT selected
msg, done = fresh("hide").execute(ctx)
check(ctx.count_mode(r3.ObjectMode.Hidden) == 0, "nothing was hidden")
check(ctx.changes == [], "no document mutation, so no undo record")
check("select" in msg.lower() and "hide" in msg.lower(),
      f"it asks for a selection  ({msg})")

# ===========================================================================
print()
print("Show with nothing hidden reports that (not 'success')")
# ===========================================================================
ctx = FakeCtx()
ctx.add_input(seg(), "Line01")
msg, done = fresh("show").execute(ctx)
check(ctx.count_mode(r3.ObjectMode.Normal) == 1, "the normal object is untouched")
check(ctx.changes == [], "no document mutation, so no undo record")
check(msg == "No objects are hidden.",
      f"it says there is nothing hidden, the reference CAD application wording (MEASURED Research13 #160)  ({msg})")

# ===========================================================================
print()
print("lock then unlock round-trips, and a locked object is still IN the document")
# ===========================================================================
ctx = FakeCtx()
ctx.add_input(seg(), "Line01")
ctx.select("Line01")
msg, done = fresh("lock").execute(ctx)
check(ctx.modes("Line01") == [r3.ObjectMode.Locked],
      f"the locked object is Mode==Locked  ({ctx.modes('Line01')})")
# the task's explicit trap: a test asserting it was deleted would pass for the
# wrong reason, so pin that it is still present.
check(ctx.exists("Line01"), "a locked object is still IN the document (not deleted)")
check(ctx.count_mode(r3.ObjectMode.Normal) == 0, "no longer Normal")
check(ctx.changes == ["Lock"], f"note_change fired once  ({ctx.changes})")
check(msg is None, f"Lock is silent (MEASURED research_all_13 #084)  ({msg!r})")

msg, done = fresh("unlock").execute(ctx)
check(ctx.modes("Line01") == [r3.ObjectMode.Normal],
      f"the object is back to Mode==Normal  ({ctx.modes('Line01')})")
check(ctx.exists("Line01"), "the unlocked object is still in the document")
check(msg == "Unlocking one object.", f"the message is the reference CAD application's (MEASURED Research13 #181)  ({msg})")

# ===========================================================================
print()
print("Unlock with nothing locked reports that")
# ===========================================================================
ctx = FakeCtx()
ctx.add_input(seg(), "Line01")
msg, done = fresh("unlock").execute(ctx)
check(ctx.count_mode(r3.ObjectMode.Normal) == 1, "the normal object is untouched")
check(ctx.changes == [], "no document mutation, so no undo record")
check(msg == "No objects are locked.",
      f"it says there is nothing locked, the reference CAD application wording (MEASURED Research13 #181)  ({msg})")

# ===========================================================================
print()
print("mixed document: Show unhides only hidden, Unlock only locked")
# ===========================================================================
ctx = FakeCtx()
ctx.add_input(seg(), "A")
ctx.add_input(seg(), "B")
ctx.add_input(seg(), "C")
# put them into three different states by driving the commands
fresh("hide").execute(_select(ctx, "A"))
fresh("lock").execute(_select(ctx, "B"))
# C stays Normal
check(ctx.count_mode(r3.ObjectMode.Hidden) == 1, "one hidden")
check(ctx.count_mode(r3.ObjectMode.Locked) == 1, "one locked")
check(ctx.count_mode(r3.ObjectMode.Normal) == 1, "one normal")

msg, done = fresh("show").execute(ctx)
check(ctx.modes("A") == [r3.ObjectMode.Normal], "Show unhid A")
check(ctx.modes("B") == [r3.ObjectMode.Locked], "Show left the locked B alone")
check(ctx.modes("C") == [r3.ObjectMode.Normal], "Show left the normal C alone")
check(msg == "Showing one hidden object.", f"it reports only A was shown  ({msg})")

ctx2 = FakeCtx()
ctx2.add_input(seg(), "A")
ctx2.add_input(seg(), "B")
fresh("hide").execute(_select(ctx2, "A"))
fresh("lock").execute(_select(ctx2, "B"))
msg, done = fresh("unlock").execute(ctx2)
check(ctx2.modes("B") == [r3.ObjectMode.Normal], "Unlock unlocked B")
check(ctx2.modes("A") == [r3.ObjectMode.Hidden], "Unlock left the hidden A alone")
check(msg == "Unlocking one object.", f"it reports only B was unlocked  ({msg})")

# ===========================================================================
# Mode survives undo -- the REAL NarwhalContext path, not FakeCtx
# ===========================================================================
# FakeCtx has no undo stack, so it cannot exercise the snapshot/restore path
# that this bug lives in. The real path is: command.execute -> note_change
# (captures `before`) -> mutate Mode -> undo_last/redo_last -> model.restore.
#
# MEASURED, the trap (see finding): `restore` re-Adds the object from
# the snapshot's attrs dict. A fresh ObjectAttributes() defaults to Normal, so
#   * UNDO  (restore `before`, captured while Mode was already Normal) is
#     NON-discriminating -- it passes even when `Mode` is absent from
#     _SNAPSHOT_ATTRS, because the missing value defaults to Normal anyway;
#   * REDO  (restore `after`, captured while Mode was Hidden/Locked) is the
#     DISCRIMINATOR -- without `Mode` in the tuple, the re-Add falls back to
#     the fresh Normal default and the redo silently un-hides / un-locks.
# So this test asserts redo, not just undo, and that is what goes RED when
# `Mode` is removed from the tuple (the mutation test the task requires).

class _Sel:
    def __init__(self, name):
        self.name = name


def real_ctx(selected):
    """A real NarwhalContext driven headless: the two bpy-touching surface
    points (selection + display rebuild) are stubbed, everything else --
    the model, the undo stack, note_change, restore -- is the real thing.
    """
    ctx = commands.NarwhalContext(model.NarwhalModel())
    ctx.selected_objects = lambda: [_Sel(n) for n in list(selected)]
    ctx.rebuild_display = lambda: None
    ctx.select_by_names = lambda names: None
    return ctx


def run_undo_redo(cmd_key, want_mode):
    """Drive cmd (Hide/Lock) -> undo -> redo on the REAL context. Returns the
    three Mode values (after cmd, after undo, after redo)."""
    ctx = real_ctx(["L1"])
    ctx.model.add_object(seg(), "L1")

    def mode():
        for f in ctx.model.file.Objects:
            if (f.Attributes.Name or "").lower() == "l1":
                return f.Attributes.Mode
        return "GONE"

    # Zero-step command: begin() auto-executes ONCE (the real path), which
    # fires note_change (captures `before`) and then mutates Mode.
    cmd = type(commands.COMMANDS[cmd_key])()
    cmd.begin(ctx)
    after_cmd = mode()
    ctx.undo_last()
    after_undo = mode()
    ctx.redo_last()
    after_redo = mode()
    return after_cmd, after_undo, after_redo


print()
print("Mode survives undo/redo (real context path)")
# Hide then Undo restores Normal and the object is selectable again.
a0, a1, a2 = run_undo_redo("hide", r3.ObjectMode.Hidden)
check(a0 == r3.ObjectMode.Hidden, f"Hide -> Mode==Hidden  ({a0})")
check(a1 == r3.ObjectMode.Normal,
      f"Hide then Undo restores Mode to Normal  ({a1})")
check(a2 == r3.ObjectMode.Hidden,
      f"Redo re-applies the hide (Mode==Hidden)  ({a2})")
# Lock then Undo restores Normal; redo re-locks.
b0, b1, b2 = run_undo_redo("lock", r3.ObjectMode.Locked)
check(b0 == r3.ObjectMode.Locked, f"Lock -> Mode==Locked  ({b0})")
check(b1 == r3.ObjectMode.Normal,
      f"Lock then Undo restores Mode to Normal  ({b1})")
check(b2 == r3.ObjectMode.Locked,
      f"Redo re-applies the lock (Mode==Locked)  ({b2})")

# ===========================================================================
# ShowSelected and UnlockSelected -- the selected-objects forms.
# ===========================================================================
# MEASURED (command_list.htm): the reference CAD application ships FOUR distinct commands, not two --
#   Show           -- re-displays ALL hidden objects
#   ShowSelected   -- shows the SELECTED hidden objects
#   Unlock         -- unlocks ALL locked objects
#   UnlockSelected -- unlocks the SELECTED locked objects
# The selected forms are the all-objects forms scoped to the selection. The
# wrinkle (and why UnlockSelected can ever have a target): selection SURVIVES
# the lock -- Lock/Hide set Mode without touching the selection, and
# ui.sync_locked_selectability only sets hide_select (blocking a later re-pick,
# not the current selection). So select -> lock -> the objects stay selected ->
# UnlockSelected releases them. The last two checks pin that the all-objects
# forms are UNCHANGED -- the guard against collapsing four commands into two.
print()
print("the two selected-objects commands are registered")
for name in ("ShowSelected", "UnlockSelected"):
    key = name.lower()
    check(key in commands.COMMANDS, f"{name} is registered")
    cmd = commands.COMMANDS.get(key)
    if cmd is not None:
        check(cmd.name == name, f"{key} -> name is {name}")
        check([(p, k) for p, k in cmd.steps]
              == [(f"Select objects to {'show' if name == 'ShowSelected' else 'unlock'}:",
                   commands.OBJECTS_MULTI)],
              f"{name} PROMPTS for its objects (MEASURED research_all_13 #161/#182)")

print()
print("ShowSelected shows only the selected hidden one; Show still shows both")
ctx = FakeCtx()
ctx.add_input(seg(), "A")
ctx.add_input(seg(), "B")
ctx.select("A", "B")
fresh("hide").execute(ctx)
check(ctx.count_mode(r3.ObjectMode.Hidden) == 2, "both A and B are hidden")
ctx.select("A")                       # only A selected now
msg, done = fresh("showselected").execute(ctx)
check(done is True, "ShowSelected completes")
check(ctx.modes("A") == [r3.ObjectMode.Normal], "ShowSelected un-hid the selected A")
check(ctx.modes("B") == [r3.ObjectMode.Hidden],
      f"ShowSelected left the UNselected B hidden  ({ctx.modes('B')})")
check("Showed 1 object" in msg, f"it reports one shown  ({msg})")
check(ctx.changes[-1] == "ShowSelected",
      f"note_change fired for ShowSelected  ({ctx.changes})")

# the all-objects Show is UNCHANGED: it re-displays BOTH.
ctx = FakeCtx()
ctx.add_input(seg(), "A")
ctx.add_input(seg(), "B")
ctx.select("A", "B")
fresh("hide").execute(ctx)
msg, done = fresh("show").execute(ctx)
check(ctx.count_mode(r3.ObjectMode.Hidden) == 0,
      "Show (all-objects) re-displays BOTH -- unchanged by the selected-objects forms")
check(msg == "Showing 2 hidden objects.", f"Show reports both, the reference CAD application wording  ({msg})")

print()
print("UnlockSelected unlocks only the selected locked one; Unlock still both")
ctx = FakeCtx()
ctx.add_input(seg(), "A")
ctx.add_input(seg(), "B")
ctx.select("A", "B")
fresh("lock").execute(ctx)
check(ctx.count_mode(r3.ObjectMode.Locked) == 2, "both A and B are locked")
ctx.select("A")                       # selection survives the lock (the wrinkle)
msg, done = fresh("unlockselected").execute(ctx)
check(ctx.modes("A") == [r3.ObjectMode.Normal], "UnlockSelected unlocked the selected A")
check(ctx.modes("B") == [r3.ObjectMode.Locked],
      f"UnlockSelected left the UNselected B locked  ({ctx.modes('B')})")
check("Unlocked 1 object" in msg, f"it reports one unlocked  ({msg})")

# the all-objects Unlock is UNCHANGED: it releases BOTH.
ctx = FakeCtx()
ctx.add_input(seg(), "A")
ctx.add_input(seg(), "B")
ctx.select("A", "B")
fresh("lock").execute(ctx)
msg, done = fresh("unlock").execute(ctx)
check(ctx.count_mode(r3.ObjectMode.Locked) == 0,
      "Unlock (all-objects) releases BOTH -- unchanged by the selected-objects forms")
check(msg == "Unlocking 2 objects.", f"Unlock reports both, the reference CAD application wording  ({msg})")

print()
print("the selected-objects forms PROMPT (MEASURED research_all_13 #070/#161/#182)")
ctx = FakeCtx()
ctx.add_input(seg(), "A")             # present, but NOT selected, nothing hidden
c = fresh("showselected")
msg, done = c.begin(ctx)
check(done and msg == "No objects are hidden." and ctx.changes == [],
      f"ShowSelected with nothing hidden says so, no prompt (#161 item 1)  ({msg!r})")

for key, hide_or_lock, mode in (("showselected", "hide", r3.ObjectMode.Hidden),
                                ("unlockselected", "lock", r3.ObjectMode.Locked)):
    ctx = FakeCtx()
    ctx.add_input(seg(), "A")
    ctx.add_input(seg(), "B")
    fresh(hide_or_lock).execute(_select(ctx, "B"))
    ctx.changes.clear()
    ctx.select("A")                   # a VISIBLE / unlocked object is selected
    c = fresh(key)
    msg, done = c.begin(ctx)
    check(not done and c.prompt.startswith("Select objects to"),
          f"{key}: a normal selection does not answer it -- it prompts ({c.prompt!r})")
    msg, done = c.feed("", ctx)
    check(done and msg is None and ctx.changes == [] and ctx.modes("B") == [mode],
          f"{key}: Enter on a set with none in that state ends silently ({msg!r})")
    ctx.select()
    c = fresh(key)
    msg, done = c.begin(ctx)
    check(not done, f"{key}: nothing selected -- it prompts")
    msg, done = c.feed("", ctx)
    check(done and msg is None and ctx.modes("B") == [mode],
          f"{key}: an empty Enter ends silently ({msg!r})")
    c = fresh(key)
    c.begin(ctx)
    c.feed("B", ctx)                  # a hidden/locked object can be NAMED at the prompt
    msg, done = c.feed("", ctx)
    check(done and ctx.modes("B") == [r3.ObjectMode.Normal] and ctx.changes == [c.name],
          f"{key}: the named object is released ({msg!r})")

for key in ("hide", "lock"):
    ctx = FakeCtx()
    ctx.add_input(seg(), "A")
    c = fresh(key)
    c.begin(ctx)
    msg, done = c.feed("", ctx)
    check(done and msg is None and ctx.changes == [],
          f"{key}: Enter at the empty prompt ends silently (#070/#084)  ({msg!r})")

# ===========================================================================
# HideSwap and LockSwap -- exchange two Mode states across the whole document.
# ===========================================================================
N, H, L = r3.ObjectMode.Normal, r3.ObjectMode.Hidden, r3.ObjectMode.Locked


def swap_doc(ctx, states):
    """Add one object per (name, mode) and write its Mode straight to the table."""
    for name, mode in states:
        ctx.model.add_object(seg(), name)
    for fobj in ctx.model.file.Objects:
        for name, mode in states:
            if fobj.Attributes.Name == name:
                fobj.Attributes.Mode = mode
    return ctx


def doc_rows(ctx):
    """Everything the swap must leave alone, plus Mode, keyed by object id."""
    out = {}
    for f in ctx.model.file.Objects:
        a = f.Attributes
        out[str(a.Id)] = (a.Name, int(a.LayerIndex), list(a.GetGroupList2() or []),
                          f.Geometry.GetBoundingBox().Max.X, a.Mode)
    return out


print()
print("HideSwap and LockSwap are registered and zero-prompt")
for name in ("HideSwap", "LockSwap"):
    cmd = commands.COMMANDS.get(name.lower())
    check(cmd is not None and cmd.name == name, f"{name} is registered")
    check(cmd is not None and list(cmd.steps) == [], f"{name} has no prompts")

for key, other, other_mode in (("hideswap", "locked", L), ("lockswap", "hidden", H)):
    a_mode, b_mode = (N, H) if key == "hideswap" else (N, L)
    print()
    print(f"{key}: mixed document swaps both directions and leaves the third state alone")
    ctx = swap_doc(FakeCtx(), [("A", a_mode), ("A2", a_mode), ("B", b_mode),
                               ("C", other_mode)])
    group = r3.Group()
    group.Name = "G"
    ctx.model.file.Groups.Add(group)
    for f in ctx.model.file.Objects:
        if f.Attributes.Name == "A":
            f.Attributes.AddToGroup(0)
    ctx.select("A", "C")
    before = doc_rows(ctx)
    msg, done = fresh(key).execute(ctx)
    after = doc_rows(ctx)
    check(done is True, f"{key} completes")
    check(ctx.modes("A") == [b_mode] and ctx.modes("A2") == [b_mode],
          f"{key}: both Normal objects moved to the other state ({ctx.modes('A')}, {ctx.modes('A2')})")
    check(ctx.modes("B") == [a_mode], f"{key}: the swapped-state object became Normal ({ctx.modes('B')})")
    check(ctx.modes("C") == [other_mode], f"{key}: the {other} object is untouched ({ctx.modes('C')})")
    check(set(before) == set(after)
          and all(before[k][:4] == after[k][:4] for k in before),
          f"{key}: ids, names, layers, groups and geometry are unchanged")
    check(after[[k for k in after if after[k][0] == "A"][0]][2] == [0],
          f"{key}: group membership survives")
    check(ctx._selected == ["A", "C"], f"{key}: the selection records are untouched")
    check(ctx.changes == [fresh(key).name], f"{key}: one note_change before mutating ({ctx.changes})")
    check(isinstance(msg, str) and "2 objects" in msg and "1 object" in msg,
          f"{key}: the message counts both directions ({msg!r})")

    print()
    print(f"{key}: nothing eligible is a reported no-op with no undo record")
    for states in ([], [("C", other_mode)]):
        ctx = swap_doc(FakeCtx(), states)
        msg, done = fresh(key).execute(ctx)
        check(done and ctx.changes == [] and "No" in (msg or ""),
              f"{key} on {[m for _n, m in states]}: no change, says so ({msg!r})")
        check(ctx.modes("C") == ([other_mode] if states else []),
              f"{key}: the {other} object stays {other}")

    print()
    print(f"{key}: only one direction available")
    ctx = swap_doc(FakeCtx(), [("A", a_mode)])
    fresh(key).execute(ctx)
    check(ctx.modes("A") == [b_mode], f"{key}: a lone Normal object is swapped ({ctx.modes('A')})")
    ctx = swap_doc(FakeCtx(), [("B", b_mode)])
    fresh(key).execute(ctx)
    check(ctx.modes("B") == [a_mode], f"{key}: a lone swapped-state object returns ({ctx.modes('B')})")

    print()
    print(f"{key}: objects sharing a name are swapped from their own state")
    ctx = FakeCtx()
    ctx.model.add_object(seg(), "Dup")
    ctx.model.add_object(seg(), "Dup")
    list(ctx.model.file.Objects)[1].Attributes.Mode = b_mode
    fresh(key).execute(ctx)
    check(sorted(int(m) for m in ctx.modes("Dup")) == sorted([int(a_mode), int(b_mode)]),
          f"{key}: one Dup each way, not both set alike ({ctx.modes('Dup')})")

    print()
    print(f"{key}: one Undo record, exact Redo, and the file round-trip (real context)")
    ctx = real_ctx([])
    swap_doc(ctx, [("A", a_mode), ("B", b_mode), ("C", other_mode)])
    start = doc_rows(ctx)
    depth = len(ctx.undo._undo)
    cmd = fresh(key)
    cmd.begin(ctx)
    swapped = doc_rows(ctx)
    check(len(ctx.undo._undo) == depth + 1, f"{key}: exactly one Undo record is added")
    check([swapped[k][4] for k in sorted(swapped)]
          == [{a_mode: b_mode, b_mode: a_mode}.get(start[k][4], start[k][4]) for k in sorted(start)],
          f"{key}: begin() runs the swap")
    ctx.undo_last()
    check(doc_rows(ctx) == start, f"{key}: ONE Undo restores every Mode exactly")
    ctx.redo_last()
    check(doc_rows(ctx) == swapped, f"{key}: Redo reproduces the swap exactly")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, f"{key}.3dm")
        saved = ctx.model.save(path)
        back = {}
        if saved:
            for f in r3.File3dm.Read(path).Objects:
                back[str(f.Attributes.Id)] = (f.Attributes.Name, f.Attributes.Mode)
        check(saved and back == {k: (v[0], v[4]) for k, v in swapped.items()},
              f"{key}: the swapped Mode survives save and reload ({back})")

print()
print("=" * 60)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"VISIBILITY TESTS PASSED ({checks} checks)")
