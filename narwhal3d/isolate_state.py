"""Track which objects `Isolate` hid so `Unisolate` can restore that exact set.

=============================================================================
Why this module exists
=============================================================================

`Isolate` (commands/hide.htm) hides every object that is neither selected nor
locked. `Unisolate` shows "all objects hidden by the Isolate command."

The document alone cannot tell `Unisolate` which hidden objects belong to
`Isolate` and which were hidden by a separate `Hide`: both carry
`ObjectMode.Hidden`. The set of names `Isolate` hid is therefore remembered
here, keyed by the model's identity (a `WeakKeyDictionary` so no model is
leaked when it is destroyed).

The module is pure and has no `bpy` or rhino3dm dependency.

Design notes
------------
* Multiple Isolates accumulate: each call to :func:`remember` **unions** the
  names into the remembered set, so a later `Unisolate` restores everything
  that any `Isolate` hid. This is the safe choice — never losing the record of
  a hide.
* :func:`pop` is destructive (it clears the set) and is what `Unisolate`
  calls. If there is no record, it returns ``None`` and the command refuses
  explicitly.
"""
from __future__ import annotations

import weakref
from typing import Any

# model identity -> frozenset of object names that Isolate hid.
# WeakKeyDictionary: the key is the model object itself, and the entry is
# dropped automatically when the model is garbage-collected.
_state: "weakref.WeakKeyDictionary[Any, frozenset[str]]" = weakref.WeakKeyDictionary()


def remember(model: Any, names: set[str] | frozenset[str]) -> None:
    """Record that `Isolate` hid `names` in this `model`.

    Unions with any existing record, so successive Isolates accumulate.
    """
    current = _state.get(model, frozenset())
    _state[model] = frozenset(current | names)


def pop(model: Any) -> frozenset[str] | None:
    """Return and clear the remembered set. ``None`` if Isolate never ran."""
    names = _state.get(model)
    if names is None:
        return None
    del _state[model]
    return names


def peek(model: Any) -> frozenset[str]:
    """Return the current remembered set without clearing it (empty if none)."""
    return _state.get(model, frozenset())


def clear_all() -> None:
    """Drop every record (test isolation)."""
    _state.clear()
