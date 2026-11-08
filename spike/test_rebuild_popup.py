"""Rebuild popup (narwhal3d/rebuild_popup.py): layout, editing, callbacks and paint-only drawing.

Blender suite. The document callbacks are the real `ui.py` operators on a real model, so
Preview, OK, Cancel and their Undo are measured, not mocked. The viewport and the mouse are
simulated: a fake 3D pane and window region, and synthetic events. Drawing goes to a
recording painter, because background Blender has no GPU context; the real GPU painter is
checked in a live window, not here.
"""
import os
import sys
from types import SimpleNamespace

import bpy  # noqa: F401  (the suite runs under Blender)
import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from narwhal3d import commands, design, rebuild, rebuild_popup as rp, state, ui  # noqa: E402

AREA = SimpleNamespace(type="VIEW_3D", x=40, y=30, regions=[])
REGION = SimpleNamespace(type="WINDOW", x=200, y=120, width=900, height=640)
AREA.regions.append(REGION)
CONTEXT = SimpleNamespace(window_manager=SimpleNamespace(clipboard=""))
POINT_ERROR = "Point count must be a whole number from 2 to 10000."
DEGREE_ERROR = "Degree must be a whole number from 1 to 11."
DEFAULTS = dict(PointCount=10, Degree=3, DeleteInput=True, CurrentLayer=False,
                PreserveTangents=False)


class Context(commands.NarwhalContext):
    def __init__(self, doc):
        super().__init__(doc)
        self.selection = ["Input"]

    def selected_objects(self):
        return [SimpleNamespace(name=name) for name in self.selection]

    def object_by_name(self, name):
        return None

    def select_by_names(self, names):
        self.selection = list(names)

    def add_geometry(self, geometry, base_name, layer_index=None):
        self.note_change("Rebuild")
        name = base_name + "_rebuilt"
        oid = self.model.add_object(geometry, name, 0 if layer_index is None else layer_index)
        return oid, name

    def _restore_document(self, snapshot):
        self.model.restore(snapshot)


def curve_names(doc):
    return [obj.Attributes.Name for obj in doc.file.Objects]


def ev(etype, value="PRESS", ascii="", ctrl=False, shift=False, x=0, y=0):
    """A synthetic Blender event; (x, y) are region pixels, as the popup reads them."""
    return SimpleNamespace(type=etype, value=value, ascii=ascii, ctrl=ctrl, shift=shift,
                           alt=False, mouse_x=REGION.x + x, mouse_y=REGION.y + y)


def typed(text):
    return [ev(c, ascii=c) for c in text]


def send(events):
    return [rp.handle_event(ui, e, CONTEXT) for e in events]


def layout_now():
    return rp._layout(rp._popup, ui, REGION.width, REGION.height)


def centre(name):
    r = layout_now().rects[name]
    return r.x + r.w / 2, r.y + r.h / 2


def click(name):
    x, y = centre(name)
    return send([ev("LEFTMOUSE", "PRESS", x=x, y=y), ev("LEFTMOUSE", "RELEASE", x=x, y=y)])


def begin(app, last=DEFAULTS):
    """Queue the Rebuild window as the command does, then open the popup over the pane."""
    ui._rebuild_last.update(last)
    cmd = commands.RebuildCmd()
    cmd.args = [["Input"]]
    ui._rebuild_window = dict(command=cmd, key=ui._doc_key(), preview=(),
                              counts=[cmd._own_counts(app.ctx.geom_by_name("Input"))],
                              closed=False, message="Click Preview to measure maximum deviation")
    assert rp.open(ui) is True
    return rp._popup


def undo_count(app):
    return len(app.ctx.undo.undo_names())


def measure(text, size):
    return ui._text_width(text, size)


class Recorder:
    """A painter that records every call. Text widths come from the real font (blf)."""

    def __init__(self):
        self.ops = []

    def begin(self):
        self.ops.append(("begin",))

    def end(self):
        self.ops.append(("end",))

    def rect(self, x0, y0, x1, y1, colour):
        self.ops.append(("rect", (x0, y0, x1, y1), tuple(colour)))

    def round(self, x0, y0, x1, y1, radius, fill, border=None, border_width=1.0):
        self.ops.append(("round", (x0, y0, x1, y1), tuple(fill)))

    def glyph(self, tris, colour):
        self.ops.append(("glyph", len(tris), tuple(colour)))

    def text(self, x, y, text, size, colour):
        self.ops.append(("text", (x, y), text, size, tuple(colour)))

    def text_width(self, text, size):
        return measure(text, size)

    def clip(self, x0, y0, x1, y1):
        self.ops.append(("clip",))

    def unclip(self):
        self.ops.append(("unclip",))


def state_snapshot(st):
    return (rp._signature(st, ui), tuple(sorted(st.draft.__dict__.items())),
            tuple(sorted(ui._rebuild_window.items())), st.fields["point"].text,
            st.fields["degree"].text)


original_state = ui.app_state
old_tag, old_redraw = ui._tag_redraw_viewports, ui.redraw_cmdbar
old_active, old_areas = ui.active_pane_area, ui._view3d_areas
saved_last = dict(ui._rebuild_last)
saved_theme = design.THEME
real_rebuild_curve = rebuild.rebuild_curve
ui._tag_redraw_viewports = lambda: None
ui.redraw_cmdbar = lambda: None
ui.active_pane_area = lambda: AREA
ui._view3d_areas = lambda: [AREA]
try:
    app = state.AppState()
    app.ctx = Context(app.model)
    app.model.add_object(r3.NurbsCurve.Create(False, 3, [r3.Point3d(i, i % 2, 0)
                                                       for i in range(6)]), "Input", 0)
    ui.app_state = app
    before = app.ctx.geom_by_name("Input").Encode()

    # ------------------------------------------------------------------ numbers (pure)
    for text, value in (("10", 10), (" 12 ", 12), ("2", 2), ("10000", 10000)):
        assert rp.parse_count(text, 2, 10000, "Point count") == (value, None), text
    for text in ("", "   ", "1", "10001", "1.5", "-3", "1e2", "+7", "abc", "1 0", "\u0663"):
        assert rp.parse_count(text, 2, 10000, "Point count") == (None, POINT_ERROR), repr(text)
    assert rp.parse_count("11", rp.DEGREE_MIN, rp.DEGREE_MAX, "Degree") == (11, None)
    assert rp.parse_count("12", rp.DEGREE_MIN, rp.DEGREE_MAX, "Degree") == (None, DEGREE_ERROR)
    assert rp.parse_count("0", rp.DEGREE_MIN, rp.DEGREE_MAX, "Degree") == (None, DEGREE_ERROR)
    assert (rp.DEGREE_MIN, rp.DEGREE_MAX) == (1, rebuild.MAX_DEGREE) == (1, 11)

    print("PASS: numeric coercion accepts whole numbers only, within the engine's limits")

    # ------------------------------------------------------------ layout (pure, real font)
    for scale in (0.6, 1.0, 1.25, 1.5, 2.0, 2.5):
        for w, h in ((1280, 900), (900, 640), (640, 480), (420, 360), (300, 260)):
            lay = rp.layout(w, h, scale, measure, "Maximum deviation: 0.1", 0.0)
            d = lay.dialog
            assert d.x >= -1e-6 and d.y >= -1e-6 and d.right <= w + 1e-6 and d.top <= h + 1e-6, (scale, w, h)
            for name in ("close", "cancel", "ok"):
                r = lay.targets[name]
                assert d.x <= r.x and r.right <= d.right and d.y <= r.y and r.top <= d.top, (name, scale, w, h)
            for name in rp.BODY_TARGETS:
                r = lay.targets[name]
                if lay.body.y <= r.y and r.top <= lay.body.top:
                    assert d.x <= r.x and r.right <= d.right, (name, scale, w, h)
            if h >= lay.content_h + rp.HEADER_H * scale + rp.FOOTER_H * scale + 2 * rp.EDGE_Y * scale:
                assert lay.max_scroll == 0, ("fits without scrolling", scale, w, h)
    comfortable = rp.layout(1280, 900, 1.0, measure, "", 0.0)
    assert comfortable.max_scroll == 0 and comfortable.dialog.w == rp.DIALOG_W
    for scale in (1.0, 1.5, 2.0):
        lay = rp.layout(1280, 900, scale, measure, "", 0.0)
        for rid, (_attr, label) in rp.CHECKS.items():
            row = lay.rects[rid]
            assert measure(label, rp.FS["label"] * scale) <= row.w - 24 * scale, ("clipped", label, scale)
    short = rp.layout(420, 240, 1.0, measure, "", 0.0)
    assert short.max_scroll > 0, "a short viewport scrolls the body rather than clipping the dialog"
    below = short.targets["tangent"]
    assert not (short.body.y <= below.y and below.top <= short.body.top), "the last option starts below the fold"
    shown = rp.layout(420, 240, 1.0, measure, "", rp.scroll_for(short, "tangent"))
    t = shown.targets["tangent"]
    assert shown.body.y <= t.y and t.top <= shown.body.top, "focusing the last option scrolls it into view"
    bottom = rp.layout(420, 240, 1.0, measure, "", 1e9)
    assert bottom.scroll == short.max_scroll
    assert rp.scroll_for(bottom, "point") == bottom.spans["point"][0], "focusing a field above the view scrolls up to it"
    tiny = rp.layout(120, 90, 2.5, measure, "x", 0.0)
    assert tiny.dialog.x >= 0 and tiny.dialog.y >= 0 and tiny.dialog.right <= 120 and tiny.dialog.top <= 90
    assert len(rp.layout(900, 900, 1.0, measure, "word " * 60).feedback) > 1, "feedback wraps"
    assert rp.layout(900, 900, 1.0, measure, "").feedback == [] and "feedback" not in rp.layout(900, 900, 1.0, measure, "").rects, "no error, no feedback box"
    print("PASS: layout stays inside the viewport at 0.6-2.5 scale, scrolls when short, fits labels")

    # ---------------------------------------------------------------- palette / theme
    design.set_theme("dark")
    dark = rp._palette()
    assert dark["panel"] == design.rgb01(design.PANEL_BG) and dark["scrim"][3] == 0.56
    design.set_theme("light")
    light = rp._palette()
    assert light["panel"] == design.rgb01(design.PANEL_BG) and light["scrim"][3] == 0.31
    assert dark["panel"] != light["panel"] and dark["error"] != light["error"]
    design.set_theme(saved_theme)
    print("PASS: popup colours are design tokens and follow the theme, with no toggle")

    # -------------------------------------------------------------- lifecycle and guards
    ui._rebuild_window = None
    assert rp.open(ui) is False and not rp.is_open()
    begin(app, last=dict(PointCount=12, Degree=4, DeleteInput=False, CurrentLayer=True,
                         PreserveTangents=True))
    assert (rp._popup.draft.point_count, rp._popup.draft.degree, rp._popup.draft.delete_input, rp._popup.draft.current_layer,
            rp._popup.draft.preserve_tangents) == (12, 4, False, True, True), "open reads the last settings"
    assert ui._rebuild_window["operator"] is rp._popup.draft and rp.is_open()
    handle = rp._popup.handler
    rp.close(ui)
    removed_again = True
    try:
        bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
    except ValueError:
        removed_again = False
    assert not removed_again, "close(ui) removes the popup's own draw handler"
    assert not rp.is_open() and ui._rebuild_window["operator"] is None
    assert ui._rebuild_window is not None, "close forgets the popup, not the window"
    assert app.ctx.geom_by_name("Input").Encode() == before and undo_count(app) == 0
    begin(app)
    ui._rebuild_window["key"] = "some other document"
    assert rp.handle_event(ui, ev("MOUSEMOVE"), CONTEXT) is False and not rp.is_open()
    begin(app)
    ui._rebuild_window["key"] = "stale"
    assert rp.open(ui) is False and ui._rebuild_window is None and not rp.is_open()
    begin(app)
    assert rp._popup.pane_key == (AREA.x, AREA.y)
    other = SimpleNamespace(type="VIEW_3D", x=1, y=1,
                            regions=[SimpleNamespace(type="WINDOW", x=0, y=0, width=10, height=10)])
    ui._view3d_areas = lambda: [other]
    try:
        assert rp.handle_event(ui, ev("MOUSEMOVE"), CONTEXT) is False and not rp.is_open()
    finally:
        ui._view3d_areas = lambda: [AREA]
    print("PASS: open reads the last settings; close, stale windows and a vanished pane fail closed")

    # --------------------------------------------------------- consumption and pass-through
    begin(app)
    assert rp.handle_event(ui, ev("MOUSEMOVE", "NOTHING"), CONTEXT) is True
    assert rp.handle_event(ui, ev("TIMER", "NOTHING"), CONTEXT) is False
    assert rp.handle_event(ui, ev("LEFT_SHIFT", "PRESS"), CONTEXT) is False
    assert rp.handle_event(ui, ev("LEFTMOUSE", "PRESS", x=-30, y=-30), CONTEXT) is True
    assert rp._popup.focus == "point" and app.ctx.geom_by_name("Input").Encode() == before
    send([ev("ESC")])
    assert not rp.is_open()
    begin(app)
    x, y = centre("cancel")
    assert rp.handle_event(ui, ev("LEFTMOUSE", "PRESS", x=x, y=y), CONTEXT) is True
    assert not rp.is_open()
    assert rp.handle_event(ui, ev("LEFTMOUSE", "RELEASE", x=x, y=y), CONTEXT) is True
    assert rp.handle_event(ui, ev("LEFTMOUSE", "RELEASE", x=x, y=y), CONTEXT) is False
    print("PASS: input is consumed while open; timers and modifiers pass through; the release "
          "of a click that closes the popup is not passed on")

    # ----------------------------------------------------- editing, coupling and validation
    begin(app)
    send([ev("A", ctrl=True)] + typed("5") + [ev("TAB")])
    assert rp._popup.draft.point_count == 5 and rp._popup.draft.degree == 3 and rp._popup.focus == "degree"
    send([ev("A", ctrl=True)] + typed("9") + [ev("TAB")])
    assert (rp._popup.draft.point_count, rp._popup.draft.degree) == (10, 9), "Point count = Degree + 1, last set wins"
    assert rp._popup.fields["point"].text == "10" and ui._rebuild_window["preview"] == ()
    assert ui._rebuild_window["message"] == "Settings changed"
    click("point_box")
    send([ev("A", ctrl=True)] + typed("1.5") + [ev("TAB")])
    assert rp._popup.draft.point_count == 10, "a bad value never reaches the draft"
    assert rp._popup.field_error == ("point", POINT_ERROR)
    assert rp.feedback(rp._popup) == (POINT_ERROR, True)
    assert "preview" not in layout_now().targets and "preview" not in rp.FOCUS_ORDER
    click("ok")
    assert rp.is_open() and undo_count(app) == 0, "OK is blocked while a value is invalid"
    assert app.ctx.geom_by_name("Input").Encode() == before
    click("point_box")
    send([ev("A", ctrl=True)] + typed("7"))
    assert rp._popup.field_error is None, "editing clears the error for that field"
    send([ev("TAB")])
    assert (rp._popup.draft.point_count, rp._popup.draft.degree) == (7, 6), "a lowered Point count lowers Degree"
    click("point_box")
    send([ev("A", ctrl=True)] + typed("8") + [ev("RET")])
    assert rp._popup.draft.point_count == 8 and rp._popup.focus == "point", "Enter commits the count only"
    assert ui._rebuild_window["preview"] == () and app.ctx.geom_by_name("Input").Encode() == before
    assert undo_count(app) == 0, "Enter computes no fit"
    click("degree_box")
    send([ev("A", ctrl=True)] + typed("1234567890123456"))
    assert len(rp._popup.fields["degree"].text) <= 12, "the count field takes at most 12 characters"
    send([ev("A", ctrl=True)] + typed("9") + [ev("TAB")])
    assert rp._popup.draft.degree == 9 and rp._popup.draft.point_count == 10
    rp.close(ui)
    print("PASS: typed counts are validated on commit, coupled by the operator, and gate acceptance")

    # a short viewport scrolls a new error into view, so a rejected value is never hidden
    begin(app)
    REGION.height = 340
    try:
        click("point_box")
        send([ev("A", ctrl=True)] + typed("1.5") + [ev("TAB")])
        lay = layout_now()
        fb = lay.rects["feedback"]
        assert lay.scroll > 0 and lay.body.y <= fb.y and fb.top <= lay.body.top, "the error is scrolled into view"
        assert rp.feedback(rp._popup) == (POINT_ERROR, True)
    finally:
        REGION.height = 640
    rp.close(ui)

    # ------------------------------------------------------------ focus and keyboard
    begin(app)
    send([ev("TAB", shift=True)])
    assert rp._popup.focus == "close"
    send([ev("TAB", shift=True)])
    assert rp._popup.focus == "ok"
    send([ev("TAB")])
    assert rp._popup.focus == "close"
    for _ in range(len(rp.FOCUS_ORDER)):
        send([ev("TAB")])
    assert rp._popup.focus == "close", "Tab wraps around the focus ring"
    send([ev("DOWN_ARROW")])
    assert rp._popup.focus == "point"
    click("create")
    assert rp._popup.focus == "create" and rp._popup.draft.current_layer is True
    send([ev("SPACE", ascii=" ")])
    assert rp._popup.draft.current_layer is False, "Space toggles the focused checkbox"
    send([ev("RET")])
    assert rp._popup.draft.current_layer is True, "Enter toggles the focused checkbox"
    rp.close(ui)
    print("PASS: Tab / Shift+Tab / arrows walk the focus ring; Space and Enter activate it")

    # ---------------------------------------------------------------- buttons and callbacks
    begin(app)
    click("delete")
    assert rp._popup.draft.delete_input is False
    ui._rebuild_window["preview"] = ((0, 0), (1, 1))
    click("tangent")
    assert rp._popup.draft.preserve_tangents is True and ui._rebuild_window["preview"] == ()
    assert ui._rebuild_window["message"] == "Settings changed"
    click("tangent")
    assert rp._popup.draft.preserve_tangents is False
    rp.close(ui)

    begin(app)

    def failing(*_args, **_kwargs):
        raise ValueError("forced fit failure")

    rebuild.rebuild_curve = failing
    try:
        result = ui.NARWHAL_OT_rebuild_preview.execute(rp._popup.draft, None)
    finally:
        rebuild.rebuild_curve = real_rebuild_curve
    assert result == {"CANCELLED"} and ui._rebuild_window["message"] == "forced fit failure"
    assert ui._rebuild_window["preview"] == () and app.ctx.geom_by_name("Input").Encode() == before
    assert rp.feedback(rp._popup) == ("", False), "the popup shows no Preview result"
    rp.close(ui)
    print("PASS: the Preview operator still reports a failing fit and changes nothing; the popup shows no Preview")

    for label, action in (
        ("Cancel button", lambda: click("cancel")),
        ("Esc with a field focused", lambda: send([ev("ESC")])),
        ("close button", lambda: click("close")),
        ("scrim", lambda: send([ev("LEFTMOUSE", "PRESS", x=3, y=3)])),
    ):
        begin(app)
        if label.startswith("Esc"):
            send([ev("TAB"), ev("TAB")])
        action()
        assert not rp.is_open() and ui._rebuild_window is None, label
        assert app.ctx.geom_by_name("Input").Encode() == before and undo_count(app) == 0, label
    print("PASS: Cancel, Esc, the close button and the scrim cancel with no document change")

    begin(app)
    send([ev("A", ctrl=True)] + typed("8") + [ev("TAB")])
    click("ok")
    assert not rp.is_open() and ui._rebuild_window is None
    assert "Input" not in curve_names(app.model) and len(curve_names(app.model)) == 1
    assert undo_count(app) == 1 and ui._rebuild_last["PointCount"] == 8
    assert app.ctx.undo_last() == "Rebuild" and app.ctx.geom_by_name("Input").Encode() == before
    print("PASS: OK runs the existing operator: one Undo step; the popup closes after it")

    # ------------------------------------------------ contract: editing never fits, OK fits once
    fits = []

    def counting(geom, points, degree, tangents):
        fits.append((points, degree, bool(tangents)))
        return real_rebuild_curve(geom, points, degree, tangents)

    begin(app)
    rebuild.rebuild_curve = counting
    try:
        click("point_box")
        send([ev("A", ctrl=True)] + typed("8") + [ev("RET")])
        click("degree_box")
        send([ev("A", ctrl=True)] + typed("2") + [ev("TAB")])
        click("delete")
        click("create")
        click("tangent")
        click("tangent")
        click("point_box")
        send([ev("A", ctrl=True)] + typed("9") + [ev("TAB")])
    finally:
        rebuild.rebuild_curve = real_rebuild_curve
    assert fits == [], "editing never runs a fit"
    assert rp._popup.draft.point_count == 9 and rp._popup.draft.degree == 2
    assert ui._rebuild_window["preview"] == () and undo_count(app) == 0
    assert app.ctx.geom_by_name("Input").Encode() == before
    click("cancel")
    assert not rp.is_open() and ui._rebuild_window is None and fits == []
    assert app.ctx.geom_by_name("Input").Encode() == before and undo_count(app) == 0
    assert ui._rebuild_last["PointCount"] == 10, "Cancel keeps the remembered settings"
    print("PASS: editing and Cancel never fit or change the document; Cancel keeps the last settings")

    begin(app)
    expected = real_rebuild_curve(app.ctx.geom_by_name("Input"), 8, 3, False)[0].Encode()
    fits.clear()
    rebuild.rebuild_curve = counting
    try:
        click("point_box")
        send([ev("A", ctrl=True)] + typed("8") + [ev("RET")])
        assert fits == [], "Enter in a count computes nothing"
        send([ev("TAB", shift=True), ev("TAB", shift=True)])
        assert rp._popup.focus == "ok"
        send([ev("RET")])
    finally:
        rebuild.rebuild_curve = real_rebuild_curve
    assert not rp.is_open() and ui._rebuild_window is None
    assert fits == [(8, 3, False)], "OK runs one fit, with the committed settings"
    rebuilt = curve_names(app.model)
    rebuilt_geom = app.ctx.geom_by_name(rebuilt[0])
    assert rebuilt == ["Curve_rebuilt"] and len(rebuilt_geom.Points) == 8 and rebuilt_geom.Degree == 3
    assert rebuilt_geom.Encode() == expected, "OK applies the exact fit the Preview would show"
    assert undo_count(app) == 1 and app.ctx.undo_last() == "Rebuild"
    assert app.ctx.geom_by_name("Input").Encode() == before
    print("PASS: Enter in a count fits nothing; Enter on OK applies one exact fit as one Undo step")

    # -------------------------------------------------------------------- paint (draw only)
    design.set_theme("light")
    begin(app, last=DEFAULTS)
    st = rp._popup
    st.field_error = ("degree", DEGREE_ERROR)
    ui._rebuild_window["preview"] = ((0, 0), (5, 5))
    ui._rebuild_window["message"] = "Maximum deviation: 0.25 model units"
    snapshot = state_snapshot(st)
    recorder = Recorder()
    rp.paint_popup(ui, st, REGION.width, REGION.height, recorder)
    assert snapshot == state_snapshot(st), "painting writes no state"
    assert app.ctx.geom_by_name("Input").Encode() == before and undo_count(app) == 0

    ops = recorder.ops
    kinds = {op[0] for op in ops}
    assert kinds <= {"begin", "end", "rect", "round", "glyph", "text", "clip", "unclip"}, kinds
    assert sum(op[0] == "clip" for op in ops) == sum(op[0] == "unclip" for op in ops) == 1
    texts = [op[2] for op in ops if op[0] == "text"]
    for label in ("Rebuild", "Point count", "Degree", "Delete input",
                  "Create new object on current layer", "Preserve end tangent directions",
                  "Cancel", "OK", rp.UNIMPLEMENTED, rp.UNIMPLEMENTED_STATE,
                  "CURVE SETTINGS", "Min. 2", "Min. 1", DEGREE_ERROR):
        assert label in texts, label
    assert not any("Preview" in t or "PREVIEW" in t or "Span" in t or "deviation" in t
                   for t in texts), texts
    lay = layout_now()
    d = lay.dialog
    for op in ops:
        if op[0] == "text":
            x, _y = op[1]
            assert x >= d.x - 1e-6 and x + measure(op[2], op[3]) <= d.right + 1e-6, ("clipped", op[2])
    assert any(op[0] == "text" and op[4] == light["error"] for op in ops), "error ink shown"
    assert any(op[0] == "round" and op[2] == light["panel"] for op in ops)
    adv = lay.rects["advanced"]
    assert rp.hit(lay, adv.x + adv.w / 2, adv.y + adv.h / 2) == "panel", "unimplemented row takes no input"
    rec_dark = Recorder()
    design.set_theme("dark")
    rp.paint_popup(ui, st, REGION.width, REGION.height, rec_dark)
    assert any(op[0] == "rect" and op[2][3] == 0.56 for op in rec_dark.ops), "dark scrim"
    assert any(op[0] == "round" and op[2] == design.rgb01(design.PANEL_BG) for op in rec_dark.ops)
    design.set_theme(saved_theme)
    rp.close(ui)
    print("PASS: paint writes nothing, stays inside the dialog, shows the specified controls and theme")

    # ------------------------------------------- current readouts (the original counts)
    def painted(st, width=REGION.width, height=REGION.height):
        rec = Recorder()
        rp.paint_popup(ui, st, width, height, rec)
        return rec

    def readouts(rec, lay):
        """Per field, the "Current:" text ops drawn in that field's readout slot."""
        found = []
        for rid in rp.FIELDS:
            x = lay.rects[rid + "_current"].x
            found.append([op for op in rec.ops if op[0] == "text" and op[2].startswith("Current")
                          and op[1][0] == x])
        return found

    def texts(groups):
        return [[op[2] for op in group] for group in groups]

    cases = (
        ("one curve", [(6, 3)], "Current: 6", "Current: 3"),
        ("equal counts", [(6, 3), (6, 3), (6, 3)], "Current: 6", "Current: 3"),
        ("mixed point counts", [(6, 3), (9, 3)], "Current: varies", "Current: 3"),
        ("mixed degrees", [(6, 3), (6, 2)], "Current: 6", "Current: varies"),
        ("mixed both", [(6, 3), (9, 2)], "Current: varies", "Current: varies"),
    )
    assert rp.current_labels([]) == ("", "") and rp.current_labels(None) == ("", "")
    design.set_theme("light")
    for label, counts, want_n, want_p in cases:
        begin(app)
        ui._rebuild_window["counts"] = counts
        assert texts(readouts(painted(rp._popup), layout_now())) == [[want_n], [want_p]], label
        rp.close(ui)
    print("PASS: the Current readouts show the original count, or varies when the selection disagrees")

    begin(app)
    ui._rebuild_window["counts"] = [(6, 3), (9, 3)]
    st = rp._popup
    click("point_box")
    send([ev("A", ctrl=True)] + typed("4") + [ev("TAB")])
    assert st.draft.point_count == 4
    assert texts(readouts(painted(st), layout_now())) == [["Current: varies"], ["Current: 3"]]
    assert ui._rebuild_window["counts"] == [(6, 3), (9, 3)] and undo_count(app) == 0
    rp.close(ui)
    print("PASS: editing changes the requested settings, never the Current readouts")

    for theme in ("light", "dark"):
        design.set_theme(theme)
        muted = tuple(rp._palette()["text_muted"])
        for width, height in ((300, 260), (420, 360), (1280, 900)):
            for label, counts, want_n, want_p in cases:
                begin(app)
                ui._rebuild_window["counts"] = counts
                st = rp._popup
                lay = rp._layout(st, ui, width, height)
                groups = readouts(painted(st, width, height), lay)
                assert texts(groups) == [[want_n], [want_p]], (theme, width, label)
                for rid, group in zip(rp.FIELDS, groups):
                    cur, box = lay.rects[rid + "_current"], lay.rects[rid + "_box"]
                    assert cur.top <= box.y + 1e-6 and cur.y >= lay.rects["delete"].top - 1e-6, (rid, label)
                    assert lay.dialog.x <= cur.x and cur.right <= lay.dialog.right, (rid, label)
                    op = group[0]
                    assert "…" not in op[2] and measure(op[2], op[3]) <= cur.w + 1e-6, (width, label)
                    assert op[4] == muted, (theme, rid)
                rp.close(ui)
    design.set_theme(saved_theme)
    print("PASS: Current readouts fit their columns in small and large panes, in both themes")
finally:
    if rp.is_open():
        rp.close(ui)
    ui._rebuild_window = None
    ui._rebuild_last.clear()
    ui._rebuild_last.update(saved_last)
    design.set_theme(saved_theme)
    ui.app_state = original_state
    ui._tag_redraw_viewports, ui.redraw_cmdbar = old_tag, old_redraw
    ui.active_pane_area, ui._view3d_areas = old_active, old_areas
    rebuild.rebuild_curve = real_rebuild_curve
