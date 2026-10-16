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
SIDEBAR_TOOLTIP_PAD = 5.0
SIDEBAR_TOOLTIP_GAP = 4.0
SIDEBAR_TOOLTIP_LINE = 14.0
SIDEBAR_TOOLTIP_FONT = 11.0
FLYOUT_COLUMNS = 5
FLYOUT_CELL = 40.0
FLYOUT_MARGIN = 5.0
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
            record = dict(record)
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
    """Return only the hovered icon's command name.

    Alternate-click and linked-toolbar details belong to menus, not the transient hover
    card. Keeping this to one short line lets the card sit beside the icon instead of
    occupying the dock's blank area. ``part`` remains accepted for caller compatibility.
    """
    if button is None:
        return ()
    name = command_name or button.left or button.text or button.tooltip
    return (str(name),) if name else ()


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
    panel_w = FLYOUT_MARGIN * 2.0 + columns * cell
    panel_h = FLYOUT_TITLE_H + FLYOUT_MARGIN * 2.0 + rows * cell
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
        cx0 = x0 + FLYOUT_MARGIN + column * cell
        cy1 = grid_top - row * cell
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
