"""Command options: required Rust name/key resolution, host values and prompts.

Rust owns ordered key assignment and exact/key/prefix/enum resolution. Python
retains command-owned Option/Match objects, Unicode object-protocol conversion,
value coercion and prompt presentation; these are not alternative resolvers.
"""
from __future__ import annotations

import array
import ctypes
from functools import lru_cache
import os
from pathlib import Path
import sys


@lru_cache(maxsize=4)
def _resolver(path):
    """Bind only the explicit or adjacent packaged core; no Python replay."""
    suffix = ('narwhal_pure_core.dll' if sys.platform == 'win32' else
              'libnarwhal_pure_core.dylib' if sys.platform == 'darwin' else
              'libnarwhal_pure_core.so')
    library_path = Path(path).expanduser() if path else Path(__file__).parent / 'native' / suffix
    try:
        library = ctypes.CDLL(str(library_path))
        abi = library.narwhal_pure_core_options_abi
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        core = library.narwhal_pure_core_abi_version
        core.argtypes, core.restype = [], ctypes.c_uint32
        if core() != 1 or abi() != 1:
            raise RuntimeError('Required Rust option resolver ABI is stale; not replayed')
        call = library.narwhal_pure_core_options_resolve
        point = ctypes.POINTER(ctypes.c_uint32)
        call.argtypes = [point, ctypes.c_size_t, ctypes.c_uint32, point,
                         ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        call.restype = ctypes.c_int32
        prompt = library.narwhal_pure_core_options_prompt
        prompt.argtypes, prompt.restype = call.argtypes, ctypes.c_int32
        return call, prompt
    except (OSError, AttributeError) as error:
        raise RuntimeError('Required Rust option resolver unavailable; not replayed') from error


def _name_words(names):
    words = [len(names)]
    for name in names:
        folded = name.lower()
        if len(name) > 4096 or len(folded) > 4096:
            raise RuntimeError('Rust option name exceeds 4096 codepoints; not replayed')
        words.extend((len(name), *map(ord, name), len(folded), *map(ord, folded)))
        for ch in name:
            folded = ch.lower()
            words.extend((int(ch.isalpha()), len(folded), *map(ord, folded)))
        if len(words) > 1048576:
            raise RuntimeError('Rust option packet exceeds limit; not replayed')
    return tuple(words)


# Cache immutable host Unicode descriptors only, never options or mutable values.
_cached_names = lru_cache(maxsize=32)(_name_words)


def _resolve_names(names, text='', operation=1):
    names = tuple(names)
    if len(names) > 65536 or len(text) > 4096:
        raise RuntimeError('Rust option input exceeds limit; not replayed')
    # Keep the descriptor cache bounded independently of the transport ceiling.
    pack = _cached_names if len(names) <= 256 and sum(map(len, names)) <= 2048 else _name_words
    folded = text.lower()
    words = array.array('I', pack(names))
    words.extend((len(text), int(text.isdigit()), len(folded), *map(ord, folded)))
    if words.itemsize != 4 or len(words) > 1048576 or len(folded) > 4096:
        raise RuntimeError('Rust option packet exceeds limit; not replayed')
    source = (ctypes.c_uint32 * len(words)).from_buffer(words)
    output = (ctypes.c_uint32 * len(names))()
    count = ctypes.c_size_t(len(names) + 1)
    rc = _resolver(os.environ.get('NARWHAL_PURE_CORE'))[0](
        source, len(words), operation, output, len(names), ctypes.byref(count))
    if rc or count.value > len(names):
        raise RuntimeError(f'Invalid Rust option reply ({rc}); not replayed')
    result = list(output[:count.value])
    if operation == 0:
        if count.value != len(names) or any(
                pos != 0xffffffff and pos >= len(name)
                for name, pos in zip(names, result)):
            raise RuntimeError('Invalid Rust option key positions; not replayed')
    elif result != sorted(set(result)) or any(i >= len(names) for i in result):
        raise RuntimeError('Invalid Rust option indices; not replayed')
    return result


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
        indices = _resolve_names(option.choices, text, 2)
        hits = [option.choices[i] for i in indices]
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

    hits = [options[i] for i in _resolve_names(
        (o.name for o in options), name_part)]
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


def _prompt_segments(base, fields, labels, mode=0):
    # Host marshalling only: native owns whitespace, punctuation and field order.
    base = base or ''
    fields = tuple(fields)
    if len(base) > 4096 or len(fields) > 65536:
        raise RuntimeError('Rust option prompt exceeds limit; not replayed')
    words = array.array('I', (len(base), *map(ord, base), len(fields)))
    for field in fields:
        if len(field) > 4096:
            raise RuntimeError('Rust option field exceeds limit; not replayed')
        words.extend((len(field), *map(ord, field)))
        if len(words) > 1048576:
            raise RuntimeError('Rust option prompt exceeds limit; not replayed')
    source = (ctypes.c_uint32 * len(words)).from_buffer(words)
    output = (ctypes.c_uint32 * (len(words) + 6 * len(fields) + 32))()
    count = ctypes.c_size_t(len(output) + 1)
    rc = _resolver(os.environ.get('NARWHAL_PURE_CORE'))[1](
        source, len(words), mode, output, len(output), ctypes.byref(count))
    if rc or not 1 <= count.value <= len(output) or output[0] > 2 * len(fields) + 1:
        raise RuntimeError(f'Invalid Rust option prompt reply ({rc}); not replayed')
    segments, seen, pos = [], [], 1
    for _ in range(output[0]):
        if pos + 2 > count.value:
            raise RuntimeError('Truncated Rust option prompt; not replayed')
        label, length = output[pos:pos + 2]
        pos += 2
        if pos + length > count.value or label != 0xffffffff and label >= len(labels):
            raise RuntimeError('Invalid Rust option prompt span; not replayed')
        codepoints = output[pos:pos + length]
        if any(cp > 0x10ffff for cp in codepoints):
            raise RuntimeError('Invalid Rust option text; not replayed')
        text = ''.join(map(chr, codepoints))
        pos += length
        segments.append((text, None if label == 0xffffffff else labels[label]))
        if label != 0xffffffff:
            seen.append(label)
    if pos != count.value or seen != list(range(len(labels))):
        raise RuntimeError('Invalid Rust option prompt fields; not replayed')
    return segments


def format_prompt(base, options, values=None):
    """Native-assembled CAD prompt, including the trailing-colon convention."""
    return ''.join(text for text, _ in prompt_segments(base, options, values))


def prompt_segments(base, options, values=None):
    """Native-assembled text and original clickable option names."""
    options = tuple(options or ())
    return _prompt_segments(base, [format_option(o, values) for o in options],
                            [o.name for o in options])


def assign_keys(options):
    """Required native assignment; original Option identities stay in the host."""
    names = [o.name for o in (options or ())]
    positions = _resolve_names(names, operation=0)
    return {name: None if pos == 0xffffffff else name[pos]
            for name, pos in zip(names, positions)}


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
    """Host name presentation; ENUM punctuation/order is assembled natively."""
    return ''.join(text for text, _ in value_prompt_segments(option))


def value_prompt_segments(option):
    """Clickable choice labels preserve their original host objects."""
    if option.kind != ENUM:
        return [(f"{option.name}:", None)]
    return _prompt_segments(option.name, option.choices, option.choices, 1)


def defaults(options):
    """Starting value dict for a command's options (MODE options hold none)."""
    return {o.name: o.default for o in (options or ()) if o.kind != MODE}
