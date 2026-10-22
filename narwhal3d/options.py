"""the reference CAD application command-line option framework. **No `bpy`, no rhino3dm.**

Why this module exists
---------------------
the reference CAD application commands are not just a fixed sequence of prompts. Almost every command is
*driven* by options shown in brackets in the prompt::

    Corner of base ( Diagonal  Cube  3Point  Vertical  Center ):
    Offset distance <1.0> ( Loose=No  Corner=Sharp  Trim=Yes  BothSides=No ):

the reference CAD application documents hundreds of options across its commonly used commands. Options
are central to the command-line workflow, so they need an explicit shared model.

This module is the decidable, testable core of that: what an option *is*, how
typed input is matched against options, and how the prompt is rendered. It is
deliberately pure Python -- no `bpy`, no `rhino3dm`, and no package-relative
imports, so it can be loaded directly and exercised without launching Blender. That is the same split already used by
``cmdbar.py`` (prompt layout), ``views.py`` (view labels) and ``cplane.py``
(picking math), and for the same reason: logic that lives inside a Blender
callback is logic no test can reach.

The four kinds of option (taken from the the reference CAD application 8 docs, not invented)
-------------------------------------------------------------------
Verified against `commands/box.htm` and `commands/offset.htm`:

``MODE``
    Branches the command -- it changes what gets built and which prompts follow.
    Box: ``Diagonal``, ``Cube``, ``3Point``, ``EdgeMidpoint``, ``Vertical``,
    ``Center``. Carries no stored value; the command reacts to it.
``TOGGLE``
    A Yes/No flag, e.g. Offset's ``Loose``, ``Trim``, ``BothSides``, ``Cap``.
``ENUM``
    One of a fixed set, e.g. Offset's ``Corner`` = Sharp/Round/Smooth/Chamfer/None.
``VALUE``
    Prompts for a number or string, e.g. Offset's ``Distance``, ``Tolerance``.

How the reference CAD application matches what you type
-------------------------------
* **Case-insensitive.**
* **Unique prefix wins** -- the same rule as command names, so ``Dia`` selects
  ``Diagonal``. An ambiguous prefix is reported, not guessed at.
* Two forms are accepted: bare (``Corner``) and inline (``Corner=Round``,
  ``Distance=2.5``, ``BothSides=Yes``).
* A bare TOGGLE **flips** -- that is what clicking it in the command bar does.
* A bare ENUM or VALUE needs a value, so the command asks for one and stays on
  the same step (``NEEDS_VALUE``).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# option kinds
# ---------------------------------------------------------------------------

MODE = "mode"
TOGGLE = "toggle"
ENUM = "enum"
VALUE = "value"

KINDS = (MODE, TOGGLE, ENUM, VALUE)

# ---------------------------------------------------------------------------
# match outcomes
# ---------------------------------------------------------------------------

#: the text did not look like any option -- treat it as a normal step argument
NONE = "none"
#: a prefix that fits more than one option; the reference CAD application reports rather than guesses
AMBIGUOUS = "ambiguous"
#: matched and a value was applied (inline form, or a toggle flip)
SET = "set"
#: matched a MODE option; the command must branch
BRANCH = "branch"
#: matched, but a value is still required -- the command should prompt for it
NEEDS_VALUE = "needs_value"
#: matched, but the supplied value was not acceptable
INVALID = "invalid"


class Option:
    """One command-line option.

    `default` is the starting value for TOGGLE/ENUM/VALUE options (MODE options
    hold no value). `choices` lists the permitted values for an ENUM.
    `value_kind` ("number" or "text") says how to read a VALUE option.
    """

    __slots__ = ("name", "kind", "default", "choices", "value_kind", "help")

    def __init__(self, name, kind, default=None, choices=(), value_kind="number",
                 help=""):  # noqa: A002 - "help" mirrors the the reference CAD application docs wording
        if kind not in KINDS:
            raise ValueError(f"unknown option kind {kind!r} (expected one of {KINDS})")
        if kind == ENUM and not choices:
            raise ValueError(f"ENUM option {name!r} needs choices")
        if kind == ENUM and default is not None and default not in choices:
            raise ValueError(
                f"ENUM option {name!r} default {default!r} is not in {choices}")
        if kind == TOGGLE and default is not None and not isinstance(default, bool):
            raise ValueError(f"TOGGLE option {name!r} default must be bool")
        self.name = name
        self.kind = kind
        self.default = default
        self.choices = tuple(choices)
        self.value_kind = value_kind
        self.help = help

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Option({self.name!r}, {self.kind!r}, default={self.default!r})"


class Match:
    """The result of matching typed text against a command's options."""

    __slots__ = ("status", "option", "value", "message")

    def __init__(self, status, option=None, value=None, message=""):
        self.status = status
        self.option = option
        self.value = value
        self.message = message

    def __repr__(self):  # pragma: no cover - debugging aid
        name = self.option.name if self.option else None
        return f"Match({self.status!r}, {name!r}, value={self.value!r})"


# ---------------------------------------------------------------------------
# value parsing
# ---------------------------------------------------------------------------

_TRUE = ("yes", "y", "on", "true", "1")
_FALSE = ("no", "n", "off", "false", "0")


def parse_bool(text):
    """the reference CAD application accepts Yes/No, On/Off, True/False and 1/0. None if unrecognised."""
    t = str(text).strip().lower()
    if t in _TRUE:
        return True
    if t in _FALSE:
        return False
    return None


def format_bool(value):
    """Render a toggle the way the reference CAD application prints it."""
    return "Yes" if value else "No"


def _coerce(option, raw):
    """Turn typed text into the option's value. Returns (value, error_or_None)."""
    text = str(raw).strip()
    if option.kind == TOGGLE:
        val = parse_bool(text)
        if val is None:
            return None, f"{option.name} expects Yes or No (got {text!r})"
        return val, None
    if option.kind == ENUM:
        for choice in option.choices:
            if choice.lower() == text.lower():
                return choice, None
        # a unique prefix is good enough for an enum value too
        hits = [c for c in option.choices if c.lower().startswith(text.lower())]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return None, (f"{option.name}={text!r} is ambiguous "
                          f"({', '.join(hits)})")
        return None, (f"{option.name} must be one of "
                      f"{', '.join(option.choices)} (got {text!r})")
    if option.kind == VALUE:
        if option.value_kind == "number":
            try:
                return float(text), None
            except ValueError:
                return None, f"{option.name} expects a number (got {text!r})"
        return text, None
    return None, f"{option.name} takes no value"


def coerce(option, raw):
    """Turn typed text into the option's value. Returns (value, error_or_None).

    Public because a command needs it when the user typed a bare ENUM/VALUE
    option name and then supplied the value on the following line.
    """
    return _coerce(option, raw)


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------

def match(text, options, values=None):
    """Match one line of typed input against `options`.

    Returns a :class:`Match`. ``status == NONE`` means "this is not an option",
    which is the signal to treat the text as a normal step argument -- so a
    command with options can still take ordinary input like ``5`` or ``0,0,0``.

    `values` is the command's current option values, needed to flip a TOGGLE.
    """
    if not options:
        return Match(NONE)
    raw = str(text).strip()
    if not raw:
        return Match(NONE)

    # inline form: Name=Value
    name_part, sep, value_part = raw.partition("=")
    name_part = name_part.strip()
    value_part = value_part.strip()
    if not name_part:
        return Match(NONE)

    # exact (case-insensitive) name beats the activation letter, which beats
    # prefix matching
    hits = [o for o in options if o.name.lower() == name_part.lower()]
    if not hits and len(name_part) == 1:
        # the auto-assigned activation letter (see assign_keys) -- the letter
        # the command bar highlights, so the input the user sees is the input
        # that works
        keys = assign_keys(options)
        hits = [o for o in options
                if (keys.get(o.name) or "").lower() == name_part.lower()]
    if not hits and not name_part.isdigit():
        # a pure-digit input is NEVER an option: digits are the number path
        # (coordinates/distances/angles at point prompts) and option letters
        # are always alphabetic. Typing the full word "3point" still matches.
        hits = [o for o in options if o.name.lower().startswith(name_part.lower())]
    if not hits:
        return Match(NONE)
    if len(hits) > 1:
        names = ", ".join(o.name for o in hits)
        return Match(AMBIGUOUS, message=f"{name_part!r} is ambiguous ({names})")

    option = hits[0]

    if option.kind == MODE:
        if sep:
            return Match(INVALID, option,
                         message=f"{option.name} does not take a value")
        return Match(BRANCH, option)

    if sep:
        value, err = _coerce(option, value_part)
        if err:
            return Match(INVALID, option, message=err)
        return Match(SET, option, value)

    # bare name
    if option.kind == TOGGLE:
        current = (values or {}).get(option.name, option.default)
        return Match(SET, option, not bool(current))
    # ENUM / VALUE need a value, so the caller should prompt for one
    return Match(NEEDS_VALUE, option)


# ---------------------------------------------------------------------------
# prompt rendering
# ---------------------------------------------------------------------------

def format_option(option, values=None):
    """One option as it appears in the prompt.

    MODE options print bare (``Diagonal``); the others print ``Name=Value``,
    which is how the reference CAD application shows their current setting.
    """
    if option.kind == MODE:
        return option.name
    value = (values or {}).get(option.name, option.default)
    if value is None:
        return option.name
    if option.kind == TOGGLE:
        return f"{option.name}={format_bool(value)}"
    if isinstance(value, float):
        return f"{option.name}={value:g}"
    return f"{option.name}={value}"


def format_prompt(base, options, values=None):
    """Render a CAD-style prompt: ``Corner 1 ( Diagonal  Cube  Trim=Yes ):``

    The options list goes *inside* brackets before the trailing colon, which is
    where the reference CAD application puts it. A prompt with no options is returned untouched, so
    adding this framework cannot change how an option-less command looks.
    """
    base = (base or "").strip()
    if not options:
        return base
    rendered = "  ".join(format_option(o, values) for o in options)
    if base.endswith(":"):
        return f"{base[:-1].rstrip()} ( {rendered} ):"
    return f"{base} ( {rendered} )"


def prompt_segments(base, options, values=None):
    """The same prompt as :func:`format_prompt`, split into clickable pieces.

    Returns a list of ``(text, option_name_or_None)``. Segments whose second item
    is a name are the ones the user can **click** in the command bar; the rest is
    surrounding punctuation (``( ``, ``  ``, `` ):``) and the base prompt.

    Why this exists: the reference CAD application's command-line options are buttons, not decoration --
    you click ``2Point`` instead of typing it. To hit-test a click we need to know
    where each option's text starts and ends, and the only safe way to know that
    is to build the string out of labelled pieces instead of trying to re-parse
    the finished line.

    **Invariant:** ``"".join(text for text, _ in prompt_segments(...))`` equals
    ``format_prompt(...)`` exactly. That is what guarantees the hit-boxes line up
    with the pixels actually drawn.
    """
    base = (base or "").strip()
    if not options:
        return [(base, None)] if base else []
    # mirror format_prompt's bracket placement exactly
    if base.endswith(":"):
        head, tail = f"{base[:-1].rstrip()} ( ", " ):"
    else:
        head, tail = f"{base} ( ", " )"
    segments = [(head, None)]
    for i, option in enumerate(options):
        if i:
            segments.append(("  ", None))      # the separator format_prompt uses
        segments.append((format_option(option, values), option.name))
    segments.append((tail, None))
    return segments


def assign_keys(options):
    """Auto-assign each option its activation letter: a ``name -> letter`` map.

    The options are walked in display order and each one CLAIMS the first
    alphabetic character of its name that no earlier option has already
    claimed. Digits are never claimed: a typed digit is a coordinate, distance
    or angle at a point prompt, so option letters must always be letters and
    the highlighted letter can never collide with the number path.

    This is why ``2Point`` gets ``P`` and ``3Point`` gets ``O``: P is already
    taken when 3Point is reached, so it takes the next free letter in the
    word. The same rule gives ``Deformable`` -> D and ``Diameter`` -> I.

    The map is computed PER OPTION SET, because a letter's availability
    depends on its siblings. The matcher and the command-bar renderer both
    derive their behaviour from this one function over the same set, so the
    letter the bar highlights is always the letter that works.

    An option whose letters are all claimed maps to ``None``: it stays
    activatable by typing its name, it just has no highlighted letter.
    """
    keys = {}
    claimed = set()
    for opt in options or ():
        key = None
        for ch in opt.name:
            if ch.isalpha() and ch.lower() not in claimed:
                key = ch
                claimed.add(ch.lower())
                break
        keys[opt.name] = key
    return keys


def key_split(text, key):
    """Split a segment's text at its activation letter: ``(before, letter, after)``.

    The split lands on the FIRST case-insensitive occurrence of ``key`` in
    ``text`` -- for a first-letter key that is position 0, for a claimed-later
    key (the O in "3Point") it is mid-word. The renderer draws ``letter`` in
    the accent colour and ``before``/``after`` in the ordinary option colour;
    the three parts always join back to ``text`` byte-for-byte.

    Returns ``None`` when ``key`` is empty or absent from ``text`` (the
    degenerate all-letters-claimed case): the caller then draws the whole
    segment in the ordinary colour.
    """
    text = str(text)
    if not key:
        return None
    i = text.lower().find(str(key).lower())
    if i < 0:
        return None
    return (text[:i], text[i], text[i + 1:])


def value_prompt(option):
    """The sub-prompt shown when a bare ENUM/VALUE option needs a value."""
    if option.kind == ENUM:
        return f"{option.name} ( {'  '.join(option.choices)} ):"
    return f"{option.name}:"


def value_prompt_segments(option):
    """Split :func:`value_prompt` into clickable ENUM choice segments.

    Each ENUM choice is labelled with its own value so the command bar can
    hit-test it. VALUE prompts remain one unlabelled segment. Concatenating the
    segment text always reconstructs :func:`value_prompt` byte-for-byte.
    """
    if option.kind != ENUM:
        return [(value_prompt(option), None)]

    segments = [(f"{option.name} ( ", None)]
    for i, choice in enumerate(option.choices):
        if i:
            segments.append(("  ", None))
        segments.append((choice, choice))
    segments.append((" ):", None))
    return segments


def defaults(options):
    """Starting value dict for a command's options (MODE options hold none)."""
    return {o.name: o.default for o in (options or ()) if o.kind != MODE}


def names(options):
    """Option names used by autocomplete and compatibility checks."""
    return [o.name for o in (options or ())]
