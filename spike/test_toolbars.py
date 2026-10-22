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
check(len(implemented) == 184, "the app-equivalent registry has 184 commands", len(implemented))
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
check((enabled, total) == (119, 525),
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
      == ("Line: Normal To Surface",)
      and toolbars.sidebar_tooltip_lines(circle_rows[1], "flyout")
      == ("Circle: diameter",),
      "flyout hover shows the authored variant description, not the shared command")
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
check(point_tip == ("Point",),
      "sidebar hover shows only the canonical command name")
check(toolbars.sidebar_tooltip_lines(point_button, "flyout", "Point") ==
      ("Point",),
      "the arrow corner keeps the same compact command-name tooltip")
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
      == toolbars.FLYOUT_MARGIN * 2 + toolbars.FLYOUT_COLUMNS * toolbars.FLYOUT_CELL,
      "floating toolbar starts outside the dock and is exactly five cells wide")
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
check(bundled == set(data.ICON_NAMES), "runtime icon directory exactly matches the manifest",
      sorted(bundled ^ set(data.ICON_NAMES)))
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

print("\n" + "=" * 70)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print(f"NARWHAL TOOLBAR TESTS PASSED ({checks} checks)")
