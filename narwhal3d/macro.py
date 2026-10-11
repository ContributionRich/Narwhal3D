"""Rhino command-macro tokenizer. Pure Python, no bpy.

Rhino's toolbar buttons, command aliases and command files are *macros*: the
command sequence you would type at the command line, written on one or more
lines.  The documented grammar (``information/rhinoscripting.htm``, "Writing
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
  ``SetRedrawOn``, ``Run``) are real Rhino commands, so they map to the
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


def tokenize(macro, *, lossless=False):
    """Tokenize a Rhino command macro into a list of steps.

    ``lossless=True`` retains command prefixes and the original spelling of
    Pause/MultiPause and Enter/EnterEnd for :mod:`macro_plan`.  The default
    remains the original compact contract, so existing callers are unchanged.
    """
    if not isinstance(macro, str):
        raise MacroError("a macro is a string; got %s" % type(macro).__name__)
    steps = []
    for raw in _split_tokens(macro):
        steps.extend(_classify(raw, lossless=lossless))
    return steps
