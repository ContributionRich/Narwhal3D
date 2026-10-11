"""Narwhal 3D's temporary toolbar model.

The module is intentionally pure: it exposes toolbar buttons and layout geometry
without importing the host UI. Content and bundled icon names live in
:mod:`toolbar_data`; renderers consume the stable :class:`Button` API below.
"""
from __future__ import annotations

import os

from . import toolbar_data

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


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
                 "link", "enabled", "right_enabled")

    def __init__(self, text, tooltip, left, right, right_tooltip, icon,
                 enabled, right_enabled, link=None):
        self.text = text
        self.tooltip = tooltip
        self.left = left
        self.right = right
        self.right_tooltip = right_tooltip
        self.icon = icon
        self.link = link or None
        self.enabled = bool(enabled)
        self.right_enabled = bool(right_enabled)

    @property
    def label(self):
        """Return non-empty fallback text for renderers without an icon."""
        return self.text or self.tooltip or (self.left or "?")

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
        bool(left and left in implemented),
        bool(right and right in implemented),
        record.get("link"),
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

    Resolution is restricted to the explicit toolbar manifest and to real files
    whose resolved location remains inside :data:`ICON_DIR`. Missing files return
    ``None`` so a renderer can safely use button text instead.
    """
    if not isinstance(icon, str) or icon not in toolbar_data.ICON_NAMES:
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
    """Return actions in the category linked by ``button``."""
    name = getattr(button, "link", None)
    records = getattr(toolbar_data, "FLYOUTS", {}).get(name) if name else None
    if records is None:
        return []
    implemented = set(implemented or ())
    return [_button(record, implemented) for record in records]


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
