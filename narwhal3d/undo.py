"""Undo / redo: the document history. **No `bpy`, no rhino3dm.**

Rust owns the persistent history when application-core provides history ABI1.
Python retains opaque host snapshot references and callback orchestration. The
complete legacy implementation remains available at construction when native
history is absent; a live native history never falls back after an error.

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


class Record:
    """One undoable step: the document before it, and (once undone) after it.

    `before` is the snapshot taken just before the change; `after` is filled in
    when the record is undone, so it can be redone. `name` is the command that
    caused it, which is what `UndoMultiple` lists.
    """

    __slots__ = ("serial", "name", "before", "after", "_before_state", "_after_state")

    def __init__(self, serial, name, before, after=None):
        self.serial = serial
        self.name = name
        self.before = before
        self.after = after
        self._before_state = 0
        self._after_state = 0

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Record(#{self.serial}, {self.name!r})"


class LegacyUndoStack:
    """The document's undo and redo history.

    Recording is **lazy and grouped**. A command opens a record with
    :meth:`begin`, and the snapshot is only taken when something actually calls
    :meth:`capture` -- so a command that changes nothing (a cancelled `Line`, a
    `Zoom`) leaves no record behind, which is what the reference CAD application does. Nested
    :meth:`begin` calls join the record already open rather than starting a new
    one, so one user action stays one undo step however many layers of code it
    passes through.
    """

    def __init__(self, limit=DEFAULT_LIMIT):
        self._undo: list[Record] = []
        self._redo: list[Record] = []
        self._limit = max(1, int(limit))
        self._next_serial = 1
        # the record currently being recorded into, and how deeply nested it is
        self._open: Record | None = None
        self._depth = 0
        self._enabled = True
        self._state_id = 0
        self._next_state_id = 1
        self._generation = 0
        self._navigating = False
        self.uncertain = False

    backend = 'legacy'

    @property
    def state_id(self):
        return self._state_id

    @property
    def generation(self):
        return self._generation

    def _ready(self, *, clearing=False):
        if self._navigating:
            raise RuntimeError('history_busy')
        if self.uncertain and not clearing:
            raise RuntimeError('history_state_unknown')

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        self._ready()
        self._enabled = bool(value)
        self._generation += 1

    # -- recording ---------------------------------------------------------

    @property
    def recording(self):
        """True while a record is open (the reference CAD application's `UndoRecordingIsActive`)."""
        return self._open is not None

    @property
    def current_serial(self):
        """Serial of the open record, or :data:`NO_RECORD`.

        Mirrors the reference CAD application's `CurrentUndoRecordSerialNumber`, including that 0 means
        nothing is open.
        """
        return self._open.serial if self._open is not None else NO_RECORD

    @property
    def top_serial(self):
        """Serial of the newest undoable record, or :data:`NO_RECORD`.

        This identifies the document's state: two moments with the same top record
        hold the same document. The file workflow compares it with the value taken
        at the last save to answer "has the model changed?" -- so undoing back to
        the saved state correctly reads as unmodified again.
        """
        return self._undo[-1].serial if self._undo else NO_RECORD

    @property
    def next_serial(self):
        """Serial the next record will get (the reference CAD application's `NextUndoRecordSerialNumber`)."""
        return self._next_serial

    def begin(self, name):
        """Open an undo record named `name`; returns its serial number.

        Returns :data:`NO_RECORD` when recording is disabled. Nesting joins the
        open record and returns its serial, so callers can bracket freely without
        splitting one action into several undo steps.
        """
        self._ready()
        if not self.enabled:
            return NO_RECORD
        self._generation += 1
        if self._open is not None:
            self._depth += 1
            return self._open.serial
        self._open = Record(self._next_serial, str(name or "Change"), None)
        self._open._before_state = self._state_id
        self._next_serial += 1
        self._depth = 1
        return self._open.serial

    def capture(self, snapshot):
        """Record the document state from *before* the change, once per record.

        Called at the moment of the first actual mutation, so a command that
        touches nothing never stores a snapshot. Later calls within the same
        record are ignored -- the first one is the "before" state by definition.
        Returns True if this call is the one that stored it.
        """
        self._ready()
        if self._open is None or self._open.before is not None:
            return False
        self._open.before = snapshot
        self._generation += 1
        return True

    @property
    def captured(self):
        """True if the open record has something to undo."""
        return self._open is not None and self._open.before is not None

    def end(self):
        """Close one level of the open record.

        The record is pushed onto the undo stack only when the outermost level
        closes AND something was captured. Pushing a real change clears the redo
        chain, which is the documented behaviour ("Redo is available immediately
        after you use Undo"). Returns True if a record was pushed.
        """
        self._ready()
        if self._open is None:
            return False
        self._generation += 1
        self._depth -= 1
        if self._depth > 0:
            return False
        record, self._open, self._depth = self._open, None, 0
        if record.before is None:
            return False                  # nothing happened -- no undo step
        self._undo.append(record)
        record._after_state = self._next_state_id
        self._next_state_id += 1
        self._state_id = record._after_state
        if len(self._undo) > self._limit:
            del self._undo[:len(self._undo) - self._limit]
        self._redo.clear()
        return True

    def abandon(self):
        """Throw the open record away without pushing it.

        For a command that failed partway: the caller has already restored the
        document (or nothing was changed), so there is nothing to undo.
        """
        self._ready()
        self._open = None
        self._depth = 0
        self._generation += 1

    # -- navigation --------------------------------------------------------

    @property
    def can_undo(self):
        return bool(self._undo) and not self.uncertain

    @property
    def can_redo(self):
        return bool(self._redo) and not self.uncertain

    @property
    def undo_name(self):
        """Name of the step :meth:`undo` would reverse, or None."""
        return self._undo[-1].name if self._undo else None

    @property
    def redo_name(self):
        return self._redo[-1].name if self._redo else None

    def undo_names(self):
        """Most recent first -- the list `UndoMultiple` shows."""
        return [r.name for r in reversed(self._undo)]

    def redo_names(self):
        return [r.name for r in reversed(self._redo)]

    def undo(self, current, *, restore=None, failure_state=None):
        """Take one step back. Returns the Record, or None if there is nothing.

        `current` is a snapshot of the document as it is NOW; it becomes the
        record's `after` so the step can be redone. The caller restores
        ``record.before``. Refused while a record is open -- undoing halfway
        through recording a change would leave the history describing a document
        that never existed. With a restore callback, move the history only after
        the host's document restoration succeeds; a rejected restore keeps the
        original stack and snapshot references intact.
        """
        self._ready()
        if self._open is not None or not self._undo:
            return None
        record = self._undo[-1]
        self._apply_restore(record.before, restore, failure_state)
        self._undo.pop()
        record.after = current
        self._redo.append(record)
        self._state_id = record._before_state
        self._generation += 1
        return record

    def redo(self, current, *, restore=None, failure_state=None):
        """Step forward again. Returns the Record, or None.

        The caller restores ``record.after``. `current` refreshes `before` so the
        step can be undone again, which keeps an undo/redo/undo cycle exact.
        """
        self._ready()
        if self._open is not None or not self._redo:
            return None
        record = self._redo[-1]
        self._apply_restore(record.after, restore, failure_state)
        self._redo.pop()
        record.before = current
        self._undo.append(record)
        self._state_id = record._after_state
        self._generation += 1
        return record

    def _apply_restore(self, target, restore, failure_state):
        self._navigating = True
        try:
            if restore is not None:
                restore(target)
        except BaseException as exc:
            if failure_state is not None:
                try:
                    self.uncertain = failure_state(exc) == 'unknown'
                except BaseException:
                    self.uncertain = True
            self._generation += 1
            raise
        finally:
            self._navigating = False

    def clear(self):
        """Drop all history (the reference CAD application's `ClearUndoRecords`)."""
        self._ready(clearing=True)
        self._undo.clear()
        self._redo.clear()
        self._open, self._depth = None, 0
        if self.uncertain:
            self._state_id = self._next_state_id
            self._next_state_id += 1
        self.uncertain = False
        self._generation += 1

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"UndoStack(undo={len(self._undo)}, redo={len(self._redo)}, "
                f"recording={self.recording})")


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
    """Stable host interface; the selected backend alone owns the timeline."""
    def __init__(self, limit=DEFAULT_LIMIT):
        limit = max(1, int(limit))
        native = _native_backend(limit)
        self._backend = native if native is not None else LegacyUndoStack(limit)

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
