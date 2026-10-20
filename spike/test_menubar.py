#!/usr/bin/env python3
"""Focused checks for the complete original Narwhal CAD menu model."""
import collections
import importlib
import importlib.util
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")

# Bare package: sibling command providers resolve without importing __init__ -> ui -> bpy.
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [ADDON]
pkg.__package__ = "narwhal3d"
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

# commands.py auto-loads Import and the exact solid providers. State loads the five
# remaining providers; import them explicitly to inventory the app-equivalent registry
# without constructing state.AppState or importing the UI.
commands = importlib.import_module("narwhal3d.commands")
REGISTRY_PROVIDERS = (
    "import_commands",
    "solid_commands",
    "file_commands",
    "print_commands",
    "export_commands",
    "display_commands",
    "layer_commands",
)
for provider in REGISTRY_PROVIDERS:
    importlib.import_module(f"narwhal3d.{provider}")

REGISTERED = frozenset(commands.COMMANDS)
EXPECTED_MENUS = (
    "File", "Edit", "View", "Curve", "Surface", "SubD", "Solid", "Mesh", "Drafting",
    "Transform", "Analyze", "Render", "Tools", "Window", "Help",
)
EXPECTED_RUNNABLE_COUNTS = {
    "File": 8,
    "Edit": 47,
    "View": 11,
    "Curve": 45,
    "Surface": 12,
    "SubD": 0,
    "Solid": 18,
    "Mesh": 0,
    "Drafting": 1,
    "Transform": 15,
    "Analyze": 13,
    "Render": 0,
    "Tools": 8,
    "Window": 0,
    "Help": 1,
}
EXPECTED_SERVICE_TODOS = frozenset({
    "recoverysettings", "newfromtemplate", "saveacopy", "documentnotes",
    "gettingstarted", "inputreference", "onlinedocumentation", "showbugreport",
    "aboutnarwhal3d",
})
SIBLING_PINS = {
    "import", "filletedge", "saveas", "print", "export", "setdisplaymode",
    "changelayer",
}

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


print("\n-- live registry and authored structure --")
check(len(REGISTERED) == 179, "the bare-package live registry has 179 commands",
      repr(len(REGISTERED)))
check(SIBLING_PINS <= REGISTERED,
      "commands unique to every sibling provider are present",
      repr(sorted(SIBLING_PINS - REGISTERED)))
check(menubar.menu_names() == EXPECTED_MENUS,
      "the complete top-level structure is pinned", repr(menubar.menu_names()))
check(menubar.resource_menu_names() == EXPECTED_MENUS,
      "the renderer and dictionary tree expose the same menus")
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
runnable = [action for action in actions if action[3] in REGISTERED]
todos = [action for action in actions if action[2].endswith(resource.TODO_SUFFIX)]
specials = [action for action in actions if action[3] in resource.SPECIAL_COMMANDS]
ambiguous = [action for action in actions
             if action[3] not in resource.SPECIAL_COMMANDS
             and (action[3] in REGISTERED) == action[2].endswith(resource.TODO_SUFFIX)]

print("\n-- exact runnable/TODO partition --")
counts = collections.Counter(command for _menu, _path, _label, command in runnable)
check(set(counts) == REGISTERED and all(count == 1 for count in counts.values()),
      "every registered command appears exactly once",
      repr({"missing": sorted(REGISTERED - set(counts)),
            "duplicates": sorted(key for key, value in counts.items() if value != 1)}))
check(not ambiguous,
      "every leaf is exactly one of runnable or explicit TODO", repr(ambiguous[:5]))
check(len(runnable) + len(todos) + len(specials) == len(actions)
      and len(specials) == 1 and specials[0][3] == "__recent_files__",
      "runnable, TODO, and implemented special leaves partition the action set")
todo_payloads = {command for _menu, _path, _label, command in todos}
toolbar_commands = {
    record.get("command")
    for tab in resource.toolbar_data.TABS for record in tab["buttons"]
    if record.get("command")
}
check(len(todos) == 361 and EXPECTED_SERVICE_TODOS <= todo_payloads
      and toolbar_commands - REGISTERED <= todo_payloads,
      "all public toolbar-only commands and service gaps are explicit menu leaves",
      repr({"todos": len(todos),
            "missing_toolbar": sorted((toolbar_commands - REGISTERED) - todo_payloads),
            "missing_services": sorted(EXPECTED_SERVICE_TODOS - todo_payloads)}))
check(all(command and command == command.casefold()
          and re.fullmatch(r"[a-z0-9_]+", command)
          and command not in REGISTERED
          for _menu, _path, _label, command in todos),
      "every TODO has a canonical non-empty unregistered command key")
check(all(label.endswith("(Not implemented)")
          and label.count("(Not implemented)") == 1
          for _menu, _path, label, _command in todos),
      "every TODO label visibly ends with one marker")

runnable_by_menu = collections.Counter(menu for menu, _path, _label, _command in runnable)
check(dict(runnable_by_menu) == {k: v for k, v in EXPECTED_RUNNABLE_COUNTS.items() if v},
      "implemented commands stay in their pinned top-level categories",
      repr(dict(runnable_by_menu)))
check(sum(EXPECTED_RUNNABLE_COUNTS.values()) == len(REGISTERED),
      "the pinned category counts account for the whole registry")
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
check(menubar.coverage(REGISTERED) == (len(runnable), len(actions)),
      "registry coverage counts runnable leaves without treating TODOs or specials as live")
check(menubar.coverage(set()) == (0, len(actions)),
      "an empty registry disables every action")

print("\n-- renderer compatibility and originality --")
check(menubar.resource_rows("curve") == menubar.resource_rows("Curve"),
      "top-level lookup remains case-insensitive")
check(menubar.resource_rows("Curve", ("Modify",)),
      "popup paths remain addressable")
check(menubar.resource_rows("No Such Menu") == [] and
      menubar.resource_rows("Curve", ("No Such Group",)) == [],
      "unknown renderer paths answer empty")
check(menubar.submenu(("No Such Menu",)) is None and
      menubar.menu_rows(("No Such Menu",), REGISTERED) == [],
      "unknown dictionary paths answer empty")
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
    "the reference CAD application" + "Core",
    "." + "rui",
    "vendor/" + "reference CAD application-help",
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
check(resource.REPORT["source"] == "original",
      "the resource records original authorship")

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
print(f"MENUBAR TESTS PASSED ({checks} checks, {len(runnable)} runnable, "
      f"{len(todos)} TODO)")
