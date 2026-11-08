"""Layout tests for the Print Setup dialog: nothing clips and everything stays clickable.

Covers the regression in clipping of the "Scale (paper = model)" label and its dropdown,
at every supported interface scale and at pane sizes from the supported minimum upward.
Clipping is judged with the same measured text model the layout uses (`text_width`),
checked against widths measured in Blender's own font.
"""
import dataclasses
import importlib
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")
_pkg = types.ModuleType("narwhal3d")
_pkg.__path__ = [ADDON]
_pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = _pkg
pd = importlib.import_module("narwhal3d.printdialog")

results = []


def check(name):
    def deco(fn):
        try:
            fn()
            results.append((True, name, ""))
        except AssertionError as e:
            results.append((False, name, str(e)))
        except Exception as e:  # noqa: BLE001
            results.append((False, name, f"{type(e).__name__}: {e}"))
        return fn
    return deco


#: Interface scales the dialog is laid out for (`uiscale.MIN_SCALE` .. `MAX_SCALE`).
SCALES = (0.6, 1.0, 1.3, 2.0, 2.5)
#: Pane sizes as multiples of the supported minimum: the minimum itself, then larger.
FRAMES = ((1.0, 1.0), (1.0, 1.3), (1.25, 1.0), (1.6, 1.2), (2.4, 1.7))
VIEWS = ("Top", "Front", "Right", "Perspective")
#: Drawn widths MEASURED in Blender 5.2.2's blf font 0, by font size in px.
MEASURED = {
    7.8: {"Scale (paper = model)": 83.0, "CorelDRAW 2015/X7 (.cdr)": 102.0,
          "Letter  (215.9 x 279.4 mm)": 101.0, "Custom  (5000 x 5000 mm)": 104.0,
          "(from width x height)": 80.0, "Black and white": 58.0,
          "Default line width": 67.0, "CorelDRAW version": 75.0},
    13.0: {"Scale (paper = model)": 136.0, "CorelDRAW 2015/X7 (.cdr)": 167.0,
           "Letter  (215.9 x 279.4 mm)": 160.0, "Custom  (5000 x 5000 mm)": 166.0,
           "(from width x height)": 128.0, "Black and white": 97.0,
           "Default line width": 108.0, "CorelDRAW version": 123.0},
    26.0: {"Scale (paper = model)": 261.0, "CorelDRAW 2015/X7 (.cdr)": 330.0,
           "Letter  (215.9 x 279.4 mm)": 330.0, "Custom  (5000 x 5000 mm)": 341.0,
           "(from width x height)": 247.0, "Black and white": 186.0,
           "Default line width": 203.0, "CorelDRAW version": 233.0},
    32.5: {"Scale (paper = model)": 319.0, "CorelDRAW 2015/X7 (.cdr)": 405.0,
           "Letter  (215.9 x 279.4 mm)": 408.0, "Custom  (5000 x 5000 mm)": 420.0,
           "(from width x height)": 299.0, "Black and white": 225.0,
           "Default line width": 245.0, "CorelDRAW version": 281.0},
}


def _fresh(st, open_rid=None):
    """A copy of `st` with no interaction state (its list `open_rid` open), so each use
    starts clean."""
    return dataclasses.replace(st, open_dropdown=open_rid, hover=None, focus=None,
                               edit_text="", message="", extra={})


def _states():
    """Settings that exercise every column: CDR (extra row), Custom (fields), long text."""
    yield pd.DialogState(views=VIEWS, view="Top")
    yield pd.DialogState(views=VIEWS, view="Top", destination=1, size="Custom",
                         custom_w=5000.0, custom_h=5000.0)
    yield pd.DialogState(views=VIEWS, view="Front", destination=2, size="A0",
                         orientation="Portrait", color="BlackWhite", selected_only=True,
                         width="Hairline", area="Extents", scale="10:1")


def _cases():
    for s in SCALES:
        for fw, fh in FRAMES:
            W, H = pd.MIN_REGION[0] * s * fw, pd.MIN_REGION[1] * s * fh
            yield s, W, H


def _inside(inner, outer, what):
    eps = 1e-6
    assert inner.x >= outer.x - eps and inner.y >= outer.y - eps, (what, inner, outer)
    assert inner.x + inner.w <= outer.x + outer.w + eps, (what, inner, outer)
    assert inner.y + inner.h <= outer.y + outer.h + eps, (what, inner, outer)


def _check_closed(s, W, H, st):
    """Every row, its label and its value fits; all of it inside panel and region."""
    lay = pd.layout(W, H, s, st)
    region = pd.Rect(0, 0, W, H)
    fs = 13.0 * s
    for rid, lr, vr in lay.rows:
        what = f"row {rid} at scale {s} region {W:.0f}x{H:.0f}"
        _inside(lr, lay.panel, what + " label")
        _inside(vr, lay.panel, what + " value")
        _inside(vr, region, what + " value in region")
        label = pd.ROW_LABELS[rid]
        assert lr.x + pd.text_width(label, fs) <= vr.x, f"{what}: label {label!r} runs under value"
        kind = pd.ROW_KINDS[rid]
        if kind == "check":
            continue
        if kind == "field":
            texts = ["0" * pd.FIELD_MAX_CHARS]
        else:
            texts = [st.label_of(rid, o) for o in st.choices(rid)] + [st.shown(rid)]
        for t in texts:
            # the text starts after the pad and must end before the arrow glyph (16 px)
            end = vr.x + pd.FIELD_TEXT_PAD * s + pd.text_width(t, fs)
            assert end <= vr.x + vr.w - 16.0 * s, f"{what}: {t!r} clipped by value box"
    rows_top = max(vr.y + vr.h for _r, _l, vr in lay.rows)
    assert rows_top <= lay.title.y + 1e-6, "rows run into the title bar"
    last = min(vr.y for _r, _l, vr in lay.rows)
    assert last >= lay.panel.y + pd.NOTES_RESERVE * s - 1e-6, "rows crowd the status text"
    nr = pd.notes_rect(lay)
    assert nr.h >= 34.0 * s - 1e-6, f"status text has room for {nr.h / s:.1f} px only"
    for _bid, b in lay.buttons:
        assert nr.x + nr.w <= b.x - 8.0 * s, "status text runs into a button"
    assert nr.x + nr.w <= lay.preview.x + 1e-6, "status text runs under the preview"
    assert lay.preview.w >= 150.0 * s, f"preview too narrow ({lay.preview.w:.0f})"
    _inside(lay.preview, lay.panel, "preview")
    for rid, _lr, vr in lay.rows:
        assert pd.hit(lay, *vr.center) == ("row", rid), rid
    for bid, b in lay.buttons:
        assert pd.hit(lay, *b.center) == ("button", bid), bid
    return lay


@check("the measured text model never under-estimates Blender's own widths")
def _():
    for size, widths in MEASURED.items():
        for text, measured in widths.items():
            est = pd.text_width(text, size)
            assert est >= measured, f"{text!r} at {size}: estimate {est:.1f} < {measured}"
            # ...nor is it so loose that it wastes the pane
            assert est <= measured * 1.25, f"{text!r} at {size}: estimate {est:.1f}"


@check("every closed layout: label, value, text, rows, status and buttons fit")
def _():
    for s, W, H in _cases():
        for st in _states():
            _check_closed(s, W, H, st)


@check("the Scale label and its value box never overlap at the minimum pane")
def _():
    for s in SCALES:
        st = pd.DialogState(views=VIEWS, view="Top")
        lay = pd.layout(pd.MIN_REGION[0] * s, pd.MIN_REGION[1] * s, s, st)
        lr, vr = next((l, v) for r, l, v in lay.rows if r == "scale")
        assert lr.x + pd.text_width(pd.ROW_LABELS["scale"], 13.0 * s) <= vr.x, (s, lr, vr)


@check("every open dropdown: items fit in panel and region, and read in full")
def _():
    for s, W, H in _cases():
        for st0 in _states():
            for rid in [r for r, _l, k in st0.visible_rows() if k == "choice"]:
                st = _fresh(st0, rid)
                lay = pd.layout(W, H, s, st)
                box = next(vr for r, _l, vr in lay.rows if r == rid)
                opts = st.choices(rid)
                assert len(lay.dropdown) == len(opts), (rid, len(lay.dropdown), len(opts))
                for i, r in lay.dropdown:
                    what = f"{rid} item {i} at scale {s} region {W:.0f}x{H:.0f}"
                    _inside(r, lay.panel, what)
                    _inside(r, pd.Rect(0, 0, W, H), what + " in region")
                    assert r.y >= box.y + box.h - 1e-6 or r.y + r.h <= box.y + 1e-6, what
                    text = "    " + st.label_of(rid, opts[i])
                    assert 6.0 * s + pd.text_width(text, 13.0 * s) <= r.w - 6.0 * s, \
                        f"{what}: {text!r} runs past the list"
                    assert pd.hit(lay, *r.center) == ("item", i), what


@check("every open dropdown: each item can be clicked to choose that value")
def _():
    for s, W, H in _cases():
        for st0 in _states():
            for rid in [r for r, _l, k in st0.visible_rows() if k == "choice"]:
                n = len(st0.choices(rid))
                for i in range(n):
                    st = _fresh(st0, rid)
                    lay = pd.layout(W, H, s, st)
                    item = lay.dropdown[i][1]
                    result = pd.click(st, lay, *item.center)
                    assert result == f"chose:{rid}", (rid, i, result)
                    assert getattr(st, rid) == st0.choices(rid)[i], (rid, i)


@check("the Scale list at the minimum pane fits, shrinking only if neither side has room")
def _():
    for s in SCALES:
        st = pd.DialogState(views=VIEWS, view="Top", open_dropdown="scale")
        lay = pd.layout(pd.MIN_REGION[0] * s, pd.MIN_REGION[1] * s, s, st)
        assert len(lay.dropdown) == len(pd.SCALES)
        heights = {round(r.h, 6) for _i, r in lay.dropdown}
        assert len(heights) == 1 and max(heights) <= 24.0 * s + 1e-6, heights
        for _i, r in lay.dropdown:
            _inside(r, lay.panel, "scale item")


@check("a list opens BELOW its box when every item fits there, and never mixes sides")
def _():
    st = pd.DialogState(views=VIEWS, view="Top", open_dropdown="size")
    lay = pd.layout(1600, 900, 1.0, st)
    box = next(vr for r, _l, vr in lay.rows if r == "size")
    assert all(r.y + r.h <= box.y + 1e-6 for _i, r in lay.dropdown), "size list not below"
    st.open_dropdown = "scale"
    lay = pd.layout(1600, 900, 1.0, st)
    box = next(vr for r, _l, vr in lay.rows if r == "scale")
    sides = {(r.y >= box.y + box.h - 1e-6) for _i, r in lay.dropdown}
    assert len(sides) == 1, "list straddles its box"


@check("a list with no room below opens ABOVE its box at full item height")
def _():
    lay = pd.layout(1600, 900, 1.0, pd.DialogState(views=VIEWS, view="Top"))
    box = pd.Rect(417.0, 150.0, 303.0, 26.0)     # low in the panel: 15 items fit above only
    lay.rows = [("width", pd.Rect(100.0, 150.0, 80.0, 26.0), box)]
    items = pd.dropdown_items(lay, "width", len(pd.WIDTHS), 900)
    assert len(items) == len(pd.WIDTHS)
    assert all(r.y >= box.y + box.h - 1e-6 for _i, r in items), "should open upward"
    assert all(abs(r.h - 24.0) < 1e-6 for _i, r in items), "items shrank needlessly"


@check("a list widens to its longest item, and a list too tall for either side fits")
def _():
    st = pd.DialogState(views=VIEWS, view="Top")
    lay = pd.layout(1600, 900, 1.0, st)
    long_text = "x" * 80
    items = pd.dropdown_items(lay, "width", 2, 900, [long_text, "y"])
    need = pd.text_width("    " + long_text, 13.0) + 12.0
    assert all(r.w >= need for _i, r in items), (items[0][1].w, need)
    items = pd.dropdown_items(lay, "size", 40, 900)
    assert len(items) == 40
    lo, hi = lay.panel.y + 14.0, lay.title.y
    assert min(r.y for _i, r in items) >= lo - 1e-6, "list runs below the panel"
    assert max(r.y + r.h for _i, r in items) <= hi + 1e-6, "list runs into the title"
    assert len({round(r.h, 6) for _i, r in items}) == 1, "items not uniform"


@check("columns do not move when a setting changes (Custom adds rows, not widths)")
def _():
    for s, W, H in _cases():
        a = pd.layout(W, H, s, pd.DialogState(views=VIEWS, view="Top"))
        b = pd.layout(W, H, s, pd.DialogState(views=VIEWS, view="Top", size="Custom",
                                              destination=1))
        xa = {r: (l.x, v.x, v.w) for r, l, v in a.rows}
        xb = {r: (l.x, v.x, v.w) for r, l, v in b.rows}
        for rid in set(xa) & set(xb):
            assert xa[rid] == xb[rid], (rid, xa[rid], xb[rid])


@check("the dialog clips nothing at the minimum pane: controls, status and buttons clickable")
def _():
    for s in SCALES:
        st = pd.DialogState(views=VIEWS, view="Top", destination=1, size="Custom")
        lay = pd.layout(pd.MIN_REGION[0] * s, pd.MIN_REGION[1] * s, s, st)
        for rid, _lr, vr in lay.rows:
            assert pd.hit(lay, *vr.center) == ("row", rid), (s, rid)
        for bid, b in lay.buttons:
            assert pd.hit(lay, *b.center) == ("button", bid), (s, bid)


if __name__ == "__main__":
    failed = [r for r in results if not r[0]]
    for ok, name, msg in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {msg}" if msg else ""))
    print(f"\n{len(results) - len(failed)}/{len(results)} print-dialog layout checks passed")
    sys.exit(1 if failed else 0)
