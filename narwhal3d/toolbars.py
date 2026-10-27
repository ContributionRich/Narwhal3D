"""Narwhal 3D's temporary toolbar model.

The module is intentionally pure: it exposes toolbar buttons and layout geometry
without importing the host UI. Content and bundled icon names live in
:mod:`toolbar_data`; renderers consume the stable :class:`Button` API below.
"""
from __future__ import annotations

import os

from . import flyout_icon_data
from . import toolbar_data

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
V4_ICON_NAMES = frozenset({
    "v4-panel-properties.png", "v4-panel-display.png",
    "v4-panel-ai-assistant.png", "v4-viewport-maximize.png",
    "v4-viewport-restore.png",
})


def container_name():
    """Return the name of this project's toolbar container."""
    return toolbar_data.CONTAINER


def tab_names():
    """Return the original category names in display order."""
    return tuple(tab["name"] for tab in toolbar_data.TABS)


def default_tab():
    """Return the first category, or ``None`` when no categories exist."""
    names = tab_names()
    return names[0] if names else None


def _tab(name):
    for tab in toolbar_data.TABS:
        if tab["name"] == name:
            return tab
    return None


# Project-owned display metrics. Values remain compatible with the current host UI.
TAB_ROW_H = 18.0
TAB_PAD_X = 10.0
TAB_FONT_SIZE = 12.0


def tab_row_boxes(names, text_widths, width, pad_x=None):
    """Return fitted ``(name, x0, x1)`` tab boxes from left to right."""
    pad_x = TAB_PAD_X if pad_x is None else pad_x
    out = []
    x = 0.0
    for name, text_width in zip(names or (), text_widths or ()):
        box_width = text_width + pad_x * 2.0
        if x + box_width > width:
            break
        out.append((name, x, x + box_width))
        x += box_width
    return out


def tabs_hidden(names, boxes):
    """Return the number of category tabs that did not fit."""
    return max(0, len(names or ()) - len(boxes or ()))


def hit_tab_row(x, y, boxes, row_top, row_h=None):
    """Return the tab name at a point, using half-open horizontal boxes."""
    row_h = TAB_ROW_H if row_h is None else row_h
    if y < row_top - row_h or y > row_top:
        return None
    for name, x0, x1 in boxes or ():
        if x0 <= x < x1:
            return name
    return None


class Button:
    """One toolbar button using the API consumed by the host UI.

    ``left`` and ``right`` are lowercase command keys. ``link`` names an optional
    category flyout. Availability flags describe the supplied command registry;
    unavailable actions remain representable and identify themselves on hover.
    """

    __slots__ = ("text", "tooltip", "left", "right", "right_tooltip", "icon",
                 "link", "enabled", "right_enabled", "preset")

    def __init__(self, text, tooltip, left, right, right_tooltip, icon,
                 enabled, right_enabled, link=None, preset=()):
        self.text = text
        self.tooltip = tooltip
        self.left = left
        self.right = right
        self.right_tooltip = right_tooltip
        self.icon = icon
        self.link = link or None
        self.enabled = bool(enabled)
        self.right_enabled = bool(right_enabled)
        self.preset = tuple(preset or ())

    @property
    def label(self):
        """Return non-empty fallback text for renderers without an icon."""
        return self.text or self.tooltip or (self.left or "?")

    @property
    def is_separator(self):
        """Return whether this record is an intentional toolbar spacer."""
        return not any((self.text, self.tooltip, self.left, self.right, self.icon, self.link))

    def hover_text(self):
        """Identify the primary action and any alternate right-click action."""
        parts = []
        left = self.tooltip or self.text or (self.left or "")
        if left:
            parts.append(left if self.enabled else f"{left} - not implemented yet")
        if self.right:
            right = self.right_tooltip or self.right
            parts.append(f"right-click: {right}" if self.right_enabled
                         else f"right-click: {right} - not implemented yet")
        return "\n".join(parts)

    def __repr__(self):  # pragma: no cover - debugging aid
        mark = "" if self.enabled else " (disabled)"
        return f"Button({self.label!r}{mark})"


def _button(record, implemented):
    left = record.get("command")
    right = record.get("right_command")
    return Button(
        record.get("text") or "",
        record.get("tooltip") or "",
        left,
        right,
        record.get("right_tooltip") or "",
        record.get("icon"),
        bool(left and left in implemented and record.get("available", True)),
        bool(right and right in implemented),
        record.get("link"),
        record.get("preset") or (),
    )


def tab_rows(name, implemented):
    """Return buttons for one category, or ``[]`` for an unknown category."""
    tab = _tab(name)
    if tab is None:
        return []
    implemented = set(implemented or ())
    return [_button(record, implemented) for record in tab["buttons"]]


def icon_path(icon):
    """Resolve a bundled Narwhal icon, rejecting unknown or unsafe names.

    Resolution is restricted to the explicit toolbar/v4 allowlists and to real files
    whose resolved location remains inside :data:`ICON_DIR`. Missing files return
    ``None`` so a renderer can safely use button text instead.
    """
    if not isinstance(icon, str) or (
                icon not in toolbar_data.ICON_NAMES and icon not in V4_ICON_NAMES):
        return None
    if icon != os.path.basename(icon):
        return None
    root = os.path.realpath(ICON_DIR)
    path = os.path.realpath(os.path.join(root, icon))
    try:
        if os.path.commonpath((root, path)) != root:
            return None
    except ValueError:
        return None
    return path if os.path.isfile(path) else None


def icons_available():
    """Return whether every icon declared by the toolbar is bundled and safe."""
    return bool(toolbar_data.ICON_NAMES) and all(
        icon_path(name) is not None for name in toolbar_data.ICON_NAMES)


def coverage(implemented):
    """Return ``(enabled, total)`` primary actions across all category tabs."""
    rows = [button for name in tab_names() for button in tab_rows(name, implemented)]
    actionable = [button for button in rows if button.left]
    return sum(button.enabled for button in actionable), len(actionable)


# Fixed-size, responsive favorites grid. These values are original UI choices.
SIDEBAR_COLUMNS = 4
SIDEBAR_ICON = 24
SIDEBAR_PAD = 2.0
SIDEBAR_CELL = SIDEBAR_ICON + SIDEBAR_PAD * 2
SIDEBAR_MARK = 5.0
SIDEBAR_FLYOUT_HOT = 12.0
SIDEBAR_TOOLTIP_PAD = 5.0
SIDEBAR_TOOLTIP_GAP = 4.0
SIDEBAR_TOOLTIP_LINE = 14.0
SIDEBAR_TOOLTIP_FONT = 11.0
FLYOUT_COLUMNS = 5
FLYOUT_CELL = 32.0
FLYOUT_ICON = 22.0
FLYOUT_GAP = 2.0
FLYOUT_MARGIN = 4.0
FLYOUT_TITLE_H = 14.0
FLYOUT_OVERLAP = 0.0


def sidebar_name():
    """Return the project's favorites-toolbar name."""
    sidebar = getattr(toolbar_data, "SIDEBAR", None)
    return (sidebar or {}).get("name")


def sidebar_buttons(implemented=None):
    """Return favorites as :class:`Button` records."""
    sidebar = getattr(toolbar_data, "SIDEBAR", None)
    if not sidebar:
        return []
    implemented = set(implemented or ())
    return [_button(record, implemented) for record in sidebar.get("buttons") or ()]


def sidebar_flyout_rows(button, implemented=None):
    """Return distinct actions in the category linked by ``button``."""
    name = getattr(button, "link", None)
    records = getattr(toolbar_data, "FLYOUTS", {}).get(name) if name else None
    if records is None:
        return []
    implemented = set(implemented or ())
    records = [dict(record) for record in records]
    for record in records:
        icon = flyout_icon_data.icon_for(name, record.get("text") or "")
        if icon:
            record["icon"] = icon
    if name == "Circle":
        presets = {
            "Circle: center, radius": (),
            "Circle: diameter": ("Radius/Diameter=Diameter",),
            "Circle: 3 points": ("3Point",),
            "Circle: tangent, tangent, radius": ("Tangent",),
            "Circle tangent to 3 curves": ("Tangent",),
        }
        routed = []
        for record in records:
            text = record.get("text") or ""
            if text in presets:
                record["preset"] = presets[text]
            else:
                record["available"] = False
                record["preset"] = ()
            routed.append(record)
        records = routed
    return [_button(record, implemented) for record in records]


def activation_lines(button, right=False):
    """Command-line tokens for one toolbar activation, preserving flyout presets."""
    if button is None:
        return ()
    command = button.right if right else button.left
    if not command:
        return ()
    return (command,) if right else (command, *button.preset)


def button_key(button):
    """Return the stable fields identifying a button across sidebar redraws."""
    if button is None:
        return None
    return (button.left, button.right, button.link, button.icon,
            button.text, button.tooltip, button.preset)


def sidebar_button_index(buttons, target, preferred=None):
    """Resolve a retained button after redraw rebuilt equivalent button objects."""
    wanted = button_key(target)
    if wanted is None:
        return None
    if preferred is not None:
        index = int(preferred)
        if 0 <= index < len(buttons or ()) and button_key(buttons[index]) == wanted:
            return index
    for index, button in enumerate(buttons or ()):
        if button_key(button) == wanted:
            return index
    return None


def sidebar_tooltip_lines(button, part="command", command_name=None):
    """HTMLv7 descriptions: primary, alternate action, then actual flyout link.

    ``part`` is retained for API compatibility; the whole cell shows full help.
    ``command_name`` is only a primary fallback, never an authored-text override.
    MEASURED: tmp/v7_app.js toolTip; command-only fallbacks are OURS.
    """
    if button is None:
        return ()
    lines = []
    primary = button.tooltip or button.text or command_name or button.left
    if primary:
        lines.append(str(primary) + ("" if button.enabled else " - not implemented yet"))
    alternate = button.right_tooltip or button.right
    if alternate:
        lines.append(f"right-click: {alternate}" +
                     ("" if button.right_enabled else " - not implemented yet"))
    if button.link:
        lines.append(f"◢ opens the {button.link} toolbar")
    return tuple(lines)


def hover_weight(elapsed, duration=.12):
    """Clamped background-transition progress; not a tooltip visibility delay.

    MEASURED: HTMLv7 uses background .12s ease. OURS: a small cubic ease-out
    approximation, not an exact CSS Bezier solver. Host UI animation glue stays
    in Python; interpolate from the current weight to 1 (enter) or 0 (leave).
    Nonpositive duration completes immediately, including at elapsed zero.
    """
    if duration <= 0:
        return 1.0
    t = min(1.0, max(0.0, float(elapsed) / duration))
    return 1.0 - (1.0 - t) ** 3


def tooltip_box_at(pointer, widths, linecount, windowsize, scale=1.0):
    """Return a pointer-anchored ``(x0, y0, x1, y1)`` box, or None without lines.

    Pointer/window coordinates and measured text widths are physical pixels,
    bottom-left origin (y increases UP). Pass the captured enter pointer, not
    each motion event. Only CSS metrics are scaled: +14px right/down, 8px/4px
    padding, 1px border, and 12px font at 1.3 line height (MEASURED HTMLv7).
    OURS: clamp the box to the window, clipping oversize content in the renderer.
    This is small host-UI coordinate glue, not a bulk layout/geometry operation.
    """
    if pointer is None or linecount <= 0:
        return None
    scale = max(0.0, float(scale))
    ww, wh = (max(0.0, float(size)) for size in windowsize)
    width = min(ww, max(0.0, max(widths, default=0.0)) + 18.0 * scale)
    height = min(wh, (15.6 * linecount + 10.0) * scale)
    x0 = min(max(0.0, pointer[0] + 14.0 * scale), ww - width)
    y0 = min(max(0.0, pointer[1] - 14.0 * scale - height), wh - height)
    return (x0, y0, x0 + width, y0 + height)


def sidebar_tooltip_layout(cell, text_width, window_width, window_height,
                           gap=None, pad=None, line_height=None):
    """A compact tooltip box beside ``cell``, clamped to the application window."""
    if cell is None:
        return None
    gap = SIDEBAR_TOOLTIP_GAP if gap is None else max(0.0, float(gap))
    pad = SIDEBAR_TOOLTIP_PAD if pad is None else max(0.0, float(pad))
    line_height = (SIDEBAR_TOOLTIP_LINE if line_height is None
                   else max(1.0, float(line_height)))
    width = max(1.0, float(text_width)) + pad * 2.0
    height = line_height + pad * 2.0
    ww, wh = max(0.0, float(window_width)), max(0.0, float(window_height))
    width, height = min(width, ww), min(height, wh)
    _index, cx0, cy0, cx1, cy1 = cell
    x0 = float(cx1) + gap
    if x0 + width > ww:
        x0 = max(0.0, float(cx0) - gap - width)
    center_y = (float(cy0) + float(cy1)) * 0.5
    y0 = min(max(0.0, center_y - height * 0.5), max(0.0, wh - height))
    return (x0, y0, x0 + width, y0 + height)


def flyout_icon_groups(buttons, columns=FLYOUT_COLUMNS):
    """Split flyout buttons into separator-preserving rows of icon cells.

    The return shape is ``groups -> rows -> buttons``. Empty separator runs are
    discarded, and every non-empty row contains at most ``columns`` buttons.
    """
    columns = max(1, int(columns))
    groups = []
    group = []

    def finish_group():
        if not group:
            return
        groups.append(tuple(tuple(group[i:i + columns])
                            for i in range(0, len(group), columns)))
        group.clear()

    for button in buttons or ():
        if button.is_separator:
            finish_group()
        else:
            group.append(button)
    finish_group()
    return tuple(groups)


def flyout_icon_buttons(buttons):
    """Return drawable flyout actions in order, omitting separator records."""
    return tuple(button for button in (buttons or ()) if not button.is_separator)


def flyout_popup_layout(anchor_x, anchor_y, buttons, window_width, window_height,
                        columns=None, cell=None):
    """Lay out the light floating icon toolbar in window coordinates."""
    items = flyout_icon_buttons(buttons)
    columns = FLYOUT_COLUMNS if columns is None else columns
    cell = FLYOUT_CELL if cell is None else cell
    columns = max(1, int(columns))
    cell = max(1.0, float(cell))
    rows = (len(items) + columns - 1) // columns if items else 0
    panel_w = FLYOUT_MARGIN * 2.0 + columns * cell + (columns - 1) * FLYOUT_GAP
    panel_h = FLYOUT_TITLE_H + FLYOUT_MARGIN * 2.0 + rows * cell + max(0, rows - 1) * FLYOUT_GAP
    max_x = max(0.0, float(window_width) - panel_w)
    max_top = max(panel_h, float(window_height))
    x0 = min(max(0.0, float(anchor_x) - FLYOUT_OVERLAP), max_x)
    top = min(max(panel_h, float(anchor_y)), max_top)
    panel = (x0, top - panel_h, x0 + panel_w, top)
    title = (x0, top - FLYOUT_TITLE_H, x0 + panel_w, top)
    grid_top = title[1] - FLYOUT_MARGIN
    cells = []
    for index in range(len(items)):
        column = index % columns
        row = index // columns
        cx0 = x0 + FLYOUT_MARGIN + column * (cell + FLYOUT_GAP)
        cy1 = grid_top - row * (cell + FLYOUT_GAP)
        cells.append((index, cx0, cy1 - cell, cx0 + cell, cy1))
    return {"panel": panel, "title": title, "cells": tuple(cells), "buttons": items}


def hit_flyout(x, y, cells):
    """Return a floating-toolbar icon index at a window point, or ``None``."""
    return hit_sidebar(x, y, cells)


def sidebar_columns(width, cell=None):
    """Return the number of fixed-size columns that fit, with a minimum of one."""
    cell = SIDEBAR_CELL if cell is None else float(cell)
    if cell <= 0:
        return 1
    return max(1, int(max(0.0, float(width)) // cell))


def sidebar_grid_x(width, columns, cell=None):
    """Return the left edge that centers the favorites grid."""
    cell = SIDEBAR_CELL if cell is None else float(cell)
    return max(0.0, (float(width) - max(1, int(columns)) * cell) * 0.5)


def sidebar_rows(n_buttons, columns=SIDEBAR_COLUMNS):
    """Return the number of rows needed for ``n_buttons``."""
    if n_buttons <= 0 or columns <= 0:
        return 0
    return (n_buttons + columns - 1) // columns


def sidebar_height(n_buttons, columns=SIDEBAR_COLUMNS, cell=None):
    """Return the favorites grid height in pixels."""
    cell = SIDEBAR_CELL if cell is None else cell
    return sidebar_rows(n_buttons, columns) * cell


def sidebar_cells(n_buttons, x0, y_top, columns=SIDEBAR_COLUMNS, cell=None):
    """Return indexed boxes filled left-to-right and then downward."""
    cell = SIDEBAR_CELL if cell is None else cell
    out = []
    for index in range(max(0, int(n_buttons))):
        column = index % columns
        row = index // columns
        cell_x = x0 + column * cell
        cell_y = y_top - (row + 1) * cell
        out.append((index, cell_x, cell_y, cell_x + cell, cell_y + cell))
    return out


def sidebar_flyout_box(cell, pad=None, mark=None, hot=None):
    """Return the bottom-right hit box for a linked-category marker."""
    if cell is None:
        return None
    pad = SIDEBAR_PAD if pad is None else max(0.0, float(pad))
    mark = SIDEBAR_MARK if mark is None else max(0.0, float(mark))
    hot = SIDEBAR_FLYOUT_HOT if hot is None else max(0.0, float(hot))
    _index, x0, y0, x1, y1 = cell
    size = min(max(mark + pad * 2.0, hot), x1 - x0, y1 - y0)
    return (x1 - size, y0, x1, y0 + size)


def sidebar_action_at(x, y, cells, buttons, pad=None, mark=None, hot=None):
    """Return ``(button_index, 'flyout'|'command')`` at a point."""
    index = hit_sidebar(x, y, cells)
    if index is None or not (0 <= index < len(buttons or ())):
        return None
    cell = next((candidate for candidate in cells if candidate[0] == index), None)
    button = buttons[index]
    if cell is not None and getattr(button, "link", None):
        box = sidebar_flyout_box(cell, pad=pad, mark=mark, hot=hot)
        if box is not None and box[0] <= x < box[2] and box[1] <= y < box[3]:
            return (index, "flyout")
    return (index, "command")


def hit_sidebar(x, y, cells):
    """Return the index of the half-open favorites cell at a point."""
    for index, x0, y0, x1, y1 in cells or ():
        if x0 <= x < x1 and y0 <= y < y1:
            return index
    return None


# ---------------------------------------------------------------------------
# Category strip and contextual left toolbar (UI design, light slate concept).
#
# The top strip holds one compact button per category (icon over label) -- it selects
# a category, it is NOT a ribbon of commands. The left panel then shows that
# category's tools. Standard keeps the favourite drawing palette, which is what the
# design reference shows under "Standard".
# ---------------------------------------------------------------------------

#: One representative bundled icon per category.
CATEGORY_ICONS = {
    "Standard": "narwhal-box.png",
    "CPlanes": "narwhal-cplane.png",
    "Set View": "narwhal-rotateview.png",
    "Display": "narwhal-viewport_shaded.png",
    "Select": "narwhal-selall.png",
    "Viewport Layout": "narwhal-4view.png",
    "Visibility": "narwhal-hide.png",
    "Transform": "narwhal-move.png",
    "Curve Tools": "narwhal-fillet.png",
    "Surface Tools": "narwhal-loft.png",
    "Solid Tools": "narwhal-booleanunion.png",
    "SubD Tools": "narwhal-removecrease.png",
    "Mesh Tools": "narwhal-meshrepair.png",
    "Render": "narwhal-render.png",
    "Drafting": "narwhal-dim.png",
}
CATEGORY_ICON = 26.0        # icon inside a category button
CATEGORY_LABEL_FONT = 11.0
CONTEXT_COLUMNS = 3
CONTEXT_HEADER_H = 30.0     # "Standard" header strip above the tools
CONTEXT_MIN_CELL = 26.0


def category_icon(name):
    """The bundled icon for a category, or None."""
    return CATEGORY_ICONS.get(name)


def context_buttons(category, implemented=None):
    """The tools the left panel shows for `category` (separators removed)."""
    if category == "Standard":
        return sidebar_buttons(implemented)
    return [button for button in tab_rows(category, implemented) if not button.is_separator]


def category_boxes(names, width, button_w, gap=2.0, x0=0.0):
    """Fixed-width ``(name, x0, x1)`` category buttons left to right; buttons that do
    not fit are omitted (see :func:`tabs_hidden`)."""
    out = []
    x = float(x0)
    for name in names or ():
        if x + button_w > width:
            break
        out.append((name, x, x + button_w))
        x += button_w + gap
    return out


def context_grid(n_buttons, width, height, cell, columns=CONTEXT_COLUMNS,
                 min_cell=CONTEXT_MIN_CELL):
    """``(cell, shown)`` for the contextual grid: the preferred `cell` when all
    `n_buttons` fit in `height`, else a smaller cell down to `min_cell`; `shown` is how
    many buttons are drawn (the rest are reported, never silently dropped)."""
    columns = max(1, int(columns))
    cell = min(float(cell), max(1.0, float(width)) / columns)
    rows = -(-max(0, int(n_buttons)) // columns)
    if rows and rows * cell > height:
        cell = max(float(min_cell), float(height) / rows)
    fit_rows = int(max(0.0, float(height)) // cell) if cell > 0 else 0
    return cell, min(int(n_buttons), fit_rows * columns)


# OURS: opt-in v4 UI coordinate glue, kept in Python beside the host's draw/hit
# adapters. These small box layouts neither compute nor approximate CAD geometry.
def bounded_scroll_offset(offset, content_extent, viewport_extent):
    """Return ``(offset, max_offset)`` in pixels, clamping stale resize/filter state."""
    maximum = max(0.0, float(content_extent) - max(0.0, float(viewport_extent)))
    return min(maximum, max(0.0, float(offset))), maximum


def scroll_cells(viewport, cells):
    """Record visible indexed draw boxes and their half-open hit intersections.

    ``viewport`` is ``(x0, y0, x1, y1)`` in bottom-left-origin coordinates.
    ``cells`` entries are ``(original_index, x0, y0, x1, y1)``. Draw using the
    returned full-size ``cells`` under a scissor of ``viewport``; never anchor an
    icon to its clipped ``hit_cells`` box. Use :func:`hit_scroll` for picking.
    Degenerate/reversed viewport dimensions produce no visible or hittable cells.
    """
    vx0, vy0, vx1, vy1 = viewport
    visible, hits = [], []
    for index, x0, y0, x1, y1 in cells:
        clipped = (index, max(vx0, x0), max(vy0, y0), min(vx1, x1), min(vy1, y1))
        if clipped[1] < clipped[3] and clipped[2] < clipped[4]:
            visible.append((index, x0, y0, x1, y1))
            hits.append(clipped)
    return {"viewport": tuple(viewport), "cells": tuple(visible), "hit_cells": tuple(hits)}


def hit_scroll(x, y, layout):
    """Original item index under the point, using the layout's recorded hit boxes."""
    return hit_sidebar(x, y, layout["hit_cells"])


def category_scroll_layout(names, viewport, offset=0.0, *, button_w=80.0,
                           gap=3.0, pad=7.0):
    """Opt-in horizontal category strip; fixed-width buttons, padding on all sides.

    In addition to :func:`scroll_cells`' keys, returns bounded ``offset``,
    ``max_offset``, ``content_extent`` (width including end padding), and ``total``.
    Indices address the original ``names`` sequence, even with duplicate labels.
    Positive offset reveals later categories. All lengths use the caller's pixel
    space; pass scaled metrics when the viewport is in physical pixels.
    """
    if button_w <= 0 or gap < 0 or pad < 0:
        raise ValueError("category width must be positive; gap/padding nonnegative")
    names = tuple(names or ())
    x0, y0, x1, y1 = viewport
    extent = 2 * pad + len(names) * button_w + max(0, len(names) - 1) * gap if names else 0.0
    offset, maximum = bounded_scroll_offset(offset, extent, x1 - x0)
    cells = ((i, x0 + pad + i * (button_w + gap) - offset, y0 + pad,
              x0 + pad + i * (button_w + gap) - offset + button_w, y1 - pad)
             for i in range(len(names)))
    out = scroll_cells(viewport, cells)
    out.update(offset=offset, max_offset=maximum, content_extent=extent, total=len(names))
    return out


def context_scroll_layout(n_buttons, viewport, offset=0.0, *, cell=35.0,
                          columns=3, gap=3.0, pad=4.0, x_offset=0.0):
    """Opt-in fixed-cell context grid, three columns, scrolling top-to-bottom.

    ``offset``/``max_offset`` refer to vertical pixels; ``content_extent`` is the
    padded content height. ``content_width`` is the required width (119 by default).
    For narrower viewports the host can use ``x_offset``/``max_x_offset`` to reach
    every column without shrinking cells. Both offsets are bounded. Also returns
    ``total`` and :func:`scroll_cells`' draw/hit boxes. Exclude the context header
    from ``viewport``; pass scaled cell/gap/pad explicitly for physical pixels.
    """
    if cell <= 0 or columns < 1 or int(columns) != columns or gap < 0 or pad < 0:
        raise ValueError("positive cell/integer columns and nonnegative gap/padding required")
    columns = int(columns)
    count = max(0, int(n_buttons))
    rows = (count + columns - 1) // columns
    extent = 2 * pad + rows * cell + max(0, rows - 1) * gap if count else 0.0
    width = 2 * pad + columns * cell + (columns - 1) * gap if count else 0.0
    x0, y0, x1, y1 = viewport
    offset, maximum = bounded_scroll_offset(offset, extent, y1 - y0)
    x_offset, max_x = bounded_scroll_offset(x_offset, width, x1 - x0)
    cells = []
    for i in range(count):
        left = x0 + pad + (i % columns) * (cell + gap) - x_offset
        top = y1 - pad - (i // columns) * (cell + gap) + offset
        cells.append((i, left, top - cell, left + cell, top))
    out = scroll_cells(viewport, cells)
    out.update(offset=offset, max_offset=maximum, content_extent=extent, total=count,
               x_offset=x_offset, max_x_offset=max_x, content_width=width)
    return out


def scroll_indicator(layout, width=6.0, inset=2.0, minimum=24.0):
    """Track/thumb boxes for a vertical overflowing tool viewport, in its coordinates."""
    if not layout or layout.get('max_offset', 0) <= 0:
        return None
    x0, y0, x1, y1 = layout['viewport']
    track = (x1 - inset - width, y0 + inset, x1 - inset, y1 - inset)
    height = max(0.0, track[3] - track[1])
    if height <= 0:
        return None
    thumb_h = min(height, max(minimum, height * max(0, y1-y0) / layout['content_extent']))
    travel = height - thumb_h
    offset = min(layout['max_offset'], max(0.0, layout['offset']))
    top = track[3] - travel * offset / layout['max_offset']
    return {'track': track, 'thumb': (track[0], top-thumb_h, track[2], top), 'travel': travel}


def context_scroll_action_at(x, y, layout, buttons, pad=None, mark=None, hot=None):
    """Clipped picking with flyout corners anchored to the FULL fixed-size cells."""
    if hit_scroll(x, y, layout) is None:
        return None
    return sidebar_action_at(x, y, layout["cells"], buttons, pad=pad, mark=mark, hot=hot)
