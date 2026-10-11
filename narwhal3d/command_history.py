"""The Command History window's model. Pure Python, no bpy.

Rhino's ``CommandHistory`` command (F2, ``commands/commandhistory``) opens a
text window that, MEASURED, "lists the last 500 command lines from the current
Rhino session".  Its right-click menu and command-line options document the
operations this model covers:

  * a capped buffer -- the last 500 lines (``Clear`` "Deletes all command line
    history")
  * ``Copy All`` / ``Save As`` -- "Saves the contents of the window to a text
    file"

``CommandHistory(limit)`` is a capped, in-order ring buffer of command-line
text. Its contract is:

  append(line)      add a line; when over ``limit`` the oldest line drops
  lines(last=None)  all lines in order, or just the last ``last`` of them
  search(text)      case-insensitive; the indices of the lines containing ``text``
  save(path)        the whole buffer as plain text, written atomically
  clear()           drop every line

Implementation choices not specified by the help:

* A line is a ``str``; a non-string is refused by name (never a silent entry).
* On ``append`` a single trailing newline / CR is stripped, so ``save`` joins
  lines with one newline each (no blank-line gaps when the caller passes
  already-newlined text).
* ``search`` is a case-insensitive substring match (the window is a text view;
  "search" behaves like a find).  ``text`` must be a non-empty string.
* ``limit`` must be an integer >= 1 (a history of length 0 is not a history).
* ``lines(last)`` with ``last == 0`` gives ``[]``; a non-int ``last`` is
  refused.

The default ``limit=500`` follows the documented command-history limit.
"""

__all__ = ["CommandHistory"]


class CommandHistory:
    """A capped, in-order ring buffer of command-line text."""

    def __init__(self, limit=500):
        limit = _check_limit(limit)
        self._limit = limit
        self._lines = []

    # -- properties ---------------------------------------------------------
    @property
    def limit(self):
        return self._limit

    def __len__(self):
        return len(self._lines)

    # -- the documented operations -----------------------------------------
    def append(self, line):
        """Add one command line; drop the oldest if now over the cap.

        ``line`` must be a string.  A single trailing newline/CR is stripped so
        ``save`` produces one line per entry.
        """
        if not isinstance(line, str):
            raise ValueError(
                "a history line is a string; got %s" % type(line).__name__)
        self._lines.append(line.rstrip("\r\n"))
        if len(self._lines) > self._limit:
            del self._lines[:len(self._lines) - self._limit]

    def lines(self, last=None):
        """Return a copy of the buffer, or just its last ``last`` lines.

        ``last=None`` -> every line, in order (oldest first).  ``last=0`` ->
        ``[]``.  ``last`` must be ``None`` or an integer >= 0.
        """
        if last is None:
            return list(self._lines)
        if not isinstance(last, int) or isinstance(last, bool) or last < 0:
            raise ValueError(
                "lines(last) takes None or an int >= 0; got %r" % (last,))
        if last == 0:
            return []
        return list(self._lines[-last:])

    def search(self, text):
        """Return the indices of the lines that contain ``text`` (case-insensitively).

        ``text`` must be a non-empty string.  Indices are positions in the
        buffer as ``lines()`` returns them (0 = oldest).
        """
        if not isinstance(text, str):
            raise ValueError(
                "search(text) takes a string; got %s" % type(text).__name__)
        if not text:
            raise ValueError("search text must be non-empty")
        needle = text.lower()
        return [i for i, ln in enumerate(self._lines) if needle in ln.lower()]

    def save(self, path):
        """Write the whole buffer as plain text to ``path`` atomically.

        The lines are joined with newlines and a trailing newline is added
        (an empty buffer writes an empty file).  A temp file in the same
        directory is written first and then ``os.replace``d onto ``path``, so
        a reader never sees a half-written file.
        """
        import os
        data = "".join(ln + "\n" for ln in self._lines)
        directory = os.path.dirname(os.path.abspath(path))
        tmp = os.path.join(directory, "." + os.path.basename(path) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def clear(self):
        """Delete every line (the documented ``Clear``)."""
        self._lines = []


def _check_limit(limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(
            "limit must be an int >= 1; got %r" % (limit,))
    return limit
