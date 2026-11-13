"""Pure unit-system and tolerance model. **No `bpy`, no rhino3dm.**

Why this module exists
----------------------
The status bar reads "Millimeters" and every tolerance in the document is
expressed in the model's unit, but before this there was *no* notion of a unit
system at all. This holds the decidable part -- the unit systems the reference CAD application
documents, their scale factors to millimetres, the tolerance settings, and the
conversions between systems as plain Python. ``model.NarwhalModel`` stores the unit
system and absolute tolerance in the .3dm (``File3dm.Settings``), and the ``Units`` command
changes them; this module stays pure and supplies the names, scales and parsing.

Where every fact below comes from
--------------------------------
* **The member names** -- taken from the reference CAD application's `UnitSystem` enum in CAD SDK.xml,
  with 27 members in declaration order. These spellings are authoritative, so they are used
  verbatim as the canonical keys.
* **The default unit system and absolute tolerance** -- MEASURED from
  ``documentproperties/units.htm``: "The default unit system is millimeters,
  and the default tolerance is 0.01 millimeters."
* **The scale factors** -- the SI/metre definitions each unit is built from
  (1 inch = 25.4 mm exactly, 1 nautical mile = 1852 m exactly, etc.). These are
  the standard definitions, not the reference CAD application-specific constants; see the inline note on
  each that is not an exact metre prefix.
* **The angle-tolerance default is application-defined.** The same help page documents the
  *angle tolerance* setting but states no default value, so the value used here
  is a documented choice, not a the reference CAD application-measured one -- see
  :data:`DEFAULT_ANGLE_TOLERANCE`.

Anything the help does not document is identified as an application choice or
excluded from conversion rather than presented as a the reference CAD application-defined value.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# The 27 the reference CAD application.UnitSystem members from CAD SDK.xml, in declaration order.
# ---------------------------------------------------------------------------
UNIT_MEMBERS = (
    "None", "Angstroms", "Nanometers", "Microns", "Millimeters",
    "Centimeters", "Decimeters", "Meters", "Dekameters", "Hectometers",
    "Kilometers", "Megameters", "Gigameters", "Microinches", "Mils",
    "Inches", "Feet", "Yards", "Miles", "PrinterPoints", "PrinterPicas",
    "NauticalMiles", "AstronomicalUnits", "LightYears", "Parsecs",
    "CustomUnits", "Unset",
)

#: The two members that are NOT real units: they carry no scale and are used
#: as "no unit" placeholders. They are in the enum (and therefore in
#: :data:`UNIT_MEMBERS`) but are deliberately NOT in the scale table, so any
#: conversion involving one raises :class:`ValueError` instead of silently
#: returning a made-up number.
NON_UNITS = frozenset({"None", "Unset"})

#: The two members that are real but whose scale is set at run time:
#:   * CustomUnits -- the user defines "meters per custom unit" (see
#:     :func:`convert`); it has no fixed scale of its own.
#:   * (there is no second one -- CustomUnits is the only run-time unit.)
RUNTIME_UNITS = frozenset({"CustomUnits"})

#: Scale factor: how many MILLIMETRES one unit of `name` is.
#:
#: The metre-prefix entries are exact (a metre is a metre). The imperial and
#: specialised ones use their standard definitions:
#:   inch          25.4 mm           (exact, by definition)
#:   yard          3 inches          (exact)
#:   mile          5280 feet         (exact)
#:   nautical mile 1852 m            (exact, by definition)
#:   printer point 1/72 inch         (exactly 25.4/72 mm)
#:   printer pica  1/12 inch         (exactly 25.4/12 mm)
#:   micron/micron 1e-6 m, mil/mil  1/1000 inch, microinch 1e-6 inch
#:   astronomical unit, lightyear, parsec -- their IAU values (the lightyear and
#:     parsec use the exact metre values of the second and the light-year);
#:     see each inline note.
#:
# Fixed scales and angle factors have one numeric definition, in Rust.
# Python retains public names and host/model admission, not duplicate values.
_UNIT_SCALE_NAMES = UNIT_MEMBERS[1:25]
_adapters = {}


def _unit_adapter(name):
    adapter = _adapters.get(name)
    if adapter is not None:
        return adapter
    import importlib
    if __package__:
        adapter = importlib.import_module("." + name, __package__)
    else:
        import importlib.util
        import sys
        from pathlib import Path
        canonical = "narwhal3d_" + name
        adapter = sys.modules.get(canonical)
        if adapter is None:
            path = Path(__file__).resolve().parent / (name + ".py")
            spec = importlib.util.spec_from_file_location(canonical, path)
            adapter = importlib.util.module_from_spec(spec)
            sys.modules[canonical] = adapter
            try:
                spec.loader.exec_module(adapter)
            except BaseException:
                sys.modules.pop(canonical, None)
                raise
    _adapters[name] = adapter
    return adapter


def _unit_scales():
    result = _unit_adapter("native_unit_table").unit_table()
    if result["names"] != _UNIT_SCALE_NAMES:
        raise RuntimeError("native unit-table names/order mismatch; not replayed")
    return result["table"], "rust"


UNIT_SCALE_MM, UNIT_SCALE_SOURCE = _unit_scales()
ANGLE_UNITS = ("degrees", "radians", "gradians")
ANGLE_FACTORS = {name: _unit_adapter("native_unit_ops").angle_factor(name)
                 for name in ANGLE_UNITS}

# ---------------------------------------------------------------------------
# tolerance settings
# ---------------------------------------------------------------------------
#: MEASURED -- "The default unit system is millimeters, and the default
#: tolerance is 0.01 millimeters." (documentproperties/units.htm). Note the help
#: adds that a template can change this, so it is a default, not a law.
DEFAULT_UNIT = "Millimeters"
DEFAULT_ABSOLUTE_TOLERANCE_MM = 0.01

#: OURS -- the same page documents the *angle tolerance* setting but states no
#: default value, so there is nothing to measure. This is a chosen default (1
#: degree, a common CAD value) and is flagged OURS on purpose: do not read it as
#: a the reference CAD application-measured number.
DEFAULT_ANGLE_TOLERANCE_DEG = 1.0


def is_unit(name):
    """True if `name` is a real, fixed-scale unit (convertible, non-run-time)."""
    return name in UNIT_SCALE_MM


def is_runtime_unit(name):
    """True if `name` is a real unit whose scale is set at run time
    (only ``CustomUnits``)."""
    return name in RUNTIME_UNITS


def is_non_unit(name):
    """True if `name` is one of the two placeholder members (``None``, ``Unset``)."""
    return name in NON_UNITS


def _resolve(name, meters_per_custom=None):
    """The mm-scale for `name`, or raise a ValueError naming the problem.

    ``CustomUnits`` needs `meters_per_custom` (the user's "meters per custom
    unit", documentproperties/units.htm); without it there is no scale to use,
    so this raises rather than guessing.
    """
    if name in UNIT_SCALE_MM:
        return UNIT_SCALE_MM[name]
    if name == "CustomUnits":
        if meters_per_custom is None:
            raise ValueError(
                "CustomUnits has no fixed scale; pass meters_per_custom "
                "(the user's 'meters per custom unit')")
        return _unit_adapter("native_unit_ops").scale(float(meters_per_custom), 1000.0, 1.0)
    if name in NON_UNITS:
        raise ValueError(f"{name!r} is not a unit system (it is a placeholder)")
    valid = ", ".join(UNIT_SCALE_MM) + ", CustomUnits"
    raise ValueError(f"unknown unit system {name!r}; valid: {valid}")


def unit_scale_mm(name, meters_per_custom=None):
    """How many millimetres one `name` is, or raise ValueError.

    See the module docstring for where each factor comes from. ``CustomUnits``
    requires `meters_per_custom`.
    """
    return _resolve(name, meters_per_custom)


def convert(value, from_unit, to_unit, meters_per_custom=None):
    """A length in `from_unit` expressed in `to_unit`.

    Goes through millimetres, so any pair of fixed-scale units is exact up to
    floating point: ``mm = value * scale(from)`` then ``value = mm / scale(to)``.
    ``CustomUnits`` may be either side, provided `meters_per_custom` is given.
    Raises ValueError for unknown units, non-unit placeholders, or a
    CustomUnits side without its scale.
    """
    sf = _resolve(from_unit, meters_per_custom)
    st = _resolve(to_unit, meters_per_custom)
    return _unit_adapter("native_unit_ops").scale(float(value), sf, st)


def convert_angle(value, from_unit, to_unit):
    """An angle in `from_unit` expressed in `to_unit`.

    Degrees is the base; goes through radians. Accepts the three documented
    units (degrees, radians, gradians), case-insensitively.
    """
    return _unit_adapter("native_unit_ops").convert_angle(value, from_unit, to_unit)


def list_units(include_runtime=True):
    """The real unit names (fixed scale, plus CustomUnits if requested).

    Excludes the two non-unit placeholders so this is safe to offer as a
    "unit systems" list. Order follows :data:`UNIT_MEMBERS`.
    """
    out = []
    for name in UNIT_MEMBERS:
        if name in NON_UNITS:
            continue
        if name in RUNTIME_UNITS and not include_runtime:
            continue
        out.append(name)
    return out


#: The typed forms the reference CAD application documents for each unit (documentproperties/units.htm, "Distance
#: units" -- MEASURED: `1.2mic`, `1.2micron(s)`, `1.2mm`, `1.2c`, `1.2cm`, `1.2m`, `1.2km`,
#: `1.2microinch(es)`, `1.2mil(s)`, `1"`, `1in`, `1inch(es)`, `1'`, `1ft`, `1foot`, `1feet`,
#: `1mi`, `1mile(s)`). Every other unit is typed by its name.
# Public compatibility metadata; Rust owns the executable matching grammar.
UNIT_ABBREVIATIONS = {
    "mic": "Microns", "micron": "Microns", "microns": "Microns",
    "mm": "Millimeters", "millimeter": "Millimeters",
    "c": "Centimeters", "cm": "Centimeters", "centimeter": "Centimeters",
    "m": "Meters", "meter": "Meters",
    "km": "Kilometers", "kilometer": "Kilometers",
    "microinch": "Microinches", "microinches": "Microinches",
    "mil": "Mils", "mils": "Mils",
    '"': "Inches", "in": "Inches", "inch": "Inches", "inches": "Inches",
    "'": "Feet", "ft": "Feet", "foot": "Feet", "feet": "Feet",
    "mi": "Miles", "mile": "Miles",
}


def parse_unit(text):
    """The canonical unit-system name for typed `text`, or raise ValueError.

    Accepts, in order: a documented abbreviation (:data:`UNIT_ABBREVIATIONS`), the
    canonical name case-insensitively with or without its plural `s` and spaces
    ("Nautical mile" -> NauticalMiles), then a UNIQUE prefix of a canonical name. An
    ambiguous prefix is refused with the candidates, never guessed.
    """
    return _unit_adapter("native_unit_ops").parse_unit(text, tuple(list_units()))


def scale_factor(from_unit, to_unit):
    """The factor that keeps a model's PHYSICAL size when its unit changes: a length of 1
    `from_unit` becomes this many `to_unit` (mm -> in: 1/25.4). This is the `{2}` of
    the reference CAD application's "Model units change from {0} to {1}, scale model by {2}?"."""
    return convert(1.0, from_unit, to_unit)


def document_tolerance(model=None):
    """The document's absolute tolerance in model units -- the reference CAD application's
    `ModelAbsoluteTolerance`, which every tolerance consumer reads.

    `model` when given, else the app's open document (`narwhal3d.state.app_state.model`,
    looked up -- never imported -- so pure modules stay pure), else the documented default
    (0.01). Previously, `arclength`, `nearest`, `extend`, and `project`
    autoclose and Join all read `default_tolerances()["absolute_tolerance_mm"]` -- 0.01 in
    an INCH model too, i.e. 0.254 mm, 25x looser than the 0.01 mm meant, and blind to any
    tolerance the user set in DocumentProperties.
    """
    if model is None:
        import sys
        state = sys.modules.get("narwhal3d.state")
        model = getattr(getattr(state, "app_state", None), "model", None)
    getter = getattr(model, "absolute_tolerance", None)
    if callable(getter):
        try:
            v = float(getter())
            if v > 0.0:
                return v
        except Exception:  # noqa: BLE001
            pass
    return float(DEFAULT_ABSOLUTE_TOLERANCE_MM)


def default_tolerances():
    """The document defaults, tagged by provenance.

    ``absolute_tolerance_mm`` is MEASURED (the help states 0.01 mm);
    ``angle_tolerance_deg`` is OURS (the help documents the setting but no
    default value). Returns a dict so callers can read the provenance without
    parsing constants out of the module.
    """
    return {
        "unit": DEFAULT_UNIT,
        "absolute_tolerance_mm": DEFAULT_ABSOLUTE_TOLERANCE_MM,
        "absolute_tolerance_source": "MEASURED (documentproperties/units.htm)",
        "angle_tolerance_deg": DEFAULT_ANGLE_TOLERANCE_DEG,
        "angle_tolerance_source": "OURS (help documents the setting, no default value)",
    }
