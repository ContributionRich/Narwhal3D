#!/usr/bin/env python3
"""narwhal3d/textfield.py: text-editing and layout model checks.

Run:
  tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_textfield.py

Pure Python, no bpy. Covers the TextField editing keys (insert, backspace,
delete, arrows with/without shift, home/end, select-all, cut, max_chars,
normalisation) and the layout helpers that turn clicks and drags into caret
indices (wrap_spans, caret_xy, index_at, selection_rects).

A NON-uniform measure ('i' 3 px, 'm' 11 px, others 7) is used alongside the
fixed 7 px one, so code that assumes a fixed character width fails here.

Mutation-tested: each of several deliberate breaks in the module was watched
turn this suite RED, then restored (see the commit message / conversation).
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "textfield", os.path.join(ROOT, "narwhal3d", "textfield.py"))
tf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tf)

TF = tf.TextField
FAILS = []
COUNT = [0]


def check(cond, label):
    COUNT[0] += 1
    if cond:
        print("  ok   " + label)
    else:
        FAILS.append(label)
        print("  FAIL " + label)


def M(s):
    return 7.0 * len(s)


def W(s):
    return sum(3.0 if c == "i" else 11.0 if c == "m" else 7.0 for c in s)


def state(f):
    return (f.text, f.caret, f.selection())


# -- insert --------------------------------------------------------------------
print("insert")
f = TF("helo")
f.set_caret(3)
check(f.insert("l") is True and state(f) == ("hello", 4, None),
      "insert at caret mid-text")

f = TF("hello world")
f.set_caret(0)
f.set_caret(5, select=True)
check(f.selection() == (0, 5) and f.selected_text() == "hello",
      "shift-set_caret selects from the old caret")
f.insert("HEY")
check(state(f) == ("HEY world", 3, None), "typing replaces the selection")

f = TF("abc")
f.anchor = f.caret
check(f.selection() is None and f.selected_text() == "",
      "anchor == caret counts as no selection")

# -- normalisation ---------------------------------------------------------------
print("normalisation")
f = TF()
f.insert("a\nb\tc\r\nd")
check(f.text == "a b c d", "single-line: newlines/tabs become spaces")
f = TF(multiline=True)
f.insert("a\r\nb\rc\td")
check(f.text == "a\nb\nc\td", "multi-line: \\r\\n and \\r -> \\n, tab kept")
f = TF(multiline=True)
f.insert("a\x00b\x07c\x1b\x7fd")
check(f.text == "abcd", "control characters dropped")
f = TF("xy")
check(f.insert("\x00\x01") is False and state(f) == ("xy", 2, None),
      "inserting only control chars changes nothing, returns False")
f = TF("a\r\nb\x00", multiline=True)
check(state(f) == ("a\nb", 3, None), "constructor/set_text normalise too")

# -- max_chars ---------------------------------------------------------------------
print("max_chars")
f = TF("abc", max_chars=5)
r = f.insert("xyz")
check(r is True and state(f) == ("abcxy", 5, None),
      "max_chars truncates to what fits, returns True")
r = f.insert("q")
check(r is False and state(f) == ("abcxy", 5, None),
      "full field: insert returns False and changes nothing")
f.set_caret(1)
f.set_caret(2, select=True)
r = f.insert("ZZ")
check(r is True and state(f) == ("aZcxy", 2, None),
      "full field: replacing a selection frees room for its length")
f = TF(max_chars=5)
f.set_text("1234567")
check(state(f) == ("12345", 5, None), "set_text respects max_chars")

# -- backspace / delete -------------------------------------------------------------
print("backspace / delete")
f = TF("abc")
f.backspace()
check(state(f) == ("ab", 2, None), "backspace at end")
f = TF("abc")
f.set_caret(1)
f.backspace()
check(state(f) == ("bc", 0, None), "backspace mid-text removes the char before")
f.backspace()
check(state(f) == ("bc", 0, None), "backspace at 0 is a no-op")
f = TF("abcdef")
f.set_caret(4)
f.set_caret(1, select=True)
f.backspace()
check(state(f) == ("aef", 1, None), "backspace deletes only the selection")

f = TF("abc")
f.set_caret(1)
f.delete()
check(state(f) == ("ac", 1, None), "delete mid-text removes the char after")
f.set_caret(0)
f.delete()
check(state(f) == ("c", 0, None), "delete at 0")
f.set_caret(1)
f.delete()
check(state(f) == ("c", 1, None), "delete at len is a no-op")
f = TF("abcdef")
f.set_caret(1)
f.set_caret(4, select=True)
f.delete()
check(state(f) == ("aef", 1, None), "delete deletes only the selection")

# -- caret movement ------------------------------------------------------------------
print("movement")
f = TF("abcdef")
f.set_caret(-5)
check(f.caret == 0, "set_caret clamps below 0")
f.set_caret(99)
check(f.caret == 6, "set_caret clamps above len")
f.set_caret(3)
f.move(-1)
check(state(f) == ("abcdef", 2, None), "move left")
f.move(1, select=True)
f.move(1, select=True)
check(f.caret == 4 and f.anchor == 2 and f.selection() == (2, 4),
      "shift+right extends from the anchor")
f.set_caret(5, select=True)
check(f.anchor == 2 and f.selection() == (2, 5),
      "set_caret(select) keeps the existing anchor")
f.set_caret(4)
f.set_caret(2, select=True)
f.move(-1)
check(state(f)[1:] == (2, None), "Left with selection collapses to lo (no further)")
f.set_caret(4)
f.set_caret(2, select=True)
f.move(1)
check(state(f)[1:] == (4, None), "Right with selection collapses to hi (no further)")
f.set_caret(2)
f.set_caret(4, select=True)
f.move(-1)
check(state(f)[1:] == (2, None), "Left collapses to lo when caret is at hi")
f.move(-10)
check(f.caret == 0, "move clamps at 0")
f.move(10)
check(f.caret == 6, "move clamps at len")
f.set_caret(3, select=True)
f.set_caret(1)
check(f.selection() is None, "set_caret without select clears the selection")

f = TF("first\nsecond line\nthird", multiline=True)
f.set_caret(9)
f.home()
check(f.caret == 6, "home on 2nd logical line goes after the \\n")
f.end()
check(f.caret == 17 and f.text[17] == "\n", "end on 2nd logical line stops before its \\n")
f.set_caret(9)
f.home(select=True)
check(f.selection() == (6, 9), "shift+home selects to line start")
f.set_caret(9)
f.end(select=True)
check(f.selected_text() == "ond line", "shift+end selects to line end")
f.set_caret(len(f.text))
f.home()
check(f.caret == 18, "home on the last line")
f = TF("one two")
f.set_caret(4)
f.home()
check(f.caret == 0, "single-line home -> 0")
f.end()
check(f.caret == 7, "single-line end -> len")

# -- select all / cut -----------------------------------------------------------------
print("select all / cut")
f = TF("hello\nthere", multiline=True)
check(f.cut() == "" and f.text == "hello\nthere", "cut with no selection is a no-op")
f.select_all()
check(f.selection() == (0, 11), "select_all")
check(f.cut() == "hello\nthere" and state(f) == ("", 0, None), "cut returns and removes")
f = TF("abcdef")
f.set_caret(1)
f.set_caret(3, select=True)
check(f.cut() == "bc" and state(f) == ("adef", 1, None), "cut a middle selection")

# -- wrap_spans ---------------------------------------------------------------------
print("wrap_spans")


def covers(text, spans):
    """Spans in order, gaps are exactly the \\n characters, rebuild == text."""
    if not spans or spans[0][0] != 0 or spans[-1][1] != len(text):
        return False
    out = []
    for i, (s, e) in enumerate(spans):
        if s > e:
            return False
        out.append(text[s:e])
        if i + 1 < len(spans):
            gap = text[e:spans[i + 1][0]]
            if gap not in ("", "\n"):
                return False
            out.append(gap)
    return "".join(out) == text and all("\n" not in text[s:e] for s, e in spans)


samples = [
    "hello world foo bar",
    "a\n\nb",
    "\n",
    "  leading spaces and trailing   ",
    "supercalifragilisticexpialidocious is long",
    "first paragraph here\n\nsecond one, wrapped twice over\nmmmm iiii mmmm",
    "",
]
for text in samples:
    for width in (0, 21, 40, 70, 500):
        for meas, name in ((M, "M"), (W, "W")):
            check(covers(text, tf.wrap_spans(text, width, meas)),
                  "wrap covers every non-\\n index once, in order: %r w=%d %s"
                  % (text[:20], width, name))

check(tf.wrap_spans("", 70, M) == [(0, 0)], "empty text -> [(0, 0)]")
check(tf.wrap_spans("a\n\nb", 70, M) == [(0, 1), (2, 2), (3, 4)],
      "empty paragraph yields an empty span")
check(tf.wrap_spans("\n", 70, M) == [(0, 0), (1, 1)], "lone \\n -> two empty lines")
HW = "hello world foo bar"
check(tf.wrap_spans(HW, 70, M) == [(0, 6), (6, 16), (16, 19)],
      "word wrap at spaces, trailing space stays on its line")
check(tf.wrap_spans("abcdefghijklmnopqrstuvwxy", 70, M) == [(0, 10), (10, 20), (20, 25)],
      "a word longer than a line is broken by characters")
check(tf.wrap_spans("hi abcdefghijklmnop", 70, M) == [(0, 3), (3, 13), (13, 19)],
      "long word after a short one: wraps first, then breaks")
check(tf.wrap_spans("iiiiiiiiii mmmm", 40, W) == [(0, 11), (11, 14), (14, 15)],
      "wrap uses measure(), not a fixed char width")
check(tf.wrap_spans("abc   ", 21, M) == [(0, 6)],
      "trailing spaces do not force a wrap")

# -- caret_xy -------------------------------------------------------------------------
print("caret_xy")
sp = tf.wrap_spans(HW, 70, M)
check(tf.caret_xy(sp, HW, 6, M, 18) == (0.0, 1), "index at a soft wrap -> start of next line")
check(tf.caret_xy(sp, HW, 5, M, 18) == (35.0, 0), "index before the wrapping space")
check(tf.caret_xy(sp, HW, 19, M, 18) == (21.0, 2), "index at text end")
check(tf.caret_xy(sp, HW, 0, M, 18) == (0.0, 0), "index 0")
t = "ab\ncd"
sp2 = tf.wrap_spans(t, 70, M)
check(tf.caret_xy(sp2, t, 2, M, 18) == (14.0, 0), "index at paragraph end stays on its line")
check(tf.caret_xy(sp2, t, 3, M, 18) == (0.0, 1), "index after \\n starts the next line")
t = "a\n\nb"
sp3 = tf.wrap_spans(t, 70, M)
check(tf.caret_xy(sp3, t, 2, M, 18) == (0.0, 1), "caret on an empty paragraph")
t = "mim\nim"
sp4 = tf.wrap_spans(t, 500, W)
check(tf.caret_xy(sp4, t, 2, W, 18) == (14.0, 0), "caret x uses measure (non-uniform)")
check(tf.caret_xy(sp4, t, 6, W, 18) == (14.0, 1), "caret x is measured from the line start")

# -- index_at -------------------------------------------------------------------------
print("index_at")
one = [(0, 3)]
# "mim" boundaries with W: 0, 11, 14, 25 ('i' midpoint at 12.5, 'm' at 5.5 / 19.5)
check(tf.index_at(one, "mim", 12, 0, W) == 1, "click just left of 'i' midpoint -> before it")
check(tf.index_at(one, "mim", 13, 0, W) == 2, "click just right of 'i' midpoint -> after it")
check(tf.index_at(one, "mim", 5, 0, W) == 0, "click just left of 'm' midpoint")
check(tf.index_at(one, "mim", 6, 0, W) == 1, "click just right of 'm' midpoint")
check(tf.index_at(one, "mim", 18, 0, W) == 2, "nearest boundary uses measure, not char count")
check(tf.index_at(one, "mim", -50, 0, W) == 0, "click left of the text -> 0")
check(tf.index_at(one, "mim", 900, 0, W) == 3, "click right of the text -> len")
check(tf.index_at([(0, 5)], "hello", 17, 0, M) == 2, "single-line usage [(0, len)]")
check(tf.index_at([(0, 0)], "", 40, 0, M) == 0, "empty text -> 0")
check(tf.index_at(sp, HW, 1000, 99, M) == 19, "line past the end clamps to the last line")
check(tf.index_at(sp, HW, 0, -5, M) == 0, "negative line clamps to line 0")
check(tf.index_at(sp, HW, 15, 1, M) == 8, "click on a middle line maps into it")
check(tf.index_at(sp, HW, 1000, 0, M) == 5,
      "past the end of a soft-wrapped line -> stays on that line")
x, ln = tf.caret_xy(sp, HW, tf.index_at(sp, HW, 1000, 0, M), M, 18)
check(ln == 0, "index_at and caret_xy agree on which line a click lands")
t = "ab\ncd"
check(tf.index_at(sp2, t, 1000, 0, M) == 2, "past the end of a paragraph line -> before \\n")
t = "a\n\nb"
check(tf.index_at(sp3, t, 50, 1, M) == 2, "click on an empty paragraph line")

# -- selection_rects ---------------------------------------------------------------------
print("selection_rects")
check(tf.selection_rects(sp, HW, None, M) == [], "no selection -> no rects")
check(tf.selection_rects(sp, HW, (2, 18), M) == [(0, 14.0, 42.0), (1, 0.0, 70.0), (2, 0.0, 14.0)],
      "selection spanning 3 visual lines")
check(tf.selection_rects(sp, HW, (7, 9), M) == [(1, 7.0, 21.0)], "selection within one line")
check(tf.selection_rects(sp, HW, (2, 6), M) == [(0, 14.0, 42.0)],
      "selection ending at a soft wrap does not touch the next line")
t = "ab\n\ncd"
sp5 = tf.wrap_spans(t, 70, M)
check(tf.selection_rects(sp5, t, (1, 5), M) == [(0, 7.0, 14.0), (1, 0.0, 0.0), (2, 0.0, 7.0)],
      "selected empty paragraph gets a zero-width rect")
t = "mimi\nmm"
sp6 = tf.wrap_spans(t, 500, W)
check(tf.selection_rects(sp6, t, (1, 6), W) == [(0, 11.0, 28.0), (1, 0.0, 11.0)],
      "rect widths use measure (non-uniform)")

print()
print("%d/%d text-field checks passed" % (COUNT[0] - len(FAILS), COUNT[0]))
sys.exit(1 if FAILS else 0)
