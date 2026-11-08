"""App-styled curve Rebuild popup, drawn in the viewport it opened over.

OURS: presentation follows the approved Rebuild dialog design: no backdrop blur and no
Light/Dark control. Every colour is a `design` token read at draw time, so the popup
follows the app's active theme and nothing else. Nothing here writes global theme,
layout or viewport state.

Behaviour is NOT here. Preview, OK and Cancel call the existing operators in `ui.py`
(`NARWHAL_OT_rebuild_preview.execute`, `NARWHAL_OT_rebuild_dialog.execute` and
`.cancel`) against this module's draft, an object that carries the operator's settings
and a `report(level, text)` method. Count coupling is `ui._rebuild_counts_changed`.
Drawing (`paint_popup`) only reads state. Events (`handle_event`) edit the draft and
call those callbacks. No document is written until the OK callback runs.

Lifetime: `open(ui)` attaches one POST_PIXEL draw handler to the 3D viewport that is
active at that moment; `handle_event(ui, event, context)` consumes input while open;
`close(ui)` removes the handler and forgets the draft.

Editing uses `textfield.TextField`, the native-backed editor. Point count and degree are
whole numbers, checked on commit (Enter, Tab, a click elsewhere, Preview or OK). A bad
value is shown in the feedback line and blocks Preview and OK until it is fixed.

Keys: Tab / Shift+Tab / Up / Down move focus; Space or Enter activates a button or
toggles a checkbox; Enter in a count commits it and runs Preview; Esc cancels.

Coordinates: region pixels, origin bottom-left. Layout is written in CSS pixels and
multiplied by the interface scale (`ui._ui_scale()`).

The design's min labels are illustrative: keep the existing rebuild engine's supported
point count >= 2 and degree >= 1 (up to 11), with count >= degree + 1 coupling. Styling
must not silently remove valid reconstruction settings.
OURS: the error ink matches `ai_settings_popup`'s error token, because the design tokens
have no error colour.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import design, glyphs, printdialog, rebuild, textfield

POINT_MIN, POINT_MAX = 2, 10000
DEGREE_MIN, DEGREE_MAX = 1, rebuild.MAX_DEGREE
MAX_FIELD_CHARS = 12

#: Tab order. "close" and the footer buttons are focusable; the advanced row is not.
FOCUS_ORDER = ("close", "point", "degree", "delete", "create", "tangent",
               "preview", "cancel", "ok")
#: (operator attribute, label, minimum, maximum, last-set key for count coupling)
FIELDS = {
    "point": ("point_count", "Point count", POINT_MIN, POINT_MAX, "PointCount"),
    "degree": ("degree", "Degree", DEGREE_MIN, DEGREE_MAX, "Degree"),
}
#: (operator attribute, label)
CHECKS = {
    "delete": ("delete_input", "Delete input"),
    "create": ("current_layer", "Create new object on current layer"),
    "tangent": ("preserve_tangents", "Preserve end tangent directions"),
}
BODY_TARGETS = ("point", "degree", "delete", "create", "tangent", "preview")
HOVERABLE = BODY_TARGETS + ("close", "cancel", "ok")
SUBTITLE = "Refine the selected curve's structure."
INSTRUCTION = "Click Preview to measure maximum deviation."
UNIMPLEMENTED = "Master curve / SubD-friendly / surface rebuild"
UNIMPLEMENTED_STATE = "Not implemented yet"
#: The status `ui._queue_rebuild_window` sets before any Preview. Shown as "No preview
#: yet." rather than repeating the instruction line above it.
INITIAL_STATUS = "Click Preview to measure maximum deviation"
NO_PREVIEW = "No preview yet."

#: OURS: the AI settings popup's error tokens, light and dark (design has none).
ERROR_INK = {"light": (0xAC, 0x57, 0x1E), "dark": (0xF1, 0xB8, 0x78)}

# Sizes in CSS pixels from the dialog design.
DIALOG_W = 454.0
EDGE_X, EDGE_Y = 14.0, 12.0
RADIUS = 11.0
HEADER_H = 66.0
FOOTER_H = 57.0
PAD_X = 17.0
COL_GAP = 11.0
FIELD_H = 33.0
CURRENT_GAP = 5.0
OPTION_H = 17.0
OPTION_GAP = 10.0
CHECK = 15.0
BUTTON_H = 31.0
PREVIEW_H = 33.0
LINE = 1.45
TEXT_BASE = 0.36          # baseline offset below a text line's centre, in font sizes
SCROLL_STEP = 40.0
FS = {"title": 15.0, "sub": 10.5, "mini": 10.0, "label": 11.5, "hint": 10.0,
      "field": 12.0, "chip": 10.5, "instr": 11.0, "feedback": 10.5, "adv_title": 11.0,
      "adv_state": 10.5, "button": 11.5}

MOUSE_TYPES = frozenset(("MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "LEFTMOUSE", "RIGHTMOUSE",
                         "MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"))
KEY_KINDS = {"ESC": "esc", "RET": "enter", "NUMPAD_ENTER": "enter", "TAB": "tab",
             "BACK_SPACE": "back", "DEL": "delete", "LEFT_ARROW": "left",
             "RIGHT_ARROW": "right", "UP_ARROW": "up", "DOWN_ARROW": "down",
             "HOME": "home", "END": "end", "SPACE": "space"}
CTRL_KINDS = {"A": "select_all", "C": "copy", "X": "cut", "V": "paste"}
MODIFIER_TYPES = frozenset(("LEFT_SHIFT", "RIGHT_SHIFT", "LEFT_CTRL", "RIGHT_CTRL",
                            "LEFT_ALT", "RIGHT_ALT", "OSKEY", "NONE"))

#: The open popup's state, or None. Only `open` / `close` / `handle_event` write it.
_popup = None
#: Set when a click closed the popup, so the matching release is not passed on.
_release_pending = [False]


# ---------------------------------------------------------------------------
# model: the draft and the numbers (pure)
# ---------------------------------------------------------------------------

class Draft:
    """The settings the Rebuild operator reads, as this popup edits them.

    `report` is the operator's error channel (``self.report(level, text)``); the last
    text is shown in the feedback line."""

    def __init__(self, last):
        self.point_count = int(last["PointCount"])
        self.degree = int(last["Degree"])
        self.delete_input = bool(last["DeleteInput"])
        self.current_layer = bool(last["CurrentLayer"])
        self.preserve_tangents = bool(last["PreserveTangents"])
        self.last_report = ""

    def report(self, level, text):
        self.last_report = str(text)


def parse_count(text, lo, hi, label):
    """``(value, None)`` for a whole number in ``[lo, hi]``, else ``(None, message)``.

    Surrounding spaces are ignored. Anything else -- a sign, a decimal point, a letter,
    an empty field, a Unicode digit -- is refused rather than truncated."""
    t = (text or "").strip()
    message = f"{label} must be a whole number from {lo} to {hi}."
    if not (t and t.isascii() and t.isdigit()):
        return None, message
    value = int(t)
    if not lo <= value <= hi:
        return None, message
    return value, None


def span_count(point_count, degree, closed):
    """The span readout. The same formula `ui.NARWHAL_OT_rebuild_dialog.draw` uses."""
    return point_count if closed else max(1, point_count - degree)


def span_label(span_text):
    """The chip's text. Layout measures this exact string, so the chip is never too small."""
    return f"Span count: {span_text}"


def current_labels(counts):
    """The "Current:" readouts for (point count, degree) of the queued selection.

    The rule `ui.NARWHAL_OT_rebuild_dialog.draw` applies: the value when every selected
    curve agrees, "varies" when they differ, and no label when nothing is selected. The
    counts come from the window the command queued, so editing the draft never moves them."""
    labels = []
    for index in (0, 1):
        values = {c[index] for c in counts or ()}
        if len(values) == 1:
            value = str(values.pop())
        elif values:
            value = "varies"
        else:
            value = ""
        labels.append(f"Current: {value}" if value else "")
    return tuple(labels)


def feedback(st, ui):
    """``(text, is_error)`` for the feedback line. Read-only."""
    if st.field_error is not None:
        return st.field_error[1], True
    if st.error:
        return st.error, True
    message = (ui._rebuild_window or {}).get("message") or ""
    if not message or message == INITIAL_STATUS:
        return NO_PREVIEW, False
    return message, False


# ---------------------------------------------------------------------------
# layout (pure arithmetic; no bpy)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self):
        return self.x + self.w

    @property
    def top(self):
        return self.y + self.h

    def contains(self, px, py):
        return self.x <= px < self.right and self.y <= py < self.top


@dataclass
class Layout:
    scale: float
    dialog: Rect
    header: Rect
    body: Rect
    footer: Rect
    rects: dict       # name -> Rect (region pixels, scroll applied)
    targets: dict     # hit target id -> Rect
    spans: dict       # body target id -> (top, bottom) in content pixels, unscrolled
    body_h: float
    content_h: float
    max_scroll: float
    scroll: float
    feedback: list    # word-wrapped feedback lines
    span_text: str
    current: tuple    # the "Current:" label under the point count and under the degree


def layout(region_w, region_h, scale, measure, span_text="", feedback_text="",
           scroll=0.0, current=("", "")):
    """Where everything goes in a region of this size, at this interface scale.

    `measure(text, px) -> width` is the drawing font's width. The dialog is centred and
    clamped to the region with a margin, so it never sits off the edge. Its body scrolls
    when the region is too short (``overflow-y: auto``). Content is laid
    out top-down in content pixels and then placed with the scroll offset applied.
    """
    s = float(scale)
    if not s > 0:
        raise ValueError("interface scale must be positive")
    width = max(1.0, min(DIALOG_W * s, region_w - 2 * EDGE_X * s))
    inner = width - 2 * PAD_X * s
    col = (inner - COL_GAP * s) / 2
    col_x = (PAD_X * s, PAD_X * s + col + COL_GAP * s)
    x_in = PAD_X * s

    def lh(key):
        return FS[key] * LINE * s

    items = {}              # name -> (x within dialog, top in content, width, height)
    y = 15 * s
    items["mini"] = (x_in, y, inner, lh("mini"))
    y += lh("mini") + 9 * s
    for rid, cx in zip(FIELDS, col_x):
        title_h = lh("label")
        box_top = y + title_h + 6 * s
        items[rid + "_title"] = (cx, y, col, title_h)
        items[rid + "_box"] = (cx, box_top, col, FIELD_H * s)
        items[rid] = (cx, y, col, title_h + 6 * s + FIELD_H * s)
        items[rid + "_current"] = (cx, box_top + FIELD_H * s + CURRENT_GAP * s, col,
                                   lh("hint"))
    y += lh("label") + 6 * s + FIELD_H * s + CURRENT_GAP * s + lh("hint") + 17 * s
    for rid in CHECKS:
        items[rid] = (x_in, y, inner, OPTION_H * s)
        y += OPTION_H * s + OPTION_GAP * s
    y += 17 * s - OPTION_GAP * s
    items["rule"] = (x_in, y, inner, s)
    y += s + 14 * s
    chip_h = lh("chip") + 8 * s
    chip_w = measure(span_label(span_text), FS["chip"] * s) + 18 * s
    items["mini_preview"] = (x_in, y, inner - chip_w, chip_h)
    items["span"] = (x_in + inner - chip_w, y, chip_w, chip_h)
    y += chip_h + 5 * s
    items["instr"] = (x_in, y, inner, lh("instr"))
    y += lh("instr") + 11 * s
    items["preview"] = (x_in, y, inner, PREVIEW_H * s)
    y += PREVIEW_H * s + 8 * s
    text_w = inner - 20 * s
    lines = printdialog.wrap(feedback_text, text_w,
                             lambda t: measure(t, FS["feedback"] * s)) or [""]
    fb_h = len(lines) * lh("feedback") + 18 * s
    items["feedback"] = (x_in, y, inner, fb_h)
    y += fb_h + 15 * s
    adv_h = 2 * s + 20 * s + lh("adv_title") + 5 * s + lh("adv_state")
    items["advanced"] = (x_in, y, inner, adv_h)
    content_h = y + adv_h + 13 * s

    header_h, footer_h = HEADER_H * s, FOOTER_H * s
    height = max(1.0, min(content_h + header_h + footer_h, region_h - 2 * EDGE_Y * s))
    available = height - header_h - footer_h
    # Float drift from the height arithmetic must not create a phantom scroll range.
    body_h = content_h if available >= content_h - 1e-6 else max(0.0, available)
    max_scroll = max(0.0, content_h - body_h)
    scroll = min(max(0.0, float(scroll)), max_scroll)

    left = (region_w - width) / 2.0
    bottom = (region_h - height) / 2.0
    dialog = Rect(left, bottom, width, height)
    header = Rect(left, bottom + height - header_h, width, header_h)
    footer = Rect(left, bottom, width, footer_h)
    body = Rect(left, bottom + footer_h, width, body_h)

    def place(name):
        xl, top, w, h = items[name]
        return Rect(left + xl, body.top - top + scroll - h, w, h)

    rects = {name: place(name) for name in items}
    spans = {}
    targets = {}
    for name in BODY_TARGETS:
        if name in items:
            targets[name] = rects[name]
            spans[name] = (items[name][1], items[name][1] + items[name][3])
    spans["feedback"] = (items["feedback"][1], items["feedback"][1] + items["feedback"][3])
    title_top = header.top - 15 * s
    icon = Rect(left + PAD_X * s, title_top - 34 * s, 34 * s, 34 * s)
    text_x = icon.right + 10 * s
    text_w = width - (text_x - left) - PAD_X * s - 25 * s
    title = Rect(text_x, title_top - lh("title"), text_w, lh("title"))
    rects.update(icon=icon, title=title,
                 subtitle=Rect(text_x, title.y - 2 * s - lh("sub"), text_w, lh("sub")),
                 close=Rect(left + width - PAD_X * s + 2 * s - 25 * s,
                            header.top - 14 * s - 25 * s, 25 * s, 25 * s))
    targets["close"] = rects["close"]
    ok_w, cancel_w, gap = 110 * s, 92 * s, 8 * s
    # A narrow dialog shrinks both buttons together rather than pushing one out of it.
    room = max(0.0, width - 2 * PAD_X * s - gap)
    shrink = min(1.0, room / (ok_w + cancel_w))
    ok_w, cancel_w = ok_w * shrink, cancel_w * shrink
    button_y = footer.y + (footer_h - BUTTON_H * s) / 2
    rects["ok"] = Rect(left + width - PAD_X * s - ok_w, button_y, ok_w, BUTTON_H * s)
    rects["cancel"] = Rect(rects["ok"].x - gap - cancel_w, button_y, cancel_w,
                           BUTTON_H * s)
    targets["ok"], targets["cancel"] = rects["ok"], rects["cancel"]
    return Layout(s, dialog, header, body, footer, rects, targets, spans, body_h,
                  content_h, max_scroll, scroll, lines, span_text, tuple(current))


def hit(lay, x, y):
    """What is under (x, y): a target id, ``"panel"`` (inside, no control), ``"scrim"``
    (outside the dialog), or None when the point is outside the region."""
    for name in ("close", "cancel", "ok"):
        if lay.targets[name].contains(x, y):
            return name
    if lay.body.contains(x, y):
        for name in BODY_TARGETS:
            if lay.targets[name].contains(x, y):
                return name
        return "panel"
    if lay.dialog.contains(x, y):
        return "panel"
    return "scrim"


def scroll_for(lay, target):
    """The scroll offset that brings body target `target` fully into the body."""
    top, bottom = lay.spans[target]
    scroll = lay.scroll
    if top < scroll:
        scroll = top
    elif bottom > scroll + lay.body_h:
        scroll = bottom - lay.body_h
    return min(max(0.0, scroll), lay.max_scroll)


def fit(text, width, size, measure):
    """`text`, ellipsised so that it is no wider than `width`."""
    if width <= 0:
        return ""
    if measure(text, size) <= width:
        return text
    while text and measure(text + "…", size) > width:
        text = text[:-1]
    return text + "…" if text else ""


# ---------------------------------------------------------------------------
# state and interaction (events only; drawing never calls these)
# ---------------------------------------------------------------------------

class _Popup:
    def __init__(self, pane_key, draft, handler):
        self.pane_key = pane_key
        self.draft = draft
        self.handler = handler
        self.fields = {rid: textfield.TextField(str(getattr(draft, spec[0])),
                                                multiline=False, max_chars=MAX_FIELD_CHARS)
                       for rid, spec in FIELDS.items()}
        self.focus = "point"
        self.hover = None
        self.drag = False
        self.scroll = 0.0
        self.field_error = None     # (rid, message) for a value that did not commit
        self.error = ""             # the last Preview / OK failure


def is_open():
    return _popup is not None


def _pane_area(ui):
    area = ui.active_pane_area()
    if area is None:
        areas = ui._view3d_areas()
        area = areas[0] if areas else None
    return area


def _window_region(area):
    if area is None:
        return None
    return next((r for r in area.regions if r.type == "WINDOW"), None)


def _pane_region(ui, key):
    area = next((a for a in ui._view3d_areas() if ui._pane_key(a) == key), None)
    return _window_region(area)


def open(ui):
    """Open the popup over the active viewport, for the Rebuild window `ui` has queued.

    Returns True when it opened. The window must be current (same document); a stale
    window is cleared and reported as False. Nothing in the document changes."""
    global _popup
    import bpy

    close(ui)
    window = ui._rebuild_window
    if window is None:
        return False
    if not ui._rebuild_source_current():
        ui._clear_rebuild_window()
        return False
    area = _pane_area(ui)
    if _window_region(area) is None:
        return False
    draft = Draft(ui._rebuild_last)
    window["operator"] = draft
    handler = bpy.types.SpaceView3D.draw_handler_add(_draw, (ui,), "WINDOW", "POST_PIXEL")
    _release_pending[0] = False
    _popup = _Popup(ui._pane_key(area), draft, handler)
    ui._tag_redraw_viewports()
    return True


def close(ui):
    """Remove this popup's draw handler and forget its draft. No document change."""
    global _popup
    st = _popup
    if st is None:
        return
    _popup = None
    import bpy

    bpy.types.SpaceView3D.draw_handler_remove(st.handler, "WINDOW")
    window = ui._rebuild_window
    if window is not None and window.get("operator") is st.draft:
        window["operator"] = None
    ui._tag_redraw_viewports()


def _layout(st, ui, region_w, region_h, scroll=None):
    span = span_count(st.draft.point_count, st.draft.degree,
                      bool((ui._rebuild_window or {}).get("closed")))
    text, _bad = feedback(st, ui)
    counts = (ui._rebuild_window or {}).get("counts")
    return layout(region_w, region_h, ui._ui_scale(), ui._text_width, str(span), text,
                  st.scroll if scroll is None else scroll, current_labels(counts))


def _measure(ui, size):
    """Width of text in the field font. Trailing spaces count, as in printdialog."""
    guard = ui._text_width("|", size)
    return lambda t: (ui._text_width(t + "|", size) - guard) if t else 0.0


def _caret_index(st, ui, lay, rid, x):
    box = lay.rects[rid + "_box"]
    field = st.fields[rid]
    text = field.text
    return textfield.index_at([(0, len(text))], text, x - (box.x + 9 * lay.scale), 0,
                              _measure(ui, FS["field"] * lay.scale))


def _signature(st, ui):
    window = ui._rebuild_window or {}
    d = st.draft
    return (st.focus, st.hover, st.scroll, st.drag, st.field_error, st.error,
            st.fields["point"].text, st.fields["degree"].text, d.point_count, d.degree,
            d.delete_input, d.current_layer, d.preserve_tangents, window.get("message"),
            len(window.get("preview") or ()))


def _sync(st):
    """Show the committed draft values, except in a field that holds a bad entry."""
    for rid, spec in FIELDS.items():
        if st.field_error is not None and st.field_error[0] == rid:
            continue
        want = str(getattr(st.draft, spec[0]))
        if st.fields[rid].text != want:
            st.fields[rid].set_text(want)


def _commit(st, ui, rid):
    """Validate and apply the text of count field `rid`. Returns True when it is valid."""
    attr, label, lo, hi, last = FIELDS[rid]
    value, problem = parse_count(st.fields[rid].text, lo, hi, label)
    if problem is not None:
        st.field_error = (rid, problem)
        return False
    if st.field_error is not None and st.field_error[0] == rid:
        st.field_error = None
    if value != getattr(st.draft, attr):
        setattr(st.draft, attr, value)
        st.error = ""
        # The operator's own coupling: Point count = Degree + 1, last set wins. It also
        # clears the preview, which no longer matches the settings.
        ui._rebuild_counts_changed(st.draft, None, last)
    _sync(st)
    return True


def _leave(st, ui):
    if st.focus in FIELDS:
        _commit(st, ui, st.focus)


def _toggle(st, ui, rid):
    attr = CHECKS[rid][0]
    setattr(st.draft, attr, not getattr(st.draft, attr))
    st.error = ""
    if rid == "tangent" and ui._rebuild_window is not None:
        # The preserved tangents change the fit, so the shown preview is stale.
        ui._rebuild_window["preview"] = ()
        ui._rebuild_window["message"] = "Settings changed; click Preview"


def _preview(st, ui):
    _leave(st, ui)
    if st.field_error is not None:
        return
    st.draft.last_report = ""
    ui._rebuild_window["operator"] = st.draft
    result = ui.NARWHAL_OT_rebuild_preview.execute(st.draft, None)
    st.error = "" if result == {"FINISHED"} else (st.draft.last_report or "Preview failed.")


def _ok(st, ui, context):
    _leave(st, ui)
    if st.field_error is not None:
        return
    st.draft.last_report = ""
    ui._rebuild_window["operator"] = st.draft
    # The operator commits one Undo step, or refuses with the document changed; either
    # way it clears the window, and only then does the popup close.
    result = ui.NARWHAL_OT_rebuild_dialog.execute(st.draft, context)
    if result == {"FINISHED"} or ui._rebuild_window is None:
        close(ui)
    else:
        st.error = st.draft.last_report or "Rebuild was cancelled."


def _cancel(st, ui):
    ui.NARWHAL_OT_rebuild_dialog.cancel(st.draft, None)
    close(ui)


def _activate(st, ui, context, rid):
    if rid in ("close", "cancel"):
        _cancel(st, ui)
    elif rid == "preview":
        _preview(st, ui)
    elif rid == "ok":
        _ok(st, ui, context)
    elif rid in CHECKS:
        _toggle(st, ui, rid)


def _focus(st, ui, rid):
    """Make `rid` the focused control, committing the field that had focus."""
    if rid != st.focus:
        _leave(st, ui)
        st.focus = rid


def _click(st, ui, context, lay, x, y, shift):
    target = hit(lay, x, y)
    if target in FIELDS:
        was_focused = st.focus == target
        _focus(st, ui, target)
        st.fields[target].set_caret(_caret_index(st, ui, lay, target, x),
                                    select=shift and was_focused)
        st.drag = True
    elif target in CHECKS or target in ("preview", "ok"):
        _focus(st, ui, target)
        _activate(st, ui, context, target)
    elif target in ("close", "cancel", "scrim"):
        _cancel(st, ui)


def _step_focus(st, ui, region_w, region_h, step):
    _leave(st, ui)
    st.focus = FOCUS_ORDER[(FOCUS_ORDER.index(st.focus) + step) % len(FOCUS_ORDER)]
    lay = _layout(st, ui, region_w, region_h)
    if st.focus in lay.spans:
        st.scroll = scroll_for(lay, st.focus)
    if st.focus in FIELDS:
        st.fields[st.focus].select_all()


def _edited(st, rid):
    if st.field_error is not None and st.field_error[0] == rid:
        st.field_error = None


def _set_clipboard(context, text):
    if text and context is not None:
        context.window_manager.clipboard = text


def _field_key(st, ui, context, rid, kind, ch, shift):
    field = st.fields[rid]
    if kind == "enter":
        if _commit(st, ui, rid):
            _preview(st, ui)
    elif kind in ("char", "paste", "space"):
        text = " " if kind == "space" else "".join(c for c in ch if c.isprintable())
        if text:
            field.insert(text)
            _edited(st, rid)
    elif kind in ("back", "delete", "cut"):
        if kind == "cut":
            _set_clipboard(context, field.cut())
        elif kind == "back":
            field.backspace()
        else:
            field.delete()
        _edited(st, rid)
    elif kind in ("left", "right"):
        field.move(-1 if kind == "left" else 1, select=shift)
    elif kind == "home":
        field.home(select=shift)
    elif kind == "end":
        field.end(select=shift)
    elif kind == "select_all":
        field.select_all()
    elif kind == "copy":
        _set_clipboard(context, field.selected_text())


def _key(st, ui, context, region_w, region_h, kind, ch, shift):
    if kind == "esc":
        _cancel(st, ui)
    elif kind in ("tab", "down", "up"):
        backward = kind == "up" or (kind == "tab" and shift)
        _step_focus(st, ui, region_w, region_h, -1 if backward else 1)
    elif st.focus in FIELDS:
        _field_key(st, ui, context, st.focus, kind, ch, shift)
    elif kind in ("enter", "space"):
        _activate(st, ui, context, st.focus)


def _pointer(st, ui, context, region, event, etype, value):
    x = event.mouse_x - region.x
    y = event.mouse_y - region.y
    inside = 0 <= x < region.width and 0 <= y < region.height
    lay = _layout(st, ui, region.width, region.height)
    st.scroll = lay.scroll
    if etype in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE"):
        target = hit(lay, x, y) if inside else None
        st.hover = target if target in HOVERABLE else None
        if st.drag and st.focus in FIELDS:
            st.fields[st.focus].set_caret(_caret_index(st, ui, lay, st.focus, x),
                                          select=True)
    elif etype == "LEFTMOUSE":
        if value == "RELEASE":
            st.drag = False
        elif value in ("PRESS", "DOUBLE_CLICK") and inside:
            _click(st, ui, context, lay, x, y, bool(getattr(event, "shift", False)))
    elif etype in ("WHEELUPMOUSE", "WHEELDOWNMOUSE") and inside and lay.body.contains(x, y):
        step = -SCROLL_STEP if etype == "WHEELUPMOUSE" else SCROLL_STEP
        st.scroll = _layout(st, ui, region.width, region.height,
                            scroll=st.scroll + step * lay.scale).scroll


def handle_event(ui, event, context=None):
    """Consume an input event while the popup is open. Returns True when it was consumed.

    Timers, window events and bare modifier keys pass through. Everything else is
    consumed while the popup is open, so the viewport beneath cannot act on it. A window
    that is no longer current closes the popup and lets the event pass."""
    st = _popup
    etype = getattr(event, "type", "")
    value = getattr(event, "value", "")
    if st is None:
        if _release_pending[0] and etype == "LEFTMOUSE" and value == "RELEASE":
            _release_pending[0] = False
            return True
        return False
    if ui._rebuild_window is None or not ui._rebuild_source_current():
        close(ui)
        return False
    if etype.startswith(("TIMER", "WINDOW_", "FILE_")) or etype in MODIFIER_TYPES:
        return False
    region = _pane_region(ui, st.pane_key)
    if region is None:
        close(ui)
        return False
    if context is None:
        import bpy

        context = bpy.context
    before = _signature(st, ui)
    errors_before = (st.field_error, st.error)
    if etype in MOUSE_TYPES:
        _pointer(st, ui, context, region, event, etype, value)
    elif value != "RELEASE":
        ctrl = bool(getattr(event, "ctrl", False))
        alt = bool(getattr(event, "alt", False))
        shift = bool(getattr(event, "shift", False))
        ch = getattr(event, "ascii", "") or ""
        kind = KEY_KINDS.get(etype)
        if kind is None and ctrl and etype in CTRL_KINDS:
            kind = CTRL_KINDS[etype]
        if kind is None and ch and ch.isprintable() and not ctrl and not alt:
            kind = "char"
        if kind == "paste":
            ch = getattr(getattr(context, "window_manager", None), "clipboard", "") or ""
        if kind is not None:
            _key(st, ui, context, region.width, region.height, kind, ch, shift)
    if _popup is None:
        if etype == "LEFTMOUSE" and value in ("PRESS", "DOUBLE_CLICK"):
            _release_pending[0] = True
        return True
    if (st.field_error, st.error) != errors_before and (st.field_error or st.error):
        # A new error must be readable even in a short viewport, where the body scrolls.
        st.scroll = scroll_for(_layout(st, ui, region.width, region.height), "feedback")
    if _signature(st, ui) != before:
        ui._tag_redraw_viewports()
    return True


# ---------------------------------------------------------------------------
# drawing (reads state only)
# ---------------------------------------------------------------------------

def _palette():
    """The app's tokens, read now so a theme change shows on the next paint."""
    dark = design.THEME == "dark"
    rgb = design.rgb01
    accent = rgb(design.ACCENT)
    return {
        "scrim": (0.0, 0.0, 0.0, 0.56) if dark else (18 / 255, 27 / 255, 41 / 255, 0.31),
        "shadow": (0.0, 0.0, 0.0) if dark else (23 / 255, 38 / 255, 60 / 255),
        "panel": rgb(design.PANEL_BG),
        "header": rgb(design.PANEL_HEADER_BG),
        "outline": rgb(design.PANEL_OUTLINE),
        "list": rgb(design.LIST_BG),
        "chip": rgb(design.CHIP_BG),
        "field": rgb(design.FIELD_BG),
        "border": rgb(design.BORDER),
        "border_strong": rgb(design.BORDER_STRONG),
        "accent": accent,
        "accent_faint": accent[:3] + (0.48,),
        "focus_glow": accent[:3] + (0.2,),
        "accent_soft": rgb(design.ACCENT_SOFT),
        "text": rgb(design.TEXT),
        "text_strong": rgb(design.TEXT_STRONG),
        "text_muted": rgb(design.TEXT_MUTED),
        "tool_hover": rgb(design.TOOL_HOVER),
        "on_accent": rgb(design.ACTIVE_TITLE_TEXT),
        "error": rgb(ERROR_INK["dark" if dark else "light"]),
        "white": (1.0, 1.0, 1.0, 1.0),
    }


def _brighten(color):
    """CSS ``filter: brightness(1.035)``."""
    return (min(1.0, color[0] * 1.035), min(1.0, color[1] * 1.035),
            min(1.0, color[2] * 1.035), color[3])


def _svg(cx, cy, k):
    """Map SVG icon coordinates (24-unit box, y down) to pixels about (cx, cy)."""
    return lambda x, y: (cx + (x - 12) * k, cy - (y - 12) * k)


def _dashed(points, dash, gap, width):
    """Tris for a stroked polyline drawn as dashes of `dash` on and `gap` off."""
    tris = []
    on, remaining = True, dash
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        t = 0.0
        while t < length:
            step = min(remaining, length - t)
            if on:
                a = (x0 + (x1 - x0) * t / length, y0 + (y1 - y0) * t / length)
                b = (x0 + (x1 - x0) * (t + step) / length, y0 + (y1 - y0) * (t + step) / length)
                tris += glyphs.segment(a[0], a[1], b[0], b[1], width)
            t += step
            remaining -= step
            if remaining <= 1e-9:
                on = not on
                remaining = dash if on else gap
    return tris


def _label(p, rect, text, size, color, align="left", max_w=None):
    width = rect.w if max_w is None else max_w
    shown = fit(text, width, size, p.text_width)
    tw = p.text_width(shown, size)
    if align == "center":
        x = rect.x + (rect.w - tw) / 2
    elif align == "right":
        x = rect.right - tw
    else:
        x = rect.x
    p.text(x, rect.y + rect.h / 2 - TEXT_BASE * size, shown, size, color)


def _ring(p, rect, radius, background, accent, s):
    """The keyboard focus ring: a 2 px accent band 2 px outside the control."""
    p.round(rect.x - 4 * s, rect.y - 4 * s, rect.right + 4 * s, rect.top + 4 * s,
            radius + 4 * s, accent)
    p.round(rect.x - 2 * s, rect.y - 2 * s, rect.right + 2 * s, rect.top + 2 * s,
            radius + 2 * s, background)


def _paint_header(lay, st, pal, p):
    s = lay.scale
    icon = lay.rects["icon"]
    p.round(icon.x, icon.y, icon.right, icon.top, 8 * s, pal["list"], pal["border"], s)
    f = _svg(icon.x + icon.w / 2, icon.y + icon.h / 2, s)
    p.glyph(glyphs.polyline([f(x, y) for x, y in
                             ((3, 17.7), (7.6, 10.5), (12.8, 13.6), (20.4, 5.3))], 1.7 * s),
            pal["accent"])
    p.glyph(_dashed([f(x, y) for x, y in ((3, 9), (9.3, 9), (13, 17), (20.5, 17))],
                    2 * s, 2 * s, 1.7 * s), pal["accent_faint"])
    for x, y in ((3, 17.7), (12.8, 13.6), (20.4, 5.3)):
        p.glyph(glyphs.disc(*f(x, y), 1.2 * s, segments=10), pal["accent"])
    _label(p, lay.rects["title"], "Rebuild", FS["title"] * s, pal["text_strong"])
    _label(p, lay.rects["subtitle"], SUBTITLE, FS["sub"] * s, pal["text_muted"])
    close = lay.rects["close"]
    if st.focus == "close":
        _ring(p, close, 5 * s, pal["header"], pal["accent"], s)
    hovered = st.hover == "close"
    if hovered:
        p.round(close.x, close.y, close.right, close.top, 5 * s, pal["chip"])
    cx, cy, d = close.x + close.w / 2, close.y + close.h / 2, 4.5 * s
    p.glyph(glyphs.segment(cx - d, cy - d, cx + d, cy + d, 1.6 * s) +
            glyphs.segment(cx - d, cy + d, cx + d, cy - d, 1.6 * s),
            pal["text_strong"] if hovered else pal["text_muted"])


def _paint_field(lay, st, pal, p, rid, ui):
    s = lay.scale
    box = lay.rects[rid + "_box"]
    field = st.fields[rid]
    focused = st.focus == rid
    bad = st.field_error is not None and st.field_error[0] == rid
    border = (pal["error"] if bad else pal["accent"]
              if focused or st.hover == rid else pal["border_strong"])
    if focused:
        p.round(box.x - 2 * s, box.y - 2 * s, box.right + 2 * s, box.top + 2 * s,
                8 * s, pal["focus_glow"])
    p.round(box.x, box.y, box.right, box.top, 6 * s, pal["field"], border, s)
    size = FS["field"] * s
    tx = box.x + 9 * s
    measure = _measure(ui, size)
    if focused and field.selection() is not None:
        lo_, hi_ = field.selection()
        p.rect(tx + measure(field.text[:lo_]), box.y + 4 * s,
               tx + measure(field.text[:hi_]), box.top - 4 * s, pal["accent_soft"])
    p.text(tx, box.y + box.h / 2 - TEXT_BASE * size,
           fit(field.text, box.right - 9 * s - tx, size, p.text_width), size, pal["text_strong"])
    if focused:
        caret = tx + measure(field.text[:field.caret])
        p.rect(caret, box.y + 5 * s, caret + 1.5 * s, box.top - 5 * s, pal["text_strong"])


def _paint_check(lay, st, pal, p, rid):
    s = lay.scale
    attr, label = CHECKS[rid]
    row = lay.rects[rid]
    on = bool(getattr(st.draft, attr))
    box = Rect(row.x, row.y + (row.h - CHECK * s) / 2, CHECK * s, CHECK * s)
    if st.focus == rid:
        _ring(p, box, 4 * s, pal["panel"], pal["accent"], s)
    if on:
        p.round(box.x, box.y, box.right, box.top, 4 * s, pal["accent"], pal["accent"], s)
        cx, cy = box.x + box.w / 2, box.y + box.h / 2
        p.glyph(glyphs.polyline([(cx - 3.2 * s, cy + 0.2 * s), (cx - 1.0 * s, cy - 2.0 * s),
                                 (cx + 3.0 * s, cy + 2.6 * s)], 1.8 * s), pal["white"])
    else:
        p.round(box.x, box.y, box.right, box.top, 4 * s, pal["field"], pal["border_strong"], s)
    colour = pal["text_strong"] if st.hover == rid else pal["text"]
    _label(p, Rect(row.x + 24 * s, row.y, row.w - 24 * s, row.h), label, FS["label"] * s,
           colour)


def _paint_preview(lay, st, pal, p):
    s = lay.scale
    r = lay.rects["preview"]
    if st.focus == "preview":
        _ring(p, r, 6 * s, pal["panel"], pal["accent"], s)
    hovered = st.hover == "preview"
    p.round(r.x, r.y, r.right, r.top, 6 * s, pal["tool_hover"] if hovered else pal["chip"],
            pal["accent"] if hovered else pal["border_strong"], s)
    size, icon, gap = FS["button"] * s, 13 * s, 7 * s
    label = "Preview curve"
    text_w = p.text_width(label, size)
    x0 = r.x + (r.w - (icon + gap + text_w)) / 2
    f = _svg(x0 + icon / 2, r.y + r.h / 2, icon / 24)
    p.glyph(glyphs.polyline([f(9, 5.5), f(18, 12), f(9, 18.5)], 1.9 * icon / 24,
                            closed=True), pal["accent"])
    p.text(x0 + icon + gap, r.y + r.h / 2 - TEXT_BASE * size, label, size,
           pal["text_strong"])


def _paint_body(lay, st, pal, p, ui):
    s = lay.scale
    _label(p, lay.rects["mini"], "CURVE SETTINGS", FS["mini"] * s, pal["text_muted"])
    for index, (rid, spec) in enumerate(FIELDS.items()):
        title_rect = lay.rects[rid + "_title"]
        hint = f"Min. {spec[2]}"
        hint_w = p.text_width(hint, FS["hint"] * s)
        _label(p, title_rect, spec[1], FS["label"] * s, pal["text_strong"],
               max_w=title_rect.w - hint_w - 6 * s)
        _label(p, title_rect, hint, FS["hint"] * s, pal["text_muted"], align="right")
        _paint_field(lay, st, pal, p, rid, ui)
        if lay.current[index]:
            _label(p, lay.rects[rid + "_current"], lay.current[index], FS["hint"] * s,
                   pal["text_muted"])
    for rid in CHECKS:
        _paint_check(lay, st, pal, p, rid)
    rule = lay.rects["rule"]
    p.rect(rule.x, rule.y, rule.right, rule.top, pal["border"])
    _label(p, lay.rects["mini_preview"], "PREVIEW", FS["mini"] * s, pal["text_muted"])
    chip = lay.rects["span"]
    p.round(chip.x, chip.y, chip.right, chip.top, chip.h / 2, pal["chip"], pal["border"], s)
    _label(p, chip, span_label(lay.span_text), FS["chip"] * s,
           pal["text_muted"], align="center")
    _label(p, lay.rects["instr"], INSTRUCTION, FS["instr"] * s, pal["text_muted"])
    _paint_preview(lay, st, pal, p)
    _paint_feedback(lay, st, pal, p, ui)
    _paint_advanced(lay, pal, p)
    if lay.max_scroll > 0:
        thumb = max(18 * s, lay.body_h ** 2 / lay.content_h)
        travel = lay.body_h - thumb
        top = lay.body.top - travel * (lay.scroll / lay.max_scroll)
        p.rect(lay.body.right - 6 * s, top - thumb, lay.body.right - 3 * s, top,
               pal["text_muted"])


def _paint_feedback(lay, st, pal, p, ui):
    s = lay.scale
    r = lay.rects["feedback"]
    _text, bad = feedback(st, ui)
    p.round(r.x, r.y, r.right, r.top, 5 * s, pal["list"], pal["border"], s)
    size, line_h = FS["feedback"] * s, FS["feedback"] * LINE * s
    colour = pal["error"] if bad else pal["text_muted"]
    for i, line in enumerate(lay.feedback):
        top = r.top - 9 * s - i * line_h
        p.text(r.x + 10 * s, top - line_h / 2 - TEXT_BASE * size,
               fit(line, r.w - 20 * s, size, p.text_width), size, colour)


def _paint_advanced(lay, pal, p):
    s = lay.scale
    r = lay.rects["advanced"]
    p.round(r.x, r.y, r.right, r.top, 7 * s, pal["list"], pal["border"], s)
    x, w = r.x + 11 * s, r.w - 22 * s
    title_h = FS["adv_title"] * LINE * s
    state_h = FS["adv_state"] * LINE * s
    title = Rect(x, r.top - 11 * s - title_h, w, title_h)
    _label(p, title, UNIMPLEMENTED, FS["adv_title"] * s, pal["text_strong"])
    state = Rect(x, title.y - 5 * s - state_h, w, state_h)
    k = 13 / 24 * s
    f = _svg(x + 6.5 * s, state.y + state_h / 2, k)
    cx, cy = f(12, 12)
    p.glyph(glyphs.arc(cx, cy, 8.5 * k, 0.0, 2 * math.pi, 1.8 * k), pal["text_muted"])
    p.glyph(glyphs.segment(*f(12, 10.5), *f(12, 16.5), 2 * k) + glyphs.disc(*f(12, 7.5), k),
            pal["text_muted"])
    _label(p, Rect(x + 20 * s, state.y, w - 20 * s, state_h), UNIMPLEMENTED_STATE,
           FS["adv_state"] * s, pal["text_muted"])


def _paint_footer(lay, st, pal, p):
    s = lay.scale
    for name, label in (("cancel", "Cancel"), ("ok", "OK")):
        r = lay.rects[name]
        primary = name == "ok"
        if st.focus == name:
            _ring(p, r, 6 * s, pal["panel"], pal["accent"], s)
        fill = pal["accent"] if primary else pal["chip"]
        if st.hover == name:
            fill = _brighten(fill)
        p.round(r.x, r.y, r.right, r.top, 6 * s, fill,
                pal["accent"] if primary else pal["border_strong"], s)
        _label(p, r, label, FS["button"] * s,
               pal["on_accent"] if primary else pal["text_strong"], align="center")


def paint_popup(ui, st, region_w, region_h, p):
    """Paint the popup with painter `p`. Reads `st` and `ui`; writes neither."""
    pal = _palette()
    lay = _layout(st, ui, region_w, region_h)
    s = lay.scale
    d, hd, ft, bd = lay.dialog, lay.header, lay.footer, lay.body
    p.begin()
    try:
        p.rect(0, 0, region_w, region_h, pal["scrim"])
        strength = 2.0 if design.THEME == "dark" else 1.0
        for grow, alpha in ((20, 0.035), (12, 0.05), (5, 0.07)):
            p.round(d.x - grow * s, d.y - (grow + 8) * s, d.right + grow * s,
                    d.top + (grow - 8) * s, (RADIUS + grow) * s,
                    pal["shadow"] + (min(1.0, alpha * strength),))
        p.round(d.x, d.y, d.right, d.top, RADIUS * s, pal["outline"])
        p.round(d.x + s, d.y + s, d.right - s, d.top - s, (RADIUS - 1) * s, pal["panel"])
        p.round(d.x + s, hd.y - RADIUS * s, d.right - s, d.top - s, (RADIUS - 1) * s,
                pal["header"])
        p.rect(d.x + s, hd.y - RADIUS * s, d.right - s, hd.y, pal["panel"])
        p.rect(d.x + s, hd.y - s, d.right - s, hd.y, pal["border"])
        p.rect(d.x + s, ft.top - s, d.right - s, ft.top, pal["border"])
        _paint_header(lay, st, pal, p)
        p.clip(bd.x, bd.y, bd.right, bd.top)
        try:
            _paint_body(lay, st, pal, p, ui)
        finally:
            p.unclip()
        _paint_footer(lay, st, pal, p)
    finally:
        p.end()


class _GpuPainter:
    """The real painter: the `ui` GPU helpers, with alpha blending and scissoring."""

    def __init__(self, ui):
        import gpu

        self.ui = ui
        self.gpu = gpu
        self.shader = ui._uniform_color_shader()
        if self.shader is None:
            raise RuntimeError("no flat colour shader for the Rebuild popup")

    def begin(self):
        self.gpu.state.blend_set("ALPHA")

    def end(self):
        self.gpu.state.blend_set("NONE")

    def rect(self, x0, y0, x1, y1, colour):
        self.shader.bind()
        self.ui._draw_rect(self.shader, x0, y0, x1, y1, colour)

    def round(self, x0, y0, x1, y1, radius, fill, border=None, border_width=1.0):
        self.shader.bind()
        self.ui._draw_round_rect(self.shader, x0, y0, x1, y1, radius, fill, border,
                                 border_width)

    def glyph(self, tris, colour):
        self.ui._draw_glyph(self.shader, tris, colour)
        self.gpu.state.blend_set("ALPHA")

    def text(self, x, y, text, size, colour):
        self.ui._draw_text(x, y, text, size, colour)
        self.gpu.state.blend_set("ALPHA")   # blf.draw switches blending off

    def text_width(self, text, size):
        return self.ui._text_width(text, size)

    def clip(self, x0, y0, x1, y1):
        self.gpu.state.scissor_test_set(True)
        self.gpu.state.scissor_set(int(x0), int(y0), max(0, int(math.ceil(x1 - x0))),
                                   max(0, int(math.ceil(y1 - y0))))

    def unclip(self):
        self.gpu.state.scissor_test_set(False)


def _draw(ui):
    """POST_PIXEL: draw only, in the one viewport the popup was opened over."""
    st = _popup
    if st is None:
        return
    import bpy

    context = bpy.context
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    if (area is None or region is None or region.type != "WINDOW"
            or ui._pane_key(area) != st.pane_key):
        return
    if ui._rebuild_window is None or not ui._rebuild_source_current():
        return
    try:
        paint_popup(ui, st, region.width, region.height, _GpuPainter(ui))
    except Exception as exc:  # noqa: BLE001
        print(f"[narwhal3d] rebuild popup draw failed: {exc!r}")
