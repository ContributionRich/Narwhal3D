#!/usr/bin/env python3
"""Pure layout tests for the right dock's tabbed panel container.

The gates pin tab order and default selection, top-down Blender coordinates, clipping
inside short regions, responsive body width, and accurate ``Tab.built`` state.
"""
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rp = _load("narwhal3d.rightpanel", "narwhal3d/rightpanel.py")
toolbar_data = _load("narwhal3d.toolbar_data", "narwhal3d/toolbar_data.py")

checks = 0
failures = []


def check(label, cond):
    # Accept both common check argument orders; the string argument is the label.
    if isinstance(cond, str) and not isinstance(label, str):
        label, cond = cond, label
    global checks
    checks += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


print("== layout constants ==")
# UI design: a 280 px inspector = a 72 px icon-and-label rail + the panel body.
check("default inspector is 280 project pixels wide", rp.BAND_W == 280)
check("tab rail is a fixed 72 project pixels", rp.TAB_STRIP_W == 72)
check("tab pitch is the design's 63 px minimum tab height", rp.TAB_PITCH == 63)
check("tab icons remain fixed at 20 pixels", rp.TAB_ICON == 20)
check("optional caption row is 21 project pixels", rp.TITLE_H == 21)
check("the strip fits inside the band", rp.TAB_STRIP_W < rp.BAND_W)
check("an icon fits inside its tab", rp.TAB_ICON <= rp.TAB_PITCH
      and rp.TAB_ICON <= rp.TAB_STRIP_W + 4)

# The fixed-width tab strip remains stable while the panel body follows resizing.
_narrow = rp.layout(200, 400, title_h=0)
_wide = rp.layout(300, 400, title_h=0)
check("resizing the right dock keeps the measured tab strip width",
      _narrow["strip"][2] - _narrow["strip"][0] == rp.TAB_STRIP_W
      and _wide["strip"][2] - _wide["strip"][0] == rp.TAB_STRIP_W)
check("and gives the full 100 px width increase to the panel body",
      (_wide["body"][2] - _wide["body"][0])
      - (_narrow["body"][2] - _narrow["body"][0]) == 100)

print("\n== the five tabs in display order ==")
check("there are exactly five tabs", len(rp.TABS) == 5)
check("AI Assistant is directly beneath Bug Report",
      rp.tab_names() == ("Layers", "Properties", "Display", "Bug Report",
                         "AI Assistant"))
check("Layers is the authored default",
      rp.DEFAULT_TAB == 0 and rp.TABS[rp.DEFAULT_TAB].name == "Layers")
panel_icons = {t.icon for t in rp.TABS}
check("tabs use the exact approved legacy Layers/Report and v4 Properties/Display/AI icons",
      panel_icons == {"narwhal-panel-layers.png", "narwhal-panel-report.png",
                      "v4-panel-properties.png", "v4-panel-display.png",
                      "v4-panel-ai-assistant.png"}
      and all(rp.toolbars.icon_path(name) for name in panel_icons))
check("icon filenames are distinct",
      len(panel_icons) == len(rp.TABS))

print("\n== honesty: which panel bodies exist, and which say they do not (rule 4) ==")
# Pin the set of implemented bodies independently from the visible tab list.
built = {t.name for t in rp.TABS if t.built}

check(f"all five panel bodies now exist (built={sorted(built)})",
      built == {"Layers", "Properties", "Display", "Bug Report", "AI Assistant"})
check("every UNBUILT tab still says WHY, by name",
      all(t.reason for t in rp.TABS if not t.built))
check("and no BUILT tab carries a stale 'not built because' reason",
      not any(t.reason for t in rp.TABS if t.built))

print("\n== the Layers LIST: measured geometry ==")

check(rp.HEADER_H == 24, "the column header is 24 px (42 screen px / 1.75)")
check(rp.ROW_H == 26, "a layer row is 26 px (UI design; the old compact list was 20)")
body_w = rp.BAND_W - rp.TAB_STRIP_W
check(rp.NAME_COL_W == body_w - 5 * rp.NARROW_COL_W == 108,
      "the Name column fills the 208 px inspector body beside five 20 px state columns")
check(rp.NARROW_COL_W == 20, "the other columns are 20 px (divider pitch 35 / 1.75)")
check(rp.column_dividers() == [108, 128, 148, 168, 188],
      f"the five dividers follow the Name column ({rp.column_dividers()})")
check(rp.COLUMNS[:2] == ("Name", "Current"),
      "the list starts with the name and the current-layer tick")
check(rp.COLUMNS == ("Name", "Current", "Color", "On", "Lock", "Menu"),
      f"UI design columns: name, tick, swatch, eye, lock, more ({rp.COLUMNS})")
check(len(rp.COLUMNS) == 6, f"six columns fit the default list ({len(rp.COLUMNS)})")
check(rp.name_column_width(body_w) == rp.NAME_COL_W,
      "the default body preserves the default Name column")
check(rp.name_column_width(body_w + 100) == rp.NAME_COL_W + 100,
      "widening the dock by 100 px gives all added width to the descriptive Name column")
check(rp.column_dividers(width=279) == [179, 199, 219, 239, 259],
      "the compact state columns stay fixed and remain anchored to the widened right edge")

print("\n== list_layout: header on top, rows TOP-DOWN, nothing escaping ==")
body = rp.layout(rp.BAND_W, 400, title_h=0)["body"]
lay = rp.list_layout(body, 5)
check(set(lay) == {"header", "rows", "dividers", "list"},
      "list_layout returns the four named boxes")
hx0, hy0, hx1, hy1 = lay["header"]
check(hy1 == body[3], "the header is at the TOP of the body")
check(hy1 - hy0 == rp.HEADER_H, "and is HEADER_H tall")
check((hx0, hx1) == (body[0], body[2]), "and spans the body's width")
rows = lay["rows"]
check(len(rows) == 5, f"five layers give five rows ({len(rows)})")
check(rows[0][3] == hy0, "the first row starts where the header ends")
check(all(rows[i][3] > rows[i + 1][3] for i in range(len(rows) - 1)),
      "rows run TOP-DOWN (descending y) -- a bottom-up list passes any count check")
check(all(abs((r[3] - r[1]) - rp.ROW_H) < 1e-9 for r in rows),
      "every row is ROW_H tall")
check(all(r[1] >= body[1] for r in rows), "no row escapes the body")
check(len(lay["dividers"]) == 5, f"five dividers drawn ({len(lay['dividers'])})")
check(all(body[0] <= d[0] < body[2] for d in lay["dividers"]),
      "and every divider is inside the list")
check(lay["list"][3] == body[3] and lay["list"][1] == rows[-1][1],
      "the white 'list' box covers the header AND every row, so the draw can paint "
      "one background")

print("\n== a short panel DROPS rows rather than drawing them off-screen ==")
short = rp.list_layout(rp.layout(rp.BAND_W, 60, title_h=0)["body"], 20)
check(len(short["rows"]) < 20, f"20 layers do not fit 60 px ({len(short['rows'])})")
check(all(r[1] >= 0 for r in short["rows"]), "and no row has negative y")
check(rp.list_layout(rp.layout(rp.BAND_W, 10, title_h=0)["body"], 5)["rows"] == []
      or True, "a 10 px panel does not raise")
check(rp.list_layout((0, 0, 0, 0), 3)["rows"] == [],
      "a zero-size body lays out no rows")
check(rp.list_layout(body, 0)["rows"] == [],
      "and a document with NO layers lays out no rows")

print("\n== cell / hit_column: a click maps to the right COLUMN ==")
row = rows[0]
name_cell = rp.cell(row, 0)
check(name_cell[2] - name_cell[0] == rp.NAME_COL_W,
      "the Name cell is NAME_COL_W wide")
for i in range(len(rp.COLUMNS)):
    c = rp.cell(row, i)
    mid = (c[0] + c[2]) // 2
    check(rp.hit_column(mid, row) == i,
          f"the middle of column {i} ({rp.COLUMNS[i]}) hits {i}")
check(rp.hit_column(row[0] + 500, row) is None,
      "a click PAST the last column hits nothing")
wide_body = rp.layout(300, 400, title_h=0)["body"]
wide_row = rp.list_layout(wide_body, 1)["rows"][0]
wide_body = rp.layout(rp.BAND_W + 100, 400, title_h=0)["body"]
wide_row = rp.list_layout(wide_body, 1)["rows"][0]
check(rp.cell(wide_row, 0)[2] - rp.cell(wide_row, 0)[0] == rp.NAME_COL_W + 100,
      "the actual widened layer row gives the extra 100 px to its Name cell")
check(rp.cell(wide_row, len(rp.COLUMNS) - 1)[2] == wide_row[2],
      "and the final state cell reaches the panel body edge instead of leaving dead space")
check(rp.hit_row((row[0] + row[2]) // 2, (row[1] + row[3]) // 2, rows) == 0,
      "hit_row finds the row under a point")
check(rp.hit_row(row[0] - 5, (row[1] + row[3]) // 2, rows) is None,
      "and nothing left of the list")
check(rp.hit_row((row[0] + row[2]) // 2, body[3] + 50, rows) is None,
      "and nothing above it")
# the rows must TILE: every y in their span hits exactly one row
_lo, _hi = int(rows[-1][1]), int(rows[0][3])
_hits = [rp.hit_row(row[0] + 2, y, rows) for y in range(_lo, _hi)]
check(all(h is not None for h in _hits) and sorted(set(_hits)) == [0, 1, 2, 3, 4],
      "and the rows tile their span with no gap and no overlap")

print("\n== Display panel: responsive rows and shared hit boxes ==")
display = rp.display_panel_layout(body, ("Top", "Perspective", "Front", "Right"),
                                  ("Wireframe", "Shaded", "Rendered"))
check(set(display) == {"labels", "scope", "modes", "viewports"},
      "Display layout returns its four named groups")
check([e[1] for e in display["scope"]] == ["Active", "All"],
      "Display scope is Active then All, matching SetDisplayMode")
check([e[1] for e in display["modes"]] == ["Wireframe", "Shaded", "Rendered"],
      "only the three implemented display modes are presented")
check([e[1] for e in display["viewports"]] ==
      ["Top", "Perspective", "Front", "Right"],
      "all four viewport rows retain visual order")
_all_display_boxes = display["scope"] + display["modes"] + display["viewports"]
check(all(body[0] <= e[2] < e[4] <= body[2]
          and body[1] <= e[3] < e[5] <= body[3]
          for e in _all_display_boxes),
      "every Display control stays inside the resized panel body")
for entry in _all_display_boxes:
    kind, value, x0, y0, x1, y1 = entry
    check(rp.display_panel_hit((x0 + x1) / 2, (y0 + y1) / 2, display) ==
          (kind, value), f"Display hit-test reaches {kind} {value}")
check(rp.display_panel_hit(body[2] + 10, body[3] + 10, display) is None,
      "a click outside Display controls is not claimed")
short_display = rp.display_panel_layout((0, 0, 120, 70), ("Top", "Right"),
                                        ("Wireframe", "Shaded", "Rendered"))
check(all(e[3] >= 0 for key in ("scope", "modes", "viewports")
          for e in short_display[key]),
      "a short Display panel drops rows instead of drawing below its body")

print("\n== rules() cannot drift from the constants ==")
print("\n== layout: region coordinates, y upward ==")
W, H = rp.BAND_W, 800
box = rp.layout(W, H)
check("layout returns the four named boxes",
      set(box) == {"title", "strip", "tabs", "body"})

tx0, ty0, tx1, ty1 = box["title"]
check("the caption row spans the full width", (tx0, tx1) == (0, W))
check("the caption row is at the TOP (y1 == height)", ty1 == H)
check("the caption row is TITLE_H tall by default", ty1 - ty0 == rp.TITLE_H)

sx0, sy0, sx1, sy1 = box["strip"]
check("the rail is flush with the band's LEFT edge", sx0 == 0)
check("the strip is TAB_STRIP_W wide", sx1 - sx0 == rp.TAB_STRIP_W)
check("the strip starts below the caption row", sy1 == H - rp.TITLE_H)
check("the strip reaches the bottom", sy0 == 0)

bx0, by0, bx1, by1 = box["body"]
check("the body starts where the rail ends", bx0 == sx1)
check("the body reaches the right edge", bx1 == W)
check("the body and the rail do not overlap", bx0 >= sx1)
check("the body is below the caption row", by1 == H - rp.TITLE_H)

print("\n== the tabs run TOP-DOWN (descending y), not bottom-up ==")
tabs = box["tabs"]
check("all five tabs fit in an 800 px band", len(tabs) == 5)
tops = [t[3] for t in tabs]
check("tab tops strictly DESCEND (index 0 is the topmost)",
      all(tops[i] > tops[i + 1] for i in range(len(tops) - 1)))
check("the first tab's top meets the strip's top", tops[0] == sy1)
check("consecutive tabs are TAB_PITCH apart",
      all(abs((tops[i] - tops[i + 1]) - rp.TAB_PITCH) < 1e-9
          for i in range(len(tops) - 1)))
check("every tab spans the strip's full width",
      all((t[0], t[2]) == (sx0, sx1) for t in tabs))
check("every tab is TAB_PITCH tall",
      all(abs((t[3] - t[1]) - rp.TAB_PITCH) < 1e-9 for t in tabs))
check("no tab escapes the strip vertically",
      all(sy0 <= t[1] and t[3] <= sy1 for t in tabs))

print("\n== a band too short to hold five tabs DROPS them ==")
# TITLE_H + 2 * TAB_PITCH = 93, so 100 px has room for two tabs and not three.
short = rp.layout(W, 100)
check("a 100 px band lays out fewer than five tabs", len(short["tabs"]) < 5)
check("a 100 px band still lays out at least one", len(short["tabs"]) >= 1)
check("no dropped-tab box has negative y",
      all(t[1] >= 0 for t in short["tabs"]))
tiny = rp.layout(W, 10)
check("a 10 px band lays out NO tabs rather than off-screen ones",
      tiny["tabs"] == [])
check("a 10 px band still returns all four boxes",
      set(tiny) == {"title", "strip", "tabs", "body"})
check("a zero-size band does not raise", rp.layout(0, 0)["tabs"] == [])
check("a negative size is clamped, not raised",
      rp.layout(-5, -5)["strip"] == (0, 0, 0, 0))

print("\n== title_h=0: the host's own header supplies the caption ==")
hosted = rp.layout(W, H, title_h=0)
check("title_h=0 gives a zero-height caption box",
      hosted["title"][3] - hosted["title"][1] == 0)
check("title_h=0 gives the strip the full height", hosted["strip"][3] == H)
check("title_h=0 gives the body the full height", hosted["body"][3] == H)
check("title_h=0 still lays out five tabs", len(hosted["tabs"]) == 5)
check("title_h=0 puts the first tab at the very top",
      hosted["tabs"][0][3] == H)
check("a title_h taller than the band is clamped",
      rp.layout(W, 10, title_h=999)["title"][1] == 0)

print("\n== hit_tab: against the boxes the draw recorded ==")
for i, (x0, y0, x1, y1) in enumerate(tabs):
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    check(f"the centre of tab {i} ({rp.TABS[i].name}) hits {i}",
          rp.hit_tab(cx, cy, tabs) == i)
check("a point left of the strip hits nothing",
      rp.hit_tab(sx0 - 5, tabs[0][1] + 2, tabs) is None)
check("a point above the strip hits nothing",
      rp.hit_tab(sx0 + 2, H - 2, tabs) is None)
check("a point below the last tab hits nothing",
      rp.hit_tab(sx0 + 2, tabs[-1][1] - 5, tabs) is None)
check("an empty box list hits nothing", rp.hit_tab(5, 5, []) is None)
# the boxes must TILE: every y inside the strip's tab span hits exactly one tab
span_lo = int(tabs[-1][1])
span_hi = int(tabs[0][3])
hits = [rp.hit_tab(sx0 + 2, y, tabs) for y in range(span_lo, span_hi)]
check("the tab boxes tile their span with no gap and no overlap",
      all(h is not None for h in hits) and sorted(set(hits)) == list(range(len(rp.TABS))))

print("\n== icon_box: centred in its tab ==")
ib = rp.icon_box(tabs[0])
tx0b, ty0b, tx1b, ty1b = tabs[0]
check("the icon is TAB_ICON square",
      abs((ib[2] - ib[0]) - rp.TAB_ICON) < 1e-9
      and abs((ib[3] - ib[1]) - rp.TAB_ICON) < 1e-9)
check("the icon is centred across its tab and sits above the label",
      abs((ib[0] + ib[2]) / 2 - (tx0b + tx1b) / 2) < 1e-9
      and (ib[1] + ib[3]) / 2 > (ty0b + ty1b) / 2
      and ib[1] - ty0b >= rp.TAB_LABEL_INSET + rp.TAB_LABEL_FONT)
check("the icon stays inside its tab vertically",
      ib[1] >= ty0b and ib[3] <= ty1b)

print("\n== Object Properties aggregate rows ==")
OP = rp.ObjectProperties
same = OP(type="curve", name="A", layer="Layer 01",
          display_color_source="layer", display_color=(1, 2, 3),
          linetype="Continuous", linetype_source="layer", linetype_scale=1.0,
          print_color_source="display", print_color=(1, 2, 3),
          print_width=0.0, no_print=False)
other = OP(type="curve", name="B", layer="Layer 02",
           display_color_source="layer", display_color=(1, 2, 3),
           linetype="Continuous", linetype_source="layer", linetype_scale=1.0,
           print_color_source="display", print_color=(1, 2, 3),
           print_width=0.0, no_print=False)
rows_same = dict(rp.property_rows([same]))
rows_mixed = dict(rp.property_rows([same, other]))
rows_unavailable = dict(rp.property_rows([OP()]))
check("same values remain visible", rows_same["Layer"] == "Layer 01"
      and rows_same["Display Color"] == (1, 2, 3))
check("mixed values say varies", rows_mixed["Layer"] == "varies"
      and rows_mixed["Name"] == "varies")
check("unknown values say unavailable", rows_unavailable["Layer"] == "unavailable"
      and rows_unavailable["Print width"] == "unavailable")
# Mutation gate: collapsing mixed Layer to the first object must be RED.
check("mixed layer does not collapse to the first object",
      rows_mixed["Layer"] != rows_same["Layer"])
check("unsupported non-persistent fields are not shown as properties",
      "Display mode" not in rows_mixed and "Custom mesh" not in rows_mixed)

print("\n== the Layers panel: title buttons, search field, filter ==")
title = (72, 500, 280, 534)
tb = rp.title_buttons(title)
check(set(tb) == {"new", "menu"}, "the title row carries + and ...")
check(tb["menu"][2] == title[2] - rp.SEARCH_PAD and tb["new"][2] < tb["menu"][0],
      f"... sits at the right edge and + just left of it ({tb})")
check(all(title[1] <= b[1] and b[3] <= title[3] for b in tb.values()),
      "both buttons stay inside the title row")
body = (72, 0, 280, 500)
ll = rp.layers_layout(body, 4)
sx0, sy0, sx1, sy1 = ll["search"]
check(sy1 == body[3] - rp.SEARCH_PAD and sy1 - sy0 == rp.SEARCH_H,
      f"the search field runs along the top of the body ({ll['search']})")
check(ll["header"][3] <= sy0, "and the list header starts BELOW it, never under it")
check(len(ll["rows"]) == 4 and ll["rows"][0][3] <= ll["header"][1],
      "rows follow the header, top-down")
tiny = rp.layers_layout((72, 0, 280, 40), 3)
check(tiny["search"] is None, "a body too short for the field gives the list all of it")
names = ["Default", "Layer 01", "Walls", "layer 02", "Doors"]
check(rp.filter_layer_indices(names, "") == [0, 1, 2, 3, 4], "a blank query keeps every layer")
check(rp.filter_layer_indices(names, "  ") == [0, 1, 2, 3, 4], "so does whitespace")
check(rp.filter_layer_indices(names, "LAYER") == [1, 3],
      "matching ignores case and keeps the tree order")
check(rp.filter_layer_indices(names, "oo") == [4], "and matches inside a name")
check(rp.filter_layer_indices(names, "zzz") == [], "and can match nothing")

print("\n== rules() cannot drift from the constants ==")
r = rp.rules()
check("rules() reports the band width", r["band_w"] == rp.BAND_W)
check("rules() reports the tab names", r["tabs"] == rp.tab_names())
check("rules() records the screenshot's DPI scale",
      r["dpi_scale_of_screenshot"] == 1.75)
check("rules() reports the default tab", r["default_tab"] == rp.DEFAULT_TAB)

print("\n== opt-in fixed-row Layers scrolling ==")
scroll_body = (72, 10, 280, 150)
filtered = [2, 7, 11, 19, 23]
scroll = rp.layers_scroll_layout(scroll_body, filtered, 999)
check("search and column header stay fixed above a 78px row viewport",
      scroll["search"] == (78, 118, 274, 144)
      and scroll["header"] == (72, 88, 280, 112)
      and scroll["viewport"] == (72, 10, 280, 88))
check("five fixed26 rows require 130px content and 52px bounded scroll",
      (scroll["offset"], scroll["max_offset"], scroll["content_extent"], scroll["total"])
      == (52, 52, 130, 5))
check("visible rows retain document indices after filtering and scrolling",
      scroll["cells"] == ((11, 72, 62, 280, 88), (19, 72, 36, 280, 62),
                          (23, 72, 10, 280, 36))
      and scroll["hit_cells"] == scroll["cells"])
check("background/dividers fill the scroll viewport, not only the first fitted rows",
      scroll["list"] == (72, 10, 280, 112)
      and scroll["dividers"] == [(180, 10, 112), (200, 10, 112), (220, 10, 112),
                                  (240, 10, 112), (260, 10, 112)])
check("row/column hits use original layer indices and fixed column boxes",
      rp.layers_scroll_hit(80, 70, scroll) == (11, 0)
      and rp.layers_scroll_hit(270, 20, scroll) == (23, 5))
check("header, search, outside width and viewport bottom do not hit layers",
      all(rp.layers_scroll_hit(x, y, scroll) is None for x, y in
          ((80, 88), (80, 125), (280, 70), (71, 70), (80, 9))))
partial = rp.layers_scroll_layout(scroll_body, filtered, 13)
check("partial edge rows are full26 draw boxes with clipped hit intersections",
      partial["cells"][0] == (2, 72, 75, 280, 101)
      and partial["hit_cells"][0] == (2, 72, 75, 280, 88)
      and partial["cells"][-1] == (19, 72, -3, 280, 23)
      and partial["hit_cells"][-1] == (19, 72, 10, 280, 23))
check("clipped rows cannot capture header or below-body clicks",
      rp.layers_scroll_hit(80, 95, partial) is None
      and rp.layers_scroll_hit(80, 5, partial) is None
      and rp.layers_scroll_hit(80, 80, partial) == (2, 0))
check("row boundaries are half-open: boundary belongs to upper row only",
      rp.layers_scroll_hit(80, 75, partial) == (2, 0)
      and rp.layers_scroll_hit(80, 74.9, partial) == (7, 0))
seen, consistent = set(), True
for offset in range(53):
    state = rp.layers_scroll_layout(scroll_body, filtered, offset)
    consistent &= state["search"] == scroll["search"] and state["header"] == scroll["header"]
    for index, x0, y0, x1, y1 in state["cells"]:
        consistent &= y1 - y0 == 26
    for index, x0, y0, x1, y1 in state["hit_cells"]:
        seen.add(index)
        row_box = next(entry[1:] for entry in state["cells"] if entry[0] == index)
        for column in range(6):
            box = rp.cell(row_box, column)
            consistent &= rp.layers_scroll_hit((box[0]+box[2])/2, (y0+y1)/2, state) == (index, column)
check("all filtered rows/columns reachable at fixed size with draw/hit agreement at every offset",
      consistent and seen == set(filtered))
check("filtering and enlarging the viewport clamp stale scroll state",
      rp.layers_scroll_layout(scroll_body, [23], 999)["offset"] == 0
      and rp.layers_scroll_layout((72, 10, 280, 1000), filtered, 999)["max_offset"] == 0
      and rp.layers_scroll_layout(scroll_body, filtered, -9)["offset"] == 0)
short_scroll = rp.layers_scroll_layout((72, 0, 280, 40), [7, 19], 999)
check("short body removes search but still scrolls through full26 rows in a 16px viewport",
      short_scroll["search"] is None and short_scroll["max_offset"] == 36
      and short_scroll["cells"] == ((19, 72, 0, 280, 26),)
      and short_scroll["hit_cells"] == ((19, 72, 0, 280, 16),)
      and rp.layers_scroll_hit(80, 8, short_scroll) == (19, 0))
empty = rp.layers_scroll_layout(scroll_body, [], 999)
check("empty filter result has no phantom rows or stale offset",
      empty["cells"] == empty["hit_cells"] == () and empty["total"] == 0
      and empty["offset"] == empty["max_offset"] == empty["content_extent"] == 0)
check("zero-width and no-row-height viewports contain no hittable rows",
      all(rp.layers_scroll_layout(box, filtered, 999)["cells"] == () for box in
          ((72, 0, 72, 400), (72, 0, 280, 24), (72, 0, 280, 0))))
check("exact-fit viewport has zero scroll; one pixel less has one pixel scroll",
      rp.layers_scroll_layout((72, 0, 280, 140), range(3))["max_offset"] == 0
      and rp.layers_scroll_layout((72, 0, 280, 139), range(3))["max_offset"] == 1)
check("supplied row height supports scaled host coordinates without changing defaults",
      rp.layers_scroll_layout(scroll_body, [7, 11], row_h=52)["content_extent"] == 104
      and rp.ROW_H == 26)
try:
    rp.layers_scroll_layout(scroll_body, filtered, row_h=0)
except ValueError:
    check("zero row height rejected instead of losing rows", True)
else:
    check("zero row height rejected instead of losing rows", False)

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("right dock band layout OK")
