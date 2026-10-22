"""Narwhal 3D's complete authored menu tree.

This module retains the dictionary-shaped menu API used by older callers. The
renderer-facing rows live in :mod:`menubar_resource_data`; both views describe the
same original menu design, including explicit unavailable roadmap actions.
"""
from __future__ import annotations

from . import menubar_resource_data as _rows


def _plain(label):
    """Return a label without optional mnemonic markers."""
    out = []
    i = 0
    while i < len(label or ""):
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


def _tree(rows):
    tree = {}
    for row in rows:
        kind, label = row[0], row[1]
        if kind == "item":
            tree[_plain(label)] = (row[3], {})
        elif kind == "popup":
            tree[_plain(label)] = (row[4] if len(row) > 4 else None,
                                    _tree(row[3]))
    return tree


MENU_BAR = tuple(_plain(label) for label, _menu_rows in _rows.MENUS)
MENUS = {_plain(label): _tree(menu_rows) for label, menu_rows in _rows.MENUS}
REPORT = {
    "menus": len(MENU_BAR),
    "actions": _rows.REPORT["items"],
    "outside_bar": {},
    "source": "original",
}
