"""The Rhino-compatible command-line calculator.

Pure, standard library only, no bpy. Rhino: "Any time you are entering a
number, length, point coordinate, or angle, you can use a math formula."

API:
    evaluate(text, model_unit="Millimeters") -> (float | None, str | None)

Grammar (recursive descent; NEVER uses eval/exec):
    expr    := term (('+' | '-') term)*
    term    := factor (('*' | '/') factor)*
    factor  := ('+' | '-')* primary
    primary := NUMBER | 'pi' | 'π' | FUNC '(' [expr (',' expr)*] ')'
              | '(' expr ')'

A NUMBER may carry a unit suffix. An ANGLE unit (``d``/``degree``/``degrees``,
``radian``/``radians``, ``grade``/``gradians``) is converted to radians, since
trig works in radians unless the argument carries an angle unit (``sin(30degrees)``
= 0.5 = ``sin(pi/6)``). A LENGTH unit (``mm``, ``cm``, ``m``, ``in``, ``'``, ... )
is converted with `numparse` to the model unit (``27cm`` in Meters = 0.27).
"No spaces in formulas". A failure is always an error string, never a number.
"""

import importlib.util as _ilu

import math

import os as _os

import re



# numparse lives beside this file; import it by path so this module works both

# as part of the narwhal3d package and when loaded directly by file path

# (including direct file-path loading).

_numparse_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "numparse.py")

_spec = _ilu.spec_from_file_location("narwhal3d_numparse", _numparse_path)

numparse = _ilu.module_from_spec(_spec)

_spec.loader.exec_module(numparse)

__all__ = ["evaluate"]

# radians-per-unit for the angle suffixes.
_ANGLE_UNITS = {
    "d": math.pi / 180.0,
    "degree": math.pi / 180.0,
    "degrees": math.pi / 180.0,
    "radian": 1.0,
    "radians": 1.0,
    "grade": math.pi / 200.0,
    "gradians": math.pi / 200.0,
}

# function name -> arity. `pow` and `atan2` take two arguments.
_FUNC_ARITY = {
    "sin": 1, "cos": 1, "tan": 1,
    "asin": 1, "acos": 1, "atan": 1,
    "atan2": 2,
    "ln": 1, "log10": 1, "exp": 1,
    "sinh": 1, "cosh": 1, "tanh": 1,
    "sqrt": 1,
    "pow": 2,
}

# # The bare number (whole/decimal/scientific, fraction, mixed). Compiled from
# numparse's own pattern so the two cannot drift.
_NUM_RE = re.compile(numparse._BARE)

# A feet-inches compound (16'5", 1'2-3/4") read as a SINGLE numeric token; the
# whole thing is valued by numparse (which handles the feet+inches arithmetic).
# No leading sign here: a sign stays an operator token, as everywhere else.
_FEET_INCH_RE = re.compile(
    numparse._BARE + r"'" + numparse._BARE + r"\""
)


class _CalcError(Exception):
    """Raised on a malformed formula; caught by evaluate()."""


def _number_value(bare, suffix, model_unit):
    """A bare number plus an optional unit suffix -> a float, or (None, error)."""
    base, err = numparse.parse_number(bare)
    if err is not None:
        return None, "bad number: %r" % (bare,)
    if not suffix:
        return base, None
    key = suffix.lower()
    if key in _ANGLE_UNITS:
        return base * _ANGLE_UNITS[key], None
    val, err = numparse.parse_length(bare + suffix, model_unit)
    if err is not None:
        return None, "unknown unit: %r" % (suffix,)
    return val, None


def _number_token(text, i, model_unit):
    """Read one numeric token at position i.

    Returns (value, new_index, error). Tries the feet-inches compound form
    (16'5") first, then a bare number with an optional unit suffix.
    """
    m = _FEET_INCH_RE.match(text, i)
    if m:
        val, err = numparse.parse_length(text[i:m.end()], model_unit)
        if err is not None:
            return None, i, err
        return val, m.end(), None
    m = _NUM_RE.match(text, i)
    if not m:
        return None, i, "bad number at: %r" % (text[i:],)
    j = m.end()
    bare = text[i:j]
    k = j
    while k < len(text) and (text[k].isalpha() or text[k] in "\"'"):
        k += 1
    suffix = text[j:k]
    value, err = _number_value(bare, suffix, model_unit)
    if err is not None:
        return None, i, err
    return value, k, None


def _tokenize(text, model_unit):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\n\r":
            return None, "no spaces in formulas"
        if c in "+-*/(),":
            tokens.append((c,))
            i += 1
            continue
        if c.isdigit() or c == "." or c in "'\"":
            value, i, err = _number_token(text, i, model_unit)
            if err is not None:
                return None, err
            tokens.append(("NUM", value))
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(("NAME", text[i:j]))
            i = j
            continue
        return None, "unexpected character: %r" % (c,)
    return tokens, None


def _apply_func(name, args):
    a = args
    try:
        if name == "sin":
            return math.sin(a[0])
        if name == "cos":
            return math.cos(a[0])
        if name == "tan":
            return math.tan(a[0])
        if name == "asin":
            return math.asin(a[0])
        if name == "acos":
            return math.acos(a[0])
        if name == "atan":
            return math.atan(a[0])
        if name == "atan2":
            return math.atan2(a[0], a[1])
        if name == "ln":
            return math.log(a[0])
        if name == "log10":
            return math.log10(a[0])
        if name == "exp":
            return math.exp(a[0])
        if name == "sinh":
            return math.sinh(a[0])
        if name == "cosh":
            return math.cosh(a[0])
        if name == "tanh":
            return math.tanh(a[0])
        if name == "sqrt":
            return math.sqrt(a[0])
        if name == "pow":
            r = a[0] ** a[1]
            if isinstance(r, complex):
                raise _CalcError("pow: result is not a real number")
            return r
    except (ValueError, ZeroDivisionError, OverflowError) as e:
        raise _CalcError("%s: %s" % (name, e))
    raise _CalcError("unknown function: %s" % name)


def _parse_call(tokens, i, name, model_unit):
    if i >= len(tokens) or tokens[i][0] != "(":
        raise _CalcError("%s requires parentheses" % name)
    i += 1
    args = []
    if i < len(tokens) and tokens[i][0] == ")":
        raise _CalcError("%s requires at least one argument" % name)
    val, i = _parse_expr(tokens, i, model_unit)
    args.append(val)
    while i < len(tokens) and tokens[i][0] == ",":
        i += 1
        val, i = _parse_expr(tokens, i, model_unit)
        args.append(val)
    if i >= len(tokens) or tokens[i][0] != ")":
        raise _CalcError("missing ) after %s" % name)
    i += 1
    arity = _FUNC_ARITY[name]
    if len(args) != arity:
        raise _CalcError("%s takes %d argument(s), got %d" % (name, arity, len(args)))
    return _apply_func(name, args), i


def _parse_primary(tokens, i, model_unit):
    if i >= len(tokens):
        raise _CalcError("expected an operand")
    kind = tokens[i][0]
    if kind == "NUM":
        return tokens[i][1], i + 1
    if kind == "NAME":
        name = tokens[i][1]
        if name == "pi" or name == "\u03c0":
            return math.pi, i + 1
        if name in _FUNC_ARITY:
            return _parse_call(tokens, i + 1, name, model_unit)
        raise _CalcError("unknown name: %s" % name)
    if kind == "(":
        value, i = _parse_expr(tokens, i + 1, model_unit)
        if i >= len(tokens) or tokens[i][0] != ")":
            raise _CalcError("unbalanced parentheses")
        return value, i + 1
    raise _CalcError("unexpected token: %r" % (kind,))


def _parse_factor(tokens, i, model_unit):
    sign = 1.0
    while i < len(tokens) and tokens[i][0] in ("+", "-"):
        sign = -sign if tokens[i][0] == "-" else sign
        i += 1
    value, i = _parse_primary(tokens, i, model_unit)
    return sign * value, i


def _parse_term(tokens, i, model_unit):
    value, i = _parse_factor(tokens, i, model_unit)
    while i < len(tokens) and tokens[i][0] in ("*", "/"):
        op = tokens[i][0]
        rhs, i = _parse_factor(tokens, i + 1, model_unit)
        if op == "*":
            value = value * rhs
        else:
            if rhs == 0:
                raise _CalcError("division by zero")
            value = value / rhs
    return value, i


def _parse_expr(tokens, i, model_unit):
    value, i = _parse_term(tokens, i, model_unit)
    while i < len(tokens) and tokens[i][0] in ("+", "-"):
        op = tokens[i][0]
        rhs, i = _parse_term(tokens, i + 1, model_unit)
        value = value + rhs if op == "+" else value - rhs
    return value, i


def evaluate(text, model_unit="Millimeters"):
    """Evaluate a Rhino formula. Returns ``(value, error)``."""
    if not isinstance(text, str):
        return None, "expected a string"
    tokens, err = _tokenize(text, model_unit)
    if err is not None:
        return None, err
    if not tokens:
        return None, "empty formula"
    try:
        value, pos = _parse_expr(tokens, 0, model_unit)
    except _CalcError as e:
        return None, str(e)
    if pos != len(tokens):
        tok = tokens[pos]
        label = tok[1] if len(tok) > 1 else tok[0]
        return None, "unexpected token: %r" % (label,)
    if not math.isfinite(value):
        return None, "result is not a finite number"
    return value, None
