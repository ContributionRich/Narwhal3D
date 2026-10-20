"""the reference CAD application's SUB-OBJECT selection. **No `bpy`, no rhino3dm** -- pure, so testable.

What the help says, verbatim
----------------------------
``user_interface/keyboard modifiers.htm``:

    "To select sub-object parts with a mouse -- For many commands, you can select
    sub-parts of objects or specified object types to allow you to restrict
    selection in a tight area. **Ctrl (CMD) + Shift + Left mouse click, window, or
    crossing.**"

    "To deselect sub-object parts with a mouse -- **Ctrl (CMD) + Shift + Left
    mouse click.** Ctrl (CMD) + window, or crossing."

``popup_moreinformation/subobjectselectionkeys.htm`` says the same in three words:
*"Select with Ctrl+Shift+Click"*.

The consequence, and it is not obvious
--------------------------------------
The **same** chord both selects and deselects, so Ctrl+Shift+click is a
**TOGGLE** -- see :func:`toggle`. That is *different* from ordinary object
selection, where `select.py` implements the documented Shift=add / Ctrl=remove
split. Reusing `select.modifier_action` here would be wrong: Ctrl+Shift would
resolve to REMOVE and a first click could never select anything.

What we can actually select, and what we honestly cannot
-------------------------------------------------------
the reference CAD application sub-object selects polysurface faces, surface and mesh edges, mesh faces
and vertices, and curve segments. Of those, only :data:`SEGMENT` is implemented:
a polyline's or polycurve's individual span, which is exactly what `Join`
produces and `Explode` takes apart. :data:`NOT_IMPLEMENTED` names the rest so a
caller can report them instead of silently selecting the whole object (house
rule 4).

Why the hit test is 2-D
-----------------------
A segment has no clickable 3-D volume; the user aims at the pixels that were
drawn. So :func:`nearest` works in screen space on already-projected polylines,
the same choice `pick_control_point` and the Gumball make. Callers project.
"""
from __future__ import annotations

import math

#: Sub-object kinds, spelled as the reference CAD application's Selection Filter spells the object types
#: they belong to (`commands/selectionfilter.htm`).
SEGMENT = "Segment"
FACE = "Face"
EDGE = "Edge"
VERTEX = "Vertex"

#: Kinds this module and its callers can pick: segments, plus faces, edges, and
#: vertices from the exact topology returned by `bridge.subobject_parts`.
IMPLEMENTED = (SEGMENT, FACE, EDGE, VERTEX)

#: Recognised but NOT pickable yet. Empty now; kept so callers can still ask.
NOT_IMPLEMENTED = ()


def _bary_depth(p, tri):
    """Depth at 2-D point `p` inside the projected triangle `tri` of (x, y, depth)
    corners, or None when `p` is outside it (or the triangle is degenerate)."""
    (x1, y1, d1), (x2, y2, d2), (x3, y3, d3) = tri
    den = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    if abs(den) < 1e-12:
        return None
    a = ((y2 - y3) * (p[0] - x3) + (x3 - x2) * (p[1] - y3)) / den
    b = ((y3 - y1) * (p[0] - x3) + (x1 - x3) * (p[1] - y3)) / den
    c = 1.0 - a - b
    eps = -1e-9
    if a < eps or b < eps or c < eps:
        return None
    return a * d1 + b * d2 + c * d3


def _closest_on_polyline(cursor, pts):
    """``(distance, depth)`` of the point on the projected polyline nearest `cursor`.
    `pts` are (x, y, depth); None entries (unprojectable) are skipped."""
    pts = [q for q in pts if q is not None]
    if not pts:
        return None
    if len(pts) == 1:
        return math.hypot(cursor[0] - pts[0][0], cursor[1] - pts[0][1]), pts[0][2]
    best = None
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        ll = dx * dx + dy * dy
        t = 0.0 if ll == 0.0 else max(0.0, min(1.0, ((cursor[0] - a[0]) * dx
                                                     + (cursor[1] - a[1]) * dy) / ll))
        d = math.hypot(cursor[0] - (a[0] + t * dx), cursor[1] - (a[1] + t * dy))
        if best is None or d < best[0]:
            best = (d, a[2] + t * (b[2] - a[2]))
    return best


def pick_part(faces, edges, vertices, cursor, tolerance, occlude=True,
              depth_slack=1e-3):
    """Which part of ONE solid the cursor picks: ``(kind, id)`` or None.

    Inputs are projected: `faces` ``[(id, [tri, ...])]`` with tri = three (x, y, depth)
    corners, `edges` ``[(id, [(x, y, depth), ...])]``, `vertices` ``[(id, (x, y,
    depth))]``; depth grows AWAY from the viewer.

    the reference CAD application documents the selection chord but not overlap priority. A vertex within
    `tolerance` px beats an edge, and an edge beats a face: the smaller target wins, or
    a cube's edges could never be picked off its faces. A face is the FRONT-most one
    under the cursor. With `occlude` (Shaded / Rendered) a vertex or edge hidden behind
    that front face is not pickable; `depth_slack` (relative) lets the face's own
    boundary edges through. Wireframe passes ``occlude=False``: everything shows there.
    """
    front = None
    front_id = None
    for fid, tris in faces or []:
        for tri in tris:
            d = _bary_depth(cursor, tri)
            if d is not None and (front is None or d < front):
                front, front_id = d, fid

    def visible(depth):
        if not occlude or front is None:
            return True
        return depth <= front + depth_slack * max(1.0, abs(front))

    best = None
    for vid, v in vertices or []:
        if v is None:
            continue
        dist = math.hypot(cursor[0] - v[0], cursor[1] - v[1])
        if dist <= tolerance and visible(v[2]) and (best is None or dist < best[0]):
            best = (dist, vid)
    if best is not None:
        return (VERTEX, best[1])
    for eid, pts in edges or []:
        got = _closest_on_polyline(cursor, pts)
        if got is None:
            continue
        dist, depth = got
        if dist <= tolerance and visible(depth) and (best is None or dist < best[0]):
            best = (dist, eid)
    if best is not None:
        return (EDGE, best[1])
    if front_id is not None:
        return (FACE, front_id)
    return None


def is_subobject_chord(shift=False, ctrl=False):
    """True for the one chord the reference CAD application uses: **Ctrl+Shift**.

    Deliberately strict. Ctrl alone is REMOVE and Shift alone is ADD for whole
    objects, and both meanings are documented, so neither may be borrowed here.
    """
    return bool(shift) and bool(ctrl)


def toggle(current, hit):
    """Ctrl+Shift+click on `hit`, applied to the `current` selection.

    Returns the new selection, sorted. `hit` of None -- a Ctrl+Shift+click that
    landed on nothing -- leaves the selection ALONE rather than clearing it: the
    help gives Ctrl+Shift+click exactly two jobs, select and deselect a part, and
    says nothing about clearing. Esc is what clears (the same page lists
    "Deselects all selected objects" under the Esc key), so inventing a third
    behaviour here would be guessing.
    """
    out = set(current)
    if hit is None:
        return sorted(out)
    if hit in out:
        out.discard(hit)
    else:
        out.add(hit)
    return sorted(out)


def _distance_to_segment(px, py, ax, ay, bx, by):
    """Shortest distance from (px, py) to the finite segment a-b."""
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_polyline(cursor, points):
    """Shortest distance from `cursor` to the 2-D polyline through `points`.

    `points` is the projected sample of ONE sub-object. A sample of a single
    point still answers (the distance to it), and an empty one answers None, so a
    caller never has to special-case a degenerate projection.
    """
    pts = [p for p in points if p is not None]
    if not pts:
        return None
    if len(pts) == 1:
        return math.hypot(cursor[0] - pts[0][0], cursor[1] - pts[0][1])
    return min(_distance_to_segment(cursor[0], cursor[1],
                                    a[0], a[1], b[0], b[1])
               for a, b in zip(pts, pts[1:]))


def nearest(candidates, cursor, tolerance):
    """The sub-object whose drawn pixels pass closest to `cursor`, or None.

    `candidates` is ``[(key, projected_points)]``; `key` is whatever identifies the
    sub-object to the caller. Nothing within `tolerance` pixels returns None --
    the reference CAD application does not select a thing you did not aim at.

    Ties go to the EARLIER candidate, so the order the caller supplies is the
    tie-break. That matters for a closed curve, whose first and last segments meet.
    """
    best, best_d = None, float(tolerance)
    for key, points in candidates:
        d = distance_to_polyline(cursor, points)
        if d is None:
            continue
        if d < best_d:
            best, best_d = key, d
    return best


def describe(selection):
    """A CAD-style one-line summary of a sub-object selection.

    Grouped by object, because "2 segments of Polyline1" is what a user reads,
    not four opaque tuples.
    """
    if not selection:
        return "no sub-objects selected"
    by_object = {}
    for name, kind, index in selection:
        by_object.setdefault((name, kind), []).append(index)
    parts = []
    for (name, kind), idxs in sorted(by_object.items()):
        noun = kind.lower() + ("s" if len(idxs) != 1 else "")
        parts.append(f"{len(idxs)} {noun} of {name}")
    return ", ".join(parts)
