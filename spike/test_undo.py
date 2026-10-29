#!/usr/bin/env python3
"""Undo / redo -- the document history.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_undo.py

WHY THIS EXISTS
---------------
The first Gumball implementation let objects be dragged -- and there was **no way
to take a drag back**. `Esc` mid-drag could not revert it either, because no
document history existed to revert to. Every drag was permanent.

WHAT IS ASSERTED
----------------
Two layers, separately:

* `narwhal3d/undo.py` -- the pure history machine. Snapshots are opaque to it,
  so these checks use plain strings and never touch rhino3dm.
* `narwhal3d/model.py` -- `snapshot()`/`restore()` on a real File3dm, where the
  thing that matters is that object **ids survive** (the Blender display mapping
  and the selection are keyed on them; a restore that minted fresh ids would
  orphan every display object).

The reference behavior follows the documented undo command, general options,
and undo-record API contracts.
"""
import importlib.util
import os
import sys
import types

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


undo = _load("narwhal3d.undo", "narwhal3d/undo.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")

import rhino3dm as r3

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


# ===========================================================================
print("The pure history machine: records are named, grouped and lazy")
# ===========================================================================

s = undo.UndoStack()
check(not s.can_undo and not s.can_redo, "a fresh stack has nothing to undo or redo")
check(not s.recording, "a fresh stack is not recording")
# Reference API: CurrentUndoRecordSerialNumber is ">0: active ... 0: [not]"
check(s.current_serial == undo.NO_RECORD,
      "with no record open the current serial is 0 (the documented sentinel)")

# --- a command that changes nothing leaves NO undo step -------------------
s.begin("Zoom")
check(s.recording, "begin() starts recording")
check(s.current_serial > 0, f"an open record has a positive serial ({s.current_serial})")
pushed = s.end()
check(not pushed, "a record that captured nothing is discarded")
check(not s.can_undo, "so Zoom leaves no undo step")

# --- a command that does change something leaves exactly one -------------
s.begin("Circle")
s.capture("doc:empty")
check(s.captured, "capture() marks the record as having something to undo")
check(s.end(), "a record that captured something IS pushed")
check(s.can_undo and s.undo_name == "Circle",
      f"one undo step, named for the command ({s.undo_name!r})")

# --- capture is once-per-record: the FIRST state is the 'before' ---------
s2 = undo.UndoStack()
s2.begin("Polyline")
s2.capture("doc:A")
s2.capture("doc:B")          # a later mutation in the same command
s2.end()
rec = s2.undo("doc:C")
check(rec.before == "doc:A",
      f"the first capture wins -- later ones are mid-command states ({rec.before!r})")

# --- nesting joins one record, so one action is one step -----------------
s3 = undo.UndoStack()
serial = s3.begin("Boolean")
inner = s3.begin("Boolean")
check(serial == inner, "a nested begin() returns the SAME serial (one record)")
s3.capture("doc:before")
check(not s3.end(), "closing the inner level does not push yet")
check(s3.recording, "still recording after the inner close")
check(s3.end(), "closing the outer level pushes once")
check(len([n for n in s3.undo_names()]) == 1,
      f"nesting produced ONE undo step, not two ({s3.undo_names()})")


# ===========================================================================
print()
print("Undo and redo move through history exactly")
# ===========================================================================

s = undo.UndoStack()
for step, state in (("Circle", "doc:0"), ("Box", "doc:1"), ("Move", "doc:2")):
    s.begin(step)
    s.capture(state)
    s.end()
check(s.undo_names() == ["Move", "Box", "Circle"],
      f"undo_names lists most-recent-first, as UndoMultiple does ({s.undo_names()})")

# undo the Move: the document goes back to doc:2 (its 'before')
rec = s.undo("doc:3")
check(rec is not None and rec.name == "Move", "undo returns the Move record")
check(rec.before == "doc:2", "the caller restores the record's 'before'")
check(rec.after == "doc:3",
      "the state at undo time is kept as 'after', so it can be redone")
check(s.can_redo and s.redo_name == "Move",
      f"Move is now redoable ({s.redo_name!r})")

# redo it
rec = s.redo("doc:2")
check(rec is not None and rec.name == "Move", "redo returns the Move record")
check(rec.after == "doc:3", "redo restores the record's 'after'")
check(s.can_undo and s.undo_name == "Move", "and Move is undoable again")

# a full undo/redo/undo cycle must be exact
rec1 = s.undo("doc:3")
rec2 = s.redo(rec1.before)
rec3 = s.undo(rec2.after)
check(rec3.before == "doc:2",
      f"undo/redo/undo returns to the same state ({rec3.before!r})")

# walk all the way back
s.undo("x"); s.undo("y")
check(not s.can_undo, "undoing every step empties the undo stack")
check(s.undo("z") is None, "undo with nothing left returns None")
check(len(s.redo_names()) == 3, f"all three are redoable ({s.redo_names()})")

# --- a NEW change invalidates the redo chain -----------------------------
# commands/undo.htm: "Redo is available immediately after you use Undo."
s.begin("Sphere")
s.capture("doc:new")
s.end()
check(not s.can_redo,
      "a fresh change clears the redo chain (Redo follows Undo, not a new edit)")

# --- an empty record must NOT clear redo ---------------------------------
s4 = undo.UndoStack()
s4.begin("Circle"); s4.capture("a"); s4.end()
s4.undo("b")
check(s4.can_redo, "precondition: something is redoable")
s4.begin("Zoom"); s4.end()          # captured nothing
check(s4.can_redo,
      "a command that changed nothing leaves the redo chain intact")


# ===========================================================================
print()
print("Safety: no history that describes a document that never existed")
# ===========================================================================

s = undo.UndoStack()
s.begin("Circle"); s.capture("a"); s.end()
s.begin("Box")                     # still recording
check(s.undo("mid") is None, "undo is refused while a record is open")
check(s.redo("mid") is None, "redo is refused while a record is open")
s.end()

# a command that raises must not leave a half-described change behind
s5 = undo.UndoStack()
s5.begin("Loft"); s5.capture("a")
s5.abandon()
check(not s5.can_undo, "abandon() throws the open record away")
check(not s5.recording, "abandon() stops recording")

# Recording can be switched off (the reference UndoRecordingEnabled API).
s6 = undo.UndoStack()
s6.enabled = False
check(s6.begin("Circle") == undo.NO_RECORD,
      "with recording disabled begin() returns the 0 sentinel")
check(not s6.recording, "and nothing is recorded")

# the limit discards the OLDEST, never the newest
s7 = undo.UndoStack(limit=3)
for i in range(5):
    s7.begin(f"C{i}"); s7.capture(f"doc:{i}"); s7.end()
check(s7.undo_names() == ["C4", "C3", "C2"],
      f"at limit 3 the oldest records fall off the bottom ({s7.undo_names()})")

# Clear wipes everything (the reference ClearUndoRecords API).
s7.clear()
check(not s7.can_undo and not s7.can_redo, "clear() drops all history")

# serials keep increasing, so a record is always identifiable
s8 = undo.UndoStack()
first = s8.begin("A"); s8.end()
second = s8.begin("B"); s8.end()
check(second > first, f"serial numbers increase ({first} -> {second})")


# ===========================================================================
print()
print("The document: a snapshot restores ids, names and geometry")
# ===========================================================================

m = model.NarwhalModel()
id1, _ = m.add_object(r3.Circle(2.0).ToNurbsCurve(), "Circle01")
id2, _ = m.add_object(r3.Circle(5.0).ToNurbsCurve(), "Circle02")
m.register_blender_object(id1, "Circle01")
snap = m.snapshot()
check(len(snap) == 2, f"a snapshot covers every object ({len(snap)})")

# mutate hard: add one, delete another
m.add_object(r3.Circle(9.0).ToNurbsCurve(), "Circle03")
m.remove_object(id1)
check(m.object_count() == 2, "precondition: the document really changed")

m.restore(snap)
ids = sorted(str(f.Attributes.Id) for f in m.file.Objects)
names = sorted((f.Attributes.Name or "") for f in m.file.Objects)
check(m.object_count() == 2, f"restore puts the object count back ({m.object_count()})")
check(ids == sorted([id1, id2]),
      "restore PRESERVES object ids -- the display mapping stays valid")
check(names == ["Circle01", "Circle02"], f"names come back too ({names})")
check(m.blender_name_for(id1) == "Circle01",
      "the Blender display mapping is restored with it")

# the geometry itself, not just the bookkeeping
spans = sorted(round(f.Geometry.GetBoundingBox().Max.X
                     - f.Geometry.GetBoundingBox().Min.X, 6)
               for f in m.file.Objects)
check(spans == [4.0, 10.0], f"the restored geometry has its original size ({spans})")

# --- a snapshot must be reusable: undo/redo/undo restores it repeatedly ---
m.restore(snap)
m.restore(snap)
check(m.object_count() == 2,
      "restoring the same snapshot twice is stable (undo/redo/undo needs this)")

# --- a transform is undone, not merely forgotten -------------------------
m2 = model.NarwhalModel()
oid, _ = m2.add_object(r3.Circle(2.0).ToNurbsCurve(), "C")
before = m2.snapshot()
m2.file.Objects[0].Geometry.Transform(r3.Transform.Translation(10.0, 0.0, 0.0))
moved = m2.file.Objects[0].Geometry.GetBoundingBox()
check(moved.Min.X > 5.0, f"precondition: the object moved (min X {moved.Min.X:.1f})")
m2.restore(before)
back = m2.file.Objects[0].Geometry.GetBoundingBox()
check(abs(back.Min.X + 2.0) < 1e-6,
      f"restoring undoes a baked transform (min X back to {back.Min.X:.3f})")
check(str(m2.file.Objects[0].Attributes.Id) == oid,
      "and the id is unchanged, so the same display object still matches")

# an empty document snapshots and restores cleanly
m3 = model.NarwhalModel()
empty = m3.snapshot()
m3.add_object(r3.Circle(1.0).ToNurbsCurve(), "X")
m3.restore(empty)
check(m3.object_count() == 0,
      "undoing the very first object returns to an empty document")
check(m3.restore(None) == 0, "restore(None) is a no-op, not a crash")


# ===========================================================================
print()
print("End to end: real commands, driven as a user drives them")
# ===========================================================================
# This is the check that matters most, and the one that caught a real bug: the
# `Undo` command was itself being wrapped in an undo record, so the stack's
# "cannot undo while recording" guard refused every Undo. Testing the stack alone
# would never have found it: testing a helper is not
# testing its caller.

import copy

for name, relpath in (("cplane", "narwhal3d/cplane.py"),
                      ("coords", "narwhal3d/coords.py"),
                      ("osnap", "narwhal3d/osnap.py"),
                      ("options", "narwhal3d/options.py"),
                      ("bridge", "narwhal3d/bridge.py")):
    if f"narwhal3d.{name}" not in sys.modules:
        _load(f"narwhal3d.{name}", relpath)
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")


class HeadlessCtx(commands.NarwhalContext):
    """The real NarwhalContext with the two bpy-dependent methods stubbed.

    `add_geometry` mirrors the real one's undo hook exactly (`note_change` before
    the mutation); everything else -- the stack, the records, undo_last/redo_last
    -- is the production code path.
    """

    def __init__(self):
        super().__init__(model.NarwhalModel())

    def add_geometry(self, geom, base_name, layer_index=0):
        self.note_change(base_name)
        display = self.next_name(base_name)
        obj_id, _ = self.model.add_object(geom, display, layer_index)
        return obj_id, display

    def rebuild_display(self):
        pass

    def selected_objects(self):
        return []


def drive(ctx, key, lines):
    cmd = copy.deepcopy(commands.COMMANDS[key])
    cmd.begin(ctx)
    for line in lines:
        cmd.feed(line, ctx)
    return cmd


def run_zero_step(ctx, key):
    """A zero-step command (Undo/Redo) runs on its naming Enter."""
    return copy.deepcopy(commands.COMMANDS[key]).begin(ctx)[0]


ctx = HeadlessCtx()
check("undo" in commands.COMMANDS and "redo" in commands.COMMANDS,
      "Undo and Redo are registered commands")
check(commands.UndoCmd.records_undo is False,
      "UndoCmd opts out of recording (it navigates history, not changes it)")
check(commands.RedoCmd.records_undo is False, "RedoCmd likewise")
check(commands.CircleCmd.records_undo is True,
      "an ordinary drawing command DOES record")

drive(ctx, "circle", ["0,0", "2"])
drive(ctx, "rectangle", ["0,0", "4,2"])
drive(ctx, "polyline", ["0,0", "1,0", "2,2", ""])
check(ctx.model.object_count() == 3, "three commands built three objects")
check(ctx.undo.undo_names() == ["Polyline", "Rectangle", "Circle"],
      f"each command is ONE undo step, named for itself ({ctx.undo.undo_names()})")

# a command that refuses its input must not leave a step behind
before = list(ctx.undo.undo_names())
drive(ctx, "circle", ["0,0", "0"])          # radius 0 -> refused
check(ctx.model.object_count() == 3 and ctx.undo.undo_names() == before,
      "a Circle refused for a zero radius leaves no undo step")

# walk all the way back
msgs = [run_zero_step(ctx, "undo") for _ in range(3)]
check(msgs == ["Undid Polyline", "Undid Rectangle", "Undid Circle"],
      f"Undo reports what it reversed, newest first ({msgs})")
check(ctx.model.object_count() == 0,
      f"undoing everything empties the document ({ctx.model.object_count()})")
check(run_zero_step(ctx, "undo") == "Nothing to undo",
      "and one more Undo says so rather than failing silently")

# and forward again
msgs = [run_zero_step(ctx, "redo") for _ in range(3)]
check(msgs == ["Redid Circle", "Redid Rectangle", "Redid Polyline"],
      f"Redo replays in the original order ({msgs})")
check(ctx.model.object_count() == 3, "redoing everything rebuilds the document")
check(run_zero_step(ctx, "redo") == "Nothing to redo", "then nothing is left to redo")

# undo / redo / undo must land on the same document
run_zero_step(ctx, "undo")
n1 = ctx.model.object_count()
run_zero_step(ctx, "redo")
run_zero_step(ctx, "undo")
check(ctx.model.object_count() == n1,
      f"undo/redo/undo returns to the same object count ({n1})")

# a new change invalidates the redo chain (commands/undo.htm)
check(ctx.undo.can_redo, "precondition: something is redoable")
drive(ctx, "circle", ["9,9", "1"])
check(not ctx.undo.can_redo,
      "drawing something new clears the redo chain")


# ===========================================================================
print()
print("Undo keeps the selection (rebuild_display destroys every Blender object)")
# ===========================================================================
# `rebuild_display` deletes and recreates EVERY Blender object, so the selection
# does not survive it. Undoing a Gumball drag therefore made the widget vanish,
# because nothing was selected any more -- the reference application keeps it selected after
# undoing a change to it. `_restore_document` captures the selected names and
# re-selects them. Headless there is no bpy, so what is asserted here is that the
# path exists, is the one undo/redo use, and degrades cleanly with no selection.

check(hasattr(commands.NarwhalContext, "_restore_document"),
      "undo/redo restore through one helper, so the fix cannot be half-applied")
check(hasattr(commands.NarwhalContext, "select_by_names"),
      "and there is a by-name selection call for it to use")


class RecordingCtx(HeadlessCtx):
    """Records what the restore path asked to re-select."""

    def __init__(self):
        super().__init__()
        self.selection = ["Circle1"]
        self.reselected = None
        self.rebuilt = 0

    def selected_objects(self):
        class _Ob:
            def __init__(self, name):
                self.name = name
        return [_Ob(n) for n in self.selection]

    def rebuild_display(self):
        self.rebuilt += 1

    def select_by_names(self, names):
        self.reselected = list(names)
        return self.reselected


rctx = RecordingCtx()
drive(rctx, "circle", ["0,0", "2"])
drive(rctx, "circle", ["5,0", "1"])
name = rctx.undo_last()
check(name == "Circle", f"the undo ran ({name!r})")
check(rctx.rebuilt >= 1, "undo rebuilt the display")
check(rctx.reselected == ["Circle1"],
      f"undo re-selected what was selected before it ({rctx.reselected})")

# redo must preserve it too
rctx.reselected = None
rctx.redo_last()
check(rctx.reselected == ["Circle1"],
      f"redo re-selects as well ({rctx.reselected})")

# and with nothing selected it must not try to select anything
rctx.selection = []
rctx.reselected = None
rctx.undo_last()
check(rctx.reselected is None,
      "with nothing selected, undo does not invent a selection")


# ===========================================================================
print()
print("Copy-on-write undo records: only what changed is copied, and undo is exact")
# ===========================================================================
# MEASURED on a 5,702-object document: every undo record copied the whole document
# (~270 ms per Delete / Move / Gumball release) and Undo re-added all of it (then the
# display rebuilt all 5,702 objects: 32 s). Now a snapshot shares every entry of an
# object unchanged since the previous one, which is only right if EVERY way of changing
# a document object in place is noticed. Each kind of change below must (a) make the next
# snapshot copy exactly the changed object, (b) undo to a document EXACTLY equal to the
# one before, (c) touch only that object on restore, and (d) redo exactly.

bridge = sys.modules["narwhal3d.bridge"]


def geo_sig(g):
    """Everything a geometry IS, exactly (no rounding): user strings, and the control
    points, weights, knots, degree and domain of a curve / the vertices, faces and their
    orientation of a brep. Not the raw `Encode()` bytes: MEASURED, `Duplicate()` bumps an
    openNURBS user-data copy counter (so every copy of a tagged object differs by a byte --
    the old full restore re-added such copies too) and `Brep.GetBoundingBox` fills a
    cached box."""
    us = tuple(sorted(g.GetUserStrings()))
    if isinstance(g, r3.Curve):
        n = g.ToNurbsCurve()
        pts = tuple((p.X, p.Y, p.Z, p.W) for p in (n.Points[i] for i in range(len(n.Points))))
        knots = tuple(n.Knots[i] for i in range(len(n.Knots)))
        return (type(g).__name__, us, n.Degree, n.IsPeriodic, pts, knots,
                g.Domain.T0, g.Domain.T1)
    if isinstance(g, r3.Brep):
        vs = tuple((v.Location.X, v.Location.Y, v.Location.Z)
                   for v in (g.Vertices[i] for i in range(len(g.Vertices))))
        flips = tuple(g.Faces[i].OrientationIsReversed for i in range(len(g.Faces)))
        return ("Brep", us, len(g.Faces), len(g.Edges), vs, flips)
    if isinstance(g, r3.Point):
        p = g.Location
        return ("Point", us, p.X, p.Y, p.Z)
    return (type(g).__name__, us, g.Encode()["data"])


def doc_state(m):
    """An id-keyed description of the whole document: the snapshot attributes and the
    exact geometry (`geo_sig`)."""
    out = {}
    for f in m.file.Objects:
        a = f.Attributes
        out[str(a.Id)] = (a.Name, int(a.LayerIndex), int(a.Mode), bool(a.Visible),
                          tuple(a.ObjectColor), str(a.ColorSource), int(a.MaterialIndex),
                          str(a.MaterialSource), geo_sig(f.Geometry))
    return out


def fobj_of(m, oid):
    return m.file.Objects.FindId(oid)


cw = model.NarwhalModel()
cw.add_layer("Second")
# a REAL material: MEASURED, File3dmObjectTable.Add resets a MaterialIndex that names no
# material to -1, so an index into an empty table cannot round-trip (old restore alike)
MAT = cw.file.Materials.Add(r3.Material())
c_ids = {}
for nm, g in (("Circ", r3.Circle(r3.Point3d(0, 0, 0), 2.0).ToNurbsCurve()),
              ("Line", r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(5, 0, 0))),
              ("Poly", r3.PolylineCurve([r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0),
                                         r3.Point3d(1, 1, 0)])),
              ("Pt", r3.Point(r3.Point3d(3, 3, 3))),
              ("Box", r3.Brep.CreateFromBox(r3.Box(r3.BoundingBox(r3.Point3d(0, 0, 0),
                                                           r3.Point3d(1, 2, 3)))))):
    c_ids[nm], _a = cw.add_object(g, nm)
joined = r3.PolyCurve()
joined.Append(r3.LineCurve(r3.Point3d(0, 5, 0), r3.Point3d(2, 5, 0)))
joined.Append(r3.LineCurve(r3.Point3d(2, 5, 0), r3.Point3d(2, 7, 0)))
c_ids["Joined"], _a = cw.add_object(joined, "Joined")
for oid in c_ids.values():
    cw.register_blender_object(oid, "display-" + oid[:4])

check(model._WATCH["installed"] and not model._WATCH["error"],
      f"rhino3dm is instrumented for change tracking ({model._WATCH})")
first = cw.snapshot()
check(cw.last_snapshot == ("full", len(c_ids)),
      f"the first snapshot copies everything ({cw.last_snapshot})")
again = cw.snapshot()
check(cw.last_snapshot == ("shared", 0) and all(again.by_id[k] is first.by_id[k]
                                                for k in first.by_id),
      f"with nothing changed the next snapshot copies NOTHING and shares every entry "
      f"({cw.last_snapshot})")


def held_geometry():
    # a wrapper taken BEFORE the record starts and mutated after -- what the Gumball does
    # with the rows `ui._doc_rows` caches
    return fobj_of(cw, c_ids["Line"]).Geometry


HELD = held_geometry()


def change_cases():
    xf = r3.Transform.Translation(4.0, 0.0, 0.0)
    return [
        ("add an object", None,
         lambda: cw.add_object(r3.LineCurve(r3.Point3d(9, 9, 0), r3.Point3d(9, 10, 0)),
                               "Added")),
        ("delete an object", "Pt", lambda: cw.remove_object(c_ids["Pt"])),
        ("replace_geometry", "Circ",
         lambda: cw.replace_geometry(c_ids["Circ"], r3.Circle(7.0).ToNurbsCurve())),
        ("bridge.transform_geometry in place", "Circ",
         lambda: bridge.transform_geometry(fobj_of(cw, c_ids["Circ"]).Geometry, xf)),
        ("a bare .Transform on the live geometry", "Box",
         lambda: fobj_of(cw, c_ids["Box"]).Geometry.Transform(xf)),
        ("a wrapper held from BEFORE the snapshot, moved after", "Line",
         lambda: HELD.Translate(r3.Vector3d(0, 3, 0))),
        ("a control point (bridge.set_control_point)", "Circ",
         lambda: bridge.set_control_point(fobj_of(cw, c_ids["Circ"]).Geometry, 1,
                                          (5.0, 5.0, 0.0))),
        ("NurbsCurve.Points[i] = ...", "Circ",
         lambda: fobj_of(cw, c_ids["Circ"]).Geometry.Points.__setitem__(
             0, r3.Point4d(1.0, 2.0, 3.0, 1.0))),
        ("a PolyCurve SEGMENT (a live view) moved", "Joined",
         lambda: fobj_of(cw, c_ids["Joined"]).Geometry.SegmentCurve(1).Translate(
             r3.Vector3d(0, 0, 2))),
        ("Curve.Reverse (Dir)", "Poly",
         lambda: fobj_of(cw, c_ids["Poly"]).Geometry.Reverse()),
        ("Brep.Flip", "Box", lambda: fobj_of(cw, c_ids["Box"]).Geometry.Flip()),
        ("Point.Location = (a property setter)", "Pt",
         lambda: setattr(fobj_of(cw, c_ids["Pt"]).Geometry, "Location",
                         r3.Point3d(8, 8, 8))),
        ("a user string on the geometry (kernel tags, recipes, master keys)", "Line",
         lambda: fobj_of(cw, c_ids["Line"]).Geometry.SetUserString("Narwhal3D:t", "x")),
        ("Attributes.Mode = Hidden (Hide)", "Poly",
         lambda: setattr(fobj_of(cw, c_ids["Poly"]).Attributes, "Mode",
                         r3.ObjectMode.Hidden)),
        ("Attributes.LayerIndex (ChangeLayer)", "Line",
         lambda: setattr(fobj_of(cw, c_ids["Line"]).Attributes, "LayerIndex", 1)),
        ("Attributes.Name (Rename)", "Box",
         lambda: setattr(fobj_of(cw, c_ids["Box"]).Attributes, "Name", "Renamed")),
        ("set_object_material (Picture)", "Joined",
         lambda: cw.set_object_material(c_ids["Joined"], MAT)),
    ]


def table_order(m):
    return [str(f.Attributes.Id) for f in m.file.Objects]


for label, key, change in change_cases():
    pre = cw.snapshot()
    state_pre = doc_state(cw)
    # the document's LOGICAL order -- the table itself may lag it after an undo, until
    # `normalize_order` (which `save` runs); see the checks below
    order_pre = list(pre.by_id)
    n_pre = cw.object_count()
    change()
    state_post = doc_state(cw)
    check(state_post != state_pre, f"{label}: precondition -- the document changed")
    post = cw.snapshot()
    changed = sorted(k for k in set(state_pre) | set(state_post)
                     if state_pre.get(k) != state_post.get(k))
    want_copies = sum(1 for k in changed if k in state_post)
    check(cw.last_snapshot == ("shared", want_copies),
          f"{label}: the undo record copied ONLY the changed object "
          f"({cw.last_snapshot}, want {want_copies})")
    unchanged = [k for k in state_pre if k in state_post and k not in changed]
    check(all(post.by_id[k] is pre.by_id[k] for k in unchanged),
          f"{label}: every unchanged object's entry is SHARED with the previous record")
    cw.restore(pre)
    check(doc_state(cw) == state_pre and cw.object_count() == n_pre,
          f"{label}: UNDO restores the document exactly")
    # RE-EXPRESSED: the restore no longer re-adds every object AFTER a restored one to
    # put the physical table back in order (MEASURED: ~5,600 re-adds, ~250 ms, undoing
    # an early Delete). The document's LOGICAL order (its snapshot) is
    # exact at once; the object TABLE is re-ordered by `normalize_order`, which `save`
    # runs -- so the saved file keeps logical order (an undeleted object in its place).
    check(list(cw.snapshot().by_id) == order_pre,
          f"{label}: ...in the same LOGICAL order at once")
    cw.normalize_order()
    check(table_order(cw) == order_pre and not cw.order_pending(),
          f"{label}: ...and the object TABLE in the same order after normalize_order")
    lr = cw.last_restore or {}
    gone = sorted(lr.get("removed", {}))
    made_ = sorted(lr.get("added", []))
    want_gone = [k for k in changed if k in state_post]   # their display goes...
    want_made = [k for k in changed if k in state_pre]    # ...and these are re-made
    check(cw.last_restore is not None and gone == want_gone and made_ == want_made,
          f"{label}: the restore reports exactly the changed object -- display to remove "
          f"{gone} (want {want_gone}), to make {made_} (want {want_made})")
    cw.restore(post)
    check(doc_state(cw) == state_post, f"{label}: REDO restores the changed document exactly")
    cw.restore(pre)
    HELD = held_geometry()

# geometry that is NOT in the document: changing it must not copy anything
cw.snapshot()
free = r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 1, 1))
free.Translate(r3.Vector3d(1, 0, 0))
free.SetUserString("k", "v")
dup = fobj_of(cw, c_ids["Circ"]).Geometry.Duplicate()
dup.Transform(r3.Transform.Translation(0, 0, 9))
cw.snapshot()
check(cw.last_snapshot == ("shared", 0),
      f"new geometry (built, or a Duplicate of a document object) changed outside the "
      f"document copies nothing ({cw.last_snapshot})")

# a wrapper of an object that was then DELETED: changing it is harmless
gone = fobj_of(cw, c_ids["Line"]).Geometry
pre = cw.snapshot()
cw.remove_object(c_ids["Line"])
gone.Translate(r3.Vector3d(0, 0, 1))
mid = cw.snapshot()
cw.restore(pre)
check(c_ids["Line"] in doc_state(cw) and cw.object_count() == len(c_ids),
      "changing a wrapper of a deleted object neither crashes nor leaks into the undo")

# the safety net: a change nobody reported is caught by the object count, and a new
# File3dm is never shared with
cw.snapshot()
real_mark = model._mark
model._mark = lambda *a, **k: None
try:
    cw.remove_object(c_ids["Pt"])
finally:
    model._mark = real_mark
cw.snapshot()
check(cw.last_snapshot[0] == "full",
      f"an UNREPORTED deletion is caught (count check) and the record copies everything "
      f"({cw.last_snapshot})")
cw._round_trip()
cw.snapshot()
check(cw.last_snapshot[0] == "full",
      f"a replaced File3dm (layer delete's round trip) is never shared with "
      f"({cw.last_snapshot})")

# undo / redo through the real stack, many steps deep, lands on exact states
hist = HeadlessCtx()
states = [doc_state(hist.model)]
for line in (["circle", ["0,0", "2"]], ["circle", ["5,0", "1"]],
             ["rectangle", ["0,0", "4,2"]]):
    drive(hist, line[0], line[1])
    states.append(doc_state(hist.model))
fo = next(iter(hist.model.file.Objects))
hist.note_change("Move")
bridge.transform_geometry(fo.Geometry, r3.Transform.Translation(1.0, 1.0, 0.0))
states.append(doc_state(hist.model))
hist.note_change("Hide")
fo.Attributes.Mode = r3.ObjectMode.Hidden
states.append(doc_state(hist.model))
ok_back = True
for i in range(len(states) - 1, 0, -1):
    hist.undo_last()
    ok_back &= doc_state(hist.model) == states[i - 1]
ok_fwd = True
for i in range(1, len(states)):
    hist.redo_last()
    ok_fwd &= doc_state(hist.model) == states[i]
check(ok_back and ok_fwd,
      f"through the real undo stack: {len(states) - 1} steps (draw, draw, draw, move, "
      f"hide) undone one by one and redone, every state EXACT")

# --- the instrumentation is complete: nothing reachable was left unwatched ---------
missed = []
for cname, cls in vars(r3).items():
    if not isinstance(cls, type) or hasattr(cls, "__members__"):
        continue
    if cname in model._VALUE_CLASSES or cname.startswith("File3dm"):
        continue
    for name, attr in vars(cls).items():
        kind = type(attr).__name__
        if kind == "property":
            if attr.fset is not None and not getattr(attr.fset, "_narwhal_watch", False):
                missed.append(f"{cname}.{name} (setter)")
            if (attr.fget is not None and not model._returns_plain(attr.fget)
                    and not getattr(attr.fget, "_narwhal_watch", False)):
                missed.append(f"{cname}.{name} (getter returning a wrapper)")
        elif getattr(attr, "_narwhal_watch", False):
            continue
        elif kind == "instancemethod" and name not in (
                "__init__", "__new__", "__repr__", "__str__", "__eq__", "__ne__",
                "__hash__", "__len__", "__bool__", "__getstate__", "__setstate__",
                "_pybind11_conduit_v1_"):
            if not model._is_pure(name):
                missed.append(f"{cname}.{name} (mutator)")
            elif name not in model._FRESH_RESULT and not model._returns_plain(attr):
                missed.append(f"{cname}.{name} (may return a live view)")
check(not missed, f"every rhino3dm mutator and view-returning call is instrumented "
                  f"({len(missed)} missed: {missed[:6]})")
KNOWN_IN_PLACE = ("Transform", "Translate", "Rotate", "Scale", "Reverse", "Flip",
                  "SetPoint", "SetStartPoint", "SetEndPoint", "SetUserString",
                  "ChangeClosedCurveSeam", "ChangeDimension", "MakeDeformable",
                  "MakeRational", "MakeNonRational", "IncreaseDegree", "InsertKnot",
                  "SetDomain", "Reparameterize", "Append", "AppendSegment",
                  "RemoveNesting", "Compact", "Clear", "Add", "__setitem__",
                  "MakePiecewiseBezier", "ChangeEndWeights", "SetWeight")
check(not [n for n in KNOWN_IN_PLACE if model._is_pure(n)],
      "rhino3dm's in-place edits are all classified as mutators "
      f"({[n for n in KNOWN_IN_PLACE if model._is_pure(n)]} misfiled)")
# every zero-argument call classified READ-ONLY really leaves the object unchanged
samples = [r3.Circle(2.0).ToNurbsCurve(), joined.Duplicate(),
           r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0)),
           r3.Brep.CreateFromBox(r3.Box(r3.BoundingBox(r3.Point3d(0, 0, 0), r3.Point3d(1, 1, 1)))),
           r3.Point(r3.Point3d(1, 2, 3))]
changed_by_pure = []
n_called = 0
for g in samples:
    for klass in type(g).__mro__:
        for name, attr in vars(klass).items():
            if type(attr).__name__ != "instancemethod" or name.startswith("_"):
                continue
            if not model._is_pure(name):
                continue
            before_sig = geo_sig(g)
            try:
                getattr(g, name)()
            except Exception:  # noqa: BLE001
                continue          # needs arguments
            n_called += 1
            if geo_sig(g) != before_sig:
                changed_by_pure.append(f"{type(g).__name__}.{name}")
# `_FRESH_RESULT` calls are not followed (their results are not registered with the
# document object): that is only right if the result really is a COPY
fr = r3.File3dm()
fr_ids = [str(fr.Objects.Add(g, r3.ObjectAttributes())) for g in (
    joined.Duplicate(), r3.Circle(2.0).ToNurbsCurve(),
    r3.Brep.CreateFromBox(r3.Box(r3.BoundingBox(r3.Point3d(0, 0, 0), r3.Point3d(1, 1, 1)))),
    r3.Extrusion.Create(r3.Circle(1.0).ToNurbsCurve(), 2.0, True))]


def fr_geom(i):
    return fr.Objects.FindId(fr_ids[i]).Geometry


fresh_calls = {
    "Explode": (0, lambda: fr_geom(0).Explode()),
    "Split": (1, lambda: fr_geom(1).Split(1.0)),
    "Trim": (1, lambda: [fr_geom(1).Trim(0.5, 2.0)]),
    "ToNurbsCurve": (0, lambda: [fr_geom(0).ToNurbsCurve()]),
    "Duplicate": (2, lambda: [fr_geom(2).Duplicate()]),
    "DuplicateFace": (2, lambda: [fr_geom(2).Faces[0].DuplicateFace(False)]),
    "DuplicateSurface": (2, lambda: [fr_geom(2).Faces[0].DuplicateSurface()]),
    "ToNurbsSurface": (2, lambda: [fr_geom(2).Faces[0].ToNurbsSurface()]),
    "IsoCurve": (2, lambda: [fr_geom(2).Faces[0].IsoCurve(0, 0.5)]),
    "ToBrep": (3, lambda: [fr_geom(3).ToBrep(True)]),
}
aliased = []
for fname, (i, call) in fresh_calls.items():
    before_sig = geo_sig(fr_geom(i))
    for res in call():
        res.Transform(r3.Transform.Translation(5.0, 5.0, 5.0))
    if geo_sig(fr_geom(i)) != before_sig:
        aliased.append(fname)
check(not aliased and set(fresh_calls) <= set(model._FRESH_RESULT),
      f"every 'fresh result' call returns a COPY: moving it leaves the document object "
      f"as it was ({aliased})")
check(n_called > 20 and not changed_by_pure,
      f"{n_called} zero-argument 'read-only' calls on sample geometry change nothing "
      f"({changed_by_pure})")


# ------------------------------------------------------------------------------------
# Undoing the Delete of an EARLY object took about 0.6 seconds. MEASURED: the
# restore re-added every object after it (~5,600) just to put the TABLE back in order.
# Now only the changed object is re-added; the logical order is exact at once; the table
# is re-ordered when written, so the saved FILE keeps the original logical order.
import tempfile  # noqa: E402

big = model.NarwhalModel()
for i in range(60):
    big.add_object(r3.LineCurve(r3.Point3d(i, 0, 0), r3.Point3d(i, 1, 0)), f"L{i}")
big.snapshot()
logical0 = [str(f.Attributes.Id) for f in big.file.Objects]
victim = logical0[3]
before_del = big.snapshot()
big.file.Objects.Delete(victim)
big.snapshot()
adds = []
real_add = r3.File3dmObjectTable.Add


def counting_add(self, *a, **k):
    adds.append(1)
    return real_add(self, *a, **k)


real_add_curve = r3.File3dmObjectTable.AddCurve


def counting_add_curve(self, *a, **k):
    adds.append(1)
    return real_add_curve(self, *a, **k)


r3.File3dmObjectTable.Add = counting_add
r3.File3dmObjectTable.AddCurve = counting_add_curve
try:
    big.restore(before_del)
finally:
    r3.File3dmObjectTable.Add = real_add
    r3.File3dmObjectTable.AddCurve = real_add_curve
check(len(adds) <= 1 and list(big.snapshot().by_id) == logical0,
      f"undoing the Delete of the 4th of 60 objects re-adds ONE object, not the 56 after it "
      f"({len(adds)} adds), and the logical order is exact at once")
check(big.order_pending() and [str(f.Attributes.Id) for f in big.file.Objects] != logical0,
      "(the object TABLE lags until it is written: the undeleted object is at its end)")
out_path = os.path.join(tempfile.mkdtemp(prefix="undo_order_"), "order.3dm")
ok_save = big.save(out_path)
saved = [str(f.Attributes.Id) for f in r3.File3dm.Read(out_path).Objects]
check(ok_save and saved == logical0 and not big.order_pending(),
      "SAVING writes the document's order -- the undeleted object back in its original "
      "logical position")


print()
print("=" * 60)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"UNDO TESTS PASSED ({checks} checks)")
