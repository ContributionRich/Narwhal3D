"""The Print dialog's MODEL: settings, layout, dropdowns, fields, preview. Pure.

The controls modelled here are the settings `print2d` can honour: file destination,
size, orientation, output colour, view, output scale, default line width, and selected-
object visibility. The preview updates from the same settings used for file output.

The dialog is drawn by the app rather than as a Blender properties popup because that popup
layout is not rebuilt for the live interactions required by the preview. Everything that
does not need the GPU lives here; `ui.py` only draws it and routes events to it.

Interaction, the way every desktop dropdown works: click a setting -> its list opens
under it; click an item -> chosen, list closes; click anywhere else -> list closes and
nothing else happens; Esc closes the list. A check box toggles. A field takes digits
and '.', Enter/Tab or a click elsewhere commits, Esc abandons the edit.

Coordinates are Blender REGION pixels: origin bottom-left, y up.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import print2d

#: (label, file extension) -- the "printers" this build can print to
DESTINATIONS = (("PDF", ".pdf"), ("CorelDRAW 2015/X7", ".cdr"),
                ("Adobe Illustrator", ".ai"), ("SVG", ".svg"))
ORIENTATIONS = ("Landscape", "Portrait")
AREAS = ("Viewport", "Extents")
#: Preset values for the dialog's scale box.
SCALES = ("Fit", "1:1", "1:2", "1:5", "1:10", "1:20", "1:50", "1:100",
          "2:1", "5:1", "10:1")
COLORS = ("PrintColor", "DisplayColor", "BlackWhite")
COLOR_LABELS = {"PrintColor": "Print color", "DisplayColor": "Display color",
                "BlackWhite": "Black and white"}
WIDTHS = ("Hairline",) + tuple(f"{w:.2f}" for w in print2d.PRINT_WIDTHS_MM)
SIZES = tuple(print2d.PAGE_SIZES_MM) + (print2d.CUSTOM,)
#: Largest accepted custom sheet; 5 m accommodates large-format plotters.
MAX_CUSTOM_MM = 5000.0

#: (id, label, kind) -- kind: "choice" (a dropdown), "check", "field"
ROWS = (
    ("destination", "Printer", "choice"),
    ("cdr_version", "CorelDRAW version", "choice"),
    ("size", "Size", "choice"),
    ("custom_w", "  Width (mm)", "field"),
    ("custom_h", "  Height (mm)", "field"),
    ("orientation", "Orientation", "choice"),
    ("color", "Output color", "choice"),
    ("view", "View", "choice"),
    ("area", "Area", "choice"),
    ("scale", "Scale (paper = model)", "choice"),
    ("width", "Default line width", "choice"),
    ("selected_only", "Only selected objects", "check"),
)
ROW_LABELS = {rid: label for rid, label, _k in ROWS}
ROW_KINDS = {rid: kind for rid, _l, kind in ROWS}
BUTTONS = (("print", "Print"), ("close", "Close"))


@dataclass
class DialogState:
    destination: int = 0
    cdr_version: str = "15"
    size: str = print2d.DEFAULT_PAGE
    orientation: str = print2d.DEFAULT_ORIENTATION
    color: str = "PrintColor"
    views: tuple = ()
    view: str = ""
    area: str = "Viewport"
    scale: str = "Fit"
    width: str = f"{print2d.DEFAULT_WIDTH_MM:.2f}"
    selected_only: bool = False
    custom_w: float = 210.0
    custom_h: float = 297.0
    message: str = ""
    # interaction state
    open_dropdown: str | None = None
    hover: int | None = None
    focus: str | None = None
    edit_text: str = ""
    extra: dict = field(default_factory=dict)

    # -- rows ---------------------------------------------------------------
    def visible_rows(self):
        """The rows to draw: the size fields only when Size is Custom."""
        out = []
        for rid, label, kind in ROWS:
            if kind == "field" and self.size != print2d.CUSTOM:
                continue
            if rid == "cdr_version" and self.extension() != ".cdr":
                continue
            out.append((rid, label, kind))
        return out

    def choices(self, rid):
        return {
            "destination": tuple(range(len(DESTINATIONS))),
            "cdr_version": ("15", "17"),
            "size": SIZES,
            "orientation": ORIENTATIONS,
            "color": COLORS,
            "view": tuple(self.views) or ("",),
            "area": AREAS,
            "scale": SCALES,
            "width": WIDTHS,
        }[rid]

    def label_of(self, rid, value):
        if rid == "destination":
            return f"{DESTINATIONS[value][0]} ({DESTINATIONS[value][1]})"
        if rid == "color":
            return COLOR_LABELS[value]
        if rid == "width":
            return value if value == "Hairline" else f"{value} mm"
        if rid == "size" and value == print2d.CUSTOM:
            return "Custom..."
        if rid == "size":
            w, h = print2d.PAGE_SIZES_MM[value]
            return f"{value}  ({w:g} x {h:g} mm)"
        return str(value) or "--"

    def shown(self, rid):
        if rid in ("custom_w", "custom_h"):
            if self.focus == rid:
                return self.edit_text
            return f"{getattr(self, rid):g}"
        v = getattr(self, rid)
        if rid == "selected_only":
            return "Yes" if v else "No"
        if rid == "orientation" and self.size == print2d.CUSTOM:
            return "(from width x height)"
        if rid == "size" and v == print2d.CUSTOM:
            return f"Custom  ({self.custom_w:g} x {self.custom_h:g} mm)"
        return self.label_of(rid, v)

    # -- what the settings mean --------------------------------------------
    def extension(self):
        return DESTINATIONS[self.destination][1]

    def width_mm(self):
        return print2d.HAIRLINE_MM if self.width == "Hairline" else float(self.width)

    def scale_value(self):
        from .print_commands import parse_scale

        return parse_scale(self.scale)

    def live_value(self, rid):
        """A valid size as it is typed, otherwise the last committed value."""
        if self.focus == rid:
            try:
                v = float(self.edit_text)
            except ValueError:
                v = None
            if v is not None and 0 < v <= MAX_CUSTOM_MM:
                return v
        return getattr(self, rid)

    def setup(self):
        """The `print2d.PageSetup` these settings describe (typed sizes included)."""
        return print2d.PageSetup(size=self.size, orientation=self.orientation,
                                 area=self.area, scale=self.scale_value(),
                                 color=self.color,
                                 default_width_mm=self.width_mm(),
                                 custom_mm=(self.live_value("custom_w"),
                                            self.live_value("custom_h")),
                                 cdr_version=int(self.cdr_version))


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------

@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    def contains(self, px, py):
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h

    @property
    def center(self):
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)


@dataclass
class Layout:
    panel: Rect
    title: Rect
    rows: list          # (rid, label_rect, value_rect)
    preview: Rect
    buttons: list       # (bid, rect)
    dropdown: list = field(default_factory=list)   # (index, rect) of the open list
    scale: float = 1.0


def layout(region_w, region_h, ui_scale=1.0, state=None):
    """Where everything goes in a region of this size. Pure arithmetic.

    The panel takes most of the region, centred; controls in a left column (38 %),
    the preview on the right, and buttons along the bottom. With
    a `state`, only its visible rows are laid out and its open dropdown's list is
    placed (below the box, or above it when there is no room below).
    """
    s = max(0.5, float(ui_scale))
    pw = min(region_w - 40 * s, 1100 * s)
    ph = min(region_h - 40 * s, 720 * s)
    px = (region_w - pw) / 2.0
    py = (region_h - ph) / 2.0
    panel = Rect(px, py, pw, ph)
    title_h = 30 * s
    title = Rect(px, py + ph - title_h, pw, title_h)
    pad = 14 * s
    btn_h = 30 * s
    # Keep value boxes wide enough for labels such as "Letter  (215.9 x 279.4 mm)".
    left_w = pw * 0.44
    row_h = 30 * s
    top = py + ph - title_h - pad
    label_w = left_w * 0.40
    vis = state.visible_rows() if state is not None else list(ROWS)
    rows = []
    for i, (rid, _label, _kind) in enumerate(vis):
        y = top - (i + 1) * row_h
        rows.append((rid, Rect(px + pad, y, label_w - pad, row_h - 4 * s),
                     Rect(px + label_w, y, left_w - label_w - pad, row_h - 4 * s)))
    buttons = []
    bw = 110 * s
    for j, (bid, _label) in enumerate(reversed(BUTTONS)):
        bx = px + pw - pad - (j + 1) * bw - j * 10 * s
        buttons.append((bid, Rect(bx, py + pad, bw, btn_h)))
    buttons.reverse()
    prev_x = px + left_w + pad
    preview = Rect(prev_x, py + pad + btn_h + pad,
                   px + pw - pad - prev_x, ph - title_h - 3 * pad - btn_h)
    lay = Layout(panel, title, rows, preview, buttons, [], s)
    if state is not None and state.open_dropdown:
        lay.dropdown = dropdown_items(lay, state.open_dropdown,
                                      len(state.choices(state.open_dropdown)),
                                      region_h)
    return lay


def dropdown_items(lay, rid, n, region_h):
    """``[(index, rect)]`` for an open list of `n` items under (or over) row `rid`."""
    box = next((vr for r, _l, vr in lay.rows if r == rid), None)
    if box is None:
        return []
    item_h = 24 * lay.scale
    # at least wide enough for the longest label ("Letter  (215.9 x 279.4 mm)")
    width = max(box.w, 300 * lay.scale)
    total = n * item_h
    below = box.y - 2 * lay.scale
    downward = below - total >= 0 or below >= region_h - (box.y + box.h)
    out = []
    for i in range(n):
        if downward:
            y = box.y - (i + 1) * item_h
        else:
            y = box.y + box.h + (n - 1 - i) * item_h
        out.append((i, Rect(box.x, y, width, item_h)))
    return out


def notes_rect(lay):
    """The free space under the controls, where the status / NOTE text goes."""
    last = min((vr.y for _r, _l, vr in lay.rows), default=lay.panel.y + lay.panel.h)
    left = lay.panel.x + 14 * lay.scale
    return Rect(left, lay.panel.y + 14 * lay.scale, lay.preview.x - left - 14 * lay.scale,
                last - lay.panel.y - 28 * lay.scale)


def wrap(text, width, measure):
    """Greedy word-wrap of `text` to `width` using `measure(str) -> px`."""
    lines, cur = [], ""
    for word in (text or "").split():
        trial = f"{cur} {word}".strip()
        if cur and measure(trial) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def hit(lay, x, y):
    """What is under (x, y): ``("item", i)`` in an open list, ``("row", rid)``,
    ``("button", bid)``, ``("panel", None)``, or None (outside the dialog)."""
    for i, r in lay.dropdown:
        if r.contains(x, y):
            return ("item", i)
    for rid, label_r, value_r in lay.rows:
        if value_r.contains(x, y) or label_r.contains(x, y):
            return ("row", rid)
    for bid, r in lay.buttons:
        if r.contains(x, y):
            return ("button", bid)
    if lay.panel.contains(x, y):
        return ("panel", None)
    return None


# ---------------------------------------------------------------------------
# interaction -- pure state transitions
# ---------------------------------------------------------------------------

#: where a field's text starts inside its box, at scale 1 (ui.py draws it there)
FIELD_TEXT_PAD = 6
#: Maximum character count for a size in millimetres.
FIELD_MAX_CHARS = 12


def field_of(state):
    """The `textfield.TextField` behind the focused field's synchronized `edit_text`."""
    from . import textfield

    f = state.extra.get("field")
    if f is None or state.extra.get("field_for") != state.focus:
        f = textfield.TextField(state.edit_text, multiline=False,
                                max_chars=FIELD_MAX_CHARS)
        state.extra["field"], state.extra["field_for"] = f, state.focus
    elif f.text != state.edit_text:
        f.set_text(state.edit_text)
    return f


def _focus_field(state, rid):
    state.focus, state.edit_text = rid, f"{getattr(state, rid):g}"
    f = field_of(state)
    # focusing by Tab or by choosing Custom selects the number, so typing replaces it
    # (the desktop convention); a click then puts the caret where it landed instead
    f.select_all()
    return f


def field_index_at(state, lay, rid, x, measure):
    """The caret index under window-x `x` in field `rid`'s box."""
    from . import textfield

    vr = next((v for r, _l, v in lay.rows if r == rid), None)
    if vr is None:
        return 0
    text = state.edit_text
    x0 = vr.x + FIELD_TEXT_PAD * lay.scale
    return textfield.index_at([(0, len(text))], text, x - x0, 0, measure)


def commit_field(state):
    """Apply the focused field's text. Returns True if a value changed."""
    rid = state.focus
    if rid is None:
        return False
    state.focus = None
    text = state.edit_text.strip()
    state.edit_text = ""
    try:
        value = float(text)
    except ValueError:
        state.message = f"{ROW_LABELS[rid].strip()}: {text!r} is not a number"
        return False
    if not (0 < value <= MAX_CUSTOM_MM):
        state.message = (f"{ROW_LABELS[rid].strip()}: must be more than 0 and at most "
                         f"{MAX_CUSTOM_MM:g} mm")
        return False
    changed = value != getattr(state, rid)
    setattr(state, rid, value)
    return changed


def click(state, lay, x, y, measure=None, shift=False):
    """A left click at (x, y). Mutates `state`; returns what happened:

    ``"chose:<rid>"`` (a value changed -- rebuild the page), ``"opened:<rid>"``,
    ``"closed"`` (an open list was dismissed), ``"toggled:<rid>"``,
    ``"focus:<rid>"``, ``"button:<bid>"``, ``"panel"`` or ``"outside"``.

    A click in a size field that ALREADY has focus places the caret under it (Shift
    extends the selection) and arms a drag-select -- with the drawing font's `measure`.
    """
    h = hit(lay, x, y)
    changed_field = False
    if state.focus is not None and h != ("row", state.focus):
        changed_field = commit_field(state)
    if state.open_dropdown is not None:
        rid = state.open_dropdown
        state.open_dropdown = None
        state.hover = None
        if h is not None and h[0] == "item":
            new = state.choices(rid)[h[1]]
            old = getattr(state, rid)
            setattr(state, rid, new)
            if rid == "destination" and state.extension() == ".cdr" and state.scale == "Fit":
                state.scale = "1:1"

            if rid == "size" and new == print2d.CUSTOM and old != print2d.CUSTOM:
                _focus_field(state, "custom_w")
            return f"chose:{rid}"
        # a click outside an open list only closes it, like every desktop dropdown
        return "closed"
    if h is None:
        return "chose:field" if changed_field else "outside"
    kind, ident = h
    if kind == "row":
        rk = ROW_KINDS[ident]
        if rk == "choice":
            state.open_dropdown = ident
            cur = getattr(state, ident)
            opts = state.choices(ident)
            state.hover = opts.index(cur) if cur in opts else None
            return f"opened:{ident}"
        if rk == "check":
            setattr(state, ident, not getattr(state, ident))
            return f"toggled:{ident}"
        first = state.focus != ident
        if first:
            _focus_field(state, ident)
        if measure is not None:
            f = field_of(state)
            f.set_caret(field_index_at(state, lay, ident, x, measure),
                        select=shift and not first)
            state.extra["dragging"] = True
        return f"focus:{ident}"
    if kind == "button":
        return f"button:{ident}"
    return "chose:field" if changed_field else "panel"


def move(state, lay, x, y, measure=None):
    """Mouse moved: track the highlighted item of an open list, or extend a drag-select
    in the focused field. True if anything changed."""
    if state.extra.get("dragging") and state.focus is not None and measure is not None:
        f = field_of(state)
        before = (f.anchor, f.caret)
        f.set_caret(field_index_at(state, lay, state.focus, x, measure), select=True)
        return (f.anchor, f.caret) != before
    if state.open_dropdown is None:
        return False
    h = hit(lay, x, y)
    new = h[1] if h is not None and h[0] == "item" else state.hover
    if new != state.hover:
        state.hover = new
        return True
    return False


def release(state):
    state.extra.pop("dragging", None)


def key(state, kind, char="", shift=False):
    """A key while the dialog is open. `kind`: "char", "back", "delete", "enter",
    "tab", "esc", "left", "right", "home", "end", "select_all", "copy", "cut",
    "paste" (`char` = the clipboard). `shift` extends the selection with a motion;
    copy/cut leave their text in ``state.extra["clipboard"]``.

    Returns ``"chose:field"`` (a field committed a new value -- rebuild),
    ``"edited"``, ``"closed"`` (an open list was dismissed), ``"handled"``, or None
    when the key is not the dialog's (Esc with nothing open closes the dialog --
    the caller decides that).
    """
    if state.open_dropdown is not None:
        if kind == "esc":
            state.open_dropdown, state.hover = None, None
            return "closed"
        if kind == "enter" and state.hover is not None:
            rid = state.open_dropdown
            setattr(state, rid, state.choices(rid)[state.hover])
            if rid == "destination" and state.extension() == ".cdr" and state.scale == "Fit":
                state.scale = "1:1"

            state.open_dropdown, state.hover = None, None
            return f"chose:{rid}"
        return "handled"
    if state.focus is None:
        return None
    f = field_of(state)
    if kind in ("char", "paste"):
        # a size takes digits and ONE decimal point -- counted without the selection
        # being replaced, so selecting "2.5" and typing "." is allowed
        sel = f.selection()
        rest = f.text if sel is None else f.text[:sel[0]] + f.text[sel[1]:]
        ok = "".join(c for c in char if c.isdigit() or c == ".")
        if ok.count(".") + rest.count(".") > 1 or not ok:
            return "handled"
        f.insert(ok)
        state.edit_text = f.text
        return "chose:typing"           # rebuild the preview at the typed size
    if kind in ("back", "delete", "cut"):
        if kind == "cut":
            state.extra["clipboard"] = f.cut()
        elif kind == "back":
            f.backspace()
        else:
            f.delete()
        state.edit_text = f.text
        return "chose:typing"
    if kind == "copy":
        state.extra["clipboard"] = f.selected_text()
        return "handled"
    if kind in ("left", "right", "home", "end", "select_all"):
        {"left": lambda: f.move(-1, select=shift), "right": lambda: f.move(1, select=shift),
         "home": lambda: f.home(select=shift), "end": lambda: f.end(select=shift),
         "select_all": f.select_all}[kind]()
        return "handled"
    if kind == "esc":
        state.focus, state.edit_text = None, ""
        return "handled"
    if kind in ("enter", "tab"):
        nxt = "custom_h" if (kind == "tab" and state.focus == "custom_w") else None
        changed = commit_field(state)
        if nxt is not None:
            _focus_field(state, nxt)
        return "chose:field" if changed else "handled"
    return "handled"


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------

def page_rect(preview, page_w_mm, page_h_mm, margin_px=16.0):
    """The sheet inside the preview pane: largest rectangle of the PAGE's aspect."""
    aw = max(1.0, preview.w - 2 * margin_px)
    ah = max(1.0, preview.h - 2 * margin_px)
    k = min(aw / page_w_mm, ah / page_h_mm)
    w, h = page_w_mm * k, page_h_mm * k
    return Rect(preview.x + (preview.w - w) / 2.0, preview.y + (preview.h - h) / 2.0,
                w, h)


def _cubic(c, t):
    s = 1.0 - t
    return tuple(s * s * s * a + 3 * s * s * t * b + 3 * s * t * t * cc + t * t * t * d
                 for a, b, cc, d in zip(*c))


def preview_lines(page, sheet, samples=16):
    """The page's strokes as screen polylines inside `sheet` -- WHAT WILL PRINT.

    Built from the very `print2d.Page` the PDF writer receives, so the preview cannot
    show something other than the file. Returns ``[(points, rgb, width_px)]``.
    """
    k = sheet.w / page.width_mm if page.width_mm else 1.0
    out = []
    for st in page.strokes:
        pts = []
        for c in st.beziers:
            for i in range(samples + 1):
                if pts and i == 0:
                    continue
                x, y = _cubic(c, i / samples)
                pts.append((sheet.x + x * k, sheet.y + y * k))
        out.append((pts, st.rgb, max(1.0, st.width_mm * k)))
    return out
