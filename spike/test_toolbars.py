#!/usr/bin/env python3
"""Focused pure tests for Narwhal 3D's original temporary toolbar layer."""
from __future__ import annotations

import ast
from collections.abc import Iterator
import importlib.util
import os
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
toolbars: Any = _load("narwhal3d.toolbars", "narwhal3d/toolbars.py")
generator: Any = _load("narwhal3d.icon_generator", "narwhal3d/icon_generator.py")

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


def core_command_names() -> set[str]:
    """Read names of classes actually listed in commands._build_registry."""
    path = os.path.join(PACKAGE_DIR, "commands.py")
    with open(path, encoding="utf-8") as stream:
        tree = ast.parse(stream.read(), filename=path)
    class_commands: dict[str, str] = {}
    registry_classes: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if (isinstance(item, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "name"
                                for target in item.targets)
                        and isinstance(item.value, ast.Constant)
                        and isinstance(item.value.value, str)):
                    class_commands[node.name] = item.value.value.lower()
        if isinstance(node, ast.FunctionDef) and node.name == "_build_registry":
            for child in ast.walk(node):
                if isinstance(child, ast.For) and isinstance(child.iter, ast.List):
                    registry_classes.update(
                        item.id for item in child.iter.elts if isinstance(item, ast.Name))
    return {class_commands[name] for name in registry_classes if name in class_commands}


def all_records() -> Iterator[dict[str, object]]:
    for tab in data.TABS:
        yield from tab["buttons"]
    yield from data.SIDEBAR["buttons"]


print("\n1. Original compact content")
names = toolbars.tab_names()
check(toolbars.container_name() == "Narwhal 3D", "container uses the Narwhal 3D identity")
check(5 <= len(names) <= 8, "the top strip has a compact number of categories", names)
check({"File", "Draw", "Edit", "Transform", "Solid", "View"}.issubset(names),
      "categories cover the main modeling workflows", names)
check(len(names) == len(set(names)), "category names are unique")
check(toolbars.default_tab() in names, "the default category is valid")
check(all(toolbars.tab_rows(name, set()) for name in names), "every category contains actions")

primary = [record for tab in data.TABS for record in tab["buttons"]]
commands = [record["command"] for record in primary]
core = core_command_names()
check(len(primary) < 100, "the temporary toolbar stays deliberately small", len(primary))
check(len(commands) == len(set(commands)), "each primary action appears in one category")
check(set(commands) <= core, "every toolbar action is in the implemented core registry",
      sorted(set(commands) - core))
check(not any(not record["text"] or not record["tooltip"] for record in primary),
      "every action has an original label and hover description")

print("\n2. Stable Button API, hover text, right actions, and flyouts")
implemented = set(commands)
rows = [button for name in names for button in toolbars.tab_rows(name, implemented)]
required_attrs = ("text", "tooltip", "left", "right", "right_tooltip", "icon",
                  "link", "enabled", "right_enabled", "label")
check(rows and all(all(hasattr(button, attr) for attr in required_attrs) for button in rows),
      "Button retains every field consumed by the host UI")
check(all(button.enabled for button in rows), "all primary category actions are runnable")
check(all(button.text.lower() in button.hover_text().lower() for button in rows),
      "hover text identifies every primary action")
check(all(not button.enabled and "not implemented yet" in button.hover_text()
          for name in names for button in toolbars.tab_rows(name, set())),
      "availability remains explicit when given an empty registry")

sidebar = toolbars.sidebar_buttons(implemented)
check(toolbars.sidebar_name() == "Narwhal Tools", "the left sidebar has a project-owned name")
check(12 <= len(sidebar) <= 24, "the favorites sidebar is compact but useful", len(sidebar))
check(all(button.icon and button.left for button in sidebar), "every favorite has an icon and action")
righties = [button for button in sidebar if button.right]
check(righties and all(button.right_enabled for button in righties),
      "practical alternate right-click actions remain runnable")
check(all("right-click:" in button.hover_text() for button in righties),
      "hover text identifies every alternate action")
linked = [button for button in sidebar if button.link]
check(set(button.link for button in linked) == set(names),
      "favorites expose one flyout for every category")
check(all(toolbars.sidebar_flyout_rows(button, implemented) for button in linked),
      "every linked favorite opens a populated flyout")
check(toolbars.sidebar_flyout_rows(sidebar[-1], implemented) == [],
      "an unlinked favorite does not invent a flyout")

enabled, total = toolbars.coverage(implemented)
check((enabled, total) == (len(primary), len(primary)), "coverage reports all compact actions")
check(toolbars.tab_rows("Not A Category", implemented) == [],
      "an unknown category remains harmless")

print("\n3. Bundled Narwhal icon manifest and generator")
record_icons: set[str] = {str(record["icon"]) for record in all_records()}
check(record_icons == data.TOOLBAR_ICON_NAMES,
      "the toolbar icon manifest covers every toolbar record")
all_icons = set(data.ICON_NAMES)
check(all_icons == set(generator.icon_specs()),
      "the generator covers toolbar and right-panel manifests")
check(len(data.PANEL_ICON_NAMES) == 4,
      "the right dock declares four project-owned placeholder icons")
check(all(name.startswith("narwhal-") and name.endswith(".png")
          and name == os.path.basename(name) for name in all_icons),
      "all icon names are explicitly project-owned safe basenames")
check(toolbars.icons_available(), "every declared icon is bundled")
bundled_names = {name for name in os.listdir(os.path.join(PACKAGE_DIR, "icons"))
                 if name.endswith(".png")}
check(bundled_names == all_icons, "the icon directory contains only manifested PNGs",
      sorted(bundled_names ^ all_icons))

resolved = [toolbars.icon_path(name) for name in sorted(all_icons)]
icon_root = os.path.realpath(os.path.join(PACKAGE_DIR, "icons"))
check(all(path and os.path.commonpath((icon_root, os.path.realpath(path))) == icon_root
          for path in resolved), "every icon resolves only under narwhal3d/icons")
check(len(set(resolved)) == len(all_icons), "each manifest name resolves to one file")

png_ok = True
for path in resolved:
    with open(path, "rb") as stream:
        blob = stream.read(24)
    png_ok = png_ok and blob[:8] == b"\x89PNG\r\n\x1a\n"
    png_ok = png_ok and struct.unpack(">II", blob[16:24]) == (24, 24)
check(png_ok, "every bundled icon is a valid-looking 24x24 PNG")

with tempfile.TemporaryDirectory() as generated_dir:
    generated = generator.write_icons(generated_dir)
    reproducible = len(generated) == len(all_icons)
    for path in generated:
        committed = os.path.join(icon_root, os.path.basename(path))
        reproducible = reproducible and open(path, "rb").read() == open(committed, "rb").read()
check(reproducible, "bundled PNGs exactly match the stdlib generator")
check(len({open(path, "rb").read() for path in resolved}) == len(resolved),
      "temporary action symbols are visually distinct at the byte level")



print("\n4. Missing and unsafe icon resolution")
known = next(iter(sorted(record_icons)))
check(toolbars.icon_path(None) is None, "None does not resolve")
check(toolbars.icon_path("narwhal-not-in-the-manifest.png") is None,
      "an unknown project-looking filename does not resolve")
check(toolbars.icon_path("../" + known) is None, "parent traversal is rejected")
check(toolbars.icon_path(os.path.join(icon_root, known)) is None, "absolute paths are rejected")
check(toolbars.icon_path("vendor/" + known) is None, "prefixed external paths are rejected")

original_dir = toolbars.ICON_DIR
try:
    with tempfile.TemporaryDirectory() as empty_dir:
        toolbars.ICON_DIR = empty_dir
        check(toolbars.icon_path(known) is None, "a missing manifested icon returns None")
    with tempfile.TemporaryDirectory() as safe_dir, tempfile.TemporaryDirectory() as outside:
        target = os.path.join(outside, known)
        open(target, "wb").write(b"outside")
        os.symlink(target, os.path.join(safe_dir, known))
        toolbars.ICON_DIR = safe_dir
        check(toolbars.icon_path(known) is None, "a symlink escaping the icon root is rejected")
finally:
    toolbars.ICON_DIR = original_dir

print("\n5. Pure tab and sidebar layout")
widths = [len(name) * 7.0 for name in names]
wide = toolbars.tab_row_boxes(names, widths, 10000.0)
check([box[0] for box in wide] == list(names), "a wide row lays out every category")
check(all(wide[index][2] == wide[index + 1][1]
          for index in range(len(wide) - 1)), "tab hit boxes abut without gaps")
limited = toolbars.tab_row_boxes(names, widths, wide[1][2])
check(len(limited) == 2 and toolbars.tabs_hidden(names, limited) == len(names) - 2,
      "clipping reports hidden categories")
mid_y = 100.0 - toolbars.TAB_ROW_H / 2.0
check(toolbars.hit_tab_row((wide[0][1] + wide[0][2]) / 2.0, mid_y, wide, 100.0)
      == names[0], "tab hit-testing selects the visible box")
check(toolbars.hit_tab_row(wide[0][2], mid_y, wide, 100.0) == names[1],
      "a shared tab edge belongs to exactly one box")

columns = toolbars.sidebar_columns(117)
cells = toolbars.sidebar_cells(len(sidebar), 0.0, 300.0, columns=columns)
check(columns == 4 and len(cells) == len(sidebar), "favorites reflow into a four-column grid")
check(toolbars.sidebar_rows(len(sidebar), columns) * toolbars.SIDEBAR_CELL
      == toolbars.sidebar_height(len(sidebar), columns), "favorites height follows row count")
linked_index = next(index for index, button in enumerate(sidebar) if button.link)
linked_cell = cells[linked_index]
center = ((linked_cell[1] + linked_cell[3]) / 2.0,
          (linked_cell[2] + linked_cell[4]) / 2.0)
check(toolbars.sidebar_action_at(*center, cells, sidebar) == (linked_index, "command"),
      "the center of a linked favorite runs its primary action")
check(toolbars.sidebar_action_at(linked_cell[3] - 1, linked_cell[2] + 1,
                                 cells, sidebar) == (linked_index, "flyout"),
      "the linked favorite's corner opens its category flyout")
check(toolbars.hit_sidebar(cells[0][3], center[1], cells) == 1,
      "a shared favorites edge belongs to exactly one cell")

print("\n" + "=" * 70)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print(f"NARWHAL TOOLBAR TESTS PASSED ({checks} checks)")
