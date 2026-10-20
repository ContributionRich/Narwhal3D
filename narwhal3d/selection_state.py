"""Track the state that determines what `SelLast` and `SelPrev` select.

=============================================================================
Why this module exists
=============================================================================

`SelLast` (MEASURED, `selection_commands.htm`, "Select Objects > Last Created
Objects"):

    "The SelLast command selects the last changed objects, including
     importing objects."

`SelPrev` (MEASURED, same page, "Select Objects > Previous Selection"):

    "The SelPrev command re-selects the previous selection set of objects
     or sub-objects."

Both act on STATE the document does not carry:

* `SelLast` needs the set of objects the app CREATED (the "last created
  objects"). The document only shows the current objects; it does not record
  which ones were just added by a drawing command, so that set is recorded
  here as objects are created (`record_created`, called from `add_geometry`).
* `SelPrev` needs the selection set that was active BEFORE the current one.
  A selection is not in the document at all (it is a view of it), so the
  previous set is recorded here as the selection changes (`selection_changed`,
  called from `select_by_names`).

The module is pure and has no `bpy` or rhino3dm dependency.

Design notes
------------
* State is keyed by the MODEL's identity (a `WeakKeyDictionary`, as in
  `isolate_state`), so no model is leaked when it is destroyed and each model
  has its own independent history.
* `record_created` appends (deduplicated, in creation order). `SelLast` selects
  the full tracked set: the objects this session created. Imported-via-Open
  objects are not recorded here, so `SelLast` currently covers application-created
  objects rather than every kind of changed object.
* `selection_changed` remembers the OLD current selection as the new
  "previous" (possibly empty). `SelPrev` re-selects that set. With no prior
  selection, `previous` is ``None`` and the command reports that no prior selection exists.
"""
from __future__ import annotations

import weakref
from typing import Any

# model identity -> {"created": [names...], "current": [names...], "previous": [names...] | None}
_state: "weakref.WeakKeyDictionary[Any, dict]" = weakref.WeakKeyDictionary()


def _entry(model: Any) -> dict:
    return _state.setdefault(model, {
        "created": [],
        "current": [],
        "previous": None,
    })


def record_created(model: Any, name: str) -> None:
    """Record that `name` was created (a "last created object" for `SelLast`).

    Called from `add_geometry`. Deduplicated so a re-creation of the same name
    does not double-count.
    """
    e = _entry(model)
    if name not in e["created"]:
        e["created"].append(name)


def last_created(model: Any) -> list:
    """The names created via `add_geometry` (in creation order); ``[]`` if none."""
    return list(_state.get(model, {}).get("created", []))


def selection_changed(model: Any, new_names) -> None:
    """A selection was set to `new_names`.

    The OLD current selection becomes the new "previous" (possibly empty).
    Called from `select_by_names` -- the single choke point for selection
    changes, so every change is captured in one place.
    """
    e = _entry(model)
    e["previous"] = list(e["current"])
    e["current"] = list(new_names)


def previous_selection(model: Any) -> list | None:
    """The selection set before the most recent one (``SelPrev``'s target).

    ``None`` if there has never been a selection (the command refuses by name);
    a list (possibly empty) otherwise.
    """
    e = _state.get(model)
    if e is None:
        return None
    return e.get("previous")


def clear_all() -> None:
    """Drop every record (test isolation)."""
    _state.clear()
