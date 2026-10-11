#!/usr/bin/env python3
"""Structural snapshot for Narwhal 3D's renderer-facing menu rows."""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    path = os.path.join(ROOT, "narwhal3d", "menubar_resource_data.py")
    spec = importlib.util.spec_from_file_location("narwhal_menu_rows", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


data = _load()
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
    "File", "Edit", "View", "Draw", "Curves", "Surfaces", "Solids",
    "Transform", "Analyze", "Help",
]

# Traversal order is part of the authored UI.  This tuple covers every action so a
# renamed, removed, reordered, or dead payload fails visibly.
EXPECTED_COMMANDS = (
    "open", "save",
    "undo", "redo", "delete", "join", "explode",
    "zoom", "cplane", "grid", "gumball", "ortho",
    "point", "points", "line", "polyline", "lines", "circle", "rectangle",
    "ellipse", "polygon", "arc", "picture",
    "curve", "interpcrv", "blendcrv", "closecrv", "offset", "extend", "divide",
    "split", "trim", "fillet", "chamfer", "rebuild", "changedegree",
    "insertknot", "dupedge", "dupborder", "project", "projecttocplane",
    "extractwireframe",
    "plane", "planarsrf", "edgesrf", "loft", "revolve", "extrudesrf", "offsetsrf",
    "box", "sphere", "cylinder", "cone", "extrudecrv", "pipe", "shell",
    "booleanunion", "booleandifference", "booleanintersection",
    "move", "copy", "rotate", "rotate3d", "scale", "scale1d", "scale2d",
    "mirror", "array", "arraypolar", "orient", "orient3pt",
    "distance", "angle", "length", "radius", "diameter", "area", "volume",
    "areacentroid", "volumecentroid", "boundingbox", "evaluatept", "dir", "what",
    "help",
)


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


@check("the complete action sequence and mapping are pinned")
def _():
    commands = tuple(row[3] for _menu, rows in data.MENUS
                     for _path, row in flat(rows) if row[0] == "item")
    assert commands == EXPECTED_COMMANDS, commands
    assert len(commands) == len(set(commands)), "one command was exposed twice"


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
                assert isinstance(row[3], str), row
                assert not row[3].isdigit(), row


@check("the compact report recounts the authored tree")
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


print("\n" + "=" * 62)
print(" Narwhal 3D menu resource model")
print("=" * 62)
width = max(len(name) for name, _status, _detail in results)
for name, status, detail in results:
    mark = "OK " if status == "PASS" else "FAIL"
    print(f" [{mark}] {name.ljust(width)}  {detail}")
failed = [result for result in results if result[1] != "PASS"]
print(f"\n {len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
