"""Native acceleration host for the unit-format display port (RUST-UNITFORMAT-11).

This is the *live Python host* the task describes: it owns the small amount of
work that must stay in Python (numeric coercion, ``str``/``repr`` of the
caller's arguments, and exception *type and message*) and delegates the pure
computation -- mode normalisation, precision range validation, rounding,
fraction reduction and string building -- to the reviewed Rust port in
``native/pure_core/src/unitformat.rs``.

It is the qualified host used by ``narwhal3d/unitformat.py``. The public entry point is
``format_length(value, model_unit, mode, precision) -> str`` and it raises the
*same* exception types and messages as the reference for the *same* arguments,
in the *same* order (value, then mode, then precision).

Ownership split (per the task):

* **Python (this module)** -- the value's numeric coercion (``float()``), the
  mode's ``str()`` handling, the precision's ``int()`` coercion, and building
  each exception's type and message (the reference renders the *original*
  argument with ``repr``/``str``, which only Python holds).
* **Rust (``unitformat.rs``)** -- mode-name normalisation, precision range
  validation, the decimal-context digit limit, rounding, reduction and the
  rendered string, writing into a caller-owned buffer.

Decimal context (MEASURED against CPython 3.13):

* **Fractional / Feet-Inch:** the reference computes
  ``int((Decimal(str(mag)) * denom).quantize(1, HALF_UP))``.  The **multiply**
  is a context operation rounded to ``prec`` significant digits using the
  context's rounding mode (default **HALF_EVEN**); the **quantize** then rounds
  to integer with an explicit ``rounding=ROUND_HALF_UP`` (which overrides the
  context rounding).  A result coefficient longer than ``prec`` raises
  ``decimal.InvalidOperation``.
* **Decimal:** the reference computes
  ``Decimal(str(mag)).quantize(10^-places, HALF_UP)`` -- a direct quantize,
  no multiply.  The digit limit is on the result coefficient.
* This host emulates the DEFAULT context rounding (HALF_EVEN) for the multiply
  step.  A **non-default context rounding mode** is refused with ``RuntimeError``
  (the reference would use the context rounding for the multiply, which this
  port does not support).  The app never customizes the context (verified: no
  ``localcontext`` / ``setcontext`` in the app).
* A *custom* context (un-trapped ``InvalidOperation``, custom Emin/Emax) is
  OUT OF SCOPE -- the app never customizes the decimal context.

No fallback: a missing library, a missing/invalid ABI version, a missing
export, an invalid native response code, an unbounded native size report, or a
non-default context rounding mode raises ``RuntimeError``.  The reference is
*not* replayed.
"""
from __future__ import annotations

import ctypes
import decimal
import os
import sys
from functools import lru_cache
from pathlib import Path

# -- error / mode codes, kept in lock-step with unitformat.rs ----------------
UF_OK = 0
UF_DECIMAL = 0
UF_FRACTIONAL = 1
UF_FEET_INCH = 2
UF_PRECISION_RANGE = 10
UF_NAN = 11
UF_INVALID_OP = 12
UF_UNSUPPORTED = 13
UF_SHORT_BUFFER = 14

# The only codes each native call may legitimately return.
_RESOLVE_CODES = (UF_DECIMAL, UF_FRACTIONAL, UF_FEET_INCH, UF_UNSUPPORTED)
_FORMAT_OK_CODES = (UF_OK, UF_PRECISION_RANGE, UF_NAN, UF_INVALID_OP,
                    UF_UNSUPPORTED, UF_SHORT_BUFFER)

# Bounded caller-owned output. The longest real rendering is a 309-digit
# coefficient plus a sign and a decimal point (~315 bytes); 4096 is a generous
# cap. A native that reports ``needed`` above this is a lie or a bug and is
# refused rather than allocated (a lying ``needed`` of 10**9 must not become a
# gigabyte allocation).
_MAX_OUTPUT = 4096

_I64_MIN = -(2 ** 63)
_I64_MAX = 2 ** 63 - 1

_LIBRARY_NAME = ("narwhal_pure_core.dll" if sys.platform == "win32" else
                 "libnarwhal_pure_core.dylib" if sys.platform == "darwin" else
                 "libnarwhal_pure_core.so")
_PACKAGED_LIBRARY = str(Path(__file__).resolve().parent / "native" / _LIBRARY_NAME)

_REQUIRED = (
    "unit-format display requires the native exports "
    "narwhal_pure_core_unitformat_resolve_mode and "
    "narwhal_pure_core_unitformat_format (no Python fallback)")

# Exceptions a ctypes call can raise when the library is broken or missing.
_NATIVE_CALL_ERRORS = (OSError, ValueError, AttributeError,
                       ctypes.ArgumentError, TypeError)


@lru_cache(maxsize=2)
def _bindings(path):
    """Load the pure-core library and resolve the two unit-format exports.

    Returns ``(resolve_mode, format_fn)`` or ``(None, None)`` when the library
    is absent, the ABI version is missing/wrong, or either export is missing.
    The caller turns ``(None, None)`` into a ``RuntimeError`` (never a
    fallback). Every lookup is guarded: a missing *abi-version* symbol used to
    surface as an uncaught ``AttributeError``.
    """
    try:
        library = ctypes.CDLL(path)
    except OSError:
        return None, None
    try:
        version = library.narwhal_pure_core_abi_version
        version.argtypes = []
        version.restype = ctypes.c_uint32
        if version() != 1:
            return None, None
    except _NATIVE_CALL_ERRORS:
        return None, None
    try:
        resolve = library.narwhal_pure_core_unitformat_resolve_mode
        resolve.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        resolve.restype = ctypes.c_int32
    except _NATIVE_CALL_ERRORS:
        return None, None
    try:
        fmt = library.narwhal_pure_core_unitformat_format
        fmt.argtypes = [ctypes.c_double, ctypes.c_uint32, ctypes.c_int64,
                        ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
                        ctypes.POINTER(ctypes.c_size_t)]
        fmt.restype = ctypes.c_int32
    except _NATIVE_CALL_ERRORS:
        return None, None
    return resolve, fmt


def _native(reason):
    return RuntimeError(f"{_REQUIRED}: {reason}")


def _require_native():
    resolve, fmt = _bindings(os.environ.get("NARWHAL_PURE_CORE") or _PACKAGED_LIBRARY)
    if resolve is None or fmt is None:
        raise _native("export unavailable or ABI version mismatch")
    return resolve, fmt


def _context_precision() -> int:
    """The caller's ``decimal`` context precision (the reference's digit limit).

    Also validates that the context rounding mode is the default (HALF_EVEN).
    A non-default rounding mode is refused with ``RuntimeError``: the reference
    would use the context rounding for the multiply step, which this port does
    not support.  This is an explicit guard, not a silent divergence.
    """
    ctx = decimal.getcontext()
    rounding = ctx.rounding
    if rounding != decimal.ROUND_HALF_EVEN:
        raise _native(
            f"decimal context rounding mode {rounding!r} is not the default "
            f"(ROUND_HALF_EVEN); this port does not support non-default context "
            f"rounding modes (no Python fallback)")
    standard = decimal.Context()
    if (ctx.Emin, ctx.Emax, ctx.clamp, dict(ctx.traps)) != (
            standard.Emin, standard.Emax, standard.clamp, dict(standard.traps)):
        raise _native("custom decimal exponent limits, clamp or traps are not supported")
    if not 1 <= ctx.prec <= 0xffffffff:
        raise _native("decimal context precision exceeds the native ABI bound")
    return ctx.prec


def _unsupported_mode(mode) -> ValueError:
    return ValueError(
        f"unsupported display mode {mode!r} -- use 'Decimal', 'Fractional' or "
        f"'Feet & Inches'")


def _range_error(is_decimal: bool, precision) -> ValueError:
    if is_decimal:
        return ValueError(f"Decimal precision out of range (0..12): {precision}")
    return ValueError(f"Fraction denominator out of range (1..1024): {precision}")


def _int_precision(is_decimal: bool, precision):
    """Coerce ``precision`` with ``int()`` as the reference does; the *type* and
    *message* of a failed coercion are the reference's (Python's job)."""
    try:
        return int(precision)
    except (TypeError, ValueError):
        if is_decimal:
            raise ValueError(
                f"Decimal precision must be an integer, got {precision!r}") from None
        raise ValueError(
            f"Fraction denominator must be an integer, got {precision!r}") from None


def _call_format(fmt, value: float, mode_code: int, prec: int, ctxprec: int):
    """Two-pass call into the native formatter, with strict bounded responses.

    Returns ``(code, output_bytes)`` where ``output_bytes`` is the rendered
    string only when ``code`` is ``UF_OK`` (or ``UF_SHORT_BUFFER`` on the first
    pass, retried with a correctly sized buffer). Any code outside the
    documented set, or any ``needed`` above ``_MAX_OUTPUT`` (or above the buffer
    we offered), is refused as ``RuntimeError`` -- a lying native report must
    never become a huge allocation.
    """
    def invoke(buf, cap):
        needed = ctypes.c_size_t(0)
        code = fmt(value, mode_code, prec, ctxprec,
                   buf, ctypes.c_size_t(cap), ctypes.byref(needed))
        return code, needed.value

    probe = ctypes.create_string_buffer(1)
    code, needed = invoke(probe, 1)
    if code not in _FORMAT_OK_CODES:
        raise _native(f"invalid format code {code} (expected one of {_FORMAT_OK_CODES})")
    if code == UF_OK:
        # On the 1-byte probe the only consistent OK is a 1-byte result ("0").
        if needed != 1:
            raise _native(f"format reported OK with needed={needed} on a 1-byte probe")
        return UF_OK, probe.raw[:1]
    if code != UF_SHORT_BUFFER:
        return code, b""
    if needed < 1 or needed > _MAX_OUTPUT:
        raise _native(
            f"short-buffer size report {needed} outside [1, {_MAX_OUTPUT}]")
    buf = ctypes.create_string_buffer(needed)
    code, needed2 = invoke(buf, needed)
    if code not in _FORMAT_OK_CODES:
        raise _native(f"invalid format code {code} on retry "
                      f"(expected one of {_FORMAT_OK_CODES})")
    if code == UF_SHORT_BUFFER:
        if needed2 < 1 or needed2 > _MAX_OUTPUT:
            raise _native(
                f"retry short-buffer size report {needed2} outside [1, {_MAX_OUTPUT}]")
        raise _native(f"native still short of a {needed}-byte buffer "
                      f"(reported needs {needed2})")
    if code != UF_OK or needed2 != needed:
        raise _native(
            f"inconsistent retry report code={code} needed={needed2} for capacity {needed}")
    return UF_OK, buf.raw[:needed]


def format_length(value_in_model_units, model_unit, mode, precision) -> str:
    """Format a length (in model units) for display, delegating to the Rust port.

    Raises the reference's exact exceptions, in the reference's order: a bad
    *value* (``ValueError``), then a bad *mode* (``ValueError``), then a bad
    *precision* (``ValueError`` for a non-integer or out-of-range value) and,
    during formatting, ``ValueError("cannot convert NaN to integer")`` or
    ``decimal.InvalidOperation`` for infinities / over-long coefficients.
    ``model_unit`` is accepted for API completeness and does not alter the
    rendered string.  Raises ``RuntimeError`` if the native exports are absent
    or answer inconsistently (never a fallback to the retired Python path).
    """
    resolve, fmt = _require_native()

    # 1. VALUE (Python owns coercion; the reference's message uses the original).
    if isinstance(value_in_model_units, str):
        raise ValueError(
            f"value must be a number, not a string {value_in_model_units!r}")
    if isinstance(value_in_model_units, bool) or \
            not isinstance(value_in_model_units, (int, float)):
        try:
            value = float(value_in_model_units)
        except (TypeError, ValueError):
            raise ValueError(
                f"value must be a number, got {value_in_model_units!r}") from None
    else:
        value = float(value_in_model_units)

    # 2. MODE (Rust owns normalisation; Python owns the str() and the message).
    # The mode span is bounded by the Rust side (1 MiB, an explicit documented
    # limit); here we only guard the encoding, not the length.
    mode_text = str(mode)
    try:
        mode_bytes = mode_text.encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate can never be one of the ASCII mode names.
        raise _unsupported_mode(mode) from None
    mode_code = resolve(mode_bytes, len(mode_bytes))
    if mode_code not in _RESOLVE_CODES:
        raise _native(f"invalid resolve code {mode_code} "
                      f"(expected one of {_RESOLVE_CODES})")
    if mode_code == UF_UNSUPPORTED:
        raise _unsupported_mode(mode)
    is_decimal = mode_code == UF_DECIMAL

    # 3. PRECISION (Python owns int(); Rust owns the range decision).
    prec = _int_precision(is_decimal, precision)
    if prec < _I64_MIN or prec > _I64_MAX:
        # Certain to be out of the mode's range, and it cannot cross the ABI.
        raise _range_error(is_decimal, precision)

    ctxprec = _context_precision()
    code, rendered = _call_format(fmt, value, mode_code, prec, ctxprec)

    if code == UF_OK:
        try:
            return rendered.decode("utf-8")
        except UnicodeError as error:
            raise _native("invalid UTF-8 output") from error
    if code == UF_PRECISION_RANGE:
        raise _range_error(is_decimal, precision)
    if code == UF_NAN:
        raise ValueError("cannot convert NaN to integer")
    if code == UF_INVALID_OP:
        # Match the reference's CPython-produced message (the ``str()`` of the
        # default ``decimal.InvalidOperation`` raised inside its ``quantize``).
        raise decimal.InvalidOperation("[<class 'decimal.InvalidOperation'>]")
    raise _native("unexpected native code "
                  f"{code} (mode={mode_code} prec={prec})")
