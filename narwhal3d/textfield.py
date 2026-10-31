"""Shared text editor with mandatory persistent Rust ownership (no bpy).

This module provides caret placement by click (`index_at`), selection by
drag or shift+keys (`TextField.anchor` / `select=True`), and the standard
editing keys. It knows nothing about drawing: callers pass a `measure(str) ->
float` pixel-width function (e.g. `blf.dimensions(font, s)[0]`) and draw what
`wrap_spans` / `caret_xy` / `selection_rects` describe.

Used by the multi-line report box (`multiline=True`), the single-line
layer-rename field and the print dialog's numeric fields (`multiline=False`).

Index convention: a caret index `i` sits *before* `text[i]`; 0..len(text).
"""


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


if __package__:
    from .native_textfield import TextField
else:
    # File-path consumers (including the existing pure test harness) have no package.
    import importlib.util as _importlib_util
    import pathlib as _pathlib
    import sys as _sys
    _name = "_narwhal_native_textfield"
    if _name not in _sys.modules:
        _spec = _importlib_util.spec_from_file_location(
            _name, _pathlib.Path(__file__).with_name("native_textfield.py"))
        _module = _importlib_util.module_from_spec(_spec)
        _sys.modules[_name] = _module
        _spec.loader.exec_module(_module)
    TextField = _sys.modules[_name].TextField


# -- layout helpers (multi-line drawing) --------------------------------------

def _wrap_paragraph(text, p0, p1, width, measure, out):
    start = p0
    while True:
        if measure(text[start:p1].rstrip(" ")) <= width:
            out.append((start, p1))
            return
        # OURS safety correction, matching the Rust port's NoProgress refusal.
        # Font measurement stays in Python; an invalid width/metric must not
        # append spans forever after the paragraph has been exhausted.
        if start >= p1:
            raise ValueError("text wrapping cannot make progress with this width/measurement")
        # Word breaks: just after a run of spaces. The fit test ignores the
        # trailing spaces, which stay on the line they end.
        best = None
        for j in range(start + 1, p1):
            if text[j - 1] == " " and text[j] != " ":
                if measure(text[start:j].rstrip(" ")) <= width:
                    best = j
                else:
                    break
        if best is None:
            # A word longer than a line: break by characters, >= 1 per line.
            best = start + 1
            for k in range(start + 2, p1 + 1):
                if measure(text[start:k]) <= width:
                    best = k
                else:
                    break
        out.append((start, best))
        start = best


def wrap_spans(text, width, measure):
    """Visual lines of `text` as (start, end) index spans, wrapped to `width` px.

    `text[start:end]` is what is drawn on a line. Paragraphs split on \\n, and
    the \\n itself belongs to no span. Spans cover every other index exactly
    once, in order. Empty text -> [(0, 0)]; an empty paragraph -> (i, i)."""
    spans = []
    p0 = 0
    while True:
        nl = text.find("\n", p0)
        p1 = len(text) if nl == -1 else nl
        if p1 == p0:
            spans.append((p0, p0))
        else:
            _wrap_paragraph(text, p0, p1, width, measure, spans)
        if nl == -1:
            return spans
        p0 = nl + 1


def caret_xy(spans, text, index, measure, line_height):
    """(x_px, line_number) of the caret at `index`.

    Rule: the caret goes on the LAST line whose start <= index. Consequences:
    an index at a soft wrap (end of line i == start of line i+1) is drawn at
    the start of the next line; an index at a paragraph end (just before a
    \\n) stays at the end of its own line, because the next line starts after
    the \\n. `line_height` is accepted for call-site symmetry but unused: the
    caller multiplies the returned line number by its own line height."""
    index = _clamp(index, 0, len(text))
    line = 0
    for i, (start, _end) in enumerate(spans):
        if start <= index:
            line = i
        else:
            break
    start, end = spans[line]
    return (measure(text[start:min(index, end)]), line)


def index_at(spans, text, x, line, measure):
    """Text index whose boundary is nearest pixel `x` on visual `line`.

    `line` is clamped to the valid range. Ties go to the earlier index. On a
    soft-wrapped line the line's end index is excluded, because `caret_xy`
    draws that index at the start of the NEXT line; clicking past the end of
    such a line therefore lands before its last character (normally the
    wrapping space)."""
    line = _clamp(line, 0, len(spans) - 1)
    start, end = spans[line]
    last = end
    if line + 1 < len(spans) and spans[line + 1][0] == end and end > start:
        last = end - 1
    best, best_d = start, None
    for k in range(start, last + 1):
        d = abs(measure(text[start:k]) - x)
        if best_d is None or d < best_d:
            best, best_d = k, d
    return best


def selection_rects(spans, text, sel, measure):
    """Highlight rectangles: one (line_number, x0, x1) per visual line touched.

    x0/x1 are measured prefix widths within the line. A line whose only
    selected character is its trailing \\n (including an empty paragraph inside
    the selection) gets a zero-width rect, x0 == x1, so the caller can draw a
    minimal marker there. Empty list for sel None."""
    if not sel:
        return []
    lo, hi = sel
    rects = []
    for i, (start, end) in enumerate(spans):
        a, b = max(lo, start), min(hi, end)
        newline_selected = end < len(text) and text[end] == "\n" and lo <= end < hi
        if a < b or newline_selected:
            a = min(a, end)
            b = max(a, b)
            rects.append((i, measure(text[start:a]), measure(text[start:b])))
    return rects
