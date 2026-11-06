#!/usr/bin/env python3
"""the `Purge` command: only unused entries go, and one Undo brings them back.

Run (with the native history core, as run_tests.sh sets it up):
  tools/blender-patched/5.2/python/bin/python3.13 spike/test_purge.py

Drives the REAL registered command on a real NarwhalContext and its native
history; only the Blender display hooks are stubbed.

Covered:
  * Layers: an empty layer is purged, a layer with objects and the current layer
    are kept; one Undo restores it even when the purge renumbered other objects'
    layers, and Redo purges it again;
  * Groups: a group with no members is purged, groups with members are kept and
    every remaining object is still in the same groups (by name);
  * one Undo restores the purged group and its table position, Redo purges again;
  * a second Purge with nothing unused says so and records nothing;
  * Block Definitions and the other unbuilt types refuse by name, nothing removed.

Loaded by file path (no Blender): the same trick as spike/test_visibility.py.
"""
import importlib.util
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "narwhal3d")
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [ADDON]
pkg.__package__ = "narwhal3d"
sys.modules["narwhal3d"] = pkg


def _load(name, relpath):
    path = os.path.join(ADDON, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bridge = _load("narwhal3d.bridge", "bridge.py")
model = _load("narwhal3d.model", "model.py")
_load("narwhal3d.options", "options.py")
commands = _load("narwhal3d.commands", "commands.py")
bridge.refresh_display_objects = lambda ctx, changes: True   # no display to refresh

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


def real_ctx():
    ctx = commands.NarwhalContext(model.NarwhalModel())
    ctx.selected_objects = lambda: []
    ctx.rebuild_display = lambda: None
    ctx.select_by_names = lambda names: None
    return ctx


def purge(ctx, option):
    cmd = type(commands.COMMANDS["purge"])()
    cmd.begin(ctx)
    return cmd.feed(option, ctx)


def group_names(ctx):
    return [ctx.model.file.Groups[i].Name for i in range(len(ctx.model.file.Groups))]


def memberships(ctx):
    names = group_names(ctx)
    return {o.Attributes.Name: sorted(names[i] for i in (o.Attributes.GetGroupList2() or []))
            for o in ctx.model.file.Objects}


def grouped_doc():
    """Groups Used1, Empty, Used2, Spare; P1 in Used1 and Used2, P2 in Used2, P3 in none."""
    ctx = real_ctx()
    for name in ("Used1", "Empty", "Used2", "Spare"):
        g = r3.Group()
        g.Name = name
        ctx.model.file.Groups.Add(g)
    for name, x in (("P1", 0), ("P2", 1), ("P3", 2)):
        ctx.model.add_object(bridge.make_point((x, 0, 0)), name)
    for fobj in ctx.model.file.Objects:
        for gi in {"P1": (0, 2), "P2": (2,), "P3": ()}[fobj.Attributes.Name]:
            fobj.Attributes.AddToGroup(gi)
    return ctx


print()
print("Purge is registered and declares the ten types")
cmd = commands.COMMANDS.get("purge")
check(cmd is not None and len(cmd.options) == 10, "Purge is registered with ten options")

print()
print("Layers: only an empty, non-current layer goes")
# "Empty" sits BEFORE "Full", so purging it renumbers the object on "Full" -- the case
# whose Undo used to be refused as an unmediated object write.
ctx = real_ctx()
for name in ("Empty", "Full"):
    layer = r3.Layer()
    layer.Name = name
    ctx.model.file.Layers.Add(layer)
full = next(i for i in range(len(ctx.model.file.Layers)) if ctx.model.file.Layers[i].Name == "Full")
ctx.model.add_object(bridge.make_point((0, 0, 0)), "P", full)


def layer_names(ctx):
    return [ctx.model.file.Layers[i].Name for i in range(len(ctx.model.file.Layers))]


def layer_of_p(ctx):
    fobj = next(o for o in ctx.model.file.Objects if o.Attributes.Name == "P")
    return ctx.model.file.Layers[int(fobj.Attributes.LayerIndex)].Name


msg, done = purge(ctx, "Layers")
check(done and msg == "Purged 1 unused layer(s)" and layer_names(ctx) == ["Default", "Full"]
      and layer_of_p(ctx) == "Full",
      f"the empty layer is purged, the used one kept and P still on it "
      f"({layer_names(ctx)}, {msg!r})")
try:
    ctx.undo_last()
    undone = layer_names(ctx)
except Exception as exc:  # noqa: BLE001 -- the bug: the registry refused the snapshot
    undone = f"{type(exc).__name__}: {exc}"
check(undone == ["Default", "Empty", "Full"] and layer_of_p(ctx) == "Full",
      f"Undo restores the purged layer, P still on Full ({undone})")
try:
    ctx.redo_last()
    redone = layer_names(ctx)
except Exception as exc:  # noqa: BLE001
    redone = f"{type(exc).__name__}: {exc}"
check(redone == ["Default", "Full"] and layer_of_p(ctx) == "Full",
      f"Redo purges it again ({redone})")

print()
print("Groups: only a group with no members goes")
ctx = grouped_doc()
before = memberships(ctx)
depth = len(ctx.undo._undo)
msg, done = purge(ctx, "Groups")
check(done and msg == "Purged 2 unused group(s)", f"two memberless groups are purged ({msg!r})")
check(group_names(ctx) == ["Used1", "Used2"], f"the used groups remain, in order ({group_names(ctx)})")
check(memberships(ctx) == before,
      f"every object is still in the same groups by name ({memberships(ctx)})")
check(len(ctx.undo._undo) == depth + 1, "one Undo record")
try:
    ctx.undo_last()
    undone = group_names(ctx)
except Exception as exc:  # noqa: BLE001 -- a registry refusal must report, not crash
    undone = f"{type(exc).__name__}: {exc}"
check(undone == ["Used1", "Empty", "Used2", "Spare"] and memberships(ctx) == before,
      f"Undo restores the purged groups and every membership ({undone})")
try:
    ctx.redo_last()
    redone = group_names(ctx)
except Exception as exc:  # noqa: BLE001
    redone = f"{type(exc).__name__}: {exc}"
check(redone == ["Used1", "Used2"] and memberships(ctx) == before,
      f"Redo purges them again ({redone})")
depth = len(ctx.undo._undo)
msg, done = purge(ctx, "Groups")
check(done and msg == "Purge Groups: no unused groups to delete" and len(ctx.undo._undo) == depth,
      f"nothing unused: says so, records nothing ({msg!r})")
ctx = real_ctx()
msg, done = purge(ctx, "Groups")
check(done and "no unused groups" in msg, f"an empty group table: nothing to do ({msg!r})")

print()
print("Block Definitions and the unbuilt types refuse by name")
for option in ("Block Definitions", "Annotation Styles", "Bitmaps", "Environments",
               "Hatch Patterns", "Linetypes", "Materials", "Textures"):
    ctx = grouped_doc()
    msg, done = purge(ctx, option)
    check(done and f"the {option} option is not implemented yet" in msg
          and group_names(ctx) == ["Used1", "Empty", "Used2", "Spare"],
          f"{option}: refused by name, nothing removed ({msg!r})")
ctx = grouped_doc()
msg, _done = purge(ctx, "Block Definitions")
check("cannot delete" in msg, f"Block Definitions says why ({msg!r})")

print()
print("=" * 60)
if failures:
    print(f"{len(failures)} of {checks} CHECKS FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print(f"PURGE TESTS PASSED ({checks} checks)")
