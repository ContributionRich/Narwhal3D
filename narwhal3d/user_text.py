"""Pure Attribute User Text table rules.

MEASURED from the reference CAD application 8's ``properties/attributeusertext`` page: rows have Key,
Value and Type; keys beginning ``.`` are hidden from the Properties page; search
matches key or value; rows can be created/deleted, sorted, imported/exported and
matched from another object; multi-selection distinguishes keys shared by all
from keys shared by some. Textfield values are preserved, never evaluated here.

OURS where the reference CAD application's page is silent: search and sorting fold case, import files
carry a header and use comma for CSV / tab for TXT, and Match overwrites an
existing key while retaining source order.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

PLAIN = "Plain text"
BLOCK_ATTRIBUTE = "Block Attribute Text"
TEXTFIELD = "Textfield"
TYPES = (PLAIN, BLOCK_ATTRIBUTE, TEXTFIELD)
SHARED_ALL = "shared-by-all"
SHARED_SOME = "shared-by-some"


@dataclass(frozen=True)
class Row:
    key: str
    value: str
    type: str = PLAIN

    def __post_init__(self):
        if not isinstance(self.key, str) or not self.key:
            raise ValueError("User Text key must be a non-empty string")
        if not isinstance(self.value, str):
            raise ValueError("User Text value must be a string")
        if self.type not in TYPES:
            raise ValueError(f"unknown User Text type {self.type!r}")

    @property
    def hidden(self) -> bool:
        return self.key.startswith(".")


@dataclass(frozen=True)
class AggregateRow:
    key: str
    status: str
    values: tuple[str, ...]
    types: tuple[str, ...]


class Table:
    """Ordered unique-key Attribute User Text rows."""

    def __init__(self, rows=()):
        self._rows: list[Row] = []
        for row in rows:
            self.add(row)

    def rows(self, *, include_hidden: bool = False) -> list[Row]:
        return [row for row in self._rows if include_hidden or not row.hidden]

    def get(self, key: str) -> Row | None:
        return next((row for row in self._rows if row.key == key), None)

    def add(self, row: Row) -> Row:
        if not isinstance(row, Row):
            raise ValueError("User Text entries must be Row values")
        if self.get(row.key) is not None:
            raise ValueError(f"duplicate User Text key {row.key!r}")
        self._rows.append(row)
        return row

    def set(self, key: str, value: str, type: str = PLAIN) -> Row:
        row = Row(key, value, type)
        for index, existing in enumerate(self._rows):
            if existing.key == key:
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

    def search(self, text: str, *, include_hidden: bool = False) -> list[Row]:
        needle = str(text).casefold()
        return [row for row in self.rows(include_hidden=include_hidden)
                if needle in row.key.casefold() or needle in row.value.casefold()]

    def sorted(self, column: str = "key", *, include_hidden: bool = False) -> list[Row]:
        if column not in {"key", "value", "type"}:
            raise ValueError(f"unknown User Text sort column {column!r}")
        return sorted(self.rows(include_hidden=include_hidden),
                      key=lambda row: (getattr(row, column).casefold(), row.key.casefold()))

    def match(self, source: "Table") -> None:
        if not isinstance(source, Table):
            raise ValueError("Match source must be a User Text Table")
        for row in source.rows(include_hidden=True):
            self.set(row.key, row.value, row.type)

    def export_text(self, format: str = "csv", *, include_hidden: bool = True) -> str:
        delimiter = "," if format.casefold() == "csv" else "\t" if format.casefold() == "txt" else None
        if delimiter is None:
            raise ValueError("User Text export format must be csv or txt")
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, delimiter=delimiter, lineterminator="\n")
        writer.writerow(("Key", "Value", "Type"))
        for row in self.rows(include_hidden=include_hidden):
            writer.writerow((row.key, row.value, row.type))
        return stream.getvalue()

    @classmethod
    def import_text(cls, text: str, format: str = "csv") -> "Table":
        delimiter = "," if format.casefold() == "csv" else "\t" if format.casefold() == "txt" else None
        if delimiter is None:
            raise ValueError("User Text import format must be csv or txt")
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        if not rows or rows[0] != ["Key", "Value", "Type"]:
            raise ValueError("User Text import requires Key,Value,Type header")
        parsed = []
        for line, fields in enumerate(rows[1:], 2):
            if len(fields) != 3:
                raise ValueError(f"invalid User Text row {line}: expected 3 fields")
            try:
                parsed.append(Row(*fields))
            except ValueError as exc:
                raise ValueError(f"invalid User Text row {line}: {exc}") from exc
        return cls(parsed)


def aggregate(tables) -> list[AggregateRow]:
    """Aggregate exact keys across selected objects, preserving first-seen order."""
    tables = list(tables)
    if not all(isinstance(table, Table) for table in tables):
        raise ValueError("aggregate expects User Text Table values")
    order = []
    seen = set()
    for table in tables:
        for row in table.rows(include_hidden=True):
            if row.key not in seen:
                seen.add(row.key)
                order.append(row.key)
    result = []
    for key in order:
        present = [table.get(key) for table in tables]
        actual = [row for row in present if row is not None]
        status = SHARED_ALL if tables and len(actual) == len(tables) else SHARED_SOME
        result.append(AggregateRow(
            key, status,
            tuple(dict.fromkeys(row.value for row in actual)),
            tuple(dict.fromkeys(row.type for row in actual))))
    return result
