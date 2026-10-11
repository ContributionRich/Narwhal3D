"""Pure command-bar logic: layout geometry and command matching. **No `bpy`.**

Why this module exists
----------------------
Draw handlers never fire under ``blender -b``, so anything computed inside
``draw_command_bar`` is invisible to the headless suites. That is how the
prompt-off-screen bug survived: the layout arithmetic lived in the draw handler,
where no test could reach it.

So the arithmetic lives here, in plain Python, and ``ui.py`` only positions text
with the numbers this module returns. Decidable logic stays outside the draw path
so it can be exercised without a Blender process.

Keep this module free of ``bpy`` **and** of package-relative imports, so the
tests can load it by file path (``narwhal3d/__init__.py`` imports ``ui``, which
imports ``bpy``, so ``import narwhal3d.cmdbar`` cannot work in plain Python).
"""
from __future__ import annotations

# Defaults mirrored by ui.draw_command_bar. Kept here so the tests assert on the
# same numbers the renderer uses.
LINE_H = 16
PAD_X = 8
PAD_Y = 4


def command_bar_layout(height: int, n_history: int,
                       line_h: int = LINE_H, pad_y: int = PAD_Y):
    """Where to draw the prompt and the visible history lines.

    Returns ``(prompt_y, history_ys, max_history)`` in region coordinates
    (Blender's y grows **upward**, so the bottom of the region is y=0).

    The prompt is **pinned to the bottom line** and history scrolls upward above
    it, which is both what Rhino does and what makes the prompt impossible to
    push off-screen.

    The bug this replaces: the old code let history take *every* line that fit
    (``max_lines = (h - 2*pad_y) // line_h``) and then drew the prompt one line
    *below* the last history line. Once history filled the bar the prompt landed
    at a negative y and vanished -- the input line "kept getting pushed down and
    out of view". The fix is to reserve the bottom line for the prompt, so
    ``max_history`` is one **less** than the number of lines that fit.

    ``history_ys`` is ordered to match ``history[-len(history_ys):]`` -- i.e.
    oldest first, at the **highest** y, with the newest line immediately above
    the prompt. It is empty when the bar is too short for any history.
    """
    prompt_y = pad_y
    usable = max(0, height - pad_y * 2)
    # -1 reserves the bottom line for the prompt. This is the whole fix.
    max_history = max(0, usable // max(1, line_h) - 1)
    shown = min(max(0, n_history), max_history)
    # NOTE: callers must slice with `history[-shown:] if shown else []` --
    # `history[-0:]` is `history[0:]`, i.e. the WHOLE list, not none of it.
    history_ys = [prompt_y + line_h * (shown - i) for i in range(shown)]
    return prompt_y, history_ys, max_history


def visible_history(history, max_history: int):
    """The trailing ``max_history`` entries, guarding the ``[-0:]`` trap."""
    if max_history <= 0:
        return []
    return list(history[-max_history:])


def match_commands(prefix: str, names):
    """Command names matching ``prefix``, Rhino-style (case-insensitive prefix).

    Ordered so the best candidate is first: exact match, then case-sensitive
    prefix matches, then the rest -- each group alphabetically. Returns [] for an
    empty prefix (Rhino only autocompletes once you have typed something).

    This is Rhino's behaviour with **fuzzy autocomplete turned OFF**
    (options/appearance.htm: "When disabled, autocomplete uses the first letters
    of the command name in alphabetical order."). It is left exactly as it was on
    purpose -- fuzzy_matches() builds the fuzzy-ON behaviour on top of it, using
    this as its first (exact-prefix) list.
    """
    if not prefix:
        return []
    low = prefix.lower()
    hits = [n for n in names if n.lower().startswith(low)]

    def rank(name: str):
        if name.lower() == low:
            return (0, name.lower())
        if name.startswith(prefix):
            return (1, name.lower())
        return (2, name.lower())

    return sorted(hits, key=rank)


# ---------------------------------------------------------------------------
# fuzzy autocomplete
#
# Rhino's fuzzy autocomplete, MEASURED from options/appearance.htm ("Use fuzzy
# autocomplete") and user_interface/rhino_window.htm:
#
#   "in-line autocomplete suggests the most used candidate. For example, LI most
#    likely autocompletes to Line rather than something like LimitReferenceModel,
#    and the autocomplete menu contains best partial and inexact matches. The
#    autocomplete list consists of two command lists divided by a blank line. The
#    first list contains the exact matches that start with the string you typed.
#    The second list contains the non-exact matches sorted by how closely they
#    match the typed string."
#
# So the list is TWO groups: (1) exact prefix matches -- exactly what
# match_commands already returns -- and (2) everything else that fuzzy-matches,
# ranked by closeness, with a blank-line divider between them.
#
# The help does NOT define "how closely they match", nor "most used" without
# usage data. Both are therefore OURS, each kept in one place so it can be
# swapped out when real usage data exists:
#   * _fuzzy_score    -- the closeness metric (OURS).
#   * _fuzzy_tiebreak -- the tie-break: most-used first when usage is given,
#                        else shorter name then alphabetical (OURS).
# ---------------------------------------------------------------------------

def _fuzzy_positions(typed_low, name_low):
    """Greedy left-to-right subsequence match positions, or ``None``.

    Returns the index in ``name_low`` of each character of ``typed_low`` matched
    in order, or ``None`` if ``typed_low`` is not a subsequence of ``name_low``.
    'crv' in 'curve' -> [0, 2, 3]; 'xyz' in 'curve' -> None.
    """
    positions = []
    start = 0
    for c in typed_low:
        idx = name_low.find(c, start)
        if idx < 0:
            return None
        positions.append(idx)
        start = idx + 1
    return positions


def _fuzzy_score(typed_low, name_low):
    """Closeness of a fuzzy (subsequence) match; **OURS**, smaller is closer.

    Not defined by Rhino ("sorted by how closely they match" -- no algorithm
    given), so this is our metric, deliberately isolated in one function. A match
    is "closer" when it starts nearer the front of the name and spans fewer
    characters (fewer gaps between the typed letters). Returns ``None`` when
    ``typed_low`` is not a subsequence of ``name_low``.
    """
    positions = _fuzzy_positions(typed_low, name_low)
    if positions is None:
        return None
    first = positions[0]
    span = positions[-1] - positions[0] + 1
    return (first, span)


def _fuzzy_tiebreak(name, usage):
    """Tie-break key for equally-close matches; **OURS**.

    When ``usage`` (a name -> count mapping) is given, the most-used command
    wins -- Rhino's "suggests the most used candidate", the only part of it we
    can honour, and only when the caller has usage data. When ``usage`` is
    ``None`` we have no usage data, so we fall back to **shorter name, then
    alphabetical** -- which is why 'LI' prefers 'Line' over 'LimitReferenceModel'
    among fuzzy matches. Both halves are OURS; neither is claimed as Rhino's.
    """
    used = usage.get(name, 0) if usage else 0
    return (-used, len(name), name.lower())


def fuzzy_matches(typed: str, names, usage=None):
    """Rhino's two fuzzy lists as a pair ``(exact_prefix, inexact)``.

    ``exact_prefix`` is exactly what match_commands returns -- the names that
    start with ``typed`` (case-insensitive). ``inexact`` is every OTHER name that
    fuzzy-matches (``typed``'s letters appear in order in the name), sorted by
    closeness (_fuzzy_score) then _fuzzy_tiebreak. A name in the first list never
    appears in the second. Empty ``typed`` -> ``([], [])``.
    """
    if not typed:
        return ([], [])
    exact_prefix = match_commands(typed, names)
    already = set(exact_prefix)
    low = typed.lower()
    scored = []
    for n in names:
        if n in already:
            continue
        score = _fuzzy_score(low, n.lower())
        if score is not None:
            scored.append((score, _fuzzy_tiebreak(n, usage), n))
    scored.sort(key=lambda t: (t[0], t[1]))
    inexact = [n for _score, _tie, n in scored]
    return (exact_prefix, inexact)


def completion_lists(typed: str, names, usage=None):
    """The flat row list to draw: exact-prefix, the divider, then the inexact.

    ``None`` stands for the **blank-line divider** the help describes ("two
    command lists divided by a blank line") and is present **only** when both
    groups are non-empty -- a lone group needs no divider. Empty ``typed`` ->
    ``[]``. The drawing side in `ui.py` renders a ``None`` row as the blank line.
    """
    exact_prefix, inexact = fuzzy_matches(typed, names, usage)
    if exact_prefix and inexact:
        return list(exact_prefix) + [None] + list(inexact)
    return list(exact_prefix) + list(inexact)


def autocomplete(prefix: str, names, usage=None):
    """The single name Rhino would complete ``prefix`` to inline, or ``None``.

    **Prefix-only, deliberately** -- this returns the best exact-prefix match (the
    head of :func:`match_commands`) and does NOT fall back to a fuzzy match.

    This function also drives ``state.resolve_command`` on Enter. Falling back to
    a fuzzy match here would change command resolution: for example, ``pon`` could
    run ``Point`` instead of resolving the ``POn`` alias. Fuzzy candidates therefore
    remain display-only in :func:`fuzzy_matches` and :func:`completion_lists`.
    """
    hits = match_commands(prefix, names)
    return hits[0] if hits else None


# ---------------------------------------------------------------------------
# hit-testing: the command bar's options and completions are CLICKABLE
#
# Rhino's command-line options are buttons -- you click `2Point` rather than
# typing it -- and its autocomplete list is clickable. These hit boxes make the
# displayed options and candidates interactive rather than inert text.
#
# Turning pixels back into a name needs text WIDTHS, which only Blender's `blf`
# can measure. So the measurer is injected as a callable: the geometry stays
# pure and unit-testable (a fake `measure` of 7px/char is enough to assert the
# arithmetic), while `ui.py` passes the real `blf.dimensions`.
# ---------------------------------------------------------------------------

def option_boxes(segments, measure, x0=None):
    """Clickable x-ranges for a prompt's option segments.

    `segments` is ``options.prompt_segments(...)`` -- ``(text, name_or_None)``
    pairs whose concatenation is exactly the drawn line. `measure(text)` returns
    that text's pixel width. `x0` is where the line starts.

    Returns ``[(name, x_start, x_end), ...]`` for the named segments only, in
    draw order. Walking the segments accumulates the same widths the renderer
    does, so a box cannot drift from the glyphs it covers.
    """
    x0 = PAD_X if x0 is None else x0   # read now: uiscale may have rescaled it
    boxes = []
    x = float(x0)
    for text, name in segments:
        w = float(measure(text))
        if name is not None:
            boxes.append((name, x, x + w))
        x += w
    return boxes


def hit_option(x, boxes):
    """Which option's box contains `x`, or ``None``. Half-open on the right."""
    for name, x0, x1 in boxes or ():
        if x0 <= x < x1:
            return name
    return None


#: how many autocomplete candidates the bar will show at once.
MAX_COMPLETIONS = 5


def completion_rows(matches, prompt_y, line_h=None, height=None, pad_y=None,
                    max_rows=MAX_COMPLETIONS):
    """Where to draw the autocomplete candidates: ``[(name, y), ...]``.

    The list is stacked **directly above the prompt** and grows upward, so the
    candidates sit right next to what is being typed and the prompt itself never
    moves; the input line is pinned and must never be pushed off screen. The first candidate -- the one Enter/Tab would complete to -- is
    nearest the prompt.

    `height` bounds how many rows fit; pass the region height. Rows that would
    overflow the bar are dropped rather than drawn outside it.
    """
    line_h = LINE_H if line_h is None else line_h   # read now: uiscale may have rescaled it
    pad_y = PAD_Y if pad_y is None else pad_y   # read now: uiscale may have rescaled it
    if not matches:
        return []
    room = max_rows
    if height is not None:
        # how many whole lines fit between the prompt line and the top edge
        usable = max(0, int(height) - pad_y - (prompt_y + line_h))
        room = min(room, usable // max(1, line_h))
    rows = []
    for i, name in enumerate(list(matches)[:max(0, room)]):
        rows.append((name, prompt_y + line_h * (i + 1)))
    return rows


def completion_rows_for_bar(matches, height, n_history, line_h=None,
                           pad_y=None, max_rows=MAX_COMPLETIONS):
    """Candidate rows for a bar of `height` holding `n_history` history lines.

    This exists so the prompt line is derived in exactly ONE place. Callers used
    to call :func:`command_bar_layout` themselves and pass ``prompt_y`` in, and
    one of them unpacked the 3-tuple wrongly (``_, prompt_y, _``), binding
    ``prompt_y`` to the **history list**. Every arrow-key press and every Enter on
    a partly-typed name then died with ``TypeError: can only concatenate list
    (not "int") to list`` -- and no headless suite covered it, because the bad
    call lived in the bpy-only ``ui`` module.

    So: give this function the two facts it needs (how tall the bar is, how much
    history it holds) and it derives the rest. There is no ``prompt_y`` parameter
    left to get wrong.
    """
    line_h = LINE_H if line_h is None else line_h   # read now: uiscale may have rescaled it
    pad_y = PAD_Y if pad_y is None else pad_y   # read now: uiscale may have rescaled it
    prompt_y, _history_ys, _max_history = command_bar_layout(
        height, n_history, line_h, pad_y)
    return completion_rows(matches, prompt_y, line_h, height, pad_y, max_rows)


def hit_completion(y, rows, line_h=None):
    """Which candidate row contains `y`, or ``None``.

    A row's band is ``[y, y + line_h)`` -- the same band the glyphs occupy.
    """
    line_h = LINE_H if line_h is None else line_h   # read now: uiscale may have rescaled it
    for name, row_y in rows or ():
        if row_y <= y < row_y + line_h:
            return name
    return None


# ---------------------------------------------------------------------------
# arrow-key navigation of the candidate list
#
# The bar HIGHLIGHTS one candidate, the keyboard MOVES that highlight, and
# Enter/Tab ACCEPTS it. All three must agree on "which row", so the decision
# lives here, pure, instead of being re-derived three ways in ui.py. A stale
# highlight (left over from a longer list) must be clamped into the new one,
# and an empty list must select nothing rather than row 0 of nothing.
# ---------------------------------------------------------------------------

def clamp_completion_index(length, index):
    """Clamp ``index`` into ``[0, length - 1]``, or ``-1`` when ``length`` is 0.

    ``-1`` means "no candidate to point at", so callers can tell an empty list
    apart from a legitimate selection of the first row (index 0).
    """
    if length <= 0:
        return -1
    if index < 0:
        return 0
    if index >= length:
        return length - 1
    return index


def select_completion(rows, index):
    """The candidate at the clamped ``index``, or ``None`` when the list is empty.

    This is the single source of truth for "what Enter/Tab accepts": the bar
    highlights ``index``, the keyboard selects ``index``, and the click handler
    picks the row under the pointer -- and they agree because they all go
    through this one function.
    """
    i = clamp_completion_index(len(rows), index)
    if i < 0:
        return None
    return rows[i][0]
