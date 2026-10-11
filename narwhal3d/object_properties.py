"""Pure Object Properties value and multi-selection aggregate model.

The Object Properties panel is a view over object attributes, layer and block-parent
state, viewport overrides, and render settings. This module models those values
independently of Blender and rhino3dm; callers apply supported values through their
document adapter.

The model includes type, name, layer, display-colour source, per-viewport display mode,
linetype and scale, print colour and width, hyperlink, shadows, isocurve controls, and
custom mesh settings. ``By Parent`` applies to block members, while display mode can be
overridden per viewport.

``rhino3dm`` cannot represent several of these panel settings in document object
attributes. Such fields are capability-marked rather than being presented as persistent.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Mapping


MIXED = "mixed"
UNAVAILABLE = "unavailable"

# Property-source values are strings so the model is usable without importing
# application-specific enums.
DISPLAY_COLOR_SOURCES = frozenset({"layer", "parent", "material", "custom"})
PRINT_COLOR_SOURCES = frozenset({"display", "layer", "parent", "custom"})
PRINT_WIDTH_SOURCES = frozenset({"layer", "default", "parent", "custom"})
LINETYPE_SOURCES = frozenset({"layer", "parent", "specified"})


@dataclass(frozen=True)
class Capability:
    """Whether a field can be represented by the rhino3dm-backed document.

    ``persistent=False`` is intentionally separate from ``available``: a UI
    value may be inspectable or editable in an adapter while rhino3dm itself
    cannot encode it in ObjectAttributes.  The reason is surfaced to callers.
    """

    available: bool = True
    persistent: bool = True
    reason: str = ""


# These fields are not represented by rhino3dm ObjectAttributes.  In
# particular, never treat a Python-side value as if it will survive a.3dm
# save/reload.
RHINO3DM_UNREPRESENTABLE = frozenset({
    "display_mode",
    "cast_shadows",
    "receive_shadows",
    "isocurve_density",
    "show_isocurves",
    "custom_mesh",
    "custom_mesh_settings",
})

_UNREPRESENTABLE_REASON = (
    "rhino3dm has no ObjectAttributes field for this object-property setting; "
    "an adapter must persist it explicitly"
)


@dataclass(frozen=True)
class ObjectProperties:
    """Values shown by the Object Properties panel for one object.

    ``None`` means the source did not provide a value, not a value of zero or false.
    ``display_mode`` is keyed by viewport identifier because the override is per viewport.
    """

    type: str | None = None
    name: str | None = None
    layer: str | None = None
    display_color_source: str | None = None
    display_color: Any = None
    display_mode: Mapping[str, str] = field(default_factory=dict)
    linetype: str | None = None
    linetype_source: str | None = None
    linetype_scale: float | None = None
    print_color_source: str | None = None
    print_color: Any = None
    print_width_source: str | None = None
    print_width: float | None = None
    no_print: bool | None = None
    hyperlink: str | None = None
    cast_shadows: bool | None = None
    receive_shadows: bool | None = None
    isocurve_density: int | None = None
    show_isocurves: bool | None = None
    custom_mesh: bool | None = None
    custom_mesh_settings: Mapping[str, Any] | None = None

    def __post_init__(self):
        for field_name, allowed in (
            ("display_color_source", DISPLAY_COLOR_SOURCES),
            ("print_color_source", PRINT_COLOR_SOURCES),
            ("print_width_source", PRINT_WIDTH_SOURCES),
            ("linetype_source", LINETYPE_SOURCES),
        ):
            value = getattr(self, field_name)
            if value is not None and str(value).lower() not in allowed:
                raise ValueError(f"invalid {field_name}: {value!r}")
        if self.isocurve_density is not None and self.isocurve_density not in range(4):
            raise ValueError("isocurve_density must be one of the supported values 0..3")
        if self.linetype_scale is not None and self.linetype_scale < 0:
            raise ValueError("linetype_scale cannot be negative")
        if self.print_width is not None and self.print_width < 0:
            raise ValueError("print_width cannot be negative")
        object.__setattr__(self, "display_mode", dict(self.display_mode))
        if self.custom_mesh_settings is not None:
            object.__setattr__(self, "custom_mesh_settings",
                               dict(self.custom_mesh_settings))

    def capability(self, field_name: str) -> Capability:
        """Return representation capability for a field, without guessing persistence."""
        if field_name not in {f.name for f in fields(self)}:
            raise KeyError(field_name)
        if field_name in RHINO3DM_UNREPRESENTABLE:
            return Capability(False, False, _UNREPRESENTABLE_REASON)
        return Capability()

    def capabilities(self) -> dict[str, Capability]:
        return {f.name: self.capability(f.name) for f in fields(self)}


@dataclass(frozen=True)
class AggregateValue:
    """One row of a multi-selection aggregate."""

    state: str
    value: Any = None
    available: bool = True

    @property
    def mixed(self) -> bool:
        return self.state == MIXED

    @property
    def unavailable(self) -> bool:
        return self.state == UNAVAILABLE


def aggregate_values(values) -> AggregateValue:
    """Collapse values using Rhino-style same/mixed/unavailable semantics.

    ``None`` means unavailable.  Equality is deliberately ordinary value
    equality; mappings are normalized to plain dictionaries so viewport maps
    and mesh settings compare by contents rather than object identity.
    """
    values = tuple(values)
    present = [v for v in values if v is not None]
    if not present:
        return AggregateValue(UNAVAILABLE, None, False)
    first = present[0]
    if all(_same(first, value) for value in present[1:]) and len(present) == len(values):
        return AggregateValue("same", first, True)
    return AggregateValue(MIXED, None, True)


def _same(left, right):
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return dict(left) == dict(right)
    return left == right


def aggregate(selection: list[ObjectProperties] | tuple[ObjectProperties, ...]) -> dict[str, AggregateValue]:
    """Return one aggregate row per property for a selection.

    Empty selection and fields absent from every selected object are
    ``unavailable``.  A missing value alongside a present value is ``mixed``;
    it is never collapsed to the first object's value.
    """
    if not selection:
        return {f.name: AggregateValue(UNAVAILABLE, None, False)
                for f in fields(ObjectProperties)}
    return {
        f.name: aggregate_values(getattr(obj, f.name) for obj in selection)
        for f in fields(ObjectProperties)
    }


def aggregate_capabilities(selection: list[ObjectProperties] | tuple[ObjectProperties, ...]):
    """Aggregate capability flags without claiming unsupported fields work."""
    names = [f.name for f in fields(ObjectProperties)]
    result = {}
    for name in names:
        caps = [obj.capability(name) for obj in selection]
        if not caps:
            result[name] = Capability(False, False, "no selected object")
        elif all(cap.available and cap.persistent for cap in caps):
            result[name] = Capability()
        else:
            reasons = tuple(dict.fromkeys(cap.reason for cap in caps if cap.reason))
            result[name] = Capability(False, False, "; ".join(reasons))
    return result


__all__ = [
    "Capability", "ObjectProperties", "AggregateValue", "aggregate",
    "aggregate_values", "aggregate_capabilities", "MIXED", "UNAVAILABLE",
    "RHINO3DM_UNREPRESENTABLE",
]
