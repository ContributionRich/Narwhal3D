#!/usr/bin/env python3
"""Structural and live-registry checks for the renderer-facing menu rows."""
import collections
import importlib
import importlib.util
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")

pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [ADDON]
pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = pkg


def _load():
    path = os.path.join(ADDON, "menubar_resource_data.py")
    spec = importlib.util.spec_from_file_location("narwhal3d.menubar_resource_data", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _registry():
    commands = importlib.import_module("narwhal3d.commands")
    for provider in (
        "import_commands", "solid_commands", "file_commands", "print_commands",
        "export_commands", "display_commands", "layer_commands",
    ):
        importlib.import_module(f"narwhal3d.{provider}")
    return frozenset(commands.COMMANDS)


data = _load()
registered = _registry()
results = []


def check(name):
    def decorate(fn):
        try:
            fn()
            results.append((name, "PASS", ""))
        except Exception as error:  # noqa: BLE001
            results.append((name, "FAIL", repr(error)))
    return decorate


def flat(rows, path=()):
    for row in rows:
        yield path, row
        if row[0] == "popup":
            yield from flat(row[3], path + (row[1],))


EXPECTED_MENUS = [
    "File", "Edit", "View", "Curve", "Surface", "SubD", "Solid", "Mesh", "Drafting",
    "Transform", "Analyze", "Render", "Tools", "Window", "Help",
]
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
EXPECTED_TODO_COUNTS = {
    "File": 5,
    "Edit": 40,
    "View": 60,
    "Curve": 33,
    "Surface": 35,
    "SubD": 34,
    "Solid": 21,
    "Mesh": 39,
    "Drafting": 32,
    "Transform": 18,
    "Analyze": 1,
    "Render": 33,
    "Tools": 3,
    "Window": 2,
    "Help": 5,
}
EXPECTED_SERVICE_TODOS = {
    "recoverysettings", "newfromtemplate", "saveacopy", "documentnotes",
    "gettingstarted", "inputreference", "onlinedocumentation", "showbugreport",
    "aboutnarwhal3d",
}


def leaves():
    return [(menu, path, row[1], row[3])
            for menu, rows in data.MENUS
            for path, row in flat(rows)
            if row[0] == "item"]


@check("top-level menu design is exact")
def _():
    assert [label for label, _rows in data.MENUS] == EXPECTED_MENUS


@check("every row has the renderer's compatible tuple shape")
def _():
    for menu, rows in data.MENUS:
        for path, row in flat(rows):
            assert isinstance(row, tuple), (menu, path, row)
            assert row[0] in {"item", "sep", "popup"}, (menu, path, row)
            assert isinstance(row[1], str) and isinstance(row[2], str), row
            if row[0] == "popup":
                assert len(row) == 5 and isinstance(row[3], tuple), row
                assert row[4] is None, row
            else:
                assert len(row) == 4, row
                if row[0] == "sep":
                    assert row[1:] == ("", "", None), row
                else:
                    assert isinstance(row[3], str) and row[3], row


@check("live bare-package registry includes all sibling providers")
def _():
    assert len(registered) == 179, len(registered)
    assert {
        "import", "filletedge", "saveas", "print", "export", "setdisplaymode",
        "changelayer",
    } <= registered


@check("all 179 registered commands appear exactly once")
def _():
    runnable = [command for _menu, _path, _label, command in leaves()
                if command in registered]
    counts = collections.Counter(runnable)
    assert set(counts) == registered, {
        "missing": sorted(registered - set(counts)),
        "extra": sorted(set(counts) - registered),
    }
    assert all(count == 1 for count in counts.values()), counts


@check("runnable and TODO leaves form an explicit partition")
def _():
    specials = []
    for menu, path, label, command in leaves():
        marked = label.endswith(data.TODO_SUFFIX)
        live = command in registered
        special = command in data.SPECIAL_COMMANDS
        assert sum((marked, live, special)) == 1, (menu, path, label, command, live)
        if special:
            specials.append((menu, path, label, command))
    assert specials == [("File", ("Recent and Recovery",),
                         "Recent Files", "__recent_files__")], specials


@check("TODO labels and canonical payloads are exact")
def _():
    todos = [(menu, label, command) for menu, _path, label, command in leaves()
             if label.endswith(data.TODO_SUFFIX)]
    payloads = {command for _menu, _label, command in todos}
    toolbar_commands = {
        record.get("command")
        for tab in data.toolbar_data.TABS for record in tab["buttons"]
        if record.get("command")
    }
    assert len(todos) == 361, len(todos)
    assert EXPECTED_SERVICE_TODOS <= payloads
    assert toolbar_commands - registered <= payloads
    for menu, label, command in todos:
        assert label.endswith("(Not implemented)"), (menu, label)
        assert label.count("(Not implemented)") == 1, (menu, label)
        assert command == command.casefold(), (menu, command)
        assert re.fullmatch(r"[a-z0-9_]+", command), (menu, command)
        assert command not in registered, (menu, command)


@check("implemented and TODO category counts are pinned")
def _():
    runnable = collections.Counter()
    todos = collections.Counter()
    for menu, _path, label, command in leaves():
        if command in registered:
            runnable[menu] += 1
        if label.endswith(data.TODO_SUFFIX):
            todos[menu] += 1
    assert {menu: runnable[menu] for menu in EXPECTED_MENUS} == EXPECTED_RUNNABLE_COUNTS
    assert {menu: todos[menu] for menu in EXPECTED_MENUS} == EXPECTED_TODO_COUNTS


@check("representative implemented and TODO mappings are pinned")
def _():
    indexed = {(menu, path, label): command for menu, path, label, command in leaves()}
    assert indexed[("File", ("Document",), "New Model")] == "new"
    assert indexed[("Edit", (), "Cut")] == "cut"
    assert indexed[("Edit", (), "Copy")] == "copytoclipboard"
    assert indexed[("Edit", (), "Paste")] == "paste"
    assert indexed[("Solid", ("Create and Edit",), "Fillet Edges")] == "filletedge"
    assert indexed[("Window", (), "Workspace Management (Not implemented)")] == "workspace"
    assert indexed[("File", ("Recent and Recovery",), "Recent Files")] == "__recent_files__"
    assert indexed[("Help", ("Support",), "Report a Bug (Not implemented)")] == "showbugreport"


@check("labels and sibling groups are unambiguous")
def _():
    def audit(rows):
        labels = [row[1].casefold() for row in rows if row[0] != "sep"]
        assert len(labels) == len(set(labels)), labels
        for row in rows:
            if row[0] == "popup":
                audit(row[3])
    for _menu, rows in data.MENUS:
        audit(rows)


@check("no action carries numeric dispatch data or shortcut text")
def _():
    for _menu, rows in data.MENUS:
        for _path, row in flat(rows):
            assert row[2] == "", row
            if row[0] == "item":
                assert isinstance(row[3], str) and row[3], row
                assert not row[3].isdigit(), row


@check("the expanded report recounts the authored tree")
def _():
    counts = {"items": 0, "separators": 0, "popups": 0}
    for _menu, rows in data.MENUS:
        for _path, row in flat(rows):
            key = {"item": "items", "sep": "separators", "popup": "popups"}[row[0]]
            counts[key] += 1
    assert data.REPORT == {
        **counts,
        "menus": len(EXPECTED_MENUS),
        "source": "original",
    }, (data.REPORT, counts)
    assert counts == {"items": 541, "separators": 16, "popups": 61}, counts


@check("resource source retains original provenance")
def _():
    path = os.path.join(ADDON, "menubar_resource_data.py")
    text = open(path, encoding="utf-8").read().casefold()
    forbidden = (
        "the reference CAD application" + "Core", "." + "rui", "vendor/" + "reference CAD application-help",
        "gen_" + "menubar_resource", "trans" + "cribed",
    )
    assert data.REPORT["source"] == "original"
    assert not [marker for marker in forbidden if marker.casefold() in text]


print("\n" + "=" * 62)
print(" Narwhal CAD menu resource model")
print("=" * 62)
width = max(len(name) for name, _status, _detail in results)
for name, status, detail in results:
    mark = "OK " if status == "PASS" else "FAIL"
    print(f" [{mark}] {name.ljust(width)}  {detail}")
failed = [result for result in results if result[1] != "PASS"]
print(f"\n {len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
