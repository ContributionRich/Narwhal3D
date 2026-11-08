"""Reported: "length command not working. I should be able to pick a curve or ctrl
shift click a segment and have it tell me the length. or have it selected before typing
length and it will tell me".

MEASURED: commands/length.htm ("Select curves or edges to measure"); the reference application core library strings
"Select curves to measure", "Length = %s", "Cumulative length = %s for %d curves".

ROOT CAUSE of "Length cancelled": the step was a NAME step, so a click did not answer it
(the UI answers only OBJECTS steps with a pick) and the Enter after it hit "an empty Enter
at a fixed prompt cancels". A Ctrl+Shift segment is not an object selection, so the
select-first path never saw it.

Run: tools/blender-5.2.2-linux-x64/blender -b --factory-startup --python spike/test_length_cmd.py
"""
import math
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bpy  # noqa: E402
import rhino3dm as r3  # noqa: E402

from narwhal3d import bridge, commands, ui  # noqa: E402
from narwhal3d import model as model_mod  # noqa: E402
from narwhal3d.state import app_state  # noqa: E402

results = []


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))


class _Sel:
    def __init__(self, name):
        self.name = name


class Ctx:
    def __init__(self, named, selected=(), subobjects=()):
        self.model = model_mod.NarwhalModel()
        for nm, g in named:
            self.model.add_object(g, nm)
        self.sel = [_Sel(n) for n in selected]
        self.sub = list(subobjects)

    def geom_by_name(self, name):
        for o in self.model.file.Objects:
            if o.Attributes.Name == name:
                return o.Geometry
        return None

    def selected_objects(self):
        return self.sel

    def selected_subobjects(self):
        return self.sub

    def note_change(self, name=None):
        pass


def polyline(pts, closed=False):
    pl = r3.Polyline()
    for p in list(pts) + ([pts[0]] if closed else []):
        pl.Add(*p)
    return r3.PolylineCurve(pl)


def number_after(text, key):
    """The number following `key` in `text` (e.g. 'Length = 12 ...' -> 12.0)."""
    tail = text.split(key, 1)[1].strip().split()[0]
    return float(tail)


TRI = polyline([(0, 0, 0), (3, 0, 0), (3, 4, 0)], closed=True)          # 3-4-5: 12
OPEN = polyline([(0, 0, 0), (3, 0, 0), (3, 4, 0), (10, 4, 0)])          # 3, 4, 7
LINE = r3.LineCurve(r3.Point3d(1, 2, 3), r3.Point3d(4, 6, 3))           # 5
CIRC = r3.Circle(r3.Point3d(0, 0, 0), 5.0).ToNurbsCurve()               # 10 pi
SQ = polyline([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], closed=True)


def run(ctx, lines=()):
    cmd = type(commands.COMMANDS["length"])()
    msg, done = cmd.begin(ctx)
    msgs = [msg]
    for line in lines:
        if done:
            break
        msg, done = cmd.feed(line, ctx)
        msgs.append(msg)
    return cmd, msgs, done


try:
    # --- (a) PRESELECTED: runs at once ----------------------------------------------
    cmd, msgs, done = run(Ctx([("Tri", TRI)], selected=["Tri"]))
    check(done and msgs[0] and number_after(msgs[0], "Length =") == 12.0
          and "millimeters" in msgs[0],
          "a preselected curve is measured immediately: 12 millimeters", msgs)

    cmd, msgs, done = run(Ctx([("L", LINE), ("C", CIRC)], selected=["L", "C"]))
    text = msgs[0] or ""
    want = 5.0 + 10.0 * math.pi
    got = number_after(text, "Cumulative length =") if "Cumulative" in text else None
    # MEASURED (Research13 #81): the reference CAD application prints ONLY the accumulated line for several
    # curves ("Cumulative length = 13.283 millimeters for 3 curves"), no per-curve lines.
    check(done and "L: Length" not in text and "\n" not in text.strip() and got is not None
          and abs(got - want) < 1e-8 and text.startswith("Cumulative length =")
          and text.rstrip().endswith("for 2 curves"),
          "two curves: only 'Cumulative length = ... for 2 curves'", text)
    check(abs(commands.LengthCmd._curve_length(CIRC) - 10 * math.pi) < 1e-12,
          "a circle's length is 10 pi to round-off (not a polygon estimate)",
          commands.LengthCmd._curve_length(CIRC) - 10 * math.pi)

    # --- (b) NOTHING selected: an OBJECTS prompt a click answers ------------------
    ctx = Ctx([("Tri", TRI)])
    cmd, msgs, done = run(ctx)
    check(not done and cmd.prompt.startswith("Select curves to measure")
          and cmd.steps[0][1] == commands.OBJECTS and ui._at_selection_step(cmd),
          "with nothing selected it PROMPTS at an object step the UI's click answers",
          (cmd.prompt, cmd.steps))
    msg, done = cmd.feed("Tri", ctx)       # exactly what ui feeds for a clicked curve
    check(done and number_after(msg, "Length =") == 12.0,
          "a picked curve is measured at once", msg)

    ctx = Ctx([("Tri", TRI)])
    cmd, msgs, done = run(ctx, [""])
    check(not done and "cancelled" not in (msgs[-1] or ""),
          "Enter with nothing selected does NOT cancel -- it stays and says so", msgs[-1])
    ctx.sel = [_Sel("Tri")]                # the user selects, then presses Enter
    msg, done = cmd.feed("", ctx)
    check(done and number_after(msg, "Length =") == 12.0,
          "select at the prompt + Enter measures the selection", msg)

    pt = r3.Point(r3.Point3d(0, 0, 0))
    ctx = Ctx([("Pt", pt)])
    cmd, msgs, done = run(ctx, ["Pt"])
    check(not done and "not a curve" in (msgs[-1] or ""),
          "a non-curve is refused and the prompt stays", msgs[-1])

    # --- (c) a Ctrl+Shift SEGMENT ---------------------------------------------------
    ctx = Ctx([("P", OPEN)], selected=["P"], subobjects=[("P", "Segment", 2)])
    cmd, msgs, done = run(ctx)
    segs = bridge.curve_segments(OPEN)
    ui_len = math.dist(segs[2][1][0], segs[2][1][-1])
    check(done and number_after(msgs[0], "Length =") == 7.0 == ui_len
          and "segment 2 of P" in msgs[0] and "Cumulative" not in msgs[0],
          "segment 2 (numbered as the UI numbers it) is 7 -- and replaces its whole "
          "curve, which the reporter also had selected", msgs[0])
    ctx = Ctx([("S", SQ)], subobjects=[("S", "Segment", 3)])
    cmd, msgs, done = run(ctx)
    check(done and number_after(msgs[0], "Length =") == 2.0,
          "a closed polyline's CLOSING segment measures too", msgs[0])
    pc = r3.PolyCurve()
    pc.Append(r3.LineCurve(r3.Point3d(3, 0, 0), r3.Point3d(2, 0, 0)))
    pc.Append(r3.Arc(r3.Point3d(0, 0, 0), 2.0, math.pi).ToNurbsCurve())   # (2,0) -> (-2,0)
    ctx = Ctx([("J", pc)], subobjects=[("J", "Segment", 1)])
    cmd, msgs, done = run(ctx)
    got = number_after(msgs[0], "Length =") if msgs[0] else None
    check(done and got is not None and abs(got - 2 * math.pi) < 1e-8,
          "a joined curve's ARC segment: pi r = 2 pi", msgs[0])
    ctx = Ctx([("P", OPEN), ("T", TRI)], subobjects=[("P", "Segment", 0), ("P", "Segment", 1)],
              selected=["T"])
    cmd, msgs, done = run(ctx)
    check(done and "Cumulative length = 19 " in (msgs[0] or "")
          and "for 3 curves" in (msgs[0] or ""),
          "two segments + another selected curve: 3 + 4 + 12 = 19", msgs[0])
    ctx = Ctx([], subobjects=[("Solid", "Edge", 4)])
    cmd, msgs, done = run(ctx)
    check(done and "owner no longer exists" in (msgs[0] or "")
          and "Length =" not in (msgs[0] or ""),
          "a stale solid EDGE is refused rather than reporting a wrong number", msgs[0])

    # --- THE REAL APP PATH: app_state + the UI's own sub-object set ------------------
    app_state.set_model(model_mod.NarwhalModel())
    app_state.active_command = None
    for ob in bpy.context.selected_objects:
        ob.select_set(False)
    for t in ("Polyline", "0,0", "3,0", "3,4", "10,4", ""):
        app_state.feed_line(t)
    names = [o.Attributes.Name for o in app_state.model.file.Objects]
    name = names[-1] if names else None
    for ob in bpy.context.selected_objects:
        ob.select_set(False)
    ui.clear_subobject_selection()
    ui._selected_subobjects.add((name, "Segment", 1))
    app_state.feed_line("Length")
    check(app_state.active_command is None and "Length = 4 " in app_state.history[-1]
          and f"segment 1 of {name}" in app_state.history[-1],
          "app: a Ctrl+Shift segment, then Length -> its length (4), via ui's own set",
          app_state.history[-3:])
    ui.clear_subobject_selection()
    app_state.feed_line("Length")
    still = app_state.active_command is not None
    app_state.feed_line(name)              # what the UI feeds when you click the curve
    check(still and app_state.active_command is None
          and "Length = 14 " in app_state.history[-1],
          "app: Length, then pick the curve -> 14 (not 'Length cancelled')",
          app_state.history[-3:])
except Exception:  # noqa: BLE001
    results.append((False, "the suite raised", traceback.format_exc()))

bad = [r for r in results if not r[0]]
for ok, label, detail in results:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"  [{detail}]"))
print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
sys.stdout.flush()
os._exit(1 if bad else 0)
