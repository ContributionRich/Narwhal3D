"""Pure layout and content model for Narwhal 3D's right dock.

The dock has four working panels—Layers, Properties, Display, and Bug Report—in a
fixed vertical tab strip. Sizes and colors are project UI constants. The panel body
uses all remaining live width so resizing the dock expands useful content rather than
scaling icons. Unsupported controls are omitted instead of being drawn as dead chrome.
"""

from .object_properties import (
    ObjectProperties, aggregate, aggregate_capabilities, MIXED, UNAVAILABLE,
)


def _display_value(aggregate_value):
    return ("varies" if aggregate_value.state == MIXED else
            "unavailable" if aggregate_value.state == UNAVAILABLE else
            aggregate_value.value)


def property_rows(selection):
    """Return Rhino-style Object Properties rows for zero or more objects.

    This is deliberately a pure view adapter: it only aggregates supplied
    :class:`ObjectProperties` values.  Unsupported/non-persistent fields are
    omitted rather than presented as editable document state.
    """
    selection = tuple(selection)
    if not selection:
        return [("", "No object selected")]
    rows = []
    if len(selection) > 1:
        rows.append(("Selected", f"{len(selection)} objects"))
    values = aggregate(selection)
    caps = aggregate_capabilities(selection)
    ordered = (
        ("Type", "type"), ("Name", "name"), ("Layer", "layer"),
        ("Display Color source", "display_color_source"),
        ("Display Color", "display_color"),
        ("Linetype", "linetype"), ("Linetype source", "linetype_source"),
        ("Linetype scale", "linetype_scale"),
        ("Print source", "print_color_source"),
        ("Print width source", "print_width_source"),
        ("Print color", "print_color"),
        ("Print width", "print_width"), ("No print", "no_print"),
    )
    for label, field_name in ordered:
        # A field can be inspected in an adapter but still not be persistent;
        # do not make that look like a document property we can edit/save.
        if not caps[field_name].available and not caps[field_name].persistent:
            continue
        rows.append((label, _display_value(values[field_name])))
    return rows


# Descriptive alias for callers and focused tests.
format_property_rows = property_rows


#: Default band width in project logical pixels.
BAND_W = 200

#: Width of the vertical tab strip down the band's right edge.
TAB_STRIP_W = 21

#: Vertical distance between tab centres.
TAB_PITCH = 36

#: Fixed tab icon edge length; dock resizing does not scale bitmap artwork.
TAB_ICON = 16

#: Height of the optional caption row.
TITLE_H = 21


class Tab:
    """One tab of the Right Container.

    `built` is the honest flag: False means the panel's BODY is not implemented,
    and the draw says so rather than showing empty chrome that reads as a panel
    with nothing in it.
    """

    __slots__ = ("name", "icon", "built", "reason")

    def __init__(self, name, icon, built=False, reason=""):
        self.name = name
        self.icon = icon
        self.built = bool(built)
        self.reason = reason

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Tab({self.name!r}, built={self.built})"


#: Working panels in product order. Every icon is generated and owned by Narwhal 3D.
TABS = (
    Tab("Layers", "narwhal-panel-layers.png", built=True, reason=""),
    Tab("Properties", "narwhal-panel-properties.png", built=True, reason=""),
    Tab("Display", "narwhal-panel-display.png", built=True, reason=""),
    Tab("Bug Report", "narwhal-panel-report.png", built=True, reason=""),
)

#: Layers is the default working panel.
DEFAULT_TAB = 0


def tab_names():
    """Return right-dock tab names in product order."""
    return tuple(t.name for t in TABS)


def layout(width, height, title_h=None):
    """Lay the band out in REGION coordinates (origin bottom-left, y upward).

    Returns a dict of boxes, each ``(x0, y0, x1, y1)``:

    ``title``
        the caption row, full width, along the top. ZERO-HEIGHT when the host
        already provides one -- see `title_h` below.
    ``strip``
        the vertical tab strip, down the right edge, below the title.
    ``tabs``
        one box per :data:`TABS` entry, TOP-DOWN inside ``strip``. Shorter than
        ``TABS`` if the band is too short to hold them all -- the band is painted
        at whatever height Blender gave it, so this cannot assume there is room.
    ``body``
        everything left for the active panel's content.

    `title_h` defaults to the Verified Rhino caption height, but the drawing site
    passes **0**: our band is hosted in a Blender ``INFO`` area, whose own header
    region already sits above the WINDOW region we paint and already carries the
    caption (the left dock band does the same). Drawing a second one inside the
    window would stack two caption rows. The measured number is kept because it is
    the fact, and because a future host without a header would need it.

    Blender's region coordinates put y=0 at the BOTTOM, while every number this
    module measured is a distance DOWN from the top, so the conversion happens
    here, once, instead of at each drawing site.
    """
    title_h = TITLE_H if title_h is None else title_h   # read now: uiscale may have rescaled it
    width = max(0, int(width))
    height = max(0, int(height))
    title_h = max(0, min(int(title_h), height))
    top = height
    title = (0, top - title_h, width, top)

    strip_x0 = max(0, width - TAB_STRIP_W)
    strip_top = top - title_h
    strip = (strip_x0, 0, width, strip_top)

    tabs = []
    for i in range(len(TABS)):
        y1 = strip_top - i * TAB_PITCH
        y0 = y1 - TAB_PITCH
        if y0 < 0:
            break
        tabs.append((strip_x0, y0, width, y1))

    body = (0, 0, strip_x0, strip_top)
    return {"title": title, "strip": strip, "tabs": tabs, "body": body}


def hit_tab(x, y, tabs):
    """Index of the tab box containing ``(x, y)``, or None.

    ``tabs`` is :func:`layout`'s ``tabs`` list in the SAME coordinate space as
    ``x``/``y``. Kept separate from `layout` so the click path tests against the
    boxes the draw recorded, which is the rule the command bar's option boxes and
    the sidebar's cells both follow -- hit-testing recomputed geometry has twice
    let dead chrome stay green in this project.
    """
    for i, (x0, y0, x1, y1) in enumerate(tabs):
        if x0 <= x < x1 and y0 <= y < y1:
            return i
    return None


def icon_box(tab_box, size=None):
    """The square an icon occupies, centred in its tab box."""
    size = TAB_ICON if size is None else size   # read now: uiscale may have rescaled it
    x0, y0, x1, y1 = tab_box
    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    half = size * 0.5
    return (cx - half, cy - half, cx + half, cy + half)


# ---------------------------------------------------------------------------
# THE LAYERS PANEL'S LIST
#
# Fixed logical-pixel dimensions keep the compact state columns aligned while the
# layer-name column takes any extra dock width.
#
# The list and its header are real and interactive. The panel's measured SEARCH BOX
# and two 16 px toolbar rows (23 px tall; 11 buttons at a 22 px pitch) are deliberately
# omitted: drawing a search field that cannot accept input or buttons that do nothing
# would falsely present unsupported controls as implemented.
# ---------------------------------------------------------------------------

#: Column header height in logical pixels.
HEADER_H = 24

#: One layer row in logical pixels.
ROW_H = 20

#: Minimum width of the Name column.
NAME_COL_W = 79

#: Width of every compact state column.
NARROW_COL_W = 20

#: The columns follow Rhino's Layer Properties order: Name, Current, On/Off,
#: Lock/Unlock, Color, Material, Linetype, Print Color, Print Width, and Section Style.
#: The compact panel exposes the first six.
COLUMNS = ("Name", "Current", "On", "Lock", "Color", "Material")


def name_column_width(width=None, count=len(COLUMNS)):
    """Responsive Name-column width; measured width at the default panel size.

    Rhino's compact state columns remain fixed while the descriptive Layer/Name
    column consumes additional dock width. ``None`` preserves the measured default.
    """
    if width is None:
        return NAME_COL_W
    fixed = max(0, int(count) - 1) * NARROW_COL_W
    return max(NAME_COL_W, float(width) - fixed)


def column_x(index, width=None, count=len(COLUMNS)):
    """``(x0, x1)`` of column `index` within the list, 0 = Name."""
    name_w = name_column_width(width, count)
    if index <= 0:
        return (0, name_w)
    x0 = name_w + (index - 1) * NARROW_COL_W
    return (x0, x0 + NARROW_COL_W)


def column_dividers(count=len(COLUMNS), width=None):
    """Divider positions; measured at default width and responsive when widened."""
    return [column_x(i, width, count)[0] for i in range(1, count)]


def list_layout(body, n_rows):
    """Lay the Layers list out inside the panel's `body` box.

    `body` is :func:`layout`'s ``body`` entry, in REGION coordinates (y upward).
    Returns a dict:

    ``header``
        the column-header row, along the top of the body.
    ``rows``
        one ``(x0, y0, x1, y1)`` per layer, TOP-DOWN -- shorter than `n_rows` when
        the panel is too short, because the band is painted at whatever height
        Blender gave it.
    ``dividers``
        ``[(x, y0, y1)]`` for the vertical column rules, spanning header + rows.
    ``list``
        the whole white list area (header + rows), so the draw can paint one
        background rather than per-row.
    """
    bx0, by0, bx1, by1 = body
    width = max(0, bx1 - bx0)
    header_h = min(HEADER_H, max(0, by1 - by0))
    header = (bx0, by1 - header_h, bx1, by1)

    rows = []
    top = by1 - header_h
    for i in range(max(0, int(n_rows))):
        y1 = top - i * ROW_H
        y0 = y1 - ROW_H
        if y0 < by0:
            break
        rows.append((bx0, y0, bx1, y1))

    bottom = rows[-1][1] if rows else header[1]
    dividers = [(bx0 + x, bottom, by1) for x in column_dividers(width=width)
                if x < width]
    return {"header": header, "rows": rows, "dividers": dividers,
            "list": (bx0, bottom, bx1, by1)}


def cell(row_box, index):
    """``(x0, y0, x1, y1)`` of one column's cell inside a row box."""
    rx0, ry0, rx1, ry1 = row_box
    cx0, cx1 = column_x(index, max(0, rx1 - rx0))
    return (rx0 + cx0, ry0, min(rx0 + cx1, rx1), ry1)


def hit_row(x, y, rows):
    """Index of the row containing ``(x, y)``, or None."""
    for i, (x0, y0, x1, y1) in enumerate(rows):
        if x0 <= x < x1 and y0 <= y < y1:
            return i
    return None


def hit_column(x, row_box):
    """Index into :data:`COLUMNS` for a point's x within a row, or None.

    Returns None past the last column rather than clamping to it, so a click in the
    clipped remainder of the list does nothing instead of toggling Material.
    """
    rx0 = row_box[0]
    for i in range(len(COLUMNS)):
        cx0, cx1 = column_x(i, max(0, row_box[2] - rx0))
        if rx0 + cx0 <= x < rx0 + cx1:
            return i
    return None


# ---------------------------------------------------------------------------
# DISPLAY PANEL -- common per-viewport mode controls
#
# The panel exposes the existing Active/All scope and Wireframe/Shaded/Rendered
# modes. Its metrics reuse the Layers row rhythm.
# ---------------------------------------------------------------------------
DISPLAY_PAD = 8
DISPLAY_LABEL_H = 18
DISPLAY_ROW_H = 26
DISPLAY_GAP = 6


def display_panel_layout(body, viewport_names=(), mode_names=()):
    """Responsive boxes for Display scope, mode, and viewport rows."""
    bx0, by0, bx1, by1 = body
    x0, x1 = bx0 + DISPLAY_PAD, max(bx0 + DISPLAY_PAD, bx1 - DISPLAY_PAD)
    width = max(0, x1 - x0)
    y = by1 - DISPLAY_PAD
    labels = []

    def label(name):
        nonlocal y
        y0 = max(by0, y - DISPLAY_LABEL_H)
        labels.append((name, x0, y0, x1, y))
        y = y0

    def row(kind, value):
        nonlocal y
        y1_ = y
        y0_ = y1_ - DISPLAY_ROW_H
        if width <= 0 or y0_ < by0:
            return None
        y = y0_ - DISPLAY_GAP
        return (kind, value, x0, y0_, x1, y1_)

    label("Apply to")
    scope = []
    scope_y1 = y
    scope_y0 = scope_y1 - DISPLAY_ROW_H
    if width > DISPLAY_GAP and scope_y0 >= by0:
        mid = x0 + (width - DISPLAY_GAP) * 0.5
        scope = [("scope", "Active", x0, scope_y0, mid, scope_y1),
                 ("scope", "All", mid + DISPLAY_GAP, scope_y0, x1, scope_y1)]
        y = scope_y0 - DISPLAY_GAP

    label("Display mode")
    modes = [box for name in mode_names if (box := row("mode", name)) is not None]
    label("Viewports")
    viewports = [box for name in viewport_names
                 if (box := row("viewport", name)) is not None]
    return {"labels": labels, "scope": scope, "modes": modes,
            "viewports": viewports}


def display_panel_hit(x, y, layout):
    """``(kind, value)`` under a point, against the boxes actually drawn."""
    for key in ("scope", "modes", "viewports"):
        for kind, value, x0, y0, x1, y1 in (layout or {}).get(key, ()):
            if x0 <= x < x1 and y0 <= y < y1:
                return kind, value
    return None


def rules():
    """The measured facts this module asserts, for the test suite to pin.

    Same shape as `layers.rules` and `osnap`'s: the numbers live in one place so
    a test cannot drift from the docstring.
    """
    return {
        "band_w": BAND_W,
        "tab_strip_w": TAB_STRIP_W,
        "tab_pitch": TAB_PITCH,
        "tab_icon": TAB_ICON,
        "title_h": TITLE_H,
        "tabs": tab_names(),
        "default_tab": DEFAULT_TAB,
        "dpi_scale_of_screenshot": 1.75,
        "header_h": HEADER_H,
        "row_h": ROW_H,
        "name_col_w": NAME_COL_W,
        "narrow_col_w": NARROW_COL_W,
        "columns": COLUMNS,
        "dividers": tuple(column_dividers()),
    }
