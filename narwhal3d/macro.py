"""the reference CAD application command-macro tokenizer. Pure Python, no bpy.

the reference CAD application's toolbar buttons, command aliases and command files are *macros*: the
command sequence you would type at the command line, written on one or more
lines.  The documented grammar (``information/cad_scripting.htm``, "Writing
Command Macros") is:

  (space)    acts like pressing Enter -- it separates the entries
  "..."      a name / text containing spaces is enclosed in double quotes
  !          cancel: at the start cancels the previous command, elsewhere it
             cancels the macro; may also be attached to a command (``!&_Delete``)
  -          suppress the dialog box (``-Command``)
  _          run the command / option in English (``_Command``)
  '          the next command is a nestable (transparent) command (``'Command``)
  *          repeat the command automatically
  ~          suppress command options (clutter-free feedback)
  #          enable history recording for the following command
  %          disable history recording for the following command
  !&         disable history locking in a command session (``!&_Delete``)
  ;          comment: a ``;`` outside quotes runs to the end of the line, so a
             line beginning with ``;`` is a full-line comment
  ::         layer nesting (appears inside layer names, e.g. ``"A:: B"``)

and the special scripting commands (same page, "Special scripting commands"):

  Pause / MultiPause    stop the macro for user input (one / several selections)
  Enter / EnterEnd      simulate Enter / complete the command
  NoEcho / Echo         turn echoing of the macro to the history window off / on
  SetRedrawOff/On       prevent / restore screen redraw
  Run                   run another application

``tokenize()`` turns a macro string into a list of *steps*, each a small dict.

The tokenizer is accelerated by the native ``pure_core`` command-macro tokenizer
(``Q106-12``): for a valid string the native route runs first and returns the
byte-for-byte same step list (the lossless ``prefixes`` list is converted back
to a tuple). The native route only ever fires on a string (a non-string is
still refused below with ``MacroError``), so the Python path below remains the
complete, unchanged fallback for when the library is absent, predates the
export, rejects the input (over the byte cap), or returns malformed output.

The step kinds form the tokenizer's public contract:

  {"kind": "cancel"}                              for a ``!``
  {"kind": "command", "name": ..., "dash": bool,
   "underscore": bool, "transparent": bool, ...}  for a command word
  {"kind": "pause"}                               for Pause / MultiPause
  {"kind": "enter"}                               for Enter / EnterEnd
  {"kind": "text", "value": ...}                  for a bare value / quoted string
  {"kind": "option", "name": ..., "value": ...}   for a ``Name=Value`` entry

OUR decisions (tagged, because the help and the contract do not line up one-to-
one):

* The help groups ``Pause``/``MultiPause`` and ``Enter``/``EnterEnd`` as sibling
  "special scripting commands", but the step contract has only the two kinds
  ``pause`` and ``enter``.  So ``MultiPause`` maps to ``pause`` and ``EnterEnd``
  maps to ``enter``.
* The remaining special commands (``NoEcho``, ``Echo``, ``SetRedrawOff``,
  ``SetRedrawOn``, ``Run``) are real the reference CAD application commands, so they map to the
  ``command`` kind with their name preserved (case as written).
* The three flags ``dash`` / ``underscore`` / ``transparent`` are ALWAYS present
  on a command step (the contract's shape).  The extra documented prefix flags
  (``!``/``&``/``*``/``~``/``#``/``%``) are recorded only when set, so nothing
  the help documents is lost; a leading ``!`` also emits a ``cancel`` step.
* A bare (unprefixed) word is a value / keyword argument, not a command:
  ``New`` in ``! _-Layer New ...`` is a text step.  Commands in well-formed
  macros carry at least one of the ``-``/``_``/``'`` prefixes (which is why the
  help recommends the English underscore), and a flagged word whose name starts
  with a letter is a command; a flagged word that does not (``_3Point``) or a
  negative number (``-15,0,0``) is a text value kept exactly as written.
"""
import json

__all__ = ["tokenize", "MacroError", "SPECIAL_COMMANDS"]

# Special scripting commands, by the lower-cased name the help documents them.
_PAUSE = frozenset({"pause", "multipause"})
_ENTER = frozenset({"enter", "enterend"})
_COMMAND_SPECIALS = frozenset(
    {"noecho", "echo", "setredrawoff", "setredrawon", "run"})
SPECIAL_COMMANDS = frozenset(_PAUSE | _ENTER | _COMMAND_SPECIALS)

# Documented prefix flag characters.  Each is a prefix on a command word and
# sets one flag on that command's step:
#   -   dash              (suppress the dialog box)          -- the contract's
#   _   underscore        (run in English)                    --   three, always
#   '   transparent       (nestable / view command)           --   present
#   !   cancel            (a leading ``!`` also emits a cancel step)
#   &   history_lock_off  (``!&_Delete`` disables history locking)
#   *   repeat            (repeat the command automatically)
#   ~   suppress_options  (clutter-free command feedback)
#   #   history_on        (enable history recording)
#   %   history_off       (disable history recording)
_FLAG_CHARS = frozenset("-_ '!&*~#%")


class MacroError(ValueError):
    """A macro string that cannot be tokenized (e.g. not a string)."""


def _native_macro():
    """Lazily resolve the native pure-core adapter, or ``None``.

    Identical to the ``coords._native_coords()`` / ``bearing._native_bearing()``
    accessors: returns the ``native_pure`` module when it can be imported
    (production, where this file is ``narwhal3d.macro``) or loaded from this
    directory (spike tests load this module by file path, so ``narwhal3d`` is
    not an importable package there). ``None`` means "use the unchanged Python
    tokenizer" -- the native library is absent, it predates the macro export,
    or the adapter cannot be imported.
    """
    try:
        from . import native_pure as _mod  # production package context
        return _mod
    except ImportError:
        pass
    try:
        import importlib.util as _ilu
        import os as _os
        import sys as _sys
        _canon = "narwhal3d_native_pure"
        if _canon in _sys.modules:
            return _sys.modules[_canon]
        _path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                              "native_pure.py")
        if not _os.path.isfile(_path):
            return None
        _spec = _ilu.spec_from_file_location(_canon, _path)
        _mod = _ilu.module_from_spec(_spec)
        _sys.modules[_canon] = _mod
        _spec.loader.exec_module(_mod)
        return _mod
    except Exception:  # noqa: BLE001 -- any failure degrades to the fallback
        return None


def _split_tokens(macro):
    """Split a macro into raw token strings.

    Double quotes keep a space-separated name as ONE token (the quote
    characters are retained so the caller can tell a quoted value from a bare
    one).  A ``;`` outside quotes starts a comment that runs to the end of the
    current line, so both full-line comments (``; foo``) and trailing comments
    (``foo ; bar``) are dropped.  Any whitespace separates tokens.
    """
    tokens = []
    cur = []
    in_quote = False
    i = 0
    n = len(macro)
    while i < n:
        ch = macro[i]
        if in_quote:
            cur.append(ch)
            if ch == '"':
                in_quote = False
            i += 1
            continue
        if ch == '"':
            in_quote = True
            cur.append(ch)
            i += 1
            continue
        if ch == ";":
            if cur:
                tokens.append("".join(cur))
                cur = []
            while i < n and macro[i] != "\n":
                i += 1
            continue
        if ch.isspace():
            if cur:
                tokens.append("".join(cur))
                cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        tokens.append("".join(cur))
    return tokens


def _is_fully_quoted(token):
    return len(token) >= 2 and token[0] == '"' and token[-1] == '"'


def _strip_flags(token):
    """Split ``token`` into (leading_flag_list, name).

    Leading flag characters are any of the documented prefix characters; the
    scan stops at the first character that is not a flag (the start of the
    command name).  ``_strip_flags("!&_Delete")`` -> (["!","&","_"], "Delete").
    """
    flags = []
    i = 0
    while i < len(token) and token[i] in _FLAG_CHARS:
        flags.append(token[i])
        i += 1
    return flags, token[i:]


def _find_eq_outside_quotes(token):
    in_q = False
    for i, ch in enumerate(token):
        if ch == '"':
            in_q = not in_q
        elif ch == "=" and not in_q:
            return i
    return -1


def _clean_value(part):
    """Normalise an option value: strip surrounding quotes, then prefix flags.

    ``"Shaded"`` -> ``Shaded``; ``_Shaded`` -> ``Shaded``; ``10`` -> ``10``.
    """
    s = part
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return _strip_flags(s)[1]


def _command_step(name, flags):
    step = {"kind": "command", "name": name}
    # the contract's three flags are always present on a command step
    step["dash"] = "-" in flags
    step["underscore"] = "_" in flags
    step["transparent"] = "'" in flags
    # the extra documented flags, present only when set (OURS -- lossless)
    if "&" in flags:
        step["history_lock_off"] = True
    if "*" in flags:
        step["repeat"] = True
    if "~" in flags:
        step["suppress_options"] = True
    if "#" in flags:
        step["history_on"] = True
    if "%" in flags:
        step["history_off"] = True
    return step


def _classify(token, lossless=False):
    """Classify one raw token into a list of step dicts (usually one).

    ``lossless`` is used by the pure execution planner.  The original compact
    token contract intentionally maps the four special scripting commands onto
    two kinds; planner steps additionally retain the spelling and exact prefix
    sequence without changing the established default output.
    """
    # 1. a standalone ``!`` is a cancel (start, middle or end of a macro)
    if token == "!":
        return [{"kind": "cancel"}]
    # 2. a fully quoted string is one text token (a name with spaces)
    if _is_fully_quoted(token):
        return [{"kind": "text", "value": token[1:-1]}]
    # 3. a ``Name=Value`` entry is an option ('=' outside any quotes)
    i = _find_eq_outside_quotes(token)
    if i >= 0:
        option = {"kind": "option",
                  "name": _strip_flags(token[:i])[1],
                  "value": _clean_value(token[i + 1:])}
        if lossless:
            option["prefixes"] = tuple(_strip_flags(token[:i])[0])
        return [option]
    # 4/5. strip the leading prefix flags and classify the name
    flags, name = _strip_flags(token)
    low = name.lower()
    if low in SPECIAL_COMMANDS:
        # the documented special scripting commands
        if low in _PAUSE:
            step = {"kind": "pause"}
            if lossless:
                step["special"] = name
                step["prefixes"] = tuple(flags)
            return _with_cancel(flags, [step])
        if low in _ENTER:
            step = {"kind": "enter"}
            if lossless:
                step["special"] = name
                step["prefixes"] = tuple(flags)
            return _with_cancel(flags, [step])
        # NoEcho / Echo / SetRedrawOff / SetRedrawOn / Run are real commands
        step = _command_step(name, flags)
        if lossless:
            step["prefixes"] = tuple(flags)
        return _with_cancel(flags, [step])
    if flags and name and name[0].isalpha():
        # a flagged word whose name starts with a letter is a command
        step = _command_step(name, flags)
        if lossless:
            step["prefixes"] = tuple(flags)
        return _with_cancel(flags, [step])
    # 6. anything else is a value / keyword argument, kept exactly as written
    return [{"kind": "text", "value": token}]


def _with_cancel(flags, steps):
    """Prepend a cancel step when a leading ``!`` flag was present."""
    if "!" in flags:
        return [{"kind": "cancel"}] + steps
    return steps


def _steps_from_json(payload):
    """Convert the native JSON step list into the Python step contract.

    ``json.loads`` returns the lossless ``prefixes`` as a *list*; the Python
    reference emits a *tuple*, so each step's ``prefixes`` (and any nested one)
    is converted back to a tuple so the result is byte-for-byte the Python
    output. Everything else (kinds, names, values, booleans) round-trips
    exactly through JSON.
    """
    steps = json.loads(payload)
    for step in steps:
        if "prefixes" in step and isinstance(step["prefixes"], list):
            step["prefixes"] = tuple(step["prefixes"])
    return steps


# Packed step kind / flag codes (Q117-04) -- the exact values the Rust
# publisher writes into the ``kinds`` / ``flags`` arrays. See
# ``macro_parse.rs`` for the source of truth; these mirror it so the converter
# below rebuilds the Python contract without any JSON.
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


NATIVE_MACRO_MIN_BYTES = 240

# A complete-route review re-measured performance after strict packed-output
# validation was added. JSON remains a clear win from 240 through 4096 bytes,
# but regresses above that. Packed varied from 0.88x to 0.92x Python around
# 8-17 KiB and fell to 0.91x by 34 KiB, so it has no stable >=10% production
# band. Keep it directly tested but inactive; Python handles the gaps/tail.
NATIVE_MACRO_JSON_MAX_BYTES = 4096
NATIVE_MACRO_PACKED_MIN_BYTES = 2_000_000  # above the 1 MiB adapter input cap


def tokenize(macro, *, lossless=False):
    """Tokenize a the reference CAD application command macro into a list of steps.

    ``lossless=True`` retains command prefixes and the original spelling of
    Pause/MultiPause and Enter/EnterEnd for :mod:`macro_plan`.  The default
    remains the original compact contract, so existing callers are unchanged.

    The measured production win band is 240..4096 UTF-8 bytes, where the
    legacy JSON native route is at least 10% faster. The packed route remains
    directly tested but inactive because its post-validation margin was not a
    stable 10%; below and above the win band Python runs.
    """
    if not isinstance(macro, str):
        raise MacroError("a macro is a string; got %s" % type(macro).__name__)
    nbytes = len(macro.encode("utf-8"))
    if nbytes >= NATIVE_MACRO_MIN_BYTES:
        _nm = _native_macro()
        if _nm is not None:
            # Q117-04: the packed one-pass route (no JSON round-trip) is the
            # production native route at or above the measured packed
            # crossover. A successful packed result is the exact Python step
            # contract.
            if nbytes >= NATIVE_MACRO_PACKED_MIN_BYTES and \
                    _nm._macro_packed_native_active(macro):
                packed = _nm._native_macro_packed(macro, lossless)
                if packed is not None:
                    return _steps_from_packed(packed, lossless)
            # The legacy two-pass JSON ABI: the native route below the packed
            # crossover (and the fallback when the packed call is unavailable
            # or rejected). It stays a complete, unchanged route.
            if nbytes <= NATIVE_MACRO_JSON_MAX_BYTES:
                payload = _nm._native_macro(macro, lossless)
                if payload is not None:
                    return _steps_from_json(payload)
    steps = []
    for raw in _split_tokens(macro):
        steps.extend(_classify(raw, lossless=lossless))
    return steps
