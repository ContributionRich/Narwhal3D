"""Pure catalog model for Rhino's New-document templates.

MEASURED from the Rhino ``New`` and ``Notes`` help pages: a template is a
3DM file; selecting ``No template`` uses internal defaults; and Notes lines
beginning exactly with ``'``, ``#``, or ``//`` describe a template rather than
being copied into the new document.

OURS: this module deliberately stops at catalog data. It neither opens a
model in the application nor displays a file dialog. The 3DM metadata reader
is injected so discovery remains deterministic and testable without Rhino,
Blender, or a real document.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

_DESCRIPTION_PREFIXES = ("'", "#", "//")


@dataclass(frozen=True)
class TemplateMetadata:
    """Stable metadata extracted from one template file.

    ``path`` is absolute and normalized for identity, while the other fields
    are plain strings. Keeping the reader's document object out of this value
    makes catalog results safe to cache, compare, and serialize.
    """

    path: str
    name: str
    description: str = ""
    units: str = ""
    notes: str = ""

    @property
    def is_no_template(self) -> bool:
        return False


@dataclass(frozen=True)
class NoTemplate:
    """The explicit New-command choice to use Rhino's internal defaults."""

    name: str = "No template"
    path: None = None
    description: str = "Use Rhino's internal defaults."
    units: str = ""
    notes: str = ""

    @property
    def is_no_template(self) -> bool:
        return True


TemplateEntry = TemplateMetadata | NoTemplate
NO_TEMPLATE = NoTemplate()


def is_description_line(line: str) -> bool:
    """Return whether a Notes line is a template description line.

    Prefix matching is intentionally column-sensitive. For example, ``" #x"``
    is an ordinary note, not a description.
    """
    if not isinstance(line, str):
        raise TypeError("template note lines must be strings")
    return line.startswith(_DESCRIPTION_PREFIXES)


def split_description(notes: str) -> tuple[str, str]:
    """Return ``(description, notes_for_new_document)``.

    Line endings are retained and lines are kept in their original order within
    each result. This is the same rule used by the document Notes model, kept
    local so the catalog has no package or UI dependency.
    """
    if not isinstance(notes, str):
        raise TypeError("template notes must be a string")
    description: list[str] = []
    copied: list[str] = []
    for line in notes.splitlines(keepends=True):
        target = description if is_description_line(line.rstrip("\r\n")) else copied
        target.append(line)
    return "".join(description), "".join(copied)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _field(raw: Any, *names: str, default: Any = "") -> Any:
    if isinstance(raw, Mapping):
        for name in names:
            if name in raw:
                return raw[name]
        return default
    for name in names:
        value = getattr(raw, name, None)
        if value is not None:
            try:
                return value() if callable(value) else value
            except TypeError:
                continue
    return default


def metadata_from_reader(path: str, raw: Any) -> TemplateMetadata:
    """Convert an injected reader result into stable template metadata.

    Reader results may be mappings or lightweight objects. Supported aliases
    (``notes``/``Notes`` and ``units``/``ModelUnitSystem``) make this useful
    with both small test doubles and rhino3dm adapters.
    """
    absolute = os.path.abspath(os.path.normpath(path))
    name = _text(_field(raw, "name", "Name", default=os.path.basename(absolute)))
    notes = _text(_field(raw, "notes", "Notes", "document_notes", default=""))
    description, copied = split_description(notes)
    explicit = _field(raw, "description", "Description", default=None)
    if explicit is not None and _text(explicit):
        description = _text(explicit)
    units = _text(_field(raw, "units", "Units", "ModelUnitSystem", default=""))
    return TemplateMetadata(absolute, name or os.path.basename(absolute), description, units, copied)


def _default_reader(path: str) -> Any:
    """Read only metadata needed by the catalog, with no application model."""
    try:
        import rhino3dm as r3
    except ImportError as exc:  # pragma: no cover - exercised by environment
        raise RuntimeError("rhino3dm is required for the default template reader") from exc
    document = r3.File3dm.Read(path)
    settings = document.Settings
    notes = _field(document, "Notes", "notes", default="")
    if not notes:
        notes = _field(settings, "Notes", "notes", default="")
    return {
        "name": os.path.basename(path),
        "notes": notes,
        "units": _field(settings, "ModelUnitSystem", default=""),
    }


def discover(directories: Iterable[os.PathLike[str] | str],
             reader: Callable[[str], Any] | None = None) -> tuple[TemplateMetadata, ...]:
    """Discover readable ``.3dm`` files under supplied directories.

    Paths are de-duplicated by normalized absolute path and sorted by
    case-folded relative-independent path before reading. Reader failures are
    propagated: silently showing a template with missing metadata would make
    the New catalog dishonest.
    """
    paths: set[str] = set()
    for supplied in directories:
        root = os.path.abspath(os.fspath(supplied))
        if os.path.isfile(root):
            if root.casefold().endswith(".3dm"):
                paths.add(os.path.normpath(root))
            continue
        if not os.path.isdir(root):
            continue
        for current, dirnames, filenames in os.walk(root):
            dirnames.sort(key=lambda item: (item.casefold(), item))
            for filename in sorted(filenames, key=lambda item: (item.casefold(), item)):
                if filename.casefold().endswith(".3dm"):
                    paths.add(os.path.normpath(os.path.join(current, filename)))
    ordered = sorted(paths, key=lambda item: (item.casefold(), item))
    load = reader or _default_reader
    return tuple(metadata_from_reader(path, load(path)) for path in ordered)


class TemplateCatalog:
    """Immutable-order catalog used by the pure New command model."""

    def __init__(self, entries: Iterable[TemplateEntry] = ()) -> None:
        templates = tuple(entries)
        self._templates = tuple(sorted(templates, key=lambda item: (item.name.casefold(), item.name)))

    @classmethod
    def from_directories(cls, directories, reader=None) -> "TemplateCatalog":
        return cls(discover(directories, reader=reader))

    @property
    def entries(self) -> tuple[TemplateEntry, ...]:
        return (NO_TEMPLATE,) + self._templates

    def search(self, query: str = "") -> tuple[TemplateEntry, ...]:
        """Return No-template plus matching entries in stable order."""
        if not isinstance(query, str):
            raise TypeError("template search query must be a string")
        needle = query.casefold().strip()
        if not needle:
            return self.entries
        return tuple(item for item in self.entries
                     if needle in item.name.casefold()
                     or needle in item.description.casefold()
                     or (item.path is not None and needle in item.path.casefold()))

    def new_document_notes(self, entry: TemplateEntry) -> str:
        """Return notes copied into a new document, excluding descriptions."""
        if entry.is_no_template:
            return ""
        return entry.notes

    def __len__(self) -> int:
        return len(self._templates)
