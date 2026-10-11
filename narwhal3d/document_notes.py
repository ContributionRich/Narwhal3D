"""Pure model for Rhino's document Notes and Notes document properties.

MEASURED from ``commands/notes`` and ``documentproperties/notes``:
notes are saved with the document, ``Locked`` prevents text changes, and print
setup can place notes at the top or bottom of printed output.  A template note
whose line begins with ``'``, ``#``, or ``//`` is a template description; it is
shown in the template chooser but is not copied into a new document.

OURS: this module is a UI-independent text model.  The ordered dictionary
adapter is deliberately explicit so save/reload tests do not depend on a
 dataclass repr or enum implementation detail.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class NotesLockedError(PermissionError):
    """Raised when note text is edited while the document's notes are locked."""


class PrintPlacement(str, Enum):
    NONE = "none"
    TOP = "top"
    BOTTOM = "bottom"


_TEMPLATE_PREFIXES = ("'", "#", "//")


def is_template_description_line(line: str) -> bool:
    """Return whether *line* is a template-description line.

    The marker must be the first character(s), as Rhino documents it; leading
    whitespace does not turn an ordinary note into a template description.
    """
    if not isinstance(line, str):
        raise TypeError("template description lines must be strings")
    return line.startswith(_TEMPLATE_PREFIXES)


def split_template_description(text: str) -> tuple[str, str]:
    """Split template description lines from the notes copied to a new file.

    Returns ``(description, copied_notes)``.  Every marked line is description
    text and every unmarked line is copied.  Line endings are retained within
    each result, so a round trip does not rewrite the user's note text.
    """
    if not isinstance(text, str):
        raise TypeError("notes text must be a string")
    lines = text.splitlines(keepends=True)
    descriptions = []
    copied = []
    for line in lines:
        content = line.rstrip("\r\n")
        (descriptions if is_template_description_line(content) else copied).append(line)
    return "".join(descriptions), "".join(copied)


@dataclass
class DocumentNotes:
    """The persisted text, lock state, and print placement for one document."""

    text: str = ""
    locked: bool = False
    print_placement: PrintPlacement = PrintPlacement.NONE

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("notes text must be a string")
        self.locked = _coerce_bool(self.locked, "locked")
        self.print_placement = PrintPlacement(self.print_placement)

    def set_text(self, text: str) -> None:
        if self.locked:
            raise NotesLockedError("document notes are locked")
        if not isinstance(text, str):
            raise TypeError("notes text must be a string")
        self.text = text

    def set_locked(self, locked: bool) -> None:
        self.locked = _coerce_bool(locked, "locked")

    def set_print_placement(self, placement: PrintPlacement | str) -> None:
        self.print_placement = PrintPlacement(placement)

    @property
    def template_description(self) -> str:
        """The marked description portion when this note is used as a template."""
        return split_template_description(self.text)[0]

    def for_new_document(self) -> "DocumentNotes":
        """Return notes copied from this template, excluding its description."""
        _, copied = split_template_description(self.text)
        return DocumentNotes(copied, locked=False, print_placement=self.print_placement)

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, plain-data persistence representation."""
        return {
            "locked": self.locked,
            "print_placement": self.print_placement.value,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DocumentNotes":
        """Restore notes from the adapter representation with strict fields."""
        if not isinstance(data, Mapping):
            raise TypeError("document notes serialization must be a mapping")
        expected = {"locked", "print_placement", "text"}
        if set(data) != expected:
            raise ValueError("document notes serialization requires exactly locked, print_placement, text")
        return cls(text=data["text"], locked=data["locked"],
                   print_placement=data["print_placement"])


def _coerce_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a bool")
    return value


Notes = DocumentNotes
