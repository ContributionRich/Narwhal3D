"""The command bar as an interactive surface. Pure, no Blender.

Three interaction failures are covered here, based on the compatibility target's
published interface behavior:

1. **Options were not clickable.** The interface guide says: *"Click an
   option name, or type the underlined letter of the option, to change the
   behavior."* The bar drew the bracketed options as inert text with no idea where
   each one's glyphs were, so nothing could be clicked.

2. **No autocomplete list.** Same page: *"Press the Enter key or click the command
   name on the auto-complete list. The autocomplete list filters out the possible
   commands based on your input."* `match_commands` existed and was tested, but
   nothing ever drew or hit-tested a list.

3. **A radius could not be PICKED.** commands/circle.htm: *"Pick the center
   location. Pick a radius location or specify an option."* Covered by
   spike/test_distance_step.py, next door.

What is asserted here is the decidable half: the prompt splits into labelled
pieces whose concatenation is byte-identical to the drawn line (so a hit-box can
never drift from its glyphs), and the geometry that maps a click back to a name.
The text measurer is injected, so no Blender process is needed.

Run: tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_cmdbar_hits.py
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "narwhal3d", filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cmdbar = _load("narwhal_cmdbar_test", "cmdbar.py")
opts = _load("narwhal_options_test", "options.py")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", repr(e)))
    return deco


#: a deterministic stand-in for blf.dimensions: every glyph is 7px wide. The real
#: renderer passes the measured width; the arithmetic being tested is identical.
def measure(text):
    return 7 * len(text)


CIRCLE_OPTS = (
    opts.Option("Deformable", opts.TOGGLE, default=False),
    opts.Option("2Point", opts.MODE),
    opts.Option("3Point", opts.MODE),
    opts.Option("Vertical", opts.MODE),
)
SCALE_OPTION = opts.Option(
    "Scale", opts.ENUM, default="No", choices=("No", "1D", "3D"))
VALUE_OPTION = opts.Option("Distance", opts.VALUE, default=1.0)


# ---------------------------------------------------------------------------
# 1. the segments must reconstruct the drawn line EXACTLY
# ---------------------------------------------------------------------------

@check("no bpy was imported")
def _():
    assert "bpy" not in sys.modules


@check("segments rejoin into exactly what format_prompt renders")
def _():
    for base in ("Radius:", "Corner of base:", "Start of line", ""):
        segs = opts.prompt_segments(base, CIRCLE_OPTS, {"Deformable": False})
        joined = "".join(t for t, _ in segs)
        want = opts.format_prompt(base, CIRCLE_OPTS, {"Deformable": False})
        assert joined == want, f"{base!r}: {joined!r} != {want!r}"


@check("a prompt with no options yields one unlabelled segment")
def _():
    segs = opts.prompt_segments("Point 1:", (), None)
    assert segs == [("Point 1:", None)], segs


@check("every declared option appears as exactly one labelled segment")
def _():
    segs = opts.prompt_segments("Radius:", CIRCLE_OPTS, {"Deformable": False})
    named = [n for _, n in segs if n is not None]
    assert named == ["Deformable", "2Point", "3Point", "Vertical"], named


@check("a toggle's label carries its current value (so the box covers it)")
def _():
    segs = opts.prompt_segments("Radius:", CIRCLE_OPTS, {"Deformable": True})
    text = dict((n, t) for t, n in segs if n is not None)["Deformable"]
    assert text == "Deformable=Yes", text


@check("ENUM value segments reconstruct the value prompt byte-identically")
def _():
    segs = opts.value_prompt_segments(SCALE_OPTION)
    joined = "".join(text for text, _ in segs)
    assert joined == opts.value_prompt(SCALE_OPTION) == "Scale ( No  1D  3D ):"


@check("each ENUM choice is individually labelled with its value")
def _():
    segs = opts.value_prompt_segments(SCALE_OPTION)
    labelled = [(text, name) for text, name in segs if name is not None]
    assert labelled == [("No", "No"), ("1D", "1D"), ("3D", "3D")], labelled


@check("Scale value choices produce pinned command-bar hit boxes")
def _():
    segs = opts.value_prompt_segments(SCALE_OPTION)
    boxes = cmdbar.option_boxes(segs, measure, x0=8)
    assert boxes == [
        ("No", 64.0, 78.0),
        ("1D", 92.0, 106.0),
        ("3D", 120.0, 134.0),
    ], boxes
    for name, x0, x1 in boxes:
        assert cmdbar.hit_option((x0 + x1) / 2.0, boxes) == name


@check("VALUE prompt stays one unlabelled segment")
def _():
    segs = opts.value_prompt_segments(VALUE_OPTION)
    assert segs == [(opts.value_prompt(VALUE_OPTION), None)] == [("Distance:", None)]


# ---------------------------------------------------------------------------
# 2. option hit-boxes
# ---------------------------------------------------------------------------

@check("each option gets a box, in draw order")
def _():
    segs = opts.prompt_segments("Radius:", CIRCLE_OPTS, {"Deformable": False})
    boxes = cmdbar.option_boxes(segs, measure, x0=8)
    assert [b[0] for b in boxes] == ["Deformable", "2Point", "3Point", "Vertical"]
    # boxes must be ordered and non-overlapping
    for (_, a0, a1), (_, b0, b1) in zip(boxes, boxes[1:]):
        assert a0 < a1 <= b0 < b1, (a0, a1, b0, b1)


@check("a box's width is its own text's measured width")
def _():
    segs = opts.prompt_segments("Radius:", CIRCLE_OPTS, {"Deformable": False})
    boxes = cmdbar.option_boxes(segs, measure, x0=8)
    by_name = {n: (x0, x1) for n, x0, x1 in boxes}
    x0, x1 = by_name["2Point"]
    assert abs((x1 - x0) - measure("2Point")) < 1e-9, (x0, x1)


@check("the first box starts after the base prompt, not at x0")
def _():
    segs = opts.prompt_segments("Radius:", CIRCLE_OPTS, {"Deformable": False})
    boxes = cmdbar.option_boxes(segs, measure, x0=8)
    assert boxes[0][1] > 8 + measure("Radius"), boxes[0]


@check("clicking inside a box returns that option")
def _():
    segs = opts.prompt_segments("Radius:", CIRCLE_OPTS, {"Deformable": False})
    boxes = cmdbar.option_boxes(segs, measure, x0=8)
    for name, x0, x1 in boxes:
        mid = (x0 + x1) / 2.0
        assert cmdbar.hit_option(mid, boxes) == name, (name, mid)


@check("clicking between/outside the boxes returns None")
def _():
    segs = opts.prompt_segments("Radius:", CIRCLE_OPTS, {"Deformable": False})
    boxes = cmdbar.option_boxes(segs, measure, x0=8)
    assert cmdbar.hit_option(0, boxes) is None          # in the base prompt
    assert cmdbar.hit_option(10_000, boxes) is None      # past the end
    # the 2-space separator between two options is not either of them
    gap = boxes[0][2] + 1
    assert cmdbar.hit_option(gap, boxes) is None, gap


@check("a box is half-open: its right edge belongs to no option")
def _():
    boxes = [("A", 10.0, 20.0), ("B", 20.0, 30.0)]
    assert cmdbar.hit_option(19.999, boxes) == "A"
    assert cmdbar.hit_option(20.0, boxes) == "B"


@check("hit_option tolerates no boxes at all")
def _():
    assert cmdbar.hit_option(5, []) is None
    assert cmdbar.hit_option(5, None) is None


# ---------------------------------------------------------------------------
# 3. the autocomplete list's rows
# ---------------------------------------------------------------------------

@check("rows stack upward from just above the prompt")
def _():
    rows = cmdbar.completion_rows(["Circle", "Cone", "Copy"], prompt_y=4,
                                  line_h=16, height=94, pad_y=4)
    ys = [y for _, y in rows]
    assert ys == sorted(ys), ys
    assert ys[0] == 4 + 16, ys       # first candidate one line above the prompt


@check("the prompt line is never covered by a candidate row")
def _():
    rows = cmdbar.completion_rows(["A", "B", "C"], prompt_y=4, line_h=16,
                                  height=94, pad_y=4)
    assert all(y > 4 for _, y in rows), rows


@check("the first row is the best match (what Enter accepts)")
def _():
    names = ["Circle", "Cone", "Copy", "Cylinder"]
    matches = cmdbar.match_commands("c", names)
    rows = cmdbar.completion_rows(matches, prompt_y=4, line_h=16, height=94)
    assert rows[0][0] == matches[0], (rows[0], matches[0])


@check("rows are capped by MAX_COMPLETIONS")
def _():
    many = [f"Cmd{i}" for i in range(50)]
    rows = cmdbar.completion_rows(many, prompt_y=4, line_h=16, height=10_000)
    assert len(rows) == cmdbar.MAX_COMPLETIONS, len(rows)


@check("a short bar drops the rows that would not fit, never overflows it")
def _():
    # 40px tall bar: prompt at 4 + one 16px line = 20, so only one row fits
    rows = cmdbar.completion_rows(["A", "B", "C", "D"], prompt_y=4, line_h=16,
                                  height=40, pad_y=4)
    assert len(rows) <= 1, rows
    for _, y in rows:
        assert y + 16 <= 40, (y, "row drawn outside the bar")


@check("no matches means no rows")
def _():
    assert cmdbar.completion_rows([], prompt_y=4, line_h=16, height=94) == []


@check("clicking a row returns that command")
def _():
    rows = cmdbar.completion_rows(["Circle", "Cone"], prompt_y=4, line_h=16,
                                  height=94)
    for name, y in rows:
        assert cmdbar.hit_completion(y + 8, rows, 16) == name, (name, y)


@check("clicking off the rows returns None")
def _():
    rows = cmdbar.completion_rows(["Circle", "Cone"], prompt_y=4, line_h=16,
                                  height=94)
    assert cmdbar.hit_completion(4, rows, 16) is None        # the prompt line
    assert cmdbar.hit_completion(900, rows, 16) is None       # above everything
    assert cmdbar.hit_completion(5, None, 16) is None


@check("row bands do not overlap, so a click is never ambiguous")
def _():
    rows = cmdbar.completion_rows(["A", "B", "C"], prompt_y=4, line_h=16,
                                  height=94)
    for y in range(0, 100):
        hits = [n for n, ry in rows if ry <= y < ry + 16]
        assert len(hits) <= 1, (y, hits)


# ---------------------------------------------------------------------------
# 4. matching follows the documented compatibility behavior
# ---------------------------------------------------------------------------

@check("'ci' offers Circle as a unique prefix")
def _():
    names = ["Box", "Circle", "Cone", "Copy", "Cylinder", "Line"]
    assert cmdbar.match_commands("ci", names) == ["Circle"], \
        cmdbar.match_commands("ci", names)


@check("a prefix matching several commands lists them all")
def _():
    names = ["Box", "Circle", "Cone", "Copy", "Cylinder", "Line"]
    hits = cmdbar.match_commands("c", names)
    assert hits == ["Circle", "Cone", "Copy", "Cylinder"], hits


# ---------------------------------------------------------------------------
# 5. activation letters: auto-assigned per option set, first free letter
# ---------------------------------------------------------------------------

@check("assign_keys takes the first free alphabetic letter, in display order")
def _():
    # Deformable claims D; 2Point skips its digit and claims P; 3Point's P is
    # taken, so it claims the next free letter in the word (the lowercase o).
    keys = opts.assign_keys(CIRCLE_OPTS)
    assert keys == {"Deformable": "D", "2Point": "P", "3Point": "o",
                    "Vertical": "V"}, keys


@check("digits are never keys -- they are the number path")
def _():
    keys = opts.assign_keys(CIRCLE_OPTS)
    assert all(k is None or k.isalpha() for k in keys.values()), keys


@check("assignment depends on display order")
def _():
    swapped = (opts.Option("3Point", opts.MODE), opts.Option("2Point", opts.MODE))
    assert opts.assign_keys(swapped) == {"3Point": "P", "2Point": "o"}, \
        opts.assign_keys(swapped)


@check("an option with every letter claimed gets None (no highlighted letter)")
def _():
    crowded = (opts.Option("A", opts.MODE), opts.Option("B", opts.MODE),
               opts.Option("ab", opts.MODE))
    assert opts.assign_keys(crowded) == {"A": "A", "B": "B", "ab": None}


@check("claiming is case-insensitive")
def _():
    keys = opts.assign_keys((opts.Option("Point", opts.MODE),
                             opts.Option("3point", opts.MODE)))
    assert keys["Point"] == "P" and keys["3point"] == "o", keys


@check("the assigned key is exactly what match() accepts for the option")
def _():
    # The bar highlights assign_keys' letter; typing it (any case) must resolve
    # to that same option. A bare name on a TOGGLE flips it (SET), on a MODE it
    # branches (BRANCH); either way the match resolves to the option itself.
    keys = opts.assign_keys(CIRCLE_OPTS)
    for opt in CIRCLE_OPTS:
        m = opts.match(keys[opt.name].lower(), CIRCLE_OPTS, {"Deformable": False})
        assert m.status not in (opts.NONE, opts.AMBIGUOUS), (opt.name, m)
        assert m.option is opt, (opt.name, m)


@check("a pure-digit input is never an option (it stays a number)")
def _():
    for digit in ("3", "2"):
        m = opts.match(digit, CIRCLE_OPTS, {"Deformable": False})
        assert m.status == opts.NONE, (digit, m)


@check("typing the full name of a digit-led option still works")
def _():
    m = opts.match("3point", CIRCLE_OPTS, {"Deformable": False})
    assert m.option is CIRCLE_OPTS[2], m


@check("key_split lands on the first occurrence of the key and rejoins")
def _():
    cases = (("3Point", "o", ("3P", "o", "int")),
             ("Vertical", "V", ("", "V", "ertical")),
             ("Deformable=Yes", "D", ("", "D", "eformable=Yes")),
             ("Rounded", "z", None),
             ("Rounded", "", None))
    for text, key, want in cases:
        got = opts.key_split(text, key)
        assert got == want, (text, key, got)
        if got is not None:
            assert "".join(got) == text, (text, got)


# ---------------------------------------------------------------------------
failed = 0
for name, status, detail in results:
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    if status == "FAIL":
        failed += 1
print(f"\n{len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
