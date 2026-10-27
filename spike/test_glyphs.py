#!/usr/bin/env python3
"""Pure tests for the panel glyphs (narwhal3d/glyphs.py).

Each glyph is a triangle list that the Layers panel draws in one colour, so the state it
shows (eye open/closed, padlock locked/unlocked) must differ in SHAPE, and every glyph
must stay inside the box it was asked for or it would spill into the next column.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("glyphs", os.path.join(ROOT, "narwhal3d/glyphs.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

checks = 0
failures = []


def check(cond, label):
    global checks
    checks += 1
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


SIZE = 12.0
CX, CY = 100.0, 50.0
shapes = {
    "eye open": g.eye(CX, CY, SIZE, open_=True),
    "eye closed": g.eye(CX, CY, SIZE, open_=False),
    "lock locked": g.lock(CX, CY, SIZE, locked=True),
    "lock open": g.lock(CX, CY, SIZE, locked=False),
    "plus": g.plus(CX, CY, SIZE),
    "ellipsis": g.ellipsis(CX, CY, SIZE),
    "tick": g.tick(CX, CY, SIZE),
    "magnifier": g.magnifier(CX, CY, SIZE),
}
for name, tris in shapes.items():
    check(tris and len(tris) % 3 == 0, f"{name}: a non-empty TRIS list ({len(tris)} vertices)")
    x0, y0, x1, y1 = g.bounds(tris)
    slack = SIZE * 0.2
    check(x0 >= CX - SIZE / 2 - slack and x1 <= CX + SIZE / 2 + slack
          and y0 >= CY - SIZE / 2 - slack and y1 <= CY + SIZE / 2 + slack,
          f"{name}: stays inside its {SIZE:g} px box ({x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f})")
    check(abs((x0 + x1) / 2 - CX) < SIZE * 0.35 and abs((y0 + y1) / 2 - CY) < SIZE * 0.35,
          f"{name}: is centred where it was asked")

check(shapes["eye open"] != shapes["eye closed"], "an OFF layer's eye differs in shape from ON")
check(g.bounds(shapes["eye closed"])[3] - g.bounds(shapes["eye closed"])[1]
      > g.bounds(shapes["eye open"])[3] - g.bounds(shapes["eye open"])[1],
      "the closed eye is struck through (taller than the open one)")
check(shapes["lock locked"] != shapes["lock open"], "an unlocked padlock differs from a locked one")
check(g.bounds(shapes["lock open"])[2] > g.bounds(shapes["lock locked"])[2],
      "the unlocked shackle swings out to the right")
check(g.segment(0, 0, 0, 0, 2) == [], "a zero-length stroke draws nothing")
check(g.bounds([]) is None, "an empty glyph has no bounds")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILED:", *failures, sep="\n  ")
    sys.exit(1)
