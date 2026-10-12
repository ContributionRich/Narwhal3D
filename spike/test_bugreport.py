"""The Bug Report panel's model: typing, wrapping, layout, input, and report files.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_bugreport.py
"""
import importlib
import os
import shutil
import sys
import tempfile
import types
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("narwhal3d")
_pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = _pkg
br = importlib.import_module("narwhal3d.bugreport")

results = []


def check(ok, label, detail=""):
    results.append((bool(ok), label, detail))


st = br.ReportState()
check(br.key(st, "char", "x") is None and st.text == "",
      "with no focus, keys are not the panel's (they stay commands)")
st.focus = True
for ch in "Trim lost":
    br.key(st, "char", ch)
br.key(st, "enter")
br.key(st, "paste", "my\r\nselection\x07")
check(st.text == "Trim lost\nmy\nselection",
      "typing, Enter = new line, paste normalises line ends and drops control chars",
      repr(st.text))
br.key(st, "back")
check(st.text.endswith("selectio"), "Backspace deletes one character")
check(br.key(st, "submit") == "submit", "Ctrl+Enter submits")
check(br.key(st, "esc") == "blur" and not st.focus, "Esc leaves the box")
st.focus = True
st.text = "a" * (br.MAX_CHARS - 1)
br.key(st, "paste", "bcd")
check(len(st.text) == br.MAX_CHARS and "limited" not in st.message,
      "the length cap truncates, not rejects")
br.key(st, "char", "z")
check(len(st.text) == br.MAX_CHARS and "limited" in st.message,
      "and says so once full")

meas = lambda t: 7.0 * len(t)          # noqa: E731
lines = br.wrap("one two three four\n\nverylongwordthatdoesnotfit end", 70, meas)
check(all(meas(ln) <= 70 for ln in lines) and "" in lines,
      "wrap keeps paragraphs and breaks an over-long word", f"{lines}")
check(br.wrap("", 70, meas) == [""], "empty text wraps to one empty line")

lay = br.layout((0, 0, 200, 600))
for cid, r in lay["chips"]:
    check(br.hit(lay, (r[0] + r[2]) / 2, (r[1] + r[3]) / 2) == ("chip", cid),
          f"the {cid} chip is hit at its centre")
b = lay["box"]
st = br.ReportState()
check(br.click(st, lay, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2) == "focus" and st.focus,
      "clicking the text box focuses it")
sub = dict(lay["buttons"])["submit"]
check(br.click(st, lay, (sub[0] + sub[2]) / 2, (sub[1] + sub[3]) / 2) == "submit",
      "the Submit button submits")
check(br.click(st, lay, 1000, 1000) == "blur" and not st.focus,
      "a click off the panel takes the focus away")
check(b[3] > b[1] + 100 and lay["chips"][0][1][1] > b[3] and sub[3] < b[1],
      "layout: chips above the box, buttons below it, box tall")

# The compact panel can be about 160 px wide, where fixed three-column chips truncate
# labels. Category chips therefore wrap and size from their measured text.
check(dict(br.CATEGORIES).get("missing") == "Missing feature" and len(br.CATEGORIES) == 4,
      "a fourth category, Missing feature, beside Bug / Behavior / Idea",
      f"{br.CATEGORIES}")
m7 = lambda t: 7.0 * len(t)            # noqa: E731  (~the panel font at 11 px)
for width in (160, 230, 700):
    lw = br.layout((0, 0, width, 600), measure=m7)
    chips = lw["chips"]
    labels = dict(br.CATEGORIES)
    fits = all(m7(labels[c]) <= (r[2] - r[0]) for c, r in chips)
    inside = all(r[0] >= 0 and r[2] <= width for _c, r in chips)
    overlap = any(not (a[2] <= b_[0] or b_[2] <= a[0] or a[3] <= b_[1] or b_[3] <= a[1])
                  for i, (_c, a) in enumerate(chips) for _d, b_ in chips[i + 1:])
    box_ = lw["box"]
    check(fits and inside and not overlap
          and all(r[1] > box_[3] for _c, r in chips) and box_[3] > box_[1] + 100,
          f"{width} px wide: every chip label fits its chip, nothing overlaps or leaves the "
          f"panel, and the text box sits below all of them",
          f"{[(c, round(r[2] - r[0]), m7(labels[c])) for c, r in chips]}")
    for cid, r in chips:
        if br.hit(lw, (r[0] + r[2]) / 2, (r[1] + r[3]) / 2) != ("chip", cid):
            check(False, f"{width} px: the {cid} chip is hit at its centre")
rows = lambda lw: len({round(r[1]) for _c, r in lw["chips"]})   # noqa: E731
_l160 = br.layout((0, 0, 160, 600), measure=m7)
_right = {}
for _c, r in _l160["chips"]:
    _right[round(r[1])] = max(_right.get(round(r[1]), 0), r[2])
check(all(abs(v - (160 - 6)) < 1e-6 for v in _right.values()),
      "each row of chips fills the panel width (no ragged right edge)", f"{_right}")
check(rows(br.layout((0, 0, 700, 600), measure=m7)) == 1
      and rows(br.layout((0, 0, 160, 600), measure=m7)) > 1,
      "one row when there is room; extra rows only when a label would be cut off")
st = br.ReportState()
mr = dict(br.layout((0, 0, 160, 600), measure=m7)["chips"])["missing"]
check(br.click(st, br.layout((0, 0, 160, 600), measure=m7),
               (mr[0] + mr[2]) / 2, (mr[1] + mr[3]) / 2) == "category:missing"
      and st.category == "missing",
      "clicking Missing feature selects it")
check("## #3 -- Missing feature -- t" in br.format_report(3, "t", "missing", "x", []),
      "and the report file labels it in words")

# --- click to place the caret, drag / Shift to select ---------------------------
m = lambda t: 7.0 * len(t)              # noqa: E731
lb = br.layout((0, 0, 200, 600), measure=m)
bx = lb["box"]
st = br.ReportState(text="hello world\nsecond line")
st.focus = True
view = br.text_view(st, bx, m)
# the start of "world" on line 0: x0 + 6 chars * 7 px, a little below the top
x_w = view["x0"] + 6 * 7 + 1
y0_ = view["top"] - 0.5 * view["line_h"]
check(br.click(st, lb, x_w, y0_, view=view, measure=m) == "focus"
      and br.field_of(st).caret == 6,
      "a click in the text puts the caret where it landed (before 'world'), not at the end",
      f"caret {br.field_of(st).caret}")
br.key(st, "char", "X")
check(st.text == "hello Xworld\nsecond line",
      "and typing inserts THERE", repr(st.text))
view = br.text_view(st, bx, m)
y1_ = view["top"] - 1.5 * view["line_h"]
br.click(st, lb, view["x0"] + 0.2, y1_, view=view, measure=m)
check(br.field_of(st).caret == len("hello Xworld\n"),
      "a click on the SECOND line lands on that line", f"{br.field_of(st).caret}")
br.drag(st, view, view["x0"] + 6 * 7 + 1, y1_, m)
check(br.field_of(st).selected_text() == "second",
      "dragging selects the text it passes over", repr(br.field_of(st).selected_text()))
br.release(st)
check(not br.drag(st, view, view["x0"], y1_, m),
      "and after the release, moving the mouse no longer selects")
br.key(st, "copy")
check(st.extra.get("clipboard") == "second" and "second" in st.text,
      "Ctrl+C hands the selection to the clipboard and keeps it")
br.key(st, "char", "2nd"[0])
br.key(st, "paste", "nd")
check(st.text == "hello Xworld\n2nd line", "typing REPLACES a selection", repr(st.text))
br.key(st, "home")
br.key(st, "end", shift=True)
check(br.field_of(st).selected_text() == "2nd line",
      "Home, then Shift+End selects the line", repr(br.field_of(st).selected_text()))
br.key(st, "cut")
check(st.text == "hello Xworld\n" and st.extra["clipboard"] == "2nd line",
      "Ctrl+X cuts it", repr(st.text))
br.key(st, "left")
br.key(st, "left", shift=True)
br.key(st, "left", shift=True)
check(br.field_of(st).selected_text() == "ld",
      "Shift+Left extends the selection character by character",
      repr(br.field_of(st).selected_text()))
br.key(st, "delete")
check(st.text == "hello Xwor\n", "Delete removes the selection", repr(st.text))
br.key(st, "select_all")
br.key(st, "back")
check(st.text == "", "Ctrl+A then Backspace empties the box")
# up / down move by VISUAL line, keeping the column
st.text = "abcdef ghijkl mnopqr"
st.focus = True
f_ = br.field_of(st)
f_.set_caret(2)
br.key(st, "down", measure=m, width=50)       # 50 px: 7 chars a line
check(f_.caret == 9, "Down moves to the next WRAPPED line at the same column",
      f"caret {f_.caret}")
br.key(st, "up", measure=m, width=50)
check(f_.caret == 2, "and Up comes back", f"caret {f_.caret}")
# the view follows the caret, not the end of the text
st.text = "\n".join(f"line {i}" for i in range(60))
br.field_of(st).set_caret(0)
v_ = br.text_view(st, bx, m)
check(v_["first"] == 0, "with the caret at the top, the top of a long report is shown",
      f"first {v_['first']}")
br.field_of(st).set_caret(len(st.text))
v_ = br.text_view(st, bx, m)
check(v_["first"] + v_["rows"] == 60, "and with it at the end, the end",
      f"{v_['first']}+{v_['rows']}")

# --- screenshot and log are OPTIONAL ---------------------------------------------
st = br.ReportState()
check(st.include_shot and not st.include_log,
      "by default a report carries a screenshot and no log")
chk = dict(lb["checks"])
br.click(st, lb, (chk["shot"][0] + chk["shot"][2]) / 2, (chk["shot"][1] + chk["shot"][3]) / 2)
check(not st.include_shot, "clicking 'Include screenshot' turns it off")
br.click(st, lb, chk["log"][0] + 5, (chk["log"][1] + chk["log"][3]) / 2, log_ok=False)
check(not st.include_log, "the log box cannot be ticked when there is no log")
br.click(st, lb, chk["log"][0] + 5, (chk["log"][1] + chk["log"][3]) / 2)
check(st.include_log, "and can be when there is one")
check(all(r[1] >= lb["buttons"][0][1][3] and r[3] <= lb["box"][1]
          for _c, r in lb["checks"]),
      "the checkboxes sit between the buttons and the text box")
# --- a document copy is explicit opt-in ------------------------------------------
check("file" in chk and not st.include_file,
      "an 'Include current file' checkbox, OFF by default", repr(list(chk)))
br.click(st, lb, chk["file"][0] + 5, (chk["file"][1] + chk["file"][3]) / 2, log_ok=False)
check(st.include_file and st.include_log,
      "clicking it ticks it (even with no log; the log box is untouched)")
br.click(st, lb, chk["file"][0] + 5, (chk["file"][1] + chk["file"][3]) / 2)
check(not st.include_file, "and clicking again unticks it")
_rows = sorted(r for _c, r in lb["checks"])
check(all(a[3] <= b[1] + 1e-9 for a, b in zip(_rows, _rows[1:])),
      "the three checkboxes do not overlap", repr(_rows))
d1 = br.format_report(5, "t", "bug", "x", [], doc_rel=br.file_rel(5))
d2 = br.format_report(6, "t", "bug", "x", [], doc_error="disk full")
d3 = br.format_report(7, "t", "bug", "x", [])
check(br.file_rel(5) == os.path.join("files", "0005.3dm")
      and "- Document file: `files/0005.3dm`" in d1
      and "- Document file: NOT saved -- disk full" in d2
      and "Document file" not in d3,
      "the report names the saved copy -- or says why it was not saved; no line when "
      "none was asked for", d1 + d2)

tmp = tempfile.mkdtemp()
try:
    check(br.next_number(tmp) == 1, "first report is #1")
    e = br.format_report(1, "2026-10-02 09:00:00", "behavior",
                         "after a trim\nit deselects", [
                             ("Command running", "Trim"),
                             ("Recent command line (oldest first)", ["> Trim", "> w9,0,0"]),
                         ], "shots/0001.png")
    path = br.write_report(tmp, e)
    br.write_report(tmp, br.format_report(2, "t", "bug", "x", []))
    text = open(path, encoding="utf-8").read()
    check(text.startswith("# User reports") and text.count("## #") == 2,
          "one file, a neutral public header once, reports appended")
    check("## #1 -- Behavior -- 2026-10-02 09:00:00" in text
          and "> after a trim\n> it deselects" in text,
          "the user's report is quoted verbatim, line by line")
    check("- Command running: Trim" in text and "    > w9,0,0" in text
          and "`shots/0001.png`" in text, "context and the screenshot path are written")
    check(br.next_number(tmp) == 3 and br.count_open(tmp) == 2,
          "numbering continues; open reports are counted")
    with open(path, "w", encoding="utf-8") as fh:
        head, _sep, rest = text.partition("## #1")
        fh.write(head + "## #1" + rest.replace("\n**Status:** open\n",
                                                "\n**Status:** fixed in abc123\n", 1))
    check(br.count_open(tmp) == 1, "marking one report fixed reduces the open count")
    e3 = br.format_report(3, "t", "bug", "x", [], None,
                          log_lines=["Traceback (most recent call last):", "  boom ```"])
    check("**Session log (last 2 lines):**" in e3 and "```\nTraceback" in e3
          and "Screenshot" not in e3 and "boom '''" in e3,
          "a report can carry the session log's tail (fenced; a ``` inside cannot break "
          "the fence) and no screenshot", e3)
    check("Session log" not in br.format_report(4, "t", "bug", "x", []),
          "and no log section when none was asked for")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# --- token-free GitHub submission: sanitized, bounded, and user-reviewed ---------
entry = br.format_report(
    9, "t", "bug", "moving the curve leaves its points behind", [
        ("Document", "/home/alice/private/model.3dm"),
    ], shot_rel="shots/0009.png", doc_rel="files/0009.3dm")
public_body = br.github_body(entry, home="/home/alice")
check("/home/alice" not in public_body and "~/private/model.3dm" in public_body,
      "the GitHub body redacts the local home directory")
check("saved locally; attach it manually" in public_body
      and "attach it only after reviewing" in public_body,
      "local screenshot and document paths become manual attachment instructions")
check("did not upload any screenshot, log, or document automatically" in public_body,
      "the public body states that attachments were not uploaded")
url = br.github_issue_url("bug", "moving the curve leaves its points behind", public_body)
query = parse_qs(urlparse(url).query)
check(url.startswith(br.GITHUB_NEW_ISSUE + "?")
      and query.get("labels") == ["bug"]
      and query.get("title") == ["[Bug] moving the curve leaves its points behind"],
      "the browser URL targets Narwhal Issues with a category title and bug label")
check(query.get("body") == [public_body],
      "a normal sanitized report is fully prefilled in the browser")
long_body = "x" * (br.GITHUB_PREFILL_LIMIT + 500)
long_query = parse_qs(urlparse(br.github_issue_url("idea", "x", long_body)).query)
check(len(long_query["body"][0]) < len(long_body)
      and "full sanitized report is in your clipboard" in long_query["body"][0],
      "an oversized URL body is bounded and directs the user to the complete clipboard copy")

failed = [r for r in results if not r[0]]
for ok, label, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if not ok else ""))
print(f"\n{len(results) - len(failed)}/{len(results)} bug-report checks passed")
sys.exit(1 if failed else 0)
