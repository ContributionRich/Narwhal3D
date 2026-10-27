#!/usr/bin/env python3
"""Focused pure tests for Narwhal 3D's complete desktop toolbar workspace."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_DIR = os.path.join(ROOT, "narwhal3d")

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [PACKAGE_DIR]
sys.modules["narwhal3d"] = pkg


def _load(name: str, relpath: str) -> Any:
    path = os.path.join(ROOT, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


data: Any = _load("narwhal3d.toolbar_data", "narwhal3d/toolbar_data.py")
variant_icons: Any = sys.modules["narwhal3d.flyout_icon_data"]
toolbars: Any = _load("narwhal3d.toolbars", "narwhal3d/toolbars.py")
generator: Any = _load("narwhal3d.icon_generator", "narwhal3d/icon_generator.py")
builder: Any = _load("narwhal_icon_builder", "scripts/build_toolbar_icons.py")

checks = 0
failures: list[str] = []


def check(condition: object, label: str, detail: object = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f" ({detail})" if detail else ""))
        failures.append(label)


def live_command_names() -> set[str]:
    commands = importlib.import_module("narwhal3d.commands")
    for provider in (
        "import_commands", "solid_commands", "file_commands", "print_commands",
        "export_commands", "display_commands", "layer_commands",
    ):
        importlib.import_module(f"narwhal3d.{provider}")
    return set(commands.COMMANDS)


def all_records():
    for tab in data.TABS:
        yield from tab["buttons"]
    yield from data.SIDEBAR["buttons"]
    for rows in data.FLYOUTS.values():
        yield from rows


def png_size(path: str | Path) -> tuple[int, int] | None:
    with open(path, "rb") as stream:
        blob = stream.read(24)
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", blob[16:24])


print("\n1. Complete desktop toolbar arrangement")
names = toolbars.tab_names()
expected_names = (
    "Standard", "CPlanes", "Set View", "Display", "Select", "Viewport Layout",
    "Visibility", "Transform", "Curve Tools", "Surface Tools", "Solid Tools",
    "SubD Tools", "Mesh Tools", "Render", "Drafting",
)
check(toolbars.container_name() == "Narwhal 3D", "container uses the Narwhal 3D identity")
check(names == expected_names, "top-tab order is complete and pinned", names)
check(toolbars.default_tab() == "Standard", "Standard is the default top toolbar")
check(len(names) == len(set(names)), "top toolbar names are unique")
check(sum(len(tab["buttons"]) for tab in data.TABS) == 582,
      "the fifteen top toolbars retain all 582 authored slots")

implemented = live_command_names()
rows = [button for name in names for button in toolbars.tab_rows(name, implemented)]
check(len(implemented) == 186, "the app-equivalent registry has 186 commands", len(implemented))
check(len(rows) == 582, "every top slot reaches the stable Button API")
check(sum(button.is_separator for button in rows) == 57,
      "57 intentional top-toolbar spacers remain real separators")
check(all(button.is_separator or (button.text and button.tooltip and button.left)
          for button in rows), "every non-separator is named and clickable")
check(all(button.enabled or "not implemented yet" in button.hover_text()
          for button in rows if not button.is_separator),
      "every unavailable top action explains itself on hover")
parametric = [button for button in rows if button.left == "parametrictools"]
check(len(parametric) == 1 and parametric[0].tooltip == "Parametric tools"
      and not parametric[0].enabled,
      "the generic parametric slot remains vendor-neutral and explicitly unavailable")
enabled, total = toolbars.coverage(implemented)
check((enabled, total) == (120, 525),
      "coverage counts repeated runnable slots and explicit unavailable slots", (enabled, total))

print("\n2. Exact 37-cell left palette and linked flyouts")
sidebar = toolbars.sidebar_buttons(implemented)
expected_sidebar = (
    "cancel", "point", "polyline", "curve", "circle", "ellipse", "arc", "rectangle",
    "polygon", "fillet", "srfpt", "filletsrf", "box", "booleanunion", "project",
    "mesh", "join", "explode", "trim", "split", "group", "ungroup", "editpton",
    "pointson", "textobject", "move", "copy", "rotate", "scale", "dir", "array",
    "align", "flowalongsrf", "pushpull", "check", "block", "history",
)
check(toolbars.sidebar_name() == "Narwhal Tools", "left palette uses the product name")
check(tuple(button.left for button in sidebar) == expected_sidebar,
      "left palette has the exact 37 actions in display order")
check(all(button.icon and button.left for button in sidebar),
      "every left-palette cell has an icon and action")
check(sum(bool(button.right) for button in sidebar) == 16,
      "sixteen left cells retain alternate right-click actions")
linked = [button for button in sidebar if button.link]
check(len(linked) == 28 and len({button.link for button in linked}) == 28,
      "28 left cells expose distinct selectable flyouts")
check(all(toolbars.sidebar_flyout_rows(button, implemented) for button in linked),
      "every arrow-marked left cell opens a populated flyout")
circle_button = next(button for button in linked if button.link == "Circle")
circle_rows = toolbars.sidebar_flyout_rows(circle_button, implemented)
check(tuple(row.text for row in circle_rows) == (
    "Circle: center, radius", "Circle: diameter", "Circle: 3 points",
    "Circle around curve", "Circle: tangent, tangent, radius",
    "Circle tangent to 3 curves", "Circle vertical to CPlane: center, radius",
    "Circle vertical to CPlane: diameter", "Circle: deformable", "Circle: fit points"),
      "Circle flyout preserves ten distinct authored actions in order")
check(toolbars.activation_lines(circle_rows[0]) == ("circle",)
      and toolbars.activation_lines(circle_rows[1])
      == ("circle", "Radius/Diameter=Diameter")
      and toolbars.activation_lines(circle_rows[2]) == ("circle", "3Point")
      and toolbars.activation_lines(circle_rows[4]) == ("circle", "Tangent"),
      "implemented Circle flyout variants retain their distinct command presets")
check(tuple(index for index, row in enumerate(circle_rows) if row.enabled)
      == (0, 1, 2, 4, 5),
      "unimplemented Circle variants are visibly disabled instead of running bare Circle")
curve_button = next(button for button in linked if button.link == "Curve")
curve_rows = toolbars.sidebar_flyout_rows(curve_button, implemented)
coil_rows = {row.left: row for row in curve_rows if row.left in ("helix", "spiral")}
check(set(coil_rows) == {"helix", "spiral"}
      and all(row.enabled for row in coil_rows.values())
      and toolbars.activation_lines(coil_rows["helix"]) == ("helix",)
      and toolbars.activation_lines(coil_rows["spiral"]) == ("spiral",),
      "Curve flyout exposes live Helix and Spiral actions")
mapped_rows = {}
for flyout_name in sorted({key[0] for key in variant_icons.FLYOUT_SOURCES}):
    flyout = types.SimpleNamespace(link=flyout_name)
    mapped_rows[flyout_name] = [
        row for row in toolbars.sidebar_flyout_rows(flyout, implemented)
        if (flyout_name, row.text) in variant_icons.FLYOUT_SOURCES
    ]
check(len(mapped_rows) == 16 and sum(map(len, mapped_rows.values())) == 90,
      "all 90 collapsed slots across sixteen flyouts have explicit icon mappings")
check(all(len(rows) == len({row.icon for row in rows})
          for rows in mapped_rows.values()),
      "every mapped variant has its own stable runtime icon name")
check(all(row.icon == variant_icons.icon_for(name, row.text)
          for name, rows in mapped_rows.items() for row in rows),
      "flyout rows apply only their exact authored slot mapping")
line_rows = mapped_rows["Lines"]
check(toolbars.sidebar_tooltip_lines(line_rows[2], "flyout")
      == ("Line: Normal To Surface", "right-click: Surface normal, both sides")
      and toolbars.sidebar_tooltip_lines(circle_rows[1], "flyout")
      == ("Circle: diameter",),
      "flyout hover preserves authored variants and their alternate descriptions")
source_payloads = {}
for (flyout_name, text), source_name in variant_icons.FLYOUT_SOURCES.items():
    digest = hashlib.sha256(Path(ROOT, "artwork", "icons", source_name).read_bytes()).digest()
    source_payloads.setdefault((flyout_name, digest), []).append(
        variant_icons.icon_for(flyout_name, text))
duplicate_source_groups = [
    icons for icons in source_payloads.values() if len(icons) > 1
]
check(sum(map(len, duplicate_source_groups)) == 12
      and all(set(icons) <= set(variant_icons.ICON_BADGES)
              and len({variant_icons.ICON_BADGES[icon] for icon in icons}) == len(icons)
              for icons in duplicate_source_groups),
      "byte-identical authored variants receive explicit distinct runtime badges")
variant_hashes = {
    name: {hashlib.sha256(Path(PACKAGE_DIR, "icons", row.icon).read_bytes()).digest()
           for row in rows}
    for name, rows in mapped_rows.items()
}
check(all(len(variant_hashes[name]) == len(rows)
          for name, rows in mapped_rows.items()),
      "every mapped row in each flyout renders visibly distinct runtime artwork",
      {name: (len(variant_hashes[name]), len(rows))
       for name, rows in mapped_rows.items()})
retained_button = linked[0]
retained_index = sidebar.index(retained_button)
rebuilt_sidebar = toolbars.sidebar_buttons(implemented)
check(rebuilt_sidebar[retained_index] is not retained_button
      and toolbars.sidebar_button_index(rebuilt_sidebar, retained_button,
                                         retained_index) == retained_index,
      "deferred flyout anchor survives a sidebar redraw rebuilding Button objects")
check(toolbars.sidebar_button_index(rebuilt_sidebar, retained_button, 999)
      == retained_index,
      "stable button identity recovers when a preferred anchor index is stale")
check(toolbars.sidebar_flyout_rows(sidebar[0], implemented) == [],
      "an unlinked left cell does not invent a flyout")
point_button = next(button for button in linked if button.link == "Point")
point_tip = toolbars.sidebar_tooltip_lines(point_button, "command", "Point")
check(point_tip == (point_button.tooltip, "right-click: " + point_button.right_tooltip,
                    "◢ opens the Point toolbar"),
      "sidebar hover includes authored primary, alternate and actual flyout link", point_tip)
check(toolbars.sidebar_tooltip_lines(point_button, "flyout", "Point") == point_tip,
      "the arrow corner keeps the same full description as the command surface")

# Synthetic variants keep availability tests independent of registry additions.
for left_enabled in (False, True):
    for right_enabled in (False, True):
        variant = toolbars.Button("Short text", "Circle: diameter", "circle", "points",
                                  "Multiple points", None, left_enabled, right_enabled,
                                  link="Actual linked category")
        expected = ("Circle: diameter" + ("" if left_enabled else " - not implemented yet"),
                    "right-click: Multiple points" +
                    ("" if right_enabled else " - not implemented yet"),
                    "◢ opens the Actual linked category toolbar")
        check(all(toolbars.sidebar_tooltip_lines(variant, part, "Circle") == expected
                  for part in ("command", "flyout")),
              f"authored variant/link win; independent availability {left_enabled}/{right_enabled}")

fallback = toolbars.Button("Text fallback", "", "rawcommand", "rawright", "", None,
                           True, False)
check(toolbars.sidebar_tooltip_lines(fallback, command_name="Canonical") ==
      ("Text fallback", "right-click: rawright - not implemented yet"),
      "text precedes canonical fallback and bare right command remains described")
fallback.text = ""
check(toolbars.sidebar_tooltip_lines(fallback, command_name="Canonical") ==
      ("Canonical", "right-click: rawright - not implemented yet")
      and toolbars.sidebar_tooltip_lines(fallback) ==
      ("rawcommand", "right-click: rawright - not implemented yet"),
      "missing authored text uses optional canonical then raw command fallback")
fallback.right = None
fallback.right_tooltip = "Authored alternate without a command"
check(toolbars.sidebar_tooltip_lines(fallback) ==
      ("rawcommand", "right-click: Authored alternate without a command - not implemented yet"),
      "prototype alternate help survives even without a runnable right command")
fallback.right_tooltip = ""
check(toolbars.sidebar_tooltip_lines(fallback) == ("rawcommand",),
      "absent alternate and link do not invent extra lines")
check(toolbars.sidebar_tooltip_lines(None) == ()
      and toolbars.sidebar_tooltip_lines(
          toolbars.Button("", "", None, None, "", None, False, False)) == (),
      "missing buttons and empty separator records have no description")
tip_box = toolbars.sidebar_tooltip_layout(
    (0, 20.0, 100.0, 48.0, 128.0), 60.0, 1600.0, 900.0)
check(tip_box is not None and tip_box[0] == 48.0 + toolbars.SIDEBAR_TOOLTIP_GAP
      and 100.0 <= tip_box[1] < tip_box[3] <= 128.0,
      "compact tooltip is placed beside and vertically near its icon")
clamped_tip = toolbars.sidebar_tooltip_layout(
    (0, 1580.0, 2.0, 1600.0, 30.0), 120.0, 1600.0, 900.0)
check(clamped_tip is not None and clamped_tip[0] >= 0.0 and clamped_tip[1] >= 0.0
      and clamped_tip[2] <= 1600.0 and clamped_tip[3] <= 900.0,
      "compact tooltip remains inside the application window")
flyout_buttons = toolbars.sidebar_flyout_rows(point_button, implemented)
icon_groups = toolbars.flyout_icon_groups(flyout_buttons)
flattened = [button for group in icon_groups for row in group for button in row]
check(flattened == [button for button in flyout_buttons if not button.is_separator],
      "icon popup preserves every non-separator flyout action in order")
check(all(1 <= len(row) <= toolbars.FLYOUT_COLUMNS
          for group in icon_groups for row in group),
      "icon popup wraps into compact bounded rows")
separated_button = next(button for button in linked
                        if any(row.is_separator
                               for row in toolbars.sidebar_flyout_rows(button, implemented)))
check(len(toolbars.flyout_icon_groups(
          toolbars.sidebar_flyout_rows(separated_button, implemented))) > 1,
      "icon popup preserves authored separator groups")
popup = toolbars.flyout_popup_layout(117, 700, flyout_buttons, 1600, 900)
check(toolbars.FLYOUT_COLUMNS == 5 and toolbars.FLYOUT_OVERLAP == 0.0
      and popup["panel"][0] == 117
      and popup["panel"][2] - popup["panel"][0]
      == toolbars.FLYOUT_MARGIN * 2 + toolbars.FLYOUT_COLUMNS * toolbars.FLYOUT_CELL
      + (toolbars.FLYOUT_COLUMNS - 1) * toolbars.FLYOUT_GAP,
      "floating toolbar starts outside the dock and has five cells plus v7 gaps")
check(toolbars.FLYOUT_CELL == 32 and toolbars.FLYOUT_ICON == 22 and toolbars.FLYOUT_GAP == 2,
      'v7 flyouts use native-sized 32px buttons, 22px icons and 2px gaps')
check(len(popup["cells"]) == len(popup["buttons"]) == len(flattened),
      "floating toolbar allocates one hit box for every displayed icon")
first_cell = popup["cells"][0]
first_center = ((first_cell[1] + first_cell[3]) * 0.5,
                (first_cell[2] + first_cell[4]) * 0.5)
check(toolbars.hit_flyout(*first_center, popup["cells"]) == 0,
      "floating toolbar hit-testing selects its first icon")
clamped = toolbars.flyout_popup_layout(1595, 10, flyout_buttons, 1600, 900)
check(clamped["panel"][0] >= 0 and clamped["panel"][1] >= 0
      and clamped["panel"][2] <= 1600 and clamped["panel"][3] <= 900,
      "floating toolbar remains entirely inside the application window")

print("\n3. Contributor artwork and deterministic fallback manifest")
record_icons = {record["icon"] for record in all_records() if record.get("icon")}
check(record_icons == data.TOOLBAR_ICON_NAMES,
      "the toolbar manifest covers top, left, and flyout records")
check(set(data.ICON_NAMES) == set(generator.icon_specs()),
      "the placeholder generator covers the complete runtime manifest")
check(len(data.ICON_NAMES) == 632, "632 distinct runtime symbols are manifested")
check(len(data.PANEL_ICON_NAMES) == 5,
      "the five right-dock tabs have project-owned icon slots")
check(all(name.startswith("narwhal-") and name.endswith(".png")
          and name == os.path.basename(name) for name in data.ICON_NAMES),
      "all runtime icon names are safe project basenames")

matched, missing = builder.source_matches()
expected_missing = {
    "Flyout Analyze: Analyze curve deviation",
    "Flyout Block: Update all linked blocks",
    "Standard: Parametric tools",
    "Visibility: Hide A", "Visibility: Hide B", "Visibility: Hide C",
}
check(len(matched) == 626, "626 runtime symbols use contributor artwork", len(matched))
check(set(data.MISSING_ARTWORK) == expected_missing,
      "the exact six source-art gaps are explicit", data.MISSING_ARTWORK)
check(set(missing) == {name for name, source in data.ICON_SOURCES.items() if not source},
      "only the six declared gaps use generated placeholders", missing)
check({data.ICON_SOURCES[name] for name in data.PANEL_ICON_NAMES} ==
      {"layer.png", "properties.png", "panel_display.png", "bugreport.png", "help.png"},
      "right-dock tabs use contributed artwork, with Help temporary for Assistant")
check(all((Path(builder.SOURCE) / source).is_file() for source in matched.values()),
      "every mapped source PNG is bundled")
check(all(png_size(Path(builder.SOURCE) / source) == (96, 96)
          for source in matched.values()), "every used source icon is 96x96 PNG")
license_text = (Path(builder.SOURCE) / "README.md").read_text(encoding="utf-8")
check("GPL-3.0-or-later" in license_text and "ContributionRich" in license_text,
      "source artwork carries its contributor and GPL licence declaration")

print("\n4. Runtime icon safety and visual replacement")
check(toolbars.icons_available(), "every declared runtime icon is bundled")
bundled = {path.name for path in Path(PACKAGE_DIR, "icons").glob("*.png")}
expected_v4 = {
    "v4-panel-properties.png", "v4-panel-display.png", "v4-panel-ai-assistant.png",
    "v4-viewport-maximize.png", "v4-viewport-restore.png",
}
check(toolbars.V4_ICON_NAMES == expected_v4, "v4 allowlist is exactly the five approved icons")
check(bundled == set(data.ICON_NAMES) | expected_v4,
      "runtime icon directory exactly matches legacy manifest plus five v4 assets",
      sorted(bundled ^ (set(data.ICON_NAMES) | expected_v4)))
check(all(toolbars.icon_path(name) and png_size(toolbars.icon_path(name)) == (96, 96)
          for name in expected_v4), "all five v4 icons resolve as 96x96 PNGs")
resolved = [toolbars.icon_path(name) for name in sorted(data.ICON_NAMES)]
icon_root = os.path.realpath(os.path.join(PACKAGE_DIR, "icons"))
check(all(path and os.path.commonpath((icon_root, os.path.realpath(path))) == icon_root
          for path in resolved), "every runtime icon resolves only under narwhal3d/icons")
check(all(png_size(path) == (24, 24) for path in resolved),
      "every runtime symbol is a valid-looking 24x24 PNG")
with tempfile.TemporaryDirectory() as generated_dir:
    generated = {Path(path).name: Path(path).read_bytes()
                 for path in generator.write_icons(generated_dir)}
    contributed_differ = all(
        generated[name] != Path(icon_root, name).read_bytes() for name in matched
    )
    placeholders_match = all(
        generated[name] == Path(icon_root, name).read_bytes() for name in missing
    )
check(contributed_differ, "all mapped artwork replaces its generated placeholder")
check(placeholders_match, "only declared missing artwork retains generated symbols")
known = next(iter(sorted(data.ICON_NAMES)))
check(toolbars.icon_path(None) is None, "None does not resolve")
check(toolbars.icon_path("narwhal-not-manifested.png") is None,
      "unknown project-looking filenames do not resolve")
check(toolbars.icon_path("../" + known) is None, "parent traversal is rejected")

print("\n5. Pure tab and responsive left-palette layout")
widths = [len(name) * 7.0 for name in names]
wide = toolbars.tab_row_boxes(names, widths, 10000.0)
check([box[0] for box in wide] == list(names), "a wide row lays out every top category")
limited = toolbars.tab_row_boxes(names, widths, wide[1][2])
check(len(limited) == 2 and toolbars.tabs_hidden(names, limited) == len(names) - 2,
      "clipping reports hidden categories")
columns = toolbars.sidebar_columns(117)
cells = toolbars.sidebar_cells(len(sidebar), 0.0, 300.0, columns=columns)
check(columns == 4 and len(cells) == 37, "left palette forms the measured four-column grid")
check(toolbars.sidebar_rows(len(sidebar), columns) == 10,
      "37 cells occupy ten rows at default width")
linked_index = next(index for index, button in enumerate(sidebar) if button.link)
linked_cell = cells[linked_index]
center = ((linked_cell[1] + linked_cell[3]) / 2.0,
          (linked_cell[2] + linked_cell[4]) / 2.0)
check(toolbars.sidebar_action_at(*center, cells, sidebar) == (linked_index, "command"),
      "a linked cell center runs its primary action")
check(toolbars.sidebar_action_at(linked_cell[3] - 1, linked_cell[2] + 1,
                                 cells, sidebar) == (linked_index, "flyout"),
      "the linked cell corner opens its flyout")

print("\n6. Category strip and contextual left toolbar (UI design)")
check(all(toolbars.icon_path(toolbars.category_icon(name)) for name in names),
      "every category has a bundled, safe icon")
standard = toolbars.context_buttons("Standard", implemented)
check([b.text for b in standard] == [b.text for b in sidebar],
      "Standard shows the favourite drawing palette in the left panel")
transform = toolbars.context_buttons("Transform", implemented)
check(transform and not any(b.is_separator for b in transform)
      and [b.text for b in transform]
      == [b.text for b in toolbars.tab_rows("Transform", implemented) if not b.is_separator],
      "another category shows exactly its own tools, without separators")
check(toolbars.context_buttons("No Such Category", implemented) == [],
      "an unknown category shows nothing")
strip = toolbars.category_boxes(names, 1600, 80, gap=2, x0=5)
check(len(strip) == len(names) and strip[0] == ("Standard", 5.0, 85.0)
      and all(b[1] - a[2] == 2 for a, b in zip(strip, strip[1:])),
      "all fifteen category buttons fit a wide window, 80 px each, 2 px apart")
narrow = toolbars.category_boxes(names, 500, 80, gap=2, x0=5)
check(len(narrow) == 6 and toolbars.tabs_hidden(names, narrow) == 9,
      "a narrow window reports the categories that did not fit")
check(toolbars.context_grid(37, 120, 600, 35) == (35.0, 37),
      "the palette keeps its preferred 35 px cells when everything fits")
cell, shown = toolbars.context_grid(50, 120, 400, 35)
check(cell == 26.0 and shown == 45,
      "a long category shrinks to the minimum cell and reports what it cannot show")
cell, shown = toolbars.context_grid(50, 120, 500, 35)
check(shown == 50 and 26.0 <= cell < 35.0 and -(-50 // 3) * cell <= 500,
      "a long category shrinks just enough to fit")

print("\n7. v4 icon confinement (including allowed-name symlink escapes)")
with tempfile.TemporaryDirectory() as scratch:
    root = Path(scratch, "icons")
    root.mkdir()
    outside = Path(scratch, "outside.png")
    outside.write_bytes(b"not an icon")
    saved_dir = toolbars.ICON_DIR
    toolbars.ICON_DIR = str(root)
    try:
        for name in (known, "v4-panel-display.png"):
            candidate = root / name
            check(toolbars.icon_path(name) is None, f"missing allowed icon refused: {name}")
            candidate.mkdir()
            check(toolbars.icon_path(name) is None, f"directory refused: {name}")
            candidate.rmdir()
            candidate.symlink_to(outside)
            check(toolbars.icon_path(name) is None, f"allowed-name symlink escape refused: {name}")
            candidate.unlink()
            candidate.write_bytes(b"present")
            check(toolbars.icon_path(name) == str(candidate), f"confined regular file resolves: {name}")
            check(toolbars.icon_path(str(candidate)) is None, f"absolute path refused: {name}")
        unknown = root / "v4-not-approved.png"
        unknown.write_bytes(b"present")
        check(toolbars.icon_path(unknown.name) is None, "existing unknown v4 basename is refused")
        check(all(toolbars.icon_path(value) is None for value in
                  ([], 1, "../" + known, "icons/" + known, "..\\" + known)),
              "non-string and nested/traversal names remain refused")
    finally:
        toolbars.ICON_DIR = saved_dir

print("\n8. Opt-in fixed-size scrolling layouts")
cat = toolbars.category_scroll_layout(["A", "A", "C"], (10, 20, 210, 80), 90)
check((cat["offset"], cat["max_offset"], cat["content_extent"], cat["total"]) == (60, 60, 260, 3),
      "category extent includes both 7px pads, fixed80 buttons and 3px gaps; offset clamps")
check(cat["cells"] == ((0, -43, 27, 37, 73), (1, 40, 27, 120, 73), (2, 123, 27, 203, 73))
      and cat["hit_cells"][0] == (0, 10, 27, 37, 73),
      "category boxes translate without shrinking; partially visible button is clipped only for hits")
check(toolbars.hit_scroll(5, 40, cat) is None and toolbars.hit_scroll(38, 40, cat) is None
      and toolbars.hit_scroll(70, 40, cat) == 1 and toolbars.hit_scroll(203, 40, cat) is None,
      "category hits respect clipping, gaps, half-open boundaries and duplicate original indices")
check(toolbars.category_scroll_layout(names, (0, 0, 200, 60), -20)["offset"] == 0
      and toolbars.category_scroll_layout(names, (0, 0, 2000, 60), 999)["offset"] == 0,
      "negative and stale wide-resize category offsets clamp to zero")
check(toolbars.category_scroll_layout(["A", "B"], (0, 0, 177, 60))["max_offset"] == 0,
      "two categories exactly fit including padding, without a phantom trailing gap")
seen = set()
for offset in range(1060):
    scroll = toolbars.category_scroll_layout(names, (0, 0, 200, 60), offset)
    seen.update(i for i, x0, y0, x1, y1 in scroll["hit_cells"]
                if toolbars.hit_scroll((x0+x1)/2, (y0+y1)/2, scroll) == i)
check(seen == set(range(len(names))), "scrolling makes all fifteen categories hittable, not just first slots")

grid = toolbars.context_scroll_layout(50, (10, 20, 129, 120), 9999)
check((grid["offset"], grid["max_offset"], grid["content_extent"], grid["content_width"],
       grid["max_x_offset"], grid["total"]) == (551, 551, 651, 119, 0, 50),
      "50 tools keep three fixed35 columns, 3px gaps, 4px padding and 551px vertical range")
check(grid["cells"] and grid["hit_cells"]
      and grid["cells"][0] == (42, 14, 100, 49, 135)
      and grid["hit_cells"][0] == (42, 14, 100, 49, 120)
      and grid["cells"][-1] == (49, 52, 24, 87, 59),
      "scrolled grid keeps original index42 at top and final tool49 fully reachable at bottom")
check(toolbars.hit_scroll(20, 125, grid) is None
      and toolbars.hit_scroll(50, 110, grid) is None,
      "clipped-off top and 3px column gap never activate a tool")
check(toolbars.context_scroll_layout(6, (0, 0, 119, 81))["max_offset"] == 0
      and toolbars.context_scroll_layout(6, (0, 0, 119, 80))["max_offset"] == 1,
      "grid exact fit and one-pixel overflow include bottom padding but no trailing gap")
check(toolbars.context_scroll_layout(50, (0, 0, 119, 100), -5)["offset"] == 0
      and toolbars.context_scroll_layout(1, (0, 0, 119, 100), 999)["offset"] == 0,
      "negative and stale category-switch offsets clamp")
seen, fixed, matched_hits = set(), True, True
for offset in range(552):
    scroll = toolbars.context_scroll_layout(50, (0, 0, 119, 100), offset)
    fixed &= all(x1-x0 == 35 and y1-y0 == 35 for i, x0, y0, x1, y1 in scroll["cells"])
    for i, x0, y0, x1, y1 in scroll["hit_cells"]:
        seen.add(i)
        matched_hits &= toolbars.hit_scroll((x0+x1)/2, (y0+y1)/2, scroll) == i
check(seen == set(range(50)) and fixed and matched_hits,
      "all fifty fixed-size tools are reachable; every visible box picks its original index")
seen = set()
for offset in range(81):
    for horizontal in range(100):
        scroll = toolbars.context_scroll_layout(6, (0, 0, 20, 20), offset, x_offset=horizontal)
        seen.update(entry[0] for entry in scroll["hit_cells"])
check(seen == set(range(6)) and scroll["max_x_offset"] == 99
      and toolbars.context_scroll_layout(6, (0, 0, 20, 20), x_offset=999)["x_offset"] == 99
      and toolbars.context_scroll_layout(6, (0, 0, 20, 20), x_offset=-1)["x_offset"] == 0,
      "even a 20px-wide viewport can scroll to every original column without shrinking")
linked_buttons = [types.SimpleNamespace(link="Circle")]
partial = toolbars.context_scroll_layout(1, (0, 0, 20, 40))
check(toolbars.context_scroll_action_at(19, 6, partial, linked_buttons) == (0, "command")
      and toolbars.context_scroll_action_at(37, 6, partial, linked_buttons) is None,
      "clipping neither relocates a flyout corner nor enables invisible pixels")
partial = toolbars.context_scroll_layout(1, (0, 0, 20, 40), x_offset=20)
check(toolbars.context_scroll_action_at(18, 6, partial, linked_buttons) == (0, "flyout"),
      "scrolling the real flyout corner into view makes it actionable")
for build in (lambda box, count: toolbars.category_scroll_layout(range(count), box, 999),
              lambda box, count: toolbars.context_scroll_layout(count, box, 999)):
    empty = build((0, 0, 119, 100), 0)
    check(empty["total"] == 0 and empty["cells"] == empty["hit_cells"] == ()
          and empty["offset"] == empty["max_offset"] == empty["content_extent"] == 0,
          "empty scroll content clears stale state")
    check(all(build(box, 4)["cells"] == build(box, 4)["hit_cells"] == ()
              for box in ((0, 0, 0, 100), (0, 0, 119, 0), (10, 10, 0, 0))),
          "zero/reversed viewport never draws or hits degenerate cells")
scaled = toolbars.context_scroll_layout(6, (0, 0, 238, 162), cell=70, gap=6, pad=8)
check(scaled["max_offset"] == 0 and scaled["cells"][0] == (0, 8, 84, 78, 154),
      "explicit physical-pixel metrics preserve the 2x layout without hidden scaling")
for call in (lambda: toolbars.category_scroll_layout(names, (0, 0, 200, 60), button_w=0),
             lambda: toolbars.context_scroll_layout(3, (0, 0, 119, 100), cell=0),
             lambda: toolbars.context_scroll_layout(3, (0, 0, 119, 100), columns=1.5)):
    try:
        call()
    except ValueError:
        check(True, "invalid fixed metrics refused instead of dropping cells")
    else:
        check(False, "invalid fixed metrics refused instead of dropping cells")

print('\n9. Visible scroll indicator')
long_grid = toolbars.context_scroll_layout(50, (0, 0, 126, 200))
bar = toolbars.scroll_indicator(long_grid)
check(bar is not None and bar['thumb'][3] == bar['track'][3],
      'overflow shows a thumb at the top before scrolling')
check(bar['thumb'][3] - bar['thumb'][1] < bar['track'][3] - bar['track'][1],
      'thumb length indicates hidden content')
bottom_grid = toolbars.context_scroll_layout(50, (0, 0, 126, 200), 99999)
bottom_bar = toolbars.scroll_indicator(bottom_grid)
check(abs(bottom_bar['thumb'][1] - bottom_bar['track'][1]) < 1e-9,
      'the thumb reaches the bottom when the last tools are visible')
check(toolbars.scroll_indicator(toolbars.context_scroll_layout(3, (0, 0, 126, 200))) is None,
      'no misleading scrollbar when all tools fit')

print("\n10. HTMLv7 pointer tooltip and soft background timing")
check(toolbars.hover_weight(-1) == toolbars.hover_weight(0) == 0.0
      and toolbars.hover_weight(.12) == toolbars.hover_weight(1) == 1.0,
      "120ms hover transition clamps at both ends")
check(.7 < toolbars.hover_weight(.06) < .9
      and toolbars.hover_weight(.03, .06) == toolbars.hover_weight(.06),
      "midpoint is eased, not a binary or linear jump; custom duration rescales time")
weights = [toolbars.hover_weight(i * .001) for i in range(121)]
check(all(0 <= a <= b <= 1 for a, b in zip(weights, weights[1:]))
      and max(b - a for a, b in zip(weights, weights[1:])) < .03,
      "background progress is monotone and smooth through the whole transition")
check(toolbars.hover_weight(0, 0) == toolbars.hover_weight(1, -1) == 1,
      "nonpositive animation duration finishes immediately")
box = toolbars.tooltip_box_at((100, 200), [40, 80, 60], 3, (800, 600))
check(box is not None and box[0] == 114 and box[2] == 212
      and abs(box[3] - 186) < 1e-9 and abs(box[1] - 129.2) < 1e-9,
      "tooltip anchors 14px right/down with longest-line width and three padded lines")
scaled_box = toolbars.tooltip_box_at((200, 400), [80, 160, 120], 3, (1600, 1200), 2)
check(all(abs(a * 2 - b) < 1e-9 for a, b in zip(box, scaled_box)),
      "2x scale doubles CSS metrics without double-scaling measured text widths")
check(toolbars.tooltip_box_at((790, 10), [80], 1, (800, 600)) ==
      (702, 0, 800, 25.6), "right/bottom edge clamps instead of overflowing")
upper = toolbars.tooltip_box_at((-100, 1000), [80], 1, (800, 600))
check(upper[0] == 0 and upper[3] == 600,
      "left/top edge clamps stale off-window anchors")
check(toolbars.tooltip_box_at((20, 20), [1000], 100, (40, 30)) == (0, 0, 40, 30)
      and toolbars.tooltip_box_at((20, 20), [1000], 100, (0, 0)) == (0, 0, 0, 0),
      "oversized descriptions and zero-size windows stay bounded")
check(toolbars.tooltip_box_at(None, [80], 1, (800, 600)) is None
      and toolbars.tooltip_box_at((20, 20), [], 0, (800, 600)) is None,
      "no captured pointer or no description produces no tooltip box")

print("\n" + "=" * 70)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print(f"NARWHAL TOOLBAR TESTS PASSED ({checks} checks)")
