"""Pure document Units/tolerances property model.

The fields mirror the measured ``documentproperties/units`` page without
pretending that undocumented defaults exist.  Model units and absolute
 tolerance have measured defaults (millimeters and 0.01 mm); layout units,
angle tolerance, custom-unit metadata, and display precision are explicit
until a caller supplies them.

This module is deliberately a settings adapter, not a geometry editor.
:meth:`DocumentUnits.conversion_plan` reports the physical-unit scale factor;
the caller owns the reference CAD application's measured scale-model question and any geometry
transformation.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from collections.abc import Mapping
from typing import Any

try:
    from . import units as _units
except ImportError:  # loaded directly by spike tests with a package shim
    from narwhal3d import units as _units


_DISPLAY_MODES = {"Decimal", "Fractional", "Feet & Inches"}
_DECIMAL_RANGE = range(13)
_FRACTION_DENOMINATORS = frozenset((1, 2, 4, 8, 16, 32, 64, 128))


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{field} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{field} must be a number") from None
    if not isfinite(result):
        raise ValueError(f"{field} must be finite")
    if positive and result <= 0.0:
        raise ValueError(f"{field} must be greater than zero")
    return result


def _canonical_unit(value: Any, field: str, custom_scale: float | None) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a unit name")
    try:
        name = _units.parse_unit(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {exc}") from None
    if name == "CustomUnits" and custom_scale is None:
        raise ValueError(f"{field} CustomUnits requires meters_per_custom_unit")
    if name in _units.NON_UNITS:
        raise ValueError(f"{field} cannot be {_units.NON_UNITS!r}")
    return name


@dataclass(frozen=True)
class DocumentUnits:
    """Validated, serializable Units properties for one document.

    ``angle_tolerance`` is in degrees when supplied, matching the documented
    angle input convention.  Its default is ``None`` because the help page
    names the setting but does not state a default.  ``layout_units`` and
    ``display_precision`` are likewise ``None`` when unknown, rather than
    invented defaults.
    """

    model_units: str = "Millimeters"
    layout_units: str | None = None
    absolute_tolerance: float = 0.01
    angle_tolerance: float | None = None
    custom_unit_name: str | None = None
    meters_per_custom_unit: float | None = None
    distance_display: str = "Decimal"
    display_precision: int | None = None

    def __post_init__(self) -> None:
        custom_scale = None
        if self.meters_per_custom_unit is not None:
            custom_scale = _number(self.meters_per_custom_unit,
                                    "meters_per_custom_unit", positive=True)
            object.__setattr__(self, "meters_per_custom_unit", custom_scale)
        object.__setattr__(self, "model_units",
                           _canonical_unit(self.model_units, "model_units", custom_scale))
        if self.layout_units is not None:
            object.__setattr__(self, "layout_units",
                               _canonical_unit(self.layout_units, "layout_units", custom_scale))
        if self.model_units == "CustomUnits" or self.layout_units == "CustomUnits":
            if custom_scale is None:
                raise ValueError("CustomUnits requires meters_per_custom_unit")
            if not isinstance(self.custom_unit_name, str) or not self.custom_unit_name.strip():
                raise ValueError("CustomUnits requires a non-empty custom_unit_name")
            object.__setattr__(self, "custom_unit_name", self.custom_unit_name.strip())
        elif self.custom_unit_name is not None:
            if not isinstance(self.custom_unit_name, str) or not self.custom_unit_name.strip():
                raise ValueError("custom_unit_name must be a non-empty string")
            object.__setattr__(self, "custom_unit_name", self.custom_unit_name.strip())
        if self.meters_per_custom_unit is not None and self.custom_unit_name is None:
            raise ValueError("meters_per_custom_unit requires custom_unit_name")

        absolute = _number(self.absolute_tolerance, "absolute_tolerance", positive=True)
        object.__setattr__(self, "absolute_tolerance", absolute)
        if self.angle_tolerance is not None:
            object.__setattr__(self, "angle_tolerance",
                               _number(self.angle_tolerance, "angle_tolerance", positive=True))
        if not isinstance(self.distance_display, str):
            raise TypeError("distance_display must be a string")
        display = self.distance_display.strip()
        aliases = {"feet and inches": "Feet & Inches", "feet_inches": "Feet & Inches",
                   "fraction": "Fractional", "decimal": "Decimal", "fractional": "Fractional"}
        display = aliases.get(display.lower(), display)
        if display not in _DISPLAY_MODES:
            raise ValueError("distance_display must be Decimal, Fractional, or Feet & Inches")
        object.__setattr__(self, "distance_display", display)
        if self.display_precision is not None:
            if isinstance(self.display_precision, bool) or not isinstance(self.display_precision, int):
                raise TypeError("display_precision must be an integer or None")
            if display == "Decimal" and self.display_precision not in _DECIMAL_RANGE:
                raise ValueError("Decimal display_precision must be from 0 through 12")
            if display != "Decimal" and self.display_precision not in _FRACTION_DENOMINATORS:
                raise ValueError("fractional display_precision must be a power-of-two denominator from 1 through 128")

    @property
    def has_custom_scale(self) -> bool:
        return self.meters_per_custom_unit is not None

    @property
    def has_angle_tolerance(self) -> bool:
        return self.angle_tolerance is not None

    def capabilities(self) -> dict[str, bool]:
        """Report which optional measured settings are explicitly available."""
        return {
            "model_units": True,
            "layout_units": self.layout_units is not None,
            "absolute_tolerance": True,
            "angle_tolerance": self.has_angle_tolerance,
            "custom_units": self.has_custom_scale,
            "distance_display": True,
            "display_precision": self.display_precision is not None,
            "geometry_scaling": False,
        }

    def conversion_plan(self, to_model_units: str) -> dict[str, Any]:
        """Return a caller-executed physical-size conversion plan.

        No geometry is changed here.  ``scale_geometry`` is intentionally not
        an option: the measured yes/no question belongs to the caller.
        """
        target = _canonical_unit(to_model_units, "to_model_units", self.meters_per_custom_unit)
        factor = _units.convert(1.0, self.model_units, target,
                                meters_per_custom=self.meters_per_custom_unit)
        return {"from_model_units": self.model_units, "to_model_units": target,
                "scale_factor": factor, "scale_geometry": None,
                "geometry_scaling": "caller responsibility"}

    def to_dict(self) -> dict[str, Any]:
        return {"absolute_tolerance": self.absolute_tolerance,
                "angle_tolerance": self.angle_tolerance,
                "custom_unit_name": self.custom_unit_name,
                "distance_display": self.distance_display,
                "display_precision": self.display_precision,
                "layout_units": self.layout_units,
                "meters_per_custom_unit": self.meters_per_custom_unit,
                "model_units": self.model_units}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DocumentUnits":
        if not isinstance(data, Mapping):
            raise TypeError("document units serialization must be a mapping")
        expected = {"absolute_tolerance", "angle_tolerance", "custom_unit_name",
                    "distance_display", "display_precision", "layout_units",
                    "meters_per_custom_unit", "model_units"}
        if set(data) != expected:
            raise ValueError("document units serialization requires exactly the documented fields")
        return cls(**{key: data[key] for key in expected})


UnitsSettings = DocumentUnits
