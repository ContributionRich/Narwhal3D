"""Parsing of the reference CAD application numbers, fractions, and lengths with units.

Pure, standard library only, no bpy. The rules are measured from
``user_interface/accurate_modeling``: "No spaces are permitted in a number",
and a typed length with a unit suffix is converted to the model units
("if your model units are meters, and you type 27cm, the reference CAD application automatically
converts your number to 0.27").

API (both return ``(value, error)`` -- ``(float, None)`` on success and
``(None, str)`` on failure; a failure is ALWAYS an error string, never a
wrong number):

    parse_number(text) -> (float | None, str | None)
    parse_length(text, model_unit="Millimeters") -> (float | None, str | None)
"""

import re

__all__ = ["parse_number", "parse_length"]

# A bare number (no sign, no unit): a mixed number (1-3/4), a fraction (5/16),
# or a decimal/integer with optional scientific notation (123, 123.456, .456,
# 1.23e10, 1.23E10).
_BARE = (
    r"(?:"
    r"\d+-\d+/\d+"
    r"|\d+/\d+"
    r"|(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?"
    r")"
)
_SIGN = r"[+-]?"

_NUMBER_RE = re.compile(r"^(?P<sign>[+-]?)(?P<bare>" + _BARE + r")$")

# A feet-inches compound: 16'5"  or  1'2-3/4"  (feet marker ' then inches ").
_FEET_INCH_RE = re.compile(
    r"^(?P<sign>[+-]?)"
    r"(?P<feet>" + _BARE + r")'"
    r"(?P<inches>" + _BARE + r")\""
    r"$"
)

# A bare number plus an optional unit suffix (cm, millimeters, in, ', ").
# The suffix is a run of letters and/or quote characters; the whole run is
# looked up case-insensitively in _SUFFIX_MM (anything unrecognised is an error).
_SUFFIX_CHAR = r"[A-Za-z\"']"
_LENGTH_RE = re.compile(
    r"^(?P<num>" + _SIGN + _BARE + r")(?P<suffix>" + _SUFFIX_CHAR + r"+)?$"
)

# Per-form parsers (used to interpret the bare number).
_MIXED_RE = re.compile(r"^(?P<whole>\d+)-(?P<num>\d+)/(?P<den>\d+)$")
_FRAC_RE = re.compile(r"^(?P<num>\d+)/(?P<den>\d+)$")
_DEC_RE = re.compile(r"^(?P<dec>\d+(?:\.\d+)?|\.\d+)(?P<si>[eE][+-]?\d+)?$")

# # OURS: the reference CAD application accepts "any metric or US customary length unit or
# abbreviation". This is our own table of millimetres-per-unit for the spellings
# we support; it is flagged OURS because it is our choice, not a measured table.
_SUFFIX_MM = {
    "mm": 1.0, "millimeter": 1.0, "millimeters": 1.0,
    "millimetre": 1.0, "millimetres": 1.0,
    "cm": 10.0, "centimeter": 10.0, "centimeters": 10.0,
    "centimetre": 10.0, "centimetres": 10.0,
    "m": 1000.0, "meter": 1000.0, "meters": 1000.0,
    "metre": 1000.0, "metres": 1000.0,
    "km": 1e6,
    "um": 0.001, "micron": 0.001, "microns": 0.001,
    "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4,
    "ft": 304.8, "foot": 304.8, "feet": 304.8, "'": 304.8,
    "yd": 914.4, "yard": 914.4, "yards": 914.4,
    "mi": 1609344.0, "mile": 1609344.0, "miles": 1609344.0,
}

# The canonical model-unit names we accept (case-insensitive); anything else is
# an error, never a guess.
_MODEL_UNIT_MM = {
    "millimeters": 1.0,
    "centimeters": 10.0,
    "meters": 1000.0,
    "kilometers": 1e6,
    "microns": 0.001,
    "inches": 25.4,
    "feet": 304.8,
    "yards": 914.4,
    "miles": 1609344.0,
}

_FOOT_MM = 304.8
_INCH_MM = 25.4


def _has_space(s):
    return any(c in s for c in (" ", "\t", "\n", "\r"))


def _bare_value(bare):
    """Interpret a bare number (mixed, fraction, or decimal/scientific)."""
    m = _MIXED_RE.match(bare)
    if m:
        den = int(m.group("den"))
        if den == 0:
            return None
        return int(m.group("whole")) + int(m.group("num")) / den
    m = _FRAC_RE.match(bare)
    if m:
        den = int(m.group("den"))
        if den == 0:
            return None
        return int(m.group("num")) / den
    m = _DEC_RE.match(bare)
    if m:
        return float(m.group("dec") + (m.group("si") or ""))
    return None


def parse_number(text):
    """Parse a the reference CAD application number (no unit). Returns ``(value, error)``."""
    if not isinstance(text, str):
        return None, "expected a string"
    if _has_space(text):
        return None, "no spaces are permitted in a number"
    m = _NUMBER_RE.match(text)
    if not m:
        return None, "not a number: %r" % (text,)
    sign = -1.0 if m.group("sign") == "-" else 1.0
    val = _bare_value(m.group("bare"))
    if val is None:
        return None, "not a number: %r" % (text,)
    return sign * val, None


def parse_length(text, model_unit="Millimeters"):
    """Parse a length (number + optional unit) and convert to *model_unit*.

    A bare number has no unit and is returned unchanged (it is already in model
    units). Returns ``(value, error)``.
    """
    if not isinstance(text, str):
        return None, "expected a string"
    if _has_space(text):
        return None, "no spaces are permitted in a number"
    mu = _MODEL_UNIT_MM.get(model_unit.lower())
    if mu is None:
        return None, "unknown model unit: %r" % (model_unit,)

    m = _FEET_INCH_RE.match(text)
    if m:
        feet = _bare_value(m.group("feet"))
        inches = _bare_value(m.group("inches"))
        if feet is None or inches is None:
            return None, "not a length: %r" % (text,)
        sign = -1.0 if m.group("sign") == "-" else 1.0
        mm = sign * (feet * _FOOT_MM + inches * _INCH_MM)
        return mm / mu, None

    m = _LENGTH_RE.match(text)
    if not m:
        return None, "not a length: %r" % (text,)
    num, err = parse_number(m.group("num"))
    if err is not None:
        return None, err
    suffix = m.group("suffix")
    if suffix is None:
        return num, None  # bare number: already in model units
    smm = _SUFFIX_MM.get(suffix.lower())
    if smm is None:
        return None, "unknown unit suffix: %r" % (suffix,)
    return (num * smm) / mu, None
