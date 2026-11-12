"""Independent test-only macro grammar oracle. Never shipped in the application."""
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


def _native_macro():
    return None


def tokenize(macro, *, lossless=False):
    if not isinstance(macro, str):
        raise MacroError("a macro is a string; got %s" % type(macro).__name__)
    steps = []
    for token in _split_tokens(macro):
        steps.extend(_classify(token, lossless=lossless))
    return steps
