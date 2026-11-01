"""Line: the reference CAD application's prompts and the Angled / FourPoint / Bisector modes.

MEASURED (docs/measured/research_all_13/notes/082-Line.md, every point typed):

* prompts `Start of line` / `End of line`; with BothSides the first is `Middle of line`;
  BothSides from (0,0,0) to (4,0,0) is (-4,0,0)-(4,0,0);
* Angled: base (0,0,0)-(4,0,0), `Pivot angle` 45 in the FRONT CPlane, end pick projected
  onto the angled direction -> (0,0,0)-(1,0,1), length sqrt 2;
* FourPoint: base (0,0,0)-(4,0,0), start (1,0,0), end (7,0,0) -> (1,0,0)-(7,0,0);
* Bisector: (0,0,0), (2,0,0), (0,2,0), end (3,3,0) -> (0,0,0)-(3,3,0), length 3 sqrt 2;
* a zero-length line re-prompts `End of line` with NO message and builds nothing.

The Angled end is fed as world (2,0,0): the reference CAD application's typed end (2,2,0) lay off the Front CPlane
(the note's own FourPoint attempt shows inputs landing on it), and (2,0,0) projects onto
the 45-degree direction at exactly sqrt 2, the measured length.

Run: tools/blender-patched/5.2/python/bin/python3.13 spike/test_line_modes.py
"""
import importlib.util
import math
import os
import sys
import types

import rhino3dm as r3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [os.path.join(ROOT, "narwhal3d")]
sys.modules["narwhal3d"] = pkg


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cplane = _load("narwhal3d.cplane", "narwhal3d/cplane.py")
bridge = _load("narwhal3d.bridge", "narwhal3d/bridge.py")
model = _load("narwhal3d.model", "narwhal3d/model.py")
_load("narwhal3d.options", "narwhal3d/options.py")
commands = _load("narwhal3d.commands", "narwhal3d/commands.py")

checks = []


def check(ok, label):
    checks.append((bool(ok), label))
    print(("  ok   " if ok else "  FAIL ") + label)


class Ctx:
    def __init__(self, plane=None):
        self.model = model.NarwhalModel()
        self.added = []
        self.plane = plane

    def add_geometry(self, geom, base_name, layer_index=None):
        self.added.append(geom)
        name = f"{base_name}{len(self.added)}"
        return name, name

    def active_cplane(self):
        return self.plane

    def selected_objects(self):
        return []

    def note_change(self, label=None):
        pass


def run(lines, plane=None):
    ctx = Ctx(plane)
    cmd = commands.LineCmd()
    msg, done = cmd.begin(ctx)
    msgs = [msg]
    prompts = [cmd.prompt]
    for line in lines:
        if done:
            break
        msg, done = cmd.feed(line, ctx)
        msgs.append(msg)
        prompts.append(cmd.prompt)
    return ctx, cmd, msgs, prompts, done


def ends(geom):
    return [tuple(round(c, 9) + 0.0 for c in (p.X, p.Y, p.Z))
            for p in (geom.PointAtStart, geom.PointAtEnd)]


def base(prompt):
    return prompt.split(" (")[0].rstrip(":")


ctx, cmd, msgs, prompts, done = run(["0,0,0", "4,0,0"])
check([base(p) for p in prompts[:2]] == ["Start of line", "End of line"],
      f"plain Line prompts Start/End of line ({prompts[:2]})")
check(done and ends(ctx.added[0]) == [(0, 0, 0), (4, 0, 0)], "plain line (0,0,0)-(4,0,0)")

ctx, cmd, msgs, prompts, done = run(["BothSides=Yes", "0,0,0", "4,0,0"])
check(base(prompts[1]) == "Middle of line", f"BothSides: 'Middle of line' ({prompts[1]!r})")
check(done and ends(ctx.added[0]) == [(-4, 0, 0), (4, 0, 0)], "BothSides (-4,0,0)-(4,0,0)")

ctx, cmd, msgs, prompts, done = run(["Angled", "w0,0,0", "w4,0,0", "45", "w2,0,0"],
                                   plane=cplane.WORLD_FRONT)
check([base(p) for p in prompts[1:5]] ==
      ["Start of base line", "End of base line", "Pivot angle", "End of line"],
      f"Angled prompts ({[base(p) for p in prompts[1:5]]})")
g = ctx.added[0] if ctx.added else None
check(done and g is not None and ends(g) == [(0, 0, 0), (1, 0, 1)]
      and abs(g.PointAtStart.DistanceTo(g.PointAtEnd) - math.sqrt(2)) < 1e-12,
      f"Angled 45 in Front: (0,0,0)-(1,0,1), sqrt 2 ({g and ends(g)})")
ctx, *_ = run(["Angled", "0,0,0", "4,0,0", "90", "3"])
check(ctx.added and ends(ctx.added[0]) == [(0, 0, 0), (0, 3, 0)],
      "Angled 90 in Top with a typed length 3: along +Y")

ctx, cmd, msgs, prompts, done = run(["FourPoint", "0,0,0", "4,0,0", "1,0,0", "7,0,0"])
check([base(p) for p in prompts[1:5]] ==
      ["Start of base line", "End of base line", "Start of line", "End of line"],
      f"FourPoint prompts ({[base(p) for p in prompts[1:5]]})")
check(done and ends(ctx.added[0]) == [(1, 0, 0), (7, 0, 0)], "FourPoint (1,0,0)-(7,0,0)")
ctx, *_ = run(["FourPoint", "0,0,0", "4,0,0", "1,2,0", "7,5,0"])
check(ctx.added and ends(ctx.added[0]) == [(1, 2, 0), (7, 2, 0)],
      "FourPoint: point 4 is projected onto the base direction from point 3")

ctx, cmd, msgs, prompts, done = run(["Bisector", "0,0,0", "2,0,0", "0,2,0", "3,3,0"])
check([base(p) for p in prompts[1:5]] ==
      ["Start of bisector line", "Start of angle to bisect", "End of angle to bisect",
       "End of line"], f"Bisector prompts ({[base(p) for p in prompts[1:5]]})")
g = ctx.added[0] if ctx.added else None
check(done and g is not None and ends(g) == [(0, 0, 0), (3, 3, 0)]
      and abs(g.PointAtStart.DistanceTo(g.PointAtEnd) - 3 * math.sqrt(2)) < 1e-12,
      f"Bisector (0,0,0)-(3,3,0), 3 sqrt 2 ({g and ends(g)})")

ctx, cmd, msgs, prompts, done = run(["5,5,0", "5,5,0"])
check(not done and msgs[-1] is None and not ctx.added and base(cmd.prompt) == "End of line",
      f"zero length re-prompts End of line silently ({msgs[-1]!r}, {cmd.prompt!r})")
msg, done = cmd.feed("6,5,0", ctx)
check(done and ends(ctx.added[0]) == [(5, 5, 0), (6, 5, 0)], "...and the next end builds")

failed = [label for ok, label in checks if not ok]
print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
