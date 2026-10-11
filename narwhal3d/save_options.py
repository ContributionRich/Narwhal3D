"""Pure Save/SaveAs option state and rhino3dm writer capabilities.

rhino3dm 8.35.0 exposes only ``Version`` and ``SaveUserData`` through
``File3dmWriteOptions``. Callers validate requested settings before translating the
supported state to writer values; unavailable options are refused by name rather than
silently ignored. This module does not write files or mutate document state.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Any, Mapping


OPTION_NAMES = (
    "Version",
    "SaveSmall",
    "GeometryOnly",
    "SaveTextures",
    "SavePlugInData",
    "Browse",
)


@dataclass(frozen=True)
class Capability:
    """What this implementation can pass through to the rhino3dm writer."""

    name: str
    supported: bool
    writer_field: str | None
    reason: str


# ``SaveUserData`` is rhino3dm's writer switch for user/plugin data.  The other
# five names have no corresponding writer control; in particular, Browse is a
# UI action, not a file-format option.
CAPABILITIES = MappingProxyType({
    "Version": Capability(
        "Version", True, "Version", "rhino3dm File3dmWriteOptions.Version",
    ),
    "SaveSmall": Capability(
        "SaveSmall", False, None,
        "rhino3dm has no render/analysis-mesh omission switch",
    ),
    "GeometryOnly": Capability(
        "GeometryOnly", False, None,
        "rhino3dm has no geometry-only writer switch",
    ),
    "SaveTextures": Capability(
        "SaveTextures", False, None,
        "rhino3dm has no embedded-texture writer switch",
    ),
    "SavePlugInData": Capability(
        "SavePlugInData", True, "SaveUserData",
        "rhino3dm File3dmWriteOptions.SaveUserData",
    ),
    "Browse": Capability(
        "Browse", False, None, "Browse opens the reference CAD application's Save window; it is UI-only",
    ),
})


class UnsupportedSaveOption(ValueError):
    """A known the reference CAD application option that this rhino3dm-backed writer cannot perform."""


class InvalidSaveOption(ValueError):
    """An unknown option or invalid option value."""


@dataclass(frozen=True)
class SaveOptions:
    """Validated command-line state for Save or SaveAs.

    ``None`` means no target the reference CAD application version was requested.  Boolean options are
    explicit state, not capability claims: a true unsupported option is rejected
    by :func:`validate`, never dropped while writing.
    """

    Version: int | None = None
    SaveSmall: bool = False
    GeometryOnly: bool = False
    SaveTextures: bool = False
    SavePlugInData: bool = False
    Browse: bool = False

    def __post_init__(self) -> None:
        if self.Version is not None and (
            isinstance(self.Version, bool)
            or not isinstance(self.Version, int)
            or not 2 <= self.Version <= 8
        ):
            raise InvalidSaveOption("Version must be an integer from 2 through 8")
        for field in fields(self)[1:]:
            value = getattr(self, field.name)
            if not isinstance(value, bool):
                raise InvalidSaveOption(f"{field.name} must be True or False")

    def as_mapping(self) -> dict[str, Any]:
        """Return all six option names, including their default values."""
        return {field.name: getattr(self, field.name) for field in fields(self)}

    def validate(self) -> "SaveOptions":
        """Validate requested options and return this immutable state."""
        for name, value in self.as_mapping().items():
            requested = value is not None and value is not False
            if requested and not CAPABILITIES[name].supported:
                raise UnsupportedSaveOption(
                    f"{name} is not supported by the rhino3dm writer: "
                    f"{CAPABILITIES[name].reason}"
                )
        return self

    def writer_options(self) -> dict[str, Any]:
        """Return only validated keyword values understood by rhino3dm.

        Refusal happens before this projection, so unsupported state cannot be
        silently accepted by omission.
        """
        self.validate()
        out: dict[str, Any] = {}
        if self.Version is not None:
            out["Version"] = self.Version
        if self.SavePlugInData:
            out["SaveUserData"] = True
        return out


def validate(options: SaveOptions | Mapping[str, Any] | None = None) -> SaveOptions:
    """Build and validate options from a dataclass or an option mapping."""
    if options is None:
        state = SaveOptions()
    elif isinstance(options, SaveOptions):
        state = options
    else:
        unknown = set(options) - set(OPTION_NAMES)
        if unknown:
            raise InvalidSaveOption(f"unknown Save option: {sorted(unknown)!r}")
        state = SaveOptions(**dict(options))
    return state.validate()


def capability_matrix() -> dict[str, Capability]:
    """Return a mutable snapshot of the public capability matrix."""
    return dict(CAPABILITIES)


def file_behavior(command: str) -> str:
    """Return the measured active-file behavior for a save command.

    SaveAs activates the new file; SaveACopy deliberately does not.  Save has
    no transition of its own.  This metadata is separate from option validation.
    """
    try:
        return {
            "Save": "current_file",
            "SaveAs": "new_file_active",
            "SaveACopy": "current_file_unchanged",
        }[command]
    except KeyError:
        raise InvalidSaveOption(f"unknown save command: {command!r}") from None
