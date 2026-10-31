"""Undo / redo: the document history. **No `bpy`, no rhino3dm.**

Rust history ABI1 is required. Python retains opaque host snapshot references
and callback orchestration only. Missing or incompatible native support refuses
construction; neither admission nor runtime failures select a Python timeline.

Why this exists
---------------
Direct Gumball manipulation requires a way to take a drag back. `Esc` mid-drag could not revert it either, because there was no
document history to revert *to*. Every drag was permanent. That is the gap this
closes.

How the reference CAD application models undo (MEASURED, not invented)
----------------------------------------------
From `CAD SDK.xml` and `commands/undo.htm`:

* Changes are grouped into **undo records**, and a record has a **name** and a
  **serial number**: `document API.BeginUndoRecord(System.String)` takes a
  description, and `NextUndoRecordSerialNumber` / `CurrentUndoRecordSerialNumber`
  expose the numbering. `EndUndoRecord(System.UInt32)` closes one by serial.
* `CurrentUndoRecordSerialNumber` is documented as ">0: undo recording is active
  and being saved on the undo record with the specified serial number. 0: ...",
  so **zero means "no record open"** -- hence :data:`NO_RECORD`.
* `document API.Undo()` "Undo the last action"; `document API.Redo()` "Redo the last
  action that was 'undone'".
* `UndoRecordingEnabled` exists, so recording can be switched off.
* `UndoMultiple` "displays a list of recent commands to reverse", so records are
  **named and user-visible** -- which is why :meth:`UndoStack.undo_names` exists
  even though no UI shows them yet.
* Redo is "available immediately after you use Undo" (`commands/undo.htm`), and
  the standard corollary: a fresh change invalidates the redo chain.

**The limit is memory, not steps.** `options/general.htm` gives exactly one undo
setting: "Max memory used (MB) -- The maximum amount of memory the reference CAD application will attempt
to use." So the reference CAD application does *not* have a fixed "N levels of undo". We bound by a
record COUNT instead, because a Python-side byte count of live rhino3dm geometry
is not something we can measure honestly. :data:`DEFAULT_LIMIT` is therefore an application-defined stand-in.

What a snapshot is
------------------
This module never looks inside one. A "snapshot" is whatever opaque object the
caller's `snapshot()` produced -- for the real document that is
`model.NarwhalModel.snapshot()`. Keeping it opaque is what lets the whole history
machine be tested with plain strings.
"""
from __future__ import annotations

#: the reference CAD application's "no undo record is open" sentinel: `CurrentUndoRecordSerialNumber`
#: is documented as ">0: ... active ... 0: [not]". MEASURED.
NO_RECORD = 0

#: How many records we keep. the reference CAD application bounds undo by memory ("Max
#: memory used (MB)", options/general.htm), which we cannot measure honestly from
#: Python, so a count is our stand-in. Not a the reference CAD application number; do not present it as
#: one.
DEFAULT_LIMIT = 100


def _native_backend(limit):
    import importlib.util
    from pathlib import Path
    import sys
    name = 'narwhal3d.native_history'
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name('native_history.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module.try_create(limit)


class UndoStack:
    """Stable host interface; Rust alone owns the timeline."""
    def __init__(self, limit=DEFAULT_LIMIT):
        limit = max(1, int(limit))
        self._backend = _native_backend(limit)

    def __getattr__(self, name):
        return getattr(self._backend, name)

    @property
    def enabled(self):
        return self._backend.enabled

    @enabled.setter
    def enabled(self, value):
        self._backend.enabled = value

    def begin(self, name):
        return self._backend.begin(name)

    def capture(self, snapshot):
        return self._backend.capture(snapshot)

    def end(self):
        return self._backend.end()

    def abandon(self):
        return self._backend.abandon()

    def undo(self, current, *, restore=None, failure_state=None):
        return self._backend.undo(current, restore=restore, failure_state=failure_state)

    def redo(self, current, *, restore=None, failure_state=None):
        return self._backend.redo(current, restore=restore, failure_state=failure_state)

    def clear(self):
        return self._backend.clear()

    def __repr__(self):
        return repr(self._backend)
