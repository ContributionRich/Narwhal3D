"""How lengths are DISPLAYED (status bar, distance commands).

``format_length(value_in_model_units, model_unit, mode, precision) -> str`` formats
a length already expressed in model units using one of Narwhal's three distance modes:

* **Distance display** -- "Sets the distance display for the status bar and
  distance and length commands."
    * **Decimal** -- "Sets the display to decimal (1.25)."
    * **Fractional** -- "Sets the display to fractional (1-1/4)."
    * **Feet & Inches** -- "Sets the display to feet and inches (1'-3")."
* **Display precision** -- "Sets the number of decimal places for the distance
  display."

The ``value`` is in the MODEL's units (whatever the document is set to); the mode
chooses only how to *render* that number.  For **Feet & Inches** the number is
treated as INCHES (so ``30.5`` -> ``2'-6 1/2"``), which matches the task's own
example and is the natural reading of a feet-and-inches display.

``precision`` means different things per mode:

* **Decimal** -- the number of decimal places (``0, 1, 2, ...``).
* **Fractional** -- the fraction DENOMINATOR (``1, 2, 4, 8, 16, 32, 64, 128``).
  ``1.75`` at denominator 16 is ``1 + 12/16`` -> reduced to ``1-3/4``.
* **Feet & Inches** -- the fraction DENOMINATOR for the INCH part only
  (``1, 2, 4, 8, 16, 32, 64, 128``).  ``30.5`` in at denominator 16 ->
  ``2'-6 1/2"`` (6.5in = 6 + 8/16 -> ``6 1/2``).

# OURS: the exact string forms follow the task examples and the help's own
examples (``1-1/4``, ``1'-3"``), which carry no unit suffix on the fractional
mode.  A bare whole number is printed without a fraction (``1``).  A negative
value prints a leading ``-`` and the magnitude in the chosen form (``-1-3/4``,
``-2'-6 1/2"``).  Rounding is round-half-up on the magnitude, so ``0.99999`` at
denominator 16 carries to the whole and prints ``1`` (Fractional) / ``1"``
(Feet & Inches) -- never ``0-16/16``.

``model_unit`` is accepted for API completeness (the value is already in it) but
does not alter the rendered string; the display mode is independent of the
underlying unit in the reference CAD application.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

_DECIMAL = "decimal"
_FRACTIONAL = "fractional"
_FEET_INCH = "feet_inch"


def _norm_mode(mode) -> str:
    """Map the documented mode names (and close variants) to an internal key."""
    m = str(mode).strip().lower().replace("&", "and")
    if m in ("decimal",):
        return _DECIMAL
    if m in ("fractional", "fraction"):
        return _FRACTIONAL
    if m in ("feet and inches", "feet_inches", "feet-inch", "feet & inches"):
        return _FEET_INCH
    raise ValueError(
        f"unsupported display mode {mode!r} -- use 'Decimal', 'Fractional' or "
        "'Feet & Inches'")


def _places(p) -> int:
    try:
        n = int(p)
    except (TypeError, ValueError):
        raise ValueError(f"Decimal precision must be an integer, got {p!r}")
    if not 0 <= n <= 12:
        raise ValueError(f"Decimal precision out of range (0..12): {p}")
    return n


def _denom(p) -> int:
    try:
        n = int(p)
    except (TypeError, ValueError):
        raise ValueError(f"Fraction denominator must be an integer, got {p!r}")
    if not 1 <= n <= 1024:
        raise ValueError(f"Fraction denominator out of range (1..1024): {p}")
    return n


def format_length(value_in_model_units, model_unit, mode, precision) -> str:
    """Format a length (in model units) for display.

    See the module docstring for the documented modes and the meaning of
    ``precision`` in each.  Raises ``ValueError`` on an unsupported mode, an
    out-of-range ``precision``, or a non-numeric value.  ``model_unit`` is
    accepted but does not alter the rendered string.
    """
    if isinstance(value_in_model_units, str):
        raise ValueError(
            f"value must be a number, not a string {value_in_model_units!r}")
    if isinstance(value_in_model_units, bool) or \
            not isinstance(value_in_model_units, (int, float)):
        try:
            value = float(value_in_model_units)
        except (TypeError, ValueError):
            raise ValueError(
                f"value must be a number, got {value_in_model_units!r}")
    else:
        value = float(value_in_model_units)

    m = _norm_mode(mode)
    if m == _DECIMAL:
        return _decimal(value, _places(precision))
    if m == _FRACTIONAL:
        return _fractional(value, _denom(precision))
    return _feet_inch(value, _denom(precision))


def _decimal(value: float, places: int) -> str:
    neg = value < 0
    mag = abs(value)
    q = Decimal(str(mag)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return ("-" if neg else "") + format(q, "f")


def _fractional(value: float, denom: int) -> str:
    neg = value < 0
    mag = abs(value)
    num = int((Decimal(str(mag)) * denom).quantize(
        Decimal(1), rounding=ROUND_HALF_UP))
    whole, rem = divmod(num, denom)
    if rem == 0:
        body = str(whole)
    else:
        g = _gcd(rem, denom)
        body = (f"{whole}-{rem // g}/{denom // g}" if whole else
                f"{rem // g}/{denom // g}")
    return ("-" if neg else "") + body


def _feet_inch(value: float, denom: int) -> str:
    neg = value < 0
    mag = abs(value)
    total_num = int((Decimal(str(mag)) * denom).quantize(
        Decimal(1), rounding=ROUND_HALF_UP))
    feet, inch_num = divmod(total_num, 12 * denom)
    inch_whole, inch_rem = divmod(inch_num, denom)
    if inch_rem == 0:
        inch_part = str(inch_whole)
    else:
        g = _gcd(inch_rem, denom)
        inch_part = (f"{inch_whole} {inch_rem // g}/{denom // g}" if inch_whole
                     else f"{inch_rem // g}/{denom // g}")
    body = f"{feet}'-{inch_part}\"" if feet else f"{inch_part}\""
    return ("-" if neg else "") + body


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a
