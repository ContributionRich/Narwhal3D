#!/usr/bin/env python3
"""Focused checks for the original Narwhal 3D menu model."""
import ast
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


resource = _load("narwhal3d.menubar_resource_data",
                 "narwhal3d/menubar_resource_data.py")
menu_data = _load("narwhal3d.menu_data", "narwhal3d/menu_data.py")
menubar = _load("narwhal3d.menubar", "narwhal3d/menubar.py")

checks = 0
failures = []


def check(condition, label, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}" + (f"  ({detail})" if detail else ""))


def walk_rows(rows, path=()):
    for row in rows:
        yield path, row
        if row[0] == "popup":
            yield from walk_rows(row[3], path + (row[1],))


def implemented_command_names():
    """Read class command names without importing the geometry stack."""
    path = os.path.join(ROOT, "narwhal3d", "commands.py")
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "name"
                       for target in stmt.targets):
                continue
            if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                if stmt.value.value:
                    names.add(stmt.value.value.lower())
    return names


EXPECTED_MENUS = (
    "File", "Edit", "View", "Draw", "Curves", "Surfaces", "Solids",
    "Transform", "Analyze", "Help",
)

print("\n-- authored menu structure --")
check(menubar.menu_names() == EXPECTED_MENUS,
      "the compact top-level structure is pinned", repr(menubar.menu_names()))
check(menubar.resource_menu_names() == EXPECTED_MENUS,
      "the renderer and legacy tree expose the same menus")
check(len(EXPECTED_MENUS) == len(set(EXPECTED_MENUS)),
      "top-level labels are unique")
check(all(menubar.submenu((name,)) for name in EXPECTED_MENUS),
      "every top-level menu has content")
check(menubar.not_on_the_bar() == {},
      "the authored design has no discarded compatibility groups")

all_rows = [(menu, path, row)
            for menu, rows in resource.MENUS
            for path, row in walk_rows(rows)]
actions = [(menu, path, row[1], row[3])
           for menu, path, row in all_rows if row[0] == "item"]
popups = [(menu, path, row) for menu, path, row in all_rows if row[0] == "popup"]

print("\n-- complete action resolution --")
implemented = implemented_command_names()
dead = [(menu, path, label, command) for menu, path, label, command in actions
        if not command or command.lower() not in implemented]
check(not dead, "every visible leaf names an implemented command", repr(dead[:5]))
check(len(actions) == resource.REPORT["items"] == menu_data.REPORT["actions"],
      "action counts agree across both public data shapes", repr(len(actions)))
check(all(row[4] is None for _menu, _path, row in popups),
      "group rows are navigation only, never hidden duplicate actions")

unresolved = []
for menu, rows in resource.MENUS:
    for path, row in walk_rows(rows):
        if row[0] != "item":
            continue
        siblings = menubar.resource_rows(menu, path)
        resolved = {id(source): command
                    for source, command, _option
                    in menubar.submenu_actions(siblings, None, ())}
        if resolved.get(id(row)) != row[3]:
            unresolved.append((menu, path, row[1], resolved.get(id(row))))
check(not unresolved, "submenu_actions resolves every shipped leaf", repr(unresolved[:5]))

legacy_actions = []


def walk_tree(items):
    for command, children in items.values():
        if command:
            legacy_actions.append(command)
        walk_tree(children)


for name in menu_data.MENU_BAR:
    walk_tree(menu_data.MENUS[name])
check(legacy_actions == [command for _menu, _path, _label, command in actions],
      "the dictionary tree preserves every action and authored order")
check(menubar.coverage(implemented) == (len(actions), len(actions)),
      "full registry coverage enables the complete compact menu")
check(menubar.coverage(set()) == (0, len(actions)),
      "an empty registry disables every action")

print("\n-- renderer compatibility and originality --")
check(menubar.resource_rows("draw") == menubar.resource_rows("Draw"),
      "top-level lookup remains case-insensitive")
check(menubar.resource_rows("Draw", ("Lines",)),
      "popup paths remain addressable")
check(menubar.resource_rows("No Such Menu") == [] and
      menubar.resource_rows("Draw", ("No Such Group",)) == [],
      "unknown renderer paths answer empty")
check(menubar.submenu(("No Such Menu",)) is None and
      menubar.menu_rows(("No Such Menu",), implemented) == [],
      "unknown legacy paths answer empty")
check(all(not row[2] for _menu, _path, row in all_rows),
      "no imported shortcut table survives")
check(all("&" not in row[1] for _menu, _path, row in all_rows),
      "authored labels contain no imported mnemonic marks")

source_files = (
    "narwhal3d/menu_data.py",
    "narwhal3d/menubar_resource_data.py",
    "narwhal3d/viewport_menu_data.py",
    "narwhal3d/menubar.py",
)
forbidden = (
    "Rhino" + "Core",
    "." + "rui",
    "vendor/" + "rhino-help",
    "gen_" + "menu_data",
    "gen_" + "menubar_resource",
    "gen_" + "viewport_menu",
    "trans" + "cribed",
)
contaminated = []
for relpath in source_files:
    text = open(os.path.join(ROOT, relpath), encoding="utf-8").read().casefold()
    for marker in forbidden:
        if marker.casefold() in text:
            contaminated.append((relpath, marker))
check(not contaminated, "menu sources contain no imported-data provenance markers",
      repr(contaminated))

# Pin the generic compatibility helpers used by the shell.
check(menubar.plain_label("Save && Close") == "Save & Close" and
      menubar.plain_label("&Open") == "Open",
      "plain_label retains its compatibility behavior")
synthetic = (
    ("sep", "", "", None),
    ("item", "A", "", "a"),
    ("sep", "", "", None),
    ("sep", "", "", None),
)
check(menubar.collapse_separators(synthetic) == (synthetic[1],),
      "separator cleanup remains stable")

print()
if failures:
    print(f"{len(failures)} of {checks} checks failed:")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print(f"MENUBAR TESTS PASSED ({checks} checks, {len(actions)} actions)")
