"""Native-only command-macro tokenizer.

Rust owns quote/comment splitting, token classification and prefix policy.
Python only loads the checked ABI and converts its caller-owned output into
host dictionaries/tuples. Missing/stale/refused native execution is explicit;
there is no Python tokenizer or size-based Python bypass.
"""
import importlib.util
import json
from pathlib import Path
import sys

__all__ = ["tokenize", "MacroError", "SPECIAL_COMMANDS"]
SPECIAL_COMMANDS = frozenset({"pause", "multipause", "enter", "enterend",
    "noecho", "echo", "setredrawoff", "setredrawon", "run"})


class MacroError(ValueError):
    """A non-string command macro."""


def _native_macro():
    if __package__:
        from . import native_pure
        return native_pure
    name = "narwhal3d_native_pure"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("native_pure.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return sys.modules[name]


native_pure = _native_macro()


def _steps_from_json(payload):
    """Restore tuple fields after the checked native JSON boundary."""
    steps = json.loads(payload)
    for step in steps:
        if "prefixes" in step:
            step["prefixes"] = tuple(step["prefixes"])
    return steps


_PACKED_CANCEL = 0
_PACKED_COMMAND = 1
_PACKED_PAUSE = 2
_PACKED_ENTER = 3
_PACKED_TEXT = 4
_PACKED_OPTION = 5

_PACKED_DASH = 1 << 0
_PACKED_UNDERSCORE = 1 << 1
_PACKED_TRANSPARENT = 1 << 2
_PACKED_HISTORY_LOCK_OFF = 1 << 3
_PACKED_REPEAT = 1 << 4
_PACKED_SUPPRESS_OPTIONS = 1 << 5
_PACKED_HISTORY_ON = 1 << 6
_PACKED_HISTORY_OFF = 1 << 7


def _steps_from_packed(packed, lossless):
    """Convert a native packed step list into the Python step contract.

    ``packed`` is the ``(kinds, flags, s0, s1, s2, strings, n_steps, n_bytes)``
    tuple from ``native_pure._native_macro_packed``: per-step ``kind`` / flag
    bitmasks, three ``(offset << 32) | length`` fields per step, and the shared
    ``strings`` byte buffer they all index into. No JSON is involved.

    ``lossless`` must be the same flag the native call was made with, because
    the lossless-only fields (``prefixes``, and ``special`` on pause / enter)
    are *absent* in the compact contract but *present* (possibly an empty
    tuple / empty string) in the lossless one -- both pack to a ``0`` field, so
    they cannot be told apart from the data alone. Passing ``lossless`` lets the
    converter emit exactly the key set the Python reference would, so the result
    is byte-for-byte the Python output in either mode.
    """
    kinds, flags, s0, s1, s2, strings, n_steps, _n_bytes = packed
    str_buf = bytes(strings)

    def field(arr, i):
        pair = arr[i]
        if pair == 0:
            return ""
        off = pair >> 32
        ln = pair & 0xFFFFFFFF
        return str_buf[off:off + ln].decode("utf-8")

    steps = []
    for i in range(n_steps):
        kind = kinds[i]
        f = flags[i]
        if kind == _PACKED_CANCEL:
            steps.append({"kind": "cancel"})
        elif kind == _PACKED_COMMAND:
            step = {"kind": "command", "name": field(s0, i),
                    "dash": bool(f & _PACKED_DASH),
                    "underscore": bool(f & _PACKED_UNDERSCORE),
                    "transparent": bool(f & _PACKED_TRANSPARENT)}
            if f & _PACKED_HISTORY_LOCK_OFF:
                step["history_lock_off"] = True
            if f & _PACKED_REPEAT:
                step["repeat"] = True
            if f & _PACKED_SUPPRESS_OPTIONS:
                step["suppress_options"] = True
            if f & _PACKED_HISTORY_ON:
                step["history_on"] = True
            if f & _PACKED_HISTORY_OFF:
                step["history_off"] = True
            if lossless:
                step["prefixes"] = tuple(field(s2, i))
            steps.append(step)
        elif kind == _PACKED_PAUSE:
            step = {"kind": "pause"}
            if lossless:
                step["special"] = field(s0, i)
                step["prefixes"] = tuple(field(s1, i))
            steps.append(step)
        elif kind == _PACKED_ENTER:
            step = {"kind": "enter"}
            if lossless:
                step["special"] = field(s0, i)
                step["prefixes"] = tuple(field(s1, i))
            steps.append(step)
        elif kind == _PACKED_TEXT:
            steps.append({"kind": "text", "value": field(s0, i)})
        else:  # _PACKED_OPTION
            step = {"kind": "option", "name": field(s0, i),
                    "value": field(s1, i)}
            if lossless:
                step["prefixes"] = tuple(field(s2, i))
            steps.append(step)
    return steps


def tokenize(macro, *, lossless=False):
    """Tokenize every supported macro through Rust, with no Python replay."""
    if not isinstance(macro, str):
        raise MacroError("a macro is a string; got %s" % type(macro).__name__)
    try:
        payload = native_pure._native_macro(macro, lossless)
    except (UnicodeError, AttributeError) as error:
        raise RuntimeError("native macro tokenizer unavailable or input is not valid UTF-8") from error
    if payload is None:
        raise RuntimeError("native macro tokenizer unavailable, stale, refused input (1 MiB limit), or invalid report; no Python fallback")
    return _steps_from_json(payload)
