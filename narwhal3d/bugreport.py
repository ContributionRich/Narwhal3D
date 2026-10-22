"""The report panel's model: text editing, layout, and report-file capture.

Reports are appended to one Markdown file in the configured report directory, newest
last. Each entry is numbered and timestamped and may include application context, a
window image, a session-log excerpt, and a copy of the current document. The UI gathers
those optional resources; this module formats and writes them.

Coordinates for `layout`/`hit` are Blender REGION pixels: origin bottom-left, y up.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import textfield

#: Public category identifiers and labels shown by the report panel.
CATEGORIES = (("bug", "Bug"), ("behavior", "Behavior"), ("idea", "Idea"),
              ("missing", "Missing feature"))
REPORT_FILE = "BUG_REPORTS.md"
MAX_CHARS = 8000                       # keep individual reports bounded
GITHUB_NEW_ISSUE = "https://github.com/ContributionRich/Narwhal3D/issues/new"
GITHUB_PREFILL_LIMIT = 3500


@dataclass
class ReportState:
    text: str = ""
    category: str = "bug"
    focus: bool = False
    message: str = ""
    extra: dict = field(default_factory=dict)
    #: Window capture is enabled by default; session logs remain opt-in.
    include_shot: bool = True
    include_log: bool = False
    #: Document copies are opt-in because model files can be large.
    include_file: bool = False


# ---------------------------------------------------------------------------
# editing -- caret placement and text selection
# ---------------------------------------------------------------------------

def field_of(state):
    """The `textfield.TextField` behind `state.text`, kept in step with it (callers and
    tests may assign `state.text` directly; the field follows, caret at the end)."""
    f = state.extra.get("field")
    if f is None:
        f = textfield.TextField(state.text, multiline=True, max_chars=MAX_CHARS)
        state.extra["field"] = f
    elif f.text != state.text:
        f.set_text(state.text)
    return f


#: key kinds that move the caret (with `shift`, they extend the selection)
_MOTION = ("left", "right", "home", "end", "up", "down")


def key(state, kind, char="", shift=False, measure=None, width=None):
    """A key while the text box has focus. Returns "edited", "moved", "copy", "submit",
    "blur" or None.

    `kind`: "char", "back", "delete", "enter" (a NEW LINE -- reports are paragraphs),
    "submit" (Ctrl+Enter), "paste" (`char` holds the clipboard text), "esc" (leave the
    box), "select_all", "copy" / "cut" (the text for the clipboard is left in
    ``state.extra["clipboard"]``), and the motions left / right / home / end / up / down
    -- `shift` extends the selection. Up/down need the drawing font's `measure` and the
    text `width`, because they move by VISUAL line.
    """
    if not state.focus:
        return None
    if kind == "esc":
        state.focus = False
        return "blur"
    if kind == "submit":
        return "submit"
    f = field_of(state)
    did = "edited"
    if kind == "back":
        f.backspace()
    elif kind == "delete":
        f.delete()
    elif kind == "select_all":
        f.select_all()
        did = "moved"
    elif kind in ("copy", "cut"):
        state.extra["clipboard"] = f.cut() if kind == "cut" else f.selected_text()
        did = "copy" if kind == "copy" else "edited"
    elif kind in _MOTION:
        did = "moved"
        if kind == "left":
            f.move(-1, select=shift)
        elif kind == "right":
            f.move(1, select=shift)
        elif kind == "home":
            f.home(select=shift)
        elif kind == "end":
            f.end(select=shift)
        elif measure is not None and width is not None:
            spans = textfield.wrap_spans(f.text, width, measure)
            _x, line = textfield.caret_xy(spans, f.text, f.caret, measure, 1)
            x = state.extra.get("goal_x")
            if x is None:
                x = _x
            target = line + (-1 if kind == "up" else 1)
            if 0 <= target < len(spans):
                f.set_caret(textfield.index_at(spans, f.text, x, target, measure),
                            select=shift)
            state.extra["goal_x"] = x
            state.text = f.text
            return did
    else:
        add = {"char": char, "enter": "\n", "paste": char}.get(kind)
        if add is None:
            return None
        if not f.insert(add) and add:
            state.message = f"a report is limited to {MAX_CHARS} characters"
    state.extra.pop("goal_x", None)          # a vertical run ends at any other key
    state.text = f.text
    return did


def wrap(text, width, measure):
    """Lines for drawing: paragraphs kept, words wrapped to `width` px, a word longer
    than a line broken by characters. An empty text gives no lines."""
    out = []
    for para in text.split("\n"):
        if not para:
            out.append("")
            continue
        cur = ""
        for word in para.split(" "):
            trial = word if not cur else f"{cur} {word}"
            if measure(trial) <= width:
                cur = trial
                continue
            if cur:
                out.append(cur)
            cur = ""
            while word and measure(word) > width:
                n = len(word)
                while n > 1 and measure(word[:n]) > width:
                    n -= 1
                out.append(word[:n])
                word = word[n:]
            cur = word
        out.append(cur)
    return out


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------

def chip_rows(width, labels, measure, gap, label_pad):
    """Pack chips into rows so that no label is CUT OFF: ``[[(index, x, w), ...], ...]``.

    Each chip uses its label's measured width plus padding so labels are not clipped.
    Chips flow left to right
    and wrap when the next would not fit, then each row's spare width is shared equally so
    the row fills the panel. A label wider than the whole panel gets a row to itself at
    the panel's width -- the floor, rather than vanishing. `x` is relative to the left.
    """
    rows, cur, used = [], [], 0.0
    for i, lb in enumerate(labels):
        w = measure(lb) + 2 * label_pad
        need = w if not cur else used + gap + w
        if cur and need > width:
            rows.append(cur)
            cur, used = [], 0.0
            need = w
        cur.append((i, w))
        used = need
    if cur:
        rows.append(cur)
    out = []
    for row in rows:
        natural = sum(w for _i, w in row) + gap * (len(row) - 1)
        extra = max(0.0, width - natural) / len(row)
        x, placed = 0.0, []
        for i, w in row:
            ww = min(width, w + extra)
            placed.append((i, x, ww))
            x += ww + gap
        out.append(placed)
    return out


#: (id, label) for the optional attachments above the buttons.
CHECKS = (("shot", "Include screenshot"), ("log", "Include session log"),
          ("file", "Include current file"))


def layout(body, scale=1.0, measure=None):
    """``{"title", "chips": [(id, rect)], "box", "checks": [(id, rect)],
    "buttons": [(id, rect)], "status"}`` inside `body` = (x0, y0, x1, y1). Rects are
    (x0, y0, x1, y1).

    `measure(text) -> width` is the drawing font's; without one, an average glyph width
    The category chips wrap onto extra rows when their labels do not fit
    (`chip_rows`), and the text box gives up the height they take."""
    s = max(0.5, float(scale))
    if measure is None:
        measure = lambda t: 6.5 * s * len(t)         # noqa: E731
    x0, y0, x1, y1 = body
    pad = 6 * s
    gap = 4 * s
    title = (x0 + pad, y1 - 22 * s, x1 - pad, y1 - 4 * s)
    chip_h = 20 * s
    cy1 = title[1] - 4 * s
    inner = x1 - x0 - 2 * pad
    chips = []
    for r, row in enumerate(chip_rows(inner, [lb for _c, lb in CATEGORIES], measure,
                                      gap, 4 * s)):
        top = cy1 - r * (chip_h + gap)
        for i, x, w in row:
            chips.append((CATEGORIES[i][0], (x0 + pad + x, top - chip_h,
                                             x0 + pad + x + w, top)))
    chips_bottom = min(r[1] for _c, r in chips)
    status = (x0 + pad, y0 + pad, x1 - pad, y0 + pad + 16 * s)
    btn_h = 24 * s
    by0 = status[3] + 4 * s
    bw = (x1 - x0 - 2 * pad - 4 * s) / 2
    buttons = [("submit", (x0 + pad, by0, x0 + pad + bw, by0 + btn_h)),
               ("clear", (x1 - pad - bw, by0, x1 - pad, by0 + btn_h))]
    check_h = 18 * s
    checks = []
    cy = by0 + btn_h + 4 * s
    for cid, _l in reversed(CHECKS):              # listed top-down, laid out bottom-up
        checks.append((cid, (x0 + pad, cy, x1 - pad, cy + check_h)))
        cy += check_h
    checks.reverse()
    box = (x0 + pad, cy + 4 * s, x1 - pad, chips_bottom - 6 * s)
    return {"title": title, "chips": chips, "box": box, "checks": checks,
            "buttons": buttons, "status": status}


#: inner padding of the text box and its line height, at scale 1 (what ui.py draws)
BOX_PAD = 4
LINE_H = 14


def text_view(state, box, measure, scale=1.0):
    """Where every visual line of the report is drawn -- shared by the draw and the
    click, so a click lands on the character that is under it.

    Returns ``{"spans", "first", "rows", "line_h", "x0", "top", "width"}``: wrapped
    `textfield` spans, the first visible one, how many fit, and the geometry. The view
    scrolls to keep the CARET visible (it used to pin the end of the text, which was
    right only while typing could happen nowhere else).
    """
    s = max(0.5, float(scale))
    f = field_of(state)
    width = (box[2] - box[0]) - 2 * BOX_PAD * s
    spans = textfield.wrap_spans(f.text, width, measure)
    line_h = LINE_H * s
    rows = max(1, int(((box[3] - box[1]) - 2 * BOX_PAD * s) // line_h))
    _x, caret_line = textfield.caret_xy(spans, f.text, f.caret, measure, line_h)
    first = int(state.extra.get("scroll", 0))
    first = min(first, caret_line)
    first = max(first, caret_line - rows + 1)
    first = max(0, min(first, max(0, len(spans) - rows)))
    state.extra["scroll"] = first
    return {"spans": spans, "first": first, "rows": rows, "line_h": line_h,
            "x0": box[0] + BOX_PAD * s, "top": box[3] - BOX_PAD * s, "width": width}


def index_at_point(state, view, x, y, measure):
    """The text index under region point (x, y) in the box drawn as `view`."""
    f = field_of(state)
    line = view["first"] + int((view["top"] - y) // view["line_h"])
    return textfield.index_at(view["spans"], f.text, x - view["x0"], line, measure)


def _inside(r, x, y):
    return r[0] <= x < r[2] and r[1] <= y < r[3]


def hit(lay, x, y):
    """``("chip", id)`` / ``("box", None)`` / ``("check", id)`` / ``("button", id)``
    / None."""
    for cid, r in lay["chips"]:
        if _inside(r, x, y):
            return ("chip", cid)
    if _inside(lay["box"], x, y):
        return ("box", None)
    for cid, r in lay.get("checks", ()):
        if _inside(r, x, y):
            return ("check", cid)
    for bid, r in lay["buttons"]:
        if _inside(r, x, y):
            return ("button", bid)
    return None


def click(state, lay, x, y, view=None, measure=None, shift=False, log_ok=True):
    """A click in the panel body. Returns "focus", "category:<id>", "check:<id>",
    "submit", "clear", "blur" (clicked elsewhere in the body) or None (not on the panel).

    In the box, with the drawn `view` and `measure`, the click PLACES THE CARET there
    (Shift extends the selection to it) and arms a drag-select (`drag`). `log_ok` False
    leaves the log checkbox alone -- there is no log to attach.
    """
    h = hit(lay, x, y)
    if h is None:
        if state.focus:
            state.focus = False
            return "blur"
        return None
    kind, ident = h
    if kind == "box":
        state.focus = True
        if view is not None and measure is not None:
            f = field_of(state)
            f.set_caret(index_at_point(state, view, x, y, measure), select=shift)
            state.extra["dragging"] = True
            state.extra.pop("goal_x", None)
        return "focus"
    if kind == "chip":
        state.category = ident
        return f"category:{ident}"
    if kind == "check":
        if ident == "shot":
            state.include_shot = not state.include_shot
        elif ident == "file":
            state.include_file = not state.include_file
        elif log_ok:
            state.include_log = not state.include_log
        return f"check:{ident}"
    if ident == "clear":
        state.text, state.message = "", ""
        field_of(state)
        return "clear"
    return "submit"


def drag(state, view, x, y, measure):
    """Mouse moved with the button down after a click in the box: extend the selection
    to the character under it. Returns True when the selection changed."""
    if not state.extra.get("dragging") or not state.focus:
        return False
    f = field_of(state)
    before = (f.anchor, f.caret)
    f.set_caret(index_at_point(state, view, x, y, measure), select=True)
    return (f.anchor, f.caret) != before


def release(state):
    state.extra.pop("dragging", None)


# ---------------------------------------------------------------------------
# the report file
# ---------------------------------------------------------------------------

def report_dir(root):
    return os.environ.get("NARWHAL3D_REPORT_DIR") or os.path.join(root, "bug_reports")


def next_number(folder):
    """1 + the highest `## #N` heading already in the file (0 reports -> 1)."""
    path = os.path.join(folder, REPORT_FILE)
    if not os.path.exists(path):
        return 1
    with open(path, encoding="utf-8") as fh:
        nums = [int(m) for m in re.findall(r"^## #(\d+)\b", fh.read(), re.M)]
    return max(nums, default=0) + 1


def file_rel(number):
    """Where report `number`'s document copy goes, relative to the report folder."""
    return os.path.join("files", f"{int(number):04d}.3dm")


def format_report(number, when, category, text, context, shot_rel=None, log_lines=None,
                  doc_rel=None, doc_error=None):
    """Format one report as Markdown.

    `context` is ``[(label, value)]``; list values become indented blocks. Optional
    `log_lines` form a fenced session-log excerpt. `doc_rel` names a saved document copy,
    while `doc_error` records why a requested copy could not be saved.
    """
    label = dict(CATEGORIES).get(category, category)
    lines = [f"## #{number} -- {label} -- {when}", "", "**User report:**", ""]
    lines += [f"> {ln}" if ln else ">" for ln in text.strip().split("\n")]
    lines += ["", "**Context (captured automatically):**", ""]
    for k, v in context:
        if isinstance(v, (list, tuple)):
            lines.append(f"- {k}:")
            lines += [f"    {item}" for item in v] or ["    (none)"]
        else:
            lines.append(f"- {k}: {v}")
    if shot_rel:
        lines.append(f"- Screenshot: `{shot_rel}`")
    if doc_rel:
        lines.append(f"- Document file: `{doc_rel}` (a copy, as it was when reported)")
    elif doc_error:
        lines.append(f"- Document file: NOT saved -- {doc_error}")
    if log_lines:
        lines += ["", f"**Session log (last {len(log_lines)} lines):**", "", "```"]
        lines += [ln.replace("```", "'''") for ln in log_lines]
        lines.append("```")
    lines += ["", "**Status:** open", "", "---", ""]
    return "\n".join(lines)


def write_report(folder, entry):
    """Append `entry` to the report file (creating it with a header). Returns its path."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, REPORT_FILE)
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as fh:
        if new:
            fh.write("# User reports\n\nSubmitted from the application's report panel. Newest entries appear last.\n\n---\n\n")
        fh.write(entry)
    return path


def count_open(folder):
    path = os.path.join(folder, REPORT_FILE)
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as fh:
        return len(re.findall(r"^\*\*Status:\*\* open\s*$", fh.read(), re.M))


def github_title(category, text):
    """Build a bounded issue title from the selected category and first report line."""
    label = dict(CATEGORIES).get(category, "Report")
    first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "Report")
    prefix = f"[{label}] "
    return prefix + first[:max(1, 100 - len(prefix))]


def github_body(entry, home=None):
    """Return a privacy-reviewed Markdown body for a user-controlled GitHub submission.

    No attachment is uploaded. Local home paths are shortened, and local screenshot or
    document references become instructions to attach those files manually after review.
    """
    body = str(entry or "")
    home = os.path.abspath(os.path.expanduser(home or "~"))
    if home and home != os.path.sep:
        body = body.replace(home, "~")
    lines = []
    for line in body.splitlines():
        if line.startswith("- Screenshot: `"):
            lines.append("- Screenshot: saved locally; attach it manually if it is safe to share")
        elif line.startswith("- Document file: `"):
            lines.append("- Document file: saved locally; attach it only after reviewing its contents")
        else:
            lines.append(line)
    notice = (
        "<!-- Review this report and remove private information before submitting. "
        "Narwhal did not upload any screenshot, log, or document automatically. -->"
    )
    return notice + "\n\n" + "\n".join(lines).strip() + "\n"


def github_issue_url(category, text, body, limit=GITHUB_PREFILL_LIMIT):
    """Return a prefilled GitHub issue URL; the complete body is copied separately."""
    from urllib.parse import urlencode

    body = str(body or "")
    limit = max(200, int(limit))
    if len(body) > limit:
        body = (body[:limit].rstrip()
                + "\n\n---\nThe full sanitized report is in your clipboard. Paste it here before submitting.\n")
    query = urlencode({
        "title": github_title(category, text),
        "body": body,
        "labels": "bug",
    })
    return f"{GITHUB_NEW_ISSUE}?{query}"
