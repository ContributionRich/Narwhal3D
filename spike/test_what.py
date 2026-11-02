"""the `What` command -- report the object's details.

MEASURED (commands/what.htm): "Select objects. Information about the object and
its layer will display." One OBJECTS_MULTI step; the report is TEXT -- the same
rows the Properties panel's Details button shows, built by `bridge.object_details`
from the EXACT geometry (master or recipe), never the display proxy.

This drives it through `WhatCmd` (test_pipe_cmd.py's `Ctx`/`run` shape) on a box
and a circle: the report contains `Faces: 6` for the box, and the circle's degree
and control-point count. The selection path (select first, then run) is checked
alongside the typed-name path.

Run: PYTHONPATH=vendor/occt tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13 spike/test_what.py
"""
import importlib.util
import os
import sys
import types

import rhino3dm as r3  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = pkg


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


class Stub:
    """A selected-object stand-in: `What` only reads its `.name`."""

    def __init__(self, name):
        self.name = name


class Ctx:
    """The named document objects plus a (possibly non-empty) selection."""

    def __init__(self, named, selected=()):
        self.model = model.NarwhalModel()
        self._selected = [Stub(nm) for nm in selected]
        for nm, g in named:
            self.model.add_object(g, nm, 0)

    def add_geometry(self, geom, base_name, layer_index=None):
        return None, f"{base_name}_x"

    def geom_by_name(self, name):
        for o in self.model.file.Objects:
            if o.Attributes.Name == name:
                return o.Geometry
        return None

    def object_by_name(self, name):
        return None

    def selected_objects(self):
        return list(self._selected)

    def active_cplane(self):
        return None

    def note_change(self, label=None):
        pass


def run_typed(named, selected, names):
    """Drive `What` through the TYPED-NAME path (feed names, then Enter)."""
    ctx = Ctx(named, selected)
    c = type(commands.COMMANDS["what"])()
    c.begin(ctx)
    last, done = None, False
    for line in names + [""]:
        last, done = c.feed(line, ctx)
        if done:
            break
    return last, done, ctx


def run_selected(named, selected):
    """Drive `What` through the SELECTION path (select first, then run)."""
    ctx = Ctx(named, selected)
    c = type(commands.COMMANDS["what"])()
    msg, done = c.begin(ctx)
    return msg, done, ctx


BOX = bridge.make_box((0, 0, 0), (10, 10, 0), 10)
CIRCLE = bridge.make_circle((0, 0, 0), 5.0)

print("-- the command exists --")
check("what" in commands.COMMANDS, "What is registered in the COMMANDS registry")
check(type(commands.COMMANDS["what"])().prompt.startswith(
    "Select objects for displaying detailed object properties"),
    "the prompt is the reference CAD application's (MEASURED research_all_13 #185)")

print("\n-- a box: the report contains Faces: 6 --")
msg, done, ctx = run_typed([("Box", BOX), ("Circle", CIRCLE)], (), ["Box"])
check(done, f"the typed-name path runs on the confirm Enter ({msg!r} / done={done})")
check(msg is not None and "Faces: 6" in msg, f"the box report contains 'Faces: 6' ({msg!r})")
check("Box:" in msg, f"the report is labelled with the object's name ({msg!r})")
check("Type: polysurface" in msg and "Volume: 1000" in msg,
      f"the box report carries its type and volume ({msg!r})")

print("\n-- a circle: the report carries its degree and control-point count --")
msg, done, ctx = run_typed([("Box", BOX), ("Circle", CIRCLE)], (), ["Circle"])
check(done, f"the circle runs on the confirm Enter ({msg!r} / done={done})")
check(msg is not None and "Degree: 2" in msg, f"the circle report contains 'Degree: 2' ({msg!r})")
check(msg is not None and "Control points: rational, count=9" in msg,
      f"the circle report carries its control-point count ({msg!r})")
check(msg is not None and "Shape: circle" in msg and "Radius: 5" in msg,
      f"the circle report is a NURBS circle of radius 5 ({msg!r})")

print("\n-- the selection path: select first, then run --")
msg, done, ctx = run_selected([("Box", BOX), ("Circle", CIRCLE)], ("Box", "Circle"))
check(done, f"the selection path runs on the naming Enter ({msg!r} / done={done})")
check(msg is not None and "Faces: 6" in msg,
      f"the selection report (Box) contains 'Faces: 6' ({msg!r})")
check(msg is not None and "Degree: 2" in msg,
      f"the selection report (Circle) contains 'Degree: 2' ({msg!r})")
check(msg is not None and "Box:" in msg and "Circle:" in msg,
      f"both selected objects are reported, each labelled ({msg!r})")

print("\n-- a missing name is reported, not a crash --")
msg, done, ctx = run_typed([("Box", BOX)], (), ["Nope"])
check(done, f"a missing object runs and reports ({msg!r} / done={done})")
check(msg is not None and "Nope" in msg and "no object by that name" in msg,
      f"a missing name says so by name ({msg!r})")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    for f_ in failures:
        print("FAIL:", f_)
    sys.exit(1)
print("(What command) PASSED")
