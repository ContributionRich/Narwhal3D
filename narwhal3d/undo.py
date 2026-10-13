"""Undo / redo: the document history. **No `bpy`, no rhino3dm.**

Pure Python keeps document-history logic outside Blender callbacks so it can be
exercised without launching Blender.

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

    __slots__ = ("serial", "name", "before", "after")

    def __init__(self, serial, name, before, after=None):
        self.serial = serial
        self.name = name
        self.before = before
        self.after = after

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Record(#{self.serial}, {self.name!r})"


class UndoStack:
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
        self.enabled = True

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
        if not self.enabled:
            return NO_RECORD
        if self._open is not None:
            self._depth += 1
            return self._open.serial
        self._open = Record(self._next_serial, str(name or "Change"), None)
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
        if self._open is None or self._open.before is not None:
            return False
        self._open.before = snapshot
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
        if self._open is None:
            return False
        self._depth -= 1
        if self._depth > 0:
            return False
        record, self._open, self._depth = self._open, None, 0
        if record.before is None:
            return False                  # nothing happened -- no undo step
        self._undo.append(record)
        if len(self._undo) > self._limit:
            del self._undo[:len(self._undo) - self._limit]
        self._redo.clear()
        return True

    def abandon(self):
        """Throw the open record away without pushing it.

        For a command that failed partway: the caller has already restored the
        document (or nothing was changed), so there is nothing to undo.
        """
        self._open = None
        self._depth = 0

    # -- navigation --------------------------------------------------------

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

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

    def undo(self, current):
        """Take one step back. Returns the Record, or None if there is nothing.

        `current` is a snapshot of the document as it is NOW; it becomes the
        record's `after` so the step can be redone. The caller restores
        ``record.before``. Refused while a record is open -- undoing halfway
        through recording a change would leave the history describing a document
        that never existed.
        """
        if self._open is not None or not self._undo:
            return None
        record = self._undo.pop()
        record.after = current
        self._redo.append(record)
        return record

    def redo(self, current):
        """Step forward again. Returns the Record, or None.

        The caller restores ``record.after``. `current` refreshes `before` so the
        step can be undone again, which keeps an undo/redo/undo cycle exact.
        """
        if self._open is not None or not self._redo:
            return None
        record = self._redo.pop()
        record.before = current
        self._undo.append(record)
        return record

    def clear(self):
        """Drop all history (the reference CAD application's `ClearUndoRecords`)."""
        undo_count = len(self._undo)
        redo_count = len(self._redo)
        self._undo.clear()
        self._redo.clear()
        self.abandon()
        return undo_count, redo_count

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"UndoStack(undo={len(self._undo)}, redo={len(self._redo)}, "
                f"recording={self.recording})")
