"""Pure menu models for Narwhal 3D.

The compact product menu is available in two compatible forms: a dictionary tree for
legacy callers and ordered renderer rows for the application shell.  All shipped leaf
rows identify runnable commands; this module never guesses an action from its label.
"""
from __future__ import annotations

from . import menu_data
from . import menubar_resource_data as resource_data


def menu_names():
    """Return top-level menu names from left to right."""
    return tuple(menu_data.MENU_BAR)


def _node_items(node):
    return node[1] if isinstance(node, tuple) else {}


def _node_command(node):
    return node[0] if isinstance(node, tuple) else None


def submenu(path):
    """Return the dictionary of items at ``path``, or ``None`` when unknown."""
    if not path:
        return None
    items = menu_data.MENUS.get(path[0])
    if items is None:
        return None
    for label in path[1:]:
        node = items.get(label)
        if node is None:
            return None
        items = _node_items(node)
    return items


class Row:
    """One row from the dictionary-shaped menu model."""

    __slots__ = ("label", "command", "children", "enabled", "path")

    def __init__(self, label, command, children, enabled, path):
        self.label = label
        self.command = command
        self.children = bool(children)
        self.enabled = bool(enabled)
        self.path = tuple(path)

    def __repr__(self):  # pragma: no cover - debugging aid
        mark = "" if self.enabled else " (disabled)"
        arrow = " >" if self.children else ""
        return f"Row({self.label!r}{arrow}{mark})"


def menu_rows(path, implemented):
    """Return legacy rows at ``path`` in the authored menu order."""
    items = submenu(path)
    if not items:
        return []
    rows = []
    for label, node in items.items():
        command = _node_command(node)
        children = _node_items(node)
        enabled = bool(command and command.lower() in implemented)
        if children and not enabled:
            enabled = _subtree_has_enabled(children, implemented)
        rows.append(Row(label, command, bool(children), enabled,
                        tuple(path) + (label,)))
    return rows


def _subtree_has_enabled(items, implemented):
    for node in items.values():
        command = _node_command(node)
        if command and command.lower() in implemented:
            return True
        if _subtree_has_enabled(_node_items(node), implemented):
            return True
    return False


def coverage(implemented):
    """Return ``(enabled, total)`` for command-bearing menu rows."""
    total = 0
    enabled = 0

    def walk(items):
        nonlocal total, enabled
        for node in items.values():
            command = _node_command(node)
            if command:
                total += 1
                enabled += int(command.lower() in implemented)
            walk(_node_items(node))

    for menu in menu_names():
        walk(menu_data.MENUS.get(menu) or {})
    return enabled, total


def not_on_the_bar():
    """Compatibility API: the authored tree has no discarded top-level groups."""
    outside = menu_data.REPORT.get("outside_bar")
    return dict(outside) if isinstance(outside, dict) else {}


def plain_label(label):
    """Remove optional mnemonic markers while preserving a doubled ampersand."""
    if not label:
        return ""
    out = []
    i = 0
    while i < len(label):
        if label[i] == "&":
            if i + 1 < len(label) and label[i + 1] == "&":
                out.append("&")
                i += 2
                continue
            i += 1
            continue
        out.append(label[i])
        i += 1
    return "".join(out)


def resource_menu_names():
    """Return renderer menu names in authored order."""
    return tuple(plain_label(label) for label, _rows in resource_data.MENUS)


def resource_menu_labels_verbatim():
    """Return stored top-level labels; retained for shell compatibility."""
    return tuple(label for label, _rows in resource_data.MENUS)


def join_key(label):
    """Return a punctuation-free, case-folded key for a label or option."""
    text = plain_label(label or "").replace("...", "")
    return "".join(ch for ch in text if ch.isalnum()).lower()


def resource_rows(menu_label, path=()):
    """Return renderer rows at ``path``, or an empty list when unknown."""
    wanted = plain_label(menu_label).casefold()
    rows = None
    for label, payload in resource_data.MENUS:
        if plain_label(label).casefold() == wanted:
            rows = payload
            break
    if rows is None:
        return []
    for step in path:
        step_wanted = plain_label(step).casefold()
        found = None
        for row in rows:
            if row[0] == "popup" and plain_label(row[1]).casefold() == step_wanted:
                found = row[3]
                break
        if found is None:
            return []
        rows = found
    return list(rows)


def popup_command(row):
    """Return a popup's optional direct command."""
    if not row or row[0] != "popup":
        return None
    return row[4] if len(row) > 4 else None


def submenu_actions(rows, parent_command, option_names):
    """Resolve each row to ``(row, command, option)`` without label guessing.

    Authored item payloads win.  The parent/option fallback remains for API
    compatibility with callers that construct variant rows dynamically.
    """
    option_by_key = {join_key(name): name for name in (option_names or ())}
    out = []
    default_taken = False
    for row in rows or ():
        if row[0] != "item":
            out.append((row, None, None))
            continue
        own_command = row[3]
        if own_command:
            out.append((row, own_command, None))
            continue
        if not parent_command:
            out.append((row, None, None))
            continue
        option = option_by_key.get(join_key(row[1]))
        if option is not None:
            out.append((row, parent_command, option))
        elif not default_taken:
            default_taken = True
            out.append((row, parent_command, None))
        else:
            out.append((row, None, None))
    return out


def collapse_separators(rows):
    """Remove leading, trailing, and repeated separators."""
    out = []
    for row in rows or ():
        if row[0] == "sep":
            if not out or out[-1][0] == "sep":
                continue
            out.append(row)
        else:
            out.append(row)
    while out and out[-1][0] == "sep":
        out.pop()
    return tuple(out)
