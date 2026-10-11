"""the reference CAD application's `SelAll` / `SelNone` / `Invert` selection commands -- the pure LOGIC.

It has no `bpy` or rhino3dm dependency, so the logic is independent of a viewport.

`select.py` decides what a CLICK or a DRAG does to a selection. These three
commands are the document-level operations on top of that: they act on the WHOLE
set of objects, not on what the cursor happened to hit. `commands.py` wires each
to the live model; this module only decides what the
new selection IS, given the document's object names and the current selection.

The documented behavior from `commands/selection_commands.htm` is:

    SelAll  "The SelAll command selects all objects."
    SelNone "The SelNone command deselects all objects."
            "The SelNone command does not run inside a command to clear
             pre-selected objects."
    Invert  "The Invert command deselects all selected objects and selects all
             visible objects that were not previously selected."

None of the three has documented command-line options (checked with the fixed
help tool). `Invert` is the one to read carefully: it operates on VISIBLE
objects, so the caller must hand it the visible names -- hidden ones are not
candidates to be turned on. That filtering is the caller's job, not this module's,
which keeps it pure and testable.
"""
from __future__ import annotations


def _names(names):
    """A stable, de-duplicated list of names, in first-seen order.

    Same shape `select.py` returns, so a caller can pass one function's output
    straight into the next and tests can compare exactly.
    """
    return list(dict.fromkeys(names or ()))


def sel_all(all_names, selected=None):
    """`SelAll`: every object in the document, in document order.

    "selects all objects." The current selection is irrelevant -- the spec says
    nothing about what was already selected, so it is accepted (for a uniform
    call shape) and ignored.
    """
    return _names(all_names)


def sel_none(all_names=None, selected=None):
    """`SelNone`: an empty selection.

    "deselects all objects." It also "does not run inside a command to clear
    pre-selected objects" -- that is a command-prompt restriction enforced by the
    caller, not a change to this result: when the command runs, the selection is empty.
    """
    return []


def invert(all_names, selected=None):
    """`Invert`: every object NOT currently selected, in document order.

    "deselects all selected objects and selects all VISIBLE objects that were
    not previously selected." So `all_names` must be the document's VISIBLE
    names -- the caller filters hidden objects before calling. A name in
    `selected` that is not in `all_names` (e.g. a since-deleted object) is simply
    not re-selected, which matches "everything not currently selected" taken
    over the document.
    """
    selected = set(selected or ())
    return [n for n in _names(all_names) if n not in selected]


#: command name (lowercase, as used as a key in `commands.COMMANDS`) -> the pure
#: function that decides the new selection from (document names, current
#: selection). The command layer wires each function to the live model.
COMMANDS = {
    "selall": sel_all,
    "selnone": sel_none,
    "invert": invert,
}
