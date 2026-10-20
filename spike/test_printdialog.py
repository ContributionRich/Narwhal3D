"""Pure tests for Print settings, layout, hit-testing, and preview geometry."""
import importlib
import math
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")
_pkg = types.ModuleType("narwhal3d")
_pkg.__path__ = [ADDON]
_pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = _pkg
pd = importlib.import_module("narwhal3d.printdialog")
p2 = importlib.import_module("narwhal3d.print2d")

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


class Model:
    def __init__(self):
        self.file = r3.File3dm()


TOP = p2.View((1, 0, 0), (0, 1, 0), (0, 0, 0), 200.0, 150.0)


@check("defaults are PDF, Letter Landscape, Print color, Fit, 0.13 mm")
def _():
    st = pd.DialogState(views=("Top", "Front"), view="Top")
    assert st.extension() == ".pdf" and st.shown("destination").startswith("PDF")
    assert (st.size, st.orientation, st.color, st.scale) == (
        "Letter", "Landscape", "PrintColor", "Fit")
    assert st.width_mm() == 0.13, st.width_mm()


@check("destinations expose PDF, CorelDRAW 2015, AI, and SVG")
def _():
    assert pd.DESTINATIONS == (("PDF", ".pdf"), ("CorelDRAW 2015/X7", ".cdr"),
                               ("Adobe Illustrator", ".ai"), ("SVG", ".svg"))


@check("choosing CorelDRAW defaults untouched Fit output to manufacturing 1:1")
def _():
    st = pd.DialogState()
    lay = pd.layout(1600, 900, 1.0, st)
    destination = next(vr for rid, _label, vr in lay.rows if rid == "destination")
    pd.click(st, lay, *destination.center)
    lay = pd.layout(1600, 900, 1.0, st)
    assert pd.click(st, lay, *dict(lay.dropdown)[1].center) == "chose:destination"
    assert st.extension() == ".cdr" and st.scale == "1:1"
    assert "cdr_version" in [rid for rid, _label, _kind in st.visible_rows()]
    st.cdr_version = "17"
    assert st.setup().cdr_version == 17
    by_key = pd.DialogState()
    lay = pd.layout(1600, 900, 1.0, by_key)
    destination = next(vr for rid, _label, vr in lay.rows if rid == "destination")
    pd.click(by_key, lay, *destination.center)
    by_key.hover = 1
    assert pd.key(by_key, "enter") == "chose:destination"
    assert by_key.extension() == ".cdr" and by_key.scale == "1:1"


@check("the width list exposes genuine CorelDRAW Hairline and standard drafting widths")
def _():
    assert pd.WIDTHS[:2] == ("Hairline", "0.13") and pd.WIDTHS[-1] == "2.00"
    st = pd.DialogState(width="Hairline")
    assert st.width_mm() == 0.0762 and st.shown("width") == "Hairline", st.width_mm()


@check("the settings become the PageSetup the PDF writer uses")
def _():
    st = pd.DialogState(size="A4", orientation="Portrait", area="Extents",
                        scale="1:20", color="BlackWhite", width="0.50")
    s = st.setup()
    assert (s.size, s.orientation, s.area, s.color) == ("A4", "Portrait", "Extents",
                                                        "BlackWhite")
    assert abs(s.scale - 0.05) < 1e-12 and s.default_width_mm == 0.5, s


@check("layout: controls LEFT, preview RIGHT, buttons along the bottom, all inside")
def _():
    lay = pd.layout(1600, 900)
    p = lay.panel
    for _rid, lr, vr in lay.rows:
        assert lr.x >= p.x and vr.x + vr.w <= lay.preview.x, (vr, lay.preview)
    assert lay.preview.x > p.x + p.w * 0.3
    for _bid, b in lay.buttons:
        assert b.y < lay.preview.y and p.contains(b.x + 1, b.y + 1), b
    # rows run top to bottom in ROWS order
    ys = [vr.y for _r, _l, vr in lay.rows]
    assert ys == sorted(ys, reverse=True), ys


@check("hit-testing finds rows and buttons at their centres; outside is None")
def _():
    lay = pd.layout(1600, 900)
    for rid, _lr, vr in lay.rows:
        assert pd.hit(lay, *vr.center) == ("row", rid), rid
    for bid, b in lay.buttons:
        assert pd.hit(lay, *b.center) == ("button", bid), bid
    assert pd.hit(lay, *lay.preview.center) == ("panel", None)
    assert pd.hit(lay, 1, 1) is None


@check("the sheet in the preview has the PAGE's aspect (and flips with orientation)")
def _():
    prev = pd.Rect(0, 0, 600, 400)
    land = pd.page_rect(prev, 279.4, 215.9)
    port = pd.page_rect(prev, 215.9, 279.4)
    assert abs(land.w / land.h - 279.4 / 215.9) < 1e-9
    assert abs(port.w / port.h - 215.9 / 279.4) < 1e-9
    assert land.w > land.h and port.h > port.w


@check("the preview draws the SAME page the PDF gets: a 1:1 circle lands where it prints")
def _():
    m = Model()
    m.file.Objects.AddCurve(r3.Circle(r3.Point3d(0, 0, 0), 20.0).ToNurbsCurve())
    st = pd.DialogState(scale="1:1")
    page = p2.build_page(m, TOP, st.setup())
    sheet = pd.page_rect(pd.Rect(0, 0, 900, 700), page.width_mm, page.height_mm)
    (pts, rgb, _w), = pd.preview_lines(page, sheet)
    k = sheet.w / page.width_mm
    cx, cy = sheet.x + sheet.w / 2, sheet.y + sheet.h / 2
    worst = max(abs(math.hypot(x - cx, y - cy) - 20.0 * k) for x, y in pts)
    assert worst < 0.01 * k + 0.05 * 20 * k / 16, worst   # sampled chord sag only
    assert rgb == (0.0, 0.0, 0.0)


def _lay(st, w=1600, h=900):
    return pd.layout(w, h, 1.0, st)


def _row(lay, rid):
    return next(vr for r, _l, vr in lay.rows if r == rid)


@check("clicking a setting OPENS its dropdown -- it does not change the setting")
def _():
    st = pd.DialogState()
    lay = _lay(st)
    assert pd.click(st, lay, *_row(lay, "size").center) == "opened:size"
    assert st.size == "Letter" and st.open_dropdown == "size"
    lay = _lay(st)
    assert len(lay.dropdown) == len(pd.SIZES), len(lay.dropdown)
    assert st.hover == pd.SIZES.index("Letter"), st.hover
    box = _row(lay, "size")
    assert all(r.y + r.h <= box.y + 1e-9 for _i, r in lay.dropdown), "list not below"


@check("clicking an item CHOOSES it and closes the list")
def _():
    st = pd.DialogState()
    lay = _lay(st)
    pd.click(st, lay, *_row(lay, "size").center)
    lay = _lay(st)
    i = pd.SIZES.index("A4")
    r = dict(lay.dropdown)[i]
    assert pd.click(st, lay, *r.center) == "chose:size"
    assert st.size == "A4" and st.open_dropdown is None


@check("a click OUTSIDE an open list only closes it (the row under it is untouched)")
def _():
    st = pd.DialogState()
    lay = _lay(st)
    pd.click(st, lay, *_row(lay, "orientation").center)
    lay = _lay(st)
    # click the Area row, which is not covered by the orientation list's items
    target = _row(lay, "selected_only")
    assert pd.click(st, lay, *target.center) == "closed"
    assert st.open_dropdown is None and st.selected_only is False
    assert st.orientation == "Landscape"


@check("Esc closes an open list; Enter picks the highlighted item; hover follows the mouse")
def _():
    st = pd.DialogState()
    lay = _lay(st)
    pd.click(st, lay, *_row(lay, "scale").center)
    assert pd.key(st, "esc") == "closed" and st.open_dropdown is None
    pd.click(st, _lay(st), *_row(lay, "scale").center)
    lay = _lay(st)
    r = dict(lay.dropdown)[pd.SCALES.index("1:20")]
    assert pd.move(st, lay, *r.center) and st.hover == pd.SCALES.index("1:20")
    assert pd.key(st, "enter") == "chose:scale" and st.scale == "1:20"


@check("a list with no room below opens UPWARD, staying inside the region")
def _():
    st = pd.DialogState()
    lay = _lay(st, 1600, 900)
    box = pd.Rect(100, 40, 200, 26)
    lay.rows = [("width", pd.Rect(10, 40, 80, 26), box)]
    items = pd.dropdown_items(lay, "width", len(pd.WIDTHS), 900)
    assert all(r.y >= box.y + box.h - 1e-9 for _i, r in items), "should open upward"
    assert max(r.y + r.h for _i, r in items) <= 900


@check("the check box toggles on click")
def _():
    st = pd.DialogState()
    lay = _lay(st)
    assert pd.click(st, lay, *_row(lay, "selected_only").center) == "toggled:selected_only"
    assert st.selected_only is True


@check("Custom size: the Width/Height FIELDS appear, focused, and take typed numbers")
def _():
    st = pd.DialogState()
    lay = _lay(st)
    assert "custom_w" not in [r for r, _l, _v in lay.rows]
    pd.click(st, lay, *_row(lay, "size").center)
    lay = _lay(st)
    pd.click(st, lay, *dict(lay.dropdown)[pd.SIZES.index("Custom")].center)
    lay = _lay(st)
    rows = [r for r, _l, _v in lay.rows]
    assert "custom_w" in rows and "custom_h" in rows, rows
    assert st.focus == "custom_w", st.focus
    st.edit_text = ""
    for ch in "300":
        assert pd.key(st, "char", ch) == "chose:typing"
    assert pd.key(st, "char", "x") == "handled" and st.edit_text == "300"
    assert pd.key(st, "tab") == "chose:field" and st.custom_w == 300.0
    assert st.focus == "custom_h"
    st.edit_text = ""
    for ch in "123.5":
        pd.key(st, "char", ch)
    assert pd.key(st, "enter") == "chose:field" and st.custom_h == 123.5
    s = st.setup()
    assert s.size == "Custom" and s.custom_mm == (300.0, 123.5)
    assert p2.page_size(s) == (300.0, 123.5)


@check("a bad custom value is REFUSED with a message; Esc abandons an edit")
def _():
    st = pd.DialogState(size="Custom", custom_w=200.0)
    st.focus, st.edit_text = "custom_w", "0"
    pd.key(st, "enter")
    assert st.custom_w == 200.0 and "more than 0" in st.message, st.message
    st.focus, st.edit_text = "custom_w", "999"
    pd.key(st, "esc")
    assert st.custom_w == 200.0 and st.focus is None


@check("a click elsewhere COMMITS a field being typed in")
def _():
    st = pd.DialogState(size="Custom")
    lay = _lay(st)
    pd.click(st, lay, *_row(lay, "custom_w").center)
    st.edit_text = "250"
    assert pd.click(st, lay, *lay.preview.center) == "chose:field"
    assert st.custom_w == 250.0 and st.focus is None


@check("a Custom page really is that size when built")
def _():
    m = Model()
    m.file.Objects.AddCurve(r3.LineCurve(r3.Point3d(0, 0, 0), r3.Point3d(10, 0, 0)))
    st = pd.DialogState(size="Custom", custom_w=300.0, custom_h=123.5, scale="1:1")
    page = p2.build_page(m, TOP, st.setup())
    assert (page.width_mm, page.height_mm) == (300.0, 123.5), (page.width_mm,
                                                               page.height_mm)


@check("the note text wraps to the space under the controls, never past it")
def _():
    lay = _lay(pd.DialogState())
    nr = pd.notes_rect(lay)
    assert nr.x + nr.w <= lay.preview.x and nr.h > 0, (nr, lay.preview)
    text = "surfaces and solids print their EDGES only -- silhouettes are not done " * 3
    lines = pd.wrap(text, 200, lambda t: 7.0 * len(t))
    assert len(lines) > 1 and all(7.0 * len(l) <= 200 for l in lines), lines
    assert " ".join(lines) == " ".join(text.split())


@check("a dropdown list is at least wide enough for its longest label")
def _():
    st = pd.DialogState()
    lay = _lay(st)
    pd.click(st, lay, *_row(lay, "size").center)
    lay = _lay(st)
    assert all(r.w >= 300 for _i, r in lay.dropdown), lay.dropdown[0]


@check("the preview follows a size field AS IT IS TYPED (before Enter)")
def _():
    st = pd.DialogState(size="Custom", custom_w=210.0, custom_h=297.0)
    st.focus, st.edit_text = "custom_w", ""
    for ch in "40":
        r = pd.key(st, "char", ch)
    assert r == "chose:typing" and st.custom_w == 210.0     # not committed yet
    assert st.setup().custom_mm == (40.0, 297.0), st.setup().custom_mm
    st.edit_text = "0"                                       # invalid while typing
    assert st.setup().custom_mm == (210.0, 297.0)            # falls back, no crash
    assert pd.key(st, "back") == "chose:typing"


@check("a size-field click places the caret; drag and Shift extend selection")
def _():
    m = lambda t: 7.0 * len(t)          # noqa: E731
    st = pd.DialogState(size="Custom", custom_w=210.0, custom_h=297.0)
    lay = pd.layout(1200, 800, 1.0, st)
    vr = _row(lay, "custom_w")
    x0 = vr.x + pd.FIELD_TEXT_PAD
    y = vr.y + vr.h / 2
    pd.click(st, lay, x0 + 7 * 1 + 1, y, measure=m)        # after the "2"
    assert st.focus == "custom_w" and pd.field_of(st).caret == 1, pd.field_of(st).caret
    assert pd.field_of(st).selection() is None, "a click does not select-all"
    pd.release(st)
    pd.key(st, "char", "5")
    assert st.edit_text == "2510", st.edit_text
    # drag from the start to after "25"
    pd.click(st, lay, x0 + 0.5, y, measure=m)
    assert pd.move(st, lay, x0 + 14 + 1, y, measure=m)
    pd.release(st)
    assert pd.field_of(st).selected_text() == "25", pd.field_of(st).selected_text()
    assert not pd.move(st, lay, x0 + 28, y, measure=m), "no drag after the release"
    pd.key(st, "char", "3")
    assert st.edit_text == "310", st.edit_text            # typing replaced "25"
    pd.key(st, "end")
    pd.key(st, "left", shift=True)
    pd.key(st, "left", shift=True)
    pd.key(st, "copy")
    assert st.extra["clipboard"] == "10"
    pd.key(st, "cut")
    assert st.edit_text == "3" and st.extra["clipboard"] == "10"
    pd.key(st, "paste", "0.5x")                             # filtered to digits + one '.'
    assert st.edit_text == "30.5", st.edit_text
    assert pd.key(st, "paste", ".") == "handled" and st.edit_text == "30.5"
    pd.key(st, "home")
    pd.key(st, "delete")
    assert st.edit_text == "0.5", st.edit_text
    assert pd.key(st, "tab") == "chose:field" and st.custom_w == 0.5
    # Tab focuses the next field with its number SELECTED: typing replaces it
    assert st.focus == "custom_h" and pd.field_of(st).selected_text() == "297"
    pd.key(st, "char", "4")
    assert st.edit_text == "4", st.edit_text


if __name__ == "__main__":
    failed = [r for r in results if not r[0]]
    for ok, name, msg in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {msg}" if msg else ""))
    print(f"\n{len(results) - len(failed)}/{len(results)} print-dialog checks passed")
    sys.exit(1 if failed else 0)
