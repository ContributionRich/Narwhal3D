"""Text-editing model for GPU-drawn text boxes (pure Python, no bpy).

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


class TextField:
    """Editable text with a caret and an optional selection (anchor..caret)."""

    def __init__(self, text="", multiline=False, max_chars=None):
        self.multiline = bool(multiline)
        self.max_chars = max_chars
        self.text = ""
        self.caret = 0
        self.anchor = None
        self.set_text(text)

    # -- selection -----------------------------------------------------------

    def selection(self):
        """(lo, hi) with lo < hi, or None when nothing is selected."""
        if self.anchor is None or self.anchor == self.caret:
            return None
        return (min(self.anchor, self.caret), max(self.anchor, self.caret))

    def selected_text(self):
        sel = self.selection()
        return self.text[sel[0]:sel[1]] if sel else ""

    def select_all(self):
        self.anchor = 0
        self.caret = len(self.text)

    def _delete_selection(self):
        """Remove the selected text; True if there was a selection."""
        sel = self.selection()
        self.anchor = None
        if not sel:
            return False
        lo, hi = sel
        self.text = self.text[:lo] + self.text[hi:]
        self.caret = lo
        return True

    # -- text ----------------------------------------------------------------

    def _normalise(self, s):
        s = str(s).replace("\r\n", "\n").replace("\r", "\n")
        s = "".join(ch for ch in s if ch in "\n\t" or ch.isprintable())
        if not self.multiline:
            s = s.replace("\n", " ").replace("\t", " ")
        return s

    def set_text(self, text):
        """Replace everything; caret to the end, selection cleared.

        Normalised like `insert`, and truncated to `max_chars`."""
        s = self._normalise(text)
        if self.max_chars is not None:
            s = s[:max(0, self.max_chars)]
        self.text = s
        self.caret = len(s)
        self.anchor = None

    def insert(self, s):
        """Type `s` at the caret, replacing any selection.

        Returns True if at least one character was inserted. False means the
        field is unchanged: either it is full (`max_chars`) or `s` held nothing
        insertable (only control characters / empty). A partial fit inserts
        the leading part that fits and returns True."""
        s = self._normalise(s)
        if not s:
            return False
        sel = self.selection()
        if self.max_chars is not None:
            removed = (sel[1] - sel[0]) if sel else 0
            room = self.max_chars - (len(self.text) - removed)
            if room <= 0:
                return False
            s = s[:room]
        self._delete_selection()
        self.text = self.text[:self.caret] + s + self.text[self.caret:]
        self.caret += len(s)
        return True

    def backspace(self):
        """Delete the selection, else the character before the caret."""
        if self._delete_selection() or self.caret == 0:
            return
        self.text = self.text[:self.caret - 1] + self.text[self.caret:]
        self.caret -= 1

    def delete(self):
        """Delete the selection, else the character after the caret."""
        if self._delete_selection() or self.caret >= len(self.text):
            return
        self.text = self.text[:self.caret] + self.text[self.caret + 1:]

    def cut(self):
        """Return the selected text and delete it ("" if nothing selected)."""
        t = self.selected_text()
        if t:
            self._delete_selection()
        return t

    # -- caret movement ------------------------------------------------------

    def set_caret(self, index, select=False):
        """Move the caret to `index` (clamped).

        select=True extends from the existing anchor, or starts a selection at
        the old caret; select=False clears the selection."""
        if select:
            if self.anchor is None:
                self.anchor = self.caret
        else:
            self.anchor = None
        self.caret = _clamp(int(index), 0, len(self.text))

    def move(self, delta, select=False):
        """Left/Right by `delta` characters.

        Without shift and with a selection, Left collapses to its start and
        Right to its end, without moving further (standard editor behaviour)."""
        if not select:
            sel = self.selection()
            if sel and delta:
                self.set_caret(sel[0] if delta < 0 else sel[1])
                return
        self.set_caret(self.caret + delta, select)

    def home(self, select=False):
        """Start of the current logical line (0 in single-line mode)."""
        start = 0
        if self.multiline:
            start = self.text.rfind("\n", 0, self.caret) + 1
        self.set_caret(start, select)

    def end(self, select=False):
        """End of the current logical line (before its \\n)."""
        stop = len(self.text)
        if self.multiline:
            nl = self.text.find("\n", self.caret)
            if nl != -1:
                stop = nl
        self.set_caret(stop, select)


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
