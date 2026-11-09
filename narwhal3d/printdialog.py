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
#: Typed custom sizes are shown to this many decimals of the paper unit.
FIELD_DIGITS = 4
#: The Custom sheet a document starts with, per paper unit (mm): A4 for metric, Letter
#: (8.5 x 11 in) for inch. OURS -- no reference help page says what a custom sheet defaults to.
DEFAULT_CUSTOM_MM = {"Millimeters": (210.0, 297.0), "Centimeters": (210.0, 297.0),
                     "Inches": (215.9, 279.4)}


def default_custom_mm(unit):
    """``(width, height)`` in mm of the default Custom sheet for document `unit`."""
    return DEFAULT_CUSTOM_MM[print2d.paper_unit(unit)[0]]

#: (id, label, kind) -- kind: "choice" (a dropdown), "check", "field"
ROWS = (
    ("destination", "Printer", "choice"),
    ("cdr_version", "CorelDRAW version", "choice"),
    ("size", "Size", "choice"),
    ("custom_w", "  Width", "field"),        # + the paper unit: DialogState.row_label
    ("custom_h", "  Height", "field"),
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

# Advance widths in em of Blender's bundled UI font (blf font 0), MEASURED on Blender
# 5.2.2 as blf.dimensions at size 1000 / 1000 for U+0020..U+007E, then U+2713 (the
# check mark) and U+25BE (the dropdown arrow). The pure layout cannot call blf.
_EM_ASCII = (
    0.269, 0.220, 0.403, 0.600, 0.614, 0.844, 0.609, 0.254, 0.391, 0.391,
    0.645, 0.645, 0.269, 0.645, 0.269, 0.325, 0.645, 0.645, 0.645, 0.645,
    0.645, 0.645, 0.645, 0.645, 0.645, 0.645, 0.269, 0.269, 0.645, 0.645,
    0.645, 0.524, 0.974, 0.653, 0.638, 0.722, 0.689, 0.580, 0.554, 0.730,
    0.708, 0.339, 0.535, 0.634, 0.535, 0.857, 0.710, 0.749, 0.612, 0.749,
    0.632, 0.614, 0.610, 0.703, 0.653, 0.949, 0.646, 0.643, 0.613, 0.391,
    0.325, 0.391, 0.438, 0.455, 0.241, 0.518, 0.565, 0.523, 0.565, 0.536,
    0.306, 0.565, 0.547, 0.206, 0.234, 0.510, 0.230, 0.839, 0.547, 0.549,
    0.565, 0.565, 0.322, 0.475, 0.311, 0.547, 0.512, 0.752, 0.507, 0.512,
    0.479, 0.391, 0.296, 0.391, 0.645,
)
_ADVANCE_EM = {chr(32 + i): w for i, w in enumerate(_EM_ASCII)}
_ADVANCE_EM.update({"\u2713": 0.850, "\u25be": 0.370})
#: Summed advances run up to 7 % UNDER the drawn width (kerning), and blf rounds each
#: glyph's advance to whole pixels at small sizes. The margin and the half pixel per glyph
#: keep every estimate on the wide side: MEASURED to bound blf for every string the dialog
#: draws, at scales 0.6 to 2.5.
_TEXT_MARGIN = 1.10
_GLYPH_PX = 0.5
#: Glyphs the dialog never draws are costed as wide.
_DEFAULT_EM = 0.70


def restore_settings(state, saved):
    """Apply remembered dialog settings (`preferences` 'print_settings') to `state`.

    The View is contextual and never restored. A remembered Custom size that is exactly
    a DEFAULT sheet (the dialog saves the size whether or not Custom was used) is not a
    choice, so the document's own default stands: Letter on an inch document, A4 on a
    mm one. Any other remembered size is restored as typed.
    """
    saved = {k: v for k, v in dict(saved).items() if k not in ("view", "unit")}
    pair = (saved.get("custom_w"), saved.get("custom_h"))
    if pair in DEFAULT_CUSTOM_MM.values():
        saved.pop("custom_w", None)
        saved.pop("custom_h", None)
    for key, value in saved.items():
        setattr(state, key, value)
    return state


def text_width(text, size=13.0):
    """Estimated drawn width in px of `text` at font `size` px (a conservative upper bound)."""
    text = text or ""
    return (sum(_ADVANCE_EM.get(c, _DEFAULT_EM) for c in text) * float(size) * _TEXT_MARGIN
            + _GLYPH_PX * len(text))


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
    custom_w: float | None = None    # ALWAYS mm; shown and typed in the paper unit;
    custom_h: float | None = None    # None = the document's default (`default_custom_mm`)
    #: the DOCUMENT's unit system; page lengths are shown in its paper unit
    #: (`print2d.paper_unit`). Line widths stay mm -- MEASURED, commands/print.htm:
    #: "normal drafting widths (in millimeters)".
    unit: str = "Millimeters"
    message: str = ""
    # interaction state
    open_dropdown: str | None = None
    hover: int | None = None
    focus: str | None = None
    edit_text: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        w, h = default_custom_mm(self.unit)
        if self.custom_w is None:
            self.custom_w = w
        if self.custom_h is None:
            self.custom_h = h

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

    def paper(self):
        """``(unit system, abbreviation, mm per unit)`` page lengths are shown in."""
        return print2d.paper_unit(self.unit)

    def row_label(self, rid):
        """The drawn label of row `rid`; the size fields name the paper unit."""
        if ROW_KINDS[rid] == "field":
            return f"{ROW_LABELS[rid]} ({self.paper()[1]})"
        return ROW_LABELS[rid]

    def _size_text(self, w_mm, h_mm):
        return (f"{print2d.show_length(w_mm, self.unit)} x "
                f"{print2d.show_length(h_mm, self.unit)} {self.paper()[1]}")

    def field_text(self, rid):
        """A committed custom size (mm) as typed text in the paper unit."""
        return self._typed(getattr(self, rid))

    def _typed(self, mm):
        text = f"{mm / self.paper()[2]:.{FIELD_DIGITS}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    def page_text(self, page):
        """The status line's page size, in the paper unit."""
        return self._size_text(page.width_mm, page.height_mm)

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
            return f"{value}  ({self._size_text(w, h)})"
        return str(value) or "--"

    def shown(self, rid):
        if rid in ("custom_w", "custom_h"):
            if self.focus == rid:
                return self.edit_text
            return self.field_text(rid)
        v = getattr(self, rid)
        if rid == "selected_only":
            return "Yes" if v else "No"
        if rid == "orientation" and self.size == print2d.CUSTOM:
            return "(from width x height)"
        if rid == "size" and v == print2d.CUSTOM:
            return (f"Custom  ({self._typed(self.custom_w)} x {self._typed(self.custom_h)} "
                    f"{self.paper()[1]})")
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
                v = float(self.edit_text) * self.paper()[2]
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


#: Layout figures at scale 1 (each is multiplied by the interface scale).
LABEL_GAP = 12
#: Right edge of a value box kept clear for its dropdown arrow (16 px glyph + gap).
ARROW_ZONE = 20
ROW_H, ROW_MIN_H = 30, 24
ITEM_H, LIST_MIN_W = 24, 300
#: Height kept free under the rows for the status text (two lines).
NOTES_RESERVE = 62
#: The smallest region (at scale 1) the dialog is laid out for without clipping. It
#: scales with the interface, as every figure here does.
MIN_REGION = (680.0, 480.0)


def _column_needs(state, s):
    """``(label, value)`` widths in px: the longest label, and the widest value box any
    row needs for its options or current text. Taken over EVERY row, so the columns do
    not move when a setting changes."""
    fs = 13.0 * s
    label = max(text_width(state.row_label(rid), fs) for rid, _text, _kind in ROWS)
    value = 16.0 * s                                  # a check box
    for rid, _text, kind in ROWS:
        if kind == "check":
            continue
        if kind == "field":
            texts = ["0" * FIELD_MAX_CHARS]
        else:
            texts = [state.label_of(rid, o) for o in state.choices(rid)] + [state.shown(rid)]
        widest = max(text_width(t, fs) for t in texts)
        value = max(value, widest + (FIELD_TEXT_PAD + ARROW_ZONE) * s)
    return label, value


def layout(region_w, region_h, ui_scale=1.0, state=None):
    """Where everything goes in a region of this size. Pure arithmetic.

    The panel takes most of the region, centred. The controls form a left column whose
    label and value columns are as wide as their measured text (`text_width`), so no
    label runs under its value box; the preview takes the rest of the right; buttons run
    along the bottom. Rows shrink (to `ROW_MIN_H`) before they reach the status text.
    With a `state`, only its visible rows are laid out and its open dropdown's list is
    placed by `dropdown_items`.
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
    label_need, value_need = _column_needs(state if state is not None else DialogState(), s)
    label_col = pad + label_need + LABEL_GAP * s
    left_w = max(pw * 0.44, label_col + value_need + pad)
    top = py + ph - title_h - pad
    vis = state.visible_rows() if state is not None else list(ROWS)
    row_h = min(ROW_H * s, max(ROW_MIN_H * s, (top - py - NOTES_RESERVE * s) / len(vis)))
    rows = []
    for i, (rid, _label, _kind) in enumerate(vis):
        y = top - (i + 1) * row_h
        rows.append((rid, Rect(px + pad, y, label_col - pad, row_h - 4 * s),
                     Rect(px + label_col, y, left_w - label_col - pad, row_h - 4 * s)))
    buttons = []
    bw = 110 * s
    for j, (bid, _label) in enumerate(reversed(BUTTONS)):
        bx = px + pw - pad - (j + 1) * bw - j * 10 * s
        buttons.append((bid, Rect(bx, py + pad, bw, btn_h)))
    buttons.reverse()
    prev_x = px + left_w + pad
    preview = Rect(prev_x, py + pad + btn_h + pad, max(0.0, px + pw - pad - prev_x),
                   ph - title_h - 3 * pad - btn_h)
    lay = Layout(panel, title, rows, preview, buttons, [], s)
    if state is not None and state.open_dropdown:
        rid = state.open_dropdown
        opts = state.choices(rid)
        lay.dropdown = dropdown_items(lay, rid, len(opts), region_h,
                                      [state.label_of(rid, o) for o in opts])
    return lay


def dropdown_items(lay, rid, n, region_h, labels=None):
    """``[(index, rect)]`` for an open list of `n` items under (or over) row `rid`.

    The list opens on the side with room for every item at full height; if neither side
    has it, the list takes the larger side and its items shrink to fit. It never leaves
    the panel's content area. `labels` (the items' text) widen the list to fit them.
    """
    box = next((vr for r, _l, vr in lay.rows if r == rid), None)
    if box is None:
        return []
    s = lay.scale
    pad = 14 * s
    lo = lay.panel.y + pad
    hi = min(lay.title.y, region_h)
    below = box.y - lo
    above = hi - (box.y + box.h)
    item_h = ITEM_H * s
    if n * item_h <= below:
        downward = True
    elif n * item_h <= above:
        downward = False
    else:
        downward = below >= above
        item_h = max(0.0, max(below, above)) / max(1, n)
    width = max(box.w, LIST_MIN_W * s)
    if labels:
        # the item text is drawn 6 px in from the left and must end 6 px short of the right
        width = max(width, max(text_width("    " + t, 13.0 * s) for t in labels) + 12 * s)
    x = box.x
    right = lay.panel.x + lay.panel.w - pad
    if x + width > right:
        x = max(lay.panel.x + pad, right - width)
    out = []
    for i in range(n):
        if downward:
            y = box.y - (i + 1) * item_h
        else:
            y = box.y + box.h + (n - 1 - i) * item_h
        out.append((i, Rect(x, y, width, item_h)))
    return out


def notes_rect(lay):
    """The free space under the controls, where the status / NOTE text goes. It stops
    short of the preview AND of the buttons on the bottom row."""
    last = min((vr.y for _r, _l, vr in lay.rows), default=lay.panel.y + lay.panel.h)
    left = lay.panel.x + 14 * lay.scale
    right = min([lay.preview.x] + [b.x for _bid, b in lay.buttons])
    return Rect(left, lay.panel.y + 14 * lay.scale, right - 14 * lay.scale - left,
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
#: Maximum character count for a typed size (in the paper unit).
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
    state.focus, state.edit_text = rid, state.field_text(rid)
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
    _unit, abbr, unit_mm = state.paper()
    try:
        value = float(text) * unit_mm          # typed in the paper unit, kept in mm
    except ValueError:
        state.message = f"{ROW_LABELS[rid].strip()}: {text!r} is not a number"
        return False
    if not (0 < value <= MAX_CUSTOM_MM):
        state.message = (f"{ROW_LABELS[rid].strip()}: must be more than 0 and at most "
                         f"{state._typed(MAX_CUSTOM_MM)} {abbr}")
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
