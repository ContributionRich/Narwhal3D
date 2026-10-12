"""Pure command-alias parsing and editable table support.

The application provides a small built-in alias set. Callers can create additional
aliases in an :class:`AliasTable`, import them from text, export them, delete them,
and restore the table to the built-in defaults.

This module has no UI or geometry dependencies so it can be used by headless
command-resolution code and focused tests.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType


#: Supported macro prefix characters and their command-line meanings.
PREFIX_MEANINGS = {
    "'": "The next command is nestable.",
    "!": "Cancels the previous command at the start of a macro.",
    "_": "Uses the canonical command and option names.",
}

# Keep the built-in macro text verbatim: prefixes and option order are part of
# each alias's behavior. Editable tables copy this mapping so custom aliases
# never alter application defaults.
_MACROS: dict[str, str] = {
    "Z": "'_Zoom",
    "ZE": "'_Zoom _Extents",
    "ZT": "'_Zoom _Target",
    "ZEA": "'_Zoom _All _Extents",
    "ZS": "'_Zoom _Selected",
    "ZSA": "'_Zoom _All _Selected",
    "S": "'_Snap",
    "O": "'_Ortho",
    "P": "'_Planar",
    "M": "! _Move",
    "U": "_Undo",
    "POn": "! _PointsOn",
    "POff": "! _PointsOff",
    "C": "'_SelCrossing",
    "W": "'_SelWindow",
    "COn": "'_CurvatureGraph",
    "COff": "'_CurvatureGraphOff",
    "Break": "! _DeleteSubCrv",
    "SelPolysurface": "'_SelPolysrf",
    "PlugInManager": "! _OptionsPage _PlugIns",
    "AdvancedDisplay": "! _OptionsPage _DisplayModes",
    "DisplayAttrsMgr": "! _OptionsPage _DisplayModes",
}

# Command targets that can currently run. This remains explicit so the module
# stays independent from the command registry and its heavier dependencies.
_KNOWN_COMMANDS = {
    "Zoom",
    "Move",
    "Undo",
    "PointsOn",
    "PointsOff",
    "SelPolysrf",
    "Ortho",
}

# A macro is an optional supported prefix, an optional canonical-name marker,
# a command, and zero or more whitespace-separated options.
_MACRO_RE = re.compile(
    r"^(?P<prefix>['!_])?\s*_?(?P<cmd>\w+)(?:\s+(?P<rest>.*))?$"
)


class Alias:
    """One alias and the parsed parts of its macro."""

    def __init__(self, name, macro, prefix, command, options):
        self.name = name
        self.macro = macro
        self.prefix = prefix
        self.command = command
        self.options = options

    @property
    def implemented(self) -> bool:
        return self.command in _KNOWN_COMMANDS

    @property
    def status(self) -> str:
        return "implemented" if self.implemented else "not implemented yet"

    @property
    def message(self) -> str:
        """Return an honest status message for callers that cannot run an alias."""
        if self.implemented:
            return f"{self.command}: already implemented"
        return (
            f"{self.name} -> {self.command} is not implemented yet "
            f"(the target command is not built)"
        )

    def __repr__(self):  # pragma: no cover - debugging aid
        return (
            f"Alias({self.name!r} -> {self.command!r}{self.options}, "
            f"prefix={self.prefix!r}, {self.status})"
        )


def parse_macro(macro: str):
    """Parse a macro into ``(prefix, command, options)``.

    Return ``(None, None, None)`` when the value does not follow the supported
    form, allowing callers to report invalid input instead of partially applying
    it.
    """
    match = _MACRO_RE.match(macro or "")
    if not match:
        return None, None, None
    prefix = match.group("prefix")
    command = match.group("cmd")
    rest = match.group("rest")
    options = tuple(token.lstrip("_").strip() for token in (rest or "").split())
    return prefix, command, options


def _build_table():
    """Build the case-insensitive application-default lookup table."""
    table = {}
    for name, macro in _MACROS.items():
        prefix, command, options = parse_macro(macro)
        if command is None:
            raise ValueError(f"alias {name!r}: unparseable macro {macro!r}")
        table[name.casefold()] = Alias(name, macro, prefix, command, options)
    return table


ALIAS_TABLE: dict[str, Alias] = _build_table()

# Expose defaults through a read-only mapping. AliasTable instances copy it.
DEFAULT_MACROS = MappingProxyType(dict(_MACROS))


@dataclass(frozen=True)
class AliasImportReport:
    """The result of an atomic text import.

    ``errors`` contains malformed lines and ``collisions`` contains aliases
    repeated in the input or already present in the table. Either condition
    rejects the entire import and leaves the table unchanged.
    """

    imported: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    collisions: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors and not self.collisions

    def __bool__(self) -> bool:
        return self.ok


class AliasTable:
    """An editable, case-insensitive command-alias table.

    Every instance starts with a copy of the built-in application defaults.
    ``ALIAS_TABLE`` and :func:`resolve_alias` expose the same defaults to
    existing command-resolution callers.
    """

    def __init__(self):
        self._aliases = self._make_aliases(DEFAULT_MACROS)

    @staticmethod
    def _make_aliases(macros):
        table = {}
        for name, macro in macros.items():
            prefix, command, options = parse_macro(macro)
            if command is None:
                raise ValueError(f"alias {name!r}: unparseable macro {macro!r}")
            table[name.casefold()] = Alias(name, macro, prefix, command, options)
        return table

    def lookup(self, name: str):
        """Return an exact alias match, case-insensitively, or ``None``."""
        if not isinstance(name, str) or not name.strip():
            return None
        return self._aliases.get(name.strip().casefold())

    resolve_alias = lookup

    @property
    def aliases(self):
        """Return aliases in deterministic display order as a read-only tuple."""
        return tuple(
            sorted(self._aliases.values(), key=lambda alias: alias.name.casefold())
        )

    def create(self, name: str, macro: str) -> Alias:
        """Create an alias, raising ``ValueError`` for invalid or duplicate input."""
        alias = self._parse_entry(name, macro)
        key = alias.name.casefold()
        if key in self._aliases:
            raise ValueError(f"alias collision: {name!r}")
        self._aliases[key] = alias
        return alias

    def delete(self, name: str) -> bool:
        """Delete an alias by case-insensitive name and report whether it existed."""
        if not isinstance(name, str):
            return False
        return self._aliases.pop(name.strip().casefold(), None) is not None

    def restore_defaults(self) -> None:
        """Discard custom aliases and restore the built-in application defaults."""
        self._aliases = self._make_aliases(DEFAULT_MACROS)

    def export_text(self) -> str:
        """Export deterministic ``alias=macro`` lines."""
        return "".join(
            f"{alias.name}={alias.macro}\n"
            for alias in self.aliases
        )

    @staticmethod
    def _parse_entry(name: str, macro: str) -> Alias:
        if (
            not isinstance(name, str)
            or not name.strip()
            or any(character.isspace() for character in name.strip())
        ):
            raise ValueError(f"malformed alias name: {name!r}")
        if "=" in name or "\n" in name or "\r" in name:
            raise ValueError(f"malformed alias name: {name!r}")
        if (
            not isinstance(macro, str)
            or not macro.strip()
            or "\n" in macro
            or "\r" in macro
        ):
            raise ValueError(f"malformed macro for alias {name!r}")
        prefix, command, options = parse_macro(macro)
        if command is None:
            raise ValueError(f"malformed macro for alias {name!r}: {macro!r}")
        return Alias(name.strip(), macro, prefix, command, options)

    def import_text(self, text: str) -> AliasImportReport:
        """Atomically import ``alias=macro`` lines.

        Blank lines and lines beginning with ``#`` are ignored. Existing names
        and duplicate names in the input are reported as collisions. Every line
        is validated before this table is changed.
        """
        errors = []
        collisions = []
        pending = {}
        if not isinstance(text, str):
            return AliasImportReport(errors=("input is not text",))
        for number, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in raw:
                errors.append(f"line {number}: expected alias=macro")
                continue
            name, macro = raw.split("=", 1)
            try:
                alias = self._parse_entry(name.strip(), macro.strip())
            except ValueError as exc:
                errors.append(f"line {number}: {exc}")
                continue
            key = alias.name.casefold()
            if key in self._aliases or key in pending:
                collisions.append(
                    f"line {number}: alias collision: {alias.name!r}"
                )
            else:
                pending[key] = alias
        if errors or collisions:
            return AliasImportReport(
                errors=tuple(errors),
                collisions=tuple(collisions),
            )
        self._aliases.update(pending)
        return AliasImportReport(imported=tuple(alias.name for alias in pending.values()))


def resolve_alias(name: str):
    """Return an exact case-insensitive application-default alias, or ``None``."""
    if not isinstance(name, str) or not name.strip():
        return None
    return ALIAS_TABLE.get(name.strip().casefold())
