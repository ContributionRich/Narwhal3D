"""Pure model for the reference CAD application's Document Properties > Document User Text table.

MEASURED from ``documentproperties/usertext``: rows have Key, Value and Type;
Type is Plain text, Block Attribute Text, or Textfield; period-prefixed keys are
hidden from this panel; search covers key/value; and the table can import/export
CSV/TXT and paste values between document, object, and layout properties.

OURS: CSV has a ``Key,Value,Type`` header, TXT uses tab-delimited fields, and
search/sort are case-insensitive with stable key tie-breaks.  Textfield values
are deliberately opaque strings: this module never evaluates them.

The command view is separate from the panel view: ``GetDocumentUserText``
queries the native document table by exact key, or lists all rows on Enter,
including period-prefixed hidden keys. A missing explicit key is reported as
missing; it is not confused with the panel's hidden-row filter.

The the reference CAD application help also names .3dm import, but a pure table has no 3DM reader.  That
route refuses explicitly with ``ThreeDMImportDependencyError`` rather than
silently accepting or approximating a file.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PLAIN = "Plain text"
BLOCK_ATTRIBUTE = "Block Attribute Text"
TEXTFIELD = "Textfield"
TYPES = (PLAIN, BLOCK_ATTRIBUTE, TEXTFIELD)


class ThreeDMImportDependencyError(NotImplementedError):
    """A .3dm import needs the document/3DM backend, not this pure model."""


@dataclass(frozen=True)
class Row:
    key: str
    value: str
    type: str = PLAIN

    def __post_init__(self):
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("Document User Text key must be a non-empty string")
        if not isinstance(self.value, str):
            raise ValueError("Document User Text value must be a string")
        if self.type not in TYPES:
            raise ValueError(f"unknown Document User Text type {self.type!r}")

    @property
    def hidden(self) -> bool:
        return self.key.startswith(".")


class Table:
    """Ordered, unique-key document user-text rows."""

    def __init__(self, rows: Iterable[Row] = (), *, export_on_document_export=False):
        self._rows: list[Row] = []
        self.export_on_document_export = bool(export_on_document_export)
        for row in rows:
            self.add(row)

    def rows(self, *, include_hidden=False) -> list[Row]:
        return [r for r in self._rows if include_hidden or not r.hidden]

    def get(self, key: str) -> Row | None:
        return next((r for r in self._rows if r.key == key), None)

    def add(self, row: Row) -> Row:
        if not isinstance(row, Row):
            raise ValueError("Document User Text entries must be Row values")
        if self.get(row.key) is not None:
            raise ValueError(f"duplicate Document User Text key {row.key!r}")
        self._rows.append(row)
        return row

    def set(self, key: str, value: str, type: str = PLAIN) -> Row:
        row = Row(key, value, type)
        for index, old in enumerate(self._rows):
            if old.key == key:
                self._rows[index] = row
                return row
        self._rows.append(row)
        return row

    def delete(self, key: str) -> bool:
        for index, row in enumerate(self._rows):
            if row.key == key:
                del self._rows[index]
                return True
        return False

    def search(self, text: str, *, include_hidden=False) -> list[Row]:
        needle = str(text).casefold()
        return [r for r in self.rows(include_hidden=include_hidden)
                if needle in r.key.casefold() or needle in r.value.casefold()]

    def sorted(self, column="key", *, include_hidden=False) -> list[Row]:
        if column not in {"key", "value", "type"}:
            raise ValueError(f"unknown Document User Text sort column {column!r}")
        return sorted(self.rows(include_hidden=include_hidden),
                      key=lambda r: (getattr(r, column).casefold(), r.key.casefold()))

    @staticmethod
    def _delimiter(format: str) -> str:
        fmt = str(format).casefold().lstrip(".")
        if fmt == "csv":
            return ","
        if fmt == "txt":
            return "\t"
        raise ValueError("Document User Text format must be csv or txt")

    def export_text(self, format="csv", *, include_hidden=True) -> str:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, delimiter=self._delimiter(format), lineterminator="\n")
        writer.writerow(("Key", "Value", "Type"))
        for row in self.rows(include_hidden=include_hidden):
            writer.writerow((row.key, row.value, row.type))
        return stream.getvalue()

    @classmethod
    def import_text(cls, text: str, format="csv", *, export_on_document_export=False):
        reader = csv.reader(io.StringIO(text), delimiter=cls._delimiter(format))
        rows = list(reader)
        if not rows or rows[0] != ["Key", "Value", "Type"]:
            raise ValueError("Document User Text import requires Key,Value,Type header")
        table = cls(export_on_document_export=export_on_document_export)
        for line, fields in enumerate(rows[1:], 2):
            if not fields or fields == []:
                raise ValueError(f"invalid Document User Text row {line}: empty row")
            if len(fields) != 3:
                raise ValueError(f"invalid Document User Text row {line}: expected 3 fields")
            try:
                table.add(Row(*fields))
            except ValueError as exc:
                raise ValueError(f"invalid Document User Text row {line}: {exc}") from exc
        return table

    @classmethod
    def import_file(cls, path, *, export_on_document_export=False):
        suffix = Path(path).suffix.casefold()
        if suffix == ".3dm":
            raise ThreeDMImportDependencyError(
                "Document User Text .3dm import requires the the reference CAD application 3DM/document backend")
        if suffix not in {".csv", ".txt"}:
            raise ValueError("Document User Text import file must be .csv, .txt, or .3dm")
        return cls.import_text(Path(path).read_text(encoding="utf-8"), suffix[1:],
                               export_on_document_export=export_on_document_export)

    def paste(self, clipboard: str, *, overwrite=True) -> Row:
        """Paste one key/value, converting Attribute User Text's clipboard form.

        the reference CAD application's documented object-to-document form is ``<Key>:<Object Name>|<Value>``.
        The converted document key is the complete ``<Key>:<Object Name>`` prefix;
        this avoids losing object identity.  A plain ``<Key>|<Value>`` is accepted
        for document/layout clipboard content.  A non-overwriting paste refuses an
        existing key rather than replacing it.
        """
        key, value = self._parse_clipboard(clipboard)
        if not overwrite and self.get(key) is not None:
            raise ValueError(f"paste would overwrite existing Document User Text key {key!r}")
        return self.set(key, value)

    def paste_to_all_layouts(self, clipboard: str, layouts: Iterable["Table"]) -> int:
        """Paste to layouts using the reference CAD application's documented non-overwrite semantics."""
        key, value = self._parse_clipboard(clipboard)
        changed = 0
        for layout in layouts:
            if not isinstance(layout, Table):
                raise ValueError("all-layout paste expects Document User Text tables")
            if layout.get(key) is None:
                layout.set(key, value)
                changed += 1
        return changed

    @staticmethod
    def _parse_clipboard(clipboard: str) -> tuple[str, str]:
        if not isinstance(clipboard, str):
            raise ValueError("User Text clipboard must be text")
        if "|" not in clipboard:
            raise ValueError("invalid User Text clipboard: expected key|value")
        prefix, value = clipboard.split("|", 1)
        if not prefix:
            raise ValueError("invalid User Text clipboard: empty key")
        return prefix, value


DocumentUserText = Table
