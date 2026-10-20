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
check("default band is 200 project pixels wide", rp.BAND_W == 200)
check("tab strip is a fixed 21 project pixels", rp.TAB_STRIP_W == 21)
check("tab pitch is 36 project pixels", rp.TAB_PITCH == 36)
check("tab icons remain fixed at 16 pixels", rp.TAB_ICON == 16)
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

print("\n== the four tabs in display order ==")
check("there are exactly four tabs", len(rp.TABS) == 4)
check("order is Layers, Properties, Display, Bug Report",
      rp.tab_names() == ("Layers", "Properties", "Display", "Bug Report"))
check("Layers is the authored default",
      rp.DEFAULT_TAB == 0 and rp.TABS[rp.DEFAULT_TAB].name == "Layers")
panel_icons = {t.icon for t in rp.TABS}
check("every tab names a project-owned manifested icon",
      panel_icons == set(toolbar_data.PANEL_ICON_NAMES)
      and panel_icons <= set(toolbar_data.ICON_NAMES))
check("icon filenames are distinct",
      len(panel_icons) == len(rp.TABS))

print("\n== honesty: which panel bodies exist, and which say they do not (rule 4) ==")
# Pin the set of implemented bodies independently from the visible tab list.
built = {t.name for t in rp.TABS if t.built}

check(f"all four panel bodies now exist (built={sorted(built)})",
      built == {"Layers", "Properties", "Display", "Bug Report"})
check("every UNBUILT tab still says WHY, by name",
      all(t.reason for t in rp.TABS if not t.built))
check("and no BUILT tab carries a stale 'not built because' reason",
      not any(t.reason for t in rp.TABS if t.built))

print("\n== the Layers LIST: measured geometry ==")

check(rp.HEADER_H == 24, "the column header is 24 px (42 screen px / 1.75)")
check(rp.ROW_H == 20, "a layer row is 20 px (35 screen px / 1.75)")
check(rp.NAME_COL_W == 79, "the Name column is 79 px (first divider at screen x+139)")
check(rp.NARROW_COL_W == 20, "the other columns are 20 px (divider pitch 35 / 1.75)")
check(rp.column_dividers() == [79, 99, 119, 139, 159],
      f"the five dividers land where they were measured ({rp.column_dividers()})")
check(rp.COLUMNS[:2] == ("Name", "Current"),
      "the column order is layer.htm's: Name, Current, On, Lock, Color, Material")
check(len(rp.COLUMNS) == 6, f"six columns fit the default list ({len(rp.COLUMNS)})")
check(rp.name_column_width(179) == rp.NAME_COL_W,
      "the default 179 px body preserves the measured 79 px Name column")
check(rp.name_column_width(279) == rp.NAME_COL_W + 100,
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
check("the strip is flush with the band's RIGHT edge", sx1 == W)
check("the strip is TAB_STRIP_W wide", sx1 - sx0 == rp.TAB_STRIP_W)
check("the strip starts below the caption row", sy1 == H - rp.TITLE_H)
check("the strip reaches the bottom", sy0 == 0)

bx0, by0, bx1, by1 = box["body"]
check("the body ends where the strip begins", bx1 == sx0)
check("the body starts at the left edge", bx0 == 0)
check("the body and the strip do not overlap", bx1 <= sx0)
check("the body is below the caption row", by1 == H - rp.TITLE_H)

print("\n== the tabs run TOP-DOWN (descending y), not bottom-up ==")
tabs = box["tabs"]
check("all four tabs fit in an 800 px band", len(tabs) == 4)
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

print("\n== a band too short to hold four tabs DROPS them ==")
# TITLE_H + 2 * TAB_PITCH = 93, so 100 px has room for two tabs and not three.
short = rp.layout(W, 100)
check("a 100 px band lays out fewer than four tabs", len(short["tabs"]) < 4)
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
check("title_h=0 still lays out four tabs", len(hosted["tabs"]) == 4)
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
      all(h is not None for h in hits) and sorted(set(hits)) == [0, 1, 2, 3])

print("\n== icon_box: centred in its tab ==")
ib = rp.icon_box(tabs[0])
tx0b, ty0b, tx1b, ty1b = tabs[0]
check("the icon is TAB_ICON square",
      abs((ib[2] - ib[0]) - rp.TAB_ICON) < 1e-9
      and abs((ib[3] - ib[1]) - rp.TAB_ICON) < 1e-9)
check("the icon's centre is its tab's centre",
      abs((ib[0] + ib[2]) / 2 - (tx0b + tx1b) / 2) < 1e-9
      and abs((ib[1] + ib[3]) / 2 - (ty0b + ty1b) / 2) < 1e-9)
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

print("\n== rules() cannot drift from the constants ==")
r = rp.rules()
check("rules() reports the band width", r["band_w"] == rp.BAND_W)
check("rules() reports the tab names", r["tabs"] == rp.tab_names())
check("rules() records the screenshot's DPI scale",
      r["dpi_scale_of_screenshot"] == 1.75)
check("rules() reports the default tab", r["default_tab"] == rp.DEFAULT_TAB)

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("right dock band layout OK")
