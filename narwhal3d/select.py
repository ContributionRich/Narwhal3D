"""Rhino's object selection rules. **No `bpy`, no rhino3dm.**

Pure, so every rule is unit-tested without launching Blender
(spike/test_select.py) -- the same split as `cplane.py`, `osnap.py` and
`gumball.py`.

Why this exists
---------------
The owner could not select anything, which blocked testing every Gumball
feature. Investigating found something worse than a bug: leaning on Blender's
own `view3d.select` gives **the wrong semantics for a Rhino clone**. Measured
from `commands/selection_commands.htm`, "Select object basics":

    "Click an object to select it. Click away from the objects to deselect."
    "Drag a selection rectangle from left to right to use a window selection."
    "When you make a window selection, objects completely enclosed in the
     selection rectangle are selected."
    "Drag a selection rectangle from right to left to do a crossing selection."
    "When you make a crossing selection, all objects inside or partially inside
     the selection rectangle are selected."
    "To add objects to a selection -- Press and hold Shift while clicking single
     objects or using a window or crossing."
    "To remove objects from a selection -- Press and hold Ctrl(CMD) while
     clicking single objects or using a window or crossing."

Blender disagrees on both modifiers: Shift+click *toggles* rather than adds, and
Ctrl+click is not a remove at all. So the modifier handling cannot be delegated;
it has to be ours. What Blender's picker IS good for is the occlusion-aware hit
test ("which object is under this pixel"), which `ui` still borrows -- this module
decides what to DO with that answer.

What is deliberately NOT here
-----------------------------
The Selection Menu ("If several objects appear to be stacked in the area of the
cursor and Rhino is not able to tell which one you want to select, the Selection
Menu appears"), sub-object selection, and the Selection Filters panel. Each is a
real documented feature and each needs UI we do not have; none is faked here.
"""
from __future__ import annotations

# --- what a drag direction means -------------------------------------------

#: Left-to-right drag: only objects COMPLETELY enclosed are selected.
WINDOW = "window"
#: Right-to-left drag: objects inside OR PARTIALLY inside are selected.
CROSSING = "crossing"

# --- what the modifiers mean (Rhino's, not Blender's) ----------------------

#: No modifier: the new selection REPLACES whatever was selected.
REPLACE = "replace"
#: Shift: add to the selection.
ADD = "add"
#: Ctrl: remove from the selection.
REMOVE = "remove"


def modifier_action(shift=False, ctrl=False):
    """Which selection action the held modifiers ask for.

    MEASURED (commands/selection_commands.htm): Shift adds, Ctrl removes. Both
    together is not documented; Ctrl (remove) wins, because removing is the more
    destructive reading and guessing the other way could silently grow a
    selection the user was trying to shrink.
    """
    if ctrl:
        return REMOVE
    if shift:
        return ADD
    return REPLACE


def drag_mode(start_x, end_x):
    """WINDOW for a left-to-right drag, CROSSING for right-to-left.

    Rhino keys this on the drag's horizontal DIRECTION, not on which edge is
    which, so a perfectly vertical drag (dx == 0) has no direction to read and is
    treated as a window -- the stricter of the two, so nothing is selected that
    the user did not fully enclose.
    """
    return CROSSING if (end_x - start_x) < 0 else WINDOW


def normalise_rect(x0, y0, x1, y1):
    """A drag's two corners as ``(min_x, min_y, max_x, max_y)``."""
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def is_click(x0, y0, x1, y1, tolerance=3.0):
    """True when press and release are close enough to mean a CLICK, not a drag.

    Without a tolerance every click registers as a zero-size drag rectangle and
    selects nothing. 3 px is OURS -- Rhino documents the two interactions but not
    where it draws the line between them.
    """
    return abs(x1 - x0) <= tolerance and abs(y1 - y0) <= tolerance


# --- applying a hit to a selection ----------------------------------------

def apply(current, hits, action=REPLACE):
    """The new selection, given the current one and what was just hit.

    `current` and `hits` are iterables of object names; the result is a list in a
    stable order (current order first, then new arrivals) so the caller can rely
    on it and tests can compare exactly.

    The empty-`hits` case is the documented "Click away from the objects to
    deselect": with no modifier it clears the selection. With Shift or Ctrl it
    does NOT -- clicking empty space while adding or removing asks for nothing to
    change, and wiping the selection there would make a mis-aimed Shift+click
    destroy the set the user was building.
    """
    current = list(dict.fromkeys(current or []))
    hits = list(dict.fromkeys(hits or []))

    if action == ADD:
        out = list(current)
        for name in hits:
            if name not in out:
                out.append(name)
        return out
    if action == REMOVE:
        drop = set(hits)
        return [n for n in current if n not in drop]
    # REPLACE -- including the "click away deselects everything" case
    return hits


def rect_contains(rect, bbox):
    """True if `bbox` lies COMPLETELY inside `rect` (window selection).

    Both are ``(min_x, min_y, max_x, max_y)`` in screen pixels.
    """
    rx0, ry0, rx1, ry1 = rect
    bx0, by0, bx1, by1 = bbox
    return bx0 >= rx0 and by0 >= ry0 and bx1 <= rx1 and by1 <= ry1


def rect_overlaps(rect, bbox):
    """True if `bbox` is inside OR partially inside `rect` (crossing selection).

    Touching counts as overlapping: a crossing selection is the generous one, and
    a user who drags exactly to an edge means to catch it.
    """
    rx0, ry0, rx1, ry1 = rect
    bx0, by0, bx1, by1 = bbox
    return not (bx1 < rx0 or bx0 > rx1 or by1 < ry0 or by0 > ry1)


def in_rect(candidates, rect, mode=WINDOW):
    """Names of `candidates` caught by `rect` under `mode`.

    `candidates` is an iterable of ``(name, bbox)``. A candidate whose bbox is
    None -- it did not project to the screen at all -- is skipped rather than
    guessed at.
    """
    test = rect_contains if mode == WINDOW else rect_overlaps
    out = []
    for name, bbox in candidates or []:
        if bbox is None:
            continue
        if test(rect, bbox):
            out.append(name)
    return out
